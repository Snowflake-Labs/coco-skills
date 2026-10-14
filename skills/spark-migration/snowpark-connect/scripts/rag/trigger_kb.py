# flake8: noqa: T201
"""Trigger-gated compatibility knowledge base for SCOS migration analysis.

This is the precise, exact-match replacement for the fuzzy embedding RAG.
Instead of ranking candidates by cosine similarity (which mapped noise straight
into ``final_risk``), it fires a curated rule ONLY when its literal anchor
(an API call, method, or SQL construct) actually appears in the customer code.

Rules are loaded from ``data/kb_rules.json`` (mined from the SCOS
behavioral-differences catalog, the Snowflake gaps report, and historical
RCA fixtures; the build pipeline is internal-only and not shipped here).

``SCOSTriggerRAG`` implements the same ``BaseRAG.search`` contract as the
embedding/remote backends, so it is a drop-in ``--rag-backend trigger`` with no
changes to the analyzer pipeline: matches become ``SCOSSearchResult`` whose
``score`` is the curated severity (NOT a similarity), so risk is decoupled from
fuzzy distance.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from rag.base import BaseRAG, SCOSSearchResult
from rag.detectors import run_detectors
from rag.sql_ast import NOT_IN_API_TOKEN, WINDOW_ORDER_NOTE_MARK, analyze_sql

DEFAULT_KB_PATH = Path(__file__).resolve().parent.parent / "data" / "kb_rules.json"

# Severity -> pseudo "score" in [0,1] so it slots into the existing
# failure_likelihood = score * 100 logic, but driven by curation not cosine.
SEVERITY_SCORE = {"high": 0.95, "medium": 0.7, "low": 0.45}

# ``gate`` keys decided by inspecting the PARSED CALL's shape rather than by
# matching text. A rule gated on one of these is a structurally certain match
# when it fires (see ``_rule_decidable``). Deliberately narrow: the older
# ``numeric_lit_arg`` / ``noarg_method`` gates are AST checks too, but they
# narrow token-presence rules rather than define them, so their decidability is
# left unchanged.
_STRUCTURAL_GATES = ("nested_lambda_capture",)

_SQL_SIGNATURE = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|MERGE|WITH|FROM|QUALIFY)\b", re.IGNORECASE
)

# External cloud-storage path schemes in a string literal. SCOS reads/writes go
# through Snowflake stages/tables, not raw cloud URLs, so any such literal is a
# guaranteed repoint — detection is structurally certain (the scheme is present),
# the rewrite target (which stage/table) is contextual. Matching ``ast.Constant``
# string VALUES means comments/identifiers never false-trigger.
_CLOUD_PATH = re.compile(r"(?:s3a?|gs|wasb|abfss?|adl)://|\bdbfs:/|/mnt/", re.IGNORECASE)
_CLOUD_PATH_RULE_ID = "scos:external-cloud-path"
_CLOUD_PATH_ANCHOR = "external cloud storage path"
_CLOUD_PATH_NOTE = (
    "External cloud-storage path (s3://, dbfs:, /mnt/, gs://, abfss://) detected. "
    "SCOS reads/writes go through Snowflake stages or tables, not raw cloud URLs - "
    "repoint to an external stage (@DB.SCHEMA.STAGE/...) or spark.table(...)."
)



def _is_sql_token(tok: str) -> bool:
    return tok.isupper() and len(tok) > 2 or " " in tok


def _rule_decidable(rule: dict) -> bool:
    """Is a fired rule statically decidable (no LLM adjudication needed)?

    Decidability is about CONFIDENCE that the match is a true positive in
    context — NOT about severity. A match is decidable when its firing
    condition is structurally certain:

      * ``signature`` rules fire only when a divergent/unsupported keyword
        argument is actually passed — the AST kwarg check IS the decision.
      * ``python_attribute`` rules (e.g. the ``.rdd`` gateway) fire on the
        accessor itself; reaching the unsupported API is unambiguous.
      * ``status == "Unsupported"`` means the matched API/SQL construct is
        simply not implemented, so a real call to it is a guaranteed runtime
        failure on presence alone.

    Everything else (behavioral / unclassified anchors that fire on mere token
    presence) stays context-dependent and is left for the LLM.

      * Hand-curated ``scos:``-catalog rules flagged as hard ``Error`` are also
        decidable. Unlike the auto-mined ``csv:`` / ``gaps:`` behavioral rules
        (which fire on token presence and are false-positive-prone), a ``scos:``
        Error rule is an author-asserted certain failure on a specific construct
        — e.g. ``spark.conf.set`` on a static-pinned key raises. Trusting it
        makes the finding authoritative (curated = authoritative): it is emitted
        deterministically and the Phase 1.1 adjudicator cannot silently dismiss
        it. Scoped to the ``scos:`` prefix so the token-presence families
        (``csv:``/``gaps:``, incl. the SPRKCNTPY5400 join/distinct rules) stay
        non-decidable.

      * A rule gated on a STRUCTURAL AST predicate (``gate`` keys in
        ``_STRUCTURAL_GATES``) is decidable for the same reason a ``signature``
        rule is: the gate does not test token presence, it tests the shape of
        the parsed call, so firing already IS the decision. This also matters
        for reachability — a non-decidable finding can be routed to the
        deferred path, which carries only rule metadata and drops the rule's
        curated ``fix``, leaving the fixer with the note but not the remedy.
    """
    kind = rule.get("trigger_kind") or rule.get("kind")
    if kind in ("signature", "python_attribute"):
        return True
    if any(k in (rule.get("gate") or {}) for k in _STRUCTURAL_GATES):
        return True
    if (rule.get("status") or "").lower() == "unsupported":
        return True
    rule_id = rule.get("rule_id") or ""
    if rule_id.startswith("scos:") and rule.get("status_class") == "Error":
        return True
    return False


def _is_noarg_method(node: ast.Call) -> bool:
    """Return True when ``node`` is a no-argument attribute method call NOT on F/functions.

    Fires on: ``df.count()``, ``x.count()``, ``self.df.count()``
    Suppresses: ``F.count('*')``, ``functions.count(col)``, any call with args.
    Conservative: if the receiver looks like the functions module, suppress even with 0 args.
    """
    # Must be an attribute call: <receiver>.<method>(...)
    if not isinstance(node.func, ast.Attribute):
        return False
    # Must have zero positional args and zero keyword args
    if node.args or node.keywords:
        return False
    # Check receiver is not F / functions / pyspark.sql.functions
    receiver = node.func.value
    if isinstance(receiver, ast.Name) and receiver.id in ("F", "functions"):
        return False
    # pyspark.sql.functions.count() — attribute chain ending in "functions"
    if isinstance(receiver, ast.Attribute) and receiver.attr == "functions":
        return False
    return True


def _is_numeric_lit_arg(node: ast.Call) -> bool:
    """Return True if the first positional arg of ``node`` is a numeric literal.

    Matches: int/float constants, ``Decimal(...)`` / ``decimal.Decimal(...)`` calls.
    Returns False (conservative) for anything else — strings, booleans, None, variables.
    """
    if not node.args:
        return False
    arg = node.args[0]
    # int or float constant (ast.Constant with numeric value, NOT bool which is a subclass of int)
    if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)) and not isinstance(arg.value, bool):
        return True
    # Decimal("...") or decimal.Decimal("...") call
    if isinstance(arg, ast.Call):
        func = arg.func
        if isinstance(func, ast.Name) and func.id == "Decimal":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "Decimal":
            return True
    # Negative numeric literal: ast.UnaryOp(op=USub, operand=Constant(int|float))
    if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
        operand = arg.operand
        if isinstance(operand, ast.Constant) and isinstance(operand.value, (int, float)) and not isinstance(operand.value, bool):
            return True
    return False


def _lambda_param_names(fn: ast.Lambda) -> set[str]:
    """Every name bound by ``fn``'s parameter list."""
    a = fn.args
    names = {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def _direct_name_loads(node: ast.AST) -> set[str]:
    """Names read in ``node``'s subtree, NOT descending into nested lambdas.

    Stopping at a lambda boundary is what makes shadowing work: a name rebound
    by a deeper lambda's own parameter belongs to that deeper scope, and is
    reported (or not) when the recursion reaches it.
    """
    out: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Lambda):
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            out.add(n.id)
        stack.extend(ast.iter_child_nodes(n))
    return out


def _takes_collection_first(node: ast.Call) -> bool:
    """True when ``node`` has the argument shape of a SQL higher-order function.

    Every PySpark HOF in this family takes the COLLECTION first and the lambda
    after it — ``F.transform(col, f)``, ``F.aggregate(col, init, merge)``,
    ``F.zip_with(c1, c2, f)``, ``F.array_sort(col, comparator)``. The Python
    APIs that share these leaf names take the FUNCTION first and are ordinary
    Python closures that CPython resolves, not SQL lambdas:
    ``DataFrame.transform(fn)``, ``RDD.filter(fn)``, ``RDD.reduce(fn)``,
    the builtin ``filter(fn, it)``, ``functools.reduce(fn, it, init)``.

    Discriminating on the signature rather than on the receiver name means no
    alias guessing (``F`` / ``sf`` / ``functions`` / a bare import all work)
    and no denylist of receivers to keep current.
    """
    if node.args and isinstance(node.args[0], ast.Lambda):
        return False  # function-first: a Python callable API, not a SQL HOF
    # A SQL HOF always has a collection argument alongside the lambda; a
    # lambda-only call (``df.transform(func=fn)``) cannot be one.
    return any(
        not isinstance(a, ast.Lambda)
        for a in (*node.args, *(kw.value for kw in node.keywords))
    )


def _enclosing_scope_reads(fn: ast.Lambda) -> set[str]:
    """Names ``fn`` reads from the scope it is DEFINED in, not called in.

    Parameter defaults are evaluated where the lambda literal appears, so
    ``lambda a, v, _z=z: ...`` binds the enclosing lambda's ``z`` at
    definition time. It is the standard Python capture idiom and a plausible
    thing for a fixer to emit, and it fails on SCOS identically to a direct
    read — the default still resolves to the outer lambda's variable.
    """
    args = fn.args
    out: set[str] = set()
    for d in (*args.defaults, *(d for d in args.kw_defaults if d is not None)):
        out |= _direct_name_loads(d)
    return out


def _is_nested_lambda_capture(node: ast.Call) -> bool:
    """True when a lambda passed to ``node`` contains a *nested* lambda whose
    body reads a variable bound by an ENCLOSING lambda's parameters.

    This is the cross-lambda capture SCOS cannot resolve: a lambda compiled to
    Snowflake SQL sees only its own parameters and the parent dataframe's
    columns, so an outer lambda variable read from an inner lambda raises
    ``Cannot resolve variable '<v>' within lambda function``.

    Fires on   ``F.transform("zs", lambda z: F.aggregate(z, F.lit(1),
                              lambda acc, v: acc * v * F.size(z)))``  (reads ``z``)
               ``F.transform("zs", lambda z: F.aggregate(z, F.lit(1),
                              lambda a, v, _z=z: a * v * _z))``   (via a default)
    Suppresses ``F.transform("b", lambda x: x + 1)``            (no nested lambda)
               ``lambda z: F.aggregate(z, F.lit(0), lambda a, v: a + F.col("x"))``
                                                               (parent df column)
               ``lambda z: F.aggregate(F.array_repeat(F.size(z), F.size(z)),
                                       F.lit(0), lambda a, s: a + s)``
                                        (outer var used in the COLLECTION arg,
                                         which is resolved in the outer scope)
               ``lambda z: F.transform(z, lambda z: z + 1)``   (inner shadows it)
               ``df.transform(lambda d: d.select(F.transform("a",
                                       lambda x: x + d.b)))``
                                        (``DataFrame.transform`` — a Python
                                         closure; see _takes_collection_first)

    The match anchors on the OUTERMOST capturing call, because the remedy is
    applied there: the captured value is paired into the inner collection in
    the outer lambda's body. Conservative — needs a real Python AST, so the
    SQL/regex scan paths skip this gate rather than guess.
    """
    if not _takes_collection_first(node):
        return False
    stack: list[tuple[ast.AST, frozenset[str]]] = [
        (a, frozenset())
        for a in (*node.args, *(kw.value for kw in node.keywords))
    ]
    while stack:
        cur, enclosing = stack.pop()
        if isinstance(cur, ast.Lambda):
            own = _lambda_param_names(cur)
            if enclosing and (
                (_direct_name_loads(cur.body) & (enclosing - own))
                or (_enclosing_scope_reads(cur) & enclosing)
            ):
                return True
            stack.append((cur.body, enclosing | own))
        else:
            stack.extend((ch, enclosing) for ch in ast.iter_child_nodes(cur))
    return False


@lru_cache(maxsize=1)
def _structural_gate_leaves(path: Path = DEFAULT_KB_PATH) -> frozenset[str]:
    """Bare leaf names a structurally-gated rule is anchored on."""
    leaves: set[str] = set()
    for r in json.loads(Path(path).read_text()):
        if not any(k in (r.get("gate") or {}) for k in _STRUCTURAL_GATES):
            continue
        api = r.get("api") or r.get("match_tokens") or []
        if isinstance(api, str):
            api = [api]
        leaves.update(t.lower() for t in api if t and "." not in t)
    return frozenset(leaves)


def is_gated_nested_lambda_capture(node: ast.AST) -> bool:
    """``_is_nested_lambda_capture`` restricted to the calls the KB anchors the
    gate on, so a whole-block scan agrees with what ``detect`` would report.

    Without the leaf-name restriction any call wrapping a capturing one would
    satisfy the shape test — ``list(filter(lambda x: ... lambda y: ... x ...))``
    reports the Python closures inside it through the outer ``list``.
    """
    if not isinstance(node, ast.Call):
        return False
    leaf = TriggerKB._func_path(node.func).rsplit(".", 1)[-1].lower()
    return leaf in _structural_gate_leaves() and _is_nested_lambda_capture(node)


def _node_within(inner: ast.AST, outer: ast.AST) -> bool:
    """True when ``inner``'s source extent is enclosed by ``outer``'s."""
    try:
        return (
            (outer.lineno, outer.col_offset)
            <= (inner.lineno, inner.col_offset)
            and (inner.end_lineno, inner.end_col_offset)
            <= (outer.end_lineno, outer.end_col_offset)
        )
    except (AttributeError, TypeError):
        return False


@dataclass
class Match:
    rule_id: str
    anchor: str
    severity: str
    disposition: str
    note: str
    fix: str | None
    jira: str | None
    matched_token: str
    line: int
    snippet: str
    # See ``_rule_decidable``. Structural detectors leave this False.
    decidable: bool = False
    # Deterministic EWI code + status class carried from the rule catalog.
    ewi_code: str = ""
    status_class: str = ""


@dataclass
class TriggerKB:
    """Loads rules once and detects literal triggers in Python / SQL code."""

    rules: list[dict]
    py_leaf: dict[str, list[dict]] = field(default_factory=dict)
    py_path: dict[str, list[dict]] = field(default_factory=dict)
    # py_path keyed by its last segment (leaf), so a call only checks the few
    # dotted rules that share its leaf instead of scanning every py_path entry.
    py_path_by_leaf: dict[str, list[str]] = field(default_factory=dict)
    sql_index: dict[str, re.Pattern] = field(default_factory=dict)
    sql_rules: dict[str, list[dict]] = field(default_factory=dict)
    # Bare function-name anchors that are valid BOTH as a PySpark call
    # (F.try_multiply(...)) and as a SQL function (SELECT try_multiply(...)).
    # Indexed here too so they fire inside SQL strings, not just via the AST.
    sql_func_index: dict[str, re.Pattern] = field(default_factory=dict)
    sql_func_rules: dict[str, list[dict]] = field(default_factory=dict)
    # Signature-aware rules (mined from the API-compatibility catalog). Keyed by
    # the call leaf (e.g. ``drop_duplicates``); each carries ``allowed_kwargs``
    # and fires ONLY when a matched call passes a keyword arg outside that set
    # (e.g. a pandas-style ``keep=``). High precision: an unknown kwarg is a
    # guaranteed runtime error, decided structurally without the LLM.
    sig_rules: dict[str, list[dict]] = field(default_factory=dict)
    # Attribute-access rules (e.g. the `.rdd` gateway). Keyed by the attribute
    # name; fire when the attribute is accessed on ANY object (``df.rdd``,
    # ``df.rdd.map(...)``, ``x = df.rdd``). Used for APIs that are reached
    # through a gateway accessor rather than a bare leaf call — matching the
    # accessor is unambiguous, unlike the colliding per-method leaf names.
    attr_rules: dict[str, list[dict]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path = DEFAULT_KB_PATH, *, language: str = "python") -> "TriggerKB":
        rules = json.loads(Path(path).read_text())
        # Skip Python-only attribute-access rules when analysing non-Python code.
        # (e.g. lowercase .groupby / .dropna are Python-only; Java uses camelCase)
        if language != "python":
            rules = [r for r in rules if r.get("kind") not in ("python_attribute",)]
        kb = cls(rules=rules)
        kb._index()
        return kb

    def _index(self) -> None:
        for r in self.rules:
            kind = r.get("kind") or r.get("trigger_kind")  # back-compat shim during schema migration
            if kind == "manual":
                continue
            api = r.get("api") or r.get("match_tokens") or []
            gate = r.get("gate") or {}
            # Signature rules: fire only on a kwarg-shaped trigger, never on a
            # bare call. Two semantics, both inside ``gate``:
            #   - ``kwarg_in``    (preferred): fire when any passed kwarg is in the set
            #     (used for SCOS-narrowing — e.g. ``sample(seed=)`` is non-deterministic
            #     in SCOS, so we want to fire only when ``seed`` is actually passed).
            #   - ``kwarg_not_in`` (legacy): fire when any kwarg is OUTSIDE the set
            #     (catches generic Python signature errors).
            if kind == "signature" and ("kwarg_in" in gate or "kwarg_not_in" in gate):
                for tok in api:
                    if not tok:
                        continue
                    leaf = tok.lower().rsplit(".", 1)[-1]
                    self.sig_rules.setdefault(leaf, []).append(r)
                continue
            # Attribute-gateway rules (e.g. `.rdd`): indexed by attribute name,
            # matched against ast.Attribute access rather than calls.
            if kind == "python_attribute":
                for tok in api:
                    if tok:
                        self.attr_rules.setdefault(tok, []).append(r)
                continue
            for tok in api:
                if not tok:
                    continue
                if _is_sql_token(tok):
                    key = tok.upper()
                    if key not in self.sql_index:
                        pat = r"\b" + re.escape(key).replace(r"\ ", r"\s+") + r"\b"
                        self.sql_index[key] = re.compile(pat, re.IGNORECASE)
                    self.sql_rules.setdefault(key, []).append(r)
                elif "." in tok:
                    key = tok.lower()
                    self.py_path.setdefault(key, []).append(r)
                    leaf = key.rsplit(".", 1)[-1]
                    if key not in self.py_path_by_leaf.setdefault(leaf, []):
                        self.py_path_by_leaf[leaf].append(key)
                else:
                    # Bare identifier: a PySpark function/expression name. These
                    # are usually valid as BOTH a Python call and a SQL function,
                    # so index in py_leaf (AST) AND as a SQL function call.
                    low = tok.lower()
                    self.py_leaf.setdefault(low, []).append(r)
                    if low not in self.sql_func_index:
                        self.sql_func_index[low] = re.compile(
                            r"\b" + re.escape(low) + r"\s*\(", re.IGNORECASE
                        )
                    self.sql_func_rules.setdefault(low, []).append(r)

    # ----------------------------------------------------------------- detect
    @staticmethod
    def _func_path(func: ast.expr) -> str:
        parts: list[str] = []
        node: ast.expr | None = func
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    def _match_call(self, path: str) -> list[tuple[dict, str]]:
        out: list[tuple[dict, str]] = []
        low = path.lower()
        leaf = low.rsplit(".", 1)[-1]
        for r in self.py_leaf.get(leaf, []):
            out.append((r, leaf))
        # Only consider dotted rules whose leaf matches this call's leaf, then
        # confirm the suffix — O(few) instead of scanning all py_path entries.
        for key in self.py_path_by_leaf.get(leaf, []):
            if low == key or low.endswith("." + key):
                for r in self.py_path[key]:
                    out.append((r, key))
        return out

    def _scan_sql(self, text: str, base_line: int, seen: set, found: list[Match],
                  ast_result=None) -> None:
        # SQL clause/keyword constructs (QUALIFY, PIVOT, ...) and bare SQL
        # function calls (try_multiply(...), concat_ws(...)) share this scan.
        # When an AST pass has adjudicated window-ordering (ast_result is not
        # None and handled_window_order), skip the coarse token rules that flag
        # a window function on bare presence — the AST emits a precise finding
        # only when the ORDER BY is genuinely absent.
        suppress_window_order = bool(
            ast_result is not None and getattr(ast_result, "handled_window_order", False)
        )
        # When the AST has adjudicated NOT IN arity, skip the token rules
        # anchored on the NOT IN keyword — they fire on every NOT IN regardless
        # of arity, whereas the AST flags only the multi-column tuple form that
        # actually diverges.
        suppress_not_in = bool(
            ast_result is not None and getattr(ast_result, "handled_not_in", False)
        )
        # behavioral:sql.* token rules the §9 AST detectors supersede when the
        # parse succeeded (INSERT OVERWRITE+PARTITION, GROUPING SETS w/ GROUP BY,
        # LATERAL VIEW unsupported generator). The AST checks the surrounding
        # structure; the token rule fires on bare keyword presence.
        superseded_rule_ids = (
            getattr(ast_result, "handled_token_rule_ids", frozenset())
            if ast_result is not None else frozenset()
        )
        for index, rules_map in ((self.sql_index, self.sql_rules),
                                 (self.sql_func_index, self.sql_func_rules)):
            for key, pat in index.items():
                m = pat.search(text)
                if not m:
                    continue
                line = base_line + text[: m.start()].count("\n")
                for r in rules_map[key]:
                    if suppress_window_order and WINDOW_ORDER_NOTE_MARK in (r.get("note") or ""):
                        continue
                    if suppress_not_in and any(
                        a.upper() == NOT_IN_API_TOKEN for a in (r.get("api") or [])
                    ):
                        continue
                    if r.get("rule_id") in superseded_rule_ids:
                        continue
                    _g = r.get("gate") or {}
                    if _g.get("noarg_method"):
                        continue  # SQL context: never a DataFrame method call
                    if _g.get("nested_lambda_capture"):
                        continue  # needs a Python AST to see the lambda nesting
                    if not self._condition_ok(r, text):
                        continue
                    dedup = (r["rule_id"], line)
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    found.append(self._mk_match(r, key, line, m.group(0)))

    @staticmethod
    def _condition_ok(rule: dict, context: str) -> bool:
        """Optional argument gate: if a rule declares ``gate.arg_contains``, the
        call site (or SQL text) must contain one of those substrings. Lets a
        rule anchored on a broad API (``spark.read.format``) fire only for the
        relevant argument (``binaryFile``), avoiding false positives on other
        formats like ``snowflake`` / ``parquet``.

        ``gate.not_contains`` is the inverse: the rule is SUPPRESSED when the
        call site contains any of those substrings. This lets a rule anchored on
        an ambiguous bare method (``save``) fire only outside a disqualifying
        chain — e.g. the ML ``model.save`` rule must NOT fire on a
        ``df.write.format(...).save()`` DataFrameWriter chain."""
        gate = rule.get("gate") or {}
        low = context.lower()
        nc = gate.get("not_contains")
        if nc and any(s.lower() in low for s in nc):
            return False
        aw = gate.get("arg_contains") or rule.get("applies_when")  # back-compat
        if not aw:
            return True
        return any(s.lower() in low for s in aw)

    def _run_detectors(self, text: str, base_line: int, seen: set, found: list[Match],
                       ast_result=None) -> None:
        """Run structural detectors (alias collisions, window-CASE, etc.) over a
        SQL text segment and append matches, deduped by (rule_id, line). When an
        AST pass succeeded, skip the regex detectors it supersedes (LCA, IN-in-ON
        clause) — the AST versions are scope-precise and already appended."""
        skip = ast_result.handled_detectors if ast_result is not None else frozenset()
        for det, pos, snippet in run_detectors(text):
            if det.rule_id in skip:
                continue
            line = base_line + text[:pos].count("\n")
            dedup = (det.rule_id, line)
            if dedup in seen:
                continue
            seen.add(dedup)
            found.append(Match(
                rule_id=det.rule_id, anchor=det.anchor, severity=det.severity,
                disposition=det.disposition, note=det.note, fix=None,
                jira=det.jira, matched_token=det.anchor, line=line,
                snippet=snippet.strip()[:200],
            ))

    @staticmethod
    def _anchor_of(r: dict) -> str:
        """Display string for a rule. Prefer the deprecated ``anchor`` field if
        still present (backward-compat), otherwise use the first ``api`` token."""
        if r.get("anchor"):
            return r["anchor"]
        api = r.get("api") or r.get("match_tokens") or []
        return api[0] if api else r.get("rule_id", "")

    @classmethod
    def _mk_match(cls, r: dict, token: str, line: int, snippet: str) -> Match:
        return Match(
            rule_id=r["rule_id"], anchor=cls._anchor_of(r), severity=r["severity"],
            disposition=r.get("disposition") or "annotate",
            note=r["note"], fix=r.get("fix"),
            jira=r.get("jira"), matched_token=token, line=line,
            snippet=snippet.strip()[:200],
            decidable=_rule_decidable(r),
            ewi_code=r.get("ewi_code", ""),
            status_class=r.get("status_class", ""),
        )

    def _check_signature(
        self, node: ast.Call, path: str, seen: set, found: list[Match]
    ) -> None:
        """Fire signature rules when a matched call passes a keyword argument
        that signals a real divergence. Two ``gate`` shapes are supported:
          - ``gate.kwarg_in``     (preferred): fire when any kwarg IS in the set
            (used for SCOS-narrowing rules — e.g. ``sample(seed=)`` is
            non-deterministic in SCOS, so we want to fire only when ``seed``
            is actually passed).
          - ``gate.kwarg_not_in`` (legacy): fire when any kwarg is OUTSIDE the set.
        Calls with ``**kwargs`` unpacking are skipped — we can't statically
        prove the kwarg shape there.
        """
        leaf = path.lower().rsplit(".", 1)[-1]
        sig_hits = self.sig_rules.get(leaf)
        if not sig_hits:
            return
        passed = [k.arg for k in node.keywords if k.arg]
        if not passed or any(k.arg is None for k in node.keywords):
            return  # no named kwargs, or **kwargs unpacking present
        for r in sig_hits:
            gate = r.get("gate") or {}
            if "kwarg_in" in gate:
                bad = [k for k in passed if k in set(gate["kwarg_in"])]
                prefix = "Divergent keyword argument(s)"
            elif "kwarg_not_in" in gate:
                bad = [k for k in passed if k not in set(gate["kwarg_not_in"])]
                prefix = "Unsupported keyword argument(s)"
            else:
                continue  # nothing to gate on
            if not bad:
                continue
            dedup = (r["rule_id"], node.lineno)
            if dedup in seen:
                continue
            seen.add(dedup)
            bad_str = ", ".join(f"`{b}=`" for b in bad)
            anchor = self._anchor_of(r)
            found.append(Match(
                rule_id=r["rule_id"], anchor=anchor, severity=r["severity"],
                disposition=r.get("disposition") or "annotate",
                note=f"{prefix} {bad_str} for `{anchor}`. " + (r.get("note") or ""),
                fix=r.get("fix"), jira=r.get("jira"),
                matched_token=f"{leaf}({bad[0]}=…)", line=node.lineno, snippet=path,
                decidable=_rule_decidable(r),
                ewi_code=r.get("ewi_code", ""),
                status_class=r.get("status_class", ""),
            ))

    def detect(self, code: str) -> list[Match]:
        """Return rule matches whose literal anchor appears in ``code``."""
        found: list[Match] = []
        seen: set = set()
        # Outermost call already matched per structurally-gated rule. A capture
        # nested three deep satisfies the gate at more than one level, and the
        # `(rule_id, lineno)` dedup only collapses those when the calls happen
        # to share a line — so formatted source would report the same site
        # twice. The outer finding's rewrite covers the inner one.
        structural_hits: dict[str, list[ast.Call]] = {}
        tree = None
        try:
            tree = ast.parse(code)
        except SyntaxError:
            tree = None

        if tree is not None:
            # Run structural detectors against the full Python source — many
            # detector patterns (e.g. `.cast("interval …")`, Snowflake-stage
            # paths in read.X(…)) appear in Python source verbatim, not just
            # inside SQL strings. The detectors' regexes are narrow enough that
            # running over the full source doesn't cross-fire.
            self._run_detectors(code, 1, seen, found)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    path = self._func_path(node.func)
                    if path:
                        seg = None  # computed lazily: only rules with an
                        # arg_contains gate need the call's source text, and
                        # ast.get_source_segment is expensive (re-splits source).
                        for r, tok in self._match_call(path):
                            _g = r.get("gate") or {}
                            if _g.get("arg_contains") or _g.get("not_contains") or r.get("applies_when"):
                                if seg is None:
                                    seg = ast.get_source_segment(code, node) or path
                                if not self._condition_ok(r, seg):
                                    continue
                            if _g.get("numeric_lit_arg") and not _is_numeric_lit_arg(node):
                                continue
                            if _g.get("noarg_method") and not _is_noarg_method(node):
                                continue
                            if _g.get("nested_lambda_capture"):
                                if not _is_nested_lambda_capture(node):
                                    continue
                                # ast.walk is breadth-first, so an ancestor call
                                # is always seen before its descendants.
                                outer = structural_hits.setdefault(r["rule_id"], [])
                                if any(_node_within(node, o) for o in outer):
                                    continue
                                outer.append(node)
                            dedup = (r["rule_id"], node.lineno)
                            if dedup in seen:
                                continue
                            seen.add(dedup)
                            found.append(self._mk_match(r, tok, node.lineno, path))
                        # Signature-aware kwarg gate (API-catalog rules): fire
                        # only when a keyword arg matches the rule's `gate.kwarg_*`
                        # set (e.g. sample(seed=), drop_duplicates(keep=...)).
                        self._check_signature(node, path, seen, found)
                # Attribute-gateway rules (e.g. `.rdd`): fire on the accessor
                # itself, regardless of what follows it.
                if self.attr_rules and isinstance(node, ast.Attribute):
                    for r in self.attr_rules.get(node.attr, []):
                        dedup = (r["rule_id"], node.lineno)
                        if dedup in seen:
                            continue
                        seen.add(dedup)
                        found.append(self._mk_match(
                            r, "." + node.attr, node.lineno, "." + node.attr))
                # SQL embedded in string literals
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    s = node.value
                    # External cloud-storage path literal (s3://, dbfs:, /mnt/, ...).
                    # Decidable: the scheme is literally present; repoint is contextual.
                    if _CLOUD_PATH.search(s):
                        line0 = getattr(node, "lineno", 1)
                        dedup = (_CLOUD_PATH_RULE_ID, line0)
                        if dedup not in seen:
                            seen.add(dedup)
                            found.append(Match(
                                rule_id=_CLOUD_PATH_RULE_ID, anchor=_CLOUD_PATH_ANCHOR,
                                severity="medium", disposition="annotate",
                                note=_CLOUD_PATH_NOTE, fix=None, jira=None,
                                matched_token=s[:80], line=line0, snippet=s[:200],
                                decidable=True,
                                # External cloud-storage paths are an I/O repoint
                                # (read/write must go through a Snowflake stage or
                                # table), not a code-conversion error. Classify as
                                # SPRKCNTPY5400-IO so the report counts it under
                                # "needs human input" rather than "conversion error".
                                ewi_code="SPRKCNTPY5400", status_class="IO",
                            ))
                    if len(s) > 8 and _SQL_SIGNATURE.search(s):
                        line0 = getattr(node, "lineno", 1)
                        # Structural AST pass on the embedded SQL (same contract
                        # as the standalone-.sql path) so shape-dependent gaps
                        # (window-ORDER-BY, multi-column NOT IN, §9 behaviors) are
                        # caught here too, not only the coarse token rules. When
                        # the string can't be parsed, ast_res is None and the
                        # token path runs exactly as before.
                        ast_res = analyze_sql(s, line0)
                        if ast_res is not None:
                            for fd in ast_res.findings:
                                dedup = (fd.rule_id, fd.line)
                                if dedup in seen:
                                    continue
                                seen.add(dedup)
                                found.append(Match(
                                    rule_id=fd.rule_id, anchor=fd.anchor, severity=fd.severity,
                                    disposition=fd.disposition, note=fd.note, fix=None,
                                    jira=fd.jira, matched_token=fd.anchor, line=fd.line,
                                    snippet=fd.snippet.strip()[:200],
                                ))
                        self._scan_sql(s, line0, seen, found, ast_res)
                        # Structural regex detectors already ran over the full
                        # source above — do not re-run per SQL string (double-fire).
        else:
            # Not valid Python: treat as raw text (e.g. a .sql block). Try the
            # sqlglot AST pass first — when it parses, its scope-precise
            # structural findings replace the coarse token/regex matchers
            # (window-ORDER-BY, LCA, IN-in-ON). When it can't parse, ast_res is
            # None and the token/regex path runs exactly as before, so exotic
            # vendor SQL is never dropped.
            ast_res = analyze_sql(code, 1)
            if ast_res is not None:
                for fd in ast_res.findings:
                    dedup = (fd.rule_id, fd.line)
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    found.append(Match(
                        rule_id=fd.rule_id, anchor=fd.anchor, severity=fd.severity,
                        disposition=fd.disposition, note=fd.note, fix=None,
                        jira=fd.jira, matched_token=fd.anchor, line=fd.line,
                        snippet=fd.snippet.strip()[:200],
                    ))
            self._scan_sql(code, 1, seen, found, ast_res)
            self._run_detectors(code, 1, seen, found, ast_res)
            # also catch bare call-like tokens via regex
            suppress_window_order = bool(
                ast_res is not None and ast_res.handled_window_order
            )
            for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(", code):
                path = m.group(1)
                line = 1 + code[: m.start()].count("\n")
                line_text = code.splitlines()[line - 1] if code.splitlines() else code
                for r, tok in self._match_call(path):
                    if suppress_window_order and WINDOW_ORDER_NOTE_MARK in (r.get("note") or ""):
                        continue
                    if not self._condition_ok(r, line_text):
                        continue
                    _g = r.get("gate") or {}
                    if _g.get("numeric_lit_arg"):
                        continue  # no AST available; conservative: skip
                    if _g.get("noarg_method"):
                        continue  # no AST available; conservative: skip
                    if _g.get("nested_lambda_capture"):
                        continue  # no AST available; conservative: skip
                    dedup = (r["rule_id"], line)
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    found.append(self._mk_match(r, tok, line, path))

        order = {"high": 3, "medium": 2, "low": 1}
        found.sort(key=lambda m: (order.get(m.severity, 0), -m.line), reverse=True)
        return found

    def anchor_leaf_names(self) -> set[str]:
        """Bare (lowercase) method/function names with a rule in this KB.

        Includes ``kind="manual"`` rules too — ``_index()`` skips those for
        auto-firing, but they're still real documented divergences (e.g.
        ``join``'s NULL-inclusion gap). Only bare, unqualified anchors are
        returned: a dotted anchor like ``dbutils.fs.head`` or
        ``SnowflakeSession.sql`` is about that specific receiver, not every
        method sharing the trailing name — stripping it to a leaf would
        wrongly flag the unrelated ``DataFrame.head()`` or ``spark.sql()``.
        SQL keyword tokens are excluded too, since they're not method names.

        Rules gated on a STRUCTURAL predicate (``_STRUCTURAL_GATES``) are
        excluded: they do not fire on the API's presence, only on a specific
        parsed shape, so letting them disqualify the API would take every
        block that merely *calls* the API off the safe-list fast path. The
        shape they need is instead detected directly by the callers of
        ``is_block_safe``. On today's catalog this only drops ``reduce``,
        which no ``safe_apis.json`` entry names, so the reconciled safe set is
        unchanged — the exclusion is here to keep a future structural rule on
        a genuinely safe API (``exists``, ``zip_with``, ...) from silently
        costing every caller a RAG lookup.
        """
        leaves: set[str] = set()
        for r in self.rules:
            if any(k in (r.get("gate") or {}) for k in _STRUCTURAL_GATES):
                continue
            api = r.get("api") or r.get("match_tokens") or []
            if isinstance(api, str):
                api = [api]
            for tok in api:
                if not tok or "." in tok or _is_sql_token(tok):
                    continue
                leaves.add(tok.lower())
        return leaves


class SCOSTriggerRAG(BaseRAG):
    """Drop-in RAG backend backed by the trigger KB (no embeddings, no network).

    ``search`` returns the curated rules whose literal anchor appears in the
    query block. ``score`` carries the curated severity, so the analyzer's
    ``failure_likelihood`` reflects real, exact-match evidence rather than
    cosine similarity to internal test cases.
    """

    def __init__(self, kb_path: str | Path = DEFAULT_KB_PATH, *, language: str = "python") -> None:
        super().__init__()
        self.kb = TriggerKB.load(kb_path, language=language)

    def search(self, query: str, limit: int = 5) -> list[SCOSSearchResult]:
        matches = self.kb.detect(query)
        results: list[SCOSSearchResult] = []
        seen_rules: set[str] = set()
        for m in matches:
            if m.rule_id in seen_rules:
                continue
            seen_rules.add(m.rule_id)
            notes = []
            if m.fix:
                notes.append(f"Fix: {m.fix}")
            if m.jira:
                notes.append(f"JIRA: {m.jira}")
            notes.append(f"disposition: {m.disposition}; severity: {m.severity}")
            results.append(SCOSSearchResult(
                code=f"{m.anchor}  (matched `{m.matched_token}` at line {m.line})",
                score=SEVERITY_SCORE.get(m.severity, 0.5),
                root_cause=m.note,
                additional_notes=" | ".join(notes),
                test_name=m.rule_id,
                matched_token=m.matched_token,
                decidable=m.decidable,
                ewi_code=m.ewi_code,
                status_class=m.status_class,
            ))
            if len(results) >= limit:
                break
        return results


def _cli() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Detect SCOS compatibility triggers in a file.")
    ap.add_argument("path")
    ap.add_argument("--kb", default=str(DEFAULT_KB_PATH))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    kb = TriggerKB.load(args.kb)
    matches = kb.detect(Path(args.path).read_text())
    if args.json:
        print(json.dumps([m.__dict__ for m in matches], indent=2))
    else:
        print(f"{len(matches)} trigger match(es) in {args.path}\n")
        for m in matches:
            j = f" [{m.jira}]" if m.jira else ""
            print(f"  L{m.line:<4} {m.severity:<6} {m.anchor}{j}  (token={m.matched_token!r})")
            print(f"        ↳ {m.note[:140]}")


if __name__ == "__main__":
    _cli()
