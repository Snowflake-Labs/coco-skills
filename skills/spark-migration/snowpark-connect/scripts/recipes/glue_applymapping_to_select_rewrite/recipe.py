"""Rewrite AWS Glue ``ApplyMapping.apply(frame=…, mappings=[…])`` into an
explicit ``df.select(F.col("`src`").cast(<spark_type>).alias("tgt"), …)``
projection, preserving Glue's project + rename + cast semantics in one call.

What it does
------------

``ApplyMapping`` does **three** things at once and all three must survive the
port (recipe G4 in ``references/python/glue-recipes.md``):

1. **Projects** — only the mapped columns remain; every unmapped column is
   dropped.
2. **Renames** — source name -> target name.
3. **Casts** — target Glue type; a failed cast yields ``null`` rather than
   raising, which Spark's ``cast`` also does, so the semantics line up.

A single ``select`` gives all three for free::

    Out = ApplyMapping.apply(frame=DyF, mappings=[
        ("src_id",   "string", "id",   "bigint"),
        ("src_name", "string", "name", "string"),
    ])
    ->
    Out = DyF.select(F.col("`src_id`").cast("long").alias("id"),
                     F.col("`src_name`").cast("string").alias("name"))

Three details are load-bearing:

* **Never** emit a ``withColumnRenamed`` / ``withColumn`` chain. That keeps the
  unmapped columns and silently changes the output schema — the exact failure
  mode G4 warns about. Only ``select`` reproduces the projection.
* Source names are wrapped in **backticks** inside the ``F.col(...)`` string.
  Glue column names routinely contain dots and spaces, which Spark would
  otherwise parse as a nested-field path: ``("a.b", "string", "ab", "string")``
  must become ``F.col("`a.b`").cast("string").alias("ab")``.
* Glue type names are not all Spark type names. ``bigint``->``long``,
  ``integer``->``int`` and ``null``->``string`` are the ones that actually bite;
  see ``_GLUE_TO_SPARK_TYPE``. Matching is case-insensitive on the Glue name and
  any unknown or parameterized type (``decimal(10,2)``) passes through unchanged.

When a rewrite happens the recipe injects ``from pyspark.sql import functions as
F`` once at the top of the processed unit, unless the module already binds ``F``
to ``pyspark.sql.functions``. If ``F`` is already bound to *something else* the
recipe refuses to rewrite (it would shadow the existing binding) and annotates
instead.

Trigger
-------

A call to ``ApplyMapping.apply(...)`` — keyword form
(``frame=<F>, mappings=<M>``), positional form (``apply(<F>, <M>)``), mixed
form, and a dotted receiver (``awsglue.transforms.ApplyMapping.apply(...)``).

The rewrite only fires when ``mappings`` is a **statically readable literal**
list/tuple whose every element is a 4-element tuple of string literals.

Negative cases (must NOT trigger)
---------------------------------

* No ``ApplyMapping`` token in the source (cheap substring gate).
* ``mappings`` is a variable, a call, or a comprehension.
* Any element of the literal is not a tuple, is not length 4, or contains a
  non-string-literal member.
* ``ApplyMapping.apply`` with no recoverable ``frame`` argument.
* ``F`` already bound in the module to a non-``pyspark.sql.functions`` value.

In every one of those cases the code is left **unchanged** and a
``# SCOS: TODO - [SPRKCNTPY3603-Fixed] …`` comment spelling out the required
target shape (projection via ``select``, backticked source names, Glue->Spark
type mapping) is prepended. Guessing here would silently change the output
schema, which is far worse than a TODO.

Idempotency
-----------

Re-running on the recipe's own output is a byte-for-byte no-op with zero edits:
the rewritten statement no longer contains ``ApplyMapping`` (so the substring
gate short-circuits, and per-statement detection finds nothing), and an
annotated-but-unchanged statement is skipped by the leading-comment
``RECIPE_ID`` check.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "glue_applymapping_to_select_rewrite"
MIN_SCOS_VERSION = "0.4.0"
NOTEBOOK_SCOPE = "cell"

_EWI = "SPRKCNTPY3603"
_F_IMPORT = "from pyspark.sql import functions as F"

# Glue type name -> Spark type name. Keys are lowercase; lookup is
# case-insensitive. Unknown / parameterized types pass through unchanged.
_GLUE_TO_SPARK_TYPE = {
    "bigint": "long",
    "integer": "int",
    "null": "string",
    # identity mappings, listed explicitly so the table doubles as the
    # documented supported surface
    "string": "string",
    "boolean": "boolean",
    "double": "double",
    "float": "float",
    "short": "short",
    "byte": "byte",
    "decimal": "decimal",
    "timestamp": "timestamp",
    "date": "date",
    "long": "long",
    "int": "int",
}

_FIXED_COMMENT = (
    f"# SCOS: [{_EWI}-Fixed] {RECIPE_ID}: ApplyMapping.apply(frame=…, mappings=[…]) "
    f"-> df.select(F.col(\"`src`\").cast(<spark_type>).alias(\"tgt\"), …); select "
    f"preserves ApplyMapping's projection semantics (only mapped columns survive)"
)

_TODO_COMMENT_LINES = (
    f"# SCOS: TODO - [{_EWI}-Fixed] {RECIPE_ID}: ApplyMapping.apply() mappings are not a "
    f"statically-readable literal list of 4-element string tuples, so the projection "
    f"could not be generated automatically.",
    f"# SCOS: TODO - [{_EWI}-Fixed] {RECIPE_ID}: Rewrite manually as a single select: "
    f"df.select(*[F.col(f\"`{{src}}`\").cast(_CAST.get(ttype, ttype)).alias(tgt) "
    f"for (src, _stype, tgt, ttype) in mappings]).",
    f"# SCOS: TODO - [{_EWI}-Fixed] {RECIPE_ID}: Requirements: (1) use select, NOT "
    f"withColumnRenamed/withColumn — ApplyMapping is a PROJECTION and drops every "
    f"unmapped column; (2) wrap each source name in backticks inside F.col(\"`…`\") "
    f"because Glue names contain dots/spaces Spark would read as a nested path; "
    f"(3) map Glue type names to Spark: bigint->long, integer->int, null->string "
    f"(string/boolean/double/float/short/byte/decimal/timestamp/date are identity).",
)


# ---------------------------------------------------------------------------
# static readers
# ---------------------------------------------------------------------------
def _dotted(node: cst.BaseExpression) -> str:
    """``a.b.c`` -> ``"a.b.c"``; anything else -> ``""``."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr.value}" if base else ""
    return ""


def _str_value(node: cst.BaseExpression) -> Optional[str]:
    """Python value of a string literal, else None."""
    if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
        try:
            return node.evaluated_value  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            return None
    return None


def _spark_type(glue_type: str) -> str:
    """Glue type name -> Spark type name (case-insensitive, pass-through)."""
    return _GLUE_TO_SPARK_TYPE.get(glue_type.strip().lower(), glue_type)


def _is_applymapping_apply(call: cst.Call) -> bool:
    """``ApplyMapping.apply(...)`` including a dotted receiver."""
    func = call.func
    if not (isinstance(func, cst.Attribute) and func.attr.value == "apply"):
        return False
    receiver = _dotted(func.value)
    return receiver == "ApplyMapping" or receiver.endswith(".ApplyMapping")


def _frame_and_mappings(
    call: cst.Call,
) -> tuple[Optional[cst.BaseExpression], Optional[cst.BaseExpression]]:
    """Recover the ``frame`` and ``mappings`` arguments.

    Handles the keyword form, the positional form ``apply(frame, mappings)``,
    and the mixed form. Returns ``(None, None)`` slots for anything absent.
    """
    frame: Optional[cst.BaseExpression] = None
    mappings: Optional[cst.BaseExpression] = None
    positional: list[cst.BaseExpression] = []
    for arg in call.args:
        if arg.star:
            # *args / **kwargs -> nothing statically recoverable
            return None, None
        if arg.keyword is None:
            positional.append(arg.value)
            continue
        if arg.keyword.value == "frame":
            frame = arg.value
        elif arg.keyword.value == "mappings":
            mappings = arg.value
    if frame is None and positional:
        frame = positional.pop(0)
    if mappings is None and positional:
        mappings = positional.pop(0)
    return frame, mappings


def _read_mappings(node: cst.BaseExpression) -> Optional[list[tuple[str, str, str, str]]]:
    """Statically read a literal list/tuple of 4-tuples of string literals.

    Returns None (do not rewrite) for a variable, a comprehension, a non-tuple
    element, a tuple of the wrong length, or a tuple with a non-literal member.
    """
    if not isinstance(node, (cst.List, cst.Tuple)):
        return None
    out: list[tuple[str, str, str, str]] = []
    for element in node.elements:
        if not isinstance(element, cst.Element):
            return None  # starred element
        value = element.value
        if not isinstance(value, (cst.Tuple, cst.List)):
            return None
        members: list[str] = []
        for inner in value.elements:
            if not isinstance(inner, cst.Element):
                return None
            text = _str_value(inner.value)
            if text is None:
                return None
            members.append(text)
        if len(members) != 4:
            return None
        out.append((members[0], members[1], members[2], members[3]))
    if not out:
        return None  # empty literal -> nothing to project; leave to a human
    return out


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------
def _select_call(
    frame: cst.BaseExpression, mappings: list[tuple[str, str, str, str]]
) -> cst.Call:
    """Build ``<frame>.select(F.col("`src`").cast("t").alias("tgt"), …)``."""
    args: list[cst.Arg] = []
    for src, _src_type, tgt, tgt_type in mappings:
        col = cst.Call(
            func=cst.Attribute(value=cst.Name("F"), attr=cst.Name("col")),
            args=[cst.Arg(cst.SimpleString(f'"`{src}`"'))],
        )
        cast = cst.Call(
            func=cst.Attribute(value=col, attr=cst.Name("cast")),
            args=[cst.Arg(cst.SimpleString(f'"{_spark_type(tgt_type)}"'))],
        )
        alias = cst.Call(
            func=cst.Attribute(value=cast, attr=cst.Name("alias")),
            args=[cst.Arg(cst.SimpleString(f'"{tgt}"'))],
        )
        args.append(cst.Arg(alias))
    return cst.Call(
        func=cst.Attribute(value=frame, attr=cst.Name("select")),
        args=args,
    )


def _with_leading_comments(
    stmt: cst.SimpleStatementLine, comments: tuple[str, ...] | list[str]
) -> cst.SimpleStatementLine:
    new_leading = list(stmt.leading_lines) + [
        cst.EmptyLine(comment=cst.Comment(c)) for c in comments
    ]
    return stmt.with_changes(leading_lines=tuple(new_leading))


# ---------------------------------------------------------------------------
# F binding analysis
# ---------------------------------------------------------------------------
class _FBindingScan(cst.CSTVisitor):
    """Classify how the name ``F`` is bound at module level.

    ``functions_as_F`` -- ``from pyspark.sql import functions as F`` or
    ``import pyspark.sql.functions as F`` is present.
    ``other_binding``  -- ``F`` is bound to something else (assignment or a
    different import alias); rewriting would shadow it.
    """

    def __init__(self) -> None:
        self.functions_as_F = False
        self.other_binding = False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        module = _dotted(node.module) if node.module is not None else ""
        if isinstance(node.names, cst.ImportStar):
            return
        for alias in node.names:
            asname = alias.asname
            bound = (
                asname.name.value
                if asname is not None and isinstance(asname.name, cst.Name)
                else None
            )
            if bound != "F":
                continue
            name = _dotted(alias.name)
            if module == "pyspark.sql" and name == "functions":
                self.functions_as_F = True
            else:
                self.other_binding = True

    def visit_Import(self, node: cst.Import) -> None:
        for alias in node.names:
            asname = alias.asname
            bound = (
                asname.name.value
                if asname is not None and isinstance(asname.name, cst.Name)
                else None
            )
            if bound != "F":
                continue
            if _dotted(alias.name) == "pyspark.sql.functions":
                self.functions_as_F = True
            else:
                self.other_binding = True

    def visit_Assign(self, node: cst.Assign) -> None:
        for target in node.targets:
            if isinstance(target.target, cst.Name) and target.target.value == "F":
                self.other_binding = True


def _scan_f_binding(source: str) -> _FBindingScan:
    scan = _FBindingScan()
    try:
        cst.parse_module(source).visit(scan)
    except Exception:  # noqa: BLE001
        pass
    return scan


# ---------------------------------------------------------------------------
# recipe
# ---------------------------------------------------------------------------
class _ApplyMappingRewriter(cst.CSTTransformer):
    """Rewrite every convertible ``ApplyMapping.apply(...)`` in a statement."""

    def __init__(self, *, allow_rewrite: bool) -> None:
        super().__init__()
        self._allow_rewrite = allow_rewrite
        self.rewrites = 0
        self.todo = False

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if not _is_applymapping_apply(updated_node):
            return updated_node
        frame, mappings_node = _frame_and_mappings(updated_node)
        if frame is None or mappings_node is None:
            self.todo = True
            return updated_node
        mappings = _read_mappings(mappings_node)
        if mappings is None or not self._allow_rewrite:
            self.todo = True
            return updated_node
        self.rewrites += 1
        return _select_call(frame, mappings)


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        scan = _scan_f_binding("\n".join(self._lines))
        # ``F`` bound to something other than pyspark.sql.functions -> refuse to
        # rewrite rather than shadow it.
        self._allow_rewrite = not scan.other_binding
        self._has_f_import = scan.functions_as_F
        self._need_import = False

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        start = self._line_of(original_node)
        if _annotate.comment_above_contains(self._lines, start, RECIPE_ID):
            return updated_node
        sub = _ApplyMappingRewriter(allow_rewrite=self._allow_rewrite)
        new_stmt = updated_node.visit(sub)
        if sub.rewrites == 0 and not sub.todo:
            return updated_node
        assert isinstance(new_stmt, cst.SimpleStatementLine)
        if sub.rewrites:
            self._need_import = True
            comments: tuple[str, ...] = (_FIXED_COMMENT,)
            note = f"ApplyMapping -> select projection ({sub.rewrites} call(s))"
        else:
            comments = _TODO_COMMENT_LINES
            note = "ApplyMapping with non-literal mappings -> TODO"
        self._record(start, note)
        return _with_leading_comments(new_stmt, comments)

    def leave_Module(  # type: ignore[override]
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        if not self._need_import or self._has_f_import:
            return updated_node
        import_stmt = cst.SimpleStatementLine(
            body=[
                cst.ImportFrom(
                    module=cst.Attribute(
                        value=cst.Name("pyspark"), attr=cst.Name("sql")
                    ),
                    names=[
                        cst.ImportAlias(
                            name=cst.Name("functions"),
                            asname=cst.AsName(name=cst.Name("F")),
                        )
                    ],
                )
            ]
        )
        return updated_node.with_changes(body=(import_stmt, *updated_node.body))


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    if "ApplyMapping" not in source:
        return _common.RecipeResult(source=source, edits=[])
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
