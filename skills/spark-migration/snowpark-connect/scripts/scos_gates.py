#!/usr/bin/env python3
"""Deterministic phase gates for the PySpark -> SCOS migration skill.

These gates replace the LLM "critic" sub-agents with byte-for-byte
reproducible checks. An LLM grading another LLM's output is a second
probabilistic step that burns tokens, adds latency, and can hallucinate a
PASS; every check the critics actually performed was a `grep`/`py_compile`/
`json.load` under the hood, so they belong in a script the coordinator runs
as a deterministic node.

Subcommands (one per former LLM "critic"):
  analyzer   Phase 1  — analysis.json completeness + known analyzer blind spots.
  imports    Phase 3  — migration headers, session-init replacement, no
                        unsupported imports, snowpark_connect present.
  reports    Phase 1.2 / Phase 4 — assessment HTML+IR or dashboard CSVs exist and
                        are well-formed (--section assessment|csvs).
  fixer      Phase 2  — syntax (py_compile), notebook JSON validity, high-risk
                        issue coverage, no empty/missing files.

Stdlib only (no third-party deps), but invoke through `uv run` for a guaranteed
interpreter across macOS / Linux / Windows, matching the rest of the skill:

    uv run --project <SKILL_DIR> python scripts/scos_gates.py analyzer \
        --state <CONVERSION>/migration_state.json
    uv run --project <SKILL_DIR> python scripts/scos_gates.py analyzer \
        --state ... --json

(A bare `python3 scripts/scos_gates.py ...` also works wherever python3 is on PATH.)

Exit codes:
  0  PASS or PASS_WITH_GAPS (only advisory WARN findings)
  2  FAIL (one or more CRITICAL findings; coordinator should re-dispatch the
     analyzer with `gaps` as feedback, then re-run this gate)
  3  Usage / IO error (could not read state, analysis, or sources)
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import py_compile
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Iterator

# Optional dep (lives in the same scripts/ dir; available whenever the gate
# is invoked from the skill via `uv run --project <SKILL_DIR>`). Imported
# lazily so the gate stays runnable in test environments that mock out the
# surrounding skill directory.
try:
    import notebook_io  # type: ignore[import-not-found]

    _NOTEBOOK_IO = True
except ImportError:
    notebook_io = None  # type: ignore[assignment]
    _NOTEBOOK_IO = False

# Placeholder line that generate_scos_reports stamps when Phase 3 was skipped.
# A header carrying this is NOT a finished header — the imports gate must FAIL
# on it. Sourced from notebook_io (single source of truth) with a literal
# fallback for the lazy-import case.
_STUB_HEADER_SENTINEL = (
    getattr(notebook_io, "STUB_HEADER_SENTINEL", None)
    if _NOTEBOOK_IO
    else None
) or "Deterministic header added by report generator"

# Revert/cleanup helpers shared with revert_failing_files.py (same scripts/ dir).
# The fixer gate's opt-in --revert-failing mode reuses them so there is one
# compile-and-revert implementation, not two. Imported lazily for the same
# reason as notebook_io above.
try:
    from revert_failing_files import _git_revert, _purge_pycache  # type: ignore[import-not-found]

    _REVERT_HELPERS = True
except ImportError:
    _REVERT_HELPERS = False

# --- Severity -----------------------------------------------------------------

CRITICAL = "CRITICAL"  # hard FAIL — a real, well-known incompatibility was not flagged
WARN = "WARN"          # advisory gap — report for optional re-scan, do not block

EXIT_PASS = 0
EXIT_FAIL = 2
EXIT_USAGE = 3


@dataclass
class Finding:
    severity: str
    code: str            # short machine-readable id
    message: str
    file: str | None = None
    line: int | None = None


@dataclass
class GateResult:
    gate: str
    verdict: str                      # PASS | PASS_WITH_GAPS | FAIL
    findings: list[Finding] = field(default_factory=list)
    # Files reverted to the pre-Phase-2 baseline by the fixer gate's opt-in
    # --revert-failing safety net (empty unless that mode is used).
    reverted: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return EXIT_FAIL if self.verdict == "FAIL" else EXIT_PASS


# --- Blind-spot patterns: the analyzer's known misses, made explicit --------
#
# Each pattern is (code, severity, compiled regex). CRITICAL patterns are
# well-known hard incompatibilities the analyzer is REQUIRED to flag; if a
# match has no covering analysis.json entry the gate FAILs. WARN patterns are
# real but noisier / often handled by recipes, so an uncovered match is only
# advisory.
_BLIND_SPOTS: list[tuple[str, str, re.Pattern]] = [
    # JVM internals are never available on Spark Connect / SCOS (SPRKCNTPY4000).
    ("jvm_attr", CRITICAL, re.compile(r"\._j(df|vm|seq|map|cols)\b")),
    ("spark_context", CRITICAL, re.compile(r"\bsparkContext\b|\bSparkContext\b")),
    ("udf_decorator", CRITICAL, re.compile(r"@\s*udf\b")),
    ("pandas_udf_decorator", CRITICAL, re.compile(r"@\s*pandas_udf\b")),
    ("udtf_decorator", CRITICAL, re.compile(r"@\s*udtf\b")),
    ("apply_in_pandas", CRITICAL, re.compile(r"\bapplyInPandas\b")),
    ("checkpoint", CRITICAL, re.compile(r"\.checkpoint\s*\(")),
    # Noisier / sometimes recipe-handled — advisory only.
    ("map_subscript_col", WARN, re.compile(r"\[\s*col\s*\(")),
    ("hadoop_fs", WARN, re.compile(r"\bhadoop\b|hdfs://")),
    ("use_database_schema", WARN, re.compile(r"USE\s+DATABASE\b|USE\s+SCHEMA\b", re.IGNORECASE)),
    ("deequ", WARN, re.compile(r"\bpydeequ\b|\bdeequ\b")),
    ("delta_lake", WARN, re.compile(r"delta\.tables|\bDeltaTable\b|format\(\s*[\"']delta[\"']\s*\)")),
    ("ml_pipeline", WARN, re.compile(r"\bVectorAssembler\b|\bCrossValidator\b|\bPipeline\b")),
]

# Risk-sanity probes: if the only issues are near-zero risk yet the source uses
# one of these, the analyzer likely produced false negatives.
_LOW_RISK_RED_FLAGS = re.compile(r"\bsparkContext\b|\.rdd\b|\bbroadcast\s*\(")
_PYSPARK_IMPORT = re.compile(r"^\s*(import\s+pyspark|from\s+pyspark)", re.MULTILINE)


# --- Helpers ------------------------------------------------------------------

def _fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"scos_gates: {msg}", file=sys.stderr)
    sys.exit(EXIT_USAGE)


def _coerce_risk(value: object) -> float | None:
    """Best-effort float for an LLM-supplied `final_risk`.

    Returns None for missing/non-numeric values (e.g. "high", None, a dict)
    so a malformed entry is skipped rather than crashing the gate with an
    unhandled traceback (which would exit 1 instead of a clean gate result).
    """
    if isinstance(value, bool):  # bool is an int subclass; treat as absent
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _parse_lines(spec: object) -> tuple[int, int] | None:
    """Parse an analysis.json `lines` value ("12-15" or "30-30" or 30)."""
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


def _is_code_line(stripped: str) -> bool:
    """Skip blank lines, full-line comments, and already-annotated lines."""
    if not stripped or stripped.startswith("#"):
        return False
    if "# SCOS" in stripped:  # already flagged by a recipe / prior pass
        return False
    return True


def load_state(state_path: Path) -> dict:
    try:
        return json.loads(state_path.read_text())
    except FileNotFoundError:
        _fail(f"migration_state.json not found: {state_path}")
    except json.JSONDecodeError as e:
        _fail(f"migration_state.json is not valid JSON: {e}")


def resolve_paths(state: dict, state_path: Path) -> tuple[Path, Path, list[str]]:
    """Return (analysis_path, migrated_dir, manifest)."""
    conv = state.get("conversion_root") or str(state_path.parent)
    conv_path = Path(conv)
    if not conv_path.is_absolute():
        conv_path = (state_path.parent / conv_path).resolve()
    migrated = state.get("migrated_dir") or str(conv_path / "Output")
    migrated_path = Path(migrated)
    if not migrated_path.is_absolute():
        migrated_path = (state_path.parent / migrated_path).resolve()
    analysis_path = conv_path / "analysis.json"
    manifest = [f for f in state.get("manifest", []) if isinstance(f, str)]
    return analysis_path, migrated_path, manifest


def index_analysis(entries: list[dict]) -> dict[str, list[tuple[int, int]]]:
    """Map basename -> list of (start, end) line ranges with an issue.

    Analysis `file` paths are often absolute paths captured on another machine,
    so we key on basename (good enough for the gate; collisions only make it
    more lenient, never falsely failing).
    """
    by_base: dict[str, list[tuple[int, int]]] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue  # analysis.json is LLM-generated; tolerate non-object items
        f = e.get("file")
        if not isinstance(f, str):
            continue
        rng = _parse_lines(e.get("lines"))
        if rng is None:
            continue
        by_base.setdefault(os.path.basename(f), []).append(rng)
    return by_base


def _covered(by_base: dict[str, list[tuple[int, int]]], basename: str, line: int) -> bool:
    for start, end in by_base.get(basename, []):
        if start <= line <= end:
            return True
    return False


def _gather_files(migrated_dir: Path, manifest: list[str], suffix: str) -> list[Path]:
    """Manifest files with the given suffix; fall back to a directory walk."""
    files: list[Path] = []
    for rel in manifest:
        if rel.endswith(suffix):
            p = migrated_dir / rel
            if p.exists():
                files.append(p)
    if not files:
        for dp, dirs, fs in os.walk(migrated_dir):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
            files.extend(Path(dp) / fn for fn in fs if fn.endswith(suffix))
    return files


# --- Notebook-aware source iteration -----------------------------------------
#
# Workloads in the goldset (and real customer migrations) routinely include
# notebooks: `.ipynb` (Jupyter), Databricks-native `.python` / `.scala` / `.sql`
# (JSON-encoded notebook-source format), and Databricks-exported `.py` files
# whose first line is `# Databricks notebook source`. The gates need to apply
# the same import / header / live-code / py_compile checks to notebook
# *cells* that they apply to plain .py files — otherwise a workload whose
# entry point is a notebook bypasses the gate entirely.
#
# Two iterators below:
#   _iter_python_file_units : one row per FILE (.py or python notebook), with
#                             the file's full python source concatenated
#                             (header / substring / regex scans).
#   _iter_python_code_units : one row per COMPILATION unit (.py = one row;
#                             notebook = one row per python code-cell), for
#                             py_compile / compile() syntax checks.
#
# Databricks-exported `.py` notebooks are already covered by the .py-suffix
# iteration (their on-disk extension is .py), so we skip them in the
# notebook iterators to avoid double-counting.


def _python_notebook_paths(
    migrated_dir: Path, manifest: list[str], state: dict
) -> list[Path]:
    """Return absolute paths of Python-language *true* notebook files.

    Sources, in priority order:
      1. ``state["notebook_index"]`` (built by ``orchestrate_phases.py``)
         — most reliable; carries format + language metadata per file.
      2. Manifest entries ending in ``.ipynb`` / ``.python``.
      3. Walk ``migrated_dir`` for ``.ipynb`` / ``.python``.

    Excludes Databricks-exported ``.py`` (those are already covered by the
    .py-suffix iteration; including them here would double-process).
    """
    paths: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        if p.exists():
            paths.append(p)

    nb_index = state.get("notebook_index") or {}
    if isinstance(nb_index, dict):
        for abs_path, info in nb_index.items():
            if not isinstance(info, dict):
                continue
            if info.get("language") != "python":
                continue
            fmt = info.get("format")
            # exported_text notebooks ARE .py files on disk — skip; they're
            # picked up by the .py iteration.
            if fmt in (None, "exported_text", "not_notebook"):
                continue
            p = Path(abs_path)
            if not p.is_absolute():
                p = (migrated_dir / p).resolve()
            _add(p)

    if not paths:
        # Fall back to manifest + directory walk for .ipynb / .python.
        for rel in manifest:
            if rel.endswith((".ipynb", ".python")):
                _add(migrated_dir / rel)
        if not paths:
            for dp, dirs, fs in os.walk(migrated_dir):
                dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
                for fn in fs:
                    if fn.endswith((".ipynb", ".python")):
                        _add(Path(dp) / fn)

    return paths


@dataclass
class _FileUnit:
    """One row per source FILE (whole-file text scan)."""
    path: Path
    display: str        # filename for diagnostics
    text: str           # full python source (concatenated for notebooks)
    is_notebook: bool


@dataclass
class _CodeUnit:
    """One row per COMPILATION unit (py_compile-equivalent input)."""
    path: Path
    display: str        # 'foo.py' or 'foo.ipynb#cell-3'
    text: str
    is_notebook: bool


def _iter_python_file_units(
    migrated_dir: Path, manifest: list[str], state: dict
) -> Iterator[_FileUnit]:
    """Yield ``_FileUnit`` per Python source file (``.py`` or notebook).

    For notebooks, ``text`` is the concatenation of all python code-cell
    sources joined with blank lines — sufficient for substring / regex
    checks the gates apply to plain .py files.
    """
    for p in _gather_files(migrated_dir, manifest, ".py"):
        t = _read_text(p)
        if t is None:
            continue
        yield _FileUnit(path=p, display=p.name, text=t, is_notebook=False)

    if not _NOTEBOOK_IO:
        return
    for nb in _python_notebook_paths(migrated_dir, manifest, state):
        try:
            parsed = notebook_io.parse_notebook(str(nb))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        chunks: list[str] = []
        for cell in parsed.cells:
            if cell.cell_type != "code":
                continue
            if cell.cell_language not in ("python", "unknown"):
                continue
            chunks.append(cell.source)
        if not chunks:
            continue
        yield _FileUnit(
            path=nb,
            display=nb.name,
            text="\n\n".join(chunks),
            is_notebook=True,
        )


def _iter_python_code_units(
    migrated_dir: Path, manifest: list[str], state: dict
) -> Iterator[_CodeUnit]:
    """Yield ``_CodeUnit`` per compilation unit.

    A ``.py`` file is one unit (the whole file). A notebook contributes one
    unit per python code-cell, so ``compile()`` sees each cell with its own
    indentation context — matching what the kernel actually executes.
    """
    for p in _gather_files(migrated_dir, manifest, ".py"):
        t = _read_text(p)
        if t is None:
            continue
        yield _CodeUnit(path=p, display=p.name, text=t, is_notebook=False)

    if not _NOTEBOOK_IO:
        return
    for nb in _python_notebook_paths(migrated_dir, manifest, state):
        try:
            parsed = notebook_io.parse_notebook(str(nb))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        for cell in parsed.cells:
            if cell.cell_type != "code":
                continue
            if cell.cell_language not in ("python", "unknown"):
                continue
            yield _CodeUnit(
                path=nb,
                display=f"{nb.name}#cell-{cell.index}",
                text=cell.source,
                is_notebook=True,
            )


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def _load_entries(analysis_path: Path) -> list | None:
    """Parse analysis.json into a list, or None if missing/invalid."""
    text = _read_text(analysis_path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _marker_near(lines: list[str], start: int, end: int, window: int = 3) -> bool:
    """True if a fixer-applied `# SCOS` marker appears within [start-window, end+window].

    Excludes Phase 0.5 recipe annotations (``# SCOS-TODO:``, ``# SCOS-WARN:``)
    which are *inputs* to the fixer, not evidence that the fixer acted.  Only
    actual fixer markers (``# SCOS: [``, ``# SCOS: TODO -``, ``# SCOS: Performance``)
    count as coverage.
    """
    lo = max(1, start - window)
    hi = min(len(lines), end + window)
    for i in range(lo, hi + 1):
        line = lines[i - 1]
        if "# SCOS" in line and "# SCOS-TODO:" not in line and "# SCOS-WARN:" not in line:
            return True
    return False


def _live_code_linenos(lines: list[str]) -> set[int]:
    """Line numbers that are executable code (not blank, comment, or docstring).

    Tracks triple-quoted blocks with a simple toggle. Approximate but good
    enough to avoid flagging matches that live in the migration-header
    docstring or in comments.
    """
    live: set[int] = set()
    in_doc = False
    doc_delim = ""
    for i, raw in enumerate(lines, start=1):
        if in_doc:
            if doc_delim in raw:
                in_doc = False
            continue
        opened = False
        for delim in ('"""', "'''"):
            idx = raw.find(delim)
            if idx != -1 and delim not in raw[idx + 3:]:
                in_doc, doc_delim, opened = True, delim, True
                break
        if opened:
            continue
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        live.add(i)
    return live


def _finalize(res: GateResult) -> None:
    if any(f.severity == CRITICAL for f in res.findings):
        res.verdict = "FAIL"
    elif res.findings:
        res.verdict = "PASS_WITH_GAPS"
    else:
        res.verdict = "PASS"


# --- Analyzer gate ------------------------------------------------------------

def run_analyzer_gate(state_path: Path) -> GateResult:
    state = load_state(state_path)
    analysis_path, migrated_dir, manifest = resolve_paths(state, state_path)
    res = GateResult(gate="analyzer", verdict="PASS")

    # 1. analysis.json exists and is valid JSON.
    try:
        raw = analysis_path.read_text()
    except FileNotFoundError:
        res.verdict = "FAIL"
        res.findings.append(Finding(CRITICAL, "analysis_missing",
                                    f"analysis.json not found at {analysis_path}"))
        return res
    try:
        entries = json.loads(raw)
        if not isinstance(entries, list):
            raise json.JSONDecodeError("expected a JSON array", raw, 0)
    except json.JSONDecodeError as e:
        res.verdict = "FAIL"
        res.findings.append(Finding(CRITICAL, "analysis_invalid_json",
                                    f"analysis.json is not a valid JSON array: {e}"))
        return res

    by_base = index_analysis(entries)

    # Gather python source units across .py + python notebooks (.ipynb / .python).
    # Exported-text notebooks are .py on disk and are picked up automatically.
    units = list(_iter_python_file_units(migrated_dir, manifest, state))

    any_pyspark = False
    low_risk_red_flag_hit = False

    for u in units:
        text = u.text
        if _PYSPARK_IMPORT.search(text):
            any_pyspark = True
        if _LOW_RISK_RED_FLAGS.search(text):
            low_risk_red_flag_hit = True

        basename = u.path.name
        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.strip()
            if not _is_code_line(stripped):
                continue
            for code, severity, pat in _BLIND_SPOTS:
                if pat.search(raw_line) and not _covered(by_base, basename, lineno):
                    res.findings.append(Finding(
                        severity, f"blindspot:{code}",
                        f"{code} not covered by any analysis.json entry",
                        file=u.display, line=lineno))

    # 2. Risk-distribution sanity.
    if any_pyspark and len(entries) == 0:
        res.findings.append(Finding(
            CRITICAL, "empty_analysis_with_pyspark",
            "analysis.json has 0 issues but source imports pyspark — likely analyzer failure"))

    if entries:
        risks: list[float] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            r = _coerce_risk(e.get("final_risk"))
            if r is not None:
                risks.append(r)
        if risks and max(risks) < 0.1 and low_risk_red_flag_hit:
            res.findings.append(Finding(
                WARN, "all_low_risk_with_red_flags",
                "all issues have final_risk < 0.1 but source uses sparkContext/.rdd/broadcast "
                "— likely false negatives"))

    # (The Snowpark-Connect import requirement is intentionally NOT checked
    # here: Phase 3 (import-updater) injects
    # `from snowflake import snowpark_connect` unconditionally, so flagging it
    # at analysis time is redundant and would add noise to every run.)

    # Verdict.
    if any(f.severity == CRITICAL for f in res.findings):
        res.verdict = "FAIL"
    elif res.findings:
        res.verdict = "PASS_WITH_GAPS"
    else:
        res.verdict = "PASS"
    return res


# --- Imports gate (replaces import-critic) ------------------------------------

_BUILDER_RE = re.compile(r"SparkSession\s*\.\s*builder")
_UNSUPPORTED_IMPORTS: list[tuple[str, re.Pattern]] = [
    ("databricks", re.compile(r"^\s*(from|import)\s+databricks\b")),
    ("delta.tables", re.compile(r"^\s*from\s+delta\.tables\b")),
]


def run_imports_gate(state_path: Path) -> GateResult:
    state = load_state(state_path)
    _analysis, migrated_dir, manifest = resolve_paths(state, state_path)
    res = GateResult(gate="imports", verdict="PASS")

    units = list(_iter_python_file_units(migrated_dir, manifest, state))
    if not units:
        res.findings.append(Finding(CRITICAL, "no_py_files",
                                    f"no .py / .ipynb / .python files found under {migrated_dir}"))
        _finalize(res)
        return res

    has_snowpark_connect = False
    for u in units:
        if "snowpark_connect" in u.text:
            has_snowpark_connect = True
        lines = u.text.splitlines()
        live = _live_code_linenos(lines)

        # 1. Migration header present in the first 15 lines.
        # For notebooks the header lives in the first code cell, which is
        # at the top of the concatenated text — same check applies.
        head_text = "\n".join(lines[:15])
        if "SCOS Migration" not in head_text:
            res.findings.append(Finding(CRITICAL, "missing_header",
                                        "no SCOS migration header docstring in first 15 lines",
                                        file=u.display))
        # 1b. Stub header: a placeholder stamped by generate_scos_reports when
        # Phase 3 (update_imports.py) was skipped. It carries the marker but no
        # real Changes/Limitations, so reject it — the coordinator must run
        # update_imports.py to produce a rich, annotation-derived header.
        elif _STUB_HEADER_SENTINEL in "\n".join(lines[:25]):
            res.findings.append(Finding(CRITICAL, "stub_header",
                                        "placeholder header detected (Phase 3 update_imports.py "
                                        "was skipped); re-run update_imports.py to regenerate the "
                                        "rich migration header",
                                        file=u.display))

        for lineno, raw_line in enumerate(lines, start=1):
            if lineno not in live:
                continue
            pre_comment = raw_line.split("#", 1)[0]
            # 2. SparkSession.builder still in live code.
            if _BUILDER_RE.search(pre_comment):
                res.findings.append(Finding(CRITICAL, "spark_builder_in_code",
                                            "SparkSession.builder remains in live code; "
                                            "session init not replaced",
                                            file=u.display, line=lineno))
            # 4. Unsupported imports.
            for label, pat in _UNSUPPORTED_IMPORTS:
                if pat.search(raw_line):
                    res.findings.append(Finding(CRITICAL, "unsupported_import",
                                                f"unsupported import remains: {label}",
                                                file=u.display, line=lineno))

    # 3. snowpark_connect init present somewhere.
    if not has_snowpark_connect:
        res.findings.append(Finding(CRITICAL, "missing_snowpark_connect",
                                    "no file references snowpark_connect (entry-point init missing)"))

    # 5. File integrity: every manifest .py / .ipynb / .python exists in Output/.
    for rel in manifest:
        if not rel.endswith((".py", ".ipynb", ".python")):
            continue
        if not (migrated_dir / rel).exists():
            res.findings.append(Finding(CRITICAL, "manifest_file_missing",
                                        "manifest source file absent from Output/", file=rel))

    _finalize(res)
    return res


# --- Reports gate (replaces reporter-critic) ----------------------------------

def run_reports_gate(state_path: Path, section: str) -> GateResult:
    state = load_state(state_path)
    analysis_path, _migrated_dir, _manifest = resolve_paths(state, state_path)
    reports = analysis_path.parent / "Reports"
    res = GateResult(gate=f"reports:{section}", verdict="PASS")

    if section == "assessment":
        html = reports / "MigrationReadinessReport.html"
        ir = reports / "AssessmentIR.json"
        if not html.exists():
            res.findings.append(Finding(CRITICAL, "missing_html", f"{html.name} not found"))
        if not ir.exists():
            res.findings.append(Finding(CRITICAL, "missing_ir", f"{ir.name} not found"))
        htext = _read_text(html) if html.exists() else None
        if htext is not None and ("{{ " in htext or "{%" in htext):
            res.findings.append(Finding(CRITICAL, "unrendered_jinja",
                                        "HTML contains unsubstituted Jinja placeholders ({{ or {%)",
                                        file=html.name))

    elif section == "csvs":
        issues = reports / "Issues.csv"
        inv = reports / "InputFilesInventory.csv"
        dep = reports / "ArtifactDependencyInventory.csv"
        for f in (issues, inv, dep):
            if not f.exists():
                res.findings.append(Finding(CRITICAL, "missing_csv", f"{f.name} not found"))

        itext = _read_text(issues) if issues.exists() else None
        if itext is not None:
            ilines = [ln for ln in itext.splitlines() if ln.strip()]
            data_rows = max(0, len(ilines) - 1)
            if data_rows < 1:
                res.findings.append(Finding(CRITICAL, "issues_no_data",
                                            "Issues.csv has no data rows (header only)",
                                            file=issues.name))
            else:
                if "SPRKCNTPY" not in itext:
                    res.findings.append(Finding(CRITICAL, "wrong_ewi_prefix",
                                                "Issues.csv has data rows but no SPRKCNTPY codes "
                                                "(wrong language prefix?)", file=issues.name))
                header = ilines[0]
                if not any(tok.lower() in header.lower() for tok in ("ewi", "file", "line")):
                    res.findings.append(Finding(WARN, "unexpected_columns",
                                                f"Issues.csv header lacks expected columns: {header}",
                                                file=issues.name))

        ntext = _read_text(inv) if inv.exists() else None
        if ntext is not None:
            nlines = [ln for ln in ntext.splitlines() if ln.strip()]
            if max(0, len(nlines) - 1) < 1:
                res.findings.append(Finding(CRITICAL, "inventory_no_data",
                                            "InputFilesInventory.csv has no data rows",
                                            file=inv.name))
    else:
        _fail(f"unknown reports section: {section}")

    _finalize(res)
    return res


# --- Fixer gate (replaces fixer-critic) ---------------------------------------

_NOOP_RE = re.compile(r"\.(hint|repartition|coalesce)\s*\(")

# A fixer may record its verdict for an issue back into analysis.json (the
# ``resolution`` field) instead of leaving an inline ``# SCOS:`` comment. A
# recognized resolution satisfies the high-risk coverage check without an
# inline marker, so a legitimately-"safe" finding — e.g. a window function
# whose order is made deterministic by an explicit ``orderBy`` (context the
# static KB cannot capture) — no longer forces a noisy ``# SCOS: ...safe``
# comment or a spurious gap re-dispatch. ``safe`` additionally requires a
# non-empty ``resolution_reason`` so it can't become a silent free pass.
_RESOLVED_VERDICTS = {"fixed", "safe", "todo", "perf"}


# ---------------------------------------------------------------------------
# "fixed-but-unchanged" detection (general, pattern-agnostic anti-gaming check)
# ---------------------------------------------------------------------------
# `resolution` (and a nearby `# SCOS:` marker) are self-reported by the fixer and
# therefore gameable: a run can mark an issue resolution="fixed" WITHOUT actually
# rewriting the incompatible code (observed: a run tagged 92 convertible RDD ops
# "fixed" while 79 live `sc.parallelize(...)` calls remained — green gate, code
# crashes at runtime). This verifies ground truth instead of the label: for any
# issue claimed fixed, the *executable code* at its location must differ from the
# pre-Phase-2 baseline (`phase-1-complete`). Comments and whitespace are stripped,
# so merely adding a `# SCOS:` annotation does not count as a change. This is not
# RDD-specific — it catches "labelled done, edited nothing" for every pattern.
# It is a necessary check, not a sufficient one: it proves an edit happened, not
# that the edit is correct (only running the code — the validate step — proves
# that). Fail-open: if the git baseline is unavailable the check is skipped.

def _strip_comments(text: str) -> str:
    """Blank comment spans in place (line numbering preserved). tokenize
    distinguishes strings from comments, so a `#` inside a literal is kept."""
    try:
        lines = text.splitlines()
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                (srow, scol), (erow, ecol) = tok.start, tok.end
                if srow == erow and 1 <= srow <= len(lines):
                    ln = lines[srow - 1]
                    lines[srow - 1] = ln[:scol] + ln[ecol:]
        return "\n".join(lines)
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        # Fallback: drop whole-line comments only (keeps line count).
        return "\n".join(
            "" if l.lstrip().startswith("#") else l for l in text.splitlines()
        )


def _norm_code(line: str) -> str:
    """Whitespace-insensitive code key (robust to reindent / spacing changes)."""
    return "".join(line.split())


def _norm_code_set(text: str) -> set:
    """Set of normalized executable-code lines (comments + whitespace removed)."""
    return {k for k in (_norm_code(l) for l in _strip_comments(text).splitlines()) if k}


# Constructs that are UNCONDITIONALLY unsupported in Spark Connect — they raise
# at runtime regardless of the receiver's type. Used to keep the
# "fixed-but-unchanged" check precise: an unchanged line can still be *correct*
# (e.g. `.randomSplit`/`.sample`/`.take` are valid on DataFrames once the source
# was converted upstream) or not even be code (a docstring), so we only flag an
# unchanged "fixed" line when it STILL carries one of these hard-fatal tokens.
# These are the RDD/SparkContext names with no DataFrame homonym (mirror of
# recipes/_common.RDD_ONLY_CHAIN_METHODS + the sc.* entry points); ambiguous
# names shared with DataFrame (map/filter/collect/count/union/sample/take/…) are
# deliberately excluded to avoid false positives.
_SC_FATAL_RE = re.compile(
    r"(?<![\w.])sc\.(?:parallelize|range|textFile|wholeTextFiles|emptyRDD|union|"
    r"broadcast|accumulator|binaryFiles|binaryRecords|sequenceFile|objectFile|"
    r"pickleFile|hadoopFile|hadoopRDD|newAPIHadoopFile|newAPIHadoopRDD)\s*\("
    r"|\.sparkContext\b"
    r"|\.(?:glom|cartesian|reduceByKey|reduceByKeyLocally|groupByKey|"
    r"aggregateByKey|foldByKey|combineByKey|sampleByKey|countByKey|countByValue|"
    r"mapValues|flatMapValues|keyBy|zipWithIndex|zipWithUniqueId|sortByKey|"
    r"mapPartitions|mapPartitionsWithIndex|takeSample|takeOrdered|saveAsTextFile|"
    r"saveAsPickleFile|saveAsSequenceFile|saveAsObjectFile|cogroup|groupWith|"
    r"subtractByKey|lookup|collectAsMap|treeAggregate|treeReduce)\s*\("
)


# The ~6 `status_class=Error` KB rules that are BEHAVIORAL, not crash-fatal — the
# `spark.conf.set` / config family. They're Error-severity (the setting is
# silently ignored, which can change results) but the code still *runs*, and a
# config may legitimately be kept as-is, so they must NOT hard-fail the
# "Error-still-live" check. Everything else in the Error tier is a genuine
# unsupported-API/SQL divergence. Kept small and explicit (see kb_rules.json).
_BEHAVIORAL_CONFIG_RE = re.compile(
    r"\.conf\.set\s*\(|\.config\s*\(|SparkConf|setConf|spark\.sql\.extensions"
)


def _baseline_text(migrated_dir: Path, file_path: Path, phase_tag: str):
    """Content of ``file_path`` at ``phase_tag`` via git, or None if unavailable
    (no git repo / tag / tracked path). Read-only counterpart of
    revert_failing_files._git_revert; errors are swallowed (fail-open)."""
    try:
        full = subprocess.run(
            ["git", "ls-files", "--full-name", str(file_path)],
            cwd=str(migrated_dir), check=True, capture_output=True, text=True,
        ).stdout.strip()
        if not full:
            return None
        show = subprocess.run(
            ["git", "show", f"{phase_tag}:{full}"],
            cwd=str(migrated_dir), check=True, capture_output=True, text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return show.stdout


# ---------------------------------------------------------------------------
# Label-independent "unsupported construct still live" scan (ground truth)
# ---------------------------------------------------------------------------
# `resolution` is self-reported and gameable via ANY label — a run can mark an
# unconverted issue todo (22-55-27), fixed (06-16-35), safe, or leave it None
# with only a recipe annotation (07-16-35: live `sc = spark.sparkContext`) and
# still pass a label-based gate. So this check ignores the label entirely and
# inspects the migrated OUTPUT for constructs that are UNCONDITIONALLY
# unsupported in Spark Connect (they raise regardless of data / receiver). It is
# AST-based, so comments, docstrings, and string literals never false-positive,
# and it needs no execution / data / schema / IO — the failure is
# data-independent (missing API surface), so its mere presence proves a crash.


def _kb_gateway_attrs() -> set:
    """Attribute names whose access is unconditionally fatal in Spark Connect,
    sourced from kb_rules.json (kind=python_attribute, status Error) so the set
    tracks the KB instead of drifting as a hand-list — e.g. rdd, sparkContext,
    readStream, writeStream, streams, _metadata. Falls back to the RDD/SC core
    when the KB is unreadable (fail-safe, never empty)."""
    fallback = {"rdd", "sparkContext"}
    try:
        kb = Path(__file__).resolve().parent / "data" / "kb_rules.json"
        raw = json.loads(kb.read_text())
        rules = raw if isinstance(raw, list) else raw.get("rules", [])
        attrs = {
            a for r in rules
            if r.get("kind") == "python_attribute" and r.get("status_class") == "Error"
            for a in (r.get("api") or []) if isinstance(a, str)
        }
        return attrs or fallback
    except Exception:
        return fallback


# RDD/SparkContext methods with NO DataFrame/SparkSession homonym — calling any
# of these is fatal in Spark Connect regardless of receiver, so a live call is a
# guaranteed runtime crash. Mirror of the analyzer's RDD-exclusive surface +
# SparkContext-only readers/entry points. Names shared with DataFrame/Session
# (range/union/sample/take/collect/count/broadcast[=F.broadcast]/map/…) are
# deliberately excluded so valid migrated code never trips the check.
_FATAL_METHODS = frozenset({
    "parallelize", "emptyRDD", "textFile", "wholeTextFiles", "binaryFiles",
    "binaryRecords", "sequenceFile", "objectFile", "pickleFile", "hadoopFile",
    "hadoopRDD", "newAPIHadoopFile", "newAPIHadoopRDD", "accumulator",
    "glom", "cartesian", "reduceByKey", "reduceByKeyLocally", "groupByKey",
    "aggregateByKey", "foldByKey", "combineByKey", "sampleByKey", "countByKey",
    "countByValue", "mapValues", "flatMapValues", "keyBy", "zipWithIndex",
    "zipWithUniqueId", "sortByKey", "mapPartitions", "mapPartitionsWithIndex",
    "takeSample", "takeOrdered", "saveAsTextFile", "saveAsPickleFile",
    "saveAsSequenceFile", "saveAsObjectFile", "cogroup", "groupWith",
    "subtractByKey", "lookup", "collectAsMap", "treeAggregate", "treeReduce",
})


def _scan_unsupported_constructs(text: str, gateway_attrs: set):
    """AST scan of ``text`` for unconditionally-unsupported Spark Connect
    constructs. Returns a sorted list of ``(lineno, label)``, one per line.
    AST-based: comments / docstrings / string contents never match. Returns []
    when the source does not parse (the compile gate owns syntax errors)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    hits: dict = {}

    class _V(ast.NodeVisitor):
        def visit_Call(self, node):  # noqa: N802
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in _FATAL_METHODS:
                hits.setdefault(node.lineno, f".{fn.attr}()")
            self.generic_visit(node)

        def visit_Attribute(self, node):  # noqa: N802
            if node.attr in gateway_attrs:
                hits.setdefault(node.lineno, f".{node.attr}")
            self.generic_visit(node)

    _V().visit(tree)
    return sorted(hits.items())


def _revert_to_baseline(migrated_dir: Path, py_file: Path, phase_tag: str) -> bool:
    """Revert ``py_file`` to ``phase_tag`` and confirm the restored source
    compiles. Returns True only when both succeed — otherwise the caller keeps
    the blocking syntax finding so broken code is never silently accepted.
    """
    if not _REVERT_HELPERS:
        return False
    if not _git_revert(migrated_dir, py_file, phase_tag):
        return False
    try:
        py_compile.compile(str(py_file), doraise=True)
    except (py_compile.PyCompileError, OSError, ValueError):
        return False
    return True


def _iter_sql_texts(
    migrated_dir: Path, manifest: list[str], state: dict
) -> Iterator[tuple[str, str]]:
    """Yield ``(display, sql_text)`` for every SQL the mechanical-rewrite check
    must verify: each standalone ``.sql`` file (excluding Databricks native-JSON
    ``.sql`` notebooks) and each *static* ``spark.sql("...")`` string in a ``.py``
    file. Dynamic (f-string / concatenated / variable) SQL is not yielded — it
    cannot be statically rewritten and so is out of scope for this check."""
    # Standalone .sql files.
    if _NOTEBOOK_IO:
        for dirpath, _dirs, files in notebook_io.walk_filtered(str(migrated_dir)):
            for fname in files:
                if not fname.lower().endswith(".sql"):
                    continue
                p = Path(dirpath) / fname
                if notebook_io.is_notebook(str(p)):
                    continue
                t = _read_text(p)
                if t is not None:
                    yield (p.name, t)

    # Embedded static spark.sql("...") strings in .py files.
    import ast as _ast
    for p in _gather_files(migrated_dir, manifest, ".py"):
        t = _read_text(p)
        if t is None:
            continue
        try:
            tree = _ast.parse(t)
        except SyntaxError:
            continue
        for node in _ast.walk(tree):
            if (
                isinstance(node, _ast.Call)
                and isinstance(node.func, _ast.Attribute)
                and node.func.attr == "sql"
                and node.args
                and isinstance(node.args[0], _ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                yield (f"{p.name}:{getattr(node, 'lineno', '?')}", node.args[0].value)


def _preexisting_unfixed_files(state: dict) -> set[str]:
    """Basenames of source files carrying an *unfixed* pre-existing syntax
    error, as recorded by the Phase 0.5 pre-flight (``precompile_check.py``).

    A compile failure in one of these files is a customer-source problem the
    pre-flight could not safely auto-repair — it must NOT be attributed to the
    fixer (which never touched it) nor hard-block the migration. The fixer gate
    downgrades such failures from a blocking CRITICAL to an advisory
    ``preexisting_syntax`` WARN.
    """
    out: set[str] = set()
    for e in state.get("preexisting_syntax", []) or []:
        if not e.get("auto_fixed") and e.get("file"):
            out.add(os.path.basename(e["file"]))
    return out


def run_fixer_gate(
    state_path: Path,
    *,
    revert_failing: bool = False,
    phase_tag: str = "phase-1-complete",
) -> GateResult:
    """Phase 2 fix-application quality gate.

    Read-only by default (a pure critic). When ``revert_failing`` is True it
    additionally acts as the final compilation safety net: any ``.py`` that
    fails to compile is reverted to its pre-Phase-2 baseline (``phase_tag``)
    via git, so broken syntax can never ship. A reverted file is reported as an
    advisory ``fix_reverted`` WARN (not a blocking ``syntax_error``) because the
    restored original is, by definition, valid pre-migration code. This folds
    the former standalone Phase 2b (revert_failing_files.py) into the gate so
    there is a single post-loop compilation gate.
    """
    state = load_state(state_path)
    analysis_path, migrated_dir, manifest = resolve_paths(state, state_path)
    res = GateResult(gate="fixer", verdict="PASS")
    preexisting_unfixed = _preexisting_unfixed_files(state)

    # 0. Orchestration enforcement. For any multi-file workload the coordinator
    # MUST route Phase 2 through orchestrate_phases.py, which writes the worker-
    # pool plan (`max_parallel_fixers` + `phase2_chunks`) to state. If those are
    # absent the coordinator improvised an inline single-agent fix and bypassed
    # the parallel fixer pool entirely — fail so it re-runs the orchestrator.
    code_manifest = [r for r in manifest if r.endswith((".py", ".ipynb", ".python"))]
    if len(code_manifest) >= 2:
        if "max_parallel_fixers" not in state or "phase2_chunks" not in state:
            res.findings.append(Finding(
                CRITICAL, "phase2_not_orchestrated",
                f"multi-file workload ({len(code_manifest)} files) but "
                "migration_state.json has no orchestrator plan "
                "(max_parallel_fixers / phase2_chunks) — run "
                "scripts/orchestrate_phases.py and dispatch the printed waves "
                "instead of fixing inline"))

    py_files = _gather_files(migrated_dir, manifest, ".py")

    # 1. Syntax: every .py compiles (zero tolerance).
    for p in py_files:
        try:
            py_compile.compile(str(p), doraise=True)
        except (py_compile.PyCompileError, OSError, ValueError) as e:
            code = "syntax_error" if isinstance(e, py_compile.PyCompileError) else "compile_error"
            msg = (f"py_compile failed: {e.msg}" if isinstance(e, py_compile.PyCompileError)
                   else f"could not compile: {e}")
            if p.name in preexisting_unfixed:
                res.findings.append(Finding(WARN, "preexisting_syntax",
                                            "pre-existing source syntax error the Phase 0.5 "
                                            "pre-flight could not auto-fix (not fixer-caused; "
                                            f"needs manual source correction): {msg}", file=p.name))
            elif revert_failing and _revert_to_baseline(migrated_dir, p, phase_tag):
                res.reverted.append(p.name)
                res.findings.append(Finding(WARN, "fix_reverted",
                                            "fix would not compile — file reverted to its "
                                            f"pre-Phase-2 baseline ({phase_tag})", file=p.name))
            else:
                res.findings.append(Finding(CRITICAL, code, msg, file=p.name))
        # 2. Empty files.
        try:
            if p.stat().st_size == 0:
                res.findings.append(Finding(CRITICAL, "empty_file",
                                            "migrated .py file is empty (0 bytes)", file=p.name))
        except OSError:
            pass

    # When we repaired files, clear the __pycache__ that py_compile created so a
    # re-run sees the reverted source, not a stale cached bytecode.
    if revert_failing and res.reverted and _REVERT_HELPERS:
        _purge_pycache(migrated_dir)

    # 1a. Notebook JSON validity (.ipynb structural shape — cells/cell_type/source).
    for nb in _gather_files(migrated_dir, manifest, ".ipynb"):
        ntext = _read_text(nb)
        if ntext is None:
            continue
        try:
            obj = json.loads(ntext)
            if not isinstance(obj, dict) or "cells" not in obj:
                raise ValueError("missing 'cells' key")
            for cell in obj["cells"]:
                if not isinstance(cell, dict) or "cell_type" not in cell or "source" not in cell:
                    raise ValueError("bad cell structure")
        except (json.JSONDecodeError, ValueError) as e:
            res.findings.append(Finding(CRITICAL, "invalid_notebook",
                                        f"invalid notebook JSON: {e}", file=nb.name))

    # 1b. Notebook cell syntax: each python code cell must compile.
    # py_compile operates on files, so we use builtin compile() on each cell's
    # source. The cell label encodes file + cell index for diagnostics.
    nb_paths_seen: set[str] = set()
    for u in _iter_python_code_units(migrated_dir, manifest, state):
        if not u.is_notebook:
            continue  # already covered by py_compile loop above
        nb_paths_seen.add(str(u.path.resolve()) if u.path.exists() else str(u.path))
        try:
            compile(u.text, u.display, "exec")
        except SyntaxError as e:
            if u.path.name in preexisting_unfixed:
                res.findings.append(Finding(WARN, "preexisting_syntax",
                                            "pre-existing notebook-cell syntax error the Phase 0.5 "
                                            "pre-flight could not auto-fix (not fixer-caused; "
                                            f"needs manual source correction): {e.msg} "
                                            f"(line {e.lineno})",
                                            file=u.display, line=e.lineno))
            else:
                res.findings.append(Finding(CRITICAL, "notebook_cell_syntax_error",
                                            f"notebook cell failed to compile: {e.msg} "
                                            f"(line {e.lineno})",
                                            file=u.display, line=e.lineno))

    # 3. File integrity: every manifest .py / .ipynb / .python exists.
    for rel in manifest:
        if not rel.endswith((".py", ".ipynb", ".python")):
            continue
        if not (migrated_dir / rel).exists():
            res.findings.append(Finding(CRITICAL, "manifest_file_missing",
                                        "manifest source file absent from Output/", file=rel))

    # 4. High-risk issue coverage + recipe_adjacent annotation (needs analysis.json).
    # Apply to BOTH .py files and python notebook files. For notebooks we
    # use the concatenated source so line numbers track the analyzer's
    # perspective on the flattened file (best-effort; the analyzer itself
    # records line ranges relative to file basename).
    entries = _load_entries(analysis_path)
    if entries is not None:
        lines_by_base: dict[str, list[str] | None] = {}
        # For the "fixed-but-unchanged" check (4c): normalized code set of the
        # OUTPUT file, and the comment-stripped lines of the pre-Phase-2 baseline
        # (phase_tag). baseline stays None when git/tag is unavailable -> 4c skips.
        out_codeset_by_base: dict[str, set] = {}
        baseline_stripped_by_base: dict[str, list | None] = {}
        for u in _iter_python_file_units(migrated_dir, manifest, state):
            lines_by_base[u.path.name] = u.text.splitlines()
            out_codeset_by_base[u.path.name] = _norm_code_set(u.text)
            _bt = _baseline_text(migrated_dir, u.path, phase_tag)
            baseline_stripped_by_base[u.path.name] = (
                _strip_comments(_bt).splitlines() if _bt is not None else None
            )
        for e in entries:
            if not isinstance(e, dict):
                continue
            rng = _parse_lines(e.get("lines"))
            f = e.get("file")
            if rng is None or not isinstance(f, str):
                continue
            lines = lines_by_base.get(os.path.basename(f))
            if lines is None:
                continue  # file absent -> covered by file-integrity / coverage gates
            risk = _coerce_risk(e.get("final_risk")) or 0.0
            resolution = (e.get("resolution") or "").strip().lower()
            reason = (e.get("resolution_reason") or "").strip()
            has_marker = _marker_near(lines, rng[0], rng[1])
            if risk >= 0.7:
                if resolution == "safe" and not reason:
                    res.findings.append(Finding(CRITICAL, "safe_without_reason",
                                                f"high-risk issue (final_risk={risk:.2f}) marked "
                                                "resolution='safe' without a resolution_reason",
                                                file=os.path.basename(f), line=rng[0]))
                elif not has_marker and resolution not in _RESOLVED_VERDICTS:
                    res.findings.append(Finding(CRITICAL, "high_risk_unmarked",
                                                f"high-risk issue (final_risk={risk:.2f}) has no fix, "
                                                "# SCOS marker, or analysis.json resolution",
                                                file=os.path.basename(f), line=rng[0]))
            # ---- KB-status-driven tiering (family-agnostic, not RDD-only) ----
            # The two detection paths populate DIFFERENT fields, so read both:
            #   * RDD-detector issues carry `rdd_class` (convertible/mixed/
            #     no_equivalent), status_class is None.
            #   * KB-trigger issues (Delta, dbutils, streaming, SQL, config, ...)
            #     carry `status_class` from kb_rules.json: Fixed (a rewrite
            #     exists) / Error (must-fix, no auto-fix) / Warning (behavioral,
            #     advisory) / IO (repoint).
            rdd_class = (e.get("rdd_class") or "").strip().lower()
            status_class = (e.get("status_class") or "").strip().lower()
            is_convertible = rdd_class in ("convertible", "mixed") or status_class == "fixed"

            # Compute region liveness once (does the flagged code survive
            # unchanged into the output?). Reused by the fixed/unchanged and the
            # Error-still-live checks. Skipped when the git baseline is absent.
            base = os.path.basename(f)
            bl = baseline_stripped_by_base.get(base)
            outset = out_codeset_by_base.get(base)
            region = bl[rng[0] - 1:rng[1]] if bl is not None else []
            region_code = {k for k in (_norm_code(l) for l in region) if k}
            region_live = bool(region_code) and outset is not None and region_code <= outset

            # 4b. Convertible op MUST be converted, not punted to TODO. Applies to
            # any family with a known rewrite — RDD convertible/mixed, OR a KB rule
            # whose status_class is `Fixed`. `no_equivalent` / genuine `Error`
            # (no rewrite) are exempt: TODO + comment-out is correct for them.
            if is_convertible and resolution == "todo":
                src = "RDD" if rdd_class in ("convertible", "mixed") else "KB(status=Fixed)"
                res.findings.append(Finding(
                    CRITICAL, "convertible_not_converted",
                    f"convertible op ({src}) left as resolution='todo'; a known "
                    "DataFrame/Snowpark rewrite exists, so it must be applied "
                    "(see references/), not annotated as a TODO.",
                    file=base, line=rng[0]))

            # 4c. "fixed" must mean the code actually changed. resolution="fixed"
            # but the flagged code survives unchanged in the output = a label
            # without an edit. Fire only when the unchanged region still carries a
            # genuine incompatibility — a hard-fatal RDD/SC construct, OR an
            # RDD-classified op, OR a KB `Error`-tier issue (any family). This
            # "still incompatible" guard removes false positives on unchanged
            # lines that are legitimately fine (valid-on-DataFrame ops, docstrings).
            if resolution == "fixed" and region_live:
                # "still incompatible" must be a CONCRETE, re-detected signal in
                # the surviving region — a hard-fatal RDD/SC construct. We do NOT
                # broaden this to `status_class==error` alone: an unchanged Error
                # line can be legitimately fine now (the fix was applied upstream,
                # e.g. `data.randomSplit(...)` once `data` became a DataFrame), so
                # "unchanged + Error" over-fires. The Error family's "labelled but
                # not done" case is instead caught FP-safely by 4e, which also
                # requires the issue to be unmarked AND unresolved. Generalizing
                # the fixed-lie beyond RDD needs true re-detection (replay the KB
                # matcher on the region) — a deliberate follow-up, not this rule.
                if any(_SC_FATAL_RE.search(l) for l in region):
                    res.findings.append(Finding(
                        CRITICAL, "fixed_but_unchanged",
                        "issue marked resolution='fixed' but its code is unchanged "
                        f"from the pre-Phase-2 baseline ({phase_tag}) and still "
                        "carries the flagged incompatibility — labelled but never "
                        "applied. Re-dispatch to actually rewrite it (or comment "
                        "out + TODO if genuinely unconvertible).",
                        file=base, line=rng[0]))

            # 4e. KB `Error`-tier incompatibility left LIVE and unhandled. An Error
            # rule (unsupported API/SQL in any family — Delta, dbutils, streaming,
            # ...) whose code survives unchanged in the output, is not resolved
            # (fixed/safe), and carries no `# SCOS` marker = the fixer neither
            # converted nor honestly flagged it. The behavioral config family
            # (spark.conf.set, ~6 rules) is excluded — those run and may be kept.
            if (status_class == "error" and region_live and not has_marker
                    and resolution not in ("fixed", "safe")
                    and not any(_BEHAVIORAL_CONFIG_RE.search(l) for l in region)):
                res.findings.append(Finding(
                    CRITICAL, "mustfix_error_unresolved",
                    f"Error-tier incompatibility (ewi={e.get('ewi_code')}) is still "
                    "live in the output, unresolved, and unmarked — convert it, or "
                    "comment it out + TODO. It will not work as-is in Spark Connect.",
                    file=base, line=rng[0]))

            if e.get("kind") == "recipe_adjacent" and not _marker_near(lines, rng[0], rng[1]):
                res.findings.append(Finding(WARN, "recipe_adjacent_unmarked",
                                            "recipe_adjacent issue not annotated "
                                            "(recipe-coverage gap mining relies on it)",
                                            file=os.path.basename(f), line=rng[0]))

        # 4f. Zero-effort detection (from main): if the fixer reported
        # issues_fixed=0 but analysis.json has actionable issues
        # (kind=standard/recipe_incomplete, not resolved), the fixer didn't do
        # its job — fail so it re-runs. Inside `if entries is not None`.
        fixes_info = state.get("phases_completed", {}).get("2_fixes", {})
        issues_fixed = fixes_info.get("issues_fixed", -1)
        if issues_fixed == 0:
            actionable = sum(
                1 for e in entries
                if isinstance(e, dict)
                and e.get("kind") in ("standard", "recipe_incomplete")
                and (e.get("resolution") or "").strip().lower() not in ("safe", "fixed", "perf")
            )
            if actionable > 0:
                res.findings.append(Finding(
                    CRITICAL, "phase2_no_effort",
                    f"fixer reported issues_fixed=0 but {actionable} actionable "
                    "issue(s) remain (kind=standard/recipe_incomplete, not resolved). "
                    "Re-dispatch the fixer agent on these files."))

    # 4d. Label-independent ground-truth scan. Regardless of what any issue's
    # `resolution` claims (fixed / safe / todo / None), the migrated OUTPUT must
    # not still contain a construct that is unconditionally unsupported in Spark
    # Connect. This is the un-gameable backstop: it reads the code that will run,
    # not the self-reported label, so it catches a live `sc = spark.sparkContext`
    # / `sc.parallelize(...)` / `df.rdd` / `.glom()` even when the fixer marked
    # the issue done (or a recipe only annotated it). Gateways are KB-driven
    # (kind=python_attribute, Error); methods are the no-homonym RDD/SC set.
    # Runs regardless of analysis.json presence (outside `if entries is not None`).
    _gateway_attrs = _kb_gateway_attrs()
    for u in _iter_python_file_units(migrated_dir, manifest, state):
        for lineno, label in _scan_unsupported_constructs(u.text, _gateway_attrs):
            res.findings.append(Finding(
                CRITICAL, "unsupported_construct_live",
                f"'{label}' is unconditionally unsupported in Spark Connect but "
                "is still live in the migrated output — it raises at runtime "
                "regardless of the issue's resolution label. Convert it to the "
                "DataFrame/Snowpark equivalent, or comment it out + TODO if it "
                "has no equivalent.",
                file=u.display, line=lineno))

    # 5. No-op over-annotation (advisory) — also scan notebook cells.
    for u in _iter_python_file_units(migrated_dir, manifest, state):
        for lineno, raw_line in enumerate(u.text.splitlines(), start=1):
            if _NOOP_RE.search(raw_line) and "# SCOS" in raw_line and ".rdd" not in raw_line:
                res.findings.append(Finding(WARN, "noop_over_annotation",
                                            "no-op DataFrame method (hint/repartition/coalesce) "
                                            "carries a # SCOS annotation",
                                            file=u.display, line=lineno))

    # 6. Mechanical SQL gaps must be REWRITTEN, not merely annotated. The
    # deterministic rewriter has a safe, semantics-preserving fix for these, so a
    # comment is not a resolution. We decide this by running the rewriter itself
    # over the FINAL SQL: if ``rewrite_sql`` would STILL change it, a safe
    # mechanical fix was available and was not applied (Phase 0.6 skipped/reverted,
    # or the fixer only annotated) — fail so it is actually rewritten. Using the
    # rewriter (not ``analyze_sql`` + a rule-id allowlist) is what keeps this
    # gate satisfiable: shapes the rewriter conservatively DECLINES (e.g. a
    # multi-expression ROLLUP it can't fold) report ``changed=False`` and so are
    # NOT demanded here — they fall to the judgment/annotate path instead.
    # Judgment-heavy gaps (window-missing-ORDER-BY, multi-column NOT IN, …) have
    # no safe syntactic fix, are not in _TRANSFORMS, and so never trip this check.
    try:
        from rag.sql_rewrite import rewrite_sql as _rewrite_sql
        _sql_check = True
    except Exception as _e:  # noqa: BLE001 — rag unavailable in a stripped env; skip
        # Surface WHY the mechanical-SQL check is inert (commonly: the rag
        # package eagerly imports the Snowflake SDK, absent in stripped/test
        # envs) so a silent skip is not mistaken for "no mechanical gaps".
        print(f"scos_gates: SQL mechanical-rewrite check skipped — could not import "
              f"rag.sql_rewrite ({type(_e).__name__}: {_e})", file=sys.stderr)
        _sql_check = False
    if _sql_check:
        for display, sql_text in _iter_sql_texts(migrated_dir, manifest, state):
            try:
                rw = _rewrite_sql(sql_text, dialect="spark")
            except Exception:  # noqa: BLE001
                continue
            if not rw.parsed or not rw.changed:
                # Unparseable, already-clean, or the rewriter declines this shape
                # → nothing safe to force here.
                continue
            seen_rules: set[str] = set()
            for edit in rw.applied:
                if edit.rule_id not in seen_rules:
                    seen_rules.add(edit.rule_id)
                    res.findings.append(Finding(
                        CRITICAL, "sql_mechanical_not_rewritten",
                        f"mechanical SQL gap '{edit.rule_id}' remains un-rewritten "
                        "(an annotation is not a fix — re-run the SQL rewriter / "
                        "Phase 0.6 rewrite_sql_files.py)",
                        file=display, line=edit.line))

    _finalize(res)
    return res

def print_human(res: GateResult) -> None:
    crit = [f for f in res.findings if f.severity == CRITICAL]
    warn = [f for f in res.findings if f.severity == WARN]
    print(f"=== SCOS GATE: {res.gate} ===")
    print(f"VERDICT: {res.verdict}")
    print(f"  critical: {len(crit)}   advisory: {len(warn)}")
    if res.reverted:
        print(f"  reverted to baseline: {len(res.reverted)} ({', '.join(res.reverted)})")
    for f in crit:
        loc = f" [{f.file}:{f.line}]" if f.file else ""
        print(f"  CRITICAL {f.code}{loc}: {f.message}")
    for f in warn:
        loc = f" [{f.file}:{f.line}]" if f.file else ""
        print(f"  WARN     {f.code}{loc}: {f.message}")
    if res.verdict == "FAIL":
        print("\nGAPS (critical findings to fix, then re-run the gate):")
        for f in crit:
            loc = f" {f.file}:{f.line}" if f.file else ""
            print(f"  - {f.code}{loc}: {f.message}")
    print(f"\n{'PASS' if res.exit_code == 0 else 'FAIL'}: {res.gate} gate")


def print_json(res: GateResult) -> None:
    payload = {
        "gate": res.gate,
        "verdict": res.verdict,
        "exit_code": res.exit_code,
        "findings": [asdict(f) for f in res.findings],
        "gaps": [asdict(f) for f in res.findings if f.severity == CRITICAL],
        "reverted": res.reverted,
        "reverted_count": len(res.reverted),
    }
    print(json.dumps(payload, indent=2))


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--state", required=True, type=Path, help="Path to migration_state.json")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic SCOS migration phase gates")
    sub = parser.add_subparsers(dest="gate", required=True)

    _add_common(sub.add_parser("analyzer", help="Phase 1 analysis quality gate"))
    _add_common(sub.add_parser("imports", help="Phase 3 imports/headers quality gate"))
    p_rep = sub.add_parser("reports", help="Phase 1.2 / Phase 4 reporting quality gate")
    _add_common(p_rep)
    p_rep.add_argument("--section", required=True, choices=("assessment", "csvs"),
                       help="assessment = Phase 1.2 HTML+IR; csvs = Phase 4 dashboard CSVs")
    p_fix = sub.add_parser("fixer", help="Phase 2 fix-application quality gate")
    _add_common(p_fix)
    p_fix.add_argument("--revert-failing", action="store_true",
                       help="Final safety net: revert any .py that does not compile to its "
                            "pre-Phase-2 baseline (--phase-tag) instead of failing the gate. "
                            "Off by default (the gate is read-only).")
    p_fix.add_argument("--phase-tag", default="phase-1-complete",
                       help="Git ref to revert failing files to when --revert-failing is set "
                            "(default: phase-1-complete).")

    args = parser.parse_args(argv)

    if args.gate == "analyzer":
        res = run_analyzer_gate(args.state)
    elif args.gate == "imports":
        res = run_imports_gate(args.state)
    elif args.gate == "reports":
        res = run_reports_gate(args.state, args.section)
    elif args.gate == "fixer":
        res = run_fixer_gate(args.state, revert_failing=args.revert_failing,
                             phase_tag=args.phase_tag)
    else:
        parser.error(f"unknown gate: {args.gate}")
        return EXIT_USAGE

    (print_json if args.json else print_human)(res)
    return res.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
