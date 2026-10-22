"""Conservative output contracts and exact arithmetic checks; never execute text."""
from __future__ import annotations

import ast
import json
import operator
import re
from decimal import Decimal
from fractions import Fraction


REFERENCE_ERRORS = {
    'invalid reference fields': '参考字段缺失或包含额外字段',
    'invalid reference answer': '参考答案为空或长度不合规',
    'invalid reference reason': '参考说明为空或长度不合规',
    'reference quote not grounded': '参考引文未在原资料中找到或格式不合规',
    'invalid calculations': '计算项数量或类型不合规',
    'invalid calculation fields': '计算项字段不完整',
    'duplicate calculation path': '多个计算项重复绑定同一最终字段',
    'unsupported calculation': '该任务语义或算式不支持精确四则验算',
    'invalid answer path': '最终字段路径格式不合规',
    'invalid path escape': '最终字段路径转义不合规',
    'answer path not found': '计算项指向不存在的最终字段',
    'calculation contradicts reference field': '算式结果与对应参考字段不一致',
    'numeric fields require bound calculations': '部分数值字段缺少唯一绑定算式',
    'inconsistent reference arithmetic': '参考说明中的算术等式不成立',
    'not a bounded number': '参考答案不是单一有效数值',
    'number outside limit': '参考数值超出支持范围',
}


def strict_json(text: str):
    if len(text) > 200000:
        raise ValueError('JSON answer too large')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate key')
            result[key] = value
        return result
    def number(value):
        try:
            parsed = Decimal(value)
        except ArithmeticError as exc:
            raise ValueError('invalid decimal') from exc
        if not parsed.is_finite() or abs(parsed.as_tuple().exponent) > 10000:
            raise ValueError('decimal exponent outside limit')
        return parsed
    def invalid_constant(value):
        raise ValueError('non-finite number')
    result = json.loads(text, parse_float=number, parse_constant=invalid_constant, object_pairs_hook=pairs)
    pending = [(result, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 64: raise ValueError('JSON nesting outside limit')
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        pending.extend((child, depth + 1) for child in children)
    return result


def same_json(actual, expected, depth=0) -> bool:
    if depth > 64: return False
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(same_json(actual[k], expected[k], depth + 1) for k in actual)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(same_json(a, b, depth + 1) for a, b in zip(actual, expected))
    if type(actual) in (int, Decimal) and type(expected) in (int, Decimal):
        return actual == expected
    return type(actual) is type(expected) and actual == expected


def structured_kind(task: str) -> str:
    if numeric_only(task): return 'number'
    for clause in re.split(r'[。！？!?；;，,\n]', task):
        if re.match(r'\s*(?:最终)?(?:请)?(?:只|仅)?(?:输出|返回|回答|生成)\s*(?:一个)?\s*JSON(?![A-Za-z0-9_])', clause, re.I) or re.match(
                r'\s*(?:please\s+)?(?:only\s+)?(?:return|output|respond with)\s+(?:only\s+)?(?:a\s+)?JSON(?![A-Za-z0-9_])', clause, re.I):
            return 'json'
    return ''


def exact_number(text: str) -> Fraction:
    if len(text) > 160 or not re.fullmatch(r'[+\-−]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+\-]?[0-9]+)?', text.strip()):
        raise ValueError('not a bounded number')
    decimal = Decimal(text.strip().replace('−', '-'))
    if not decimal.is_finite() or abs(decimal.as_tuple().exponent) > 256:
        raise ValueError('number outside limit')
    value = Fraction(decimal)
    if value.numerator.bit_length() > 256 or value.denominator.bit_length() > 256:
        raise ValueError('number outside limit')
    return value


def normalize_format(task: str, answer: str) -> str | None:
    """Only remove recognized wrappers; never extract a guessed answer."""
    kind, value = structured_kind(task), answer.strip()
    fence = re.fullmatch(r'```(?:json|text)?[ \t]*\r?\n(.*?)\r?\n```', value, re.S | re.I)
    if fence: value = fence[1].strip()
    if kind == 'json':
        if not fence: return None
        try: strict_json(value)
        except (ValueError, RecursionError): return None
    elif kind == 'number':
        if value.startswith('**') and value.endswith('**'): value = value[2:-2].strip()
        value = re.sub(r'^(?:最终答案|最终结果|答案|结果)\s*(?:为|是|[:：])?\s*', '', value)
        value = value.rstrip('。')
        try: exact_number(value)
        except (ValueError, ArithmeticError): return None
    else:
        return None
    return value if value != answer.strip() else None


def _pointer(value, path: str):
    if not isinstance(path, str) or len(path) > 256 or path and not path.startswith('/'):
        raise ValueError('invalid answer path')
    for token in path.split('/')[1:] if path else []:
        if re.search(r'~(?![01])', token): raise ValueError('invalid path escape')
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r'0|[1-9][0-9]*', token) and int(token) < len(value):
            value = value[int(token)]
        else:
            raise ValueError('answer path not found')
    return value


def reference_check(task: str, answer: str, source: str, reference: dict) -> dict:
    """Check a blind model reference's consistency, not its semantic truth."""
    kind = structured_kind(task)
    if not kind or not isinstance(reference, dict) or set(reference) != {'reference_answer', 'reason', 'source_quotes', 'calculations'}:
        raise ValueError('invalid reference fields')
    expected, reason = reference['reference_answer'], reference['reason']
    quotes, calculations = reference['source_quotes'], reference['calculations']
    if not isinstance(expected, str) or not 1 <= len(expected.strip()) <= 20000:
        raise ValueError('invalid reference answer')
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 160:
        raise ValueError('invalid reference reason')
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= 4 or any(
            not isinstance(q, str) or not 1 <= len(q.strip()) <= 160 or q not in source for q in quotes):
        raise ValueError('reference quote not grounded')
    if not isinstance(calculations, list) or len(calculations) > 4:
        raise ValueError('invalid calculations')
    value = exact_number(expected) if kind == 'number' else strict_json(expected)
    exact_math = exact_arithmetic_context(task, source)
    numeric_paths, pending = set(), [('', value)]
    while pending:
        path, item = pending.pop()
        if type(item) in (int, Decimal, Fraction): numeric_paths.add(path)
        elif isinstance(item, dict):
            pending.extend((path+'/'+key.replace('~', '~0').replace('/', '~1'), child) for key, child in item.items())
        elif isinstance(item, list): pending.extend((path+'/'+str(i), child) for i, child in enumerate(item))
    checked = set()
    for calculation in calculations:
        if not isinstance(calculation, dict) or set(calculation) != {'path', 'expression'}:
            raise ValueError('invalid calculation fields')
        path, expression = calculation['path'], calculation['expression']
        if isinstance(path, str) and path in checked:
            raise ValueError('duplicate calculation path')
        if not isinstance(path, str) or not isinstance(expression, str) or not exact_math:
            raise ValueError('unsupported calculation')
        field = _pointer(value, path)
        try:
            calculated = _calculate(expression)
        except (ValueError, SyntaxError, ArithmeticError, RecursionError) as exc:
            raise ValueError('unsupported calculation') from exc
        if type(field) not in (int, Decimal, Fraction) or calculated != field:
            raise ValueError('calculation contradicts reference field')
        checked.add(path)
    if exact_math and checked != numeric_paths:
        raise ValueError('numeric fields require bound calculations')
    if exact_math and arithmetic_contradictions(reason):
        raise ValueError('inconsistent reference arithmetic')
    try:
        actual = exact_number(answer) if kind == 'number' else strict_json(answer)
        matches = actual == value if kind == 'number' else same_json(actual, value)
    except (ValueError, ArithmeticError, RecursionError):
        matches = False
    return {'passed': matches, 'status': 'passed' if matches else 'failed', 'method': 'model_review',
            'issues': [] if matches else ['候选与独立参考不一致，保留原答案，请核对差异'],
            'reason': ('独立参考与候选一致；已校验所列计算与字段对应关系' if exact_math else
                       '独立参考与候选一致；该任务不适用精确四则验算') if matches else '独立参考与候选不同，参考未自动采用',
            'reference_reason': reason, 'reference_answer': expected, 'source_quotes': quotes,
            'calculations': calculations, 'repairable': False,
            'verification': {'kind': 'independent_reference', 'arithmetic_mode': 'exact' if exact_math else 'not_applicable',
                             'calculations_checked': len(checked),
                             'source_quotes_checked': len(quotes), 'semantic_truth_proven': False}}


def exact_arithmetic_context(task: str, source: str) -> bool:
    """Programming/rounding semantics need their own verifier, not rational math."""
    if re.search(r'完整(?:箱|包|组|批|份)数|(?:只|仅)(?:统计|计入|计算)完整(?:箱|包|组|批|份)|\bwhole (?:boxes|packs|groups|batches|portions)\b', task + '\n' + source, re.I):
        return False
    if re.search(r"代码|程序|脚本|函数|浮点|精度|四舍五入|取整|舍入|近似|大约|约为|约等于|C\+\+|C#|(?<![A-Za-z])(?:code|program|function|python|javascript|typescript|java|float|ieee|round|approximate|rust)(?![A-Za-z])", task, re.I):
        return False
    return not re.search(r"四舍五入|向下取整|向上取整|舍入|近似|大约|约为|约等于|\b(?:rounding|rounded|approximate)\b|(?:^|\n)\s*(?:def |class |import |from \w+ import |function |const |let |var |[A-Za-z_]\w*\s*=\s*[^\n]*[+*/\-])|```(?:python|javascript|js|typescript|ts)\b", source, re.I)


def numeric_only(task: str) -> bool:
    """Recognize a complete explicit clause, not a quoted/negated substring."""
    if re.search(r"不要(?:只|仅)(?:回答|输出)(?:最终)?(?:数字|数值)|(?:并|同时)(?:请)?(?:解释|说明|给出|输出|回答)", task):
        return False
    clauses = re.split(r"[。！？!?；;，,\n]", task)
    return any(re.fullmatch(r"\s*(?:最终)?(?:请)?(?:只|仅)(?:需)?(?:回答|输出)(?:最终)?(?:数字|数值)\s*", clause)
               or re.fullmatch(r"\s*(?:please\s+)?(?:answer|output)\s+(?:a\s+)?(?:final\s+)?number\s+only[.]?\s*", clause, re.I)
               for clause in clauses)


def numeric_output_issue(task: str, answer: str) -> str | None:
    if numeric_only(task) and not re.fullmatch(r"[+\-−]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+\-]?[0-9]+)?", answer.strip()):
        return "用户明确要求只回答数字；候选含有其他内容或不是单一有限数值。保留计算结果，仅修正输出格式。"
    return None


def _calculate(expression: str) -> Fraction:
    if len(expression) > 160:
        raise ValueError("expression too long")
    tree = ast.parse(expression.strip(), mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 80:
        raise ValueError("expression too large")

    def visit(node, depth=0):
        if depth > 16:
            raise ValueError("expression too deep")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            # Read original decimal spelling, avoiding binary float rounding.
            spelling = ast.get_source_segment(expression.strip(), node)
            if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?|\.[0-9]+", spelling or ""):
                raise ValueError("unsupported number")
            value = Fraction(spelling)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand, depth + 1)
            if isinstance(node.op, ast.USub): value = -value
        elif isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult, ast.Div):
            operation = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}[type(node.op)]
            value = operation(visit(node.left, depth + 1), visit(node.right, depth + 1))
        else:
            raise ValueError("unsupported arithmetic")
        if value.numerator.bit_length() > 256 or value.denominator.bit_length() > 256:
            raise ValueError("number too large")
        return value

    return visit(tree.body)


def arithmetic_contradictions(text: str) -> list[str]:
    """Check explicit unqualified numeric equalities; absence proves nothing.

    Quotes, negation, approximations and rounding are deliberately outside this
    small guard's scope. Variables, calls, powers, units and ellipses don't parse.
    """
    if len(text) > 2000:
        return []
    text = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'|“[^”\n]*”|‘[^’\n]*’|`[^`\n]*`', '', text)
    # An affirmative claim such as "无错误" must not bypass its own arithmetic.
    polarity = re.sub(r"无错误|没有错误|不存在错误|未发现错误|正确无误", "", text)
    if re.search(r"不|错|误|否|假设|若|约|近似|取整|舍入|保留|\b(?:not|false|wrong|incorrect|if|approx|round)\b", polarity, re.I):
        return []
    normalized = text.translate(str.maketrans({"×": "*", "÷": "/", "−": "-"}))
    normalized = re.sub(r"(?<=[0-9)])\s*等于\s*(?=[-+(0-9])", "=", normalized)
    normalized = re.sub(r"(?<=\d)\.(?=\s|$)", "", normalized)
    problems = []
    # Consume a whole chain, so 296 + 35 = 293 cannot hide behind a true prefix.
    pattern = r"(?<![A-Za-z0-9_.<>=!])[-+(0-9][0-9\s.()+*/=\-]*=[0-9\s.()+*/=\-]*[0-9)](?![A-Za-z0-9_.<>=!])"
    for match in re.finditer(pattern, normalized):
        equation = match.group().strip()
        if len(equation) > 320 or not re.search(r"[+*/]|(?<=\d)\s*-", equation):
            continue
        while equation.startswith('(') and equation.endswith(')'):
            balance = 0
            for index, character in enumerate(equation):
                balance += (character == '(') - (character == ')')
                if balance <= 0:
                    break
            if index != len(equation) - 1 or balance != 0:
                break
            equation = equation[1:-1].strip()
        # Don't accept a substring of a variable/function/percentage/formula.
        before, after = normalized[:match.start()], normalized[match.end():]
        suffix = after.lstrip()
        if re.match(r"(?:正确(?:无误)?|无错误|没有错误|不存在错误|未发现错误|(?:is\s+)?correct)(?=$|[\s。；;，,!.！])", suffix, re.I):
            suffix = ''
        prefix_symbol = before[-1:] if before else ''
        if (re.search(r"[A-Za-z0-9_.<>=!%+*/\-]$", before)
                or re.search(r"[,<>=!%+*/&|^~?:√\[\]{}\-]\s*$", before)
                or re.match(r"[A-Za-z0-9_.<>=!%≈+*/,&|^~?:√\[\]{}\-]", suffix)
                or suffix and suffix[0].isalnum()
                or prefix_symbol and prefix_symbol.isalnum() and not prefix_symbol.isascii() and not '\u4e00' <= prefix_symbol <= '\u9fff'
                or match.group().lstrip().startswith('(') and re.search(r"\w\s*$", before)):
            continue
        parts = equation.split("=")
        try:
            values = [_calculate(part) for part in parts]
        except (ValueError, SyntaxError, ArithmeticError, RecursionError):
            continue
        if any(value != values[0] for value in values[1:]):
            problems.append(equation)
        if len(problems) == 3:
            break
    return problems


def numeric_restatement(answer: str, correction: str) -> bool:
    """A complete numeric correction that says the same thing cannot fix it."""
    correction = correction.translate(str.maketrans({"×": "*", "÷": "/", "−": "-"})).strip()
    if not re.fullmatch(r"[0-9\s.()+*/=\-]+", correction):
        return False
    try:
        candidate = _calculate(answer.strip().replace('−', '-'))
        return all(_calculate(part) == candidate for part in correction.split('='))
    except (ValueError, SyntaxError, ArithmeticError, RecursionError):
        return False
