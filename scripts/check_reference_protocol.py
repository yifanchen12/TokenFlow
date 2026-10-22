"""One-shot blind reference diagnostics; fixed candidates, never a full-task score."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval_workflow import answer_pass, load_cases
from runtime_config import RuntimeConfig
from task_orchestrator import TaskRun


def save(path, value):
    temporary = path.with_suffix('.partial')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def summary(rows):
    calls = [call for row in rows for call in row.get('calls', [])]
    return {'cases': len(rows), 'valid_references': sum(r['valid_reference'] for r in rows),
            'gold_agreements': sum(r.get('gold_agreement', False) for r in rows),
            'accepted_correct': sum(r['acceptance'].get('passed', False) and r['candidate_correct'] for r in rows),
            'false_accepts': sum(r['acceptance'].get('passed', False) and not r['candidate_correct'] for r in rows),
            'false_rejects': sum(not r['acceptance'].get('passed', False) and r['candidate_correct'] for r in rows),
            'reference_errors': dict(Counter(r['acceptance']['reference_error'] for r in rows
                                            if r['acceptance'].get('reference_error'))),
            'model_calls': len(calls),
            'reported_total_tokens': sum(c.get('usage', {}).get('total_tokens', 0) for c in calls),
            'unknown_usage_calls': sum(not {'prompt_tokens', 'completion_tokens', 'total_tokens'} <= c.get('usage', {}).keys() for c in calls)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['prepare', 'baseline', 'candidate', 'replay'])
    parser.add_argument('target', choices=['local', 'native'], nargs='?', default='local')
    parser.add_argument('--root', type=Path, default=Path('tmp/reference-compat-20261023'))
    args = parser.parse_args()
    root = args.root
    root.mkdir(parents=True, exist_ok=True)
    cases_path = root / 'pilot-cases.json'
    from scripts.generate_eval_cases import generate_collaboration_cases
    if args.phase == 'prepare':
        from scripts.generate_verification_cases import generate_cases
        if cases_path.exists(): raise SystemExit('Preserve existing pilot cases.')
        cases = generate_collaboration_cases(20261022, 'holdout')[::10] + generate_cases(202610232)[::10]
        save(cases_path, cases)
        save(root / 'baseline-source.json', {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
             for name in ['answer_checks.py', 'task_orchestrator.py', 'scripts/check_reference_protocol.py']})
        print('Fixed 12-case pilot prepared.', flush=True)
        return
    path = root / f'{args.target}-{args.phase}.json'
    if path.exists(): raise SystemExit('Preserve existing diagnostic; no automatic reruns.')
    cases = load_cases(cases_path)
    previous = None
    if args.phase == 'replay':
        pilot = json.loads((root / f'{args.target}-candidate.json').read_text(encoding='utf-8'))
        if not pilot.get('continue_to_replay'): raise SystemExit('Pilot did not pass the predeclared continuation gate.')
        old_path = Path('docs/reports/assets/reference-compat-20261023') / f'{args.target}-fixed-candidates.json'
        previous = json.loads(old_path.read_text(encoding='utf-8'))
        cases = (generate_collaboration_cases(20261022, 'holdout') if args.target == 'local' else
                 load_cases(Path('docs/reports/assets/reference-20261023/validation.json')))
    target = 'edge' if args.target == 'local' else 'native:codex'
    model = 'qwen3.5:9b' if args.target == 'local' else 'gpt-6-luna'
    store = RuntimeConfig(root / f'{args.target}-{args.phase}.db')
    store.save_profile({'id': 'edge', 'name': 'Local reference diagnostic', 'local': True, 'protocol': 'ollama',
                        'base_url': 'http://127.0.0.1:11666/v1', 'models': ['qwen3.5:9b']})
    store.save_settings({'roles': {'local': 'edge', 'planner': target, 'executor': target, 'reviewer': target},
                         'role_models': {'local': 'qwen3.5:9b', 'planner': model, 'executor': model, 'reviewer': model},
                         'context_window_tokens': 32768})
    report = {'phase': args.phase, 'target': args.target, 'scope': 'blind_reference_only_fixed_candidates_synthetic',
              'pilot_cases_sha256': hashlib.sha256(cases_path.read_bytes()).hexdigest(), 'cases': [],
              'source_sha256': {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
                                for name in ['answer_checks.py', 'task_orchestrator.py', 'scripts/check_reference_protocol.py']}}
    print(f'{args.target} {args.phase}: modules loaded; one shot per case.', flush=True)
    transport_failures = 0
    for case in cases:
        old = previous[case['name']] if previous else None
        candidate = old['answer'] if old else case['expected_answer']
        if old and old['acceptance'].get('method') == 'deterministic':
            review, calls, captured = old['acceptance'], [], {}
        else:
            runner = TaskRun(store, case['task'], case['content'], allow_harnesses=False, ollama_context=32768)
            prompt, content = runner.review_input('CANDIDATE_SENTINEL', case['content'], {})
            assert 'CANDIDATE_SENTINEL' not in prompt + content
            captured = {}
            original = runner.role_call
            def capture(*a, **kw):
                result = original(*a, **kw)
                captured['output'] = result
                return result
            runner.role_call = capture
            review = runner.review(candidate, case['content'], {})
            calls = runner.run['calls']
        valid = review.get('verification', {}).get('kind') == 'independent_reference'
        gold_agreement = False
        if valid:
            gold_agreement = (review['passed'] if old is None else answer_pass(
                review['reference_answer'], case['expected_answer'], case.get('answer_match', 'exact')))
        row = {'name': case['name'], 'category': case['category'], 'candidate': candidate,
               'candidate_correct': answer_pass(candidate, case['expected_answer'], case.get('answer_match', 'exact')),
               'valid_reference': valid, 'gold_agreement': gold_agreement, 'acceptance': review,
               'reference_output': captured.get('output'), 'calls': calls}
        if old: row['previous_acceptance'] = old['acceptance']
        report['cases'].append(row)
        report['summary'] = summary(report['cases'])
        report['complete'] = len(report['cases']) == len(cases)
        if args.phase == 'candidate' and report['complete']:
            baseline = json.loads((root / f'{args.target}-baseline.json').read_text(encoding='utf-8'))
            score = report['summary']
            report['continue_to_replay'] = (baseline.get('complete', False) and score['valid_references'] >= 10
                and score['gold_agreements'] >= 9 and score['accepted_correct'] >= baseline['summary']['accepted_correct'])
        save(path, report)
        print(args.target, args.phase, len(report['cases']), review['status'], review.get('reference_error', ''), flush=True)
        transport_failures = transport_failures + 1 if calls and calls[-1]['status'] == 'failed' else 0
        if transport_failures >= 3: raise SystemExit('Three consecutive transport failures; stop and preserve evidence.')
    print(report['summary'], flush=True)


if __name__ == '__main__':
    main()
