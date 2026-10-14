#!/usr/bin/env python3
"""Deterministic codebase scan that populates the codebase-derived half of the IR.

Runs at Phase 4 of ``migrate-pyspark-to-snowpark-connect`` alongside
:mod:`transform_analysis`. The two transformers each build a partial
:class:`~assess_ir.Assessment`; :mod:`render_assessment` merges them.

This scanner has zero dependencies beyond stdlib + Pydantic so it can be
invoked from skill runners without any extra installs.

What we populate from the workload directory:

* ``workload.files_scanned / lines_of_code / library_imports / file_dependencies``
* ``file_types``                  — extension → (count, lines)
* ``data_sources``                — URL scheme + reader-format heuristics
* ``complex_patterns``            — RDD, Streaming, ML, Hive, Delta, UDF, JDBC
* ``compatibility.supported_usages`` (counted Spark API references modulo
  the analyzer's not-supported set; the merger reconciles totals)
* ``file_summary_by_type / file_summary_by_technology``
* ``spark_api_by_category``       — DataFrame / RDD / SQL bins
* ``third_party_libs``             — top imports + a static support allowlist
* ``most_depended_files / most_complex_files / cross_module_dependencies``
* ``migration_waves``              — topological waves from the import graph
* ``workload_classification / project_type / code_churn /``
  ``sources_sinks_inventory / high_risk_formats / common_refactors``

What this scanner DOES NOT do:

* Run any tools (no compilers, no Spark, no LLM). Pure regex + path walk.
* Try to be exhaustive — it captures the prototype's signal sections; the
  analyzer fills in the precision-sensitive bits.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

_SCRIPT_DIR = Path(__file__).resolve().parent
_KB_PATH           = _SCRIPT_DIR.parent / "data" / "kb_rules.json"
_CAPABILITIES_PATH = _SCRIPT_DIR.parent / "data" / "llm_capabilities.json"


def _load_capabilities() -> "tuple[dict[str, dict], dict[str, dict], dict]":
    """Load llm_capabilities.json and return ``(entries, tiers, workload_classification)``.

    ``entries`` is keyed by EWI prefix; ``tiers`` is keyed by tier key
    (``"specialized"``, ``"ai_assisted"``, ``"standard"``); ``workload_classification``
    carries the archetype 2x2 matrix and thresholds.

    All values are empty when the file is absent so callers degrade gracefully.
    """
    if not _CAPABILITIES_PATH.exists():
        return {}, {}, {}
    try:
        raw = json.loads(_CAPABILITIES_PATH.read_text(encoding="utf-8", errors="ignore"))
        return raw.get("entries", {}), raw.get("_tiers", {}), raw.get("workload_classification", {})
    except Exception:
        return {}, {}, {}


# Module-level config — loaded once at import.
_LLM_CAPABILITIES: "dict[str, dict]"
_LLM_TIER_CONFIG: "dict[str, dict]"
_WC_CONFIG: "dict"
_LLM_CAPABILITIES, _LLM_TIER_CONFIG, _WC_CONFIG = _load_capabilities()


def _tier_from_ewi_prefix(prefix: str) -> str:
    """Return the capability tier for an EWI prefix from llm_capabilities.json.

    Tier values: ``"specialized"`` (Snowflake feature mapping needed),
    ``"ai_assisted"`` (Cortex LLM can restructure), ``"standard"`` (transpiler only).

    Falls back to ``"ai_assisted"`` for unknown prefixes so that newly-added KB
    patterns are conservatively treated as LLM-convertible rather than being silently
    dropped.  When the capabilities file is updated to classify the new prefix
    differently, the change takes effect on the next render with no code changes.
    """
    cap = _LLM_CAPABILITIES.get(prefix, {})
    return cap.get("tier", "ai_assisted")


def _load_kb_index() -> "tuple[dict, dict]":
    """Load kb_rules.json and return two lookup dicts keyed by EWI prefix.

    Returns
    -------
    prefix_stats : dict[str, Counter]
        ``{ewi_prefix: Counter({status_class: count})}``
    prefix_rules : dict[str, list[dict]]
        ``{ewi_prefix: [rule, ...]}`` — full rule objects, used for rule_id
        namespace analysis (determines architectural vs convertible patterns).

    Both dicts are empty when the KB file is absent or unreadable so that
    callers degrade gracefully rather than crashing.
    """
    prefix_stats: dict[str, Counter] = {}
    prefix_rules: dict[str, list] = {}
    if not _KB_PATH.exists():
        return prefix_stats, prefix_rules
    try:
        raw = json.loads(_KB_PATH.read_text(encoding="utf-8", errors="ignore"))
        rules: list[dict] = raw if isinstance(raw, list) else raw.get("rules", [])
        for rule in rules:
            ewi = (rule.get("ewi_code") or "").strip()
            if not ewi:
                continue
            prefix = ewi.split("-")[0]
            sc = rule.get("status_class", "")
            prefix_stats.setdefault(prefix, Counter())[sc] += 1
            prefix_rules.setdefault(prefix, []).append(rule)
    except Exception:
        pass
    return prefix_stats, prefix_rules


# Module-level KB index — loaded once at import, graceful empty fallback.
_KB_PREFIX_STATS, _KB_PREFIX_RULES = _load_kb_index()



if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from assess_ir import (
    Assessment,
    AssessmentMetadata,
    CircularDependency,
    ComplexPatternRow,
    CompatibilitySummary,
    DataSourceRow,
    DependencyFile,
    DependencyGraph,
    FileCompatibilityRow,
    FileInfoRow,
    FileSummaryByTechnology,
    FileSummaryByType,
    FileTypeRow,
    GraphCluster,
    GraphEdge,
    GraphNode,
    HighRiskFormatRow,
    IsolatedModule,
    IsolatedModuleFile,
    MigrationWave,
    ProjectType,
    Readiness,
    RefactorCheckRow,
    SourceSinkInventoryRow,
    SparkApiByCategory,
    SparkApiByStatus,
    ThirdPartyLibRow,
    UnresolvedDataEdge,
    UnresolvedDynamicImport,
    WaveGraph,
    WaveGraphEdge,
    WaveGraphNode,
    WorkloadClassification,
    WorkloadSummary,
    render_migration_stages,
)

from file_info import (
    _IMPORT_TO_DIST,
    _URL_EXT_TO_FORMAT,
    _format_from_url as _fi_format_from_url,
    _load_anaconda_snapshot,
    build_file_info_row,
    is_migration_scope,
)

# ---------------------------------------------------------------------------
# Data-edge AST walker — moved out of this module (2025 refactor). The
# walker owns signature normalization + all five path-extraction patterns
# (positional arg, ``.option("path", x).load()``, variable-key subscript,
# ``spark.sql`` passthrough, ``.format().load()`` chain, and loop-generated
# paths). Constants and helpers are re-exported here as module attributes so
# existing scan_codebase tests keep working unchanged.
# ---------------------------------------------------------------------------
from data_edge_ast import (
    UnresolvedEdge,
    _URI_SCHEME_PREFIXES,
    _SIGNATURE_NOISE_WORDS,
    _attr_chain_names,
    _collect_assignments,
    _collect_call_site_args,
    _collect_for_targets,
    _extract_path_signatures as _extract_path_signatures_full,
    _extract_path_uris_and_sigs,
    _normalize_signature,
    _signature_from_node,
)

# Optional: data-flow mining via schema_mine (sibling validate skill).
# Requires PySpark at runtime; gracefully absent in environments without it.
# _schema_mine_fn is always defined so tests can patch it even when unavailable.
_schema_mine_fn = None  # overridden below if importable
_DATA_MINING_AVAILABLE = False
try:
    _VALIDATE_SCRIPTS = _SCRIPT_DIR.parent.parent / "validate-pyspark-to-snowpark-connect" / "scripts"
    if str(_VALIDATE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_VALIDATE_SCRIPTS))
    from schema_mine import mine as _schema_mine_fn  # type: ignore[import]
    _DATA_MINING_AVAILABLE = True
except Exception:
    pass


# --- Filtering ------------------------------------------------------------

_EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules",
    ".idea", ".vscode", "dist", "build", "target", ".mypy_cache",
    ".scos-output", "Reports", "Output", "Logs", ".tox", ".cache",
    "scos_temp_input", "scos_temp_output",
}
_EXCLUDED_FILENAMES = {
    "analysis.json", "Issues.csv", "migration_state.json",
    "MigrationReadinessReport.html", "AssessmentIR.json",
}
_CODE_EXTS = {".py", ".scala", ".java", ".sql", ".r", ".ipynb"}
_INTERESTING_EXTS = _CODE_EXTS | {
    ".md", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".sh", ".xml",
    ".properties", ".conf", ".csv", ".tsv",
}
# Files we mine for data-source URLs (s3://, jdbc:, etc.) IN ADDITION to
# code files. Config and shell wrappers commonly carry the actual bucket /
# table / JDBC paths even when the .py code only references config keys.
# These extensions contribute to ``data_urls`` only — NOT to import graphs,
# Spark-API counts, or any other code metric.
_URL_REF_EXTS = {
    ".sh", ".bash", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".conf", ".properties", ".env", ".xml",
}


# --- Pattern catalog -------------------------------------------------------

_TECH_BY_EXT = {
    ".py": "Python",
    ".scala": "Scala",
    ".java": "Java",
    ".sql": "SQL",
    ".r": "R",
    ".ipynb": "Notebook",
}

# Friendly labels for the File Type Summary tiles
_EXT_LABELS = {
    ".py": ("Python", "Primary code"),
    ".scala": ("Scala", "Primary code"),
    ".java": ("Java", "Primary code"),
    ".sql": ("SQL", "Queries / DDL"),
    ".r": ("R", "Primary code"),
    ".ipynb": ("Notebook", "Notebooks"),
    ".md": ("Markdown", "Docs"),
    ".yaml": ("YAML", "Config"),
    ".yml": ("YAML", "Config"),
    ".json": ("JSON", "Config / data"),
    ".toml": ("TOML", "Config"),
    ".cfg": ("Config", "Config"),
    ".ini": ("INI", "Config"),
    ".sh": ("Shell", "Scripts"),
    ".xml": ("XML", "Config / data"),
    ".properties": ("Properties", "Config"),
    ".conf": ("Config", "Config"),
    ".csv": ("CSV", "Data"),
    ".tsv": ("TSV", "Data"),
}


# ---------------------------------------------------------------------------
# Workload archetype classification — loaded from llm_capabilities.json.
# ---------------------------------------------------------------------------
_WC_THRESHOLDS = _WC_CONFIG.get("thresholds", {})

# Fraction of code files touching external storage to be High Ingestion.
_WC_INGESTION_HIGH_THRESHOLD: float = float(_WC_THRESHOLDS.get("ingestion_high_pct", 0.30))

# Spark API calls per 10 lines of code to be High Compute.
_WC_COMPUTE_HIGH_PER_10_LOC: float = float(_WC_THRESHOLDS.get("compute_high_per_10_loc", 1.0))

# 2×2 archetype lookup: (ingestion_level, compute_level) → (name, color, profile).
_WC_ARCHETYPES: "dict[tuple[str, str], tuple[str, str, str]]" = {
    tuple(k.split(",")): (  # type: ignore[misc]
        v.get("name", ""),
        v.get("color", "yellow"),
        v.get("migration_profile", ""),
    )
    for k, v in _WC_CONFIG.get("archetypes", {}).items()
    if v.get("name")
}


# Per-pattern impact level read from llm_capabilities.json so that adding or
# reclassifying a pattern only requires updating the config, not this file.
_IMPACT_BY_NAME: "dict[str, str]" = {
    cap["name"]: cap.get("impact", "Medium")
    for cap in _LLM_CAPABILITIES.values()
    if cap.get("name")
}

_COMPLEX_PATTERNS = [
    # (name, regex, impact) — impact resolved from llm_capabilities.json via _IMPACT_BY_NAME.
    # Regexes live here (Python raw strings are cleaner than JSON-escaped equivalents).
    (name, regex, _IMPACT_BY_NAME.get(name, "Medium"))
    for name, regex in [
        ("RDD Operations",            r"\b(rdd|\.parallelize\(|sparkContext\b)"),
        ("Streaming",                 r"\b(readStream|writeStream|StreamingContext|kafka\.format)\b"),
        ("Spark ML / MLlib",          r"\b(pyspark\.ml|mllib|ML\.Pipeline)\b"),
        ("Hive / Catalog",            r"\b(hive|HiveContext|catalog\.listTables)\b"),
        ("Delta Lake",                r"\b(\.format\(\"delta\"\)|delta\.tables)\b"),
        ("Custom UDFs",               r"\b(udf|pandas_udf)\s*\("),
        ("Broadcast / Accumulator",   r"\b(broadcast\(|accumulator\()"),
        ("MapType / Complex JSON",    r"\b(MapType|from_json|to_json|get_json_object)\b"),
        ("JDBC Reads/Writes",         r"\bjdbc:[a-z]+:"),
        ("XML Parsing",               r"\b(spark-xml|com\.databricks\.spark\.xml)\b"),
    ]
]

# Reverse lookup: pattern name → EWI prefix, built dynamically from the
# capabilities config so the mapping lives in one place (llm_capabilities.json).
# Falls back to empty string for any pattern not yet registered in the config.
_PATTERN_EWI: dict[str, str] = {
    cap["name"]: prefix
    for prefix, cap in _LLM_CAPABILITIES.items()
    if cap.get("name")
}

# (label, regex). Order matters — first match wins per file.
_DATA_FORMAT_PATTERNS = [
    ("Parquet",  r"\.(read|write)\b.*\.parquet\("),
    ("Parquet",  r"\.format\(\s*[\"']parquet[\"']\s*\)"),
    ("Delta",    r"\.format\(\s*[\"']delta[\"']\s*\)"),
    ("Iceberg",  r"\.format\(\s*[\"']iceberg[\"']\s*\)"),
    ("Hudi",     r"\.format\(\s*[\"']hudi[\"']\s*\)"),
    ("JSON",     r"\.(read|write)\b.*\.json\("),
    ("JSON",     r"\.format\(\s*[\"']json[\"']\s*\)"),
    ("CSV",      r"\.(read|write)\b.*\.csv\("),
    ("CSV",      r"\.format\(\s*[\"']csv[\"']\s*\)"),
    ("ORC",      r"\.(read|write)\b.*\.orc\("),
    ("ORC",      r"\.format\(\s*[\"']orc[\"']\s*\)"),
    ("Avro",     r"\.format\(\s*[\"']avro[\"']\s*\)"),
    ("Text",     r"\.(read|write)\b.*\.text\("),
    ("Text",     r"\.format\(\s*[\"']text[\"']\s*\)"),
    ("Table",    r"\.(read\.table|saveAsTable|writeTo)\("),
    ("JDBC",     r"\.format\(\s*[\"']jdbc[\"']\s*\)"),
]

# URL schemes captured for the per-row data sources inventory.
_DATA_URL_RE = re.compile(
    r"(s3a?://[^\s\"'`)]+|hdfs://[^\s\"'`)]+|gs://[^\s\"'`)]+|abfss?://[^\s\"'`)]+|wasbs?://[^\s\"'`)]+|jdbc:[a-z]+://?[^\s\"'`)]+)",
    re.IGNORECASE,
)


def _is_placeholder_url(url: str) -> bool:
    """Filter out example/comment URLs that aren't real data references.

    The SCOS migration agent inlines comment examples like
    ``# session.file.put("s3://...", "@MY_STAGE/path")`` into migrated
    files; the regex catches the literal ``"s3://..."`` and surfaces it
    as a "real" path. Cheap heuristic: paths whose path portion is empty,
    is just ``...``, or starts with a placeholder marker like
    ``<``/``$`` are stripped.
    """
    if "://" not in url:
        return True
    _scheme, _, rest = url.partition("://")
    rest = rest.strip()
    if not rest:
        return True
    # Strip leading slash so jdbc:postgresql://host... and s3://bucket... align
    path = rest.lstrip("/")
    if not path or path in {"...", "…"}:
        return True
    # Detect "${VAR}", "<bucket>", "{{ env }}", etc. — any URL that's pure
    # template placeholder rather than a concrete reference.
    if path[0] in {"<", "$", "{"}:
        return True
    if path.startswith("..."):
        return True
    # Filter non-data URLs: code artifacts, config uploads, binaries
    _NON_DATA_HINTS = {
        "/artifacts/", "/artifact/", "/dist/", "/build/", "/deploy/",
        "/config/", "/configs/", "/conf/",
    }
    url_lower = url.lower()
    if any(hint in url_lower for hint in _NON_DATA_HINTS):
        return True
    # Filter URLs pointing to code files (not data)
    _CODE_EXTS = {".py", ".zip", ".jar", ".sh", ".yaml", ".yml", ".cfg", ".ini"}
    for ext in _CODE_EXTS:
        if url_lower.endswith(ext):
            return True
    return False
_URL_SCHEME_LABELS = {
    "s3": "S3", "s3a": "S3",
    "hdfs": "HDFS",
    "gs": "GCS",
    "abfs": "ADLS", "abfss": "ADLS",
    "wasb": "WASB", "wasbs": "WASB",
    "jdbc": "JDBC",
}


_SPARK_API_RE = re.compile(
    r"\b(?:spark\.|sparkSession\.|sc\.|\.read\.|\.write\.|"
    r"DataFrame|withColumn|groupBy|agg|filter|join|window|"
    r"selectExpr|union|coalesce|repartition|cache|persist)\b"
)

# SQL-specific signals for .sql files — these files get zero hits from the
# Python-oriented patterns above, which caused SQL-heavy workloads to be
# incorrectly classified as Low-Compute / Low-Ingestion.
#
# _SQL_INGESTION_RE: a SQL file performs persistent I/O (ingestion) when it
# contains data-manipulation statements that write to or create tables/stages.
# Pure SELECT-only scripts are pure transforms and are NOT counted as ingestion.
_SQL_INGESTION_RE = re.compile(
    r"\b(?:"
    r"INSERT\s+(?:INTO|OVERWRITE)"
    r"|CREATE\s+(?:OR\s+REPLACE\s+)?(?:EXTERNAL\s+)?(?:TEMPORARY\s+)?TABLE"
    r"|COPY\s+INTO"
    r"|MERGE\s+INTO"
    r"|CREATE\s+(?:OR\s+REPLACE\s+)?(?:EXTERNAL\s+)?STAGE"
    r")\b",
    re.IGNORECASE,
)

# _SQL_COMPUTE_RE: counts SQL transformation operations that are semantically
# equivalent to the Spark DataFrame API calls counted by _SPARK_API_RE.
# Each match represents meaningful compute work: a join, aggregation, window
# function, CTE definition, conditional branch, or set operation.
_SQL_COMPUTE_RE = re.compile(
    r"\b(?:"
    r"JOIN"                            # any JOIN type
    r"|GROUP\s+BY"                     # aggregation
    r"|OVER\s*\("                      # window function
    r"|HAVING"                         # post-aggregation filter
    r"|CASE\s+WHEN"                    # conditional logic
    r"|WITH\s+\w"                      # CTE definition
    r"|UNION(?:\s+ALL)?"              # set union
    r"|INTERSECT"                      # set intersection
    r"|EXCEPT"                         # set difference
    r"|PIVOT"                          # pivot/unpivot
    r"|UNPIVOT"
    r")\b",
    re.IGNORECASE,
)

# Curated import-name → Snowpark-Connect compatibility hints. A best-effort
# allowlist for the Third-Party Library Summary table — we'd rather be
# conservative ("Verify") than promise full support.
_LIBRARY_SUPPORT = {
    "numpy": True, "pandas": True, "scipy": True, "scikit-learn": True,
    "sklearn": True, "matplotlib": True, "pyarrow": True, "boto3": True,
    "requests": True, "snowflake-snowpark-python": True,
    "snowflake": True, "pydantic": True, "jinja2": True,
    "pyspark": False,
    "pyspark.ml": False, "pyspark.mllib": False, "pyspark.streaming": False,
    "delta": False, "deltalake": False, "spark-xml": False,
    "spark-avro": True, "spark-sql-kafka-0-10": False,
}

# Static Spark API category mapping for the SMA-style summary tile.
_SPARK_CATEGORIES = {
    "DataFrame": (r"\b(DataFrame|withColumn|groupBy|agg|filter|join|window|select|union|coalesce|repartition)\b", True),
    "SQL":       (r"\b(spark\.sql\(|sparkSession\.sql\()", True),
    "I/O":       (r"\.(read|write)\.", True),
    "RDD":       (r"\b(rdd|sparkContext|parallelize)\b", False),
    "Streaming": (r"\b(readStream|writeStream)\b", False),
    "MLlib":     (r"\b(pyspark\.ml|mllib)\b", False),
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def scan(
    workload_dir: Path,
    project: str = "unknown-project",
    language: str = "python",
    session: "Any | None" = None,
) -> Assessment:
    """Walk ``workload_dir`` and return a partial :class:`Assessment`.

    The Assessment populates only the codebase-derived fields; analyzer-only
    fields (issues, migration_categories, readiness scores driven by findings)
    are left at their defaults so the merge in :mod:`render_assessment` picks
    up the analyzer's values.

    ``session`` — an optional Snowpark ``Session``. When provided, the
    Anaconda-package list is fetched authoritatively from Snowflake and
    the user-level on-disk cache is refreshed. When omitted, the scanner
    reads from that cache (if fresh) or from the repo-committed bundled
    default. See :func:`file_info._load_anaconda_snapshot` for details.
    """
    workload_dir = workload_dir.resolve()
    if not workload_dir.exists():
        raise SystemExit(f"Workload dir does not exist: {workload_dir}")

    paths = list(_iter_source_files(workload_dir))
    files_info = [_inspect_file(p, workload_dir) for p in paths]

    # ---- File Type Summary tiles --------------------------------------
    ext_counter: Counter[str] = Counter()
    ext_lines: dict[str, int] = defaultdict(int)
    for info in files_info:
        ext_counter[info["ext"]] += 1
        ext_lines[info["ext"]] += info["lines"]

    file_types = [
        FileTypeRow(
            extension=_EXT_LABELS.get(ext, (ext.lstrip("."), ""))[0],
            count=count,
            lines=ext_lines[ext],
            significance=_EXT_LABELS.get(ext, ("", ""))[1],
        )
        for ext, count in ext_counter.most_common()
    ]

    code_files = [info for info in files_info if info["ext"] in _CODE_EXTS]
    total_loc = sum(info["lines"] for info in code_files)

    # ---- Library imports + data formats per file ---------------------
    all_imports: Counter[str] = Counter()
    data_formats: dict[str, dict] = defaultdict(
        lambda: {"reads": 0, "writes": 0, "paths": [], "files": []}
    )
    data_urls: dict[str, list[tuple[str, str]]] = defaultdict(list)
    pattern_hits: Counter[str] = Counter()
    pattern_files: dict[str, set[str]] = defaultdict(set)
    spark_api_count = 0
    spark_category_counts: dict[str, dict] = defaultdict(lambda: {"supported": 0, "unsupported": 0})

    for info in code_files:
        for imp in info["imports"]:
            # info["imports"] holds the full dotted module path; for the
            # third-party library counter we bucket by top-level package
            # (e.g. "common.utils" -> "common", "pyspark.sql" -> "pyspark").
            # Relative imports ("." / ".foo") are intra-project and excluded.
            root = _import_root(imp)
            if root:
                all_imports[root] += 1
        for fmt, kind in info["data_formats"]:
            if kind == "read":
                data_formats[fmt]["reads"] += 1
            elif kind == "write":
                data_formats[fmt]["writes"] += 1
        for scheme, url in info["data_urls"]:
            label = _URL_SCHEME_LABELS.get(scheme.lower(), scheme.upper())
            data_urls[label].append((url, info["name"]))
        for pname, count in info["patterns"].items():
            pattern_hits[pname] += count
            if count:
                pattern_files[pname].add(info["name"])
        spark_api_count += info["spark_api"]
        for cat, counts in info["spark_categories"].items():
            spark_category_counts[cat]["supported"] += counts["supported"]
            spark_category_counts[cat]["unsupported"] += counts["unsupported"]

    # Second pass: mine config / shell wrappers for data-source URLs that
    # the .py code only references symbolically. Real workloads keep
    # ``s3://bucket/...`` paths in ``spark-submit.sh`` and config JSON/YAML,
    # so a code-only pass produces a paths-empty inventory.
    # We only contribute to ``data_urls`` (NOT imports, NOT spark_api counts)
    # so non-code files don't distort the code-side metrics.
    # Also capture surrounding text for format inference from non-code files.
    url_context: dict[str, str] = {}
    for info in files_info:
        if info["ext"] in _CODE_EXTS or info["ext"] not in _URL_REF_EXTS:
            continue
        path_obj = workload_dir / info["rel_path"]
        try:
            text = path_obj.read_text(errors="ignore")
        except OSError:
            text = ""
        for scheme, url in info["data_urls"]:
            label = _URL_SCHEME_LABELS.get(scheme.lower(), scheme.upper())
            data_urls[label].append((url, info["name"]))
            # Capture surrounding context for format inference
            if url not in url_context and text:
                idx = text.find(url)
                if idx >= 0:
                    ctx_start = max(0, idx - 150)
                    ctx_end = min(len(text), idx + len(url) + 80)
                    url_context[url] = text[ctx_start:ctx_end]

    # Strip placeholder URLs (``s3://...``, ``jdbc:...``-with-no-host etc.)
    # that the analyzer typically inlines into comments as examples. These
    # crowd out the real paths and offer no signal.
    for label, refs in list(data_urls.items()):
        data_urls[label] = [(u, f) for u, f in refs if not _is_placeholder_url(u)]
        if not data_urls[label]:
            del data_urls[label]

    # Build connection+format rows. The reference design groups ALL entries by
    # connection (S3, HDFS, GCS, etc.) with format as a sub-dimension.
    # Strategy:
    #   1. URL-based: connection from scheme, format from path extension
    #   2. Code-based: format from regex, connection inferred from URLs in same
    #      file. Falls back to dominant connection if available.
    def _format_from_url(url: str, context: str = "") -> str:
        """Thin wrapper around file_info._format_from_url; returns 'Undefined' when no match."""
        return _fi_format_from_url(url, context) or "Undefined"

    # Map each file to its detected connection(s) and format(s)
    file_connections: dict[str, str] = {}
    for label, refs in data_urls.items():
        for _url, fname in refs:
            if fname not in file_connections:
                file_connections[fname] = label

    file_formats: dict[str, str] = {}
    for info in code_files:
        fmts = [fmt for fmt, _ in info["data_formats"]]
        if fmts:
            file_formats[info["name"]] = fmts[0]

    # Dominant connection (most common across the project)
    dominant_connection = ""
    if data_urls:
        dominant_connection = max(data_urls.keys(), key=lambda k: len(data_urls[k]))

    _WRITE_PATH_HINTS = {"output", "write", "sink", "dest", "target"}
    writer_files = {
        info["name"] for info in code_files
        if any(k == "write" for _, k in info["data_formats"])
        or "writer" in info["name"].lower()
    }

    # keyed by (connection, format) -> bucket. ``paths`` / ``files`` collect
    # every occurrence for back-compat with older IR consumers; the
    # ``read_*`` / ``write_*`` lists split those by direction so the
    # Sources vs Targets table doesn't over-report by mixing them.
    combined_sources: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "reads": 0, "writes": 0,
            "paths": [], "files": [],
            "read_paths": [], "read_files": [],
            "write_paths": [], "write_files": [],
        }
    )

    # URL-detected sources: connection from scheme, format from extension,
    # surrounding text context, or the file's code-detected format.
    for label, refs in data_urls.items():
        unique_refs = list(dict.fromkeys(refs))[:25]
        for url, fname in unique_refs:
            context = url_context.get(url, "")
            inferred_fmt = _format_from_url(url, context)
            # If still Undefined, try the file's code-detected format
            if inferred_fmt == "Undefined" and fname in file_formats:
                inferred_fmt = file_formats[fname]
            # Skip entries where format remains completely unknown
            if inferred_fmt == "Undefined":
                continue
            key = (label, inferred_fmt)
            combined_sources[key]["paths"].append(url)
            combined_sources[key]["files"].append(fname)
            # Determine read vs write from file context or URL path
            url_lower = url.lower()
            is_write = (
                fname in writer_files
                or any(hint in url_lower for hint in _WRITE_PATH_HINTS)
            )
            if is_write:
                combined_sources[key]["writes"] += 1
                combined_sources[key]["write_paths"].append(url)
                combined_sources[key]["write_files"].append(fname)
            else:
                combined_sources[key]["reads"] += 1
                combined_sources[key]["read_paths"].append(url)
                combined_sources[key]["read_files"].append(fname)

    # Code-detected formats: associate with the connection found in the same
    # file, or the dominant project connection, or "Local" as last resort.
    for info in code_files:
        file_conn = file_connections.get(info["name"], dominant_connection or "Local")
        for fmt, kind in info["data_formats"]:
            key = (file_conn, fmt)
            if kind == "read":
                combined_sources[key]["reads"] += 1
                combined_sources[key]["read_files"].append(info["name"])
            else:
                combined_sources[key]["writes"] += 1
                combined_sources[key]["write_files"].append(info["name"])

    data_sources = [
        DataSourceRow(
            connection=conn,
            format=fmt,
            reads=bucket["reads"],
            writes=bucket["writes"],
            paths=list(dict.fromkeys(bucket["paths"]))[:25],
            files=list(dict.fromkeys(bucket["files"]))[:25],
            read_paths=list(dict.fromkeys(bucket["read_paths"]))[:25],
            read_files=list(dict.fromkeys(bucket["read_files"]))[:25],
            write_paths=list(dict.fromkeys(bucket["write_paths"]))[:25],
            write_files=list(dict.fromkeys(bucket["write_files"]))[:25],
            supported=fmt.lower() not in {"delta", "hudi"},
        )
        for (conn, fmt), bucket in sorted(
            combined_sources.items(), key=lambda kv: -(kv[1]["reads"] + kv[1]["writes"])
        )
    ]

    complex_patterns = [
        ComplexPatternRow(
            pattern=name,
            occurrences=str(pattern_hits[name]),
            impact=impact,
            files_affected=len(pattern_files.get(name, set())),
        )
        for name, _, impact in _COMPLEX_PATTERNS
        if pattern_hits.get(name, 0) > 0
    ]

    # ---- Third-party libraries -----------------------------------------
    # Internal packages: file basenames (without .py extension), top-level
    # dirs, AND all subdirectory names that are Python packages.
    # We also add every .py file basename so that sibling-directory imports
    # like ``import helper_function`` (from helper_function.py two dirs up)
    # are correctly classified as internal rather than "unsupported".
    internal_modules: set[str] = set()
    for info in code_files:
        # Bare module name (e.g. "helper_function" from "helper_function.py")
        internal_modules.add(info["name"].removesuffix(".py"))
        parts = Path(info["rel_path"]).parts
        # Add every directory component as a potential package name
        for part in parts[:-1]:
            internal_modules.add(part)

    # Authoritative Python stdlib module names, delivered by CPython since
    # 3.10 via ``sys.stdlib_module_names``. Falls back to a hand-curated
    # subset only if the attribute is missing on the runtime interpreter
    # (older Pythons); the curated set covered common cases but missed
    # long-tail modules like ``ast`` / ``types`` / ``asyncio``, which
    # produced false positives on the AR-required column.
    _STDLIB_MODULES = frozenset(
        getattr(sys, "stdlib_module_names", None)
        or {
            "os", "sys", "re", "math", "json", "abc", "io", "copy", "ast",
            "collections", "itertools", "functools", "typing", "pathlib",
            "logging", "datetime", "time", "traceback", "importlib",
            "contextlib", "hashlib", "uuid", "tempfile", "shutil",
            "subprocess", "threading", "multiprocessing", "argparse",
            "dataclasses", "enum", "warnings", "inspect", "string",
            "textwrap", "struct", "operator", "pickle", "shelve",
            "csv", "configparser", "unittest", "pdb", "glob",
            "types", "keyword", "builtins", "asyncio", "random", "secrets",
            "bisect", "heapq", "email", "queue", "signal", "array",
            "decimal", "fractions", "statistics", "sqlite3",
            "zlib", "gzip", "bz2", "lzma", "tarfile", "zipfile",
            "xml", "html", "webbrowser",
        }
    )

    anaconda_pkgs = _load_anaconda_snapshot(session=session)

    # Test-only libraries: dev/test frameworks not deployed to Snowflake.
    # Presence does NOT mean "unsupported at runtime" — these libraries are
    # never shipped with the production workload.
    _TEST_ONLY_LIBS: frozenset[str] = frozenset({
        "pytest", "pytest_mock", "pytest_asyncio", "pytest_cov",
        "pytest_xdist", "pytest_benchmark", "pytest_timeout",
        "hypothesis", "mock", "unittest", "responses", "faker",
        "factory_boy", "freezegun", "moto", "deepdiff", "testcontainers",
    })

    # Migration-scope replacement map: library → Snowflake/SCOS equivalent.
    # Used to build the "not_supported_reason" field in the popover.
    _MIGRATION_SCOPE_REPLACEMENT: dict[str, str] = {
        "pyspark": "snowflake.snowpark (Snowpark Connect for Spark)",
        "pyspark.sql": "snowflake.snowpark",
        "dbutils": "Snowflake equivalents (stages, secrets, session env)",
        "databricks": "Snowflake-native services",
        "databricks.sdk": "Snowflake Python SDK / Snowpark",
        "databricks.connect": "Snowpark Connect (SCOS)",
        "databricks.sql": "Snowflake Connector for Python",
        "databricks.koalas": "Snowpark pandas (modin on Snowflake)",
        "delta": "Snowflake native tables (with Time Travel / Iceberg)",
        "delta.tables": "Snowflake native tables",
        "deltalake": "Snowflake native tables",
        "koalas": "Snowpark pandas",
        "pyspark.pandas": "Snowpark pandas",
    }

    def _classify_lib(name: str) -> tuple[bool, str, str, str]:
        """Return (snowpark_supported, classification, role, not_supported_reason).

        ``role`` is one of:
          * ``"internal"``           — defined inside this workload.
          * ``"stdlib"``             — Python standard library.
          * ``"migration-scope"``    — rewritten away by the migration tool.
          * ``"test-only"``          — dev/test framework, not deployed.
          * ``"runtime-third-party"``— genuinely external runtime library.

        ``snowpark_supported`` is ``True`` when no user action is needed:
        stdlib (built-in), in Anaconda channel, migration-scope (tool handles it),
        test-only (not deployed), or internal (workload's own code).

        ``not_supported_reason`` explains the "No" badge in the report popover.
        """
        # Internal: any module whose name matches a .py file or package dir
        # inside this workload (catches sibling-dir cross-imports).
        if name in internal_modules:
            return True, "internal", "internal", ""
        # Test-only: dev tools, never deployed to Snowflake runtime.
        if name in _TEST_ONLY_LIBS:
            return True, "unknown", "test-only", ""
        # Migration-scope: rewritten away by the tool.
        if is_migration_scope(name):
            repl = _MIGRATION_SCOPE_REPLACEMENT.get(name.split(".")[0], "")
            if not repl:
                # Try root-level key lookup for dotted names
                repl = _MIGRATION_SCOPE_REPLACEMENT.get(name, "")
            if repl:
                reason = f"Rewritten by the migration tool → {repl}"
            else:
                reason = "Rewritten by the migration tool. See SCOS migration guidance."
            return False, "migration-scope", "migration-scope", reason
        # Python stdlib: built-in in every Snowflake Python runtime.
        if name in _STDLIB_MODULES:
            return True, "stdlib", "stdlib", ""
        # Check Snowflake Anaconda channel — the authoritative signal.
        key = name.lower().replace("-", "_")
        if key in anaconda_pkgs:
            return True, "supported", "runtime-third-party", ""
        dist_names = _IMPORT_TO_DIST.get(name.lower(), frozenset())
        if dist_names & anaconda_pkgs:
            return True, "supported", "runtime-third-party", ""
        # Not in Anaconda — needs Artifact Repository or alternative.
        reason = (
            "Not in Snowflake's built-in Anaconda channel. "
            "Verify availability on PyPI and install via the Artifact Repository, "
            "or find a Snowflake-native alternative."
        )
        return False, "unsupported", "runtime-third-party", reason

    third_party_libs = [
        ThirdPartyLibRow(
            name=name,
            import_count=count,
            snowpark_supported=supported,
            classification=classification,
            role=role,
            not_supported_reason=reason,
        )
        for name, count in all_imports.most_common()
        for supported, classification, role, reason in [_classify_lib(name)]
        if role != "internal"  # filter out internal modules from the third-party table
    ]

    # ---- Spark API summary -----------------------------------------------
    spark_api_by_category = [
        SparkApiByCategory(
            category=cat,
            supported=cnts["supported"],
            unsupported=cnts["unsupported"],
        )
        for cat, cnts in sorted(spark_category_counts.items(), key=lambda kv: -sum(kv[1].values()))
        if (cnts["supported"] + cnts["unsupported"]) > 0
    ]
    total_supported = sum(c["supported"] for c in spark_category_counts.values())
    total_unsupported = sum(c["unsupported"] for c in spark_category_counts.values())
    total_api = total_supported + total_unsupported
    spark_api_by_status = [
        SparkApiByStatus(
            status="Supported",
            count=total_supported,
            percent=round(100 * total_supported / total_api, 1) if total_api else 0.0,
        ),
        SparkApiByStatus(
            status="NotSupported",
            count=total_unsupported,
            percent=round(100 * total_unsupported / total_api, 1) if total_api else 0.0,
        ),
    ]

    # ---- File / technology summaries -----------------------------------
    file_summary_by_type = [
        FileSummaryByType(
            type=_EXT_LABELS.get(ext, (ext.lstrip("."), ""))[0],
            files=count,
            lines=ext_lines[ext],
            percent=round(100 * count / len(files_info), 1) if files_info else 0.0,
        )
        for ext, count in ext_counter.most_common()
    ]
    tech_counter: Counter[str] = Counter()
    for info in code_files:
        tech_counter[_TECH_BY_EXT.get(info["ext"], "Other")] += 1
    file_summary_by_technology = [
        FileSummaryByTechnology(technology=tech, file_count=cnt)
        for tech, cnt in tech_counter.most_common()
    ]

    # ---- Dependency graph -----------------------------------------------
    import_edges, dependents_count = _build_dependency_graph(code_files)

    # Data-flow edges (writer→reader) mined by schema_mine.
    # These are kept separate from import_edges so the two graphs remain
    # visually distinct.  The combined list is used ONLY for topology and
    # isolation analysis so data-linked orphan files join the main cluster.
    data_edges_kind, unresolved_dynamic_imports, unresolved_data_edges = (
        _build_data_dep_edges(code_files, workload_dir)
    )
    # Legacy 2-tuple projection: many downstream helpers (isolation, wave
    # topology, cycle detection) expect ``(src, tgt)`` pairs. Factory
    # dispatch edges DO participate in these — they're still data flow
    # from the candidate file into the orchestrator, so they belong in the
    # combined edge list for isolation/topology purposes.
    data_edges: list[tuple[str, str]] = [(a, b) for (a, b, _k) in data_edges_kind]
    data_edge_set: frozenset[tuple[str, str]] = frozenset(data_edges)
    if data_edges:
        existing_import: set[tuple[str, str]] = set(import_edges)
        for de in data_edges:
            if de not in existing_import:
                dependents_count[de[1]] = dependents_count.get(de[1], 0) + 1

    # Combined edge list for wave topology and connected-component analysis.
    combined_edges = list(import_edges)
    if data_edges:
        import_set = set(import_edges)
        for de in data_edges:
            if de not in import_set:
                combined_edges.append(de)

    most_depended_files = [
        DependencyFile(path=path, name=Path(path).name, metric=cnt)
        for path, cnt in sorted(dependents_count.items(), key=lambda kv: -kv[1])[:10]
        if cnt > 0
    ]
    # Approximate "complexity" with raw line count when no better signal exists.
    most_complex_files = [
        DependencyFile(path=info["rel_path"], name=info["name"], metric=info["lines"])
        for info in sorted(code_files, key=lambda i: -i["lines"])[:10]
    ]
    cross_module_dependencies = sum(1 for (a, b) in combined_edges if _module_of(a) != _module_of(b))
    migration_waves = _topological_waves(code_files, combined_edges)

    # Pre-laid-out diagrams. Coordinates are baked into the IR so the
    # template can emit SVG with no layout math of its own. Per-file
    # status on graph nodes stays at the scanner's default and is
    # backfilled from the merged ``files`` list in ``Assessment.merge``.
    out_edges_full: dict[str, set[str]] = defaultdict(set)
    for a, b in combined_edges:
        out_edges_full[a].add(b)
    cycles = _find_cycles({info["rel_path"] for info in code_files}, out_edges_full)
    circular_dependencies = _circular_dependencies_from(cycles, code_files)
    # Import DAG: shows only static import edges.  Combined edges are used for
    # topology so data-linked files are not treated as orphans.
    dependency_graph = _build_unified_dependency_graph(code_files, import_edges)
    # Data DAG: purpose-built left-to-right chain layout with a Framework
    # bounding box for base classes / utilities / __init__.py, plus external
    # source & sink pseudo-nodes derived from the reader's / writer's mined
    # I/O. Falls back to None only when there are no dynamic-import-derived
    # pipeline stages AND no writer→reader data edges (i.e. nothing to show).
    _data_config_pool = _load_config_pool(workload_dir)
    _data_config_data = _load_config_data(workload_dir)
    _data_entry_points = _load_entry_points_registry(workload_dir)
    # Re-mine dynamic imports (schema_mine is cheap AST work) so we can lay
    # out the chain deterministically. The scanner's data-edge pass already
    # ran, but its per-file sites aren't returned here — recomputing keeps
    # `_build_data_flow_graph` a pure function of its inputs.
    _sites_by_file: dict[str, list[dict]] = {}
    for info in code_files:
        abs_path = str(workload_dir / info["rel_path"])
        io = _mine_file_io_and_imports(abs_path, _data_config_pool)
        if io is None:
            continue
        _s, _k, _dyn = io
        if _dyn:
            _sites_by_file[info["rel_path"]] = _dyn
    data_dependency_graph = _build_data_flow_graph(
        code_files,
        data_edges_kind,
        _sites_by_file,
        _data_config_data,
        _data_config_pool,
        workload_dir,
        _data_entry_points,
        import_edges,
    )
    # Partition by connected component using combined edges.
    # Files connected only via data flow are promoted out of the isolated list.
    (
        isolated_modules,
        largest_component_size,
        main_cluster,
        package_marker_count,
    ) = _build_isolated_modules(code_files, combined_edges)
    wave_graph = _build_wave_graph(migration_waves)

    # ---- Per-file rows --------------------------------------------------
    files_rows = [
        FileCompatibilityRow(
            path=info["rel_path"],
            name=info["name"],
            technology=_TECH_BY_EXT.get(info["ext"], "Other"),
            lines=info["lines"],
            spark_usages=info["spark_api"],
            issues=0,  # filled by transform_analysis
            status="High",
        )
        for info in code_files
    ]

    # ---- Additional Discovery tab --------------------------------------
    # Legacy operation counts (kept for backward compatibility).
    io_ops = sum(b["reads"] + b["writes"] for b in data_formats.values())
    transform_ops = max(0, spark_api_count - io_ops)

    # File-based metrics — more accurate than raw operation counts because a
    # single large file cannot skew the result.
    _total_files = len(code_files)
    _ingestion_file_count = sum(1 for info in code_files if info["data_formats"])
    _ingestion_file_pct = _ingestion_file_count / _total_files if _total_files else 0.0
    _spark_per_10_loc = (spark_api_count / total_loc * 10) if total_loc else 0.0

    # Classify each axis independently using absolute, LOC-normalised thresholds.
    _ingestion_level = "High" if _ingestion_file_pct >= _WC_INGESTION_HIGH_THRESHOLD else "Low"
    _compute_level = "High" if _spark_per_10_loc >= _WC_COMPUTE_HIGH_PER_10_LOC else "Low"
    _archetype_name, _archetype_color, _migration_profile = _WC_ARCHETYPES[(_ingestion_level, _compute_level)]

    workload_classification = WorkloadClassification(
        classification=_archetype_name,
        ingestion_level=_ingestion_level,
        compute_level=_compute_level,
        archetype_color=_archetype_color,
        migration_profile=_migration_profile,
        ingestion_file_count=_ingestion_file_count,
        ingestion_file_pct=round(_ingestion_file_pct, 4),
        total_files=_total_files,
        spark_per_10_loc=round(_spark_per_10_loc, 2),
        # Legacy fields — kept so existing IRs and merge logic continue to work.
        io_operations=io_ops,
        transform_operations=transform_ops,
        description=(
            f"{_ingestion_file_count} of {_total_files} file(s) "
            f"({_ingestion_file_pct:.0%}) perform external reads or writes; "
            f"average of {_spark_per_10_loc:.1f} Spark API call(s) per 10 LOC."
        ),
    )

    has_xml = pattern_hits.get("XML Parsing", 0) > 0 or any("xml" in i for i in all_imports)
    has_custom_validation = any(
        "validator" in info["rel_path"].lower() or "validation" in info["rel_path"].lower()
        for info in code_files
    )
    project_type = _classify_project_type(
        pattern_hits=pattern_hits,
        third_party_libs=third_party_libs,
        code_files=code_files,
        all_imports=all_imports,
        has_xml=has_xml,
        has_custom_validation=has_custom_validation,
    )

    # Code churn intentionally left at defaults here — ``transform_analysis``
    # populates it from analyzer-derived evidence (readiness score + line spans).
    # We surface the scan-derived line tally separately in the file_summary so
    # nothing is hidden.

    # Sources & Sinks Inventory (Additional Discovery tab). Rebuilt to
    # aggregate over the SAME ``combined_sources`` dict that powers the
    # Overview tab's ``data_sources`` table — one row per (connection,
    # format, direction), so a viewer flipping between the two tabs sees
    # numbers that reconcile. Previously this was three hardcoded
    # categories with their own aggregation logic, which drifted from
    # Overview and confused reviewers.
    sources_sinks_inventory: list[SourceSinkInventoryRow] = []
    for (conn, fmt), bucket in sorted(
        combined_sources.items(), key=lambda kv: -(kv[1]["reads"] + kv[1]["writes"])
    ):
        if bucket["reads"] > 0:
            sources_sinks_inventory.append(
                SourceSinkInventoryRow(
                    direction="Source",
                    category=f"{conn} {fmt}".strip() if conn else fmt,
                    occurrences=bucket["reads"],
                    detected=True,
                )
            )
        if bucket["writes"] > 0:
            sources_sinks_inventory.append(
                SourceSinkInventoryRow(
                    direction="Sink",
                    category=f"{conn} {fmt}".strip() if conn else fmt,
                    occurrences=bucket["writes"],
                    detected=True,
                )
            )
    # Streaming and JDBC categories are still worth surfacing when
    # regex-detected but not captured by the URL/format scan (e.g.
    # ``readStream`` without a scheme literal).
    _streaming_hits = pattern_hits.get("Streaming", 0)
    if _streaming_hits > 0 and not any(
        r.direction == "Source" and "kafka" in r.category.lower()
        for r in sources_sinks_inventory
    ):
        sources_sinks_inventory.append(
            SourceSinkInventoryRow(
                direction="Source",
                category="Streaming (Kafka)",
                occurrences=_streaming_hits,
                detected=True,
            )
        )
    _jdbc_hits = pattern_hits.get("JDBC Reads/Writes", 0)
    if _jdbc_hits > 0 and not any(
        r.direction == "Sink" and "jdbc" in r.category.lower()
        for r in sources_sinks_inventory
    ):
        sources_sinks_inventory.append(
            SourceSinkInventoryRow(
                direction="Sink",
                category="JDBC Write",
                occurrences=_jdbc_hits,
                detected=True,
            )
        )

    # high_risk_formats — driven by llm_capabilities.json entries with high_risk_label set.
    # detection_includes_imports=true means use has_xml (pattern + import scan); else use pattern count.
    _pattern_detected: dict[str, bool] = {
        name: pattern_hits.get(name, 0) > 0 for name, _, _ in _COMPLEX_PATTERNS
    }
    _pattern_detected["XML Parsing"] = has_xml  # XML: also count import-based detection

    high_risk_formats = [
        HighRiskFormatRow(
            format=cap["high_risk_label"],
            risk=cap.get("impact", "High"),
            detail=f"{pattern_hits.get(cap.get('name', ''), 0)} {cap['high_risk_label']} references detected.",
            recommended_action=cap.get("recommended_action") or "",
        )
        for cap in _LLM_CAPABILITIES.values()
        if cap.get("high_risk_label") and _pattern_detected.get(cap.get("name", ""), False)
    ]

    # common_refactors — driven by llm_capabilities.json entries with refactor_label set.
    # One row per entry; checked=True when the pattern was detected in this codebase.
    common_refactors = [
        RefactorCheckRow(
            name=cap["refactor_label"],
            description=(
                cap.get("recommended_action") or cap.get("name", "")
                if pattern_hits.get(cap.get("name", ""), 0) > 0
                else f"No {cap.get('name', '').lower()} usage detected"
            ),
            checked=pattern_hits.get(cap.get("name", ""), 0) > 0,
        )
        for cap in _LLM_CAPABILITIES.values()
        if cap.get("refactor_label")
    ]

    # ---- Workload tile values + executive summary ---------------------
    _fallback_ext = ".scala" if language == "scala" else (".java" if language == "java" else ".py")
    primary_ext, _ = (ext_counter.most_common(1) or [(_fallback_ext, 0)])[0]
    workload = WorkloadSummary(
        files_scanned=len(files_info),
        lines_of_code=total_loc,
        file_dependencies=len(combined_edges),
        library_imports=sum(all_imports.values()),
        code_file_count=len(code_files),
        primary_language=_TECH_BY_EXT.get(primary_ext, "Python"),
    )

    compatibility = CompatibilitySummary(
        supported_usages=total_supported,
        not_supported_usages=total_unsupported,
        highly_compatible_files=sum(1 for f in files_rows if f.status == "High"),
        total_code_files=len(files_rows),
    )

    high = sum(1 for f in files_rows if f.status == "High")
    medium = sum(1 for f in files_rows if f.status == "Medium")
    low = sum(1 for f in files_rows if f.status == "Low")
    # Pre-merge counts only — every file looks "High" here because analyzer
    # issues haven't been joined in yet. ``Assessment.merge`` re-renders
    # these cards from the merged file table so the wave plan, per-file
    # readiness table, and Migration Approach (Summary) all agree.
    migration_stages = render_migration_stages(high=high, medium=medium, low=low)

    # ---- File Information rows (per-file data-flow & governance) -------
    # Strictly local: what does THIS file touch, and what does it need to
    # run in Snowflake. No DAG-inherited lineage — that lives in the
    # data DAG diagram. See file_info.py for the detection logic.
    #
    # Data DAG-derived sink locations: for workloads that build their I/O
    # paths from configuration at runtime (Kipawa-style class-based ETL),
    # the source code has no literal URL to extract. ``schema_mine`` has
    # already resolved the config-driven paths into external sink/source
    # pseudo-nodes on the data DAG — we mine those here to fill in
    # target_location that the code alone can't reveal.
    dag_sinks_by_file: dict[str, list[str]] = {}
    dag_sources_by_file: dict[str, list[str]] = {}
    if data_dependency_graph is not None:
        node_by_id = {n.id: n for n in data_dependency_graph.nodes}
        for edge in data_dependency_graph.edges:
            src_node = node_by_id.get(edge.source)
            tgt_node = node_by_id.get(edge.target)
            # Edge into an external sink → the source-side file is a writer;
            # the sink node's label is the resolved target location.
            if tgt_node is not None and tgt_node.id.startswith("ext:sink:"):
                loc = getattr(tgt_node, "full_label", None) or getattr(tgt_node, "label", "") or ""
                if loc:
                    dag_sinks_by_file.setdefault(edge.source, []).append(loc)
            # Edge from an external source → the target-side file is a reader;
            # record the source location for future use (currently informational).
            if src_node is not None and src_node.id.startswith("ext:source:"):
                loc = getattr(src_node, "full_label", None) or getattr(src_node, "label", "") or ""
                if loc:
                    dag_sources_by_file.setdefault(edge.target, []).append(loc)

    file_info_rows: list[FileInfoRow] = []
    for info in code_files:
        # Re-read Python sources so we can AST-scan for EAI signals. Cheap
        # for the workload sizes this tool targets; done here (not in the
        # main _inspect_file pass) to avoid holding every file's source
        # in memory across the whole scan.
        py_source = ""
        if info["ext"] in (".py", ".sql", ".scala", ".java"):
            abs_path = workload_dir / info["rel_path"]
            try:
                py_source = abs_path.read_text(errors="ignore")
            except (OSError, UnicodeDecodeError):
                py_source = ""
        # Normalize URL schemes to display labels here (``s3`` → ``S3``,
        # ``jdbc`` → ``JDBC``, …) so ``file_info`` only sees the labels
        # its ``_SOURCE_LABEL_BY_SCHEME`` map is keyed on.
        normalized_urls = [
            (_URL_SCHEME_LABELS.get(scheme.lower(), scheme.upper()), url)
            for (scheme, url) in info["data_urls"]
        ]
        row_dict = build_file_info_row(
            name=info["name"],
            path=info["rel_path"],
            ext=info["ext"],
            lines=info["lines"],
            source=py_source,
            imports=info["imports"],
            data_urls=normalized_urls,
            data_formats=info["data_formats"],
            spark_api=info["spark_api"],
            internal_modules=internal_modules,
            stdlib_modules=_STDLIB_MODULES,
            anaconda_packages=anaconda_pkgs,
            dag_sink_locations=dag_sinks_by_file.get(info["rel_path"], []),
            dag_source_locations=dag_sources_by_file.get(info["rel_path"], []),
        )
        file_info_rows.append(FileInfoRow(**row_dict))

    return Assessment(
        metadata=AssessmentMetadata(project=project, mode="CODEBASE"),
        workload=workload,
        file_types=file_types,
        data_sources=data_sources,
        complex_patterns=complex_patterns,
        compatibility=compatibility,
        migration_stages=migration_stages,
        # NB: code_churn is intentionally omitted here. The analyzer transformer
        # (``transform_analysis``) computes it from analyzer evidence; if neither
        # source populates it the IR's default (0% / 0 lines / empty description)
        # is what renders.
        file_summary_by_type=file_summary_by_type,
        file_summary_by_technology=file_summary_by_technology,
        spark_api_by_category=spark_api_by_category,
        spark_api_by_status=spark_api_by_status,
        third_party_libs=third_party_libs,
        files=files_rows,
        file_info=file_info_rows,
        migration_strategy=(
            f"This migration plan organizes {len(code_files)} code files into "
            f"{len(migration_waves)} waves based on dependency topology and "
            "compatibility. Files with no internal dependencies are migrated "
            "first (foundation layer), followed by increasingly complex "
            "components. This ensures that at each stage, dependencies are "
            "already available in Snowflake."
        ),
        most_depended_files=most_depended_files,
        most_complex_files=most_complex_files,
        cross_module_dependencies=cross_module_dependencies,
        migration_waves=migration_waves,
        dependency_graph=dependency_graph,
        data_dependency_graph=data_dependency_graph,
        wave_graph=wave_graph,
        circular_dependencies=circular_dependencies,
        isolated_modules=isolated_modules,
        largest_component_size=largest_component_size,
        main_cluster=main_cluster,
        package_marker_count=package_marker_count,
        workload_classification=workload_classification,
        project_type=project_type,
        sources_sinks_inventory=sources_sinks_inventory,
        high_risk_formats=high_risk_formats,
        common_refactors=common_refactors,
        unresolved_dynamic_imports=unresolved_dynamic_imports,
        unresolved_data_edges=unresolved_data_edges,
    )


# ---------------------------------------------------------------------------
# Per-file scan
# ---------------------------------------------------------------------------


def _iter_source_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.name in _EXCLUDED_FILENAMES:
            continue
        if any(part in _EXCLUDED_DIRS for part in p.relative_to(root).parts):
            continue
        if p.suffix.lower() not in _INTERESTING_EXTS:
            continue
        yield p


def _inspect_file(path: Path, root: Path) -> dict:
    try:
        text = path.read_text(errors="ignore")
    except (OSError, UnicodeDecodeError):
        text = ""
    rel = path.relative_to(root)
    ext = path.suffix.lower()
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)

    imports = _collect_imports(text, ext)
    data_formats_seen: list[tuple[str, str]] = []
    for label, pat in _DATA_FORMAT_PATTERNS:
        for m in re.finditer(pat, text):
            matched = m.group(0)
            if "read" in matched:
                kind = "read"
            elif "write" in matched or "save" in matched:
                kind = "write"
            else:
                # Look at the line context to determine direction
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.end())
                line = text[line_start:line_end if line_end != -1 else len(text)]
                kind = "write" if ".write" in line or "save" in line.lower() else "read"
            data_formats_seen.append((label, kind))

    data_urls: list[tuple[str, str]] = []
    for m in _DATA_URL_RE.finditer(text):
        url = m.group(0)
        scheme = url.split(":", 1)[0]
        data_urls.append((scheme, url))

    pattern_counts: dict[str, int] = {}
    for name, pat, _impact in _COMPLEX_PATTERNS:
        pattern_counts[name] = len(re.findall(pat, text))

    if ext == ".sql":
        # SQL files are invisible to the Python-oriented _SPARK_API_RE and
        # _DATA_FORMAT_PATTERNS, which would silently dilute both the ingestion
        # footprint and compute intensity metrics for SQL-heavy workloads.
        # Use SQL-native patterns instead.
        if _SQL_INGESTION_RE.search(text):
            data_formats_seen.append(("SQL", "write"))
        spark_api = len(_SQL_COMPUTE_RE.findall(text))
    else:
        spark_api = len(_SPARK_API_RE.findall(text))

    spark_categories: dict[str, dict] = {
        cat: {"supported": 0, "unsupported": 0} for cat in _SPARK_CATEGORIES
    }
    for cat, (pat, supported) in _SPARK_CATEGORIES.items():
        hits = len(re.findall(pat, text))
        spark_categories[cat]["supported" if supported else "unsupported"] += hits

    return {
        "rel_path": str(rel),
        "name": path.name,
        "ext": ext,
        "lines": lines,
        "imports": imports,
        "data_formats": data_formats_seen,
        "data_urls": data_urls,
        "patterns": pattern_counts,
        "spark_api": spark_api,
        "spark_categories": spark_categories,
    }


# Accept leading dots so relative imports ("from .base_writer import ...")
# round-trip through the IR. Stdlib + 3rd-party imports never have a leading
# dot, so the extra ``\.*`` is a no-op for them.
_PY_IMPORT_RE = re.compile(r"^\s*(?:from\s+(\.+[\w\.]*|[\w\.]+)\s+import|import\s+([\w\.]+))", re.MULTILINE)
_SCALA_IMPORT_RE = re.compile(r"^\s*import\s+([\w\.]+)", re.MULTILINE)


def _collect_imports(text: str, ext: str) -> list[str]:
    """Return the dotted module paths from ``text``'s import statements.

    Returns the import target verbatim — including any leading dots for
    relative imports — so callers can independently derive the root segment
    (third-party library aggregation) and the leaf segment (intra-project
    dependency resolution). The previous implementation eagerly collapsed
    to ``split(".")[0]``, which silently dropped enough information that
    ``from common.utils import Utils`` could not be resolved back to the
    file at ``common/utils.py``.
    """
    if ext in (".py", ".ipynb"):
        return [(m.group(1) or m.group(2)) for m in _PY_IMPORT_RE.finditer(text)]
    if ext in (".scala", ".java"):
        return [m.group(1) for m in _SCALA_IMPORT_RE.finditer(text)]
    return []


def _import_root(dotted: str) -> str:
    """Top-level package name for the third-party library counter.

    Returns ``""`` for relative imports (leading dot) — those are intra-
    project edges, not library dependencies, and would otherwise inflate
    the ``library_imports`` tile and pollute the third-party libs table.
    """
    if not dotted or dotted.startswith("."):
        return ""
    return dotted.split(".", 1)[0]


# ---------------------------------------------------------------------------
# Data-flow dependency mining (optional; requires schema_mine + PySpark)
# ---------------------------------------------------------------------------


# Data-path heuristics for filtering config values down to plausible
# read/write targets (URIs, absolute paths, datafile-suffixed names).
_DATA_PATH_PREFIXES = (
    "s3://", "s3a://", "s3n://", "gs://", "wasb://", "wasbs://", "abfs://",
    "abfss://", "hdfs://", "file://", "dbfs:/", "snowflake://",
)
_DATA_FILE_SUFFIXES = (".parquet", ".json", ".csv", ".orc", ".avro", ".jsonl", ".txt")
# Reader/writer chain detection for AST analysis.
_READ_TERMINAL_METHODS = frozenset({
    "parquet", "json", "csv", "orc", "text", "avro", "load", "table",
})
_WRITE_TERMINAL_METHODS = frozenset({
    "save", "saveAsTable", "insertInto", "parquet", "json", "csv", "orc",
    "text", "avro",
})


def _looks_like_data_path(s: str) -> bool:
    """True for strings that plausibly name a data target (URI/path/datafile)."""
    if not s or not isinstance(s, str):
        return False
    if any(s.startswith(p) for p in _DATA_PATH_PREFIXES):
        return True
    if any(s.lower().endswith(suf) for suf in _DATA_FILE_SUFFIXES):
        return True
    if s.startswith("/") and len(s) > 1:
        return True
    return False


def _walk_config_values(obj: Any, pool: dict[str, set[str]]) -> None:
    """Recursively collect path-like or table-name-like string values keyed by their dict keys.

    Accepts both URI/path strings (S3, HDFS, etc.) and plain identifier-shaped
    strings so that schema/database/table names like ``ECOM_STAGING`` or
    ``rouses_ecom`` are available for config-driven f-string resolution.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and (
                _looks_like_data_path(v) or _looks_like_table_name(v)
            ):
                pool.setdefault(str(k), set()).add(v)
            elif isinstance(v, (dict, list)):
                _walk_config_values(v, pool)
    elif isinstance(obj, list):
        for x in obj:
            _walk_config_values(x, pool)


def _path_is_inside(candidate: Path, root: Path) -> bool:
    """Return True iff the fully-resolved ``candidate`` is under ``root``.

    Guards the workload-config walkers against symlink-based path traversal:
    ``Path.rglob`` follows symlinks by default, so a hostile workload could
    plant ``config/evil.json -> /etc/passwd`` and cause our loaders to slurp
    that file. Resolving both sides and requiring a common prefix rejects
    any target that escapes the workload root.
    """
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _config_search_roots(workload_dir: Path) -> list[Path]:
    """Return directories to scan for config files.

    ``workload_dir`` is the primary root.  SCOS outputs only the converted
    Python files into an ``Output/`` sub-directory and does NOT copy companion
    config YAMLs.  To catch configs that live one level up (e.g.
    ``Rouses-ETL-Release-1.0/ROUSES/configs/`` when ``workload_dir`` is
    ``…/ROUSES/Output/``), we also include the immediate parent of
    ``workload_dir``, stopping before we escape the filesystem root.

    Traversal is intentionally limited to one ancestor level.  Going further
    risks reading config files from unrelated sibling projects that happen to
    share a common ancestor directory (CWE-22 / data-leak concern).

    All returned directories are verified to exist; duplicates are removed.
    """
    roots: list[Path] = [workload_dir]
    parent = workload_dir.parent
    if parent != parent.parent:  # not filesystem root
        roots.append(parent)
    return [r for r in roots if r.is_dir()]


def _load_config_pool(workload_dir: Path) -> dict[str, set[str]]:
    """Scan ``workload_dir`` (and nearby ancestor dirs) for JSON/YAML configs
    and build a pool of path-like or table-name-like values keyed by their
    dict-key names.

    Used by the AST resolver to follow chains like
    ``self.x = config_section["s3SinkDirectory"]`` → look up ``s3SinkDirectory``
    in the pool → real S3 URI, or ``DATABASE_NAME = cfg.get("DATABASE")`` →
    table name string.

    Also searches parent/grandparent of ``workload_dir`` so that SCOS
    ``Output/`` trees can still resolve configs that live alongside the
    original source (SCOS does not copy companion config files into Output/)."""
    pool: dict[str, set[str]] = {}
    seen_files: set[Path] = set()

    for search_root in _config_search_roots(workload_dir):
        try:
            candidates = list(search_root.rglob("*"))
        except Exception:
            continue
        for cfg in candidates:
            try:
                if not cfg.is_file():
                    continue
                resolved = cfg.resolve()
            except OSError:
                continue
            if resolved in seen_files:
                continue
            # Path-traversal guard: file must stay within its own search root.
            if not _path_is_inside(cfg, search_root):
                continue
            if cfg.suffix not in (".json", ".yaml", ".yml"):
                continue
            try:
                rel_parts = cfg.relative_to(search_root).parts
            except ValueError:
                continue
            if any(part in _EXCLUDED_DIRS for part in rel_parts):
                continue
            seen_files.add(resolved)
            try:
                text = cfg.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            data: Any = None
            if cfg.suffix == ".json":
                try:
                    data = json.loads(text)
                except Exception:
                    continue
            else:
                try:
                    import yaml  # type: ignore[import]
                    data = yaml.safe_load(text)
                except Exception:
                    continue
            if data is None:
                continue
            _walk_config_values(data, pool)
    return pool


def _collect_assignments_legacy_wrapper(tree: ast.AST) -> dict[str, list[ast.AST]]:
    """Deprecated shim — kept for tests that reach in for the un-refactored name.

    Delegates to :func:`data_edge_ast._collect_assignments`. New callers should
    import that symbol directly.
    """
    return _collect_assignments(tree)


# Built-in string ops that preserve enough of the path for matching.
_STR_PASSTHROUGH_METHODS = frozenset({
    "rstrip", "lstrip", "strip", "lower", "upper", "format", "replace", "removesuffix", "removeprefix",
})


def _static_string(
    node: ast.AST | None,
    assignments: dict[str, list[ast.AST]],
    config_pool: dict[str, set[str]],
    depth: int = 0,
) -> str | None:
    """Best-effort static resolution of an expression to a path string.

    Handles: literals, f-strings, name/attribute lookup via ``assignments``,
    dict subscript / ``.get(KEY)`` → ``config_pool`` lookup, string concats
    (returns the left side), and pass-through string methods (rstrip etc.).

    Distinct from :func:`data_edge_ast._signature_from_node`: this one
    demands the value RESOLVE through the config pool, so a bare
    ``cfg[KEY]`` returns the pool VALUE (not the key). Kept in this module
    because ``_resolve_via_config`` is the only caller — it's tied to the
    config-pool machinery here, not to the signature walker.
    """
    if depth > 6 or node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.strip():
                return v.value
        return None
    if isinstance(node, ast.Name):
        for av in assignments.get(node.id, []):
            r = _static_string(av, assignments, config_pool, depth + 1)
            if r:
                return r
        if config_pool:
            values = config_pool.get(node.id)
            if values:
                return next(iter(values))
        return None
    if isinstance(node, ast.Attribute):
        for av in assignments.get(node.attr, []):
            r = _static_string(av, assignments, config_pool, depth + 1)
            if r:
                return r
        return None
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            values = config_pool.get(sl.value)
            if values:
                return next(iter(values))
        # Variable-key subscript: trace the slice back to a literal string
        # first, then look up the resolved key in the config pool. This is
        # the config-pool half of the pattern 2b support that
        # :mod:`data_edge_ast` handles for fingerprint purposes.
        if isinstance(sl, (ast.Name, ast.Attribute)):
            key_str = _static_string_key(sl, assignments, depth + 1)
            if key_str:
                values = config_pool.get(key_str)
                if values:
                    return next(iter(values))
        return None
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr == "get" and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    # First try to resolve the receiver — handles simple
                    # ``cfg["section"].get("KEY")`` where receiver resolves.
                    receiver = _static_string(func.value, assignments, config_pool, depth + 1)
                    if receiver:
                        return receiver
                    # Chained .get() fallback: CONFIG.get("config").get("databricks")[0].get("DATABASE")
                    # When the receiver is itself an unresolvable call chain, the
                    # config pool is already flat so we can look up the key directly.
                    values = config_pool.get(arg0.value)
                    if values:
                        return next(iter(values))
                return None
            if func.attr in _STR_PASSTHROUGH_METHODS:
                return _static_string(func.value, assignments, config_pool, depth + 1)
        return None
    if isinstance(node, ast.BinOp):
        # path + "/" or "/" + path — return whichever side resolves.
        left = _static_string(node.left, assignments, config_pool, depth + 1)
        if left:
            return left
        return _static_string(node.right, assignments, config_pool, depth + 1)
    return None


def _static_string_key(
    node: ast.AST | None,
    assignments: dict[str, list[ast.AST]],
    depth: int = 0,
) -> str | None:
    """Trace ``node`` (a subscript slice) back to a literal string KEY.

    Doesn't consult the config pool — used only to bridge the
    variable-key subscript to the config-pool lookup in
    :func:`_static_string`. Keeps that resolution self-contained rather
    than reaching into ``data_edge_ast._signature_from_node`` (which has
    a different semantic — signatures may legitimately be non-key
    strings).
    """
    if depth > 6 or node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        for av in assignments.get(node.id, []):
            r = _static_string_key(av, assignments, depth + 1)
            if r:
                return r
    if isinstance(node, ast.Attribute):
        for av in assignments.get(node.attr, []):
            r = _static_string_key(av, assignments, depth + 1)
            if r:
                return r
    return None


def _resolve_via_config(
    abs_path: str, config_pool: dict[str, set[str]]
) -> tuple[set[str], set[str]]:
    """AST pass: find read/write calls, resolve their path arguments via
    workload config.  Complements schema_mine for runtime-resolved cases."""
    if not config_pool:
        return set(), set()
    try:
        src = Path(abs_path).read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src)
    except Exception:
        return set(), set()

    assignments = _collect_assignments(tree)
    for param, nodes in _collect_call_site_args(tree).items():
        if param not in assignments:
            assignments[param] = nodes
        else:
            assignments[param] = assignments[param] + nodes
    sources: set[str] = set()
    sinks: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        attr_name = func.attr
        chain = _attr_chain_names(func)

        if attr_name in _READ_TERMINAL_METHODS and ("read" in chain or "readStream" in chain):
            if node.args:
                val = _static_string(node.args[0], assignments, config_pool)
                if val and (_looks_like_data_path(val) or _looks_like_table_name(val)):
                    sources.add(val)
        if attr_name in _WRITE_TERMINAL_METHODS and ("write" in chain or "writeStream" in chain):
            if node.args:
                val = _static_string(node.args[0], assignments, config_pool)
                if val and (_looks_like_data_path(val) or _looks_like_table_name(val)):
                    sinks.add(val)

    return sources, sinks


# ---------------------------------------------------------------------------
# Fingerprint reader/writer call arguments by concatenating their literal
# string parts (ignoring runtime placeholders). This lets a writer and a
# reader that BOTH reference the same S3 path via slightly-different
# f-strings match on the shared literal portion, sidestepping schema_mine's
# lossy name output for non-literal arguments.
#
# The heavy lifting lives in :mod:`data_edge_ast`. This module exposes a
# 2-tuple back-compat entry point so existing callers keep the same shape,
# and a 4-tuple ``_extract_path_signatures_with_unresolved`` for the new
# DAG-builder that also wants the unresolved-edge diagnostics.
# ---------------------------------------------------------------------------


def _extract_path_signatures(abs_path: str) -> tuple[set[str], set[str]]:
    """Legacy 2-tuple entry point kept for tests and downstream helpers.

    Delegates to :func:`data_edge_ast._extract_path_signatures` and drops
    the two unresolved-edge lists. New callers that want the diagnostics
    should use :func:`_extract_path_signatures_with_unresolved`.
    """
    sources, sinks, _reads, _writes = _extract_path_signatures_full(abs_path)
    return sources, sinks


def _extract_path_signatures_with_unresolved(
    abs_path: str,
) -> tuple[set[str], set[str], list[UnresolvedEdge], list[UnresolvedEdge]]:
    """Full 4-tuple entry point used by :func:`_build_data_dep_edges`.

    Returns ``(sources, sinks, unresolved_reads, unresolved_writes)``. The
    two ``UnresolvedEdge`` lists carry a call-site diagnostic (line, kind,
    call expression, argument expression, dynamic reason) so the DAG
    builder can surface them in an "Unresolved read/write calls" block
    beneath the diagram.
    """
    return _extract_path_signatures_full(abs_path)


def _load_config_data(workload_dir: Path) -> list[dict]:
    """Load every JSON / YAML config file in the workload and return the RAW
    parsed objects.

    Unlike :func:`_load_config_pool`, which flattens configs into a pool of
    path-like values keyed by dict key, this preserves the FULL structure so
    the dynamic-import chain builder can walk each config and pull out
    arbitrary string values by key.  Reuses the same walk/exclusion logic so
    the two scanners see the same config surface.
    """
    out: list[dict] = []
    try:
        candidates = list(workload_dir.rglob("*"))
    except Exception:
        return out
    for cfg in candidates:
        try:
            if not cfg.is_file():
                continue
        except OSError:
            continue
        # Reject files whose resolved target escapes the workload root — guards
        # against symlink-based path traversal (CWE-22).
        if not _path_is_inside(cfg, workload_dir):
            continue
        if cfg.suffix not in (".json", ".yaml", ".yml"):
            continue
        try:
            rel_parts = cfg.relative_to(workload_dir).parts
        except ValueError:
            continue
        if any(part in _EXCLUDED_DIRS for part in rel_parts):
            continue
        try:
            text = cfg.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        data: Any = None
        if cfg.suffix == ".json":
            try:
                data = json.loads(text)
            except Exception:
                continue
        else:
            try:
                import yaml  # type: ignore[import]
                data = yaml.safe_load(text)
            except Exception:
                continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _walk_lookup_key(obj: Any, key_name: str, results: list[str]) -> None:
    """Recursively collect every STRING value stored under ``key_name``
    anywhere in a nested dict/list structure. Order follows the natural walk
    order (breadth of first occurrence)."""
    if isinstance(obj, dict):
        v = obj.get(key_name)
        if isinstance(v, str):
            results.append(v)
        for val in obj.values():
            if isinstance(val, (dict, list)):
                _walk_lookup_key(val, key_name, results)
    elif isinstance(obj, list):
        for x in obj:
            if isinstance(x, (dict, list)):
                _walk_lookup_key(x, key_name, results)


def _lookup_by_key_name(config_data: list[dict], key_name: str) -> str | None:
    """Return the first string value stored under ``key_name`` across all
    loaded configs. ``None`` when nothing matches."""
    if not key_name:
        return None
    for cfg in config_data:
        found: list[str] = []
        _walk_lookup_key(cfg, key_name, found)
        if found:
            return found[0]
    return None


def _walk_lookup_list(
    obj: Any, list_key: str, inner_key: str, results: list[str]
) -> None:
    """Locate every list stored under ``list_key`` and append each item's
    string value at ``inner_key``.  Preserves list order within each match."""
    if isinstance(obj, dict):
        lst = obj.get(list_key)
        if isinstance(lst, list):
            for item in lst:
                if isinstance(item, dict):
                    v = item.get(inner_key)
                    if isinstance(v, str):
                        results.append(v)
        for val in obj.values():
            if isinstance(val, (dict, list)):
                _walk_lookup_list(val, list_key, inner_key, results)
    elif isinstance(obj, list):
        for x in obj:
            if isinstance(x, (dict, list)):
                _walk_lookup_list(x, list_key, inner_key, results)


def _lookup_list_by_key_name(
    config_data: list[dict], list_key: str, inner_key: str
) -> list[str]:
    """Return, in order, every string ``inner_key`` value found inside a list
    stored under ``list_key`` anywhere in ``config_data``. Empty list when no
    match."""
    if not list_key or not inner_key:
        return []
    out: list[str] = []
    for cfg in config_data:
        _walk_lookup_list(cfg, list_key, inner_key, out)
    return out


def _resolve_module_to_file(module_name: str, code_files: list[dict]) -> str | None:
    """Resolve ``readers.s3_json_reader`` → ``src/readers/s3_json_reader.py``.

    Tries the dotted path directly, then common source-root prefixes, then a
    suffix match against every file's rel_path."""
    if not module_name:
        return None
    dotted = module_name.replace(".", "/")
    target = f"{dotted}.py"
    file_paths = {info["rel_path"] for info in code_files}
    # Direct or common-prefix matches.
    for candidate in (target, f"src/{target}"):
        if candidate in file_paths:
            return candidate
    # Suffix match: any file whose rel_path ends with the target.
    for fp in file_paths:
        if fp.endswith("/" + target) or fp == target:
            return fp
    return None


def _load_entry_points_registry(workload_dir: Path) -> dict[str, dict[str, str]]:
    """Parse ``pyproject.toml`` / ``setup.py`` / ``setup.cfg`` / ``entry_points.txt``
    anywhere in ``workload_dir`` and return a nested mapping::

        {group_name: {entry_name: module_string}}

    Where ``module_string`` is the ``pkg.mod`` (or ``pkg.mod:attr``) reference
    the entry point resolves to at runtime.

    Gracefully returns an empty dict if the workload has no packaging metadata
    or the files can't be parsed.
    """
    registry: dict[str, dict[str, str]] = {}
    try:
        candidates = list(workload_dir.rglob("*"))
    except Exception:
        return registry

    for cfg in candidates:
        try:
            if not cfg.is_file():
                continue
        except OSError:
            continue
        # Reject files whose resolved target escapes the workload root — guards
        # against symlink-based path traversal (CWE-22).
        if not _path_is_inside(cfg, workload_dir):
            continue
        try:
            rel_parts = cfg.relative_to(workload_dir).parts
        except ValueError:
            continue
        if any(part in _EXCLUDED_DIRS for part in rel_parts):
            continue
        name = cfg.name
        # --- pyproject.toml -------------------------------------------------
        if name == "pyproject.toml":
            try:
                import tomllib  # type: ignore[import]
            except Exception:
                try:
                    import tomli as tomllib  # type: ignore[import,no-redef]
                except Exception:
                    continue
            try:
                data = tomllib.loads(cfg.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            project = data.get("project") or {}
            eps = project.get("entry-points") or {}
            if isinstance(eps, dict):
                for group, mapping in eps.items():
                    if isinstance(mapping, dict):
                        for ep_name, target in mapping.items():
                            if isinstance(target, str):
                                registry.setdefault(group, {})[ep_name] = target
            # ``scripts`` / ``gui-scripts`` — well-known groups.
            for group_key in ("scripts", "gui-scripts"):
                mapping = project.get(group_key) or {}
                if isinstance(mapping, dict):
                    dest_group = "console_scripts" if group_key == "scripts" else "gui_scripts"
                    for ep_name, target in mapping.items():
                        if isinstance(target, str):
                            registry.setdefault(dest_group, {})[ep_name] = target
            # Fall back to poetry-style ``[tool.poetry.plugins]`` block.
            poetry = ((data.get("tool") or {}).get("poetry") or {})
            plugins = poetry.get("plugins") or {}
            if isinstance(plugins, dict):
                for group, mapping in plugins.items():
                    if isinstance(mapping, dict):
                        for ep_name, target in mapping.items():
                            if isinstance(target, str):
                                registry.setdefault(group, {})[ep_name] = target
            continue
        # --- setup.cfg ------------------------------------------------------
        if name == "setup.cfg":
            try:
                import configparser
                parser = configparser.ConfigParser()
                parser.read_string(cfg.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            if parser.has_section("options.entry_points"):
                for group, raw in parser.items("options.entry_points"):
                    # Values look like ``\nfoo = pkg.mod:main\nbar = pkg.mod:aux``
                    for line in (raw or "").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        ep_name, _, target = line.partition("=")
                        registry.setdefault(group, {})[ep_name.strip()] = target.strip()
            continue
        # --- setup.py -------------------------------------------------------
        if name == "setup.py":
            try:
                tree = ast.parse(cfg.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            # Look for a call to setup(...) with entry_points=... kwarg.
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                fn_name = ""
                if isinstance(fn, ast.Name):
                    fn_name = fn.id
                elif isinstance(fn, ast.Attribute):
                    fn_name = fn.attr
                if fn_name != "setup":
                    continue
                for kw in node.keywords:
                    if kw.arg != "entry_points":
                        continue
                    val = kw.value
                    # ``entry_points={"grp": ["name = pkg.mod:attr", ...]}``
                    if isinstance(val, ast.Dict):
                        for k, v in zip(val.keys, val.values):
                            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                                continue
                            group = k.value
                            items: list[str] = []
                            if isinstance(v, ast.List):
                                for elt in v.elts:
                                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                        items.append(elt.value)
                            elif isinstance(v, ast.Constant) and isinstance(v.value, str):
                                items.extend(v.value.splitlines())
                            for entry in items:
                                entry = entry.strip()
                                if not entry or "=" not in entry:
                                    continue
                                ep_name, _, target = entry.partition("=")
                                registry.setdefault(group, {})[ep_name.strip()] = target.strip()
            continue
        # --- entry_points.txt (setuptools egg-info) -------------------------
        if name == "entry_points.txt":
            try:
                import configparser
                parser = configparser.ConfigParser()
                parser.read_string(cfg.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            for group in parser.sections():
                for ep_name, target in parser.items(group):
                    registry.setdefault(group, {})[ep_name] = target
            continue
    return registry


def _resolve_path_arg_to_file(
    path_arg: str | None,
    workload_dir: Path,
    code_files: list[dict],
) -> str | None:
    """Return the rel_path of the workload file matching ``path_arg`` (a
    path-like string). Returns ``None`` when no file matches.

    Tries in order:
      1. Direct path match (relative to ``workload_dir`` or absolute).
      2. Suffix / basename match — the shortest path whose rel_path ends
         with the same trailing segments.
    """
    if not path_arg:
        return None
    file_paths = {info["rel_path"] for info in code_files}
    p = path_arg
    # Direct path match — absolute path.
    if p.startswith("/"):
        try:
            rel = str(Path(p).resolve().relative_to(workload_dir.resolve()))
            if rel in file_paths:
                return rel
        except Exception:
            pass
    # Relative path match.
    if p in file_paths:
        return p
    # Try workload_dir / p.
    try:
        candidate = (workload_dir / p).resolve()
        rel = candidate.relative_to(workload_dir.resolve())
        rel_str = str(rel)
        if rel_str in file_paths:
            return rel_str
    except Exception:
        pass
    # Suffix / basename match.
    base = Path(p).name
    matches = [fp for fp in file_paths if fp.endswith("/" + p) or fp == p]
    if matches:
        return sorted(matches, key=len)[0]
    matches = [fp for fp in file_paths if Path(fp).name == base]
    if matches:
        return sorted(matches, key=len)[0]
    return None


def _candidate_files_for_names(
    names: list[str],
    orchestrator_file: str,
    code_files: list[dict],
    workload_dir: Path,
) -> list[str]:
    """Locate workload files that IMPORT or DEFINE any of ``names``.

    For a factory dict ``{"a": ClsA, "b": ClsB}`` we care about the files
    where ``ClsA`` and ``ClsB`` are declared or imported so those files can
    appear as fan-out edges into the orchestrator. Uses two static signals:

      1. Any file with ``class NAME:`` — the definition site.
      2. Any file whose imports list contains a suffix ending in ``NAME`` —
         a re-export or forwarding module.

    ``orchestrator_file`` is excluded from the result (a file can't fan out
    onto itself), preserving the intent that these are edges *into* the
    orchestrator.
    """
    if not names:
        return []
    resolved: set[str] = set()
    # 1. Definition sites — parse each file's AST looking for ``class NAME:``.
    class_pat = re.compile(r"^\s*class\s+(\w+)\b", re.MULTILINE)
    for info in code_files:
        rel = info["rel_path"]
        if rel == orchestrator_file:
            continue
        abs_path = workload_dir / rel
        try:
            text = abs_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        classes_here = set(class_pat.findall(text))
        if classes_here & set(names):
            resolved.add(rel)
    # 2. Import graph fallback: any file whose imports mention the name.
    for info in code_files:
        rel = info["rel_path"]
        if rel == orchestrator_file or rel in resolved:
            continue
        imports = info.get("imports") or []
        for imp in imports:
            leaf = imp.split(".")[-1] if imp else ""
            if leaf in set(names):
                resolved.add(rel)
                break
    return sorted(resolved)


def _resolve_dynamic_import_site(
    site: dict,
    orchestrator_rel: str,
    config_data: list[dict],
    code_files: list[dict],
    entry_points: dict[str, dict[str, str]],
    workload_dir: Path,
    llm_import_targets: dict[tuple[str, int], list[str]] | None = None,
) -> tuple[list[str], str | None]:
    """Dispatch a single dynamic-import site to a list of resolved workload files.

    Returns ``(files, None)`` when at least one workload file resolved, else
    ``([], "<specific reason>")``. The reason is always a specific
    human-readable diagnostic — never the string ``"unknown"``.

    When ``llm_import_targets`` maps this site's ``(orchestrator, line)`` to a
    non-empty target list, the LLM's resolution wins over the static dispatch —
    the LLM read the code and traced a runtime-computed path the AST walker
    could not.  This is the import-graph analogue of injecting LLM data-edge
    signatures: static is the fast baseline, LLM is the correctness override.
    """
    if llm_import_targets:
        override = llm_import_targets.get(
            (orchestrator_rel, int(site.get("line") or 0))
        )
        if override:
            return list(override), None

    kind = site.get("kind") or ""
    if kind in ("import_module", "__import__"):
        ck = site.get("config_key")
        container = site.get("container_key")
        module_names: list[str] = []
        if ck and container:
            module_names.extend(_lookup_list_by_key_name(config_data, container, ck))
        elif ck:
            m = _lookup_by_key_name(config_data, ck)
            if m:
                module_names.append(m)
        resolved: list[str] = []
        for name in module_names:
            f = _resolve_module_to_file(name, code_files)
            if f and f not in resolved:
                resolved.append(f)
        if resolved:
            return resolved, None
        if ck:
            return [], f"config key '{ck}' not found in any workload config"
        return [], "argument expression not traceable to config, path, or entry-point"

    if kind in ("spec_from_file", "imp_load_source"):
        path_arg = site.get("path_arg")
        if not path_arg:
            return [], "argument expression not traceable to config, path, or entry-point"
        f = _resolve_path_arg_to_file(path_arg, workload_dir, code_files)
        if f:
            return [f], None
        return [], f"path '{path_arg}' did not match any workload file"

    if kind == "entry_point":
        if not entry_points:
            return [], (
                "no entry_points registry found in workload "
                "(no pyproject.toml / setup.py / setup.cfg / entry_points.txt)"
            )
        group = site.get("entry_point_group")
        name = site.get("entry_point_name")
        if not group:
            return [], "argument expression not traceable to config, path, or entry-point"
        group_map = entry_points.get(group)
        if not group_map:
            return [], f"entry point group '{group}' not found in registry"
        resolved = []
        if name:
            target = group_map.get(name)
            if target:
                # Strip ``:attr`` suffix — resolve just the module portion.
                module_name = target.split(":", 1)[0].strip()
                f = _resolve_module_to_file(module_name, code_files)
                if f:
                    return [f], None
                return [], f"entry point '{group}:{name}' → '{target}' did not match any workload file"
            return [], f"entry point '{group}:{name}' not registered in group '{group}'"
        # No name — return every module registered under this group.
        for target in group_map.values():
            module_name = target.split(":", 1)[0].strip()
            f = _resolve_module_to_file(module_name, code_files)
            if f and f not in resolved:
                resolved.append(f)
        if resolved:
            return resolved, None
        return [], f"entry point group '{group}' had no modules matching a workload file"

    if kind == "factory_dict":
        candidates = list(site.get("candidate_classes") or [])
        if not candidates:
            return [], f"factory dict '{site.get('dict_var_name') or '?'}' has no resolvable candidate classes"
        files = _candidate_files_for_names(
            candidates, orchestrator_rel, code_files, workload_dir
        )
        if files:
            return files, None
        return [], f"factory dict '{site.get('dict_var_name') or '?'}' has no resolvable candidate classes"

    return [], "argument expression not traceable to config, path, or entry-point"


def _order_sites_for_chain(sites: list[dict]) -> list[dict]:
    """Order dynamic-import sites for chain assembly.

    Sites are first sorted by source-code order. Then, to reflect the way
    orchestrator code typically declares a pipeline (reader helper, writer
    helper, and a for-loop that walks the transform list — each in its own
    method, defined in whatever order the author preferred), we interpose
    every list-based site (``container_key`` set) BETWEEN the reader-endpoint
    and the writer-endpoint. Concretely: chain = [first single-stage site] +
    [all container sites, in source order] + [remaining single-stage sites,
    in source order]. This is a pure structural heuristic — no role labels
    or config-key names are inspected — and it degrades to plain source
    order when there are no container sites (or no single-stage sites)."""
    by_line = sorted(sites, key=lambda s: (s.get("line") or 0,
                                            s.get("raw_expr") or ""))
    single = [s for s in by_line if not s.get("container_key")]
    container = [s for s in by_line if s.get("container_key")]
    if not container or not single:
        return by_line
    return [single[0], *container, *single[1:]]


def _dynamic_import_chain_edges(
    sites_by_file: dict[str, list[dict]],
    config_data: list[dict],
    code_files: list[dict],
    entry_points: dict[str, dict[str, str]] | None = None,
    workload_dir: Path | None = None,
) -> tuple[list[tuple[str, str, str]], list[UnresolvedDynamicImport]]:
    """Turn AST-discovered dynamic-import sites into file-level chain edges.

    Returns a tuple ``(edges, unresolved)`` where each edge is
    ``(src_rel_path, tgt_rel_path, kind)`` — ``kind`` is ``"data"`` for
    chain-based edges (consecutive stages) and ``"factory_dispatch"`` for
    factory_dict fan-out edges (candidate class file → orchestrator).

    For every orchestrator file, sites are dispatched through
    :func:`_resolve_dynamic_import_site` — the caller of this function can
    inspect ``unresolved`` to render a warning block for any site that
    didn't map to at least one workload file.
    """
    entry_points = entry_points or {}
    workload_dir_p = workload_dir or Path(".")
    edges: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    unresolved: list[UnresolvedDynamicImport] = []

    for orchestrator, sites in sites_by_file.items():
        stages: list[str] = []
        for site in _order_sites_for_chain(sites):
            kind = site.get("kind") or "import_module"
            resolved_files, reason = _resolve_dynamic_import_site(
                site, orchestrator, config_data, code_files, entry_points, workload_dir_p
            )
            if not resolved_files and reason:
                unresolved.append(UnresolvedDynamicImport(
                    file=orchestrator,
                    line=int(site.get("line") or 0),
                    kind=kind,
                    reason=reason,
                    raw_expr=str(site.get("raw_expr") or ""),
                ))
                continue
            if kind == "factory_dict":
                # Emit fan-out edges: each candidate class file feeds INTO the
                # orchestrator so the diagram shows the dispatch is 1-of-N.
                for cf in resolved_files:
                    if cf == orchestrator:
                        continue
                    key = (cf, orchestrator, "factory_dispatch")
                    if key not in seen:
                        seen.add(key)
                        edges.append(key)
                continue
            # Everything else contributes stages that get chained pairwise.
            for f in resolved_files:
                if not stages or stages[-1] != f:
                    stages.append(f)

        for a, b in zip(stages[:-1], stages[1:]):
            if a == b:
                continue
            key = (a, b, "data")
            if key not in seen:
                seen.add(key)
                edges.append(key)

    return edges, unresolved


def _mine_file_io(
    abs_path: str,
    config_pool: dict[str, set[str]] | None = None,
) -> tuple[set[str], set[str]] | None:
    """Return ``(source_names, sink_names)`` for one file.

    Combines two static analysis passes:
      1. ``schema_mine.mine()`` — resolves literal-string read/write paths
         (requires PySpark).
      2. ``_resolve_via_config()`` — AST + workload-config substitution for
         runtime-driven paths (works without PySpark).

    Unresolved schema_mine placeholders (``name_confidence=="unresolved"``,
    ``llm_todo``) are filtered to avoid spurious cross-file matches.

    Returns ``None`` only when both passes are unusable (schema_mine missing
    AND no config_pool provided AND file can't be parsed)."""
    sources: set[str] = set()
    sinks: set[str] = set()
    schema_mine_ran = False
    if _DATA_MINING_AVAILABLE:
        schema_mine_ran = True
        try:
            result = _schema_mine_fn(abs_path)  # type: ignore[name-defined]
            for name, info in (result.get("_sources") or {}).items():
                if (info or {}).get("name_confidence") == "unresolved":
                    continue
                sources.add(name)
            for name, info in (result.get("_sinks") or {}).items():
                if (info or {}).get("llm_todo"):
                    continue
                sinks.add(name)
        except Exception:
            pass

    if config_pool:
        cfg_sources, cfg_sinks = _resolve_via_config(abs_path, config_pool)
        sources |= cfg_sources
        sinks |= cfg_sinks

    if not schema_mine_ran and not config_pool:
        return None
    return sources, sinks


def _find_ast_dynamic_import_sites(abs_path: str) -> list[dict]:
    """AST-only fallback for dynamic import detection.

    Detects patterns that ``schema_mine`` misses because they use
    ``importlib.util`` directly rather than ``importlib.import_module``:

      1. ``spec_from_file_location(name, path)`` — path arg is the file to load.
         Handled the same as ``schema_mine``'s ``spec_from_file`` kind.

      2. The inline lambda pattern used by SCOS-converted workloads::

           module = (lambda m: (s.loader.exec_module(m), m)[1])(
               importlib.util.module_from_spec(s))

         This wraps both ``module_from_spec`` and ``exec_module`` in a
         single expression.  We detect it by looking for *any* call to
         ``module_from_spec`` whose argument traces back to a
         ``spec_from_file_location`` call.

    Always returns a list (possibly empty) so callers never see ``None``.
    Silently returns ``[]`` on parse failure.
    """
    try:
        src = Path(abs_path).read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src)
    except Exception:
        return []

    assignments = _collect_assignments(tree)
    sites: list[dict] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        attr = func.attr

        # Pattern 1: importlib.util.spec_from_file_location(name, path)
        if attr == "spec_from_file_location" and len(node.args) >= 2:
            path_node = node.args[1]
            path_arg = _static_string(path_node, assignments, {})
            sites.append({
                "kind": "spec_from_file",
                "path_arg": path_arg,
                "line": getattr(node, "lineno", None),
            })
            continue

        # Pattern 2: module_from_spec(spec_var) where spec_var traces back
        # to a spec_from_file_location call — emit as spec_from_file using
        # the path from that call.
        if attr == "module_from_spec" and node.args:
            spec_node = node.args[0]
            # Follow Name → assignments to find the spec_from_file_location call
            spec_calls: list[ast.AST] = []
            if isinstance(spec_node, ast.Name):
                spec_calls = assignments.get(spec_node.id, [])
            elif isinstance(spec_node, ast.Call):
                spec_calls = [spec_node]
            for sc in spec_calls:
                if not isinstance(sc, ast.Call):
                    continue
                sf = sc.func
                if isinstance(sf, ast.Attribute) and sf.attr == "spec_from_file_location":
                    if len(sc.args) >= 2:
                        path_arg = _static_string(sc.args[1], assignments, {})
                        sites.append({
                            "kind": "spec_from_file",
                            "path_arg": path_arg,
                            "line": getattr(sc, "lineno", None),
                        })
    return sites


def _mine_file_io_and_imports(
    abs_path: str,
    config_pool: dict[str, set[str]] | None = None,
) -> tuple[set[str], set[str], list[dict]] | None:
    """Extended variant of :func:`_mine_file_io` that also returns any
    dynamic-import sites discovered in the file.

    Dynamic-import detection uses two passes:
      1. ``schema_mine.mine(detect_dynamic_imports=True)`` — primary, covers
         ``importlib.import_module``, ``__import__``, entry-point dispatchers.
      2. :func:`_find_ast_dynamic_import_sites` — AST-only fallback that
         covers ``spec_from_file_location`` and the inline ``exec_module``
         lambda pattern that schema_mine misses.

    Returns ``None`` only when both passes are unusable AND no config_pool is
    supplied (mirrors :func:`_mine_file_io`)."""
    sources: set[str] = set()
    sinks: set[str] = set()
    dynamic_imports: list[dict] = []
    schema_mine_ran = False
    if _DATA_MINING_AVAILABLE:
        schema_mine_ran = True
        try:
            result = _schema_mine_fn(abs_path, detect_dynamic_imports=True)  # type: ignore[name-defined]
            for name, info in (result.get("_sources") or {}).items():
                if (info or {}).get("name_confidence") == "unresolved":
                    continue
                sources.add(name)
            for name, info in (result.get("_sinks") or {}).items():
                if (info or {}).get("llm_todo"):
                    continue
                sinks.add(name)
            dynamic_imports = list(result.get("_dynamic_imports") or [])
        except Exception:
            pass

    # AST fallback: catch spec_from_file_location / exec_module patterns that
    # schema_mine doesn't detect (always run — cheap, pure-AST).
    ast_dyn = _find_ast_dynamic_import_sites(abs_path)
    existing_lines = {s.get("line") for s in dynamic_imports}
    for site in ast_dyn:
        if site.get("line") not in existing_lines:
            dynamic_imports.append(site)

    if config_pool:
        cfg_sources, cfg_sinks = _resolve_via_config(abs_path, config_pool)
        sources |= cfg_sources
        sinks |= cfg_sinks

    if not schema_mine_ran and not config_pool:
        return None
    return sources, sinks, dynamic_imports



_YAML_VAR_RE = re.compile(r"\$\{[^}]*\}")
# Jinja-style {{ var }} placeholders used in Airflow, Helm, and similar formats.
_YAML_JINJA_RE = re.compile(r"\{\{[^}]*\}\}")

# SQL comment stripping
_SQL_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_SQL_COMMENT_LINE_RE = re.compile(r"--[^\n]*")
# SQL read-clause patterns: FROM table / JOIN table
_SQL_READ_CLAUSE_RE = re.compile(
    r"\bFROM\s+([\w.`\[\]\"]+)|\bJOIN\s+([\w.`\[\]\"]+)",
    re.IGNORECASE,
)
# SQL write-clause patterns: INSERT INTO, CREATE TABLE/VIEW, MERGE INTO, UPDATE, DELETE FROM
_SQL_WRITE_CLAUSE_RE = re.compile(
    r"\bINSERT\s+(?:INTO|OVERWRITE)\s+([\w.`\[\]\"]+)"
    r"|\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.`\[\]\"]+)"
    r"|\bMERGE\s+INTO\s+([\w.`\[\]\"]+)"
    r"|\bUPDATE\s+([\w.`\[\]\"]+)"
    r"|\bTRUNCATE\s+TABLE\s+([\w.`\[\]\"]+)"
    r"|\bDELETE\s+FROM\s+([\w.`\[\]\"]+)",
    re.IGNORECASE,
)
# Detect CTE / subquery aliases defined inline: `name AS (`.
# These are never external tables and must be excluded from source/sink sets.
_SQL_CTE_ALIAS_RE = re.compile(r"\b(\w+)\s+AS\s*\(", re.IGNORECASE)
# SQL reserved words that can appear directly after write-clause keywords
# (e.g. INSERT INTO ... VALUES, UPDATE ... SET) but are not table names.
_SQL_NON_TABLE_WORDS: frozenset[str] = frozenset({
    "values", "select", "set", "default", "null", "true", "false",
    "exists", "not", "distinct", "all", "any", "some", "case", "when",
    "then", "else", "end", "union", "intersect", "except", "with",
    "recursive", "returning", "output", "over", "partition", "by",
    "order", "group", "having", "limit", "offset",
})


def _parse_yaml_topology(
    workload_dir: Path,
    code_files: list[dict],
) -> list[tuple[str, str, str]]:
    """Extract task-execution-order edges from orchestration YAML files.

    Works across ANY orchestration format with zero hardcoded field names by
    using structural cross-reference detection:

    1. Find every list-of-dicts in the YAML at any nesting depth.
    2. For each such list, detect the **ID field**: the field whose string
       values are unique across all items in the list — i.e., it acts as a
       primary key.  No field name is assumed.
    3. Detect **dependency fields**: any field whose collected string values
       (including strings nested inside lists or dicts) intersect with the ID
       field's value set.  This catches ``depends_on: ["A"]``,
       ``depends_on: [{task_key: "A"}]``, ``after: A``, ``needs: ["A"]``,
       or any other representation without naming the field.
    4. Resolve file references: recursively scan each item's nested values for
       any string that matches a workload code file via suffix lookup.
    5. Emit ``(source_rel, target_rel, "yaml_dag")`` edges for each
       cross-reference that maps to two distinct files.

    Variable placeholder resolution:
    * ``${var.NAME}`` substituted from the YAML ``variables`` section defaults.
    * Remaining ``${…}`` and ``{{…}}`` placeholders are stripped.
    """
    rel_paths = [info["rel_path"] for info in code_files]
    suffix_index: dict[str, str] = {}
    for rp in rel_paths:
        parts = Path(rp).parts
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            if suffix not in suffix_index:
                suffix_index[suffix] = rp

    def _extract_var_defaults(doc: dict) -> dict[str, str]:
        var_defaults: dict[str, str] = {}
        variables = doc.get("variables")
        if isinstance(variables, dict):
            for var_name, var_def in variables.items():
                if isinstance(var_def, dict):
                    default = var_def.get("default")
                    if isinstance(default, str):
                        var_defaults[var_name] = default
                elif isinstance(var_def, str):
                    var_defaults[var_name] = var_def
        return var_defaults

    def _make_resolver(var_defaults: dict[str, str]):
        def _resolve(raw: str) -> str | None:
            if not raw:
                return None
            def _sub_var(m: re.Match) -> str:
                inner = m.group(0)[2:-1]  # strip ${ and }
                if inner.startswith("var."):
                    name = inner[4:]
                    if name in var_defaults:
                        return var_defaults[name]
                return ""
            cleaned = _YAML_VAR_RE.sub(_sub_var, raw)
            cleaned = _YAML_JINJA_RE.sub("", cleaned)
            cleaned = re.sub(r"/+", "/", cleaned).strip("/")
            if not cleaned:
                return None
            parts = cleaned.split("/")
            for i in range(len(parts)):
                candidate = "/".join(parts[i:])
                if candidate in suffix_index:
                    return suffix_index[candidate]
                if not candidate.endswith(".py"):
                    with_py = candidate + ".py"
                    if with_py in suffix_index:
                        return suffix_index[with_py]
            return None
        return _resolve

    def _collect_str_values(val: Any) -> set[str]:
        """Flatten a field value to the set of leaf strings it contains."""
        out: set[str] = set()
        if isinstance(val, str) and val:
            out.add(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item:
                    out.add(item)
                elif isinstance(item, dict):
                    for v in item.values():
                        if isinstance(v, str) and v:
                            out.add(v)
        elif isinstance(val, dict):
            for v in val.values():
                if isinstance(v, str) and v:
                    out.add(v)
        return out

    def _find_id_field(items: list[dict]) -> str | None:
        """Return the field that acts as a unique primary key across all items.

        A field is a candidate ID field if:
        - Every item in the list has it with a non-empty string value.
        - All those values are distinct (no duplicates).

        If no field satisfies the strict condition, relax to: field present
        in >= 2/3 of items with all-distinct values among those that have it.
        Returns ``None`` if no suitable field exists.
        """
        if not items:
            return None
        # Strict: field present in ALL items with unique values
        for field in list(items[0].keys()):
            vals = [item.get(field) for item in items]
            str_vals = [v for v in vals if isinstance(v, str) and v]
            if len(str_vals) == len(items) and len(set(str_vals)) == len(items):
                return field
        # Relaxed: present in ≥ 2/3 of items, all distinct among those present
        threshold = max(2, len(items) * 2 // 3)
        all_fields: set[str] = set()
        for item in items:
            all_fields |= set(item.keys())
        for field in all_fields:
            str_vals = [item[field] for item in items
                        if isinstance(item.get(field), str) and item.get(field)]
            if len(str_vals) >= threshold and len(set(str_vals)) == len(str_vals):
                return field
        return None

    def _find_file_in_item(item: dict, resolve: Any) -> str | None:
        """Recursively find the first nested string value that resolves to a code file."""
        for v in item.values():
            if isinstance(v, str):
                r = resolve(v)
                if r:
                    return r
            elif isinstance(v, dict):
                r = _find_file_in_item(v, resolve)
                if r:
                    return r
        return None

    def _all_item_lists(obj: Any) -> list[list[dict]]:
        """Recursively collect all lists whose items are all dicts."""
        found: list[list[dict]] = []
        if isinstance(obj, list):
            dicts = [x for x in obj if isinstance(x, dict)]
            if len(dicts) >= 2:
                found.append(dicts)
            for item in obj:
                found.extend(_all_item_lists(item))
        elif isinstance(obj, dict):
            for v in obj.values():
                found.extend(_all_item_lists(v))
        return found

    def _edges_from_item_list(
        items: list[dict],
        resolve: Any,
        seen: set[tuple[str, str]],
    ) -> list[tuple[str, str, str]]:
        """Emit yaml_dag edges for one candidate task list via cross-reference detection."""
        id_field = _find_id_field(items)
        if id_field is None:
            return []

        id_values: set[str] = {
            item[id_field] for item in items if isinstance(item.get(id_field), str)
        }

        # Dep fields: any field (other than id_field) whose collected leaf strings
        # overlap with id_values.  No field name is assumed.
        dep_fields: set[str] = set()
        for item in items:
            for field, val in item.items():
                if field == id_field:
                    continue
                if _collect_str_values(val) & id_values:
                    dep_fields.add(field)
        if not dep_fields:
            return []

        # Map item ID → resolved file
        id_to_file: dict[str, str | None] = {}
        for item in items:
            item_id = item.get(id_field)
            if isinstance(item_id, str) and item_id not in id_to_file:
                id_to_file[item_id] = _find_file_in_item(item, resolve)

        result: list[tuple[str, str, str]] = []
        for item in items:
            target_id = item.get(id_field)
            if not isinstance(target_id, str):
                continue
            target_file = id_to_file.get(target_id)
            if not target_file:
                continue
            for dep_field in dep_fields:
                for dep_id in _collect_str_values(item.get(dep_field)):
                    if dep_id not in id_values:
                        continue
                    source_file = id_to_file.get(dep_id)
                    if not source_file or source_file == target_file:
                        continue
                    pair = (source_file, target_file)
                    if pair not in seen:
                        seen.add(pair)
                        result.append((source_file, target_file, "yaml_dag"))
        return result

    all_edges: list[tuple[str, str, str]] = []
    global_seen: set[tuple[str, str]] = set()

    for yaml_path in workload_dir.rglob("*.yml"):
        try:
            import yaml as _yaml  # type: ignore[import]
            with open(yaml_path, encoding="utf-8", errors="ignore") as fh:
                doc = _yaml.safe_load(fh)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue

        var_defaults = _extract_var_defaults(doc)
        resolve = _make_resolver(var_defaults)

        for item_list in _all_item_lists(doc):
            all_edges.extend(_edges_from_item_list(item_list, resolve, global_seen))

    return all_edges


def _extract_sql_data_refs(
    abs_path: str,
    config_pool: dict[str, set[str]],
) -> tuple[set[str], set[str]]:
    """Extract read/write table references from a SQL file via regex.

    Strips SQL comments, substitutes ``${KEY}`` and ``{{…}}`` placeholders
    from the config pool, then matches FROM/JOIN clauses (reads) and
    INSERT/CREATE/MERGE/UPDATE clauses (writes).  Results are filtered through
    ``_looks_like_table_name`` / ``_looks_like_data_path`` so noise is rejected.

    Three additional correctness passes:
    * **CTE exclusion** — names defined via ``word AS (`` are inline aliases,
      not external tables, and are removed from both sets.
    * **SQL keyword exclusion** — words like ``VALUES`` and ``SET`` that appear
      immediately after write-clause keywords are rejected in ``_clean_ref``.
    * **Self-reference removal** — tables that appear as BOTH source and sink
      in the same file represent in-place UPSERT/MERGE patterns (e.g.
      ``MERGE INTO T … FROM T``).  They create false bidirectional edges when
      multiple files all update the same table, so they are removed from the
      sink set.  The YAML-topology signal captures execution ordering instead.
    """
    try:
        text = Path(abs_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set(), set()

    text = _SQL_COMMENT_BLOCK_RE.sub(" ", text)
    text = _SQL_COMMENT_LINE_RE.sub(" ", text)

    def _sub_config(m: re.Match) -> str:
        key = m.group(0)[2:-1]
        values = config_pool.get(key) if config_pool else None
        return next(iter(values)) if values else ""

    text = _YAML_VAR_RE.sub(_sub_config, text)
    text = _YAML_JINJA_RE.sub("", text)

    # Collect inline CTE / subquery alias names so they can be excluded.
    # "word AS (" appears for WITH-clause CTEs and derived-table aliases.
    cte_names: frozenset[str] = frozenset(
        m.group(1).lower() for m in _SQL_CTE_ALIAS_RE.finditer(text)
    )

    def _clean_ref(raw: str) -> str | None:
        name = raw.strip("`[]\"'")
        name = _YAML_VAR_RE.sub("", name)   # strip residual ${...}
        name = name.lstrip(".")              # strip leading dots from empty-var prefixes
        name = name.strip()
        if not name:
            return None
        if name.lower() in _SQL_NON_TABLE_WORDS:
            return None
        if name.lower() in cte_names:
            return None
        if _looks_like_table_name(name) or _looks_like_data_path(name):
            return name
        return None

    sources: set[str] = set()
    sinks: set[str] = set()

    for m in _SQL_READ_CLAUSE_RE.finditer(text):
        raw = next((g for g in m.groups() if g), None)
        if raw:
            ref = _clean_ref(raw)
            if ref:
                sources.add(ref)

    for m in _SQL_WRITE_CLAUSE_RE.finditer(text):
        raw = next((g for g in m.groups() if g), None)
        if raw:
            ref = _clean_ref(raw)
            if ref:
                sinks.add(ref)

    # Remove self-references: a table that is both read and written in this
    # file is being updated in-place (UPSERT / MERGE INTO T FROM T).  Keeping
    # it as a sink would create a false bidirectional edge with every other
    # file that reads OR writes the same table.  Compare case-insensitively
    # because SQL names in the same file may differ only in case.
    source_lc = {s.lower() for s in sources}
    sinks = {s for s in sinks if s.lower() not in source_lc}

    return sources, sinks


def _build_data_dep_edges(
    code_files: list[dict],
    workload_dir: Path,
    llm_source_sigs: dict[str, list[str]] | None = None,
    llm_sink_sigs: dict[str, list[str]] | None = None,
) -> tuple[
    list[tuple[str, str, str]],
    list[UnresolvedDynamicImport],
    list[UnresolvedDataEdge],
]:
    """Return data-flow edges as ``(source_rel, target_rel, kind)`` triples.

    Combines three static signals so a real workload's data DAG is captured:
      1. Shared storage — writer's sink path matches another file's source
         path (via schema_mine + config-aware AST resolution). Emitted as
         ``kind="data"``.
      2. Dynamic-import chain — orchestrator files that call
         ``importlib.import_module(cfg[KEY])`` (or one of the four new
         patterns: ``__import__``, ``spec_from_file_location``,
         ``imp.load_source``, ``entry_points``, factory-dict). Chain-based
         edges are ``kind="data"``; factory_dict fan-out edges are
         ``kind="factory_dispatch"``.

    Also returns:

      * ``UnresolvedDynamicImport`` diagnostics — dynamic-import sites the
        resolver couldn't tie to a workload file.
      * ``UnresolvedDataEdge`` diagnostics — read/write call sites whose
        path argument the AST walker couldn't statically resolve. Each
        carries a **dynamically derived** reason describing the AST node
        the walker stopped at (see :mod:`data_edge_ast`).

    Deduplicates edges across signals.

    ``llm_source_sigs`` / ``llm_sink_sigs`` (``{normalized_sig: [rel_path, ...]}``)
    are optional LLM-resolved read/write signatures.  When supplied (by
    ``rebuild_data_flow_graph`` during ``--llm-resolved-edges``), they are folded
    into the signature maps before matching so LLM-resolved paths add edges on top
    of the full AST signal set — enrichment is additive and deduplicated, never a
    replacement.  Callers MUST pass already-normalized signatures.
    """
    config_pool = _load_config_pool(workload_dir)
    config_data = _load_config_data(workload_dir)
    entry_points = _load_entry_points_registry(workload_dir)

    source_files: dict[str, list[str]] = defaultdict(list)
    sink_files: dict[str, list[str]] = defaultdict(list)
    sig_source_files: dict[str, list[str]] = defaultdict(list)
    sig_sink_files: dict[str, list[str]] = defaultdict(list)
    sites_by_file: dict[str, list[dict]] = {}
    unresolved_edges: list[UnresolvedDataEdge] = []

    for info in code_files:
        rel = info["rel_path"]
        abs_path = str(workload_dir / rel)
        io = _mine_file_io_and_imports(abs_path, config_pool)
        if io is None:
            continue
        file_sources, file_sinks, dyn_imports = io
        for name in file_sources:
            source_files[name.lower()].append(rel)
        for name in file_sinks:
            sink_files[name.lower()].append(rel)
        if dyn_imports:
            sites_by_file[rel] = dyn_imports
        # Signal 4 fingerprints + unresolved-edge diagnostics — extracted
        # independently so f-string / ``.format(...)`` paths survive with
        # matchable signatures even when schema_mine's name is a placeholder
        # (``src0``, ``dynamo_write``, ...). Unresolved diagnostics are
        # rebased to the workload-relative path before returning.
        sig_sources, sig_sinks, u_reads, u_writes = (
            _extract_path_signatures_full(abs_path, config_pool=config_pool)
        )
        for sig in sig_sources:
            sig_source_files[sig].append(rel)
        for sig in sig_sinks:
            sig_sink_files[sig].append(rel)
        for edge in u_reads:
            unresolved_edges.append(
                UnresolvedDataEdge(
                    file=rel,
                    line=edge.line,
                    kind=edge.kind,
                    call_expr=edge.call_expr,
                    arg_expr=edge.arg_expr,
                    reason=edge.reason,
                )
            )
        for edge in u_writes:
            unresolved_edges.append(
                UnresolvedDataEdge(
                    file=rel,
                    line=edge.line,
                    kind=edge.kind,
                    call_expr=edge.call_expr,
                    arg_expr=edge.arg_expr,
                    reason=edge.reason,
                )
            )
        # Signal 5: SQL table references extracted via regex from .sql files.
        if Path(rel).suffix.lower() == ".sql":
            sql_srcs, sql_snks = _extract_sql_data_refs(abs_path, config_pool)
            for name in sql_srcs:
                sig = _normalize_signature(name)
                if sig:
                    source_files[sig].append(rel)
                    sig_source_files[sig].append(rel)
            for name in sql_snks:
                sig = _normalize_signature(name)
                if sig:
                    sink_files[sig].append(rel)
                    sig_sink_files[sig].append(rel)

    data_edges: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    # Inject LLM-resolved signatures BEFORE the signal-matching blocks so LLM
    # paths participate in Signal 4 / 4b exactly like AST-mined ones — and,
    # crucially, so Signals 1/2/3 still run and their edges are retained. LLM
    # enrichment is thereby purely additive: it can only add edges, never
    # replace the AST result. Sigs must already be normalized by the caller
    # (see ``rebuild_data_flow_graph``) to match the canonical AST keys.
    for sig, files in (llm_source_sigs or {}).items():
        for f in files:
            if f not in sig_source_files[sig]:
                sig_source_files[sig].append(f)
    for sig, files in (llm_sink_sigs or {}).items():
        for f in files:
            if f not in sig_sink_files[sig]:
                sig_sink_files[sig].append(f)

    # Signal 1: shared storage (writer sink → reader source), schema_mine names.
    for name_lc, writers in sink_files.items():
        for reader in source_files.get(name_lc, []):
            for writer in writers:
                if writer == reader:
                    continue
                key = (writer, reader, "data")
                if key not in seen:
                    seen.add(key)
                    data_edges.append(key)

    # Signal 4: shared storage matched on normalized path signatures. Catches
    # f-string / ``.format(...)`` / concatenation cases where schema_mine's
    # leaf-name output would have been ``src0`` on the reader and a variable
    # name on the writer — both sides now produce the same fingerprint.
    for sig, writers in sig_sink_files.items():
        for reader in sig_source_files.get(sig, []):
            for writer in writers:
                if writer == reader:
                    continue
                key = (writer, reader, "data")
                if key not in seen:
                    seen.add(key)
                    data_edges.append(key)

    # Signal 4b: suffix match. Writers often produce a shorter signature
    # (just the trailing constant, e.g. ``overlap/hash_id_output``) while a
    # downstream reader carries the fully-qualified prefixed form
    # (``deduplication/incremental_daily/.../overlap/hash_id_output``). Two
    # signatures match here if one is a ``/``-aligned suffix of the other and
    # the shorter side is either multi-segment OR its lone segment is
    # sufficiently specific (>= 8 chars, non-noise — already enforced by
    # ``_normalize_signature``). This lets ``dynamo_write_df`` match
    # ``eqs/dynamo_write_df`` while rejecting ``csv`` matching ``x/y/csv``.
    def _segs(sig: str) -> list[str]:
        return [p for p in sig.split("/") if p]

    def _is_specific_enough(short_segs: list[str]) -> bool:
        if len(short_segs) >= 2:
            return True
        if len(short_segs) == 1 and len(short_segs[0]) >= 8:
            return True
        return False

    write_sigs = list(sig_sink_files.keys())
    read_sigs = list(sig_source_files.keys())
    for wsig in write_sigs:
        w_segs = _segs(wsig)
        for rsig in read_sigs:
            if wsig == rsig:
                continue
            r_segs = _segs(rsig)
            short, long_ = (
                (w_segs, r_segs) if len(w_segs) <= len(r_segs) else (r_segs, w_segs)
            )
            if not _is_specific_enough(short):
                continue
            if long_[-len(short):] != short:
                continue
            for writer in sig_sink_files[wsig]:
                for reader in sig_source_files[rsig]:
                    if writer == reader:
                        continue
                    key = (writer, reader, "data")
                    if key not in seen:
                        seen.add(key)
                        data_edges.append(key)

    # Signal 2: dynamic-import chains + factory_dispatch fan-out edges.
    chain_edges, unresolved = _dynamic_import_chain_edges(
        sites_by_file, config_data, code_files, entry_points, workload_dir
    )
    for edge in chain_edges:
        if edge not in seen:
            seen.add(edge)
            data_edges.append(edge)

    # Signal 3: YAML deployable topology (Databricks Asset Bundle DAG edges).
    yaml_edges = _parse_yaml_topology(workload_dir, code_files)
    for edge in yaml_edges:
        if edge not in seen:
            seen.add(edge)
            data_edges.append(edge)

    return data_edges, unresolved, unresolved_edges


def rebuild_data_flow_graph(
    workload_dir: Path,
    llm_source_sigs: dict[str, list[str]],
    llm_sink_sigs: dict[str, list[str]],
    language: str = "python",
    llm_import_targets: dict[tuple[str, int], list[str]] | None = None,
) -> "DependencyGraph | None":
    """Rebuild the data DAG layout with LLM-resolved path signatures injected.

    Called by ``render_assessment --llm-resolved-edges`` after the
    ``data_edge_resolver`` agent has written its results.  Re-runs only the
    deterministic scanner steps (file inventory + data-edge extraction);
    the LLM analyzer (``analyze_pyspark.py``) is NOT re-invoked.

    Args:
        workload_dir: Workload source directory (the same one the original
            scan used).
        llm_source_sigs: ``{sig: [rel_path, ...]}`` for every LLM-resolved
            *read* edge (signature → list of reading files).
        llm_sink_sigs: ``{sig: [rel_path, ...]}`` for every LLM-resolved
            *write* edge (signature → list of writing files).
        language: Source language, forwarded to ``_iter_source_files``.
        llm_import_targets: ``{(orchestrator_rel, line): [target_rel, ...]}``
            for every dynamic-import site the LLM resolved.  Overrides the
            static dispatch so runtime-computed import chains lay out
            correctly instead of remaining blind spots.

    Returns:
        Updated :class:`~assess_ir.DependencyGraph` with LLM-resolved edges
        merged in, or ``None`` when there is nothing to render.
    """
    workload_dir = workload_dir.resolve()
    paths = list(_iter_source_files(workload_dir))
    files_info = [_inspect_file(p, workload_dir) for p in paths]
    code_files = [info for info in files_info if info["ext"] in _CODE_EXTS]

    # sites_by_file (dynamic-import sites) is needed for chain LAYOUT; config +
    # entry points feed _build_data_flow_graph.
    config_pool = _load_config_pool(workload_dir)
    config_data = _load_config_data(workload_dir)
    entry_points = _load_entry_points_registry(workload_dir)

    sites_by_file: dict[str, list[dict]] = {}
    for info in code_files:
        rel = info["rel_path"]
        io = _mine_file_io_and_imports(str(workload_dir / rel), config_pool)
        if io is None:
            continue
        _, _, dyn_imports = io
        if dyn_imports:
            sites_by_file[rel] = dyn_imports

    # Normalize the raw LLM sigs to the canonical AST key form (URI scheme
    # stripped, {placeholder} tokens removed, lowercased) so they match the
    # AST-mined keys and actually create edges.
    norm_source_sigs: dict[str, list[str]] = defaultdict(list)
    for raw_sig, files in llm_source_sigs.items():
        sig = _normalize_signature(raw_sig)
        if sig is None:
            continue
        for f in files:
            if f not in norm_source_sigs[sig]:
                norm_source_sigs[sig].append(f)
    norm_sink_sigs: dict[str, list[str]] = defaultdict(list)
    for raw_sig, files in llm_sink_sigs.items():
        sig = _normalize_signature(raw_sig)
        if sig is None:
            continue
        for f in files:
            if f not in norm_sink_sigs[sig]:
                norm_sink_sigs[sig].append(f)

    # Authoritative edge build: ALL AST signals (schema-mine names, dynamic-import
    # chains, YAML topology, path signatures + suffix match) PLUS the LLM-resolved
    # signatures, deduped by (src, tgt, kind). This is the SAME builder the
    # original scan uses (not a Signal-4-only reimplementation), so LLM enrichment
    # is strictly additive — it can add edges but never drops an AST-resolved one.
    data_edges, _, _ = _build_data_dep_edges(
        code_files, workload_dir, norm_source_sigs, norm_sink_sigs,
    )

    # Re-build import_edges for layout (needed by _build_data_flow_graph).
    import_edges, _ = _build_dependency_graph(code_files)

    return _build_data_flow_graph(
        code_files,
        data_edges,
        sites_by_file,
        config_data,
        config_pool,
        workload_dir,
        entry_points,
        import_edges,
        llm_import_targets,
    )


# ---------------------------------------------------------------------------
# Dependency graph + topological waves
# ---------------------------------------------------------------------------


def _build_dependency_graph(code_files: list[dict]) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """Approximate intra-project file dependencies.

    Edge (A, B) means A imports B. Resolution tries, in order:

    1. The full dotted import path matched against each file's rel_path
       converted to dotted form — and every progressively-shorter suffix
       of it. So ``from common.utils import Utils`` resolves to
       ``src/common/utils.py`` because the suffix ``common.utils`` is
       registered against that file even though the rel_path is rooted at
       ``src``. Longer matches are preferred (more specific).
    2. Leaf-stem fallback: the rightmost segment ("utils") matched against
       file stems. Picks up cases where step 1 doesn't reach (e.g. an
       intra-package symbol import the regex can't see).

    Relative imports ("from .foo import ...") strip their leading dots
    before resolution, which lets them resolve via the same lookup tables.
    Duplicate edges from the same importer (e.g. multiple ``from foo
    import bar`` lines) collapse to a single edge.
    """
    dotted_to_path: dict[str, str] = {}
    leaf_to_path: dict[str, str] = {}
    for info in code_files:
        rel = info["rel_path"]
        no_ext = Path(rel).with_suffix("") if Path(rel).suffix else Path(rel)
        parts = list(no_ext.parts)
        # Packages: register both the parent directory dotted form and the
        # bare directory name so ``import pkg`` resolves to its __init__.
        if parts and parts[-1] == "__init__":
            parts.pop()
        for i in range(len(parts)):
            dotted = ".".join(parts[i:])
            if dotted:
                dotted_to_path.setdefault(dotted, rel)
        if parts:
            leaf_to_path.setdefault(parts[-1], rel)

    edges: list[tuple[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    dependents: dict[str, int] = defaultdict(int)
    for info in code_files:
        a = info["rel_path"]
        for imp in info["imports"]:
            target = _resolve_import(imp, dotted_to_path, leaf_to_path)
            if not target or target == a:
                continue
            key = (a, target)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(key)
            dependents[target] += 1
    return edges, dict(dependents)


def _resolve_import(
    dotted: str,
    dotted_to_path: dict[str, str],
    leaf_to_path: dict[str, str],
) -> str | None:
    if not dotted:
        return None
    bare = dotted.lstrip(".")
    if not bare:
        return None
    parts = bare.split(".")
    # Longest-suffix-first walk so ``pipeline.base_pipeline`` matches
    # before falling back to a less-specific ``base_pipeline``.
    for i in range(len(parts)):
        candidate = ".".join(parts[i:])
        target = dotted_to_path.get(candidate)
        if target:
            return target
    return leaf_to_path.get(parts[-1])


def _module_of(path: str) -> str:
    """Bucket files into "modules" for the cross-module-edge counter.

    Uses the parent directory of the file rather than the topmost path
    component — that way a project rooted under ``src/`` doesn't collapse
    every file into one giant "src" module (giving a misleading
    cross_module_dependencies = 0). For files at the project root we fall
    back to the file stem.
    """
    parts = Path(path).parts
    if len(parts) >= 2:
        return parts[-2]
    return Path(path).stem if parts else ""


def _topological_waves(code_files: list[dict], edges: list[tuple[str, str]]) -> list[MigrationWave]:
    """Kahn's-algorithm-style topological layering.

    Wave 1 is "foundation" (files with no outgoing intra-project deps).
    Subsequent waves each carry the next batch of files whose dependencies
    have all been placed in earlier waves. Files with no path into the
    import graph end up in the foundation wave alongside true leaves.

    Each topological layer becomes exactly one wave — we don't split
    large layers, because the whole point of grouping by layer is that
    layer-mates can be migrated in parallel. Splitting them by an
    arbitrary chunk size (e.g. 20) produces spurious "Wave 2 depends on
    Wave 1" badges between files that have no actual dependency.
    """
    file_paths = {info["rel_path"] for info in code_files}
    out_edges: dict[str, set[str]] = defaultdict(set)
    in_edges: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        if a in file_paths and b in file_paths:
            out_edges[a].add(b)
            in_edges[b].add(a)

    info_by_path = {info["rel_path"]: info for info in code_files}

    remaining = set(file_paths)
    placed: set[str] = set()
    waves: list[list[dict]] = []
    wave_idx = 1
    while remaining:
        layer = [p for p in sorted(remaining) if out_edges[p].issubset(placed)]
        if not layer:
            # Cycle — break it by placing the highest in-degree file
            layer = [max(remaining, key=lambda p: len(in_edges[p]))]
        waves.append([info_by_path[p] for p in layer if p in info_by_path])
        placed.update(layer)
        remaining.difference_update(layer)
        wave_idx += 1
        if wave_idx > 50:  # belt + suspenders against runaway
            break

    out: list[MigrationWave] = []
    for i, wave_files in enumerate(waves, start=1):
        if i == 1:
            layer_name = "Foundation Layer"
            descr = "No prior dependencies"
        elif i == 2:
            layer_name = "Core Layer"
            descr = "Depends on foundation"
        elif i == 3:
            layer_name = "Integration Layer"
            descr = "Depends on core layer"
        elif i == 4:
            layer_name = "Application Layer"
            descr = "Depends on integration layer"
        else:
            layer_name = "Complex Layer"
            descr = "Depends on earlier waves"
        out.append(
            MigrationWave(
                name=f"Wave {i}: {layer_name.split()[0]}",
                layer=layer_name,
                depends_on_waves=list(range(1, i)),
                description=descr,
                files=[
                    FileCompatibilityRow(
                        path=info["rel_path"],
                        name=info["name"],
                        technology=_TECH_BY_EXT.get(info["ext"], "Other"),
                        lines=info["lines"],
                        spark_usages=info["spark_api"],
                        issues=0,
                        status="High",
                    )
                    for info in wave_files
                ],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Diagram layout (SVG coordinates computed in Python, rendered in Jinja)
# ---------------------------------------------------------------------------

# Per-module dependency-diagram constants. Match the reference prototype's
# proportions so the rendered HTML lines up with the existing CSS.
_NODE_W = 155
_NODE_H = 28
_NODE_HGAP = 10
_NODE_PITCH = _NODE_W + _NODE_HGAP
_LAYER_VGAP = 60
_LAYER_PITCH = _NODE_H + _LAYER_VGAP
_DIAG_PADDING_X = 30
_DIAG_PADDING_Y = 30
_LABEL_MAX_CHARS = 22
# Max nodes per visual row in the unified cross-folder graph. A single
# topological layer wider than this wraps onto stacked sub-rows so the SVG
# doesn't grow unboundedly wide (the container still scrolls horizontally).
_UNIFIED_MAX_COLS = 6

# Wave-graph constants.
_WAVE_NODE_W = 70
_WAVE_NODE_H = 50
_WAVE_HGAP = 30
_WAVE_PITCH = _WAVE_NODE_W + _WAVE_HGAP
_WAVE_ROW_PITCH = 110
_WAVE_PADDING_X = 40
_WAVE_PADDING_Y = 40
_WAVES_PER_ROW = 10


def _find_cycles(
    nodes: set[str],
    out_edges: dict[str, set[str]],
) -> list[list[str]]:
    """Return the SCCs of size > 1 in ``(nodes, out_edges)`` (Tarjan).

    Each returned list is the file rel_paths in one cycle, sorted for
    deterministic output. Self-loops (a node that imports itself) aren't
    interesting and aren't reported.
    """
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    counter = [0]
    sccs: list[list[str]] = []

    def strongconnect(start: str) -> None:
        # Iterative Tarjan to avoid hitting Python's recursion limit on
        # large workloads with long chains.
        work: list[tuple[str, iter]] = [(start, iter(sorted(out_edges.get(start, ()))))]
        index[start] = counter[0]
        lowlink[start] = counter[0]
        counter[0] += 1
        stack.append(start)
        on_stack[start] = True
        while work:
            v, it = work[-1]
            try:
                w = next(it)
            except StopIteration:
                work.pop()
                # Root of an SCC?
                if lowlink[v] == index[v]:
                    component: list[str] = []
                    while True:
                        x = stack.pop()
                        on_stack[x] = False
                        component.append(x)
                        if x == v:
                            break
                    if len(component) > 1:
                        sccs.append(sorted(component))
                # Propagate lowlink to parent
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[v])
                continue
            if w not in nodes:
                continue
            if w not in index:
                index[w] = counter[0]
                lowlink[w] = counter[0]
                counter[0] += 1
                stack.append(w)
                on_stack[w] = True
                work.append((w, iter(sorted(out_edges.get(w, ())))))
            elif on_stack.get(w, False):
                lowlink[v] = min(lowlink[v], index[w])

    for n in sorted(nodes):
        if n not in index:
            strongconnect(n)
    sccs.sort(key=lambda c: (-len(c), c[0] if c else ""))
    return sccs


def _truncate_label(name: str) -> str:
    """Trim long basenames so they fit inside the 155-px node rect."""
    if len(name) <= _LABEL_MAX_CHARS:
        return name
    return name[: _LABEL_MAX_CHARS - 2] + ".."


def _build_unified_dependency_graph(
    code_files: list[dict],
    edges: list[tuple[str, str]],
    data_edge_set: frozenset[tuple[str, str]] = frozenset(),
    include_all_files: bool = False,
) -> DependencyGraph | None:
    """One cross-folder dependency graph laid out by in-degree rows.

    This replaces the folder-bucketed per-module diagram. Every code file that
    participates in at least one intra-project import edge is placed in a single
    graph, so a reader in ``readers/`` and the driver in ``pipeline/`` that
    imports it are visibly connected rather than stranded in separate boxes.

    Layout:
      * Row = in-degree of the node. Files with in_degree=0 (entry points /
        scripts that nothing imports) are drawn on the first row at the TOP.
        Files with in_degree=1 on the second row, and so on down to the
        most-shared utilities at the bottom.
      * Within a row, nodes are ordered by blast_radius descending (heaviest
        shared libraries first) then basename, and wrapped onto stacked
        sub-rows past ``_UNIFIED_MAX_COLS`` so the SVG stays a sane width.

    Each node carries ``in_degree`` (direct importers) and ``blast_radius``
    (transitive importers) so the report can size/colour and explain the
    "change this and N jobs break" risk. Each edge carries its ``source``/
    ``target`` ids so the rendered SVG can highlight a node's blast radius
    interactively.

    When ``include_all_files`` is True, every file in ``code_files`` is
    included as a node — files with no edges are placed in a dedicated
    "islands" layer below the connected chain. This is used by the data
    dependency graph so users can see the full codebase (including
    utility/base files that no dataset passes through) alongside the
    dataset flow. When False (default), only edge-participating files
    appear, preserving the existing behaviour for the import DAG.

    Returns ``None`` when there are no intra-project edges (nothing to
    draw) — unless ``include_all_files`` is True and there are code files
    to display, in which case an edgeless graph of islands is returned.
    """
    by_path = {info["rel_path"]: info for info in code_files}

    out_edges: dict[str, set[str]] = defaultdict(set)
    in_edges: dict[str, set[str]] = defaultdict(set)
    intra_edges: list[tuple[str, str]] = []
    for a, b in edges:
        if a in by_path and b in by_path and a != b and b not in out_edges[a]:
            out_edges[a].add(b)
            in_edges[b].add(a)
            intra_edges.append((a, b))
    if not intra_edges and not include_all_files:
        return None
    if include_all_files and not by_path:
        return None

    # Files that participate in at least one edge — laid out into
    # dependency-depth layers as before.  Isolated files are handled
    # separately below when ``include_all_files`` is True.
    connected = {p for edge in intra_edges for p in edge}
    if include_all_files:
        node_ids = list(by_path.keys())
    else:
        node_ids = [p for p in by_path if p in connected]

    in_degree = {p: len(in_edges.get(p, set())) for p in node_ids}

    # Blast radius = transitive dependents (reverse BFS over import edges).
    blast_radius: dict[str, int] = {}
    for p in node_ids:
        seen: set[str] = set()
        stack = list(in_edges.get(p, set()))
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(in_edges.get(x, set()))
        blast_radius[p] = len(seen)

    # Row = in-degree of the node: files with in_degree=0 (entry points / leaf
    # scripts that nobody imports) land on the first row; files with
    # in_degree=1 on the second row; and so on down to the most-shared
    # utilities at the bottom.  Within a row, nodes are sorted by blast_radius
    # descending (most-impact first) then basename so the heaviest shared
    # libraries cluster on the left.
    # Files with no edges at all are kept in a dedicated "islands" row below
    # the deepest connected row (include_all_files=True path only).
    isolated: list[str] = []
    layers: dict[int, list[str]] = defaultdict(list)
    for p in node_ids:
        if include_all_files and p not in connected:
            isolated.append(p)
            continue
        layers[in_degree[p]].append(p)
    for lst in layers.values():
        lst.sort(key=lambda p: (-blast_radius[p], by_path[p]["name"].lower()))

    # Isolated islands: place in a dedicated row(s) below the deepest
    # connected layer so utilities/base classes are visible but visually
    # distinct from the pipeline flow.
    if isolated:
        connected_max = max(layers.keys(), default=-1)
        island_layer = connected_max + 1
        layers[island_layer] = sorted(
            isolated, key=lambda p: by_path[p]["name"].lower()
        )

    max_layer = max(layers.keys()) if layers else 0
    widest = max((len(v) for v in layers.values()), default=0)
    cols_used = min(widest, _UNIFIED_MAX_COLS)
    svg_width = cols_used * _NODE_PITCH + _DIAG_PADDING_X * 2 - _NODE_HGAP

    node_coords: dict[str, tuple[int, int]] = {}
    nodes: list[GraphNode] = []
    cur_y = _DIAG_PADDING_Y
    total_subrows = 0
    for layer_idx in range(max_layer + 1):
        row = layers.get(layer_idx, [])
        if not row:
            continue
        nsub = (len(row) + _UNIFIED_MAX_COLS - 1) // _UNIFIED_MAX_COLS
        for i, p in enumerate(row):
            sub = i // _UNIFIED_MAX_COLS
            col = i % _UNIFIED_MAX_COLS
            cols_in_subrow = min(_UNIFIED_MAX_COLS, len(row) - sub * _UNIFIED_MAX_COLS)
            row_span_px = cols_in_subrow * _NODE_PITCH - _NODE_HGAP
            x_start = max(_DIAG_PADDING_X, (svg_width - row_span_px) // 2)
            x = x_start + col * _NODE_PITCH
            y = cur_y + sub * _LAYER_PITCH
            node_coords[p] = (x, y)
            basename = by_path[p]["name"]
            nodes.append(
                GraphNode(
                    id=p,
                    label=_truncate_label(basename),
                    full_label=basename,
                    path=p,
                    x=x,
                    y=y,
                    width=_NODE_W,
                    height=_NODE_H,
                    status="High",  # backfilled in Assessment.merge
                    in_degree=in_degree[p],
                    blast_radius=blast_radius[p],
                )
            )
        cur_y += nsub * _LAYER_PITCH
        total_subrows += nsub

    svg_height = total_subrows * _LAYER_PITCH + _DIAG_PADDING_Y * 2 - _LAYER_VGAP

    gedges: list[GraphEdge] = []
    for a, b in intra_edges:
        if a not in node_coords or b not in node_coords:
            continue
        ax, ay = node_coords[a]
        bx, by_ = node_coords[b]
        gedges.append(
            GraphEdge(
                x1=ax + _NODE_W // 2,
                y1=ay + _NODE_H,
                x2=bx + _NODE_W // 2,
                y2=by_,
                source=a,
                target=b,
                kind="data" if (a, b) in data_edge_set else "import",
            )
        )

    return DependencyGraph(
        module="Project",
        width=svg_width,
        height=svg_height,
        file_count=len(node_ids) if include_all_files else len(connected),
        edge_count=len(intra_edges),
        nodes=nodes,
        edges=gedges,
    )


# ---------------------------------------------------------------------------
# Data-flow DAG: purpose-built top-to-bottom layout with framework cluster
# ---------------------------------------------------------------------------
#
# The unified graph above is a good default for the import DAG (top-to-bottom
# layered by dependency depth) but the *data* DAG has a different shape:
# a linear execution chain (reader → transforms → writer) with a set of
# framework prerequisites (base classes, utilities, __init__.py) sitting
# beside it, plus optional external storage endpoints on either end.
# Users kept asking "why is my pipeline drawn as a random spray of tiles?"
# so :func:`_build_data_flow_graph` renders it as a vertical chain with
# a labelled Framework bounding box on the LEFT.

# Chain-layout constants for the data DAG.
_CHAIN_NODE_W = 200
_CHAIN_NODE_H = 40
_CHAIN_START_Y = 40      # y where the top-most chain node begins (external source)
_CHAIN_VGAP = 50         # vertical gap between consecutive chain nodes
_FRAMEWORK_TOP = 40      # y of the framework cluster's inner top
_FRAMEWORK_HGAP = 12
_FRAMEWORK_VGAP = 8
_FRAMEWORK_NODE_W = 170
_FRAMEWORK_NODE_H = 26
_FRAMEWORK_LABEL_H = 26  # space reserved above nodes for the cluster heading
_FRAMEWORK_PAD_X = 14
_FRAMEWORK_PAD_Y = 12
_FRAMEWORK_MAX_COLS = 3  # cap the framework grid at N columns for readability
_FRAMEWORK_CHAIN_GAP = 60  # horizontal gap between framework cluster and chain column
_ISLAND_TOP_PAD = 40     # vertical padding below the chain before the islands row
_ISLAND_NODE_W = 170
_ISLAND_NODE_H = 26
_ISLAND_HGAP = 12
_ISLAND_VGAP = 8
_ISLAND_MAX_COLS = 4
_EXT_NODE_W = 220
_EXT_NODE_H = 46
# Per-file endpoint pills are smaller than the historical single-per-chain
# endpoint pill so multiple sinks / sources can stack under / above a
# chain-file rect without overflowing the row.
_ENDPOINT_NODE_W = 140
_ENDPOINT_NODE_H = 28
_ENDPOINT_HGAP = 12
_ENDPOINT_VGAP = 8

# Multi-pipeline chain layout: each pipeline gets its own vertical column
# ``_MULTI_CHAIN_WIDTH`` wide, with ``_MULTI_CHAIN_HGAP`` between adjacent
# columns. Sized so an external-source pill fits inside a column.
_MULTI_CHAIN_WIDTH = _EXT_NODE_W
_MULTI_CHAIN_HGAP = 120

# Layered (Sugiyama) topology constants used by the data DAG. Each depth
# layer is one horizontal row of chain files; ``_LAYER_VGAP`` is the space
# between consecutive rows and ``_LAYER_HGAP`` is the horizontal gap between
# sibling nodes within the same row.
_LAYER_HGAP = 40
_LAYER_VGAP_DAG = 90


def _is_framework_path(rel_path: str) -> bool:
    """Framework-detection heuristic (R3).

    A file counts as a migration framework prerequisite when it is:
      * an ``__init__.py`` package marker,
      * an entry-point script (``main.py`` / ``run.py``),
      * named ``base_*`` (base class convention),
      * living under a ``common/`` or ``utils/`` directory anywhere in the tree.

    The abstract-method-based branch of the heuristic ("class defines
    ``abstractmethod``") is handled separately in
    :func:`_framework_paths` because it needs to read file contents.
    """
    parts = Path(rel_path).parts
    name = Path(rel_path).name
    if name == "__init__.py":
        return True
    if name in {"main.py", "run.py", "__main__.py"}:
        return True
    if name.startswith("base_"):
        return True
    if any(p in {"common", "utils"} for p in parts):
        return True
    return False


def _defines_abstractmethod(workload_dir: Path, rel_path: str) -> bool:
    """Return True if the file's source references ``abstractmethod``.

    A quick substring check — good enough to catch the ``@abstractmethod``
    decorator or ``from abc import ABC, abstractmethod`` imports without
    parsing the file's AST. False positives on comments are fine: those
    files are legitimately structural even when the class isn't abstract.
    """
    try:
        text = (workload_dir / rel_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return "abstractmethod" in text


def _framework_paths(
    workload_dir: Path,
    code_files: list[dict],
    chain_paths: set[str],
    orchestrator_paths: set[str] | None = None,
) -> set[str]:
    """Files that belong inside the Framework cluster (R3).

    Any code file that is (a) not on the execution chain AND (b) matches
    :func:`_is_framework_path` OR defines an ``abstractmethod`` OR is an
    orchestrator (a file with at least one dynamic-import site — it drives
    the pipeline and belongs with the other prerequisites). The
    abstractmethod branch requires reading the file, which is cheap for
    the O(few dozen) code files a workload typically contains.
    """
    result: set[str] = set()
    orchestrator_paths = orchestrator_paths or set()
    for info in code_files:
        rel = info["rel_path"]
        if rel in chain_paths:
            continue
        if rel in orchestrator_paths:
            result.add(rel)
            continue
        if _is_framework_path(rel):
            result.add(rel)
            continue
        if _defines_abstractmethod(workload_dir, rel):
            result.add(rel)
    return result


def _chain_from_dynamic_imports(
    sites_by_file: dict[str, list[dict]],
    config_data: list[dict],
    code_files: list[dict],
    entry_points: dict[str, dict[str, str]] | None = None,
    workload_dir: Path | None = None,
    llm_import_targets: dict[tuple[str, int], list[str]] | None = None,
) -> tuple[list[list[str]], list[UnresolvedDynamicImport]]:
    """Reader → transforms → writer chains, one per independent pipeline.

    Every orchestrator file whose dynamic-import sites resolve to at LEAST
    two stages becomes a candidate pipeline. Two candidates are considered
    the same pipeline if their chains share any file, in which case they
    are merged by taking the union of files (preserving source-code order
    from the first-encountered chain, then appending any new files from
    the later chain). The result is a list of INDEPENDENT chains — each
    inner list is a linear chain in source-code order, and no two chains
    share a file.

    Site sequencing within one orchestrator follows
    :func:`_order_sites_for_chain`, which interposes list-based (loop)
    sites between the first and last single-stage sites — so a typical
    reader-helper / writer-helper / transforms-loop layout produces the
    expected reader → transforms → writer chain regardless of the order
    the three helper methods were defined in.

    Also returns any unresolved dynamic-import sites encountered while
    walking the sites (aggregated across ALL candidates, not just the
    survivors), so the caller can surface them in the warning block.
    """
    entry_points = entry_points or {}
    workload_dir_p = workload_dir or Path(".")
    unresolved: list[UnresolvedDynamicImport] = []
    candidates: list[list[str]] = []
    for orch, sites in sites_by_file.items():
        stages: list[str] = []
        local_unresolved: list[UnresolvedDynamicImport] = []
        for site in _order_sites_for_chain(sites):
            kind = site.get("kind") or "import_module"
            resolved_files, reason = _resolve_dynamic_import_site(
                site, orch, config_data, code_files, entry_points, workload_dir_p,
                llm_import_targets,
            )
            if not resolved_files and reason:
                local_unresolved.append(UnresolvedDynamicImport(
                    file=orch,
                    line=int(site.get("line") or 0),
                    kind=kind,
                    reason=reason,
                    raw_expr=str(site.get("raw_expr") or ""),
                ))
                continue
            if kind == "factory_dict":
                # factory_dict fan-out edges are collected by the edge
                # builder, not by the linear chain assembly, so ignore
                # them here.
                continue
            for f in resolved_files:
                if f not in stages:
                    stages.append(f)
        unresolved.extend(local_unresolved)
        if len(stages) >= 2:
            candidates.append(stages)

    # Merge candidates that share any file. Two chains sharing a file are
    # the "same pipeline" (e.g. a reader library reused across orchestrators).
    # Union-find keyed by file rel_path collapses overlapping chains.
    parent: dict[int, int] = {i: i for i in range(len(candidates))}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    file_to_candidate: dict[str, int] = {}
    for i, chain in enumerate(candidates):
        for f in chain:
            if f in file_to_candidate:
                union(file_to_candidate[f], i)
            else:
                file_to_candidate[f] = i

    # Group by root, then merge each group by concatenating chains in the
    # order they were discovered, dropping duplicates. Preserves per-chain
    # source-code order for the leader; appended chains contribute any
    # files not already present.
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(candidates)):
        groups[find(i)].append(i)

    merged: list[list[str]] = []
    for root in sorted(groups.keys()):
        indices = groups[root]
        merged_chain: list[str] = []
        for idx in indices:
            for f in candidates[idx]:
                if f not in merged_chain:
                    merged_chain.append(f)
        if len(merged_chain) >= 2:
            merged.append(merged_chain)
    return merged, unresolved




def _first_data_path(paths: Iterable[str]) -> str:
    """Pick the most descriptive external endpoint URI for the pseudo-node.

    Prefer paths with an explicit scheme (``s3://``, ``jdbc:``, ``gs://``,
    …) so the label makes it obvious this is an external storage location;
    fall back to the first alphabetically otherwise. Returns ``""`` when
    the input is empty.
    """
    ranked = sorted(
        paths,
        key=lambda p: (0 if "://" in p or p.startswith("jdbc:") else 1, p),
    )
    return ranked[0] if ranked else ""


def _short_ext_label(uri: str, kind: str = "source") -> str:
    """Compact label for an external-endpoint node.

    URI or path (``s3://``, ``gs://``, ``hdfs://``, …): show the last one or
    two meaningful (non-glob, non-placeholder) segments, e.g.:

      ``s3://bucket/foo/bar/detokenization/``                → ``detokenization``
      ``s3://bucket/deduplication/incremental_daily/...``    → ``incremental_daily``
      ``s3://bucket/streamloader/partition_year=.../.../``   → ``streamloader``

    Bare table names (no ``/``) render as-is. When nothing meaningful can
    be extracted (empty string, or only wildcards / f-string braces), we
    fall back to ``"external source"`` / ``"external sink"``. Never emit
    ``*`` or the empty string — the tooltip carries the raw string, so
    even an all-glob URI ends up with a readable pill label.
    """
    if not uri:
        return f"external {kind}"

    def _fallback() -> str:
        return f"external {kind}"

    # Bare table name (no ``://``, no ``/``) — show as-is with a sane length cap.
    if "://" not in uri and "/" not in uri:
        # Strip glob / placeholder characters. If nothing meaningful is
        # left, or if the whole string is bracketed (``{year}``), fall
        # back to the placeholder label.
        stripped = re.sub(r"\{[^{}]*\}", "", uri).strip()
        cleaned = stripped.strip("*").strip()
        if not cleaned or cleaned in {"*", "{}"}:
            return _fallback()
        return cleaned if len(cleaned) <= 34 else cleaned[:16] + "…" + cleaned[-16:]

    # URI / filesystem path: strip the scheme prefix if any, tokenize on ``/``,
    # discard segments that are glob-only, empty, or f-string placeholders
    # (``partition_year={year}``, ``*``, ``**``, ``{...}`` alone). Prefer
    # meaningful trailing segments.
    body = uri
    if "://" in body:
        _scheme, body = body.split("://", 1)
    parts = [p for p in body.split("/") if p]

    def _is_placeholderish(seg: str) -> bool:
        s = seg.strip()
        if not s:
            return True
        if s in {"*", "**"}:
            return True
        # f-string placeholder like {year} or partition_year={year}: strip
        # braces and check what's left.
        stripped = re.sub(r"\{[^{}]*\}", "", s).strip()
        if not stripped:
            return True
        # Something like partition_year= (equal-sign key with placeholder value).
        if stripped.endswith("=") or stripped.startswith("="):
            return True
        return False

    meaningful = [p for p in parts if not _is_placeholderish(p)]
    if not meaningful:
        return _fallback()

    # Take the last meaningful segment. If it's a bare glob-with-extension
    # like ``*.json`` (widely informative but the dir name adds context),
    # include the preceding segment for extra clarity: ``kipawa/*.json``.
    leaf = meaningful[-1].strip()
    if leaf.startswith("*.") and len(meaningful) >= 2:
        leaf = meaningful[-2].rstrip("/") + "/" + leaf
    if not leaf:
        return _fallback()
    if len(leaf) > 34:
        leaf = leaf[:16] + "…" + leaf[-16:]
    return leaf


def _looks_like_table_name(name: str) -> bool:
    """Heuristic: ``name`` looks like a bare table / DynamoDB / Snowflake name.

    Table names lack path separators and URI schemes, are non-empty, and
    consist of characters likely to appear in an identifier (letters,
    digits, underscores, dots for schema.table). Used to accept
    schema_mine's clean-name sources / sinks that ARE the external
    endpoint even though they don't look like a filesystem path."""
    if not name or not isinstance(name, str):
        return False
    if "://" in name or "/" in name:
        return False
    s = name.strip()
    if not s:
        return False
    # Table names are identifiers — no embedded whitespace.
    if any(c.isspace() for c in s):
        return False
    # Must have at least one alphanumeric — reject pure-punctuation and
    # bare glob chars.
    if not any(c.isalnum() for c in s):
        return False
    # Reject the well-known noise words (already used for signature filtering).
    if s.lower() in _SIGNATURE_NOISE_WORDS:
        return False
    return True


def _endpoint_signature(uri: str) -> str | None:
    """Normalize a raw URI / table name into a signature usable for matching
    against a chain-file's write signatures. Table names skip URI-scheme
    stripping (they'd normalize themselves to nothing otherwise); path-like
    strings are pushed through :func:`_normalize_signature`."""
    if not uri:
        return None
    if "://" in uri or "/" in uri:
        return _normalize_signature(uri)
    # Bare table name — normalize just enough (lowercase, trim braces).
    cleaned = re.sub(r"\{[^{}]*\}", "", uri).strip().strip("/").strip()
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if lowered in _SIGNATURE_NOISE_WORDS or len(lowered) < 4:
        return None
    return lowered


def _sig_matches_internal_write(
    endpoint_sig: str, internal_writes: set[str]
) -> bool:
    """Return True iff ``endpoint_sig`` matches any chain-file's write
    signature, using the SAME rules as ``_build_data_dep_edges`` Signal 4/4b:

      * Exact equality
      * ``/``-aligned suffix match: the shorter side is a trailing suffix of
        the longer side AND is specific enough (>= 2 segments, or a single
        segment >= 8 chars)

    Uses the same segmentation helpers as :func:`_build_data_dep_edges`.
    """
    if not endpoint_sig:
        return False
    return any(_sigs_equivalent(endpoint_sig, w) for w in internal_writes)


def _sigs_equivalent(a: str, b: str) -> bool:
    """Return True iff two normalized signatures refer to the same endpoint,
    using ``_build_data_dep_edges`` Signal 4/4b's suffix-match rule.

    Additionally strips trailing "placeholder-ish" segments (bare ``*``,
    partition markers like ``partition_year=``, empty tails) before
    comparing — so ``processed/vid_base_table/partition_year=/…`` matches
    the bare table name ``vid_base_table``.
    """
    if not a or not b:
        return False
    if a == b:
        return True

    def _strip_trailing_placeholders(segs: list[str]) -> list[str]:
        out = list(segs)
        while out:
            tail = out[-1]
            if tail in {"*", "**", ""}:
                out.pop()
                continue
            if tail.endswith("=") or tail.startswith("="):
                out.pop()
                continue
            break
        return out

    a_segs = _strip_trailing_placeholders([p for p in a.split("/") if p])
    b_segs = _strip_trailing_placeholders([p for p in b.split("/") if p])

    if not a_segs or not b_segs:
        return False
    if a_segs == b_segs:
        return True

    def _is_specific_enough(short_segs: list[str]) -> bool:
        if len(short_segs) >= 2:
            return True
        if len(short_segs) == 1 and len(short_segs[0]) >= 8:
            return True
        return False

    short, long_ = (a_segs, b_segs) if len(a_segs) <= len(b_segs) else (b_segs, a_segs)
    if not _is_specific_enough(short):
        return False
    return long_[-len(short):] == short


def _discover_per_file_external_endpoints(
    chain_files: Iterable[str],
    workload_dir: Path,
    config_pool: dict[str, set[str]] | None,
) -> tuple[
    dict[str, list[tuple[str, str]]],
    dict[str, list[tuple[str, str]]],
]:
    """Per chain file, mine its raw reads/writes and figure out which
    portion is EXTERNAL to the pipeline (i.e. not produced or consumed by
    another chain file).

    Returns ``(per_file_ext_sources, per_file_ext_sinks)`` where each maps
    ``rel_path -> [(raw_uri_or_name, endpoint_signature), ...]``.

    Deduplication semantics:
      * ``endpoint_signature`` is the normalized signature (same space as
        Signal 4). External endpoints are matched against
        ``all_internal_writes`` (chain-file write signatures) using the
        SAME suffix / prefix rules as ``_build_data_dep_edges``; anything
        that matches an internal write is dropped (it's an internal edge,
        not a true external endpoint).
      * Within a file, duplicate signatures are collapsed to a single
        (raw, sig) entry — preferring the raw form that has a URI scheme
        (``s3://…``) over one that doesn't.

    The caller performs cross-file dedup (same signature shared by
    multiple files → single external node with fan-in / fan-out edges).
    """
    chain_files = list(chain_files)

    # Per-file signature bookkeeping: keep write/read sigs separated by file
    # so external-endpoint classification can exclude the current file
    # (self-loops shouldn't collapse a terminal sink into "internal"). A
    # sink is external iff NO OTHER chain file reads it. A source is
    # external iff NO OTHER chain file writes it.
    per_file_reads: dict[str, list[tuple[str, str]]] = {}
    per_file_writes: dict[str, list[tuple[str, str]]] = {}
    per_file_schema_sources: dict[str, set[str]] = {}
    per_file_schema_sinks: dict[str, set[str]] = {}
    write_sigs_by_file: dict[str, set[str]] = {}
    read_sigs_by_file: dict[str, set[str]] = {}

    for rel in chain_files:
        abs_ = str(workload_dir / rel)
        reads, writes = _extract_path_uris_and_sigs(abs_)
        per_file_reads[rel] = reads
        per_file_writes[rel] = writes
        w_sigs = {sig for _raw, sig in writes}
        r_sigs = {sig for _raw, sig in reads}
        io = _mine_file_io(abs_, config_pool)
        if io is not None:
            schema_srcs, schema_snks = io
        else:
            schema_srcs, schema_snks = set(), set()
        per_file_schema_sources[rel] = schema_srcs
        per_file_schema_sinks[rel] = schema_snks
        for name in schema_srcs:
            if not _looks_like_table_name(name) and not _looks_like_data_path(name):
                continue
            sig = _endpoint_signature(name)
            if sig:
                r_sigs.add(sig)
        for name in schema_snks:
            if not _looks_like_table_name(name) and not _looks_like_data_path(name):
                continue
            sig = _endpoint_signature(name)
            if sig:
                w_sigs.add(sig)
        write_sigs_by_file[rel] = w_sigs
        read_sigs_by_file[rel] = r_sigs

    def _other_writes(exclude_rel: str) -> set[str]:
        out: set[str] = set()
        for other_rel, sigs in write_sigs_by_file.items():
            if other_rel != exclude_rel:
                out |= sigs
        return out

    def _other_reads(exclude_rel: str) -> set[str]:
        out: set[str] = set()
        for other_rel, sigs in read_sigs_by_file.items():
            if other_rel != exclude_rel:
                out |= sigs
        return out

    # Second pass: collect external endpoints per file, keeping the raw URI
    # / name so we can render a readable pill label.
    per_file_ext_sources: dict[str, list[tuple[str, str]]] = {}
    per_file_ext_sinks: dict[str, list[tuple[str, str]]] = {}

    def _fold_entry(
        entries: list[tuple[str, str]], raw: str, sig: str,
    ) -> None:
        """Append ``(raw, sig)`` to ``entries``, folding into an existing
        equivalent-signature entry if any. When folding, prefer the more
        informative raw string (URI scheme > longer signature > longer
        raw). Also folds when the incoming signature is a bare table name
        that ALREADY appears as one of the segments of an existing URI-
        derived signature — real workloads emit both forms for the same
        endpoint (schema_mine's leaf-name output plus the config-resolved
        URI)."""
        e_segs = [p for p in sig.split("/") if p]
        e_is_leaf = len(e_segs) == 1
        for i, (existing_raw, existing_sig) in enumerate(entries):
            existing_segs = [p for p in existing_sig.split("/") if p]
            if _sigs_equivalent(existing_sig, sig):
                _fold_pick(entries, i, existing_raw, existing_sig, raw, sig)
                return
            # Leaf-name folding: the incoming sig is a single segment that
            # matches one of the existing URI-form segments. Real
            # deduplication: schema_mine emits ``table_a`` alongside the
            # config-resolved ``ext/lookup/table_a``.
            if e_is_leaf and sig in existing_segs:
                _fold_pick(entries, i, existing_raw, existing_sig, raw, sig)
                return
            if len(existing_segs) == 1 and existing_sig in e_segs:
                _fold_pick(entries, i, existing_raw, existing_sig, raw, sig)
                return
        entries.append((raw, sig))

    def _fold_pick(
        entries: list[tuple[str, str]], i: int,
        existing_raw: str, existing_sig: str, raw: str, sig: str,
    ) -> None:
        """Replace ``entries[i]`` with (raw, sig) when the new pair scores
        higher (more informative). Scoring: URI scheme wins, then longer
        signature, then longer raw."""
        def _score(r: str, s: str) -> tuple[int, int, int]:
            return (
                1 if "://" in r else 0,
                len([p for p in s.split("/") if p]),
                len(r),
            )
        if _score(raw, sig) > _score(existing_raw, existing_sig):
            entries[i] = (raw, sig)

    for rel in chain_files:
        # ---- External sources ---------------------------------------------
        # A source is external if NO OTHER chain file writes it. Additionally,
        # if THIS file also writes the same sig (a self-read-back pattern —
        # write to a temp path, read it back within the same file), the read
        # is INTERNAL consumption; only the write side surfaces externally.
        other_writes = _other_writes(rel)
        own_writes = write_sigs_by_file.get(rel, set())
        ext_entries: list[tuple[str, str]] = []

        for raw, sig in per_file_reads.get(rel, []):
            if _sig_matches_internal_write(sig, other_writes):
                continue
            if _sig_matches_internal_write(sig, own_writes):
                continue
            _fold_entry(ext_entries, raw, sig)

        for name in per_file_schema_sources.get(rel, set()):
            if not _looks_like_table_name(name) and not _looks_like_data_path(name):
                continue
            sig = _endpoint_signature(name)
            if not sig:
                continue
            if _sig_matches_internal_write(sig, other_writes):
                continue
            if _sig_matches_internal_write(sig, own_writes):
                continue
            _fold_entry(ext_entries, name, sig)

        if ext_entries:
            per_file_ext_sources[rel] = ext_entries

        # ---- External sinks -----------------------------------------------
        # A sink is external / terminal if NO OTHER chain file reads it.
        # Buffer heuristic: if the file writes to path P AND reads it back
        # AND ALSO writes to at least one OTHER external path that is
        # NOT self-read, P looks like a staging buffer (write to temp, read
        # back, write to final external). That collapses Kipawa's ``temp``
        # into the single ``final`` sink. When ALL writes are self-read
        # (typical read-modify-write pattern for a persistent history
        # table), none are dropped — all surface as external sinks.
        other_reads = _other_reads(rel)
        own_reads = read_sigs_by_file.get(rel, set())
        own_writes = write_sigs_by_file.get(rel, set())

        def _is_self_read(w_sig: str) -> bool:
            for r_sig in own_reads:
                if _sigs_equivalent(w_sig, r_sig):
                    return True
            return False

        # Identify writes that are neither self-read nor consumed by other
        # chain files — these are "genuinely terminal" external writes.
        terminal_external_writes: set[str] = set()
        for w_sig in own_writes:
            if _is_self_read(w_sig):
                continue
            if _sig_matches_internal_write(w_sig, other_reads):
                continue
            terminal_external_writes.add(w_sig)
        # Only apply the buffer filter when the file HAS at least one
        # genuinely terminal external write; otherwise every self-read
        # write is a persistent-history pattern and stays.
        buffer_sigs: set[str] = set()
        if terminal_external_writes:
            for w_sig in own_writes:
                if _is_self_read(w_sig):
                    buffer_sigs.add(w_sig)

        snk_entries: list[tuple[str, str]] = []
        for raw, sig in per_file_writes.get(rel, []):
            if _sig_matches_internal_write(sig, other_reads):
                continue
            if _sig_matches_internal_write(sig, buffer_sigs):
                continue
            _fold_entry(snk_entries, raw, sig)

        for name in per_file_schema_sinks.get(rel, set()):
            if not _looks_like_table_name(name) and not _looks_like_data_path(name):
                continue
            sig = _endpoint_signature(name)
            if not sig:
                continue
            if _sig_matches_internal_write(sig, other_reads):
                continue
            if _sig_matches_internal_write(sig, buffer_sigs):
                continue
            _fold_entry(snk_entries, name, sig)

        if snk_entries:
            per_file_ext_sinks[rel] = snk_entries

    return per_file_ext_sources, per_file_ext_sinks


def _leaf_orchestrators(
    orchestrators: Iterable[str],
    import_edges: Iterable[tuple[str, str]],
) -> set[str]:
    """Return the "downstream-most" orchestrators.

    An orchestrator A "reaches" B if there is a directed import path
    A → ... → B in the import graph (A imports B, directly or via
    intermediaries). Downstream is the CALLEE end of that relation —
    A imports B, so B is A's *downstream*. The downstream-most (leaf)
    orchestrator is the one that other orchestrators reach INTO (via
    imports) but that does not itself reach into any other orchestrator.
    That file is closer to the chain and is the correct source for the
    single ``orchestrates`` arrow (avoids drawing duplicate arrows from
    every upstream orchestrator in the composition chain).

    Concretely: if ``main.py`` imports ``pipeline_impl.py``, then
    ``pipeline_impl.py`` is downstream, and the arrow is drawn only from
    ``pipeline_impl.py``.

    When two orchestrators reach each other (import cycle), both are
    considered leaves (rare — we don't try to break the cycle here).
    """
    orch_set = set(orchestrators)
    if not orch_set:
        return set()
    # Build the full import graph so an intermediary non-orchestrator file
    # doesn't break the reachability computation.
    adj: dict[str, list[str]] = defaultdict(list)
    for a, b in import_edges:
        adj[a].append(b)

    def reaches(start: str) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            for nxt in adj.get(cur, ()):
                if nxt == start:
                    continue
                if nxt in seen:
                    continue
                seen.add(nxt)
                stack.append(nxt)
        return seen

    reached_from: dict[str, set[str]] = {o: reaches(o) for o in orch_set}
    # Leaf = an orchestrator that does NOT reach any OTHER orchestrator via
    # its own import closure. It's the downstream-most member of the
    # composition chain — the one closest to the reader.
    leaves: set[str] = set()
    for o in orch_set:
        reaches_other_orch = any(
            other != o and other in reached_from[o]
            for other in orch_set
        )
        if not reaches_other_orch:
            leaves.add(o)
    return leaves


_CHAIN_EDGE_KINDS: frozenset[str | None] = frozenset((None, "data", "", "yaml_dag"))


def _implicit_chains_from_data_edges(
    data_edges: list[tuple[str, str, str]],
    by_path: dict[str, dict],
) -> list[list[str]]:
    """Build implicit chains from ``kind == "data"`` or ``"yaml_dag"`` edges by topological sort.

    Used when the workload has NO dynamic-import orchestrator (so
    :func:`_chain_from_dynamic_imports` returns empty) BUT the mining passes
    still recovered writer→reader data edges — a common pattern in
    "Step 1 → Step 2 → Step 3" style workloads where the pipeline is implicit
    in the file naming and cross-file data flow, not in a config manifest.
    ``yaml_dag`` edges (from Databricks Asset Bundle YAMLs) also contribute
    so pipelines declared via task topology render as proper chains even when
    no shared-storage edges were discovered.

    Groups edges into weakly-connected components, topologically sorts each
    component (with rel_path as tie-breaker for stability), and returns one
    ordered chain per component. Framework / orchestrates / factory_dispatch
    edges are ignored — they don't represent data flow between chain stages.
    """
    from collections import defaultdict, deque

    adj: dict[str, list[str]] = defaultdict(list)
    rev: dict[str, list[str]] = defaultdict(list)
    in_deg: dict[str, int] = defaultdict(int)
    nodes: set[str] = set()
    for a, b, kind in data_edges:
        if kind not in _CHAIN_EDGE_KINDS:
            continue
        if a not in by_path or b not in by_path or a == b:
            continue
        # Dedup edges (multiple sink matches can produce duplicates).
        if b in adj[a]:
            continue
        adj[a].append(b)
        rev[b].append(a)
        in_deg[b] += 1
        in_deg.setdefault(a, in_deg.get(a, 0))
        nodes.add(a)
        nodes.add(b)
    if not nodes:
        return []

    # Weakly-connected components via BFS over the undirected projection.
    undirected: dict[str, set[str]] = defaultdict(set)
    for src, tgts in adj.items():
        for tgt in tgts:
            undirected[src].add(tgt)
            undirected[tgt].add(src)
    seen: set[str] = set()
    components: list[list[str]] = []
    for n in sorted(nodes):
        if n in seen:
            continue
        comp: list[str] = []
        q = deque([n])
        seen.add(n)
        while q:
            x = q.popleft()
            comp.append(x)
            for y in undirected[x]:
                if y not in seen:
                    seen.add(y)
                    q.append(y)
        components.append(comp)

    # Topological sort per component (Kahn's), rel_path as tie-breaker.
    chains: list[list[str]] = []
    for comp in components:
        comp_set = set(comp)
        local_in = {n: sum(1 for p in rev[n] if p in comp_set) for n in comp}
        ready = sorted([n for n, d in local_in.items() if d == 0])
        order: list[str] = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for m in sorted(adj[n]):
                if m not in comp_set:
                    continue
                local_in[m] -= 1
                if local_in[m] == 0:
                    ready.append(m)
                    ready.sort()
        # If a cycle prevents a full sort, append remaining nodes deterministically.
        for n in sorted(comp):
            if n not in order:
                order.append(n)
        if len(order) >= 2:
            chains.append(order)
    return chains


def _build_data_flow_graph(
    code_files: list[dict],
    data_edges: list[tuple[str, str, str]],
    sites_by_file: dict[str, list[dict]],
    config_data: list[dict],
    config_pool: dict[str, set[str]] | None,
    workload_dir: Path,
    entry_points: dict[str, dict[str, str]] | None = None,
    import_edges: list[tuple[str, str]] | None = None,
    llm_import_targets: dict[tuple[str, int], list[str]] | None = None,
) -> DependencyGraph | None:
    """Purpose-built data-DAG layout: layered (Sugiyama) topology per pipeline
    component + Framework cluster (LEFT) + external endpoints.

    Layout:
      * Every independent execution pipeline renders as its OWN layered
        DAG component. For each chain file we compute its topological
        ``depth`` from the ``kind="data"`` sub-graph (``depth(f) = 0`` for
        files with no incoming data edges from another chain file;
        ``depth(f) = 1 + max(depth(producer))`` otherwise). Files at the
        same depth share a horizontal row and sort left-to-right by
        ``rel_path`` for stability. Consecutive rows are separated by
        ``_LAYER_VGAP_DAG``; adjacent nodes within a row are separated by
        ``_LAYER_HGAP``. Fan-outs (writer→{r1, r2, r3}) now render as
        three sibling nodes in the same row instead of collapsing into a
        single vertical column.
      * External source pseudo-node renders in a virtual row ABOVE depth 0,
        aligned above the reader (depth-0 file with the smallest depth /
        smallest rel_path). External sink renders BELOW the deepest row,
        aligned below the writer (last file in source order).
      * Multiple weakly-connected components lay out side-by-side; each
        component is horizontally centered within its own column, and the
        column width is ``max(row widths)`` for that component.
      * Framework prerequisites (base classes, ``__init__.py``, ``main.py``,
        ``common/*``, ``utils/*``, files defining ``abstractmethod``, and
        orchestrator files with dynamic-import sites) render in a wrapped
        grid INSIDE a labelled ``<rect>`` cluster to the LEFT of all
        chain columns. Each framework node carries an in-degree badge
        showing how many workload files import it (from the STATIC import
        graph — a distinct signal from the data-flow degree of chain nodes).
      * Isolated files sit on an "islands" row below the whole thing.

    Edge routing:
      * Adjacent-layer edges (source depth < target depth == source depth
        + 1) render as straight ``<line>``s from source bottom-center to
        target top-center.
      * Skip edges (target depth > source depth + 1) render as a bezier
        ``<path>`` that leaves the source's RIGHT edge, arcs to the right
        of the component, and re-enters the target's TOP edge — avoiding
        every intermediate row's rectangles.
      * Same-row (horizontal) edges — unusual, only appears when a cycle
        forces two files onto one depth — render as a curved ``<path>``
        arcing above the row. Their presence is a warning that the data
        graph has a cycle: verify the pipeline is really that shape before
        trusting the diagram.

    Chain-node ``in_degree`` and ``blast_radius`` are recomputed from the
    ``kind="data"`` sub-graph (not the static import graph): a chain node's
    ``in_degree`` is the number of workload files that feed data INTO it,
    and ``blast_radius`` is the number of downstream chain files that
    transitively consume its output. Framework nodes retain the static-
    import in_degree (that's the meaningful "how many files depend on this
    prerequisite" signal for a base class / utility module).

    Orchestrator dedup: among the orchestrators for one pipeline, only the
    LEAF orchestrator (the one no other orchestrator reaches via the
    static import graph) draws the dashed blue ``orchestrates`` arrow.
    The arrow is a bezier that leaves the orchestrator's RIGHT edge,
    curves around the framework cluster, and enters the reader's TOP
    edge — its label sits BELOW the framework cluster bottom so it never
    overlaps the cluster rectangle.

    Returns ``None`` when neither a resolved chain NOR data edges exist —
    matching the empty-state behaviour of the previous data-DAG builder.
    """
    by_path = {info["rel_path"]: info for info in code_files}
    if not by_path:
        return None
    chains, _chain_unresolved = _chain_from_dynamic_imports(
        sites_by_file, config_data, code_files,
        entry_points or {}, workload_dir,
        llm_import_targets,
    )
    if not chains and data_edges:
        # No dynamic-import orchestrator, but there ARE data edges — build
        # implicit chains via topological sort of weakly-connected components
        # so "Step 1 → Step 2 → Step 3" style workloads render as proper
        # vertical chains instead of collapsing into a flat islands grid.
        chains = _implicit_chains_from_data_edges(data_edges, by_path)
    elif chains and data_edges:
        # Dynamic-import chains exist but some files (e.g. SQL files loaded
        # by Python callers) may only be connected via data edges and so were
        # not captured by the import-chain scan.  Fold any extra weakly-
        # connected data-edge components in as additional chain entries so
        # they render as proper DAG nodes instead of isolated islands.
        extra_chains = _implicit_chains_from_data_edges(data_edges, by_path)
        already_placed: set[str] = {f for c in chains for f in c}
        for extra_chain in extra_chains:
            new_files = [f for f in extra_chain if f not in already_placed]
            if new_files:
                chains.append(new_files)
    if not chains and not data_edges:
        return None

    # ---- 1. Discover PER-FILE external source / sink endpoints. --------
    # For each chain file, mine its raw reads/writes and figure out which
    # entries are external to the pipeline (i.e. not internally produced
    # / consumed by another chain file). These are attached as METADATA
    # on the chain-node ``GraphNode`` (``external_sources`` /
    # ``external_sinks``) — they are surfaced via the tooltip preview
    # (truncated) + click-opened detail panel (full list). External
    # endpoints are NO LONGER emitted as pseudo-nodes in the SVG (they
    # were too visually noisy on real workloads — Verisk hit 7 source
    # pills + 13 sink pills).
    all_chain_files: set[str] = set()
    for c in chains:
        all_chain_files.update(c)

    per_file_ext_sources, per_file_ext_sinks = _discover_per_file_external_endpoints(
        all_chain_files, workload_dir, config_pool,
    )

    def _dedup_uris(entries: list[tuple[str, str]]) -> list[str]:
        """Return raw URIs from ``entries`` preserving first-appearance
        order and dropping exact-duplicate raw strings. The signature-
        based fold already happened inside
        ``_discover_per_file_external_endpoints``; this second pass
        only guards against a rare same-raw duplicate slipping through
        (e.g. schema_mine and the raw scan both emitting the same URI
        verbatim)."""
        seen: set[str] = set()
        out: list[str] = []
        for raw, _sig in entries:
            if raw in seen:
                continue
            seen.add(raw)
            out.append(raw)
        return out

    # rel_path -> list[str] of full URIs / table names, order-preserved,
    # deduped within a single file. Consumed in step 5b when we build
    # the chain GraphNodes.
    ext_sources_by_file: dict[str, list[str]] = {
        rel: _dedup_uris(entries)
        for rel, entries in per_file_ext_sources.items()
    }
    ext_sinks_by_file: dict[str, list[str]] = {
        rel: _dedup_uris(entries)
        for rel, entries in per_file_ext_sinks.items()
    }

    # ---- 2. Partition remaining files. ----------------------------------
    # Orchestrators: any file with at least one dynamic-import site (but
    # not itself a chain stage). Promoted into the framework cluster so a
    # single dashed "orchestrates" arrow can reference them.
    orchestrator_paths = {
        f for f in sites_by_file.keys()
        if f in by_path and f not in all_chain_files
    }
    framework_set = _framework_paths(
        workload_dir, code_files, all_chain_files, orchestrator_paths
    )
    # Extra data-linked files that are neither on the chain nor framework.
    data_participants: set[str] = set()
    for a, b, _kind in data_edges:
        if a in by_path and a not in all_chain_files and a not in framework_set:
            data_participants.add(a)
        if b in by_path and b not in all_chain_files and b not in framework_set:
            data_participants.add(b)
    placed = all_chain_files | framework_set | data_participants
    island_set = {p for p in by_path if p not in placed}

    # ---- 2b. Compute in-degrees from the IMPORT graph (badge). ---------
    import_edges = import_edges or []
    import_in_degree: dict[str, int] = defaultdict(int)
    import_importers: dict[str, set[str]] = defaultdict(set)
    for a, b in import_edges:
        if a in by_path and b in by_path and a != b:
            if a not in import_importers[b]:
                import_importers[b].add(a)
                import_in_degree[b] += 1

    # ---- 3. Pre-compute framework cluster geometry (LEFT of chains). ---
    framework_sorted = sorted(
        framework_set,
        key=lambda p: (0 if by_path[p]["name"] == "main.py" else
                       1 if p in orchestrator_paths else
                       2 if by_path[p]["name"].startswith("base_") else
                       3 if by_path[p]["name"] == "__init__.py" else
                       4, by_path[p]["name"].lower(), p),
    )
    clusters: list[GraphCluster] = []

    cluster_x = _DIAG_PADDING_X
    cluster_y = _FRAMEWORK_TOP
    cluster_w = 0
    cluster_h = 0
    fw_cols = 0
    fw_rows = 0
    if framework_sorted:
        fw_cols = min(_FRAMEWORK_MAX_COLS, max(1, len(framework_sorted)))
        fw_rows = (len(framework_sorted) + fw_cols - 1) // fw_cols
        inner_w = fw_cols * _FRAMEWORK_NODE_W + (fw_cols - 1) * _FRAMEWORK_HGAP
        cluster_w = inner_w + _FRAMEWORK_PAD_X * 2
        cluster_h = (_FRAMEWORK_LABEL_H
                     + fw_rows * _FRAMEWORK_NODE_H
                     + (fw_rows - 1) * _FRAMEWORK_VGAP
                     + _FRAMEWORK_PAD_Y)

    # ---- 4. (Legacy per-chain columns removed.) --------------------------
    # The historical chain_columns list is no longer used — the per-file
    # external endpoint discovery in Section 1 already produces per-file
    # (rel_path -> [(raw_uri, sig)]) mappings that Section 5 consumes
    # directly. Removing this list keeps the endpoint model single-source
    # (no drift between "which pill exists" and "where it renders").

    # ---- 5. Layered (Sugiyama) per-component layout. ----------------------
    # For each chain component compute the topological depth of every chain
    # file over the ``kind="data"`` sub-graph restricted to that component.
    # depth(f) = 0 when nothing in the component feeds f; otherwise
    # depth(f) = 1 + max(depth(producer)) across incoming data-edge sources.
    #
    # A row spans one depth. Nodes at the same depth are horizontally
    # aligned. Chain columns for independent components are laid out
    # side-by-side, each centered within its own local component width.
    nodes: list[GraphNode] = []
    node_coords: dict[str, tuple[int, int, int, int]] = {}

    if framework_sorted:
        chains_start_x = cluster_x + cluster_w + _FRAMEWORK_CHAIN_GAP
    else:
        chains_start_x = _DIAG_PADDING_X

    # Restrict data edges to writer→reader ``data`` and YAML-topology
    # ``yaml_dag`` pairs — orchestrates, framework, and factory_dispatch
    # don't participate in the depth computation.
    actual_data_pairs = {(a, b) for a, b, k in data_edges if k in ("data", "yaml_dag")}

    # Per-component: build local adjacency + reverse adjacency restricted
    # to that component's chain files, compute depths, then bucket into rows.
    #
    # Cycles: the topological_depth loop below detects unresolved nodes
    # (a strongly-connected component in the data sub-graph) and assigns
    # them the maximum depth of any resolved node + 1 so the diagram still
    # renders. Same-row / back edges then flag the cycle visually.
    chain_top_reader_ids: list[str] = []
    tallest_chain_bottom = _CHAIN_START_Y
    component_x_offsets: list[int] = []
    # ``chain_depth_rows``: per component, list of (depth -> [file_ids in
    # left-to-right order]) so we know which files share a row for the
    # edge-routing step.
    chain_depth_rows: list[dict[int, list[str]]] = []
    # ``chain_depth_of``: per component, file -> depth.
    chain_depth_of: list[dict[str, int]] = []
    # Track each chain's writer id (last file in the chain source order)
    # so its external sink can be aligned below it.
    chain_writer_ids: list[str] = []
    running_x = chains_start_x
    for idx, chain in enumerate(chains):
        chain_set = set(chain)
        # Component-local data adjacency (writer→reader).
        local_adj: dict[str, list[str]] = defaultdict(list)
        local_rev: dict[str, list[str]] = defaultdict(list)
        for a, b in actual_data_pairs:
            if a in chain_set and b in chain_set and a != b:
                if b not in local_adj[a]:
                    local_adj[a].append(b)
                    local_rev[b].append(a)

        # Topological depth via memoized DFS. Nodes on a cycle get a
        # fallback depth equal to (max resolved depth + 1) so they still
        # appear on the diagram.
        depth: dict[str, int] = {}
        VISITING = -1

        def _depth(node: str, stack: set[str]) -> int:
            if node in depth and depth[node] != VISITING:
                return depth[node]
            if node in stack:
                # Cycle — resolve later.
                return -1
            stack.add(node)
            producers = local_rev.get(node, [])
            best = -1
            for p in producers:
                d = _depth(p, stack)
                if d >= 0 and d > best:
                    best = d
            stack.discard(node)
            resolved = 0 if best < 0 else best + 1
            depth[node] = resolved
            return resolved

        for f in chain:
            _depth(f, set())
        # For any node whose participation in a cycle prevented a clean
        # depth, park it one row below the deepest resolved row.
        max_depth = max((d for d in depth.values() if d >= 0), default=0)
        for f in chain:
            if depth.get(f, -1) < 0:
                depth[f] = max_depth + 1

        chain_depth_of.append(depth)

        # Bucket into rows, sort each row by rel_path for stability.
        rows: dict[int, list[str]] = defaultdict(list)
        for f in chain:
            rows[depth[f]].append(f)
        for d in list(rows.keys()):
            rows[d].sort()
        chain_depth_rows.append(dict(rows))

        # Component width = max row width. No longer widened to fit
        # external source/sink pills — those are now metadata on the
        # chain-node itself (tooltip preview + click-opened detail
        # panel), not standalone SVG pills.
        max_row_files = max(len(v) for v in rows.values()) if rows else 1
        row_w_chain = max_row_files * _CHAIN_NODE_W + max(0, max_row_files - 1) * _LAYER_HGAP
        component_w = max(row_w_chain, _CHAIN_NODE_W)
        component_center = running_x + component_w // 2
        component_x_offsets.append(running_x)

        # The "reader" for anchoring the external source: the alphabetically
        # first depth-0 file (matches the row's left-to-right ordering).
        depth0 = sorted(rows.get(0, [])) if rows else []
        reader_id = depth0[0] if depth0 else (chain[0] if chain else "")
        # Anchor writer at the LAST file in the original chain order (kept
        # for backwards-compat with the orchestrator arrow routing).
        writer_id = chain[-1] if chain else ""
        chain_writer_ids.append(writer_id)

        y_cursor = _CHAIN_START_Y
        first_chain_file_id: str | None = None

        # ---- 5a. Chain-file rows -------------------------------------------
        # Place each depth row. External source/sink pseudo-nodes are NOT
        # emitted anymore — their data is attached as node metadata
        # (``external_sources`` / ``external_sinks``) and surfaced via the
        # tooltip + click-opened detail panel.
        depths_sorted = sorted(rows.keys())
        # ``row_y_start_by_depth`` remembers the y coordinate at which each
        # depth row begins — used later for edge routing.
        for row_i, d in enumerate(depths_sorted):
            files_in_row = rows[d]
            n = len(files_in_row)
            row_w = n * _CHAIN_NODE_W + max(0, n - 1) * _LAYER_HGAP
            row_x0 = component_center - row_w // 2
            row_y = y_cursor
            for i, rel in enumerate(files_in_row):
                x = row_x0 + i * (_CHAIN_NODE_W + _LAYER_HGAP)
                info = by_path[rel]
                nodes.append(
                    GraphNode(
                        id=rel,
                        label=_truncate_label(info["name"]),
                        full_label=info["name"],
                        path=rel,
                        x=x, y=row_y,
                        width=_CHAIN_NODE_W, height=_CHAIN_NODE_H,
                        status="High", group="chain",
                        external_sources=list(ext_sources_by_file.get(rel, [])),
                        external_sinks=list(ext_sinks_by_file.get(rel, [])),
                    )
                )
                node_coords[rel] = (x, row_y, _CHAIN_NODE_W, _CHAIN_NODE_H)
                if first_chain_file_id is None:
                    first_chain_file_id = rel

            # Advance y_cursor for the next chain-file row. Chain nodes now
            # occupy the entire SVG vertical extent — no per-file sink pill
            # rows dangling below them.
            y_after_row = row_y + _CHAIN_NODE_H
            if row_i < len(depths_sorted) - 1:
                y_cursor = y_after_row + _LAYER_VGAP_DAG
            else:
                y_cursor = y_after_row

        chain_top_reader_ids.append(first_chain_file_id or "")

        tallest_chain_bottom = max(tallest_chain_bottom, y_cursor)
        running_x += component_w + _MULTI_CHAIN_HGAP

    chain_bottom_y = tallest_chain_bottom

    # ---- 6. Emit framework cluster nodes (grid, LEFT of chains). -------
    if framework_sorted:
        fw_node_ids: list[str] = []
        inner_x0 = cluster_x + _FRAMEWORK_PAD_X
        inner_y0 = cluster_y + _FRAMEWORK_LABEL_H
        for i, rel in enumerate(framework_sorted):
            row = i // fw_cols
            col = i % fw_cols
            nx = inner_x0 + col * (_FRAMEWORK_NODE_W + _FRAMEWORK_HGAP)
            ny = inner_y0 + row * (_FRAMEWORK_NODE_H + _FRAMEWORK_VGAP)
            info = by_path[rel]
            deg = import_in_degree.get(rel, 0)
            basename = info["name"]
            display = _truncate_label(basename)
            if deg > 0:
                display = f"{display} · \U0001F9F2 {deg}"
            nodes.append(
                GraphNode(
                    id=rel,
                    label=display,
                    full_label=basename,
                    path=rel,
                    x=nx,
                    y=ny,
                    width=_FRAMEWORK_NODE_W,
                    height=_FRAMEWORK_NODE_H,
                    status="High",
                    group="framework",
                    in_degree=deg,
                )
            )
            node_coords[rel] = (nx, ny, _FRAMEWORK_NODE_W, _FRAMEWORK_NODE_H)
            fw_node_ids.append(rel)

        clusters.append(
            GraphCluster(
                label="Framework (migration prerequisites)",
                x=cluster_x,
                y=cluster_y,
                width=cluster_w,
                height=cluster_h,
                node_ids=fw_node_ids,
            )
        )

    # ---- 7. Islands row (files with no chain / framework role). --------
    misc = sorted(data_participants | island_set,
                  key=lambda p: by_path[p]["name"].lower())
    if misc:
        cols = min(_ISLAND_MAX_COLS, max(1, len(misc)))
        island_y = chain_bottom_y + _ISLAND_TOP_PAD
        row_x0 = _DIAG_PADDING_X
        for i, rel in enumerate(misc):
            row = i // cols
            col = i % cols
            nx = row_x0 + col * (_ISLAND_NODE_W + _ISLAND_HGAP)
            ny = island_y + row * (_ISLAND_NODE_H + _ISLAND_VGAP)
            info = by_path[rel]
            nodes.append(
                GraphNode(
                    id=rel,
                    label=_truncate_label(info["name"]),
                    full_label=info["name"],
                    path=rel,
                    x=nx,
                    y=ny,
                    width=_ISLAND_NODE_W,
                    height=_ISLAND_NODE_H,
                    status="High",
                    group="",
                )
            )
            node_coords[rel] = (nx, ny, _ISLAND_NODE_W, _ISLAND_NODE_H)

    # ---- 8. Edges. ------------------------------------------------------
    gedges: list[GraphEdge] = []

    # Precompute per-component right edge (for bezier skip-edge routing —
    # we want the control point to sit OUTSIDE the component so the curve
    # doesn't cross any intermediate row).
    component_right_x: list[int] = []
    for idx, (offset, rows_map) in enumerate(zip(component_x_offsets, chain_depth_rows)):
        max_row_files = max((len(v) for v in rows_map.values()), default=1)
        row_w_chain = max_row_files * _CHAIN_NODE_W + max(0, max_row_files - 1) * _LAYER_HGAP
        # Component width no longer accommodates external-endpoint pills —
        # those are chain-node metadata now, not standalone SVG rects.
        component_w = max(row_w_chain, _CHAIN_NODE_W)
        component_right_x.append(offset + component_w)

    # Build a fast (file -> component index) map so cross-component skip
    # edges can locate the correct right-edge for routing.
    file_to_component: dict[str, int] = {}
    for idx, chain in enumerate(chains):
        for rel in chain:
            file_to_component[rel] = idx

    def _vert_edge(src: str, tgt: str, kind: str = "data", label: str = "") -> None:
        """Straight vertical edge: source bottom-center → target top-center."""
        if src not in node_coords or tgt not in node_coords:
            return
        sx, sy, sw, sh = node_coords[src]
        tx, ty, tw, th = node_coords[tgt]
        gedges.append(
            GraphEdge(
                x1=sx + sw // 2, y1=sy + sh,
                x2=tx + tw // 2, y2=ty,
                source=src, target=tgt,
                kind=kind, label=label,
            )
        )

    def _skip_edge(src: str, tgt: str, kind: str = "data", label: str = "") -> None:
        """Bezier skip-edge from source right → curve past component → target top.

        Used when depth(target) > depth(source) + 1 so intermediate rows
        aren't drawn through by a straight line. Control point sits to the
        RIGHT of the enclosing component so the curve arcs cleanly.
        """
        if src not in node_coords or tgt not in node_coords:
            return
        sx, sy, sw, sh = node_coords[src]
        tx, ty, tw, th = node_coords[tgt]
        x1 = sx + sw
        y1 = sy + sh // 2
        x2 = tx + tw // 2
        y2 = ty
        comp_idx = file_to_component.get(src, file_to_component.get(tgt, 0))
        right_bound = component_right_x[comp_idx] if comp_idx < len(component_right_x) else max(sx + sw, tx + tw)
        ctrl_x = max(right_bound + 30, x1 + 40)
        ctrl_y = (y1 + y2) // 2
        path_d = f"M {x1} {y1} C {ctrl_x} {y1}, {ctrl_x} {y2}, {x2} {y2}"
        gedges.append(
            GraphEdge(
                x1=x1, y1=y1, x2=x2, y2=y2,
                source=src, target=tgt,
                kind=kind, label=label,
                path_d=path_d,
            )
        )

    def _same_row_edge(src: str, tgt: str, kind: str = "data", label: str = "") -> None:
        """Curved horizontal edge for the unusual same-depth (cycle) case.

        Arcs ABOVE the row via a bezier control point offset upward — the
        arrowhead lands on the target's TOP edge so directionality is
        obvious.
        """
        if src not in node_coords or tgt not in node_coords:
            return
        sx, sy, sw, sh = node_coords[src]
        tx, ty, tw, th = node_coords[tgt]
        x1 = sx + sw // 2
        y1 = sy
        x2 = tx + tw // 2
        y2 = ty
        # Arc upward — control offsets 60 px above the row.
        ctrl_y = min(y1, y2) - 60
        ctrl_x = (x1 + x2) // 2
        path_d = f"M {x1} {y1} Q {ctrl_x} {ctrl_y} {x2} {y2}"
        gedges.append(
            GraphEdge(
                x1=x1, y1=y1, x2=x2, y2=y2,
                source=src, target=tgt,
                kind=kind, label=label,
                path_d=path_d,
            )
        )

    def _generic_edge(src: str, tgt: str, kind: str = "data", label: str = "") -> None:
        """Fallback for cross-component / factory_dispatch / other edges:
        source right-mid → target left-mid straight line."""
        if src not in node_coords or tgt not in node_coords:
            return
        sx, sy, sw, sh = node_coords[src]
        tx, ty, tw, th = node_coords[tgt]
        gedges.append(
            GraphEdge(
                x1=sx + sw, y1=sy + sh // 2,
                x2=tx, y2=ty + th // 2,
                source=src, target=tgt,
                kind=kind, label=label,
            )
        )

    # 8a. External endpoint arrows removed. Previously we drew:
    #   External source pill → depth-0 reader     (fan-out per consumer)
    #   Producer → external sink pill             (per producer)
    # Those pills are no longer emitted; their data lives on the chain
    # node itself (``external_sources`` / ``external_sinks``) and is
    # surfaced via the tooltip + detail-panel UI.
    #
    # 8b. Data edges between chain files: route by depth difference.
    # Track which pairs we've already drawn so the fan-out / cross-component
    # loop below doesn't emit duplicates.
    drawn_chain_pairs: set[tuple[str, str]] = set()
    for idx, chain in enumerate(chains):
        depth_map = chain_depth_of[idx]
        chain_set = set(chain)
        for a, b in actual_data_pairs:
            if a not in chain_set or b not in chain_set:
                continue
            da = depth_map.get(a, 0)
            db = depth_map.get(b, 0)
            diff = db - da
            if diff == 1:
                _vert_edge(a, b, kind="data")
            elif diff > 1:
                _skip_edge(a, b, kind="data")
            elif diff == 0:
                # Same row — cycle-breaking horizontal edge.
                _same_row_edge(a, b, kind="data")
            else:
                # Back edge (cycle) — draw as arced same-row style so it's
                # visually flagged, but arc DOWNWARD to differentiate.
                _same_row_edge(a, b, kind="data")
            drawn_chain_pairs.add((a, b))

    # 8c. Data / factory_dispatch edges that CROSS components, or are
    # otherwise not already drawn. Cross-component data edges are rare
    # (independent pipelines by definition share no chain files); when
    # they exist we render them with the generic right→left arrow.
    fan_by_target: dict[str, int] = defaultdict(int)
    for a, b, kind in data_edges:
        if kind == "factory_dispatch":
            fan_by_target[b] += 1
    for a, b, kind in data_edges:
        if (a, b) in drawn_chain_pairs:
            continue
        if a not in node_coords or b not in node_coords:
            continue
        if kind == "factory_dispatch":
            label = f"1 of {fan_by_target.get(b, 1)}"
            _generic_edge(a, b, kind="factory_dispatch", label=label)
        elif kind in (None, "data", "", "yaml_dag"):
            _generic_edge(a, b, kind="data")

    # Orchestrator → reader dashed blue "orchestrates" edge(s). For every
    # independent pipeline, pick the LEAF orchestrator (the one no other
    # orchestrator reaches via the import graph) and draw exactly one
    # bezier arrow from it to the pipeline's reader. Bezier routing keeps
    # the arrow out of the framework cluster rectangle, and the label is
    # placed BELOW the cluster bottom so it never overlaps the cluster.
    if orchestrator_paths and chains:
        # For each pipeline, filter orchestrators to those whose sites
        # resolve into THIS chain's file set. Pre-compute per-orchestrator
        # resolved-file sets ONCE, then intersect with each chain — cheaper
        # than re-resolving sites inside the per-pipeline loop.
        orch_resolved: dict[str, set[str]] = {}
        for orch in orchestrator_paths:
            resolved: set[str] = set()
            for site in sites_by_file.get(orch, []):
                files_r, _reason = _resolve_dynamic_import_site(
                    site, orch, config_data, code_files,
                    entry_points or {}, workload_dir,
                )
                resolved.update(files_r)
            orch_resolved[orch] = resolved

        cluster_right = cluster_x + cluster_w if framework_sorted else _DIAG_PADDING_X
        cluster_bottom = cluster_y + cluster_h if framework_sorted else _FRAMEWORK_TOP
        for idx, chain in enumerate(chains):
            reader_id = chain[0] if chain else ""
            if reader_id not in node_coords:
                continue
            chain_files = set(chain)
            per_pipeline = {
                o for o, files_r in orch_resolved.items()
                if files_r & chain_files
            }
            if not per_pipeline:
                per_pipeline = set(orchestrator_paths)
            leaves = _leaf_orchestrators(per_pipeline, import_edges)
            if not leaves:
                leaves = per_pipeline
            rx, ry, rw, rh = node_coords[reader_id]
            for orch in sorted(leaves):
                if orch not in node_coords:
                    continue
                ox, oy, ow, oh = node_coords[orch]
                # Start at orchestrator's RIGHT edge, land at reader's TOP edge.
                x1 = ox + ow
                y1 = oy + oh // 2
                x2 = rx + rw // 2
                y2 = ry
                # Bezier control point sits OUTSIDE the framework cluster's
                # right boundary and BELOW the cluster bottom so the curve
                # arcs around the cluster rather than through it.
                ctrl_x = max(cluster_right + 40, x2)
                ctrl_y = cluster_bottom + 30
                path_d = f"M {x1} {y1} Q {ctrl_x} {ctrl_y} {x2} {y2}"
                # Label placement: horizontal midpoint of the curve, y
                # BELOW the framework cluster bottom (never overlaps).
                label_x = (x1 + x2) // 2
                label_y = cluster_bottom + 18
                gedges.append(
                    GraphEdge(
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        source=orch,
                        target=reader_id,
                        kind="orchestrates",
                        label="orchestrates",
                        path_d=path_d,
                        label_x=label_x,
                        label_y=label_y,
                    )
                )

    # ---- 8d. Recompute chain-node in_degree / blast_radius from data edges.
    # The template tooltip surfaces ``n.in_degree`` as "N direct data
    # producer(s)" and ``n.blast_radius`` as "N downstream consumer(s)".
    # For chain nodes those numbers must reflect the DATA-FLOW graph, not
    # the static import graph — a chain file might have zero static
    # importers yet still be fed by many upstream writers. Framework
    # nodes keep their static-import in_degree (that's the meaningful
    # "how many workload files import this prerequisite" signal).
    data_producers_of: dict[str, set[str]] = defaultdict(set)
    data_consumers_of: dict[str, set[str]] = defaultdict(set)
    all_chain_files_set: set[str] = set()
    for c in chains:
        all_chain_files_set.update(c)
    for a, b in actual_data_pairs:
        # Only count edges BETWEEN workload files (both endpoints are
        # chain files here — external endpoints aren't in actual_data_pairs).
        if a in all_chain_files_set and b in all_chain_files_set and a != b:
            data_producers_of[b].add(a)
            data_consumers_of[a].add(b)
    # Blast radius = transitive downstream consumers via BFS.
    def _blast_radius(start: str) -> int:
        seen: set[str] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            for nxt in data_consumers_of.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return len(seen)
    for n in nodes:
        if n.group == "chain":
            n.in_degree = len(data_producers_of.get(n.id, set()))
            n.blast_radius = _blast_radius(n.id)

    # ---- 9. SVG dimensions. --------------------------------------------
    max_x = max(
        (nx + nw for nx, ny, nw, nh in node_coords.values()),
        default=_DIAG_PADDING_X,
    )
    max_y = max(
        (ny + nh for nx, ny, nw, nh in node_coords.values()),
        default=_DIAG_PADDING_Y,
    )
    if clusters:
        max_x = max(max_x, clusters[0].x + clusters[0].width)
        max_y = max(max_y, clusters[0].y + clusters[0].height)

    svg_width = max_x + _DIAG_PADDING_X
    svg_height = max_y + _DIAG_PADDING_Y

    # File count reported to the UI — real code files only. The
    # ``startswith("external")`` guard is a defensive no-op now that
    # external-endpoint pseudo-nodes are no longer emitted.
    file_count = sum(1 for n in nodes if not n.group.startswith("external"))
    edge_count = sum(1 for e in gedges if e.kind == "data")

    return DependencyGraph(
        module="Project",
        width=svg_width,
        height=svg_height,
        file_count=file_count,
        edge_count=edge_count,
        nodes=nodes,
        edges=gedges,
        clusters=clusters,
        pipeline_count=len(chains),
    )


def _connected_components(
    node_ids: list[str],
    edges: list[tuple[str, str]],
) -> list[list[str]]:
    """Weakly-connected components over the undirected projection of ``edges``.

    Union-find. Every node id is its own component until an edge merges two.
    Used to find isolated "island" modules that share no code with the rest
    of the project (Quick-Win pilots).
    """
    parent = {n: n for n in node_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path-halving
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    nodeset = set(node_ids)
    for a, b in edges:
        if a in nodeset and b in nodeset:
            union(a, b)

    comps: dict[str, list[str]] = defaultdict(list)
    for n in node_ids:
        comps[find(n)].append(n)
    return list(comps.values())


def _module_from_component(
    comp: list[str],
    by_path: dict[str, dict],
    edges: list[tuple[str, str]],
) -> IsolatedModule:
    """Build an :class:`IsolatedModule` (files + intra-component edges) from a
    single connected component of file paths."""
    files = sorted(comp, key=lambda p: by_path[p]["name"].lower())
    mfiles = [
        IsolatedModuleFile(
            path=p,
            name=by_path[p]["name"],
            lines=by_path[p]["lines"],
            status="High",  # backfilled in Assessment.merge
        )
        for p in files
    ]
    idx_of = {p: i for i, p in enumerate(files)}
    comp_set = set(files)
    comp_edges = sorted(
        {
            (idx_of[a], idx_of[b])
            for a, b in edges
            if a in comp_set and b in comp_set and a != b
        }
    )
    return IsolatedModule(
        files=mfiles,
        file_count=len(mfiles),
        total_lines=sum(f.lines for f in mfiles),
        edges=list(comp_edges),
    )


def _build_isolated_modules(
    code_files: list[dict],
    edges: list[tuple[str, str]],
) -> tuple[list[IsolatedModule], int, IsolatedModule | None, int]:
    """Partition the codebase by weakly-connected component for migration.

    Runs connected-components over all logic files (``__init__.py`` package
    markers are counted separately, since they carry no migratable logic). The
    largest component is the "main cluster" — files coupled tightly enough that
    they should migrate together. **Every other component is an isolated
    module**: it shares zero code with the main cluster, so it's a safe pilot
    to migrate first regardless of size. When nothing clusters (no import edges)
    every standalone file is its own isolated module.

    Returns ``(isolated_modules, largest_component_size, main_cluster,
    package_marker_count)`` so the report can reconcile every file:
    ``len(logic files) == sum(island sizes) + main_cluster size``.
    """
    by_path = {info["rel_path"]: info for info in code_files}
    package_marker_count = sum(1 for p in by_path if Path(p).name == "__init__.py")
    node_ids = [p for p in by_path if Path(p).name != "__init__.py"]
    if not node_ids:
        return [], 0, None, package_marker_count

    comps = _connected_components(node_ids, edges)
    comps.sort(key=lambda c: (-len(c), sorted(c)[0] if c else ""))
    largest = len(comps[0]) if comps else 0

    # A "main cluster" only exists if some component actually couples files
    # together (size >= 2). Otherwise every file stands alone and all are
    # isolated quick wins.
    has_main_cluster = largest >= 2
    main_cluster = (
        _module_from_component(comps[0], by_path, edges) if has_main_cluster else None
    )
    candidates = comps[1:] if has_main_cluster else comps

    # Isolation — not size — defines a quick win: any component outside the main
    # cluster shares no code with it, so it can be cut over independently.
    islands = [_module_from_component(comp, by_path, edges) for comp in candidates]

    # Smallest / simplest islands first — the easiest pilots to pitch.
    islands.sort(key=lambda m: (m.file_count, m.total_lines))
    return islands, largest, main_cluster, package_marker_count


def _build_wave_graph(waves: list[MigrationWave]) -> WaveGraph | None:
    """Lay out wave rectangles in rows of 10 with curved Bezier arrows.

    Visual model matches the reference prototype:
    * Independent waves (no prerequisites) draw green; dependent waves
      draw blue.
    * Each prerequisite ``w_p`` of wave ``w_t`` becomes one arrow from
      the top-center of ``w_p`` to the top-center of ``w_t``. Control
      point sits at the midpoint x-coord with a small vertical bow so
      overlapping arrows stay readable.
    """
    if not waves:
        return None

    n = len(waves)
    nodes: list[WaveGraphNode] = []
    centers: dict[int, tuple[int, int]] = {}  # wave_index -> (top-center x, top y)
    for i, wave in enumerate(waves, start=1):
        col = (i - 1) % _WAVES_PER_ROW
        row = (i - 1) // _WAVES_PER_ROW
        x = _WAVE_PADDING_X + col * _WAVE_PITCH
        y = _WAVE_PADDING_Y + row * _WAVE_ROW_PITCH
        nodes.append(
            WaveGraphNode(
                wave_index=i,
                label=f"Wave {i}",
                sublabel=f"{len(wave.files)} files",
                x=x,
                y=y,
                width=_WAVE_NODE_W,
                height=_WAVE_NODE_H,
                independent=not wave.depends_on_waves,
            )
        )
        centers[i] = (x + _WAVE_NODE_W // 2, y)

    edges: list[WaveGraphEdge] = []
    for i, wave in enumerate(waves, start=1):
        x_target, y_target = centers[i]
        for prereq in wave.depends_on_waves:
            if prereq not in centers:
                continue
            x_source, y_source = centers[prereq]
            cx = (x_source + x_target) // 2
            # Slight downward bow for same-row arrows; midpoint for cross-row
            if y_source == y_target:
                cy = y_source + 5
            else:
                cy = (y_source + y_target) // 2
            edges.append(
                WaveGraphEdge(x1=x_source, y1=y_source, cx=cx, cy=cy, x2=x_target, y2=y_target)
            )

    cols_used = min(n, _WAVES_PER_ROW)
    rows_used = (n + _WAVES_PER_ROW - 1) // _WAVES_PER_ROW
    width = _WAVE_PADDING_X * 2 + cols_used * _WAVE_PITCH - _WAVE_HGAP
    height = _WAVE_PADDING_Y * 2 + rows_used * _WAVE_ROW_PITCH - (_WAVE_ROW_PITCH - _WAVE_NODE_H)
    return WaveGraph(width=width, height=height, nodes=nodes, edges=edges)


def _circular_dependencies_from(
    cycles: list[list[str]],
    code_files: list[dict],
) -> list[CircularDependency]:
    """Convert raw SCC rel_path lists into ``CircularDependency`` rows."""
    name_by_path = {info["rel_path"]: info["name"] for info in code_files}
    out: list[CircularDependency] = []
    for cyc in cycles:
        names = sorted({name_by_path.get(p, Path(p).name) for p in cyc})
        out.append(CircularDependency(files=names))
    return out


def _classify_project_type(
    pattern_hits: "Counter[str]",
    third_party_libs: list,
    code_files: list[dict],
    all_imports: "Counter[str]",
    has_xml: bool,
    has_custom_validation: bool,
) -> "ProjectType":
    """Return the code-complexity project type for the scanned workload.

    Three tiers, evaluated solely on code syntax and data paradigms.
    Infrastructure concerns (EAI, unsupported packages) are handled as
    Deployment Prerequisites in the report and do NOT influence this tier.

    Tier assignment is driven by ``llm_capabilities.json``:
      * ``"specialized"`` → Specialized Architecture: paradigm maps to a
        specific Snowflake feature (Snowpark ML, Dynamic Tables) and may
        require targeted architectural validation.
      * ``"ai_assisted"``  → AI-Assisted Refactoring: Cortex LLM engine can
        restructure the pattern; no architectural change needed.
      * ``"standard"``     → Standard Conversion: deterministic transpiler only.

    To reclassify a pattern (e.g., when pyspark.ml support improves), update
    ``scripts/data/llm_capabilities.json`` — no Python changes required.
    """
    specialized_triggers: list[str] = []
    ai_triggers: list[str] = []

    for name, _regex, _impact in _COMPLEX_PATTERNS:
        count = pattern_hits.get(name, 0)
        if count == 0:
            continue
        label_str = f"{name} ({count} occurrence(s))"
        ewi_prefix = _PATTERN_EWI.get(name, "")
        tier = _tier_from_ewi_prefix(ewi_prefix)
        if tier == "specialized":
            specialized_triggers.append(label_str)
        else:
            # "ai_assisted" or unknown — conservatively treated as AI-convertible.
            ai_triggers.append(label_str)

    # XML import detection (not covered by a single EWI prefix lookup alone).
    if has_xml and not any("XML" in t for t in ai_triggers):
        ai_triggers.append("XML parsing")

    # Language indicators (informational).
    lang_indicators: list[str] = []
    scala_count = sum(1 for c in code_files if c["ext"] == ".scala")
    python_count = sum(1 for c in code_files if c["ext"] == ".py")
    if scala_count:
        lang_indicators.append(f"Scala codebase ({scala_count} files)")
    if python_count:
        lang_indicators.append(f"Python codebase ({python_count} files)")

    def _tier_meta(tier_key: str) -> "dict":
        """Return the tier display config from llm_capabilities.json _tiers section."""
        return _LLM_TIER_CONFIG.get(tier_key, {})

    # --- Tier determination (highest tier wins) ----------------------------
    if specialized_triggers:
        cfg = _tier_meta("specialized")
        return ProjectType(
            label=cfg.get("label", "Specialized Architecture"),
            color=cfg.get("color", "orange"),
            resolution_engine=cfg.get("resolution_engine", "Cortex LLM Engine + Platform Mapping"),
            description="Classification triggered by: " + ", ".join(specialized_triggers) + ".",
            indicators=(specialized_triggers + ai_triggers + lang_indicators)[:6],
            expected_action=cfg.get("expected_action", ""),
            scan_advisory=cfg.get("scan_advisory", ""),
        )
    if ai_triggers:
        cfg = _tier_meta("ai_assisted")
        return ProjectType(
            label=cfg.get("label", "AI-Assisted Refactoring"),
            color=cfg.get("color", "blue"),
            resolution_engine=cfg.get("resolution_engine", "Deterministic Transpiler + Cortex LLM Engine"),
            description="Classification triggered by: " + ", ".join(ai_triggers) + ".",
            indicators=(ai_triggers + lang_indicators)[:6],
            expected_action=cfg.get("expected_action", ""),
            scan_advisory=cfg.get("scan_advisory", ""),
        )
    cfg = _tier_meta("standard")
    return ProjectType(
        label=cfg.get("label", "Standard Conversion"),
        color=cfg.get("color", "green"),
        resolution_engine=cfg.get("resolution_engine", "Deterministic Transpiler"),
        description="No complex Spark paradigms detected; eligible for direct rule-based conversion.",
        indicators=lang_indicators[:6],
        expected_action=cfg.get("expected_action", ""),
        scan_advisory=cfg.get("scan_advisory", ""),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workload-dir", required=True, type=Path)
    parser.add_argument("--project", default="unknown-project")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    assessment = scan(args.workload_dir, project=args.project)
    payload = assessment.model_dump(mode="json")
    out = json.dumps(payload, indent=2, default=str)

    if args.output:
        args.output.write_text(out)
        print(f"Wrote codebase IR to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
