"""Tests for scripts/batch.py — batch planning, merge-reports, and pool subcommands."""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent
BATCH_SCRIPT = SCRIPTS_DIR / "batch.py"
MERGE_SCRIPT = BATCH_SCRIPT  # alias: merge-reports is a subcommand of batch.py
SKILL_ROOT = SCRIPTS_DIR.parent

# Make `import batch` resolve regardless of pytest rootdir.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import batch  # noqa: E402

_be = batch  # alias used by batch-planning tests (validate_coverage, _lpt_split, etc.)

from cli_helpers import run_cli, run_cli_argv  # noqa: E402

from cortex_code_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    ToolUseBlock,
)


# ===========================================================================
# Shared helpers — batch planning (from test_batch_entrypoints.py)
# ===========================================================================


def _run_batch(
    manifest_path: Path,
    sections_path: Path,
    out_path: Path,
    extra_args: list[str] | None = None,
):
    argv = [
        "--manifest", str(manifest_path),
        "--sections", str(sections_path),
        "--out", str(out_path),
    ]
    if extra_args:
        argv.extend(extra_args)
    return run_cli(batch.main, argv)


def _write_manifest(path: Path, eps: list[dict]) -> None:
    """eps: list of {"id": str, "weight": int|None, ...}"""
    data = {
        "entrypoints": [
            {
                "id": ep["id"],
                "path": ep["id"],
                "dir": f"entrypoints/{ep['id']}",
                "weight": ep.get("weight"),
                "weight_breakdown": None,
            }
            for ep in eps
        ]
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_sections(path: Path, sections: list[dict]) -> None:
    path.write_text(json.dumps(sections), encoding="utf-8")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Shared helpers — merge-reports (from test_merge_reports.py)
# ---------------------------------------------------------------------------


def _run_merge(batches_dir: Path, out_dir: Path, run_id: str = "test-run"):
    return run_cli(batch.merge_main, [
        "--batches-dir", str(batches_dir),
        "--out", str(out_dir),
        "--run-id", run_id,
    ])


def _run_pool(argv_tail: list[str]):
    return run_cli_argv(batch.pool_main, ["batch.py", *argv_tail])


def _run_pool_status(root: Path):
    return run_cli(batch._pool_status_main, ["--root", str(root)])


def _make_ep(ep_id: str, overall: str, comparison_verdict: str) -> dict:
    """Build a minimal but schema-faithful entrypoint entry."""
    has_baseline = comparison_verdict != "no_baseline"
    return {
        "id": ep_id,
        "source_path": f"Output/{ep_id}.py",
        "phase_a": {
            "verdict": "baseline_produced" if has_baseline else "no_baseline",
            "iters": 1 if has_baseline else 0,
            "captured_outputs": [],
            "patches_applied": [],
            "errors": [],
        },
        "phase_b": {
            "verdict": overall,
            "iters": 1,
            "captured_outputs": [],
            "patches_applied": [],
            "errors": [],
            "scos_query_ids": [],
            "migration_fix_commits": [],
        },
        "comparison": {
            "verdict": comparison_verdict,
            "diffs": [],
            "documented_divergences": [],
        },
        "trial_dir": f"results/phase_b/{ep_id}/",
        "verdict": {
            "overall": overall,
            "reason": "matched baseline" if overall == "passed" else "",
        },
    }


def _make_run_index(run_id: str, status: str, eps: list[dict]) -> dict:
    """Build a minimal schema-faithful run_index.json."""
    return {
        "run": {
            "id": run_id,
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": "2025-01-01T01:00:00Z",
            "status": status,
            "skill_version": "0.1.0",
            "connection": "my_conn",
            "database": "TEST_DB",
            "schema_namespace": "TEST_SCHEMA",
        },
        "milestones": {},
        "entrypoints": eps,
        "artifacts_index": {
            "analysis": "shared/schemas/manifest.json",
            "patch_blueprint": None,
            "mock_data": [],
            "rendered_tests": [],
        },
        "events": None,
        "fixer_dispatches": [],
        "documented_divergences": [],
        "warnings": [],
        "parse_errors": [],
    }


def _setup_batches(tmp_path: Path) -> tuple[Path, Path]:
    """Create a Validation layout with 3 batches (one corrupt)."""
    val_root = tmp_path / "Validation"
    batches_dir = val_root / "batches"
    batches_dir.mkdir(parents=True)

    # Batch A: one passed+match EP, one passed_no_baseline+no_baseline EP
    batch_a = batches_dir / "batch_a"
    batch_a.mkdir()
    (batch_a / "results").mkdir()
    (batch_a / "results" / "REPORT.md").write_text("# Batch A Report\n")
    eps_a = [
        _make_ep("ep_alpha", "passed", "match"),
        _make_ep("ep_beta", "passed_no_baseline", "no_baseline"),
    ]
    (batch_a / "run_index.json").write_text(
        json.dumps(_make_run_index("run-a", "passed", eps_a)), encoding="utf-8"
    )

    # Batch B: one hard_stuck+real_divergence EP
    batch_b = batches_dir / "batch_b"
    batch_b.mkdir()
    eps_b = [
        _make_ep("ep_gamma", "hard_stuck", "real_divergence"),
    ]
    (batch_b / "run_index.json").write_text(
        json.dumps(_make_run_index("run-b", "partial", eps_b)), encoding="utf-8"
    )

    # Batch C: corrupt run_index.json
    batch_c = batches_dir / "batch_c"
    batch_c.mkdir()
    (batch_c / "run_index.json").write_text("{ not valid json !!!!", encoding="utf-8")

    return val_root, batches_dir


# ---------------------------------------------------------------------------
# Shared helpers — pool (from test_pool.py)
# ---------------------------------------------------------------------------


def _result_msg(
    is_error: bool = False,
    session_id: str = "sess-real",
    result_text: str = "",
    usage: dict | None = None,
    duration_ms: int = 1,
    num_turns: int = 1,
    total_cost_usd: float = 0.0,
) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=duration_ms,
        duration_api_ms=duration_ms,
        is_error=is_error,
        num_turns=num_turns,
        session_id=session_id,
        stop_reason=None,
        total_cost_usd=total_cost_usd,
        usage=usage,
        result=result_text,
        structured_output=None,
        permission_denials=None,
        uuid="u1",
    )


def _system_init(session_id: str = "real-123") -> SystemMessage:
    return SystemMessage(subtype="init", data={"session_id": session_id})


def _assistant_msg(content=None) -> AssistantMessage:
    return AssistantMessage(
        content=content or [],
        model="test-model",
        message_id="msg-1",
        parent_tool_use_id=None,
        error=None,
        usage=None,
    )


def _make_batch(
    batch_id: str = "b1",
    worktree: str = "/tmp/wt1",
    **kwargs,
) -> batch.PoolBatch:
    defaults: dict = dict(
        batch_id=batch_id,
        ep_ids=["ep1", "ep2"],
        n_eps=2,
        total_weight=1.0,
        worktree=worktree,
        run_id="run1",
        validation_branch="val/branch",
    )
    defaults.update(kwargs)
    return batch.PoolBatch(**defaults)


def make_fake_query(messages, record=None):
    async def fake_query(*, prompt, options):
        if record is not None:
            record.append(options)
        for m in messages:
            yield m

    return fake_query


def _make_args(
    tmp_path: Path,
    prepared_path: Path | None = None,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        primary_conv_root=str(tmp_path),
        original_source="/fake/source",
        connection="test_conn",
        skill_directory=str(tmp_path / "skill"),
        prepared=str(prepared_path or (tmp_path / "batches_prepared.json")),
        pool_size=3,
        model=None,
        effort=None,
        max_turns=0,
        retries=1,
        friction_log=None,
    )


def _make_manifest(eps: list[dict]) -> dict:
    return {
        "entrypoints": [
            {"id": ep["id"], "weight": ep.get("weight", 1)}
            for ep in eps
        ]
    }


def _write_valid_batch_artifacts(worktree: Path, *, entrypoints: list[dict] | None = None) -> None:
    """Write the minimum artifact set ``run_batch`` expects after a successful worker."""
    val_dir = worktree / "Validation"
    results_dir = val_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    eps = entrypoints or [_make_ep("ep1", "passed", "match")]
    (results_dir / "summary.json").write_text(
        json.dumps({
            "decision": {"overall": "passed", "ship_recommendation": "ship"},
            "trials": {},
        }),
        encoding="utf-8",
    )
    (results_dir / "REPORT.md").write_text("# Batch report\n", encoding="utf-8")
    (val_dir / "run_index.json").write_text(
        json.dumps(_make_run_index("run-test", "passed", eps)),
        encoding="utf-8",
    )
    (val_dir / "events.jsonl").write_text('{"kind":"trial_marked"}\n', encoding="utf-8")


# ===========================================================================
# Tests — batch planning (from test_batch_entrypoints.py)
# ===========================================================================

# ---------------------------------------------------------------------------
# Happy-path: single batch under both caps
# ---------------------------------------------------------------------------


def test_single_batch_under_both_caps(tmp_path):
    """Section whose EPs fit under both caps produces exactly 1 batch."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    _write_manifest(m, [
        {"id": "ep_a", "weight": 5},
        {"id": "ep_b", "weight": 3},
        {"id": "ep_c", "weight": 4},
    ])
    _write_sections(s, [
        {"section_id": "sec1", "name": "Section 1", "ep_ids": ["ep_a", "ep_b", "ep_c"]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "8", "--max-weight", "40"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    assert data["summary"]["n_batches"] == 1
    assert len(data["batches"]) == 1
    b = data["batches"][0]
    assert b["batch_id"] == "sec1__01"
    assert b["section_ids"] == ["sec1"]
    assert b["n_eps"] == 3
    assert b["total_weight"] == 12
    assert set(b["ep_ids"]) == {"ep_a", "ep_b", "ep_c"}


# ---------------------------------------------------------------------------
# Weight cap splitting
# ---------------------------------------------------------------------------


def test_over_weight_cap_creates_multiple_batches(tmp_path):
    """Section over the weight cap splits into multiple batches each within cap."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    # 6 EPs × weight 4 = total 24; max_weight=9 → ceil(24/9)=3 bins, each ≤ 9
    eps = [{"id": f"ep_{i}", "weight": 4} for i in range(6)]
    _write_manifest(m, eps)
    _write_sections(s, [
        {"section_id": "heavy", "name": "Heavy",
         "ep_ids": [f"ep_{i}" for i in range(6)]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "10", "--max-weight", "9"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    assert data["summary"]["n_batches"] == 3
    for b in data["batches"]:
        assert b["total_weight"] <= 9, (
            f"batch {b['batch_id']} has weight {b['total_weight']} > 9"
        )

    # All 6 EPs present exactly once
    all_ep_ids = [eid for b in data["batches"] for eid in b["ep_ids"]]
    assert sorted(all_ep_ids) == [f"ep_{i}" for i in range(6)]


def test_weight_balance_roughly(tmp_path):
    """LPT should distribute weight roughly evenly across bins."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    # 6 EPs with varying weights; LPT should balance them
    eps = [
        {"id": "ep_a", "weight": 8},
        {"id": "ep_b", "weight": 7},
        {"id": "ep_c", "weight": 6},
        {"id": "ep_d", "weight": 5},
        {"id": "ep_e", "weight": 4},
        {"id": "ep_f", "weight": 3},
    ]  # total=33, max_weight=12 → ceil(33/12)=3 bins
    _write_manifest(m, eps)
    _write_sections(s, [
        {"section_id": "s1", "name": "S1",
         "ep_ids": ["ep_a", "ep_b", "ep_c", "ep_d", "ep_e", "ep_f"]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "10", "--max-weight", "12"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    assert data["summary"]["n_batches"] == 3
    weights = [b["total_weight"] for b in data["batches"]]
    # LPT should bring max - min spread down to ≤ max individual weight (8)
    assert max(weights) - min(weights) <= 8
    assert data["summary"]["total_weight"] == 33


# ---------------------------------------------------------------------------
# Entrypoint-count cap splitting
# ---------------------------------------------------------------------------


def test_over_entrypoint_cap_creates_multiple_batches(tmp_path):
    """Section over the entrypoint count cap produces batches ≤ max_entrypoints."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    # 10 EPs, max_entrypoints=3 → ceil(10/3)=4 bins
    eps = [{"id": f"ep_{i:02d}", "weight": 1} for i in range(10)]
    _write_manifest(m, eps)
    _write_sections(s, [
        {"section_id": "big", "name": "Big",
         "ep_ids": [f"ep_{i:02d}" for i in range(10)]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "3", "--max-weight", "100"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    for b in data["batches"]:
        assert b["n_eps"] <= 3, (
            f"batch {b['batch_id']} has {b['n_eps']} EPs > 3"
        )
    assert data["summary"]["n_entrypoints"] == 10


# ---------------------------------------------------------------------------
# Single heavy EP
# ---------------------------------------------------------------------------


def test_single_ep_over_max_weight_gets_own_batch_with_warning(tmp_path):
    """A single EP whose weight > max_weight lands in its own batch with a warning."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    _write_manifest(m, [{"id": "big_ep", "weight": 100}])
    _write_sections(s, [
        {"section_id": "sec_heavy", "name": "Heavy", "ep_ids": ["big_ep"]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "8", "--max-weight", "40"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    assert data["summary"]["n_batches"] == 1
    assert data["batches"][0]["ep_ids"] == ["big_ep"]
    assert data["batches"][0]["total_weight"] == 100
    assert any(
        "big_ep" in w and "exceeds" in w for w in data["warnings"]
    ), f"expected heavy-EP warning; got: {data['warnings']}"


# ---------------------------------------------------------------------------
# Coverage gate: hard failures (exit 3)
# ---------------------------------------------------------------------------


def test_coverage_errors_exit_3(tmp_path):
    """Coverage gate failures exit 3 and never write batches.json."""
    cases = [
        (
            [{"id": "ep_in_section", "weight": 5}, {"id": "ep_orphan", "weight": 3}],
            [{"section_id": "sec1", "name": "Section 1", "ep_ids": ["ep_in_section"]}],
            ["ep_orphan", "not assigned to any section"],
        ),
        (
            [{"id": "ep_shared", "weight": 5}, {"id": "ep_unique", "weight": 3}],
            [
                {"section_id": "sec1", "name": "S1", "ep_ids": ["ep_shared"]},
                {"section_id": "sec2", "name": "S2", "ep_ids": ["ep_shared", "ep_unique"]},
            ],
            ["ep_shared", "appears in 2 sections"],
        ),
        (
            [{"id": "ep_real", "weight": 5}],
            [{"section_id": "sec1", "name": "Section 1", "ep_ids": ["ep_real", "ep_ghost"]}],
            ["ep_ghost", "not in the manifest"],
        ),
    ]
    for manifest_eps, sections, expected_fragments in cases:
        m = tmp_path / f"manifest_{expected_fragments[0]}.json"
        s = tmp_path / f"sections_{expected_fragments[0]}.json"
        o = tmp_path / f"batches_{expected_fragments[0]}.json"
        _write_manifest(m, manifest_eps)
        _write_sections(s, sections)
        r = _run_batch(m, s, o)
        assert r.returncode == 3, f"expected exit 3; got {r.returncode}\nstderr={r.stderr}"
        for fragment in expected_fragments:
            assert fragment in r.stderr
        assert "coverage check failed" in r.stderr
        assert not o.exists(), "batches.json must not be written on coverage failure"


def test_coverage_happy_path_full_coverage(tmp_path):
    """Full coverage (all manifest EPs assigned exactly once) → exit 0, batches produced."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    _write_manifest(m, [
        {"id": "ep_a", "weight": 5},
        {"id": "ep_b", "weight": 3},
        {"id": "ep_c", "weight": 4},
    ])
    _write_sections(s, [
        {"section_id": "sec1", "name": "S1", "ep_ids": ["ep_a", "ep_b"]},
        {"section_id": "sec2", "name": "S2", "ep_ids": ["ep_c"]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "8", "--max-weight", "40"])
    assert r.returncode == 0, f"expected exit 0; stderr={r.stderr}"
    data = _load(o)
    assert data["summary"]["n_entrypoints"] == 3
    all_eps = {eid for b in data["batches"] for eid in b["ep_ids"]}
    assert all_eps == {"ep_a", "ep_b", "ep_c"}
    # Under whole-section mixing, sec1 and sec2 may share one batch (mixed__01).
    # Verify coverage via the section_ids list on each batch instead of per-batch section_id.
    all_section_ids = {sid for b in data["batches"] for sid in b["section_ids"]}
    assert all_section_ids == {"sec1", "sec2"}
    all_ep_ids = {eid for b in data["batches"] for eid in b["ep_ids"]}
    assert all_ep_ids == {"ep_a", "ep_b", "ep_c"}


# ---------------------------------------------------------------------------
# validate_coverage unit tests
# ---------------------------------------------------------------------------


def test_validate_coverage_error_kinds():
    """Unit coverage for validate_coverage error messages and ordering."""
    clean = _be.validate_coverage(
        _make_manifest([{"id": "a"}, {"id": "b"}, {"id": "c"}]),
        [{"section_id": "s1", "ep_ids": ["a", "b"]}, {"section_id": "s2", "ep_ids": ["c"]}],
    )
    assert clean == []

    missing = _be.validate_coverage(
        _make_manifest([{"id": "a"}, {"id": "b"}]),
        [{"section_id": "s1", "ep_ids": ["a"]}],
    )
    assert len(missing) == 1 and "b" in missing[0] and "not assigned" in missing[0]

    cross_dup = _be.validate_coverage(
        _make_manifest([{"id": "a"}, {"id": "b"}]),
        [{"section_id": "s1", "ep_ids": ["a", "b"]}, {"section_id": "s2", "ep_ids": ["a"]}],
    )
    dup = [e for e in cross_dup if "appears in 2 sections" in e]
    assert len(dup) == 1 and "a" in dup[0]

    within_dup = _be.validate_coverage(
        _make_manifest([{"id": "a"}, {"id": "b"}]),
        [{"section_id": "s1", "ep_ids": ["a", "a", "b"]}],
    )
    wd = [e for e in within_dup if "appears 2 times in section" in e]
    assert len(wd) == 1 and "a" in wd[0]

    unknown = _be.validate_coverage(
        _make_manifest([{"id": "a"}]),
        [{"section_id": "s1", "ep_ids": ["a", "ghost", "ghost"]}],
    )
    unk = [e for e in unknown if "not in the manifest" in e]
    assert len(unk) == 1 and "ghost" in unk[0]

    empty_sections = _be.validate_coverage(_make_manifest([{"id": "a"}, {"id": "b"}]), [])
    assert len([e for e in empty_sections if "not assigned" in e]) == 2

    empty_ep_ids = _be.validate_coverage(
        _make_manifest([{"id": "a"}]),
        [{"section_id": "s1", "ep_ids": []}],
    )
    assert len([e for e in empty_ep_ids if "not assigned" in e]) == 1

    ordered = _be.validate_coverage(
        _make_manifest([{"id": "a"}, {"id": "b"}, {"id": "c"}]),
        [{"section_id": "s1", "ep_ids": ["a", "a"]}, {"section_id": "s2", "ep_ids": ["ghost"]}],
    )
    kinds = []
    for e in ordered:
        if "appears" in e:
            kinds.append("dup")
        elif "not assigned" in e:
            kinds.append("missing")
        elif "not in the manifest" in e:
            kinds.append("unknown")
    assert kinds.index("dup") < kinds.index("missing") < kinds.index("unknown")


def test_coverage_error_within_section_dup_exits_3(tmp_path):
    """EP duplicated within same section → exit 3, error in stderr, no output written."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    _write_manifest(m, [{"id": "ep_a", "weight": 5}])
    _write_sections(s, [
        {"section_id": "sec1", "name": "S1", "ep_ids": ["ep_a", "ep_a"]},
    ])

    r = _run_batch(m, s, o)
    assert r.returncode == 3, f"expected exit 3; got {r.returncode}\nstderr={r.stderr}"
    assert "ep_a" in r.stderr
    assert "times in section" in r.stderr, "expected within-section dup phrasing"
    assert not o.exists(), "batches.json must not be written on coverage failure"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_determinism_byte_identical(tmp_path):
    """Identical inputs produce byte-identical batches.json across two runs."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o1 = tmp_path / "batches1.json"
    o2 = tmp_path / "batches2.json"

    eps = [{"id": f"ep_{chr(65 + i)}", "weight": (i % 7) + 1} for i in range(15)]
    _write_manifest(m, eps)
    _write_sections(s, [
        {"section_id": "s1", "name": "S1",
         "ep_ids": [ep["id"] for ep in eps[:8]]},
        {"section_id": "s2", "name": "S2",
         "ep_ids": [ep["id"] for ep in eps[8:]]},
    ])

    r1 = _run_batch(m, s, o1, ["--max-entrypoints", "4", "--max-weight", "15"])
    r2 = _run_batch(m, s, o2, ["--max-entrypoints", "4", "--max-weight", "15"])
    assert r1.returncode == 0 and r2.returncode == 0

    assert o1.read_bytes() == o2.read_bytes(), (
        "batches.json is not deterministic across two runs with identical input"
    )


# ---------------------------------------------------------------------------
# Output shape and summary
# ---------------------------------------------------------------------------


def test_output_has_required_top_level_keys(tmp_path):
    """Output JSON has all required top-level fields."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    _write_manifest(m, [{"id": "ep_x", "weight": 5}])
    _write_sections(s, [{"section_id": "sec1", "name": "S1", "ep_ids": ["ep_x"]}])

    _run_batch(m, s, o, ["--max-entrypoints", "8", "--max-weight", "40"])
    data = _load(o)

    assert "max_entrypoints" in data
    assert "max_weight" in data
    assert "batches" in data
    assert "summary" in data
    assert "warnings" in data

    summary = data["summary"]
    for key in ("n_batches", "n_entrypoints", "total_weight",
                "weight_min", "weight_max", "weight_mean"):
        assert key in summary, f"summary missing key {key!r}"


def test_summary_counts_are_correct(tmp_path):
    """Summary n_entrypoints and total_weight match the actual batches."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    eps = [{"id": f"ep_{i}", "weight": i + 1} for i in range(9)]
    _write_manifest(m, eps)
    _write_sections(s, [
        {"section_id": "s1", "name": "S1",
         "ep_ids": [ep["id"] for ep in eps]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "4", "--max-weight", "20"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    sum_eps = sum(b["n_eps"] for b in data["batches"])
    sum_wt = sum(b["total_weight"] for b in data["batches"])

    assert data["summary"]["n_entrypoints"] == sum_eps
    assert data["summary"]["total_weight"] == sum_wt
    assert data["summary"]["n_batches"] == len(data["batches"])
    assert data["summary"]["weight_min"] == min(b["total_weight"] for b in data["batches"])
    assert data["summary"]["weight_max"] == max(b["total_weight"] for b in data["batches"])


def test_batch_id_format(tmp_path):
    """batch_id follows the <section_id>__<NN> pattern (1-based, zero-padded)."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    eps = [{"id": f"ep_{i:02d}", "weight": 1} for i in range(6)]
    _write_manifest(m, eps)
    _write_sections(s, [
        {"section_id": "my_sec", "name": "My Section",
         "ep_ids": [ep["id"] for ep in eps]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "2", "--max-weight", "100"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    batch_ids = [b["batch_id"] for b in data["batches"]]
    assert batch_ids == ["my_sec__01", "my_sec__02", "my_sec__03"]


# ---------------------------------------------------------------------------
# stdout and CLI error cases
# ---------------------------------------------------------------------------


def test_cli_input_errors_exit_1(tmp_path):
    """Missing/invalid manifest or sections inputs exit 1."""
    assert _run_batch(
        tmp_path / "nonexistent_manifest.json",
        tmp_path / "sections.json",
        tmp_path / "out.json",
    ).returncode == 1

    m = tmp_path / "manifest.json"
    _write_manifest(m, [{"id": "ep_a", "weight": 1}])
    assert _run_batch(
        m, tmp_path / "nonexistent_sections.json", tmp_path / "out.json"
    ).returncode == 1

    s = tmp_path / "sections.json"
    s.write_text(json.dumps({"section_id": "x", "ep_ids": []}), encoding="utf-8")
    assert _run_batch(m, s, tmp_path / "out.json").returncode == 1

    s.write_text(json.dumps([{"name": "No ID", "ep_ids": ["ep_a"]}]), encoding="utf-8")
    assert _run_batch(m, s, tmp_path / "out.json").returncode == 1


def test_section_id_traversal_rejected(tmp_path):
    """A malicious section_id (path traversal / shell chars / spaces) must be
    rejected before it can flow into a worktree path or git branch name."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    _write_manifest(m, [{"id": "ep_a", "weight": 1}])
    for bad_sid in ("../evil", "foo/bar", "foo bar", "foo;rm -rf"):
        s.write_text(
            json.dumps([{"section_id": bad_sid, "name": "n", "ep_ids": ["ep_a"]}]),
            encoding="utf-8",
        )
        r = _run_batch(m, s, tmp_path / "out.json")
        assert r.returncode == 1, f"{bad_sid!r} should have been rejected"
        # The rejected section_id must appear in the diagnostic.
        assert bad_sid in (r.stderr + r.stdout), (
            f"error message did not name the offending section_id {bad_sid!r}:\n"
            f"stderr={r.stderr!r}\nstdout={r.stdout!r}"
        )


def test_batch_sections_rejects_non_positive_caps():
    """`batch_sections` raises ValueError (not ZeroDivisionError) when either cap
    is <= 0 — exercise the function directly so we don't depend on argparse."""
    manifest = {"entrypoints": [{"id": "ep_a", "weight": 1}]}
    sections = [{"section_id": "s", "name": "S", "ep_ids": ["ep_a"]}]
    for me, mw in ((0, 8), (-1, 8), (8, 0), (8, -1)):
        try:
            batch.batch_sections(manifest, sections, me, mw)
        except ValueError as exc:
            assert "must be positive" in str(exc), (
                f"unexpected ValueError text for caps ({me}, {mw}): {exc}"
            )
        else:
            raise AssertionError(
                f"batch_sections(..., {me}, {mw}) should have raised ValueError"
            )


def test_cli_rejects_non_positive_caps(tmp_path):
    """The CLI surfaces the cap-validation as exit 1 (via main()'s ValueError
    handler), not a traceback."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    _write_manifest(m, [{"id": "ep_a", "weight": 1}])
    s.write_text(
        json.dumps([{"section_id": "s", "name": "S", "ep_ids": ["ep_a"]}]),
        encoding="utf-8",
    )
    r = _run_batch(m, s, tmp_path / "out.json",
                   ["--max-entrypoints", "0", "--max-weight", "8"])
    assert r.returncode == 1
    assert "must be positive" in (r.stderr + r.stdout)
    # And no traceback leaked through.
    assert "ZeroDivisionError" not in (r.stderr + r.stdout)
    assert "Traceback" not in (r.stderr + r.stdout)


# ---------------------------------------------------------------------------
# Multi-section ordering
# ---------------------------------------------------------------------------


def test_multi_section_batch_order(tmp_path):
    """Batches are ordered by section order, then by batch index within section."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    _write_manifest(m, [
        {"id": "ep_a", "weight": 1},
        {"id": "ep_b", "weight": 1},
        {"id": "ep_c", "weight": 1},
        {"id": "ep_d", "weight": 1},
    ])
    _write_sections(s, [
        {"section_id": "alpha", "name": "Alpha", "ep_ids": ["ep_a", "ep_b"]},
        {"section_id": "beta", "name": "Beta", "ep_ids": ["ep_c", "ep_d"]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "1", "--max-weight", "100"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    section_order = [b["section_ids"][0] for b in data["batches"]]
    # All alpha batches before beta batches
    alpha_last = max(i for i, sid in enumerate(section_order) if sid == "alpha")
    beta_first = min(i for i, sid in enumerate(section_order) if sid == "beta")
    assert alpha_last < beta_first


# ---------------------------------------------------------------------------
# Weight-cap correctness: exact repro + property-style checks
# ---------------------------------------------------------------------------


def test_four_weight25_eps_no_bin_over_max_weight():
    """Exact repro: 4×weight-25 eps, max_weight=40 — no bin must exceed 40.

    ceil(100/40)=3 bins is too few; LPT naively puts 2 EPs in one bin (50>40).
    The retry loop must detect the violation and re-pack with 4 bins.
    """
    from batch import _lpt_split

    eps = [("a", 25), ("b", 25), ("c", 25), ("d", 25)]
    bins, warnings = _lpt_split(eps, max_entrypoints=8, max_weight=40)

    bin_weights = [sum(w for _, w in b) for b in bins]
    assert all(bw <= 40 for bw in bin_weights), (
        f"bin(s) exceeded max_weight=40: {bin_weights}"
    )
    # All 4 EPs present
    all_ep_ids = [eid for b in bins for eid, _ in b]
    assert sorted(all_ep_ids) == ["a", "b", "c", "d"]
    # No warnings (no individual EP exceeds cap)
    assert warnings == []


def test_weight_cap_invariant_via_subprocess(tmp_path):
    """End-to-end repro via CLI: 4×weight-25, max_weight=40 → no batch > 40."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    _write_manifest(m, [{"id": c, "weight": 25} for c in "abcd"])
    _write_sections(s, [
        {"section_id": "sec1", "name": "S1", "ep_ids": list("abcd")},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "8", "--max-weight", "40"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    for b in data["batches"]:
        assert b["total_weight"] <= 40, (
            f"batch {b['batch_id']} total_weight={b['total_weight']} > 40"
        )
    assert data["summary"]["n_entrypoints"] == 4


def test_weight_cap_invariant_property(tmp_path):
    """Property: when all individual weights <= max_weight, no bin exceeds cap.

    Exercises several weight lists that previously could trip the n_bins formula.
    """
    from batch import _lpt_split

    cases: list[tuple[list[int], int, int]] = [
        # (weights, max_entrypoints, max_weight)
        ([25, 25, 25, 25], 8, 40),           # original repro
        ([20, 20, 20, 20, 20], 8, 30),       # 5×20, ceil(100/30)=4 → needs 5
        ([15, 14, 13, 12, 11, 10], 8, 25),   # varied, some bins would overflow
        ([10, 10, 10, 10, 10, 10, 10], 4, 15),  # both caps bind
        ([7, 7, 7, 7, 7, 7], 8, 10),         # ceil(42/10)=5, LPT needs 6
        ([1] * 20, 5, 4),                    # count-cap dominant
        ([30, 25, 20, 15, 10, 5], 8, 35),    # mixed weights
    ]

    for weights, max_eps, max_wt in cases:
        eps = [(f"ep_{i:02d}", w) for i, w in enumerate(weights)]
        bins, warnings = _lpt_split(eps, max_entrypoints=max_eps, max_weight=max_wt)

        # No individual EP exceeds max_wt in these cases, so no warnings expected
        assert warnings == [], f"unexpected warnings for {weights}: {warnings}"

        bin_weights = [sum(w for _, w in b) for b in bins]
        bin_counts = [len(b) for b in bins]

        for bw in bin_weights:
            assert bw <= max_wt, (
                f"bin weight {bw} > max_weight={max_wt} for weights={weights}"
            )
        for bc in bin_counts:
            assert bc <= max_eps, (
                f"bin count {bc} > max_entrypoints={max_eps} for weights={weights}"
            )

        # All EPs accounted for
        all_ids = sorted(eid for b in bins for eid, _ in b)
        assert all_ids == sorted(eid for eid, _ in eps)


def test_weight_cap_with_one_heavy_ep_still_warns(tmp_path):
    """Mix: one EP over cap (warns, own bin) + others fit under cap."""
    from batch import _lpt_split

    # heavy=50 gets its own bin (warned); the rest (10+10+10=30 <= 40) fit in one bin
    eps = [("heavy", 50), ("x", 10), ("y", 10), ("z", 10)]
    bins, warnings = _lpt_split(eps, max_entrypoints=8, max_weight=40)

    # Exactly one warning for the heavy EP
    assert len(warnings) == 1
    assert "heavy" in warnings[0] and "exceeds" in warnings[0]

    bin_weights = [sum(w for _, w in b) for b in bins]
    # heavy bin is 50 > 40 but unavoidable; the other bin(s) must be <= 40
    non_heavy_weights = [
        bw for b, bw in zip(bins, bin_weights)
        if not any(eid == "heavy" for eid, _ in b)
    ]
    assert all(bw <= 40 for bw in non_heavy_weights), (
        f"non-heavy bin exceeded cap: {non_heavy_weights}"
    )


# ---------------------------------------------------------------------------
# Whole-section mixing tests
# ---------------------------------------------------------------------------


def test_small_sections_mix_into_one_batch(tmp_path):
    """3 tiny sections that all fit within both caps → single mixed__01 batch."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    # sec_a: 1 EP weight 2; sec_b: 2 EPs weight 1 each; sec_c: 1 EP weight 1
    # total: 4 EPs, weight 5 — well within max_eps=10, max_weight=20
    _write_manifest(m, [
        {"id": "ep1", "weight": 2},
        {"id": "ep2", "weight": 1},
        {"id": "ep3", "weight": 1},
        {"id": "ep4", "weight": 1},
    ])
    _write_sections(s, [
        {"section_id": "sec_a", "name": "A", "ep_ids": ["ep1"]},
        {"section_id": "sec_b", "name": "B", "ep_ids": ["ep2", "ep3"]},
        {"section_id": "sec_c", "name": "C", "ep_ids": ["ep4"]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "10", "--max-weight", "20"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    assert data["summary"]["n_batches"] == 1
    b = data["batches"][0]
    assert b["batch_id"] == "mixed__01"
    assert set(b["section_ids"]) == {"sec_a", "sec_b", "sec_c"}
    assert set(b["ep_ids"]) == {"ep1", "ep2", "ep3", "ep4"}
    assert b["n_eps"] == 4
    assert b["total_weight"] == 5
    assert b["n_eps"] <= 10
    assert b["total_weight"] <= 20


def test_mixing_never_exceeds_caps(tmp_path):
    """FFD must never place a section that would push a batch over either cap."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    # sec_a: weight 7 n=2; sec_b: weight 6 n=2; sec_c: weight 5 n=1; sec_d: weight 4 n=1
    # FFD: sec_a+sec_b fit (13≤15, 4≤5); sec_c+sec_d fit (9≤15, 2≤5) — neither exceeds caps
    _write_manifest(m, [
        {"id": "a1", "weight": 4}, {"id": "a2", "weight": 3},
        {"id": "b1", "weight": 3}, {"id": "b2", "weight": 3},
        {"id": "c1", "weight": 5},
        {"id": "d1", "weight": 4},
    ])
    _write_sections(s, [
        {"section_id": "sec_a", "name": "A", "ep_ids": ["a1", "a2"]},
        {"section_id": "sec_b", "name": "B", "ep_ids": ["b1", "b2"]},
        {"section_id": "sec_c", "name": "C", "ep_ids": ["c1"]},
        {"section_id": "sec_d", "name": "D", "ep_ids": ["d1"]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "5", "--max-weight", "15"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    for b in data["batches"]:
        assert b["total_weight"] <= 15, (
            f"batch {b['batch_id']} exceeds max_weight: {b['total_weight']}"
        )
        assert b["n_eps"] <= 5, (
            f"batch {b['batch_id']} exceeds max_entrypoints: {b['n_eps']}"
        )


def test_oversized_section_splits_standalone_not_mixed(tmp_path):
    """An oversized section splits into <sid>__NN chunks; a fitting section stays separate."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    # big_sec: 6 EPs → n=6 > max_entrypoints=4 → oversized, splits standalone
    # tiny_sec: 1 EP → fits, never merged into an oversized chunk
    _write_manifest(m, [
        {"id": f"big_{i}", "weight": 2} for i in range(6)
    ] + [{"id": "small_ep", "weight": 1}])
    _write_sections(s, [
        {"section_id": "big_sec", "name": "Big",
         "ep_ids": [f"big_{i}" for i in range(6)]},
        {"section_id": "tiny_sec", "name": "Tiny",
         "ep_ids": ["small_ep"]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "4", "--max-weight", "20"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    batches = data["batches"]
    big_batches = [b for b in batches if "big_sec" in b.get("section_ids", [])]
    tiny_batches = [b for b in batches if "tiny_sec" in b.get("section_ids", [])]

    # Oversized section splits into multiple standalone chunks
    assert len(big_batches) >= 2, "oversized section should split into multiple batches"
    for b in big_batches:
        assert b["batch_id"].startswith("big_sec__"), (
            f"oversized chunk should have <sid>__NN id, got {b['batch_id']!r}"
        )
        assert b["section_ids"] == ["big_sec"], (
            f"oversized chunk should contain only big_sec, got {b['section_ids']}"
        )
        assert b["n_eps"] <= 4

    # Tiny fitting section stays separate (never merged into an oversized chunk)
    assert len(tiny_batches) == 1
    assert tiny_batches[0]["batch_id"] == "tiny_sec__01"
    assert tiny_batches[0]["section_ids"] == ["tiny_sec"]

    # No EP appears in both big and tiny batches
    big_eps = {eid for b in big_batches for eid in b["ep_ids"]}
    tiny_eps = {eid for b in tiny_batches for eid in b["ep_ids"]}
    assert big_eps & tiny_eps == set()


def test_coverage_preserved_under_mixing(tmp_path):
    """Union of all batches' ep_ids equals the full manifest EP set with no duplicates."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    # 2 small fitting sections + 1 oversized section
    _write_manifest(m, [
        {"id": "s1_ep1", "weight": 3},
        {"id": "s1_ep2", "weight": 2},
        {"id": "s2_ep1", "weight": 4},
        {"id": "big_ep1", "weight": 5},
        {"id": "big_ep2", "weight": 5},
        {"id": "big_ep3", "weight": 5},
        {"id": "big_ep4", "weight": 5},
        {"id": "big_ep5", "weight": 5},
    ])
    _write_sections(s, [
        {"section_id": "small1", "name": "Small1", "ep_ids": ["s1_ep1", "s1_ep2"]},
        {"section_id": "small2", "name": "Small2", "ep_ids": ["s2_ep1"]},
        {"section_id": "oversized", "name": "Big",
         "ep_ids": ["big_ep1", "big_ep2", "big_ep3", "big_ep4", "big_ep5"]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "3", "--max-weight", "20"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    all_ep_ids = [eid for b in data["batches"] for eid in b["ep_ids"]]
    manifest_ep_ids = {
        "s1_ep1", "s1_ep2", "s2_ep1",
        "big_ep1", "big_ep2", "big_ep3", "big_ep4", "big_ep5",
    }
    assert set(all_ep_ids) == manifest_ep_ids, "some EPs are missing from batches"
    assert len(all_ep_ids) == len(manifest_ep_ids), "some EPs appear more than once"


def test_mixed_batch_id_and_section_ids(tmp_path):
    """A multi-section batch has batch_id='mixed__NN' and len(section_ids)>1."""
    m = tmp_path / "manifest.json"
    s = tmp_path / "sections.json"
    o = tmp_path / "batches.json"

    _write_manifest(m, [
        {"id": "ep1", "weight": 3},
        {"id": "ep2", "weight": 2},
    ])
    _write_sections(s, [
        {"section_id": "alpha", "name": "Alpha", "ep_ids": ["ep1"]},
        {"section_id": "beta", "name": "Beta", "ep_ids": ["ep2"]},
    ])

    r = _run_batch(m, s, o, ["--max-entrypoints", "5", "--max-weight", "20"])
    assert r.returncode == 0, r.stderr

    data = _load(o)
    mixed = [b for b in data["batches"] if b["batch_id"].startswith("mixed__")]
    assert len(mixed) == 1, (
        f"expected 1 mixed batch, got {[b['batch_id'] for b in data['batches']]}"
    )
    b = mixed[0]
    assert len(b["section_ids"]) > 1
    assert "alpha" in b["section_ids"]
    assert "beta" in b["section_ids"]


# ===========================================================================
# Tests — merge-reports (from test_merge_reports.py)
# ===========================================================================


def test_merge_reports_end_to_end(tmp_path):
    """Merge-reports produces run_index, REPORT.md, rollups, and review sections."""
    val_root, batches_dir = _setup_batches(tmp_path)
    result = _run_merge(batches_dir, val_root, run_id="my-custom-run")
    assert result.returncode == 0, f"stderr: {result.stderr}"

    out = result.stdout.strip()
    assert out.endswith("REPORT.md") and Path(out).exists()

    assert (val_root / "run_index.json").exists()
    assert (val_root / "results" / "REPORT.md").exists()

    data = json.loads((val_root / "run_index.json").read_text())
    assert data["run"]["id"] == "my-custom-run"
    assert data["run"]["n_batches"] == 3
    assert len(data["batches"]) == 3
    assert len(data["entrypoints"]) == 3
    assert data["run"]["status"] == "partial"

    by_id = {ep["id"]: ep for ep in data["entrypoints"]}
    assert by_id["ep_alpha"]["batch_id"] == "batch_a"
    assert by_id["ep_beta"]["batch_id"] == "batch_a"
    assert by_id["ep_gamma"]["batch_id"] == "batch_b"

    t = data["totals"]
    assert t["passed"] == 1 and t["passed_no_baseline"] == 1 and t["hard_stuck"] == 1
    assert t["match"] == 1 and t["no_baseline"] == 1 and t["real_divergence"] == 1

    pe_batch_ids = [pe.get("batch_id") for pe in data["parse_errors"]]
    assert "batch_c" in pe_batch_ids

    by_batch = {b["batch_id"]: b for b in data["batches"]}
    assert by_batch["batch_a"]["report_path"] == "batches/batch_a/results/REPORT.md"
    assert by_batch["batch_b"]["report_path"] is None

    report = (val_root / "results" / "REPORT.md").read_text()
    assert "Needs Human Review" in report
    assert "ep_gamma" in report and "ep_beta" in report
    review_section = report.split("## Needs Human Review")[-1]
    assert "ep_alpha" not in review_section


def test_merge_status_rollup_passed(tmp_path):
    """All-pass scenario should produce status=passed."""
    val_root = tmp_path / "Validation"
    batches_dir = val_root / "batches"
    batches_dir.mkdir(parents=True)

    batch = batches_dir / "batch_ok"
    batch.mkdir()
    eps = [
        _make_ep("ep_one", "passed", "match"),
        _make_ep("ep_two", "passed_no_baseline", "no_baseline"),
    ]
    (batch / "run_index.json").write_text(
        json.dumps(_make_run_index("run-ok", "passed", eps)), encoding="utf-8"
    )

    _run_merge(batches_dir, val_root)
    data = json.loads((val_root / "run_index.json").read_text())
    assert data["run"]["status"] == "passed"


def test_merge_status_rollup_in_progress(tmp_path):
    """A batch with status=in_progress should bubble up."""
    val_root = tmp_path / "Validation"
    batches_dir = val_root / "batches"
    batches_dir.mkdir(parents=True)

    batch = batches_dir / "batch_wip"
    batch.mkdir()
    eps = [_make_ep("ep_wip", "passed", "match")]
    idx = _make_run_index("run-wip", "in_progress", eps)
    (batch / "run_index.json").write_text(json.dumps(idx), encoding="utf-8")

    _run_merge(batches_dir, val_root)
    data = json.loads((val_root / "run_index.json").read_text())
    assert data["run"]["status"] == "in_progress"


def test_merge_missing_run_index_warns_and_records_batch(tmp_path):
    val_root = tmp_path / "Validation"
    batches_dir = val_root / "batches"
    batches_dir.mkdir(parents=True)

    batch_missing = batches_dir / "batch_missing"
    batch_missing.mkdir()
    # Deliberately no run_index.json

    merged = batch.merge(batches_dir, val_root, "test-run")
    assert any("batch_missing" in w and "missing run_index.json" in w for w in merged["warnings"])
    by_batch = {b["batch_id"]: b for b in merged["batches"]}
    assert by_batch["batch_missing"]["status"] == "missing"
    assert by_batch["batch_missing"]["n_entrypoints"] == 0


def test_merge_report_includes_warnings_section(tmp_path):
    val_root = tmp_path / "Validation"
    batches_dir = val_root / "batches"
    batches_dir.mkdir(parents=True)
    (batches_dir / "batch_missing").mkdir()

    merged = batch.merge(batches_dir, val_root, "test-run")
    report = batch._render_report(merged)
    assert "## Warnings" in report
    assert "missing run_index.json" in report


def test_assemble_from_prepared_skips_pool_failed_batches(tmp_path):
    worktrees_root = tmp_path / "worktrees"
    out_dir = tmp_path / "Validation"
    out_dir.mkdir(parents=True)

    wt = worktrees_root / "wt_ok"
    val = wt / "Validation"
    val.mkdir(parents=True)
    (val / "run_index.json").write_text(
        json.dumps(_make_run_index("run-ok", "passed", [_make_ep("ep1", "passed", "match")])),
        encoding="utf-8",
    )

    prepared_path = tmp_path / "batches_prepared.json"
    prepared_path.write_text(json.dumps({
        "base_sha": "abc123",
        "batches": [
            {
                "batch_id": "batch_ok",
                "worktree": str(wt),
                "run_id": "run-ok",
                "validation_branch": "val/ok",
                "ep_ids": ["ep1"],
                "error": None,
            },
            {
                "batch_id": "batch_failed",
                "worktree": str(worktrees_root / "wt_fail"),
                "run_id": "run-fail",
                "validation_branch": "val/fail",
                "ep_ids": ["ep2"],
                "error": None,
            },
        ],
    }), encoding="utf-8")

    (out_dir / "pool_status.json").write_text(json.dumps({
        "updated_at": "2026-01-01T00:00:00+00:00",
        "run": {"status": "partial", "n_batches": 2, "n_done": 1, "n_failed": 1},
        "batches": [
            {"batch_id": "batch_ok", "status": "done"},
            {
                "batch_id": "batch_failed",
                "status": "failed",
                "error": "missing required artifact: Validation/results/summary.json",
            },
        ],
    }), encoding="utf-8")

    _, warnings = batch.assemble_from_prepared(prepared_path, out_dir)
    assert (out_dir / "batches" / "batch_ok" / "run_index.json").exists()
    assert not (out_dir / "batches" / "batch_failed").exists()
    warning_text = " ".join(warnings)
    assert "batch_failed" in warning_text
    assert "summary.json" in warning_text


def test_merge_missing_batches_dir_exits_1(tmp_path):
    result = _run_merge(tmp_path / "nonexistent", tmp_path / "out")
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# --prepared mode tests
# ---------------------------------------------------------------------------


def _run_merge_prepared(
    prepared_path: Path, out_dir: Path, run_id: str = "test-run"
):
    return run_cli(batch.merge_main, [
        "--prepared", str(prepared_path),
        "--out", str(out_dir),
        "--run-id", run_id,
    ])


def _setup_prepared(tmp_path: Path) -> tuple[Path, Path]:
    """Create fake worktrees + batches_prepared.json.

    Batches:
      batch_good_a  — valid Validation dir with run_index.json + REPORT.md
      batch_good_b  — valid Validation dir with run_index.json (no REPORT.md)
      batch_err     — has error set → should be skipped
      batch_nodir   — error=null but Validation dir missing → should be skipped
    """
    worktrees_root = tmp_path / "worktrees"
    out_dir = tmp_path / "Validation"

    # batch_good_a
    wt_a = worktrees_root / "wt_a"
    val_a = wt_a / "Validation"
    (val_a / "results").mkdir(parents=True)
    (val_a / "results" / "REPORT.md").write_text("# Batch A\n")
    eps_a = [
        _make_ep("ep_one", "passed", "match"),
        _make_ep("ep_two", "passed_no_baseline", "no_baseline"),
    ]
    (val_a / "run_index.json").write_text(
        json.dumps(_make_run_index("run-a", "passed", eps_a)), encoding="utf-8"
    )

    # batch_good_b
    wt_b = worktrees_root / "wt_b"
    val_b = wt_b / "Validation"
    val_b.mkdir(parents=True)
    eps_b = [_make_ep("ep_three", "hard_stuck", "real_divergence")]
    (val_b / "run_index.json").write_text(
        json.dumps(_make_run_index("run-b", "partial", eps_b)), encoding="utf-8"
    )

    # batch_err — no worktree needed; error field set
    wt_err = worktrees_root / "wt_err"  # won't be read

    # batch_nodir — worktree exists but no Validation subdir
    wt_nodir = worktrees_root / "wt_nodir"
    wt_nodir.mkdir(parents=True)

    prepared = {
        "base_sha": "abc123",
        "worktrees_dir": str(worktrees_root),
        "batches": [
            {
                "batch_id": "batch_good_a",
                "worktree": str(wt_a),
                "run_id": "run-a",
                "validation_branch": "val/batch_good_a",
                "ep_ids": ["ep_one", "ep_two"],
                "error": None,
            },
            {
                "batch_id": "batch_good_b",
                "worktree": str(wt_b),
                "run_id": "run-b",
                "validation_branch": "val/batch_good_b",
                "ep_ids": ["ep_three"],
                "error": None,
            },
            {
                "batch_id": "batch_err",
                "worktree": str(wt_err),
                "run_id": None,
                "validation_branch": None,
                "ep_ids": [],
                "error": "worker crashed",
            },
            {
                "batch_id": "batch_nodir",
                "worktree": str(wt_nodir),
                "run_id": None,
                "validation_branch": None,
                "ep_ids": [],
                "error": None,
            },
        ],
    }

    prepared_path = tmp_path / "batches_prepared.json"
    prepared_path.write_text(json.dumps(prepared), encoding="utf-8")
    return prepared_path, out_dir


def test_merge_prepared_end_to_end(tmp_path):
    """--prepared merges good batches, skips failures, and emits warnings."""
    prepared_path, out_dir = _setup_prepared(tmp_path)
    result = _run_merge_prepared(prepared_path, out_dir)
    assert result.returncode == 0, f"stderr: {result.stderr}"

    out = result.stdout.strip()
    assert out.endswith("REPORT.md") and Path(out).exists()

    assert (out_dir / "batches" / "batch_good_a" / "run_index.json").exists()
    assert (out_dir / "batches" / "batch_good_a" / "results" / "REPORT.md").exists()
    assert (out_dir / "batches" / "batch_good_b" / "run_index.json").exists()
    assert not (out_dir / "batches" / "batch_err").exists()
    assert not (out_dir / "batches" / "batch_nodir").exists()
    assert (out_dir / "results" / "REPORT.md").exists()

    data = json.loads((out_dir / "run_index.json").read_text())
    ep_ids = {ep["id"] for ep in data["entrypoints"]}
    assert ep_ids == {"ep_one", "ep_two", "ep_three"}

    t = data["totals"]
    assert t["entrypoints"] == 3
    assert t["passed"] == 1 and t["passed_no_baseline"] == 1 and t["hard_stuck"] == 1

    warning_text = " ".join(data.get("warnings", []))
    assert "batch_err" in warning_text and "batch_nodir" in warning_text


def test_prepared_batch_id_traversal_rejected(tmp_path):
    """A malicious batch_id (path-traversal/shell-chars) must not be used as a
    destination directory component — assemble_from_prepared should refuse the
    bad batch_id, emit a warning, and never write outside out_dir/batches."""
    worktrees_root = tmp_path / "worktrees"
    out_dir = tmp_path / "Validation"

    # Build a real worktree the merger could otherwise copy from.
    wt = worktrees_root / "wt_real"
    val = wt / "Validation"
    (val / "results").mkdir(parents=True)
    (val / "results" / "REPORT.md").write_text("# evil\n")
    (val / "run_index.json").write_text(
        json.dumps(_make_run_index("evil-run", "passed", [_make_ep("ep_x", "passed", "match")])),
        encoding="utf-8",
    )

    prepared_path = tmp_path / "batches_prepared.json"
    prepared_path.write_text(
        json.dumps({
            "base_sha": "abc123",
            "worktrees_dir": str(worktrees_root),
            "batches": [{
                "batch_id": "../escape",
                "worktree": str(wt),
                "run_id": "evil-run",
                "validation_branch": "val/escape",
                "ep_ids": ["ep_x"],
                "error": None,
            }],
        }),
        encoding="utf-8",
    )
    result = _run_merge_prepared(prepared_path, out_dir)

    # The merge succeeds at the top level; the offending batch is skipped.
    assert result.returncode == 0, f"stderr={result.stderr}"
    # Nothing was written under out_dir/batches (the only legitimate
    # destination); critically, no directory was created via the "../" prefix.
    batches_dir = out_dir / "batches"
    if batches_dir.exists():
        assert list(batches_dir.iterdir()) == [], (
            f"batches dir should be empty; got {list(batches_dir.iterdir())}"
        )
    # No directory escaped above out_dir.
    assert not (tmp_path / "escape").exists(), "path traversal succeeded"
    # The bad batch_id is surfaced via warnings in run_index.json.
    data = json.loads((out_dir / "run_index.json").read_text())
    assert any("escape" in w for w in data.get("warnings", [])), (
        f"warnings did not mention the rejected batch_id; got {data.get('warnings')}"
    )


# ---------------------------------------------------------------------------
# Mutual-exclusion tests
# ---------------------------------------------------------------------------


def test_both_args_exits_2(tmp_path):
    result = run_cli(batch.merge_main, [
        "--batches-dir", str(tmp_path / "batches"),
        "--prepared", str(tmp_path / "prepared.json"),
        "--out", str(tmp_path / "out"),
    ])
    assert result.returncode == 2


def test_neither_arg_exits_2(tmp_path):
    result = run_cli(batch.merge_main, ["--out", str(tmp_path / "out")])
    assert result.returncode == 2


# ===========================================================================
# Tests — pool (from test_pool.py)
# ===========================================================================


def test_write_status_atomic(tmp_path):
    b1 = _make_batch("b1", worktree=str(tmp_path / "wt1"))
    b2 = _make_batch("b2", worktree=str(tmp_path / "wt2"))
    b1.current_phase = "patching"
    b2.current_phase = "synthesizing"
    b1.session_id = "sess-aaa"
    b2.session_id = "sess-bbb"

    batch._write_status([b1, b2], pool_size=3, out_dir=tmp_path)

    status_file = tmp_path / "pool_status.json"
    assert status_file.exists()
    data = json.loads(status_file.read_text())
    assert data["run"]["pool_size"] == 3
    assert data["run"]["n_batches"] == 2
    assert "updated_at" in data
    assert len(data["batches"]) == 2

    expected_keys = {
        "batch_id", "status", "current_phase", "session_id",
        "started_at", "updated_at", "attempt",
        "error", "metrics", "summary_json_path",
    }
    forbidden_keys = {"last_tool", "log_path"}
    for entry in data["batches"]:
        assert set(entry.keys()) == expected_keys, f"Unexpected keys: {set(entry.keys())}"
        for key in forbidden_keys:
            assert key not in entry, f"Forbidden key {key!r} present in batch entry"

    phases = {e["batch_id"]: e["current_phase"] for e in data["batches"]}
    assert phases["b1"] == "patching"
    assert phases["b2"] == "synthesizing"


def test_derive_phase_labels():
    """Phase labels track milestones, progress counts, and terminal completion."""
    assert batch._derive_phase(None) == "starting"
    assert batch._derive_phase({}) == "starting"

    assert batch._derive_phase({"milestones": {"entrypoints_selected": True}}) == "synthesizing"
    assert batch._derive_phase({
        "milestones": {"entrypoints_selected": True, "synth_deep": True, "patches_authored": True,
                       "phase_a_complete": True},
    }) == "Phase B"

    # phase_a_complete milestone (no trials overlay) → Phase B base label
    assert batch._derive_phase({
        "milestones": {"entrypoints_selected": True, "synth_deep": True,
                       "patches_authored": True, "phase_a_complete": True},
        "phase": "phase_a_done",
        "trials": {"ep1": {"status": "passed", "phase_a_iters": [{"iter": 1}]}},
    }) == "Phase B (1/1 terminal)"

    import validate  # noqa: E402
    used = {
        "entrypoints_selected", "synth_deep", "patches_authored",
        "phase_a_complete", "phase_b_complete",
    }
    assert used.issubset(set(validate.CANONICAL_MILESTONES))

    ms_a = {
        "entrypoints_selected": True, "synth_deep": True,
        "patches_authored": True,
    }
    trials_a = {
        # latest Phase A iter produced a clean baseline -> settled
        "ep1": {"status": "pending", "phase_a_iters": [{"iter": 1, "passing": 2, "failing": 0}]},
        # no Phase A iters yet -> not settled
        "ep2": {"status": "pending", "phase_a_iters": []},
        # Phase A skipped -> settled
        "ep3": {"status": "phase_a_skipped", "phase_a_iters": []},
        # passed iter 3 then regressed iter 4 -> latest failed -> NOT settled
        "ep4": {"status": "pending", "phase_a_iters": [
            {"iter": 3, "passing": 1, "failing": 0},
            {"iter": 4, "passing": 0, "failing": 1}]},
    }
    assert batch._derive_phase({"milestones": ms_a, "phase": "init", "trials": trials_a}) == "Phase A (2/4 done)"

    trials_b = {
        "ep1": {"status": "passed"},
        "ep2": {"status": "pending"},
        "ep3": {"status": "hard_stuck"},
    }
    assert batch._derive_phase({
        "milestones": {"phase_a_complete": True},
        "phase": "phase_a_done",
        "trials": trials_b,
    }) == "Phase B (2/3 terminal)"

    # Reopen state: phase rewound to phase_a_done, phase_b_complete explicitly
    # cleared, phase_a_complete still set → back to the Phase B overlay (NOT complete).
    assert batch._derive_phase({
        "milestones": {"phase_a_complete": True, "phase_b_complete": False},
        "phase": "phase_a_done",
        "trials": trials_b,
    }) == "Phase B (2/3 terminal)"

    # phase_b_complete milestone alone signals completion (even before phase field flips)
    assert batch._derive_phase({
        "milestones": {"phase_b_complete": True},
        "trials": {"ep1": {"status": "passed"}},
    }) == "Phase B complete"

    assert batch._derive_phase({
        "milestones": {}, "phase": "phase_b_done",
        "trials": {"ep1": {"status": "passed"}},
    }) == "Phase B complete"


def test_phase_watcher_refreshes_on_state_change(tmp_path):
    """The watcher polls each running batch's state.json on a fixed cadence,
    independent of any SDK message. Simulate state.json advancing and
    confirm the batch's current_phase catches up."""
    wt = tmp_path / "wt1"
    (wt / "Validation").mkdir(parents=True)
    state_path = wt / "Validation" / "state.json"

    state_path.write_text(json.dumps(
        {"milestones": {"entrypoints_selected": True}}
    ), encoding="utf-8")

    b = _make_batch("b1", worktree=str(wt), status="running", current_phase="starting")
    writes = []

    def _write():
        writes.append(b.current_phase)

    async def _drive():
        stop = asyncio.Event()
        task = asyncio.create_task(batch._phase_watcher([b], _write, stop, interval=0.05))
        # First tick should promote "starting" -> "synthesizing".
        await asyncio.sleep(0.15)
        # State advances (without any SDK message).
        state_path.write_text(json.dumps(
            {"milestones": {"entrypoints_selected": True, "synth_deep": True}}
        ), encoding="utf-8")
        await asyncio.sleep(0.15)
        stop.set()
        await task

    asyncio.run(_drive())
    assert b.current_phase == "patching", (
        f"phase did not update after state.json change; got {b.current_phase!r}"
    )
    assert "synthesizing" in writes
    assert "patching" in writes

    b_done = _make_batch("done", worktree=str(wt), status="done", current_phase="-")
    b_wait = _make_batch("wait", worktree=str(wt), status="queued", current_phase="starting")

    async def _drive_terminal():
        stop = asyncio.Event()
        task = asyncio.create_task(
            batch._phase_watcher([b_done, b_wait], lambda: None, stop, interval=0.02)
        )
        await asyncio.sleep(0.1)
        stop.set()
        await task

    asyncio.run(_drive_terminal())
    assert b_done.current_phase == "-"
    assert b_wait.current_phase == "starting"


def test_build_prompt_friction_log(tmp_path):
    b = _make_batch("b1", worktree=str(tmp_path / "wt1"))
    args = _make_args(tmp_path)
    prompt = batch._build_prompt(b, args, "deadbeef")
    assert "FRICTION_LOG" not in prompt and "FRICTION LOG" not in prompt

    log_path = str(tmp_path / "friction.md")
    args.friction_log = log_path
    prompt = batch._build_prompt(b, args, "deadbeef")
    assert f"FRICTION_LOG={log_path}" in prompt
    assert "FRICTION LOG (shared across batches)" in prompt


def test_pool_cli_accepts_friction_log_flag(tmp_path):
    """--friction-log parses cleanly on the pool subcommand."""
    r = _run_pool([
        "--prepared", str(tmp_path / "does_not_exist.json"),
        "--primary-conv-root", str(tmp_path),
        "--original-source", "/fake",
        "--connection", "sfctest0",
        "--skill-directory", str(tmp_path),
        "--friction-log", str(tmp_path / "friction.md"),
    ])
    assert "unrecognized" not in (r.stderr + r.stdout).lower()
    assert "--friction-log" not in r.stderr.lower() or "does_not_exist" in (r.stderr + r.stdout).lower()


def test_run_batch_success_no_logs(tmp_path, monkeypatch):
    worktree = tmp_path / "wt1"
    _write_valid_batch_artifacts(worktree)

    b = _make_batch("b1", worktree=str(worktree))
    args = _make_args(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    usage_dict = {
        "input_tokens": 1000,
        "output_tokens": 50,
        "cache_creation_input_tokens": 200,
        "cache_read_input_tokens": 700,
    }
    messages = [
        _system_init("real-123"),
        _assistant_msg(content=[ToolUseBlock(id="t1", name="Bash", input={})]),
        _result_msg(
            is_error=False,
            session_id="real-123",
            usage=usage_dict,
            duration_ms=1234,
            num_turns=5,
        ),
    ]
    monkeypatch.setattr(batch, "query", make_fake_query(messages))

    result = asyncio.run(batch.run_batch(b, args, out_dir, "base-sha", lambda: None))

    assert result is True
    assert b.session_id == "real-123"
    assert b.summary_json_path is not None
    assert b.summary_json_path.endswith("summary.json")

    # Metrics captured from ResultMessage.usage
    assert b.metrics["input_tokens"] == 1000
    assert b.metrics["output_tokens"] == 50
    assert b.metrics["cache_creation_input_tokens"] == 200
    assert b.metrics["cache_read_input_tokens"] == 700
    assert b.metrics["duration_ms"] == 1234
    assert b.metrics["num_turns"] == 5

    # run_batch itself doesn't touch current_phase — that's _phase_watcher's
    # job (reads state.json). No state.json here → stays at "starting".
    assert b.current_phase == "starting"

    # NO per-batch log files or pool/ dir.
    assert not (out_dir / "pool").exists(), "pool/ dir must not be created"
    assert not any(out_dir.rglob("agent.log")), "agent.log must not be created"
    assert not any(out_dir.rglob("agent.events.jsonl")), "agent.events.jsonl must not be created"
    # session.jsonl feature was intentionally removed.
    assert not (worktree / "Validation" / "session.jsonl").exists()


def test_run_batch_failure_is_error(tmp_path, monkeypatch):
    worktree = tmp_path / "wt1"
    summary_dir = worktree / "Validation" / "results"
    summary_dir.mkdir(parents=True)
    (summary_dir / "summary.json").write_text("{}")

    b = _make_batch("b1", worktree=str(worktree))
    args = _make_args(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    messages = [
        _system_init("real-123"),
        _result_msg(is_error=True, session_id="real-123"),
    ]
    monkeypatch.setattr(batch, "query", make_fake_query(messages))

    result = asyncio.run(batch.run_batch(b, args, out_dir, "base-sha", lambda: None))
    assert result is False
    assert b.error == "SDK session ended with error"


def test_run_batch_artifact_verification_failures(tmp_path, monkeypatch):
    """run_batch rejects incomplete or invalid Validation artifacts."""
    cases = [
        ("no_summary", {}, "missing required artifact", "summary.json"),
        ("invalid_json", {"summary.json": "{ not json"}, "invalid summary.json", None),
        ("no_decision", {"summary.json": "{}"}, "decision.overall", None),
        (
            "no_run_index",
            {
                "summary.json": json.dumps({"decision": {"overall": "passed"}}),
                "REPORT.md": "# report\n",
            },
            "run_index.json",
            None,
        ),
    ]
    for name, files, err_fragment, err_fragment2 in cases:
        worktree = tmp_path / f"wt_{name}"
        results_dir = worktree / "Validation" / "results"
        results_dir.mkdir(parents=True)
        for rel, content in files.items():
            (results_dir / rel).write_text(content, encoding="utf-8")

        b = _make_batch("b1", worktree=str(worktree))
        args = _make_args(tmp_path)
        messages = [_system_init("real-123"), _result_msg(is_error=False, session_id="real-123")]
        monkeypatch.setattr(batch, "query", make_fake_query(messages))

        result = asyncio.run(batch.run_batch(b, args, tmp_path / "out", "base-sha", lambda: None))
        assert result is False, name
        assert err_fragment in (b.error or ""), (name, b.error)
        if err_fragment2:
            assert err_fragment2 in (b.error or ""), (name, b.error)


def test_verify_batch_completion_accepts_valid_artifacts(tmp_path):
    worktree = tmp_path / "wt1"
    _write_valid_batch_artifacts(worktree)
    ok, err = batch._verify_batch_completion(str(worktree))
    assert ok is True
    assert err is None


def test_pool_respects_concurrency_and_replenishes(tmp_path, monkeypatch):
    counter = {"current": 0, "max": 0}

    async def fake_run_batch(b, args, out_dir, base_sha, write_status_fn):
        counter["current"] += 1
        counter["max"] = max(counter["max"], counter["current"])
        await asyncio.sleep(0.05)
        counter["current"] -= 1
        return True

    monkeypatch.setattr(batch, "run_batch", fake_run_batch)

    n = 7
    batches = [
        _make_batch(f"b{i}", worktree=str(tmp_path / f"wt{i}"))
        for i in range(n)
    ]
    args = _make_args(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    queue: asyncio.Queue = asyncio.Queue()
    for b in batches:
        queue.put_nowait(b)
    pending = {b.batch_id for b in batches}

    async def _run():
        workers = [
            batch._worker(i, queue, pending, batches, args, out_dir, "sha", 1,
                         lambda: None)
            for i in range(3)
        ]
        await asyncio.gather(*workers)

    asyncio.run(_run())

    assert counter["max"] <= 3
    assert all(b.status == "done" for b in batches)


def test_pool_retry_behavior(tmp_path, monkeypatch):
    """Worker retries transient failures, clears stale errors, and exhausts cleanly."""
    async def _noop_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    calls: dict[str, int] = {}

    async def succeed_on_second(b, args, out_dir, base_sha, write_status_fn):
        calls[b.batch_id] = calls.get(b.batch_id, 0) + 1
        return calls[b.batch_id] > 1

    monkeypatch.setattr(batch, "run_batch", succeed_on_second)
    b_ok = _make_batch("b_ok", worktree=str(tmp_path / "wt_ok"))
    args = _make_args(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(b_ok)
    pending = {b_ok.batch_id}
    asyncio.run(batch._worker(0, queue, pending, [b_ok], args, out_dir, "sha", 1, lambda: None))
    assert b_ok.status == "done" and b_ok.attempt == 1

    calls.clear()

    async def raise_then_succeed(b, args, out_dir, base_sha, write_status_fn):
        calls[b.batch_id] = calls.get(b.batch_id, 0) + 1
        if calls[b.batch_id] == 1:
            b.error = "transient failure"
            raise RuntimeError("transient failure")
        return True

    monkeypatch.setattr(batch, "run_batch", raise_then_succeed)
    b_clear = _make_batch("b_clear", worktree=str(tmp_path / "wt_clear"))
    queue = asyncio.Queue()
    queue.put_nowait(b_clear)
    pending = {b_clear.batch_id}
    asyncio.run(batch._worker(0, queue, pending, [b_clear], args, out_dir, "sha", 1, lambda: None))
    assert b_clear.status == "done" and b_clear.error is None

    async def always_fail(b, args, out_dir, base_sha, write_status_fn):
        return False

    monkeypatch.setattr(batch, "run_batch", always_fail)
    b_fail = _make_batch("b_fail", worktree=str(tmp_path / "wt_fail"))
    queue = asyncio.Queue()
    queue.put_nowait(b_fail)
    pending = {b_fail.batch_id}
    asyncio.run(batch._worker(0, queue, pending, [b_fail], args, out_dir, "sha", 1, lambda: None))
    assert b_fail.status == "failed" and b_fail.attempt == 1


def test_worker_propagates_cancellation(tmp_path, monkeypatch):
    """External cancellation (e.g. Ctrl+C) must surface as CancelledError out
    of the worker task instead of being swallowed by the `except BaseException`
    handler around run_batch."""

    async def slow_run_batch(b, args, out_dir, base_sha, write_status_fn):
        await asyncio.sleep(10)  # plenty of time to be cancelled
        return True

    monkeypatch.setattr(batch, "run_batch", slow_run_batch)
    b = _make_batch("b1", worktree=str(tmp_path / "wt1"))
    args = _make_args(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(b)
    pending = {b.batch_id}

    async def _go():
        task = asyncio.create_task(
            batch._worker(0, queue, pending, [b], args, out_dir, "sha", 1,
                          lambda: None)
        )
        # Let the worker enter `await run_batch(...)`.
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(_go()), (
        "Worker swallowed CancelledError instead of propagating it"
    )


def test_pool_no_eligible_batches_still_writes_result(tmp_path, monkeypatch):
    """When every prepared batch already has an error, `_run` must still write
    a well-formed pool_status.json (run.n_batches=0, batches=[]) and exit 0,
    so the orchestrator's Step 5 reader doesn't FileNotFoundError."""
    # Stub out the merge-reports subprocess — we don't have a real batch tree.
    monkeypatch.setattr(batch, "_run_merge_reports", lambda args, out_dir: None)

    prepared = {
        "base_sha": "deadbeef",
        "batches": [
            {"batch_id": "b1", "error": "worker crashed", "worktree": str(tmp_path / "wt1")},
            {"batch_id": "b2", "error": "init failed",    "worktree": str(tmp_path / "wt2")},
        ],
    }
    prepared_path = tmp_path / "batches_prepared.json"
    prepared_path.write_text(json.dumps(prepared), encoding="utf-8")

    args = _make_args(tmp_path, prepared_path=prepared_path)
    rc = asyncio.run(batch._run(args))
    assert rc == 0

    out_dir = Path(args.primary_conv_root) / "Validation"
    result = json.loads((out_dir / "pool_status.json").read_text())
    assert result["run"]["n_batches"] == 0
    assert result["run"]["n_done"] == 0
    assert result["run"]["n_failed"] == 0
    assert result["batches"] == []
    assert "merge_report_path" in result  # top-level key, possibly None
    assert "metrics_totals" in result["run"]


def test_run_exits_1_on_any_failed_batch(tmp_path, monkeypatch):
    """When at least one batch ends up terminal-failed (retries exhausted),
    `_run` writes pool_status.json and returns exit 1."""

    async def always_fail(b, args, out_dir, base_sha, write_status_fn):
        return False  # every attempt fails → exhausts retries

    monkeypatch.setattr(batch, "run_batch", always_fail)
    monkeypatch.setattr(batch, "_run_merge_reports", lambda args, out_dir: None)

    # One eligible batch pointing at a real worktree dir so any peripheral I/O
    # doesn't blow up. The batch will end status="failed" after zero retries.
    wt = tmp_path / "wt"
    (wt / "Validation").mkdir(parents=True)
    prepared = {
        "base_sha": "deadbeef",
        "batches": [{
            "batch_id": "b1",
            "ep_ids": ["ep_a"],
            "n_eps": 1,
            "total_weight": 1,
            "worktree": str(wt),
            "run_id": "run-b1",
            "validation_branch": "validation/b1",
            "error": None,
        }],
    }
    prepared_path = tmp_path / "batches_prepared.json"
    prepared_path.write_text(json.dumps(prepared), encoding="utf-8")

    args = _make_args(tmp_path, prepared_path=prepared_path)
    args.retries = 0  # no retry → first failure is terminal
    rc = asyncio.run(batch._run(args))
    assert rc == 1

    out_dir = Path(args.primary_conv_root) / "Validation"
    result = json.loads((out_dir / "pool_status.json").read_text())
    assert result["run"]["n_batches"] == 1
    assert result["run"]["n_failed"] == 1
    assert result["run"]["n_done"] == 0
    assert result["batches"][0]["status"] == "failed"


def test_pool_missing_prepared_exits_1(tmp_path):
    """`batch.py pool --prepared <nonexistent>` must fail cleanly with exit 1."""
    r = _run_pool([
        "--prepared", str(tmp_path / "missing.json"),
        "--primary-conv-root", str(tmp_path),
        "--original-source", str(tmp_path),
        "--connection", "test_conn",
        "--skill-directory", str(tmp_path),
        "--pool-size", "5",
    ])
    assert r.returncode == 1, f"expected exit 1; got {r.returncode}\nstderr={r.stderr}"
    combined = r.stderr + r.stdout
    assert "--prepared file does not exist" in combined
    assert "FileNotFoundError" not in combined
    assert "Traceback" not in combined


def test_pool_malformed_prepared_exits_1(tmp_path):
    bad = tmp_path / "prepared.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    r = _run_pool([
        "--prepared", str(bad),
        "--primary-conv-root", str(tmp_path),
        "--original-source", str(tmp_path),
        "--connection", "test_conn",
        "--skill-directory", str(tmp_path),
        "--pool-size", "5",
    ])
    assert r.returncode == 1, f"expected exit 1; got {r.returncode}\nstderr={r.stderr}"
    combined = r.stderr + r.stdout
    assert "could not read --prepared file" in combined
    assert "Traceback" not in combined


def test_pool_size_zero_exits_2(tmp_path):
    r = _run_pool([
        "--prepared", str(tmp_path / "does_not_exist.json"),
        "--primary-conv-root", str(tmp_path),
        "--original-source", str(tmp_path),
        "--connection", "test_conn",
        "--skill-directory", str(tmp_path),
        "--pool-size", "0",
    ])
    assert r.returncode == 2, f"expected exit 2; got {r.returncode}\nstderr={r.stderr}"
    assert "--pool-size must be >= 1" in (r.stderr + r.stdout)
    assert "FileNotFoundError" not in (r.stderr + r.stdout)
    assert "does_not_exist.json" not in (r.stderr + r.stdout)


def test_pool_status_render():
    """Running and done pool status headers include batch ordering and totals."""
    running = batch._render_pool_status({
        "updated_at": "2026-07-01T20:10:00+00:00",
        "run": {
            "status": "running", "n_done": 1, "n_failed": 1, "n_batches": 4,
            "started_at": "2026-07-01T20:00:00+00:00",
            "metrics_totals": {"total_tokens": 82601},
        },
        "batches": [
            {"batch_id": "b1", "status": "done", "current_phase": "Phase B complete",
             "metrics": {"input_tokens": 40000, "output_tokens": 300,
                         "cache_creation_input_tokens": 10000,
                         "cache_read_input_tokens": 32301}},
            {"batch_id": "b2", "status": "running", "current_phase": "Phase A (1/2 done)"},
            {"batch_id": "b3", "status": "queued", "current_phase": None},
            {"batch_id": "b4", "status": "failed", "current_phase": "patching",
             "error": "worker exited nonzero"},
        ],
    })
    lines = running.splitlines()
    assert lines[0].startswith("[pool] running 10m: 1/4 done, 1 failed, 1 running, 1 queued")
    assert "pool tokens: 82,601" in lines[0]

    done = batch._render_pool_status({
        "updated_at": "2026-07-01T21:03:00+00:00",
        "run": {
            "status": "done", "n_done": 2, "n_failed": 0, "n_batches": 2,
            "started_at": "2026-07-01T20:00:00+00:00",
            "finished_at": "2026-07-01T21:03:00+00:00",
            "metrics_totals": {"total_tokens": 4201830},
        },
        "merge_report_path": "/x/REPORT.md",
        "batches": [
            {"batch_id": "b1", "status": "done", "current_phase": "Phase B complete"},
            {"batch_id": "b2", "status": "done", "current_phase": "Phase B complete"},
        ],
    })
    assert done.splitlines()[0].startswith("[pool] done in 1h 3m: 2/2 done, 0 failed, 0 running, 0 queued")
    assert "report: /x/REPORT.md" in done.splitlines()[0]


def test_pool_status_cli_running_and_missing(tmp_path):
    """pool-status CLI handles missing, malformed, and running pool_status.json."""
    d = tmp_path / "Validation"
    d.mkdir()

    r = _run_pool_status(d)
    assert r.returncode == 0
    assert "not yet present" in r.stdout

    (d / "pool_status.json").write_text("{not valid json")
    r = _run_pool_status(d)
    assert r.returncode == 0
    assert "could not read" in r.stdout

    (d / "pool_status.json").write_text(json.dumps({"updated_at": "x"}))
    r = _run_pool_status(d)
    assert r.returncode == 0
    assert "no `run` block yet" in r.stdout

    (d / "pool_status.json").write_text(json.dumps({
        "updated_at": "2026-07-01T20:12:00+00:00",
        "run": {
            "status": "running", "n_batches": 3, "n_done": 1, "n_failed": 0,
            "started_at": "2026-07-01T20:00:00+00:00",
            "metrics_totals": {"total_tokens": 42},
        },
        "batches": [
            {"batch_id": "b1", "status": "done", "current_phase": "Phase B complete"},
            {"batch_id": "b2", "status": "running", "current_phase": "Phase A"},
            {"batch_id": "b3", "status": "queued", "current_phase": None},
        ],
    }))
    r = _run_pool_status(d)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("[pool] running 12m: 1/3 done, 0 failed, 1 running, 1 queued")
    assert "DONE     b1: Phase B complete" in r.stdout
    assert "RUNNING  b2: Phase A" in r.stdout
    assert "QUEUED   b3: —" in r.stdout


def test_run_writes_pool_status_with_metrics(tmp_path, monkeypatch):
    # Two worktrees each with the full artifact set run_batch verifies.
    worktree1 = tmp_path / "wt1"
    worktree2 = tmp_path / "wt2"
    for wt in (worktree1, worktree2):
        _write_valid_batch_artifacts(wt)
        (wt / "Validation" / "state.json").write_text(
            json.dumps({"milestones": {"patches_authored": True}}),
            encoding="utf-8",
        )

    prepared = {
        "base_sha": "abc123",
        "batches": [
            {
                "batch_id": "batch-1",
                "error": None,
                "ep_ids": ["ep1"],
                "n_eps": 1,
                "total_weight": 1.0,
                "worktree": str(worktree1),
                "run_id": "run1",
                "validation_branch": "val/branch",
            },
            {
                "batch_id": "batch-2",
                "error": None,
                "ep_ids": ["ep2"],
                "n_eps": 1,
                "total_weight": 1.0,
                "worktree": str(worktree2),
                "run_id": "run1",
                "validation_branch": "val/branch",
            },
        ],
    }
    prepared_path = tmp_path / "batches_prepared.json"
    prepared_path.write_text(json.dumps(prepared))

    usage_dict = {
        "input_tokens": 1000,
        "output_tokens": 50,
        "cache_creation_input_tokens": 200,
        "cache_read_input_tokens": 700,
    }

    # Each call to fake_query yields a fresh generator
    async def fake_query(*, prompt, options):
        yield _system_init("real-sess")
        yield _result_msg(
            is_error=False,
            session_id="real-sess",
            usage=usage_dict,
            duration_ms=1234,
            num_turns=5,
        )

    monkeypatch.setattr(batch, "query", fake_query)
    monkeypatch.setattr(batch, "_run_merge_reports", lambda args, out_dir: "/fake/REPORT.md")

    args = _make_args(tmp_path, prepared_path=prepared_path)
    ret = asyncio.run(batch._run(args))

    assert ret == 0

    out_dir = tmp_path / "Validation"
    result_file = out_dir / "pool_status.json"
    assert result_file.exists()

    result = json.loads(result_file.read_text())
    assert result["run"]["n_batches"] == 2
    assert result["run"]["n_done"] == 2

    # Per-batch metrics
    for b in result["batches"]:
        assert b["status"] == "done"
        assert b["session_id"] is not None
        m = b["metrics"]
        assert m["input_tokens"] == 1000
        assert m["output_tokens"] == 50
        assert m["cache_creation_input_tokens"] == 200
        assert m["cache_read_input_tokens"] == 700
        assert m["duration_ms"] == 1234
        assert m["num_turns"] == 5

    # Aggregate metrics_totals (2 batches × each usage_dict)
    mt = result["run"]["metrics_totals"]
    assert mt["input_tokens"] == 2000
    assert mt["output_tokens"] == 100
    assert mt["cache_creation_input_tokens"] == 400
    assert mt["cache_read_input_tokens"] == 1400
    assert mt["total_tokens"] == 3900  # 2000 + 100 + 400 + 1400
    assert mt["duration_ms"] == 2468   # 2 × 1234
    assert mt["num_turns"] == 10       # 2 × 5

    # No streamlit_cmd anywhere in the result
    assert "streamlit_cmd" not in json.dumps(result)
    assert result["merge_report_path"] == "/fake/REPORT.md"

    assert (out_dir / "pool_status.json").exists()


# ---------------------------------------------------------------------------
# seed-venv shared-path tests
# ---------------------------------------------------------------------------


def test_cmd_seed_venv_uses_shared_path(tmp_path):
    """cmd_seed_venv resolves venv_dir to Validation/shared/.venv-{source,scos}."""
    import argparse
    import json as _json
    from unittest.mock import patch, MagicMock
    import validate

    # Minimal state.json so _load_state succeeds.
    val_dir = tmp_path / "Validation"
    val_dir.mkdir()
    state = {"schema_version": validate.SCHEMA_VERSION, "milestones": {}, "phase": "init",
             "paths": {}, "trials": {}}
    (val_dir / "state.json").write_text(_json.dumps(state))

    captured_venv_dirs: list[str] = []

    def fake_run(cmd, *a, **kw):
        if isinstance(cmd, list) and "venv" in cmd:
            # Last positional arg is the venv path
            captured_venv_dirs.append(cmd[-1])
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        return m

    # Also mock venv_python.is_file() to False so uv venv is always invoked.
    with patch("validate.subprocess.run", side_effect=fake_run):
        for phase, expected_suffix in (("a", "shared/.venv-source"), ("b", "shared/.venv-scos")):
            captured_venv_dirs.clear()
            args = argparse.Namespace(conv_root=str(tmp_path), phase=phase, requirements=None)
            try:
                validate.cmd_seed_venv(args)
            except SystemExit:
                pass
            # At least one subprocess.run call should have targeted the shared path.
            assert any(expected_suffix in p for p in captured_venv_dirs), (
                f"phase={phase}: expected '{expected_suffix}' in one of {captured_venv_dirs}"
            )


def test_derive_phase_excludes_phase_a_skipped_from_terminal():
    # phase_a_skipped is NOT terminal (validate.py::_TERMINAL_TRIAL_STATUSES and
    # batch-runner.md): Phase B still runs and resolves it to passed_no_baseline
    # or hard_stuck. It must NOT count toward the terminal total, else the pool
    # status would hide trials that still have Phase B work pending.
    state = {
        "phase": "running",
        "milestones": {"phase_a_complete": True},
        "trials": {
            "t1": {"status": "passed"},
            "t2": {"status": "phase_a_skipped"},  # still needs Phase B
            "t3": {"status": "hard_stuck"},
        },
    }
    assert batch._derive_phase(state) == "Phase B (2/3 terminal)"
