"""Exercise the trusted checker with synthetic Git objects, events, and YAML.

Run trusted tests with python3 -I -B -m unittest discover. The explicit import
below loads the adjacent trusted checker, not a module inside a candidate repo.
Parsing tests call its helpers; Git tests create local temporary repositories,
commit fixtures, and inspect their stored objects; event tests mock network
fetches; workflow tests parse the checked-in workflow and exercise its bootstrap
shell fragment in a temporary directory. TemporaryDirectory/addCleanup remove
fixtures even on assertion failure, and environment patches are also restored.

Only trusted test code, the trusted checker, local Git, and the trusted bootstrap
fragment execute. Candidate scripts contain marker-writing text as inert fixture
bytes: marker absence checks that the tested paths did not execute them. Some
tests launch the trusted interpreter with a candidate working directory to check
import isolation. These are targeted regressions, not proof against all code
execution or all hostile environments.

Mocked failures and reduced budgets make boundary/error cases deterministic;
they do not simulate every OS scheduling or network condition. Action pins and
wheel provenance are compared to fixed expected metadata, not queried live.
No test contacts GitHub/PyPI or proves that a PR-editable workflow is an
authoritative merge gate. Each test returns None; assertion failures identify
regressions, while unexpected fixture/process errors fail the test directly.
"""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

# Resolve relative to this trusted test file, never relative to fixture cwd.
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_skill_structure.py"
spec = importlib.util.spec_from_file_location("skill_structure", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
# Minimal accepted frontmatter deliberately omits optional legacy metadata.
VALID = b"---\nname: Example\ndescription: |\n  Useful multiline description.\n---\n# Instructions\n"


class ParsingTests(unittest.TestCase):
    """Check parsing, metadata, and rendering with in-memory synthetic inputs.

    No Git process is needed here; protocol tests replace Git.run with canned
    bytes. Expected Invalid/Incomplete exceptions distinguish rejected content
    from an inspection that cannot finish, rather than treating both as success.
    """

    def test_safe_yaml_and_known_metadata(self):
        """Accept multiline YAML strings and the supported optional field shapes."""
        data = checker.frontmatter(VALID.decode().replace("---\n#", "tools: [Read, Bash]\nauthors: [Ada]\ncategories: example\nid: example\n---\n#"))
        report = checker.Report()
        checker.metadata(data, "example", report, b"skills/example/SKILL.md")
        self.assertEqual(report.status, 0)
        self.assertIn("Useful multiline", data["description"])

    def test_rejected_yaml(self):
        """Reject ambiguous mappings, aliases, unsafe/custom tags, and bad YAML."""
        for body in ("name: first\nname: second", "x: &anchor [a]\ny: *anchor",
                     "x: !custom value", "x: !!python/object/apply:os.system [false]",
                     "x: {nested: 1, nested: 2}", "? [a, b]\n: c", "[a, b]", "null",
                     "x: [unclosed", "x: {<<: {name: example}}"):
            with self.subTest(body=body), self.assertRaises(checker.Invalid):
                checker.frontmatter("---\n" + body + "\n---\n")

    def test_delimiters_and_empty_document(self):
        """Require exact leading/closing delimiters around a nonempty mapping."""
        for content in ("", "---suffix\nname: a\n---", "---\nname: a", "---\n---\n"):
            with self.subTest(content=content), self.assertRaises(checker.Invalid):
                checker.frontmatter(content)

    def test_yaml_budgets(self):
        """Report incomplete for both deep and wide YAML, not structural success."""
        for body in ("x: " + "[" * 13 + "a" + "]" * 13,
                     "x: [" + ",".join("a" for _ in range(checker.MAX_NODES)) + "]"):
            with self.assertRaises(checker.Incomplete):
                checker.frontmatter("---\n" + body + "\n---")

    def test_types_required_fields_and_optional_id(self):
        """Fail wrong field types, blank required values, and mismatched IDs."""
        for patch in ({"name": " "}, {"description": False}, {"name": []},
                      {"tools": "Read"}, {"tools": [1]}, {"authors": {}},
                      {"categories": [None]}, {"title": 12}, {"status": True},
                      {"id": "other"}, {"id": "example-"}, {"id": None}):
            with self.subTest(patch=patch):
                report = checker.Report()
                checker.metadata({"name": "Example", "description": "Valid"} | patch,
                                 "example", report, b"p")
                self.assertEqual(report.status, 1)
        report = checker.Report()
        checker.metadata({}, "example", report, b"p")
        self.assertEqual(report.status, 1)

    def test_enums_lengths_and_unknown_fields_are_advisory(self):
        """Keep legacy enum/length advice nonblocking and ignore extension fields."""
        report = checker.Report()
        checker.metadata({"name": "Human readable name", "description": "Text",
                          "type": "future", "status": "future", "language": "future",
                          "title": "x" * 31, "summary": "x" * 141,
                          "extension": {"anything": [1, 2]}}, "example", report, b"p")
        self.assertEqual(report.status, 0)
        # Three enum warnings plus two length warnings; the unknown extension
        # contributes no diagnostic and none of these warnings changes status.
        self.assertEqual(len(report.lines), 5)

    def test_malformed_git_outputs_never_pass(self):
        """Use canned Git replies to reject framing, size, and identity mismatches."""
        git = checker.Git("/unused")
        for raw in (b"missing NUL", b"bad\0", b"100644 blob " + b"a" * 40 + b"\ta/b\0"):
            with self.subTest(raw=raw), mock.patch.object(git, "run", return_value=raw), \
                    self.assertRaises(checker.Incomplete):
                git.tree("a" * 40)
        for replies in ((b"unknown\n",), (b"2\n", b"a")):
            with mock.patch.object(git, "run", side_effect=replies), self.assertRaises(checker.Incomplete):
                git.text("a" * 40, 100)
        with mock.patch.object(git, "commit"), mock.patch.object(git, "run", return_value=b"skills/a/x"), \
                self.assertRaises(checker.Incomplete):
            checker.inspect(git, "a" * 40, "b" * 40, checker.Report())
        with mock.patch.object(git, "run", side_effect=(b"commit\n", b"b" * 40 + b"\n")), \
                self.assertRaises(checker.Incomplete):
            git.commit("a" * 40)

    def test_paths_and_diagnostics(self):
        """Reject malformed path bytes and render diagnostic punctuation inert."""
        for path in (b"skills/a/../b", b"skills//b", b"skills/a\n::error::x", b"skills/\xff",
                     "skills/a/\u202eevil".encode(), b"skills/a/" + b"x" * 1024):
            with self.subTest(path=path), self.assertRaises(checker.Incomplete):
                checker.check_path(path)
        report = checker.Report()
        report.add("fail", "fixed", b"skills/\n::error::`<img>$(touch p)")
        line = report.lines[0]
        self.assertNotIn("\n", line)
        self.assertNotIn("::error::", line)
        self.assertNotIn("`", line)
        self.assertNotIn("<", line)
        with mock.patch.object(checker, "MAX_DIAGNOSTICS", 1), self.assertRaises(checker.Incomplete):
            report.add("fail", "second")


class GitTests(unittest.TestCase):
    """Inspect real local Git objects created solely from synthetic fixtures.

    Each test gets a fresh repository and empty base commit. Fixture creation
    uses a working tree for convenience; the checker must read committed object
    data instead. Process/import tests execute only the trusted checker. Cleanup
    is registered before setup work so later setup/assertion errors do not leave
    temporary repositories behind. No helper here performs a network fetch.
    """

    def setUp(self):
        """Create a disposable repository/base commit and register cleanup.

        Return None; filesystem or Git setup errors fail the test rather than
        being interpreted as a checker result.
        """
        self.temp = tempfile.TemporaryDirectory(prefix="structure-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository"
        self.repo.mkdir()
        self.git("init", "--quiet", "--template=")
        self.base = self.commit()

    def git(self, *args, data=None):
        """Run trusted fixture Git argv with optional stdin bytes; return stripped stdout.

        Use synthetic author identity and suppress ambient config/hooks/signing.
        Calls are local fixture operations, not arbitrary candidate commands;
        this helper does not itself disable every network protocol. Nonzero
        exit, OS errors, or the ten-second timeout fail the test directly.
        """
        # No global configuration, hooks, or signing for fixture creation.
        env = {"PATH": os.defpath, "HOME": str(self.root), "LC_ALL": "C",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_AUTHOR_NAME": "Synthetic Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
               "GIT_COMMITTER_NAME": "Synthetic Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
        return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull,
                               "-c", "commit.gpgSign=false", "-C", str(self.repo), *args],
                              input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, check=True, timeout=10).stdout.strip()

    def commit(self):
        """Stage fixture changes and return a new commit SHA, even if empty.

        Uses only the temporary repository; Git failures propagate to the test.
        """
        self.git("add", "--all")
        self.git("commit", "--allow-empty", "--quiet", "-m", "synthetic fixture")
        return self.git("rev-parse", "HEAD").decode()

    def put(self, name, content):
        """Write fixture bytes at a trusted relative name and return its Path.

        Create missing parents; filesystem errors propagate. This is test setup,
        not a path-safe writer for arbitrary candidate-controlled filenames.
        """
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def skill(self, name="example", content=VALID, license=b"Unreviewed license text\n"):
        """Write a synthetic skill's two required files; return None.

        Arguments let tests substitute malformed text or names. Defaults satisfy
        structure only, not license approval. Fixture write errors propagate.
        """
        self.put(f"skills/{name}/SKILL.md", content)
        self.put(f"skills/{name}/LICENSE", license)

    def run_check(self, head=None, base=None, extra=()):
        """Return (exit status, captured stdout) for trusted offline checker argv.

        Use explicit revisions when supplied; otherwise commit current fixtures
        for head and use the setup base. extra supplies trusted CLI arguments.
        Checker-handled failures become statuses; fixture/unexpected errors
        propagate. This helper never launches candidate scripts.
        """
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = checker.main(["--repository", str(self.repo), "--base", base or self.base,
                                   "--head", head or self.commit(), *extra])
        return status, output.getvalue()

    def test_valid_executable_binary_and_no_candidate_execution(self):
        """Allow support blobs without reading them or executing a marked script."""
        self.skill()
        marker = self.root / "executed"
        executable = self.put("skills/example/run.py", f"open({str(marker)!r}, 'w').write('bad')".encode())
        executable.chmod(0o755)
        self.put("skills/example/image.bin", b"\xff\0" * 200000)
        original = checker.Git.run
        calls = []

        def recorded(git, *args, **kwargs):
            """Record plumbing argv, return real Git output, and propagate failures."""
            calls.append(args)
            return original(git, *args, **kwargs)

        with mock.patch.object(checker.Git, "run", recorded):
            status, output = self.run_check()
        self.assertEqual(status, 0, output)
        self.assertFalse(marker.exists())
        # Exactly two reads and size probes belong to SKILL.md and LICENSE,
        # not the executable or large binary support blob. Marker absence alone
        # would not prove those support contents were never read.
        self.assertEqual(sum(args[:2] == ("cat-file", "blob") for args in calls), 2)
        self.assertEqual(sum(args[:2] == ("cat-file", "-s") for args in calls), 2)
        self.assertIn("--no-renames", next(args for args in calls if args[0] == "diff"))

    def test_no_changes_and_automation_edits_have_no_influence(self):
        """Keep candidate automation/import traps inert, including with candidate cwd."""
        marker = self.root / "imported"
        poison = f"open({str(marker)!r}, 'w').write('bad')\nraise RuntimeError('candidate')\n".encode()
        for name in (".github/scripts/check_skill_structure.py", ".github/tests/test_skill_structure.py",
                     "yaml.py", "sitecustomize.py", "setup.py"):
            self.put(name, poison)
        self.put(".github/requirements-skill-check.txt", b"malicious-dependency==0\n")
        self.put(".github/workflows/skill-structure.yml", b"untrusted workflow")
        head = self.commit()
        status, output = self.run_check(head)
        self.assertEqual(status, 0, output)
        self.assertIn("no relevant", output)
        self.skill(license=b"")
        head = self.commit()
        # Execute the absolute trusted script, never the candidate copy. The
        # empty LICENSE must still fail even beside fake YAML/startup modules.
        result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--repository", str(self.repo),
                                 "--base", self.base, "--head", head], cwd=self.repo,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(marker.exists())

    def test_missing_deleted_and_entire_skill_deletion(self):
        """Fail partial required-file deletion but permit deleting an entire skill."""
        self.skill()
        base = self.commit()
        (self.repo / "skills/example/LICENSE").unlink()
        status, _ = self.run_check(base=base)
        self.assertEqual(status, 1)
        shutil.rmtree(self.repo / "skills/example")
        status, output = self.run_check(base=base)
        self.assertEqual(status, 0, output)
        self.assertIn("skill deleted", output)
        self.skill()
        (self.repo / "skills/example/SKILL.md").unlink()
        self.assertEqual(self.run_check(base=base)[0], 1)

    def test_rename_inspects_old_and_new_roots(self):
        """Treat a rename as deletion plus full inspection of the retained new root."""
        self.skill()
        base = self.commit()
        (self.repo / "skills/example").rename(self.repo / "skills/renamed")
        status, output = self.run_check(base=base)
        self.assertEqual(status, 0, output)
        self.assertIn("skill deleted path=skills/example", output)
        self.assertIn("retained skill path=skills/renamed", output)

    def test_whole_tree_on_support_only_change(self):
        """Find an unchanged symlink when another file makes the skill relevant."""
        self.skill()
        (self.repo / "skills/example/hidden-link").symlink_to("/outside")
        base = self.commit()
        self.put("skills/example/support.txt", b"only changed file")
        status, output = self.run_check(base=base)
        self.assertEqual(status, 1, output)
        self.assertIn("hidden-link", output)

    def test_direct_child_and_parent_replacements(self):
        """Reject non-directory replacements of a skill root or the skills parent."""
        for mode in ("symlink", "blob", "parent"):
            with self.subTest(mode=mode):
                skills = self.repo / "skills"
                if skills.is_symlink() or skills.is_file():
                    skills.unlink()
                elif skills.exists():
                    shutil.rmtree(skills)
                self.skill()
                base = self.commit()
                target = skills if mode == "parent" else skills / "example"
                shutil.rmtree(target)
                if mode == "blob":
                    target.write_bytes(b"regular blob cannot replace a skill directory")
                else:
                    target.symlink_to("/outside")
                self.assertEqual(self.run_check(base=base)[0], 1)

    def test_submodules_direct_child_and_nested(self):
        """Reject synthetic gitlink entries both at skill roots and below them."""
        self.skill()
        base = self.commit()
        # Seed mode 160000 directly: no real submodule checkout or remote is
        # needed to exercise the stored Git representation of a submodule.
        for path in ("skills/example/submodule", "skills/submodule"):
            self.git("update-index", "--add", "--cacheinfo", f"160000,{base},{path}")
        self.git("commit", "--quiet", "-m", "synthetic gitlinks")
        status, output = self.run_check(self.git("rev-parse", "HEAD").decode(), base)
        self.assertEqual(status, 1, output)
        self.assertIn("only regular blobs", output)
        self.assertIn("skill root must be a directory", output)

    def test_required_directories_and_utf8(self):
        """Require regular nonblank UTF-8 files, not directories with matching names."""
        self.skill()
        (self.repo / "skills/example/LICENSE").unlink()
        self.put("skills/example/LICENSE/nested", b"not a license file")
        self.assertEqual(self.run_check()[0], 1)
        shutil.rmtree(self.repo / "skills/example/LICENSE")
        for filename, contents in (("LICENSE", b"\xff"), ("LICENSE", b" \n"), ("SKILL.md", b"\xff")):
            with self.subTest(filename=filename, contents=contents):
                self.skill()
                self.put("skills/example/" + filename, contents)
                self.assertEqual(self.run_check()[0], 1)

    def test_sizes_checked_before_blob_read(self):
        """Stop at oversized required-blob metadata without requesting its contents."""
        original = checker.Git.run
        for filename, limit in (("SKILL.md", checker.MAX_SKILL), ("LICENSE", checker.MAX_LICENSE)):
            self.skill()
            self.put("skills/example/" + filename, b"x" * (limit + 1))
            head = self.commit()
            oversized = self.git("rev-parse", f"{head}:skills/example/{filename}").decode()
            calls = []

            def recorded(git, *args, **kwargs):
                """Capture Git argv, delegate unchanged, and propagate real failures."""
                calls.append(args)
                return original(git, *args, **kwargs)

            with mock.patch.object(checker.Git, "run", recorded):
                status, output = self.run_check(head)
            self.assertEqual(status, 2, output)
            self.assertNotIn(("cat-file", "blob", oversized), calls)

    def test_limits(self):
        """Report incomplete beyond entry, directory-depth, or affected-skill caps."""
        self.skill()
        # The two required files count too: 499 support entries make 501 total.
        for index in range(499):
            self.put(f"skills/example/f{index}", b"")
        self.assertEqual(self.run_check()[0], 2)
        shutil.rmtree(self.repo / "skills")
        self.skill()
        self.put("skills/example/" + "d/" * 12 + "leaf", b"")
        self.assertEqual(self.run_check()[0], 2)
        shutil.rmtree(self.repo / "skills")
        for index in range(21):
            self.skill(f"skill-{index}")
        self.assertEqual(self.run_check()[0], 2)

    def test_budget_boundaries(self):
        """Accept exactly bounded entry counts, required-file sizes, and tree depth."""
        self.skill()
        # Include SKILL.md/LICENSE in the 500-entry total. The root is depth 1,
        # so eleven nested directories reach the permitted depth of twelve.
        for index in range(498):
            self.put(f"skills/example/f{index}", b"")
        self.assertEqual(self.run_check()[0], 0)
        shutil.rmtree(self.repo / "skills")
        self.skill(content=VALID + b"x" * (checker.MAX_SKILL - len(VALID)),
                   license=b"x" * checker.MAX_LICENSE)
        self.put("skills/example/" + "d/" * 11 + "leaf", b"")
        self.assertEqual(self.run_check()[0], 0)

    def test_shell_markdown_names_and_summary_are_inert(self):
        """Encode permitted punctuation and reject controls in console/summary paths."""
        self.skill("$(touch marker); `x` <img>")
        summary = self.root / "summary.md"
        status, output = self.run_check(extra=("--summary-trusted", str(summary)))
        self.assertEqual(status, 0, output)
        self.assertNotIn("$(touch", output)
        self.assertNotIn("<img>", output)
        self.assertEqual(summary.read_text(), "```text\n" + output + "```\n")
        self.assertFalse((self.repo / "marker").exists())
        self.skill("bad\n::error::injected")
        status, output = self.run_check()
        self.assertEqual(status, 2)
        self.assertNotIn("::error::", output)

    def test_missing_objects_bad_sha_and_blob_revision(self):
        """Reject symbolic, missing, injected, or non-commit revisions without echoing them."""
        self.skill()
        head = self.commit()
        blob = self.git("rev-parse", f"{head}:skills/example/SKILL.md").decode()
        for bad in ("HEAD", head[:7], "0" * 40, head + ";touch marker", blob):
            with self.subTest(bad=bad):
                status, output = self.run_check(head, base=bad)
                self.assertEqual(status, 2, output)
                self.assertNotIn(";touch", output)

    def test_process_timeout_failure_and_output_limits(self):
        """Exercise timeout, byte budgets, bad commands, and mocked launch failure."""
        # Shrink limits to force deterministic failure paths on small local
        # operations; this does not reproduce a live fetch or stuck helper tree.
        with mock.patch.object(checker, "GIT_TIMEOUT", 0):
            self.assertEqual(self.run_check(self.base)[0], 2)
        with mock.patch.object(checker, "MAX_TOTAL", 1):
            self.assertEqual(self.run_check(self.base)[0], 2)
        with self.assertRaises(checker.Incomplete):
            checker.Git(self.repo).run("status", limit=1)
        with self.assertRaises(checker.Incomplete):
            checker.Git(self.repo).run("not-a-git-subcommand")
        with mock.patch.object(checker.subprocess, "Popen", side_effect=OSError):
            self.assertEqual(self.run_check(self.base)[0], 2)

    def test_empty_git_tree_skill_is_not_invisible(self):
        """Detect an empty skill tree even though Git's file diff has no entries."""
        # Ordinary git add cannot preserve an empty directory. Build object
        # trees directly so the test reaches the direct-child comparison path.
        empty = self.git("mktree", data=b"").decode()
        skills = self.git("mktree", data=f"040000 tree {empty}\tempty-skill\n".encode()).decode()
        root = self.git("mktree", data=f"040000 tree {skills}\tskills\n".encode()).decode()
        head = self.git("commit-tree", root, "-p", self.base, "-m", "synthetic empty tree").decode()
        self.assertEqual(self.git("diff", "--name-only", self.base, head), b"")
        status, output = self.run_check(head)
        self.assertEqual(status, 1, output)
        self.assertIn("missing required regular file", output)

    def test_mode_only_change_and_untracked_files(self):
        """Select committed mode changes while ignoring later working-tree edits."""
        self.skill()
        base = self.commit()
        (self.repo / "skills/example/SKILL.md").chmod(0o755)
        head = self.commit()
        # Despite the fixture text's label, this is an uncommitted modification
        # of a tracked file. The requested commit still contains valid content.
        self.put("skills/example/SKILL.md", b"untracked invalid content")
        status, output = self.run_check(head, base)
        self.assertEqual(status, 0, output)
        self.assertIn("retained skill", output)
        status, output = self.run_check(head, head)
        self.assertEqual(status, 0, output)
        self.assertIn("no relevant", output)

    def test_unchanged_invalid_skill_not_selected(self):
        """Leave an unrelated unchanged invalid skill outside the selected scope."""
        self.skill("untouched", content=b"invalid")
        base = self.commit()
        self.skill("changed")
        status, output = self.run_check(base=base)
        self.assertEqual(status, 0, output)
        self.assertNotIn("untouched", output)

    def test_summary_env_ignored_and_write_failure_is_incomplete(self):
        """Require an explicit summary path and fail incomplete if writing it fails."""
        summary = self.root / "untrusted-summary"
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            self.assertEqual(self.run_check(self.base)[0], 0)
        self.assertFalse(summary.exists())
        status, output = self.run_check(self.base, extra=("--summary-trusted", str(self.root)))
        self.assertEqual(status, 2)
        self.assertIn("Result: inspection incomplete", output)
        self.assertNotIn("Result: structure valid", output)

    def test_missing_yaml_returns_incomplete_without_traceback(self):
        """Hide site packages to check the fixed missing-dependency exit/diagnostic."""
        # -S suppresses site initialization for this trusted subprocess only;
        # no package is uninstalled or replaced to manufacture the failure.
        result = subprocess.run([sys.executable, "-I", "-B", "-S", str(SCRIPT)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("dependency is unavailable", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_diagnostic_omission_is_incomplete(self):
        """Escalate truncated findings to incomplete rather than a complete failure list."""
        self.skill(content=b"---\nname: 1\ndescription: false\n---")
        with mock.patch.object(checker, "MAX_DIAGNOSTICS", 1):
            status, output = self.run_check()
        self.assertEqual(status, 2)
        self.assertIn("results omitted", output)


class EventTests(unittest.TestCase):
    """Validate synthetic event wiring and temporary-fetch lifecycle without network.

    Mock fetch/Git responses to test argument construction, head mismatch, and
    cleanup. These checks do not establish remote availability, TLS behavior,
    or real concurrent GitHub ref changes. Unexpected fixture errors fail tests.
    """

    def setUp(self):
        """Create a disposable event path and patched trusted repository identity.

        Register directory/environment cleanup; return None. The event includes
        hostile-looking unused head fields to expose accidental use of them.
        Filesystem errors propagate as test errors.
        """
        self.temp = tempfile.TemporaryDirectory(prefix="structure-events-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "event.json"
        self.base, self.head = "a" * 40, "b" * 40
        self.event = {"number": 7, "repository": {"full_name": "owner/repository"},
                      "pull_request": {"base": {"sha": self.base, "repo": {"full_name": "owner/repository"}},
                                       "head": {"sha": self.head, "ref": "$(touch marker)",
                                                "repo": {"clone_url": "https://evil.invalid/repo"}}}}
        self.env = mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repository"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def event_args(self):
        """Serialize the current fixture event and return its trusted CLI arguments.

        Tests mutate self.event before calling; JSON/write errors propagate.
        """
        self.path.write_text(json.dumps(self.event))
        return ["--event", str(self.path), "--expected-repository", "owner/repository"]

    def test_valid_event_and_fixed_fetch_arguments(self):
        """Construct a fixed public fetch and reject a mocked head identity change."""
        self.event_args()
        self.assertEqual(checker.event_revisions(self.path, "owner/repository"), (self.base, self.head, 7))
        # This verifies argv/ref identity only: no real remote is contacted.
        git = mock.Mock()
        git.run.return_value = (self.head + "\n").encode()
        checker.fetch_public(git, "owner/repository", 7, self.base, self.head)
        fetch = git.run.call_args_list[1]
        self.assertIn("https://github.com/owner/repository.git", fetch.args)
        self.assertIn("refs/pull/7/head:refs/skill-check/head", fetch.args)
        self.assertNotIn("evil.invalid", str(git.run.call_args_list))
        self.assertTrue(fetch.kwargs["network"])
        git.run.return_value = b"c" * 40 + b"\n"
        with self.assertRaises(checker.Incomplete):
            checker.fetch_public(git, "owner/repository", 7, self.base, self.head)

    def test_event_validation(self):
        """Reject bad PR numbers, inconsistent repositories, and symbolic head refs."""
        for number in (True, 0, -1, 2147483648, "7;touch marker"):
            self.event["number"] = number
            self.event_args()
            with self.assertRaises(checker.Incomplete):
                checker.event_revisions(self.path, "owner/repository")
        self.event["number"] = 7
        self.event_args()
        for expected in (None, "other/repository", "https://github.com/owner/repository", "owner/.."):
            with self.assertRaises(checker.Incomplete):
                checker.event_revisions(self.path, expected)
        self.event["pull_request"]["number"] = True
        self.event_args()
        with self.assertRaises(checker.Incomplete):
            checker.event_revisions(self.path, "owner/repository")
        del self.event["pull_request"]["number"]
        self.event["pull_request"]["head"]["sha"] = "HEAD"
        self.event_args()
        with self.assertRaises(checker.Incomplete):
            checker.event_revisions(self.path, "owner/repository")
        self.event["pull_request"]["head"]["sha"] = self.head
        self.event["repository"]["full_name"] = "other/repository"
        self.event_args()
        with self.assertRaises(checker.Incomplete):
            checker.event_revisions(self.path, "owner/repository")

    def test_no_network_without_flag_and_private_temporary_fetch(self):
        """Require opt-in fetch and remove its private temporary directory afterward."""
        args = self.event_args()
        with mock.patch.object(checker, "fetch_public") as fetch, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(checker.main(args), 2)
            fetch.assert_not_called()
        locations = []

        def fake_fetch(git, *unused):
            """Record the fetch directory and assert mode 0700; return no objects.

            Other fetch inputs are unused; inspect is also mocked, so success
            verifies lifecycle wiring only. A mode mismatch fails the test.
            """
            locations.append(Path(git.repository))
            self.assertEqual(locations[0].stat().st_mode & 0o777, 0o700)

        with mock.patch.object(checker, "fetch_public", side_effect=fake_fetch), \
                mock.patch.object(checker, "inspect") as inspect, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(checker.main(args + ["--fetch-public"]), 0)
            inspect.assert_called_once()
        self.assertFalse(locations[0].exists())

    def test_failed_fetch_cleans_up_and_does_not_inspect(self):
        """Clean up after a mocked fetch failure and never inspect absent objects."""
        args = self.event_args()
        locations = []

        def failed_fetch(git, *unused):
            """Record the temporary path, then raise a fixed synthetic Incomplete.

            Ignore remaining fetch arguments; no network or Git work occurs.
            """
            locations.append(Path(git.repository))
            raise checker.Incomplete("synthetic fetch failure")

        with mock.patch.object(checker, "fetch_public", side_effect=failed_fetch), \
                mock.patch.object(checker, "inspect") as inspect, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(checker.main(args + ["--fetch-public"]), 2)
            inspect.assert_not_called()
        self.assertFalse(locations[0].exists())

    def test_invalid_json_size_and_cli_combinations(self):
        """Reject unreadable event shapes, oversized data, and conflicting CLI modes."""
        for raw in (b"{", b"null", b"[]", b"\xff", b"x" * (checker.MAX_OUTPUT + 1)):
            self.path.write_bytes(raw)
            with self.assertRaises(checker.Incomplete):
                checker.event_revisions(self.path, "owner/repository")
        args = self.event_args()
        for extra in (["--base", "c" * 40], ["--fetch-public", "--repository", self.temp.name]):
            with mock.patch.object(checker, "fetch_public") as fetch, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(checker.main(args + extra), 2)
                fetch.assert_not_called()


class WorkflowContractTests(unittest.TestCase):
    """Check the local workflow/lock contract, not live service behavior or authority.

    Parsed mappings and source-string assertions pin the intended wiring and
    reject unexpected steps/options. Literal action IDs and wheel metadata are
    regression expectations, not live verification of release provenance. Only
    the bootstrap fragment is executed locally, against disposable fixtures;
    checkout, setup-python, installation, and network inspection are not run.
    """

    @classmethod
    def setUpClass(cls):
        """Read the trusted workflow once and expose its parsed job/steps to tests.

        Return None; file, YAML, or missing-key errors fail class setup. BaseLoader
        preserves GitHub's 'on' key and scalar spelling for exact comparisons.
        """
        cls.github = SCRIPT.parents[1]
        cls.source = (cls.github / "workflows/skill-structure.yml").read_text(encoding="utf-8")
        # BaseLoader preserves GitHub's YAML 'on' key and scalar values as strings.
        cls.workflow = checker.yaml.load(cls.source, Loader=checker.yaml.BaseLoader)
        cls.job = cls.workflow["jobs"]["structure"]
        cls.steps = cls.job["steps"]

    def test_pr_events_without_path_filter_or_privileged_trigger(self):
        """Pin ordinary PR events and prevent filters from silently skipping checks."""
        self.assertEqual(set(self.workflow), {"name", "on", "permissions", "concurrency", "jobs"})
        self.assertEqual(self.workflow["on"], {"pull_request": {"types": [
            "opened", "synchronize", "reopened", "ready_for_review", "edited"]}})
        self.assertIn("advisory", self.workflow["name"])

    def test_single_read_only_hosted_job_and_bounded_concurrency(self):
        """Pin read-only token permissions, runner, timeout, and per-PR cancellation."""
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(self.workflow["concurrency"], {
            "group": "skill-structure-${{ github.event.pull_request.number }}",
            "cancel-in-progress": "true"})
        self.assertEqual(set(self.workflow["jobs"]), {"structure"})
        self.assertEqual(set(self.job), {"name", "runs-on", "timeout-minutes", "env", "defaults", "steps"})
        self.assertEqual(self.job["runs-on"], "ubuntu-24.04")
        self.assertEqual(self.job["timeout-minutes"], "5")
        self.assertIn("advisory", self.job["name"])
        self.assertEqual(self.job["defaults"], {"run": {
            "shell": "bash", "working-directory": "${{ github.workspace }}"}})

    def test_only_verified_actions_and_no_extra_steps(self):
        """Compare action IDs and step shapes to fixed reviewed contract expectations."""
        self.assertEqual(len(self.steps), 5)
        self.assertEqual([step["uses"] for step in self.steps if "uses" in step], [
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"])
        for index, step in enumerate(self.steps):
            self.assertEqual(set(step), {"name", "uses", "with"} if index in (0, 2)
                             else {"name", "run"})

    def test_checkout_is_exact_base_repository_revision_and_only_two_files(self):
        """Pin the base checkout's two-file sparse patterns and expansion restrictions."""
        self.assertEqual(self.steps[0]["with"], {
            "repository": "${{ github.repository }}",
            "ref": "${{ github.event.pull_request.base.sha }}",
            "path": "trusted",
            "sparse-checkout": "/.github/scripts/check_skill_structure.py\n"
                               "/.github/requirements-skill-check.txt\n",
            "sparse-checkout-cone-mode": "false", "fetch-depth": "1",
            "persist-credentials": "false", "submodules": "false", "lfs": "false"})

    def test_python_version_architecture_no_cache_or_candidate_version_file(self):
        """Pin explicit Python/architecture inputs rather than a candidate version file."""
        self.assertEqual(self.steps[2]["with"], {"python-version": "3.12", "architecture": "x64"})
        self.assertEqual(self.job["env"], {
            "EXPECTED_REPOSITORY": "${{ github.repository }}", "PIP_CONFIG_FILE": "/dev/null"})

    def test_install_only_trusted_lock_isolated_hashes_and_wheels(self):
        """Compare install argv to isolated, hash-required, binary-only lock usage."""
        command = shlex.split(self.steps[3]["run"].replace("\\\n", ""))
        self.assertEqual(command, [
            "python", "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check",
            "install", "--require-hashes", "--only-binary=:all:", "--no-cache-dir",
            "--index-url", "https://pypi.org/simple", "-r",
            "$GITHUB_WORKSPACE/trusted/.github/requirements-skill-check.txt"])

    def test_checker_invocation_uses_trusted_paths_context_and_no_candidate_execution(self):
        """Pin checker argv and reject dangerous workflow source-wiring regressions."""
        command = shlex.split(self.steps[4]["run"].replace("\\\n", ""))
        self.assertEqual(command, [
            "python", "-I", "-B", "$GITHUB_WORKSPACE/trusted/.github/scripts/check_skill_structure.py",
            "--event", "$GITHUB_EVENT_PATH", "--expected-repository", "$EXPECTED_REPOSITORY",
            "--fetch-public", "--summary-trusted", "$GITHUB_STEP_SUMMARY"])
        # These source checks are deliberately literal and also see comments.
        # Keep documentation accurate without introducing forbidden wiring text.
        # Absence of these strings is a regression check, not a security proof.
        # No inline event/title/path interpolation in shell, custom tokens, or local actions.
        for step in self.steps:
            if "run" in step:
                self.assertNotIn("${{", step["run"])
        self.assertNotIn("secrets.", self.source)
        self.assertNotIn("github.token", self.source)
        self.assertNotIn("github.event.pull_request.head", self.source)
        self.assertNotIn("continue-on-error", self.source)

    def test_dependency_lock_matches_official_pypi_wheel_metadata(self):
        """Match recorded wheel identity/hash/provenance, without querying PyPI live."""
        lock = (self.github / "requirements-skill-check.txt").read_text(encoding="utf-8")
        requirement = "\n".join(line for line in lock.splitlines() if not line.startswith("#"))
        self.assertEqual(shlex.split(requirement.replace("\\\n", "")), [
            "PyYAML==6.0.3",
            "--hash=sha256:ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc"])
        self.assertIn("https://pypi.org/pypi/PyYAML/6.0.3/json", lock)
        self.assertIn("pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64."
                      "manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl", lock)
        self.assertIn("requires_dist=null", lock)

    def test_bootstrap_fails_missing_files_and_symlinks_without_candidate_fallback(self):
        """Execute trusted bootstrap guards against missing files and final symlinks."""
        script = self.steps[1]["run"]
        with tempfile.TemporaryDirectory(prefix="structure-bootstrap-") as directory:
            root = Path(directory)
            trusted = root / "trusted/.github"
            (trusted / "scripts").mkdir(parents=True)
            required = [trusted / "scripts/check_skill_structure.py",
                        trusted / "requirements-skill-check.txt"]
            # A candidate checker/lock elsewhere must not satisfy the bootstrap check.
            candidate = root / ".github"
            (candidate / "scripts").mkdir(parents=True)
            (candidate / "scripts/check_skill_structure.py").write_text("raise RuntimeError('candidate')")
            (candidate / "requirements-skill-check.txt").write_text("candidate-package==0")

            def run_bootstrap():
                """Run the trusted workflow guard in the fixture directory; return result.

                No candidate script is executed. Nonzero exits are asserted by
                callers; launch errors or the five-second timeout fail the test.
                """
                return subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                                      cwd=root, env={"PATH": os.defpath, "GITHUB_WORKSPACE": directory},
                                      capture_output=True, text=True, timeout=5)

            for path in required:
                result = run_bootstrap()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("[incomplete]", result.stderr)
                self.assertIn("Bootstrap:", result.stderr)
                self.assertIn("no candidate fallback", result.stderr)
                path.write_text("trusted fixture")
            # Arbitrary regular-file text suffices: this guard checks presence
            # and final-component type, not code content or ancestor symlinks.
            self.assertEqual(run_bootstrap().returncode, 0)
            for path in required:
                path.unlink()
                path.symlink_to(candidate / "requirements-skill-check.txt")
                self.assertEqual(run_bootstrap().returncode, 2)
                path.unlink()
                path.write_text("trusted fixture")

    def test_documented_legacy_and_new_metadata_values_are_compatible(self):
        """Accept supported legacy/current enums and attribution shapes without advice."""
        for status in ("stable", "beta", "draft", "Published", "Archived", "Hidden"):
            for category in ("community", "snowflake", "partner"):
                for attribution in ("Ada", ["Ada"]):
                    with self.subTest(status=status, category=category, attribution=attribution):
                        report = checker.Report()
                        checker.metadata({"name": "Example", "description": "Useful",
                                          "status": status, "type": category, "authors": attribution,
                                          "categories": attribution, "tags": attribution,
                                          "allowed-tools": attribution}, "example", report, b"p")
                        self.assertEqual(report.status, 0)
                        self.assertEqual(report.lines, [])


if __name__ == "__main__":
    unittest.main()
