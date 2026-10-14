"""comparator.py — single-sink diff for SCOS validation.

Compares a Phase A (source baseline) against a Phase B (migrated) output.
Supports CSV and Parquet formats. The two sides are called *baseline* and
*shadow* — generic comparison terms.

Handles benign differences (column case, whitespace, decimal trailing zeros,
row order without explicit keys) and surfaces real divergences as structured
JSON. Usable in-process via ``compare()`` (harness helpers) and as a CLI
``comparator.py compare`` (the Scala harness shells out to it, looping per
table). Exit codes: 0 = match (or match_with_skips), 1 = diverge / missing
file, 2 = usage or comparison error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Value comparison utilities
# ---------------------------------------------------------------------------


def _normalize_struct(val: str) -> str:
    """Canonicalize a struct / array-of-struct cell for order-independent compare.

    Handles all of: JSON objects ``{"a":1}``, Spark Row repr ``{a=1, b=2}``,
    Python literal repr with single quotes ``{'a': 'b'}`` / ``[{'a': 'b'}]``, and
    JSON/Python **arrays** ``[{...}, {...}]``. The common SCOS migration artifact is
    a struct column serialized as a JSON string (double quotes, sorted keys) vs the
    PySpark baseline's native repr (single quotes / insertion-order keys); these are
    the SAME data and must compare equal deterministically — not depend on a runner
    remembering to ``document-divergence``.

    Returns a canonical ``json.dumps(..., sort_keys=True)`` form when the value
    parses as a dict/list; otherwise returns the value unchanged.
    """
    s = val.strip()
    if not (s.startswith("{") and s.endswith("}")) and not (
        s.startswith("[") and s.endswith("]")
    ):
        return val
    # 1) Strict JSON (handles JSON objects/arrays + nested-key ordering via sort_keys).
    try:
        return json.dumps(json.loads(s), sort_keys=True, default=str)
    except (json.JSONDecodeError, ValueError):
        pass
    # 2) Python literal repr (single quotes, True/False/None, tuples) — e.g. the
    #    PySpark/pandas baseline rendering of a struct or array<struct> column.
    try:
        import ast
        return json.dumps(ast.literal_eval(s), sort_keys=True, default=str)
    except (ValueError, SyntaxError):
        pass
    # 3) Last-resort: Spark Row repr ``{a=1, b=2}`` — sort the comma-split parts.
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1]
        parts = [p.strip() for p in inner.split(",")]
        return "{" + ", ".join(sorted(parts)) + "}"
    return val


def _normalize_collection(val: str) -> Optional[str]:
    """Normalize array/list/ndarray string representations to canonical JSON.

    Converts '[47.123, -122.987]', '[ 47.123 -122.987 ]' (numpy ndarray repr),
    and similar list representations to a sorted-number JSON array string for
    representation-independent comparison. Returns None if not parseable as a
    numeric collection.
    """
    s = val.strip()
    if not (s.startswith("[") and s.endswith("]")):
        return None
    inner = s[1:-1].strip()
    if not inner:
        return "[]"
    # Try JSON parse first (handles comma-separated)
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return json.dumps(parsed)
    except (json.JSONDecodeError, ValueError):
        pass
    # numpy ndarray repr: space-separated numbers like '[ 47.123 -122.987 ]'
    parts = inner.split()
    try:
        nums = [float(p.rstrip(",")) for p in parts if p.rstrip(",")]
        return json.dumps(nums)
    except (ValueError, TypeError):
        return None


def _is_null(value: str) -> bool:
    """Treat empty string and literal 'NULL' as null."""
    return value == "" or value.upper() == "NULL"


def _canon_null(v) -> str:
    """Canonicalize NULL representations for cell comparison."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("null", "nan", "\\n", "n/a", "") or s == "\\N":
        return ""
    return s


def _try_parse_decimal(value: str) -> Optional[Decimal]:
    """Parse a string as Decimal, returning None on failure."""
    if not value or value.upper() == "NULL":
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _try_parse_datetime(value: str) -> Optional[datetime]:
    """Parse ISO 8601 datetime string, stripping Z/UTC offset suffix."""
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1]
    elif s.endswith(("+0000", "-0000")):
        s = s[:-5]
    elif s.endswith(("+00:00", "-00:00")):
        s = s[:-6]
    # Try common ISO formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _cells_equal(
    baseline_val: str, shadow_val: str, *, tolerance: float
) -> Tuple[bool, str]:
    """Compare two cell values with tolerance.

    Returns (is_equal, kind) where kind describes the mismatch type if unequal.
    """
    # NULL handling: both null representations are equivalent
    b_null = _is_null(baseline_val)
    s_null = _is_null(shadow_val)
    if b_null and s_null:
        return True, ""
    if b_null or s_null:
        return False, "null"

    # Exact string match (fast path)
    if baseline_val == shadow_val:
        return True, ""

    # Case-insensitive string match
    if baseline_val.strip().lower() == shadow_val.strip().lower():
        return True, ""

    # Struct / array-of-struct normalization: handles {b=1, a=2} vs {a=2, b=1},
    # single-quote Python repr vs JSON, and array<struct> ([{...}, {...}]). This
    # makes the struct→JSON-string SCOS migration artifact compare equal in every
    # run (deterministic) rather than depending on a manual document-divergence.
    _b, _s = baseline_val.strip(), shadow_val.strip()
    if (_b[:1] in ("{", "[")) and (_s[:1] in ("{", "[")):
        try:
            if _normalize_struct(baseline_val) == _normalize_struct(shadow_val):
                return True, ""
        except Exception:
            pass

    # Collection normalization: reconcile ndarray/list/JSON-array representations.
    # Handles e.g. '[ 47.123 -122.987 ]' (numpy) vs '[47.123, -122.987]' (JSON).
    b_coll = _normalize_collection(baseline_val)
    s_coll = _normalize_collection(shadow_val)
    if b_coll is not None and s_coll is not None:
        if b_coll == s_coll:
            return True, ""
        # Apply numeric tolerance element-wise
        try:
            b_list = json.loads(b_coll)
            s_list = json.loads(s_coll)
            if (isinstance(b_list, list) and isinstance(s_list, list)
                    and len(b_list) == len(s_list)):
                all_close = all(
                    abs(float(a) - float(b)) <= tolerance * max(abs(float(a)), abs(float(b)), 1.0)
                    for a, b in zip(b_list, s_list)
                )
                if all_close:
                    return True, ""
        except (ValueError, TypeError):
            pass

    # Decimal / numeric comparison
    b_dec = _try_parse_decimal(baseline_val)
    s_dec = _try_parse_decimal(shadow_val)
    if b_dec is not None and s_dec is not None:
        # Normalize trailing zeros for exact match
        if b_dec.normalize() == s_dec.normalize():
            return True, ""
        # Tolerance-based comparison
        try:
            b_f = float(b_dec)
            s_f = float(s_dec)
            denom = max(abs(b_f), abs(s_f), 1.0)
            diff = abs(b_f - s_f)
            if diff <= tolerance * denom:
                return True, ""
            # Within order of magnitude but exceeds tolerance
            if diff <= denom:
                return False, "numeric_tol"
        except (OverflowError, ValueError):
            pass
        return False, "value"

    # Timestamp comparison
    b_dt = _try_parse_datetime(baseline_val)
    s_dt = _try_parse_datetime(shadow_val)
    if b_dt is not None and s_dt is not None:
        if b_dt == s_dt:
            return True, ""
        return False, "value"

    # String value difference
    return False, "value"


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------


def _load_csv(path: str) -> Tuple[List[str], List[List[str]]]:
    """Load CSV file, returning (headers_uppercased, rows).

    Each row is a list of string values corresponding to the headers.
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        raw_headers = next(reader)
        headers = [h.strip().upper() for h in raw_headers]
        rows = [row for row in reader]
    return headers, rows


def _load_parquet(path: str) -> Tuple[List[str], List[List[str]], Dict[str, str]]:
    """Load Parquet file, returning (headers_uppercased, rows_as_strings, types_dict)."""
    import pandas as pd
    df = pd.read_parquet(path)
    headers = [c.upper() for c in df.columns]
    types = {c.upper(): str(df[c].dtype) for c in df.columns}
    # itertuples avoids constructing a pandas Series per row (iterrows is 50-100x
    # slower and also coerces dtypes row-wise). _canon_null is still applied per
    # cell, so the canonicalized values are unchanged.
    rows: List[List[str]] = [
        [_canon_null(v) for v in rec]
        for rec in df.itertuples(index=False, name=None)
    ]
    return headers, rows, types


def _is_parquet(path: str) -> bool:
    """Check if a path refers to a Parquet file."""
    return path.lower().endswith(".parquet")


def _parquet_meta(path: str) -> Optional[Tuple[List[str], int]]:
    """Read (uppercased column names, row count) from Parquet metadata WITHOUT
    materializing rows. Handles both a single .parquet file and a Spark output
    directory of part-files. Returns None on any error so callers fall back to
    the full load path."""
    try:
        import pyarrow.dataset as ds
        d = ds.dataset(path)
        return [c.upper() for c in d.schema.names], d.count_rows()
    except Exception:
        return None


def _normalize_sink_name(raw: str) -> str:
    text = str(raw or "").replace("`", "").replace('"', "").strip()
    if not text:
        return ""
    if "://" in text or text.startswith("/"):
        base = os.path.basename(text)
        return base.rsplit(".", 1)[0].upper()
    parts = [part for part in text.split(".") if part]
    if parts:
        return parts[-1].upper()
    return text.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper()


def _load_entrypoint_from_schemas(schemas_dir: str, trial_id: str) -> Optional[dict]:
    try:
        from helpers import load_entrypoint  # harness-local sibling
        return load_entrypoint(schemas_dir, trial_id)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _load_manifest_from_schemas(schemas_dir: str) -> Optional[dict]:
    try:
        with open(os.path.join(schemas_dir, "manifest.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _load_key_columns_from_schemas(
    schemas_dir: str, trial_id: str, table_name: str,
) -> Optional[List[str]]:
    """Extract comparison keys (natural_keys) for a sink from entrypoint schemas."""
    entrypoint = _load_entrypoint_from_schemas(schemas_dir, trial_id)
    if not entrypoint:
        return None

    normalized_table = _normalize_sink_name(table_name)
    for tbl_name, tbl in (entrypoint.get("tables") or {}).items():
        candidates = [tbl_name, tbl.get("original_path", "")]
        if not any(_normalize_sink_name(c) == normalized_table for c in candidates):
            continue
        keys = tbl.get("natural_keys")
        if keys and isinstance(keys, list):
            return [str(k) for k in keys]
    return None


def _load_expected_divergences_from_schemas(
    schemas_dir: str, trial_id: str, sink_name: str,
) -> List[Dict[str, Any]]:
    """Load expected divergences for a trial+sink from manifest.json."""
    manifest = _load_manifest_from_schemas(schemas_dir)
    if not manifest:
        return []
    divs = manifest.get("expected_divergences", {})
    normalized_sink = _normalize_sink_name(sink_name)
    matches: List[Dict[str, Any]] = []
    for key, entries in divs.items():
        if not isinstance(key, str) or "." not in key:
            continue
        key_trial, key_sink = key.split(".", 1)
        if key_trial != trial_id:
            continue
        if _normalize_sink_name(key_sink) == normalized_sink:
            matches.extend(entries or [])
    return matches


# ---------------------------------------------------------------------------
# Comparison engine
# ---------------------------------------------------------------------------


_COMPATIBLE_TYPE_GROUPS = {
    frozenset({"byte", "tinyint", "int8", "short", "smallint", "int16",
               "int", "integer", "int32", "long", "bigint", "int64"}),
    frozenset({"float", "real", "float32", "double", "float64"}),
    frozenset({"string", "varchar", "char", "object"}),
    frozenset({"timestamp", "timestamp_ntz", "timestamp_ltz", "datetime64[ns]"}),
    frozenset({"boolean", "bool"}),
}

# pandas widens int columns containing NaN to float64; treat int↔float as
# compatible so all-NaN baseline columns don't cause spurious type mismatches.
_NAN_WIDENED_COMPAT = frozenset({
    "byte", "tinyint", "int8", "short", "smallint", "int16",
    "int", "integer", "int32", "long", "bigint", "int64",
    "float", "real", "float32", "double", "float64",
})

# Types that serialize through CSV identically to their string form. When the
# baseline records one of these and the shadow records "string" (or vice versa),
# treat as compatible — the cell values are what we actually compare. This
# specifically catches the Phase-A-typed → Phase-B-via-CSV-roundtrip drift
# observed on date/timestamp/decimal columns when the SCOS shadow is captured
# from a CSV-backed table.
_CSV_ROUNDTRIP_TYPES = frozenset({
    "date", "timestamp", "timestamp_ntz", "timestamp_ltz",
    "decimal", "numeric",
})


def _types_compatible(t1: str, t2: str, *, nan_widened: bool = False) -> bool:
    """Check if two Spark type strings are compatible for comparison.

    When nan_widened=True, also treat int↔float as compatible (pandas NaN
    widening: an int column with all-NaN becomes float64 in the baseline).
    """
    t1, t2 = t1.lower().split("(")[0], t2.lower().split("(")[0]
    if t1 == t2:
        return True
    for group in _COMPATIBLE_TYPE_GROUPS:
        if t1 in group and t2 in group:
            return True
    # CSV-roundtrip tolerance: date/timestamp/decimal vs string is a serialization
    # artifact, not a semantic divergence — cell-level comparison still catches
    # real value differences.
    string_aliases = {"string", "varchar", "char"}
    if t1 in _CSV_ROUNDTRIP_TYPES and t2 in string_aliases:
        return True
    if t2 in _CSV_ROUNDTRIP_TYPES and t1 in string_aliases:
        return True
    # NaN-widened: pandas promotes int→float64 when column has NaN values.
    if nan_widened and t1 in _NAN_WIDENED_COMPAT and t2 in _NAN_WIDENED_COMPAT:
        return True
    return False


def _load_types(csv_path: str) -> Optional[Dict[str, str]]:
    """Load types.json sidecar if present next to a data CSV."""
    types_path = csv_path.replace(".csv", ".types.json")
    if not os.path.isfile(types_path):
        return None
    try:
        with open(types_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _is_all_null_col(rows: List[List[str]], col_idx: int) -> bool:
    """Check if a column is entirely null/empty (no real values to type-check)."""
    if not rows:
        return True
    for row in rows:
        val = row[col_idx] if col_idx < len(row) else ""
        if _canon_null(val) != "":
            return False
    return True


def _build_schema_diff(
    baseline_cols: List[str],
    shadow_cols: List[str],
    baseline_types: Optional[Dict[str, str]] = None,
    shadow_types: Optional[Dict[str, str]] = None,
    baseline_rows: Optional[List[List[str]]] = None,
    shadow_rows: Optional[List[List[str]]] = None,
    documented_divergence_cols: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    """Compare column sets (already uppercased) and types. Returns None if identical.

    When baseline_rows/shadow_rows are provided, columns that are all-null on
    one side get relaxed type checking (int↔float compatible due to pandas NaN
    widening).

    When documented_divergence_cols is provided, type mismatches for those
    columns are suppressed (they are accepted via expected-divergence records).
    """
    b_set = set(baseline_cols)
    s_set = set(shadow_cols)
    missing_in_shadow = sorted(b_set - s_set)
    extra_in_shadow = sorted(s_set - b_set)

    type_mismatches: List[Dict[str, str]] = []
    _doc_cols = documented_divergence_cols or set()
    if baseline_types and shadow_types:
        for col in sorted(b_set & s_set):
            if col in _doc_cols:
                continue
            bt = baseline_types.get(col, "")
            st = shadow_types.get(col, "")
            if bt and st and not _types_compatible(bt, st):
                # Check if either side is all-null — relax via nan_widened
                nan_widened = False
                if baseline_rows is not None and col in baseline_cols:
                    idx = baseline_cols.index(col)
                    if _is_all_null_col(baseline_rows, idx):
                        nan_widened = True
                if not nan_widened and shadow_rows is not None and col in shadow_cols:
                    idx = shadow_cols.index(col)
                    if _is_all_null_col(shadow_rows, idx):
                        nan_widened = True
                if nan_widened and _types_compatible(bt, st, nan_widened=True):
                    continue
                type_mismatches.append({
                    "column": col,
                    "baseline_type": bt,
                    "shadow_type": st,
                })

    if not missing_in_shadow and not extra_in_shadow and not type_mismatches:
        return None
    return {
        "missing_in_shadow": missing_in_shadow,
        "extra_in_shadow": extra_in_shadow,
        "type_mismatches": type_mismatches,
    }


def _compare_rows_keyed_multiset(
    baseline_groups: Dict[Tuple, List[List[str]]],
    shadow_groups: Dict[Tuple, List[List[str]]],
    compare_indices: List[int],
    headers: List[str],
    *,
    tolerance: float,
    sample_limit: int,
) -> List[Dict[str, Any]]:
    """Multiset-safe key comparison used when natural_keys are non-unique.

    Groups rows by key on each side, sorts each group's rows, then compares
    element-wise.  Identical duplicate rows on both sides produce no diffs;
    genuine count or value differences are still reported.
    """
    diffs: List[Dict[str, Any]] = []
    shadow_remaining: Dict[Tuple, List[List[str]]] = {k: list(v) for k, v in shadow_groups.items()}
    row_idx = 0
    for key, b_group in baseline_groups.items():
        if len(diffs) >= sample_limit:
            break
        b_sorted = sorted(b_group, key=lambda r: tuple(r))
        s_sorted = sorted(shadow_remaining.pop(key, []), key=lambda r: tuple(r))
        n = min(len(b_sorted), len(s_sorted))
        for i in range(n):
            if len(diffs) >= sample_limit:
                break
            field_diffs = _compare_single_row(b_sorted[i], s_sorted[i], compare_indices, headers, tolerance=tolerance)
            if field_diffs:
                diffs.append({"row_index": row_idx, "key": list(key), "field_diffs": field_diffs})
            row_idx += 1
        # Baseline rows with no shadow counterpart
        for _ in b_sorted[n:]:
            if len(diffs) >= sample_limit:
                break
            diffs.append({
                "row_index": row_idx,
                "key": list(key),
                "field_diffs": [{"col": "_ROW_", "baseline_value": "present", "shadow_value": "missing", "kind": "value"}],
            })
            row_idx += 1
        # Shadow rows with no baseline counterpart
        for _ in s_sorted[n:]:
            if len(diffs) >= sample_limit:
                break
            diffs.append({
                "row_index": -1,
                "key": list(key),
                "field_diffs": [{"col": "_ROW_", "baseline_value": "missing", "shadow_value": "present", "kind": "value"}],
            })
    # Shadow keys absent from baseline
    for key, s_group in shadow_remaining.items():
        for _ in s_group:
            if len(diffs) >= sample_limit:
                break
            diffs.append({
                "row_index": -1,
                "key": list(key),
                "field_diffs": [{"col": "_ROW_", "baseline_value": "missing", "shadow_value": "present", "kind": "value"}],
            })
    return diffs


def _canonical_sort_cell(value, *, tolerance: float) -> str:
    """Canonical token for one cell, used only to build a row sort key.

    Cells that ``_cells_equal`` treats as equal must produce the SAME token so
    equal rows sort together; distinct cells must produce distinct tokens so
    unequal rows sort to aligned positions. Mirrors the equality rules: nulls
    fold together, strings are case/whitespace folded, numbers are rounded to the
    relative ``tolerance`` (so near-equal floats coincide), and struct/array
    reprs are order-normalized.
    """
    s = _canon_null(value)
    if _is_null(s):  # same null predicate the equality path applies post-_canon_null
        return "\x00"  # null: sorts before every real value
    dec = _try_parse_decimal(s)
    if dec is not None:
        f = float(dec)
        if tolerance and tolerance > 0 and f != 0.0:
            # Fixed-bucket rounding to significant figures, whereas `_cells_equal` uses a
            # sliding relative-tolerance window. Two rows equal-within-tolerance whose
            # floats straddle a bucket edge can sort to swapped positions and mis-pair,
            # adding spurious per-column diffs. This is NON-masking (it never hides a real
            # divergence) and rare; acceptable for the tiny mock tables this harness
            # compares. Deliberately not resolved via bipartite matching.
            sig = max(1, int(round(-math.log10(tolerance))))
            f = float(format(f, f".{sig}g"))
        return "\x01N" + repr(f)
    coll = _normalize_collection(s)
    if coll is not None:
        return "\x02C" + coll
    stripped = s.strip()
    if stripped[:1] in ("{", "["):
        return "\x03S" + _normalize_struct(s)
    return "\x04" + stripped.lower()


def _canonical_row_sort_key(row: List[str], *, tolerance: float) -> Tuple[str, ...]:
    """Row sort key: per-cell canonical tokens (see ``_canonical_sort_cell``)."""
    return tuple(_canonical_sort_cell(c, tolerance=tolerance) for c in row)


def _compare_rows_ordered(
    baseline_rows: List[List[str]],
    shadow_rows: List[List[str]],
    headers: List[str],
    *,
    tolerance: float,
    sample_limit: int,
) -> List[Dict[str, Any]]:
    """Order-independent fallback used when no natural key resolves.

    Sorts both sides by a tolerance-aware canonical row key so rows equal within
    ``tolerance`` (and byte/case/struct-equal rows) land at the same position,
    then compares positionally with the per-cell engine (``_compare_single_row``,
    which applies ``tolerance`` and yields per-column ``field_diffs``). Same rows
    in any order match; near-equal floats match; a genuine value difference is
    still reported per column, and surplus rows on either side surface as
    present/missing ``_ROW_`` diffs.
    """
    col_indices = list(range(len(headers)))
    b_sorted = sorted(baseline_rows, key=lambda r: _canonical_row_sort_key(r, tolerance=tolerance))
    s_sorted = sorted(shadow_rows, key=lambda r: _canonical_row_sort_key(r, tolerance=tolerance))
    diffs: List[Dict[str, Any]] = []
    n = min(len(b_sorted), len(s_sorted))
    for i in range(n):
        if len(diffs) >= sample_limit:
            return diffs
        field_diffs = _compare_single_row(b_sorted[i], s_sorted[i], col_indices, headers, tolerance=tolerance)
        if field_diffs:
            diffs.append({"row_index": i, "key": None, "field_diffs": field_diffs})
    # Surplus baseline rows (present in baseline, missing in shadow).
    for i in range(n, len(b_sorted)):
        if len(diffs) >= sample_limit:
            return diffs
        diffs.append({
            "row_index": i,
            "key": None,
            "field_diffs": [{"col": "_ROW_", "baseline_value": "present", "shadow_value": "missing", "kind": "value"}],
        })
    # Surplus shadow rows (present in shadow, missing in baseline).
    for _ in range(n, len(s_sorted)):
        if len(diffs) >= sample_limit:
            return diffs
        diffs.append({
            "row_index": -1,
            "key": None,
            "field_diffs": [{"col": "_ROW_", "baseline_value": "missing", "shadow_value": "present", "kind": "value"}],
        })
    return diffs


def _compare_rows_keyed(
    baseline_rows: List[List[str]],
    shadow_rows: List[List[str]],
    headers: List[str],
    key_columns: List[str],
    *,
    tolerance: float,
    sample_limit: int,
) -> List[Dict[str, Any]]:
    """Key-based row matching. Returns list of row_diff dicts."""
    key_indices = [headers.index(k.upper()) for k in key_columns if k.upper() in headers]
    if not key_indices:
        # Key columns not present in this sink: fall back to an order-independent
        # sorted-positional comparison.
        return _compare_rows_ordered(
            baseline_rows, shadow_rows, headers,
            tolerance=tolerance, sample_limit=sample_limit,
        )

    compare_indices = [i for i in range(len(headers)) if i not in key_indices]

    # Group rows by key (supports both unique and non-unique keys)
    shadow_groups: Dict[Tuple, List[List[str]]] = {}
    for row in shadow_rows:
        key = tuple(row[i] if i < len(row) else "" for i in key_indices)
        shadow_groups.setdefault(key, []).append(row)

    baseline_groups: Dict[Tuple, List[List[str]]] = {}
    for row in baseline_rows:
        key = tuple(row[i] if i < len(row) else "" for i in key_indices)
        baseline_groups.setdefault(key, []).append(row)

    # Detect non-unique keys and fall back to multiset comparison
    non_unique = (
        any(len(v) > 1 for v in baseline_groups.values())
        or any(len(v) > 1 for v in shadow_groups.values())
    )
    if non_unique:
        sys.stderr.write(
            "comparator: warning: natural_keys are non-unique (duplicate key values detected "
            "on at least one side); falling back to per-key multiset comparison.\n"
        )
        return _compare_rows_keyed_multiset(
            baseline_groups, shadow_groups, compare_indices, headers,
            tolerance=tolerance, sample_limit=sample_limit,
        )

    # Unique-key fast path: preserve original pop-based behaviour exactly
    shadow_by_key: Dict[Tuple, List[str]] = {k: v[0] for k, v in shadow_groups.items()}

    diffs: List[Dict[str, Any]] = []
    for row_idx, b_row in enumerate(baseline_rows):
        if len(diffs) >= sample_limit:
            break
        key = tuple(b_row[i] if i < len(b_row) else "" for i in key_indices)
        s_row = shadow_by_key.pop(key, None)
        if s_row is None:
            diffs.append({
                "row_index": row_idx,
                "key": list(key),
                "field_diffs": [{"col": "_ROW_", "baseline_value": "present", "shadow_value": "missing", "kind": "value"}],
            })
            continue
        field_diffs = _compare_single_row(b_row, s_row, compare_indices, headers, tolerance=tolerance)
        if field_diffs:
            diffs.append({
                "row_index": row_idx,
                "key": list(key),
                "field_diffs": field_diffs,
            })

    # Remaining shadow rows not in baseline
    for key in list(shadow_by_key.keys()):
        if len(diffs) >= sample_limit:
            break
        diffs.append({
            "row_index": -1,
            "key": list(key),
            "field_diffs": [{"col": "_ROW_", "baseline_value": "missing", "shadow_value": "present", "kind": "value"}],
        })

    return diffs


def _compare_single_row(
    b_row: List[str],
    s_row: List[str],
    col_indices: List[int],
    headers: List[str],
    *,
    tolerance: float,
) -> List[Dict[str, Any]]:
    """Compare a single pair of rows at specified column indices."""
    field_diffs: List[Dict[str, Any]] = []
    for idx in col_indices:
        b_val = _canon_null(b_row[idx] if idx < len(b_row) else "")
        s_val = _canon_null(s_row[idx] if idx < len(s_row) else "")
        equal, kind = _cells_equal(b_val, s_val, tolerance=tolerance)
        if not equal:
            field_diffs.append({
                "col": headers[idx] if idx < len(headers) else f"COL_{idx}",
                "baseline_value": b_val,
                "shadow_value": s_val,
                "kind": kind,
            })
    return field_diffs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$|^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")


def _classify_divergence(schema_diff, row_diffs: List[Dict], row_count_delta: int) -> str:
    """Classify divergence into a semantic category."""
    has_schema = bool(schema_diff and (schema_diff.get("missing_in_shadow") or schema_diff.get("extra_in_shadow")))
    has_rows = bool(row_diffs)
    if has_schema and not has_rows and row_count_delta == 0:
        return "schema_only"
    if not has_schema and not has_rows and row_count_delta != 0:
        return "row_count_only"
    if not has_rows:
        return "schema_only" if has_schema else "mixed"
    kinds, all_null, all_ntol, all_date = set(), True, True, True
    for rd in row_diffs:
        for fd in rd.get("field_diffs", []):
            k = fd.get("kind", "value")
            kinds.add(k)
            bv, sv = fd.get("baseline_value", ""), fd.get("shadow_value", "")
            if not (_canon_null(bv) == "" or _canon_null(sv) == ""):
                all_null = False
            if k != "numeric_tol":
                all_ntol = False
            if not (_DATE_RE.match(bv) or _DATE_RE.match(sv)):
                all_date = False
    if all_null and kinds <= {"null", "value"}:
        return "null_handling"
    if all_ntol:
        return "decimal_precision"
    if all_date and kinds <= {"value"}:
        return "date_format"
    return "cell_data" if len(kinds) == 1 and "value" in kinds else "mixed"


def compare(
    baseline_path: str,
    shadow_path: str,
    *,
    key_columns: Optional[List[str]] = None,
    row_tolerance: float = 1e-6,
    sample_limit: int = 200,
    ignore_columns: Optional[set] = None,
    key_columns_from_schemas: Optional[str] = None,
    expected_divergences_from_schemas: Optional[str] = None,
    expected_divergences_trial: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare baseline and shadow outputs, returning a structured diff dict.

    Supports CSV and Parquet formats. Mixed formats are a config error.
    Returns a CompareResult dict with keys: result, summary, shape,
    schema_diff, row_diffs, row_count_delta, first_diverging_row,
    skipped_columns. ``divergence_class`` is added only on a cell-level
    ``"diverge"`` — the disjoint-schema short-circuits (both the metadata
    fast-path and the "No shared columns" full-load path) omit it.
    """
    # Handle missing files
    _miss = {"shape": {"baseline": None, "shadow": None}, "schema_diff": None,
             "row_diffs": [], "row_count_delta": 0, "first_diverging_row": None,
             "skipped_columns": []}
    # os.path.exists (not isfile): the PySpark harness normalizes captured
    # outputs to single .parquet files, but the Scala/JVM harness writes each
    # table as a Spark output DIRECTORY (tables/<name>.parquet/ with part-files).
    # pandas/pyarrow read both transparently; isfile() would reject the dir.
    if not os.path.exists(baseline_path):
        return {**_miss, "result": "missing_baseline",
                "summary": f"Baseline file not found: {baseline_path}"}
    if not os.path.exists(shadow_path):
        return {**_miss, "result": "missing_shadow",
                "summary": f"Shadow file not found: {shadow_path}"}

    ignore_cols: set = set(ignore_columns) if ignore_columns else set()

    # Format detection: parquet vs csv
    b_parquet = _is_parquet(baseline_path)
    s_parquet = _is_parquet(shadow_path)
    if b_parquet != s_parquet:
        return {**_miss, "result": "error",
                "summary": "mixed format: one side is parquet and the other is csv"}

    # Fast path: if the two Parquet schemas share NO columns, no cell comparison
    # is possible — decide from metadata (column names + row count) without
    # materializing any rows. Mirrors the "No shared columns" diverge result the
    # full-load path produces below. Falls back to the full load on any error.
    if b_parquet:
        _bm = _parquet_meta(baseline_path)
        _sm = _parquet_meta(shadow_path)
        if _bm is not None and _sm is not None:
            b_names, b_nrows = _bm
            s_names, s_nrows = _sm
            if not (set(b_names) & set(s_names)):
                return {
                    "result": "diverge",
                    "summary": "No shared columns between baseline and shadow",
                    "shape": {"baseline": {"rows": b_nrows, "cols": len(b_names)},
                              "shadow": {"rows": s_nrows, "cols": len(s_names)}},
                    "schema_diff": _build_schema_diff(b_names, s_names, {}, {},
                                                      baseline_rows=[], shadow_rows=[]),
                    "row_diffs": [],
                    "row_count_delta": s_nrows - b_nrows,
                    "first_diverging_row": 0,
                    "skipped_columns": [],
                }

    # Load data and type metadata
    if b_parquet:
        baseline_headers, baseline_rows, baseline_types = _load_parquet(baseline_path)
        shadow_headers, shadow_rows, shadow_types = _load_parquet(shadow_path)
    else:
        baseline_headers, baseline_rows = _load_csv(baseline_path)
        shadow_headers, shadow_rows = _load_csv(shadow_path)
        baseline_types = _load_types(baseline_path)
        shadow_types = _load_types(shadow_path)

    # Resolve key columns from schemas if not explicitly provided
    if not key_columns and key_columns_from_schemas and expected_divergences_trial:
        table_name = Path(baseline_path).stem
        key_columns = _load_key_columns_from_schemas(
            key_columns_from_schemas, expected_divergences_trial, table_name,
        )

    # Resolve expected divergences
    applied_divergences: List[Dict[str, Any]] = []
    schema_divergence_cols: set = set()
    if expected_divergences_from_schemas:
        sink_name = Path(baseline_path).stem
        trial_id = expected_divergences_trial or ""
        exp_divs = _load_expected_divergences_from_schemas(
            expected_divergences_from_schemas, trial_id, sink_name,
        )
        for ed in exp_divs:
            col = ed.get("column", "").upper()
            scope = ed.get("scope", "both")
            if col:
                # Suppress cell-level diffs for data/both scopes
                if scope in ("data", "both"):
                    ignore_cols.add(col)
                # Suppress schema type-mismatch for all scopes — a documented
                # divergence for a column implicitly accepts the type difference
                # that causes the value difference.
                schema_divergence_cols.add(col)
                applied_divergences.append(ed)

    shape = {
        "baseline": {"rows": len(baseline_rows), "cols": len(baseline_headers)},
        "shadow": {"rows": len(shadow_rows), "cols": len(shadow_headers)},
    }

    # Schema comparison (columns + types)
    schema_diff = _build_schema_diff(
        baseline_headers, shadow_headers, baseline_types, shadow_types,
        baseline_rows=baseline_rows, shadow_rows=shadow_rows,
        documented_divergence_cols=schema_divergence_cols or None,
    )

    # If schemas diverge on columns, we still try to compare shared columns
    if schema_diff and (schema_diff["missing_in_shadow"] or schema_diff["extra_in_shadow"]):
        # Find shared columns (in baseline order)
        shadow_set = set(shadow_headers)
        shared_cols = [h for h in baseline_headers if h in shadow_set]
        if not shared_cols:
            return {
                "result": "diverge",
                "summary": "No shared columns between baseline and shadow",
                "shape": shape,
                "schema_diff": schema_diff,
                "row_diffs": [],
                "row_count_delta": len(shadow_rows) - len(baseline_rows),
                "first_diverging_row": 0,
                "skipped_columns": [],
            }
        # Remap rows to shared columns
        b_col_map = [baseline_headers.index(c) for c in shared_cols]
        s_col_map = [shadow_headers.index(c) for c in shared_cols]
        baseline_rows = [[row[i] if i < len(row) else "" for i in b_col_map] for row in baseline_rows]
        shadow_rows = [[row[i] if i < len(row) else "" for i in s_col_map] for row in shadow_rows]
        headers = shared_cols
    else:
        # Reorder shadow columns to match baseline order if needed
        if shadow_headers != baseline_headers and set(shadow_headers) == set(baseline_headers):
            reorder = [shadow_headers.index(h) for h in baseline_headers]
            shadow_rows = [[row[i] if i < len(row) else "" for i in reorder] for row in shadow_rows]
        headers = baseline_headers

    # Filter out documented-divergence columns (including expected_divergences)
    skipped_columns: List[str] = []
    if ignore_cols:
        skipped_columns = [h for h in headers if h in ignore_cols]
        keep = [i for i, h in enumerate(headers) if h not in ignore_cols]
        headers = [headers[i] for i in keep]
        baseline_rows = [[row[i] if i < len(row) else "" for i in keep] for row in baseline_rows]
        shadow_rows = [[row[i] if i < len(row) else "" for i in keep] for row in shadow_rows]

    # Cell comparison (full row-by-row). The harness only ever does cell-level
    # equivalence; shape/schema divergences are folded into the result below.
    row_count_delta = len(shadow_rows) - len(baseline_rows)

    if key_columns:
        row_diffs = _compare_rows_keyed(
            baseline_rows, shadow_rows, headers, key_columns,
            tolerance=row_tolerance, sample_limit=sample_limit,
        )
    else:
        # No natural key: sort both sides by a tolerance-aware canonical row key
        # and compare positionally, so identical rows in a different order (and
        # near-equal floats) match, while genuine value changes are still reported
        # per column and added/dropped rows surface as present/missing rows.
        row_diffs = _compare_rows_ordered(
            baseline_rows, shadow_rows, headers,
            tolerance=row_tolerance, sample_limit=sample_limit,
        )

    # Synthetic-diff suppression. Real ignored columns are dropped from `headers`
    # before comparison, but the synthetic `_ROW_` (present/missing rows) field_diffs
    # are appended afterward and bypass that filter. Apply ignore_cols to them here so
    # `document-divergence --column _ROW_` suppresses both the per-row diffs and the
    # row-count delta (otherwise documenting them has no effect).
    if ignore_cols:
        suppressed: set = set()
        filtered_diffs: List[Dict[str, Any]] = []
        for rd in row_diffs:
            kept = []
            for fd in rd.get("field_diffs", []):
                col = fd.get("col")
                if col in ignore_cols:
                    suppressed.add(col)
                    continue
                kept.append(fd)
            if kept:
                filtered_diffs.append({**rd, "field_diffs": kept})
        row_diffs = filtered_diffs
        if row_count_delta != 0 and "_ROW_" in ignore_cols:
            suppressed.add("_ROW_")
            row_count_delta = 0
        for col in sorted(suppressed):
            if col not in skipped_columns:
                skipped_columns.append(col)

    # Determine result
    truncated = len(row_diffs) >= sample_limit
    has_divergence = bool(row_diffs) or bool(schema_diff) or row_count_delta != 0

    first_diverging: Optional[int] = None
    if row_diffs:
        first_diverging = row_diffs[0]["row_index"]

    if has_divergence:
        n_field_diffs = sum(len(d["field_diffs"]) for d in row_diffs)
        type_mismatches = schema_diff.get("type_mismatches", []) if schema_diff else []
        summary = (
            f"{n_field_diffs} cell difference(s) across {len(row_diffs)} row(s)"
            f"{'; row count delta=' + str(row_count_delta) if row_count_delta else ''}"
            f"{'; schema differences present' if schema_diff else ''}"
            f"{'; ' + str(len(type_mismatches)) + ' type mismatch(es)' if type_mismatches else ''}"
        )
        result = "diverge"
    elif skipped_columns:
        summary = (
            f"Match with skips: {len(baseline_rows)} rows, "
            f"{len(headers)} compared columns, "
            f"{len(skipped_columns)} skipped ({', '.join(skipped_columns)})"
        )
        result = "match_with_skips"
    else:
        summary = f"Match: {len(baseline_rows)} rows, {len(headers)} columns"
        result = "match"

    output: Dict[str, Any] = {
        "result": result,
        "summary": summary,
        "shape": shape,
        "schema_diff": schema_diff,
        "row_diffs": row_diffs,
        "row_count_delta": row_count_delta,
        "first_diverging_row": first_diverging,
        "skipped_columns": skipped_columns,
    }
    if result == "diverge":
        output["divergence_class"] = _classify_divergence(schema_diff, row_diffs, row_count_delta)
    if applied_divergences:
        output["expected_divergences_applied"] = applied_divergences
    if truncated:
        output["truncated"] = True
    return output


# ---------------------------------------------------------------------------
# CLI
#
# Retained for the Scala harness (validate-spark-scala-to-snowpark-connect),
# which shells out to `comparator.py compare` per table via $VALIDATOR_SCRIPTS.
# The PySpark harness calls compare() in-process. No tiers: comparison is always
# cell-level.
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comparator.py",
        description="Compare baseline and shadow sink outputs, emit structured diff JSON.",
    )
    sub = parser.add_subparsers(dest="command")

    cmp = sub.add_parser("compare", help="Run a single-sink comparison")
    cmp.add_argument("--baseline", required=True, help="Path to baseline CSV/Parquet")
    cmp.add_argument("--shadow", required=True, help="Path to shadow CSV/Parquet")
    cmp.add_argument("--output", required=True, help="Path to write diff JSON")
    cmp.add_argument(
        "--key-columns", default=None,
        help="Comma-separated key columns for row matching (optional)",
    )
    cmp.add_argument(
        "--row-tolerance", type=float, default=1e-6,
        help="Relative numeric tolerance (default: 1e-6)",
    )
    cmp.add_argument(
        "--sample-limit", type=int, default=200,
        help="Max row diffs to include in output (default: 200)",
    )
    cmp.add_argument(
        "--ignore-columns", default=None,
        help="Comma-separated column names to skip (documented divergences)",
    )
    cmp.add_argument(
        "--key-columns-from-schemas", default=None,
        help="Path to shared/schemas for automatic key column resolution",
    )
    cmp.add_argument(
        "--expected-divergences-from-schemas", default=None,
        help="Path to shared/schemas for expected divergence filtering",
    )
    cmp.add_argument(
        "--expected-divergences-trial", default=None,
        help="Trial ID for expected divergence lookup",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        return 2

    if args.command == "compare":
        key_cols = None
        if args.key_columns:
            key_cols = [k.strip() for k in args.key_columns.split(",")]
        ignore_cols = None
        if args.ignore_columns:
            ignore_cols = {c.strip().upper() for c in args.ignore_columns.split(",")}

        try:
            result = compare(
                baseline_path=args.baseline,
                shadow_path=args.shadow,
                key_columns=key_cols,
                row_tolerance=args.row_tolerance,
                sample_limit=args.sample_limit,
                ignore_columns=ignore_cols,
                key_columns_from_schemas=args.key_columns_from_schemas,
                expected_divergences_from_schemas=args.expected_divergences_from_schemas,
                expected_divergences_trial=args.expected_divergences_trial,
            )
        except Exception as exc:
            sys.stderr.write(f"comparator: error: {type(exc).__name__}: {exc}\n")
            return 2

        # Write output JSON
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)

        # Exit code: 0 = match, 1 = divergence / missing file, 2 = error
        if result["result"] in ("match", "match_with_skips"):
            return 0
        elif result["result"] in ("diverge", "missing_baseline", "missing_shadow"):
            return 1
        else:
            return 2

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
