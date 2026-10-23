"""One-shot API/Harness diagnostics using an existing protected connection."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval_scenarios import evaluate_scenarios, markdown_report
from eval_workflow import answer_pass
from runtime_config import RuntimeConfig, FrozenConfig, _runtime_settings, _protect, native_catalogs
from task_orchestrator import TaskRun, task_preflight, json_object


SOURCES = ('runtime_config.py','task_orchestrator.py','answer_checks.py','native_text.py',
           'eval_scenarios.py','scripts/check_api_dispatch.py')


def save(path, value):
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def isolated_config(path, original, connection):
    # The real database is read-only. Only the chosen API secret is decrypted, in memory.
    with sqlite3.connect(original.resolve().as_uri()+'?mode=ro',uri=True) as db:
        row = db.execute("SELECT data FROM settings WHERE id='runtime'").fetchone()
        settings = _runtime_settings(json.loads(row[0]) if row else None)
        profiles = {}
        for data, secret in db.execute('SELECT data,secret FROM connections'):
            profile = json.loads(data)
            profile['key_configured'] = bool(secret)
            if profile['id'] == connection:
                profile['api_key'] = _protect(base64.b64decode(secret),True).decode() if secret else ''
            profiles[profile['id']] = profile
    if connection not in profiles: raise ValueError('指定的调度连接未保存')
    model = settings['role_models']['planner']
    if model == 'auto' or model not in profiles[connection]['models']:
        model = profiles[connection]['models'][0]
    settings['roles']['planner'] = connection
    settings['role_models']['planner'] = model
    settings.update(api_dispatch=True,collaboration_mode='hybrid',max_calls=6,
                    max_task_tokens=64000,context_window_tokens=32768)
    # A catalogued edge target tests routing constraints; no inference service is started.
    profiles['eval-edge'] = {'id':'eval-edge','name':'Registered edge routing fixture','local':True,'protocol':'ollama',
                             'base_url':'http://127.0.0.1:11666/v1','models':['qwen3.5:9b'],'api_key':''}
    settings['roles']['local'] = 'eval-edge'
    settings['role_models']['local'] = 'qwen3.5:9b'
    return FrozenConfig(path,profiles,settings,native_catalogs())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['prepare','pilot','full'])
    parser.add_argument('--root',type=Path,default=Path('tmp/api-dispatch-20261023'))
    parser.add_argument('--config',type=Path,default=RuntimeConfig().path)
    parser.add_argument('--connection',default='imported-dsh')
    args = parser.parse_args()
    root = args.root
    root.mkdir(parents=True,exist_ok=True)
    if args.phase == 'prepare':
        from scripts.generate_verification_cases import generate_cases
        from test_verification_cases import self_check
        if (root/'pilot-cases.json').exists(): raise SystemExit('Preserve existing diagnostic cases.')
        cases = {v['name']:v for v in json.loads(Path('docs/reports/assets/reference-20261023/validation.json').read_text(encoding='utf-8'))}
        prior = json.loads(Path('docs/reports/assets/reference-compat-20261023/native-replay.json').read_text(encoding='utf-8'))['cases']
        selected = [(v,'correct_false_reject') for v in prior if v['candidate_correct'] and not v['acceptance'].get('passed')]
        for category in dict.fromkeys(v['category'] for v in prior):
            selected += [(v,'wrong_candidate') for v in prior if v['category']==category and not v['candidate_correct']][:2]
        assert len(selected)==26
        save(root/'pilot-cases.json',[dict(cases[v['name']],fixed_candidate=v['candidate'],pilot_kind=kind) for v,kind in selected])
        self_check(seed=202610234)
        save(root/'validation.json',generate_cases(202610234))
        print('Fixed 14 correct rejected + 12 wrong candidates; new 60-case corpus labels independently checked.')
        return
    output = root/(args.phase+'.json')
    if output.exists(): raise SystemExit('Preserve existing measurements; no automatic retries.')
    source = {n:hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in SOURCES}
    store = isolated_config(root/(args.phase+'.db'),args.config,args.connection)
    if args.phase == 'full':
        pilot = json.loads((root/'pilot.json').read_text(encoding='utf-8'))
        if not pilot.get('continue_to_full'): raise SystemExit('Pilot gate failed; larger batch is disabled.')
        if pilot['source_sha256'] != source: raise SystemExit('Source changed after pilot; preserve measurements for review.')
        def progress(report):
            report['source_sha256'] = source
            report['conditions'] = 'Frozen source; BUPT API planner; bound Codex model; edge catalogued but no local service started. Synthetic V4 fresh values, seed202610234; not new template families.'
            save(output,report)
            output.with_suffix('.md').write_text(markdown_report(report),encoding='utf-8')
            print('full',len(report['cases']),len(report['cases'][-1]['arms']),flush=True)
            last = report['cases'][-1]['arms']
            if len(last)==3 and all(v['status']=='failed' and v['calls'] and all(c['status']=='failed' for c in v['calls']) for v in last.values()):
                raise SystemExit('Three wholly failed branches; stop and preserve transport evidence.')
        cases = json.loads((root/'validation.json').read_text(encoding='utf-8'))
        model = store.settings()['bindings']['codex']['model']
        evaluate_scenarios(cases,store,'eval-edge',complex_target='native:codex',edge_model='qwen3.5:9b',
                           complex_model=model,ollama_context=32768,progress=progress)
        return
    cases = json.loads((root/'pilot-cases.json').read_text(encoding='utf-8'))
    report = {'scope':'fixed_candidates_api_dispatch_and_blind_review_not_full_tasks',
              'source_sha256':source,'cases':[],'edge_service_started':False}
    transport_failures = 0
    for case in cases:
        runner = TaskRun(store,case['task'],case['content'],allow_harnesses=False)
        capture = {}
        original = runner.role_call
        def record(role,*a,**kw):
            value = original(role,*a,**kw)
            capture[role] = value
            return value
        runner.role_call = record
        try:
            preview = task_preflight({'task':case['task'],'content':case['content']},runner.store)
            if not preview['ready']: raise ValueError('调度预检未通过')
            runner.dispatch_options = preview['harnesses']
            runner.reserve_review = True
            prompt, content = runner.dispatch_input(case['content'])
            runner.validate_plan(runner.build_dispatch_plan(json_object(runner.role_call('planner',prompt,content))))
            assert runner.run['api_dispatch']['reviewer'] == 'native:codex'
            blind_prompt,blind_content = runner.review_input('CANDIDATE_SENTINEL',case['content'],{})
            assert 'CANDIDATE_SENTINEL' not in blind_prompt+blind_content
            review = runner.review(case['fixed_candidate'],case['content'],{})
        except Exception as exc:
            review = {'passed':False,'status':'invalid','error_code':'routing_or_execution','reason':'调度或执行未完成：'+type(exc).__name__}
        row = {'name':case['name'],'category':case['category'],'pilot_kind':case['pilot_kind'],
               'candidate':case['fixed_candidate'],'candidate_correct':answer_pass(case['fixed_candidate'],case['expected_answer'],case.get('answer_match','exact')),
               'acceptance':review,'dispatch':runner.run.get('api_dispatch'),'calls':runner.run['calls'],
               'outputs':capture}
        report['cases'].append(row)
        positive = [r for r in report['cases'] if r['candidate_correct']]
        wrong = [r for r in report['cases'] if not r['candidate_correct']]
        calls = [c for r in report['cases'] for c in r['calls']]
        report['summary'] = {'correct_candidates':len(positive),'correct_accepted':sum(r['acceptance'].get('passed',False) for r in positive),
            'wrong_candidates':len(wrong),'false_accepts':sum(r['acceptance'].get('passed',False) for r in wrong),
            'wrong_valid_rejections':sum(r['acceptance'].get('status')=='failed' and r['acceptance'].get('verification',{}).get('kind')=='independent_reference' for r in wrong),
            'local_model_calls':sum(c.get('local',False) for c in calls),'model_calls':len(calls),
            'reported_total_tokens':sum(c.get('usage',{}).get('total_tokens',0) for c in calls),
            'unknown_usage_calls':sum(not {'prompt_tokens','completion_tokens','total_tokens'} <= c.get('usage',{}).keys() for c in calls)}
        report['complete'] = len(report['cases'])==26
        s = report['summary']
        report['continue_to_full'] = report['complete'] and s['correct_accepted']>=12 and s['false_accepts']==0 and s['local_model_calls']==0
        save(output,report)
        print('pilot',len(report['cases']),case['pilot_kind'],review['status'],flush=True)
        transport_failures = transport_failures+1 if runner.run['calls'] and runner.run['calls'][-1]['status']=='failed' else 0
        if transport_failures>=3: raise SystemExit('Three consecutive transport failures; preserve results.')
    print(report['summary'],report['continue_to_full'],flush=True)


if __name__ == '__main__':
    main()
