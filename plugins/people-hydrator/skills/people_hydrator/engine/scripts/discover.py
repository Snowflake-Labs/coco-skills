#!/usr/bin/env python3
"""Backend discovery job for people_hydrator (discover once, reuse everywhere).

Full run (one crawl):
  discover.py --workspace W --connection C --database HR_DB [--schema PUBLIC]
      [--context-docs DIR --context-connector local_files]
Topic-confirmed run (v3 Discover -> Bind): profile only the tables confirmed per topic
in W/topics.json; tables profiled earlier are reused, so adding skills later is incremental:
  discover.py --workspace W --connection C --from-topics
Re-match only (after context confirmation / analyst corrections / adding skills):
  discover.py --workspace W --rematch

Steps: profile -> usage -> context (optional docs) -> graph -> match -> tuning.
Progress is appended as JSON lines to W/progress.jsonl (the wizard tails it).
Bindings are produced for EVERY catalog skill, so selecting another skill later
never requires a new crawl.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts, however this file is run

SCRIPTS = Path(__file__).resolve().parent
CATALOG = SCRIPTS.parents[1] / "catalog" / "people.json"


def log(workspace: Path, step: str, status: str, **detail: object) -> None:
    event = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "step": step, "status": status, **detail}
    with (workspace / "progress.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
    print(json.dumps(event), flush=True)


def run(workspace: Path, step: str, args: list[str]) -> None:
    log(workspace, step, "started")
    start = time.time()
    proc = subprocess.run([sys.executable, str(SCRIPTS / args[0]), *args[1:]],
                          capture_output=True, text=True, check=False)
    for line in proc.stderr.splitlines():
        if line.startswith("{"):
            try:
                log(workspace, step, "progress", **json.loads(line))
            except json.JSONDecodeError:
                pass
    if proc.returncode != 0:
        log(workspace, step, "failed", error=(proc.stderr or proc.stdout)[-600:])
        raise SystemExit(f"{step} failed")
    log(workspace, step, "done", seconds=round(time.time() - start, 1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--connection")
    parser.add_argument("--database", action="append", default=[])
    parser.add_argument("--schema", action="append", default=[])
    parser.add_argument("--context-docs", type=Path)
    parser.add_argument("--context-connector", default="local_files")
    parser.add_argument("--rematch", action="store_true")
    parser.add_argument("--skip-usage", action="store_true")
    parser.add_argument("--table", action="append", default=[])
    parser.add_argument("--from-topics", action="store_true", help="profile the tables confirmed in W/topics.json")
    parser.add_argument("--keep-progress", action="store_true", help="append to progress.jsonl instead of starting fresh")
    args = parser.parse_args()

    ws = args.workspace.resolve()
    ws.mkdir(parents=True, exist_ok=True)
    index, ctx = ws / "data_index.json", ws / "context.json"
    started = time.time()
    tables = list(args.table)
    if args.from_topics:
        import discover_topics
        tables += discover_topics.confirmed_tables(ws)
        if not tables:
            raise SystemExit("no confirmed tables in topics.json; confirm at least one topic first")
    if not args.rematch:
        if not args.connection or not (args.database or tables):
            raise SystemExit("--connection and --database (or --table / --from-topics) are required")
        if not args.keep_progress:
            (ws / "progress.jsonl").write_text("", encoding="utf-8")
        profile = ["profile_data.py", "--connection", args.connection, "--out", str(index)]
        for db in args.database:
            profile += ["--database", db]
        for schema in args.schema:
            profile += ["--schema", schema]
        for t in tables:
            profile += ["--table", t]
        if tables:
            profile.append("--merge")
        run(ws, "profile", profile)
        if not args.skip_usage:
            run(ws, "usage", ["mine_usage.py", "--connection", args.connection, "--index", str(index)])
    elif not index.exists():
        raise SystemExit("no data_index.json yet; run a full discovery first")

    if args.context_docs:
        raw = ws / "context_raw" / f"{args.context_connector}.json"
        run(ws, "context_extract", ["extract_context.py", "--path", str(args.context_docs),
                                    "--connector", args.context_connector,
                                    "--scope", str(args.context_docs), "--out", str(raw)])
        run(ws, "context_ingest", ["context_store.py", "ingest", "--context", str(ctx),
                                   "--items", str(raw), "--org-allowlist", str(index)])
    if not ctx.exists():
        ctx.write_text(json.dumps({"version": 1, "sources": [], "items": []}) + "\n", encoding="utf-8")

    run(ws, "graph", ["build_context_graph.py", "--index", str(index), "--context", str(ctx),
                      "--out", str(ws / "context_graph.json")])
    match = ["match_fields.py", "--index", str(index), "--graph", str(ws / "context_graph.json"),
             "--catalog", str(CATALOG), "--out", str(ws / "bindings.json"),
             "--inventory-out", str(ws / "account_inventory.json"), "--context", str(ctx)]
    if (ws / "learned.json").exists():
        match += ["--learned", str(ws / "learned.json")]
    if (ws / "topics.json").exists():
        match += ["--topics", str(ws / "topics.json")]
    run(ws, "match", match)
    run(ws, "tuning", ["build_tuning.py", "--catalog", str(CATALOG), "--index", str(index),
                       "--bindings", str(ws / "bindings.json"), "--context", str(ctx),
                       "--out-dir", str(ws / "tuning")])
    run(ws, "readiness", ["readiness.py", "--workspace", str(ws), "--catalog", str(CATALOG)])
    log(ws, "discovery", "complete", seconds=round(time.time() - started, 1), rematch=args.rematch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
