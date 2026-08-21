#!/usr/bin/env python3
"""
Generate SCOS-compatible CSV reports from SCOS migration outputs.

Reads scanned ``# SCOS:`` comments from migrated files (and the source tree
inventory) to produce:
  - Reports/Issues.csv  (sourced solely from ``# SCOS:`` comments — see
    ``generate_issues_csv`` for why analysis.json is intentionally not used)
  - Reports/InputFilesInventory.csv
  - Reports/ArtifactDependencyInventory.csv
  - Reports/tool_execution.csv
  - Logs/SCOSMigration-Log-<timestamp>.log

These reports are compatible with dvp-sma-dashboard-generator.

Usage:
    python generate_scos_reports.py \
        --output-dir /path/to/output \
        --analysis /path/to/analysis.json \
        --source-dir /path/to/original/source \
        --project-name "MyProject" \
        --email "user@company.com" \
        --company "Company Inc" \
        --language python
"""

import argparse
import ast
import csv
import difflib
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from notebook_io import (
    PYTHON_MIGRATION_HEADER_DOCSTRING,
    SCALA_MIGRATION_HEADER_COMMENT,
    SKIP_DIRS,
    STUB_HEADER_SENTINEL,
    detect_format,
    is_notebook,
    parse_notebook,
    walk_filtered,
    write_notebook,
)

TOOL_VERSION = "scos-migration-1.0.0"

# Back-compat aliases for any external consumer importing these from this
# module — the single source of truth now lives in notebook_io.
_walk_filtered = walk_filtered

# SNOW-3347464: Scala migration header comment inserted deterministically
SCALA_MIGRATION_HEADER = SCALA_MIGRATION_HEADER_COMMENT
PYTHON_MIGRATION_HEADER = PYTHON_MIGRATION_HEADER_DOCSTRING

# Per-language output conventions. Java shares Scala's C-style comment syntax
# and ``SPRKCNTSCL`` EWI prefix but has its own extension and notebook cell
# language. Before this table every call site tested ``language == "scala"``
# and sent everything else down the Python path, so Java output silently got
# ``.py`` extensions, ``#`` comments and a Python docstring header.
_LANG_CONVENTIONS: dict[str, dict[str, str]] = {
    "python": {"ext": ".py", "comment": "#", "cell_language": "python", "label": "Python"},
    "scala": {"ext": ".scala", "comment": "//", "cell_language": "scala", "label": "Scala"},
    "java": {"ext": ".java", "comment": "//", "cell_language": "java", "label": "Java"},
}


def _lang_conv(language: str) -> dict[str, str]:
    """Output conventions for *language*, defaulting to Python."""
    return _LANG_CONVENTIONS.get(language, _LANG_CONVENTIONS["python"])


def _source_ext(language: str) -> str:
    """Source-file extension for *language* (``.py`` / ``.scala`` / ``.java``)."""
    return _lang_conv(language)["ext"]


DATA_DIR = Path(__file__).parent / "data"

# Notebook-aware technology classification using detect_format from notebook_io.
# Maps (format, language) pairs to Technology values for InputFilesInventory.csv.
_NOTEBOOK_TECHNOLOGY_MAP = {
    ("ipynb", "python"): "JupyterNotebook",
    ("ipynb", "scala"): "JupyterNotebook",
    ("ipynb", "sql"): "JupyterNotebook",
    ("ipynb", "unknown"): "JupyterNotebook",
    ("native_json", "python"): "PythonDbxNotebook",
    ("native_json", "scala"): "ScalaDbxNotebook",
    ("native_json", "sql"): "SqlDbxNotebook",
    ("exported_text", "python"): "PythonDbx",
    ("exported_text", "scala"): "ScalaDbx",
}

# Fallback for plain (non-notebook) files, keyed by extension.
_PLAIN_TECHNOLOGY_MAP = {
    ".py": "Python",
    ".scala": "Scala",
    ".sql": "SQL",
    ".r": "R",
    ".R": "R",
}


def _classify_technology(file_path: str, ext: str) -> str:
    """Return the Technology string for a source file.

    Uses ``detect_format`` from *notebook_io* to distinguish Databricks
    notebook formats that share the same file extension (e.g. ``.scala``
    can be a native-JSON notebook, an exported-text notebook, or a plain
    Scala file).
    """
    fmt_info = detect_format(file_path)
    fmt = fmt_info.get("format", "not_notebook")
    lang = fmt_info.get("language", "unknown")

    if fmt != "not_notebook":
        return _NOTEBOOK_TECHNOLOGY_MAP.get((fmt, lang), "Other")
    return _PLAIN_TECHNOLOGY_MAP.get(ext, "Other")


# Build/config files that ARE conversion targets (Phase 3 updates them) even
# though ``_classify_technology`` returns "Other" for them. Mirrors the build
# names recognized by verify_phase / the Phase 0 manifest snippet.
_BUILD_FILENAMES = {
    "build.sbt", "pom.xml", "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts",
}
_BUILD_EXTS = {".sbt"}


def _is_conversion_unit(fname: str, ext: str, technology: str) -> bool:
    """True if the file is a migration target (source code or a build file).

    Code (Python/Scala/SQL/R and notebooks) resolves to a non-"Other"
    technology; build files are matched by name/extension. Everything else —
    data files (CSV/JSON/Parquet/…), resources, docs — is NOT a conversion unit
    and is marked ``Ignored`` in the inventory so it is not counted as migration
    work. This is the code-vs-data split: only conversion units (``Ignored ==
    "False"``) represent actual migration effort.
    """
    if technology != "Other":
        return True  # source code (incl. notebooks)
    if fname in _BUILD_FILENAMES or ext in _BUILD_EXTS:
        return True  # build/config files are updated in Phase 3
    return False  # data / resource / doc — not a conversion unit

# Well-known third-party Python packages (top-level import name)
KNOWN_THIRD_PARTY_PYTHON = {
    "numpy", "pandas", "scipy", "sklearn", "matplotlib", "seaborn",
    "requests", "flask", "django", "sqlalchemy", "boto3", "botocore",
    "google", "azure", "pyspark", "databricks", "delta", "pyarrow",
    "yaml", "pyyaml", "toml", "dotenv", "pytest", "unittest",
    "snowflake", "cryptography", "paramiko", "jinja2", "click",
    "tqdm", "rich", "loguru", "celery", "redis", "kafka",
    "tensorflow", "torch", "keras", "xgboost", "lightgbm",
}

# Well-known third-party Scala/Java packages (prefix)
KNOWN_THIRD_PARTY_SCALA = {
    "org.apache.spark", "org.apache.hadoop", "org.apache.kafka",
    "org.apache.commons", "org.apache.http", "org.apache.log4j",
    "org.apache.avro", "org.apache.parquet", "org.apache.hive",
    "com.databricks", "io.delta", "org.scalatest", "org.scalactic",
    "com.typesafe", "akka", "play", "cats", "zio",
    "com.snowflake", "net.snowflake",
    # SNOW-3362688: Expanded third-party coverage
    "com.amazonaws", "com.google", "com.fasterxml", "com.microsoft",
    "com.twitter", "com.github", "com.hortonworks", "com.cloudera",
    "io.circe", "io.spray", "io.netty",
    "org.json4s", "org.slf4j", "org.log4s", "org.mockito", "org.specs2",
    "org.joda", "org.scalaj", "org.rogach",
    "net.liftweb", "net.ceedubs",
    "pureconfig", "scopt", "enumeratum", "shapeless", "monocle",
    "za.co.absa",
    "spark",  # unqualified spark.* imports
}

# SNOW-3362688: Scala/Java standard library prefixes
SCALA_JAVA_STDLIB = {
    "scala", "java", "javax", "jdk", "sun",
}

# SNOW-3362688: Detect user-defined packages from package declarations in source files
def detect_user_packages(source_dir: str, language: str = "scala") -> set[str]:
    """Scan JVM sources for package declarations and return user package prefixes.

    Extracts `package com.company.project` declarations to identify which
    import prefixes belong to user code rather than third-party libraries.
    Returns a set of package prefixes (e.g., {"com.socgen.htr"}).

    Handles Scala (``package com.foo``) and Java (``package com.foo;``) — the
    trailing ``;`` is stripped for both.
    """
    user_packages: set[str] = set()
    ext = _source_ext(language)
    for root, _dirs, files in _walk_filtered(source_dir):
        for fname in files:
            if not fname.endswith(ext):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("package ") and not line.startswith("package object"):
                            pkg = line[len("package "):].strip().rstrip("{;")
                            if pkg and not pkg.startswith("("):
                                user_packages.add(pkg)
                        # Stop scanning after first non-comment, non-package line
                        if line and not line.startswith("//") and not line.startswith("/*") and not line.startswith("*") and not line.startswith("package"):
                            break
            except OSError:
                continue
    return user_packages

# Python stdlib top-level modules (subset covering the most common)
PYTHON_STDLIB = {
    "abc", "argparse", "ast", "asyncio", "base64", "bisect",
    "calendar", "cmath", "codecs", "collections", "colorsys",
    "concurrent", "configparser", "contextlib", "copy", "csv",
    "ctypes", "dataclasses", "datetime", "decimal", "difflib",
    "dis", "email", "enum", "errno", "fcntl", "filecmp",
    "fnmatch", "fractions", "functools", "gc", "getpass", "glob",
    "gzip", "hashlib", "heapq", "hmac", "html", "http",
    "importlib", "inspect", "io", "itertools", "json", "keyword",
    "linecache", "locale", "logging", "lzma", "math", "mimetypes",
    "multiprocessing", "numbers", "operator", "os", "pathlib",
    "pickle", "pkgutil", "platform", "pprint", "profile",
    "queue", "random", "re", "reprlib", "resource",
    "secrets", "select", "shelve", "shlex", "shutil", "signal",
    "site", "socket", "sqlite3", "ssl", "stat", "statistics",
    "string", "struct", "subprocess", "sys", "syslog", "tempfile",
    "textwrap", "threading", "time", "timeit", "token", "tokenize",
    "traceback", "types", "typing", "unicodedata", "unittest",
    "urllib", "uuid", "venv", "warnings", "weakref", "xml",
    "xmlrpc", "zipfile", "zipimport", "zlib",
    "__future__", "builtins", "_thread",
}


def load_ewi_mapping(language: str) -> list[dict]:
    """Load EWI code mapping for the given language from CSV.

    Looks in data/<language>/ewi_code_mapping.csv first (new layout),
    then falls back to the legacy data/ewi_code_mapping.csv if the
    language-specific file does not exist.
    """
    lang_dir = DATA_DIR / language / "ewi_code_mapping.csv"
    if lang_dir.exists():
        mapping_path = lang_dir
    else:
        mapping_path = DATA_DIR / "ewi_code_mapping.csv"

    entries = []
    with open(mapping_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["language"] == language:
                entries.append(row)
    return entries


# --- EWI status classification --------------------------------------------
#
# Post-detection, every rule-backed finding already carries a deterministic
# ``ewi_code`` + ``status_class`` (from kb_rules.json) embedded in the fixer's
# ``# SCOS: [CODE-STATUS]`` marker, so the reporter TRUSTS the marker's suffix
# when present. For a marker with no suffix (rule-less / LLM-only findings) the
# disposition is decided structurally: the before/after code-shape probe (see
# ``_build_code_change_probe``) yields ``Fixed`` when the code actually changed,
# otherwise the marker flavour supplies a default. The code itself falls back to
# ``resolve_ewi_code`` (ewi_code_mapping.csv category + keyword lookup).
#
# Reporting interpretation (see EWISummary.csv):
#   "files needing human input" == Status in {Error, IO}
#   "code conversion errors"    == Status == Error
#   "auto-fixed"                == Status == Fixed
VALID_STATUSES = ("Fixed", "IO", "Error", "Warning")


def _default_status(category: str) -> str:
    """Fallback disposition for a marker with no explicit status suffix, taken
    from the marker flavour: TODO -> Error (needs human), else Warning."""
    return "Error" if category == "Snowpark Connect TODO" else "Warning"


# --- Message-driven disposition (over-Error correction) ----------------------
#
# ``-Error`` / Category ``ConversionError`` must be reserved for code that FAILS
# AT RUNTIME on SCOS. The marker flavour alone (``_default_status``) over-tagged
# every ``TODO`` as Error, and the fixer sometimes stamps an inline ``-Error`` on
# a hedge-worded advisory or an unresolved I/O op. ``_message_signal`` reads the
# comment text and returns a disposition ONLY on a positive signal, in priority
# order IO > Warning(advisory) > Error(genuine); ``None`` means "no signal".
#   * IO      — external input/output that needs a stage/table/JDBC to run.
#   * Warning — a hedged / advisory / semantic-difference / perf note (executes).
#   * Error   — an ABSOLUTE failure (no equivalent / unsupported / raises).
# Advisory is checked before genuine so a hedged "…unsupported; validate…"
# (conditional, still runs) resolves to Warning, while an unhedged
# "PIVOT … is unsupported" resolves to Error.
# ``IO`` is for an external-storage PATH repoint — the operation itself is
# supported, only the location/mechanism must move to a Snowflake stage/table.
# IO covers DATA input/output plumbing that is not a code-logic failure:
#   * external cloud-storage paths (s3://, gs://, abfss://, dbfs:, hdfs://)
#   * unsupported file FORMATS whose read/write must repoint to a stage/table
#     (ORC, Avro, and the .text() writer)
#   * external DATA-SOURCE connectors that move rows in/out of another system
#     (JDBC, Redshift, Hive Warehouse, dbutils.fs file-system access)
# These are human-action I/O items, NOT ConversionErrors. In-code-logic failures
# (dbutils.secrets/jobs, unsupported SQL/DataFrame constructs) are NOT IO — they
# are caught by the genuine-failure branch below (-> Error).
_MSG_IO_RE = re.compile(
    r"s3[an]?://|\bs3\b|gs://|gcs://|abfss?://|wasbs?://|dbfs:/|hdfs://|"
    r"external .*(path|uri|location|bucket)|writing to external|"
    r"write(s|ing)?( csv| json| parquet| text)? to (an? )?(external|s3|gcs|azure|cloud|blob|local|volume)|"
    r"read(s|ing)? from (an? )?(external|s3|gcs|azure|cloud)|"
    r"to (a )?snowflake (internal )?(stage|table)|from (a )?snowflake (stage|table)|"
    r"internal stage|stage listing|list @stage|"
    # unsupported file formats -> repoint read/write to a stage/table
    r"\borc\b|\bavro\b|"
    # external data-source connectors -> use the Snowflake connector/stage
    r"\bjdbc\b|\bredshift\b|hivewarehousesession|com\.databricks\.spark|dbutils\.fs",
    re.IGNORECASE,
)
_MSG_ADVISORY_RE = re.compile(
    r"\bmay (fail|differ|not|need|be|have|require|recursively|hang)\b|\bmight\b|\bverify\b|\bvalidate\b|"
    r"behaves? differently|\bdiffers?\b|limited .*support|may not .*(support|translat)|"
    r"not fully supported|partial(/|ly| )|different (algorithm|behavior)|coercion (differs|may)|"
    r"\bdeprecated\b|semantics.{0,20}differ|nondeterministic|non-deterministic|performance tip|"
    r"\bslow\b|hotpath|advisory|caveat|\brecommend|"
    # perf / lifecycle advisories that execute fine (no runtime failure):
    r"\bcan (hang|time ?out|be slow|be expensive|degrade)\b|\btime ?out(s|ing)?\b|"
    r"\bconsider\b|serialization constraint|lifecycle differs|materialization strategy",
    re.IGNORECASE,
)
_MSG_GENUINE_RE = re.compile(
    r"no (scos )?equivalent|has no equivalent|cannot be auto-?rewritten|not implemented|"
    r"snowparkconnectnotimplemented|\bwill fail\b|\braises?\b|"
    r"\bunsupported\b|\bnot supported\b|not available in|"
    r"\brdd\b.{0,30}(unsupported|not supported|no equivalent)|streaming.{0,30}(unsupported|not supported|no engine)|"
    r"\bmllib\b|pyspark\.ml\b|sparkcontext.{0,30}(unsupported|not supported|not available)|"
    r"dbutils\.(fs|secrets|jobs|notebook)|"
    r"\bdeltatable\b|\bdelta\b.{0,30}(not supported|unsupported)|create table using delta|delta lake import|"
    r"is a databricks|databricks-specific|databricks-only|databricks-runtime",
    re.IGNORECASE,
)
# ``Handled`` — the unsupported construct was already NEUTRALIZED by a recipe so
# the emitted line RUNS on SCOS (stubbed to a placeholder, dropped/stripped as a
# no-op, commented out). The API is still "unsupported" — which is why the text
# also trips ``_MSG_GENUINE_RE`` — but the code as written no longer raises, so
# it is a manual follow-up (Warning), NOT a ConversionError. Checked BEFORE the
# genuine-failure branch so e.g. "dbutils.secrets has no SCOS equivalent; stubbed
# to None" resolves to Warning. Only PAST-TENSE, done-by-the-tool words are
# matched (never the imperative "rewrite/use/migrate", nor the couldn't-handle
# phrase "cannot be auto-rewritten", which must stay Error).
_MSG_HANDLED_RE = re.compile(
    r"\bstubbed\b|\bstub(bed)? to\b|\bplaceholder\b|commented[ -]out|\bno-?op\b|"
    r"\bdropped\b|\bstripped\b|assigned? [^.]*\bNone\b|replaced with a (placeholder|stub|no-?op)",
    re.IGNORECASE,
)


def _message_signal(message: str) -> str | None:
    """Return "IO"/"Warning"/"Error" from a positive signal in the comment text,
    or None when the text carries no disposition signal."""
    text = message or ""
    if _MSG_IO_RE.search(text):
        return "IO"
    if _MSG_ADVISORY_RE.search(text):
        return "Warning"
    # A construct the recipe already stubbed/dropped/no-op'd RUNS — manual
    # follow-up, not a runtime failure — even though its note names an
    # "unsupported" API. Must precede the genuine-failure branch below.
    if _MSG_HANDLED_RE.search(text):
        return "Warning"
    if _MSG_GENUINE_RE.search(text):
        return "Error"
    return None


def _status_from_message(message: str) -> str:
    """Suffix-less disposition: a positive message signal, else Warning.

    Replaces the naive ``_default_status`` TODO->Error default: a bare advisory
    TODO with no runtime-failure signal is a Warning, not a ConversionError.
    """
    return _message_signal(message) or "Warning"


def status_to_category(status: str) -> str:
    """Map a resolved EWI Status to its SMA Category coherently: ConversionError
    is reserved strictly for Status == "Error"; everything else is Warning."""
    return "ConversionError" if status == "Error" else "Warning"


def resolve_ewi_code(
    mapping: list[dict],
    category: str,
    root_cause: str | None,
    element: str = "",
) -> dict:
    """
    Resolve the best EWI code for an issue based on category and root_cause keywords.

    Returns dict with: ewi_code, sma_category, description, doc_url
    """
    root_cause_lower = (root_cause or "").lower()

    # First pass: try keyword-based matching for specific codes
    for entry in mapping:
        kw = entry.get("keyword_pattern", "")
        if not kw:
            continue
        keywords = [k.strip().lower() for k in kw.split("|")]
        if any(k in root_cause_lower for k in keywords):
            desc = entry["description_template"].replace("{element}", element)
            return {
                "ewi_code": entry["ewi_code"],
                "sma_category": entry["sma_category"],
                "description": desc,
                "doc_url": entry["doc_url"],
            }

    # Second pass: match by snowpark_connect_category
    category_normalized = category.strip()
    for entry in mapping:
        if entry["snowpark_connect_category"] == category_normalized and not entry.get("keyword_pattern"):
            desc = entry["description_template"].replace("{element}", element)
            return {
                "ewi_code": entry["ewi_code"],
                "sma_category": entry["sma_category"],
                "description": desc,
                "doc_url": entry["doc_url"],
            }

    # Fallback to Generic
    for entry in mapping:
        if entry["snowpark_connect_category"] == "Generic":
            desc = entry["description_template"].replace("{element}", element)
            return {
                "ewi_code": entry["ewi_code"],
                "sma_category": entry["sma_category"],
                "description": desc,
                "doc_url": entry["doc_url"],
            }

    # Absolute fallback — use language-appropriate code prefix
    fallback_prefix = "SPRKCNTSCL" if any(
        e.get("ewi_code", "").startswith("SPRKCNTSCL") for e in mapping
    ) else "SPRKCNTPY"
    return {
        "ewi_code": f"{fallback_prefix}1000",
        "sma_category": "ConversionError",
        "description": f"The element '{element}' is not supported for Snowpark Connect",
        "doc_url": "",
    }


# SNOW-3347464: Deterministic header insertion for Scala (and Python) output files
def ensure_migration_headers(migrated_dir: str, language: str) -> int:
    """Insert migration header into every output file that is missing one.

    For Scala/Java files: prepends ``// SCOS Migration Output`` as line 1.
    For Python files: prepends a docstring header if absent.

    Returns the number of files that were patched (header was missing).
    """
    ext = _source_ext(language)
    # Match any existing migration-header docstring, not just our exact
    # "SCOS Migration Output" stub. The LLM migration agent sometimes writes
    # its own header worded "SCOS Migration: <file>"; checking the broader
    # "SCOS Migration" marker keeps us from stacking a second docstring on top
    # of it (which produced a corrupted double header).
    header_marker = "SCOS Migration"
    patched = 0

    for root, _dirs, files in _walk_filtered(migrated_dir):
        for fname in files:
            if not fname.endswith(ext):
                continue
            fpath = os.path.join(root, fname)
            # Notebooks (Databricks exported-text / native-JSON / .ipynb) must NOT
            # get a raw header prepended: for exported-text it destroys the
            # ``// Databricks notebook source`` / ``# Databricks notebook source``
            # first-line marker (breaking notebook detection + re-parsing), and for
            # JSON formats it corrupts the document. Their migration header is added
            # structurally as a cell by Phase 3 (update_imports._transform_notebook),
            # so skip them here.
            try:
                if is_notebook(fpath):
                    continue
            except OSError:
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    # SNOW-3347464: Check first 5 lines for existing header
                    head_lines = [f.readline() for _ in range(5)]
                if any(header_marker in ln for ln in head_lines):
                    continue

                # Re-read full content and prepend header
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                if language in ("scala", "java"):
                    # ``// SCOS Migration Output`` is valid in both languages.
                    # Java's rich header is authored by update_imports_java.py,
                    # which keys off the same "SCOS Migration Output" marker.
                    new_content = SCALA_MIGRATION_HEADER + "\n" + content
                else:
                    # Placeholder only — Phase 3 (update_imports.py) is the real
                    # header author. The STUB_HEADER_SENTINEL line makes this
                    # detectable: scos_gates.py imports FAILS on it and
                    # update_imports.py REPLACES it with a rich header.
                    new_content = (
                        '"""\nSCOS Migration Output\n'
                        "=====================\n"
                        f"Source File: {fname}\n"
                        f"Migrated on: {datetime.now().strftime('%Y-%m-%d')}\n"
                        "\nChanges Overview:\n"
                        f"- {STUB_HEADER_SENTINEL}.\n"
                        "\nKnown Limitations:\n"
                        "- None\n"
                        '"""\n'
                    ) + content

                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                patched += 1
            except OSError:
                continue

    return patched


# SNOW-3347464: EWI deduplication helpers
def normalize_pattern(description: str) -> str:
    """Normalize an EWI description for dedup grouping.

    Strips whitespace, lowercases, and removes variable-length numeric
    literals and quoted identifiers so structurally identical patterns
    collapse into the same key.
    """
    s = description.strip().lower()
    # Replace quoted identifiers: "Foo", 'Bar', `Baz`
    s = re.sub(r'["\'][^"\']*["\']', '""', s)
    s = re.sub(r"`[^`]*`", '""', s)
    # Collapse runs of digits
    s = re.sub(r"\d+", "N", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s)
    return s


def deduplicate_issues(rows: list[dict]) -> list[dict]:
    """Deduplicate Issues.csv rows by (Code, normalized Description).

    Groups identical EWI patterns and aggregates:
      - file_count: number of distinct files affected
      - affected_files: semicolon-separated list of FileId values

    Keeps the first occurrence's details for Description, Category, etc.
    """
    from collections import OrderedDict

    groups: OrderedDict[tuple[str, str], dict] = OrderedDict()

    for row in rows:
        key = (row["Code"], normalize_pattern(row["Description"]))
        if key not in groups:
            groups[key] = {
                **row,
                "_files": {row["FileId"]} if row["FileId"] else set(),
            }
        else:
            if row["FileId"]:
                groups[key]["_files"].add(row["FileId"])

    deduped = []
    for group in groups.values():
        files = sorted(group.pop("_files"))
        group["FileCount"] = str(len(files))
        group["AffectedFiles"] = ";".join(files)
        deduped.append(group)

    return deduped


def _comment_prefix(language: str) -> str:
    if language == "sql":
        return "--"
    return _lang_conv(language)["comment"]


def _scos_category(body: str) -> str:
    """Classify a SCOS comment body by its leading keyword."""
    if body.startswith("TODO -") or body.startswith("TODO:"):
        return "Snowpark Connect TODO"
    if body.startswith("Performance tip -") or body.startswith("Performance tip:"):
        return "Snowpark Connect Performance"
    return "Snowpark Connect Fix"


# The fixer embeds the EWI code inline, e.g. ``# SCOS: [SPRKCNTPY0060] ...``.
# The agent sometimes places it after a "TODO - " / "Performance tip - "
# prefix, so match the bracketed code ANYWHERE in the line (not just at the
# start) to avoid double-stamping. The optional ``-<STATUS>`` suffix (Fixed / IO /
# Error / Warning) the deterministic annotator appends is captured separately so
# the base code stays usable for ewi_code_mapping lookups.
_SCOS_INLINE_CODE_RE = re.compile(r"\[(SPRKCNT[A-Z]*\d+)(?:-(Fixed|IO|Error|Warning))?\]")


def find_scos_blocks(lines: list[str], language: str) -> list[dict]:
    """Locate every ``# SCOS:`` (or ``// SCOS:``) comment block in ``lines``.

    Also matches the ``SCOS-WARN:`` and ``SCOS-TODO:`` markers emitted by the
    Phase 0.5 annotate-only recipes so they reach Issues.csv too; the flavour
    drives the block ``category`` ("Snowpark Connect Warning" / "Snowpark
    Connect TODO").

    A block is a ``SCOS`` marker line plus the run of comment lines that
    immediately follow it (same comment prefix, until a blank line, a
    non-comment line, a new ``SCOS`` marker, or an ``EWI:`` marker). The
    block body is flattened to a single whitespace-normalised string so
    multi-line explanations are not truncated.

    If the first line carries an inline ``[SPRKCNT...]`` EWI code (the fixer's
    convention), it is split out into ``code`` and removed from
    ``description`` so the code is not duplicated across CSV columns.

    Returns a list of dicts: ``start_idx`` (0-based line of the ``SCOS:``
    marker), ``description`` (flattened, code stripped), ``code`` (str|None),
    ``category``, ``indent``.
    """
    prefix = _comment_prefix(language)
    # Match the plain ``SCOS:`` marker the fixer writes plus the ``SCOS-WARN:``
    # / ``SCOS-TODO:`` markers the Phase 0.5 annotate-only recipes emit, so all
    # three flavours reach Issues.csv. The ``(-WARN|-TODO)?`` suffix is captured
    # so we can categorise the block by flavour.
    start_re = re.compile(rf"^(\s*){re.escape(prefix)}\s*SCOS(-WARN|-TODO)?:\s*(.*)")
    cont_re = re.compile(rf"^\s*{re.escape(prefix)}(.*)$")
    scos_re = re.compile(rf"^\s*{re.escape(prefix)}\s*SCOS(-WARN|-TODO)?:")
    ewi_re = re.compile(rf"^\s*{re.escape(prefix)}EWI:")

    blocks: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip("\r\n")
        m = start_re.match(line)
        if not m:
            i += 1
            continue
        indent = m.group(1)
        variant = m.group(2)  # "-WARN", "-TODO", or None
        body0 = m.group(3).strip()
        code_match = _SCOS_INLINE_CODE_RE.search(body0)
        if code_match:
            code = code_match.group(1)
            status = code_match.group(2)  # F / IO / Error / Warning, or None
            first_text = (body0[:code_match.start()] + body0[code_match.end():])
            first_text = " ".join(first_text.split())
        else:
            code = None
            status = None
            first_text = body0
        if variant == "-WARN":
            category = "Snowpark Connect Warning"
        elif variant == "-TODO":
            category = "Snowpark Connect TODO"
        else:
            category = _scos_category(first_text)
        parts = [first_text] if first_text else []
        j = i + 1
        while j < n:
            ln = lines[j].rstrip("\r\n")
            if not ln.strip():
                break
            if scos_re.match(ln) or ewi_re.match(ln):
                break
            cm = cont_re.match(ln)
            if not cm:
                break
            parts.append(cm.group(1).strip())
            j += 1
        description = " ".join(" ".join(parts).split())
        blocks.append({
            "start_idx": i,
            "description": description,
            "code": code,
            "status": status,
            "category": category,
            "indent": indent,
        })
        i = max(j, i + 1)
    return blocks


def _build_code_change_probe(lines: list[str], original_src: str | None, language: str):
    """Return ``probe(marker_line_idx) -> bool`` telling whether the code the
    marker annotates was actually changed relative to ``original_src``.

    The comparison is over CODE-ONLY lines (blanks and comment lines excluded),
    so the ``# SCOS:`` comments the migration injected never register as
    "changes" themselves. A marker is treated as sitting on changed code when:

      * the statement at/after the marker was inserted or rewritten
        (``insert`` / ``replace`` diff opcode on the migrated side), or
      * an original statement was deleted at that position
        (``delete`` / ``replace`` opcode) — this is how removals like a
        stripped ``spark.conf.set(...)`` line are recognised as a fix.

    When ``original_src`` is absent (e.g. notebooks, or the source file could
    not be paired), the probe returns ``False`` for every marker — the caller
    then falls back to its prior status handling with no behavior change.
    """
    if not original_src:
        return lambda _idx: False

    prefix = _comment_prefix(language)

    def _is_comment(raw: str) -> bool:
        return raw.lstrip().startswith(prefix)

    # Migrated code lines paired with their index into ``lines``.
    migr_pairs: list[tuple[int, str]] = []
    for li, ln in enumerate(lines):
        st = ln.rstrip("\r\n").strip()
        if not st or _is_comment(ln):
            continue
        migr_pairs.append((li, " ".join(st.split())))
    migr_code = [c for _, c in migr_pairs]

    orig_code = []
    for ln in original_src.splitlines():
        st = ln.strip()
        if not st or _is_comment(ln):
            continue
        orig_code.append(" ".join(st.split()))

    sm = difflib.SequenceMatcher(None, orig_code, migr_code, autojunk=False)
    changed_b: set[int] = set()      # migrated positions inserted/replaced
    delete_gaps: set[int] = set()    # migrated positions where original code was removed
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert"):
            changed_b.update(range(j1, j2))
        if tag in ("replace", "delete"):
            delete_gaps.add(j1)

    def probe(marker_idx: int) -> bool:
        # The statement this marker annotates is the first code line after it.
        for j, (li, _code) in enumerate(migr_pairs):
            if li > marker_idx:
                return (j in changed_b) or (j in delete_gaps)
        # Marker sits after the last code line — only changed if trailing code
        # was deleted there.
        return len(migr_code) in delete_gaps

    return probe


def _annotate_lines(
    lines: list[str],
    language: str,
    mapping: list[dict],
    original_src: str | None = None,
) -> tuple[list[str], int]:
    """Ensure each ``# SCOS:`` comment carries its EWI code inline.

    Two passes, both idempotent and content-anchored (we only touch the
    comments the fixer wrote, never a remapped line number):

    1. Drop any legacy standalone ``#EWI:`` line that sits directly above a
       ``# SCOS:`` line — that separate line duplicated the message.
    2. Normalize each ``# SCOS:`` first line to ``# SCOS: [<code>-<status>] <message>``.
       When the marker already carries ``[<code>-<status>]`` (the deterministic
       path — kb_rules -> analysis.json -> fixer), it is preserved. Otherwise the
       base code falls back to ``resolve_ewi_code`` (mapping.csv) and the status
       to the before/after code-shape probe (``Fixed`` if changed) or the marker
       flavour default. Re-running is idempotent (no ``-Fixed-Fixed`` stacking).

       ``# SCOS-TODO:`` / ``# SCOS-WARN:`` markers are handled too, but only when
       they already carry a bracketed code without a status (e.g. the fixer's
       ``# SCOS-TODO: [SPRKCNTPY1000] ...``): they gain the flavour disposition
       (TODO -> Error, WARN -> Warning) and are never labelled ``Fixed`` — a
       manual follow-up / advisory is not a completed fix, even when an adjacent
       code change would otherwise trip the code-shape upgrade. Code-less recipe
       markers (recipe-id audit trail only) are left verbatim.

    Returns ``(new_lines, change_count)``.
    """
    prefix = _comment_prefix(language)
    ewi_re = re.compile(rf"^\s*{re.escape(prefix)}EWI:")
    scos_re = re.compile(rf"^\s*{re.escape(prefix)}\s*SCOS:")
    scos_split_re = re.compile(
        rf"^(\s*{re.escape(prefix)}\s*SCOS(-TODO|-WARN)?:\s*)(.*)$"
    )
    changed = 0

    # Pass 1: strip legacy "#EWI: ..." lines that precede a "# SCOS:" line.
    cleaned: list[str] = []
    n = len(lines)
    k = 0
    while k < n:
        cur = lines[k]
        nxt = lines[k + 1] if k + 1 < n else ""
        if ewi_re.match(cur.rstrip("\r\n")) and scos_re.match(nxt.rstrip("\r\n")):
            changed += 1
            k += 1
            continue
        cleaned.append(cur)
        k += 1
    lines = cleaned

    # Before/after code-shape map: compare the migrated file's CODE lines
    # (comments + blanks excluded, so the injected ``# SCOS:`` comments do not
    # skew the alignment) against the original source. A marker whose adjacent
    # statement was rewritten/inserted, or where an original statement was
    # deleted, is evidence the fixer/recipe actually changed code there -> the
    # disposition is ``Fixed``, independent of whatever suffix the LLM
    # wrote. This makes ``-Fixed`` deterministic (a removed/rewritten line can no
    # longer be mislabeled ``-Error``). Only an *upgrade* to F is applied here;
    # annotation-only markers keep their rule/analysis status untouched.
    changed_marker = _build_code_change_probe(lines, original_src, language)
    have_source = original_src is not None

    # Pass 2: normalize the SCOS first line so it carries exactly one inline
    # ``[<code>-<status>]`` token, right after the marker.
    for block in sorted(find_scos_blocks(lines, language), key=lambda b: b["start_idx"], reverse=True):
        idx = block["start_idx"]
        raw = lines[idx]
        if raw.endswith("\r\n"):
            nl, core = "\r\n", raw[:-2]
        elif raw.endswith("\n"):
            nl, core = "\n", raw[:-1]
        else:
            nl, core = "", raw
        m = scos_split_re.match(core)
        if not m:
            continue
        head, kind, rest = m.group(1), m.group(2), m.group(3)
        # Pull every bracketed code out of the message; the first base code wins.
        found = [mm.group(1) for mm in _SCOS_INLINE_CODE_RE.finditer(rest)]
        found_status = None
        for mm in _SCOS_INLINE_CODE_RE.finditer(rest):
            if mm.group(2):
                found_status = mm.group(2)
                break
        # ``# SCOS-TODO:`` / ``# SCOS-WARN:`` recipe markers that carry only a
        # recipe-id audit trail (no ``[SPRKCNT...]`` code) are left verbatim —
        # rewriting them would destroy that trail (see
        # ``test_annotate_lines_leaves_recipe_warn_todo_untouched``). But when
        # such a marker *does* carry a bracketed code with no status suffix
        # (e.g. the fixer's ``# SCOS-TODO: [SPRKCNTPY1000] dbutils.secrets...``),
        # fall through so the deterministic status is stamped — otherwise the
        # code is left with no disposition (``-Error`` / ``-Warning`` missing).
        if kind and not found:
            continue
        rest_wo = _SCOS_INLINE_CODE_RE.sub("", rest)
        rest_wo = " ".join(rest_wo.split())
        if found:
            base = found[0]
        else:
            base = resolve_ewi_code(mapping, block["category"], block["description"], "")["ewi_code"]
        # When the inline marker already carries a status suffix (deterministic
        # path: rule/recipe stamped it), preserve it as-is.
        if found_status:
            status = found_status
            # Re-evaluate a fixer-stamped ``-Error``: downgrade to IO/Warning ONLY
            # on a positive advisory/I/O signal in the text; an absolute failure
            # (no equivalent / unsupported / raises) keeps Error.
            if status == "Error":
                sig = _message_signal(block["description"] or rest_wo)
                if sig in ("IO", "Warning"):
                    status = sig
        else:
            # No suffix (rule-less / LLM-only marker): disposition from the
            # message text (positive signal), else Warning — a bare advisory TODO
            # is not a runtime failure. The code-shape probe below may upgrade to
            # Fixed.
            status = _status_from_message(block["description"] or rest_wo)
        # Deterministic status from before/after code shape. When the original
        # source is available for this file (`have_source`), the disposition is
        # decided by whether the fixer/recipe actually changed code at the marker:
        #   * code rewritten/removed  -> ``Fixed``, overriding a stale suffix
        #   * code UNCHANGED but marked ``Fixed`` -> downgrade: an annotation-only
        #     "verify"/advisory note is not a fix, so use the flavour default.
        # ``IO`` is left intact (still needs a human-supplied stage). Without a
        # source pairing, the fixer's suffix is preserved unchanged.
        #
        # Plain ``# SCOS:`` markers only. A ``-TODO`` / ``-WARN`` marker's
        # disposition is intrinsic to its flavour — a stub or annotation that
        # happens to change adjacent code is still a manual follow-up / advisory,
        # not a completed fix — so it is exempt from the "changed code -> Fixed"
        # upgrade (else a stubbed ``dbutils.secrets`` TODO would be mislabeled
        # ``Fixed`` once a source pairing is available).
        if have_source and kind is None:
            if changed_marker(idx):
                if status in ("Error", "Warning"):
                    status = "Fixed"
            elif status == "Fixed" and found_status != "Fixed":
                status = _status_from_message(block["description"] or rest_wo)
        # A ``-TODO`` / ``-WARN`` marker is a manual follow-up / advisory by
        # definition — never a completed fix. Coerce a stray ``Fixed`` (from an
        # explicit inline suffix) back to the message disposition.
        if kind and status == "Fixed":
            status = _status_from_message(block["description"] or rest_wo)
        code = f"{base}-{status}"
        new_core = f"{head}[{code}] {rest_wo}" if rest_wo else f"{head}[{code}]"
        if new_core != core:
            lines[idx] = new_core + nl
            changed += 1

    # Pass 3: deduplicate adjacent identical SCOS marker lines.
    # Multiple producers (recipes, fixer, re-runs) can stack identical comments
    # above the same statement. Collapse consecutive identical SCOS lines.
    deduped: list[str] = []
    for line in lines:
        stripped = line.rstrip("\r\n").lstrip()
        if stripped.startswith(f"{prefix} SCOS") or stripped.startswith(f"{prefix}SCOS"):
            # Check if the previous line (normalized) is identical
            if deduped:
                prev_stripped = deduped[-1].rstrip("\r\n").lstrip()
                if prev_stripped == stripped:
                    changed += 1
                    continue
        deduped.append(line)
    lines = deduped

    return lines, changed


def annotate_scos_markers(
    migrated_dir: str,
    language: str,
    mapping: list[dict],
    source_dir: str = "",
) -> int:
    """Ensure every ``# SCOS:`` comment in the migrated tree carries its EWI
    code inline (``# SCOS: [SPRKCNT...-<status>] <message>``).


    This replaces the old ``scos_to_ewi_bridge`` line-number injection. Rather
    than emitting a second ``#EWI:`` line (which drifted into the header,
    duplicated the message, and stamped a generic code over the fixer's real
    one), the code lives in the single ``# SCOS:`` comment the fixer wrote.
    Legacy ``#EWI:`` lines sitting directly above a ``# SCOS:`` comment are
    removed on the way through.

    Returns the number of lines changed across all files.
    """
    flat_ext = _source_ext(language)
    target_cell_lang = _lang_conv(language)["cell_language"]
    total = 0

    for root, _dirs, files in _walk_filtered(migrated_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            info = detect_format(fpath)
            if info.get("format") != "not_notebook":
                try:
                    nb = parse_notebook(fpath, info=info)
                except (ValueError, OSError):
                    continue
                # Pair migrated code cells with the original notebook's code
                # cells (by position) so the before/after code-shape check can
                # run per cell — this is what lets the deterministic -Fixed logic
                # apply to notebook workloads, not just flat .py files. Only
                # pair when the code-cell counts match exactly; a mismatch
                # (e.g. a recipe inserted a bootstrap cell) means positional
                # pairing would be wrong, so we skip and fall back to the
                # fixer's suffix rather than risk mis-labeling.
                orig_code_cells = None
                if source_dir:
                    rel = os.path.relpath(fpath, migrated_dir)
                    src_path = os.path.join(source_dir, rel)
                    if os.path.isfile(src_path):
                        try:
                            src_info = detect_format(src_path)
                            if src_info.get("format") != "not_notebook":
                                onb = parse_notebook(src_path, info=src_info)
                                ocells = [c.source for c in onb.cells
                                          if c.cell_type == "code" and c.cell_language == target_cell_lang]
                                mcount = sum(1 for c in nb.cells
                                             if c.cell_type == "code" and c.cell_language == target_cell_lang)
                                if len(ocells) == mcount:
                                    orig_code_cells = ocells
                        except (ValueError, OSError):
                            orig_code_cells = None
                changed = 0
                code_cell_idx = 0
                for cell in nb.cells:
                    if cell.cell_type != "code" or cell.cell_language != target_cell_lang:
                        continue
                    cell_original_src = None
                    if orig_code_cells is not None and code_cell_idx < len(orig_code_cells):
                        cell_original_src = orig_code_cells[code_cell_idx]
                    code_cell_idx += 1
                    if cell.source.endswith("\r\n"):
                        line_sep, trailing = "\r\n", "\r\n"
                    elif cell.source.endswith("\n"):
                        line_sep, trailing = "\n", "\n"
                    elif "\r\n" in cell.source:
                        line_sep, trailing = "\r\n", ""
                    else:
                        line_sep, trailing = "\n", ""
                    cell_lines = cell.source.splitlines()
                    new_lines, inserted = _annotate_lines(
                        cell_lines, language, mapping,
                        original_src=cell_original_src,
                    )
                    if inserted:
                        cell.source = line_sep.join(new_lines) + trailing
                        changed += inserted
                if changed:
                    try:
                        write_notebook(fpath, nb)
                        total += changed
                    except OSError:
                        continue
                continue

            if fname.endswith(flat_ext):
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                except OSError:
                    continue
                # Pair the migrated file with its original source (same relative
                # path under source_dir) so the annotator can derive a
                # deterministic ``-Fixed`` from the before/after code shape. Missing
                # pairs fall back to the prior status handling.
                original_src = None
                if source_dir:
                    rel = os.path.relpath(fpath, migrated_dir)
                    src_path = os.path.join(source_dir, rel)
                    if os.path.isfile(src_path):
                        try:
                            with open(src_path, "r", encoding="utf-8", errors="ignore") as sf:
                                original_src = sf.read()
                        except OSError:
                            original_src = None
                lines, inserted = _annotate_lines(
                    lines, language, mapping,
                    original_src=original_src,
                )
                if inserted:
                    try:
                        with open(fpath, "w", encoding="utf-8") as f:
                            f.writelines(lines)
                        total += inserted
                    except OSError:
                        continue

    return total


def scan_scos_comments(migrated_dir: str, language: str) -> list[dict]:
    """Scan migrated files for SCOS comments and return issue dicts.

    Covers both flat source files (``.py`` / ``.scala``) and every notebook
    format supported by ``notebook_io``. For notebook cells, the returned
    ``line`` field is rendered as ``cell:<index>:<line>`` so downstream
    reports tag the issue with the owning cell.

    Skips common build/VCS/cache directories (``.git``, ``__pycache__``,
    ``.venv``, ``node_modules``, etc.) so binary artifacts never reach the
    notebook detector or the flat-file reader.
    """
    issues: list[dict] = []
    flat_ext = _source_ext(language)
    target_cell_lang = _lang_conv(language)["cell_language"]

    for root, _dirs, files in _walk_filtered(migrated_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, migrated_dir)

            # Detect notebook format FIRST. A Databricks exported-text
            # notebook carries a ``.py`` / ``.scala`` extension, so an
            # extension-first check would route it into the flat-scan
            # branch and emit a plain integer line number instead of
            # tagging the issue with its owning cell. Pay the detection
            # cost up front so exported-text notebooks flow through the
            # cell-scan branch.
            info = detect_format(fpath)
            if info.get("format") != "not_notebook":
                # Notebook case: parse cells and scan each cell's source.
                # Reuse the already-computed ``info`` so parse_notebook
                # doesn't re-read the notebook's head to re-detect.
                try:
                    nb = parse_notebook(fpath, info=info)
                except (ValueError, OSError):
                    continue
                for cell in nb.cells:
                    if cell.cell_type != "code":
                        continue
                    if cell.cell_language != target_cell_lang:
                        continue
                    for block in find_scos_blocks(cell.source.splitlines(), language):
                        issues.append({
                            "snowpark_connect_category": block["category"],
                            "description": block["description"],
                            "code": block["code"],
                            "status": block["status"],
                            "line": block["start_idx"] + 1,
                            "file": rel_path,
                            "cell_id": cell.index,
                        })
                continue

            # Plain-source fallback: only files with the target flat
            # extension are scanned line-by-line. Every other extension
            # is skipped so we don't open binary/unrelated artifacts.
            if fname.endswith(flat_ext):
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        flines = f.readlines()
                except OSError:
                    continue
                for block in find_scos_blocks(flines, language):
                    issues.append({
                        "snowpark_connect_category": block["category"],
                        "description": block["description"],
                        "code": block["code"],
                        "status": block["status"],
                        "line": block["start_idx"] + 1,
                        "file": rel_path,
                    })

            # Standalone .sql files carry `-- SCOS:` markers written by the
            # Phase-0.6 SQL rewriter / fixer. They are not in the manifest and
            # use the SQL comment prefix, so scan them with language="sql"
            # regardless of the workload language so SQL issues reach Issues.csv.
            elif fname.endswith(".sql"):
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        flines = f.readlines()
                except OSError:
                    continue
                for block in find_scos_blocks(flines, "sql"):
                    issues.append({
                        "snowpark_connect_category": block["category"],
                        "description": block["description"],
                        "code": block["code"],
                        "status": block["status"],
                        "line": block["start_idx"] + 1,
                        "file": rel_path,
                    })

    return issues


def generate_issues_csv(
    analysis_path: str,
    migrated_dir: str,
    output_dir: str,
    language: str,
    mapping: list[dict],
    source_dir: str = "",
) -> int:
    """Generate Reports/Issues.csv. Returns count of rows written.

    Each row's ``Code`` is the suffixed taxonomy code (``SPRKCNTPY####-<STATUS>``)
    so the disposition is visible at a glance, while ``Status`` is broken out as
    its own column for easy aggregation. The base code (suffix stripped) is used
    for the ewi_code_mapping metadata lookup.
    """
    reports_dir = os.path.join(output_dir, "Reports")
    os.makedirs(reports_dir, exist_ok=True)
    csv_path = os.path.join(reports_dir, "Issues.csv")

    rows = []

    # Issues.csv is generated *solely* from the ``# SCOS:`` comments the
    # migrate fixer wrote into the Output files.
    #
    # ``analysis.json`` is the analyzer's hypotheses and the fixer's *input*,
    # not a record of what remains after fixing. For each finding the fixer
    # may:
    #   (a) fix the code in place        -> issue resolved, no marker wanted
    #   (b) write a ``# SCOS:`` comment   -> captured by the scan below
    #   (c) ignore a spurious finding     -> nothing worth surfacing
    # Reading analysis.json here re-surfaced case (c): spurious / ignored
    # findings whose code snippet was unchanged slipped past the
    # "still-present" guard and were stamped onto unrelated lines as stale,
    # mislocated #EWI markers. Only the comments the fixer actually emitted
    # represent real, unresolved issues — and they already sit on the correct
    # line — so the # SCOS: scan is now the single source of truth.
    if migrated_dir and os.path.isdir(migrated_dir):
        by_code = {e["ewi_code"]: e for e in mapping}
        comments = scan_scos_comments(migrated_dir, language)
        for c in comments:
            resolved = resolve_ewi_code(mapping, c["snowpark_connect_category"], c["description"], "")
            # Prefer the code the fixer embedded inline (e.g. SPRKCNTPY0060);
            # fall back to the generic resolver only when the comment had none.
            base = c.get("code") or resolved["ewi_code"]
            # When the inline marker already carries a status suffix
            # (deterministic path: rule/recipe stamped [CODE-STATUS]),
            # use it directly — no text-based refinement or derivation.
            if c.get("status"):
                status = c["status"]
                # Re-evaluate a fixer-stamped -Error against the message text
                # (same guard as annotate_scos_markers, for direct callers).
                if status == "Error":
                    sig = _message_signal(c["description"])
                    if sig in ("IO", "Warning"):
                        status = sig
            else:
                # No suffix: disposition from the message text, else Warning.
                status = _status_from_message(c["description"])
            code = f"{base}-{status}"
            entry = by_code.get(base)
            base_category = entry["sma_category"] if entry else resolved["sma_category"]
            # Keep Category coherent with the resolved Status, both directions, so
            # ConversionError is reserved strictly for genuine runtime failures:
            #   Status == Error            -> ConversionError (upgrades a code whose
            #                                 catalogued default was Warning).
            #   Status in {IO, Warning, Fixed} -> Warning, EXCEPT preserve the
            #                                 advisory ``Information`` tier.
            # Enforces  Category == "ConversionError"  <=>  Status == "Error".
            if status == "Error":
                sma_category = "ConversionError"
            elif base_category == "Information":
                sma_category = "Information"
            else:
                sma_category = status_to_category(status)
            doc_url = entry["doc_url"] if entry else resolved["doc_url"]
            if c.get("cell_id") is not None:
                line_str = f"cell:{c['cell_id']}:{c['line']}"
            else:
                line_str = str(c["line"])
            rows.append({
                "Code": code,
                "Description": c["description"],
                "Category": sma_category,
                "FileId": c["file"].replace("\\", "/").lstrip("/"),
                "Line": line_str,
                "Column": "",
                "Url": doc_url,
                "Status": status,
            })

    # SNOW-3347464: Deduplicate by (EWI code, normalized description pattern)
    deduped_rows = deduplicate_issues(rows)

    # ``Status`` is placed after the original columns so existing consumers that
    # index FileId at position 3 keep working; the suffixed ``Code`` already
    # carries the disposition inline.
    fieldnames = ["Code", "Description", "Category", "FileId", "Line", "Column", "Url",
                  "Status", "FileCount", "AffectedFiles"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(deduped_rows)

    print(f"  Issues.csv: {len(deduped_rows)} rows written ({len(rows)} before dedup) to {csv_path}")

    # Roll the issues up into an at-a-glance summary so consumers can answer
    # "how many files need human input", "how many conversion errors", etc.
    generate_ewi_summary_csv(deduped_rows, reports_dir)
    return len(deduped_rows)


def generate_ewi_summary_csv(rows: list[dict], reports_dir: str) -> None:
    """Write Reports/EWISummary.csv aggregating the deduplicated issues.

    Sections (Metric,Key,IssueCount,FileCount):
      * ``total``        — one row, overall totals
      * ``by_status``    — one row per status (Fixed / IO / Error / Warning)
      * ``by_code``      — one row per suffixed EWI code
      * ``needs_human``  — one row, Status in {Error, IO}
    where IssueCount is the number of (deduplicated) issue patterns and
    FileCount is the number of DISTINCT files those patterns touch.
    """
    from collections import OrderedDict

    def _files(row: dict) -> set[str]:
        affected = row.get("AffectedFiles") or ""
        if affected:
            return {f for f in affected.split(";") if f}
        fid = row.get("FileId") or ""
        return {fid} if fid else set()

    by_status: "OrderedDict[str, dict]" = OrderedDict(
        (s, {"issues": 0, "files": set()}) for s in VALID_STATUSES
    )
    by_code: "OrderedDict[str, dict]" = OrderedDict()
    all_files: set[str] = set()
    human_files: set[str] = set()
    human_issues = 0

    for r in rows:
        status = r.get("Status") or "Warning"
        code = r.get("Code") or ""
        files = _files(r)
        all_files |= files
        slot = by_status.setdefault(status, {"issues": 0, "files": set()})
        slot["issues"] += 1
        slot["files"] |= files
        cslot = by_code.setdefault(code, {"issues": 0, "files": set()})
        cslot["issues"] += 1
        cslot["files"] |= files
        if status in ("Error", "IO"):
            human_issues += 1
            human_files |= files

    out_rows = []
    out_rows.append({"Metric": "total", "Key": "all", "IssueCount": len(rows),
                     "FileCount": len(all_files)})
    for status, slot in by_status.items():
        out_rows.append({"Metric": "by_status", "Key": status,
                         "IssueCount": slot["issues"], "FileCount": len(slot["files"])})
    out_rows.append({"Metric": "needs_human", "Key": "Error+IO",
                     "IssueCount": human_issues, "FileCount": len(human_files)})
    for code, slot in sorted(by_code.items()):
        out_rows.append({"Metric": "by_code", "Key": code,
                         "IssueCount": slot["issues"], "FileCount": len(slot["files"])})

    csv_path = os.path.join(reports_dir, "EWISummary.csv")
    fieldnames = ["Metric", "Key", "IssueCount", "FileCount"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"  EWISummary.csv: {len(out_rows)} rows written to {csv_path}")


def generate_input_files_inventory(
    source_dir: str,
    output_dir: str,
    project_name: str,
    execution_id: str,
) -> int:
    """Generate Reports/InputFilesInventory.csv. Returns count of rows."""
    reports_dir = os.path.join(output_dir, "Reports")
    os.makedirs(reports_dir, exist_ok=True)
    csv_path = os.path.join(reports_dir, "InputFilesInventory.csv")

    rows = []
    for root, _dirs, files in _walk_filtered(source_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, source_dir).replace("\\", "/")
            ext = os.path.splitext(fname)[1].lower()
            tech = _classify_technology(fpath, ext)
            is_unit = _is_conversion_unit(fname, ext, tech)

            try:
                stat = os.stat(fpath)
                byte_size = stat.st_size
            except OSError:
                byte_size = 0

            loc = 0
            char_len = 0
            parse_result = "Parsed"
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    char_len = len(content)
                    loc = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            except OSError:
                parse_result = "Error"

            rows.append({
                "Element": fname,
                "ProjectId": project_name,
                "FileId": rel_path,
                "Count": 1,
                "SessionId": execution_id,
                "Extension": ext,
                "Technology": tech,
                "Bytes": byte_size,
                "CharacterLength": char_len,
                "LinesOfCode": loc,
                "ParseResult": parse_result,
                "Ignored": "False" if is_unit else "True",
                "OriginFilePath": fpath,
            })

    fieldnames = [
        "Element", "ProjectId", "FileId", "Count", "SessionId", "Extension",
        "Technology", "Bytes", "CharacterLength", "LinesOfCode", "ParseResult",
        "Ignored", "OriginFilePath",
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    units = sum(1 for r in rows if r["Ignored"] == "False")
    print(
        f"  InputFilesInventory.csv: {len(rows)} rows written to {csv_path} "
        f"({units} conversion unit(s), {len(rows) - units} ignored data/resource file(s))"
    )
    return len(rows)


# Hoisted out of ``extract_scala_imports`` so the pattern is compiled once
# per process instead of once per call — for workloads with many Scala
# files or many Scala cells this eliminates a meaningful amount of
# per-call overhead.
_SCALA_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+(?:\.\{[^}]+\}|\.\*|\._)?)")


def extract_python_imports(file_path: str) -> list[tuple[str, str]]:
    """Extract import statements from a Python file or notebook.

    Returns list of ``(module, full_statement)`` tuples. For notebooks, only
    Python-language code cells are inspected — cross-language cells are
    analyzed by the Scala sub-skill's equivalent helper.
    """
    imports: list[tuple[str, str]] = []
    # Cache detect_format and pass it through to parse_notebook so the
    # 4 KiB head isn't re-read inside parse_notebook on the notebook path.
    info = detect_format(file_path)
    if info.get("format") != "not_notebook":
        try:
            nb = parse_notebook(file_path, info=info)
        except (ValueError, OSError):
            return imports
        for cell in nb.cells:
            if cell.cell_type != "code" or cell.cell_language != "python":
                continue
            try:
                tree = ast.parse(cell.source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append((alias.name, f"import {alias.name}"))
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module:
                        imports.append((module, f"from {module} import ..."))
        return imports

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((alias.name, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module:
                    imports.append((module, f"from {module} import ..."))
    except (SyntaxError, OSError):
        pass
    return imports


def extract_scala_imports(file_path: str) -> list[tuple[str, str]]:
    """Extract import statements from a Scala file or notebook.

    Returns list of ``(package, full_statement)`` tuples. For notebooks, only
    Scala-language code cells are inspected.
    """
    return _extract_jvm_imports(file_path, "scala")


def extract_java_imports(file_path: str) -> list[tuple[str, str]]:
    """Extract import statements from a Java file or notebook.

    Java-specific handling versus Scala: the trailing ``;`` is stripped and
    ``import static a.b.C.member;`` is unwrapped to ``a.b.C.member`` (a bare
    ``[\\w.]+`` match would otherwise report the package as ``static``).
    """
    return _extract_jvm_imports(file_path, "java")


def _extract_jvm_imports(file_path: str, language: str) -> list[tuple[str, str]]:
    """Shared Scala/Java import scanner. Notebook cells are filtered by language."""
    imports: list[tuple[str, str]] = []

    def _scan_text(text: str) -> None:
        for line in text.splitlines():
            stripped = line.strip()
            static = False
            if language == "java" and stripped.startswith("import static "):
                # Re-shape to a plain import so the shared regex applies.
                line = stripped.replace("import static ", "import ", 1)
                static = True
            m = _SCALA_IMPORT_RE.match(line)
            if m:
                full_import = m.group(1).rstrip(";")
                base = full_import.split(".{")[0].split("._")[0].split(".*")[0]
                prefix = "import static " if static else "import "
                imports.append((base, f"{prefix}{full_import}"))

    # Cache detect_format and pass it through to parse_notebook so the
    # 4 KiB head isn't re-read inside parse_notebook on the notebook path.
    info = detect_format(file_path)
    if info.get("format") != "not_notebook":
        try:
            nb = parse_notebook(file_path, info=info)
        except (ValueError, OSError):
            return imports
        for cell in nb.cells:
            if cell.cell_type != "code" or cell.cell_language != language:
                continue
            _scan_text(cell.source)
        return imports

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            _scan_text(f.read())
    except OSError:
        pass
    return imports


def classify_dependency(module: str, language: str, source_files: set[str], user_packages: set[str] | None = None) -> str:
    """Classify a dependency as UserCodeFile, ThirdPartyLibraries, or UnknownLibraries."""
    if language == "python":
        top_level = module.split(".")[0]

        # Check if it's a local file in the project
        module_as_path = module.replace(".", "/")
        candidates = [
            module_as_path + ".py",
            module_as_path + "/__init__.py",
            os.path.join(module_as_path, "__init__.py"),
        ]
        for c in candidates:
            if c in source_files:
                return "UserCodeFile"

        if top_level in PYTHON_STDLIB:
            return "ThirdPartyLibraries"
        if top_level in KNOWN_THIRD_PARTY_PYTHON:
            return "ThirdPartyLibraries"
        return "UnknownLibraries"

    else:  # scala / java (SCALA_JAVA_STDLIB covers both)
        top_level = module.split(".")[0]

        # SNOW-3362688: Check Scala/Java stdlib first
        if top_level in SCALA_JAVA_STDLIB:
            return "ThirdPartyLibraries"

        # SNOW-3362688: Check user-defined packages from package declarations
        if user_packages:
            for user_pkg in user_packages:
                if module.startswith(user_pkg):
                    return "UserCodeFile"

        # Check known third-party
        for prefix in KNOWN_THIRD_PARTY_SCALA:
            if module.startswith(prefix):
                return "ThirdPartyLibraries"

        # Check if it matches a local file
        module_as_path = module.replace(".", "/") + ".scala"
        if module_as_path in source_files:
            return "UserCodeFile"
        return "UnknownLibraries"


def generate_artifact_dependency_inventory(
    source_dir: str,
    output_dir: str,
    language: str,
    execution_id: str,
) -> int:
    """Generate Reports/ArtifactDependencyInventory.csv. Returns count of rows."""
    reports_dir = os.path.join(output_dir, "Reports")
    os.makedirs(reports_dir, exist_ok=True)
    csv_path = os.path.join(reports_dir, "ArtifactDependencyInventory.csv")

    ext = _source_ext(language)
    extract_fn = extract_python_imports
    if language == "scala":
        extract_fn = extract_scala_imports
    elif language == "java":
        extract_fn = extract_java_imports

    # Build set of all source files (relative paths)
    source_files = set()
    for root, _dirs, files in _walk_filtered(source_dir):
        for fname in files:
            rel = os.path.relpath(os.path.join(root, fname), source_dir).replace("\\", "/")
            source_files.add(rel)

    # SNOW-3362688: Detect user-defined packages for JVM classification
    user_packages = (
        detect_user_packages(source_dir, language) if language in ("scala", "java") else None
    )

    rows = []
    for root, _dirs, files in _walk_filtered(source_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            # Include plain source files AND notebooks, so ArtifactDependencyInventory
            # captures imports from every scannable artifact. Check the
            # cheap flat-extension match first so plain ``.py`` / ``.scala``
            # files never pay the ``is_notebook`` probe cost.
            if not fname.endswith(ext) and not is_notebook(fpath):
                continue
            rel_path = os.path.relpath(fpath, source_dir).replace("\\", "/")

            imports = extract_fn(fpath)
            for module, _stmt in imports:
                dep_type = classify_dependency(module, language, source_files, user_packages=user_packages)
                rows.append({
                    "ExecutionId": execution_id,
                    "FileId": rel_path,
                    "Dependency": module,
                    "Type": dep_type,
                    "Success": "True",
                    "StatusDetail": "Parsed",
                    "Arguments": "",
                    "Location": "",
                    "IndirectDependencies": "",
                    "TotalIndirectDependencies": 0,
                    "DirectParents": "",
                    "TotalDirectParents": 0,
                    "IndirectParents": "",
                    "TotalIndirectParents": 0,
                })

    fieldnames = [
        "ExecutionId", "FileId", "Dependency", "Type", "Success", "StatusDetail",
        "Arguments", "Location", "IndirectDependencies", "TotalIndirectDependencies",
        "DirectParents", "TotalDirectParents", "IndirectParents", "TotalIndirectParents",
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  ArtifactDependencyInventory.csv: {len(rows)} rows written to {csv_path}")
    return len(rows)


def generate_tool_execution_csv(output_dir: str, execution_id: str) -> None:
    """Generate Reports/tool_execution.csv."""
    reports_dir = os.path.join(output_dir, "Reports")
    os.makedirs(reports_dir, exist_ok=True)
    csv_path = os.path.join(reports_dir, "tool_execution.csv")

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ExecutionId", "ToolVersion"])
        writer.writeheader()
        writer.writerow({"ExecutionId": execution_id, "ToolVersion": TOOL_VERSION})

    print(f"  tool_execution.csv: written to {csv_path}")


def generate_log_file(
    output_dir: str,
    project_name: str,
    email: str,
    company: str,
    execution_id: str,
    source_dir: str,
    language: str = "python",
) -> None:
    """Generate Logs/<Language>SnowConvert-Log-<timestamp>.log."""
    logs_dir = os.path.join(output_dir, "Logs")
    os.makedirs(logs_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d.%H%M%S")
    lang_label = _lang_conv(language)["label"]
    log_path = os.path.join(logs_dir, f"{lang_label}SnowConvert-Log-{timestamp}.log")

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"SCOS Migration Log\n")
        f.write(f"==================\n")
        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
        f.write(f"ExecutionId: {execution_id}\n")
        f.write(f"ToolVersion: {TOOL_VERSION}\n")
        f.write(f"ProjectName: {project_name}\n")
        f.write(f"OwnerEmail: {email}\n")
        f.write(f"OwnerCompany: {company}\n")
        f.write(f"SourceDirectory: {source_dir}\n")
        f.write(f"OutputDirectory: {output_dir}\n")

    print(f"  Log file: written to {log_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate SCOS-compatible reports from SCOS migration outputs")
    parser.add_argument("--output-dir", required=True, help="Root output directory (Reports/ and Logs/ will be created here)")
    parser.add_argument("--analysis", default="analysis.json", help="Path to analysis.json (default: analysis.json)")
    parser.add_argument("--source-dir", required=True, help="Original source code directory (for InputFilesInventory)")
    parser.add_argument("--migrated-dir", default=None, help="Migrated _scos directory to scan for SCOS comments (auto-detected if not set)")
    parser.add_argument("--project-name", default="SCOS Migration", help="Project name")
    parser.add_argument("--email", default="", help="Customer email")
    parser.add_argument("--company", default="", help="Customer company")
    parser.add_argument("--language", choices=["python", "scala", "java"], default="python", help="Source language (default: python)")

    args = parser.parse_args()

    output_dir = os.path.abspath(args.output_dir)

    # Defensive: strip a trailing "Reports" segment so callers who pass
    # <conversion_root>/Reports get the same result as <conversion_root>.
    # The script always creates Reports/ internally; passing it twice would
    # produce Reports/Reports/ nesting.
    if os.path.basename(output_dir) == "Reports":
        parent = os.path.dirname(output_dir)
        print(
            f"WARNING: --output-dir ends with 'Reports'; using parent "
            f"'{parent}' to avoid Reports/Reports/ nesting.",
            file=sys.stderr,
        )
        output_dir = parent

    source_dir = os.path.abspath(args.source_dir)
    analysis_path = os.path.abspath(args.analysis)

    # Auto-detect migrated directory.
    # Priority:
    #   1. Explicit --migrated-dir (user override)
    #   2. <output-dir>/Output/  — standard skill layout: --output-dir is the
    #      Conversion-SCOS-<timestamp> root and Output/ holds the migrated files.
    #   3. <source-dir>_scos     — legacy layout where a sibling _scos/ dir was used.
    #   4. <output-dir>           — last-resort fallback.
    migrated_dir = args.migrated_dir
    if migrated_dir is None:
        conversion_output = os.path.join(output_dir, "Output")
        legacy_candidate = source_dir + "_scos"
        if os.path.isdir(conversion_output):
            migrated_dir = conversion_output
        elif os.path.isdir(legacy_candidate):
            migrated_dir = legacy_candidate
        else:
            migrated_dir = output_dir
    migrated_dir = os.path.abspath(migrated_dir) if migrated_dir else None

    execution_id = str(uuid.uuid4())

    print(f"SCOS Report Generator")
    print(f"=====================")
    print(f"  Output:    {output_dir}")
    print(f"  Analysis:  {analysis_path}")
    print(f"  Source:    {source_dir}")
    print(f"  Migrated:  {migrated_dir}")
    print(f"  Language:  {args.language}")
    print(f"  Execution: {execution_id}")
    print()

    # Load EWI mapping
    print("Loading EWI code mapping...")
    mapping = load_ewi_mapping(args.language)
    print(f"  Loaded {len(mapping)} mapping entries for {args.language}")
    print()

    # Generate all reports
    print("Generating reports...")

    # SNOW-3347464: Ensure every output file has a migration header before scanning
    if migrated_dir and os.path.isdir(migrated_dir):
        patched = ensure_migration_headers(migrated_dir, args.language)
        if patched:
            print(f"  Migration headers: inserted into {patched} files")
        else:
            print("  Migration headers: all files already have headers")

        # Ensure each "# SCOS:" comment carries its refined EWI code + status
        # suffix inline (and drop any legacy standalone "#EWI:" line).
        # Content-anchored, so it runs BEFORE generate_issues_csv — which then
        # scans the final files and reads the same inline codes.
        annotated = annotate_scos_markers(
            migrated_dir, args.language, mapping,
            source_dir=source_dir,
        )
        if annotated:
            print(f"  EWI codes: normalized {annotated} '# SCOS:' comment line(s)")
        else:
            print("  EWI codes: all '# SCOS:' comments already carry a code")
    print()

    issues_count = generate_issues_csv(
        analysis_path, migrated_dir, output_dir, args.language, mapping,
        source_dir=source_dir,
    )

    files_count = generate_input_files_inventory(
        source_dir, output_dir, args.project_name, execution_id
    )

    deps_count = generate_artifact_dependency_inventory(
        source_dir, output_dir, args.language, execution_id
    )

    generate_tool_execution_csv(output_dir, execution_id)

    generate_log_file(
        output_dir, args.project_name, args.email, args.company,
        execution_id, source_dir, args.language
    )

    print()
    print(f"Report generation complete!")
    print(f"  Issues:       {issues_count}")
    print(f"  Input files:  {files_count}")
    print(f"  Dependencies: {deps_count}")
    print(f"  Reports at:   {os.path.join(output_dir, 'Reports')}")
    print(f"  Logs at:      {os.path.join(output_dir, 'Logs')}")


if __name__ == "__main__":
    sys.exit(main() or 0)
