#!/usr/bin/env python3
"""scos_state.py — Python port of the ScosState state machine + CLI.

This is the Snowflake-FREE spine of the Scala control jar's ScosState, ported to
Python so the Scala validator can move off the 280 MB JVM control jar (parity
with the PySpark validator, whose validate.py owns the same state logic). The
on-disk schemas (`state.json`, `events.jsonl`, `run_index.json`) are byte-for-byte
shared with the Scala/Python validators — field names MUST match.

Scope of this module: the full Snowflake-FREE CLI surface of ScosState —
init, select-entrypoints, status, summary (the exit-4 output gate), build-index
(run_index.json), document-divergence, migrate-divergences, put-schemas, commit
(git), and the record-* / mark-* family — plus the pure state machine
(advance_phase, the record-trial-status hard gate, comparison_verdict,
manual-review / recovery). The only subcommands still owned by the JVM control
jar are the Snowflake-touching ones (provision, cleanup, snapshot-stage,
check-connection), which need a live connection and are not portable here.

Faithful to ScosState.scala — field names match the shared on-disk schema.
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
import platform

try:
    import fcntl as _fcntl  # POSIX only
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

SCHEMA_VERSION = 1
VALIDATION_DIRNAME = "Validation"

CANONICAL_MILESTONES = {
    "synth_survey", "entrypoints_selected", "synth_deep",
    "patches_authored", "workload_built", "tests_authored",
    "venv_prewarmed", "snowflake_provisioned",
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


def schemas_dir(conv_root: Path) -> Path:
    return validation_root(conv_root) / "shared" / "schemas"


def schemas_manifest_path(conv_root: Path) -> Path:
    return schemas_dir(conv_root) / "manifest.json"


def load_schemas_manifest(conv_root: Path) -> dict:
    p = schemas_manifest_path(conv_root)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_EP_WEIGHT_LABELS: dict[str, int] = {
    "critical": 30, "high": 20, "medium": 10, "low": 5,
}


def _coerce_entrypoint_weight(raw) -> int:
    """Normalize manifest weight to int for shared batch.py (label-safe)."""
    if raw is None:
        return 1
    if isinstance(raw, str):
        return _EP_WEIGHT_LABELS.get(raw.lower().strip(), 1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _normalize_manifest_weights(manifest: dict) -> dict:
    """Return manifest copy with numeric entrypoint weights (batch.py expects int)."""
    out = dict(manifest)
    normalized: list = []
    for ep in manifest.get("entrypoints") or []:
        if not isinstance(ep, dict):
            normalized.append(ep)
            continue
        ep_copy = dict(ep)
        if "weight" in ep_copy:
            ep_copy["weight"] = _coerce_entrypoint_weight(ep_copy.get("weight"))
        normalized.append(ep_copy)
    out["entrypoints"] = normalized
    return out


def _normalize_patch_side_quotes(side_spec: dict) -> dict:
    """Decode one level of LLM double-escaping in patch search/replace strings."""
    if not isinstance(side_spec, dict):
        return side_spec
    out = dict(side_spec)
    for key in ("search", "replace"):
        val = out.get(key)
        if not isinstance(val, str):
            continue
        if r'\"' in val:
            val = val.replace(r'\"', '"')
        if r"\'" in val:
            val = val.replace(r"\'", "'")
        out[key] = val
    return out


def _normalize_patch_entries(entries: list) -> list:
    """Normalize patch JSON before delegating to shared patch_engine.add_patches."""
    out: list = []
    for entry in entries:
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        e = dict(entry)
        for side in ("source", "migrated"):
            if side in e:
                e[side] = _normalize_patch_side_quotes(e[side])
        out.append(e)
    return out


def save_schemas_manifest(conv_root: Path, manifest: dict) -> None:
    """Write ``schemas/manifest.json`` (SoT for expected_divergences, PySpark parity)."""
    schemas_dir(conv_root).mkdir(parents=True, exist_ok=True)
    write_atomic(schemas_manifest_path(conv_root), _normalize_manifest_weights(manifest))


def _merge_expected_divergence_entry(
    exp: dict,
    *,
    trial_id: str,
    sink_id: str,
    column: str,
    div_entry: dict,
    scope: str,
) -> None:
    """Upsert one divergence entry under all canonical sink keys."""
    col = column.upper()
    sink_keys = {f"{trial_id}.{sink_id}"}
    if scope in ("udf", "serialization"):
        sink_keys.add(f"{trial_id}.__udf__")
    norm = normalize_sink_name(sink_id)
    if norm:
        sink_keys.add(f"{trial_id}.{norm}")
    for key in sink_keys:
        lst = list(exp.get(key) or [])
        idx = next(
            (i for i, d in enumerate(lst)
             if isinstance(d, dict) and (d.get("column") or "").upper() == col),
            -1,
        )
        exp[key] = (lst[:idx] + [div_entry] + lst[idx + 1:]) if idx >= 0 else lst + [div_entry]


def _import_scala_schema_mine():
    """Load this skill's schema_mine by path (avoid PySpark schema_mine shadow)."""
    import importlib.util
    _sm_path = Path(__file__).resolve().parent / "schema_mine.py"
    _spec = importlib.util.spec_from_file_location("scos_schema_mine", _sm_path)
    mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _prune_schemas_to_selected(
    conv_root: Path,
    manifest: dict,
    selected_ids: set,
) -> int:
    """Keep only selected entrypoint dirs under schemas/ (PySpark prepare-batches parity)."""
    before = len(manifest.get("entrypoints") or [])
    sd = schemas_dir(conv_root)
    kept_refs = []
    for ref in manifest.get("entrypoints") or []:
        ep_id = ref.get("id")
        if ep_id not in selected_ids:
            ep_dir = sd / "entrypoints" / (ep_id or "")
            if ep_dir.is_dir():
                shutil.rmtree(ep_dir, ignore_errors=True)
        else:
            kept_refs.append({
                "id": ep_id,
                "path": ref.get("path") or ep_id,
                "dir": ref.get("dir") or f"entrypoints/{ep_id}",
                "source_runtime": ref.get("source_runtime"),
                "weight": ref.get("weight"),
                "weight_breakdown": ref.get("weight_breakdown"),
            })
    manifest["entrypoints"] = kept_refs
    divs = manifest.get("expected_divergences")
    if isinstance(divs, dict):
        for key in list(divs):
            trial_id = key.split(".", 1)[0] if isinstance(key, str) and "." in key else key
            if trial_id not in selected_ids:
                del divs[key]
    summary = manifest.get("summary")
    if isinstance(summary, dict):
        summary["n_entrypoints"] = len(kept_refs)
    sd.mkdir(parents=True, exist_ok=True)
    write_atomic(sd / "manifest.json", manifest)
    return before - len(kept_refs)


def _copy_schemas_to_worktree(primary: Path, worktree: Path) -> None:
    """Copy primary schemas/ into a batch worktree (full copy; prune afterward)."""
    src = schemas_dir(primary)
    dst = schemas_dir(worktree)
    if not src.is_dir():
        return
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(str(src), str(dst))


def ensure_analysis_shim_from_schemas(conv_root: Path) -> None:
    """Regenerate analysis.json from schemas/ when schemas/ is the SoT."""
    if not schemas_manifest_path(conv_root).is_file():
        return
    try:
        sm = _import_scala_schema_mine()
        sm.schemas_to_analysis_shim(conv_root)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[scos-control] WARNING: schemas→analysis shim failed: {exc}",
              file=sys.stderr)


def load_analysis_prefer_schemas(conv_root: Path) -> dict:
    """Load analysis for gates: refresh shim from schemas/ when present, then read.

    ``schemas/`` is the agent-editable SoT (PySpark parity). ``analysis.json`` is
    the generated JVM/prevalidate shim — always regenerate it before reading when
    ``schemas/manifest.json`` exists so completeness checks cannot drift.
    """
    if schemas_manifest_path(conv_root).is_file():
        ensure_analysis_shim_from_schemas(conv_root)
    return load_analysis(conv_root)


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
    the LLM data-synthesizer writes ``ep["external_sinks"]``. ``schema_mine`` reads
    both, so every sink-consuming gate here MUST too — otherwise a
    synthesizer-authored workload (sinks under ``external_sinks``, ``sinks: []``)
    is falsely treated as a no-sink trial and blocked / mis-verdicted.
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
        Path(ep_rel).suffix in (".scala", ".sc") or Path(ast_rel).suffix in (".scala", ".sc")
    )


def entrypoint_ast_write_evidence(
    ast_facts: Optional[dict], ep: dict,
) -> List[str]:
    """Return evidence strings when AST facts show writes for this entrypoint.

    Used to refuse synthetic ``no_sink_baseline`` when ``analysis.json`` has
    ``sinks: []`` but Scalameta still saw writes / write_helpers / unresolved
    writes — that is an analysis mining gap, not a true no-sink trial.
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
        if not rel.parts:
            # dst IS src — nothing to copy
            return
        exclude_name = rel.parts[0]
        shutil.copytree(src, dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(exclude_name))
    except ValueError:
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=str(cwd), capture_output=True, text=True)


def _run_sbt_streaming(
    cmd: list,
    cwd: str,
    env: dict,
    log_path: "Path",
) -> int:
    """Run an sbt command, streaming output to both the terminal and *log_path*.

    Returns the process exit code. Uses line-buffered Popen so agents see sbt
    output in real time instead of waiting for sbt to finish (which can take
    10–20 min for a full batch).  Mirrors PySpark run-tests live streaming.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log_f:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        assert proc.stdout is not None
        for raw in proc.stdout:
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                line = repr(raw) + "\n"
            sys.stdout.write(line)
            sys.stdout.flush()
            log_f.write(line)
        proc.wait()
    return proc.returncode


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

    Mirrors PySpark validate.py advance_phase: advances when all trials have the
    required iters (phase_a_iters for phase_a_done; phase_b_iters / terminal
    statuses for phase_b_done). Does NOT require all trials to be terminal —
    the phase tracks iteration progress, not final verdict.

    Also flips ``phase_a_complete`` / ``phase_b_complete`` milestones (PySpark
    parity) so ``batch.py`` pool phase labels stay accurate.
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
    # Flip pool-visible milestones in lockstep with phase (PySpark parity).
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
    auto-promotion path was taken (parity with PySpark _infer_pass_status).
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


# Boilerplate phase_a_skipped reasons that must never reach the report.
# Matched case-insensitively against the stripped reason (exact phrase).
_PHASE_A_SKIP_DENYLIST = frozenset({
    "unknown", "n/a", "na", "error", "failed", "timeout",
    "environment", "spark issue", "cannot run",
})


def _weak_phase_a_skip_reason(reason: str) -> Optional[str]:
    """Return a short rejection cause when *reason* is too weak, else None.

    Requires length >= 12 and at least one token that looks like a named
    construct/class (ALL_CAPS, CamelCase, or dotted FQN fragment). Exact
    deny-list phrases are always rejected.
    """
    r = (reason or "").strip()
    if not r:
        return "blank"
    normalized = re.sub(r"\s+", " ", r.lower())
    if normalized in _PHASE_A_SKIP_DENYLIST:
        return f"deny-list phrase '{normalized}'"
    if len(r) < 12:
        return "too short (< 12 chars)"
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_.$]*", r)
    def _looks_like_construct(t: str) -> bool:
        if "." in t and len(t) >= 5:
            return True  # com.example.Foo / package.Type
        if t.isupper() and len(t) >= 3:
            return True  # QUALIFY, RDD, …
        if len(t) >= 3 and any(c.isupper() for c in t[1:]) and any(c.islower() for c in t):
            return True  # CamelCase / PascalCase
        return False
    if not any(_looks_like_construct(t) for t in tokens):
        return "no named construct/class token"
    return None


def apply_trial_status(
    state: dict, trial_id: str, status: str,
    final_iter: Optional[int] = None, reason: Optional[str] = None,
    analysis_repair_exhausted: bool = False, baseline_not_comparable: bool = False,
    harness_repair_exhausted: bool = False, patch_repair_exhausted: bool = False,
    allow_derived: bool = False,
) -> Tuple[dict, Optional[int], Optional[str], bool]:
    """Pure core of record-trial-status. Returns (new_state, exit_code, error, noop).

    exit_code/error are set (and new_state is unchanged) when the transition is
    rejected; noop=True is the idempotent already-terminal-same case (exit 0).

    Mirrors PySpark ``validate.py`` cmd_record_trial_status gates so Phase A can
    only be skipped for a genuine, *named* environment reason:
      - ``phase_a_skipped`` / ``hard_stuck`` REQUIRE a non-blank --reason.
      - ``phase_a_skipped`` rejects deny-listed boilerplate and requires a
        construct/class token (length >= 12).
      - ``phase_a_skipped`` stores a dedicated ``phase_a_skip_reason`` that is
        PRESERVED through the phase_a_skipped -> passed_no_baseline promotion so
        the report can always explain a missing baseline.
      - ``passed_no_baseline`` is DERIVED, never set directly by a runner
        (internal promotion passes ``allow_derived=True``); the runner path only
        reaches it via ``phase_a_skipped`` + a clean Phase B or the explicit
        ``--baseline-not-comparable --reason`` escape.
      - ``passed`` requires the latest Phase B iter to show passing>=1, failing==0.
      - ``hard_stuck`` requires a fixer dispatch OR documented exhaustion of an
        inline-repair track (analysis / harness / patch).
    """
    if status not in TRIAL_STATUSES:
        return state, 2, f"invalid status '{status}'; expected one of: {', '.join(sorted(TRIAL_STATUSES))}", False
    trials: Dict[str, dict] = state.get("trials") or {}
    if trial_id not in trials:
        return state, 2, f"trial '{trial_id}' not in state.trials", False

    current = _status(trials[trial_id])
    if current == status and current in TERMINAL_TRIAL_STATUSES:
        return state, 0, None, True  # idempotent no-op

    trial = trials[trial_id]
    all_iters = (trial.get("phase_b_iters") or []) + (trial.get("phase_a_iters") or [])

    if status == "hard_stuck":
        dispatches = state.get("fixer_dispatches") or []
        has_dispatch = any(
            trial_id in (d.get("trials_affected") or []) for d in dispatches
        )
        # Schema/data gaps (TABLE_OR_VIEW_NOT_FOUND / COLUMN_NOT_FOUND) are repaired
        # inline (edit schemas, datagen, provision), never by the fixer.
        schema_repair_iters = [
            it for it in all_iters
            if it.get("fix_category") in ("schema_gap", "analysis_repair")
        ]
        harness_repair_iters = [
            it for it in all_iters if it.get("fix_category") == "harness_failure"
        ]
        patch_repair_iters = [
            it for it in all_iters if it.get("fix_category") == "patch_failure"
        ]
        any_exhausted = (analysis_repair_exhausted or harness_repair_exhausted
                         or patch_repair_exhausted)
        if not (has_dispatch or any_exhausted):
            return (state, 2,
                    f"REJECTED: cannot mark trial '{trial_id}' hard_stuck — no fixer "
                    f"dispatch and no inline-repair exhaustion on record. For "
                    f"code/dialect errors dispatch the migration-fixer first; for "
                    f"missing tables/columns run the inline schema-repair loop "
                    f"(edit schemas, re-run schema_mine + datagen, provision) recorded via "
                    f"record-iter --fix-category analysis_repair, then pass "
                    f"--analysis-repair-exhausted; for harness kit issues use "
                    f"--fix-category harness_failure and --harness-repair-exhausted; "
                    f"for missing blueprint patches use --fix-category patch_failure "
                    f"and --patch-repair-exhausted. See agents/scos-runner.md.", False)
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
        if harness_repair_exhausted and not has_dispatch and not harness_repair_iters:
            return (state, 2,
                    f"REJECTED: cannot mark trial '{trial_id}' hard_stuck with "
                    f"--harness-repair-exhausted before any harness repair is on "
                    f"record. Fix the shared kit under Validation/tests/ and record "
                    f"the attempt via record-iter --fix-category harness_failure "
                    f"before declaring exhaustion. See agents/scos-runner.md.", False)
        if patch_repair_exhausted and not has_dispatch and not patch_repair_iters:
            return (state, 2,
                    f"REJECTED: cannot mark trial '{trial_id}' hard_stuck with "
                    f"--patch-repair-exhausted before any patch repair is on record. "
                    f"Add blueprint patches with patch-add and record the attempt via "
                    f"record-iter --fix-category patch_failure before declaring "
                    f"exhaustion. See agents/scos-runner.md.", False)

    # 'passed' requires the latest Phase B iter to show passing>=1 and failing==0.
    # A runner must never declare a match without a green SCOS run on record.
    if status == "passed":
        b_iters = trial.get("phase_b_iters") or []
        if not b_iters:
            return (state, 2,
                    f"REJECTED: cannot mark trial '{trial_id}' passed — no Phase B "
                    f"iterations recorded. Run Phase B (run-phase-b) and record its "
                    f"result before marking passed. See agents/scos-runner.md.", False)
        latest = b_iters[-1]
        if not (latest.get("passing", 0) >= 1 and latest.get("failing", 1) == 0):
            return (state, 2,
                    f"REJECTED: cannot mark trial '{trial_id}' passed — latest Phase B "
                    f"iter {latest.get('iter', '?')} has passing={latest.get('passing', 0)}, "
                    f"failing={latest.get('failing', 0)}. 'passed' requires passing>=1 "
                    f"and failing==0. See agents/scos-runner.md.", False)

    # 'passed_no_baseline' is DERIVED, never set directly by a runner. It is reached
    # only by (a) internal promotion of a reasoned 'phase_a_skipped' after a clean
    # Phase B (allow_derived=True), or (b) the explicit --baseline-not-comparable
    # --reason escape for the rare "Phase A captured different sinks" case. Setting
    # it directly would let a trial reach a no-baseline verdict with no surfaced
    # reason — exactly the gap this closes.
    if status == "passed_no_baseline" and not (allow_derived or baseline_not_comparable):
        baseline_produced = any(
            it.get("passing", 0) >= 1 and it.get("failing", 1) == 0
            for it in (trial.get("phase_a_iters") or [])
        )
        if baseline_produced:
            return (state, 2,
                    f"REJECTED: cannot mark trial '{trial_id}' passed_no_baseline — "
                    f"Phase A produced a baseline (a phase_a iter passed), so it MUST "
                    f"be compared. Mark 'passed' once SCOS matches it (small "
                    f"date-relative row-count diffs are cosmetic — record them with "
                    f"document-divergence and the trial still passes); treat an "
                    f"unresolved REAL divergence as hard_stuck. See agents/scos-runner.md.",
                    False)
        return (state, 2,
                f"REJECTED: do not set trial '{trial_id}' passed_no_baseline directly. "
                f"It is derived: if Phase A cannot produce a comparable baseline at "
                f"all, mark 'phase_a_skipped --reason <why>' — Phase B then "
                f"auto-promotes a clean run to passed_no_baseline, preserving that "
                f"reason. If Phase A genuinely captured different sinks than Phase B, "
                f"pass --baseline-not-comparable --reason. See agents/scos-runner.md.",
                False)

    # hard_stuck and phase_a_skipped are last-resort statuses surfaced in the final
    # report; both REQUIRE a human-readable --reason so the report can explain why a
    # trial could not be matched (hard_stuck) or why no local baseline was produced
    # (phase_a_skipped). --baseline-not-comparable likewise needs a reason.
    if status in ("hard_stuck", "phase_a_skipped") and not (reason or "").strip():
        return (state, 2,
                f"REJECTED: --reason is required when marking trial '{trial_id}' "
                f"'{status}'. It is surfaced in the final report. For phase_a_skipped, "
                f"name the specific construct the source runtime genuinely cannot "
                f"execute (last resort — missing tables/columns are inline schema "
                f"repairs and connector reads are patches, NOT skips). For hard_stuck, "
                f"state the confirmed no-workaround limitation (rare — most failures "
                f"are fixable). See agents/local-runner.md / scos-runner.md.", False)
    if status == "phase_a_skipped":
        weak = _weak_phase_a_skip_reason((reason or "").strip())
        if weak:
            return (state, 2,
                    f"REJECTED: phase_a_skipped reason for trial '{trial_id}' is too "
                    f"weak ({weak}). Name the specific unsupported construct/class "
                    f"(e.g. 'QUALIFY in rank.sql', 'VariantType in bronze') — length "
                    f">= 12, not boilerplate like 'unknown'/'error'/'spark issue'. "
                    f"See agents/local-runner.md / scos-runner.md.", False)
    if status == "passed_no_baseline" and baseline_not_comparable and not (reason or "").strip():
        return (state, 2,
                f"REJECTED: --baseline-not-comparable requires --reason naming why "
                f"Phase A's captured sinks are not comparable to Phase B's. "
                f"See agents/scos-runner.md.", False)

    updated = {**trial, "status": status}
    if final_iter is not None:
        updated["final_iter"] = final_iter
    if status == "hard_stuck" and reason:
        updated["hard_stuck_reason"] = reason
    elif status == "phase_a_skipped" and reason:
        # Dedicated field, PRESERVED across the phase_a_skipped -> passed_no_baseline
        # promotion so the report still explains the missing baseline.
        updated["phase_a_skip_reason"] = reason
    elif status == "passed_no_baseline" and reason:
        # --baseline-not-comparable path: record why the baseline was not comparable.
        updated["phase_a_skip_reason"] = reason
    if status == "passed":
        updated.pop("hard_stuck_reason", None)
        updated.pop("phase_a_skip_reason", None)

    new_trials = {**trials, trial_id: updated}
    # conv_root is optional on apply_trial_status callers; milestone events skip
    # when absent (pool labels still flip via milestones dict on the returned state).
    new_state = advance_phase({**state, "trials": new_trials})
    return new_state, None, None, False


def materialize_manual_review_statuses(conv_root: Path, state: dict) -> dict:
    """Pending trials with BOTH a _manual_review.json marker AND _index.json
    (capture evidence) become passed_no_baseline. Mirrors ScosState.

    The no-baseline reason is preserved as ``phase_a_skip_reason`` (pulled from
    the marker's ``reason`` when present, else the trial's existing skip reason,
    else a generic default) so the final report always explains the missing
    baseline — parity with PySpark ``validate.py``.
    """
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
            marker_reason = ""
            try:
                marker_reason = (json.loads(marker.read_text(encoding="utf-8"))
                                 .get("reason") or "").strip()
            except (ValueError, OSError):
                marker_reason = ""
            reason = (marker_reason
                      or trial.get("phase_a_skip_reason")
                      or "manual review required; no comparable Phase A baseline")
            updated[tid] = {**trial, "status": "passed_no_baseline",
                            "manual_review_marker": str(marker),
                            "phase_a_skip_reason": reason}
            changed = True
    if changed:
        return advance_phase({**state, "trials": updated})
    return state


def recover_pending_trials(state: dict) -> Tuple[dict, int]:
    """Promote trials with a final Phase B iter to their terminal status.

    Handles TWO auto-promotable statuses (parity with PySpark
    ``_AUTO_PROMOTABLE_STATUSES = frozenset({"pending", "phase_a_skipped"})``):

    * ``pending`` — normal path. A clean Phase B promotes to ``passed``
      (when Phase A produced a comparable baseline) or ``passed_no_baseline``
      (no baseline); a failing Phase B promotes to ``hard_stuck``.
    * ``phase_a_skipped`` — the explicit-skip path. Phase B was still run
      (we always run Phase B even when Phase A was skipped). A clean Phase B
      promotes to ``passed_no_baseline`` preserving the skip reason; a failing
      Phase B promotes to ``hard_stuck`` with the fixer-dispatch gate bypass
      (the skip itself is a documented exhaustion).

    ``passed`` requires a *comparable Phase A baseline* (a phase_a iter that
    passed). ``passed_no_baseline`` always preserves ``phase_a_skip_reason``
    (or sets a generic default) so the report explains the missing baseline.

    True no-sink smoke (``no_sink_baseline`` on a Phase A iter) may finish Phase B
    with ``passing=0, failing=0`` — that is still a clean execution-parity pass.
    Zero-capture Phase B without that flag stays ``pending`` (do not soft-green
    sink capture failures or all-``allow_empty`` exemptions).
    """
    # hard_stuck is included so that a trial that failed Phase B but was subsequently
    # fixed (code patched, jars rebuilt) can be promoted once it passes on a re-run,
    # without requiring --verify-all.
    _PROMOTABLE = frozenset({"pending", "phase_a_skipped", "hard_stuck"})
    trials: Dict[str, dict] = state.get("trials") or {}
    recovered = 0
    updated = dict(trials)
    for tid, t in trials.items():
        status = _status(t)
        b_iters = t.get("phase_b_iters") or []
        a_iters = t.get("phase_a_iters") or []
        if status not in _PROMOTABLE or not b_iters:
            continue
        baseline_produced = any(
            it.get("passing", 0) >= 1 and it.get("failing", 1) == 0 for it in a_iters
        )
        no_sink_baseline = any(bool(it.get("no_sink_baseline")) for it in a_iters)
        # A phase_a_skipped trial has no trustworthy baseline even if it somehow
        # recorded a passing Phase A iter — the explicit skip reason is authoritative.
        if status == "phase_a_skipped" or t.get("phase_a_skip_reason"):
            baseline_produced = False
            no_sink_baseline = False
        last_b = b_iters[-1]
        passing = last_b.get("passing", 0)
        failing = last_b.get("failing", 0)
        if passing > 0 and failing == 0:
            new_status = "passed" if baseline_produced else "passed_no_baseline"
        elif failing > 0:
            new_status = "hard_stuck"
        elif no_sink_baseline and failing == 0:
            # Clean Phase B with zero tables is expected for AST-confirmed no-sink.
            new_status = "passed"
        else:
            # passing==0 and failing==0 without no_sink_baseline: incomplete capture
            # (e.g. all sinks allow_empty, or Phase B wrote nothing). Stay pending —
            # never promote to passed_no_baseline from an empty capture alone.
            new_status = status
        if new_status != status:
            recovered += 1
            t2 = {**t, "status": new_status}
            if new_status == "hard_stuck":
                reason = (
                    "auto-recovered: phase_b_failure after phase_a_skipped"
                    if status == "phase_a_skipped"
                    else "auto-recovered: phase_b_failure"
                )
                t2["hard_stuck_reason"] = reason
            elif new_status == "passed_no_baseline":
                # Preserve the skip reason; set a generic default if none recorded
                # so the report never shows a blank no-baseline verdict.
                t2["phase_a_skip_reason"] = (
                    t.get("phase_a_skip_reason")
                    or "no comparable Phase A baseline produced")
            elif new_status == "passed" and no_sink_baseline and passing == 0:
                # Do NOT set phase_a_skip_reason — that field means "no trustworthy
                # baseline" and would flip run_index phase_a.verdict to no_baseline.
                t2["no_sink_smoke"] = True
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
        # Surface the required reason: matched-baseline for a pass, the hard_stuck
        # reason for a real divergence, or the PRESERVED phase_a_skip_reason so a
        # no-baseline verdict always explains itself (parity with PySpark).
        no_sink_baseline = any(
            bool(it.get("no_sink_baseline")) for it in (trial.get("phase_a_iters") or [])
        ) or bool(trial.get("no_sink_smoke"))
        reason = (
            trial.get("hard_stuck_reason")
            or trial.get("phase_a_skip_reason")
            or (
                "no-sink smoke: clean run with no declared sinks to compare"
                if status == "passed" and no_sink_baseline
                else ("matched baseline" if status == "passed" else "")
            )
        )
        # Phase A verdict (run_index schema): baseline_produced | no_sink_baseline |
        # no_baseline | phase_a_skipped. A trial with a preserved skip reason lacks a
        # trustworthy baseline even if an unusable capture recorded a passing iter.
        a_baseline = any(
            it.get("passing", 0) >= 1 and it.get("failing", 1) == 0
            for it in (trial.get("phase_a_iters") or [])
        )
        if status == "phase_a_skipped" or trial.get("phase_a_skip_reason"):
            phase_a_verdict = "phase_a_skipped" if status == "phase_a_skipped" else "no_baseline"
        elif no_sink_baseline:
            phase_a_verdict = "no_sink_baseline"
        elif a_baseline:
            phase_a_verdict = "baseline_produced"
        else:
            phase_a_verdict = "no_baseline"
        phase_a_block = {**phase_a_block, "verdict": phase_a_verdict,
                         "skip_reason": trial.get("phase_a_skip_reason", "")}

        # Comparison verdict: prefer compare.json (written by _run_comparators_for_passed_trials)
        # over the status-based fallback so the verdict reflects actual parquet diffs.
        comp_verdict = comparison_verdict(trial)   # status-based fallback
        comp_diffs = diff_entries                  # legacy *_diff.json entries
        compare_json_path = phase_b_dir / "compare.json"
        if compare_json_path.is_file():
            try:
                cj = json.loads(compare_json_path.read_text(encoding="utf-8"))
                cj_verdict = cj.get("verdict", "error")
                has_divs = bool(trial.get("documented_divergences"))
                if cj_verdict == "match":
                    comp_verdict = "match"
                elif cj_verdict == "diverge":
                    # diverge with documented divergences = cosmetic; without = real
                    comp_verdict = "cosmetic_divergence" if has_divs else "real_divergence"
                else:  # "error" or unexpected
                    comp_verdict = "unverified"
                comp_diffs = cj.get("tables") or diff_entries
            except Exception:  # noqa: BLE001
                pass  # keep status-based fallback

        out.append({
            "id": tid,
            "source_path": source_path,
            "phase_a": phase_a_block,
            "phase_b": phase_b_block,
            "comparison": {
                "verdict": comp_verdict,
                "diffs": comp_diffs,
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
        "| Trial | Status | A iters | B iters | Fix Category | Reason (hard_stuck / phase_a_skip) |",
        "|-------|--------|---------|---------|--------------|-------------------------------------|",
    ]
    for tid in sorted(trials):
        t = trials[tid]
        reason = t.get("hard_stuck_reason") or t.get("phase_a_skip_reason", "")
        lines.append(f"| {tid} | {_status(t)} | {len(t.get('phase_a_iters') or [])} | "
                     f"{len(t.get('phase_b_iters') or [])} | {t.get('fix_category', '')} | "
                     f"{reason} |")
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

    # Every selected trial must reach a terminal verdict before summary can produce
    # a meaningful report (parity with PySpark _require_terminal_trials_for_summary).
    non_terminal = sorted(
        tid for tid, t in trials.items()
        if _status(t) not in TERMINAL_TRIAL_STATUSES
    )
    if non_terminal:
        return _die(
            "summary blocked — non-terminal trials: "
            + ", ".join(non_terminal)
            + ". Resolve them (or run `run-phase-b` to auto-promote passing ones) "
            "before re-running summary.",
            1,
        )

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
                                 "detail": (t.get("phase_a_skip_reason")
                                            or "SCOS run passed without a trustworthy Phase A baseline")})
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


_ALIGN_CODE_EXTS = (".scala", ".sc", ".sql")
_ALIGN_SKIP_DIRS = {
    ".git", ".bsp", ".idea", ".metals", ".bloop", "target", "project",
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
    database = args.database
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
            "synth_survey", "entrypoints_selected", "synth_deep", "patches_authored",
            "workload_built", "tests_authored", "venv_prewarmed", "snowflake_provisioned",
            "phase_a_complete", "phase_b_complete")},
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
    phase_filter = getattr(args, "phase", "all") or "all"
    verbose = bool(getattr(args, "verbose", False))
    print(f"Phase: {phase}")
    print(f"Phase A iter: {(state.get('phase_a') or {}).get('iter', 0)}")
    print(f"Phase B iter: {(state.get('phase_b') or {}).get('iter', 0)}")
    print()
    if not trials:
        print("No trials configured.")
        return 1
    any_pending = any_review = any_blocked = False
    for tid in sorted(trials):
        trial = trials[tid]
        st = _status(trial)
        any_pending = any_pending or st == "pending"
        any_review = any_review or st == "passed_no_baseline"
        any_blocked = any_blocked or st == "hard_stuck"
        print(f"  {tid}: {st}")
        if verbose:
            if phase_filter in ("A", "all"):
                for it in trial.get("phase_a_iters") or []:
                    print(f"    Phase A iter {it.get('iter', '?')}: "
                          f"pass={it.get('passing', 0)} fail={it.get('failing', 0)} "
                          f"patches_extended={it.get('extended_patches', 0)}")
            if phase_filter in ("B", "all"):
                for it in trial.get("phase_b_iters") or []:
                    print(f"    Phase B iter {it.get('iter', '?')}: "
                          f"pass={it.get('passing', 0)} fail={it.get('failing', 0)} "
                          f"issues={it.get('issues', 0)} "
                          f"fix_commit={it.get('fix_commit', 'none')}")
    # Show last Phase B iter across all trials when filtering to B (non-verbose).
    if phase_filter == "B" and not verbose:
        last_b = None
        for trial in trials.values():
            for it in trial.get("phase_b_iters") or []:
                if last_b is None or it.get("iter", 0) > last_b.get("iter", 0):
                    last_b = it
        if last_b is not None:
            print(f"\n  Last Phase B iter: failing={last_b.get('failing', '?')}, "
                  f"fix_commit={last_b.get('fix_commit', 'none')}")
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
    state = advance_phase(state, conv_root)
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
        baseline_not_comparable=getattr(args, "baseline_not_comparable", False),
        harness_repair_exhausted=getattr(args, "harness_repair_exhausted", False),
        patch_repair_exhausted=getattr(args, "patch_repair_exhausted", False))
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
    A is fast. Safe to run in the background right after `init`."""
    conv_root = Path(args.conv_root).expanduser().resolve()
    state = load_state(conv_root)
    skill_dir = Path(__file__).resolve().parent.parent
    kit_src = skill_dir / "harness-scala" / "kit"
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

    # Resolve (and, if absent, auto-provision) a Java 8/11/17 JDK now so the first
    # real Phase A pays neither the JDK-detection nor the Coursier-fetch cost, and so
    # the kit warm-compile below uses the SAME compatible JVM Phase A will run on.
    # HARD-FAIL if unresolved: venv_prewarmed must mean the JDK+sbt path actually ran.
    java_home = _resolve_phase_a_jdk(allow_provision=True)
    if not java_home:
        return _die(
            "prewarm FAILED — could not resolve/provision a Java 8/11/17 JDK "
            "(Spark 3.5 does not support Java 21+). Install JDK 17 or make Coursier "
            "reachable so the harness can auto-provision temurin:17. Milestone "
            "venv_prewarmed was NOT set.", 3)
    print(f"[scos-control] prewarm: resolved Phase A JDK -> {java_home}")

    # Best-effort pre-stage the SCOS client jar so Phase B preflight is satisfied
    # without a mid-run scramble (no-op / warns when it isn't in a cache yet).
    _stage_scos_client_jar(tests_dir, conv_root)

    # Warm sbt: resolve deps + compile the kit (Test/compile pulls test deps too).
    if not shutil.which("sbt"):
        return _die(
            "prewarm FAILED — sbt not on PATH. Install sbt 1.x before Phase A. "
            "Kit was staged but venv_prewarmed was NOT set.", 3)

    result = subprocess.run(
        ["sbt", "-batch", "Test/compile"], cwd=str(tests_dir),
        capture_output=True, text=True,
        env=_apply_jdk_to_env(dict(os.environ), java_home),
    )
    if result.returncode != 0:
        # HARD-FAIL: a broken kit compile must not mark venv_prewarmed — agents
        # would skip re-prewarm and burn Phase A on a cold/broken harness.
        err_tail = (result.stderr or result.stdout or "")[-2000:]
        return _die(
            f"prewarm FAILED — kit Test/compile returned {result.returncode}. "
            f"venv_prewarmed was NOT set. Fix the harness kit under "
            f"{tests_dir} (or re-copy from harness-scala/kit/) and re-run prewarm."
            f"\n{err_tail}", 3)

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

# ---------------------------------------------------------------------------
# JVM readiness — Phase A's local Spark 3.5 (and Phase B's SCOS/Arrow client)
# run ONLY on Java 8/11/17. The harness auto-provisions a compatible JDK so runs
# are one-shot regardless of the ambient JVM: the eval image ships JDK 21, which
# Spark 3.5 rejects at session startup (before any baseline can be captured),
# which is the root cause of "Phase A never produces a baseline". Per the agreed
# design: prefer an installed 8/11/17 JVM, else provision Temurin 17 via the
# shared Coursier bootstrap.
# ---------------------------------------------------------------------------

_SUPPORTED_JDK_MAJORS = (8, 11, 17)
_JDK_CACHE_FILE = Path.home() / ".cache" / "scos" / "phase_a_jdk.txt"


def _java_major(java_home: str) -> "Optional[int]":
    """Return the major version (8/11/17/21…) of the JDK at *java_home*, or None.

    Parses `java -version` (which prints to stderr), handling both the legacy
    `1.8.0_x` form (-> 8) and the modern `17.0.x` form (-> 17).
    """
    if not java_home:
        return None
    java_bin = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
    if not java_bin.is_file():
        return None
    try:
        out = subprocess.run([str(java_bin), "-version"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    blob = (out.stderr or "") + (out.stdout or "")
    m = re.search(r'version "(\d+)(?:\.(\d+))?', blob)
    if not m:
        return None
    major = int(m.group(1))
    if major == 1 and m.group(2):  # 1.8.0_x -> 8
        major = int(m.group(2))
    return major


def _cache_jdk(home: str) -> None:
    try:
        _JDK_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _JDK_CACHE_FILE.write_text(home, encoding="utf-8")
    except OSError:
        pass


def _candidate_java_homes() -> list:
    """Best-effort list of installed JDK homes to probe (most-specific first)."""
    homes: list = []
    env_home = os.environ.get("JAVA_HOME", "").strip()
    if env_home:
        homes.append(env_home)
    if sys.platform == "darwin":
        for major in _SUPPORTED_JDK_MAJORS:
            try:
                r = subprocess.run(["/usr/libexec/java_home", "-v", str(major)],
                                   capture_output=True, text=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0 and r.stdout.strip():
                homes.append(r.stdout.strip())
    jvm_dir = Path("/usr/lib/jvm")
    if jvm_dir.is_dir():
        homes.extend(str(p) for p in sorted(jvm_dir.iterdir()) if p.is_dir())
    seen: set = set()
    uniq: list = []
    for h in homes:
        if h and h not in seen:
            seen.add(h)
            uniq.append(h)
    return uniq


def _provision_jdk_via_coursier(major: int) -> "Optional[str]":
    """Provision a Temurin JDK of *major* via the shared Coursier launcher.

    Reuses the proven, no-raise ``_bootstrap_coursier`` from the sibling migrate
    skill's ``preprocess_scalafix``. Returns the JAVA_HOME reported by
    ``cs java-home --jvm temurin:<major>``, or None if Coursier is unavailable or
    the fetch fails.
    """
    migrate_scripts = Path(__file__).resolve().parent.parent.parent / "scripts"
    if str(migrate_scripts) not in sys.path:
        sys.path.insert(0, str(migrate_scripts))
    try:
        from preprocess_scalafix import _bootstrap_coursier  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"[scos-control] WARNING: cannot import Coursier bootstrap ({exc}); "
              "cannot auto-provision a JDK", file=sys.stderr)
        return None
    cs = _bootstrap_coursier()
    if not cs:
        return None
    try:
        r = subprocess.run([cs, "java-home", "--jvm", f"temurin:{major}"],
                           capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[scos-control] WARNING: `cs java-home` failed: {exc}", file=sys.stderr)
        return None
    home = ((r.stdout or "").strip().splitlines() or [""])[-1].strip()
    if r.returncode != 0 or not home:
        print(f"[scos-control] WARNING: Coursier could not provision JDK {major}: "
              f"{(r.stderr or '').strip()[:400]}", file=sys.stderr)
        return None
    print(f"[scos-control] provisioned JDK {major} via Coursier -> {home}")
    return home


def _resolve_phase_a_jdk(allow_provision: bool = True) -> "Optional[str]":
    """Return a JAVA_HOME for a Java 8/11/17 JDK (the Spark 3.5 requirement).

    1. Reuse a previously resolved+cached compatible JDK.
    2. Detect an installed 8/11/17 JVM (JAVA_HOME, /usr/libexec/java_home, /usr/lib/jvm).
    3. Fall back to provisioning Temurin 17 via Coursier (when *allow_provision*).

    Returns None only when nothing compatible can be found or provisioned — the
    caller (preflight) turns that into a hard failure with remediation, so an
    incompatible JVM can never silently yield a no-baseline pass.
    """
    try:
        if _JDK_CACHE_FILE.is_file():
            cached = _JDK_CACHE_FILE.read_text(encoding="utf-8").strip()
            if cached and _java_major(cached) in _SUPPORTED_JDK_MAJORS:
                return cached
    except OSError:
        pass
    for home in _candidate_java_homes():
        if _java_major(home) in _SUPPORTED_JDK_MAJORS:
            _cache_jdk(home)
            return home
    if allow_provision:
        home = _provision_jdk_via_coursier(17)
        if home and _java_major(home) in _SUPPORTED_JDK_MAJORS:
            _cache_jdk(home)
            return home
    return None


def _apply_jdk_to_env(env: dict, java_home: str) -> dict:
    """Return a copy of *env* with JAVA_HOME set and $JAVA_HOME/bin prepended to PATH.

    Applied to the sbt subprocess environment so BOTH the sbt build JVM and its
    ``Test/fork`` child (which runs local Spark in Phase A) use the compatible JDK,
    and so the workload source jar is COMPILED with the same JDK it is RUN with
    (avoiding UnsupportedClassVersionError from class-file version skew).
    """
    if not java_home:
        return env
    env = dict(env)
    env["JAVA_HOME"] = java_home
    bin_dir = str(Path(java_home) / "bin")
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
    return env


def _scos_client_jar_locatable(tests_dir: Path, conv_root: Path) -> bool:
    """Read-only check: is the SCOS Scala client jar reachable for Phase B staging?

    Mirrors the search paths in ``_stage_scos_client_jar`` without copying anything —
    used by preflight to hard-fail early instead of hitting a runtime
    ClassNotFoundException mid-Phase-B.
    """
    if list((tests_dir / "lib").glob("snowpark-connect-java-client*.jar")):
        return True
    search = [
        conv_root / "Output" / "lib",
        Path.home() / ".m2" / "repository" / "com" / "snowflake",
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


def _preflight_checks(conv_root: Path, phase: str) -> "Tuple[int, list, Optional[str]]":
    """Deterministic environment-readiness gate run BEFORE Phase A/B.

    Verifies everything the JVM run needs so a run either proceeds to produce a
    real baseline or fails fast with actionable remediation — it must NEVER let an
    environment failure silently degrade into a no-baseline pass. Returns
    ``(exit_code, problems, java_home)``; exit_code 0 means ready.

    *phase* is ``'a'`` (JVM + build/run) or ``'b'`` (JVM + SCOS client jar).
    """
    problems: list = []
    if not shutil.which("sbt"):
        problems.append(
            "sbt not on PATH — install sbt 1.x (the harness renders and runs "
            "ScalaTest specs via sbt).")

    java_home = _resolve_phase_a_jdk(allow_provision=True)
    if not java_home:
        current = os.environ.get("JAVA_HOME", "").strip() or "(default `java` on PATH)"
        cur_major = _java_major(current) if os.path.isdir(current) else None
        problems.append(
            "no Java 8/11/17 JDK available (Spark 3.5 does NOT support Java 21+). "
            f"Current JAVA_HOME={current}"
            + (f" is Java {cur_major}." if cur_major else ".")
            + " Install a JDK 17 (e.g. `apt-get install -y openjdk-17-jdk-headless`) "
              "or make Coursier reachable so the harness can auto-provision "
              "`temurin:17`.")

    if phase == "b":
        tests_dir = validation_root(conv_root) / "tests"
        if not _scos_client_jar_locatable(tests_dir, conv_root):
            problems.append(
                "SCOS Scala client jar (snowpark-connect-java-client) not found in "
                "tests/lib, Output/lib, ~/.m2, or the Coursier cache — Phase B would "
                "fail with ClassNotFoundException. Ensure the migrate build placed it "
                "in Output/lib.")

    return (0 if not problems else 3), problems, java_home


def _cmd_preflight(args) -> int:
    conv_root = Path(args.conv_root).expanduser().resolve()
    phase = (getattr(args, "phase", "a") or "a").lower()
    rc, problems, java_home = _preflight_checks(conv_root, phase)
    if rc != 0:
        preview = "\n".join(f"  - {p}" for p in problems)
        return _die(
            f"preflight FAILED — environment not ready for Phase {phase.upper()}; fix "
            "these before running (the harness will NOT silently produce a no-baseline "
            "pass):\n" + preview, rc)
    print(f"[scos-control] preflight OK (phase {phase}): JAVA_HOME={java_home}, sbt present")
    return 0


def _cmd_phase_reset(args) -> int:
    """Clear stale phase artefacts so the next phase starts clean.

    --to a  : wipe compiled test-classes + rendered specs
    --to b  : same wipe + optionally clear phase_a results (--clear-results) + reset tests_authored milestone
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    target_phase = args.to.lower()

    workspace = validation_root(conv_root)
    tests_dir = workspace / "tests"

    cleared: List[str] = []

    # 1. Clear tests/target/ (compiled sbt classes: prevents phase-A classpath leaking into phase-B)
    target_dir = tests_dir / "target"
    if target_dir.is_dir():
        shutil.rmtree(target_dir, ignore_errors=True)
        cleared.append("tests/target/")
        print(f"[scos-control] phase-reset: cleared {target_dir}")
    else:
        print(f"[scos-control] phase-reset: tests/target/ absent — skipping")

    # 2. Remove rendered spec files (phase_a/, phase_b/, and legacy flat scala/)
    cleared_specs = _clear_rendered_specs(tests_dir)
    if cleared_specs:
        cleared.extend(cleared_specs)
        print(f"[scos-control] phase-reset: removed rendered specs: {cleared_specs}")
    else:
        print("[scos-control] phase-reset: no rendered specs to clear")

    # 3. Optionally clear Phase A trial outputs (--to b + --clear-results only)
    if target_phase == "b" and getattr(args, "clear_results", False):
        phase_a_results = workspace / "results" / "phase_a"
        if phase_a_results.is_dir():
            shutil.rmtree(phase_a_results, ignore_errors=True)
            phase_a_results.mkdir(parents=True, exist_ok=True)
            cleared.append("results/phase_a/")
            print(f"[scos-control] phase-reset: cleared Phase A results ({phase_a_results})")

    # 4. Record phase_reset event in events.jsonl
    append_event(workspace, {
        "kind": "phase_reset",
        "to": target_phase,
        "cleared": cleared,
        "clear_results": getattr(args, "clear_results", False),
    })

    # 5. Reset tests_authored milestone so Phase B re-renders fresh specs
    try:
        state = load_state(conv_root)
        if (state.get("milestones") or {}).get("tests_authored"):
            state.setdefault("milestones", {})["tests_authored"] = False
            save_state(conv_root, state)
            print(f"[scos-control] phase-reset: milestone 'tests_authored' reset to False")
    except (FileNotFoundError, ValueError) as exc:
        print(f"[scos-control] phase-reset: could not update state.json ({exc}) — continuing",
              file=sys.stderr)

    print(f"[scos-control] phase-reset --to {target_phase} complete; cleared: {cleared or ['(nothing)']}")
    return 0


# ---------------------------------------------------------------------------
# prevalidate — single-pass static validation gate
# ---------------------------------------------------------------------------

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
    invalidates the cache even if the analysis shim was not refreshed yet.
    Fall back to ``analysis.json`` when schemas/ has not been mined.
    """
    import hashlib
    h = hashlib.sha256()
    sd = schemas_dir(conv_root)
    if sd.is_dir() and schemas_manifest_path(conv_root).is_file():
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
            for f in sorted(tree.rglob("*.scala")):
                try:
                    h.update(f.read_bytes())
                except OSError:
                    pass
            for bf in sorted(tree.glob("build.sbt")):
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
            fix_hint="Run 'sbt assembly' in Output/ before prevalidate.",
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
        if not any(f"{class_path}{suf}.class" in jar_entries
                   for suf in ("", "$", "Module$")):
            findings.append(_prevalidate_finding(
                "entry_class", "blocking",
                f"entrypoint_class {cls!r} not found in {jar.name}",
                phase="a|b",
                entrypoint=ep.get("id"),
                fix_hint=(
                    f"Update analysis.json entrypoints[id={ep.get('id')!r}].entrypoint_class"
                    " to the compiled class name exactly (include $ for companion objects)."
                ),
            ))
    return findings


def _check_io_completeness(conv_root: Path) -> List[dict]:
    """Grep Output/src for unpatched cloud / excel / mongo / file I/O blocking Phase B.

    PySpark parity: excel, mongo, and non-table file writes must be remapped to
    ``SCOS_SINK_*`` (stage capture) or ``saveAsTable`` before Phase B runs.
    """
    findings: List[dict] = []
    output_src = conv_root / "Output" / "src"
    if not output_src.is_dir():
        return findings

    _io_patterns: List[Tuple[str, str, str]] = [
        (
            r'"s3://|"s3a://|"gs://|"abfss://|"wasbs://|"wasb://|"hdfs://|"dbfs:/',
            "cloud_uri_literal",
            "Redirect via System.getProperty(\"SCOS_INPUT_*\") / SCOS_SINK_* or path_redirects.",
        ),
        (
            r'spark\.read\.format\("jdbc|\.option\("url"|\.option\("driver"|\.jdbc\(',
            "jdbc_read",
            "Phase B cannot reach external databases. Add an I/O patch.",
        ),
        (
            r'new SparkContext|sc\.textFile|sc\.hadoopFile|sc\.sequenceFile|sc\.objectFile',
            "rdd_io",
            "SparkContext / RDD I/O is incompatible with Spark Connect. Refactor to DataFrame API.",
        ),
        (
            r'com\.crealytics\.spark\.excel|format\(\s*"com\.crealytics\.spark\.excel"|'
            r'format\(\s*"excel"|format\(\s*"com\.crealytics',
            "excel_io",
            "Excel I/O is unsupported on SCOS. Remap to parquet/SCOS_SINK_* or saveAsTable.",
        ),
        (
            r'format\(\s*"mongo|format\(\s*"mongodb|com\.mongodb\.spark|'
            r'MongoSpark|spark\.mongodb',
            "mongo_io",
            "Mongo I/O is unsupported on SCOS. Remap to saveAsTable / SCOS_SINK_* capture.",
        ),
        (
            r'\.save\(\s*"|'\
            r'\.csv\(\s*"|\.json\(\s*"|\.parquet\(\s*"|\.orc\(\s*"|\.text\(\s*"',
            "literal_file_write",
            "Literal file writes fail on SCOS. Patch to System.getProperty(\"SCOS_SINK_<ID>\") "
            "(stage capture) or saveAsTable.",
        ),
    ]
    _suppress = re.compile(
        r'//\s*SCOS_INPUT|//\s*SCOS_SINK|System\.getProperty|//\s*TEST-PATCH|//\s*suppress',
        re.IGNORECASE,
    )

    for scala_file in sorted(output_src.rglob("*.scala")):
        try:
            lines = scala_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            if _suppress.search(line):
                continue
            for pattern, kind, hint in _io_patterns:
                if re.search(pattern, line):
                    findings.append(_prevalidate_finding(
                        "io_completeness", "blocking",
                        f"unpatched {kind} in Output/: "
                        f"{scala_file.relative_to(conv_root)}:{lineno}",
                        phase="b",
                        file=str(scala_file.relative_to(conv_root)),
                        line=lineno,
                        fix_hint=hint,
                        rebuild_required=True,
                    ))
                    break
    return findings


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
    # Concrete JSON literals are intended values for lift-json Args.* wrappers
    # (e.g. getStageProperties → "[]", getFileProperties → '[{"counts":0,...}]').
    # These must NOT be flagged as stubs even though the placeholder regex below
    # would otherwise match "[]" / "[...]". An ellipsis placeholder like "[{...}]"
    # is NOT valid JSON, so it correctly falls through to the stub check.
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


def _check_cli_args_completeness_pv(conv_root: Path, phase: str) -> List[dict]:
    """Block Phase A/B when cli_args / entrypoint_kwargs still contain stubs.

    Incomplete Args.* / empty CLI arrays are a top Flashfood failure class
    (TransformationException, ArrayIndexOutOfBounds). Mock/cli_args fixes do
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
                            "Fill cli_args with non-empty concrete values the Args.* / "
                            "main(Array[String]) path expects (paths, flags, non-empty arrays)."
                        ),
                        rebuild_required=False,
                    ))
            # Empty list when the entrypoint declares it expects CLI is a soft warning;
            # hard-block only when kwargs also empty and llm_todo mentions args.
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


def _check_intermediate_tables_pv(conv_root: Path, phase: str) -> List[dict]:
    """Require intermediate_tables to be declared with schema and present in schemas/.

    Phase A TABLE_OR_VIEW_NOT_FOUND on pipeline handoffs is almost always a missing
    intermediate CREATE/seed. schema_mine + seedEntrypoint must see typed columns.
    """
    findings: List[dict] = []
    try:
        analysis = json.loads(analysis_path(conv_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return findings

    intermediates = analysis.get("intermediate_tables") or []
    if not intermediates:
        return findings

    schemas_root = validation_root(conv_root) / "shared" / "schemas" / "entrypoints"
    schema_keys: set = set()
    if schemas_root.is_dir():
        for tf in schemas_root.rglob("*.json"):
            if tf.name.startswith("_"):
                continue
            try:
                doc = json.loads(tf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            key = (doc.get("_table_key") or tf.stem or "").lower()
            if key:
                schema_keys.add(key)
            orig = (doc.get("original_path") or "").lower()
            if orig:
                schema_keys.add(orig.split(".")[-1])

    for entry in intermediates:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        if not name:
            findings.append(_prevalidate_finding(
                "intermediate_tables", "blocking",
                "intermediate_tables[] entry missing name",
                phase=phase,
                fix_hint="Set name to the Spark table identifier the pipeline reads/writes.",
                rebuild_required=False,
            ))
            continue
        cols = entry.get("schema")
        if not isinstance(cols, list) or not cols:
            findings.append(_prevalidate_finding(
                "intermediate_tables", "blocking",
                f"intermediate '{name}' missing typed schema[] — cannot CREATE/seed",
                phase=phase,
                fix_hint=(
                    "Add columns to intermediate_tables[].schema, re-run schema_mine + "
                    "datagen so seedEntrypoint pre-creates the empty table."
                ),
                rebuild_required=False,
            ))
            continue
        bare = name.split(".")[-1].lower().replace("`", "").replace('"', "")
        if schemas_root.is_dir() and bare not in schema_keys and name.lower() not in schema_keys:
            findings.append(_prevalidate_finding(
                "intermediate_tables", "blocking",
                f"intermediate '{name}' not present in schemas/ after mine — re-run schema_mine",
                phase=phase,
                fix_hint="Run schema_mine.py then confirm schemas/entrypoints/*/tables/.",
                rebuild_required=False,
            ))
    return findings


def _check_sink_strategy_pv(conv_root: Path, phase: str) -> List[dict]:
    """Phase B: every non-table sink must declare kind + capture strategy.

    File/excel/mongo sinks are captured via SCOS_SINKS stage (PySpark parity) when
    patched to System.getProperty(\"SCOS_SINK_*\"); otherwise require saveAsTable remap.
    """
    if phase != "b":
        return []
    findings: List[dict] = []
    try:
        analysis = json.loads(analysis_path(conv_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return findings

    # Merge top-level "sinks" (legacy) + "external_sinks" (data-synthesizer) so a
    # per-entrypoint string-id reference resolves regardless of which global key the
    # producer used (parity with schema_mine's global merge).
    sink_by_id = {
        s.get("id"): s
        for s in (list(analysis.get("sinks") or []) + list(analysis.get("external_sinks") or []))
        if isinstance(s, dict) and s.get("id")
    }

    for ep in analysis.get("entrypoints") or []:
        if not isinstance(ep, dict):
            continue
        eid = ep.get("id") or "<unknown>"
        for raw in entrypoint_declared_sinks(ep):
            sink = raw if isinstance(raw, dict) else sink_by_id.get(raw)
            if not isinstance(sink, dict):
                # unresolved string id — analysis completeness already covers missing keys
                if isinstance(raw, str) and raw in sink_by_id:
                    sink = sink_by_id[raw]
                elif isinstance(raw, str):
                    continue
                else:
                    continue
            kind = (sink.get("kind") or "table").strip().lower()
            if kind in ("", "table"):
                continue
            sid = sink.get("id") or sink.get("name") or "<sink>"
            allow = (sink.get("allow_empty") or sink.get("allowEmpty") or "").strip()
            fmt = (sink.get("format") or kind).strip().lower()
            if kind in ("excel", "mongo", "mongodb") or fmt in ("excel", "mongo", "mongodb"):
                findings.append(_prevalidate_finding(
                    "sink_strategy", "blocking",
                    f"entrypoint '{eid}' sink '{sid}' kind={kind!r} is not SCOS-native",
                    phase="b",
                    entrypoint=eid,
                    fix_hint=(
                        "patch-add a migrated remap to saveAsTable / parquet(SCOS_SINK_*) "
                        "before Phase B; or set allow_empty with a short reason if intentional."
                        if not allow else
                        "allow_empty set — confirm Phase B skip is intentional."
                    ),
                    rebuild_required=not bool(allow),
                ))
                if allow:
                    findings[-1]["severity"] = "warning"
                    findings[-1]["rebuild_required"] = False
            elif not (sink.get("format") or kind in ("file", "parquet", "csv", "json", "orc", "text")):
                findings.append(_prevalidate_finding(
                    "sink_strategy", "warning",
                    f"entrypoint '{eid}' sink '{sid}' kind={kind!r} has no format — "
                    "stage capture defaults to parquet",
                    phase="b",
                    entrypoint=eid,
                    fix_hint="Set sinks[].format (parquet/csv/json) for file-sink capture.",
                ))
    return findings


def _check_cores_zero_pv(conv_root: Path, phase: str) -> List[dict]:
    """Detect local[0] / repartition(0) / coalesce(0) — known ArrayIndexOutOfBounds class."""
    findings: List[dict] = []
    trees = [conv_root / "Validation" / "source"]
    if phase == "b":
        trees.append(conv_root / "Output")
    patterns: List[Tuple[str, str]] = [
        (r"local\[\s*0\s*\]", "local[0] master"),
        (r"\.repartition\(\s*0\s*\)", "repartition(0)"),
        (r"\.coalesce\(\s*0\s*\)", "coalesce(0)"),
    ]
    _suppress = re.compile(r"//\s*TEST-PATCH|//\s*suppress", re.IGNORECASE)
    for tree in trees:
        if not tree.is_dir():
            continue
        for scala_file in sorted(tree.rglob("*.scala")):
            try:
                lines = scala_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, 1):
                if _suppress.search(line):
                    continue
                for pattern, label in patterns:
                    if re.search(pattern, line):
                        findings.append(_prevalidate_finding(
                            "cores_zero", "blocking" if phase == "a" else "warning",
                            f"{label} in {scala_file.relative_to(conv_root)}:{lineno}",
                            phase=phase,
                            file=str(scala_file.relative_to(conv_root)),
                            line=lineno,
                            fix_hint=(
                                "Replace with local[1]/repartition(1) or inject non-empty "
                                "cli_args that set a positive core/partition count."
                            ),
                            rebuild_required=True,
                        ))
                        break
    return findings


def _check_unsupported_constructs_pv(conv_root: Path, phase: str) -> List[dict]:
    """Translate analysis.json unsupported_constructs[] into phase-scoped findings.

    Severity rules:
      - phase_b_blocking=True constructs → blocking in Phase B, warning in Phase A
      - kind == "udf" is never Phase-B-blocking (often re-registerable); warning only
      - other non-blocking constructs → warning in both phases
    """
    findings: List[dict] = []
    try:
        analysis = json.loads(analysis_path(conv_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return findings
    for ep in analysis.get("entrypoints") or []:
        for uc in ep.get("unsupported_constructs") or []:
            if not isinstance(uc, dict):
                continue
            kind = (uc.get("kind") or "unknown").strip().lower()
            # Soften UDF: never treat as Phase-B hard-block even if AST flagged it.
            phase_b_blocking = bool(uc.get("phase_b_blocking", False)) and kind != "udf"
            severity = "blocking" if (phase == "b" and phase_b_blocking) else "warning"
            fix = (
                "Re-register or adapt the UDF for SCOS; treat as a warning until proven failing."
                if kind == "udf"
                else "Refactor to remove this unsupported construct before running Phase B."
            )
            findings.append(_prevalidate_finding(
                "unsupported_construct", severity,
                f"{uc.get('kind', 'unknown')}: {uc.get('detail', '')}",
                phase=phase,
                entrypoint=ep.get("id"),
                file=uc.get("file"),
                line=uc.get("line"),
                fix_hint=fix,
                rebuild_required=phase_b_blocking and kind in ("rdd_op", "external_io"),
            ))
    return findings


def _check_analysis_completeness_pv(conv_root: Path, phase: str) -> List[dict]:
    """Ensure every selected entrypoint has the fields Phase A/B runners need.

    When ``schemas/`` exists, regenerates the analysis shim first so checks
    reflect agent edits to ``_meta.json`` / tables (not a stale analysis.json).
    """
    findings: List[dict] = []
    try:
        if schemas_manifest_path(conv_root).is_file():
            analysis = load_analysis_prefer_schemas(conv_root)
        else:
            analysis = json.loads(analysis_path(conv_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, SystemExit, Exception) as exc:
        findings.append(_prevalidate_finding(
            "analysis_completeness", "blocking",
            f"analysis/schemas missing or unreadable: {exc}",
            phase=phase,
            fix_hint=(
                "Re-run schema_mine.py --conv-root (or data-synthesizer) so "
                "Validation/shared/schemas/ exists."
            ),
        ))
        return findings

    eps = analysis.get("entrypoints") or []
    if not eps:
        findings.append(_prevalidate_finding(
            "analysis_completeness", "blocking",
            "no entrypoints[] — nothing to validate (check schemas/manifest.json)",
            phase=phase,
            fix_hint="Run schema_mine.py --conv-root (or scope-entrypoints) first.",
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
                fix_hint="Set entrypoint_class to the compiled FQN (include $ for objects).",
            ))
        if not entry_method:
            findings.append(_prevalidate_finding(
                "analysis_completeness", "warning",
                f"entrypoint '{eid}' missing entrypoint_method (defaulting to main/run at runtime)",
                phase=phase,
                entrypoint=eid,
                fix_hint="Set entrypoint_method explicitly (usually 'main' or 'run').",
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
                        "Mine sinks from AST writes (re-run deep analysis / synthesizer). "
                        "Empty sinks[] is only valid when AST also has no writes — "
                        "do not treat an analysis gap as a no-sink smoke baseline."
                    ),
                    rebuild_required=False,
                ))
        todo = (ep.get("llm_todo") or "").strip()
        if todo:
            # Phase A also blocks when the todo is an incomplete-analysis class that
            # reliably causes harness loops (dynamic paths / unresolved args).
            phase_a_block = bool(_LLM_TODO_PHASE_A_BLOCK.search(todo))
            sev = "blocking" if (phase == "b" or phase_a_block) else "warning"
            findings.append(_prevalidate_finding(
                "analysis_completeness", sev,
                f"entrypoint '{eid}' has open llm_todo: {todo[:160]}",
                phase=phase,
                entrypoint=eid,
                fix_hint=(
                    "Resolve dynamic paths / cli_args / unresolved I/O in "
                    "schemas/entrypoints/<id>/_meta.json (and tables/) before Phase A."
                    if phase_a_block else
                    "Resolve or dismiss the llm_todo in schemas/ before Phase B."
                ),
                rebuild_required=False,
            ))
    return findings


def _phase_spec_dir(tests_dir: Path, phase: str) -> Path:
    """Per-phase rendered-spec directory (prevents A/B spec cross-contamination)."""
    label = "a" if str(phase).lower() in ("a", "phase_a", "source") else "b"
    return tests_dir / "src" / "test" / "scala" / f"phase_{label}"


def _clear_rendered_specs(tests_dir: Path, *, phases: Optional[List[str]] = None) -> List[str]:
    """Remove rendered *.scala specs from phase dirs and legacy flat scala/."""
    cleared: List[str] = []
    scala_root = tests_dir / "src" / "test" / "scala"
    targets: List[Path] = []
    if phases is None:
        targets = [scala_root / "phase_a", scala_root / "phase_b", scala_root]
    else:
        for p in phases:
            targets.append(_phase_spec_dir(tests_dir, p))
        targets.append(scala_root)

    seen: set = set()
    for d in targets:
        key = str(d)
        if key in seen:
            continue
        seen.add(key)
        if not d.is_dir():
            continue
        if d.name.startswith("phase_"):
            files = list(d.glob("*.scala"))
        else:
            files = [f for f in d.glob("*.scala") if f.is_file()]
        for sf in files:
            sf.unlink(missing_ok=True)
        if files:
            try:
                rel = str(d.relative_to(tests_dir))
            except ValueError:
                rel = str(d)
            cleared.append(f"{rel}/ ({len(files)} spec(s))")
    return cleared


def _check_scos_venv_pv(conv_root: Path) -> List[dict]:
    """Verify the SCOS Python venv can import snowpark_connect (Phase B PF-1)."""
    findings: List[dict] = []
    venv = os.environ.get("SNOWPARK_CONNECT_PYTHON_VENV", "")
    if not venv:
        skill_dir = os.environ.get("SKILL_DIRECTORY", "")
        if skill_dir:
            venv = str(Path(skill_dir).parent / ".venv")
    # Same candidate probe used by run-phase-b — finds the shared skill .venv
    # even when SKILL_DIRECTORY and SNOWPARK_CONNECT_PYTHON_VENV are both unset.
    if not venv:
        for candidate in [
            Path(__file__).resolve().parent.parent / ".venv",
            Path(__file__).resolve().parent.parent.parent / ".venv",
        ]:
            if (candidate / "bin" / "python3").exists():
                venv = str(candidate)
                break
    if not venv:
        findings.append(_prevalidate_finding(
            "scos_venv", "warning",
            "SNOWPARK_CONNECT_PYTHON_VENV not set — cannot verify SCOS venv (Phase B PF-1)",
            phase="b",
            fix_hint="Set SNOWPARK_CONNECT_PYTHON_VENV to the path of the SCOS Python venv.",
        ))
        return findings
    venv_python = Path(venv) / "bin" / "python"
    if not venv_python.exists():
        findings.append(_prevalidate_finding(
            "scos_venv", "blocking",
            f"SCOS venv python not found at {venv_python} — Phase B will fail at session start",
            phase="b",
            fix_hint=f"Re-create the venv: uv pip install --python {venv_python} snowpark-connect",
        ))
        return findings
    try:
        result = subprocess.run(
            [str(venv_python), "-c", "import snowpark_connect"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            findings.append(_prevalidate_finding(
                "scos_venv", "blocking",
                f"cannot import snowpark_connect from {venv_python}: "
                f"{result.stderr.strip() or result.stdout.strip()}",
                phase="b",
                fix_hint=f"uv pip install --python {venv_python} snowpark-connect",
            ))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        findings.append(_prevalidate_finding(
            "scos_venv", "warning",
            f"scos_venv check timed out or failed: {exc}", phase="b",
        ))
    return findings


def _run_sbt_compile_pv(conv_root: Path, phase: str) -> List[dict]:
    """Run 'sbt compile' in the phase-appropriate project dir; collect [error] lines."""
    findings: List[dict] = []
    project_dir = (conv_root / "Validation" / "source") if phase == "a" else (conv_root / "Output")
    if not project_dir.is_dir():
        return [_prevalidate_finding("sbt_compile", "warning",
                                     f"project dir not found for phase {phase}: {project_dir}")]
    has_build = any((project_dir / bf).exists() for bf in ("build.sbt", "pom.xml", "build.gradle"))
    if not has_build:
        return []
    if not shutil.which("sbt"):
        return [_prevalidate_finding("sbt_compile", "warning",
                                     "sbt not found on PATH — skipping compile check",
                                     fix_hint="Install sbt 1.x and ensure it is on PATH.")]
    try:
        result = subprocess.run(
            ["sbt", "--no-colors", "compile"],
            cwd=str(project_dir), capture_output=True, text=True, timeout=300,
        )
        combined = result.stdout + result.stderr
        for ln in combined.splitlines():
            if "[error]" in ln.lower():
                findings.append(_prevalidate_finding(
                    "sbt_compile", "blocking",
                    f"sbt compile error: {ln.strip()}",
                    phase=phase,
                    fix_hint="Fix the compilation error before proceeding.",
                    rebuild_required=True,
                ))
    except subprocess.TimeoutExpired:
        findings.append(_prevalidate_finding(
            "sbt_compile", "warning", "sbt compile timed out (300s) — skipping",
        ))
    except (FileNotFoundError, OSError) as exc:
        findings.append(_prevalidate_finding(
            "sbt_compile", "warning", f"sbt compile could not run: {exc}",
            fix_hint="Ensure sbt is on PATH.",
        ))
    return findings


def _cmd_prevalidate(args) -> int:
    """Aggregate all static validation checks into a single prevalidation report.

    Checks (all aggregated, never abort-on-first):
      1. JVM / SCOS client jar preflight (_preflight_checks).
      2. Analysis completeness (entrypoint_class, sources/sinks, open llm_todo).
      3. cli_args / entrypoint_kwargs completeness (stub values block; no rebuild).
      4. intermediate_tables declare+schema (+ present in schemas/).
      5. Schema mining + mock-data verify (_ensure_mock_data).
      6. column_check.py --conv-root --json.
      7. dep_check.py --conv-root.
      8. sbt compile (Validation/source/ for Phase A, Output/ for Phase B).
      9. Entry-class validation vs assembled JAR.
     10. cores=0 / repartition(0) static detect.
     11. Phase B only: SCOS venv + I/O completeness + sink strategy.
     12. unsupported_constructs[] from analysis.json (phase-scoped severity; UDFs warn-only).

    Caching: hash(schemas/ + source + patches) → if unchanged and
    report exists, print cached message and exit without re-running checks.

    Output: Validation/shared/prevalidation_report.json + stdout summary.
    Exit codes: 0 = all clear, 1 = blocking findings, 2 = warnings only.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    phase = (getattr(args, "phase", "a") or "a").lower()
    _scripts = Path(__file__).resolve().parent

    # Refresh JVM analysis shim from schemas/ before any gate that reads it.
    ensure_analysis_shim_from_schemas(conv_root)

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

    # 1. JVM + SCOS jar preflight
    _, pf_problems, _ = _preflight_checks(conv_root, phase)
    for p in pf_problems:
        all_findings.append(_prevalidate_finding("preflight", "blocking", p, phase=phase))

    # 2. Analysis completeness (schemas-first when schemas/ present)
    all_findings.extend(_check_analysis_completeness_pv(conv_root, phase))

    # 3. cli_args / kwargs stubs
    all_findings.extend(_check_cli_args_completeness_pv(conv_root, phase))

    # 4. intermediate tables
    all_findings.extend(_check_intermediate_tables_pv(conv_root, phase))

    # 5. Schema mining + mock-data verify (reuse _ensure_mock_data)
    _, mock_problems = _ensure_mock_data(conv_root)
    for p in mock_problems:
        all_findings.append(_prevalidate_finding("mock_data", "blocking", p, phase=phase))

    # 6. column_check.py
    b, w = _prevalidate_run_json_tool(
        [sys.executable, str(_scripts / "column_check.py"), "--conv-root", str(conv_root), "--json"],
        "column_check", phase,
    )
    all_findings.extend(b)
    all_findings.extend(w)

    # 7. dep_check.py
    b, w = _prevalidate_run_json_tool(
        [sys.executable, str(_scripts / "dep_check.py"), "--conv-root", str(conv_root)],
        "dep_check", phase,
    )
    all_findings.extend(b)
    all_findings.extend(w)

    # 8. sbt compile
    all_findings.extend(_run_sbt_compile_pv(conv_root, phase))

    # 9. Entry-class validation
    all_findings.extend(_check_entry_classes(conv_root))

    # 10. cores=0 / empty-partition static risks
    all_findings.extend(_check_cores_zero_pv(conv_root, phase))

    # 11. Phase B: SCOS venv + I/O completeness + sink strategy
    if phase == "b":
        all_findings.extend(_check_scos_venv_pv(conv_root))
        all_findings.extend(_check_io_completeness(conv_root))
        all_findings.extend(_check_sink_strategy_pv(conv_root, phase))

    # 12. Unsupported constructs
    all_findings.extend(_check_unsupported_constructs_pv(conv_root, phase))

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
    # Also read project/*.scala / project/*.sbt: many sbt projects define dependency
    # versions (including Spark) in a dedicated project/Dependencies.scala file.
    proj_dir = source_dir / "project"
    if proj_dir.is_dir():
        for f in sorted(proj_dir.glob("*.scala")) + sorted(proj_dir.glob("*.sbt")):
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
        #
        # SPARK 3.5.x CONSTRAINT: Spark 3.5.x JARs are compiled with Scala 2.12.18.
        # The Scala 2.12.x LambdaDeserializer has a bug (fixed in 2.12.18) that causes
        #   java.lang.IllegalArgumentException: too many arguments
        #   at LambdaMetafactory.altMetafactory
        # when the harness reads Parquet files during seeding (Helpers.scala).
        # If the workload declares an older 2.12.x (e.g. 2.12.13), using that version
        # for the harness scala-library makes the seeding step crash. We clamp to
        # 2.12.18 minimum when the workload targets Spark 3.5.x so the harness
        # scala-library matches Spark's own compiled lambda format.
        effective_scala_ver = scala_ver
        if spark_ver:
            _minor = ".".join(spark_ver.split(".")[:2])
            _spark35_min_scala = "2.12.18"
            if _minor >= "3.5" and scala_ver.startswith("2.12") and scala_ver < _spark35_min_scala:
                effective_scala_ver = _spark35_min_scala
                print(f"[scos-control] clamped SCOS_KIT_SCALA_VERSION {scala_ver} -> "
                      f"{_spark35_min_scala} (Spark {spark_ver} requires >= {_spark35_min_scala} "
                      f"to avoid LambdaDeserializer capture-variable bug)")
        else:
            # spark_ver not detected from build files: the kit defaults to Spark 3.5.x,
            # so apply the same 2.12.18 floor unconditionally when the workload uses
            # an older Scala 2.12 patch that has the LambdaDeserializer bug.
            _spark35_min_scala = "2.12.18"
            if scala_ver.startswith("2.12") and scala_ver < _spark35_min_scala:
                effective_scala_ver = _spark35_min_scala
                print(f"[scos-control] clamped SCOS_KIT_SCALA_VERSION {scala_ver} -> "
                      f"{_spark35_min_scala} (kit default Spark 3.5.x requires >= {_spark35_min_scala} "
                      f"to avoid LambdaDeserializer capture-variable bug; spark version not detected)")
        env["SCOS_KIT_SCALA_VERSION"] = effective_scala_ver
    if env:
        print(f"[scos-control] aligning kit to source versions for Phase A: {env}")
    return env


def _is_fat_jar(jar_path: str) -> bool:
    """True when the jar name looks like an assembly/shadow/uber/fat artifact."""
    name = Path(jar_path).name.lower()
    return any(k in name for k in ("assembly", "shadow", "uber", "fat", "-all"))


# Jar basename prefixes that collide with the kit's local Spark/Delta and must
# stay off EXTRA_CLASSPATH (same class of bug as spark-connect-client-jvm on
# Phase A's tests/lib/).
_CLASSPATH_EXCLUDE_SUBSTRINGS = (
    "spark-", "hadoop-", "delta-core", "delta-spark", "delta-storage",
    "hive-", "orc-core", "orc-mapreduce", "parquet-hadoop",
    "scala-library-", "scala-reflect-", "scala-compiler-",
)
# Maven/Ivy path segments that identify colliding GAVs even when the basename
# is shaded or version-suffixed oddly (e.g. .../org/apache/spark/...).
_CLASSPATH_EXCLUDE_PATH_SEGMENTS = (
    "/org/apache/spark/", "/org/apache/hadoop/", "/org/apache/hive/",
    "/io/delta/", "/org/apache/orc/", "/org/apache/parquet/",
    "/scala/scala-library/", "/scala/scala-reflect/", "/scala/scala-compiler/",
)


def _classpath_entry_excluded(entry: str) -> Tuple[bool, str]:
    """Return ``(excluded, reason)`` for one classpath entry."""
    lower = entry.lower().replace("\\", "/")
    base = Path(entry).name.lower()
    if "/provided/" in lower:
        return True, "provided-scope"
    if any(s in base for s in _CLASSPATH_EXCLUDE_SUBSTRINGS):
        return True, "basename-prefix"
    if base.startswith("delta-") and "deequ" not in base:
        return True, "delta-basename"
    if any(seg in lower for seg in _CLASSPATH_EXCLUDE_PATH_SEGMENTS):
        return True, "gav-path"
    return False, ""


def _filter_dependency_classpath(
    raw: str, *, detail: Optional[dict] = None,
) -> str:
    """Drop Spark/Delta/Hadoop/provided jars that collide with the kit classpath.

    When *detail* is a dict, fill ``kept`` / ``dropped`` lists of
    ``{path, reason?}`` for build-doctor JSON logging.
    """
    if not raw or not raw.strip():
        if detail is not None:
            detail["kept"] = []
            detail["dropped"] = []
        return ""
    sep = os.pathsep
    kept: List[str] = []
    kept_meta: List[dict] = []
    dropped_meta: List[dict] = []
    for entry in raw.split(sep):
        entry = entry.strip()
        if not entry:
            continue
        excluded, reason = _classpath_entry_excluded(entry)
        if excluded:
            dropped_meta.append({"path": entry, "reason": reason})
            continue
        kept.append(entry)
        kept_meta.append({"path": entry})
    if detail is not None:
        detail["kept"] = kept_meta
        detail["dropped"] = dropped_meta
    return sep.join(kept)


def _looks_like_classpath_line(line: str) -> bool:
    """sbt `export` noise filter: last line that looks like a real classpath."""
    s = line.strip()
    if ".jar" not in s.lower():
        return False
    # Multi-entry classpath, or a single absolute/relative jar path.
    return (os.pathsep in s) or s.endswith(".jar") or ".jar" + os.pathsep in s


def _export_dependency_classpath(
    source_dir: Path, build_tool: str, java_home: str = "",
    log_path: Optional[Path] = None,
    filter_detail: Optional[dict] = None,
    force_rebuild: bool = False,
) -> str:
    """Export the workload's runtime dependency classpath (minus Spark/Delta/Hadoop).

    Used when a Phase A/B build yields a thin jar so ReflectionEntrypoint /
    the subprocess can still resolve workload deps. Logs raw + filtered
    output to *log_path* (default: ``scos_source_build.log``).

    Speed 3 — Cache: the raw classpath is written to
    ``<source_dir>/scos_runtime_classpath.txt`` after every successful export.
    On subsequent calls the cache is returned directly when it is newer than all
    build definition files (build.sbt, pom.xml, build.gradle*, lock files).
    Pass ``force_rebuild=True`` to bypass the cache.
    """
    log_path = log_path or (source_dir / "scos_source_build.log")
    build_env = _apply_jdk_to_env(dict(os.environ), java_home) if java_home else dict(os.environ)
    raw = ""
    sep = os.pathsep

    def _append_log(msg: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(msg)
        except OSError:
            pass

    # Speed 3: return cached classpath when build files are unchanged.
    cache_file = source_dir / "scos_runtime_classpath.txt"
    if not force_rebuild and cache_file.is_file():
        _build_lock_names = [
            "build.sbt", "pom.xml", "build.gradle", "build.gradle.kts",
            "project/plugins.sbt", "pom.lock", "gradle.lockfile",
            "build.gradle.kts.lock",
        ]
        cache_mtime = cache_file.stat().st_mtime
        freshest = max(
            (p.stat().st_mtime for name in _build_lock_names
             for p in [(source_dir / name)] if p.is_file()),
            default=0.0,
        )
        if freshest > 0.0 and cache_mtime >= freshest:
            cached_raw = cache_file.read_text(encoding="utf-8").strip()
            if cached_raw:
                print("[scos-control] using cached classpath (build file unchanged)")
                _append_log(f"\n=== cached classpath from {cache_file} ===\n{cached_raw}\n")
                detail: dict = {}
                filtered_cached = _filter_dependency_classpath(cached_raw, detail=detail)
                if filter_detail is not None:
                    filter_detail.update(detail)
                return filtered_cached

    if build_tool == "sbt" and shutil.which("sbt"):
        cmd = ["sbt", "-batch", "--error", "export runtime:fullClasspath"]
        _append_log(f"\n=== {' '.join(cmd)} ===\n")
        r = subprocess.run(cmd, cwd=str(source_dir), capture_output=True, text=True,
                           env=build_env)
        _append_log(r.stdout or "")
        if r.stderr:
            _append_log(r.stderr)
        # Prefer the last classpath-looking stdout line (sbt prints banners first).
        for line in reversed((r.stdout or "").splitlines()):
            if _looks_like_classpath_line(line):
                raw = line.strip()
                break
        _append_log(f"\n=== parsed classpath ({len(raw)} chars) ===\n{raw}\n")
    elif build_tool == "mvn" and shutil.which("mvn"):
        out_file = source_dir / "scos_runtime_classpath.txt"
        cmd = ["mvn", "-q", "-DincludeScope=runtime",
               f"-Dmdep.outputFile={out_file}", "dependency:build-classpath"]
        _append_log(f"\n=== {' '.join(cmd)} ===\n")
        r = subprocess.run(cmd, cwd=str(source_dir), capture_output=True, text=True,
                           env=build_env)
        _append_log((r.stdout or "") + (r.stderr or ""))
        if out_file.is_file():
            try:
                raw = out_file.read_text(encoding="utf-8").strip()
            except OSError:
                raw = ""
        _append_log(f"\n=== parsed classpath ({len(raw)} chars) ===\n{raw}\n")
    elif build_tool == "gradle":
        gradle = "./gradlew" if (source_dir / "gradlew").is_file() else (
            "gradle" if shutil.which("gradle") else "")
        if gradle:
            # Write a throwaway init script that prints runtimeClasspath as a
            # pathsep-joined absolute path list (usable EXTRA_CLASSPATH).
            init_script = source_dir / "scos_print_classpath.init.gradle"
            init_body = (
                "allprojects {\n"
                "  tasks.register('scosPrintRuntimeClasspath') {\n"
                "    doLast {\n"
                "      def cfg = configurations.findByName('runtimeClasspath')\n"
                "      if (cfg == null) cfg = configurations.findByName('runtime')\n"
                "      if (cfg == null) cfg = configurations.findByName('compileClasspath')\n"
                "      if (cfg != null) {\n"
                "        try {\n"
                "          def paths = cfg.resolve().collect { it.absolutePath }\n"
                "          println(paths.join(File.pathSeparator))\n"
                "        } catch (Exception e) {\n"
                "          System.err.println('scosPrintRuntimeClasspath: ' + e.message)\n"
                "        }\n"
                "      }\n"
                "    }\n"
                "  }\n"
                "}\n"
            )
            try:
                init_script.write_text(init_body, encoding="utf-8")
            except OSError as exc:
                _append_log(f"\n=== gradle init script write failed: {exc} ===\n")
                init_script = None
            if init_script is not None:
                cmd = [gradle, "-q", "-I", str(init_script), "scosPrintRuntimeClasspath"]
                _append_log(f"\n=== {' '.join(cmd)} ===\n")
                r = subprocess.run(cmd, cwd=str(source_dir), capture_output=True,
                                   text=True, env=build_env)
                _append_log((r.stdout or "") + (r.stderr or ""))
                for line in reversed((r.stdout or "").splitlines()):
                    if _looks_like_classpath_line(line):
                        raw = line.strip()
                        break
                _append_log(f"\n=== parsed classpath ({len(raw)} chars) ===\n{raw}\n")
                try:
                    init_script.unlink(missing_ok=True)
                except OSError:
                    pass
    else:
        _append_log(f"\n=== export classpath skipped (build_tool={build_tool!r}) ===\n")

    # Speed 3: persist the raw (pre-filter) classpath for sbt/gradle so the next
    # call can skip the 30-s–3-min CP export when build files are unchanged.
    # mvn already writes scos_runtime_classpath.txt via -Dmdep.outputFile above.
    if raw and build_tool not in ("mvn",):
        try:
            cache_file.write_text(raw, encoding="utf-8")
        except OSError:
            pass

    detail: dict = {}
    filtered = _filter_dependency_classpath(raw, detail=detail)
    if filter_detail is not None:
        filter_detail.update(detail)
    n_kept = len(detail.get("kept") or [])
    n_drop = len(detail.get("dropped") or [])
    _append_log(
        f"\n=== filtered classpath ({n_kept} kept, {n_drop} dropped) ===\n"
        f"{filtered}\n"
    )
    if n_drop:
        sample = ", ".join(
            Path(d["path"]).name for d in (detail.get("dropped") or [])[:8]
        )
        _append_log(f"=== dropped sample: {sample} ===\n")
    if filtered:
        print(f"[scos-control] exported dependency classpath: "
              f"{n_kept} jar(s) kept, {n_drop} Spark/Delta/Hadoop filtered")
    else:
        print("[scos-control] WARNING: dependency classpath export empty after "
              f"filtering (see {log_path})", file=sys.stderr)
    return filtered


def _classify_build_failure(log_path: Path) -> Tuple[str, str]:
    """Map a source-build log to ``(cause, remediation)`` for build-doctor / _die."""
    text = ""
    try:
        if log_path.is_file():
            text = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    lower = text.lower()
    if not text.strip():
        return (
            "no-build-tool-or-empty-log",
            "Ensure sbt/mvn/gradle is on PATH and Validation/source/ has a build file; "
            "re-run `scos_state.py build-doctor`.",
        )
    if "unresolved dependency" in lower or "could not find artifact" in lower \
            or "unresolved module" in lower or re.search(r"not found:\s+\S+#\S+", text):
        # Detect the specific snowpark-connect-java-client POM defect where Maven
        # Central publishes a POM with an unsubstituted ${scala.binary.version} in
        # the artifact filename, causing Coursier to request a non-existent URL.
        if "snowpark-connect-java-client" in lower and r"${scala.binary.version}" in text:
            return (
                "scos-client-pom-defect",
                "Maven Central POM for snowpark-connect-java-client has unsubstituted "
                "${scala.binary.version} in the artifact filename. "
                "Auto-fix: copy the JAR to Output/lib/ as an unmanaged dependency and "
                "comment out the managed libraryDependencies entry in build.sbt. "
                "scos_state.py will attempt this automatically on the next build.",
            )
        return (
            "unresolved-dependency",
            "Fix missing/unresolvable dependencies in the source build file "
            "(check repositories / credentials), then re-run build-doctor.",
        )
    if "[error]" in lower and (
        "compil" in lower
        or "not found: value" in lower
        or "not found: type" in lower
        or "is already defined" in lower
        or "overflow" in lower
    ):
        return (
            "compile-error",
            "Fix compile errors in Validation/source/ (see scos_source_build.log), "
            "then re-run build-doctor.",
        )
    if "no usable build tool" in lower or ("build.sbt" not in lower and "pom.xml" not in lower
                                           and "no build" in lower):
        return (
            "no-build-tool",
            "Validation/source/ needs build.sbt, pom.xml, or build.gradle — "
            "confirm --original-source pointed at the project root.",
        )
    return (
        "build-failed",
        f"Inspect {log_path} for the root error; prefer `sbt assembly` / fat jar, "
        "or thin jar + exported runtime classpath.",
    )


_SBT_MIN_ARM64 = (1, 9, 0)
_SBT_ARM64_TARGET = "1.9.9"
# Known public Maven/Gradle/Sonatype hosts — resolvers pointing elsewhere are private
_PUBLIC_RESOLVER_HOSTS = frozenset({
    "repo1.maven.org", "central.maven.org",
    "oss.sonatype.org", "s01.oss.sonatype.org",
    "plugins.gradle.org", "dl.bintray.com",
    "packages.confluent.io", "jcenter.bintray.com",
    "maven.google.com", "dl.google.com",
    "clojars.org", "repo.hortonworks.com",
    "repo.scala-sbt.org",
})


def _ensure_sbt_arm64_compatible(project_dir: Path) -> bool:
    """On arm64 hosts, upgrade sbt < 1.9 to 1.9.9 in project/build.properties.

    sbt 1.4.x and earlier ship a JNA version built only for x86_64; it fails with
    UnsatisfiedLinkError on Apple Silicon and aarch64 Linux.  sbt 1.9.x+ bundles a
    fat JNA that includes the arm64 slice.

    Returns True when the file was rewritten, False otherwise.
    """
    machine = platform.machine().lower()
    if machine not in ("arm64", "aarch64"):
        return False
    props_path = project_dir / "project" / "build.properties"
    if not props_path.is_file():
        return False
    text = props_path.read_text(encoding="utf-8")
    m = re.search(r"sbt\.version\s*=\s*(\d+)\.(\d+)\.(\d+)", text)
    if not m:
        return False
    current = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if current >= _SBT_MIN_ARM64:
        return False
    new_text = re.sub(
        r"(sbt\.version\s*=\s*)\S+",
        lambda mo: mo.group(1) + _SBT_ARM64_TARGET,
        text,
    )
    props_path.write_text(new_text, encoding="utf-8")
    print(f"[scos-control] arm64: upgraded sbt {'.'.join(map(str, current))} → "
          f"{_SBT_ARM64_TARGET} in {props_path.relative_to(project_dir)} "
          f"(JNA arm64 compatibility fix)")
    return True


def _strip_private_resolvers(project_dir: Path) -> List[str]:
    """Comment out private/corporate Maven resolvers in build.sbt that cannot be
    reached outside the customer's network.

    Detects ``resolvers +=`` / ``resolvers ++=`` lines whose URL host is not in
    ``_PUBLIC_RESOLVER_HOSTS`` and prepends ``// SCOS-STRIPPED: `` so sbt skips
    them while keeping the original text visible for audit.

    Returns the list of hostnames that were stripped.
    """
    build_sbt = project_dir / "build.sbt"
    if not build_sbt.is_file():
        return []
    text = build_sbt.read_text(encoding="utf-8")
    stripped: List[str] = []
    new_lines: List[str] = []
    for line in text.splitlines(keepends=True):
        stripped_line = line.lstrip()
        if stripped_line.startswith("// SCOS-STRIPPED:"):
            new_lines.append(line)
            continue
        if not re.search(r"resolvers\s*\+\+?=", stripped_line):
            new_lines.append(line)
            continue
        hosts = re.findall(r'https?://([^/"]+)', line)
        private = [h for h in hosts if h not in _PUBLIC_RESOLVER_HOSTS]
        if not private:
            new_lines.append(line)
            continue
        indent = line[: len(line) - len(stripped_line)]
        new_lines.append(f"{indent}// SCOS-STRIPPED: {stripped_line}")
        stripped.extend(private)
    if stripped:
        build_sbt.write_text("".join(new_lines), encoding="utf-8")
        print(f"[scos-control] stripped {len(stripped)} private resolver(s) from "
              f"build.sbt: {', '.join(stripped)}")
    return stripped


def _fix_scos_client_pom(project_dir: Path, conv_root: Path) -> bool:
    """Auto-fix the snowpark-connect-java-client Maven Central POM defect.

    The 1.0.0 POM on Maven Central has an unsubstituted ``${scala.binary.version}``
    in the artifact filename URL, causing Coursier to request a non-existent path.

    Fix strategy:
    1. Locate the JAR in (priority order): tests/lib/, Output/lib/ (already fixed),
       ~/.m2/repository, Coursier cache.
    2. Copy it to ``project_dir/lib/`` as an unmanaged dependency.
    3. Comment out the managed ``libraryDependencies += … snowpark-connect-java-client …``
       line in ``project_dir/build.sbt``.

    Returns True when both the JAR was staged and build.sbt was patched.
    """
    lib_dir = project_dir / "lib"
    # If already fixed (JAR already in lib/), nothing to do.
    if list(lib_dir.glob("snowpark-connect-java-client*.jar")):
        return True

    # Search order for the JAR.
    search_dirs = [
        conv_root / "Validation" / "tests" / "lib",
        project_dir / "lib",
        Path.home() / ".m2" / "repository" / "com" / "snowflake" / "snowpark-connect-java-client_2.12",
        Path.home() / ".m2" / "repository" / "com" / "snowflake" / "snowpark-connect-java-client_2.13",
        Path.home() / ".cache" / "coursier" / "v1" / "https" / "repo1.maven.org" / "maven2" / "com" / "snowflake",
        Path.home() / "Library" / "Caches" / "Coursier" / "v1" / "https" / "repo1.maven.org" / "maven2" / "com" / "snowflake",
    ]
    found_jars: List[Path] = []
    for base in search_dirs:
        if base.is_dir():
            found_jars.extend(j for j in base.rglob("snowpark-connect-java-client*.jar")
                              if "sources" not in j.name and "javadoc" not in j.name)
    if not found_jars:
        print("[scos-control] WARNING: SCOS client JAR not found for POM fix — "
              "cannot auto-fix; download snowpark-connect-java-client manually "
              "and place it in Output/lib/", file=sys.stderr)
        return False
    src_jar = max(found_jars, key=lambda j: j.stat().st_mtime)
    lib_dir.mkdir(parents=True, exist_ok=True)
    dest = lib_dir / src_jar.name
    shutil.copy2(src_jar, dest)
    print(f"[scos-control] POM fix: staged SCOS client jar -> lib/{dest.name}")

    # Comment out the managed dep line(s) in build.sbt.
    build_sbt = project_dir / "build.sbt"
    if build_sbt.is_file():
        sbt_text = build_sbt.read_text(encoding="utf-8")
        patched = re.sub(
            r'^([^\n]*libraryDependencies[^\n]*snowpark-connect-java-client[^\n]*)',
            r'// SCOS-POM-FIX: \1',
            sbt_text,
            flags=re.MULTILINE,
        )
        if patched != sbt_text:
            build_sbt.write_text(patched, encoding="utf-8")
            print("[scos-control] POM fix: commented out managed snowpark-connect-java-client "
                  "dep in build.sbt (unmanaged lib/ jar takes precedence)")
    return True


def _build_source_jar(
    source_dir: Path, java_home: str = "", force_rebuild: bool = False,
) -> dict:
    """Build the ORIGINAL source workload into a runnable jar for Phase A (local Spark).

    Speed 2 — Prefer resolve over unconditional rebuild:
    Before running the build ladder, check whether a usable jar already exists:
    * Fat jar  → return immediately (no build needed).
    * Thin jar → check whether the cached classpath (Speed 3) is fresh; if so
      return immediately.  Only rebuild when no jar found, ``force_rebuild=True``,
      or the jar is older than key source files.

    Ladder: assembly/shadow (fat) → package/compile + export dependency classpath.
    Thin jar + non-empty filtered classpath is a valid Phase A input (``ok=True``).

    Returns a dict::
        {jar, extra_classpath, build_tool, ok, log_path}

    * ``ok`` is True for a fat jar, or a thin jar with a non-empty extra classpath.
    * Hard-die at the caller only when ``jar`` is empty (no artifact at all).
    * Raises no exception on build failure — the caller records / classifies it.
    """
    empty = {"jar": "", "extra_classpath": "", "build_tool": "", "ok": False, "log_path": ""}
    if not source_dir.is_dir():
        return empty

    is_sbt = (source_dir / "build.sbt").is_file()
    is_maven = (source_dir / "pom.xml").is_file()
    is_gradle = (source_dir / "build.gradle").is_file() or (source_dir / "build.gradle.kts").is_file()
    if is_sbt:
        build_tool = "sbt"
    elif is_maven:
        build_tool = "mvn"
    elif is_gradle:
        build_tool = "gradle"
    else:
        build_tool = ""

    log_path = source_dir / "scos_source_build.log"
    empty = {**empty, "build_tool": build_tool, "log_path": str(log_path)}
    build_env = _apply_jdk_to_env(dict(os.environ), java_home) if java_home else None

    # Pre-build fixes: arm64 sbt version + private resolver strip (idempotent).
    if build_tool == "sbt":
        _ensure_sbt_arm64_compatible(source_dir)
    _strip_private_resolvers(source_dir)

    # Speed 2: skip the build ladder when a usable jar already exists AND no
    # source file is newer than the jar (avoids stale-jar silently passing tests).
    if not force_rebuild:
        existing = _find_built_jar(source_dir)
        if existing:
            jar_mtime = Path(existing).stat().st_mtime
            freshest_src = max(
                (p.stat().st_mtime for p in source_dir.rglob("*.scala")),
                default=0.0,
            )
            if freshest_src > jar_mtime:
                print(f"[scos-control] source file(s) newer than cached jar — "
                      f"forcing rebuild of {source_dir.name}")
                existing = ""  # fall through to build ladder
        if existing and _is_fat_jar(existing):
            print(f"[scos-control] reusing existing fat source jar (skipping rebuild): {existing}")
            return {
                "jar": existing, "extra_classpath": "", "build_tool": build_tool,
                "ok": True, "log_path": str(log_path),
            }
        if existing and build_tool:
            # Thin jar: return immediately when cached classpath is still fresh.
            cached_cp = _export_dependency_classpath(
                source_dir, build_tool, java_home=java_home or "", log_path=log_path)
            if cached_cp:
                print(f"[scos-control] reusing existing thin source jar + cached classpath "
                      f"(skipping rebuild): {existing}")
                return {
                    "jar": existing, "extra_classpath": cached_cp, "build_tool": build_tool,
                    "ok": True, "log_path": str(log_path),
                }

    def _run(cmd: list) -> int:
        print(f"[scos-control] building source jar: {' '.join(cmd)} (cwd={source_dir})")
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n=== {' '.join(cmd)} ===\n")
            r = subprocess.run(cmd, cwd=str(source_dir), stdout=lf,
                               stderr=subprocess.STDOUT, env=build_env)
        return r.returncode

    if not build_tool:
        print(f"[scos-control] WARNING: no usable build tool for source at {source_dir} "
              "(need sbt/mvn/gradle) — Phase A cannot produce a baseline", file=sys.stderr)
        try:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write("\n=== no usable build tool (need sbt/mvn/gradle) ===\n")
        except OSError:
            pass
        return empty

    ran_package = False
    _pom_fix_attempted = False
    if build_tool == "sbt" and shutil.which("sbt"):
        # Try assembly first (needs sbt-assembly plugin); fall back to package.
        if _run(["sbt", "-batch", "assembly"]) != 0:
            # Check for the SCOS client POM defect and auto-fix once.
            cause, _ = _classify_build_failure(log_path)
            if cause == "scos-client-pom-defect" and not _pom_fix_attempted:
                _pom_fix_attempted = True
                conv_root_guess = source_dir.parent  # Output/ → conv_root or source/ → Validation/ → conv_root
                for candidate in (source_dir.parent, source_dir.parent.parent,
                                  source_dir.parent.parent.parent):
                    if (candidate / "Validation").is_dir():
                        conv_root_guess = candidate
                        break
                if _fix_scos_client_pom(source_dir, conv_root_guess):
                    print("[scos-control] POM fix applied — retrying sbt assembly")
                    if _run(["sbt", "-batch", "assembly"]) == 0:
                        _pom_fix_attempted = False  # success, skip package fallback
                    else:
                        _run(["sbt", "-batch", "package"])
                        ran_package = True
                else:
                    _run(["sbt", "-batch", "package"])
                    ran_package = True
            else:
                _run(["sbt", "-batch", "package"])
                ran_package = True
    elif build_tool == "mvn" and shutil.which("mvn"):
        _run(["mvn", "-q", "-DskipTests", "package"])
        ran_package = True
    elif build_tool == "gradle":
        gradle = "./gradlew" if (source_dir / "gradlew").is_file() else (
            "gradle" if shutil.which("gradle") else "")
        if not gradle:
            print(f"[scos-control] WARNING: gradle not found for {source_dir}",
                  file=sys.stderr)
            return empty
        if _run([gradle, "shadowJar"]) != 0:
            _run([gradle, "assemble"])
            ran_package = True
    else:
        print(f"[scos-control] WARNING: {build_tool} not on PATH for {source_dir}",
              file=sys.stderr)
        return empty

    jar = _find_built_jar(source_dir)
    extra_cp = ""

    # If assembly "succeeded" but only a thin jar exists (or assembly was skipped
    # via package fallback), export the runtime dependency classpath.
    if jar and _is_fat_jar(jar):
        print(f"[scos-control] built fat source jar: {jar}")
        return {
            "jar": jar, "extra_classpath": "", "build_tool": build_tool,
            "ok": True, "log_path": str(log_path),
        }

    if jar and not ran_package and build_tool == "sbt" and shutil.which("sbt"):
        # Assembly produced a non-fat jar (unusual) — still package for a clean thin jar.
        _run(["sbt", "-batch", "package"])
        jar = _find_built_jar(source_dir) or jar

    if jar and not _is_fat_jar(jar):
        extra_cp = _export_dependency_classpath(
            source_dir, build_tool, java_home=java_home or "", log_path=log_path)
        ok = bool(extra_cp)
        if ok:
            print(f"[scos-control] thin source jar + classpath OK: {jar}")
        else:
            print(f"[scos-control] WARNING: thin source jar without dependency "
                  f"classpath: {jar} (see {log_path})", file=sys.stderr)
        return {
            "jar": jar, "extra_classpath": extra_cp, "build_tool": build_tool,
            "ok": ok, "log_path": str(log_path),
        }

    if jar:
        print(f"[scos-control] built source jar: {jar}")
        return {
            "jar": jar, "extra_classpath": "", "build_tool": build_tool,
            "ok": True, "log_path": str(log_path),
        }

    print(f"[scos-control] WARNING: source build produced no jar (see {log_path}); "
          "Phase A baseline will be unavailable", file=sys.stderr)
    return {
        "jar": "", "extra_classpath": "", "build_tool": build_tool,
        "ok": False, "log_path": str(log_path),
    }


def _detect_build_tool(project_dir: Path) -> str:
    if (project_dir / "build.sbt").is_file():
        return "sbt"
    if (project_dir / "pom.xml").is_file():
        return "mvn"
    if (project_dir / "build.gradle").is_file() or (project_dir / "build.gradle.kts").is_file():
        return "gradle"
    return ""


def _resolve_workload_artifact(
    project_dir: Path,
    java_home: str = "",
    preferred_jar: str = "",
    *,
    allow_build: bool = True,
    filter_detail: Optional[dict] = None,
    force_rebuild: bool = False,
) -> dict:
    """Resolve a fat/thin workload jar + optional dependency classpath for a project dir.

    Prefer an existing *preferred_jar* (e.g. ``analysis.json["jar_path"]``) when present;
    otherwise find a built jar under ``target/``. Thin jars get a filtered runtime
    classpath export. When nothing usable exists and *allow_build* is True, run the
    full ``_build_source_jar`` ladder.
    Pass ``force_rebuild=True`` to bypass Speed-2 jar reuse and Speed-3 classpath cache.
    """
    empty = {"jar": "", "extra_classpath": "", "build_tool": "", "ok": False, "log_path": ""}
    if not project_dir.is_dir():
        return empty

    build_tool = _detect_build_tool(project_dir)
    log_path = project_dir / "scos_source_build.log"
    empty = {**empty, "build_tool": build_tool, "log_path": str(log_path)}

    # Pre-build fixes: arm64 sbt version + private resolver strip (idempotent).
    if build_tool == "sbt":
        _ensure_sbt_arm64_compatible(project_dir)
    _strip_private_resolvers(project_dir)

    candidates: List[str] = []
    if preferred_jar and Path(preferred_jar).is_file():
        candidates.append(str(Path(preferred_jar).resolve()))
    found = _find_built_jar(project_dir)
    if found and found not in candidates:
        candidates.append(found)

    for jar in candidates:
        # Skip cached jar when source files are newer — forces a rebuild below.
        if not force_rebuild:
            jar_mtime = Path(jar).stat().st_mtime
            freshest_src = max(
                (p.stat().st_mtime for p in project_dir.rglob("*.scala")),
                default=0.0,
            )
            if freshest_src > jar_mtime:
                print(f"[scos-control] source file(s) newer than cached jar — "
                      f"forcing rebuild of {project_dir.name}")
                break  # fall through to allow_build path
        if _is_fat_jar(jar):
            print(f"[scos-control] resolved fat jar: {jar}")
            return {
                "jar": jar, "extra_classpath": "", "build_tool": build_tool,
                "ok": True, "log_path": str(log_path),
            }
        # Thin jar — export dependency classpath without rebuilding.
        if build_tool:
            extra_cp = _export_dependency_classpath(
                project_dir, build_tool, java_home=java_home or "",
                log_path=log_path, filter_detail=filter_detail,
                force_rebuild=force_rebuild)
            ok = bool(extra_cp)
            if ok:
                print(f"[scos-control] resolved thin jar + classpath: {jar}")
            else:
                print(f"[scos-control] WARNING: thin jar without dependency "
                      f"classpath: {jar}", file=sys.stderr)
            return {
                "jar": jar, "extra_classpath": extra_cp, "build_tool": build_tool,
                "ok": ok, "log_path": str(log_path),
            }
        # No build tool to export classpath — jar alone is not enough for thin.
        return {
            "jar": jar, "extra_classpath": "", "build_tool": build_tool,
            "ok": False, "log_path": str(log_path),
        }

    if allow_build:
        return _build_source_jar(project_dir, java_home=java_home or "",
                                 force_rebuild=force_rebuild)
    return empty


def _cmd_build_doctor(args) -> int:
    """Prove a workload build converges (ladder + classifier). No tests run.

    Scala analogue of PySpark ``seed-venv``. Use ``--side source`` (default) for
    Phase A ``Validation/source``, or ``--side migrated``/``output`` for ``Output/``
    before Phase B.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    side = (getattr(args, "side", None) or "source").strip().lower()
    if side in ("migrated", "output", "b", "phase_b"):
        project_dir = conv_root / "Output"
        side_label = "migrated"
    else:
        override = getattr(args, "source_dir", None)
        project_dir = Path(override).expanduser().resolve() if override else (
            validation_root(conv_root) / "source"
        )
        side_label = "source"

    java_home = _resolve_phase_a_jdk(allow_provision=True) or ""
    preferred = ""
    if side_label == "migrated":
        try:
            analysis = load_analysis(conv_root)
            jar_rel = analysis.get("jar_path", "") or ""
            if jar_rel:
                preferred = str((conv_root / jar_rel).resolve())
        except Exception:  # noqa: BLE001
            preferred = ""

    filter_detail: dict = {}
    force_rebuild_bd = getattr(args, "force_rebuild", False)
    build = _resolve_workload_artifact(
        project_dir, java_home=java_home, preferred_jar=preferred,
        allow_build=True, filter_detail=filter_detail, force_rebuild=force_rebuild_bd)
    cause, remediation = ("", "")
    if not build.get("ok"):
        cause, remediation = _classify_build_failure(Path(build.get("log_path") or ""))
        if build.get("jar") and not build.get("extra_classpath"):
            cause = cause or "thin-jar-empty-classpath"
            remediation = (
                remediation
                or "Thin jar needs a non-empty filtered runtime classpath "
                   "(sbt export / mvn dependency:build-classpath / gradle "
                   "scosPrintRuntimeClasspath)."
            )
    report = {
        "ok": bool(build.get("ok")),
        "side": side_label,
        "project_dir": str(project_dir),
        "jar": build.get("jar") or "",
        "extra_classpath": build.get("extra_classpath") or "",
        "build_tool": build.get("build_tool") or "",
        "log_path": build.get("log_path") or "",
        "fat_jar": bool(build.get("jar") and _is_fat_jar(build["jar"])),
        "classpath_kept": len(filter_detail.get("kept") or []),
        "classpath_dropped": len(filter_detail.get("dropped") or []),
        "classpath_dropped_sample": [
            {"name": Path(d["path"]).name, "reason": d.get("reason", "")}
            for d in (filter_detail.get("dropped") or [])[:12]
        ],
        "cause": cause,
        "remediation": remediation,
        "java_home": java_home,
    }
    out_path = getattr(args, "output", None)
    text = json.dumps(report, indent=2) + "\n"
    if out_path:
        Path(out_path).expanduser().resolve().write_text(text, encoding="utf-8")
        print(f"[scos-control] build-doctor report -> {out_path}")
    else:
        sys.stdout.write(text)
    if not build.get("jar"):
        return 5
    if not build.get("ok"):
        return 1
    return 0


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
        # Also search Python package distributions (snowpark_connect_deps*)
        try:
            import importlib.util as _ilu
            for _pkg in ("snowpark_connect_deps_1", "snowpark_connect_deps"):
                _spec = _ilu.find_spec(_pkg)
                if _spec and _spec.submodule_search_locations:
                    for _loc in _spec.submodule_search_locations:
                        sc_search.append(Path(_loc) / "jars")
        except Exception:
            pass
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

    Deequ classes are often dropped from the workload assembly by sbt-assembly's
    MergeStrategy.first when a transitive dep also carries them. Re-running
    `sbt assembly` does NOT fix this (the merge drops them again), so the class
    fails to load with NoClassDefFoundError: com/amazon/deequ/VerificationResult at
    class-load time — even when validate() is never called, because the JVM resolves
    all referenced types when the class (e.g. Silver) is loaded.

    This applies to BOTH phases: Phase A loads the original source's Silver class on
    local Spark; Phase B loads the migrated Output's class on the SCOS server. Stage
    the workload's declared Deequ jar from the Coursier/Ivy cache (same mechanism as
    the SCOS client jars).
    """
    lib_dir = tests_dir / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    if list(lib_dir.glob("deequ*.jar")):
        return  # already staged
    build_files = list((conv_root / "Output").glob("build.sbt")) + \
                  list((conv_root / "Output").glob("pom.xml")) + \
                  list((conv_root / "Output").glob("build.gradle*"))
    needs_deequ = any(
        "deequ" in f.read_text(encoding="utf-8", errors="ignore").lower()
        for f in build_files if f.is_file()
    )
    if not needs_deequ:
        return
    deequ_search = [
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
              "Coursier/Ivy cache — a class referencing com.amazon.deequ may fail to "
              "load (NoClassDefFoundError). Copy the jar to tests/lib/ manually.",
              file=sys.stderr)


def _render_spec(template: str, ep: dict, source_jar: str, migrated_jar: str,
                 trial_dir: str, phase_a_dir: str, analysis_json: str,
                 state_json: str,
                 extra_classpath_source: str = "",
                 extra_classpath_migrated: str = "") -> str:
    """Render one TestTemplate.scala.tmpl substituting all {{TOKEN}} placeholders.

    Both the original-source jar (Phase A, local Spark) and the migrated Output jar
    (Phase B, SCOS) are baked in; the rendered spec selects between them at runtime
    via SCOS_FLAVOR so the same spec drives both phases. EXTRA_CLASSPATH_* mirrors
    JAR_PATH_* for thin-jar + dependency-classpath Phase A runs (migrated stays
    empty this pass — Phase B still expects an Output assembly).
    """
    ep_id = ep["id"]
    class_name = f"Test{_snake_to_camel(ep_id)}Spec"
    entry_class = ep.get("entrypoint_class", "")
    entry_method = ep.get("entrypoint_method", "main")

    def _scala_str(s: str) -> str:
        """Escape a Python string value for safe embedding in a Scala string literal."""
        return s.replace("\\", "\\\\").replace('"', '\\"')

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
        "{{EXTRA_CLASSPATH_SOURCE}}": extra_classpath_source or "",
        "{{EXTRA_CLASSPATH_MIGRATED}}": extra_classpath_migrated or "",
        "{{ENTRY_CLASS_NAME}}": entry_class,
        "{{ENTRY_METHOD_NAME}}": entry_method,
        "{{ENTRYPOINT_ARGS}}": args_literal,
        "{{TRIAL_DIR}}": trial_dir,
        "{{PHASE_A_DIR}}": phase_a_dir,
        "{{WIDGET_ENV_VARS}}": widget_literal,
        "{{ANALYSIS_JSON_PATH}}": analysis_json,
        "{{SCHEMAS_DIR_PATH}}": str(Path(analysis_json).parent / "schemas")
            if analysis_json else "",
        "{{STATE_JSON_PATH}}": state_json,
    }
    result = template
    for tok, val in tokens.items():
        result = result.replace(tok, val)
    return result


def _clear_trial_outputs(trial_dir: Path) -> None:
    """Remove stale per-trial capture state so a partial/crashed re-run never shows
    prior-iteration outputs (mirrors the PySpark harness driver._clear_trial_outputs).

    The Scala fixture writes ``_index.json`` + ``tables/`` per trial and the comparator
    writes ``*_diff.json`` into the same dir. If a rerun's sbt fails to re-execute a
    spec (compile error, JVM abort), those old files would leak into the new result and
    be counted as this iteration's baseline/capture. Clear them before (re)rendering.
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


def _ensure_mock_data(conv_root: Path) -> tuple[int, list]:
    """Deterministically guarantee seedable mock data exists before Phase A runs.

    Phase A's whole purpose is to produce a local baseline; running ``sbt test``
    against missing/stale/unseedable mock data yields an EMPTY baseline, which then
    tempts the runner into ``phase_a_skipped``. This guard closes that hole
    (mirroring ``run-phase-b``'s auto-provision):

      1. If ``shared/schemas/`` is missing, mine it from ``analysis.json``
         (``schema_mine.analysis_to_schemas``).
      2. ``datagen.verify_schema`` the declarations, then seed typed mocks for
         every valid table (hash-gated ``datagen.seed_workload`` — only
         regenerates missing/stale files, never clobbers good ones). A table with
         a schema problem gets no data.
      3. ``datagen.verify_mocks`` what was generated and sweep everything else,
         so ``mock_data`` reflects exactly this run and no stale file survives.
         If anything is reported, return it so the caller can HARD-FAIL with an
         actionable list instead of silently running sbt against broken data.

    Returns ``(exit_code, problems)``. exit_code 0 means mocks verified clean.
    Reuses the canonical PySpark ``datagen.py`` (same layout contract as
    ``schema_mine.py``); imported from the sibling validator's scripts dir.
    """
    shared = conv_root / "Validation" / "shared"
    schemas_dir = shared / "schemas"
    mock_dir = shared / "mock_data"

    # 1. Ensure schemas/ exists (mine from analysis.json if absent).
    if not (schemas_dir / "manifest.json").is_file():
        try:
            # Import THIS skill's schema_mine explicitly by path — a sibling
            # PySpark `schema_mine.py` (no analysis_to_schemas) could otherwise
            # shadow it if it landed on sys.path first in this process.
            schema_mine = _import_scala_schema_mine()
            res = schema_mine.analysis_to_schemas(conv_root)
            print(f"[scos-control] schema_mine: {res.get('entrypoints')} entrypoint(s), "
                  f"{res.get('tables')} table(s) -> {schemas_dir}")
        except Exception as exc:  # noqa: BLE001
            return 2, [f"schema_mine failed: {exc}"]
    else:
        # schemas/ is SoT — refresh analysis.json for the JVM kit.
        ensure_analysis_shim_from_schemas(conv_root)

    # 2/3. Seed (hash-gated) + verify via the canonical PySpark datagen.
    _pyspark_scripts = Path(__file__).resolve().parent.parent.parent \
        / "validate-pyspark-to-snowpark-connect" / "scripts"
    if str(_pyspark_scripts) not in sys.path:
        sys.path.insert(0, str(_pyspark_scripts))
    try:
        import datagen  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        # Hard-fail like schema_mine: an unimportable datagen leaves Phase A on
        # empty/stale mocks and drives unwarranted phase_a_skipped pressure.
        return 2, [f"datagen import failed: {exc}"]

    try:
        manifest = datagen.read_manifest(schemas_dir)
        entrypoints = datagen.read_entrypoints(schemas_dir, manifest)
        sql_files_path = schemas_dir / "sql_files.json"
        sql_files = None
        if sql_files_path.is_file():
            try:
                sql_files = json.loads(sql_files_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                sql_files = None
        # Validate the schemas, generate data only for the valid tables, then
        # validate what was generated and sweep everything else away so mock_dir
        # reflects exactly this run.
        keyed = datagen.verify_schema(entrypoints, sql_files=sql_files)
        gated = datagen.tables_to_gate(keyed, entrypoints)
        seeded = datagen.seed_workload(entrypoints, str(mock_dir), gated_tables=gated)
        table_paths = seeded["table_paths"]
        mock_problems, _overlap = datagen.verify_mocks(
            entrypoints, str(mock_dir), set(table_paths))
        bad_mocks = datagen.tables_to_gate(mock_problems, entrypoints)
        for _key, _msgs in mock_problems.items():
            keyed.setdefault(_key, []).extend(_msgs)
        datagen.sweep_mock_dir(
            str(mock_dir), [ep["id"] for ep in entrypoints],
            {p for _key, _paths in table_paths.items() if _key not in bad_mocks
             for p in _paths})
        problems = [m for _key in sorted(keyed) for m in keyed[_key]]
    except Exception as exc:  # noqa: BLE001
        return 2, [f"datagen seed/verify failed: {exc}"]

    if problems:
        return 1, problems
    print(f"[scos-control] mock-data guard: verify OK ({len(entrypoints)} entrypoint(s)) -> {mock_dir}")
    return 0, []


def _available_ram_gb() -> Optional[float]:
    """Best-effort AVAILABLE (not total) host RAM in GiB for JVM parallelism capping.

    Priority:
      1. psutil.virtual_memory().available — cross-platform, accurate
      2. /proc/meminfo MemAvailable — Linux
      3. sysctl hw.memsize / 2 — macOS (total/2 as conservative estimate when
         psutil is absent; avoids over-commit on heavily-loaded macOS hosts)
    """
    try:
        import psutil as _psutil
        return _psutil.virtual_memory().available / (1024 ** 3)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        pass
    try:
        avail: Optional[float] = None
        total: Optional[float] = None
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) / (1024 * 1024)
                elif line.startswith("MemTotal:"):
                    total = int(line.split()[1]) / (1024 * 1024)
        if avail is not None:
            return avail
        if total is not None:
            return total
    except (OSError, ValueError):
        pass
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"], text=True, stderr=subprocess.DEVNULL)
        # On macOS we only have total; divide by 2 as a conservative available estimate
        # so a heavily-loaded machine doesn't over-commit 4 × 4 GiB JVMs.
        return int(out.strip()) / (1024 ** 3) / 2
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _resolve_test_parallelism(explicit: Optional[int]) -> int:
    """Host-aware SCOS_TEST_PARALLELISM. Explicit ``--parallelism N`` always wins.

    Uses min(ram_based_cap, cpu_based_cap) so a low-core host (e.g. 2-vCPU CI
    runner) doesn't launch 4 × 4 GiB JVMs that thrash on context-switches.

    * ram_based_cap: available_ram / 4 GiB per JVM  (< 8 GiB → 1, < 16 → 2, else 4)
    * cpu_based_cap: cpu_count // 2, minimum 1
    """
    if explicit is not None:
        return max(1, int(explicit))
    gb = _available_ram_gb()
    cpu_cap = max(1, (os.cpu_count() or 2) // 2)
    if gb is None:
        chosen = min(4, cpu_cap)
        print(f"[scos-control] parallelism={chosen} (ram_cap=4[default], cpu_cap={cpu_cap}, "
              "available_ram=unknown GiB)")
        return chosen
    if gb < 8:
        ram_cap = 1
    elif gb < 16:
        ram_cap = 2
    else:
        ram_cap = 4
    chosen = min(ram_cap, cpu_cap)
    print(f"[scos-control] parallelism={chosen} (ram_cap={ram_cap}, cpu_cap={cpu_cap}, "
          f"available_ram={gb:.1f} GiB; pass --parallelism to override)")
    return chosen


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

    # Validate --trial-id early (before mock guard) so bad IDs fail fast.
    _target_trial_early = getattr(args, "trial_id", None) or ""
    if _target_trial_early and _target_trial_early not in (state.get("trials") or {}):
        return _die(f"--trial-id '{_target_trial_early}' not in state.trials", 2)

    # 0. Preflight — HARD-FAIL on a missing/incompatible JVM or toolchain so an
    # environment failure can never masquerade as a no-baseline pass (the reported
    # idempotency bug). Resolves (and auto-provisions) a Java 8/11/17 JDK because
    # Phase A's local Spark 3.5 crashes at startup on JDK 21 before capturing anything.
    pf_rc, pf_problems, java_home = _preflight_checks(conv_root, "a")
    if pf_rc != 0:
        _pf_preview = "\n".join(f"  - {p}" for p in pf_problems)
        return _die(
            "Phase A preflight FAILED — environment not ready; fix these before "
            "running (the harness will NOT silently produce a no-baseline pass):\n"
            + _pf_preview, pf_rc)
    print(f"[scos-control] Phase A preflight OK: JAVA_HOME={java_home}")

    # 0b. Mock-data guard — ensure a seedable, verified baseline dataset exists
    # BEFORE running sbt, so Phase A cannot silently produce an empty baseline
    # (the usual precursor to an unwarranted phase_a_skipped). Mirrors run-phase-b's
    # auto-provision. Skippable with --no-mock-guard for a deliberate re-run.
    if not getattr(args, "no_mock_guard", False):
        mg_rc, mg_problems = _ensure_mock_data(conv_root)
        if mg_rc != 0:
            _preview = "\n".join(f"  - {p}" for p in mg_problems[:20])
            return _die(
                "mock-data guard failed — Phase A would run against missing/broken "
                "mock data and produce no trustworthy baseline. Fix analysis.json "
                "schemas and re-run (do NOT skip Phase A for this). datagen problems:\n"
                + _preview, mg_rc)

    # 1. Stage kit -----------------------------------------------------------
    kit_src = skill_dir / "harness-scala" / "kit"
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
        try:
            stale.chmod(stale.stat().st_mode | 0o200)  # ensure writable before delete
        except OSError:
            pass
        stale.unlink(missing_ok=True)

    # Deequ (and similar deps dropped from the assembly by MergeStrategy.first) is
    # needed for Phase A too: loading the ORIGINAL source's Silver class on local
    # Spark resolves com.amazon.deequ types at class-load time. Without it Phase A
    # fails with NoClassDefFoundError before producing a baseline.
    _stage_deequ_if_needed(tests_dir, conv_root)


    # 2. Build the ORIGINAL source jar (the local baseline) ------------------
    # Phase A runs the original source on local Spark. The source has been patched
    # (mock I/O + injectable session) and uses plain SparkSession, so it runs locally.
    # Compiled with the same JDK Phase A runs on (java_home) to avoid class-file skew.
    # Ladder: fat/assembly preferred; thin jar + filtered dependency classpath is OK.
    # Speed 2: reuses existing jar when present (no rebuild when build-doctor ran first).
    source_dir = conv_root / "Validation" / "source"
    force_rebuild_a = getattr(args, "force_rebuild", False)
    build = _build_source_jar(source_dir, java_home=java_home or "",
                              force_rebuild=force_rebuild_a)
    source_jar = build.get("jar") or ""
    extra_cp_source = build.get("extra_classpath") or ""
    if not source_jar:
        # HARD FAIL only when no jar of any kind exists. Thin jar + classpath is a
        # valid Phase A input; per-entrypoint CNF/link failures stay per-trial.
        cause, remediation = _classify_build_failure(Path(build.get("log_path") or ""))
        return _die(
            "Phase A source-jar build FAILED — no jar produced (cannot baseline). "
            f"cause={cause}. {remediation} "
            f"Inspect {build.get('log_path') or (source_dir / 'scos_source_build.log')}. "
            "This is a build/environment failure, not an environment-difference skip "
            "(do NOT mark phase_a_skipped). Run `scos_state.py build-doctor` first.", 5)
    if not build.get("ok"):
        # Thin jar without a usable dependency classpath is NOT a silent proceed —
        # it produces ClassNotFound that looks like workload bugs / bogus skips.
        cause, remediation = _classify_build_failure(Path(build.get("log_path") or ""))
        rem = remediation or (
            "Export runtime classpath (sbt/mvn/gradle) and re-run "
            "build-doctor --side source."
        )
        return _die(
            "Phase A source build not OK — thin jar without a filtered dependency "
            f"classpath (jar={source_jar}). cause={cause or 'thin-jar-empty-classpath'}. "
            f"{rem} "
            f"Log: {build.get('log_path') or (source_dir / 'scos_source_build.log')}. "
            "Do NOT mark phase_a_skipped.", 5)
    if extra_cp_source:
        print(f"[scos-control] Phase A using thin jar + {len(extra_cp_source.split(os.pathsep))} "
              f"extra classpath entries")

    # 3. Speed 1 — Defer Output/ resolve/build out of Phase A ----------------
    # Phase B re-renders its own specs with a fresh migrated-jar resolve; baking the
    # migrated jar into Phase A specs was the only reason to resolve/build Output/ here.
    # Skipping it avoids triggering `sbt assembly` of Output/ before any trial runs,
    # saving 30-120 s on first run.  Phase B still hard-fails when no migrated jar found.
    migrated_jar = ""
    extra_cp_migrated = ""
    print("[scos-control] Phase A: skipping Output/ jar resolve (Phase B re-renders specs "
          "with its own migrated jar; run `build-doctor --side migrated` before Phase B)")
    # Workload jars are loaded by ReflectionEntrypoint via absolute path — they are NOT
    # placed in tests/lib/ (which is the kit compile classpath and must stay clean).

    # 4. Render test specs ---------------------------------------------------
    template_path = tests_dir / "templates" / "TestTemplate.scala.tmpl"
    if not template_path.is_file():
        return _die(f"TestTemplate not found: {template_path} — "
                    "did the kit copy succeed?", 2)
    template = template_path.read_text(encoding="utf-8")
    # Clear Phase B + legacy flat specs so only Phase A specs are compiled.
    _clear_rendered_specs(tests_dir, phases=["b"])
    spec_dir = _phase_spec_dir(tests_dir, "a")
    spec_dir.mkdir(parents=True, exist_ok=True)

    analysis_json_path = str(conv_root / "Validation" / "shared" / "analysis.json")
    state_json_path = str(conv_root / "Validation" / "state.json")

    trials = state.get("trials", {})
    eps_by_id = {ep["id"]: ep for ep in ensure_entrypoints_list(analysis) if ep.get("id")}

    # Terminal trials don't need Phase A re-run (their baseline is already settled).
    # --verify-all overrides this for regression-checking (same pattern as Phase B).
    # --trial-id focuses the run on a single trial (all others deselected).
    verify_all_a: bool = getattr(args, "verify_all", False)
    target_trial_a: str = getattr(args, "trial_id", None) or ""
    if target_trial_a and target_trial_a not in trials:
        return _die(f"--trial-id '{target_trial_a}' not in state.trials", 2)
    if target_trial_a:
        pre_phase_a_terminal = {tid for tid in trials if tid != target_trial_a}
    elif verify_all_a:
        pre_phase_a_terminal = set()
    else:
        pre_phase_a_terminal = {
            tid for tid, t in trials.items()
            if _status(t) in TERMINAL_TRIAL_STATUSES
        }
    if pre_phase_a_terminal:
        print(f"[scos-control] Phase A: skipping {len(pre_phase_a_terminal)} terminal "
              f"trial(s): {sorted(pre_phase_a_terminal)}")

    rendered: list = []
    for tid in list(trials.keys()):
        # Skip terminal trials — wipes their baseline and adds confusing iters.
        if tid in pre_phase_a_terminal:
            continue
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
            extra_classpath_source=extra_cp_source,
            extra_classpath_migrated=extra_cp_migrated,
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
        return _die(
            "sbt not on PATH — specs rendered but not executed. "
            "Install sbt 1.x before Phase A (same gate as prewarm / run-phase-b).",
            2,
        )

    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = results_dir / "sbt_source.log"

    # Align the kit's Spark/Delta/Scala to the workload's so the original (already-compiled)
    # bytecode runs without Catalyst/Delta binary-signature mismatches (Phase A only).
    kit_versions = _detect_source_versions(source_dir)
    parallelism = _resolve_test_parallelism(getattr(args, "parallelism", None))
    sbt_env = {
        **os.environ,
        **kit_versions,
        "SCOS_FLAVOR": "source",
        "SCOS_TEST_PARALLELISM": str(parallelism),
        "SCOS_RESULTS_DIR": str(results_dir),
        "SCOS_CONV_ROOT": str(conv_root),
        "SCOS_ANALYSIS_JSON": analysis_json_path,
        "SCOS_SCHEMAS_DIR": str(conv_root / "Validation" / "shared" / "schemas"),
        "SCOS_STATE_JSON": state_json_path,
        "SCOS_MOCK_DATA_DIR": str(conv_root / "Validation" / "shared" / "mock_data"),
        # Speed 5: default to in-process execution (no child JVM) for Phase A.
        # ScosTrialFixture.runTrial checks this env var; "0" runs the workload
        # in-process via ReflectionEntrypoint (faster: no extra JVM spawn).
        # Set SCOS_PHASE_A_SUBPROCESS=1 in the environment to opt back into the
        # subprocess mode (useful for SerializedLambda isolation on Java 17 + Spark 3.5).
        "SCOS_PHASE_A_SUBPROCESS": os.environ.get("SCOS_PHASE_A_SUBPROCESS", "0"),
    }
    # Pin the sbt build + Test/fork JVM to the resolved Java 8/11/17 JDK so local
    # Spark 3.5 starts (the ambient JVM may be 21, which Spark 3.5 rejects).
    sbt_env = _apply_jdk_to_env(sbt_env, java_home or "")

    # Inject SCOS_INPUT_DATALAKE_<N> for each external source that is NOT
    # a delta read so DataLake.load() intercepts (Blob/S3/SFTP patch) can
    # redirect to local mock parquet instead of mounting Azure/S3/SFTP.
    # Also sets SCOS_INPUT_DATALAKE_MOCK to the first such file (used by
    # the simple single-mock intercept for workloads with uniform Bronze schema).
    try:
        analysis_data = json.loads(Path(analysis_json_path).read_text(encoding="utf-8"))
        mock_data_root = Path(conv_root) / "Validation" / "shared" / "mock_data"
        first_dl_mock: str = ""
        for ep in (analysis_data.get("entrypoints") or []):
            ep_id = ep.get("id", "")
            datalake_idx = 0
            for src in (ep.get("external_sources") or []):
                mock_file = src.get("mock_file", "")
                src_id = src.get("id", "")
                # Non-delta file sources that have a mock file are DataLake reads
                if mock_file and "delta" not in src_id.lower() and "delta" not in mock_file.lower():
                    datalake_idx += 1
                    mock_path = mock_data_root / ep_id / mock_file
                    if mock_path.exists():
                        sbt_env[f"SCOS_INPUT_DATALAKE_{ep_id.upper()}_{datalake_idx}"] = str(mock_path)
                        sbt_env[f"SCOS_INPUT_DATALAKE_{datalake_idx}"] = str(mock_path)
                        if not first_dl_mock:
                            first_dl_mock = str(mock_path)
        if first_dl_mock:
            sbt_env.setdefault("SCOS_INPUT_DATALAKE_MOCK", first_dl_mock)
        # Inject SCOS_INPUT_TAX_RATES for LoadTaxes MongoDB bypass patch.
        for ep in (analysis_data.get("entrypoints") or []):
            ep_id = ep.get("id", "")
            for _tax_candidate in ("tax_rates.parquet", "inputtaxratedatabaseproperties.parquet"):
                _tax_mock = mock_data_root / ep_id / _tax_candidate
                if _tax_mock.exists():
                    sbt_env["SCOS_INPUT_TAX_RATES"] = str(_tax_mock)
                    break
    except Exception:  # noqa: BLE001
        pass  # best-effort; harness still works without this

    print(f"[scos-control] sbt test (Phase A) -> {log_path}")
    sbt_rc = _run_sbt_with_transient_retry(
        cmd=["sbt", "-batch", "test"],
        cwd=str(tests_dir),
        env=sbt_env,
        log_path=log_path,
        label="Phase A",
    )
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

    state = advance_phase(state, conv_root)   # update phase tracker (init → phase_a_done when all have A iters)
    save_state(conv_root, state)
    append_event(workspace, {"kind": "phase_a_complete", "sbt_rc": sbt_rc,
                             "trials": rendered})

    # Systemic-failure guard (idempotency): if NOT ONE rendered trial produced an
    # `_index.json`, sbt failed to compile/run the whole batch (the classic JDK-21
    # symptom: local Spark 3.5 crashes at startup before any capture). That is an
    # environment failure, not N independent workload bugs, and it must HARD-FAIL —
    # otherwise every trial would flow to Phase B and silently degrade to
    # passed_no_baseline, producing a verdict that flips once the environment is
    # fixed. A clean no-sink run still writes an `_index.json`, so this only fires on
    # a true systemic crash. Per-trial capture (some produced, some didn't) is left
    # to the normal fixer loop. Iters are already recorded above, so a re-run after
    # fixing the environment is idempotent.
    produced_any = any((results_dir / tid / "_index.json").is_file() for tid in rendered)
    if rendered and not produced_any:
        return _die(
            f"Phase A produced NO baselines across all {len(rendered)} trial(s) "
            f"(sbt rc={sbt_rc}) — this is a systemic build/JVM failure, not an "
            "environment-difference skip. Inspect "
            f"{log_path} (and per-trial workload_error.txt). Do NOT mark trials "
            "phase_a_skipped for this. The most common cause is an incompatible JVM "
            "(Spark 3.5 needs Java 8/11/17) or a kit/source compile error.", 5)

    # Auto-pass trials with no declared sinks that ran without error:
    # "if no sinks are declared, running without error IS the baseline" (user policy).
    # Subprocess mode records subprocess_exit_code in _index.json.
    # Non-subprocess mode: a _index.json with no failures and no workload_error.txt.
    # Narrow exception: refuse synthetic baseline when AST still shows writes for the
    # entrypoint — that is an analysis mining gap, not a true no-sink trial.
    analysis_json_path = str(conv_root / "Validation" / "shared" / "analysis.json")
    try:
        analysis = json.loads(Path(analysis_json_path).read_text(encoding="utf-8"))
        ep_by_id = {
            ep["id"]: ep
            for ep in (analysis.get("entrypoints") or [])
            if isinstance(ep, dict) and ep.get("id")
        }
        ep_sinks = {
            eid: entrypoint_declared_sinks(ep)
            for eid, ep in ep_by_id.items()
        }
    except Exception:  # noqa: BLE001
        ep_by_id = {}
        ep_sinks = {}
    ast_facts = _load_json_optional(ast_facts_path(conv_root))

    state = load_state(conv_root)
    for tid in rendered:
        if state["trials"].get(tid, {}).get("status") in TERMINAL_TRIAL_STATUSES:
            continue
        declared_sinks = ep_sinks.get(tid, ["_unknown_"])  # default: unknown → skip
        if declared_sinks:  # has sinks → need actual capture, do not auto-pass
            continue
        write_ev = entrypoint_ast_write_evidence(ast_facts, ep_by_id.get(tid) or {"id": tid})
        if write_ev:
            print(
                f"[scos-control] trial {tid}: sinks=[] but ast_facts shows writes "
                f"({'; '.join(write_ev[:2])}"
                f"{'…' if len(write_ev) > 2 else ''}) — refusing no_sink_baseline; "
                "re-mine sinks before treating this as smoke-only",
                file=sys.stderr,
            )
            trial = state["trials"].setdefault(tid, {})
            trial["analysis_sink_gap"] = True
            continue
        # No sinks declared — check that workload ran without error
        trial_dir = results_dir / tid
        workload_err = trial_dir / "workload_error.txt"
        index_file   = trial_dir / "_index.json"
        no_error = not workload_err.exists()
        if index_file.exists():
            try:
                idx = json.loads(index_file.read_text(encoding="utf-8"))
                no_error = no_error and (idx.get("subprocess_exit_code", 0) == 0)
                no_error = no_error and not idx.get("failures")
            except Exception:  # noqa: BLE001
                no_error = False
        if no_error:
            # A clean no-sink run IS the baseline (user policy), but Phase A must NOT
            # set the terminal 'passed' status — that is Phase B's job (and the
            # 'passed' gate now requires a green Phase B iter). Instead mark the
            # latest phase_a iter as a produced baseline (passing>=1, failing==0) so
            # `recover_pending_trials` promotes the trial to 'passed' after a clean
            # Phase B. For a no-sink entrypoint the harness records passing=0
            # (no captured tables), which would otherwise read as "no baseline".
            trial = state["trials"].setdefault(tid, {})
            a_iters = trial.get("phase_a_iters") or []
            if a_iters and a_iters[-1].get("failing", 0) == 0:
                a_iters[-1]["passing"] = max(a_iters[-1].get("passing", 0), 1)
                a_iters[-1]["no_sink_baseline"] = True
                a_iters[-1]["notes"] = (
                    "No sinks declared; workload ran without error — "
                    "run-without-error IS the baseline")
                trial["phase_a_iters"] = a_iters
                print(f"[scos-control] trial {tid} clean no-sink Phase A baseline "
                      "recorded (pending Phase B)")

    state = advance_phase(state, conv_root)   # re-check: no-sink trials now have passing A iter
    save_state(conv_root, state)
    deselected_a = len(pre_phase_a_terminal)
    print(f"[scos-control] Phase A done: {len(rendered)} trial(s) run, "
          f"{deselected_a} terminal skipped, sbt_rc={sbt_rc}")
    return sbt_rc




# ---------------------------------------------------------------------------
# verify-all reopen  (port of PySpark _maybe_reopen_trial_after_phase_b_failure)
# ---------------------------------------------------------------------------

_REFRESHABLE_PHASE_B_STATUSES = frozenset({"passed", "passed_no_baseline", "hard_stuck"})


def _maybe_reopen_trial(
    state: dict, trial_id: str, passing: int, failing: int, iter_n: int,
    conv_root: Path, workspace: Path,
) -> dict:
    """Reopen a previously terminal Phase B trial when a --verify-all rerun fails.

    Only acts when:
      * ``failing > 0`` (the rerun was not clean), AND
      * the current status is in ``_REFRESHABLE_PHASE_B_STATUSES``.

    Clears the terminal status and ``phase_a_skip_reason`` so the trial can be
    re-evaluated from scratch — a rerun that now succeeds promotes it back via
    ``recover_pending_trials`` in the normal way (parity with PySpark
    ``_maybe_reopen_trial_after_phase_b_failure``).
    """
    if failing == 0:
        return state
    trials = state.get("trials", {})
    trial = trials.get(trial_id)
    if not trial:
        return state
    prior = _status(trial)
    if prior not in _REFRESHABLE_PHASE_B_STATUSES:
        return state

    t2 = {**trial, "status": "pending"}
    t2.pop("final_iter", None)
    t2.pop("hard_stuck_reason", None)
    # Drop the skip reason so a trial that now yields a real baseline auto-promotes
    # to passed instead of passed_no_baseline (PySpark parity).
    t2.pop("phase_a_skip_reason", None)
    new_state = {**state, "phase": "phase_a_done", "trials": {**trials, trial_id: t2}}
    append_event(workspace, {
        "kind": "trial_marked", "trial_id": trial_id, "status": "pending",
        "reason": (f"reopened by run-phase-b --verify-all after failed rerun "
                   f"(iter {iter_n}: passing={passing} failing={failing} prior={prior})"),
        "auto": True,
    })
    print(f"[scos-control] reopened trial {trial_id} from {prior} to pending "
          f"after failed --verify-all rerun iter {iter_n}")
    return new_state


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

    # Trials already terminal BEFORE this run: skip re-render + re-run for them
    # (PySpark parity: run-tests deselects terminal trials before pytest).
    # --trial-id focuses the run on a single trial (all others deselected).
    # --verify-all re-runs everything (clears the terminal set).
    verify_all: bool = getattr(args, "verify_all", False)
    target_trial: str = getattr(args, "trial_id", None) or ""
    _trials_b = state.get("trials") or {}
    if target_trial and target_trial not in _trials_b:
        return _die(f"--trial-id '{target_trial}' not in state.trials", 2)
    if target_trial:
        pre_sbt_terminal = {tid for tid in _trials_b if tid != target_trial}
    elif verify_all:
        pre_sbt_terminal = set()
    else:
        # hard_stuck trials are retriable: a code fix may have landed since the
        # last failure. Exclude them from the terminal set so they always get a
        # fresh Phase B run. If they fail again they stay hard_stuck; if they
        # pass, recover_pending_trials will promote them.
        pre_sbt_terminal = {
            tid for tid, t in _trials_b.items()
            if _status(t) in TERMINAL_TRIAL_STATUSES and _status(t) != "hard_stuck"
        }

    if not tests_dir.is_dir():
        return _die("tests/ not found — run `prewarm` or `run-phase-a` first", 2)

    # 0. Preflight — HARD-FAIL on an incompatible JVM or a missing SCOS client jar
    # before doing any work. The SCOS/Arrow client also needs Java 8/11/17.
    pf_rc, pf_problems, java_home = _preflight_checks(conv_root, "b")
    if pf_rc != 0:
        _pf_preview = "\n".join(f"  - {p}" for p in pf_problems)
        return _die(
            "Phase B preflight FAILED — environment not ready; fix these before "
            "running:\n" + _pf_preview, pf_rc)
    print(f"[scos-control] Phase B preflight OK: JAVA_HOME={java_home}")

    # 0b. Auto-provision golden schemas when missing (PySpark parity) ---------
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

    # 2b. Prove migrated workload JAR (+ thin-jar classpath) before sbt ------
    # Mirror Phase A's build-doctor gate so we fail before Snowflake provision
    # burn / multi-JVM Phase B on a missing ClassNotFound.
    _analysis_path = conv_root / "Validation" / "shared" / "analysis.json"
    preferred_migrated = ""
    if _analysis_path.exists():
        try:
            _a = json.loads(_analysis_path.read_text(encoding="utf-8"))
            _jar_rel = _a.get("jar_path", "") or ""
            if _jar_rel:
                preferred_migrated = str((conv_root / _jar_rel).resolve())
        except Exception:  # noqa: BLE001
            preferred_migrated = ""
    migrated_filter: dict = {}
    migrated_build = _resolve_workload_artifact(
        conv_root / "Output",
        java_home=java_home or "",
        preferred_jar=preferred_migrated,
        allow_build=True,
        filter_detail=migrated_filter,
    )
    migrated_jar_b = migrated_build.get("jar") or ""
    extra_cp_migrated_b = migrated_build.get("extra_classpath") or ""
    if not migrated_jar_b:
        cause, remediation = _classify_build_failure(
            Path(migrated_build.get("log_path") or ""))
        return _die(
            "Phase B migrated-jar resolve FAILED — no Output jar. "
            f"cause={cause}. {remediation} "
            "Run `scos_state.py build-doctor --side migrated` first.", 5)
    if not migrated_build.get("ok"):
        cause, remediation = _classify_build_failure(
            Path(migrated_build.get("log_path") or ""))
        rem = remediation or (
            "Export runtime classpath for Output/ and re-run "
            "build-doctor --side migrated."
        )
        return _die(
            "Phase B migrated build not OK — thin jar without dependency classpath "
            f"(jar={migrated_jar_b}). cause={cause or 'thin-jar-empty-classpath'}. "
            f"{rem}", 5)
    if extra_cp_migrated_b:
        print(f"[scos-control] Phase B using thin migrated jar + "
              f"{len(extra_cp_migrated_b.split(os.pathsep))} classpath entries "
              f"({len(migrated_filter.get('dropped') or [])} Spark/Delta filtered)")
    else:
        print(f"[scos-control] Phase B using fat migrated jar: {migrated_jar_b}")

    # Resolve source jar for dual-baked specs (Phase A path; may be empty if
    # Phase A never produced a fat jar — thin source jars still work via flavor).
    source_dir_b = conv_root / "Validation" / "source"
    source_build_b = _resolve_workload_artifact(
        source_dir_b, java_home=java_home or "", allow_build=False)
    source_jar_b = source_build_b.get("jar") or ""
    extra_cp_source_b = source_build_b.get("extra_classpath") or ""

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
        # Clear Phase A + legacy flat specs so only Phase B specs are compiled.
        _clear_rendered_specs(tests_dir, phases=["a"])
        spec_dir_b = _phase_spec_dir(tests_dir, "b")
        spec_dir_b.mkdir(parents=True, exist_ok=True)
        analysis_b = load_analysis(conv_root)
        eps_by_id_b = {ep["id"]: ep for ep in ensure_entrypoints_list(analysis_b) if ep.get("id")}
        rerendered: list = []
        for tid_b in list(state.get("trials", {}).keys()):
            # Skip terminal trials — re-rendering + re-running wastes JVM slots
            # and adds confusing iters to already-settled verdicts.  --verify-all
            # bypasses this by keeping pre_sbt_terminal empty.
            if tid_b in pre_sbt_terminal:
                continue
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
                extra_classpath_source=extra_cp_source_b,
                extra_classpath_migrated=extra_cp_migrated_b,
            )
            class_name_b = f"Test{_snake_to_camel(tid_b)}Spec"
            (spec_dir_b / f"{class_name_b}.scala").write_text(spec_content_b, encoding="utf-8")
            rerendered.append(tid_b)
        if rerendered:
            print(f"[scos-control] re-rendered {len(rerendered)} spec(s) for Phase B "
                  f"(TRIAL_DIR → phase_b): {rerendered}")
            # Speed 6: only wipe test-classes/ when the spark-connect-client-jvm jar
            # was added/changed in tests/lib/ (not unconditionally on every re-render).
            # The trigger is: Phase A had no connect-client JAR (local Spark only), but
            # Phase B staged it.  That SPI registration changes SparkSession resolution
            # at the bytecode level — an incremental Zinc compile is NOT sufficient.
            # If the JAR did NOT change (same jar, same mtime) Zinc handles it fine.
            # Pass --force-recompile to always wipe (safe fallback for any suspect cache).
            import shutil as _shutil
            force_recompile = getattr(args, "force_recompile", False)
            test_classes_dir = tests_dir / "target" / "scala-2.12" / "test-classes"
            should_wipe = force_recompile
            if not should_wipe and test_classes_dir.is_dir():
                # Check whether any spark-connect-client-jvm jar is newer than test-classes.
                lib_dir_b = tests_dir / "lib"
                connect_jars = list(lib_dir_b.glob("spark-connect-client-jvm*.jar")) \
                    if lib_dir_b.is_dir() else []
                if connect_jars:
                    newest_client = max(j.stat().st_mtime for j in connect_jars)
                    tc_mtime = test_classes_dir.stat().st_mtime
                    if newest_client > tc_mtime:
                        should_wipe = True
                        print("[scos-control] spark-connect-client-jvm was staged/updated "
                              "— wiping test-classes/ for full recompile")
                else:
                    # No connect-client jar found at all: wipe to be safe.
                    should_wipe = True
            elif not should_wipe:
                # test-classes doesn't exist yet: no wipe needed (sbt will do full compile).
                pass
            if should_wipe and test_classes_dir.is_dir():
                _shutil.rmtree(test_classes_dir, ignore_errors=True)
            elif not force_recompile and test_classes_dir.is_dir():
                print("[scos-control] spark-connect-client-jvm unchanged — "
                      "skipping test-classes/ wipe (Zinc incremental compile)")

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
        "SCOS_TEST_PARALLELISM": str(_resolve_test_parallelism(getattr(args, "parallelism", None))),
        "SCOS_RESULTS_DIR": str(results_dir),
        "SCOS_CONV_ROOT": str(conv_root),
        "SCOS_ANALYSIS_JSON": analysis_json_path,
        "SCOS_SCHEMAS_DIR": str(conv_root / "Validation" / "shared" / "schemas"),
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
    # Pin the sbt + SCOS client JVM to the resolved Java 8/11/17 JDK (the Arrow-based
    # SCOS client fails on JDK 21 the same way local Spark does).
    sbt_env = _apply_jdk_to_env(sbt_env, java_home or "")

    # Inject Phase B mock-data references so Blob.load intercept patches can redirect
    # Azure/S3 reads to the seeded Snowflake tables in the golden schema.
    #
    # For each entrypoint, SCOS_INPUT_DATALAKE_<EP>_<N> is set to the fully-qualified
    # Snowflake table name in the golden schema clone (e.g.
    # SCOS_VALIDATION_DB.EP_GOLDEN_T<hash>.datalake_mock_1).  The patched Blob.load
    # intercept (blob_load_phase_b_scos_intercept) detects SCOS_FLAVOR=migrated and
    # calls spark.table() instead of spark.read.parquet() so the read targets Snowflake.
    #
    # SCOS_INPUT_TAX_RATES is also forwarded (LoadTaxes mongodb bypass patch checks it).
    try:
        _analysis_data = json.loads(Path(analysis_json_path).read_text(encoding="utf-8"))
        _state_data = json.loads(Path(state_json_path).read_text(encoding="utf-8"))
        _mock_data_root = conv_root / "Validation" / "shared" / "mock_data"
        _golden_schemas = _state_data.get("snowflake", {}).get("golden_schemas", {})
        _first_dl_mock: str = ""
        for ep in (_analysis_data.get("entrypoints") or []):
            ep_id = ep.get("id", "")
            golden_schema = _golden_schemas.get(ep_id, {}).get("schema", "")
            datalake_idx = 0
            for src in (ep.get("external_sources") or []):
                # external_sources may be a list of strings (id labels) or dicts
                if isinstance(src, str):
                    src_id, mock_file = src, ""
                else:
                    src_id  = src.get("id", "")
                    mock_file = src.get("mock_file", "")
                if mock_file and "delta" not in src_id.lower() and "delta" not in mock_file.lower():
                    datalake_idx += 1
                    if golden_schema:
                        # Snowflake table seeded during provision: datalake_mock_<N>
                        table_ref = f"{golden_schema}.datalake_mock_{datalake_idx}"
                        sbt_env[f"SCOS_INPUT_DATALAKE_{ep_id.upper()}_{datalake_idx}"] = table_ref
                        sbt_env.setdefault(f"SCOS_INPUT_DATALAKE_{datalake_idx}", table_ref)
                        if not _first_dl_mock:
                            _first_dl_mock = table_ref
            # Fallback: if no datalake source mock was resolved but a golden schema exists,
            # point SCOS_INPUT_DATALAKE_MOCK at the always-seeded DELTA_MOCK_DATA table.
            # This ensures Blob.load Phase B intercept fires even for workloads whose
            # external_sources are stored as string labels rather than full source objects.
            if golden_schema and not sbt_env.get(f"SCOS_INPUT_DATALAKE_{ep_id.upper()}_1"):
                _db = _state_data.get("snowflake", {}).get("database", "SCOS_VALIDATION_DB")
                _fallback = f"{_db}.{golden_schema}.DELTA_MOCK_DATA"
                sbt_env[f"SCOS_INPUT_DATALAKE_{ep_id.upper()}_1"] = _fallback
                sbt_env.setdefault("SCOS_INPUT_DATALAKE_MOCK", _fallback)
                if not _first_dl_mock:
                    _first_dl_mock = _fallback
            # LoadTaxes: pass the mock tax-rates parquet path to SCOS_INPUT_TAX_RATES
            # so the loadtaxes_mongo_read_to_scos_input patch bypasses MongoDB.
            # Phase B: use a Snowflake table ref from the golden schema when available;
            # fall back to local parquet (will only work if the file is staged).
            for _tax_candidate in ("tax_rates.parquet", "inputtaxratedatabaseproperties.parquet"):
                _tax_mock = _mock_data_root / ep_id / _tax_candidate
                if _tax_mock.exists():
                    golden_schema_b = _golden_schemas.get(ep_id, {}).get("schema", "")
                    _db_b = _state_data.get("snowflake", {}).get("database", "SCOS_VALIDATION_DB")
                    if golden_schema_b:
                        # Prefer the seeded INPUTTAXRATEDATABASEPROPERTIES table when it exists
                        # (seeded from inputtaxratedatabaseproperties.parquet which has province_code,
                        # gst, pst, hst columns); fall back to DELTA_MOCK_DATA which gets the tax
                        # columns added by the delta_read_phase_b_schema_augment harness patch.
                        _tax_tbl_name = (
                            "INPUTTAXRATEDATABASEPROPERTIES"
                            if _tax_candidate == "inputtaxratedatabaseproperties.parquet"
                            else "DELTA_MOCK_DATA"
                        )
                        sbt_env["SCOS_INPUT_TAX_RATES"] = f"{_db_b}.{golden_schema_b}.{_tax_tbl_name}"
                    else:
                        sbt_env["SCOS_INPUT_TAX_RATES"] = str(_tax_mock)
                    print(f"[scos-control] Phase B: injected SCOS_INPUT_TAX_RATES={sbt_env['SCOS_INPUT_TAX_RATES']}")
                    break
        if _first_dl_mock:
            sbt_env.setdefault("SCOS_INPUT_DATALAKE_MOCK", _first_dl_mock)
        if _first_dl_mock:
            print(f"[scos-control] Phase B: injected SCOS_INPUT_DATALAKE_MOCK={_first_dl_mock}")
    except Exception:  # noqa: BLE001
        pass  # best-effort; tests still run if golden schemas aren't available

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
    sbt_rc = _run_sbt_with_transient_retry(
        # Use testOnly to exclude KitSpec: it fails on the SCOS sidecar port (15002)
        # which is not available in local-server mode.
        cmd=["sbt", "-batch", "testOnly com.snowflake.scos.kit.generated.*"],
        cwd=str(tests_dir),
        env=sbt_env,
        log_path=log_path,
        label="Phase B",
    )
    print(f"[scos-control] sbt test exited: {sbt_rc}")

    # 4. Record iter per trial -----------------------------------------------
    state = load_state(conv_root)
    trials = state.get("trials", {})
    recorded: list = []
    for tid in trials:
        # Never append an iter to a trial that was already terminal before this run
        # (it did not participate in sbt); --verify-all already cleared pre_sbt_terminal.
        if tid in pre_sbt_terminal:
            continue
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

    # 5. Auto-promote: pending + phase_a_skipped trials that passed Phase B ---
    # Mirrors PySpark run-tests per-trial _maybe_auto_promote_passing_trial call.
    # --verify-all additionally reopens terminal trials that FAILED the rerun
    # (PySpark _maybe_reopen_trial_after_phase_b_failure parity).
    if verify_all:
        for tid in recorded:
            b_iters = state.get("trials", {}).get(tid, {}).get("phase_b_iters") or []
            if not b_iters:
                continue
            last = b_iters[-1]
            state = _maybe_reopen_trial(
                state, tid, last.get("passing", 0), last.get("failing", 0),
                last.get("iter", len(b_iters)), conv_root, workspace,
            )
    state, promoted = recover_pending_trials(state)
    if promoted:
        print(f"[scos-control] auto-promoted {promoted} trial(s) after Phase B")
    state = advance_phase(state, conv_root)   # phase_a_done → phase_b_done when all trials terminal

    save_state(conv_root, state)
    append_event(workspace, {"kind": "phase_b_complete", "sbt_rc": sbt_rc,
                             "trials": recorded})
    deselected = len(pre_sbt_terminal)
    print(f"[scos-control] Phase B done: {len(recorded)} trial(s) run, "
          f"{deselected} terminal skipped, sbt_rc={sbt_rc}")
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


def _provision_golden_schemas(
    conv_root: Path,
    state: dict | None = None,
    *,
    force_reseed: bool = False,
) -> tuple[int, dict]:
    """Provision Snowflake golden schemas in-process (PySpark hash-gated parity).

    Always invokes the shared ``provision_golden_schemas`` library so per-table
    ``provision_hashes.json`` skips can reseed only changed schemas after inline
    repair. Early-skip only when already provisioned AND not forcing — wait: for
    true PySpark parity we always call the provisioner; hash matching makes
    unchanged tables cheap. ``force_reseed`` clears the hash store so every table
    is recreated/reloaded.

    Returns (exit_code, updated_state).
    """
    conv_root = conv_root.expanduser().resolve()
    state = state or load_state(conv_root)
    workspace = validation_root(conv_root)

    if force_reseed:
        hashes = workspace / "shared" / "provision_hashes.json"
        if hashes.is_file():
            hashes.unlink()
            print(f"[scos-control] force-reseed: cleared {hashes}")
        # Keep golden schema names but force the provisioner to rewrite table contents.
        state.setdefault("snowflake", {})["provisioned"] = False
    elif not _needs_provision(state):
        # Still re-enter the hash-gated provisioner so schema edits after the first
        # provision reseeds changed tables (PySpark driver behavior). Only skip when
        # the caller explicitly opted out via env SCOS_SKIP_PROVISION=1.
        if os.environ.get("SCOS_SKIP_PROVISION", "").strip() in ("1", "true", "yes"):
            print("[scos-control] golden schemas already provisioned — "
                  "SCOS_SKIP_PROVISION=1, skipping")
            return 0, state
        print("[scos-control] golden schemas present — re-entering hash-gated provision "
              "(unchanged tables skipped via provision_hashes.json)")

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
    PySpark — hash-gated via Validation/shared/provision_hashes.json), and writes
    state["snowflake"]["golden_schemas"] + milestone back.

    Called from SKILL.md Step 6 after schema_mine.py + datagen:
        python scos_state.py provision --conv-root $CONVERSION_ROOT
    After inline schema repair, re-run provision (or run-phase-b) — changed tables
    reseed automatically. Use --force-reseed to clear hashes and reload everything.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    rc, _ = _provision_golden_schemas(
        conv_root, force_reseed=bool(getattr(args, "force_reseed", False)),
    )
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
    trials = state.get("trials") or {}
    if args.trial_id not in trials:
        return _die(f"trial '{args.trial_id}' not found in state.json")
    col = args.column.upper()
    scope = (getattr(args, "scope", None) or "data").strip().lower()
    if scope not in ("data", "udf", "serialization", "both"):
        return _die(f"--scope must be data|udf|serialization|both (got {scope!r})", 2)
    entry = {
        "sink_id": args.sink_id, "column": col, "reason": args.reason,
        "baseline_sample": args.baseline_sample or "", "shadow_sample": args.shadow_sample or "",
        "documented_at_iter": args.iter if args.iter is not None else 0,
        "scope": scope,
    }
    trial = trials[args.trial_id]
    existing = trial.get("documented_divergences") or []
    idx = next((i for i, d in enumerate(existing)
                if d.get("sink_id") == args.sink_id and (d.get("column") or "").upper() == col), -1)
    trial["documented_divergences"] = (existing[:idx] + [entry] + existing[idx + 1:]) if idx >= 0 else existing + [entry]
    save_state(conv_root, state)

    div_entry = {
        "column": col,
        "reason": args.reason,
        "baseline_sample": args.baseline_sample or "",
        "shadow_sample": args.shadow_sample or "",
        "scope": scope,
    }
    manifest_path = schemas_manifest_path(conv_root)
    if manifest_path.is_file():
        manifest = load_schemas_manifest(conv_root)
        exp = manifest.setdefault("expected_divergences", {})
        _merge_expected_divergence_entry(
            exp,
            trial_id=args.trial_id,
            sink_id=args.sink_id,
            column=col,
            div_entry=div_entry,
            scope=scope,
        )
        save_schemas_manifest(conv_root, manifest)
        ensure_analysis_shim_from_schemas(conv_root)
    else:
        analysis = load_analysis(conv_root)
        exp = analysis.get("expected_divergences") or {}
        _merge_expected_divergence_entry(
            exp,
            trial_id=args.trial_id,
            sink_id=args.sink_id,
            column=col,
            div_entry=div_entry,
            scope=scope,
        )
        analysis["expected_divergences"] = exp
        save_analysis(conv_root, analysis)
    print(f"[scos-control] documented divergence: {args.trial_id}/{args.sink_id}/{col} (scope={scope})")
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
            "error_hash": f"needs:{(args.reason or '')[:70]}" if args.reason else "unselected_dep",
            "trials_affected": [args.trial_id], "outcome": "no_change"}
    state["fixer_dispatches"] = (state.get("fixer_dispatches") or []) + [fake]
    trials[args.trial_id] = {
        **trials[args.trial_id],
        "status": "passed_no_baseline",
        "dependency_note": args.reason,
    }
    trials[args.trial_id].pop("hard_stuck_reason", None)
    state["trials"] = trials
    state = advance_phase(state, conv_root)
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


# ---------------------------------------------------------------------------
# known-patches suggest  (Scala-native; PySpark parity of artifact contract)
# ---------------------------------------------------------------------------

def _cmd_known_patches_suggest(args) -> int:
    """Scan Validation/source + Output with Scala-native detectors.

    Writes (same shapes as PySpark validate.py known-patches suggest):
      - ``Validation/shared/known_patch_suggestions.json`` — confident auto-patches
      - ``Validation/shared/patch_investigation.json`` — residual worklist

    Also seeds ``expected_divergences`` entries with ``scope=udf`` from AST udfs
    so Phase B ClassNotFound on JVM UDFs can be treated as documented divergence.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    analysis = load_analysis(conv_root)
    shared = conv_root / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)

    _scripts = Path(__file__).resolve().parent
    if str(_scripts) not in sys.path:
        sys.path.insert(0, str(_scripts))
    try:
        import scala_patch_engine as _spe  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        return _die(f"cannot import scala_patch_engine: {exc}", 2)

    trees = [
        ("Validation/source", conv_root / "Validation" / "source"),
        ("Output", conv_root / "Output"),
    ]
    all_suggestions: list = []
    all_sites: list = []
    seen_files: set = set()
    for _label, root in trees:
        if not root.is_dir():
            continue
        for scala_path in sorted(root.rglob("*.scala")):
            try:
                rel = str(scala_path.relative_to(conv_root))
            except ValueError:
                continue
            if rel in seen_files:
                continue
            seen_files.add(rel)
            try:
                text = scala_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                print(f"[scos-control] WARN: known-patches skip {rel}: {exc}", file=sys.stderr)
                continue
            for s in _spe.suggest_known_patches(text, rel):
                all_suggestions.append(s)
            for site in _spe.scan_investigation_sites(text, rel):
                all_sites.append(site)

    sug_path = shared / "known_patch_suggestions.json"
    write_atomic(sug_path, {"patches": all_suggestions})

    by_category: dict = {}
    for site in all_sites:
        by_category[site["category"]] = by_category.get(site["category"], 0) + 1
    invest_path = shared / "patch_investigation.json"
    write_atomic(invest_path, {"summary": by_category, "sites": all_sites})

    udf_added = _spe.seed_udf_expected_divergences(analysis)
    if udf_added:
        manifest_path = schemas_manifest_path(conv_root)
        if manifest_path.is_file():
            manifest = load_schemas_manifest(conv_root)
            exp = manifest.setdefault("expected_divergences", {})
            for key, divs in (analysis.get("expected_divergences") or {}).items():
                if key.endswith(".__udf__"):
                    exp[key] = divs
            save_schemas_manifest(conv_root, manifest)
            ensure_analysis_shim_from_schemas(conv_root)
        else:
            save_analysis(conv_root, analysis)

    print(
        f"[scos-control] known-patches suggest: {len(all_suggestions)} auto-patch suggestion(s), "
        f"{len(all_sites)} investigation site(s) across {len(seen_files)} file(s)"
        + (f"; seeded {udf_added} udf expected_divergence(s)" if udf_added else "")
        + f" → {sug_path}"
    )
    return 0


# ---------------------------------------------------------------------------
# Transient startup retry (PySpark scos-runner parity)
# ---------------------------------------------------------------------------

_TRANSIENT_STARTUP_RE = re.compile(
    r"(?i)\b(?:4001|UNAVAILABLE|DEADLINE_EXCEEDED|failed to connect|connection refused|"
    r"channel.?closed|timed out after|warehouse.*(resum|suspend)|Could not connect)\b"
)

# Patterns for orphaned local Snowpark Connect Python servers left after a hung trial.
_STALE_SCOS_PKILL_PATTERNS = (
    "snowpark_connect",
    "snowflake.snowpark_connect",
    "snowpark.connect",
)


def _log_looks_transient(log_path: Path) -> bool:
    if not log_path.is_file():
        return False
    try:
        # Tail ~200 KB — enough for sbt failure summaries without reading multi-MB logs.
        data = log_path.read_bytes()
        text = data[-200_000:].decode("utf-8", errors="replace")
    except OSError:
        return False
    return bool(_TRANSIENT_STARTUP_RE.search(text))


def _kill_stale_scos_servers() -> List[str]:
    """Best-effort kill of orphaned local SCOS Python servers before a retry.

    Hung trials leave daemon Python servers that steal ports / hang the next
    gRPC handshake. Timeouts are symptoms — clear the stale process first, then
    retry with a longer ``SCOS_TRIAL_TIMEOUT_SECS``.
    """
    killed: List[str] = []
    if os.name == "nt":
        return killed
    for pattern in _STALE_SCOS_PKILL_PATTERNS:
        try:
            proc = subprocess.run(
                ["pkill", "-f", pattern],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            # pkill: 0 = matched, 1 = no match — both OK; other codes = error.
            if proc.returncode == 0:
                killed.append(pattern)
        except (OSError, subprocess.TimeoutExpired):
            continue
    if killed:
        print(
            f"[scos-control] killed stale SCOS server process(es) matching: "
            f"{', '.join(killed)}",
            file=sys.stderr,
        )
    return killed


def _run_sbt_with_transient_retry(
    *,
    cmd: list,
    cwd: str,
    env: dict,
    log_path: Path,
    label: str,
) -> int:
    """Run sbt once; on transient startup errors, kill stale SCOS and retry once.

    Mirrors PySpark agent policy: 4001 / UNAVAILABLE / DEADLINE_EXCEEDED / connect
    failures before a stable session are environment cold-start, not hard_stuck.
    Kill orphaned local servers before the longer-timeout retry.
    """
    sbt_rc = _run_sbt_streaming(cmd, cwd=cwd, env=env, log_path=log_path)
    if sbt_rc == 0 or not _log_looks_transient(log_path):
        return sbt_rc
    print(
        f"[scos-control] {label}: transient startup signature in log — "
        "killing stale SCOS servers, then retrying once with "
        "SCOS_TRIAL_TIMEOUT_SECS=900",
        file=sys.stderr,
    )
    _kill_stale_scos_servers()
    retry_env = dict(env)
    retry_env["SCOS_TRIAL_TIMEOUT_SECS"] = "900"
    retry_log = log_path.with_name(log_path.stem + "_retry" + log_path.suffix)
    sbt_rc2 = _run_sbt_streaming(cmd, cwd=cwd, env=retry_env, log_path=retry_log)
    try:
        if retry_log.is_file():
            log_path.write_bytes(retry_log.read_bytes())
    except OSError:
        pass
    print(f"[scos-control] {label}: transient retry exited: {sbt_rc2}")
    return sbt_rc2


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

    entries = _normalize_patch_entries(entries)

    # SCOS env-ref audit (PySpark validate.py patch-add parity)
    try:
        import validate as _validate_mod  # type: ignore[import]
        audit_warns = _validate_mod._audit_patch_scos_env_refs(conv_root, entries)
        if audit_warns:
            if getattr(args, "force", False):
                for w in audit_warns:
                    print(f"[patch-add] WARN: {w}", file=sys.stderr)
            else:
                for w in audit_warns:
                    print(f"[patch-add] ERROR: {w}", file=sys.stderr)
                return _die(
                    "SCOS_INPUT/SINK/TEST_AUX ids above are not declared for any "
                    "entrypoint — fix the patch or re-run with --force",
                    2,
                )
        for hint in _validate_mod._audit_patch_glob_opportunity(conv_root, entries):
            print(f"[patch-add] HINT: {hint}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[patch-add] WARN: skipped env-ref audit ({exc})", file=sys.stderr)

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


# ---------------------------------------------------------------------------
# Worktree helpers for parallel batch orchestration
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

    # Write fresh state.json with a unique run_id.
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
            "synth_survey", "entrypoints_selected", "synth_deep", "patches_authored",
            "workload_built", "tests_authored", "venv_prewarmed", "snowflake_provisioned",
            "phase_a_complete", "phase_b_complete")},
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
    *, primary_conv: Path | None = None,
) -> None:
    """Scope analysis.json + schemas/ to batch ep_ids and register pending trials.

    Called by prepare-batches for each worktree; not a public CLI subcommand.
    When primary_conv is set and schemas/ exists there, copy+prune schemas/ into
    the worktree (PySpark parity). Always keeps a scoped analysis.json for the
    JVM harness (regenerated from schemas when possible).
    """
    id_set = set(ep_ids)
    all_eps = (primary_analysis.get("entrypoints")
               or primary_analysis.get("entrypoint_candidates")
               or [])
    selected = [ep for ep in all_eps if ep.get("id") in id_set]

    # Prefer schemas/ as SoT: copy from primary then prune.
    if primary_conv is not None and schemas_manifest_path(primary_conv).is_file():
        _copy_schemas_to_worktree(primary_conv, worktree_path)
        man = load_schemas_manifest(worktree_path)
        if man:
            _prune_schemas_to_selected(worktree_path, man, id_set)
            ensure_analysis_shim_from_schemas(worktree_path)
        else:
            scoped = dict(primary_analysis)
            scoped["entrypoints"] = selected
            scoped["entrypoint_candidates"] = selected
            save_analysis(worktree_path, scoped)
    else:
        # Legacy: scope analysis.json only; schema_mine runs later in synthesizer.
        scoped = dict(primary_analysis)
        scoped["entrypoints"] = selected
        scoped["entrypoint_candidates"] = selected
        save_analysis(worktree_path, scoped)

    # Register pending trials; remove stale ones from prior runs.
    state = load_state(worktree_path)
    new_ids = {ep.get("id") for ep in selected if ep.get("id")}
    # If schemas provided the selection, prefer those ids.
    if not new_ids and id_set:
        new_ids = set(id_set)
        selected = [{"id": i} for i in sorted(new_ids)]
    state.setdefault("trials", {})
    for ep in selected:
        ep_id = ep.get("id", "unknown")
        state["trials"].setdefault(
            ep_id, {"status": "pending", "phase_a_iters": [], "phase_b_iters": []},
        )
    # Also register any id_set members missing from selected (schemas-only path).
    for ep_id in id_set:
        state["trials"].setdefault(
            ep_id, {"status": "pending", "phase_a_iters": [], "phase_b_iters": []},
        )
    stale = [tid for tid in list(state["trials"]) if tid not in id_set]
    for tid in stale:
        del state["trials"][tid]
    state.setdefault("milestones", {})["entrypoints_selected"] = True
    save_state(worktree_path, state)
    print(f"[scos-control] {worktree_path.name}: selected {len(id_set)} ep(s): "
          f"{sorted(id_set)}")


# ---------------------------------------------------------------------------
# scope-entrypoints
# ---------------------------------------------------------------------------


def _cmd_schemas_to_analysis(args) -> int:
    """Regenerate analysis.json from schemas/ (generated JVM shim)."""
    conv_root = Path(args.conv_root).expanduser().resolve()
    if not schemas_manifest_path(conv_root).is_file():
        return _die("schemas/manifest.json not found — run schema_mine first", 2)
    ensure_analysis_shim_from_schemas(conv_root)
    return 0


def _cmd_scope_entrypoints(args) -> int:
    """Scope schemas/ (preferred) and analysis.json to a subset of entrypoints by --ids.

    Run BEFORE Step 2 sectioning to restrict validation to a subset.
    Prefer schemas/manifest.json when present (PySpark parity); always keep
    analysis.json in sync via shim or direct prune.
    """
    conv_root = Path(args.conv_root).expanduser().resolve()
    keep_ids = {x.strip() for x in (args.ids or "").split(",") if x.strip()}
    if not keep_ids:
        return _die("--ids is required and must be a non-empty comma-separated list")

    man = load_schemas_manifest(conv_root)
    if man.get("entrypoints"):
        known = {ep.get("id") for ep in man["entrypoints"]}
        unknown = sorted(i for i in keep_ids if i not in known)
        if unknown:
            return _die(f"unknown entrypoint id(s) not in schemas/manifest: {unknown}")
        removed = _prune_schemas_to_selected(conv_root, man, keep_ids)
        ensure_analysis_shim_from_schemas(conv_root)
        # Also prune analysis.json if present (dual-read compat).
        analysis = load_analysis(conv_root) if analysis_path(conv_root).is_file() else {}
        if analysis:
            cands = analysis.get("entrypoint_candidates") or analysis.get("entrypoints") or []
            selected = [ep for ep in cands if ep.get("id") in keep_ids]
            analysis["entrypoints"] = selected
            analysis["entrypoint_candidates"] = selected
            save_analysis(conv_root, analysis)
        print(f"[scos-control] scoped schemas/ to {len(keep_ids)} entrypoint(s); "
              f"kept {sorted(keep_ids)}; removed {removed} unselected")
        return 0

    analysis = load_analysis(conv_root)
    cands = analysis.get("entrypoint_candidates") or analysis.get("entrypoints") or []
    if not cands:
        return _die("no entrypoints in schemas/ or analysis.json — run schema_mine first")
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

    # Prefer schemas/manifest for batch weights (PySpark parity); fall back to analysis.json.
    man = load_schemas_manifest(conv_root_primary)
    if man.get("entrypoints"):
        manifest = man
        # Build a dual-read analysis view for worktree scoping when analysis is stale.
        analysis = load_analysis(conv_root_primary) if analysis_path(conv_root_primary).is_file() else {}
        if not (analysis.get("entrypoints") or analysis.get("entrypoint_candidates")):
            analysis = {
                "entrypoints": [
                    {"id": r["id"], "path": r.get("path"), "weight": r.get("weight")}
                    for r in man["entrypoints"] if r.get("id")
                ],
            }
    else:
        analysis = load_analysis(conv_root_primary)
        eps = analysis.get("entrypoints") or analysis.get("entrypoint_candidates") or []
        if not eps:
            return _die(
                "no entrypoints — run schema_mine.py (source → schemas/) first",
                2,
            )
        manifest = {"entrypoints": eps}

    manifest = _normalize_manifest_weights(manifest)

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

            # Step 3: scope schemas/ + analysis.json to this batch's ep_ids.
            _select_eps_for_worktree(
                worktree_path, analysis, ep_ids, primary_conv=conv_root_primary,
            )

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
# harvest
# ---------------------------------------------------------------------------


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
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scos_state.py",
                                description="Scala validator state machine (Python port of ScosState).")
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
    st.add_argument("--phase", choices=["A", "B", "all"], default="all",
                    help="With --verbose, limit iter detail to Phase A, B, or all")
    st.add_argument("--verbose", action="store_true",
                    help="Print per-trial Phase A/B iter detail")
    st.set_defaults(func=_cmd_status)

    cr(sub.add_parser("summary")).set_defaults(func=_cmd_summary)
    cr(sub.add_parser("build-index")).set_defaults(func=_cmd_build_index)
    cr(sub.add_parser("put-schemas")).set_defaults(func=_cmd_put_schemas)
    cr(sub.add_parser("migrate-divergences")).set_defaults(func=_cmd_migrate_divergences)

    pa = cr(sub.add_parser("patch-add"))
    pa.add_argument("--from-file", required=True)
    pa.add_argument("--no-commit", action="store_true")
    pa.add_argument("--force", action="store_true",
                    help="Downgrade the SCOS env-ref audit failure to a warning and apply patches anyway")
    pa.set_defaults(func=_cmd_patch_add)

    # known-patches suggest (parity with PySpark validate.py known-patches suggest)
    kp = cr(sub.add_parser("known-patches",
                            help="Known-patches library operations"))
    kp_sub = kp.add_subparsers(dest="kp_cmd")
    kp_sug = cr(kp_sub.add_parser("suggest",
                help="Scala-native scan of Validation/source + Output; write "
                     "known_patch_suggestions.json + patch_investigation.json "
                     "(and seed udf expected_divergences)"))
    kp_sug.set_defaults(func=_cmd_known_patches_suggest)
    kp.set_defaults(func=lambda a: _die("known-patches requires a subcommand: suggest", 2))

    ri = cr(sub.add_parser("record-iter"))
    ri.add_argument("--trial-id", required=True)
    ri.add_argument("--phase", required=True)
    ri.add_argument("--iter", type=int, required=True)
    ri.add_argument("--passing", type=int, required=True)
    ri.add_argument("--failing", type=int, required=True)
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
    rts.add_argument("--harness-repair-exhausted", action="store_true",
                     help="Allow hard_stuck for a harness/kit defect after a recorded "
                          "harness_failure repair round (fix the copied kit under "
                          "Validation/tests/ first).")
    rts.add_argument("--patch-repair-exhausted", action="store_true",
                     help="Allow hard_stuck for an un-patchable I/O dependency after a "
                          "recorded patch_failure round (add blueprint patches with "
                          "patch-add first).")
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

    ms = cr(sub.add_parser("record-milestone"))
    ms.add_argument("--milestone", required=True)
    ms.set_defaults(func=_cmd_record_milestone)

    cr(sub.add_parser("prewarm")).set_defaults(func=_cmd_prewarm)

    pf = cr(sub.add_parser("preflight",
                           help="Hard environment-readiness gate for Phase A/B: verifies a "
                                "Java 8/11/17 JDK (auto-provisions Temurin 17 if absent), sbt, "
                                "and (phase b) the SCOS client jar. Exit 3 if not ready."))
    pf.add_argument("--phase", choices=["a", "b"], default="a",
                    help="Which phase to check readiness for (default: a)")
    pf.set_defaults(func=_cmd_preflight)

    pr = cr(sub.add_parser("phase-reset",
                            help="Clear stale phase artefacts before switching phases: wipes "
                                 "Validation/tests/target/ (compiled sbt classes) and rendered "
                                 "specs in tests/src/test/scala/phase_{a,b}/ (plus legacy flat "
                                 "tests/src/test/scala/*.scala), then resets the tests_authored "
                                 "milestone so Phase B re-renders fresh specs."))
    pr.add_argument("--to", choices=["a", "b"], required=True,
                    help="Target phase to reset to. Both 'a' and 'b' wipe target/ and specs. "
                         "'b' additionally resets the tests_authored milestone.")
    pr.add_argument("--clear-results", dest="clear_results", action="store_true",
                    help="(--to b only) Also clear Phase A trial outputs (results/phase_a/). "
                         "Default: keep Phase A baselines for comparison.")
    pr.set_defaults(func=_cmd_phase_reset)

    pv = cr(sub.add_parser(
        "prevalidate",
        help="Single-pass static validation gate: aggregates preflight, mock-data, "
             "column_check, dep_check, sbt compile, entry-class, and (phase b) "
             "SCOS venv + I/O completeness checks. "
             "Exit 0 = all clear, 1 = blocking findings, 2 = warnings only. "
             "Re-run is cached by hash of source files + analysis.json + patches.",
    ))
    pv.add_argument("--phase", choices=["a", "b"], default="a",
                    help="Phase to validate: 'a' (source-side) or 'b' (migrated + SCOS). "
                         "Default: a")
    pv.add_argument("--force", action="store_true",
                    help="Bypass the cache and re-run all checks unconditionally.")
    pv.set_defaults(func=_cmd_prevalidate)

    bd = cr(sub.add_parser(
        "build-doctor",
        help="Prove a workload builds (assembly → thin jar + classpath). "
             "JSON report to stdout; no tests run. Scala analogue of PySpark seed-venv. "
             "Use --side source (Phase A) or --side migrated (Phase B Output/).",
    ))
    bd.add_argument("--side", choices=["source", "migrated", "output"], default="source",
                    help="Which tree to prove: Validation/source (default) or Output/")
    bd.add_argument("--source-dir", default=None,
                    help="Override project dir when --side source "
                         "(default: <conv>/Validation/source)")
    bd.add_argument("--output", default=None,
                    help="Write JSON report to this path (default: stdout)")
    bd.add_argument("--force-rebuild", action="store_true",
                    help="Force a full rebuild even when an existing jar is found "
                         "(Speed 2 bypass).")
    bd.set_defaults(func=_cmd_build_doctor)

    rpa = cr(sub.add_parser("run-phase-a",
                             help="Deterministic Phase A: copy kit, render specs, sbt test (source)"))
    rpa.add_argument("--parallelism", type=int, default=None,
                     help="SCOS_TEST_PARALLELISM for sbt. When omitted, auto-cap from "
                          "host RAM (<8GiB→1, <16GiB→2, else 4). Explicit N always wins.")
    rpa.add_argument("--trial-id", default=None,
                     help="Run Phase A for a single trial only (all others deselected). "
                          "Useful after schema repair to re-establish just one baseline.")
    rpa.add_argument("--no-mock-guard", action="store_true",
                     help="Skip the pre-flight mock-data guard (schema_mine + datagen "
                          "seed/verify). Only for a deliberate re-run where mocks are "
                          "already known good.")
    rpa.add_argument("--verify-all", action="store_true",
                     help="Re-run ALL trials, including terminal ones. Useful for "
                          "re-establishing a baseline after a schema change. Equivalent "
                          "to Phase B --verify-all.")
    rpa.add_argument("--force-rebuild", action="store_true",
                     help="Force a full source-jar rebuild even when an existing jar is "
                          "detected (Speed 2 bypass). Use after editing build.sbt when "
                          "the jar mtime check would otherwise reuse a stale artifact.")
    rpa.set_defaults(func=_cmd_run_phase_a)

    rpb = cr(sub.add_parser("run-phase-b",
                             help="Deterministic Phase B: derive SPARK_REMOTE, sbt test (migrated)"))
    rpb.add_argument("--parallelism", type=int, default=None,
                     help="SCOS_TEST_PARALLELISM for sbt. When omitted, auto-cap from "
                          "host RAM (<8GiB→1, <16GiB→2, else 4). Explicit N always wins.")
    rpb.add_argument("--trial-id", default=None,
                     help="Run Phase B for a single trial only (all others deselected). "
                          "Equivalent to PySpark run-tests --trial-id.")
    rpb.add_argument("--verify-all", action="store_true",
                     help="Re-run ALL trials, including terminal ones. A previously "
                          "terminal trial that fails the rerun is reopened to pending "
                          "(regression detection). Equivalent to PySpark run-tests "
                          "--verify-all.")
    rpb.add_argument("--force-recompile", action="store_true",
                     help="Always wipe test-classes/ and force a full kit recompile "
                          "before Phase B (Speed 6 bypass). Use when Zinc incremental "
                          "compile produces stale bytecode.")
    rpb.set_defaults(func=_cmd_run_phase_b)

    prov = cr(sub.add_parser("provision",
                             help="Hash-gated Snowflake golden provision "
                                  "(shared PySpark provisioner; reseeds changed tables)"))
    prov.add_argument("--force-reseed", action="store_true",
                      help="Clear provision_hashes.json and reload every table")
    prov.set_defaults(func=_cmd_provision)

    dd = cr(sub.add_parser("document-divergence"))
    dd.add_argument("--trial-id", required=True)
    dd.add_argument("--sink-id", required=True)
    dd.add_argument("--column", required=True)
    dd.add_argument("--reason", required=True)
    dd.add_argument("--baseline-sample", default=None)
    dd.add_argument("--shadow-sample", default=None)
    dd.add_argument("--iter", type=int, default=None)
    dd.add_argument("--scope", default="data",
                    help="data|udf|serialization|both (default data). "
                         "udf/serialization document JVM-UDF SCOS limitations.")
    dd.set_defaults(func=_cmd_document_divergence)

    meb = cr(sub.add_parser("mark-empty-baseline"))
    meb.add_argument("--trial-id", required=True)
    meb.add_argument("--sink-id", required=True)
    meb.set_defaults(func=_cmd_mark_empty_baseline)

    fd = cr(sub.add_parser("record-fixer-dispatch"))
    fd.add_argument("--iter", type=int, required=True, help="Phase B iteration number")
    fd.add_argument("--error-class", required=True,
                    choices=["harness_failure", "patch_failure", "workload_failure",
                             "assertion_failure", "unselected_dependency"])
    fd.add_argument("--error-hash", required=True,
                    help="First 80 chars of exception msg (stripped of query IDs/timestamps)")
    fd.add_argument("--trial-ids", dest="trial_ids", default="",
                    help="Comma-separated trial IDs affected by this error class")
    fd.add_argument("--trial-id", dest="trial_ids",
                    help="Singular alias for --trial-ids (agent docs)")
    fd.add_argument("--outcome", required=True,
                    choices=["success", "no_change", "partial"],
                    help="Fixer outcome for this dispatch")
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

    # scope-entrypoints — pre-sectioning subset filter (no state.json required)
    sce = cr(sub.add_parser("scope-entrypoints",
                             help="Scope schemas/ (and analysis.json) to a subset of entrypoints"))
    sce.add_argument("--ids", required=True,
                     help="Comma-separated entrypoint IDs to keep")
    sce.set_defaults(func=_cmd_scope_entrypoints)

    # schemas-to-analysis — regenerate JVM analysis.json shim from schemas/
    shim = cr(sub.add_parser(
        "schemas-to-analysis",
        help="Regenerate Validation/shared/analysis.json from schemas/ (JVM shim)",
    ))
    shim.set_defaults(func=_cmd_schemas_to_analysis)

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
