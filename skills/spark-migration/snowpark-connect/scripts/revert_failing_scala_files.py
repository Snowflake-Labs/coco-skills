#!/usr/bin/env python3
"""Portable syntax/type-check gate and git-revert tool for Scala files produced
during Phase 2 of the SCOS Scala migration skill.

Runs identically on macOS, Linux, and Windows under ``uv run``.

Usage::

    uv run --project <SKILL_DIRECTORY> \
        python <SKILL_DIRECTORY>/scripts/revert_failing_scala_files.py \
        --migrated <MIGRATED_DIR> \
        --phase-tag phase-1-complete \
        [--classpath <path/to/snowpark-connect-java-client.jar>] \
        [--json]

Behaviour::

    Compilation is **batch-first**: a single ``scalac`` invocation is run over
    every *.scala file at once. If that batch exits 0, every file passes and no
    per-file work is done (one JVM start instead of N — the common, healthy
    case). Only when the batch reports errors (or scalac cannot run) does the
    tool fall back to per-file checking to attribute failures precisely. The
    batch compile also resolves cross-file symbols correctly in type-check mode,
    avoiding the false reverts a per-file-in-isolation typer check would cause.

    Per-file modes (used on the fallback path, and always for the no-scalac
    tokenizer mode):
      1. When a classpath JAR is available (via --classpath or auto-located from
         common build-tool caches): type-check with
         ``scalac -classpath <jar> -Ystop-after:typer -d /tmp``
         (stops after type checking — catches type errors, no bytecode emission).
      2. When scalac is on PATH but no classpath is available: parse-check with
         ``scalac -Ystop-after:parser -nobootcp -d /tmp``
         (parse-only, fast — no codegen).
      3. Fall back to a tokenizer-aware bracket/brace balance check when
         scalac is absent (tracks string literals, block comments, and
         nested ``{}``, ``()``, ``[]`` so strings containing braces don't
         throw false positives).
      4. On failure: revert via ``git show <phase-tag>:<rel> > <abs>``
         using ``git ls-files --full-name``.
    After the sweep, remove any ``target/`` and ``.bsp/`` directories
    under <MIGRATED> left by sbt/Scala Metals.

Compile mode is printed at start: ``Compile mode: <type_check|parse_only>``.

Exit code = min(fail_count, 255).  0 = all files pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_PRUNE_DIR_NAMES = {".git", "target", ".bsp", ".metals"}

# First line (or first non-empty line) that marks a file as a Databricks notebook
# export — these contain top-level expressions and cannot be compiled by scalac.
_DATABRICKS_NOTEBOOK_MARKER = "// Databricks notebook source"


def _is_databricks_notebook(path: Path) -> bool:
    """Return True if the file is a Databricks-exported .scala notebook script."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    return stripped == _DATABRICKS_NOTEBOOK_MARKER
    except OSError:
        pass
    return False


# ---------------------------------------------------------------------------
# File iteration
# ---------------------------------------------------------------------------


def _iter_scala_files(root: Path):
    """Yield every *.scala file under ``root``, skipping pruned dirs.

    Databricks-exported notebook scripts (first non-empty line equals
    ``// Databricks notebook source``) are excluded: they contain top-level
    expressions that fail scalac with "expected class or object definition" —
    a pre-existing condition unrelated to migration quality.

    Symlinks are skipped (mirrors revert_failing_java_files._iter_java_files).
    A symlink pointing outside the migrated root could allow git-show to
    overwrite files in the original source tree (CWE-22).
    """
    resolved_root = root.resolve()
    for path in root.rglob("*.scala"):
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
        if _is_databricks_notebook(path):
            continue
        yield path


# ---------------------------------------------------------------------------
# JAR auto-location
# ---------------------------------------------------------------------------


def _locate_snowpark_connect_jar() -> "Path | None":
    """Probe common build-tool caches for the snowpark-connect-java-client JAR.

    Probes (in order):
    1. ``~/.cache/coursier/v1/.../snowpark-connect-java-client_2.12/``
    2. ``~/.cache/coursier/v1/.../snowpark-connect-java-client_2.13/``
    3. ``~/.ivy2/cache/com.snowflake/snowpark-connect-java-client_2.12/jars/``
    4. ``~/.ivy2/cache/com.snowflake/snowpark-connect-java-client_2.13/jars/``
    5. ``~/.gradle/caches/modules-2/files-2.1/com.snowflake/snowpark-connect-java-client_2.12/``

    Returns the highest-version JAR found (semver sort), or ``None``.
    """
    home = Path.home()

    probe_bases = [
        home / ".cache/coursier/v1/https/repo1.maven.org/maven2/com/snowflake/snowpark-connect-java-client_2.12",
        home / ".cache/coursier/v1/https/repo1.maven.org/maven2/com/snowflake/snowpark-connect-java-client_2.13",
        home / ".ivy2/cache/com.snowflake/snowpark-connect-java-client_2.12/jars",
        home / ".ivy2/cache/com.snowflake/snowpark-connect-java-client_2.13/jars",
        home / ".gradle/caches/modules-2/files-2.1/com.snowflake/snowpark-connect-java-client_2.12",
    ]

    candidates: list[tuple[tuple[int, ...], Path]] = []
    for base in probe_bases:
        if not base.exists():
            continue
        for jar in base.rglob("*.jar"):
            if not jar.is_file():
                continue
            # Extract version from JAR stem, e.g. "snowpark-connect-java-client_2.12-0.4.1"
            m = re.search(r"-(\d+\.\d+(?:\.\d+)*)", jar.stem)
            if m:
                try:
                    ver_tuple = tuple(int(p) for p in m.group(1).split(".") if p.isdigit())
                    candidates.append((ver_tuple, jar))
                except ValueError:
                    pass

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# ---------------------------------------------------------------------------
# Syntax / type checking
# ---------------------------------------------------------------------------


def _scalac_available() -> bool:
    """Return True if ``scalac`` is on PATH."""
    try:
        subprocess.run(
            ["scalac", "-version"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _scalac_cmd(
    files: "list[Path]", classpath: "Path | None", tmpdir: str,
    scalac_prefix: "tuple[str, ...] | list[str]" = ("scalac",),
) -> list[str]:
    """Build the scalac argv for one or more files.

    ``scalac_prefix`` is the resolved compiler invocation — usually
    ``["scalac"]`` (binary on PATH) or a Coursier launch like
    ``[cs, "launch", "scalac:2.12.20", "--"]``. Type-check mode
    (``-Ystop-after:typer``) when a *classpath* is available, otherwise
    parse-only mode (``-Ystop-after:parser``). Files are passed as separate
    argv elements so paths with whitespace are handled natively.
    """
    if classpath is not None:
        base = list(scalac_prefix) + ["-classpath", str(classpath), "-Ystop-after:typer", "-d", tmpdir]
    else:
        base = list(scalac_prefix) + ["-Ystop-after:parser", "-nobootcp", "-d", tmpdir]
    return base + [str(f) for f in files]


DEFAULT_SCALA_VERSION = "2.12.20"
DEFAULT_SCALA_BINARY = "2.12"
DEFAULT_SPARK_VERSION = "3.5.6"
DEFAULT_SCOS_VERSION = "1.0.0"


def _resolve_cs(allow_coursier: bool = False) -> "str | None":
    """Return a Coursier launcher path (``cs``/``coursier``), or None. Best-effort.

    Coursier on PATH wins; otherwise (only when ``allow_coursier``) the proven
    Phase 0.5b bootstrap downloads + caches a launcher. Shared by scalac and
    classpath resolution so both use one toolchain probe.
    """
    cs = shutil.which("cs") or shutil.which("coursier")
    if cs:
        return cs
    if not allow_coursier:
        return None
    try:
        from preprocess_scalafix import _bootstrap_coursier  # reuse proven bootstrap
        return _bootstrap_coursier()
    except Exception:  # noqa: BLE001
        return None


def _resolve_scalac(
    scala_version: str = DEFAULT_SCALA_VERSION, allow_coursier: bool = False
) -> "list[str] | None":
    """Resolve a runnable scalac invocation prefix, or None. Best-effort; never raises.

    Order (first that works wins):
      1. ``scalac`` on PATH                       -> ["scalac"]
      2. (allow_coursier only) Coursier on PATH   -> [cs, "launch", "scalac:<ver>", "--"]
      3. (allow_coursier only) bootstrapped cs    -> [cs, "launch", "scalac:<ver>", "--"]

    The returned prefix is NOT trusted until it passes ``_smoke_scalac`` — that
    is what makes an incorrect invocation degrade safely instead of breaking the
    gate. ALL Coursier use is opt-in (``allow_coursier``), because the first
    ``cs launch`` downloads a JVM + scala (slow, one-time). So the **default**
    behavior is identical to before: scalac-on-PATH or nothing (→ tokenizer),
    with no surprise downloads.
    """
    if shutil.which("scalac"):
        return ["scalac"]
    cs = _resolve_cs(allow_coursier)
    if cs:
        return [cs, "launch", f"scalac:{scala_version}", "--"]
    return None


def _parse_classpath_arg(value: str) -> "Path | str":
    """Interpret a ``--classpath`` value flexibly.

    Real SCOS type-checking needs the full transitive classpath (the SCOS client
    JAR **plus** ``spark-connect-client-jvm`` and friends — dozens of JARs), not
    a single JAR. So this accepts three forms:

    * ``@/path/to/cp.txt`` — read the classpath string from a file (handy for a
      coursier-resolved list; surrounding whitespace/newlines are stripped).
    * a string containing ``os.pathsep`` (``:`` / ``;``) — used verbatim as a
      multi-entry classpath.
    * anything else — a single path, resolved as before.
    """
    value = value.strip()
    if value.startswith("@"):
        return Path(value[1:]).expanduser().read_text(encoding="utf-8").strip()
    if os.pathsep in value:
        return value
    return Path(value).expanduser().resolve()


def _resolve_scos_classpath(
    allow_coursier: bool,
    spark_version: str = DEFAULT_SPARK_VERSION,
    scos_version: str = DEFAULT_SCOS_VERSION,
    scala_binary: str = DEFAULT_SCALA_BINARY,
) -> "str | None":
    """Best-effort: assemble the full SCOS type-check classpath via Coursier. None on any failure.

    Two parts, because the published client POM is quirky:
      1. ``cs fetch --classpath spark-connect-client-jvm_<bin>:<spark> slf4j-api``
         — this is the API surface migrated code compiles against.
      2. The ``snowpark-connect-java-client`` JAR. Its published POM leaves
         ``${scala.binary.version}`` unsubstituted in the artifact filename, so a
         plain ``cs fetch`` of the coordinate fails; we download the correctly
         named JAR directly from Maven Central and cache it.

    Never raises — returns None so the caller falls back to parse_only/tokenizer.
    """
    cs = _resolve_cs(allow_coursier)
    if not cs:
        return None
    try:
        deps = subprocess.run(
            [cs, "fetch", "--classpath",
             f"org.apache.spark:spark-connect-client-jvm_{scala_binary}:{spark_version}",
             "org.slf4j:slf4j-api:2.0.16"],
            capture_output=True, text=True, timeout=900,
        )
        if deps.returncode != 0 or not deps.stdout.strip():
            return None
        deps_cp = deps.stdout.strip()

        # Download the client JAR directly (POM property-substitution bug workaround).
        cache_dir = Path.home() / ".cache" / "scos" / "jars"
        cache_dir.mkdir(parents=True, exist_ok=True)
        jar_name = f"snowpark-connect-java-client_{scala_binary}-{scos_version}.jar"
        jar_path = cache_dir / jar_name
        if not jar_path.exists():
            url = (
                "https://repo1.maven.org/maven2/com/snowflake/"
                f"snowpark-connect-java-client_{scala_binary}/{scos_version}/{jar_name}"
            )
            import urllib.request
            with urllib.request.urlopen(url, timeout=300) as resp:  # noqa: S310
                data = resp.read()
            jar_path.write_bytes(data)
        return str(jar_path) + os.pathsep + deps_cp
    except Exception:  # noqa: BLE001
        return None


def _smoke_scalac(
    scalac_prefix: "tuple[str, ...] | list[str]", classpath: "Path | None" = None
) -> bool:
    """Compile a trivial object through the REAL ``_scalac_cmd`` path.

    This is the safety gate. A resolved compiler (and, when given, a classpath
    jar) is only trusted for the gate if it can compile a known-good snippet.
    Any failure — wrong invocation, missing JVM, threading bug in the command
    builder, or an incompatible classpath jar — returns False so the caller
    degrades to a weaker mode rather than reverting good files against a broken
    compiler. Returns True only on a clean exit 0.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        smoke = Path(tmpdir) / "ScosSmoke.scala"
        smoke.write_text(
            "object ScosSmoke { def main(args: Array[String]): Unit = () }\n",
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                _scalac_cmd([smoke], classpath, tmpdir, scalac_prefix),
                capture_output=True,
                timeout=300,  # first `cs launch` may download a JVM + scala (one-time)
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
    return result.returncode == 0


def _batch_scalac_passes(
    files: "list[Path]", classpath: "Path | None",
    scalac_prefix: "tuple[str, ...] | list[str]" = ("scalac",),
) -> "bool | None":
    """Compile every file in a single scalac invocation.

    Returns:
        True  — batch exited 0; every file is syntactically/type correct.
        False — batch reported errors; caller must attribute per-file.
        None  — scalac could not run (timeout / not found); caller falls back.

    Compiling all sources together (rather than one-at-a-time) is both faster
    (one JVM start) and more correct in type-check mode, where cross-file symbol
    references resolve properly instead of false-failing in isolation.
    """
    if not files:
        return True
    # Scale the timeout with the batch size; generous but bounded.
    timeout = min(900, max(60, 8 * len(files)))
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            result = subprocess.run(
                _scalac_cmd(files, classpath, tmpdir, scalac_prefix),
                capture_output=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
    return result.returncode == 0


def _check_with_scalac(
    file_path: Path, classpath: "Path | None" = None,
    scalac_prefix: "tuple[str, ...] | list[str]" = ("scalac",),
    extra_sources: "list[Path] | None" = None,
) -> "tuple[bool, str]":
    """Compile-check a single Scala file via scalac.

    Pass ``extra_sources`` (the sibling files) alongside *file_path* so that
    cross-file symbol references resolve correctly in type-check mode.  Without
    siblings, a file that uses a class defined in another source would
    false-fail in isolation even when the whole project compiles cleanly.

    Returns ``(ok, diagnostic)``: *ok* is True when the file compiles; on failure
    *diagnostic* carries the scalac stderr (trimmed) so the orchestrator can feed
    it back to a bounded repair pass before the file is reverted. Used on the
    per-file fallback path to attribute failures to individual files.
    """
    all_sources = list(extra_sources or []) + [file_path]
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            result = subprocess.run(
                _scalac_cmd(all_sources, classpath, tmpdir, scalac_prefix),
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0:
                return True, ""
            stderr = (result.stderr or b"").decode("utf-8", "replace").strip()
            return False, stderr[:2000]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False, "timeout or scalac not found: compile check could not complete within 30s"


def _check_with_fallback(source: str) -> bool:
    """Tokenizer-aware bracket/brace balance check.

    Correctly handles:
    * Single-quoted characters: ``'{'``
    * Double-quoted strings (including ``\\`` escapes): ``"hello {world}"``
    * Triple-quoted strings: ``\"\"\"...{ ... }...\"\"\"``
    * Block comments: ``/* ... { ... } ... */``
    * Line comments: ``// ...``
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

        # Triple-quoted string
        if source[i : i + 3] == '"""':
            end = source.find('"""', i + 3)
            if end == -1:
                return False  # unclosed triple-quote
            i = end + 3
            continue

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

        # Single-quoted character literal (e.g. '{')
        if c == "'":
            i += 1
            if i + 1 < n and source[i + 1] == "'":
                i += 2  # 'x'
            continue

        # Block comment
        if source[i : i + 2] == "/*":
            end = source.find("*/", i + 2)
            if end == -1:
                return False  # unclosed block comment
            i = end + 2
            continue

        # Line comment
        if source[i : i + 2] == "//":
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


def _check_syntax(
    file_path: Path, use_scalac: bool, classpath: "Path | None" = None,
    scalac_prefix: "tuple[str, ...] | list[str]" = ("scalac",),
    extra_sources: "list[Path] | None" = None,
) -> "tuple[bool, str]":
    """Return ``(ok, diagnostic)`` — ok True iff the file is syntactically/type correct.

    *diagnostic* carries the scalac stderr (type_check/parse_only) or a short
    tokenizer message (fallback) on failure, for the orchestrator's repair pass.
    """
    if use_scalac:
        return _check_with_scalac(file_path, classpath, scalac_prefix, extra_sources=extra_sources)
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
    """Return True if ``phase_tag`` resolves in the git repo containing ``migrated``.

    The compile gate's entire revert contract depends on this tag, so the caller
    fails fast when it is absent rather than discovering it mid-revert.
    """
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
        file_path.write_bytes(show.stdout)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Build artifact cleanup
# ---------------------------------------------------------------------------


def _remove_build_dirs(root: Path) -> int:
    """Remove ``target/`` and ``.bsp/`` directories left by sbt/Metals."""
    removed = 0
    for name in ("target", ".bsp"):
        for d in root.rglob(name):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return removed


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


# A file the fixer annotated as containing genuinely-unsupported RDD code
# (Bucket A) — e.g. ``// EWI: SPRKCNTSCL1500 => ... not supported ...; manual
# refactor required.``. Such files CANNOT compile under SCOS by definition, so
# reverting them is pointless (the pre-migration code is just as broken). They are
# quarantined: not reverted, not counted as gate failures, reported as manual.
# The phrase "manual refactor" distinguishes this from the *convertible*
# parallelize annotation ("Convert to spark.createDataFrame"), which must compile.
_RDD_MANUAL_MARKER_RE = re.compile(
    r"SPRKCNTSCL1500[\s\S]{0,200}?manual refactor", re.IGNORECASE
)


def _has_manual_rdd_marker(file_path: Path) -> bool:
    """True if the file carries a Bucket-A 'manual refactor' RDD EWI marker."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _RDD_MANUAL_MARKER_RE.search(text) is not None


def _run_sweep(
    migrated: Path,
    scalac_ok: bool,
    classpath: "Path | None",
    phase_tag: str,
    json_mode: bool,
    scalac_prefix: "tuple[str, ...] | list[str]" = ("scalac",),
    no_revert: bool = False,
) -> "tuple[list[str], list[str], str, list[str], dict[str, str]]":
    """Check every Scala file; revert the failures (unless *no_revert*).

    Batch-first: when scalac is available, try one batched compile over all
    files. If it passes, no per-file work is needed. Only on a reported failure
    (or when scalac can't run) does it fall back to per-file attribution so the
    exact failing files are reverted.

    Returns ``(failures, reverted, compile_strategy, quarantined, diagnostics)``:
    *compile_strategy* is ``"batch"`` / ``"per_file"`` / ``"none"``; *quarantined*
    lists files that failed but carry a Bucket-A 'manual refactor' RDD marker (NOT
    reverted, NOT counted as failures); *diagnostics* maps each failing file to its
    compiler error text.

    When *no_revert* is True (the orchestrator's diagnose pass) failing files are
    reported with their diagnostics but NOT reverted — so a bounded compiler-feedback
    repair can run before any file is thrown away. ``reverted`` is empty in that mode.
    """
    scala_files = list(_iter_scala_files(migrated))
    failures: list[str] = []
    reverted: list[str] = []
    quarantined: list[str] = []
    diagnostics: dict[str, str] = {}

    if not scala_files:
        return failures, reverted, "none", quarantined, diagnostics

    if scalac_ok:
        batch = _batch_scalac_passes(scala_files, classpath, scalac_prefix)
        if batch is True:
            print(f"Compile: batched {len(scala_files)} file(s) -> pass", file=sys.stderr)
            return failures, reverted, "batch", quarantined, diagnostics
        reason = "errors" if batch is False else "scalac could not run"
        print(
            f"Compile: batch reported {reason}; attributing per-file",
            file=sys.stderr,
        )

    # Fallback / tokenizer path: per-file attribution.
    # Pass all sibling files alongside each candidate so that cross-file symbol
    # references (e.g. a class defined in another source) resolve correctly in
    # type-check mode.  Compiling in isolation would false-fail any file that
    # references symbols from its siblings.
    revert_errors: list[str] = []
    for scala_file in scala_files:
        siblings = [f for f in scala_files if f != scala_file]
        ok, diag = _check_syntax(scala_file, scalac_ok, classpath, scalac_prefix,
                                  extra_sources=siblings if scalac_ok else None)
        if not ok:
            rel = str(scala_file.relative_to(migrated))
            # Quarantine known-unsupported RDD files instead of reverting them:
            # the original is equally broken and a revert would erase the EWI.
            if _has_manual_rdd_marker(scala_file):
                quarantined.append(rel)
                if not json_mode:
                    print(f"QUARANTINE_MANUAL_RDD: {scala_file}")
                continue
            failures.append(rel)
            if diag:
                diagnostics[rel] = diag
            if not no_revert:
                if _git_revert(migrated, scala_file, phase_tag):
                    reverted.append(rel)
                else:
                    print(
                        f"REVERT_FAIL: {scala_file} — could not restore to {phase_tag}; "
                        "broken file is still on disk. Aborting sweep.",
                        file=sys.stderr,
                    )
                    revert_errors.append(rel)
                    return failures, reverted, "per_file", quarantined, diagnostics
            if not json_mode:
                print(f"SYNTAX_FAIL: {scala_file}")

    return failures, reverted, "per_file", quarantined, diagnostics


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migrated",
        required=True,
        help="Path to the <MIGRATED> directory containing Phase 2 Scala output.",
    )
    parser.add_argument(
        "--phase-tag",
        default="phase-1-complete",
        help="Git ref to revert failing files back to. Default: phase-1-complete.",
    )
    parser.add_argument(
        "--classpath",
        default=None,
        help=(
            "Classpath for type-check mode. Accepts a single JAR path, a full "
            "os.pathsep-joined classpath string, or '@FILE' to read the classpath "
            "from a file. Real type-checking needs the SCOS client JAR PLUS "
            "spark-connect-client-jvm and deps — a single JAR usually only reaches "
            "parse_only. When omitted with --bootstrap-coursier, the full classpath "
            "is auto-resolved; otherwise the script probes local caches."
        ),
    )
    parser.add_argument(
        "--spark-version",
        default=DEFAULT_SPARK_VERSION,
        help=f"Spark version for auto-resolved spark-connect-client-jvm (default {DEFAULT_SPARK_VERSION}).",
    )
    parser.add_argument(
        "--scos-version",
        default=DEFAULT_SCOS_VERSION,
        help=f"snowpark-connect-java-client version for auto-resolution (default {DEFAULT_SCOS_VERSION}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary instead of text lines.",
    )
    parser.add_argument(
        "--scala-version",
        default=DEFAULT_SCALA_VERSION,
        help=f"Scala version for Coursier-launched scalac (default {DEFAULT_SCALA_VERSION}).",
    )
    parser.add_argument(
        "--bootstrap-coursier",
        action="store_true",
        default=os.environ.get("SCOS_BOOTSTRAP_COURSIER") == "1",
        help=(
            "Allow resolving scalac via Coursier when it is not on PATH (cs on "
            "PATH, else bootstrap a launcher). The first launch downloads a JVM + "
            "scala (one-time, cached). Default OFF: only a scalac already on PATH "
            "is used, so behavior is unchanged on machines without scalac."
        ),
    )
    parser.add_argument(
        "--require-type-check",
        action="store_true",
        help=(
            "Fail (exit 3) if the gate cannot run in type_check mode (scalac + "
            "client JAR). Use in CI/production to enforce real type-checking "
            "instead of silently degrading to parse_only/tokenizer."
        ),
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
    args = parser.parse_args(argv)

    # Diagnose mode never reverts, so the phase tag is not required for it.
    require_tag = not args.no_revert

    migrated = Path(args.migrated).expanduser().resolve()
    if not migrated.is_dir():
        print(f"ERROR: --migrated {migrated} is not a directory", file=sys.stderr)
        return 255

    # Fail fast if the revert anchor tag is missing — the gate cannot honor its
    # "revert failing files to <phase_tag>" contract without it. Better to stop
    # now than to discover it only after compiling, or to silently leave broken
    # files in place because the revert no-op'd.
    if require_tag and not _phase_tag_exists(migrated, args.phase_tag):
        print(
            f"ERROR: git ref '{args.phase_tag}' not found in the repo at {migrated}. "
            f"Create it after Phase 1 (e.g. `git tag -f {args.phase_tag}`) before "
            "running the compile gate.",
            file=sys.stderr,
        )
        return 2

    # Resolve a working scalac. Default = scalac-on-PATH only; Coursier is
    # opt-in (--bootstrap-coursier). The resolved compiler is trusted ONLY after
    # a smoke compile through the real command path, so a wrong invocation, a
    # missing JVM, or a command-builder bug degrades safely instead of reverting
    # good files against a broken compiler.
    scalac_prefix = _resolve_scalac(args.scala_version, allow_coursier=args.bootstrap_coursier)
    if scalac_prefix is not None and not _smoke_scalac(scalac_prefix):
        print(
            "WARN: resolved scalac failed its smoke test; falling back to tokenizer mode.",
            file=sys.stderr,
        )
        scalac_prefix = None
    scalac_ok = scalac_prefix is not None
    scalac_prefix = scalac_prefix or ("scalac",)  # harmless default; only used when scalac_ok

    # Resolve classpath. Real type-checking needs the FULL transitive classpath
    # (SCOS client JAR + spark-connect-client-jvm + deps), not a single JAR:
    #   explicit --classpath (single path | classpath string | @file)
    #   → else, when Coursier is enabled, auto-resolve the full SCOS classpath
    #   → else, the legacy single-JAR cache probe (insufficient alone — only
    #     provides SnowparkConnectSession, so it usually degrades to parse_only).
    classpath: "Path | str | None" = None
    if args.classpath:
        classpath = _parse_classpath_arg(args.classpath)
    elif scalac_ok and args.bootstrap_coursier:
        classpath = _resolve_scos_classpath(
            allow_coursier=True,
            spark_version=args.spark_version,
            scos_version=args.scos_version,
        )
        if classpath is None:
            classpath = _locate_snowpark_connect_jar()
    elif scalac_ok:
        classpath = _locate_snowpark_connect_jar()

    # Validate the classpath JAR too: if a trivial type-check compile fails WITH
    # it, the JAR is unusable/incompatible — drop to parse_only rather than
    # mass-reverting every file against a bad classpath.
    if scalac_ok and classpath is not None and not _smoke_scalac(scalac_prefix, classpath):
        print(
            "WARN: client JAR failed type-check smoke; using parse_only (no classpath).",
            file=sys.stderr,
        )
        classpath = None

    # Determine and announce compile mode
    # Three honest modes: type_check (scalac + classpath), parse_only (scalac,
    # no classpath), tokenizer (no scalac at all — bracket/brace balance only).
    if scalac_ok and classpath is not None:
        compile_mode = "type_check"
    elif scalac_ok:
        compile_mode = "parse_only"
    else:
        compile_mode = "tokenizer"
    classpath_used: str | None = str(classpath) if classpath is not None else None

    print(f"Compile mode: {compile_mode}", file=sys.stderr)

    # Enforce type_check when requested — BEFORE any revert work, so a degraded
    # gate fails loudly in CI/production instead of silently rubber-stamping.
    if args.require_type_check and compile_mode != "type_check":
        print(
            f"ERROR: --require-type-check set but compile_mode is '{compile_mode}'. "
            "Need a working scalac (on PATH or via --bootstrap-coursier) AND the "
            "snowpark-connect-java-client JAR (--classpath or in a resolver cache).",
            file=sys.stderr,
        )
        return 3

    failures, reverted, compile_strategy, quarantined, diagnostics = _run_sweep(
        migrated, scalac_ok, classpath, args.phase_tag, args.json, scalac_prefix,
        no_revert=args.no_revert,
    )

    # Diagnose mode is inspection-only — leave the working tree untouched.
    target_dirs_removed = 0 if args.no_revert else _remove_build_dirs(migrated)

    if args.json:
        print(
            json.dumps(
                {
                    "fail_count": len(failures),
                    "failures": failures,
                    "reverted": reverted,
                    "quarantined_manual": quarantined,
                    "diagnostics": diagnostics,
                    "no_revert": args.no_revert,
                    "scalac_available": scalac_ok,
                    "compile_mode": compile_mode,
                    "compile_strategy": compile_strategy,
                    "classpath_used": classpath_used,
                    "target_dirs_removed": target_dirs_removed,
                },
                indent=2,
            )
        )
    else:
        print(f"FAIL_COUNT={len(failures)}")
        print(f"REVERTED={len(reverted)}")
        print(f"QUARANTINED_MANUAL={len(quarantined)}")
        print(f"NO_REVERT={args.no_revert}")
        print(f"SCALAC_AVAILABLE={scalac_ok}")
        print(f"COMPILE_MODE={compile_mode}")
        print(f"COMPILE_STRATEGY={compile_strategy}")
        print(f"CLASSPATH_USED={classpath_used}")
        print(f"TARGET_DIRS_REMOVED={target_dirs_removed}")

    return min(len(failures), 255)


if __name__ == "__main__":
    raise SystemExit(main())
