"""Reference binding and content-preserving normalization boundaries."""
import copy
from answer_checks import reference_check, normalize_format, strict_json, same_json, structured_kind, exact_arithmetic_context
from task_orchestrator import reference_schema, REFERENCE_SCHEMA


def reference(answer='42', expression='40+2', quote='数量40，补充2'):
    return {'reference_answer': answer, 'reason': '按原文计算', 'source_quotes': [quote],
            'calculations': [{'path': '', 'expression': expression}]}


def self_check():
    task, source = '数量是多少？只回答数字。', '数量40，补充2'
    value = reference_check(task, '42', source, reference())
    assert value['passed'] and value['verification']['semantic_truth_proven'] is False
    assert reference_check(task, '4.2e1', source, reference())['passed']
    schema = reference_schema(task, source)
    assert list(schema['properties']) == ['source_quotes', 'calculations', 'reason', 'reference_answer']
    assert schema['properties']['reference_answer']['minLength'] == 1
    assert schema['properties']['calculations']['maxItems'] == 1
    assert schema['properties']['calculations']['items']['properties']['path']['enum'] == ['']
    assert reference_schema('只输出JSON。', source)['properties']['calculations']['maxItems'] == 4
    assert 'minItems' not in REFERENCE_SCHEMA['properties']['calculations']
    for modified, error in (({**reference(), 'reference_answer': ' '}, 'invalid reference answer'),
                            ({**reference(), 'reason': ''}, 'invalid reference reason'),
                            ({**reference(), 'calculations': reference()['calculations'] * 2}, 'duplicate calculation path')):
        try: reference_check(task, '42', source, modified)
        except ValueError as exc: assert str(exc) == error
        else: raise AssertionError(error)
    boxes = '西仓登记77件，其中锁定12件。每箱装2件，只统计完整箱。'
    rounded = reference('32', quote=boxes); rounded['calculations'] = []
    checked = reference_check('计算完整箱数。只回答数字。', '32', boxes, rounded)
    assert checked['passed'] and checked['verification']['arithmetic_mode'] == 'not_applicable'
    assert reference_schema(task, boxes)['properties']['calculations']['maxItems'] == 0
    assert not exact_arithmetic_context('只计算完整份数。只回答数字。', '材料共有7份，每组2份')
    assert exact_arithmetic_context(task, '完整资料：数量40，补充2')
    assert not reference_check(task, '32.5', boxes, rounded)['passed']
    bad_rounded = {**rounded, 'calculations': [{'path': '', 'expression': '(77-12)/2'}]}
    try: reference_check(task, '32', boxes, bad_rounded)
    except ValueError as exc: assert str(exc) == 'unsupported calculation'
    else: raise AssertionError('Rounding must not use rational division proof')
    mismatch = reference_check(task, '38', source, reference('31', '30+1'))
    assert not mismatch['passed'] and not mismatch['repairable'] and mismatch['reference_answer'] == '31'
    data = 'W2,70克\nW3,6.25千克\nW5,1750克\nW6,10克\nW8,280克'
    proof = {'reference_answer':'{"grams":8360,"included":["W2","W3","W5","W6","W8"]}',
             'reason':'换算后合计8360克', 'source_quotes':['W3,6.25千克'],
             'calculations':[{'path':'/grams','expression':'70+6250+1750+10+280'}]}
    checked = reference_check('只输出JSON对象。', proof['reference_answer'], data, proof)
    assert checked['passed']
    assert not reference_check('只输出JSON对象。', proof['reference_answer'].replace('8360','8310'), data, proof)['passed']
    bad = copy.deepcopy(proof); bad['reference_answer'] = bad['reference_answer'].replace('8360','8310')
    for payload in (bad, {**proof,'source_quotes':['不存在']}, {**proof,'calculations':[]},
                    {**proof,'calculations':proof['calculations']*2}, {**proof,'extra':True},
                    {**proof,'calculations':[{'path':'/missing','expression':'8360'}]},
                    {**proof,'calculations':[{'path':'/grams','expression':'__import__("os")'}]}):
        try: reference_check('只输出JSON对象。', proof['reference_answer'], data, payload)
        except (ValueError, SyntaxError): pass
        else: raise AssertionError('Invalid binding must not pass')
    escaped = {'reference_answer':'{"a/b":{"~x":[true,3]}}','reason':'字段核对', 'source_quotes':[source],
               'calculations':[{'path':'/a~1b/~0x/1','expression':'1+2'}]}
    assert reference_check('只输出JSON。', escaped['reference_answer'], source, escaped)['passed']
    floating = reference('0.30000000000000004', quote='0.1+0.2'); floating['calculations'] = []
    assert reference_check('JavaScript值是多少？只回答数字。', '0.30000000000000004', '0.1+0.2', floating)['passed']
    rounding = reference('42', quote='减免向下取整，结算42'); rounding['calculations'] = []
    assert reference_check(task, '42', '减免向下取整，结算42', rounding)['passed']
    approximate = reference('0.33',quote='1/3');approximate['calculations']=[]
    assert reference_check('近似到两位小数，只回答数字。','0.33','1/3',approximate)['passed']
    assert normalize_format(task, '**42**') == '42'
    assert normalize_format(task, '最终结果：42。') == '42'
    assert normalize_format(task, '```text\n-42\n```') == '-42'
    for answer in ('2+40=42', '结果42，另一个值31', '42%', '42千克', 'NaN', '结果1e9999'):
        assert normalize_format(task, answer) is None, answer
    assert normalize_format('只输出JSON。','```json\n{"a":42}\n```') == '{"a":42}'
    assert normalize_format('只输出JSON。','```json\n{"a":1,"a":2}\n```') is None
    assert not structured_kind('解释JSON。') and not structured_kind('不要只回答数字')
    assert same_json(strict_json('{"a":1.0}'), strict_json('{"a":1}'))
    assert not same_json(strict_json('{"a":true}'), strict_json('{"a":1}'))
    for invalid in ('{"a":1,"a":2}', 'NaN', '1e99999', '['*70+'0'+']'*70):
        try: strict_json(invalid)
        except ValueError: pass
        else: raise AssertionError('Strict JSON boundary')
    print('Independent reference and safe format checks: PASS')


if __name__ == '__main__':
    self_check()
