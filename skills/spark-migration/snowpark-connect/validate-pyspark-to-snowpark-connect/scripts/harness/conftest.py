"""Shared validation test kit copied into ``Validation/tests``.

This file bootstraps the shared test environment (paths, date pin,
aux files) and exposes lightweight per-entrypoint fixtures. The actual runtime
(local PySpark+Delta, Databricks cluster, or Snowpark Connect/SCOS) is selected
and driven by the ``runtimes`` package; rendered tests call
``runtimes.driver.run_validation_trial``.
"""

from __future__ import annotations

# Prevent pytest from collecting the unrendered test_template.py when this
# conftest lives alongside it (e.g. in the harness source tree).
collect_ignore_glob = ["test_template.py"]

import datetime
import json
import os
import sys
import time
import uuid

os.environ["TZ"] = "UTC"
time.tzset()
os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

_HERE = os.path.dirname(os.path.abspath(__file__))
_VALIDATION_ROOT = os.path.dirname(_HERE)
_CONV_ROOT = os.path.dirname(_VALIDATION_ROOT)
_SHARED_DIR = os.path.join(_VALIDATION_ROOT, "shared")
_SOURCE_ROOT = os.path.join(_VALIDATION_ROOT, "source")
_OUTPUT_ROOT = os.path.join(_CONV_ROOT, "Output")
_AUX_DIR = os.path.join(_SHARED_DIR, "auxiliary")
_SCHEMAS_DIR = os.path.join(_SHARED_DIR, "schemas")

# Make the runtimes package importable for flavor classification (light: only
# loads runtimes.base, no pyspark/sdk).
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtimes.base import is_phase_b  # noqa: E402

# SCOS_FLAVOR: only "scos" is meaningful (Phase B). For Phase A, the driver
# resolves each entrypoint's source_runtime from ep_config.
_FLAVOR = os.environ.get("SCOS_FLAVOR", "local")
_IS_PHASE_B = is_phase_b(_FLAVOR)
_WORKLOAD_ROOT = _OUTPUT_ROOT if _IS_PHASE_B else _SOURCE_ROOT

from helpers import assemble_analysis  # noqa: E402
_ANALYSIS = assemble_analysis(_SCHEMAS_DIR)

_PYTHONPATH_ENTRIES = [_HERE, _WORKLOAD_ROOT]
for _root in _ANALYSIS.get("import_roots", []):
    _p = os.path.join(_WORKLOAD_ROOT, _root)
    if _p not in sys.path:
        sys.path.insert(0, _p)
    _PYTHONPATH_ENTRIES.append(_p)
if _WORKLOAD_ROOT not in sys.path:
    sys.path.insert(0, _WORKLOAD_ROOT)
_existing_pythonpath = os.environ.get("PYTHONPATH", "")
_pythonpath_parts = [p for p in _PYTHONPATH_ENTRIES if p]
if _existing_pythonpath:
    _pythonpath_parts.append(_existing_pythonpath)
os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(_pythonpath_parts))

if _IS_PHASE_B:
    os.environ["SPARK_CONNECT_MODE_ENABLED"] = "1"

os.environ["SCOS_MOCK_DATA_DIR"] = os.path.join(_SHARED_DIR, "mock_data")
os.environ["SCOS_RESULTS_DIR"] = os.environ.get(
    "SCOS_RESULTS_DIR",
    os.path.join(
        _VALIDATION_ROOT,
        "results",
        "phase_b" if _IS_PHASE_B else "phase_a",
    ),
)
os.environ["SCOS_STATE_JSON"] = os.path.join(_VALIDATION_ROOT, "state.json")
os.environ["SCOS_SCHEMAS_DIR"] = _SCHEMAS_DIR
os.environ["SCOS_CONV_ROOT"] = _CONV_ROOT
os.environ["SCOS_RUN_ID"] = uuid.uuid4().hex[:8]

# Re-export SCOS_DATABRICKS_ENV_FILE from state.json if not already in the process env.
if "SCOS_DATABRICKS_ENV_FILE" not in os.environ:
    try:
        with open(os.path.join(_VALIDATION_ROOT, "state.json"), encoding="utf-8") as _sf:
            _state = json.load(_sf)
        _dbx_env = _state.get("databricks", {}).get("env_file")
        if _dbx_env and os.path.isfile(_dbx_env):
            os.environ["SCOS_DATABRICKS_ENV_FILE"] = _dbx_env
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

for _aux in _ANALYSIS.get("auxiliary_files", []):
    if "name" in _aux:
        _aux_key = _aux["name"]
    elif "id" in _aux:
        _aux_key = _aux["id"]
    else:
        raise ValueError(
            f"auxiliary_files entry missing both 'name' and 'id': {_aux!r}. "
            "Expected shape {name, aux_file} or {id, path[, kind]}."
        )
    if "aux_file" in _aux:
        _aux_path = os.path.join(_AUX_DIR, _aux["aux_file"])
    elif "path" in _aux:
        _aux_path = os.path.join(_AUX_DIR, os.path.basename(_aux["path"]))
    else:
        raise ValueError(
            f"auxiliary_files entry missing both 'aux_file' and 'path': {_aux!r}. "
            "Expected shape {name, aux_file} or {id, path[, kind]}."
        )
    os.environ[f"SCOS_TEST_AUX_{_aux_key.upper()}"] = _aux_path


def _install_date_pin() -> None:
    if os.environ.get("SCOS_PIN_DATE_DISABLED") == "1":
        return
    try:
        import pyspark.sql.functions as F
    except ImportError:
        # Phase B runs in the scos venv (snowpark-connect, no pyspark). SCOS pins
        # current_date()/current_timestamp() server-side in ScosRuntime instead.
        return

    pinned = os.environ.get("SCOS_PINNED_DATE") or datetime.date.today().isoformat()
    F.current_date = lambda: F.to_date(F.lit(pinned))
    F.current_timestamp = lambda: F.to_timestamp(F.lit(f"{pinned} 00:00:00"))


_install_date_pin()

# Bridge: migrated code uses bare 'snowpark_connect' but package is 'snowflake.snowpark_connect'.
try:
    import snowflake.snowpark_connect as _spc
    sys.modules.setdefault("snowpark_connect", _spc)
except Exception:
    pass

import pytest


# ---------------------------------------------------------------------------
# pytest-xdist worker cap
# One worker per EP; Phase B tests are IO-bound (Snowflake / SCOS), so we do
# not cap on CPU. Set SCOS_PYTEST_WORKERS to force a lower cap if your
# Snowflake account has a low concurrent-session limit.
# ---------------------------------------------------------------------------

def _scos_max_workers() -> int:
    """Return the number of xdist workers for this run.

    Default: one worker per entrypoint (ep_count). Phase B tests are
    IO-bound (Snowflake / SCOS), so we do not cap on CPU. Phase A runs a local
    Spark JVM per worker, so it IS capped at the CPU count to avoid heap/GC
    contention (and OOM) from N concurrent JVMs on a smaller box.

    Set SCOS_PYTEST_WORKERS to force a lower cap if your Snowflake account
    has a low concurrent-session limit.
    """
    ep_count = len(_ANALYSIS.get("entrypoints", []))
    if ep_count == 0:
        return 1
    env_str = os.environ.get("SCOS_PYTEST_WORKERS", "")
    if env_str:
        env_val = int(env_str)
        if env_val > 0:
            return min(ep_count, env_val)
    if not _IS_PHASE_B:
        return min(ep_count, os.cpu_count() or ep_count)
    return ep_count


def pytest_xdist_auto_num_workers(config):
    """Cap the worker count chosen by ``-n auto``.

    xdist resolves ``-n auto`` to ``os.cpu_count()`` via THIS hook, BEFORE the
    ``config.option.numprocesses`` override in ``pytest_configure`` can take
    effect. By implementing this hook we return ``_scos_max_workers()`` so
    Phase B tests run one worker per entrypoint.
    """
    return _scos_max_workers()


def pytest_configure(config):
    """Cap xdist workers when ``-n auto`` is passed.

    Delegates to ``_scos_max_workers()``; see that function's docstring for
    the rule. With ``-n 1`` or no ``-n`` flag, this is a no-op.
    """
    numprocesses = getattr(config.option, "numprocesses", None)
    if numprocesses is None:
        return  # xdist not active (no -n flag)

    max_workers = _scos_max_workers()

    # numprocesses == "auto" (str) or an int
    if numprocesses == "auto" or (
        isinstance(numprocesses, int) and numprocesses > max_workers
    ):
        config.option.numprocesses = max_workers


# Provisioning (local Spark/Delta, Snowflake clone) and capture now live in the
# runtimes package; conftest only bootstraps the environment and exposes the
# lightweight per-entrypoint fixtures. Rendered tests call
# ``runtimes.driver.run_validation_trial`` directly.

@pytest.fixture(scope="session")
def analysis():
    return _ANALYSIS


@pytest.fixture(scope="session")
def mock_data_root():
    return os.environ["SCOS_MOCK_DATA_DIR"]


@pytest.fixture(scope="module")
def ep_id(request):
    name = os.path.basename(str(request.fspath))
    return name.removeprefix("test_").removesuffix(".py")


@pytest.fixture(scope="module")
def mock_data_dir(mock_data_root, ep_id):
    return os.path.join(mock_data_root, ep_id)


@pytest.fixture(scope="module")
def ep_config(analysis, ep_id):
    return next(e for e in analysis["entrypoints"] if e["id"] == ep_id)


@pytest.fixture(scope="session")
def state_json():
    # Session scope is safe: fixture consumers only READ this dict. The per-trial
    # mutation in ScosRuntime.provision() operates on request.state_json — a
    # SEPARATE copy that build_trial_request() loads fresh from SCOS_STATE_JSON —
    # not this shared fixture instance.
    with open(os.environ["SCOS_STATE_JSON"], encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _sf_conn_scope():
    from helpers import _SF_CONN_HOLDER, SharedSnowflakeConn  # type: ignore
    holder = SharedSnowflakeConn()
    token = _SF_CONN_HOLDER.set(holder)
    try:
        yield holder
    finally:
        _SF_CONN_HOLDER.reset(token)
        holder.close()

