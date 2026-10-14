# flake8: noqa: T201
"""Phase 0.6: deterministically rewrite SCOS-incompatible SQL in standalone
``.sql`` files, before the Phase-1 analyzer runs.

Standalone ``.sql`` workloads are otherwise only *analyzed* — no phase rewrites
or annotates them. This step closes that gap for the mechanical cases:

* discover ``.sql`` files under ``migrated_dir`` (excluding Databricks
  native-JSON ``.sql`` notebooks, same rule as ``find_plain_sql_files``);
* run :func:`rag.sql_rewrite.rewrite_sql` over each;
* when there is anything to record, prepend a ``-- SCOS:`` audit block — one
  line per mechanical rewrite (with the original snippet) and one
  ``-- SCOS: TODO -`` line per residual (judgment-heavy) gap for the LLM fixer;
* write the rewritten body back and record ``sql_rewrite_edits`` +
  ``phases_completed["0_6_sql_rewrite"]`` in ``migration_state.json``.

Idempotent: a file already carrying the sentinel is skipped; ``rewrite_sql``
itself is a no-op on already-rewritten SQL. Unparseable SQL is left byte-identical.
``.sql`` files are intentionally NOT added to the manifest (that would feed them
to the Python-only phases — py_compile, chunking, the imports/coverage gates).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import notebook_io  # noqa: E402  (stdlib-only)
from notebook_io import MIGRATION_HEADER_MARKER  # noqa: E402
from rag.sql_rewrite import rewrite_sql  # noqa: E402

# Phase 0.6 stamps ONE header — the same `SCOS Migration Output` marker the
# Python/notebook header uses — so a .sql file gets a single coherent header
# (not a separate Phase-0.6 block plus a Phase-3 header). update_imports (Phase
# 3) recognizes this marker and leaves the file unchanged, avoiding a second
# stacked block. The marker also serves as the idempotency guard.
_SENTINEL = MIGRATION_HEADER_MARKER


def find_plain_sql_files(root: Path) -> list[Path]:
    """``.sql`` files under ``root`` that are NOT Databricks native-JSON
    notebooks (mirrors ``analyze_pyspark.find_plain_sql_files`` without importing
    the heavy analyzer)."""
    if root.is_file():
        if root.suffix.lower() == ".sql" and not notebook_io.is_notebook(str(root)):
            return [root]
        return []
    results: list[Path] = []
    for dirpath, _dirs, files in notebook_io.walk_filtered(str(root)):
        for fname in files:
            if Path(fname).suffix.lower() != ".sql":
                continue
            cand = Path(dirpath) / fname
            if not notebook_io.is_notebook(str(cand)):
                results.append(cand)
    return sorted(results)


def _snip(s: str, n: int = 300) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _build_header(filename: str, applied, residual) -> str:
    # ONE `-- SCOS Migration Output` header (the same marker the .py/notebook
    # header uses). The deduped `-- SCOS:` / `-- SCOS: TODO` lines live inside it
    # so they remain harvestable for Issues.csv; identical findings (the same gap
    # at several statements) collapse to one line tagged "[N×]".
    def _dedup(items, key):
        order, count, first = [], {}, {}
        for it in items:
            k = key(it)
            if k not in count:
                count[k] = 0
                first[k] = it
                order.append(k)
            count[k] += 1
        return [(first[k], count[k]) for k in order]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"-- {MIGRATION_HEADER_MARKER}",
        f"-- Source File: {filename}",
        f"-- Migrated on: {today}",
        f"-- SQL rewrite: {len(applied)} rewrite(s), {len(residual)} manual TODO(s)",
    ]
    for e, c in _dedup(applied, lambda e: (e.rule_id, e.note, e.before)):
        tag = f" [{c}×]" if c > 1 else ""
        lines.append(f"-- SCOS: [{e.rule_id}]{tag} {e.note}")
        lines.append(f"--   original: {_snip(e.before)}")
    for f, c in _dedup(residual, lambda f: (f.rule_id, f.note)):
        tag = f" [{c}×]" if c > 1 else ""
        lines.append(f"-- SCOS: TODO - [{f.rule_id}]{tag} {_snip(f.note)}")
    return "\n".join(lines) + "\n"


_KB = None


def _kb():
    """Lazily load the trigger KB once (function + keyword SQL rules)."""
    global _KB
    if _KB is None:
        from rag.trigger_kb import TriggerKB
        _KB = TriggerKB.load()
    return _KB


def rewrite_one(path: Path):
    """Rewrite a single ``.sql`` file in place. Returns a dict describing what
    happened: ``{"modified": bool, "edits": [...]}``."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if _SENTINEL in text:
        return {"modified": False, "edits": [], "skipped": "already_processed"}

    # Deterministic mechanical rewrites (AST) — gives the rewritten body + the
    # rewrites that were applied.
    res = rewrite_sql(text, dialect="spark")
    # FULL detection over the rewritten body: AST shape gaps PLUS the keyword /
    # dual-surface function gaps from kb_rules.json (e.g. percentile_approx,
    # collect_list, TBLPROPERTIES). These would otherwise live only in
    # analysis.json and never be annotated in the .sql. Works even when the SQL
    # does not parse (the keyword/function rules are regex-based).
    findings = _kb().detect(res.new_text)

    if not res.parsed and not findings:
        # Unparseable AND nothing detected — leave byte-identical and claim
        # nothing (a "reviewed" header on opaque SQL would be misleading).
        return {"modified": False, "edits": []}

    # Stamp ONE `-- SCOS Migration Output` header listing every applied rewrite
    # and every remaining gap (deduped). Parseable-and-clean files still get a
    # 0/0 header showing they were reviewed.
    header = _build_header(path.name, res.applied, findings)
    new_text = header + res.new_text
    path.write_text(new_text, encoding="utf-8")

    edits = [
        {"rule_id": e.rule_id, "kind": "rewrite",
         "before": e.before, "after": e.after, "note": e.note}
        for e in res.applied
    ] + [
        {"rule_id": f.rule_id, "kind": "residual", "note": f.note}
        for f in findings
    ]
    return {"modified": True, "edits": edits}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Phase 0.6: deterministically rewrite SCOS-incompatible SQL "
                     "in standalone .sql files before the analyzer runs.")
    )
    parser.add_argument("--state", required=True, help="Path to migration_state.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing files.")
    args = parser.parse_args(argv)

    state_path = os.path.abspath(args.state)
    if not os.path.exists(state_path):
        print(f"ERROR: migration_state.json not found: {state_path}", file=sys.stderr)
        return 1
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    migrated_dir = state.get("migrated_dir", "")
    if not migrated_dir:
        print("ERROR: migrated_dir not set in migration_state.json", file=sys.stderr)
        return 1

    sql_files = find_plain_sql_files(Path(migrated_dir))

    print("=" * 60)
    print("PHASE 0.6: STANDALONE SQL REWRITE")
    print("=" * 60)
    print(f"  State        : {state_path}")
    print(f"  Migrated dir : {migrated_dir}")
    print(f"  .sql files   : {len(sql_files)}")
    print(f"  Mode         : {'dry-run' if args.dry_run else 'apply'}")
    print()

    edits_block: dict[str, list[dict]] = {}
    files_processed = 0
    files_modified = 0
    total_edits = 0
    total_residual = 0
    for sql_path in sql_files:
        files_processed += 1
        rel = os.path.relpath(str(sql_path), migrated_dir)
        if args.dry_run:
            text = sql_path.read_text(encoding="utf-8", errors="replace")
            if _SENTINEL in text:
                continue
            res = rewrite_sql(text, dialect="spark")
            # Mirror rewrite_one(): residual is the FULL detection over the
            # rewritten body (AST + keyword/function KB rules), not res.residual
            # (AST-only), so dry-run counts match what apply actually stamps.
            findings = _kb().detect(res.new_text)
            # Unparseable AND nothing detected → apply leaves it byte-identical
            # and claims nothing; don't report it as "would modify".
            if not res.parsed and not findings:
                continue
            files_modified += 1
            total_edits += len(res.applied)
            total_residual += len(findings)
            if res.applied:
                print(f"  WOULD REWRITE {rel}: "
                      f"{len(res.applied)} rewrite(s), {len(findings)} TODO(s)")
            else:
                print(f"  WOULD STAMP {rel}: reviewed, 0 rewrite(s), "
                      f"{len(findings)} TODO(s)")
            continue

        outcome = rewrite_one(sql_path)
        if outcome["modified"]:
            files_modified += 1
            edits_block[rel] = outcome["edits"]
            n_rw = sum(1 for e in outcome["edits"] if e["kind"] == "rewrite")
            n_res = sum(1 for e in outcome["edits"] if e["kind"] == "residual")
            total_edits += n_rw
            total_residual += n_res
            print(f"  REWRITE {rel}: {n_rw} rewrite(s), {n_res} TODO(s)")

    print()
    print(f"PHASE 0.6 SUMMARY: {files_processed} processed, {files_modified} modified, "
          f"{total_edits} rewrite(s), {total_residual} TODO(s)")

    if not args.dry_run:
        # Merge (don't clobber) so re-runs over a partially-processed tree keep
        # earlier entries; this run's keys win for files it touched.
        existing = state.get("sql_rewrite_edits") or {}
        existing.update(edits_block)
        state["sql_rewrite_edits"] = existing
        state.setdefault("phases_completed", {})["0_6_sql_rewrite"] = {
            "status": "passed",
            "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "files_processed": files_processed,
            "files_modified": files_modified,
            "total_edits": total_edits,
            "total_residual": total_residual,
        }
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
