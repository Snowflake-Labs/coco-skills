#!/usr/bin/env python3
"""Deterministic chunk planner for Phase 1.1 (defer-adjudication) dispatch.

The Phase 1.1 adjudicator confirms-or-dismisses every ``kind ==
"needs_adjudication"`` row the analyzer produced in defer mode. Running it as a
SINGLE agent over a large workload overloads one context window: on an 89-file /
635-row workload the lone adjudicator degrades and over-confirms (it defaults
toward ``standard`` when it can't carefully trace a block). This planner splits
the deferred work into **bounded** chunks so each adjudicator worker sees only a
handful of files and rows — the same worker-pool pattern Phase 2 uses.

It reads ``analysis.json``, groups the ``needs_adjudication`` rows by file, and
bin-packs the files into chunks capped by BOTH ``--max-files-per-chunk`` and
``--max-rows-per-chunk`` (whichever binds first). Chunks are grouped into waves
of ``--max-parallel``. Workers write per-chunk verdict sidecars; the coordinator
merges them once via ``apply_adjudications.py`` (single writer of analysis.json).

Usage:
    python3 orchestrate_adjudication.py --analysis /path/to/analysis.json
    python3 orchestrate_adjudication.py --analysis a.json --max-files-per-chunk 8 \
        --max-rows-per-chunk 60 --max-parallel 4
"""

import argparse
import json
import sys
from pathlib import Path


def _load_rows(analysis_path: Path) -> list:
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    # tolerate {"issues": [...]} / {"results": [...]} shapes
    for v in data.values() if isinstance(data, dict) else []:
        if isinstance(v, list):
            return v
    return []


def _deferred_by_file(rows: list) -> dict:
    """{file -> count of needs_adjudication rows}, insertion-ordered by first
    appearance so the plan is deterministic."""
    by_file: dict = {}
    for r in rows:
        if r.get("kind") == "needs_adjudication" and not r.get("adjudicated"):
            f = r.get("file")
            if f:
                by_file[f] = by_file.get(f, 0) + 1
    return by_file


def _pack(by_file: dict, max_files: int, max_rows: int) -> list:
    """Greedy bin-pack files into chunks. Start a new chunk when adding a file
    would exceed max_files or max_rows. A single file whose row count alone
    exceeds max_rows still gets its own chunk (never split a file — the
    adjudicator needs the whole file's context)."""
    chunks: list = []
    cur_files: list = []
    cur_rows = 0
    for f, n in by_file.items():
        if cur_files and (len(cur_files) >= max_files or cur_rows + n > max_rows):
            chunks.append({"files": cur_files, "rows": cur_rows})
            cur_files, cur_rows = [], 0
        cur_files.append(f)
        cur_rows += n
    if cur_files:
        chunks.append({"files": cur_files, "rows": cur_rows})
    return chunks


def _self_check() -> None:
    # 3 files, caps 2 files / 5 rows -> expect the packing to bound each chunk.
    bf = {"a": 3, "b": 3, "c": 1}
    ch = _pack(bf, max_files=2, max_rows=5)
    assert all(c["rows"] <= 5 or len(c["files"]) == 1 for c in ch), ch
    assert all(len(c["files"]) <= 2 for c in ch), ch
    assert sum(len(c["files"]) for c in ch) == 3, ch
    # oversized single file gets its own chunk
    ch2 = _pack({"big": 100, "small": 1}, max_files=8, max_rows=60)
    assert ["big"] in [c["files"] for c in ch2], ch2


def main(argv=None) -> int:
    _self_check()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analysis", type=Path, required=True,
                    help="Path to analysis.json produced by the Phase-1 analyzer.")
    ap.add_argument("--max-files-per-chunk", type=int, default=8,
                    help="Max source files one adjudicator worker handles (default 8).")
    ap.add_argument("--max-rows-per-chunk", type=int, default=60,
                    help="Max needs_adjudication rows one worker handles (default 60).")
    ap.add_argument("--max-parallel", type=int, default=4,
                    help="Adjudicator workers dispatched concurrently per wave (default 4).")
    args = ap.parse_args(argv)

    if not args.analysis.is_file():
        print(f"ERROR: analysis.json not found: {args.analysis}", file=sys.stderr)
        return 3
    rows = _load_rows(args.analysis)
    by_file = _deferred_by_file(rows)
    total_files = len(by_file)
    total_rows = sum(by_file.values())

    if total_rows == 0:
        print("No needs_adjudication rows — Phase 1.1 is a no-op (skip).")
        print("ADJUDICATION_PLAN chunks=0 waves=0 deferred_files=0 deferred_rows=0")
        return 0

    chunks = _pack(by_file, max(1, args.max_files_per_chunk), max(1, args.max_rows_per_chunk))
    mp = max(1, args.max_parallel)

    print(f"Deferred adjudication: {total_rows} row(s) across {total_files} file(s) "
          f"-> {len(chunks)} chunk(s), caps={args.max_files_per_chunk}f/{args.max_rows_per_chunk}r, "
          f"max_parallel={mp}\n")
    for wave_start in range(0, len(chunks), mp):
        wave = chunks[wave_start:wave_start + mp]
        wave_no = wave_start // mp + 1
        print(f"WAVE {wave_no} ({len(wave)} chunk(s)):")
        for i, c in enumerate(wave, start=wave_start + 1):
            print(f"  CHUNK_ID={i} rows={c['rows']} files={len(c['files'])}")
            print(f"  CHUNK_FILES={','.join(c['files'])}")
        print()

    n_waves = (len(chunks) + mp - 1) // mp
    print(f"ADJUDICATION_PLAN chunks={len(chunks)} waves={n_waves} "
          f"deferred_files={total_files} deferred_rows={total_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
