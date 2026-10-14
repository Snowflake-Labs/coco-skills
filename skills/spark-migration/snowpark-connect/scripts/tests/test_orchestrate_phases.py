"""Unit tests for orchestrate_phases.build_balanced_chunks (Phase 2 worker pool).

The balanced chunker feeds the fixer worker pool: it must (1) fan a small
workload out to at least min(max_parallel, n_files) chunks so fixers run in
parallel, (2) never let a chunk exceed the token budget, and (3) be
deterministic so re-running the orchestrator reproduces the same plan.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import orchestrate_phases as op


@pytest.fixture
def fake_tokens(monkeypatch):
    """Make estimate_file_tokens deterministic and path-independent."""
    weights = {
        "big.py": 8000,
        "mid.py": 4000,
        "a.py": 1000,
        "b.py": 900,
        "c.py": 800,
        "d.py": 700,
    }
    monkeypatch.setattr(
        op, "estimate_file_tokens", lambda p: weights.get(os.path.basename(p), 1000)
    )
    return weights


def _flatten(chunks):
    return sorted(f for c in chunks for f in c)


def test_small_workload_fans_out_to_pool_width(fake_tokens):
    # 6 files, generous budget → would be ONE chunk with the old packer; the
    # balanced packer must produce max_parallel chunks so the pool stays busy.
    manifest = ["big.py", "mid.py", "a.py", "b.py", "c.py", "d.py"]
    chunks = op.build_balanced_chunks(manifest, "/x", budget=80_000, max_parallel=4)
    assert len(chunks) == 4
    # No file lost or duplicated.
    assert _flatten(chunks) == sorted(manifest)


def test_pool_of_one_is_single_chunk(fake_tokens):
    manifest = ["big.py", "mid.py", "a.py"]
    chunks = op.build_balanced_chunks(manifest, "/x", budget=80_000, max_parallel=1)
    assert len(chunks) == 1
    assert chunks[0] == sorted(manifest)


def test_pool_wider_than_files_caps_at_file_count(fake_tokens):
    manifest = ["a.py", "b.py", "c.py"]
    chunks = op.build_balanced_chunks(manifest, "/x", budget=80_000, max_parallel=8)
    assert len(chunks) == 3
    assert all(len(c) == 1 for c in chunks)


def test_budget_is_a_hard_cap_even_under_pool_width(fake_tokens):
    # Tight budget forces MORE chunks than the pool width so no chunk exceeds it.
    manifest = ["a.py", "b.py", "c.py", "d.py"]  # 1000+900+800+700 = 3400
    chunks = op.build_balanced_chunks(manifest, "/x", budget=1000, max_parallel=2)
    for c in chunks:
        tok = sum(fake_tokens[f] for f in c)
        assert tok <= 1000, f"chunk {c} = {tok} exceeds budget"
    assert _flatten(chunks) == sorted(manifest)


def test_oversized_single_file_gets_its_own_chunk(fake_tokens):
    # big.py (8000) alone exceeds a 2000 budget — unavoidable, but it must not
    # be dropped and must not be packed with anything else.
    manifest = ["big.py", "a.py", "b.py"]
    chunks = op.build_balanced_chunks(manifest, "/x", budget=2000, max_parallel=2)
    assert _flatten(chunks) == sorted(manifest)
    big_chunk = [c for c in chunks if "big.py" in c][0]
    assert big_chunk == ["big.py"]


def test_deterministic_across_runs(fake_tokens):
    manifest = ["big.py", "mid.py", "a.py", "b.py", "c.py", "d.py"]
    first = op.build_balanced_chunks(manifest, "/x", budget=80_000, max_parallel=3)
    second = op.build_balanced_chunks(manifest, "/x", budget=80_000, max_parallel=3)
    assert first == second


def test_empty_manifest(fake_tokens):
    assert op.build_balanced_chunks([], "/x", budget=80_000, max_parallel=4) == []


def test_ensure_phase_0_6_backstop_rewrites_standalone_sql(tmp_path):
    """orchestrate's Phase 0.6 backstop runs the standalone SQL rewrite when the
    coordinator skipped it, records the phase, and rewrites mechanical gaps."""
    import json
    out = tmp_path / "Output"
    out.mkdir()
    (out / "q.sql").write_text(
        "SELECT a, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1\n"
    )
    state_path = tmp_path / "migration_state.json"
    state_path.write_text(json.dumps({
        "conversion_root": str(tmp_path),
        "migrated_dir": str(out),
        "phases_completed": {"1_analysis": {"status": "passed"}},
    }))
    op._ensure_phase_0_6(str(state_path))
    state = json.loads(state_path.read_text())
    assert "0_6_sql_rewrite" in state.get("phases_completed", {})
    rewritten = (out / "q.sql").read_text()
    # QUALIFY was rewritten away (no live QUALIFY outside comments).
    live = [ln for ln in rewritten.splitlines()
            if "qualify" in ln.lower() and not ln.lstrip().startswith("--")]
    assert live == []


def test_ensure_phase_0_6_is_idempotent(tmp_path):
    """If 0_6_sql_rewrite is already recorded, the backstop is a no-op."""
    import json
    out = tmp_path / "Output"
    out.mkdir()
    (out / "q.sql").write_text("SELECT 1 QUALIFY ROW_NUMBER() OVER (ORDER BY x) = 1\n")
    state_path = tmp_path / "migration_state.json"
    state_path.write_text(json.dumps({
        "conversion_root": str(tmp_path), "migrated_dir": str(out),
        "phases_completed": {"0_6_sql_rewrite": {"status": "passed"}},
    }))
    before = (out / "q.sql").read_text()
    op._ensure_phase_0_6(str(state_path))
    assert (out / "q.sql").read_text() == before  # untouched
