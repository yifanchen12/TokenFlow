"""Three arms, independent refusal checks and honest all-attempt accounting."""
import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from eval_scenarios import ARMS, evaluate_scenarios, guard_pass, scenario_summary
from runtime_config import ConnectionClient, RuntimeConfig
from test_optimizations import response


def self_check():
    from scripts.generate_eval_cases import generate_collaboration_cases
    development = generate_collaboration_cases(20261022, 'development')
    holdout = generate_collaboration_cases(20261022, 'holdout')
    assert development == generate_collaboration_cases(20261022, 'development')
    assert len(development) == len(holdout) == 60
    assert len({c['category'] for c in development}) == 6
    assert all(c['template'].startswith('holdout-') for c in holdout)
    assert all('guard_expectation' in c and 'expected_answer' not in c for c in generate_collaboration_cases(20261022, 'guards'))
    with tempfile.TemporaryDirectory() as directory, patch('tokenflow.detect_harnesses', return_value={}):
        store = RuntimeConfig(Path(directory) / 'connections.db')
        store.save_profile({'id': 'edge', 'name': 'Fixture', 'local': True, 'protocol': 'openai',
                            'base_url': 'http://127.0.0.1:9/v1', 'models': ['fixture'], 'api_key': 'fixture-secret'})
        original = store.settings()
        positive = {'name': 'code', 'task': 'report 的返回值是多少？只回答数字。', 'content': 'BASE = 40\ndef report():\n    return BASE + 2\n',
                    'expected_answer': '42', 'must_keep': ['BASE = 40', 'return BASE + 2'], 'category': 'code', 'source_kind': 'synthetic'}
        guard = {'name': 'duplicate', 'task': '表格中 A 的 value 是多少？只回答数字。', 'content': 'id,value\nA,42\nA,43',
                 'must_keep': ['A,42', 'A,43'], 'category': 'ambiguous', 'guard_expectation': {'statuses': ['needs_review'], 'issue_contains': ['不唯一']}}
        def chat(client, model, messages, **kwargs):
            if 'reference_answer' in json.dumps(messages):
                return response(json.dumps({'reference_answer':'42','reason':'独立计算','source_quotes':['BASE = 40'], 'calculations':[]}))
            if 'candidate_answer' in json.dumps(messages):
                return response(json.dumps({'passed': True, 'issues': [], 'reason': '一致', 'evidence': []}))
            return response('42')
        progress = []
        with patch.object(ConnectionClient, 'chat', chat):
            report = evaluate_scenarios([positive, guard], store, 'edge', complex_target='edge', progress=lambda r: progress.append(copy.deepcopy(r)),
                                        min_cases=1, min_category_cases=1)
        assert store.settings() == original and store.runs()['total'] == 0
        assert len(progress) == 6 and len(progress[0]['cases'][0]['arms']) == 1
        assert set(report['cases'][0]['arms']) == set(ARMS)
        assert [report['cases'][0]['arms'][a]['model_calls'] for a in ARMS] == [1, 2, 0]
        assert all(report['cases'][0]['arms'][arm]['status'] == 'completed' for arm in ARMS)
        assert report['cases'][0]['arms']['collaboration']['answer'] == '42'
        assert report['summary']['arms']['collaboration']['guard_passed'] == 1
        assert report['quality_gate']['status'] == 'passed'
        assert 'fixture-secret' not in json.dumps(report)
        assert 'http://127.0.0.1:9' not in json.dumps(report)
        store.save_profile({'id': 'cloud', 'name': 'Cloud fixture', 'local': False, 'protocol': 'responses',
                            'base_url': 'http://127.0.0.1:10/v1', 'models': ['fixture']})
        source = {**positive, 'task': '值是多少？只回答数字。', 'content': '值为42', 'must_keep': ['值为42']}
        def request(client, suffix, payload, **kwargs):
            assert suffix == '/responses' and 'options' not in payload
            verdict = json.dumps({'passed': True, 'issues': [], 'reason': '一致', 'evidence': []})
            reference = json.dumps({'reference_answer':'42','reason':'独立计算','source_quotes':['值为42'],'calculations':[{'path':'','expression':'42'}]})
            text = reference if 'reference_answer' in json.dumps(payload) else verdict if 'candidate_answer' in json.dumps(payload) else '42'
            return {'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': text}]}],
                    'usage': {'input_tokens': 30, 'output_tokens': 10}}
        with patch.object(ConnectionClient, '_request', request):
            mixed = evaluate_scenarios([source], store, 'edge', complex_target='cloud', min_cases=1, min_category_cases=1)
        assert all(v['status'] == 'completed' and v['usage_complete'] for v in mixed['cases'][0]['arms'].values())
        assert not guard_pass({'status': 'failed', 'result': '资料不足'}, {'answer': ['资料不足']})
        rows = copy.deepcopy(report['cases'])
        # A failed arm with reported usage still contributes to totals and fails the gate.
        value = rows[0]['arms']['collaboration']
        value.update(status='failed', correct=False, accepted_correct=False,
                     usage={'prompt_tokens': 300, 'completion_tokens': 100, 'total_tokens': 400})
        summary, gate = scenario_summary(rows, 2, min_cases=1, min_category_cases=1)
        assert summary['arms']['collaboration']['total_tokens'] == 400 and gate['status'] == 'failed'
        value.update(correct=True, accepted_correct=False, acceptance={'status':'invalid'})
        summary, gate = scenario_summary(rows, 2, min_cases=1, min_category_cases=1)
        assert summary['arms']['collaboration']['correct_but_blocked'] == {'execution_failure':1}
        value.update(status='needs_review')
        summary, gate = scenario_summary(rows, 2, min_cases=1, min_category_cases=1)
        assert summary['arms']['collaboration']['correct_but_blocked'] == {'invalid_review':1}
        # Native/harness usage missing is unknown, never zero or a passing saving.
        value.update(status='completed', correct=True, accepted_correct=True, usage={}, usage_complete=False)
        summary, gate = scenario_summary(rows, 2, min_cases=1, min_category_cases=1)
        assert summary['arms']['collaboration']['total_tokens'] is None
        assert summary['comparisons']['raw']['all_attempt_token_saving_ratio'] is None
        assert gate['status'] == 'not_evaluated'
        # Correct answers with complete usage still cannot pass without native isolation evidence.
        exposed = copy.deepcopy(report['cases'][:1])
        exposed[0]['arms']['collaboration']['isolation_complete'] = False
        _, gate = scenario_summary(exposed, 1, min_cases=1, min_category_cases=1)
        assert gate['status'] == 'not_evaluated' and any('隔离' in reason for reason in gate['reasons'])
        with patch.object(ConnectionClient, 'chat', side_effect=AssertionError('must reject before calls')):
            try:
                evaluate_scenarios([source], store, 'edge', complex_target='native:claude')
            except ValueError as exc:
                assert '隔离' in str(exc)
            else:
                raise AssertionError('Unaudited native evaluators must fail before calls')
    print('Three-arm and guard accounting self-check: PASS')


if __name__ == '__main__':
    self_check()
