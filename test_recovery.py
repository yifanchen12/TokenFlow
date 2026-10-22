"""Recovery uses cached original source and charges only new bounded calls."""
import copy
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from model_providers import ProviderError
from runtime_config import ConnectionClient, RuntimeConfig
from task_orchestrator import TaskRun, retry_task, start_task
from tokenflow_store import DocumentStore
from test_optimizations import response


def self_check():
    with tempfile.TemporaryDirectory() as directory, patch('tokenflow.detect_harnesses', return_value={}):
        store = RuntimeConfig(Path(directory) / 'connections.db')
        documents = DocumentStore(str(Path(directory) / 'documents.db'))
        for identity, local in (('edge', True), ('cloud', False)):
            store.save_profile({'id': identity, 'name': identity, 'protocol': 'openai', 'local': local,
                                'base_url': 'http://127.0.0.1:9/v1', 'models': ['fixture']})
        store.save_settings({'roles': {'local': 'edge', 'planner': 'cloud', 'executor': 'cloud', 'reviewer': 'cloud'}})
        data = {'task': '分析资料并形成结论', 'content': '原始事实为42'}
        plan = {'steps': [
            {'id': 'extract', 'kind': 'local_model', 'instruction': '提取事实', 'depends_on': [], 'input_scope': 'source', 'expected_output': '关键事实'},
            {'id': 'answer', 'kind': 'model', 'instruction': '汇总事实', 'depends_on': ['extract'], 'input_scope': 'dependencies', 'expected_output': '完整结论'}],
            'acceptance_criteria': ['保留事实42']}
        verdict = json.dumps({'passed': True, 'issues': [], 'reason': '一致', 'evidence': []})
        with patch.object(ConnectionClient, 'chat', side_effect=[response(json.dumps(plan)), response('事实42'), ProviderError('fixture failure')]):
            failed = TaskRun(store, **data, document_store=documents, allow_harnesses=False).execute()
        before = copy.deepcopy(store.runs(failed['id']))
        assert before['status'] == 'failed' and before['can_retry']

        # Run asynchronous submissions synchronously without timers or polling races.
        class Immediate:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        with patch('task_orchestrator.threading.Thread', Immediate), patch.object(ConnectionClient, 'chat', side_effect=[response('完整结论42'), response(verdict)]) as called:
            receipt = retry_task(failed['id'], 'recovery_fixture_1234', store, documents)
            repeated = retry_task(failed['id'], 'recovery_fixture_1234', store, documents)
        recovered = store.runs(receipt['id'])
        assert recovered['id'] != failed['id'] and repeated['id'] == recovered['id'] and repeated['reused']
        assert recovered['status'] == 'completed' and called.call_count == 2
        assert recovered['recovery']['reused_steps'] == ['extract']
        assert recovered['step_results'][0]['reused'] and recovered['step_results'][0]['duration_ms'] == 0
        assert recovered['metrics']['reported_total_tokens'] == 240
        assert store.runs(failed['id']) == before
        assert '事实42' in called.call_args_list[0].args[1][-1]['content']

        with patch.object(ConnectionClient, 'chat', side_effect=[response('42'), response('invalid JSON')]):
            invalid = TaskRun(store, '事实是多少？只回答数字。', data['content'], document_store=documents).execute()
        with patch('task_orchestrator.threading.Thread', Immediate), patch.object(ConnectionClient, 'chat', return_value=response(json.dumps({'reference_answer':'42','reason':'一致','source_quotes':[data['content']],'calculations':[{'path':'','expression':'42'}]}))) as called:
            reviewed = store.runs(retry_task(invalid['id'], 'review_fixture_12345', store, documents)['id'])
        assert reviewed['status'] == 'completed' and reviewed['result'] == '42'
        assert called.call_count == 1 and reviewed['recovery']['candidate_reused']
        assert reviewed['calls'][0]['purpose'] == 'reviewer'
        # Semantic rejection regenerates the final answer, rather than reusing it.
        rejected = copy.deepcopy(invalid)
        rejected.update(id='c' * 32, acceptance={'status': 'failed'})
        store.save_run(rejected)
        with patch('task_orchestrator.threading.Thread', Immediate), patch.object(ConnectionClient, 'chat', side_effect=[response('42'), response(json.dumps({'reference_answer':'42','reason':'一致','source_quotes':[data['content']],'calculations':[{'path':'','expression':'42'}]}))]) as called:
            assert store.runs(retry_task(rejected['id'], 'reject_fixture_12345', store, documents)['id'])['status'] == 'completed'
            assert called.call_count == 2
        # No public request flag can bypass acceptance.
        with patch('task_orchestrator.threading.Thread', Immediate), patch.object(ConnectionClient, 'chat', side_effect=[response('42'), response(json.dumps({'reference_answer':'42','reason':'一致','source_quotes':[data['content']],'calculations':[{'path':'','expression':'42'}]}))]) as called:
            run = start_task({**data, 'task': '事实是多少？只回答数字。', 'evaluation_mode': 'raw'}, store, document_store=documents)
            assert store.runs(run['id'])['acceptance']['status'] == 'passed' and called.call_count == 2
        # Internal baseline modes deliberately differ only in verification, not source selection.
        for mode, outputs in (('raw', ['42']), ('verified_raw', ['42', json.dumps({'reference_answer':'42','reason':'一致','source_quotes':[data['content']],'calculations':[{'path':'','expression':'42'}]})])):
            with patch.object(ConnectionClient, 'chat', side_effect=[response(v) for v in outputs]) as called:
                run = TaskRun(store, '事实是多少？只回答数字。', data['content'], document_store=documents, evaluation_mode=mode).execute()
            assert run['status'] == 'completed' and called.call_count == len(outputs)
            assert run['preprocessing']['compression_applied'] is False
        long_source = ('无关背景说明。' * 80 + '\n\n') * 20 + 'TARGET = 42'
        with patch.object(ConnectionClient, 'chat', side_effect=[response('42'), response(json.dumps({'reference_answer':'42','reason':'一致','source_quotes':['TARGET = 42'],'calculations':[{'path':'','expression':'42'}]}))]) as called:
            selected = TaskRun(store, 'TARGET 的值是多少？只回答数字。', long_source, document_store=documents).execute()
        assert selected['status'] == 'completed' and selected['routing'] == 'direct' and called.call_count == 2
        assert selected['preprocessing']['compression_applied'] and not selected['preparation_decision']['automatic']
        # A failed file parse is not an intentionally empty-input task.
        with patch('document_parser.parse_document_file', side_effect=ValueError('fixture parse failure')):
            parse_failed = TaskRun(store, '分析文件', '', source_path='missing.txt', document_store=documents).execute()
        assert not parse_failed['input_empty'] and not parse_failed['can_retry']
        try: retry_task(parse_failed['id'], '', store, documents)
        except ValueError: pass
        else: raise AssertionError('failed parse resumed with no source')
        # Side-effect steps and a replaced/missing cache never silently restart.
        unsafe = copy.deepcopy(before)
        unsafe.update(id='d' * 32, plan=[{'kind': 'harness'}])
        store.save_run(unsafe)
        for previous in (unsafe, before):
            if previous is before:
                with documents._connect() as db:
                    db.execute('UPDATE chunks SET content=? WHERE document_id=?', ('replaced source', before['document_cache']['document_id']))
            with patch.object(ConnectionClient, 'chat') as called:
                try: retry_task(previous['id'], '', store, documents)
                except ValueError: pass
                else: raise AssertionError('unsafe recovery allowed')
                called.assert_not_called()
    print('Recovery and internal baseline self-check: PASS')


if __name__ == '__main__':
    self_check()
