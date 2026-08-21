#!/usr/bin/env python3
"""validate.py — state + utility CLI for the SCOS migration validation skill.

Manages state.json, workspace scaffolding, phase-scoped venv seeding,
and status/summary reporting.  LLM agents (source-runner, scos-runner)
own test authoring and iteration loops directly.
NO subagent dispatch — that lives in SKILL.md.

# TODO: at 2100+ lines, consider extracting cmd_build_index/cmd_summary
# into scripts/reporting.py.

Subcommands — state management:
    init                       Initialize Validation/ workspace + state.json, cut
                               the validation/<run_id> git branch off Output/'s branch
    install-kit                Copy the harness kit (*.py) into Validation/tests
                               (cross-platform; idempotent)
    scope-entrypoints          Prune mined schemas/ to a subset (pre-sectioning;
                               no state.json / no cap)
    seed-venv                  Seed a phase-scoped venv (source or scos)
    record-milestone           Mark a milestone (e.g., synth_deep) complete
    record-trial-status        Set a trial's terminal status (passed, hard_stuck, …)
    record-iter                Record a Phase A or Phase B iteration result
    record-fixer-dispatch      Record a migration-fixer dispatch
    record-patch               Record a workload/test patch applied during a phase
    patch-add                  Smoke-test + apply + record a blueprint patch (the
                               gatekeeper: unique-match + compile checks, then commit
                               the Output/ side as [TEST-PATCH]). Supports
                               "regex": true (Python regex search with backref
                               replace) and glob relative_file (applies one entry
                               across many files; skips non-matching files).
    mark-unselected-dependency Handle cross-entrypoint deps (auto → passed_no_baseline)
    document-divergence        Record a documented column divergence for a trial
    migrate-divergences        Migrate divergences from write_NNN to table-name keys
    commit                     Stage and commit Output/ ([TEST-PATCH] | [MIGRATION-FIX])
    consolidate                Cherry-pick [MIGRATION-FIX] commits from validation
                               branches onto the current (deliverable) branch;
                               supports --abort / --continue for conflict resolution
    harvest                    Copy Validation/ onto the original branch (requires
                               summary first), then cherry-pick [MIGRATION-FIX]
                               Output/ commits
    prepare-batches            Set up per-batch git worktrees with schemas scoped
                               to each batch's entrypoints; writes
                               Validation/shared/batches_prepared.json
    run-tests                  Run pytest for a phase's trials (auto-deselect passing
                               trials, record-iter); wraps test execution with
                               iteration accounting
    known-patches suggest      Scan source with KNOWN_PATCHES + investigation
                               detectors; write known_patch_suggestions.json and
                               patch_investigation.json
    runtime-detect             Validate Databricks credentials + Unity Catalog
                               support; report Phase A runtime decision (JSON)

Subcommands — reporting:
    status                     Show current validation status
    summary                    Final report (summary.json → REPORT.md →
                               build-index → prune workspace cache)
    build-index                Emit canonical Validation/run_index.json manifest

Exit codes:
    0 — success / all green
    1 — logical failure (pending trials, nothing to commit, etc.)
    2 — bad arguments / precondition not met
    3 — state.json missing or malformed
    4 — summary incomplete (required outputs missing)
    5 — harvest cherry-pick produced conflicts (left in-progress for reconciliation)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
SCRIPTS_DIR = Path(__file__).resolve().parent
VALIDATION_DIRNAME = "Validation"

# Packages that must NEVER be installed into a phase venv because they conflict
# with the phase's core Spark/Snowpark deps (source: pyspark+delta, scos:
# snowpark-connect). Used both to filter workload requirements.txt and the
# static import scan below.
_ALWAYS_EXCLUDE = {"databricks-connect", "pyspark-connect", "dbconnect"}
# Core deps the harness installs itself with authoritative version pins; a
# workload requirements.txt must not override these (harness install wins).
_HARNESS_WINS_FILTER = {
    "pyspark", "numpy", "pyarrow", "pandas", "jdk4py",
    "snowpark-connect", "snowflake-snowpark-python",
    "snowflake-connector-python",
}

# Ensure scripts/harness/ is importable so datagen's lazy `from helpers import …`
# calls resolve regardless of how validate.py is invoked (script, subprocess, import).
_harness_path = str(SCRIPTS_DIR / "harness")
if _harness_path not in sys.path:
    sys.path.insert(0, _harness_path)

# patch_engine lives alongside this script; it is on sys.path[0] when validate.py
# is invoked as a script.
import datagen  # noqa: E402
import patch_engine  # noqa: E402


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _die(code: int, msg: str) -> None:
    print(f"[validate.py] error: {msg}", file=sys.stderr)
    sys.exit(code)


def _git(conv_root: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    """Run a git command in ``conv_root`` and return the completed process."""
    return subprocess.run(
        ["git", *args], cwd=str(conv_root), capture_output=True, text=True
    )


def _current_branch(conv_root: Path) -> Optional[str]:
    res = _git(conv_root, "rev-parse", "--abbrev-ref", "HEAD")
    if res.returncode != 0:
        return None
    name = res.stdout.strip()
    return name or None


# Python bytecode + tooling caches must never enter the conv-root git history:
# ``_git_commit_output`` runs ``git add Output`` over the whole tree, which would
# otherwise stage ``Output/**/__pycache__/*.pyc`` produced by Phase A/B runs and
# pollute the [TEST-PATCH] / [MIGRATION-FIX] commits. git honours a ``.gitignore``
# even while it is untracked, so writing the file is enough to keep it effective.
_GITIGNORE_PATTERNS = [
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
]


def _ensure_gitignore(conv_root: Path) -> None:
    """Ensure conv_root/.gitignore lists the bytecode/cache patterns. Creates the
    file if missing, or appends only the patterns it does not already contain."""
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


def cmd_install_kit(args: argparse.Namespace) -> None:
    """Copy the validation harness kit into ``<conv-root>/Validation/tests``.

    Cross-platform replacement for ``cp -R $SKILL/scripts/harness/. $TESTS_DIR``:
    copies every ``*.py`` from ``scripts/harness/`` (skipping ``__pycache__``),
    plus the kit ``.gitignore.template`` (→ tests/ ``.gitignore``). Idempotent —
    overwrites existing kit files in place."""
    conv_root = Path(args.conv_root).resolve()
    tests_dir = conv_root / VALIDATION_DIRNAME / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    harness = SCRIPTS_DIR / "harness"

    copied: List[str] = []
    for src in sorted(harness.glob("*.py")):
        shutil.copy2(src, tests_dir / src.name)
        copied.append(src.name)
    gi_tpl = harness / ".gitignore.template"
    if gi_tpl.is_file():
        shutil.copy2(gi_tpl, tests_dir / ".gitignore")
        copied.append(".gitignore")

    # Copy runtimes/ subpackage (required by conftest.py and test templates)
    runtimes_src = harness / "runtimes"
    runtimes_dst = tests_dir / "runtimes"
    if runtimes_src.is_dir():
        shutil.copytree(runtimes_src, runtimes_dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        copied.append("runtimes/")

    print(f"[validate.py] installed kit into {tests_dir}: {', '.join(copied)}")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _slug(s: str) -> str:
    """Slugify a string: lowercase, alnum + underscore only."""
    return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")


def _project_slug(name: str) -> str:
    """Snowflake-safe slug: can't start with digit."""
    base = _slug(name)
    if not base:
        return "project"
    if base[0].isdigit():
        return f"p_{base}"
    return base


def _normalize_sink_name(raw: str) -> str:
    text = str(raw or "").replace("`", "").replace('"', "").strip()
    if not text:
        return ""
    if "://" in text or text.startswith("/"):
        return Path(text).name.rsplit(".", 1)[0]
    parts = [part for part in text.split(".") if part]
    if parts:
        return parts[-1]
    return Path(text).name.rsplit(".", 1)[0]


def _validation_root(conv_root: Path) -> Path:
    return conv_root / VALIDATION_DIRNAME


def _state_path(conv_root: Path) -> Path:
    return _validation_root(conv_root) / "state.json"


def _schemas_dir(conv_root: Path) -> Path:
    return _validation_root(conv_root) / "shared" / "schemas"


def _write_atomic(path: Path, data: Any) -> None:
    """Write JSON atomically via tmp + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".validate_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            f.write("\n")
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_json(path: Path, *, required: bool = False) -> Dict[str, Any]:
    if not path.is_file():
        if required:
            _die(3, f"required file not found: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _die(3, f"{path.name} is not valid JSON: {e}")


def _load_json_tolerant(path: Path) -> "tuple[Dict[str, Any], str | None]":
    """Load JSON, returning ({}, error_string) on parse failure instead of dying."""
    if not path.is_file():
        return {}, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, ValueError) as e:
        return {}, str(e)


def _load_state(conv_root: Path) -> Dict[str, Any]:
    state = _load_json(_state_path(conv_root), required=True)
    if state.get("schema_version") != SCHEMA_VERSION:
        _die(3, f"state.json schema_version mismatch (expected {SCHEMA_VERSION})")
    return state


def _save_state(conv_root: Path, state: Dict[str, Any]) -> None:
    _write_atomic(_state_path(conv_root), state)


def _append_event(conv_root: Path, event: Dict[str, Any]) -> None:
    """Append a structured event to the append-only events.jsonl timeline."""
    event = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"), **event}
    p = _validation_root(conv_root) / "events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


def _load_manifest(conv_root: Path) -> Dict[str, Any]:
    return _load_json(_schemas_dir(conv_root) / "manifest.json", required=True)


def _save_manifest(conv_root: Path, manifest: Dict[str, Any]) -> None:
    _write_atomic(_schemas_dir(conv_root) / "manifest.json", manifest)


def _load_entrypoints(conv_root: Path, manifest: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    if manifest is None:
        manifest = _load_manifest(conv_root)
    sd = _schemas_dir(conv_root)
    eps: List[Dict[str, Any]] = []
    for ref in manifest.get("entrypoints") or []:
        eps.append(datagen.load_entrypoint(str(sd), ref["id"]))
    return eps


def _save_entrypoint(conv_root: Path, ep: Dict[str, Any]) -> None:
    datagen.save_entrypoint(_schemas_dir(conv_root), ep)


def _prune_schemas_to_selected(
    conv_root: Path,
    manifest: Dict[str, Any],
    selected: List[Dict[str, Any]],
) -> int:
    """Keep only *selected* entrypoints on disk. Removes unselected directories,
    trims manifest index, prunes divergences, recomputes status."""
    before = len(manifest.get("entrypoints") or [])
    selected_ids = {ep.get("id") for ep in selected if ep.get("id")}
    sd = _schemas_dir(conv_root)

    for ref in manifest.get("entrypoints") or []:
        ep_id = ref.get("id")
        if ep_id not in selected_ids:
            ep_dir = sd / datagen.entrypoint_dir(ep_id or "")
            if ep_dir.is_dir():
                shutil.rmtree(ep_dir)

    manifest["entrypoints"] = [
        {"id": ep["id"], "path": ep["path"], "dir": datagen.entrypoint_dir(ep["id"])}
        for ep in selected
    ]

    divs = manifest.get("expected_divergences")
    if isinstance(divs, dict):
        for key in list(divs):
            trial_id = key.split(".", 1)[0] if isinstance(key, str) and "." in key else key
            if trial_id not in selected_ids:
                del divs[key]

    for ep in selected:
        _save_entrypoint(conv_root, ep)

    datagen.recompute_manifest_status(manifest, selected)
    _save_manifest(conv_root, manifest)
    return before - len(selected)


def _manual_review_marker_path(conv_root: Path, trial_id: str) -> Path:
    return _validation_root(conv_root) / "results" / "phase_b" / trial_id / "_manual_review.json"


def _materialize_manual_review_statuses(conv_root: Path, state: Dict[str, Any]) -> None:
    changed = False
    for trial_id, trial in (state.get("trials") or {}).items():
        if trial.get("status", "pending") != "pending":
            continue
        marker_path = _manual_review_marker_path(conv_root, trial_id)
        if not marker_path.is_file():
            continue
        trial["status"] = "passed_no_baseline"
        trial["manual_review_marker"] = str(marker_path)
        changed = True
    if changed:
        _advance_phase(state, conv_root)
        _save_state(conv_root, state)



# ---------------------------------------------------------------------------
# source/Output layout check
#
# The patch engine keys every blueprint patch on a single ``relative_file`` and
# derives both physical paths from it: ``Validation/source/<rel>`` (Phase A) and
# ``Output/<rel>`` (Phase B). That invariant only holds if the two trees share
# the same relative path roots. When ``--original-source`` points one level
# shallower/deeper than ``Output/`` keeps the migrated tree (e.g. Output nests
# everything under ``plk-rbi/<project>/`` but the copied source is just
# ``<project>/``), every patch silently misses on one side. We verify the roots
# line up right after the copy and stop with a clear message if they don't.
# ---------------------------------------------------------------------------

_ALIGN_CODE_EXTS = (".py", ".ipynb", ".sql")
_ALIGN_SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ipynb_checkpoints", "node_modules", ".tox", VALIDATION_DIRNAME,
}


def _rel_code_files(root: Path) -> set:
    """Forward-slash relative paths of code files under *root* (caches/venvs skipped)."""
    found: set = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _ALIGN_SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(_ALIGN_CODE_EXTS):
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                found.add(rel.replace(os.sep, "/"))
    return found


def _suggest_aligned_source(orig: Path, src: set, out: set) -> Optional[str]:
    """Best-effort: find an ``--original-source`` that *would* make ``src`` a subset
    of ``out`` and return a concrete path string to re-run with, or ``None``.

    Handles the two common misalignments:
      A) source is one level too shallow — ``Output/`` wraps everything under a dir.
         Prepending the folder the user pointed *into* (``orig.name``) lines them up,
         so the fix is to point at ``orig.parent``.
      B) source is one level too deep — it carries an extra single wrapper dir that
         ``Output/`` lacks. Descending into that wrapper lines them up.
    """
    if not src or not out:
        return None
    # Case A: point one level up (Output nests source under orig.name/).
    wrapped = {f"{orig.name}/{s}" for s in src}
    if wrapped <= out:
        return str(orig.parent)
    # Case B: point one level down into the single wrapper dir.
    tops = {s.split("/", 1)[0] for s in src if "/" in s}
    if len(tops) == 1:
        d = next(iter(tops))
        stripped = {s[len(d) + 1:] for s in src if s.startswith(d + "/")}
        if stripped and stripped <= out:
            return str(orig / d)
    return None


def _check_source_output_aligned(
    source_root: Path, output_root: Path, orig_source: Path
) -> None:
    """Verify ``Validation/source`` and ``Output`` share the same relative path
    roots so the patch engine's ``<rel>`` resolves on both sides. This is a check
    only — on mismatch it stops with exit 2 and asks the operator to re-run init
    with an ``--original-source`` whose layout mirrors ``Output/``."""
    src = _rel_code_files(source_root)
    out = _rel_code_files(output_root)
    if not src or not out:
        return  # nothing to check (empty file-based source, or Output not code)
    if src <= out:
        return  # every source file is locatable at Output/<rel> — roots line up

    # Notebook migration case: source .py files pair with Output .py.ipynb files.
    # Also handle bare X.ipynb ↔ X.py pairs (e.g. COMMON_UTILS.ipynb ↔ COMMON_UTILS.py).
    # Normalize .py.ipynb → .py and bare .ipynb → .py before re-checking.
    normalized_out = {
        r[: -len(".ipynb")] if r.endswith(".py.ipynb")
        else r[: -len(".ipynb")] + ".py" if r.endswith(".ipynb")
        else r
        for r in out
    }
    if src <= normalized_out:
        return  # every source .py aligns with a .py.ipynb output — notebook pairs OK

    missing = sorted(src - out)[:5]
    suggestion = _suggest_aligned_source(orig_source, src, out)
    if suggestion:
        fix = (f"  Suggested fix: re-run init with\n"
               f"    --original-source {suggestion}\n"
               f"  (that directory's layout lines up with Output/ — all "
               f"{len(src)} source files would match).")
    else:
        fix = ("Fix: re-run init with --original-source pointing at the directory "
               "whose internal layout matches Output/, adding any wrapping "
               "directories needed so the two trees line up (e.g. point at the "
               "parent that contains 'plk-rbi/' so Validation/source/<rel> and "
               "Output/<rel> resolve to the same files).")
    _die(2,
         "Validation/source and Output/ do not share relative path roots "
         f"({len(src & out)}/{len(src)} source code files line up). Patches key on a "
         "single <relative_file> resolved as BOTH Validation/source/<rel> and "
         "Output/<rel>, so the two trees must mirror each other.\n"
         f"  e.g. these source files have no Output/ match: {missing}\n"
         f"{fix}")


# Dir list used inside each per-batch worktree. NOT applied to the primary
# conv-root — the primary only needs Validation/source/, Validation/shared/
# (and its children), and Validation/worktrees/. The per-batch scaffolding dirs
# (tests/, results/phase_a/, results/phase_b/, shared/mock_data/) live only
# inside worktrees.
_WORKTREE_VALIDATION_SUBDIRS = [
    "source", "tests", "shared", "shared/schemas", "shared/schemas/entrypoints",
    "shared/mock_data", "results", "results/phase_a", "results/phase_b",
]


def _ensure_worktree_skeleton(conv_root: Path) -> None:
    """Create the full per-worktree Validation/ tree (source/, tests/,
    shared/…, results/phase_a/, results/phase_b/, shared/mock_data/). Callers:
    ``cmd_init`` (per-worktree init) and ``cmd_prepare_batches``'s worktree loop."""
    workspace = _validation_root(conv_root)
    for d in _WORKTREE_VALIDATION_SUBDIRS:
        (workspace / d).mkdir(parents=True, exist_ok=True)


def _init_prepare_source(
    conv_root: Path,
    original_source: Optional[str],
    migrated_source: Optional[str],
    *,
    check_alignment: bool = True,
) -> Tuple[Path, Optional[Path]]:
    """Wipe+copy original source into ``Validation/source/`` and optionally
    verify source↔Output alignment. This is the only skeleton work that runs
    on the primary conv-root; worktree-local scaffolding (tests/, results/…,
    shared/mock_data/) is set up separately via `_ensure_worktree_skeleton`
    and only inside each worktree.

    Returns (orig, migrated_src). Called once on the primary conv_root before
    any worktrees are created, so a bad --original-source fails exactly once.
    """
    workspace = _validation_root(conv_root)
    workspace.mkdir(parents=True, exist_ok=True)

    # Resolve migrated source (Output/)
    migrated_src: Optional[Path] = None
    if migrated_source:
        migrated_src = Path(migrated_source).resolve()
    elif (conv_root / "Output").is_dir():
        migrated_src = (conv_root / "Output").resolve()

    # Copy original source (always wipe first to avoid merged-layout ghost files)
    if not original_source:
        _die(2, "--original-source is required so Phase A can run against the original workload")
    orig = Path(original_source).resolve()
    if not orig.exists():
        _die(2, f"--original-source does not exist: {orig}")
    src_target = workspace / "source"
    if src_target.exists():
        shutil.rmtree(src_target)
    src_target.mkdir(parents=True)
    if orig.is_dir():
        shutil.copytree(orig, src_target, dirs_exist_ok=True)
    elif orig.is_file():
        shutil.copy2(orig, src_target / orig.name)
    else:
        _die(2, f"--original-source is not a file or directory: {orig}")

    if check_alignment and migrated_src is not None and migrated_src.is_dir():
        _check_source_output_aligned(workspace / "source", migrated_src, orig)

    return orig, migrated_src


def _init_write_state(
    conv_root: Path,
    connection: str,
    original_source: Optional[str],
    project_slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Derive slug + fresh run_id, write state.json, return the state dict.

    Each call produces a unique run_id — callers must not share run_ids across
    worktrees (Critical Rule #3: golden Snowflake schemas must never collide).
    """
    slug = _project_slug(project_slug or conv_root.name)
    run_id = uuid.uuid4().hex[:8]
    schema_name = f"{slug}_{run_id}".upper()
    skill_dir = SCRIPTS_DIR.parent

    state: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": _now(),
        "phase": "init",
        "config": {
            "connection_name": connection,
            "project_slug": slug,
        },
        "paths": {
            "skill_dir": str(skill_dir),
            "original_source": str(Path(original_source).resolve()) if original_source else None,
            "conv_root": str(conv_root),
        },
        "snowflake": {
            "database": os.environ.get("SCOS_VALIDATION_DATABASE", "SCOS_VALIDATION"),
            "schema": schema_name,
            "stage": f"{os.environ.get('SCOS_VALIDATION_DATABASE', 'SCOS_VALIDATION')}.{schema_name}.SCOS_TEST_STAGE",
            "stage_prefix": run_id,
            "provisioned": False,
            "provisioned_tables": [],
        },
        "milestones": {
            "entrypoints_selected": False,
            "synth_deep": False,
            "patches_authored": False,
            "phase_a_complete": False,
            "phase_b_complete": False,
        },
        "phase_a": {"iter": 0},
        "phase_b": {"iter": 0},
        "trials": {},
        "git": {"original_branch": None, "validation_branch": None},
        "synth_warnings": [],
    }
    _save_state(conv_root, state)
    return state


def _exclude_worktrees_from_git(conv_root: Path) -> None:
    """Idempotently add 'Validation/worktrees/' to <conv_root>/.gitignore.

    The per-batch worktrees live under Validation/worktrees/ (one full repo
    checkout each); ignoring just that path keeps the bulky nested checkouts out
    of `git status` and the editor while leaving the rest of Validation/
    (results, REPORT.md, pool_status.json, schemas, state) VISIBLE as ordinary
    untracked files. Scoping to worktrees/ only (not all of Validation/) also
    means it never affects a linked worktree committing its own
    Validation/source baseline. Never raises.
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


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> None:
    conv_root = Path(args.conv_root).resolve()
    sp = _state_path(conv_root)
    force = getattr(args, "force", False)

    # Idempotency: if state.json already exists and matches, exit early.
    if sp.is_file() and not force:
        existing = _load_json(sp)
        if existing.get("schema_version") == SCHEMA_VERSION:
            has_milestone = any(existing.get("milestones", {}).values())
            if has_milestone:
                print(f"[validate.py] skipping init (already initialized at "
                      f"run_id={existing.get('run_id','?')}, "
                      f"phase={existing.get('phase','?')})")
                return
            # Stale leftover with no milestones — overwrite below

    # Full worktree skeleton (tests/, results/…, shared/mock_data/, …), then
    # copy original source into Validation/source/ + verify alignment.
    _ensure_worktree_skeleton(conv_root)
    _init_prepare_source(
        conv_root,
        args.original_source,
        getattr(args, "migrated_source", None),
        check_alignment=True,
    )

    # Write state.json with a fresh run_id.
    state = _init_write_state(
        conv_root,
        args.connection,
        args.original_source,
        getattr(args, "project_slug", None),
    )
    run_id = state["run_id"]
    schema_name = state["snowflake"]["schema"]

    # Cut an ephemeral validation branch off the migrated code's current branch.
    # All blueprint I/O patches land here as [TEST-PATCH] commits (not cherry-picked
    # onto the deliverable). [MIGRATION-FIX] commits are cherry-picked at harvest.
    # The validation branch is kept for inspection after harvest.
    original_branch = _current_branch(conv_root)

    # Warn if the current branch is itself a validation/* branch (branch nesting).
    if original_branch and original_branch.startswith("validation/"):
        print(f"[validate.py] WARNING: current branch '{original_branch}' is itself a "
              f"validation branch — you may be nesting validation branches. Consider "
              f"switching to main/master first.", file=sys.stderr)

    # Detect orphaned validation branches from prior failed runs.
    validation_branch = f"validation/{run_id}"
    existing_branches = _git(conv_root, "branch", "--list", "validation/*")
    if existing_branches.returncode == 0:
        for line in existing_branches.stdout.splitlines():
            raw = line.strip()
            if not raw:
                continue
            # A leading `*` (current) or `+` (checked out in another worktree)
            # marker means the branch is live somewhere — never delete it. Only
            # unmarked branches are candidates for orphan cleanup.
            checked_out = raw[0] in ("*", "+")
            stale = raw[1:].strip() if raw[0] in ("*", "+") else raw
            if not stale or stale == validation_branch or checked_out:
                continue
            # Check if its Validation/ workspace still exists (if not, it's orphaned).
            ws_check = _git(conv_root, "ls-tree", "--name-only", stale, "Validation/")
            if ws_check.returncode != 0 or not ws_check.stdout.strip():
                print(f"[validate.py] removing orphaned validation branch '{stale}' "
                      f"(no Validation/ directory)")
                _git(conv_root, "branch", "-D", stale)

    if original_branch:
        _ensure_gitignore(conv_root)
        res = _git(conv_root, "checkout", "-b", validation_branch)
        if res.returncode != 0:
            # Branch may already exist (re-init / --force); switch to it.
            res = _git(conv_root, "checkout", validation_branch)
        if res.returncode == 0:
            state["git"] = {
                "original_branch": original_branch,
                "validation_branch": validation_branch,
            }
            _save_state(conv_root, state)
            print(f"[validate.py] validation branch: {validation_branch} (off {original_branch})")
            # Baseline-commit the imported Phase-A source so later [TEST-PATCH]
            # commits (which stage Validation/source/ alongside Output/) show only
            # the patch diff, and a `git revert` of any one patch cleanly restores
            # both sides. Not cherry-picked at harvest (only [MIGRATION-FIX] is).
            base_sha = _git_commit_paths(
                conv_root, [os.path.join(VALIDATION_DIRNAME, "source")],
                "[VALIDATION] import Phase-A source baseline")
            if base_sha:
                print(f"[validate.py] committed Phase-A source baseline: {base_sha}")
        else:
            print(f"[validate.py] WARNING: could not create validation branch: {res.stderr.strip()}")
    else:
        print("[validate.py] WARNING: <conv-root> is not a git repo; harvest/commit will not work")

    print(f"[validate.py] initialized validation workspace: run_id={run_id}, schema={schema_name}")


# ---------------------------------------------------------------------------
# _select_entrypoints_for_worktree (internal helper used by prepare-batches)
# ---------------------------------------------------------------------------


def _select_entrypoints_for_worktree(conv_root: Path, ids: str, max_eps: int) -> None:
    """Scope a worktree's schemas/ to *ids* and register them in state.trials.

    Called by prepare-batches for each batch worktree; not a public CLI subcommand.
    *ids*: comma-separated entrypoint ID string (required, non-empty).
    *max_eps*: safety cap — dies with exit 2 if len(selected) > max_eps.
    """
    state = _load_state(conv_root)
    manifest = _load_manifest(conv_root)
    entrypoints = _load_entrypoints(conv_root, manifest)
    if not entrypoints:
        _die(2, "schemas/ has no entrypoints — run schema_mine --out first")

    id_set = {x.strip() for x in ids.split(",") if x.strip()}
    if not id_set:
        _die(2, "ids must be a non-empty comma-separated list of entrypoint IDs")

    selected = [c for c in entrypoints if c.get("id") in id_set]
    if not selected:
        _die(2, f"no entrypoints matched ids {ids!r}")

    if len(selected) > max_eps:
        _die(
            2,
            f"{len(selected)} entrypoints selected, which exceeds max {max_eps}; "
            "raise max_eps or pass a smaller set",
        )

    removed = _prune_schemas_to_selected(conv_root, manifest, selected)
    if removed:
        print(f"[validate.py] pruned schemas/: removed {removed} unselected entrypoint(s)")

    # Register selected entrypoints in state.trials; remove any stale entries.
    state["milestones"]["entrypoints_selected"] = True
    new_ids = {ep.get("id", "unknown") for ep in selected}
    stale = [tid for tid in state["trials"] if tid not in new_ids]
    for tid in stale:
        del state["trials"][tid]
    if stale:
        print(f"[validate.py] removed {len(stale)} stale trial(s): {stale}")
    for ep in selected:
        ep_id = ep.get("id", "unknown")
        if ep_id not in state["trials"]:
            state["trials"][ep_id] = {
                "status": "pending",
                "phase_a_iters": [],
                "phase_b_iters": [],
            }
    _save_state(conv_root, state)
    print(f"[validate.py] selected {len(selected)} entrypoint(s): {[e.get('id') for e in selected]}")


# ---------------------------------------------------------------------------
# scope-entrypoints
# ---------------------------------------------------------------------------


def cmd_scope_entrypoints(args: argparse.Namespace) -> None:
    """Prune the mined schemas/ to a subset of entrypoints, in place.

    Unlike ``_select_entrypoints_for_worktree`` (which operates inside an
    already-initialised worktree), this runs *before* any worktree exists:
    it needs only ``schemas/manifest.json`` + ``entrypoints/`` (no state.json,
    no cap). Use it for the orchestrator's "validate a subset" scoping step,
    after Step 1 mining and before Step 2 sectioning. The kept ids flow through
    sectioning / batching unchanged.
    """
    conv_root = Path(args.conv_root).resolve()
    manifest = _load_manifest(conv_root)
    entrypoints = _load_entrypoints(conv_root, manifest)
    if not entrypoints:
        _die(2, "schemas/ has no entrypoints — run schema_mine --out first")

    keep_ids = [x.strip() for x in (args.ids or "").split(",") if x.strip()]
    if not keep_ids:
        _die(2, "--ids is required and must be a non-empty comma-separated list")

    known = {ep.get("id") for ep in entrypoints}
    unknown = [i for i in keep_ids if i not in known]
    if unknown:
        _die(2, f"unknown entrypoint id(s) not in manifest: {unknown}")

    keep_set = set(keep_ids)
    selected = [ep for ep in entrypoints if ep.get("id") in keep_set]

    removed = _prune_schemas_to_selected(conv_root, manifest, selected)
    print(
        f"[validate.py] scoped schemas/ to {len(selected)} entrypoint(s); "
        f"kept {[e.get('id') for e in selected]}; "
        f"removed {removed} unselected entrypoint(s)"
    )


# ---------------------------------------------------------------------------
# seed-venv
# ---------------------------------------------------------------------------


def _install_scanned_imports(
    venv_python: Path,
    conv_root: Path,
    schemas_dir: Path,
    original_source: Optional[Path],
) -> None:
    """Best-effort: statically scan every entrypoint's import closure for
    third-party modules and pre-install them (mapped to pip names) into the
    shared phase venv.

    This eliminates the recurring per-trial ``No module named X`` failures for
    implicit/transitive imports (findspark, pytz, mysql, openpyxl, ...) that the
    workload's requirements.txt never declared. Additive and guarded: if the
    scan finds nothing new, nothing is installed. NEVER aborts the run — a
    failed install logs a warning and continues."""
    if not schemas_dir.is_dir():
        return
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        import datagen as _datagen
        import schema_mine as _schema_mine
    except Exception as exc:  # pragma: no cover - import guard
        print(f"[validate.py] WARNING: import-scan unavailable ({exc}); skipping pre-install")
        return
    try:
        entrypoints = _datagen.read_entrypoints(str(schemas_dir))
    except Exception as exc:
        print(f"[validate.py] WARNING: could not read entrypoints for import-scan: {exc}")
        return

    # Resolve each entrypoint + its import-closure to absolute source files.
    search_roots = [conv_root] + ([original_source] if original_source is not None else [])
    files: set[str] = set()
    import_roots: set[str] = set()
    for ep in entrypoints:
        rels = list(ep.get("closure") or [])
        if ep.get("path"):
            rels.append(ep["path"])
        for rel in rels:
            for base in search_roots:
                cand = base / rel
                if cand.is_file():
                    files.add(str(cand))
                    break
        for r in (ep.get("import_roots") or ["."]):
            for base in search_roots:
                cand = base / r
                if cand.is_dir():
                    import_roots.add(str(cand))
    if not files:
        return

    try:
        pip_names = _schema_mine.pip_requirements_for_imports(
            sorted(files), sorted(import_roots)
        )
    except Exception as exc:
        print(f"[validate.py] WARNING: import-scan failed: {exc}")
        return

    # Reuse the conflict-exclusion guard. Also drop harness-managed pins so we
    # never override the harness's authoritative versions -- EXCEPT the two core
    # packages the harness must always guarantee are importable (the pyspark /
    # snowflake "not found" cases are a seeding bug). These are installed
    # unpinned, so uv keeps any already-present harness version.
    scan_exclude = _ALWAYS_EXCLUDE | (
        _HARNESS_WINS_FILTER - {"pyspark", "snowflake-connector-python"}
    )
    to_install = sorted({p for p in pip_names if p.lower() not in scan_exclude})
    if not to_install:
        return

    print(
        f"[validate.py] seed-venv: import-scan pre-installing "
        f"{len(to_install)} package(s): {to_install}"
    )
    # Fast path: one batched install. On any failure, fall back to per-package
    # best-effort so a single unresolvable package doesn't block the rest.
    batch = subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), *to_install],
        capture_output=True, text=True,
    )
    if batch.returncode == 0:
        return
    print("[validate.py] WARNING: batched import-scan install failed; retrying per-package (best-effort)")
    for pkg in to_install:
        res = subprocess.run(
            ["uv", "pip", "install", "--python", str(venv_python), pkg],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or "").strip().splitlines()
            reason = tail[-1] if tail else "unknown error"
            print(f"[validate.py] WARNING: could not pre-install '{pkg}' (best-effort, continuing): {reason}")


def cmd_seed_venv(args: argparse.Namespace) -> None:
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)
    workspace = _validation_root(conv_root)
    phase = args.phase  # "a" or "b"

    if phase == "a":
        venv_dir = workspace / "shared" / ".venv-source"
    else:
        venv_dir = workspace / "shared" / ".venv-scos"

    venv_python = venv_dir / "bin" / "python"

    # 1. Create venv if absent
    if not venv_python.is_file():
        print(f"[validate.py] creating {venv_dir.name} venv with uv...")
        result = subprocess.run(
            ["uv", "venv", "--seed", "--python", "3.11", str(venv_dir)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            _die(2, f"uv venv failed:\n{result.stderr}")

    # 2. Install phase-specific core deps
    if phase == "a":
        # Determine flavor: databricks-connect vs pyspark+delta
        schemas_dir = workspace / "shared" / "schemas"
        use_databricks = False
        if schemas_dir.is_dir():
            try:
                sys.path.insert(0, str(SCRIPTS_DIR))
                import datagen as _datagen
                entrypoints = _datagen.read_entrypoints(str(schemas_dir))
                has_dbx_ep = any(
                    ep.get("source_runtime") == "databricks"
                    for ep in entrypoints
                )
                # Check creds
                dbx_env_file = state.get("databricks", {}).get("env_file")
                has_dbx_creds = bool(dbx_env_file) or any(
                    k.startswith("DATABRICKS_") for k in os.environ
                )
                use_databricks = has_dbx_ep and has_dbx_creds
            except Exception as exc:
                print(f"[validate.py] WARNING: could not read entrypoints for flavor detection: {exc}")

        if use_databricks:
            print("[validate.py] source venv: installing databricks-connect (databricks source_runtime detected)")
            subprocess.run(
                ["uv", "pip", "install", "--python", str(venv_python),
                 "databricks-connect>=15.0",
                 "pandas", "pyarrow",
                ],
                check=True,
            )
        else:
            print("[validate.py] source venv: installing pyspark + delta-spark")
            subprocess.run(
                ["uv", "pip", "install", "--python", str(venv_python),
                 "pyspark>=3.5,<4",
                 "delta-spark>=3.1,<4",
                 "jdk4py>=21.0.4.0",
                 "pandas", "pyarrow",
                ],
                check=True,
            )
    else:
        # Phase B — SCOS venv
        print("[validate.py] scos venv: installing snowpark-connect + jdk4py")
        subprocess.run(
            ["uv", "pip", "install", "--python", str(venv_python),
             "snowpark-connect",
             # snowpark-connect declares no pyspark requirement of its own, but it
             # runs against Spark 3.5 and breaks on PySpark 4 (which renamed
             # PySparkException's `error_class` kwarg to `errorClass`, surfacing as
             # "SNOWPARK CONNECT ERROR CODE: 5001"). `pyspark` is in
             # _HARNESS_WINS_FILTER, so the workload's own pin is dropped and the
             # import scan then installs pyspark unpinned -- which resolves to 4.x
             # unless we put an authoritative version here first.
             "pyspark>=3.5,<4",
             "jdk4py>=21.0.4.0",
             "pandas", "pyarrow",
            ],
            check=True,
        )

    # 3. Always install test tooling
    subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python),
         "pytest>=7.0",
         "pytest-xdist",
         "pytest-json-report",
        ],
        check=True,
    )

    # 4. Discover and install workload requirements
    original_source_path = state.get("paths", {}).get("original_source")
    original_source = Path(original_source_path) if original_source_path else None
    workload_reqs_applied = False

    reqs_file = getattr(args, "requirements", None)
    if reqs_file:
        reqs_file = Path(reqs_file)
        if not reqs_file.exists():
            _die(2, f"--requirements path does not exist: {reqs_file}")
    else:
        for candidate_root in [original_source, conv_root]:
            if candidate_root is None:
                continue
            for name in ("requirements.txt",):
                p = candidate_root / name
                if p.exists():
                    reqs_file = p
                    break
            if reqs_file:
                break

    if reqs_file:
        print(f"[validate.py] seed-venv: using requirements from {reqs_file}")
        # Filter out packages that conflict with the phase's core deps
        # (_ALWAYS_EXCLUDE / _HARNESS_WINS_FILTER are module-level constants).
        filtered_lines = []
        warned = []
        local_file_dropped = []
        harness_wins_dropped = []
        for line in reqs_file.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                filtered_lines.append(line)
                continue
            pkg_name = stripped.split("==")[0].split(">=")[0].split("<=")[0].split("<")[0].split(">")[0].split("[")[0].split("!")[0].split("@")[0].strip().lower()
            if pkg_name in _ALWAYS_EXCLUDE:
                warned.append(pkg_name)
                continue
            if pkg_name in _HARNESS_WINS_FILTER:
                harness_wins_dropped.append(pkg_name)
                continue
            # Drop direct local-file PEP 508 references
            if "@" in stripped and "file://" in stripped:
                try:
                    after = stripped.split("file://", 1)[1].strip()
                    path_str = after.split()[0]
                    if path_str.startswith("/"):
                        candidate_path = path_str
                    else:
                        candidate_path = "/" + path_str.split("/", 1)[1] if "/" in path_str else path_str
                    if not Path(candidate_path).exists():
                        local_file_dropped.append(pkg_name or stripped)
                        continue
                except Exception:
                    local_file_dropped.append(pkg_name or stripped)
                    continue
            filtered_lines.append(line)
        if warned:
            print(f"[validate.py] WARNING: excluded conflicting packages from requirements: {warned}")
        if local_file_dropped:
            print(f"[validate.py] WARNING: dropped non-portable local file:// requirements: {local_file_dropped}")
        if harness_wins_dropped:
            print(f"[validate.py] WARNING: dropped harness-wins workload pins (harness install authoritative): {harness_wins_dropped}")
        filtered_reqs = venv_dir / "_filtered_requirements.txt"
        filtered_reqs.write_text("\n".join(filtered_lines) + "\n")
        subprocess.run(
            ["uv", "pip", "install", "--python", str(venv_python),
             "-r", str(filtered_reqs),
            ],
            check=True,
        )
        workload_reqs_applied = True

    # 4b. Static import scan → pre-install third-party modules the entrypoints
    #     import implicitly/transitively but that requirements.txt never
    #     declared. Prevents repeated per-trial "No module named X" failures.
    #     Best-effort: never aborts the run.
    _install_scanned_imports(
        venv_python, conv_root, workspace / "shared" / "schemas", original_source
    )

    # 5. pip check (best-effort warning)
    check_result = subprocess.run(
        ["uv", "pip", "check", "--python", str(venv_python)],
        capture_output=True, text=True,
    )
    if check_result.returncode != 0 and check_result.stdout.strip():
        for line in check_result.stdout.strip().splitlines():
            print(f"[validate.py] WARN (pip check): {line.strip()}")

    reqs_label = f"applied ({reqs_file})" if workload_reqs_applied else "absent"
    print(f"seed-venv complete: phase={phase}, venv={venv_dir}, workload-reqs={reqs_label}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)
    _materialize_manual_review_statuses(conv_root, state)
    trials = state.get("trials", {})
    phase = state.get("phase", "init")
    phase_filter = getattr(args, "phase", "all")
    output_json = getattr(args, "json", False)

    if output_json:
        # JSON output: one object per trial with status, phase_a_pass, phase_b_pass
        def _is_passing(it: Dict[str, Any]) -> bool:
            return it.get("passing", 0) > 0 and it.get("failing", 0) == 0

        result = {}
        for trial_id, trial in sorted(trials.items()):
            status = trial.get("status", "pending")
            phase_a_pass = any(_is_passing(it) for it in trial.get("phase_a_iters", []))
            phase_b_pass = any(_is_passing(it) for it in trial.get("phase_b_iters", []))
            result[trial_id] = {
                "status": status,
                "phase_a_pass": phase_a_pass,
                "phase_b_pass": phase_b_pass,
            }
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        sys.exit(0)

    print(f"Phase: {phase}")
    print(f"Phase A iter: {state.get('phase_a', {}).get('iter', 0)}")
    print(f"Phase B iter: {state.get('phase_b', {}).get('iter', 0)}")
    print()

    if not trials:
        print("No trials configured.")
        sys.exit(1)

    any_pending = False
    any_review = False
    any_blocked = False

    for trial_id, trial in sorted(trials.items()):
        status = trial.get("status", "pending")
        if status == "pending":
            any_pending = True
        elif status == "passed_no_baseline":
            any_review = True
        elif status == "hard_stuck":
            any_blocked = True

        print(f"  {trial_id}: {status}")

        if args.verbose:
            if phase_filter in ("A", "all"):
                for it in trial.get("phase_a_iters", []):
                    print(f"    Phase A iter {it.get('iter', '?')}: "
                          f"pass={it.get('passing', 0)} fail={it.get('failing', 0)} "
                          f"patches_extended={it.get('extended_patches', 0)}")
            if phase_filter in ("B", "all"):
                for it in trial.get("phase_b_iters", []):
                    print(f"    Phase B iter {it.get('iter', '?')}: "
                          f"pass={it.get('passing', 0)} fail={it.get('failing', 0)} "
                          f"issues={it.get('issues', 0)} "
                          f"fix_commit={it.get('fix_commit', 'none')}")

    # Show last Phase B iter across all trials (most recent fix attempt).
    if phase == "B" and not args.verbose:
        last_b = None
        for trial in trials.values():
            for it in trial.get("phase_b_iters", []):
                if last_b is None or it.get("iter", 0) > last_b.get("iter", 0):
                    last_b = it
        if last_b is not None:
            print(f"\n  Last Phase B iter: failing={last_b.get('failing', '?')}, "
                  f"fix_commit={last_b.get('fix_commit', 'none')}")

    print()
    if any_blocked:
        sys.exit(2)
    elif any_pending or any_review:
        sys.exit(1)
    elif phase != "phase_b_done":
        # All trials show terminal/passed for the phases run so far, but
        # Phase B has not been completed yet. The re-entry guard treats
        # RC=0 as "all done, exit"; emit RC=1 (work pending) so the
        # orchestrator advances to the next pending step.
        sys.exit(1)
    else:
        print("All trials passed.")
        sys.exit(0)


# ---------------------------------------------------------------------------
# _write_report_md  (called by cmd_summary)
# ---------------------------------------------------------------------------


def _write_report_md(
    conv_root: Path,
    workspace: Path,
    summary: Dict[str, Any],
    trials: Dict[str, Any],
    database: str,
    golden_schemas: Dict[str, Any],
    state: Dict[str, Any],
) -> None:
    """Write a markdown REPORT at results/REPORT.md."""
    report_path = workspace / "results" / "REPORT.md"

    decision = summary.get("decision", {})
    overall = decision.get("overall", "unknown")
    passed = len([t for t in trials.values() if t.get("status") == "passed"])
    lines: List[str] = [
        "# Validation Report", "",
        f"**Outcome:** {overall} ({passed}/{len(trials)} passed)", "",
        "## Trials", "",
        "| Trial | Status | A iters | B iters | Fix Category | Reason (hard_stuck / phase_a_skip) |",
        "|-------|--------|---------|---------|--------------|-------------------|",
    ]
    for tid, t in sorted(trials.items()):
        st = t.get("status", "pending")
        a, b = len(t.get("phase_a_iters", [])), len(t.get("phase_b_iters", []))
        lines.append(f"| {tid} | {st} | {a} | {b} | "
                     f"{t.get('fix_category', '')} | "
                     f"{t.get('hard_stuck_reason') or t.get('phase_a_skip_reason', '')} |")
    lines.append("")

    dispatches = state.get("fixer_dispatches", [])
    if dispatches:
        lines += ["## Fixer Dispatches", ""]
        for d in dispatches:
            lines.append(f"- iter={d.get('iter')} class={d.get('error_class')} "
                         f"trials={d.get('trials_affected', [])} outcome={d.get('outcome')}")
        lines.append("")

    query_ids: List[str] = []
    for t in trials.values():
        for b_iter in t.get("phase_b_iters", []):
            qid = b_iter.get("scos_query_id") or b_iter.get("query_id", "")
            if qid:
                query_ids.append(str(qid))
    if query_ids:
        lines += ["## Phase B SCOS Query IDs", ""]
        lines += [f"- `{qid}`" for qid in query_ids]
        lines.append("")

    lines += ["## Infrastructure", "", f"- Database: `{database}`"]
    if golden_schemas:
        for ep_id, gs in golden_schemas.items():
            lines.append(f"- Schema ({ep_id}): `{gs.get('schema', '?')}`")
    else:
        lines.append(f"- Schema: `{state.get('snowflake', {}).get('schema', '?')}`")
    lines.append("")

    report_cmd = _report_app_command(conv_root)
    lines += [
        "## Interactive report",
        "",
        "Copy/paste this single line (do not break across lines):",
        "",
        "```bash",
        report_cmd,
        "```",
        "",
    ]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[validate.py] REPORT.md written to {report_path}")


def _report_app_command(conv_root: Path) -> str:
    """Single-line shell command to launch the Streamlit validation report."""
    skill_dir = SCRIPTS_DIR.parent
    project_root = skill_dir.parent
    report_app = SCRIPTS_DIR / "report" / "validation_report_app.py"
    validation_root = conv_root / VALIDATION_DIRNAME
    return (
        f"uv run --project {project_root} python -m streamlit run "
        f"{report_app} -- --run-root {validation_root}"
    )


def _print_report_app_command(conv_root: Path) -> None:
    """Emit a copy-pasteable one-liner (no internal line breaks)."""
    cmd = _report_app_command(conv_root)
    print()
    print("Open the interactive report (copy/paste this single line):")
    print(cmd)


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Auto-promote passing trials (run-tests + summary safety net)
# ---------------------------------------------------------------------------


def _phase_a_baseline_produced(trial: Dict[str, Any]) -> bool:
    """True when Phase A recorded at least one clean passing iter."""
    return any(
        it.get("passing", 0) >= 1 and it.get("failing", 0) == 0
        for it in trial.get("phase_a_iters", [])
    )


def _trial_lacks_baseline(trial: Dict[str, Any]) -> bool:
    """True when a trial has no comparable Phase A baseline — it was skipped, or
    derived to passed_no_baseline (the skip reason is preserved through promotion).
    Keeps the report's Phase A verdict / has_baseline consistent with
    _infer_pass_status, which treats an explicit skip reason as authoritative even
    when an unusable Phase A capture recorded a passing iter."""
    return (trial.get("status") in ("phase_a_skipped", "passed_no_baseline")
            or bool(trial.get("phase_a_skip_reason")))


def _verdict_reason(trial: Dict[str, Any]) -> str:
    """Human-readable reason for a trial's report verdict.

    hard_stuck / passed_no_baseline both carry the model's required reason
    (hard_stuck_reason / phase_a_skip_reason — the latter preserved through the
    phase_a_skipped -> passed_no_baseline promotion); a plain pass reports the
    match. Anything else has no reason to surface."""
    return (
        trial.get("hard_stuck_reason")
        or trial.get("phase_a_skip_reason")
        or ("matched baseline" if trial.get("status") == "passed" else "")
    )


def _infer_pass_status(trial: Dict[str, Any]) -> Optional[str]:
    """Return 'passed' or 'passed_no_baseline' when latest Phase B iter is clean."""
    b_iters = trial.get("phase_b_iters", [])
    if not b_iters:
        return None
    last_b = b_iters[-1]
    if not (last_b.get("passing", 0) >= 1 and last_b.get("failing", 0) == 0):
        return None
    # An explicit Phase A skip means there is no trustworthy baseline — even if an
    # (empty / unusable) Phase A capture happened to record a passing iter, the
    # trial promotes to passed_no_baseline, never passed.
    if trial.get("phase_a_skip_reason"):
        return "passed_no_baseline"
    if _phase_a_baseline_produced(trial):
        return "passed"
    return "passed_no_baseline"


_AUTO_PROMOTABLE_STATUSES = frozenset({"pending", "phase_a_skipped"})
_REFRESHABLE_PHASE_B_STATUSES = frozenset({"passed", "passed_no_baseline", "hard_stuck"})


def _maybe_auto_promote_passing_trial(
    conv_root: Path,
    trial_id: str,
    *,
    phase: str,
    iter_n: int,
    passing: int,
    failing: int,
    allow_terminal_refresh: bool = False,
) -> bool:
    """Promote a trial to passed/passed_no_baseline after a clean Phase B pytest run.

    Called from run-tests so the LLM does not need record-trial-status for passes.
    Returns True when status was promoted.
    """
    if phase.upper() != "B":
        return False
    if not (passing >= 1 and failing == 0):
        return False

    state = _load_state(conv_root)
    trial = state.get("trials", {}).get(trial_id)
    if not trial:
        return False
    allowed_statuses = _AUTO_PROMOTABLE_STATUSES | (
        _REFRESHABLE_PHASE_B_STATUSES if allow_terminal_refresh else frozenset()
    )
    if trial.get("status") not in allowed_statuses:
        return False

    new_status = _infer_pass_status(trial)
    if not new_status:
        return False

    trial["status"] = new_status
    trial["final_iter"] = iter_n
    trial.pop("hard_stuck_reason", None)
    _advance_phase(state, conv_root)
    _save_state(conv_root, state)
    _append_event(conv_root, {
        "kind": "trial_marked",
        "trial_id": trial_id,
        "status": new_status,
        "reason": "auto-promoted by run-tests",
        "auto": True,
    })
    print(
        f"[validate.py] auto-promoted trial {trial_id} status={new_status} "
        f"final_iter={iter_n}"
    )
    return True


def _maybe_reopen_trial_after_phase_b_failure(
    conv_root: Path,
    trial_id: str,
    *,
    phase: str,
    iter_n: int,
    passing: int,
    failing: int,
    allow_terminal_refresh: bool = False,
) -> bool:
    """Reopen a previously terminal Phase B trial when a rerun fails."""
    if phase.upper() != "B":
        return False
    if not allow_terminal_refresh:
        return False
    if failing == 0:
        return False

    state = _load_state(conv_root)
    trial = state.get("trials", {}).get(trial_id)
    if not trial:
        return False
    if trial.get("status") not in _REFRESHABLE_PHASE_B_STATUSES:
        return False

    prior = trial.get("status")
    trial["status"] = "pending"
    trial.pop("final_iter", None)
    trial.pop("hard_stuck_reason", None)
    # Reopen re-runs Phase A from scratch — drop the stale skip reason so a trial
    # that now yields a real baseline auto-promotes to passed, not passed_no_baseline.
    trial.pop("phase_a_skip_reason", None)
    state["phase"] = "phase_a_done"
    # Reopening rewinds Phase B — it is no longer complete (Phase A still is).
    state.setdefault("milestones", {})["phase_b_complete"] = False
    _save_state(conv_root, state)
    _append_event(conv_root, {
        "kind": "trial_marked",
        "trial_id": trial_id,
        "status": "pending",
        "reason": (
            f"reopened by run-tests after failed Phase B rerun "
            f"(iter {iter_n}: passing={passing}, failing={failing}, prior={prior})"
        ),
        "auto": True,
    })
    print(
        f"[validate.py] reopened trial {trial_id} from {prior} to pending "
        f"after failed Phase B rerun final_iter={iter_n}"
    )
    return True


def _recover_pending_trials(state: Dict[str, Any]) -> int:
    """Safety net: promote trials that passed Phase B but lack a terminal status.

    Primary path is run-tests auto-promotion after a clean pytest iter. This covers
    raw pytest (no run-tests), or a crash between record-iter and auto-promote.

    Only promotes clear passes. Failures stay non-terminal so record-trial-status
    hard_stuck gates are not bypassed.

    Returns count of trials recovered.
    """
    recovered = 0
    for trial_id, t in state.get("trials", {}).items():
        if t.get("status") not in _AUTO_PROMOTABLE_STATUSES:
            continue
        new_status = _infer_pass_status(t)
        if not new_status:
            continue
        t["status"] = new_status
        b_iters = t.get("phase_b_iters", [])
        if b_iters:
            t["final_iter"] = b_iters[-1].get("iter")
        t.pop("hard_stuck_reason", None)
        recovered += 1
    if recovered:
        print(
            f"summary: recovered {recovered} trials without terminal status",
            file=sys.stderr,
        )
    return recovered


def _check_terminal_trials(state: Dict[str, Any]) -> Optional[List[str]]:
    """Return list of non-terminal trial IDs, or None if all are terminal."""
    non_terminal = sorted(
        tid for tid, t in state.get("trials", {}).items()
        if t.get("status") not in _TERMINAL_TRIAL_STATUSES
    )
    return non_terminal if non_terminal else None


def _require_terminal_trials_for_summary(state: Dict[str, Any]) -> None:
    """Every selected trial must reach a terminal verdict before summary."""
    non_terminal = _check_terminal_trials(state)
    if non_terminal:
        _die(1, "summary blocked — non-terminal trials: "
                + ", ".join(non_terminal))


def _require_clean_output_for_summary(conv_root: Path) -> None:
    """run_index reads validation-branch git log; uncommitted Output/ is invisible."""
    res = _git(conv_root, "status", "--porcelain", "Output/")
    if res.returncode != 0:
        return  # not a git repo — init warned already
    dirty = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if dirty:
        _die(1, "summary blocked — uncommitted Output/ changes; run "
                "`validate.py commit --kind migration-fix` or `--kind test-patch`")


def cmd_summary(args: argparse.Namespace) -> None:
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)
    _materialize_manual_review_statuses(conv_root, state)
    if _recover_pending_trials(state):
        _save_state(conv_root, state)
    _require_terminal_trials_for_summary(state)
    _require_clean_output_for_summary(conv_root)
    workspace = _validation_root(conv_root)
    trials = state.get("trials", {})

    database = state.get("snowflake", {}).get("database", "SCOS_VALIDATION")
    cleanup_sql = []
    golden_schemas = state.get("snowflake", {}).get("golden_schemas", {})
    if golden_schemas:
        for ep_id, gs in golden_schemas.items():
            schema_name = gs.get("schema", "")
            if schema_name:
                cleanup_sql.append(f"DROP SCHEMA IF EXISTS {database}.{schema_name} CASCADE")
    else:
        schema = state.get("snowflake", {}).get("schema", "")
        if schema:
            cleanup_sql.append(f"DROP SCHEMA IF EXISTS {database}.{schema} CASCADE")

    # Print trial summary table
    print("\n" + "=" * 70)
    print("SCOS Validation Summary")
    print("=" * 70)
    print(f"\n{'Trial ID':<40} {'Status':<14} {'A iters':<10} {'B iters':<10} {'Reason'}")
    print("-" * 90)

    total_passed = 0
    total_manual_review = 0
    total_hard_stuck = 0
    total_pending = 0
    total_a_iters = 0
    total_b_iters = 0
    warnings: List[str] = []
    # Phase A was attempted if any trial recorded a Phase A iter (milestone-free
    # signal — venv seeding is no longer a milestone).
    phase_a_started = any(t.get("phase_a_iters") for t in trials.values())

    total_divergences = 0

    for trial_id, trial in sorted(trials.items()):
        status = trial.get("status", "pending")
        a_iters = trial.get("phase_a_iters", [])
        b_iters = trial.get("phase_b_iters", [])
        a_count = len(a_iters)
        b_count = len(b_iters)
        div_count = len(trial.get("documented_divergences", []))
        total_a_iters += a_count
        total_b_iters += b_count
        total_divergences += div_count
        reason = trial.get("hard_stuck_reason") or trial.get("phase_a_skip_reason", "")
        status_label = status
        if div_count > 0:
            status_label = f"{status} ({div_count} div)"
        if len(reason) > 80:
            print(f"{trial_id:<40} {status_label:<14} {a_count:<10} {b_count:<10}")
            print(f"{'':>40} reason: {reason}")
        else:
            print(f"{trial_id:<40} {status_label:<14} {a_count:<10} {b_count:<10} {reason}")

        if status == "passed":
            total_passed += 1
        elif status == "passed_no_baseline":
            total_manual_review += 1
        elif status == "hard_stuck":
            total_hard_stuck += 1
        else:
            total_pending += 1

        # Hard warning: Phase A ran (some trial has iters) but a non-skipped trial
        # recorded none → runner skipped record-iter. Exclude phase_a_skipped trials:
        # they legitimately have no Phase A iters (baseline was skipped, not missed).
        is_skipped = status == "phase_a_skipped" or bool(trial.get("phase_a_skip_reason"))
        if phase_a_started and a_count == 0 and not is_skipped:
            warnings.append(
                f"trial {trial_id!r}: Phase A ran but phase_a_iters=[] "
                "— source-runner did not call record-iter"
            )

    print("-" * 90)
    print(
        f"{'TOTALS':<40} passed={total_passed} manual_review={total_manual_review} "
        f"hard_stuck={total_hard_stuck} pending={total_pending}"
    )
    print(f"{'ITERS':<40} A={total_a_iters} B={total_b_iters}")
    if total_divergences > 0:
        print(f"{'DOCUMENTED DIVERGENCES':<40} {total_divergences} column(s) across trials")
    print()
    if golden_schemas:
        print(f"Golden schemas ({len(golden_schemas)}):")
        for ep_id, gs in golden_schemas.items():
            print(f"  {ep_id}: {database}.{gs.get('schema', '?')}")
    else:
        schema = state.get("snowflake", {}).get("schema", "")
        print(f"Ephemeral schema: {database}.{schema}")
    print("Cleanup SQL:")
    for sql in cleanup_sql:
        print(f"  {sql};")
    print()

    for w in warnings:
        print(f"[validate.py] WARN: {w}", file=sys.stderr)

    # Compute decision block
    if total_manual_review > 0:
        ship_rec = "review"
        overall = "partial"
    elif total_passed == len(trials) and total_hard_stuck == 0:
        ship_rec = "green"
        overall = "passed"
    elif total_hard_stuck > 0:
        ship_rec = "block"
        overall = "blocked"
    else:
        ship_rec = "review"
        overall = "partial"

    blocking_reasons = []
    for tid, t in sorted(trials.items()):
        st = t.get("status", "pending")
        if st == "hard_stuck":
            blocking_reasons.append({
                "trial": tid,
                "kind": st,
                "reason": t.get("hard_stuck_reason", ""),
            })

    non_blocking_qualifications: List[Dict[str, Any]] = []
    for tid, t in sorted(trials.items()):
        if t.get("status") == "passed_no_baseline":
            non_blocking_qualifications.append({
                "trial": tid,
                "kind": "manual_review_required",
                "detail": "SCOS run passed without a trustworthy Phase A baseline",
            })
        for div in t.get("documented_divergences", []):
            non_blocking_qualifications.append({
                "trial": tid,
                "kind": "documented_divergence",
                "detail": f"{div.get('sink_id')}.{div.get('column')}: {div.get('reason', '')}",
            })

    decision = {
        "overall": overall,
        "ship_recommendation": ship_rec,
        "blocking_reasons": blocking_reasons,
        "non_blocking_qualifications": non_blocking_qualifications,
        "non_blocking_divergences": total_divergences,
        "phase_a_passes": total_passed,
        "manual_review_required": total_manual_review,
        "phase_b_passes": sum(
            1 for t in trials.values()
            if t.get("status") == "passed"
            and len(t.get("phase_b_iters", [])) > 0
        ),
    }

    # Write summary.json
    summary = {
        "decision": decision,
        "trials": trials,
        "phase_a_iters": state.get("phase_a", {}).get("iter", 0),
        "phase_b_iters": state.get("phase_b", {}).get("iter", 0),
        "ephemeral_schemas": {ep: f"{database}.{gs.get('schema', '')}" for ep, gs in golden_schemas.items()} if golden_schemas else {"default": f"{database}.{state.get('snowflake', {}).get('schema', '')}"},
        "cleanup_sql": cleanup_sql,
        "warnings": warnings,
    }
    results_dir = workspace / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(results_dir / "summary.json", summary)
    print(f"[validate.py] summary written to {results_dir / 'summary.json'}")

    # --- Write REPORT.md (markdown audit trail) ---
    _write_report_md(conv_root, workspace, summary, trials, database, golden_schemas, state)

    # --- Build canonical run_index.json ---
    _index_ns = argparse.Namespace(conv_root=str(conv_root))
    cmd_build_index(_index_ns)

    # --- Cleanup stale artifacts ---
    _cleanup_artifacts(workspace)

    # --- Final gate: verify expected output files exist ---
    expected = {
        "summary.json": results_dir / "summary.json",
        "REPORT.md": results_dir / "REPORT.md",
        "run_index.json": workspace / "run_index.json",
        "events.jsonl": workspace / "events.jsonl",
    }
    missing = [name for name, p in expected.items() if not p.is_file()]
    if missing:
        print(
            f"[validate.py] error: summary incomplete — missing required output(s): "
            f"{', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(4)
    print(f"[validate.py] summary complete — all {len(expected)} required outputs present")
    _print_report_app_command(conv_root)


# ---------------------------------------------------------------------------
# record-iter
# ---------------------------------------------------------------------------


def _record_iter_impl(
    conv_root: Path,
    trial_id: str,
    phase: str,  # "A" or "B" (uppercase)
    iter_n: int,
    passing: int,
    failing: int,
    fix_category: Optional[str] = None,
    _extra_entry: Optional[Dict[str, Any]] = None,
) -> None:
    """Core record-iter logic. Callable from cmd_record_iter and cmd_run_tests."""
    state = _load_state(conv_root)
    trials = state.setdefault("trials", {})
    if trial_id not in trials:
        raise ValueError(f"trial {trial_id!r} not in state.trials — run prepare-batches first")

    iter_key = "phase_a_iters" if phase == "A" else "phase_b_iters"
    existing_iters = trials[trial_id].get(iter_key, [])
    existing = next((it for it in existing_iters if it.get("iter") == iter_n), None)
    if existing is not None:
        if fix_category is not None:
            if existing.get("fix_category") == fix_category:
                print(
                    f"[validate.py] iter {iter_n} Phase {phase} already recorded for "
                    f"{trial_id} with fix_category={fix_category} — no-op"
                )
            else:
                existing["fix_category"] = fix_category
                _save_state(conv_root, state)
                _append_event(conv_root, {
                    "kind": "iter_tagged",
                    "trial_id": trial_id,
                    "phase": f"phase_{phase.lower()}",
                    "iter": iter_n,
                    "fix_category": fix_category,
                })
                print(
                    f"[validate.py] tagged Phase {phase} iter {iter_n} for trial "
                    f"{trial_id}: fix_category={fix_category}"
                )
        else:
            print(
                f"[validate.py] iter {iter_n} Phase {phase} already recorded for "
                f"{trial_id} — no-op"
            )
        return

    entry: Dict[str, Any] = {"iter": iter_n, "passing": passing, "failing": failing}
    if fix_category is not None:
        entry["fix_category"] = fix_category
    if _extra_entry:
        entry.update(_extra_entry)

    if phase == "A":
        trials[trial_id].setdefault("phase_a_iters", []).append(entry)
    else:
        trials[trial_id].setdefault("phase_b_iters", []).append(entry)

    _save_state(conv_root, state)
    _append_event(conv_root, {
        "kind": "iter_recorded",
        "trial_id": trial_id,
        "phase": f"phase_{phase.lower()}",
        "iter": iter_n,
        "passing": passing,
        "failing": failing,
    })
    print(
        f"[validate.py] recorded Phase {phase} iter {iter_n} for trial {trial_id}: "
        f"pass={passing} fail={failing}"
    )


def cmd_record_iter(args: argparse.Namespace) -> None:
    conv_root = Path(args.conv_root).resolve()
    # Accept both short (`A`/`B`) and long (`phase_a`/`phase_b`) forms.
    _phase_arg = (args.phase or "").lower()
    if _phase_arg in ("phase_a", "a"):
        phase = "A"
    elif _phase_arg in ("phase_b", "b"):
        phase = "B"
    else:
        _die(2, f"--phase must be one of A|B|phase_a|phase_b, got {args.phase!r}")

    extra: Dict[str, Any] = {}
    if args.issues is not None:
        extra["issues"] = args.issues
    if args.patches_extended is not None:
        extra["extended_patches"] = args.patches_extended
    if args.fix_commit is not None:
        extra["fix_commit"] = args.fix_commit

    # Default to the phase's current iter (the one run-tests just recorded).
    iter_n = args.iter if args.iter is not None else _phase_iter(_load_state(conv_root), phase)

    try:
        _record_iter_impl(
            conv_root=conv_root,
            trial_id=args.trial_id,
            phase=phase,
            iter_n=iter_n,
            passing=args.passing,
            failing=args.failing,
            fix_category=getattr(args, "fix_category", None),
            _extra_entry=extra or None,
        )
    except ValueError as exc:
        _die(2, str(exc))


# ---------------------------------------------------------------------------
# no-progress detector
#
# 53% of failing iterations re-hit the *identical* failure signature as the
# entrypoint's previous iteration — each repeat re-runs a ~20-40s trial and
# burns an LLM turn with no change. This detector is pure OBSERVABILITY +
# ESCALATION by default: it emits a `no_progress_detected` event when the same
# failure signature repeats and NEVER changes a trial's terminal outcome.
#
# CRITICAL: entrypoints that eventually PASS often do so *after* a long
# identical plateau (real data: passes survived 10x-26x identical plateaus, and
# the pass always lands on an iter where the signature CHANGES). So a naive
# "abandon after K identical" WOULD drop real passes and is forbidden as the
# default. Any hard stop is behind an explicit opt-in env var (default OFF) and,
# even when enabled, is routed through the SAME hard_stuck fixer gate used by
# record-trial-status — so it can only fire once a fixer has been dispatched,
# never on a raw plateau.
# ---------------------------------------------------------------------------

_NO_PROGRESS_SIGNAL_K_DEFAULT = 4

_NP_SCOS_CODE_RE = re.compile(r"SNOWPARK CONNECT ERROR CODE:\s*(\d+)", re.IGNORECASE)
_NP_NO_MODULE_RE = re.compile(r"No module named ['\"]?([A-Za-z0-9_.]+)")
_NP_NAME_UNDEF_RE = re.compile(r"name ['\"]([A-Za-z0-9_.]+)['\"] is not defined")
_NP_EMPTY_SINK_RE = re.compile(
    r"(?:empty sink|produced 0 rows|returned 0 rows|\b0 rows\b|no rows|"
    r"empty (?:result|dataframe|output)|EmptySink)",
    re.IGNORECASE,
)
_NP_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_NP_HEXADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_NP_LINENO_RE = re.compile(r"line \d+", re.IGNORECASE)
_NP_EXC_LINE_RE = re.compile(r"^(?:E\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*(?::\s*(.*))?$")
_NP_EXC_SUFFIXES = ("Error", "Exception", "Exit", "Iteration", "Warning", "Interrupt")
# Spark error-class token inside a SCOS message, e.g. [TABLE_OR_VIEW_NOT_FOUND],
# [PARSE_SYNTAX_ERROR]. Used to disambiguate the same SCOS code (esp. 5001).
_NP_SPARK_ERRCLASS_RE = re.compile(r"\[([A-Z][A-Z0-9_]{3,})\]")
# Fully-qualified dotted identifier (table/object), e.g. cams_glue_catalog.l0.tbl.
_NP_QUALIFIED_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


def _no_progress_signal_k() -> int:
    """Threshold at which the (non-destructive) no_progress signal is emitted.

    Overridable via SCOS_NO_PROGRESS_SIGNAL_K; defaults to 4.
    """
    raw = os.environ.get("SCOS_NO_PROGRESS_SIGNAL_K")
    if raw:
        try:
            v = int(raw)
        except ValueError:
            return _NO_PROGRESS_SIGNAL_K_DEFAULT
        if v >= 1:
            return v
    return _NO_PROGRESS_SIGNAL_K_DEFAULT


def _no_progress_hard_stop_k() -> Optional[int]:
    """Opt-in hard-stop threshold. Returns None (OFF) unless
    SCOS_NO_PROGRESS_HARD_STOP_K is set to a positive int.

    Even when set, the hard stop is gated (see _maybe_no_progress_hard_stop) so it
    can only fire after a fixer dispatch — it can never cut a raw real-pass plateau.
    """
    raw = os.environ.get("SCOS_NO_PROGRESS_HARD_STOP_K")
    if not raw:
        return None
    try:
        v = int(raw)
    except ValueError:
        return None
    return v if v >= 1 else None


def _np_scrub(text: str) -> str:
    """Strip volatile bits (uuids, hex addrs, line numbers) so that two identical
    failures normalize to the same signature."""
    text = _NP_UUID_RE.sub("<uuid>", text)
    text = _NP_HEXADDR_RE.sub("<addr>", text)
    text = _NP_LINENO_RE.sub("line <n>", text)
    return text.strip()


def _normalize_failure_signature(message: Optional[str]) -> str:
    """Map a pytest failure message to a stable signature class.

    Ordered, most-specific first:
      SNOWPARK CONNECT ERROR CODE: 5001  -> "SCOS_ERR_5001"
      No module named 'foo'              -> "NoModule:foo"
      name 'bar' is not defined          -> "NameUndef:bar"
      (empty sink / 0 rows / no rows)    -> "EmptySink"
      <ExcType>[: <head>]                -> "StopIteration" / "AssertionError:<head>"
      (nothing recognizable)             -> scrubbed first line / "Unknown"

    Identical failures yield identical signatures; any real change in the failure
    yields a different signature (that is the "progress" the counter watches for).
    """
    if not message:
        return "Unknown"
    text = str(message)

    m = _NP_SCOS_CODE_RE.search(text)
    if m:
        # A SCOS error CODE alone is too coarse: the same code (esp. 5001) covers
        # very different failures — PARSE_SYNTAX_ERROR on empty SQL vs
        # TABLE_OR_VIEW_NOT_FOUND on object A vs object B. Fixing one and hitting a
        # DIFFERENT one is progress, so enrich with the Spark error class and a
        # scrubbed object identifier when present. A bare banner (no class) keeps
        # the plain "SCOS_ERR_<code>" signature (back-compat).
        parts = [f"SCOS_ERR_{m.group(1)}"]
        cls = _NP_SPARK_ERRCLASS_RE.search(text)
        if cls:
            parts.append(cls.group(1))
            obj = _NP_QUALIFIED_ID_RE.search(_np_scrub(text[cls.end():]))
            if obj:
                parts.append(obj.group(0)[:80])
        return ":".join(parts)
    m = _NP_NO_MODULE_RE.search(text)
    if m:
        return f"NoModule:{m.group(1)}"
    m = _NP_NAME_UNDEF_RE.search(text)
    if m:
        return f"NameUndef:{m.group(1)}"
    if _NP_EMPTY_SINK_RE.search(text):
        return "EmptySink"

    # Generic: the exception line is the last recognizable "<Type>: <msg>" line.
    for line in reversed(text.splitlines()):
        s = line.strip()
        if not s:
            continue
        lm = _NP_EXC_LINE_RE.match(s)
        if not lm:
            continue
        cand = lm.group(1).rsplit(".", 1)[-1]
        if cand.endswith(_NP_EXC_SUFFIXES):
            head = _np_scrub((lm.group(2) or "").splitlines()[0] if lm.group(2) else "")
            head = head[:80].strip()
            return f"{cand}:{head}" if head else cand

    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return _np_scrub(first)[:80] or "Unknown"


def _extract_failure_message(test: Dict[str, Any]) -> Optional[str]:
    """Pull a representative failure message out of one pytest-json-report test.

    Prefers the crash message (compact, stable) then the longrepr, across the
    call / setup / teardown stages, then any top-level longrepr.
    """
    for stage in ("call", "setup", "teardown"):
        stage_obj = test.get(stage) or {}
        crash = stage_obj.get("crash") or {}
        msg = crash.get("message")
        if msg:
            return str(msg)
        longrepr = stage_obj.get("longrepr")
        if longrepr:
            return str(longrepr)
    lr = test.get("longrepr")
    return str(lr) if lr else None


def _next_no_progress_run(
    prev_sig: Optional[str], prev_count: int, new_sig: Optional[str]
) -> Tuple[Optional[str], int]:
    """Advance the consecutive-identical-failure run for one iteration.

    ``new_sig`` is None when the iteration made progress (it PASSED). A passing
    iter OR a changed signature RESETS the run — only an identical repeat extends
    it. Pure/side-effect-free so it is directly unit-testable.
    """
    if new_sig is None:  # passed -> progress
        return None, 0
    if new_sig == prev_sig:  # identical repeat -> no progress
        return new_sig, prev_count + 1
    return new_sig, 1  # signature CHANGED -> progress, restart run at 1


def _maybe_no_progress_hard_stop(
    conv_root: Path,
    trial_id: str,
    *,
    signature: str,
    count: int,
    iter_n: int,
) -> bool:
    """Opt-in, gated hard stop. Default OFF (see _no_progress_hard_stop_k).

    Even when enabled it routes through _check_hard_stuck_gate with no exhaustion
    flags, which only permits hard_stuck when a fixer dispatch is already on record.
    So it can never truncate a raw plateau on a trial that has not yet been through
    the fixer — preserving the observed real-pass plateaus. Returns True if it
    changed the trial's status.
    """
    hard_k = _no_progress_hard_stop_k()
    if hard_k is None or count < hard_k:
        return False
    state = _load_state(conv_root)
    trial = state.get("trials", {}).get(trial_id)
    if trial is None or trial.get("status") in _TERMINAL_TRIAL_STATUSES:
        return False
    if _check_hard_stuck_gate(trial_id, trial, state) is not None:
        # Gate not satisfied (no fixer dispatch on record) -> remain pure signal.
        return False
    trial["status"] = "hard_stuck"
    trial["final_iter"] = iter_n
    trial["hard_stuck_reason"] = (
        f"no-progress hard stop: failure signature {signature!r} repeated "
        f"{count} consecutive iterations after fixer dispatch"
    )
    _advance_phase(state, conv_root)
    _save_state(conv_root, state)
    _append_event(conv_root, {
        "kind": "trial_marked",
        "trial_id": trial_id,
        "status": "hard_stuck",
        "reason": trial["hard_stuck_reason"],
        "auto": True,
        "no_progress": True,
    })
    print(
        f"[validate.py] no_progress hard stop: trial {trial_id} -> hard_stuck "
        f"(sig={signature} count={count})"
    )
    return True


def _update_no_progress(
    conv_root: Path,
    trial_id: str,
    *,
    phase: str,
    iter_n: int,
    failure_signature: Optional[str],
) -> None:
    """Track consecutive identical failure signatures per trial and, at the signal
    threshold, emit a non-destructive `no_progress_detected` event.

    ``failure_signature`` is None when this iteration passed. This NEVER changes a
    trial's terminal outcome under default settings — it only records the run on
    ``trial['no_progress']`` and appends an event. A hard stop happens only via the
    opt-in, gated _maybe_no_progress_hard_stop.
    """
    state = _load_state(conv_root)
    trial = state.get("trials", {}).get(trial_id)
    if trial is None:
        return
    tracker = trial.get("no_progress") or {}
    sig, count = _next_no_progress_run(
        tracker.get("signature"), int(tracker.get("count", 0)), failure_signature
    )
    if sig is None:
        # progress (pass / changed signature that cleared): drop stale tracker
        if "no_progress" in trial:
            trial.pop("no_progress", None)
            _save_state(conv_root, state)
        return

    trial["no_progress"] = {"signature": sig, "count": count, "iter": iter_n}
    _save_state(conv_root, state)

    if count < _no_progress_signal_k():
        return

    _append_event(conv_root, {
        "kind": "no_progress_detected",
        "trial_id": trial_id,
        "phase": f"phase_{phase.lower()}",
        "iter": iter_n,
        "signature": sig,
        "count": count,
        "hint": (
            "same failure signature repeated across consecutive iterations — "
            "change approach (dispatch the migration-fixer subagent / try a "
            "different repair path) rather than re-running the identical trial"
        ),
    })
    print(
        f"[validate.py] no_progress_detected trial={trial_id} sig={sig} "
        f"count={count} (signal only — outcome unchanged)"
    )
    _maybe_no_progress_hard_stop(
        conv_root, trial_id, signature=sig, count=count, iter_n=iter_n
    )


# ---------------------------------------------------------------------------
# run-tests
# ---------------------------------------------------------------------------


def cmd_run_tests(args: argparse.Namespace) -> None:
    """Pytest wrapper: auto-deselects terminal trials, auto-emits record-iter."""
    conv_root = Path(args.conv_root).resolve()
    phase = args.phase.lower()  # "a" or "b"
    verify_all: bool = getattr(args, "verify_all", False)
    target_trial_id: Optional[str] = getattr(args, "trial_id", None)
    allow_terminal_refresh = bool(phase == "b" and (verify_all or target_trial_id))

    # 1. Resolve VENV_PYTHON
    venv_name = ".venv-source" if phase == "a" else ".venv-scos"
    venv_python = conv_root / "Validation" / "shared" / venv_name / "bin" / "python"
    if not venv_python.is_file():
        _die(
            2,
            f"venv not found: {venv_python}\n"
            f"Run: validate.py seed-venv --conv-root {conv_root} --phase {phase}",
        )

    # 2. Load state
    state = _load_state(conv_root)
    trials: Dict[str, Any] = state.get("trials", {})
    # run-tests owns the iteration counter: auto-advance unless explicitly overridden.
    iter_n: int = args.iter if args.iter is not None else _phase_iter(state, phase) + 1
    if target_trial_id and target_trial_id not in trials:
        _die(2, f"trial {target_trial_id!r} not in state.trials")

    # Persist iter_n BEFORE running pytest so it survives if pytest dies.
    phase_key = _phase_key(phase)
    state.setdefault(phase_key, {})["iter"] = iter_n
    _save_state(conv_root, state)

    # 3. Compute deselect set
    if target_trial_id:
        deselect_ids: set = {tid for tid in trials if tid != target_trial_id}
    elif verify_all:
        deselect_ids: set = set()
    else:
        if phase == "a":
            terminal = {"passed", "passed_no_baseline", "hard_stuck", "phase_a_skipped"}
        else:
            terminal = {"passed", "passed_no_baseline", "hard_stuck"}
        deselect_ids = {
            tid for tid, t in trials.items() if t.get("status") in terminal
        }

    # 4. Build pytest command
    tests_dir = conv_root / "Validation" / "tests"
    results_dir = conv_root / "Validation" / "results" / f"phase_{phase}"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / f"pytest_{iter_n}.json"

    skill_dir = SCRIPTS_DIR.parent
    cmd = [
        str(venv_python), "-m", "pytest", str(tests_dir),
        "-n", "auto", "--tb=short",
        "--json-report", f"--json-report-file={report_path}",
    ]
    if deselect_ids:
        k_expr = " or ".join(f"test_{tid}" for tid in sorted(deselect_ids))
        cmd += ["-k", f"not ({k_expr})"]

    # 5. Environment
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["SKILL_DIRECTORY"] = str(skill_dir)
    env["SCOS_RESULTS_DIR"] = str(results_dir)
    if phase == "b":
        env["SCOS_FLAVOR"] = "scos"

    # 6. Run pytest — stream stdout/stderr to terminal
    print(
        f"[validate.py] run-tests: phase={phase} iter={iter_n} "
        f"deselected={len(deselect_ids)} verify_all={verify_all}"
        f"{f' trial_id={target_trial_id}' if target_trial_id else ''}"
    )
    proc = subprocess.run(cmd, env=env)
    rc = proc.returncode

    # 7. Parse JSON report; emit record-iter per pending trial that ran
    ran_count = 0
    pass_total = 0
    fail_total = 0

    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[validate.py] WARNING: could not parse JSON report: {exc}", file=sys.stderr)
            report = {}

        # Aggregate outcomes per trial_id
        trial_results: Dict[str, Dict[str, int]] = {}
        # First failure message per trial -> normalized signature for no-progress.
        trial_fail_msgs: Dict[str, str] = {}
        for test in report.get("tests", []):
            nodeid = test.get("nodeid", "")
            file_part = nodeid.split("::")[0] if "::" in nodeid else nodeid
            stem = Path(file_part).stem  # "test_<trial_id>"
            if not stem.startswith("test_"):
                continue
            tid = stem[5:]  # strip "test_" prefix
            if tid not in trials or tid in deselect_ids:
                continue
            outcome = test.get("outcome", "")
            tr = trial_results.setdefault(tid, {"passing": 0, "failing": 0})
            if outcome == "passed":
                tr["passing"] += 1
            elif outcome in ("failed", "error"):
                tr["failing"] += 1
                if tid not in trial_fail_msgs:
                    msg = _extract_failure_message(test)
                    if msg:
                        trial_fail_msgs[tid] = msg

        for tid, tr in trial_results.items():
            ran_count += 1
            pass_total += tr["passing"]
            fail_total += tr["failing"]
            try:
                _record_iter_impl(
                    conv_root=conv_root,
                    trial_id=tid,
                    phase=phase.upper(),
                    iter_n=iter_n,
                    passing=tr["passing"],
                    failing=tr["failing"],
                )
                _maybe_reopen_trial_after_phase_b_failure(
                    conv_root,
                    tid,
                    phase=phase,
                    iter_n=iter_n,
                    passing=tr["passing"],
                    failing=tr["failing"],
                    allow_terminal_refresh=allow_terminal_refresh,
                )
                _maybe_auto_promote_passing_trial(
                    conv_root,
                    tid,
                    phase=phase,
                    iter_n=iter_n,
                    passing=tr["passing"],
                    failing=tr["failing"],
                    allow_terminal_refresh=allow_terminal_refresh,
                )
                # Non-destructive no-progress tracking: signature is None on a pass
                # (progress), else the normalized failure class. Emits a signal at
                # the threshold; never changes terminal outcome under defaults.
                failure_signature = (
                    _normalize_failure_signature(trial_fail_msgs.get(tid))
                    if tr["failing"] > 0
                    else None
                )
                _update_no_progress(
                    conv_root,
                    tid,
                    phase=phase,
                    iter_n=iter_n,
                    failure_signature=failure_signature,
                )
            except Exception as exc:
                print(
                    f"[validate.py] WARNING: record-iter failed for {tid}: {exc}",
                    file=sys.stderr,
                )

    # 8. Summary line
    print(
        f"run-tests: phase={phase} iter={iter_n} ran={ran_count} "
        f"passed={pass_total} failed={fail_total} deselected={len(deselect_ids)}"
        f"{f' trial_id={target_trial_id}' if target_trial_id else ''}"
    )
    # 9. Preserve pytest exit code
    sys.exit(rc)



# ---------------------------------------------------------------------------
# record-trial-status
# ---------------------------------------------------------------------------


_TRIAL_STATUSES = (
    "pending",
    "passed",
    "passed_no_baseline",
    "hard_stuck",
    "phase_a_skipped",
)

_SCHEMA_REPAIR_CATEGORIES = frozenset({"schema_gap", "analysis_repair"})
_HARNESS_REPAIR_CATEGORIES = frozenset({"harness_failure"})
_PATCH_REPAIR_CATEGORIES = frozenset({"patch_failure"})


def _repair_iters(trial: Dict[str, Any], categories: frozenset) -> List[Dict[str, Any]]:
    """Phase A/B iters whose fix_category is in *categories*."""
    all_iters = (trial.get("phase_b_iters") or []) + (trial.get("phase_a_iters") or [])
    return [it for it in all_iters if it.get("fix_category") in categories]


def _check_hard_stuck_gate(
    trial_id: str,
    trial: Dict[str, Any],
    state: Dict[str, Any],
    *,
    analysis_repair_exhausted: bool = False,
    harness_repair_exhausted: bool = False,
    patch_repair_exhausted: bool = False,
) -> Optional[str]:
    """Return an error message when hard_stuck is not allowed, else None."""
    fixer_dispatches = state.get("fixer_dispatches", [])
    trial_dispatches = [
        d for d in fixer_dispatches if trial_id in d.get("trials_affected", [])
    ]
    # A SINGLE fixer dispatch that changed nothing (outcome="no_change") is NOT
    # credible exhaustion — it is the exact pattern behind premature hard_stuck
    # (historically ~99% of hard_stuck EPs passed on a later run). A repeated
    # SCOS error is almost always patchable (redirect catalog/Iceberg/IO reads to
    # the provisioned SCOS schema), so require either a dispatch that actually
    # attempted a change (outcome != "no_change") or repeated dispatches showing
    # persistence.
    credible_dispatch = (
        any(d.get("outcome") != "no_change" for d in trial_dispatches)
        or len(trial_dispatches) >= 2
    )
    if credible_dispatch:
        return None

    schema_repair_iters = _repair_iters(trial, _SCHEMA_REPAIR_CATEGORIES)
    harness_repair_iters = _repair_iters(trial, _HARNESS_REPAIR_CATEGORIES)
    patch_repair_iters = _repair_iters(trial, _PATCH_REPAIR_CATEGORIES)

    if not (analysis_repair_exhausted or harness_repair_exhausted or patch_repair_exhausted):
        return (
            f"REJECTED: cannot mark trial '{trial_id}' hard_stuck — "
            f"no credible fixer dispatch (a single 'no_change' dispatch does not "
            f"count — a repeated SCOS error is almost always patchable) and no "
            f"inline-repair exhaustion on record. "
            f"For code/dialect errors dispatch the migration-fixer first; "
            f"for missing tables/columns run the inline schema-repair loop "
            f"(edit schemas/, datagen, re-pytest — the harness reseeds "
            f"automatically) and record-iter --fix-category analysis_repair, then pass "
            f"--analysis-repair-exhausted once repair is exhausted; for harness kit "
            f"issues use --fix-category harness_failure and "
            f"--harness-repair-exhausted; for missing blueprint patches use "
            f"--fix-category patch_failure and --patch-repair-exhausted. "
            f"See agents/scos-runner.md."
        )

    if analysis_repair_exhausted and not schema_repair_iters:
        return (
            f"REJECTED: cannot mark trial '{trial_id}' hard_stuck with "
            f"--analysis-repair-exhausted before any schema-repair attempt is on "
            f"record. Try the schema/data path first (edit "
            f"schemas/entrypoints/<id>/tables/<KEY>.json, datagen, "
            f"re-pytest — the harness reseeds automatically) and tag the iter with "
            f"record-iter --fix-category analysis_repair. Exhaustion means you "
            f"already tried the credible schema/data fixes and still have no viable "
            f"next move. See agents/scos-runner.md."
        )

    if harness_repair_exhausted and not harness_repair_iters:
        return (
            f"REJECTED: cannot mark trial '{trial_id}' hard_stuck with "
            f"--harness-repair-exhausted before any harness repair is on record. "
            f"Fix the shared kit under Validation/tests/ and record the attempt via "
            f"record-iter --fix-category harness_failure before declaring "
            f"exhaustion. Exhaustion means the kit path has been tried and there is "
            f"still no credible next harness fix. See agents/scos-runner.md."
        )

    if patch_repair_exhausted and not patch_repair_iters:
        return (
            f"REJECTED: cannot mark trial '{trial_id}' hard_stuck with "
            f"--patch-repair-exhausted before any patch repair is on record. Add "
            f"blueprint patches with patch-add and record the attempt via "
            f"record-iter --fix-category patch_failure before declaring "
            f"exhaustion. Exhaustion means you already tried the credible plumbing "
            f"patches and still have no viable next move. See agents/scos-runner.md."
        )

    return None


def cmd_record_trial_status(args: argparse.Namespace) -> None:
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)
    trial_id = args.trial_id
    status = args.status

    if status not in _TRIAL_STATUSES:
        _die(2, f"invalid status {status!r}; expected one of: {', '.join(_TRIAL_STATUSES)}")

    trials = state.setdefault("trials", {})
    if trial_id not in trials:
        _die(2, f"trial {trial_id!r} not in state.trials")

    # Idempotent: if already at the same terminal status, no-op.
    current = trials[trial_id].get("status")
    if current == status and current in _TERMINAL_TRIAL_STATUSES:
        print(f"[validate.py] trial {trial_id} already {status} — no-op")
        sys.exit(0)

    # Hard-gate: require fixer dispatch OR documented inline-repair exhaustion
    # (schema/data, harness kit, or blueprint patches). phase_a_skipped is exempt.
    if status == "hard_stuck":
        gate_err = _check_hard_stuck_gate(
            trial_id,
            trials[trial_id],
            state,
            analysis_repair_exhausted=getattr(args, "analysis_repair_exhausted", False),
            harness_repair_exhausted=getattr(args, "harness_repair_exhausted", False),
            patch_repair_exhausted=getattr(args, "patch_repair_exhausted", False),
        )
        if gate_err:
            sys.stderr.write(gate_err + "\n")
            sys.exit(2)

    # Hard-gate: 'passed' requires the latest Phase B iter to show passing>=1
    # and failing==0.  passed_no_baseline is exempt (no baseline comparison).
    if status == "passed":
        b_iters = trials[trial_id].get("phase_b_iters", [])
        if not b_iters:
            sys.stderr.write(
                f"REJECTED: cannot mark trial '{trial_id}' passed — "
                f"no Phase B iterations recorded.\n"
            )
            sys.exit(2)
        latest = b_iters[-1]
        latest_passing = latest.get("passing", 0)
        latest_failing = latest.get("failing", 0)
        if not (latest_passing >= 1 and latest_failing == 0):
            sys.stderr.write(
                f"REJECTED: cannot mark trial '{trial_id}' passed — "
                f"latest Phase B iter {latest.get('iter', '?')} has "
                f"passing={latest_passing}, failing={latest_failing}. "
                f"Status 'passed' requires passing>=1 and failing==0.\n"
            )
            sys.exit(2)

    # Hard-gate: 'passed_no_baseline' is never set directly. It is DERIVED — a
    # trial marked 'phase_a_skipped' (with a required --reason) that then passes
    # Phase B is auto-promoted to passed_no_baseline, carrying that reason into the
    # report. Marking it directly would let a trial reach a no-baseline verdict
    # without a surfaced reason, which is exactly the gap this closes. There is no
    # separate "empty baseline" / "not comparable" status: if Phase A cannot
    # produce a comparable baseline (unexpected empty declared sink, connector read
    # that can't be satisfied locally, different sinks captured), mark
    # phase_a_skipped. Intentionally empty sinks use the per-sink allow_empty
    # override instead.
    if status == "passed_no_baseline":
        _die(
            2,
            f"REJECTED: do not set trial '{trial_id}' passed_no_baseline directly. "
            f"It is derived: if Phase A produced a comparable baseline, COMPARE it "
            f"(document-divergence + 'passed' for cosmetic diffs, hard_stuck for a "
            f"real one). If Phase A cannot produce a comparable baseline at all, mark "
            f"'phase_a_skipped --reason <why>' — Phase B then auto-promotes a clean "
            f"run to passed_no_baseline with that reason. See agents/scos-runner.md.",
        )

    # Hard-gate: hard_stuck and phase_a_skipped are LAST-RESORT statuses that are
    # surfaced in the final report. Both REQUIRE a human-readable --reason so the
    # report can explain why a trial could not be matched (hard_stuck) or why no
    # local baseline could be produced (phase_a_skipped). Do not accept them blank.
    reason = getattr(args, "reason", None)
    if status in ("hard_stuck", "phase_a_skipped") and not (reason or "").strip():
        _die(
            2,
            f"REJECTED: --reason is required when marking trial {trial_id!r} "
            f"'{status}'. It is surfaced in the final report. For phase_a_skipped, "
            f"name the specific construct the source runtime genuinely cannot "
            f"execute (last resort — connector reads must be patched, not skipped). "
            f"For hard_stuck, state the confirmed no-workaround limitation (rare — "
            f"most failures are fixable). See agents/source-runner.md / scos-runner.md.",
        )

    trials[trial_id]["status"] = status
    final_iter = args.final_iter if args.final_iter is not None else _latest_trial_iter(trials[trial_id])
    if final_iter > 0 or args.final_iter is not None:
        trials[trial_id]["final_iter"] = final_iter
    if status == "hard_stuck" and reason:
        trials[trial_id]["hard_stuck_reason"] = reason
    elif status == "phase_a_skipped" and reason:
        # Dedicated field, PRESERVED when Phase B promotes phase_a_skipped ->
        # passed_no_baseline, so the report can still explain the missing baseline.
        trials[trial_id]["phase_a_skip_reason"] = reason
    if status == "passed":
        trials[trial_id].pop("hard_stuck_reason", None)
        trials[trial_id].pop("phase_a_skip_reason", None)

    _advance_phase(state, conv_root)
    _save_state(conv_root, state)
    _append_event(conv_root, {
        "kind": "trial_marked",
        "trial_id": trial_id,
        "status": status,
        "reason": reason or "",
    })
    if final_iter > 0 or args.final_iter is not None:
        print(f"[validate.py] trial {trial_id} status={status} final_iter={final_iter}")
    else:
        print(f"[validate.py] trial {trial_id} status={status}")


_TERMINAL_TRIAL_STATUSES = {
    "passed",
    "passed_no_baseline",
    "hard_stuck",
}


def _advance_phase(state: Dict[str, Any], conv_root: Optional[Path] = None) -> None:
    """Advance state['phase'] when all trials reach a terminal status.

    Goes init -> phase_a_done once Phase A iters exist for every trial AND
    every trial is terminal; goes phase_a_done -> phase_b_done once Phase B
    iters exist AND every trial is terminal. Idempotent.

    Phase completion is the meaningful milestone (not venv seeding): flips the
    ``phase_a_complete`` / ``phase_b_complete`` milestones in lockstep with the
    phase field, and — when ``conv_root`` is given — appends a
    ``milestone_completed`` event so the timeline records the transition.
    """
    trials = state.get("trials") or {}
    if not trials:
        return
    phase = state.get("phase", "init")
    all_terminal = all(t.get("status") in _TERMINAL_TRIAL_STATUSES for t in trials.values())
    if not all_terminal:
        return
    have_phase_a = all(
        t.get("phase_a_iters") or t.get("status") == "phase_a_skipped"
        for t in trials.values()
    )
    have_phase_b = all(t.get("phase_b_iters") for t in trials.values())
    if phase in ("init",) and have_phase_a and not have_phase_b:
        state["phase"] = "phase_a_done"
        _flip_phase_milestone(state, "phase_a_complete", conv_root)
    elif have_phase_b:
        state["phase"] = "phase_b_done"
        # Phase B implies Phase A also completed (skips count as Phase A done).
        _flip_phase_milestone(state, "phase_a_complete", conv_root)
        _flip_phase_milestone(state, "phase_b_complete", conv_root)


def _flip_phase_milestone(
    state: Dict[str, Any], milestone: str, conv_root: Optional[Path]
) -> None:
    """Set a phase-completion milestone once, emitting its event if possible."""
    milestones = state.setdefault("milestones", {})
    if milestones.get(milestone):
        return
    milestones[milestone] = True
    if conv_root is not None:
        _append_event(conv_root, {"kind": "milestone_completed", "milestone": milestone})


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------

COMMIT_PREFIXES = {
    "test-patch": "[TEST-PATCH]",
    "migration-fix": "[MIGRATION-FIX]",
}


def _git_commit_tree(conv_root: Path, tree_path: str, message: str) -> Optional[str]:
    """Stage *tree_path* (relative to conv_root) and commit. Returns the new SHA,
    or None if there was nothing to commit. Dies on git failure."""
    res = _git(conv_root, "add", tree_path)
    if res.returncode != 0:
        _die(1, f"git add failed: {res.stderr}")

    if _git(conv_root, "diff", "--cached", "--quiet").returncode == 0:
        return None  # nothing staged

    res = _git(conv_root, "commit", "-m", message)
    if res.returncode != 0:
        _die(1, f"git commit failed: {res.stderr}")
    return _git(conv_root, "rev-parse", "HEAD").stdout.strip()


def _git_commit_output(
    conv_root: Path, message: str, *, files: Optional[List[str]] = None
) -> Optional[str]:
    """Stage Output/ (or specific files under Output/) and commit.

    When *files* is provided, stages only those paths (relative to conv_root);
    otherwise stages the entire Output/ tree. Returns the new SHA, or None if
    there was nothing to commit. Dies on git failure.
    """
    if files is not None:
        return _git_commit_paths(conv_root, files, message)
    return _git_commit_tree(conv_root, "Output", message)


def _git_commit_paths(conv_root: Path, tree_paths: List[str], message: str) -> Optional[str]:
    """Stage multiple trees and commit them in one commit. Returns the new SHA, or
    None if nothing was staged. Dies on git failure. Used by ``patch-add`` to
    capture BOTH the ``Output/`` and ``Validation/source/`` sides of a blueprint
    patch in a single ``[TEST-PATCH]`` commit, so a later ``git revert`` of that
    commit undoes both sides."""
    for tp in tree_paths:
        res = _git(conv_root, "add", tp)
        if res.returncode != 0:
            _die(1, f"git add {tp} failed: {res.stderr}")
    # Scope the empty-check AND the commit to the given pathspecs so unrelated
    # already-staged index entries are neither required nor swept into the commit
    # (a pathspec-limited `git commit` does a partial commit of only those paths).
    if _git(conv_root, "diff", "--cached", "--quiet", "--", *tree_paths).returncode == 0:
        return None  # nothing staged for these paths
    res = _git(conv_root, "commit", "-m", message, "--", *tree_paths)
    if res.returncode != 0:
        _die(1, f"git commit failed: {res.stderr}")
    return _git(conv_root, "rev-parse", "HEAD").stdout.strip()


# Matches SCOS_* leaks in two forms: an assignment (`SCOS_X = ...`, not `==`) or a
# quoted string reference (`os.environ["SCOS_X"]`, `"SCOS_X"`). Bare identifier refs
# (e.g. `f"db.{SCOS_SCHEMA}"`, `return SCOS_KEY`) are INTENTIONALLY not matched:
# catching them re-introduces false positives on TEST-PATCH variables that appear in
# restructured MIGRATION-FIX diffs. A genuine bare-ref leak is caught downstream by the
# harvest cherry-pick conflict itself.
_SCOS_LEAK_RE = re.compile(r"""SCOS_[A-Z0-9_]+\s*=(?!=)|["']SCOS_[A-Z0-9_]+["']""")


def _assert_no_scos_leak_in_output(conv_root: Path) -> None:
    """Guard: [MIGRATION-FIX] commits are cherry-picked onto the deliverable, so
    they must be production-safe and may NOT introduce validation-harness
    identifiers (``SCOS_*`` env vars). Those belong in [TEST-PATCH] patches.
    Letting them into a migration-fix is the #1 cause of harvest cherry-pick
    conflicts (the test-patch already rebound the same lines to ``SCOS_*``).
    Scan the uncommitted Output/ diff for newly-added ``SCOS_*`` tokens and reject
    before committing."""
    diff = _git(conv_root, "diff", "HEAD", "--", "Output").stdout
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
            "[MIGRATION-FIX] commits are cherry-picked onto the deliverable and "
            "must be production-safe — never reference SCOS_* env vars or internal "
            "mock IDs. Rewrite connector/JDBC reads to the PRODUCTION "
            "fully-qualified name (the table's original_path in "
            "schemas/entrypoints/<id>/_meta.json or tables/ subdirectory), not a harness env var. If the change "
            "exists only to satisfy pytest, commit it with --kind test-patch "
            f"instead and split any mixed edit.\n  first offending line: {sample}\n"
        )
        sys.exit(2)


def _assert_fix_commits_clean(conv_root: Path, fix_shas: List[str]) -> None:
    """Harvest-time gate: a [MIGRATION-FIX] commit being cherry-picked onto the
    deliverable must not introduce SCOS_* harness identifiers into Output/. The
    commit-time guard (``_assert_no_scos_leak_in_output``) catches fixes made via
    ``validate.py commit``; this catches raw ``git commit`` bypasses before they
    reach the deliverable branch."""
    offenders: List[tuple] = []
    for sha in fix_shas:
        diff = _git(conv_root, "show", sha, "--", "Output").stdout
        toks: set = set()
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                toks.update(_SCOS_LEAK_RE.findall(line))
        if toks:
            offenders.append((sha, sorted(toks)))
    if offenders:
        lines = ["cannot harvest — [MIGRATION-FIX] commit(s) leak validation-harness "
                 "identifiers into Output/ (they are cherry-picked onto the deliverable "
                 "and must be production-safe):"]
        for sha, toks in offenders:
            lines.append(f"  {sha[:10]}  {', '.join(toks)}")
        lines.append("Amend each to use the production fully-qualified name (from the "
                      "table's original_path) instead of the SCOS_* env var, or move the "
                      "change into a [TEST-PATCH] commit, then re-run harvest.")
        _die(1, "\n".join(lines))


def _resolve_commit_files(conv_root: Path, raw_paths: List[str]) -> List[str]:
    """Normalize and validate paths for ``commit --files``.

    Each path may be given relative to conv_root or relative to Output/;
    paths not already under Output/ are automatically prefixed with Output/.
    Rejects any path that resolves outside Output/ (path traversal) or does
    not exist. Returns paths relative to conv_root (suitable for ``git add``).
    """
    output_root = (conv_root / "Output").resolve()
    result: List[str] = []
    for raw in raw_paths:
        candidate = (conv_root / raw).resolve()
        try:
            candidate.relative_to(output_root)
        except ValueError:
            # Not already under Output/ — try prefixing
            candidate = (conv_root / "Output" / raw).resolve()
            try:
                candidate.relative_to(output_root)
            except ValueError:
                _die(1, f"--files: '{raw}' resolves outside Output/ — path traversal rejected")
        if not candidate.exists():
            _die(1, f"--files: '{raw}' does not exist (resolved: {candidate})")
        result.append(str(candidate.relative_to(conv_root)))
    return result


def cmd_commit(args: argparse.Namespace) -> None:
    conv_root = Path(args.conv_root).resolve()
    prefix = COMMIT_PREFIXES[args.kind]
    message = args.message if args.message.startswith(prefix) else f"{prefix} {args.message}"

    if args.kind == "migration-fix":
        _assert_no_scos_leak_in_output(conv_root)

    # Authoritative trial association: record which entrypoint(s) a fix is for as
    # a git trailer so run_index can place the commit under the right
    # entrypoint(s) even when an entrypoint spans multiple files. The trailer
    # travels with the commit through harvest's cherry-pick.
    trial_ids = [t.strip() for t in (args.trial_ids or "").split(",") if t.strip()]
    if trial_ids:
        message = f"{message}\n\nSCOS-Trials: {','.join(trial_ids)}"

    sha = _git_commit_output(conv_root, message, files=_resolve_commit_files(conv_root, [f.strip() for f in (args.files or "").split(",") if f.strip()]) or None)
    if sha is None:
        if args.print_sha_only:
            print(_git(conv_root, "rev-parse", "HEAD").stdout.strip())
        else:
            print("[validate.py] nothing to commit")
        sys.exit(0)

    _append_event(conv_root, {
        "kind": "commit", "commit_kind": args.kind, "sha": sha,
        "trial_ids": trial_ids,
    })
    if args.print_sha_only:
        print(sha)
    else:
        print(f"[validate.py] committed ({args.kind}): {sha}")


# ---------------------------------------------------------------------------
# patch-add — the blueprint gatekeeper
# ---------------------------------------------------------------------------


def _io_id_from_source_name(name: str) -> str:
    """Match harness ``_io_id_from_name``: upper-snake, trailing ``_`` stripped."""
    return re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").upper()


def _aux_key_from_source_name(name: str) -> str:
    """Match harness ``_aux_key_from_name``: last segment, uppercased."""
    return name.split(".")[-1].strip().lower().upper()


def _audit_patch_scos_env_refs(conv_root: Path, entries: List[Dict[str, Any]]) -> List[str]:
    """Warn when patch text references SCOS env ids not declared in schemas."""
    manifest = _load_manifest(conv_root)
    entrypoints = _load_entrypoints(conv_root, manifest)
    declared_input: set[str] = set()
    declared_aux: set[str] = set()
    declared_sink: set[str] = set()
    for ep in entrypoints:
        for tname, tbl in (ep.get("tables") or {}).items():
            access = tbl.get("access", "read")
            category = tbl.get("category", "table")
            if category == "file" and access in ("read", "readwrite"):
                if tbl.get("relational", True):
                    declared_input.add(_io_id_from_source_name(tname))
                else:
                    declared_aux.add(_aux_key_from_source_name(tname))
            if access in ("write", "readwrite") and category != "table":
                declared_sink.add(_io_id_from_source_name(tname))

    warnings: List[str] = []
    pat = re.compile(r"SCOS_(INPUT|SINK)_([A-Za-z0-9_]+)")
    pat_aux = re.compile(r"SCOS_TEST_AUX_([A-Za-z0-9_]+)")
    for entry in entries:
        chunks = [str(entry.get("replace") or "")]
        for side in ("source", "migrated"):
            sub = entry.get(side)
            if isinstance(sub, dict):
                chunks.append(str(sub.get("replace") or ""))
        for text in chunks:
            for m in pat.finditer(text):
                kind, raw_id = m.group(1), m.group(2)
                if raw_id.endswith("_"):
                    warnings.append(
                        f"patch {entry.get('id', '?')}: SCOS_{kind}_{raw_id} has a trailing "
                        f"underscore — harness strips trailing '_' from ids "
                        f"(use SCOS_{kind}_{raw_id.rstrip('_')} for source key "
                        f"'{raw_id.rstrip('_').lower()}')"
                    )
                canon = _io_id_from_source_name(raw_id)
                if kind == "INPUT":
                    aux_key = raw_id.rstrip("_").upper()
                    pool_ok = (
                        (canon and canon in declared_input)
                        or (aux_key in declared_aux)
                    )
                    pool_label = "tables (read)"
                else:
                    pool_ok = canon and canon in declared_sink
                    pool_label = "tables (write)"
                if canon and not pool_ok:
                    warnings.append(
                        f"patch {entry.get('id', '?')}: SCOS_{kind}_{raw_id} "
                        f"(canonical {canon}) not in declared {pool_label} "
                        f"for any entrypoint"
                    )
            for m in pat_aux.finditer(text):
                raw_id = m.group(1)
                aux_key = raw_id.rstrip("_").upper()
                if aux_key and aux_key not in declared_aux:
                    warnings.append(
                        f"patch {entry.get('id', '?')}: SCOS_TEST_AUX_{raw_id} "
                        f"not in declared non-relational file sources for any entrypoint"
                     )
    return warnings


def _patch_rewrite_signature(entry: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    """A file-independent fingerprint of a *top-level* patch's rewrite:
    ``(regex, replace_all, search, replace)``. Returns None for per-side
    (``source``/``migrated``) entries and for entries whose ``relative_file`` is
    already a glob — neither is a glob-consolidation candidate."""
    rel = entry.get("relative_file") or ""
    if any(c in rel for c in "*?["):
        return None  # already a glob
    if isinstance(entry.get("source"), dict) or isinstance(entry.get("migrated"), dict):
        return None  # per-side; glob entries are top-level only
    search = entry.get("search")
    if not search:
        return None
    return (bool(entry.get("regex", False)), bool(entry.get("replace_all", False)),
            search, entry.get("replace", ""))


def _audit_patch_glob_opportunity(conv_root: Path, entries: List[Dict[str, Any]]) -> List[str]:
    """Recommend a glob when the SAME rewrite (identical search/replace/flags) is
    applied to 2+ different files — either across this batch or combined with
    patches already in the blueprint. Such repeats collapse into ONE glob entry
    (``relative_file`` with ``*``/``**``), which is the single biggest lever for
    keeping the blueprint small. Advisory only — never blocks the batch."""
    sig_to_files: Dict[Tuple[Any, ...], set] = defaultdict(set)
    try:
        existing = patch_engine.load_blueprint(conv_root).get("patches", [])
    except Exception:
        existing = []
    for e in list(existing) + list(entries):
        sig = _patch_rewrite_signature(e)
        if sig is None:
            continue
        rel = (e.get("relative_file") or "").replace("\\", "/").lstrip("/")
        if rel:
            sig_to_files[sig].add(rel)

    hints: List[str] = []
    for sig, files in sig_to_files.items():
        if len(files) < 2:
            continue
        try:
            common = os.path.commonpath(list(files))
        except ValueError:
            common = ""
        exts = {os.path.splitext(f)[1] for f in files}
        ext = exts.pop() if len(exts) == 1 else ".*"
        suggested = f"{common}/**/*{ext}" if common else f"**/*{ext}"
        search_snip = re.sub(r"\s+", " ", str(sig[2]))[:60]
        flags = []
        if sig[0]:
            flags.append("regex")
        if sig[1]:
            flags.append("replace_all")
        flag_str = f" ({', '.join(flags)})" if flags else ""
        hints.append(
            f"{len(files)} files share the SAME rewrite{flag_str} "
            f"[search: {search_snip!r}...] — replace them with ONE glob entry: "
            f'relative_file "{suggested}". Files: {sorted(files)}'
        )
    return hints


def _emit_collapse_hints(
    entries: List[Dict[str, Any]],
    existing_sigs: set,
) -> None:
    """Emit HINT for multi-file rewrite opportunities not yet in blueprint.

    Emits to stderr when 2+ entries in the batch share the same rewrite
    signature (search/replace/flags) but target different files, and that
    signature is not already committed to the blueprint.
    """
    batch_sig_to_files: Dict[Tuple[Any, ...], List[str]] = defaultdict(list)
    for _e in entries:
        _sig = _patch_rewrite_signature(_e)
        if _sig is None:
            continue
        _rel = (_e.get("relative_file") or "").replace("\\", "/").lstrip("/")
        if _rel:
            batch_sig_to_files[_sig].append(_rel)

    for _sig, _files in batch_sig_to_files.items():
        if _sig not in existing_sigs and len(set(_files)) >= 2:
            _search_snip = re.sub(r"\s+", " ", str(_sig[2]))[:60]
            print(f"[patch-add] HINT: {len(set(_files))} entries share the SAME "
                  f"rewrite [search: {_search_snip!r}...] across different files "
                  f"{sorted(set(_files))} — prefer ONE glob entry "
                  f"(relative_file with *//**) when a glob is feasible.",
                  file=sys.stderr)


def cmd_patch_add(args: argparse.Namespace) -> None:
    """Smoke-test a batch of blueprint patch entries; if every side of every
    entry passes the unique-match + compile checks, apply them all atomically,
    append them to patch_blueprint.json, and commit the Output/ side in one
    [TEST-PATCH] commit.

    Supports ``"regex": true`` on an entry: treats ``search`` as a Python regex
    (default flags; opt in to DOTALL/MULTILINE via inline ``(?s)``/``(?m)``).
    ``replace`` supports backreferences (``\\1``, ``\\g<name>``).

    Supports glob ``relative_file`` (contains ``*``, ``?``, or ``[``): expands
    to every matching file under each side's prefix, applies the patch to files
    that contain the search, silently skips files with zero matches, and fails
    only if NO file matches at all.

    The entry file (``--from-file``) holds a batch in any of these shapes::

        {"patches": [ <entry>, <entry>, ... ]}   # canonical (blueprint shape)
        [ <entry>, <entry>, ... ]                # bare list
        <entry>                                  # single entry (treated as a batch of 1)

    where each ``<entry>`` is keyed on a single ``relative_file`` (the engine
    derives ``Validation/source/<rel>`` and ``Output/<rel>``)::

        {"id": "ingress_users", "relative_file": "src/x.py", "note": "...",
         "search": "<exact text present in BOTH copies>",
         "replace": "df = spark.read.parquet(os.environ['SCOS_INPUT_USERS'])"}

    A top-level search/replace patches both sides. To handle drifted text, add a
    ``source``/``migrated`` sub-block with its own search/replace; the presence of
    a sub-block also selects which sides to patch (none ⇒ both). If any entry
    fails the unique-match + compile checks, nothing is written (exit 2)."""
    conv_root = Path(args.conv_root).resolve()
    entry_path = Path(args.from_file).resolve()
    if not entry_path.is_file():
        _die(2, f"--from-file not found: {entry_path}")
    try:
        payload = json.loads(entry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _die(2, f"--from-file is not valid JSON: {exc}")

    # Normalize to a list of entries.
    if isinstance(payload, dict) and isinstance(payload.get("patches"), list):
        entries = payload["patches"]
    elif isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = [payload]
    else:
        _die(2, "--from-file must be an object, a list, or {\"patches\": [...]}")

    audit_warns = _audit_patch_scos_env_refs(conv_root, entries)
    if audit_warns:
        if getattr(args, "force", False):
            for w in audit_warns:
                print(f"[patch-add] WARN: {w}", file=sys.stderr)
        else:
            for w in audit_warns:
                print(f"[patch-add] ERROR: {w}", file=sys.stderr)
            _die(2, "SCOS_INPUT/SINK/TEST_AUX ids above are not declared for any "
                 "entrypoint — fix the patch or re-run with --force")

    for hint in _audit_patch_glob_opportunity(conv_root, entries):
        print(f"[patch-add] HINT: {hint}", file=sys.stderr)

    # Glob consolidation is an optimization (keeps the blueprint small), not a
    # correctness gate. Emit a HINT when 2+ entries in the batch share the same
    # rewrite but target different files — do NOT hard-fail. A slightly larger
    # blueprint is strictly better than forcing the author to hand-edit source
    # (bypassing patch-add) when a glob is impossible (e.g. a sibling file has a
    # pre-existing parse error that would trip the ast.parse gate).
    existing_patches = patch_engine.load_blueprint(conv_root).get("patches", [])
    existing_sigs = {_patch_rewrite_signature(e) for e in existing_patches}
    _emit_collapse_hints(entries, existing_sigs)

    ok, results, written, deduped = patch_engine.add_patches(conv_root, entries)
    for r in results:
        label = f"{r.patch_id}/{r.side}" if r.patch_id else r.side
        status = "ok" if r.ok else "FAIL"
        detail = "" if r.ok else f" — {r.error}"
        print(f"[patch-add] {label} {r.file}: {status}{detail}")

    if not ok:
        _die(2, "patch batch rejected; nothing written")

    if deduped:
        print(f"[patch-add] skipped {len(deduped)} duplicate patch(es) "
              f"(identical to a patch already in the blueprint): {', '.join(deduped)}")

    # Only the patches that were actually applied (not deduped) get recorded.
    applied_ids = [e.get("id") for e in entries if e.get("id") not in set(deduped)]
    _append_event(conv_root, {"kind": "patch_added", "patch_ids": applied_ids,
                              "deduped_ids": deduped, "files": written})

    # Commit BOTH the Output/ and Validation/source/ sides as one discardable
    # [TEST-PATCH] commit, so a later `git revert` of that commit cleanly undoes
    # both sides (source is baseline-committed at init, so this shows only the
    # patch diff). [TEST-PATCH] commits are never cherry-picked at harvest, so the
    # Validation/source/ side never leaks into the Output/ deliverable.
    # Never gate this on the prefix of `written`: an entry may carry an absolute
    # path, in which case a ``startswith("Output/")`` filter is empty and the
    # commit is silently skipped. _git_commit_paths stages both trees
    # unconditionally and is a no-op when nothing there changed.
    if not args.no_commit:
        label = applied_ids[0] if len(applied_ids) == 1 else f"{len(applied_ids)} patches"
        sha = _git_commit_paths(
            conv_root, ["Output", os.path.join(VALIDATION_DIRNAME, "source")],
            f"[TEST-PATCH] {label}")
        if sha:
            print(f"[patch-add] committed [TEST-PATCH] {label}: {sha}")
        else:
            print("[patch-add] no Output/ or source changes to commit")
    print(f"[patch-add] applied {len(applied_ids)} patch(es) to {len(written)} file(s)"
          + (f"; {len(deduped)} deduped" if deduped else ""))


# ---------------------------------------------------------------------------
# harvest — copy Validation/, then cherry-pick [MIGRATION-FIX] commits
# ---------------------------------------------------------------------------


def _require_summary_before_harvest(conv_root: Path) -> None:
    """Harvest assumes Step 8 (summary) already wrote the canonical artifacts."""
    workspace = _validation_root(conv_root)
    summary_path = workspace / "results" / "summary.json"
    run_index_path = workspace / "run_index.json"
    if not summary_path.is_file():
        _die(1, f"{summary_path.relative_to(conv_root)} missing — run "
                "`validate.py summary` before harvest")
    if not run_index_path.is_file():
        _die(1, f"{run_index_path.relative_to(conv_root)} missing — run "
                "`validate.py summary` before harvest")


def _commit_validation_to_branch(conv_root: Path, branch: str) -> None:
    """Commit the live ``Validation/`` (incl. summary outputs) onto *branch* so it
    is durable BEFORE harvest switches away. After this, ``Validation/`` is always
    recoverable with ``git checkout <branch> -- Validation/`` even if harvest is
    killed mid-flight — there is no transient temp copy to lose."""
    src = conv_root / VALIDATION_DIRNAME
    if not src.is_dir():
        _die(1, f"{VALIDATION_DIRNAME}/ not found under {conv_root}; run validation first")
    sha = _git_commit_tree(conv_root, VALIDATION_DIRNAME,
                           f"[HARVEST] snapshot Validation/ on {branch} before switch")
    if sha:
        print(f"[validate.py] committed Validation/ onto {branch}: {sha}")


def _harvest_validation_workspace(
    conv_root: Path,
    validation_branch: str,
) -> Optional[str]:
    """Restore ``Validation/`` from *validation_branch* onto the current branch and
    commit. Sourcing from the committed branch (not a temp dir) means a kill at any
    point leaves ``Validation/`` recoverable via the same git checkout."""
    res = _git(conv_root, "checkout", validation_branch, "--", VALIDATION_DIRNAME)
    if res.returncode != 0:
        _die(1, f"could not restore {VALIDATION_DIRNAME}/ from {validation_branch}: {res.stderr}")
    message = f"[HARVEST] Validation workspace from {validation_branch}"
    sha = _git_commit_tree(conv_root, VALIDATION_DIRNAME, message)
    if sha:
        print(f"[validate.py] committed Validation/ onto current branch: {sha}")
    else:
        print("[validate.py] Validation/ unchanged on current branch (no commit)")
    return sha


def _cherry_pick_in_progress(conv_root: Path) -> bool:
    git_dir = _git(conv_root, "rev-parse", "--git-dir").stdout.strip()
    if not git_dir:
        return False
    base = (conv_root / git_dir) if not os.path.isabs(git_dir) else Path(git_dir)
    return (base / "CHERRY_PICK_HEAD").exists() or (base / "sequencer").is_dir()



def _unmerged_paths(conv_root: Path) -> List[str]:
    out = _git(conv_root, "diff", "--name-only", "--diff-filter=U").stdout
    return [f for f in out.splitlines() if f.strip()]


def _advance_cherry_pick(conv_root: Path) -> bool:
    """Drive an in-progress cherry-pick sequence to completion, auto-skipping
    picks that became **empty/redundant** — i.e. a stacked [MIGRATION-FIX] whose
    net change is already present after an earlier pick was reconciled to the
    final intended state. git stops such a pick with a non-zero status and no
    unmerged paths ("the previous cherry-pick is now empty"); we skip it rather
    than forcing the operator to run ``git cherry-pick --skip`` by hand.

    Returns True when the sequence is fully resolved (no pick in progress),
    False when a genuine conflict (unmerged paths) still needs the operator."""
    guard = 0
    while _cherry_pick_in_progress(conv_root):
        if _unmerged_paths(conv_root):
            return False  # real conflict — operator must reconcile
        # In progress, nothing conflicting, nothing to commit ⇒ empty/redundant
        # pick. Skip it; git then advances to the next pick (which may apply
        # cleanly, conflict, or end the sequence).
        _git(conv_root, "cherry-pick", "--skip")
        guard += 1
        if guard > 1000:  # safety: never spin forever
            break
    return not _cherry_pick_in_progress(conv_root)


def _finish_harvest(conv_root: Path, state: Dict[str, Any]) -> None:
    """Record harvest in state/events and commit the update on the deliverable branch."""
    git = state.get("git", {})
    validation_branch = git.get("validation_branch")
    state.setdefault("git", {})["harvested"] = True
    _save_state(conv_root, state)
    _append_event(conv_root, {"kind": "harvested", "branch": validation_branch})

    sha = _git_commit_tree(
        conv_root,
        VALIDATION_DIRNAME,
        f"[HARVEST] finalize from {validation_branch or 'validation'}",
    )
    if sha:
        print(f"[validate.py] committed Validation/ state update: {sha}")

    workspace = _validation_root(conv_root)
    if not (workspace / "run_index.json").is_file():
        _die(1, "harvest incomplete — Validation/run_index.json missing "
                "(run summary before harvest)")
    if not _load_state(conv_root).get("git", {}).get("harvested"):
        _die(1, "harvest incomplete — state.json git.harvested is not true")

    print("[validate.py] harvest deliverable check passed")
    if validation_branch:
        print(f"[validate.py] validation branch {validation_branch} kept for inspection "
              f"(delete with `git branch -D {validation_branch}` when no longer needed)")


def cmd_harvest(args: argparse.Namespace) -> None:
    """Copy Validation/ onto the original branch, then cherry-pick
    [MIGRATION-FIX] commits for Output/.

    Requires ``validate.py summary`` to have completed first (``summary.json`` and
    ``run_index.json`` must exist). ``run_index.json`` is produced by ``summary``
    and copied with ``Validation/``; harvest does not rebuild it (use ``build-index``
    manually only to recover a missing index before re-running ``summary``).

    The validation branch is KEPT for inspection — delete manually when done.

    Exit codes:
        0  harvested cleanly, or --abort succeeded
        1  git failure / precondition not met
        5  cherry-pick conflicts — resolve, then `validate.py harvest --continue`
    """
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)
    git = state.get("git", {})
    original_branch = git.get("original_branch")
    validation_branch = git.get("validation_branch")

    if getattr(args, "abort", False):
        _git(conv_root, "cherry-pick", "--abort")
        if original_branch:
            _git(conv_root, "checkout", original_branch)
        print("[validate.py] harvest aborted")
        print("RESULT=aborted")
        sys.exit(0)

    if not original_branch or not validation_branch:
        _die(1, "no validation branch recorded in state.git; init did not create one")

    # Auto-recover a stale cherry-pick left over from a prior run (not one
    # we deliberately started via --continue).
    if not getattr(args, "continue_", False) and _cherry_pick_in_progress(conv_root):
        print("[validate.py] detected stale cherry-pick in progress from a prior run; aborting it")
        _git(conv_root, "cherry-pick", "--abort")

    # Resume an in-progress cherry-pick after conflict reconciliation.
    if getattr(args, "continue_", False):
        if not _cherry_pick_in_progress(conv_root):
            # Nothing in progress — the sequence already completed (e.g. the
            # last pick was empty and got skipped, or the operator finished it
            # by hand). Finalize rather than erroring.
            print("[validate.py] no cherry-pick in progress; finalizing harvest")
            _finish_harvest(conv_root, state)
            print("RESULT=ok")
            sys.exit(0)
        # Commit the staged reconciliation, then drive any remaining picks,
        # auto-skipping ones that are now empty/redundant.
        _git(conv_root, "cherry-pick", "--continue")
        if not _advance_cherry_pick(conv_root):
            _print_harvest_conflicts(conv_root)
            print("RESULT=conflict")
            sys.exit(5)
        _finish_harvest(conv_root, state)
        print("RESULT=ok")
        sys.exit(0)

    # Make the live Validation/ (incl. summary outputs) durable on the validation
    # branch BEFORE switching away. From here on, a kill at any point is fully
    # recoverable with `git checkout <validation_branch> -- Validation/` — there is
    # no transient temp copy that a timeout can wipe.
    _require_summary_before_harvest(conv_root)
    _commit_validation_to_branch(conv_root, validation_branch)

    # Collect [MIGRATION-FIX] commits in chronological order.
    rng = f"{original_branch}..{validation_branch}"
    log = _git(conv_root, "log", "--reverse", "--grep", r"\[MIGRATION-FIX\]",
               "--format=%H", rng)
    if log.returncode != 0:
        _die(1, f"git log failed: {log.stderr}")
    fix_shas = [s for s in log.stdout.splitlines() if s.strip()]

    # Final gate: a [MIGRATION-FIX] that leaks SCOS_* into Output/ would conflict
    # on cherry-pick and pollute the deliverable. The commit-time guard catches
    # `validate.py commit`; this also catches raw `git commit` bypasses.
    _assert_fix_commits_clean(conv_root, fix_shas)

    res = _git(conv_root, "checkout", original_branch)
    if res.returncode != 0:
        _die(1, f"could not checkout {original_branch}: {res.stderr}")

    print(f"[validate.py] restoring Validation/ from {validation_branch} onto {original_branch}")
    _harvest_validation_workspace(conv_root, validation_branch)

    if not fix_shas:
        print("[validate.py] no [MIGRATION-FIX] commits to cherry-pick")
        _finish_harvest(conv_root, state)
        print("RESULT=ok")
        sys.exit(0)

    print(f"[validate.py] cherry-picking {len(fix_shas)} [MIGRATION-FIX] commit(s) onto {original_branch}")
    _git(conv_root, "cherry-pick", *fix_shas)
    # Auto-skip any pick that is empty/redundant; stop only on a real conflict.
    if not _advance_cherry_pick(conv_root):
        _print_harvest_conflicts(conv_root)
        print("RESULT=conflict")
        sys.exit(5)

    _finish_harvest(conv_root, state)
    print(f"[validate.py] harvested Validation/ + {len(fix_shas)} fix commit(s) onto {original_branch}")
    print("RESULT=ok")


def _print_harvest_conflicts(conv_root: Path) -> None:
    conflicts = _git(conv_root, "diff", "--name-only", "--diff-filter=U")
    files = [f for f in conflicts.stdout.splitlines() if f.strip()]
    print("[validate.py] cherry-pick produced conflicts:")
    for f in files:
        print(f"  - {f}")
    print("[validate.py] reconcile each file (keep the migration fix, drop any "
          "test-patch I/O rewrites), `git add` them, then run "
          "`validate.py harvest --continue --conv-root <root>`. "
          "To bail out: `validate.py harvest --abort --conv-root <root>`.")


# ---------------------------------------------------------------------------
# consolidate
# ---------------------------------------------------------------------------


def cmd_consolidate(args: argparse.Namespace) -> None:
    """Cherry-pick [MIGRATION-FIX] commits across multiple validation branches
    onto the currently checked-out branch (the deliverable).

    Does NOT read or write state.json — it is stateless w.r.t. the validation
    workspace and safe to run from any primary worktree.

    Exit codes:
        0  consolidated cleanly, --abort succeeded, or nothing to pick
        1  git failure / precondition not met
        5  cherry-pick conflicts — resolve, then re-run with --continue
        6  git is busy (index.lock or another CHERRY_PICK_HEAD in progress)
           — sleep 30 s and retry; no state was modified
    """
    conv_root = Path(args.conv_root).resolve()

    if getattr(args, "abort", False):
        _git(conv_root, "cherry-pick", "--abort")
        print("RESULT=aborted")
        sys.exit(0)

    if getattr(args, "continue_", False):
        if not _cherry_pick_in_progress(conv_root):
            print("[validate.py] no cherry-pick in progress")
            sys.exit(0)
        _git(conv_root, "cherry-pick", "--continue")
        if not _advance_cherry_pick(conv_root):
            _print_harvest_conflicts(conv_root)
            print("RESULT=conflict")
            sys.exit(5)
        print("RESULT=ok")
        sys.exit(0)

    # Normal path — git itself handles concurrency.
    base_sha = args.base_sha
    branches_arg = getattr(args, "branches", None)
    if branches_arg:
        branches = [b.strip() for b in branches_arg.split(",") if b.strip()]
        if not branches:
            _die(1, "--branches must specify at least one branch name")
    else:
        res = _git(conv_root, "branch", "--list", "validation/*")
        branches = []
        for b in res.stdout.splitlines():
            raw = b.strip()
            if not raw:
                continue
            # Strip `*` (current branch) and `+` (checked out in another
            # worktree) markers — same fix as cmd_init's orphan cleanup.
            name = raw[1:].strip() if raw[0] in ("*", "+") else raw
            if name:
                branches.append(name)

    fix_shas: List[str] = []
    seen: set = set()
    for branch in branches:
        log = _git(conv_root, "log", "--reverse", "--grep", r"\[MIGRATION-FIX\]",
                   "--format=%H", f"{base_sha}..{branch}")
        if log.returncode != 0:
            _die(1, f"git log failed for {branch}: {log.stderr}")
        # Idempotency: skip commits whose change is ALREADY on the current
        # (deliverable) branch. `git cherry <upstream> <head> <limit>` lists the
        # commits in base_sha..branch that are NOT yet on HEAD, each prefixed
        # "+ <sha>"; commits already present by patch-id (e.g. cherry-picked under
        # a NEW sha by a prior harvest) are marked "-" or omitted entirely. Keep
        # only the "+" set so re-invoking consolidate over an already-harvested
        # branch (crash re-dispatch, no-`--branches` global re-run, manual retry)
        # is a clean no-op instead of a re-pick that conflicts. On any git error
        # fall back to the unfiltered range (prior behavior).
        cherry = _git(conv_root, "cherry", "HEAD", branch, base_sha)
        cherry_ok = cherry.returncode == 0
        not_applied: set = set()
        if cherry_ok:
            for _ln in cherry.stdout.splitlines():
                _ln = _ln.strip()
                if _ln.startswith("+ "):
                    not_applied.add(_ln[2:].strip())
        for sha in log.stdout.splitlines():
            sha = sha.strip()
            if not sha or sha in seen:
                continue
            if cherry_ok and sha not in not_applied:
                continue  # already on the deliverable (by patch-id) — skip
            fix_shas.append(sha)
            seen.add(sha)

    _assert_fix_commits_clean(conv_root, fix_shas)

    if not fix_shas:
        print("[validate.py] no [MIGRATION-FIX] commits to consolidate")
        print("RESULT=ok")
        sys.exit(0)

    print(f"[validate.py] cherry-picking {len(fix_shas)} [MIGRATION-FIX] commit(s)")
    res = _git(conv_root, "cherry-pick", *fix_shas)
    if res.returncode == 128:
        # Git precondition error: either another git process holds the index lock
        # or a CHERRY_PICK_HEAD already exists from another worker's unresolved
        # conflict.  Both are transient — the worker retries after 30 s.
        hint = res.stderr.strip().splitlines()[0] if res.stderr.strip() else "git busy"
        print(f"[validate.py] git busy ({hint}) — retry in 30 s")
        print("RESULT=locked")
        sys.exit(6)
    if res.returncode != 0 and not _cherry_pick_in_progress(conv_root):
        # Non-zero exit without a conflict state — a genuine git error.
        _die(1, f"git cherry-pick failed: {res.stderr.strip()}")
    # rc=0 (clean) or rc=1 (conflict, CHERRY_PICK_HEAD set) → advance / resolve.
    if not _advance_cherry_pick(conv_root):
        _print_harvest_conflicts(conv_root)
        print("RESULT=conflict")
        sys.exit(5)
    print(f"[validate.py] consolidated {len(fix_shas)} fix commit(s)")
    print("RESULT=ok")


# ---------------------------------------------------------------------------
# prepare-batches
# ---------------------------------------------------------------------------


def cmd_prepare_batches(args: argparse.Namespace) -> None:
    """Set up per-batch git worktrees with schemas scoped to each batch's entrypoints.

    Computes the batch plan from sections.json (printing it), then creates one
    worktree per batch, runs init + _select_entrypoints_for_worktree in each, and writes the
    consolidated batches_prepared.json (plan + worktree map) to
    <conv-root>/Validation/shared/.

    Exit codes:
        0  all batches prepared successfully
        1  one or more batches failed (per-batch errors recorded in json)
        2  bad arguments
        3  sections.json fails coverage check (no worktrees created)
    """
    conv_root_primary = Path(args.conv_root).resolve()
    sections_path = Path(args.sections).resolve()
    if args.worktrees_dir is None:
        worktrees_dir = conv_root_primary / VALIDATION_DIRNAME / "worktrees"
    else:
        worktrees_dir = Path(args.worktrees_dir).resolve()
    schemas_src = Path(args.schemas).resolve()

    if not sections_path.is_file():
        _die(2, f"sections.json not found: {sections_path}")
    if not schemas_src.is_dir():
        _die(2, f"--schemas dir not found: {schemas_src}")
    manifest_path = schemas_src / "manifest.json"
    if not manifest_path.is_file():
        _die(2, f"manifest.json not found in --schemas dir: {manifest_path}")

    # Compute the batch plan in-process (reusing batch) and print it
    # for the operator. The plan is folded into batches_prepared.json below.
    import batch as _be
    manifest = _load_json(manifest_path, required=True)
    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    if not isinstance(sections, list):
        _die(2, "sections.json must be a JSON array")
    _cov_errors = _be.validate_coverage(manifest, sections)
    if _cov_errors:
        _die(3, "sections.json coverage check failed:\n" + "\n".join(f"  - {e}" for e in _cov_errors))
    try:
        _batches, _warnings = _be.batch_sections(
            manifest, sections, args.max_entrypoints, args.max_weight)
    except ValueError as exc:
        _die(2, f"batching failed: {exc}")
    plan = _be._build_output(_batches, _warnings, args.max_entrypoints, args.max_weight)
    batches = plan.get("batches") or []
    if not batches:
        _die(2, "sections.json produced no batches")

    shared_dir = conv_root_primary / VALIDATION_DIRNAME / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)

    # Create the shared batch-learnings file (idempotent — skip if already present).
    learnings_path = shared_dir / "batch-learnings.md"
    if not learnings_path.exists():
        learnings_path.write_text(
            "# Batch Learnings\n\n"
            "Shared log of reusable findings from completed workers.\n"
            "Append a `### Batch <batch_id>` section after harvest completes.\n"
            "Be specific and actionable — focus on patterns that generalise to other batches.\n\n"
            "| What to include | Example |\n"
            "|-----------------|---------|\n"
            "| Patch patterns  | `boto3` S3 reads → `SCOS_INPUT_*` env var (patch P7) |\n"
            "| Schema quirks   | Table X uses `TIMESTAMP_NTZ`; cast with `.cast('timestamp_ntz')` |\n"
            "| Phase A skips   | `QUALIFY` clause unsupported in local PySpark — mark `phase_a_skipped` |\n"
            "| Systemic issues | All entrypoints in `etl/` share a broken `widget.get()` call |\n\n"
            "---\n\n",
            encoding="utf-8",
        )

    # Print the plan so the orchestrator can surface it to the user.
    s = plan["summary"]
    print(f"[validate.py] batch plan: {s['n_batches']} batches, "
          f"{s['n_entrypoints']} entrypoints, weight "
          f"min/mean/max = {s['weight_min']}/{s['weight_mean']:.1f}/{s['weight_max']}")
    for b in batches:
        print(f"  {b['batch_id']:<28} n={b['n_eps']:<3} weight={b['total_weight']}")
    for w in plan.get("warnings", []):
        print(f"  WARNING: {w}")

    worktrees_dir.mkdir(parents=True, exist_ok=True)

    # Exclude only Validation/worktrees/ (the nested per-batch checkouts) from
    # the primary repo's git index so they don't pollute `git status` or the
    # editor — the rest of Validation/ stays visible. Defensive; never blocks.
    _exclude_worktrees_from_git(conv_root_primary)

    # Change 1 (one-time source prep): copy original source into the primary's
    # Validation/source/ and run the alignment check ONCE before creating any
    # worktrees.  A bad --original-source fails exactly once here, not N times.
    _init_prepare_source(
        conv_root_primary, args.original_source, None, check_alignment=True,
    )
    primary_source_dir = conv_root_primary / VALIDATION_DIRNAME / "source"

    results: List[Dict[str, Any]] = []
    n_ok = 0
    for batch in batches:
        batch_id = batch["batch_id"]
        ep_ids: List[str] = batch.get("ep_ids") or []
        worktree_path = worktrees_dir / batch_id
        rec: Dict[str, Any] = {
            "batch_id": batch_id,
            "section_ids": batch.get("section_ids") or [],
            "section_names": batch.get("section_names") or [],
            "ep_ids": ep_ids,
            "n_eps": batch.get("n_eps", len(ep_ids)),
            "total_weight": batch.get("total_weight"),
            "worktree": str(worktree_path),
            "run_id": None,
            "validation_branch": None,
            "error": None,
        }
        try:
            # Step 1: create worktree (idempotent — skip if path already exists).
            if not worktree_path.exists():
                branch_name = f"validation-base/{batch_id}"
                res = _git(conv_root_primary, "worktree", "add", "-b", branch_name,
                           str(worktree_path), args.base_sha)
                if res.returncode != 0:
                    # Branch may already exist on re-run — add without -b.
                    res = _git(conv_root_primary, "worktree", "add",
                               str(worktree_path), args.base_sha)
                    if res.returncode != 0:
                        raise RuntimeError(f"git worktree add failed: {res.stderr.strip()}")

            # Step 2: lightweight per-worktree init — no alignment re-check, no
            # re-copy from original_source.  Source was validated against the
            # primary once above; each worktree gets a fast copy from there.
            # Each worktree receives a FRESH unique run_id (Critical Rule #3:
            # Snowflake schemas must never collide across worktrees).
            _wt_sp = _state_path(worktree_path)
            _wt_skip_init = False
            if _wt_sp.is_file():
                _wt_existing = _load_json(_wt_sp)
                if (_wt_existing.get("schema_version") == SCHEMA_VERSION
                        and any(_wt_existing.get("milestones", {}).values())):
                    _wt_skip_init = True
                    print(f"[validate.py] {batch_id}: skipping init "
                          f"(already initialized at "
                          f"run_id={_wt_existing.get('run_id', '?')})")
            if not _wt_skip_init:
                # Create Validation/ dir skeleton for this worktree.
                _ensure_worktree_skeleton(worktree_path)
                _wt_workspace = _validation_root(worktree_path)
                # Copy source from primary's already-validated Validation/source/.
                _wt_src = _wt_workspace / "source"
                if _wt_src.exists():
                    shutil.rmtree(_wt_src)
                _wt_src.mkdir(parents=True)
                shutil.copytree(str(primary_source_dir), str(_wt_src),
                                dirs_exist_ok=True)
                # Write fresh state.json with a unique run_id for this worktree.
                _wt_state = _init_write_state(
                    worktree_path, args.connection, args.original_source,
                )
                _wt_run_id = _wt_state["run_id"]
                # Create the validation branch in the worktree.
                _wt_orig_branch = _current_branch(worktree_path)
                if _wt_orig_branch:
                    _ensure_gitignore(worktree_path)
                    _wt_val_branch = f"validation/{_wt_run_id}"
                    _wt_res = _git(worktree_path, "checkout", "-b", _wt_val_branch)
                    if _wt_res.returncode != 0:
                        _wt_res = _git(worktree_path, "checkout", _wt_val_branch)
                    if _wt_res.returncode == 0:
                        _wt_state["git"] = {
                            "original_branch": _wt_orig_branch,
                            "validation_branch": _wt_val_branch,
                        }
                        _save_state(worktree_path, _wt_state)
                        print(f"[validate.py] {batch_id}: validation branch "
                              f"{_wt_val_branch} (off {_wt_orig_branch})")
                        _wt_base = _git_commit_paths(
                            worktree_path,
                            [os.path.join(VALIDATION_DIRNAME, "source")],
                            "[VALIDATION] import Phase-A source baseline",
                        )
                        if _wt_base:
                            print(f"[validate.py] {batch_id}: committed "
                                  f"Phase-A source baseline: {_wt_base}")
                    else:
                        print(f"[validate.py] WARNING: {batch_id}: could not "
                              f"create validation branch: "
                              f"{_wt_res.stderr.strip()}")
                else:
                    print(f"[validate.py] WARNING: {batch_id}: worktree is not "
                          f"a git repo; harvest/commit will not work")

            # Step 3: copy the pre-mined source schemas into the worktree.
            dst_schemas = worktree_path / VALIDATION_DIRNAME / "shared" / "schemas"
            dst_schemas.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(schemas_src), str(dst_schemas), dirs_exist_ok=True)

            # Step 4: scope the schemas to this batch's entrypoints.
            try:
                _select_entrypoints_for_worktree(
                    worktree_path, ",".join(ep_ids), max(len(ep_ids) + 1, 9999)
                )
            except SystemExit as e:
                raise RuntimeError(
                    f"_select_entrypoints_for_worktree exited with code {e.code}"
                )

            # Step 5: read back run_id and validation_branch.
            state = _load_state(worktree_path)
            rec["run_id"] = state.get("run_id")
            rec["validation_branch"] = state.get("git", {}).get("validation_branch")

            # Step 6: persist the Databricks env-file path (validated once by the
            # orchestrator) so this worker's harness uses databricks-connect. The
            # orchestrator already confirmed reachability — no per-worktree probe.
            dbx_env_file = getattr(args, "databricks_env_file", None)
            if dbx_env_file:
                state.setdefault("databricks", {})["env_file"] = dbx_env_file
                _save_state(worktree_path, state)

            n_ok += 1

        except SystemExit as e:
            msg = f"unexpected SystemExit({e.code})"
            rec["error"] = msg
            print(f"[validate.py] batch {batch_id}: ERROR — {msg}", file=sys.stderr)
        except Exception as exc:
            rec["error"] = str(exc)
            print(f"[validate.py] batch {batch_id}: ERROR — {exc}", file=sys.stderr)

        results.append(rec)

    # Write batches_prepared.json — the single source of truth: the batch plan
    # (sections, weights, caps, summary, warnings) plus the prepared worktree map.
    out_path = (
        conv_root_primary / VALIDATION_DIRNAME / "shared" / "batches_prepared.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(out_path, {
        "base_sha": args.base_sha,
        "worktrees_dir": str(worktrees_dir),
        "max_entrypoints": args.max_entrypoints,
        "max_weight": args.max_weight,
        "summary": plan.get("summary", {}),
        "warnings": plan.get("warnings", []),
        "batches": results,
    })

    total = len(batches)
    print(f"[validate.py] prepared {n_ok}/{total} batches")
    if n_ok < total:
        sys.exit(1)


# ---------------------------------------------------------------------------
# record-milestone
# ---------------------------------------------------------------------------


CANONICAL_MILESTONES = (
    "entrypoints_selected",
    "synth_deep",
    "patches_authored",
    "phase_a_complete",
    "phase_b_complete",
)


def cmd_record_milestone(args: argparse.Namespace) -> None:
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)
    milestone = args.milestone
    if milestone not in CANONICAL_MILESTONES:
        raise SystemExit(
            f"[validate.py] unknown milestone {milestone!r}; "
            f"expected one of: {', '.join(CANONICAL_MILESTONES)}"
        )

    # Gate phase milestones on trial terminal status
    if milestone == "phase_a_complete":
        # Every trial must have ≥1 phase_a_iters entry or status phase_a_skipped
        trials = state.get("trials", {})
        unready = sorted(
            tid for tid, t in trials.items()
            if not t.get("phase_a_iters") and t.get("status") != "phase_a_skipped"
        )
        if unready:
            _die(1, f"phase_a_complete blocked — trials without phase_a_iters or "
                    f"phase_a_skipped status: {', '.join(unready)}")
    elif milestone == "phase_b_complete":
        # Every trial must be terminal
        non_terminal = _check_terminal_trials(state)
        if non_terminal:
            _die(1, f"phase_b_complete blocked — non-terminal trials: "
                    f"{', '.join(non_terminal)}")

    state.setdefault("milestones", {})[milestone] = True
    _save_state(conv_root, state)
    _append_event(conv_root, {
        "kind": "milestone_completed",
        "milestone": milestone,
    })
    print(f"[validate.py] milestone {milestone!r} recorded")


# ---------------------------------------------------------------------------
# document-divergence
# ---------------------------------------------------------------------------


def _latest_trial_iter(trial: Dict[str, Any]) -> int:
    """Highest iter index recorded for this trial.

    Divergences are documented during Phase B comparison, so prefer the latest
    Phase B iter; fall back to the latest Phase A iter, then 0 when nothing has
    been recorded yet.
    """
    for key in ("phase_b_iters", "phase_a_iters"):
        nums = [
            it.get("iter")
            for it in (trial.get(key) or [])
            if isinstance(it.get("iter"), int)
        ]
        if nums:
            return max(nums)
    return 0


def _phase_key(phase: str) -> str:
    """Get the state key for a phase ('phase_a' or 'phase_b')."""
    return "phase_b" if phase.lower() in ("b", "phase_b") else "phase_a"


def _phase_iter(state: Dict[str, Any], phase: str) -> int:
    """Current iteration counter for a phase, owned by ``run-tests``.

    ``run-tests`` writes the counter at the start of each invocation;
    every other ``record-*`` command defaults to that value, i.e. the
    iteration that just ran. This is intentional so a patch/fixer-dispatch
    recorded after a failing run is attributed to the run that exposed it.
    """
    key = _phase_key(phase)
    return state.get(key, {}).get("iter", 0)


def cmd_document_divergence(args: argparse.Namespace) -> None:
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)
    manifest = _load_manifest(conv_root)

    trial_id = args.trial_id
    if trial_id not in state.get("trials", {}):
        _die(2, f"trial '{trial_id}' not found in state.json")

    trial = state["trials"][trial_id]
    divs = trial.setdefault("documented_divergences", [])

    # When --iter is omitted, auto-fill the trial's current (latest) iter so
    # documented_at_iter reflects when the divergence actually happened rather
    # than always 0.
    documented_at_iter = args.iter if args.iter is not None else _latest_trial_iter(trial)

    entry = {
        "sink_id": args.sink_id,
        "column": args.column.upper(),
        "reason": args.reason,
        "baseline_sample": args.baseline_sample or "",
        "shadow_sample": args.shadow_sample or "",
        "documented_at_iter": documented_at_iter,
    }

    existing_idx = None
    for i, d in enumerate(divs):
        if d.get("sink_id") == entry["sink_id"] and d.get("column") == entry["column"]:
            existing_idx = i
            break

    if existing_idx is not None:
        divs[existing_idx] = entry
        print(f"[validate.py] updated divergence: {trial_id}/{args.sink_id}/{entry['column']}")
    else:
        divs.append(entry)
        print(f"[validate.py] documented divergence: {trial_id}/{args.sink_id}/{entry['column']}")

    _save_state(conv_root, state)

    expected_divergences = manifest.setdefault("expected_divergences", {})
    divergence_entry = {
        "column": entry["column"],
        "reason": entry["reason"],
        "baseline_sample": entry["baseline_sample"],
        "shadow_sample": entry["shadow_sample"],
        "scope": "data",
    }
    sink_keys = {f"{trial_id}.{args.sink_id}"}
    normalized_sink = _normalize_sink_name(args.sink_id)
    if normalized_sink:
        sink_keys.add(f"{trial_id}.{normalized_sink}")
    for key in sink_keys:
        sink_divs = expected_divergences.setdefault(key, [])
        replaced = False
        for i, existing in enumerate(sink_divs):
            if existing.get("column", "").upper() == entry["column"]:
                sink_divs[i] = divergence_entry
                replaced = True
                break
        if not replaced:
            sink_divs.append(divergence_entry)
    _save_manifest(conv_root, manifest)


def cmd_migrate_divergences(args: argparse.Namespace) -> None:
    """Migrate documented divergences from write_NNN keys to table-name keys.
    
    Uses the target field from Phase A capture metadata to derive table names
    via the slug rule. Fails loudly for ambiguous entries.
    """
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)
    workspace = _validation_root(conv_root)
    
    migrated = 0
    ambiguous = 0
    
    for trial_id, trial in state.get("trials", {}).items():
        divs = trial.get("documented_divergences", [])
        if not divs:
            continue
        
        # Load Phase A captures metadata if available
        phase_a_dir = workspace / "results" / "phase_a" / trial_id
        captures_meta = {}
        
        # Try to build write_NNN -> table_name mapping from existing artifacts
        if phase_a_dir.is_dir():
            for entry in sorted(phase_a_dir.iterdir()):
                if entry.name.startswith("write_") and entry.is_dir():
                    # Check if there's a target recorded in the capture
                    # The slug is embedded in the dir name after write_NNN_
                    parts = entry.name.split("_", 2)
                    if len(parts) >= 3:
                        captures_meta[entry.name] = parts[2]  # The slug portion
        
        new_divs = []
        for div in divs:
            sink_id = div.get("sink_id", "")
            if not sink_id.startswith("write_"):
                # Already migrated or not in old format
                new_divs.append(div)
                migrated += 1
                continue
            
            # Try to map write_NNN to a table name
            slug = captures_meta.get(sink_id, "")
            if not slug:
                print(
                    f"MIGRATION_AMBIGUOUS: {sink_id} (trial={trial_id}) "
                    f"cannot be mapped to a table name.\n"
                    f"Action: re-baseline this trial "
                    f"(delete phase_a/{trial_id}/ and re-run Phase A)."
                )
                ambiguous += 1
                new_divs.append(div)  # Keep as-is, operator must re-baseline
                continue
            
            # Migrate the entry
            new_div = dict(div)
            new_div["sink_id"] = slug
            new_div["_migrated_from"] = sink_id
            new_divs.append(new_div)
            migrated += 1
        
        trial["documented_divergences"] = new_divs
    
    _save_state(conv_root, state)
    
    print(f"[validate.py] divergence migration: {migrated} migrated, {ambiguous} ambiguous")
    if ambiguous > 0:
        print(f"[validate.py] WARNING: {ambiguous} entries could not be mapped. "
              "Re-baseline those trials to resolve.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# record-fixer-dispatch
# ---------------------------------------------------------------------------


def cmd_record_fixer_dispatch(args: argparse.Namespace) -> None:
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)
    # Fixer dispatch happens during Phase B; default to its current iter.
    iter_n = args.iter if args.iter is not None else _phase_iter(state, "b")
    dispatches = state.setdefault("fixer_dispatches", [])
    entry = {
        "iter": iter_n,
        "error_class": args.error_class,
        "error_hash": args.error_hash,
        "trials_affected": [t.strip() for t in args.trial_ids.split(",") if t.strip()],
        "outcome": args.outcome,
    }
    dispatches.append(entry)
    _save_state(conv_root, state)
    _append_event(conv_root, {
        "kind": "fixer_dispatched",
        "iter": iter_n,
        "error_class": args.error_class,
        "trial_id": entry["trials_affected"][0] if len(entry["trials_affected"]) == 1 else None,
        "trial_ids": entry["trials_affected"],
        "trials_affected": entry["trials_affected"],
        "outcome": args.outcome,
    })
    print(f"[validate.py] recorded fixer dispatch: iter={iter_n} "
          f"class={args.error_class} trials={len(entry['trials_affected'])}")


# ---------------------------------------------------------------------------
# mark-unselected-dependency
# ---------------------------------------------------------------------------


def cmd_mark_unselected_dependency(args: argparse.Namespace) -> None:
    """Mark a trial as passed_no_baseline due to an unselected upstream dependency."""
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)

    trial_id = args.trial_id
    if trial_id not in state.get("trials", {}):
        _die(2, f"trial '{trial_id}' not found in state.json")

    # Record a fixer dispatch with error_class=unselected_dependency, outcome=no_change
    dispatches = state.setdefault("fixer_dispatches", [])
    dispatches.append({
        "iter": args.iter or 0,
        "error_class": "unselected_dependency",
        "error_hash": f"needs:{args.reason[:70]}" if args.reason else "unselected_dep",
        "trials_affected": [trial_id],
        "outcome": "no_change",
    })

    # Set trial status to passed_no_baseline
    state["trials"][trial_id]["status"] = "passed_no_baseline"
    state["trials"][trial_id]["dependency_note"] = args.reason
    state["trials"][trial_id].pop("hard_stuck_reason", None)

    _advance_phase(state, conv_root)
    _save_state(conv_root, state)
    _append_event(conv_root, {
        "kind": "marked_unselected_dependency",
        "trial_id": trial_id,
        "reason": args.reason,
    })
    print(f"[validate.py] trial {trial_id} marked unselected_dependency → passed_no_baseline"
          f" (reason: {args.reason})")


# ---------------------------------------------------------------------------
# record-patch
# ---------------------------------------------------------------------------


def cmd_record_patch(args: argparse.Namespace) -> None:
    """Record a patch applied to a trial's test/harness/output files."""
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)

    trial_id = args.trial_id
    if trial_id not in state.get("trials", {}):
        _die(2, f"trial '{trial_id}' not found in state.json")

    trial = state["trials"][trial_id]
    patches = trial.setdefault("patches", [])
    entry = {
        "file": args.file,
        "phase": args.phase,
        "iter": args.iter if args.iter is not None else _phase_iter(state, args.phase),
        "reason": args.reason,
        "diff_path": args.diff_path or None,
        "ts": _now(),
    }
    patches.append(entry)
    _save_state(conv_root, state)
    _append_event(conv_root, {
        "kind": "patch_applied",
        "trial_id": trial_id,
        "phase": args.phase,
        "iter": entry["iter"],
        "file": args.file,
        "reason": args.reason,
    })
    print(f"[validate.py] patch recorded for {trial_id}: {args.file} ({args.reason})")


# ---------------------------------------------------------------------------
# build-index
# ---------------------------------------------------------------------------


def _migration_fix_commits_by_trial(
    conv_root: Path, branch: Optional[str], primary_path_by_trial: Dict[str, str]
) -> Dict[str, List[Dict[str, Any]]]:
    """Map each trial to the ``[MIGRATION-FIX]`` commits that fixed it.

    Attribution is authoritative when the commit carries a ``SCOS-Trials:`` git
    trailer (written by ``validate.py commit --trial-ids ...``) — this is correct
    even when an entrypoint spans multiple files, since the runner declares which
    trial(s) the fix is for. When a commit has no trailer (older runs), it falls
    back to matching the trial's PRIMARY file (``Output/<path>``) against the
    commit's changed files. Each commit is ``{sha, subject, body?}`` (the
    ``[MIGRATION-FIX]`` prefix stripped, the trailer line removed from the body),
    returned oldest-first. Returns empty lists if the branch is missing.
    """
    by_trial: Dict[str, List[Dict[str, Any]]] = {t: [] for t in primary_path_by_trial}
    if not branch:
        return by_trial
    known = set(primary_path_by_trial)
    trial_by_primary = {path: t for t, path in primary_path_by_trial.items()}
    prefix = COMMIT_PREFIXES.get("migration-fix", "[MIGRATION-FIX]")
    res = _git(
        conv_root, "-c", "core.quotepath=false", "log", branch,
        "--grep", r"^\[MIGRATION-FIX\]",
        "--format=%x1e%h%x1f%s%x1f%b%x1f", "--name-only",
    )
    if res.returncode != 0:
        return by_trial
    for record in res.stdout.split("\x1e"):
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) < 4:
            continue
        sha, subject, body = parts[0].strip(), parts[1].strip(), parts[2]
        if subject.startswith(prefix):
            subject = subject[len(prefix):].strip()
        files = [ln.strip() for ln in parts[3].splitlines() if ln.strip()]

        # Authoritative: SCOS-Trials trailer declares the target trial(s).
        declared: List[str] = []
        body_lines = []
        for ln in body.splitlines():
            m = re.match(r"\s*SCOS-Trials:\s*(.+)", ln)
            if m:
                declared = [t.strip() for t in m.group(1).split(",") if t.strip()]
            else:
                body_lines.append(ln)
        clean_body = "\n".join(body_lines).strip()

        if declared:
            targets = [t for t in declared if t in known]
        else:  # fallback: match the trial's primary Output/ file
            targets = sorted({trial_by_primary[f] for f in files if f in trial_by_primary})

        commit: Dict[str, Any] = {"sha": sha, "subject": subject}
        if clean_body:
            commit["body"] = clean_body
        for t in targets:
            by_trial[t].append(commit)
    for commits in by_trial.values():
        commits.reverse()  # git log is newest-first; report oldest-first
    return by_trial


def cmd_build_index(args: argparse.Namespace) -> None:
    """Emit canonical run_index.json manifest from scattered state."""
    conv_root = Path(args.conv_root).resolve()
    state = _load_state(conv_root)
    workspace = _validation_root(conv_root)

    manifest = _load_manifest(conv_root)
    entrypoints = _load_entrypoints(conv_root, manifest)
    trials = state.get("trials", {})
    golden_schemas = state.get("snowflake", {}).get("golden_schemas", {})

    # --- run block ---
    phase = state.get("phase", "init")
    all_terminal = all(
        t.get("status") in _TERMINAL_TRIAL_STATUSES for t in trials.values()
    ) if trials else False
    if phase == "phase_b_done" or all_terminal:
        run_status = "passed" if all(
            t.get("status") in ("passed", "passed_no_baseline") for t in trials.values()
        ) else "partial" if any(
            t.get("status") == "hard_stuck" for t in trials.values()
        ) else "passed"
    else:
        run_status = "in_progress"

    run_block = {
        "id": state.get("run_id"),
        "started_at": state.get("created_at"),
        "completed_at": _now() if all_terminal else None,
        "status": run_status,
        "skill_version": state.get("skill_version"),
        "connection": state.get("config", {}).get("connection_name",
                      state.get("snowflake", {}).get("connection", "")),
        "database": state.get("snowflake", {}).get("database", ""),
        "schema_namespace": state.get("snowflake", {}).get("schema", ""),
    }

    # --- milestones ---
    milestones_raw = state.get("milestones", {})
    milestones = {}
    for name in CANONICAL_MILESTONES:
        done = milestones_raw.get(name, False)
        milestones[name] = {
            "status": "done" if done else "pending",
            "completed_at": None,
        }

    # --- entrypoints ---
    ep_analysis = {ep.get("id", ep.get("path", "")): ep for ep in entrypoints}
    # [MIGRATION-FIX] commits that fixed each trial (the changes that were kept).
    primary_path_by_trial = {
        tid: f"Output/{ep_analysis.get(tid, {}).get('path', '')}"
        for tid in trials if ep_analysis.get(tid, {}).get("path")
    }
    mfix_by_trial = _migration_fix_commits_by_trial(
        conv_root, state.get("git", {}).get("validation_branch"), primary_path_by_trial
    )
    parse_errors: list = []
    entrypoints_list = []

    def _captured_outputs(phase_index: dict[str, Any]) -> list[dict[str, Any]]:
        outputs = []
        for cap in phase_index.get("tables", []):
            outputs.append(
                {
                    "name": cap.get("name", ""),
                    "path": cap.get("path", ""),
                    "rows": cap.get("row_count"),
                    "schema": cap.get("schema_json"),
                    "format": cap.get("format") or "parquet",
                }
            )
        return outputs

    for trial_id, trial in sorted(trials.items()):
        ep_info = ep_analysis.get(trial_id, {})
        source_path = ep_info.get("path", "")

        # Phase A block
        phase_a_dir = workspace / "results" / "phase_a" / trial_id
        if phase_a_dir.is_dir():
            phase_a_index, _err = _load_json_tolerant(phase_a_dir / "_index.json")
            if _err:
                _rel = f"results/phase_a/{trial_id}/_index.json"
                print(f"[validate.py] warn: failed to parse {_rel}: {_err}; continuing with partial data", file=sys.stderr)
                parse_errors.append({"path": _rel, "error": _err, "trial_id": trial_id, "phase": "phase_a"})
        else:
            phase_a_index = {}
        phase_a_captures = _captured_outputs(phase_a_index)

        phase_a_block = {
            "verdict": ("phase_a_skipped" if trial.get("status") == "phase_a_skipped"
                        else "no_baseline" if _trial_lacks_baseline(trial)
                        else "baseline_produced" if _phase_a_baseline_produced(trial)
                        else "no_baseline"),
            "iters": len(trial.get("phase_a_iters", [])),
            "captured_outputs": phase_a_captures,
            "patches_applied": [p for p in trial.get("patches", []) if p.get("phase") == "phase_a"],
            "errors": phase_a_index.get("failures", []),
        }

        # Phase B block
        phase_b_dir = workspace / "results" / "phase_b" / trial_id
        if phase_b_dir.is_dir():
            phase_b_index, _err = _load_json_tolerant(phase_b_dir / "_index.json")
            if _err:
                _rel = f"results/phase_b/{trial_id}/_index.json"
                print(f"[validate.py] warn: failed to parse {_rel}: {_err}; continuing with partial data", file=sys.stderr)
                parse_errors.append({"path": _rel, "error": _err, "trial_id": trial_id, "phase": "phase_b"})
        else:
            phase_b_index = {}
        phase_b_captures = _captured_outputs(phase_b_index)

        scos_query_ids = []
        for b_iter in trial.get("phase_b_iters", []):
            qid = b_iter.get("scos_query_id") or b_iter.get("query_id", "")
            if qid:
                scos_query_ids.append(str(qid))

        phase_b_block = {
            "verdict": trial.get("status", "pending"),
            "iters": len(trial.get("phase_b_iters", [])),
            "captured_outputs": phase_b_captures,
            "patches_applied": [p for p in trial.get("patches", []) if p.get("phase") == "phase_b"],
            "errors": phase_b_index.get("failures", []),
            "scos_query_ids": scos_query_ids,
            "migration_fix_commits": mfix_by_trial.get(trial_id, []),
        }

        # Comparison block
        diffs_dir = phase_b_dir / "diffs" if phase_b_dir.is_dir() else None
        diff_entries = []
        if diffs_dir and diffs_dir.is_dir():
            for diff_file in sorted(diffs_dir.glob("*.json")):
                diff_data, _err = _load_json_tolerant(diff_file)
                if _err:
                    _rel = f"results/phase_b/{trial_id}/diffs/{diff_file.name}"
                    print(f"[validate.py] warn: failed to parse {_rel}: {_err}; continuing with partial data", file=sys.stderr)
                    parse_errors.append({"path": _rel, "error": _err, "trial_id": trial_id, "phase": "phase_b"})
                    continue
                _shape = diff_data.get("shape") or {}
                diff_entries.append({
                    "table": diff_file.stem,
                    "diff_path": f"results/phase_b/{trial_id}/diffs/{diff_file.name}",
                    "schema_match": diff_data.get("schema_diff") is None,
                    "row_count_a": (_shape.get("baseline") or {}).get("rows"),
                    "row_count_b": (_shape.get("shadow") or {}).get("rows"),
                    "tier": diff_data.get("tier", "cell"),
                    "verdict": diff_data.get("result", "unknown"),
                })

        has_baseline = _phase_a_baseline_produced(trial) and not _trial_lacks_baseline(trial)
        comparison_verdict = "no_baseline"
        if has_baseline:
            if trial.get("status") == "passed":
                comparison_verdict = "match"
            elif trial.get("documented_divergences"):
                comparison_verdict = "cosmetic_divergence"
            elif trial.get("status") == "hard_stuck":
                comparison_verdict = "real_divergence"

        comparison_block = {
            "verdict": comparison_verdict,
            "diffs": diff_entries,
            "documented_divergences": trial.get("documented_divergences", []),
        }

        ep_entry = {
            "id": trial_id,
            "source_path": source_path,
            "phase_a": phase_a_block,
            "phase_b": phase_b_block,
            "comparison": comparison_block,
            "trial_dir": f"results/phase_b/{trial_id}/",
            "verdict": {
                "overall": trial.get("status", "pending"),
                "reason": _verdict_reason(trial),
            },
        }
        entrypoints_list.append(ep_entry)

    # --- artifacts_index ---
    mock_data_entries = []
    mock_data_root = workspace / "shared" / "mock_data"
    if mock_data_root.is_dir():
        for trial_dir in sorted(mock_data_root.iterdir()):
            if trial_dir.is_dir():
                files = [str(f.relative_to(workspace)) for f in sorted(trial_dir.rglob("*")) if f.is_file()]
                mock_data_entries.append({"trial_id": trial_dir.name, "files": files})

    rendered_tests = []
    tests_dir = workspace / "tests"
    if tests_dir.is_dir():
        rendered_tests = [str(f.relative_to(workspace)) for f in sorted(tests_dir.glob("test_*.py"))]

    artifacts_index = {
        "analysis": "shared/schemas/manifest.json",
        "patch_blueprint": "shared/patch_blueprint.json" if (workspace / "shared" / "patch_blueprint.json").is_file() else None,
        "mock_data": mock_data_entries,
        "rendered_tests": rendered_tests,
    }

    # --- events (last 200 lines ref) ---
    events_path = workspace / "events.jsonl"
    events_ref = "events.jsonl" if events_path.is_file() else None

    # --- assemble ---
    run_index = {
        "run": run_block,
        "milestones": milestones,
        "entrypoints": entrypoints_list,
        "artifacts_index": artifacts_index,
        "events": events_ref,
        "fixer_dispatches": state.get("fixer_dispatches", []),
        "documented_divergences": [
            div for t in trials.values()
            for div in t.get("documented_divergences", [])
        ],
        "warnings": state.get("synth_warnings", []),
        "parse_errors": parse_errors,
    }

    out_path = workspace / "run_index.json"
    _write_atomic(out_path, run_index)
    print(f"[validate.py] run_index.json written to {out_path}")


# ---------------------------------------------------------------------------
# cleanup-artifacts (internal, called from cmd_summary)
# ---------------------------------------------------------------------------


def _cleanup_artifacts(workspace: Path) -> None:
    """Remove stale artifacts from the Validation workspace."""
    removed = 0

    # Remove __pycache__ directories
    for cache_dir in list(workspace.rglob("__pycache__")):
        if cache_dir.is_dir():
            try:
                shutil.rmtree(cache_dir)
                removed += 1
            except OSError:
                pass

    # Remove .pytest_cache
    pytest_cache = workspace / ".pytest_cache"
    if pytest_cache.is_dir():
        try:
            shutil.rmtree(pytest_cache)
            removed += 1
        except OSError:
            pass

    if removed:
        print(f"[validate.py] cleanup: removed {removed} stale artifact(s)")


# ---------------------------------------------------------------------------
# Runtime detection
# ---------------------------------------------------------------------------


def _import_runtimes():
    """Make the harness ``runtimes`` package importable and return it."""
    harness = str(SCRIPTS_DIR / "harness")
    if harness not in sys.path:
        sys.path.insert(0, harness)
    import runtimes  # type: ignore[import-not-found]

    return runtimes


def cmd_runtime_detect(args: argparse.Namespace) -> None:
    """Validate Databricks workspace credentials and Unity Catalog support.

    Checks whether DATABRICKS_HOST/TOKEN/CLUSTER_ID resolve and whether the
    cluster has Unity Catalog enabled. With --persist, saves the credential
    file path to state.json so the test harness can use databricks-connect
    automatically at test time.
    """
    conv_root = Path(args.conv_root).resolve()
    # --env-file is a convenience for the SCOS_DATABRICKS_ENV_FILE env var.
    if getattr(args, "env_file", None):
        os.environ["SCOS_DATABRICKS_ENV_FILE"] = args.env_file
    rt = _import_runtimes()
    env = rt.detect_databricks_env()

    has_uc = False
    if env:
        try:
            from databricks.sdk import WorkspaceClient
            client = WorkspaceClient(host=env["host"], token=env["token"])
            _SYSTEM_CATALOGS = {"hive_metastore", "samples", "system", "__databricks_internal"}
            catalog_names = [c.name for c in client.catalogs.list() if c.name]
            has_uc = any(c not in _SYSTEM_CATALOGS for c in catalog_names)
        except Exception:
            pass

    print(json.dumps({
        "databricks_env_present": bool(env),
        "has_unity_catalog": has_uc,
    }, indent=2))

    if getattr(args, "persist", False):
        env_file = os.environ.get("SCOS_DATABRICKS_ENV_FILE", "")
        state_path = conv_root / "Validation" / "state.json"
        if state_path.is_file() and env_file:
            state = json.loads(state_path.read_text())
            state.setdefault("databricks", {})["env_file"] = env_file
            state_path.write_text(json.dumps(state, indent=2) + "\n")
            print(f"[validate.py] saved databricks env_file to state.json")
        elif env_file:
            print(f"[validate.py] WARNING: state.json not found at {state_path}; env_file not saved")
        else:
            print(f"[validate.py] --persist: SCOS_DATABRICKS_ENV_FILE not set; nothing to save")


# ---------------------------------------------------------------------------
# known-patches subcommand
# ---------------------------------------------------------------------------


def cmd_known_patches_suggest(args: argparse.Namespace) -> None:
    """Scan each entrypoint's source with KNOWN_PATCHES + investigation detectors.

    Writes two artifacts:
      - ``known_patch_suggestions.json`` — confident auto-appliable patches.
      - ``patch_investigation.json`` — a worklist of non-Spark-I/O sites the
        patch-author must look into (no auto-fix), grouped by category.
    """
    conv_root = Path(args.conv_root).resolve()
    manifest = _load_manifest(conv_root)
    eps = _load_entrypoints(conv_root, manifest)
    source_dir = _validation_root(conv_root) / "source"
    output_dir = conv_root / "Output"

    all_suggestions: List[Dict[str, Any]] = []
    all_sites: List[Dict[str, Any]] = []
    # Scan each entrypoint's whole import/%run closure, not just its top file —
    # I/O frequently lives in imported reader/writer helpers. schema_mine records
    # the closure (entrypoint + transitive static imports + %run targets) on each
    # ep; fall back to the single path for schemas mined before closure existed.
    # Dedup across entrypoints so a shared helper is scanned (and suggested) once.
    seen_files: set = set()
    for ep in eps:
        rel = ep.get("path", "")
        files = ep.get("closure") or ([rel] if rel else [])
        for rel_f in files:
            if not rel_f or rel_f in seen_files:
                continue
            seen_files.add(rel_f)
            src_path = source_dir / rel_f
            if src_path.is_file():
                try:
                    text = src_path.read_text(encoding="utf-8")
                except OSError:
                    text = None
                if text is not None:
                    all_suggestions.extend(patch_engine.suggest_known_patches(text, rel_f))
                    all_sites.extend(patch_engine.scan_investigation_sites(text, rel_f))
            # The migrate skill's spark_io_detect annotations live in the migrated copy.
            out_path = output_dir / rel_f
            if out_path.is_file():
                try:
                    out_text = out_path.read_text(encoding="utf-8")
                except OSError:
                    out_text = None
                if out_text is not None:
                    all_sites.extend(patch_engine.scan_scos_annotations(out_text, rel_f))

    validation_root = _validation_root(conv_root)
    validation_root.mkdir(parents=True, exist_ok=True)

    out_path = validation_root / "known_patch_suggestions.json"
    out_path.write_text(
        json.dumps({"patches": all_suggestions}, indent=2) + "\n",
        encoding="utf-8",
    )

    by_category: Dict[str, int] = {}
    for site in all_sites:
        by_category[site["category"]] = by_category.get(site["category"], 0) + 1
    invest_path = validation_root / "patch_investigation.json"
    invest_path.write_text(
        json.dumps({"summary": by_category, "sites": all_sites}, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"known-patches suggest: {len(all_suggestions)} auto-patch suggestions, "
        f"{len(all_sites)} investigation site(s) across {len(eps)} entrypoints"
    )


def cmd_known_patches(args: argparse.Namespace) -> None:
    """Dispatch 'known-patches <subcommand>'."""
    sub = getattr(args, "kp_command", None)
    if sub == "suggest":
        cmd_known_patches_suggest(args)
    else:
        _die(2, "known-patches requires a subcommand: suggest")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate.py",
        description="SCOS validation workspace manager",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- init ---
    p_init = sub.add_parser("init", help="Initialize validation workspace")
    p_init.add_argument("--conv-root", required=True, help="Conversion root directory")
    p_init.add_argument("--connection", required=True, help="Snowflake connection name")
    p_init.add_argument("--original-source", help="Path to original PySpark source")
    p_init.add_argument("--migrated-source", help="Path to migrated SCOS source (default: <conv-root>/Output/)")
    p_init.add_argument("--project-slug", help="Project slug (default: derived from conv-root name)")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing state.json even if initialized")

    # --- install-kit ---
    p_kit = sub.add_parser("install-kit",
                           help="Copy the harness kit into Validation/tests (cross-platform)")
    p_kit.add_argument("--conv-root", required=True)

    # --- scope-entrypoints ---
    p_scope = sub.add_parser(
        "scope-entrypoints",
        help="Prune mined schemas/ to a subset of entrypoints (pre-sectioning; "
             "no state.json / no cap)")
    p_scope.add_argument("--conv-root", required=True)
    p_scope.add_argument("--ids", required=True,
                         help="Comma-separated entrypoint IDs to KEEP")

    # --- seed-venv ---
    p_venv = sub.add_parser("seed-venv", help="Seed a phase-scoped venv (source or scos)")
    p_venv.add_argument("--conv-root", required=True)
    p_venv.add_argument("--phase", required=True, choices=["a", "b"],
                        help="Phase: a=source (Validation/shared/.venv-source), b=scos (Validation/shared/.venv-scos)")
    p_venv.add_argument("--requirements", help="Override auto-discovered requirements file path")

    # --- status ---
    p_status = sub.add_parser("status", help="Show validation status")
    p_status.add_argument("--conv-root", required=True)
    p_status.add_argument("--verbose", action="store_true")
    p_status.add_argument("--phase", choices=["A", "B", "all"], default="all")
    p_status.add_argument("--json", action="store_true", help="Output JSON format")

    # --- summary ---
    p_summary = sub.add_parser("summary", help="Final validation summary")
    p_summary.add_argument("--conv-root", required=True)

    # --- record-iter ---
    p_rec = sub.add_parser("record-iter", help="Record iteration results (per-trial)")
    p_rec.add_argument("--conv-root", required=True)
    p_rec.add_argument("--trial-id", required=True,
                       help="Entrypoint id; appended into state.trials[<id>].phase_<x>_iters")
    p_rec.add_argument("--phase", choices=["A", "B", "phase_a", "phase_b"], required=True,
                       help="Phase: accept short (A/B) or long (phase_a/phase_b) form")
    p_rec.add_argument("--iter", type=int, default=None,
                       help="Optional; defaults to the phase's current iter (set by run-tests)")
    p_rec.add_argument("--passing", type=int, required=True)
    p_rec.add_argument("--failing", type=int, required=True)
    p_rec.add_argument("--issues", type=int, help="Issue count (Phase B)")
    p_rec.add_argument("--patches-extended", type=int, help="Extended patches count (Phase A)")
    p_rec.add_argument("--fix-commit", help="Phase B fixer commit SHA, if any")
    p_rec.add_argument("--fix-category",
                       choices=["harness_failure", "patch_failure",
                                 "workload_failure", "assertion_failure",
                                 "unselected_dependency",
                                 "schema_gap", "analysis_repair"],
                        help="Categorize the fix applied in this iteration")

    # --- record-trial-status ---
    p_ts = sub.add_parser("record-trial-status", help="Set a trial's terminal status")
    p_ts.add_argument("--conv-root", required=True)
    p_ts.add_argument("--trial-id", required=True)
    p_ts.add_argument("--status", required=True,
                      choices=list(_TRIAL_STATUSES))
    p_ts.add_argument("--final-iter", type=int,
                      help="Optional; defaults to the trial's latest recorded iter")
    p_ts.add_argument("--reason", help="REQUIRED for hard_stuck and phase_a_skipped; "
                      "surfaced in the final report")
    p_ts.add_argument(
        "--analysis-repair-exhausted",
        action="store_true",
        help="Schema/data repair path exhausted; requires recorded "
             "schema_gap/analysis_repair work and allows hard_stuck without fixer",
    )
    p_ts.add_argument(
        "--harness-repair-exhausted",
        action="store_true",
        help="Shared-kit harness repair exhausted; requires recorded "
             "harness_failure work and allows hard_stuck without fixer dispatch",
    )
    p_ts.add_argument(
        "--patch-repair-exhausted",
        action="store_true",
        help="Blueprint patch repair exhausted; requires recorded "
             "patch_failure work and allows hard_stuck without fixer dispatch",
    )
    p_ts.add_argument(
        "--phase",
        choices=["A", "B", "phase_a", "phase_b"],
        help="Optional — accepted for parity with record-iter (ignored; status "
             "alone determines the trial phase)",
    )

    # --- commit ---
    p_commit = sub.add_parser("commit", help="Stage and commit Output/")
    p_commit.add_argument("--conv-root", required=True)
    p_commit.add_argument("--iter", type=int, help="Iteration number (informational)")
    p_commit.add_argument("--message", required=True, help="Commit message (the prefix is added automatically)")
    p_commit.add_argument("--kind", required=True, choices=sorted(COMMIT_PREFIXES),
                          help="test-patch (not cherry-picked) | migration-fix (cherry-picked at harvest)")
    p_commit.add_argument("--trial-ids", default="",
                          help="Comma-separated trial id(s) this fix is for; recorded as a "
                               "SCOS-Trials git trailer so run_index attributes the commit to "
                               "the right entrypoint(s). Strongly recommended for --kind migration-fix.")
    p_commit.add_argument("--print-sha-only", action="store_true")
    p_commit.add_argument(
        "--files", default="",
        help="Comma-separated list of file paths to stage instead of the entire Output/ tree. "
             "Paths may be relative to conv-root or relative to Output/; paths not already "
             "under Output/ are automatically prefixed. Must resolve under Output/.",
    )

    # --- patch-add ---
    p_padd = sub.add_parser("patch-add",
                            help="Smoke-test + apply + record a batch of blueprint patches (commits Output side as [TEST-PATCH])")
    p_padd.add_argument("--conv-root", required=True)
    p_padd.add_argument("--from-file", required=True,
                        help="Path to a JSON file holding a batch of patch entries: "
                             "{\"patches\": [...]}, a bare list, or a single entry "
                             "{id, replace_all?, note?, source?, migrated?}")
    p_padd.add_argument("--no-commit", action="store_true",
                        help="Apply + record but do not git-commit the Output side yet")
    p_padd.add_argument("--force", action="store_true",
                        help="Downgrade the SCOS env-ref audit failure to a warning and apply patches anyway")

    # --- harvest ---
    p_harvest = sub.add_parser("harvest",
                               help="Copy Validation/ to original branch (requires summary), cherry-pick [MIGRATION-FIX]")
    p_harvest.add_argument("--conv-root", required=True)
    p_harvest.add_argument("--continue", dest="continue_", action="store_true",
                           help="Resume an in-progress cherry-pick after resolving conflicts")
    p_harvest.add_argument("--abort", action="store_true",
                           help="Abort an in-progress cherry-pick and return to the original branch")

    # --- consolidate ---
    p_cons = sub.add_parser("consolidate",
                            help="Cherry-pick [MIGRATION-FIX] commits across multiple validation branches onto the current branch")
    p_cons.add_argument("--conv-root", required=True)
    p_cons.add_argument("--base-sha", required=True)
    p_cons.add_argument("--branches", required=False, default="",
                        help="comma-separated validation branch names (omit to auto-discover all local validation/* branches)")
    p_cons.add_argument("--continue", dest="continue_", action="store_true",
                        help="Resume an in-progress cherry-pick after resolving conflicts")
    p_cons.add_argument("--abort", action="store_true",
                        help="Abort an in-progress cherry-pick")

    # --- prepare-batches ---
    p_pb = sub.add_parser("prepare-batches",
                          help="Compute batches from sections.json and set up per-batch git worktrees (init + scope worktree schemas)")
    p_pb.add_argument("--conv-root", required=True,
                      help="Primary migration repo (contains Output/)")
    p_pb.add_argument("--sections", required=True,
                      help="Path to sections.json (semantic groups of entrypoint ids)")
    p_pb.add_argument("--base-sha", required=True,
                      help="Deliverable HEAD SHA to base each worktree on")
    p_pb.add_argument("--worktrees-dir", default=None,
                      help="Directory under which per-batch worktrees are created "
                           "(default: <conv-root>/Validation/worktrees)")
    p_pb.add_argument("--schemas", required=True,
                      help="Path to the already-mined source schemas dir (manifest.json + entrypoints/)")
    p_pb.add_argument("--max-entrypoints", type=int, default=10,
                      help="Max entrypoints per batch (default: 10)")
    p_pb.add_argument("--max-weight", type=int, default=80,
                      help="Max total weight per batch (default: 80)")
    p_pb.add_argument("--connection", required=True,
                      help="Snowflake connection name (forwarded to init)")
    p_pb.add_argument("--original-source", required=True,
                      help="Path to original PySpark source (forwarded to init)")
    p_pb.add_argument("--databricks-env-file", required=False, default=None,
                      help="Path to a Databricks .env file (validated by the orchestrator); "
                           "persisted into each worktree's state.json so the harness uses "
                           "databricks-connect for databricks-native entrypoints")

    # --- record-milestone ---
    p_mile = sub.add_parser("record-milestone", help="Record a milestone in state.json")
    p_mile.add_argument("--conv-root", required=True)
    p_mile.add_argument("--milestone", required=True, help="Milestone name")

    # --- document-divergence ---
    p_div = sub.add_parser("document-divergence",
                           help="Record a documented column divergence for a trial")
    p_div.add_argument("--conv-root", required=True)
    p_div.add_argument("--trial-id", required=True)
    p_div.add_argument("--sink-id", required=True)
    p_div.add_argument("--column", required=True, help="Column name (will be uppercased)")
    p_div.add_argument("--reason", required=True, help="Why this divergence is acceptable")
    p_div.add_argument("--baseline-sample", default="", help="Example baseline value")
    p_div.add_argument("--shadow-sample", default="", help="Example shadow value")
    p_div.add_argument("--iter", type=int, default=None,
                       help="Iteration this divergence was observed at; "
                            "defaults to the trial's current (latest) iter")

    # --- migrate-divergences ---
    p_mig = sub.add_parser("migrate-divergences",
                           help="Migrate documented divergences from write_NNN to table-name keys")
    p_mig.add_argument("--conv-root", required=True)

    # --- record-fixer-dispatch ---
    p_fd = sub.add_parser("record-fixer-dispatch",
                          help="Record a Phase B fixer dispatch in state.json")
    p_fd.add_argument("--conv-root", required=True)
    p_fd.add_argument("--iter", type=int, default=None,
                      help="Optional; defaults to the current Phase B iter (set by run-tests)")
    p_fd.add_argument("--error-class", required=True,
                       choices=["harness_failure", "patch_failure", "workload_failure",
                                "assertion_failure", "unselected_dependency"])
    p_fd.add_argument("--error-hash", required=True,
                      help="First 80 chars of exception msg (stripped of query IDs/timestamps)")
    p_fd.add_argument("--trial-ids", required=True,
                      help="Comma-separated trial IDs affected by this error class")
    p_fd.add_argument("--outcome", required=True,
                      choices=["success", "no_change", "partial"],
                      help="Fixer outcome for this dispatch")

    # --- mark-unselected-dependency ---
    p_ud = sub.add_parser("mark-unselected-dependency",
                          help="Mark trial as passed_no_baseline due to unselected upstream")
    p_ud.add_argument("--conv-root", required=True)
    p_ud.add_argument("--trial-id", required=True, help="Trial ID to mark")
    p_ud.add_argument("--reason", required=True,
                      help="Which unselected entrypoint produces the missing data")
    p_ud.add_argument("--iter", type=int, default=0, help="Current iteration number")

    # --- record-patch ---
    p_patch = sub.add_parser("record-patch",
                             help="Record a patch applied to a trial")
    p_patch.add_argument("--conv-root", required=True)
    p_patch.add_argument("--trial-id", required=True, help="Trial ID the patch applies to")
    p_patch.add_argument("--phase", required=True, choices=["phase_a", "phase_b"],
                         help="Which phase the patch was applied in")
    p_patch.add_argument("--file", required=True,
                         help="Path to patched file (relative to conv-root)")
    p_patch.add_argument("--reason", required=True, help="Short reason for the patch")
    p_patch.add_argument("--iter", type=int, default=None, help="Iteration number")
    p_patch.add_argument("--diff-path", default=None, help="Path to diff file if saved")

    # --- build-index ---
    p_bi = sub.add_parser("build-index",
                          help="Emit canonical run_index.json manifest")
    p_bi.add_argument("--conv-root", required=True)

    # --- run-tests ---
    p_rt = sub.add_parser(
        "run-tests",
        help="Run pytest with auto-deselect of terminal trials and auto-record-iter",
    )
    p_rt.add_argument("--conv-root", required=True, help="Conversion root directory")
    p_rt.add_argument("--phase", required=True, choices=["a", "b"],
                      help="Phase: a=source, b=scos")
    p_rt.add_argument("--iter", type=int, default=None,
                      help="Optional override; run-tests auto-advances the counter when omitted")
    p_rt.add_argument("--trial-id",
                      help="Optional single trial to run; deselects all other trials "
                           "and bypasses status-based deselection for that trial")
    p_rt.add_argument("--verify-all", action="store_true",
                      help="Skip deselect — run every trial (end-of-phase regression check)")

    # --- known-patches ---
    p_kp = sub.add_parser("known-patches", help="Known-patches library operations")
    kp_sub = p_kp.add_subparsers(dest="kp_command")
    p_kp.set_defaults(kp_command=None)
    p_kp_suggest = kp_sub.add_parser(
        "suggest",
        help="Scan source with KNOWN_PATCHES + investigation detectors; write known_patch_suggestions.json and patch_investigation.json",
    )
    p_kp_suggest.add_argument("--conv-root", required=True, help="Conversion root directory")

    # --- runtime-detect ---
    p_rd = sub.add_parser("runtime-detect",
                          help="Report Phase A runtime decision (JSON)")
    p_rd.add_argument("--conv-root", required=True)
    p_rd.add_argument("--env-file",
                      help="Path to the Databricks .env (DATABRICKS_HOST/TOKEN/CLUSTER_ID). "
                           "Equivalent to setting SCOS_DATABRICKS_ENV_FILE; the env var is used as a fallback.")
    p_rd.add_argument("--persist", action="store_true",
                      help="Save the Databricks env_file path to state.json for automatic cred resolution at test time")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "init": cmd_init,
        "install-kit": cmd_install_kit,
        "scope-entrypoints": cmd_scope_entrypoints,
        "seed-venv": cmd_seed_venv,
        "status": cmd_status,
        "summary": cmd_summary,
        "record-iter": cmd_record_iter,
        "run-tests": cmd_run_tests,
        "record-trial-status": cmd_record_trial_status,
        "record-patch": cmd_record_patch,
        "patch-add": cmd_patch_add,
        "commit": cmd_commit,
        "harvest": cmd_harvest,
        "consolidate": cmd_consolidate,
        "prepare-batches": cmd_prepare_batches,
        "record-milestone": cmd_record_milestone,
        "document-divergence": cmd_document_divergence,
        "migrate-divergences": cmd_migrate_divergences,
        "record-fixer-dispatch": cmd_record_fixer_dispatch,
        "mark-unselected-dependency": cmd_mark_unselected_dependency,
        "build-index": cmd_build_index,
        "runtime-detect": cmd_runtime_detect,
        "known-patches": cmd_known_patches,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        _die(2, f"unknown command: {args.command}")
    handler(args)


if __name__ == "__main__":
    main()
