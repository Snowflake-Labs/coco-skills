"""Scala-native known-patches + investigation detectors (PySpark parity).

Do NOT import or run PySpark ``patch_engine`` detectors on ``.scala`` sources —
those emit Python ``os.environ`` / ``pass`` rewrites. This module produces
``patch-add``-compatible entries that use ``System.getProperty`` / Scala idioms.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

DetectFn = Callable[[str, str], List[Dict[str, Any]]]
BuildFn = Callable[[Dict[str, Any], str], Dict[str, Any]]


# ---------------------------------------------------------------------------
# Confident auto-patches (KNOWN_PATCHES)
# ---------------------------------------------------------------------------


def _detect_dbutils_notebook_exit(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    if re.search(r"dbutils\.notebook\.exit\s*\(", source_text):
        return [{}]
    return []


def _build_dbutils_notebook_exit_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    return {
        "id": "remove_dbutils_notebook_exit",
        "relative_file": relative_file,
        "regex": True,
        "replace_all": True,
        "note": "dbutils.notebook.exit(...) → sys.exit(0) — Databricks notebook API absent in SCOS",
        "search": r"dbutils\.notebook\.exit\s*\([^)]*\)",
        "replace": "sys.exit(0) /* SCOS: removed dbutils.notebook.exit */",
    }


_WIDGET_GET = re.compile(
    r"""dbutils\.widgets\.get\(\s*(["'])(?P<name>[^"']+)\1\s*\)"""
)


def _detect_widget_get(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for m in _WIDGET_GET.finditer(source_text):
        name = m.group("name")
        if name not in seen:
            seen.add(name)
            out.append({"name": name, "full_match": m.group(0)})
    return out


def _build_widget_get_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    name = match_info["name"]
    env = name.upper().replace("-", "_").replace(".", "_")
    return {
        "id": f"widget_get_{env.lower()}",
        "relative_file": relative_file,
        "replace_all": True,
        "note": f"dbutils.widgets.get('{name}') → System.getProperty(\"SCOS_WIDGET_{env}\")",
        "search": match_info["full_match"],
        "replace": f'System.getProperty("SCOS_WIDGET_{env}", "")',
    }


_DROP_TABLE = re.compile(
    r"""(?im)^(?P<indent>\s*)(?:spark|session)\.sql\(\s*(?:"DROP\s+TABLE[^"]*"|'DROP\s+TABLE[^']*')\s*\)"""
)


def _detect_drop_table_sql(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    if _DROP_TABLE.search(source_text):
        return [{}]
    return []


def _build_drop_table_sql_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    return {
        "id": "remove_drop_table_sql",
        "relative_file": relative_file,
        "regex": True,
        "replace_all": True,
        "note": "spark.sql(\"DROP TABLE...\") → () — destructive DDL must not run against shared test schema",
        "search": r"""(?im)^(\s*)(?:spark|session)\.sql\(\s*(?:"DROP\s+TABLE[^"]*"|'DROP\s+TABLE[^']*')\s*\)""",
        "replace": r"\1() /* SCOS: removed DROP TABLE */",
    }


_DISPLAY = re.compile(r"""(?<![\w.])display\s*\(""")


def _detect_display(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    if _DISPLAY.search(source_text):
        return [{}]
    return []


def _build_display_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    return {
        "id": "remove_display",
        "relative_file": relative_file,
        "regex": True,
        "replace_all": True,
        "note": "display(...) → () — Databricks viewer unavailable in local Spark/SCOS",
        "search": r"(?<![\w.])display\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)",
        "replace": r"() /* SCOS: removed display */",
    }


_SAVE_AS_TABLE = re.compile(
    r"""\.saveAsTable\(\s*(["'])(?P<name>[^"']+)\1\s*\)"""
)


def _detect_save_as_table(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for m in _SAVE_AS_TABLE.finditer(source_text):
        name = m.group("name")
        if name.startswith("SCOS_") or "getProperty" in m.group(0):
            continue
        if name not in seen:
            seen.add(name)
            out.append({"name": name, "full_match": m.group(0)})
    return out


def _build_save_as_table_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    name = match_info["name"]
    env = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper() or "SINK"
    return {
        "id": f"save_as_table_{env.lower()}",
        "relative_file": relative_file,
        "replace_all": True,
        "note": (
            f'.saveAsTable("{name}") → System.getProperty("SCOS_SINK_{env}") '
            "(env indirection for golden-schema / trial isolation)"
        ),
        "search": match_info["full_match"],
        "replace": f'.saveAsTable(System.getProperty("SCOS_SINK_{env}", "{name}"))',
    }


_WIDGET_TEXT = re.compile(
    r"""dbutils\.widgets\.(?:text|dropdown|combobox)\(\s*(["'])(?P<name>[^"']+)\1"""
)


def _detect_widget_declaration(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for m in _WIDGET_TEXT.finditer(source_text):
        name = m.group("name")
        if name not in seen:
            seen.add(name)
            out.append({"name": name, "full_match": m.group(0)})
    return out


def _build_widget_declaration_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    name = match_info["name"]
    env = name.upper().replace("-", "_").replace(".", "_")
    # Neutralize widget declaration — values come from SCOS_WIDGET_* env / getProperty
    return {
        "id": f"widget_decl_{env.lower()}",
        "relative_file": relative_file,
        "regex": True,
        "replace_all": True,
        "note": (
            f"dbutils.widgets.*(('{name}', …) → () — widget defaults come from "
            f'System.getProperty("SCOS_WIDGET_{env}")'
        ),
        "search": (
            rf"""dbutils\.widgets\.(?:text|dropdown|combobox)\(\s*(["']){re.escape(name)}\1[^)]*\)"""
        ),
        "replace": f'() /* SCOS: widget {name} via SCOS_WIDGET_{env} */',
    }


KNOWN_PATCHES: List[Dict[str, Any]] = [
    {
        "id": "remove_dbutils_notebook_exit",
        "description": "Replace dbutils.notebook.exit(...) with sys.exit(0)",
        "detect": _detect_dbutils_notebook_exit,
        "build_patch": _build_dbutils_notebook_exit_patch,
    },
    {
        "id": "widget_get_env_indirection",
        "description": "Replace dbutils.widgets.get('<key>') with System.getProperty(\"SCOS_WIDGET_<KEY>\")",
        "detect": _detect_widget_get,
        "build_patch": _build_widget_get_patch,
    },
    {
        "id": "widget_declaration_neutralize",
        "description": "Neutralize dbutils.widgets.text/dropdown declarations (env-driven defaults)",
        "detect": _detect_widget_declaration,
        "build_patch": _build_widget_declaration_patch,
    },
    {
        "id": "save_as_table_env_indirection",
        "description": "Rewrite literal .saveAsTable(\"name\") to SCOS_SINK_* env indirection",
        "detect": _detect_save_as_table,
        "build_patch": _build_save_as_table_patch,
    },
    {
        "id": "remove_drop_table_sql",
        "description": "Neutralize spark.sql(\"DROP TABLE...\") against the shared test schema",
        "detect": _detect_drop_table_sql,
        "build_patch": _build_drop_table_sql_patch,
    },
    {
        "id": "remove_display",
        "description": "Remove Databricks display(...) viewer calls",
        "detect": _detect_display,
        "build_patch": _build_display_patch,
    },
]


def suggest_known_patches(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    """Run every Scala KNOWN_PATCHES detector; return patch-add entries."""
    patches: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for kp in KNOWN_PATCHES:
        try:
            matches = kp["detect"](source_text, relative_file)
        except Exception:  # noqa: BLE001
            continue
        for m in matches:
            try:
                entry = kp["build_patch"](m, relative_file)
            except Exception:  # noqa: BLE001
                continue
            pid = entry.get("id", "")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                patches.append(entry)
    return patches


# ---------------------------------------------------------------------------
# Investigation worklist (no auto-fix)
# ---------------------------------------------------------------------------

_INVESTIGATION_PATTERNS: List[Tuple[str, "re.Pattern[str]", str]] = [
    (
        "cloud_read_write",
        re.compile(r"""(?:s3a?://|dbfs:/|gs://|abfss?://|wasbs?://|hdfs://)"""),
        "Cloud path. Redirect reads to System.getProperty(\"SCOS_INPUT_<ID>\") "
        "and writes to System.getProperty(\"SCOS_SINK_<ID>\"). See patch-author.md.",
    ),
    (
        "connector_read",
        re.compile(
            r'\.format\(\s*"(?:snowflake|jdbc|redshift|mongo|mongodb|'
            r'com\.crealytics\.spark\.excel|excel)"'
        ),
        "Connector / excel / mongo read — PER-SIDE patch: source→spark.table(...), "
        "migrated→rebind or saveAsTable/SCOS_SINK_* (see patch-author.md).",
    ),
    (
        "literal_file_write",
        re.compile(r'\.write\.[a-zA-Z.]*(?:parquet|csv|json|orc|text|save)\(\s*"'),
        "Literal file write. Patch to System.getProperty(\"SCOS_SINK_<ID>\") "
        "(Phase B stage capture) or saveAsTable.",
    ),
    (
        "rdd_io",
        re.compile(r"""new\s+SparkContext|sc\.textFile|sc\.hadoopFile|sc\.objectFile"""),
        "SparkContext / RDD I/O is incompatible with Spark Connect. Refactor to DataFrame API.",
    ),
    (
        "namespace_read",
        re.compile(
            r"""(?i)(?:spark\.table|spark\.sql|\.table\()\s*\(\s*["'][A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_]"""
        ),
        "Possible hardcoded DB.SCHEMA.TABLE. Rebind prefix to SCOS_DATABASE_NAME / SCOS_OUTPUT_SCHEMA.",
    ),
    (
        "file_open",
        re.compile(r"""Source\.fromFile|scala\.io\.Source|Files\.readAllBytes|new\s+FileInputStream"""),
        "Local file open — if it reads config/aux the workload needs, redirect to "
        "System.getProperty(\"SCOS_TEST_AUX_<NAME>\") and declare relational:false.",
    ),
    (
        "external_dep",
        re.compile(r"""dbutils\.fs\.|dbutils\.secrets\.|com\.amazonaws\.|software\.amazon\.awssdk"""),
        "External / Databricks dependency. Rewrite to native Spark, inline literal, or delete.",
    ),
    (
        "udf_registration",
        re.compile(r"""(?:spark\.udf|functions)\.register\s*\(|\.udf\s*\("""),
        "UDF registration — may need expected_divergences scope=udf on Phase B if "
        "ClassNotFound on Snowflake server; see udf-dependencies.md.",
    ),
]


def scan_investigation_sites(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    """Return residual non-Spark-I/O sites for the patch-author worklist."""
    sites: List[Dict[str, Any]] = []
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for lineno, raw in enumerate(source_text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("*"):
            continue
        if "TEST-PATCH" in raw or "SCOS_INPUT" in raw or "SCOS_SINK" in raw:
            continue
        if "System.getProperty" in raw:
            continue
        for category, pattern, hint in _INVESTIGATION_PATTERNS:
            if not pattern.search(raw):
                continue
            key = (category, stripped)
            existing = index.get(key)
            if existing is None:
                entry = {
                    "category": category,
                    "relative_file": relative_file,
                    "line": lineno,
                    "text": stripped[:200],
                    "occurrences": 1,
                    "hint": hint,
                }
                index[key] = entry
                sites.append(entry)
            else:
                existing["occurrences"] += 1
    return sites


def seed_udf_expected_divergences(analysis: dict) -> int:
    """Seed analysis.expected_divergences from AST udfs / unsupported_constructs.

    Returns the number of new divergence entries added. Scope ``udf`` lets Phase B
    treat ClassNotFound / Kryo UDF failures as documented divergences (PySpark parity).
    """
    exp = analysis.setdefault("expected_divergences", {})
    added = 0
    for ep in analysis.get("entrypoints") or []:
        if not isinstance(ep, dict):
            continue
        eid = ep.get("id") or ""
        if not eid:
            continue
        names: List[str] = []
        for u in ep.get("udfs") or []:
            if isinstance(u, dict) and u.get("name"):
                names.append(str(u["name"]))
            elif isinstance(u, str):
                names.append(u)
        for uc in ep.get("unsupported_constructs") or []:
            if isinstance(uc, dict) and (uc.get("kind") or "").lower() == "udf":
                detail = str(uc.get("detail") or uc.get("name") or "udf")
                names.append(detail)
        for name in names:
            key = f"{eid}.__udf__"
            lst = list(exp.get(key) or [])
            col = name.upper()[:80] or "UDF"
            if any((d.get("column") or "").upper() == col and (d.get("scope") or "") == "udf"
                   for d in lst if isinstance(d, dict)):
                continue
            lst.append({
                "column": col,
                "reason": f"JVM UDF '{name}' may be unavailable on Snowflake server (SCOS limitation)",
                "scope": "udf",
                "baseline_sample": "",
                "shadow_sample": "",
            })
            exp[key] = lst
            added += 1
    return added
