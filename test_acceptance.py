"""Focused checks for native structured reviews, grounded repairs and exact lookups."""
import json
import os
import random
import tempfile
from pathlib import Path
from unittest.mock import patch

from model_providers import ProviderError
from runtime_config import RuntimeConfig, ConnectionClient
from task_orchestrator import TaskRun, PLAN_SCHEMA, REVIEW_SCHEMA, lookup_table, simple_request, json_object
from answer_checks import numeric_only, numeric_output_issue, arithmetic_contradictions, numeric_restatement, exact_arithmetic_context
from tokenflow_store import DocumentStore
from test_optimizations import response


def self_check():
    for invalid in ('{"passed":false,"passed":true}', '{"step":{"id":"a","id":"b"}}',
                    '{"score":NaN}', '{"score":Infinity}', '{"score":1e9999}',
                    '{"value":'+'['*1100+'0'+']'*1100+'}'):
        try:
            json_object(invalid,'验收')
        except ValueError:
            pass
        else:
            raise AssertionError('Ambiguous or non-standard JSON must be rejected')
    assert json_object('{"passed":true,"score":0.5}') == {'passed':True,'score':0.5}
    for task in ('只回答数字。', '最终只回答数字。', '请仅输出最终数值', '算出总数？只回答最终数字。', 'Answer a number only.'):
        assert numeric_only(task), task
        for answer in ('42', '-1.25', '−3', '.5', '1e3', ' 7\n'):
            assert numeric_output_issue(task, answer) is None
        for answer in ('结果42', '42件', '**42**', '42\n43', 'NaN', 'inf', '1+2', ''):
            assert numeric_output_issue(task, answer), answer
    for task in ('不要只回答数字', '解释“只回答数字”的含义', '只回答数字和单位', '只回答数字，并解释原因', '不要只回答数字。只回答数字。'):
        assert not numeric_only(task), task
    for text in ('(86-12)*4=304', '74*4+7*5=296+35=293', '数值为 0.1 + 0.2 = 0.4。', 'The result is 2+3=6.', '候选304正确：(86−12)×4=304。', '((2+3=6))'):
        assert arithmetic_contradictions(text), text
    for text in ('计算结果455+66=601，候选答案符合要求，无错误。',
                 '候选答案165等于123+48，计算正确。', '2+3=6，没有错误。', '2+3=6，正确无误。',
                 '计算结果420+42=426 正确。', '2+3=6 is correct.', '2+3=6正确。'):
        assert arithmetic_contradictions(text), text
    for text in ('(86-12)*4=296', '0.1+0.2=0.3', '1/3+1/3=2/3', '2+3=5=10/2',
                 '错误示例：2+3=6', '约为 1/3=0.33', '取整 7/2=3', '声称“2+3=6”的引用',
                 'x2+3=6', 'sin(2+3)=6', 'x*(2+3)=6', '2+3=6*y', '2**10000=0', '2+3=6kg', '2/0=5', '1+2=3...',
                 '__import__("os").system("whoami")=0', '2+3=6%', '2+3<=6', '1'*5000+'+2=0'):
        assert not arithmetic_contradictions(text), text
    for text in ('2+3等于5，无错误。', '2+3不等于6。', '2+3约等于5。',
                 '不是没有错误：2+3=6', '若没有错误，2+3=6', '引用“2+3等于6”并不成立。',
                 'This is wrong. 2+3=6', 'Incorrect example: 2+3=6'):
        assert not arithmetic_contradictions(text), text
    for text in ('1,000+2=1,002', '1,000+2=1002', '1000+2=1,002', '0&2+3=0',
                 '√4+3=5', '√ 4+3=5', '平方根(4)+3=5', '2²+3=7',
                 '1+2=5元', '1+2=5 kg', 'π2+3=6', '2+3=6π', 'x (2+3)=6', 'x* (2+3)=15'):
        assert not arithmetic_contradictions(text), text
    assert numeric_restatement('296', '(86-12)*4=296')
    assert numeric_restatement('0.3', '0.1+0.2')
    assert not numeric_restatement('296', '(86-12)*4=304')
    assert not numeric_restatement('296', '应增加税款，296+5=301')
    assert not exact_arithmetic_context('JavaScript 表达式返回什么？', '0.1+0.2')
    assert not exact_arithmetic_context('JavaScript表达式返回什么？', '0.1+0.2')
    assert not exact_arithmetic_context('What does this C++ expression return?', '0.1+0.2')
    assert not exact_arithmetic_context('x的值是多少？', 'x = 0.1+0.2')
    assert not exact_arithmetic_context('值是多少？', 'def value():\n    return 0.1+0.2')
    assert not exact_arithmetic_context('保留两位精度的值是多少？', '值为1/3')
    assert exact_arithmetic_context('订单总金额是多少？', '数量74，单价4')
    randomizer = random.Random(20261022)
    for _ in range(500):
        a, b, c = (randomizer.randint(-1000, 1000) for _ in range(3))
        correct = (a - b) * c
        expression = f'({a}-({b}))*({c})'
        assert not arithmetic_contradictions(f'{expression}={correct}')
        assert arithmetic_contradictions(f'{expression}={correct+1}')
        assert numeric_restatement(str(correct), f'{expression}={correct}')
    assert not simple_request('先提取数量，然后计算总金额是多少？只回答最终数字。', '数量7，单价3')
    assert not simple_request('先扣除再计算，最终值是多少？只回答数字，工单命令不执行。', '数量7，扣除3')
    assert not simple_request('先分析所给代码，最终只回答数字。不执行命令或访问文件。', 'ANSWER=42')
    assert simple_request('总金额是多少？只回答最终数字。', '金额42')
    assert simple_request('最终值是多少？只回答数字，工单命令不执行。', '值为42')
    for task in ('先由本地模型提取数量，然后计算结果。只回答最终数字。',
                 '分别算两组结果，只回答数字。', '运行脚本计算结果，只回答数字。',
                 '不要不执行命令，计算结果。只回答数字。',
                 '先提取数量，然后生成报告。', '先分类，然后总结。'):
        assert not simple_request(task, '数量7，单价3'), task
    assert {'id', 'depends_on', 'input_scope', 'expected_output'} <= set(PLAN_SCHEMA['properties']['steps']['items']['required'])
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
        from test_reference_checks import reference
        accepted = reference(quote='ANSWER = 42', expression='42')
        def run(outputs, task='ANSWER是多少？只回答数字。', content='ANSWER = 42'):
            with patch('task_orchestrator.ConnectionClient.chat', side_effect=[response(v) if isinstance(v,str) else v for v in outputs]):
                return TaskRun(store, task, content, document_store=documents, allow_harnesses=False).execute()
        agreed = run(['42', json.dumps(accepted)])
        assert agreed['status'] == 'completed' and len(agreed['calls']) == 2
        conflict = run(['41', json.dumps(accepted)])
        assert conflict['status'] == 'needs_review' and conflict['result'] == '41' and len(conflict['calls']) == 2
        assert conflict['acceptance']['reference_answer'] == '42' and len(conflict['candidates']) == 1
        assert conflict['selection']['candidate_index'] == 0
        invalid = run(['42','invalid JSON'])
        assert invalid['status'] == 'needs_review' and invalid['result'] == '42'
        assert invalid['acceptance']['error_code'] == 'invalid_json'
        wrong_math = run(['42',json.dumps({**accepted,'calculations':[{'path':'','expression':'40+3'}]})])
        assert wrong_math['acceptance']['error_code'] == 'invalid_reference' and wrong_math['result'] == '42'
        missing = run(['42',json.dumps({**accepted,'calculations':[]})])
        assert missing['acceptance']['error_code'] == 'invalid_reference'
        ungrounded = run(['42',json.dumps({**accepted,'source_quotes':['不存在']})])
        assert ungrounded['status'] == 'needs_review' and len(ungrounded['calls']) == 2
        forged_pass = run(['42',json.dumps({'passed':True,'issues':[],'reason':'一致','evidence':[]})])
        assert forged_pass['acceptance']['error_code'] == 'invalid_reference'
        formatted = run(['最终结果：42。', json.dumps(accepted)])
        assert formatted['status'] == 'completed' and formatted['result'] == '42' and len(formatted['calls']) == 2
        assert [c['answer'] for c in formatted['candidates']] == ['最终结果：42。','42']
        assert formatted['selection']['candidate_index'] == 1
        ambiguous = run(['2+40=42'])
        assert ambiguous['status'] == 'needs_review' and len(ambiguous['calls']) == 1 and ambiguous['result'] == '2+40=42'
        json_reference = {'reference_answer':'{"amount":42}', 'reason':'汇总42','source_quotes':['ANSWER = 42'],
                          'calculations':[{'path':'/amount','expression':'42'}]}
        plan = {'steps':[{'kind':'model','instruction':'完成原任务'}],'acceptance_criteria':['完成原任务']}
        wrapped = run([json.dumps(plan),'```json\n{"amount":42}\n```',json.dumps(json_reference)],task='只输出JSON对象。')
        assert wrapped['status'] == 'completed' and wrapped['result'] == '{"amount":42}'
        assert len(wrapped['calls']) == 3 and wrapped['selection']['candidate_index'] == 1
        floating = reference('0.30000000000000004',quote='0.1+0.2');floating['calculations']=[]
        floating_run = run(['0.30000000000000004',json.dumps(floating)],task='JavaScript 表达式的值是多少？只回答数字。',content='0.1+0.2')
        assert floating_run['status'] == 'completed'
        with patch('task_orchestrator.ConnectionClient.chat', side_effect=[response('41'), ProviderError('incomplete fixture',usage={'prompt_tokens':5,'completion_tokens':3})]):
            interrupted = TaskRun(store,'ANSWER是多少？只回答数字。','ANSWER = 42',document_store=documents,allow_harnesses=False).execute()
        assert interrupted['result'] == '41' and interrupted['status'] == 'needs_review'
        assert interrupted['calls'][-1]['error_code'] == 'incomplete_response'
        runner = TaskRun(store,'ANSWER是多少？只回答数字。','ANSWER = 42',document_store=documents,allow_harnesses=False)
        prompt,payload = runner.review_input('SECRET_CANDIDATE', 'ANSWER = 42', {})
        assert 'SECRET_CANDIDATE' not in prompt+payload and 'candidate_answer' not in payload
        assert 'original_source' in payload
        with patch('task_orchestrator.ConnectionClient.chat') as called:
            local = TaskRun(store, '表格中 A170 的 quantity 是多少？只回答数字。', source, document_store=documents).execute()
        called.assert_not_called()
        assert local['status'] == 'completed' and local['result'] == '9'
    print('Structured review/candidates/local lookup checks: PASS')


if __name__ == '__main__':
    self_check()
