"""Tests for remaining PySpark-parity control-plane helpers in scos_state.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location("scos_state", _SCRIPTS / "scos_state.py")
_scos_state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scos_state)  # type: ignore[union-attr]


def test_log_looks_transient():
    p = Path("/tmp")  # placeholder — use tmp_path via pytest
    # exercised below with tmp_path


def test_transient_retry_helper(tmp_path):
    log = tmp_path / "sbt.log"
    log.write_text("gRPC UNAVAILABLE: failed to connect to warehouse\n", encoding="utf-8")
    assert _scos_state._log_looks_transient(log)

    clean = tmp_path / "ok.log"
    clean.write_text("[error] Compilation failed\n", encoding="utf-8")
    assert not _scos_state._log_looks_transient(clean)

    calls = []

    def fake_run(cmd, cwd, env, log_path):
        calls.append(dict(env))
        log_path.write_text("still UNAVAILABLE\n", encoding="utf-8")
        return 1 if len(calls) == 1 else 0

    with patch.object(_scos_state, "_run_sbt_streaming", side_effect=fake_run):
        with patch.object(_scos_state, "_kill_stale_scos_servers", return_value=["snowpark_connect"]) as kill:
            rc = _scos_state._run_sbt_with_transient_retry(
                cmd=["sbt", "test"],
                cwd=str(tmp_path),
                env={"SCOS_TRIAL_TIMEOUT_SECS": "300"},
                log_path=log,
                label="Phase B",
            )
    assert rc == 0
    assert len(calls) == 2
    assert calls[1]["SCOS_TRIAL_TIMEOUT_SECS"] == "900"
    kill.assert_called_once()


def test_known_patches_suggest_writes_artifacts(tmp_path):
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True)
    schemas = shared / "schemas"
    ep_dir = schemas / "entrypoints" / "job1"
    (ep_dir / "tables").mkdir(parents=True)
    (schemas / "manifest.json").write_text(json.dumps({
        "entrypoints": [{"id": "job1", "path": "Job.scala", "dir": "entrypoints/job1"}],
        "expected_divergences": {},
    }), encoding="utf-8")
    (ep_dir / "_meta.json").write_text(json.dumps({
        "id": "job1",
        "entrypoint_class": "com.Example$",
        "entrypoint_method": "main",
        "udfs": [{"name": "Image$"}],
    }), encoding="utf-8")
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{
            "id": "job1",
            "entrypoint_class": "com.Example$",
            "entrypoint_method": "main",
            "external_sources": [],
            "sinks": [],
            "udfs": [{"name": "Image$"}],
        }],
    }), encoding="utf-8")
    out = tmp_path / "Output" / "src" / "main" / "scala"
    out.mkdir(parents=True)
    (out / "Job.scala").write_text(
        'val x = dbutils.widgets.get("run_id")\nspark.read.parquet("s3://b/x")\n',
        encoding="utf-8",
    )

    rc = _scos_state._cmd_known_patches_suggest(
        SimpleNamespace(conv_root=str(tmp_path))
    )
    assert rc == 0
    sug = json.loads((shared / "known_patch_suggestions.json").read_text())
    assert "patches" in sug
    assert any("widget_get" in (p.get("id") or "") for p in sug["patches"])
    invest = json.loads((shared / "patch_investigation.json").read_text())
    assert invest["sites"]
    manifest = json.loads((schemas / "manifest.json").read_text())
    assert "job1.__udf__" in (manifest.get("expected_divergences") or {})
    analysis = json.loads((shared / "analysis.json").read_text())
    assert "job1.__udf__" in (analysis.get("expected_divergences") or {})


def test_provision_force_reseed_clears_hashes(tmp_path):
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True)
    hashes = shared / "provision_hashes.json"
    hashes.write_text('{"scos": {"ep": {"T": "abc"}}}', encoding="utf-8")
    (shared / "schemas").mkdir()
    (tmp_path / "Validation" / "state.json").parent.mkdir(parents=True, exist_ok=True)

    # Minimal state so _provision_golden_schemas gets past force-reseed into die on missing config
    state = {
        "config": {},
        "run_id": "r1",
        "trials": {"ep1": {"status": "pending"}},
        "snowflake": {"provisioned": True, "golden_schemas": {"ep1": {"schema": "G"}}},
    }
    (tmp_path / "Validation" / "state.json").write_text(json.dumps(state), encoding="utf-8")

    with patch.object(_scos_state, "_die", side_effect=lambda msg, code=1: code):
        # Will fail later on missing connection_name — that's fine; we only assert hash clear.
        _scos_state._provision_golden_schemas(tmp_path, state, force_reseed=True)
    assert not hashes.exists()
