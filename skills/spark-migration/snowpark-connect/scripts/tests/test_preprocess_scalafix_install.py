"""Tests for Phase-0.5b scalafix cs-launch model.

TDD RED PHASE — all tests targeting new behaviour are expected to FAIL until
``_resolve_scalafix_invocation`` + ``--no-auto-launch`` + ``SCOS_SCALAFIX_AUTO_LAUNCH``
opt-out + smoke-check are implemented.

Assertions covered
------------------
1.  scalafix-cli already on PATH  →  invocation prefix is ``[bin]``; no
    ``cs launch`` subprocess attempted.
2.  scalafix absent + Coursier (cs/coursier) absent  →  exit 1, status=failed,
    skip_reason mentions "Coursier" or "cs" unavailable *to launch* it (not the
    stale "not on PATH" message).
3a. scalafix absent + ``--no-auto-launch`` flag  →  exit 1, status=failed,
    no launch subprocess.
3b. scalafix absent + ``SCOS_SCALAFIX_AUTO_LAUNCH=0``  →  same as 3a.
3c. (back-compat) ``--no-auto-install`` deprecated alias  →  same as 3a.
3d. (back-compat) ``SCOS_SCALAFIX_AUTO_INSTALL=0`` deprecated alias  →  same as 3a.
4.  scalafix absent + cs present + smoke-check PASSES (stub ``cs launch
    COORDS -- --version`` → rc=0 + "Scalafix vX.Y.Z" output)  →  invocation
    prefix is ``[cs, "launch", coords, "--"]``; no coursier-absence skip_reason.
4b. Per-file cmd is built as ``[cs, "launch", coords, "--", "--rules", ...]``.
5.  scalafix absent + cs present + smoke-check FAILS (stub → rc≠0 / bad output)
    →  exit 1, status=failed, skip_reason mentions "cs launch" or "could not
    start".
6.  No ``os.environ["PATH"]`` mutation occurs in the cs-launch path.
6b. ``cs install`` is NEVER invoked in any branch.
7.  Exit code reflects runner availability: 1 when no runner resolved, 0 when
    a runner was found (parametrised sweep).
8.  ``python -m py_compile`` passes on the script.
9.  No stale ``cs install`` / install wording in SKILL.md or recipes.md.

Run from the ``snowpark-connect/`` directory::

    uv run --project . python -m pytest scripts/tests/test_preprocess_scalafix_install.py -v

Expected: ALL tests targeting new behaviour FAIL (red phase).  The implementation
refactor happens in Phase 3.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Load the module under test via importlib (path contains hyphens — can't import)
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parents[1]   # .../scripts/
_SKILL_ROOT = _SCRIPTS_DIR.parent                            # .../snowpark-connect/

_spec = importlib.util.spec_from_file_location(
    "preprocess_scalafix",
    _SCRIPTS_DIR / "preprocess_scalafix.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

main = _mod.main
PHASE_KEY = _mod.PHASE_KEY

# The default coordinate the implementation should use.  Try reading from the
# module so the test stays in sync; fall back to the pinned value confirmed by
# the reviser (ch.epfl.scala::scalafix-cli:0.14.6 or the catalog shorthand).
DEFAULT_COORDS: str = getattr(
    _mod,
    "DEFAULT_SCALAFIX_COORDS",
    "ch.epfl.scala::scalafix-cli:0.14.6",
)

# ---------------------------------------------------------------------------
# Constants shared by helpers
# ---------------------------------------------------------------------------

_FAKE_CS = "/usr/local/bin/cs"

# Real scalafix --version output format: "Scalafix vX.Y.Z"
_SMOKE_VERSION_OUTPUT = "Scalafix v0.14.6"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_classpath_cache(tmp_path, monkeypatch):
    """Redirect the on-disk classpath cache to a per-test tmpdir.

    Prevents tests from reading or writing the real ``.classpath_cache.json``
    file in ``scripts/scalafix_sbt/``.  Without this, tests that exercise the
    sbt runner path would corrupt the cache with fake classpath data, and tests
    that run after a real Phase 0.5 invocation would get unexpected cache hits.
    """
    monkeypatch.setattr(_mod, "_CP_CACHE_PATH", tmp_path / ".classpath_cache.json")


@pytest.fixture()
def state_file(tmp_path):
    """Minimal valid migration_state.json with an empty manifest.

    With no .scala files the phase reaches the resolver quickly, letting us
    focus assertions on the launch/skip branch without needing real scalafix.
    """
    migrated = tmp_path / "migrated"
    migrated.mkdir()
    state: dict[str, Any] = {"migrated_dir": str(migrated), "manifest": []}
    p = tmp_path / "migration_state.json"
    p.write_text(json.dumps(state))
    return p


@pytest.fixture()
def state_file_with_scala(tmp_path):
    """State file with one real .scala file present in the migrated dir.

    Used to verify per-file subprocess invocations in test_a4b.
    """
    migrated = tmp_path / "migrated"
    migrated.mkdir()
    scala_file = migrated / "Foo.scala"
    scala_file.write_text("object Foo { def bar(): Int = 1 }\n")
    state: dict[str, Any] = {
        "migrated_dir": str(migrated),
        "manifest": ["Foo.scala"],
    }
    p = tmp_path / "migration_state.json"
    p.write_text(json.dumps(state))
    return p


def _phase_result(state_file: pathlib.Path) -> dict[str, Any]:
    """Read back the 0_5b_scalafix result written into the state file."""
    state = json.loads(state_file.read_text())
    return state.get("phases_completed", {}).get(PHASE_KEY, {})


def _cp(cmd, returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Build a CompletedProcess stub."""
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def _safe_main(argv: list[str]) -> int:
    """Run main(), converting SystemExit to int.

    argparse calls sys.exit(2) for unrecognised flags (e.g. --no-auto-launch
    before the arg is wired up).  Capturing that makes assertions cleaner:
    the test fails with 'assert 2 == 0' rather than an ERROR.
    """
    try:
        return main(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def _is_smoke_check(cmd) -> bool:
    """True when *cmd* is the pre-flight ``cs launch ... -- --version`` probe."""
    return (
        isinstance(cmd, list)
        and "launch" in cmd
        and "--" in cmd
        and "--version" in cmd
    )


def _is_cs_install(cmd) -> bool:
    """True when *cmd* invokes ``cs install`` (MUST never happen in launch model)."""
    return (
        isinstance(cmd, list)
        and len(cmd) >= 2
        and "install" in cmd
        # Avoid false positives on --no-auto-install being echoed somehow
        and cmd[0] not in ("echo", "printf")
    )


def _is_scalafix_rules_invocation(cmd) -> bool:
    """True when *cmd* is the scalafix per-file ``--rules`` processing call."""
    return isinstance(cmd, list) and "--rules" in cmd


def _fake_which_cs_only(name: str) -> str | None:
    """scalafix-cli absent; cs present at a well-known fake path."""
    if name in ("scalafix-cli", "scalafix"):
        return None
    if name in ("cs", "coursier"):
        return _FAKE_CS
    return None


def _fake_run_smoke_passes(cmd, **kwargs):
    """Smoke-check succeeds (Scalafix vX.Y.Z to stdout); all other calls ok."""
    if _is_smoke_check(cmd):
        return _cp(cmd, 0, stdout=_SMOKE_VERSION_OUTPUT, stderr="")
    return _cp(cmd, 0)


# ---------------------------------------------------------------------------
# Assertion 1 — scalafix on PATH: invocation is [bin], no cs launch
# ---------------------------------------------------------------------------


def test_a1_scalafix_on_path_no_launch_attempted(monkeypatch, state_file):
    """When scalafix-cli is already on PATH the resolver MUST NOT invoke
    ``cs launch``.  The invocation prefix used must be the bare binary path
    (a single-element list), not a launch prefix."""
    monkeypatch.setattr(shutil, "which", lambda name: (
        "/usr/local/bin/scalafix-cli" if name in ("scalafix-cli", "scalafix") else None
    ))

    launch_calls: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "launch" in cmd:
            launch_calls.append(list(cmd))
        return _cp(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file)])

    assert rc == 0, "exit code must be 0"
    assert launch_calls == [], (
        "cs launch MUST NOT be attempted when scalafix-cli is already on PATH"
    )


# ---------------------------------------------------------------------------
# Assertion 2 — scalafix absent + Coursier absent → skip (reason mentions cs)
# ---------------------------------------------------------------------------


def test_a2_no_scalafix_no_coursier_skips(monkeypatch, state_file):
    """All of scalafix-cli / scalafix / cs / coursier absent.
    Must exit 1, status=failed, skip_reason mentions Coursier unavailability
    for *launch* (NOT the stale 'not on PATH' message)."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    # ADJUSTED for bootstrap: stub _bootstrap_coursier so after implementation it
    # returns None (bootstrap unavailable) without attempting a real network download.
    # raising=False avoids AttributeError in the red phase before implementation.
    monkeypatch.setattr(_mod, "_bootstrap_coursier", lambda: None, raising=False)

    rc = _safe_main(["--state", str(state_file)])
    result = _phase_result(state_file)

    assert rc == 1, "exit code must be 1 (hard-gate: no runner available)"
    assert result.get("status") == "failed"

    reason = result.get("skip_reason", "")
    assert "coursier" in reason.lower() or "cs" in reason.lower(), (
        f"skip_reason must mention coursier/cs unavailability to launch; got: {reason!r}"
    )
    # ADJUSTED: after bootstrap implementation the reason may contain "not on PATH" as
    # part of the fuller explanation "(not on PATH and bootstrap failed/disabled)".
    # The positive assertion above (mentioning "coursier"/"cs") already guards against
    # the bare stale "scalafix-cli not on PATH" phrasing — no separate negative needed.


# ---------------------------------------------------------------------------
# Assertion 3a — --no-auto-launch CLI flag
# ---------------------------------------------------------------------------


def test_a3a_no_auto_launch_flag(monkeypatch, state_file):
    """``--no-auto-launch`` must suppress launch entirely.
    Exit 1, status=failed, no cs launch subprocess."""
    monkeypatch.setattr(shutil, "which", lambda _: None)

    launch_calls: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "launch" in cmd:
            launch_calls.append(list(cmd))
        return _cp(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file), "--no-auto-launch"])

    result = _phase_result(state_file)
    assert rc == 1, "exit code must be 1 (hard-gate: no runner available)"
    assert result.get("status") == "failed"
    assert launch_calls == [], "--no-auto-launch must suppress any cs launch subprocess"


# ---------------------------------------------------------------------------
# Assertion 3b — SCOS_SCALAFIX_AUTO_LAUNCH=0 env var
# ---------------------------------------------------------------------------


def test_a3b_env_auto_launch_disabled(monkeypatch, state_file):
    """``SCOS_SCALAFIX_AUTO_LAUNCH=0`` must disable auto-launch, equivalent to
    ``--no-auto-launch``.  Exit 1, status=failed, no launch subprocess.

    cs IS made available via which so the only valid reason to fail is the
    env-var opt-out — not coursier absence.  The skip_reason must reflect
    "auto-launch disabled", proving the env var was actually checked.
    """
    # Provide cs so the code CAN'T fall through to "coursier absent"
    monkeypatch.setattr(shutil, "which", _fake_which_cs_only)
    monkeypatch.setenv("SCOS_SCALAFIX_AUTO_LAUNCH", "0")

    launch_calls: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "launch" in cmd:
            launch_calls.append(list(cmd))
        return _cp(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file)])

    result = _phase_result(state_file)
    assert rc == 1, "exit code must be 1 (hard-gate: no runner available)"
    assert result.get("status") == "failed"
    assert launch_calls == [], (
        "SCOS_SCALAFIX_AUTO_LAUNCH=0 must suppress any cs launch subprocess"
    )
    reason = result.get("skip_reason", "")
    # The reason must reflect that auto-launch was explicitly disabled, not
    # install failure or coursier absence.
    assert "launch" in reason.lower() and (
        "disabled" in reason.lower() or "auto" in reason.lower()
    ), (
        f"skip_reason must mention auto-launch disabled (env var was checked); "
        f"got: {reason!r}"
    )


# ---------------------------------------------------------------------------
# Assertion 3c — deprecated --no-auto-install alias still disables launch
# ---------------------------------------------------------------------------


def test_a3c_deprecated_no_auto_install_flag(monkeypatch, state_file):
    """Deprecated ``--no-auto-install`` flag must still disable auto-launch
    (back-compat).  Exit 1, status=failed, no launch subprocess."""
    monkeypatch.setattr(shutil, "which", lambda _: None)

    launch_calls: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "launch" in cmd:
            launch_calls.append(list(cmd))
        return _cp(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file), "--no-auto-install"])

    result = _phase_result(state_file)
    assert rc == 1, "exit code must be 1 (hard-gate: no runner available)"
    assert result.get("status") == "failed"
    assert launch_calls == [], (
        "--no-auto-install deprecated alias must suppress any cs launch subprocess"
    )


# ---------------------------------------------------------------------------
# Assertion 3d — deprecated SCOS_SCALAFIX_AUTO_INSTALL=0 still disables launch
# ---------------------------------------------------------------------------


def test_a3d_deprecated_env_auto_install_disabled(monkeypatch, state_file):
    """Deprecated ``SCOS_SCALAFIX_AUTO_INSTALL=0`` env var must still disable
    auto-launch (back-compat).  Exit 1, status=failed, no launch subprocess."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setenv("SCOS_SCALAFIX_AUTO_INSTALL", "0")

    launch_calls: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "launch" in cmd:
            launch_calls.append(list(cmd))
        return _cp(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file)])

    result = _phase_result(state_file)
    assert rc == 1, "exit code must be 1 (hard-gate: no runner available)"
    assert result.get("status") == "failed"
    assert launch_calls == [], (
        "SCOS_SCALAFIX_AUTO_INSTALL=0 deprecated alias must suppress cs launch"
    )


# ---------------------------------------------------------------------------
# Assertion 4 — cs present + smoke-check PASSES → no coursier-absence skip
# ---------------------------------------------------------------------------


def test_a4_smoke_check_passes_no_coursier_skip(monkeypatch, state_file):
    """When the smoke-check passes, the phase must NOT record a skip_reason
    that mentions coursier absence or launch failure.

    With an empty manifest the phase ends with status=skipped for
    'no .scala files in manifest' — that is expected and correct.
    The critical assertion is that the coursier/launch error path was NOT taken.
    """
    monkeypatch.setattr(shutil, "which", _fake_which_cs_only)
    monkeypatch.setattr(subprocess, "run", _fake_run_smoke_passes)

    rc = _safe_main(["--state", str(state_file)])
    result = _phase_result(state_file)

    assert rc == 0, "exit code must be 0"

    reason = result.get("skip_reason", "")
    # Must not be skipped because of coursier / launch failure
    assert "coursier" not in reason.lower(), (
        f"After smoke-check pass, skip_reason must not mention coursier absence; "
        f"got: {reason!r}"
    )
    assert "launch" not in reason.lower() or "no .scala" in reason.lower(), (
        f"After smoke-check pass, skip_reason must not mention launch failure; "
        f"got: {reason!r}"
    )
    assert "unavailable" not in reason.lower(), (
        f"After smoke-check pass, must not say unavailable; got: {reason!r}"
    )


# ---------------------------------------------------------------------------
# Assertion 4b — per-file cmd is [cs, "launch", coords, "--", "--rules", ...]
# ---------------------------------------------------------------------------


def test_a4b_per_file_cmd_uses_launch_prefix(monkeypatch, state_file_with_scala):
    """With a .scala file in the manifest and smoke-check passing, every
    scalafix file-processing command must be structured as:

        [cs_bin, "launch", <coords>, "--", "--rules", <conf>, ...]

    The ``--`` separator divides Coursier-launch args from scalafix args.
    """
    captured_cmds: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            captured_cmds.append(list(cmd))
        if _is_smoke_check(cmd):
            return _cp(cmd, 0, stdout=_SMOKE_VERSION_OUTPUT, stderr="")
        return _cp(cmd, 0)

    monkeypatch.setattr(shutil, "which", _fake_which_cs_only)
    monkeypatch.setattr(subprocess, "run", fake_run)

    _safe_main(["--state", str(state_file_with_scala)])

    rule_invocations = [c for c in captured_cmds if _is_scalafix_rules_invocation(c)]
    assert rule_invocations, (
        "Expected at least one '--rules' scalafix invocation but none were captured.\n"
        f"All captured commands: {captured_cmds}"
    )

    cmd = rule_invocations[0]

    # Must start with [cs_bin, "launch", <coords>, "--"]
    assert cmd[0] == _FAKE_CS, (
        f"First element of cmd must be the cs binary path; got: {cmd[0]!r}\n"
        f"Full cmd: {cmd}"
    )
    assert cmd[1] == "launch", (
        f"Second element must be 'launch'; got: {cmd[1]!r}\n"
        f"Full cmd: {cmd}"
    )
    # A "--" separator must appear before scalafix args
    assert "--" in cmd, (
        f"Command must contain '--' separator between coursier and scalafix args; "
        f"got: {cmd!r}"
    )
    dash_idx = cmd.index("--")
    scalafix_args = cmd[dash_idx + 1:]
    assert "--rules" in scalafix_args, (
        f"'--rules' must appear after '--' separator; "
        f"post-'--' args: {scalafix_args!r}\n"
        f"Full cmd: {cmd}"
    )


# ---------------------------------------------------------------------------
# Assertion 5 — smoke-check FAILS → skip, reason mentions cs launch failure
# ---------------------------------------------------------------------------


def test_a5_smoke_check_fails_skips(monkeypatch, state_file):
    """When ``cs launch COORDS -- --version`` returns non-zero, the phase must
    exit 1, record status=failed, and include language about cs launch being
    unable to start scalafix in skip_reason."""
    monkeypatch.setattr(shutil, "which", _fake_which_cs_only)

    def fake_run_smoke_fails(cmd, **kwargs):
        if _is_smoke_check(cmd):
            return _cp(cmd, 1, stdout="", stderr="Error resolving artifact: not found")
        return _cp(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run_smoke_fails)

    rc = _safe_main(["--state", str(state_file)])
    result = _phase_result(state_file)

    assert rc == 1, "exit code must be 1 (hard-gate: no runner available)"
    assert result.get("status") == "failed"

    reason = result.get("skip_reason", "")
    assert "launch" in reason.lower() or "could not start" in reason.lower(), (
        f"skip_reason must mention cs launch failure; got: {reason!r}"
    )
    assert "cs" in reason.lower() or "scalafix" in reason.lower(), (
        f"skip_reason must mention cs or scalafix context; got: {reason!r}"
    )


def test_a5b_smoke_check_bad_output_skips(monkeypatch, state_file):
    """When ``cs launch COORDS -- --version`` returns rc=0 but output contains
    no recognisable version string, the smoke-check is still considered failed
    and the phase exits 1 with status=failed and a reason that mentions the
    smoke/launch/version failure (not a stale install-failure message)."""
    monkeypatch.setattr(shutil, "which", _fake_which_cs_only)

    def fake_run_bad_output(cmd, **kwargs):
        if _is_smoke_check(cmd):
            # rc=0 but output is garbage — no "Scalafix vX.Y.Z" pattern
            return _cp(cmd, 0, stdout="Download complete\n", stderr="")
        return _cp(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run_bad_output)

    rc = _safe_main(["--state", str(state_file)])
    result = _phase_result(state_file)

    assert rc == 1, "exit code must be 1 (hard-gate: no runner available)"
    assert result.get("status") == "failed", (
        "Phase must be failed when smoke-check output contains no version string"
    )
    reason = result.get("skip_reason", "")
    # Must mention smoke-check / launch / version failure — not a generic
    # install-success-but-binary-not-found message from the old model.
    assert any(kw in reason.lower() for kw in ("launch", "smoke", "version", "could not start")), (
        f"skip_reason must reference launch/smoke/version failure; got: {reason!r}"
    )


# ---------------------------------------------------------------------------
# Assertion 6 — no os.environ["PATH"] mutation in the cs-launch path
# ---------------------------------------------------------------------------


def test_a6_no_path_mutation_in_launch_path(tmp_path, monkeypatch, state_file):
    """The cs-launch path must NOT mutate os.environ['PATH'].
    Ephemeral launch is self-contained; no PATH prepend is needed.

    We plant a fake scalafix-cli in COURSIER_BIN_DIR so the old install-model
    code WOULD find it and prepend PATH — proving the new launch model actively
    refrains from PATH mutation even when a binary is discoverable.
    """
    # Create a fake binary that the old cs-install probe would find
    fake_bin_dir = tmp_path / "coursier_bin"
    fake_bin_dir.mkdir()
    fake_binary = fake_bin_dir / "scalafix-cli"
    fake_binary.write_text("#!/bin/sh\necho fake-scalafix\n")
    fake_binary.chmod(0o755)
    monkeypatch.setenv("COURSIER_BIN_DIR", str(fake_bin_dir))

    original_path = os.environ.get("PATH", "")

    monkeypatch.setattr(shutil, "which", _fake_which_cs_only)
    monkeypatch.setattr(subprocess, "run", _fake_run_smoke_passes)

    _safe_main(["--state", str(state_file)])

    assert os.environ.get("PATH", "") == original_path, (
        "os.environ['PATH'] must not be mutated by the cs-launch path; "
        f"before={original_path!r}, after={os.environ.get('PATH', '')!r}"
    )


# ---------------------------------------------------------------------------
# Assertion 6b — cs install must NEVER be invoked in any branch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra_args,which_fn", [
    # scalafix on PATH
    (
        [],
        lambda n: "/bin/scalafix-cli" if n in ("scalafix-cli", "scalafix") else None,
    ),
    # no scalafix, no coursier
    ([], lambda _: None),
    # cs present, smoke passes
    ([], _fake_which_cs_only),
    # --no-auto-launch
    (["--no-auto-launch"], lambda _: None),
], ids=[
    "scalafix-on-path",
    "no-coursier",
    "cs-smoke-passes",
    "no-auto-launch",
])
def test_a6b_cs_install_never_invoked(extra_args, which_fn, monkeypatch, state_file):
    """In every branch the launch model must NEVER invoke ``cs install``."""
    install_calls: list[list] = []

    def fake_run(cmd, **kwargs):
        if _is_cs_install(cmd):
            install_calls.append(list(cmd))
        if _is_smoke_check(cmd):
            return _cp(cmd, 0, stdout=_SMOKE_VERSION_OUTPUT, stderr="")
        return _cp(cmd, 0)

    monkeypatch.setattr(shutil, "which", which_fn)
    # ADJUSTED for bootstrap: prevent real network download in the "no-coursier" case
    # after implementation.  No-op in the red phase (attribute doesn't exist yet).
    monkeypatch.setattr(_mod, "_bootstrap_coursier", lambda: None, raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)

    _safe_main(["--state", str(state_file)] + extra_args)

    assert install_calls == [], (
        f"'cs install' must NEVER be invoked in the launch model; "
        f"got: {install_calls!r}"
    )


# ---------------------------------------------------------------------------
# Assertion 7 — exit code reflects runner availability (parametrised sweep)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra_args,which_fn,smoke_rc,smoke_stdout,expected_rc", [
    # scalafix on PATH → runner resolved → rc=0
    (
        [],
        lambda n: "/bin/scalafix-cli" if n in ("scalafix-cli", "scalafix") else None,
        0,
        "",
        0,
    ),
    # no scalafix, no coursier → no runner → rc=1
    ([], lambda _: None, 0, "", 1),
    # --no-auto-launch → no runner → rc=1
    (["--no-auto-launch"], lambda _: None, 0, "", 1),
    # cs present, smoke passes → runner resolved → rc=0
    ([], _fake_which_cs_only, 0, _SMOKE_VERSION_OUTPUT, 0),
    # cs present, smoke fails (bad rc) → no runner → rc=1
    ([], _fake_which_cs_only, 1, "", 1),
    # cs present, smoke passes but output has no version (bad output) → no runner → rc=1
    ([], _fake_which_cs_only, 0, "Download complete", 1),
], ids=[
    "scalafix-on-path",
    "no-scalafix-no-coursier",
    "no-auto-launch-flag",
    "cs-smoke-passes",
    "cs-smoke-fails-rc",
    "cs-smoke-fails-bad-output",
])
def test_a7_exit_code_reflects_runner_availability(
    extra_args, which_fn, smoke_rc, smoke_stdout, expected_rc, monkeypatch, state_file
):
    """main() exit code reflects runner availability: 1 when no runner is resolved,
    0 when a runner is found (even if ultimately skipped for empty manifest)."""

    def fake_run(cmd, **kwargs):
        if _is_smoke_check(cmd):
            return _cp(cmd, smoke_rc, stdout=smoke_stdout, stderr="")
        return _cp(cmd, 0)

    monkeypatch.setattr(shutil, "which", which_fn)
    # ADJUSTED for bootstrap: prevent real network calls when cs/coursier absent;
    # stub returns None (bootstrap fails) so the phase skips cleanly.  raising=False
    # is safe in the red phase when _bootstrap_coursier is not yet defined.
    monkeypatch.setattr(_mod, "_bootstrap_coursier", lambda: None, raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file)] + extra_args)
    assert rc == expected_rc, (
        f"Expected exit {expected_rc}; got {rc} "
        f"(extra_args={extra_args!r}, smoke_rc={smoke_rc})"
    )


# ---------------------------------------------------------------------------
# Assertion 8 — py_compile passes
# ---------------------------------------------------------------------------


def test_a8_py_compile_passes():
    """scripts/preprocess_scalafix.py must be syntactically valid Python."""
    script = _SCRIPTS_DIR / "preprocess_scalafix.py"
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed for {script}:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Assertion 9 — no stale install wording in docs
# ---------------------------------------------------------------------------

_SKILL_MD = (
    _SKILL_ROOT
    / "migrate-spark-scala-to-snowpark-connect"
    / "SKILL.md"
)
_RECIPES_MD = _SKILL_ROOT / "references" / "scala" / "recipes.md"


def test_a9_skill_md_no_cs_install_scalafix_cli():
    """After the refactor SKILL.md must not contain ``cs install scalafix-cli``.
    Phase 0.5b is now ephemeral launch, not persistent install."""
    if not _SKILL_MD.exists():
        pytest.skip(f"SKILL.md not found at {_SKILL_MD}")
    content = _SKILL_MD.read_text()
    assert "cs install scalafix-cli" not in content, (
        "SKILL.md must not retain 'cs install scalafix-cli' — "
        "Phase 0.5b switched to 'cs launch'"
    )


def test_a9_skill_md_no_auto_install_description():
    """SKILL.md Phase 0.5b must not describe 'auto-install' behaviour.
    The new model is ephemeral cs launch, not cs install."""
    if not _SKILL_MD.exists():
        pytest.skip(f"SKILL.md not found at {_SKILL_MD}")
    content = _SKILL_MD.read_text()
    # The current wording: "automatically installs it via Coursier (cs install scalafix-cli)"
    assert "automatically installs it" not in content, (
        "SKILL.md must not retain 'automatically installs it' — launch model is ephemeral"
    )


def test_a9_skill_md_no_stale_skip_reason_example():
    """SKILL.md must not retain the old literal skip_reason JSON example
    ``"scalafix-cli absent and Coursier … not available to auto-install"``."""
    if not _SKILL_MD.exists():
        pytest.skip(f"SKILL.md not found at {_SKILL_MD}")
    content = _SKILL_MD.read_text()
    assert "auto-install" not in content, (
        "SKILL.md must not retain 'auto-install' wording — replaced by 'auto-launch'"
    )


def test_a9_skill_md_no_no_auto_install_opt_out_docs():
    """SKILL.md opt-out documentation must reference --no-auto-launch /
    SCOS_SCALAFIX_AUTO_LAUNCH, not the old --no-auto-install."""
    if not _SKILL_MD.exists():
        pytest.skip(f"SKILL.md not found at {_SKILL_MD}")
    content = _SKILL_MD.read_text()
    # Old wording: "pass --no-auto-install or set SCOS_SCALAFIX_AUTO_INSTALL=0"
    # as the primary opt-out description (not the back-compat note)
    assert "--no-auto-install" not in content or "deprecated" in content.lower(), (
        "SKILL.md must replace '--no-auto-install' with '--no-auto-launch' as primary "
        "opt-out; deprecated alias may appear only with a deprecation note"
    )


def test_a9_recipes_md_no_stale_install_cell():
    """recipes.md Phase-0.5b table must not describe ``cs install``."""
    if not _RECIPES_MD.exists():
        pytest.skip(f"recipes.md not found at {_RECIPES_MD}")
    content = _RECIPES_MD.read_text()
    assert "auto-installs via `cs install scalafix-cli`" not in content, (
        "recipes.md must not retain 'auto-installs via cs install scalafix-cli' — "
        "now uses cs launch"
    )


def test_a9_recipes_md_no_standalone_install_section():
    """recipes.md must not retain a standalone 'Installing scalafix-cli' section
    that documents ``cs install scalafix-cli`` as the primary install path."""
    if not _RECIPES_MD.exists():
        pytest.skip(f"recipes.md not found at {_RECIPES_MD}")
    content = _RECIPES_MD.read_text()
    assert "Phase 0.5b now runs `cs install scalafix-cli` automatically" not in content, (
        "recipes.md must not retain stale 'Phase 0.5b now runs cs install scalafix-cli "
        "automatically' — changed to cs launch"
    )


# ---------------------------------------------------------------------------
# Assertion 10 — recipe_edits is POPULATED with a real rule name when files
#                are modified (regression for SHOULD-FIX: previously diffs
#                were reconstructed as "@@ -i,1 +i,1 @@" with no trailing rule
#                name, so _parse_patch_hunks never set current_rule and
#                recipe_edits stayed empty)
# ---------------------------------------------------------------------------


def test_a10_recipe_edits_populated_with_rule_name(monkeypatch, state_file_with_scala):
    """When scalafix rewrites a file, recipe_edits must be NON-EMPTY with the
    real rule name attributed.

    The runner applies each rule ONCE over all files in a single in-place
    ``--rules class:<FQCN> <files…>`` invocation (no ``--stdout``); attribution
    is derived from the conf's ``class:`` rule list + a difflib of each file's
    before/after content. The stub simulates scalafix by writing rewritten
    content in place for the ScosCheckpointToCache rule only.
    """
    _RULE = "ScosCheckpointToCache"
    _REWRITTEN = "object Foo { def bar(): Int = 2 /* SCOS */ }\n"

    def fake_run(cmd, **kwargs):
        if _is_smoke_check(cmd):
            return _cp(cmd, 0, stdout=_SMOKE_VERSION_OUTPUT, stderr="")
        # Batched in-place invocation: rewrite the file(s) for the checkpoint
        # rule only (simulating scalafix writing to disk).
        if isinstance(cmd, list) and "--rules" in cmd and "--stdout" not in cmd:
            rules_arg = cmd[cmd.index("--rules") + 1]
            if _RULE in rules_arg:
                for arg in cmd:
                    if isinstance(arg, str) and arg.endswith(".scala"):
                        pathlib.Path(arg).write_text(_REWRITTEN)
        return _cp(cmd, 0)

    monkeypatch.setattr(shutil, "which", _fake_which_cs_only)
    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file_with_scala)])
    assert rc == 0, "exit code must be 0"

    state = json.loads(state_file_with_scala.read_text())
    recipe_edits = state.get("recipe_edits", {})

    assert recipe_edits, (
        "recipe_edits must be non-empty when scalafix reports modifications."
    )

    all_edits = [e for edits in recipe_edits.values() for e in edits]
    assert all_edits, "At least one edit entry must appear in recipe_edits"

    rule_names = {e.get("recipe_id", "") for e in all_edits}
    assert any(_RULE in r for r in rule_names), (
        f"Expected rule name '{_RULE}' in recipe_ids; got: {rule_names!r}."
    )
    # Anchors must follow the scalafix:<Rule>:<line>:<digest> contract.
    anchors = [e.get("output_line_anchor", "") for e in all_edits]
    assert all(a.startswith("scalafix:") for a in anchors), (
        f"All anchors must use the scalafix: namespace; got: {anchors!r}"
    )


# ===========================================================================
# SBT RUNNER TESTS (S-series) — preferred runner via the pinned sbt wrapper
#
# These cover _resolve_sbt_invocation() + its placement (priority #2, after
# PATH scalafix-cli, before Coursier) and the --no-sbt / SCOS_SCALAFIX_USE_SBT
# opt-outs.  sbt is the runner that virtually every Scala dev machine has, so
# it is what makes the phase reliably RUN instead of skipping.
# ===========================================================================

_FAKE_SBT = "/usr/local/bin/sbt"
_FAKE_JAVA = "/usr/bin/java"
# Exported classpath: must contain "scalafix-cli" and an os.pathsep separator.
_FAKE_SBT_CP = f"/r/scalafix-cli.jar{os.pathsep}/r/scalafix-core.jar{os.pathsep}/r/target/classes"


def _fake_which_sbt_and_cs(name: str) -> str | None:
    """scalafix-cli absent; sbt + java + cs all present."""
    if name in ("scalafix-cli", "scalafix"):
        return None
    if name == "sbt":
        return _FAKE_SBT
    if name == "java":
        return _FAKE_JAVA
    if name in ("cs", "coursier"):
        return _FAKE_CS
    return None


def _is_sbt_export(cmd) -> bool:
    return (
        isinstance(cmd, list)
        and cmd
        and cmd[0] == _FAKE_SBT
        and any("fullClasspath" in str(c) for c in cmd)
    )


def _is_cli_main_call(cmd) -> bool:
    return isinstance(cmd, list) and "scalafix.cli.Cli" in cmd


def _fake_run_sbt_ok(cmd, **kwargs):
    """sbt export succeeds; java scalafix.cli.Cli --version reports a version."""
    if _is_sbt_export(cmd):
        return _cp(cmd, 0, stdout=f"{_FAKE_SBT_CP}\n", stderr="")
    if _is_cli_main_call(cmd) and "--version" in cmd:
        return _cp(cmd, 0, stdout="scalafix 0.14.3", stderr="")
    if _is_smoke_check(cmd):  # cs launch smoke — should NOT be reached when sbt wins
        return _cp(cmd, 0, stdout=_SMOKE_VERSION_OUTPUT, stderr="")
    return _cp(cmd, 0)


def test_s1_sbt_runner_preferred_over_coursier(monkeypatch, state_file_with_scala):
    """With sbt + cs both present (scalafix-cli absent), the sbt runner is chosen:
    file processing runs via ``java -cp <cp> scalafix.cli.Cli --rules class:… <files>``
    (one batched in-place invocation per rule, no ``--stdout``) and NO ``cs launch``
    smoke-check is attempted."""
    captured: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            captured.append(list(cmd))
        return _fake_run_sbt_ok(cmd, **kwargs)

    monkeypatch.setattr(shutil, "which", _fake_which_sbt_and_cs)
    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file_with_scala)])
    assert rc == 0

    # Coursier must NOT have been used as the runner.
    assert not any(_is_smoke_check(c) for c in captured), (
        "cs launch smoke-check must not run when sbt is the chosen runner"
    )
    # Per-rule processing is a batched in-place invocation: java + scalafix.cli.Cli
    # + --rules class:… + .scala file arg(s), and explicitly NOT --stdout.
    rule_cmds = [c for c in captured if "--rules" in c]
    assert rule_cmds, f"Expected per-rule batched invocations; captured: {captured}"
    cmd0 = rule_cmds[0]
    assert cmd0[0] == _FAKE_JAVA, f"runner must be java; got {cmd0[0]!r}"
    assert "scalafix.cli.Cli" in cmd0
    assert "--stdout" not in cmd0, "batched runner must not use --stdout"
    rules_arg = cmd0[cmd0.index("--rules") + 1]
    assert rules_arg.startswith("class:"), f"rule must be a class: ref; got {rules_arg!r}"
    assert any(str(a).endswith(".scala") for a in cmd0), (
        f"batched invocation must include .scala file arg(s); got {cmd0!r}"
    )
    # sbt runner bakes rules into the classpath → no --tool-classpath needed.
    assert "--tool-classpath" not in cmd0


def test_s2_no_sbt_flag_falls_back_to_coursier(monkeypatch, state_file):
    """`--no-sbt` disables the sbt runner; with cs present the resolver falls
    back to a cs launch smoke-check."""
    captured: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            captured.append(list(cmd))
        return _fake_run_sbt_ok(cmd, **kwargs)

    monkeypatch.setattr(shutil, "which", _fake_which_sbt_and_cs)
    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file), "--no-sbt"])
    assert rc == 0
    assert not any(_is_sbt_export(c) for c in captured), (
        "sbt export must not run when --no-sbt is set"
    )
    assert any(_is_smoke_check(c) for c in captured), (
        "cs launch smoke-check must run as the fallback when --no-sbt is set"
    )


def test_s3_env_use_sbt_disabled_falls_back(monkeypatch, state_file):
    """SCOS_SCALAFIX_USE_SBT=0 must disable the sbt runner (same as --no-sbt)."""
    captured: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            captured.append(list(cmd))
        return _fake_run_sbt_ok(cmd, **kwargs)

    monkeypatch.setattr(shutil, "which", _fake_which_sbt_and_cs)
    monkeypatch.setenv("SCOS_SCALAFIX_USE_SBT", "0")
    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0
    assert not any(_is_sbt_export(c) for c in captured)
    assert any(_is_smoke_check(c) for c in captured)


def test_s4_sbt_export_failure_falls_back_to_coursier(monkeypatch, state_file):
    """When sbt is present but ``sbt export`` fails, the resolver falls back to
    Coursier rather than skipping."""
    captured: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            captured.append(list(cmd))
        if _is_sbt_export(cmd):
            return _cp(cmd, 1, stdout="", stderr="boom: resolution failed")
        if _is_smoke_check(cmd):
            return _cp(cmd, 0, stdout=_SMOKE_VERSION_OUTPUT, stderr="")
        return _cp(cmd, 0)

    monkeypatch.setattr(shutil, "which", _fake_which_sbt_and_cs)
    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0
    assert any(_is_sbt_export(c) for c in captured), "sbt export should be attempted"
    assert any(_is_smoke_check(c) for c in captured), (
        "must fall back to cs launch when sbt export fails"
    )


def test_s5_sbt_only_no_coursier_resolves(monkeypatch, state_file_with_scala):
    """sbt + java present, Coursier ABSENT, scalafix-cli absent → the phase still
    runs (status == passed), proving sbt closes the 'always skipped' gap."""
    def fake_which(name: str) -> str | None:
        if name == "sbt":
            return _FAKE_SBT
        if name == "java":
            return _FAKE_JAVA
        return None  # no scalafix-cli, no cs/coursier

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", _fake_run_sbt_ok)
    # Ensure bootstrap can't rescue Coursier in this scenario.
    monkeypatch.setattr(_mod, "_bootstrap_coursier", lambda: None)

    rc = _safe_main(["--state", str(state_file_with_scala)])
    result = _phase_result(state_file_with_scala)
    assert rc == 0
    assert result.get("status") == "passed", (
        f"sbt-only environment must run the phase; got: {result!r}"
    )


def test_s6_no_runner_skip_reason_mentions_sbt(monkeypatch, state_file):
    """With no scalafix-cli, no sbt, and no Coursier, the phase exits 1
    (status=failed) and the skip_reason must name all three missing runners
    (incl. sbt)."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(_mod, "_bootstrap_coursier", lambda: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(cmd, 0))

    rc = _safe_main(["--state", str(state_file)])
    result = _phase_result(state_file)
    assert rc == 1, "exit code must be 1 (hard-gate: no runner available)"
    assert result.get("status") == "failed"
    reason = result.get("skip_reason", "").lower()
    assert "sbt" in reason, f"skip_reason must mention sbt; got: {reason!r}"
    assert "coursier" in reason, f"skip_reason must mention Coursier; got: {reason!r}"


# ===========================================================================
# BOOTSTRAP TESTS (B-series) — TDD RED PHASE
#
# These tests cover the new _bootstrap_coursier() helper and the updated
# _resolve_scalafix_invocation() bootstrap gate added in the Coursier-bootstrap
# plan.  ALL B-series tests are expected to FAIL until Phase 3 implementation.
#
# Asset names (confirmed by reviser, msg-642335a8):
#   darwin  arm64/aarch64 → cs-aarch64-apple-darwin.gz
#   darwin  x86_64        → cs-x86_64-apple-darwin.gz
#   linux   x86_64/AMD64  → cs-x86_64-pc-linux.gz
#   linux   aarch64/arm64 → cs-aarch64-pc-linux.gz
# Base URL: https://github.com/coursier/launchers/raw/master/<asset>
# ===========================================================================

_FAKE_BOOTSTRAPPED_CS = "/fake/bootstrapped/cs"

# ---------------------------------------------------------------------------
# B1 — cs absent + bootstrap ENABLED + _bootstrap_coursier returns a path
#      → resolver proceeds; NOT skipped for "coursier unavailable"
# ---------------------------------------------------------------------------


def test_b1_bootstrap_success_resolver_proceeds(monkeypatch, state_file):
    """When cs/coursier are absent from PATH but _bootstrap_coursier returns a
    valid path, the resolver must proceed to the smoke-check using that path.
    The phase must NOT record a skip_reason about coursier being unavailable or
    bootstrap having failed.

    With an empty manifest the phase will ultimately skip for 'no .scala files'
    — that is correct and expected.  The critical assertion is that the
    coursier/bootstrap-failure path was NOT taken.
    """
    # No cs/coursier/scalafix on PATH
    monkeypatch.setattr(shutil, "which", lambda _: None)
    # Bootstrap succeeds: returns a fake cs path
    monkeypatch.setattr(_mod, "_bootstrap_coursier", lambda: _FAKE_BOOTSTRAPPED_CS)

    def fake_run(cmd, **kwargs):
        # Smoke-check against the bootstrapped cs must pass
        if _is_smoke_check(cmd) and cmd[0] == _FAKE_BOOTSTRAPPED_CS:
            return _cp(cmd, 0, stdout=_SMOKE_VERSION_OUTPUT, stderr="")
        return _cp(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file)])
    result = _phase_result(state_file)

    assert rc == 0, "exit code must be 0"
    reason = result.get("skip_reason", "")
    # Must NOT be skipped because coursier is unavailable or bootstrap failed
    assert "not available to launch" not in reason, (
        f"After bootstrap succeeds, must NOT skip with 'not available to launch'; "
        f"got: {reason!r}"
    )
    assert "bootstrap failed" not in reason.lower(), (
        f"After bootstrap succeeds, must NOT say 'bootstrap failed'; got: {reason!r}"
    )
    # Acceptable skip: "no .scala files in manifest" (empty fixture); NOT coursier errors
    assert "coursier" not in reason.lower() or "no .scala" in reason.lower(), (
        f"After bootstrap succeeds, skip_reason must not cite coursier unavailability; "
        f"got: {reason!r}"
    )


# ---------------------------------------------------------------------------
# B2 — cs absent + bootstrap returns None (failure)
#      → exit 1, failed, reason mentions "bootstrap"
# ---------------------------------------------------------------------------


def test_b2_bootstrap_fails_skips_with_bootstrap_reason(monkeypatch, state_file):
    """When cs/coursier are absent and _bootstrap_coursier returns None (failure),
    the phase must exit 1, record status=failed, and include the word 'bootstrap'
    in skip_reason — distinguishing it from a plain coursier-absent message."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    # Bootstrap fails
    monkeypatch.setattr(_mod, "_bootstrap_coursier", lambda: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(cmd, 0))

    rc = _safe_main(["--state", str(state_file)])
    result = _phase_result(state_file)

    assert rc == 1, "exit code must be 1 (hard-gate: no runner available)"
    assert result.get("status") == "failed"
    reason = result.get("skip_reason", "")
    # New reason (from plan): "…Coursier unavailable (not on PATH and bootstrap failed/disabled)"
    assert "bootstrap" in reason.lower(), (
        f"When bootstrap fails, skip_reason must mention 'bootstrap'; got: {reason!r}"
    )


# ---------------------------------------------------------------------------
# B3 — cs absent + --no-bootstrap-coursier flag
#      → _bootstrap_coursier NOT called; exit 1, failed
# ---------------------------------------------------------------------------


def test_b3_no_bootstrap_coursier_flag_skips_without_bootstrap(monkeypatch, state_file):
    """The --no-bootstrap-coursier CLI flag must suppress _bootstrap_coursier entirely.
    Exit 1, status=failed.  _bootstrap_coursier must NOT be called."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    bootstrap_called: list[int] = []
    monkeypatch.setattr(
        _mod,
        "_bootstrap_coursier",
        lambda: (bootstrap_called.append(1) or _FAKE_BOOTSTRAPPED_CS),
    )
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(cmd, 0))

    rc = _safe_main(["--state", str(state_file), "--no-bootstrap-coursier"])
    result = _phase_result(state_file)

    assert rc == 1, (
        "exit code must be 1 (hard-gate: no runner available); "
        "argparse may not recognise --no-bootstrap-coursier yet — implement the flag"
    )
    assert result.get("status") == "failed"
    assert bootstrap_called == [], (
        "_bootstrap_coursier must NOT be called when --no-bootstrap-coursier is passed"
    )


# ---------------------------------------------------------------------------
# B4 — cs absent + SCOS_BOOTSTRAP_COURSIER=0 env var
#      → _bootstrap_coursier NOT called; skip_reason mentions bootstrap disabled
# ---------------------------------------------------------------------------


def test_b4_env_bootstrap_disabled_skips_without_bootstrap(monkeypatch, state_file):
    """SCOS_BOOTSTRAP_COURSIER=0 must disable bootstrap, equivalent to
    --no-bootstrap-coursier.  _bootstrap_coursier must NOT be called and
    the phase exits 1 with status=failed; skip_reason must mention 'bootstrap'
    (proving the env var was checked)."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setenv("SCOS_BOOTSTRAP_COURSIER", "0")
    bootstrap_called: list[int] = []
    # Bootstrap is stubbed to succeed — if called, it would return a path;
    # the test verifies it is NOT called.
    monkeypatch.setattr(
        _mod,
        "_bootstrap_coursier",
        lambda: (bootstrap_called.append(1) or _FAKE_BOOTSTRAPPED_CS),
    )
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(cmd, 0))

    rc = _safe_main(["--state", str(state_file)])
    result = _phase_result(state_file)

    assert rc == 1, "exit code must be 1 (hard-gate: no runner available)"
    assert result.get("status") == "failed"
    assert bootstrap_called == [], (
        "_bootstrap_coursier must NOT be called when SCOS_BOOTSTRAP_COURSIER=0"
    )
    reason = result.get("skip_reason", "")
    # After implementation the reason should mention "bootstrap" (disabled/skipped)
    assert "bootstrap" in reason.lower(), (
        f"skip_reason must mention 'bootstrap' when SCOS_BOOTSTRAP_COURSIER=0; "
        f"got: {reason!r}"
    )


# ---------------------------------------------------------------------------
# B5 — cs ALREADY present → _bootstrap_coursier NOT called
#      (guard test; also verifies the function is defined post-implementation)
# ---------------------------------------------------------------------------


def test_b5_cs_already_present_bootstrap_not_called(monkeypatch, state_file):
    """When cs/coursier is already on PATH, _bootstrap_coursier must NOT be called.
    The assertion that _bootstrap_coursier exists on the module ensures this test
    fails in the red phase (before implementation) and guards regressions after."""
    # Fail fast if the implementation hasn't been added yet
    bootstrap_fn = getattr(_mod, "_bootstrap_coursier", None)
    assert bootstrap_fn is not None, (
        "_bootstrap_coursier must be defined in preprocess_scalafix — "
        "implementation not yet complete (expected red-phase failure)"
    )

    monkeypatch.setattr(shutil, "which", _fake_which_cs_only)
    bootstrap_called: list[int] = []
    monkeypatch.setattr(
        _mod,
        "_bootstrap_coursier",
        lambda: (bootstrap_called.append(1) or _FAKE_BOOTSTRAPPED_CS),
    )
    monkeypatch.setattr(subprocess, "run", _fake_run_smoke_passes)

    rc = _safe_main(["--state", str(state_file)])

    assert rc == 0, "exit code must be 0"
    assert bootstrap_called == [], (
        "_bootstrap_coursier must NOT be called when cs is already on PATH"
    )


# ---------------------------------------------------------------------------
# B6 — exit code reflects runner availability in bootstrap scenarios
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bootstrap_return,smoke_stdout,expected_rc", [
    # cs absent, bootstrap succeeds, smoke passes → runner resolved → rc=0
    (_FAKE_BOOTSTRAPPED_CS, _SMOKE_VERSION_OUTPUT, 0),
    # cs absent, bootstrap fails → no runner → rc=1
    (None, "", 1),
], ids=[
    "bootstrap-succeeds",
    "bootstrap-fails",
])
def test_b6_bootstrap_called_and_exit_reflects_runner(
    bootstrap_return, smoke_stdout, expected_rc, monkeypatch, state_file
):
    """_bootstrap_coursier must be called when cs is absent; exit code reflects
    runner availability: 1 when bootstrap fails (no runner resolved), 0 when
    bootstrap succeeds and the smoke-check passes."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    bootstrap_calls: list[object] = []

    def fake_bootstrap() -> str | None:
        bootstrap_calls.append(bootstrap_return)
        return bootstrap_return

    monkeypatch.setattr(_mod, "_bootstrap_coursier", fake_bootstrap)

    def fake_run(cmd, **kwargs):
        if _is_smoke_check(cmd) and bootstrap_return is not None:
            return _cp(cmd, 0, stdout=smoke_stdout, stderr="")
        return _cp(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = _safe_main(["--state", str(state_file)])

    assert rc == expected_rc, f"exit code must be {expected_rc}; got {rc}"
    assert bootstrap_calls, (
        "_bootstrap_coursier must have been called when cs absent and bootstrap enabled"
    )


def test_b6_no_bootstrap_flag_exits_one(monkeypatch, state_file):
    """--no-bootstrap-coursier with no runner available must produce exit 1 (hard-gate)."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(cmd, 0))

    rc = _safe_main(["--state", str(state_file), "--no-bootstrap-coursier"])
    assert rc == 1, (
        f"exit code must be 1 (no runner available) with --no-bootstrap-coursier; got {rc}"
    )


# ---------------------------------------------------------------------------
# B7 — _bootstrap_coursier asset URL mapping (offline unit tests)
#      Stubs sys.platform, platform.machine, and urllib.request.urlopen.
#      NO real network I/O.
# ---------------------------------------------------------------------------


def test_b7a_bootstrap_coursier_url_darwin_arm64(monkeypatch, tmp_path):
    """_bootstrap_coursier must request cs-aarch64-apple-darwin.gz for macOS arm64.

    Reviser confirmed (msg-642335a8): asset name cs-aarch64-apple-darwin.gz is
    present in the launchers repo and the base URL
    https://github.com/coursier/launchers/raw/master/<asset> is correct.
    """
    import platform as _platform
    import urllib.request as _urllib_req

    bootstrap_fn = getattr(_mod, "_bootstrap_coursier", None)
    assert bootstrap_fn is not None, (
        "_bootstrap_coursier must be defined in preprocess_scalafix — "
        "expected red-phase failure"
    )

    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(_platform, "machine", lambda: "arm64")
    # Redirect cache writes to tmp_path so we don't pollute ~/.cache
    monkeypatch.setenv("HOME", str(tmp_path))

    captured_urls: list[str] = []

    def fake_urlopen(url, timeout=None):
        captured_urls.append(str(url))
        raise OSError("stubbed urlopen — no network in tests")

    monkeypatch.setattr(_urllib_req, "urlopen", fake_urlopen)

    result = bootstrap_fn()

    assert result is None, (
        "bootstrap must return None when the download raises (best-effort)"
    )
    assert len(captured_urls) == 1, (
        f"Expected exactly one urlopen call; got: {captured_urls}"
    )
    url = captured_urls[0]
    assert "aarch64-apple-darwin" in url, (
        f"darwin arm64 must request cs-aarch64-apple-darwin.gz; got URL: {url!r}\n"
        "Plan: darwin+arm64/aarch64 → cs-aarch64-apple-darwin.gz"
    )
    assert "coursier" in url.lower(), (
        f"URL must point to the coursier launchers repo; got: {url!r}"
    )


def test_b7b_bootstrap_coursier_url_linux_x86_64(monkeypatch, tmp_path):
    """_bootstrap_coursier must request cs-x86_64-pc-linux.gz for Linux x86_64.

    Reviser note (msg-642335a8): platform.machine() can return 'AMD64' (uppercase)
    on some Linux environments.  Implementation should normalise with .lower() or
    add an explicit 'AMD64' case.  Both 'x86_64' and 'AMD64' are tested here.
    """
    import platform as _platform
    import urllib.request as _urllib_req

    bootstrap_fn = getattr(_mod, "_bootstrap_coursier", None)
    assert bootstrap_fn is not None, (
        "_bootstrap_coursier must be defined in preprocess_scalafix — "
        "expected red-phase failure"
    )

    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "machine", lambda: "x86_64")
    monkeypatch.setenv("HOME", str(tmp_path))

    captured_urls: list[str] = []

    def fake_urlopen(url, timeout=None):
        captured_urls.append(str(url))
        raise OSError("stubbed urlopen — no network in tests")

    monkeypatch.setattr(_urllib_req, "urlopen", fake_urlopen)

    result = bootstrap_fn()

    assert result is None, "bootstrap must return None when download raises"
    assert len(captured_urls) == 1, (
        f"Expected exactly one urlopen call; got: {captured_urls}"
    )
    url = captured_urls[0]
    assert "x86_64-pc-linux" in url, (
        f"linux x86_64 must request cs-x86_64-pc-linux.gz; got URL: {url!r}\n"
        "Plan: linux+x86_64 → cs-x86_64-pc-linux.gz"
    )
    assert "coursier" in url.lower(), (
        f"URL must point to the coursier launchers repo; got: {url!r}"
    )


def test_b7c_bootstrap_coursier_url_linux_amd64_uppercase(monkeypatch, tmp_path):
    """_bootstrap_coursier must handle platform.machine() == 'AMD64' (uppercase).

    Reviser flagged (msg-642335a8, SHOULD-ADDRESS A): some Linux environments
    return 'AMD64' instead of 'x86_64'.  Implementation must normalise.
    The expected asset is still cs-x86_64-pc-linux.gz.
    """
    import platform as _platform
    import urllib.request as _urllib_req

    bootstrap_fn = getattr(_mod, "_bootstrap_coursier", None)
    assert bootstrap_fn is not None, (
        "_bootstrap_coursier must be defined in preprocess_scalafix — "
        "expected red-phase failure"
    )

    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_platform, "machine", lambda: "AMD64")  # uppercase variant
    monkeypatch.setenv("HOME", str(tmp_path))

    captured_urls: list[str] = []

    def fake_urlopen(url, timeout=None):
        captured_urls.append(str(url))
        raise OSError("stubbed urlopen — no network in tests")

    monkeypatch.setattr(_urllib_req, "urlopen", fake_urlopen)

    result = bootstrap_fn()

    assert result is None, "bootstrap must return None when download raises"
    assert len(captured_urls) == 1, (
        f"Expected exactly one urlopen call; got: {captured_urls}"
    )
    url = captured_urls[0]
    assert "x86_64-pc-linux" in url, (
        f"Linux AMD64 (uppercase) must still request cs-x86_64-pc-linux.gz; "
        f"got URL: {url!r}\n"
        "Implementation must normalise machine string with .lower() or explicit case"
    )


# ===========================================================================
# BATCH TESTS (#8) — one scalafix launch per rule, not per (rule, file)
# ===========================================================================


def _multi_scala_state(tmp_path, names: list[str]):
    """Build a state file with several .scala files in the migrated dir."""
    migrated = tmp_path / "migrated"
    migrated.mkdir()
    for n in names:
        (migrated / n).write_text("object X { def f(): Int = 1 }\n")
    state = {"migrated_dir": str(migrated), "manifest": list(names)}
    p = tmp_path / "migration_state.json"
    p.write_text(json.dumps(state))
    return p, migrated


def test_batch_one_launch_per_rule_not_per_file(monkeypatch, tmp_path):
    """The core #8 win: each rule runs ONCE over all files in a single batched
    invocation, so launches scale with rule-count, not rule-count × file-count."""
    state_file, _ = _multi_scala_state(tmp_path, ["A.scala", "B.scala", "C.scala"])
    captured: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            captured.append(list(cmd))
        if _is_smoke_check(cmd):
            return _cp(cmd, 0, stdout=_SMOKE_VERSION_OUTPUT, stderr="")
        return _cp(cmd, 0)  # rule batch: exit 0, writes nothing

    monkeypatch.setattr(shutil, "which", _fake_which_cs_only)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _safe_main(["--state", str(state_file)]) == 0

    rule_cmds = [c for c in captured if "--rules" in c]
    assert rule_cmds, "expected at least one --rules invocation"
    # Each rule invocation must carry ALL three files (batched), not one.
    for c in rule_cmds:
        scala_args = [a for a in c if str(a).endswith(".scala")]
        assert len(scala_args) == 3, f"rule cmd must batch all 3 files; got {scala_args}"
    # Exactly one invocation per distinct rule — no per-file multiplication.
    rules_used = [c[c.index("--rules") + 1] for c in rule_cmds]
    assert len(rule_cmds) == len(set(rules_used)), (
        f"expected one launch per rule; got {len(rule_cmds)} for rules {set(rules_used)}"
    )
    # No --stdout in the batched path.
    assert all("--stdout" not in c for c in rule_cmds)


def test_batch_failure_falls_back_to_per_file_stdout(monkeypatch, tmp_path):
    """When the batched invocation fails, the runner falls back to per-file
    ``--stdout`` so a single bad file can't suppress the rule everywhere."""
    state_file, migrated = _multi_scala_state(tmp_path, ["A.scala", "B.scala"])
    _REWRITTEN = "object X { def f(): Int = 2 /* SCOS */ }\n"
    captured: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            captured.append(list(cmd))
        if _is_smoke_check(cmd):
            return _cp(cmd, 0, stdout=_SMOKE_VERSION_OUTPUT, stderr="")
        if isinstance(cmd, list) and "--rules" in cmd:
            scala_args = [a for a in cmd if str(a).endswith(".scala")]
            if "--stdout" in cmd:
                # Per-file fallback: rewrite this single file.
                return _cp(cmd, 0, stdout=_REWRITTEN, stderr="")
            if len(scala_args) > 1:
                # Batched invocation → simulate a batch failure.
                return _cp(cmd, 1, stdout="", stderr="boom")
        return _cp(cmd, 0)

    monkeypatch.setattr(shutil, "which", _fake_which_cs_only)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _safe_main(["--state", str(state_file)]) == 0

    # Fallback per-file --stdout invocations must have been attempted.
    assert any("--stdout" in c for c in captured), (
        "batch failure must trigger per-file --stdout fallback"
    )
    # Attribution still works through the fallback.
    state = json.loads(state_file.read_text())
    assert state.get("recipe_edits"), "fallback must still populate recipe_edits"


def test_batch_idempotency_skips_already_processed(monkeypatch, tmp_path):
    """A file already carrying scalafix: edits is excluded from every rule batch."""
    state_file, migrated = _multi_scala_state(tmp_path, ["Done.scala", "New.scala"])
    state = json.loads(state_file.read_text())
    state["recipe_edits"] = {
        "Done.scala": [{"recipe_id": "scalafix:ScosCheckpointToCache",
                        "src_line": 1, "output_line_anchor": "scalafix:ScosCheckpointToCache:1:deadbeef"}]
    }
    state_file.write_text(json.dumps(state))
    captured: list[list] = []

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            captured.append(list(cmd))
        if _is_smoke_check(cmd):
            return _cp(cmd, 0, stdout=_SMOKE_VERSION_OUTPUT, stderr="")
        return _cp(cmd, 0)

    monkeypatch.setattr(shutil, "which", _fake_which_cs_only)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _safe_main(["--state", str(state_file)]) == 0

    rule_cmds = [c for c in captured if "--rules" in c]
    assert rule_cmds, "expected rule invocations for the un-processed file"
    done_path = str((migrated / "Done.scala"))
    new_path = str((migrated / "New.scala"))
    for c in rule_cmds:
        assert done_path not in c, "already-processed file must not be re-batched"
    assert any(new_path in c for c in rule_cmds), "the new file must be processed"
