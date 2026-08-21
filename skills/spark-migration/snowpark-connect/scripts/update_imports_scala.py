#!/usr/bin/env python3
"""Deterministic Phase 3 for Scala: imports, session-init, build files, headers.

This is the Scala counterpart of ``update_imports.py`` (PySpark). It replaces the
former ``agents/import-updater.md`` LLM specialist with a mechanical script: every
action the agent performed was deterministic (rename the session builder, drop
unsupported imports, transform the build file, stamp a header), so it belongs in
code, not an LLM.

What it does, per the ``agents/import-updater.md`` spec and the
``verify_phase.py --phase 3`` gate it must satisfy:

  1. Session init (production entry files only): rename
     ``SparkSession.builder`` -> ``SnowparkConnectSession.builder``, inject the
     ``import com.snowflake.snowpark_connect.client.SnowparkConnectSession``,
     drop ``.enableHiveSupport()`` / ``.master(...)`` / ``.remote(...)`` calls,
     and materialize any ``// SCOS-RECIPE-PRESERVED-CONFIG: k=v`` markers into
     ``.config("k", "v")`` on the new builder. Test files (``*Spec/Test/Suite``)
     keep ``master("local[*]")`` and are NOT converted (a TODO is added).
  2. Unsupported imports: delete entire import lines for unsupported packages.
  3. Build files: transform ``build.sbt`` / ``pom.xml`` / ``build.gradle`` /
     ``build.gradle.kts`` to use ``snowpark-connect-java-client`` with pinned
     versions and the Arrow ``--add-opens`` JVM flags.
  4. Migration header: prepend an idempotent SCOS block comment to every file.

Run::

    uv run --project <SKILL_DIRECTORY> \
      python <SKILL_DIRECTORY>/scripts/update_imports_scala.py \
      --state <CONVERSION>/migration_state.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))


def _write_state_atomic(state_path: str, state: dict) -> None:
    """Write state JSON atomically via tmp+rename to avoid truncation on interrupt."""
    data = json.dumps(state, indent=2)
    p = Path(state_path)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

try:
    import notebook_io  # type: ignore[import-not-found]
    _NOTEBOOK_IO = True
except ImportError:  # pragma: no cover - depends on host packaging
    notebook_io = None  # type: ignore[assignment]
    _NOTEBOOK_IO = False

# Pinned, verified versions (kept in sync with the import-updater spec).
_SCOS_CLIENT_VERSION = "1.0.0"   # pinned concrete version for reproducible builds
_SPARK_VERSION = "3.5.6"
_SCALA_DEFAULT_SHORT = "2.12"
_SCOS_IMPORT = "import com.snowflake.snowpark_connect.client.SnowparkConnectSession"
_EWI_SESSION = "// SCOS: [SPRKCNTSCL3500] Converted to Snowpark Connect session"

_ADD_OPENS = (
    "--add-opens=java.base/java.lang=ALL-UNNAMED",
    "--add-opens=java.base/java.lang.invoke=ALL-UNNAMED",
    "--add-opens=java.base/java.lang.reflect=ALL-UNNAMED",
    "--add-opens=java.base/java.io=ALL-UNNAMED",
    "--add-opens=java.base/java.net=ALL-UNNAMED",
    "--add-opens=java.base/java.nio=ALL-UNNAMED",
    "--add-opens=java.base/java.util=ALL-UNNAMED",
    "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED",
    "--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED",
    "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED",
    "--add-opens=java.base/sun.nio.cs=ALL-UNNAMED",
    "--add-opens=java.base/sun.security.action=ALL-UNNAMED",
    "--add-opens=java.base/sun.util.calendar=ALL-UNNAMED",
    # Arrow off-heap DirectBuffer allocation (required on JDK 17)
    "--add-opens=java.base/jdk.internal.misc=ALL-UNNAMED",
    "--add-opens=jdk.unsupported/sun.misc=ALL-UNNAMED",
)

_TEST_FILE_RE = re.compile(r"(Spec|Test|Suite)\.scala$|/src/test/|[\\/]test[\\/]")

# sys.env rewrite patterns (Scala stdlib; not available via System.setProperty injection)
# These three forms cover the idiomatic usages. All three target call-expression forms
# so they cannot match string literals that contain "sys.env".
_SYS_ENV_GET_OR_ELSE_RE = re.compile(
    r'sys\.env\.getOrElse\(\s*("(?:[^"\\]|\\.)*")\s*,\s*("(?:[^"\\]|\\.)*")\s*\)'
)
_SYS_ENV_GET_RE = re.compile(
    r'sys\.env\.get\(\s*("(?:[^"\\]|\\.)*")\s*\)'
)
_SYS_ENV_DIRECT_RE = re.compile(
    r'sys\.env\(\s*("(?:[^"\\]|\\.)*")\s*\)'
)

# System.getenv rewrite pattern — same injection problem: EnvUtil.setEnv writes via
# System.setProperty, which System.getenv() never reads (reads the OS process env).
_SYSTEM_GETENV_RE = re.compile(
    r'System\.getenv\(\s*("(?:[^"\\]|\\.)*")\s*\)'
)

# DeltaTable usage annotation — fired when delta.tables import was deleted but call
# sites remain, which would otherwise produce unresolved-reference compile errors.
_DELTA_TABLE_USAGE_RE = re.compile(
    r'\bDeltaTable\s*\.\s*(forPath|forName|forUid|columnExists|isDeltaTable)\s*\('
)

# Unsupported import prefixes (gate-flagged set + clearly-unsupported Spark
# subprojects). A line is dropped when it is an `import` of one of these.
_UNSUPPORTED_IMPORT_PREFIXES = (
    "org.apache.spark.sql.catalyst",
    "org.apache.spark.sql.hive",
    "org.apache.spark.graphx",
    "org.apache.spark.streaming",
    "org.apache.spark.mllib",
    "org.apache.spark.ml",
    "org.apache.hadoop",
    "com.hortonworks",
    "za.co.absa.spline",
    "delta.tables",
)

_PRESERVED_CFG_RE = re.compile(r"SCOS-RECIPE-PRESERVED-CONFIG:\s*(\S+?)=(.*?)\s*$")
# Inline ``// SCOS:`` on an import line is a Phase-2 verifier syntax artifact.
# Hoist the comment to its own line above the import deterministically.
_INLINE_SCOS_ON_IMPORT_RE = re.compile(r"^(import\s+\S+[^\S\r\n]*)(//\s*SCOS:.*)$", re.MULTILINE)
_INSERT_AFTER_RE = re.compile(r"^\s*//\s*SCOS-RECIPE-INSERT-AFTER-BUILDER:")
_MIGRATION_MARKER = "SCOS Migration Output"

# Match ``SparkSession.builder`` even when the idiomatic Scala fluent style splits
# the object from ``.builder`` across whitespace/newlines, e.g.::
#     lazy val spark = SparkSession
#         .builder()
# A plain ``"SparkSession.builder" in source`` substring test misses this form and
# leaves the entry-point session unconverted. The capture group preserves the
# original separator so only the receiver object is renamed.
_SPARK_BUILDER_RE = re.compile(r"SparkSession(\s*\.\s*builder)")
# Locate the (already-renamed) Snowpark builder factory call — also whitespace/
# newline tolerant — so preserved config can be appended right after it. The
# optional group captures an existing ``()`` so we know whether to add one.
_SCOS_BUILDER_RE = re.compile(r"SnowparkConnectSession\s*\.\s*builder(\s*\(\s*\))?")


# --------------------------------------------------------------------------- #
# Session-init transform
# --------------------------------------------------------------------------- #

def _unquote(tok: str) -> str:
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ("\"", "'"):
        return tok[1:-1]
    return tok


def _collect_preserved_config(source: str) -> list[tuple[str, str]]:
    """Return ordered, de-duplicated (key, value) pairs from PRESERVED-CONFIG
    markers, excluding Hive-only keys (which SCOS does not honor)."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in source.splitlines():
        m = _PRESERVED_CFG_RE.search(line)
        if not m:
            continue
        k, v = _unquote(m.group(1)), _unquote(m.group(2))
        if k.startswith("hive.") or k.startswith("spark.sql.hive."):
            continue
        # Strip Delta session extension — io.delta.sql.DeltaSparkSessionExtension is
        # unavailable in SCOS; re-materialising it causes a runtime failure.
        if k == "spark.sql.extensions" and "delta" in v.lower():
            continue
        if (k, v) not in seen:
            seen.add((k, v))
            pairs.append((k, v))
    return pairs


def _drop_call(source: str, method: str) -> str:
    """Remove ``.<method>(...)`` calls with a paren-balanced walk.

    Handles nested parens (e.g. ``.remote(buildUri(cfg))``) and multi-line
    argument lists that the previous ``[^()]*`` regex could not reach.
    """
    pattern = re.compile(rf"\.{re.escape(method)}\s*\(")
    parts: list[str] = []
    pos = 0
    for m in pattern.finditer(source):
        parts.append(source[pos:m.start()])
        depth = 1
        i = m.end()
        while i < len(source) and depth:
            ch = source[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        pos = i  # skip past the balanced closing paren
    parts.append(source[pos:])
    return "".join(parts)


def replace_session_init(source: str, *, is_test: bool) -> tuple[str, int]:
    """Rename the Spark session builder to Snowpark Connect and materialize
    preserved config. Returns ``(new_source, replacements)``.

    Phase 0.5 (ScosSparkSessionBuilderRewrite Scalafix rule) already renames
    SparkSession → SnowparkConnectSession and drops .master()/.enableHiveSupport()/
    .remote() for non-test files.  This function detects that case (has_scos and
    not has_spark) and skips the drop/rename, proceeding directly to config
    materialization and import injection — which MUST happen here, after the
    Phase 2 marker-survival gate.

    When Phase 0.5 did NOT run (no toolchain, or SCOS_SCALAFIX_USE_SBT=0), has_spark
    will be True and this function falls back to the full regex rename+drop path.

    Test files are left on SparkSession (a TODO is added) so local integration
    harnesses keep their master("local[*]") runner.
    """
    has_spark = bool(_SPARK_BUILDER_RE.search(source))
    has_scos = bool(_SCOS_BUILDER_RE.search(source))

    if not has_spark and not has_scos:
        return source, 0

    if is_test:
        # Only add TODO when still on SparkSession — Phase 0.5 correctly skips
        # test files (by file-name convention), so has_spark is expected here.
        if has_spark and "SCOS: TODO" not in source:
            source = (
                "// SCOS: TODO - convert this test to SnowparkConnectSession for "
                "SCOS integration testing (kept local SparkSession for now)\n"
                + source
            )
        return source, 0

    new = source
    # Phase 0.5 fallback: rename + drop only when SparkSession is still present.
    # When Phase 0.5 already renamed, skip straight to materialization below.
    if has_spark:
        # Drop unsupported builder calls before the rename so the gate's prod-code
        # checks (no enableHiveSupport / master / remote) pass.
        new = _drop_call(new, "enableHiveSupport")
        new = _drop_call(new, "master")
        new = _drop_call(new, "remote")
        # Rename only the receiver object, preserving the original (possibly
        # multi-line) separator before ``.builder``.
        new = _SPARK_BUILDER_RE.sub(r"SnowparkConnectSession\1", new)

    # Materialize preserved config onto the new builder.
    pairs = _collect_preserved_config(new)
    if pairs:
        config_chain = "".join(f'.config("{k}", "{v}")' for k, v in pairs
                               if f'.config("{k}", "{v}")' not in new)
        if config_chain:
            # Append onto the builder factory call (whitespace/newline tolerant).
            # If the matched form lacks ``()`` (e.g. ``.builder`` alone), add it.
            m = _SCOS_BUILDER_RE.search(new)
            if m:
                prefix = "" if m.group(1) else "()"
                new = new[:m.end()] + prefix + config_chain + new[m.end():]

    # Inject the import (idempotent) and the EWI marker once.
    if _SCOS_IMPORT not in new:
        new = _inject_import(new, _SCOS_IMPORT, ewi=_EWI_SESSION)

    # Remove now-resolved INSERT-AFTER-BUILDER hint lines (would be flagged stale).
    new = "\n".join(
        ln for ln in new.split("\n") if not _INSERT_AFTER_RE.search(ln)
    )
    return new, 1


def _inject_import(source: str, import_line: str, *, ewi: str | None = None) -> str:
    """Insert ``import_line`` after the package decl / existing import block."""
    lines = source.split("\n")
    insert_at = 0
    for idx, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("package "):
            insert_at = idx + 1
        elif s.startswith("import "):
            insert_at = idx + 1
        elif s == "" or s.startswith("//") or s.startswith("/*") or s.startswith("*"):
            continue
        else:
            break
    block = ([ewi, import_line] if ewi else [import_line])
    lines[insert_at:insert_at] = block
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Unsupported-import removal
# --------------------------------------------------------------------------- #

# Pattern for SCOS-RECIPE-INSERT-IMPORT markers emitted by Phase 0.5 Scalafix
# rules (e.g. ScosSnowflakeConnectorIO).  A rule uses this marker to request
# that Phase 3 inject a specific import, avoiding the need for SemanticDB in
# the syntactic Scalafix pass.
_INSERT_IMPORT_RE = re.compile(r"^\s*//\s*SCOS-RECIPE-INSERT-IMPORT:\s+(.+)$")


def process_insert_import_markers(source: str) -> tuple[str, int]:
    """Inject imports requested via ``// SCOS-RECIPE-INSERT-IMPORT: <class>`` and
    remove the marker lines.

    Each marker line becomes an ``import <class>`` statement placed after the
    existing package/import block (via ``_inject_import``).  Duplicate markers
    for the same class are de-duplicated.  The marker lines are consumed so they
    do not appear in the final output.
    """
    requested: list[str] = []
    clean_lines: list[str] = []
    for line in source.split("\n"):
        m = _INSERT_IMPORT_RE.match(line)
        if m:
            cls = m.group(1).strip()
            if cls not in requested:
                requested.append(cls)
        else:
            clean_lines.append(line)

    if not requested:
        return source, 0

    new = "\n".join(clean_lines)
    for cls in requested:
        import_line = f"import {cls}"
        # Only inject if not already present
        if import_line not in new:
            new = _inject_import(new, import_line)
    return new, len(requested)


def comment_unsupported_imports(source: str) -> tuple[str, int]:
    """Delete entire import lines for unsupported packages (clean removal —
    Rule 21: no trailing fragments / em-dashes left behind)."""
    out: list[str] = []
    removed = 0
    for line in source.split("\n"):
        stripped = line.strip()
        if stripped.startswith("import "):
            target = stripped[len("import "):].lstrip()
            if any(target.startswith(p) for p in _UNSUPPORTED_IMPORT_PREFIXES):
                removed += 1
                continue
        out.append(line)
    return "\n".join(out), removed


# --------------------------------------------------------------------------- #
# Migration header
# --------------------------------------------------------------------------- #

def _scos_todos(source: str) -> list[str]:
    todos: list[str] = []
    for line in source.splitlines():
        if "SCOS: TODO" in line or "SCOS-TODO" in line:
            todos.append(line.strip().lstrip("/").strip())
    return todos


def add_migration_header(source: str, original_path: str) -> tuple[str, bool]:
    """Prepend the SCOS migration block comment if absent (idempotent)."""
    head = "\n".join(source.splitlines()[:10])
    if _MIGRATION_MARKER in head:
        return source, False
    todos = _scos_todos(source)
    limitations = todos if todos else ["None — all issues resolved"]
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "/*",
        f" * {_MIGRATION_MARKER}",
        " * =====================",
        f" * Source File: {original_path}",
        f" * Migrated on: {date}",
        " *",
        " * Changes Overview:",
        " * - Imports, session initialization, and build config updated for Snowpark Connect (SCOS).",
        " *",
        " * Known Limitations:",
    ]
    lines += [f" * - {lim}" for lim in limitations]
    lines.append(" */")
    return "\n".join(lines) + "\n" + source, True


# --------------------------------------------------------------------------- #
# Per-file orchestration
# --------------------------------------------------------------------------- #

def rewrite_sys_env(source: str) -> tuple[str, int]:
    """Rewrite ``sys.env`` calls to ``System.getProperty`` so harness injection works.

    The JVM cannot mutate ``System.getenv`` in-process, so ``sys.env(...)`` /
    ``sys.env.getOrElse(...)`` calls never see values injected by ``EnvUtil.setEnv``
    (which writes via ``System.setProperty``).  This rewrite is deterministic and
    safe — ``sys.env`` is the standard-library accessor; the three patterns below
    cover all idiomatic forms without touching string literals.

    Returns ``(new_source, rewrite_count)``.
    """
    new, n = source, 0
    # sys.env.getOrElse("K", "default") -> System.getProperty("K", "default")
    def _sub_or_else(m: re.Match) -> str:
        return f"System.getProperty({m.group(1)}, {m.group(2)})"
    new2 = _SYS_ENV_GET_OR_ELSE_RE.sub(_sub_or_else, new)
    n += new2 != new; new = new2
    # sys.env.get("K") -> Option(System.getProperty("K"))
    new2 = _SYS_ENV_GET_RE.sub(lambda m: f"Option(System.getProperty({m.group(1)}))", new)
    n += new2 != new; new = new2
    # sys.env("K") -> System.getProperty("K")
    new2 = _SYS_ENV_DIRECT_RE.sub(lambda m: f"System.getProperty({m.group(1)})", new)
    n += new2 != new; new = new2
    return new, n


def rewrite_system_getenv(source: str) -> tuple[str, int]:
    """Rewrite ``System.getenv("K")`` → ``System.getProperty("K")``.

    ``EnvUtil.setEnv`` writes values via ``System.setProperty``; ``System.getenv``
    reads the OS process environment which the JVM cannot mutate in-process.
    Any workload calling ``System.getenv(key)`` to read harness-injected SCOS_*
    variables will silently receive ``null`` unless rewritten here.

    Returns ``(new_source, rewrite_count)``.
    """
    new2 = _SYSTEM_GETENV_RE.sub(lambda m: f"System.getProperty({m.group(1)})", source)
    return new2, (1 if new2 != source else 0)


def annotate_delta_table_usages(source: str) -> tuple[str, int]:
    """Prepend a SCOS annotation above each ``DeltaTable.*`` call site.

    Phase 3 deletes ``import delta.tables`` lines.  Any ``DeltaTable.forPath(...)``
    etc. that remains without its import causes an unresolved-reference compile error.
    This annotation makes the breakage visible and tags the line for human fix.

    Returns ``(new_source, annotation_count)``.
    """
    annotation = "// SCOS: [SPRKCNTSCL1000] DeltaTable API not available in SCOS — rewrite to spark.read.table() or spark.sql(); manual refactor required"
    lines = source.splitlines(keepends=True)
    out: list[str] = []
    n = 0
    for line in lines:
        if _DELTA_TABLE_USAGE_RE.search(line) and annotation not in line:
            indent = len(line) - len(line.lstrip())
            out.append(" " * indent + annotation + "\n")
            n += 1
        out.append(line)
    return "".join(out), n


def transform_scala_source(source: str, original_path: str) -> tuple[str, dict]:
    """Apply all Phase-3 source transforms to one Scala file."""
    is_test = bool(_TEST_FILE_RE.search(original_path.replace("\\", "/")))
    stats = {"session_replaced": 0, "imports_removed": 0, "header_added": False,
             "sys_env_rewrites": 0, "system_getenv_rewrites": 0, "delta_table_annotations": 0,
             "insert_imports": 0}

    new, n_sess = replace_session_init(source, is_test=is_test)
    stats["session_replaced"] = n_sess

    new, n_env = rewrite_sys_env(new)
    stats["sys_env_rewrites"] = n_env

    new, n_getenv = rewrite_system_getenv(new)
    stats["system_getenv_rewrites"] = n_getenv

    # Inject imports requested by Phase 0.5 Scalafix rules via INSERT-IMPORT markers.
    new, n_ins = process_insert_import_markers(new)
    stats["insert_imports"] = n_ins

    new, n_imp = comment_unsupported_imports(new)
    stats["imports_removed"] = n_imp

    # Hoist any inline ``// SCOS:`` comment off import lines (verifier syntax-artifact check).
    new = _INLINE_SCOS_ON_IMPORT_RE.sub(r"\2\n\1", new)

    # Annotate DeltaTable.* call sites whose import was just deleted — leaving them
    # bare causes unresolved-reference compile errors.
    if "delta.tables" in source and n_imp:
        new, n_dt = annotate_delta_table_usages(new)
        stats["delta_table_annotations"] = n_dt

    new, header_added = add_migration_header(new, original_path)
    stats["header_added"] = header_added
    return new, stats


# --------------------------------------------------------------------------- #
# Build-file transforms
# --------------------------------------------------------------------------- #

# Dependency fragments that must never survive in a transformed build file.
_FORBIDDEN_DEP_SUBSTRINGS = (
    "spark-connect-client-jvm",
    "spark-hive",
    "com.hortonworks",
    "za.co.absa.spline",
)

_SCOS_MANAGED_MARKER = "SCOS-MANAGED: snowpark-connect dependency"

# Known artifact group prefixes where a Scala 2.12 cross-build is guaranteed.
# Only lines matching one of these have _2.11 → _2.12 applied.
_SCALA_BINARY_UPDATE_ANCHORS = (
    "org.apache.spark",
    "com.snowflake",
    "com.databricks",
    "io.delta",
)


def _update_scala_binary(text: str) -> str:
    """Replace ``_2.11`` → ``_2.12`` only in dependency lines for known artifacts."""
    lines = text.splitlines(keepends=True)
    return "".join(
        ln.replace("_2.11", "_2.12")
        if "_2.11" in ln and any(a in ln for a in _SCALA_BINARY_UPDATE_ANCHORS)
        else ln
        for ln in lines
    )


def _detect_scala_short(text: str) -> str:
    m = re.search(r'scalaVersion\s*:?=?\s*"(\d+\.\d+)', text) or \
        re.search(r"<scala\.version>(\d+\.\d+)", text)
    return m.group(1) if m else _SCALA_DEFAULT_SHORT


def _strip_forbidden_dep_lines(text: str) -> str:
    return "\n".join(
        ln for ln in text.split("\n")
        if not any(sub in ln for sub in _FORBIDDEN_DEP_SUBSTRINGS)
    )


def _transform_sbt(text: str) -> str:
    short = _detect_scala_short(text)
    text = _strip_forbidden_dep_lines(text)
    text = _update_scala_binary(text)
    if "snowpark-connect-java-client" not in text:
        block = (
            f"\n// {_SCOS_MANAGED_MARKER}\n"
            f'val scalaShort = "{short}"\n'
            f'libraryDependencies += "com.snowflake" % s"snowpark-connect-java-client_$scalaShort" '
            f'% "{_SCOS_CLIENT_VERSION}"\n'
        )
        text = text.rstrip("\n") + "\n" + block
    if "add-opens" not in text:
        flags = ",\n  ".join(f'"{f}"' for f in _ADD_OPENS)
        text = text.rstrip("\n") + "\n\nTest / javaOptions ++= Seq(\n  " + flags + "\n)\n"
    return text


def _transform_maven(text: str) -> str:
    text = _strip_forbidden_dep_lines(text)
    text = _update_scala_binary(text)
    text = re.sub(r"<scala\.version>2\.11(\.\d+)?</scala\.version>",
                  "<scala.version>2.12.18</scala.version>", text)
    if "snowpark-connect-java-client" not in text:
        dep = (
            "    <!-- SCOS-MANAGED: snowpark-connect dependency -->\n"
            "    <dependency>\n"
            "      <groupId>com.snowflake</groupId>\n"
            "      <artifactId>snowpark-connect-java-client_${scala.short}</artifactId>\n"
            "      <!-- SCOS: TODO pin snowpark-connect-java-client version "
            "(Maven has no safe dynamic keyword) -->\n"
            "      <version>PIN_CONCRETE_VERSION</version>\n"
            "    </dependency>\n"
        )
        if "</dependencies>" in text:
            text = text.replace("</dependencies>", dep + "  </dependencies>", 1)
        else:
            text = text.rstrip("\n") + "\n" + dep
    return text


def _transform_gradle(text: str, *, kotlin: bool) -> str:
    short = _detect_scala_short(text)
    text = _strip_forbidden_dep_lines(text)
    text = _update_scala_binary(text)
    if "snowpark-connect-java-client" not in text:
        if kotlin:
            dep = (f'    implementation("com.snowflake:snowpark-connect-java-client_{short}'
                   f':{_SCOS_CLIENT_VERSION}")')
        else:
            dep = (f'    implementation "com.snowflake:snowpark-connect-java-client_{short}'
                   f':{_SCOS_CLIENT_VERSION}"')
        dep = f"    // {_SCOS_MANAGED_MARKER}\n" + dep
        if re.search(r"dependencies\s*\{", text):
            text = re.sub(r"(dependencies\s*\{)", r"\1\n" + dep, text, count=1)
        else:
            text = text.rstrip("\n") + "\n\ndependencies {\n" + dep + "\n}\n"
    if "add-opens" not in text:
        if kotlin:
            flags = ",\n        ".join(f'"{f}"' for f in _ADD_OPENS)
            block = "\ntasks.test {\n    jvmArgs(\n        " + flags + "\n    )\n}\n"
        else:
            flags = ",\n            ".join(f"'{f}'" for f in _ADD_OPENS)
            block = "\ntest {\n    jvmArgs " + flags + "\n}\n"
        text = text.rstrip("\n") + "\n" + block
    return text


def transform_build_file(name: str, text: str) -> tuple[str, bool]:
    """Transform one build file by filename. Returns ``(new_text, changed)``."""
    base = os.path.basename(name)
    if base == "build.sbt":
        new = _transform_sbt(text)
    elif base == "pom.xml":
        new = _transform_maven(text)
    elif base == "build.gradle":
        new = _transform_gradle(text, kotlin=False)
    elif base == "build.gradle.kts":
        new = _transform_gradle(text, kotlin=True)
    else:
        return text, False
    return new, new != text


# --------------------------------------------------------------------------- #
# File / notebook drivers
# --------------------------------------------------------------------------- #

def transform_file(output_path: str) -> dict:
    """Transform one already-copied file in ``Output/`` in place."""
    res = {"session_replaced": 0, "imports_removed": 0, "header_added": False,
           "cells_processed": 0, "error": None}
    try:
        if _NOTEBOOK_IO and notebook_io.is_notebook(output_path):
            return _transform_notebook(output_path)
        content = Path(output_path).read_text(encoding="utf-8", errors="ignore")
        new_content, stats = transform_scala_source(content, os.path.basename(output_path))
        res.update(stats)
        if new_content != content:
            Path(output_path).write_text(new_content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        res["error"] = str(exc)
    return res


def _transform_notebook(output_path: str) -> dict:
    res = {"session_replaced": 0, "imports_removed": 0, "header_added": False,
           "cells_processed": 0, "error": None}
    try:
        nb = notebook_io.parse_notebook(output_path)
        for cell in nb.cells:
            if cell.cell_type != "code" or cell.cell_language != "scala":
                continue
            is_test = bool(_TEST_FILE_RE.search(output_path.replace("\\", "/")))
            new, n_sess = replace_session_init(cell.source, is_test=is_test)
            new, n_imp = comment_unsupported_imports(new)
            if new != cell.source:
                cell.source = new
            res["session_replaced"] += n_sess
            res["imports_removed"] += n_imp
            res["cells_processed"] += 1
        header_present = any(
            c.cell_type == "markdown" and _MIGRATION_MARKER in c.source for c in nb.cells
        )
        # Also check for the // comment form used in exported_text notebooks
        if not header_present:
            header_present = any(
                _MIGRATION_MARKER in c.source for c in nb.cells
            )
        if not header_present and hasattr(notebook_io, "Cell"):
            header_lines = [
                _MIGRATION_MARKER, "=====================",
                f"Source File: {os.path.basename(output_path)}",
                "Imports/session/build updated for Snowpark Connect (SCOS).",
            ]
            nb_format = getattr(nb, "format", "")
            if nb_format == "exported_text":
                # exported-text notebooks serialize cell source as-is with no
                # // prefix — use // comment lines so the header is valid Scala
                body = "\n".join("// " + line for line in header_lines)
                cell_type = "code"
            else:
                # ipynb / native formats: use a proper markdown cell
                body = "\n".join(header_lines)
                cell_type = "markdown"
            md = notebook_io.Cell(index=0, cell_type=cell_type,
                                  cell_language="markdown" if cell_type == "markdown" else "scala",
                                  source=body)
            nb.cells.insert(0, md)
            res["header_added"] = True
        notebook_io.write_notebook(output_path, nb)
    except Exception as exc:  # noqa: BLE001
        res["error"] = str(exc)
    return res


# --------------------------------------------------------------------------- #
# State plumbing + CLI
# --------------------------------------------------------------------------- #

def _resolve_paths(state: dict, state_path: str) -> tuple[str, str]:
    conversion_root = state.get("conversion_root", os.path.dirname(state_path))
    migrated_dir = state.get("migrated_dir", os.path.join(conversion_root, "Output"))
    return conversion_root, migrated_dir


def _manifest_targets(state: dict, migrated_dir: str) -> list[str]:
    targets: list[str] = []
    source_dir = os.path.dirname(migrated_dir.rstrip("/"))
    for entry in state.get("manifest", []):
        if not isinstance(entry, str):
            continue
        if os.path.isabs(entry):
            # Absolute manifest entries (e.g. notebook paths from notebook_index)
            # already point at the on-disk file under migrated_dir — use them
            # directly. (Resolving them against source_dir produced a doubled
            # ``Output/Output/...`` path that never existed, silently skipping
            # every notebook in Phase 3 — they then reached Phase 4 header-less.)
            out_file = entry
            if not os.path.exists(out_file):
                # Fall back to re-rooting under migrated_dir by basename/relpath.
                try:
                    rel = os.path.relpath(entry, source_dir)
                except ValueError:
                    rel = os.path.basename(entry)
                out_file = os.path.join(migrated_dir, rel)
        else:
            out_file = os.path.join(migrated_dir, entry)
        if os.path.exists(out_file):
            targets.append(out_file)
    return targets


def _process_build_files(state: dict, migrated_dir: str) -> list[str]:
    """Transform the root build files plus any in state['build_files']."""
    changed: list[str] = []
    names: list[str] = ["build.sbt", "pom.xml", "build.gradle", "build.gradle.kts"]
    names += [b for b in (state.get("build_files", []) or []) if isinstance(b, str)]
    seen: set[str] = set()
    for name in names:
        bf = Path(migrated_dir) / name
        key = str(bf.resolve()) if bf.exists() else str(bf)
        if key in seen or not bf.exists():
            continue
        seen.add(key)
        text = bf.read_text(encoding="utf-8", errors="ignore")
        new_text, did = transform_build_file(name, text)
        if did:
            bf.write_text(new_text, encoding="utf-8")
            changed.append(name)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic Phase 3 (Scala): imports, session-init, build files, headers."
    )
    parser.add_argument("--state", required=True, help="Path to migration_state.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    state_path = os.path.abspath(args.state)
    if not os.path.exists(state_path):
        print(f"ERROR: migration_state.json not found: {state_path}", file=sys.stderr)
        return 1

    state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    _root, migrated_dir = _resolve_paths(state, state_path)
    targets = _manifest_targets(state, migrated_dir)

    print("SCOS Deterministic Import & Header Update — Scala (Phase 3)")
    print("==========================================================")
    print(f"  State:      {state_path}")
    print(f"  Output dir: {migrated_dir}")
    print(f"  Targets:    {len(targets)} file(s)")

    if args.dry_run:
        for t in targets:
            print(f"  DRY-RUN: would update {os.path.relpath(t, migrated_dir)}")
        return 0

    files_done: list[str] = []
    total_sess = total_imp = total_hdr = total_env = total_getenv = total_dt = 0
    errors: list[str] = []
    for out_file in targets:
        rel = os.path.relpath(out_file, migrated_dir)
        res = transform_file(out_file)
        if res.get("error"):
            print(f"  ERROR {rel} — {res['error']}")
            errors.append(rel)
            continue
        files_done.append(rel)
        total_sess   += res.get("session_replaced", 0)
        total_imp    += res.get("imports_removed", 0)
        total_env    += res.get("sys_env_rewrites", 0)
        total_getenv += res.get("system_getenv_rewrites", 0)
        total_dt     += res.get("delta_table_annotations", 0)
        if res.get("header_added"):
            total_hdr += 1
        flags = [f for f, on in (
            ("header",                    res.get("header_added")),
            (f"{res.get('session_replaced')} session", res.get("session_replaced")),
            (f"{res.get('imports_removed')} import(s)", res.get("imports_removed")),
            (f"{res.get('sys_env_rewrites')} sys.env",  res.get("sys_env_rewrites")),
            (f"{res.get('system_getenv_rewrites')} System.getenv", res.get("system_getenv_rewrites")),
            (f"{res.get('delta_table_annotations')} DeltaTable", res.get("delta_table_annotations")),
        ) if on]
        print(f"  DONE  {rel} [{', '.join(flags) or 'no-op'}]")

    build_changed = _process_build_files(state, migrated_dir)
    for b in build_changed:
        print(f"  BUILD {b} [transformed]")

    print(f"\nPhase 3 complete: {len(files_done)} file(s), {total_hdr} header(s), "
          f"{total_sess} session init(s), {total_env} sys.env rewrite(s), "
          f"{total_getenv} System.getenv rewrite(s), {total_dt} DeltaTable annotation(s), "
          f"{total_imp} unsupported import(s) removed, "
          f"{len(build_changed)} build file(s).")
    if errors:
        print(f"Errors: {len(errors)} file(s) skipped — {errors}")

    state.setdefault("phases_completed", {})["3_imports"] = {
        "status": "passed",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "files_processed": len(files_done),
        "headers_added": total_hdr,
        "session_inits_replaced": total_sess,
        "unsupported_imports_removed": total_imp,
        "build_files_transformed": build_changed,
    }
    if errors:
        state["phases_completed"]["3_imports"]["errors"] = errors
    _write_state_atomic(state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
