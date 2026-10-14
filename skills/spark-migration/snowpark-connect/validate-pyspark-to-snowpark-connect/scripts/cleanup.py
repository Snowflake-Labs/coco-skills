"""SCOS + Databricks cleanup orchestrator for SCOS validation.

Discovers which runtimes were used during the validation run and calls each
runtime's ``cleanup_session()`` to drop its golden/clone schemas (run-prefix
sweep). Discovery is entirely prefix-based, so it works even when lazy/
driver-driven provisioning never wrote provisioning state back to disk.

Per-flavor dependency isolation
-------------------------------
Each flavor's teardown needs a different heavy client that lives in a different
phase venv, and they cannot coexist in one interpreter:

  - **scos** cleanup needs ``snowflake-connector`` (``shared/.venv-scos``)
  - **databricks** cleanup needs ``databricks-connect`` (``shared/.venv-source``)

So the orchestrator DISPATCHES each flavor's teardown to its phase venv via a
subprocess (re-invoking this same file with ``--_flavor``). A single
``cleanup.py`` call therefore cleans every flavor that ran, regardless of which
interpreter you launch it from.

Strategy:
  - **SCOS (always — Phase B always runs on SCOS):** sweep schemas matching
    ``{project_slug}_{run_id}_*`` in the SCOS database.
  - **Databricks (when any entrypoint used the ``databricks`` runtime):** sweep
    ``scos_golden_{run_id}_*`` + ``scos_trial_*`` schemas from the cluster catalog.
  - **Local:** nothing persistent to tear down.

Prompts for confirmation unless ``--force``.

Exit codes:
    0  success (or nothing to clean)
    2  usage / state error
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


DATABASE = os.environ.get("SCOS_VALIDATION_DATABASE", "SCOS_VALIDATION")
VALIDATION_DIRNAME = "Validation"
SCRIPTS_DIR = Path(__file__).resolve().parent

# Machine-readable marker the child prints so the orchestrator can parse results.
_RESULT_MARKER = "CLEANUP_JSON:"

# flavor -> phase venv (relative to Validation/) that holds its heavy client.
_FLAVOR_VENV = {
    "scos": "shared/.venv-scos",
    "databricks": "shared/.venv-source",
}


def _validation_root(conv_root: Path) -> Path:
    return conv_root / VALIDATION_DIRNAME


def _import_runtimes():
    """Make the harness ``runtimes`` package importable and return it."""
    harness = str(SCRIPTS_DIR / "harness")
    if harness not in sys.path:
        sys.path.insert(0, harness)
    import runtimes  # type: ignore[import-not-found]

    return runtimes


def _needs_databricks_cleanup(conv_root: Path) -> bool:
    """Return True if any entrypoint used the databricks runtime."""
    schemas_dir = _validation_root(conv_root) / "shared" / "schemas"
    if not schemas_dir.is_dir():
        return False
    import datagen  # type: ignore[import-not-found]
    try:
        eps = datagen.read_entrypoints(str(schemas_dir))
    except Exception:  # noqa: BLE001
        return False
    has_databricks_ep = any(ep.get("source_runtime") == "databricks" for ep in eps)
    if not has_databricks_ep:
        return False
    runtimes = _import_runtimes()
    return runtimes.detect_databricks_env() is not None


# ---------------------------------------------------------------------------
# Child mode: run ONE flavor's cleanup_session in this interpreter.
# ---------------------------------------------------------------------------

def _run_flavor_inproc(flavor: str, state: dict, database: str, dry_run: bool) -> list:
    """Instantiate the flavor's runtime and call cleanup_session. Returns FQNs."""
    _import_runtimes()
    if flavor == "scos":
        from runtimes.scos_runtime import ScosRuntime  # type: ignore
        rt = ScosRuntime()
    elif flavor == "databricks":
        from runtimes.databricks_runtime import DatabricksRuntime  # type: ignore
        rt = DatabricksRuntime()
    else:
        return []
    return rt.cleanup_session(state=state, database=database, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Orchestrator mode: dispatch each flavor to its phase venv.
# ---------------------------------------------------------------------------

def _venv_python(flavor: str, workspace_root: Path) -> Path | None:
    rel = _FLAVOR_VENV.get(flavor)
    if not rel:
        return None
    py = workspace_root / rel / "bin" / "python"
    return py if py.is_file() else None


def _dispatch_flavor(
    flavor: str, conv_root: Path, workspace_root: Path, state: dict, *, dry_run: bool
) -> list:
    """Run a flavor's cleanup in its phase venv (subprocess). Returns dropped FQNs."""
    py = _venv_python(flavor, workspace_root)
    if py is None:
        # Fall back to the current interpreter (best-effort); cleanup_session
        # degrades gracefully if its client isn't importable here.
        py = Path(sys.executable)
        print(
            f"  WARNING: phase venv for '{flavor}' not found "
            f"({workspace_root / _FLAVOR_VENV[flavor]}); trying current interpreter.",
            file=sys.stderr,
        )

    cmd = [str(py), str(Path(__file__).resolve()),
           "--conv-root", str(conv_root), "--_flavor", flavor]
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--force")

    child_env = dict(os.environ)
    # The databricks child needs the cluster creds resolvable.
    env_file = (state.get("databricks") or {}).get("env_file")
    if flavor == "databricks" and env_file:
        child_env.setdefault("SCOS_DATABRICKS_ENV_FILE", env_file)

    proc = subprocess.run(cmd, env=child_env, capture_output=True, text=True)
    # Surface child stderr (warnings, connect logs) but keep it quiet on success.
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        print(f"  WARNING: {flavor} cleanup exited {proc.returncode}", file=sys.stderr)
    dropped: list = []
    for line in proc.stdout.splitlines():
        if line.startswith(_RESULT_MARKER):
            try:
                dropped = json.loads(line[len(_RESULT_MARKER):])
            except json.JSONDecodeError:
                pass
        elif line.strip():
            print(line)
    return dropped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tear down ephemeral SCOS + Databricks artifacts from validation."
    )
    parser.add_argument("--conv-root", required=True,
                        help="Path to the conversion root (parent of Validation/)")
    parser.add_argument("--force", "--yes", action="store_true", dest="force",
                        help="Skip confirmation prompt (required non-interactively)")
    # Internal: run a single flavor's cleanup in THIS interpreter (its phase venv).
    parser.add_argument("--_flavor", dest="flavor", choices=list(_FLAVOR_VENV),
                        help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true",
                        help=argparse.SUPPRESS)
    args = parser.parse_args()

    conv_root = Path(args.conv_root).resolve()
    workspace_root = _validation_root(conv_root)
    state_path = workspace_root / "state.json"
    if not state_path.is_file():
        print(f"ERROR: state.json not found at {state_path}", file=sys.stderr)
        return 2

    state = json.loads(state_path.read_text())
    global DATABASE
    DATABASE = state.get("snowflake", {}).get("database") or DATABASE

    # --- Child mode: do exactly one flavor here, emit machine-readable result. ---
    if args.flavor:
        try:
            dropped = _run_flavor_inproc(args.flavor, state, DATABASE, dry_run=args.dry_run)
        except ModuleNotFoundError as exc:
            print(f"  WARNING: {args.flavor} cleanup skipped — {exc}", file=sys.stderr)
            dropped = []
        print(f"{_RESULT_MARKER}{json.dumps(dropped)}")
        return 0

    # --- Orchestrator mode: dispatch each used flavor to its phase venv. ---
    flavors = ["scos"]  # Phase B always runs on SCOS.
    if _needs_databricks_cleanup(conv_root):
        flavors.append("databricks")

    # Dry-run discovery is only needed for user-facing confirmation. With
    # --force / --dry-run we can skip it — the real DROP path already emits its
    # own "Dropped: <fqn>" lines, and skipping the pre-pass halves the
    # SHOW-SCHEMAS + connect overhead for the common headless case.
    if args.dry_run:
        would: list = []
        for flavor in flavors:
            would.extend(
                _dispatch_flavor(flavor, conv_root, workspace_root, state, dry_run=True)
            )
        if not would:
            print("Nothing to clean.")
            return 0
        # List-only; drop nothing. Used by headless/automated callers that must never
        # drop Snowflake objects without an explicit approval gate.
        print("DRY RUN — would DROP SCHEMA CASCADE (nothing dropped):")
        for fqn in would:
            print(f"  {fqn}")
        return 0

    if not args.force:
        # Interactive confirmation: discover first, print the list, prompt.
        would = []
        for flavor in flavors:
            would.extend(
                _dispatch_flavor(flavor, conv_root, workspace_root, state, dry_run=True)
            )
        if not would:
            print("Nothing to clean.")
            return 0
        print("Will DROP SCHEMA CASCADE:")
        for fqn in would:
            print(f"  {fqn}")
        print()
        if not sys.stdin.isatty():
            print("ERROR: non-interactive session (no TTY). Re-run with --force or --yes.",
                  file=sys.stderr)
            return 2
        if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    dropped = 0
    for flavor in flavors:
        dropped += len(_dispatch_flavor(flavor, conv_root, workspace_root, state, dry_run=False))

    if dropped == 0:
        print("Nothing to clean.")
        return 0

    # Reset compat fields in state.json.
    sf = state.setdefault("snowflake", {})
    sf["provisioned"] = False
    sf["golden_schemas"] = {}
    state_path.write_text(json.dumps(state, indent=2) + "\n")

    print(f"\nCleanup complete: {dropped} schema(s) dropped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
