"""Annotate Spark *runtime/cluster* configs that are silent no-ops on SCOS.

What it does
------------

Snowpark Connect (SCOS) accepts ``spark.conf.set(key, value)`` for any key —
``set_config_param`` in the SCOS engine stores the value in ``global_config``
unconditionally — but only a fixed set of keys is ever *read*. Cluster- and
runtime-resource configs (``spark.executor.*``, ``spark.dynamicAllocation.*``,
``spark.shuffle.*``, ``spark.serializer``, ...) are stored-but-never-consumed:
Snowflake's warehouse manages compute, so those knobs have **no effect**. They
do not raise — they are silently ignored.

This recipe is **annotate-only**. It prepends a uniform ``# SCOS-WARN`` marker
(EWI ``SPRKCNTPY3500`` / "No-Op Config") above any statement that sets such a
key, so:

  * the no-op is visible to the human reviewer instead of looking effective, and
  * ``generate_scos_reports.py`` surfaces it in ``Issues.csv`` deterministically
    rather than relying on the LLM fixer to notice it.

It deliberately does **not** delete the call: SCOS ignores the value anyway, so
leaving it is harmless, and removing it risks disturbing a non-SCOS code path
that still reads the same SparkConf.

Ground truth
------------

The DROP families below are exactly the spark.* config families that do **not**
appear in SCOS's ``GlobalConfig.default_global_config``,
``SessionConfig.default_session_config``, ``SESSION_CONFIG_KEY_WHITELIST``, or
``set_snowflake_parameters`` match arms (see
``snowflake/snowpark_connect/config.py``). Keys that *are* honored — every
``spark.sql.*`` semantic knob (``ansi.enabled``, ``session.timeZone``,
``timestampType``, ``storeAssignmentPolicy``, ``caseSensitive``, ...), all
``snowpark.connect.*`` / ``snowflake.*`` knobs, ``spark.app.name``,
``spark.jars``, and the whitelisted ``spark.hadoop.fs.s3a.*`` / Azure
credential keys — are **never** annotated here. Unknown keys are left untouched
(deferred to the LLM fixer / spark-config.md reference), so the recipe can never
silently mislabel a semantics-affecting config as a no-op.

Targeted statement shapes
-------------------------

* ``spark.conf.set("spark.executor.memory", "4g")`` — ``<x>.conf.set(...)``.
* ``SparkSession.builder.config("spark.executor.cores", "4")...`` — a
  ``.config(...)`` call (the ``spark_builder_drop_master_init_session_rewrite``
  recipe runs earlier alphabetically and converts in-chain ``.config(...)`` to
  ``conf.set(...)``, which this recipe then annotates; bare ``.config(...)``
  outside a ``getOrCreate()`` chain is covered directly).

Idempotency
-----------

Re-running on annotated source is a no-op (leading-comment check via
``_annotate.comment_above_contains``).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "spark_config_noop_annotate"
MIN_SCOS_VERSION = "0.4.0"

# Exact keys that are silent no-ops on SCOS.
_NOOP_KEYS = frozenset(
    {
        "spark.cores.max",
        "spark.default.parallelism",
        "spark.driver.memory",
        "spark.driver.cores",
        "spark.driver.maxResultSize",
        "spark.driver.memoryOverhead",
        "spark.extraListeners",
        "spark.logConf",
        "spark.local.dir",
    }
)

# Dotted prefixes whose whole family is a silent no-op on SCOS (cluster /
# runtime-resource / infra knobs Snowflake's warehouse owns).
_NOOP_PREFIXES = (
    "spark.executor.",
    "spark.dynamicAllocation.",
    "spark.shuffle.",
    "spark.kryo.",
    "spark.kryoserializer.",
    "spark.memory.",
    "spark.speculation",
    "spark.task.",
    "spark.scheduler.",
    "spark.yarn.",
    "spark.kubernetes.",
    "spark.mesos.",
    "spark.network.",
    "spark.rpc.",
    "spark.broadcast.",
    "spark.eventLog.",
    "spark.history.",
    "spark.ui.",
    "spark.metrics.",
    "spark.cleaner.",
    "spark.storage.",
    "spark.reducer.",
    "spark.blockManager.",
    "spark.locality.",
)

# Prefixes that are ALWAYS honored / semantics-affecting on SCOS — a hard guard
# so a future addition to the no-op lists can never strip these. Checked first.
_HONORED_PREFIXES = (
    "spark.sql.",
    "snowpark.connect.",
    "snowflake.",
    "spark.hadoop.",  # s3a / azure credential keys are session-whitelisted
    "spark.jars",  # spark.jars is honored (JPype classpath)
)
_HONORED_EXACT = frozenset({"spark.app.name", "spark.driver.host"})


def _is_noop_key(key: str) -> bool:
    if key in _HONORED_EXACT:
        return False
    if any(key.startswith(p) for p in _HONORED_PREFIXES):
        return False
    if key in _NOOP_KEYS:
        return True
    return any(key.startswith(p) for p in _NOOP_PREFIXES)


def _first_string_arg(call: cst.Call) -> Optional[str]:
    """Evaluated value of the first positional string-literal arg, else None."""
    for arg in call.args:
        if arg.keyword is not None:
            continue
        node = arg.value
        if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
            try:
                return node.evaluated_value
            except Exception:  # noqa: BLE001
                return None
        return None  # first positional arg is not a static string
    return None


def _is_conf_set(call: cst.Call) -> bool:
    """``<anything>.conf.set(...)`` — receiver attribute is ``conf``."""
    func = call.func
    return (
        isinstance(func, cst.Attribute)
        and isinstance(func.attr, cst.Name)
        and func.attr.value == "set"
        and isinstance(func.value, cst.Attribute)
        and isinstance(func.value.attr, cst.Name)
        and func.value.attr.value == "conf"
    )


def _is_config_call(call: cst.Call) -> bool:
    """``....config(...)`` — builder-style config setter."""
    func = call.func
    return (
        isinstance(func, cst.Attribute)
        and isinstance(func.attr, cst.Name)
        and func.attr.value == "config"
    )


class _Detector(cst.CSTVisitor):
    """Record the first no-op config key set in the statement subtree."""

    def __init__(self) -> None:
        super().__init__()
        self.key: Optional[str] = None

    def visit_Call(self, node: cst.Call) -> None:
        if self.key is not None:
            return
        if not (_is_conf_set(node) or _is_config_call(node)):
            return
        key = _first_string_arg(node)
        if key is not None and _is_noop_key(key):
            self.key = key


def _comment_for(key: str) -> str:
    return (
        f"# SCOS-WARN: [SPRKCNTPY3500-Warning] {RECIPE_ID}: '{key}' is a no-op on SCOS "
        f"(Snowflake warehouse manages compute/runtime); the value is ignored. "
        f"Safe to remove."
    )


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        start = self._line_of(original_node)
        if _annotate.comment_above_contains(self._lines, start, RECIPE_ID):
            return updated_node
        det = _Detector()
        updated_node.visit(det)
        if det.key is None:
            return updated_node
        self._record(start, f"annotated no-op config {det.key!r}")
        return _annotate.prepend_comment(updated_node, _comment_for(det.key))


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
