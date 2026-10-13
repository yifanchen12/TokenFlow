"""Offline checks for budgets, request identity, file reuse and storage policy."""
import json
import os
import sqlite3
import tempfile
import time
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from model_providers import ProviderError
from runtime_config import RuntimeConfig, ConnectionClient
from task_orchestrator import TaskRun, start_task
from document_parser import parse_document_bytes
from tokenflow_store import DocumentStore


def response(text):
    return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


def self_check():
    import tokenflow as tf
    from scripts.prepare_eval_cases import prepare
    from eval_workflow import evaluate_pipeline
    for protocol, truncated in (
        ("responses", {"status": "incomplete", "usage": {"input_tokens": 100, "output_tokens": 20}}),
        ("anthropic", {"stop_reason": "max_tokens", "usage": {"input_tokens": 100, "output_tokens": 20}}),
    ):
        client = ConnectionClient({"protocol": protocol, "base_url": "http://127.0.0.1:1/v1", "api_key": ""})
        with patch.object(client, "_request", return_value=truncated):
            try:
                client.chat("fixture", [{"role": "user", "content": "fixture"}])
            except ProviderError as error:
                assert error.usage == {"prompt_tokens": 100, "completion_tokens": 20}
            else:
                raise AssertionError("truncation must still fail")

    with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
        "TOKENFLOW_CONFIG_DB": str(Path(temporary) / "connections.db"),
        "TOKENFLOW_DB_PATH": str(Path(temporary) / "documents.db"),
    }):
        store = RuntimeConfig()
        store.save_profile({"id": "fixture", "name": "Fixture", "protocol": "openai", "base_url": "http://127.0.0.1:1/v1", "models": ["fixture"], "local": True})
        store.save_settings({"roles": {role: "fixture" for role in ("local", "planner", "executor", "reviewer")}})
        for invalid in ({"max_task_tokens": True}, {"context_window_tokens": -1}, {"output_limits": {"reviewer": 1}}, {"cache_retention_days": -1}):
            try:
                store.save_settings(invalid)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid limits must fail")
        store.save_settings({"max_task_tokens": 256})
        with patch("task_orchestrator.ConnectionClient.chat") as called:
            blocked = TaskRun(store, "ANSWER是多少？", "ANSWER = 42").execute()
            assert blocked["status"] == "failed" and blocked["metrics"]["model_calls"] == 0
            called.assert_not_called()
        store.save_settings({"max_task_tokens": 64000, "context_window_tokens": 256})
        with patch("task_orchestrator.ConnectionClient.chat") as called:
            assert TaskRun(store, "ANSWER是多少？", "ANSWER = 42").execute()["metrics"]["model_calls"] == 0
            called.assert_not_called()
        store.save_settings({"context_window_tokens": 32768, "output_limits": {"executor": 128, "reviewer": 256}})
        review = json.dumps({"passed": True, "issues": [], "reason": "verified 42"})
        with patch("task_orchestrator.ConnectionClient.chat", side_effect=[response("42"), response(review)]) as called:
            completed = TaskRun(store, "ANSWER是多少？", "ANSWER = 42").execute()
        assert completed["status"] == "completed"
        assert [call.kwargs["max_tokens"] for call in called.call_args_list] == [128, 256]
        with patch("task_orchestrator.ConnectionClient.chat", side_effect=ProviderError("truncated", usage={"prompt_tokens": 100, "completion_tokens": 20})):
            failed = TaskRun(store, "ANSWER是多少？", "ANSWER = 42").execute()
        assert failed["status"] == "failed" and failed["metrics"]["reported_total_tokens"] == 120

        payload = {"request_id": "request-fixture-0001", "task": "统计 CSV 的行数", "content": "name,value\na,2\n"}
        first = start_task(payload, store)
        again = start_task(payload, RuntimeConfig())
        assert again["id"] == first["id"] and again["reused"]
        for _ in range(100):
            if store.runs(first["id"])["status"] == "completed":
                break
            time.sleep(.03)
        assert start_task(payload, store)["id"] == first["id"]
        try:
            start_task(payload | {"content": "changed"}, store)
        except ValueError:
            pass
        else:
            raise AssertionError("request ID must not accept a different payload")
        store.manage_history(first["id"])
        try:
            start_task(payload, store)
        except ValueError as error:
            assert "已清理" in str(error)
        else:
            raise AssertionError("deleted task must not replay")

        with patch("local_ocr.recognize", side_effect=lambda data: {"text": "OCR 42", "method": "local_ocr", "mean_confidence": .9}) as recognized:
            one = parse_document_bytes("one.png", b"fixture-image", use_cache=True)
            two = parse_document_bytes("renamed.png", b"fixture-image", use_cache=True)
            assert recognized.call_count == 1 and not one["cache_reused"] and two["cache_reused"]
            assert two["name"] == "renamed.png" and two["mean_confidence"] == .9
            with patch("document_parser.PARSER_CACHE_VERSION", 99):
                assert not parse_document_bytes("one.png", b"fixture-image", use_cache=True)["cache_reused"]
            assert not parse_document_bytes("one.png", b"changed-image", use_cache=True)["cache_reused"]
        with patch("tokenflow_store.DocumentStore.parsed", side_effect=sqlite3.OperationalError("unavailable")):
            assert parse_document_bytes("a.txt", b"hello", use_cache=True)["text"] == "hello"
        documents = DocumentStore()
        doc = documents.index("policy", "original source")
        assert documents.document(doc["document_id"]) == "original source"
        documents.manage(kind="document", identity=str(doc["document_id"]))
        try:
            documents.document(doc["document_id"])
        except ValueError:
            pass
        else:
            raise AssertionError("deleted source must be absent")
        documents.parsed("a" * 64, {"name": "large-a", "text": "x" * 600000})
        documents.parsed("b" * 64, {"name": "large-b", "text": "y" * 600000})
        assert documents.manage(max_mb=1)["content_bytes"] <= 1024 * 1024
        store.save_settings({"history_max_runs": 1})
        store.save_run({"id": "a" * 32, "status": "running", "process_id": os.getpid()})
        store.save_run({"id": "b" * 32, "status": "completed"})
        store.save_run({"id": "c" * 32, "status": "completed"})
        assert store.manage_history(prune=True)["runs"] == 2
        try:
            store.manage_history("a" * 32)
        except ValueError:
            pass
        else:
            raise AssertionError("active run cannot be deleted")
        # Verify actual protected management/recovery routes against isolated storage.
        server = ThreadingHTTPServer(("127.0.0.1", 0), tf.TokenFlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Content-Type": "application/json", "X-TokenFlow-Token": tf.SESSION_TOKEN}
        def request(method, path, body=None, *, protected=True):
            connection.request(method, path, json.dumps(body) if body is not None else None, headers if protected else {})
            result = connection.getresponse()
            return result.status, json.loads(result.read())
        try:
            assert request("GET", "/api/config/storage", protected=False)[0] == 403
            status, storage = request("GET", "/api/config/storage")
            assert status == 200 and storage["cache"]["content_bytes"] >= 0
            assert request("POST", "/api/tasks/delete", {"id": "a" * 32})[0] == 400
            assert request("POST", "/api/tasks/delete", {"id": ""})[0] == 400
            assert request("GET", "/api/tasks/request/" + payload["request_id"])[0] == 400
            kept = documents.index("draft source", "ANSWER = 42")
            store.save_run({"id": "d" * 32, "task": "ANSWER是多少？", "status": "completed", "result": "42",
                            "document_cache": kept})
            status, draft = request("POST", "/api/tasks/eval-draft", {"id": "d" * 32})
            assert status == 200 and draft["content"] == "ANSWER = 42"
            assert draft["candidate_answer"] == "42" and not draft["expected_answer"] and draft["label_status"] == "draft"
            assert request("POST", "/api/config/storage/clear", {})[0] == 200
            assert documents.status()["documents"] == 0 and documents.status()["parsed_files"] == 0
            assert request("POST", "/api/tasks/delete", {"id": "d" * 32})[0] == 200
        finally:
            connection.close()
            server.shutdown(); server.server_close(); thread.join()
        source = Path(temporary) / "source.txt"
        source.write_text("ANSWER = 42", encoding="utf-8")
        manifest = Path(temporary) / "manifest.json"
        output = Path(temporary) / "prepared.json"
        case = {"source_kind": "real_task", "label_status": "draft", "category": "document", "task": "ANSWER是多少？",
                "source_file": "source.txt", "expected_answer": "42", "must_keep": ["42"], "candidate_answer": "unverified"}
        manifest.write_text(json.dumps([case]), encoding="utf-8")
        try:
            prepare(manifest, output)
        except ValueError:
            pass
        else:
            raise AssertionError("unverified draft cannot become an evaluation dataset")
        case["label_status"] = "verified"
        manifest.write_text(json.dumps([case]), encoding="utf-8")
        assert prepare(manifest, output) == 1
        prepared = json.loads(output.read_text(encoding="utf-8"))
        assert prepared[0]["content"] == "ANSWER = 42" and "candidate_answer" not in prepared[0]
        with patch("runtime_config.ConnectionClient.chat", side_effect=[response("42"), response("42"), response(review)]):
            report = evaluate_pipeline(prepared, store.profile("fixture", True), "fixture", min_cases=1, min_saving=0)
        group = report["summary"]["by_category"]["document"]
        assert group["processed_accuracy"] == 1 and group["processed_total_tokens"] == 240
        assert report["summary"]["reported_total_token_saving_ratio"] == -1
    print("Budget/cache/identity checks: PASS")


if __name__ == "__main__":
    self_check()
