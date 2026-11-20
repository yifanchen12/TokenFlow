"""Run one synthetic file task through a Windows EXE, using the configured BUPT API."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, Request


def check_parallel(api, folder, model, profile_id, encoding):
    tasks = []
    expected = ["42", "19"]
    for i, quantities in enumerate(((17, 28, 3), (14, 9, 4), (21, 8, 6))):
        source = folder / f"parallel-{i}.txt"
        a, b, c = quantities
        source.write_text(f"上午领取{a}份，下午领取{b}份，之后退回{c}份。", encoding=encoding)
        tasks.append({"task": "先合计两次领取的份数，再减去退回的份数。只回答最终数字。", "path": str(source),
                      "request_id": f"parallel-inventory-{i}"})
    assert all(api("/api/tasks/preview", task)["ready"] for task in tasks)
    with ThreadPoolExecutor(3) as pool:
        ids = [r["id"] for r in pool.map(lambda task: api("/api/tasks/start", task), tasks)]
    assert len(set(ids)) == 3
    assert set(ids) <= {r["id"] for r in api("/api/tasks?status=active")["runs"]}
    api("/api/tasks/cancel", {"id": ids[2]})
    deadline = time.monotonic() + 600
    while True:
        runs = [api("/api/tasks/" + identity) for identity in ids]
        if all(r["status"] not in {"queued", "running", "cancelling"} for r in runs):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("Concurrent EXE tasks timed out")
        time.sleep(.5)
    assert runs[2]["status"] == "cancelled"
    assert len({r["document_cache"]["document_id"] for r in runs[:2]}) == 2
    for i, run in enumerate(runs):
        assert run["metrics"]["local_calls"] == 0 and run["collaboration_mode"] == "remote_only"
        assert all(c["target"] == profile_id and c["model"] == model for c in run["calls"])
        receipt = api("/api/tasks/start", tasks[i])
        assert receipt["reused"] and receipt["id"] == ids[i]
        if i < 2:
            assert run["status"] == "completed" and run["result"].strip() == expected[i] and run["acceptance"]["status"] == "passed"
            assert {"planner", "executor", "reviewer"} <= {c["purpose"] for c in run["calls"]}
            assert run["parsing"]["encoding"] == ("utf-8-sig" if encoding == "utf-8" else encoding)
    overlap = max(min(a["started_at"]+a["duration_ms"]/1000, b["started_at"]+b["duration_ms"]/1000)
                  - max(a["started_at"], b["started_at"]) for a in runs[0]["calls"] for b in runs[1]["calls"])
    assert overlap > 0 and not api("/api/tasks?status=active")["runs"]
    assert {r["id"] for r in api("/api/tasks?status=completed")["runs"]} == set(ids[:2])
    return {"answers": expected, "review": "passed", "cancelled": 1, "receipts_reused": 3,
            "max_call_overlap_seconds": round(overlap, 3), "calls": [len(r["calls"]) for r in runs], "local_calls": 0}


def check(executable, encoding="utf-8", concurrent=False):
    remote = urlsplit(os.environ.get("BUPT_API_BASE_URL", ""))
    if remote.scheme != "https" or not (remote.hostname or "").endswith(".bupt.edu.cn") or not os.environ.get("BUPT_API_KEY"):
        raise RuntimeError("A configured BUPT HTTPS endpoint and key are required")
    with tempfile.TemporaryDirectory(prefix="tokenflow-bupt-") as temp:
        folder = Path(temp)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        # This test registers one cloud connection. Keep native model executables
        # out of the EXE's path, including during planner capability discovery.
        env = dict(os.environ, PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                   TOKENFLOW_HOST="127.0.0.1", TOKENFLOW_PORT=str(port), TOKENFLOW_OPEN_BROWSER="0",
                   TOKENFLOW_CONFIG_DB=str(folder / "connections.db"), TOKENFLOW_DB_PATH=str(folder / "documents.db"))
        source = folder / "inventory.txt"
        source.write_text("上午领取17份，下午领取28份，之后退回3份。", encoding=encoding)
        opener = build_opener(ProxyHandler({}))
        headers = {"Content-Type": "application/json"}

        def api(path, data=None):
            body = json.dumps(data).encode() if data is not None else None
            request = Request(f"http://127.0.0.1:{port}" + path, data=body, headers=headers)
            with opener.open(request, timeout=20) as response:
                return json.load(response)

        process = subprocess.Popen([str(executable)], cwd=folder, env=env,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 90
            while True:
                try:
                    if api("/health").get("status") == "ok":
                        break
                except URLError:
                    pass
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("EXE failed to start")
                time.sleep(.3)
            headers["X-TokenFlow-Token"] = api("/api/session")["token"]
            profile = api("/api/config/import-native", {"harness": "dsh"})
            profile = api("/api/config/discover", {"id": profile["id"]})
            model = "deepseek-v4-flash"
            if profile["local"] or model not in profile["models"]:
                raise RuntimeError("BUPT model discovery failed")
            roles = dict.fromkeys(("planner", "executor", "reviewer"), profile["id"])
            api("/api/config/settings", {"collaboration_mode": "remote_only", "roles": roles,
                                         "role_models": dict.fromkeys(roles, model), "max_calls": 6})
            if concurrent:
                report = check_parallel(api, folder, model, profile["id"], encoding)
                with executable.open("rb") as binary:
                    report["sha256"] = hashlib.file_digest(binary, "sha256").hexdigest()
                print(json.dumps(report | {"model": model, "encoding": encoding}))
                return
            task = {"path": str(source), "task": "先合计两次领取的份数，再减去退回的份数。只回答最终数字。",
                    "request_id": "packaged-inventory-check"}
            if not api("/api/tasks/preview", task)["ready"]:
                raise RuntimeError("EXE task preflight failed")
            identity = api("/api/tasks/start", task)["id"]
            deadline = time.monotonic() + 600
            while True:
                run = api("/api/tasks/" + identity)
                if run["status"] not in ("running", "cancelling"):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("EXE task timed out")
                time.sleep(1)
            calls = run["calls"]
            if (run["collaboration_mode"] != "remote_only" or run["metrics"]["local_calls"] != 0
                    or run["status"] != "completed" or run["result"].strip() != "42"
                    or run["acceptance"]["status"] != "passed" or not run.get("document_cache")
                    or not {"planner", "executor", "reviewer"} <= {c["purpose"] for c in calls}
                    or not all(c["target"] == profile["id"] and c["model"] == model and c["local"] is False
                               and c["status"] == "completed" for c in calls)):
                raise RuntimeError(f"EXE task verification failed (status={run['status']}, calls={len(calls)})")
            if run.get("parsing", {}).get("encoding") != ("utf-8-sig" if encoding == "utf-8" else encoding):
                raise RuntimeError("EXE did not record the expected source encoding")
            history = api("/api/tasks?status=completed")
            receipt = api("/api/tasks/start", task)
            if [item["id"] for item in history["runs"]] != [identity] or receipt["id"] != identity or not receipt["reused"]:
                raise RuntimeError("EXE history or duplicate request check failed")
            digest = hashlib.sha256()
            with executable.open("rb") as binary:
                for chunk in iter(lambda: binary.read(1024 * 1024), b""):
                    digest.update(chunk)
            print(json.dumps({"sha256": digest.hexdigest(),
                              "model": model, "source_encoding": encoding, "answer": 42, "review": "passed", "history": "passed",
                              "receipt_reused": True, "roles": [c["purpose"] for c in calls],
                              "local_calls": run["metrics"]["local_calls"], "tokens": run["metrics"]["reported_total_tokens"]}))
        finally:
            if process.poll() is None:
                try:
                    api("/api/shutdown", {})
                    process.wait(timeout=15)
                except (OSError, subprocess.TimeoutExpired):
                    if process.poll() is None:
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                        process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--encoding", choices=("utf-8", "utf-16", "utf-32"), default="utf-8")
    parser.add_argument("--concurrent", action="store_true", help="Submit three tasks together and cancel one")
    args = parser.parse_args()
    check(args.executable.resolve(strict=True), args.encoding, args.concurrent)
