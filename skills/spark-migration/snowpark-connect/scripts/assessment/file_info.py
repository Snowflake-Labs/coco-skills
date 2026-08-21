"""File Information table — per-file data-flow & governance rollup.

Renders one row per code file capturing *local* I/O and Snowflake-hosting
prerequisites. Complements the Per-File Compatibility table (which answers
"how hard is this file to migrate") by answering "what does this file touch
and what infrastructure does it need to run in Snowflake".

Design principles:

* **Local-only** — no DAG inheritance. If ``flatten_columns.py`` operates
  purely on DataFrames, we say so; we don't propagate a lineage label from
  an upstream ingestor. Pipeline lineage lives in the visual data DAG.
* **Specific source names** — ``S3``, ``JDBC (PostgreSQL)``, ``REST API``
  rather than the vague ``"External"``. If we don't know, we say
  ``"DataFrame"`` for pure logic and ``""`` for truly unknown.
* **Three independent Snowflake-hosting flags**:
    - ``eai_required``: does this file make out-of-band network calls that
      Snowflake's default firewall would block?
    - ``ar_required``: does this file import Python packages that aren't
      in Snowflake's built-in Anaconda channel?
    - Both are Python-specific and derived by AST inspection; Scala/Java
      files get ``"N/A"`` for AR.

Public API:

    populate_file_info(code_files, workload_dir, migration_scope_roots) -> list[FileInfoRow]
    classify_library(name, internal_modules) -> tuple[bool, str]
    is_migration_scope(name) -> bool

Called from :func:`scan_codebase.scan` after the per-file walk collects
``imports``, ``data_urls``, ``data_formats`` for each file.
"""
from __future__ import annotations

import ast
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category B: migration-scope libraries (excluded from AR check).
#
# These are libraries the migration tool ITSELF rewrites away — pyspark,
# Databricks utilities, Delta Lake, and their ecosystem. Post-migration
# they either don't exist (rewritten to Snowpark equivalents) or are
# supplied by Snowflake's runtime; asking "does this need AR?" would
# falsely flag every file that touched Spark.
#
# Match rule: an import root or its dotted prefix is in this set.
#   Example: ``pyspark.sql.functions`` matches ``pyspark`` here.
# ---------------------------------------------------------------------------

_MIGRATION_SCOPE: frozenset[str] = frozenset({
    # --- PySpark and all submodules ---
    "pyspark",
    "pyspark.sql",
    "pyspark.sql.functions",
    "pyspark.sql.types",
    "pyspark.sql.window",
    "pyspark.sql.connect",
    "pyspark.sql.streaming",
    "pyspark.sql.avro",
    "pyspark.sql.protobuf",
    "pyspark.ml",
    "pyspark.mllib",
    "pyspark.streaming",
    "pyspark.rdd",
    "pyspark.pandas",
    "pyspark.resource",
    "pyspark.broadcast",
    "pyspark.accumulators",
    "pyspark.serializers",
    "pyspark.files",
    "pyspark.status",
    "pyspark.storagelevel",
    "pyspark.taskcontext",

    # --- Databricks utilities / SDK / connectors ---
    "dbutils",  # the notebook global (rewritten to Snowflake equivalents)
    "databricks",
    "databricks.sdk",
    "databricks.connect",
    "databricks.sql",           # databricks-sql-connector
    "databricks.feature_store",
    "databricks.feature_engineering",
    "databricks.koalas",
    "databricks.automl",
    "databricks.vector_search",
    "databricks_api",
    "databricks_cli",

    # --- Delta Lake (fully replaced by native Snowflake tables) ---
    "delta",
    "delta.tables",
    "delta.pip_utils",
    "deltalake",
    "delta_sharing",

    # --- Koalas (predecessor of pyspark.pandas) ---
    "koalas",

    # --- Spark extension libs (Java/Scala originals, sometimes wrapped in Python) ---
    "spark_xml",         # spark-xml
    "spark_avro",        # spark-avro (bundled into pyspark 3.x)
    "spark_nlp",         # spark-nlp
    "sparknlp",          # spark-nlp Python entry
    "graphframes",
    "spark_tensorflow_distributor",
    "spark_tensorflow_connector",
    "spark_deep_learning",
    "petastorm",         # Uber's Parquet-Spark connector
    "horovod",           # distributed training on Spark

    # --- Hive/HDFS bridges typically used only with Spark ---
    "pyhive",            # Hive Server 2 over Thrift
    "hive_metastore",
    "snakebite",         # HDFS client
    "hdfs",              # webhdfs client (context: Spark workloads)
    "pyarrow_hdfs",      # PyArrow HDFS bridge

    # --- Spark bindings for other langs (rarely imported from Python) ---
    "sparksession",
    "pyspark_pandas",    # legacy alias
})


def is_migration_scope(name: str) -> bool:
    """True if the (possibly dotted) import root belongs to Category B.

    Matches both the exact name and any dotted prefix — ``pyspark.sql.window``
    matches via ``pyspark``. Callers usually pass the top-level root already
    (from ``_import_root``), but we accept the full path so future callers
    don't have to pre-split.
    """
    if not name:
        return False
    if name in _MIGRATION_SCOPE:
        return True
    parts = name.split(".")
    for i in range(1, len(parts)):
        if ".".join(parts[:i]) in _MIGRATION_SCOPE:
            return True
    # Bare root check (for ``dbutils.fs``: parts = ["dbutils", "fs"];
    # prefix loop above already covers this, but keep an explicit check
    # for readability when the caller only passes ``dbutils``).
    return parts[0] in _MIGRATION_SCOPE


# ---------------------------------------------------------------------------
# EAI detection: network-egress libraries that Snowflake's default firewall
# blocks.
#
# Detection is intentionally NOT based on URL schemes in Spark I/O
# (``s3://``, ``gs://``, ``abfss://``) — Snowpark Connect handles those
# natively via storage integrations, not EAI. What triggers EAI is Python
# code that opens a socket / issues an HTTP request / connects to an
# external DB / hits a cloud compute service from inside a UDF or the
# orchestrator.
# ---------------------------------------------------------------------------

# Import roots that are almost always network-egress. Presence alone is a
# strong signal; call-site detection sharpens it further.
_EAI_NETWORK_ROOTS: frozenset[str] = frozenset({
    # HTTP clients
    "requests",
    "urllib",       # stdlib but ONLY network egress
    "urllib2",
    "urllib3",
    "httpx",
    "aiohttp",
    "http",         # stdlib http.client
    "httplib",
    "httplib2",
    # Raw network
    "socket",       # stdlib
    "ssl",          # stdlib
    "asyncio",      # only when opening streams — refined at call site
    # Email / messaging egress
    "smtplib",
    "poplib",
    "imaplib",
    "ftplib",
    "telnetlib",
    "email",        # only egress via SMTP; refined at call site
    "slack_sdk",
    "slack_bolt",
    "slackclient",
    "pagerduty",
    "pdpyras",      # PagerDuty API
    "twilio",
    "sendgrid",
    "mailgun",
    "notion_client",
    "atlassian",    # atlassian-python-api
    "jira",
    "github",       # PyGithub
    # External DB drivers (bypass Snowpark's connection path)
    "psycopg2",
    "psycopg",      # psycopg3
    "pymysql",
    "mysql",        # mysql-connector-python
    "mysqlclient",
    "pyodbc",
    "pymongo",
    "motor",        # async MongoDB
    "redis",
    "aioredis",
    "cassandra",
    "elasticsearch",
    "opensearch",
    "opensearchpy",
    "neo4j",
    "influxdb",
    "clickhouse_driver",
    "clickhouse_connect",
    "cx_Oracle",
    "oracledb",
    # Messaging / streaming (external)
    "kafka",             # kafka-python
    "confluent_kafka",
    "pika",              # RabbitMQ
    "aio_pika",
    "kombu",
    "nats",
    "pulsar",
})

# Cloud SDKs that MIGHT trigger EAI depending on which service is used.
# For these, we look at the client-construction call: boto3.client("s3")
# is storage (native to Snowpark) and doesn't trigger EAI; boto3.client(
# "lambda") is compute and does.
_CLOUD_SDK_ROOTS: frozenset[str] = frozenset({
    "boto3",
    "botocore",
    "google.cloud",
    "google.api_core",
    "azure",
    "azure.storage",
    "azure.identity",
})

# Cloud services that DO NOT need EAI (native storage handled by Snowpark).
_CLOUD_STORAGE_SERVICES: frozenset[str] = frozenset({
    "s3",              # AWS
    "storage",         # GCS / Azure Blob
    "blob",            # Azure Blob
    "adls",            # Azure Data Lake
    "gcs",             # GCS
})


def _is_network_root(root: str) -> bool:
    """Match ``requests``, ``requests.auth``, ``requests_oauthlib``, etc."""
    if not root:
        return False
    if root in _EAI_NETWORK_ROOTS:
        return True
    # Prefix match for dotted paths — ``requests.auth`` should still count.
    parts = root.split(".")
    for i in range(1, len(parts)):
        if ".".join(parts[:i]) in _EAI_NETWORK_ROOTS:
            return True
    return parts[0] in _EAI_NETWORK_ROOTS


class _EAIVisitor(ast.NodeVisitor):
    """Walk a module AST and decide whether EAI is required.

    Signals collected:

    * Direct import of a known network-egress root → module-level EAI.
    * ``boto3.client("<service>")`` / ``boto3.resource("<service>")``
      where ``<service>`` is not a storage service → module-level EAI.
    * Any of the above INSIDE a function decorated with ``@udf`` /
      ``@pandas_udf`` / ``@udtf`` / ``@sproc`` → UDF-context EAI (a
      stronger signal, surfaced separately in the report).
    """

    _UDF_DECORATORS: frozenset[str] = frozenset({
        "udf", "pandas_udf", "udtf", "sproc", "sql_udf", "sql_udtf",
    })

    def __init__(self) -> None:
        self.module_eai = False
        self.udf_eai = False
        self._udf_depth = 0
        # Collect the specific package/service names that triggered EAI.
        self.triggers: list[str] = []

    def _in_udf(self) -> bool:
        return self._udf_depth > 0

    def _record(self, reason: str = "") -> None:
        if reason and reason not in self.triggers:
            self.triggers.append(reason)
        if self._in_udf():
            self.udf_eai = True
        else:
            self.module_eai = True

    # --- import statements ---

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_network_root(alias.name):
                self._record(alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and _is_network_root(node.module):
            self._record(node.module.split(".")[0])

    # --- function/UDF context ---

    def _is_udf_decorator(self, dec: ast.expr) -> bool:
        # @udf or @udf(...)
        if isinstance(dec, ast.Name) and dec.id in self._UDF_DECORATORS:
            return True
        if isinstance(dec, ast.Call):
            return self._is_udf_decorator(dec.func)
        if isinstance(dec, ast.Attribute) and dec.attr in self._UDF_DECORATORS:
            return True
        return False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        udf_deco = any(self._is_udf_decorator(d) for d in node.decorator_list)
        if udf_deco:
            self._udf_depth += 1
        try:
            self.generic_visit(node)
        finally:
            if udf_deco:
                self._udf_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        udf_deco = any(self._is_udf_decorator(d) for d in node.decorator_list)
        if udf_deco:
            self._udf_depth += 1
        try:
            self.generic_visit(node)
        finally:
            if udf_deco:
                self._udf_depth -= 1

    # --- cloud SDK client construction ---

    def visit_Call(self, node: ast.Call) -> None:
        # boto3.client("lambda") / boto3.resource("s3") ... first arg is the service.
        if self._is_cloud_client_call(node.func):
            svc = self._first_arg_string(node)
            if svc is not None and svc.lower() not in _CLOUD_STORAGE_SERVICES:
                sdk_root = self._root_of(node.func).split(".")[0]
                self._record(f"{sdk_root} ({svc})")
        # Inside a UDF, any call whose function chain starts with a known
        # network root is per-row network egress — a stronger signal than
        # the module-level import alone. Without this, a file whose only
        # egress happens inside a ``@udf`` body would be flagged ``"Yes"``
        # rather than ``"Yes (UDF)"``.
        if self._in_udf():
            root = self._root_of(node.func)
            if root and _is_network_root(root.split(".")[0]):
                self._record(root.split(".")[0])
        self.generic_visit(node)

    def _is_cloud_client_call(self, func: ast.expr) -> bool:
        if isinstance(func, ast.Attribute):
            if func.attr in ("client", "resource"):
                base = self._root_of(func.value)
                return base in _CLOUD_SDK_ROOTS or (base and base.split(".")[0] in _CLOUD_SDK_ROOTS)
        return False

    def _root_of(self, expr: ast.expr) -> str:
        parts: list[str] = []
        while isinstance(expr, ast.Attribute):
            parts.append(expr.attr)
            expr = expr.value
        if isinstance(expr, ast.Name):
            parts.append(expr.id)
        return ".".join(reversed(parts))

    def _first_arg_string(self, node: ast.Call) -> str | None:
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
        for kw in node.keywords:
            if kw.arg in ("service_name", "service"):
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    return kw.value.value
        return None


def detect_eai(source: str) -> str:
    """Return one of ``"No"``, ``"Yes"``, ``"Yes (UDF)"`` for a Python source file.

    Non-parseable files (syntax errors, or Scala/Java source passed in
    accidentally) return ``"No"`` — we prefer under-reporting to false alarms.
    """
    verdict, _ = detect_eai_detail(source)
    return verdict


def detect_eai_detail(source: str) -> tuple[str, list[str]]:
    """Return ``(verdict, triggers)`` for a Python source file.

    ``verdict`` is one of ``"No"``, ``"Yes"``, ``"Yes (UDF)"``.
    ``triggers`` is the list of package/service names that caused EAI to fire
    (e.g. ``["requests", "smtplib", "boto3 (lambda)"]``). Empty when ``"No"``.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return "No", []
    visitor = _EAIVisitor()
    visitor.visit(tree)
    if visitor.udf_eai:
        return "Yes (UDF)", visitor.triggers
    if visitor.module_eai:
        return "Yes", visitor.triggers
    return "No", []


# ---------------------------------------------------------------------------
# AR detection: which imports are not in Snowflake's Anaconda channel?
#
# Single source of truth is Snowflake itself — ``INFORMATION_SCHEMA.PACKAGES``
# returns the authoritative Anaconda package list for the connected account.
# We do not ship any repo-committed baseline because it would drift over
# time as Snowflake adds / removes packages, and every stale row is either
# a false positive (AR flagged when the package now exists) or a false
# negative (AR missed when the package was removed). Cheaper to query
# once and cache.
#
# Resolution order:
#   1. Live Snowflake ``session`` — ``SELECT PACKAGE_NAME FROM
#      INFORMATION_SCHEMA.PACKAGES WHERE LANGUAGE = 'python'`` — refreshes
#      the user-level cache and returns the authoritative set.
#   2. User cache at ``~/.cache/snowpark-migration/anaconda_packages.json``,
#      if fresh (<= :data:`_CACHE_TTL_DAYS` old).
#   3. Empty set — no session, no cache. In this state every third-party
#      import is conservatively flagged ``AR Required = Yes``. To get
#      accurate answers, run ``scripts/assessment/refresh_anaconda_cache.py``
#      once from a machine that has Snowflake credentials.
# ---------------------------------------------------------------------------

_CACHE_DIR = Path.home() / ".cache" / "snowpark-migration"
_CACHE_FILE = _CACHE_DIR / "anaconda_packages.json"
_CACHE_TTL_DAYS = 30


def _normalize_pkg_name(name: str) -> str:
    """Package names are keyed by their Python import root: lowercase with
    hyphens folded to underscores so ``scikit-learn`` and ``scikit_learn``
    resolve to the same entry."""
    return name.lower().replace("-", "_")


def _build_import_to_dist_map() -> dict[str, frozenset[str]]:
    """Build {import-root → frozenset(normalized-PyPI-dist-names)} from installed packages.

    Inverted lookup: given an import root found in user code (e.g. ``sklearn``),
    find the PyPI dist name(s) that own it (e.g. ``scikit_learn``). This lets
    ``detect_ar_required`` check an import against the Anaconda set without
    any hardcoded alias mapping — it just asks "what dist(s) provide this
    import root?" and checks those dist names against the Anaconda set.

    Resolution uses ``importlib.metadata.packages_distributions()`` (Python 3.11+)
    when available, falling back to a manual ``top_level.txt`` walk for 3.10.
    Either way the result covers every package installed in the current
    environment. Packages not installed here are not in the map; those imports
    fall through to AR=Yes (conservative and correct — we can't confirm
    Anaconda support for something we've never seen installed)."""
    try:
        import importlib.metadata as _meta

        if hasattr(_meta, "packages_distributions"):
            raw: dict[str, list[str]] = _meta.packages_distributions()  # type: ignore[attr-defined]
            return {
                k.lower(): frozenset(_normalize_pkg_name(d) for d in v)
                for k, v in raw.items()
            }
        # Python 3.10 fallback: iterate distributions and read top_level.txt
        result: dict[str, set[str]] = {}
        for dist in _meta.distributions():
            name: str = dist.metadata.get("Name") or ""  # type: ignore[assignment]
            if not name:
                continue
            top_level_txt = dist.read_text("top_level.txt")
            if not top_level_txt:
                continue
            normalized_dist = _normalize_pkg_name(name)
            for root in top_level_txt.splitlines():
                root = root.strip().lower()
                if root:
                    result.setdefault(root, set()).add(normalized_dist)
        return {k: frozenset(v) for k, v in result.items()}
    except Exception:  # noqa: BLE001 — metadata failures are non-fatal
        return {}


# Built once at module import. Maps import roots to the PyPI dist name(s) that
# own them — e.g. {"sklearn": frozenset({"scikit_learn"}), "PIL": frozenset({"pillow"})}.
# Used by detect_ar_required for a per-import reverse-lookup against the Anaconda set.
_IMPORT_TO_DIST: dict[str, frozenset[str]] = _build_import_to_dist_map()


def _read_cached_anaconda_packages() -> frozenset[str] | None:
    """Return the cached package set if the cache file exists and is
    fresh (younger than :data:`_CACHE_TTL_DAYS`); else ``None``.

    Malformed / partially-written cache files are treated as absent so a
    corrupted cache never breaks the scan — the caller falls through to
    the empty-set fallback."""
    if not _CACHE_FILE.is_file():
        return None
    try:
        payload = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    generated_at = payload.get("generated_at", "")
    try:
        ts = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if datetime.now(timezone.utc) - ts > timedelta(days=_CACHE_TTL_DAYS):
        return None
    pkgs = payload.get("packages")
    if not isinstance(pkgs, list):
        return None
    return frozenset(_normalize_pkg_name(str(p)) for p in pkgs if p)


def _write_cached_anaconda_packages(packages: set[str]) -> None:
    """Persist the resolved Anaconda package set to the user cache. Best-
    effort — an IO failure is logged but never raised, so a filesystem
    hiccup can't break the scan itself."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "packages": sorted(packages),
        }
        _CACHE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        logger.debug("Could not write Anaconda cache to %s: %s", _CACHE_FILE, e)


def refresh_anaconda_cache(session: Any) -> frozenset[str]:
    """Query Snowflake for its Anaconda channel package list and refresh
    the on-disk user cache.

    Called automatically whenever a Snowpark session is passed into
    ``scan_codebase.scan(session=...)``; also exposed as a CLI via
    ``scripts/assessment/refresh_anaconda_cache.py`` for one-shot seeding
    from a machine that has Snowflake credentials.

    Returns the fetched package set; returns an empty frozenset if the
    query fails or returns no rows so the caller sees the same
    ``no-anaconda-data`` state as a first-run offline scan (all third-
    party imports conservatively flagged as AR-required)."""
    try:
        rows = session.sql(
            "SELECT PACKAGE_NAME FROM INFORMATION_SCHEMA.PACKAGES "
            "WHERE LANGUAGE = 'python'"
        ).collect()
    except Exception as e:  # noqa: BLE001 - Snowpark can raise many concrete types
        logger.warning("Snowflake anaconda-package query failed: %s", e)
        return frozenset()
    names = {_normalize_pkg_name(str(r[0])) for r in rows if r and r[0]}
    if not names:
        return frozenset()
    _write_cached_anaconda_packages(names)
    return frozenset(names)


def _load_anaconda_snapshot(session: Any | None = None) -> frozenset[str]:
    """Return the current Snowflake Anaconda package set as normalized PyPI dist names.

    Resolution order:
      1. Live Snowflake ``session`` — run SQL, refresh user cache, return
         the authoritative set.
      2. User cache — <= :data:`_CACHE_TTL_DAYS` old.
      3. :data:`_ANACONDA_FALLBACK` — a small, intentionally minimal set
         of well-known Anaconda packages. Used only when steps 1 and 2
         both fail so common imports (``pandas``, ``numpy``, ``boto3``,
         …) don't get falsely flagged ``AR=Yes`` on the first offline
         scan. Anything not in this set still gets ``AR=Yes`` until a
         session refresh or a cache seed happens.

    The returned set contains PyPI dist names only (e.g. ``scikit_learn``).
    Import-root aliasing (``sklearn`` ↔ ``scikit_learn``) is handled at
    check time by :func:`detect_ar_required` via :data:`_IMPORT_TO_DIST`."""
    if session is not None:
        fetched = refresh_anaconda_cache(session)
        if fetched:
            return fetched
    cached = _read_cached_anaconda_packages()
    if cached is not None:
        return cached
    return frozenset(_ANACONDA_FALLBACK)


# Minimal offline fallback used ONLY when both the live session and the
# user cache are unavailable. Not comprehensive — Snowflake's Anaconda
# channel has thousands of packages and this list intentionally covers
# only the top common ones so a first-run offline scan doesn't over-
# report ``AR=Yes`` for imports like ``pandas`` or ``numpy``. Any package
# not listed here still gets ``AR=Yes`` in the offline case; to get an
# accurate answer, run the skill against a machine with Snowflake creds
# (which refreshes the cache) or run ``refresh_anaconda_cache.py``.
#
# This constant is small on purpose — do not grow it into a mirror of
# Snowflake's channel. Growing it re-introduces the drift problem that
# motivated the session-first design.
_ANACONDA_FALLBACK: frozenset[str] = frozenset({
    # Data science / numerics (PyPI dist names, normalized)
    "numpy", "pandas", "scipy", "scikit_learn", "matplotlib",
    "seaborn", "statsmodels", "xgboost", "lightgbm",
    # Arrow / Parquet
    "pyarrow", "fastparquet",
    # HTTP / networking
    "requests", "urllib3", "certifi", "idna", "charset_normalizer",
    # Cloud SDKs
    "boto3", "botocore", "s3fs", "gcsfs",
    # Snowflake first-party
    "snowflake", "snowflake_snowpark_python", "snowflake_connector_python",
    "snowflake_ml_python",
    # Serialization / config
    "pyyaml", "pydantic", "jsonschema",
    # Dates / time
    "pytz", "python_dateutil",
    # Utilities
    "attrs", "typing_extensions", "packaging", "cryptography",
    # Templating
    "jinja2", "markupsafe",
})


# Test-only alias retained for back-compat with existing test imports.
# Points at the same minimal fallback used in production.
_ANACONDA_SNAPSHOT = _ANACONDA_FALLBACK


def detect_ar_required(
    imports: list[str],
    *,
    internal_modules: set[str],
    stdlib_modules: frozenset[str],
    anaconda_packages: frozenset[str],
) -> list[str]:
    """Return the import roots that require the Artifact Repository.

    An empty list means no AR is needed. A non-empty list names the specific
    third-party packages that are not available in Snowflake's Anaconda channel
    and would need to be staged via the Artifact Repository.

    Rules (checked in order for each import):
      * Import root in ``stdlib_modules`` → not AR.
      * Import root in ``internal_modules`` (workload's own packages) → not AR.
      * Import root in :data:`_MIGRATION_SCOPE` → not AR (being replaced).
      * Import root in ``anaconda_packages`` (direct or via reverse lookup) → not AR.
      * Otherwise → AR required; root added to result list.
    """
    ar_imports: list[str] = []
    seen: set[str] = set()
    for imp in imports:
        root = imp.split(".")[0] if imp else ""
        if not root:
            continue
        if imp.startswith("."):
            continue
        key = root.lower().replace("-", "_")
        if key in seen:
            continue
        if key in stdlib_modules:
            continue
        if root in internal_modules or key in {m.lower() for m in internal_modules}:
            continue
        if is_migration_scope(imp) or is_migration_scope(root):
            continue
        if key in anaconda_packages:
            continue
        dist_names = _IMPORT_TO_DIST.get(root.lower(), frozenset())
        if dist_names & anaconda_packages:
            continue
        ar_imports.append(root)
        seen.add(key)
    return ar_imports


# ---------------------------------------------------------------------------
# Source / target derivation from per-file scan data.
#
# The two columns answer different questions and MUST use different
# vocabularies:
#
#   * Source System = the data PLATFORM the file reads from
#     (``"S3"``, ``"Azure Blob"``, ``"GCS"``, ``"HDFS"``, ``"Kafka"``,
#     ``"REST API"``, ``"Snowflake"``, ``"JDBC (PostgreSQL)"``, …).
#     File formats (Parquet / JSON / CSV) DO NOT go here — a Parquet file
#     can live on any of these platforms.
#
#   * Target Type = the MECHANISM the file writes through
#     (``"Table"``, ``"Stage"``, ``"File"``, ``"Email"``, ``"SFTP"``,
#     ``"API"``, ``"DataFrame"``, ``"N/A"``). This maps to what the
#     migrated workload will produce in Snowflake or elsewhere.
#
#   * ``"DataFrame"`` for either column means the file is pure logic —
#     it operates on Spark DataFrames in memory but doesn't touch an
#     external platform of its own.
#
#   * ``"N/A"`` means the file is a package marker, config helper, or
#     other utility with neither Spark usage nor I/O — no data-flow role
#     to classify.
# ---------------------------------------------------------------------------

# URL scheme label → data-platform name for the Source System column.
_PLATFORM_BY_SCHEME: dict[str, str] = {
    "S3": "S3",
    "HDFS": "HDFS",
    "GCS": "GCS",
    "ADLS": "Azure Blob",
    "WASB": "Azure Blob",
    "JDBC": "JDBC",
}

# URL schemes that correspond to external cloud storage — in Snowflake
# these are surfaced as external Stages when written to.
_CLOUD_STORAGE_SCHEMES: frozenset[str] = frozenset({
    "S3", "GCS", "ADLS", "WASB",
})

_URL_EXT_TO_FORMAT: dict[str, str] = {
    ".json": "JSON", ".parquet": "Parquet", ".csv": "CSV",
    ".orc": "ORC", ".avro": "Avro", ".txt": "Text", ".xml": "XML",
}

_URL_FMT_HINTS: dict[str, str] = {
    "parquet": "Parquet", "json": "JSON", "csv": "CSV", "orc": "ORC", "avro": "Avro",
}


def _format_from_url(url: str, context: str = "") -> str | None:
    """Infer data format from URL extension or surrounding context keywords.

    Two-pass: strict extension match first (endswith / glob / path-segment),
    then loose substring, then format-keyword hints in url+context.
    Returns None when nothing matches (caller decides the fallback).
    """
    clean = url.lower().split("?")[0].split("#")[0]
    for ext, fmt in _URL_EXT_TO_FORMAT.items():
        if clean.endswith(ext) or f"*{ext}" in clean or ext + "/" in clean:
            return fmt
    for ext, fmt in _URL_EXT_TO_FORMAT.items():
        if ext in clean:
            return fmt
    combined = clean + " " + context.lower()
    for hint, fmt in _URL_FMT_HINTS.items():
        if hint in combined:
            return fmt
    return None

# Import roots (or top-level names) that identify a specific PLATFORM
# beyond what URL literals reveal. Ordered — first match wins.
_PLATFORM_BY_IMPORT: list[tuple[frozenset[str], str]] = [
    (frozenset({"kafka", "confluent_kafka", "kafka_python"}), "Kafka"),
    (frozenset({"pulsar"}), "Pulsar"),
    (frozenset({"nats"}), "NATS"),
    (frozenset({"pika", "aio_pika", "kombu"}), "RabbitMQ"),
    (frozenset({"psycopg2", "psycopg"}), "JDBC (PostgreSQL)"),
    (frozenset({"pymysql", "mysql", "mysqlclient"}), "JDBC (MySQL)"),
    (frozenset({"pyodbc"}), "JDBC (ODBC)"),
    (frozenset({"cx_oracle", "oracledb"}), "JDBC (Oracle)"),
    (frozenset({"pymongo", "motor"}), "MongoDB"),
    (frozenset({"redis", "aioredis"}), "Redis"),
    (frozenset({"cassandra"}), "Cassandra"),
    (frozenset({"elasticsearch", "opensearchpy", "opensearch"}), "Elasticsearch"),
    (frozenset({"snowflake_connector_python", "snowflake_snowpark_python"}), "Snowflake"),
    (frozenset({"requests", "urllib", "urllib2", "httpx", "aiohttp", "http", "httplib", "httplib2"}), "REST API"),
]

# Filename/path hints — last-resort inference for files that don't have
# literal URLs or platform-specific imports (common in class-based ETL
# frameworks like Kipawa's, where paths come from config at runtime).
_FILENAME_PLATFORM_HINTS: list[tuple[frozenset[str], str]] = [
    (frozenset({"s3"}), "S3"),
    (frozenset({"azure", "adls", "wasb", "blob"}), "Azure Blob"),
    (frozenset({"gcs", "gcp"}), "GCS"),
    (frozenset({"hdfs"}), "HDFS"),
    (frozenset({"kafka"}), "Kafka"),
    (frozenset({"snowflake", "snowpark"}), "Snowflake"),
    (frozenset({"rest", "api", "http"}), "REST API"),
    (frozenset({"jdbc", "postgres", "postgresql", "mysql", "oracle"}), "JDBC"),
]


def _jdbc_flavor(url: str) -> str:
    """Refine a bare ``JDBC`` label to ``JDBC (PostgreSQL)`` etc. when possible."""
    u = url.lower()
    for flavor, tag in (
        ("postgres", "PostgreSQL"),
        ("mysql", "MySQL"),
        ("mssql", "SQL Server"),
        ("sqlserver", "SQL Server"),
        ("oracle", "Oracle"),
        ("snowflake", "Snowflake"),
        ("db2", "DB2"),
        ("redshift", "Redshift"),
        ("teradata", "Teradata"),
    ):
        if flavor in u:
            return f"JDBC ({tag})"
    return "JDBC"


def _platform_from_scheme(scheme_label: str, url: str) -> str:
    base = _PLATFORM_BY_SCHEME.get(scheme_label, scheme_label)
    if scheme_label == "JDBC":
        return _jdbc_flavor(url)
    return base


def _platform_from_imports(imports: list[str]) -> str:
    roots = {imp.split(".")[0].lower() for imp in imports if imp and not imp.startswith(".")}
    for tokens, label in _PLATFORM_BY_IMPORT:
        if roots & tokens:
            return label
    return ""


def _platform_from_filename(path: str) -> str:
    """Infer a platform from filename/path tokens when the code has no
    literal URLs. Kipawa's ``S3ParquetWriter`` / ``S3JsonReader`` classes
    build their paths from configuration at runtime — the file itself
    only mentions the platform in its name.
    """
    p = path.lower()
    for tokens, label in _FILENAME_PLATFORM_HINTS:
        if any(t in p for t in tokens):
            return label
    return ""


def _platform_from_dag_location(loc: str) -> str:
    """Infer the source/target platform from a DAG-resolved URL/table name.

    ``loc`` comes from an ``ext:source:`` / ``ext:sink:`` node's label,
    which schema_mine populates after resolving the workload's runtime
    paths (including config-file-driven ones). Examples:
      * ``"s3://bucket/prefix/"`` → ``"S3"``
      * ``"jdbc:postgresql://host/db"`` → ``"JDBC (PostgreSQL)"``
      * ``"PROD_DB.ANALYTICS.ACCOUNTS"`` (no scheme) → ``"Snowflake"``
    """
    if not loc:
        return ""
    lo = loc.lower()
    if lo.startswith("s3://") or lo.startswith("s3a://") or lo.startswith("s3n://"):
        return "S3"
    if lo.startswith("gs://"):
        return "GCS"
    if lo.startswith(("abfs://", "abfss://", "wasb://", "wasbs://")):
        return "Azure Blob"
    if lo.startswith("hdfs://"):
        return "HDFS"
    if lo.startswith("jdbc:"):
        return _jdbc_flavor(loc)
    if lo.startswith(("http://", "https://")):
        return "REST API"
    # A bare ``DB.SCHEMA.TABLE`` identifier with no scheme is almost always
    # a Snowflake table reference in this tool's context.
    if "://" not in loc and loc.count(".") >= 1 and " " not in loc:
        return "Snowflake"
    return ""


def _target_type_from_dag_location(loc: str) -> str:
    """Categorize a DAG-resolved sink URL/table into a Target Type label."""
    if not loc:
        return ""
    lo = loc.lower()
    if lo.startswith("@") or lo.startswith("f'@") or lo.startswith('f"@'):
        return "Snowflake Stage"
    if lo.startswith(("s3://", "s3a://", "s3n://", "gs://",
                       "abfs://", "abfss://", "wasb://", "wasbs://")):
        return "Cloud Storage"
    if lo.startswith("hdfs://"):
        return "Cloud Storage"
    if lo.startswith("jdbc:"):
        return "Snowflake Table"
    if lo.startswith(("http://", "https://")):
        return "API"
    # No scheme → almost certainly a Snowflake table (DB.SCHEMA.TABLE).
    if "://" not in loc and loc.count(".") >= 1 and " " not in loc:
        return "Snowflake Table"
    return ""


# ---- Target Type / Target Location extraction ----

_TABLE_WRITE_RE = re.compile(
    r"\.saveAsTable\s*\(\s*[\"']([^\"']+)[\"']\s*\)"
    r"|"
    r"\.option\s*\(\s*[\"']dbtable[\"']\s*,\s*[\"']([^\"']+)[\"']\s*\)"
    r"|"
    r"\.insertInto\s*\(\s*[\"']([^\"']+)[\"']\s*\)"
    r"|"
    r"\.writeTo\s*\(\s*[\"']([^\"']+)[\"']\s*\)"
)
# Delta/Parquet .save("s3://...") or .option("path", "s3://...")
_DELTA_SAVE_PATH_RE = re.compile(
    r"\.save\s*\(\s*[\"']((?:s3|gs|abfs|abfss|wasb|wasbs|hdfs)://[^\"']+)[\"']\s*\)"
    r"|"
    r"\.option\s*\(\s*[\"']path[\"']\s*,\s*[\"']((?:s3|gs|abfs|abfss|wasb|wasbs|hdfs)://[^\"']+)[\"']\s*\)"
)
# SQL DDL/DML target table extraction
_SQL_TARGET_TABLE_RE = re.compile(
    r"(?:INSERT\s+(?:INTO|OVERWRITE(?:\s+TABLE)?)\s+|"
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)"
    r"([\w.`\"]+)",
    re.IGNORECASE,
)
_SNOWFLAKE_WRITE_RE = re.compile(
    r"\.write\s*\.format\s*\(\s*[\"']snowflake[\"']\s*\)"
    r"|"
    r"\.write\s*\.mode\s*\([^)]*\)\s*\.format\s*\(\s*[\"']snowflake[\"']\s*\)"
    r"|"
    r"snowpark[^\s]*\.write_pandas\s*\("
)
# Named Snowflake stages are referenced as ``@STAGE_NAME/...`` strings. When
# code writes to such a path we surface it as ``"Snowflake Stage"`` rather
# than ``"Cloud Storage"`` — the two have different Snowflake-side setup.
_SNOWFLAKE_STAGE_WRITE_RE = re.compile(
    r"\.(?:save|parquet|json|csv|text|orc)\s*\(\s*[\"']?f?[\"']@[A-Za-z_][A-Za-z0-9_.]*"
    r"|"
    r"\.option\s*\(\s*[\"']path[\"']\s*,\s*[\"']?f?[\"']@[A-Za-z_]"
)
_SMTP_RE = re.compile(r"\bsmtplib\s*\.\s*SMTP\b|\bsend_email\s*\(|\.\s*send_message\s*\(")
_SFTP_RE = re.compile(r"\b(?:pysftp|paramiko|ftplib)\b")
_HTTP_POST_RE = re.compile(r"\b(?:requests|httpx|urllib\.request)\s*\.\s*(?:post|put|patch)\b")
_LOCAL_FILE_WRITE_RE = re.compile(
    # Pandas-style file writes: ``df.to_csv("out.csv")``. Require a string
    # literal as the first argument so we don't false-match Spark's SQL
    # column functions like ``F.to_json(col)`` — those pass an expression,
    # not a path. ``to_json`` is intentionally omitted because it collides
    # with ``pyspark.sql.functions.to_json`` and would false-match
    # transformer code that stringifies a struct column.
    r"\.to_(?:csv|excel|parquet|feather|hdf|pickle)\s*\(\s*[\"']"
    r"|"
    r"\bopen\s*\([^)]*[\"']w"
)
# Streaming egress: Spark structured streaming writes, Kafka producers,
# and Kinesis put_record are all "Streaming Topic" targets.
_STREAMING_WRITE_RE = re.compile(
    r"\.writeStream\b"
    r"|"
    r"\b(?:KafkaProducer|Producer)\s*\("
    r"|"
    r"\.produce\s*\("
    r"|"
    r"\.send\s*\(\s*[\"'][A-Za-z][\w.\-]*[\"']"     # kafka.send('topic', ...)
    r"|"
    r"kinesis[^\s]*\.\s*put_record"
)


def _detect_target_from_source(source: str, ext: str = ".py") -> tuple[str, str]:
    """Scan a file's source text for write patterns that reveal a specific
    target type + location.

    Returns ``(target_type, target_location)``. Location is empty when the
    pattern doesn't include a static string (e.g. writes to a variable-
    interpolated path).
    """
    if not source:
        return "", ""

    # SQL files: extract the first DDL/DML target table name.
    if ext == ".sql":
        m = _SQL_TARGET_TABLE_RE.search(source)
        if m:
            return "Snowflake Table", m.group(1).strip('`"')
        return "", ""

    m = _TABLE_WRITE_RE.search(source)
    if m:
        loc = next((g for g in m.groups() if g), "")
        return "Snowflake Table", loc

    if _SNOWFLAKE_WRITE_RE.search(source):
        # Snowflake write without an explicit dbtable option — we know it's
        # a Table target but don't have the FQ name statically.
        return "Snowflake Table", ""

    if _SNOWFLAKE_STAGE_WRITE_RE.search(source):
        return "Snowflake Stage", ""

    # Delta/Parquet .save("s3://...") — extract the cloud path as location.
    m = _DELTA_SAVE_PATH_RE.search(source)
    if m:
        loc = next((g for g in m.groups() if g), "")
        return "Cloud Storage", loc

    if _STREAMING_WRITE_RE.search(source):
        return "Streaming Topic", ""

    if _SFTP_RE.search(source):
        return "SFTP", ""
    if _SMTP_RE.search(source):
        return "Email", ""
    if _HTTP_POST_RE.search(source):
        return "API", ""
    if _LOCAL_FILE_WRITE_RE.search(source):
        return "File", ""

    return "", ""


def _filename_target_hint(path: str) -> str:
    """Infer a coarse target type from filename when the code doesn't have
    literal write patterns. Only used for files that clearly play a
    'writer' role (e.g. inside a ``writers/`` directory or with a
    ``*Writer`` class).
    """
    p = path.lower()
    if any(seg in p for seg in ("writer/", "writers/", "sink/", "sinks/")):
        if any(tok in p for tok in ("s3", "azure", "blob", "gcs", "adls", "wasb", "hdfs")):
            return "Cloud Storage"
        if "table" in p or "snowflake" in p or "jdbc" in p:
            return "Snowflake Table"
        if "stage" in p:
            return "Snowflake Stage"
        if "kafka" in p or "stream" in p:
            return "Streaming Topic"
        if "email" in p:
            return "Email"
        if "sftp" in p or "ftp" in p:
            return "SFTP"
        if "api" in p or "rest" in p or "http" in p:
            return "API"
        return "File"  # writer of unknown mechanism
    return ""


def _filename_is_reader(path: str) -> bool:
    p = path.lower()
    return any(seg in p for seg in ("reader/", "readers/", "source/", "sources/"))


def _is_package_marker(path: str) -> bool:
    """A file whose sole purpose is to mark a Python package (or an empty
    utility file) has no data-flow role — surface N/A on all three
    columns rather than the misleading ``DataFrame`` default."""
    return path.endswith("__init__.py")


def build_file_info_row(
    *,
    name: str,
    path: str,
    ext: str,
    lines: int,
    source: str,
    imports: list[str],
    data_urls: list[tuple[str, str]],
    data_formats: list[tuple[str, str]],
    spark_api: int,
    internal_modules: set[str],
    stdlib_modules: frozenset[str],
    anaconda_packages: frozenset[str],
    dag_sink_locations: list[str] | None = None,
    dag_source_locations: list[str] | None = None,
) -> dict:
    """Assemble a single row's dict form.

    Returned dict conforms to the :class:`FileInfoRow` model (in ``assess_ir``);
    the caller instantiates the pydantic object.

    ``spark_api`` is the count of Spark API references detected in the file.
    Files with ``spark_api == 0`` and no I/O signals are utility/init files
    and get ``"N/A"`` for source/target rather than the misleading
    ``"DataFrame"`` default.

    ``dag_sink_locations`` / ``dag_source_locations`` are labels of external
    sink/source nodes from the data-flow DAG that this file has edges to.
    For workloads that resolve paths from config at runtime (class-based
    ETL frameworks), these are the only way to surface a concrete
    target_location — the source code alone has no literal URL. When
    provided, sink locations fill in ``target_location`` if the pattern
    scan didn't extract one; source locations enrich the source-system
    label when the source is otherwise pure DataFrame.
    """
    dag_sink_locations = dag_sink_locations or []
    dag_source_locations = dag_source_locations or []
    is_python = ext == ".py"
    has_spark = spark_api > 0
    is_pkg_marker = _is_package_marker(path)

    # Package markers (``__init__.py``) with no data-flow activity of their
    # own default to N/A across the board — they're re-export shims, not
    # files that touch a system. Short-circuit before running any of the
    # detection logic so directory-based filename hints don't fire on them.
    if is_pkg_marker and not has_spark and not any(k == "read" for (_f, k) in data_formats) and not any(k == "write" for (_f, k) in data_formats):
        ar_pkgs = detect_ar_required(
            imports,
            internal_modules=internal_modules,
            stdlib_modules=stdlib_modules,
            anaconda_packages=anaconda_packages,
        ) if is_python else []
        return {
            "name": name,
            "path": path,
            "source_system": ["N/A"],
            "target_type": ["N/A"],
            "target_location": "",
            "file_formats": [],
            "eai_required": "No",
            "eai_packages": [],
            "ar_required": "N/A" if not is_python else ("Yes" if ar_pkgs else "No"),
            "ar_packages": ar_pkgs,
            "lines": lines,
        }

    read_formats = [f for (f, k) in data_formats if k == "read"]
    write_formats = [f for (f, k) in data_formats if k == "write"]
    has_reads = bool(read_formats)
    has_writes = bool(write_formats)

    # ---- Source System (platform) ----
    src_labels: list[str] = []

    # 1) URL literals give us the platform directly — but only when the file
    #    has reads. A write-only file's URLs are targets, not sources.
    if has_reads or not has_writes:
        for label, url in data_urls:
            p = _platform_from_scheme(label, url)
            if p and p not in src_labels:
                src_labels.append(p)

    # 2) Import-based platform detection (Kafka SDKs, REST clients, external DBs).
    imp_platform = _platform_from_imports(imports)
    if imp_platform and imp_platform not in src_labels:
        src_labels.append(imp_platform)

    # 3) DAG-derived source locations reveal the platform when the file has
    #    edges from an external source pseudo-node. Cheap: schema_mine has
    #    already resolved config-driven paths, so this catches Kipawa-style
    #    class-based ETL where the code alone shows no URL.
    for loc in dag_source_locations:
        p = _platform_from_dag_location(loc)
        if p and p not in src_labels:
            src_labels.append(p)

    # 4) Filename/path hints — last resort, and only when the file is on
    #    the read side (a "reader" file, or has read-format signals with
    #    no URL). Writers' names shouldn't drive source labels.
    if not src_labels:
        if _filename_is_reader(path) or has_reads:
            hint = _platform_from_filename(path)
            if hint:
                src_labels.append(hint)

    source_system: list[str]
    if src_labels:
        source_system = src_labels
    elif has_spark:
        source_system = ["In-Memory"]
    else:
        source_system = ["N/A"]

    # ---- Target Type (mechanism) — collect all distinct write targets ----
    tgt_types: list[str] = []
    target_location = ""

    # 1) Pattern scan of the source: saveAsTable / snowflake / SMTP / SFTP / etc.
    if source and (is_python or ext == ".sql"):
        tt, tl = _detect_target_from_source(source, ext=ext)
        if tt:
            tgt_types.append(tt)
            target_location = tl

    # 2) DAG-derived sink locations: schema_mine has resolved config-driven
    #    paths that the source text doesn't contain literally.
    for loc in dag_sink_locations:
        tt = _target_type_from_dag_location(loc)
        if tt and tt not in tgt_types:
            tgt_types.append(tt)
        if not target_location:
            target_location = loc

    # 3) URL-based inference from literals in the code.
    if has_writes:
        for label, url in data_urls:
            if label in _CLOUD_STORAGE_SCHEMES:
                if "Cloud Storage" not in tgt_types:
                    tgt_types.append("Cloud Storage")
                if not target_location:
                    target_location = url
            elif label == "HDFS":
                if "Cloud Storage" not in tgt_types:
                    tgt_types.append("Cloud Storage")
                if not target_location:
                    target_location = url
            elif label == "JDBC":
                if "Snowflake Table" not in tgt_types:
                    tgt_types.append("Snowflake Table")
                if not target_location:
                    target_location = url

    # 4) Filename hints — only for files that clearly play a writer role.
    if not tgt_types:
        hint = _filename_target_hint(path)
        if hint:
            tgt_types.append(hint)

    # 4b) Supplement from write-format signals not captured by pattern/URL/DAG steps.
    #     .saveAsTable() → "Table" format → Snowflake Table destination.
    #     SQL execution patterns in .sql files → table writes.
    #     These fire even when tgt_types is already non-empty (e.g. a file can
    #     write both to Cloud Storage AND saveAsTable in the same script).
    if "Table" in write_formats and "Snowflake Table" not in tgt_types:
        tgt_types.append("Snowflake Table")
    if "SQL" in write_formats and not tgt_types:
        tgt_types.append("Snowflake Table")

    # 5) Fallbacks.
    if not tgt_types:
        if has_writes:
            tgt_types = ["File"]
        elif has_spark:
            tgt_types = ["In-Memory"]
        else:
            tgt_types = ["N/A"]

    target_type: list[str] = tgt_types

    # ---- EAI / AR (Python-only) ----
    if is_python and source:
        eai_required, eai_pkgs = detect_eai_detail(source)
    else:
        eai_required = "No"
        eai_pkgs: list[str] = []

    if is_python:
        ar_pkgs = detect_ar_required(
            imports,
            internal_modules=internal_modules,
            stdlib_modules=stdlib_modules,
            anaconda_packages=anaconda_packages,
        )
        ar_required = "Yes" if ar_pkgs else "No"
    else:
        ar_pkgs = []
        ar_required = "N/A"

    # Deduplicate formats from code patterns and URL extensions.
    # URL extension detection mirrors scan_codebase logic so that
    # files like s3://bucket/file.csv appear as "CSV" here too.
    _fmt_kinds: dict[str, set[str]] = {}
    for _fmt, _kind in data_formats:
        _fmt_kinds.setdefault(_fmt, set()).add(_kind)
    for _label, _url in data_urls:
        _url_fmt = _format_from_url(_url)
        if _url_fmt:
            _url_kinds = _fmt_kinds.setdefault(_url_fmt, set())
            if has_reads or not has_writes:
                _url_kinds.add("read")
            if has_writes:
                _url_kinds.add("write")
    file_formats = []
    for _fmt in sorted(_fmt_kinds):
        _kinds = _fmt_kinds[_fmt]
        if "read" in _kinds and "write" in _kinds:
            file_formats.append(f"{_fmt} (read/write)")
        elif "read" in _kinds:
            file_formats.append(f"{_fmt} (read)")
        else:
            file_formats.append(f"{_fmt} (write)")

    return {
        "name": name,
        "path": path,
        "source_system": source_system,
        "target_type": target_type,
        "target_location": target_location,
        "file_formats": file_formats,
        "eai_required": eai_required,
        "eai_packages": eai_pkgs,
        "ar_required": ar_required,
        "ar_packages": ar_pkgs,
        "lines": lines,
    }
