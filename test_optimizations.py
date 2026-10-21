"""Offline regression checks for parsing, transport, task lifecycle and full-pipeline metrics."""
import io
import json
import os
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from document_parser import parse_document_bytes
from runtime_config import RuntimeConfig
from task_orchestrator import TaskRun, TaskCancelled, cancel_task, start_task, simple_request, local_statistics, deterministic_task
from tokenflow_store import DocumentStore


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buffer.getvalue()


def response(text, *, usage=True):
    result = {"choices": [{"message": {"content": text}}]}
    if usage:
        result["usage"] = {"prompt_tokens": 100, "completion_tokens": 20}
    return result


def self_check():
    import tokenflow as tf
    from eval_workflow import evaluate_pipeline

    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = f'''<worksheet xmlns="{ns}"><sheetData>
      <row r="1"><c r="A1" t="inlineStr"><is><t>name</t></is></c><c r="B1" t="inlineStr"><is><t>value</t></is></c><c r="C1" t="inlineStr"><is><t>other</t></is></c></row>
      <row r="2"><c r="A2" t="s"><v>0</v></c><c r="C2"><v>42</v></c></row>
      <row r="4"><c r="A4" t="inlineStr"><is><r><t>be</t></r><r><t>ta</t></r></is></c><c r="B4"><v>8</v></c><c r="C4"><f>B4*2</f></c></row>
    </sheetData></worksheet>'''
    files = {"xl/worksheets/sheet2.xml": sheet,
             "xl/sharedStrings.xml": f'<sst xmlns="{ns}"><si><t>alpha</t></si></sst>'}
    parsed = parse_document_bytes("fixture.xlsx", archive(files))
    assert parsed["text"].splitlines() == ["name\tvalue\tother", "alpha\t\t42", "\t\t", "beta\t8\t=B4*2 [无缓存值]"]
    assert parsed["sheets"][0]["rows"] == 4 and parsed["warnings"]
    stats = local_statistics(parsed["text"])
    assert stats["table"]["columns"]["value"]["sum"] == "8"
    files.update({
        "xl/worksheets/sheet10.xml": sheet.replace("42", "99"),
        "xl/workbook.xml": f'<workbook xmlns="{ns}" xmlns:r="relations"><sheets><sheet name="First" r:id="b"/><sheet name="Second" r:id="a"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="a" Target="worksheets/sheet2.xml"/><Relationship Id="b" Target="/xl/worksheets/sheet10.xml"/></Relationships>'})
    parsed = parse_document_bytes("multi.xlsx", archive(files))
    assert [s["name"] for s in parsed["sheets"]] == ["First", "Second"]
    assert [t["table"]["columns"]["other"]["sum"] for t in local_statistics(parsed["text"])["tables"]] == ["99", "42"]
    docx = archive({"word/document.xml": '<w:document xmlns:w="w"><w:p><w:r><w:t>hel</w:t></w:r><w:r><w:t>lo</w:t></w:r></w:p><w:p><w:r><w:t>world</w:t></w:r></w:p></w:document>'})
    assert parse_document_bytes("fixture.docx", docx)["text"] == "hello\n\nworld"

    # Exercise real pipes with more than Windows' argv limit, including UTF-8.
    prompt = "中文长资料\n" * 10000
    code, out, _, cut, _, timed_out = tf._run_bounded(
        [sys.executable, "-c", "import sys; print(len(sys.stdin.buffer.read().decode('utf-8')))"], os.getcwd(), 10, stdin_text=prompt)
    assert code == 0 and int(out) == len(prompt) and not cut and not timed_out
    event = threading.Event()
    timer = threading.Timer(.2, event.set)
    timer.start()
    started = time.monotonic()
    try:
        tf._run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], os.getcwd(), 10, cancel_event=event)
    except TaskCancelled:
        pass
    else:
        raise AssertionError("cancel must stop the child")
    finally:
        timer.join()
    assert time.monotonic() - started < 8

    for harness in ("codex", "claude", "dsh"):
        args = tf.build_harness_args(harness, "auto", prompt, ".", [harness])
        def child(command, cwd, timeout, env, **kwargs):
            assert prompt not in command and len(" ".join(command)) < 32767
            if harness == "dsh":
                overlay = json.loads(Path(command[command.index("--patch") + 1]).read_text(encoding="utf-8"))
                assert overlay[0]["config"]["task"] == prompt
            else:
                assert kwargs["stdin_text"] == prompt
                assert command[-1] == ("-" if harness == "codex" else "--")
            return (0, "done", "", False, False, False)
        with patch.object(tf, "_run_bounded", side_effect=child):
            assert tf._run_harness(args, harness, prompt, None)[0] == 0

    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "TOKENFLOW_DB_PATH": str(Path(directory) / "documents.db"),
            "TOKENFLOW_CONFIG_DB": str(Path(directory) / "connections.db")}):
        store = RuntimeConfig(Path(directory) / "connections.db")
        store.save_profile({"id": "fixture", "name": "Fixture", "base_url": "http://127.0.0.1:1/v1", "protocol": "openai", "local": True, "models": ["fixture"]})
        store.save_settings({"roles": {r: "fixture" for r in ("local", "planner", "executor", "reviewer")}})
        with patch("task_orchestrator.ConnectionClient.chat") as called:
            counted = TaskRun(store, "统计 CSV 的行数和平均值", "name,value\na,2\nb,4\n").execute()
            assert counted["status"] == "completed" and counted["metrics"]["model_calls"] == 0
            assert json.loads(counted["result"])["table"]["columns"]["value"]["mean"] == "3"
            called.assert_not_called()
        assert not deterministic_task("统计 C S V 的平均值", local_statistics("not a table"))
        review = json.dumps({"passed": True, "issues": [], "reason": "已核对事实42"})
        with patch("task_orchestrator.ConnectionClient.chat", side_effect=[response("42"), response(review)]) as called:
            direct = TaskRun(store, "ANSWER是多少？", "ANSWER = 42").execute()
        assert direct["status"] == "completed" and called.call_count == 2 and direct["routing"] == "direct"
        assert direct["metrics"]["reported_total_tokens"] == 240
        assert not simple_request("先分析文件，然后生成报告", "资料")
        assert not simple_request("修改代码并解释 ANSWER 是多少？", "ANSWER=42")
        assert simple_request("总结下面的文档并提取关键结论", "资料")
        cancelled = TaskRun(store, "ANSWER是多少？", "ANSWER = 42")
        cancelled.cancel_event.set()
        with patch("task_orchestrator.ConnectionClient.chat") as called:
            assert cancelled.execute()["status"] == "cancelled"
            called.assert_not_called()

        documents = DocumentStore(str(Path(directory) / "documents.db"))
        source = "# 背景\n" + "不相关背景。\n" * 500 + "\n# 退款期限\n退款期限为37天。\n\n# 其他\n" + "额外背景。\n" * 500
        documents.index("unrelated", "退款期限为999天。")
        first = documents.index("policy.md", source)
        assert documents.index("again.md", source)["reused"]
        matches = documents.search("退款期限", document_id=first["document_id"])
        assert matches and all(m["document_id"] == first["document_id"] for m in matches)
        assert all(source[m["start"]:m["start"] + len(m["content"])] == m["content"] for m in matches)
        runner = TaskRun(store, "退款期限是几天？", source, document_store=documents)
        selected = runner.select_context()
        assert "退款期限为37天" in selected and "999" not in selected and len(selected) < len(source)
        assert runner.run["document_cache"]["reused"] and runner.run["sources"]
        assert TaskRun(store, "总结全文", source, document_store=documents).select_context() == source.strip()

        # Cancellation during an HTTP call retains usage and never starts review.
        entered, release = threading.Event(), threading.Event()
        def blocked_call(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return response("42")
        with patch("task_orchestrator.ConnectionClient.chat", side_effect=blocked_call) as called:
            task = start_task({"task": "ANSWER是多少？", "content": "ANSWER = 42"}, store)
            assert entered.wait(5)
            server = ThreadingHTTPServer(("127.0.0.1", 0), tf.TokenFlowHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                payload = json.dumps({"id": task["id"]})
                connection.request("POST", "/api/tasks/cancel", payload, {"Content-Type": "application/json"})
                result = connection.getresponse()
                assert result.status == 403
                result.read()
                connection.request("POST", "/api/tasks/cancel", payload,
                                   {"Content-Type": "application/json", "X-TokenFlow-Token": tf.SESSION_TOKEN})
                result = connection.getresponse()
                assert result.status == 200 and json.loads(result.read())["status"] == "cancelling"
                connection.close()
            finally:
                release.set()
                server.shutdown()
                server.server_close()
                thread.join()
            for _ in range(100):
                record = store.runs(task["id"])
                if record["status"] == "cancelled":
                    break
                time.sleep(.03)
            assert record["status"] == "cancelled" and called.call_count == 1
            assert record["metrics"]["reported_input_tokens"] == 100
        store.save_run({"id": "a" * 32, "task": "old", "status": "cancelling", "process_id": -1})
        assert store.runs("a" * 32)["status"] == "interrupted"

        case = {"name": "short", "source_kind": "synthetic", "task": "ANSWER是多少？", "content": "ANSWER = 42", "must_keep": ["42"], "expected_answer": "42"}
        profile = store.profile("fixture", True)
        with patch("runtime_config.ConnectionClient.chat", side_effect=[response("42"), response("42"), response(review)]):
            report = evaluate_pipeline([case], profile, "fixture", min_cases=1, min_saving=0)
        assert report["summary"]["pipeline_calls"] == 2
        assert report["summary"]["reported_processed_total_tokens"] == 240
        assert report["summary"]["reported_raw_total_tokens"] == 120
        assert report["summary"]["raw_correct_cases"] == report["summary"]["accepted_correct_cases"] == 1
        assert report["summary"]["reported_all_total_token_saving_ratio"] == -1
        assert report["quality_gate"]["status"] == "failed"  # full pipeline costs more than one call
        bad_review = json.dumps({"passed": False, "issues": ["缺少42"], "reason": "回答错误",
                                 "evidence": [{"source_quote": "ANSWER = 42", "answer_quote": "wrong", "correction": "42"}]})
        with patch("runtime_config.ConnectionClient.chat", side_effect=[response("42"), response("wrong"), response(bad_review), response("42"), response(review)]):
            repaired = evaluate_pipeline([case], profile, "fixture", min_cases=1, min_saving=0)
        assert repaired["summary"]["pipeline_calls"] == 4 and repaired["summary"]["repair_calls"] == 2
        assert repaired["summary"]["reported_processed_total_tokens"] == 480
        assert repaired["summary"]["repair_improved_cases"] == 1 and repaired["summary"]["repair_degraded_cases"] == 0
        with patch("runtime_config.ConnectionClient.chat", side_effect=[response("42"), response("42"), response("invalid review JSON")]):
            failed = evaluate_pipeline([case], profile, "fixture", min_cases=1, min_saving=0)
        assert failed["summary"]["completed_pairs"] == 0 and failed["summary"]["accepted_correct_cases"] == 0
        assert failed["summary"]["raw_correct_cases"] == 1 and failed["summary"]["reported_all_raw_total_tokens"] == 120
        assert failed["summary"]["reported_pipeline_total_tokens"] == 240 and failed["summary"]["reported_all_total_token_saving_ratio"] == -1
        from model_providers import ProviderError
        with patch("runtime_config.ConnectionClient.chat", side_effect=[ProviderError("raw fixture failed", usage={"prompt_tokens":5,"completion_tokens":1}), response("42"), response(review)]):
            raw_failed = evaluate_pipeline([case], profile, "fixture", min_cases=1, min_saving=0)
        assert raw_failed["summary"]["raw_correct_cases"] == 0 and raw_failed["summary"]["completed_pairs"] == 0
        assert raw_failed["summary"]["accepted_correct_cases"] == 1 and raw_failed["summary"]["reported_all_raw_total_tokens"] == 6
        with patch("runtime_config.ConnectionClient.chat", side_effect=[response("42", usage=False), response("42", usage=False), response(review, usage=False)]):
            missing = evaluate_pipeline([case], profile, "fixture", min_cases=1, min_saving=0)
        assert missing["quality_gate"]["status"] == "not_evaluated" and not missing["summary"]["usage_complete"]
        assert missing["summary"]["reported_all_raw_total_tokens"] is None and missing["summary"]["reported_all_total_token_saving_ratio"] is None
    quality_self_check()
    print("Optimization regression checks: PASS")


def quality_self_check():
    import tokenflow as tf
    from eval_workflow import _validate_case, evaluate, sample_cases, summarize
    from model_providers import ProviderError, response_text
    from scripts.generate_eval_cases import CATEGORIES, file_bytes, generate_cases, write_cases

    generated = [case for split in ("development", "holdout") for case in generate_cases(20261013, split)]
    assert len(generated) == 120 and len({c["name"] for c in generated}) == 120
    for category in CATEGORIES:
        assert sum(c["category"] == category for c in generated) == 20
    development, holdout = generated[:60], generated[60:]
    assert {c["template"] for c in development}.isdisjoint({c["template"] for c in holdout})
    assert development == generate_cases(20261013, "development")
    validation = generate_cases(20261020, "validation")
    assert len(validation) == 60 and validation == generate_cases(20261020, "validation")
    assert {c["template"] for c in validation}.isdisjoint({c["template"] for c in generated})
    assert all(sum(c["category"] == category for c in validation) == 10 for category in CATEGORIES)
    from task_orchestrator import lookup_table
    for case in validation:
        if case["category"] == "table":
            lookup = lookup_table(case["task"], case["content"])
            assert lookup and not lookup["issues"] and lookup["answer"] == case["expected_answer"]
    for case in generated:
        _validate_case(case, True)
    for fmt in ("txt", "docx", "xlsx"):
        text = "Record\tValue\nALPHA\t42" if fmt == "xlsx" else "Record ALPHA\nValue 42"
        data = file_bytes(text, fmt)
        assert data == file_bytes(text, fmt)
        assert "42" in parse_document_bytes("case." + fmt, data)["text"]
    with tempfile.TemporaryDirectory() as directory, patch("scripts.generate_eval_cases.file_bytes", side_effect=ImportError):
        root = Path(directory) / "cases"
        result = write_cases(root, 20261013)
        assert result["status"] == "incomplete" and len(result["skipped"]) == 20 and result["generated_cases"] == 100
        marker = root / "generation.json"
        before = marker.read_bytes()
        try:
            write_cases(root, 1)
        except FileExistsError:
            pass
        else:
            raise AssertionError("generation cannot overwrite an earlier corpus")
        assert marker.read_bytes() == before

    with tempfile.TemporaryDirectory() as directory:
        store = RuntimeConfig(Path(directory) / "connections.db")
        documents = DocumentStore(str(Path(directory) / "documents.db"))
        conflict = next(c for c in generated if c["template"] == "dated-rule-update")
        runner = TaskRun(store, conflict["task"], conflict["content"], document_store=documents)
        selected = runner.select_context()
        assert all(f in selected for f in conflict["must_keep"])
        assert runner.run["retrieval"]["sources_scope"] == "search_preview"
        assert runner.run["retrieval"]["matches"] == 3
        for template in ("global-constant", "helper-constant-chain"):
            case = next(c for c in generated if c["template"] == template)
            selected = tf.compact_content(case["content"], task=case["task"])
            assert all(f in selected for f in case["must_keep"]) and len(selected) < len(case["content"])
        background = "\n\n".join(f"def other_{i}():\n    return {i}" for i in range(100))
        cycle = "def first():\n    return second()\n\ndef second():\n    return first()\n\n" + background
        selected = tf.compact_content(cycle, task="first 的返回值是什么？")
        assert "def first" in selected and "def second" in selected and len(selected) < len(cycle)
        for target in ("def target():\n    return globals()['VALUE']", "def target():\n    def nested():\n        return 42\n    return nested()", "def target():\n    " + "# long body\n    " * 300 + "return VALUE"):
            source = "VALUE = 42\n\n" + background + "\n\n" + target
            assert tf.compact_content(source, task="target 的返回值是什么？") == source.strip()
        imported = "import math\n\n" + background + "\n\ndef target():\n    return math.pi\n"
        assert "import math" in tf.compact_content(imported, task="target 的返回值是什么？")
        # Neither isolated selection nor synthetic generation calls a model.
        with patch("runtime_config.ConnectionClient.chat") as model_call, patch("task_orchestrator.TaskRun.execute") as execute:
            report = evaluate(generated, offline_task_context=True)
            model_call.assert_not_called()
            execute.assert_not_called()
        assert report["summary"]["cases"] == 120 and report["summary"]["critical_fact_loss_rate"] == 0
        assert report["quality_gate"]["status"] == "not_evaluated" and report["summary"]["processed_accuracy"] is None

    for content, reason in (("42", "length"), ("", "stop"), ({"unsafe": "text"}, "stop"), ("42", [])):
        value = response(content)
        value["choices"][0]["finish_reason"] = reason
        try:
            response_text(value)
        except ProviderError as exc:
            assert exc.usage["prompt_tokens"] == 100
        else:
            raise AssertionError("incomplete or malformed answers cannot pass")
    truncated = response("7")
    truncated["choices"][0]["finish_reason"] = "length"
    with patch("eval_workflow.OpenAICompatibleProvider") as client:
        client.return_value.chat.return_value = truncated
        report = evaluate(sample_cases()[:1], "freetoken", "fixture", min_cases=1, min_category_cases=1, min_saving=0)
        assert report["summary"]["completed_pairs"] == 0 and report["quality_gate"]["status"] != "passed"
        assert report["cases"][0]["raw_usage"]["prompt_tokens"] == 100
        client.return_value.chat.return_value = response("7")
        report = evaluate(sample_cases()[:1], "freetoken", "fixture", min_cases=1, min_saving=0)
        assert report["quality_gate"]["status"] == "not_evaluated"  # category still needs 10 pairs

    rows = [{"source_kind": "synthetic", "category": "simple" if i < 90 else "conflict", "compression_applied": True,
             "processed_retains_facts": True, "raw_answer_pass": i < 90, "processed_answer_pass": i < 90,
             "raw_usage": {"prompt_tokens": 100, "total_tokens": 102}, "processed_usage": {"prompt_tokens": 80, "total_tokens": 82},
             "raw_latency_ms": 1, "processed_latency_ms": 1} for i in range(100)]
    summary, gate = summarize(rows, True, 50, .02, .05)
    assert summary["processed_accuracy"] == .9 and summary["by_category"]["conflict"]["processed_accuracy"] == 0
    assert gate["status"] == "failed" and gate["categories"]["synthetic"]["conflict"]["status"] == "failed"
    for row in rows[90:]:
        row.update(source_kind="real_task", category="simple")
    summary, gate = summarize(rows, True, 50, .02, .05)
    assert summary["by_source_kind"]["synthetic"]["processed_accuracy"] == 1
    assert summary["by_source_kind"]["real_task"]["processed_accuracy"] == 0 and gate["status"] == "failed"
    print("Synthetic corpus/evidence/gate checks: PASS")


if __name__ == "__main__":
    self_check()
