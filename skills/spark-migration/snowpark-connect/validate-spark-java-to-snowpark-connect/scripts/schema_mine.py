#!/usr/bin/env python3
"""Java schema-mining: source (or analysis.json) -> the PySpark ``schemas/`` layout.

Primary path (PySpark parity)::

    schema_mine.py <source_root> --out <schemas_dir>
    # or: schema_mine.py --conv-root <conv>   # uses Validation/source

Internally: ``scos-analyze-java.jar`` -> ``ast_facts.json`` -> ``ast_to_analysis``
(survey + promote all + deep) -> write ``Validation/shared/schemas/``.

Legacy path (still supported)::

    schema_mine.py --conv-root <conv> --from-analysis
    # reads existing Validation/shared/analysis.json only

Downstream, the *unchanged* canonical PySpark scripts consume ``schemas/``:
  - ``datagen.py schemas/ mock_data``           -> typed mocks
  - ``scos_state.py provision --conv-root ...`` -> golden Snowflake schemas

Mapping:
  external_sources -> read tables (mock_file + COPY; non-tabular -> staged file)
  sinks            -> empty write tables (DML lands here)
  intermediate_tables -> empty write tables in each reader/writer entrypoint

Type inference (Fix #3):
  When a column has type ``string`` (the default produced by the LLM analyzer),
  ``schema_mine.py`` consults two sources of evidence:
  1. ``Validation/shared/ast_facts.json`` (from ``scos-analyze-java.jar``): column names
     referenced across all Java source files.  Not expression-aware, but good for
     name-based heuristics.
  2. Column name suffix/pattern rules (see ``_infer_type_from_name``).

  Only ``string`` columns are upgraded — columns already typed by the LLM
  (``integer``, ``double``, ``date``, etc.) are never downgraded.

Java-only fields are written onto each entrypoint ``_meta.json``:
  ``entrypoint_class``, ``entrypoint_method``, ``cli_args``, ``weight``.

NOTE: ``seed_strategy=from_source_join`` ``seed_sql`` is NOT applied here (the
default provisioner has no server-side seed step); such intermediates are created
empty. The Phase A vs Phase B comparison stays valid, just less data-exercising
for that table.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_TABLE_UNSAFE_RE = re.compile(r'[/\\:*?"<>|\s]')
_LOC_WEIGHT_DIVISOR = 50
_WEIGHT_LABELS = {"critical": 30, "high": 20, "medium": 10, "low": 5}

# ---------------------------------------------------------------------------
# Dynamic class-load site detection (opt-in: detect_dynamic_imports=True)
# Java parity for PySpark schema_mine._find_dynamic_import_sites.
# Detects Class.forName / classLoader.loadClass patterns where the class name
# traces back to a config subscript, surfacing config-driven pipeline dispatch.
# Uses a lightweight regex scan of .java source files.
# ---------------------------------------------------------------------------
_FORNAME_RE = re.compile(
    r'(?:Class\.forName|classLoader\.loadClass|loadClass)\s*\(\s*'
    r'(?P<expr>[^)]{1,120})\)',
)
_CONFIG_KEY_RE = re.compile(r'(?:\.get)?\("([^"]{1,60})"\)')


def _find_dynamic_class_loads(source_root: Path) -> list[dict]:
    """Scan *.java files for Class.forName(config("KEY")) patterns.

    Returns list of {file, line, kind, config_key, raw_expr}.
    """
    sites: list[dict] = []
    for p in sorted(source_root.rglob("*.java")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = _FORNAME_RE.search(line)
            if not m:
                continue
            expr = m.group("expr").strip()
            key_m = _CONFIG_KEY_RE.search(expr)
            sites.append({
                "file": str(p.relative_to(source_root)),
                "line": i,
                "kind": "class_forname",
                "config_key": key_m.group(1) if key_m else None,
                "raw_expr": expr,
            })
    return sites

# ---------------------------------------------------------------------------
# Type inference from column name (Fix #3)
# Applied only when the column type is "string" (LLM default / unknown).
# Rules are ordered: first match wins.  Patterns are case-insensitive suffix/
# full-name checks so they're cheap and safe for goldset workloads.
# ---------------------------------------------------------------------------

_TYPE_SUFFIX_RULES: list = [
    # Timestamps / dates
    (re.compile(r"_ts$|_timestamp$|_at$|_time$", re.I), "timestamp"),
    (re.compile(r"_date$|_dt$|_day$", re.I), "date"),
    # Numeric IDs that are commonly longs in JVM-land
    (re.compile(r"_id$", re.I), "long"),
    # Rank / row-number window columns
    (re.compile(r"^rn$|^rank$|^row_num(ber)?$|^seq$|^sequence$", re.I), "long"),
    # Counts
    (re.compile(r"^count$|_count$|_cnt$|^num_|_num$", re.I), "long"),
    # Monetary / scored quantities
    (re.compile(r"_amount$|_amt$|_price$|_cost$|_revenue$|_total$|_value$", re.I), "double"),
    # Scores / ratios / rates
    (re.compile(r"_score$|_rate$|_ratio$|_pct$|_percent$|_fraction$", re.I), "double"),
    # Physical measurements (temperature in Celsius, distances, weights)
    (re.compile(r"_c$|_f$|_k$|_celsius$|_fahrenheit$", re.I), "double"),
    (re.compile(r"_deg$|_degrees$|_km$|_miles$|_meters?$|_kg$|_lbs?$", re.I), "double"),
    # Duration / intervals
    (re.compile(r"_minutes?$|_seconds?$|_hours?$|_days?$|_duration$|_elapsed$", re.I), "double"),
    # Penalties / corrections (often float arithmetic)
    (re.compile(r"_penalty$|_adjustment$|_correction$|_delta$|_diff$", re.I), "double"),
    # Latitude / longitude
    (re.compile(r"_lat$|_lon$|_lng$|latitude$|longitude$", re.I), "double"),
    # Flags / indicators (boolean)
    (re.compile(r"^is_|^has_|^flag_|_flag$|_indicator$|_enabled$|_active$", re.I), "boolean"),
    # Zones / numbers that are clearly integer (zone_num, zone_span)
    (re.compile(r"_num$|_number$|_zone$|_level$|_tier$", re.I), "long"),
]

_STRING_TYPES = {"string", "varchar", "text", "str"}


def _infer_type_from_name(col_name: str) -> str | None:
    """Return a Spark type hint for *col_name* based on suffix/pattern rules.

    Returns ``None`` when no pattern matches (caller keeps the existing type).
    Only applied when the existing column type is in ``_STRING_TYPES``.
    """
    for pattern, inferred_type in _TYPE_SUFFIX_RULES:
        if pattern.search(col_name):
            return inferred_type
    return None


def _upgrade_columns(columns: list, ast_col_names: set) -> list:
    """Apply type inference to a column list.

    ``ast_col_names`` is the union of all column_refs across ast_facts.json —
    used only to confirm the column appears in actual source code (avoids
    upgrading phantom columns that were hallucinated by the LLM analyzer).
    When ast_facts is absent, ``ast_col_names`` is empty and the check is skipped.
    """
    out = []
    for col in columns:
        if not isinstance(col, dict):
            out.append(col)
            continue
        col_type = (col.get("type") or "string").lower()
        if col_type in _STRING_TYPES:
            name = col.get("name") or col.get("column") or ""
            # Only upgrade if the column appears in AST facts (or facts absent)
            if not ast_col_names or name.lower() in ast_col_names:
                inferred = _infer_type_from_name(name)
                if inferred:
                    col = {**col, "type": inferred}
        out.append(col)
    return out


def _load_ast_col_names(conv_root: Path) -> set:
    """Return a lowercased set of all column_refs from ast_facts.json, or empty set."""
    ast_path = conv_root / "Validation" / "shared" / "ast_facts.json"
    if not ast_path.is_file():
        return set()
    try:
        facts = json.loads(ast_path.read_text(encoding="utf-8"))
        names: set = set()
        for f in facts.get("files") or []:
            for col in f.get("column_refs") or []:
                if isinstance(col, str):
                    names.add(col.lower())
        return names
    except (ValueError, OSError):
        return set()


def _table_filename(key: str, used: set) -> str:
    """Return a filesystem-safe filename stem for a table key, unique within *used*.

    Truncates to 80 characters so the full path never exceeds the macOS 255-byte
    filename limit even when the conversion root is deep.
    """
    safe = _TABLE_UNSAFE_RE.sub("_", key)
    if not safe:
        safe = "_table"
    safe = safe[:80]  # guard against over-long original_path descriptions
    candidate = safe
    suffix = 2
    while candidate in used:
        candidate = f"{safe[:76]}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _die(code: int, msg: str) -> None:
    print(f"[schema_mine.py] error: {msg}", file=sys.stderr)
    sys.exit(code)


def _bare_table_name(raw: str) -> str:
    """Last dotted segment, sans quotes (mirrors provision._bare_table_name)."""
    if not raw:
        return ""
    name = str(raw).strip().strip('"').strip("`")
    if "." in name:
        name = name.split(".")[-1]
    return name.strip().strip('"').strip("`")


def _resolve_schema(schema_field, schemas_cache: dict):
    """Resolve an analysis schema field to a column list.

    Handles inline ``[{name,type,...}]``; ``{"$ref": "schemas.json#/external_sources/<k>"}``;
    and bare string keys into schemas.json's ``external_sources``.
    """
    if isinstance(schema_field, list):
        return schema_field
    if isinstance(schema_field, dict) and "$ref" in schema_field:
        ref = schema_field["$ref"]
        if isinstance(ref, str) and ref.startswith("schemas.json#/"):
            node: object = schemas_cache
            for seg in ref[len("schemas.json#/"):].split("/"):
                if isinstance(node, dict):
                    node = node.get(seg)
            if isinstance(node, list):
                return node
    if isinstance(schema_field, str):
        node = (schemas_cache.get("external_sources") or {}).get(schema_field)
        if isinstance(node, list):
            return node
    return []


def _resolve_catalog(items, catalog: dict):
    """Expand string-id references against a top-level catalog; pass dicts through."""
    out = []
    for it in items or []:
        if isinstance(it, str):
            if it in catalog:
                out.append(catalog[it])
        elif isinstance(it, dict):
            out.append(it)
    return out


def _apply_cross_ep_schema_inheritance(ep_out: list[dict]) -> None:
    """Copy producer sink columns onto consumer read tables (PySpark parity).

    Mutates ``ep_out`` in place. Matches on bare table name (last dotted segment
    of ``original_path`` or table key). Does not imply runtime data reuse —
    each EP still gets its own mock seed.
    """
    sink_owner: dict[str, tuple[str, list]] = {}
    for ep in ep_out:
        eid = ep.get("id") or ""
        for tname, tbl in (ep.get("tables") or {}).items():
            if not isinstance(tbl, dict):
                continue
            access = (tbl.get("access") or "").lower()
            if access not in ("write", "readwrite"):
                continue
            cols = tbl.get("columns") or []
            if not cols:
                continue
            bare = (_bare_table_name(tbl.get("original_path") or "")
                    or _bare_table_name(tname) or str(tname)).lower()
            if bare:
                sink_owner.setdefault(bare, (eid, cols))

    for ep in ep_out:
        for tname, tbl in (ep.get("tables") or {}).items():
            if not isinstance(tbl, dict):
                continue
            access = (tbl.get("access") or "").lower()
            if access not in ("read", "readwrite"):
                continue
            bare = (_bare_table_name(tbl.get("original_path") or "")
                    or _bare_table_name(tname) or str(tname)).lower()
            owner = sink_owner.get(bare)
            if not owner:
                continue
            _writer_ep, cols = owner
            if _writer_ep == ep.get("id"):
                continue  # same EP readwrite — already has write cols
            have = {
                (c.get("name") or "").lower()
                for c in (tbl.get("columns") or [])
                if isinstance(c, dict)
            }
            for col in cols:
                if not isinstance(col, dict) or not col.get("name"):
                    continue
                if col["name"].lower() not in have:
                    tbl.setdefault("columns", []).append({
                        **{k: v for k, v in col.items()
                           if k in ("name", "type", "nullable", "values")},
                        "origin": "intermediate_sink",
                    })
                    have.add(col["name"].lower())
            if tbl.get("llm_todo") and "no column" in (tbl.get("llm_todo") or "").lower():
                tbl.pop("llm_todo", None)


def _normalize_weight(raw) -> int:
    if raw is None:
        return 0
    if isinstance(raw, str):
        return _WEIGHT_LABELS.get(raw.lower().strip(), 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _ep_weight(ep_entry: dict, ep_src: dict) -> tuple[int, dict]:
    """PySpark-parity weight: 1 + 2*reads + writes + loc//50. Numeric only."""
    tables = ep_entry.get("tables") or {}
    n_read = sum(
        1 for t in tables.values()
        if isinstance(t, dict) and t.get("access") in ("read", "readwrite")
    )
    n_write = sum(
        1 for t in tables.values()
        if isinstance(t, dict) and t.get("access") in ("write", "readwrite")
    )
    loc = 0
    explicit = _normalize_weight(ep_src.get("weight"))
    # Prefer explicit numeric/label weight when the agent already set one and
    # tables are still empty (pre-deep); otherwise recompute from tables.
    if explicit and not tables:
        breakdown = {"explicit": explicit, "n_read_tables": 0, "n_write_tables": 0, "loc": 0}
        return explicit, breakdown
    weight = 1 + 2 * n_read + n_write + (loc // _LOC_WEIGHT_DIVISOR)
    if explicit and explicit > weight:
        weight = explicit
    return weight, {
        "n_read_tables": n_read,
        "n_write_tables": n_write,
        "loc": loc,
        "loc_weight": loc // _LOC_WEIGHT_DIVISOR,
    }


def _table_entry(obj, access, columns):
    raw = next((obj.get(k) for k in ("original_path", "original_target", "name", "id")
                if obj.get(k)), "")
    # Bare table name first (matches datagen's _canon: bare lowercased table name),
    # falling back to id/name when original_path has no usable segment.
    key = _bare_table_name(raw) or str(obj.get("id") or obj.get("name") or "tbl")
    entry: dict = {
        "access": access,
        "category": obj.get("category", "table"),
        "relational": True,
        "columns": columns,
        "reader_options": obj.get("reader_options") or {},
        "original_path": raw or str(obj.get("id") or obj.get("name") or key),
    }
    # For table-category sources, derive declared_table_name from the last segment
    # of original_path when it is a concrete identifier (not a placeholder like
    # "<dynamic_table_1>"). This is the name the harness's _declared_table_name
    # helper would derive, but writing it explicitly here avoids a second lookup
    # and makes the schema files self-documenting.
    # E.g. "catalog.schema.freight_estimate" -> "freight_estimate"
    _orig = entry.get("original_path", "")
    if (
        obj.get("category", "table") in ("table", "jdbc", "snowflake")
        and _orig
        and "<" not in _orig
        and "://" not in _orig
        and not _orig.startswith("/")
    ):
        _bare = _bare_table_name(_orig)
        if _bare and re.match(r"^[A-Za-z0-9_$]+$", _bare):
            entry["declared_table_name"] = _bare
    # For relational read tables: always set the canonical datagen mock_file name so
    # provision (which reads schemas/ from disk via load_entrypoint) finds the file that
    # datagen actually generates.  datagen's _materialize_fmt + _ext_for logic:
    #   - category "file": ext follows source_format (csv->csv, json->json, text->txt, else parquet)
    #   - category "table"/"connector": always parquet (regardless of format field)
    # datagen's _canon: bare lowercased table name — must match key.lower().
    if access == "read":
        _category = obj.get("category", "table")
        _fmt = (obj.get("format") or "").lower()
        if _category == "file":
            if _fmt in ("csv", "tsv"):
                _ext = "csv"
            elif _fmt in ("json", "jsonl", "ndjson"):
                _ext = "json"
            elif _fmt == "text":
                _ext = "txt"
            elif _fmt == "avro":
                _ext = "avro"
            else:
                _ext = "parquet"
        else:
            _ext = "parquet"
        # Always derive the canonical name (key.lower().ext) rather than honoring
        # any inbound "mock_file" hint — datagen's _canon matching (bare lowercased
        # table name) requires this to stay in sync with what datagen actually writes.
        entry["mock_file"] = f"{key.lower()}.{_ext}"
    return key, entry


def analysis_to_schemas(
    conv_root: Path,
    detect_dynamic_imports: bool = False,
    schemas_dir_override: Path | None = None,
) -> dict:
    """Convert analysis.json -> Validation/shared/schemas/. Returns a small summary."""
    shared = conv_root / "Validation" / "shared"
    analysis_path = shared / "analysis.json"
    if not analysis_path.is_file():
        _die(2, f"analysis.json not found: {analysis_path}")
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        _die(2, f"cannot parse analysis.json: {e}")

    schemas_json = shared / "schemas.json"
    schemas_cache: dict = {}
    if schemas_json.is_file():
        try:
            schemas_cache = json.loads(schemas_json.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            schemas_cache = {}

    # Load AST column names for type-inference cross-check (Fix #3).
    # If ast_facts.json is absent the set is empty and _upgrade_columns applies
    # name-based rules unconditionally (still safe — only upgrades "string").
    ast_col_names = _load_ast_col_names(conv_root)

    def _resolve_typed(schema_field) -> list:
        """Resolve schema field and upgrade string-typed columns from name heuristics."""
        return _upgrade_columns(_resolve_schema(schema_field, schemas_cache), ast_col_names)

    src_catalog = {s["id"]: s for s in (analysis.get("external_sources") or [])
                   if isinstance(s, dict) and s.get("id")}
    # Accept both "sinks" (legacy key) and "external_sinks" (data-synthesizer output
    # key, if a future Java agent adopts the same split as Scala/PySpark) so neither
    # naming silently drops sinks.
    _global_sinks = list(analysis.get("sinks") or []) + list(analysis.get("external_sinks") or [])
    sink_catalog = {s["id"]: s for s in _global_sinks
                    if isinstance(s, dict) and s.get("id")}

    entrypoints = analysis.get("entrypoints", []) or []
    ep_ids = [ep.get("id") for ep in entrypoints if ep.get("id")]
    ep_out: list = []

    for ep in entrypoints:
        ep_id = ep.get("id")
        if not ep_id:
            continue
        tables: dict = {}
        for src in _resolve_catalog(ep.get("external_sources"), src_catalog):
            cols = _resolve_typed(src.get("schema"))
            if cols:
                k, v = _table_entry(src, "read", cols)
                tables[k] = v
            elif src.get("mock_file"):  # non-tabular document/file -> stage only
                raw = src.get("original_path") or src.get("name") or src.get("id") or "doc"
                tables[str(src.get("id") or src.get("name") or raw)] = {
                    "access": "read", "category": src.get("category", "file"),
                    "relational": False, "mock_file": src.get("mock_file"),
                    "columns": [], "reader_options": src.get("reader_options") or {},
                    "original_path": raw,
                }
        _ep_sinks = list(ep.get("sinks") or []) + list(ep.get("external_sinks") or [])
        for sink in _resolve_catalog(_ep_sinks, sink_catalog):
            kind = (sink.get("kind") or "table").strip().lower()
            cols = _resolve_typed(sink.get("schema"))
            allow_empty = sink.get("allow_empty") or sink.get("allowEmpty")
            if kind in ("", "table"):
                # Table sinks need typed columns for empty pre-create / compare.
                if cols:
                    k, v = _table_entry(sink, "write", cols)
                    if allow_empty:
                        v["allow_empty"] = str(allow_empty).strip()
                    tables[k] = v
                continue
            # Non-table sinks (file / excel / mongo / blob / parquet path): keep them
            # so Phase B stage capture and allow_empty validation see the contract
            # (PySpark parity — do NOT drop non-table sinks).
            raw = (
                sink.get("original_target")
                or sink.get("original_path")
                or sink.get("name")
                or sink.get("id")
                or "sink"
            )
            key = str(sink.get("id") or sink.get("name") or raw)
            cat = "file" if kind in ("file", "parquet", "csv", "json", "orc", "avro", "text") else kind
            entry: dict = {
                "access": "write",
                "category": cat,
                "relational": bool(cols),
                "columns": cols or [],
                "format": sink.get("format") or (kind if kind != "file" else "parquet"),
                "original_path": raw,
                "reader_options": sink.get("reader_options") or {},
            }
            if allow_empty:
                entry["allow_empty"] = str(allow_empty).strip()
            tables[key] = entry
        ep_entry: dict = {
            "id": ep_id,
            "path": ep.get("path", ep_id),
            "run_mode": ep.get("run_mode", "script"),
            "import_roots": ep.get("import_roots", ["src/main/java"]),
            "entrypoint_kwargs": ep.get("entrypoint_kwargs", {}),
            "tables": tables,
            "source_runtime": ep.get("source_runtime") or "spark",
        }
        # pass through optional harness fields when present (Java + shared)
        for _opt in (
            "entrypoint_callable", "cli_args", "entrypoint_class",
            "entrypoint_method", "widget_env_vars", "joins", "llm_todo",
            "unsupported_constructs", "mock_data_dir",
        ):
            if ep.get(_opt) is not None:
                ep_entry[_opt] = ep[_opt]
        weight, breakdown = _ep_weight(ep_entry, ep)
        ep_entry["weight"] = weight
        ep_entry["weight_breakdown"] = breakdown
        ep_out.append(ep_entry)

    # Cross-entrypoint schema inheritance (PySpark schema_mine parity): a table
    # written by one EP and read by another inherits the producer's columns.
    _apply_cross_ep_schema_inheritance(ep_out)

    # intermediate_tables -> empty write tables in each reader/writer entrypoint
    for entry in (analysis.get("intermediate_tables") or []):
        cols = _resolve_typed(entry.get("schema"))
        name = entry.get("name", "")
        if not cols or not name:
            continue
        tname = _bare_table_name(name) or name.lower().replace(".", "_")
        targets = [e for e in ((entry.get("reader_entrypoint_ids") or [])
                               + (entry.get("consumer_entrypoint_ids") or [])
                               + [entry.get("writer_entrypoint_id")]) if e in ep_ids]
        if not targets:
            targets = ep_ids
        for epd in ep_out:
            if epd["id"] in targets:
                mid_entry = {
                    "access": "write", "category": "table", "relational": True,
                    "columns": cols, "reader_options": {},
                    "original_path": name,
                    "intermediate": True,
                }
                if entry.get("seed_strategy"):
                    mid_entry["seed_strategy"] = entry["seed_strategy"]
                if entry.get("allow_empty") or entry.get("allowEmpty"):
                    mid_entry["allow_empty"] = str(
                        entry.get("allow_empty") or entry.get("allowEmpty")
                    ).strip()
                epd["tables"].setdefault(tname, mid_entry)

    # Recompute weights after intermediate merges (write count may change).
    for epd in ep_out:
        w, bd = _ep_weight(epd, epd)
        epd["weight"] = w
        epd["weight_breakdown"] = bd

    schemas_dir = shared / "schemas"
    if schemas_dir_override is not None:
        schemas_dir = Path(schemas_dir_override)
    schemas_dir.mkdir(parents=True, exist_ok=True)
    open_todos = 0
    for e in ep_out:
        if e.get("llm_todo"):
            open_todos += 1
        for t in (e.get("tables") or {}).values():
            if isinstance(t, dict) and t.get("llm_todo"):
                open_todos += 1
    manifest = {
        "root": analysis.get("root") or str(conv_root / "Validation" / "source"),
        "complete": open_todos == 0 and bool(analysis.get("complete", False)),
        "summary": {
            "n_entrypoints": len(ep_out),
            "n_tables": sum(len(e.get("tables") or {}) for e in ep_out),
            "open_todos": open_todos,
            "build_tool": analysis.get("build_tool"),
            "source_roots": analysis.get("source_roots") or [],
        },
        "expected_divergences": analysis.get("expected_divergences") or {},
        "entrypoints": [{
            "id": e["id"],
            "path": e["path"],
            "dir": f"entrypoints/{e['id']}",
            "source_runtime": e.get("source_runtime") or "spark",
            "weight": e.get("weight"),
            "weight_breakdown": e.get("weight_breakdown"),
        } for e in ep_out],
    }
    (schemas_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    total_tables = 0
    for e in ep_out:
        ep_dir = schemas_dir / "entrypoints" / e["id"]
        (ep_dir / "tables").mkdir(parents=True, exist_ok=True)
        tables = e.pop("tables")
        total_tables += len(tables)
        used: set = set()
        for k, v in tables.items():
            entry = dict(v)
            entry["_table_key"] = k
            (ep_dir / "tables" / f"{_table_filename(k, used)}.json").write_text(
                json.dumps(entry, indent=2, default=str) + "\n", encoding="utf-8")
        (ep_dir / "_meta.json").write_text(
            json.dumps(e, indent=2, default=str) + "\n", encoding="utf-8")
    ast_hint = f" (ast_facts: {len(ast_col_names)} col names)" if ast_col_names else ""

    dynamic_loads: list[dict] = []
    if detect_dynamic_imports:
        source_root = conv_root / "Validation" / "source"
        if source_root.is_dir():
            dynamic_loads = _find_dynamic_class_loads(source_root)
        if dynamic_loads:
            (schemas_dir / "dynamic_class_loads.json").write_text(
                json.dumps(dynamic_loads, indent=2) + "\n", encoding="utf-8")

    # Persist Java-only top-level metadata for build-doctor / report.
    meta_sidecar = {
        "build_tool": analysis.get("build_tool"),
        "source_roots": analysis.get("source_roots") or [],
        "jar_path": analysis.get("jar_path"),
        "migration_issues": analysis.get("migration_issues") or [],
    }
    (schemas_dir / "java_meta.json").write_text(
        json.dumps(meta_sidecar, indent=2, default=str) + "\n", encoding="utf-8")

    # Layer B2: catalog project *.sql template files (PySpark sql_files.json parity).
    source_root = conv_root / "Validation" / "source"
    sql_catalog = catalog_sql_files(source_root) if source_root.is_dir() else []
    if sql_catalog:
        (schemas_dir / "sql_files.json").write_text(
            json.dumps(sql_catalog, indent=2, default=str) + "\n", encoding="utf-8")
        summary = manifest.setdefault("summary", {})
        if isinstance(summary, dict):
            summary["n_sql_files"] = len(sql_catalog)
            (schemas_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")

    return {"entrypoints": len(ep_out),
            "tables": total_tables,
            "schemas_dir": str(schemas_dir),
            "ast_hint": ast_hint,
            "dynamic_class_loads": len(dynamic_loads),
            "open_todos": open_todos,
            "complete": manifest["complete"],
            "sql_files": len(sql_catalog)}


# ---------------------------------------------------------------------------
# Layer B2: *.sql template mining (PySpark sql_files.json parity)
# ---------------------------------------------------------------------------

_SQL_SKIP_DIRS = frozenset({
    ".git", "target", "node_modules", "__pycache__", ".venv", "venv",
    "Validation", "Output", "mock_data", "schemas", "build", "dist",
})
_SQL_TYPE_KEYWORDS = frozenset({
    "string", "int", "integer", "long", "double", "float", "boolean", "bool",
    "date", "timestamp", "decimal", "binary", "byte", "short", "array", "map",
    "struct", "null", "void",
})
_SQL_FILE_LINK_TODO = (
    "Link to every entrypoint that executes this file (search for new File(...), "
    "Files.readString, getClass().getResource, or string literals containing .sql). "
    "Merge each table's columns into those entrypoints' sources (read) or sinks "
    "(write); confirm sources/sinks and column types; delete this todo when done. "
    "Keep this sql_files row — only delete the entire row if no entrypoint uses "
    "the file (dead/orphan SQL)."
)


def _normalize_sql_placeholders(sql: str) -> str:
    s = re.sub(r"\$?\{[^}]+\}\.", "", sql)
    s = re.sub(r"\$?\{[^}]+\}", "_ph_", s)
    return s


def _dedupe_sql_columns(columns: set[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for col in sorted(columns):
        key = col.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(col)
    return out


def catalog_sql_files(root: Path) -> list[dict]:
    """Walk ``*.sql`` under *root* and mine table/column lineage (PySpark B2).

    Returns a path-keyed catalog for ``schemas/sql_files.json``. Whoever authors
    patches for this entrypoint links each file to entrypoints that execute it.
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return []
    if not root.is_dir():
        return []
    _ident = re.compile(r"[A-Za-z_]\w*")
    catalog: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SQL_SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".sql"):
                continue
            abs_p = Path(dirpath) / fn
            try:
                rel = str(abs_p.relative_to(root))
            except ValueError:
                rel = str(abs_p)
            try:
                sql = _normalize_sql_placeholders(
                    abs_p.read_text(encoding="utf-8", errors="replace")
                )
            except OSError:
                continue
            try:
                stmts = sqlglot.parse(sql, dialect="spark")
            except Exception:
                continue
            tables: dict[str, dict] = {}
            for st in stmts:
                if st is None:
                    continue
                cte_names = {c.alias.lower() for c in st.find_all(exp.CTE) if c.alias}
                write_targets: set[str] = set()
                for node in st.find_all(exp.Insert):
                    if isinstance(node.this, exp.Table) and node.this.name.lower() not in cte_names:
                        write_targets.add(node.this.name)
                for node in st.find_all(exp.Delete):
                    if isinstance(node.this, exp.Table) and node.this.name.lower() not in cte_names:
                        write_targets.add(node.this.name)
                for node in st.find_all(exp.Merge):
                    if isinstance(node.this, exp.Table) and node.this.name.lower() not in cte_names:
                        write_targets.add(node.this.name)
                alias2tbl: dict[str, str] = {}
                phys: list[str] = []
                for t in st.find_all(exp.Table):
                    if t.name.lower() in cte_names or t.name == "_ph_":
                        continue
                    alias2tbl[t.alias_or_name] = t.name
                    entry = tables.setdefault(t.name, {"columns": set(), "roles": set()})
                    phys.append(t.name)
                    if t.name in write_targets:
                        entry["roles"].add("write")
                    else:
                        entry["roles"].add("read")
                uniq = set(phys)
                for c in st.find_all(exp.Column):
                    col = c.name
                    if not col or not _ident.fullmatch(col) or col.lower() in _SQL_TYPE_KEYWORDS:
                        continue
                    tname = alias2tbl.get(c.table) if c.table else None
                    if tname:
                        tables.setdefault(tname, {"columns": set(), "roles": set()})
                        tables[tname]["columns"].add(col)
                    elif len(uniq) == 1:
                        only = next(iter(uniq))
                        tables.setdefault(only, {"columns": set(), "roles": set()})
                        tables[only]["columns"].add(col)
            tables_out: dict[str, dict] = {}
            for t, info in tables.items():
                k = t.lower()
                tables_out[k] = {
                    "name": t,
                    "columns": _dedupe_sql_columns(info.get("columns", set())),
                    "roles": sorted(info.get("roles", set())),
                }
            catalog.append({
                "path": rel.replace("\\", "/"),
                "tables": tables_out,
                "llm_todo": _SQL_FILE_LINK_TODO,
            })
    catalog.sort(key=lambda e: e["path"])
    return catalog


# ---------------------------------------------------------------------------
# Config-pool flattening (for the analyzer jar's dynamic-path resolution, once
# scos-analyze-java.jar grows a --config-pool-file flag — see run_scos_analyze_java).
# ---------------------------------------------------------------------------

_CONFIG_EXCLUDED_DIRS = frozenset({
    ".git", "target", "node_modules", "__pycache__", ".venv", "venv",
    "Validation", "Output", "mock_data", "schemas",
})
_PATHISH_RE = re.compile(
    r"(?:^[a-zA-Z][a-zA-Z0-9+.-]*://)|(?:^/)|(?:\.parquet$)|(?:\.csv$)|(?:\.json$)"
    r"|(?:^[A-Za-z_][\w]*\.[A-Za-z_][\w]*\.[A-Za-z_][\w]*$)",  # db.schema.table
)


def _walk_config_flat(data, out: dict[str, str], *, prefix: str = "") -> None:
    """Flatten JSON/YAML into ``{key: single_string_value}`` for the jar pool."""
    if isinstance(data, dict):
        for k, v in data.items():
            if not isinstance(k, str):
                continue
            key = k if not prefix else f"{prefix}.{k}"
            if isinstance(v, (dict, list)):
                _walk_config_flat(v, out, prefix=key)
            elif isinstance(v, str) and v.strip():
                # Prefer path-/table-like values; still keep short identifiers.
                if _PATHISH_RE.search(v) or len(v) < 120:
                    # Leaf key wins over dotted path for jar lookups.
                    out.setdefault(k, v)
                    out.setdefault(key, v)
            elif isinstance(v, (int, float, bool)):
                out.setdefault(k, str(v))
    elif isinstance(data, list):
        for item in data:
            _walk_config_flat(item, out, prefix=prefix)


def _build_flat_config_pool(source_root: Path, out_path: Path) -> Path | None:
    """Scan source for JSON/YAML configs -> flat map for dynamic-path resolution.

    Also honors an explicit ``Validation/shared/config_pool.json`` if already
    present (agent-authored). Returns the path written, or None if empty.
    NOTE: scos-analyze-java.jar does not yet accept a ``--config-pool-file``
    flag (see harness-java/control/src/main/java/Main.java), so
    run_scos_analyze_java() does not wire this in automatically. The pool is
    still produced here for future jar support and for manual inspection.
    """
    pool: dict[str, str] = {}
    # Prefer an explicit shared pool when present.
    explicit = out_path
    if explicit.is_file():
        try:
            data = json.loads(explicit.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                # Ensure values are strings for the jar.
                flat = {str(k): str(v) for k, v in data.items() if v is not None}
                if flat:
                    return explicit
        except (json.JSONDecodeError, OSError):
            pass

    search_roots = [source_root]
    # Companion configs sometimes live next to Validation/source (conv root).
    try:
        parent = source_root.parent
        if parent.name == "Validation":
            search_roots.append(parent.parent)
    except Exception:
        pass

    seen: set[Path] = set()
    for root in search_roots:
        if not root.is_dir():
            continue
        try:
            candidates = list(root.rglob("*"))
        except OSError:
            continue
        for cfg in candidates:
            try:
                if not cfg.is_file() or cfg.suffix not in (".json", ".yaml", ".yml"):
                    continue
                resolved = cfg.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            try:
                rel_parts = cfg.relative_to(root).parts
            except ValueError:
                continue
            if any(p in _CONFIG_EXCLUDED_DIRS for p in rel_parts):
                continue
            # Skip our own schemas / analysis artifacts.
            if cfg.name in ("analysis.json", "ast_facts.json", "manifest.json",
                            "config_pool.json", "schemas.json"):
                continue
            seen.add(resolved)
            try:
                text = cfg.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            data = None
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
            if data is not None:
                _walk_config_flat(data, pool)

    if not pool:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(pool, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"[schema_mine.py] wrote config_pool.json ({len(pool)} keys) -> {out_path}",
        file=sys.stderr,
    )
    return out_path


# ---------------------------------------------------------------------------
# scos-analyze-java.jar invocation (Java analog of Scala's run_scos_analyze)
# ---------------------------------------------------------------------------

def _find_scos_analyze_java_jar(skill_dir: Path | None = None) -> Path | None:
    """Locate scos-analyze-java.jar relative to this skill or SKILL_DIRECTORY env."""
    candidates: list[Path] = []
    if skill_dir:
        candidates.append(
            skill_dir / "harness-java" / "control" / "target" / "scos-analyze-java.jar"
        )
    env = os.environ.get("SKILL_DIRECTORY")
    if env:
        candidates.append(
            Path(env) / "harness-java" / "control" / "target" / "scos-analyze-java.jar"
        )
    here = Path(__file__).resolve().parent.parent  # validate-spark-java-.../
    candidates.append(
        here / "harness-java" / "control" / "target" / "scos-analyze-java.jar"
    )
    for c in candidates:
        if c.is_file():
            return c
    return None


def run_scos_analyze_java(source_root: Path, ast_out: Path, *, jar: Path | None = None) -> None:
    """Run scos-analyze-java.jar -> ast_facts.json. Raises SystemExit on failure.

    Unlike the Scala validator's run_scos_analyze, this does NOT pass a
    --config-pool-file flag: scos-analyze-java.jar's CLI (harness-java/control/
    src/main/java/Main.java) only accepts --source/--output. Use
    _build_flat_config_pool separately once the jar grows that flag.
    """
    jar = jar or _find_scos_analyze_java_jar()
    if jar is None:
        _die(2, "scos-analyze-java.jar not found; build harness-java/control/ (mvn package)")
    ast_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "java", "-jar", str(jar), "analyze",
        "--source", str(source_root),
        "--output", str(ast_out),
    ]
    print(f"[schema_mine.py] running: {' '.join(cmd)}", file=sys.stderr)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        _die(2, "java not found on PATH (required for scos-analyze-java.jar)")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        _die(2, f"scos-analyze-java.jar failed (exit {proc.returncode}): {err[:500]}")
    if not ast_out.is_file():
        _die(2, f"scos-analyze-java.jar did not write {ast_out}")


def synthesize_from_source(
    conv_root: Path,
    *,
    source_root: Path | None = None,
    schemas_out: Path | None = None,
    emit_analysis_json: bool = True,
    jar: Path | None = None,
    detect_dynamic_imports: bool = False,
) -> dict:
    """PySpark-parity entry: source -> schemas/ (via scos-analyze-java.jar + ast_to_analysis).

    1. Run scos-analyze-java.jar -> Validation/shared/ast_facts.json
    2. Survey + promote all candidates + deep analysis -> analysis.json skeleton
    3. Project analysis.json -> schemas/ (numeric weights, Java _meta fields)
    """
    # Import sibling ast_to_analysis without requiring package install.
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import ast_to_analysis as a2a  # noqa: PLC0415

    conv_root = Path(conv_root).expanduser().resolve()
    shared = conv_root / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    src = Path(source_root).expanduser().resolve() if source_root else (
        conv_root / "Validation" / "source"
    )
    if not src.is_dir():
        _die(2, f"source root not found: {src}")

    # Ensure Validation/source exists for downstream (symlink-free copy expected
    # by the orchestrator; if caller passed an alternate source_root, copy/link
    # only when Validation/source is missing).
    validation_source = conv_root / "Validation" / "source"
    if src.resolve() != validation_source.resolve() and not validation_source.is_dir():
        validation_source.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(src, validation_source)
        except OSError:
            import shutil
            shutil.copytree(src, validation_source, dirs_exist_ok=True)

    ast_path = shared / "ast_facts.json"
    run_scos_analyze_java(src if src.is_dir() else validation_source, ast_path, jar=jar)

    # Survey -> promote all -> deep (same deterministic path as PySpark/Scala).
    survey = a2a.run(conv_root, mode="survey", merge=True)
    candidates = survey.get("entrypoint_candidates") or []
    if not candidates:
        print("[schema_mine.py] WARNING: no entrypoint candidates found", file=sys.stderr)
    # Promote every candidate into entrypoints[] with numeric default weight.
    entrypoints = []
    for c in candidates:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        ep = dict(c)
        ep.setdefault("weight", 10)  # medium default; recomputed after deep
        entrypoints.append(ep)
    survey["entrypoints"] = entrypoints
    analysis_path = shared / "analysis.json"
    analysis_path.write_text(json.dumps(survey, indent=2) + "\n", encoding="utf-8")

    if entrypoints:
        a2a.run(conv_root, mode="deep", merge=True)

    if not emit_analysis_json and analysis_path.is_file():
        # Keep analysis.json for JVM shim / dual-read; emit flag only controls
        # whether we treat it as the durable SoT in docs. Always leave it for now.
        pass

    out = schemas_out or (shared / "schemas")
    res = analysis_to_schemas(
        conv_root,
        detect_dynamic_imports=detect_dynamic_imports,
        schemas_dir_override=out,
    )
    print(
        f"[schema_mine.py] synthesized schemas/ for {res['entrypoints']} entrypoint(s), "
        f"{res['tables']} table(s), open_todos={res.get('open_todos', '?')} "
        f"-> {res['schemas_dir']}{res.get('ast_hint', '')}"
    )
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="schema_mine.py",
        description="Java: convert analysis.json into the PySpark schemas/ layout.")
    ap.add_argument("--conv-root", required=True,
                    help="Conversion root containing Validation/")
    ap.add_argument("--from-source", action="store_true",
                    help="Run the full source -> schemas/ pipeline via scos-analyze-java.jar "
                         "instead of reading an existing analysis.json.")
    ap.add_argument("--detect-dynamic-imports", action="store_true",
                    help="Scan for Class.forName(config(...)) dynamic dispatch sites.")
    args = ap.parse_args(argv)
    conv_root = Path(args.conv_root).expanduser().resolve()
    if args.from_source:
        res = synthesize_from_source(conv_root, detect_dynamic_imports=args.detect_dynamic_imports)
    else:
        res = analysis_to_schemas(conv_root, detect_dynamic_imports=args.detect_dynamic_imports)
    print(f"[schema_mine.py] wrote schemas/ for {res['entrypoints']} entrypoint(s), "
          f"{res['tables']} table(s) -> {res['schemas_dir']}{res.get('ast_hint', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
