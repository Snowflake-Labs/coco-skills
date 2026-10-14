"""Tests for patch_engine.py — literal, regex, and glob patch modes.

Run: uv run --project <skill>/.. python -m pytest scripts/tests/ -q
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness"))
import patch_engine  # noqa: E402
import validate  # noqa: E402
import notebook_source  # noqa: E402


def _setup_conv(tmp_path, files: dict[str, str]):
    """Create a minimal conv_root with Validation/source/<f> and Output/<f>."""
    for rel, content in files.items():
        src = tmp_path / "Validation" / "source" / rel
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(content)
        out = tmp_path / "Output" / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
    (tmp_path / "Validation" / "shared").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Literal patch (regression)
# ---------------------------------------------------------------------------


def test_literal_patch(tmp_path):
    _setup_conv(tmp_path, {"a.py": "x = 1\ny = 2\n"})
    ok, results, written, deduped = patch_engine.add_patches(tmp_path, [
        {"id": "p1", "relative_file": "a.py", "search": "x = 1", "replace": "x = 42"}
    ])
    assert ok
    assert all(r.ok for r in results)
    assert (tmp_path / "Validation" / "source" / "a.py").read_text() == "x = 42\ny = 2\n"
    assert (tmp_path / "Output" / "a.py").read_text() == "x = 42\ny = 2\n"


# ---------------------------------------------------------------------------
# Regex mode
# ---------------------------------------------------------------------------


def test_regex_single_match(tmp_path):
    _setup_conv(tmp_path, {"b.py": "result = foo(123)\n"})
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "r1", "relative_file": "b.py", "regex": True,
         "search": r"foo\(\d+\)", "replace": "bar(0)"}
    ])
    assert ok
    assert (tmp_path / "Output" / "b.py").read_text() == "result = bar(0)\n"


def test_regex_replace_all(tmp_path):
    _setup_conv(tmp_path, {"c.py": "a = f(1)\nb = f(2)\nc = f(3)\n"})
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "r2", "relative_file": "c.py", "regex": True, "replace_all": True,
         "search": r"f\(\d+\)", "replace": "g(0)"}
    ])
    assert ok
    assert (tmp_path / "Output" / "c.py").read_text() == "a = g(0)\nb = g(0)\nc = g(0)\n"


def test_regex_backreference(tmp_path):
    _setup_conv(tmp_path, {"d.py": "val = compute(42, 'hello')\n"})
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "r3", "relative_file": "d.py", "regex": True,
         "search": r"compute\((\d+), '(\w+)'\)", "replace": r"result(\2, \1)"}
    ])
    assert ok
    assert (tmp_path / "Output" / "d.py").read_text() == "val = result(hello, 42)\n"


def test_regex_invalid_pattern(tmp_path):
    _setup_conv(tmp_path, {"e.py": "x = 1\n"})
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "r4", "relative_file": "e.py", "regex": True,
         "search": r"(unclosed", "replace": ""}
    ])
    assert not ok
    assert "invalid regex" in results[-1].error


def test_regex_ambiguous_no_replace_all(tmp_path):
    _setup_conv(tmp_path, {"f.py": "a = f(1)\nb = f(2)\n"})
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "r5", "relative_file": "f.py", "regex": True,
         "search": r"f\(\d+\)", "replace": "g(0)"}
    ])
    assert not ok
    assert "ambiguous" in results[-1].error


def test_regex_ast_parse_failure(tmp_path):
    _setup_conv(tmp_path, {"g.py": "if True:\n    x = 1\n"})
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "r6", "relative_file": "g.py", "regex": True,
         "search": r"x = 1", "replace": ""}
    ])
    assert not ok
    assert "no longer parses" in results[-1].error


# ---------------------------------------------------------------------------
# Glob relative_file
# ---------------------------------------------------------------------------


def test_glob_multiple_files(tmp_path):
    _setup_conv(tmp_path, {
        "src/a.py": "x = old\n",
        "src/b.py": "y = old\n",
        "src/c.txt": "z = old\n",  # not .py
    })
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "g1", "relative_file": "src/*.py",
         "search": "old", "replace": "new"}
    ])
    assert ok
    assert (tmp_path / "Output" / "src" / "a.py").read_text() == "x = new\n"
    assert (tmp_path / "Output" / "src" / "b.py").read_text() == "y = new\n"
    # c.txt not matched by glob
    assert (tmp_path / "Output" / "src" / "c.txt").read_text() == "z = old\n"


def test_glob_skips_files_no_match(tmp_path):
    _setup_conv(tmp_path, {
        "src/a.py": "x = old\n",
        "src/b.py": "y = something_else\n",
    })
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "g2", "relative_file": "src/*.py",
         "search": "old", "replace": "new"}
    ])
    assert ok
    assert (tmp_path / "Output" / "src" / "a.py").read_text() == "x = new\n"
    # b.py was skipped (no match), unchanged
    assert (tmp_path / "Output" / "src" / "b.py").read_text() == "y = something_else\n"


def test_glob_zero_total_matches_fails(tmp_path):
    _setup_conv(tmp_path, {
        "src/a.py": "x = 1\n",
        "src/b.py": "y = 2\n",
    })
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "g3", "relative_file": "src/*.py",
         "search": "NONEXISTENT", "replace": "new"}
    ])
    assert not ok
    assert "search not found in any file" in results[-1].error


def test_glob_with_per_side_block_errors(tmp_path):
    _setup_conv(tmp_path, {"src/a.py": "x = 1\n"})
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "g4", "relative_file": "src/*.py",
         "source": {"search": "x", "replace": "y"},
         "search": "x", "replace": "y"}
    ])
    assert not ok
    assert "glob relative_file does not support per-side" in results[-1].error


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def test_dedup_identical_regex(tmp_path):
    _setup_conv(tmp_path, {"h.py": "a = f(1)\n"})
    ok, results, written, deduped = patch_engine.add_patches(tmp_path, [
        {"id": "d1", "relative_file": "h.py", "regex": True,
         "search": r"f\(1\)", "replace": "g(1)"},
        {"id": "d2", "relative_file": "h.py", "regex": True,
         "search": r"f\(1\)", "replace": "g(1)"},
    ])
    assert ok
    assert "d2" in deduped


def test_literal_vs_regex_not_deduped(tmp_path):
    """A literal and regex entry with the same search text are different signatures."""
    _setup_conv(tmp_path, {"i.py": "x = abc\n"})
    ok, results, written, deduped = patch_engine.add_patches(tmp_path, [
        {"id": "nd1", "relative_file": "i.py",
         "search": "abc", "replace": "xyz"},
        {"id": "nd2", "relative_file": "i.py", "regex": True,
         "search": "abc", "replace": "xyz"},
    ])
    # First patch changes abc->xyz, second regex patch tries to find "abc" again
    # which is now gone -> failure (not deduped)
    assert not ok or "nd2" not in deduped


# ---------------------------------------------------------------------------
# Side selection: source-only / migrated-only / drifted per-side blocks
# ---------------------------------------------------------------------------


def _write_side(tmp_path, rel, *, source=None, migrated=None):
    """Write a file with different content on each side (for drift tests)."""
    if source is not None:
        p = tmp_path / "Validation" / "source" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source)
    if migrated is not None:
        p = tmp_path / "Output" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(migrated)
    (tmp_path / "Validation" / "shared").mkdir(parents=True, exist_ok=True)


def test_source_only_patch_leaves_output_untouched(tmp_path):
    _setup_conv(tmp_path, {"a.py": "df = read_sf('T')\n"})
    ok, _, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "s1", "relative_file": "a.py",
         "source": {"search": "read_sf('T')", "replace": "spark.table('S.T')"}}
    ])
    assert ok
    assert "spark.table('S.T')" in (tmp_path / "Validation" / "source" / "a.py").read_text()
    assert (tmp_path / "Output" / "a.py").read_text() == "df = read_sf('T')\n"


def test_migrated_only_patch_leaves_source_untouched(tmp_path):
    _setup_conv(tmp_path, {"a.py": "db = 'PROD'\n"})
    ok, _, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "m1", "relative_file": "a.py",
         "migrated": {"search": "'PROD'", "replace": "os.environ['SCOS_DATABASE_NAME']"}}
    ])
    assert ok
    assert (tmp_path / "Validation" / "source" / "a.py").read_text() == "db = 'PROD'\n"
    assert "SCOS_DATABASE_NAME" in (tmp_path / "Output" / "a.py").read_text()


def test_drifted_per_side_blocks(tmp_path):
    """source and migrated have different text -> each side patched independently."""
    _write_side(tmp_path, "a.py",
                source="x = legacy_src\n",
                migrated="x = migrated_form\n")
    ok, _, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "drift", "relative_file": "a.py",
         "source": {"search": "legacy_src", "replace": "PATCHED"},
         "migrated": {"search": "migrated_form", "replace": "PATCHED"}}
    ])
    assert ok
    assert (tmp_path / "Validation" / "source" / "a.py").read_text() == "x = PATCHED\n"
    assert (tmp_path / "Output" / "a.py").read_text() == "x = PATCHED\n"


# ---------------------------------------------------------------------------
# Atomic batch semantics
# ---------------------------------------------------------------------------


def test_batch_atomic_rollback_on_failure(tmp_path):
    """If any entry fails, NOTHING is written and the blueprint is untouched."""
    _setup_conv(tmp_path, {"a.py": "x = 1\ny = 2\n"})
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "good", "relative_file": "a.py", "search": "x = 1", "replace": "x = 99"},
        {"id": "bad", "relative_file": "a.py", "search": "NONEXISTENT", "replace": "z"},
    ])
    assert not ok
    assert written == []
    # first entry's change must NOT have been persisted
    assert (tmp_path / "Validation" / "source" / "a.py").read_text() == "x = 1\ny = 2\n"
    bp = tmp_path / "Validation" / "shared" / "patch_blueprint.json"
    assert not bp.exists() or json.loads(bp.read_text())["patches"] == []


def test_batch_stacks_on_same_file(tmp_path):
    """Two entries editing the same file stack against an in-memory working copy."""
    _setup_conv(tmp_path, {"a.py": "val = STAGE_A\n"})
    ok, _, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "s1", "relative_file": "a.py", "search": "STAGE_A", "replace": "STAGE_B"},
        {"id": "s2", "relative_file": "a.py", "search": "STAGE_B", "replace": "STAGE_C"},
    ])
    assert ok
    assert (tmp_path / "Output" / "a.py").read_text() == "val = STAGE_C\n"


def test_missing_id_errors(tmp_path):
    _setup_conv(tmp_path, {"a.py": "x = 1\n"})
    ok, results, _, _ = patch_engine.add_patches(tmp_path, [
        {"relative_file": "a.py", "search": "x = 1", "replace": "x = 2"}
    ])
    assert not ok
    assert "missing 'id'" in results[-1].error


def test_missing_search_errors(tmp_path):
    _setup_conv(tmp_path, {"a.py": "x = 1\n"})
    ok, results, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "nosrch", "relative_file": "a.py", "replace": "x = 2"}
    ])
    assert not ok


# ---------------------------------------------------------------------------
# Deletion + multiline / named-group regex
# ---------------------------------------------------------------------------


def test_literal_deletion_keeps_valid_python(tmp_path):
    _setup_conv(tmp_path, {"a.py": "x = 1\nlog.emit('hi')\ny = 2\n"})
    ok, _, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "del", "relative_file": "a.py", "search": "log.emit('hi')\n", "replace": ""}
    ])
    assert ok
    assert (tmp_path / "Output" / "a.py").read_text() == "x = 1\ny = 2\n"


def test_regex_multiline_dotall(tmp_path):
    """(?s) lets the pattern span newlines (e.g. a multi-line connector read)."""
    code = "a = spark.read \\\n    .format('snowflake') \\\n    .load()\nb = 2\n"
    _setup_conv(tmp_path, {"a.py": code})
    ok, _, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "ml", "relative_file": "a.py", "regex": True,
         "search": r"(?s)spark\.read.*?\.load\(\)", "replace": "spark.table('S.T')"}
    ])
    assert ok
    assert (tmp_path / "Output" / "a.py").read_text() == "a = spark.table('S.T')\nb = 2\n"


def test_regex_named_group_backref(tmp_path):
    _setup_conv(tmp_path, {"a.py": "env = get('stage')\n"})
    ok, _, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "ng", "relative_file": "a.py", "regex": True,
         "search": r"get\('(?P<name>\w+)'\)", "replace": r"'\g<name>'"}
    ])
    assert ok
    assert (tmp_path / "Output" / "a.py").read_text() == "env = 'stage'\n"


# ---------------------------------------------------------------------------
# Glob: recursion, single-side, regex combo
# ---------------------------------------------------------------------------


def test_glob_recursive_nested_dirs(tmp_path):
    _setup_conv(tmp_path, {
        "src/a.py": "v = old\n",
        "src/sub/b.py": "v = old\n",
        "src/sub/deep/c.py": "v = old\n",
    })
    ok, _, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "gr", "relative_file": "src/**/*.py", "search": "old", "replace": "new"}
    ])
    assert ok
    for rel in ("src/a.py", "src/sub/b.py", "src/sub/deep/c.py"):
        assert (tmp_path / "Output" / rel).read_text() == "v = new\n"


def test_glob_matches_only_one_side(tmp_path):
    """A glob entry succeeds when only one side has matching files."""
    # file exists only on the source side
    _write_side(tmp_path, "only/s.py", source="v = old\n")
    ok, _, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "g1side", "relative_file": "only/*.py", "search": "old", "replace": "new"}
    ])
    assert ok
    assert (tmp_path / "Validation" / "source" / "only" / "s.py").read_text() == "v = new\n"


def test_glob_regex_replace_all_combo(tmp_path):
    """The headline use case: one entry collapses many varied sites across files."""
    _setup_conv(tmp_path, {
        "nb/a.py": "import sys\nif p:\n    dbutils.notebook.exit('a')\n",
        "nb/b.py": "import sys\ndbutils.notebook.exit(f'b {x}')\ndbutils.notebook.exit('c')\n",
    })
    ok, _, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "collapse", "relative_file": "nb/*.py", "regex": True, "replace_all": True,
         "search": r"dbutils\.notebook\.exit\([^\n]*\)", "replace": "sys.exit(0)"}
    ])
    assert ok
    remaining = 0
    for side in ("Validation/source", "Output"):
        for rel in ("nb/a.py", "nb/b.py"):
            remaining += (tmp_path / side / rel).read_text().count("dbutils.notebook.exit")
    assert remaining == 0


# ---------------------------------------------------------------------------
# Blueprint persistence + fold + dedup against existing
# ---------------------------------------------------------------------------


def _blueprint(tmp_path):
    return json.loads((tmp_path / "Validation" / "shared" / "patch_blueprint.json").read_text())


def test_blueprint_persisted(tmp_path):
    _setup_conv(tmp_path, {"a.py": "x = 1\n"})
    patch_engine.add_patches(tmp_path, [
        {"id": "p", "relative_file": "a.py", "note": "n", "search": "x = 1", "replace": "x = 2"}
    ])
    patches = _blueprint(tmp_path)["patches"]
    assert len(patches) == 1 and patches[0]["id"] == "p"


def test_identical_per_side_blocks_folded(tmp_path):
    """Identical source+migrated blocks are folded to a single top-level search/replace."""
    _setup_conv(tmp_path, {"a.py": "x = 1\n"})
    ok, _, _, _ = patch_engine.add_patches(tmp_path, [
        {"id": "fold", "relative_file": "a.py",
         "source": {"search": "x = 1", "replace": "x = 2"},
         "migrated": {"search": "x = 1", "replace": "x = 2"}}
    ])
    assert ok
    stored = _blueprint(tmp_path)["patches"][0]
    assert "source" not in stored and "migrated" not in stored
    assert stored["search"] == "x = 1" and stored["replace"] == "x = 2"


def test_dedup_against_existing_blueprint(tmp_path):
    """Re-submitting an already-applied patch in a later call is deduped, not re-applied."""
    _setup_conv(tmp_path, {"a.py": "x = 1\n"})
    ok1, _, _, d1 = patch_engine.add_patches(tmp_path, [
        {"id": "first", "relative_file": "a.py", "search": "x = 1", "replace": "x = 2"}
    ])
    assert ok1 and d1 == []
    # same content, different id, in a SEPARATE call -> deduped (and not re-applied)
    ok2, _, written2, d2 = patch_engine.add_patches(tmp_path, [
        {"id": "again", "relative_file": "a.py", "search": "x = 1", "replace": "x = 2"}
    ])
    assert ok2 and "again" in d2 and written2 == []
    assert len(_blueprint(tmp_path)["patches"]) == 1


# ---------------------------------------------------------------------------
# Glob-consolidation hint (validate._audit_patch_glob_opportunity)
# ---------------------------------------------------------------------------


def _exit_patch(rel):
    return {"id": f"e_{rel}", "relative_file": rel, "regex": True, "replace_all": True,
            "search": r"dbutils\.notebook\.exit\([^\n]*\)", "replace": "sys.exit(0)"}


def test_glob_consolidation_hints(tmp_path):
    hints = validate._audit_patch_glob_opportunity(
        tmp_path, [_exit_patch("nb/a.py"), _exit_patch("nb/b.py"), _exit_patch("nb/sub/c.py")],
    )
    assert len(hints) == 1 and "3 files share the SAME rewrite" in hints[0]

    assert validate._audit_patch_glob_opportunity(tmp_path, [_exit_patch("nb/a.py")]) == []

    g = {"id": "g", "relative_file": "nb/**/*.py", "regex": True, "search": "x", "replace": "y"}
    assert validate._audit_patch_glob_opportunity(tmp_path, [g, g]) == []

    def ps(rel):
        return {"id": f"ps_{rel}", "relative_file": rel,
                "source": {"search": "a", "replace": "b"},
                "migrated": {"search": "c", "replace": "d"}}
    assert validate._audit_patch_glob_opportunity(tmp_path, [ps("a.py"), ps("b.py")]) == []

    (tmp_path / "Validation" / "shared").mkdir(parents=True, exist_ok=True)
    patch_engine.save_blueprint(tmp_path, {"patches": [_exit_patch("nb/a.py")]})
    hints = validate._audit_patch_glob_opportunity(tmp_path, [_exit_patch("nb/b.py")])
    assert len(hints) == 1 and "2 files share the SAME rewrite" in hints[0]

    p1 = {"id": "p1", "relative_file": "a.py", "search": "foo", "replace": "bar"}
    p2 = {"id": "p2", "relative_file": "b.py", "search": "baz", "replace": "qux"}
    assert validate._audit_patch_glob_opportunity(tmp_path, [p1, p2]) == []


def test_resolve_commit_files_output_prefixed_path(tmp_path):
    """Path given as Output/foo.py is accepted and returned relative to conv_root."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "foo.py").write_text("")
    result = validate._resolve_commit_files(tmp_path, ["Output/foo.py"])
    assert result == [str(Path("Output") / "foo.py")]


def test_resolve_commit_files_bare_name_autoprefixed(tmp_path):
    """Path without Output/ prefix is auto-prefixed and resolves correctly."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "bar.py").write_text("")
    result = validate._resolve_commit_files(tmp_path, ["bar.py"])
    assert result == [str(Path("Output") / "bar.py")]


def test_resolve_commit_files_traversal_rejected(tmp_path):
    """Path resolving outside Output/ via traversal is rejected with SystemExit."""
    (tmp_path / "Output").mkdir()
    with pytest.raises(SystemExit):
        validate._resolve_commit_files(tmp_path, ["../secret.txt"])


def test_resolve_commit_files_nonexistent_rejected(tmp_path):
    """Non-existent file under Output/ is rejected with SystemExit."""
    (tmp_path / "Output").mkdir()
    with pytest.raises(SystemExit):
        validate._resolve_commit_files(tmp_path, ["Output/no_such_file.py"])


def test_resolve_commit_files_multiple_paths(tmp_path):
    """Multiple valid files are all returned correctly."""
    out = tmp_path / "Output"
    out.mkdir()
    (out / "a.py").write_text("")
    (out / "b.py").write_text("")
    result = validate._resolve_commit_files(tmp_path, ["Output/a.py", "b.py"])
    assert set(result) == {
        str(Path("Output") / "a.py"),
        str(Path("Output") / "b.py"),
    }


def _init_git_repo(root: Path) -> None:
    import subprocess
    def g(*a):
        subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")


def test_git_commit_paths_scoped_to_pathspec(tmp_path):
    """commit --files must commit ONLY the listed paths, even if an unrelated file
    is already staged in the index (the partial-commit scoping fix)."""
    import subprocess

    out = tmp_path / "Output"
    out.mkdir()
    (out / "a.py").write_text("v1\n")
    (out / "b.py").write_text("v1\n")
    _init_git_repo(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True, capture_output=True)

    # Modify both; pre-stage the UNRELATED file into the index.
    (out / "a.py").write_text("v2\n")
    (out / "b.py").write_text("v2\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "Output/b.py"], check=True, capture_output=True)

    files = validate._resolve_commit_files(tmp_path, ["a.py"])
    sha = validate._git_commit_paths(tmp_path, files, "fix a only")
    assert sha

    committed = subprocess.run(
        ["git", "-C", str(tmp_path), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True, text=True,
    ).stdout.split()
    status = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain"],
        capture_output=True, text=True,
    ).stdout

    assert committed == [str(Path("Output") / "a.py")]
    # b.py was NOT swept into the commit; it remains staged/uncommitted.
    assert "Output/b.py" in status


# ---------------------------------------------------------------------------
# Known-patches library
# ---------------------------------------------------------------------------


class TestNormalizeEnvName:
    def test_normalizes_literals(self):
        assert patch_engine._normalize_env_name("my_table") == "MY_TABLE"
        assert patch_engine._normalize_env_name("my-schema.table") == "MY_SCHEMA_TABLE"
        assert patch_engine._normalize_env_name("table_") == "TABLE"
        assert patch_engine._normalize_env_name("!!!") == "TABLE"


class TestDetectOsSystem:
    def test_detects_and_ignores(self):
        assert patch_engine._detect_os_system("import os\nos.system('ls -la')\n", "a.py")
        assert patch_engine._detect_os_system("def f():\n    os.system('cmd')\n", "a.py")
        assert patch_engine._detect_os_system("import os\npath = os.path.join('a', 'b')\n", "a.py") == []
        assert patch_engine._detect_os_system("", "a.py") == []


class TestBuildOsSystemPatch:
    def test_rewrites_calls(self):
        import re
        p = patch_engine._build_os_system_patch({}, "src/job.py")
        assert p["id"] == "remove_os_system" and p["replace_all"] is True

        src = "import os\nos.system('ls')\nprint('x')\n"
        result = re.sub(p["search"], p["replace"], src)
        assert "pass  # SCOS: removed os.system" in result
        assert "os.system(" not in result

        src = "def f():\n    os.system('cmd')\n"
        result = re.sub(p["search"], p["replace"], src)
        assert "    pass  # SCOS: removed os.system" in result

        src = "os.system('ls')  # clean up\nprint('done')\n"
        result = re.sub(p["search"], p["replace"], src, flags=re.MULTILINE)
        assert "os.system(" not in result


class TestDetectSysPathMutation:
    def test_module_scope_only(self):
        assert patch_engine._detect_sys_path_mutation("import sys\nsys.path.insert(0, '/x')\n", "b.py")
        assert patch_engine._detect_sys_path_mutation("import sys\nsys.path.append('/x')\n", "b.py")
        assert patch_engine._detect_sys_path_mutation("def setup():\n    sys.path.insert(0, '/x')\n", "b.py") == []
        assert patch_engine._detect_sys_path_mutation("import sys\nx = sys.path\n", "b.py") == []


class TestBuildSysPathMutationPatch:
    def test_rewrites_calls(self):
        import re
        p = patch_engine._build_sys_path_mutation_patch({}, "b.py")
        assert p["id"] == "remove_top_level_sys_path_mutation" and p["replace_all"] is True

        src = "import sys\nsys.path.insert(0, '/x')\ndo_work()\n"
        result = re.sub(p["search"], p["replace"], src)
        assert "# SCOS: removed sys.path mutation" in result
        assert "sys.path.insert(" not in result

        src = "sys.path.append('/lib')  # add vendor path\ndo_work()\n"
        result = re.sub(p["search"], p["replace"], src, flags=re.MULTILINE)
        assert "sys.path.append(" not in result
        assert "# SCOS: removed sys.path mutation" in result


class TestDetectSaveAsTableEnv:
    def test_detects_literals_and_skips_dynamic(self):
        src = 'df.write.mode("overwrite").saveAsTable("my_table")\n'
        matches = patch_engine._detect_saveastable_env(src, "c.py")
        assert len(matches) == 1 and matches[0]["literal"] == "my_table"

        src = 'df.saveAsTable("t")\ndf2.saveAsTable("t")\n'
        assert len(patch_engine._detect_saveastable_env(src, "c.py")) == 1

        assert patch_engine._detect_saveastable_env("df.saveAsTable(table_name)\n", "c.py") == []
        assert patch_engine._detect_saveastable_env(
            'df.saveAsTable(os.environ.get("SCOS_OUTPUT_T", "t"))\n', "c.py"
        ) == []


class TestBuildSaveAsTableEnvPatch:
    def test_builds_env_backed_replace(self):
        m = {"literal": "my_output", "full_match": '.saveAsTable("my_output")'}
        p = patch_engine._build_saveastable_env_patch(m, "c.py")
        assert p["id"] == "saveastable_env_my_output"
        assert "SCOS_OUTPUT_MY_OUTPUT" in p["replace"]

        m = {"literal": "schema.my-table", "full_match": '.saveAsTable("schema.my-table")'}
        p = patch_engine._build_saveastable_env_patch(m, "c.py")
        assert "SCOS_OUTPUT_SCHEMA_MY_TABLE" in p["replace"]


class TestDetectWidgetGetEnv:
    def test_detects_widget_keys(self):
        src = 'env = dbutils.widgets.get("environment")\n'
        matches = patch_engine._detect_widget_get_env(src, "d.py")
        assert len(matches) == 1 and matches[0]["key"] == "environment"
        assert patch_engine._detect_widget_get_env("val = dbutils.widgets.get(key_name)\n", "d.py") == []


class TestBuildWidgetGetEnvPatch:
    def test_builds_env_lookup(self):
        m = {"key": "environment", "full_match": 'dbutils.widgets.get("environment")'}
        p = patch_engine._build_widget_get_env_patch(m, "d.py")
        assert p["replace"] == 'os.environ["ENVIRONMENT"]'

        m = {"key": "my_env", "full_match": 'dbutils.widgets.get("my_env")'}
        assert patch_engine._build_widget_get_env_patch(m, "d.py")["replace"] == 'os.environ["MY_ENV"]'


class TestDetectDbutilsNotebookExit:
    def test_positive(self):
        src = 'do_work()\ndbutils.notebook.exit("success")\n'
        assert patch_engine._detect_dbutils_notebook_exit(src, "e.py") != []

    def test_positive_with_args(self):
        src = 'dbutils.notebook.exit(json.dumps({"status": "ok"}))\n'
        assert patch_engine._detect_dbutils_notebook_exit(src, "e.py") != []

    def test_negative(self):
        src = "import sys\nsys.exit(0)\n"
        assert patch_engine._detect_dbutils_notebook_exit(src, "e.py") == []


class TestBuildDbutilsNotebookExitPatch:
    def test_patch_structure(self):
        p = patch_engine._build_dbutils_notebook_exit_patch({}, "e.py")
        assert p["id"] == "remove_dbutils_notebook_exit"
        assert p["replace"] == "sys.exit(0)"
        assert p["regex"] is True
        assert p["replace_all"] is True


class TestDetectWidgetDeclaration:
    def test_dbutils_text(self):
        src = 'dbutils.widgets.text("uc_name", "teo_dev", "uc_name")\n'
        m = patch_engine._detect_widget_declaration(src, "d.py")
        assert len(m) == 1
        assert m[0]["name"] == "uc_name" and m[0]["default"] == "teo_dev"

    def test_dbutils_dropdown_with_choices(self):
        src = 'dbutils.widgets.dropdown("env", "dev", ["dev", "prod"], "Environment")\n'
        m = patch_engine._detect_widget_declaration(src, "d.py")
        assert len(m) == 1 and m[0]["name"] == "env" and m[0]["default"] == "dev"

    def test_ignores_ipywidgets(self):
        # ipywidgets is handled by the dedicated detector, not this one.
        src = 'w = widgets.Text(value="dev", description="environment")\n'
        assert patch_engine._detect_widget_declaration(src, "d.py") == []

    def test_dedupes_by_name(self):
        src = (
            'dbutils.widgets.text("k", "v1", "k")\n'
            'dbutils.widgets.text("k", "v1", "k")\n'
        )
        assert len(patch_engine._detect_widget_declaration(src, "d.py")) == 1

    def test_skips_non_literal_default(self):
        assert patch_engine._detect_widget_declaration(
            'dbutils.widgets.text("k", default_val)\n', "d.py"
        ) == []


class TestIpywidgets:
    def _apply(self, src):
        import re
        result = src
        for p in patch_engine.suggest_known_patches(src, "job.py"):
            if p.get("regex"):
                result = re.sub(p["search"], p["replace"], result)
            else:
                result = result.replace(p["search"], p["replace"])
        return result

    def test_assigned_widget_couples_decl_and_read(self):
        src = (
            'env_w = widgets.Text(value="dev", description="environment")\n'
            'env = env_w.value\n'
            'print(env_w.value)\n'
        )
        ids = {p["id"] for p in patch_engine.suggest_known_patches(src, "job.py")}
        assert "ipywidget_decl_environment" in ids
        assert "ipywidget_read_env_w" in ids
        out = self._apply(src)
        assert 'env_w = os.environ.get("ENVIRONMENT", "dev")' in out
        assert "env = env_w\n" in out
        assert ".value" not in out
        import ast
        ast.parse(out)

    def test_inline_value_collapses_in_one_shot(self):
        src = 'brand = ipywidgets.Dropdown(options=["plk"], value="plk", description="brand").value\n'
        out = self._apply(src)
        assert out == 'brand = os.environ.get("BRAND", "plk")\n'
        ids = {p["id"] for p in patch_engine.suggest_known_patches(src, "job.py")}
        assert ids == {"ipywidget_inline_brand"}

    def test_declared_but_unused_emits_decl_only(self):
        # No <var>.value read: the read patch must NOT be emitted (would 0-match
        # and fail the atomic batch).
        src = 'unused_w = widgets.Text(value="zzz", description="unused")\n'
        ids = [p["id"] for p in patch_engine.suggest_known_patches(src, "job.py")]
        assert ids == ["ipywidget_decl_unused"]

    def test_skips_numeric_and_dynamic_values(self):
        assert patch_engine._detect_ipywidgets('n = widgets.IntText(value=5, description="c")\n', "j.py") == []
        assert patch_engine._detect_ipywidgets('w = widgets.Text(value=some_var, description="c")\n', "j.py") == []
        # no value= at all
        assert patch_engine._detect_ipywidgets('w = widgets.Dropdown(options=["a"], description="c")\n', "j.py") == []

    def test_key_derives_from_var_when_no_description(self):
        src = 'region = widgets.Text(value="us")\nx = region.value\n'
        out = self._apply(src)
        assert 'region = os.environ.get("REGION", "us")' in out
        assert "x = region\n" in out

    def test_nested_paren_constructor_is_skipped(self):
        # description=fn() truncates the [^\n)]* capture; skip so a malformed
        # patch can't reject the whole batch. A clean sibling still fires.
        src = (
            'a = widgets.Text(value="dev", description=fn())\n'
            'b = widgets.Text(value="us", description="region")\n'
            'y = b.value\n'
        )
        ids = {p["id"] for p in patch_engine.suggest_known_patches(src, "job.py")}
        assert ids == {"ipywidget_decl_region", "ipywidget_read_b"}

    def test_two_description_less_inline_widgets_get_distinct_keys(self):
        src = 'x = widgets.Text(value="plk").value\ny = widgets.Text(value="bk").value\n'
        ids = {p["id"] for p in patch_engine.suggest_known_patches(src, "job.py")}
        assert ids == {"ipywidget_inline_plk", "ipywidget_inline_bk"}
        out = self._apply(src)
        assert 'x = os.environ.get("PLK", "plk")' in out
        assert 'y = os.environ.get("BK", "bk")' in out

    def test_value_write_site_skips_whole_widget(self):
        # w.value is written back — collapse model breaks, so skip entirely.
        src = 'w = widgets.Text(value="dev", description="e")\nw.value = "x"\ny = w.value\n'
        assert patch_engine._detect_ipywidgets(src, "j.py") == []
        # == comparison is a read, not a write, so it still fires.
        src2 = 'w = widgets.Text(value="dev", description="e")\nif w.value == "dev":\n    pass\n'
        ids = {p["id"] for p in patch_engine.suggest_known_patches(src2, "job.py")}
        assert ids == {"ipywidget_decl_e", "ipywidget_read_w"}


class TestIpywidgetsThroughAddPatches:
    """Exercise the atomic add_patches + ast.parse gate, not just a re.sub loop."""

    def test_coupled_decl_read_commits_both_sides(self, tmp_path):
        src = (
            "import os\n"
            'env_w = widgets.Text(value="dev", description="environment")\n'
            "env = env_w.value\n"
        )
        _setup_conv(tmp_path, {"src/job.py": src})
        entries = patch_engine.suggest_known_patches(src, "src/job.py")
        ok, results, written, _ = patch_engine.add_patches(tmp_path, entries)
        assert ok
        assert sorted(written) == ["Output/src/job.py", "Validation/source/src/job.py"]
        for side in ("Output", "Validation/source"):
            out = (tmp_path / side / "src/job.py").read_text()
            assert 'env_w = os.environ.get("ENVIRONMENT", "dev")' in out
            assert ".value" not in out
            import ast
            ast.parse(out)

    def test_nested_paren_widget_does_not_block_clean_patches(self, tmp_path):
        # The nested-paren widget is skipped by the detector, so add_patches only
        # sees valid entries and commits them — no corruption, no batch abort.
        src = (
            "import os\n"
            'a = widgets.Text(value="dev", description=fn())\n'
            'b = widgets.Text(value="us", description="region")\n'
            "y = b.value\n"
        )
        _setup_conv(tmp_path, {"src/job.py": src})
        entries = patch_engine.suggest_known_patches(src, "src/job.py")
        ok, _, written, _ = patch_engine.add_patches(tmp_path, entries)
        assert ok and written
        out = (tmp_path / "Output" / "src/job.py").read_text()
        assert 'b = os.environ.get("REGION", "us")' in out
        assert "y = b\n" in out
        # the nested-paren widget is left untouched (no corruption)
        assert 'a = widgets.Text(value="dev", description=fn())' in out
        import ast
        ast.parse(out)


class TestBuildWidgetDeclarationPatch:
    def test_builds_setdefault(self):
        m = {"name": "uc_name", "default": "teo_dev",
             "full_match": 'dbutils.widgets.text("uc_name", "teo_dev", "uc_name")'}
        p = patch_engine._build_widget_declaration_patch(m, "d.py")
        assert p["id"] == "widget_decl_uc_name"
        assert p["replace"] == 'os.environ.setdefault("UC_NAME", "teo_dev")'
        assert p["replace_all"] is True

    def test_composes_with_widget_get(self):
        # The declaration seeds UC_NAME; the get reads os.environ["UC_NAME"].
        src = (
            'dbutils.widgets.text("uc_name", "teo_dev", "uc_name")\n'
            'name = dbutils.widgets.get("uc_name")\n'
        )
        result = src
        for p in patch_engine.suggest_known_patches(src, "job.py"):
            result = result.replace(p["search"], p["replace"])
        assert 'os.environ.setdefault("UC_NAME", "teo_dev")' in result
        assert 'os.environ["UC_NAME"]' in result
        assert "dbutils.widgets" not in result


class TestSuggestKnownPatches:
    def test_suggest_multiple_patterns(self):
        src = textwrap.dedent("""\
            import os
            import sys
            sys.path.append('/x')
            os.system('ls')
            dbutils.widgets.text("env", "dev", "env")
            env = dbutils.widgets.get("env")
            df.saveAsTable("output_table")
            dbutils.notebook.exit("done")
        """)
        patches = patch_engine.suggest_known_patches(src, "job.py")
        ids = {p["id"] for p in patches}
        assert "remove_os_system" in ids
        assert "remove_top_level_sys_path_mutation" in ids
        assert "widget_get_env" in ids
        assert "widget_decl_env" in ids
        assert "saveastable_env_output_table" in ids
        assert "remove_dbutils_notebook_exit" in ids

    def test_suggest_no_matches_returns_empty(self):
        src = "x = 1\ny = 2\n"
        assert patch_engine.suggest_known_patches(src, "clean.py") == []

    def test_suggest_deduplicates_within_file(self):
        # Two os.system calls — should produce only one patch entry
        src = "os.system('a')\nos.system('b')\n"
        patches = patch_engine.suggest_known_patches(src, "job.py")
        assert len([p for p in patches if p["id"] == "remove_os_system"]) == 1

    def test_suggest_all_entries_have_required_fields(self):
        src = 'env = dbutils.widgets.get("e")\n'
        patches = patch_engine.suggest_known_patches(src, "job.py")
        for p in patches:
            assert "id" in p
            assert "relative_file" in p
            assert "search" in p
            assert "replace" in p


def test_magic_run_is_not_a_known_patch():
    """# MAGIC %run is a LIVE include now (translated to _nb_run), not deleted —
    so it must NOT be suggested as a removal patch, and the detector is gone."""
    patches = patch_engine.suggest_known_patches(
        '# COMMAND ----------\n# MAGIC %run ./COMMON_UTILS\nx = 1\n', "job.py"
    )
    assert all(p["id"] != "remove_magic_run" for p in patches)
    assert not hasattr(patch_engine, "_detect_magic_run")


class TestDetectDropTableSql:
    def test_positive_double_quoted_if_exists(self):
        src = 'spark.sql("DROP TABLE IF EXISTS met_ta.BaseGAM_Temp")\n'
        assert patch_engine._detect_drop_table_sql(src, "a.py") != []

    def test_positive_single_quoted_no_if_exists(self):
        src = "spark.sql('DROP TABLE pub_ta.foo')\n"
        assert patch_engine._detect_drop_table_sql(src, "a.py") != []

    def test_positive_lowercase(self):
        src = 'spark.sql("drop table if exists lower.case")\n'
        assert patch_engine._detect_drop_table_sql(src, "a.py") != []

    def test_positive_indented(self):
        src = 'def cleanup():\n    spark.sql("DROP TABLE IF EXISTS tmp.tbl")\n'
        assert patch_engine._detect_drop_table_sql(src, "a.py") != []

    def test_negative_create_table(self):
        src = 'spark.sql("CREATE TABLE foo AS SELECT * FROM bar")\n'
        assert patch_engine._detect_drop_table_sql(src, "a.py") == []

    def test_negative_select_with_drop_table_name(self):
        src = 'spark.sql("SELECT * FROM DROP_TABLE")\n'
        assert patch_engine._detect_drop_table_sql(src, "a.py") == []

    def test_negative_variable_arg(self):
        src = "spark.sql(some_variable)\n"
        assert patch_engine._detect_drop_table_sql(src, "a.py") == []

    def test_negative_drop_database(self):
        src = 'spark.sql("DROP DATABASE foo")\n'
        assert patch_engine._detect_drop_table_sql(src, "a.py") == []


class TestBuildDropTableSqlPatch:
    def test_patch_structure(self):
        p = patch_engine._build_drop_table_sql_patch({}, "src/job.py")
        assert p["id"] == "remove_drop_table_sql"
        assert p["relative_file"] == "src/job.py"
        assert p["regex"] is True
        assert p["replace_all"] is True

    def test_patch_transforms_double_quoted(self):
        import re
        src = 'spark.sql("DROP TABLE IF EXISTS met_ta.BaseGAM_Temp")\n'
        p = patch_engine._build_drop_table_sql_patch({}, "job.py")
        result = re.sub(p["search"], p["replace"], src)
        assert "pass  # SCOS: removed DROP TABLE (destructive)" in result
        assert "spark.sql(" not in result

    def test_patch_transforms_single_quoted(self):
        import re
        src = "spark.sql('DROP TABLE pub_ta.foo')\n"
        p = patch_engine._build_drop_table_sql_patch({}, "job.py")
        result = re.sub(p["search"], p["replace"], src)
        assert "pass  # SCOS: removed DROP TABLE (destructive)" in result
        assert "spark.sql(" not in result

    def test_patch_preserves_indent(self):
        import re
        src = 'def cleanup():\n    spark.sql("DROP TABLE IF EXISTS tmp.tbl")\n'
        p = patch_engine._build_drop_table_sql_patch({}, "job.py")
        result = re.sub(p["search"], p["replace"], src)
        assert "    pass  # SCOS: removed DROP TABLE (destructive)" in result

    def test_patch_transforms_lowercase(self):
        import re
        src = 'spark.sql("drop table if exists lower.case")\n'
        p = patch_engine._build_drop_table_sql_patch({}, "job.py")
        result = re.sub(p["search"], p["replace"], src)
        assert "pass  # SCOS: removed DROP TABLE (destructive)" in result

    def test_patch_transforms_three_part_name(self):
        import re
        src = 'spark.sql("DROP TABLE IF EXISTS db_name.schema_name.table_name")\n'
        p = patch_engine._build_drop_table_sql_patch({}, "job.py")
        result = re.sub(p["search"], p["replace"], src)
        assert "pass  # SCOS: removed DROP TABLE (destructive)" in result


class TestSuggestDropTableSqlKnownPatch:
    def test_suggest_includes_remove_drop_table_sql(self):
        src = textwrap.dedent("""\
            spark.sql("DROP TABLE IF EXISTS tmp.staging")
            spark.sql('DROP TABLE prod.output')
            df.write.mode("overwrite").saveAsTable("output_table")
        """)
        patches = patch_engine.suggest_known_patches(src, "job.py")
        ids = {p["id"] for p in patches}
        assert "remove_drop_table_sql" in ids
        assert "saveastable_env_output_table" in ids

    def test_suggest_no_drop_table_no_entry(self):
        src = 'spark.sql("CREATE TABLE foo AS SELECT * FROM bar")\n'
        patches = patch_engine.suggest_known_patches(src, "job.py")
        ids = {p["id"] for p in patches}
        assert "remove_drop_table_sql" not in ids

    def test_suggest_deduplicates_multiple_drop_table_calls(self):
        src = textwrap.dedent("""\
            spark.sql("DROP TABLE IF EXISTS a.tbl1")
            spark.sql("DROP TABLE IF EXISTS b.tbl2")
        """)
        patches = patch_engine.suggest_known_patches(src, "job.py")
        assert len([p for p in patches if p["id"] == "remove_drop_table_sql"]) == 1


class TestDropTableSqlE2ESuggest:
    def test_suggest_e2e_finds_drop_table(self, tmp_path):
        """known-patches suggest includes remove_drop_table_sql for DROP TABLE calls."""
        scripts_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        schemas_dir = tmp_path / "Validation" / "shared" / "schemas"
        schemas_dir.mkdir(parents=True)
        ep_dir = schemas_dir / "entrypoints" / "ep1"
        (ep_dir / "tables").mkdir(parents=True)
        (ep_dir / "_meta.json").write_text(
            json.dumps({"id": "ep1", "path": "src/job.py"})
        )
        manifest = {
            "entrypoints": [{"id": "ep1", "path": "src/job.py", "dir": "entrypoints/ep1"}]
        }
        (schemas_dir / "manifest.json").write_text(json.dumps(manifest))

        src_file = tmp_path / "Validation" / "source" / "src" / "job.py"
        src_file.parent.mkdir(parents=True)
        src_file.write_text(textwrap.dedent("""\
            import os
            spark.sql("DROP TABLE IF EXISTS tmp.staging")
            spark.sql('DROP TABLE prod.output')
            df.write.mode("overwrite").saveAsTable("output_table")
        """))

        import subprocess
        result = subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "validate.py"),
                "known-patches",
                "suggest",
                "--conv-root",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "known-patches suggest:" in result.stdout

        out_path = tmp_path / "Validation" / "known_patch_suggestions.json"
        assert out_path.is_file()
        data = json.loads(out_path.read_text())
        ids = {p["id"] for p in data.get("patches", [])}
        assert "remove_drop_table_sql" in ids
        assert "saveastable_env_output_table" in ids


class TestKnownPatchesSuggestSubcommand:
    def test_suggest_e2e(self, tmp_path):
        """validate.py known-patches suggest writes known_patch_suggestions.json."""
        scripts_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # schemas/manifest + entrypoint dir
        schemas_dir = tmp_path / "Validation" / "shared" / "schemas"
        schemas_dir.mkdir(parents=True)
        ep_dir = schemas_dir / "entrypoints" / "ep1"
        (ep_dir / "tables").mkdir(parents=True)
        (ep_dir / "_meta.json").write_text(
            json.dumps({"id": "ep1", "path": "src/job.py"})
        )
        manifest = {
            "entrypoints": [{"id": "ep1", "path": "src/job.py", "dir": "entrypoints/ep1"}]
        }
        (schemas_dir / "manifest.json").write_text(json.dumps(manifest))

        # Source file with detectable patterns (auto-patch + investigation)
        src_file = tmp_path / "Validation" / "source" / "src" / "job.py"
        src_file.parent.mkdir(parents=True)
        src_file.write_text(
            'import os\nenv = dbutils.widgets.get("env")\nos.system("ls")\n'
            'df = spark.read.parquet("s3://bucket/raw/users")\n'
        )

        import subprocess
        result = subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "validate.py"),
                "known-patches",
                "suggest",
                "--conv-root",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "known-patches suggest:" in result.stdout

        out_path = tmp_path / "Validation" / "known_patch_suggestions.json"
        assert out_path.is_file()
        data = json.loads(out_path.read_text())
        ids = {p["id"] for p in data.get("patches", [])}
        assert "remove_os_system" in ids
        assert any("widget_get" in pid for pid in ids)

        # Investigation worklist is written alongside, flagging the s3 read.
        invest_path = tmp_path / "Validation" / "patch_investigation.json"
        assert invest_path.is_file()
        invest = json.loads(invest_path.read_text())
        cats = {s["category"] for s in invest.get("sites", [])}
        assert "cloud_read_write" in cats
        assert invest["summary"].get("cloud_read_write", 0) >= 1

    def test_suggest_scans_import_closure_not_just_entrypoint(self, tmp_path):
        """I/O in an imported helper (listed in ep['closure']) must surface in the
        worklist even though the entrypoint file itself has none."""
        scripts_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        schemas_dir = tmp_path / "Validation" / "shared" / "schemas"
        (schemas_dir / "entrypoints" / "ep1" / "tables").mkdir(parents=True)
        # closure lists the entrypoint AND an imported helper module
        (schemas_dir / "entrypoints" / "ep1" / "_meta.json").write_text(
            json.dumps({"id": "ep1", "path": "src/job.py",
                        "closure": ["src/job.py", "src/io_helpers.py"]})
        )
        (schemas_dir / "manifest.json").write_text(json.dumps(
            {"entrypoints": [{"id": "ep1", "path": "src/job.py", "dir": "entrypoints/ep1"}]}
        ))
        src = tmp_path / "Validation" / "source" / "src"
        src.mkdir(parents=True)
        # entrypoint file: no residual I/O
        (src / "job.py").write_text("import io_helpers\ndf = io_helpers.load(spark)\n")
        # helper: the S3 read the entrypoint scan alone would miss
        (src / "io_helpers.py").write_text(
            'def load(spark):\n    return spark.read.parquet("s3://bucket/raw/users")\n'
        )
        # migrated Output/ copy of the helper carries a spark_io_detect annotation —
        # scan_scos_annotations must run against closure helpers too, not just the ep.
        out = tmp_path / "Output" / "src"
        out.mkdir(parents=True)
        (out / "job.py").write_text("import io_helpers\ndf = io_helpers.load(spark)\n")
        (out / "io_helpers.py").write_text(
            "def load(spark):\n"
            "    # SCOS: [SPRKCNTPY3200-IO] spark_io_detect: Iceberg catalog table I/O\n"
            '    return spark.read.parquet("s3://bucket/raw/users")\n'
        )

        import subprocess
        result = subprocess.run(
            [sys.executable, str(scripts_dir / "validate.py"),
             "known-patches", "suggest", "--conv-root", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        invest = json.loads((tmp_path / "Validation" / "patch_investigation.json").read_text())
        helper_sites = [s for s in invest["sites"]
                        if s["relative_file"] == "src/io_helpers.py"
                        and s["category"] == "cloud_read_write"]
        assert helper_sites, invest["sites"]
        # the migrate-tool annotation in the helper's Output/ copy is also surfaced
        annot_sites = [s for s in invest["sites"]
                       if s["relative_file"] == "src/io_helpers.py"
                       and s["category"] == "scos_io_annotation"]
        assert annot_sites, invest["sites"]

    def test_suggest_dedupes_shared_helper_across_entrypoints(self, tmp_path):
        """A helper in two entrypoints' closures is scanned once (no duplicate sites)."""
        scripts_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        schemas_dir = tmp_path / "Validation" / "shared" / "schemas"
        for ep in ("ep1", "ep2"):
            (schemas_dir / "entrypoints" / ep / "tables").mkdir(parents=True)
            (schemas_dir / "entrypoints" / ep / "_meta.json").write_text(
                json.dumps({"id": ep, "path": f"src/{ep}.py",
                            "closure": [f"src/{ep}.py", "src/shared.py"]})
            )
        (schemas_dir / "manifest.json").write_text(json.dumps({"entrypoints": [
            {"id": "ep1", "path": "src/ep1.py", "dir": "entrypoints/ep1"},
            {"id": "ep2", "path": "src/ep2.py", "dir": "entrypoints/ep2"},
        ]}))
        src = tmp_path / "Validation" / "source" / "src"
        src.mkdir(parents=True)
        (src / "ep1.py").write_text("import shared\n")
        (src / "ep2.py").write_text("import shared\n")
        (src / "shared.py").write_text('x = spark.read.parquet("s3://bucket/raw")\n')

        import subprocess
        result = subprocess.run(
            [sys.executable, str(scripts_dir / "validate.py"),
             "known-patches", "suggest", "--conv-root", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        invest = json.loads((tmp_path / "Validation" / "patch_investigation.json").read_text())
        shared_sites = [s for s in invest["sites"] if s["relative_file"] == "src/shared.py"]
        assert len(shared_sites) == 1, shared_sites  # scanned once, not twice


class TestInvestigationScan:
    """patch_engine.scan_investigation_sites flags residual (non-auto) I/O sites."""

    def test_flags_cloud_and_connector_and_open(self):
        src = (
            'df = spark.read.parquet("s3://bucket/raw")\n'
            'c = spark.read.format("snowflake").option("dbtable", "T").load()\n'
            'cfg = open("/mnt/config.yaml").read()\n'
        )
        cats = {s["category"] for s in patch_engine.scan_investigation_sites(src, "job.py")}
        assert {"cloud_read_write", "connector_read", "file_open"}.issubset(cats)

    def test_connector_read_carries_line_and_hint(self):
        src = 'x = 1\ndf = spark.read.format("jdbc").load()\n'
        sites = [s for s in patch_engine.scan_investigation_sites(src, "job.py")
                 if s["category"] == "connector_read"]
        assert len(sites) == 1
        assert sites[0]["line"] == 2
        assert sites[0]["relative_file"] == "job.py"
        assert "PER-SIDE" in sites[0]["hint"]

    def test_namespace_read_requires_table_context(self):
        # A 3-part literal inside an import / plain module path must NOT flag.
        assert patch_engine.scan_investigation_sites(
            'from pyspark.sql.functions import col\n', "job.py"
        ) == []
        # A 3-part literal in a spark.table read SHOULD flag.
        cats = {s["category"] for s in patch_engine.scan_investigation_sites(
            'df = spark.table("PROD_DB.PROD_SCHEMA.CUSTOMERS")\n', "job.py"
        )}
        assert "namespace_read" in cats

    def test_dedupes_identical_lines_and_counts_occurrences(self):
        src = (
            'a = spark.read.parquet("s3://b/x")\n'
            'a = spark.read.parquet("s3://b/x")\n'
        )
        sites = patch_engine.scan_investigation_sites(src, "job.py")
        cloud = [s for s in sites if s["category"] == "cloud_read_write"]
        assert len(cloud) == 1
        assert cloud[0]["occurrences"] == 2

    def test_comments_and_auto_handled_patterns_excluded(self):
        # Commented lines are skipped; widgets/notebook.exit are KNOWN_PATCHES,
        # not investigation items.
        src = (
            '# df = spark.read.parquet("s3://b/x")\n'
            'env = dbutils.widgets.get("env")\n'
            'dbutils.notebook.exit("done")\n'
        )
        assert patch_engine.scan_investigation_sites(src, "job.py") == []


class TestScanScosAnnotations:
    """scan_scos_annotations surfaces the migrate skill's spark_io_detect markers."""

    def test_flags_spark_io_detect_marker(self):
        out = (
            "# SCOS: [SPRKCNTPY3200-IO] spark_io_detect: write to s3 path\n"
            'df.write.parquet("s3://b/out")\n'
        )
        sites = patch_engine.scan_scos_annotations(out, "job.py")
        assert len(sites) == 1
        assert sites[0]["category"] == "scos_io_annotation"
        assert sites[0]["line"] == 1
        assert "spark_io_detect" in sites[0]["text"]

    def test_ignores_non_annotation_scos_comments_and_plain_code(self):
        out = (
            "x = 1  # SCOS: removed os.system\n"          # not a spark_io_detect marker
            'df = spark.read.parquet("s3://b/x")\n'          # plain code, no marker
        )
        assert patch_engine.scan_scos_annotations(out, "job.py") == []


# ---------------------------------------------------------------------------
# patch-add SCOS env-ref audit: hard-fail and --force escape hatch
# ---------------------------------------------------------------------------


def _setup_patch_add_conv(tmp_path):
    """Minimal conv-root for patch-add subprocess tests.

    Sets up:
      - Validation/shared/schemas with a manifest + entrypoint ep1 that has
        no file-category tables (so SCOS_INPUT_* refs in patches are undeclared)
      - Validation/source/src/job.py and Output/src/job.py with patchable content
      - Validation/shared/patch_blueprint.json (empty)
      - a git repo so commit can proceed
    """
    import subprocess

    # Source and Output files
    src = tmp_path / "Validation" / "source" / "src" / "job.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("df = spark.read.parquet('/data/input')\n")
    out = tmp_path / "Output" / "src" / "job.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("df = spark.read.parquet('/data/input')\n")

    # Schemas: entrypoint with a plain table (not category=file) so SCOS_INPUT_* is undeclared
    schemas_dir = tmp_path / "Validation" / "shared" / "schemas"
    ep_dir = schemas_dir / "entrypoints" / "ep1"
    (ep_dir / "tables").mkdir(parents=True)
    (ep_dir / "_meta.json").write_text(json.dumps({"id": "ep1", "path": "src/job.py"}))
    tbl_data = {"_table_key": "my_table", "access": "read", "category": "table"}
    (ep_dir / "tables" / "my_table.json").write_text(json.dumps(tbl_data))
    manifest = {
        "entrypoints": [{"id": "ep1", "path": "src/job.py", "dir": "entrypoints/ep1"}]
    }
    (schemas_dir / "manifest.json").write_text(json.dumps(manifest))

    # Empty blueprint
    bp = tmp_path / "Validation" / "shared" / "patch_blueprint.json"
    bp.write_text(json.dumps({"patches": []}) + "\n")

    # Git repo so patch-add can attempt a commit
    def g(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("add", "-A")
    g("commit", "-qm", "init")


class TestPatchAddSCOSEnvRefAudit:
    def test_undeclared_scos_input_hard_fails(self, tmp_path):
        """patch-add exits 2 and writes nothing when replace refs undeclared SCOS_INPUT_<ID>."""
        scripts_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _setup_patch_add_conv(tmp_path)

        patch = {
            "id": "p_undeclared",
            "relative_file": "src/job.py",
            "search": "spark.read.parquet('/data/input')",
            "replace": "spark.read.parquet(os.environ['SCOS_INPUT_UNDECLARED_TABLE'])",
        }
        patch_file = tmp_path / "patch_undeclared.json"
        patch_file.write_text(json.dumps(patch))

        import subprocess
        result = subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "validate.py"),
                "patch-add",
                "--conv-root", str(tmp_path),
                "--from-file", str(patch_file),
                "--no-commit",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2, result.stderr
        assert "ERROR" in result.stderr
        # The source file must not have been modified
        content = (tmp_path / "Validation" / "source" / "src" / "job.py").read_text()
        assert "SCOS_INPUT_UNDECLARED_TABLE" not in content

    def test_undeclared_scos_input_force_applies(self, tmp_path):
        """patch-add with --force downgrades the audit failure to WARN and applies the patch."""
        scripts_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _setup_patch_add_conv(tmp_path)

        patch = {
            "id": "p_undeclared_force",
            "relative_file": "src/job.py",
            "search": "spark.read.parquet('/data/input')",
            "replace": "spark.read.parquet(os.environ['SCOS_INPUT_UNDECLARED_TABLE'])",
        }
        patch_file = tmp_path / "patch_force.json"
        patch_file.write_text(json.dumps(patch))

        import subprocess
        result = subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "validate.py"),
                "patch-add",
                "--conv-root", str(tmp_path),
                "--from-file", str(patch_file),
                "--no-commit",
                "--force",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "WARN" in result.stderr
        # The patch was applied
        content = (tmp_path / "Validation" / "source" / "src" / "job.py").read_text()
        assert "SCOS_INPUT_UNDECLARED_TABLE" in content


def test_patch_add_glob_consolidation_is_hint_not_fatal(tmp_path):
    """2+ entries sharing the same rewrite across different files no longer abort
    the batch (was `_die(2)`); patch-add prints a HINT and applies them. This is
    the escape hatch for when a glob is impossible (e.g. a sibling file would trip
    the ast.parse gate)."""
    import subprocess
    scripts_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _setup_patch_add_conv(tmp_path)

    # add a second file with identical content so the SAME rewrite applies to both
    out2 = tmp_path / "Output" / "src" / "job2.py"
    out2.write_text("df = spark.read.parquet('/data/input')\n")
    src2 = tmp_path / "Validation" / "source" / "src" / "job2.py"
    src2.write_text("df = spark.read.parquet('/data/input')\n")

    # identical search/replace, different relative_file -> old code exited 2
    same = {"search": "spark.read.parquet('/data/input')",
            "replace": "spark.read.parquet('/data/other')"}
    batch = {"patches": [
        {"id": "p1", "relative_file": "src/job.py", **same},
        {"id": "p2", "relative_file": "src/job2.py", **same},
    ]}
    patch_file = tmp_path / "batch.json"
    patch_file.write_text(json.dumps(batch))

    result = subprocess.run(
        [sys.executable, str(scripts_dir / "validate.py"), "patch-add",
         "--conv-root", str(tmp_path), "--from-file", str(patch_file), "--no-commit"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "HINT" in result.stderr
    # both files were actually patched
    assert "/data/other" in (tmp_path / "Output" / "src" / "job.py").read_text()
    assert "/data/other" in (tmp_path / "Output" / "src" / "job2.py").read_text()



# ---------------------------------------------------------------------------
# .ipynb notebook patch support
# ---------------------------------------------------------------------------


def _make_nb_json(*code_cells):
    """Build a notebook JSON string."""
    cells = []
    for src in code_cells:
        cells.append({
            "cell_type": "code",
            "metadata": {},
            "outputs": [],
            "execution_count": None,
            "source": src.splitlines(keepends=True),
        })
    nb = {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    return json.dumps(nb, indent=1) + "\n"


def _setup_ipynb_conv(tmp_path, rel_path, *code_cells):
    """Create a conv_root with a .ipynb at the given relative path."""
    nb_path = tmp_path / rel_path
    nb_path.parent.mkdir(parents=True, exist_ok=True)
    nb_path.write_text(_make_nb_json(*code_cells), encoding="utf-8")
    return tmp_path


def test_patch_ipynb_unique_match(tmp_path):
    conv_root = _setup_ipynb_conv(
        tmp_path, "Output/pipeline.ipynb",
        "df = spark.read.parquet('/s3/data')",
        "df.write.parquet('/s3/output')",
    )
    side_spec = {
        "file": "Output/pipeline.ipynb",
        "search": "spark.read.parquet('/s3/data')",
        "replace": "spark.read.parquet(os.environ['SCOS_INPUT_DATA'])",
    }
    result = patch_engine.smoke_test_side(
        conv_root, side_spec, replace_all=False, side="migrated"
    )
    assert result.ok, result.error
    assert result.match_count == 1
    patched_nb = json.loads(result.patched_text)
    assert patched_nb["cells"][0]["source"] is not None
    cell_src = "".join(patched_nb["cells"][0]["source"])
    assert "SCOS_INPUT_DATA" in cell_src
    cell2_src = "".join(patched_nb["cells"][1]["source"])
    assert "/s3/output" in cell2_src


def test_patch_ipynb_translates_to_valid_python(tmp_path):
    conv_root = _setup_ipynb_conv(
        tmp_path, "Output/job.ipynb",
        "x = 1\ny = x + 1",
    )
    side_spec = {
        "file": "Output/job.ipynb",
        "search": "y = x + 1",
        "replace": "y = x + 2",
    }
    result = patch_engine.smoke_test_side(
        conv_root, side_spec, replace_all=False, side="migrated"
    )
    assert result.ok
    patched_nb = json.loads(result.patched_text)
    py_src = notebook_source.notebook_dict_to_python(patched_nb)
    ast.parse(py_src)
    assert "y = x + 2" in py_src


def test_patch_ipynb_ambiguous_fails(tmp_path):
    conv_root = _setup_ipynb_conv(
        tmp_path, "Output/multi.ipynb",
        "x = 1",
        "x = 1",  # same code in two cells
    )
    side_spec = {
        "file": "Output/multi.ipynb",
        "search": "x = 1",
        "replace": "x = 99",
    }
    result = patch_engine.smoke_test_side(
        conv_root, side_spec, replace_all=False, side="migrated"
    )
    assert not result.ok
    assert "ambiguous" in result.error.lower()
    assert result.match_count == 2


def test_patch_ipynb_replace_all(tmp_path):
    conv_root = _setup_ipynb_conv(
        tmp_path, "Output/all.ipynb",
        "print('hello')\nprint('hello')",
        "print('hello')",
    )
    side_spec = {
        "file": "Output/all.ipynb",
        "search": "print('hello')",
        "replace": "print('world')",
    }
    result = patch_engine.smoke_test_side(
        conv_root, side_spec, replace_all=True, side="migrated"
    )
    assert result.ok, result.error
    assert result.match_count == 3
    patched_nb = json.loads(result.patched_text)
    for cell in patched_nb["cells"]:
        src = "".join(cell["source"])
        assert "hello" not in src
        assert "world" in src


def test_patch_ipynb_compile_check_rejects_bad_syntax(tmp_path):
    conv_root = _setup_ipynb_conv(
        tmp_path, "Output/bad.ipynb",
        "x = 1",
    )
    side_spec = {
        "file": "Output/bad.ipynb",
        "search": "x = 1",
        "replace": "x = ((",  # invalid Python
    }
    result = patch_engine.smoke_test_side(
        conv_root, side_spec, replace_all=False, side="migrated"
    )
    assert not result.ok
    assert "no longer translates" in result.error or "parses" in result.error.lower()


def test_patch_ipynb_not_found(tmp_path):
    conv_root = _setup_ipynb_conv(
        tmp_path, "Output/nf.ipynb",
        "x = 42",
    )
    side_spec = {
        "file": "Output/nf.ipynb",
        "search": "y = 99",
        "replace": "y = 100",
    }
    result = patch_engine.smoke_test_side(
        conv_root, side_spec, replace_all=False, side="migrated"
    )
    assert not result.ok
    assert "not found" in result.error.lower()


def test_patch_py_still_works(tmp_path):
    py_path = tmp_path / "Output" / "script.py"
    py_path.parent.mkdir(parents=True, exist_ok=True)
    py_path.write_text("x = 1\ny = 2\n", encoding="utf-8")
    side_spec = {
        "file": "Output/script.py",
        "search": "x = 1",
        "replace": "x = 99",
    }
    result = patch_engine.smoke_test_side(
        tmp_path, side_spec, replace_all=False, side="migrated"
    )
    assert result.ok
    assert "x = 99" in result.patched_text


def test_patch_ipynb_preserves_unicode(tmp_path):
    """Patching a notebook with non-ASCII chars preserves them (no \\uXXXX)."""
    conv_root = _setup_ipynb_conv(
        tmp_path, "Output/unicode.ipynb",
        "# caf\u00e9 comment\nx = 1",
    )
    side_spec = {
        "file": "Output/unicode.ipynb",
        "search": "x = 1",
        "replace": "x = 2",
    }
    result = patch_engine.smoke_test_side(
        conv_root, side_spec, replace_all=False, side="migrated"
    )
    assert result.ok
    assert "caf\u00e9" in result.patched_text
    assert "\\u00e9" not in result.patched_text


def test_patch_ipynb_regex_replace_all(tmp_path):
    """regex=true on a notebook rewrites every match across all cells."""
    conv_root = _setup_ipynb_conv(
        tmp_path, "Output/regex.ipynb",
        "dbutils.notebook.exit('a')\nx = 1",
        "dbutils.notebook.exit('b')",
    )
    side_spec = {
        "file": "Output/regex.ipynb",
        "search": r"dbutils\.notebook\.exit\([^)]*\)",
        "replace": "sys.exit(0)",
        "regex": True,
    }
    result = patch_engine.smoke_test_side(
        conv_root, side_spec, replace_all=True, side="migrated"
    )
    assert result.ok, result.error
    assert result.match_count == 2
    patched_nb = json.loads(result.patched_text)
    for cell in patched_nb["cells"]:
        src = "".join(cell["source"])
        assert "dbutils" not in src
        assert "sys.exit(0)" in src


def test_patch_ipynb_regex_ambiguous_fails(tmp_path):
    """regex=true without replace_all is rejected when it matches more than once."""
    conv_root = _setup_ipynb_conv(
        tmp_path, "Output/regex_amb.ipynb",
        "v1 = 1",
        "v2 = 2",
    )
    side_spec = {
        "file": "Output/regex_amb.ipynb",
        "search": r"v\d = \d",
        "replace": "v = 0",
        "regex": True,
    }
    result = patch_engine.smoke_test_side(
        conv_root, side_spec, replace_all=False, side="migrated"
    )
    assert not result.ok
    assert "ambiguous" in result.error.lower()
    assert result.match_count == 2


def test_patch_ipynb_compile_gate_scoped_to_patched_cell(tmp_path):
    """A valid patch to one cell is NOT rejected because an unrelated cell would
    fail translation — the compile gate is scoped to the patched cell(s)."""
    conv_root = _setup_ipynb_conv(
        tmp_path, "Output/mixed.ipynb",
        "x = 1",              # patch target — translates + parses fine
        "def broken(:",       # unrelated, already-unparseable cell
    )
    side_spec = {
        "file": "Output/mixed.ipynb",
        "search": "x = 1",
        "replace": "x = 2",
    }
    result = patch_engine.smoke_test_side(
        conv_root, side_spec, replace_all=False, side="migrated"
    )
    assert result.ok, result.error
    patched_nb = json.loads(result.patched_text)
    assert "x = 2" in "".join(patched_nb["cells"][0]["source"])


# ---------------------------------------------------------------------------
# Notebook migration: migrated-side patch on a .py entry resolves to .py.ipynb
# ---------------------------------------------------------------------------


def test_patch_migrated_py_resolves_to_ipynb(tmp_path):
    """A migrated-side patch keyed on '<name>.py' targets Output/<name>.py.ipynb
    (the actual migrated file) and writes JSON back there, not a bogus .py."""
    out = tmp_path / "Output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "job.py.ipynb").write_text(_make_nb_json("df = spark.read.parquet('/s3/x')"), encoding="utf-8")
    side_spec = {
        "file": "Output/job.py",  # source-style name; migrated file is .py.ipynb
        "search": "spark.read.parquet('/s3/x')",
        "replace": "spark.read.parquet(os.environ['SCOS_INPUT_X'])",
    }
    result = patch_engine.smoke_test_side(
        tmp_path, side_spec, replace_all=False, side="migrated"
    )
    assert result.ok, result.error
    assert result.file == "Output/job.py.ipynb"  # resolved to the real file
    json.loads(result.patched_text)  # still valid notebook JSON
    assert "SCOS_INPUT_X" in result.patched_text


def test_patch_migrated_py_resolves_to_ipynb_with_current_text(tmp_path):
    """When a stacked patch supplies current_text (the working copy from a prior
    patch), a migrated '<name>.py' spec must STILL resolve to the .py.ipynb file
    on disk and take the notebook (per-cell) dispatch — not treat the notebook
    JSON as a flat text file. The fix moves resolution up front, independent of
    current_text."""
    out = tmp_path / "Output"
    out.mkdir(parents=True, exist_ok=True)
    # The real file exists on disk; current_text is the in-flight working copy.
    (out / "job.py.ipynb").write_text(_make_nb_json("a = 1"), encoding="utf-8")
    working = _make_nb_json("a = 1\nb = spark.read.parquet('/s3/y')")
    side_spec = {
        "file": "Output/job.py",  # source-style name
        "search": "spark.read.parquet('/s3/y')",
        "replace": "spark.read.parquet(os.environ['SCOS_INPUT_Y'])",
    }
    result = patch_engine.smoke_test_side(
        tmp_path, side_spec, replace_all=False, side="migrated",
        current_text=working,
    )
    assert result.ok, result.error
    assert result.file == "Output/job.py.ipynb"  # resolved even with current_text
    json.loads(result.patched_text)  # dispatched as notebook JSON, still valid
    assert "SCOS_INPUT_Y" in result.patched_text


def test_add_patches_writes_to_ipynb_not_bogus_py(tmp_path):
    """add_patches must write the patched notebook to Output/<name>.py.ipynb and
    NOT create an Output/<name>.py containing notebook JSON."""
    out = tmp_path / "Output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "job.py.ipynb").write_text(_make_nb_json("x = 1"), encoding="utf-8")
    (tmp_path / "Validation" / "shared").mkdir(parents=True, exist_ok=True)
    ok, results, written, _ = patch_engine.add_patches(tmp_path, [
        {"id": "p1", "relative_file": "job.py",
         "migrated": {"search": "x = 1", "replace": "x = 2"}},
    ])
    assert ok, [r.error for r in results]
    assert "Output/job.py.ipynb" in written
    assert not (out / "job.py").exists()  # no bogus .py with JSON content
    nb = json.loads((out / "job.py.ipynb").read_text())
    assert "x = 2" in "".join(nb["cells"][0]["source"])


# ---------------------------------------------------------------------------
# _check_java_syntax
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not shutil.which("javac"), reason="javac not on PATH")
def test_check_java_syntax_accepts_valid_class():
    valid = "public class Foo {\n    public void run() { System.out.println(1); }\n}\n"
    assert patch_engine._check_java_syntax(valid) is None


@pytest.mark.skipif(not shutil.which("javac"), reason="javac not on PATH")
def test_check_java_syntax_rejects_broken_syntax():
    broken = "public class Foo {\n    public void run() { System.out.println(1) }\n}\n"
    err = patch_engine._check_java_syntax(broken)
    assert err is not None, "missing semicolon must be flagged as a syntax error"


@pytest.mark.skipif(not shutil.which("javac"), reason="javac not on PATH")
def test_check_java_syntax_ignores_classpath_only_errors():
    """A reference to an unresolvable symbol is a classpath error, not a syntax
    error — _check_java_syntax must not flag it (no classpath is set up here)."""
    unresolved = (
        "public class Foo {\n"
        "    public void run() { com.example.Nonexistent.doThing(); }\n"
        "}\n"
    )
    assert patch_engine._check_java_syntax(unresolved) is None


@pytest.mark.skipif(not shutil.which("javac"), reason="javac not on PATH")
def test_check_java_syntax_passes_english_locale_flags_to_javac(monkeypatch):
    """Regression guard: javac error classification matches English substrings
    (e.g. "';' expected"). Without forcing -J-Duser.language=en, a non-English
    JVM default locale would localize javac's message and the substring match
    would silently miss real syntax errors."""
    captured_argv = {}
    real_run = subprocess.run

    def _spy(argv, **kwargs):
        captured_argv["argv"] = argv
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy)
    broken = "public class Foo {\n    public void run() { System.out.println(1) }\n}\n"
    patch_engine._check_java_syntax(broken)
    assert "-J-Duser.language=en" in captured_argv["argv"]
    assert "-J-Duser.country=US" in captured_argv["argv"]



