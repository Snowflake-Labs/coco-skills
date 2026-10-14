"""Tests for revert_failing_scala_files.py — the Phase 2b compilation gate.

Covers the pure tokenizer fallback (`_check_with_fallback`) and the batch-first
sweep control flow (`_run_sweep`). The sweep tests monkeypatch the scalac and
git helpers so they run without a JVM or a git repo, asserting:
  - batch pass  -> no per-file checks, no reverts (the speedup path)
  - batch fail  -> per-file attribution + revert of exactly the failing files
  - no scalac   -> per-file tokenizer path
"""

from __future__ import annotations

from pathlib import Path

import pytest

import revert_failing_scala_files as rv


# --- tokenizer fallback (pure, no JVM) -------------------------------------


@pytest.mark.parametrize("src", [
    "object M { def f(): Int = 1 }",
    'val s = "a string with { unbalanced brace"',          # brace inside string
    "// a comment with ) paren\nobject M {}",               # paren in line comment
    '/* block } comment */ object M { val x = (1, 2) }',    # brace in block comment
    'val t = """triple { quoted } string"""',               # braces in triple-quote
    "val c = '{'",                                          # char literal brace
])
def test_fallback_accepts_balanced(src):
    assert rv._check_with_fallback(src) is True


@pytest.mark.parametrize("src", [
    "object M { def f(): Int = 1 ",      # missing closing brace
    "def f() = (1, 2",                    # missing closing paren
    "val x = arr[0",                      # missing closing bracket
    'val s = "unterminated string',       # unclosed string
    "/* unterminated block comment",      # unclosed block comment
])
def test_fallback_rejects_unbalanced(src):
    assert rv._check_with_fallback(src) is False


# --- _scalac_cmd construction ----------------------------------------------


def test_scalac_cmd_typer_mode_with_classpath():
    cmd = rv._scalac_cmd([Path("A.scala"), Path("B.scala")], Path("/jars/x.jar"), "/tmp/out")
    assert "-Ystop-after:typer" in cmd
    assert "-classpath" in cmd and "/jars/x.jar" in cmd
    assert cmd[-2:] == ["A.scala", "B.scala"]


def test_scalac_cmd_parse_mode_without_classpath():
    cmd = rv._scalac_cmd([Path("A.scala")], None, "/tmp/out")
    assert "-Ystop-after:parser" in cmd
    assert "-nobootcp" in cmd
    assert "-classpath" not in cmd


# --- _run_sweep control flow -----------------------------------------------


def _make_tree(tmp_path: Path, names: list[str]) -> Path:
    migrated = tmp_path / "Output"
    migrated.mkdir()
    for n in names:
        (migrated / n).write_text("object M {}\n", encoding="utf-8")
    return migrated


def test_sweep_batch_pass_skips_per_file(tmp_path, monkeypatch):
    migrated = _make_tree(tmp_path, ["A.scala", "B.scala"])
    monkeypatch.setattr(rv, "_batch_scalac_passes", lambda files, cp, prefix=("scalac",): True)
    # Per-file check must NOT be called when the batch passes.
    monkeypatch.setattr(rv, "_check_with_scalac",
                        lambda *a, **k: pytest.fail("per-file check should be skipped"))
    monkeypatch.setattr(rv, "_git_revert", lambda *a, **k: pytest.fail("no revert expected"))

    failures, reverted, strategy, quarantined, diagnostics = rv._run_sweep(migrated, True, None, "phase-1-complete", True)
    assert failures == [] and reverted == []
    assert strategy == "batch"


def test_sweep_batch_fail_attributes_per_file(tmp_path, monkeypatch):
    migrated = _make_tree(tmp_path, ["Good.scala", "Bad.scala"])
    monkeypatch.setattr(rv, "_batch_scalac_passes", lambda files, cp, prefix=("scalac",): False)
    # Only Bad.scala fails the per-file check.
    monkeypatch.setattr(rv, "_check_with_scalac",
                        lambda fp, cp=None, prefix=("scalac",), extra_sources=None: (fp.name != "Bad.scala", "boom"))
    reverts: list[str] = []
    def _fake_revert(mig, fp, tag):
        reverts.append(fp.name)
        return True
    monkeypatch.setattr(rv, "_git_revert", _fake_revert)

    failures, reverted, strategy, quarantined, diagnostics = rv._run_sweep(migrated, True, None, "phase-1-complete", True)
    assert failures == ["Bad.scala"]
    assert reverted == ["Bad.scala"]
    assert reverts == ["Bad.scala"]
    assert strategy == "per_file"


def test_sweep_batch_none_falls_back(tmp_path, monkeypatch):
    migrated = _make_tree(tmp_path, ["A.scala"])
    monkeypatch.setattr(rv, "_batch_scalac_passes", lambda files, cp, prefix=("scalac",): None)  # couldn't run
    monkeypatch.setattr(rv, "_check_with_scalac", lambda fp, cp=None, prefix=("scalac",), extra_sources=None: (True, ""))  # all pass per-file
    monkeypatch.setattr(rv, "_git_revert", lambda *a, **k: True)
    failures, reverted, strategy, quarantined, diagnostics = rv._run_sweep(migrated, True, None, "phase-1-complete", True)
    assert failures == [] and strategy == "per_file"


def test_sweep_no_scalac_uses_tokenizer(tmp_path, monkeypatch):
    migrated = tmp_path / "Output"
    migrated.mkdir()
    (migrated / "Good.scala").write_text("object M { val x = 1 }\n", encoding="utf-8")
    (migrated / "Bad.scala").write_text("object M { val x = 1 \n", encoding="utf-8")  # missing }
    # Batch must not be consulted when scalac is unavailable.
    monkeypatch.setattr(rv, "_batch_scalac_passes",
                        lambda *a, **k: pytest.fail("batch should not run without scalac"))
    monkeypatch.setattr(rv, "_git_revert", lambda mig, fp, tag: True)

    failures, reverted, strategy, quarantined, diagnostics = rv._run_sweep(migrated, False, None, "phase-1-complete", True)
    assert failures == ["Bad.scala"]
    assert strategy == "per_file"


def test_sweep_empty_tree(tmp_path):
    migrated = tmp_path / "Output"
    migrated.mkdir()
    failures, reverted, strategy, quarantined, diagnostics = rv._run_sweep(migrated, True, None, "phase-1-complete", True)
    assert failures == [] and reverted == [] and strategy == "none"


def test_batch_empty_file_list_passes():
    assert rv._batch_scalac_passes([], None) is True


# --- R2: scalac resolution, smoke gate, and --require-type-check ------------

import subprocess as _sp  # noqa: E402


def test_resolve_scalac_prefers_path(monkeypatch):
    monkeypatch.setattr(rv.shutil, "which", lambda name: "/usr/bin/scalac" if name == "scalac" else None)
    assert rv._resolve_scalac() == ["scalac"]


def test_resolve_scalac_none_without_coursier(monkeypatch):
    # No scalac, no opt-in → None (identical to legacy behavior, no network).
    monkeypatch.setattr(rv.shutil, "which", lambda name: None)
    assert rv._resolve_scalac(allow_coursier=False) is None


def test_resolve_scalac_uses_coursier_when_opted_in(monkeypatch):
    def _which(name):
        return "/opt/cs" if name in ("cs", "coursier") else None
    monkeypatch.setattr(rv.shutil, "which", _which)
    prefix = rv._resolve_scalac("2.12.20", allow_coursier=True)
    assert prefix == ["/opt/cs", "launch", "scalac:2.12.20", "--"]


def test_smoke_scalac_false_when_binary_missing():
    # A bogus prefix raises FileNotFoundError inside subprocess.run → False.
    assert rv._smoke_scalac(["definitely-not-a-real-compiler-xyz"]) is False


def test_parse_classpath_single_path(tmp_path):
    jar = tmp_path / "client.jar"
    jar.write_text("", encoding="utf-8")
    out = rv._parse_classpath_arg(str(jar))
    assert isinstance(out, Path) and out == jar.resolve()


def test_parse_classpath_pathsep_string_verbatim():
    import os as _os
    cp = _os.pathsep.join(["/a/x.jar", "/b/y.jar"])
    out = rv._parse_classpath_arg(cp)
    assert out == cp  # multi-entry string used verbatim, not Path-wrapped


def test_parse_classpath_at_file(tmp_path):
    import os as _os
    cp = _os.pathsep.join(["/a/x.jar", "/b/y.jar"])
    f = tmp_path / "cp.txt"
    f.write_text(cp + "\n", encoding="utf-8")
    out = rv._parse_classpath_arg("@" + str(f))
    assert out == cp  # trailing newline stripped


def test_resolve_scos_classpath_none_without_coursier(monkeypatch):
    # No cs available and bootstrap disabled → None (no network, safe fallback).
    monkeypatch.setattr(rv, "_resolve_cs", lambda allow_coursier=False: None)
    assert rv._resolve_scos_classpath(allow_coursier=False) is None


def _git_repo_with_tag(tmp_path: Path) -> Path:
    """A minimal git repo containing one balanced .scala file tagged phase-1-complete."""
    migrated = tmp_path / "Output"
    migrated.mkdir()
    (migrated / "M.scala").write_text("object M { val x = 1 }\n", encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ.get("PATH", "")}
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "commit", "-qm", "init"], ["git", "tag", "phase-1-complete"]):
        _sp.run(cmd, cwd=migrated, env=env, check=True, capture_output=True)
    return migrated


def test_require_type_check_fails_when_degraded(tmp_path, monkeypatch):
    migrated = _git_repo_with_tag(tmp_path)
    # Force degraded (no scalac) so type_check is impossible.
    monkeypatch.setattr(rv, "_resolve_scalac", lambda *a, **k: None)
    rc = rv.main(["--migrated", str(migrated), "--require-type-check", "--json"])
    assert rc == 3


def test_no_scalac_runs_tokenizer_and_passes(tmp_path, monkeypatch):
    migrated = _git_repo_with_tag(tmp_path)
    monkeypatch.setattr(rv, "_resolve_scalac", lambda *a, **k: None)
    rc = rv.main(["--migrated", str(migrated), "--json"])
    assert rc == 0  # balanced file passes tokenizer; no reverts


def test_smoke_failure_degrades_to_tokenizer(tmp_path, monkeypatch):
    migrated = _git_repo_with_tag(tmp_path)
    # Resolver returns a prefix, but it fails the smoke test → degrade to tokenizer.
    monkeypatch.setattr(rv, "_resolve_scalac", lambda *a, **k: ["scalac"])
    monkeypatch.setattr(rv, "_smoke_scalac", lambda *a, **k: False)
    # With --require-type-check this must fail (we degraded), proving the gate engaged.
    rc = rv.main(["--migrated", str(migrated), "--require-type-check", "--json"])
    assert rc == 3
    # Without the flag it silently runs tokenizer and passes the balanced file.
    rc = rv.main(["--migrated", str(migrated), "--json"])
    assert rc == 0


# --- R1: Bucket-A quarantine (unsupported RDD, manual refactor) --------------

_MANUAL_MARKER = (
    "// EWI: SPRKCNTSCL1500 => RDD API '.rdd.getNumPartitions' is not supported in\n"
    "// Snowpark Connect; manual refactor required.\n"
)


def test_manual_rdd_marker_detected(tmp_path):
    f = tmp_path / "M.scala"
    f.write_text(_MANUAL_MARKER + "object M { println(df.rdd.getNumPartitions) }\n", encoding="utf-8")
    assert rv._has_manual_rdd_marker(f) is True


def test_convertible_parallelize_annotation_not_quarantined(tmp_path):
    # The recipe's convertible annotation ("Convert to spark.createDataFrame")
    # must NOT be treated as a manual-quarantine marker.
    f = tmp_path / "C.scala"
    f.write_text(
        "// SCOS: [SPRKCNTSCL1500] sc.parallelize is unsupported in Snowpark Connect. "
        "Convert to spark.createDataFrame.\nobject C {}\n",
        encoding="utf-8",
    )
    assert rv._has_manual_rdd_marker(f) is False


def test_sweep_quarantines_marked_file_instead_of_reverting(tmp_path, monkeypatch):
    migrated = tmp_path / "Output"
    migrated.mkdir()
    # Good.scala passes; Manual.scala fails BUT carries the manual RDD marker.
    (migrated / "Good.scala").write_text("object G { val x = 1 }\n", encoding="utf-8")
    (migrated / "Manual.scala").write_text(
        _MANUAL_MARKER + "object Manual { val x = 1 \n",  # unbalanced → tokenizer fail
        encoding="utf-8",
    )
    # Tokenizer path (scalac_ok=False); revert must never be called for the marked file.
    monkeypatch.setattr(rv, "_git_revert",
                        lambda *a, **k: pytest.fail("quarantined file must not be reverted"))
    failures, reverted, strategy, quarantined, diagnostics = rv._run_sweep(
        migrated, False, None, "phase-1-complete", True
    )
    assert failures == []                       # not counted as a gate failure
    assert quarantined == ["Manual.scala"]      # routed to manual bucket
    assert reverted == []


# --- P3: compiler-feedback diagnostics + --no-revert diagnose mode ----------

def test_diagnostics_captured_for_failures(tmp_path):
    migrated = tmp_path / "Output"
    migrated.mkdir()
    (migrated / "Bad.scala").write_text("object B { val x = 1 \n", encoding="utf-8")  # missing }
    # Diagnose (no_revert) so no git is needed; tokenizer path yields a diagnostic.
    failures, reverted, strategy, quarantined, diagnostics = rv._run_sweep(
        migrated, False, None, "phase-1-complete", True, no_revert=True
    )
    assert failures == ["Bad.scala"]
    assert reverted == []
    assert "Bad.scala" in diagnostics and "tokenizer" in diagnostics["Bad.scala"]


def test_no_revert_never_calls_git_revert(tmp_path, monkeypatch):
    migrated = tmp_path / "Output"
    migrated.mkdir()
    (migrated / "Bad.scala").write_text("object B { \n", encoding="utf-8")  # unbalanced
    monkeypatch.setattr(rv, "_git_revert",
                        lambda *a, **k: pytest.fail("no_revert mode must not revert"))
    failures, reverted, strategy, quarantined, diagnostics = rv._run_sweep(
        migrated, False, None, "phase-1-complete", True, no_revert=True
    )
    assert failures == ["Bad.scala"] and reverted == []


def test_main_no_revert_skips_tag_requirement(tmp_path):
    # Diagnose mode must work without a git repo / phase tag (it never reverts).
    migrated = tmp_path / "Output"
    migrated.mkdir()
    (migrated / "Bad.scala").write_text("object B { val x = 1 \n", encoding="utf-8")
    rc = rv.main(["--migrated", str(migrated), "--no-revert", "--json"])
    assert rc == 1  # one failing file reported, no tag error (exit 2), no revert
