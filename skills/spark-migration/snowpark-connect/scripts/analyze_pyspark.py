# flake8: noqa: T201

"""
SCOS Migration Agent - PySpark Compatibility Analyzer

Analyze PySpark scripts for potential SCOS compatibility issues.

Usage:
    python analyze_pyspark.py --path /path/to/script.py
    python analyze_pyspark.py --path /path/to/scripts/

This script:
1. Parses PySpark files using Python AST (handles multi-line statements)
2. Extracts complete SQL expressions and method chains
3. Checks API compatibility from the compatibility CSV
4. Uses unified RAG to find similar failing SQL and DataFrame patterns
5. Reports results with root causes and workarounds
"""

import argparse
import ast
import builtins
import csv
import json
import logging
import os
import re
import sys
import textwrap
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from code_normalization import normalize_code_lightweight
from notebook_io import detect_format, is_notebook, parse_notebook, walk_filtered
from rag import BaseRAG
from rag.trigger_kb import (
    SEVERITY_SCORE,
    SCOSTriggerRAG,
    TriggerKB,
    is_gated_nested_lambda_capture,
)
from rag.sql_rewrite import SQL_FIXER_ACTIONS
from scos_session import (
    add_connectivity_args,
    build_rag,
    open_session,
)

from snowflake.snowpark import Session

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"

# SNOW-3347480: Safe-API allowlist — APIs that need no RAG lookup
_SAFE_APIS: set[str] | None = None
_SAFE_API_SKIPS: int = 0  # Counter for skipped queries


def load_safe_apis(json_path: Path | None = None) -> set[str]:
    """
    Load the safe-API allowlist from JSON.

    Returns a set of API pattern strings that are confirmed fully compatible
    with Spark Connect and require no RAG query.

    Falls back to empty set (all APIs queried) if file is missing.
    """
    if json_path is None:
        json_path = DATA_DIR / "safe_apis.json"
    if not json_path.exists():
        logger.warning("Safe-API allowlist not found at %s — all APIs will be queried", json_path)
        return set()
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        apis = {entry["pattern"] for entry in data.get("apis", [])}
        logger.info("Loaded %d safe-API patterns from %s", len(apis), json_path.name)
        return apis
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Failed to parse safe-API allowlist %s: %s — all APIs will be queried", json_path, exc)
        return set()


def load_kb_anchor_leaves(kb_path: Path | None = None) -> set[str]:
    """
    Bare method/function names the trigger KB has a rule for.

    ``groupBy``, ``join``, ``dropDuplicates``, etc. are marked "fully
    supported" in safe_apis.json but have a real, sometimes high-severity
    divergence documented in kb_rules.json. Used by ``reconcile_safe_apis``
    to keep those APIs off the safe-list fast path. Returns an empty set if
    the KB file is missing or fails to parse.
    """
    try:
        kb = TriggerKB.load(kb_path or (DATA_DIR / "kb_rules.json"))
        return kb.anchor_leaf_names()
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("Failed to load KB anchors for safe-list reconciliation: %s", exc)
        return set()


def reconcile_safe_apis(safe_apis: set[str], kb_anchor_leaves: set[str]) -> set[str]:
    """
    Drop any API from the safe list that also has a KB divergence rule.

    Called once at load time so every downstream user of the safe list
    (is_block_safe and its callers) gets the corrected set for free, with
    no change to their own signatures.
    """
    if not kb_anchor_leaves:
        return safe_apis
    return {api for api in safe_apis if api.lower() not in kb_anchor_leaves}


def is_block_safe(block_functions: list[str], safe_apis: set[str]) -> bool:
    """
    Check if ALL functions in a code block are in the safe-API allowlist.

    SNOW-3347480: If every function in the block is known-safe, we skip
    the RAG query entirely for this block.
    """
    if not safe_apis or not block_functions:
        return False
    return all(func in safe_apis for func in block_functions)


def has_nested_lambda_capture(code: str) -> bool:
    """True when ``code`` contains a higher-order call whose inner lambda reads
    an ENCLOSING lambda's variable — unresolvable in SCOS.

    The safe-API fast path is keyed on function NAMES, so it cannot see this
    shape: every name involved can be on the allowlist while the nesting still
    diverges. A block carrying it must reach the trigger KB.

    Applies the KB's own ``nested_lambda_capture`` predicate, so the fast path
    skips exactly the blocks the gate would decline to flag — one definition of
    the shape, and no block pays for a RAG lookup that cannot yield a finding.
    """
    if code.count("lambda") < 2:
        return False
    try:
        tree = ast.parse(textwrap.dedent(code))
    except SyntaxError:
        return True  # cannot rule it out; take the slow, safe path
    return any(is_gated_nested_lambda_capture(n) for n in ast.walk(tree))


# --- Condition-aware decidability (deterministic, AST/SQL — no LLM) -----------
# Makes condition-gated triggers (percentile_approx/collect_list/corr ...)
# DECIDABLE so they bypass the COMPLETE pass exactly like other decidable
# triggers: condition unmet -> cleared (drop the candidate, false positive in
# this context); condition met -> decidable real finding; INDETERMINATE
# (e.g. window applied to the function via a variable in another block) ->
# left non-decidable so it falls through to the residual COMPLETE pass.
try:
    from static_condition_pass import (
        _extract_sql_strings as _sc_sql_strings,
        _load_conditional_fns as _sc_load_cond,
        _scan_python as _sc_scan_py,
        _scan_sql as _sc_scan_sql,
    )
    _COND_FNS = _sc_load_cond(None)
except Exception:
    _COND_FNS = {}


_FILE_SRC_CACHE: dict[str, str | None] = {}


def _read_file_source_cached(file_path) -> str | None:
    """Read a source file's full text once (cached per path). Used to resolve
    named window variables that are defined outside the block being analyzed.
    Returns None if the file cannot be read."""
    key = str(file_path)
    if key not in _FILE_SRC_CACHE:
        try:
            _FILE_SRC_CACHE[key] = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            _FILE_SRC_CACHE[key] = None
    return _FILE_SRC_CACHE[key]


def _conditional_verdicts(code: str, assignment_source: str | None = None) -> dict[str, str]:
    """fn -> 'met' | 'cleared' | 'indeterminate' for condition-gated triggers in
    this block. Priority met > indeterminate > cleared (never clear if any usage
    is windowed/distinct or indeterminate).

    ``assignment_source`` (the whole file) is used only to resolve named window
    variables defined outside the current block — e.g. a ``row_number().over(w)``
    whose ``w`` was assigned with ``.orderBy(...)`` earlier in the file — so the
    window-ORDER-BY condition can be decided instead of left indeterminate."""
    if not _COND_FNS or not code:
        return {}
    occ = _sc_scan_py(code, _COND_FNS, "<block>", assignment_src=assignment_source)
    for q in _sc_sql_strings(code):
        occ += _sc_scan_sql(q, _COND_FNS, "<block>")
    verdict: dict[str, str] = {}
    rank = {"met": 3, "indeterminate": 2, "cleared": 1}
    for o in occ:
        v = "met" if o["met"] is True else ("cleared" if o["met"] is False else "indeterminate")
        cur = verdict.get(o["function"])
        if cur is None or rank[v] > rank[cur]:
            verdict[o["function"]] = v
    return verdict


def _match_cond_fn(cand: dict, verdicts: dict[str, str]) -> str | None:
    blob = ((cand.get("code") or "") + " " + (cand.get("root_cause") or "")
            + " " + (cand.get("test_name") or "")).lower()
    for fn in verdicts:
        if re.search(r"\b" + re.escape(fn) + r"\b", blob):
            return fn
    return None


def apply_condition_resolution(candidates: list[dict], code: str, assignment_source: str | None = None) -> list[dict]:
    """Drop candidates whose condition is provably unmet (false positive here),
    and mark condition-met candidates DECIDABLE so the block bypasses COMPLETE.
    Indeterminate conditions are left untouched -> residual LLM pass."""
    verdicts = _conditional_verdicts(code, assignment_source)
    if not verdicts:
        return candidates
    out = []
    for c in candidates:
        fn = _match_cond_fn(c, verdicts)
        if fn is None:
            out.append(c)
            continue
        if verdicts[fn] == "cleared":
            continue  # condition unmet -> not a problem in this context
        if verdicts[fn] == "met":
            c["decidable"] = True  # real, structurally-decided -> no LLM needed
            c["_detected_by"] = "condition"  # provenance for detected_by tagging
        out.append(c)  # 'indeterminate' kept as-is (stays non-decidable -> LLM)
    return out


# Compatibility scores (0-1 scale)
COMPAT_SCORES = {
    "D0": 1.0,
    "D1": 0.8,
    "D2": 0.5,
    "NONE": 0.0,
    "UNKNOWN": None,
    "OUTOFSCOPE": 0.0,
}

# SNOW-3347695: Per-property SparkContext replacement table with risk scores and static fallbacks
SPARK_CONTEXT_PROPERTIES = {
    "master": {
        "risk": 0.4,
        "replacement": '"sc://" + os.environ.get("SPARK_CONNECT_URL", "local")',
        "reason": "sparkContext.master is not available in Spark Connect. Replace with static string for diagnostic logging.",
        "category": "SparkContext Property",
    },
    "applicationId": {
        "risk": 0.2,
        "replacement": 'spark.conf.get("spark.app.id", "unknown")',
        "reason": "sparkContext.applicationId is not available in Spark Connect. Use spark.conf.get() instead.",
        "category": "SparkContext Property",
    },
    "appName": {
        "risk": 0.2,
        "replacement": 'spark.conf.get("spark.app.name", "unknown")',
        "reason": "sparkContext.appName is not available in Spark Connect. Use spark.conf.get() instead.",
        "category": "SparkContext Property",
    },
    "getConf": {
        "risk": 0.3,
        "replacement": "spark.conf",
        "reason": "sparkContext.getConf is not available in Spark Connect. Use spark.conf.get(key) / spark.conf.getAll instead.",
        "category": "SparkContext Property",
    },
    "statusTracker": {
        "risk": 0.6,
        "replacement": 'int(os.environ.get("SPARK_WORKER_NODES", "1"))',
        "reason": "sparkContext.statusTracker is not available in Spark Connect. No equivalent — use environment variables.",
        "category": "SparkContext Property",
    },
    "_jvm": {
        "risk": 1.0,
        "replacement": None,
        "reason": "sparkContext._jvm is not available in Spark Connect. Hard blocker — requires full rewrite to cloud-native API.",
        "category": "SparkContext Property",
    },
    "_jsc": {
        "risk": 1.0,
        "replacement": None,
        "reason": "sparkContext._jsc is not available in Spark Connect. Hard blocker — requires full rewrite to cloud-native API.",
        "category": "SparkContext Property",
    },
    "hadoopConfiguration": {
        "risk": 1.0,
        "replacement": None,
        "reason": "sparkContext.hadoopConfiguration is not available in Spark Connect. Use Snowflake storage integration for credentials, boto3/stage for filesystem access.",
        "category": "SparkContext Property",
    },
    "parallelize": {
        "risk": 0.8,
        "replacement": "spark.createDataFrame()",
        "reason": "sparkContext.parallelize is not available in Spark Connect. Use spark.createDataFrame() instead.",
        "category": "SparkContext Property",
    },
    "textFile": {
        "risk": 0.8,
        "replacement": "spark.read.text()",
        "reason": "sparkContext.textFile is not available in Spark Connect. Use spark.read.text() with stage-based path.",
        "category": "SparkContext Property",
    },
    "broadcast": {
        "risk": 0.7,
        "replacement": None,
        "reason": "sparkContext.broadcast is not available in Spark Connect. Use DataFrame join hints (broadcast(df)) or pass lookup data as regular variables for small datasets.",
        "category": "SparkContext Property",
    },
    "accumulator": {
        "risk": 0.7,
        "replacement": None,
        "reason": "sparkContext.accumulator is not available in Spark Connect, but it is NOT a blanket TODO: a driver-side accumulator (count/sum/min/max/avg/distinct/sketch) maps to a DataFrame aggregation — see references/python/rdd-conversion.md §12. Only cache-hit counting, mid-job progress polling, foreachPartition sinks, and writeStream.foreachBatch cross-batch state are true hard gaps (§13).",
        "category": "SparkContext Property",
    },
    "version": {
        "risk": 0.1,
        "replacement": "spark.version",
        "reason": "sparkContext.version is not available in Spark Connect. Use spark.version instead.",
        "category": "SparkContext Property",
    },
    "defaultParallelism": {
        "risk": 0.3,
        "replacement": 'int(os.environ.get("SPARK_DEFAULT_PARALLELISM", "200"))',
        "reason": "sparkContext.defaultParallelism is not available in Spark Connect. Use environment variable or default.",
        "category": "SparkContext Property",
    },
    "defaultMinPartitions": {
        "risk": 0.3,
        "replacement": 'int(os.environ.get("SPARK_MIN_PARTITIONS", "2"))',
        "reason": "sparkContext.defaultMinPartitions is not available in Spark Connect. Use environment variable or default.",
        "category": "SparkContext Property",
    },
    "uiWebUrl": {
        "risk": 0.3,
        "replacement": '"N/A — Spark Connect mode"',
        "reason": "sparkContext.uiWebUrl is not available in Spark Connect. Replace with static string.",
        "category": "SparkContext Property",
    },
}

# SNOW-3347699: Hadoop filesystem access patterns
HADOOP_PATTERNS = {
    "FileSystem.get": {
        "risk": 1.0,
        "reason": "Hadoop FileSystem.get() requires JVM interop (SparkContext._jvm) not available in Spark Connect. Replace with boto3/azure-storage-blob/google-cloud-storage.",
        "category": "Hadoop Filesystem",
        "how_to_fix": "Replace with cloud-native SDK: boto3 for S3, azure-storage-blob for ABFS, google-cloud-storage for GCS, or Snowflake stage operations.",
    },
    "hadoop.fs.Path": {
        "risk": 1.0,
        "reason": "Hadoop Path operations require JVM interop not available in Spark Connect.",
        "category": "Hadoop Filesystem",
        "how_to_fix": "Use Python pathlib or cloud-native SDK for path operations.",
    },
    "hadoopConfiguration().set": {
        "risk": 1.0,
        "reason": "Hadoop configuration for cloud credentials is not available in Spark Connect. Use Snowflake storage integration instead.",
        "category": "Hadoop Filesystem",
        "how_to_fix": "Create a Snowflake storage integration for the cloud provider. See: CREATE STORAGE INTEGRATION.",
    },
}

# SNOW-3347699: DBFS path patterns
DBFS_PATH_PATTERNS = [
    "dbfs:/",
    "dbfs:",
    "/mnt/",
]

# SNOW-3347693: JVM-only library imports that won't work in Spark Connect
JVM_ONLY_IMPORTS = {
    "pydeequ": {
        "risk": 1.0,
        "reason": "pydeequ requires JVM interop (Amazon Deequ) not available in Spark Connect. Replace with native DataFrame validation.",
        "category": "JVM Library",
        "how_to_fix": (
            "Replace Deequ checks with native DataFrame equivalents: "
            "isComplete → filter(col.isNull()).count(), "
            "isUnique → groupBy(col).count().filter(count > 1), "
            "isNonNegative → filter(col < 0).count(), "
            "hasCompleteness → (total - nulls) / total >= threshold."
        ),
    },
    "great_expectations.dataset.sparkdf_dataset": {
        "risk": 1.0,
        "reason": "Great Expectations SparkDFDataset requires SparkContext not available in Spark Connect.",
        "category": "JVM Library",
        "how_to_fix": "Use Great Expectations with PandasDataset or SqlAlchemyDataset, or use native DataFrame validation.",
    },
    "com.amazon.deequ": {
        "risk": 1.0,
        "reason": "Amazon Deequ is a JVM-only library not available in Spark Connect.",
        "category": "JVM Library",
        "how_to_fix": "Replace with native DataFrame validation operations.",
    },
    "com.holdenkarau.spark.testing": {
        "risk": 1.0,
        "reason": "Spark Testing Base requires SparkContext not available in Spark Connect.",
        "category": "JVM Library",
        "how_to_fix": "Use pytest with SparkSession.builder for testing, or mock-based testing approaches.",
    },
}

# SNOW-3390000: Unsupported Spark ecosystem libraries with no Snowpark Connect
# equivalent. These have NO curated trigger in kb_rules.json and are not caught
# by the pyspark.ml / JVM-validation detectors, so without this detector a block
# that uses them produces no rule-based issue and (absent a fuzzy RAG match) is
# dropped BEFORE the COMPLETE pass — the analyzer never sees it and the block
# ships un-migrated. Surfacing them here forces the block into the LLM pass,
# where the "HARD RISK FLOOR" prompt rule assigns high risk and a forced
# conversion. Keyed by import module; usage-token fallbacks below catch cases
# where the import lives in a different notebook cell.
UNSUPPORTED_ECOSYSTEM_IMPORTS = {
    # AWS Glue. Every `awsglue.*` import is a guaranteed ImportError off Glue,
    # and it is the single largest blocker family in a Glue workload — without
    # this entry `awsglue` fell through to the generic 0.4 "unknown import"
    # fail-safe and the assessment under-reported the risk. Only the IMPORT is
    # flagged here (no `_ECOSYSTEM_USAGE_TOKENS` entries) so that transform
    # blocks still reach the per-construct `scos:glue-*` KB rules, which carry
    # the actionable per-API guidance and the SPRKCNTPY36xx codes.
    "awsglue": {
        "risk": 1.0,
        "reason": "AWS Glue (awsglue: GlueContext, DynamicFrame, awsglue.transforms, gluetypes, job bookmarks) is a Glue-runtime-only library with no Snowpark Connect equivalent.",
        "category": "Unsupported Ecosystem Library",
        "how_to_fix": "Migrate per the AWS Glue recipe catalog in references/python/glue-recipes.md: GlueContext/Job -> snowpark_connect.init_spark_session() (G1), create_dynamic_frame.from_catalog -> spark.read.table + lowercase identifier normalization (G2), ApplyMapping -> select/cast/alias (G4), Filter.apply -> null-safe Column predicate (G5), and drop the DynamicFrame wrappers entirely (G6).",
    },
    "graphframes": {
        "risk": 1.0,
        "reason": "GraphFrames is a JVM-backed Spark package (GraphFrame, pageRank, connectedComponents, shortestPaths, labelPropagation) with no Snowpark Connect equivalent.",
        "category": "Unsupported Ecosystem Library",
        "how_to_fix": "Reimplement the graph algorithm in Snowflake SQL/Snowpark (e.g. recursive CTEs for traversal/connectivity) or an external graph service; there is no drop-in SCOS graph API.",
    },
    "sparknlp": {
        "risk": 1.0,
        "reason": "Spark NLP (John Snow Labs) is a JVM-backed Spark package not available in Snowpark Connect.",
        "category": "Unsupported Ecosystem Library",
        "how_to_fix": "Use Snowflake Cortex LLM/NLP functions or a Snowpark UDF backed by a Python NLP library (spaCy/transformers) instead.",
    },
    "mosaic": {
        "risk": 1.0,
        "reason": "Databricks Mosaic (geospatial) is a JVM-backed Spark extension not available in Snowpark Connect.",
        "category": "Unsupported Ecosystem Library",
        "how_to_fix": "Use Snowflake's native GEOGRAPHY/GEOMETRY types and ST_* functions instead of Mosaic.",
    },
    "sparkxgb": {
        "risk": 1.0,
        "reason": "spark-xgboost (sparkxgb / xgboost4j-spark) trains distributed on the Spark cluster and is not available in Snowpark Connect.",
        "category": "Unsupported Ecosystem Library",
        "how_to_fix": "Train with snowflake-ml-python (Snowpark ML) or run XGBoost inside a Snowpark Python UDF/stored procedure.",
    },
    "xgboost4j": {
        "risk": 1.0,
        "reason": "xgboost4j-spark is a JVM Spark integration not available in Snowpark Connect.",
        "category": "Unsupported Ecosystem Library",
        "how_to_fix": "Train with snowflake-ml-python (Snowpark ML) or run XGBoost inside a Snowpark Python UDF/stored procedure.",
    },
    "databricks.koalas": {
        "risk": 0.9,
        "reason": "Koalas (databricks.koalas) is a Databricks pandas-on-Spark package not available in Snowpark Connect.",
        "category": "Unsupported Ecosystem Library",
        "how_to_fix": "Rewrite as Snowpark DataFrame operations, or use Snowpark pandas (modin on Snowflake) where available.",
    },
    "pyspark.pandas": {
        "risk": 0.9,
        "reason": "pandas-on-Spark (pyspark.pandas) is not supported in Snowpark Connect.",
        "category": "Unsupported Ecosystem Library",
        "how_to_fix": "Rewrite as Snowpark DataFrame operations, or use Snowpark pandas (modin on Snowflake) where available.",
    },
    "synapse.ml": {
        "risk": 1.0,
        "reason": "SynapseML / MMLSpark is a JVM-backed Spark library not available in Snowpark Connect.",
        "category": "Unsupported Ecosystem Library",
        "how_to_fix": "Replace with snowflake-ml-python (Snowpark ML) or Snowflake Cortex functions.",
    },
    "mmlspark": {
        "risk": 1.0,
        "reason": "MMLSpark (legacy SynapseML) is a JVM-backed Spark library not available in Snowpark Connect.",
        "category": "Unsupported Ecosystem Library",
        "how_to_fix": "Replace with snowflake-ml-python (Snowpark ML) or Snowflake Cortex functions.",
    },
    "petastorm": {
        "risk": 0.9,
        "reason": "Petastorm reads Spark/Parquet datasets into distributed DL training loops; its Spark converter is not available in Snowpark Connect.",
        "category": "Unsupported Ecosystem Library",
        "how_to_fix": "Materialize training data to a Snowflake stage/table and load it with the Snowflake ML data connector or a Snowpark UDF.",
    },
}

# Usage tokens that indicate one of the above ecosystem libraries even when the
# import statement lives in a different notebook cell (so the per-block scan
# still fires). Maps a literal token -> owning module key above.
_ECOSYSTEM_USAGE_TOKENS = {
    "GraphFrame(": "graphframes",
    ".pageRank(": "graphframes",
    ".connectedComponents(": "graphframes",
    ".shortestPaths(": "graphframes",
    ".labelPropagation(": "graphframes",
}

# SNOW-3390000: Unknown-import FAIL-SAFE support.
# Enumerated blocklists (above) only catch libraries we thought of. The durable
# guard against the long tail is a fail-safe: a block that imports a module which
# is NOT recognizably safe (stdlib / pyspark core / a curated driver-safe
# allowlist) and is not already covered by a dedicated detector should be routed
# to the LLM for review rather than silently dropped. These third-party modules
# run fine on the SCOS *driver* (no Spark-cluster / JVM coupling), so importing
# them is not, by itself, a compatibility problem — do not flag them.
SAFE_THIRD_PARTY_IMPORTS = frozenset({
    # Spark/Snowflake frameworks (submodule incompatibilities handled elsewhere)
    "pyspark", "snowflake", "snowpark",
    # Numeric / data / viz
    "numpy", "pandas", "scipy", "sklearn", "statsmodels", "matplotlib",
    "seaborn", "plotly", "altair", "polars", "duckdb", "pyarrow", "dask",
    # ML / DL (driver-side use is fine; distributed Spark variants are separate)
    "torch", "tensorflow", "keras", "transformers", "xgboost", "lightgbm",
    "catboost", "joblib", "nltk", "spacy",
    # IO / cloud / web / utils
    "requests", "urllib3", "httpx", "aiohttp", "boto3", "botocore", "google",
    "azure", "yaml", "toml", "dateutil", "pytz", "fsspec", "s3fs", "tqdm",
    "pydantic", "jinja2", "openpyxl", "xlrd", "xlsxwriter", "sqlalchemy",
    "psycopg2", "pymysql", "cryptography", "jwt", "bs4", "lxml", "PIL", "cv2",
    "dotenv", "click", "typer", "rich", "loguru", "pytest",
})

# Full module keys already handled by a dedicated unsupported-API detector, so
# the unknown-import fail-safe must NOT double-flag them.
_KNOWN_UNSUPPORTED_KEYS = (
    set(UNSUPPORTED_ECOSYSTEM_IMPORTS)
    | set(JVM_ONLY_IMPORTS)
    | {"delta", "delta.tables"}
)

# Matches the module path in `import x.y` / `from x.y import z` (also the first
# module of a comma list; per-line MULTILINE scan).
_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", re.MULTILINE)

# Python standard-library module names (3.10+). Used to distinguish stdlib
# imports (always safe) from third-party ones.
_STDLIB_MODULES = getattr(sys, "stdlib_module_names", frozenset())


def _is_reviewable_import(module: str) -> bool:
    """True if importing ``module`` warrants review — i.e. it is neither the
    stdlib nor a curated driver-safe third-party library. Known-unsupported libs
    also return True here; their dedicated detectors then classify the severity.
    Kept in one place so the block extractor and the fail-safe detector agree on
    what counts as an "interesting" import."""
    top = module.split(".")[0]
    if not top or top in {"__future__", "builtins"}:
        return False
    if top in _STDLIB_MODULES:
        return False
    if top in SAFE_THIRD_PARTY_IMPORTS:
        return False
    return True

# SNOW-3319134: ML pipeline patterns (pyspark.ml imports and classes)
ML_PIPELINE_PATTERNS = {
    "LogisticRegression": {
        "risk": 1.0,
        "reason": "pyspark.ml.classification.LogisticRegression is not supported in SCOS. Use snowflake.ml.modeling.linear_model.LogisticRegression.",
        "category": "ML Pipeline",
        "how_to_fix": "Replace with snowflake.ml.modeling.linear_model.LogisticRegression. Rename params: maxIter→max_iter, regParam→C (C=1/regParam), featuresCol→input_cols (list), labelCol→label_cols (list). Add output_cols.",
    },
    "RandomForestClassifier": {
        "risk": 1.0,
        "reason": "pyspark.ml.classification.RandomForestClassifier is not supported in SCOS. Use snowflake.ml.modeling.ensemble.RandomForestClassifier.",
        "category": "ML Pipeline",
        "how_to_fix": "Replace with snowflake.ml.modeling.ensemble.RandomForestClassifier. Rename: numTrees→n_estimators, maxDepth→max_depth, featuresCol→input_cols, labelCol→label_cols.",
    },
    "GBTClassifier": {
        "risk": 1.0,
        "reason": "pyspark.ml.classification.GBTClassifier is not supported in SCOS. Use snowflake.ml.modeling.ensemble.GradientBoostingClassifier.",
        "category": "ML Pipeline",
        "how_to_fix": "Replace with snowflake.ml.modeling.ensemble.GradientBoostingClassifier. Rename: maxIter→n_estimators, maxDepth→max_depth.",
    },
    "RandomForestRegressor": {
        "risk": 1.0,
        "reason": "pyspark.ml.regression.RandomForestRegressor is not supported in SCOS. Use snowflake.ml.modeling.ensemble.RandomForestRegressor.",
        "category": "ML Pipeline",
        "how_to_fix": "Replace with snowflake.ml.modeling.ensemble.RandomForestRegressor. Rename: numTrees→n_estimators, maxDepth→max_depth.",
    },
    "LinearRegression": {
        "risk": 1.0,
        "reason": "pyspark.ml.regression.LinearRegression is not supported in SCOS. Use snowflake.ml.modeling.linear_model.LinearRegression.",
        "category": "ML Pipeline",
        "how_to_fix": "Replace with snowflake.ml.modeling.linear_model.LinearRegression. Rename: maxIter→max_iter, regParam→alpha, featuresCol→input_cols, labelCol→label_cols.",
    },
    "Pipeline": {
        "risk": 1.0,
        "reason": "pyspark.ml.Pipeline is not supported in SCOS. Use snowflake.ml.modeling.pipeline.Pipeline or sequential fit/predict calls.",
        "category": "ML Pipeline",
        "how_to_fix": "Replace with snowflake.ml.modeling.pipeline.Pipeline or call fit/predict on each stage sequentially.",
    },
    "CrossValidator": {
        "risk": 1.0,
        "reason": "pyspark.ml.tuning.CrossValidator is not supported in SCOS. Use snowflake.ml.modeling.model_selection.GridSearchCV.",
        "category": "ML Pipeline",
        "how_to_fix": "Replace with snowflake.ml.modeling.model_selection.GridSearchCV. Rename: estimator→estimator, numFolds→cv, estimatorParamMaps→param_grid.",
    },
    "VectorAssembler": {
        "risk": 1.0,
        "reason": "pyspark.ml.feature.VectorAssembler is not needed in Snowflake ML. Snowflake ML accepts multiple input columns directly.",
        "category": "ML Pipeline",
        "how_to_fix": "Remove VectorAssembler. Pass the original feature columns directly to the estimator via input_cols=[col1, col2, ...].",
    },
}

# SNOW-3319139: UDTF/UDAF patterns
UDTF_UDAF_PATTERNS = {
    "@udtf": {
        "risk": 0.15,
        "reason": (
            "PySpark @udtf is natively supported in SCOS. When compatibility "
            "mode is enabled, the SCOS runtime auto-translates the Spark-style "
            "eval() method to the Snowpark UDTF handler contract, so no "
            "structural rewrite is needed."
        ),
        "category": "UDTF/UDAF",
        "how_to_fix": (
            "Enable compatibility mode once per session: "
            "spark.conf.set('snowpark.connect.udtf.compatibility_mode', 'true'). "
            "Keep the @udtf class and eval() method as written; register via "
            "spark.udtf.register(name, Class). For vectorized execution also set "
            "spark.sql.execution.pythonUDTF.arrow.enabled=true."
        ),
    },
    "PandasUDFType.GROUPED_AGG": {
        "risk": 0.8,
        "reason": "PandasUDFType.GROUPED_AGG needs conversion to Snowpark vectorized UDAF with accumulate/merge/finish pattern.",
        "category": "UDTF/UDAF",
        "how_to_fix": "Convert to Snowpark UDAF: create handler class with accumulate(), merge(), finish() methods. Register with session.udaf.register().",
    },
    "PandasUDFType.SCALAR": {
        "risk": 0.5,
        "reason": "PandasUDFType.SCALAR can be simplified to @udf with pandas Series type hints in Spark Connect.",
        "category": "UDTF/UDAF",
        "how_to_fix": "Replace @pandas_udf(returnType, PandasUDFType.SCALAR) with @udf and add pandas Series type hints to the function signature.",
    },
}

# SNOW-3319141: Delta Lake patterns
DELTA_LAKE_PATTERNS = {
    "DeltaTable.forPath": {
        "risk": 1.0,
        "reason": "DeltaTable API is not available in SCOS. Use session.table() or spark.table() for reading Snowflake/Iceberg tables.",
        "category": "Delta Lake",
        "how_to_fix": "Replace DeltaTable.forPath(spark, path) with spark.table(table_name). Ensure the table exists as an Iceberg table in Snowflake.",
    },
    "DeltaTable.forName": {
        "risk": 1.0,
        "reason": "DeltaTable API is not available in SCOS. Use session.table() or spark.table().",
        "category": "Delta Lake",
        "how_to_fix": "Replace DeltaTable.forName(spark, name) with spark.table(name).",
    },
    "delta.tables": {
        "risk": 1.0,
        "reason": "delta.tables import is not available in SCOS. Use Snowflake native table operations.",
        "category": "Delta Lake",
        "how_to_fix": "Remove delta.tables import. Use spark.table() for reads and df.write.saveAsTable() for writes.",
    },
}

# SNOW-3319141: Delta SQL patterns (OPTIMIZE, VACUUM, MERGE INTO on delta paths)
DELTA_SQL_KEYWORDS = ["OPTIMIZE", "VACUUM", "ZORDER"]

# RDD patterns - these indicate unsupported RDD usage
RDD_PATTERNS = [
    # SparkContext access
    ".sparkContext",
    ".rdd",
    # RDD imports
    "from pyspark import RDD",
    "from pyspark.rdd import",
    # SparkContext-specific methods - these methods only exist on SparkContext, so any .methodName( is RDD usage
    ".parallelize(",
    ".textFile(",
    ".wholeTextFiles(",
    ".binaryFiles(",
    ".binaryRecords(",
    ".hadoopFile(",
    ".hadoopRDD(",
    ".newAPIHadoopFile(",
    ".newAPIHadoopRDD(",
    ".sequenceFile(",
    ".objectFile(",
    ".pickleFile(",
    ".emptyRDD(",
    # Accumulator constructors / base classes / factory methods. These are
    # SparkContext-bound distributed counters with a DataFrame-aggregation
    # workaround (NOT a blanket TODO) — see references/python/rdd-conversion.md
    # §12; only the §13 uses (cache-hit / mid-job / foreachPartition are ⚠perm;
    # foreachBatch is ⚠ns-with-workaround) are hard gaps. ``sc.accumulator(`` is
    # additionally surfaced via SPARK_CONTEXT_PROPERTIES. The modern factory
    # forms ``sc.longAccumulator()`` / ``sc.doubleAccumulator()`` and the
    # PascalCase classes ``LongAccumulator`` / ``DoubleAccumulator`` map the same
    # way (§12.1 / §12.2) — the call forms + Spark-specific class names avoid the
    # false positives a bare ``accumulator`` substring would cause.
    ".collectionAccumulator(",
    ".longAccumulator(",
    ".doubleAccumulator(",
    "AccumulatorParam",
    "AccumulatorV2",
    "LongAccumulator",
    "DoubleAccumulator",
]

# RDD methods - operations on RDD objects
RDD_METHODS = {
    "map",
    "flatMap",
    "filter",
    "reduce",
    "reduceByKey",
    "reduceByKeyLocally",
    "groupByKey",
    "sortByKey",
    "sortBy",
    "join",
    "leftOuterJoin",
    "rightOuterJoin",
    "fullOuterJoin",
    "cogroup",
    "cartesian",
    "pipe",
    "coalesce",
    "repartition",
    "foreach",
    "foreachPartition",
    "collect",
    "count",
    "first",
    "take",
    "takeSample",
    "takeOrdered",
    "saveAsTextFile",
    "saveAsSequenceFile",
    "saveAsObjectFile",
    "countByKey",
    "countByValue",
    "aggregate",
    "fold",
    "glom",
    "mapPartitions",
    "mapPartitionsWithIndex",
    "zip",
    "zipWithIndex",
    "zipWithUniqueId",
    "keyBy",
    "keys",
    "values",
    "lookup",
    "top",
    "max",
    "min",
    "sum",
    "mean",
    "variance",
    "stdev",
    "sampleStdev",
    "sampleVariance",
    "histogram",
    "countApproxDistinct",
    "countApproxDistinctByKey",
    "randomSplit",
    "union",
    "intersection",
    "subtract",
    "distinct",
    "cache",
    "persist",
    "unpersist",
    "checkpoint",
    "isCheckpointed",
    "getCheckpointFile",
    "toLocalIterator",
    "isEmpty",
    "getNumPartitions",
    "mapValues",
    "flatMapValues",
    "groupWith",
    "combineByKey",
    "aggregateByKey",
    "foldByKey",
    "sampleByKey",
    # Aggregate & §10 "additional verified RDD operations" — all RDD-exclusive
    # (no DataFrame homonym). Each has a DataFrame workaround documented in
    # references/python/rdd-conversion.md (§14 / §16). Kept in sync with
    # RDD_EXCLUSIVE_METHODS below and the annotate recipe's _TARGET_METHODS.
    "treeAggregate",
    "treeReduce",
    "collectAsMap",
    "countApprox",
    "countApproxDistinct",
    "meanApprox",
    "sumApprox",
    "collectWithJobGroup",
    "mapPartitionsWithSplit",
    "repartitionAndSortWithinPartitions",
    "saveAsPickleFile",
    "getStorageLevel",
    "toDebugString",
}

# RDD-EXCLUSIVE methods: the subset of RDD_METHODS whose names have NO DataFrame
# (or dict / builtin / pandas) homonym, so seeing ``.<name>(`` anywhere is
# unambiguously RDD. These are flagged WITHOUT the ``.rdd`` / ``sc.`` context
# gate in ``has_rdd_usage`` — that gate exists to avoid false positives on
# ambiguous names (``.map``/``.filter``/``.count``/``.collect`` exist on
# DataFrame too), but it also suppresses real RDD usage when an RDD flows
# through a variable / function parameter with no co-located ``.rdd`` token
# (e.g. ``out = rdd.reduceByKey(add)``). Ungating the exclusive names closes
# that dataflow gap without reintroducing false positives.
#
# Deliberately NOT included (need the gate / handled elsewhere):
#   - ambiguous: map, filter, collect, count, first, take, distinct, union,
#     join, intersection, subtract, cache, persist, unpersist, repartition,
#     coalesce, sort, reduce, fold, aggregate, sum, max, min, mean, keys,
#     values, sample, pipe, foreach, foreachPartition, lookup, top, histogram,
#     variance, stdev, randomSplit, zip
#   - §10 ambiguous DataFrame homonyms — groupBy (DataFrame.groupBy), toDF
#     (usually means a `.rdd` hop that shouldn't exist), partitionBy
#     (DataFrameWriter.partitionBy), context — stay GATED. groupBy/toDF/context
#     route to the existing `.rdd`-hop / DataFrame handling, not a broad token;
#     partitionBy is a writer method the detector does not flag today.
#   - cogroup / groupWith: the pandas cogrouped-ops API uses ``.cogroup`` on a
#     grouped DataFrame, so the name is not RDD-exclusive
#   - glom, getNumPartitions, isCheckpointed, getCheckpointFile,
#     saveAsSequenceFile: no-equivalent ops already flagged via RDD_PATTERNS /
#     handled by the rdd_no_equivalent recipe. (saveAsObjectFile is NOT here: it
#     moved to the exclusive set below — like its sibling saveAsPickleFile it has
#     a [Partial] DataFrame parquet/table round-trip, §16.11; only the reader
#     entry point ``sc.objectFile`` stays no-equivalent.)
RDD_EXCLUSIVE_METHODS = {
    "reduceByKey",
    "reduceByKeyLocally",
    "groupByKey",
    "aggregateByKey",
    "foldByKey",
    "combineByKey",
    "sampleByKey",
    "countByKey",
    "countByValue",
    "mapValues",
    "flatMapValues",
    "keyBy",
    "zipWithIndex",
    "zipWithUniqueId",
    "sortByKey",
    "mapPartitions",
    "mapPartitionsWithIndex",
    "takeOrdered",
    "takeSample",
    "saveAsTextFile",
    # [Partial] DataFrame-shaped parquet/table round-trip (§16.11) — same verdict
    # as its sibling saveAsPickleFile, so it is convertible (not no-equivalent).
    "saveAsObjectFile",
    # Aggregate recipes (§14) + §10 additional verified ops (§16): all
    # RDD-exclusive names (no DataFrame homonym), so flagged WITHOUT the gate.
    # Ambiguous §10 homonyms (groupBy, toDF, partitionBy, context) are
    # deliberately EXCLUDED — see the note below — and reduce/fold/aggregate
    # stay gated in RDD_METHODS.
    "treeAggregate",
    "treeReduce",
    "collectAsMap",
    "countApprox",
    "countApproxDistinct",
    "meanApprox",
    "sumApprox",
    "collectWithJobGroup",
    "mapPartitionsWithSplit",
    "repartitionAndSortWithinPartitions",
    "saveAsPickleFile",
    "getStorageLevel",
    "toDebugString",
}

# =============================================================================
# RDD -> DataFrame CONVERSION GUIDANCE (actionable RDD fix payloads)
# =============================================================================
# The RDD detector short-circuits the LLM and emits a deterministic issue, so
# this issue is the ONLY guidance the fixer gets. A single generic "not
# supported" string leaves it nothing to act on and it punts convertible ops to
# a `# SCOS: TODO`. Instead the detector classifies each block and points the
# fixer at the authoritative rewrite mapping:
#
#   * references/python/rdd-conversion.md is the SINGLE SOURCE OF TRUTH for the
#     per-op DataFrame rewrites (kept in sync by test_rdd_reference_sync.py).
#     The fix payload NAMES the detected op(s) and cites that doc rather than
#     duplicating the ~70 rewrite strings here (which would silently drift).
#   * RDD_NO_EQUIVALENT is the ONLY thing we must classify in code: the handful
#     of ops with genuinely no DataFrame equivalent. Everything else is
#     convertible and MUST be rewritten (never TODO'd) using the reference.
#
# Tokens are lowercase op/entry-point names (matched as ``.<token>(``).
RDD_NO_EQUIVALENT = {
    # RDD-instance ops with no DataFrame equivalent
    "pipe",
    "glom",
    "saveassequencefile",
    # NOTE: saveAsObjectFile is deliberately NOT here — it is a [Partial]
    # convertible parquet/table round-trip (§16.11), mirroring saveAsPickleFile,
    # and lives in RDD_EXCLUSIVE_METHODS. Only the reader ``sc.objectFile``
    # entry point below has no equivalent.
    "ischeckpointed",
    "getcheckpointfile",
    "getnumpartitions",
    # SparkContext entry points with no equivalent (Hadoop / Java-serialized IO)
    "sequencefile",
    "objectfile",
    "picklefile",
    "hadoopfile",
    "hadooprdd",
    "newapihadoopfile",
    "newapihadooprdd",
}

# UDF serialization patterns - these indicate potential cloudpickle serialization issues
# when running on Snowflake's server-side Python worker
UDF_SERIALIZATION_PATTERNS = [
    ".applyInPandas(",
    ".mapInPandas(",
    "@udf(",
    "@udf\n",
    "@pandas_udf(",
    "@pandas_udf\n",
    "udf(",
]

# Checkpoint patterns — not supported; replace with cache().
#
# Note: this is a Spark Connect client-side limitation, not a SCOS
# server-side restriction. The PySpark Connect client raises
# PySparkNotImplementedError for checkpoint() / localCheckpoint() before
# any request reaches the server.
CHECKPOINT_PATTERNS = [
    ".checkpoint(",
    ".checkpoint()",
    ".localCheckpoint(",
    ".localCheckpoint()",
]

# Map column subscript with Column key - not supported in Spark Connect
# map_col[col("key")] fails; use element_at(map_col, col("key")) instead
MAP_SUBSCRIPT_PATTERN = r'\]\s*\[\s*col\s*\('

# =============================================================================
# UNSUPPORTED SPARK APIs (from Snowflake documentation)
# https://docs.snowflake.com/en/developer-guide/snowpark-connect/snowpark-connect-compatibility
# =============================================================================

# Modules/imports that indicate unsupported features (risk on 0-1 scale)
UNSUPPORTED_IMPORTS = {
    "pyspark.ml": {
        "risk": 1.0,
        "reason": "pyspark.ml (MLlib) is not supported in SCOS",
        "category": "Unsupported Module",
        "how_to_fix": "Use Snowflake ML or Snowpark ML instead",
    },
    "pyspark.streaming": {
        "risk": 1.0,
        "reason": "pyspark.streaming is not supported in SCOS",
        "category": "Unsupported Module",
        "how_to_fix": "Use Snowflake Streams and Tasks for streaming workloads",
    },
    "pyspark.mllib": {
        "risk": 1.0,
        "reason": "pyspark.mllib is not supported in SCOS",
        "category": "Unsupported Module",
        "how_to_fix": "Use Snowflake ML or Snowpark ML instead",
    },
}

# =============================================================================
# SNOWFLAKE CONNECTOR PUSHDOWN (recommended improvement, not a required fix)
# =============================================================================

SNOWFLAKE_CONNECTOR_PATTERN = {
    "risk": 0.2,
    "reason": (
        "Snowflake Connector for Spark (.format('snowflake')) is supported in SCOS but "
        "SnowflakeSession.sql() provides a better experience -- simpler code, no connector "
        "config boilerplate, and direct use of the Snowpark Connect session."
    ),
    "category": "Recommended Improvement",
    "how_to_fix": (
        "Consider replacing the .read.format('snowflake')...load() chain with "
        "SnowflakeSession.sql() for a cleaner integration. "
        "See the Snowflake Connector Pushdown rule for the complete pattern."
    ),
}

# =============================================================================
# DATA SOURCE LIMITATIONS
# =============================================================================

# File formats that are completely unsupported (risk on 0-1 scale)
UNSUPPORTED_FORMATS = {
    "avro": {
        "risk": 1.0,
        "reason": "Avro format is not supported in SCOS",
        "category": "Unsupported Format",
        "how_to_fix": "Convert data to Parquet, CSV, or JSON format",
    },
    "orc": {
        "risk": 1.0,
        "reason": "ORC format is not supported in SCOS",
        "category": "Unsupported Format",
        "how_to_fix": "Convert data to Parquet, CSV, or JSON format",
    },
    "delta": {
        "risk": 1.0,
        "reason": "Delta format is not supported in SCOS",
        "category": "Unsupported Format",
        "how_to_fix": "Convert data to Parquet, CSV, or JSON format",
    },
    "binaryFile": {
        "risk": 1.0,
        "reason": "Binary format is not supported in SCOS",
        "category": "Unsupported Format",
        "how_to_fix": "Convert data to Parquet, CSV, or JSON format",
    },
}

# File formats with partial support and their limitations
FORMAT_LIMITATIONS = {
    "csv": {
        "unsupported_modes": ["ignore"],
        "unsupported_options": [
            "quote",
            "quoteAll",
            "escapeQuotes",
            "comment",
            "preferDate",
            "enforceSchema",
            "ignoreLeadingWhiteSpace",
            "ignoreTrailingWhiteSpace",
            "nanValue",
            "positiveInf",
            "negativeInf",
            "timestampNTZFormat",
            "enableDateTimeParsingFallback",
            "maxColumns",
            "maxCharsPerColumn",
            "mode",
            "columnNameOfCorruptRecord",
            "charToEscapeQuoteEscaping",
            "samplingRatio",
            "emptyValue",
            "locale",
            "lineSep",
            "unescapedQuoteHandling",
        ],
    },
    "json": {
        "unsupported_modes": ["ignore"],
        "unsupported_options": [
            "timeZone",
            "primitiveSCOSString",
            "prefersDecimal",
            "allowComments",
            "allowUnquotedFieldNames",
            "allowSingleQuotes",
            "allowNumericLeadingZeros",
            "allowBackslashEscapingAnyCharacter",
            "mode",
            "columnNameOfCorruptRecord",
            "timestampNTZFormat",
            "enableDateTimeParsingFallback",
            "allowUnquotedControlChars",
            "encoding",
            "lineSep",
            "samplingRatio",
            "dropFieldIfAllNull",
            "locale",
            "allowNonNumericNumbers",
            "compression",
            "ignoreNullFields",
        ],
    },
    "parquet": {
        "unsupported_modes": ["ignore"],
        "unsupported_options": [
            "datetimeRebaseMode",
            "int96RebaseMode",
            "mergeSchema",
        ],
    },
    "text": {
        "unsupported_modes": ["ignore"],
        "unsupported_options": [],
    },
    "xml": {
        "unsupported_modes": ["ignore"],
        "unsupported_options": [
            "arrayElementName",
            "dateFormat",
            "declaration",
            "inferSchema",
            "locale",
            "modifiedBefore",
            "recursiveFileLookup",
            "rootTag",
            "samplingRatio",
            "timeZone",
            "timestampFormat",
            "timestampNTZFormat",
            "validateName",
            "wildcardColName",
        ],
    },
}

# Unsupported data types (risk on 0-1 scale)
UNSUPPORTED_DATATYPES = {}

# =============================================================================
# SUPPORTED SPARK CONFIGS IN SCOS
# Configs NOT in this set are no-ops (silently ignored by SCOS)
# Based on src/snowflake/snowpark_connect/config.py
# =============================================================================

# Configs that have actual effects in SCOS (Snowflake session, Snowpark behavior, etc.)
SUPPORTED_CONFIGS = {
    # Configs with Snowflake session effects (set_snowflake_parameters)
    "spark.sql.session.timeZone",
    "spark.sql.globalTempDatabase",
    "spark.sql.parquet.outputTimestampType",
    # Configs with Snowpark session effects (snowpark_config_mapping)
    "spark.app.name",
    "snowpark.connect.udf.imports",
    "snowpark.connect.udf.python.imports",
    "snowpark.connect.udf.java.imports",
    # Configs read by SCOS logic (default_global_config)
    "spark.driver.host",
    "spark.sql.pyspark.inferNestedDictAsStruct.enabled",
    "spark.sql.pyspark.legacy.inferArrayTypeFromFirstElement.enabled",
    "spark.sql.repl.eagerEval.enabled",
    "spark.sql.repl.eagerEval.maxNumRows",
    "spark.sql.repl.eagerEval.truncate",
    "spark.sql.session.localRelationCacheThreshold",
    "spark.sql.timestampType",
    "spark.sql.crossJoin.enabled",
    "spark.sql.caseSensitive",
    "spark.sql.mapKeyDedupPolicy",
    "spark.sql.ansi.enabled",
    "spark.sql.legacy.allowHashOnMapType",
    "spark.sql.sources.default",
    "spark.Catalog.databaseFilterInformationSchema",
    "spark.sql.parser.quotedRegexColumnNames",
    "spark.sql.execution.arrow.maxRecordsPerBatch",
    "spark.sql.legacy.dataset.nameNonStructGroupingKeyAsValue",
    # Session config whitelist (AWS/Azure credentials)
    "spark.hadoop.fs.s3a.access.key",
    "spark.hadoop.fs.s3a.secret.key",
    "spark.hadoop.fs.s3a.session.token",
    "spark.hadoop.fs.s3a.server-side-encryption.key",
    "spark.hadoop.fs.s3a.assumed.role.arn",
    "spark.sql.execution.pythonUDTF.arrow.enabled",
    "spark.sql.tvf.allowMultipleTableArguments.enabled",
    "spark.sql.parquet.enable.summary-metadata",
    "spark.jars",
    "mapreduce.fileoutputcommitter.marksuccessfuljobs",
    "parquet.enable.summary-metadata",
    # Snowpark Connect specific configs (these have effects in SCOS)
    # Note: All snowpark.connect.* configs are also matched by prefix, listed here for documentation
    "snowpark.connect.sql.passthrough",  # Enables SQL passthrough mode
    "snowpark.connect.cte.optimization_enabled",  # Enables CTE optimization
    "snowpark.connect.iceberg.external_volume",  # Iceberg external volume
    "snowpark.connect.sql.identifiers.auto-uppercase",  # Identifier case handling
    "snowpark.connect.sql.partition.external_table_location",  # External table location
    "snowpark.connect.udtf.compatibility_mode",  # UDTF compatibility
    "snowpark.connect.views.duplicate_column_names_handling_mode",  # View column handling
    "snowpark.connect.temporary.views.create_in_snowflake",  # Temp view creation
    "snowpark.connect.enable_snowflake_extension_behavior",  # Snowflake extensions
    "snowpark.connect.describe_cache_ttl_seconds",  # Describe cache TTL
    "snowpark.connect.structured_types.fix",  # Structured types fix
    "snowpark.connect.scala.version",  # Scala version for Java UDFs (config exists in SCOS)
    "snowpark.connect.integralTypesEmulation",  # Integral types emulation
    "snowpark.connect.localRelation.optimizeSmallData",  # Local relation optimization
    "snowpark.connect.parquet.useVectorizedScanner",  # Parquet vectorized scanner
    "snowpark.connect.parquet.useLogicalType",  # Parquet logical types
    "snowpark.connect.handleIntegralOverflow",  # Integral overflow handling
    "snowpark.connect.version",  # SCOS version (read-only)
    # Snowflake specific configs
    "snowflake.repartition.for.writes",  # Repartition for writes
}


def is_supported_config(config_key: str) -> bool:
    """Check if a Spark config key is supported by SCOS."""
    # Check exact match
    if config_key in SUPPORTED_CONFIGS:
        return True
    return False


def check_config_no_ops(code: str) -> list[dict]:
    """
    Check for Spark config settings that are no-ops in SCOS.

    Detects patterns like:
    - spark.conf.set("key", "value")
    - .config("key", "value") in builder chains
    - SparkConf().set("key", "value")

    Returns:
        List of issues found with no-op configs
    """
    issues = []

    # Pattern 1: spark.conf.set("key", "value") or spark.conf.set('key', 'value')
    conf_set_pattern = r'\.conf\.set\s*\(\s*["\']([^"\']+)["\']\s*,'

    # Pattern 2: .config("key", "value") in builder chains
    config_pattern = r'\.config\s*\(\s*["\']([^"\']+)["\']\s*,'

    # Pattern 3: SparkConf().set("key", "value")
    sparkconf_set_pattern = r'SparkConf\s*\(\s*\).*\.set\s*\(\s*["\']([^"\']+)["\']\s*,'

    all_patterns = [
        (conf_set_pattern, "spark.conf.set()"),
        (config_pattern, ".config()"),
        (sparkconf_set_pattern, "SparkConf().set()"),
    ]

    found_configs = set()  # Track found configs to avoid duplicates

    for pattern, pattern_name in all_patterns:
        for match in re.finditer(pattern, code):
            config_key = match.group(1)

            # Skip if already reported
            if config_key in found_configs:
                continue

            # Check if this config is supported
            if not is_supported_config(config_key):
                found_configs.add(config_key)
                issues.append(
                    {
                        "api": config_key,
                        "risk": 0.2,  # Low risk - config is just ignored
                        "reason": f"Spark config '{config_key}' is a no-op in SCOS - this setting has no effect",
                        "category": "No-Op Config",
                        "how_to_fix": f"No action needed — config '{config_key}' is silently ignored in SCOS and does not cause errors",
                        "pattern": pattern_name,
                    }
                )

    return issues


@dataclass
class APIInfo:
    """API compatibility information."""

    name: str
    api_type: str
    compatibility: str
    is_supported: bool
    score: float | None  # 0-1 scale

    @classmethod
    def from_csv_row(cls, row: dict) -> "APIInfo":
        compat = row.get("COMPATIBILITY", "UNKNOWN").strip().upper()
        # Normalize compatibility values
        if compat.startswith("SHEET_"):
            compat = compat.replace("SHEET_", "")
        if compat not in COMPAT_SCORES:
            compat = "UNKNOWN"

        return cls(
            name=row.get("API", ""),
            api_type=row.get("TYPE", ""),
            compatibility=compat,
            is_supported=row.get("IS_SUPPORTED", "").lower() == "true",
            score=COMPAT_SCORES.get(compat),
        )


def load_api_compatibility(csv_path: Path) -> tuple[dict[str, APIInfo], set[str]]:
    """
    Load API compatibility data from CSV.

    Returns:
        - api_map: dict mapping API names to APIInfo
        - all_methods: set of all method/function names for detection
    """
    api_map = {}
    all_methods = set()

    if not csv_path.exists():
        logger.warning(f"Warning: API compatibility CSV not found at {csv_path}")
        return api_map, all_methods

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            info = APIInfo.from_csv_row(row)
            if info.name:
                # Store by full path
                api_map[info.name] = info

                # Also store by short name (last part) for easier lookup
                # Prefer BETTER compatibility when there are conflicts
                short_name = info.name.split(".")[-1]
                if short_name not in api_map:
                    api_map[short_name] = info
                elif info.score is not None:
                    existing = api_map[short_name]
                    # Prefer higher compatibility score (D0=100 > D1=80 > D2=50 > NONE=0)
                    if existing.score is None or info.score > existing.score:
                        api_map[short_name] = info

                # Add to methods set (for function/method types)
                if info.api_type in ("function", "method"):
                    all_methods.add(short_name)

    return api_map, all_methods


def has_rdd_usage(code: str) -> tuple[bool, str | None]:
    """
    Check if code contains RDD patterns.

    Returns:
        - (True, reason) if RDD usage detected
        - (False, None) otherwise
    """
    code_lower = code.lower()

    # Check for RDD access patterns
    for pattern in RDD_PATTERNS:
        if pattern.lower() in code_lower:
            return True, f"Uses '{pattern}' which is not supported in SCOS"

    # Check for RDD type annotations (e.g., -> RDD, : RDD)
    if re.search(r":\s*RDD\b|->.*\bRDD\b", code):
        return True, "Uses RDD type annotation which indicates RDD usage"

    # RDD-EXCLUSIVE methods have no DataFrame/builtin homonym, so any call is
    # unambiguously RDD — flag WITHOUT requiring a co-located .rdd/sc. token.
    # This catches RDD chains on a bound variable / function parameter
    # (e.g. ``out = rdd.reduceByKey(add)``) that the gated check below misses
    # because there is no dataflow/type tracking across statements.
    for method in RDD_EXCLUSIVE_METHODS:
        if f".{method.lower()}(" in code_lower:
            return (
                True,
                f"RDD-exclusive operation '.{method}()' is not supported in SCOS",
            )

    # Check if it looks like RDD method chain (e.g., .map(...).filter(...))
    # Only flag the AMBIGUOUS method names (which also exist on DataFrame) when
    # we additionally see an RDD-specific token, to avoid false positives.
    if (
        ".rdd" in code_lower
        or "sparkcontext" in code_lower
        or re.search(r"\bsc\.", code_lower)
    ):
        for method in RDD_METHODS:
            if f".{method.lower()}(" in code_lower:
                return True, f"RDD operation '.{method}()' is not supported in SCOS"

    return False, None


# Entry-point tokens (SparkContext creators) worth surfacing in guidance, in
# addition to the RDD-instance method names. Kept lowercase for matching.
# Display names (camelCase) for every RDD op/entry-point we detect, keyed by
# the lowercase match token. Methods come from RDD_METHODS; SparkContext
# entry-points are derived from RDD_PATTERNS (``.textFile(`` -> ``textFile``) so
# there is no separate hand-maintained list to drift.
_RDD_DISPLAY = {m.lower(): m for m in RDD_METHODS}
for _p in RDD_PATTERNS:
    _p = _p.strip()
    if _p.startswith(".") and _p.endswith("("):
        _name = _p[1:-1]
        _RDD_DISPLAY.setdefault(_name.lower(), _name)


def build_rdd_conversion_guidance(code: str) -> dict:
    """Build an actionable RDD->DataFrame fix payload.

    The RDD detector short-circuits the LLM, so this deterministic issue is the
    ONLY guidance the fixer gets. Rather than duplicate the rewrite mapping, we
    NAME the detected op(s) and point the fixer at the authoritative reference
    (references/python/rdd-conversion.md); convertible ops MUST be rewritten
    (never TODO'd), only the ``RDD_NO_EQUIVALENT`` handful becomes a TODO.

    Returns a dict with keys:
      * ``fix``                    -- agent-facing fix directive
      * ``explanation``            -- why / how, framed by convertibility
      * ``suggested_fixer_action`` -- rewrite directive, or ``None`` for
                                      no-equivalent ops (=> TODO)
      * ``rdd_class``              -- "convertible" | "no_equivalent" | "mixed"
      * ``matched_ops``            -- sorted list of detected tokens
    """
    code_lower = code.lower()

    convertible: list[str] = []  # display names
    no_equiv: list[str] = []  # display names
    matched: list[str] = []
    for token in sorted(_RDD_DISPLAY):
        if f".{token}(" not in code_lower:
            continue
        matched.append(token)
        (no_equiv if token in RDD_NO_EQUIVALENT else convertible).append(
            _RDD_DISPLAY[token]
        )

    ref = "references/python/rdd-conversion.md"

    def _names(items: list[str]) -> str:
        shown = items[:8]
        more = len(items) - len(shown)
        return ", ".join(shown) + (f", (+{more} more)" if more > 0 else "")

    conv_text = _names(convertible)
    noeq_text = _names(no_equiv)

    if convertible and no_equiv:
        return {
            "fix": (
                f"Rewrite the convertible RDD op(s) — {conv_text} — using the "
                f"DataFrame API per {ref} (apply the matching row/worked example "
                f"for each). APPLY the rewrite; do NOT leave a `# SCOS: TODO` for "
                f"a convertible op. Only the no-equivalent op(s) — {noeq_text} — "
                f"get a `# SCOS: TODO`."
            ),
            "explanation": (
                "Mixed RDD block: rewrite the convertible ops using the DataFrame "
                "API (do not defer them); only the no-equivalent op(s) need a TODO."
            ),
            "suggested_fixer_action": (
                f"Rewrite {conv_text} via the DataFrame API (see {ref}); "
                f"TODO only {noeq_text}."
            ),
            "rdd_class": "mixed",
            "matched_ops": matched,
        }
    if convertible:
        return {
            "fix": (
                f"Rewrite the RDD operation(s) — {conv_text} — using the DataFrame "
                f"API. Look up the exact equivalent for each in {ref} (matching "
                f"row / worked example) and route on that row's verdict tag. APPLY "
                f"the rewrite; do NOT defer the whole op to a `# SCOS: TODO`. For a "
                f"[Partial] op (e.g. saveAsObjectFile/saveAsPickleFile, "
                f"getStorageLevel), apply the closest form AND add a `# SCOS: TODO` "
                f"for the aspect SCOS cannot reproduce."
            ),
            "explanation": (
                "Convertible RDD operations — rewrite them using the DataFrame API "
                "per the reference, routing on each op's verdict tag. A convertible "
                "op must not be deferred wholesale to a TODO; a [Partial] op applies "
                "the closest form and keeps a TODO only for the aspect SCOS cannot "
                "reproduce."
            ),
            "suggested_fixer_action": (
                f"Rewrite {conv_text} using the DataFrame API; see {ref}."
            ),
            "rdd_class": "convertible",
            "matched_ops": matched,
        }
    if no_equiv:
        return {
            "fix": (
                f"No DataFrame/SCOS equivalent for: {noeq_text}. Add a `# SCOS: "
                f"TODO` naming the op, why it is unsupported, and the "
                f"Snowflake-native alternative. See {ref}."
            ),
            "explanation": (
                "These RDD operations have no Snowpark Connect equivalent and "
                "require manual migration."
            ),
            "suggested_fixer_action": None,
            "rdd_class": "no_equivalent",
            "matched_ops": matched,
        }

    # Detected via `.rdd` attribute / RDD import / type annotation with no
    # specific op token — the `.rdd` hop itself is removable.
    return {
        "fix": (
            "Remove the `.rdd` hop and operate on the DataFrame directly; rewrite "
            f"any RDD operations with the DataFrame API per {ref}. APPLY the "
            "rewrite; do NOT leave a `# SCOS: TODO`."
        ),
        "explanation": (
            "RDD access detected. Drop back into the DataFrame API — this is "
            "convertible; rewrite it rather than deferring to a TODO."
        ),
        "suggested_fixer_action": (
            "Remove the `.rdd` hop and operate on the DataFrame directly."
        ),
        "rdd_class": "convertible",
        "matched_ops": matched,
    }


def check_unsupported_apis(code: str) -> list[dict]:
    """
    Check for unsupported Spark APIs in code.

    Returns:
        List of issues found, each with risk, reason, category, how_to_fix
    """
    issues = []

    # Check for unsupported imports
    for module, info in UNSUPPORTED_IMPORTS.items():
        # Check for import statements
        if f"import {module}" in code or f"from {module}" in code:
            issues.append(
                {
                    "api": module,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                    "how_to_fix": info.get("how_to_fix"),
                }
            )

    # Check for unsupported data types in schema definitions
    for dtype, info in UNSUPPORTED_DATATYPES.items():
        if dtype in code:
            issues.append(
                {
                    "api": dtype,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                    "how_to_fix": info.get("how_to_fix"),
                }
            )

    # Check for checkpoint usage
    for pattern in CHECKPOINT_PATTERNS:
        if pattern in code:
            issues.append(
                {
                    "api": "checkpoint",
                    "risk": 0.9,
                    "reason": "DataFrame.checkpoint() is not supported in SCOS — replace with cache()",
                    "category": "Unsupported API",
                    "how_to_fix": "Replace .checkpoint() and .localCheckpoint() with .cache()",
                }
            )
            break  # Only report once

    # Check for UDF serialization patterns
    for pattern in UDF_SERIALIZATION_PATTERNS:
        if pattern in code:
            issues.append(
                {
                    "api": "UDF serialization",
                    "risk": 0.6,
                    "reason": "UDF may have serialization issues in SCOS — cloudpickle may fail on helper functions or module-level references",
                    "category": "UDF Serialization",
                    "how_to_fix": "Make UDF self-contained (Tier 2), use stage imports (Tier 1), or apply __module__ patching (Tier 3). See references/python/udf-dependencies.md",
                }
            )
            break  # Only report once

    # Check for map column subscript with Column key
    if re.search(MAP_SUBSCRIPT_PATTERN, code):
        issues.append(
            {
                "api": "Map column subscript",
                "risk": 0.9,
                "reason": "Map column subscript with Column key (map_col[col('key')]) is not supported in Spark Connect — use element_at() instead",
                "category": "Unsupported API",
                "how_to_fix": "Replace map_col[col('key')] with element_at(map_col, col('key'))",
            }
        )

    return issues


def check_data_source_issues(code: str) -> list[dict]:
    """
    Check for data source compatibility issues.

    Returns:
        List of issues found with format/option problems
    """
    issues = []
    code_lower = code.lower()

    # Detect Snowflake Connector pushdown pattern (supported but SnowflakeSession is better UX)
    sf_connector_patterns = ['.format("snowflake")', ".format('snowflake')"]
    for pattern in sf_connector_patterns:
        if pattern.lower() in code_lower:
            issues.append(
                {
                    "api": "Snowflake Connector pushdown",
                    "risk": SNOWFLAKE_CONNECTOR_PATTERN["risk"],
                    "reason": SNOWFLAKE_CONNECTOR_PATTERN["reason"],
                    "category": SNOWFLAKE_CONNECTOR_PATTERN["category"],
                    "how_to_fix": SNOWFLAKE_CONNECTOR_PATTERN["how_to_fix"],
                }
            )
            break

    # Check for unsupported file formats
    # Pattern: .format("avro") or .load("file.avro")
    for fmt, info in UNSUPPORTED_FORMATS.items():
        patterns = [
            f'.format("{fmt}")',
            f".format('{fmt}')",
            f".{fmt}(",  # e.g., .avro(), .orc()
            f'.load("{fmt}',
            f".load('{fmt}",
        ]
        for pattern in patterns:
            if pattern.lower() in code_lower:
                issues.append(
                    {
                        "format": fmt,
                        "risk": info["risk"],
                        "reason": info["reason"],
                        "category": info["category"],
                        "how_to_fix": info.get("how_to_fix"),
                    }
                )
                break  # Only report once per format

    # Check file extensions in paths
    for fmt in UNSUPPORTED_FORMATS:
        if f".{fmt}" in code_lower and ("load(" in code_lower or "read" in code_lower):
            info = UNSUPPORTED_FORMATS[fmt]
            # Avoid duplicate if already caught above
            if not any(i.get("format") == fmt for i in issues):
                issues.append(
                    {
                        "format": fmt,
                        "risk": info["risk"],
                        "reason": info["reason"],
                        "category": info["category"],
                        "how_to_fix": info.get("how_to_fix"),
                    }
                )

    # Check for unsupported save modes
    for fmt, limits in FORMAT_LIMITATIONS.items():
        # Only check if this format is being used
        if (
            f'.format("{fmt}")' in code_lower
            or f".format('{fmt}')" in code_lower
            or f".{fmt}(" in code_lower
        ):
            for mode in limits.get("unsupported_modes", []):
                mode_patterns = [
                    f'.mode("{mode}")',
                    f".mode('{mode}')",
                    f'.mode("{mode.lower()}")',
                    f".mode('{mode.lower()}')",
                ]
                for pattern in mode_patterns:
                    if pattern.lower() in code_lower:
                        issues.append(
                            {
                                "format": fmt,
                                "risk": 0.7,
                                "reason": f"Save mode '{mode}' is not supported for {fmt.upper()} in SCOS",
                                "category": "Unsupported Save Mode",
                                "how_to_fix": f"Use 'overwrite' or 'errorifexists' mode instead of '{mode}'",
                            }
                        )
                        break

    # Check for unsupported options
    for fmt, limits in FORMAT_LIMITATIONS.items():
        if (
            f'.format("{fmt}")' in code_lower
            or f".format('{fmt}')" in code_lower
            or f".{fmt}(" in code_lower
        ):
            for opt in limits.get("unsupported_options", []):
                opt_patterns = [
                    f'.option("{opt}"',
                    f".option('{opt}'",
                ]
                for pattern in opt_patterns:
                    if pattern.lower() in code_lower:
                        issues.append(
                            {
                                "format": fmt,
                                "risk": 0.5,
                                "reason": f"Option '{opt}' is not supported for {fmt.upper()} in SCOS",
                                "category": "Unsupported Option",
                                "how_to_fix": f"Remove or work around the '{opt}' option",
                            }
                        )
                        break

    # Check for file read operations - performance optimization
    # Reading from external files (cloud storage, local paths) may be slower than
    # reading from Snowflake internal stages. Add advisory for any file read.
    file_read_patterns = [
        (r"\.read\.csv\s*\(", "csv"),
        (r"\.read\.json\s*\(", "json"),
        (r"\.read\.parquet\s*\(", "parquet"),
        (r"\.read\.text\s*\(", "text"),
        (r"\.read\.orc\s*\(", "orc"),
        (r"\.load\s*\(", "load"),
    ]

    for pattern, read_type in file_read_patterns:
        if re.search(pattern, code, re.IGNORECASE):
            issues.append(
                {
                    "api": f"file read ({read_type})",
                    "risk": 0.2,
                    "reason": (
                        "Reading from external files (S3, Azure, GCS, local paths) may be slower than "
                        "reading from Snowflake internal stage. For better performance, "
                        "consider uploading files to a Snowflake stage first."
                    ),
                    "category": "Performance Optimization",
                    "how_to_fix": (
                        "Upload files to a Snowflake stage using session.file.put() for faster processing. "
                        "Example: session.file.put('file:///local/path/data.csv', '@MY_STAGE/data/', auto_compress=False). "
                    ),
                }
            )
            break

    return issues


def check_udf_serialization_issues(code: str) -> list[dict]:
    """
    Check for applyInPandas/mapInPandas patterns that may cause
    cloudpickle serialization issues on Snowflake's server-side worker.

    Detects:
    - applyInPandas/mapInPandas usage (potential serialization risk)
    - UDF functions that call other functions defined in the same module

    Returns:
        List of issues found with UDF serialization risks
    """
    issues = []

    for pattern in UDF_SERIALIZATION_PATTERNS:
        if pattern in code:
            api_name = pattern.strip(".(")
            issues.append(
                {
                    "api": api_name,
                    "risk": 0.5,
                    "reason": (
                        f"{api_name} UDFs are serialized with cloudpickle for server-side execution. "
                        "If the UDF calls helper functions defined in the workload module, "
                        "cloudpickle will try to import the workload module on the server, "
                        "causing ModuleNotFoundError. Also, any third-party packages imported "
                        "by the UDF must be available in Snowflake's Anaconda channel."
                    ),
                    "category": "UDF Serialization",
                    "how_to_fix": (
                        "See references/udf-dependencies.md for the tiered fix approach: "
                        "(1) Use snowpark.connect.udf.packages / snowpark.connect.udf.python.imports "
                        "for external dependencies. "
                        "(2) Keep UDF logic self-contained (inline). "
                        "(3) For complex UDFs with many helpers, use factory functions + "
                        "__module__ = '__main__' patching on the UDF and all helpers in its call chain."
                    ),
                }
            )
            break  # Only report once

    return issues


# SNOW-3256946, SNOW-3256947, SNOW-3256949, SNOW-3256948, SNOW-3256950:
# Memory anti-patterns, known issues, case sensitivity, UDF config, performance
def check_memory_and_known_issues(code: str) -> list[dict]:
    """
    Check for memory anti-patterns, known SCOS issues, case sensitivity concerns,
    UDF configuration needs, and performance anti-patterns.

    Detects:
    - .count() / .collect() / .cache() / .toPandas() on large DataFrames (SNOW-3256947)
    - saveAsTable for transient tables (SNOW-3256949)
    - QUALIFY clause in spark.sql() (SNOW-3256949)
    - Cross join anti-patterns (SNOW-3256950)
    - Case sensitivity / INSERT SELECT * patterns (SNOW-3256946)
    - UDF package dependency needs (SNOW-3256948)
    """
    issues = []

    # SNOW-3256947: Memory anti-pattern detection
    # .count() on DataFrames (can hang on large data)
    if re.search(r"\.count\s*\(\s*\)", code):
        issues.append({
            "api": ".count()",
            "risk": 0.4,
            "reason": (
                "DataFrame.count() can hang on large datasets in SCOS. "
                "Consider using SQL COUNT via SnowflakeSession for safer execution."
            ),
            "category": "Memory Anti-Pattern",
            "how_to_fix": (
                "Replace with SnowflakeSession SQL: "
                "df.createOrReplaceTempView('_tmp'); "
                "snowflake_session.sql('SELECT COUNT(*) FROM _tmp').collect()[0][0]"
            ),
        })

    # .collect() on DataFrames (OOM risk)
    if re.search(r"\.collect\s*\(\s*\)", code):
        # Only flag if not preceded by .sql(...).collect() which is a small result pattern
        if not re.search(r"\.sql\s*\([^)]+\)\s*\.collect\s*\(\s*\)", code):
            issues.append({
                "api": ".collect()",
                "risk": 0.5,
                "reason": (
                    "DataFrame.collect() transfers all data to the driver and can cause OOM "
                    "on large datasets in SCOS. Only safe for small result sets."
                ),
                "category": "Memory Anti-Pattern",
                "how_to_fix": (
                    "If the result set is small (e.g., aggregation), this is safe. "
                    "For large datasets, use SnowflakeSession.sql() with LIMIT, "
                    "or process data in Snowflake directly."
                ),
            })

    # .cache() on DataFrames (temp view lifecycle differs)
    if re.search(r"\.cache\s*\(\s*\)", code):
        issues.append({
            "api": ".cache()",
            "risk": 0.4,
            "reason": (
                "DataFrame.cache() in SCOS creates temp view references that may become "
                "invalid if the source is dropped. Unlike native Spark, cached data does "
                "not survive source view drops."
            ),
            "category": "Memory Anti-Pattern",
            "how_to_fix": (
                "Replace with checkpoint-to-temp-table via SnowflakeSession CTAS: "
                "df.createOrReplaceTempView('_tmp_src'); "
                "snowflake_session.sql('CREATE OR REPLACE TEMPORARY TABLE _cached AS "
                "SELECT * FROM _tmp_src').collect(); df_cached = spark.table('_cached')"
            ),
        })

    # .toPandas() on DataFrames (driver OOM risk)
    if re.search(r"\.toPandas\s*\(\s*\)", code):
        issues.append({
            "api": ".toPandas()",
            "risk": 0.5,
            "reason": (
                "DataFrame.toPandas() transfers all data to driver memory and can cause OOM. "
                "Consider adding .limit(N) or processing in Snowflake."
            ),
            "category": "Memory Anti-Pattern",
            "how_to_fix": (
                "Add .limit(N) before .toPandas() if only a sample is needed, "
                "or process data in Snowflake using SnowflakeSession.sql(), "
                "or export to stage with df.write.csv() and read with pandas."
            ),
        })

    # SNOW-3256949: Known issues detection
    # QUALIFY clause in spark.sql() — not supported via Spark Connect
    if re.search(r"spark\.sql\s*\(.*QUALIFY\b", code, re.IGNORECASE | re.DOTALL):
        issues.append({
            "api": "QUALIFY clause",
            "risk": 0.7,
            "reason": (
                "QUALIFY clause is Snowflake-specific and not supported in standard "
                "Spark SQL via Spark Connect. Route through SnowflakeSession.sql() instead."
            ),
            "category": "Known SCOS Issue",
            "how_to_fix": (
                "Replace spark.sql('...QUALIFY...') with "
                "snowflake_session.sql('...QUALIFY...')"
            ),
        })

    # SNOW-3256950: Performance anti-patterns
    # Cross join detection
    if re.search(r"\.crossJoin\s*\(", code):
        issues.append({
            "api": "crossJoin()",
            "risk": 0.6,
            "reason": (
                "Cross joins can cause data explosion. If followed by a filter on matching keys, "
                "rewrite as a keyed inner join for better performance."
            ),
            "category": "Performance Anti-Pattern",
            "how_to_fix": (
                "Rewrite crossJoin().filter(df1['id'] == df2['id']) as "
                "df1.join(df2, df1['id'] == df2['id'], 'inner')"
            ),
        })

    # SNOW-3256946: Case sensitivity — INSERT ... SELECT * pattern
    if re.search(
        r"""spark\.sql\s*\(\s*["']INSERT\s+INTO\s+\S+\s+SELECT\s+\*""",
        code,
        re.IGNORECASE,
    ):
        issues.append({
            "api": "INSERT INTO ... SELECT *",
            "risk": 0.5,
            "reason": (
                "INSERT INTO ... SELECT * relies on column ordering which may differ "
                "between Spark and Snowflake. Use explicit column lists to avoid mismatch."
            ),
            "category": "Case Sensitivity",
            "how_to_fix": (
                "Replace SELECT * with explicit column list: "
                "INSERT INTO tbl (col1, col2) SELECT col1, col2 FROM src"
            ),
        })

    # SNOW-3256948: UDF package configuration detection
    # Detect UDFs that import third-party packages needing snowpark.connect.udf.packages
    udf_import_patterns = [
        (r"@udf\b", "UDF detected"),
        (r"@pandas_udf\b", "Pandas UDF detected"),
        (r"\.udf\.register\s*\(", "UDF registration detected"),
    ]
    for pattern, desc in udf_import_patterns:
        if re.search(pattern, code):
            # Check if the same block imports third-party packages
            third_party_imports = re.findall(
                r"import\s+(numpy|pandas|scipy|sklearn|scikit|requests|boto3|cryptography)",
                code,
            )
            if third_party_imports:
                packages = ", ".join(sorted(set(third_party_imports)))
                issues.append({
                    "api": f"UDF with imports: {packages}",
                    "risk": 0.5,
                    "reason": (
                        f"{desc} with third-party imports ({packages}). "
                        "These packages must be configured via snowpark.connect.udf.packages "
                        "for Snowflake server-side execution."
                    ),
                    "category": "UDF Configuration",
                    "how_to_fix": (
                        f'spark.conf.set("snowpark.connect.udf.packages", "{packages}")'
                    ),
                })
            break

    return issues


# SNOW-3347695: Check for per-property SparkContext access patterns
def check_spark_context_properties(code: str) -> list[dict]:
    """
    Check for sparkContext property access patterns and return per-property issues
    with individual risk scores and replacement suggestions.
    """
    issues = []
    found_properties = set()

    for prop, info in SPARK_CONTEXT_PROPERTIES.items():
        # Match patterns like: sparkContext.master, sc.master, spark.sparkContext.master
        patterns = [
            f".sparkContext.{prop}",
            f"sparkContext.{prop}",
            f"sc.{prop}(",  # method call form
        ]
        # For non-method properties, also match without parens
        if prop not in ("parallelize", "textFile", "broadcast", "accumulator", "getConf"):
            patterns.append(f"sc.{prop}")

        for pattern in patterns:
            if pattern in code and prop not in found_properties:
                found_properties.add(prop)
                issue = {
                    "api": f"sparkContext.{prop}",
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                }
                if info.get("replacement"):
                    issue["how_to_fix"] = f"Replace with: {info['replacement']}"
                else:
                    issue["how_to_fix"] = info["reason"]
                issues.append(issue)
                break

    return issues


# Legacy SQL/Hive entry points removed in Spark Connect / SCOS.
# `sqlContext`, `SQLContext`, and `HiveContext` were deprecated in Spark 2.0;
# Spark Connect drops them entirely. Their surface lives on the SparkSession
# (`spark`), so any reference is a hard runtime failure that must be rewritten.
LEGACY_ENTRY_POINTS = {
    "sqlContext": {
        # Instance attribute access on the conventional `sqlContext` object.
        "pattern": r"\bsqlContext\s*\.",
        "risk": 0.8,
        "reason": (
            "sqlContext is not available in Spark Connect / SCOS. Its methods "
            "live on the active `spark` session: sqlContext.sql(...) -> "
            "spark.sql(...), sqlContext.read -> spark.read, sqlContext.table(...) "
            "-> spark.table(...), sqlContext.createDataFrame(...) -> "
            "spark.createDataFrame(...)."
        ),
        "category": "Spark Session Element",
        "replacement": "the same method on `spark` (e.g. spark.sql(...), spark.read)",
    },
    "SQLContext": {
        # Constructor / import form: SQLContext(sc) / from pyspark.sql import SQLContext
        "pattern": r"\bSQLContext\b",
        "risk": 0.8,
        "reason": (
            "SQLContext was deprecated in Spark 2.0 and is unavailable in Spark "
            "Connect / SCOS. Use the existing `spark` session directly."
        ),
        "category": "Spark Session Element",
        "replacement": "the existing `spark` session",
    },
    "HiveContext": {
        "pattern": r"\bHiveContext\b",
        "risk": 0.8,
        "reason": (
            "HiveContext was deprecated in Spark 2.0 and is unavailable in Spark "
            "Connect / SCOS. Use `spark`; Hive catalog access maps to Snowflake's "
            "native catalog."
        ),
        "category": "Spark Session Element",
        "replacement": "the existing `spark` session",
    },
}


def check_legacy_entry_points(code: str) -> list[dict]:
    """Detect legacy SQL/Hive entry points (``sqlContext`` / ``SQLContext`` /
    ``HiveContext``) removed in Spark Connect.

    Each is unavailable under SCOS; the methods live on the SparkSession. One
    issue per distinct entry point seen in the block.
    """
    issues = []
    for name, info in LEGACY_ENTRY_POINTS.items():
        if re.search(info["pattern"], code):
            issues.append(
                {
                    "api": name,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                    "how_to_fix": f"Replace with: {info['replacement']}",
                }
            )
    return issues


# SNOW-3347699: Check for Hadoop filesystem access patterns
def check_hadoop_patterns(code: str) -> list[dict]:
    """
    Check for Hadoop FileSystem API calls, DBFS paths, and Hadoop credential configuration.
    """
    issues = []

    # Check for Hadoop API patterns
    for pattern_name, info in HADOOP_PATTERNS.items():
        if pattern_name in code:
            issues.append(
                {
                    "api": pattern_name,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                    "how_to_fix": info.get("how_to_fix"),
                }
            )

    # Check for broader Hadoop patterns via regex
    hadoop_regex_patterns = [
        (r"org\.apache\.hadoop\.fs\.FileSystem", "Hadoop FileSystem API"),
        (r"org\.apache\.hadoop\.fs\.Path", "Hadoop Path API"),
        (r"org\.apache\.hadoop\.conf\.Configuration", "Hadoop Configuration API"),
        (r"sc\._jvm\.org\.apache\.hadoop", "Hadoop JVM interop via SparkContext"),
        (r"_jsc\.hadoopConfiguration", "Hadoop Configuration via _jsc"),
    ]
    for regex, name in hadoop_regex_patterns:
        if re.search(regex, code) and not any(i["api"] == name for i in issues):
            issues.append(
                {
                    "api": name,
                    "risk": 1.0,
                    "reason": f"{name} requires JVM interop not available in Spark Connect.",
                    "category": "Hadoop Filesystem",
                    "how_to_fix": "Replace with cloud-native SDK (boto3/azure-storage-blob/google-cloud-storage) or Snowflake stage operations.",
                }
            )

    # SNOW-3347699: Check for DBFS path patterns
    for dbfs_pattern in DBFS_PATH_PATTERNS:
        if dbfs_pattern in code:
            issues.append(
                {
                    "api": f"DBFS path ({dbfs_pattern})",
                    "risk": 0.8,
                    "reason": f"DBFS path '{dbfs_pattern}' is Databricks-specific and not available in SCOS. Rewrite to Snowflake internal stage + COPY INTO.",
                    "category": "Hadoop Filesystem",
                    "how_to_fix": "Replace DBFS paths with Snowflake stage references (@STAGE_NAME/path). Upload data to a Snowflake stage first.",
                }
            )
            break  # Only report DBFS once

    return issues


# SNOW-3347693: Check for JVM-only library imports
def check_jvm_library_imports(code: str) -> list[dict]:
    """
    Check for imports of JVM-dependent libraries (Deequ, pydeequ, Great Expectations Spark, etc.)
    that require SparkContext JVM interop not available in Spark Connect.
    """
    issues = []

    for module, info in JVM_ONLY_IMPORTS.items():
        if f"import {module}" in code or f"from {module}" in code:
            issues.append(
                {
                    "api": module,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                    "how_to_fix": info.get("how_to_fix"),
                }
            )

    # Also detect Deequ usage patterns even without explicit imports
    deequ_patterns = ["VerificationSuite", "VerificationResult", "CheckLevel", "pydeequ"]
    for pattern in deequ_patterns:
        if pattern in code and not any("pydeequ" in i.get("api", "") for i in issues):
            issues.append(
                {
                    "api": f"pydeequ ({pattern})",
                    "risk": 1.0,
                    "reason": f"{pattern} is part of pydeequ/Deequ which requires JVM interop not available in Spark Connect.",
                    "category": "JVM Library",
                    "how_to_fix": JVM_ONLY_IMPORTS["pydeequ"]["how_to_fix"],
                }
            )
            break

    return issues


# SNOW-3390000: Check for unsupported Spark ecosystem libraries
def check_unsupported_ecosystem_libs(code: str) -> list[dict]:
    """Detect imports/usages of Spark ecosystem libraries that have no Snowpark
    Connect equivalent (GraphFrames, Spark NLP, Mosaic, spark-xgboost, Koalas /
    pandas-on-Spark, SynapseML, Petastorm).

    These carry no curated trigger in ``kb_rules.json`` and are not caught by the
    pyspark.ml / JVM-validation detectors, so without this detector a block using
    them yields no rule-based issue and — absent a fuzzy RAG match — is dropped
    before the COMPLETE pass (see ``_process_single_block``). Surfacing them as a
    high-risk issue guarantees the block reaches the LLM, where the HARD RISK
    FLOOR prompt rule forces a Snowflake-native conversion.
    """
    issues = []
    seen: set[str] = set()

    for module, info in UNSUPPORTED_ECOSYSTEM_IMPORTS.items():
        if f"import {module}" in code or f"from {module}" in code:
            seen.add(module)
            issues.append({
                "api": module,
                "risk": info["risk"],
                "reason": info["reason"],
                "category": info["category"],
                "how_to_fix": info.get("how_to_fix"),
            })

    # Usage-token fallback: catch a library whose import lives in another cell
    # (e.g. `g = GraphFrame(...)` after an earlier `from graphframes import ...`).
    for token, module in _ECOSYSTEM_USAGE_TOKENS.items():
        if token in code and module not in seen:
            seen.add(module)
            info = UNSUPPORTED_ECOSYSTEM_IMPORTS[module]
            issues.append({
                "api": f"{module} ({token.strip('.(')})",
                "risk": info["risk"],
                "reason": info["reason"],
                "category": info["category"],
                "how_to_fix": info.get("how_to_fix"),
            })

    return issues


# SNOW-3390000: Unknown-import fail-safe
def check_unknown_third_party_imports(code: str) -> list[dict]:
    """Fail-safe for the long tail of unsupported dependencies.

    The enumerated blocklists only catch libraries we anticipated. A block that
    imports some *other* third-party module (a niche Spark package, a proprietary
    Databricks module, an internal in-house lib) carries no curated trigger and
    usually no RAG match — so without a signal it is dropped before the LLM pass
    and ships un-reviewed. This detector emits a low-severity *review* issue for
    any import that is not recognizably safe (stdlib / pyspark core / a curated
    driver-safe allowlist) and is not already owned by a dedicated detector. That
    guarantees the block reaches the LLM, which — with the HARD RISK FLOOR rule —
    judges it from its own knowledge (unknown Spark/Databricks extension → high;
    ordinary pure-Python lib → low, and then dropped by the emit threshold).

    Kept intentionally low-risk (0.4) so it surfaces for review without inflating
    a benign import into a blocking error.
    """
    issues: list[dict] = []
    seen: set[str] = set()

    for match in _IMPORT_RE.finditer(code):
        module = match.group(1)
        top = module.split(".")[0]
        if top in seen:
            continue
        if not _is_reviewable_import(module):
            continue
        # Already owned by a dedicated unsupported-API detector — don't double-flag.
        if any(module == k or module.startswith(k + ".") for k in _KNOWN_UNSUPPORTED_KEYS):
            continue
        seen.add(top)
        issues.append({
            "api": f"import {module}",
            "risk": 0.4,
            "reason": (
                f"'{module}' is not a recognized SCOS-supported dependency. Verify it "
                "runs on the Snowpark Connect driver (no JVM interop or Spark "
                "cluster-side packages); if it is a Spark/Databricks extension it must "
                "be converted to a Snowflake-native equivalent."
            ),
            "category": "Unknown Dependency (review)",
            "how_to_fix": (
                "Confirm the library is pure-Python and driver-side compatible, or "
                "replace it with a Snowflake-native equivalent (Snowpark, "
                "snowflake-ml-python, Cortex, or an external function)."
            ),
        })

    return issues


# SNOW-3319134: Check for ML pipeline patterns
def check_ml_pipeline_patterns(code: str) -> list[dict]:
    """
    Check for pyspark.ml pipeline patterns (estimators, VectorAssembler, Pipeline, CrossValidator)
    and provide guided transformation to snowflake.ml equivalents.
    """
    issues = []
    found_patterns = set()

    for class_name, info in ML_PIPELINE_PATTERNS.items():
        # Check for import or instantiation patterns
        patterns = [
            f"import {class_name}",
            f"from pyspark.ml",  # will be refined below
            f"{class_name}(",
        ]
        if any(p in code for p in patterns) and class_name in code and class_name not in found_patterns:
            found_patterns.add(class_name)
            issues.append(
                {
                    "api": class_name,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                    "how_to_fix": info.get("how_to_fix"),
                }
            )

    return issues


# SNOW-3319139: Check for UDTF/UDAF patterns
def check_udtf_udaf_patterns(code: str) -> list[dict]:
    """
    Check for PySpark UDTF and UDAF patterns that need structural transformation
    for Snowpark equivalents.
    """
    issues = []

    for pattern_name, info in UDTF_UDAF_PATTERNS.items():
        if pattern_name in code:
            issues.append(
                {
                    "api": pattern_name,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                    "how_to_fix": info.get("how_to_fix"),
                }
            )

    return issues


# SNOW-3277715: Check for lazy view re-evaluation patterns
def check_view_reuse_patterns(code: str) -> list[dict]:
    """
    SNOW-3277715: Detect temp view reuse patterns where a DataFrame references a
    view that is later overwritten via createOrReplaceTempView, or dropped via
    spark.catalog.dropTempView while still referenced.

    In Spark Classic, the logical plan is resolved eagerly. In Spark Connect (SCOS),
    the plan is unresolved and re-resolves by name on each evaluation — causing
    silent result differences or "view not found" errors.
    """
    issues = []

    # Extract all createOrReplaceTempView calls with their view names
    create_view_pattern = r'\.createOrReplaceTempView\s*\(\s*["\']([^"\']+)["\']\s*\)'
    view_creations = re.findall(create_view_pattern, code)

    # Extract all spark.sql / spark.table references to view names
    sql_from_pattern = r'spark\.sql\s*\(\s*["\'].*?FROM\s+(\w+)'
    table_ref_pattern = r'spark\.table\s*\(\s*["\'](\w+)["\']\s*\)'
    sql_refs = re.findall(sql_from_pattern, code, re.IGNORECASE | re.DOTALL)
    table_refs = re.findall(table_ref_pattern, code)
    all_view_refs = set(sql_refs + table_refs)

    # Check for view name reuse: same name created more than once
    view_counts = Counter(view_creations)
    reused_views = {name for name, count in view_counts.items() if count > 1}

    # Check for views that are both referenced and overwritten
    overwritten_refs = all_view_refs & set(view_creations)
    # Only flag if the same view name appears in createOrReplaceTempView AND is
    # referenced, AND is created more than once (indicating overwrite)
    flagged_views = overwritten_refs & reused_views

    for view_name in flagged_views:
        issues.append({
            "api": f"createOrReplaceTempView('{view_name}') — view reuse",
            "risk": 0.8,
            "reason": (
                f"Temp view '{view_name}' is overwritten via createOrReplaceTempView "
                "while an existing DataFrame may still reference it. In Spark Connect "
                "(SCOS), the DataFrame re-resolves against the new view definition on "
                "each evaluation, producing different results than Spark Classic."
            ),
            "category": "Lazy View Re-Evaluation",
            "how_to_fix": (
                f"After the overwriting createOrReplaceTempView('{view_name}'), "
                f"re-read the DataFrame: df = spark.sql(\"SELECT * FROM {view_name}\")"
            ),
        })

    # Check for dropTempView while DataFrame still references the view
    drop_view_pattern = r'\.catalog\.dropTempView\s*\(\s*["\'](\w+)["\']\s*\)'
    dropped_views = set(re.findall(drop_view_pattern, code))
    dropped_refs = all_view_refs & dropped_views

    for view_name in dropped_refs:
        issues.append({
            "api": f"dropTempView('{view_name}') — referenced after drop",
            "risk": 0.9,
            "reason": (
                f"Temp view '{view_name}' is dropped via spark.catalog.dropTempView "
                "while an existing DataFrame still references it. In Spark Connect "
                "(SCOS), the server will raise a 'view not found' error, whereas "
                "Spark Classic continues to work with the already-resolved plan."
            ),
            "category": "Lazy View Re-Evaluation",
            "how_to_fix": (
                f"Materialize the DataFrame before dropping the view, or "
                f"do not drop '{view_name}' while it is still referenced."
            ),
        })

    return issues


# SNOW-3319141: Check for Delta Lake patterns
def check_delta_lake_patterns(code: str) -> list[dict]:
    """
    Check for Delta Lake operations (DeltaTable API, delta format reads/writes,
    OPTIMIZE/VACUUM SQL) and provide Snowflake-native equivalents.
    """
    issues = []

    # Check for DeltaTable API and delta.tables import
    for pattern_name, info in DELTA_LAKE_PATTERNS.items():
        if pattern_name in code:
            issues.append(
                {
                    "api": pattern_name,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                    "how_to_fix": info.get("how_to_fix"),
                }
            )

    # Check for Delta SQL keywords in spark.sql() calls
    code_upper = code.upper()
    for keyword in DELTA_SQL_KEYWORDS:
        if keyword in code_upper and "spark.sql" in code.lower():
            if not any(i.get("api") == f"Delta SQL: {keyword}" for i in issues):
                if keyword == "OPTIMIZE":
                    fix = "Remove OPTIMIZE. Snowflake uses automatic micro-partitioning — no manual optimization needed."
                elif keyword == "VACUUM":
                    fix = "Remove VACUUM. Snowflake manages Time Travel retention automatically."
                else:  # ZORDER
                    fix = "Replace ZORDER BY with ALTER TABLE ... CLUSTER BY for Snowflake clustering keys."
                issues.append(
                    {
                        "api": f"Delta SQL: {keyword}",
                        "risk": 0.9,
                        "reason": f"Delta Lake {keyword} SQL is not supported in SCOS. Snowflake handles this automatically.",
                        "category": "Delta Lake",
                        "how_to_fix": fix,
                    }
                )

    return issues


def check_duplicate_struct_fields(code: str) -> list[dict]:
    """Detect StructType schemas that define the same field name more than once.

    Spark permits duplicate field names in a ``StructType`` at definition time,
    but Snowflake/SCOS requires unique column/field names and rejects the schema
    when it is materialized (``createDataFrame`` / ``read`` / ``saveAsTable``).
    This construction uses only "safe" APIs (``StructType`` / ``StructField`` /
    ``.add``), so the token- and KB-based detectors never see it — hence a
    dedicated AST check. Detection is structurally certain (the duplicate names
    are literally present), so callers emit it deterministically (no LLM).

    Handles both schema forms, per distinct StructType instance (so a duplicate
    in an inner nested struct is not confused with the outer one):
      * chained  ``StructType().add("x", ...).add("x", ...)``
      * list     ``StructType([StructField("x", ...), StructField("x", ...)])``
    """
    issues: list[dict] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return issues

    # field-name literals grouped by the identity of their owning StructType node
    groups: dict[int, list[str]] = {}

    def _chain_root(call: ast.Call) -> ast.AST:
        # walk down the receiver while we are on chained ``.add(...)`` calls, so
        # every ``.add`` in one chain maps to the same terminal StructType node
        node: ast.AST = call
        while (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add"
        ):
            node = node.func.value
        return node

    def _str_arg0(call: ast.Call) -> str | None:
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(
            call.args[0].value, str
        ):
            return call.args[0].value
        return None

    def _is_named(func: ast.AST, name: str) -> bool:
        return (isinstance(func, ast.Name) and func.id == name) or (
            isinstance(func, ast.Attribute) and func.attr == name
        )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # form 1: <struct>.add("name", ...)
        if isinstance(func, ast.Attribute) and func.attr == "add":
            nm = _str_arg0(node)
            if nm is not None:
                groups.setdefault(id(_chain_root(node)), []).append(nm)
        # form 2: StructType([StructField("name", ...), ...])
        elif _is_named(func, "StructType"):
            for arg in node.args:
                if isinstance(arg, (ast.List, ast.Tuple)):
                    for elt in arg.elts:
                        if isinstance(elt, ast.Call) and _is_named(elt.func, "StructField"):
                            nm = _str_arg0(elt)
                            if nm is not None:
                                groups.setdefault(id(node), []).append(nm)

    for names in groups.values():
        for name in sorted({n for n in names if names.count(n) > 1}):
            issues.append({
                "api": f"StructType duplicate field '{name}'",
                "risk": 0.9,
                "reason": (
                    f"StructType defines field '{name}' more than once. Spark permits "
                    "duplicate field names at definition, but Snowflake/SCOS requires "
                    "unique column/field names and rejects the schema when it is "
                    "materialized (createDataFrame / read / saveAsTable)."
                ),
                "category": "Schema Structural Validity",
                "how_to_fix": (
                    f"Rename the duplicate '{name}' field(s) so every field in the "
                    "StructType has a unique name."
                ),
            })
    return issues


def _build_assessment_text(preliminary_assessment: dict) -> str:
    """Build preliminary assessment text for LLM prompt."""
    assessment_parts = []

    scos_issues = preliminary_assessment.get("scos_issues", [])
    if scos_issues:
        assessment_parts.append("SCOS Compatibility Issues:")
        for issue in scos_issues:
            issue_name = issue.get("api") or issue.get("format", "unknown")
            assessment_parts.append(
                f"  - {issue_name}: {issue['reason']} (Risk: {issue['risk'] * 100:.0f}%)"
            )
            if issue.get("how_to_fix"):
                assessment_parts.append(f"    Fix: {issue['how_to_fix']}")

    api_risk = preliminary_assessment.get("api_risk", 0)
    if api_risk > 0:
        assessment_parts.append(f"\nAPI Compatibility Risk: {api_risk * 100:.0f}%")
        func_compat = preliminary_assessment.get("func_compatibility", [])
        for f in func_compat:
            if f.get("score", 1.0) < 1.0:
                assessment_parts.append(
                    f"  - {f['name']}: {f['compatibility']} (score: {f['score'] * 100:.0f}%)"
                )

    scos_risk = preliminary_assessment.get("scos_risk", 0)
    if scos_risk > 0:
        assessment_parts.append(f"\nSCOS Issues Risk: {scos_risk * 100:.0f}%")

    return (
        "\n".join(assessment_parts)
        if assessment_parts
        else "No rule-based issues detected."
    )


def _severity_label(score: float) -> str:
    """Map a curated trigger severity score back to a human label."""
    if score >= 0.9:
        return "HIGH"
    if score >= 0.6:
        return "MEDIUM"
    return "LOW"


def _build_patterns_text(matching_patterns: list[dict]) -> str:
    """Build the matched-pattern text for the LLM prompt.

    Two kinds of match are rendered differently:
      * ``trigger_exact`` — from the offline trigger KB. The named API/SQL
        construct LITERALLY appears in the input code, so it is presented as an
        EXACT MATCH with a curated SEVERITY (not a cosine similarity).
      * ``rag_similar`` — from the fuzzy embedding backends; presented as a
        similar test case with a cosine similarity, as before.
    """
    if not matching_patterns:
        return "No compatibility triggers or similar test cases matched."

    parts = []
    for i, p in enumerate(matching_patterns, 1):
        if p.get("match_kind") == "trigger_exact":
            parts.append(
                f"""TRIGGER #{i} — EXACT MATCH (curated severity: {p.get('severity', 'MEDIUM')})
Rule: {p.get('test_name', 'N/A')}
Matched anchor / where: {p.get('code', '')}
Compatibility note (root cause): {p.get('root_cause', 'N/A')}
Fix / notes: {p.get('additional_notes', 'N/A')}"""
            )
        else:
            parts.append(
                f"""TEST CASE #{i} (Cosine similarity: {p.get('score', 0.0):.1%})
Test Name: {p.get('test_name', 'N/A')}
Code/SQL:
```
{p.get('code', '')}
```
Root Cause: {p.get('root_cause', 'N/A')}
Additional Notes: {p.get('additional_notes', 'N/A')}"""
            )
    return "\n\n".join(parts)


# --- Phase 0.5 recipe-context helpers ---------------------------------------
# These thread the recipe_edits block from migration_state.json into the
# Cortex prompt so the LLM has explicit grounding for sites Phase 0.5 already
# touched.  The contract:
#   - `_rewrite` recipes have applied a deterministic LibCST rewrite already;
#     the LLM should not re-flag the line as a fresh issue.
#   - `_annotate` / `_comment` recipes flagged the divergence inline but did
#     not auto-rewrite; the LLM should attempt a workload-specific fix when
#     the surrounding context makes intent clear.
# Backwards-compatible: if no state is passed (or no recipe_edits block
# exists), every per-block recipe list is empty and the prompt collapses to
# "No recipes fired on this block."
def _recipe_path_key(file_path: Path, source_root: Path | None) -> str | None:
    """Return the canonical ``recipe_edits`` key for ``file_path``.

    Phase 0.5 keys ``recipe_edits`` by **path relative to the conversion
    `Output/` root** (e.g. ``"etl/main.py"``).  The analyzer must use the
    same canonicalization to look entries up; basename-only matching is
    unsafe because real workloads routinely contain colliding basenames
    (``etl/main.py`` vs ``ml/main.py``, ``__init__.py`` × N, …) and a
    basename hit would silently route edits to the wrong file.

    Returns ``None`` when ``source_root`` is missing or ``file_path``
    cannot be resolved beneath it; callers treat that as a hard
    configuration error rather than fall back to a lossy alternative.
    """
    if source_root is None:
        return None
    try:
        return str(file_path.resolve().relative_to(source_root.resolve()))
    except ValueError:
        return None


# Module-scoped linkage telemetry.  Populated by `_recipe_edits_for_file`
# and emitted as a single summary log at the end of analyze_pyspark.main().
_RECIPE_LINKAGE_STATS: dict[str, int] = {
    "files_with_edits": 0,           # analyzer reached a file that had >=1 edit in recipe_edits_all
    "files_without_edits": 0,        # analyzer reached a file that had 0 edits (normal)
    "files_canonicalization_failed": 0,  # path-normalization failed; edits silently dropped
}
_RECIPE_LINKAGE_FAILED_PATHS: list[str] = []


def _recipe_edits_for_file(
    recipe_edits_all: dict[str, list[dict]] | None,
    file_path: Path,
    source_root: Path | None,
) -> list[dict]:
    """Return the list of recipe-edit entries that touched ``file_path``.

    Uses a single canonical relative-path key produced by
    :func:`_recipe_path_key`.  No basename or absolute-path fallbacks --
    they would risk silent mis-routing of edits between same-name files
    in different directories.
    """
    if not recipe_edits_all:
        return []
    key = _recipe_path_key(file_path, source_root)
    if key is None:
        _RECIPE_LINKAGE_STATS["files_canonicalization_failed"] += 1
        _RECIPE_LINKAGE_FAILED_PATHS.append(str(file_path))
        logger.warning(
            "Could not compute canonical recipe_edits key for %s relative to source_root=%s; "
            "any Phase 0.5 edits for this file will be IGNORED.  Pass the conversion's "
            "Output/ root as the path argument to keep recipe grounding intact.",
            file_path,
            source_root,
        )
        return []
    edits = recipe_edits_all.get(key) or []
    if edits:
        _RECIPE_LINKAGE_STATS["files_with_edits"] += 1
    else:
        _RECIPE_LINKAGE_STATS["files_without_edits"] += 1
    return edits


def _recipe_edits_for_block(
    file_recipe_edits: list[dict],
    line_start: int,
    line_end: int,
) -> list[dict]:
    """Return recipe edits whose output line falls inside the block range.

    Prefers ``output_line`` (the exact line in the final output file,
    populated by Phase 0.5's post-pass after all recipes complete) over
    ``src_line`` (the line in the intermediate content when the recipe ran,
    which drifts forward as recipes prepend comment lines).  Falls back to
    ``src_line`` for legacy entries written before ``output_line`` was
    introduced.
    """
    if not file_recipe_edits:
        return []
    out = []
    for e in file_recipe_edits:
        ol = e.get("output_line")
        match_line = ol if isinstance(ol, int) else e.get("src_line")
        if isinstance(match_line, int) and line_start <= match_line <= line_end:
            out.append(e)
    return out


def _classify_recipe_kind(recipe_id: str) -> str:
    """Return ``'rewrite'`` | ``'annotate'`` | ``'comment'`` | ``'other'``."""
    if recipe_id.endswith("_rewrite"):
        return "rewrite"
    if recipe_id.endswith("_annotate"):
        return "annotate"
    if recipe_id.endswith("_comment"):
        return "comment"
    return "other"


def _build_recipe_text(block_recipe_edits: list[dict]) -> str:
    """Build the per-block ``Recipe Context`` section for the Cortex prompt."""
    if not block_recipe_edits:
        return "No recipes fired on this block."
    lines = []
    for e in block_recipe_edits:
        rid = e.get("recipe_id", "<unknown>")
        kind = _classify_recipe_kind(rid)
        src_line = e.get("src_line", "?")
        if kind == "rewrite":
            lines.append(
                f"  - {rid} @ src_line {src_line} (REWRITE applied): a "
                f"deterministic LibCST rewrite has ALREADY been applied at "
                f"this site by Phase 0.5.  Do NOT emit a fresh issue for this "
                f"line.  If you have workload-specific context that suggests "
                f"a better rewrite, set kind='recipe_adjacent' and propose it "
                f"in `fix`."
            )
        elif kind in ("annotate", "comment"):
            lines.append(
                f"  - {rid} @ src_line {src_line} (ANNOTATE-only): Phase 0.5 "
                f"flagged the divergence inline with a `# SCOS:` comment but "
                f"did NOT auto-rewrite.  When workload context makes intent "
                f"clear, propose a concrete rewrite in `fix` and set "
                f"kind='recipe_incomplete'."
            )
        else:
            lines.append(
                f"  - {rid} @ src_line {src_line} ({kind.upper()})"
            )
    return "\n".join(lines)




@dataclass
class CodeBlock:
    """A block of code extracted from a PySpark file."""

    code: str
    line_start: int
    line_end: int
    block_type: str  # "sql", "expr", "method_chain", "statement"
    functions: list[str]  # Functions/methods found in this block
    cell_id: int | None = None  # Populated when block originates from a notebook cell
    # Language the block's cell was written in. "python" for plain .py files and
    # Python notebook cells; "scala" when cross-language extraction is enabled
    # and the block came from an embedded Scala cell.
    language: str = "python"

    @property
    def normalized_code(self) -> str:
        """Return normalized code for RAG queries (comments removed, whitespace normalized)."""
        return normalize_code_lightweight(self.code)


class PySparkExtractor(ast.NodeVisitor):
    """Extract PySpark code blocks using AST."""

    def __init__(
        self, source_lines: list[str], pyspark_methods: set[str] | None = None
    ):
        self.source_lines = source_lines
        self.blocks: list[CodeBlock] = []
        # Use provided methods or fall back to common ones
        self.pyspark_methods = pyspark_methods or {
            "select",
            "filter",
            "where",
            "groupBy",
            "agg",
            "join",
            "orderBy",
            "sort",
            "withColumn",
            "drop",
            "distinct",
            "union",
            "intersect",
            "subtract",
            "limit",
            "sample",
            "createDataFrame",
            "read",
            "write",
            "show",
            "collect",
        }

    def get_source(self, node: ast.AST) -> str:
        """Get source code for a node."""
        try:
            return ast.get_source_segment("\n".join(self.source_lines), node) or ""
        except Exception:
            # Fallback: get lines
            start = node.lineno - 1
            end = getattr(node, "end_lineno", node.lineno)
            return "\n".join(self.source_lines[start:end])

    def extract_string_value(self, node: ast.AST) -> str | None:
        """Extract string value from a node."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            # f-string - try to get parts
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant):
                    parts.append(str(value.value))
                elif isinstance(value, ast.FormattedValue):
                    # Extract source of the expression inside the f-string
                    parts.append("<" + self.get_source(value.value) + ">")
            return "".join(parts) if parts else None
        return None

    def extract_functions(self, code: str) -> list[str]:
        """Extract function/method names from code."""
        functions = []
        # Pattern for function calls: word followed by (
        pattern = r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
        for match in re.finditer(pattern, code):
            func_name = match.group(1)
            # Skip common Python keywords and builtins
            if func_name not in [
                "if",
                "for",
                "while",
                "with",
                "def",
                "class",
                "print",
                "len",
                "str",
                "int",
                "list",
                "dict",
                "set",
                "tuple",
            ]:
                functions.append(func_name)
        return list(set(functions))

    def _has_call_nodes(self, node: ast.AST) -> bool:
        """Check if an AST node contains any function/method calls."""
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                return True
        return False

    def visit_Call(self, node: ast.Call):
        """Visit function/method calls."""
        # Check for spark.sql(...) or session.sql(...)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "sql":
            if node.args:
                sql_str = self.extract_string_value(node.args[0])
                if sql_str:
                    self.blocks.append(
                        CodeBlock(
                            code=sql_str,
                            line_start=node.lineno,
                            line_end=getattr(node, "end_lineno", node.lineno),
                            block_type="sql",
                            functions=self.extract_functions(sql_str),
                        )
                    )

        # Check for expr(...)
        if isinstance(node.func, ast.Name) and node.func.id == "expr":
            if node.args:
                expr_str = self.extract_string_value(node.args[0])
                if expr_str:
                    self.blocks.append(
                        CodeBlock(
                            code=expr_str,
                            line_start=node.lineno,
                            line_end=getattr(node, "end_lineno", node.lineno),
                            block_type="expr",
                            functions=self.extract_functions(expr_str),
                        )
                    )

        # Check for selectExpr(...)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "selectExpr":
            for arg in node.args:
                expr_str = self.extract_string_value(arg)
                if expr_str:
                    self.blocks.append(
                        CodeBlock(
                            code=expr_str,
                            line_start=node.lineno,
                            line_end=getattr(node, "end_lineno", node.lineno),
                            block_type="selectExpr",
                            functions=self.extract_functions(expr_str),
                        )
                    )

        self.generic_visit(node)

    def _emit_method_chain_block(self, expr_node: ast.AST, anchor: ast.AST) -> None:
        """Emit a ``method_chain`` block for ``expr_node`` if it contains a
        known PySpark method call. ``anchor`` supplies the line numbers (the
        enclosing statement, so e.g. an ``assert``'s reported line matches the
        assert keyword rather than just its test expression)."""
        source = self.get_source(expr_node)
        if any(f".{method}(" in source for method in self.pyspark_methods):
            self.blocks.append(
                CodeBlock(
                    code=source,
                    line_start=anchor.lineno,
                    line_end=getattr(anchor, "end_lineno", anchor.lineno),
                    block_type="method_chain",
                    functions=self.extract_functions(source),
                )
            )

    def visit_Expr(self, node: ast.Expr):
        """Visit expression statements (often method chains)."""
        self._emit_method_chain_block(node, node)
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert):
        """Visit assert statements — extremely common in PySpark migration
        test/validation suites (``assert df.select(...).collect() == [...]``).

        ``ast.Assert`` has no dedicated visitor by default, so its ``test``
        expression (and any method chain inside it) was previously invisible
        to block extraction entirely: ``visit_Call`` only recognises the
        narrow ``.sql()``/``expr()``/``.selectExpr()`` shapes, and
        ``visit_Expr`` only fires for bare top-level expression statements,
        not the ``Compare``/``Call`` expression nested inside an assert's
        ``test``. Without this visitor, RAG/trigger-KB compatibility checks
        never ran on any PySpark call wrapped in an ``assert`` — a silent
        blind spot for exactly the kind of code this analyzer exists to
        catch (migrated PySpark test suites).
        """
        self._emit_method_chain_block(node.test, node)
        if node.msg is not None:
            self._emit_method_chain_block(node.msg, node)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return):
        """Visit return statements (``return df.select(...)``).

        Like ``assert``, ``ast.Return`` wraps an expression without being an
        ``ast.Expr`` statement itself, so a helper function that builds and
        returns a DataFrame in one chain (a common pattern) was also
        invisible to block extraction before this visitor existed.
        """
        if node.value is not None:
            self._emit_method_chain_block(node.value, node)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        """Visit assignments (df = spark.read..., etc.)."""
        # Skip simple literal assignments (no function calls)
        # This filters out: var_a = 10, my_list = ["a", "b"], config = {"k": "v"}
        if not self._has_call_nodes(node.value):
            self.generic_visit(node)
            return

        source = self.get_source(node)

        # Use the dotted-call pattern (".method(") like visit_Expr, not a bare
        # substring check. A bare check false-positives on short CSV method
        # names (e.g. "e" from pyspark.sql.functions.e matches almost anything).
        has_spark = "spark" in source.lower() or "session" in source.lower()
        has_pyspark_method = any(f".{method}(" in source for method in self.pyspark_methods)

        if has_spark or has_pyspark_method:
            self.blocks.append(
                CodeBlock(
                    code=source,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    block_type="assignment",
                    functions=self.extract_functions(source),
                )
            )

        self.generic_visit(node)

    # ---- File-scope RDD markers ------------------------------------------- #
    # RDD imports and ``RDD`` type annotations do not form assignment/expr
    # blocks, so without these visitors they are never seen by ``has_rdd_usage``
    # (the ``:RDD``/``->RDD`` regex and the RDD-import patterns are otherwise
    # dead at block granularity). Emit a small marker block per occurrence so
    # the existing detectors flag them. AST-based, so no false positives from
    # the substring ``RDD`` inside strings/comments/identifiers.

    @staticmethod
    def _annotation_mentions_rdd(annotation: ast.AST | None) -> bool:
        if annotation is None:
            return False
        for child in ast.walk(annotation):
            if isinstance(child, ast.Name) and child.id == "RDD":
                return True
            if isinstance(child, ast.Attribute) and child.attr == "RDD":
                return True
        return False

    def _add_marker_block(self, node: ast.AST, block_type: str) -> None:
        self.blocks.append(
            CodeBlock(
                code=self.get_source(node),
                line_start=node.lineno,
                line_end=getattr(node, "lineno", node.lineno),
                block_type=block_type,
                functions=[],
            )
        )

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """``from pyspark import RDD`` / ``from pyspark.rdd import ...``."""
        module = node.module or ""
        is_rdd = module == "pyspark.rdd" or module.startswith("pyspark.rdd.") or (
            module == "pyspark" and any(a.name == "RDD" for a in node.names)
        )
        if is_rdd:
            self._add_marker_block(node, "rdd_import")
        elif module and _is_reviewable_import(module):
            # SNOW-3390000: emit a marker block so a non-safe third-party import
            # (which otherwise forms no block) is still analyzed. Its dedicated
            # detector or the unknown-import fail-safe then classifies it.
            self._add_marker_block(node, "third_party_import")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        """``import pyspark.rdd`` / ``import pyspark.rdd as r``."""
        if any(a.name == "pyspark.rdd" or a.name.startswith("pyspark.rdd.") for a in node.names):
            self._add_marker_block(node, "rdd_import")
        elif any(_is_reviewable_import(a.name) for a in node.names):
            # SNOW-3390000: see visit_ImportFrom above.
            self._add_marker_block(node, "third_party_import")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        """``my_rdd: RDD = ...`` -- annotated assignment with an RDD type."""
        if self._annotation_mentions_rdd(node.annotation):
            self._add_marker_block(node, "rdd_annotation")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """``def f(x: RDD) -> RDD:`` -- RDD in a parameter / return annotation."""
        args = node.args
        annotated = [a.annotation for a in (
            args.args + args.posonlyargs + args.kwonlyargs
        )]
        annotated += [args.vararg.annotation if args.vararg else None,
                      args.kwarg.annotation if args.kwarg else None,
                      node.returns]
        if any(self._annotation_mentions_rdd(a) for a in annotated):
            # Anchor on the signature line(s) only (def ... : ), not the body,
            # so the marker's code carries the ``: RDD`` / ``-> RDD`` token that
            # ``has_rdd_usage`` matches.
            body_start = node.body[0].lineno if node.body else node.lineno + 1
            sig = "\n".join(self.source_lines[node.lineno - 1 : body_start - 1])
            self.blocks.append(
                CodeBlock(
                    code=sig or self.get_source(node),
                    line_start=node.lineno,
                    line_end=max(node.lineno, body_start - 1),
                    block_type="rdd_annotation",
                    functions=[],
                )
            )
        self.generic_visit(node)


# ---- Fallback sweep for calls invisible to the Spark-aware visitors ------- #
# visit_Expr/visit_Assign only recognize statements that look like PySpark
# (a "spark"/"session" reference, or a known dotted PySpark method). A call to
# something else -- AWS Glue's glueContext.write_dynamic_frame.from_options(),
# Azure Synapse's mssparkutils.fs.mount(), or any other vendor SDK -- matches
# neither check and is invisible today, no matter what kind of statement it's
# in. Instead of adding a visit_X per statement type, we sweep every Call node
# once and anchor each uncovered one to its enclosing statement/expression.


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Return ``{child: parent}`` for every node in ``tree``."""
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    return parent_map


def _fallback_anchor_node(node: ast.AST, parent_map: dict[ast.AST, ast.AST]) -> ast.AST | None:
    """Find the node that should anchor a fallback block for the ``Call`` at
    ``node``: the nearest enclosing statement, or -- if the call is in an
    if/while's test, a for's iterable, or a with's context expression -- that
    smaller sub-expression instead of the whole statement (so we don't
    re-scan the statement's body, which already has its own blocks).
    """
    cur: ast.AST | None = node
    prev: ast.AST | None = None
    while cur is not None:
        if isinstance(cur, ast.stmt):
            if isinstance(cur, (ast.If, ast.While)) and prev is cur.test:
                return prev
            if isinstance(cur, (ast.For, ast.AsyncFor)) and prev is cur.iter:
                return prev
            if isinstance(cur, (ast.With, ast.AsyncWith)) and isinstance(prev, ast.withitem):
                # Call's parent is the withitem wrapper here, not the With
                # statement itself -- unwrap it to get the context_expr.
                return prev.context_expr
            return cur
        prev, cur = cur, parent_map.get(cur)
    return None


def _collect_fallback_call_blocks(
    tree: ast.AST,
    source_lines: list[str],
    existing_blocks: list["CodeBlock"],
    extract_functions_fn,
) -> list["CodeBlock"]:
    """Find Call nodes not covered by any existing block and emit one
    fallback_call block per anchor (see _fallback_anchor_node).

    Multiple calls sharing an anchor (e.g. f(g(x)), or two calls in one
    ``if`` test) collapse into a single block via seen_anchor_ranges.
    """
    covered_ranges = [(b.line_start, b.line_end) for b in existing_blocks]

    def _is_covered(lineno: int) -> bool:
        return any(start <= lineno <= end for start, end in covered_ranges)

    full_source = "\n".join(source_lines)
    parent_map = _build_parent_map(tree)
    seen_anchor_ranges: set[tuple[int, int]] = set()
    fallback_blocks: list[CodeBlock] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_covered(node.lineno):
            continue

        anchor = _fallback_anchor_node(node, parent_map)
        if anchor is None:
            continue

        anchor_start = anchor.lineno
        anchor_end = getattr(anchor, "end_lineno", anchor_start)
        if (anchor_start, anchor_end) in seen_anchor_ranges:
            continue
        if _is_covered(anchor_start):
            continue
        seen_anchor_ranges.add((anchor_start, anchor_end))

        if isinstance(anchor, ast.stmt):
            # A full statement is valid Python on its own -- take its lines.
            code = "\n".join(source_lines[anchor_start - 1 : anchor_end])
        else:
            # A sub-expression anchor (test/iter/context_expr). Slicing the
            # whole line would include the "if ...:" wrapper with no body,
            # which isn't valid Python and would trip up detect()'s parser.
            # Extract just the expression's own text instead.
            code = ast.get_source_segment(full_source, anchor) or "\n".join(
                source_lines[anchor_start - 1 : anchor_end]
            )

        fallback_blocks.append(
            CodeBlock(
                code=code,
                line_start=anchor_start,
                line_end=anchor_end,
                block_type="fallback_call",
                functions=extract_functions_fn(code),
            )
        )

    return fallback_blocks


# Known leading magic directives that can precede Python cell source.
# Includes Databricks cell-language markers (%python/%scala/%sql/%md/%sh/
# %fs/%run/%pyspark/%r) and common IPython line magics. Any line matching
# these prefixes is replaced by a single-line comment placeholder so
# ``ast.parse`` succeeds AND original line numbers are preserved 1:1.
_KNOWN_MAGIC_PREFIXES = (
    "%python", "%scala", "%sql", "%r", "%md", "%sh", "%fs", "%run", "%pyspark",
    "%matplotlib", "%time", "%timeit", "%load", "%config", "%env",
    "%autoreload", "%pip", "%conda",
)


def _strip_leading_magic_directive(source: str) -> str:
    """Replace an optional leading ``%magic`` line with a comment placeholder.

    We replace the directive with a ``# <preserved magic>`` line so line
    numbers in the original source map 1:1 to line numbers in the returned
    string. This avoids the off-by-one line-number errors that a "drop the
    line and shift by +1" strategy would produce.

    Unknown leading ``%`` lines are replaced with the same placeholder so
    ``ast.parse`` never chokes on them, but are also annotated so a human
    reader can find the original directive in the placeholder comment.
    """
    if not source:
        return source
    lines = source.split("\n", 1)
    first = lines[0].lstrip()
    rest = lines[1] if len(lines) > 1 else ""
    if not first.startswith("%") or first.startswith("%%"):
        return source

    # Extract the magic name (first whitespace-delimited token after %).
    first_word = first.split(None, 1)[0]
    is_known = any(first_word.startswith(prefix) for prefix in _KNOWN_MAGIC_PREFIXES)
    suffix = f" {first.strip()}" if not is_known else ""
    placeholder = f"# magic_directive_preserved:{first_word}{suffix}"
    if rest:
        return placeholder + "\n" + rest
    return placeholder


def extract_code_blocks_from_source(
    source: str,
    pyspark_methods: set[str] | None = None,
    cell_id: int | None = None,
) -> list[CodeBlock]:
    """Extract PySpark code blocks from an in-memory source string.

    Used both for plain ``.py`` files (via :func:`extract_code_blocks`) and for
    individual notebook cells, where ``cell_id`` identifies the cell. Line
    numbers on returned blocks are relative to ``source`` — for notebook cells
    that means "line within cell", which matches the ``cell:<id>:<line>``
    convention used by report generation.
    """
    analysis_source = _strip_leading_magic_directive(source)
    try:
        source_lines = analysis_source.splitlines()
        tree = ast.parse(analysis_source)
    except SyntaxError as e:
        logger.warning(f"Warning: Syntax error while parsing cell/source: {e}")
        return []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Warning: Could not parse cell/source: {e}")
        return []

    extractor = PySparkExtractor(source_lines, pyspark_methods)
    extractor.visit(tree)
    fallback_blocks = _collect_fallback_call_blocks(
        tree, source_lines, extractor.blocks, extractor.extract_functions
    )
    all_blocks = extractor.blocks + fallback_blocks
    if cell_id is not None:
        for block in all_blocks:
            block.cell_id = cell_id
    return all_blocks


def extract_code_blocks(
    file_path: Path, pyspark_methods: set[str] | None = None
) -> list[CodeBlock]:
    """Extract PySpark code blocks from a Python file or notebook.

    For notebooks (``.ipynb`` plus all Databricks formats supported by
    ``notebook_io``), each Python code cell is parsed independently and the
    resulting blocks carry ``cell_id`` so reports can tag them as
    ``cell:<id>:<line>``.
    """
    path_str = str(file_path)
    # Capture detection once and pass through to parse_notebook so the
    # notebook's 4 KiB head isn't re-read inside parse_notebook.
    info = detect_format(path_str)
    if info.get("format") != "not_notebook":
        try:
            nb = parse_notebook(path_str, info=info)
        except (ValueError, OSError) as e:
            logger.warning(f"Warning: Could not parse notebook {file_path}: {e}")
            return []

        blocks: list[CodeBlock] = []
        for cell in nb.cells:
            if cell.cell_type != "code":
                continue
            if cell.cell_language != "python":
                continue
            blocks.extend(
                extract_code_blocks_from_source(
                    cell.source, pyspark_methods, cell_id=cell.index
                )
            )
        return blocks

    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"Warning: Could not read {file_path}: {e}")
        return []
    return extract_code_blocks_from_source(source, pyspark_methods)


def find_pyspark_files(
    path: Path, notebook_index: dict[str, dict] | None = None
) -> list[Path]:
    """Find all Python sources and notebooks under ``path``.

    Includes ``.py`` files and every notebook format recognised by
    ``notebook_io`` (``.ipynb``, ``.python``, Databricks-native ``.scala`` /
    ``.sql`` JSON, and Databricks exported ``.py`` / ``.scala``). The Python
    analyzer extracts Python-language cells from each notebook — Scala / SQL /
    markdown cells are skipped here and handled by the Scala sub-skill's
    analyzer or ignored, per the cross-language delegation rules.

    When ``notebook_index`` is provided (typically loaded from
    ``migration_state.json`` after Phase 0), notebook membership checks use
    the cached mapping instead of opening every candidate file — this skips
    the redundant per-file I/O for large workloads. The index is keyed by
    path string and must contain entries for every notebook under ``path``;
    files absent from the index are still included if their extension is
    ``.py`` (the only plain-text extension we unconditionally pick up).

    Additionally, when an index entry exposes ``code_cells_by_language``,
    notebooks with zero Python code cells are excluded — the Python analyzer
    would filter every cell out anyway, so we skip the notebook entirely to
    avoid the parse cost. Entries without that field are still included.
    """
    candidate_exts = {".py", ".ipynb", ".python", ".scala", ".sql"}

    def _index_entry(candidate: Path) -> dict | None:
        """Return the notebook_index entry for ``candidate``, if any."""
        if notebook_index is None:
            return None
        key = str(candidate)
        if key in notebook_index:
            return notebook_index[key]
        # The index is keyed by ``os.path.abspath(fpath)`` (see
        # ``notebook_io.scan_notebooks``), so we must match the same
        # normalization here. ``Path.resolve()`` would follow symlinks
        # and return the real path, which misses the abspath-keyed entry
        # whenever the workload is reached through a symlink.
        abs_key = os.path.abspath(key)
        if abs_key in notebook_index:
            return notebook_index[abs_key]
        return None

    def _is_notebook_cached(candidate: Path) -> bool:
        if notebook_index is not None:
            return _index_entry(candidate) is not None
        return is_notebook(str(candidate))

    def _has_python_cells(candidate: Path) -> bool:
        """True if the notebook has >=1 Python code cell, or if we don't know.

        Returning True when the index lacks ``code_cells_by_language`` keeps
        the optimization purely additive — we never drop notebooks on
        ambiguous input.
        """
        entry = _index_entry(candidate)
        if entry is None:
            return True
        counts = entry.get("code_cells_by_language")
        if not isinstance(counts, dict):
            return True
        return counts.get("python", 0) > 0

    if path.is_file():
        if path.suffix.lower() in {".py"}:
            return [path]
        if _is_notebook_cached(path) and _has_python_cells(path):
            return [path]
        return []

    # Walk the tree once (filtered to skip .venv / __pycache__ / .git / etc.)
    # and filter by extension in the loop, instead of running five separate
    # rglob passes (one per extension) that each descend into build/VCS
    # dirs. walk_filtered also prunes SKIP_DIRS so checked-in virtualenvs
    # and git object stores never reach the notebook detector.
    results: list[Path] = []
    for root, _dirs, files in walk_filtered(str(path)):
        root_path = Path(root)
        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext not in candidate_exts:
                continue
            candidate = root_path / fname
            if ext == ".py":
                # .py files may be plain Python OR a Databricks exported-text
                # notebook. Either way we include them — a plain .py always
                # has Python, and an exported-text .py notebook is flagged by
                # the index as notebook-with-python-cells.
                results.append(candidate)
            elif _is_notebook_cached(candidate) and _has_python_cells(candidate):
                results.append(candidate)
    return sorted(set(results))


def find_plain_sql_files(path: Path) -> list[Path]:
    """Return `.sql` files that are NOT Databricks native-JSON notebooks.

    ``notebook_io.detect_format`` classifies a ``.sql`` file as a notebook only
    when its first byte is ``{`` (Databricks native JSON format).  Any other
    ``.sql`` file — a standalone SQL script — is classified as
    ``"not_notebook"`` and silently excluded from ``find_pyspark_files``.  This
    function collects exactly those plain SQL files so the caller can route them
    through ``analyze_plain_sql_files``.
    """
    if path.is_file():
        if path.suffix.lower() == ".sql" and not is_notebook(str(path)):
            return [path]
        return []

    results: list[Path] = []
    for root, _dirs, files in walk_filtered(str(path)):
        root_path = Path(root)
        for fname in files:
            if Path(fname).suffix.lower() != ".sql":
                continue
            candidate = root_path / fname
            if not is_notebook(str(candidate)):
                results.append(candidate)
    return sorted(results)


def analyze_plain_sql_files(
    sql_files: list[Path],
    trigger_kb: TriggerKB,
    risk_threshold: float,
) -> list[dict]:
    """Scan plain `.sql` files for SCOS incompatibilities using the trigger KB.

    Plain SQL files contain raw SQL that may be fed to ``spark.sql()`` at
    runtime.  They are not valid Python so the AST-based analysis path is
    inapplicable; ``TriggerKB.detect()`` already handles non-Python text via
    its ``else`` branch (``_scan_sql`` + ``_run_detectors`` + regex call
    matching), so we route the file content there directly.

    Each match is emitted as a result dict compatible with the main pipeline's
    output (same keys used by ``print_results`` / JSON export).  No LLM call
    is made — the trigger KB provides exact-match, curated severity scores that
    are sufficient for SQL-construct detection.
    """
    results: list[dict] = []
    for file_path in sql_files:
        try:
            sql_text = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read SQL file %s: %s", file_path, exc)
            continue

        matches = trigger_kb.detect(sql_text)
        # Dedup by (rule_id, line), not rule_id alone: a construct can recur at
        # several distinct lines in one file (e.g. multiple multi-column NOT IN
        # sites), and each occurrence is a separate thing to fix. Collapsing by
        # rule_id would surface only the first and hide the rest. TriggerKB
        # already dedups exact (rule_id, line) repeats, so this only drops true
        # duplicates.
        seen_rules: set[tuple[str, int]] = set()
        for m in matches:
            risk = SEVERITY_SCORE.get(m.severity, 0.5)
            if risk < risk_threshold:
                continue
            dedup_key = (m.rule_id, m.line)
            if dedup_key in seen_rules:
                continue
            seen_rules.add(dedup_key)
            results.append({
                "file": str(file_path),
                "lines": f"{m.line}-{m.line}",
                "code": m.snippet or m.matched_token,
                "final_risk": risk,
                "root_cause": m.note,
                "explanation": m.note,
                "fix": m.fix or "",
                # Concrete remediation so the LLM fixer attempts the rewrite for
                # judgment-heavy SQL gaps the deterministic Phase-0.6 rewriter
                # left in place. Falls back to the note for any unmapped rule.
                "suggested_fixer_action": SQL_FIXER_ACTIONS.get(m.rule_id) or m.note,
                "kind": "standard",
                "confidence": "HIGH",
                "language": "sql",
                # Deterministic EWI code + status from the rule catalog.
                "ewi_code": m.ewi_code,
                "status_class": m.status_class,
            })
    return results


def _process_single_block(
    block: CodeBlock,
    scos_rag: BaseRAG,
    api_compat: dict[str, APIInfo],
    file_path: Path,
    similarity_threshold: float,
    safe_apis: set[str] | None = None,
) -> tuple[dict | None, dict | None]:
    """
    Process a single code block for compatibility analysis.

    SNOW-3347480: Accepts safe_apis allowlist; skips RAG query if all
    functions in the block are known-safe.

    Returns:
        Tuple of (rdd_result, block_to_analyze) where:
        - rdd_result: If block is RDD, contains the final result dict
        - block_to_analyze: If block needs LLM analysis, contains preliminary data
        Both can be None if block is SCOS compatible.
    """
    # Check for RDD usage first (always 100% risk)
    is_rdd, rdd_reason = has_rdd_usage(block.code)

    if is_rdd:
        # RDD operations are not supported - 100% risk, no LLM needed.
        # Emit a method-specific, actionable fix: convertible ops carry the
        # concrete DataFrame rewrite, no-equivalent ops flag a TODO. A single
        # generic string here gives the fixer nothing to apply.
        guidance = build_rdd_conversion_guidance(block.code)
        result = {
            "file": str(file_path),
            "lines": f"{block.line_start}-{block.line_end}",
            "code": block.code,
            "final_risk": 1.0,
            "root_cause": rdd_reason,
            "explanation": guidance["explanation"],
            "fix": guidance["fix"],
            "confidence": "HIGH",
            # Provenance: structural RDD detector, no LLM adjudication.
            "detected_by": "deterministic_rule",
            # convertible | no_equivalent | mixed — lets the fixer decide
            # between applying the rewrite and leaving a TODO.
            "rdd_class": guidance["rdd_class"],
        }
        # Only convertible/mixed blocks carry a concrete rewrite; a null here is
        # the signal that the op genuinely needs a manual TODO.
        if guidance["suggested_fixer_action"]:
            result["suggested_fixer_action"] = guidance["suggested_fixer_action"]
        if block.cell_id is not None:
            result["cell_id"] = block.cell_id
        result["language"] = block.language
        return (result, None)

    # SNOW-3390000: Unsupported ecosystem libraries and unrecognized third-party
    # imports are emitted DETERMINISTICALLY (no LLM adjudication), mirroring the
    # RDD short-circuit above. The LLM cannot reliably surface the long tail —
    # it talks a fabricated/unknown package down to 0.0 — so relying on it would
    # let unsupported code ship un-reviewed. A deterministic emit guarantees the
    # finding is always present: known ecosystem libs at high risk (forced
    # conversion), unrecognized imports as a review item.
    det_issues = (
        check_unsupported_ecosystem_libs(block.code)
        + check_unknown_third_party_imports(block.code)
    )
    if det_issues:
        top = max(det_issues, key=lambda i: i["risk"])
        result = {
            "file": str(file_path),
            "lines": f"{block.line_start}-{block.line_end}",
            "code": block.code,
            "final_risk": top["risk"],
            "root_cause": top["reason"],
            "explanation": top["reason"],
            "fix": top.get("how_to_fix"),
            "confidence": "HIGH" if top["risk"] >= 0.7 else "MEDIUM",
            "detected_by": "deterministic_rule",
            "category": top["category"],
        }
        if block.cell_id is not None:
            result["cell_id"] = block.cell_id
        result["language"] = block.language
        return (result, None)

    # SNOW: structural schema-validity detector (deterministic, certain). A
    # StructType with duplicate field names is rejected by Snowflake but uses
    # only "safe" APIs, so the token/KB detectors and safe-API allowlist would
    # skip the block entirely. Emit deterministically like the RDD/ecosystem
    # short-circuits above so it is always surfaced, no LLM adjudication.
    struct_issues = check_duplicate_struct_fields(block.code)
    if struct_issues:
        top = max(struct_issues, key=lambda i: i["risk"])
        result = {
            "file": str(file_path),
            "lines": f"{block.line_start}-{block.line_end}",
            "code": block.code,
            "final_risk": top["risk"],
            "root_cause": top["reason"],
            "explanation": top["reason"],
            "fix": top.get("how_to_fix"),
            "confidence": "HIGH",
            "detected_by": "deterministic_rule",
            "category": top["category"],
        }
        if block.cell_id is not None:
            result["cell_id"] = block.cell_id
        result["language"] = block.language
        return (result, None)

    # Check for unsupported Spark APIs (from Snowflake docs)
    api_issues = check_unsupported_apis(block.code)

    # Check for data source issues (unsupported formats, modes, options)
    datasource_issues = check_data_source_issues(block.code)

    # Check for Spark configs that are no-ops in SCOS
    config_issues = check_config_no_ops(block.code)

    # Check for UDF serialization issues (applyInPandas/mapInPandas)
    udf_issues = check_udf_serialization_issues(block.code)

    # SNOW-3347695: Check for per-property SparkContext access patterns
    spark_context_issues = check_spark_context_properties(block.code)

    # Check for legacy SQL/Hive entry points (sqlContext / SQLContext / HiveContext)
    legacy_entry_point_issues = check_legacy_entry_points(block.code)

    # SNOW-3347699: Check for Hadoop filesystem access patterns
    hadoop_issues = check_hadoop_patterns(block.code)

    # SNOW-3347693: Check for JVM-only library imports
    jvm_library_issues = check_jvm_library_imports(block.code)

    # SNOW-3319134: Check for ML pipeline patterns
    ml_pipeline_issues = check_ml_pipeline_patterns(block.code)

    # NOTE: unsupported ecosystem libraries (GraphFrames, Koalas, …) and
    # unrecognized third-party imports are handled by the deterministic
    # short-circuit near the top of this function — they never reach here.

    # SNOW-3319139: Check for UDTF/UDAF patterns
    udtf_udaf_issues = check_udtf_udaf_patterns(block.code)

    # SNOW-3319141: Check for Delta Lake patterns
    delta_lake_issues = check_delta_lake_patterns(block.code)

    # SNOW-3277715: Check for lazy view re-evaluation patterns
    view_reuse_issues = check_view_reuse_patterns(block.code)

    # SNOW-3256946, SNOW-3256947, SNOW-3256949, SNOW-3256948, SNOW-3256950:
    # Check for memory anti-patterns, known issues, case sensitivity, UDF config, performance
    memory_known_issues = check_memory_and_known_issues(block.code)

    # Combine all SCOS-specific issues
    scos_issues = (
        api_issues + datasource_issues + config_issues + udf_issues
        + spark_context_issues + legacy_entry_point_issues + hadoop_issues
        + jvm_library_issues + ml_pipeline_issues + udtf_udaf_issues
        + delta_lake_issues + view_reuse_issues + memory_known_issues
    )

    # SNOW-3347480: If no rule-based issues AND all functions are in the safe
    # allowlist, skip the RAG query entirely — this block is known-compatible.
    # A cross-lambda variable capture is invisible to a name-keyed allowlist —
    # every name involved can be safe while the nesting still diverges — so a
    # block carrying one never takes the fast path.
    if (
        not scos_issues
        and safe_apis
        and is_block_safe(block.functions, safe_apis)
        and not has_nested_lambda_capture(block.code)
    ):
        global _SAFE_API_SKIPS
        _SAFE_API_SKIPS += 1
        return None, None

    # Calculate max risk from SCOS issues
    scos_risk = max((issue["risk"] for issue in scos_issues), default=0)

    # Get unified RAG prediction - use normalized code for better matching
    prediction = scos_rag.predict_failure(block.normalized_code)

    # Collect candidates from unified RAG. When the backend is the offline
    # trigger KB, matches are EXACT (the named API/SQL construct literally
    # appears in the code) and ``score`` is a curated severity, not a cosine —
    # tag them so the LLM prompt frames them correctly rather than as fuzzy
    # "similar test cases".
    is_trigger = isinstance(scos_rag, SCOSTriggerRAG)
    candidates = []

    for p in prediction.get("similar_patterns", []):
        if p.root_cause:  # Only consider if it has a known issue
            candidates.append(
                {
                    "source": "TRIGGER_KB" if is_trigger else "UNIFIED_RAG",
                    "match_kind": "trigger_exact" if is_trigger else "rag_similar",
                    "severity": _severity_label(p.score),
                    "code": p.code,
                    "score": p.score,
                    "root_cause": p.root_cause,
                    "test_name": p.test_name,
                    "additional_notes": p.additional_notes,
                    # SNOW: structurally-decidable exact trigger (unsupported
                    # API / signature kwarg / attribute gateway). Lets the
                    # analyzer skip LLM adjudication for blocks whose only
                    # findings are decidable. Fuzzy matches are never decidable.
                    "decidable": getattr(p, "decidable", False),
                    # Deterministic EWI code + status from the rule catalog.
                    "ewi_code": getattr(p, "ewi_code", ""),
                    "status_class": getattr(p, "status_class", ""),
                }
            )

    # Sort by score descending, decidable matches first at equal score. The
    # top candidate becomes the finding's `root_cause`, and two rules anchored
    # on the same API can both fire with the same curated severity — in which
    # case the tie was previously broken by catalog order, letting a broad
    # token-presence rule speak for a site a structurally-decided rule had
    # identified exactly. Decidability is the stronger claim, so it wins.
    candidates.sort(key=lambda x: (x["score"], bool(x.get("decidable"))), reverse=True)

    # Filter candidates by similarity threshold. This threshold is a *cosine*
    # cutoff and only applies to fuzzy matches — an EXACT trigger match is real
    # regardless of its (severity) score, so it is never dropped here; the LLM
    # adjudicates its risk from the curated severity instead.
    candidates = [
        c
        for c in candidates
        if c.get("match_kind") == "trigger_exact" or c["score"] >= similarity_threshold
    ]

    # Condition-aware decidability: clear condition-unmet false positives and mark
    # condition-met triggers decidable so the block bypasses the COMPLETE pass.
    # The whole-file source lets named window variables defined outside this
    # block resolve (e.g. ``w = base.orderBy(...)`` used far below).
    candidates = apply_condition_resolution(
        candidates, block.code, assignment_source=_read_file_source_cached(file_path)
    )

    # Select top matches (up to 3)
    matching_patterns = []
    failure_likelihood = 0.0

    if candidates:
        # Best match sets the base likelihood
        best_match = candidates[0]
        failure_likelihood = best_match["score"]
        matching_patterns.append(best_match)

        for c in candidates[1:]:
            if len(matching_patterns) >= 3:
                break
            # Exact triggers are DISTINCT real issues — keep the top few
            # regardless of relative score. Fuzzy matches are only kept when
            # close (>=85%) to the best score, to avoid weak-neighbor noise.
            if c.get("match_kind") == "trigger_exact" or c["score"] >= failure_likelihood * 0.85:
                matching_patterns.append(c)

    # If no issues detected from any source and no matching patterns above threshold,
    # skip this code block - it's considered SCOS compatible
    if not scos_issues and not matching_patterns:
        return None, None

    # Get API compatibility for functions in this block
    func_compat = []
    min_compat_score = 1.0
    for func in block.functions:
        if func in api_compat:
            info = api_compat[func]
            func_compat.append(
                {
                    "name": func,
                    "compatibility": info.compatibility,
                    "score": info.score,
                    "supported": info.is_supported,
                }
            )
            if info.score is not None and info.score < min_compat_score:
                min_compat_score = info.score

    # Calculate preliminary risk from rule-based sources (all on 0-1 scale)
    api_risk = 1.0 - min_compat_score if min_compat_score < 1.0 else 0.0
    preliminary_risk = max(failure_likelihood, api_risk, scos_risk)

    # Prepare preliminary assessment for LLM
    preliminary_assessment = {
        "scos_issues": scos_issues,
        "scos_risk": scos_risk,
        "api_risk": api_risk,
        "func_compatibility": func_compat,
        "rag_similarity": failure_likelihood,
    }

    # Return block data for batch LLM processing
    return (
        None,
        {
            "block": block,
            "matching_patterns": matching_patterns,
            "preliminary_assessment": preliminary_assessment,
            "preliminary_risk": preliminary_risk,
            "min_compat_score": min_compat_score,
            "func_compat": func_compat,
            "scos_issues": scos_issues,
            "scos_risk": scos_risk,
            "failure_likelihood": failure_likelihood,
        },
    )


# Default number of parallel workers for block processing
DEFAULT_PARALLEL_WORKERS = 8


# SNOW-3347477: Three-phase architecture — Phase 1 & 2 (extract + batch search)
def prefetch_rag_queries(
    files: list[Path],
    scos_rag: BaseRAG,
    pyspark_methods: set[str],
    safe_apis: set[str] | None = None,
    parallel_workers: int = DEFAULT_PARALLEL_WORKERS,
) -> dict[str, int]:
    """
    Pre-warm the RAG cache by extracting all queries from all files and
    executing unique queries in parallel BEFORE per-file analysis begins.

    Three-phase architecture:
      Phase 1 (EXTRACT): Parse all files, extract code blocks, collect unique
        normalized queries. Skip blocks where all functions are in safe_apis.
      Phase 2 (SEARCH): Execute all unique queries via ThreadPoolExecutor.
        Results are stored in scos_rag._cache (via search_cached).
      Phase 3 (ANALYZE): Handled by analyze_files() — reads from pre-warmed cache.

    Args:
        files: List of file paths to analyze.
        scos_rag: RAG service (results cached in-memory via BaseRAG).
        pyspark_methods: Known PySpark method names for extraction.
        safe_apis: SNOW-3347480 allowlist to skip safe patterns.
        parallel_workers: Max concurrent Cortex Search queries.

    Returns:
        Stats dict with total_blocks, unique_queries, safe_skipped, errors.
    """
    import time as _time

    # --- Phase 1: EXTRACT (CPU-only, fast) ---
    phase1_start = _time.time()
    unique_queries: set[str] = set()
    total_blocks = 0
    safe_skipped = 0

    for file_path in files:
        blocks = extract_code_blocks(file_path, pyspark_methods)
        for block in blocks:
            total_blocks += 1
            # SNOW-3347480: Skip blocks where all functions are safe
            if (
                safe_apis
                and is_block_safe(block.functions, safe_apis)
                and not has_nested_lambda_capture(block.code)
            ):
                # Check RDD first — RDD blocks should not be skipped
                is_rdd, _ = has_rdd_usage(block.code)
                if not is_rdd:
                    safe_skipped += 1
                    continue
            unique_queries.add(block.normalized_code)

    phase1_time = _time.time() - phase1_start
    logger.info(
        "Phase 1 (extract): %d blocks from %d files, %d unique queries, %d safe-skipped (%.1fs)",
        total_blocks,
        len(files),
        len(unique_queries),
        safe_skipped,
        phase1_time,
    )

    if not unique_queries:
        return {
            "total_blocks": total_blocks,
            "unique_queries": 0,
            "safe_skipped": safe_skipped,
            "errors": 0,
        }

    # --- Phase 2: SEARCH (batch parallel, biggest win) ---
    phase2_start = _time.time()
    errors = 0

    def _search_one(query: str) -> str | None:
        """Execute a single search and let BaseRAG cache the result."""
        try:
            scos_rag.search_cached(query, limit=3)
            return None
        except Exception as exc:
            logger.warning("Prefetch query failed: %s", exc)
            return str(exc)

    # SNOW-3347477: Execute all unique queries in parallel
    # SNOW-3319329: Concurrency ramp — issue the first few queries serially
    # before fanning out. This is belt-and-suspenders with SCOSRemoteRAG's
    # warmup_on_init: it guarantees the Azure App Service has handled at
    # least a couple of requests sequentially before N=parallel_workers hit
    # it simultaneously, eliminating the cold-start timeout burst observed
    # in the RBI migration log.
    unique_list = list(unique_queries)
    ramp_size = min(2, len(unique_list))
    for q in unique_list[:ramp_size]:
        err = _search_one(q)
        if err is not None:
            errors += 1
    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        futures = {executor.submit(_search_one, q): q for q in unique_list[ramp_size:]}
        for future in as_completed(futures):
            err = future.result()
            if err is not None:
                errors += 1

    phase2_time = _time.time() - phase2_start
    logger.info(
        "Phase 2 (search): %d queries in %.1fs (%d errors), %d workers",
        len(unique_queries),
        phase2_time,
        errors,
        parallel_workers,
    )

    return {
        "total_blocks": total_blocks,
        "unique_queries": len(unique_queries),
        "safe_skipped": safe_skipped,
        "errors": errors,
    }


@dataclass
class AnalyzerConfig:
    """Tunables threaded through the per-file Phase 1 helpers.

    Bundling these keeps ``analyze_files`` and its helpers from re-typing
    the same parameters at every signature and call site. Defaults match what
    the CLI would set for a vanilla run.
    """

    risk_threshold: float = 0.3  # SNOW-3347466: raised from 0.1 to 0.3
    similarity_threshold: float = 0.55
    parallel_workers: int = DEFAULT_PARALLEL_WORKERS
    safe_apis: set[str] | None = None
    recipe_edits_all: dict[str, list[dict]] | None = None
    source_root: Path | None = None
    surface_unknown_apis: bool = True


_STDLIB_MODULE_NAMES: frozenset[str] = getattr(sys, "stdlib_module_names", frozenset())
_BUILTIN_NAMES: frozenset[str] = frozenset(vars(builtins))
_RECEIVER_RE = re.compile(r'\b([A-Za-z_]\w*)\.[A-Za-z_]\w*\s*\(')


def _build_covered_api_names(
    api_compat: dict,
    safe_apis: set[str] | None,
    kb_rules: list[dict],
) -> frozenset[str]:
    """Leaf method/function names already watched by the 4 detection sources."""
    names: set[str] = set()
    for api_name in api_compat:
        names.add(api_name.split(".")[-1].lower())
    for pattern in (safe_apis or ()):
        names.add(pattern.lower())
    for rule in kb_rules:
        for api in (rule.get("api") or []):
            names.add(api.split(".")[-1].lower())
    for collection in (
        RDD_EXCLUSIVE_METHODS, RDD_METHODS, RDD_NO_EQUIVALENT,
        DELTA_LAKE_PATTERNS, HADOOP_PATTERNS,
        UNSUPPORTED_IMPORTS, UNSUPPORTED_ECOSYSTEM_IMPORTS,
    ):
        for key in collection:
            names.add(key.split(".")[-1].lower())
    return frozenset(names)


def _build_file_import_map(source: str) -> dict[str, str]:
    """``{local_alias: top_level_module}`` for all imports in ``tree``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    result: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                result[local] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            for alias in node.names:
                result[alias.asname or alias.name] = top
    return result


def _collect_unknown_api_rows(
    file_path: Path,
    blocks: list,
    covered: frozenset[str],
    source: str,
) -> list[dict]:
    import_map = _build_file_import_map(source)
    per_module: dict[str, dict] = {}
    seen_names: set[str] = set()

    for block in blocks:
        for name in block.functions:
            lname = name.lower()
            if lname in covered or lname in seen_names:
                continue
            seen_names.add(lname)
            module = import_map.get(name) or import_map.get(lname)
            if not module or module in _STDLIB_MODULE_NAMES or module in _BUILTIN_NAMES:
                continue
            entry = per_module.setdefault(module, {
                "api_names": [],
                "rep_lines": f"{block.line_start}-{block.line_end}",
                "rep_code": block.code[:200],
            })
            entry["api_names"].append(name)

        for m in _RECEIVER_RE.finditer(block.code):
            alias = m.group(1)
            module = import_map.get(alias)
            if not module or module in _STDLIB_MODULE_NAMES or module in _BUILTIN_NAMES:
                continue
            if module not in per_module:
                per_module[module] = {
                    "api_names": [],
                    "rep_lines": f"{block.line_start}-{block.line_end}",
                    "rep_code": block.code[:200],
                }
            if alias not in per_module[module]["api_names"]:
                per_module[module]["api_names"].append(alias)

    return [
        {
            "file": str(file_path),
            "lines": data["rep_lines"],
            "code": data["rep_code"],
            "kind": "needs_classification",
            "detected_by": "unknown_surface_scan",
            "import_module": module,
            "api_names": data["api_names"],
            "confidence": "UNADJUDICATED",
            "adjudicated": False,
        }
        for module, data in per_module.items()
    ]


def _block_is_fully_decidable(item: dict) -> bool:
    """True when a block's findings can be emitted WITHOUT LLM adjudication.

    The bypass is deliberately conservative — it fires only when EVERY
    surviving candidate is a structurally-decidable exact trigger (an
    unsupported API, a signature kwarg violation, or an attribute gateway)
    and there are no soft rule-based ``scos_issues`` that would normally rely
    on the LLM to confirm-or-dismiss. A single non-decidable or fuzzy
    candidate sends the whole block to the LLM as before.

    Decidability is independent of severity: a LOW-severity unsupported API
    is still a certain true positive, so it is bypassed; conversely a
    behavioral HIGH-severity pattern that merely matched a token is NOT
    decidable and still goes to the LLM.
    """
    matching_patterns = item.get("matching_patterns") or []
    if not matching_patterns:
        return False
    if item.get("scos_issues"):
        return False
    return all(
        c.get("match_kind") == "trigger_exact" and c.get("decidable")
        for c in matching_patterns
    )


def _curated_fix(candidate: dict) -> str | None:
    """The catalog rule's own ``fix`` directive for a trigger candidate.

    The trigger KB flattens it into ``additional_notes`` as a ``"Fix: ..."``
    segment (alongside ``JIRA:`` / ``disposition:``), so every consumer that
    wants the concrete rewrite has to pull it back out of there.
    """
    for seg in (candidate.get("additional_notes") or "").split(" | "):
        if seg.startswith("Fix: "):
            return seg[len("Fix: "):]
    return None


def _build_decidable_result(
    file_path: Path, item: dict, risk_threshold: float
) -> dict | None:
    """Build a finished issue dict for a fully-decidable block, mirroring the
    shape ``_finalize_file_results`` produces for the LLM path. Returns None
    when the curated risk is below ``risk_threshold`` (treated as compatible).
    """
    block = item["block"]
    best = item["matching_patterns"][0]
    final_risk = best.get("score", 0.0)
    if final_risk < risk_threshold:
        return None
    root_cause = best.get("root_cause")
    # Surface the curated rule's `fix` directive (carried through the trigger KB
    # as a "Fix: ..." segment in additional_notes) so a decidable must-fix issue
    # hands the fixer a concrete rewrite instruction instead of None.
    fix_text = _curated_fix(best)
    result = {
        "file": str(file_path),
        "lines": f"{block.line_start}-{block.line_end}",
        "code": block.code,
        "final_risk": final_risk,
        "root_cause": root_cause,
        "explanation": f"Potential compatibility issue: {root_cause}",
        "fix": fix_text,
        "suggested_fixer_action": fix_text,
        # Decidable triggers are guaranteed divergences regardless of curated
        # severity, so confidence is HIGH even for LOW-severity rules.
        "confidence": "HIGH",
        # No recipe relationship (recipe-touched blocks are excluded from the
        # bypass), so the fixer routes this like any other fresh finding.
        "kind": "standard",
        # Provenance: condition-met static pass vs structurally-decidable exact
        # trigger (unsupported API / signature kwarg / attribute gateway). This
        # is the single provenance field — the former redundant "source":
        # "trigger_decidable" leg was dropped (no readers; == detected_by here).
        "detected_by": best.get("_detected_by", "decidable_trigger"),
        # Deterministic EWI code + status from the rule catalog.
        "ewi_code": best.get("ewi_code", ""),
        "status_class": best.get("status_class", ""),
    }
    if block.cell_id is not None:
        result["cell_id"] = block.cell_id
    result["language"] = block.language
    return result


def classify_block_certainty(
    item: dict, file_recipe_edits: list[dict]
) -> tuple[bool, str]:
    """THE routing decision — is a flagged block *certain* (emit deterministically,
    no LLM) or *uncertain* (needs LLM adjudication)?

    This is the single owner of the certain/uncertain routing, so no downstream
    layer re-decides it. It composes the certainty inputs that are computed
    upstream at match time:
      * per-rule decidability (``trigger_kb._rule_decidable`` → each candidate's
        ``decidable`` flag) and the condition pass (``apply_condition_resolution``),
        both read here via the candidate ``match_kind`` / ``decidable`` flags;
      * per-block structural decidability (``_block_is_fully_decidable``);
      * Phase 0.5 recipe touch — a recipe-touched block is kept UNCERTAIN so the
        LLM can still propose a ``suggested_fixer_action`` for annotate-only recipes.

    Returns ``(is_certain, reason)``; ``reason`` is provenance for logs.
    """
    block = item["block"]
    if _recipe_edits_for_block(file_recipe_edits, block.line_start, block.line_end):
        return False, "recipe-touched (LLM proposes suggested_fixer_action)"
    if not _block_is_fully_decidable(item):
        return False, "non-decidable or fuzzy candidate present"
    return True, "fully-decidable exact trigger(s)"


def _build_deferred_result(file_path: Path, item: dict) -> dict:
    """Build a NON-adjudicated finding for the adjudicator/fixer to confirm-or-dismiss.

    Unlike ``_build_decidable_result`` this does NOT apply the risk threshold —
    the whole point is that the analyzer is no longer the arbiter, so every
    surviving trigger match is passed through for the Phase 1.1b adjudicator and
    Phase-2 fixer to judge. The raw candidate matches are attached as
    ``deferred_candidates`` so they have the same KB context (rule_id / anchor /
    root_cause / EWI) the analyzer LLM
    would otherwise have consumed.
    """
    block = item["block"]
    patterns = item.get("matching_patterns") or []
    best = patterns[0] if patterns else {}
    result = {
        "file": str(file_path),
        "lines": f"{block.line_start}-{block.line_end}",
        "code": block.code,
        # Preliminary KB score as a PRIOR only — not an adjudicated risk.
        # ``adjudicated: False`` marks that no model has confirmed this yet.
        "final_risk": best.get("score", 0.0),
        "root_cause": best.get("root_cause"),
        "explanation": (
            "Unadjudicated trigger match deferred to the fixer: "
            f"{best.get('root_cause')}"
        ),
        # The curated remedy, when the top candidate has one. A rule's `fix` is
        # the only place a concrete rewrite lives; dropping it here left the
        # fixer holding a diagnosis with no instruction, which reads as "the
        # catalog has no remedy for this" and produces a needless TODO.
        "fix": _curated_fix(best),
        "confidence": "UNADJUDICATED",
        # Routes the Phase-2 fixer to confirm-or-dismiss then fix.
        "kind": "needs_adjudication",
        "source": "deferred_adjudication",
        "detected_by": "deferred_to_fixer",
        "adjudicated": False,
        "ewi_code": best.get("ewi_code", ""),
        "status_class": best.get("status_class", ""),
        "deferred_candidates": [
            {
                # Trigger-KB candidates carry the rule id as `test_name`;
                # `rule_id` was always absent here, so every deferred candidate
                # reported `"rule_id": null` and could not be traced back.
                "rule_id": c.get("rule_id") or c.get("test_name"),
                "anchor": c.get("anchor"),
                "match_kind": c.get("match_kind"),
                "decidable": c.get("decidable"),
                "root_cause": c.get("root_cause"),
                "fix": _curated_fix(c),
                "ewi_code": c.get("ewi_code"),
                "status_class": c.get("status_class"),
                "score": c.get("score"),
            }
            for c in patterns
        ],
    }
    if block.cell_id is not None:
        result["cell_id"] = block.cell_id
    result["language"] = block.language
    return result



def _partition_decidable_blocks(
    blocks_to_analyze: list[dict],
    file_recipe_edits: list[dict],
    file_path: Path,
    risk_threshold: float,
) -> tuple[list[dict], list[dict]]:
    """Turn a file's flagged blocks into finished findings — no LLM, ever.

    Fully-decidable triggers (unsupported APIs, signature kwarg violations,
    attribute gateways) are emitted via ``_build_decidable_result`` (risk-gated).
    Every uncertain or Phase-0.5-recipe-touched block is emitted as a
    ``needs_adjudication`` finding (``_build_deferred_result``) for the Phase 1.1
    adjudicator to confirm-or-dismiss and the Phase-2 fixer to implement.

    Returns ``(results, [])``. The empty second element is retained for
    call-site compatibility — there is no LLM batch path, so nothing is ever
    left "remaining".
    """
    results: list[dict] = []
    for item in blocks_to_analyze:
        is_certain, _reason = classify_block_certainty(item, file_recipe_edits)
        if not is_certain:
            # No Cortex round-trip — hand the raw trigger match to the Phase 1.1
            # adjudicator / Phase-2 fixer for confirm-or-dismiss-and-fix.
            results.append(_build_deferred_result(file_path, item))
            continue
        result = _build_decidable_result(file_path, item, risk_threshold)
        if result is not None:
            results.append(result)
        # else: below threshold — treated as compatible, dropped silently.
    return results, []


def _collect_file_llm_inputs(
    scos_rag: BaseRAG,
    api_compat: dict[str, APIInfo],
    pyspark_methods: set[str],
    file_path: Path,
    config: AnalyzerConfig,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Phase 1: extract blocks and route every flagged block to a finding.

    Returns ``(early_results, blocks_to_analyze, file_recipe_edits)``:
      * ``early_results`` — all finished findings for this file: RDD blocks,
        fully-decidable triggers, and deferred ``needs_adjudication`` blocks
      * ``blocks_to_analyze`` — always empty (retained for call-site
        compatibility; there is no LLM verdict step)
      * ``file_recipe_edits`` — Phase 0.5 recipe edits scoped to this file

    Cheap and fully offline (trigger-KB lookups / pre-warmed RAG cache); the
    analyzer makes no ``CORTEX.COMPLETE`` calls.
    """
    early_results: list[dict] = []
    blocks = extract_code_blocks(file_path, pyspark_methods)
    if not blocks:
        return early_results, [], []

    # Resolve which recipe edits (if any) Phase 0.5 made to this file.
    file_recipe_edits = _recipe_edits_for_file(
        config.recipe_edits_all, file_path, config.source_root
    )

    blocks_to_analyze: list[dict] = []
    with ThreadPoolExecutor(max_workers=config.parallel_workers) as executor:
        future_to_block = {
            executor.submit(
                _process_single_block,
                block,
                scos_rag,
                api_compat,
                file_path,
                config.similarity_threshold,
                config.safe_apis,  # SNOW-3347480: Pass allowlist
            ): block
            for block in blocks
        }

        for future in as_completed(future_to_block):
            block = future_to_block[future]
            try:
                rdd_result, block_data = future.result()

                if rdd_result is not None:
                    # RDD block - resolved without the LLM.
                    early_results.append(rdd_result)
                elif block_data is not None:
                    # Block needs LLM analysis.
                    blocks_to_analyze.append(block_data)
                # else: block is SCOS compatible, skip it

            except Exception as e:
                logger.error(
                    f"Error processing block at lines {block.line_start}-{block.line_end}: {e}"
                )
                raise

    # SNOW: Decidability gate — emit structurally-certain trigger findings
    # (unsupported APIs, signature kwarg violations, attribute gateways)
    # without an LLM round-trip. Only fully-decidable, non-recipe blocks are
    # bypassed (added to early_results); everything else stays on the LLM path.
    decidable_results, blocks_to_analyze = _partition_decidable_blocks(
        blocks_to_analyze, file_recipe_edits, file_path, config.risk_threshold
    )
    early_results.extend(decidable_results)

    return early_results, blocks, file_recipe_edits




def _finalize_file_results(
    file_path: Path,
    blocks_to_analyze: list[dict],
    llm_results: dict,
    file_recipe_edits: list[dict],
    early_results: list[dict],
    config: AnalyzerConfig,
) -> list[dict]:
    """Phase 3: combine LLM verdicts with preliminary data into final findings.

    ``early_results`` (e.g. RDD blocks resolved in Phase 1) seed the output so the
    deterministic recipe-fallback tagging below applies to them too.
    """
    results = list(early_results)

    for item in blocks_to_analyze:
        block = item["block"]
        block_id = f"{block.line_start}-{block.line_end}"
        matching_patterns = item["matching_patterns"]
        preliminary_risk = item["preliminary_risk"]
        min_compat_score = item["min_compat_score"]
        func_compat = item["func_compat"]
        scos_issues = item["scos_issues"]
        scos_risk = item["scos_risk"]
        failure_likelihood = item["failure_likelihood"]

        # Get LLM result for this block
        llm_result = llm_results.get(block_id)

        final_risk = preliminary_risk  # Default to preliminary if LLM fails
        root_cause = None
        how_to_fix = None

        if llm_result:
            # LLM determines the final risk
            final_risk = llm_result.get("final_risk", preliminary_risk)
            root_cause = llm_result.get("root_cause")
            how_to_fix = llm_result.get("fix")

        # Fall back to rule-based root cause if LLM didn't provide one
        if not root_cause:
            if matching_patterns:
                best = matching_patterns[0]
                root_cause = best.get("root_cause")

            # If SCOS issues have higher risk, use their info
            if scos_issues and scos_risk >= failure_likelihood:
                top_issue = max(scos_issues, key=lambda x: x["risk"])
                root_cause = root_cause or top_issue["reason"]
                how_to_fix = how_to_fix or top_issue.get("how_to_fix")

        # Only report if final risk is above threshold
        if final_risk >= config.risk_threshold:
            explanation = (
                llm_result.get("explanation")
                if llm_result
                else f"Potential compatibility issue: {root_cause}"
            )
            confidence = (
                llm_result.get("confidence")
                if llm_result
                else ("HIGH" if final_risk >= 0.9 else "MEDIUM")
            )

            result = {
                "file": str(file_path),
                "lines": f"{block.line_start}-{block.line_end}",
                "code": block.code,
                "final_risk": final_risk,
                "root_cause": root_cause,
                "explanation": explanation,
                "fix": how_to_fix,
                "confidence": confidence,
                # Provenance: an LLM verdict came back for this block -> "llm".
                # Otherwise the block reached the batch but the LLM produced no
                # result, so the reported finding is the rule-based fallback.
                "detected_by": "llm" if llm_result else "deterministic_rule",
                # Deterministic EWI code + status from the best matching rule.
                "ewi_code": matching_patterns[0].get("ewi_code", "") if matching_patterns else "",
                "status_class": matching_patterns[0].get("status_class", "") if matching_patterns else "",
            }
            if block.cell_id is not None:
                result["cell_id"] = block.cell_id
            result["language"] = block.language

            # Phase 0.5 recipe-context fields (optional; absent on older runs
            # without --recipe-edits or when the LLM response predates the
            # recipe-aware schema).
            if llm_result:
                for k in (
                    "kind",
                    "recipe_id",
                    "suggested_fixer_action",
                    "suggested_recipe_id",
                ):
                    v = llm_result.get(k)
                    if v not in (None, ""):
                        result[k] = v

            results.append(result)

    # Deterministic fallback: if the LLM ignored the recipe-aware schema (or
    # the issue came from a rule-based early-return path that never reaches
    # the LLM), tag `kind`/`recipe_id` from the recipes that actually fired
    # on the block.  This guarantees downstream consumers (the fixer agent,
    # dashboards) get consistent tagging regardless of LLM compliance.  We
    # never overwrite an LLM-provided `kind`; we only fill in missing values.
    for result in results:
        try:
            ls, le = (int(x) for x in str(result.get("lines", "")).split("-"))
        except (ValueError, AttributeError):
            ls = le = None
        block_recipe_edits = (
            _recipe_edits_for_block(file_recipe_edits, ls, le)
            if ls is not None
            else []
        )
        if "kind" not in result:
            if block_recipe_edits:
                rewrite_edits = [
                    e
                    for e in block_recipe_edits
                    if _classify_recipe_kind(e["recipe_id"]) == "rewrite"
                ]
                annotate_edits = [
                    e
                    for e in block_recipe_edits
                    if _classify_recipe_kind(e["recipe_id"])
                    in ("annotate", "comment")
                ]
                if rewrite_edits:
                    result["kind"] = "recipe_validated"
                    result.setdefault("recipe_id", rewrite_edits[0]["recipe_id"])
                elif annotate_edits:
                    result["kind"] = "recipe_incomplete"
                    result.setdefault("recipe_id", annotate_edits[0]["recipe_id"])
                else:
                    result["kind"] = "standard"
            else:
                result["kind"] = "standard"
        elif (
            "recipe_id" not in result
            and result["kind"] in ("recipe_validated", "recipe_incomplete")
            and block_recipe_edits
        ):
            result["recipe_id"] = block_recipe_edits[0]["recipe_id"]

    return results


def analyze_files(
    scos_rag: BaseRAG,
    api_compat: dict[str, APIInfo],
    pyspark_methods: set[str],
    files: list[Path],
    config: AnalyzerConfig,
) -> list[dict]:
    """Analyze multiple files — fully deterministic, no LLM.

    Phase 1 (block extraction + preliminary scoring) runs per file — it is cheap
    (offline trigger KB / pre-warmed RAG cache). Fully-decidable triggers are
    emitted directly; every uncertain / recipe-touched block is emitted as a
    ``needs_adjudication`` finding for the Phase 1.1b adjudicator + Phase-2 fixer.
    The analyzer makes NO ``CORTEX.COMPLETE`` calls.
    """
    kb_rules = scos_rag.kb.rules if isinstance(scos_rag, SCOSTriggerRAG) else []
    covered_api_names = (
        _build_covered_api_names(api_compat, config.safe_apis, kb_rules)
        if config.surface_unknown_apis
        else None
    )

    all_results: list[dict] = []
    for file_path in files:
        logger.info(f"  📄 {file_path.name}")
        early_results, file_blocks, file_recipe_edits = _collect_file_llm_inputs(
            scos_rag,
            api_compat,
            pyspark_methods,
            file_path,
            config,
        )
        all_results.extend(
            _finalize_file_results(
                file_path,
                [],
                {},
                file_recipe_edits,
                early_results,
                config,
            )
        )
        if covered_api_names is not None and file_blocks:
            all_results.extend(
                _collect_unknown_api_rows(
                    file_path, file_blocks, covered_api_names,
                    _read_file_source_cached(file_path) or "",
                )
            )
    return all_results


def print_json_results(results: list[dict]):
    """Print analysis results in JSON format."""
    print(json.dumps(results, indent=2))


def print_results(results: list[dict]):
    """Print analysis results."""
    if not results:
        print("\n✅ No potential issues found above threshold.")
        return

    print("\n" + "=" * 80)
    print("ANALYSIS RESULTS")
    print("=" * 80)
    print(f"Code blocks analyzed with potential issues: {len(results)}")

    for r in results:
        final_risk = r["final_risk"]
        # Choose icon based on risk
        if final_risk >= 0.7:
            risk_icon = "🔴"
        elif final_risk >= 0.3:
            risk_icon = "🟡"
        else:
            risk_icon = "🟢"

        print(f"\n{'-' * 80}")
        print(
            f"{risk_icon} {r['file']}:{r['lines']} - Final Risk: {final_risk * 100:.1f}%"
        )
        print(f"   Code: {r['code']}")

        if r.get("root_cause"):
            print(f"   Root Cause: {r['root_cause']}")

        if r.get("explanation"):
            print(f"   Explanation: {r['explanation']}")

        if r.get("fix"):
            print(f"   Fix: {r['fix']}")

        if r.get("confidence"):
            print(f"   Confidence: {r['confidence']}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze PySpark scripts for SCOS compatibility issues"
    )
    parser.add_argument(
        "--path",
        type=str,
        required=True,
        help="Path to a PySpark file or directory containing PySpark files",
    )
    add_connectivity_args(parser)
    parser.add_argument(
        "--risk-threshold",
        "-t",
        type=float,
        default=0.3,  # SNOW-3347466: Raised from 0.1 to 0.3 to filter noisy informational EWIs
        help="Minimum risk (0-1) to report (default: 0.3 = 30%%)",
    )
    parser.add_argument(
        "--include-informational",  # SNOW-3347466: New flag to include all issues regardless of threshold
        action="store_true",
        default=False,
        help="Include all issues regardless of risk threshold (overrides --risk-threshold to 0.0)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize the RAG services and load CSV data",
    )
    parser.add_argument(
        "--similarity-threshold",
        "-s",
        type=float,
        default=0.55,
        help="Minimum cosine similarity [-1.0, 1.0] to consider RAG patterns relevant (default: 0.55)",
    )
    parser.add_argument(
        "--parallel-workers",
        "-p",
        type=int,
        default=DEFAULT_PARALLEL_WORKERS,
        help=f"Number of parallel workers for block processing (default: {DEFAULT_PARALLEL_WORKERS})",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format for console output (default: text)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Path to write the analysis results as a JSON array (e.g. "
            "<CONVERSION>/analysis.json). When set, the full results are "
            "written to this file directly and stdout only carries a short "
            "confirmation line — downstream steps must read this file rather "
            "than parse console output. Parent directories are created as "
            "needed."
        ),
    )
    parser.add_argument(
        "--notebook-index",
        type=str,
        default=None,
        help=(
            "Optional path to migration_state.json; when provided, the "
            "notebook_index stored there is used to skip per-candidate "
            "notebook-detection I/O when walking the workload."
        ),
    )
    parser.add_argument(
        "--recipe-edits",
        type=str,
        default=None,
        help=(
            "Optional path to a JSON file describing Phase 0.5 recipe edits. "
            "Accepts either a `migration_state.json` (the `recipe_edits` key "
            "is extracted) or a standalone JSON object shaped like "
            "{\"<rel/path.py>\": [{\"recipe_id\": ..., \"src_line\": ...}, ...]}. "
            "Loaded edits are injected per-block as grounding so each issue is "
            "tiered by `kind` (recipe_validated / recipe_incomplete / "
            "recipe_adjacent / standard)."
        ),
    )
    parser.add_argument(
        "--defer-adjudication",
        action="store_true",
        default=False,
        help=(
            "Accepted for orchestrator compatibility and IGNORED — "
            "defer-adjudication is the only mode. The analyzer never calls "
            "SNOWFLAKE.CORTEX.COMPLETE; non-decidable (and recipe-touched) blocks "
            "are emitted as `needs_adjudication` for the Phase 1.1b adjudicator + "
            "Phase-2 fixer."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    args = _parse_args(argv)

    # SNOW-3347466: Override risk threshold when --include-informational is used
    if args.include_informational:
        args.risk_threshold = 0.0

    # Configure logging to stderr so it doesn't interfere with stdout (text/json) output
    # Set root logger to WARNING to suppress noisy library logs
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)

    # Set this script's logger to INFO to see our own messages
    logger.setLevel(logging.INFO)

    path = Path(args.path).expanduser()
    if not path.exists():
        logger.error(f"Error: Path does not exist: {path}")
        sys.exit(1)

    # Fail fast on an unwritable --output BEFORE running the (billable, slow)
    # analysis. Otherwise a PermissionError on the final write would discard
    # every result after the LLM/RAG work has already been paid for.
    output_path: Path | None = None
    if args.output:
        output_path = Path(args.output).expanduser()
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Error: cannot create output directory {output_path.parent}: {e}")
            sys.exit(1)
        # Existing file must be writable; otherwise the parent dir must be.
        if output_path.exists():
            if not os.access(output_path, os.W_OK):
                logger.error(f"Error: output file {output_path} is not writable")
                sys.exit(1)
        elif not os.access(output_path.parent, os.W_OK):
            logger.error(f"Error: output directory {output_path.parent} is not writable")
            sys.exit(1)

    notebook_index: dict[str, dict] | None = None
    if args.notebook_index:
        try:
            with open(args.notebook_index, "r", encoding="utf-8") as f:
                state = json.load(f)
            raw_index = state.get("notebook_index") or {}
            if isinstance(raw_index, dict):
                notebook_index = raw_index
                logger.info(
                    f"Loaded notebook_index with {len(notebook_index)} entries from {args.notebook_index}"
                )
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Could not load notebook_index from {args.notebook_index}: {e}")

    # Load Phase 0.5 recipe edits separately via --recipe-edits.  The file
    # may be either a `migration_state.json` (recipe_edits is extracted from
    # the top-level key) or a standalone JSON object shaped like
    # `{ "<rel/path.py>": [ {recipe_id, src_line, ...}, ... ] }`.
    recipe_edits_all: dict[str, list[dict]] | None = None
    if args.recipe_edits:
        try:
            with open(args.recipe_edits, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and "recipe_edits" in raw and isinstance(raw["recipe_edits"], dict):
                raw_recipes = raw["recipe_edits"]
                source_shape = "migration_state.json"
            elif isinstance(raw, dict):
                raw_recipes = raw
                source_shape = "standalone recipe_edits map"
            else:
                raw_recipes = {}
                source_shape = "unrecognized (expected dict)"
            if raw_recipes:
                recipe_edits_all = raw_recipes
                total_edits = sum(len(v) for v in raw_recipes.values() if isinstance(v, list))
                logger.info(
                    f"Loaded recipe_edits for {len(raw_recipes)} file(s) "
                    f"({total_edits} edit(s)) from {args.recipe_edits} [{source_shape}]"
                )
            else:
                logger.info(
                    f"--recipe-edits {args.recipe_edits} contained no edits "
                    f"[{source_shape}]; proceeding without recipe grounding."
                )
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Could not load recipe_edits from {args.recipe_edits}: {e}")

    # Find PySpark files
    files = find_pyspark_files(path, notebook_index=notebook_index)
    logger.info(f"Found {len(files)} Python file(s) to analyze")

    # Find plain SQL files (not Databricks native-JSON notebooks).
    # These are excluded from find_pyspark_files because they are not valid
    # Python; route them through the trigger-KB SQL path instead.
    plain_sql_files = find_plain_sql_files(path)
    if plain_sql_files:
        logger.info(f"Found {len(plain_sql_files)} plain SQL file(s) to analyze")

    # Load API compatibility data
    compat_csv = DATA_DIR / "api_compatibility.csv"
    logger.info(f"\nLoading API compatibility data from {compat_csv}...")
    api_compat, pyspark_methods = load_api_compatibility(compat_csv)
    logger.info(
        f"Loaded {len(api_compat)} API entries, {len(pyspark_methods)} methods/functions"
    )

    # SNOW-3347480: Load safe-API allowlist
    safe_apis = load_safe_apis()

    # Drop any API that also has a KB divergence rule (e.g. groupBy, join) —
    # see reconcile_safe_apis.
    safe_apis = reconcile_safe_apis(safe_apis, load_kb_anchor_leaves())

    # Connect to Snowflake (for the RAG backend only — the analyzer makes NO
    # CORTEX.COMPLETE calls, so there is no LLM preflight).
    # The trigger backend is fully offline — no session needed.
    session = None
    if args.rag_backend != "trigger":
        session = open_session(args.connection)

    # Initialize RAG backend
    scos_rag: BaseRAG = build_rag(session, args.rag_backend)

    # Load data if --init flag is set (only applicable for cortex backend)
    if args.rag_backend == "cortex" and args.init:
        scos_rag.init()
        logger.info("Loading SCOS RAG data from data/...")
        total_count = 0

        rag_files = [
            "df_test_rca_normalized.csv",
            "sql_test_rca_normalized.csv",
            "expectation_tests_xfail_rca_normalized.csv",
            "jira_rca_normalized.csv",
            # SNOW-3347463: Databricks compatibility patterns
            "dbx_compat_rca_normalized.csv",
            # SNOW-3319145: ML, UDTF/UDAF, and Delta Lake patterns
            "ml_compat_rca_normalized.csv",
            "udtf_udaf_compat_rca_normalized.csv",
            "delta_lake_compat_rca_normalized.csv",
        ]
        for csv_file in rag_files:
            count = scos_rag.upload_csv(csv_file)
            logger.info(f"  Loaded {count} records from {csv_file}")
            total_count += count

        logger.info(f"  Total: {total_count} failure records loaded")

    # Analyze files
    logger.info(
        f"\nAnalyzing files (risk threshold: {args.risk_threshold * 100:.2f}%, similarity: {args.similarity_threshold}, workers: {args.parallel_workers})..."
    )

    # SNOW-3347477: Phase 1 & 2 — Extract all queries and pre-warm the RAG cache
    # in parallel BEFORE per-file analysis (Phase 3).
    if len(files) > 1:
        logger.info("\n--- Three-phase RAG pipeline (SNOW-3347477) ---")
        prefetch_stats = prefetch_rag_queries(
            files,
            scos_rag,
            pyspark_methods,
            safe_apis=safe_apis,
            parallel_workers=args.parallel_workers,
        )
        logger.info(
            "Prefetch complete: %d unique queries cached, %d safe-API skips",
            prefetch_stats["unique_queries"],
            prefetch_stats["safe_skipped"],
        )

    # Phase 3: Analysis (RAG calls hit the pre-warmed cache). `source_root=path`
    # resolves each file's relpath when looking it up in `recipe_edits_all`.
    config = AnalyzerConfig(
        risk_threshold=args.risk_threshold,
        similarity_threshold=args.similarity_threshold,
        parallel_workers=args.parallel_workers,
        safe_apis=safe_apis,
        recipe_edits_all=recipe_edits_all,
        source_root=path,
    )
    all_results = analyze_files(
        scos_rag,
        api_compat,
        pyspark_methods,
        files,
        config,
    )

    # Analyze plain SQL files through the trigger KB.
    # ``TriggerKB`` is always available regardless of the RAG backend because
    # the SQL path does not use the LLM — it is pure exact-match detection.
    if plain_sql_files:
        sql_trigger_kb = (
            scos_rag.kb if isinstance(scos_rag, SCOSTriggerRAG) else TriggerKB.load()
        )
        sql_results = analyze_plain_sql_files(
            plain_sql_files, sql_trigger_kb, args.risk_threshold
        )
        if sql_results:
            logger.info(
                "Found %d issue(s) in %d plain SQL file(s)",
                len(sql_results),
                len(plain_sql_files),
            )
        all_results.extend(sql_results)

    # Sort by final risk (highest first), then within same risk by descending
    # line number within each file — this ensures the fixer can insert comments
    # bottom-up without earlier insertions shifting subsequent line targets.
    def _sort_key(x):
        try:
            line_start = int(str(x.get("lines", "0")).split("-")[0])
        except (ValueError, AttributeError):
            line_start = 0
        # Primary: risk descending (negate for ascending sort → higher first)
        # Secondary within same file+risk: line descending (negate)
        return (-x.get("final_risk", 0), x.get("file", ""), -line_start)

    all_results = sorted(all_results, key=_sort_key)

    # Recipe-edit linkage summary (visible whenever --recipe-edits was used).
    if recipe_edits_all is not None:
        total_edit_keys = len(recipe_edits_all)
        matched_files = _RECIPE_LINKAGE_STATS["files_with_edits"]
        unmatched_files = _RECIPE_LINKAGE_STATS["files_canonicalization_failed"]
        logger.info(
            "Recipe-edit linkage: %d/%d files in recipe_edits matched an analyzed file; "
            "%d analyzed file(s) had Phase 0.5 edits, %d had none, %d failed path canonicalization.",
            matched_files,
            total_edit_keys,
            matched_files,
            _RECIPE_LINKAGE_STATS["files_without_edits"],
            unmatched_files,
        )
        if unmatched_files:
            logger.warning(
                "Recipe-edit linkage: %d file(s) could not be canonicalized against source_root=%s "
                "and their recipe edits were ignored.  First few: %s",
                unmatched_files,
                path,
                _RECIPE_LINKAGE_FAILED_PATHS[:5],
            )

    # SNOW-3347479: Log RAG cache statistics
    scos_rag.log_cache_stats()

    # SNOW-3347480: Log safe-API skip statistics
    total_blocks_processed = _SAFE_API_SKIPS + (scos_rag.cache_stats["hits"] + scos_rag.cache_stats["misses"])
    if total_blocks_processed > 0:
        logger.info(
            "Skipped %d safe-API queries (%.1f%% of total)",
            _SAFE_API_SKIPS,
            _SAFE_API_SKIPS / (total_blocks_processed + _SAFE_API_SKIPS) * 100,
        )

    # Write results.  When --output is given, the analyzer itself writes the
    # canonical analysis.json so downstream steps read a file rather than
    # re-serializing console output (which is lossy / hallucination-prone when
    # an LLM is in the loop). stdout then carries only a confirmation line.
    # Writability was preflighted at startup; if the write still fails here
    # (disk full, races, revoked perms), fall back to stdout rather than
    # silently discarding the just-computed results, and exit non-zero.
    if output_path is not None:
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(all_results, f, indent=2)
        except OSError as e:
            logger.error(f"Failed to write results to {output_path}: {e}")
            logger.error("Emitting results to stdout instead so they are not lost.")
            print_json_results(all_results)
            if session:
                session.close()
            sys.exit(1)
        logger.info(f"Wrote {len(all_results)} issue(s) to {output_path}")
        print(f"Analysis complete: {len(all_results)} issue(s) written to {output_path}")
    elif args.output_format == "json":
        print_json_results(all_results)
    else:
        print_results(all_results)

    # Cleanup
    if session:
        session.close()


if __name__ == "__main__":
    main()
