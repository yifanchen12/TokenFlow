"""Focused offline regression checks. No real credentials or paid calls."""
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from runtime_config import RuntimeConfig, ConnectionClient, native_catalogs
from task_orchestrator import TaskRun, local_statistics


def self_check():
    import tokenflow as tf
    with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"TOKENFLOW_DB_PATH": str(Path(temp) / "documents.db")}):
        store = RuntimeConfig(Path(temp) / "connections.db")
        claude_args = tf.build_harness_args("claude", "fixture-model", "fixture prompt", temp, ["claude"])
        assert claude_args[-2:] == ["--", "fixture prompt"]  # --add-dir must not swallow the prompt
        state = {"models": ["fixture-small", "fixture-quality"], "requests": []}

        class Fixture(BaseHTTPRequestHandler):
            def do_GET(self):
                state["requests"].append((self.path, self.headers.get("Authorization")))
                body = json.dumps({"data": [{"id": m} for m in state["models"]]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["requests"].append((self.path, data))
                if self.path.endswith("responses"):
                    result = {"output": [{"type": "reasoning", "content": None},
                                         {"content": [{"type": "output_text", "text": "fixture result"}]}],
                              "usage": {"input_tokens": 10, "output_tokens": 4}}
                elif self.path.endswith("messages"):
                    result = {"content": [{"type": "text", "text": "fixture result"}], "usage": {"input_tokens": 10, "output_tokens": 4}}
                else:
                    result = {"choices": [{"message": {"content": "fixture result"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 4}}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/v1"
            profile = store.save_profile({"id": "fixture", "name": "Fixture", "base_url": url,
                                          "protocol": "openai", "local": True, "api_key": "test-fixture-key", "models": []})
            assert "test-fixture-key" not in json.dumps(store.public())
            if os.name == "nt":
                assert b"test-fixture-key" not in store.path.read_bytes()
            first = store.discover("fixture")
            assert first["models"] == state["models"] and first["discovered_at"]
            assert state["requests"][0][1] == "Bearer test-fixture-key"
            state["models"] = ["fixture-new"]
            second = store.discover("fixture")
            assert second["models"] == ["fixture-new"] and "fixture-small" in second["model_history"]
            reopened = RuntimeConfig(store.path)
            assert reopened.profile("fixture", True)["api_key"] == "test-fixture-key"
            reopened.save_profile(second | {"api_key": ""})
            assert reopened.profile("fixture", True)["api_key"] == "test-fixture-key"
            for bad in ("https://user:secret@example.invalid/v1", "http://example.invalid/v1", "https://example.invalid/v1?key=test"):
                try:
                    store.save_profile(second | {"base_url": bad})
                except ValueError:
                    pass
                else:
                    raise AssertionError("unsafe URLs must fail")
            for protocol in ("openai", "responses", "anthropic"):
                saved = store.save_profile({"id": protocol, "name": protocol, "base_url": url, "protocol": protocol, "models": ["fixture-new"]})
                response = ConnectionClient(store.profile(protocol, True)).chat("fixture-new", [{"role": "user", "content": "fixture"}])
                assert response["choices"][0]["message"]["content"] == "fixture result"
            store.save_settings({"bindings": {"codex": {"connection": "responses", "model": "fixture-new"}}})
            with patch.object(tf, "_resolve_harness_command", return_value=["codex"]), patch.object(tf, "_run_bounded", return_value=(0, "done", "", False, False, False)) as spawned:
                result = tf.execute_configured_harness("codex", "auto", "fixture prompt", store)
                args, cwd, timeout, env = spawned.call_args.args
                assert result["ok"] and result["model"] == "fixture-new"
                assert any("model_providers.tokenflow_saved.base_url" in a for a in args)
                assert not any("test-fixture-key" in a for a in args)
                assert "TOKENFLOW_SAVED_API_KEY" in env
            try:
                store.save_settings({"bindings": {"codex": {"connection": "anthropic", "model": "auto"}}})
            except ValueError:
                pass
            else:
                raise AssertionError("protocol mismatch must fail")
            # Explicit cloud preprocessing is allowed and counted as cloud, not local inference.
            store.save_settings({"roles": {"local": "openai"}})
            assert store.settings()["roles"]["local"] == "openai"
            store.save_settings({"bindings": {"dsh": {"connection": "fixture", "model": "fixture-new"}}})
            def dsh_child(args, cwd, timeout, env, **kwargs):
                patch_path = args[args.index("--patch") + 1]
                patch_text = Path(patch_path).read_text(encoding="utf-8")
                overlay = json.loads(patch_text)
                assert "test-fixture-key" not in patch_text and "--model" not in args
                assert overlay[1]["config"]["model"] == "fixture-new"
                assert overlay[2]["config"]["defaultPreset"] == "read-only"
                assert env["TOKENFLOW_SAVED_API_KEY"] == "test-fixture-key"
                return (0, "done", "", False, False, False)
            with patch.object(tf, "_resolve_harness_command", return_value=["dsh"]), patch.object(tf, "_run_bounded", side_effect=dsh_child):
                assert tf.execute_configured_harness("dsh", "auto", "fixture prompt", store)["ok"]
            with patch.dict(os.environ, {"BUPT_API_BASE_URL": url, "BUPT_API_KEY": "test-bupt-key"}):
                imported = store.import_native("dsh")
                assert imported["protocol"] == "openai" and not imported["local"] and imported["key_configured"]
                assert "test-bupt-key" not in json.dumps(imported)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        stats = local_statistics("name,value\na,2\nb,4\n")
        assert stats["table"]["rows"] == 2 and stats["table"]["columns"]["value"]["mean"] == "3"
        snapshots, save_run = [], store.save_run
        def capture(record):
            snapshots.append(json.loads(json.dumps(record)))
            save_run(record)
        with patch.object(store, "save_run", side_effect=capture), patch.object(tf, "execute_configured_harness", side_effect=AssertionError("statistics must not use a model")):
            run = TaskRun(store, "统计CSV的行数和平均值", "name,value\na,2\nb,4\n").execute()
        assert run["status"] == "completed" and run["metrics"]["model_calls"] == 0 and run["acceptance"]["method"] == "deterministic"
        assert all("metrics" in r for r in snapshots if r["status"] == "completed")

        store.save_settings({"roles": {"local": "auto"}, "max_calls": 6})
        calls = []
        plan = {"steps": [{"kind": "harness", "harness": "codex", "instruction": "读取并分析"},
                          {"kind": "harness", "harness": "claude", "instruction": "汇总上一步并给出结论"}],
                "acceptance_criteria": ["回答用户任务", "保留数字42"]}
        answers = [json.dumps(plan), "first result 42", "final result 42", json.dumps({"passed": True, "issues": [], "reason": "已覆盖任务且保留数字42"})]
        def harness(harness, model, prompt, **kwargs):
            calls.append((harness, prompt))
            return {"ok": True, "output": answers[len(calls)-1], "model": "fixture-quality"}
        with patch.object(tf, "detect_harnesses", return_value={h: {"installed": True, "configured": True} for h in ("codex", "claude", "dsh")}), \
             patch.object(TaskRun, "local_target", return_value=None), patch.object(tf, "execute_configured_harness", side_effect=harness):
            run = TaskRun(store, "分析资料并完成结论", "事实：42").execute()
        assert run["status"] == "completed" and run["metrics"]["model_calls"] == 4
        assert calls[1][0] == "codex" and calls[2][0] == "claude" and "first result 42" in calls[2][1]
        assert run["acceptance"]["status"] == "passed" and not run["metrics"]["usage_complete"]

        bad_answers = [json.dumps({"steps": [{"kind": "shell", "instruction": "anything"}], "acceptance_criteria": ["test"]})]
        with patch.object(TaskRun, "local_target", return_value=None), patch.object(tf, "execute_configured_harness", return_value={"ok": True, "output": bad_answers[0]}):
            bad = TaskRun(store, "复杂分析", "资料").execute()
        assert bad["status"] == "failed" and len(bad["calls"]) == 1 and bad["acceptance"]["status"] == "not_evaluated"

        store.save_settings({"max_calls": 3})
        with patch.object(TaskRun, "local_target", return_value=None), patch.object(tf, "detect_harnesses", return_value={h: {"installed": True, "configured": True} for h in ("codex", "claude")}), \
             patch.object(tf, "execute_configured_harness", return_value={"ok": True, "output": json.dumps(plan)}):
            over = TaskRun(store, "复杂分析", "资料").execute()
        assert over["status"] == "failed" and "预算" in over["error"] and len(over["calls"]) == 1

        # An unsuccessful review cannot be promoted to completion just because execution returned text.
        one = {"steps": [{"kind": "model", "instruction": "回答任务"}], "acceptance_criteria": ["必须包含42"]}
        responses = iter([json.dumps(one), "wrong result", json.dumps({"passed": False, "issues": ["缺少42"], "reason": "缺少必要事实"})])
        with patch.object(TaskRun, "local_target", return_value=None), patch.object(tf, "execute_configured_harness", side_effect=lambda *a, **kw: {"ok": True, "output": next(responses)}):
            rejected = TaskRun(store, "分析资料", "事实42").execute()
        assert rejected["status"] == "needs_review" and rejected["acceptance"]["status"] == "failed" and len(rejected["calls"]) == 3
        store.save_settings({"max_calls": 6})
        calls.clear()
        answers[:] = [json.dumps(plan), "first result 42", "final result 42", json.dumps({"passed": True, "issues": [], "reason": "已核对原文42"})]
        local_response = {"choices": [{"message": {"content": "本地摘要：关键事实42"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
        with patch.object(tf, "detect_harnesses", return_value={h: {"installed": True, "configured": True} for h in ("codex", "claude", "dsh")}), \
             patch.object(tf, "execute_configured_harness", side_effect=harness), patch("task_orchestrator.ConnectionClient.chat", return_value=local_response) as local:
            local_run = TaskRun(store, "分析资料并完成结论", "背景说明。" * 1500 + "关键事实42").execute()
        assert local_run["status"] == "completed" and local_run["metrics"]["local_calls"] == 1 and local.call_count == 1
        assert "本地摘要：关键事实42" in calls[0][1] and "背景说明。" * 100 not in calls[0][1]
        assert "背景说明。" * 100 in calls[-1][1]  # reviewer checks original source
        responses = iter([json.dumps(one), "wrong result", json.dumps({"passed": False, "issues": ["缺少42"], "reason": "缺少必要事实"}),
                          "correct result42", json.dumps({"passed": True, "issues": [], "reason": "已修正缺失的42"})])
        with patch.object(TaskRun, "local_target", return_value=None), patch.object(tf, "execute_configured_harness", side_effect=lambda *a, **kw: {"ok": True, "output": next(responses)}):
            repaired = TaskRun(store, "分析资料", "事实42").execute()
        assert repaired["status"] == "completed" and len(repaired["calls"]) == 5 and repaired["result"] == "correct result42"
        assert store.runs(run["id"])["result"] == "final result 42"
        assert "test-fixture-key" not in json.dumps(store.runs())
    print("Orchestration self-check: PASS")


if __name__ == "__main__":
    self_check()
