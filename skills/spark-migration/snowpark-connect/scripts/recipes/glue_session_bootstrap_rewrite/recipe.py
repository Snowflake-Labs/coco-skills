"""Collapse an AWS Glue job bootstrap into a single Snowpark Connect session:
``GlueContext(...)`` becomes ``snowpark_connect.init_spark_session()``, the
``.spark_session`` hop is dropped, and the now-dead ``SparkContext`` /
``Job`` scaffolding is commented out.

What it does
------------

A Glue job's entry point is three coupled objects that SCOS replaces with one
session (recipe G1 in ``references/python/glue-recipes.md``). This recipe owns
the *session* half of G1 plus the ``job.*`` removal that G8 warns about:

1. **``GlueContext(<anything>)`` -> ``snowpark_connect.init_spark_session()``**
   in all three statement shapes — assignment RHS, ``return``, and a bare
   expression statement::

       glueContext = GlueContext(sc)
       -> glueContext = snowpark_connect.init_spark_session()

   When any such rewrite fires, ``from snowflake import snowpark_connect`` is
   injected once at the top of the processed unit (after the existing import
   block), unless an equivalent binding is already present.

2. **``<var>.spark_session`` -> ``<var>``**. ``.spark_session`` is the Glue
   accessor that unwraps a ``GlueContext`` into a ``SparkSession``. After (1)
   the variable already *is* the session, so leaving the hop in place raises
   ``AttributeError`` at runtime::

       spark = glueContext.spark_session
       -> spark = glueContext

   This is gated on **module-level Glue evidence** (see Trigger): a non-Glue
   codebase that defines its own ``.spark_session`` property must not be
   broken by this recipe.

3. **Dead ``SparkContext`` binding -> commented out.** A
   ``SparkContext.getOrCreate()`` / ``SparkContext(...)`` assignment whose
   variable is used *only* as an argument to ``GlueContext(...)`` exists purely
   to feed the GlueContext, and the session call in (1) replaces it. The whole
   statement is commented out. If the variable has **any** other use the
   statement is left completely alone — ``sc`` still carrying real work is
   another recipe's business.

4. **``Job(...)`` / ``<job>.init(...)`` / ``<job>.commit()`` -> commented out**
   with a bookmark warning. These have no SCOS equivalent, but deleting them
   silently is a correctness trap: ``job.commit()`` is what advances a Glue
   **job bookmark**, so removing it turns an incremental job into one that
   reprocesses everything on every run. Each commented-out statement therefore
   carries a ``# SCOS: TODO - [SPRKCNTPY3606-Error]`` comment pointing at
   recipe **G8** (external stage + directory table + Stream) as the
   replacement for bookmark-based incrementality.

Statements are commented out rather than deleted: the original source is kept
verbatim as ``#``-prefixed lines and the statement body is replaced with
``pass``. The ``pass`` is what keeps the output compilable when the commented
statement was the only statement in a function or ``if`` body.

Trigger
-------

Cheap substring gate: the source must contain ``GlueContext`` or ``awsglue``.
That same check is the **Glue-evidence gate** for transform (2) — the
``.spark_session`` rewrite only ever fires in a file that also shows a
``GlueContext`` call or an ``awsglue`` import.

``GlueContext`` is matched as a bare ``Name`` or as the final attribute of a
dotted path (``awsglue.context.GlueContext(...)``); likewise ``Job`` and
``SparkContext``.

A module pre-pass (``_ModuleScan``) collects, before any rewriting:

* the set of variables assigned from ``Job(...)`` — only these get their
  ``.init`` / ``.commit`` calls commented out;
* the set of variables assigned from a ``SparkContext`` constructor whose every
  non-assignment occurrence is a direct ``GlueContext(...)`` argument.

Negative cases (must NOT trigger)
---------------------------------

* No ``GlueContext`` and no ``awsglue`` token — the substring gate
  short-circuits, so plain PySpark is byte-for-byte untouched.
* ``<var>.spark_session`` in a file with no Glue evidence — a user-defined
  ``spark_session`` property is left alone.
* ``sc = SparkContext.getOrCreate()`` where ``sc`` is also used for anything
  else (``sc.parallelize(...)``, passed to another function, reassigned,
  never passed to ``GlueContext``) — left unchanged.
* ``<x>.init(...)`` / ``<x>.commit()`` where ``<x>`` is not traceable to a
  ``Job(...)`` assignment (including ``self.job.commit()``, whose receiver is
  an attribute rather than a module-level name) — left unchanged.
* ``snowpark_connect.init_spark_session()`` already present — nothing matches.

Known conservative gaps, all of which leave the code **unchanged** so the
analyzer still sees a live ``GlueContext`` to classify:

* Semicolon-joined statements (``sc = SparkContext.getOrCreate(); glueContext =
  GlueContext(sc)``) — every statement transform requires a single-statement
  line, because commenting out one half of a shared line is not expressible.
* ``AnnAssign`` (``glueContext: GlueContext = GlueContext(sc)``) — only
  ``Assign`` / ``Return`` / bare-expression shapes are rewritten.
* An assignment *to* ``<x>.spark_session`` (a write, not a read) would also lose
  the hop. Glue's ``spark_session`` is read-only, so this shape does not occur
  in real Glue jobs and is not special-cased.
* A ``.spark_session`` read in a *compound-statement header*
  (``if glueContext.spark_session:``) is still rewritten and still recorded as
  an edit, but carries no explanatory comment — the comment can only attach to
  a ``SimpleStatementLine``.

Idempotency
-----------

Re-running on the recipe's own output is a byte-for-byte no-op with zero edits.
Every transform erases its own trigger token: the ``GlueContext`` call is gone,
no ``.spark_session`` remains, and the ``SparkContext`` / ``Job`` statements are
now ``pass`` with the original code demoted to comments (so the pre-pass finds
no ``Job``-assigned or GlueContext-only-``SparkContext`` variables at all).
Statements already carrying a ``RECIPE_ID`` leading comment are skipped
outright. The import injection is guarded by ``_has_snowpark_connect_import``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "glue_session_bootstrap_rewrite"
MIN_SCOS_VERSION = "0.4.0"
NOTEBOOK_SCOPE = "cell"

_EWI_ENTRY = "SPRKCNTPY3600"
_EWI_BOOKMARK = "SPRKCNTPY3606"

_SNOWPARK_IMPORT_TEXT = "from snowflake import snowpark_connect"

# ``snowpark_connect.init_spark_session()``
_REPLACEMENT_EXPR = cst.Call(
    func=cst.Attribute(
        value=cst.Name("snowpark_connect"),
        attr=cst.Name("init_spark_session"),
    ),
    args=[],
)

# Renders as ``from snowflake import snowpark_connect``.
_IMPORT_NODE = cst.SimpleStatementLine(
    body=[
        cst.ImportFrom(
            module=cst.Name("snowflake"),
            names=[cst.ImportAlias(name=cst.Name("snowpark_connect"))],
            relative=[],
        )
    ]
)

_GLUE_CONTEXT_COMMENT = (
    f"# SCOS: [{_EWI_ENTRY}-Fixed] {RECIPE_ID}: GlueContext(...) -> "
    f"snowpark_connect.init_spark_session(); warehouse/database/schema/role come "
    f"from the connection, not from code"
)

_SPARK_SESSION_COMMENT = (
    f"# SCOS: [{_EWI_ENTRY}-Fixed] {RECIPE_ID}: dropped the .spark_session hop -> "
    f"the variable already IS the session after the GlueContext rewrite, so "
    f"keeping .spark_session would raise AttributeError"
)

_SPARK_CONTEXT_COMMENT = (
    f"# SCOS: [{_EWI_ENTRY}-Fixed] {RECIPE_ID}: commented out the SparkContext "
    f"binding — its only use was as the GlueContext(...) argument, and "
    f"snowpark_connect.init_spark_session() replaces both"
)

_JOB_COMMENT_LINES = (
    f"# SCOS: TODO - [{_EWI_BOOKMARK}-Error] {RECIPE_ID}: commented out Glue job "
    f"lifecycle (Job(...) / .init(...) / .commit()) — there is no SCOS equivalent.",
    f"# SCOS: TODO - [{_EWI_BOOKMARK}-Error] {RECIPE_ID}: job.commit() is what "
    f"advances the Glue JOB BOOKMARK. Deleting it silently turns an incremental "
    f"job into one that REPROCESSES EVERYTHING on every run.",
    f"# SCOS: TODO - [{_EWI_BOOKMARK}-Error] {RECIPE_ID}: if this job relied on "
    f"bookmarks, reimplement incrementality per recipe G8: external stage over "
    f"the same prefix + directory table, a Stream on that stage (new files show "
    f"metadata$action = 'INSERT'), consumed inside a DML transaction so the "
    f"offset advances only on success, then COPY INTO the target table.",
)


# ---------------------------------------------------------------------------
# Shape predicates
# ---------------------------------------------------------------------------


def _tail_name(expr: cst.BaseExpression) -> Optional[str]:
    """Final identifier of a bare ``Name`` or a dotted ``a.b.C`` path."""
    if isinstance(expr, cst.Name):
        return expr.value
    if isinstance(expr, cst.Attribute) and isinstance(expr.attr, cst.Name):
        return expr.attr.value
    return None


def _is_ctor_call(node: cst.BaseExpression, class_name: str) -> bool:
    """``ClassName(...)`` or ``pkg.mod.ClassName(...)``."""
    return isinstance(node, cst.Call) and _tail_name(node.func) == class_name


def _is_glue_context_call(node: cst.BaseExpression) -> bool:
    return _is_ctor_call(node, "GlueContext")


def _is_job_call(node: cst.BaseExpression) -> bool:
    return _is_ctor_call(node, "Job")


def _is_spark_context_ctor(node: cst.BaseExpression) -> bool:
    """``SparkContext(...)``, ``SparkContext.getOrCreate()`` or the dotted
    equivalents (``pyspark.context.SparkContext.getOrCreate()``)."""
    if not isinstance(node, cst.Call):
        return False
    if _tail_name(node.func) == "SparkContext":
        return True
    # ``<...>.SparkContext.getOrCreate()`` / ``SparkContext.getOrCreate()``
    func = node.func
    if (
        isinstance(func, cst.Attribute)
        and isinstance(func.attr, cst.Name)
        and func.attr.value == "getOrCreate"
        and _tail_name(func.value) == "SparkContext"
    ):
        return True
    return False


def _single_name_target(assign: cst.Assign) -> Optional[str]:
    if len(assign.targets) != 1:
        return None
    target = assign.targets[0].target
    return target.value if isinstance(target, cst.Name) else None


def _has_glue_evidence(source: str) -> bool:
    """Module-level pre-scan of the raw source text.

    Gates the ``.spark_session`` rewrite so a non-Glue codebase with its own
    ``.spark_session`` property is never touched.
    """
    return "GlueContext" in source or "awsglue" in source


# ---------------------------------------------------------------------------
# Module pre-pass
# ---------------------------------------------------------------------------


class _ModuleScan(cst.CSTVisitor):
    """Collect the module-wide facts the statement rewrites need.

    * ``job_vars`` — names assigned from ``Job(...)``.
    * ``sc_only_for_glue`` — names assigned from a ``SparkContext`` constructor
      whose every non-assignment occurrence is a direct ``GlueContext(...)``
      argument, i.e. the binding is dead once the session call replaces it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.job_vars: set[str] = set()
        # name -> counts
        self._name_loads: dict[str, int] = {}
        self._assign_targets: dict[str, int] = {}
        self._sc_assigns: dict[str, int] = {}
        self._glue_args: dict[str, int] = {}

    @staticmethod
    def _bump(counter: dict[str, int], key: str) -> None:
        counter[key] = counter.get(key, 0) + 1

    def visit_Name(self, node: cst.Name) -> None:
        # Counts EVERY occurrence, including assignment targets, the receiver
        # of an attribute access and a bare function name. Subtracting the
        # assignment-target count leaves the "loads", which is exactly the
        # conservative denominator we want.
        self._bump(self._name_loads, node.value)

    def visit_Call(self, node: cst.Call) -> None:
        if not _is_glue_context_call(node):
            return
        for arg in node.args:
            # Positional ``GlueContext(sc)`` and keyword ``GlueContext(x=sc)``
            # both count as "used only to build the GlueContext".
            if isinstance(arg.value, cst.Name):
                self._bump(self._glue_args, arg.value.value)

    def visit_Assign(self, node: cst.Assign) -> None:
        name = _single_name_target(node)
        if name is None:
            return
        self._bump(self._assign_targets, name)
        if _is_job_call(node.value):
            self.job_vars.add(name)
        elif _is_spark_context_ctor(node.value):
            self._bump(self._sc_assigns, name)

    @property
    def sc_only_for_glue(self) -> set[str]:
        out: set[str] = set()
        for name, n_sc in self._sc_assigns.items():
            n_targets = self._assign_targets.get(name, 0)
            n_glue = self._glue_args.get(name, 0)
            loads = self._name_loads.get(name, 0) - n_targets
            # Exactly one binding, that binding is the SparkContext ctor, the
            # name is fed to GlueContext at least once, and it is fed to
            # NOTHING else.
            if n_sc == 1 and n_targets == 1 and n_glue >= 1 and loads == n_glue:
                out.add(name)
        return out


# ---------------------------------------------------------------------------
# Comment-out helpers
# ---------------------------------------------------------------------------


def _source_lines_of_statement(stmt: cst.SimpleStatementLine) -> list[str]:
    """Render the code portion of the statement (no leading comments/blanks)
    as individual lines, so each can become its own ``#`` comment."""
    mod = cst.Module(body=[stmt.with_changes(leading_lines=())])
    return mod.code.rstrip("\n").splitlines() or [""]


def _comment_out(
    stmt: cst.SimpleStatementLine, notes: tuple[str, ...] | list[str]
) -> cst.SimpleStatementLine:
    """Replace ``stmt``'s body with ``pass`` and demote the original code to
    trailing ``#`` comment lines, preceded by ``notes``.

    The ``pass`` keeps the output compilable when the commented-out statement
    was the sole statement of a function / ``if`` body.
    """
    demoted = [f"# {line}" for line in _source_lines_of_statement(stmt)]
    new_leading = list(stmt.leading_lines) + [
        cst.EmptyLine(comment=cst.Comment(text)) for text in (*notes, *demoted)
    ]
    return stmt.with_changes(leading_lines=tuple(new_leading), body=[cst.Pass()])


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, *, scan: _ModuleScan, glue_evidence: bool, **kw) -> None:
        super().__init__(**kw)
        self._scan = scan
        self._glue_evidence = glue_evidence
        self.session_rewrites = 0
        # Source lines on which a ``.spark_session`` hop was dropped; consumed
        # by the enclosing statement so the SCOS comment lands in the right
        # place. LibCST leaves children before parents, so every entry is
        # already present by the time the statement is left.
        self._spark_session_lines: set[int] = set()

    def _end_line_of(self, original_node: cst.CSTNode) -> int:
        return self.get_metadata(
            cst.metadata.PositionProvider, original_node
        ).end.line

    # ---- transform 2: ``<var>.spark_session`` -> ``<var>`` ----------------

    def leave_Attribute(  # type: ignore[override]
        self, original_node: cst.Attribute, updated_node: cst.Attribute
    ) -> cst.BaseExpression:
        if not self._glue_evidence:
            return updated_node
        if not (
            isinstance(updated_node.attr, cst.Name)
            and updated_node.attr.value == "spark_session"
        ):
            return updated_node
        line = self._line_of(original_node)
        self._spark_session_lines.add(line)
        self._record(line, "dropped the .spark_session hop (var already IS the session)")
        return updated_node.value

    # ---- statement dispatch ----------------------------------------------

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        start = self._line_of(original_node)
        end = self._end_line_of(original_node)

        # Claim any ``.spark_session`` rewrite that happened inside this
        # statement so we can explain it with a comment. Entries outside every
        # SimpleStatementLine (e.g. in an ``if`` header) simply go unclaimed —
        # the edit is still recorded, it just carries no comment.
        pending_ss = {ln for ln in self._spark_session_lines if start <= ln <= end}
        self._spark_session_lines -= pending_ss
        notes: list[str] = [_SPARK_SESSION_COMMENT] if pending_ss else []

        if _annotate.comment_above_contains(self._lines, start, RECIPE_ID):
            return self._with_notes(updated_node, notes)

        if len(updated_node.body) != 1:
            return self._with_notes(updated_node, notes)
        small = updated_node.body[0]

        # ---- transform 4: Job lifecycle -> commented out -----------------
        if self._is_job_statement(small):
            self._record(start, "commented out Glue Job lifecycle (no SCOS equivalent)")
            return _comment_out(updated_node, [*notes, *_JOB_COMMENT_LINES])

        # ---- transform 3: dead SparkContext binding -> commented out -----
        if isinstance(small, cst.Assign):
            name = _single_name_target(small)
            if (
                name is not None
                and name in self._scan.sc_only_for_glue
                and _is_spark_context_ctor(small.value)
            ):
                self._record(
                    start, "commented out SparkContext binding (only fed GlueContext)"
                )
                return _comment_out(updated_node, [*notes, _SPARK_CONTEXT_COMMENT])

        # ---- transform 1: GlueContext(...) -> init_spark_session() -------
        new_small = self._rewrite_glue_context(small)
        if new_small is not None:
            self.session_rewrites += 1
            self._record(start, "GlueContext(...) -> snowpark_connect.init_spark_session()")
            notes.append(_GLUE_CONTEXT_COMMENT)
            return self._with_notes(updated_node.with_changes(body=[new_small]), notes)

        return self._with_notes(updated_node, notes)

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _with_notes(
        stmt: cst.SimpleStatementLine, notes: list[str]
    ) -> cst.SimpleStatementLine:
        if not notes:
            return stmt
        new_leading = list(stmt.leading_lines) + [
            cst.EmptyLine(comment=cst.Comment(text)) for text in notes
        ]
        return stmt.with_changes(leading_lines=tuple(new_leading))

    def _is_job_statement(self, small: cst.BaseSmallStatement) -> bool:
        """``<var> = Job(...)``, ``<job>.init(...)`` or ``<job>.commit()`` where
        ``<job>`` is traceable to a ``Job(...)`` assignment."""
        if isinstance(small, cst.Assign):
            return _is_job_call(small.value) and _single_name_target(small) is not None
        if isinstance(small, cst.Expr):
            call = small.value
            if not isinstance(call, cst.Call):
                return False
            func = call.func
            return (
                isinstance(func, cst.Attribute)
                and isinstance(func.attr, cst.Name)
                and func.attr.value in ("init", "commit")
                and isinstance(func.value, cst.Name)
                and func.value.value in self._scan.job_vars
            )
        return False

    @staticmethod
    def _rewrite_glue_context(
        small: cst.BaseSmallStatement,
    ) -> Optional[cst.BaseSmallStatement]:
        """Swap a ``GlueContext(...)`` value for the session call in any of the
        three supported statement shapes. Returns None when nothing matched."""
        if isinstance(small, cst.Assign) and _is_glue_context_call(small.value):
            return small.with_changes(value=_REPLACEMENT_EXPR)
        if (
            isinstance(small, cst.Return)
            and small.value is not None
            and _is_glue_context_call(small.value)
        ):
            return small.with_changes(value=_REPLACEMENT_EXPR)
        if isinstance(small, cst.Expr) and _is_glue_context_call(small.value):
            return small.with_changes(value=_REPLACEMENT_EXPR)
        return None


# ---------------------------------------------------------------------------
# Import injection (mirrors spark_builder_drop_master_init_session_rewrite)
# ---------------------------------------------------------------------------


def _has_snowpark_connect_import(module: cst.Module) -> bool:
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for s in stmt.body:
            if isinstance(s, cst.ImportFrom):
                if (
                    isinstance(s.module, cst.Name) and s.module.value == "snowflake"
                ) or (
                    isinstance(s.module, cst.Attribute)
                    and isinstance(s.module.value, cst.Name)
                    and s.module.value.value == "snowflake"
                ):
                    for n in s.names:
                        if isinstance(n, cst.ImportAlias):
                            asname = n.asname.name.value if n.asname else None
                            name = (
                                n.name.value
                                if isinstance(n.name, cst.Name)
                                else None
                            )
                            if name == "snowpark_connect" and asname is None:
                                return True
                            if asname == "snowpark_connect":
                                return True
            if isinstance(s, cst.Import):
                for n in s.names:
                    asname = n.asname.name.value if n.asname else None
                    if asname == "snowpark_connect":
                        return True
    return False


def _ensure_import(module: cst.Module) -> cst.Module:
    if _has_snowpark_connect_import(module):
        return module
    body = list(module.body)
    insert_at = 0
    for i, stmt in enumerate(body):
        if isinstance(stmt, cst.SimpleStatementLine) and any(
            isinstance(s, (cst.Import, cst.ImportFrom)) for s in stmt.body
        ):
            insert_at = i + 1
        elif insert_at > 0:
            break
    new_body = body[:insert_at] + [_IMPORT_NODE] + body[insert_at:]
    return module.with_changes(body=tuple(new_body))


def apply(
    source: str, *, file: str = "<input.py>", facts_db: str | None = None
) -> _common.RecipeResult:
    # Cheap substring gate; doubles as the Glue-evidence gate for the
    # ``.spark_session`` rewrite.
    if not _has_glue_evidence(source):
        return _common.RecipeResult(source=source, edits=[])

    module = cst.parse_module(source)
    scan = _ModuleScan()
    module.visit(scan)

    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    recipe = _Recipe(
        source=source,
        file=file,
        facts_db=facts_db,
        scan=scan,
        glue_evidence=True,
    )
    new_module = wrapper.visit(recipe)
    # The import is only needed by the ``init_spark_session()`` call, so tie it
    # to that specific rewrite rather than to any edit.
    if recipe.session_rewrites > 0:
        new_module = _ensure_import(new_module)
    return _common.RecipeResult(source=new_module.code, edits=list(recipe.edits))
