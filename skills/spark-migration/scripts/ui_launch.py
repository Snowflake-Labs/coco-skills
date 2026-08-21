#!/usr/bin/env python3
"""
ui_launch.py — Idempotent launcher for the spark-migration progress UI server.

⚠️ EXPERIMENTAL: The Progress UI is an experimental feature. `start` is a no-op
unless the launch is explicitly opted into with `--confirm-experimental` (or the
`SPARK_MIGRATION_UI_EXPERIMENTAL=1` environment variable). This interlock exists
so the UI can never be launched by accident.

Usage:
    python3 ui_launch.py start  --run <output_dir> --skill-dir <skill_dir> --confirm-experimental [--port <n|auto>] [--no-browser]
    python3 ui_launch.py stop   --run <output_dir>
    python3 ui_launch.py status --run <output_dir>

Behaviour:
  start  — No-op unless explicitly opted in (see above). Otherwise: if a live
           server already owns server.pid, prints its URL and exits. Else picks a
           free port (or uses --port), spawns progress_server.py as a background
           process, writes server.pid/server.log, optionally opens the browser,
           and prints the dashboard URL.
  stop   — Sends SIGTERM to the PID in server.pid.
  status — Prints "running:<port>" or "stopped".
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time

UI_DIR = ".migration-ui"
PID_FILE = "server.pid"
PORT_FILE = "server.port"
LOG_FILE = "server.log"

BASE_PORT = 7860
MAX_PORT  = 7960


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ui_dir(run_dir: str) -> str:
    return os.path.join(run_dir, UI_DIR)


def _pid_path(run_dir: str) -> str:
    return os.path.join(_ui_dir(run_dir), PID_FILE)


def _port_path(run_dir: str) -> str:
    return os.path.join(_ui_dir(run_dir), PORT_FILE)


def _log_path(run_dir: str) -> str:
    return os.path.join(_ui_dir(run_dir), LOG_FILE)


def _read_pid(run_dir: str) -> int | None:
    path = _pid_path(run_dir)
    if not os.path.exists(path):
        return None
    try:
        return int(open(path).read().strip())
    except (ValueError, OSError):
        return None


def _read_port(run_dir: str) -> int | None:
    path = _port_path(run_dir)
    if not os.path.exists(path):
        return None
    try:
        return int(open(path).read().strip())
    except (ValueError, OSError):
        return None


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _free_port(preferred: int | None = None) -> int:
    if preferred:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                pass
    for port in range(BASE_PORT, MAX_PORT + 1):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port in {BASE_PORT}-{MAX_PORT}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _experimental_opt_in(args: argparse.Namespace) -> bool:
    """The Progress UI is experimental and must NEVER launch by accident.

    It only starts when the launch is explicitly requested — either via the
    ``--confirm-experimental`` flag or the ``SPARK_MIGRATION_UI_EXPERIMENTAL``
    environment variable. Any other invocation of ``start`` is a no-op.
    """
    if getattr(args, "confirm_experimental", False):
        return True
    return os.environ.get("SPARK_MIGRATION_UI_EXPERIMENTAL", "").lower() in ("1", "true", "yes")


def cmd_start(args: argparse.Namespace) -> None:
    if not _experimental_opt_in(args):
        print(
            "[progress-ui] Experimental Progress UI not launched. "
            "It only starts on explicit request — pass --confirm-experimental "
            "(or set SPARK_MIGRATION_UI_EXPERIMENTAL=1). This interlock prevents "
            "accidental launches.",
            file=sys.stderr,
        )
        return

    run_dir = os.path.abspath(args.run)
    skill_dir = os.path.abspath(args.skill_dir)
    os.makedirs(_ui_dir(run_dir), exist_ok=True)

    # Reuse live server
    pid = _read_pid(run_dir)
    if pid and _is_running(pid):
        port = _read_port(run_dir)
        url = f"http://localhost:{port}"
        print(f"[progress-ui] Already running (PID {pid}): {url}", flush=True)
        return

    preferred = None if args.port == "auto" else int(args.port)
    port = _free_port(preferred)

    server_script = os.path.join(skill_dir, "snowpark-connect", "progress-ui", "server", "progress_server.py")
    if not os.path.exists(server_script):
        print(f"[progress-ui] WARNING: server not found at {server_script}, skipping UI", file=sys.stderr)
        return

    log_path = _log_path(run_dir)
    with open(log_path, "a") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, server_script,
             "--run", run_dir,
             "--skill-dir", skill_dir,
             "--port", str(port)],
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,   # detach from parent signal group
        )

    # Write PID + port
    with open(_pid_path(run_dir), "w") as f:
        f.write(str(proc.pid))
    with open(_port_path(run_dir), "w") as f:
        f.write(str(port))

    # Brief wait to confirm it started
    time.sleep(0.8)
    if not _is_running(proc.pid):
        print(f"[progress-ui] WARNING: server exited immediately. Check {log_path}", file=sys.stderr)
        return

    url = f"http://localhost:{port}"
    print(f"\n{'─'*60}", flush=True)
    print(f"  Spark Migration Progress UI  ·  EXPERIMENTAL", flush=True)
    print(f"  {url}", flush=True)
    print(f"{'─'*60}\n", flush=True)

    if not args.no_browser:
        _open_window(url, skill_dir)


def cmd_stop(args: argparse.Namespace) -> None:
    pid = _read_pid(args.run)
    if not pid:
        print("[progress-ui] No server.pid found, nothing to stop.")
        return
    if not _is_running(pid):
        print(f"[progress-ui] PID {pid} is not running.")
        return
    os.kill(pid, signal.SIGTERM)
    print(f"[progress-ui] Sent SIGTERM to PID {pid}.")


def cmd_status(args: argparse.Namespace) -> None:
    pid = _read_pid(args.run)
    if pid and _is_running(pid):
        port = _read_port(args.run)
        print(f"running:{port}")
    else:
        print("stopped")


def _open_window(url: str, skill_dir: str) -> None:
    """Spawn ui_window.py as a detached process — never blocks migration."""
    window_script = os.path.join(skill_dir, "scripts", "ui_window.py")
    if not os.path.exists(window_script):
        # Fallback to plain browser if script is missing
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return
    try:
        subprocess.Popen(
            [sys.executable, window_script, "--url", url],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # headless / no display — URL already printed above


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(prog="ui_launch")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start")
    s.add_argument("--run", required=True)
    s.add_argument("--skill-dir", required=True)
    s.add_argument("--port", default="auto")
    s.add_argument("--no-browser", action="store_true")
    s.add_argument(
        "--confirm-experimental",
        action="store_true",
        help="Required opt-in to start the experimental Progress UI. Without this "
             "flag (or SPARK_MIGRATION_UI_EXPERIMENTAL=1) 'start' is a no-op.",
    )

    s = sub.add_parser("stop")
    s.add_argument("--run", required=True)

    s = sub.add_parser("status")
    s.add_argument("--run", required=True)

    args = p.parse_args()
    try:
        {"start": cmd_start, "stop": cmd_stop, "status": cmd_status}[args.cmd](args)
    except Exception as exc:
        print(f"[progress-ui] WARNING: {exc}", file=sys.stderr)
        sys.exit(0)  # non-fatal


if __name__ == "__main__":
    main()
