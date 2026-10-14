"""
Config Ingestion Framework — base contracts.

A *strategy* is responsible for:
  1. Detecting whether it applies to a given config file (``detect``).
  2. Extracting structured enrichment data from that file (``extract``).

The extracted data is a :class:`ConfigEnrichmentResult`, which the
:class:`ConfigIngestionPass` then applies to the ASG without mutating
the original source files.

Design principles
-----------------
- Non-destructive: strategies only produce *additions* or *replacements*
  for placeholder names.  They never delete existing ASG nodes.
- Graceful: extraction errors produce ``warnings``, not exceptions.
- Extensible: add a new strategy by subclassing
  :class:`ConfigIngestionStrategy` and registering it in
  ``warp_core.config_ingestion.registry``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Spark type mapping (config string → WARP internal type)
# ---------------------------------------------------------------------------

SPARK_TYPE_MAP: dict[str, str] = {
    "StringType":    "STRING",
    "IntegerType":   "INTEGER",
    "LongType":      "LONG",
    "DoubleType":    "DOUBLE",
    "FloatType":     "FLOAT",
    "BooleanType":   "BOOLEAN",
    "DateType":      "DATE",
    "TimestampType": "TIMESTAMP",
    "BinaryType":    "BINARY",
    "ShortType":     "INTEGER",
    "ByteType":      "INTEGER",
    "DecimalType":   "DECIMAL",
    "ArrayType":     "ARRAY",
    "MapType":       "MAP",
    "StructType":    "STRUCT",
    # Also accept lowercase/shorthand versions
    "string":    "STRING",
    "integer":   "INTEGER",
    "int":       "INTEGER",
    "long":      "LONG",
    "double":    "DOUBLE",
    "float":     "FLOAT",
    "boolean":   "BOOLEAN",
    "bool":      "BOOLEAN",
    "date":      "DATE",
    "timestamp": "TIMESTAMP",
}


def spark_type_to_warp(type_str: str) -> str:
    """Convert a Spark type string to a WARP internal type name."""
    # Handle parameterised types like DecimalType(10, 2)
    base = type_str.split("(")[0].strip()
    return SPARK_TYPE_MAP.get(base, "UNKNOWN")


# ---------------------------------------------------------------------------
# Enrichment result models
# ---------------------------------------------------------------------------

@dataclass
class NodeResolution:
    """
    Resolve a placeholder node name to an actual data asset name.

    Targets nodes whose ``name`` starts with ``PTH_`` or equals
    ``"Uninferable"``.
    """

    node_type: str
    """``"data_in"`` or ``"data_out"``."""

    new_name: str
    """The resolved name (e.g. ``"sample_data"`` or ``"DEMO.SPCONN.KIPAWA_RAW"``."""

    source_file: str
    """Config file (relative path) that provided this name."""

    source_key: str
    """JSON key path that contained the value (e.g. ``"pipeline.reader.config.*.s3SourceDirectory"``)."""

    node_id: str | None = None
    """If known, the exact ASG node ID to update (e.g. ``"in_007"``).
    If None, the pass will try to match by node_type + placeholder name."""

    confidence: float = 0.85
    """Confidence level for this resolution."""


@dataclass
class ColumnInference:
    """
    Add columns to a node's schema from a config file.

    Uses ``source="config"`` so they show up in schema coverage.
    """

    node_type: str
    """``"data_in"`` or ``"data_out"``."""

    columns: list[dict[str, str]]
    """
    List of column dicts.  Each dict must have ``"name"``; ``"data_type"`` is
    optional (defaults to ``"UNKNOWN"`` if absent)::

        [{"name": "client_version", "data_type": "STRING"}, ...]
    """

    source_file: str
    """Config file (relative path) that provided the column list."""

    source_key: str
    """JSON key path that contained the column list."""

    node_id: str | None = None
    """Target node ID.  If None, apply to all nodes of ``node_type``."""

    confidence: float = 0.85
    """Confidence weight for inferred columns."""


@dataclass
class ModuleResolution:
    """
    Record which runtime class/module backs a data node or transformation.

    Creates an ``AnalysisWarning`` in the ASG; does not add I/O nodes.
    """

    node_type: str
    """``"data_in"``, ``"data_out"``, or ``"transformation"``."""

    module_name: str
    """Dotted Python module + class name (e.g. ``"dataReaders.S3JsonReader"``)."""

    source_file: str
    source_key: str

    node_id: str | None = None


@dataclass
class ConfigEnrichmentResult:
    """
    Everything a strategy extracted from ONE config file.

    The :class:`ConfigIngestionPass` collects results from all strategies
    and applies them to the ASG in a single non-destructive pass.
    """

    strategy_name: str
    """Human-readable strategy name (e.g. ``"TelenavKipawa"``)."""

    config_file: str
    """Relative path of the config file that was parsed."""

    node_resolutions: list[NodeResolution] = field(default_factory=list)
    column_inferences: list[ColumnInference] = field(default_factory=list)
    module_resolutions: list[ModuleResolution] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)
    """Strategy-specific extra info stored in the ASG for traceability."""


# ---------------------------------------------------------------------------
# Abstract strategy
# ---------------------------------------------------------------------------

class ConfigIngestionStrategy(ABC):
    """
    Abstract base class for config-driven pipeline ingestion strategies.

    To add a new strategy:

    1. Subclass this class.
    2. Implement ``name``, ``description``, ``detect``, and ``extract``.
    3. Register the class in ``warp_core.config_ingestion.registry``.

    Example::

        class MyStrategy(ConfigIngestionStrategy):
            name = "MyFramework"
            description = "Parses MyFramework config files."

            def detect(self, config: dict, file_path: Path) -> bool:
                return "myFramework" in config

            def extract(
                self,
                config: dict,
                file_path: Path,
                workload_root: Path,
                asg: dict,
            ) -> ConfigEnrichmentResult:
                result = ConfigEnrichmentResult(
                    strategy_name=self.name,
                    config_file=str(file_path.relative_to(workload_root)),
                )
                # ... populate result ...
                return result
    """

    #: Unique strategy identifier (used in enrichment metadata).
    name: str = ""

    #: Short description shown in the REPORT.md.
    description: str = ""

    #: Confidence with which this strategy claims to match a file.
    #: Used to break ties when multiple strategies detect the same file.
    priority: int = 10

    @abstractmethod
    def detect(self, config: dict, file_path: Path) -> bool:
        """
        Return ``True`` if this strategy can handle the given config.

        Called once per config file *before* extraction.  Should be cheap
        (key lookups only — no deep traversal).
        """

    @abstractmethod
    def extract(
        self,
        config: dict,
        file_path: Path,
        workload_root: Path,
        asg: dict,
    ) -> ConfigEnrichmentResult:
        """
        Parse *config* and return enrichment data for the ASG.

        Parameters
        ----------
        config:
            The parsed config file (dict or list).
        file_path:
            Absolute path to the config file.
        workload_root:
            Absolute path to the workload root directory.
        asg:
            Current ASG dict (read-only — do not mutate).

        Returns
        -------
        :class:`ConfigEnrichmentResult` with all extracted enrichments.
        """
