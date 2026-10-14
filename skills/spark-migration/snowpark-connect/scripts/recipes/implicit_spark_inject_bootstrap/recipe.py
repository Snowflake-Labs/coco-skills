"""Deterministic bootstrap injection for implicit-``spark`` workloads.

What it does
------------

For any file that

  1. references the implicitly-provided ``spark`` global anywhere
     (``spark.read.*``, ``spark.sql(...)``, ``spark.conf.*``, etc.),
  2. does NOT define ``spark`` at module scope (assignment) and does
     NOT bind ``spark`` via a module-scope import, and
  3. does NOT already import ``snowpark_connect``,

inject the canonical SCOS session bootstrap at the top of the module:

    from snowflake import snowpark_connect
    spark = snowpark_connect.init_spark_session()

Why this exists
---------------

Many Spark workloads never contain an explicit
``SparkSession.builder...getOrCreate()`` call — ``spark`` is provided
by the runtime as an ambient global. This happens in two common shapes:

  * **Databricks notebooks** — the Databricks runtime injects ``spark``
    automatically (export carries ``# Databricks notebook source`` /
    ``# MAGIC %run`` markers).
  * **Plain ``spark-submit`` / YARN scripts** — the driver provides
    ``spark`` (and the original ``SparkSession.builder`` is frequently
    already commented out in the source), so the script just uses
    ``spark.table(...)`` / ``spark.conf.set(...)`` directly.

The other Spark-Session recipes
(``spark_builder_drop_master_init_session_rewrite``,
``sparkcontext_property_fallback_rewrite``) match on an *existing*
builder chain, so they cannot fire for either shape — there is nothing
to rewrite. The result was:

  * Phase 0.5: nothing matches → no edits
  * Phase 1: analyzer flags ``spark`` as undefined
  * Phase 2: LLM fixer must spend tokens deciding where to inject
    the bootstrap, often missing shared modules under context pressure
  * Runtime: ``NameError: name 'spark' is not defined`` at first call

This recipe closes that gap with a deterministic, idempotent injection
that runs before any LLM phase. It is intentionally per-file: every
file that uses ambient ``spark`` gets its own bootstrap. The bootstrap
is idempotent (``init_spark_session()`` returns the cached session on
second call), so the per-file redundancy is safe whether the workload
is later run as standalone scripts or with helpers inlined.

Trigger
-------

All three must hold:

  * The CST contains at least one ``Attribute(value=Name("spark"))``
    or ``Call(func=Name("spark"))`` node, i.e. the file actually uses
    the implicit global.
  * Nothing binds ``spark`` at module scope: no top-level assignment
    (``spark = …``) and no module-scope import (``from x import spark``
    / ``import x as spark``).
  * No existing snowpark_connect import:
    ``from snowflake import snowpark_connect``,
    ``import snowflake.snowpark_connect as snowpark_connect``, or
    ``import snowpark_connect`` (the legacy/incorrect form is also
    treated as "already handled" — replacing it is a separate concern).

Negative cases (must NOT trigger)
---------------------------------

  * File with a live ``SparkSession.builder...getOrCreate()`` — the
    builder assigns ``spark`` at module scope, so this recipe skips it
    and the builder recipes own that case.
  * Already-migrated SCOS file (bootstrap or import present) —
    idempotent no-op.
  * File defines or imports its own ``spark`` (custom builder, fixture,
    shared helper) — leave alone; we do not second-guess explicit user
    code or double-bind the name.
  * File never references ``spark`` (e.g. a pure-SQL notebook that only
    uses ``%sql`` magic) — no injection needed; do not add dead code.

Output
------

The bootstrap is inserted as the first two statements of the module,
after any module-level header comments / docstring. A single
``# SCOS: [SPRKCNTPY1001-Fixed]`` marker is stamped directly above it so
the injection is visible inline and flows into the migration header's
``Changes Overview`` (which is built solely from ``# SCOS:`` comments).
Subsequent user imports follow unchanged. A single ``recipe_edits`` row
is recorded at ``src_line=1``.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "implicit_spark_inject_bootstrap"
MIN_SCOS_VERSION = "0.4.0"

# This recipe reasons about *module* scope ("is ``spark`` defined / imported
# anywhere in this file?") and injects a single top-of-module bootstrap. In a
# notebook the logical module is the whole notebook, not one cell, so Phase 0.5
# must evaluate it against the concatenation of all Python code cells and inject
# once into the first cell — never per-cell (which would both over-inject and
# miss a definition that lives in an earlier cell). See preprocess_recipes.py.
NOTEBOOK_SCOPE = "module"


# ---------------------------------------------------------------------------
# CST analysis
# ---------------------------------------------------------------------------


class _SparkUsageScanner(cst.CSTVisitor):
    """Walk the tree and answer two questions in one pass:

      * Does the file *use* ``spark`` as an undefined global (i.e. as the
        base of an attribute access or a direct call)?
      * Does the file *bind* ``spark`` at module scope — via an
        assignment (``spark = …``) OR a module-scope import
        (``from x import spark`` / ``import x as spark``)?

    We only count *module-level* bindings as definitions — a local
    ``spark = …`` inside a function body is a shadowing binding, not a
    module-level definition, and does not satisfy the rest of the
    module's references. A module-scope import of ``spark`` (e.g. from a
    shared helper) is treated as a definition so we never double-bind
    the name.
    """

    def __init__(self) -> None:
        super().__init__()
        self.uses_spark = False
        self.defines_spark_at_module_scope = False
        self._scope_depth = 0  # 0 == module scope; >0 == inside def/class

    # --- scope tracking --------------------------------------------------

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._scope_depth += 1

    def leave_FunctionDef(self, original_node: cst.FunctionDef) -> None:
        self._scope_depth -= 1

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self._scope_depth += 1

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        self._scope_depth -= 1

    # --- usage detection -------------------------------------------------

    def visit_Attribute(self, node: cst.Attribute) -> None:
        # ``spark.read``, ``spark.sql``, ``spark.conf`` — the base is a Name.
        if isinstance(node.value, cst.Name) and node.value.value == "spark":
            self.uses_spark = True

    def visit_Call(self, node: cst.Call) -> None:
        # Bare ``spark(...)`` is unusual but possible (e.g. lambda binding).
        if isinstance(node.func, cst.Name) and node.func.value == "spark":
            self.uses_spark = True

    # --- definition detection -------------------------------------------

    def visit_Assign(self, node: cst.Assign) -> None:
        if self._scope_depth != 0:
            return
        for target in node.targets:
            if isinstance(target.target, cst.Name) and target.target.value == "spark":
                self.defines_spark_at_module_scope = True
            # tuple unpacking: ``spark, sc = build()``
            if isinstance(target.target, cst.Tuple):
                for elt in target.target.elements:
                    inner = getattr(elt, "value", None)
                    if isinstance(inner, cst.Name) and inner.value == "spark":
                        self.defines_spark_at_module_scope = True

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        if self._scope_depth != 0:
            return
        if isinstance(node.target, cst.Name) and node.target.value == "spark":
            self.defines_spark_at_module_scope = True

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        # ``from helpers import spark`` (or ``... import x as spark``) at
        # module scope binds the name; do not double-inject a bootstrap.
        if self._scope_depth != 0:
            return
        names = node.names
        if isinstance(names, cst.ImportStar):
            return
        for alias in names:
            bound = alias.asname.name.value if alias.asname else (
                alias.name.value if isinstance(alias.name, cst.Name) else None
            )
            if bound == "spark":
                self.defines_spark_at_module_scope = True

    def visit_Import(self, node: cst.Import) -> None:
        # ``import x as spark`` at module scope binds the name.
        if self._scope_depth != 0:
            return
        for alias in node.names:
            bound = alias.asname.name.value if alias.asname else None
            if bound == "spark":
                self.defines_spark_at_module_scope = True


def _has_snowpark_connect_import(module: cst.Module) -> bool:
    """True iff the module already imports snowpark_connect under any of
    the three recognised forms.

    Forms recognised:

      * ``from snowflake import snowpark_connect``   (canonical)
      * ``import snowflake.snowpark_connect as snowpark_connect``
      * ``import snowpark_connect``                  (legacy / incorrect
        — treated as "already attempted bootstrap"; the import-updater
        agent or a separate recipe is responsible for fixing the form.)
    """
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for s in stmt.body:
            if isinstance(s, cst.ImportFrom):
                mod = s.module
                if isinstance(mod, cst.Name) and mod.value == "snowflake":
                    for n in s.names:
                        if isinstance(n, cst.ImportAlias) and (
                            (
                                isinstance(n.name, cst.Name)
                                and n.name.value == "snowpark_connect"
                            )
                            or (n.asname and n.asname.name.value == "snowpark_connect")
                        ):
                            return True
            elif isinstance(s, cst.Import):
                for n in s.names:
                    asname = n.asname.name.value if n.asname else None
                    if asname == "snowpark_connect":
                        return True
                    if (
                        isinstance(n.name, cst.Name)
                        and n.name.value == "snowpark_connect"
                    ):
                        return True
    return False


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------


# `from snowflake import snowpark_connect`
_IMPORT_STMT = cst.SimpleStatementLine(
    body=[
        cst.ImportFrom(
            module=cst.Name("snowflake"),
            names=[cst.ImportAlias(name=cst.Name("snowpark_connect"))],
            relative=[],
        )
    ]
)

# `spark = snowpark_connect.init_spark_session()`
_ASSIGN_STMT = cst.SimpleStatementLine(
    body=[
        cst.Assign(
            targets=[cst.AssignTarget(target=cst.Name("spark"))],
            value=cst.Call(
                func=cst.Attribute(
                    value=cst.Name("snowpark_connect"),
                    attr=cst.Name("init_spark_session"),
                ),
                args=[],
            ),
        )
    ]
)

# SCOS marker stamped above the injected bootstrap. Without it the injection was
# invisible to both the inline reader and the migration header — the header's
# Changes Overview is built solely from ``# SCOS:`` comments
# (update_imports._collect_scos_annotations), so a comment-less bootstrap never
# appeared as a change. The sibling session recipe
# (``sparkcontext_getorcreate_init_session_rewrite``) stamps the same
# ``[SPRKCNTPY1001-Fixed]`` code when it *replaces* a builder; this recipe stamps
# it when it *injects* a session, so the two paths report identically.
_MARKER = (
    f"# SCOS: [SPRKCNTPY1001-Fixed] {RECIPE_ID}: the implicitly-provided `spark` "
    "global is not available in SCOS; injected "
    "snowpark_connect.init_spark_session() to bind `spark` at module scope."
)


def _inject_at_top(module: cst.Module) -> cst.Module:
    """Insert the two-line bootstrap as the first statements of the module.

    Module-level header comments (e.g. ``# Databricks notebook source``)
    live in ``module.header`` (a tuple of ``EmptyLine`` objects), which
    LibCST keeps separate from ``module.body``. Inserting at
    ``body[0]`` therefore lands after the header and before any other
    statement — the desired placement.

    A single ``# SCOS:`` marker is attached above the injected import so the
    injection is visible both inline and in the migration header (which is
    built only from ``# SCOS:`` comments).
    """
    import_with_marker = _IMPORT_STMT.with_changes(
        leading_lines=(cst.EmptyLine(comment=cst.Comment(_MARKER)),)
    )
    new_body = (import_with_marker, _ASSIGN_STMT) + tuple(module.body)
    return module.with_changes(body=new_body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def apply(
    source: str, *, file: str = "<input.py>", facts_db: str | None = None
) -> _common.RecipeResult:
    """Inject the SCOS bootstrap into implicit-``spark`` files that need it.

    Fires for any file that uses ``spark`` as an ambient global without
    binding it at module scope — covering both Databricks notebooks and
    plain ``spark-submit``/YARN scripts. Idempotent and safe to re-run:
    returns a no-op ``RecipeResult`` when any trigger condition fails or
    the bootstrap is already present.
    """
    # Cheap textual pre-check: skip parsing files that can't possibly use
    # the ambient global (the word ``spark`` must appear somewhere).
    if "spark" not in source:
        return _common.RecipeResult(source=source, edits=[])

    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError:
        # Don't crash the Phase 0.5 dispatcher on unparseable input;
        # leave the file alone for the later compilation gate to catch.
        return _common.RecipeResult(source=source, edits=[])

    if _has_snowpark_connect_import(module):
        return _common.RecipeResult(source=source, edits=[])

    scanner = _SparkUsageScanner()
    module.visit(scanner)
    if not scanner.uses_spark:
        return _common.RecipeResult(source=source, edits=[])
    if scanner.defines_spark_at_module_scope:
        return _common.RecipeResult(source=source, edits=[])

    new_module = _inject_at_top(module)

    anchor = _common.output_anchor(
        RECIPE_ID, 1, "from snowflake import snowpark_connect"
    )
    # _recipe_base.record_edit is the canonical sink (handles the
    # optional sqlite facts_db). Importing the module via _common keeps
    # the recipe self-contained alongside its peers.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import _recipe_base  # noqa: E402

    edit = _recipe_base.record_edit(
        file=file,
        src_line=1,
        recipe_id=RECIPE_ID,
        output_line_anchor=anchor,
        facts_db=facts_db,
    )
    return _common.RecipeResult(source=new_module.code, edits=[edit])
