#!/usr/bin/env python3
"""Java analyzer gate: column_refs + write_helpers coverage vs analysis.json.

Replaces the manual LLM body-scan. Run after ``datagen.py`` (unchanged
PySpark script) and before recording ``analyzer_deep``.

Usage:
  column_check.py --conv-root $CONVERSION_ROOT
  column_check.py --ast-facts path --analysis path [--source-root path]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _die(code: int, msg: str) -> None:
    print(f"[column_check.py] error: {msg}", file=sys.stderr)
    sys.exit(code)


def _load_json(path: Path):
    """Load JSON with a clear error instead of an opaque traceback.

    ast_facts.json / analysis.json are produced by the external scos-analyze.jar;
    a partial write or bad encoding would otherwise crash with a raw JSONDecodeError.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        _die(2, f"cannot parse {path.name}: {e}")


def _analysis_source_catalog(analysis: dict) -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    for src in analysis.get("external_sources") or []:
        if isinstance(src, dict) and src.get("id"):
            catalog[src["id"]] = src
    return catalog


def _analysis_sink_catalog(analysis: dict) -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    for sink in analysis.get("sinks") or []:
        if isinstance(sink, dict) and sink.get("id"):
            catalog[sink["id"]] = sink
    return catalog


def _resolve_ep_sources(ep: dict, src_catalog: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    for item in ep.get("external_sources") or []:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str) and item in src_catalog:
            out.append(src_catalog[item])
    return out


def _resolve_ep_sinks(ep: dict, sink_catalog: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    for item in ep.get("sinks") or []:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str) and item in sink_catalog:
            out.append(sink_catalog[item])
    return out


def _ast_facts_by_rel(ast_facts: dict, source_root: Path) -> dict[str, dict]:
    by_rel: dict[str, dict] = {}
    for f in ast_facts.get("files") or []:
        if not isinstance(f, dict) or not f.get("parse_ok", True):
            continue
        raw = f.get("path") or ""
        p = Path(raw)
        try:
            rel = str(p.resolve().relative_to(source_root.resolve()))
        except ValueError:
            rel = str(p)
        by_rel[rel] = f
        by_rel[rel.replace("\\", "/")] = f
    return by_rel


def check_columns(
    ast_facts: dict,
    analysis: dict,
    *,
    source_root: Path | None = None,
) -> list[str]:
    """Verify column_refs and write_helpers against analysis.json declarations."""
    problems: list[str] = []
    src_catalog = _analysis_source_catalog(analysis)
    sink_catalog = _analysis_sink_catalog(analysis)
    by_rel = _ast_facts_by_rel(ast_facts, source_root) if source_root else {}

    for ep in analysis.get("entrypoints") or []:
        if not isinstance(ep, dict):
            continue
        ep_id = ep.get("id") or "?"
        rel = ep.get("path") or ep_id
        facts = by_rel.get(rel) or by_rel.get(str(rel).replace("\\", "/"))
        if not facts:
            problems.append("%s: no ast_facts for path '%s'" % (ep_id, rel))
            continue

        col_refs = sorted({
            c for c in (facts.get("column_refs") or [])
            if isinstance(c, str) and c.strip()
        })
        sources = _resolve_ep_sources(ep, src_catalog)
        relational = [
            s for s in sources
            if s.get("category") in (None, "table", "file", "jdbc", "snowflake")
            and s.get("relational", True) is not False
            and isinstance(s.get("schema"), list)
        ]
        declared = {
            (c.get("name") or c.get("column") or "").lower()
            for s in relational
            for c in (s.get("schema") or [])
            if isinstance(c, dict) and (c.get("name") or c.get("column"))
        }
        missing = [c for c in col_refs if c.lower() not in declared]
        if missing and relational:
            problems.append(
                "%s: column_refs not declared in any external_sources schema: %s"
                % (ep_id, ", ".join(missing))
            )
        elif missing and not relational:
            problems.append(
                "%s: column_refs %s but no relational external_sources with schema declared"
                % (ep_id, ", ".join(missing))
            )

        write_helpers = {
            h for h in (facts.get("write_helpers") or [])
            if isinstance(h, str) and h.strip()
        }
        if write_helpers:
            sinks = _resolve_ep_sinks(ep, sink_catalog)
            sink_ids = {(s.get("id") or s.get("name") or "").lower() for s in sinks}
            sink_names = {(s.get("name") or s.get("id") or "").lower() for s in sinks}
            for helper in sorted(write_helpers):
                hlow = helper.lower()
                if hlow not in sink_ids and hlow not in sink_names:
                    problems.append(
                        "%s: write_helper '%s' has no matching sinks[] entry"
                        % (ep_id, helper)
                    )
    return problems


def run(
    *,
    ast_facts: dict,
    analysis: dict,
    source_root: Path | None = None,
) -> dict:
    problems = check_columns(ast_facts, analysis, source_root=source_root)
    return {"ok": not problems, "problems": problems}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="column_check.py",
        description="Java body-scan gate: ast_facts column_refs vs analysis.json schemas.",
    )
    ap.add_argument("--conv-root", default=None, help="Conversion root containing Validation/")
    ap.add_argument("--ast-facts", default=None)
    ap.add_argument("--analysis", default=None)
    ap.add_argument("--source-root", default=None)
    ap.add_argument("--json", action="store_true", help="Print full JSON report")
    args = ap.parse_args(argv)

    if args.conv_root:
        conv_root = Path(args.conv_root).expanduser().resolve()
        ast_path = conv_root / "Validation" / "shared" / "ast_facts.json"
        analysis_path = conv_root / "Validation" / "shared" / "analysis.json"
        source_root = conv_root / "Validation" / "source"
    else:
        if not args.ast_facts or not args.analysis:
            _die(2, "provide --conv-root or both --ast-facts and --analysis")
        ast_path = Path(args.ast_facts).expanduser().resolve()
        analysis_path = Path(args.analysis).expanduser().resolve()
        source_root = Path(args.source_root).expanduser().resolve() if args.source_root else None

    if not ast_path.is_file():
        _die(2, f"ast_facts not found: {ast_path}")
    if not analysis_path.is_file():
        _die(2, f"analysis.json not found: {analysis_path}")

    ast_facts = _load_json(ast_path)
    analysis = _load_json(analysis_path)
    report = run(ast_facts=ast_facts, analysis=analysis, source_root=source_root)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if report["ok"]:
            print("[column_check] verify OK")
        else:
            for p in report["problems"]:
                print(f"[column_check] {p}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
