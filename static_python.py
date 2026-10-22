"""Bounded integer/function interpretation. Source is never passed to eval/exec."""
from __future__ import annotations

import ast
import operator
import re


def static_python_answer(task: str, source: str) -> dict | None:
    if not re.search(r"返回值|返回什么|\breturns?\b|return value", task, re.I) or re.search(
            r"解释|修改|创建|保存|运行|执行|测试|然后|以及|并|\b(?:explain|write|run|then|and)\b", task, re.I):
        return None
    if len(source) > 100000 or len(task) > 500:
        return None
    fenced = re.fullmatch(r"\s*```(?:python|py)?\s*\n(.*?)\n```\s*", source, re.S)
    if fenced:
        source = fenced.group(1)
    try:
        tree = ast.parse(source)
        if sum(1 for _ in ast.walk(tree)) > 5000:
            return None
        functions, globals_ = {}, {}
        ticks = 0
        stack = []

        def integer(value):
            if type(value) is not int or value.bit_length() > 256:
                raise ValueError("outside integer subset")
            return value

        def expression(node, local, assigned, depth=0):
            nonlocal ticks
            ticks += 1
            if ticks > 512 or depth > 24:
                raise ValueError("evaluation limit")
            if isinstance(node, ast.Constant):
                return integer(node.value)
            if isinstance(node, ast.Name):
                if node.id in assigned:
                    return local[node.id]  # Do not mistake unbound locals for globals.
                return globals_[node.id]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = expression(node.operand, local, assigned, depth + 1)
                return integer(-value if isinstance(node.op, ast.USub) else value)
            if isinstance(node, ast.BinOp):
                operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                              ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
                op = operations.get(type(node.op))
                if op is None:
                    raise ValueError("unsupported operator")
                return integer(op(expression(node.left, local, assigned, depth + 1),
                                  expression(node.right, local, assigned, depth + 1)))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.args and not node.keywords:
                if node.func.id in assigned:
                    raise ValueError("local value shadows a function")
                return function(node.func.id)
            raise ValueError("unsupported expression")

        def function(name):
            if name in stack or len(stack) >= 12:
                raise ValueError("recursive function")
            fn = functions[name]
            args = fn.args
            if args.args or args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg:
                raise ValueError("only zero-argument functions")
            if any(isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await, ast.NamedExpr)) for n in ast.walk(fn)):
                raise ValueError("unsupported function semantics")
            for statement in fn.body:
                if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                    continue
                if isinstance(statement, ast.Return):
                    continue
                if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                    continue
                raise ValueError("unsupported function body")
            assigned = {target.id for statement in fn.body if isinstance(statement, ast.Assign)
                        for target in statement.targets if isinstance(target, ast.Name)}
            local = {}
            stack.append(name)
            try:
                for statement in fn.body:
                    if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                        local[statement.targets[0].id] = expression(statement.value, local, assigned)
                    elif isinstance(statement, ast.Return):
                        return integer(expression(statement.value, local, assigned))
                    elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                        continue
                    else:
                        raise ValueError("unsupported function body")
            finally:
                stack.pop()
            raise ValueError("no integer return")

        for statement in tree.body:
            if isinstance(statement, ast.FunctionDef):
                args = statement.args
                if (statement.name in functions or statement.name in globals_ or statement.decorator_list or statement.returns
                        or args.defaults or args.kw_defaults and any(v is not None for v in args.kw_defaults)
                        or any(a.annotation for a in args.args + args.posonlyargs + args.kwonlyargs)
                        or args.vararg and args.vararg.annotation or args.kwarg and args.kwarg.annotation
                        or getattr(statement, "type_params", [])):
                    return None
                functions[statement.name] = statement
            elif isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                name = statement.targets[0].id
                if name in globals_ or name in functions:
                    return None
                globals_[name] = expression(statement.value, {}, set())
            elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                continue
            else:
                return None
        targets = [name for name in functions if re.search(r"(?<![A-Za-z_0-9])" + re.escape(name) + r"(?![A-Za-z_0-9])", task)]
        if len(targets) != 1:
            return None
        name = re.escape(targets[0]) + r'\s*(?:\(\))?'
        chinese = (r'(?:请)?(?:计算|给出|查询)?\s*(?:代码中\s*|函数\s*)?' + name +
                   r'\s*(?:的)?(?:返回值\s*(?:是(?:多少|什么))?|返回什么)\s*[？?]?[。.]?\s*'
                   r'(?:(?:请)?(?:只|仅)(?:回答|输出)(?:数字|数值|结果)\s*[。.]?)?')
        english = (r'what\s+(?:is\s+(?:the\s+)?return\s+value\s+of\s+' + name +
                   r'|does\s+' + name + r'\s+return)\s*\??\s*(?:answer\s+(?:a\s+)?(?:number|digits?)\s+only[.]?)?')
        if not re.fullmatch(chinese, task.strip(), re.I) and not re.fullmatch(english, task.strip(), re.I):
            return None
        value = function(targets[0])
        return {"answer": str(value), "function": targets[0], "method": "static_python",
                "scope": "bounded_integer_functions", "evaluated_nodes": ticks,
                "reason": "对原始 Python 的整数赋值、无参函数依赖和算术做有界静态求值；未执行源码"}
    except (ValueError, SyntaxError, KeyError, TypeError, ArithmeticError, RecursionError):
        return None
