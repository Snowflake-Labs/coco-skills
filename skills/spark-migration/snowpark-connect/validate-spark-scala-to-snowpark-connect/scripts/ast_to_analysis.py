#!/usr/bin/env python3
"""Deterministic bridge: ast_facts.json -> analysis.json skeleton.

PySpark's schema_mine.py mines Python source directly and emits a *completable
contract* with ``llm_todo`` markers. Scala splits the pipeline:

  ast_facts.jar analyze  -> ast_facts.json   (deterministic; StructType + SQL)
  ast_to_analysis.py     -> analysis.json    (this script — deterministic skeleton)
  LLM                    -> fills llm_todo gaps only
  schema_mine.py         -> schemas/ + sql_files.json

Mining layers (PySpark parity):
  A. StructType/StructField (jar ``struct_schemas`` / read ``schema_fields``,
     plus Python regex fallback)
  B. spark.sql() lineage via sqlglot on ``sql_calls[].literal``
  B2. ``*.sql`` templates → ``schemas/sql_files.json`` (in schema_mine.py)
  C lite. Per-read column attribution from helper files + StructType prefer
  Cross-EP sink→read column inheritance (in schema_mine.analysis_to_schemas)

Modes:
  survey — entrypoint_candidates, source_roots, build_tool (no selected entrypoints)
  deep   — per-selected-entrypoint external_sources, sinks, schemas (with llm_todo)
  auto   — survey when entrypoints[] absent, else deep (default)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_FILE_READERS = {"parquet", "csv", "json", "orc", "text", "textfile", "load"}
_TABLE_READERS = {"table", "jdbc"}
_WRITE_TABLE = {"savetable", "insertinto"}
_WRITE_FILE = {"parquet", "csv", "json", "orc", "text", "save"}

_JOB_NAME_RE = re.compile(r"(Pipeline|Job|Driver)\.scala$", re.I)
_NOTEBOOK_MARKERS = (
    "// Databricks notebook source",
    "// COMMAND ---------",
)

# ---------------------------------------------------------------------------
# Filter / join literal mining (Fix 3 — AST data-contract enrichment)
# Prefer Scalameta facts from scos-analyze; regex fills gaps only.
# Best-effort for literal predicates; complex predicates stay llm_todo.
# ---------------------------------------------------------------------------

# col("x") === "y"  |  $"x" === "y"  |  col("x") === 42
_EQ_LIT_RE = re.compile(
    r"""(?:col\s*\(\s*["']([^"']+)["']\s*\)|\$"([^"]+)")"""
    r"""\s*===\s*"""
    r"""(?:["']([^"']*)["']|(-?\d+(?:\.\d+)?|true|false))""",
    re.I,
)
# col("x").isin("a", "b")  |  col("x").isin(Seq("a", "b"))
_ISIN_RE = re.compile(
    r"""(?:col\s*\(\s*["']([^"']+)["']\s*\)|\$"([^"]+)")"""
    r"""\s*\.\s*isin\s*\(\s*(?:Seq\s*\(\s*)?([^)]*?)(?:\s*\))?\s*\)""",
    re.I | re.S,
)
# .filter("col = 'val'") / .where("col = \"val\"") / .filter("col = 1")
_FILTER_SQL_RE = re.compile(
    r"""\.(?:filter|where)\s*\(\s*"""
    r"""["']([A-Za-z_][\w]*)\s*=\s*(?:'([^']*)'|"([^"]*)"|(-?\d+(?:\.\d+)?|true|false))["']"""
    r"""\s*\)""",
    re.I,
)
# .join(..., Seq("k1", "k2"))
_JOIN_SEQ_RE = re.compile(
    r"""\.join\s*\([^;]*?Seq\s*\(\s*((?:["'][^"']+["']\s*,?\s*)+)\)""",
    re.I | re.S,
)
# .join(other, "key")
_JOIN_STR_RE = re.compile(
    r"""\.join\s*\(\s*[^,]+,\s*["']([^"']+)["']""",
    re.I,
)
# .join(..., col("a") === col("b")) / $"a" === $"b"
_JOIN_EQ_COLS_RE = re.compile(
    r"""\.join\s*\([^;]*?"""
    r"""(?:col\s*\(\s*["']([^"']+)["']\s*\)|\$"([^"]+)")"""
    r"""\s*===\s*"""
    r"""(?:col\s*\(\s*["']([^"']+)["']\s*\)|\$"([^"]+)")""",
    re.I | re.S,
)
_STR_LIT_RE = re.compile(r"""["']([^"']+)["']""")
# Negated isin — do not seed excluded domains
_NEGATED_ISIN_RE = re.compile(
    r"""(?:!|not\s+)\s*(?:col\s*\(\s*["'][^"']+["']\s*\)|\$"[^"]+")\s*\.\s*isin\s*\(""",
    re.I,
)


def _die(code: int, msg: str) -> None:
    print(f"[ast_to_analysis.py] error: {msg}", file=sys.stderr)
    sys.exit(code)


def _load_json(path: Path) -> Any:
    """Load JSON with a clear error instead of an opaque traceback.

    ast_facts.json / analysis.json are produced by the external scos-analyze.jar;
    a partial write or bad encoding would otherwise crash with a raw JSONDecodeError.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        _die(2, f"cannot parse {path.name}: {e}")


def _slug(path: str) -> str:
    stem = Path(path).stem
    return re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_").lower() or "entrypoint"


def _rel_path(path: str, root: Path) -> str:
    p = Path(path)
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------------------
# Transitive I/O resolver
# ---------------------------------------------------------------------------

def _import_to_rel_candidates(imp: str, source_roots: list[str]) -> list[str]:
    """Convert a Scala FQN import to candidate relative file paths.

    e.g. 'com.flashfood.petl.transform.{Bronze, Silver}' →
         ['src/main/scala/com/flashfood/petl/transform/Bronze.scala',
          'src/main/scala/com/flashfood/petl/transform/Silver.scala']
    """
    # Strip trailing ._ or ._
    base = re.sub(r"\.\{.*\}$", "", imp)   # remove {A,B} suffix
    base = re.sub(r"\._$", "", base)        # remove ._
    pkg_path = base.replace(".", "/") + ".scala"

    candidates = []
    for src_root in source_roots or ["src/main/scala"]:
        candidates.append(f"{src_root}/{pkg_path}")
    # Also try the package directory (parent) for wildcard imports
    return candidates


def _collect_transitive_facts(
    entrypoint_facts: dict,
    by_rel: dict,
    source_root: Path,
    source_roots: list[str],
    max_depth: int = 4,
) -> list[dict]:
    """Return a list of ast_facts dicts for files reachable via imports
    from the entrypoint file (BFS, capped at max_depth).

    Only files that have non-empty reads, writes, write_helpers, schemas,
    or column/sql signals are included in the result so the caller is never
    flooded with irrelevant stdlib imports.
    """
    visited: set[str] = set()
    queue: list[tuple[dict, int]] = [(entrypoint_facts, 0)]
    result: list[dict] = []

    while queue:
        facts, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for imp in facts.get("imports") or []:
            # Expand {A, B} grouped imports
            m = re.match(r"^([\w.]+)\.\{([^}]+)\}$", imp)
            if m:
                pkg_prefix = m.group(1)
                names = [n.strip() for n in m.group(2).split(",")]
                expanded = [f"{pkg_prefix}.{n}" for n in names if n and n != "_"]
            else:
                expanded = [imp]

            for single_imp in expanded:
                for rel in _import_to_rel_candidates(single_imp, source_roots):
                    # Normalise
                    rel_norm = rel.replace("\\", "/")
                    # Try to match against by_rel (which uses OS-native separators)
                    dep_facts = by_rel.get(rel_norm) or by_rel.get(
                        rel_norm.replace("/", os.sep)
                    )
                    if dep_facts is None:
                        # Try strip-prefix match (handles partial source_root)
                        for key, val in by_rel.items():
                            if key.replace("\\", "/").endswith(
                                "/".join(Path(rel_norm).parts[-3:])
                            ):
                                dep_facts = val
                                rel_norm = key
                                break
                    if dep_facts is None or rel_norm in visited:
                        continue
                    visited.add(rel_norm)
                    has_io = (
                        dep_facts.get("reads")
                        or dep_facts.get("writes")
                        or dep_facts.get("write_helpers")
                        or dep_facts.get("unresolved_reads")
                        or dep_facts.get("filters")
                        or dep_facts.get("joins")
                        or dep_facts.get("struct_schemas")
                        or dep_facts.get("sql_calls")
                        or dep_facts.get("column_refs")
                    )
                    if has_io:
                        result.append(dep_facts)
                    # Always enqueue for further traversal (BFS)
                    queue.append((dep_facts, depth + 1))

    return result


def _bare_name(raw: str) -> str:
    if not raw:
        return ""
    name = str(raw).strip().strip('"').strip("`")
    if "." in name:
        name = name.split(".")[-1]
    if "/" in name:
        name = name.split("/")[-1]
    return re.sub(r"\.[^.]+$", "", name) or name


def _detect_build_tool(source_root: Path) -> str:
    for name, tool in (
        ("build.sbt", "sbt"),
        ("pom.xml", "maven"),
        ("build.gradle.kts", "gradle"),
        ("build.gradle", "gradle"),
    ):
        if (source_root / name).is_file():
            return tool
    return "unknown"


def _detect_source_roots(source_root: Path, build_tool: str) -> list[str]:
    candidates = ["src/main/scala", "src/main/java"]
    found = [c for c in candidates if (source_root / c).is_dir()]
    if found:
        return found
    if build_tool == "unknown" and any(source_root.glob("*.scala")):
        return ["."]
    return found or ["src/main/scala"]


def _read_notebook_markers(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return False
    return any(m in head for m in _NOTEBOOK_MARKERS)


def _file_facts_by_rel(ast_facts: dict, source_root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in ast_facts.get("files") or []:
        if not isinstance(f, dict) or not f.get("parse_ok", True):
            continue
        rel = _rel_path(f.get("path", ""), source_root)
        out[rel] = f
        out[rel.replace("\\", "/")] = f
    return out


def _classify_read(call: str) -> tuple[str, str]:
    c = (call or "").lower()
    if c in _TABLE_READERS:
        return "table", c
    if c in _FILE_READERS:
        return "file", c
    return "table", c or "load"


def _classify_write(call: str) -> tuple[str, str]:
    c = (call or "").lower()
    if c in _WRITE_TABLE:
        return "table", c
    if c in _WRITE_FILE:
        return "file", c
    return "table", c or "save"


def _source_id(prefix: str, raw: str) -> str:
    bare = _bare_name(raw).lower() or "unknown"
    safe = re.sub(r"[^a-z0-9_]+", "_", bare).strip("_")
    return f"{prefix}_{safe}"[:64]


def _schema_from_columns(columns: list[str], *, llm_todo: str) -> list[dict]:
    schema = [{"name": c, "type": "string"} for c in sorted(set(columns)) if c]
    if schema:
        return schema
    return []


def _normalize_sql_placeholders(sql: str) -> str:
    """Make templated SQL parseable by sqlglot (PySpark schema_mine parity)."""
    s = re.sub(r"\$?\{[^}]+\}\.", "", sql)
    s = re.sub(r"\$?\{[^}]+\}", "_ph_", s)
    return s


def _sql_lineage(sql_bodies: list[str]) -> dict[str, dict]:
    """Per-table column sets + filter literal domains via sqlglot (PySpark parity).

    Returns ``{table: {columns: [...], values: {col: [lits]}}}`` plus optional
    ``__col_values__`` for unqualified column domains.
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return {}
    tables: dict[str, set] = {}
    col_values: dict[str, set] = {}
    for body in sql_bodies:
        if not body or not isinstance(body, str):
            continue
        try:
            tree = sqlglot.parse_one(_normalize_sql_placeholders(body), dialect="spark")
        except Exception:
            continue
        if tree is None:
            continue
        alias2tbl: dict[str, str] = {}
        for t in tree.find_all(exp.Table):
            alias2tbl[t.alias_or_name] = t.name
            tables.setdefault(t.name, set())
        for c in tree.find_all(exp.Column):
            tname = alias2tbl.get(c.table) if c.table else None
            if tname:
                tables.setdefault(tname, set()).add(c.name)
            elif len(tables) == 1:
                only = next(iter(tables))
                tables[only].add(c.name)
        for in_expr in tree.find_all(exp.In):
            col = in_expr.this
            if isinstance(col, exp.Column) and in_expr.expressions:
                vals = [e.this for e in in_expr.expressions if isinstance(e, exp.Literal)]
                if vals:
                    col_values.setdefault(col.name, set()).update(vals)
        for eq in tree.find_all(exp.EQ):
            for a, b in ((eq.this, eq.expression), (eq.expression, eq.this)):
                if isinstance(a, exp.Column) and isinstance(b, exp.Literal):
                    col_values.setdefault(a.name, set()).add(b.this)
                    break
    out: dict[str, dict] = {}
    for t, cs in tables.items():
        out[t] = {"columns": sorted(cs)}
    if col_values:
        out["__col_values__"] = {c: sorted(v) for c, v in col_values.items()}
    return out


def _sql_bodies_from_facts(facts: dict, extra_facts: list[dict] | None = None) -> list[str]:
    bodies: list[str] = []
    for f in [facts] + list(extra_facts or []):
        for sql in f.get("sql_calls") or []:
            if isinstance(sql, dict) and isinstance(sql.get("literal"), str):
                lit = sql["literal"].strip()
                if lit:
                    bodies.append(lit)
    return bodies


def _merge_sql_lineage_into_sources(
    sources: list[dict],
    lineage: dict[str, dict],
) -> list[dict]:
    """Attach sqlglot-mined columns/values to matching sources; add missing tables."""
    if not lineage:
        return sources
    col_values_global = lineage.get("__col_values__") or {}
    by_bare: dict[str, dict] = {}
    for src in sources:
        if not isinstance(src, dict):
            continue
        for key in (src.get("id"), src.get("name"), src.get("original_path")):
            if isinstance(key, str) and key:
                bare = _bare_name(key).lower()
                if bare:
                    by_bare[bare] = src

    for tname, info in lineage.items():
        if tname == "__col_values__" or not isinstance(info, dict):
            continue
        bare = _bare_name(tname).lower()
        cols = [c for c in (info.get("columns") or []) if isinstance(c, str) and c]
        src = by_bare.get(bare)
        if src is None:
            # New table discovered only via spark.sql — add as source with llm_todo.
            schema = [{"name": c, "type": "string"} for c in sorted(set(cols))]
            entry = {
                "id": _source_id("src", tname),
                "name": _bare_name(tname) or tname,
                "category": "table",
                "original_path": tname,
                "reader_method": "sql",
                "reader_options": {},
                "schema": schema,
                "llm_todo": (
                    "discovered via spark.sql() lineage; confirm schema types "
                    "and that this table is a real external source"
                ),
            }
            sources.append(entry)
            by_bare[bare] = entry
            src = entry
        else:
            have = {
                (c.get("name") or "").lower()
                for c in (src.get("schema") or [])
                if isinstance(c, dict)
            }
            schema = list(src.get("schema") or [])
            for c in cols:
                if c.lower() not in have:
                    schema.append({"name": c, "type": "string"})
                    have.add(c.lower())
            src["schema"] = schema
            # Clear "no column_refs" style todos when sqlglot filled columns.
            todo = (src.get("llm_todo") or "")
            if cols and "no column_refs" in todo:
                src.pop("llm_todo", None)

        # Apply filter literal domains onto matching schema columns.
        values_map = dict(col_values_global)
        for c in (src.get("schema") or []):
            if not isinstance(c, dict) or not c.get("name"):
                continue
            vals = values_map.get(c["name"])
            if vals and not c.get("values"):
                c["values"] = list(vals)
    return sources


def _sf_type(t: str) -> str:
    base = (t or "string").split("<")[0].split("(")[0].lower()
    return {
        "long": "bigint", "int": "int", "integer": "int",
        "double": "double", "float": "float", "boolean": "boolean",
        "timestamp": "timestamp", "date": "date", "string": "string",
        "decimal": "decimal", "bigint": "bigint",
    }.get(base, "string")


def _validate_with_sqlframe(sources: list[dict], sql_bodies: list[str]) -> dict:
    """Layer D (PySpark parity): replay spark.sql bodies against the mined catalog."""
    if not sql_bodies:
        return {"status": "skipped", "reason": "no embedded SQL"}
    try:
        from sqlframe.standalone import StandaloneSession
    except ImportError:
        return {"status": "skipped", "reason": "sqlframe not installed"}
    try:
        spark = StandaloneSession.builder.getOrCreate()
    except Exception as exc:
        return {"status": "skipped", "reason": f"sqlframe init failed: {exc}"}

    registered = 0
    for src in sources:
        if not isinstance(src, dict) or src.get("relational") is False:
            continue
        name = _bare_name(src.get("name") or src.get("original_path") or src.get("id") or "")
        if not name:
            continue
        cols = {
            c["name"]: _sf_type(c.get("type") or "string")
            for c in (src.get("schema") or [])
            if isinstance(c, dict) and c.get("name")
        }
        if not cols:
            continue
        try:
            spark.catalog.add_table(name, cols)
            registered += 1
        except Exception:
            pass
    if registered == 0:
        return {"status": "skipped", "reason": "no typed tables to register"}

    missing, ok = [], 0
    for body in sql_bodies:
        try:
            df = spark.sql(_normalize_sql_placeholders(body))
            _ = df.columns
            ok += 1
        except Exception as e:
            msg = str(e)
            m = (
                re.search(r"[Uu]nknown column:?\s*([\w.]+)", msg)
                or re.search(r"[Cc]olumn '([\w.]+)' could not be resolved", msg)
                or re.search(r"Cannot find column '?([\w.]+)'?", msg)
            )
            missing.append({
                "column": m.group(1) if m else None,
                "error": msg[:160],
            })
    return {
        "status": "ran",
        "queries_ok": ok,
        "queries_failed": len(missing),
        "missing_columns": missing,
        "tables_registered": registered,
    }


def _apply_sqlframe_validation(
    sources: list[dict],
    todos: list[str],
    validation: dict,
) -> tuple[list[dict], list[str]]:
    """Merge sqlframe missing-column findings into source schemas / llm_todos."""
    if validation.get("status") != "ran":
        return sources, todos
    missing = [
        m.get("column") for m in (validation.get("missing_columns") or [])
        if isinstance(m, dict) and m.get("column")
    ]
    if not missing:
        return sources, todos
    # Attach unresolved columns to the first relational source as a hint when
    # we cannot attribute them (single-table queries usually resolve via sqlglot).
    bare_cols = [c.split(".")[-1] for c in missing if c]
    if len(sources) == 1 and bare_cols:
        src = sources[0]
        have = {
            (c.get("name") or "").lower()
            for c in (src.get("schema") or [])
            if isinstance(c, dict)
        }
        schema = list(src.get("schema") or [])
        added = []
        for c in bare_cols:
            if c.lower() not in have:
                schema.append({"name": c, "type": "string", "origin": "sqlframe"})
                have.add(c.lower())
                added.append(c)
        src["schema"] = schema
        if added:
            todo = (
                f"sqlframe found missing columns {added} — confirm types "
                f"(added as string from Layer D replay)"
            )
            src["llm_todo"] = todo
            todos.append(f"{src.get('id')}: sqlframe missing cols")
    else:
        for col in bare_cols[:5]:
            todos.append(f"sqlframe: unresolved column '{col}' — attribute to a source")
    return sources, todos


def _derive_fqcn(source_root: Path, rel: str, owner: str) -> str:
    """Derive the fully-qualified class name from source root, relative path, and owner name.

    For src/main/scala/com/example/pkg/Foo.scala with owner Foo:
      package = com.example.pkg
      fqcn    = com.example.pkg.Foo
    """
    from pathlib import PurePosixPath
    p = PurePosixPath(rel)
    pkg_parts = list(p.parts[:-1])  # drop filename
    _SOURCE_PREFIXES = [
        ("src", "main", "scala"), ("src", "test", "scala"),
        ("src", "main", "java"),  ("src", "test", "java"),
    ]
    for prefix in _SOURCE_PREFIXES:
        n = len(prefix)
        if tuple(pkg_parts[:n]) == prefix:
            pkg_parts = pkg_parts[n:]
            break
    pkg = ".".join(pkg_parts)
    return f"{pkg}.{owner}" if pkg else owner


def _build_candidates(ast_facts: dict, source_root: Path) -> list[dict]:
    by_rel = _file_facts_by_rel(ast_facts, source_root)
    candidates: list[dict] = []
    seen: set[str] = set()

    for rel, facts in sorted(by_rel.items()):
        if rel in seen:
            continue
        path = source_root / rel
        entrypoints = facts.get("entrypoints") or []
        objects = facts.get("objects") or []
        spark = facts.get("spark_session_created", False)
        job_named = bool(_JOB_NAME_RE.search(rel))
        notebook = path.is_file() and _read_notebook_markers(path)

        if not entrypoints and not spark and not job_named and not notebook:
            continue

        ep_id = _slug(rel)
        if ep_id in seen:
            continue
        seen.add(ep_id)

        if entrypoints:
            ep = entrypoints[0]
            owner = ep.get("owner", objects[0] if objects else Path(rel).stem)
            method = ep.get("method", "main")
            call = f"{owner}::{method}"
            kind = "scala_object"
            entry_kind = "entrypoint_main"
            rationale = f"AST entrypoint {call} in {rel}"
            fqcn = _derive_fqcn(source_root, rel, owner)
        elif notebook:
            owner = Path(rel).stem
            method = "main"
            call = f"{owner}::main"
            kind = "notebook"
            entry_kind = "entrypoint_main"
            rationale = f"Databricks notebook markers in {rel}"
            fqcn = _derive_fqcn(source_root, rel, owner)
        elif job_named:
            owner = Path(rel).stem
            method = "main"
            call = f"{owner}::main"
            kind = "scala_object"
            entry_kind = "entrypoint_main"
            rationale = f"Job/pipeline/driver naming pattern in {rel}"
            fqcn = _derive_fqcn(source_root, rel, owner)
        else:
            owner = Path(rel).stem
            method = "main"
            call = f"{owner}::main"
            kind = "scala_object"
            entry_kind = "entrypoint_utility"
            rationale = f"SparkSession.builder detected in {rel}"
            fqcn = _derive_fqcn(source_root, rel, owner)

        candidates.append({
            "id": ep_id,
            "path": rel,
            "kind": kind,
            "entry_kind": entry_kind,
            "call": call,
            "rationale": rationale,
            "entrypoint_class": fqcn,
            "entrypoint_method": method,
        })
    return candidates


def _reads_for_entrypoint(facts: dict) -> list[dict]:
    reads: list[dict] = []
    seen: set[tuple] = set()
    # Counter for generating unique placeholder IDs for dynamic reads
    _dynamic_idx = [0]

    for r in facts.get("reads") or []:
        if not isinstance(r, dict):
            continue
        call = r.get("call", "")
        args = r.get("args") or []
        if args:
            for arg in args:
                if not arg:
                    continue
                key = (call, arg, False)
                if key in seen:
                    continue
                seen.add(key)
                category, method = _classify_read(call)
                entry = {
                    "call": call, "arg": arg, "category": category,
                    "reader_method": method, "line": r.get("line"),
                }
                sf = r.get("schema_fields")
                if isinstance(sf, list) and sf:
                    entry["schema_fields"] = sf
                reads.append(entry)
        else:
            # Empty args = dynamic path (variable or expression the analyzer could not
            # resolve to a string literal).  Still create a placeholder so the
            # data-synthesizer knows a read exists and can assign an llm_todo for it.
            _dynamic_idx[0] += 1
            placeholder = f"<dynamic_{call}_{_dynamic_idx[0]}>"
            key = (call, placeholder, True)
            if key not in seen:
                seen.add(key)
                category, method = _classify_read(call)
                entry = {
                    "call": call,
                    "arg": placeholder,
                    "category": category,
                    "reader_method": method,
                    "unresolved": True,
                    "line": r.get("line"),
                }
                sf = r.get("schema_fields")
                if isinstance(sf, list) and sf:
                    entry["schema_fields"] = sf
                reads.append(entry)

    # Unresolved reads from ScosAnalyze: dynamic path/table args that could not be
    # statically resolved. Create a placeholder source so the data-synthesizer knows
    # the endpoint exists and marks it with an llm_todo to confirm path + schema.
    for r in facts.get("unresolved_reads") or []:
        if not isinstance(r, dict):
            continue
        call = r.get("call", "")
        arg_expr = (r.get("arg_expr") or "").strip()
        if not arg_expr:
            continue
        key = (call, arg_expr, True)
        if key in seen:
            continue
        seen.add(key)
        category, method = _classify_read(call)
        reads.append({
            "call": call,
            "arg": arg_expr,  # dynamic expression used as path stub
            "category": category,
            "reader_method": method,
            "unresolved": True,
            "line": r.get("line"),
        })

    for ref in facts.get("table_refs") or []:
        if ref:
            key = ("table", ref, False)
            if key not in seen:
                seen.add(key)
                reads.append({
                    "call": "table",
                    "arg": ref,
                    "category": "table",
                    "reader_method": "table",
                })
    return reads


def _writes_for_entrypoint(facts: dict) -> list[dict]:
    writes: list[dict] = []
    seen: set[tuple] = set()

    for w in facts.get("writes") or []:
        if not isinstance(w, dict):
            continue
        call = w.get("call", "")
        for arg in w.get("args") or []:
            if not arg:
                continue
            key = (call, arg, False)
            if key in seen:
                continue
            seen.add(key)
            kind, method = _classify_write(call)
            writes.append({"call": call, "arg": arg, "kind": kind, "method": method})

    # Unresolved writes from ScosAnalyze: dynamic target args.
    for w in facts.get("unresolved_writes") or []:
        if not isinstance(w, dict):
            continue
        call = w.get("call", "")
        arg_expr = (w.get("arg_expr") or "").strip()
        if not arg_expr:
            continue
        key = (call, arg_expr, True)
        if key in seen:
            continue
        seen.add(key)
        kind, method = _classify_write(call)
        writes.append({
            "call": call,
            "arg": arg_expr,
            "kind": kind,
            "method": method,
            "unresolved": True,
            "line": w.get("line"),
        })

    return writes


def _mock_file_for_source(src_id: str, category: str, reader_method: str) -> str | None:
    if category != "file":
        return None
    ext = reader_method if reader_method in {"parquet", "csv", "json", "orc", "text"} else "csv"
    return f"{src_id}.{ext}"


_STRUCT_FIELD_START_RE = re.compile(
    r"""StructField\s*\(\s*["']([^"']+)["']\s*,\s*""",
    re.I,
)
_NAMED_STRUCT_START_RE = re.compile(
    r"""(?:val|var)\s+(\w+)\s*(?::\s*StructType)?\s*=\s*StructType\b""",
    re.I,
)
_TYPE_CTOR_MAP = {
    "stringtype": "string", "integertype": "int", "inttype": "int",
    "longtype": "long", "doubletype": "double", "floattype": "float",
    "booleantype": "boolean", "bytetype": "byte", "shorttype": "short",
    "binarytype": "binary", "datetype": "date", "timestamptype": "timestamp",
}


def _spark_type_from_text(raw: str) -> str:
    s = (raw or "").strip()
    m = re.match(r"DecimalType\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)", s, re.I)
    if m:
        return f"decimal({m.group(1)}{',' + m.group(2) if m.group(2) else ''})"
    m = re.match(r"ArrayType\s*\((.+)\)\s*$", s, re.I)
    if m:
        return f"array<{_spark_type_from_text(m.group(1))}>"
    key = re.sub(r"\W+", "", s).lower()
    for suffix, mapped in _TYPE_CTOR_MAP.items():
        if key.endswith(suffix):
            return mapped
    return "string"


def _balanced_slice(text: str, open_idx: int) -> str:
    """Return text[open_idx:close] covering a balanced ``(...)`` starting at open_idx."""
    if open_idx < 0 or open_idx >= len(text) or text[open_idx] != '(':
        return ""
    depth = 0
    end = open_idx
    while end < len(text):
        if text[end] == '(':
            depth += 1
        elif text[end] == ')':
            depth -= 1
            if depth == 0:
                return text[open_idx:end + 1]
        end += 1
    return text[open_idx:]


def _parse_struct_field_at(text: str, start: int) -> tuple[dict | None, int]:
    """Parse one StructField(...) at *start*; return (field, index after field)."""
    m = _STRUCT_FIELD_START_RE.match(text, start)
    if not m:
        return None, start
    name = m.group(1)
    i = m.end()
    depth = 0
    type_start = i
    while i < len(text):
        ch = text[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            if depth == 0:
                break
            depth -= 1
        elif ch == ',' and depth == 0:
            break
        i += 1
    ftype = _spark_type_from_text(text[type_start:i])
    nullable = True
    if i < len(text) and text[i] == ',':
        i += 1
        nm = re.match(r"\s*(?:nullable\s*=\s*)?(true|false)", text[i:], re.I)
        if nm:
            nullable = nm.group(1).lower() == "true"
            i += nm.end()
    while i < len(text) and text[i] != ')':
        i += 1
    if i < len(text) and text[i] == ')':
        i += 1
    return {"name": name, "type": ftype, "nullable": nullable}, i


def _fields_from_struct_body(body: str) -> list[dict]:
    fields: list[dict] = []
    i = 0
    while i < len(body):
        m = re.search(r"StructField\s*\(", body[i:], re.I)
        if not m:
            break
        abs_start = i + m.start()
        field, end = _parse_struct_field_at(body, abs_start)
        if field:
            fields.append(field)
        i = end if end > abs_start else abs_start + 1
    return fields


def _extract_struct_schemas_from_source(text: str) -> list[dict]:
    """Python fallback when jar has not yet emitted ``struct_schemas``."""
    out: list[dict] = []
    if not text:
        return out
    for m in _NAMED_STRUCT_START_RE.finditer(text):
        paren = text.find('(', m.end())
        if paren < 0:
            continue
        body = _balanced_slice(text, paren)
        fields = _fields_from_struct_body(body)
        if fields:
            out.append({"name": m.group(1), "fields": fields})
    return out


def _normalize_schema_fields(raw_fields: list) -> list[dict]:
    out: list[dict] = []
    for f in raw_fields or []:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        out.append({
            "name": f["name"],
            "type": f.get("type") or "string",
            "nullable": f.get("nullable", True) if "nullable" in f else True,
        })
    return out


def _collect_named_structs(facts: dict, extra_facts: list[dict], source_text: str) -> dict[str, list[dict]]:
    """Merge jar ``struct_schemas`` + Python regex fallback into name→fields."""
    named: dict[str, list[dict]] = {}
    for f in [facts] + list(extra_facts or []):
        for ss in f.get("struct_schemas") or []:
            if isinstance(ss, dict) and ss.get("name") and ss.get("fields"):
                cols = _normalize_schema_fields(ss["fields"])
                if cols:
                    named[ss["name"]] = cols
    for ss in _extract_struct_schemas_from_source(source_text):
        if ss["name"] not in named and ss.get("fields"):
            named[ss["name"]] = _normalize_schema_fields(ss["fields"])
    return named


def _schema_for_read(
    rd: dict,
    column_refs: list[str],
    named_structs: dict[str, list[dict]],
    *,
    multi_source: bool,
    attributed_cols: list[str] | None = None,
) -> tuple[list[dict], str | None, str]:
    """Return (schema, origin, llm_todo_or_empty)."""
    sf = rd.get("schema_fields")
    if isinstance(sf, list) and sf:
        cols = _normalize_schema_fields(sf)
        if cols:
            return cols, "structtype", ""

    bare = _bare_name(rd.get("arg") or "").lower()
    if bare and named_structs:
        bare_cmp = bare.replace("_", "")
        for n, fields in named_structs.items():
            nlow = n.lower().replace("_", "")
            if bare_cmp and (bare_cmp in nlow or nlow.startswith(bare_cmp)):
                cols = _normalize_schema_fields(fields)
                if cols:
                    return cols, "structtype_named", ""

    # Layer C: DF-attributed input columns for this read arg
    if attributed_cols:
        schema = _schema_from_columns(attributed_cols, llm_todo="")
        if schema:
            return schema, "df_attribution", (
                "" if not multi_source else
                "confirm column types from use-sites (df-attributed; types default string)"
            )

    schema = _schema_from_columns(column_refs, llm_todo="")
    if not schema:
        return [], None, (
            "no column_refs attributed to this source; declare schema columns "
            "(or confirm non-tabular document_schema for file blobs)"
        )
    if multi_source:
        return schema, "column_refs_shared", (
            "column_refs mined file-wide — confirm which columns belong to this "
            "source and upgrade types from use-sites (sum/arithmetic -> numeric, "
            "date filters -> date/timestamp)"
        )
    return schema, "column_refs", (
        "confirm column types from use-sites (all columns default to string)"
    )


def _source_columns_index(facts: dict, extra_facts: list[dict] | None = None) -> dict[str, list[str]]:
    """Map read arg → attributed input columns from jar Layer C ``source_columns``."""
    out: dict[str, list[str]] = {}
    for f in [facts] + list(extra_facts or []):
        for sc in f.get("source_columns") or []:
            if not isinstance(sc, dict):
                continue
            arg = sc.get("arg")
            cols = [c for c in (sc.get("input_cols") or []) if isinstance(c, str) and c]
            if arg and cols:
                # Prefer richer attribution if the same arg appears twice
                prev = out.get(arg) or []
                if len(cols) >= len(prev):
                    out[str(arg)] = cols
    return out


def _build_source_catalog(
    ep_reads: list[dict],
    column_refs: list[str],
    *,
    named_structs: dict[str, list[dict]] | None = None,
    per_read_column_refs: dict[int, list[str]] | None = None,
    source_columns: dict[str, list[str]] | None = None,
) -> tuple[list[dict], list[str]]:
    """Build external_sources with per-read schema attribution (Layer A + C)."""
    catalog: dict[str, dict] = {}
    todos: list[str] = []
    named = named_structs or {}
    multi = len(ep_reads) > 1
    src_cols = source_columns or {}

    for idx, rd in enumerate(ep_reads):
        raw = rd["arg"]
        src_id = _source_id("src", raw)
        local_cols = (per_read_column_refs or {}).get(idx, column_refs)
        attributed = src_cols.get(str(raw)) or src_cols.get(raw)
        schema, origin, type_todo = _schema_for_read(
            rd, local_cols, named, multi_source=multi, attributed_cols=attributed,
        )
        if src_id in catalog:
            existing = catalog[src_id]
            if origin in ("structtype", "df_attribution") and schema:
                existing["schema"] = schema
                existing["schema_origin"] = origin
                if origin == "structtype":
                    existing.pop("llm_todo", None)
            continue
        category = rd["category"]
        entry: dict[str, Any] = {
            "id": src_id,
            "name": _bare_name(raw) or src_id,
            "category": category,
            "original_path": raw,
            "reader_method": rd["reader_method"],
            "reader_options": {},
            "schema": schema,
        }
        if origin:
            entry["schema_origin"] = origin
        mock = _mock_file_for_source(src_id, category, rd["reader_method"])
        if mock:
            entry["mock_file"] = mock
        if rd.get("unresolved"):
            call_desc = rd.get("call", "read")
            entry["llm_todo"] = (
                f"path is dynamic (expression: `{raw}` passed to `{call_desc}`); "
                "confirm the real source path/table, declare schema columns, and "
                "set mock_file to a representative data file"
            )
            todos.append(f"{src_id}: dynamic path — confirm source")
        elif type_todo and origin not in ("structtype", "df_attribution"):
            entry["llm_todo"] = type_todo
            todos.append(f"{src_id}: {type_todo.split(';')[0][:60]}")
        elif type_todo and origin == "df_attribution" and "confirm column types" in type_todo:
            entry["llm_todo"] = type_todo
            todos.append(f"{src_id}: confirm types")
        catalog[src_id] = entry

    return list(catalog.values()), todos


def _build_sink_catalog(
    ep_writes: list[dict],
    write_helpers: list[str],
    column_refs: list[str],
) -> tuple[list[dict], list[str]]:
    catalog: dict[str, dict] = {}
    todos: list[str] = []

    for wr in ep_writes:
        raw = wr["arg"]
        sink_id = _source_id("sink", raw)
        if sink_id in catalog:
            continue
        schema = _schema_from_columns(column_refs, llm_todo="")
        entry: dict[str, Any] = {
            "id": sink_id,
            "name": _bare_name(raw) or sink_id,
            "kind": wr["kind"],
            "method": wr["method"],
            "original_target": raw,
            "schema": schema,
            "natural_keys": [],
            "llm_todo": "declare natural_keys for stable A/B comparison (or [] if none)",
        }
        if not schema:
            entry["llm_todo"] = (
                "sink schema could not be mined; declare output columns and natural_keys"
            )
        todos.append(f"{sink_id}: natural_keys + output schema")
        catalog[sink_id] = entry

    for helper in write_helpers or []:
        sink_id = _source_id("sink", helper)
        if sink_id in catalog:
            continue
        entry = {
            "id": sink_id,
            "name": helper,
            "kind": "table",
            "method": "write_helper",
            "original_target": helper,
            "schema": _schema_from_columns(column_refs, llm_todo=""),
            "natural_keys": [],
            "llm_todo": (
                f"write_helper '{helper}' delegates to a writer — declare the real "
                "sink target, output schema, and natural_keys"
            ),
        }
        todos.append(f"{sink_id}: resolve write_helper target")
        catalog[sink_id] = entry

    return list(catalog.values()), todos


def _parse_isin_args(raw: str) -> list:
    """Extract literal scalars from an ``.isin(...)`` argument blob."""
    out: list = []
    for m in _STR_LIT_RE.finditer(raw or ""):
        out.append(m.group(1))
    if out:
        return out
    # bare numeric / boolean literals (no quotes)
    for tok in re.split(r"\s*,\s*", (raw or "").strip()):
        tok = tok.strip()
        if re.fullmatch(r"-?\d+", tok):
            out.append(int(tok))
        elif re.fullmatch(r"-?\d+\.\d+", tok):
            out.append(float(tok))
        elif tok.lower() in ("true", "false"):
            out.append(tok.lower() == "true")
    return out


def _coerce_lit(raw: str | None, *, as_str: str | None = None,
                as_num: str | None = None) -> object | None:
    if as_str is not None:
        return as_str
    if as_num is not None:
        if as_num.lower() == "true":
            return True
        if as_num.lower() == "false":
            return False
        if re.fullmatch(r"-?\d+", as_num):
            return int(as_num)
        if re.fullmatch(r"-?\d+\.\d+", as_num):
            return float(as_num)
        return as_num
    if raw is None:
        return None
    return raw


def _mine_filter_join_regex(source_text: str) -> tuple[dict[str, list], set[str]]:
    """Regex hedge: literal ``===`` / ``isin`` / SQL filter / ``Seq`` join keys.

    Returns ``(col_values, join_keys)``. Complex predicates are ignored.
    """
    col_values: dict[str, list] = {}
    join_keys: set[str] = set()
    if not source_text:
        return col_values, join_keys

    # Strip negated isin spans so we don't seed excluded domains
    scrubbed = _NEGATED_ISIN_RE.sub(" /*negated_isin*/ ", source_text)

    for m in _EQ_LIT_RE.finditer(scrubbed):
        col = m.group(1) or m.group(2)
        val = _coerce_lit(None, as_str=m.group(3), as_num=m.group(4))
        if col and val is not None:
            col_values.setdefault(col, [])
            if val not in col_values[col]:
                col_values[col].append(val)

    for m in _ISIN_RE.finditer(scrubbed):
        col = m.group(1) or m.group(2)
        vals = _parse_isin_args(m.group(3) or "")
        if col and vals:
            bucket = col_values.setdefault(col, [])
            for v in vals:
                if v not in bucket:
                    bucket.append(v)

    for m in _FILTER_SQL_RE.finditer(scrubbed):
        col = m.group(1)
        val = _coerce_lit(None, as_str=m.group(2) or m.group(3), as_num=m.group(4))
        if col and val is not None:
            col_values.setdefault(col, [])
            if val not in col_values[col]:
                col_values[col].append(val)

    for m in _JOIN_SEQ_RE.finditer(scrubbed):
        for km in _STR_LIT_RE.finditer(m.group(1) or ""):
            join_keys.add(km.group(1))

    for m in _JOIN_STR_RE.finditer(scrubbed):
        if m.group(1):
            join_keys.add(m.group(1))

    for m in _JOIN_EQ_COLS_RE.finditer(scrubbed):
        for g in m.groups():
            if g:
                join_keys.add(g)

    return col_values, join_keys


def _facts_filters_joins(facts: dict) -> tuple[dict[str, list], set[str]]:
    """Pull Scalameta ``filters[]`` / ``joins[]`` from one ast_facts file dict."""
    col_values: dict[str, list] = {}
    join_keys: set[str] = set()

    for f in facts.get("filters") or []:
        if not isinstance(f, dict):
            continue
        col = f.get("col")
        vals = f.get("values")
        if not col or not isinstance(vals, list) or not vals:
            continue
        bucket = col_values.setdefault(str(col), [])
        for v in vals:
            if v not in bucket:
                bucket.append(v)

    for j in facts.get("joins") or []:
        if not isinstance(j, dict):
            continue
        for k in j.get("join_keys") or []:
            if isinstance(k, str) and k:
                join_keys.add(k)

    return col_values, join_keys


def _merge_col_values(dst: dict[str, list], src: dict[str, list]) -> None:
    for col, vals in src.items():
        bucket = dst.setdefault(col, [])
        for v in vals:
            if v not in bucket:
                bucket.append(v)


def _collect_filter_join_enrichment(
    facts: dict,
    *,
    source_text: str = "",
    extra_facts: list[dict] | None = None,
) -> tuple[dict[str, list], set[str]]:
    """Prefer Scalameta facts; regex fills only columns/keys still missing."""
    col_values: dict[str, list] = {}
    join_keys: set[str] = set()

    for f in [facts] + list(extra_facts or []):
        cv, jk = _facts_filters_joins(f)
        _merge_col_values(col_values, cv)
        join_keys |= jk

    if source_text:
        r_cv, r_jk = _mine_filter_join_regex(source_text)
        # Gaps only: do not override / extend a column Scalameta already seeded
        for col, vals in r_cv.items():
            if col not in col_values:
                col_values[col] = list(vals)
        # Join keys are additive (Scalameta + regex union)
        join_keys |= r_jk

    return col_values, join_keys


def _enrich_sources_with_filter_join(
    sources: list[dict],
    col_values: dict[str, list],
    join_keys: set[str],
) -> list[dict]:
    """Set ``values`` / ``join_key`` on matching schema columns (in-place).

    Filter columns with literal values that are absent from a source schema are
    appended (so mocks exercise the filter). Join keys only mark columns that
    already exist on a source — never invent a join column on every table.
    """
    if not col_values and not join_keys:
        return sources

    for src in sources:
        if not isinstance(src, dict):
            continue
        schema = src.get("schema")
        if not isinstance(schema, list):
            schema = []
            src["schema"] = schema
        by_name = {
            c["name"]: c for c in schema
            if isinstance(c, dict) and isinstance(c.get("name"), str) and c["name"]
        }
        for col, vals in col_values.items():
            if col in by_name:
                existing = by_name[col].get("values")
                if isinstance(existing, list) and existing:
                    merged = list(existing)
                    for v in vals:
                        if v not in merged:
                            merged.append(v)
                    by_name[col]["values"] = merged
                else:
                    by_name[col]["values"] = list(vals)
            else:
                entry = {"name": col, "type": "string", "values": list(vals)}
                schema.append(entry)
                by_name[col] = entry
        for jk in join_keys:
            if jk in by_name:
                by_name[jk]["join_key"] = True
    return sources


def _joins_edges_from_keys(sources: list[dict], join_keys: set[str]) -> list[dict]:
    """Build ``{left,right}`` edges between sources that share a join-key column."""
    edges: list[dict] = []
    seen: set[tuple] = set()
    for jk in sorted(join_keys):
        holders = []
        for s in sources:
            if not isinstance(s, dict) or not s.get("id"):
                continue
            for c in s.get("schema") or []:
                if isinstance(c, dict) and c.get("name") == jk:
                    holders.append(s["id"])
                    break
        for i in range(len(holders)):
            for j in range(i + 1, len(holders)):
                left = f"{holders[i]}.{jk}"
                right = f"{holders[j]}.{jk}"
                key = tuple(sorted((left, right)))
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"left": left, "right": right})
    return edges


def _read_source_text(source_root: Path, rel: str) -> str:
    path = source_root / rel
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _merge_catalog(existing: list, new_items: list) -> list:
    by_id = {x["id"]: x for x in existing if isinstance(x, dict) and x.get("id")}
    for item in new_items:
        iid = item.get("id")
        if not iid:
            continue
        if iid not in by_id:
            by_id[iid] = item
        else:
            cur = by_id[iid]
            for key, val in item.items():
                if key == "llm_todo":
                    continue
                if key not in cur or cur[key] in (None, "", [], {}):
                    cur[key] = val
                # SKILL-FIX: also replace placeholder values like "<dynamic_table_1>"
                # with a concrete value from a later deep-analysis pass. Placeholders
                # are generated by ast_to_analysis when a path is dynamically computed
                # and cannot be statically resolved; they must not block a subsequent
                # run that resolved the same source with a real original_path.
                elif (
                    key in ("original_path", "name")
                    and isinstance(cur[key], str)
                    and "<" in cur[key]
                    and isinstance(val, str)
                    and "<" not in val
                    and val
                ):
                    cur[key] = val
            if item.get("llm_todo") and not cur.get("llm_todo"):
                cur["llm_todo"] = item["llm_todo"]
    return list(by_id.values())


def survey_analysis(ast_facts: dict, source_root: Path, analysis: dict | None = None) -> dict:
    out = dict(analysis or {})
    build_tool = _detect_build_tool(source_root)
    out["build_tool"] = build_tool
    out["source_roots"] = _detect_source_roots(source_root, build_tool)
    out["entrypoint_candidates"] = _build_candidates(ast_facts, source_root)
    out.setdefault("migration_issues", [])
    out["complete"] = False
    out["llm_todos"] = [
        "select entrypoints from entrypoint_candidates",
        "after selection, re-run ast_to_analysis.py --mode deep",
    ]
    return out


def deep_analysis(
    ast_facts: dict,
    source_root: Path,
    analysis: dict,
    *,
    merge: bool = True,
) -> dict:
    selected = analysis.get("entrypoints") or []
    if not selected:
        _die(2, "analysis.json has no selected entrypoints — run select-entrypoints first")

    by_rel = _file_facts_by_rel(ast_facts, source_root)
    global_sources: list[dict] = list(analysis.get("external_sources") or []) if merge else []
    global_sinks: list[dict] = list(analysis.get("sinks") or []) if merge else []
    all_todos: list[str] = []

    updated_eps: list[dict] = []
    for ep in selected:
        if not isinstance(ep, dict):
            continue
        ep = dict(ep)
        rel = ep.get("path") or ep.get("id", "")
        facts = by_rel.get(rel) or by_rel.get(rel.replace("\\", "/"))
        if not facts:
            ep.setdefault("llm_todo", f"no ast_facts for path '{rel}' — re-run analyze")
            all_todos.append(f"{ep.get('id')}: missing ast_facts")
            updated_eps.append(ep)
            continue

        col_refs = [c for c in (facts.get("column_refs") or []) if isinstance(c, str) and c]
        ep_reads = _reads_for_entrypoint(facts)
        ep_writes = _writes_for_entrypoint(facts)
        write_helpers = list(facts.get("write_helpers") or [])
        src_roots = analysis.get("source_roots") or ["src/main/scala"]

        # Transitive walk (I/O + filter/join + StructType from helper classes).
        transitive = _collect_transitive_facts(
            facts, by_rel, source_root, src_roots, max_depth=4
        )

        # Per-read column attribution: EP-file reads get EP column_refs; helper
        # reads get that helper's column_refs (Layer C lite).
        per_read_column_refs: dict[int, list[str]] = {
            i: list(col_refs) for i in range(len(ep_reads))
        }
        seen_read_keys = {(r.get("call"), r.get("arg")) for r in ep_reads}
        seen_write_keys = {(w.get("call"), w.get("arg")) for w in ep_writes}

        for dep_facts in transitive:
            dep_col_refs = [
                c for c in (dep_facts.get("column_refs") or [])
                if isinstance(c, str) and c
            ]
            col_refs = list(set(col_refs + dep_col_refs))
            for rd in _reads_for_entrypoint(dep_facts):
                key = (rd.get("call"), rd.get("arg"))
                if key in seen_read_keys:
                    continue
                seen_read_keys.add(key)
                per_read_column_refs[len(ep_reads)] = dep_col_refs or list(col_refs)
                ep_reads.append(rd)
            for wr in _writes_for_entrypoint(dep_facts):
                key = (wr.get("call"), wr.get("arg"))
                if key in seen_write_keys:
                    continue
                seen_write_keys.add(key)
                ep_writes.append(wr)
            write_helpers.extend(dep_facts.get("write_helpers") or [])

        # Backfill entrypoint_class / entrypoint_method if missing (candidates
        # populated before this fix or via manual editing may lack them).
        if not ep.get("entrypoint_class"):
            ep_ast = (facts.get("entrypoints") or [{}])[0]
            objects = facts.get("objects") or []
            owner = ep_ast.get("owner", objects[0] if objects else Path(rel).stem)
            ep["entrypoint_class"] = _derive_fqcn(source_root, rel, owner)
        if not ep.get("entrypoint_method"):
            ep_ast = (facts.get("entrypoints") or [{}])[0]
            ep["entrypoint_method"] = ep_ast.get("method", "main")

        # Layer A: StructType schemas from jar + Python regex fallback.
        source_text_early = _read_source_text(source_root, rel)
        for dep in transitive:
            dep_rel = _rel_path(dep.get("path", ""), source_root)
            extra = _read_source_text(source_root, dep_rel)
            if extra:
                source_text_early = source_text_early + "\n" + extra
        named_structs = _collect_named_structs(facts, list(transitive), source_text_early)
        source_columns = _source_columns_index(facts, list(transitive))

        sources, src_todos = _build_source_catalog(
            ep_reads,
            col_refs,
            named_structs=named_structs,
            per_read_column_refs=per_read_column_refs,
            source_columns=source_columns,
        )
        sinks, sink_todos = _build_sink_catalog(ep_writes, write_helpers, col_refs)

        # Layer B (PySpark parity): sqlglot lineage from spark.sql("...") literals.
        sql_bodies = _sql_bodies_from_facts(facts, list(transitive))
        if sql_bodies:
            lineage = _sql_lineage(sql_bodies)
            sources = _merge_sql_lineage_into_sources(sources, lineage)

        # Layer D: sqlframe replay — surface missing columns as llm_todos / schema fills.
        if sql_bodies:
            validation = _validate_with_sqlframe(sources, sql_bodies)
            sources, src_todos = _apply_sqlframe_validation(sources, src_todos, validation)
            ep["sqlframe_validation"] = {
                k: validation[k] for k in ("status", "queries_ok", "queries_failed", "reason")
                if k in validation
            }

        # Fix 3: enrich schemas with filter literal domains + join keys.
        # Prefer Scalameta filters[]/joins[]; regex on source text fills gaps.
        source_text = source_text_early

        col_values, join_key_set = _collect_filter_join_enrichment(
            facts, source_text=source_text, extra_facts=list(transitive),
        )
        sources = _enrich_sources_with_filter_join(sources, col_values, join_key_set)
        join_edges = _joins_edges_from_keys(sources, join_key_set)
        if join_edges:
            # Merge with any LLM-authored joins already on the entrypoint
            existing_joins = [
                j for j in (ep.get("joins") or [])
                if isinstance(j, dict) and j.get("left") and j.get("right")
            ]
            seen_j = {
                tuple(sorted((j["left"], j["right"]))) for j in existing_joins
            }
            for edge in join_edges:
                key = tuple(sorted((edge["left"], edge["right"])))
                if key not in seen_j:
                    existing_joins.append(edge)
                    seen_j.add(key)
            ep["joins"] = existing_joins

        global_sources = _merge_catalog(global_sources, sources)
        global_sinks = _merge_catalog(global_sinks, sinks)

        # Re-apply enrichment on the merged global catalog so values/join_key
        # survive when merge keeps a pre-existing schema list.
        _enrich_sources_with_filter_join(global_sources, col_values, join_key_set)

        # SKILL-FIX: store full source objects (not just IDs) in per-entrypoint
        # external_sources. The Scala harness reads from ep.external_sources and
        # merges with global sources by ID. When only IDs are stored, the harness
        # falls back to the global source where original_path = "<dynamic_table_N>"
        # (an unresolved placeholder), causing safeIdent to throw. Full objects keep
        # the per-entrypoint context (original_path, schema, mock_file) intact and
        # prevent the global placeholder from overriding with unsafe identifiers.
        ep["external_sources"] = sources
        ep["sinks"] = [s["id"] for s in sinks]
        ep["mock_data_dir"] = f"shared/mock_data/{ep.get('id', _slug(rel))}"
        ep.setdefault("run_mode", "script")
        ep.setdefault("import_roots", analysis.get("source_roots") or ["src/main/scala"])

        # Collect unsupported_constructs from direct + transitive facts (new AST fields)
        all_uc: list[dict] = []
        for _f in [facts] + list(transitive):
            all_uc.extend(_f.get("unsupported_constructs") or [])
        if all_uc:
            ep["unsupported_constructs"] = all_uc

        # Generate llm_todos for risk signals from the new AST fields
        uc_todos: list[str] = []
        for sql in (facts.get("sql_calls") or []):
            if sql.get("has_current_date"):
                uc_todos.append(
                    f"sql uses CURRENT_DATE (line {sql.get('line')}) — rewrite for Snowflake"
                )
            if sql.get("has_qualify"):
                uc_todos.append(
                    f"sql uses QUALIFY (line {sql.get('line')}) — verify Snowflake support"
                )
        if facts.get("udfs"):
            udf_names = [u.get("name", "") for u in (facts.get("udfs") or [])]
            uc_todos.append(
                f"UDFs detected: {udf_names} — must be re-registered in SCOS environment"
            )
        if facts.get("streaming"):
            uc_todos.append("streaming operations detected — not supported in Phase B SCOS")
        for io_entry in (facts.get("external_io") or []):
            uc_todos.append(
                f"external I/O ({io_entry.get('kind')}) — remove or replace for Phase B"
            )

        ep_todos = src_todos + sink_todos + uc_todos
        if write_helpers:
            ep_todos.append(
                f"confirm write_helpers {write_helpers} have matching sinks[] entries"
            )
        if ep_todos:
            ep["llm_todo"] = "; ".join(ep_todos[:3])
            if len(ep_todos) > 3:
                ep["llm_todo"] += f" (+{len(ep_todos) - 3} more)"
        if all_uc:
            ep["complete"] = False
        all_todos.extend(ep_todos)
        updated_eps.append(ep)

    out = dict(analysis)
    out["entrypoints"] = updated_eps
    out["external_sources"] = global_sources
    out["sinks"] = global_sinks
    out["complete"] = not all_todos and not _remaining_llm_todos(out)
    out["llm_todos"] = sorted(set(all_todos))
    return out


def _remaining_llm_todos(analysis: dict) -> list[str]:
    found: list[str] = []
    for key in ("llm_todos",):
        for item in analysis.get(key) or []:
            if item:
                found.append(str(item))
    if analysis.get("llm_todo"):
        found.append(str(analysis["llm_todo"]))
    for ep in analysis.get("entrypoints") or []:
        if isinstance(ep, dict) and ep.get("llm_todo"):
            found.append(f"{ep.get('id')}: {ep['llm_todo']}")
    for coll_key in ("external_sources", "sinks"):
        for item in analysis.get(coll_key) or []:
            if isinstance(item, dict) and item.get("llm_todo"):
                found.append(f"{item.get('id')}: {item['llm_todo']}")
    return found


def run(
    conv_root: Path,
    *,
    mode: str = "auto",
    merge: bool = True,
) -> dict:
    shared = conv_root / "Validation" / "shared"
    source_root = conv_root / "Validation" / "source"
    ast_path = shared / "ast_facts.json"
    analysis_path = shared / "analysis.json"

    if not ast_path.is_file():
        _die(2, f"ast_facts.json not found: {ast_path}")
    if not source_root.is_dir():
        _die(2, f"Validation/source not found: {source_root}")

    ast_facts = _load_json(ast_path)
    analysis = _load_json(analysis_path) if analysis_path.is_file() else {}

    selected = analysis.get("entrypoints") or []
    if mode == "auto":
        mode = "deep" if selected else "survey"

    if mode == "survey":
        result = survey_analysis(ast_facts, source_root, analysis if merge else None)
    elif mode == "deep":
        if not selected:
            _die(2, "deep mode requires selected entrypoints[] in analysis.json")
        result = deep_analysis(ast_facts, source_root, analysis, merge=merge)
    else:
        _die(2, f"unknown mode: {mode}")

    shared.mkdir(parents=True, exist_ok=True)
    analysis_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ast_to_analysis.py",
        description="Convert ast_facts.json into an analysis.json skeleton with llm_todo markers.",
    )
    ap.add_argument("--conv-root", required=True, help="Conversion root containing Validation/")
    ap.add_argument(
        "--mode",
        choices=("auto", "survey", "deep"),
        default="auto",
        help="survey=entrypoint_candidates only; deep=per-entrypoint sources/sinks; auto=pick",
    )
    ap.add_argument(
        "--no-merge",
        action="store_true",
        help="Replace mined catalogs instead of merging into existing analysis.json",
    )
    args = ap.parse_args(argv)
    conv_root = Path(args.conv_root).expanduser().resolve()
    result = run(conv_root, mode=args.mode, merge=not args.no_merge)

    n_cand = len(result.get("entrypoint_candidates") or [])
    n_ep = len(result.get("entrypoints") or [])
    n_src = len(result.get("external_sources") or [])
    n_sink = len(result.get("sinks") or [])
    n_todo = len(_remaining_llm_todos(result))
    print(
        f"[ast_to_analysis.py] wrote analysis.json "
        f"(candidates={n_cand}, entrypoints={n_ep}, sources={n_src}, sinks={n_sink}, "
        f"llm_todos={n_todo}, complete={result.get('complete', False)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
