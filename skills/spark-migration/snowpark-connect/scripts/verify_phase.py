#!/usr/bin/env python3
"""
Deterministic phase verification for the Scala SCOS migration.

Replaces the four LLM "critic" sub-agents (analyzer-critic, fixer-critic,
import-critic, reporter-critic) with a single stdlib-only script. Every check
those critics performed was mechanical (grep / test / wc / json.load), so this
script reproduces them faithfully and deterministically — no LLM, no tokens, no
"the model misread the grep output" flakiness.

The four ``agents/*-critic.md`` files are retained as human-readable reference
for what each phase verifies; this script is the executable source of truth.

Verdicts:
    PASS            - all checks green
    PASS_WITH_GAPS  - no hard failures, but advisory gaps were found
    FAIL            - one or more hard failures

Exit codes mirror validate_migration_state.py:
    Without --strict : always 0 (advisory mode)
    With    --strict : 1 on FAIL, 0 otherwise
    2                : state file missing / invalid JSON / bad arguments

Usage:
    python3 verify_phase.py --phase 1 --language scala --strict \\
        --state /path/to/migration_state.json
    python3 verify_phase.py --phase 3 --json --state ...
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Notebook reading is shared with the analyzer / PySpark gates via notebook_io.
# Optional so verify_phase still runs in environments where it is unavailable
# (the notebook-coverage check then simply finds no notebooks).
try:
    import notebook_io  # type: ignore[import-not-found]
    _NOTEBOOK_IO = True
except ImportError:  # pragma: no cover - depends on host packaging
    notebook_io = None  # type: ignore[assignment]
    _NOTEBOOK_IO = False

# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #

# A check is OK (green), GAP (advisory — downgrades verdict to PASS_WITH_GAPS
# but never fails the gate), or FAIL (hard failure).
STATUS_OK = "OK"
STATUS_GAP = "GAP"
STATUS_FAIL = "FAIL"

_STATUS_ICON = {STATUS_OK: "[OK]  ", STATUS_GAP: "[GAP] ", STATUS_FAIL: "[FAIL]"}


@dataclass
class CheckResult:
    name: str
    status: str  # STATUS_OK | STATUS_GAP | STATUS_FAIL
    detail: str = ""


@dataclass
class PhaseReport:
    phase: int
    state_path: str
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(CheckResult(name, status, detail))

    @property
    def has_failures(self) -> bool:
        return any(c.status == STATUS_FAIL for c in self.checks)

    @property
    def has_gaps(self) -> bool:
        return any(c.status == STATUS_GAP for c in self.checks)

    @property
    def verdict(self) -> str:
        if self.has_failures:
            return "FAIL"
        if self.has_gaps:
            return "PASS_WITH_GAPS"
        return "PASS"


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

# Directories that are build output / tooling caches, never user source.
_SKIP_DIRS = {".git", "target", ".bsp", ".metals", ".idea", "project"}

# Patterns the analyzer historically misses (analyzer-critic check 3).
_BLIND_SPOT_PATTERNS: list[tuple[str, str]] = [
    ("UDF registration", r"\budf\s*\(|spark\.udf\.register|UserDefinedFunction"),
    ("checkpoint", r"\.checkpoint\s*\("),
    ("map subscript with col", r"\(col\("),
    ("Catalyst imports", r"spark\.sql\.catalyst"),
    ("Hadoop imports", r"org\.apache\.hadoop"),
    ("HWC imports", r"HiveWarehouseSession|com\.hortonworks"),
    ("Spline imports", r"za\.co\.absa\.spline"),
]

# Imports/types that must not survive into non-comment production code.
_UNSUPPORTED_IMPORT_RE = re.compile(
    r"org\.apache\.spark\.sql\.catalyst|org\.apache\.hadoop|com\.hortonworks|"
    r"za\.co\.absa\.spline|org\.apache\.spark\.sql\.hive|delta\.tables"
)

# Stale cross-file references (fixer-critic check 4).
# NOTE: ``FileSystem`` is matched with word boundaries so it catches Hadoop's
# ``org.apache.hadoop.fs.FileSystem`` / ``FileSystem.get(...)`` but NOT the
# standard, fully-supported ``java.nio.file.FileSystems`` (plural) — a frequent
# false positive otherwise.
_STALE_REF_RE = re.compile(
    r"\bFileSystem\b|hadoopConfiguration|HiveContext|enableHiveSupport|"
    r"QualifiedTableName|TableIdentifier|CatalystSqlParser|"
    r"HiveWarehouseSession|getHWCSession|getHiveSession"
)

_TEST_FILE_RE = re.compile(r"(Spec|Test|Suite)\.scala$|/src/test/|[\\/]test[\\/]")


def is_test_path(path_str: str) -> bool:
    return bool(_TEST_FILE_RE.search(path_str.replace("\\", "/")))


def load_state(state_path: Path) -> dict:
    return json.loads(state_path.read_text(encoding="utf-8"))


def _conversion_root(state: dict, state_path: Path) -> Path:
    root = state.get("conversion_root")
    return Path(root) if root else state_path.parent


def _migrated_dir(state: dict, state_path: Path) -> Path:
    md = state.get("migrated_dir")
    if md:
        return Path(md)
    return _conversion_root(state, state_path) / "Output"


def _abs_key(p, root: Path) -> str:
    """Normalize a file path to an absolute string key for matching.

    ``analysis.json`` file fields may be absolute (under ``root``) or relative
    to it; ``scala_files`` are absolute. Resolving both through this helper lets
    callers match by full path instead of the collision-prone basename.
    """
    pp = Path(p)
    if not pp.is_absolute():
        pp = root / pp
    try:
        return str(pp.resolve())
    except OSError:
        return str(pp)


# --- High-risk coverage helpers (ported 1:1 from the PySpark scos_gates.py) ---
# Verdicts in analysis.json["resolution"] that satisfy high-risk coverage WITHOUT
# an inline marker — mirrors scos_gates._RESOLVED_VERDICTS exactly.
_RESOLVED_VERDICTS = {"fixed", "safe", "todo", "perf"}


def _parse_lines(spec: object) -> "tuple[int, int] | None":
    """Parse an analysis.json ``lines`` value ("12-15" / "30-30" / 30).

    Identical semantics to scos_gates._parse_lines so the Scala and Python
    coverage gates read the analyzer's line ranges the same way.
    """
    if isinstance(spec, bool):
        return None
    if isinstance(spec, int):
        return (spec, spec)
    if not isinstance(spec, str):
        return None
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)\s*$", spec)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    if spec.strip().isdigit():
        v = int(spec.strip())
        return (v, v)
    return None


def _coerce_risk(value: object) -> float:
    """Best-effort float for an LLM-supplied ``final_risk`` (0.0 if unusable)."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _marker_near(lines: list, start: int, end: int, window: int = 3) -> bool:
    """True if a SCOS marker appears within [start-window, end+window].

    Scala analogue of scos_gates._marker_near. Recognizes both the canonical
    ``// SCOS`` marker and the legacy ``// EWI:`` form (older Scala fixer output)
    so a file annotated either way counts as covered.
    """
    lo = max(1, start - window)
    hi = min(len(lines), end + window)
    return any(("// SCOS" in lines[i - 1] or "// EWI:" in lines[i - 1])
               for i in range(lo, hi + 1))


def iter_scala_files(root: Path) -> list[Path]:
    """All .scala files under root, skipping build/tooling dirs. Sorted.

    Databricks-native ``.scala`` *notebooks* (JSON-encoded source) are excluded
    here — they are JSON, not raw Scala, and are handled by the notebook-coverage
    check instead so the line-based heuristics never run against notebook JSON.
    """
    out: list[Path] = []
    for p in root.rglob("*.scala"):
        if any(part in _SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        if _NOTEBOOK_IO and notebook_io.is_notebook(str(p)):
            continue
        out.append(p)
    return sorted(out)


def _jvm_notebook_units(root: Path, state: dict, language: str = "scala") -> list[dict]:
    """Return notebook units for *language* under ``root`` for the coverage check.

    Each unit is ``{"display": str, "source": str, "parse_error": str | None}``
    where ``source`` is the flattened cell text for *language*. Sources, in
    priority order (mirroring the PySpark gates' ``_python_notebook_paths``):
      1. ``state["notebook_index"]`` (built by ``orchestrate_phases.py``) — carries
         per-file format + language metadata.
      2. A filtered ``notebook_io.scan_notebooks`` walk of ``root``.

    Plain ``.scala``/``.java`` files (``detect_format`` → ``not_notebook``) are
    never returned, so there is no overlap with :func:`iter_scala_files` /
    :func:`iter_java_files`.
    """
    if not _NOTEBOOK_IO:
        return []

    abs_paths: list[str] = []
    seen: set[str] = set()
    nb_index = state.get("notebook_index") or {}
    if isinstance(nb_index, dict) and nb_index:
        for abs_path, info in nb_index.items():
            if not isinstance(info, dict) or info.get("language") != language:
                continue
            if info.get("format") in (None, "not_notebook"):
                continue
            p = Path(abs_path)
            if not p.is_absolute():
                p = (root / p).resolve()
            key = str(p)
            if key not in seen and p.exists():
                seen.add(key)
                abs_paths.append(str(p))
    if not abs_paths:
        for entry in notebook_io.scan_notebooks(str(root)):
            if entry.get("language") == language:
                abs_paths.append(entry["abs_path"])

    units: list[dict] = []
    for ap in sorted(abs_paths):
        display = Path(ap).name
        try:
            source = notebook_io.flatten_cells_to_script(ap, target_language=language)
            units.append({"display": display, "source": source, "parse_error": None})
        except Exception as e:  # malformed notebook JSON / unexpected structure
            units.append({"display": display, "source": "", "parse_error": str(e)})
    return units


def iter_java_files(root: Path) -> list[Path]:
    """All .java files under root, skipping build/tooling dirs. Sorted.

    True notebooks are excluded (mirroring :func:`iter_scala_files`): an exported
    Java notebook is JSON, so line-based checks would misread it, and it is
    covered by the notebook-coverage check instead. Without this the same unit
    would also be counted twice in the Phase 2 orchestration gate.
    """
    out: list[Path] = []
    for p in root.rglob("*.java"):
        if any(part in _SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        if _NOTEBOOK_IO and notebook_io.is_notebook(str(p)):
            continue
        out.append(p)
    return sorted(out)


# Java test-file detection. MUST stay in sync with update_imports_java.py's
# _TEST_FILE_RE and ScosJavaRewrite.java's isTest check: if Phase 3's updater
# treats a file as a test (and so leaves it on SparkSession) while this verifier
# counts it as production, the Phase 3 session-init check false-fails forever.
_TEST_JAVA_FILE_RE = re.compile(
    r"(Test|Tests|IT|Spec|Suite)\.java$|/src/test/|[\\/]test[\\/]"
)


def is_test_java_path(path_str: str) -> bool:
    return bool(_TEST_JAVA_FILE_RE.search(path_str.replace("\\", "/")))


def _java_brace_balance(text: str) -> str | None:
    """Best-effort brace/paren balance check for Java. Returns error or None."""
    depth_brace = 0
    depth_paren = 0
    in_line = False
    in_block = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_line:
            if c == "\n":
                in_line = False
        elif in_block:
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                in_block = False
                i += 1
        elif c == "/" and i + 1 < n:
            if text[i + 1] == "/":
                in_line = True
                i += 1
            elif text[i + 1] == "*":
                in_block = True
                i += 1
        elif c == "{":
            depth_brace += 1
        elif c == "}":
            depth_brace -= 1
            if depth_brace < 0:
                return "unbalanced closing brace"
        elif c == "(":
            depth_paren += 1
        elif c == ")":
            depth_paren -= 1
            if depth_paren < 0:
                return "unbalanced closing paren"
        i += 1
    if depth_brace != 0:
        return f"unbalanced braces (net {depth_brace:+d})"
    if depth_paren != 0:
        return f"unbalanced parens (net {depth_paren:+d})"
    return None


def _check_java_file_count(state: dict, java_files: list[Path], report: PhaseReport) -> None:
    """File-count check for Java migrations (mirrors _check_file_count for .java)."""
    base = Path(state.get("migrated_dir") or ".")

    def _counts_as_java(m: str) -> bool:
        if not m.endswith(".java"):
            return False
        parts = Path(m).parts
        if any(p in _SKIP_DIRS for p in parts[:-1]):
            return False
        return True

    manifest_java = [m for m in (str(x) for x in state.get("manifest", []))
                     if _counts_as_java(m)]
    if not manifest_java:
        report.add("file count", STATUS_OK, f"{len(java_files)} file(s); no manifest count")
        return
    if len(java_files) != len(manifest_java):
        report.add("file count", STATUS_FAIL,
                   f"{len(java_files)} on disk vs {len(manifest_java)} in manifest")
    else:
        report.add("file count", STATUS_OK, f"{len(java_files)} == manifest")





def strip_comment(line: str) -> str:
    """Return the code portion of a line with any trailing // comment removed.

    Naive (does not parse strings), matching the original critics' grep -v
    heuristic. Lines that are whole-line comments or block-comment bodies
    return an empty string.
    """
    stripped = line.lstrip()
    if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
        return ""
    idx = line.find("//")
    return line[:idx] if idx != -1 else line


def code_lines(text: str) -> list[tuple[int, str]]:
    """1-indexed (lineno, code_portion) for lines that contain active code."""
    out: list[tuple[int, str]] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        code = strip_comment(raw)
        if code.strip():
            out.append((i, code))
    return out


# --------------------------------------------------------------------------- #
# Phase 1 — Analyzer verification (ports analyzer-critic.md)
# --------------------------------------------------------------------------- #


def verify_phase_1(state: dict, state_path: Path, report: PhaseReport) -> None:
    root = _conversion_root(state, state_path)
    migrated = _migrated_dir(state, state_path)
    analysis_path = root / "analysis.json"

    # Check 1: analysis.json is valid JSON.
    if not analysis_path.exists():
        report.add("analysis.json present", STATUS_FAIL, f"not found: {analysis_path}")
        return
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        report.add("analysis.json valid JSON", STATUS_FAIL, f"invalid JSON: {e}")
        return
    report.add("analysis.json valid JSON", STATUS_OK, "")

    entries = analysis if isinstance(analysis, list) else analysis.get("issues", [])
    analyzed_files: set[str] = set()
    for e in entries:
        f = e.get("file") if isinstance(e, dict) else None
        if f:
            fp = Path(f)
            # Relative paths in analysis.json are relative to the migrated dir.
            if not fp.is_absolute():
                fp = migrated / fp
            analyzed_files.add(str(fp.resolve()))

    scala_files = iter_scala_files(migrated)

    # Check 2: file coverage — a file with Spark imports must be in analysis.
    coverage_gaps: list[str] = []
    for sf in scala_files:
        if str(sf.resolve()) in analyzed_files:
            continue
        try:
            txt = sf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "import org.apache.spark" in txt:
            coverage_gaps.append(sf.name)
    if coverage_gaps:
        report.add(
            "file coverage",
            STATUS_GAP,
            f"{len(coverage_gaps)} Spark file(s) absent from analysis: "
            + ", ".join(coverage_gaps[:5]) + ("…" if len(coverage_gaps) > 5 else ""),
        )
    else:
        report.add("file coverage", STATUS_OK, f"{len(scala_files)} file(s)")

    # Check 3: blind-spot scan — pattern present but file has no analysis entry.
    blind_hits: list[str] = []
    for sf in scala_files:
        if str(sf.resolve()) in analyzed_files:
            continue
        try:
            txt = sf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for label, pat in _BLIND_SPOT_PATTERNS:
            if re.search(pat, txt):
                blind_hits.append(f"{sf.name}:{label}")
                break
    if blind_hits:
        report.add(
            "blind-spot scan",
            STATUS_GAP,
            f"{len(blind_hits)} unanalyzed file(s) with risky patterns: "
            + ", ".join(blind_hits[:5]) + ("…" if len(blind_hits) > 5 else ""),
        )
    else:
        report.add("blind-spot scan", STATUS_OK, "")

    # Check 4: risk-distribution sanity.
    risks = [e.get("final_risk", e.get("risk", 0.0)) for e in entries if isinstance(e, dict)]
    any_spark = any(
        "import org.apache.spark" in sf.read_text(encoding="utf-8", errors="replace")
        for sf in scala_files
    )
    if not entries and any_spark:
        report.add(
            "risk distribution",
            STATUS_GAP,
            "0 issues but source contains Spark imports — possible analyzer failure",
        )
    elif risks and all(r < 0.1 for r in risks):
        has_hot = any(
            re.search(r"sparkContext|\.rdd\b|HiveWarehouseSession|spark\.sql\.catalyst",
                      sf.read_text(encoding="utf-8", errors="replace"))
            for sf in scala_files
        )
        if has_hot:
            report.add(
                "risk distribution",
                STATUS_GAP,
                "all issues <0.1 risk but hot patterns present — possible false negatives",
            )
        else:
            report.add("risk distribution", STATUS_OK, "")
    else:
        report.add("risk distribution", STATUS_OK, f"{len(entries)} issue(s)")


# --------------------------------------------------------------------------- #
# Phase 2 — Fixer verification (ports fixer-critic.md, minus compile)
# --------------------------------------------------------------------------- #

_IMPORT_ARTIFACT_RE = re.compile(r"^\s*import .*[—–]|^\s*import .* removed")
_BARE_EMDASH_RE = re.compile(r"^\s*[—–]\s*$")
_NOOP_RE = re.compile(r"\.hint\s*\(|\.repartition\s*\(|\.coalesce\s*\(")
_SCOS_MARKER = "// SCOS:"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def verify_phase_2(state: dict, state_path: Path, report: PhaseReport) -> None:
    root = _conversion_root(state, state_path)
    migrated = _migrated_dir(state, state_path)
    scala_files = iter_scala_files(migrated)
    notebooks = _jvm_notebook_units(migrated, state, "scala")

    # Check 0: orchestration enforcement. For any multi-file workload the
    # coordinator MUST route Phase 2 through orchestrate_phases.py, which writes
    # the worker-pool plan (`max_parallel_fixers` + `phase2_chunks`) to state.
    # If those are absent the coordinator improvised an inline single-agent fix
    # and bypassed the parallel fixer pool — fail so it re-runs the orchestrator.
    code_units = len(scala_files) + len(notebooks)
    if code_units >= 2 and (
        "max_parallel_fixers" not in state or "phase2_chunks" not in state
    ):
        report.add(
            "phase 2 orchestration", STATUS_FAIL,
            f"multi-file workload ({code_units} files) but migration_state.json "
            "has no orchestrator plan (max_parallel_fixers / phase2_chunks) — run "
            "scripts/orchestrate_phases.py --phase 2 and dispatch the printed "
            "waves instead of fixing inline",
        )
    else:
        report.add("phase 2 orchestration", STATUS_OK, "")

    # Check 1: syntax artifacts (Rules 21/22). Compilation is owned by Phase 2b.
    artifacts: list[str] = []
    for sf in scala_files:
        for i, raw in enumerate(_read(sf).splitlines(), start=1):
            if _IMPORT_ARTIFACT_RE.search(raw) or _BARE_EMDASH_RE.match(raw):
                artifacts.append(f"{sf.name}:{i}")
    if artifacts:
        report.add("syntax artifacts", STATUS_FAIL,
                   "malformed lines: " + ", ".join(artifacts[:8]))
    else:
        report.add("syntax artifacts", STATUS_OK, "")

    # Check 2: high-risk coverage — ported 1:1 from the PySpark scos_gates.py
    # high-risk gate. A high-risk (final_risk >= 0.7) issue is "covered" when a
    # SCOS marker sits within +/-3 lines of its analyzer line range, OR the issue
    # carries a recognized analysis.json ``resolution`` verdict (fixed/safe/todo/
    # perf). ``resolution: "safe"`` additionally requires a ``resolution_reason``.
    # Per-issue marker-near (not whole-file presence) avoids both the duplicate-
    # basename mis-attribution and the "marker anywhere in the file" looseness.
    analysis_path = root / "analysis.json"
    entries: list = []
    if analysis_path.exists():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            entries = analysis if isinstance(analysis, list) else analysis.get("issues", [])
        except json.JSONDecodeError:
            entries = []

    # Per-file line lists keyed by absolute path (NOT basename — repos often have
    # many same-named files, e.g. several Image.scala). Notebooks are handled in
    # the notebook-coverage check below.
    lines_by_key: dict[str, tuple[str, list]] = {}
    by_abs: dict[str, Path] = {}
    for sf in scala_files:
        try:
            disp = str(sf.relative_to(migrated))
        except ValueError:
            disp = sf.name
        key = _abs_key(sf, migrated)
        lines_by_key[key] = (disp, _read(sf).splitlines())
        by_abs[key] = sf

    n_high_risk = 0
    uncovered: list[str] = []
    safe_no_reason: list[str] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        rng = _parse_lines(e.get("lines"))
        f = e.get("file")
        if rng is None or not isinstance(f, str):
            continue
        unit = lines_by_key.get(_abs_key(f, migrated))
        if unit is None:
            continue  # notebook or absent file -> handled elsewhere
        disp, flines = unit
        if _coerce_risk(e.get("final_risk", e.get("risk"))) < 0.7:
            continue
        n_high_risk += 1
        resolution = (e.get("resolution") or "").strip().lower()
        reason = (e.get("resolution_reason") or "").strip()
        if resolution == "safe" and not reason:
            safe_no_reason.append(f"{disp}:{rng[0]}")
        elif not _marker_near(flines, rng[0], rng[1]) and resolution not in _RESOLVED_VERDICTS:
            uncovered.append(f"{disp}:{rng[0]}")
    if safe_no_reason or uncovered:
        msgs = []
        if uncovered:
            msgs.append("high-risk issue(s) with no nearby // SCOS marker or "
                        "analysis resolution: " + ", ".join(uncovered[:8]))
        if safe_no_reason:
            msgs.append("resolution='safe' without resolution_reason: "
                        + ", ".join(safe_no_reason[:8]))
        report.add("high-risk coverage", STATUS_FAIL, "; ".join(msgs))
    elif n_high_risk:
        report.add("high-risk coverage", STATUS_OK,
                   f"{n_high_risk} high-risk issue(s) covered (marker-near or resolution)")
    else:
        report.add("high-risk coverage", STATUS_OK, "no high-risk issues")

    # Check 3: no-op over-annotation. .hint/.repartition/.coalesce should NOT be
    # annotated, EXCEPT when on a .rdd. chain (those are real problems).
    # Skip pure comment lines: Scalafix ScosPartitionNoopStrip emits comments that
    # *explain* removed .repartition()/.coalesce() calls — those explanatory comment
    # lines contain the noop pattern in the text but are not actual code annotations.
    over_annotated: list[str] = []
    for sf in scala_files:
        for i, raw in enumerate(_read(sf).splitlines(), start=1):
            if raw.lstrip().startswith("//"):
                continue  # pure comment line — noop pattern here is explanatory, not code
            if _SCOS_MARKER in raw and _NOOP_RE.search(raw) and ".rdd" not in raw:
                over_annotated.append(f"{sf.name}:{i}")
    if over_annotated:
        report.add("no-op over-annotation", STATUS_FAIL,
                   "annotated no-ops: " + ", ".join(over_annotated[:8]))
    else:
        report.add("no-op over-annotation", STATUS_OK, "")

    # Check 4: stale cross-file references in non-comment code.
    stale: list[str] = []
    for sf in scala_files:
        raw_lines = _read(sf).splitlines()
        for lineno, code in code_lines(_read(sf)):
            if _STALE_REF_RE.search(code):
                # Skip references the fixer already annotated as known-unsupported
                # (an // EWI: or // SCOS: marker on this or the preceding raw line).
                # Those are deliberate manual-refactor items, not stale leftovers.
                ctx = raw_lines[max(0, lineno - 2):lineno]
                if any(("// EWI:" in r or "// SCOS:" in r) for r in ctx):
                    continue
                stale.append(f"{sf.name}:{lineno}")
    if stale:
        report.add("cross-file consistency", STATUS_FAIL,
                   "stale refs in code: " + ", ".join(stale[:8]))
    else:
        report.add("cross-file consistency", STATUS_OK, "")

    # Check 5/6: file count + no empty files.
    _check_file_count(state, scala_files, report)
    empties = [sf.name for sf in scala_files if sf.stat().st_size == 0]
    if empties:
        report.add("no empty files", STATUS_FAIL, "0-byte file(s): " + ", ".join(empties[:8]))
    else:
        report.add("no empty files", STATUS_OK, "")

    # Check 7: the fixer MUST NOT delete Phase 0.5 recipe-managed regions.
    # Materialization of preserved config happens in Phase 3 (session rebuild),
    # so Phase 2 only asserts the recipe MARKERS *survived* the LLM fixer — i.e.
    # the fixer did not collapse a builder and silently drop the preserved-config
    # intent. (Phase 3's _verify_preserved_config checks actual materialization;
    # running that here would false-fail on the not-yet-materialized
    # INSERT-AFTER-BUILDER hint, which is legitimately resolved in Phase 3.)
    recipe_edits = state.get("recipe_edits", {}) or {}
    # Emitted by the Scalafix AST rule ScosBuilderPreserveConfig (Phase 0.5 — the
    # sole deterministic pre-processing tier; the regex recipe tier was removed).
    preserve_ids = {
        "scalafix:ScosBuilderPreserveConfig",
    }
    dropped: list[str] = []
    for rel, edits in recipe_edits.items():
        if not any((e or {}).get("recipe_id") in preserve_ids for e in (edits or [])):
            continue
        sf = by_abs.get(_abs_key(rel, migrated))
        if sf is None:
            continue
        txt = _read(sf)
        if ("SCOS-RECIPE-PRESERVED-CONFIG" not in txt
                and "SCOS-RECIPE-INSERT-AFTER-BUILDER" not in txt):
            dropped.append(Path(rel).name)
    if dropped:
        report.add("preserved-config markers survived", STATUS_FAIL,
                   "fixer dropped recipe preserve-config markers in: "
                   + ", ".join(dropped[:8]))
    else:
        report.add("preserved-config markers survived", STATUS_OK, "")

    # Check 8: notebook coverage. Scala notebooks (.ipynb / Databricks-native /
    # exported) are not *.scala files, so the line-based checks above skip them
    # entirely. Run the equivalent validity + artifact + marker checks against
    # the flattened Scala cell source so notebooks are not a blind spot.
    if notebooks:
        bad_parse = [n["display"] for n in notebooks if n["parse_error"]]
        if bad_parse:
            report.add("notebook validity", STATUS_FAIL,
                       "unparseable notebook(s): " + ", ".join(bad_parse[:8]))
        else:
            report.add("notebook validity", STATUS_OK,
                       f"{len(notebooks)} scala notebook(s) parsed")

        nb_artifacts: list[str] = []
        for n in notebooks:
            for i, raw in enumerate(n["source"].splitlines(), start=1):
                if _IMPORT_ARTIFACT_RE.search(raw) or _BARE_EMDASH_RE.match(raw):
                    nb_artifacts.append(f"{n['display']}:{i}")
        if nb_artifacts:
            report.add("notebook syntax artifacts", STATUS_FAIL,
                       "malformed lines: " + ", ".join(nb_artifacts[:8]))
        else:
            report.add("notebook syntax artifacts", STATUS_OK, "")

        # High-risk coverage for notebooks — same PySpark mechanism as Check 2,
        # applied to each notebook's flattened Scala source: a high-risk issue is
        # covered by a SCOS marker within +/-3 lines of its analyzer line range, or
        # by a recognized analysis.json ``resolution`` verdict. Notebooks are matched
        # to analyzer entries by basename (their flattened-source line numbers are
        # what the analyzer recorded against the notebook file).
        nb_by_name = {n["display"]: n for n in notebooks}
        nb_uncovered: list[str] = []
        nb_safe_no_reason: list[str] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            rng = _parse_lines(e.get("lines"))
            f = e.get("file")
            if rng is None or not isinstance(f, str):
                continue
            n = nb_by_name.get(Path(f).name)
            if n is None or _coerce_risk(e.get("final_risk", e.get("risk"))) < 0.7:
                continue
            flines = n["source"].splitlines()
            resolution = (e.get("resolution") or "").strip().lower()
            reason = (e.get("resolution_reason") or "").strip()
            if resolution == "safe" and not reason:
                nb_safe_no_reason.append(f"{n['display']}:{rng[0]}")
            elif not _marker_near(flines, rng[0], rng[1]) and resolution not in _RESOLVED_VERDICTS:
                nb_uncovered.append(f"{n['display']}:{rng[0]}")
        if nb_uncovered or nb_safe_no_reason:
            parts = []
            if nb_uncovered:
                parts.append("notebook high-risk issue(s) with no nearby // SCOS "
                             "marker or resolution: " + ", ".join(nb_uncovered[:8]))
            if nb_safe_no_reason:
                parts.append("resolution='safe' without resolution_reason: "
                             + ", ".join(nb_safe_no_reason[:8]))
            report.add("notebook high-risk coverage", STATUS_FAIL, "; ".join(parts))
        else:
            report.add("notebook high-risk coverage", STATUS_OK, "")


# --------------------------------------------------------------------------- #
# Phase 3 — Import/session/build/header verification (ports import-critic.md)
# --------------------------------------------------------------------------- #

_HIVE_MASTER_RE = re.compile(r'enableHiveSupport|\.master\("yarn"\)|\.master\("local')
_SPARK_REMOTE_RE = re.compile(r'SparkSession\.builder.*\.remote|\.remote\("sc://')
_PRESERVED_CFG_RE = re.compile(r"SCOS-RECIPE-PRESERVED-CONFIG:\s*(\S+?)=(.*)$")


def _unquote_marker_token(tok: str) -> str:
    """Normalize a PRESERVED-CONFIG marker token to its bare inner value.

    The canonical marker carries bare values (``spark.x=200``), but be tolerant
    of a quoted form (``"spark.x"="200"``) so a stray quoted marker — e.g. from
    an older Scalafix run that emitted ``.syntax`` — still matches the
    materialized ``.config("spark.x", "200")`` instead of silently failing the
    Phase 3 gate. Strips one balanced pair of surrounding single/double quotes.
    """
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ("\"", "'"):
        return tok[1:-1]
    return tok


# Anchor to an ACTIVE marker line (a // comment whose first token is the marker),
# so descriptive mentions in header/changelog block comments
# (e.g. " * - Materialized SCOS-RECIPE-INSERT-AFTER-BUILDER: ...") do not
# false-trigger the stale-marker check.
_INSERT_AFTER_RE = re.compile(r"^\s*//\s*SCOS-RECIPE-INSERT-AFTER-BUILDER:")
# Unresolved dependency-version placeholders that would break the build (F13).
# Targets ONLY broken forms — never a bare Maven <version>X.Y.Z</version> tag:
#   <latest>            angle-bracket placeholder (sbt/Maven)
#   PIN_CONCRETE_VERSION  Maven fallback sentinel
#   :0.x.y                Gradle coordinate placeholder
#   % "<...>"             sbt version string that is a placeholder
#   :<name>               Gradle coordinate ending in an angle-bracket version
_PLACEHOLDER_VER_RE = re.compile(
    r'<latest>|PIN_CONCRETE_VERSION|:0\.x\.y\b|%\s*"<[^"]*>"|:<[A-Za-z0-9._-]+>'
)


def verify_phase_3(state: dict, state_path: Path, report: PhaseReport) -> None:
    migrated = _migrated_dir(state, state_path)
    scala_files = iter_scala_files(migrated)
    prod_files = [sf for sf in scala_files
                  if not is_test_path(str(sf.relative_to(migrated)))]

    # Check 1: migration header on every file.
    missing_header = [sf.name for sf in scala_files
                      if "SCOS Migration" not in "\n".join(_read(sf).splitlines()[:10])]
    if missing_header:
        report.add("migration header", STATUS_FAIL,
                   "missing header: " + ", ".join(missing_header[:8]))
    else:
        report.add("migration header", STATUS_OK, f"{len(scala_files)} file(s)")

    # Check 2: no enableHiveSupport / yarn|local master in non-comment prod code.
    hive_hits: list[str] = []
    for sf in prod_files:
        for lineno, code in code_lines(_read(sf)):
            if _HIVE_MASTER_RE.search(code):
                hive_hits.append(f"{sf.name}:{lineno}")
    if hive_hits:
        report.add("session init replaced", STATUS_FAIL,
                   "Hive/master refs in prod: " + ", ".join(hive_hits[:8]))
    else:
        report.add("session init replaced", STATUS_OK, "")

    # Check 3: SnowparkConnectSession present; no SparkSession...remote in prod.
    has_scos = any("SnowparkConnectSession" in _read(sf) for sf in prod_files)
    if not has_scos:
        report.add("SnowparkConnectSession init", STATUS_FAIL,
                   "no non-test file initializes SnowparkConnectSession")
    else:
        report.add("SnowparkConnectSession init", STATUS_OK, "")
    remote_hits: list[str] = []
    for sf in prod_files:
        for lineno, code in code_lines(_read(sf)):
            if _SPARK_REMOTE_RE.search(code):
                remote_hits.append(f"{sf.name}:{lineno}")
    if remote_hits:
        report.add("no SparkSession.remote", STATUS_FAIL,
                   "vanilla remote() in prod: " + ", ".join(remote_hits[:8]))
    else:
        report.add("no SparkSession.remote", STATUS_OK, "")

    # Check 4: no unsupported imports in non-comment code.
    # Only flag actual `import` declarations — config string values that contain
    # Hadoop/Catalyst package names (e.g. .config("spark.jars.packages",
    # "org.apache.hadoop:hadoop-aws:...")) are not import statements and must not
    # be flagged here. The regex is still used as the allowlist; we restrict the
    # match to lines whose code portion starts with "import ".
    unsupported: list[str] = []
    for sf in scala_files:
        for lineno, code in code_lines(_read(sf)):
            if code.strip().startswith("import ") and _UNSUPPORTED_IMPORT_RE.search(code):
                unsupported.append(f"{sf.name}:{lineno}")
    if unsupported:
        report.add("no unsupported imports", STATUS_FAIL,
                   "unsupported import(s): " + ", ".join(unsupported[:8]))
    else:
        report.add("no unsupported imports", STATUS_OK, "")

    # Check 5: build-file verification.
    _verify_build_files(migrated, state, report)

    # Check 6: syntax artifacts (same as Phase 2 check 1).
    artifacts: list[str] = []
    for sf in scala_files:
        for i, raw in enumerate(_read(sf).splitlines(), start=1):
            if _IMPORT_ARTIFACT_RE.search(raw) or _BARE_EMDASH_RE.match(raw):
                artifacts.append(f"{sf.name}:{i}")
    report.add("syntax artifacts", STATUS_FAIL if artifacts else STATUS_OK,
               ("malformed: " + ", ".join(artifacts[:8])) if artifacts else "")

    # Check 7: file count.
    _check_file_count(state, scala_files, report)

    # Check 8: preserved-config materialization.
    _verify_preserved_config(scala_files, report)

    # Check 9: no surviving sys.env / System.getenv calls in migrated files.
    # Phase 3 (update_imports_scala.py) should have rewritten all of these to
    # System.getProperty.  Surviving calls receive null from the harness because
    # EnvUtil.setEnv only writes via System.setProperty + override map — it cannot
    # mutate the OS process environment that System.getenv/sys.env read from.
    # Note: sys.env is also used legitimately for SCOS_FLAVOR (set on sbt command line),
    # so this is advisory (GAP) rather than a hard FAIL.
    _SYS_ENV_RESIDUAL_RE = re.compile(r'\bsys\.env\s*[\.\(]|\bSystem\.getenv\s*\(')
    residual: list[str] = []
    for sf in prod_files:
        text = _read(sf)
        for lineno, code in code_lines(text):
            if _SYS_ENV_RESIDUAL_RE.search(code):
                residual.append(f"{sf.name}:{lineno}")
    if residual:
        report.add("sys_env_residual", STATUS_GAP,
                   f"{len(residual)} sys.env/System.getenv call(s) survived Phase 3 — "
                   f"harness injection (EnvUtil.setEnv) cannot reach these: "
                   + ", ".join(residual[:6]))
    else:
        report.add("sys_env_residual", STATUS_OK, "")


def _verify_build_files(migrated: Path, state: dict, report: PhaseReport) -> None:
    fails: list[str] = []
    checked = 0
    root_names = ("pom.xml", "build.sbt", "build.gradle", "build.gradle.kts")

    # --- Root build files: full checks (positive + negative). ---
    for name in ("pom.xml", "build.sbt"):
        bf = migrated / name
        if not bf.exists():
            continue
        checked += 1
        txt = _read(bf)
        if "snowpark-connect-java-client" not in txt:
            fails.append(f"{name}: missing snowpark-connect-java-client")
        if "spark-connect-client-jvm" in txt:
            fails.append(f"{name}: OSS spark-connect-client-jvm present")
        if name == "pom.xml" and "2.11" in txt:
            fails.append(f"{name}: still references 2.11")
        if _PLACEHOLDER_VER_RE.search(txt):
            fails.append(f"{name}: unresolved version placeholder — pin a concrete version")
    for name in ("build.gradle", "build.gradle.kts"):
        bf = migrated / name
        if not bf.exists():
            continue
        checked += 1
        txt = _read(bf)
        if "snowpark-connect-java-client" not in txt:
            fails.append(f"{name}: missing snowpark-connect-java-client")
        if "spark-connect-client-jvm" in txt:
            fails.append(f"{name}: OSS spark-connect-client-jvm present")
        if "add-opens" not in txt:
            fails.append(f"{name}: missing --add-opens jvmArgs")
        if "spark-hive" in txt:
            fails.append(f"{name}: spark-hive still present")
        if _PLACEHOLDER_VER_RE.search(txt):
            fails.append(f"{name}: unresolved version placeholder — pin a concrete version")

    # --- Nested build files (from the Phase 0 manifest): NEGATIVE checks only. ---
    # Submodules legitimately may not declare the SCOS client (inherited from a
    # parent / no Spark usage), so we do NOT require its presence here — we only
    # flag stale or forbidden content that must never survive in any module.
    try:
        root_set = {(migrated / n).resolve() for n in root_names}
    except OSError:
        root_set = set()
    for rel in (state.get("build_files", []) or []):
        bf = migrated / rel
        try:
            rbf = bf.resolve()
        except OSError:
            continue
        if not rbf.is_file() or rbf in root_set:
            continue  # missing, or already covered by the root pass
        checked += 1
        txt = _read(bf)
        disp = str(rel)
        if "spark-connect-client-jvm" in txt:
            fails.append(f"{disp}: OSS spark-connect-client-jvm present")
        if "spark-hive" in txt:
            fails.append(f"{disp}: spark-hive still present")
        if bf.name == "pom.xml" and "2.11" in txt:
            fails.append(f"{disp}: still references 2.11")
        if _PLACEHOLDER_VER_RE.search(txt):
            fails.append(f"{disp}: unresolved version placeholder — pin a concrete version")

    if not checked:
        report.add("build files", STATUS_OK, "no root build file present")
    elif fails:
        report.add("build files", STATUS_FAIL, "; ".join(fails[:8]))
    else:
        report.add("build files", STATUS_OK, f"{checked} build file(s) transformed")


def _verify_preserved_config(scala_files: list[Path], report: PhaseReport) -> None:
    pairs: set[tuple[str, str]] = set()
    stale: list[str] = []
    materialized_blob_parts: list[str] = []
    for sf in scala_files:
        txt = _read(sf)
        materialized_blob_parts.append(txt)
        for raw in txt.splitlines():
            m = _PRESERVED_CFG_RE.search(raw)
            if m:
                pairs.add((_unquote_marker_token(m.group(1)),
                           _unquote_marker_token(m.group(2))))
            if _INSERT_AFTER_RE.search(raw):
                stale.append(sf.name)
    blob = "\n".join(materialized_blob_parts)
    unmaterialized: list[str] = []
    for k, v in pairs:
        cfg = re.escape(k)
        val = re.escape(v)
        if not re.search(rf'\.config\(\s*"{cfg}"\s*,\s*"{val}"\s*\)', blob) and \
           not re.search(rf'\.conf\.set\(\s*"{cfg}"\s*,\s*"{val}"\s*\)', blob):
            unmaterialized.append(f"{k}={v}")
    problems: list[str] = []
    if unmaterialized:
        problems.append("unmaterialized: " + ", ".join(unmaterialized[:5]))
    if stale:
        problems.append("stale INSERT-AFTER-BUILDER in: " + ", ".join(sorted(set(stale))[:5]))
    if problems:
        report.add("preserved-config", STATUS_FAIL, "; ".join(problems))
    elif pairs:
        report.add("preserved-config", STATUS_OK, f"{len(pairs)} config(s) materialized")
    else:
        report.add("preserved-config", STATUS_OK, "no preserved-config markers")


# --------------------------------------------------------------------------- #
# Phase 4 — Report verification (ports reporter-critic.md)
# --------------------------------------------------------------------------- #


def verify_phase_4(state: dict, state_path: Path, report: PhaseReport) -> None:
    root = _conversion_root(state, state_path)
    reports = root / "Reports"
    required = ["Issues.csv", "InputFilesInventory.csv", "ArtifactDependencyInventory.csv"]

    missing = [n for n in required if not (reports / n).exists()]
    if missing:
        report.add("required CSVs", STATUS_FAIL, "missing: " + ", ".join(missing))
        return
    report.add("required CSVs", STATUS_OK, "all 3 present")

    issues = reports / "Issues.csv"
    issues_lines = _read(issues).splitlines()
    has_data = len(issues_lines) > 1
    if has_data:
        report.add("Issues.csv", STATUS_OK, f"{len(issues_lines) - 1} issue row(s)")
    else:
        # A clean migration legitimately produces zero issues — a header-only
        # Issues.csv is valid, not a failure.
        report.add("Issues.csv", STATUS_OK, "0 issues (clean migration)")

    inv_text = _read(reports / "InputFilesInventory.csv")
    inv_lines = inv_text.splitlines()
    inv_rows = max(0, len(inv_lines) - 1)
    if inv_rows == 0:
        report.add("InputFilesInventory rows", STATUS_FAIL, "0 data rows")
    else:
        manifest_scala = sum(1 for m in state.get("manifest", []) if str(m).endswith(".scala"))
        # Compare the manifest against CODE rows only (code-vs-data split): data /
        # resource files are inventoried but marked Ignored, and must not inflate
        # the comparison. Count .scala-extension rows when the Extension column is
        # present; fall back to total rows for legacy inventories without it.
        scala_rows: int | None = None
        try:
            reader = csv.DictReader(io.StringIO(inv_text))
            if reader.fieldnames and "Extension" in reader.fieldnames:
                scala_rows = sum(
                    1 for r in reader if (r.get("Extension") or "").strip().lower() == ".scala"
                )
        except (csv.Error, ValueError):
            scala_rows = None
        compare_rows = scala_rows if scala_rows is not None else inv_rows
        label = "scala code row(s)" if scala_rows is not None else "row(s)"
        detail = f"{inv_rows} total row(s); {compare_rows} {label}"
        if manifest_scala and abs(compare_rows - manifest_scala) > max(2, manifest_scala // 2):
            report.add("InputFilesInventory rows", STATUS_GAP,
                       f"{compare_rows} {label} vs ~{manifest_scala} manifest .scala")
        else:
            report.add("InputFilesInventory rows", STATUS_OK, detail)

    header = issues_lines[0] if issues_lines else ""
    expected_cols = ("EWI", "File", "Line", "Description")
    if not any(c.lower() in header.lower() for c in expected_cols):
        report.add("Issues.csv structure", STATUS_GAP,
                   f"unexpected header: {header[:80]}")
    else:
        report.add("Issues.csv structure", STATUS_OK, "")

    blob = _read(issues)
    if not has_data:
        # No issue rows → nothing to classify; a clean migration is valid.
        report.add("EWI code prefix", STATUS_OK, "no issues to classify")
    elif "SPRKCNTSCL" not in blob:
        if "SPRKCNTPY" in blob:
            report.add("EWI code prefix", STATUS_FAIL,
                       "only SPRKCNTPY codes — wrong language prefix")
        else:
            report.add("EWI code prefix", STATUS_FAIL,
                       "issue rows present but no SPRKCNTSCL codes found")
    else:
        report.add("EWI code prefix", STATUS_OK, "")


# --------------------------------------------------------------------------- #
# Shared check
# --------------------------------------------------------------------------- #


def _check_file_count(state: dict, scala_files: list[Path], report: PhaseReport) -> None:
    # Count manifest .scala entries the SAME way iter_scala_files counts on disk:
    # exclude Databricks ``.scala`` notebooks (JSON, validated separately) and
    # entries under build/tooling skip-dirs (e.g. sbt ``project/``). Without this,
    # any workload with .scala notebooks or sbt project/*.scala files reports a
    # perpetual false mismatch (disk count < manifest count).
    base = Path(state.get("migrated_dir") or ".")

    # Notebook paths from the index, in every form they might appear (absolute,
    # resolved-absolute, and rel_path). Used to exclude notebooks from BOTH the
    # manifest count and the on-disk count — the latter matters because once a
    # SCOS header is prepended the ``// Databricks notebook source`` first-line
    # marker is gone, so iter_scala_files can no longer detect them as notebooks.
    nb_paths: set[str] = set()
    nb_abs: set[str] = set()
    nb_index = state.get("notebook_index")
    if isinstance(nb_index, dict):
        for k, info in nb_index.items():
            nb_paths.add(str(k))
            nb_abs.add(_abs_key(k, base))
            if isinstance(info, dict) and info.get("rel_path"):
                nb_paths.add(str(info["rel_path"]))
                nb_abs.add(_abs_key(info["rel_path"], base))

    def _counts_as_source(m: str) -> bool:
        if not m.endswith(".scala"):
            return False
        if m in nb_paths or _abs_key(m, base) in nb_abs:   # Databricks .scala notebook
            return False
        parts = Path(m).parts
        if any(p in _SKIP_DIRS for p in parts[:-1]):   # project/, target/, ...
            return False
        return True

    manifest_scala = [m for m in (str(x) for x in state.get("manifest", []))
                      if _counts_as_source(m)]
    # On-disk side: drop any file that is a known notebook (marker may have been
    # mangled by a prepended header) so both sides exclude notebooks consistently.
    disk_scala = [sf for sf in scala_files if _abs_key(sf, base) not in nb_abs]
    if not manifest_scala:
        report.add("file count", STATUS_OK, f"{len(disk_scala)} file(s); no manifest count")
        return
    if len(disk_scala) != len(manifest_scala):
        report.add("file count", STATUS_FAIL,
                   f"{len(disk_scala)} on disk vs {len(manifest_scala)} in manifest")
    else:
        report.add("file count", STATUS_OK, f"{len(disk_scala)} == manifest")


# --------------------------------------------------------------------------- #
# Java phase verifications (mirror Scala functions with .java files)
# --------------------------------------------------------------------------- #

# Java comment syntax is the same as Scala: // and /* */
# SCOS marker is the same: // SCOS:
# Import pattern is the same: import org.apache.spark


def verify_phase_1_java(state: dict, state_path: Path, report: PhaseReport) -> None:
    """Phase 1 verification for Java workloads — mirrors verify_phase_1."""
    root = _conversion_root(state, state_path)
    migrated = _migrated_dir(state, state_path)
    analysis_path = root / "analysis.json"

    if not analysis_path.exists():
        report.add("analysis.json present", STATUS_FAIL, f"not found: {analysis_path}")
        return
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        report.add("analysis.json valid JSON", STATUS_FAIL, f"invalid JSON: {e}")
        return
    report.add("analysis.json valid JSON", STATUS_OK, "")

    entries = analysis if isinstance(analysis, list) else analysis.get("issues", [])
    analyzed_files: set[str] = set()
    for e in entries:
        f = e.get("file") if isinstance(e, dict) else None
        if f:
            fp = Path(f)
            if not fp.is_absolute():
                fp = migrated / fp
            analyzed_files.add(str(fp.resolve()))

    java_files = iter_java_files(migrated)

    # Check 2: file coverage — a .java file with Spark imports must be in analysis.
    coverage_gaps: list[str] = []
    for jf in java_files:
        if str(jf.resolve()) in analyzed_files:
            continue
        try:
            txt = jf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "import org.apache.spark" in txt:
            coverage_gaps.append(jf.name)
    if coverage_gaps:
        report.add(
            "file coverage",
            STATUS_GAP,
            f"{len(coverage_gaps)} Spark file(s) absent from analysis: "
            + ", ".join(coverage_gaps[:5]) + ("…" if len(coverage_gaps) > 5 else ""),
        )
    else:
        report.add("file coverage", STATUS_OK, f"{len(java_files)} file(s)")

    # Check 3: blind-spot scan.
    blind_hits: list[str] = []
    for jf in java_files:
        if str(jf.resolve()) in analyzed_files:
            continue
        try:
            txt = jf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for label, pat in _BLIND_SPOT_PATTERNS:
            if re.search(pat, txt):
                blind_hits.append(f"{jf.name}:{label}")
                break
    if blind_hits:
        report.add(
            "blind-spot scan",
            STATUS_GAP,
            f"{len(blind_hits)} unanalyzed file(s) with risky patterns: "
            + ", ".join(blind_hits[:5]) + ("…" if len(blind_hits) > 5 else ""),
        )
    else:
        report.add("blind-spot scan", STATUS_OK, "")

    # Check 4: risk-distribution sanity.
    risks = [e.get("final_risk", e.get("risk", 0.0)) for e in entries if isinstance(e, dict)]
    any_spark = any(
        "import org.apache.spark" in jf.read_text(encoding="utf-8", errors="replace")
        for jf in java_files
    )
    if not entries and any_spark:
        report.add(
            "risk distribution",
            STATUS_GAP,
            "0 issues but source contains Spark imports — possible analyzer failure",
        )
    elif risks and all(r < 0.1 for r in risks):
        has_hot = any(
            re.search(r"sparkContext|\.rdd\b|HiveWarehouseSession|spark\.sql\.catalyst",
                      jf.read_text(encoding="utf-8", errors="replace"))
            for jf in java_files
        )
        if has_hot:
            report.add(
                "risk distribution",
                STATUS_GAP,
                "all issues <0.1 risk but hot patterns present — possible false negatives",
            )
        else:
            report.add("risk distribution", STATUS_OK, "")
    else:
        report.add("risk distribution", STATUS_OK, f"{len(entries)} issue(s)")


def verify_phase_2_java(state: dict, state_path: Path, report: PhaseReport) -> None:
    """Phase 2 verification for Java workloads — mirrors verify_phase_2."""
    root = _conversion_root(state, state_path)
    migrated = _migrated_dir(state, state_path)
    java_files = iter_java_files(migrated)
    notebooks = _jvm_notebook_units(migrated, state, "java")

    # Check 0: orchestration enforcement.
    code_units = len(java_files) + len(notebooks)
    if code_units >= 2 and (
        "max_parallel_fixers" not in state or "phase2_chunks" not in state
    ):
        report.add(
            "phase 2 orchestration", STATUS_FAIL,
            f"multi-file workload ({code_units} files) but migration_state.json "
            "has no orchestrator plan (max_parallel_fixers / phase2_chunks) — run "
            "scripts/orchestrate_phases.py --phase 2 --language java and dispatch the "
            "printed waves instead of fixing inline",
        )
    else:
        report.add("phase 2 orchestration", STATUS_OK, "")

    # Check 1: syntax artifacts (Java import lines must be syntactically pure).
    artifacts: list[str] = []
    for jf in java_files:
        for i, raw in enumerate(_read(jf).splitlines(), start=1):
            if _IMPORT_ARTIFACT_RE.search(raw) or _BARE_EMDASH_RE.match(raw):
                artifacts.append(f"{jf.name}:{i}")
    if artifacts:
        report.add("syntax artifacts", STATUS_FAIL,
                   "malformed lines: " + ", ".join(artifacts[:8]))
    else:
        report.add("syntax artifacts", STATUS_OK, "")

    # Check 2: high-risk coverage (same logic as Scala).
    analysis_path = root / "analysis.json"
    entries: list = []
    if analysis_path.exists():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            entries = analysis if isinstance(analysis, list) else analysis.get("issues", [])
        except json.JSONDecodeError:
            entries = []

    lines_by_key: dict[str, tuple[str, list]] = {}
    for jf in java_files:
        try:
            disp = str(jf.relative_to(migrated))
        except ValueError:
            disp = jf.name
        key = _abs_key(jf, migrated)
        lines_by_key[key] = (disp, _read(jf).splitlines())

    n_high_risk = 0
    uncovered: list[str] = []
    safe_no_reason: list[str] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        rng = _parse_lines(e.get("lines"))
        f = e.get("file")
        if rng is None or not isinstance(f, str):
            continue
        unit = lines_by_key.get(_abs_key(f, migrated))
        if unit is None:
            continue
        disp, flines = unit
        if _coerce_risk(e.get("final_risk", e.get("risk"))) < 0.7:
            continue
        n_high_risk += 1
        resolution = (e.get("resolution") or "").strip().lower()
        reason = (e.get("resolution_reason") or "").strip()
        if resolution == "safe" and not reason:
            safe_no_reason.append(f"{disp}:{rng[0]}")
        elif not _marker_near(flines, rng[0], rng[1]) and resolution not in _RESOLVED_VERDICTS:
            uncovered.append(f"{disp}:{rng[0]}")
    if safe_no_reason or uncovered:
        msgs = []
        if uncovered:
            msgs.append("high-risk issue(s) with no nearby // SCOS marker or "
                        "analysis resolution: " + ", ".join(uncovered[:8]))
        if safe_no_reason:
            msgs.append("resolution='safe' without resolution_reason: "
                        + ", ".join(safe_no_reason[:8]))
        report.add("high-risk coverage", STATUS_FAIL, "; ".join(msgs))
    elif n_high_risk:
        report.add("high-risk coverage", STATUS_OK,
                   f"{n_high_risk} high-risk issue(s) covered (marker-near or resolution)")
    else:
        report.add("high-risk coverage", STATUS_OK, "no high-risk issues")

    # Check 3: no-op over-annotation.
    over_annotated: list[str] = []
    for jf in java_files:
        for i, raw in enumerate(_read(jf).splitlines(), start=1):
            if _SCOS_MARKER in raw and _NOOP_RE.search(raw) and ".rdd" not in raw:
                over_annotated.append(f"{jf.name}:{i}")
    if over_annotated:
        report.add("no-op over-annotation", STATUS_FAIL,
                   "annotated no-ops: " + ", ".join(over_annotated[:8]))
    else:
        report.add("no-op over-annotation", STATUS_OK, "")

    # Check 4: stale cross-file references.
    stale: list[str] = []
    for jf in java_files:
        raw_lines = _read(jf).splitlines()
        for lineno, code in code_lines(_read(jf)):
            if _STALE_REF_RE.search(code):
                ctx = raw_lines[max(0, lineno - 2):lineno]
                if any(("// EWI:" in r or "// SCOS:" in r) for r in ctx):
                    continue
                stale.append(f"{jf.name}:{lineno}")
    if stale:
        report.add("cross-file consistency", STATUS_FAIL,
                   "stale refs in code: " + ", ".join(stale[:8]))
    else:
        report.add("cross-file consistency", STATUS_OK, "")

    # Check 5/6: file count + no empty files.
    _check_java_file_count(state, java_files, report)
    empties = [jf.name for jf in java_files if jf.stat().st_size == 0]
    if empties:
        report.add("no empty files", STATUS_FAIL, "0-byte file(s): " + ", ".join(empties[:8]))
    else:
        report.add("no empty files", STATUS_OK, "")

    # Check 7: preserved-config markers survived the fixer.
    # JavaParser rules use the javaparser: recipe_id namespace.
    recipe_edits = state.get("recipe_edits", {}) or {}
    java_preserve_ids = {
        "javaparser:ScosSparkSessionBuilderRewrite",
    }
    dropped: list[str] = []
    for rel, edits in recipe_edits.items():
        if not any((e or {}).get("recipe_id") in java_preserve_ids for e in (edits or [])):
            continue
        jf_path = migrated / rel
        if not jf_path.exists():
            continue
        txt = _read(jf_path)
        if "SCOS-RECIPE-PRESERVED-CONFIG" not in txt and "SCOS-RECIPE-INSERT-AFTER-BUILDER" not in txt:
            dropped.append(Path(rel).name)
    if dropped:
        report.add("preserved-config markers survived", STATUS_FAIL,
                   "fixer dropped recipe preserve-config markers in: "
                   + ", ".join(dropped[:8]))
    else:
        report.add("preserved-config markers survived", STATUS_OK, "")

    # Check 8: best-effort brace/paren balance (Java compile-syntax check).
    # Uses a tokenizer fallback — no JDK required. Hard failures are syntax
    # regressions the fixer introduced; missing a JDK is not a failure.
    syntax_errors: list[str] = []
    for jf in java_files:
        err = _java_brace_balance(_read(jf))
        if err:
            syntax_errors.append(f"{jf.name}: {err}")
    if syntax_errors:
        report.add("java syntax (tokenizer)", STATUS_FAIL,
                   "balance errors: " + "; ".join(syntax_errors[:8]))
    else:
        report.add("java syntax (tokenizer)", STATUS_OK,
                   f"{len(java_files)} file(s) passed brace/paren balance check")

    # Check 9: notebook coverage. Java notebooks (.ipynb / Databricks-native /
    # exported) are not *.java files, so every line-based check above skips them.
    # Mirrors verify_phase_2 Check 8 against the flattened Java cell source so
    # notebooks are not a blind spot on the Java path.
    if notebooks:
        bad_parse = [n["display"] for n in notebooks if n["parse_error"]]
        if bad_parse:
            report.add("notebook validity", STATUS_FAIL,
                       "unparseable notebook(s): " + ", ".join(bad_parse[:8]))
        else:
            report.add("notebook validity", STATUS_OK,
                       f"{len(notebooks)} java notebook(s) parsed")

        nb_artifacts: list[str] = []
        for n in notebooks:
            for i, raw in enumerate(n["source"].splitlines(), start=1):
                if _IMPORT_ARTIFACT_RE.search(raw) or _BARE_EMDASH_RE.match(raw):
                    nb_artifacts.append(f"{n['display']}:{i}")
        if nb_artifacts:
            report.add("notebook syntax artifacts", STATUS_FAIL,
                       "malformed lines: " + ", ".join(nb_artifacts[:8]))
        else:
            report.add("notebook syntax artifacts", STATUS_OK, "")

        # High-risk coverage for notebooks — same mechanism as Check 2, applied to
        # each notebook's flattened Java source. Notebooks are matched to analyzer
        # entries by basename (their flattened-source line numbers are what the
        # analyzer recorded against the notebook file).
        nb_by_name = {n["display"]: n for n in notebooks}
        nb_uncovered: list[str] = []
        nb_safe_no_reason: list[str] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            rng = _parse_lines(e.get("lines"))
            f = e.get("file")
            if rng is None or not isinstance(f, str):
                continue
            n = nb_by_name.get(Path(f).name)
            if n is None or _coerce_risk(e.get("final_risk", e.get("risk"))) < 0.7:
                continue
            flines = n["source"].splitlines()
            resolution = (e.get("resolution") or "").strip().lower()
            reason = (e.get("resolution_reason") or "").strip()
            if resolution == "safe" and not reason:
                nb_safe_no_reason.append(f"{n['display']}:{rng[0]}")
            elif not _marker_near(flines, rng[0], rng[1]) and resolution not in _RESOLVED_VERDICTS:
                nb_uncovered.append(f"{n['display']}:{rng[0]}")
        if nb_uncovered or nb_safe_no_reason:
            parts = []
            if nb_uncovered:
                parts.append("notebook high-risk issue(s) with no nearby // SCOS "
                             "marker or resolution: " + ", ".join(nb_uncovered[:8]))
            if nb_safe_no_reason:
                parts.append("resolution='safe' without resolution_reason: "
                             + ", ".join(nb_safe_no_reason[:8]))
            report.add("notebook high-risk coverage", STATUS_FAIL, "; ".join(parts))
        else:
            report.add("notebook high-risk coverage", STATUS_OK, "")


def verify_phase_3_java(state: dict, state_path: Path, report: PhaseReport) -> None:
    """Phase 3 verification for Java workloads — mirrors verify_phase_3."""
    migrated = _migrated_dir(state, state_path)
    java_files = iter_java_files(migrated)
    prod_files = [jf for jf in java_files
                  if not is_test_java_path(str(jf.relative_to(migrated)))]

    # Check 1: migration header on every file.
    missing_header = [jf.name for jf in java_files
                      if "SCOS Migration" not in "\n".join(_read(jf).splitlines()[:15])]
    if missing_header:
        report.add("migration header", STATUS_FAIL,
                   "missing header: " + ", ".join(missing_header[:8]))
    else:
        report.add("migration header", STATUS_OK, f"{len(java_files)} file(s)")

    # Check 2: no enableHiveSupport / yarn|local master in non-comment prod code.
    hive_hits: list[str] = []
    for jf in prod_files:
        for lineno, code in code_lines(_read(jf)):
            if _HIVE_MASTER_RE.search(code):
                hive_hits.append(f"{jf.name}:{lineno}")
    if hive_hits:
        report.add("session init replaced", STATUS_FAIL,
                   "Hive/master refs in prod: " + ", ".join(hive_hits[:8]))
    else:
        report.add("session init replaced", STATUS_OK, "")

    # Check 3: SnowparkConnectSession present; no SparkSession...remote in prod.
    has_scos = any("SnowparkConnectSession" in _read(jf) for jf in prod_files)
    if not has_scos:
        report.add("SnowparkConnectSession init", STATUS_FAIL,
                   "no non-test file initializes SnowparkConnectSession")
    else:
        report.add("SnowparkConnectSession init", STATUS_OK, "")
    remote_hits: list[str] = []
    for jf in prod_files:
        for lineno, code in code_lines(_read(jf)):
            if _SPARK_REMOTE_RE.search(code):
                remote_hits.append(f"{jf.name}:{lineno}")
    if remote_hits:
        report.add("no SparkSession.remote", STATUS_FAIL,
                   "vanilla remote() in prod: " + ", ".join(remote_hits[:8]))
    else:
        report.add("no SparkSession.remote", STATUS_OK, "")

    # Check 4: no unsupported imports in non-comment code.
    unsupported: list[str] = []
    for jf in java_files:
        for lineno, code in code_lines(_read(jf)):
            if _UNSUPPORTED_IMPORT_RE.search(code):
                unsupported.append(f"{jf.name}:{lineno}")
    if unsupported:
        report.add("no unsupported imports", STATUS_FAIL,
                   "unsupported import(s): " + ", ".join(unsupported[:8]))
    else:
        report.add("no unsupported imports", STATUS_OK, "")

    # Check 5: build-file verification (same as Scala — same build files).
    _verify_build_files(migrated, state, report)

    # Check 6: syntax artifacts.
    artifacts: list[str] = []
    for jf in java_files:
        for i, raw in enumerate(_read(jf).splitlines(), start=1):
            if _IMPORT_ARTIFACT_RE.search(raw) or _BARE_EMDASH_RE.match(raw):
                artifacts.append(f"{jf.name}:{i}")
    report.add("syntax artifacts", STATUS_FAIL if artifacts else STATUS_OK,
               ("malformed: " + ", ".join(artifacts[:8])) if artifacts else "")

    # Check 7: file count.
    _check_java_file_count(state, java_files, report)

    # Check 8: preserved-config materialization (same pattern as Scala).
    _verify_preserved_config(java_files, report)

    # Check 9: no surviving System.getenv calls (Phase 3 rewrites these to
    # System.getProperty).  Surviving calls receive null from the SCOS harness
    # because EnvUtil.setEnv writes via System.setProperty — it cannot mutate
    # the OS process environment that System.getenv reads from.
    # Advisory (GAP) rather than hard FAIL — mirrors the Scala check.
    _JAVA_GETENV_RE = re.compile(r'\bSystem\s*\.\s*getenv\s*\(')
    residual: list[str] = []
    for jf in prod_files:
        text = _read(jf)
        for lineno, code in code_lines(text):
            if _JAVA_GETENV_RE.search(code):
                residual.append(f"{jf.name}:{lineno}")
    if residual:
        report.add("System_getenv_residual", STATUS_GAP,
                   f"{len(residual)} System.getenv call(s) survived Phase 3 — "
                   "harness uses System.setProperty; getenv returns null: "
                   + ", ".join(residual[:6]))
    else:
        report.add("System_getenv_residual", STATUS_OK, "")


def verify_phase_4_java(state: dict, state_path: Path, report: PhaseReport) -> None:
    """Phase 4 verification for Java workloads — mirrors verify_phase_4."""
    root = _conversion_root(state, state_path)
    reports_dir = root / "Reports"
    required = ["Issues.csv", "InputFilesInventory.csv", "ArtifactDependencyInventory.csv"]

    missing = [n for n in required if not (reports_dir / n).exists()]
    if missing:
        report.add("required CSVs", STATUS_FAIL, "missing: " + ", ".join(missing))
        return
    report.add("required CSVs", STATUS_OK, "all 3 present")

    issues = reports_dir / "Issues.csv"
    issues_lines = _read(issues).splitlines()
    has_data = len(issues_lines) > 1
    report.add("Issues.csv", STATUS_OK,
               f"{len(issues_lines) - 1} issue row(s)" if has_data else "0 issues (clean migration)")

    inv_text = _read(reports_dir / "InputFilesInventory.csv")
    inv_lines = inv_text.splitlines()
    inv_rows = max(0, len(inv_lines) - 1)
    if inv_rows == 0:
        report.add("InputFilesInventory rows", STATUS_FAIL, "0 data rows")
    else:
        manifest_java = sum(1 for m in state.get("manifest", []) if str(m).endswith(".java"))
        java_rows: int | None = None
        try:
            reader = csv.DictReader(io.StringIO(inv_text))
            if reader.fieldnames and "Extension" in reader.fieldnames:
                java_rows = sum(
                    1 for r in reader if (r.get("Extension") or "").strip().lower() == ".java"
                )
        except (csv.Error, ValueError):
            java_rows = None
        compare_rows = java_rows if java_rows is not None else inv_rows
        label = "java code row(s)" if java_rows is not None else "row(s)"
        detail = f"{inv_rows} total row(s); {compare_rows} {label}"
        if manifest_java and abs(compare_rows - manifest_java) > max(2, manifest_java // 2):
            report.add("InputFilesInventory rows", STATUS_GAP,
                       f"{compare_rows} {label} vs ~{manifest_java} manifest .java")
        else:
            report.add("InputFilesInventory rows", STATUS_OK, detail)

    header = issues_lines[0] if issues_lines else ""
    expected_cols = ("EWI", "File", "Line", "Description")
    if not any(c.lower() in header.lower() for c in expected_cols):
        report.add("Issues.csv structure", STATUS_GAP, f"unexpected header: {header[:80]}")
    else:
        report.add("Issues.csv structure", STATUS_OK, "")

    # Java reuses SPRKCNTSCL* EWI codes (SMA JVM family).
    blob = _read(issues)
    if not has_data:
        report.add("EWI code prefix", STATUS_OK, "no issues to classify")
    elif "SPRKCNTSCL" not in blob:
        report.add("EWI code prefix", STATUS_FAIL,
                   "issue rows present but no SPRKCNTSCL codes found (Java reuses SPRKCNTSCL*)")
    else:
        report.add("EWI code prefix", STATUS_OK, "")


# --------------------------------------------------------------------------- #
# Dispatch + output
# --------------------------------------------------------------------------- #

_PHASE_FUNCS = {
    1: verify_phase_1,
    2: verify_phase_2,
    3: verify_phase_3,
    4: verify_phase_4,
}

_PHASE_FUNCS_JAVA = {
    1: verify_phase_1_java,
    2: verify_phase_2_java,
    3: verify_phase_3_java,
    4: verify_phase_4_java,
}


def run_phase(phase: int, state: dict, state_path: Path,
              language: str = "scala") -> PhaseReport:
    report = PhaseReport(phase=phase, state_path=str(state_path))
    funcs = _PHASE_FUNCS_JAVA if language == "java" else _PHASE_FUNCS
    funcs[phase](state, state_path, report)
    return report


def render_text(report: PhaseReport) -> str:
    lines = [f"Verifying Phase {report.phase}: {report.state_path}", ""]
    for c in report.checks:
        icon = _STATUS_ICON.get(c.status, "[??]  ")
        lines.append(f"  {icon} {c.name:<26} {c.detail}")
    lines.append("")
    fails = [c for c in report.checks if c.status == STATUS_FAIL]
    gaps = [c for c in report.checks if c.status == STATUS_GAP]
    if fails:
        lines.append(f"FAIL: {len(fails)} check(s) failed:")
        for c in fails:
            lines.append(f"  - {c.name}: {c.detail}")
    elif gaps:
        lines.append(f"PASS_WITH_GAPS: {len(gaps)} advisory gap(s):")
        for c in gaps:
            lines.append(f"  - {c.name}: {c.detail}")
    else:
        lines.append("PASS: all checks green.")
    return "\n".join(lines)


def render_json(report: PhaseReport) -> str:
    return json.dumps(
        {
            "phase": report.phase,
            "state_path": report.state_path,
            "verdict": report.verdict,
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail}
                for c in report.checks
            ],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else "")
    parser.add_argument("--state", required=True, help="Path to migration_state.json")
    parser.add_argument("--phase", required=True, type=int, choices=[1, 2, 3, 4],
                        help="Migration phase to verify (1=analysis, 2=fixes, 3=imports, 4=reports)")
    parser.add_argument("--language", default="scala", choices=["scala", "java"],
                        help="Migration language (default: scala; use 'java' for Java migrations).")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 on FAIL. Without --strict, always exit 0 (advisory).")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON instead of human text.")
    args = parser.parse_args(argv)

    state_path = Path(args.state)
    if not state_path.exists():
        print(f"ERROR: state file not found: {state_path}", file=sys.stderr)
        return 2
    try:
        state = load_state(state_path)
    except json.JSONDecodeError as e:
        print(f"ERROR: state file is not valid JSON: {e}", file=sys.stderr)
        return 2

    report = run_phase(args.phase, state, state_path, language=args.language)
    print(render_json(report) if args.json else render_text(report))

    if args.strict and report.has_failures:
        return 1
    return 0


if __name__ == "__main__":
    # Self-checks: exercise the comment stripper and test-path detector so a
    # broken edit fails loudly when the script is run directly.
    assert strip_comment("  // pure comment") == ""
    assert strip_comment('val x = f()  // trailing').strip() == "val x = f()"
    assert strip_comment(" * javadoc body") == ""
    assert is_test_path("src/test/scala/FooSpec.scala")
    assert is_test_path("a/b/MyTest.scala")
    assert not is_test_path("src/main/scala/Main.scala")
    assert is_test_java_path("src/test/java/FooTest.java")
    assert is_test_java_path("com/x/FooTest.java")
    assert not is_test_java_path("src/main/java/Foo.java")
    assert _NOOP_RE.search("df.repartition(4)")
    assert _UNSUPPORTED_IMPORT_RE.search("import org.apache.hadoop.fs.Path")
    assert not _UNSUPPORTED_IMPORT_RE.search("import org.apache.spark.sql.functions")
    print("verify_phase self-checks passed.")
    raise SystemExit(main())
