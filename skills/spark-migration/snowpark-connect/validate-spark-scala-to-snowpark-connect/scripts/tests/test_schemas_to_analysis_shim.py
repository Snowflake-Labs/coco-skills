"""Tests for schemas → analysis.json shim and weight/manifest projection."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import schema_mine as sm  # noqa: E402


def _write_minimal_schemas(shared: Path) -> None:
    schemas = shared / "schemas"
    ep_dir = schemas / "entrypoints" / "jobs"
    (ep_dir / "tables").mkdir(parents=True)
    manifest = {
        "root": str(shared.parent / "source"),
        "complete": False,
        "summary": {"n_entrypoints": 1, "build_tool": "sbt", "source_roots": ["src/main/scala"]},
        "expected_divergences": {},
        "entrypoints": [{
            "id": "jobs",
            "path": "Jobs.scala",
            "dir": "entrypoints/jobs",
            "source_runtime": "spark",
            "weight": 5,
        }],
    }
    (schemas / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (schemas / "scala_meta.json").write_text(json.dumps({
        "build_tool": "sbt", "source_roots": ["src/main/scala"],
    }) + "\n")
    (ep_dir / "_meta.json").write_text(json.dumps({
        "id": "jobs",
        "path": "Jobs.scala",
        "run_mode": "script",
        "import_roots": ["src/main/scala"],
        "entrypoint_kwargs": {},
        "entrypoint_class": "com.example.Jobs",
        "entrypoint_method": "main",
        "cli_args": ["--env", "test"],
        "weight": 5,
        "source_runtime": "spark",
    }, indent=2) + "\n")
    (ep_dir / "tables" / "orders.json").write_text(json.dumps({
        "_table_key": "orders",
        "access": "read",
        "category": "table",
        "columns": [{"name": "order_id", "type": "string"}],
        "mock_file": "orders.parquet",
    }, indent=2) + "\n")
    (ep_dir / "tables" / "out_orders.json").write_text(json.dumps({
        "_table_key": "out_orders",
        "access": "write",
        "category": "table",
        "columns": [{"name": "order_id", "type": "string"}],
    }, indent=2) + "\n")


def test_schemas_to_analysis_shim(tmp_path):
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True)
    _write_minimal_schemas(shared)
    analysis = sm.schemas_to_analysis_shim(tmp_path)
    assert analysis["generated_from"] == "schemas/"
    assert analysis["build_tool"] == "sbt"
    assert len(analysis["entrypoints"]) == 1
    ep = analysis["entrypoints"][0]
    assert ep["entrypoint_class"] == "com.example.Jobs"
    assert ep["cli_args"] == ["--env", "test"]
    assert len(ep["external_sources"]) == 1
    assert ep["external_sources"][0]["id"] == "orders"
    assert len(ep["sinks"]) == 1
    assert ep["sinks"][0]["id"] == "out_orders"
    assert ep["external_sinks"] == ep["sinks"]
    on_disk = json.loads((shared / "analysis.json").read_text())
    assert on_disk["entrypoints"][0]["entrypoint_class"] == "com.example.Jobs"


def test_analysis_to_schemas_numeric_weight(tmp_path):
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True)
    (tmp_path / "Validation" / "source").mkdir(parents=True)
    analysis = {
        "build_tool": "sbt",
        "source_roots": ["src/main/scala"],
        "complete": False,
        "entrypoints": [{
            "id": "jobs",
            "path": "Jobs.scala",
            "run_mode": "script",
            "entrypoint_class": "com.example.Jobs",
            "entrypoint_method": "main",
            "weight": "high",
            "external_sources": [{
                "id": "orders",
                "name": "orders",
                "category": "table",
                "schema": [{"name": "order_id", "type": "string"}],
            }],
            "external_sinks": [{
                "id": "out",
                "name": "out",
                "kind": "table",
                "schema": [{"name": "order_id", "type": "string"}],
            }],
        }],
        "external_sources": [],
        "sinks": [],
    }
    (shared / "analysis.json").write_text(json.dumps(analysis, indent=2) + "\n")
    res = sm.analysis_to_schemas(tmp_path)
    assert res["entrypoints"] == 1
    man = json.loads((shared / "schemas" / "manifest.json").read_text())
    assert isinstance(man["entrypoints"][0]["weight"], int)
    assert man["entrypoints"][0]["weight"] >= 20  # "high" or recomputed
    meta = json.loads((shared / "schemas" / "entrypoints" / "jobs" / "_meta.json").read_text())
    assert meta["entrypoint_class"] == "com.example.Jobs"
