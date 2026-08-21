"""DDL catalog readers and ASG enrichment.

Two readers are provided:

1. **Hive DDL reader** (``parse_hive_ddl`` / ``enrich_asg_from_ddl``)
   For Scala Spark workloads with Hive-format DDL (backtick names, ``-- table``
   section headers).  Matching is column-set-based, requiring ``seq_string_vars``
   from the Scala parser.

2. **Standard SQL DDL reader** (``parse_sql_ddl`` / ``enrich_asg_from_sql_ddl``)
   For Python (PySpark/Snowpark) or Scala workloads with standard SQL DDL
   (``CREATE TABLE name (col TYPE, ...)``).  Matching is name-based — no
   ``seq_string_vars`` required.  Supports Snowflake, Spark SQL, PostgreSQL,
   MySQL, and BigQuery syntax.

----

Hive DDL reader details
-----------------------

Parses Hive ``CREATE TABLE`` DDL files and enriches an ASG with authoritative
column schemas for tables that are demonstrably used by the workload.

Matching strategy (Option A — column-based)
-------------------------------------------
The DDL may contain tables that are *not* used by the workload being analysed.
Rather than blindly injecting every DDL table, we match tables to the workload
using column evidence already extracted from the source code:

1.  ``seq_string_vars`` — resolved ``List[String]`` variable values from the
    Scala parser (column lists passed to wrapper functions like
    ``getDataFromSrv``).
2.  ``companion_symbols`` — resolved ``val col: String = "col_name"`` constants
    from companion/schema objects referenced in ``select()`` calls.

A DDL table is considered *used* when the workload code contains a column list
that is a non-trivial subset of the table's declared columns.  "Non-trivial"
means at least ``MIN_NON_TRIVIAL`` columns that are not common audit/partition
columns (``dt``, ``ts_last_received``, etc.).

When a match is found the DataSource is enriched with:
  - The authoritative column name (lower-cased Hive name).
  - The Hive → internal type mapping.
  - ``source = InferenceSource.CATALOG`` / ``confidence = HIGH``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from warp_core.ir.pyspark_models import (
    ASG,
    DataSource,
    InferenceConfidence,
    InferenceSource,
    InferredColumn,
    SourceLocation,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Columns that appear in virtually every Hive table and carry no matching signal.
_TRIVIAL_COLS: frozenset[str] = frozenset({
    "dt", "ts_last_received", "ds_source", "ds_hashdiff", "ts_insert_row",
    "ts_business", "id_task", "original_row_index", "ts_insert_row_bv",
})

#: Minimum number of *non-trivial* matching columns to accept a DDL table match.
MIN_NON_TRIVIAL: int = 3

#: Maps Hive storage types to WARP internal type strings.
_HIVE_TYPE_MAP: dict[str, str] = {
    "string": "STRING",
    "varchar": "STRING",
    "char": "STRING",
    "text": "STRING",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "int": "INT",
    "integer": "INT",
    "bigint": "INT",
    "tinyint": "INT",
    "smallint": "INT",
    "double": "DECIMAL",
    "float": "DECIMAL",
    "decimal": "DECIMAL",
    "numeric": "DECIMAL",
    "boolean": "BOOLEAN",
    "binary": "BINARY",
}


# ---------------------------------------------------------------------------
# DDL parser
# ---------------------------------------------------------------------------

def parse_hive_ddl(ddl_path: str | Path) -> dict[str, dict[str, str]]:
    """Parse a Hive DDL file and return a column-schema catalog.

    Returns
    -------
    dict[str, dict[str, str]]
        ``{fully_qualified_table_name: {col_name_lower: internal_type}}``
    """
    text = Path(ddl_path).read_text(encoding="utf-8", errors="replace")
    catalog: dict[str, dict[str, str]] = {}

    # Each table block starts with "-- table `db`.`table`" (on one line or its own line).
    for block in re.split(r"(?:^|\n)-- table\s+", text):
        # The table name is the first non-whitespace content of the block.
        name_m = re.match(r"`([^`]+)`\.`([^`]+)`", block.lstrip())
        if not name_m:
            continue
        db, tbl = name_m.groups()
        fqn = f"{db}.{tbl}"

        # Column definitions appear between CREATE TABLE (...) and PARTITIONED BY / STORED BY / ROW FORMAT.
        create_m = re.search(
            r"CREATE\s+(?:EXTERNAL\s+)?TABLE\s[^(]+\((.*?)\)\s*(?:PARTITIONED\s+BY|ROW\s+FORMAT|STORED\s+(?:BY|AS)|LOCATION|;)",
            block,
            re.DOTALL | re.IGNORECASE,
        )
        if not create_m:
            continue

        col_block = create_m.group(1)
        cols: dict[str, str] = {}
        for col_m in re.finditer(r"`(\w+)`\s+([\w()]+)", col_block):
            col_name = col_m.group(1).lower()
            raw_type = col_m.group(2).lower().split("(")[0]  # strip precision
            cols[col_name] = _HIVE_TYPE_MAP.get(raw_type, "STRING")

        if cols:
            catalog[fqn] = cols

    return catalog


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _collect_workload_col_lists(
    seq_string_vars: dict[str, dict[str, list[str]]],
    companion_symbols: dict[str, str] | None = None,
) -> list[frozenset[str]]:
    """Collect non-trivial column sets visible from the workload source code.

    Parameters
    ----------
    seq_string_vars:
        ``{filepath: {var_name: [col_name, ...]}}`` — resolved
        ``List[String]`` variables from the Scala parser.
    companion_symbols:
        ``{ObjectName.fieldName: resolved_string_value}`` — resolved
        companion-object constants (optional, adds more signal).
    """
    col_sets: list[frozenset[str]] = []

    # From List[String] variables
    for _file, vars_dict in seq_string_vars.items():
        for _var, cols in vars_dict.items():
            lower_cols = {c.lower() for c in cols if isinstance(c, str)}
            non_trivial = lower_cols - _TRIVIAL_COLS
            if len(non_trivial) >= MIN_NON_TRIVIAL:
                col_sets.append(frozenset(non_trivial))

    # From companion symbol values (group by object prefix)
    if companion_symbols:
        by_object: dict[str, set[str]] = {}
        for key, val in companion_symbols.items():
            if "." in key:
                obj = key.split(".")[0]
                by_object.setdefault(obj, set()).add(val.lower())
        for obj_cols in by_object.values():
            non_trivial = obj_cols - _TRIVIAL_COLS
            if len(non_trivial) >= MIN_NON_TRIVIAL:
                col_sets.append(frozenset(non_trivial))

    return list({s for s in col_sets})  # deduplicate


def match_ddl_to_workload(
    catalog: dict[str, dict[str, str]],
    col_lists: list[frozenset[str]],
) -> dict[str, list[frozenset[str]]]:
    """Identify which DDL tables are used by the workload.

    A DDL table is matched when at least one workload column list is a
    non-trivial subset of the table's declared columns.

    Returns
    -------
    dict[str, list[frozenset[str]]]
        ``{fqn: [matched_col_lists]}`` — only matched tables are present.
    """
    matches: dict[str, list[frozenset[str]]] = {}
    for fqn, ddl_cols in catalog.items():
        ddl_col_set = frozenset(ddl_cols.keys())
        matched: list[frozenset[str]] = [
            lst for lst in col_lists if lst <= ddl_col_set
        ]
        if matched:
            matches[fqn] = matched
    return matches


# ---------------------------------------------------------------------------
# ASG enrichment
# ---------------------------------------------------------------------------

def enrich_asg_from_ddl(
    asg: ASG,
    ddl_path: str | Path,
    seq_string_vars: dict[str, dict[str, list[str]]],
    companion_symbols: dict[str, str] | None = None,
) -> int:
    """Enrich an ASG with column schemas from a Hive DDL catalog.

    For each DDL table whose columns are demonstrably used by the workload:
      - If an existing ``data_in`` node already has the same name, its columns
        are upgraded to catalog-sourced entries (only if catalog confidence is
        higher than current confidence).
      - Otherwise, a new ``data_in`` node is added with the authoritative schema.

    Parameters
    ----------
    asg:
        The ASG to enrich in-place.
    ddl_path:
        Path to the Hive DDL file (``HiveTableDDLInput.txt``).
    seq_string_vars:
        Resolved ``List[String]`` variables from the Scala parser
        (``{filepath: {var_name: [col, ...]}}``)
    companion_symbols:
        Optional resolved companion-object constants.

    Returns
    -------
    int
        Number of new DataSource nodes added.
    """
    catalog = parse_hive_ddl(ddl_path)
    col_lists = _collect_workload_col_lists(seq_string_vars, companion_symbols)
    if not col_lists:
        return 0

    matched = match_ddl_to_workload(catalog, col_lists)
    if not matched:
        return 0

    # Index existing data_in by name for quick lookup
    existing_by_name: dict[str, DataSource] = {}
    for src in asg.data_in:
        if src.name:
            existing_by_name[src.name] = src

    max_id = max(
        (int(s.id.split("_")[1]) for s in asg.data_in if "_" in s.id),
        default=0,
    )

    added = 0
    for fqn, matched_lists in matched.items():
        ddl_cols = catalog[fqn]

        # Build the columns the workload actually selects (union of matched lists)
        used_col_names: set[str] = set()
        for lst in matched_lists:
            used_col_names |= lst
        # Only include columns that exist in the DDL (filter out any stray matches)
        used_col_names &= set(ddl_cols.keys())

        catalog_cols = [
            InferredColumn(
                name=col,
                inferred_type=ddl_cols[col],
                source=InferenceSource.CATALOG,
                confidence=InferenceConfidence.HIGH,
            )
            for col in sorted(used_col_names)
        ]

        if fqn in existing_by_name:
            # Enrich existing node — upgrade columns that are catalog-sourced
            src = existing_by_name[fqn]
            existing_names = {c.name for c in (src.inferred_columns or [])}
            for new_col in catalog_cols:
                if new_col.name not in existing_names:
                    src.inferred_columns = (src.inferred_columns or []) + [new_col]
        else:
            # Add a new catalog-sourced DataSource
            max_id += 1
            short_name = fqn.split(".")[-1]
            new_src = DataSource(
                id=f"in_{max_id:04d}",
                type="table",
                name=fqn,
                path=fqn,
                location=SourceLocation(pathfile="DDL", scope="catalog", span="0:0-0:0"),
                inferred_columns=catalog_cols,
                is_indirect=False,
                via_function=None,
            )
            asg.data_in.append(new_src)
            added += 1

    return added


# ===========================================================================
# Standard SQL DDL reader (Python / Snowpark / Spark SQL / PostgreSQL / MySQL)
# ===========================================================================

#: Maps standard SQL storage types to WARP internal type strings.
_SQL_TYPE_MAP: dict[str, str] = {
    "varchar": "STRING",
    "varchar2": "STRING",
    "char": "STRING",
    "nchar": "STRING",
    "nvarchar": "STRING",
    "text": "STRING",
    "string": "STRING",
    "clob": "STRING",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "timestamp_ntz": "TIMESTAMP",
    "timestamp_ltz": "TIMESTAMP",
    "timestamp_tz": "TIMESTAMP",
    "datetime": "TIMESTAMP",
    "int": "L_INT",
    "integer": "L_INT",
    "bigint": "L_INT",
    "smallint": "L_INT",
    "tinyint": "L_INT",
    "number": "NUMERIC",
    "numeric": "NUMERIC",
    "decimal": "NUMERIC",
    "float": "NUMERIC",
    "double": "NUMERIC",
    "real": "NUMERIC",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "binary": "BINARY",
    "varbinary": "BINARY",
}

#: DDL keywords that can appear where a column name would be, but are not columns.
_DDL_KEYWORDS: frozenset[str] = frozenset({
    "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT",
    "INDEX", "KEY", "COMMENT", "REFERENCES",
})

#: Regex to locate the start of a CREATE TABLE statement and capture the table name.
_SQL_CREATE_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([`\"\w][\w.`\"]*)\s*\(",
    re.IGNORECASE,
)


def _split_ddl_col_block(col_block: str) -> list[str]:
    """Split a DDL column-definition block into individual column strings.

    Splits on commas that are *not* inside parentheses, so that type modifiers
    like ``VARCHAR(50)`` or ``DECIMAL(18, 4)`` are kept intact.
    """
    defs: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in col_block:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            token = "".join(current).strip()
            if token:
                defs.append(token)
            current = []
        else:
            current.append(ch)
    remainder = "".join(current).strip()
    if remainder:
        defs.append(remainder)
    return defs


def parse_sql_ddl(ddl_path: str | Path) -> dict[str, dict[str, str]]:
    """Parse a standard SQL DDL file and return a column-schema catalog.

    Handles ``CREATE TABLE`` statements in standard SQL format without Hive-specific
    backtick syntax or ``-- table`` section headers.  Supports Snowflake, Spark SQL,
    PostgreSQL, MySQL, and BigQuery DDL.

    Uses balanced-parenthesis extraction to correctly handle nested ``VARCHAR(50)``,
    ``DEFAULT CURRENT_TIMESTAMP()``, etc.

    Returns
    -------
    dict[str, dict[str, str]]
        ``{TABLE_NAME_UPPER: {col_name_lower: internal_type}}``

        Both the fully-qualified name (``SCHEMA.TABLE``) and the short name
        (``TABLE``) are stored so that name-based matching works regardless of
        whether the ASG node uses a short or fully-qualified table name.
    """
    text = Path(ddl_path).read_text(encoding="utf-8", errors="replace")
    # Strip single-line SQL comments so "--" tokens don't confuse the parser.
    text = re.sub(r"--[^\n]*", "", text)

    catalog: dict[str, dict[str, str]] = {}

    for m in _SQL_CREATE_RE.finditer(text):
        raw_name = m.group(1).strip().strip('`"')
        body_start = m.end()

        # Extract the column-definition block using balanced parenthesis counting.
        depth = 1
        pos = body_start
        while pos < len(text) and depth > 0:
            if text[pos] == "(":
                depth += 1
            elif text[pos] == ")":
                depth -= 1
            pos += 1
        col_block = text[body_start : pos - 1]

        cols: dict[str, str] = {}
        for col_def in _split_ddl_col_block(col_block):
            col_def = col_def.strip()
            if not col_def:
                continue
            parts = col_def.split()
            if not parts:
                continue
            col_candidate = parts[0].strip('`"').upper()
            if col_candidate in _DDL_KEYWORDS:
                continue
            if len(parts) < 2:
                continue
            raw_type = parts[1].lower().split("(")[0]
            col_name = parts[0].strip('`"').lower()
            cols[col_name] = _SQL_TYPE_MAP.get(raw_type, "STRING")

        if not cols:
            continue

        table_upper = raw_name.upper()
        catalog[table_upper] = cols

        # Also index by short name so FQN nodes match short DDL names and vice-versa.
        short = raw_name.split(".")[-1].upper()
        if short != table_upper:
            catalog.setdefault(short, cols)

    return catalog


def _sql_find_matching_ddl(
    node_name: str,
    catalog: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    """Return DDL columns for *node_name*, or ``None`` if no match found.

    Matching order (first hit wins):
    1. Exact match (both FQN or both short).
    2. Short name of the node matches a catalog entry (node is FQN, DDL is short).
    """
    if node_name in catalog:
        return catalog[node_name]
    short = node_name.split(".")[-1]
    if short != node_name and short in catalog:
        return catalog[short]
    return None


def enrich_asg_from_sql_ddl(
    asg_dict: dict,
    ddl_path: str | Path,
) -> int:
    """Enrich an ASG (plain dict / JSON) with column schemas from a standard SQL DDL.

    For each ``data_in`` node whose name matches a DDL table name, upgrades the
    ``inferred_type``, ``source``, and ``confidence`` of **existing** columns that
    appear in the DDL.  New columns are never added — this avoids false positives for
    nodes that select computed or aggregated columns not present in the base table DDL
    (e.g. ``SELECT COUNT(*) AS total_raw FROM …``).

    Parameters
    ----------
    asg_dict:
        The parsed ASG JSON as a plain Python dict (mutated in-place).
    ddl_path:
        Path to a SQL DDL file with ``CREATE TABLE`` statements.

    Returns
    -------
    int
        Number of columns upgraded to ``catalog`` / ``high`` confidence.
    """
    catalog = parse_sql_ddl(ddl_path)
    if not catalog:
        return 0

    upgraded = 0
    for node in asg_dict.get("data_in", []):
        node_name = (node.get("name") or "").upper()
        if not node_name:
            continue

        ddl_cols = _sql_find_matching_ddl(node_name, catalog)
        if ddl_cols is None:
            continue

        for col in node.get("inferred_columns") or []:
            col_lower = (col.get("name") or "").lower()
            if col_lower in ddl_cols:
                col["inferred_type"] = ddl_cols[col_lower]
                col["source"] = "catalog"
                col["confidence"] = "high"
                upgraded += 1

    return upgraded
