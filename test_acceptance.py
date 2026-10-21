"""Focused checks for native structured reviews, grounded repairs and exact lookups."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from model_providers import ProviderError
from runtime_config import RuntimeConfig, ConnectionClient
from task_orchestrator import TaskRun, REVIEW_SCHEMA, lookup_table
from tokenflow_store import DocumentStore
from test_optimizations import response


def self_check():
    source = 'record,region,quantity\n"A 17","north,west",43\nA170,south,9\n'
    assert lookup_table('CSV 表格中 A 17 的 quantity 是多少？只回答数字。', source)['answer'] == '43'
    assert lookup_table('表格中 A170 的 quantity 是多少？只回答数字。', source)['answer'] == '9'
    assert lookup_table('表格中 A 17 的 quantity 是多少？只回答数字。', source + 'A 17,x,8\n')['issues']
    assert lookup_table('表格中 A 17 的 missing 是多少？只回答数字。', source)['issues']
    assert lookup_table('表格中 A 17 的 quantity 是多少？然后总结。', source) is None
    assert lookup_table('表格中 A 的 value 是多少？只回答数字。', 'id\tvalue\nA\t=5*4 [无缓存值]')['issues']
    assert lookup_table('表格中 A 的 value 是多少？只回答数字。', 'id\tvalue\nA\t')['issues']
    assert lookup_table('表格中 A 的 value 是多少？只回答数字。', 'id\tvalue\tvalue\nA\t8\t9')['issues']
    assert lookup_table('表格中 A 的 value 是多少？只回答数字。', '[工作表：one]\nid\tvalue\nA\t8') is None
    with tempfile.TemporaryDirectory() as directory:
        store = RuntimeConfig(Path(directory) / 'connections.db')
        profile = store.save_profile({'id':'local', 'name':'Fixture', 'protocol':'ollama',
                                      'base_url':'http://127.0.0.1:1/v1', 'models':['fixture'], 'api_key':''})
        assert profile['local']
        try:
            store.save_profile({**profile, 'base_url':'https://example.com/v1'})
        except ValueError:
            pass
        else:
            raise AssertionError('native connection must be loopback')
        store.save_settings({'roles':{r:'local' for r in ('planner','executor','reviewer')}})
        documents = DocumentStore(str(Path(directory) / 'documents.db'))
        client = ConnectionClient(store.profile('local', True))
        with patch.object(client, '_request', return_value={'message':{'content':'{}'}, 'prompt_eval_count':4,'eval_count':1}) as request:
            client.chat('fixture', [], context_window=8192, response_schema=REVIEW_SCHEMA)
            assert request.call_args.args[1]['format'] == REVIEW_SCHEMA
            assert request.call_args.args[1]['options']['num_ctx'] == 8192
            assert request.call_args.args[1]['think'] is False
        passed = {'passed':True,'issues':[],'reason':'数值一致','evidence':[]}
        rejected = {'passed':False,'issues':['数值不一致'],'reason':'原文为42，候选为41',
                    'evidence':[{'source_quote':'ANSWER = 42','answer_quote':'41','correction':'42'}]}
        def run(outputs):
            with patch('task_orchestrator.ConnectionClient.chat', side_effect=[response(v) for v in outputs]):
                return TaskRun(store, 'ANSWER是多少？只回答数字。', 'ANSWER = 42', document_store=documents, allow_harnesses=False).execute()
        repaired = run(['41', json.dumps(rejected), '42', json.dumps(passed)])
        assert repaired['status'] == 'completed' and len(repaired['calls']) == 4
        assert [c['answer'] for c in repaired['candidates']] == ['41','42']
        rejected['evidence'][0]['source_quote'] = '不存在的原文'
        ungrounded = run(['41', json.dumps(rejected)])
        assert ungrounded['status'] == 'needs_review' and len(ungrounded['calls']) == 2
        invalid = run(['42','invalid JSON'])
        assert invalid['status'] == 'needs_review' and invalid['result'] == '42'
        assert invalid['acceptance']['error_code'] == 'invalid_json'
        inconsistent = run(['42',json.dumps({**passed,'issues':['却有问题']})])
        assert inconsistent['status'] == 'needs_review'
        oversized = run(['42',json.dumps({**passed,'reason':'x' * 161})])
        assert oversized['acceptance']['error_code'] == 'invalid_verdict'
        rejected['evidence'][0]['source_quote'] = 'ANSWER = 42'
        with patch('task_orchestrator.ConnectionClient.chat', side_effect=[response('41'), response(json.dumps(rejected)), ProviderError('incomplete fixture', usage={'prompt_tokens':5,'completion_tokens':3})]):
            failed_repair = TaskRun(store, 'ANSWER是多少？只回答数字。', 'ANSWER = 42', document_store=documents, allow_harnesses=False).execute()
        assert failed_repair['result'] == '41' and failed_repair['candidates'][0]['answer'] == '41'
        assert failed_repair['status'] != 'completed' and failed_repair['calls'][-1]['error_code'] == 'incomplete_response'
        with patch('task_orchestrator.ConnectionClient.chat') as called:
            local = TaskRun(store, '表格中 A170 的 quantity 是多少？只回答数字。', source, document_store=documents).execute()
        called.assert_not_called()
        assert local['status'] == 'completed' and local['result'] == '9'
    print('Structured review/candidates/local lookup checks: PASS')


if __name__ == '__main__':
    self_check()
