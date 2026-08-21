"""Rewrite / annotate any access to ``<session>.sparkContext`` (or a
bare ``sc`` bound to a SparkContext) -- it is universally unsupported
in Spark Connect.

Why "universally"
-----------------

Snowpark Connect (SCOS) implements the Spark Connect protocol. In
upstream PySpark Connect, ANY access to ``SparkSession.sparkContext``
raises ``PySparkNotImplementedError`` at the ``.sparkContext`` step
itself, before whatever attribute or method comes after can even be
evaluated. See the vendored upstream stub:

    src/snowflake/snowpark_connect/includes/python/pyspark/sql/connect/session.py
    lines 695-698:
        elif name in ["newSession", "sparkContext"]:
            raise PySparkNotImplementedError(
                error_class="NOT_IMPLEMENTED",
                message_parameters={"feature": f"{name}()"},
            )

So enumerating "unsupported SparkContext properties" was the wrong
shape -- every ``.sparkContext.*`` chain is unsupported, regardless
of what comes after the ``.sparkContext`` segment.

What the recipe does
--------------------

Two rewrite paths, picked structurally based on how the chain is
used:

A. **Property-read context** -- ``<x>.sparkContext.<prop>`` (or
   ``sc.<prop>``) used as a value (logging, comparison, assignment
   right-hand side, ...). This is the dominant pattern in our
   telemetry (every workload that prints the app id, the master, or
   the spark version). The rewrite is::

        spark.sparkContext.applicationId
        sc.defaultParallelism

      ->

        getattr(spark, "applicationId", "scos-unsupported")
        getattr(sc, "defaultParallelism", "scos-unsupported")

   Note that the rewrite **drops the ``.sparkContext`` middle hop**
   and tries the attribute directly on the session / bound name.
   Some properties (e.g. ``version``) actually do exist on
   ``SparkSession`` in Spark Connect, so the ``getattr`` form will
   return the real value when it can and the fallback string when
   it cannot. Either way, the line never crashes.

B. **Method-call context** -- ``<x>.sparkContext.<method>(...)`` or
   ``sc.<method>(...)``. Examples: ``sc.parallelize([1,2,3])``,
   ``sc.broadcast(val)``, ``sc.setLogLevel("ERROR")``,
   ``sc.setCheckpointDir(path)``, ``sc.addFile(path)``,
   ``sc.textFile(uri)``. A ``getattr`` fallback would substitute
   the string ``"scos-unsupported"`` for the method, and then
   ``"scos-unsupported"(...)`` would raise ``TypeError``. So we do
   NOT rewrite -- we attach a ``# SCOS-TODO`` annotation naming the
   exact method so the LLM fixer can migrate it to the appropriate
   Snowpark / SparkSession surface (``createDataFrame``, no-op,
   ``spark.read.text``, etc.).

C. **Bind-pattern** -- ``sc = <x>.sparkContext`` (or any line whose
   value expression is exactly ``<x>.sparkContext`` with no further
   attribute access). This is the line that ESTABLISHES the unsupported
   binding -- if we don't annotate it, the user's program crashes at
   THIS line before any of our property / method rewrites elsewhere
   get a chance to run. We do NOT auto-rewrite (substituting ``<x>``
   for ``<x>.sparkContext`` is a behavior change we can't make
   blindly), but we annotate so the LLM fixer can either drop the
   line, replace it with ``sc = <x>`` and propagate, or migrate each
   downstream use to the SparkSession surface directly.

Trigger
-------

(A) and (B): an ``Attribute`` access ``<receiver>.<attr>`` where
``<receiver>`` is:

  * a ``Name`` literally ``sc`` (the de-facto convention), OR
  * an ``Attribute`` whose terminal attr is ``sparkContext``
    (e.g. ``spark.sparkContext``, ``self.spark.sparkContext``).

(C): a ``SimpleStatementLine`` containing an expression whose value is
``<x>.sparkContext`` with no further ``.<attr>`` after it.

Whichever path fires, the recipe attaches a leading SCOS comment
naming itself and the EWI code ``SPRKCNTPY4002``.

Negative cases (must NOT trigger)
---------------------------------

* Attribute writes / deletes -- only reads / calls.
* ``getattr(sc, "X", ...)`` already present -- idempotent (no
  matching Attribute node to rewrite).
* Bare ``sc.sparkContext`` -- non-sensical, ignored.
* References inside f-strings (PEP 701 -- nested quote conflict
  on Python <3.12). The recipe skips these; the LLM fixer / analyzer
  picks them up downstream.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "sparkcontext_property_fallback_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_FALLBACK_LITERAL = '"scos-unsupported"'
_COMMENT_READ = (
    f"# SCOS: [SPRKCNTPY4002-Fixed] {RECIPE_ID}: <session>.sparkContext is not "
    f"supported in Spark Connect (PySparkNotImplementedError at the "
    f".sparkContext access). Replaced the property read with a getattr() "
    f"fallback on the session / bound name so logging and diagnostic "
    f"code continues to run."
)
_COMMENT_CALL_TEMPLATE = (
    "# SCOS-TODO: [SPRKCNTPY4002-Error] {recipe_id}: <session>.sparkContext is not "
    "supported in Spark Connect -- the call to {method!r} cannot be "
    "auto-rewritten (a getattr() fallback would TypeError when called). "
    "Migrate to the Snowpark / SparkSession equivalent (e.g. parallelize -> "
    "spark.createDataFrame, broadcast -> Snowpark broadcast helper, "
    "setLogLevel/setCheckpointDir -> drop / no-op, textFile -> spark.read.text)."
)
_COMMENT_BIND = (
    f"# SCOS-TODO: [SPRKCNTPY4002-Error] {RECIPE_ID}: this line binds a name "
    f"to <session>.sparkContext, which raises PySparkNotImplementedError "
    f"at runtime under Spark Connect. DROP this binding entirely -- there is "
    f"no SparkContext alias under Connect. Do NOT rewrite it to `sc = spark`: "
    f"the SparkSession has no .parallelize/.textFile/.emptyRDD/.broadcast, so "
    f"aliasing only turns each downstream call into an AttributeError and masks "
    f"the unconverted code. Instead migrate each downstream `sc.<op>` to its own "
    f"target (parallelize -> spark.createDataFrame, range -> spark.range, "
    f"textFile -> spark.read.text, emptyRDD -> spark.createDataFrame([], schema), "
    f"broadcast -> use the value directly / F.broadcast(df), "
    f"setLogLevel/setCheckpointDir/cancelJobGroup -> drop). After migration the "
    f"name is unreferenced, so the binding is simply removed."
)


def _is_sc_receiver(expr: cst.BaseExpression) -> bool:
    """True iff ``expr`` is ``sc`` (Name) or terminates in
    ``sparkContext`` (Attribute)."""
    if isinstance(expr, cst.Name) and expr.value == "sc":
        return True
    if isinstance(expr, cst.Attribute):
        if isinstance(expr.attr, cst.Name) and expr.attr.value == "sparkContext":
            return True
    return False


def _unwrap_sc_receiver(expr: cst.BaseExpression) -> cst.BaseExpression:
    """For ``<x>.sparkContext`` return ``<x>``. For bare ``sc`` return
    ``sc`` (the rewrite for property reads keeps the bare receiver --
    a stray ``sc`` is no worse than a stray ``getattr(sc, ...)``)."""
    if isinstance(expr, cst.Attribute) and isinstance(expr.attr, cst.Name) and expr.attr.value == "sparkContext":
        return expr.value
    return expr


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        # Per-line accumulators by category. A single statement can
        # appear in multiple categories at once (e.g.
        # ``info = (sc.applicationId, sc.parallelize([0]).count())``
        # is BOTH a read-rewrite and a call-context annotation), so
        # each line can end up with multiple stacked comments.
        self._lines_with_read_rewrites: set[int] = set()
        self._lines_with_call_annotations: dict[int, list[str]] = {}
        self._lines_with_bind: set[int] = set()
        # Set of (id, ...) for Attribute nodes that are the "outer"
        # ``.sparkContext`` segment of a longer chain like
        # ``spark.sparkContext.applicationId``. Those Attributes are
        # already handled by the outer property-read rewrite and must
        # NOT be re-flagged as standalone bind patterns.
        self._chained_sparkcontext_attrs: set[int] = set()
        # Stack of ``Call`` nodes whose ``func`` is an Attribute we
        # might be about to visit. If the immediate Call.func is our
        # target attribute we treat it as method-call context and
        # DON'T rewrite (a getattr fallback would TypeError).
        self._call_func_attrs: list[int] = []
        # F-string nesting -- skip rewrites inside FormattedString
        # because nested quote rewriting requires Python 3.12+.
        self._fstring_depth = 0

    def visit_FormattedString(self, node: cst.FormattedString) -> None:
        self._fstring_depth += 1

    def leave_FormattedString(  # type: ignore[override]
        self,
        original_node: cst.FormattedString,
        updated_node: cst.FormattedString,
    ) -> cst.FormattedString:
        self._fstring_depth -= 1
        return updated_node

    def visit_Call(self, node: cst.Call) -> None:
        """Record the id of any Attribute appearing as ``Call.func`` so
        we can recognise method-call context when we visit that
        Attribute. ``leave_Attribute`` checks the top of this list."""
        if isinstance(node.func, cst.Attribute):
            self._call_func_attrs.append(id(node.func))

    def leave_Call(  # type: ignore[override]
        self,
        original_node: cst.Call,
        updated_node: cst.Call,
    ) -> cst.BaseExpression:
        if isinstance(original_node.func, cst.Attribute):
            if self._call_func_attrs and self._call_func_attrs[-1] == id(
                original_node.func
            ):
                self._call_func_attrs.pop()
        return updated_node

    def _is_sparkcontext_attr(self, node: cst.Attribute) -> bool:
        """``node`` is ``<x>.sparkContext.<attr>`` (intermediate) or
        ``sc.<attr>``. Either way the receiver carries the
        unsupported binding."""
        return _is_sc_receiver(node.value)

    def _attr_name(self, node: cst.Attribute) -> Optional[str]:
        if isinstance(node.attr, cst.Name):
            return node.attr.value
        return None

    def visit_Attribute(self, node: cst.Attribute) -> None:
        """Pre-mark the inner ``.sparkContext`` segment of a chain so
        ``leave_SimpleStatementLine`` doesn't mis-classify it as a
        standalone bind pattern. Example: ``spark.sparkContext.applicationId``
        contains two Attributes -- the outer ``...applicationId`` and the
        inner ``spark.sparkContext``. The inner one is part of a chain,
        not a bind."""
        if (
            isinstance(node.value, cst.Attribute)
            and isinstance(node.value.attr, cst.Name)
            and node.value.attr.value == "sparkContext"
        ):
            self._chained_sparkcontext_attrs.add(id(node.value))

    def leave_Attribute(  # type: ignore[override]
        self,
        original_node: cst.Attribute,
        updated_node: cst.Attribute,
    ) -> cst.BaseExpression:
        if self._fstring_depth > 0:
            return updated_node
        if not self._is_sparkcontext_attr(original_node):
            return updated_node
        attr_name = self._attr_name(original_node)
        if attr_name is None:
            return updated_node

        # ``hadoopConfiguration`` is handled by the dedicated
        # ``hadoop_conf_credential_todo_annotate`` recipe (which sorts before
        # this one). A getattr() fallback here would turn
        # ``sc.hadoopConfiguration.set(...)`` into
        # ``getattr(sc, "hadoopConfiguration", ...).set(...)`` -- an
        # AttributeError on the fallback string. Leave the chain intact.
        if attr_name == "hadoopConfiguration":
            return updated_node

        # If this Attribute is itself the ``func`` of a Call we just
        # entered, the context is a method call. Don't rewrite -- a
        # getattr fallback would TypeError when invoked. Annotate
        # only, capturing the method name for the comment.
        is_method_call = (
            self._call_func_attrs
            and self._call_func_attrs[-1] == id(original_node)
        )

        line = self._line_of(original_node)
        if is_method_call:
            self._lines_with_call_annotations.setdefault(line, []).append(
                attr_name
            )
            return updated_node

        # Property-read context: drop the .sparkContext hop and
        # wrap in getattr.
        self._lines_with_read_rewrites.add(line)
        receiver = _unwrap_sc_receiver(updated_node.value)
        return cst.Call(
            func=cst.Name("getattr"),
            args=[
                cst.Arg(value=receiver),
                cst.Arg(value=cst.SimpleString(f'"{attr_name}"')),
                cst.Arg(value=cst.SimpleString(_FALLBACK_LITERAL)),
            ],
        )

    def _detect_bind_in_statement(
        self, stmt: cst.SimpleStatementLine
    ) -> None:
        """Flag the line if any small-statement in ``stmt`` uses an
        ``<x>.sparkContext`` Attribute as a *terminal* value (no
        further ``.<attr>`` after it) -- e.g. an assignment RHS, a
        function argument, or a return value. We've already marked
        chained ``.sparkContext`` Attributes via ``visit_Attribute``
        and excluded them here so we don't double-flag a chain whose
        head is also being rewritten."""

        line = self._line_of(stmt)

        class _Finder(cst.CSTVisitor):
            def __init__(self, owner: "_Recipe") -> None:
                super().__init__()
                self.owner = owner
                self.found = False

            def visit_Attribute(self, node: cst.Attribute) -> None:
                if self.found:
                    return
                if not (
                    isinstance(node.attr, cst.Name)
                    and node.attr.value == "sparkContext"
                ):
                    return
                # Skip if this Attribute is the inner segment of a
                # longer chain that the property-read or method-call
                # path already handled.
                if id(node) in self.owner._chained_sparkcontext_attrs:
                    return
                self.found = True

        finder = _Finder(self)
        stmt.visit(finder)
        if finder.found:
            self._lines_with_bind.add(line)

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        # Bind-pattern detection runs against the ORIGINAL node so
        # the chained-Attribute id set we built during the visit phase
        # still matches.
        self._detect_bind_in_statement(original_node)

        line = self._line_of(original_node)
        had_read = line in self._lines_with_read_rewrites
        call_methods = self._lines_with_call_annotations.get(line, [])
        had_call = bool(call_methods)
        had_bind = line in self._lines_with_bind

        if not (had_read or had_call or had_bind):
            return updated_node

        # Idempotency: if a previous run already annotated this line,
        # don't re-record an edit and don't stack a duplicate comment.
        if _annotate.comment_above_contains(self._lines, line, RECIPE_ID):
            return updated_node

        # Stack comments in priority order: read fix first (most
        # impactful), then call-context TODO(s), then bind TODO.
        # ``_annotate.prepend_comment`` appends to the BACK of
        # ``leading_lines`` -- so each successive call lands directly
        # above the statement and BELOW the previous comment. Iterate
        # in priority order to get [read, call*, bind, statement].
        node = updated_node
        comments: list[str] = []
        if had_read:
            comments.append(_COMMENT_READ)
            self._record(line, "rewrote sparkContext property read to getattr")
        for method in call_methods:
            comments.append(
                _COMMENT_CALL_TEMPLATE.format(recipe_id=RECIPE_ID, method=method)
            )
            self._record(line, f"annotated sparkContext method call {method!r}")
        if had_bind:
            comments.append(_COMMENT_BIND)
            self._record(line, "annotated sparkContext bind pattern")
        for comment in comments:
            node = _annotate.prepend_comment(node, comment)
        return node


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
