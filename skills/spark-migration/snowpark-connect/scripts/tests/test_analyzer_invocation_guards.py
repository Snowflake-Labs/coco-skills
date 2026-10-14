from __future__ import annotations

import pytest

import analyze_pyspark
import check_cortex_llm_access
import scos_session


class _FakeCompleteOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_probe_returns_non_empty_response(monkeypatch):
    monkeypatch.setattr(scos_session, "CompleteOptions", _FakeCompleteOptions)
    monkeypatch.setattr(scos_session, "cortex_complete", lambda *args, **kwargs: "  OK  ")
    out = scos_session.verify_cortex_complete_access(object(), model="claude-opus-4-6")
    assert out == "OK"


def test_probe_rejects_empty_response(monkeypatch):
    monkeypatch.setattr(scos_session, "CompleteOptions", _FakeCompleteOptions)
    monkeypatch.setattr(scos_session, "cortex_complete", lambda *args, **kwargs: "   ")
    with pytest.raises(RuntimeError, match="returned empty output"):
        scos_session.verify_cortex_complete_access(object())


@pytest.mark.parametrize(
    "message",
    [
        "403 Forbidden",
        "Unknown user-defined function SNOWFLAKE.CORTEX.COMPLETE",
        "SQL access control error: Insufficient privileges to operate on account",
        "USE AI FUNCTIONS is required",
    ],
)
def test_non_retryable_error_markers(message):
    assert scos_session.is_non_retryable_llm_error(RuntimeError(message)) is True


def test_retryable_error_is_not_marked_non_retryable():
    assert scos_session.is_non_retryable_llm_error(RuntimeError("Read timed out")) is False


def test_preflight_cli_passes_and_prints_identity(monkeypatch, capsys):
    session = _FakeSession()
    monkeypatch.setattr(check_cortex_llm_access, "open_session", lambda connection: session)
    monkeypatch.setattr(
        check_cortex_llm_access,
        "get_session_identity",
        lambda _session: ("acct", "user", "role"),
    )
    monkeypatch.setattr(
        check_cortex_llm_access,
        "verify_cortex_complete_access",
        lambda _session, model: "OK",
    )
    monkeypatch.setattr(
        check_cortex_llm_access.sys,
        "argv",
        ["check_cortex_llm_access.py", "--connection", "myconn"],
    )

    rc = check_cortex_llm_access.main()
    out = capsys.readouterr().out

    assert rc == 0
    assert "CORTEX_LLM_PREFLIGHT=PASS" in out
    assert "connection=myconn" in out
    assert session.closed is True


def test_preflight_cli_fails_loudly(monkeypatch, capsys):
    session = _FakeSession()
    monkeypatch.setattr(check_cortex_llm_access, "open_session", lambda connection: session)
    monkeypatch.setattr(
        check_cortex_llm_access,
        "get_session_identity",
        lambda _session: ("acct", "user", "role"),
    )

    def _raise(_session, model):
        raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(check_cortex_llm_access, "verify_cortex_complete_access", _raise)
    monkeypatch.setattr(check_cortex_llm_access.sys, "argv", ["check_cortex_llm_access.py"])

    rc = check_cortex_llm_access.main()
    err = capsys.readouterr().err

    assert rc == 2
    assert "CORTEX_LLM_PREFLIGHT=FAIL" in err
    assert "403 Forbidden" in err
    assert session.closed is True


def test_pyspark_cli_accepts_path_flag(tmp_path):
    args = analyze_pyspark._parse_args(["--path", str(tmp_path)])
    assert args.path == str(tmp_path)


def test_pyspark_cli_rejects_positional_path_only(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        analyze_pyspark._parse_args([str(tmp_path)])
    assert excinfo.value.code == 2


def test_pyspark_cli_requires_path_value():
    with pytest.raises(SystemExit) as excinfo:
        analyze_pyspark._parse_args(["--path"])
    assert excinfo.value.code == 2
