#!/usr/bin/env python3
"""Portable syntax-check gate and git-revert tool for Java files produced
during Phase 2 of the SCOS Java migration skill.

Runs identically on macOS, Linux, and Windows under ``uv run``.

Usage::

    uv run --project <SKILL_DIRECTORY> \
        python <SKILL_DIRECTORY>/scripts/revert_failing_java_files.py \
        --migrated <MIGRATED_DIR> \
        --phase-tag phase-1-complete \
        [--json]

Behaviour::

    Compilation is **batch-first**: a single ``javac -proc:none`` invocation is
    run over every *.java file at once. If that batch exits 0, every file passes
    and no per-file work is done (one JVM start instead of N — the common,
    healthy case). Only when the batch reports errors (or javac cannot run) does
    the tool fall back to per-file checking to attribute failures precisely.

    Per-file modes (used on the fallback path):
      1. When javac is on PATH: ``javac -proc:none -d <tmpdir> <file>``
         (annotation-processing disabled — checks Java syntax without requiring
         annotation-processor classpath dependencies; Phase 0.5c already required
         JDK, so javac is expected).
      2. Fall back to a tokenizer-aware bracket/brace balance check when
         javac is absent (tracks string literals, block comments, and
         nested ``{}``, ``()``, ``[]`` so strings containing braces don't
         throw false positives).
      3. On failure: revert via ``git show <phase-tag>:<rel> > <abs>``
         using ``git ls-files --full-name``.
    After the sweep, remove any ``target/`` directories under <MIGRATED>
    left by Maven.

Exit code = min(fail_count, 255).  0 = all files pass.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_PRUNE_DIR_NAMES = {".git", "target", ".gradle", ".idea"}


# ---------------------------------------------------------------------------
# File iteration
# ---------------------------------------------------------------------------


def _iter_java_files(root: Path):
    """Yield every *.java file under ``root``, skipping pruned dirs.

    Symlinks are skipped unconditionally — a ``.java`` symlink pointing
    outside the migrated root would allow ``_git_revert`` to overwrite
    arbitrary files (CWE-22). Only regular files whose resolved path
    remains inside ``root.resolve()`` are yielded (defense-in-depth).
    """
    resolved_root = root.resolve()
    for path in root.rglob("*.java"):
        if path.is_symlink():
            continue  # never follow symlinks out of the migrated root
        if not path.is_file():
            continue
        if any(part in _PRUNE_DIR_NAMES for part in path.parts):
            continue
        try:
            if not path.resolve().is_relative_to(resolved_root):
                continue  # resolved path escapes the migrated root
        except (OSError, ValueError):
            continue  # unresolvable path — skip safely
        yield path


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def _javac_available() -> bool:
    """Return True if ``javac`` is on PATH."""
    try:
        subprocess.run(
            ["javac", "-version"],
            check=True,
            capture_output=True,
            timeout=15,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _javac_cmd(files: "list[Path]", tmpdir: str) -> list[str]:
    """Build the javac argv for one or more files.

    Uses ``-proc:none`` to disable annotation processing, avoiding classpath
    requirements for annotation processors. Class files go to ``tmpdir`` so
    they never pollute the source tree.
    """
    return ["javac", "-proc:none", "-d", tmpdir] + [str(f) for f in files]


def _smoke_javac() -> bool:
    """Compile a trivial Java class through the real ``_javac_cmd`` path.

    Only trusted after this smoke compile succeeds on a known-good snippet.
    Any failure — missing JVM, wrong java version — returns False so the
    caller degrades to tokenizer mode rather than reverting good files.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        smoke = Path(tmpdir) / "ScosSmoke.java"
        smoke.write_text(
            "public class ScosSmoke { public static void main(String[] args) {} }\n",
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                _javac_cmd([smoke], tmpdir),
                capture_output=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
    return result.returncode == 0


def _batch_javac_passes(files: "list[Path]") -> "bool | None":
    """Compile every file in a single javac invocation.

    Returns:
        True  — batch exited 0; every file is syntactically correct.
        False — batch reported errors; caller must attribute per-file.
        None  — javac could not run (timeout / not found); caller falls back.

    Compiling all sources together is both faster (one JVM start) and more
    correct, since cross-file symbol references resolve properly instead of
    false-failing in isolation.
    """
    if not files:
        return True
    timeout = min(600, max(60, 5 * len(files)))
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            result = subprocess.run(
                _javac_cmd(files, tmpdir),
                capture_output=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
    return result.returncode == 0


def _check_with_javac(file_path: Path) -> "tuple[bool, str]":
    """Compile-check a single Java file via javac -proc:none.

    Returns ``(ok, diagnostic)`` — ok is True when the file compiles; on
    failure diagnostic carries the javac stderr (trimmed) for repair feedback.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            result = subprocess.run(
                _javac_cmd([file_path], tmpdir),
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0:
                return True, ""
            stderr = (result.stderr or b"").decode("utf-8", "replace").strip()
            return False, stderr[:2000]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False, "timeout or javac not found: compile check could not complete"


def _check_with_fallback(source: str) -> bool:
    """Tokenizer-aware bracket/brace balance check.

    Correctly handles:
    * Double-quoted strings (including ``\\`` escapes)
    * Block comments: ``/* ... { ... } ... */``
    * Line comments: ``// ...``
    * Character literals: ``'{'``
    * Nested ``{}``, ``()``, ``[]``

    Returns True when the source is syntactically plausible (all openers
    have matching closers), False when a bracket/brace imbalance is detected.
    """
    OPEN = {"{": "}", "(": ")", "[": "]"}
    CLOSE = set(OPEN.values())
    stack: list[str] = []
    i = 0
    n = len(source)

    while i < n:
        c = source[i]

        # Double-quoted string
        if c == '"':
            i += 1
            while i < n:
                if source[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if source[i] == '"':
                    break
                i += 1
            else:
                return False  # unclosed string
            i += 1
            continue

        # Character literal (e.g. '{' or '\n')
        if c == "'":
            i += 1
            if i < n and source[i] == "\\" and i + 1 < n:
                i += 2  # '\x'
            elif i < n:
                i += 1  # 'x'
            if i < n and source[i] == "'":
                i += 1
            continue

        # Block comment
        if source[i: i + 2] == "/*":
            end = source.find("*/", i + 2)
            if end == -1:
                return False  # unclosed block comment
            i = end + 2
            continue

        # Line comment
        if source[i: i + 2] == "//":
            end = source.find("\n", i + 2)
            i = end + 1 if end != -1 else n
            continue

        if c in OPEN:
            stack.append(OPEN[c])
        elif c in CLOSE:
            if not stack or stack[-1] != c:
                return False
            stack.pop()

        i += 1

    return len(stack) == 0


def _check_syntax(file_path: Path, use_javac: bool) -> "tuple[bool, str]":
    """Return ``(ok, diagnostic)`` — ok True iff the file is syntactically correct."""
    if use_javac:
        return _check_with_javac(file_path)
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True, ""  # unreadable → skip
    if _check_with_fallback(source):
        return True, ""
    return False, "tokenizer: unbalanced bracket/paren/brace or unclosed string/comment"


# ---------------------------------------------------------------------------
# Git revert
# ---------------------------------------------------------------------------


def _phase_tag_exists(migrated: Path, phase_tag: str) -> bool:
    """Return True if ``phase_tag`` resolves in the git repo containing ``migrated``."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{phase_tag}^{{commit}}"],
            cwd=str(migrated),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _git_revert(migrated: Path, file_path: Path, phase_tag: str) -> bool:
    """Replace ``file_path`` with its blob at ``phase_tag`` using ``git show``.

    Equivalent to::

        git show "<tag>":"$(git ls-files --full-name <file>)" > <file>

    Returns True when the revert succeeded.
    """
    try:
        full_name = subprocess.run(
            ["git", "ls-files", "--full-name", str(file_path)],
            cwd=str(migrated),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not full_name:
            return False
        show = subprocess.run(
            ["git", "show", f"{phase_tag}:{full_name}"],
            cwd=str(migrated),
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    try:
        # Re-validate confinement before writing: file_path must still resolve
        # inside the migrated root (defense-in-depth against CWE-22).
        if not file_path.resolve().is_relative_to(migrated.resolve()):
            return False
        file_path.write_bytes(show.stdout)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Build artifact cleanup
# ---------------------------------------------------------------------------


def _remove_build_dirs(root: Path) -> int:
    """Remove ``target/`` directories left by Maven."""
    removed = 0
    for d in root.rglob("target"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


# Files annotated with SPRKCNTSCL1500 contain genuinely-unsupported RDD API
# calls. javac will report unresolved-reference errors for the old RDD types,
# but reverting them is pointless — the pre-migration code is equally broken
# under SCOS. They are quarantined: not reverted, not counted as gate failures.
_RDD_SCOS_MARKER_RE = re.compile(r"SPRKCNTSCL1500", re.IGNORECASE)


def _has_rdd_scos_marker(file_path: Path) -> bool:
    """True if the file carries a SPRKCNTSCL1500 RDD EWI marker."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _RDD_SCOS_MARKER_RE.search(text) is not None


def _run_sweep(
    migrated: Path,
    javac_ok: bool,
    phase_tag: str,
    json_mode: bool,
    no_revert: bool = False,
) -> "tuple[list[str], list[str], str, list[str], dict[str, str], list[str]]":
    """Check every Java file; revert the failures (unless *no_revert*).

    Batch-first: when javac is available, try one batched compile over all
    files. If it passes, no per-file work is needed. Only on a reported failure
    (or when javac can't run) does it fall back to per-file attribution.

    Returns ``(failures, reverted, compile_strategy, quarantined, diagnostics,
    revert_failures)``:
    *compile_strategy* is ``"batch"`` / ``"per_file"`` / ``"none"``;
    *quarantined* lists files that failed but carry a SPRKCNTSCL1500 marker
    (NOT reverted, NOT counted as failures);
    *revert_failures* lists files that failed compile AND whose git revert also
    failed — their broken state is still on disk and must be investigated.

    When *no_revert* is True, failing files are reported with diagnostics but
    NOT reverted — allows a bounded repair pass before any file is discarded.
    """
    java_files = list(_iter_java_files(migrated))
    failures: list[str] = []
    reverted: list[str] = []
    quarantined: list[str] = []
    revert_failures: list[str] = []
    diagnostics: dict[str, str] = {}

    if not java_files:
        return failures, reverted, "none", quarantined, diagnostics, revert_failures

    if javac_ok:
        batch = _batch_javac_passes(java_files)
        if batch is True:
            print(f"Compile: batched {len(java_files)} file(s) -> pass", file=sys.stderr)
            return failures, reverted, "batch", quarantined, diagnostics, revert_failures
        reason = "errors" if batch is False else "javac could not run"
        print(
            f"Compile: batch reported {reason}; attributing per-file",
            file=sys.stderr,
        )

    # Per-file attribution fallback
    for java_file in java_files:
        ok, diag = _check_syntax(java_file, javac_ok)
        if not ok:
            rel = str(java_file.relative_to(migrated))
            # Quarantine files with unsupported RDD annotations — reverting them
            # doesn't help since the original is equally broken under SCOS.
            if _has_rdd_scos_marker(java_file):
                quarantined.append(rel)
                if not json_mode:
                    print(f"QUARANTINE_RDD: {java_file}")
                continue
            failures.append(rel)
            if diag:
                diagnostics[rel] = diag
            if not no_revert:
                if _git_revert(migrated, java_file, phase_tag):
                    reverted.append(rel)
                else:
                    print(
                        f"REVERT_FAIL: {java_file} — could not restore to {phase_tag}; "
                        "broken file is still on disk. Continuing sweep.",
                        file=sys.stderr,
                    )
                    revert_failures.append(rel)
            if not json_mode:
                print(f"SYNTAX_FAIL: {java_file}")

    return failures, reverted, "per_file", quarantined, diagnostics, revert_failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migrated",
        required=True,
        help="Path to the <MIGRATED> directory containing Phase 2 Java output.",
    )
    parser.add_argument(
        "--phase-tag",
        default="phase-1-complete",
        help="Git ref to revert failing files back to. Default: phase-1-complete.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary instead of text lines.",
    )
    parser.add_argument(
        "--no-revert",
        action="store_true",
        help=(
            "Diagnose mode: report failing files and their compiler errors "
            "(JSON 'diagnostics') WITHOUT reverting. Lets the orchestrator run a "
            "bounded compiler-feedback repair pass before any file is reverted; "
            "re-run without this flag to revert whatever still fails."
        ),
    )
    parser.add_argument(
        "--require-javac",
        action="store_true",
        help=(
            "Fail (exit 3) if the gate cannot run with javac (e.g. javac is not "
            "on PATH or fails its smoke test). Use in CI/production to enforce real "
            "compilation checking instead of silently degrading to tokenizer mode."
        ),
    )
    args = parser.parse_args(argv)

    # Diagnose mode never reverts, so the phase tag is not required for it.
    require_tag = not args.no_revert

    migrated = Path(args.migrated).expanduser().resolve()
    if not migrated.is_dir():
        print(f"ERROR: --migrated {migrated} is not a directory", file=sys.stderr)
        return 255

    if require_tag and not _phase_tag_exists(migrated, args.phase_tag):
        print(
            f"ERROR: git ref '{args.phase_tag}' not found in the repo at {migrated}. "
            f"Create it after Phase 1 (e.g. `git tag -f {args.phase_tag}`) before "
            "running the compile gate.",
            file=sys.stderr,
        )
        return 2

    # Probe javac: found on PATH AND passes a trivial smoke compile.
    _found = _javac_available()
    if _found:
        javac_ok = _smoke_javac()
        if not javac_ok:
            print(
                "WARN: javac found but failed smoke test; falling back to tokenizer mode.",
                file=sys.stderr,
            )
    else:
        javac_ok = False

    compile_mode = "javac" if javac_ok else "tokenizer"
    print(f"Compile mode: {compile_mode}", file=sys.stderr)

    if args.require_javac and not javac_ok:
        print(
            "ERROR: --require-javac set but javac is not available or failed its smoke test. "
            "Ensure a JDK 11+ is installed and javac is on PATH.",
            file=sys.stderr,
        )
        return 3

    failures, reverted, compile_strategy, quarantined, diagnostics, revert_failures = _run_sweep(
        migrated, javac_ok, args.phase_tag, args.json, no_revert=args.no_revert,
    )

    # Diagnose mode is inspection-only — leave the working tree untouched.
    target_dirs_removed = 0 if args.no_revert else _remove_build_dirs(migrated)

    if args.json:
        print(
            json.dumps(
                {
                    "fail_count": len(failures),
                    "failures": failures,
                    "reverted_count": len(reverted),
                    "reverted": reverted,
                    "quarantined_count": len(quarantined),
                    "quarantined": quarantined,
                    "revert_failure_count": len(revert_failures),
                    "revert_failures": revert_failures,
                    "diagnostics": diagnostics,
                    "no_revert": args.no_revert,
                    "javac_available": javac_ok,
                    "compile_mode": compile_mode,
                    "compile_strategy": compile_strategy,
                    "target_dirs_removed": target_dirs_removed,
                },
                indent=2,
            )
        )
    else:
        print(f"FAIL_COUNT={len(failures)}")
        print(f"REVERTED_COUNT={len(reverted)}")
        print(f"QUARANTINED_COUNT={len(quarantined)}")
        print(f"REVERT_FAILURE_COUNT={len(revert_failures)}")
        print(f"NO_REVERT={args.no_revert}")
        print(f"JAVAC_AVAILABLE={javac_ok}")
        print(f"COMPILE_MODE={compile_mode}")
        print(f"COMPILE_STRATEGY={compile_strategy}")
        print(f"TARGET_DIRS_REMOVED={target_dirs_removed}")

    # Non-zero exit if any revert failed — those files are still broken on disk.
    if revert_failures:
        return min(len(failures) + len(revert_failures), 255)
    return min(len(failures), 255)


if __name__ == "__main__":
    raise SystemExit(main())
