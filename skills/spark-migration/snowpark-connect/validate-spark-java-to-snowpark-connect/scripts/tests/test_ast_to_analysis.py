"""Tests for ast_to_analysis.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import ast_to_analysis as ata  # noqa: E402


def _write_ast(tmp_path: Path, files: list[dict]) -> None:
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "ast_facts.json").write_text(json.dumps({
        "source": str(tmp_path / "Validation" / "source"),
        "file_count": len(files),
        "parse_errors": 0,
        "files": files,
    }), encoding="utf-8")


def _write_source(tmp_path: Path, rel: str, body: str = "") -> Path:
    src = tmp_path / "Validation" / "source" / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(
        body or "public class Main { public static void main(String[] args) {} }",
        encoding="utf-8",
    )
    return src


def test_survey_builds_candidates(tmp_path):
    rel = "src/main/java/Jobs.java"
    src = _write_source(tmp_path, rel)
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "objects": ["Main"],
        "entrypoints": [{"owner": "Main", "method": "main"}],
        "reads": [{"call": "table", "args": ["ORDERS"]}],
        "writes": [],
        "table_refs": ["ORDERS"],
        "column_refs": ["order_id"],
        "write_helpers": [],
        "spark_session_created": True,
    }])
    result = ata.run(tmp_path, mode="survey")
    assert len(result["entrypoint_candidates"]) == 1
    assert result["entrypoint_candidates"][0]["id"] == "jobs"
    assert result["build_tool"] in {"maven", "gradle", "unknown"}


def test_deep_builds_sources_sinks_with_llm_todo(tmp_path):
    rel = "src/main/java/Jobs.java"
    src = _write_source(tmp_path, rel)
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{"id": "jobs", "path": rel}],
        "source_roots": ["src/main/java"],
        "build_tool": "maven",
    }), encoding="utf-8")
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "objects": ["Main"],
        "entrypoints": [{"owner": "Main", "method": "main"}],
        "reads": [{"call": "table", "args": ["DB.SCH.ORDERS"]}],
        "writes": [{"call": "saveAsTable", "args": ["DB.SCH.OUT"]}],
        "table_refs": ["DB.SCH.ORDERS", "DB.SCH.OUT"],
        "column_refs": ["order_id", "amount"],
        "write_helpers": ["writeOut"],
        "spark_session_created": True,
    }])
    result = ata.run(tmp_path, mode="deep")
    ep = result["entrypoints"][0]
    assert ep["external_sources"]
    assert ep["sinks"]
    assert result["external_sources"][0]["schema"]
    assert result["external_sources"][0].get("llm_todo")
    assert result["sinks"][0].get("natural_keys") == []
    assert result["complete"] is False
    assert result["llm_todos"]


# ── unresolved edge consumption (ScosAnalyzeJava data-edge parity) ─────────────


def _write_ast_unresolved(tmp_path: Path, unresolved_reads, unresolved_writes) -> None:
    rel = "src/main/java/Job.java"
    src = _write_source(tmp_path, rel)
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{"id": "job", "path": rel}],
        "source_roots": ["src/main/java"],
        "build_tool": "maven",
    }), encoding="utf-8")
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "objects": ["Job"],
        "entrypoints": [{"owner": "Job", "method": "main"}],
        "reads": [],
        "writes": [],
        "unresolved_reads": unresolved_reads,
        "unresolved_writes": unresolved_writes,
        "table_refs": [],
        "column_refs": ["id"],
        "write_helpers": [],
        "spark_session_created": True,
    }])


def test_unresolved_read_creates_source_with_llm_todo(tmp_path):
    """An unresolved read (dynamic path) must create a source with a dynamic-path llm_todo."""
    _write_ast_unresolved(tmp_path, unresolved_reads=[
        {"call": "parquet", "arg_expr": "configPath", "line": 10},
    ], unresolved_writes=[])
    result = ata.run(tmp_path, mode="deep")
    sources = result.get("external_sources") or []
    assert sources, "expected at least one source from unresolved read"
    src = sources[0]
    assert "dynamic" in src["llm_todo"].lower() or "path" in src["llm_todo"].lower()
    assert "configPath" in src["llm_todo"] or "configPath" in src.get("original_path", "")
    assert src.get("reader_method") == "parquet"


def test_unresolved_write_creates_sink_with_llm_todo(tmp_path):
    """An unresolved write (dynamic target) must create a sink with an llm_todo."""
    _write_ast_unresolved(tmp_path, unresolved_reads=[], unresolved_writes=[
        {"call": "saveAsTable", "arg_expr": "outputTable", "line": 15},
    ])
    result = ata.run(tmp_path, mode="deep")
    sinks = result.get("sinks") or []
    assert sinks, "expected at least one sink from unresolved write"
    sink = sinks[0]
    assert sink.get("llm_todo") or sink.get("method") == "saveastable"


def test_resolved_and_unresolved_reads_together(tmp_path):
    """Mix of resolved + unresolved reads: all become sources, unresolved flagged."""
    rel = "src/main/java/MixedJob.java"
    src = _write_source(tmp_path, rel)
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{"id": "mixedjob", "path": rel}],
        "source_roots": ["src/main/java"],
        "build_tool": "maven",
    }), encoding="utf-8")
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "objects": ["MixedJob"],
        "entrypoints": [{"owner": "MixedJob", "method": "main"}],
        "reads": [{"call": "parquet", "args": ["s3://bucket/static.parquet"], "line": 5}],
        "writes": [{"call": "saveAsTable", "args": ["out_table"], "line": 20}],
        "unresolved_reads": [
            {"call": "csv", "arg_expr": "dynamicCsvPath", "line": 8},
        ],
        "unresolved_writes": [],
        "table_refs": [],
        "column_refs": ["id", "value"],
        "write_helpers": [],
        "spark_session_created": True,
    }])
    result = ata.run(tmp_path, mode="deep")
    sources = result.get("external_sources") or []
    # Should have both the static read AND the unresolved one
    assert len(sources) == 2, f"expected 2 sources (static + unresolved), got {len(sources)}"
    methods = {s["reader_method"] for s in sources}
    assert "parquet" in methods
    assert "csv" in methods
    # The unresolved one must have a dynamic-path llm_todo
    unresolved_src = next(s for s in sources if s["reader_method"] == "csv")
    assert "dynamic" in unresolved_src.get("llm_todo", "").lower() or \
           "path" in unresolved_src.get("llm_todo", "").lower()


def test_unresolved_edges_deduplication(tmp_path):
    """Duplicate unresolved reads (same call+arg_expr) must be deduplicated."""
    _write_ast_unresolved(tmp_path, unresolved_reads=[
        {"call": "parquet", "arg_expr": "samePath", "line": 5},
        {"call": "parquet", "arg_expr": "samePath", "line": 5},  # duplicate
    ], unresolved_writes=[])
    result = ata.run(tmp_path, mode="deep")
    sources = result.get("external_sources") or []
    assert len(sources) == 1, "duplicate unresolved reads must be deduplicated"


def test_line_field_on_resolved_read(tmp_path):
    """Reads now carry a line field; ast_to_analysis must not break when it's present."""
    rel = "src/main/java/LineJob.java"
    src = _write_source(tmp_path, rel)
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{"id": "linejob", "path": rel}],
        "source_roots": ["src/main/java"],
        "build_tool": "maven",
    }), encoding="utf-8")
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "objects": ["LineJob"],
        "entrypoints": [{"owner": "LineJob", "method": "main"}],
        "reads": [{"call": "parquet", "args": ["s3://b/f.parquet"], "line": 42}],
        "writes": [],
        "unresolved_reads": [],
        "unresolved_writes": [],
        "table_refs": [],
        "column_refs": ["id"],
        "write_helpers": [],
        "spark_session_created": True,
    }])
    result = ata.run(tmp_path, mode="deep")
    sources = result.get("external_sources") or []
    assert sources, "source should be created for read with line field"
    assert sources[0]["reader_method"] == "parquet"


# ── Java-specific coverage ──────────────────────────────────────────────────

def test_survey_uses_classes_key_not_objects(tmp_path):
    """JavaParser emits 'classes', not 'objects'. ast_to_analysis must read 'classes'."""
    rel = "src/main/java/MyJob.java"
    src = _write_source(tmp_path, rel)
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "classes": ["MyJob"],          # Java: 'classes' key
        "entrypoints": [{"owner": "MyJob", "method": "main"}],
        "reads": [],
        "writes": [],
        "table_refs": [],
        "column_refs": [],
        "write_helpers": [],
        "spark_session_created": False,
    }])
    result = ata.run(tmp_path, mode="survey")
    # Should produce a candidate from 'classes' list, not crash on missing 'objects'
    assert len(result["entrypoint_candidates"]) == 1
    assert result["entrypoint_candidates"][0]["id"] == "myjob"


def test_survey_dot_method_notation_in_call(tmp_path):
    """Java uses owner.method notation (not owner::method) for call references."""
    rel = "src/main/java/Pipe.java"
    src = _write_source(tmp_path, rel)
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "classes": ["Pipe"],
        "entrypoints": [{"owner": "Pipe", "method": "main", "kind": "java_class"}],
        "reads": [{"call": "Pipe.main", "args": ["s3://bucket/data"], "line": 5}],
        "writes": [],
        "table_refs": [],
        "column_refs": [],
        "write_helpers": [],
        "spark_session_created": True,
    }])
    result = ata.run(tmp_path, mode="survey")
    cands = result.get("entrypoint_candidates", [])
    assert len(cands) == 1


def test_survey_java_source_root_default(tmp_path):
    """Default source root for Java should be src/main/java, not src/main/scala."""
    rel = "src/main/java/com/example/Job.java"
    src = _write_source(tmp_path, rel)
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "classes": ["Job"],
        "entrypoints": [{"owner": "Job", "method": "main"}],
        "reads": [], "writes": [], "table_refs": [], "column_refs": [],
        "write_helpers": [], "spark_session_created": False,
    }])
    result = ata.run(tmp_path, mode="survey")
    roots = result.get("source_roots", [])
    assert any("java" in r for r in roots), f"expected java source root, got {roots}"
    assert not any("scala" in r for r in roots), f"scala root leaked into java result: {roots}"


def test_survey_build_tool_maven_detected(tmp_path):
    """When a pom.xml is present, build_tool should be 'maven'."""
    rel = "src/main/java/Job.java"
    src = _write_source(tmp_path, rel)
    # Plant a pom.xml
    (tmp_path / "Validation" / "source").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Validation" / "source" / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion></project>", encoding="utf-8"
    )
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "classes": ["Job"],
        "entrypoints": [{"owner": "Job", "method": "main"}],
        "reads": [], "writes": [], "table_refs": [], "column_refs": [],
        "write_helpers": [], "spark_session_created": False,
    }])
    result = ata.run(tmp_path, mode="survey")
    assert result.get("build_tool") == "maven"
