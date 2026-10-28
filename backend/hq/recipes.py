"""HQ recipes: whole parametric assets in one call, from assets/recipes/<name>.json.

A recipe is {"about", "params": {name: default}, "let": {name: "=expr"}, "steps": [forge_batch steps]}.
In steps, "$name" (the whole string) is a param/let value and "=a*b+2" is arithmetic over them; "$0" and
"$0.ids" stay batch step refs. A step with "if": "<param>" runs only when that param is set and not "none":
keep those last, so the step numbers before them do not move.
"""
import ast
import difflib
import json
import operator
import re
from functools import lru_cache
from pathlib import Path

from . import op

_DIR = Path(__file__).resolve().parents[2] / "assets" / "recipes"
_MATH = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_REF = re.compile(r"\$([A-Za-z_]\w*)\Z")


@lru_cache(maxsize=1)
def recipes():
    return {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(_DIR.glob("*.json"))}


def _calc(node, env):
    if isinstance(node, ast.Expression):
        return _calc(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and isinstance(env.get(node.id), (int, float)):
        return env[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _MATH:
        return _MATH[type(node.op)](_calc(node.left, env), _calc(node.right, env))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_calc(node.operand, env)
    raise ValueError("only numbers, + - * / and numeric params")


def _fill(value, env):
    if isinstance(value, dict):
        return {k: _fill(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, env) for v in value]
    if isinstance(value, str):
        if value.startswith("="):
            try:
                return round(float(_calc(ast.parse(value[1:], mode="eval"), env)), 3)
            except (SyntaxError, ValueError, ZeroDivisionError) as exc:
                raise ValueError("bad expression %r: %s" % (value, exc)) from None
        ref = _REF.match(value)
        if ref and ref.group(1) in env:
            return env[ref.group(1)]
    return value


def _near(name, options, what):
    close = difflib.get_close_matches(str(name), options, n=1)
    return "No %s %r.%s %ss: %s" % (what, name, " Did you mean %r?" % close[0] if close else "", what.capitalize(), ", ".join(options))


def expand(name, given=None):
    """The recipe's forge_batch steps with params filled in. Raises with the valid names on a mistake."""
    found = recipes()
    if name not in found:
        raise ValueError(_near(name, list(found), "recipe"))
    spec = found[name]
    env = dict(spec.get("params") or {})
    for key, value in (given or {}).items():
        if key not in env:
            raise ValueError(_near(key, list(env), "param") + " (recipe %s)" % name)
        if isinstance(env[key], (int, float)) and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("%s must be a number (default %s), got %r" % (key, env[key], value))
        env[key] = value
    for key, expr in (spec.get("let") or {}).items():
        env[key] = _fill(expr, env)
    steps = []
    for step in spec["steps"]:
        step = dict(step)
        cond = step.pop("if", None)
        if cond is not None and str(env.get(cond) or "none") == "none":
            continue
        steps.append(_fill(step, env))
    return steps


@op("recipe", "recipe(recipe, params?) whole asset in one undo step, e.g. params={recipe:'humanoid_base', height:180, hair:'ponytail'}. "
              "Recipes: %s. see skill forge/recipes" % " ".join(recipes()))
def recipe(scene, p):
    name = str(p.get("recipe") or "")
    if not name:
        return {"summary": "; ".join("%s(%s)" % (k, ", ".join(v.get("params") or {})) for k, v in recipes().items())[:1400]}
    given = dict(p.get("params") or {})
    given.update({k: v for k, v in p.items() if k not in {"recipe", "params", "id", "wait", "verbose"}})
    from ..scene import _op_batch
    out = _op_batch(scene, {"steps": expand(name, given)})
    out["summary"] = "%s: %s" % (name, out["summary"])
    return out
