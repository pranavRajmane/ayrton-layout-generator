"""Safe evaluation of the empirical formulas kept in presets (e.g. pour time).

Only numbers, the named variables, + - * / ** and a few math functions are
allowed; anything else (attribute access, calls to other names, comprehensions)
is refused before evaluation.
"""
from __future__ import annotations

import ast
import math

FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp, "pow": pow,
         "min": min, "max": max, "abs": abs, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x)}
_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)


class FormulaError(ValueError):
    pass


def _check(node, names):
    if isinstance(node, ast.Expression):
        return _check(node.body, names)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return
    if isinstance(node, ast.Name):
        if node.id not in names and node.id not in FUNCS:
            raise FormulaError(f"unknown name '{node.id}' (allowed: {sorted(names)} and {sorted(FUNCS)})")
        return
    if isinstance(node, ast.BinOp) and isinstance(node.op, _OPS):
        _check(node.left, names)
        _check(node.right, names)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _OPS):
        return _check(node.operand, names)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS and not node.keywords:
        for a in node.args:
            _check(a, names)
        return
    raise FormulaError(f"not allowed in a formula: {ast.dump(node)[:60]}")


def evaluate(expr: str, **variables: float) -> float:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"cannot read formula '{expr}': {e.msg}") from None
    _check(tree, set(variables))
    try:
        v = eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}, {**FUNCS, **variables})   # noqa: S307
    except (ArithmeticError, ValueError, TypeError) as e:
        raise FormulaError(f"formula '{expr}' failed: {e}") from None
    if not isinstance(v, (int, float)) or not math.isfinite(v):
        raise FormulaError(f"formula '{expr}' gave {v!r}")
    return float(v)
