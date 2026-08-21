"""Non-fatal Progress UI emitters for the validate pipeline.

Looks for a Conversion run that already has ``.migration-ui/`` (started by the
migrate skill) and appends events via ``spark-migration/scripts/progress_bus.py``.
All helpers swallow errors — validation must never fail because the dashboard
is unavailable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# validate-pyspark-to-snowpark-connect/scripts → snowpark-connect → spark-migration
_SPARK_MIGRATION_ROOT = Path(__file__).resolve().parents[3]
_PROGRESS_BUS = _SPARK_MIGRATION_ROOT / "scripts" / "progress_bus.py"

# Map batch.py / state.json phase labels → Progress UI phase ids.
_PHASE_MAP = {
    "starting": "survey",
    "synthesizing": "survey",
    "patching": "survey",
    "phase a": "phase-a",
    "phase b": "phase-b",
    "phase b complete": "phase-b",
    "harvest": "harvest",
}


def find_progress_run(start: Path | str | None) -> Path | None:
    """Walk upward from *start* looking for ``.migration-ui/``."""
    if not start:
        return None
    cur = Path(start).resolve()
    for p in [cur, *cur.parents]:
        if (p / ".migration-ui").is_dir():
            return p
    return None


def _run_bus(run: Path, *args: str) -> None:
    if not _PROGRESS_BUS.is_file():
        return
    try:
        subprocess.run(
            [sys.executable, str(_PROGRESS_BUS), *args, "--run", str(run)],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


def phase_start(run: Path | None, phase: str, message: str | None = None) -> None:
    if not run:
        return
    args = ["phase-start", "--phase", phase]
    # milestone carries optional message; phase-start does not — emit both when msg set
    _run_bus(run, *args)
    if message:
        _run_bus(run, "milestone", "--phase", phase, "--message", message)


def phase_end(run: Path | None, phase: str) -> None:
    if not run:
        return
    _run_bus(run, "phase-end", "--phase", phase)


def milestone(run: Path | None, phase: str, message: str) -> None:
    if not run:
        return
    _run_bus(run, "milestone", "--phase", phase, "--message", message)


def validation_ep(
    run: Path | None,
    *,
    ep: str,
    phase: str,
    status: str,
    total: int | None = None,
    message: str | None = None,
) -> None:
    if not run:
        return
    args = ["validation-ep", "--ep", ep, "--phase", phase, "--status", status]
    if total is not None:
        args += ["--total", str(total)]
    if message:
        args += ["--message", message]
    _run_bus(run, *args)


def report_ready(run: Path | None, path: str, phase: str = "harvest") -> None:
    if not run:
        return
    _run_bus(run, "report-ready", "--file", path, "--phase", phase)


def validation_complete(run: Path | None, data: dict[str, Any] | None = None) -> None:
    if not run:
        return
    payload = {"validation_complete": True, **(data or {})}
    _run_bus(run, "milestone", "--phase", "validation-complete",
             "--message", "Validation complete")
    _run_bus(run, "summary", "--data", json.dumps(payload))


def map_batch_phase(label: str | None) -> str | None:
    """Map a pool ``current_phase`` string to a Progress UI phase id."""
    if not label:
        return None
    low = label.lower().strip()
    # "Phase A (3/5 done)" → phase-a
    for key, mapped in _PHASE_MAP.items():
        if low.startswith(key) or key in low:
            return mapped
    if "phase a" in low or low.startswith("phase_a"):
        return "phase-a"
    if "phase b" in low or low.startswith("phase_b"):
        return "phase-b"
    return None


def emit_eps_from_summary(run: Path | None, summary_path: Path, total: int | None = None) -> None:
    """Emit terminal validation_ep events from a batch ``summary.json``."""
    if not run or not summary_path.is_file():
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return

    # Prefer entrypoints list; fall back to decision.per_entrypoint shapes.
    eps = summary.get("entrypoints")
    if not isinstance(eps, list):
        decision = summary.get("decision") or {}
        per = decision.get("per_entrypoint") or decision.get("entrypoints") or []
        eps = per if isinstance(per, list) else []

    n = total if total is not None else (len(eps) or None)
    for ep in eps:
        if not isinstance(ep, dict):
            continue
        ep_id = ep.get("ep_id") or ep.get("id") or ep.get("entrypoint_id")
        if not ep_id:
            continue
        # Phase B verdict
        pb = ep.get("phase_b") or {}
        status_b = (
            pb.get("verdict")
            or pb.get("status")
            or ep.get("verdict")
            or ep.get("overall")
            or (summary.get("decision") or {}).get("overall")
        )
        # Normalize common summary vocabulary onto bus statuses.
        status_b = _normalize_status(status_b)
        if status_b:
            validation_ep(run, ep=str(ep_id), phase="b", status=status_b, total=n)

        pa = ep.get("phase_a") or {}
        status_a = _normalize_status(pa.get("verdict") or pa.get("status"))
        if status_a:
            validation_ep(run, ep=str(ep_id), phase="a", status=status_a, total=n)


def _normalize_status(raw: Any) -> str | None:
    if not raw:
        return None
    s = str(raw).lower().strip()
    aliases = {
        "pass": "passed",
        "passed": "passed",
        "ok": "passed",
        "success": "passed",
        "passed_no_baseline": "passed_no_baseline",
        "pass_no_baseline": "passed_no_baseline",
        "fail": "failed",
        "failed": "failed",
        "error": "failed",
        "hard_stuck": "hard_stuck",
        "stuck": "hard_stuck",
        "skipped": "skipped",
        "skip": "skipped",
        "running": "running",
        "pending": "pending",
    }
    return aliases.get(s)
