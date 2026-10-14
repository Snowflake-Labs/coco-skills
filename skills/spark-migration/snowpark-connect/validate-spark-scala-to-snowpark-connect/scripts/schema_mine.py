#!/usr/bin/env python3
"""Scala schema-mining: source (or analysis.json) -> the PySpark ``schemas/`` layout.

Primary path (PySpark parity)::

    schema_mine.py <source_root> --out <schemas_dir>
    # or: schema_mine.py --conv-root <conv>   # uses Validation/source

Internally: ``scos-analyze.jar`` → ``ast_facts.json`` → ``ast_to_analysis``
(survey + promote all + deep) → write ``Validation/shared/schemas/``.

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

Scala-only fields are written onto each entrypoint ``_meta.json``:
  ``entrypoint_class``, ``entrypoint_method``, ``cli_args``, ``weight``.
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
# Scala parity for PySpark schema_mine._find_dynamic_import_sites.
# Detects Class.forName / classLoader.loadClass patterns where the class name
# traces back to a config subscript, surfacing config-driven pipeline dispatch.
# Uses a lightweight regex scan of .scala source files (no Python ast access to
# JVM code; Scalameta ast_facts does not include general method calls).
# ---------------------------------------------------------------------------
_FORNAME_RE = re.compile(
    r'(?:Class\.forName|classLoader\.loadClass|loadClass)\s*\(\s*'
    r'(?P<expr>[^)]{1,120})\)',
)
_CONFIG_KEY_RE = re.compile(r'(?:\.get)?\("([^"]{1,60})"\)')


def _find_dynamic_class_loads(source_root: Path) -> list[dict]:
    """Scan *.scala / *.sc files for Class.forName(config(\"KEY\")) patterns.

    Returns list of {file, line, kind, config_key, raw_expr}.
    """
    sites: list[dict] = []
    for p in sorted(source_root.rglob("*.scala")) + sorted(source_root.rglob("*.sc")):
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


def _table_entry(obj, access, columns):
    raw = next((obj.get(k) for k in ("original_path", "original_target", "name", "id")
                if obj.get(k)), "")
    # Prefer the explicit id/name as the schema table key — original_path is often a
    # runtime S3 path that produces mangled filenames when passed through _bare_table_name.
    key = (
        str(obj.get("id") or obj.get("name") or "")
        or _bare_table_name(raw)
        or "tbl"
    )
    entry: dict = {
        "access": access,
        "category": obj.get("category", "table"),
        "relational": True,
        "columns": columns,
        "reader_options": obj.get("reader_options") or {},
        "original_path": raw or str(obj.get("id") or obj.get("name") or key),
    }
    # SKILL-FIX: for table-category sources, derive declared_table_name from the
    # last segment of original_path when it is a concrete identifier (not a
    # placeholder like "<dynamic_table_1>"). This is the name the harness's
    # _declared_table_name helper would derive, but writing it explicitly here
    # avoids a second lookup and makes the schema files self-documenting.
    # E.g. "AWSDataCatalog.oi_analytics.psd_freight_estimate" → "psd_freight_estimate"
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
    #   - category "file": ext follows source_format (csv→csv, json→json, text→txt, else parquet)
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
        # Honor explicit mock_file from analysis.json when present — avoids
        # deriving a wrong name when original_path contains dots (e.g. "foo.parquet"
        # → _bare_table_name returns "parquet" → mock_file becomes "parquet.parquet").
        explicit_mock = obj.get("mock_file")
        if explicit_mock:
            entry["mock_file"] = explicit_mock
        else:
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
    # Accept both "sinks" (legacy key) and "external_sinks" (data-synthesizer output key).
    # The data-synthesizer writes ep["external_sinks"]; schema_mine previously only read
    # ep["sinks"], causing all data-synthesizer-authored sinks to be silently dropped.
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
        # Merge "sinks" (legacy) + "external_sinks" (data-synthesizer) per-entrypoint.
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
            "import_roots": ep.get("import_roots", ["src/main/scala"]),
            "entrypoint_kwargs": ep.get("entrypoint_kwargs", {}),
            "tables": tables,
            "source_runtime": ep.get("source_runtime") or "spark",
        }
        # pass through optional harness fields when present (Scala + shared)
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

    # Persist Scala-only top-level metadata for build-doctor / report.
    meta_sidecar = {
        "build_tool": analysis.get("build_tool"),
        "source_roots": analysis.get("source_roots") or [],
        "jar_path": analysis.get("jar_path"),
        "migration_issues": analysis.get("migration_issues") or [],
    }
    (schemas_dir / "scala_meta.json").write_text(
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
    "Link to every entrypoint that executes this file (search for open(...), "
    "Source.fromFile, getClass.getResource, or string literals containing .sql). "
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

    Returns a path-keyed catalog for ``schemas/sql_files.json``. The
    data-synthesizer links each file to entrypoints that execute it.
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


def _die(code: int, msg: str) -> None:
    print(f"[schema_mine.py] error: {msg}", file=sys.stderr)
    sys.exit(code)


def _find_scos_analyze_jar(skill_dir: Path | None = None) -> Path | None:
    """Locate scos-analyze.jar relative to this skill or SKILL_DIRECTORY env."""
    candidates: list[Path] = []
    if skill_dir:
        candidates.append(
            skill_dir / "harness-scala" / "control" / "target" / "scos-analyze.jar"
        )
    env = os.environ.get("SKILL_DIRECTORY")
    if env:
        candidates.append(
            Path(env) / "harness-scala" / "control" / "target" / "scos-analyze.jar"
        )
    here = Path(__file__).resolve().parent.parent  # validate-spark-scala-.../
    candidates.append(
        here / "harness-scala" / "control" / "target" / "scos-analyze.jar"
    )
    for c in candidates:
        if c.is_file():
            return c
    return None


def run_scos_analyze(
    source_root: Path,
    ast_out: Path,
    *,
    jar: Path | None = None,
    config_pool_file: Path | None = None,
) -> None:
    """Run scos-analyze.jar → ast_facts.json. Raises SystemExit on failure."""
    jar = jar or _find_scos_analyze_jar()
    if jar is None:
        _die(2, "scos-analyze.jar not found; run sbt assembly in harness-scala/control/")
    ast_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "java", "-jar", str(jar), "analyze",
        "--source", str(source_root),
        "--output", str(ast_out),
    ]
    pool_path = config_pool_file
    if pool_path is None:
        # Auto-build a flat config pool next to ast_facts for dynamic path resolution.
        candidate = ast_out.parent / "config_pool.json"
        built = _build_flat_config_pool(source_root, candidate)
        if built is not None:
            pool_path = built
    if pool_path is not None and Path(pool_path).is_file():
        cmd.extend(["--config-pool-file", str(pool_path)])
    print(f"[schema_mine.py] running: {' '.join(cmd)}", file=sys.stderr)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        _die(2, "java not found on PATH (required for scos-analyze.jar)")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        _die(2, f"scos-analyze.jar failed (exit {proc.returncode}): {err[:500]}")
    if not ast_out.is_file():
        _die(2, f"scos-analyze.jar did not write {ast_out}")


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
                    # Leaf key wins over dotted path for jar B7/B8 lookups.
                    out.setdefault(k, v)
                    out.setdefault(key, v)
            elif isinstance(v, (int, float, bool)):
                out.setdefault(k, str(v))
    elif isinstance(data, list):
        for item in data:
            _walk_config_flat(item, out, prefix=prefix)


def _build_flat_config_pool(source_root: Path, out_path: Path) -> Path | None:
    """Scan source for JSON/YAML configs → flat map for ``--config-pool-file``.

    Also honors an explicit ``Validation/shared/config_pool.json`` if already
    present (agent-authored). Returns the path written, or None if empty.
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


def synthesize_from_source(
    conv_root: Path,
    *,
    source_root: Path | None = None,
    schemas_out: Path | None = None,
    emit_analysis_json: bool = True,
    jar: Path | None = None,
    detect_dynamic_imports: bool = False,
) -> dict:
    """PySpark-parity entry: source → schemas/ (via Scalameta + ast_to_analysis).

    1. Run scos-analyze.jar → Validation/shared/ast_facts.json
    2. Survey + promote all candidates + deep analysis → analysis.json skeleton
    3. Project analysis.json → schemas/ (numeric weights, Scala _meta fields)
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
    run_scos_analyze(src if src.is_dir() else validation_source, ast_path, jar=jar)

    # Survey → promote all → deep (same as data-synthesizer deterministic path).
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


def schemas_to_analysis_shim(conv_root: Path, *, analysis_out: Path | None = None) -> dict:
    """Build a kit-compatible analysis.json from schemas/ (generated, not hand-edited).

    The JVM harness still loads analysis.json; this regenerates it from the
    schemas/ source of truth so agents edit schemas/ only.
    """
    conv_root = Path(conv_root).expanduser().resolve()
    shared = conv_root / "Validation" / "shared"
    schemas_dir = shared / "schemas"
    manifest_path = schemas_dir / "manifest.json"
    if not manifest_path.is_file():
        _die(2, f"schemas/manifest.json not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scala_meta = {}
    sm_path = schemas_dir / "scala_meta.json"
    if sm_path.is_file():
        try:
            scala_meta = json.loads(sm_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            scala_meta = {}

    # Prefer PySpark datagen.load_entrypoint when importable; else read dirs manually.
    eps: list[dict] = []
    try:
        _pyspark = (
            Path(__file__).resolve().parent.parent.parent
            / "validate-pyspark-to-snowpark-connect" / "scripts"
        )
        if str(_pyspark) not in sys.path:
            sys.path.insert(0, str(_pyspark))
        import datagen as _dg  # noqa: PLC0415
        for ref in manifest.get("entrypoints") or []:
            eid = ref.get("id")
            if eid:
                eps.append(_dg.load_entrypoint(str(schemas_dir), eid))
    except Exception:
        for ref in manifest.get("entrypoints") or []:
            eid = ref.get("id")
            if not eid:
                continue
            meta_p = schemas_dir / "entrypoints" / eid / "_meta.json"
            if not meta_p.is_file():
                continue
            ep = json.loads(meta_p.read_text(encoding="utf-8"))
            tables = {}
            tdir = schemas_dir / "entrypoints" / eid / "tables"
            if tdir.is_dir():
                for tf in tdir.glob("*.json"):
                    t = json.loads(tf.read_text(encoding="utf-8"))
                    key = t.pop("_table_key", tf.stem)
                    tables[key] = t
            ep["tables"] = tables
            eps.append(ep)

    external_sources: list[dict] = []
    sinks: list[dict] = []
    out_eps: list[dict] = []
    for ep in eps:
        sources = []
        ep_sinks = []
        for key, tbl in (ep.get("tables") or {}).items():
            if not isinstance(tbl, dict):
                continue
            access = (tbl.get("access") or "read").lower()
            cols = tbl.get("columns") or []
            schema_cols = [
                {"name": c.get("name"), "type": c.get("type", "string"),
                 "nullable": c.get("nullable", True)}
                for c in cols if isinstance(c, dict) and c.get("name")
            ]
            if access in ("read", "readwrite"):
                src = {
                    "id": key,
                    "name": key,
                    "category": tbl.get("category") or "table",
                    "original_path": tbl.get("original_path") or key,
                    "schema": schema_cols,
                }
                if tbl.get("mock_file"):
                    src["mock_file"] = tbl["mock_file"]
                if tbl.get("llm_todo"):
                    src["llm_todo"] = tbl["llm_todo"]
                sources.append(src)
                external_sources.append(src)
            if access in ("write", "readwrite"):
                sink = {
                    "id": key,
                    "name": key,
                    "kind": "table" if (tbl.get("category") or "table") == "table"
                            else (tbl.get("category") or "file"),
                    "original_target": tbl.get("original_path") or key,
                    "schema": schema_cols,
                }
                if tbl.get("allow_empty"):
                    sink["allow_empty"] = tbl["allow_empty"]
                if tbl.get("format"):
                    sink["format"] = tbl["format"]
                if tbl.get("llm_todo"):
                    sink["llm_todo"] = tbl["llm_todo"]
                ep_sinks.append(sink)
                sinks.append(sink)
        out_ep = {
            "id": ep.get("id"),
            "path": ep.get("path") or ep.get("id"),
            "run_mode": ep.get("run_mode", "script"),
            "import_roots": ep.get("import_roots") or ["src/main/scala"],
            "entrypoint_kwargs": ep.get("entrypoint_kwargs") or {},
            "external_sources": sources,
            "sinks": ep_sinks,
            "external_sinks": ep_sinks,  # both keys for dual consumers
            "weight": ep.get("weight"),
        }
        for k in ("entrypoint_class", "entrypoint_method", "cli_args",
                  "entrypoint_callable", "widget_env_vars", "joins", "llm_todo",
                  "unsupported_constructs", "mock_data_dir", "source_runtime"):
            if ep.get(k) is not None:
                out_ep[k] = ep[k]
        out_eps.append(out_ep)

    analysis = {
        "root": manifest.get("root"),
        "complete": bool(manifest.get("complete")),
        "entrypoints": out_eps,
        "entrypoint_candidates": out_eps,
        "external_sources": external_sources,
        "sinks": sinks,
        "external_sinks": sinks,
        "expected_divergences": manifest.get("expected_divergences") or {},
        "build_tool": scala_meta.get("build_tool") or (manifest.get("summary") or {}).get("build_tool"),
        "source_roots": scala_meta.get("source_roots") or (manifest.get("summary") or {}).get("source_roots") or [],
        "jar_path": scala_meta.get("jar_path"),
        "migration_issues": scala_meta.get("migration_issues") or [],
        "generated_from": "schemas/",
    }
    out_path = analysis_out or (shared / "analysis.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(analysis, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"[schema_mine.py] wrote analysis shim ({len(out_eps)} entrypoints) -> {out_path}")
    return analysis


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="schema_mine.py",
        description=(
            "Scala schema mine (PySpark parity): source → schemas/, or "
            "legacy --from-analysis / --conv-root analysis.json → schemas/."
        ),
    )
    ap.add_argument(
        "workload",
        nargs="?",
        default=None,
        help="Source directory to mine (PySpark-style). Implies synthesize path.",
    )
    ap.add_argument(
        "--out",
        metavar="SCHEMAS_DIR",
        default=None,
        help="Write schemas/ here (default: <conv>/Validation/shared/schemas)",
    )
    ap.add_argument(
        "--conv-root",
        default=None,
        help="Conversion root containing Validation/ (required unless --out alone with workload)",
    )
    ap.add_argument(
        "--from-analysis",
        action="store_true",
        help="Legacy: only project existing analysis.json → schemas/ (skip jar/ast)",
    )
    ap.add_argument(
        "--emit-analysis-json",
        action="store_true",
        default=True,
        help="Keep Validation/shared/analysis.json as JVM/debug shim (default)",
    )
    ap.add_argument(
        "--no-emit-analysis-json",
        action="store_false",
        dest="emit_analysis_json",
    )
    ap.add_argument(
        "--shim-only",
        action="store_true",
        help="Regenerate analysis.json from schemas/ only (schemas-to-analysis shim)",
    )
    ap.add_argument(
        "--detect-dynamic-imports",
        action="store_true",
        help="Scan for Class.forName(config(...)) sites",
    )
    args = ap.parse_args(argv)

    if args.shim_only:
        if not args.conv_root:
            _die(2, "--shim-only requires --conv-root")
        schemas_to_analysis_shim(Path(args.conv_root).expanduser().resolve())
        return 0

    if args.from_analysis or (args.conv_root and not args.workload):
        if not args.conv_root:
            _die(2, "--conv-root is required for --from-analysis / analysis→schemas")
        conv = Path(args.conv_root).expanduser().resolve()
        res = analysis_to_schemas(
            conv,
            detect_dynamic_imports=args.detect_dynamic_imports,
            schemas_dir_override=Path(args.out).expanduser().resolve() if args.out else None,
        )
        print(
            f"[schema_mine.py] wrote schemas/ for {res['entrypoints']} entrypoint(s), "
            f"{res['tables']} table(s) -> {res['schemas_dir']}{res.get('ast_hint', '')}"
        )
        return 0

    # Synthesize from source (PySpark Step 1 parity).
    if not args.workload and not args.conv_root:
        _die(2, "provide a workload path or --conv-root")
    if args.conv_root:
        conv = Path(args.conv_root).expanduser().resolve()
        source = (
            Path(args.workload).expanduser().resolve()
            if args.workload
            else conv / "Validation" / "source"
        )
    else:
        # workload-only: treat parent of schemas --out as needing a synthetic conv layout
        source = Path(args.workload).expanduser().resolve()
        if not args.out:
            _die(2, "workload mode requires --out <schemas_dir> (or pass --conv-root)")
        # Create a minimal temp conversion layout under the out parent
        out = Path(args.out).expanduser().resolve()
        # Expect --out .../Validation/shared/schemas → conv is two levels up from shared
        if out.name == "schemas" and out.parent.name == "shared":
            conv = out.parent.parent.parent
        else:
            _die(2, "with workload + --out, prefer --conv-root; or --out .../Validation/shared/schemas")

    synthesize_from_source(
        conv,
        source_root=source,
        schemas_out=Path(args.out).expanduser().resolve() if args.out else None,
        emit_analysis_json=args.emit_analysis_json,
        detect_dynamic_imports=args.detect_dynamic_imports,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
