"""In-process pytest entry: build a TrialRequest and run it through a runtime.

Rendered ``test_<ep>.py`` files call :func:`run_validation_trial` with the
per-entrypoint constants. Provisioning lives in the runtime classes, capture
lives in ``_executor``, and this module only assembles the request, selects the
flavor, and (for Phase B) compares against the Phase A baseline.

All runtimes (local, databricks, scos) are driven in-process through this
module. The driver calls ``runtime.provision(request)`` (idempotent, hash-gated)
then ``runtime.run_trial(request)`` which only clones + runs + captures.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Callable, Dict, Optional

from helpers import compare_results, requires_nonempty_sink_capture  # type: ignore[import-not-found]

from . import get_runtime
from .base import TrialRequest, normalize_flavor


# One runtime instance per (flavor, kwargs) within a worker process. A fresh
# DatabricksRuntime per trial re-runs detect_databricks_env() and re-resolves the
# catalog (CREATE/DROP SCHEMA) on first .spark access, so cache it (P15).
_RUNTIME_CACHE: Dict[tuple, Any] = {}


def _cached_runtime(flavor: str, **kwargs: Any):
    # Only DatabricksRuntime is expensive to construct (detect env + resolve
    # catalog via CREATE/DROP SCHEMA on first .spark). local/scos init is cheap,
    # so leave them uncached (also keeps get_runtime patchable in tests).
    name = normalize_flavor(flavor)
    if name != "databricks":
        return get_runtime(flavor, **kwargs)
    key = (name, tuple(sorted(kwargs.items())))
    rt = _RUNTIME_CACHE.get(key)
    if rt is None:
        rt = get_runtime(flavor, **kwargs)
        _RUNTIME_CACHE[key] = rt
    return rt


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_trial_request(
    *,
    trial_id: str,
    entrypoint_path: str,
    callable_name: Optional[str] = None,
    kwargs: Optional[Dict[str, Any]] = None,
    kwargs_factory: Optional[Callable[[str], Dict[str, Any]]] = None,
    extra_env: Optional[Dict[str, str]] = None,
    module_globals_factory: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> TrialRequest:
    """Assemble a TrialRequest from env + analysis/state for one entrypoint.

    Reads from the kit's SCOS_* env and shared/schemas/:
      - Phase A runtime: determined from ep_config["source_runtime"] + credentials.
        Falls back to SCOS_FLAVOR, then "local".
      - Phase B: SCOS_FLAVOR=scos switches the workload root to Output/ and
        sets flavor to "scos". No other value of SCOS_FLAVOR is needed.
      - SCOS_CONV_ROOT     -> project_root (conversion root)
      - SCOS_RESULTS_DIR   -> results dir for this phase
      - SCOS_SCHEMAS_DIR   -> shared/schemas/
      - SCOS_STATE_JSON    -> state.json
      - SCOS_MOCK_DATA_DIR -> shared/mock_data root
    """
    project_root = os.environ["SCOS_CONV_ROOT"]
    results_dir = os.environ["SCOS_RESULTS_DIR"]
    from helpers import assemble_analysis  # type: ignore[import-not-found]
    analysis = assemble_analysis(os.environ["SCOS_SCHEMAS_DIR"])
    ep_config = next(e for e in analysis["entrypoints"] if e["id"] == trial_id)

    # Load state.json early — needed for credential resolution in flavor logic
    state_json: Optional[dict] = None
    state_path = os.environ.get("SCOS_STATE_JSON")
    if state_path and os.path.isfile(state_path):
        state_json = _load_json(state_path)

    _env_flavor = normalize_flavor(os.environ.get("SCOS_FLAVOR", ""))
    if _env_flavor == "scos":
        flavor = "scos"
    else:
        source_rt = ep_config.get("source_runtime", "spark")
        if source_rt == "databricks":
            # Load stored cred path from state.json so detect_databricks_env() finds it
            env_file = (state_json or {}).get("databricks", {}).get("env_file", "")
            if env_file:
                os.environ.setdefault("SCOS_DATABRICKS_ENV_FILE", env_file)
            from . import detect_databricks_env
            flavor = "databricks" if detect_databricks_env() else "local"
        else:
            flavor = normalize_flavor(_env_flavor or "local")

    mock_ep_id = ep_config.get("mock_ep_id") or trial_id
    mock_data_dir = os.path.join(os.environ["SCOS_MOCK_DATA_DIR"], mock_ep_id)

    here = os.path.dirname(os.path.abspath(__file__))
    comparator_path = os.path.join(os.path.dirname(here), "comparator.py")

    return TrialRequest(
        trial_id=trial_id,
        flavor=flavor,
        project_root=project_root,
        entrypoint_path=entrypoint_path,
        callable_name=callable_name,
        ep_config=ep_config,
        mock_data_dir=mock_data_dir,
        results_dir=results_dir,
        kwargs=kwargs or {},
        kwargs_factory=kwargs_factory,
        extra_env=extra_env or {},
        module_globals_factory=module_globals_factory,
        partition_key=analysis.get("partition_key"),
        state_json=state_json,
        analysis=analysis,
        comparator_path=comparator_path,
    )


def _phase_output_names(phase_dir: str) -> list[str]:
    index_path = os.path.join(phase_dir, "_index.json")
    try:
        with open(index_path, encoding="utf-8") as fh:
            index = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    tables = {
        str(item.get("name") or "").strip()
        for item in index.get("tables", [])
        if str(item.get("name") or "").strip()
    }
    return sorted(tables)


def _has_phase_a_baseline(phase_a_dir: str) -> bool:
    if not _phase_output_names(phase_a_dir):
        return False
    # A partial/errored Phase A can still leave parquet behind (e.g. step1/step2
    # wrote some tables before the run aborted). Comparing Phase B against that
    # corrupt baseline yields a misleading diff, so require the run's own verdict:
    # _harness_status.json with ok: true (written by _executor after capture).
    status_path = os.path.join(phase_a_dir, "_harness_status.json")
    try:
        with open(status_path, encoding="utf-8") as fh:
            return json.load(fh).get("ok") is True
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False


def _phase_a_ran_clean(phase_a_dir: str) -> bool:
    """True when Phase A recorded a clean run (ok:true), regardless of how many
    tables it produced. Used for no-sink entrypoints whose baseline is simply
    "the workload executes without error"."""
    status_path = os.path.join(phase_a_dir, "_harness_status.json")
    try:
        with open(status_path, encoding="utf-8") as fh:
            return json.load(fh).get("ok") is True
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False


def _clear_trial_outputs(trial_dir: str) -> None:
    os.makedirs(trial_dir, exist_ok=True)
    for filename in (
        "_harness_status.json",
        "_index.json",
        "_manual_review.json",
        "workload_error.txt",
        "capture_error.txt",
    ):
        path = os.path.join(trial_dir, filename)
        if os.path.exists(path):
            os.remove(path)
    for dirname in ("tables", "artifacts", "diffs"):
        path = os.path.join(trial_dir, dirname)
        if os.path.isdir(path):
            shutil.rmtree(path)


def _write_manual_review_marker(trial_dir: str, phase_a_dir: str, trial_id: str, manifest: dict) -> None:
    os.makedirs(trial_dir, exist_ok=True)
    marker = {
        "trial_id": trial_id,
        "reason": "no_phase_a_baseline",
        "phase_a_dir": phase_a_dir,
        "phase_b_dir": trial_dir,
        "captured_outputs": sorted(t.get("name") for t in manifest.get("tables", [])),
    }
    with open(os.path.join(trial_dir, "_manual_review.json"), "w", encoding="utf-8") as fh:
        json.dump(marker, fh, indent=2)


def run_validation_trial(request: TrialRequest):
    """Run one trial in-process and assert outcome (pytest-facing).

    Mirrors the old ``test_main_entrypoint`` semantics:
      - raise the workload error after capture (so the trial fails),
      - assert the workload produced outputs and capture had no failures,
      - for scos: compare against the Phase A baseline when one exists, else
        write a manual-review marker (``passed_no_baseline`` path).
    """
    flavor = request.canonical_flavor

    # Clear stale per-trial outputs so a partial/crashed re-run never shows prior state.
    _trial_dir = os.path.join(request.results_dir, request.trial_id)
    _clear_trial_outputs(_trial_dir)

    runtime = _cached_runtime(flavor, project_root=request.project_root) if flavor == "databricks" else _cached_runtime(flavor)
    runtime.provision(request)
    result = runtime.run_trial(request)

    trial_dir = os.path.join(request.results_dir, request.trial_id)
    manifest = result.manifest or {}

    # ------------------------------------------------------------------
    # Phase B (scos) capture-honesty gate.
    #
    # A Phase B run that produces ZERO captured tables while a Phase A
    # baseline exists with N>0 tables is NOT a success — it almost always
    # means the migrated workload errored before writing any sink (the real
    # SCOS error is in manifest['error']/failures), or the workload wrote to
    # a sink the harness does not snapshot. Surface that loudly and terminally
    # so the trial cannot be silently re-run forever in a 0-table capture loop.
    # ------------------------------------------------------------------
    if flavor == "scos":
        phase_a_dir = trial_dir.replace("/phase_b/", "/phase_a/")
        baseline_outputs = _phase_output_names(phase_a_dir)
        captured = sorted(t.get("name") for t in manifest.get("tables", []))
        if baseline_outputs and not captured:
            detail = manifest.get("error")
            if not detail:
                fails = manifest.get("failures") or []
                detail = (
                    f"{len(fails)} capture failure(s): {fails}" if fails
                    else "no workload error and no capture failures were recorded — "
                    "the workload likely wrote to a sink the harness does not snapshot, "
                    "or produced only empty sinks"
                )
            raise RuntimeError(
                f"Phase B capture honesty violation for trial {request.trial_id}: "
                f"captured 0 outputs but the Phase A baseline has "
                f"{len(baseline_outputs)} output(s) {baseline_outputs}. "
                f"This is a FAILED capture, not a pass. Root cause: {detail}"
            )

    if manifest.get("error"):
        raise RuntimeError(
            f"Workload error in trial {request.trial_id}: {manifest['error']}"
        )
    critical_failures = [
        str(item.get("message") or item.get("reason") or "").strip()
        for item in manifest.get("failures", [])
        if item.get("critical")
    ]
    critical_failures = [msg for msg in critical_failures if msg]
    if critical_failures:
        raise AssertionError("\n".join(critical_failures))
    # A trial with no required non-empty sinks (pure DDL/config, or sinks that are
    # all explicitly allow_empty) may legitimately finish with zero captured
    # outputs. Only require captured outputs when at least one declared sink must
    # actually produce rows; otherwise a clean zero-output run is valid.
    requires_nonempty_sinks = requires_nonempty_sink_capture(request.ep_config)
    captured_outputs = [t.get("name") for t in manifest.get("tables", [])]
    if requires_nonempty_sinks:
        assert captured_outputs, (
            f"No outputs produced for trial {request.trial_id} "
            f"(failures: {manifest.get('failures', [])})"
        )
    # For Phase A, non-critical capture failures (e.g. temp views that vanished
    # before snapshot) are acceptable warnings as long as tables were produced.
    # Phase B comparison still requires a clean capture.
    if flavor != "scos" and manifest.get("failures"):
        import sys
        sys.stderr.write(
            f"[warn] Phase A capture had non-critical failures for {request.trial_id}: "
            f"{manifest.get('failures', [])}\n"
        )
    elif manifest.get("failures"):
        assert not manifest.get("failures"), (
            f"Snapshot capture failed for trial {request.trial_id}: {manifest.get('failures', [])}"
        )

    if flavor == "scos":
        phase_a_dir = trial_dir.replace("/phase_b/", "/phase_a/")
        if _has_phase_a_baseline(phase_a_dir):
            compare_results(phase_a_dir, trial_dir, request.comparator_path)
        elif not requires_nonempty_sinks and _phase_a_ran_clean(phase_a_dir):
            # Either a true no-sink entrypoint, or one whose declared sinks are all
            # explicitly allow_empty. Phase A ran clean with no comparable data, and
            # Phase B just ran clean too (it is past the error gate). That is an
            # execution-parity match — nothing to diff, and NOT a no-baseline /
            # manual-review case.
            pass
        else:
            _write_manual_review_marker(trial_dir, phase_a_dir, request.trial_id, manifest)

    return result
