#!/usr/bin/env python3
"""
progress_bus.py — Append-only JSONL event bus for the spark-migration progress UI.

All writes are file-locked so parallel fixer workers can append concurrently.
Monotonic `seq` is assigned at write time inside the lock. Every line is
flushed individually so readers never see a partial record.

CLI (used by SKILL.md inline blocks):
    python3 progress_bus.py run-init   --run <dir> --data '<json>'
    python3 progress_bus.py phase-start --run <dir> --phase <name> [--step <id>] [--path <scos|sma>]
    python3 progress_bus.py phase-end   --run <dir> --phase <name> [--step <id>]
    python3 progress_bus.py milestone   --run <dir> --phase <name> --message <text> [--step <id>]
    python3 progress_bus.py agent-status --run <dir> --worker <id> --status <started|in_progress|finished> [--phase <name>] [--message <text>]
    python3 progress_bus.py file-progress --run <dir> --file <path> --status <converted|verified|failed|reverted> [--worker <id>] [--phase <name>] [--data '<json>']
    python3 progress_bus.py report-ready --run <dir> --file <path> [--phase <name>] [--message <text>]
    python3 progress_bus.py metric       --run <dir> --phase <name> --key <k> --value <v> [--message <text>]
    python3 progress_bus.py error        --run <dir> --phase <name> --message <text> [--data '<json>']
    python3 progress_bus.py summary      --run <dir> --data '<json>'
    python3 progress_bus.py tail         --run <dir> [--since <seq>]
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

EVENTS_FILE = "events.jsonl"
SEQ_FILE = ".seq"         # tiny counter file inside .migration-ui/
UI_DIR = ".migration-ui"


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _ui_dir(run_dir: str) -> str:
    return os.path.join(run_dir, UI_DIR)


def _events_path(run_dir: str) -> str:
    return os.path.join(_ui_dir(run_dir), EVENTS_FILE)


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(run_dir: str, event_type: str, **fields: Any) -> int:
    """Append one event to events.jsonl. Returns the assigned seq number."""
    ui = _ui_dir(run_dir)
    os.makedirs(ui, exist_ok=True)
    events_path = os.path.join(ui, EVENTS_FILE)
    seq_path = os.path.join(ui, SEQ_FILE)

    with open(events_path, "a", encoding="utf-8") as fh:
        # Acquire exclusive lock for seq assignment + append (crash-safe)
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            # Assign monotonic seq
            seq = 0
            if os.path.exists(seq_path):
                with open(seq_path, "r") as sf:
                    try:
                        seq = int(sf.read().strip()) + 1
                    except ValueError:
                        seq = 0
            with open(seq_path, "w") as sf:
                sf.write(str(seq))

            event: dict[str, Any] = {
                "ts": _ts(),
                "seq": seq,
                "type": event_type,
            }
            event.update({k: v for k, v in fields.items() if v is not None})
            line = json.dumps(event, separators=(",", ":"))
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

    return seq


def read_events(run_dir: str, since: int = -1) -> list[dict]:
    """Return all events with seq > since."""
    path = _events_path(run_dir)
    if not os.path.exists(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if ev.get("seq", -1) > since:
                    events.append(ev)
            except json.JSONDecodeError:
                pass
    return events


def tail_events(run_dir: str, since: int = -1) -> None:
    """Print events as newline-delimited JSON, flushing each line (for pipes)."""
    for ev in read_events(run_dir, since):
        print(json.dumps(ev), flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_data(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[progress_bus] WARNING: --data is not valid JSON ({exc}), ignoring", file=sys.stderr)
        return None


def _cmd_run_init(args: argparse.Namespace) -> None:
    data = _parse_data(args.data) or {}
    emit(args.run, "run_init",
         path=getattr(args, "path", None),
         level="milestone",
         message="Migration run started",
         data=data)


def _cmd_phase_start(args: argparse.Namespace) -> None:
    emit(args.run, "phase_start",
         phase=args.phase,
         step=getattr(args, "step", None),
         path=getattr(args, "path", None),
         level="milestone",
         message=f"Phase started: {args.phase}")


def _cmd_phase_end(args: argparse.Namespace) -> None:
    emit(args.run, "phase_end",
         phase=args.phase,
         step=getattr(args, "step", None),
         level="milestone",
         message=f"Phase complete: {args.phase}")


def _cmd_milestone(args: argparse.Namespace) -> None:
    emit(args.run, "milestone",
         phase=getattr(args, "phase", None),
         step=getattr(args, "step", None),
         level="milestone",
         message=args.message,
         data=_parse_data(getattr(args, "data", None)))


def _cmd_agent_status(args: argparse.Namespace) -> None:
    emit(args.run, "agent_status",
         worker=args.worker,
         phase=getattr(args, "phase", None),
         level="info",
         message=getattr(args, "message", None) or f"Worker {args.worker}: {args.status}",
         data={"status": args.status})


def _cmd_file_progress(args: argparse.Namespace) -> None:
    data = _parse_data(getattr(args, "data", None)) or {}
    data["file"] = args.file
    data["status"] = args.status
    emit(args.run, "file_progress",
         phase=getattr(args, "phase", None),
         worker=getattr(args, "worker", None),
         level="info",
         message=f"{args.status}: {args.file}",
         data=data)


def _cmd_validation_ep(args: argparse.Namespace) -> None:
    status_map = {
        "pending":            "info",
        "running":            "info",
        "passed":             "milestone",
        "passed_no_baseline": "milestone",
        "failed":             "error",
        "hard_stuck":         "error",
        "skipped":            "info",
    }
    level = status_map.get(args.status, "info")
    msg   = args.message or f"{args.ep} phase-{args.phase.upper()}: {args.status}"
    data  = {"ep_id": args.ep, "phase": args.phase, "status": args.status}
    if args.trial:  data["trial_id"] = args.trial
    if args.total is not None: data["total_eps"] = args.total
    emit(args.run, "validation_ep",
         phase=f"phase-{args.phase}",
         level=level,
         message=msg,
         data=data)


def _cmd_report_ready(args: argparse.Namespace) -> None:
    emit(args.run, "report_ready",
         phase=getattr(args, "phase", None),
         level="milestone",
         message=getattr(args, "message", None) or f"Report ready: {args.file}",
         data={"file": args.file})


def _cmd_metric(args: argparse.Namespace) -> None:
    emit(args.run, "metric",
         phase=getattr(args, "phase", None),
         level="info",
         message=getattr(args, "message", None) or f"{args.key}={args.value}",
         data={"key": args.key, "value": args.value})


def _cmd_error(args: argparse.Namespace) -> None:
    emit(args.run, "error",
         phase=getattr(args, "phase", None),
         level="error",
         message=args.message,
         data=_parse_data(getattr(args, "data", None)))


def _cmd_summary(args: argparse.Namespace) -> None:
    data = _parse_data(args.data) or {}
    emit(args.run, "summary",
         level="milestone",
         message="Migration complete",
         data=data)


def _cmd_tail(args: argparse.Namespace) -> None:
    since = getattr(args, "since", -1) or -1
    tail_events(args.run, int(since))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="progress_bus")
    sub = p.add_subparsers(dest="cmd", required=True)

    def _run(sp):
        sp.add_argument("--run", required=True, help="Output folder (run root)")

    # run-init
    s = sub.add_parser("run-init")
    _run(s); s.add_argument("--path"); s.add_argument("--data")

    # phase-start / phase-end
    for name in ("phase-start", "phase-end"):
        s = sub.add_parser(name)
        _run(s); s.add_argument("--phase", required=True); s.add_argument("--step"); s.add_argument("--path")

    # milestone
    s = sub.add_parser("milestone")
    _run(s); s.add_argument("--phase"); s.add_argument("--step"); s.add_argument("--message", required=True); s.add_argument("--data")

    # agent-status
    s = sub.add_parser("agent-status")
    _run(s); s.add_argument("--worker", required=True)
    s.add_argument("--status", required=True, choices=["started", "in_progress", "finished"])
    s.add_argument("--phase"); s.add_argument("--message")

    # file-progress
    s = sub.add_parser("file-progress")
    _run(s); s.add_argument("--file", required=True)
    s.add_argument("--status", required=True, choices=["converted", "verified", "failed", "reverted", "skipped"])
    s.add_argument("--worker"); s.add_argument("--phase"); s.add_argument("--data")

    # report-ready
    s = sub.add_parser("report-ready")
    _run(s); s.add_argument("--file", required=True); s.add_argument("--phase"); s.add_argument("--message")

    # metric
    s = sub.add_parser("metric")
    _run(s); s.add_argument("--phase", required=True)
    s.add_argument("--key", required=True); s.add_argument("--value", required=True); s.add_argument("--message")

    # error
    s = sub.add_parser("error")
    _run(s); s.add_argument("--phase"); s.add_argument("--message", required=True); s.add_argument("--data")

    # validation-ep: per-entrypoint phase A/B progress
    s = sub.add_parser("validation-ep")
    _run(s)
    s.add_argument("--ep",      required=True, help="Entrypoint id")
    s.add_argument("--phase",   required=True, choices=["a", "b"], help="a = local Spark, b = SCOS")
    s.add_argument("--status",  required=True,
                   choices=["pending", "running", "passed", "passed_no_baseline", "failed", "hard_stuck", "skipped"])
    s.add_argument("--trial",   default=None, help="Optional trial id")
    s.add_argument("--message", default=None)
    s.add_argument("--total",   type=int, default=None, help="Total entrypoints in this batch")

    # summary
    s = sub.add_parser("summary")
    _run(s); s.add_argument("--data")

    # tail
    s = sub.add_parser("tail")
    _run(s); s.add_argument("--since", type=int, default=-1)

    return p


def main() -> None:
    p = _build_parser()
    args = p.parse_args()
    dispatch = {
        "run-init":     _cmd_run_init,
        "phase-start":  _cmd_phase_start,
        "phase-end":    _cmd_phase_end,
        "milestone":    _cmd_milestone,
        "agent-status": _cmd_agent_status,
        "file-progress":  _cmd_file_progress,
        "validation-ep":  _cmd_validation_ep,
        "report-ready": _cmd_report_ready,
        "metric":       _cmd_metric,
        "error":        _cmd_error,
        "summary":      _cmd_summary,
        "tail":         _cmd_tail,
    }
    try:
        dispatch[args.cmd](args)
    except Exception as exc:
        # Non-fatal: migration must not stop if the bus fails
        print(f"[progress_bus] WARNING: {exc}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
