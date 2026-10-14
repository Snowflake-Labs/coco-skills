"""Tests for the --require-ast-facts gate in ``analyze_scala``.

The gate is enforced by ``_enforce_ast_facts(require, facts, no_ast_env)``.
These tests call it directly so no session/RAG mocking is needed.
Exit code 3 mirrors ``--require-type-check`` in revert_failing_scala_files.py.
"""
from __future__ import annotations

import pytest

from analyze_scala import _enforce_ast_facts


def test_exits_3_when_facts_none():
    """Toolchain present but extract_facts returned None → must fail loud."""
    with pytest.raises(SystemExit) as exc:
        _enforce_ast_facts(require=True, facts=None, no_ast_env=False)
    assert exc.value.code == 3


def test_exits_3_when_env_disabled_conflicts():
    """SCOS_NO_AST_FACTS=1 + --require-ast-facts is a contradiction → exit 3."""
    with pytest.raises(SystemExit) as exc:
        _enforce_ast_facts(require=True, facts=None, no_ast_env=True)
    assert exc.value.code == 3


def test_no_exit_when_facts_present():
    """Facts available → gate is satisfied, no exit."""
    _enforce_ast_facts(require=True, facts={"a.scala": {}}, no_ast_env=False)


def test_noop_when_not_required():
    """Without the flag nothing should fail regardless of facts or env."""
    _enforce_ast_facts(require=False, facts=None, no_ast_env=True)
