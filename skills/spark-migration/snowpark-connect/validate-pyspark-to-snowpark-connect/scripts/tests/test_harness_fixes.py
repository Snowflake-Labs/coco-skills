"""Unit tests for harness bug fixes (Commit 1) and seed-venv deps (Commit 2).

Covers:
  1.1 assemble_analysis — auxiliary_files forwarded from manifest
  1.2 SCOS_OUTPUT_SCHEMA env var set to db.clone_schema; cleaned up after run
  1.3 compare_results — a sink present in only one phase is always a failure
  2   seed-venv — pandas + pyarrow installed in both phases

Run: ../.venv/bin/pytest scripts/tests/test_harness_fixes.py -q
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_TESTS_DIR = Path(__file__).parent
_SCRIPTS_DIR = _TESTS_DIR.parent
_HARNESS_DIR = _SCRIPTS_DIR / "harness"

for _p in (str(_HARNESS_DIR), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Stub snowflake / snowpark in sys.modules BEFORE importing scos_runtime
# (mirrors test_scos_provision_fast_path.py)
# ---------------------------------------------------------------------------
_SF_MOD = sys.modules.get("snowflake") or types.ModuleType("snowflake")
_SF_CONN_MOD = sys.modules.get("snowflake.connector") or types.ModuleType("snowflake.connector")
_SF_CONN_MOD.connect = mock.MagicMock(  # type: ignore[attr-defined]
    side_effect=AssertionError("snowflake.connector.connect called unexpectedly")
)
_SF_MOD.connector = _SF_CONN_MOD  # type: ignore[attr-defined]
sys.modules.setdefault("snowflake", _SF_MOD)
sys.modules.setdefault("snowflake.connector", _SF_CONN_MOD)
# Force-own the slot so mock.patch.object(_SPC_MOD, ...) patches the object
# that run_trial's `import snowpark_connect` actually sees, regardless of
# which test file was collected first.
_SPC_MOD = sys.modules.get("snowpark_connect") or types.ModuleType("snowpark_connect")
sys.modules["snowpark_connect"] = _SPC_MOD
sys.modules.setdefault("snowflake.snowpark_connect", types.ModuleType("snowflake.snowpark_connect"))

from helpers import assemble_analysis, compare_results  # noqa: E402
from runtimes.scos_runtime import ScosRuntime  # noqa: E402
from runtimes.local_runtime import LocalDeltaRuntime  # noqa: E402
from runtimes.base import TrialContext, TrialRequest  # noqa: E402
from runtimes._executor import run_and_capture  # noqa: E402
from runtimes import driver  # noqa: E402
import validate  # noqa: E402


# ===========================================================================
# 1.1  assemble_analysis — auxiliary_files
# ===========================================================================

def _write_ep(ep_dir: Path, ep: dict) -> None:
    d = ep_dir / ep["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "_meta.json").write_text(json.dumps({k: v for k, v in ep.items() if k != "tables"}))


def test_assemble_analysis_auxiliary_files_present(tmp_path):
    """auxiliary_files list from manifest is forwarded in the result dict."""
    schemas_dir = tmp_path / "schemas"
    (schemas_dir / "entrypoints").mkdir(parents=True)
    _write_ep(schemas_dir / "entrypoints", {"id": "ep1"})
    aux = [{"id": "aux1", "aux_file": "lib/helper.py"}]
    (schemas_dir / "manifest.json").write_text(json.dumps(
        {"entrypoints": [{"id": "ep1"}], "auxiliary_files": aux}
    ))
    result = assemble_analysis(str(schemas_dir))
    assert "auxiliary_files" in result
    assert result["auxiliary_files"] == aux


def test_assemble_analysis_auxiliary_files_absent(tmp_path):
    """auxiliary_files is an empty list when the manifest key is missing."""
    schemas_dir = tmp_path / "schemas"
    (schemas_dir / "entrypoints").mkdir(parents=True)
    _write_ep(schemas_dir / "entrypoints", {"id": "ep1"})
    (schemas_dir / "manifest.json").write_text(json.dumps(
        {"entrypoints": [{"id": "ep1"}]}  # no auxiliary_files key
    ))
    result = assemble_analysis(str(schemas_dir))
    assert "auxiliary_files" in result
    assert result["auxiliary_files"] == []


def test_assemble_analysis_auxiliary_files_no_manifest(tmp_path):
    """auxiliary_files is an empty list when no manifest exists (glob fallback)."""
    schemas_dir = tmp_path / "schemas"
    ep_dir = schemas_dir / "entrypoints" / "ep1"
    ep_dir.mkdir(parents=True)
    (ep_dir / "_meta.json").write_text(json.dumps({"id": "ep1"}))
    result = assemble_analysis(str(schemas_dir))
    assert "auxiliary_files" in result
    assert result["auxiliary_files"] == []


# ===========================================================================
# 1.2  SCOS_OUTPUT_SCHEMA env var — set to db.clone_schema; cleaned up on exit
# ===========================================================================

def _minimal_state(db: str = "MYDB") -> dict:
    return {
        "snowflake": {"database": db, "golden_schemas": {}, "account": "acct"},
        "config": {},
        "trials": {},
    }


def _minimal_request(tmp_path: Path, state: dict) -> TrialRequest:
    (tmp_path / "results").mkdir(exist_ok=True)
    return TrialRequest(
        trial_id="trial_001",
        flavor="scos",
        project_root=str(tmp_path),
        entrypoint_path="",
        ep_config={"id": "ep1", "tables": {}},
        mock_data_dir=str(tmp_path),
        results_dir=str(tmp_path / "results"),
        state_json=state,
        analysis={},
    )


def _scos_patches(clone_schema: str, captured: dict):
    """Patch scos_runtime dependencies; capture SCOS_OUTPUT_SCHEMA at run_and_capture time."""

    @contextlib.contextmanager
    def mock_clone(state_json, trial_id):
        yield clone_schema

    def mock_run_and_capture(spark, request, ctx):
        captured["SCOS_OUTPUT_SCHEMA"] = os.environ.get("SCOS_OUTPUT_SCHEMA")
        return {"ok": True, "error": None}

    return mock.patch.multiple(
        "runtimes.scos_runtime",
        clone_golden_schema_for_trial=mock_clone,
        declared_sink_tables=mock.MagicMock(return_value=[]),
        _list_seed_tables=mock.MagicMock(return_value=[]),
        install_sql_date_pin=mock.MagicMock(),
        _stage_root_for_trial=mock.MagicMock(return_value=None),
        run_and_capture=mock_run_and_capture,
    )


def test_scos_output_schema_db_qualified(tmp_path):
    """SCOS_OUTPUT_SCHEMA == db.clone_schema while run_trial is active."""
    db, clone_schema = "MYDB", "val_clone_001"
    captured: dict = {}
    spark_mock = mock.MagicMock()

    with _scos_patches(clone_schema, captured), \
         mock.patch.object(_SPC_MOD, "init_spark_session", return_value=spark_mock, create=True):
        os.environ.pop("SCOS_OUTPUT_SCHEMA", None)
        ScosRuntime().run_trial(_minimal_request(tmp_path, _minimal_state(db)))

    assert captured["SCOS_OUTPUT_SCHEMA"] == f"{db}.{clone_schema}"


def test_scos_output_schema_cleaned_up_after_run(tmp_path):
    """SCOS_OUTPUT_SCHEMA is removed from os.environ once run_trial finishes."""
    captured: dict = {}
    spark_mock = mock.MagicMock()

    with _scos_patches("val_clone_002", captured), \
         mock.patch.object(_SPC_MOD, "init_spark_session", return_value=spark_mock, create=True):
        os.environ.pop("SCOS_OUTPUT_SCHEMA", None)
        ScosRuntime().run_trial(_minimal_request(tmp_path, _minimal_state()))

    assert "SCOS_OUTPUT_SCHEMA" not in os.environ


def test_scos_output_schema_bare_when_no_db(tmp_path):
    """SCOS_OUTPUT_SCHEMA falls back to bare clone_schema when database is empty."""
    clone_schema = "val_clone_003"
    captured: dict = {}
    spark_mock = mock.MagicMock()

    with _scos_patches(clone_schema, captured), \
         mock.patch.object(_SPC_MOD, "init_spark_session", return_value=spark_mock, create=True):
        os.environ.pop("SCOS_OUTPUT_SCHEMA", None)
        ScosRuntime().run_trial(_minimal_request(tmp_path, _minimal_state(db="")))

    assert captured["SCOS_OUTPUT_SCHEMA"] == clone_schema


# ===========================================================================
# 1.2b Local runtime JVM env — SPARK_LOCAL_IP pinned before session build
# ===========================================================================

def _minimal_local_request(tmp_path: Path) -> TrialRequest:
    results_dir = tmp_path / "results"
    results_dir.mkdir(exist_ok=True)
    return TrialRequest(
        trial_id="trial_local_001",
        flavor="local",
        project_root=str(tmp_path),
        entrypoint_path="job.py",
        ep_config={"id": "ep1", "tables": {}, "path": "job.py"},
        mock_data_dir=str(tmp_path),
        results_dir=str(results_dir),
        state_json={},
        analysis={},
    )


def test_local_runtime_sets_spark_local_ip_before_build(tmp_path):
    """SPARK_LOCAL_IP is pinned before _build_local_session starts the JVM."""
    captured: dict = {}
    spark_mock = mock.MagicMock()

    def fake_build_local_session(_warehouse_dir):
        captured["SPARK_LOCAL_IP_during_build"] = os.environ.get("SPARK_LOCAL_IP")
        return spark_mock

    def fake_run_and_capture(_spark, _request, _ctx):
        return {"ok": True, "error": None}

    old_local_ip = os.environ.get("SPARK_LOCAL_IP")
    os.environ.pop("SCOS_OUTPUT_SCHEMA", None)
    os.environ.pop("SCOS_DATABASE_NAME", None)
    os.environ.pop("SPARK_LOCAL_IP", None)
    try:
        with mock.patch("runtimes.local_runtime._build_local_session", side_effect=fake_build_local_session), \
             mock.patch("runtimes.local_runtime.install_delta_patches"), \
             mock.patch("runtimes.local_runtime.file_io_env", return_value={}), \
             mock.patch("runtimes.local_runtime.declared_sink_tables", return_value=[]), \
             mock.patch("runtimes.local_runtime.seed_entrypoint", return_value=[]), \
             mock.patch("runtimes.local_runtime.run_and_capture", side_effect=fake_run_and_capture):
            LocalDeltaRuntime().run_trial(_minimal_local_request(tmp_path))
    finally:
        if old_local_ip is None:
            os.environ.pop("SPARK_LOCAL_IP", None)
        else:
            os.environ["SPARK_LOCAL_IP"] = old_local_ip

    assert captured["SPARK_LOCAL_IP_during_build"] == "127.0.0.1"
    assert "SPARK_LOCAL_IP" not in os.environ


# ===========================================================================
# 1.3  compare_results — a sink present in only one phase is always a failure
# ===========================================================================

def _dummy_parquet(path: Path) -> None:
    """Write a dummy .parquet file (_find_tables checks name only, not content)."""
    path.write_bytes(b"PAR1")


def _dummy_comparator(path: Path) -> None:
    path.write_text("def compare(a, b, **kw):\n    return {'result': 'match', 'summary': 'ok'}\n")


def test_compare_results_phase_b_only_raises(tmp_path):
    """A table produced only in Phase B (missing in Phase A) is always a failure.

    Unexpected missing sinks still fail here. Intentional empty sinks are handled
    earlier by the harness via the per-sink allow_empty override, so compare_results
    only sees real captured outputs."""
    val_dir = tmp_path / "Validation"
    val_dir.mkdir()
    trial_id = "trial_002"

    phase_a = val_dir / "Output" / f"{trial_id}_src"
    phase_b = val_dir / "Output" / trial_id
    (phase_a / "tables").mkdir(parents=True)
    (phase_b / "tables").mkdir(parents=True)
    _dummy_parquet(phase_b / "tables" / "surprise_table.parquet")
    comp = tmp_path / "comparator.py"
    _dummy_comparator(comp)

    with pytest.raises(AssertionError, match="surprise_table"):
        compare_results(str(phase_a), str(phase_b), str(comp))


# ===========================================================================
# 1.4  driver cleanup + script-mode execution
# ===========================================================================


def test_clear_trial_outputs_removes_stale_artifacts(tmp_path):
    trial_dir = tmp_path / "trial"
    (trial_dir / "tables").mkdir(parents=True)
    (trial_dir / "artifacts").mkdir()
    (trial_dir / "diffs").mkdir()
    for path in (
        trial_dir / "_harness_status.json",
        trial_dir / "_index.json",
        trial_dir / "_manual_review.json",
        trial_dir / "workload_error.txt",
        trial_dir / "capture_error.txt",
        trial_dir / "tables" / "out.parquet",
        trial_dir / "artifacts" / "wb.xlsx",
        trial_dir / "diffs" / "out.json",
    ):
        path.write_text("stale", encoding="utf-8")

    driver._clear_trial_outputs(str(trial_dir))

    assert list(trial_dir.iterdir()) == []


def test_run_and_capture_executes_script_mode_as_main(tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "Validation" / "source"
    source_dir.mkdir(parents=True)
    marker = tmp_path / "script_ran.txt"
    (source_dir / "job.py").write_text(
        "from pathlib import Path\n"
        f"MARKER = Path({str(marker)!r})\n"
        "if __name__ == '__main__':\n"
        "    MARKER.write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    request = TrialRequest(
        trial_id="ep_script",
        flavor="local",
        project_root=str(project_root),
        entrypoint_path="job.py",
        ep_config={"id": "ep_script", "tables": {}},
        results_dir=str(tmp_path / "results"),
    )
    ctx = TrialContext(
        trial_id="ep_script",
        flavor="local",
        output_schema="OUT",
        results_dir=str(tmp_path / "results"),
    )

    with mock.patch("runtimes._executor.capture_results", return_value={"tables": [], "artifacts": [], "failures": []}), \
         mock.patch("runtimes._executor.intercept_session", side_effect=lambda spark: contextlib.nullcontext(spark)):
        manifest = run_and_capture(object(), request, ctx)

    assert marker.read_text(encoding="utf-8") == "ran"
    assert manifest["ok"] is True


def test_run_and_capture_captures_system_exit_from_main(tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "Validation" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "job.py").write_text(
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(2)\n",
        encoding="utf-8",
    )
    request = TrialRequest(
        trial_id="ep_exit",
        flavor="local",
        project_root=str(project_root),
        entrypoint_path="job.py",
        ep_config={"id": "ep_exit", "tables": {}},
        results_dir=str(tmp_path / "results"),
    )
    ctx = TrialContext(
        trial_id="ep_exit",
        flavor="local",
        output_schema="OUT",
        results_dir=str(tmp_path / "results"),
    )

    with mock.patch("runtimes._executor.capture_results", return_value={"tables": [], "artifacts": [], "failures": []}), \
         mock.patch("runtimes._executor.intercept_session", side_effect=lambda spark: contextlib.nullcontext(spark)):
        manifest = run_and_capture(object(), request, ctx)

    assert manifest["ok"] is False
    assert manifest["error"] == "2"


def test_run_and_capture_treats_zero_system_exit_as_success(tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "Validation" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "job.py").write_text(
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(0)\n",
        encoding="utf-8",
    )
    request = TrialRequest(
        trial_id="ep_exit_zero",
        flavor="local",
        project_root=str(project_root),
        entrypoint_path="job.py",
        ep_config={"id": "ep_exit_zero", "tables": {}},
        results_dir=str(tmp_path / "results"),
    )
    ctx = TrialContext(
        trial_id="ep_exit_zero",
        flavor="local",
        output_schema="OUT",
        results_dir=str(tmp_path / "results"),
    )

    with mock.patch("runtimes._executor.capture_results", return_value={"tables": [], "artifacts": [], "failures": []}), \
         mock.patch("runtimes._executor.intercept_session", side_effect=lambda spark: contextlib.nullcontext(spark)):
        manifest = run_and_capture(object(), request, ctx)

    assert manifest["ok"] is True
    assert manifest["error"] is None


def test_run_and_capture_keeps_readwrite_sink_when_case_differs(tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "Validation" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "job.py").write_text("pass\n", encoding="utf-8")
    request = TrialRequest(
        trial_id="ep_case",
        flavor="local",
        project_root=str(project_root),
        entrypoint_path="job.py",
        ep_config={"id": "ep_case", "tables": {}},
        results_dir=str(tmp_path / "results"),
    )
    ctx = TrialContext(
        trial_id="ep_case",
        flavor="local",
        output_schema="OUT",
        results_dir=str(tmp_path / "results"),
        seed_tables=["OUT.MY_TABLE"],
        sink_tables=["out.my_table"],
    )
    captured: dict[str, Any] = {}

    def fake_capture_results(_spark, _schema, _trial_dir, sink_capture_dir=None, *, exclude, exclude_if_empty):
        captured["exclude"] = list(exclude)
        return {"tables": [{"name": "my_table"}], "artifacts": [], "failures": []}

    with mock.patch("runtimes._executor.capture_results", side_effect=fake_capture_results), \
         mock.patch("runtimes._executor.intercept_session", side_effect=lambda spark: contextlib.nullcontext(spark)):
        manifest = run_and_capture(object(), request, ctx)

    assert captured["exclude"] == []
    assert manifest["ok"] is True


def test_run_and_capture_fails_missing_nonempty_declared_sink(tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "Validation" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "job.py").write_text("pass\n", encoding="utf-8")
    request = TrialRequest(
        trial_id="ep_missing_sink",
        flavor="local",
        project_root=str(project_root),
        entrypoint_path="job.py",
        ep_config={
            "id": "ep_missing_sink",
            "tables": {
                "orders": {
                    "access": "write",
                    "category": "table",
                    "columns": [{"name": "id", "type": "int"}],
                }
            },
        },
        results_dir=str(tmp_path / "results"),
    )
    ctx = TrialContext(
        trial_id="ep_missing_sink",
        flavor="local",
        output_schema="OUT",
        results_dir=str(tmp_path / "results"),
    )

    with mock.patch("runtimes._executor.capture_results", return_value={"tables": [], "artifacts": [], "failures": []}), \
         mock.patch("runtimes._executor.intercept_session", side_effect=lambda spark: contextlib.nullcontext(spark)):
        manifest = run_and_capture(object(), request, ctx)

    assert manifest["ok"] is False
    assert manifest["failures"][0]["reason"] == "empty_declared_sink"
    assert "Fix the mock/schema data so the sink becomes non-empty" in manifest["failures"][0]["message"]
    assert "set allow_empty to a short reason string" in manifest["failures"][0]["message"]


def test_run_and_capture_allows_missing_allow_empty_sink(tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "Validation" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "job.py").write_text("pass\n", encoding="utf-8")
    request = TrialRequest(
        trial_id="ep_allow_empty_sink",
        flavor="local",
        project_root=str(project_root),
        entrypoint_path="job.py",
        ep_config={
            "id": "ep_allow_empty_sink",
            "tables": {
                "orders": {
                    "access": "write",
                    "category": "table",
                    "allow_empty": "incremental no-op is valid for this fixture",
                    "columns": [{"name": "id", "type": "int"}],
                }
            },
        },
        results_dir=str(tmp_path / "results"),
    )
    ctx = TrialContext(
        trial_id="ep_allow_empty_sink",
        flavor="local",
        output_schema="OUT",
        results_dir=str(tmp_path / "results"),
    )

    with mock.patch("runtimes._executor.capture_results", return_value={"tables": [], "artifacts": [], "failures": []}), \
         mock.patch("runtimes._executor.intercept_session", side_effect=lambda spark: contextlib.nullcontext(spark)):
        manifest = run_and_capture(object(), request, ctx)

    assert manifest["ok"] is True
    assert manifest["failures"] == []


class _StaticRuntime:
    def __init__(self, manifest):
        self._manifest = manifest

    def provision(self, _request):
        return None

    def run_trial(self, request):
        return SimpleNamespace(
            manifest=self._manifest,
            results_dir=request.results_dir,
            flavor=request.flavor,
            ok=not self._manifest.get("failures"),
        )


def test_driver_raises_clear_empty_sink_message(tmp_path):
    request = TrialRequest(
        trial_id="ep_driver_empty",
        flavor="local",
        project_root=str(tmp_path),
        entrypoint_path="job.py",
        ep_config={
            "id": "ep_driver_empty",
            "tables": {
                "orders": {
                    "access": "write",
                    "category": "table",
                    "columns": [{"name": "id", "type": "int"}],
                }
            },
        },
        results_dir=str(tmp_path / "results"),
    )
    manifest = {
        "tables": [],
        "artifacts": [],
        "failures": [{
            "reason": "empty_declared_sink",
            "message": (
                "Declared sink 'orders' produced no captured rows. Fix the mock/schema "
                "data so the sink becomes non-empty, or set allow_empty to a short "
                "reason string if empty output is intentional."
            ),
            "critical": True,
        }],
    }

    with mock.patch.object(driver, "get_runtime", return_value=_StaticRuntime(manifest)):
        with pytest.raises(AssertionError, match="set allow_empty to a short reason string"):
            driver.run_validation_trial(request)


def test_driver_surfaces_workload_error_before_empty_sink_failure(tmp_path):
    request = TrialRequest(
        trial_id="ep_driver_workload_error",
        flavor="local",
        project_root=str(tmp_path),
        entrypoint_path="job.py",
        ep_config={
            "id": "ep_driver_workload_error",
            "tables": {
                "orders": {
                    "access": "write",
                    "category": "table",
                    "columns": [{"name": "id", "type": "int"}],
                }
            },
        },
        results_dir=str(tmp_path / "results"),
    )
    manifest = {
        "tables": [],
        "artifacts": [],
        "error": "boom before any sink write",
        "failures": [{
            "reason": "empty_declared_sink",
            "message": (
                "Declared sink 'orders' produced no captured rows. Fix the mock/schema "
                "data so the sink becomes non-empty, or set allow_empty to a short "
                "reason string if empty output is intentional."
            ),
            "critical": True,
        }],
    }

    with mock.patch.object(driver, "get_runtime", return_value=_StaticRuntime(manifest)):
        with pytest.raises(RuntimeError, match="Workload error in trial ep_driver_workload_error: boom before any sink write"):
            driver.run_validation_trial(request)


# ===========================================================================
# 2.  seed-venv — pandas + pyarrow in both phases
# ===========================================================================

def _setup_seed_venv(tmp_path: Path, phase: str) -> SimpleNamespace:
    """Create minimal filesystem structure for cmd_seed_venv and return args."""
    conv_root = tmp_path / "project"
    val = conv_root / "Validation"
    venv_name = ".venv-source" if phase == "a" else ".venv-scos"
    venv_python = val / "shared" / venv_name / "bin" / "python"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("#!/bin/sh\nexec python3 \"$@\"\n")
    venv_python.chmod(0o755)
    (val / "state.json").write_text(json.dumps(
        {"schema_version": validate.SCHEMA_VERSION, "milestones": {}, "paths": {}}
    ))
    return SimpleNamespace(conv_root=str(conv_root), phase=phase)


def _collect_pip_install_calls(args) -> list[list]:
    """Run cmd_seed_venv with mocked subprocess; return list of uv pip install calls."""
    calls: list[list] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        r = mock.MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with mock.patch("validate.subprocess.run", side_effect=fake_run):
        validate.cmd_seed_venv(args)

    return [c for c in calls if "pip" in c and "install" in c]


def test_seed_venv_phase_a_installs_pandas_pyarrow(tmp_path):
    """Phase-A seed-venv includes pandas and pyarrow in the core-deps install."""
    args = _setup_seed_venv(tmp_path, "a")
    pip_calls = _collect_pip_install_calls(args)
    all_pkgs = {pkg for call in pip_calls for pkg in call}
    assert "pandas" in all_pkgs, f"pandas missing; pip calls: {pip_calls}"
    assert "pyarrow" in all_pkgs, f"pyarrow missing; pip calls: {pip_calls}"


def test_seed_venv_phase_b_installs_pandas_pyarrow(tmp_path):
    """Phase-B seed-venv includes pandas and pyarrow in the core-deps install."""
    args = _setup_seed_venv(tmp_path, "b")
    pip_calls = _collect_pip_install_calls(args)
    all_pkgs = {pkg for call in pip_calls for pkg in call}
    assert "pandas" in all_pkgs, f"pandas missing; pip calls: {pip_calls}"
    assert "pyarrow" in all_pkgs, f"pyarrow missing; pip calls: {pip_calls}"
