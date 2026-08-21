#!/usr/bin/env python3
"""Regression tests for apply_adjudications.py verdict matching.

Guards the defect where verdicts were silently discarded and misattributed.

Root cause: rows were indexed one-to-one by ``(file, lines)``. For notebook
sources ``lines`` is **cell-relative**, so it is not unique within a file — every
cell has a line 1, and unrelated issues in different cells collide on the same
label. A dict keeps only the last row per key, so the rest were unreachable and
their verdicts were counted as ``already_adjudicated`` and dropped.

Measured on real workloads before the fix:
  Verisk_Claims  156 verdicts submitted, 66 discarded (68 rows left unadjudicated)
  RAD_Property   69 verdicts submitted, 7 discarded

Worse than loss, it also misattributed: the first verdict in a collision group
was applied to the last row of that group, so a ``confirm`` reasoned about a Delta
MERGE could be stamped onto a benign ``count()`` while the real blocker stayed
untouched.

Run:
    python3 -m pytest test_apply_adjudications_matching.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "apply_adjudications.py"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _row(cell_id, ewi, code, lines="2-2", file="nb.py"):
    return {"file": file, "cell_id": cell_id, "lines": lines, "ewi_code": ewi,
            "code": code, "kind": "needs_adjudication", "final_risk": 0.5}


def _verdict(row, decision, *, strong=True, **extra):
    """Build a verdict for *row*. strong=False simulates a legacy sidecar."""
    v = {"file": row["file"], "lines": row["lines"], "decision": decision, **extra}
    if strong:
        v.update({"cell_id": row["cell_id"], "ewi_code": row["ewi_code"],
                  "code": row["code"]})
    return v


def _run(tmp_path, rows, verdicts, *extra_args):
    (tmp_path / "analysis.json").write_text(json.dumps(rows, indent=2))
    adj = tmp_path / "Adjudication"
    adj.mkdir(exist_ok=True)
    (adj / "chunk_1.json").write_text(json.dumps(verdicts, indent=2))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--analysis", str(tmp_path / "analysis.json"),
         "--verdicts-dir", str(adj), *extra_args],
        capture_output=True, text=True)
    after = json.loads((tmp_path / "analysis.json").read_text())
    return proc, after


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #

def test_colliding_lines_every_verdict_applied(tmp_path):
    """N distinct issues sharing (file, lines): all N verdicts must land."""
    rows = [_row("c1", "E1", "DeltaTable.forName(spark, 't')"),
            _row("c2", "E2", "df.count()"),
            _row("c3", "E3", "spark.read.parquet(p)"),
            _row("c4", "E4", "P.* EXCEPT(a, b)")]
    verdicts = [_verdict(rows[0], "confirm", final_risk=0.95),
                _verdict(rows[1], "dismiss", resolution_reason="count is supported"),
                _verdict(rows[2], "confirm", final_risk=0.8),
                _verdict(rows[3], "confirm", final_risk=0.95)]
    proc, after = _run(tmp_path, rows, verdicts)

    assert proc.returncode == 0, proc.stderr
    unadjudicated = [r for r in after if not r.get("adjudicated")]
    assert not unadjudicated, f"verdicts discarded for: {unadjudicated}"
    assert "submitted=4 applied=4" in proc.stdout


def test_colliding_lines_no_misattribution(tmp_path):
    """A verdict must land on the row it was written for, not a sibling."""
    rows = [_row("c1", "DELTA", "DeltaTable.forName(spark, 't').merge(...)"),
            _row("c2", "COUNT", "log['n'] = df.count()")]
    verdicts = [_verdict(rows[0], "confirm", final_risk=0.99),
                _verdict(rows[1], "dismiss", resolution_reason="supported")]
    proc, after = _run(tmp_path, rows, verdicts)

    assert proc.returncode == 0, proc.stderr
    delta = next(r for r in after if r["ewi_code"] == "DELTA")
    count = next(r for r in after if r["ewi_code"] == "COUNT")
    # the confirm (risk 0.99) belongs to the Delta row
    assert delta["kind"] == "standard" and delta["final_risk"] == 0.99
    # the dismiss belongs to the count row
    assert count["resolution"] == "safe"
    assert count.get("final_risk") != 0.99, "confirm was misattributed to count()"


def test_legacy_sidecar_without_cell_id_still_applies(tmp_path):
    """Sidecars predating the contract change must not regress."""
    rows = [_row("c1", "E1", "first"), _row("c2", "E2", "second")]
    verdicts = [_verdict(rows[0], "dismiss", strong=False, resolution_reason="a"),
                _verdict(rows[1], "dismiss", strong=False, resolution_reason="b")]
    proc, after = _run(tmp_path, rows, verdicts)

    assert proc.returncode == 0, proc.stderr
    assert all(r.get("adjudicated") for r in after)
    assert "positional=2" in proc.stdout


def test_unapplied_verdict_is_a_hard_failure(tmp_path):
    """A verdict that matches nothing must fail loudly, not exit 0."""
    rows = [_row("c1", "E1", "only")]
    verdicts = [_verdict(rows[0], "dismiss", resolution_reason="ok"),
                {"file": "other.py", "lines": "9-9", "decision": "dismiss",
                 "resolution_reason": "no such row"}]
    proc, _ = _run(tmp_path, rows, verdicts)

    assert proc.returncode == 1, "dropped verdict must not exit 0"
    assert "were NOT applied" in proc.stderr


def test_allow_unapplied_override(tmp_path):
    """--allow-unapplied downgrades the hard failure but still reports it."""
    rows = [_row("c1", "E1", "only")]
    verdicts = [{"file": "other.py", "lines": "9-9", "decision": "dismiss",
                 "resolution_reason": "no such row"}]
    proc, _ = _run(tmp_path, rows, verdicts, "--allow-unapplied")

    assert proc.returncode == 0
    assert "were NOT applied" in proc.stderr


def test_rows_with_no_verdict_are_reported_not_silent(tmp_path):
    """Rows no worker covered stay unadjudicated and are warned about."""
    rows = [_row("c1", "E1", "judged"), _row("c2", "E2", "never judged")]
    verdicts = [_verdict(rows[0], "dismiss", resolution_reason="ok")]
    proc, after = _run(tmp_path, rows, verdicts)

    assert proc.returncode == 0, proc.stderr
    assert "UNRESOLVED_left=1" in proc.stdout
    assert "received no verdict" in proc.stderr
    assert sum(1 for r in after if not r.get("adjudicated")) == 1


def test_idempotent_rerun(tmp_path):
    """Re-running must not double-apply or start discarding verdicts."""
    rows = [_row("c1", "E1", "a"), _row("c2", "E2", "b")]
    verdicts = [_verdict(rows[0], "confirm", final_risk=0.9),
                _verdict(rows[1], "dismiss", resolution_reason="fine")]
    proc1, after1 = _run(tmp_path, rows, verdicts)
    assert proc1.returncode == 0

    # second pass over the already-merged analysis: nothing left to adjudicate,
    # so both verdicts are unapplied -> loud, not silent
    proc2, after2 = _run(tmp_path, after1, verdicts, "--allow-unapplied")
    assert proc2.returncode == 0
    assert after2[0]["final_risk"] == 0.9
    assert after2[1]["resolution"] == "safe"


def test_self_check_runs_on_every_invocation(tmp_path):
    """The built-in _self_check must not be silently skipped."""
    proc = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                          capture_output=True, text=True)
    assert proc.returncode == 0
    assert "cell-relative" in proc.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
