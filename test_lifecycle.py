"""Cross-process ownership, stable task configuration and recovery checks."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from runtime_config import RuntimeConfig, ConnectionClient
from runtime_ownership import FileLease, lease_path, run_active
from task_orchestrator import TaskRun, start_task
from tokenflow_store import DocumentStore


def self_check():
    with tempfile.TemporaryDirectory() as directory:
        store = RuntimeConfig(Path(directory) / "connections.db")
        identity = "a" * 32
        record = {"id": identity, "task": "Fixture", "status": "running", "process_id": os.getpid(), "calls": []}
        store.save_run(record)
        lock = FileLease.acquire(lease_path(store.path, "task"), {"run_id": identity})
        assert lock and FileLease.acquire(lease_path(store.path, "task")) is None
        service = FileLease.acquire(lease_path(store.path, "service"), {"port": 8765})
        other = FileLease.acquire(lease_path(Path(directory) / "other.db", "service"))
        assert service and other
        child = '''
import json,sys
from runtime_config import RuntimeConfig
from runtime_ownership import FileLease,lease_path
s=RuntimeConfig(sys.argv[1]);result={"status":s.runs(sys.argv[2])["status"]}
for kind in ("task","service"):
    lease=FileLease.acquire(lease_path(s.path,kind));result[kind+"_blocked"]=lease is None
    if lease:lease.close()
try:s.manage_history(sys.argv[2]);result["delete_blocked"]=False
except ValueError:result["delete_blocked"]=True
print(json.dumps(result))
'''
        try:
            output = subprocess.run([sys.executable, "-c", child, str(store.path), identity], capture_output=True,
                                    text=True, timeout=15, check=True, cwd=Path(__file__).parent)
            data = json.loads(output.stdout)
            assert data == {"status": "running", "task_blocked": True, "service_blocked": True, "delete_blocked": True}
            record["managed_owner"] = True
            assert run_active(store.path, record)
        finally:
            lock.close(); service.close(); other.close()
        assert not run_active(store.path, record)
        import tokenflow as tf
        service = FileLease.acquire(lease_path(store.path, 'service'), {'host': '127.0.0.1', 'port': 8765})
        try:
            with patch.dict(os.environ, {'TOKENFLOW_CONFIG_DB': str(store.path)}), patch.object(tf, 'ThreadingHTTPServer') as server, patch('webbrowser.open') as opened:
                tf.run_server(port=0, reuse_existing=True)
                server.assert_not_called(); opened.assert_not_called()
                tf.run_server(port=0, open_browser=True, reuse_existing=True)
                opened.assert_called_once_with('http://127.0.0.1:8765')
                try: tf.run_server(port=0)
                except RuntimeError: pass
                else: raise AssertionError('CLI duplicate did not reject')
            import launcher
            with patch.dict(os.environ, {'TOKENFLOW_OPEN_BROWSER': '0'}), patch.object(tf, 'run_server') as serve:
                launcher.main()
                assert serve.call_args.kwargs == {'open_browser': False, 'reuse_existing': True}
        finally:
            service.close()
        store.save_run(record)
        assert store.runs(identity)["status"] == "interrupted"
        # A process that exits without releasing in Python still loses its OS lock.
        dying = '''
import os,sys
from runtime_config import RuntimeConfig
from runtime_ownership import FileLease,lease_path
s=RuntimeConfig(sys.argv[1]);i="b"*32
l=FileLease.acquire(lease_path(s.path,"task"),{"run_id":i})
s.save_run({"id":i,"task":"Fixture","status":"running","process_id":os.getpid(),"managed_owner":True,"calls":[]})
print("ready",flush=True);input()
'''
        process = subprocess.Popen([sys.executable, "-c", dying, str(store.path)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=Path(__file__).parent)
        try:
            assert process.stdout.readline().strip() == "ready"
            assert store.runs("b" * 32)["status"] == "running"
            process.communicate("\n", timeout=10)
            assert store.runs("b" * 32)["status"] == "interrupted"
        finally:
            if process.poll() is None:
                process.kill(); process.communicate()
        profile = {"id": "fixture", "name": "Fixture", "protocol": "openai", "local": True,
                   "base_url": "http://127.0.0.1:9/v1", "models": ["old-model"], "api_key": "fixture-secret"}
        store.save_profile(profile)
        store.save_settings({"roles": {r: "fixture" for r in ("local", "planner", "executor", "reviewer")}})
        runner = TaskRun(store, "总结", "source")
        store.save_profile({**profile, "base_url": "http://127.0.0.1:10/v1", "models": ["new-model"]})
        observed = []
        def chat(client, model, messages, **kwargs):
            observed.append((client.profile["base_url"], model))
            return {"choices": [{"message": {"content": "answer"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        with patch.object(ConnectionClient, "chat", chat):
            runner.role_call("executor", "总结", "source")
            TaskRun(store, "总结", "source").role_call("executor", "总结", "source")
        assert observed == [(profile["base_url"], "old-model"), ("http://127.0.0.1:10/v1", "new-model")]
        assert "fixture-secret" not in json.dumps(runner.run)
        assert "fixture-secret" not in json.dumps(runner.store.public())
        # Harness bindings and their saved connection must use the same frozen view.
        native = {**profile, 'id': 'responses', 'protocol': 'responses'}
        store.save_profile(native)
        store.save_settings({'roles': {'executor': 'native:codex'},
                             'bindings': {'codex': {'connection': 'responses', 'model': 'old-model'}}})
        frozen = TaskRun(store, '总结', 'source')
        store.save_profile({**native, 'base_url': 'http://127.0.0.1:10/v1', 'models': ['new-model']})
        store.save_settings({'bindings': {'codex': {'connection': 'responses', 'model': 'new-model'}}})
        commands = []
        def harness(command, *args, **kwargs):
            commands.append(command)
            events = [{'type': 'turn.started'}, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'answer'}},
                      {'type': 'turn.completed'}]
            return (0, '\n'.join(json.dumps(v) for v in events), '', False, False, False)
        with patch('tokenflow._resolve_harness_command', return_value=['fixture']), patch('tokenflow._run_bounded', harness):
            frozen.role_call('executor', '总结', 'source')
            TaskRun(store, '总结', 'source').role_call('executor', '总结', 'source')
        assert 'http://127.0.0.1:9/v1' in str(commands[0]) and 'old-model' in commands[0]
        assert 'http://127.0.0.1:10/v1' in str(commands[1]) and 'new-model' in commands[1]
        store.save_settings({'bindings': {'codex': {'connection': 'native', 'model': 'auto'}}})
        with patch('runtime_config.native_catalogs', return_value={'codex': {'default_model': 'frozen-native'}}):
            harness_step = TaskRun(store, '分析', 'source')
        with patch('tokenflow.execute_configured_harness', return_value={'ok': True, 'output': 'answer'}) as execute:
            harness_step.call('native:codex', '分析', 'source', 'harness_step')
        assert execute.call_args.args[1] == 'frozen-native'
        # The task mutex belongs to a DB, not the entire Python process.
        independent = RuntimeConfig(Path(directory) / 'independent.db')
        independent.save_profile(profile)
        roles = {r: 'fixture' for r in ('local', 'planner', 'executor', 'reviewer')}
        independent.save_settings({'roles': roles})
        store.save_settings({'roles': roles})
        release, entered = threading.Event(), threading.Event()
        counter, counter_lock = [0], threading.Lock()
        def blocked(client, model, messages, **kwargs):
            if 'reference_answer' in json.dumps(messages):
                answer = json.dumps({'reference_answer':'42','reason':'一致','source_quotes':['事实42'],'calculations':[{'path':'','expression':'42'}]})
            elif 'candidate_answer' in json.dumps(messages):
                answer = json.dumps({'passed': True, 'issues': [], 'reason': '一致', 'evidence': []})
            else:
                with counter_lock:
                    counter[0] += 1
                    if counter[0] == 2: entered.set()
                assert release.wait(10)
                answer = '42'
            return {'choices': [{'message': {'content': answer}}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 1}}
        with patch.object(ConnectionClient, 'chat', blocked):
            try:
                running = []
                for index, config in enumerate((store, independent)):
                    documents = DocumentStore(str(Path(directory) / f'parallel-{index}.db'))
                    running.append((config, start_task({'task': '事实是多少？只回答数字。', 'content': '事实42'}, config, document_store=documents)['id']))
                assert entered.wait(5)
                assert all(config.runs(identity)['status'] == 'running' for config, identity in running)
                try: start_task({'task': 'another', 'content': ''}, store)
                except RuntimeError: pass
                else: raise AssertionError('same-DB task started twice')
            finally:
                release.set()
            deadline = time.monotonic() + 10
            while any(config.runs(identity)['status'] == 'running' for config, identity in running) and time.monotonic() < deadline:
                time.sleep(.02)
            records = [config.runs(identity) for config, identity in running]
            assert all(record['status'] == 'completed' for record in records), [(r['status'], r.get('acceptance')) for r in records]
    print("Lifecycle self-check: PASS")


if __name__ == "__main__":
    self_check()
