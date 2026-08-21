"""Enable SCOS UDTF compatibility mode when any ``@udtf`` decorator is
present in the module.

What it does
------------

PySpark UDTFs (``@udtf(returnType=...)`` from ``pyspark.sql.functions``)
don't have a direct Spark Connect protocol mapping; SCOS instead runs
them via a Snowflake UDTF wrapper. Several semantic differences
(``analyze()`` static-method lookup, ``terminate()`` ordering, table
arguments) require the per-session flag
``snowpark.compatibility_mode_for_udtf=True``. Forgetting to set this
results in workloads that "almost work" but silently drop terminate()
rows or fail to resolve ``analyze()`` for type inference.

Injection strategy (in order of preference)
-------------------------------------------

The recipe must NOT emit code that references an unbound name. We pick
the first viable injection site:

1. **Inside a builder function**: if any function body contains a
   ``<name> = snowpark_connect.init_spark_session()`` line (the form
   produced by ``spark_builder_drop_master_init_session_rewrite``, which
   runs alphabetically before this recipe), inject
   ``<name>.conf.set(...)`` immediately after that assignment. This is
   the most common shape after the builder rewrite and keeps the
   binding scope correct.
2. **Module-level ``spark = ...``**: if no builder function is present
   but there is a module-level ``spark = <expr>`` assignment, insert
   ``spark.conf.set(...)`` immediately after that assignment (or after
   the last adjacent module-level ``spark = ...``).
3. **Comment-only fallback**: if neither anchor is found, do NOT inject
   executable code — emit a single ``# SCOS-TODO`` comment at the top
   of the module so the LLM fixer can wire up the conf.set wherever the
   workload actually creates a session. Emitting unbound ``spark`` was
   the bug fixed in the e2e validation run on /tmp/scos_e2e.

Trigger
-------

The module contains at least one ``FunctionDef`` or ``ClassDef`` with
a decorator whose innermost name is ``udtf``. The injection is a
single line; it is **not** repeated per-decorator.

Negative cases (must NOT trigger)
---------------------------------

* Module has no ``@udtf`` decorators.
* The conf is already set (``<x>.conf.set("snowpark.compatibility_mode_for_udtf", ...)``
  literal already anywhere in the module).

Idempotency
-----------

Re-running is a no-op because the literal conf assignment is detected
as already present (the ``_module_already_sets_conf`` walk covers all
three injection sites).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "udtf_enable_compatibility_mode_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_CONF_KEY = "snowpark.compatibility_mode_for_udtf"
_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY2600-Fixed] {RECIPE_ID}: @udtf decorator detected; "
    f"enabling SCOS compatibility mode for terminate()/analyze() "
    f"semantics. Required for correctness, not opt-in."
)


def _decorator_terminal_name(dec: cst.Decorator) -> Optional[str]:
    """Return the innermost identifier of the decorator expression
    (e.g. ``udtf`` for ``@udtf``, ``@F.udtf(returnType=...)``,
    ``@pyspark.sql.functions.udtf``). Returns None for unrecognized
    shapes."""
    expr = dec.decorator
    # ``@deco_expr(args)`` -- unwrap the Call.
    if isinstance(expr, cst.Call):
        expr = expr.func
    if isinstance(expr, cst.Name):
        return expr.value
    if isinstance(expr, cst.Attribute) and isinstance(expr.attr, cst.Name):
        return expr.attr.value
    return None


class _Detector(cst.CSTVisitor):
    def __init__(self) -> None:
        super().__init__()
        self.has_udtf = False

    def _check_decorators(self, decorators) -> None:
        if self.has_udtf:
            return
        for dec in decorators:
            if _decorator_terminal_name(dec) == "udtf":
                self.has_udtf = True
                return

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._check_decorators(node.decorators)

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self._check_decorators(node.decorators)


def _module_already_has_todo_marker(source: str) -> bool:
    """Detect the SCOS-TODO fallback comment from a previous run so the
    recipe stays idempotent on the comment-only path (where there is no
    ``.conf.set(...)`` literal for ``_module_already_sets_conf`` to
    catch)."""
    marker = f"SCOS-TODO: [SPRKCNTPY2600-Warning] {RECIPE_ID}"
    return marker in source


def _module_already_sets_conf(module: cst.Module) -> bool:
    """True iff the module already contains a ``<x>.conf.set(<key>, ...)``
    call literal for our key."""
    found = {"v": False}
    key_literal_double = f'"{_CONF_KEY}"'
    key_literal_single = f"'{_CONF_KEY}'"

    class _CheckConf(cst.CSTVisitor):
        def visit_Call(self, node: cst.Call) -> None:
            if found["v"]:
                return
            fn = node.func
            if not isinstance(fn, cst.Attribute):
                return
            if not isinstance(fn.attr, cst.Name) or fn.attr.value != "set":
                return
            recv = fn.value
            if not isinstance(recv, cst.Attribute):
                return
            if not isinstance(recv.attr, cst.Name) or recv.attr.value != "conf":
                return
            # First positional arg should be the literal key.
            for arg in node.args:
                if arg.keyword is not None:
                    continue
                if isinstance(arg.value, cst.SimpleString):
                    s = arg.value.value
                    if s == key_literal_double or s == key_literal_single:
                        found["v"] = True
                        return
                return

    module.visit(_CheckConf())
    return found["v"]


def _build_conf_stmt(receiver_name: str) -> cst.SimpleStatementLine:
    """``<receiver_name>.conf.set("snowpark.compatibility_mode_for_udtf", True)``
    on its own line with the SCOS comment above it."""
    call = cst.Call(
        func=cst.Attribute(
            value=cst.Attribute(
                value=cst.Name(receiver_name),
                attr=cst.Name("conf"),
            ),
            attr=cst.Name("set"),
        ),
        args=[
            cst.Arg(value=cst.SimpleString(f'"{_CONF_KEY}"')),
            cst.Arg(value=cst.Name("True")),
        ],
    )
    return cst.SimpleStatementLine(
        body=[cst.Expr(value=call)],
        leading_lines=[cst.EmptyLine(comment=cst.Comment(_COMMENT_TEXT))],
    )


def _build_todo_comment_stmt() -> cst.SimpleStatementLine:
    """Comment-only fallback: a ``pass`` statement whose only purpose is
    to anchor a leading SCOS-TODO comment near the top of the module."""
    fallback_text = (
        f"# SCOS-TODO: [SPRKCNTPY2600-Warning] {RECIPE_ID}: @udtf decorator "
        f"detected, but no Snowpark Connect session anchor was found "
        f"in this module -- please add "
        f"<session>.conf.set(\"{_CONF_KEY}\", True) after your session "
        f"is created. Required for terminate()/analyze() correctness."
    )
    return cst.SimpleStatementLine(
        body=[cst.Pass()],
        leading_lines=[cst.EmptyLine(comment=cst.Comment(fallback_text))],
    )


def _is_named_assignment(stmt: cst.SimpleStatementLine, name: str) -> bool:
    """True iff the statement is ``<name> = <expr>``."""
    if len(stmt.body) != 1:
        return False
    s = stmt.body[0]
    if not isinstance(s, cst.Assign):
        return False
    if len(s.targets) != 1:
        return False
    tgt = s.targets[0].target
    return isinstance(tgt, cst.Name) and tgt.value == name


def _assignment_target_name(stmt: cst.SimpleStatementLine) -> Optional[str]:
    """If ``stmt`` is ``<name> = <expr>``, return ``<name>``; else None."""
    if len(stmt.body) != 1:
        return None
    s = stmt.body[0]
    if not isinstance(s, cst.Assign):
        return None
    if len(s.targets) != 1:
        return None
    tgt = s.targets[0].target
    if not isinstance(tgt, cst.Name):
        return None
    return tgt.value


def _is_init_spark_session_assignment(
    stmt: cst.SimpleStatementLine,
) -> Optional[str]:
    """If ``stmt`` is ``<name> = snowpark_connect.init_spark_session()``,
    return ``<name>``. Otherwise None.

    This is the post-builder-rewrite shape that the alphabetically
    earlier ``spark_builder_drop_master_init_session_rewrite`` recipe
    produces; we look for it as the strongest signal that ``<name>`` is
    a live Snowpark Connect session.
    """
    name = _assignment_target_name(stmt)
    if name is None:
        return None
    s = stmt.body[0]
    assert isinstance(s, cst.Assign)
    val = s.value
    if not isinstance(val, cst.Call):
        return None
    fn = val.func
    if not isinstance(fn, cst.Attribute):
        return None
    if not isinstance(fn.attr, cst.Name) or fn.attr.value != "init_spark_session":
        return None
    if not isinstance(fn.value, cst.Name) or fn.value.value != "snowpark_connect":
        return None
    return name


def _is_import(stmt: cst.SimpleStatementLine) -> bool:
    return any(isinstance(s, (cst.Import, cst.ImportFrom)) for s in stmt.body)


def _try_inject_after_init_in_body(body: list) -> Optional[list]:
    """Walk a statement body looking for
    ``<name> = snowpark_connect.init_spark_session()``. If found, return
    a new body list with the conf.set inserted immediately after it.
    Returns None if no such anchor exists in this body.
    """
    for i, stmt in enumerate(body):
        if isinstance(stmt, cst.SimpleStatementLine):
            name = _is_init_spark_session_assignment(stmt)
            if name is not None:
                return list(body[: i + 1]) + [_build_conf_stmt(name)] + list(body[i + 1:])
    return None


def _inject_inside_first_builder_function(
    module: cst.Module,
) -> Optional[cst.Module]:
    """Find the first FunctionDef whose body contains a
    ``<name> = snowpark_connect.init_spark_session()`` line and inject
    the conf.set right after that assignment. Returns the updated
    module, or None if no builder function exists.
    """
    new_top: list = []
    injected = False
    for top in module.body:
        if (
            not injected
            and isinstance(top, cst.FunctionDef)
            and isinstance(top.body, cst.IndentedBlock)
        ):
            inner = list(top.body.body)
            new_inner = _try_inject_after_init_in_body(inner)
            if new_inner is not None:
                new_block = top.body.with_changes(body=tuple(new_inner))
                top = top.with_changes(body=new_block)
                injected = True
        new_top.append(top)
    if not injected:
        return None
    return module.with_changes(body=tuple(new_top))


def _inject_at_module_spark_assignment(
    module: cst.Module,
) -> Optional[cst.Module]:
    """If the module body has a top-level ``spark = ...`` (or ``spark = ``
    chain ending in ``snowpark_connect.init_spark_session()``), insert
    the conf.set immediately after the last consecutive such assignment.
    Returns the updated module, or None if no such anchor exists.
    """
    body = list(module.body)
    last_spark_assign = -1
    for i, stmt in enumerate(body):
        if isinstance(stmt, cst.SimpleStatementLine) and _is_named_assignment(
            stmt, "spark"
        ):
            last_spark_assign = i
    if last_spark_assign == -1:
        return None
    new_body = body[: last_spark_assign + 1] + [_build_conf_stmt("spark")] + body[last_spark_assign + 1:]
    return module.with_changes(body=tuple(new_body))


def _inject_top_level_todo(module: cst.Module) -> cst.Module:
    """Last-resort fallback: emit a comment-only ``pass`` statement at
    the top of the module (after any leading docstring + imports) so
    the LLM fixer can wire up the conf.set in the right place."""
    body = list(module.body)
    insert_at = 0
    # Skip leading docstring (first stmt being an Expr(SimpleString)).
    if (
        body
        and isinstance(body[0], cst.SimpleStatementLine)
        and len(body[0].body) == 1
        and isinstance(body[0].body[0], cst.Expr)
        and isinstance(body[0].body[0].value, cst.SimpleString)
    ):
        insert_at = 1
    # Skip past contiguous import lines after the docstring.
    while insert_at < len(body) and isinstance(
        body[insert_at], cst.SimpleStatementLine
    ) and _is_import(body[insert_at]):
        insert_at += 1
    new_body = body[:insert_at] + [_build_todo_comment_stmt()] + body[insert_at:]
    return module.with_changes(body=tuple(new_body))


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    module = cst.parse_module(source)
    det = _Detector()
    module.visit(det)
    if not det.has_udtf:
        return _common.RecipeResult(source=source, edits=[])
    if _module_already_sets_conf(module):
        return _common.RecipeResult(source=source, edits=[])
    if _module_already_has_todo_marker(source):
        return _common.RecipeResult(source=source, edits=[])

    # Try the three injection strategies in priority order.
    new_module = _inject_inside_first_builder_function(module)
    if new_module is None:
        new_module = _inject_at_module_spark_assignment(module)
    if new_module is None:
        new_module = _inject_top_level_todo(module)

    first_udtf_line = _find_first_udtf_line(module) or 1
    anchor = _common.output_anchor(
        RECIPE_ID, first_udtf_line, "inject udtf compatibility conf"
    )
    edit = _record_edit_passthrough(
        file=file,
        src_line=first_udtf_line,
        recipe_id=RECIPE_ID,
        output_line_anchor=anchor,
        facts_db=facts_db,
    )
    return _common.RecipeResult(source=new_module.code, edits=[edit])


def _find_first_udtf_line(module: cst.Module) -> Optional[int]:
    """Walk decorators in source order and return the first udtf
    decorator's start line."""
    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    line_holder = {"v": None}

    class _Finder(cst.CSTVisitor):
        METADATA_DEPENDENCIES = (cst.metadata.PositionProvider,)

        def _check(self, decorators) -> None:
            if line_holder["v"] is not None:
                return
            for dec in decorators:
                if _decorator_terminal_name(dec) == "udtf":
                    pos = self.get_metadata(
                        cst.metadata.PositionProvider, dec
                    )
                    line_holder["v"] = pos.start.line
                    return

        def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
            self._check(node.decorators)

        def visit_ClassDef(self, node: cst.ClassDef) -> None:
            self._check(node.decorators)

    wrapper.visit(_Finder())
    return line_holder["v"]


def _record_edit_passthrough(
    *, file: str, src_line: int, recipe_id: str,
    output_line_anchor: str, facts_db: Optional[str]
):
    """Direct passthrough to the shared record_edit helper so the
    module-level edit shows up in the audit table even though there's no
    BaseRecipe instance carrying it."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import _recipe_base  # noqa: E402

    return _recipe_base.record_edit(
        file=file,
        src_line=src_line,
        recipe_id=recipe_id,
        output_line_anchor=output_line_anchor,
        facts_db=facts_db,
    )
