"""Tests encoding mandatory-AST behavior for analyze_java.py (TDD red phase).

Pre-fix status against current (fallback-enabled) code:
  test_hard_fails_when_facts_unavailable        → FAILS  (no exit; regex fallback runs)
  test_hard_fails_when_env_disabled             → FAILS  (no exit; SCOS_NO_AST_FACTS=1 bypass works)
  test_parse_failure_hard_fails                 → FAILS  (no parse_ok gate in main; no exit)
  test_succeeds_when_facts_available            → PASSES (happy path already completes normally)
  test_enforce_ast_facts_java_exits_3_on_none   → PASSES (_enforce_ast_facts logic already correct)
  test_enforce_ast_facts_java_noop_when_present → PASSES (_enforce_ast_facts logic already correct)

Tests 1-3 are expected to fail until the implementor:
  - Removes the SCOS_NO_AST_FACTS bypass guard in main()
  - Changes the extract_facts None-return from a silent fallback to sys.exit(3)
  - Adds a per-file parse_ok check that exits 3 on failures
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Ensure scripts/ is on sys.path ─────────────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ── Guard: analyze_java imports snowflake.snowpark at module level ──────────
try:
    import snowflake.snowpark  # noqa: F401
    _SNOWPARK_AVAILABLE = True
except ImportError:
    _SNOWPARK_AVAILABLE = False

if not _SNOWPARK_AVAILABLE:
    _sf = types.ModuleType("snowflake")
    _sf.snowpark = types.ModuleType("snowflake.snowpark")  # type: ignore[attr-defined]
    _sf.snowpark.Session = object  # type: ignore[attr-defined]
    sys.modules.setdefault("snowflake", _sf)
    sys.modules.setdefault("snowflake.snowpark", _sf.snowpark)

import analyze_java as A  # noqa: E402  (after sys.path and snowflake stub)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _fake_java_ast_facts(return_value) -> types.ModuleType:
    """Build a fake java_ast_facts module whose extract_facts returns return_value."""
    mod = types.ModuleType("java_ast_facts")
    mod.extract_facts = lambda *a, **kw: return_value  # type: ignore[attr-defined]
    return mod


def _run_main(argv: list[str], monkeypatch) -> None:
    """Drive analyze_java.main() with argv, stubbing all I/O infrastructure.

    Mocks open_session, build_rag, and analyze_files_concurrently so tests
    run offline without a JDK or Snowflake connection.
    """
    monkeypatch.setattr(sys, "argv", ["analyze_java.py"] + argv)
    mock_session = MagicMock()
    monkeypatch.setattr(A, "open_session", lambda *a, **kw: mock_session)
    monkeypatch.setattr(A, "build_rag", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(A, "analyze_files_concurrently", lambda *a, **kw: [])
    A.main()


# ── Tests driving main() — expected to FAIL against current code ─────────────

def test_hard_fails_when_facts_unavailable(monkeypatch, tmp_path):
    """extract_facts returning None must cause exit 3, not a silent regex fallback.

    Current code: _enforce_ast_facts(require=False, ...) → returns without exit.
    FAILS pre-fix. PASSES after: extraction block hard-fails on None return.
    """
    f = tmp_path / "Job.java"
    f.write_text(
        "import org.apache.spark.sql.SparkSession;\nclass Job {}",
        encoding="utf-8",
    )
    monkeypatch.setitem(sys.modules, "java_ast_facts", _fake_java_ast_facts(None))
    with pytest.raises(SystemExit) as exc:
        _run_main(["--path", str(f)], monkeypatch)
    assert exc.value.code == 3


def test_hard_fails_when_env_disabled(monkeypatch, tmp_path):
    """SCOS_NO_AST_FACTS=1 must be rejected for Java (exit 3), not used as a bypass.

    Current code: env guard skips extraction entirely; _enforce_ast_facts called
    with require=False → no exit. FAILS pre-fix.
    PASSES after: extraction block checks env and exits 3 immediately.
    """
    monkeypatch.setenv("SCOS_NO_AST_FACTS", "1")
    with pytest.raises(SystemExit) as exc:
        _run_main(["--path", str(tmp_path)], monkeypatch)
    assert exc.value.code == 3


def test_parse_failure_hard_fails(monkeypatch, tmp_path):
    """A file present in facts with parse_ok=False must produce exit 3 with no rows.

    Current code: no parse_ok gate; silently falls through to analysis with the
    failed entry. No SystemExit. FAILS pre-fix.
    PASSES after: main() detects parse failures and exits 3.
    """
    f = tmp_path / "Broken.java"
    f.write_text("not valid java !!!;", encoding="utf-8")
    broken_key = str(f.resolve())
    facts = {
        broken_key: {
            "parse_ok": False,
            "imports": [], "calls": [], "selects": [], "new_types": [], "spark_sql": [],
        }
    }
    monkeypatch.setitem(sys.modules, "java_ast_facts", _fake_java_ast_facts(facts))
    with pytest.raises(SystemExit) as exc:
        _run_main(["--path", str(tmp_path)], monkeypatch)
    assert exc.value.code == 3


def test_succeeds_when_facts_available(monkeypatch, tmp_path):
    """Valid facts with parse_ok=True → analyze_java.main() completes without exit.

    Current code: happy path already works. PASSES.
    After fix: same behavior. PASSES.
    """
    f = tmp_path / "Job.java"
    f.write_text("class Job {}", encoding="utf-8")
    file_key = str(f.resolve())
    facts = {
        file_key: {
            "parse_ok": True,
            "imports": [], "calls": [], "selects": [], "new_types": [], "spark_sql": [],
        }
    }
    monkeypatch.setitem(sys.modules, "java_ast_facts", _fake_java_ast_facts(facts))
    # Must NOT raise SystemExit.
    _run_main(["--path", str(f)], monkeypatch)


# ── Tests calling _enforce_ast_facts directly — PASS against current code ────

def test_enforce_ast_facts_java_exits_3_on_none():
    """_enforce_ast_facts(require=True, facts=None) exits 3.

    The function already has correct logic. This pins the contract so the
    call-site change (always require=True) is caught if the function is regressed.
    Current code: PASSES. After fix: PASSES.
    """
    with pytest.raises(SystemExit) as exc:
        A._enforce_ast_facts(require=True, facts=None, no_ast_env=False)
    assert exc.value.code == 3


def test_enforce_ast_facts_java_noop_when_present():
    """_enforce_ast_facts(require=True, facts=non-None) → no exit.

    Current code: PASSES. After fix: PASSES.
    """
    A._enforce_ast_facts(require=True, facts={"Job.java": {}}, no_ast_env=False)
