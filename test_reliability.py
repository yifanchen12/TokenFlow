"""Focused offline checks for exact statistics, mixed PDFs, budgets and paged history."""
import importlib.util
import io
import json
import os
import tempfile
import threading
from pathlib import Path
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from document_parser import parse_document_bytes
from runtime_config import ConnectionClient, RuntimeConfig
from task_orchestrator import TaskRun, local_statistics, stage_tokens
from tokenflow_store import DocumentStore


def self_check():
    import tokenflow as tf
    from test_optimizations import archive, response
    large = "id,amount\nA,10000000000000000000000000001\nB,-10000000000000000000000000000\n"
    column = local_statistics(large)["table"]["columns"]["amount"]
    assert column["sum"] == "1" and column["median"] == "0.5" and column["mean"] == "0.5" and column["mean_exact"]
    column = local_statistics("id,value\na,0.1\nb,0.2\n")["table"]["columns"]["value"]
    assert column["sum"] == "0.3" and column["median"] == "0.15"
    assert not local_statistics("id,value\na,1\nb,0\nc,0\n")["table"]["columns"]["value"]["mean_exact"]
    for source in ("id,value\na,1e99999\n", "id,value\na,1e4096\nb,1e-4096\n"):
        try:
            local_statistics(source)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe precision range must be rejected")

    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
        "TOKENFLOW_CONFIG_DB": str(Path(directory) / "connections.db"), "TOKENFLOW_DB_PATH": str(Path(directory) / "documents.db"),
        "TOKENFLOW_TOKENIZER_BACKEND": "heuristic", "TOKENFLOW_TOKENIZER_PATH": "",
    }):
        store = RuntimeConfig()
        documents = DocumentStore()
        sheet = '<worksheet xmlns="s"><row><c t="inlineStr"><is><t>id</t></is></c><c t="inlineStr"><is><t>value</t></is></c></row><row><c t="inlineStr"><is><t>A</t></is></c><c><v>10</v></c></row><row><c t="inlineStr"><is><t>B</t></is></c><c><f>5*4</f></c></row></worksheet>'
        path = Path(directory) / "formula.xlsx"
        path.write_bytes(archive({"xl/worksheets/sheet1.xml": sheet}))
        with patch("task_orchestrator.ConnectionClient.chat") as called:
            run = TaskRun(store, "统计表格总和", "", document_store=documents, source_path=str(path)).execute()
            assert run["status"] == "needs_review" and run["acceptance"]["status"] == "failed"
            assert run["source_warnings"] and run["parsing"]["missing_formula_count"] == 1
            assert json.loads(run["result"])["table"]["columns"]["value"]["sum"] == "10"
            counted = TaskRun(store, "统计表格行数", "", document_store=documents, source_path=str(path)).execute()
            assert counted["status"] == "completed"
            called.assert_not_called()
        cached = parse_document_bytes("cached.xlsx", archive({"xl/worksheets/sheet1.xml": sheet.replace('<f>5*4</f>', '<f>5*4</f><v>20</v>')}))
        assert cached["missing_formula_count"] == 0 and local_statistics(cached["text"])["table"]["columns"]["value"]["sum"] == "30"

        store.save_profile({"id": "fixture", "name": "Fixture", "base_url": "http://127.0.0.1:1/v1", "protocol": "openai", "models": ["fixture"], "api_key": ""})
        store.save_settings({"roles": {r: "fixture" for r in ("planner", "executor", "reviewer")}, "context_window_tokens": 1024,
                             "output_limits": {r: 64 for r in ("local", "planner", "executor", "reviewer")}})
        source = "背景资料与问题无关。\n\n" * 1200 + "# ANSWER\nANSWER = 42"
        with patch("task_orchestrator.ConnectionClient.chat") as called:
            run = TaskRun(store, "ANSWER是什么？只回答数字。", source, document_store=documents, allow_harnesses=False).execute()
            assert run["status"] == "failed" and "验收" in run["error"] and run["metrics"]["model_calls"] == 0
            called.assert_not_called()
        store.save_settings({"context_window_tokens": 32768})
        review = json.dumps({"passed": True, "issues": [], "reason": "fixture accepted"})
        runner = TaskRun(store, "ANSWER是什么？", "ANSWER = 42", document_store=documents, allow_harnesses=False)
        with patch("task_orchestrator.ConnectionClient.chat", side_effect=[response("42"), response(review)]):
            run = runner.execute()
        assert run["status"] == "completed"
        assert run["metrics"]["single_raw_input_estimate"] == stage_tokens(runner.task, runner.content, "fixture")
        assert set(run["metrics"]["by_role"]) == {"executor", "reviewer"}

        history = RuntimeConfig(Path(directory) / "history.db")
        for i in range(25):
            history.save_run({"id": f"{i:032x}", "task": f"Alpha {i}" if i % 2 else f"中文 {i}",
                              "status": "completed" if i % 2 else "failed", "created_at": i})
        with history.connect() as db:
            db.execute("UPDATE runs SET updated=100")
        first, second = history.runs(), history.runs(offset=20)
        assert first["total"] == 25 and first["has_more"] and len(second["runs"]) == 5 and not second["has_more"]
        assert len({r["id"] for r in first["runs"] + second["runs"]}) == 25
        assert history.runs(query="中文", status="failed")["total"] == 13
        assert history.runs(query="ALPHA", status="completed")["total"] == 12
        assert history.runs(query="%") ["total"] == 0
        for options in ({"limit": 101}, {"offset": -1}, {"query": "x" * 201}, {"status": "unknown"}):
            try:
                history.runs(**options)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid history parameters must fail")

        with patch.dict(os.environ, {"TOKENFLOW_CONFIG_DB": str(history.path)}):
            server = ThreadingHTTPServer(("127.0.0.1", 0), tf.TokenFlowHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request("GET", "/api/tasks?offset=20")
                reply = connection.getresponse()
                assert reply.status == 403
                reply.read()
                connection.request("GET", "/api/tasks?offset=20", headers={"X-TokenFlow-Token": tf.SESSION_TOKEN})
                reply = connection.getresponse()
                assert reply.status == 200 and len(json.loads(reply.read())["runs"]) == 5
                connection.request("GET", "/api/tasks?limit=bad", headers={"X-TokenFlow-Token": tf.SESSION_TOKEN})
                reply = connection.getresponse()
                assert reply.status == 400
                reply.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

        client = ConnectionClient({"base_url": "http://127.0.0.1:1/v1", "protocol": "openai", "api_key": ""})
        with patch.object(client, "_request", return_value={"message": {"content": "42"}, "prompt_eval_count": 5, "eval_count": 1}) as request:
            assert client.chat("fixture", [], context_window=32768)["usage"]["total_tokens"] == 6
            assert request.call_args.args[1]["think"] is False and request.call_args.args[1]["options"]["temperature"] == 0

    if all(importlib.util.find_spec(name) for name in ("fitz", "pypdf", "PIL", "rapidocr_onnxruntime", "onnxruntime")):
        import fitz
        from scripts.generate_eval_cases import file_bytes
        image = file_bytes("Record ALPHA\nValue 42", "png")
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_image(fitz.Rect(40, 50, 540, 230), stream=image)
            page.insert_text((40, 800), "Page 1")
            data = pdf.tobytes()
        with patch("local_ocr.recognize", return_value={"text": "Record ALPHA\nValue 42\nPage 1", "mean_confidence": .99}) as ocr:
            parsed = parse_document_bytes("mixed.pdf", data)
            assert "42" in parsed["text"] and parsed["text"].count("Page 1") == 1 and parsed["ocr_pages"] == [1]
            ocr.assert_called_once()
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_image(fitz.Rect(40, 30, 90, 48), stream=image)
            page.insert_text((40, 90), "Value 42")
            data = pdf.tobytes()
        with patch("local_ocr.recognize") as ocr:
            assert "42" in parse_document_bytes("logo.pdf", data)["text"]
            ocr.assert_not_called()
    else:
        print("Mixed PDF fixtures: skipped (optional dependencies unavailable)")
    print("Statistics/PDF/budget/history reliability checks: PASS")


if __name__ == "__main__":
    self_check()
