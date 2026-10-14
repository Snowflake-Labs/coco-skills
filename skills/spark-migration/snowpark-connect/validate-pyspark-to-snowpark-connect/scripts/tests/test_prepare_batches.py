"""Tests for the `prepare-batches` subcommand of validate.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

SCRIPTS_DIR = Path(__file__).parent.parent
VALIDATE_PY = SCRIPTS_DIR / "validate.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True
    )


def _setup_repo(tmp_path: Path) -> Path:
    """Init a migration repo with Output/ on main, configured for git commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "branch", "-M", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    # Create Output/ with a placeholder file and commit
    out = repo / "Output"
    out.mkdir()
    (out / "wl_ep1.py").write_text("# placeholder", encoding="utf-8")
    (out / "wl_ep2.py").write_text("# placeholder", encoding="utf-8")
    (out / "wl_ep3.py").write_text("# placeholder", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial commit")
    return repo


def _base_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _make_original_source(tmp_path: Path) -> Path:
    """Create a minimal PySpark source directory mirroring Output/."""
    src = tmp_path / "source"
    src.mkdir()
    (src / "wl_ep1.py").write_text("# ep1 source", encoding="utf-8")
    (src / "wl_ep2.py").write_text("# ep2 source", encoding="utf-8")
    (src / "wl_ep3.py").write_text("# ep3 source", encoding="utf-8")
    return src


def _make_schemas(tmp_path: Path, ep_ids: list[str]) -> Path:
    """Build a minimal mined schemas dir directly (no schema_mine dependency)."""
    schemas = tmp_path / "schemas"
    schemas.mkdir()

    manifest = {
        "root": str(tmp_path),
        "complete": True,
        "summary": {"n_entrypoints": len(ep_ids), "n_tables": 0,
                    "n_non_relational": 0, "open_todos": 0},
        "expected_divergences": {},
        "entrypoints": [
            {
                "id": ep_id,
                "path": f"wl_{ep_id}.py",
                "dir": f"entrypoints/{ep_id}",
                "weight": 1,
                "weight_breakdown": None,
            }
            for ep_id in ep_ids
        ],
    }
    (schemas / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    for ep_id in ep_ids:
        ep_dir = schemas / "entrypoints" / ep_id
        ep_dir.mkdir(parents=True)
        meta = {
            "id": ep_id,
            "path": f"wl_{ep_id}.py",
            "run_mode": "batch",
            "tables": {},
        }
        (ep_dir / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return schemas


def _make_sections(tmp_path: Path, sections: list[dict]) -> Path:
    """Write a minimal sections.json (semantic groups of entrypoint ids)."""
    path = tmp_path / "sections.json"
    path.write_text(json.dumps(sections), encoding="utf-8")
    return path


def _run_prepare_batches(
    repo: Path,
    base_sha: str,
    worktrees_dir: Path,
    sections_json: Path,
    schemas_dir: Path,
    original_source: Path,
) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable, str(VALIDATE_PY), "prepare-batches",
        "--conv-root", str(repo),
        "--sections", str(sections_json),
        "--base-sha", base_sha,
        "--worktrees-dir", str(worktrees_dir),
        "--schemas", str(schemas_dir),
        "--connection", "test_conn",
        "--original-source", str(original_source),
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def _cleanup_worktrees(repo: Path, worktrees_dir: Path) -> None:
    """Remove all worktrees created under worktrees_dir."""
    for entry in sorted(worktrees_dir.iterdir()) if worktrees_dir.is_dir() else []:
        _git(repo, "worktree", "remove", "--force", str(entry))


# ---------------------------------------------------------------------------
# Happy path: two batches, both prepared successfully
# ---------------------------------------------------------------------------

def test_prepare_batches_primary_skeleton_is_minimal(tmp_path):
    """The PRIMARY conv-root's Validation/ must not accumulate empty
    scaffolding dirs (tests/, results/phase_a/, results/phase_b/,
    shared/mock_data/) — those are only meaningful inside each worktree."""
    repo = _setup_repo(tmp_path)
    sha = _base_sha(repo)
    src = _make_original_source(tmp_path)
    schemas = _make_schemas(tmp_path, ["ep1"])
    sections_json = _make_sections(tmp_path, [
        {"section_id": "s", "name": "S", "ep_ids": ["ep1"]},
    ])
    worktrees_dir = tmp_path / "worktrees"

    result = _run_prepare_batches(repo, sha, worktrees_dir, sections_json, schemas, src)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    primary_val = repo / "Validation"
    assert (primary_val / "source").is_dir(), "primary should still have source/"
    assert (primary_val / "shared").is_dir(), "primary should still have shared/"
    # These worktree-only dirs must NOT be created on the primary.
    for stale in ("tests", "results", "results/phase_a", "results/phase_b",
                  "shared/mock_data"):
        assert not (primary_val / stale).exists(), (
            f"primary should not have {stale}/; it's a per-worktree dir"
        )
    # And each worktree still gets the full skeleton.
    bp = json.loads((primary_val / "shared" / "batches_prepared.json").read_text())
    for rec in bp["batches"]:
        wt = Path(rec["worktree"]) / "Validation"
        for d in ("tests", "results/phase_a", "results/phase_b", "shared/mock_data"):
            assert (wt / d).is_dir(), f"worktree missing {d}/ at {wt}"

    _cleanup_worktrees(repo, worktrees_dir)


def test_prepare_batches_mixes_small_sections(tmp_path):
    """Two small sections that both fit under the caps are packed into ONE mixed
    batch (whole-section mixing), so prepare-batches creates a single worktree
    covering all their entrypoints."""
    repo = _setup_repo(tmp_path)
    sha = _base_sha(repo)
    src = _make_original_source(tmp_path)
    schemas = _make_schemas(tmp_path, ["ep1", "ep2", "ep3"])
    sections_json = _make_sections(tmp_path, [
        {"section_id": "sales", "name": "Sales", "ep_ids": ["ep1", "ep2"]},
        {"section_id": "events", "name": "Events", "ep_ids": ["ep3"]},
    ])
    worktrees_dir = tmp_path / "worktrees"

    result = _run_prepare_batches(repo, sha, worktrees_dir, sections_json, schemas, src)

    assert result.returncode == 0, \
        f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "prepared 1/1" in result.stdout

    bp_path = repo / "Validation" / "shared" / "batches_prepared.json"
    assert bp_path.is_file(), f"batches_prepared.json not found: {bp_path}"
    bp = json.loads(bp_path.read_text(encoding="utf-8"))
    assert bp["base_sha"] == sha
    assert len(bp["batches"]) == 1
    assert "max_entrypoints" in bp and "max_weight" in bp
    assert bp["summary"]["n_batches"] == 1
    assert not (repo / "Validation" / "shared" / "batches.json").exists(), \
        "prepare-batches should not write a separate batches.json"

    rec = bp["batches"][0]
    assert rec["error"] is None, f"batch {rec['batch_id']} error: {rec['error']}"
    assert rec["batch_id"].startswith("mixed__"), rec["batch_id"]
    assert set(rec.get("section_ids") or []) == {"sales", "events"}
    assert set(rec["ep_ids"]) == {"ep1", "ep2", "ep3"}
    assert rec["n_eps"] == len(rec["ep_ids"])
    assert rec["run_id"] is not None and rec["validation_branch"] is not None

    wt = Path(rec["worktree"])
    assert wt.is_dir(), f"worktree missing: {wt}"
    branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert branch == rec["validation_branch"] and branch.startswith("validation/")
    manifest_path = wt / "Validation" / "shared" / "schemas" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    kept_ids = {e["id"] for e in manifest.get("entrypoints", [])}
    assert kept_ids == {"ep1", "ep2", "ep3"}, kept_ids

    _cleanup_worktrees(repo, worktrees_dir)


def test_prepare_batches_oversized_section_splits_to_worktrees(tmp_path):
    """A section larger than the entrypoint cap splits into multiple standalone
    batches -> multiple worktrees, each on its own validation branch with schemas
    scoped to its subset and a unique run_id. Also guards init's orphan-branch
    cleanup against mis-parsing a sibling's live checked-out branch."""
    repo = _setup_repo(tmp_path)
    sha = _base_sha(repo)
    src = _make_original_source(tmp_path)
    ep_ids = [f"ep{i}" for i in range(1, 13)]  # 12 EPs > default max-entrypoints (10)
    schemas = _make_schemas(tmp_path, ep_ids)
    sections_json = _make_sections(tmp_path, [
        {"section_id": "big", "name": "Big", "ep_ids": ep_ids},
    ])
    worktrees_dir = tmp_path / "worktrees"

    result = _run_prepare_batches(repo, sha, worktrees_dir, sections_json, schemas, src)

    assert result.returncode == 0, \
        f"stdout={result.stdout}\nstderr={result.stderr}"
    # Regression guard: orphan-cleanup must not mis-parse git's `+ ` worktree
    # marker nor delete a sibling batch's live, checked-out validation branch.
    assert "+ validation/" not in result.stdout, result.stdout
    assert "removing orphaned validation branch" not in result.stdout, result.stdout

    bp = json.loads((repo / "Validation" / "shared" / "batches_prepared.json").read_text())
    assert len(bp["batches"]) >= 2
    assert all(r["batch_id"].startswith("big__") for r in bp["batches"]), \
        [r["batch_id"] for r in bp["batches"]]

    # Coverage: union of ep_ids across batches == all EPs, no duplicates.
    seen = [e for r in bp["batches"] for e in r["ep_ids"]]
    assert sorted(seen) == sorted(ep_ids)
    assert len(seen) == len(set(seen)), "an EP appears in more than one batch"
    # Unique run_id per worktree (golden-schema isolation).
    run_ids = [r["run_id"] for r in bp["batches"]]
    assert len(set(run_ids)) == len(run_ids)

    for rec in bp["batches"]:
        wt = Path(rec["worktree"])
        assert wt.is_dir(), f"worktree missing: {wt}"
        branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        assert branch == rec["validation_branch"], \
            f"expected {rec['validation_branch']}, got {branch}"
        manifest = json.loads(
            (wt / "Validation" / "shared" / "schemas" / "manifest.json").read_text())
        kept = {e["id"] for e in manifest.get("entrypoints", [])}
        assert kept == set(rec["ep_ids"]), f"{rec['batch_id']}: {kept} != {set(rec['ep_ids'])}"

    _cleanup_worktrees(repo, worktrees_dir)


# ---------------------------------------------------------------------------
# Idempotency: re-running prepare-batches on existing worktrees exits 0
# ---------------------------------------------------------------------------

def test_prepare_batches_idempotent(tmp_path):
    repo = _setup_repo(tmp_path)
    sha = _base_sha(repo)
    src = _make_original_source(tmp_path)
    schemas = _make_schemas(tmp_path, ["ep1"])
    sections_json = _make_sections(tmp_path, [
        {"section_id": "s", "name": "S", "ep_ids": ["ep1"]},
    ])
    worktrees_dir = tmp_path / "worktrees"

    # First run
    r1 = _run_prepare_batches(repo, sha, worktrees_dir, sections_json, schemas, src)
    assert r1.returncode == 0, f"first run failed: {r1.stdout}\n{r1.stderr}"

    # Second run — must succeed without error
    r2 = _run_prepare_batches(repo, sha, worktrees_dir, sections_json, schemas, src)
    assert r2.returncode == 0, f"second run failed: {r2.stdout}\n{r2.stderr}"
    assert "prepared 1/1" in r2.stdout

    _cleanup_worktrees(repo, worktrees_dir)


# ---------------------------------------------------------------------------
# --databricks-env-file: path is persisted into each worktree's state.json
# ---------------------------------------------------------------------------

def test_prepare_batches_persists_databricks_env_file(tmp_path):
    repo = _setup_repo(tmp_path)
    sha = _base_sha(repo)
    src = _make_original_source(tmp_path)
    schemas = _make_schemas(tmp_path, ["ep1", "ep2"])
    sections_json = _make_sections(tmp_path, [
        {"section_id": "a", "name": "A", "ep_ids": ["ep1"]},
        {"section_id": "b", "name": "B", "ep_ids": ["ep2"]},
    ])
    worktrees_dir = tmp_path / "worktrees"
    env_file = tmp_path / "databricks.env"
    env_file.write_text("DATABRICKS_HOST=https://x\n", encoding="utf-8")

    cmd = [
        sys.executable, str(VALIDATE_PY), "prepare-batches",
        "--conv-root", str(repo),
        "--sections", str(sections_json),
        "--base-sha", sha,
        "--worktrees-dir", str(worktrees_dir),
        "--schemas", str(schemas),
        "--connection", "test_conn",
        "--original-source", str(src),
        "--databricks-env-file", str(env_file),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    bp = json.loads(
        (repo / "Validation" / "shared" / "batches_prepared.json").read_text()
    )
    for rec in bp["batches"]:
        state = json.loads(
            (Path(rec["worktree"]) / "Validation" / "state.json").read_text()
        )
        assert state.get("databricks", {}).get("env_file") == str(env_file), \
            f"batch {rec['batch_id']} did not persist the databricks env_file"

    _cleanup_worktrees(repo, worktrees_dir)


# ---------------------------------------------------------------------------
# Coverage gate: exit 3, no worktrees created
# ---------------------------------------------------------------------------


def test_prepare_batches_coverage_error_exits_3_no_worktrees(tmp_path):
    """sections.json with a missing EP causes exit 3 and no worktree dirs."""
    repo = _setup_repo(tmp_path)
    sha = _base_sha(repo)
    src = _make_original_source(tmp_path)
    # manifest has ep1, ep2, ep3 but sections only covers ep1 and ep2
    schemas = _make_schemas(tmp_path, ["ep1", "ep2", "ep3"])
    sections_json = _make_sections(tmp_path, [
        {"section_id": "sales", "name": "Sales", "ep_ids": ["ep1", "ep2"]},
        # ep3 intentionally omitted → coverage error
    ])
    worktrees_dir = tmp_path / "worktrees"

    result = _run_prepare_batches(repo, sha, worktrees_dir, sections_json, schemas, src)

    assert result.returncode == 3, (
        f"expected exit 3 for coverage error; got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "coverage check failed" in result.stderr or "coverage check failed" in result.stdout, (
        f"expected coverage error message; stderr={result.stderr}"
    )
    assert "ep3" in result.stderr or "ep3" in result.stdout, (
        f"expected ep3 named in error; stderr={result.stderr}"
    )
    # No worktree directories should have been created
    assert not worktrees_dir.exists() or not any(worktrees_dir.iterdir()), (
        f"worktree directories were created despite coverage error: "
        f"{list(worktrees_dir.iterdir()) if worktrees_dir.exists() else []}"
    )
