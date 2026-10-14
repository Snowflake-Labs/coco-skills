# flake8: noqa
"""Catalog-driven SQL detection + rewrite engine.

Parses SQL once (sqlglot, ``dialect="spark"``) and drives both detection and
conversion from the data catalog (``data/sql_rules.json``):

* :func:`detect` — for every catalog rule, find candidate nodes (``find`` type),
  evaluate the rule's declarative ``when`` predicates or its named ``matcher``,
  and emit findings. Returns ``None`` if the text could not be parsed (callers
  fall back to the regex/token path), matching the original ``analyze_sql``
  contract. Findings dedup by ``(rule_id, line)``.
* :func:`rewrite` — apply the catalog-declared transforms per statement and
  regenerate only the statements that changed (statement-level reconstruction
  lifted from the original ``sql_rewrite``); idempotent; parse-failure no-op.

The matcher/transform *bodies* live in :mod:`rag.sql_matchers` /
:mod:`rag.sql_rewrite_transforms` (the hybrid escape-hatch); this module is the
generic evaluator.
"""
from __future__ import annotations

import re

from rag.sql_ast import AstFinding, _node_line, _normalize_placeholders
from rag.sql_catalog import RULES
from rag.sql_matchers import MATCHERS, TRANSFORMS, _fn_name
from rag.sql_rewrite_transforms import SqlEdit

# The ``::`` cast operator leaves no AST trace (sqlglot parses ``x::t`` identically
# to ``CAST(x AS t)``), so it is detected lexically. The Spark 3.5.3 SQL parser
# SCOS uses rejects ``::`` (it is a Spark 4.0 feature); the spark generator always
# emits ``CAST(...)``, so finding ``::`` in a statement that has a Cast node is a
# reliable trigger to regenerate that statement.
_COLON_CAST_RE = re.compile(r"::")

# String/comment spans must be stripped before the lexical ``::`` search so a
# ``'a::b'`` literal (or a ``--`` comment) does not spuriously trigger a cast
# rewrite. Matches single-/double-quoted strings (with doubled-quote escapes),
# line comments, and block comments.
_SQL_LITERAL_RE = re.compile(
    r"'(?:''|[^'])*'"      # single-quoted string
    r"|\"(?:\"\"|[^\"])*\""  # double-quoted identifier/string
    r"|--[^\n]*"            # line comment
    r"|/\*.*?\*/",          # block comment
    re.DOTALL,
)


def _has_colon_cast_token(text: str) -> bool:
    """True if ``::`` appears in ``text`` OUTSIDE any string literal or comment."""
    masked = _SQL_LITERAL_RE.sub(" ", text)
    return _COLON_CAST_RE.search(masked) is not None

# Template-variable round-tripping for the rewrite path. ``_normalize_placeholders``
# (used by detect()) *strips* ``${DB}.${SCHEMA}.`` qualifiers so sqlglot can parse,
# but a regenerated statement would then lose the template vars entirely. For
# rewrite() we instead map each ``${X}`` / ``{X}`` to a unique valid identifier
# token (which survives parse+regenerate unchanged) and restore it afterwards, so
# rewritten statements keep their original ``${DATABASE_NAME}`` references intact.
_PH_TOKEN_RE = re.compile(r"\$?\{[^}]+\}")


def _tokenize_placeholders(text: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}
    counter = [0]

    def _repl(m):
        tok = f"SCOSPHK{counter[0]}Z"
        counter[0] += 1
        mapping[tok] = m.group(0)
        return tok

    return _PH_TOKEN_RE.sub(_repl, text), mapping


def _restore_placeholders(text: str, mapping: dict[str, str]) -> str:
    for tok, orig in mapping.items():
        text = text.replace(tok, orig)
    return text


# --------------------------------------------------------------------------- #
# Predicate evaluation (declarative `when` entries)
# --------------------------------------------------------------------------- #

def _predicate_ok(node, pred: dict, exp) -> bool:
    if "func_name_in" in pred:
        names = {n.upper() for n in pred["func_name_in"]}
        # For window nodes the function is node.this; otherwise the node itself.
        target = node.this if type(node).__name__ == "Window" else node
        return _fn_name(target) in names
    if "arg_present" in pred:
        return bool(node.args.get(pred["arg_present"]))
    if "arg_absent" in pred:
        return not node.args.get(pred["arg_absent"])
    if "this_type" in pred:
        return type(node.this).__name__ == pred["this_type"]
    if "parent_type" in pred:
        return type(node.parent).__name__ == pred["parent_type"]
    if "has_descendant" in pred:
        cls = getattr(exp, pred["has_descendant"], None)
        return cls is not None and next(iter(node.find_all(cls)), None) is not None
    if "sql_matches" in pred:
        try:
            return re.search(pred["sql_matches"], node.sql(dialect="spark"), re.IGNORECASE) is not None
        except Exception:
            return False
    return False


def _find_nodes(stmt, find: str, exp):
    if find == "*":
        return list(stmt.find_all(exp.Expression))
    cls = getattr(exp, find, None)
    if cls is None:
        return []
    return list(stmt.find_all(cls))


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #

def detect(text: str, base_line: int = 1):
    """Run the catalog detectors over ``text``. Returns a list of
    :class:`AstFinding` on a clean parse, or ``None`` if sqlglot cannot parse."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return None

    normalized = _normalize_placeholders(text)
    try:
        statements = sqlglot.parse(normalized, dialect="spark")
    except Exception:
        return None
    if not statements or all(s is None for s in statements):
        return None

    findings: list[AstFinding] = []
    seen: set[tuple[str, int, str]] = set()

    for st in statements:
        if st is None:
            continue
        for rule in RULES:
            if not rule.get("detect", True):
                continue
            matcher_name = rule.get("matcher")
            for node in _find_nodes(st, rule["find"], exp):
                if matcher_name:
                    overrides_list = MATCHERS[matcher_name](node, base_line)
                else:
                    when = rule.get("when") or []
                    overrides_list = [{}] if all(_predicate_ok(node, p, exp) for p in when) else []
                for ov in overrides_list:
                    line = ov.get("line", _node_line(node, base_line))
                    snippet = ov.get("snippet", node.sql(dialect="spark")[:200])
                    # Dedup on (rule_id, line, snippet) — not (rule_id, line)
                    # alone — so two DISTINCT violations of the same rule on one
                    # line (common in minified SQL) are both reported; only exact
                    # repeats collapse.
                    key = (rule["id"], line, snippet)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(AstFinding(
                        rule_id=rule["id"],
                        anchor=rule.get("anchor", rule["id"]),
                        severity=ov.get("severity", rule.get("severity", "medium")),
                        disposition="annotate",
                        jira=ov.get("jira", rule.get("jira")),
                        line=line,
                        snippet=snippet,
                        note=ov.get("note", rule.get("note", "")),
                    ))
    return findings


# --------------------------------------------------------------------------- #
# Rewrite (statement-level regeneration; transforms declared by the catalog)
# --------------------------------------------------------------------------- #

def _active_transforms():
    """Transform fns referenced by at least one catalog rule, in TRANSFORMS
    (apply) order. De-duplicated (e.g. explain_ddl + explain_mode share one)."""
    referenced = {r["transform"] for r in RULES if r.get("transform")}
    return [(name, TRANSFORMS[name]) for name in TRANSFORMS if name in referenced]


def _apply_transforms(stmt, base_line, transforms):
    edits = []
    deleted = False
    for _name, fn in transforms:
        if stmt is None:
            break
        stmt, new_edits = fn(stmt, base_line)
        edits.extend(new_edits)
        if stmt is None:
            deleted = True
            break
    return stmt, edits, deleted


def rewrite(text: str, *, dialect: str = "spark", base_line: int = 1):
    """Apply catalog transforms and return ``(new_text, applied_edits, parsed)``.
    Parse failure → ``(text, [], False)``. Untouched statements are copied
    verbatim; only changed statements are regenerated."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return text, [], False
    normalized, ph_map = _tokenize_placeholders(text)
    try:
        statements = sqlglot.parse(normalized, dialect=dialect)
    except Exception:
        return text, [], False
    entries = [s for s in statements if s is not None]
    if not entries:
        return text, [], False

    transforms = _active_transforms()
    entries = sorted(entries, key=lambda s: _node_line(s, 1))
    starts = [_node_line(s, 1) for s in entries]
    orig_lines = text.split("\n")

    applied = []
    out_lines: list[str] = []
    cursor = 0
    for k, stmt in enumerate(entries):
        seg_start = max(starts[k] - 1, 0)
        seg_end = (starts[k + 1] - 1) if k + 1 < len(entries) else len(orig_lines)
        if seg_start > cursor:
            out_lines.extend(orig_lines[cursor:seg_start])
        seg_lines = orig_lines[seg_start:seg_end]

        new_stmt, edits, deleted = _apply_transforms(stmt, base_line + seg_start, transforms)
        seg_text = "\n".join(seg_lines)
        # `::` cast: lexically detected, fixed by regeneration (spark generator
        # emits CAST). Require a Cast node in the AST so a `::` inside a string
        # literal does not spuriously trigger a rewrite.
        if (
            not deleted
            and new_stmt is not None
            and _has_colon_cast_token(seg_text)
            and next(new_stmt.find_all(exp.Cast), None) is not None
        ):
            edits = list(edits) + [SqlEdit(
                rule_id="dialect:colon_cast",
                line=base_line + seg_start,
                before="<expr>::<type>",
                after="CAST(<expr> AS <type>)",
                note=("the :: cast operator is not in the Spark SQL grammar used "
                      "by spark.sql(); rewrote it as CAST(... AS ...)"),
            )]
        if edits and deleted:
            applied.extend(edits)  # statement removed — emit nothing
        elif edits:
            applied.extend(edits)
            seg_text = "\n".join(seg_lines)
            semi = ";" if seg_text.rstrip().endswith(";") else ""
            regen = _restore_placeholders(
                new_stmt.sql(dialect=dialect, pretty=True), ph_map
            ) + semi
            out_lines.extend(regen.split("\n"))
            trail = 0
            while trail < len(seg_lines) and seg_lines[-1 - trail].strip() == "":
                trail += 1
            out_lines.extend([""] * trail)
        else:
            out_lines.extend(seg_lines)
        cursor = seg_end
    if cursor < len(orig_lines):
        out_lines.extend(orig_lines[cursor:])

    return "\n".join(out_lines), applied, True
