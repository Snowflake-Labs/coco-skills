"""Rewrite ``awsglue.transforms`` calls to native DataFrame operations, and
annotate the Glue transforms that have no safe one-liner equivalent.

Implements Glue recipes **G3** (``ResolveChoice``), **G6** (DynamicFrame /
DataFrame lifecycle), **G7** (``DynamicFrameCollection`` custom transforms) and
**G10** (``DropFields`` / ``SelectFields`` / ``RenameField`` / ``Join`` /
``Map`` / ``Relationalize`` / ``Unbox``) from
``references/python/glue-recipes.md``.

What it does
------------

Mechanical rewrites (EWI ``SPRKCNTPY3605-Fixed``)::

    ResolveChoice.apply(frame=F, choice="match_catalog")  ->  F
    DropFields.apply(frame=F, paths=["a", "b"])           ->  F.drop("a", "b")
    SelectFields.apply(frame=F, paths=["a", "b"])         ->  F.select("a", "b")
    RenameField.apply(frame=F, old_name="a", new_name="b")->  F.withColumnRenamed("a", "b")
    DynamicFrame.fromDF(df, gc, "ctx")                    ->  df
    <X>.toDF()                                            ->  <X>
    <X>.schema()                                          ->  <X>.schema

``ResolveChoice(choice="match_catalog")`` is a **pure no-op** on SCOS: choice
types only arise because a DynamicFrame defers schema resolution, and reading a
typed Snowflake table cannot produce one. ``toDF`` / ``fromDF`` are a pure
round-trip wrapper pair (G6) with no SCOS equivalent.

``schema`` is a **method** on ``DynamicFrame`` but a **property** on
``DataFrame`` (G6), so a zero-argument ``<X>.schema()`` becomes the attribute
access ``<X>.schema``. Leaving the parens in place raises
``TypeError: 'StructType' object is not callable`` at runtime — the canonical
occurrence is mid-expression, inside a comprehension
(``[f.name for f in dyf.schema().fields]``).

The receiver may be dotted (``awsglue.transforms.DropFields.apply(...)``); the
frame may be passed as ``frame=`` / ``frame1=`` or positionally.

Annotate-only (code left byte-identical, a ``# SCOS: TODO`` comment prepended)
for the shapes that cannot be mechanically converted without changing data or
guessing:

* ``ResolveChoice.apply`` with a ``choice`` other than ``"match_catalog"``
  (``"cast:long"``, ``"make_cols"``, ``"project:…"``) — those genuinely change
  the data, so the human must write the explicit
  ``df.withColumn(c, F.col(c).cast(<type>))``.
* ``Relationalize.apply`` / ``Unbox.apply`` — they flatten nested structures
  into *multiple* frames; convert explicitly with ``select`` + ``explode`` +
  ``F.col("s.*")`` and verify the resulting frame count.
* ``Join.apply`` — deliberately **annotate-only**. ``keys1`` / ``keys2`` are
  positional, per-side key lists, and Glue tolerates duplicate/renamed key
  columns that a native ``join(on=[...])`` would collapse or ambiguate. Getting
  the key alignment wrong silently changes the row count, so the recipe
  suggests ``a.join(b, on=…, how="inner")`` and leaves the authoring to the
  human rather than guessing.
* ``Map.apply`` — a Python UDF; prefer native column expressions. The comment
  carries the concrete G7 idiom (Glue's per-column
  ``after[c] if before is None or before[c] is None else before[c]`` is exactly
  ``F.coalesce(F.col("before")[c], F.col("after")[c])``).
* ``DynamicFrameCollection({...}, gc)`` / ``SelectFromCollection.apply`` — the
  Glue Custom Transform single-frame-collection ceremony (G7); collapse the node
  to a plain df-in / df-out function that returns the DataFrame directly.
* Any otherwise-rewritable transform whose ``paths=`` / ``old_name=`` /
  ``new_name=`` / ``choice=`` argument is not a static string literal, or whose
  frame argument is missing / not a plain receiver expression. Never guessed.

``.toDF()`` gating (the dangerous one)
--------------------------------------

``df.toDF(*cols)`` is **legitimate PySpark** — it renames columns — and recipe
G2's own output uses ``df.toDF(*[c.lower() for c in df.columns])``. So the
rewrite fires only when **both** hold:

1. the call has **zero arguments** (``.toDF()`` exactly), and
2. the module shows Glue evidence — an ``awsglue`` import, or a ``GlueContext``
   / ``DynamicFrame`` reference in the source text (cheap pre-scan).

If either fails the call is left completely alone, with **no** annotation.
The same module-level Glue-evidence gate protects the ``<X>.schema()`` ->
``<X>.schema`` rewrite, because other libraries legitimately expose a callable
``.schema()`` (pandera, marshmallow, graphene); and it protects the two
generically-named transforms, ``Map.apply`` and ``Join.apply``, so a
user-defined class called ``Map`` or ``Join`` in non-Glue code is never
annotated. The unambiguously Glue-specific names (``ResolveChoice``,
``DropFields``, ``SelectFields``, ``RenameField``, ``Relationalize``,
``Unbox``, ``DynamicFrame.fromDF``, ``DynamicFrameCollection``,
``SelectFromCollection``) fire without the evidence gate.

Trigger
-------

A ``SimpleStatementLine`` containing any of: ``<X>.apply(...)`` where ``X``
resolves to one of the Glue transform names above; ``DynamicFrame.fromDF(...)``;
``DynamicFrameCollection(...)``; or a zero-argument ``.toDF()`` / ``.schema()``
in a Glue-evidenced module.

Negative cases (must NOT trigger)
---------------------------------

* ``df.toDF(*[c.lower() for c in df.columns])`` — recipe G2's own output; has
  arguments, so it is left byte-identical with zero edits.
* ``df.toDF("a", "b")`` — legitimate PySpark column rename.
* ``.toDF()`` in a module with no Glue evidence.
* ``.schema(...)`` with **any** argument (a validation-library schema builder).
* ``.schema()`` in a module with no Glue evidence (pandera / marshmallow /
  graphene all expose a callable ``.schema()``).
* ``df.schema`` — already a property access, never re-touched.
* ``Map.apply`` / ``Join.apply`` in a module with no Glue evidence.
* Any ``.apply(...)`` whose receiver is not a known Glue transform name
  (``MyRule.apply(x)``, ``pandas_df.apply(fn)``).
* Plain PySpark (``spark.read.table(...).select(...)``).

Idempotency
-----------

Re-running on this recipe's own output is a byte-for-byte no-op with zero
edits: the rewritten forms contain no Glue trigger, and the annotate-only
statements are guarded by ``_annotate.comment_above_contains`` plus a
``leading_lines`` scan for ``RECIPE_ID``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "glue_transforms_to_dataframe_rewrite"
MIN_SCOS_VERSION = "0.4.0"
NOTEBOOK_SCOPE = "cell"

_CODE = "SPRKCNTPY3605"

# Cheap substring gate: every trigger shape mentions one of these tokens.
_GATE_TOKENS = (
    "ResolveChoice",
    "DropFields",
    "SelectFields",
    "RenameField",
    "Relationalize",
    "Unbox",
    "Join",
    "Map",
    "fromDF",
    "toDF",
    "schema",
    "DynamicFrameCollection",
    "SelectFromCollection",
)

# Transform names that are unambiguously Glue: no module-level evidence needed.
_UNAMBIGUOUS = frozenset(
    {
        "ResolveChoice",
        "DropFields",
        "SelectFields",
        "RenameField",
        "Relationalize",
        "Unbox",
        "SelectFromCollection",
    }
)
# Generically-named transforms: require module-level Glue evidence.
_EVIDENCE_GATED = frozenset({"Map", "Join"})

# Expression shapes safe to splice in as a method receiver without adding parens.
_SAFE_RECEIVER = (cst.Name, cst.Attribute, cst.Call, cst.Subscript)


def _fixed(msg: str) -> str:
    return f"# SCOS: [{_CODE}-Fixed] {RECIPE_ID}: {msg}"


def _todo(msg: str) -> str:
    return f"# SCOS: TODO - [{_CODE}-Fixed] {RECIPE_ID}: {msg}"


def _has_glue_evidence(source: str) -> bool:
    """Cheap module-level pre-scan for Glue provenance."""
    return (
        "awsglue" in source or "GlueContext" in source or "DynamicFrame" in source
    )


def _final_name(node: cst.CSTNode) -> Optional[str]:
    """Final identifier of a ``Name`` or dotted ``Attribute``."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute) and isinstance(node.attr, cst.Name):
        return node.attr.value
    return None


def _attr_call_name(call: cst.Call) -> Optional[str]:
    """Method name of a ``<recv>.<name>(...)`` call."""
    func = call.func
    if isinstance(func, cst.Attribute) and isinstance(func.attr, cst.Name):
        return func.attr.value
    return None


def _transform_name(call: cst.Call) -> Optional[str]:
    """For ``<X>.apply(...)`` return the final identifier of ``X``.

    Handles a dotted receiver: ``awsglue.transforms.DropFields.apply`` ->
    ``"DropFields"``.
    """
    if _attr_call_name(call) != "apply":
        return None
    return _final_name(call.func.value)  # type: ignore[union-attr]


def _split_args(call: cst.Call):
    """``(positional_values, {keyword: value}, has_star)``."""
    pos: list[cst.BaseExpression] = []
    kw: dict[str, cst.BaseExpression] = {}
    has_star = False
    for arg in call.args:
        if arg.star:
            has_star = True
            continue
        if arg.keyword is not None:
            kw[arg.keyword.value] = arg.value
        else:
            pos.append(arg.value)
    return pos, kw, has_star


def _pick(
    kw: dict, pos: list, name: str, index: int
) -> Optional[cst.BaseExpression]:
    """Keyword ``name`` if present, else positional ``index``, else None."""
    if name in kw:
        return kw[name]
    if len(pos) > index:
        return pos[index]
    return None


def _string_lit(node: Optional[cst.CSTNode]) -> Optional[cst.BaseExpression]:
    """The node itself iff it is a plain string literal, else None."""
    if isinstance(node, cst.SimpleString):
        return node
    return None


def _string_value(node: Optional[cst.CSTNode]) -> Optional[str]:
    if isinstance(node, cst.SimpleString):
        try:
            return node.evaluated_value  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            return None
    return None


def _string_elements(node: Optional[cst.CSTNode]) -> Optional[list]:
    """Non-empty list/tuple of string literals -> the literal nodes, else None."""
    if not isinstance(node, (cst.List, cst.Tuple)):
        return None
    out: list[cst.BaseExpression] = []
    for el in node.elements:
        if not isinstance(el, cst.Element):
            return None  # starred element
        lit = _string_lit(el.value)
        if lit is None:
            return None
        out.append(lit)
    return out or None


def _method_call(
    recv: cst.BaseExpression, name: str, args: list
) -> cst.Call:
    """``<recv>.<name>(<args...>)`` with default ``, `` separators."""
    return cst.Call(
        func=cst.Attribute(value=recv, attr=cst.Name(name)),
        args=[cst.Arg(a) for a in args],
    )


class _CallRewriter(cst.CSTTransformer):
    """Rewrite / flag Glue transform calls inside one statement subtree."""

    def __init__(self, *, glue_evidence: bool) -> None:
        super().__init__()
        self._glue = glue_evidence
        self.fixed: list[str] = []
        self.todos: list[str] = []

    # -- comment bookkeeping ------------------------------------------------
    def _add_fixed(self, msg: str) -> None:
        if msg not in self.fixed:
            self.fixed.append(msg)

    def _add_todo(self, msg: str) -> None:
        if msg not in self.todos:
            self.todos.append(msg)

    @property
    def changed(self) -> bool:
        return bool(self.fixed or self.todos)

    # -- main dispatch -----------------------------------------------------
    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        meth = _attr_call_name(updated_node)

        # G6: <X>.toDF()  ->  <X>   (zero-arg only, Glue-evidenced modules only)
        if meth == "toDF":
            if not self._glue or updated_node.args:
                return updated_node  # legitimate PySpark toDF(*cols): hands off
            recv = updated_node.func.value  # type: ignore[union-attr]
            if not isinstance(recv, _SAFE_RECEIVER):
                return updated_node
            self._add_fixed(
                "DynamicFrame.toDF() -> receiver "
                "(DynamicFrame/DataFrame round-trip has no SCOS equivalent)"
            )
            return recv

        # G6: <X>.schema()  ->  <X>.schema   (method on DynamicFrame, property
        # on DataFrame). Zero-arg only, Glue-evidenced modules only: pandera /
        # marshmallow / graphene all expose a legitimately callable .schema().
        if meth == "schema":
            if not self._glue or updated_node.args:
                return updated_node
            self._add_fixed(
                "DynamicFrame.schema() -> DataFrame.schema "
                "(a method on DynamicFrame but a PROPERTY on DataFrame; keeping "
                "the parens raises TypeError: 'StructType' object is not callable)"
            )
            return updated_node.func  # the <X>.schema Attribute

        # G6: DynamicFrame.fromDF(df, gc, "ctx")  ->  df
        if meth == "fromDF" and _final_name(
            updated_node.func.value  # type: ignore[union-attr]
        ) == "DynamicFrame":
            pos, kw, _star = _split_args(updated_node)
            frame = _pick(kw, pos, "dataframe", 0)
            if frame is not None and isinstance(frame, _SAFE_RECEIVER):
                self._add_fixed(
                    'DynamicFrame.fromDF(df, gc, "ctx") -> df '
                    "(round-trip wrapper dropped)"
                )
                return frame
            self._add_todo(
                "DynamicFrame.fromDF(...) with no statically-identifiable "
                "DataFrame argument; drop the wrapper and pass the DataFrame "
                "through directly"
            )
            return updated_node

        # G7: DynamicFrameCollection({...}, gc) -- Custom Transform ceremony
        if _final_name(updated_node.func) == "DynamicFrameCollection":
            self._add_todo(
                "DynamicFrameCollection({'CustomTransform': ...}, gc) is Glue "
                "Custom Transform ceremony with no SCOS equivalent; collapse the "
                "node to a plain df-in/df-out function that returns the "
                "DataFrame directly (return df)"
            )
            return updated_node

        name = _transform_name(updated_node)
        if name is None:
            return updated_node
        if name in _EVIDENCE_GATED and not self._glue:
            return updated_node
        if name not in _UNAMBIGUOUS and name not in _EVIDENCE_GATED:
            return updated_node

        pos, kw, _star = _split_args(updated_node)

        if name == "ResolveChoice":
            return self._resolve_choice(updated_node, pos, kw)
        if name in ("DropFields", "SelectFields"):
            return self._paths_transform(updated_node, pos, kw, name)
        if name == "RenameField":
            return self._rename_field(updated_node, pos, kw)
        if name in ("Relationalize", "Unbox"):
            self._add_todo(
                f"{name}.apply(...) flattens nested structures into MULTIPLE "
                f"frames and has no one-liner equivalent; convert explicitly "
                f'with select + explode + F.col("s.*") and verify the resulting '
                f"frame count"
            )
            return updated_node
        if name == "Join":
            self._add_todo(
                "Join.apply(frame1=a, frame2=b, keys1=[...], keys2=[...]) -> "
                'a.join(b, on=..., how="inner") -- NOT auto-rewritten: keys1/keys2 '
                "are positional per-side key lists, and a mismatched or duplicated "
                "key alignment silently changes the row count; author the join "
                "condition explicitly and verify counts"
            )
            return updated_node
        if name == "Map":
            self._add_todo(
                "Map.apply(frame=f, f=fn) runs a per-row Python UDF; prefer native "
                "column expressions -- Glue's "
                "'after[c] if before is None or before[c] is None else before[c]' is "
                'exactly F.coalesce(F.col("before")[c], F.col("after")[c])'
            )
            return updated_node
        if name == "SelectFromCollection":
            self._add_todo(
                "SelectFromCollection.apply(...) is Glue Custom Transform ceremony; "
                "collapse the single-frame collection to a plain df-in/df-out "
                "function and select on the one frame you want"
            )
            return updated_node
        return updated_node

    # -- per-transform handlers -------------------------------------------
    def _resolve_choice(
        self, call: cst.Call, pos: list, kw: dict
    ) -> cst.BaseExpression:
        frame = _pick(kw, pos, "frame", 0)
        # ``choice`` is read from the keyword ONLY: the real Glue signature is
        # ``apply(frame, specs=None, choice="", ...)``, so positional index 1 is
        # ``specs`` — reading it as ``choice`` would misinterpret the call.
        choice_val = _string_value(kw.get("choice"))
        if choice_val == "match_catalog":
            if frame is not None and isinstance(frame, _SAFE_RECEIVER):
                self._add_fixed(
                    'ResolveChoice.apply(choice="match_catalog") -> frame '
                    "(no-op: a typed Snowflake table cannot produce a "
                    "DynamicFrame choice type)"
                )
                return frame
            self._add_todo(
                'ResolveChoice.apply(choice="match_catalog") is a no-op on SCOS '
                "but the frame argument is missing or not a plain expression; "
                "replace the call with the frame by hand"
            )
            return call
        if choice_val is not None:
            self._add_todo(
                f'ResolveChoice.apply(choice="{choice_val}") CHANGES the data and '
                f"is not a no-op; write the explicit equivalent, e.g. "
                f"df.withColumn(c, F.col(c).cast(<type>)) for a cast: choice"
            )
            return call
        self._add_todo(
            "ResolveChoice.apply(...) with a non-literal or missing 'choice'; only "
            'choice="match_catalog" is a safe no-op -- resolve by hand '
            "(df.withColumn(c, F.col(c).cast(<type>)) for a cast)"
        )
        return call

    def _paths_transform(
        self, call: cst.Call, pos: list, kw: dict, name: str
    ) -> cst.BaseExpression:
        target = "drop" if name == "DropFields" else "select"
        frame = _pick(kw, pos, "frame", 0)
        paths = _string_elements(_pick(kw, pos, "paths", 1))
        if frame is not None and isinstance(frame, _SAFE_RECEIVER) and paths:
            self._add_fixed(
                f'{name}.apply(paths=["..."]) -> DataFrame.{target}("...")'
            )
            return _method_call(frame, target, paths)
        self._add_todo(
            f"{name}.apply(...) with a non-literal/missing 'paths' list or frame; "
            f"rewrite as DataFrame.{target}(<column names>) by hand"
        )
        return call

    def _rename_field(
        self, call: cst.Call, pos: list, kw: dict
    ) -> cst.BaseExpression:
        frame = _pick(kw, pos, "frame", 0)
        old = _string_lit(_pick(kw, pos, "old_name", 1))
        new = _string_lit(_pick(kw, pos, "new_name", 2))
        if (
            frame is not None
            and isinstance(frame, _SAFE_RECEIVER)
            and old is not None
            and new is not None
        ):
            self._add_fixed(
                'RenameField.apply(old_name="a", new_name="b") -> '
                'DataFrame.withColumnRenamed("a", "b")'
            )
            return _method_call(frame, "withColumnRenamed", [old, new])
        self._add_todo(
            "RenameField.apply(...) with a non-literal/missing 'old_name'/"
            "'new_name' or frame; rewrite as "
            "DataFrame.withColumnRenamed(<old>, <new>) by hand"
        )
        return call


def _already_annotated(stmt: cst.SimpleStatementLine) -> bool:
    for line in stmt.leading_lines:
        if line.comment is not None and RECIPE_ID in line.comment.value:
            return True
    return False


def _with_leading_comments(
    stmt: cst.SimpleStatementLine, comments: list
) -> cst.SimpleStatementLine:
    new_leading = list(stmt.leading_lines) + [
        cst.EmptyLine(comment=cst.Comment(c)) for c in comments
    ]
    return stmt.with_changes(leading_lines=tuple(new_leading))


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, *, source: str, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(source=source, **kw)
        self._glue = _has_glue_evidence(source)

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        start = self._line_of(original_node)
        if _already_annotated(updated_node) or _annotate.comment_above_contains(
            self._lines, start, RECIPE_ID
        ):
            return updated_node
        sub = _CallRewriter(glue_evidence=self._glue)
        new_stmt = updated_node.visit(sub)
        if not sub.changed:
            return updated_node
        assert isinstance(new_stmt, cst.SimpleStatementLine)
        comments = [_fixed(m) for m in sub.fixed] + [_todo(m) for m in sub.todos]
        self._record(
            start,
            f"glue transforms -> DataFrame ops "
            f"(fixed={len(sub.fixed)}, todo={len(sub.todos)})",
        )
        return _with_leading_comments(new_stmt, comments)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    if not any(tok in source for tok in _GATE_TOKENS):
        return _common.RecipeResult(source=source, edits=[])
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
