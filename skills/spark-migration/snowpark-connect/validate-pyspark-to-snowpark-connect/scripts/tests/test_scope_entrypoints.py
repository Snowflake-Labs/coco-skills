"""Tests for the `scope-entrypoints` subcommand of validate.py.

`scope-entrypoints` prunes the mined schemas/ to a subset *before* sectioning —
it operates on `<conv-root>/Validation/shared/schemas` with no state.json and no
cap, deleting unselected entrypoints in place.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent
VALIDATE_PY = SCRIPTS_DIR / "validate.py"


def _make_schemas(conv_root: Path, ep_ids: list[str]) -> Path:
    """Build a minimal mined schemas dir at <conv-root>/Validation/shared/schemas."""
    schemas = conv_root / "Validation" / "shared" / "schemas"
    schemas.mkdir(parents=True)

    manifest = {
        "root": str(conv_root),
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


def _run_scope(conv_root: Path, ids: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATE_PY), "scope-entrypoints",
         "--conv-root", str(conv_root), "--ids", ids],
        capture_output=True, text=True,
    )


def _manifest_ids(schemas: Path) -> set[str]:
    manifest = json.loads((schemas / "manifest.json").read_text(encoding="utf-8"))
    return {e["id"] for e in manifest.get("entrypoints", [])}


def test_scope_keeps_subset_and_deletes_rest(tmp_path):
    conv_root = tmp_path / "conv"
    conv_root.mkdir()
    schemas = _make_schemas(conv_root, ["ep1", "ep2", "ep3", "ep4"])

    result = _run_scope(conv_root, "ep1,ep3")

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "kept ['ep1', 'ep3']" in result.stdout
    assert "removed 2 unselected entrypoint(s)" in result.stdout
    # manifest trimmed to the kept set
    assert _manifest_ids(schemas) == {"ep1", "ep3"}
    # unselected entrypoint dirs removed from disk
    assert (schemas / "entrypoints" / "ep1").is_dir()
    assert (schemas / "entrypoints" / "ep3").is_dir()
    assert not (schemas / "entrypoints" / "ep2").exists()
    assert not (schemas / "entrypoints" / "ep4").exists()


def test_scope_no_cap_keeps_large_subset(tmp_path):
    """scope-entrypoints has no cap (unlike the internal _select_entrypoints_for_worktree helper)."""
    conv_root = tmp_path / "conv"
    conv_root.mkdir()
    ep_ids = [f"ep{i}" for i in range(1, 21)]  # 20 EPs
    schemas = _make_schemas(conv_root, ep_ids)
    keep = ep_ids[:15]  # 15 > the old default cap of 10

    result = _run_scope(conv_root, ",".join(keep))

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _manifest_ids(schemas) == set(keep)


def test_scope_unknown_id_errors_no_mutation(tmp_path):
    conv_root = tmp_path / "conv"
    conv_root.mkdir()
    schemas = _make_schemas(conv_root, ["ep1", "ep2"])

    result = _run_scope(conv_root, "ep1,nope")

    assert result.returncode == 2, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "nope" in (result.stderr + result.stdout)
    # nothing pruned on error
    assert _manifest_ids(schemas) == {"ep1", "ep2"}
    assert (schemas / "entrypoints" / "ep2").is_dir()


def test_scope_whitespace_and_blank_ids_tolerated(tmp_path):
    conv_root = tmp_path / "conv"
    conv_root.mkdir()
    schemas = _make_schemas(conv_root, ["ep1", "ep2", "ep3"])

    result = _run_scope(conv_root, " ep1 , , ep3 ")

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _manifest_ids(schemas) == {"ep1", "ep3"}


def test_scope_empty_ids_errors(tmp_path):
    conv_root = tmp_path / "conv"
    conv_root.mkdir()
    _make_schemas(conv_root, ["ep1"])

    result = _run_scope(conv_root, "  ,  ")

    assert result.returncode == 2, f"stdout={result.stdout}\nstderr={result.stderr}"
