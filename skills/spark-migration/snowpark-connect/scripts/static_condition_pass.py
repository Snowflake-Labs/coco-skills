#!/usr/bin/env python3
"""static_condition_pass.py — deterministic, AST/SQL-only condition check.

NO df.explain, NO SCOS session, NO schema. For conditional compat rules
(condition = in_window | distinct_arg | window_no_order_by), it reads the SOURCE
and decides whether the divergence precondition actually holds at each usage,
then downgrades false-positive "verify" hedges in analysis.json to
resolution="safe".

Detection surfaces:
  * Python DataFrame API  -> Python `ast`: is the conditional-function call the
    receiver of an `.over(...)` call (=> in_window)?
  * Embedded/standalone SQL -> sqlglot: is the function inside a Window node
    (=> in_window)? does it carry DISTINCT (=> distinct_arg)?

Safety: clear ONLY on positive proof the condition is unmet (function used
inline in an aggregation/projection, not windowed / not distinct). Variable
indirection, unparseable SQL, or unknown context => leave the hedge.

Deterministic by construction: pure parse + sorted output.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import sqlglot
from sqlglot import expressions as sx

_KB_RULES_PATH = Path(__file__).parent / "data" / "kb_rules.json"
_AGG_METHODS = {"agg", "select", "withcolumn", "withColumn", "filter", "where", "groupby", "groupBy"}

# Conditions this pass can resolve structurally (met/cleared/indeterminate).
_CONDITIONAL = ("in_window", "distinct_arg", "window_no_order_by")


def _load_conditional_fns(kb_rules_path: Optional[str] = None):
    """Build ``{function_name: condition}`` in-memory from ``kb_rules.json`` —
    the single source of truth — instead of a derived, separately-committed
    ``compat_registry.json`` (which could silently drift from the catalog).

    For every catalog rule whose ``condition`` is one this pass resolves
    (``in_window`` / ``distinct_arg``) and whose ``surface`` is ``function``,
    map each of its ``api`` tokens (leaf name, lowercased — matching how
    ``_fn_name`` / the SQL scan render call names) to that condition.
    """
    rules = json.loads(Path(kb_rules_path or _KB_RULES_PATH).read_text())
    out: Dict[str, str] = {}
    for r in rules:
        cond = r.get("condition")
        if cond not in _CONDITIONAL:
            continue
        if r.get("surface") not in (None, "function"):
            continue
        for tok in (r.get("api") or []):
            if tok:
                out.setdefault(tok.lower().rsplit(".", 1)[-1], cond)
    return out


# ---------------- Python AST ----------------
def _fn_name(call: ast.Call) -> Optional[str]:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr.lower()
    if isinstance(f, ast.Name):
        return f.id.lower()
    return None


def _expr_has_attr_call(node: ast.AST, attr_lower: str) -> bool:
    """True if the expression subtree contains an attribute call ``.<attr>(...)``
    (e.g. ``.orderBy`` / ``.partitionBy``), case-insensitive."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr.lower() == attr_lower:
            return True
    return False


def _references_window(node: ast.AST) -> bool:
    """True if the expression references the ``Window`` builder (``Window.``...)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == "Window":
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "Window":
            return True
    return False


def _window_has_order_by(win: Optional[ast.AST], assignments: Dict[str, list]):
    """Decide whether the ``.over(win)`` window carries an ORDER BY.

    Returns ``True`` (has ORDER BY -> no divergence), ``False`` (provably lacks
    ORDER BY -> real finding), or ``None`` (cannot resolve -> indeterminate).

    Handles the common named-variable indirection where the window is defined
    elsewhere, e.g. ``base_window_order = base_window.orderBy(F.col("x"))``.
    """
    if win is None:
        return None
    # Inline window expression: Window.partitionBy(...).orderBy(...)
    if not isinstance(win, ast.Name):
        if _expr_has_attr_call(win, "orderby"):
            return True
        if _references_window(win) or _expr_has_attr_call(win, "partitionby"):
            return False  # a window built without orderBy
        return None
    # Named variable: resolve its assignment(s) in this module.
    vals = assignments.get(win.id)
    if not vals:
        return None
    if any(_expr_has_attr_call(v, "orderby") for v in vals):
        return True
    if any(_references_window(v) or _expr_has_attr_call(v, "partitionby") for v in vals):
        return False
    return None


def _scan_python(src: str, cond: Dict[str, str], rel: str, assignment_src: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node  # type: ignore

    # nodes inside an `.over(...)` receiver subtree are in-window
    in_window_ids = set()
    # map id(windowed function call) -> the window argument expr of `.over(win)`
    over_window: Dict[int, Any] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "over":
            for sub in ast.walk(n.func.value):
                in_window_ids.add(id(sub))
            # The windowed function call is the receiver of `.over(...)`.
            over_window[id(n.func.value)] = n.args[0] if n.args else None

    # module-level variable assignments (name -> [value exprs]) so a windowed
    # function using a named Window variable (``.over(base_window_order)``) can
    # be resolved to whether that window was defined with an ORDER BY. When the
    # window is defined in a different block/scope than the usage, the caller
    # passes ``assignment_src`` (the whole file) so the definition still
    # resolves; otherwise assignments are gathered from the block itself.
    assignments: Dict[str, list] = {}
    assign_tree = tree
    if assignment_src is not None:
        try:
            assign_tree = ast.parse(assignment_src)
        except SyntaxError:
            assign_tree = tree
    for n in ast.walk(assign_tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    assignments.setdefault(t.id, []).append(n.value)

    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = _fn_name(n)
        if name not in cond:
            continue
        condition = cond[name]
        in_window = id(n) in in_window_ids
        # is it inlined inside an aggregation/projection call (not bound to a var)?
        inline_agg = False
        p = getattr(n, "parent", None)
        depth = 0
        while p is not None and depth < 8:
            if isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute) and p.func.attr in _AGG_METHODS:
                inline_agg = True
                break
            if isinstance(p, (ast.Assign, ast.FunctionDef)):
                break  # bound to a var / function boundary -> stop, treat as indeterminate
            p = getattr(p, "parent", None)
            depth += 1

        if condition == "in_window":
            if in_window:
                met = True
            elif inline_agg:
                met = False          # provably used outside a window
            else:
                met = None           # indeterminate (variable indirection) -> don't clear
        elif condition == "window_no_order_by":
            # The "windowed function requires an ORDER BY" divergence only
            # applies when the call is actually windowed AND that window lacks
            # an ORDER BY. If the window provably HAS an ORDER BY, this is a
            # false positive and is cleared.
            if not in_window:
                met = False          # not windowed here -> divergence cannot apply
            else:
                has_ob = _window_has_order_by(over_window.get(id(n)), assignments)
                if has_ob is True:
                    met = False      # window HAS order by -> false positive, clear
                elif has_ob is False:
                    met = True       # window lacks order by -> real, decidable
                else:
                    met = None       # unresolved window var -> indeterminate (LLM verify)
        else:  # distinct_arg: Python API has no distinct param -> indeterminate
            met = None
        out.append({"file": rel, "line": n.lineno, "function": name,
                    "condition": condition, "met": met, "surface": "python"})
    return out


def _extract_sql_strings(src: str) -> List[str]:
    """spark.sql("...") and F.expr("...") string-literal arguments."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    sqls = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("sql", "expr"):
            for a in n.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    sqls.append(a.value)
    return sqls


def _scan_sql(query: str, cond: Dict[str, str], rel: str) -> List[Dict[str, Any]]:
    try:
        trees = sqlglot.parse(query, read="spark")
    except Exception:
        return []
    out = []
    _name_re = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*\(", re.IGNORECASE)
    for tree in trees:
        if tree is None:
            continue
        for fnode in tree.find_all(sx.Func, sx.Anonymous):
            # Derive the AS-WRITTEN spark name: sqlglot canonicalizes the node
            # class (percentile_approx -> ApproxQuantile) but renders the original
            # name in the spark dialect.
            try:
                rendered = fnode.sql(dialect="spark")
            except Exception:
                rendered = ""
            m = _name_re.match(rendered)
            name = (m.group(1).lower() if m else "")
            if name not in cond:
                continue
            condition = cond[name]
            in_window = fnode.find_ancestor(sx.Window) is not None
            has_distinct = "distinct" in rendered.lower()
            if condition == "in_window":
                met = True if in_window else False
            elif condition == "window_no_order_by":
                if not in_window:
                    met = False
                else:
                    win = fnode.find_ancestor(sx.Window)
                    has_ob = win is not None and win.args.get("order") is not None
                    met = False if has_ob else True
            else:  # distinct_arg
                met = True if has_distinct else False
            out.append({"file": rel, "line": None, "function": name,
                        "condition": condition, "met": met, "surface": "sql"})
    return out


def scan_tree(src_root: Path, cond: Dict[str, str]) -> List[Dict[str, Any]]:
    occ = []
    for p in sorted(src_root.rglob("*.py")):
        rel = str(p.relative_to(src_root))
        text = p.read_text(encoding="utf-8", errors="replace")
        occ += _scan_python(text, cond, rel)
        for q in _extract_sql_strings(text):
            occ += _scan_sql(q, cond, rel)
    for p in sorted(src_root.rglob("*.sql")):
        rel = str(p.relative_to(src_root))
        occ += _scan_sql(p.read_text(encoding="utf-8", errors="replace"), cond, rel)
    # deterministic order
    occ.sort(key=lambda d: (d["file"], d["function"], str(d["line"]), d["surface"], str(d["met"])))
    return occ


def summarize(occ: List[Dict[str, Any]]) -> Dict[str, Any]:
    # per function: clearable iff it occurs and NO occurrence is met=True or indeterminate
    by_fn: Dict[str, Dict[str, int]] = {}
    for o in occ:
        s = by_fn.setdefault(o["function"], {"met_true": 0, "met_false": 0, "indeterminate": 0})
        if o["met"] is True: s["met_true"] += 1
        elif o["met"] is False: s["met_false"] += 1
        else: s["indeterminate"] += 1
    clearable = sorted(f for f, s in by_fn.items()
                       if s["met_false"] > 0 and s["met_true"] == 0 and s["indeterminate"] == 0)
    confirmed = sorted(f for f, s in by_fn.items() if s["met_true"] > 0)
    return {"occurrences": len(occ), "by_function": dict(sorted(by_fn.items())),
            "clearable": clearable, "confirmed": confirmed}


def reconcile(analysis_path: Path, summary: Dict[str, Any]):
    issues = json.loads(analysis_path.read_text())
    clearable = set(summary["clearable"])
    confirmed = set(summary["confirmed"])
    cleared = confirmed_n = 0
    for it in issues:
        blob = json.dumps(it).lower()
        for fn in clearable:
            if fn in blob and it.get("resolution") != "safe":
                it["resolution"] = "safe"
                it["resolution_reason"] = f"statically-cleared: {fn} condition not met (AST/SQL)"
                it["kind"] = "static_condition_verified"
                cleared += 1
                break
        else:
            for fn in confirmed:
                if fn in blob:
                    it["kind"] = "static_condition_verified"
                    confirmed_n += 1
                    break
    analysis_path.write_text(json.dumps(issues, indent=1))
    return {"cleared": cleared, "confirmed": confirmed_n, "total_issues": len(issues)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--kb-rules", default=None, help="path to kb_rules.json (default: bundled catalog)")
    ap.add_argument("--analysis", default=None)
    ap.add_argument("--out", default=None, help="write deterministic scan JSON here")
    a = ap.parse_args()
    cond = _load_conditional_fns(a.kb_rules)
    occ = scan_tree(Path(a.src), cond)
    summary = summarize(occ)
    result = {"conditional_fns": dict(sorted(cond.items())), "summary": summary, "occurrences": occ}
    print("SCAN_SUMMARY=" + json.dumps(summary, sort_keys=True))
    if a.out:
        Path(a.out).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    if a.analysis:
        rec = reconcile(Path(a.analysis), summary)
        print("RECONCILE=" + json.dumps(rec, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
