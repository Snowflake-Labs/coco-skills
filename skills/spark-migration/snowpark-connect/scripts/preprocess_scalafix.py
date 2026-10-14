"""Phase 0.5b: Scalafix pre-processing for Spark Scala → SCOS migration.

Applies AST-grade Scalafix rewrites to every .scala file in the manifest.

Runner resolution (first that works wins)
------------------------------------------
1. ``scalafix-cli`` / ``scalafix`` already on PATH — used directly.
2. **sbt on PATH (preferred fallback)** — sbt is the build tool virtually every
   Scala developer already has, whereas coursier / scalafix-cli usually are not.
   The pinned wrapper project under ``scripts/scalafix_sbt/`` compiles the SCOS
   rules and exports a classpath; the script then runs
   ``java -cp <classpath> scalafix.cli.Cli`` (rules baked into the classpath, so
   the conf's ``class:`` references resolve).  This is what makes the phase
   reliably *run* instead of skipping — the Scala analogue of libcst for PySpark.
3. Coursier — ``cs launch`` scalafix-cli ephemerally; the rule JAR is supplied
   with ``--tool-classpath``.  Coursier is auto-bootstrapped when absent (like
   ``uv``); opt out via ``--no-bootstrap-coursier`` / ``SCOS_BOOTSTRAP_COURSIER=0``.

The phase is best-effort and NEVER blocks the migration (always exits 0).  It is
skipped only when none of the above runners is available, or auto-launch is
disabled (``--no-auto-launch`` / ``SCOS_SCALAFIX_AUTO_LAUNCH=0``).  Disable the
sbt runner with ``--no-sbt`` / ``SCOS_SCALAFIX_USE_SBT=0``.

Pinned, verified versions: scala 2.12.20, scalafix-cli 0.14.3.

Usage
-----
    uv run --project <SKILL_DIRECTORY> \\
        python <SKILL_DIRECTORY>/scripts/preprocess_scalafix.py \\
        --state <CONVERSION>/migration_state.json

    # Optional: only for the Coursier fallback — supply a pre-built rule JAR
    # via --tool-classpath.  Not needed for the sbt runner (it compiles + bakes
    # the rules into the run classpath itself).
    uv run --project <SKILL_DIRECTORY> \\
        python <SKILL_DIRECTORY>/scripts/preprocess_scalafix.py \\
        --state <CONVERSION>/migration_state.json \\
        --scalafix-classpath /path/to/scos-rules.jar

    # Disable the preferred sbt runner (resolve via PATH then Coursier only)
    uv run --project <SKILL_DIRECTORY> \\
        python <SKILL_DIRECTORY>/scripts/preprocess_scalafix.py \\
        --state <CONVERSION>/migration_state.json \\
        --no-sbt

    # Opt out of Coursier auto-launch (phase skips if no runner is available)
    uv run --project <SKILL_DIRECTORY> \\
        python <SKILL_DIRECTORY>/scripts/preprocess_scalafix.py \\
        --state <CONVERSION>/migration_state.json \\
        --no-auto-launch
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHASE_KEY = "0_5b_scalafix"
CONF_FILENAME = "scos.scalafix.conf"
RULES_DIR = pathlib.Path(__file__).parent / "scalafix_rules"
# Pinned sbt wrapper project that compiles the rules + resolves scalafix-cli.
SBT_DIR = pathlib.Path(__file__).parent / "scalafix_sbt"
# Fully-qualified main class of the scalafix CLI (run via `java -cp <cp>`).
SCALAFIX_CLI_MAIN = "scalafix.cli.Cli"

# Default Coursier coordinate for scalafix-cli, pinned and fully-qualified.
# scalafix-cli is published with a FULL Scala-version suffix, so the explicit
# artifact name (scalafix-cli_2.12.20) is used rather than `::` binary cross.
# Override via --scalafix-coords / SCOS_SCALAFIX_COORDS.
DEFAULT_SCALAFIX_COORDS = "ch.epfl.scala:scalafix-cli_2.12.20:0.14.3"

# Map rule name → recipe_id prefix used in recipe_edits
RULE_PREFIX = "scalafix:"

# ── notebook_io (stdlib-only sibling; optional — notebooks skipped if absent) ──
_SCRIPTS_DIR = pathlib.Path(__file__).parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    import notebook_io as _nb_io  # type: ignore[import-not-found]
    _NB_IO_OK = True
except ImportError:  # pragma: no cover
    _NB_IO_OK = False

# Sentinel comments that frame cell content in synthetic per-cell wrapper files.
# Scalafix (SyntacticRule, no type resolution) operates on valid Scala AST —
# individual notebook cells are plain Scala fragments, so each is wrapped in a
# minimal ``object`` body before processing and unwrapped afterward.
_CELL_MARKER_START = "// __SCOS_CELL_START__"
_CELL_MARKER_END = "// __SCOS_CELL_END__"

# ── on-disk classpath cache ───────────────────────────────────────────────────
# The resolved sbt classpath is written here after first resolution so that
# subsequent invocations skip the 5–30s ``sbt export Compile/fullClasspath``
# subprocess entirely.  The same file is read by ``scala_ast_facts.py``.
_CP_CACHE_PATH = SBT_DIR / ".classpath_cache.json"
_CP_CACHE_SOURCES = [
    SBT_DIR / "build.sbt",
    RULES_DIR / "SCOSRules.scala",
    RULES_DIR / "ScosMigrateFacts.scala",
]

# ── state helpers ────────────────────────────────────────────────────────────


def _load_state(state_path: pathlib.Path) -> dict[str, Any]:
    with state_path.open() as fh:
        return json.load(fh)


def _save_state(state_path: pathlib.Path, state: dict[str, Any]) -> None:
    with state_path.open("w") as fh:
        json.dump(state, fh, indent=2)
    print(f"[Phase 0.5b] migration_state.json updated → {state_path}")


# ── classpath cache helpers ───────────────────────────────────────────────────


def _cp_cache_key() -> str:
    """SHA-256 over the *contents* of the build sources.

    Content-hash (not mtime/size): stable across git checkouts that reset
    mtimes, and changes iff a source's bytes actually change.
    """
    h = hashlib.sha256()
    for p in _CP_CACHE_SOURCES:
        h.update(p.name.encode("utf-8"))
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"\x00")
    return h.hexdigest()


def _load_cp_cache() -> "tuple[str, str] | None":
    """Return (classpath, java) from the on-disk cache, or None on any miss."""
    try:
        data = json.loads(_CP_CACHE_PATH.read_text(encoding="utf-8"))
        if data.get("key") != _cp_cache_key():
            return None
        cp = data.get("classpath", "")
        java = data.get("java", "")
        if not cp or not java:
            return None
        if not shutil.which(java) and not pathlib.Path(java).is_file():
            return None
        # Spot-check that the first few cached JAR paths still exist on disk.
        # Guards against a deleted/moved Coursier cache that would otherwise
        # yield a classpath hit but then fail at Scalafix invocation time.
        jar_paths = [p for p in cp.split(os.pathsep) if p.endswith(".jar")][:5]
        if any(not pathlib.Path(j).is_file() for j in jar_paths):
            return None
        return cp, java
    except Exception:  # noqa: BLE001
        return None


def _save_cp_cache(classpath: str, java: str) -> None:
    """Persist (classpath, java) to the on-disk cache.  Best-effort, never raises."""
    # Sanity guard: a real Scalafix transitive classpath has many JARs.
    # Reject suspiciously short classpaths (e.g. test fixtures) to prevent
    # corrupting the cache with fake data when tests run _save_cp_cache.
    if len(classpath.split(os.pathsep)) < 10:
        return
    try:
        _CP_CACHE_PATH.write_text(
            json.dumps(
                {"key": _cp_cache_key(), "classpath": classpath, "java": java},
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


# ── scalafix invocation ──────────────────────────────────────────────────────


def _run_rule_stdout(
    scalafix_cmd: list[str],
    rule_fqcn: str,
    scala_file: pathlib.Path,
    tool_classpath: str | None,
) -> tuple[bool, str | None]:
    """Run ONE rule on *scala_file* and return ``(ok, fixed_content)``.

    Uses ``--rules class:<FQCN> --stdout`` so scalafix prints the rewritten file
    to stdout WITHOUT touching disk (the caller writes cumulatively).  ``class:``
    references are self-contained — the compiled rule classes are already on the
    runner's classpath (sbt runner) or supplied via ``--tool-classpath`` (Coursier).

    Note: scalafix has no "print a rule-annotated diff" mode.  ``--diff`` means
    "only fix files changed in git" (requires a git repo) — NOT a diff printer —
    so it is deliberately not used here.
    """
    cmd = [*scalafix_cmd, "--rules", f"class:{rule_fqcn}", "--stdout", str(scala_file)]
    if tool_classpath:
        cmd += ["--tool-classpath", tool_classpath]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print(
            f"[Phase 0.5b] WARN: scalafix timed out on {scala_file} ({rule_fqcn})",
            file=sys.stderr,
        )
        return False, None
    except Exception as exc:  # noqa: BLE001
        print(
            f"[Phase 0.5b] WARN: scalafix error on {scala_file} ({rule_fqcn}): {exc}",
            file=sys.stderr,
        )
        return False, None

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        msg = detail[-1] if detail else f"exit {result.returncode}"
        print(
            f"[Phase 0.5b] WARN: scalafix exited {result.returncode} on "
            f"{scala_file} ({rule_fqcn}): {msg}",
            file=sys.stderr,
        )
        return False, None

    # --stdout prints the (possibly unchanged) full file content.
    return True, result.stdout


def _changed_src_lines(before: str, after: str) -> list[int]:
    """Return the 1-based line numbers in *before* that a rule rewrote.

    Computed with difflib (scalafix emits no per-edit metadata).  For pure
    insertions the anchor line is the insertion point.  Blank-only changes
    (e.g. scalafix normalising trailing newlines) are ignored so they don't
    inflate the edit count or create spurious anchors.
    """
    import difflib

    b = before.splitlines()
    a = after.splitlines()

    def _blank(seq: list[str]) -> bool:
        return all(not s.strip() for s in seq)

    changed: set[int] = set()
    sm = difflib.SequenceMatcher(a=b, b=a, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        # Skip whitespace-only churn (trailing newline normalisation, etc.).
        if _blank(b[i1:i2]) and _blank(a[j1:j2]):
            continue
        if tag in ("replace", "delete"):
            changed.update(range(i1 + 1, i2 + 1))
        elif tag == "insert":
            changed.add(min(i1 + 1, len(b)) or 1)
    return sorted(changed)


def _anchors_for_rule(short_name: str, changed_lines: list[int]) -> list[dict[str, Any]]:
    """Build recipe_edits anchors for one rule's changed source lines.

    Anchor format matches the existing contract:
      recipe_id          = "scalafix:<RuleName>"
      output_line_anchor = "scalafix:<RuleName>:<src_line>:<sha1[:8]>"
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


def _run_rule_batch(
    scalafix_cmd: list[str],
    rule_fqcn: str,
    files: list[pathlib.Path],
    tool_classpath: str | None,
) -> bool:
    """Run ONE rule over MANY files in a single scalafix invocation, in-place.

    Unlike ``_run_rule_stdout`` (single file, ``--stdout``), this rewrites the
    files on disk directly — scalafix's default when no ``--stdout``/``--test``
    is given — so all files for a rule are handled in ONE process launch instead
    of one launch per file. The caller snapshots content before/after to
    attribute per-rule edits (scalafix emits no per-edit metadata).

    Returns True iff scalafix exited 0 for the whole batch.
    """
    if not files:
        return True
    cmd = [*scalafix_cmd, "--rules", f"class:{rule_fqcn}", *[str(f) for f in files]]
    if tool_classpath:
        cmd += ["--tool-classpath", tool_classpath]
    # Scale the timeout with the batch size; generous but bounded.
    timeout = min(900, max(120, 30 * len(files)))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(
            f"[Phase 0.5b] WARN: scalafix batch timed out ({rule_fqcn}, {len(files)} file(s))",
            file=sys.stderr,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        print(
            f"[Phase 0.5b] WARN: scalafix batch error ({rule_fqcn}): {exc}",
            file=sys.stderr,
        )
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        msg = detail[-1] if detail else f"exit {result.returncode}"
        print(
            f"[Phase 0.5b] WARN: scalafix batch exited {result.returncode} "
            f"({rule_fqcn}): {msg}",
            file=sys.stderr,
        )
        return False
    return True


def _apply_rule_across_files(
    scalafix_cmd: list[str],
    fqcn: str,
    short_name: str,
    files: list[pathlib.Path],
    tool_classpath: str | None,
) -> tuple[dict[pathlib.Path, list[dict[str, Any]]], set[pathlib.Path]]:
    """Apply ONE rule to all *files*, returning per-file edits + the set of files
    the rule ran successfully on.

    Batch-first: one in-place invocation over all files; attribute each file by a
    before/after snapshot diff. On batch failure (or when scalafix can't run the
    batch), fall back to per-file ``--stdout`` so a single bad file doesn't
    suppress the rule everywhere and attribution stays precise. Each rule reads
    the current on-disk content, so cumulative ordering across rules is preserved.
    """
    edits_by_file: dict[pathlib.Path, list[dict[str, Any]]] = {}
    ran_ok: set[pathlib.Path] = set()
    if not files:
        return edits_by_file, ran_ok

    before: dict[pathlib.Path, str] = {}
    for f in files:
        try:
            before[f] = f.read_text()
        except Exception as exc:  # noqa: BLE001
            print(f"[Phase 0.5b] WARN: cannot read {f}: {exc}", file=sys.stderr)
    readable = [f for f in files if f in before]
    if not readable:
        return edits_by_file, ran_ok

    if _run_rule_batch(scalafix_cmd, fqcn, readable, tool_classpath):
        for f in readable:
            ran_ok.add(f)
            try:
                after = f.read_text()
            except Exception:  # noqa: BLE001
                continue
            if after == before[f]:
                continue  # rule made no change — idempotent
            changed = _changed_src_lines(before[f], after)
            if changed:
                edits_by_file[f] = _anchors_for_rule(short_name, changed)
        return edits_by_file, ran_ok

    # Batch failed → per-file fallback (precise attribution, isolates bad file).
    print(
        f"[Phase 0.5b]   {short_name}: batch failed, falling back to per-file",
        file=sys.stderr,
    )
    for f in readable:
        ok, fixed = _run_rule_stdout(scalafix_cmd, fqcn, f, tool_classpath)
        if not ok or fixed is None:
            continue
        ran_ok.add(f)
        if fixed == before[f]:
            continue
        changed = _changed_src_lines(before[f], fixed)
        if changed:
            edits_by_file[f] = _anchors_for_rule(short_name, changed)
        f.write_text(fixed)
    return edits_by_file, ran_ok


def _run_combined_batch(
    scalafix_cmd: list[str],
    fqcns: list[str],
    files: list[pathlib.Path],
    tool_classpath: str | None,
) -> bool:
    """Run ALL rules over ALL files in one Scalafix invocation (in-place).

    Reduces N×JVM-startup to 1×JVM-startup for the common case where all rules
    can run together.  Returns True iff scalafix exited 0.
    Falls back to the per-rule loop in the caller when this returns False.
    """
    if not files or not fqcns:
        return True
    rule_args: list[str] = []
    for fqcn in fqcns:
        rule_args += ["--rules", f"class:{fqcn}"]
    cmd = [*scalafix_cmd, *rule_args, *[str(f) for f in files]]
    if tool_classpath:
        cmd += ["--tool-classpath", tool_classpath]
    timeout = min(900, max(120, 30 * len(files)))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(
            f"[Phase 0.5b] WARN: combined batch timed out ({len(files)} file(s))",
            file=sys.stderr,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[Phase 0.5b] WARN: combined batch error: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        msg = detail[-1] if detail else f"exit {result.returncode}"
        print(
            f"[Phase 0.5b] WARN: combined batch exited {result.returncode}: {msg}",
            file=sys.stderr,
        )
        return False
    return True


def _attribute_rules_on_temp_copies(
    scalafix_cmd: list[str],
    rule_classes: list[tuple[str, str]],
    changed_files: list[pathlib.Path],
    before_all: dict[pathlib.Path, str],
    tool_classpath: str | None,
    tmpdir: pathlib.Path,
) -> dict[pathlib.Path, list[dict[str, Any]]]:
    """Attribute per-rule, per-line edits for *changed_files* using temp copies.

    For each changed file writes the *original* content to a temp copy, then runs
    each rule sequentially via ``--stdout`` (threading output so rule B sees rule
    A's result, matching the cumulative ordering of the combined batch run).
    Does NOT modify the on-disk originals — attribution only.
    """
    edits_by_file: dict[pathlib.Path, list[dict[str, Any]]] = {}
    for f in changed_files:
        original = before_all.get(f)
        if original is None:
            continue
        tmp = tmpdir / f"attr_{f.name}"
        try:
            tmp.write_text(original, encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        prev_content = original
        file_edits: list[dict[str, Any]] = []
        for fqcn, short_name in rule_classes:
            ok, fixed = _run_rule_stdout(scalafix_cmd, fqcn, tmp, tool_classpath)
            if not ok or fixed is None:
                continue
            changed = _changed_src_lines(prev_content, fixed)
            if changed:
                file_edits.extend(_anchors_for_rule(short_name, changed))
            prev_content = fixed
            try:
                tmp.write_text(fixed, encoding="utf-8")
            except Exception:  # noqa: BLE001
                break
        if file_edits:
            edits_by_file[f] = file_edits
    return edits_by_file


# ── scalafix-cli detection / auto-launch ────────────────────────────────────


def _bootstrap_coursier() -> "str | None":
    """Download and cache the Coursier launcher; return the path or None.

    Best-effort — never raises.  When ``cs``/``coursier`` is already on PATH
    it is returned immediately without any network access.  Otherwise a
    platform-appropriate ``.gz`` binary is fetched from the Coursier launchers
    repo, decompressed, cached at ``~/.cache/scos/coursier/cs``, and made
    executable.  Any failure (unsupported platform/arch, network error, etc.)
    is printed as a WARN and ``None`` is returned so the caller can skip
    gracefully.
    """
    # ── 1. Already on PATH? ──────────────────────────────────────────────────
    c = shutil.which("cs") or shutil.which("coursier")
    if c:
        return c

    try:
        # ── 2. Detect platform ───────────────────────────────────────────────
        if sys.platform.startswith("darwin"):
            plat = "darwin"
        elif sys.platform.startswith("linux"):
            plat = "linux"
        else:
            print(
                f"[Phase 0.5b] WARN: Coursier bootstrap not supported on "
                f"platform {sys.platform!r}; skipping.",
                file=sys.stderr,
            )
            return None

        # ── 3. Detect and normalise arch ─────────────────────────────────────
        machine = platform.machine().lower()
        if machine in ("arm64", "aarch64"):
            arch = "aarch64"
        elif machine in ("amd64", "x86_64"):
            arch = "x86_64"
        else:
            print(
                f"[Phase 0.5b] WARN: Coursier bootstrap unsupported arch "
                f"{machine!r}; skipping.",
                file=sys.stderr,
            )
            return None

        # ── 4. Map to asset name + URL ───────────────────────────────────────
        _ASSET_MAP = {
            ("darwin", "aarch64"): "cs-aarch64-apple-darwin.gz",
            ("darwin", "x86_64"): "cs-x86_64-apple-darwin.gz",
            ("linux", "x86_64"): "cs-x86_64-pc-linux.gz",
            ("linux", "aarch64"): "cs-aarch64-pc-linux.gz",
        }
        asset = _ASSET_MAP[(plat, arch)]
        url = f"https://github.com/coursier/launchers/raw/master/{asset}"

        # ── 5. Cache hit? ────────────────────────────────────────────────────
        cache_dir = pathlib.Path.home() / ".cache" / "scos" / "coursier"
        cs_path = cache_dir / "cs"
        if cs_path.exists() and os.access(str(cs_path), os.X_OK):
            return str(cs_path)

        # ── 6. Download + decompress + cache ─────────────────────────────────
        print(
            f"[Phase 0.5b] Bootstrapping Coursier from {url} …",
            file=sys.stderr,
        )
        response = urllib.request.urlopen(url, timeout=120)
        compressed = response.read()
        binary = gzip.decompress(compressed)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cs_path.write_bytes(binary)
        os.chmod(str(cs_path), 0o755)
        print(
            "[Phase 0.5b] NOTE: first 'cs launch' will also auto-download a "
            "JVM (one-time, cached by Coursier).",
            file=sys.stderr,
        )
        return str(cs_path)

    except Exception as exc:  # noqa: BLE001
        print(
            f"[Phase 0.5b] WARN: Coursier bootstrap failed: {exc}",
            file=sys.stderr,
        )
        return None


def _resolve_sbt_invocation() -> tuple[list[str] | None, str | None]:
    """Resolve a runnable scalafix command via the pinned sbt wrapper project.

    Returns ``(scalafix_cmd, None)`` on success or ``(None, reason)``.

    sbt is used purely as a DETERMINISTIC resolver + compiler.  We ask it to
    ``export Compile/fullClasspath`` for ``scripts/scalafix_sbt`` — which compiles
    the SCOS rules and resolves scalafix-cli — then run
    ``java -cp <classpath> scalafix.cli.Cli``.  Because the compiled rule classes
    are on that classpath, the conf's ``class:`` rule references resolve with no
    separate ``--tool-classpath``.
    """
    sbt = shutil.which("sbt")
    if sbt is None:
        return None, "sbt not on PATH"
    java = shutil.which("java")
    if java is None:
        return None, "sbt present but 'java' not on PATH"
    if not (SBT_DIR / "build.sbt").exists():
        return None, f"sbt wrapper project missing at {SBT_DIR}"

    # ── Cache hit: skip sbt export ────────────────────────────────────────────
    _cached = _load_cp_cache()
    if _cached is not None:
        _cached_cp, _cached_java = _cached
        print("[Phase 0.5b] Using cached scalafix classpath (skipping sbt export).")
        return [_cached_java, "-cp", _cached_cp, SCALAFIX_CLI_MAIN], None
    # ─────────────────────────────────────────────────────────────────────────

    export_cmd = [
        sbt,
        "--batch",
        "-Dsbt.log.noformat=true",
        "-error",
        "export Compile/fullClasspath",
    ]
    print("[Phase 0.5b] Resolving scalafix via sbt (compiling rules + resolving deps) …")
    try:
        result = subprocess.run(
            export_cmd,
            cwd=str(SBT_DIR),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        return None, "sbt export timed out (rule compile / dependency resolution)"
    except Exception as exc:  # noqa: BLE001
        return None, f"sbt export failed to start: {exc}"

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        detail = tail[-1] if tail else f"exit {result.returncode}"
        return None, f"sbt export failed: {detail}"

    # `export` prints the classpath as a single File.pathSeparator-joined line.
    classpath: str | None = None
    for line in reversed(result.stdout.splitlines()):
        candidate = line.strip()
        if "scalafix-cli" not in candidate:
            continue
        if os.pathsep in candidate or candidate.endswith(".jar"):
            classpath = candidate
            break
    if not classpath:
        return None, "sbt export produced no scalafix classpath"

    cmd = [java, "-cp", classpath, SCALAFIX_CLI_MAIN]

    # Smoke-check: confirm the CLI main class starts and reports a version.
    try:
        smoke = subprocess.run(
            [*cmd, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "scalafix.cli.Cli smoke-check timed out"
    except Exception as exc:  # noqa: BLE001
        return None, f"scalafix.cli.Cli smoke-check failed to start: {exc}"

    combined = (smoke.stdout or "") + (smoke.stderr or "")
    if smoke.returncode == 0 and re.search(r"scalafix|\d+\.\d+\.\d+", combined, re.IGNORECASE):
        ver = re.search(r"\d+\.\d+\.\d+", combined)
        vstr = f" (v{ver.group()})" if ver else ""
        print(f"[Phase 0.5b] scalafix started via sbt runner{vstr}")
        _save_cp_cache(classpath, java)  # persist for next run
        return cmd, None
    detail = combined.strip()[:200] or f"exit {smoke.returncode}"
    return None, f"scalafix.cli.Cli did not start: {detail}"


def _resolve_scalafix_invocation(
    auto_launch: bool,
    coords: str = DEFAULT_SCALAFIX_COORDS,
    bootstrap: bool = True,
    use_sbt: bool = True,
    user_tool_classpath: str | None = None,
) -> tuple[list[str] | None, str | None, str | None]:
    """Return ``(scalafix_cmd, tool_classpath, None)`` when scalafix is runnable,
    or ``(None, None, skip_reason)`` when no runner can be made available.

    ``tool_classpath`` is the rule JAR to pass via ``--tool-classpath`` (``None``
    for the sbt runner, which bakes the compiled rules into the run classpath).

    Resolution order:
    1. ``scalafix-cli`` / ``scalafix`` already on PATH → used directly.
    2. **sbt on PATH (preferred fallback)** → run via the pinned sbt wrapper.
    3. Coursier (``cs``/``coursier``, auto-bootstrapped) → ``cs launch`` scalafix-cli.
    """
    # ── 1. Already on PATH? ──────────────────────────────────────────────────
    bin_path = shutil.which("scalafix-cli") or shutil.which("scalafix")
    if bin_path:
        return [bin_path], user_tool_classpath, None

    # ── 2. Auto-launch disabled? ─────────────────────────────────────────────
    if not auto_launch:
        return None, None, "auto-launch disabled (--no-auto-launch)"

    # ── 3. sbt runner (preferred over Coursier) ──────────────────────────────
    sbt_reason: str | None = None
    if use_sbt:
        sbt_cmd, sbt_reason = _resolve_sbt_invocation()
        if sbt_cmd is not None:
            return sbt_cmd, None, None  # rules baked into the -cp; no tool-classpath
        print(f"[Phase 0.5b] sbt runner unavailable ({sbt_reason}); trying Coursier …")
    else:
        sbt_reason = "sbt runner disabled (--no-sbt)"

    # ── 4. Locate Coursier launcher ──────────────────────────────────────────
    cs = shutil.which("cs") or shutil.which("coursier")
    if cs is None and bootstrap:
        cs = _bootstrap_coursier()
    if cs is None:
        return None, None, (
            "scalafix-cli absent; "
            f"sbt unavailable ({sbt_reason}); "
            "Coursier unavailable (not on PATH and bootstrap failed/disabled)"
        )

    # ── 5. Smoke-check: confirm the coordinate resolves and starts scalafix ──
    prefix = [cs, "launch", coords, "--"]
    smoke_cmd = [cs, "launch", coords, "--", "--version"]
    try:
        result = subprocess.run(
            smoke_cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return None, None, "cs launch could not start scalafix-cli: timed out during smoke-check"
    except Exception as exc:  # noqa: BLE001
        return None, None, f"cs launch could not start scalafix-cli: {exc}"

    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0 and re.search(r"scalafix", combined, re.IGNORECASE):
        ver_match = re.search(r"\d+\.\d+\.\d+", combined)
        ver_str = f" (v{ver_match.group()})" if ver_match else ""
        print(f"[Phase 0.5b] scalafix-cli started via 'cs launch {coords}'{ver_str}")
        return prefix, user_tool_classpath, None

    detail = (result.stderr or result.stdout or "unexpected output").strip()
    return None, None, f"cs launch could not start scalafix-cli: {detail}"


# ── notebook cell processing ─────────────────────────────────────────────────


def _process_notebook_cells(
    nb_file: pathlib.Path,
    migrated_dir: pathlib.Path,
    scalafix_cmd: list[str],
    rule_classes: list[tuple[str, str]],
    tool_classpath: str | None,
    recipe_edits: dict[str, list[dict[str, Any]]],
    tmpdir: pathlib.Path,
) -> tuple[bool, int, int, bool]:
    """Apply Scalafix rules to Scala code cells in a Databricks .scala notebook.

    Each Scala code cell is extracted, wrapped in a minimal synthetic ``object``
    body (so the fragment is a valid Scala compilation unit), processed by the
    same Scalafix rules that run on plain .scala files, and the transformed
    content is written back.  Cell-level failures are non-fatal — the original
    cell content is kept.

    Returns ``(was_processed, cells_modified, new_edits_count, failed)`` where:
    - *was_processed*: notebook had at least one Scala code cell.
    - *cells_modified*: number of cells whose content changed.
    - *new_edits_count*: number of new recipe_edits entries recorded.
    - *failed*: notebook could not be parsed or written back (caller adds to failures).
    """
    try:
        rel_path = str(nb_file.relative_to(migrated_dir))
    except ValueError:
        rel_path = str(nb_file)

    try:
        nb = _nb_io.parse_notebook(nb_file)
    except Exception as exc:
        print(
            f"[Phase 0.5b] WARN: could not parse notebook {rel_path}: {exc}",
            file=sys.stderr,
        )
        return False, 0, 0, True

    def _extract_cell_src(text: str) -> str | None:
        lines = text.splitlines()
        try:
            start = next(i for i, ln in enumerate(lines) if _CELL_MARKER_START in ln)
            end = next(i for i, ln in enumerate(lines) if _CELL_MARKER_END in ln)
            return "\n".join(lines[start + 1 : end])
        except StopIteration:
            return None

    # Write one synthetic wrapper .scala file per Scala code cell
    scala_cell_indices: list[int] = []
    tmp_files: list[pathlib.Path] = []
    safe_name = re.sub(r"[^\w]", "_", rel_path)
    for idx, cell in enumerate(nb.cells):
        if getattr(cell, "cell_type", None) != "code":
            continue
        if getattr(cell, "cell_language", None) != "scala":
            continue
        src = cell.source or ""
        wrapped = (
            "object __ScosCell {\n"
            + _CELL_MARKER_START + "\n"
            + src
            + "\n" + _CELL_MARKER_END + "\n"
            + "}\n"
        )
        tmp = tmpdir / f"{safe_name}_cell{idx}.scala"
        tmp.write_text(wrapped, encoding="utf-8")
        scala_cell_indices.append(idx)
        tmp_files.append(tmp)

    if not tmp_files:
        return False, 0, 0, False  # no Scala code cells in this notebook

    # Snapshot content before any rule runs
    before_texts: dict[pathlib.Path, str] = {
        f: f.read_text(encoding="utf-8") for f in tmp_files
    }

    # Apply each rule across all temp files (rule-outer, mirrors plain .scala path)
    for fqcn, _short in rule_classes:
        _run_rule_batch(scalafix_cmd, fqcn, tmp_files, tool_classpath)

    # Extract transformed content and compute recipe_edits
    pending_edits: list[dict[str, Any]] = []
    cells_modified = 0

    for cell_idx, tmp in zip(scala_cell_indices, tmp_files):
        original_src = _extract_cell_src(before_texts[tmp])
        extracted_src = _extract_cell_src(tmp.read_text(encoding="utf-8"))
        if original_src is None or extracted_src is None:
            continue  # markers gone — skip cell

        changed_lines = _changed_src_lines(original_src, extracted_src)
        if not changed_lines:
            continue

        nb.cells[cell_idx].source = extracted_src
        cells_modified += 1
        for _fqcn, short_name in rule_classes:
            for src_line in changed_lines:
                anchor_src = f"{RULE_PREFIX}{short_name}:cell{cell_idx}:{src_line}"
                digest = hashlib.sha1(anchor_src.encode()).hexdigest()[:8]
                pending_edits.append(
                    {
                        "recipe_id": f"{RULE_PREFIX}{short_name}",
                        "src_line": src_line,
                        "output_line_anchor": (
                            f"{RULE_PREFIX}{short_name}:cell{cell_idx}:{src_line}:{digest}"
                        ),
                    }
                )

    if cells_modified == 0:
        return True, 0, 0, False  # processed but no cells changed

    # Write the notebook back with transformed cell content
    try:
        _nb_io.write_notebook(nb, nb_file)
    except Exception as exc:
        print(
            f"[Phase 0.5b] WARN: could not write notebook {rel_path}: {exc}",
            file=sys.stderr,
        )
        return True, 0, 0, True

    # Merge into recipe_edits (de-duplicate anchors from prior runs)
    existing = recipe_edits.setdefault(rel_path, [])
    existing_anchors = {e["output_line_anchor"] for e in existing}
    new_edits = [e for e in pending_edits if e["output_line_anchor"] not in existing_anchors]
    existing.extend(new_edits)
    print(
        f"[Phase 0.5b]   modified {rel_path}"
        f" (notebook, {cells_modified} cell(s), {len(new_edits)} edit(s))"
    )
    return True, cells_modified, len(new_edits), False


# ── main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 0.5: mandatory Scalafix AST pre-processing (the sole deterministic tier)."
    )
    parser.add_argument(
        "--state",
        required=True,
        metavar="STATE_JSON",
        help="Path to migration_state.json written by Phase 0.",
    )
    parser.add_argument(
        "--scalafix-classpath",
        metavar="JAR",
        default=None,
        help=(
            "Optional pre-built Scalafix rule JAR, passed via --tool-classpath "
            "(Coursier/PATH fallback only; the sbt runner compiles rules itself)."
        ),
    )
    parser.add_argument(
        "--scalafix-coords",
        metavar="COORDS",
        default=None,
        help=(
            "Coursier coordinate for scalafix-cli "
            f"(default: {DEFAULT_SCALAFIX_COORDS}). "
            "Override via SCOS_SCALAFIX_COORDS env var."
        ),
    )
    # Primary opt-out flag
    parser.add_argument(
        "--no-auto-launch",
        dest="auto_launch",
        action="store_false",
        help=(
            "Disable automatic launch of scalafix-cli via Coursier. "
            "When set, the phase is skipped if scalafix-cli is not on PATH."
        ),
    )
    # Deprecated alias — kept for back-compat; same effect as --no-auto-launch
    parser.add_argument(
        "--no-auto-install",
        dest="auto_launch",
        action="store_false",
        help=argparse.SUPPRESS,  # deprecated; use --no-auto-launch
    )
    parser.add_argument(
        "--no-bootstrap-coursier",
        dest="bootstrap",
        action="store_false",
        help=(
            "Disable automatic Coursier bootstrap when cs/coursier is absent. "
            "Opt out via SCOS_BOOTSTRAP_COURSIER=0 env var."
        ),
    )
    parser.add_argument(
        "--no-sbt",
        dest="use_sbt",
        action="store_false",
        help=(
            "Disable the preferred sbt runner.  When set, scalafix is resolved "
            "only via PATH then Coursier.  Opt out via SCOS_SCALAFIX_USE_SBT=0."
        ),
    )
    parser.set_defaults(auto_launch=True, bootstrap=True, use_sbt=True)
    args = parser.parse_args(argv)

    # Resolve coordinates: flag > env > default
    coords: str = DEFAULT_SCALAFIX_COORDS
    env_coords = os.environ.get("SCOS_SCALAFIX_COORDS", "").strip()
    if env_coords:
        coords = env_coords
    if args.scalafix_coords:
        coords = args.scalafix_coords

    # SCOS_SCALAFIX_AUTO_LAUNCH=0/false disables auto-launch (primary env var)
    # SCOS_SCALAFIX_AUTO_INSTALL=0/false deprecated alias, same effect
    auto_launch: bool = args.auto_launch
    if os.environ.get("SCOS_SCALAFIX_AUTO_LAUNCH", "").lower() in ("0", "false"):
        auto_launch = False
    if os.environ.get("SCOS_SCALAFIX_AUTO_INSTALL", "").lower() in ("0", "false"):
        auto_launch = False

    # SCOS_BOOTSTRAP_COURSIER=0/false disables Coursier bootstrap
    bootstrap: bool = args.bootstrap
    if os.environ.get("SCOS_BOOTSTRAP_COURSIER", "").lower() in ("0", "false"):
        bootstrap = False
    # Bootstrap is also disabled when auto-launch is off
    if not auto_launch:
        bootstrap = False

    # SCOS_SCALAFIX_USE_SBT=0/false disables the preferred sbt runner
    use_sbt: bool = args.use_sbt
    if os.environ.get("SCOS_SCALAFIX_USE_SBT", "").lower() in ("0", "false"):
        use_sbt = False

    state_path = pathlib.Path(args.state).expanduser().resolve()
    if not state_path.exists():
        print(
            f"[Phase 0.5b] ERROR: state file not found: {state_path}",
            file=sys.stderr,
        )
        return 1

    state = _load_state(state_path)

    # ── 1. Detect / launch scalafix (PATH → sbt → Coursier) ──────────────────
    scalafix_cmd, tool_classpath, skip_reason = _resolve_scalafix_invocation(
        auto_launch,
        coords,
        bootstrap,
        use_sbt=use_sbt,
        user_tool_classpath=args.scalafix_classpath,
    )
    if scalafix_cmd is None:
        # This is the SOLE deterministic pre-processing tier (the regex recipe
        # tier was removed), so a missing runner is a HARD failure — not a skip.
        print(
            f"[Phase 0.5] ERROR: no Scalafix runner available ({skip_reason}). "
            "AST pre-processing is mandatory for Scala migrations — install sbt + a JVM "
            "(preferred), or scalafix-cli / Coursier, then re-run. "
            "See the SKILL Phase 0.5 prerequisites.",
            file=sys.stderr,
        )
        state.setdefault("phases_completed", {})[PHASE_KEY] = {
            "status": "failed",
            "skip_reason": skip_reason,
        }
        _save_state(state_path, state)
        return 1

    if len(scalafix_cmd) == 1:
        print(f"[Phase 0.5b] scalafix-cli found: {scalafix_cmd[0]}")
    # else: launch / sbt message already printed by _resolve_scalafix_invocation

    # ── 2. Resolve conf path ─────────────────────────────────────────────────
    conf_path = RULES_DIR / CONF_FILENAME
    if not conf_path.exists():
        print(
            f"[Phase 0.5b] ERROR: config not found: {conf_path}",
            file=sys.stderr,
        )
        return 1

    # ── 3. Collect .scala files from manifest ────────────────────────────────
    migrated_dir = pathlib.Path(state.get("migrated_dir", "")).expanduser().resolve()
    manifest: list[str] = state.get("manifest", [])

    scala_files: list[pathlib.Path] = []
    notebook_scala_files: list[pathlib.Path] = []
    for rel in manifest:
        # Notebook paths from notebook_index are absolute; pathlib preserves them.
        candidate = migrated_dir / rel
        if candidate.suffix == ".scala" and candidate.exists():
            if _NB_IO_OK and _nb_io.is_notebook(candidate):
                notebook_scala_files.append(candidate)
            else:
                scala_files.append(candidate)

    if not scala_files and not notebook_scala_files:
        print("[Phase 0.5b] No .scala files or notebooks in manifest — nothing to do.")
        state.setdefault("phases_completed", {})[PHASE_KEY] = {
            "status": "skipped",
            "skip_reason": "no .scala files in manifest",
        }
        _save_state(state_path, state)
        return 0

    nb_msg = f", {len(notebook_scala_files)} Databricks notebook(s)" if notebook_scala_files else ""
    print(f"[Phase 0.5b] Processing {len(scala_files)} .scala file(s){nb_msg} …")

    # ── 4. Determine which rules will run ────────────────────────────────────
    # The conf lists rules as fully-qualified `class:` references, e.g.
    #   "class:com.snowflake.scos.scalafix.ScosCheckpointToCache"
    # Capture (fqcn, short_name) so we can run each rule individually via
    # `--rules class:<fqcn>` and attribute edits to `scalafix:<short_name>`.
    rule_classes: list[tuple[str, str]] = []
    try:
        conf_text = conf_path.read_text()
        for line in conf_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("#"):
                continue
            m = re.search(r"class:([\w.]+)", stripped)
            if m:
                fqcn = m.group(1)
                rule_classes.append((fqcn, fqcn.rsplit(".", 1)[-1]))
    except Exception:  # noqa: BLE001
        rule_classes = []
    rules_run: list[str] = [short for _fqcn, short in rule_classes]

    if not rule_classes:
        print(
            "[Phase 0.5b] ERROR: no `class:` rule references found in conf — "
            f"check {conf_path}",
            file=sys.stderr,
        )
        state.setdefault("phases_completed", {})[PHASE_KEY] = {
            "status": "failed",
            "skip_reason": f"no class: rule references in {conf_path.name}",
        }
        _save_state(state_path, state)
        return 1

    # ── 5. Process files (rule-outer: one batched scalafix run per rule) ─────
    # Each rule runs ONCE over every eligible file in a single scalafix
    # invocation, so the toolchain starts len(rules) times instead of
    # len(rules) × len(files).  Rules are applied in conf order and each reads
    # the current on-disk content, so the cumulative result per file is the same
    # as the former file-outer/rule-inner loop.
    recipe_edits: dict[str, list[dict[str, Any]]] = state.setdefault("recipe_edits", {})
    failures: list[str] = []
    files_modified = 0
    total_edits = 0

    # Idempotency: some rules (annotate / preserve-config) prepend marker
    # comments WITHOUT changing the matched expression, so they would re-fire on
    # a re-run.  Skip files already processed by this phase (tracked via their
    # recorded scalafix: edits) before any rule runs.
    eligible: list[pathlib.Path] = []
    for scala_file in scala_files:
        rel_path = str(scala_file.relative_to(migrated_dir))
        prior = recipe_edits.get(rel_path, [])
        if any(str(e.get("recipe_id", "")).startswith(RULE_PREFIX) for e in prior):
            print(f"[Phase 0.5b]   {rel_path} already processed — skipping (idempotent)")
            continue
        eligible.append(scala_file)

    # Accumulate per-file edits + per-file "did any rule run" status across rules.
    edits_accum: dict[pathlib.Path, list[dict[str, Any]]] = {f: [] for f in eligible}
    ran_ok_count: dict[pathlib.Path, int] = {f: 0 for f in eligible}

    # Snapshot all eligible files before any rule runs (needed for attribution).
    before_all: dict[pathlib.Path, str] = {}
    for f in eligible:
        try:
            before_all[f] = f.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    readable = [f for f in eligible if f in before_all]

    # ── Combined-batch path: 1 JVM launch for all N rules ────────────────────
    all_fqcns = [fqcn for fqcn, _ in rule_classes]
    combined_ok = _run_combined_batch(scalafix_cmd, all_fqcns, readable, tool_classpath)

    if combined_ok:
        # All rules ran in one JVM launch; every readable file counts as ran_ok.
        for f in readable:
            ran_ok_count[f] = len(rule_classes)
        # Attribution: for the combined batch, coarse attribution (all changed
        # lines attributed to all rules) is almost always faster than re-running
        # each rule via --stdout on changed files (M × N JVM starts).  Fine-
        # grained attribution is reserved for very small change sets where the
        # overhead is negligible and precise per-rule anchors are worth having.
        _ATTRIBUTION_THRESHOLD = 3  # files; above this, use coarse attribution
        changed_files = [
            f for f in readable
            if f in before_all and f.read_text(encoding="utf-8") != before_all[f]
        ]
        if changed_files and len(changed_files) <= _ATTRIBUTION_THRESHOLD:
            _attr_tmp = pathlib.Path(tempfile.mkdtemp(prefix="scos_attr_"))
            try:
                attr_edits = _attribute_rules_on_temp_copies(
                    scalafix_cmd, rule_classes, changed_files,
                    before_all, tool_classpath, _attr_tmp,
                )
                for f, file_edits in attr_edits.items():
                    edits_accum[f].extend(file_edits)
            finally:
                shutil.rmtree(_attr_tmp, ignore_errors=True)
        elif changed_files:
            # Coarse attribution: diff combined-batch output vs before; attribute
            # all changed lines to all rules that ran.  Correct for fixer binding
            # (recipe_id prefix is still "scalafix:") without per-file JVM overhead.
            for f in changed_files:
                after_text = f.read_text(encoding="utf-8")
                changed_lines = _changed_src_lines(before_all[f], after_text)
                if changed_lines:
                    for _fqcn, short_name in rule_classes:
                        edits_accum[f].extend(_anchors_for_rule(short_name, changed_lines))
    else:
        # Combined batch failed — fall back to the original per-rule loop.
        print(
            "[Phase 0.5b]   combined batch failed; falling back to per-rule mode",
            file=sys.stderr,
        )
        for fqcn, short_name in rule_classes:
            edits_by_file, ran_ok = _apply_rule_across_files(
                scalafix_cmd, fqcn, short_name, readable, tool_classpath
            )
            for f in ran_ok:
                ran_ok_count[f] += 1
            for f, file_edits in edits_by_file.items():
                edits_accum[f].extend(file_edits)
    # ─────────────────────────────────────────────────────────────────────────

    for scala_file in eligible:
        rel_path = str(scala_file.relative_to(migrated_dir))

        # A file counts as failed only when EVERY rule errored on it.
        if rule_classes and ran_ok_count[scala_file] == 0:
            failures.append(rel_path)
            print(f"[Phase 0.5b] WARN: skipping {rel_path} (scalafix error)", file=sys.stderr)
            continue

        edits = edits_accum[scala_file]
        if edits:
            existing = recipe_edits.setdefault(rel_path, [])
            # Merge: avoid duplicating anchors already written by a prior run.
            existing_anchors = {e["output_line_anchor"] for e in existing}
            new_edits = [e for e in edits if e["output_line_anchor"] not in existing_anchors]
            existing.extend(new_edits)
            total_edits += len(new_edits)
            files_modified += 1
            print(f"[Phase 0.5b]   modified {rel_path} ({len(new_edits)} edit(s))")

    # ── 5a. Process Databricks .scala notebook cells ──────────────────────────
    # Scalafix requires valid Scala files; notebook cells are code fragments.
    # Each Scala cell is wrapped in a synthetic ``object`` body, processed, and
    # the transformed content written back.  Uses the same ``rule_outer`` batch
    # strategy as section 5 — one Scalafix launch per rule across all cells.
    notebooks_processed = 0
    notebooks_modified = 0

    if notebook_scala_files and rule_classes and _NB_IO_OK:
        tmpdir_obj = pathlib.Path(tempfile.mkdtemp(prefix="scos_nb_phase05_"))
        try:
            for nb_file in notebook_scala_files:
                try:
                    nb_rel = str(nb_file.relative_to(migrated_dir))
                except ValueError:
                    nb_rel = str(nb_file)
                # Idempotency: skip if any scalafix: entry already recorded
                prior = recipe_edits.get(nb_rel, [])
                if any(str(e.get("recipe_id", "")).startswith(RULE_PREFIX) for e in prior):
                    print(
                        f"[Phase 0.5b]   {nb_rel} (notebook) already processed"
                        " — skipping (idempotent)"
                    )
                    continue
                was_done, cells_mod, n_edits, nb_failed = _process_notebook_cells(
                    nb_file,
                    migrated_dir,
                    scalafix_cmd,
                    rule_classes,
                    tool_classpath,
                    recipe_edits,
                    tmpdir_obj,
                )
                if nb_failed:
                    failures.append(nb_rel)
                elif was_done:
                    notebooks_processed += 1
                    if cells_mod > 0:
                        notebooks_modified += 1
                        total_edits += n_edits
                        files_modified += 1
                    else:
                        # No cells changed — record a sentinel so the idempotency
                        # check skips this notebook on every subsequent run.
                        recipe_edits.setdefault(nb_rel, []).append({
                            "recipe_id": f"{RULE_PREFIX}__no_changes__",
                            "src_line": 0,
                            "output_line_anchor": f"{RULE_PREFIX}__no_changes__:0:00000000",
                        })
        finally:
            shutil.rmtree(tmpdir_obj, ignore_errors=True)

    # ── 6. Write phase completion entry ─────────────────────────────────────
    ran_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    phase_entry: dict[str, Any] = {
        "status": "passed",
        "ran_at": ran_at,
        "files_processed": len(scala_files) + notebooks_processed,
        "files_modified": files_modified,
        "total_edits": total_edits,
        "rules_run": rules_run,
    }
    if notebooks_processed:
        phase_entry["notebooks_processed"] = notebooks_processed
        phase_entry["notebooks_modified"] = notebooks_modified
    if failures:
        phase_entry["failures"] = failures
    state.setdefault("phases_completed", {})[PHASE_KEY] = phase_entry
    _save_state(state_path, state)

    # ── 7. Print summary ─────────────────────────────────────────────────────
    print()
    print("PHASE 0.5b SUMMARY")
    print(f"  Files processed : {len(scala_files)} (.scala) + {notebooks_processed} (notebooks)")
    print(f"  Files modified  : {files_modified}")
    print(f"  Total edits     : {total_edits}")
    print(f"  Rules run       : {', '.join(rules_run) or '(none parsed from conf)'}")
    if failures:
        print(f"  Failures (skipped, migration continues): {len(failures)}")
        for f in failures:
            print(f"    - {f}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
