"""Deterministic bridge: ast_facts.json -> analysis.json skeleton.

PySpark's schema_mine.py mines Python source directly and emits a *completable
contract* with ``llm_todo`` markers. Java splits the pipeline:

  ast_facts.jar analyze  -> ast_facts.json   (deterministic)
  ast_to_analysis.py     -> analysis.json    (this script — deterministic skeleton)
  LLM                    -> fills llm_todo gaps only
  schema_mine.py         -> schemas/         (deterministic)

Modes:
  survey — entrypoint_candidates, source_roots, build_tool (no selected entrypoints)
  deep   — per-selected-entrypoint external_sources, sinks, schemas (with llm_todo)
  auto   — survey when entrypoints[] absent, else deep (default)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_FILE_READERS = {"parquet", "csv", "json", "orc", "text", "textfile", "load"}
_TABLE_READERS = {"table", "jdbc"}
_WRITE_TABLE = {"savetable", "insertinto"}
_WRITE_FILE = {"parquet", "csv", "json", "orc", "text", "save"}

_JOB_NAME_RE = re.compile(r"(Pipeline|Job|Driver)\.java$", re.I)
_NOTEBOOK_MARKERS = (
    "// Databricks notebook source",
    "// COMMAND ---------",
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
    candidates = ["src/main/java", "src/main/scala"]
    found = [c for c in candidates if (source_root / c).is_dir()]
    if found:
        return found
    if build_tool == "unknown" and any(source_root.glob("*.java")):
        return ["."]
    return found or ["src/main/java"]


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


def _build_candidates(ast_facts: dict, source_root: Path) -> list[dict]:
    by_rel = _file_facts_by_rel(ast_facts, source_root)
    candidates: list[dict] = []
    seen: set[str] = set()

    for rel, facts in sorted(by_rel.items()):
        if rel in seen:
            continue
        path = source_root / rel
        entrypoints = facts.get("entrypoints") or []
        objects = facts.get("classes") or []
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
            call = f"{owner}.{method}"
            kind = "java_class"
            entry_kind = "entrypoint_main"
            rationale = f"AST entrypoint {call} in {rel}"
        elif notebook:
            call = f"{Path(rel).stem}.main"
            kind = "notebook"
            entry_kind = "entrypoint_main"
            rationale = f"Databricks notebook markers in {rel}"
        elif job_named:
            call = f"{Path(rel).stem}.main"
            kind = "java_class"
            entry_kind = "entrypoint_main"
            rationale = f"Job/pipeline/driver naming pattern in {rel}"
        else:
            call = f"{Path(rel).stem}.main"
            kind = "java_class"
            entry_kind = "entrypoint_utility"
            rationale = f"SparkSession.builder detected in {rel}"

        candidates.append({
            "id": ep_id,
            "path": rel,
            "kind": kind,
            "entry_kind": entry_kind,
            "call": call,
            "rationale": rationale,
        })
    return candidates


def _reads_for_entrypoint(facts: dict) -> list[dict]:
    reads: list[dict] = []
    seen: set[tuple] = set()

    for r in facts.get("reads") or []:
        if not isinstance(r, dict):
            continue
        call = r.get("call", "")
        for arg in r.get("args") or []:
            if not arg:
                continue
            key = (call, arg, False)
            if key in seen:
                continue
            seen.add(key)
            category, method = _classify_read(call)
            reads.append({"call": call, "arg": arg, "category": category, "reader_method": method})

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


def _build_source_catalog(
    ep_reads: list[dict],
    column_refs: list[str],
) -> tuple[list[dict], list[str]]:
    catalog: dict[str, dict] = {}
    todos: list[str] = []

    for rd in ep_reads:
        raw = rd["arg"]
        src_id = _source_id("src", raw)
        if src_id in catalog:
            continue
        category = rd["category"]
        schema = _schema_from_columns(column_refs, llm_todo="")
        entry: dict[str, Any] = {
            "id": src_id,
            "name": _bare_name(raw) or src_id,
            "category": category,
            "original_path": raw,
            "reader_method": rd["reader_method"],
            "reader_options": {},
            "schema": schema,
        }
        mock = _mock_file_for_source(src_id, category, rd["reader_method"])
        if mock:
            entry["mock_file"] = mock
        # Unresolved sources: path/table arg was a dynamic expression that could
        # not be statically resolved. Flag prominently so the data-synthesizer
        # knows to confirm the real source path, schema, and mock file.
        if rd.get("unresolved"):
            call_desc = rd.get("call", "read")
            entry["llm_todo"] = (
                f"path is dynamic (expression: `{raw}` passed to `{call_desc}`); "
                "confirm the real source path/table, declare schema columns, and "
                "set mock_file to a representative data file"
            )
            todos.append(f"{src_id}: dynamic path — confirm source")
        elif not schema:
            entry["llm_todo"] = (
                "no column_refs attributed to this source; declare schema columns "
                "(or confirm non-tabular document_schema for file blobs)"
            )
            todos.append(f"{src_id}: declare schema")
        elif len(ep_reads) > 1:
            entry["llm_todo"] = (
                "column_refs mined file-wide — confirm which columns belong to this "
                "source and upgrade types from use-sites (sum/arithmetic -> numeric, "
                "date filters -> date/timestamp)"
            )
            todos.append(f"{src_id}: confirm column attribution and types")
        else:
            entry["llm_todo"] = (
                "confirm column types from use-sites (all columns default to string)"
            )
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
        write_helpers = facts.get("write_helpers") or []

        sources, src_todos = _build_source_catalog(ep_reads, col_refs)
        sinks, sink_todos = _build_sink_catalog(ep_writes, write_helpers, col_refs)

        global_sources = _merge_catalog(global_sources, sources)
        global_sinks = _merge_catalog(global_sinks, sinks)

        ep["external_sources"] = [s["id"] for s in sources]
        ep["sinks"] = [s["id"] for s in sinks]
        ep["mock_data_dir"] = f"shared/mock_data/{ep.get('id', _slug(rel))}"
        ep.setdefault("run_mode", "script")
        ep.setdefault("import_roots", analysis.get("source_roots") or ["src/main/java"])
        ep_todos = src_todos + sink_todos
        if write_helpers:
            ep_todos.append(
                f"confirm write_helpers {write_helpers} have matching sinks[] entries"
            )
        if ep_todos:
            ep["llm_todo"] = "; ".join(ep_todos[:3])
            if len(ep_todos) > 3:
                ep["llm_todo"] += f" (+{len(ep_todos) - 3} more)"
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
