#!/usr/bin/env python3
"""
progress_server.py — Stdlib HTTP + SSE server for the spark-migration progress UI.

Routes:
  GET /              → index.html
  GET /static/<path> → progress-ui/static/
  GET /events        → SSE stream (resumable via ?since=<seq>)
  GET /api/snapshot  → JSON snapshot of current computed state
  GET /api/run       → run.json contents
  GET /file?path=... → sandboxed file viewer (text only, scoped to run_dir)

Usage:
    python3 progress_server.py --run <output_dir> --skill-dir <skill_dir> --port <n>
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
COLLECTORS_DIR = os.path.join(HERE, "..", "collectors")
STATIC_DIR     = os.path.join(HERE, "..", "static")
UI_DIR         = ".migration-ui"

# ---------------------------------------------------------------------------
# Global state (set by main before server starts)
# ---------------------------------------------------------------------------

RUN_DIR   = ""
SKILL_DIR = ""

# ---------------------------------------------------------------------------
# Persistent chat worker — one Snowflake session for the whole UI lifetime
# ---------------------------------------------------------------------------

class _ChatWorker:
    """Long-lived ``ui_chat_answer.py --serve`` subprocess.

    Spawns once on first chat, reuses the same Snowflake session across turns
    so browser SSO / SAML only prompts once. Thread-safe.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None

    def _script_paths(self) -> tuple[str, str]:
        script = os.path.join(SKILL_DIR, "snowpark-connect", "scripts", "ui_chat_answer.py")
        project = os.path.join(SKILL_DIR, "snowpark-connect")
        return script, project

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _start(self) -> None:
        if self._alive():
            return
        script, project = self._script_paths()
        if not os.path.exists(script):
            raise FileNotFoundError("chat backend is not available in this install")
        try:
            self._proc = subprocess.Popen(
                ["uv", "run", "--project", project, "python", script, "--serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # line-buffered
                env={**os.environ},
            )
        except FileNotFoundError:
            # `uv` binary missing
            raise
        # Wait for the {"ready": true} handshake (up to 30s — uv may cold-start).
        assert self._proc.stdout is not None
        deadline = time.time() + 30
        while time.time() < deadline:
            line = self._proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("ready"):
                return
        # Failed to become ready — kill and surface.
        self._stop()
        raise RuntimeError("chat worker failed to start")

    def _stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin:
                proc.stdin.write(json.dumps({"op": "shutdown"}) + "\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def ask(self, payload: dict, timeout: float = 120.0) -> dict:
        with self._lock:
            try:
                self._start()
            except FileNotFoundError as exc:
                # Missing script OR missing `uv` binary both raise FileNotFoundError.
                msg = str(exc)
                if "chat backend" in msg:
                    return {"error": msg}
                return {"error": "uv is not installed on this host, so chat is unavailable"}
            except Exception as exc:
                return {"error": f"chat worker failed to start: {exc}"}

            proc = self._proc
            if proc is None or proc.stdin is None or proc.stdout is None:
                return {"error": "chat worker is not running"}

            try:
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
            except Exception as exc:
                self._stop()
                return {"error": f"chat worker write failed: {exc}"}

            # Read response with a soft timeout via a side reader thread.
            result: dict[str, Any] = {}
            err_box: list[BaseException] = []

            def _read() -> None:
                try:
                    assert proc.stdout is not None
                    while True:
                        line = proc.stdout.readline()
                        if not line:
                            err_box.append(RuntimeError("chat worker closed unexpectedly"))
                            return
                        line = line.strip()
                        if not line.startswith("{"):
                            continue
                        try:
                            result.update(json.loads(line))
                            return
                        except Exception:
                            continue
                except BaseException as exc:  # noqa: BLE001
                    err_box.append(exc)

            t = threading.Thread(target=_read, daemon=True)
            t.start()
            t.join(timeout=timeout)
            if t.is_alive():
                self._stop()
                return {"error": "the assistant took too long to respond — please retry"}
            if err_box:
                self._stop()
                return {"error": f"chat worker error: {err_box[0]}"}
            if not result:
                self._stop()
                return {"error": "no response from the assistant"}
            return result


_chat_worker = _ChatWorker()

# ---------------------------------------------------------------------------
# Event bus reader (import sibling without package install)
# ---------------------------------------------------------------------------

def _import_bus():
    bus_path = os.path.join(SKILL_DIR, "scripts", "progress_bus.py")
    spec = importlib.util.spec_from_file_location("progress_bus", bus_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_collector(name: str):
    path = os.path.join(COLLECTORS_DIR, f"{name}.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Snapshot state (rebuilt from events + collectors)
# ---------------------------------------------------------------------------

_snapshot_lock = threading.Lock()
_snapshot: dict[str, Any] = {}
_last_collector_run = 0.0
COLLECTOR_INTERVAL = 3.0  # seconds

# ---------------------------------------------------------------------------
# Weighted progress model
#
# Overall progress is a blend of pipeline stages so the headline number tracks
# real work through every phase — not just file conversion. Each stage group
# earns credit as its phases complete; the migration group is additionally
# driven by real per-file counts and the validation group by entrypoint counts.
# The weights always sum to 1.0 and the validation slice is always reserved, so
# progress is strictly monotonic: a run with no validation tops out at 80% and
# only reaches 100% when the `summary` event fires (the true completion signal),
# rather than briefly showing a false 100% and then dropping when validation
# starts.
# ---------------------------------------------------------------------------

# (group_id, weight, expected_phase_count, [phase-name substrings])
STAGE_MODEL = [
    ("assessment", 0.25, 3, ["preprocess", "sql-rewrite", "sql_rewrite", "analysis", "report-assessment"]),
    ("migration",  0.45, 2, ["migration", "imports", "header", "verification", "verify", "notebook"]),
    ("reports",    0.10, 1, ["reports"]),
    ("validation", 0.20, 2, ["validation", "survey", "phase-a", "phase-b", "harvest"]),
]

_VALIDATION_TERMINAL = {"passed", "passed_no_baseline", "failed", "hard_stuck", "skipped"}


def _match_group(phase: str) -> str | None:
    """Map a phase name to its stage group. STAGE_MODEL is ordered assessment-
    first, and the reports key is 'reports' (not 'report'), so 'report-assessment'
    correctly resolves to the assessment group rather than reports."""
    if not phase:
        return None
    p = str(phase).lower()
    for gid, _w, _n, keys in STAGE_MODEL:
        for k in keys:
            if k in p:
                return gid
    return None


def _compute_overall_pct(
    phases: dict[str, dict],
    file_fraction: float,
    ep_fraction: float | None,
    has_summary: bool,
    *,
    validation_started: bool = False,
    has_validation_complete: bool = False,
) -> int:
    """Weighted stage blend, 0..100.

    - Migration-only finish (``has_summary`` and no validation activity) → 100%.
    - Validation still running → migration groups locked full; validation slice
      advances with phase / entrypoint credit; caps at 99% until complete.
    - ``has_validation_complete`` → 100%.
    """
    if has_validation_complete:
        return 100
    if has_summary and not validation_started:
        return 100

    # Per-group accumulated phase credit (done = 1.0, running = 0.5).
    credit: dict[str, float] = {gid: 0.0 for gid, _w, _n, _k in STAGE_MODEL}
    for ph in phases.values():
        gid = _match_group(ph.get("phase"))
        if not gid:
            continue
        if ph.get("status") == "done":
            credit[gid] += 1.0
        elif ph.get("status") == "running":
            credit[gid] += 0.5

    # After migration summary, lock the non-validation groups at full credit so
    # the bar never dips when validation starts.
    if has_summary:
        for gid in ("assessment", "migration", "reports"):
            credit[gid] = max(credit[gid], next(n for g, _w, n, _k in STAGE_MODEL if g == gid))

    overall = 0.0
    for gid, w, expected, _k in STAGE_MODEL:
        prog = min(1.0, credit[gid] / expected) if expected else 0.0
        if gid == "migration":
            prog = max(prog, min(1.0, file_fraction))
        elif gid == "validation" and ep_fraction is not None:
            prog = max(prog, min(1.0, ep_fraction))
        overall += w * prog

    return max(0, min(99, round(overall * 100)))


def _rebuild_snapshot() -> dict[str, Any]:
    """Derive dashboard state from the full event log."""
    try:
        bus = _import_bus()
        events = bus.read_events(RUN_DIR, since=-1)
    except Exception:
        events = []

    phases: dict[str, dict] = {}
    workers: dict[str, dict] = {}
    files: dict[str, dict] = {}
    reports: list[dict] = []
    metrics: dict[str, Any] = {}
    run_ctx: dict = {}
    summary: dict = {}
    feed: list[dict] = []
    total_files: int | None = None
    has_summary = False
    has_validation_complete = False
    start_ts: str | None = None

    for ev in events:
        t = ev.get("type")
        feed.append(ev)

        if t == "run_init":
            run_ctx = ev.get("data", {})
            start_ts = ev.get("ts")
            total_files = run_ctx.get("total_files")

        elif t == "phase_start":
            ph = ev["phase"]
            phases[ph] = {"phase": ph, "status": "running", "start_ts": ev["ts"], "step": ev.get("step")}

        elif t == "phase_end":
            ph = ev["phase"]
            if ph in phases:
                phases[ph]["status"] = "done"
                phases[ph]["end_ts"] = ev["ts"]
            else:
                phases[ph] = {"phase": ph, "status": "done", "end_ts": ev["ts"]}

        elif t == "agent_status":
            w = ev["worker"]
            status = (ev.get("data") or {}).get("status", "unknown")
            workers[w] = {
                "worker": w,
                "status": status,
                "phase": ev.get("phase"),
                "message": ev.get("message"),
                "ts": ev["ts"],
            }

        elif t == "file_progress":
            d = ev.get("data") or {}
            fpath = d.get("file", ev.get("message", "?"))
            status = d.get("status", "unknown")
            files[fpath] = {
                "file": fpath,
                "status": status,
                "worker": ev.get("worker"),
                "added": d.get("added"),
                "removed": d.get("removed"),
                "ts": ev["ts"],
            }

        elif t == "report_ready":
            d = ev.get("data") or {}
            path = d.get("file", "")
            reports.append({"file": path, "phase": ev.get("phase"), "ts": ev["ts"]})

        elif t == "metric":
            d = ev.get("data") or {}
            metrics[d.get("key", "?")] = d.get("value")

        elif t == "milestone":
            if str(ev.get("phase") or "").lower() in ("validation-complete", "validation_complete"):
                has_validation_complete = True

        elif t == "summary":
            summary = ev.get("data") or {}
            has_summary = True
            if summary.get("validation_complete"):
                has_validation_complete = True

    active_phase = None
    for ph_name, ph in phases.items():
        if ph["status"] == "running":
            active_phase = ph_name

    # Distinct per-file counts from the deduped files map (last status per file
    # wins), so a file that progresses converted -> verified is counted once.
    converted = verified = failed = reverted = 0
    for f in files.values():
        st = f.get("status")
        if st == "converted":  converted += 1
        elif st == "verified": verified += 1
        elif st == "failed":   failed += 1
        elif st == "reverted": reverted += 1
    files_done = converted + verified

    # Validation entrypoint rollup (drives the validation stage's real progress).
    ep_phaseb: dict[str, str] = {}
    validation_total: int | None = None
    for ev in feed:
        if ev.get("type") == "validation_ep":
            d = ev.get("data") or {}
            if d.get("phase") == "b" and d.get("ep_id"):
                ep_phaseb[d["ep_id"]] = d.get("status", "")
            if d.get("total_eps") is not None:
                validation_total = d["total_eps"]

    ep_fraction: float | None = None
    if validation_total:
        terminal = sum(1 for s in ep_phaseb.values() if s in _VALIDATION_TERMINAL)
        ep_fraction = terminal / validation_total if validation_total else 0.0

    file_fraction = (files_done / total_files) if (total_files and total_files > 0) else 0.0

    validation_started = any(
        _match_group(ph.get("phase")) == "validation" for ph in phases.values()
    ) or bool(ep_phaseb)

    overall_pct = _compute_overall_pct(
        phases, file_fraction, ep_fraction, has_summary,
        validation_started=validation_started,
        has_validation_complete=has_validation_complete,
    )

    return {
        "run_ctx": run_ctx,
        "start_ts": start_ts,
        "has_summary": has_summary,
        "has_validation_complete": has_validation_complete,
        "validation_started": validation_started,
        "summary": summary,
        "active_phase": active_phase,
        "overall_pct": overall_pct,
        "phases": list(phases.values()),
        "workers": list(workers.values()),
        "files": list(files.values()),
        "reports": reports,
        "metrics": metrics,
        "file_counts": {
            "converted": converted,
            "verified": verified,
            "failed": failed,
            "reverted": reverted,
            "total": total_files,
        },
        "feed": feed[-200:],  # last 200 events for the live feed
    }


_collector_cache: dict[str, Any] = {}


def _maybe_run_collectors(snap: dict) -> None:
    global _last_collector_run
    now = time.time()

    # Collectors are throttled, but the snapshot is rebuilt fresh on every call.
    # Always apply the last cached collector output so collector-derived state
    # (reports, issues, readiness, diff stats) never flickers between refreshes.
    if now - _last_collector_run < COLLECTOR_INTERVAL:
        for k, v in _collector_cache.items():
            snap[k] = v
        return
    _last_collector_run = now

    scos = _import_collector("scos_collector")
    if scos:
        try:
            _collector_cache["scos"] = scos.collect(RUN_DIR)
        except Exception:
            pass

    git_col = _import_collector("git_collector")
    if git_col:
        try:
            _collector_cache["git"] = git_col.collect(RUN_DIR)
        except Exception:
            pass

    sqlite_col = _import_collector("sqlite_collector")
    if sqlite_col:
        try:
            _collector_cache["sqlite"] = sqlite_col.collect(RUN_DIR, SKILL_DIR)
        except Exception:
            pass

    for k, v in _collector_cache.items():
        snap[k] = v


def _merge_discovered_reports(snap: dict) -> None:
    """Fold collector-discovered on-disk reports into snap['reports'], so the
    assessment report and dashboard CSVs are always linked even without a
    `report-ready` event. Event-driven entries take precedence; dedup by path."""
    discovered = ((snap.get("scos") or {}).get("discovered_reports")) or []
    if not discovered:
        return
    existing = snap.setdefault("reports", [])
    by_real: dict[str, dict] = {}
    for r in existing:
        f = r.get("file")
        if f:
            by_real[os.path.realpath(f)] = r
    for d in discovered:
        f = d.get("file")
        if not f:
            continue
        real = os.path.realpath(f)
        if real in by_real:
            # Enrich the event-driven entry with kind/label if it lacks them.
            existing_entry = by_real[real]
            existing_entry.setdefault("kind", d.get("kind"))
            existing_entry.setdefault("label", d.get("label"))
        else:
            entry = {"file": f, "kind": d.get("kind"), "label": d.get("label"),
                     "ts": d.get("ts"), "phase": d.get("phase")}
            existing.append(entry)
            by_real[real] = entry

    # Feature the assessment report first, then IR, then everything else.
    _kind_rank = {"assessment": 0, "ir": 1, "html": 2, "csv": 3}
    snap["reports"] = sorted(
        existing, key=lambda r: _kind_rank.get(r.get("kind"), 5)
    )


def get_snapshot() -> dict:
    with _snapshot_lock:
        snap = _rebuild_snapshot()
        _maybe_run_collectors(snap)
        _merge_discovered_reports(snap)
        # Persist for instant load on reconnect
        snap_path = os.path.join(RUN_DIR, UI_DIR, "snapshot.json")
        try:
            with open(snap_path, "w") as fh:
                json.dump(snap, fh)
        except Exception:
            pass
        return snap


# ---------------------------------------------------------------------------
# Chat grounding — compact plaintext status the assistant answers from
# ---------------------------------------------------------------------------

def _chat_connection_name(snap: dict | None = None) -> str:
    """Snowflake connection the chat should use.

    Preference order: the connection the migration run actually used
    (`run_init` event data → snapshot run_ctx.connection_name), then any
    connection_name persisted in run.json, then 'default'. This keeps the chat
    on the same Cortex-enabled connection as the run instead of guessing."""
    if snap:
        conn = (snap.get("run_ctx") or {}).get("connection_name")
        if conn:
            return conn
    path = os.path.join(RUN_DIR, UI_DIR, "run.json")
    try:
        data = json.loads(open(path).read())
        return (data.get("data") or {}).get("connection_name") or "default"
    except Exception:
        return "default"


def _build_chat_context(snap: dict) -> str:
    """Render a compact, token-bounded status summary from a snapshot."""
    lines: list[str] = []
    ctx = snap.get("run_ctx") or {}
    if ctx.get("project_name"):
        lines.append(f"Project: {ctx['project_name']}")
    if ctx.get("conversion_type"):
        lines.append(f"Conversion type: {ctx['conversion_type']}")

    lines.append(f"Overall progress: {snap.get('overall_pct', 0)}%")
    if snap.get("has_summary"):
        lines.append("Run state: COMPLETE")
    elif snap.get("active_phase"):
        lines.append(f"Run state: RUNNING (active phase: {snap['active_phase']})")
    else:
        lines.append("Run state: not started / between phases")

    phases = snap.get("phases") or []
    if phases:
        rendered = ", ".join(f"{p.get('phase')}={p.get('status')}" for p in phases)
        lines.append(f"Phases: {rendered}")

    fc = snap.get("file_counts") or {}
    if any(v for v in fc.values()):
        lines.append(
            "Files: "
            f"{fc.get('converted', 0)} converted, {fc.get('verified', 0)} verified, "
            f"{fc.get('failed', 0)} failed, {fc.get('reverted', 0)} reverted"
            + (f", {fc['total']} total" if fc.get("total") else "")
        )

    # Validation entrypoint rollup (from validation_ep events already in the feed).
    ep_a: dict[str, int] = {}
    ep_b: dict[str, int] = {}
    total_eps = None
    for ev in snap.get("feed") or []:
        if ev.get("type") == "validation_ep":
            d = ev.get("data") or {}
            if d.get("phase") == "a" and d.get("status"):
                ep_a[d["status"]] = ep_a.get(d["status"], 0) + 1
            if d.get("phase") == "b" and d.get("status"):
                ep_b[d["status"]] = ep_b.get(d["status"], 0) + 1
            if d.get("total_eps") is not None:
                total_eps = d["total_eps"]
    if total_eps is not None or ep_a or ep_b:
        seg = [f"Validation entrypoints: {total_eps if total_eps is not None else '?'} total"]
        if ep_a:
            seg.append("phase A " + "/".join(f"{k}:{v}" for k, v in ep_a.items()))
        if ep_b:
            seg.append("phase B " + "/".join(f"{k}:{v}" for k, v in ep_b.items()))
        lines.append("; ".join(seg))

    scos = snap.get("scos") or {}
    issues = scos.get("issues") or {}
    if issues.get("total") is not None:
        by_cat = issues.get("by_category") or {}
        top = sorted(by_cat.items(), key=lambda kv: -kv[1])[:5]
        cat_str = ", ".join(f"{k}:{v}" for k, v in top)
        lines.append(f"SCOS issues: {issues['total']} total"
                     + (f" ({cat_str})" if cat_str else ""))

    git = snap.get("git") or {}
    if git.get("insertions") is not None or git.get("deletions") is not None:
        lines.append(f"Code changes: +{git.get('insertions', 0)} / -{git.get('deletions', 0)} lines"
                     + (f" across {git['files_changed']} files" if git.get("files_changed") else ""))

    reports = snap.get("reports") or []
    if reports:
        names = ", ".join(os.path.basename(r.get("file", "")) for r in reports[:12] if r.get("file"))
        lines.append(f"Reports generated: {names}")

    # Recent milestone messages give the model a sense of the latest activity.
    recent = [ev.get("message") for ev in (snap.get("feed") or [])
              if ev.get("level") == "milestone" and ev.get("message")]
    if recent:
        lines.append("Recent milestones: " + " | ".join(recent[-6:]))

    text = "\n".join(lines)
    return text[:6000]  # hard cap


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

SSE_HEADERS = [
    ("Content-Type", "text/event-stream"),
    ("Cache-Control", "no-cache"),
    ("Connection", "keep-alive"),
    ("Access-Control-Allow-Origin", "*"),
]


def _sse_line(data: str) -> bytes:
    return f"data: {data}\n\n".encode()


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress per-request console noise

    # ------------------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        query  = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._serve_static("index.html")
        elif path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
        elif path == "/events":
            since = int(query.get("since", [-1])[0])
            self._stream_events(since)
        elif path == "/api/snapshot":
            self._json(get_snapshot())
        elif path == "/api/run":
            self._serve_run_json()
        elif path == "/file":
            file_path = query.get("path", [""])[0]
            self._serve_file(file_path)
        else:
            self._not_found()

    # ------------------------------------------------------------------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/chat":
            self._chat()
        elif parsed.path == "/api/action":
            self._handle_action()
        else:
            self._not_found()

    def _handle_action(self) -> None:
        """Record a user action (e.g. proceed-with-validation) to a signal file."""
        body = self._read_json_body()
        action = body.get("action") if isinstance(body, dict) else None
        if not action:
            self._json({"error": "no action provided"})
            return
        action_path = os.path.join(RUN_DIR, UI_DIR, "user_action.json")
        payload = {
            "action": action,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            with open(action_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            self._json({"ok": True, "action": action})
        except Exception as exc:
            self._json({"error": str(exc)})

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 256 * 1024:  # cap request body at 256 KB
            return {}
        try:
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _chat(self) -> None:
        """Answer a chat question via the persistent Cortex COMPLETE worker."""
        body = self._read_json_body()
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list) or not messages:
            self._json({"error": "no messages provided"})
            return

        # Ground the model in the current live state.
        try:
            snap = get_snapshot()
        except Exception:
            snap = {}
        try:
            context = _build_chat_context(snap)
        except Exception:
            context = ""

        payload = {
            "context": context,
            "messages": messages[-20:],          # cap history
            "connection": _chat_connection_name(snap),
            # model omitted → backend uses scos_session.DEFAULT_LLM_MODEL
        }

        self._json(_chat_worker.ask(payload))

    # ------------------------------------------------------------------
    def _serve_static(self, name: str) -> None:
        # Prevent path traversal
        safe = os.path.normpath(name).lstrip("/\\")
        full = os.path.join(STATIC_DIR, safe)
        if not os.path.isfile(full) or not full.startswith(STATIC_DIR):
            self._not_found()
            return
        ctype = _content_type(full)
        with open(full, "rb") as fh:
            data = fh.read()
        self._respond(200, ctype, data)

    def _serve_run_json(self) -> None:
        path = os.path.join(RUN_DIR, UI_DIR, "run.json")
        if not os.path.exists(path):
            self._json({})
            return
        try:
            data = json.loads(open(path).read())
            self._json(data)
        except Exception:
            self._json({})

    def _stream_events(self, since: int) -> None:
        self.send_response(200)
        for k, v in SSE_HEADERS:
            self.send_header(k, v)
        self.end_headers()

        try:
            bus = _import_bus()
        except Exception:
            return

        # Send buffered events first
        for ev in bus.read_events(RUN_DIR, since=since):
            line = json.dumps(ev)
            self.wfile.write(_sse_line(line))
            self.wfile.flush()
            since = max(since, ev.get("seq", since))

        # Tail for new events
        last_seq = since
        while True:
            try:
                new_events = bus.read_events(RUN_DIR, since=last_seq)
                for ev in new_events:
                    line = json.dumps(ev)
                    self.wfile.write(_sse_line(line))
                    self.wfile.flush()
                    last_seq = max(last_seq, ev.get("seq", last_seq))
                # Heartbeat every 15 s to keep connection alive
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                time.sleep(1.5)
            except (BrokenPipeError, ConnectionResetError):
                break

    def _serve_file(self, file_path: str) -> None:
        """Serve a text file sandboxed to run_dir."""
        if not file_path:
            self._respond(400, "text/plain", b"Missing path parameter")
            return
        # Decode URL encoding
        file_path = urllib.parse.unquote(file_path)
        abs_path  = os.path.realpath(file_path)
        run_real  = os.path.realpath(RUN_DIR)
        # Sandbox: must be under run_dir
        if not abs_path.startswith(run_real + os.sep) and abs_path != run_real:
            self._respond(403, "text/plain", b"Access denied")
            return
        if not os.path.isfile(abs_path):
            self._not_found()
            return
        try:
            with open(abs_path, "rb") as fh:
                # Cap plain text at 512 KB; HTML reports can be up to 4 MB
                cap = 1024 * 4096 if abs_path.endswith((".html", ".htm")) else 1024 * 512
                data = fh.read(cap)
            self._respond(200, _content_type(abs_path), data)
        except Exception as exc:
            self._respond(500, "text/plain", str(exc).encode())

    # ------------------------------------------------------------------
    def _json(self, obj: Any) -> None:
        data = json.dumps(obj, default=str).encode()
        self._respond(200, "application/json", data)

    def _respond(self, code: int, ctype: str, data: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _not_found(self) -> None:
        self._respond(404, "text/plain", b"Not found")


def _content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".html": "text/html",
        ".js":   "application/javascript",
        ".css":  "text/css",
        ".json": "application/json",
        ".csv":  "text/csv",
        ".txt":  "text/plain",
        ".md":   "text/markdown",
        ".py":   "text/plain",
        ".scala":"text/plain",
    }.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded server that swallows the benign client-disconnect errors SSE
    streams routinely trigger, so server.log isn't buried in tracebacks."""
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionError)):
            return  # client went away mid-stream — expected, not an error
        super().handle_error(request, client_address)


def main() -> None:
    global RUN_DIR, SKILL_DIR

    p = argparse.ArgumentParser()
    p.add_argument("--run",       required=True)
    p.add_argument("--skill-dir", required=True)
    p.add_argument("--port",      type=int, default=7860)
    args = p.parse_args()

    RUN_DIR   = os.path.abspath(args.run)
    SKILL_DIR = os.path.abspath(args.skill_dir)

    os.makedirs(os.path.join(RUN_DIR, UI_DIR), exist_ok=True)

    # Write run.json if it doesn't exist yet (server started before run-init)
    run_json = os.path.join(RUN_DIR, UI_DIR, "run.json")
    if not os.path.exists(run_json):
        with open(run_json, "w") as fh:
            json.dump({"run_dir": RUN_DIR, "skill_dir": SKILL_DIR}, fh)

    server = QuietThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[progress-ui] Serving on http://localhost:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            _chat_worker._stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
