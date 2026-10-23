"""API routes complex execution and blind review without local-model vetoes."""
import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from runtime_config import ConnectionClient, RuntimeConfig
from task_orchestrator import TaskRun, task_preflight
from test_optimizations import response


def self_check():
    source, answer = '数量391119，补充2', '391121'
    task = '依据全部数量计算合计。只回答数字。'
    plan = {'complexity':'complex','selected_harness':'codex','routing_reason':'需要独立计算与核对',
            'steps':[{'id':'s1','kind':'model','instruction':'依据原资料计算完整答案',
                      'depends_on':[],'input_scope':'source','expected_output':'用户要求的最终答案'}],
            'acceptance_criteria':['按原资料完成原任务']}
    route = {k:plan[k] for k in ('complexity','selected_harness','routing_reason')} | {'local_operation':'none'}
    proof = {'reference_answer':answer,'reason':'按原文相加','source_quotes':[source],
             'calculations':[{'path':'','expression':'391119+2'}]}
    with tempfile.TemporaryDirectory() as directory, patch('tokenflow.detect_harnesses', return_value={
            h:{'installed':True,'configured':True} for h in ('codex','claude','dsh')}):
        store = RuntimeConfig(Path(directory)/'connections.db')
        for identity, local in [('edge',True),('router',False)]:
            store.save_profile({'id':identity,'name':identity,'local':local,'protocol':'openai',
                                'base_url':'http://127.0.0.1:9/v1','models':['deepseek-v4-flash' if identity=='router' else 'fixture-model']})
        store.save_settings({'api_dispatch':True,'collaboration_mode':'hybrid',
            'roles':{'local':'edge','planner':'router','executor':'router','reviewer':'edge'},
            'role_models':{'planner':'deepseek-v4-flash'},'bindings':{'codex':{'connection':'native','model':'gpt-6-astra'}}})
        frozen = store.settings()
        with patch.object(ConnectionClient,'chat',side_effect=AssertionError('Preview makes no model calls')):
            preview = task_preflight({'task':task,'content':source},store)
        assert preview['ready'] and preview['api_dispatch'] and set(preview['harnesses']) == {'codex'}
        observed = []
        def api(client, model, messages, **kw):
            assert client.profile['id'] == 'router', 'Complex tasks must never call the edge model'
            assert model == 'deepseek-v4-flash' and kw.get('route_json') is True
            observed.append(('router',json.dumps(messages)))
            return response(json.dumps(route,ensure_ascii=False))
        native = []
        def harness(h, model, prompt, **kw):
            assert h == 'codex' and model == 'gpt-6-astra' and kw.get('text_only') is True
            native.append(prompt)
            return {'ok':True,'output':json.dumps(proof) if 'reference_answer' in prompt else answer, 'model':model,
                    'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30},
                    'isolation':{'policy':'isolated_text_v1','complete':True,'tool_items':0}}
        with patch.object(ConnectionClient,'chat',api), patch('tokenflow.execute_configured_harness',side_effect=harness):
            run = TaskRun(store,task,source,allow_harnesses=False).execute()
        assert run['status'] == 'completed' and run['result'] == answer
        assert [c['target'] for c in run['calls']] == ['router','native:codex','native:codex']
        assert answer not in native[1] and 'candidate_answer' not in native[1]
        assert run['api_dispatch']['reviewer'] == 'native:codex' and run['metrics']['local_calls'] == 0
        assert store.settings() == frozen
        client = ConnectionClient(store.profile('router'))
        with patch.object(client,'_request',return_value=response('{}')) as request:
            client.chat('deepseek-v4-flash',[],route_json=True)
            body = request.call_args.args[1]
            assert body['thinking'] == {'type':'disabled'} and body['response_format'] == {'type':'json_object'}
            client.chat('deepseek-v4-flash',[])
            assert 'thinking' not in request.call_args.args[1]
        from eval_scenarios import evaluate_scenarios
        case = {'name':'routed','task':task,'content':source,'expected_answer':answer,
                'must_keep':[source],'category':'arithmetic','source_kind':'synthetic'}
        with patch.object(ConnectionClient,'chat',api), patch('tokenflow.execute_configured_harness',side_effect=harness):
            comparison = evaluate_scenarios([case],store,'edge',complex_target='native:codex',complex_model='gpt-6-astra',
                                             min_cases=1,min_category_cases=1)
        arms = comparison['cases'][0]['arms']
        assert [arms[k]['model_calls'] for k in ('raw','verified_raw','collaboration')] == [1,2,3]
        assert arms['collaboration']['api_dispatch']['planner'] == 'router'
        assert all(v['accepted_correct'] for v in arms.values())
        for bad in (dict(route,selected_harness='claude'), dict(route,selected_harness=''),
                    dict(route,selected_harness='',local_operation='extract'),
                    dict(route,complexity='uncertain',selected_harness='',local_operation='extract'),
                    dict(route,steps=plan['steps'])):
            with patch.object(ConnectionClient,'chat',return_value=response(json.dumps(bad))), \
                 patch('tokenflow.execute_configured_harness',side_effect=AssertionError('Bad plan must not execute')):
                rejected = TaskRun(store,task,source).execute()
            assert rejected['status'] == 'failed' and len(rejected['calls']) == 1
        native.clear()
        runner = TaskRun(store,task,source,allow_harnesses=False)
        runner.resume_state = {'plan':copy.deepcopy(plan['steps']),'criteria':plan['acceptance_criteria'],
                              'completed':[],'api_dispatch':run['api_dispatch']}
        with patch.object(ConnectionClient,'chat',side_effect=AssertionError('Reuse validated route')), \
             patch('tokenflow.execute_configured_harness',side_effect=harness):
            resumed = runner.execute()
        assert resumed['status'] == 'completed' and len(resumed['calls']) == 2
        replies = iter(['hello',json.dumps({'passed':True,'issues':[],'reason':'一致','evidence':[]})])
        def edge(client,*a,**kw):
            assert client.profile['id'] == 'edge'
            return response(next(replies))
        with patch.object(ConnectionClient,'chat',edge):
            local = TaskRun(store,'翻译这段话','你好',collaboration_mode='local_only').execute()
        assert local['status'] == 'completed' and all(c['target']=='edge' for c in local['calls'])
        try: store.save_settings({'api_dispatch':1})
        except ValueError: pass
        else: raise AssertionError('Dispatch flag must be boolean')
        store.save_settings({'max_task_tokens':256})
        with patch.object(ConnectionClient,'chat',side_effect=AssertionError('Budget must be reserved first')):
            budget = TaskRun(store,task,source).execute()
        assert budget['status'] == 'failed' and not budget['calls']
    print('API dispatch, complex-review isolation and budget checks: PASS')


if __name__ == '__main__':
    self_check()
