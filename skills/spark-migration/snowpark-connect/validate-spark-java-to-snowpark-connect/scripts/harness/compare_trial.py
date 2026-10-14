#!/usr/bin/env python3
"""compare_trial.py — trial-level Phase A vs Phase B compare driver (no Spark).

Pure-Python replacement for `scos-control.jar compare --conv-root --trial-id`,
which previously started a local Spark (`local[1]`) per trial just to read and
diff Parquet snapshots. This driver loads the canonical PySpark comparator at
``$VALIDATOR_SCRIPTS/harness/comparator.py`` via the local shim
``scripts/harness/comparator.py`` — no JVM, no Spark cold start, no local fork.

Behaviour (mirrors the JVM convenience form + the PySpark validator's per-table
model):
  * baseline dir = <conv>/Validation/results/phase_a/<trial>
  * shadow   dir = <conv>/Validation/results/phase_b/<trial>
  * enumerate captured tables from each side's ``_index.json`` (tables[].name),
  * compare each baseline/shadow table pair via ``comparator.compare()``
    (each table is a Spark output DIRECTORY ``tables/<name>.parquet`` — read
    natively by pandas/pyarrow),
  * apply documented divergences from ``shared/analysis.json``
    (``expected_divergences`` keyed by ``"<trial>.<sink>"``, scope in
    {data, both}) as ignore-columns — mirrors ScosComparator.loadExpectedDivergences,
  * write per-table diffs to ``<shadow>/diffs/<name>.json`` plus an aggregate
    ``<shadow>/compare.json``,
  * exit 0 = all match, 1 = any divergence/missing, 2 = error.

The contract scos-runner depends on is the EXIT CODE (0/1/2) plus the written
diff files (referenced via ``record-diff --diff-path``). build-index derives the
run_index comparison verdict from trial status + documented divergences, not from
compare.json internals — so this driver is a drop-in for the JVM compare step.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comparator  # vendored, language-agnostic single-sink diff

# Result classes mirror ScosComparator's exit-code mapping.
_MATCH = {"match", "match_with_skips"}
_DIVERGE = {"diverge", "missing_baseline", "missing_shadow"}


def _load_index(trial_dir: Path) -> Dict[str, Any]:
    idx = trial_dir / "_index.json"
    if not idx.is_file():
        return {}
    try:
        return json.loads(idx.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _table_names(index: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for t in index.get("tables", []) or []:
        if isinstance(t, dict) and t.get("name"):
            names.append(str(t["name"]))
    return names


def _load_expected_divergences(conv_root: Path) -> dict:
    """Load expected_divergences — prefer schemas/manifest.json (SoT), shim fallback."""
    manifest_path = conv_root / "Validation" / "shared" / "schemas" / "manifest.json"
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            divs = data.get("expected_divergences")
            if isinstance(divs, dict):
                return divs
        except (ValueError, OSError):
            pass
    analysis_path = conv_root / "Validation" / "shared" / "analysis.json"
    if analysis_path.is_file():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            divs = analysis.get("expected_divergences")
            if isinstance(divs, dict):
                return divs
        except (ValueError, OSError):
            pass
    return {}


def _load_ignore_columns(conv_root: Path, trial_id: str, sink: str) -> Set[str]:
    """Documented-divergence columns to skip for <trial>.<sink>.

    ``expected_divergences`` is keyed by ``"<trialId>.<sinkName>"`` in
    ``schemas/manifest.json`` (preferred) or the generated analysis shim; each
    entry contributes its column when scope is ``data``/``both`` (or unspecified).
    """
    expdivs = _load_expected_divergences(conv_root)
    cols: Set[str] = set()
    for key in (f"{trial_id}.{sink}", sink):
        for entry in expdivs.get(key, []) or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("scope", "data")).lower() not in ("data", "both"):
                continue
            col = entry.get("column") or entry.get("col")
            if col:
                cols.add(str(col).upper())
    return cols


def _rows(shape_side: Any) -> Optional[int]:
    return shape_side.get("rows") if isinstance(shape_side, dict) else None


def compare_trial(
    conv_root: Path,
    trial_id: str,
    analysis_path: Path,
    key_columns: Optional[List[str]] = None,
    row_tolerance: float = 1e-6,
    sample_limit: int = 200,
) -> Dict[str, Any]:
    val_root = conv_root / "Validation"
    pa = val_root / "results" / "phase_a" / trial_id
    pb = val_root / "results" / "phase_b" / trial_id

    # Baseline order first, then any shadow-only tables.
    names: List[str] = _table_names(_load_index(pa))
    for n in _table_names(_load_index(pb)):
        if n not in names:
            names.append(n)

    diffs_dir = pb / "diffs"
    table_results: List[Dict[str, Any]] = []
    any_error = False
    any_diverge = False

    for name in names:
        baseline = pa / "tables" / f"{name}.parquet"
        shadow = pb / "tables" / f"{name}.parquet"
        ignore = _load_ignore_columns(conv_root, trial_id, name)
        try:
            result = comparator.compare(
                str(baseline),
                str(shadow),
                key_columns=key_columns,
                row_tolerance=row_tolerance,
                sample_limit=sample_limit,
                ignore_columns=ignore or None,
            )
        except Exception as exc:  # noqa: BLE001 — never let one table abort the sweep
            result = {
                "result": "error",
                "summary": f"{type(exc).__name__}: {exc}",
                "shape": {"baseline": None, "shadow": None},
                "schema_diff": None,
            }

        diffs_dir.mkdir(parents=True, exist_ok=True)
        try:
            (diffs_dir / f"{name}.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            pass

        verdict = result.get("result", "error")
        if verdict in _DIVERGE:
            any_diverge = True
        elif verdict not in _MATCH:
            any_error = True

        shape = result.get("shape") or {}
        table_results.append({
            "table": name,
            "diff_path": f"diffs/{name}.json",
            "schema_match": result.get("schema_diff") is None,
            "row_count_a": _rows(shape.get("baseline")),
            "row_count_b": _rows(shape.get("shadow")),
            "verdict": verdict,
        })

    if not names or any_error:
        overall, exit_code = "error", 2
    elif any_diverge:
        overall, exit_code = "diverge", 1
    else:
        overall, exit_code = "match", 0

    summary = {
        "trial_id": trial_id,
        "table_count": len(names),
        "verdict": overall,
        "tables": table_results,
    }
    try:
        pb.mkdir(parents=True, exist_ok=True)
        (pb / "compare.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return {"summary": summary, "exit_code": exit_code}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="compare_trial.py",
        description="Trial-level Phase A vs Phase B compare (pure-Python, no Spark).",
    )
    p.add_argument("--conv-root", required=True, help="Conversion root containing Validation/")
    p.add_argument("--trial-id", required=True, help="Entrypoint/trial id")
    p.add_argument(
        "--analysis", default=None,
        help="Path to analysis.json (default: <conv>/Validation/shared/analysis.json)",
    )
    p.add_argument("--key-columns", default=None, help="Comma-separated key columns (optional)")
    p.add_argument("--row-tolerance", type=float, default=1e-6)
    p.add_argument("--sample-limit", type=int, default=200)
    args = p.parse_args(argv)

    conv_root = Path(args.conv_root).expanduser().resolve()
    analysis_path = (
        Path(args.analysis).expanduser().resolve()
        if args.analysis
        else conv_root / "Validation" / "shared" / "analysis.json"
    )
    key_columns = (
        [c.strip() for c in args.key_columns.split(",") if c.strip()]
        if args.key_columns else None
    )

    out = compare_trial(
        conv_root, args.trial_id, analysis_path,
        key_columns=key_columns,
        row_tolerance=args.row_tolerance, sample_limit=args.sample_limit,
    )
    s = out["summary"]
    print(
        f"[compare_trial] {args.trial_id}: {s['verdict']} "
        f"({s['table_count']} table(s)) -> exit {out['exit_code']}",
        file=sys.stderr,
    )
    return out["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
