#!/usr/bin/env python3
"""scos_state.py — Java validator state machine + CLI.

This is the Snowflake-FREE spine of the Java validator's state machine (forked
from the Scala validator's scos_state.py). Java workloads are built with
Maven/Gradle (no sbt for the workload itself). The test kit is still the Scala
harness (ScalaTest + ReflectionEntrypoint) staged from the sibling Scala skill.
On-disk schemas (`state.json`, `events.jsonl`, `run_index.json`) are byte-for-byte
shared with the Scala/Python validators — field names MUST match.

Scope of this module: the full Snowflake-FREE CLI surface —
init, select-entrypoints, status, summary (the exit-4 output gate), build-index
(run_index.json), document-divergence, migrate-divergences, put-schemas, commit
(git), and the record-* / mark-* family — plus the pure state machine
(advance_phase, the record-trial-status hard gate, comparison_verdict,
manual-review / recovery). The only subcommands still owned by the JVM control
jar are the Snowflake-touching ones (provision, cleanup, snapshot-stage,
check-connection), which need a live connection and are not portable here.

Key Java-specific adaptations vs the Scala fork:
  - _ALIGN_CODE_EXTS: .java (not .scala/.sc)
  - _ALIGN_SKIP_DIRS: Maven/Gradle build output dirs (not sbt project/target)
  - Workload build: mvn/gradle only (no sbt assembly for the workload)
  - Kit staging: from sibling validate-spark-scala-to-snowpark-connect/harness-scala/kit
  - Analyze jar: harness-java/control/target/scos-analyze-java.jar
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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import fcntl as _fcntl  # POSIX only
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

SCHEMA_VERSION = 1
VALIDATION_DIRNAME = "Validation"

CANONICAL_MILESTONES = {
    "analyzer_survey", "entrypoints_selected", "analyzer_deep",
    "patches_authored", "workload_built", "tests_authored",
    "venv_prewarmed", "snowflake_provisioned",
    # Flipped by advance_phase (not by `record-milestone`), but they MUST be
    # listed here: record-milestone _die()s on an unknown name, so an agent
    # recording them explicitly would hard-fail.
    "phase_a_complete", "phase_b_complete",
}

TRIAL_STATUSES = {
    "pending", "passed", "passed_no_baseline", "hard_stuck", "phase_a_skipped",
}

TERMINAL_TRIAL_STATUSES = {
    "passed", "passed_no_baseline", "hard_stuck", "phase_a_skipped",
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def validation_root(conv_root: Path) -> Path:
    return conv_root / VALIDATION_DIRNAME


def state_path(conv_root: Path) -> Path:
    return validation_root(conv_root) / "state.json"


def analysis_path(conv_root: Path) -> Path:
    return validation_root(conv_root) / "shared" / "analysis.json"


def ast_facts_path(conv_root: Path) -> Path:
    return validation_root(conv_root) / "shared" / "ast_facts.json"


def _load_json_optional(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _normalize_source_rel(path: str) -> str:
    return str(path or "").strip().lstrip("./").replace("\\", "/")


def _entrypoint_source_rels(ep: dict) -> List[str]:
    """Relative source paths declared on an analysis entrypoint."""
    out: List[str] = []
    for key in ("path", "source_path", "file", "source_file"):
        v = ep.get(key)
        if isinstance(v, str) and v.strip():
            out.append(_normalize_source_rel(v))
    for item in ep.get("files") or []:
        if isinstance(item, str) and item.strip():
            out.append(_normalize_source_rel(item))
        elif isinstance(item, dict):
            for key in ("path", "source_path", "file"):
                v = item.get(key)
                if isinstance(v, str) and v.strip():
                    out.append(_normalize_source_rel(v))
    # De-dupe preserving order
    seen: set = set()
    uniq: List[str] = []
    for rel in out:
        if rel and rel not in seen:
            seen.add(rel)
            uniq.append(rel)
    return uniq


def entrypoint_declared_sinks(ep: dict) -> list:
    """All sinks declared on an entrypoint, merging BOTH schema keys.

    The deterministic AST bridge (``ast_to_analysis.py``) writes ``ep["sinks"]``;
    a future LLM data-synthesizer would write ``ep["external_sinks"]``. Merge
    both here (parity with the Scala/PySpark validators) so no sink-consuming
    gate silently drops a workload that used the other key.
    """
    if not isinstance(ep, dict):
        return []
    merged: list = []
    for key in ("sinks", "external_sinks"):
        v = ep.get(key)
        if isinstance(v, list):
            merged.extend(v)
    return merged


def entrypoint_declares_sinks_key(ep: dict) -> bool:
    """True when the entrypoint carries either sink key (even if empty list)."""
    return isinstance(ep, dict) and ("sinks" in ep or "external_sinks" in ep)


def _ast_file_rel(file_facts: dict) -> str:
    return _normalize_source_rel(
        file_facts.get("path")
        or file_facts.get("relative_path")
        or file_facts.get("file")
        or ""
    )


def _ast_paths_match(ast_rel: str, ep_rel: str) -> bool:
    if not ast_rel or not ep_rel:
        return False
    if ast_rel == ep_rel or ast_rel.endswith("/" + ep_rel) or ep_rel.endswith("/" + ast_rel):
        return True
    # Match on basename when one side is only a class file name.
    return Path(ast_rel).name == Path(ep_rel).name and (
        Path(ep_rel).suffix == ".java" or Path(ast_rel).suffix == ".java"
    )


def entrypoint_ast_write_evidence(
    ast_facts: Optional[dict], ep: dict,
) -> List[str]:
    """Return evidence strings when AST facts show writes for this entrypoint.

    Used to refuse a synthetic no-sink verdict when ``analysis.json`` has
    ``sinks: []`` but the JavaParser AST still saw writes / write_helpers —
    that is an analysis mining gap, not a true no-sink trial.
    """
    if not isinstance(ast_facts, dict):
        return []
    targets = _entrypoint_source_rels(ep)
    if not targets:
        return []
    evidence: List[str] = []
    for f in ast_facts.get("files") or []:
        if not isinstance(f, dict):
            continue
        rel = _ast_file_rel(f)
        if not any(_ast_paths_match(rel, t) for t in targets):
            continue
        n_writes = len(f.get("writes") or [])
        n_unresolved = len(f.get("unresolved_writes") or [])
        n_helpers = len(f.get("write_helpers") or [])
        if n_writes or n_unresolved or n_helpers:
            evidence.append(
                f"{rel or '<unknown>'}: writes={n_writes} "
                f"unresolved_writes={n_unresolved} write_helpers={n_helpers}"
            )
    return evidence


# ---------------------------------------------------------------------------
# Primitive helpers (port of Json.scala)
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path, required: bool = False) -> dict:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"required file not found: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"cannot parse {path}: {e}") from e


def write_atomic(path: Path, obj: Any) -> None:
    """Write JSON atomically via tmp + rename, with a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".validate_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_event(validation_root_dir: Path, event: dict) -> None:
    """Append a ts-stamped JSON line to events.jsonl.

    Uses an exclusive file lock (POSIX only) so concurrent runner processes do
    not interleave or tear the JSONL lines written by each other.
    """
    validation_root_dir.mkdir(parents=True, exist_ok=True)
    enriched = {"ts": now_iso(), **event}
    line = json.dumps(enriched) + "\n"
    with (validation_root_dir / "events.jsonl").open("a", encoding="utf-8") as f:
        if _HAS_FCNTL:
            _fcntl.flock(f, _fcntl.LOCK_EX)
        try:
            f.write(line)
        finally:
            if _HAS_FCNTL:
                _fcntl.flock(f, _fcntl.LOCK_UN)


def load_state(conv_root: Path) -> dict:
    state = load_json(state_path(conv_root), required=True)
    ver = state.get("schema_version", -1)
    if ver != SCHEMA_VERSION:
        raise ValueError(f"state.json schema_version mismatch (expected {SCHEMA_VERSION}, got {ver})")
    return state


def save_state(conv_root: Path, state: dict) -> None:
    write_atomic(state_path(conv_root), state)


def load_analysis(conv_root: Path) -> dict:
    return load_json(analysis_path(conv_root), required=True)


def save_analysis(conv_root: Path, analysis: dict) -> None:
    write_atomic(analysis_path(conv_root), analysis)


def run_id() -> str:
    """8 hex chars, like Python uuid4().hex[:8] / Scala Json.runId."""
    return uuid.uuid4().hex[:8]


def project_slug(name: str) -> str:
    """Snowflake-safe slug: lowercase alnum+underscore, cannot start with a digit."""
    base = re.sub(r"[^a-z0-9_]+", "_", (name or "").lower()).strip("_")
    safe = base or "project"
    return f"p_{safe}" if safe[0].isdigit() else safe


def normalize_sink_name(raw: str) -> str:
    text = (raw or "").replace("`", "").replace('"', "").strip()
    if not text:
        return ""
    if "://" in text or text.startswith("/"):
        return re.sub(r"\.[^.]+$", "", Path(text).name)
    parts = [p for p in text.split(".") if p]
    return parts[-1] if parts else re.sub(r"\.[^.]+$", "", Path(text).name)


def ensure_entrypoints_list(analysis: dict) -> List[dict]:
    """Coerce analysis['entrypoints'] (list or dict) to a list of dicts."""
    eps = analysis.get("entrypoints", {})
    if isinstance(eps, list):
        return [e for e in eps if isinstance(e, dict)]
    if isinstance(eps, dict):
        out = []
        for k, v in eps.items():
            out.append({"id": k, **v} if isinstance(v, dict) else {"id": k})
        return out
    return []


def _list_files(d: Path) -> List[Path]:
    """All regular files under d (recursive), sorted by path string."""
    if not d.is_dir():
        return []
    return sorted((p for p in d.rglob("*") if p.is_file()), key=lambda p: str(p))


def _copy_dir(src: Path, dst: Path) -> None:
    # Guard against infinite recursion when dst lives inside src (e.g. workspace_scos/
    # is a subdirectory of the workspace root that is also the original-source).
    try:
        rel = dst.relative_to(src)
        exclude_name = rel.parts[0]
        shutil.copytree(src, dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(exclude_name))
    except ValueError:
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=str(cwd), capture_output=True, text=True)


# Commit prefixes mirror the PySpark validator. [TEST-PATCH] commits (harness I/O
# rewrites) live only on the validation branch and are NEVER cherry-picked onto
# the deliverable; [MIGRATION-FIX] commits (real SCOS fixes) are cherry-picked at
# harvest.
COMMIT_PREFIXES = {"test-patch": "[TEST-PATCH]", "migration-fix": "[MIGRATION-FIX]"}

# Validation-harness identifiers that must never reach the deliverable Output/
# via a cherry-picked [MIGRATION-FIX] (they belong in [TEST-PATCH] patches).
_SCOS_LEAK_RE = re.compile(r"SCOS_[A-Z0-9_]+")

# JVM build output + caches must never enter the conv-root git history: the
# Output/ and Validation/ stages would otherwise capture compiled classes/jars.
_GITIGNORE_PATTERNS = ["target/", "*.class", "__pycache__/", "*.py[cod]", ".pytest_cache/"]


def _current_branch(conv_root: Path) -> Optional[str]:
    res = _run_git(conv_root, "git", "rev-parse", "--abbrev-ref", "HEAD")
    if res.returncode != 0:
        return None
    name = res.stdout.strip()
    return name or None


def _ensure_gitignore(conv_root: Path) -> None:
    """Ensure conv_root/.gitignore lists the build/cache patterns. git honours an
    untracked .gitignore, so writing the file is enough to keep build output out
    of [TEST-PATCH] / [MIGRATION-FIX] commits."""
    gi = conv_root / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
    have = {ln.strip() for ln in existing.splitlines()}
    missing = [p for p in _GITIGNORE_PATTERNS if p not in have]
    if not missing:
        return
    block = "\n".join(missing) + "\n"
    if existing and not existing.endswith("\n"):
        existing += "\n"
    gi.write_text(existing + block, encoding="utf-8")


def _git_commit_tree(conv_root: Path, tree_path: str, message: str) -> Optional[str]:
    """Stage *tree_path* (relative to conv_root) and commit. Returns the new SHA,
    or None when nothing was staged. Dies on git failure."""
    if _run_git(conv_root, "git", "add", tree_path).returncode != 0:
        sys.exit(_die("git add failed", 1))
    if _run_git(conv_root, "git", "diff", "--cached", "--quiet").returncode == 0:
        return None
    if _run_git(conv_root, "git", "commit", "-m", message).returncode != 0:
        sys.exit(_die("git commit failed", 1))
    return _run_git(conv_root, "git", "rev-parse", "HEAD").stdout.strip() or None


def _git_commit_paths(conv_root: Path, tree_paths: List[str], message: str) -> Optional[str]:
    """Stage multiple trees and commit them together. Used by patch-add to capture
    BOTH the Output/ and Validation/source/ sides of a [TEST-PATCH] in one commit,
    so a later ``git revert`` undoes both sides. Returns the SHA or None."""
    for tp in tree_paths:
        if _run_git(conv_root, "git", "add", tp).returncode != 0:
            sys.exit(_die(f"git add {tp} failed", 1))
    if _run_git(conv_root, "git", "diff", "--cached", "--quiet").returncode == 0:
        return None
    if _run_git(conv_root, "git", "commit", "-m", message).returncode != 0:
        sys.exit(_die("git commit failed", 1))
    return _run_git(conv_root, "git", "rev-parse", "HEAD").stdout.strip() or None


def _assert_no_scos_leak_in_output(conv_root: Path) -> None:
    """Reject committing a [MIGRATION-FIX] that adds SCOS_* harness identifiers to
    Output/ — those are cherry-picked onto the deliverable and must be
    production-safe. The #1 cause of harvest cherry-pick conflicts."""
    diff = _run_git(conv_root, "git", "diff", "HEAD", "--", "Output").stdout
    leaked: List[str] = []
    sample = ""
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            hits = _SCOS_LEAK_RE.findall(line)
            if hits:
                leaked.extend(hits)
                if not sample:
                    sample = line[1:].strip()[:160]
    if leaked:
        uniq = ", ".join(sorted(set(leaked)))
        sys.stderr.write(
            "REJECTED: this migration-fix would write validation-harness "
            f"identifier(s) into Output/: {uniq}.\n"
            "[MIGRATION-FIX] commits are cherry-picked onto the deliverable and must "
            "be production-safe — never reference SCOS_* env vars. Rewrite the read "
            "to the PRODUCTION fully-qualified name, or commit it with "
            f"--kind test-patch instead and split any mixed edit.\n"
            f"  first offending line: {sample}\n"
        )
        sys.exit(2)


def _assert_fix_commits_clean(conv_root: Path, fix_shas: List[str]) -> None:
    """Harvest gate: a [MIGRATION-FIX] being cherry-picked must not introduce
    SCOS_* identifiers into Output/ (catches raw `git commit` bypasses)."""
    offenders: List[tuple] = []
    for sha in fix_shas:
        diff = _run_git(conv_root, "git", "show", sha, "--", "Output").stdout
        toks: set = set()
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                toks.update(_SCOS_LEAK_RE.findall(line))
        if toks:
            offenders.append((sha, sorted(toks)))
    if offenders:
        lines = ["cannot harvest — [MIGRATION-FIX] commit(s) leak validation-harness "
                 "identifiers into Output/ (cherry-picked onto the deliverable, must be "
                 "production-safe):"]
        for sha, toks in offenders:
            lines.append(f"  {sha[:10]}  {', '.join(toks)}")
        lines.append("Amend each to use the production fully-qualified name, or move the "
                     "change into a [TEST-PATCH] commit, then re-run harvest.")
        sys.exit(_die("\n".join(lines), 1))


# ---------------------------------------------------------------------------
# State machine (pure — no I/O)
# ---------------------------------------------------------------------------

def _status(trial: dict) -> str:
    return trial.get("status", "pending")


def advance_phase(state: dict, conv_root: Path | None = None) -> dict:
    """Advance state.phase init -> phase_a_done -> phase_b_done.

    Mirrors PySpark ``validate.py`` / Scala ``scos_state.advance_phase``: advances
    when all trials have the required iters (``phase_a_iters`` for
    ``phase_a_done``; ``phase_b_iters`` / terminal statuses for ``phase_b_done``).
    It does **NOT** require every trial to be terminal — the phase tracks
    iteration progress, not final verdict. (Java previously returned early unless
    all trials were terminal, so a single unresolved trial pinned the run at
    ``init`` forever.)

    Also flips the ``phase_a_complete`` / ``phase_b_complete`` milestones in
    lockstep with the phase, which is what ``batch.py`` reads for pool phase
    labels — without this the pool always reported the Phase A label.
    """
    trials: Dict[str, dict] = state.get("trials") or {}
    if not trials:
        return state
    phase = state.get("phase", "init")
    have_a = all(
        bool(t.get("phase_a_iters")) or _status(t) == "phase_a_skipped"
        for t in trials.values()
    )
    have_b = all(
        bool(t.get("phase_b_iters")) or _status(t) in ("phase_a_skipped", "hard_stuck")
        for t in trials.values()
    )
    if phase == "init" and have_a and not have_b:
        new_phase = "phase_a_done"
    elif have_b:
        new_phase = "phase_b_done"
    else:
        new_phase = phase
    if new_phase != phase:
        state = {**state, "phase": new_phase}
    milestones = state.setdefault("milestones", {})
    if new_phase in ("phase_a_done", "phase_b_done") and not milestones.get("phase_a_complete"):
        milestones["phase_a_complete"] = True
        if conv_root is not None:
            append_event(validation_root(conv_root), {
                "kind": "milestone_completed", "milestone": "phase_a_complete",
            })
    if new_phase == "phase_b_done" and not milestones.get("phase_b_complete"):
        milestones["phase_b_complete"] = True
        if conv_root is not None:
            append_event(validation_root(conv_root), {
                "kind": "milestone_completed", "milestone": "phase_b_complete",
            })
    return state


def comparison_verdict(trial: dict) -> str:
    """run_index comparison.verdict from trial status + documented divergences.

    ``phase_a_skipped`` is treated as ``passed_no_baseline`` for the verdict:
    both mean "no comparable baseline was produced", so the result is
    ``unverified`` regardless of whether the explicit-skip path or the
    auto-promotion path was taken (parity with the Scala validator /
    PySpark's ``_infer_pass_status``).
    """
    status = _status(trial)
    has_divs = bool(trial.get("documented_divergences"))
    if status == "passed":
        return "match"
    if status in ("passed_no_baseline", "phase_a_skipped"):
        return "unverified"
    if has_divs:
        return "cosmetic_divergence"
    if status == "hard_stuck":
        return "real_divergence"
    return "pending"


def apply_trial_status(
    state: dict, trial_id: str, status: str,
    final_iter: Optional[int] = None, reason: Optional[str] = None,
    analysis_repair_exhausted: bool = False, baseline_not_comparable: bool = False,
) -> Tuple[dict, Optional[int], Optional[str], bool]:
    """Pure core of record-trial-status. Returns (new_state, exit_code, error, noop).

    exit_code/error are set (and new_state is unchanged) when the transition is
    rejected; noop=True is the idempotent already-terminal-same case (exit 0).
    Mirrors cmdRecordTrialStatus including the hard_stuck fixer-dispatch /
    analysis-repair-exhaustion gate and the passed_no_baseline anti-gaming gate.
    """
    if status not in TRIAL_STATUSES:
        return state, 2, f"invalid status '{status}'; expected one of: {', '.join(sorted(TRIAL_STATUSES))}", False
    trials: Dict[str, dict] = state.get("trials") or {}
    if trial_id not in trials:
        return state, 2, f"trial '{trial_id}' not in state.trials", False

    current = _status(trials[trial_id])
    if current == status and current in TERMINAL_TRIAL_STATUSES:
        return state, 0, None, True  # idempotent no-op

    if status == "hard_stuck":
        dispatches = state.get("fixer_dispatches") or []
        has_dispatch = any(
            trial_id in (d.get("trials_affected") or []) for d in dispatches
        )
        # Schema/data gaps (TABLE_OR_VIEW_NOT_FOUND / COLUMN_NOT_FOUND) are repaired
        # inline (edit schemas, datagen, provision), never by the fixer.
        schema_repair_iters = [
            it for it in ((trials[trial_id].get("phase_b_iters") or [])
                          + (trials[trial_id].get("phase_a_iters") or []))
            if it.get("fix_category") in ("schema_gap", "analysis_repair")
        ]
        if not (has_dispatch or analysis_repair_exhausted):
            return (state, 2,
                    f"REJECTED: cannot mark trial '{trial_id}' hard_stuck — no fixer "
                    f"dispatch and no schema-repair exhaustion on record. For "
                    f"code/dialect errors dispatch the migration-fixer first; for "
                    f"missing tables/columns run the inline schema-repair loop "
                    f"(edit schemas, re-run schema_mine + datagen, provision) recorded via "
                    f"record-iter --fix-category analysis_repair, then pass "
                    f"--analysis-repair-exhausted once repair is exhausted. "
                    f"See agents/scos-runner.md.", False)
        # A single incidental repair iter is not proof of exhaustion: when the only
        # justification is --analysis-repair-exhausted (no fixer dispatch), require
        # at least two recorded repair rounds first.
        if analysis_repair_exhausted and not has_dispatch and len(schema_repair_iters) < 2:
            return (state, 2,
                    f"REJECTED: cannot mark trial '{trial_id}' hard_stuck with "
                    f"--analysis-repair-exhausted after only {len(schema_repair_iters)} "
                    f"schema-repair round(s). A schema gap must go through at least TWO "
                    f"inline repair rounds (edit schemas, re-run schema_mine + datagen, "
                    f"provision, re-run — each recorded via record-iter --fix-category "
                    f"analysis_repair) before it may be declared exhausted. A "
                    f"COLUMN_NOT_FOUND on a source column usually means the read is "
                    f"mined with output-alias columns only — add the WHERE/JOIN source "
                    f"columns. See agents/scos-runner.md.", False)

    # 'passed_no_baseline' means there is NO Phase A baseline. If Phase A actually
    # produced one (a phase_a iter with passing>=1, failing==0), the runner must
    # COMPARE against it — escaping a failed comparison to passed_no_baseline hides
    # the divergence and is the most common false verdict.
    if status == "passed_no_baseline" and not baseline_not_comparable:
        baseline_produced = any(
            it.get("passing", 0) >= 1 and it.get("failing", 1) == 0
            for it in (trials[trial_id].get("phase_a_iters") or [])
        )
        if baseline_produced:
            return (state, 2,
                    f"REJECTED: cannot mark trial '{trial_id}' passed_no_baseline — "
                    f"Phase A produced a baseline (a phase_a iter passed), so it MUST "
                    f"be compared. Mark 'passed' once SCOS matches it (small "
                    f"date-relative row-count diffs are cosmetic — record them with "
                    f"document-divergence and the trial still passes); treat an "
                    f"unresolved REAL divergence as hard_stuck. passed_no_baseline is "
                    f"only for trials with NO baseline (phase_a_skipped). If Phase A "
                    f"genuinely captured different sinks than Phase B, pass "
                    f"--baseline-not-comparable --reason. See agents/scos-runner.md.",
                    False)

    updated = {**trials[trial_id], "status": status}
    if final_iter is not None:
        updated["final_iter"] = final_iter
    if reason:
        updated["hard_stuck_reason"] = reason
    if status in ("passed", "passed_no_baseline"):
        updated.pop("hard_stuck_reason", None)

    new_trials = {**trials, trial_id: updated}
    new_state = advance_phase({**state, "trials": new_trials})
    return new_state, None, None, False


def materialize_manual_review_statuses(conv_root: Path, state: dict) -> dict:
    """Pending trials with BOTH a _manual_review.json marker AND _index.json
    (capture evidence) become passed_no_baseline. Mirrors ScosState."""
    trials: Dict[str, dict] = state.get("trials") or {}
    changed = False
    updated = dict(trials)
    for tid, trial in trials.items():
        if _status(trial) != "pending":
            continue
        phase_b = validation_root(conv_root) / "results" / "phase_b" / tid
        marker = phase_b / "_manual_review.json"
        index = phase_b / "_index.json"
        if marker.is_file() and index.is_file():
            updated[tid] = {**trial, "status": "passed_no_baseline",
                            "manual_review_marker": str(marker)}
            changed = True
    if changed:
        return advance_phase({**state, "trials": updated})
    return state


def recover_pending_trials(state: dict) -> Tuple[dict, int]:
    """Pending trials with a final Phase B iter become passed/passed_no_baseline/
    hard_stuck based on that iter's pass/fail counts. Mirrors ScosState."""
    trials: Dict[str, dict] = state.get("trials") or {}
    recovered = 0
    updated = dict(trials)
    for tid, t in trials.items():
        status = _status(t)
        b_iters = t.get("phase_b_iters") or []
        a_iters = t.get("phase_a_iters") or []
        if status != "pending" or not b_iters:
            continue
        last_b = b_iters[-1]
        passing = last_b.get("passing", 0)
        failing = last_b.get("failing", 0)
        if passing > 0 and failing == 0:
            new_status = "passed" if a_iters else "passed_no_baseline"
        elif failing > 0:
            new_status = "hard_stuck"
        else:
            new_status = status
        if new_status != status:
            recovered += 1
            t2 = {**t, "status": new_status}
            if new_status == "hard_stuck":
                t2["hard_stuck_reason"] = "auto-recovered: phase_b_failure"
            updated[tid] = t2
    return ({**state, "trials": updated}, recovered)


# ---------------------------------------------------------------------------
# run_index.json assembly (port of cmdBuildIndex / buildIndexEntrypoints)
# ---------------------------------------------------------------------------

def _build_phase_block(phase: str, d: Path, trial: dict, workspace: Path) -> dict:
    iters_key = "phase_a_iters" if phase == "A" else "phase_b_iters"
    result_files = []
    if d.is_dir():
        result_files = [str(p.relative_to(workspace)) for p in _list_files(d)
                        if p.name.endswith(".parquet")]
    return {"iters": trial.get(iters_key) or [], "result_files": result_files}


def _collect_migration_fix_commits(conv_root: Path, state: dict) -> Dict[str, List[dict]]:
    """Map trial_id -> [{sha, subject, body?}] for [MIGRATION-FIX] commits on the
    validation branch, attributed via each commit's ``SCOS-Trials`` trailer. These
    are the commits cherry-picked onto the deliverable at harvest. Empty when there
    is no validation branch (non-git run)."""
    git = state.get("git", {})
    ob, vb = git.get("original_branch"), git.get("validation_branch")
    by_trial: Dict[str, List[dict]] = {}
    if not vb:
        return by_trial
    rng = f"{ob}..{vb}" if ob else vb
    log = _run_git(conv_root, "git", "log", "--reverse", "--grep", r"\[MIGRATION-FIX\]",
                   "--format=%H%x1f%s%x1f%b%x1e", rng)
    if log.returncode != 0:
        return by_trial
    for rec in log.stdout.split("\x1e"):
        if not rec.strip():
            continue
        parts = rec.strip().split("\x1f")
        sha = parts[0].strip()
        subject = parts[1].strip() if len(parts) > 1 else ""
        body = parts[2].strip() if len(parts) > 2 else ""
        if subject.startswith("[MIGRATION-FIX]"):
            subject = subject[len("[MIGRATION-FIX]"):].strip()
        tids: List[str] = []
        m = re.search(r"^SCOS-Trials:\s*(.+)$", body, re.M)
        if m:
            tids = [t.strip() for t in m.group(1).split(",") if t.strip()]
        entry = {"sha": sha, "subject": subject}
        body_no_trailer = re.sub(r"^SCOS-Trials:.*$", "", body, flags=re.M).strip()
        if body_no_trailer:
            entry["body"] = body_no_trailer
        for t in tids:
            by_trial.setdefault(t, []).append(entry)
    return by_trial


def _build_index_entrypoints(workspace: Path, trials: dict, state: dict) -> Tuple[List[str], List[dict]]:
    parse_errors: List[str] = []
    out: List[dict] = []
    source_path = (state.get("paths") or {}).get("original_source", "")
    fix_by_trial = _collect_migration_fix_commits(workspace.parent, state)
    for tid in sorted(trials):
        trial = trials[tid]
        phase_a_dir = workspace / "results" / "phase_a" / tid
        phase_b_dir = workspace / "results" / "phase_b" / tid
        phase_a_block = _build_phase_block("A", phase_a_dir, trial, workspace)
        phase_b_block = _build_phase_block("B", phase_b_dir, trial, workspace)

        diff_entries = []
        if phase_b_dir.is_dir():
            for f in sorted(phase_b_dir.iterdir(), key=lambda p: p.name):
                if f.is_file() and f.name.endswith("_diff.json"):
                    try:
                        diff_entries.append(json.loads(f.read_text(encoding="utf-8")))
                    except Exception as e:  # noqa: BLE001
                        parse_errors.append(f"{tid}/{f.name}: {e}")

        snapshot_paths = []
        snap_dir = phase_b_dir / "stage_snapshot"
        if snap_dir.is_dir():
            snapshot_paths = [str(p.relative_to(workspace)) for p in sorted(snap_dir.iterdir(), key=lambda p: p.name)
                              if p.name.endswith(".csv")]
        phase_b_block = {**phase_b_block, "stage_snapshot_paths": snapshot_paths,
                         "migration_fix_commits": fix_by_trial.get(tid, [])}

        status = _status(trial)
        reason = trial.get("hard_stuck_reason") or ("matched baseline" if status == "passed" else "")
        out.append({
            "id": tid,
            "source_path": source_path,
            "phase_a": phase_a_block,
            "phase_b": phase_b_block,
            "comparison": {
                "verdict": comparison_verdict(trial),
                "diffs": diff_entries,
                "documented_divergences": trial.get("documented_divergences") or [],
            },
            "trial_dir": f"results/phase_b/{tid}/",
            "verdict": {"overall": status, "reason": reason},
        })
    return parse_errors, out


def build_index(conv_root: Path) -> None:
    state = load_state(conv_root)
    workspace = validation_root(conv_root)
    trials = state.get("trials") or {}
    milestones = state.get("milestones") or {}

    run_block = {
        "run_id": state.get("run_id", ""),
        "created_at": state.get("created_at", ""),
        "phase": state.get("phase", ""),
        "project_slug": (state.get("config") or {}).get("project_slug", ""),
    }
    parse_errors, entrypoints_list = _build_index_entrypoints(workspace, trials, state)

    mock_root = workspace / "shared" / "mock_data"
    mock_entries = []
    if mock_root.is_dir():
        for td in sorted((p for p in mock_root.iterdir() if p.is_dir()), key=lambda p: p.name):
            files = [str(p.relative_to(workspace)) for p in _list_files(td)]
            mock_entries.append({"trial_id": td.name, "files": files})

    aux_dir = workspace / "shared" / "auxiliary"
    aux_files = [str(p.relative_to(workspace)) for p in _list_files(aux_dir)
                 if p.name.endswith(".sql") and not p.name.endswith(".bak")] if aux_dir.is_dir() else []

    tests_dir = workspace / "tests"
    rendered_tests = [str(p.relative_to(workspace)) for p in _list_files(tests_dir)
                      if re.fullmatch(r"Test.*\.scala", p.name) or re.fullmatch(r".*[Ss]pec\.scala", p.name)] \
        if tests_dir.is_dir() else []

    schemas_exists = (workspace / "shared" / "schemas.json").is_file()
    blueprint_exists = (workspace / "shared" / "patch_blueprint.json").is_file()
    events_exists = (workspace / "events.jsonl").is_file()

    artifacts_index = {
        "analysis": "shared/analysis.json",
        "schemas": "shared/schemas.json" if schemas_exists else None,
        "patch_blueprint": "shared/patch_blueprint.json" if blueprint_exists else None,
        "mock_data": mock_entries,
        "auxiliary_sql": aux_files,
        "rendered_tests": rendered_tests,
    }

    run_index = {
        "run": run_block,
        "milestones": milestones,
        "entrypoints": entrypoints_list,
        "artifacts_index": artifacts_index,
        "events": "events.jsonl" if events_exists else None,
        "fixer_dispatches": state.get("fixer_dispatches") or [],
        "documented_divergences": [d for t in trials.values()
                                   for d in (t.get("documented_divergences") or [])],
        "warnings": state.get("synth_warnings") or [],
        "parse_errors": parse_errors,
    }
    write_atomic(workspace / "run_index.json", run_index)
    print(f"[scos-control] run_index.json written to {workspace / 'run_index.json'}")


# ---------------------------------------------------------------------------
# summary (port of cmdSummary + writeReportMd) — the exit-4 output gate
# ---------------------------------------------------------------------------

def _cleanup_sql(state: dict) -> List[str]:
    sf = state.get("snowflake") or {}
    database = sf.get("database", "SCOS_VALIDATION")
    golden = sf.get("golden_schemas") or {}
    if golden:
        return [f"DROP SCHEMA IF EXISTS {database}.{gs.get('schema')} CASCADE"
                for gs in golden.values() if gs.get("schema")]
    schema = sf.get("schema", "")
    return [f"DROP SCHEMA IF EXISTS {database}.{schema} CASCADE"] if schema else []


def _report_app_command(conv_root: Path) -> str:
    """Single-line shell command to launch the Streamlit validation report."""
    scripts_dir = Path(__file__).resolve().parent
    project_root = scripts_dir.parent.parent  # the snowpark-connect uv project
    report_app = scripts_dir / "report" / "validation_report_app.py"
    validation_root_dir = conv_root / VALIDATION_DIRNAME
    return (f"uv run --project {project_root} python -m streamlit run "
            f"{report_app} -- --run-root {validation_root_dir}")


def _print_report_app_command(conv_root: Path) -> None:
    """Emit a copy-pasteable one-liner (no internal line breaks)."""
    print()
    print("Open the interactive report (copy/paste this single line):")
    print(_report_app_command(conv_root))


def _write_report_md(workspace: Path, trials: dict, database: str, golden: dict, state: dict, overall: str) -> None:
    passed = sum(1 for t in trials.values() if _status(t) == "passed")
    lines = [
        "# Validation Report", "",
        f"**Outcome:** {overall} ({passed}/{len(trials)} passed)", "",
        "## Trials", "",
        "| Trial | Status | A iters | B iters | Fix Category | Hard Stuck Reason |",
        "|-------|--------|---------|---------|--------------|-------------------|",
    ]
    for tid in sorted(trials):
        t = trials[tid]
        lines.append(f"| {tid} | {_status(t)} | {len(t.get('phase_a_iters') or [])} | "
                     f"{len(t.get('phase_b_iters') or [])} | {t.get('fix_category', '')} | "
                     f"{t.get('hard_stuck_reason', '')} |")
    lines.append("")
    dispatches = state.get("fixer_dispatches") or []
    if dispatches:
        lines += ["## Fixer Dispatches", ""]
        for d in dispatches:
            lines.append(f"- iter={d.get('iter', 0)} class={d.get('error_class', '')} "
                         f"trials={d.get('trials_affected', [])} outcome={d.get('outcome', '')}")
        lines.append("")
    lines += ["## Infrastructure", "", f"- Database: `{database}`"]
    if golden:
        for ep, gs in golden.items():
            lines.append(f"- Schema ({ep}): `{gs.get('schema', '?')}`")
    else:
        lines.append(f"- Schema: `{(state.get('snowflake') or {}).get('schema', '?')}`")
    lines.append("")
    lines += ["## Interactive report", "",
              "Open the Streamlit validation report (copy/paste this single line):", "",
              "```", _report_app_command(workspace.parent), "```", ""]
    report = workspace / "results" / "REPORT.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"[scos-control] REPORT.md written to {report}")


def _run_comparators_for_passed_trials(conv_root: Path, state: dict) -> None:
    """Auto-run compare_trial.py for every 'passed' trial that has a Phase A baseline.

    Called by _cmd_summary before build_index so the comparison verdict in
    run_index.json always reflects actual parquet diffs, not just status inference.
    Errors are non-fatal — they emit a warning and the status-based fallback is kept.
    Ported from the Scala validator (compare_trial.py is pure Python and
    language-agnostic; no Java-specific adaptation needed).
    """
    import importlib.util as _ilu
    workspace = validation_root(conv_root)
    trials = state.get("trials") or {}
    analysis_path = workspace / "shared" / "analysis.json"

    _ct_path = Path(__file__).resolve().parent / "harness" / "compare_trial.py"
    if not _ct_path.is_file():
        print("[scos-control] WARN: harness/compare_trial.py not found — skipping auto-compare",
              file=sys.stderr)
        return

    _spec = _ilu.spec_from_file_location("scos_compare_trial", _ct_path)
    _ct_mod = _ilu.module_from_spec(_spec)
    try:
        _spec.loader.exec_module(_ct_mod)  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        print(f"[scos-control] WARN: compare_trial load failed: {exc}", file=sys.stderr)
        return

    for tid, trial in trials.items():
        if _status(trial) != "passed":
            continue
        pa_tables = workspace / "results" / "phase_a" / tid / "tables"
        if not pa_tables.is_dir():
            continue  # no Phase A baseline — nothing to compare
        try:
            out = _ct_mod.compare_trial(conv_root, tid, analysis_path)
            s = out["summary"]
            print(f"[scos-control] compare trial={tid}: {s['verdict']} "
                  f"({s['table_count']} table(s))")
        except Exception as exc:  # noqa: BLE001
            print(f"[scos-control] WARN: compare failed for {tid}: {exc}", file=sys.stderr)


def _cmd_summary(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    state = materialize_manual_review_statuses(conv_root, state)
    state, _ = recover_pending_trials(state)
    save_state(conv_root, state)

    workspace = validation_root(conv_root)
    trials = state.get("trials") or {}
    sf = state.get("snowflake") or {}
    database = sf.get("database", "SCOS_VALIDATION")
    golden = sf.get("golden_schemas") or {}
    cleanup_sql = _cleanup_sql(state)

    totals = {"passed": 0, "review": 0, "stuck": 0, "pending": 0}
    total_divs = 0
    warnings: List[str] = []
    tests_authored = bool((state.get("milestones") or {}).get("tests_authored"))
    for tid in sorted(trials):
        t = trials[tid]
        st = _status(t)
        a_iters = t.get("phase_a_iters") or []
        total_divs += len(t.get("documented_divergences") or [])
        if st == "passed":
            totals["passed"] += 1
        elif st in ("passed_no_baseline", "phase_a_skipped"):
            totals["review"] += 1
        elif st == "hard_stuck":
            totals["stuck"] += 1
        else:
            totals["pending"] += 1
        if tests_authored and not a_iters:
            warnings.append(f"trial '{tid}': tests_authored=true but phase_a_iters=[] — runner did not call record-iter")

    if totals["review"] > 0:
        overall, ship_rec = "partial", "review"
    elif totals["passed"] == len(trials) and totals["stuck"] == 0:
        overall, ship_rec = "passed", "green"
    elif totals["stuck"] > 0:
        overall, ship_rec = "blocked", "block"
    else:
        overall, ship_rec = "partial", "review"

    blocking = [{"trial": tid, "kind": "hard_stuck", "reason": trials[tid].get("hard_stuck_reason", "")}
                for tid in sorted(trials) if _status(trials[tid]) == "hard_stuck"]
    non_blocking = []
    for tid in sorted(trials):
        t = trials[tid]
        if _status(t) == "passed_no_baseline":
            non_blocking.append({"trial": tid, "kind": "manual_review_required",
                                 "detail": "SCOS run passed without a trustworthy Phase A baseline"})
        for div in (t.get("documented_divergences") or []):
            non_blocking.append({"trial": tid, "kind": "documented_divergence",
                                 "detail": f"{div.get('sink_id', '')}.{div.get('column', '')}: {div.get('reason', '')}"})

    phase_b_passes = sum(1 for t in trials.values()
                         if _status(t) == "passed" and (t.get("phase_b_iters") or []))
    decision = {
        "overall": overall, "ship_recommendation": ship_rec,
        "blocking_reasons": blocking, "non_blocking_qualifications": non_blocking,
        "non_blocking_divergences": total_divs, "phase_a_passes": totals["passed"],
        "manual_review_required": totals["review"], "phase_b_passes": phase_b_passes,
    }
    if golden:
        ephemeral = {ep: f"{database}.{gs.get('schema', '')}" for ep, gs in golden.items()}
    else:
        ephemeral = {"default": f"{database}.{sf.get('schema', '')}"}

    summary = {
        "decision": decision, "trials": trials,
        "phase_a_iters": (state.get("phase_a") or {}).get("iter", 0),
        "phase_b_iters": (state.get("phase_b") or {}).get("iter", 0),
        "ephemeral_schemas": ephemeral, "cleanup_sql": cleanup_sql,
        "warnings": warnings,
    }
    results_dir = workspace / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    write_atomic(results_dir / "summary.json", summary)
    print(f"[scos-control] summary written to {results_dir / 'summary.json'}")
    _write_report_md(workspace, trials, database, golden, state, overall)
    for w in warnings:
        print(f"[scos-control] WARN: {w}", file=sys.stderr)

    # Run comparator for every passed trial with a Phase A baseline before build-index,
    # so run_index.json comparison verdicts reflect actual parquet diffs, not just status.
    _run_comparators_for_passed_trials(conv_root, state)

    # snapshot-stage is JDBC-only (jar) — skipped here; build-index is Python.
    try:
        build_index(conv_root)
    except Exception as e:  # noqa: BLE001
        print(f"[scos-control] WARN: build-index failed: {e}", file=sys.stderr)

    expected = {
        "summary.json": results_dir / "summary.json",
        "REPORT.md": results_dir / "REPORT.md",
        "run_index.json": workspace / "run_index.json",
        "events.jsonl": workspace / "events.jsonl",
    }
    missing = [name for name, p in expected.items() if not p.is_file()]
    if missing:
        print(f"[scos-control] error: summary incomplete — missing required output(s): {', '.join(missing)}",
              file=sys.stderr)
        return 4
    print(f"[scos-control] summary complete — all {len(expected)} required outputs present")
    _print_report_app_command(conv_root)
    return 0


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _die(msg: str, code: int = 2) -> int:
    print(f"[scos-control] error: {msg}", file=sys.stderr)
    return code


_ALIGN_CODE_EXTS = (".java", ".sql")
_ALIGN_SKIP_DIRS = {
    ".git", ".idea", "target", "build", ".gradle", "out",
    "__pycache__", ".pytest_cache", "node_modules", VALIDATION_DIRNAME,
}


def _rel_code_files(root: Path) -> set:
    """Forward-slash relative paths of code files under *root* (build dirs skipped)."""
    found: set = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _ALIGN_SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(_ALIGN_CODE_EXTS):
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                found.add(rel.replace(os.sep, "/"))
    return found


def _suggest_aligned_source(orig: Path, src: set, out: set) -> Optional[str]:
    """Best-effort: find an --original-source that would make src a subset of out.
    Case A: source one level too shallow (Output wraps under orig.name/) -> point
    at orig.parent. Case B: source one level too deep (extra single wrapper dir)
    -> descend into it. Returns a path string or None."""
    if not src or not out:
        return None
    if {f"{orig.name}/{s}" for s in src} <= out:
        return str(orig.parent)
    tops = {s.split("/", 1)[0] for s in src if "/" in s}
    if len(tops) == 1:
        d = next(iter(tops))
        stripped = {s[len(d) + 1:] for s in src if s.startswith(d + "/")}
        if stripped and stripped <= out:
            return str(orig / d)
    return None


def _check_source_output_aligned(source_root: Path, output_root: Path, orig: Path) -> int:
    """Verify Validation/source and Output share the same relative path roots so
    the patch engine's <rel> resolves on both sides. Returns 0 when aligned (or
    nothing to check); returns 2 (after printing) on a real mismatch — patches
    would silently miss one side and the migrated code would never be exercised."""
    src = _rel_code_files(source_root)
    out = _rel_code_files(output_root)
    if not src or not out:
        return 0
    if src <= out:
        return 0
    missing = sorted(src - out)[:5]
    suggestion = _suggest_aligned_source(orig, src, out)
    if suggestion:
        fix = (f"  Suggested fix: re-run init with\n    --original-source {suggestion}\n"
               f"  (that directory's layout lines up with Output/ — all {len(src)} "
               f"source files would match).")
    else:
        fix = ("Fix: re-run init with --original-source pointing at the directory whose "
               "internal layout matches Output/, adding any wrapping directories needed "
               "so Validation/source/<rel> and Output/<rel> resolve to the same files.")
    return _die(
        "Validation/source and Output/ do not share relative path roots "
        f"({len(src & out)}/{len(src)} source code files line up). Patches key on a "
        "single <relative_file> resolved as BOTH Validation/source/<rel> and "
        "Output/<rel>, so the two trees must mirror each other.\n"
        f"  e.g. these source files have no Output/ match: {missing}\n{fix}", 2)


def _cmd_init(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    workspace = validation_root(conv_root)
    sp = state_path(conv_root)
    if sp.is_file() and not args.force:
        existing = load_json(sp)
        if existing.get("schema_version", -1) == SCHEMA_VERSION and any((existing.get("milestones") or {}).values()):
            print(f"[scos-control] skipping init (already initialized at "
                  f"run_id={existing.get('run_id', '?')}, phase={existing.get('phase', '?')})")
            return 0
    if not args.migrated_source and not (conv_root / "Output").exists():
        return _die("<conv-root>/Output/ is missing and --migrated-source not given")
    for d in ("source", "tests", "shared", "shared/mock_data", "shared/auxiliary",
              "shared/stubs", "results", "results/phase_a", "results/phase_b"):
        (workspace / d).mkdir(parents=True, exist_ok=True)
    if not args.original_source:
        return _die("--original-source is required")
    orig = Path(args.original_source).expanduser().resolve()
    if not orig.exists():
        return _die(f"--original-source does not exist: {orig}")
    dest_src = workspace / "source"
    # Start from an empty target: a prior failed init (wrong --original-source)
    # leaves a half-populated source/; copying again would MERGE two layouts and
    # produce a misleading alignment count. Always wipe first so a re-run is clean.
    if dest_src.exists():
        shutil.rmtree(dest_src)
    dest_src.mkdir(parents=True)
    if orig.is_dir():
        _copy_dir(orig, dest_src)
    else:
        shutil.copy2(orig, dest_src / orig.name)

    # Patches key on a single <relative_file> resolved as both Validation/source/<rel>
    # and Output/<rel>; if the copied source and the migrated tree don't share
    # relative path roots (e.g. Output nests under an extra wrapper dir), stop now
    # with a clear message instead of silently mis-patching later.
    migrated_root = (Path(args.migrated_source).expanduser().resolve()
                     if args.migrated_source else conv_root / "Output")
    if orig.is_dir() and migrated_root.is_dir():
        rc = _check_source_output_aligned(dest_src, migrated_root, orig)
        if rc:
            return rc

    slug = project_slug(args.project_slug or conv_root.name)
    rid = run_id()
    schema = f"{slug}_{rid}".upper()
    database = getattr(args, "database", None) or os.environ.get("SCOS_VALIDATION_DATABASE", "SCOS_VALIDATION")
    state = {
        "schema_version": SCHEMA_VERSION, "run_id": rid, "created_at": now_iso(),
        "phase": "init",
        "config": {"connection_name": args.connection, "project_slug": slug, "database": database},
        "paths": {"skill_dir": "", "original_source": str(orig), "conv_root": str(conv_root)},
        "snowflake": {
            "database": database, "schema": schema,
            "stage": f"{database}.{schema}.SCOS_TEST_STAGE", "stage_prefix": rid,
            "provisioned": False, "provisioned_tables": [],
        },
        "milestones": {m: False for m in (
            "analyzer_survey", "entrypoints_selected", "analyzer_deep", "patches_authored",
            "workload_built", "tests_authored", "venv_prewarmed", "snowflake_provisioned")},
        "phase_a": {"iter": 0}, "phase_b": {"iter": 0},
        "trials": {}, "synth_warnings": [],
        "git": {"original_branch": None, "validation_branch": None, "harvested": False},
    }
    save_state(conv_root, state)

    # Cut an ephemeral validation branch off the migrated code's current branch.
    # All [TEST-PATCH] blueprint I/O patches land here (never cherry-picked onto
    # the deliverable); [MIGRATION-FIX] commits are cherry-picked at harvest. The
    # validation branch is kept for inspection after harvest.
    original_branch = _current_branch(conv_root)
    if original_branch and original_branch.startswith("validation/"):
        print(f"[scos-control] WARNING: current branch '{original_branch}' is itself a "
              "validation branch — you may be nesting validation branches. Consider "
              "switching to main/master first.", file=sys.stderr)
    validation_branch = f"validation/{rid}"
    if original_branch:
        # Clean orphaned validation branches from prior failed runs (no Validation/).
        listed = _run_git(conv_root, "git", "branch", "--list", "validation/*")
        if listed.returncode == 0:
            for stale in listed.stdout.splitlines():
                stale = stale.strip().lstrip("* ")
                if not stale or stale == validation_branch:
                    continue
                ws = _run_git(conv_root, "git", "ls-tree", "--name-only", stale, "Validation/")
                if ws.returncode != 0 or not ws.stdout.strip():
                    print(f"[scos-control] removing orphaned validation branch '{stale}'")
                    _run_git(conv_root, "git", "branch", "-D", stale)
        _ensure_gitignore(conv_root)
        res = _run_git(conv_root, "git", "checkout", "-b", validation_branch)
        if res.returncode != 0:
            res = _run_git(conv_root, "git", "checkout", validation_branch)
        if res.returncode == 0:
            state["git"] = {"original_branch": original_branch,
                            "validation_branch": validation_branch, "harvested": False}
            save_state(conv_root, state)
            print(f"[scos-control] validation branch: {validation_branch} (off {original_branch})")
            base_sha = _git_commit_paths(
                conv_root, [str(Path(VALIDATION_DIRNAME) / "source")],
                "[VALIDATION] import Phase-A source baseline")
            if base_sha:
                print(f"[scos-control] committed Phase-A source baseline: {base_sha}")
        else:
            print(f"[scos-control] WARNING: could not create validation branch: {res.stderr.strip()}")
    else:
        print("[scos-control] WARNING: <conv-root> is not a git repo; harvest/commit will not work")

    print(f"[scos-control] initialized validation workspace: run_id={rid}, schema={schema}")
    return 0


def _cmd_select_entrypoints(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    analysis = load_analysis(conv_root)
    cands = analysis.get("entrypoint_candidates") or []
    if not cands:
        cands = analysis.get("entrypoints") or []
        if cands:
            print("[scos-control] WARNING: using deprecated key 'entrypoints'; rename to 'entrypoint_candidates'")
    if not cands:
        return _die("analysis.json has no entrypoint_candidates — run the analyzer first")
    if not args.ids:
        return _die("--ids is required for non-interactive selection in Scala runner")
    id_set = {x.strip() for x in args.ids.split(",")}
    selected = [c for c in cands if c.get("id") in id_set]
    if not selected:
        return _die(f"no candidates matched --ids {args.ids}")
    maxv = args.max if args.max is not None else 10
    if len(selected) > maxv:
        return _die(f"{len(selected)} entrypoints selected, exceeds --max {maxv}")

    analysis["entrypoints"] = selected
    save_analysis(conv_root, analysis)
    new_ids = {s.get("id") for s in selected if s.get("id")}
    trials = state.get("trials") or {}
    new_trials = {k: v for k, v in trials.items() if k in new_ids}
    for ep in selected:
        ep_id = ep.get("id", "unknown")
        new_trials.setdefault(ep_id, {"status": "pending", "phase_a_iters": [], "phase_b_iters": []})
    state["trials"] = new_trials
    state.setdefault("milestones", {})["entrypoints_selected"] = True
    save_state(conv_root, state)
    print(f"[scos-control] selected {len(selected)} entrypoint(s): [{', '.join(sorted(new_ids))}]")
    return 0


def _cmd_status(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = materialize_manual_review_statuses(conv_root, load_state(conv_root))
    trials = state.get("trials") or {}
    phase = state.get("phase", "init")
    print(f"Phase: {phase}")
    print(f"Phase A iter: {(state.get('phase_a') or {}).get('iter', 0)}")
    print(f"Phase B iter: {(state.get('phase_b') or {}).get('iter', 0)}")
    print()
    if not trials:
        print("No trials configured.")
        return 1
    any_pending = any_review = any_blocked = False
    for tid in sorted(trials):
        st = _status(trials[tid])
        any_pending = any_pending or st == "pending"
        any_review = any_review or st == "passed_no_baseline"
        any_blocked = any_blocked or st == "hard_stuck"
        print(f"  {tid}: {st}")
    print()
    if any_blocked:
        return 2
    if any_pending or any_review or phase != "phase_b_done":
        return 1
    print("All trials passed.")
    return 0


def _cmd_record_iter(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    pn = {"a": "A", "phase_a": "A", "b": "B", "phase_b": "B"}.get(args.phase.lower())
    if pn is None:
        return _die(f"--phase must be A|B|phase_a|phase_b, got '{args.phase}'")
    trials = state.get("trials") or {}
    if args.trial_id not in trials:
        return _die(f"trial '{args.trial_id}' not in state.trials")
    iter_key = "phase_a_iters" if pn == "A" else "phase_b_iters"
    existing = trials[args.trial_id].get(iter_key) or []
    if any(e.get("iter") == args.iter for e in existing):
        print(f"[scos-control] iter {args.iter} Phase {pn} already recorded for {args.trial_id} — no-op")
        return 0
    entry = {"iter": args.iter, "passing": args.passing, "failing": args.failing}
    if args.issues is not None:
        entry["issues"] = args.issues
    if args.patches_extended is not None:
        entry["extended_patches"] = args.patches_extended
    if args.fix_commit:
        entry["fix_commit"] = args.fix_commit
    if args.fix_category:
        entry["fix_category"] = args.fix_category
    if args.notes:
        entry["notes"] = args.notes
    trials[args.trial_id][iter_key] = existing + [entry]
    state["trials"] = trials
    state.setdefault("phase_a" if pn == "A" else "phase_b", {})["iter"] = args.iter
    state = advance_phase(state)
    save_state(conv_root, state)
    append_event(validation_root(conv_root), {
        "kind": "iter_recorded", "trial_id": args.trial_id, "phase": f"phase_{pn.lower()}",
        "iter": args.iter, "passing": args.passing, "failing": args.failing,
    })
    print(f"[scos-control] recorded Phase {pn} iter {args.iter} for {args.trial_id}: "
          f"pass={args.passing} fail={args.failing}")
    return 0


def _cmd_record_trial_status(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    new_state, code, err, noop = apply_trial_status(
        state, args.trial_id, args.status, args.final_iter, args.reason,
        analysis_repair_exhausted=getattr(args, "analysis_repair_exhausted", False),
        baseline_not_comparable=getattr(args, "baseline_not_comparable", False))
    if err:
        print(f"[scos-control] error: {err}", file=sys.stderr)
        return code or 2
    if noop:
        print(f"[scos-control] trial {args.trial_id} already {args.status} — no-op")
        return 0
    save_state(conv_root, new_state)
    append_event(validation_root(conv_root), {
        "kind": "trial_marked", "trial_id": args.trial_id,
        "status": args.status, "reason": args.reason or "",
    })
    print(f"[scos-control] trial {args.trial_id} status={args.status}"
          + (f" final_iter={args.final_iter}" if args.final_iter is not None else ""))
    return 0


def _cmd_commit(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    prefix = COMMIT_PREFIXES[args.kind]
    message = args.message if args.message.startswith(prefix) else f"{prefix} {args.message}"

    # [MIGRATION-FIX] commits are cherry-picked onto the deliverable — reject any
    # that would leak SCOS_* harness identifiers into Output/.
    if args.kind == "migration-fix":
        _assert_no_scos_leak_in_output(conv_root)

    # Record which trial(s) a fix is for as a git trailer so build-index can
    # attribute the commit to the right entrypoint(s) even across multiple files.
    trial_ids = [t.strip() for t in (args.trial_ids or "").split(",") if t.strip()]
    if trial_ids:
        message = f"{message}\n\nSCOS-Trials: {','.join(trial_ids)}"

    sha = _git_commit_output(conv_root, message)
    if sha is None:
        if args.print_sha_only:
            print(_run_git(conv_root, "git", "rev-parse", "HEAD").stdout.strip())
        else:
            print("[scos-control] nothing to commit")
        return 0
    append_event(validation_root(conv_root), {
        "kind": "commit", "commit_kind": args.kind, "sha": sha, "trial_ids": trial_ids,
    })
    print(sha if args.print_sha_only else f"[scos-control] committed ({args.kind}): {sha}")
    return 0


def _cmd_record_milestone(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    if args.milestone not in CANONICAL_MILESTONES:
        return _die(f"unknown milestone '{args.milestone}'; expected one of: {', '.join(sorted(CANONICAL_MILESTONES))}")
    state = load_state(conv_root)
    state.setdefault("milestones", {})[args.milestone] = True
    save_state(conv_root, state)
    append_event(validation_root(conv_root), {"kind": "milestone_completed", "milestone": args.milestone})
    print(f"[scos-control] milestone '{args.milestone}' recorded")
    return 0


def _cmd_prewarm(args) -> int:
    """Front-load the JVM cold start: stage the test kit into Validation/tests
    and warm the sbt/Coursier cache + zinc by compiling the kit once. Mirrors
    the PySpark validator's `prewarm-venv` so the first real `sbt test` in Phase
    A is fast. Safe to run in the background right after `init`.

    The kit is staged from the sibling Scala skill (shared, single source of truth).
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    skill_dir = Path(__file__).resolve().parent.parent
    kit_src = skill_dir.parent / "validate-spark-scala-to-snowpark-connect" / "harness-scala" / "kit"
    if not kit_src.is_dir():
        return _die(f"kit not found at {kit_src}", 2)
    tests_dir = validation_root(conv_root) / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    # Stage the kit without dragging build output (forces a full zinc recompile)
    # into the trial dir. Mirror local-runner.md's rsync --exclude approach.
    if shutil.which("rsync"):
        subprocess.run(
            ["rsync", "-a", "--exclude", "target/", "--exclude", "project/target/",
             f"{kit_src}/", f"{tests_dir}/"],
            check=True,
        )
    else:
        shutil.copytree(kit_src, tests_dir, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("target", "project/target"))
        for junk in (tests_dir / "target", tests_dir / "project" / "target"):
            if junk.exists():
                shutil.rmtree(junk, ignore_errors=True)

    # Warm sbt: resolve deps + compile the kit (Test/compile pulls test deps too).
    if not shutil.which("sbt"):
        print("[scos-control] WARNING: sbt not on PATH; kit staged but not compiled. "
              "Phase A will pay the full cold-start cost.", file=sys.stderr)
        state.setdefault("milestones", {})["venv_prewarmed"] = True
        save_state(conv_root, state)
        append_event(validation_root(conv_root),
                     {"kind": "milestone_completed", "milestone": "venv_prewarmed",
                      "note": "kit staged; sbt absent, compile skipped"})
        print("prewarm complete: kit staged (sbt absent)")
        return 0

    result = subprocess.run(
        ["sbt", "-batch", "Test/compile"], cwd=str(tests_dir),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Non-fatal: a real Phase A run will surface the compile error with the
        # rendered specs in place. Prewarm only warms caches.
        print(f"[scos-control] WARNING: kit Test/compile returned "
              f"{result.returncode}; caches partially warmed.\n{result.stderr[-2000:]}",
              file=sys.stderr)

    state.setdefault("milestones", {})["venv_prewarmed"] = True
    save_state(conv_root, state)
    append_event(validation_root(conv_root),
                 {"kind": "milestone_completed", "milestone": "venv_prewarmed"})
    print(f"prewarm complete: kit staged at {tests_dir}, sbt cache warmed")
    return 0


# ---------------------------------------------------------------------------
# run-phase-a / run-phase-b — deterministic execution runners
# Mirrors PySpark validate.py install-kit + seed-venv + pytest pattern so the
# orchestrator agent can drive Phase A/B with a single CLI call instead of
# burning LLM turns on file copies, template rendering, and sbt invocations.
# ---------------------------------------------------------------------------

def _snake_to_camel(s: str) -> str:
    """sensor_reading_loader -> SensorReadingLoader; ep-1 -> Ep1."""
    return "".join(p.capitalize() for p in re.sub(r"[^a-zA-Z0-9]+", "_", s).split("_") if p)


def _find_workload_jar(conv_root: Path) -> str:
    """Scan Output/target/ for a fat/assembly JAR when analysis.jar_path is absent."""
    output_dir = conv_root / "Output"
    if not output_dir.is_dir():
        return ""
    # Prefer assembly JARs, skip *-sources.jar and *-javadoc.jar
    for jar in sorted(output_dir.rglob("*-assembly*.jar")):
        if "sources" not in jar.name and "javadoc" not in jar.name:
            return str(jar)
    for jar in sorted(output_dir.rglob("*.jar")):
        if "test" not in jar.name and "sources" not in jar.name and "javadoc" not in jar.name:
            return str(jar)
    return ""


def _find_built_jar(base_dir: Path) -> str:
    """Find a fat/assembly (preferred) or plain jar under <base_dir>/target/, newest first."""
    target = base_dir / "target"
    if not target.is_dir():
        return ""
    cands = [j for j in target.rglob("*.jar")
             if not any(x in j.name for x in ("-sources", "-javadoc", "-tests"))
             and "/test-" not in str(j)]
    if not cands:
        return ""
    # Prefer assembly/shadow/uber/fat jars; then newest mtime.
    def _rank(j: Path) -> tuple:
        name = j.name.lower()
        fat = any(k in name for k in ("assembly", "shadow", "uber", "fat", "-all"))
        return (1 if fat else 0, j.stat().st_mtime)
    return str(max(cands, key=_rank))


def _detect_source_versions(source_dir: Path) -> dict:
    """Detect the original workload's Spark/Scala versions from its build file and map
    them to a compatible kit Spark/Delta/Scala set, returned as SCOS_KIT_* env overrides.

    Phase A runs the original (already-compiled) workload bytecode on the kit's Spark; if
    the kit's Spark differs from the one the workload was built against, Catalyst/Delta
    binary signatures diverge (e.g. TableIdentifier.copy gained a param in Spark 3.4).
    Aligning the kit to the workload's Spark avoids NoSuchMethodError. Returns {} when the
    version can't be detected (kit keeps its 3.5.x defaults).

    Delta artifact note: the id changed from `delta-core` (Spark 3.3/3.4) to `delta-spark`
    (Spark 3.5+); both id and version are mapped from the Spark minor.
    """
    # Spark minor -> (delta_artifact, delta_version). Conservative, widely-used pairings.
    spark_to_delta = {
        "3.2": ("delta-core", "2.0.2"),
        "3.3": ("delta-core", "2.3.0"),
        "3.4": ("delta-core", "2.4.0"),
        "3.5": ("delta-spark", "3.1.0"),
    }
    texts: list = []
    for name in ("build.sbt", "pom.xml", "build.gradle", "build.gradle.kts"):
        f = source_dir / name
        if f.is_file():
            try:
                texts.append(f.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                pass
    if not texts:
        return {}
    blob = "\n".join(texts)

    # Spark version: e.g. spark-sql % "3.3.2", or spark-core:3.3.2, etc.
    m = re.search(r"spark-(?:sql|core)[^0-9]{1,40}?(\d+\.\d+\.\d+)", blob)
    spark_ver = m.group(1) if m else ""
    # Scala version (binary 2.12/2.13 is what matters for the kit).
    sm = re.search(r"scalaVersion\s*:?=?\s*[\"']?(\d+\.\d+\.\d+)", blob)
    scala_ver = sm.group(1) if sm else ""

    env: dict = {}
    if spark_ver:
        minor = ".".join(spark_ver.split(".")[:2])
        if minor in spark_to_delta and minor != "3.5":
            # Only override when the workload is NOT on the kit's default 3.5 line.
            artifact, dver = spark_to_delta[minor]
            env["SCOS_KIT_SPARK_VERSION"] = spark_ver
            env["SCOS_KIT_DELTA_ARTIFACT"] = artifact
            env["SCOS_KIT_DELTA_VERSION"] = dver
    if scala_ver and scala_ver.startswith(("2.12", "2.13")):
        # Keep within the kit's supported 2.12/2.13 range; pin the exact patch the
        # workload used so its bytecode loads cleanly.
        env["SCOS_KIT_SCALA_VERSION"] = scala_ver
    if env:
        print(f"[scos-control] aligning kit to source versions for Phase A: {env}")
    return env


def _build_source_jar(source_dir: Path) -> str:
    """Build the ORIGINAL source workload into a runnable jar for Phase A (local Spark).

    Detects the build tool and prefers an assembly/fat jar (so the workload's own
    dependencies are on the classpath; Spark/Delta are provided by the kit). Returns
    the absolute jar path, or "" if no build tool is found / the build produced no jar.
    Raises no exception on build failure — the caller records a per-trial failure instead.
    """
    if not source_dir.is_dir():
        return ""
    # Java workloads use Maven or Gradle only — sbt is never used for the workload itself.
    # (sbt is still used for the shared ScalaTest kit in prewarm/run-phase-a.)
    is_maven = (source_dir / "pom.xml").is_file()
    is_gradle = (source_dir / "build.gradle").is_file() or (source_dir / "build.gradle.kts").is_file()

    log_path = source_dir / "scos_source_build.log"

    def _run(cmd: list) -> int:
        print(f"[scos-control] building source jar: {' '.join(cmd)} (cwd={source_dir})")
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n=== {' '.join(cmd)} ===\n")
            r = subprocess.run(cmd, cwd=str(source_dir), stdout=lf,
                               stderr=subprocess.STDOUT)
        return r.returncode

    if is_maven and shutil.which("mvn"):
        _run(["mvn", "-q", "-DskipTests", "package"])
    elif is_gradle:
        gradle = "./gradlew" if (source_dir / "gradlew").is_file() else (
            "gradle" if shutil.which("gradle") else "")
        if gradle:
            if _run([gradle, "shadowJar"]) != 0:
                _run([gradle, "assemble"])
    else:
        print(f"[scos-control] WARNING: no usable build tool for source at {source_dir} "
              "(need mvn/gradle) — Phase A cannot produce a baseline", file=sys.stderr)
        return ""

    jar = _find_built_jar(source_dir)
    if jar:
        print(f"[scos-control] built source jar: {jar}")
    else:
        print(f"[scos-control] WARNING: source build produced no jar (see {log_path}); "
              "Phase A baseline will be unavailable", file=sys.stderr)
    return jar


def _stage_scos_client_jar(tests_dir: Path, conv_root: Path) -> str:
    """Ensure the SCOS Scala client jar is present in tests/lib/ for Phase B.

    The kit loads `com.snowflake.snowpark_connect.client.SnowparkConnectSession` via
    reflection from the kit classpath (unmanagedJars in tests/lib/). Without this jar,
    Phase B fails with ClassNotFoundException. The jar is not on Maven Central; it is
    resolved (in order) from the migrated Output's lib/, the local Maven repo, or the
    Coursier cache — wherever the migrate build already placed it. No version is hardcoded.
    Returns the staged jar path, or "" if none could be located.

    Also stages spark-connect-client-jvm_2.12 from Coursier cache if not already present.
    This JAR must be in lib/ BEFORE managed deps on the classpath (see build.sbt
    Test/externalDependencyClasspath override) so SparkSession.Builder.remote() resolves
    correctly. Without it Phase B fails with NoSuchMethodError: remote(String).
    """
    lib_dir = tests_dir / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)

    # --- spark-connect-client-jvm (needed for SparkSession.Builder.remote()) -------
    if not list(lib_dir.glob("spark-connect-client-jvm*.jar")):
        sc_search = [
            Path.home() / ".cache" / "coursier" / "v1" / "https" / "repo1.maven.org"
                / "maven2" / "org" / "apache" / "spark",
            Path.home() / "Library" / "Caches" / "Coursier" / "v1" / "https" / "repo1.maven.org"
                / "maven2" / "org" / "apache" / "spark",
        ]
        sc_found: list = []
        for base in sc_search:
            if base.is_dir():
                sc_found.extend(base.rglob("spark-connect-client-jvm_2.12-*.jar"))
        sc_found = [j for j in sc_found if "sources" not in j.name and "javadoc" not in j.name]
        if sc_found:
            src = max(sc_found, key=lambda j: j.stat().st_mtime)
            dest = lib_dir / src.name
            shutil.copy2(src, dest)
            print(f"[scos-control] staged spark-connect-client-jvm -> tests/lib/{dest.name}")
        else:
            print("[scos-control] WARNING: spark-connect-client-jvm not found in Coursier "
                  "cache — Phase B may fail with NoSuchMethodError: remote(String). "
                  "Run `sbt update` in the tests/ directory to populate the cache.",
                  file=sys.stderr)

    # --- snowpark-connect-java-client (SCOS session entrypoint, via reflection) ----
    existing = list(lib_dir.glob("snowpark-connect-java-client*.jar"))
    if existing:
        return str(existing[0])

    search_globs = [
        conv_root / "Output" / "lib",
        Path.home() / ".m2" / "repository" / "com" / "snowflake" / "snowpark-connect-java-client_2.12",
        Path.home() / ".m2" / "repository" / "com" / "snowflake" / "snowpark-connect-java-client_2.13",
        Path.home() / "Library" / "Caches" / "Coursier" / "v1" / "https" / "repo1.maven.org"
            / "maven2" / "com" / "snowflake",
        Path.home() / ".cache" / "coursier" / "v1" / "https" / "repo1.maven.org"
            / "maven2" / "com" / "snowflake",
    ]
    found: list = []
    for base in search_globs:
        if base.is_dir():
            found.extend(base.rglob("snowpark-connect-java-client*.jar"))
    found = [j for j in found if "sources" not in j.name and "javadoc" not in j.name]
    if not found:
        print("[scos-control] WARNING: SCOS client jar (snowpark-connect-java-client) not "
              "found in Output/lib, ~/.m2, or Coursier cache — Phase B will fail with "
              "ClassNotFoundException", file=sys.stderr)
        return ""
    # Newest by mtime.
    src_jar = max(found, key=lambda j: j.stat().st_mtime)
    dest = lib_dir / src_jar.name
    shutil.copy2(src_jar, dest)
    print(f"[scos-control] staged SCOS client jar -> tests/lib/{dest.name}")

    # Deequ (and other workload deps dropped from the assembly) — needed for BOTH
    # phases, so it lives in its own helper called from run-phase-a and run-phase-b.
    _stage_deequ_if_needed(tests_dir, conv_root)

    return str(dest)


def _stage_deequ_if_needed(tests_dir: Path, conv_root: Path) -> None:
    """Stage the workload's Deequ jar into tests/lib/ when the workload declares it.

    Deequ classes are often dropped from the workload assembly by the shade/assembly
    plugin's merge strategy when a transitive dep also carries them. Re-running the
    build does NOT fix this (the merge drops them again), so the class fails to load
    with NoClassDefFoundError: com/amazon/deequ/VerificationResult at class-load time —
    even when validate() is never called, because the JVM resolves all referenced types
    when the class (e.g. Silver) is loaded.

    This applies to BOTH phases: Phase A loads the original source's Silver class on
    local Spark; Phase B loads the migrated Output's class on the SCOS server (through
    the shared Scala ScalaTest kit). Stage the workload's declared Deequ jar from the
    Coursier/Ivy/Maven cache (same mechanism as the SCOS client jars).
    """
    lib_dir = tests_dir / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    if list(lib_dir.glob("deequ*.jar")):
        return  # already staged
    build_files = list((conv_root / "Output").glob("pom.xml")) + \
                  list((conv_root / "Output").glob("build.gradle*")) + \
                  list((conv_root / "Output").glob("build.sbt"))
    needs_deequ = any(
        "deequ" in f.read_text(encoding="utf-8", errors="ignore").lower()
        for f in build_files if f.is_file()
    )
    if not needs_deequ:
        return
    deequ_search = [
        Path.home() / ".m2" / "repository" / "com" / "amazon" / "deequ",
        Path.home() / ".cache" / "coursier" / "v1" / "https" / "repo1.maven.org"
            / "maven2" / "com" / "amazon" / "deequ",
        Path.home() / "Library" / "Caches" / "Coursier" / "v1" / "https"
            / "repo1.maven.org" / "maven2" / "com" / "amazon" / "deequ",
        Path.home() / ".ivy2" / "cache" / "com.amazon" / "deequ",
    ]
    deequ_found: list = []
    for b in deequ_search:
        if b.is_dir():
            deequ_found.extend(b.rglob("deequ*.jar"))
    deequ_found = [
        j for j in deequ_found
        if "sources" not in j.name and "javadoc" not in j.name
        and "scala-2.11" not in str(j)
    ]
    if deequ_found:
        dj = max(deequ_found, key=lambda j: j.stat().st_mtime)
        shutil.copy2(dj, lib_dir / dj.name)
        print(f"[scos-control] staged Deequ jar -> tests/lib/{dj.name}")
    else:
        print("[scos-control] WARNING: Deequ declared in build but no deequ*.jar in "
              "Maven/Coursier/Ivy cache — a class referencing com.amazon.deequ may "
              "fail to load (NoClassDefFoundError). Copy the jar to tests/lib/ "
              "manually.", file=sys.stderr)


def _clear_trial_outputs(trial_dir: Path) -> None:
    """Remove stale per-trial capture state so a partial/crashed re-run never shows
    prior-iteration outputs (mirrors the PySpark harness driver._clear_trial_outputs).

    The shared Scala kit fixture writes ``_index.json`` + ``tables/`` per trial and the
    comparator writes ``*_diff.json`` into the same dir. If a rerun's build fails to
    re-execute a spec (compile error, JVM abort), those old files would leak into the
    new result and be counted as this iteration's baseline/capture. Clear them before
    (re)rendering.
    """
    trial_dir = Path(trial_dir)
    trial_dir.mkdir(parents=True, exist_ok=True)
    for filename in (
        "_harness_status.json",
        "_index.json",
        "_manual_review.json",
        "workload_error.txt",
        "capture_error.txt",
    ):
        p = trial_dir / filename
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    for dirname in ("tables", "artifacts", "diffs", "stage_snapshot"):
        p = trial_dir / dirname
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    # Stale per-table comparator diffs live directly in the trial dir too.
    for diff in trial_dir.glob("*_diff.json"):
        try:
            diff.unlink()
        except OSError:
            pass


def _render_spec(template: str, ep: dict, source_jar: str, migrated_jar: str,
                 trial_dir: str, phase_a_dir: str, analysis_json: str,
                 state_json: str, schemas_dir: str = "",
                 extra_classpath_source: str = "",
                 extra_classpath_migrated: str = "") -> str:
    """Render one TestTemplate.scala.tmpl substituting all {{TOKEN}} placeholders.

    Both the original-source jar (Phase A, local Spark) and the migrated Output jar
    (Phase B, SCOS) are baked in; the rendered spec selects between them at runtime
    via SCOS_FLAVOR so the same spec drives both phases. ``extra_classpath_*``
    mirrors the Scala validator's thin-jar dependency classpath (populated once
    build-doctor is ported; empty until then — the template treats an empty
    string as "no extra classpath", not a literal path). Every {{TOKEN}} in the
    template MUST have an entry below: an unsubstituted token silently becomes
    literal text inside a Scala string literal (compiles, but is functionally
    wrong at runtime) rather than a render-time error.
    """
    ep_id = ep["id"]
    class_name = f"Test{_snake_to_camel(ep_id)}Spec"
    entry_class = ep.get("entrypoint_class", "")
    entry_method = ep.get("entrypoint_method", "main")

    # ENTRYPOINT_ARGS: prefer cli_args list; fall back to entrypoint_kwargs dict
    cli_args = ep.get("cli_args") or []
    kwargs = ep.get("entrypoint_kwargs") or {}
    if cli_args:
        flat = list(cli_args)
    elif kwargs:
        flat = []
        for k, v in kwargs.items():
            flat.extend([f"--{k}", str(v)])
    else:
        flat = []
    def _scala_str(s: str) -> str:
        """Escape a Python string value for safe embedding in a Scala string literal."""
        return s.replace("\\", "\\\\").replace('"', '\\"')

    if flat:
        args_literal = "Array(" + ", ".join(f'"{_scala_str(a)}"' for a in flat) + ")"
    else:
        args_literal = "Array.empty[String]"

    # WIDGET_ENV_VARS: Map("KEY" -> "VALUE", ...)
    widget_vars = ep.get("widget_env_vars") or {}
    widget_literal = ", ".join(
        f'"{_scala_str(k)}" -> "{_scala_str(v)}"' for k, v in widget_vars.items()
    )

    tokens = {
        "{{EP_ID}}": ep_id,
        "{{CLASS_NAME}}": class_name,
        "{{JAR_PATH_SOURCE}}": source_jar,
        "{{JAR_PATH_MIGRATED}}": migrated_jar,
        "{{EXTRA_CLASSPATH_SOURCE}}": extra_classpath_source,
        "{{EXTRA_CLASSPATH_MIGRATED}}": extra_classpath_migrated,
        "{{ENTRY_CLASS_NAME}}": entry_class,
        "{{ENTRY_METHOD_NAME}}": entry_method,
        "{{ENTRYPOINT_ARGS}}": args_literal,
        "{{TRIAL_DIR}}": trial_dir,
        "{{PHASE_A_DIR}}": phase_a_dir,
        "{{WIDGET_ENV_VARS}}": widget_literal,
        "{{ANALYSIS_JSON_PATH}}": analysis_json,
        "{{SCHEMAS_DIR_PATH}}": schemas_dir,
        "{{STATE_JSON_PATH}}": state_json,
    }
    result = template
    for tok, val in tokens.items():
        result = result.replace(tok, val)
    return result


def _cmd_run_phase_a(args) -> int:
    """Deterministic Phase A runner — produces the local baseline.

    Phase A runs the ORIGINAL source (Validation/source, plain SparkSession) on local
    Spark+Delta against seeded mocks. It must NOT run the migrated Output (which uses
    SnowparkConnectSession and cannot run on local Spark — that is Phase B's job).

    1. Stage the kit (rsync/shutil, same as prewarm).
    2. Build the ORIGINAL source jar (Validation/source) for the local baseline.
    3. Resolve the migrated Output jar (baked into the spec for Phase B reuse).
    4. Render one Test<EpId>Spec.scala per selected trial from TestTemplate
       (both jars baked in; SCOS_FLAVOR selects at runtime).
    5. Run `sbt test` (SCOS_FLAVOR=source) and record results in state.json.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    analysis = load_analysis(conv_root)
    workspace = validation_root(conv_root)
    tests_dir = workspace / "tests"
    results_dir = workspace / "results" / "phase_a"
    skill_dir = Path(__file__).resolve().parent.parent

    # 1. Stage kit -----------------------------------------------------------
    kit_src = skill_dir.parent / "validate-spark-scala-to-snowpark-connect" / "harness-scala" / "kit"
    if not kit_src.is_dir():
        return _die(f"kit not found: {kit_src}", 2)
    tests_dir.mkdir(parents=True, exist_ok=True)
    if shutil.which("rsync"):
        subprocess.run(
            ["rsync", "-a", "--exclude", "target/", "--exclude", "project/target/",
             f"{kit_src}/", f"{tests_dir}/"],
            check=True,
        )
    else:
        shutil.copytree(kit_src, tests_dir, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("target", "project/target"))
    # Copy .gitignore template
    gi_src = kit_src / ".gitignore.template"
    if gi_src.is_file():
        shutil.copy2(gi_src, tests_dir / ".gitignore")

    # Phase A must NOT have spark-connect-client-jvm on the classpath: its SPI
    # registration replaces SparkSession with the SCOS remote version, causing
    # buildLocalSession to connect to SCOS instead of creating a local session.
    # Phase B's run-phase-b re-stages it via _stage_scos_client_jar.
    for stale in (tests_dir / "lib").glob("spark-connect-client-jvm*.jar"):
        stale.unlink()

    # Deequ (and similar deps dropped from the assembly by the shade/merge plugin) is
    # needed for Phase A too: loading the ORIGINAL source's Silver class on local Spark
    # resolves com.amazon.deequ types at class-load time. Without it Phase A fails with
    # NoClassDefFoundError before producing a baseline.
    _stage_deequ_if_needed(tests_dir, conv_root)

    # 2. Build the ORIGINAL source jar (the local baseline) ------------------
    # Phase A runs the original source on local Spark. The source has been patched
    # (mock I/O + injectable session) and uses plain SparkSession, so it runs locally.
    source_dir = conv_root / "Validation" / "source"
    source_jar = _build_source_jar(source_dir)
    if not source_jar:
        print("[scos-control] WARNING: no source jar built — Phase A specs will run but "
              "produce no baseline; trials will fall back to passed_no_baseline in Phase B",
              file=sys.stderr)

    # 3. Resolve the migrated Output jar (baked into the spec for Phase B) ----
    jar_rel = analysis.get("jar_path", "") or ""
    migrated_jar = str((conv_root / jar_rel).resolve()) if jar_rel else _find_workload_jar(conv_root)
    if migrated_jar and not Path(migrated_jar).is_file():
        print(f"[scos-control] WARNING: migrated jar not found at {migrated_jar}; "
              "Phase B will fail — build Output/ first", file=sys.stderr)
    elif not migrated_jar:
        print("[scos-control] WARNING: no migrated jar in analysis.json or Output/target/; "
              "Phase B will fail with ClassNotFoundException", file=sys.stderr)
    # Workload jars are loaded by ReflectionEntrypoint via absolute path — they are NOT
    # placed in tests/lib/ (which is the kit compile classpath and must stay clean).

    # 4. Render test specs ---------------------------------------------------
    template_path = tests_dir / "templates" / "TestTemplate.scala.tmpl"
    if not template_path.is_file():
        return _die(f"TestTemplate not found: {template_path} — "
                    "did the kit copy succeed?", 2)
    template = template_path.read_text(encoding="utf-8")
    spec_dir = tests_dir / "src" / "test" / "scala"
    spec_dir.mkdir(parents=True, exist_ok=True)

    analysis_json_path = str(conv_root / "Validation" / "shared" / "analysis.json")
    state_json_path = str(conv_root / "Validation" / "state.json")

    trials = state.get("trials", {})
    eps_by_id = {ep["id"]: ep for ep in ensure_entrypoints_list(analysis) if ep.get("id")}
    rendered: list = []
    for tid in list(trials.keys()):
        ep = eps_by_id.get(tid)
        if not ep:
            print(f"[scos-control] WARNING: no entrypoint for trial {tid} in "
                  "analysis.json — skipping spec render")
            continue
        trial_dir_str = str(results_dir / tid)
        # Clear stale per-trial outputs so a partial/crashed re-run never reuses a
        # prior iteration's baseline (_index.json/tables/).
        _clear_trial_outputs(results_dir / tid)
        spec_content = _render_spec(
            template=template, ep=ep,
            source_jar=source_jar or "", migrated_jar=migrated_jar or "",
            trial_dir=trial_dir_str, phase_a_dir=trial_dir_str,
            analysis_json=analysis_json_path, state_json=state_json_path,
            schemas_dir=str(conv_root / "Validation" / "shared" / "schemas"),
        )
        class_name = f"Test{_snake_to_camel(tid)}Spec"
        spec_path = spec_dir / f"{class_name}.scala"
        spec_path.write_text(spec_content, encoding="utf-8")
        rendered.append(tid)
        print(f"[scos-control] rendered {spec_path.name}")

    if not rendered:
        return _die("no specs rendered — verify analysis.json entrypoint ids match "
                    "state.json trials", 2)

    state.setdefault("milestones", {})["tests_authored"] = True
    save_state(conv_root, state)
    append_event(workspace, {"kind": "milestone_completed", "milestone": "tests_authored"})

    # 5. Run sbt test --------------------------------------------------------
    if not shutil.which("sbt"):
        print("[scos-control] WARNING: sbt not on PATH — specs rendered but not executed; "
              "run `sbt test` manually from " + str(tests_dir))
        return 0

    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "sbt_source.log"

    # Align the kit's Spark/Delta/Scala to the workload's so the original (already-compiled)
    # bytecode runs without Catalyst/Delta binary-signature mismatches (Phase A only).
    kit_versions = _detect_source_versions(source_dir)
    sbt_env = {
        **os.environ,
        **kit_versions,
        "SCOS_FLAVOR": "source",
        "SCOS_TEST_PARALLELISM": str(args.parallelism),
        "SCOS_RESULTS_DIR": str(results_dir),
        "SCOS_CONV_ROOT": str(conv_root),
        "SCOS_ANALYSIS_JSON": analysis_json_path,
        "SCOS_STATE_JSON": state_json_path,
        "SCOS_MOCK_DATA_DIR": str(conv_root / "Validation" / "shared" / "mock_data"),
    }

    print(f"[scos-control] sbt test (Phase A) -> {log_path}")
    with open(log_path, "w", encoding="utf-8") as log_f:
        sbt_result = subprocess.run(
            ["sbt", "-batch", "test"],
            cwd=str(tests_dir),
            env=sbt_env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
        )
    sbt_rc = sbt_result.returncode
    print(f"[scos-control] sbt test exited: {sbt_rc}")

    # 6. Record iter per trial -----------------------------------------------
    state = load_state(conv_root)
    for tid in rendered:
        index_file = results_dir / tid / "_index.json"
        passing = failing = 0
        if index_file.is_file():
            try:
                idx = json.loads(index_file.read_text(encoding="utf-8"))
                passing = len(idx.get("tables") or [])
                failing = len(idx.get("failures") or [])
            except Exception:  # noqa: BLE001
                failing = 1
        else:
            failing = 1  # sbt ran but produced no index → compile/runtime failure
        iters = state["trials"].get(tid, {}).get("phase_a_iters") or []
        iters.append({
            "iter": len(iters) + 1, "phase": "phase_a", "fix_category": "initial",
            "passing": passing, "failing": failing,
            "notes": f"run-phase-a sbt rc={sbt_rc}", "ts": now_iso(),
        })
        state["trials"].setdefault(tid, {})["phase_a_iters"] = iters

    save_state(conv_root, state)
    append_event(workspace, {"kind": "phase_a_complete", "sbt_rc": sbt_rc,
                             "trials": rendered})
    print(f"[scos-control] Phase A done: {len(rendered)} trial(s), sbt_rc={sbt_rc}")
    return sbt_rc


def _cmd_run_phase_b(args) -> int:
    """Deterministic Phase B runner.

    1. Stage the SCOS client JAR into tests/lib/.
    2. Run `sbt test` (SCOS_FLAVOR=migrated) and record results in state.json.

    Connection model (local-server mode): SnowparkConnectSession.builder().getOrCreate()
    launches a local Python server from SNOWPARK_CONNECT_PYTHON_VENV; that server resolves
    the Snowflake connection (we point it at the configured connection via
    SNOWFLAKE_DEFAULT_CONNECTION_NAME). SPARK_REMOTE is intentionally NOT set — setting it
    would force remote mode and bypass the local server. The JVM client does not read
    connections.toml itself, so the venv + default connection are what make it connect.

    This mirrors PySpark's validate.py Phase B path but for Scala/sbt.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    workspace = validation_root(conv_root)
    tests_dir = workspace / "tests"
    results_dir = workspace / "results" / "phase_b"

    if not tests_dir.is_dir():
        return _die("tests/ not found — run `prewarm` or `run-phase-a` first", 2)

    # 0. Auto-provision golden schemas when missing (PySpark parity) ----------
    prov_rc, state = _provision_golden_schemas(conv_root, state)
    if prov_rc != 0:
        return prov_rc

    # 1. Resolve the Snowflake connection for the local Python server ---------
    config = state.get("config", {})
    conn_name = config.get("connection_name", "")
    if conn_name:
        print(f"[scos-control] Phase B will use connection '{conn_name}' "
              "(local-server mode; SPARK_REMOTE not set)")
    else:
        print("[scos-control] WARNING: no connection_name in state.json — the local SCOS "
              "Python server will fall back to the default connection; Phase B may fail to "
              "authenticate", file=sys.stderr)
    # 2. Stage + verify SCOS client JAR -------------------------------------
    # The kit loads SnowparkConnectSession via reflection from the kit classpath
    # (tests/lib/). Stage it from Output/lib, ~/.m2, or the Coursier cache.
    _stage_scos_client_jar(tests_dir, conv_root)
    lib_dir = tests_dir / "lib"
    scos_jars = list(lib_dir.glob("*snowpark-connect-java-client*.jar")) if lib_dir.is_dir() else []
    if not scos_jars:
        print("[scos-control] WARNING: SCOS client JAR not found/staged in tests/lib/; "
              "sbt test will fail with ClassNotFoundException: "
              "com.snowflake.snowpark_connect.client.SnowparkConnectSession",
              file=sys.stderr)

    # 2b. Verify workload JAR exists — mirrors Phase A step 3. ----------------
    _analysis_path = conv_root / "Validation" / "shared" / "analysis.json"
    if _analysis_path.exists():
        try:
            _a = json.loads(_analysis_path.read_text(encoding="utf-8"))
            _jar_rel = _a.get("jar_path", "") or ""
            _migrated_jar = (
                str((conv_root / _jar_rel).resolve()) if _jar_rel
                else _find_workload_jar(conv_root)
            )
            if _migrated_jar and not Path(_migrated_jar).is_file():
                print(
                    f"[scos-control] WARNING: workload JAR not found at {_migrated_jar}; "
                    "sbt test will fail — run sbt assembly in Output/ first",
                    file=sys.stderr,
                )
            elif not _migrated_jar:
                print(
                    "[scos-control] WARNING: no workload JAR in analysis.json jar_path "
                    "or Output/target/; sbt test will fail — run sbt assembly in Output/ first",
                    file=sys.stderr,
                )
        except Exception:
            pass  # malformed analysis.json — sbt test will surface its own error

    # 3a. Re-render specs with phase_b trial_dir ----------------------------------
    # Phase A rendered specs with TRIAL_DIR = phase_a/<tid> and PHASE_A_DIR = phase_a/<tid>.
    # For Phase B we need TRIAL_DIR = phase_b/<tid> (captures go here) but
    # PHASE_A_DIR = phase_a/<tid> (baseline for comparison). Without re-rendering,
    # Phase B writes into the Phase A directory and comparePhases trivially passes.
    template_path = tests_dir / "templates" / "TestTemplate.scala.tmpl"
    if template_path.is_file():
        analysis_json_path_b = str(conv_root / "Validation" / "shared" / "analysis.json")
        state_json_path_b    = str(conv_root / "Validation" / "state.json")
        template_b = template_path.read_text(encoding="utf-8")
        spec_dir_b = tests_dir / "src" / "test" / "scala"
        spec_dir_b.mkdir(parents=True, exist_ok=True)
        analysis_b = load_analysis(conv_root)
        eps_by_id_b = {ep["id"]: ep for ep in ensure_entrypoints_list(analysis_b) if ep.get("id")}
        source_jar_b  = str((conv_root / "Validation" / "source" / "target").glob("*assembly*.jar").__next__()) \
                        if any((conv_root / "Validation" / "source" / "target").glob("*assembly*.jar")) else ""
        migrated_jar_b = str(next(iter((conv_root / "Output" / "target").rglob("*assembly*.jar")), ""))
        rerendered: list = []
        for tid_b in list(state.get("trials", {}).keys()):
            ep_b = eps_by_id_b.get(tid_b)
            if not ep_b:
                continue
            trial_dir_b   = str(results_dir / tid_b)          # phase_b/<tid>
            phase_a_dir_b = str(conv_root / "Validation" / "results" / "phase_a" / tid_b)
            # Clear stale Phase B outputs (prior capture/diffs) before re-running.
            # NOTE: only the phase_b/<tid> dir — never the phase_a baseline it compares against.
            _clear_trial_outputs(results_dir / tid_b)
            spec_content_b = _render_spec(
                template=template_b, ep=ep_b,
                source_jar=source_jar_b, migrated_jar=migrated_jar_b,
                trial_dir=trial_dir_b, phase_a_dir=phase_a_dir_b,
                analysis_json=analysis_json_path_b, state_json=state_json_path_b,
                schemas_dir=str(conv_root / "Validation" / "shared" / "schemas"),
            )
            class_name_b = f"Test{_snake_to_camel(tid_b)}Spec"
            (spec_dir_b / f"{class_name_b}.scala").write_text(spec_content_b, encoding="utf-8")
            rerendered.append(tid_b)
        if rerendered:
            print(f"[scos-control] re-rendered {len(rerendered)} spec(s) for Phase B "
                  f"(TRIAL_DIR → phase_b): {rerendered}")
            # Force sbt to recompile the re-rendered specs by removing compiled test classes.
            # This avoids stale bytecode (Phase A compiled against Spark 3.3.x; Phase B
            # needs Spark 3.5.x for .remote()).
            import shutil as _shutil
            for stale in (tests_dir / "target" / "scala-2.12" / "test-classes",):
                if stale.is_dir():
                    _shutil.rmtree(stale, ignore_errors=True)

    # 3. Run sbt test --------------------------------------------------------
    if not shutil.which("sbt"):
        return _die("sbt not on PATH; cannot run Phase B", 2)

    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "sbt_migrated.log"

    analysis_json_path = str(conv_root / "Validation" / "shared" / "analysis.json")
    state_json_path = str(conv_root / "Validation" / "state.json")

    # SNOWPARK_CONNECT_PYTHON_VENV: find the skill-level venv if not already exported.
    # This is what lets the JVM client launch the local Python SCOS server.
    venv_path = os.environ.get("SNOWPARK_CONNECT_PYTHON_VENV", "")
    if not venv_path:
        for candidate in [
            Path(__file__).resolve().parent.parent / ".venv",
            Path(__file__).resolve().parent.parent.parent / ".venv",
        ]:
            if (candidate / "bin" / "python3").exists():
                venv_path = str(candidate)
                break

    sbt_env = {
        **os.environ,
        "SCOS_FLAVOR": "migrated",
        "SCOS_TEST_PARALLELISM": str(args.parallelism),
        "SCOS_RESULTS_DIR": str(results_dir),
        "SCOS_CONV_ROOT": str(conv_root),
        "SCOS_ANALYSIS_JSON": analysis_json_path,
        "SCOS_STATE_JSON": state_json_path,
        "SCOS_MOCK_DATA_DIR": str(conv_root / "Validation" / "shared" / "mock_data"),
    }
    if venv_path:
        sbt_env["SNOWPARK_CONNECT_PYTHON_VENV"] = venv_path
    if conn_name:
        # The local Python SCOS server resolves the Snowflake connection from this.
        sbt_env["SNOWFLAKE_DEFAULT_CONNECTION_NAME"] = conn_name
    # Do NOT set SPARK_REMOTE — that would force remote mode and bypass the local server.
    sbt_env.pop("SPARK_REMOTE", None)

    # Auto-detect Nix libstdc++.so.6 so grpc (used by the SCOS Python server) can load.
    # On this aarch64 Linux host, libstdc++ lives in the Nix store but is not in the
    # default LD_LIBRARY_PATH, causing grpc._cython.cygrpc to fail with an ImportError.
    import glob as _glob
    _nix_libstd = next(iter(_glob.glob(
        "/nix/store/*-gcc-*-lib/lib/libstdc++.so.6")), None)
    if _nix_libstd:
        _nix_dir = str(Path(_nix_libstd).parent)
        _existing_ldpath = sbt_env.get("LD_LIBRARY_PATH", "")
        if _nix_dir not in _existing_ldpath:
            sbt_env["LD_LIBRARY_PATH"] = f"{_nix_dir}:{_existing_ldpath}".rstrip(":")
            print(f"[scos-control] auto-injected Nix libstdc++ into LD_LIBRARY_PATH: {_nix_dir}")

    print(f"[scos-control] sbt test (Phase B) -> {log_path}")
    with open(log_path, "w", encoding="utf-8") as log_f:
        sbt_result = subprocess.run(
            # Use testOnly to exclude KitSpec: it fails on the SCOS sidecar port (15002)
            # which is not available in local-server mode.
            ["sbt", "-batch", "testOnly com.snowflake.scos.kit.generated.*"],
            cwd=str(tests_dir),
            env=sbt_env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
        )
    sbt_rc = sbt_result.returncode
    print(f"[scos-control] sbt test exited: {sbt_rc}")

    # 4. Record iter per trial -----------------------------------------------
    state = load_state(conv_root)
    trials = state.get("trials", {})
    recorded: list = []
    for tid in trials:
        index_file = results_dir / tid / "_index.json"
        passing = failing = 0
        if index_file.is_file():
            try:
                idx = json.loads(index_file.read_text(encoding="utf-8"))
                passing = len(idx.get("tables") or [])
                failing = len(idx.get("failures") or [])
            except Exception:  # noqa: BLE001
                failing = 1
        else:
            failing = 1
        iters = trials[tid].get("phase_b_iters") or []
        iters.append({
            "iter": len(iters) + 1, "phase": "phase_b", "fix_category": "initial",
            "passing": passing, "failing": failing,
            "notes": f"run-phase-b sbt rc={sbt_rc}", "ts": now_iso(),
        })
        trials[tid]["phase_b_iters"] = iters
        recorded.append(tid)

    save_state(conv_root, state)
    append_event(workspace, {"kind": "phase_b_complete", "sbt_rc": sbt_rc,
                             "trials": recorded})
    print(f"[scos-control] Phase B done: {len(recorded)} trial(s), sbt_rc={sbt_rc}")
    return sbt_rc


def _needs_provision(state: dict) -> bool:
    """True when golden schemas are missing for any selected trial."""
    trials = state.get("trials") or {}
    if not trials:
        return False
    golden = (state.get("snowflake") or {}).get("golden_schemas") or {}
    if not state.get("snowflake", {}).get("provisioned"):
        return True
    return any(tid not in golden or not (golden.get(tid) or {}).get("schema")
               for tid in trials)


def _provision_golden_schemas(conv_root: Path, state: dict | None = None) -> tuple[int, dict]:
    """Provision Snowflake golden schemas in-process.

    Returns (exit_code, updated_state). Exit 0 on success or when already provisioned.
    """
    conv_root = conv_root.expanduser().resolve()
    state = state or load_state(conv_root)
    workspace = validation_root(conv_root)

    if not _needs_provision(state):
        print("[scos-control] golden schemas already provisioned — skipping")
        return 0, state

    config = state.get("config", {})
    conn_name = config.get("connection_name", "")
    project_slug_val = config.get("project_slug", "")
    run_id = state.get("run_id", "")
    database = (
        (state.get("snowflake") or {}).get("database")
        or config.get("database", "SCOS_VALIDATION")
    )

    for label, val in (
        ("config.connection_name", conn_name),
        ("config.project_slug", project_slug_val),
        ("run_id", run_id),
    ):
        if not val:
            return _die(f"{label} missing in state.json", 2), state

    schemas_dir = workspace / "shared" / "schemas"
    mock_data_dir = workspace / "shared" / "mock_data"
    if not schemas_dir.is_dir():
        return _die("shared/schemas/ not found — run schema_mine.py first", 2), state

    _pyspark_scripts = Path(__file__).resolve().parent.parent.parent \
                       / "validate-pyspark-to-snowpark-connect" / "scripts"
    _harness = _pyspark_scripts / "harness"
    _runtimes = _harness / "runtimes"
    for p in (_harness, _runtimes, _pyspark_scripts):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    try:
        from helpers import load_entrypoint  # type: ignore[import-not-found]
    except ImportError as exc:
        return _die(f"cannot import helpers from PySpark harness: {exc}", 2), state

    trials = state.get("trials") or {}
    ep_ids = list(trials.keys()) if isinstance(trials, dict) else [
        t["id"] for t in trials if isinstance(t, dict)
    ]
    if not ep_ids:
        return _die("no selected trials in state.json — run select-entrypoints first", 2), state

    entrypoints = [e for e in (load_entrypoint(str(schemas_dir), eid) for eid in ep_ids) if e]
    if not entrypoints:
        return _die("no loadable entrypoints in shared/schemas/ — re-run schema_mine.py", 2), state

    try:
        import snowflake.connector as sf  # type: ignore[import-not-found]
    except ImportError:
        return _die("snowflake-connector-python not installed", 2), state

    print(f"Connecting to Snowflake (connection={conn_name!r})…")
    try:
        conn = sf.connect(connection_name=conn_name)
    except Exception as exc:
        return _die(f"Snowflake connection failed: {exc}", 3), state

    conn_params = {"connection_name": conn_name}
    try:
        from runtimes._scos_provision import provision_golden_schemas  # type: ignore[import-not-found]
        golden = provision_golden_schemas(
            conn, conn_params, entrypoints, mock_data_dir,
            project_slug_val, run_id, database,
        )
    except SystemExit:
        raise
    except RuntimeError as exc:
        return _die(str(exc), 4), state
    except Exception as exc:
        if hasattr(sf, "errors") and isinstance(exc, sf.errors.ProgrammingError):
            return _die(f"SQL ERROR: {exc}", 4), state
        raise
    finally:
        conn.close()

    state.setdefault("snowflake", {})
    state["snowflake"]["provisioned"] = True
    state["snowflake"]["database"] = database
    state["snowflake"]["golden_schemas"] = golden
    state.setdefault("milestones", {})["snowflake_provisioned"] = True
    save_state(conv_root, state)
    append_event(workspace, {"kind": "milestone_completed", "milestone": "snowflake_provisioned"})

    print(f"Provisioning complete: {len(golden)} entrypoint(s) in {database}")
    for eid, info in golden.items():
        print(f"  {eid}: {database}.{info['schema']}")
    return 0, state


def _cmd_provision(args) -> int:
    """Provision Snowflake golden schemas — the Scala equivalent of PySpark's
    ScosRuntime.provision() called automatically by driver.py before each trial.
    Reads state.json for connection config, loads selected entrypoints from
    shared/schemas/, calls provision_golden_schemas() in-process (same library as
    PySpark), and writes state["snowflake"]["golden_schemas"] + milestone back.

    Called from SKILL.md Step 6 after schema_mine.py + datagen:
        python scos_state.py provision --conv-root $CONVERSION_ROOT
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    rc, _ = _provision_golden_schemas(conv_root)
    return rc


def _cmd_put_schemas(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    analysis = load_analysis(conv_root)
    schemas_path = validation_root(conv_root) / "shared" / "schemas.json"
    schemas = {"external_sources": {}, "provisioned_tables": {}}
    if schemas_path.is_file():
        schemas.update(load_json(schemas_path))
        schemas.setdefault("external_sources", {})
        schemas.setdefault("provisioned_tables", {})
    moved = 0
    for src in (s for ep in ensure_entrypoints_list(analysis)
                for s in (ep.get("external_sources") or [])):
        schema_val = src.get("schema")
        if isinstance(schema_val, list):
            key = src.get("name") or (project_slug(src["subpath"]) if src.get("subpath") else "unknown")
            schemas["external_sources"][key] = schema_val
            moved += 1
    write_atomic(schemas_path, schemas)
    if moved > 0:
        save_analysis(conv_root, analysis)
    print(f"[scos-control] externalized {moved} schema(s) to schemas.json")
    return 0


def _cmd_document_divergence(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    analysis = load_analysis(conv_root)
    trials = state.get("trials") or {}
    if args.trial_id not in trials:
        return _die(f"trial '{args.trial_id}' not found in state.json")
    col = args.column.upper()
    entry = {
        "sink_id": args.sink_id, "column": col, "reason": args.reason,
        "baseline_sample": args.baseline_sample or "", "shadow_sample": args.shadow_sample or "",
        "documented_at_iter": args.iter if args.iter is not None else 0,
    }
    trial = trials[args.trial_id]
    existing = trial.get("documented_divergences") or []
    idx = next((i for i, d in enumerate(existing)
                if d.get("sink_id") == args.sink_id and (d.get("column") or "").upper() == col), -1)
    trial["documented_divergences"] = (existing[:idx] + [entry] + existing[idx + 1:]) if idx >= 0 else existing + [entry]
    save_state(conv_root, state)

    div_entry = {"column": col, "reason": args.reason, "baseline_sample": args.baseline_sample or "",
                 "shadow_sample": args.shadow_sample or "", "scope": "data"}
    sink_keys = {f"{args.trial_id}.{args.sink_id}"}
    norm = normalize_sink_name(args.sink_id)
    if norm:
        sink_keys.add(f"{args.trial_id}.{norm}")
    exp = analysis.get("expected_divergences") or {}
    for key in sink_keys:
        lst = exp.get(key) or []
        j = next((i for i, d in enumerate(lst) if (d.get("column") or "").upper() == col), -1)
        exp[key] = (lst[:j] + [div_entry] + lst[j + 1:]) if j >= 0 else lst + [div_entry]
    analysis["expected_divergences"] = exp
    save_analysis(conv_root, analysis)
    print(f"[scos-control] documented divergence: {args.trial_id}/{args.sink_id}/{col}")
    return 0


def _cmd_migrate_divergences(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    workspace = validation_root(conv_root)
    trials = state.get("trials") or {}
    ambiguous = migrated = 0
    for tid, trial in trials.items():
        divs = trial.get("documented_divergences") or []
        if not divs:
            continue
        phase_a_dir = workspace / "results" / "phase_a" / tid
        captures = {}
        if phase_a_dir.is_dir():
            for dd in phase_a_dir.iterdir():
                if dd.is_dir() and dd.name.startswith("write_"):
                    parts = dd.name.split("_", 2)
                    if len(parts) >= 3:
                        captures[dd.name] = parts[2]
        new_divs = []
        for div in divs:
            sink_id = div.get("sink_id", "")
            if not sink_id.startswith("write_"):
                migrated += 1
                new_divs.append(div)
            else:
                slug = captures.get(sink_id, "")
                if not slug:
                    print(f"MIGRATION_AMBIGUOUS: {sink_id} (trial={tid}) cannot be mapped to a table name.")
                    ambiguous += 1
                    new_divs.append(div)
                else:
                    migrated += 1
                    new_divs.append({**div, "sink_id": slug, "_migrated_from": sink_id})
        trial["documented_divergences"] = new_divs
    save_state(conv_root, state)
    print(f"[scos-control] divergence migration: {migrated} migrated, {ambiguous} ambiguous")
    return 1 if ambiguous > 0 else 0


def _cmd_mark_empty_baseline(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    trials = state.get("trials") or {}
    if args.trial_id not in trials:
        return _die(f"trial '{args.trial_id}' not found in state.json")
    trial = trials[args.trial_id]
    expected = trial.get("expected_empty_baselines") or []
    if args.sink_id in expected:
        print(f"[scos-control] sink '{args.sink_id}' already in expected_empty_baselines for {args.trial_id}")
        return 0
    trial["expected_empty_baselines"] = expected + [args.sink_id]
    save_state(conv_root, state)
    print(f"[scos-control] marked sink '{args.sink_id}' as expected-empty for {args.trial_id}")
    return 0


def _cmd_record_fixer_dispatch(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    entry = {
        "iter": args.iter, "error_class": args.error_class, "error_hash": args.error_hash,
        "trials_affected": [x.strip() for x in args.trial_ids.split(",")], "outcome": args.outcome,
    }
    state["fixer_dispatches"] = (state.get("fixer_dispatches") or []) + [entry]
    save_state(conv_root, state)
    print(f"[scos-control] fixer_dispatch recorded: iter={args.iter} class={args.error_class}")
    return 0


def _cmd_mark_unselected_dependency(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    trials = state.get("trials") or {}
    if args.trial_id not in trials:
        return _die(f"trial '{args.trial_id}' not found in state.json")
    fake = {"iter": args.iter if args.iter is not None else 0, "error_class": "unselected_dependency",
            "error_hash": args.reason[:80], "trials_affected": [args.trial_id], "outcome": "no_change"}
    state["fixer_dispatches"] = (state.get("fixer_dispatches") or []) + [fake]
    trials[args.trial_id] = {**trials[args.trial_id],
                             "status": "passed_no_baseline", "hard_stuck_reason": args.reason}
    state["trials"] = trials
    state = advance_phase(state)
    save_state(conv_root, state)
    append_event(validation_root(conv_root), {
        "kind": "trial_marked", "trial_id": args.trial_id,
        "status": "passed_no_baseline", "reason": args.reason,
    })
    print(f"[scos-control] {args.trial_id} marked passed_no_baseline (unselected_dependency): {args.reason}")
    return 0


def _cmd_record_patch(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    trials = state.get("trials") or {}
    if args.trial_id not in trials:
        return _die(f"trial '{args.trial_id}' not found in state.json")
    entry = {"phase": args.phase, "file": args.file, "reason": args.reason,
             "iter": args.iter, "diff_path": args.diff_path}
    trial = trials[args.trial_id]
    patch_key = f"{args.phase}_patches"
    trial[patch_key] = (trial.get(patch_key) or []) + [entry]
    save_state(conv_root, state)
    print(f"[scos-control] recorded patch: {args.trial_id}/{args.phase}/{args.file}")
    return 0


def _cmd_build_index(args) -> int:
    build_index(Path(args.conv_root).expanduser().resolve())
    return 0


def _git_commit_output(conv_root: Path, message: str) -> Optional[str]:
    """Stage conv_root/Output and commit. Returns the new SHA, or None when
    there was nothing to commit (mirrors validate.py _git_commit_output)."""
    _run_git(conv_root, "git", "add", str(conv_root / "Output"))
    if _run_git(conv_root, "git", "diff", "--cached", "--quiet").returncode == 0:
        return None
    if _run_git(conv_root, "git", "commit", "-m", message).returncode != 0:
        return None
    return _run_git(conv_root, "git", "rev-parse", "HEAD").stdout.strip() or None


def _cmd_patch_add(args) -> int:
    """Smoke-test + apply a batch of blueprint patches to BOTH the Phase A source
    copy and the Phase B Output copy, append them to patch_blueprint.json, and
    commit the Output/ side as one [TEST-PATCH] commit. Faithful to the PySpark
    validate.py patch-add handler."""
    # patch_engine is a canonical PySpark validator script (reused, not duplicated).
    _pyspark_scripts = (Path(__file__).resolve().parent.parent.parent
                        / "validate-pyspark-to-snowpark-connect" / "scripts")
    if str(_pyspark_scripts) not in sys.path:
        sys.path.insert(0, str(_pyspark_scripts))
    import patch_engine

    conv_root = Path(args.conv_root).expanduser().resolve()
    entry_path = Path(args.from_file).expanduser().resolve()
    if not entry_path.is_file():
        return _die(f"--from-file not found: {entry_path}")
    try:
        payload = json.loads(entry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _die(f"--from-file is not valid JSON: {exc}")

    if isinstance(payload, dict) and isinstance(payload.get("patches"), list):
        entries = payload["patches"]
    elif isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = [payload]
    else:
        return _die('--from-file must be an object, a list, or {"patches": [...]}')

    ok, results, written, deduped = patch_engine.add_patches(conv_root, entries)
    for r in results:
        label = f"{r.patch_id}/{r.side}" if r.patch_id else r.side
        detail = "" if r.ok else f" — {r.error}"
        print(f"[patch-add] {label} {r.file}: {'ok' if r.ok else 'FAIL'}{detail}")
    if not ok:
        return _die("patch batch rejected; nothing written")
    if deduped:
        print(f"[patch-add] skipped {len(deduped)} duplicate patch(es): {', '.join(deduped)}")

    applied_ids = [e.get("id") for e in entries if e.get("id") not in set(deduped)]
    append_event(validation_root(conv_root), {
        "kind": "patch_added", "patch_ids": applied_ids, "deduped_ids": deduped, "files": written,
    })
    if not args.no_commit:
        label = applied_ids[0] if len(applied_ids) == 1 else f"{len(applied_ids)} patches"
        # Commit BOTH sides (Output/ + Validation/source/) in one [TEST-PATCH] so a
        # later git revert undoes both. These stay on the validation branch only.
        sha = _git_commit_paths(
            conv_root, ["Output", str(Path(VALIDATION_DIRNAME) / "source")],
            f"[TEST-PATCH] {label}")
        print(f"[patch-add] committed [TEST-PATCH] {label}: {sha}" if sha
              else "[patch-add] no changes to commit (already applied)")
    print(f"[patch-add] applied {len(applied_ids)} patch(es) to {len(written)} file(s)"
          + (f"; {len(deduped)} deduped" if deduped else ""))
    return 0


# ---------------------------------------------------------------------------
# harvest — copy Validation/ onto the original branch, then cherry-pick
# [MIGRATION-FIX] commits. Mirrors validate.py cmd_harvest.
# ---------------------------------------------------------------------------

def _require_summary_before_harvest(conv_root: Path) -> None:
    workspace = validation_root(conv_root)
    if not (workspace / "results" / "summary.json").is_file():
        sys.exit(_die("results/summary.json missing — run `scos_state.py summary` before harvest", 1))
    if not (workspace / "run_index.json").is_file():
        sys.exit(_die("run_index.json missing — run `scos_state.py summary` before harvest", 1))


def _commit_validation_to_branch(conv_root: Path, branch: str) -> None:
    """Make the live Validation/ durable on *branch* BEFORE harvest switches away,
    so a kill mid-flight is recoverable via `git checkout <branch> -- Validation/`."""
    if not (conv_root / VALIDATION_DIRNAME).is_dir():
        sys.exit(_die(f"{VALIDATION_DIRNAME}/ not found under {conv_root}; run validation first", 1))
    sha = _git_commit_tree(conv_root, VALIDATION_DIRNAME,
                           f"[HARVEST] snapshot Validation/ on {branch} before switch")
    if sha:
        print(f"[scos-control] committed Validation/ onto {branch}: {sha}")


def _harvest_validation_workspace(conv_root: Path, validation_branch: str) -> Optional[str]:
    res = _run_git(conv_root, "git", "checkout", validation_branch, "--", VALIDATION_DIRNAME)
    if res.returncode != 0:
        sys.exit(_die(f"could not restore {VALIDATION_DIRNAME}/ from {validation_branch}: {res.stderr}", 1))
    sha = _git_commit_tree(conv_root, VALIDATION_DIRNAME,
                           f"[HARVEST] Validation workspace from {validation_branch}")
    print(f"[scos-control] committed Validation/ onto current branch: {sha}" if sha
          else "[scos-control] Validation/ unchanged on current branch (no commit)")
    return sha


def _cherry_pick_in_progress(conv_root: Path) -> bool:
    git_dir = _run_git(conv_root, "git", "rev-parse", "--git-dir").stdout.strip()
    if not git_dir:
        return False
    base = (conv_root / git_dir) if not os.path.isabs(git_dir) else Path(git_dir)
    return (base / "CHERRY_PICK_HEAD").exists() or (base / "sequencer").is_dir()


def _unmerged_paths(conv_root: Path) -> List[str]:
    out = _run_git(conv_root, "git", "diff", "--name-only", "--diff-filter=U").stdout
    return [f for f in out.splitlines() if f.strip()]


def _advance_cherry_pick(conv_root: Path) -> bool:
    """Drive an in-progress cherry-pick to completion, auto-skipping empty/redundant
    picks. Returns True when fully resolved, False on a real conflict (unmerged)."""
    guard = 0
    while _cherry_pick_in_progress(conv_root):
        if _unmerged_paths(conv_root):
            return False
        _run_git(conv_root, "git", "cherry-pick", "--skip")
        guard += 1
        if guard > 1000:
            break
    return not _cherry_pick_in_progress(conv_root)


def _print_harvest_conflicts(conv_root: Path) -> None:
    files = [f for f in _run_git(conv_root, "git", "diff", "--name-only",
                                 "--diff-filter=U").stdout.splitlines() if f.strip()]
    print("[scos-control] cherry-pick produced conflicts:")
    for f in files:
        print(f"  - {f}")
    print("[scos-control] reconcile each file (keep the migration fix, drop any "
          "test-patch I/O rewrites), `git add` them, then run "
          "`scos_state.py harvest --continue --conv-root <root>`. "
          "To bail out: `scos_state.py harvest --abort --conv-root <root>`.")


def _finish_harvest(conv_root: Path, state: dict) -> None:
    git = state.get("git", {})
    validation_branch = git.get("validation_branch")
    state.setdefault("git", {})["harvested"] = True
    save_state(conv_root, state)
    append_event(validation_root(conv_root), {"kind": "harvested", "branch": validation_branch})
    sha = _git_commit_tree(conv_root, VALIDATION_DIRNAME,
                           f"[HARVEST] finalize from {validation_branch or 'validation'}")
    if sha:
        print(f"[scos-control] committed Validation/ state update: {sha}")
    if not (validation_root(conv_root) / "run_index.json").is_file():
        sys.exit(_die("harvest incomplete — Validation/run_index.json missing", 1))
    if not load_state(conv_root).get("git", {}).get("harvested"):
        sys.exit(_die("harvest incomplete — state.json git.harvested is not true", 1))
    print("[scos-control] harvest deliverable check passed")
    if validation_branch:
        print(f"[scos-control] validation branch {validation_branch} kept for inspection "
              f"(delete with `git branch -D {validation_branch}` when no longer needed)")


def _cmd_harvest(args) -> int:
    """Copy Validation/ onto the original branch, then cherry-pick [MIGRATION-FIX]
    commits for Output/. Requires summary first. Exit codes: 0 ok / --abort,
    1 git/precondition failure, 5 cherry-pick conflicts (resolve then --continue)."""
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    git = state.get("git", {})
    original_branch = git.get("original_branch")
    validation_branch = git.get("validation_branch")

    if getattr(args, "abort", False):
        _run_git(conv_root, "git", "cherry-pick", "--abort")
        if original_branch:
            _run_git(conv_root, "git", "checkout", original_branch)
        print("[scos-control] harvest aborted")
        print("RESULT=aborted")
        return 0

    if not original_branch or not validation_branch:
        return _die("no validation branch recorded in state.git; init did not create one", 1)

    # Auto-recover a stale cherry-pick from a prior run (unless resuming via --continue).
    if not getattr(args, "continue_", False) and _cherry_pick_in_progress(conv_root):
        print("[scos-control] detected stale cherry-pick in progress; aborting it")
        _run_git(conv_root, "git", "cherry-pick", "--abort")

    if getattr(args, "continue_", False):
        if not _cherry_pick_in_progress(conv_root):
            print("[scos-control] no cherry-pick in progress; finalizing harvest")
            _finish_harvest(conv_root, state)
            print("RESULT=ok")
            return 0
        _run_git(conv_root, "git", "cherry-pick", "--continue")
        if not _advance_cherry_pick(conv_root):
            _print_harvest_conflicts(conv_root)
            print("RESULT=conflict")
            return 5
        _finish_harvest(conv_root, state)
        print("RESULT=ok")
        return 0

    _require_summary_before_harvest(conv_root)
    _commit_validation_to_branch(conv_root, validation_branch)

    log = _run_git(conv_root, "git", "log", "--reverse", "--grep", r"\[MIGRATION-FIX\]",
                   "--format=%H", f"{original_branch}..{validation_branch}")
    if log.returncode != 0:
        return _die(f"git log failed: {log.stderr}", 1)
    fix_shas = [s for s in log.stdout.splitlines() if s.strip()]
    _assert_fix_commits_clean(conv_root, fix_shas)

    res = _run_git(conv_root, "git", "checkout", original_branch)
    if res.returncode != 0:
        return _die(f"could not checkout {original_branch}: {res.stderr}", 1)
    print(f"[scos-control] restoring Validation/ from {validation_branch} onto {original_branch}")
    _harvest_validation_workspace(conv_root, validation_branch)

    if not fix_shas:
        print("[scos-control] no [MIGRATION-FIX] commits to cherry-pick")
        _finish_harvest(conv_root, state)
        print("RESULT=ok")
        return 0

    print(f"[scos-control] cherry-picking {len(fix_shas)} [MIGRATION-FIX] commit(s) onto {original_branch}")
    _run_git(conv_root, "git", "cherry-pick", *fix_shas)
    if not _advance_cherry_pick(conv_root):
        _print_harvest_conflicts(conv_root)
        print("RESULT=conflict")
        return 5
    _finish_harvest(conv_root, state)
    print(f"[scos-control] harvested Validation/ + {len(fix_shas)} fix commit(s) onto {original_branch}")
    print("RESULT=ok")
    return 0


# ---------------------------------------------------------------------------
# prepare-batches helpers
# ---------------------------------------------------------------------------

_WORKTREE_VALIDATION_SUBDIRS = [
    "source", "tests", "shared", "shared/schemas", "shared/mock_data",
    "shared/auxiliary", "shared/stubs", "results", "results/phase_a", "results/phase_b",
]


def _exclude_worktrees_from_gitignore(conv_root: Path) -> None:
    """Idempotently add 'Validation/worktrees/' to <conv_root>/.gitignore.

    Keeps the nested per-batch checkouts out of ``git status`` and the editor
    while leaving the rest of Validation/ visible as ordinary untracked files.
    Never raises — defensive; never blocks prepare-batches over a gitignore write.
    """
    try:
        gi = conv_root / ".gitignore"
        existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
        if "Validation/worktrees/" in {ln.strip() for ln in existing.splitlines()}:
            return
        if existing and not existing.endswith("\n"):
            existing += "\n"
        gi.write_text(existing + "Validation/worktrees/\n", encoding="utf-8")
    except Exception:
        pass  # defensive — never block prepare-batches over a gitignore write


def _ensure_worktree_skeleton(conv_root: Path) -> None:
    """Create the full per-worktree Validation/ directory tree."""
    workspace = validation_root(conv_root)
    for d in _WORKTREE_VALIDATION_SUBDIRS:
        (workspace / d).mkdir(parents=True, exist_ok=True)


def _init_worktree(
    conv_root_primary: Path,
    worktree_path: Path,
    primary_source_dir: Path,
    original_source: str,
    connection: str,
    database: str,
    slug_hint: Optional[str],
) -> dict:
    """Init a per-batch worktree: skeleton + source copy + fresh state + branch + baseline commit.

    Each worktree gets a fresh unique run_id so its golden Snowflake schema never
    collides with another batch's (Critical Rule: schema = {slug}_{run_id}).
    """
    _ensure_worktree_skeleton(worktree_path)
    wt_workspace = validation_root(worktree_path)

    # Copy source from the primary's already-validated Validation/source/.
    wt_src = wt_workspace / "source"
    if wt_src.exists():
        shutil.rmtree(wt_src)
    wt_src.mkdir(parents=True)
    _copy_dir(primary_source_dir, wt_src)

    # Write fresh state.json with a unique run_id (Java: analyzer_survey/analyzer_deep milestones).
    slug = project_slug(slug_hint or conv_root_primary.name)
    rid = run_id()
    schema = f"{slug}_{rid}".upper()
    state: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "run_id": rid, "created_at": now_iso(),
        "phase": "init",
        "config": {"connection_name": connection, "project_slug": slug, "database": database},
        "paths": {"skill_dir": "", "original_source": original_source,
                  "conv_root": str(worktree_path)},
        "snowflake": {
            "database": database, "schema": schema,
            "stage": f"{database}.{schema}.SCOS_TEST_STAGE", "stage_prefix": rid,
            "provisioned": False, "provisioned_tables": [],
        },
        "milestones": {m: False for m in (
            "analyzer_survey", "entrypoints_selected", "analyzer_deep", "patches_authored",
            "workload_built", "tests_authored", "venv_prewarmed", "snowflake_provisioned")},
        "phase_a": {"iter": 0}, "phase_b": {"iter": 0},
        "trials": {}, "synth_warnings": [],
        "git": {"original_branch": None, "validation_branch": None, "harvested": False},
    }
    save_state(worktree_path, state)

    # Cut validation/<run_id> branch and commit source baseline.
    orig_branch = _current_branch(worktree_path)
    validation_branch = f"validation/{rid}"
    if orig_branch:
        _ensure_gitignore(worktree_path)
        res = _run_git(worktree_path, "git", "checkout", "-b", validation_branch)
        if res.returncode != 0:
            res = _run_git(worktree_path, "git", "checkout", validation_branch)
        if res.returncode == 0:
            state["git"] = {
                "original_branch": orig_branch,
                "validation_branch": validation_branch,
                "harvested": False,
            }
            save_state(worktree_path, state)
            print(f"[scos-control] {worktree_path.name}: validation branch {validation_branch}")
            base_sha = _git_commit_paths(
                worktree_path,
                [str(Path(VALIDATION_DIRNAME) / "source")],
                "[VALIDATION] import Phase-A source baseline",
            )
            if base_sha:
                print(f"[scos-control] {worktree_path.name}: committed source baseline {base_sha}")
        else:
            print(f"[scos-control] WARNING: {worktree_path.name}: could not create branch: "
                  f"{res.stderr.strip()}")
    else:
        print(f"[scos-control] WARNING: {worktree_path.name}: not a git repo; "
              "harvest/commit will not work")
    return state


def _select_eps_for_worktree(
    worktree_path: Path, primary_analysis: dict, ep_ids: List[str],
) -> None:
    """Scope analysis.json to batch ep_ids and register pending trials in state.

    Called by prepare-batches for each worktree; not a public CLI subcommand.
    """
    id_set = set(ep_ids)
    all_eps = (primary_analysis.get("entrypoints")
               or primary_analysis.get("entrypoint_candidates")
               or [])
    selected = [ep for ep in all_eps if ep.get("id") in id_set]

    # Write scoped analysis.json into this worktree.
    scoped = dict(primary_analysis)
    scoped["entrypoints"] = selected
    scoped["entrypoint_candidates"] = selected
    save_analysis(worktree_path, scoped)

    # Register pending trials; remove stale ones from prior runs.
    state = load_state(worktree_path)
    new_ids = {ep.get("id") for ep in selected if ep.get("id")}
    state.setdefault("trials", {})
    for ep in selected:
        ep_id = ep.get("id", "unknown")
        state["trials"].setdefault(
            ep_id, {"status": "pending", "phase_a_iters": [], "phase_b_iters": []},
        )
    stale = [tid for tid in list(state["trials"]) if tid not in new_ids]
    for tid in stale:
        del state["trials"][tid]
    state.setdefault("milestones", {})["entrypoints_selected"] = True
    save_state(worktree_path, state)
    print(f"[scos-control] {worktree_path.name}: selected {len(selected)} ep(s): "
          f"{sorted(new_ids)}")


# ---------------------------------------------------------------------------
# scope-entrypoints
# ---------------------------------------------------------------------------


def _cmd_scope_entrypoints(args) -> int:
    """Scope the primary analysis.json to a subset of entrypoints by --ids.

    Run BEFORE Step 2 sectioning to restrict validation to a subset of the
    analyzer's candidates.  Unlike select-entrypoints (post-init, writes trials),
    this is stateless: it only rewrites analysis.json entrypoints/entrypoint_candidates.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    analysis = load_analysis(conv_root)
    cands = analysis.get("entrypoint_candidates") or analysis.get("entrypoints") or []
    if not cands:
        return _die("analysis.json has no entrypoints — run the analyzer first")
    keep_ids = {x.strip() for x in (args.ids or "").split(",") if x.strip()}
    if not keep_ids:
        return _die("--ids is required and must be a non-empty comma-separated list")
    known = {ep.get("id") for ep in cands}
    unknown = sorted(i for i in keep_ids if i not in known)
    if unknown:
        return _die(f"unknown entrypoint id(s) not in analysis.json: {unknown}")
    selected = [ep for ep in cands if ep.get("id") in keep_ids]
    removed = len(cands) - len(selected)
    analysis["entrypoints"] = selected
    analysis["entrypoint_candidates"] = selected
    save_analysis(conv_root, analysis)
    print(f"[scos-control] scoped analysis.json to {len(selected)} entrypoint(s); "
          f"kept {[ep.get('id') for ep in selected]}; "
          f"removed {removed} unselected candidate(s)")
    return 0


# ---------------------------------------------------------------------------
# consolidate
# ---------------------------------------------------------------------------


def _cmd_consolidate(args) -> int:
    """Cherry-pick [MIGRATION-FIX] commits from validation branches onto the deliverable.

    Stateless w.r.t. state.json — safe to call from any primary worktree.
    Relies on git's own index.lock as the concurrency barrier; batch-runner retries
    on exit 6.  Unlike harvest (which also copies Validation/ onto the original branch),
    consolidate is called PER BATCH from inside the worktree after summary passes, so
    each worker cherry-picks only its own fix SHAs.

    Exit codes:
        0  consolidated cleanly, --abort succeeded, or nothing to pick
        1  git failure / precondition not met
        5  cherry-pick conflict — resolve, then re-run with --continue
        6  git busy (index.lock / CHERRY_PICK_HEAD in progress) — retry in 30 s
    """
    conv_root = Path(args.conv_root).expanduser().resolve()

    if getattr(args, "abort", False):
        _run_git(conv_root, "git", "cherry-pick", "--abort")
        print("RESULT=aborted")
        return 0

    if getattr(args, "continue_", False):
        if not _cherry_pick_in_progress(conv_root):
            print("[scos-control] no cherry-pick in progress")
            return 0
        _run_git(conv_root, "git", "cherry-pick", "--continue")
        if not _advance_cherry_pick(conv_root):
            _print_harvest_conflicts(conv_root)
            print("RESULT=conflict")
            return 5
        print("RESULT=ok")
        return 0

    # Resolve validation branches to collect from.
    base_sha = args.base_sha
    branches_arg = getattr(args, "branches", None)
    if branches_arg:
        branches = [b.strip() for b in branches_arg.split(",") if b.strip()]
        if not branches:
            return _die("--branches must specify at least one branch name", 1)
    else:
        res = _run_git(conv_root, "git", "branch", "--list", "validation/*")
        branches = []
        for b in res.stdout.splitlines():
            raw = b.strip()
            if not raw:
                continue
            name = raw[1:].strip() if raw[0] in ("*", "+") else raw
            if name:
                branches.append(name)

    # Collect [MIGRATION-FIX] SHAs, skipping commits already applied to the deliverable.
    fix_shas: List[str] = []
    seen: set = set()
    for branch in branches:
        log = _run_git(conv_root, "git", "log", "--reverse", "--grep", r"\[MIGRATION-FIX\]",
                       "--format=%H", f"{base_sha}..{branch}")
        if log.returncode != 0:
            return _die(f"git log failed for {branch}: {log.stderr}", 1)
        cherry = _run_git(conv_root, "git", "cherry", "HEAD", branch, base_sha)
        cherry_ok = cherry.returncode == 0
        not_applied: set = set()
        if cherry_ok:
            for ln in cherry.stdout.splitlines():
                ln = ln.strip()
                if ln.startswith("+ "):
                    not_applied.add(ln[2:].strip())
        for sha in log.stdout.splitlines():
            sha = sha.strip()
            if not sha or sha in seen:
                continue
            if cherry_ok and sha not in not_applied:
                continue  # already on the deliverable by patch-id
            fix_shas.append(sha)
            seen.add(sha)

    _assert_fix_commits_clean(conv_root, fix_shas)

    if not fix_shas:
        print("[scos-control] no [MIGRATION-FIX] commits to consolidate")
        print("RESULT=ok")
        return 0

    print(f"[scos-control] cherry-picking {len(fix_shas)} [MIGRATION-FIX] commit(s)")
    res = _run_git(conv_root, "git", "cherry-pick", *fix_shas)
    if res.returncode == 128:
        # Git precondition error: index.lock held by another process, or
        # CHERRY_PICK_HEAD already exists — transient, worker retries after 30 s.
        hint = res.stderr.strip().splitlines()[0] if res.stderr.strip() else "git busy"
        print(f"[scos-control] git busy ({hint}) — retry in 30 s")
        print("RESULT=locked")
        return 6
    if res.returncode != 0 and not _cherry_pick_in_progress(conv_root):
        return _die(f"git cherry-pick failed: {res.stderr.strip()}", 1)
    if not _advance_cherry_pick(conv_root):
        _print_harvest_conflicts(conv_root)
        print("RESULT=conflict")
        return 5
    print(f"[scos-control] consolidated {len(fix_shas)} fix commit(s)")
    print("RESULT=ok")
    return 0


# ---------------------------------------------------------------------------
# prepare-batches
# ---------------------------------------------------------------------------


def _cmd_prepare_batches(args) -> int:
    """Set up per-batch git worktrees with analysis scoped to each batch's entrypoints.

    Computes the batch plan from sections.json + analysis.json ep weights, creates
    one git worktree per batch at --base-sha, runs per-worktree init, scopes
    analysis.json per batch, and writes batches_prepared.json to Validation/shared/.

    Exit codes:
        0  all batches prepared
        1  one or more batches failed (per-batch errors in batches_prepared.json)
        2  bad arguments
        3  sections.json coverage check failed (no worktrees created)
    """
    conv_root_primary = Path(args.conv_root).expanduser().resolve()

    # Import batch.py from the canonical PySpark validator scripts (reused, not duplicated).
    _pyspark_scripts = (Path(__file__).resolve().parent.parent.parent
                        / "validate-pyspark-to-snowpark-connect" / "scripts")
    if str(_pyspark_scripts) not in sys.path:
        sys.path.insert(0, str(_pyspark_scripts))
    import batch as _batch  # noqa: PLC0415

    # Load and validate sections.json.
    sections_path = Path(args.sections).resolve()
    if not sections_path.is_file():
        return _die(f"sections.json not found: {sections_path}", 2)
    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    if not isinstance(sections, list):
        return _die("sections.json must be a JSON array", 2)

    # Build a manifest dict from analysis.json (batch_sections expects {entrypoints:[{id,weight?}]}).
    analysis = load_analysis(conv_root_primary)
    eps = analysis.get("entrypoints") or analysis.get("entrypoint_candidates") or []
    if not eps:
        return _die("analysis.json has no entrypoints — run the analyzer first", 2)
    manifest = {"entrypoints": eps}

    # Coverage check and LPT batch planning.
    cov_errors = _batch.validate_coverage(manifest, sections)
    if cov_errors:
        return _die(
            "sections.json coverage check failed:\n"
            + "\n".join(f"  - {e}" for e in cov_errors),
            3,
        )
    try:
        batches_list, warnings = _batch.batch_sections(
            manifest, sections, args.max_entrypoints, args.max_weight,
        )
    except ValueError as exc:
        return _die(f"batching failed: {exc}", 2)
    plan = _batch._build_output(batches_list, warnings, args.max_entrypoints, args.max_weight)
    batches = plan.get("batches") or []
    if not batches:
        return _die("sections.json produced no batches", 2)

    # Print the plan.
    s = plan["summary"]
    print(f"[scos-control] batch plan: {s['n_batches']} batches, {s['n_entrypoints']} EPs, "
          f"weight min/mean/max = {s['weight_min']}/{s['weight_mean']:.1f}/{s['weight_max']}")
    for b in batches:
        print(f"  {b['batch_id']:<28} n={b['n_eps']:<3} weight={b['total_weight']}")
    for w in plan.get("warnings", []):
        print(f"  WARNING: {w}")

    # Set up worktrees dir and primary shared dir.
    worktrees_dir = conv_root_primary / VALIDATION_DIRNAME / "worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    _exclude_worktrees_from_gitignore(conv_root_primary)

    primary_workspace = validation_root(conv_root_primary)
    primary_workspace.mkdir(parents=True, exist_ok=True)

    # One-time source copy + alignment check on primary (fails fast before any worktree).
    orig = Path(args.original_source).expanduser().resolve()
    if not orig.exists():
        return _die(f"--original-source does not exist: {orig}", 2)
    primary_source_dir = primary_workspace / "source"
    if not primary_source_dir.exists() or getattr(args, "force", False):
        if primary_source_dir.exists():
            shutil.rmtree(primary_source_dir)
        primary_source_dir.mkdir(parents=True)
        if orig.is_dir():
            _copy_dir(orig, primary_source_dir)
        else:
            shutil.copy2(orig, primary_source_dir / orig.name)
        migrated_root = conv_root_primary / "Output"
        if orig.is_dir() and migrated_root.is_dir():
            rc = _check_source_output_aligned(primary_source_dir, migrated_root, orig)
            if rc:
                return rc

    # Shared batch-learnings file (idempotent — skip if already present).
    shared_dir = primary_workspace / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    learnings_path = shared_dir / "batch-learnings.md"
    if not learnings_path.exists():
        learnings_path.write_text(
            "# Batch Learnings\n\n"
            "Shared log of reusable findings from completed workers.\n"
            "Append a `### Batch <batch_id>` section after harvest completes.\n\n",
            encoding="utf-8",
        )

    base_sha = args.base_sha

    # Create one git worktree per batch.
    results: List[Dict[str, Any]] = []
    n_ok = 0
    for batch in batches:
        batch_id = batch["batch_id"]
        ep_ids: List[str] = batch.get("ep_ids") or []
        worktree_path = worktrees_dir / batch_id
        rec: Dict[str, Any] = {
            "batch_id": batch_id,
            "section_ids": (batch.get("section_ids")
                            or ([batch["section_id"]] if batch.get("section_id") else [])),
            "section_names": (batch.get("section_names")
                              or ([batch["section_name"]] if batch.get("section_name") else [])),
            "ep_ids": ep_ids,
            "n_eps": batch.get("n_eps", len(ep_ids)),
            "total_weight": batch.get("total_weight"),
            "worktree": str(worktree_path),
            "run_id": None,
            "validation_branch": None,
            "error": None,
        }
        try:
            # Step 1: create git worktree at base_sha (idempotent — skip if already exists).
            if not worktree_path.exists():
                branch_name = f"validation-base/{batch_id}"
                res = _run_git(conv_root_primary, "git", "worktree", "add", "-b", branch_name,
                               str(worktree_path), base_sha)
                if res.returncode != 0:
                    # Branch may already exist on re-run — add without -b.
                    res = _run_git(conv_root_primary, "git", "worktree", "add",
                                   str(worktree_path), base_sha)
                    if res.returncode != 0:
                        raise RuntimeError(f"git worktree add failed: {res.stderr.strip()}")

            # Step 2: per-worktree init (skip if already initialized with milestones).
            wt_sp = state_path(worktree_path)
            skip_init = False
            if wt_sp.is_file():
                wt_existing = load_json(wt_sp)
                if (wt_existing.get("schema_version") == SCHEMA_VERSION
                        and any((wt_existing.get("milestones") or {}).values())):
                    skip_init = True
                    print(f"[scos-control] {batch_id}: skipping init "
                          f"(already at run_id={wt_existing.get('run_id', '?')})")
            if not skip_init:
                _init_worktree(
                    conv_root_primary, worktree_path, primary_source_dir,
                    args.original_source, args.connection, args.database,
                    getattr(args, "project_slug", None),
                )

            # Step 3: scope analysis.json to this batch's ep_ids.
            _select_eps_for_worktree(worktree_path, analysis, ep_ids)

            # Step 4: read back run_id + validation_branch.
            wt_state = load_state(worktree_path)
            rec["run_id"] = wt_state.get("run_id")
            rec["validation_branch"] = (wt_state.get("git") or {}).get("validation_branch")
            n_ok += 1

        except SystemExit as e:
            msg = f"unexpected SystemExit({e.code})"
            rec["error"] = msg
            print(f"[scos-control] {batch_id}: ERROR — {msg}", file=sys.stderr)
        except Exception as exc:
            rec["error"] = str(exc)
            print(f"[scos-control] {batch_id}: ERROR — {exc}", file=sys.stderr)

        results.append(rec)

    # Write batches_prepared.json — the single source of truth: plan + worktree map.
    out_path = shared_dir / "batches_prepared.json"
    write_atomic(out_path, {
        "base_sha": base_sha,
        "worktrees_dir": str(worktrees_dir),
        "max_entrypoints": args.max_entrypoints,
        "max_weight": args.max_weight,
        "summary": plan.get("summary", {}),
        "warnings": plan.get("warnings", []),
        "batches": results,
    })

    print(f"[scos-control] prepared {n_ok}/{len(batches)} batches")
    return 1 if n_ok < len(batches) else 0


# ---------------------------------------------------------------------------
# Subcommands ported from the Scala validator (SNOW-3715354)
# ---------------------------------------------------------------------------


def _java_major(java_home: str) -> Optional[int]:
    """Return the major version of the JDK at *java_home*, or None."""
    java_bin = Path(java_home) / "bin" / "java"
    if not java_bin.exists():
        java_bin = Path(java_home) / "bin" / "java.exe"
    if not java_bin.exists():
        # Fall back to whichever `java` is on PATH.
        if not shutil.which("java"):
            return None
        java_bin = Path(shutil.which("java"))  # type: ignore[arg-type]
    try:
        out = subprocess.run(
            [str(java_bin), "-version"],
            capture_output=True, text=True, timeout=30,
        )
        text = (out.stdout + out.stderr).lower()
        # "openjdk version \"17.0.1\"" / "java version \"11.0.2\""
        m = re.search(r'"(\d+)\.\d+', text) or re.search(r'version (\d+)', text)
        if m:
            major = int(m.group(1))
            # Java 8 reports as 1.8, normalise.
            return 8 if major == 1 else major
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return None


def _scos_client_jar_present(conv_root: Path) -> bool:
    """True when the SCOS Java client jar is locatable (mirrors dep_check logic)."""
    lib_dir = conv_root / VALIDATION_DIRNAME / "tests" / "lib"
    if lib_dir.is_dir() and list(lib_dir.glob("snowpark-connect-java-client*.jar")):
        return True
    search = [
        conv_root / "Output" / "lib",
        Path.home() / ".m2" / "repository" / "com" / "snowflake" / "snowpark-connect-java-client_2.12",
        Path.home() / ".m2" / "repository" / "com" / "snowflake" / "snowpark-connect-java-client_2.13",
        Path.home() / "Library" / "Caches" / "Coursier" / "v1" / "https"
            / "repo1.maven.org" / "maven2" / "com" / "snowflake",
        Path.home() / ".cache" / "coursier" / "v1" / "https" / "repo1.maven.org"
            / "maven2" / "com" / "snowflake",
    ]
    for base in search:
        if base.is_dir():
            for j in base.rglob("snowpark-connect-java-client*.jar"):
                if "sources" not in j.name and "javadoc" not in j.name:
                    return True
    return False


def _java_preflight_checks(conv_root: Path, phase: str) -> "Tuple[int, List[str], str]":
    """Deterministic environment-readiness gate for Java Phase A/B.

    Checks: Maven present, JDK 11 or 17 available, SCOS client jar for Phase B.
    Returns ``(exit_code, problems, java_home_used)``.

    Java uses Maven (not sbt like the Scala kit), JDK 11/17 (Spark 3.5 does not
    support Java 21+), and the same ``snowpark-connect-java-client`` jar.
    """
    problems: List[str] = []

    # Maven required for workload build.
    if not shutil.which("mvn"):
        problems.append(
            "mvn not on PATH — install Maven 3.8+ (e.g. `brew install maven` or "
            "`apt-get install -y maven`) for workload compilation."
        )

    # JDK 11 or 17 required.
    java_home = os.environ.get("JAVA_HOME", "").strip()
    major = _java_major(java_home) if java_home else _java_major(shutil.which("java") or "")
    java_home_used = java_home or (shutil.which("java") or "")
    if major is None:
        problems.append(
            "java not found — install JDK 11 or 17 (Spark 3.5 does NOT support Java 21+). "
            "Set JAVA_HOME or ensure `java` is on PATH."
        )
    elif major not in (11, 17):
        if major > 17:
            problems.append(
                f"Java {major} detected but Spark 3.5 requires JDK 11 or 17 "
                "(Java 21+ breaks Spark codegen and Arrow reflection). "
                f"Set JAVA_HOME to a JDK 11/17 installation."
            )
        else:
            # Java 8: not supported by Spark Connect
            problems.append(
                f"Java {major} detected but Spark Connect requires JDK 11 or 17. "
                "Upgrade to JDK 17."
            )

    if phase == "b" and not _scos_client_jar_present(conv_root):
        problems.append(
            "SCOS Java client jar (snowpark-connect-java-client) not found in "
            "Validation/tests/lib, Output/lib, ~/.m2, or Coursier cache — Phase B "
            "will fail with ClassNotFoundException. Place the jar in Output/lib/ "
            "before running Phase B, or run `mvn dependency:resolve` first."
        )

    return (0 if not problems else 3), problems, java_home_used


def _cmd_preflight(args) -> int:
    """Environment-readiness gate for Java Phase A/B. Fails fast with actionable remediation."""
    conv_root = Path(args.conv_root).expanduser().resolve()
    phase = (getattr(args, "phase", "a") or "a").lower()
    rc, problems, java_home = _java_preflight_checks(conv_root, phase)
    if rc != 0:
        preview = "\n".join(f"  - {p}" for p in problems)
        return _die(
            f"preflight FAILED — environment not ready for Phase {phase.upper()}; fix "
            "these before running (the harness will NOT silently produce a no-baseline "
            "pass):\n" + preview, rc)
    print(f"[scos-control] preflight OK (phase {phase}): java_home={java_home}, mvn present")
    return 0


def _cmd_phase_reset(args) -> int:
    """Clear stale phase artefacts so the next phase starts clean.

    --to a  : wipe compiled test-classes + rendered specs
    --to b  : same wipe, + optionally clear phase_a results (--clear-results)
              and reset tests_authored milestone.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    target_phase = (getattr(args, "to", "a") or "a").lower()

    workspace = validation_root(conv_root)
    tests_dir = workspace / "tests"
    cleared: List[str] = []

    # 1. Clear tests/target/ (compiled Maven classes; prevents phase-A classpath leaking)
    target_dir = tests_dir / "target"
    if target_dir.is_dir():
        shutil.rmtree(target_dir, ignore_errors=True)
        cleared.append("tests/target/")
        print(f"[scos-control] phase-reset: cleared {target_dir}")
    else:
        print("[scos-control] phase-reset: tests/target/ absent — skipping")

    # 2. Remove rendered spec files (phase_a/ and phase_b/)
    specs_cleared: List[str] = []
    for sub in ("phase_a", "phase_b"):
        specs_subdir = tests_dir / sub
        if specs_subdir.is_dir():
            shutil.rmtree(specs_subdir, ignore_errors=True)
            specs_cleared.append(sub)
    if specs_cleared:
        cleared.extend([f"tests/{s}/" for s in specs_cleared])
        print(f"[scos-control] phase-reset: removed rendered specs: {specs_cleared}")
    else:
        print("[scos-control] phase-reset: no rendered specs to clear")

    # 3. Optionally clear Phase A trial outputs (--to b + --clear-results)
    if target_phase == "b" and getattr(args, "clear_results", False):
        phase_a_results = workspace / "results" / "phase_a"
        if phase_a_results.is_dir():
            shutil.rmtree(phase_a_results, ignore_errors=True)
            phase_a_results.mkdir(parents=True, exist_ok=True)
            cleared.append("results/phase_a/")
            print(f"[scos-control] phase-reset: cleared Phase A results ({phase_a_results})")

    # 4. Record phase_reset event
    append_event(workspace, {
        "kind": "phase_reset",
        "to": target_phase,
        "cleared": cleared,
        "clear_results": getattr(args, "clear_results", False),
    })
    print(f"[scos-control] phase-reset to={target_phase}: cleared={cleared}")
    return 0


def _cmd_schemas_to_analysis(args) -> int:
    """Regenerate the shared/analysis.json shim from schemas/manifest.json.

    Thin wrapper around ``ast_to_analysis.py --from-manifest`` (Java path) when
    schemas/manifest.json is present. Used after patching schemas externally so
    downstream tools (column_check, dep_check, validate) see updated schema info
    without re-running the full analyzer.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    _scripts = Path(__file__).resolve().parent

    manifest = conv_root / VALIDATION_DIRNAME / "shared" / "schemas" / "manifest.json"
    if not manifest.exists():
        return _die(
            f"schemas-to-analysis: {manifest} not found — run the analyzer first "
            "to produce schemas/manifest.json before calling this command."
        )

    analysis_out = conv_root / VALIDATION_DIRNAME / "shared" / "analysis.json"
    script = _scripts / "ast_to_analysis.py"
    if not script.exists():
        return _die(f"schemas-to-analysis: ast_to_analysis.py not found at {script}")

    result = subprocess.run(
        [sys.executable, str(script),
         "--from-manifest", str(manifest),
         "--output", str(analysis_out)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return _die(f"schemas-to-analysis: ast_to_analysis.py failed (exit {result.returncode})")

    print(f"[scos-control] schemas-to-analysis: wrote {analysis_out}")
    return 0


# NOTE: ``prevalidate`` below ports the highest-value checks from the Scala
# validator: analysis completeness, cli_args/kwargs stub detection, entry-class
# vs assembled-JAR validation, and dep_check.py/column_check.py wiring. It
# intentionally does NOT yet port preflight (JVM/jar), mock-data verification,
# a Java build-ladder compile check (mvn/gradle, not sbt), cores=0 static
# detection, I/O completeness scanning, sink-strategy validation, or
# unsupported_constructs translation — those remain tracked under
# SNOW-3715354 (Java parity). ``build-doctor`` remains a full stub (see below).


def _prevalidate_finding(
    check: str,
    severity: str,
    message: str,
    phase: str = "a|b",
    entrypoint: Optional[str] = None,
    file: Optional[str] = None,
    line: Optional[int] = None,
    fix_hint: Optional[str] = None,
    auto_fixable: bool = False,
    rebuild_required: bool = False,
) -> dict:
    return {
        "check": check,
        "severity": severity,
        "phase": phase,
        "entrypoint": entrypoint,
        "file": file,
        "line": line,
        "message": message,
        "fix_hint": fix_hint,
        "auto_fixable": auto_fixable,
        "rebuild_required": rebuild_required,
    }


def _prevalidate_hash_state(conv_root: Path) -> str:
    """SHA-256 of schemas/ (SoT) + source + patches for cache gating.

    Prefer hashing ``schemas/`` when present so editing ``_meta.json`` / tables
    invalidates the cache even if analysis.json was not refreshed yet. Falls
    back to ``analysis.json`` when schemas/ has not been mined.
    """
    import hashlib
    h = hashlib.sha256()
    sd = validation_root(conv_root) / "shared" / "schemas"
    if sd.is_dir() and (sd / "manifest.json").is_file():
        for f in sorted(sd.rglob("*.json")):
            try:
                h.update(str(f.relative_to(sd)).encode())
                h.update(f.read_bytes())
            except OSError:
                pass
    else:
        apath = analysis_path(conv_root)
        if apath.is_file():
            h.update(apath.read_bytes())
    for tree in [conv_root / "Output", conv_root / "Validation" / "source"]:
        if tree.is_dir():
            for f in sorted(tree.rglob("*.java")):
                try:
                    h.update(f.read_bytes())
                except OSError:
                    pass
            for bf in sorted(tree.glob("pom.xml")) + sorted(tree.glob("build.gradle")) \
                    + sorted(tree.glob("build.gradle.kts")):
                try:
                    h.update(bf.read_bytes())
                except OSError:
                    pass
    patches_dir = conv_root / "Validation" / "shared" / "patches"
    if patches_dir.is_dir():
        for pf in sorted(patches_dir.rglob("*.json")):
            try:
                h.update(pf.read_bytes())
            except OSError:
                pass
    return h.hexdigest()


def _prevalidate_check_cache(conv_root: Path, current_hash: str) -> Optional[dict]:
    """Return the cached prevalidation report if the state hash is unchanged."""
    shared = validation_root(conv_root) / "shared"
    cache_path = shared / ".prevalidate_cache.json"
    report_path = shared / "prevalidation_report.json"
    if not cache_path.is_file() or not report_path.is_file():
        return None
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if cache.get("hash") == current_hash:
            return json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _prevalidate_save_cache(conv_root: Path, hash_val: str) -> None:
    shared = validation_root(conv_root) / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / ".prevalidate_cache.json").write_text(
        json.dumps({"hash": hash_val, "computed_at": datetime.now(timezone.utc).isoformat()}, indent=2),
        encoding="utf-8",
    )


def _prevalidate_run_json_tool(
    cmd: List[str], check_name: str, phase: str
) -> Tuple[List[dict], List[dict]]:
    """Run a script that emits ``{"ok", "problems", "warnings"}`` JSON to stdout.

    Returns (blocking_findings, warning_findings).
    If the tool exits non-zero with no stdout (e.g. missing input files), the
    stderr message is surfaced as a warning rather than a blocking finding, since
    a tool that can't run is not itself a workload defect.
    """
    blocking: List[dict] = []
    warn: List[dict] = []
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        stdout = (result.stdout or "").strip()
        if not stdout:
            # Tool produced no JSON — emit stderr as a warning
            msg = (result.stderr or "").strip() or f"{check_name}: no output (rc={result.returncode})"
            warn.append(_prevalidate_finding(check_name, "warning", msg, phase=phase))
            return blocking, warn
        data = json.loads(stdout)
        for p in data.get("problems") or []:
            blocking.append(_prevalidate_finding(
                check_name, "blocking",
                p if isinstance(p, str) else str(p), phase=phase,
            ))
        for w in data.get("warnings") or []:
            warn.append(_prevalidate_finding(
                check_name, "warning",
                w if isinstance(w, str) else str(w), phase=phase,
            ))
    except (json.JSONDecodeError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError) as exc:
        blocking.append(_prevalidate_finding(
            check_name, "blocking",
            f"{check_name}: invocation error — {exc}", phase=phase,
        ))
    return blocking, warn


_CLI_STUB_RE = re.compile(
    r"(?i)^(TODO|FIXME|TBD|stub|placeholder|null|none|n/?a|<.*>|\[.*\]|\s*)$"
)
_LLM_TODO_PHASE_A_BLOCK = re.compile(
    r"(?i)dynamic\s+path|unresolved|cli_args|args\.|incomplete\s+stub|missing\s+arg"
)


def _cli_value_incomplete(val: object) -> bool:
    if val is None:
        return True
    s = str(val).strip()
    if not s:
        return True
    # Concrete JSON literals are intended values (e.g. "[]" for an empty args
    # array, or '[{"counts":0}]'). These must NOT be flagged as stubs even
    # though the placeholder regex below would otherwise match "[]" / "[...]".
    # An ellipsis placeholder like "[{...}]" is NOT valid JSON, so it correctly
    # falls through to the stub check.
    if s[:1] in ("[", "{") or s in ("true", "false"):
        try:
            json.loads(s)
            return False
        except (ValueError, TypeError):
            pass
    if _CLI_STUB_RE.match(s):
        return True
    if s in ('""', "''"):
        return True
    return False


def _check_analysis_completeness_pv(conv_root: Path, phase: str) -> List[dict]:
    """Ensure every selected entrypoint has the fields Phase A/B runners need."""
    findings: List[dict] = []
    try:
        analysis = json.loads(analysis_path(conv_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(_prevalidate_finding(
            "analysis_completeness", "blocking",
            f"analysis.json missing or unreadable: {exc}",
            phase=phase,
            fix_hint="Re-run schema_mine.py --conv-root so Validation/shared/analysis.json exists.",
        ))
        return findings

    eps = analysis.get("entrypoints") or []
    if not eps:
        findings.append(_prevalidate_finding(
            "analysis_completeness", "blocking",
            "no entrypoints[] — nothing to validate",
            phase=phase,
            fix_hint="Run schema_mine.py --conv-root (or select-entrypoints) first.",
        ))
        return findings

    ast_facts = _load_json_optional(ast_facts_path(conv_root))

    for ep in eps:
        if not isinstance(ep, dict):
            continue
        eid = ep.get("id") or "<unknown>"
        entry_class = (ep.get("entrypoint_class") or ep.get("class_name") or "").strip()
        entry_method = (ep.get("entrypoint_method") or ep.get("method") or "").strip()
        if not entry_class:
            findings.append(_prevalidate_finding(
                "analysis_completeness", "blocking",
                f"entrypoint '{eid}' missing entrypoint_class",
                phase=phase,
                entrypoint=eid,
                fix_hint="Set entrypoint_class to the compiled FQN (include $ for nested classes).",
            ))
        if not entry_method:
            findings.append(_prevalidate_finding(
                "analysis_completeness", "warning",
                f"entrypoint '{eid}' missing entrypoint_method (defaulting to main at runtime)",
                phase=phase,
                entrypoint=eid,
                fix_hint="Set entrypoint_method explicitly (usually 'main').",
            ))
        if "external_sources" not in ep:
            findings.append(_prevalidate_finding(
                "analysis_completeness", "blocking",
                f"entrypoint '{eid}' missing external_sources[] key",
                phase=phase,
                entrypoint=eid,
                fix_hint="Re-run deep analysis so reads are recorded (empty list is OK).",
            ))
        if not entrypoint_declares_sinks_key(ep):
            findings.append(_prevalidate_finding(
                "analysis_completeness", "blocking",
                f"entrypoint '{eid}' missing sinks[] / external_sinks[] key",
                phase=phase,
                entrypoint=eid,
                fix_hint="Re-run deep analysis so writes are recorded (empty list is OK).",
            ))
        declared_sinks = (
            entrypoint_declared_sinks(ep)
            if entrypoint_declares_sinks_key(ep) else None
        )
        if isinstance(declared_sinks, list) and not declared_sinks:
            write_ev = entrypoint_ast_write_evidence(ast_facts, ep)
            if write_ev:
                findings.append(_prevalidate_finding(
                    "analysis_completeness", "blocking",
                    (f"entrypoint '{eid}' has sinks=[] but ast_facts shows writes "
                     f"({'; '.join(write_ev[:3])})"
                     + ("…" if len(write_ev) > 3 else "")),
                    phase=phase,
                    entrypoint=eid,
                    fix_hint=(
                        "Mine sinks from AST writes (re-run deep analysis). Empty sinks[] "
                        "is only valid when AST also has no writes — do not treat an "
                        "analysis gap as a no-sink smoke baseline."
                    ),
                    rebuild_required=False,
                ))
        todo = (ep.get("llm_todo") or "").strip()
        if todo:
            phase_a_block = bool(_LLM_TODO_PHASE_A_BLOCK.search(todo))
            sev = "blocking" if (phase == "b" or phase_a_block) else "warning"
            findings.append(_prevalidate_finding(
                "analysis_completeness", sev,
                f"entrypoint '{eid}' has open llm_todo: {todo[:160]}",
                phase=phase,
                entrypoint=eid,
                fix_hint=(
                    "Resolve dynamic paths / cli_args / unresolved I/O in analysis.json "
                    "before Phase A."
                    if phase_a_block else
                    "Resolve or dismiss the llm_todo before Phase B."
                ),
                rebuild_required=False,
            ))
    return findings


def _check_cli_args_completeness_pv(conv_root: Path, phase: str) -> List[dict]:
    """Block Phase A/B when cli_args / entrypoint_kwargs still contain stubs.

    Incomplete CLI args are a common analysis-completeness failure class
    (ArrayIndexOutOfBounds / NPE at entrypoint startup). Mock/cli_args fixes do
    NOT require a JAR rebuild (rebuild_required=False).
    """
    findings: List[dict] = []
    try:
        analysis = json.loads(analysis_path(conv_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return findings

    for ep in analysis.get("entrypoints") or []:
        if not isinstance(ep, dict):
            continue
        eid = ep.get("id") or "<unknown>"
        cli_args = ep.get("cli_args")
        kwargs = ep.get("entrypoint_kwargs") or {}

        if isinstance(cli_args, list):
            for i, arg in enumerate(cli_args):
                if _cli_value_incomplete(arg):
                    findings.append(_prevalidate_finding(
                        "cli_args", "blocking",
                        f"entrypoint '{eid}' cli_args[{i}] is incomplete/stub: {arg!r}",
                        phase=phase,
                        entrypoint=eid,
                        fix_hint=(
                            "Fill cli_args with non-empty concrete values the "
                            "main(String[]) path expects (paths, flags, non-empty arrays)."
                        ),
                        rebuild_required=False,
                    ))
            if not cli_args and not kwargs:
                todo = (ep.get("llm_todo") or "")
                if _LLM_TODO_PHASE_A_BLOCK.search(todo) or "args" in todo.lower():
                    findings.append(_prevalidate_finding(
                        "cli_args", "blocking",
                        f"entrypoint '{eid}' has empty cli_args but open args-related llm_todo",
                        phase=phase,
                        entrypoint=eid,
                        fix_hint="Populate cli_args (or entrypoint_kwargs) before running trials.",
                        rebuild_required=False,
                    ))

        if isinstance(kwargs, dict):
            for k, v in kwargs.items():
                if _cli_value_incomplete(v):
                    findings.append(_prevalidate_finding(
                        "cli_args", "blocking",
                        f"entrypoint '{eid}' entrypoint_kwargs[{k!r}] is incomplete/stub: {v!r}",
                        phase=phase,
                        entrypoint=eid,
                        fix_hint="Replace stub kwargs with concrete harness values.",
                        rebuild_required=False,
                    ))
    return findings


def _check_entry_classes(conv_root: Path) -> List[dict]:
    """Verify every entrypoint_class in analysis.json exists in the assembled JAR."""
    import zipfile
    findings: List[dict] = []
    try:
        analysis = json.loads(analysis_path(conv_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [_prevalidate_finding("entry_class", "warning",
                                     f"cannot read analysis.json for entry-class check: {exc}")]

    jar_rel = analysis.get("jar_path", "") or ""
    jar: Optional[Path] = None
    if jar_rel:
        p = Path(jar_rel)
        if not p.is_absolute():
            p = conv_root / "Output" / jar_rel
        if p.exists():
            jar = p
    if jar is None:
        target = conv_root / "Output" / "target"
        if target.is_dir():
            candidates = sorted(
                (j for j in target.rglob("*.jar")
                 if "sources" not in j.name and "javadoc" not in j.name),
                key=lambda j: j.stat().st_mtime, reverse=True,
            )
            if candidates:
                jar = candidates[0]
    if jar is None:
        findings.append(_prevalidate_finding(
            "entry_class", "warning",
            "no assembled JAR found in Output/target/ — skipping entry-class check",
            fix_hint="Run 'mvn package' (or 'gradle shadowJar') in Output/ before prevalidate.",
        ))
        return findings

    try:
        with zipfile.ZipFile(jar, "r") as zf:
            jar_entries = set(zf.namelist())
    except Exception as exc:
        return [_prevalidate_finding("entry_class", "warning",
                                     f"cannot inspect JAR for entry-class check: {exc}")]

    for ep in analysis.get("entrypoints") or []:
        cls = ep.get("entrypoint_class", "")
        if not cls:
            continue
        class_path = cls.replace(".", "/").rstrip("$")
        if not any(f"{class_path}{suf}.class" in jar_entries for suf in ("", "$")):
            findings.append(_prevalidate_finding(
                "entry_class", "blocking",
                f"entrypoint_class {cls!r} not found in {jar.name}",
                phase="a|b",
                entrypoint=ep.get("id"),
                fix_hint=(
                    f"Update analysis.json entrypoints[id={ep.get('id')!r}].entrypoint_class"
                    " to the compiled class name exactly (include $ for nested/inner classes)."
                ),
            ))
    return findings


def _cmd_prevalidate(args) -> int:
    """Aggregate the ported static validation checks into a prevalidation report.

    Checks (all aggregated, never abort-on-first):
      1. Analysis completeness (entrypoint_class, sources/sinks, open llm_todo).
      2. cli_args / entrypoint_kwargs completeness (stub values block; no rebuild).
      3. column_check.py --conv-root --json.
      4. dep_check.py --conv-root.
      5. Entry-class validation vs assembled JAR.

    NOT yet ported (SNOW-3715354): preflight JVM/jar checks, mock-data verify,
    a Java build-ladder compile check, cores=0 static detection, I/O
    completeness scanning, Phase-B sink-strategy validation, and
    unsupported_constructs[] translation.

    Caching: hash(schemas/ or analysis.json + source + patches) -> if unchanged
    and report exists, print cached message and exit without re-running checks.

    Output: Validation/shared/prevalidation_report.json + stdout summary.
    Exit codes: 0 = all clear, 1 = blocking findings, 2 = warnings only.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    phase = (getattr(args, "phase", "a") or "a").lower()
    _scripts = Path(__file__).resolve().parent

    # --- caching gate ---
    current_hash = _prevalidate_hash_state(conv_root)
    if not getattr(args, "force", False):
        cached = _prevalidate_check_cache(conv_root, current_hash)
        if cached is not None:
            print("[scos-control] prevalidate: no changes since last run (cached)")
            if cached.get("blocking_count", 0) > 0:
                return 1
            if cached.get("warning_count", 0) > 0:
                return 2
            return 0

    all_findings: List[dict] = []

    # 1. Analysis completeness
    all_findings.extend(_check_analysis_completeness_pv(conv_root, phase))

    # 2. cli_args / kwargs stubs
    all_findings.extend(_check_cli_args_completeness_pv(conv_root, phase))

    # 3. column_check.py
    b, w = _prevalidate_run_json_tool(
        [sys.executable, str(_scripts / "column_check.py"), "--conv-root", str(conv_root), "--json"],
        "column_check", phase,
    )
    all_findings.extend(b)
    all_findings.extend(w)

    # 4. dep_check.py
    b, w = _prevalidate_run_json_tool(
        [sys.executable, str(_scripts / "dep_check.py"), "--conv-root", str(conv_root)],
        "dep_check", phase,
    )
    all_findings.extend(b)
    all_findings.extend(w)

    # 5. Entry-class validation
    all_findings.extend(_check_entry_classes(conv_root))

    # Build report
    blocking = [f for f in all_findings if f.get("severity") == "blocking"]
    warn_lst = [f for f in all_findings if f.get("severity") == "warning"]
    rebuild_needed = any(f.get("rebuild_required") for f in blocking)
    report = {
        "phase": phase,
        "ok": len(blocking) == 0,
        "blocking_count": len(blocking),
        "warning_count": len(warn_lst),
        "rebuild_required": rebuild_needed,
        "findings": all_findings,
    }

    shared = validation_root(conv_root) / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    report_path = shared / "prevalidation_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _prevalidate_save_cache(conv_root, current_hash)

    if blocking:
        print(f"[scos-control] prevalidate FAILED — {len(blocking)} blocking finding(s), "
              f"{len(warn_lst)} warning(s). Report: {report_path}")
        for f in blocking:
            print(f"  [BLOCKING/{f.get('check','')}] {f.get('message','')}")
        return 1
    if warn_lst:
        print(f"[scos-control] prevalidate OK with {len(warn_lst)} warning(s). "
              f"Report: {report_path}")
        for f in warn_lst:
            print(f"  [WARN/{f.get('check','')}] {f.get('message','')}")
        return 2
    print(f"[scos-control] prevalidate OK — all checks passed. Report: {report_path}")
    return 0


def _detect_java_build_tool(project_dir: Path) -> str:
    if (project_dir / "pom.xml").is_file():
        return "maven"
    if (project_dir / "build.gradle.kts").is_file() or (project_dir / "build.gradle").is_file():
        return "gradle"
    return ""


def _find_jar_in_target(project_dir: Path, build_tool: str) -> str:
    """Newest fat/shaded/assembly jar under target/ (Maven) or build/libs/ (Gradle)."""
    search_dirs = [project_dir / "target"] if build_tool == "maven" else [project_dir / "build" / "libs"]
    search_dirs.append(project_dir / "target")  # some Gradle projects still emit to target/
    for d in search_dirs:
        if not d.is_dir():
            continue
        cands = [j for j in d.rglob("*.jar")
                 if not any(x in j.name for x in ("-sources", "-javadoc", "-tests"))]
        if not cands:
            continue
        # Prefer shaded/shadow/uber/fat jars; then newest mtime.
        def _rank(j: Path) -> tuple:
            fat = any(k in j.name.lower() for k in ("shaded", "shadow", "uber", "fat", "-all", "assembly"))
            return (1 if fat else 0, j.stat().st_mtime)
        return str(max(cands, key=_rank))
    return ""


def _cmd_build_doctor(args) -> int:
    """Prove the workload build converges (Maven/Gradle), no tests run.

    Minimal Java-native version: runs the project's build tool (skipping
    tests), locates the resulting JAR, and reports {ok, jar, build_tool,
    log_path}. Does NOT yet port the Scala validator's classpath-filtering
    ladder, fat-JAR classification, or build-failure classifier — tracked
    under SNOW-3715354 (Java parity). Use ``--side source`` (default) for
    Phase A ``Validation/source``, or ``--side migrated``/``output`` for
    ``Output/`` before Phase B.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    side = (getattr(args, "side", None) or "source").strip().lower()
    if side in ("migrated", "output", "b", "phase_b"):
        project_dir = conv_root / "Output"
        side_label = "migrated"
    else:
        project_dir = validation_root(conv_root) / "source"
        side_label = "source"

    if not project_dir.is_dir():
        report = {"ok": False, "side": side_label, "project_dir": str(project_dir),
                   "jar": "", "build_tool": "", "log_path": "",
                   "error": f"project dir not found: {project_dir}"}
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        return 5

    build_tool = _detect_java_build_tool(project_dir)
    if not build_tool:
        report = {"ok": False, "side": side_label, "project_dir": str(project_dir),
                   "jar": "", "build_tool": "", "log_path": "",
                   "error": "no pom.xml or build.gradle[.kts] found"}
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        return 5

    force_rebuild = getattr(args, "force_rebuild", False)
    existing_jar = "" if force_rebuild else _find_jar_in_target(project_dir, build_tool)

    log_path = validation_root(conv_root) / "shared" / f"build_doctor_{side_label}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ok = True
    if not existing_jar:
        cmd = (["mvn", "-q", "-DskipTests", "package"] if build_tool == "maven"
               else ["gradle", "build", "-x", "test"])
        try:
            result = subprocess.run(cmd, cwd=str(project_dir), capture_output=True,
                                     text=True, timeout=900)
            log_path.write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")
            ok = result.returncode == 0
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            log_path.write_text(str(exc), encoding="utf-8")
            ok = False
        existing_jar = _find_jar_in_target(project_dir, build_tool)

    report = {
        "ok": ok and bool(existing_jar),
        "side": side_label,
        "project_dir": str(project_dir),
        "jar": existing_jar,
        "build_tool": build_tool,
        "log_path": str(log_path),
    }
    out_path = getattr(args, "output", None)
    text = json.dumps(report, indent=2) + "\n"
    if out_path:
        Path(out_path).expanduser().resolve().write_text(text, encoding="utf-8")
        print(f"[scos-control] build-doctor report -> {out_path}")
    else:
        sys.stdout.write(text)
    if not existing_jar:
        return 5
    if not ok:
        return 1
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scos_state.py",
                                description="Java validator state machine (Python port of ScosState).")
    sub = p.add_subparsers(dest="command", required=True)

    def cr(sp):
        sp.add_argument("--conv-root", required=True)
        return sp

    init = cr(sub.add_parser("init"))
    init.add_argument("--connection", required=True)
    init.add_argument("--original-source")
    init.add_argument("--migrated-source")
    init.add_argument("--project-slug")
    init.add_argument("--database",
                      default=os.environ.get("SCOS_VALIDATION_DATABASE", "SCOS_VALIDATION"),
                      help="Snowflake database for golden schemas (default: $SCOS_VALIDATION_DATABASE or SCOS_VALIDATION)")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=_cmd_init)

    sel = cr(sub.add_parser("select-entrypoints"))
    sel.add_argument("--ids")
    sel.add_argument("--max", type=int, default=None)
    sel.set_defaults(func=_cmd_select_entrypoints)

    st = cr(sub.add_parser("status"))
    st.add_argument("--phase", default="all")
    st.add_argument("--verbose", action="store_true")
    st.set_defaults(func=_cmd_status)

    cr(sub.add_parser("summary")).set_defaults(func=_cmd_summary)
    cr(sub.add_parser("build-index")).set_defaults(func=_cmd_build_index)
    cr(sub.add_parser("put-schemas")).set_defaults(func=_cmd_put_schemas)
    cr(sub.add_parser("migrate-divergences")).set_defaults(func=_cmd_migrate_divergences)

    pa = cr(sub.add_parser("patch-add"))
    pa.add_argument("--from-file", required=True)
    pa.add_argument("--no-commit", action="store_true")
    pa.set_defaults(func=_cmd_patch_add)

    ri = cr(sub.add_parser("record-iter"))
    ri.add_argument("--trial-id", required=True)
    ri.add_argument("--phase", required=True)
    ri.add_argument("--iter", type=int, default=0)
    ri.add_argument("--passing", type=int, default=0)
    ri.add_argument("--failing", type=int, default=0)
    ri.add_argument("--issues", type=int, default=None)
    ri.add_argument("--patches-extended", type=int, default=None)
    ri.add_argument("--fix-commit", default=None)
    ri.add_argument("--fix-category", default=None)
    ri.add_argument("--notes", default=None)
    ri.set_defaults(func=_cmd_record_iter)

    rts = cr(sub.add_parser("record-trial-status"))
    rts.add_argument("--trial-id", required=True)
    rts.add_argument("--status", required=True)
    rts.add_argument("--final-iter", type=int, default=None)
    rts.add_argument("--reason", default=None)
    rts.add_argument("--analysis-repair-exhausted", action="store_true",
                     help="Allow hard_stuck for a schema/data gap repaired inline "
                          "(no fixer dispatch) after >=2 recorded analysis_repair rounds.")
    rts.add_argument("--baseline-not-comparable", action="store_true",
                     help="Allow passed_no_baseline even though Phase A produced a "
                          "baseline, for the rare case where Phase A captured different "
                          "sinks than Phase B. Requires --reason.")
    rts.set_defaults(func=_cmd_record_trial_status)

    cm = cr(sub.add_parser("commit"))
    cm.add_argument("--message", required=True)
    cm.add_argument("--kind", required=True, choices=sorted(COMMIT_PREFIXES),
                    help="test-patch (not cherry-picked) | migration-fix (cherry-picked at harvest)")
    cm.add_argument("--trial-ids", default="",
                    help="Comma-separated trial id(s) this fix is for; recorded as a "
                         "SCOS-Trials git trailer. Strongly recommended for --kind migration-fix.")
    cm.add_argument("--iter", type=int, default=None)
    cm.add_argument("--print-sha-only", action="store_true")
    cm.set_defaults(func=_cmd_commit)

    hv = cr(sub.add_parser("harvest"))
    hv.add_argument("--continue", dest="continue_", action="store_true",
                    help="Resume an in-progress cherry-pick after reconciling conflicts")
    hv.add_argument("--abort", action="store_true",
                    help="Abort an in-progress cherry-pick and return to the original branch")
    hv.set_defaults(func=_cmd_harvest)

    # scope-entrypoints — pre-sectioning subset filter (no state.json required)
    sce = cr(sub.add_parser("scope-entrypoints",
                             help="Scope analysis.json to a subset of entrypoints before sectioning"))
    sce.add_argument("--ids", required=True,
                     help="Comma-separated entrypoint IDs to keep")
    sce.set_defaults(func=_cmd_scope_entrypoints)

    # consolidate — cherry-pick [MIGRATION-FIX] from one batch onto the deliverable
    cs = cr(sub.add_parser("consolidate",
                            help="Cherry-pick [MIGRATION-FIX] commits onto the deliverable branch"))
    cs.add_argument("--base-sha", required=True,
                    help="Base SHA bounding the commit range (from batches_prepared.json)")
    cs.add_argument("--branches", default=None,
                    help="Comma-separated validation branch names (default: all validation/* branches)")
    cs.add_argument("--continue", dest="continue_", action="store_true",
                    help="Resume an in-progress cherry-pick after resolving conflicts")
    cs.add_argument("--abort", action="store_true",
                    help="Abort an in-progress cherry-pick")
    cs.set_defaults(func=_cmd_consolidate)

    # prepare-batches — create per-batch git worktrees
    pb = cr(sub.add_parser("prepare-batches",
                            help="Set up per-batch git worktrees for parallel validation"))
    pb.add_argument("--sections", required=True,
                    help="Path to sections.json (Step 2 sectioning output)")
    pb.add_argument("--original-source", required=True,
                    help="Path to the original (unmigrated) source tree")
    pb.add_argument("--connection", required=True,
                    help="Snowflake connection name for all worktrees")
    pb.add_argument("--database",
                    default=os.environ.get("SCOS_VALIDATION_DATABASE", "SCOS_VALIDATION"),
                    help="Snowflake database for golden schemas (default: $SCOS_VALIDATION_DATABASE)")
    pb.add_argument("--project-slug", default=None,
                    help="Project slug prefix for golden schema names (default: derived from dir name)")
    pb.add_argument("--base-sha", required=True,
                    help="Git SHA to create each worktree from (capture with git rev-parse HEAD)")
    pb.add_argument("--max-entrypoints", type=int, default=8,
                    help="Maximum entrypoints per batch (default: 8)")
    pb.add_argument("--max-weight", type=int, default=40,
                    help="Maximum weight per batch (default: 40)")
    pb.add_argument("--force", action="store_true",
                    help="Re-copy source even if Validation/source/ already exists")
    pb.set_defaults(func=_cmd_prepare_batches)

    # ── Subcommands ported from Scala validator (SNOW-3715354) ────────────────
    pf = cr(sub.add_parser("preflight",
        help="Verify Java+Maven environment readiness before Phase A/B."))
    pf.add_argument("--phase", default="a", choices=["a", "b"],
                    help="Phase to check (default: a)")
    pf.set_defaults(func=_cmd_preflight)

    pr = cr(sub.add_parser("phase-reset",
        help="Clear stale compile artefacts + rendered specs before a phase."))
    pr.add_argument("--to", default="a", choices=["a", "b"],
                    help="Reset to phase a or b (default: a)")
    pr.add_argument("--clear-results", action="store_true",
                    help="Also clear Phase A trial results (only valid with --to b)")
    pr.set_defaults(func=_cmd_phase_reset)

    sa = cr(sub.add_parser("schemas-to-analysis",
        help="Regenerate analysis.json from schemas/manifest.json."))
    sa.set_defaults(func=_cmd_schemas_to_analysis)

    pv = cr(sub.add_parser("prevalidate",
        help="Aggregate static validation gate (analysis completeness, cli_args, "
             "column_check, dep_check, entry-class vs JAR)."))
    pv.add_argument("--phase", default="a", choices=["a", "b"])
    pv.add_argument("--force", action="store_true")
    pv.set_defaults(func=_cmd_prevalidate)

    bd = cr(sub.add_parser("build-doctor",
        help="Prove workload build converges (mvn/gradle package, locate JAR)."))
    bd.add_argument("--side", default="source", choices=["source", "migrated"])
    bd.add_argument("--force-rebuild", action="store_true")
    bd.add_argument("--output", default=None)
    bd.set_defaults(func=_cmd_build_doctor)
    # ── end ported subcommands ────────────────────────────────────────────────

    ms = cr(sub.add_parser("record-milestone"))
    ms.add_argument("--milestone", required=True)
    ms.set_defaults(func=_cmd_record_milestone)

    cr(sub.add_parser("prewarm")).set_defaults(func=_cmd_prewarm)

    rpa = cr(sub.add_parser("run-phase-a",
                             help="Deterministic Phase A: copy kit, render specs, sbt test (source)"))
    rpa.add_argument("--parallelism", type=int, default=4,
                     help="SCOS_TEST_PARALLELISM passed to sbt (default: 4)")
    rpa.set_defaults(func=_cmd_run_phase_a)

    rpb = cr(sub.add_parser("run-phase-b",
                             help="Deterministic Phase B: derive SPARK_REMOTE, sbt test (migrated)"))
    rpb.add_argument("--parallelism", type=int, default=4,
                     help="SCOS_TEST_PARALLELISM passed to sbt (default: 4)")
    rpb.set_defaults(func=_cmd_run_phase_b)

    cr(sub.add_parser("provision")).set_defaults(func=_cmd_provision)

    dd = cr(sub.add_parser("document-divergence"))
    dd.add_argument("--trial-id", required=True)
    dd.add_argument("--sink-id", required=True)
    dd.add_argument("--column", required=True)
    dd.add_argument("--reason", required=True)
    dd.add_argument("--baseline-sample", default=None)
    dd.add_argument("--shadow-sample", default=None)
    dd.add_argument("--iter", type=int, default=None)
    dd.set_defaults(func=_cmd_document_divergence)

    meb = cr(sub.add_parser("mark-empty-baseline"))
    meb.add_argument("--trial-id", required=True)
    meb.add_argument("--sink-id", required=True)
    meb.set_defaults(func=_cmd_mark_empty_baseline)

    fd = cr(sub.add_parser("record-fixer-dispatch"))
    fd.add_argument("--iter", type=int, default=0)
    fd.add_argument("--error-class", default="")
    fd.add_argument("--error-hash", default="")
    fd.add_argument("--trial-ids", dest="trial_ids", default="")
    fd.add_argument("--trial-id", dest="trial_ids")  # singular alias (agent docs use --trial-id)
    fd.add_argument("--outcome", default="")
    fd.set_defaults(func=_cmd_record_fixer_dispatch)

    ud = cr(sub.add_parser("mark-unselected-dependency"))
    ud.add_argument("--trial-id", required=True)
    ud.add_argument("--reason", required=True)
    ud.add_argument("--iter", type=int, default=None)
    ud.set_defaults(func=_cmd_mark_unselected_dependency)

    rp = cr(sub.add_parser("record-patch"))
    rp.add_argument("--trial-id", required=True)
    rp.add_argument("--phase", required=True)
    rp.add_argument("--file", required=True)
    rp.add_argument("--reason", required=True)
    rp.add_argument("--iter", type=int, default=None)
    rp.add_argument("--diff-path", default=None)
    rp.set_defaults(func=_cmd_record_patch)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "record-fixer-dispatch" and not args.trial_ids:
        return _die("--trial-id (or --trial-ids) is required")
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[scos-control] error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
