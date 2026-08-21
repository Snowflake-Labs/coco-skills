"""Tests for _scos_max_workers() in harness/conftest.py.

Loads harness/conftest.py via importlib with lightweight stubs so that
pyspark / snowpark / snowflake are never imported.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# One-time module load with mocked heavy deps
# ---------------------------------------------------------------------------

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_TESTS_DIR)
_HARNESS_DIR = os.path.join(_SCRIPTS_DIR, "harness")
_CONFTEST_PATH = os.path.join(_HARNESS_DIR, "conftest.py")

_MODULE_NAME = "harness_conftest_under_test"


def _install_stubs() -> None:
    """Inject lightweight stubs before conftest.py executes."""
    # helpers.assemble_analysis — returns a minimal analysis dict
    if "helpers" not in sys.modules:
        mock_helpers = types.ModuleType("helpers")
        mock_helpers.assemble_analysis = lambda schemas_dir: {"entrypoints": [{"id": "ep0"}]}
        sys.modules["helpers"] = mock_helpers

    # runtimes / runtimes.base
    if "runtimes" not in sys.modules:
        mock_runtimes = types.ModuleType("runtimes")
        sys.modules["runtimes"] = mock_runtimes
    if "runtimes.base" not in sys.modules:
        mock_base = types.ModuleType("runtimes.base")
        mock_base.is_phase_b = lambda flavor: False
        sys.modules["runtimes.base"] = mock_base
        sys.modules["runtimes"].base = mock_base  # type: ignore[attr-defined]

    # Ensure harness dir is on sys.path so conftest's own imports resolve
    if _HARNESS_DIR not in sys.path:
        sys.path.insert(0, _HARNESS_DIR)


@pytest.fixture(scope="module")
def conftest_mod():
    """Return harness/conftest.py loaded as a plain module (once per session)."""
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]

    # Snapshot keys we're about to inject so we can restore them after loading.
    # This prevents our stubs from shadowing real implementations seen by other
    # test modules later in the session (e.g. test_schema_mine imports helpers).
    _stub_keys = ("helpers", "runtimes", "runtimes.base")
    _saved = {k: sys.modules.get(k) for k in _stub_keys}

    _install_stubs()

    try:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _CONFTEST_PATH)
        mod = importlib.util.module_from_spec(spec)
        # Register before exec_module so forward-ref annotations inside conftest
        # resolve correctly (required when from __future__ import annotations is
        # present and the module is loaded via importlib).
        sys.modules[_MODULE_NAME] = mod
        spec.loader.exec_module(mod)
    finally:
        # Restore sys.modules for stub keys so real modules remain accessible.
        for key, orig in _saved.items():
            if orig is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = orig

    return mod


# ---------------------------------------------------------------------------
# _scos_max_workers() case matrix
# ---------------------------------------------------------------------------

def test_20_eps_no_env_phase_a_caps_at_cpu(conftest_mod, monkeypatch):
    """Phase A runs a local Spark JVM per worker → no env override caps at cpu_count."""
    monkeypatch.setattr(conftest_mod, "_ANALYSIS", {"entrypoints": [{}] * 20})
    monkeypatch.setattr(conftest_mod, "_IS_PHASE_B", False)
    monkeypatch.setattr(conftest_mod.os, "cpu_count", lambda: 4)
    monkeypatch.delenv("SCOS_PYTEST_WORKERS", raising=False)
    assert conftest_mod._scos_max_workers() == 4


def test_20_eps_no_env_phase_b_returns_20(conftest_mod, monkeypatch):
    """Phase B is Snowflake IO-bound → no env override gives one worker per EP (20)."""
    monkeypatch.setattr(conftest_mod, "_ANALYSIS", {"entrypoints": [{}] * 20})
    monkeypatch.setattr(conftest_mod, "_IS_PHASE_B", True)
    monkeypatch.delenv("SCOS_PYTEST_WORKERS", raising=False)
    assert conftest_mod._scos_max_workers() == 20


def test_20_eps_env_8_returns_8(conftest_mod, monkeypatch):
    """SCOS_PYTEST_WORKERS=8 acts as an upper cap when ep_count > 8."""
    monkeypatch.setattr(conftest_mod, "_ANALYSIS", {"entrypoints": [{}] * 20})
    monkeypatch.setenv("SCOS_PYTEST_WORKERS", "8")
    assert conftest_mod._scos_max_workers() == 8


def test_3_eps_env_999_returns_3(conftest_mod, monkeypatch):
    """SCOS_PYTEST_WORKERS=999 never exceeds ep_count (3)."""
    monkeypatch.setattr(conftest_mod, "_ANALYSIS", {"entrypoints": [{}] * 3})
    monkeypatch.setenv("SCOS_PYTEST_WORKERS", "999")
    assert conftest_mod._scos_max_workers() == 3


def test_0_eps_returns_1(conftest_mod, monkeypatch):
    """Zero entrypoints → safety floor of 1."""
    monkeypatch.setattr(conftest_mod, "_ANALYSIS", {"entrypoints": []})
    monkeypatch.delenv("SCOS_PYTEST_WORKERS", raising=False)
    assert conftest_mod._scos_max_workers() == 1
