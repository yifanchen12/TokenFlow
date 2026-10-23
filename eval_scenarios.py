"""Isolated three-arm evaluation using the production verifier and dispatcher."""
from __future__ import annotations

import json
import hashlib
import tempfile
from collections import Counter
from pathlib import Path

from eval_workflow import _validate_case, answer_pass, summarize
from runtime_config import FrozenConfig, RuntimeConfig
from task_orchestrator import TaskRun
from tokenflow_store import DocumentStore

ARMS = ('raw', 'verified_raw', 'collaboration')


def guard_pass(run: dict, expectation: dict) -> bool:
    """A transport/budget failure is never evidence of a correct refusal."""
    if run['status'] in {'failed', 'cancelled', 'interrupted', 'running'}:
        return False
    if 'statuses' in expectation and run['status'] not in expectation['statuses']:
        return False
    if 'answer' in expectation and not answer_pass(run['result'], expectation['answer'], 'contains_all'):
        return False
    issues = ' '.join(run.get('acceptance', {}).get('issues', []))
    if any(word not in issues for word in expectation.get('issue_contains', [])):
        return False
    return bool(run.get('result') or issues)


def _arm_result(run: dict, case: dict, selected: str) -> dict:
    metrics = run['metrics']
    usage = ({'prompt_tokens': metrics['reported_input_tokens'], 'completion_tokens': metrics['reported_output_tokens'],
              'total_tokens': metrics['reported_total_tokens']} if metrics['usage_complete'] else {})
    candidates = run.get('candidates', [])
    match = lambda answer: answer_pass(answer, case['expected_answer'], case.get('answer_match', 'exact'))
    positive = 'guard_expectation' not in case
    correct = match(run['result']) if positive else guard_pass(run, case['guard_expectation'])
    first = match(candidates[0]['answer']) if positive and candidates else correct
    return {'status': run['status'], 'answer': run['result'], 'correct': bool(correct),
            'accepted_correct': bool(correct) and run['status'] == 'completed',
            'retains_facts': all(f in selected for f in case['must_keep']),
            'compression_applied': run.get('preprocessing', {}).get('compression_applied', False),
            'acceptance': run['acceptance'], 'usage': usage, 'usage_complete': metrics['usage_complete'],
            'duration_ms': metrics['duration_ms'], 'model_calls': metrics['model_calls'],
            'by_role': metrics['by_role'], 'routing': run.get('routing', 'deterministic'),
            'preparation': run.get('preparation_decision'), 'first_correct': bool(first),
            'repair_improved': bool(not first and correct and len(candidates) > 1),
            'repair_degraded': bool(first and not correct and len(candidates) > 1),
            'candidates': candidates, 'steps': run['step_results'], 'upgrades': run['upgrades'],
            'reference_disagreement': run['acceptance']['status'] == 'failed' if run['acceptance'].get('verification', {}).get('kind') == 'independent_reference' else None,
            'format_normalized': any(c.get('purpose') == 'format' for c in candidates),
            'selection': run.get('selection'),
            'api_dispatch': run.get('api_dispatch'),
            'semantic_suggestion': bool(run['acceptance'].get('suggestion_grounded') or run['acceptance'].get('verification') and not run['acceptance'].get('passed')),
            'error_code': run.get('error_code') or run['acceptance'].get('error_code'),
            'error': run.get('error'), 'stages': run['stages'],
            'isolation_complete': all(not c['target'].startswith('native:') or
                (c.get('isolation', {}).get('policy') == 'isolated_text_v1' and
                 c['isolation'].get('complete') and c['isolation'].get('tool_items') == 0) for c in run['calls']),
            'calls': [{k: c.get(k) for k in ('purpose', 'target', 'model', 'location', 'status', 'usage',
                                           'duration_ms', 'error_code', 'estimated_input_tokens', 'max_output_tokens',
                                           'isolation', 'usage_source', 'cached_input_tokens')} for c in run['calls']]}


def scenario_summary(rows: list[dict], expected_cases: int, *, min_cases=50, max_accuracy_drop=.02,
                     min_saving=.05, min_accuracy=.9, min_category_cases=10) -> tuple[dict, dict]:
    positives = [r for r in rows if not r.get('guard')]
    guards = [r for r in rows if r.get('guard')]
    totals = {}
    for arm in ARMS:
        values = [r['arms'][arm] for r in rows if arm in r['arms']]
        complete = bool(values) and all(v['usage_complete'] for v in values)
        roles = {}
        for role in sorted({key for v in values for key in v['by_role']}):
            group = [v['by_role'][role] for v in values if role in v['by_role']]
            known = all(v['reported_input_tokens'] is not None and v['reported_output_tokens'] is not None for v in group)
            roles[role] = {'calls': sum(v['calls'] for v in group), 'duration_ms': sum(v['duration_ms'] for v in group),
                           'input_tokens': sum(v['reported_input_tokens'] for v in group) if known else None,
                           'output_tokens': sum(v['reported_output_tokens'] for v in group) if known else None}
        positive_values = [r['arms'][arm] for r in positives if arm in r['arms']]
        totals[arm] = {'attempted': len(values), 'calls': sum(v['model_calls'] for v in values),
            'usage_complete': complete, 'input_tokens': sum(v['usage']['prompt_tokens'] for v in values) if complete else None,
            'total_tokens': sum(v['usage']['total_tokens'] for v in values) if complete else None,
            'duration_ms': sum(v['duration_ms'] for v in values), 'by_role': roles,
            'positive_correct': sum(v['correct'] for v in positive_values),
            'accepted_correct': sum(v['accepted_correct'] for v in positive_values),
            'false_accepts': sum(v['status'] == 'completed' and not v['correct'] for v in positive_values) if arm != 'raw' else None,
            'false_rejects': sum(v['correct'] and v['status'] != 'completed' for v in positive_values),
            'correct_but_blocked': dict(Counter(
                'execution_failure' if v['status'] in {'failed', 'cancelled', 'interrupted'} else
                'invalid_review' if v.get('acceptance', {}).get('status') == 'invalid' else
                'rejected_review' for v in positive_values if v['correct'] and v['status'] != 'completed')),
            'repair_improved': sum(v['repair_improved'] for v in values), 'repair_degraded': sum(v['repair_degraded'] for v in values),
            'reference_disagreements': sum(v.get('reference_disagreement') is True for v in values),
            'reference_comparisons': sum(v.get('reference_disagreement') is not None for v in values),
            'reference_errors': dict(Counter(v['acceptance']['reference_error'] for v in values
                                            if v.get('acceptance', {}).get('reference_error'))),
            'format_normalizations': sum(v.get('format_normalized', False) for v in values),
            'semantic_suggestions': sum(v.get('semantic_suggestion', False) for v in values),
            'retained_original': sum(v.get('selection', {}).get('candidate_index') == 0 for v in values if v.get('selection')),
            'selection_records': sum(bool(v.get('selection')) for v in values),
            'guard_passed': sum(r['arms'][arm]['correct'] for r in guards if arm in r['arms']),
            'errors': dict(Counter(v['error_code'] for v in values if v.get('error_code')))}
    comparisons, gates = {}, {}
    for baseline in ARMS[:2]:
        pairs = []
        for row in positives:
            arms = row['arms']
            item = {k: row[k] for k in ('name', 'category', 'source_kind')}
            processed = arms.get('collaboration', {})
            item.update(processed_retains_facts=processed.get('retains_facts', True),
                        compression_applied=processed.get('compression_applied', False))
            if baseline in arms and 'collaboration' in arms:
                raw = arms[baseline]
                item.update(raw_answer_pass=raw['accepted_correct'], processed_answer_pass=processed['accepted_correct'],
                            raw_usage=raw['usage'], processed_usage=processed['usage'],
                            raw_latency_ms=raw['duration_ms'], processed_latency_ms=processed['duration_ms'])
            pairs.append(item)
        summary, gate = summarize(pairs, True, min_cases, max_accuracy_drop, min_saving, min_accuracy, min_category_cases)
        if summary['reported_total_token_saving_ratio'] is not None and summary['reported_total_token_saving_ratio'] < min_saving:
            gate['status'] = 'failed'; gate['reasons'].append('完整输入加输出 Token 节省未达到阈值')
        failed = any(r['arms'].get('collaboration', {}).get('status') not in {None, 'completed'} for r in positives)
        if failed:
            gate['status'] = 'failed'; gate['reasons'].append('存在失败或未通过验收的正向任务')
        if any(baseline not in r['arms'] or 'collaboration' not in r['arms'] or
               not r['arms'][baseline]['usage_complete'] or not r['arms']['collaboration']['usage_complete'] for r in positives):
            if gate['status'] == 'passed': gate['status'] = 'not_evaluated'
            gate['reasons'].append('部分对照缺少完整用量或尚未完成')
        summary['all_attempt_token_saving_ratio'] = (round(1 - totals['collaboration']['total_tokens'] / totals[baseline]['total_tokens'], 4)
            if totals[baseline]['total_tokens'] and totals['collaboration']['total_tokens'] is not None else None)
        comparisons[baseline], gates[baseline] = summary, gate
    incomplete = len(rows) < expected_cases or any(len(r['arms']) != 3 for r in rows)
    guard_failure = any(not r['arms']['collaboration']['correct'] for r in guards if 'collaboration' in r['arms'])
    reasons = [f'{key}: ' + reason for key, gate in gates.items() for reason in gate['reasons']]
    if incomplete: reasons.append('三种对照尚未全部运行完毕')
    if guard_failure: reasons.append('独立拒答集合存在未通过项')
    isolation_failure = any(not value.get('isolation_complete', not any(
        c.get('target', '').startswith('native:') for c in value.get('calls', [])))
        for row in rows for value in row['arms'].values())
    if isolation_failure: reasons.append('原生调用缺少完整纯文本隔离审计，不能作为可信质量或收益证据')
    status = ('failed' if guard_failure or any(g['status'] == 'failed' for g in gates.values()) else
              'not_evaluated' if incomplete or isolation_failure or any(g['status'] != 'passed' for g in gates.values()) else 'passed')
    return {'cases': len(rows), 'positive_cases': len(positives), 'guard_cases': len(guards), 'arms': totals,
            'comparisons': comparisons}, {'status': status, 'reasons': reasons, 'baselines': gates,
            'scope': 'provided_cases_only', 'representative_quality_proven': False,
            'note': '拒答集合单列，不放宽正向门槛；未知 Harness 用量不记为零；Token 差值不是费用差值。'}


def evaluate_scenarios(cases: list[dict], store: RuntimeConfig, edge_connection: str, *, complex_target: str | None = None,
                       edge_model='auto', complex_model='auto', max_calls=6, ollama_context=32768,
                       min_cases=50, max_accuracy_drop=.02, min_saving=.05, min_accuracy=.9,
                       min_category_cases=10, progress=None) -> dict:
    if not cases or not 3 <= max_calls <= 8 or not 2048 <= ollama_context <= 131072:
        raise ValueError('需要非空样本、3–8 次调用预算和有效上下文容量')
    if min_cases < 1 or min_category_cases < 1 or not all(0 <= v <= 1 for v in (min_saving, min_accuracy, max_accuracy_drop)):
        raise ValueError('评测门槛格式无效')
    frozen = store.freeze()
    edge = frozen.profile(edge_connection, True)
    if not edge['local']: raise ValueError('端侧连接必须登记为本机直接推理')
    target = complex_target or frozen.settings()['roles']['executor']
    if target.startswith('native:') and target != 'native:codex':
        raise ValueError('原文评测的原生隔离目前仅支持 Codex，其他模型请使用 API 连接')
    if not target.startswith('native:'): frozen.profile(target)
    for case in cases:
        if 'guard_expectation' not in case:
            _validate_case(case, True)
        else:
            _validate_case(case, False)
            expectation = case['guard_expectation']
            if not isinstance(expectation, dict) or not expectation or set(expectation) - {'statuses', 'answer', 'issue_contains'}:
                raise ValueError('拒答集合需要明确的状态、答案或问题标注')
            if not any(key in expectation for key in ('answer', 'issue_contains')):
                raise ValueError('拒答不能只标状态，还需要明确的答案或问题依据')
            if 'statuses' in expectation and (not isinstance(expectation['statuses'], list) or not expectation['statuses'] or
                    any(s not in {'completed', 'needs_review'} for s in expectation['statuses'])):
                raise ValueError('拒答状态仅支持 completed/needs_review，不能把执行错误当作拒答')
            for key in ('answer', 'issue_contains'):
                if key in expectation and (not isinstance(expectation[key], list) or not expectation[key] or
                        any(not isinstance(v, str) or not v.strip() for v in expectation[key])):
                    raise ValueError('拒答答案和问题须为非空字符串数组')
    rows = []
    report = {'mode': 'scenarios', 'expected_cases': len(cases), 'cases': rows,
              'configuration': {'edge': edge_connection, 'complex': target, 'edge_model': edge_model,
                                'complex_model': complex_model, 'max_calls': max_calls,
                                'edge_available': bool(edge['models']), 'allow_file_harness_steps': False},
              'note': '单次原文、原文加生产验收、真实端云协作；每个分支完成后保存，不改写日常配置。'}
    with tempfile.TemporaryDirectory(prefix='tokenflow-scenarios-') as directory:
        isolated = RuntimeConfig(Path(directory) / 'connections.db')
        profiles = {p['id']: frozen.profile(p['id'], True) for p in frozen.profiles()}
        for profile in profiles.values(): isolated.save_profile(profile)
        settings = frozen.settings()
        dispatch_planner = {k: settings[k]['planner'] for k in ('roles', 'role_models')}
        settings.update(roles={'local': edge_connection, 'planner': target, 'executor': target, 'reviewer': target},
                        role_models={'local': edge_model, 'planner': complex_model, 'executor': complex_model, 'reviewer': complex_model},
                        max_calls=max_calls, collaboration_mode='hybrid', context_window_tokens=ollama_context)
        if settings.get('api_dispatch'):
            for key, value in dispatch_planner.items():
                settings[key]['planner'] = value
            report['configuration']['dispatch_planner'] = dispatch_planner
            report['configuration']['api_dispatch'] = True
            report['note'] = '单次原文、原文加验收、API调度与Harness完整流程；各分支使用固定配置并保留实际调用。'
        isolated.save_settings(settings)
        report['configuration'].update({k: settings[k] for k in ('output_limits', 'max_task_tokens', 'context_window_tokens')})
        fixed = FrozenConfig(isolated.path, profiles, isolated.settings(), frozen.public()['native'])
        documents = DocumentStore(str(Path(directory) / 'documents.db'))
        for index, case in enumerate(cases):
            row = {'name': case.get('name', str(index)), 'category': case.get('category', 'uncategorized'),
                   'source_kind': case.get('source_kind', 'synthetic'), 'guard': 'guard_expectation' in case, 'arms': {},
                   'source_sha256': hashlib.sha256(case['content'].encode()).hexdigest(),
                   'oracle': {k: case[k] for k in ('expected_answer', 'answer_match', 'guard_expectation') if k in case}}
            rows.append(row)
            # Rotate order to reduce fixed warmup/order bias. No parallel inference contention.
            for arm in ARMS[index % 3:] + ARMS[:index % 3]:
                runner = TaskRun(fixed, case['task'], case['content'], document_store=documents,
                                 source_name=row['name'], allow_harnesses=False,
                                 source_warnings=case.get('parsing', {}).get('warnings', []),
                                 evaluation_mode='' if arm == 'collaboration' else arm)
                run = runner.execute()
                row['arms'][arm] = _arm_result(run, case, runner.selected_context)
                report['summary'], report['quality_gate'] = scenario_summary(rows, len(cases), min_cases=min_cases,
                    max_accuracy_drop=max_accuracy_drop, min_saving=min_saving, min_accuracy=min_accuracy, min_category_cases=min_category_cases)
                if progress: progress(report)
    return report


def markdown_report(report: dict) -> str:
    summary = report['summary']
    lines = ['# TokenFlow 三种流程对照', '', report['note'], '', f"门槛：{report['quality_gate']['status']}", '',
             '| 流程 | 已运行 | 调用 | 输入 Token | 总 Token | 毫秒 | 答案正确 | 正确且完成 | 拒答通过 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for arm, value in summary['arms'].items():
        lines.append('| ' + ' | '.join(str(v) if v is not None else '未知' for v in
            (arm, value['attempted'], value['calls'], value['input_tokens'], value['total_tokens'], value['duration_ms'],
             value['positive_correct'], value['accepted_correct'], value['guard_passed'])) + ' |')
    lines += ['', '| 流程 | 错误放行 | 正确但未完成 | 阻塞原因 | 修正改善 / 退化 |', '|---|---:|---:|---|---|']
    for arm, value in summary['arms'].items():
        lines.append(f"| {arm} | {value['false_accepts'] if value['false_accepts'] is not None else '未验收'} | "
                     f"{value['false_rejects']} | {json.dumps(value['correct_but_blocked'], ensure_ascii=False)} | "
                     f"{value['repair_improved']} / {value['repair_degraded']} |")
    lines += ['', '正向和拒答分开统计；包含失败调用用量；无完整用量时不计算收益。', '']
    lines += ['- ' + reason for reason in report['quality_gate']['reasons']]
    return '\n'.join(lines) + '\n'
