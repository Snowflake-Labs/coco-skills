"""AST-driven extraction of data-edge signatures (read/write endpoints).

Historically this logic lived inside :mod:`scan_codebase`, which had grown
large enough that a self-contained data-edge walker was overdue for its own
home. This module owns:

  * Signature normalization (URI scheme stripping, noise-word rejection).
  * ``_signature_from_node`` — the recursive AST walker that turns a
    read/write call's argument expression into a fingerprint string.
  * ``_extract_path_signatures`` — the top-level per-file entry point used by
    the DAG builder in :mod:`scan_codebase`.
  * ``UnresolvedEdge`` — a diagnostic record for every read/write call that
    the walker gave up on. Each one carries a **dynamically derived** reason
    describing the AST node type the walker stopped at, so migration
    engineers can jump straight to the offending expression in the source.

Five patterns beyond the original Signal-4 fingerprint are handled here:

  1. Builder ``.option("path", x).load()`` — Databricks / Delta / JDBC.
  2. Variable-key subscript ``cfg[k]`` where ``k`` is traceable to a literal.
  3. SQL passthrough ``spark.sql(...)`` via sqlglot (lazy import).
  4. ``.format(name).load(path)`` — record the connector as a hint alongside
     the path, don't confuse the reader-format specifier with the URI arg.
  5. Loop-generated paths ``for x in ["a", "b"]: spark.read.table(x)`` —
     enumerated when the iterable is a literal list/tuple.

The unresolved-reason strings are computed at the failure site, not from a
hardcoded enum, so two workloads with different unresolvable expressions get
different, specific diagnostics.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Reader/writer terminal-method sets. Duplicated from scan_codebase's own
# constants so this module has no back-import; the sets are stable enough
# that a tiny bit of duplication is preferable to a circular dependency.
# ---------------------------------------------------------------------------
_READ_TERMINAL_METHODS: frozenset[str] = frozenset({
    "parquet", "json", "csv", "orc", "text", "avro", "load", "table",
})
_WRITE_TERMINAL_METHODS: frozenset[str] = frozenset({
    "save", "saveAsTable", "insertInto", "parquet", "json", "csv", "orc",
    "text", "avro",
})

# Option-keys inside a builder chain that indicate a data endpoint (as
# opposed to informational tuning like ``checkpoint`` / ``mergeSchema``).
_PATH_OPTION_KEYS: frozenset[str] = frozenset({
    "path", "dbtable", "query", "url", "table", "collection",
})

# Common URI prefixes we strip before signature normalization.
_URI_SCHEME_PREFIXES: tuple[str, ...] = (
    "s3://", "s3a://", "s3n://", "gs://", "hdfs://", "dbfs:/",
    "file://", "wasb://", "wasbs://", "abfs://", "abfss://", "snowflake://",
)

# Bare 1-word signatures we refuse to match on — they collide across files
# without indicating a real shared endpoint. All lowercase.
_SIGNATURE_NOISE_WORDS: frozenset[str] = frozenset({
    "data", "input", "output", "tmp", "temp", "df", "final",
    "read", "write", "file", "path", "table", "raw", "values",
})

# Built-in string ops on a resolvable receiver that we treat as pass-through
# for fingerprint purposes.
_STR_PASSTHROUGH_METHODS: frozenset[str] = frozenset({
    "rstrip", "lstrip", "strip", "lower", "upper", "format", "replace",
    "removesuffix", "removeprefix",
})

# Trivial string methods: the output IS the receiver string (no arg effect).
# Kept separate from _STR_PASSTHROUGH_METHODS so we don't double-handle
# ``format``/``replace``, which have their own argument-aware branches.
_STR_TRIVIAL_PASSTHROUGH: frozenset[str] = frozenset({
    "rstrip", "lstrip", "strip", "lower", "upper", "removesuffix", "removeprefix",
})

# SparkContext RDD reads. First positional arg is always a path / glob pattern.
_SC_READ_METHODS: frozenset[str] = frozenset({
    "textFile", "binaryFiles", "binaryRecords", "sequenceFile",
    "wholeTextFiles", "objectFile",
})

# Pandas I/O read methods. First positional arg is the path or SQL string.
_PANDAS_READ_METHODS: frozenset[str] = frozenset({
    "read_csv", "read_parquet", "read_json", "read_excel", "read_orc",
    "read_feather", "read_hdf", "read_pickle", "read_table",
    "read_sql", "read_sql_table", "read_sql_query",
})

# Pandas I/O write methods. First positional arg is the path or table name.
_PANDAS_WRITE_METHODS: frozenset[str] = frozenset({
    "to_csv", "to_parquet", "to_json", "to_excel", "to_orc",
    "to_feather", "to_hdf", "to_pickle", "to_sql",
})


# ---------------------------------------------------------------------------
# UnresolvedEdge — diagnostic record for read/write calls the walker gave up
# on. The ``reason`` field is derived at the failure site (see
# ``_describe_ast_shape``) so two workloads with different unresolved
# expressions produce different, specific reasons — NOT drawn from a fixed
# enum. Immutable so callers can freely deduplicate via sets.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnresolvedEdge:
    """A read/write call whose path argument the AST walker could not
    statically resolve to a signature.

    Fields:

    * ``file`` — workload-relative path (caller supplies; the walker returns
      the absolute path unresolved and lets the DAG builder rebase).
    * ``line`` — 1-based source line of the call.
    * ``kind`` — ``"read"`` or ``"write"``.
    * ``call_expr`` — ``ast.unparse(node.func)``, e.g. ``spark.read.parquet``.
    * ``arg_expr`` — ``ast.unparse(node.args[0])`` (or an equivalent view of
      the argument that mattered — for a builder chain we pass the whole
      chain here so the engineer sees what they wrote).
    * ``reason`` — dynamic diagnostic (see :func:`_describe_ast_shape`).
    """

    file: str
    line: int
    kind: str
    call_expr: str
    arg_expr: str
    reason: str


# ---------------------------------------------------------------------------
# Signature normalization
# ---------------------------------------------------------------------------


def _normalize_signature(s: str) -> str | None:
    """Normalize a fingerprint string into a canonical signature.

    Steps:
      * Strip common URI scheme prefixes (``s3://``, ``dbfs:/`` etc.).
      * Drop f-string / .format placeholder markers.
      * Collapse runs of ``/`` and strip leading/trailing separators.
      * **Lowercase** the result so writer and reader match even when the
        two files spell the same path with different case (``EQS`` vs
        ``eqs`` — real drift observed in Verisk).
      * Return ``None`` for signatures deemed too generic to reliably
        indicate a shared endpoint (length < 4, or a bare noise word).
    """
    if not s or not isinstance(s, str):
        return None
    out = s
    lowered = out.lower()
    for prefix in _URI_SCHEME_PREFIXES:
        if lowered.startswith(prefix):
            out = out[len(prefix):]
            break
    # Drop any f-string / .format placeholder markers (``{}``, ``{name}``,
    # ``{0}``). If they resulted from unresolved variables the interior isn't
    # useful for fingerprint matching — strip them uniformly.
    out = re.sub(r"\{[^{}]*\}", "", out)
    # Collapse runs of "/" and strip separators.
    while "//" in out:
        out = out.replace("//", "/")
    out = out.strip("/").strip()
    if len(out) < 4:
        return None
    if out.lower() in _SIGNATURE_NOISE_WORDS:
        return None
    return out.lower()


# ---------------------------------------------------------------------------
# Assignment collection — maps every ``x = ...`` and ``self.x = ...`` to a
# list of value expressions. The recursive resolver walks these to trace
# Python-side chains before hitting the config-pool boundary. This helper
# used to live in scan_codebase; it's moved here because the data-edge
# walker is by far its heaviest user. scan_codebase's ``_resolve_via_config``
# now imports it back from this module for its own resolution pass.
# ---------------------------------------------------------------------------


def _collect_assignments(tree: ast.AST) -> dict[str, list[ast.AST]]:
    """Map name / attribute-name → list of value expressions assigned to it.

    Tracks both ``x = ...`` and ``self.x = ...`` so the resolver can follow
    Python-side chains before hitting the config boundary."""
    out: dict[str, list[ast.AST]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    out.setdefault(tgt.id, []).append(n.value)
                elif isinstance(tgt, ast.Attribute):
                    out.setdefault(tgt.attr, []).append(n.value)
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            tgt = n.target
            if isinstance(tgt, ast.Name):
                out.setdefault(tgt.id, []).append(n.value)
            elif isinstance(tgt, ast.Attribute):
                out.setdefault(tgt.attr, []).append(n.value)
    return out


def _attr_chain_names(node: ast.AST) -> set[str]:
    """Collect names along an attribute/call chain (e.g. ``x.read.parquet`` → {'x','read','parquet'})."""
    out: set[str] = set()
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, ast.Attribute):
            out.add(cur.attr)
            cur = cur.value
        elif isinstance(cur, ast.Call):
            cur = cur.func
        elif isinstance(cur, ast.Name):
            out.add(cur.id)
            break
        else:
            break
    return out


# ---------------------------------------------------------------------------
# For-loop iterator collection (pattern 2e). Maps every for-target name to
# the iterable expression. Comprehensions are excluded — their targets are
# lexically scoped and don't bleed into subsequent read/write calls the way
# a bare ``for x in it:`` block does.
# ---------------------------------------------------------------------------


def _collect_for_targets(tree: ast.AST) -> dict[str, ast.AST]:
    """Build ``{loop_var_name: iterator_expr}`` for every ``for`` / ``async for``.

    Only ``Name`` targets are recorded; tuple / starred unpacking is skipped
    because we can't recover the per-element expression without evaluating
    the RHS. When a loop var is reassigned across multiple loops the LAST
    encountered iterable wins (arbitrary but stable — ``ast.walk`` visits
    in document order for the top-level statements)."""
    out: dict[str, ast.AST] = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.For, ast.AsyncFor)):
            tgt = n.target
            if isinstance(tgt, ast.Name):
                out[tgt.id] = n.iter
    return out


def _collect_simple_returns(tree: ast.AST) -> dict[str, ast.AST]:
    """Map function-name → return-value node for single-expression functions.

    Only maps functions whose body (after stripping any leading docstring) is a
    single ``return <expr>`` statement. Used by ``_signature_from_node`` to
    inline the return value when a path argument is a bare function call like
    ``get_output_path()`` where the function is defined in the same file.
    """
    out: dict[str, ast.AST] = {}
    for n in ast.walk(tree):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = n.body
        # Strip a leading docstring if present.
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)):
            body = body[1:]
        if (len(body) == 1 and isinstance(body[0], ast.Return)
                and body[0].value is not None):
            out[n.name] = body[0].value
    return out


def _resolve_to_dict(
    node: ast.AST,
    assignments: dict[str, list[ast.AST]],
) -> dict[str, ast.AST] | None:
    """If *node* is a Name/Attribute whose first assignment is a dict literal,
    return a ``{literal_key: value_node}`` map; else ``None``.

    Used by the subscript resolver to return the ACTUAL value for
    ``cfg["key"]`` rather than the key string itself, when the container is an
    in-scope dict literal.
    """
    if isinstance(node, ast.Name):
        avs = assignments.get(node.id, [])
    elif isinstance(node, ast.Attribute):
        avs = assignments.get(node.attr, [])
    else:
        return None
    for av in avs:
        if isinstance(av, ast.Dict):
            result: dict[str, ast.AST] = {}
            for k, v in zip(av.keys, av.values):
                if (isinstance(k, ast.Constant) and isinstance(k.value, str)
                        and v is not None):
                    result[k.value] = v
            return result
    return None


# ---------------------------------------------------------------------------
# Signature-from-node — the recursive AST → string resolver.
# ---------------------------------------------------------------------------


def _signature_from_node(
    node: ast.AST | None,
    assignments: dict[str, list[ast.AST]],
    depth: int = 0,
    _seen: set[int] | None = None,
    for_targets: dict[str, ast.AST] | None = None,
    simple_returns: dict[str, ast.AST] | None = None,
    config_pool: dict[str, set[str]] | None = None,
) -> str | None:
    """Extract a path-signature fingerprint from an argument expression.

    Handles bare string literals, f-strings, ``.format(...)`` calls with a
    string-constant receiver, binary ``+`` / ``/`` concatenations, and traces
    through name / attribute assignments (recursion cap: depth 6).

    Extended patterns (A2 tier):

    * **Literal dict lookup** — ``cfg["key"]`` where ``cfg`` was assigned a
      dict literal in scope returns the actual VALUE, not just the key string.
    * **pathlib division** — ``Path("base") / "segment"`` → ``"base/segment"``.
    * **Ternary expression** — ``"a" if cond else "b"`` tries the true-branch
      first and falls back to the else-branch. Callers that need BOTH branches
      for multi-signature emission should call ``_enumerate_ternary_signatures``.
    * **Trivial string methods** — ``.strip()``, ``.lower()``, etc. pass through
      the receiver without modification.
    * **os.environ.get default** — ``os.environ.get("VAR", "/default")`` returns
      the default (second arg) as a conservative static approximation.
    * **Same-file function return inlining** — a bare call ``f()`` where ``f``
      is defined in the same file with a single ``return <literal>`` body is
      inlined; pass ``simple_returns`` (from ``_collect_simple_returns``) to
      enable this branch.

    Returns the RAW (un-normalized) signature; caller must normalize.
    """
    if depth > 6 or node is None:
        return None
    if _seen is None:
        _seen = set()
    node_id = id(node)
    if node_id in _seen:
        return None
    _seen.add(node_id)

    # Bare literal.
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value

    # f-string: join all literal segments (placeholders become empty).
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                # Trace through the interpolated expression — critical for
                # ``f'{s3_hist}'`` style writes where the WHOLE f-string is
                # a single ``FormattedValue`` referencing a Name that
                # resolves to a string literal (or ``.format(...)`` call).
                sub = _signature_from_node(
                    v.value, assignments, depth + 1, _seen, for_targets, simple_returns
                )
                if sub:
                    parts.append(sub)
            # Anything else contributes nothing.
        joined = "".join(parts)
        return joined or None

    # Call: .format(), .replace(), .join(), trivial passthrough, environ.get,
    # and same-file function return inlining.
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "format":
            recv = func.value
            if isinstance(recv, ast.Constant) and isinstance(recv.value, str):
                lit = recv.value
                if node.args:
                    sub = _signature_from_node(
                        node.args[0], assignments, depth + 1, _seen, for_targets, simple_returns
                    )
                    if sub:
                        replaced = re.sub(r"\{(?:0|)\}", sub, lit, count=1)
                        replaced = re.sub(r"\{[^{}]*\}", "", replaced)
                        return replaced or None
                lit = re.sub(r"\{[^{}]*\}", "", lit)
                return lit or None
        if isinstance(func, ast.Attribute) and func.attr == "replace" and len(node.args) >= 2:
            recv_sig = _signature_from_node(
                func.value, assignments, depth + 1, _seen, for_targets, simple_returns, config_pool
            )
            arg0 = node.args[0]
            arg1 = node.args[1]
            if (
                recv_sig
                and isinstance(arg0, ast.Constant) and isinstance(arg0.value, str)
                and isinstance(arg1, ast.Constant) and isinstance(arg1.value, str)
            ):
                return recv_sig.replace(arg0.value, arg1.value)
        if isinstance(func, ast.Attribute) and func.attr == "join":
            parts_j: list[str] = []
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    parts_j.append(a.value)
                else:
                    sub = _signature_from_node(
                        a, assignments, depth + 1, _seen, for_targets, simple_returns
                    )
                    if sub:
                        parts_j.append(sub)
            joined_j = "/".join(p for p in parts_j if p)
            return joined_j or None
        # A2: Trivial string methods — just trace the receiver unchanged.
        if isinstance(func, ast.Attribute) and func.attr in _STR_TRIVIAL_PASSTHROUGH:
            return _signature_from_node(
                func.value, assignments, depth + 1, _seen, for_targets, simple_returns, config_pool
            )
        # A2: os.environ.get("VAR", default) — use the default as a static
        # approximation of the runtime path.
        if (isinstance(func, ast.Attribute) and func.attr == "get"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
                and len(node.args) >= 2):
            return _signature_from_node(
                node.args[1], assignments, depth + 1, _seen, for_targets, simple_returns, config_pool
            )
        # pathlib.Path("string") / pathlib.PurePosixPath("string") constructors
        # — the string argument IS the path string.
        _PATH_CTORS = {"Path", "PurePosixPath", "PureWindowsPath", "PosixPath", "WindowsPath"}
        if (isinstance(func, ast.Name) and func.id in _PATH_CTORS and node.args):
            return _signature_from_node(
                node.args[0], assignments, depth + 1, _seen, for_targets, simple_returns, config_pool
            )
        if (isinstance(func, ast.Attribute) and func.attr in _PATH_CTORS and node.args):
            return _signature_from_node(
                node.args[0], assignments, depth + 1, _seen, for_targets, simple_returns, config_pool
            )
        # A2: Same-file function return inlining.
        if isinstance(func, ast.Name) and simple_returns is not None:
            ret_node = simple_returns.get(func.id)
            if ret_node is not None:
                return _signature_from_node(
                    ret_node, assignments, depth + 1, _seen, for_targets, simple_returns
                )
        return None

    # Trace variables through assignments — with for-loop fallback for
    # names that ONLY appear as a loop target (pattern 2e).
    if isinstance(node, ast.Name):
        for av in assignments.get(node.id, []):
            r = _signature_from_node(
                av, assignments, depth + 1, _seen, for_targets, simple_returns, config_pool
            )
            if r:
                return r
        # For-loop target fallback. Try to resolve the iterable's first
        # literal element.
        if for_targets is not None:
            it = for_targets.get(node.id)
            if it is not None:
                first = _first_literal_from_iterable(
                    it, assignments, depth + 1, _seen, for_targets, simple_returns, config_pool
                )
                if first is not None:
                    return first
        # Config-pool fallback: resolve bare variable names like DATABASE_NAME.
        if config_pool:
            values = config_pool.get(node.id)
            if values:
                return next(iter(values))
        return None
    if isinstance(node, ast.Attribute):
        for av in assignments.get(node.attr, []):
            r = _signature_from_node(
                av, assignments, depth + 1, _seen, for_targets, simple_returns, config_pool
            )
            if r:
                return r
        # Config-pool fallback for attribute names.
        if config_pool:
            values = config_pool.get(node.attr)
            if values:
                return next(iter(values))
        return None

    # Subscript: ``cfg["KEY"]`` or ``cfg[k]`` where k traces to a literal.
    # A2: Try to resolve the actual dict value first (literal dict lookup);
    # fall back to using the literal key as a fingerprint (original behaviour).
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            d = _resolve_to_dict(node.value, assignments)
            if d is not None and sl.value in d:
                val_sig = _signature_from_node(
                    d[sl.value], assignments, depth + 1, _seen, for_targets, simple_returns
                )
                if val_sig:
                    return val_sig
            return sl.value
        # Variable-key: trace the slice back through assignments.
        if isinstance(sl, (ast.Name, ast.Attribute)):
            key_sig = _signature_from_node(
                sl, assignments, depth + 1, _seen, for_targets, simple_returns, config_pool
            )
            if key_sig:
                return key_sig
        return None

    # String concatenation with +: recurse on both sides and join.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _signature_from_node(
            node.left, assignments, depth + 1, _seen, for_targets, simple_returns
        )
        right = _signature_from_node(
            node.right, assignments, depth + 1, _seen, for_targets, simple_returns
        )
        parts_b = [p for p in (left, right) if p]
        if not parts_b:
            return None
        return "".join(parts_b)

    # A2: pathlib.Path division — ``Path("base") / "segment"`` → ``"base/segment"``.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _signature_from_node(
            node.left, assignments, depth + 1, _seen, for_targets, simple_returns
        )
        right = _signature_from_node(
            node.right, assignments, depth + 1, _seen, for_targets, simple_returns
        )
        if left and right:
            return left.rstrip("/") + "/" + right.lstrip("/")
        if left:
            return left
        return None

    # A2: Ternary expression — try the true-branch first, fall back to else.
    # Callers wanting BOTH branches should use ``_enumerate_ternary_signatures``.
    if isinstance(node, ast.IfExp):
        body_sig = _signature_from_node(
            node.body, assignments, depth + 1, _seen, for_targets, simple_returns
        )
        if body_sig:
            return body_sig
        return _signature_from_node(
            node.orelse, assignments, depth + 1, _seen, for_targets, simple_returns
        )

    return None


def _first_literal_from_iterable(
    it: ast.AST,
    assignments: dict[str, list[ast.AST]],
    depth: int,
    _seen: set[int],
    for_targets: dict[str, ast.AST] | None,
    simple_returns: dict[str, ast.AST] | None = None,
    config_pool: dict[str, set[str]] | None = None,
) -> str | None:
    """Return the first element's signature from a literal list/tuple/set."""
    if isinstance(it, (ast.List, ast.Tuple, ast.Set)):
        for elt in it.elts:
            sub = _signature_from_node(elt, assignments, depth, _seen, for_targets, simple_returns, config_pool)
            if sub:
                return sub
    return None


# ---------------------------------------------------------------------------
# Builder-chain walker (pattern 2a). Given a terminal ``.load()`` / ``.save()``
# call, walk BACKWARDS along the receiver chain and yield ``(option_key,
# option_value_node)`` pairs from every ``.option("k", v)`` or
# ``.options({...})`` we encounter. Also yields the format-hint from any
# ``.format("connector")`` in the chain (pattern 2d).
# ---------------------------------------------------------------------------


def _walk_reader_chain(
    call: ast.Call,
) -> tuple[list[tuple[str, ast.AST]], str | None]:
    """Walk backwards from a terminal call and collect option / format info.

    Returns ``(options, format_hint)`` where ``options`` is a list of
    ``(literal_key, value_node)`` tuples in source order (outer-first) and
    ``format_hint`` is the connector name from the FIRST ``.format(literal)``
    encountered (``None`` when the chain has no ``.format`` step). The
    walker only recognises ``.option(K, V)`` and ``.options(dict_literal)``
    with LITERAL keys — dynamic keys don't participate in the endpoint
    determination (see ``UnresolvedEdge`` recording elsewhere).

    Walk mechanics: the terminal call's ``func`` is an Attribute
    ``Attribute(value=<inner>, attr='load')``. Each intermediate call
    (``.option``, ``.format``, ``.options``) has the shape
    ``Call(func=Attribute(value=<even-inner>, attr='option'))``. So we
    alternate between an Attribute node and an intermediate Call, unwrapping
    the Call's ``func`` to reach the next Attribute in the receiver chain.
    """
    options: list[tuple[str, ast.AST]] = []
    format_hint: str | None = None
    cur: ast.AST | None = call.func  # start at the terminal .attr node
    while isinstance(cur, ast.Attribute):
        parent = cur.value
        if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute):
            attr = parent.func.attr
            if attr == "option" and len(parent.args) >= 2:
                key_node = parent.args[0]
                val_node = parent.args[1]
                if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                    options.append((key_node.value, val_node))
            elif attr == "options" and parent.args:
                arg0 = parent.args[0]
                if isinstance(arg0, ast.Dict):
                    for k, v in zip(arg0.keys, arg0.values):
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            options.append((k.value, v))
                # ``.options(**kwargs)`` — capture literal-value keyword args.
                for kw in parent.keywords:
                    if kw.arg and kw.value is not None:
                        options.append((kw.arg, kw.value))
            elif attr == "format" and parent.args:
                arg0 = parent.args[0]
                if (
                    format_hint is None
                    and isinstance(arg0, ast.Constant)
                    and isinstance(arg0.value, str)
                ):
                    format_hint = arg0.value
            cur = parent.func  # step to the NEXT Attribute in the chain
        else:
            cur = parent
    return options, format_hint


# ---------------------------------------------------------------------------
# A2: Ternary-branch enumeration helper.
# ---------------------------------------------------------------------------


def _enumerate_ternary_signatures(
    node: ast.AST,
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    simple_returns: dict[str, ast.AST] | None = None,
    config_pool: dict[str, set[str]] | None = None,
) -> list[str]:
    """Enumerate all resolvable branches of a ternary (``x if c else y``) node.

    Returns one raw signature per branch that resolves so BOTH arms of the
    conditional appear as separate sources/sinks rather than only the first.
    Nested ternaries are enumerated recursively.

    Callers should use this alongside the main ``_signature_from_node`` call
    (which returns the first-resolving branch) to cover the else branch.
    """
    if not isinstance(node, ast.IfExp):
        return []
    out: list[str] = []
    for branch in (node.body, node.orelse):
        if isinstance(branch, ast.IfExp):
            out.extend(
                _enumerate_ternary_signatures(branch, assignments, for_targets, simple_returns, config_pool)
            )
        else:
            sub = _signature_from_node(
                branch, assignments, for_targets=for_targets, simple_returns=simple_returns,
                config_pool=config_pool,
            )
            if sub:
                out.append(sub)
    return out


# ---------------------------------------------------------------------------
# A1 pattern handlers — new API families not covered by the original walker.
# ---------------------------------------------------------------------------


def _emit_or_unresolved_read(
    node: ast.Call,
    arg_node: ast.AST | None,
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    simple_returns: dict[str, ast.AST] | None,
    abs_path: str,
    sources: set[str],
    unresolved_reads: list[UnresolvedEdge],
    default_call_expr: str = "",
    config_pool: dict[str, set[str]] | None = None,
) -> None:
    """Resolve ``arg_node`` to a signature and add to sources; on failure record
    an UnresolvedEdge. Shared by the new A1 single-arg read handlers."""
    if arg_node is None:
        return
    raw = _signature_from_node(
        arg_node, assignments, for_targets=for_targets, simple_returns=simple_returns,
        config_pool=config_pool,
    )
    if raw:
        sig = _normalize_signature(raw)
        if sig:
            sources.add(sig)
            return
    try:
        call_expr = ast.unparse(node.func)
    except Exception:
        call_expr = default_call_expr
    try:
        arg_expr = ast.unparse(arg_node)
    except Exception:
        arg_expr = ""
    unresolved_reads.append(UnresolvedEdge(
        file=abs_path,
        line=getattr(node, "lineno", 0) or 0,
        kind="read",
        call_expr=_truncate(call_expr, _MAX_EXPR_IN_REASON),
        arg_expr=_truncate(arg_expr, _MAX_EXPR_IN_REASON),
        reason=_build_unresolved_reason("unresolved", arg_node, assignments),
    ))


def _emit_or_unresolved_write(
    node: ast.Call,
    arg_node: ast.AST | None,
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    simple_returns: dict[str, ast.AST] | None,
    abs_path: str,
    sinks: set[str],
    unresolved_writes: list[UnresolvedEdge],
    default_call_expr: str = "",
    config_pool: dict[str, set[str]] | None = None,
) -> None:
    """Resolve ``arg_node`` to a signature and add to sinks; on failure record
    an UnresolvedEdge. Shared by the new A1 single-arg write handlers."""
    if arg_node is None:
        return
    raw = _signature_from_node(
        arg_node, assignments, for_targets=for_targets, simple_returns=simple_returns,
        config_pool=config_pool,
    )
    if raw:
        sig = _normalize_signature(raw)
        if sig:
            sinks.add(sig)
            return
    try:
        call_expr = ast.unparse(node.func)
    except Exception:
        call_expr = default_call_expr
    try:
        arg_expr = ast.unparse(arg_node)
    except Exception:
        arg_expr = ""
    unresolved_writes.append(UnresolvedEdge(
        file=abs_path,
        line=getattr(node, "lineno", 0) or 0,
        kind="write",
        call_expr=_truncate(call_expr, _MAX_EXPR_IN_REASON),
        arg_expr=_truncate(arg_expr, _MAX_EXPR_IN_REASON),
        reason=_build_unresolved_reason("unresolved", arg_node, assignments),
    ))


def _handle_direct_table_call(
    node: ast.Call,
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    simple_returns: dict[str, ast.AST] | None,
    abs_path: str,
    sources: set[str],
    unresolved_reads: list[UnresolvedEdge],
    config_pool: dict[str, set[str]] | None = None,
) -> None:
    """A1.1: spark.table("name") — direct SparkSession table read.

    ``spark.read.table(name)`` is already handled by the main walker via
    ``_READ_TERMINAL_METHODS``; this handles the bare ``spark.table(name)``
    form where neither ``read`` nor ``readStream`` appears in the call chain.
    """
    _emit_or_unresolved_read(
        node, node.args[0] if node.args else None,
        assignments, for_targets, simple_returns, abs_path,
        sources, unresolved_reads, "spark.table", config_pool=config_pool,
    )


def _handle_rdd_read(
    node: ast.Call,
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    simple_returns: dict[str, ast.AST] | None,
    abs_path: str,
    sources: set[str],
    unresolved_reads: list[UnresolvedEdge],
    config_pool: dict[str, set[str]] | None = None,
) -> None:
    """A1.2: sc.textFile() / sc.binaryFiles() / etc. — SparkContext RDD reads.

    First positional arg is always the path / glob pattern.
    """
    _emit_or_unresolved_read(
        node, node.args[0] if node.args else None,
        assignments, for_targets, simple_returns, abs_path,
        sources, unresolved_reads, "sc.textFile", config_pool=config_pool,
    )


def _handle_delta_table_call(
    node: ast.Call,
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    simple_returns: dict[str, ast.AST] | None,
    abs_path: str,
    sources: set[str],
    unresolved_reads: list[UnresolvedEdge],
    config_pool: dict[str, set[str]] | None = None,
) -> None:
    """A1.3: DeltaTable.forPath(spark, path) / DeltaTable.forName(spark, name).

    Both class-methods take ``(spark_session, target)`` — the SECOND positional
    arg is the path or table name; the first is the session handle.
    """
    _emit_or_unresolved_read(
        node, node.args[1] if len(node.args) > 1 else None,
        assignments, for_targets, simple_returns, abs_path,
        sources, unresolved_reads, "DeltaTable.forPath", config_pool=config_pool,
    )


def _handle_pandas_io(
    node: ast.Call,
    attr_name: str,
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    simple_returns: dict[str, ast.AST] | None,
    abs_path: str,
    sources: set[str],
    sinks: set[str],
    unresolved_reads: list[UnresolvedEdge],
    unresolved_writes: list[UnresolvedEdge],
    config_pool: dict[str, set[str]] | None = None,
) -> None:
    """A1.4: pd.read_X() and df.to_X() pandas I/O calls.

    For all methods the first positional arg is the path or table name.
    """
    is_read = attr_name in _PANDAS_READ_METHODS
    arg_node = node.args[0] if node.args else None
    if is_read:
        _emit_or_unresolved_read(
            node, arg_node, assignments, for_targets, simple_returns, abs_path,
            sources, unresolved_reads, attr_name, config_pool=config_pool,
        )
    else:
        _emit_or_unresolved_write(
            node, arg_node, assignments, for_targets, simple_returns, abs_path,
            sinks, unresolved_writes, attr_name, config_pool=config_pool,
        )


def _handle_jdbc_call(
    node: ast.Call,
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    simple_returns: dict[str, ast.AST] | None,
    abs_path: str,
    sources: set[str],
    unresolved_reads: list[UnresolvedEdge],
    config_pool: dict[str, set[str]] | None = None,
) -> None:
    """A1.5: spark.read.jdbc(url, table, ...) — second positional arg is the table.

    The first arg is the JDBC connection URL and is intentionally ignored as a
    data-endpoint signature; only the table name indicates WHAT is being read.
    Falls back to the ``dbtable`` keyword arg when the positional table arg is
    absent (some callers use named args).
    """
    arg_node: ast.AST | None = node.args[1] if len(node.args) > 1 else None
    if arg_node is None:
        for kw in node.keywords:
            if kw.arg in ("dbtable", "table"):
                arg_node = kw.value
                break
    _emit_or_unresolved_read(
        node, arg_node, assignments, for_targets, simple_returns, abs_path,
        sources, unresolved_reads, "spark.read.jdbc", config_pool=config_pool,
    )


def _handle_catalog_sink(
    node: ast.Call,
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    simple_returns: dict[str, ast.AST] | None,
    abs_path: str,
    sinks: set[str],
    unresolved_writes: list[UnresolvedEdge],
    config_pool: dict[str, set[str]] | None = None,
) -> None:
    """A1.6: spark.catalog.createTable("name") — catalog table creation is a sink."""
    _emit_or_unresolved_write(
        node, node.args[0] if node.args else None,
        assignments, for_targets, simple_returns, abs_path,
        sinks, unresolved_writes, "spark.catalog.createTable", config_pool=config_pool,
    )


# ---------------------------------------------------------------------------
# SQL passthrough (pattern 2c) — lazy sqlglot import.
# ---------------------------------------------------------------------------


def _sql_edges_from_string(sql_text: str) -> tuple[list[str], list[str], str | None]:
    """Parse ``sql_text`` with sqlglot and return ``(sources, sinks, err)``.

    ``sources`` / ``sinks`` are un-normalized table-name strings. ``err`` is
    ``None`` on success or a short diagnostic when sqlglot can't parse the
    input (e.g. after f-string placeholder stripping the query is
    syntactically broken).

    sqlglot import is lazy: environments without it get ``err="sqlglot not
    available"`` and the caller records the whole read/write as unresolved.
    """
    try:
        import sqlglot  # type: ignore[import]
        from sqlglot import exp  # type: ignore[import]
    except Exception:
        return [], [], "sqlglot not available"
    # Strip empty ``{}`` placeholders so an f-string-derived query still
    # parses when the non-literal segments were unresolvable.
    cleaned = re.sub(r"\{[^{}]*\}", "", sql_text).strip()
    if not cleaned:
        return [], [], "empty SQL after placeholder stripping"
    try:
        statements = sqlglot.parse(cleaned, read="spark")
    except Exception as e:  # pragma: no cover - depends on sqlglot version
        return [], [], f"sqlglot parse error: {type(e).__name__}"
    sources: list[str] = []
    sinks: list[str] = []
    for stmt in statements:
        if stmt is None:
            continue
        # INSERT statements — target is a sink; select-side tables are
        # sources. sqlglot exposes the target via ``stmt.this``.
        if isinstance(stmt, exp.Insert):
            target = stmt.this
            if isinstance(target, exp.Table):
                sinks.append(target.name)
            # The rest of the tree still contains sources.
        if isinstance(stmt, exp.Create):
            # CREATE TABLE X AS SELECT ... — target is a sink.
            target = stmt.this
            if isinstance(target, exp.Table):
                sinks.append(target.name)
        # All tables reachable from the statement, minus the ones already
        # counted as sinks. sqlglot's ``find_all(exp.Table)`` walks into
        # subqueries and joins for us.
        for tbl in stmt.find_all(exp.Table):
            name = tbl.name
            if not name:
                continue
            # Qualify with schema if present.
            full = ".".join(
                p for p in (tbl.args.get("db") and tbl.args["db"].name, name) if p
            )
            candidate = full or name
            # Skip if this table IS the INSERT/CREATE target.
            if candidate in sinks and tbl is (stmt.this if isinstance(stmt, (exp.Insert, exp.Create)) else None):
                continue
            sources.append(candidate)
    # Deduplicate while preserving order.
    def _uniq(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for s in seq:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    return _uniq(sources), _uniq(sinks), None


# ---------------------------------------------------------------------------
# Unresolved-reason derivation (Part 3). The point: reasons describe what
# the walker actually saw, not from a fixed enum. Two workloads with
# different unresolved patterns get different, specific reasons.
# ---------------------------------------------------------------------------


_MAX_REASON_LEN: int = 200
_MAX_EXPR_IN_REASON: int = 120


def _truncate(s: str, limit: int) -> str:
    """Shorten ``s`` for display with an ellipsis marker."""
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "…"


def _describe_ast_shape(
    node: ast.AST,
    assignments: dict[str, list[ast.AST]] | None = None,
) -> str:
    """Human-readable structural summary of an AST node.

    Used to derive :class:`UnresolvedEdge` reasons dynamically from the
    node the walker gave up on. Never returns a hardcoded reason string —
    every branch describes what the walker actually saw.
    """
    try:
        unparsed = ast.unparse(node)
    except Exception:
        unparsed = ""
    if isinstance(node, ast.Call):
        try:
            fn_expr = ast.unparse(node.func)
        except Exception:
            fn_expr = type(node.func).__name__
        return f"call to {_truncate(fn_expr, 80)}"
    if isinstance(node, ast.Subscript):
        try:
            slice_expr = ast.unparse(node.slice)
        except Exception:
            slice_expr = type(node.slice).__name__
        # A LITERAL slice would have been resolved by ``_signature_from_node``;
        # if we're here the slice is dynamic.
        return f"subscript with dynamic key {_truncate(slice_expr, 80)}"
    if isinstance(node, ast.IfExp):
        return "conditional expression (if-else)"
    if isinstance(node, ast.Name):
        if assignments is not None and not assignments.get(node.id):
            return f"reference to '{node.id}' with no traceable assignment"
        return f"reference to '{node.id}' whose assignment did not resolve"
    if isinstance(node, ast.Attribute):
        if assignments is not None and not assignments.get(node.attr):
            return f"attribute '{node.attr}' with no traceable assignment"
        return f"attribute '{node.attr}' whose assignment did not resolve"
    if isinstance(node, ast.ListComp):
        return "comprehension expression (list)"
    if isinstance(node, ast.DictComp):
        return "comprehension expression (dict)"
    if isinstance(node, ast.SetComp):
        return "comprehension expression (set)"
    if isinstance(node, ast.GeneratorExp):
        return "comprehension expression (generator)"
    if isinstance(node, ast.Lambda):
        return "lambda expression"
    return f"node type {type(node).__name__}"


def _resolve_failure_target(
    node: ast.AST,
    assignments: dict[str, list[ast.AST]],
    depth: int = 0,
) -> ast.AST:
    """Return the deepest sub-node that best explains the resolution failure.

    ``_signature_from_node`` walks Name → assignment expression on failure.
    If a Name has an assignment that itself doesn't resolve, the more
    interesting node — for the engineer's purposes — is the assignment
    RHS, not the surface-level Name. We follow the same walk here so the
    reason string names the actual dynamic sub-expression (e.g. the
    ``cfg[get_key()]`` subscript with a Call slice, not the harmless
    ``path`` on the read side).

    Recursion cap kept low (5) so pathological workloads can't blow the
    stack. Returns the original node if no assignment chain resolves.
    """
    if depth > 5:
        return node
    if isinstance(node, ast.Name):
        avs = assignments.get(node.id, [])
        for av in avs:
            # If this assignment itself has a static resolution we would
            # have found it already — descend to see the ORIGINAL failing
            # shape.
            deeper = _resolve_failure_target(av, assignments, depth + 1)
            return deeper
        return node
    if isinstance(node, ast.Attribute):
        avs = assignments.get(node.attr, [])
        for av in avs:
            deeper = _resolve_failure_target(av, assignments, depth + 1)
            return deeper
        return node
    return node



def _build_unresolved_reason(
    prefix: str,
    node: ast.AST | None,
    assignments: dict[str, list[ast.AST]] | None = None,
) -> str:
    """Compose a reason string with the raw expression appended.

    Result is capped at ``_MAX_REASON_LEN`` chars (the raw expression is
    truncated first so the shape description always survives).

    When ``node`` is a Name/Attribute whose assignments the walker already
    tried and failed on, we descend to the deepest failing sub-node via
    :func:`_resolve_failure_target` so the reason describes the ACTUAL
    dynamic sub-expression the walker choked on (e.g. the subscript with
    a Call slice) rather than the harmless surface-level Name that just
    happens to bind to it.
    """
    if node is None:
        return _truncate(f"{prefix}: node is None", _MAX_REASON_LEN)
    focus = node
    if assignments is not None and isinstance(node, (ast.Name, ast.Attribute)):
        focus = _resolve_failure_target(node, assignments)
    shape = _describe_ast_shape(focus, assignments)
    try:
        raw = ast.unparse(focus)
    except Exception:
        raw = ""
    raw = _truncate(raw, _MAX_EXPR_IN_REASON)
    reason = f"{prefix}: argument is {shape}"
    if raw:
        reason = f"{reason} [{raw}]"
    return _truncate(reason, _MAX_REASON_LEN)


# ---------------------------------------------------------------------------
# Per-file entry point. Returns:
#   sources, sinks — normalized signature sets (existing contract).
#   unresolved_reads, unresolved_writes — list[UnresolvedEdge] with dynamic
#     reasons. Caller (scan_codebase) rebases ``file`` to the workload-
#     relative path before handing to the report.
# ---------------------------------------------------------------------------


def _looks_like_call_kind(
    attr_name: str, chain: set[str]
) -> tuple[bool, bool]:
    is_read = attr_name in _READ_TERMINAL_METHODS and (
        "read" in chain or "readStream" in chain
    )
    is_write = attr_name in _WRITE_TERMINAL_METHODS and (
        "write" in chain or "writeStream" in chain
    )
    return is_read, is_write


# ``_collect_call_site_args`` walks the tree, finds every ``def``'d function,
# then finds every direct call to that function in the same file, and maps
# ``param_name → first_literal_value_seen_across_call_sites``.  The result
# is merged INTO ``assignments`` before the main walk so that parameter names
# resolve the same way any other variable would.
# ---------------------------------------------------------------------------


def _collect_call_site_args(
    tree: ast.Module,
) -> dict[str, list[ast.AST]]:
    """Return synthetic assignments built from literal call-site arguments.

    For each function defined in ``tree``, find all direct call sites in the
    same file.  For each call, map positional and keyword arguments to the
    corresponding parameter name.  Only literal (``ast.Constant``) and simple
    f-string/concatenation nodes are included — dynamic values are skipped.

    The result is an ``assignments``-shaped dict: ``{param_name: [ast_nodes]}``
    meant to be merged with the real ``assignments`` dict so
    ``_signature_from_node`` can resolve parameter names inside helper
    functions.
    """
    func_params: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in node.args.args]
            if node.name and params:
                func_params[node.name] = params

    if not func_params:
        return {}

    call_site_args: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            fn_name = func.id
        elif isinstance(func, ast.Attribute):
            fn_name = func.attr
        else:
            continue
        params = func_params.get(fn_name)
        if not params:
            continue
        for i, arg in enumerate(node.args):
            if i >= len(params):
                break
            call_site_args.setdefault(params[i], []).append(arg)
        for kw in node.keywords:
            if kw.arg and kw.arg in params:
                call_site_args.setdefault(kw.arg, []).append(kw.value)
    return call_site_args


def _extract_path_signatures(
    abs_path: str,
    config_pool: dict[str, set[str]] | None = None,
) -> tuple[set[str], set[str], list[UnresolvedEdge], list[UnresolvedEdge]]:
    """AST pass returning normalized signatures + unresolved diagnostics.

    Returns ``(source_signatures, sink_signatures, unresolved_reads,
    unresolved_writes)``.

    The two signature sets are the same contract as before this refactor —
    normalized-fingerprint strings, one per read/write call whose argument
    yields a non-empty fingerprint after normalization. The two unresolved
    lists carry an :class:`UnresolvedEdge` per call site that the walker
    could NOT resolve; each has a dynamic ``reason`` describing the AST
    node the walker stopped at.

    Patterns covered (original + A1/A2 extensions):
      1. Bare positional-arg read/write (existing behaviour).
      2. Builder ``.option("path", x).load()`` — pattern 2a.
      3. Variable-key subscript inside the arg — pattern 2b.
      4. ``spark.sql(SQL_STRING)`` — pattern 2c (via sqlglot).
      5. Reader ``.format("delta").load(...)`` — pattern 2d.
      6. Loop-generated paths — pattern 2e.
      A1.1. spark.table("name") — direct SparkSession table read.
      A1.2. sc.textFile() / sc.binaryFiles() / etc. — SparkContext RDD reads.
      A1.3. DeltaTable.forPath(spark, path) / DeltaTable.forName(spark, name).
      A1.4. pd.read_X() / df.to_X() — pandas I/O.
      A1.5. spark.read.jdbc(url, table) — second arg is the table name.
      A1.6. spark.catalog.createTable("name") — catalog table creation (sink).
      A2.  Ternary-branch enumeration (both arms emitted as separate sigs).
    """
    try:
        src = Path(abs_path).read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src)
    except Exception:
        return set(), set(), [], []

    assignments = _collect_assignments(tree)
    # Merge 1-hop call-site literals for function parameter names.
    for param, nodes in _collect_call_site_args(tree).items():
        if param not in assignments:
            assignments[param] = nodes
        else:
            assignments[param] = assignments[param] + nodes
    # 2nd-hop expansion: if a call-site arg is itself a Name/Attribute that
    # landed in assignments, replace it with the concrete nodes it resolves to.
    for param in list(assignments.keys()):
        expanded: list[ast.AST] = []
        for n in assignments[param]:
            if isinstance(n, ast.Name) and n.id in assignments:
                expanded.extend(
                    x for x in assignments[n.id] if not isinstance(x, ast.Name)
                )
            elif isinstance(n, ast.Attribute) and n.attr in assignments:
                expanded.extend(
                    x for x in assignments[n.attr] if not isinstance(x, ast.Name)
                )
            else:
                expanded.append(n)
        if expanded:
            assignments[param] = expanded
    for_targets = _collect_for_targets(tree)
    simple_returns = _collect_simple_returns(tree)

    sources: set[str] = set()
    sinks: set[str] = set()
    unresolved_reads: list[UnresolvedEdge] = []
    unresolved_writes: list[UnresolvedEdge] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        attr_name = func.attr
        chain = _attr_chain_names(func)

        # ---- Pattern 2c: spark.sql(...) --------------------------------
        if attr_name == "sql" and ("spark" in chain or "session" in chain):
            _handle_sql_call(
                node, assignments, for_targets, simple_returns, abs_path,
                sources, sinks, unresolved_reads, config_pool=config_pool,
            )
            continue

        # ---- A1.1: spark.table("name") — direct SparkSession table read ----
        if (attr_name == "table"
                and not ("read" in chain or "readStream" in chain or "write" in chain)
                and ("spark" in chain or "session" in chain or "ss" in chain)):
            _handle_direct_table_call(
                node, assignments, for_targets, simple_returns, abs_path,
                sources, unresolved_reads, config_pool=config_pool,
            )
            continue

        # ---- A1.2: sc.textFile / sc.binaryFiles / etc. — RDD reads --------
        if (attr_name in _SC_READ_METHODS and (
                "sc" in chain or "sparkContext" in chain
                or "_sc" in chain or "spark_context" in chain)):
            _handle_rdd_read(
                node, assignments, for_targets, simple_returns, abs_path,
                sources, unresolved_reads, config_pool=config_pool,
            )
            continue

        # ---- A1.3: DeltaTable.forPath / forName ---------------------------
        if attr_name in ("forPath", "forName") and "DeltaTable" in chain:
            _handle_delta_table_call(
                node, assignments, for_targets, simple_returns, abs_path,
                sources, unresolved_reads, config_pool=config_pool,
            )
            continue

        # ---- A1.4: Pandas reads — pd.read_X() ----------------------------
        if (attr_name in _PANDAS_READ_METHODS
                and ("pd" in chain or "pandas" in chain)):
            _handle_pandas_io(
                node, attr_name, assignments, for_targets, simple_returns, abs_path,
                sources, sinks, unresolved_reads, unresolved_writes,
                config_pool=config_pool,
            )
            continue

        # ---- A1.4: Pandas writes — df.to_X() (any receiver) --------------
        if attr_name in _PANDAS_WRITE_METHODS:
            _handle_pandas_io(
                node, attr_name, assignments, for_targets, simple_returns, abs_path,
                sources, sinks, unresolved_reads, unresolved_writes,
                config_pool=config_pool,
            )
            continue

        # ---- A1.5: spark.read.jdbc(url, table) — second arg is table ------
        if attr_name == "jdbc" and ("read" in chain or "readStream" in chain):
            _handle_jdbc_call(
                node, assignments, for_targets, simple_returns, abs_path,
                sources, unresolved_reads, config_pool=config_pool,
            )
            continue

        # ---- A1.6: spark.catalog.createTable / createExternalTable --------
        if attr_name in ("createTable", "createExternalTable") and "catalog" in chain:
            _handle_catalog_sink(
                node, assignments, for_targets, simple_returns, abs_path,
                sinks, unresolved_writes, config_pool=config_pool,
            )
            continue

        is_read, is_write = _looks_like_call_kind(attr_name, chain)
        if not (is_read or is_write):
            continue

        # ---- Compute the arg-yielded signature (patterns 1, 2b, 2e). ----
        # For pattern 2a, also look at option/format chain metadata.
        arg_node: ast.AST | None = node.args[0] if node.args else None

        # Enumerate loop-iterator concrete values if applicable — this
        # produces MULTIPLE signatures per call site.
        enumerated: list[str] = []
        if arg_node is not None:
            enumerated = _enumerate_loop_signatures(
                arg_node, assignments, for_targets, simple_returns, config_pool
            )

        # A2: Enumerate ternary branches when the direct arg is an IfExp.
        ternary_sigs: list[str] = []
        if arg_node is not None and isinstance(arg_node, ast.IfExp):
            ternary_sigs = _enumerate_ternary_signatures(
                arg_node, assignments, for_targets, simple_returns, config_pool
            )

        # Pattern 2a: gather option-keyed paths from the chain (for
        # .load() / .save() family — Databricks/Delta/JDBC style).
        option_sigs: list[tuple[str, str | None]] = []  # (raw_sig, key_used)
        option_unresolved: list[tuple[str, ast.AST]] = []
        if attr_name in ("load", "save"):
            options, _fmt = _walk_reader_chain(node)
            for key, val in options:
                if key not in _PATH_OPTION_KEYS:
                    continue
                sub = _signature_from_node(
                    val, assignments, for_targets=for_targets, simple_returns=simple_returns,
                    config_pool=config_pool,
                )
                if sub:
                    option_sigs.append((sub, key))
                else:
                    option_unresolved.append((key, val))

        # Emit signatures from positional arg + enumerated loop values +
        # ternary branches + option chain.
        emitted_any = False
        if arg_node is not None:
            raw = _signature_from_node(
                arg_node, assignments, for_targets=for_targets, simple_returns=simple_returns,
                config_pool=config_pool,
            )
            if raw:
                sig = _normalize_signature(raw)
                if sig:
                    (sources if is_read else sinks).add(sig)
                    emitted_any = True
            for r in enumerated:
                sig = _normalize_signature(r)
                if sig:
                    (sources if is_read else sinks).add(sig)
                    emitted_any = True
            for r in ternary_sigs:
                sig = _normalize_signature(r)
                if sig:
                    (sources if is_read else sinks).add(sig)
                    emitted_any = True
        for raw_sig, _k in option_sigs:
            sig = _normalize_signature(raw_sig)
            if sig:
                (sources if is_read else sinks).add(sig)
                emitted_any = True

        # If nothing survived normalization, record an UnresolvedEdge with
        # a dynamic reason describing what the walker saw.
        if not emitted_any:
            _record_unresolved(
                node, arg_node, option_unresolved, assignments, for_targets,
                is_read, is_write, abs_path,
                unresolved_reads, unresolved_writes,
            )

    return sources, sinks, unresolved_reads, unresolved_writes


def _handle_sql_call(
    node: ast.Call,
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    simple_returns: dict[str, ast.AST] | None,
    abs_path: str,
    sources: set[str],
    sinks: set[str],
    unresolved_reads: list[UnresolvedEdge],
    config_pool: dict[str, set[str]] | None = None,
) -> None:
    """Extract sources/sinks from a ``spark.sql(...)`` call (pattern 2c).

    SQL passthrough uses sqlglot when available. When the SQL text can't be
    statically resolved (function call, complex expression) OR sqlglot
    can't parse the resulting text, we record ONE unresolved-read entry
    with a dynamic reason. We record under ``unresolved_reads`` because
    ``spark.sql`` is more commonly a read entry point; SQL that mutates is
    handled by extracting the INSERT/CREATE target as a sink anyway.
    """
    arg_node = node.args[0] if node.args else None
    if arg_node is None:
        return
    raw = _signature_from_node(
        arg_node, assignments, for_targets=for_targets, simple_returns=simple_returns,
        config_pool=config_pool,
    )
    if not raw:
        try:
            call_expr = ast.unparse(node.func)
        except Exception:
            call_expr = "spark.sql"
        try:
            arg_expr = ast.unparse(arg_node)
        except Exception:
            arg_expr = ""
        reason = _build_unresolved_reason(
            "unresolved SQL text", arg_node, assignments
        )
        unresolved_reads.append(
            UnresolvedEdge(
                file=abs_path,
                line=getattr(node, "lineno", 0) or 0,
                kind="read",
                call_expr=call_expr,
                arg_expr=_truncate(arg_expr, _MAX_EXPR_IN_REASON),
                reason=reason,
            )
        )
        return
    sql_sources, sql_sinks, err = _sql_edges_from_string(raw)
    if err is not None and not sql_sources and not sql_sinks:
        try:
            call_expr = ast.unparse(node.func)
        except Exception:
            call_expr = "spark.sql"
        try:
            arg_expr = ast.unparse(arg_node)
        except Exception:
            arg_expr = ""
        reason = _truncate(
            f"unresolved: SQL parse failure — {err} [{_truncate(raw, 80)}]",
            _MAX_REASON_LEN,
        )
        unresolved_reads.append(
            UnresolvedEdge(
                file=abs_path,
                line=getattr(node, "lineno", 0) or 0,
                kind="read",
                call_expr=call_expr,
                arg_expr=_truncate(arg_expr, _MAX_EXPR_IN_REASON),
                reason=reason,
            )
        )
        return
    for tbl in sql_sources:
        sig = _normalize_signature(tbl)
        if sig:
            sources.add(sig)
    for tbl in sql_sinks:
        sig = _normalize_signature(tbl)
        if sig:
            sinks.add(sig)


# Enumeration cap for loop-generated signatures. Real workloads that iterate
# a config list of tables tend to have < 20 entries; higher counts almost
# always indicate a bug or a non-endpoint iteration and would just spam the
# signature set.
_LOOP_ENUMERATION_CAP: int = 32


def _enumerate_loop_signatures(
    arg_node: ast.AST,
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    simple_returns: dict[str, ast.AST] | None = None,
    config_pool: dict[str, set[str]] | None = None,
) -> list[str]:
    """If ``arg_node`` bottoms out at a Name bound to a for-loop iterator
    over a LITERAL list/tuple, return one signature per element.

    Only the direct-Name case is enumerated; nested references (e.g.
    ``f"{x}/data"``) don't loop-enumerate because we'd need to substitute
    the loop var into the surrounding expression for each element, which
    is beyond the walker's current scope. Callers still get ONE signature
    (the constant portion) for those via ``_signature_from_node`` above.
    """
    target_name: str | None = None
    if isinstance(arg_node, ast.Name):
        target_name = arg_node.id
    if target_name is None or target_name not in for_targets:
        return []
    # Skip if the loop var also has a real assignment — a real assignment
    # takes precedence and _signature_from_node already handles it.
    if assignments.get(target_name):
        return []
    it = for_targets[target_name]
    if not isinstance(it, (ast.List, ast.Tuple, ast.Set)):
        return []
    out: list[str] = []
    for elt in it.elts[:_LOOP_ENUMERATION_CAP]:
        sub = _signature_from_node(
            elt, assignments, for_targets=for_targets, simple_returns=simple_returns,
            config_pool=config_pool,
        )
        if sub:
            out.append(sub)
    return out


def _record_unresolved(
    node: ast.Call,
    arg_node: ast.AST | None,
    option_unresolved: list[tuple[str, ast.AST]],
    assignments: dict[str, list[ast.AST]],
    for_targets: dict[str, ast.AST],
    is_read: bool,
    is_write: bool,
    abs_path: str,
    unresolved_reads: list[UnresolvedEdge],
    unresolved_writes: list[UnresolvedEdge],
) -> None:
    """Compose the UnresolvedEdge record for a read/write call that yielded
    no usable signatures.

    The failing node is chosen in priority order:

    1. If there IS a positional arg and it's an untraceable name that also
       appears as a for-loop target with a non-literal iterable, that's the
       most informative failure point.
    2. Otherwise the positional arg itself.
    3. Otherwise the first unresolved option value (for builder-chain
       ``.load()`` calls where the positional arg is empty).
    """
    try:
        call_expr = ast.unparse(node.func)
    except Exception:
        call_expr = "<unparsable>"
    kind = "read" if is_read else ("write" if is_write else "read")

    # Pick the target node whose shape best explains the failure.
    focus: ast.AST | None = arg_node
    focus_prefix = "unresolved"

    if isinstance(arg_node, ast.Name) and arg_node.id in for_targets:
        it = for_targets[arg_node.id]
        if not isinstance(it, (ast.List, ast.Tuple, ast.Set)):
            focus = it
            focus_prefix = f"unresolved: loop over '{arg_node.id}'"

    if focus is None and option_unresolved:
        key, val = option_unresolved[0]
        focus = val
        focus_prefix = f"unresolved: builder option '{key}'"

    if focus is None:
        # No positional arg AND no unresolved option — nothing to say beyond
        # the call itself. This can happen for ``.load()`` with no args and
        # no matching ``.option("path", ...)`` in the chain (bare call). We
        # still record so the engineer sees the call.
        reason = _truncate(f"{focus_prefix}: call has no path argument", _MAX_REASON_LEN)
        try:
            arg_expr = ast.unparse(node)
        except Exception:
            arg_expr = ""
    else:
        reason = _build_unresolved_reason(focus_prefix, focus, assignments)
        try:
            arg_expr = ast.unparse(arg_node) if arg_node is not None else ast.unparse(focus)
        except Exception:
            arg_expr = ""

    edge = UnresolvedEdge(
        file=abs_path,
        line=getattr(node, "lineno", 0) or 0,
        kind=kind,
        call_expr=_truncate(call_expr, _MAX_EXPR_IN_REASON),
        arg_expr=_truncate(arg_expr, _MAX_EXPR_IN_REASON),
        reason=reason,
    )
    if is_write:
        unresolved_writes.append(edge)
    else:
        unresolved_reads.append(edge)


# ---------------------------------------------------------------------------
# Back-compat helpers. ``_extract_path_uris_and_sigs`` used to be a sister of
# ``_extract_path_signatures`` returning (raw_uri, normalized_signature)
# pairs. Retained here so scan_codebase's per-file endpoint discovery keeps
# working unchanged.
# ---------------------------------------------------------------------------


def _extract_path_uris_and_sigs(
    abs_path: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Same call surface as before: returns ``(reads, writes)`` where each
    element is ``(raw_uri_or_fingerprint, normalized_signature)``.

    Delegates to the new walker so any pattern coverage improvements here
    also benefit the endpoint-details panel in the assessment report.
    Unresolved edges are dropped from this back-compat view — callers that
    want the diagnostics should call :func:`_extract_path_signatures`
    directly.
    """
    try:
        src = Path(abs_path).read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src)
    except Exception:
        return [], []
    assignments = _collect_assignments(tree)
    for_targets = _collect_for_targets(tree)
    simple_returns = _collect_simple_returns(tree)
    reads: list[tuple[str, str]] = []
    writes: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        attr_name = func.attr
        chain = _attr_chain_names(func)
        is_read, is_write = _looks_like_call_kind(attr_name, chain)
        if not (is_read or is_write) or not node.args:
            continue
        raw = _signature_from_node(
            node.args[0], assignments, for_targets=for_targets, simple_returns=simple_returns
        )
        if not raw:
            continue
        sig = _normalize_signature(raw)
        if not sig:
            continue
        if is_read:
            reads.append((raw, sig))
        if is_write:
            writes.append((raw, sig))
    return reads, writes


__all__ = [
    "UnresolvedEdge",
    "_URI_SCHEME_PREFIXES",
    "_SIGNATURE_NOISE_WORDS",
    "_PATH_OPTION_KEYS",
    "_STR_TRIVIAL_PASSTHROUGH",
    "_SC_READ_METHODS",
    "_PANDAS_READ_METHODS",
    "_PANDAS_WRITE_METHODS",
    "_normalize_signature",
    "_signature_from_node",
    "_collect_assignments",
    "_collect_simple_returns",
    "_resolve_to_dict",
    "_attr_chain_names",
    "_collect_for_targets",
    "_enumerate_ternary_signatures",
    "_walk_reader_chain",
    "_sql_edges_from_string",
    "_describe_ast_shape",
    "_build_unresolved_reason",
    "_extract_path_signatures",
    "_extract_path_uris_and_sigs",
    "_collect_call_site_args",
]
