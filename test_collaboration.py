"""Deterministic collaboration checks; model replies are explicit fixtures."""
import json
import os
import tempfile
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from model_providers import ProviderError
from runtime_config import ConnectionClient, RuntimeConfig
from task_orchestrator import TaskRun, step_input, task_preflight, start_task


def self_check():
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"TOKENFLOW_DB_PATH": str(Path(directory) / "documents.db")}):
        store = RuntimeConfig(Path(directory) / "connections.db")
        for identity, local in (("edge", True), ("cloud", False)):
            store.save_profile({"id": identity, "name": identity, "local": local, "base_url": "http://127.0.0.1:9/v1",
                                "protocol": "openai", "models": ["fixture-model"]})
        store.save_settings({"roles": {"local": "edge", "planner": "cloud", "executor": "cloud", "reviewer": "cloud"}})
        data = {"task": "分析资料并形成结论", "content": "原文独有标记 SOURCE_ONLY，事实42"}
        preview = task_preflight(data, store)
        assert preview["ready"] and preview["mode"] == "hybrid"
        assert [r["location"] for r in preview["routes"]] == ["local", "local", "cloud", "cloud", "cloud"]
        assert store.runs()["total"] == 0
        plan = {"steps": [
            {"id": "extract", "kind": "local_model", "instruction": "提取事实", "depends_on": [], "input_scope": "source", "expected_output": "关键事实"},
            {"id": "answer", "kind": "model", "instruction": "汇总事实得出结论", "depends_on": ["extract"], "input_scope": "dependencies", "expected_output": "完整结论"}],
            "acceptance_criteria": ["保留事实42并形成结论"]}
        review = json.dumps({"passed": True, "issues": [], "reason": "已核对原文42", "evidence": []}, ensure_ascii=False)
        observed = []

        def replies(values):
            pending = iter(values)
            def chat(client, model, messages, **kwargs):
                observed.append((client.profile["id"], json.dumps(messages, ensure_ascii=False)))
                value = next(pending)
                if isinstance(value, Exception):
                    raise value
                return {"choices": [{"message": {"content": value}}], "usage": {"prompt_tokens": 40, "completion_tokens": 20}}
            return chat

        with patch.object(ConnectionClient, "chat", replies([json.dumps(plan), "已提取事实42 EDGE_OUTPUT", "完整结论42", review])):
            run = TaskRun(store, **data).execute()
        assert run["status"] == "completed" and [c["location"] for c in run["calls"]] == ["cloud", "local", "cloud", "cloud"]
        assert "EDGE_OUTPUT" in observed[2][1] and "SOURCE_ONLY" not in observed[2][1]
        assert data['task'] in observed[2][1] and '用户原任务' in observed[2][1]
        assert "SOURCE_ONLY" in observed[3][1]  # Original source still reaches acceptance.
        assert all(s["status"] == "completed" for s in run["step_results"])
        assert store.runs(run["id"])["step_results"][0]["output"].endswith("EDGE_OUTPUT")
        observed.clear()
        with patch.object(ConnectionClient, "chat", replies([json.dumps(plan), ProviderError("edge unavailable"), "升级提取42", "结论42", review])):
            upgraded = TaskRun(store, **data).execute()
        assert upgraded["status"] == "completed" and len(upgraded["upgrades"]) == 1
        assert [c["target"] for c in upgraded["calls"]] == ["cloud", "edge", "cloud", "cloud", "cloud"]
        assert upgraded["metrics"]["model_calls"] == 5 and upgraded["calls"][1]["status"] == "failed"
        store.save_settings({"max_calls": 4})
        with patch.object(ConnectionClient, "chat", replies([json.dumps(plan), ProviderError("edge unavailable")])):
            limited = TaskRun(store, **data).execute()
        assert limited["status"] == "failed" and not limited["upgrades"] and len(limited["calls"]) == 2
        store.save_settings({"max_calls": 6})
        with patch.object(ConnectionClient, "chat", replies([json.dumps(plan), "已完成整理42", ProviderError("cloud unavailable")])):
            failed = TaskRun(store, **data).execute()
        assert failed["status"] == "failed" and failed["result"] == "" and failed["partial_result"] == "已完成整理42"
        assert [s["status"] for s in failed["step_results"]] == ["completed", "failed"]
        observed.clear()
        with patch.object(ConnectionClient, "chat", replies([json.dumps(plan), "本地事实42", "本地结论42", review])):
            local = TaskRun(store, **data, collaboration_mode="local_only").execute()
        assert local["status"] == "completed" and all(target == "edge" for target, _ in observed)
        assert not local["upgrades"]
        runner = TaskRun(store, **data)
        for steps in (
            [plan["steps"][0] | {"depends_on": ["answer"]}, plan["steps"][1]],
            [plan["steps"][0], plan["steps"][1] | {"depends_on": [], "input_scope": "source"}],
            [plan["steps"][0], plan["steps"][1] | {"id": "extract"}],
            [plan["steps"][0], plan["steps"][1] | {"kind": "statistics"}],
        ):
            try:
                runner.validate_plan({"steps": steps, "acceptance_criteria": ["完整结论"]})
            except ValueError:
                pass
            else:
                raise AssertionError("invalid dependencies must fail")
        legacy = runner.validate_plan({"steps": [{"kind": "model", "instruction": "分析"}, {"kind": "model", "instruction": "汇总"}], "acceptance_criteria": ["完整结论"]})
        assert legacy[1]["depends_on"] == ["step-1"] and legacy[1]["input_scope"] == "both"
        try:
            step_input(plan["steps"][1], "source", [])
        except RuntimeError:
            pass
        else:
            raise AssertionError("cannot hand off missing output")
        store.save_profile({**store.profile("cloud"), "models": ["fixture-model", "cloud-only"]})
        store.save_settings({"roles": {"local": "cloud"}, "role_models": {"local": "cloud-only"}})
        assert task_preflight(data, store)["routes"][1]["target"] == "edge"
        with patch.object(ConnectionClient, "chat", replies([json.dumps(plan), "本地事实42", "本地结论42", review])):
            rebound = TaskRun(store, **data, collaboration_mode="local_only").execute()
        assert rebound["status"] == "completed" and all(c["model"] == "fixture-model" for c in rebound["calls"])
        with store.connect() as db:
            db.execute("DELETE FROM connections WHERE id='edge'")
        local_preview = task_preflight(data | {"collaboration_mode": "local_only"}, store)
        assert not local_preview["ready"] and local_preview["issues"]
        with patch.object(ConnectionClient, "chat") as called:
            missing = TaskRun(store, **data, collaboration_mode="local_only").execute()
        assert missing["status"] == "failed" and missing["metrics"]["model_calls"] == 0
        called.assert_not_called()
        assert task_preflight({"task": "统计字符数", "content": "abc", "collaboration_mode": "local_only"}, store)["ready"]
        for invalid in ([], "unknown", True):
            try:
                task_preflight(data | {"collaboration_mode": invalid}, store)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid policy accepted")
        request = {"request_id": "collaboration-receipt-0001", "task": "统计字符数", "content": "abc", "collaboration_mode": "local_only"}
        submitted = start_task(request, store)
        for _ in range(100):
            record = store.runs(submitted["id"])
            if record["status"] not in {"running", "cancelling"}:
                break
            time.sleep(.01)
        assert record["status"] == "completed" and start_task(request, store)["reused"]
        try:
            start_task(request | {"collaboration_mode": "hybrid"}, store)
        except ValueError:
            pass
        else:
            raise AssertionError("policy must participate in receipt identity")
        import tokenflow as tf
        server = ThreadingHTTPServer(("127.0.0.1", 0), tf.TokenFlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ, {"TOKENFLOW_CONFIG_DB": str(store.path)}), patch.object(ConnectionClient, "chat") as called:
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                body = json.dumps({"task": "统计字符数", "content": "abc", "collaboration_mode": "local_only"})
                connection.request("POST", "/api/tasks/preview", body, {"Content-Type": "application/json"})
                response = connection.getresponse()
                assert response.status == 403
                response.read()
                connection.close()
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request("POST", "/api/tasks/preview", body, {"Content-Type": "application/json", "X-TokenFlow-Token": tf.SESSION_TOKEN})
                response = connection.getresponse()
                assert response.status == 200 and json.loads(response.read())["ready"]
                connection.close()
                for headers, expected in (({'Content-Type': 'application/json'}, 403),
                    ({'Content-Type': 'application/json', 'X-TokenFlow-Token': tf.SESSION_TOKEN}, 400)):
                    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    connection.request('POST', '/api/tasks/retry', '{"id":"invalid"}', headers)
                    response = connection.getresponse()
                    assert response.status == expected
                    response.read(); connection.close()
                called.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print("Collaboration self-check: PASS")


if __name__ == "__main__":
    self_check()
