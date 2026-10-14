"""Unit tests for schemas prune helpers in scos_state (PySpark prepare-batches parity)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import scos_state as s  # noqa: E402


def _seed_schemas(conv: Path, ids: list[str]) -> None:
    sd = conv / "Validation" / "shared" / "schemas"
    (sd / "entrypoints").mkdir(parents=True)
    refs = []
    for eid in ids:
        d = sd / "entrypoints" / eid
        (d / "tables").mkdir(parents=True)
        (d / "_meta.json").write_text(json.dumps({"id": eid, "path": f"{eid}.scala"}) + "\n")
        refs.append({"id": eid, "path": f"{eid}.scala", "dir": f"entrypoints/{eid}", "weight": 5})
    (sd / "manifest.json").write_text(json.dumps({
        "complete": False,
        "summary": {"n_entrypoints": len(ids)},
        "entrypoints": refs,
        "expected_divergences": {f"{ids[0]}.sink": [{"reason": "x"}]},
    }, indent=2) + "\n")


def test_prune_schemas_to_selected(tmp_path):
    _seed_schemas(tmp_path, ["ep1", "ep2", "ep3"])
    man = s.load_schemas_manifest(tmp_path)
    removed = s._prune_schemas_to_selected(tmp_path, man, {"ep1", "ep3"})
    assert removed == 1
    man2 = s.load_schemas_manifest(tmp_path)
    assert {e["id"] for e in man2["entrypoints"]} == {"ep1", "ep3"}
    assert not (tmp_path / "Validation/shared/schemas/entrypoints/ep2").is_dir()
    assert (tmp_path / "Validation/shared/schemas/entrypoints/ep1").is_dir()
    assert "ep2.sink" not in (man2.get("expected_divergences") or {})
