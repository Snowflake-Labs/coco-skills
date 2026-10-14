"""Validation runtime contract.

Three execution targets share one body; only orchestration differs:

  - ``local``      — in-process SparkSession + Delta catalog (Phase A baseline)
  - ``databricks`` — original source on a remote Databricks cluster (Phase A baseline)
  - ``scos``       — real Snowpark Connect against a cloned schema (Phase B)

All runtimes inherit ``ValidationRuntime`` and implement its abstract methods.
The pipeline for every runtime is (called by the driver before each trial):

    provision(request)               # idempotent, hash-gated — seed golden schema for entrypoint, prewarm cluster
    run_trial(request)               # clone golden + run workload + capture outputs
    cleanup_session(state, database) # discovery-driven external teardown invoked by cleanup.py
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, List, Optional


# Phase A runtimes can produce a local baseline; Phase B is always scos.
PHASE_A_FLAVORS = ("local", "databricks")
PHASE_B_FLAVORS = ("scos",)


def normalize_flavor(flavor: str) -> str:
    """Normalize casing/whitespace. Flavors are canonical: local|databricks|scos."""
    return (flavor or "").strip().lower()


def is_phase_b(flavor: str) -> bool:
    return normalize_flavor(flavor) in PHASE_B_FLAVORS


@dataclass
class TrialRequest:
    """Everything one isolated validation trial needs, runtime-agnostic."""

    trial_id: str
    flavor: str
    project_root: str
    # Path to the adapted entrypoint module, relative to the workload root
    # (source/ for local+databricks, Output/ for scos).
    entrypoint_path: str
    # Callable to invoke after loading the module.
    # None = script mode: module execution (top-level code) is the workload.
    # str  = callable mode: call mod.<callable_name>(spark, **kwargs).
    callable_name: Optional[str] = None
    # Resolved per-entrypoint analysis record (assembled from shared/schemas/).
    ep_config: Dict[str, Any] = field(default_factory=dict)
    # shared/mock_data/<mock_ep_id>/ — golden inputs (read-only).
    mock_data_dir: str = ""
    # results/{phase_a,phase_b}/<trial_id>/ — where baselines land.
    results_dir: str = ""
    # Optional per-entrypoint overrides recorded by the adapter.
    kwargs: Dict[str, Any] = field(default_factory=dict)
    # Extra env vars to set for the duration of the workload run.
    extra_env: Dict[str, str] = field(default_factory=dict)
    # Factories keep ``output_schema``-dependent values lazy; may be None.
    kwargs_factory: Optional[Callable[[str], Dict[str, Any]]] = None
    module_globals_factory: Optional[Callable[[str], Dict[str, Any]]] = None
    partition_key: Optional[str] = None
    # Loaded Validation/state.json (needed by scos for clone + stage reads).
    state_json: Optional[Dict[str, Any]] = None
    # Loaded analysis (assembled from shared/schemas/; needed by scos bridge + import_roots).
    analysis: Optional[Dict[str, Any]] = None
    comparator_path: Optional[str] = None

    @property
    def canonical_flavor(self) -> str:
        return normalize_flavor(self.flavor)


@dataclass
class TrialContext:
    """Per-trial isolation handles created by a runtime before run+capture."""

    trial_id: str
    flavor: str
    output_schema: str
    results_dir: str
    # Pre-existing tables (seeds for local/databricks; SHOW TABLES for scos).
    seed_tables: List[str] = field(default_factory=list)
    # Declared table sinks — used by runtimes/capture to reason about sink output.
    sink_tables: List[str] = field(default_factory=list)
    # Directory for file-sink capture (local/databricks write here; scos=None).
    sink_capture_dir: Optional[str] = None
    run_id: str = ""
    # Optional hook called after workload exits and before capture_results.
    # SCOS uses this to GET staged sink files into sink_capture_dir.
    pre_capture_hook: Optional[Callable[[], None]] = None


@dataclass
class TrialResult:
    """Normalized outcome — identical shape across runtimes."""

    trial_id: str
    flavor: str
    results_dir: str
    ok: bool
    manifest: Dict[str, Any] = field(default_factory=dict)
    output_schema: str = ""
    error: Optional[str] = None

    @property
    def tables_captured(self) -> int:
        return len(self.manifest.get("tables", []))


class ValidationRuntime(ABC):
    """Abstract base for all validation runtimes.

    Pipeline (called by the driver before each trial):

        provision(request: TrialRequest) → None
            Idempotent, hash-gated setup: seed golden schema for the
            entrypoint, prewarm cluster, upload file sources. Called before each trial but
            skips work when the golden schema already exists. Default: no-op.

        run_trial(request: TrialRequest) → TrialResult
            Run one isolated trial: clone golden, execute workload, capture
            outputs, tear down trial schema. Called once per entrypoint.
            Must be implemented by every runtime.

        cleanup_session(state, database, dry_run) → list[str]
            Discovery-driven external teardown invoked by cleanup.py. Drops
            this flavor's golden + clone schemas for the run (run-prefix sweep).
            Returns the list of schema identifiers dropped (or that WOULD be
            dropped when dry_run=True). Default: no-op (returns []).

    Class attribute:
        flavor: str   — one of "local", "databricks", "scos"
    """

    flavor: ClassVar[str]

    def provision(self, request: "TrialRequest") -> None:
        """Idempotent pre-trial setup. Override in runtimes that need it."""
        return None

    @abstractmethod
    def run_trial(self, request: TrialRequest) -> TrialResult:
        """Run one isolated trial and return the captured result."""
        ...

    def cleanup_session(self, *, state: dict, database: str, dry_run: bool = False) -> list:
        """Discovery-driven external teardown invoked by cleanup.py.

        Drops this flavor's golden + clone schemas for the run (run-prefix
        sweep). Returns the list of schema FQNs dropped (or that WOULD be
        dropped when dry_run=True). Default: no-op.
        """
        return []

