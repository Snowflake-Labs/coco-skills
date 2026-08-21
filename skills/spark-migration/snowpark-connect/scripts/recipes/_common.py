"""Shared LibCST scaffolding for recipes under ``scripts/recipes``.

Defines the per-recipe contract (a ``recipe.py`` module exposing ``apply()``).
``_recipe_base`` is loaded as a sibling module under ``scripts/recipes/``.

Public surface:

  * ``RecipeResult``        -- dataclass returned by each recipe's ``apply()``.
  * ``BaseRecipe``          -- thin LibCST CSTTransformer subclass that knows
                                how to record an edit via ``record_edit``.
  * ``run_recipe()``        -- driver used by every recipe's ``apply()``.
  * ``output_anchor()``     -- deterministic anchor string for an edit.
  * ``load_recipe_module()``-- per-directory recipe loader used by
                                ``preprocess_recipes.py`` to discover and
                                load each recipe under a unique module name
                                so sibling recipes don't collide on the
                                bare ``recipe`` name in ``sys.modules``.
"""
from __future__ import annotations

import hashlib
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import libcst as cst

# Make the parent ``scripts/recipes`` dir importable so ``import _recipe_base``
# works regardless of how pytest is invoked (from the repo root, from
# scripts/, or from inside a recipe dir).
_RECIPES_DIR = Path(__file__).resolve().parent
if str(_RECIPES_DIR) not in sys.path:
    sys.path.insert(0, str(_RECIPES_DIR))

_recipe_base = importlib.import_module("_recipe_base")  # type: ignore


# ---------------------------------------------------------------------------
# RDD-chain gating (shared by the sc.* entry-point rewrite recipes)
# ---------------------------------------------------------------------------
# The entry-point recipes (sc.parallelize -> createDataFrame, sc.range ->
# spark.range, sc.textFile -> spark.read.text) mechanically rewrite ONLY the
# entry point. That is a strict win when the result is used as a DataFrame, but
# when the result flows into an RDD-only operation the rewrite would strand an
# RDD method on a DataFrame (e.g. `spark.createDataFrame([1,2,3]).sum()` — a
# DataFrame has no `.sum()`), producing code labeled "-Fixed" that crashes at
# runtime. Worse, rewriting the entry point ERASES the `sc.` / `.rdd` context
# token the analyzer needs to detect and holistically convert the whole chain.
#
# So the entry-point recipes SKIP rewriting when the value is part of an RDD
# chain (see `is_rdd_chain_statement` / `collect_rdd_chained_names`), leaving the
# intact RDD block for the analyzer to classify (convertible / mixed /
# no_equivalent) and the LLM fixer to convert as a unit per
# references/python/rdd-conversion.md.
#
# RDD_ONLY_CHAIN_METHODS = RDD instance methods with NO DataFrame homonym; seeing
# `<value>.<name>(` proves `<value>` is used as an RDD. Kept intentionally
# conservative — names shared with DataFrame (collect, count, first, take,
# distinct, union, filter, sample, cache, persist, unpersist, repartition,
# coalesce, toLocalIterator, foreach, isEmpty, select, join, orderBy, sort, agg,
# groupBy, ...) are EXCLUDED so a legitimate DataFrame-source rewrite is never
# suppressed. Mirror of the RDD-only surface in
# ``scripts/analyze_pyspark.py`` (RDD_METHODS / RDD_EXCLUSIVE_METHODS).
RDD_ONLY_CHAIN_METHODS = frozenset({
    # transformations
    "map", "flatMap", "mapValues", "flatMapValues", "keyBy",
    "reduceByKey", "reduceByKeyLocally", "groupByKey", "aggregateByKey",
    "foldByKey", "combineByKey", "sampleByKey", "cogroup", "groupWith",
    "subtractByKey", "leftOuterJoin", "rightOuterJoin", "fullOuterJoin",
    "zipWithIndex", "zipWithUniqueId", "sortByKey", "sortBy",
    "mapPartitions", "mapPartitionsWithIndex", "foreachPartition",
    "zip", "cartesian", "glom", "pipe", "keys", "values", "lookup",
    "repartitionAndSortWithinPartitions",
    # actions / aggregations with no DataFrame homonym
    "reduce", "fold", "aggregate", "treeAggregate", "treeReduce",
    "histogram", "takeOrdered", "takeSample", "top",
    "sum", "mean", "stdev", "variance", "sampleStdev", "sampleVariance",
    "stats", "countByKey", "countByValue", "countApproxDistinct",
    "meanApprox", "sumApprox", "collectAsMap", "collectWithJobGroup",
    # RDD-only I/O / introspection
    "saveAsTextFile", "saveAsPickleFile", "saveAsSequenceFile",
    "saveAsObjectFile", "getNumPartitions", "getResourceProfile",
    "withResources", "barrier", "id",
})


def _root_name(expr: cst.BaseExpression) -> Optional[str]:
    """Walk ``a.b(...).c[...]`` receiver chains down to the root ``Name``."""
    while True:
        if isinstance(expr, cst.Name):
            return expr.value
        if isinstance(expr, cst.Attribute):
            expr = expr.value
        elif isinstance(expr, cst.Call):
            expr = expr.func
        elif isinstance(expr, cst.Subscript):
            expr = expr.value
        else:
            return None


class _RddChainCollector(cst.CSTVisitor):
    """Record the root receiver name of every RDD-only method call."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.found = False

    def visit_Call(self, node: cst.Call) -> None:
        func = node.func
        if (
            isinstance(func, cst.Attribute)
            and isinstance(func.attr, cst.Name)
            and func.attr.value in RDD_ONLY_CHAIN_METHODS
        ):
            self.found = True
            root = _root_name(func.value)
            if root:
                self.names.add(root)


def collect_rdd_chained_names(source: str) -> set[str]:
    """Names anywhere used as the receiver of an RDD-only method (module-wide).

    Used for the assign-then-use pattern: ``rdd = sc.parallelize(seq)`` on one
    line, ``rdd.sortByKey(...)`` on another. Returns an empty set on parse error
    (fail-open: the recipe rewrites as before).
    """
    try:
        module = cst.parse_module(source)
    except Exception:
        return set()
    collector = _RddChainCollector()
    module.visit(collector)
    return collector.names


def is_rdd_chain_statement(node: cst.CSTNode) -> bool:
    """True iff ``node`` (a statement subtree) itself contains an RDD-only method
    call — the inline-chain case (``sc.parallelize([1,2,3]).sum()``)."""
    collector = _RddChainCollector()
    node.visit(collector)
    return collector.found


def assignment_target_names(stmt: cst.CSTNode) -> set[str]:
    """Simple ``Name`` targets on the LHS of an assignment statement."""
    names: set[str] = set()
    body = getattr(stmt, "body", None)
    if body is None:
        return names
    for small in body:
        if isinstance(small, cst.Assign):
            for tgt in small.targets:
                if isinstance(tgt.target, cst.Name):
                    names.add(tgt.target.value)
        elif isinstance(small, cst.AnnAssign) and small.value is not None:
            if isinstance(small.target, cst.Name):
                names.add(small.target.value)
    return names


@dataclass
class RecipeResult:
    """What every recipe returns from ``apply()``.

    ``edits`` lists every recipe_edits row that was written (or would have
    been written, if no facts_db is configured). The per-recipe pytest asserts
    on this list directly so we don't need to thread sqlite into every test.
    """

    source: str
    edits: list = field(default_factory=list)


def output_anchor(recipe_id: str, src_line: int, snippet: str) -> str:
    """Deterministic, short anchor string for the
    ``recipe_edits.output_line_anchor`` column.

    Format: ``<recipe_id>:<src_line>:<8-hex-hash-of-snippet>``.
    """
    digest = hashlib.sha1(snippet.encode("utf-8")).hexdigest()[:8]
    return f"{recipe_id}:{src_line}:{digest}"


class BaseRecipe(cst.CSTTransformer):
    """Minimal base class for recipes.

    Subclasses set ``RECIPE_ID`` (str) and override one or more ``leave_*``
    methods. When they perform an edit they MUST call
    ``self._record(src_line, snippet)`` so the in-memory ``edits`` list and
    the facts.sqlite ``recipe_edits`` table stay in sync.

    PositionProvider is declared as a metadata dependency so subclasses can
    call ``self.get_metadata(cst.metadata.PositionProvider, node).start.line``
    on the *original* node passed to ``leave_*``. ``run_recipe()`` sets up the
    matching ``MetadataWrapper``.
    """

    METADATA_DEPENDENCIES = (cst.metadata.PositionProvider,)

    RECIPE_ID: str = ""

    def __init__(
        self,
        *,
        source: str,
        file: str,
        facts_db: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._lines = source.splitlines(keepends=False)
        self._file = file
        self._facts_db = facts_db
        self.edits: list = []
        self._seen_src_lines: set[int] = set()

    def _line_of(self, original_node: cst.CSTNode) -> int:
        """1-based source line of ``original_node`` (must be from the input
        tree, not a copy returned from a leave_* hook)."""
        pos = self.get_metadata(cst.metadata.PositionProvider, original_node)
        return pos.start.line

    def _record(self, src_line: int, snippet: str) -> None:
        """Write a recipe_edits row. Idempotent per (file, src_line)."""
        if src_line in self._seen_src_lines:
            return
        self._seen_src_lines.add(src_line)
        anchor = output_anchor(self.RECIPE_ID, src_line, snippet)
        edit = _recipe_base.record_edit(
            file=self._file,
            src_line=src_line,
            recipe_id=self.RECIPE_ID,
            output_line_anchor=anchor,
            facts_db=self._facts_db,
        )
        self.edits.append(edit)


def run_recipe(
    recipe_cls: type[BaseRecipe],
    source: str,
    *,
    file: str = "<input.py>",
    facts_db: Optional[str] = None,
) -> RecipeResult:
    """Apply ``recipe_cls`` to ``source`` and return a ``RecipeResult``.

    Always wraps the parsed module in a ``MetadataWrapper`` so subclasses can
    resolve PositionProvider via ``self.get_metadata(...)``.
    """
    module = cst.parse_module(source)
    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    recipe = recipe_cls(source=source, file=file, facts_db=facts_db)
    new_module = wrapper.visit(recipe)
    return RecipeResult(source=new_module.code, edits=list(recipe.edits))


def load_recipe_module(recipe_dir):
    """Load ``recipe.py`` next to ``recipe_dir`` under a unique module name
    derived from the directory.

    Used by ``preprocess_recipes.py`` to discover and load every recipe in
    a single process. Plain ``import recipe`` would collide between sibling
    recipes because Python caches the module by short name; using
    ``importlib.util.spec_from_file_location`` with a unique module name
    avoids that collision.
    """
    import importlib.util
    from pathlib import Path

    recipe_dir = Path(recipe_dir)
    mod_name = f"recipes__{recipe_dir.name}__recipe"
    spec = importlib.util.spec_from_file_location(mod_name, recipe_dir / "recipe.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


__all__ = [
    "BaseRecipe",
    "RecipeResult",
    "run_recipe",
    "output_anchor",
    "load_recipe_module",
    "RDD_ONLY_CHAIN_METHODS",
    "collect_rdd_chained_names",
    "is_rdd_chain_statement",
    "assignment_target_names",
]
