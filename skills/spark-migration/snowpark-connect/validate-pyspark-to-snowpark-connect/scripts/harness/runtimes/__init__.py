"""Runtime registry and per-flavor architecture.

Runtimes are registered lazily by flavor so importing this package never pulls
in ``pyspark`` (local), ``databricks.sdk`` (databricks), or
``snowflake.snowpark_connect`` (scos) until the corresponding runtime is
actually instantiated.

Per-trial pipeline (called by driver.run_validation_trial for every trial):

    runtime = get_runtime(flavor)
    runtime.provision(request)       # idempotent, hash-gated
    runtime.run_trial(request)       # clone golden + run + capture

Teardown (called by cleanup.py at the end of the whole run, NOT per trial):

    runtime.cleanup_session(state=..., database=...)   # drop this flavor's
                                                       # golden + clone schemas
                                                       # via run-prefix sweep

Golden schemas persist across pytest runs (so provision() can reuse + reseed
them hash-gated for fast iteration); cleanup.py drops them when the run is done.

Flavors:

  - **local** (local_runtime.py) — In-process PySpark + Delta catalog.
    Phase A baseline. No golden provisioning needed (mock data on disk).
    run_trial seeds a local schema per trial.

  - **databricks** (databricks_runtime.py) — Remote Databricks cluster via
    databricks-connect. Phase A baseline. provision() prewarms the cluster,
    resolves the target catalog (hive_metastore by default, write-probed),
    and seeds golden schemas hash-gated. run_trial SHALLOW CLONEs golden
    tables into a trial schema, runs the workload, captures DBFS file sinks,
    and drops the trial schema.

  - **scos** (scos_runtime.py) — Snowpark Connect against a cloned Snowflake
    schema. Phase B. provision() seeds the golden schema via _scos_provision
    (hash-gated, creates tables + stages mock data via COPY INTO).
    run_trial clones the golden schema, runs via snowpark_connect, captures
    table snapshots, and drops the clone.
"""

from __future__ import annotations

import importlib
import os
from typing import Optional

from .base import (
    PHASE_A_FLAVORS,
    PHASE_B_FLAVORS,
    TrialContext,
    TrialRequest,
    TrialResult,
    ValidationRuntime,
    is_phase_b,
    normalize_flavor,
)

# flavor -> (module_name, class_name); modules live alongside this package and
# are imported on demand from the flat tests/ dir (sys.path includes here).
_REGISTRY = {
    "local": ("local_runtime", "LocalDeltaRuntime"),
    "databricks": ("databricks_runtime", "DatabricksRuntime"),
    "scos": ("scos_runtime", "ScosRuntime"),
}

_DATABRICKS_ENV_KEYS = ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_CLUSTER_ID")


def register_runtime(flavor: str, module_name: str, class_name: str) -> None:
    _REGISTRY[normalize_flavor(flavor)] = (module_name, class_name)


def get_runtime(flavor: str, **kwargs: object) -> ValidationRuntime:
    """Instantiate the registered runtime for *flavor* (lazy import)."""
    name = normalize_flavor(flavor)
    spec = _REGISTRY.get(name)
    if spec is None:
        registered = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise NotImplementedError(
            f"Validation runtime {name!r} is not registered. Registered: {registered}"
        )
    module_name, class_name = spec
    module = importlib.import_module(f".{module_name}", __name__)
    cls = getattr(module, class_name)
    return cls(**kwargs)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Databricks workspace env discovery
# ---------------------------------------------------------------------------

def _load_env_file(path: str) -> None:
    """Best-effort .env loader (no python-dotenv dependency required)."""
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # Don't clobber an already-exported value.
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def detect_databricks_env() -> Optional[dict]:
    """Return ``{host, token, cluster_id}`` if all are resolvable, else None.

    Resolution: process env first, then the explicit ``.env`` path the user
    supplied via ``SCOS_DATABRICKS_ENV_FILE``. Credentials are provided
    deliberately by the user — there is no directory-walking discovery.
    """
    if not all(os.environ.get(k) for k in _DATABRICKS_ENV_KEYS):
        path = os.environ.get("SCOS_DATABRICKS_ENV_FILE")
        if path and os.path.isfile(path):
            _load_env_file(path)
    if not all(os.environ.get(k) for k in _DATABRICKS_ENV_KEYS):
        return None
    return {
        "host": os.environ["DATABRICKS_HOST"].rstrip("/"),
        "token": os.environ["DATABRICKS_TOKEN"],
        "cluster_id": os.environ["DATABRICKS_CLUSTER_ID"],
    }


__all__ = [
    "PHASE_A_FLAVORS",
    "PHASE_B_FLAVORS",
    "TrialContext",
    "TrialRequest",
    "TrialResult",
    "ValidationRuntime",
    "detect_databricks_env",
    "get_runtime",
    "is_phase_b",
    "normalize_flavor",
    "register_runtime",
]
