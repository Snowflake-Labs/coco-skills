#!/usr/bin/env python3
"""Phase 0.5c: JavaParser AST pre-processing for Spark Java → SCOS migration.

Applies the 12 SCOS JavaParser rewrite/annotate rules to every .java file in
the manifest.  Unlike the Scala Scalafix tier (best-effort), this phase HARD
FAILS when no JVM runner can be resolved — Java projects always have a JVM, so
a missing runner is a genuine blocker, not a skip condition.

Runner resolution (first that works wins)
------------------------------------------
1. ``--runner-jar <path>`` override supplied by the caller.
2. Prebuilt fat-jar at ``scripts/javaparser_maven/target/scos-javaparser-runner.jar``.
3. **Maven auto-build** — ``mvn -q -f scripts/javaparser_maven/pom.xml package``
   when ``mvn`` + ``java`` are on PATH.  Disable via ``--no-build`` /
   ``SCOS_JAVAPARSER_NO_BUILD=1``.

Each eligible .java file is processed rule-by-rule sequentially; outputs are
applied cumulatively (each rule sees the output of the previous).  The file is
written back to disk once, after all rules, minimising I/O.

Changed source lines are located with difflib and recorded as ``recipe_edits``
anchors (``recipe_id = "javaparser:<RuleName>"``,
``output_line_anchor = "javaparser:<RuleName>:<src_line>:<sha1[:8]>"``).

The phase is idempotent: files whose ``recipe_edits`` already carry a
``javaparser:`` entry are skipped on re-run.

Usage
-----
    uv run --project <SKILL_DIRECTORY> \\
        python <SKILL_DIRECTORY>/scripts/preprocess_javaparser.py \\
        --state <CONVERSION>/migration_state.json

    # Use a pre-built fat-jar (skips Maven auto-build)
    uv run --project <SKILL_DIRECTORY> \\
        python <SKILL_DIRECTORY>/scripts/preprocess_javaparser.py \\
        --state <CONVERSION>/migration_state.json \\
        --runner-jar /path/to/scos-javaparser-runner.jar

    # Disable Maven auto-build (hard-fail if prebuilt jar absent)
    uv run --project <SKILL_DIRECTORY> \\
        python <SKILL_DIRECTORY>/scripts/preprocess_javaparser.py \\
        --state <CONVERSION>/migration_state.json \\
        --no-build
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHASE_KEY = "0_5c_javaparser"

# Fat-jar produced by scripts/javaparser_maven/pom.xml via maven-shade-plugin.
MAVEN_JAR = pathlib.Path(__file__).parent / "javaparser_maven" / "target" / "scos-javaparser-runner.jar"
MAVEN_POM = pathlib.Path(__file__).parent / "javaparser_maven" / "pom.xml"

# Fully-qualified main classes inside the fat-jar.
REWRITE_MAIN = "com.snowflake.scos.javaparser.ScosJavaRewrite"

RULE_PREFIX = "javaparser:"

# 14 rewrite/annotate rules in application order — mirrors ALL_RULES in
# ScosJavaRewrite.java (must stay in sync with that list).
JAVA_RULES: list[str] = [
    "ScosSparkSessionBuilderRewrite",
    "ScosCheckpointToCache",
    "ScosMapSubscriptToElementAt",
    "ScosWildcardReadAnnotate",
    "ScosSaveAsTableDropStorageOpts",
    "ScosExternalCloudReadAnnotate",
    "ScosSelfJoinUnaliasedAnnotate",
    "ScosSparkContextPropertyFallbackAnnotate",
    "ScosUdtfCompatibilityModeAnnotate",
    "ScosUnionByNameAllowMissingAnnotate",
    "ScosDriverHotPathAnnotate",
    "ScosTempViewMultiUseCache",
    "ScosSystemGetenvRewrite",
    "ScosDeltaTableAnnotate",
    # ── Ported from Scalafix for Java↔Scala parity (SNOW-3715354) ──
    "ScosApproxCountDistinctDropRsd",
    "ScosDbUtilsSecretsGetStub",
    "ScosDbUtilsWidgetsToProperty",
    "ScosDeltaWriteToParquet",
    "ScosDisplayToShow",
    "ScosDisplayMethodToShow",
    "ScosHadoopConfCredentialAnnotate",
    "ScosPartitionNoopStrip",
    "ScosRddExclusiveMethodAnnotate",
    "ScosRddImportAnnotate",
    "ScosRddPersistToCache",
    "ScosScTextfileToReadText",
    "ScosScWholeTextFilesAnnotate",
    "ScosSnowflakeConnectorIO",
    "ScosSparkConfigNoopAnnotate",
    "ScosSparkContextGetOrCreateRewrite",
    "ScosSparkContextNoopCommentOut",
    "ScosSparkIoDetectAnnotate",
    "ScosUnpersistDropBlockingArg",
]

# notebook_io.is_notebook — imported gracefully; when unavailable no files are
# filtered (Databricks .java notebooks are rare; the guard is defensive).
try:
    from notebook_io import is_notebook as _is_notebook  # type: ignore[import]
except ImportError:
    def _is_notebook(p: str) -> bool:  # type: ignore[misc]
        return False


# ── state helpers ─────────────────────────────────────────────────────────────


def _load_state(state_path: pathlib.Path) -> dict[str, Any]:
    with state_path.open() as fh:
        return json.load(fh)


def _save_state(state_path: pathlib.Path, state: dict[str, Any]) -> None:
    tmp = state_path.with_suffix(".tmp")
    with tmp.open("w") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, state_path)
    print(f"[Phase 0.5c] migration_state.json updated → {state_path}")


# ── runner resolution ─────────────────────────────────────────────────────────


def _resolve_runner_invocation(
    runner_jar: str | None,
    no_build: bool,
) -> tuple[tuple[str, str] | None, str | None]:
    """Return ``((jar_path, java_exe), None)`` or ``(None, failure_reason)``.

    Resolution order:
    1. ``runner_jar`` override (``--runner-jar`` flag).
    2. Prebuilt fat-jar at ``MAVEN_JAR``.
    3. Maven auto-build (unless ``no_build`` is True).
    """
    java = shutil.which("java")
    if java is None:
        return None, "java not on PATH"

    if runner_jar:
        p = pathlib.Path(runner_jar).expanduser().resolve()
        if p.exists():
            return (str(p), java), None
        return None, f"--runner-jar not found: {p}"

    if MAVEN_JAR.exists():
        return (str(MAVEN_JAR), java), None

    if no_build:
        return None, f"prebuilt fat-jar absent ({MAVEN_JAR}) and --no-build is set"

    mvn = shutil.which("mvn")
    if mvn is None:
        return None, f"prebuilt fat-jar absent ({MAVEN_JAR}) and mvn not on PATH"
    if not MAVEN_POM.exists():
        return None, f"prebuilt fat-jar absent and pom.xml not found at {MAVEN_POM}"

    print("[Phase 0.5c] Building javaparser runner via Maven …")
    try:
        result = subprocess.run(
            [mvn, "-q", "-f", str(MAVEN_POM), "package", "-DskipTests"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return None, "Maven build timed out"
    except OSError as exc:
        return None, f"Maven build failed to start: {exc}"

    if result.returncode != 0 or not MAVEN_JAR.exists():
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        detail = tail[-1] if tail else f"exit {result.returncode}"
        return None, f"Maven build failed: {detail}"

    print("[Phase 0.5c] javaparser runner built successfully")
    return (str(MAVEN_JAR), java), None


# ── per-rule invocation ───────────────────────────────────────────────────────


def _run_rule_stdout(
    jar: str,
    java: str,
    rule_name: str,
    java_file: pathlib.Path,
) -> tuple[bool, str | None]:
    """Run ONE rule on *java_file* via ``ScosJavaRewrite --stdout``.

    Returns ``(ok, rewritten_content)``; on failure returns ``(False, None)``
    so the caller can skip this rule and continue.  The file content is read
    from ``java_file`` (a temp copy maintained by the caller) and the rewritten
    source is returned via stdout without touching the original on disk.
    """
    cmd = [
        java, "-cp", jar, REWRITE_MAIN,
        "--source", str(java_file),
        "--rule", rule_name,
        "--stdout",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print(
            f"[Phase 0.5c] WARN: javaparser timed out on {java_file.name} ({rule_name})",
            file=sys.stderr,
        )
        return False, None
    except Exception as exc:  # noqa: BLE001
        print(
            f"[Phase 0.5c] WARN: javaparser error on {java_file.name} ({rule_name}): {exc}",
            file=sys.stderr,
        )
        return False, None

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        msg = detail[-1] if detail else f"exit {result.returncode}"
        print(
            f"[Phase 0.5c] WARN: javaparser exited {result.returncode} on "
            f"{java_file.name} ({rule_name}): {msg}",
            file=sys.stderr,
        )
        return False, None

    return True, result.stdout


# ── difflib helpers ───────────────────────────────────────────────────────────


def _changed_src_lines(before: str, after: str) -> list[int]:
    """Return 1-based line numbers in *before* that a rule rewrote.

    Computed with difflib (the runner emits no per-edit metadata).  For pure
    insertions the anchor line is the insertion point.  Blank-only changes
    (e.g. trailing newline normalisation) are ignored to avoid spurious anchors.
    """
    b = before.splitlines()
    a = after.splitlines()

    def _blank(seq: list[str]) -> bool:
        return all(not s.strip() for s in seq)

    changed: set[int] = set()
    sm = difflib.SequenceMatcher(a=b, b=a, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if _blank(b[i1:i2]) and _blank(a[j1:j2]):
            continue
        if tag in ("replace", "delete"):
            changed.update(range(i1 + 1, i2 + 1))
        elif tag == "insert":
            changed.add(min(i1 + 1, len(b)) or 1)
    return sorted(changed)


def _anchors_for_rule(short_name: str, changed_lines: list[int]) -> list[dict[str, Any]]:
    """Build recipe_edits anchors for one rule's changed source lines.

    Anchor format matches the established contract:
      recipe_id          = "javaparser:<RuleName>"
      output_line_anchor = "javaparser:<RuleName>:<src_line>:<sha1[:8]>"
    """
    out: list[dict[str, Any]] = []
    for src_line in changed_lines:
        anchor_src = f"{RULE_PREFIX}{short_name}:{src_line}"
        digest = hashlib.sha1(anchor_src.encode()).hexdigest()[:8]
        out.append(
            {
                "recipe_id": f"{RULE_PREFIX}{short_name}",
                "src_line": src_line,
                "output_line_anchor": f"{RULE_PREFIX}{short_name}:{src_line}:{digest}",
            }
        )
    return out


# ── main ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 0.5c: mandatory JavaParser AST pre-processing "
            "for Spark Java → SCOS migrations."
        )
    )
    parser.add_argument(
        "--state",
        required=True,
        metavar="STATE_JSON",
        help="Path to migration_state.json written by Phase 0.",
    )
    parser.add_argument(
        "--runner-jar",
        metavar="JAR",
        default=None,
        dest="runner_jar",
        help="Override path to the scos-javaparser-runner fat-jar.",
    )
    parser.add_argument(
        "--no-build",
        dest="build",
        action="store_false",
        help=(
            "Disable Maven auto-build of the runner jar. "
            "Hard-fails if the prebuilt jar is absent. "
            "Opt out via SCOS_JAVAPARSER_NO_BUILD=1."
        ),
    )
    parser.set_defaults(build=True)
    args = parser.parse_args(argv)

    # Env var opt-out for Maven auto-build.
    build: bool = args.build
    if os.environ.get("SCOS_JAVAPARSER_NO_BUILD", "").lower() in ("1", "true"):
        build = False

    state_path = pathlib.Path(args.state).expanduser().resolve()
    if not state_path.exists():
        print(
            f"[Phase 0.5c] ERROR: state file not found: {state_path}",
            file=sys.stderr,
        )
        return 1

    state = _load_state(state_path)

    # ── 1. Resolve runner ──────────────────────────────────────────────────────
    runner, fail_reason = _resolve_runner_invocation(args.runner_jar, not build)
    if runner is None:
        # HARD FAIL: Java projects always have a JVM; a missing runner is a
        # genuine blocker, not a reason to skip.
        print(
            f"[Phase 0.5c] ERROR: no javaparser runner available ({fail_reason}). "
            "Java AST pre-processing is mandatory for Java migrations — "
            "install JDK + Maven (preferred), or pre-build the fat-jar, then re-run. "
            "See the SKILL Phase 0.5c prerequisites.",
            file=sys.stderr,
        )
        state.setdefault("phases_completed", {})[PHASE_KEY] = {
            "status": "failed",
            "skip_reason": fail_reason,
        }
        _save_state(state_path, state)
        return 1

    jar, java = runner
    print(f"[Phase 0.5c] Using javaparser runner: {jar}")

    # ── 2. Collect .java files from manifest ───────────────────────────────────
    migrated_dir = pathlib.Path(state.get("migrated_dir", "")).expanduser().resolve()
    manifest: list[str] = state.get("manifest", [])

    java_files: list[pathlib.Path] = []
    for rel in manifest:
        candidate = migrated_dir / rel
        if candidate.suffix == ".java" and candidate.exists():
            if not _is_notebook(str(candidate)):
                java_files.append(candidate)

    if not java_files:
        print("[Phase 0.5c] No .java files in manifest — nothing to do.")
        state.setdefault("phases_completed", {})[PHASE_KEY] = {
            "status": "skipped",
            "skip_reason": "no .java files in manifest",
        }
        _save_state(state_path, state)
        return 0

    print(f"[Phase 0.5c] Processing {len(java_files)} .java file(s) with {len(JAVA_RULES)} rules …")

    # ── 3. Process files ───────────────────────────────────────────────────────
    recipe_edits: dict[str, list[dict[str, Any]]] = state.setdefault("recipe_edits", {})
    failures: list[str] = []
    files_modified = 0
    total_edits = 0

    # Idempotency: skip files already processed by this phase (tracked via their
    # recorded javaparser: edits).
    eligible: list[pathlib.Path] = []
    for java_file in java_files:
        rel_path = str(java_file.relative_to(migrated_dir))
        prior = recipe_edits.get(rel_path, [])
        if any(str(e.get("recipe_id", "")).startswith(RULE_PREFIX) for e in prior):
            print(f"[Phase 0.5c]   {rel_path} already processed — skipping (idempotent)")
            continue
        eligible.append(java_file)

    for java_file in eligible:
        rel_path = str(java_file.relative_to(migrated_dir))
        try:
            original = java_file.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"[Phase 0.5c] WARN: cannot read {rel_path}: {exc}", file=sys.stderr)
            failures.append(rel_path)
            continue

        current = original
        file_edits: list[dict[str, Any]] = []
        rules_ran_ok = 0

        # Apply each rule sequentially using a temp copy so the original file is
        # not touched until all rules have been applied.
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_file = pathlib.Path(tmpdir) / java_file.name
            for rule_name in JAVA_RULES:
                before_rule = current
                try:
                    tmp_file.write_text(before_rule, encoding="utf-8")
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[Phase 0.5c] WARN: {rel_path} ({rule_name}): "
                        f"cannot write temp: {exc}",
                        file=sys.stderr,
                    )
                    continue

                ok, rewritten = _run_rule_stdout(jar, java, rule_name, tmp_file)
                if not ok or rewritten is None:
                    continue

                rules_ran_ok += 1
                if rewritten != before_rule:
                    changed = _changed_src_lines(before_rule, rewritten)
                    if changed:
                        file_edits.extend(_anchors_for_rule(rule_name, changed))
                    current = rewritten

        # A file counts as failed only when EVERY rule errored on it.
        if JAVA_RULES and rules_ran_ok == 0:
            failures.append(rel_path)
            print(
                f"[Phase 0.5c] WARN: skipping {rel_path} (all rules failed)",
                file=sys.stderr,
            )
            continue

        # Write final cumulative content back to disk.
        if current != original:
            try:
                java_file.write_text(current, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[Phase 0.5c] WARN: {rel_path}: cannot write output: {exc}",
                    file=sys.stderr,
                )
                failures.append(rel_path)
                continue

        # Merge anchors, avoiding duplicates from any prior run.
        if file_edits:
            existing = recipe_edits.setdefault(rel_path, [])
            existing_anchors = {e["output_line_anchor"] for e in existing}
            new_edits = [e for e in file_edits if e["output_line_anchor"] not in existing_anchors]
            existing.extend(new_edits)
            total_edits += len(new_edits)
            files_modified += 1
            print(f"[Phase 0.5c]   modified {rel_path} ({len(new_edits)} edit(s))")

    # ── 4. Write phase completion entry ────────────────────────────────────────
    ran_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # Non-empty failures means at least one file had ALL rules error on it.
    # That is a hard gate failure: the AST contract for those files is unmet.
    phase_status = "failed" if failures else "passed"
    phase_entry: dict[str, Any] = {
        "status": phase_status,
        "ran_at": ran_at,
        "files_processed": len(eligible),
        "files_skipped_idempotent": len(java_files) - len(eligible),
        "files_modified": files_modified,
        "total_edits": total_edits,
        "rules_run": list(JAVA_RULES),
    }
    if failures:
        phase_entry["failure_count"] = len(failures)
        phase_entry["failures"] = failures
    state.setdefault("phases_completed", {})[PHASE_KEY] = phase_entry
    _save_state(state_path, state)

    # ── 5. Print summary ───────────────────────────────────────────────────────
    print()
    print("PHASE 0.5c SUMMARY")
    print(f"  Files processed : {len(eligible)}")
    print(f"  Files skipped   : {len(java_files) - len(eligible)} (idempotent)")
    print(f"  Files modified  : {files_modified}")
    print(f"  Total edits     : {total_edits}")
    print(f"  Rules run       : {', '.join(JAVA_RULES)}")
    if failures:
        print(f"  Failures        : {len(failures)} file(s) had ALL rules fail — status=failed")
        for f in failures:
            print(f"    - {f}")
    print()

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
