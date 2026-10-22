"""Independent synthetic verification cases; labels use rules, never model answers."""
from __future__ import annotations

import argparse
import itertools
import json
import random
from pathlib import Path


def generate_cases(seed: int) -> list[dict]:
    rng = random.Random(f'verification:4:{seed}')
    cases = []
    for category in ('ledger_revisions', 'unit_conversion', 'dependency_timing',
                     'cross_document_join', 'threshold_policy', 'budget_selection'):
        for index in range(10):
            method = 'exact'
            if category == 'ledger_revisions':
                rule = '每个流水号仅采用已签署记录中版本号最大的记录；该记录若为撤销则不计入。未签署记录一律忽略。'
                rows, total = [], 0
                for n in range(1, 9):
                    old, current = rng.randint(-80, 120), rng.randint(-80, 120)
                    state = '撤销' if n % 3 == index % 3 else '生效'
                    rows += [f'T{n},1,已签署,生效,{old}', f'T{n},2,已签署,{state},{current}', f'T{n},3,未签署,生效,999']
                    total += current if state == '生效' else 0
                rng.shuffle(rows)
                source = rule + '\n流水号,版本,签署,状态,金额\n' + '\n'.join(rows)
                task = '依据流水规则核对各条有效版本，并计算净金额。只回答最终数字。'
                answer = str(total)
            elif category == 'unit_conversion':
                rule = '仅纳入状态为已确认的记录；1千克=1000克。included 按编号升序。'
                rows, grams, ids = [], 0, []
                for n in range(1, 9):
                    value = rng.randint(1, 30)
                    unit = '千克' if n % 2 else '克'
                    amount = f'{value // 4}.{(value % 4)*25:02d}' if unit == '千克' else str(value * 10)
                    status = '撤销' if n % 3 == index % 3 else '已确认'
                    rows.append(f'W{n},{status},{amount},{unit}')
                    if status == '已确认':
                        grams += value * (250 if unit == '千克' else 10)
                        ids.append(f'W{n}')
                source = rule + '\n编号,状态,重量,单位\n' + '\n'.join(rows)
                task = '提取已确认重量并统一换算为克，核对纳入编号。只输出JSON对象，字段grams为总克数，included为编号数组。'
                answer, method = json.dumps({'grams': grams, 'included': ids}), 'json'
            elif category == 'dependency_timing':
                rule = '所有任务时间单位为分钟，可无限并行，必须等待全部前置任务结束后开始；从0时刻起，计算全部任务最早完成时间。'
                rows, finishes = [], []
                for n in range(7):
                    duration = rng.randint(3, 18)
                    dependencies = sorted(rng.sample(range(n), min(n, rng.randint(0, 3))))
                    finishes.append(duration + max((finishes[d] for d in dependencies), default=0))
                    rows.append(f'{chr(65+n)},{duration},' + ('+'.join(chr(65+d) for d in dependencies) or '无'))
                source = rule + '\n任务,耗时,前置任务\n' + '\n'.join(rows)
                task = '依据全部依赖和并行规则计算项目最早完成时间。只回答最终数字。'
                answer = str(max(finishes))
            elif category == 'cross_document_join':
                rule = '仅结算已批准订单，别名必须先映射到商品编码，再按商品单价计费；orders 按订单编号升序。草稿价格不生效。'
                prices = {f'P{n}': rng.randint(5, 35) for n in range(1, 5)}
                aliases = {f'别名{n}': f'P{(n-1)%4+1}' for n in range(1, 7)}
                rows, amount, ids = [], 0, []
                for n in range(1, 9):
                    alias, quantity = rng.choice(list(aliases)), rng.randint(2, 11)
                    approved = (n + index) % 3 != 0
                    rows.append(f'O{n},{alias},{quantity},' + ('已批准' if approved else '待批准'))
                    if approved:
                        amount += quantity * prices[aliases[alias]]
                        ids.append(f'O{n}')
                source = (rule + '\n[生效单价]\n编码,单价\n' + '\n'.join(f'{k},{v}' for k,v in prices.items()) +
                          '\n[别名映射]\n别名,编码\n' + '\n'.join(f'{k},{v}' for k,v in aliases.items()) +
                          '\n[订单]\n订单,别名,数量,状态\n' + '\n'.join(rows) + '\n[未签署草稿]全部单价999。')
                task = '关联订单、别名与生效价格，计算获批订单总金额并列出结算订单。只输出JSON对象，字段amount为总金额，orders为编号数组。'
                answer, method = json.dumps({'amount': amount, 'orders': ids}), 'json'
            elif category == 'threshold_policy':
                threshold, cap = rng.randint(60, 120), rng.randint(5, 15)
                rule = f'仅已结算订单计费。每单原价为数量乘单价，原价达到{threshold}时减免原价的十分之一向下取整，但每单减免最多{cap}；未达到门槛则无减免。'
                rows, total = [], 0
                for n in range(1, 9):
                    quantity, price = rng.randint(3, 15), rng.randint(4, 20)
                    settled = (n + index) % 4 != 0
                    rows.append(f'B{n},{quantity},{price},' + ('已结算' if settled else '未结算'))
                    gross = quantity * price
                    if settled: total += gross - (min(cap, gross // 10) if gross >= threshold else 0)
                source = rule + '\n编号,数量,单价,状态\n' + '\n'.join(rows)
                task = '按逐单门槛和减免上限计算结算后净收入总额。只回答最终数字。'
                answer = str(total)
            else:
                budget = rng.randint(20, 35)
                rule = f'从项目中选至多3个，总成本不能超过{budget}。优先总收益最大，其次总成本最小，仍相同则选编号数组字典序最小的组合。编号数组须升序。'
                items = [(f'J{n}', rng.randint(5, 16), rng.randint(8, 48)) for n in range(1, 9)]
                options = []
                for size in range(4):
                    for group in itertools.combinations(items, size):
                        spend, benefit = sum(v[1] for v in group), sum(v[2] for v in group)
                        if spend <= budget: options.append((-benefit, spend, [v[0] for v in group]))
                negative, spend, ids = min(options)
                source = rule + '\n编号,成本,收益\n' + '\n'.join(','.join(map(str, row)) for row in items)
                task = '提取项目约束并找出最优组合，核对预算与平局规则。只输出JSON对象，字段selected为编号数组，cost为总成本，benefit为总收益。'
                answer, method = json.dumps({'selected': ids, 'cost': spend, 'benefit': -negative}), 'json'
            cases.append({'name': f'validation-v4-{category}-{index:02d}', 'category': category,
                          'source_kind': 'synthetic', 'template_version': 4, 'seed': seed,
                          'task': task, 'content': source, 'must_keep': source.splitlines(),
                          'expected_answer': answer, 'answer_match': method,
                          'label_status': 'rule_generated', 'note': 'Independent rules; not human-labelled real tasks.'})
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=20261022)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    cases = generate_cases(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'validation.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output_dir / 'generation.json').write_text(json.dumps({'cases':len(cases), 'seed':args.seed, 'template_version':4,
        'source_kind':'synthetic', 'model_calls':0, 'note':'New structures, six categories; not proof of real-task generalization.'}, indent=2), encoding='utf-8')
    print(f'Generated {len(cases)} cases without model calls.')


if __name__ == '__main__':
    main()
