"""Independent reference calculations for the new synthetic labels."""
import csv
import io
import json
from decimal import Decimal, ROUND_FLOOR

from scripts.generate_verification_cases import generate_cases


def self_check(seed=20261022):
    cases = generate_cases(seed)
    assert len(cases) == 60 and cases == generate_cases(seed)
    assert len({c['category'] for c in cases}) == 6
    for case in cases:
        text, category = case['content'], case['category']
        if category == 'cross_document_join':
            prices_text, rest = text.split('[生效单价]\n')[1].split('[别名映射]\n')
            aliases_text, orders_text = rest.split('[订单]\n')
            prices = {row['编码']:int(row['单价']) for row in csv.DictReader(io.StringIO(prices_text))}
            aliases = {row['别名']:row['编码'] for row in csv.DictReader(io.StringIO(aliases_text))}
            orders = [r for r in csv.DictReader(io.StringIO(orders_text.split('[未签署草稿]')[0])) if r['状态']=='已批准']
            expected = {'amount':sum(int(r['数量'])*prices[aliases[r['别名']]] for r in orders), 'orders':sorted(r['订单'] for r in orders)}
        else:
            rows = list(csv.DictReader(io.StringIO('\n'.join(text.splitlines()[1:]))))
            if category == 'ledger_revisions':
                current = {}
                for row in sorted(rows, key=lambda r:int(r['版本'])):
                    if row['签署']=='已签署': current[row['流水号']] = row
                expected = str(sum(int(r['金额']) for r in current.values() if r['状态']!='撤销'))
            elif category == 'unit_conversion':
                included = [r for r in rows if r['状态']=='已确认']
                expected = {'grams':int(sum(Decimal(r['重量'])*(1000 if r['单位']=='千克' else 1) for r in included)),
                            'included':sorted(r['编号'] for r in included)}
            elif category == 'dependency_timing':
                done, active, started, minute = set(), {}, set(), 0
                while len(done) < len(rows):
                    for row in rows:
                        dependencies = set() if row['前置任务']=='无' else set(row['前置任务'].split('+'))
                        if row['任务'] not in started and dependencies <= done:
                            active[row['任务']] = minute + int(row['耗时']); started.add(row['任务'])
                    minute = min(active.values())
                    done.update(k for k,v in active.items() if v==minute)
                    active = {k:v for k,v in active.items() if k not in done}
                expected = str(minute)
            elif category == 'threshold_policy':
                import re
                threshold = int(re.search(r'达到(\d+)', text).group(1))
                cap = int(re.search(r'最多(\d+)', text).group(1))
                total = Decimal(0)
                for row in rows:
                    if row['状态']!='已结算': continue
                    gross = Decimal(row['数量'])*Decimal(row['单价'])
                    total += gross - (min(Decimal(cap),(gross*Decimal('.1')).to_integral_value(rounding=ROUND_FLOOR)) if gross>=threshold else 0)
                expected = str(total)
            else:
                import re
                budget = int(re.search(r'不能超过(\d+)', text).group(1))
                options = []
                for mask in range(1 << len(rows)):
                    group = [row for i,row in enumerate(rows) if mask & (1 << i)]
                    cost, benefit = sum(int(r['成本']) for r in group), sum(int(r['收益']) for r in group)
                    if len(group)<=3 and cost<=budget: options.append((-benefit,cost,sorted(r['编号'] for r in group)))
                negative, cost, selected = sorted(options)[0]
                expected = {'selected':selected,'cost':cost,'benefit':-negative}
        actual = json.loads(case['expected_answer']) if case['answer_match']=='json' else case['expected_answer']
        assert actual == expected, case['name']
        assert all(fact in text for fact in case['must_keep'])
    print('Independent V4 label checks: PASS')


if __name__ == '__main__':
    self_check()
