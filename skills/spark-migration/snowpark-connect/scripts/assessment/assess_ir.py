"""Intermediate representation (IR) for the migration-readiness HTML report.

The IR is a superset of the fields that prototype
HTML renders across all five tabs (Overview, Detailed Compatibility,
Migration Plan, API Compatibility, Additional Discovery). It is populated
from two complementary sources at Phase 4 of the migrate skill:

  * ``transform_analysis.py``   — risk/finding/compat data from ``analysis.json``
  * ``scan_codebase.py``        — file types, imports, complex patterns, data
                                  sources, dependency graph, migration waves

The two transformers each return a partial Assessment; they are merged via
``Assessment.merge`` before being handed to the adapter. Fields neither source
can populate stay at their defaults (empty list / None), and the adapter
gracefully renders an empty-state placeholder.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Severity = Literal["High", "Medium", "Low"]
"""Per-issue severity. Maps to red/yellow/green badges in the HTML."""

Readiness = Literal["High", "Medium", "Low"]
"""Per-file readiness for migration. INVERTED polarity from Severity in the
HTML CSS (High readiness = green = GOOD; High severity = red = BAD)."""


# ---------------------------------------------------------------------------
# Metadata + workload-level summary
# ---------------------------------------------------------------------------


class AssessmentMetadata(BaseModel):
    """Header-band metadata. Surfaced verbatim in the report header strip."""

    project: str = "unknown-project"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    mode: Literal["CODEBASE", "ANALYSIS_JSON", "HYBRID"] = "ANALYSIS_JSON"
    """``HYBRID`` when both a codebase scan and analysis.json contribute."""
    analysis_json_path: str = ""
    skill_version: str = "0.1.0"


class WorkloadSummary(BaseModel):
    """The six KPI tiles at the top of the Overview tab + headline paragraph."""

    files_scanned: int = 0
    """Total files in the workload (all extensions, not just code)."""
    lines_of_code: int = 0
    """Sum of LOC across known code extensions (``.py``, ``.scala``, ``.java``, ``.sql``)."""
    file_dependencies: int = 0
    """Total edges in the project-internal import graph."""
    library_imports: int = 0
    """Total third-party import statements (``import`` / ``from … import``)."""
    changes_needed: int = 0
    """Total findings + supplementary issues identified."""

    primary_language: str = "Python"
    code_file_count: int = 0
    """Subset of ``files_scanned`` that are code (excludes docs, configs)."""

    executive_summary: str = ""
    """LLM-friendly 1-paragraph TL;DR. Allowed to contain ``<strong>`` / ``<code>``
    for emphasis; rendered with ``|safe`` in the adapter. Defaults to a
    deterministic blurb when the transformers can't author a better one."""


# ---------------------------------------------------------------------------
# Overview tab rows
# ---------------------------------------------------------------------------


class FileTypeRow(BaseModel):
    extension: str
    count: int
    lines: int = 0
    significance: str = ""


class DataSourceRow(BaseModel):
    """Aggregated read/write counts per data source.

    ``connection`` is the transport layer (S3, HDFS, JDBC, GCS, Local).
    ``format`` is the data serialization format (Json, Parquet, CSV, Undefined).
    Legacy rows where only ``format`` is set (= the old scheme-as-format behavior)
    are still valid; the template falls back gracefully.
    """

    format: str
    connection: str = ""
    """Transport/connection type: S3, HDFS, JDBC, GCS, Local, or empty if unknown."""
    reads: int = 0
    writes: int = 0
    supported: bool = True
    paths: list[str] = Field(default_factory=list)
    """Sample of unique paths/connection strings (both directions mixed).
    Retained for back-compat with older IR payloads and legacy consumers;
    prefer ``read_paths`` / ``write_paths`` for direction-split views."""
    files: list[str] = Field(default_factory=list)
    """Source files where references were detected, parallel to ``paths``
    (both directions mixed). Prefer ``read_files`` / ``write_files``."""
    read_paths: list[str] = Field(default_factory=list)
    """Unique paths referenced from read contexts. Populated by the codebase
    scanner; empty in older IR payloads."""
    read_files: list[str] = Field(default_factory=list)
    """Files that hold at least one read against this (connection, format)."""
    write_paths: list[str] = Field(default_factory=list)
    """Unique paths referenced from write contexts."""
    write_files: list[str] = Field(default_factory=list)
    """Files that hold at least one write against this (connection, format)."""


class ComplexPatternRow(BaseModel):
    pattern: str
    occurrences: str
    """String to support both raw counts (``"12"``) and percentages (``"1%"``)."""
    impact: Severity
    files_affected: Optional[int] = None


class MigrationStage(BaseModel):
    """One card in the 'Migration Approach (Summary)' section."""

    name: str
    description: str
    color: Literal["green", "yellow", "red", "gray"] = "gray"


class CompatibilitySummary(BaseModel):
    """Drives the green progress bar + 'X of Y files compatible' line."""

    supported_usages: int = 0
    not_supported_usages: int = 0
    highly_compatible_files: int = 0
    total_code_files: int = 0


# ---------------------------------------------------------------------------
# Detailed Compatibility tab
# ---------------------------------------------------------------------------


class FileSummaryByType(BaseModel):
    type: str
    files: int
    lines: int = 0
    percent: float = 0.0


class FileSummaryByTechnology(BaseModel):
    technology: str
    file_count: int


class SparkApiByCategory(BaseModel):
    category: str
    """e.g. 'DataFrame', 'RDD'."""
    supported: int = 0
    unsupported: int = 0


class SparkApiByStatus(BaseModel):
    status: str
    """e.g. 'Supported', 'NotSupported'."""
    count: int = 0
    percent: float = 0.0


class ThirdPartyLibRow(BaseModel):
    name: str
    import_count: int
    snowpark_supported: bool
    classification: str = "supported"
    """One of: supported, unsupported, internal, unknown. (legacy field)"""
    role: str = "runtime-third-party"
    """Library role for the workload:
    * ``"stdlib"`` — Python standard library; built-in in every Snowflake Python sandbox.
    * ``"internal"`` — module defined inside this workload (not third-party).
    * ``"migration-scope"`` — library the tool rewrites away (pyspark, dbutils, …).
    * ``"test-only"`` — dev/test framework not deployed with the workload (pytest, mock, …).
    * ``"runtime-third-party"`` — genuinely external library needed at runtime.
    """
    not_supported_reason: str = ""
    """Human-readable reason shown in the "No" popover for libraries where
    ``snowpark_supported == False``. Empty when supported."""


class MigrationCategoryRow(BaseModel):
    name: str
    description: str
    """ALWAYS sourced from analyzer findings' ``root_cause`` text — never canned."""
    effort: Severity
    """'Major' (red) collapses to ``High`` here for badge-class mapping."""
    files_affected: int
    occurrences: int
    sample_root_causes: list[str] = Field(default_factory=list)
    """Up to 3 representative ``root_cause`` strings from the contributing findings,
    used for transparency in the report tooltip / drill-down."""


class IssueRow(BaseModel):
    """Row in the EWI Issue Summary table (top of Detailed tab) + per-finding rollup."""

    code: str
    description: str
    count: int = 1
    category: str = ""
    files: list[str] = Field(default_factory=list)
    """Relative paths of the files this issue was found in, sorted and deduped."""
    rule_id: str = ""
    """Originating rule_id from kb_rules.json (empty for rule-less findings)."""
    ewi_code: str = ""
    """Deterministic EWI code from the rule catalog; takes priority over CSV lookup."""
    status_class: str = ""
    """Deterministic status class (F/IO/Error/Warning) from the rule catalog."""
    issue_type: str = "Other"
    """Issue category for display bucketing:
    * ``"Conversion"`` — active work required (status_class == 'Error').
    * ``"Warning"``    — advisory, may or may not need action (status_class == 'Warning' or LLM-only).
    * ``"Parsing"``    — SQL/Python parse error detected in the workload.
    * ``"Fixed"``      — already resolved by the migration tool (status_class == 'Fixed').
    * ``"Other"``      — doesn't fit the above (e.g. LLM-only, no status_class).
    """


class FileCompatibilityRow(BaseModel):
    """Row in the Per-File Compatibility table (bottom of Detailed tab)."""

    path: str
    name: str
    technology: str = "Unknown"
    lines: int = 0
    spark_usages: int = 0
    issues: int = 0
    status: Readiness = "High"


class FileInfoRow(BaseModel):
    """Row in the File Information table (Detailed Compatibility tab).

    Per-file data-flow & Snowflake-hosting rollup. Strictly local — the
    Source System / Target Type columns describe what THIS file touches
    directly. Pipeline lineage (upstream/downstream inheritance) is
    intentionally absent so the table stays readable when a pipeline has
    many transformer stages; that view lives in the data DAG diagram.

    Fields:
      * ``source_system`` — platform label (``"S3"``, ``"JDBC (PostgreSQL)"``,
        ``"REST API"``, ``"Kafka"``, ``"Snowflake"``, …) or ``"In-Memory"``
        for pure Spark transformers, or ``"N/A"`` for utility files.
      * ``target_type`` — one of ``"Snowflake Table"``, ``"Snowflake Stage"``,
        ``"Cloud Storage"``, ``"Streaming Topic"``, ``"In-Memory"``, or the
        unchanged special-purpose values ``"Email"`` / ``"SFTP"`` / ``"API"``
        / ``"File"`` / ``"N/A"``. Filterable so an architect can zero in on
        egress jobs (Cloud Storage), infra work (Streaming Topic), or
        Snowflake-native writes (Snowflake Table / Snowflake Stage).
      * ``target_location`` — concrete path/URI (S3 URL, FQ table name,
        ``@named_stage/…``) or ``""`` for In-Memory returns.
      * ``eai_required`` — one of ``"No"``, ``"Yes"``, ``"Yes (UDF)"``. The UDF
        variant is stronger: network egress on a per-row invocation basis.
      * ``ar_required`` — ``"Yes"`` / ``"No"`` for Python files (import needs
        Snowflake Artifact Repository staging); ``"N/A"`` for Scala/Java.
      * ``ar_packages`` — list of import roots that triggered ``ar_required="Yes"``.
        Empty when ``ar_required`` is ``"No"`` or ``"N/A"``.
      * ``lines`` — code line count, mirroring ``FileCompatibilityRow.lines``.
    """

    path: str
    name: str
    source_system: list[str] = Field(default_factory=lambda: ["N/A"])
    """Data platforms this file reads from: S3, JDBC (PostgreSQL), Kafka, REST API, …
    List because a file can read from multiple sources simultaneously.
    Special sentinels: ``["In-Memory"]`` (pure DataFrame transformer), ``["N/A"]`` (no data role)."""
    target_type: list[str] = Field(default_factory=lambda: ["N/A"])
    """Write mechanisms: Snowflake Table, Cloud Storage, Streaming Topic, Email, …
    List because a file can write to multiple destinations.
    Special sentinels: ``["In-Memory"]``, ``["N/A"]``."""
    target_location: str = ""
    file_formats: list[str] = Field(default_factory=list)
    """Data file formats accessed by this file, e.g. ``["Parquet (read)", "CSV (write)"]``.
    Derived from format-pattern detection; empty for files with no detected I/O."""
    eai_packages: list[str] = Field(default_factory=list)
    """Specific package/service names that triggered EAI detection for this file.
    E.g. ``["requests", "smtplib", "boto3 (lambda)"]``. Empty when ``eai_required == "No"``."""

    @field_validator("source_system", "target_type", mode="before")
    @classmethod
    def _coerce_str_to_list(cls, v: object) -> object:
        """Accept legacy scalar strings (e.g. from old IR JSON or test callers)
        and convert them to single-element lists so the field contract is always
        ``list[str]``."""
        if isinstance(v, str):
            return [v]
        return v
    """Primary concrete path/URI for the first target (S3 URL, FQ table name, stage path).
    Empty for In-Memory or when no specific location could be derived."""
    eai_required: str = "No"
    ar_required: str = "No"
    ar_packages: list[str] = []
    lines: int = 0


class DetailedFinding(BaseModel):
    """One analyzer finding, surfaced verbatim in the per-file drill-down.

    Unlike the aggregated ``issues`` / ``migration_categories`` rows, this keeps
    each finding's raw fields (code snippet, root_cause, explanation, fix) so the
    Per-File Compatibility table can expand each file row into a per-line
    drill-down."""

    file: str
    """Relativized path — matches ``FileCompatibilityRow.path`` so the two
    surfaces group by the same key."""
    name: str
    """Basename, used for the accordion header."""
    lines: str = ""
    """e.g. ``"79-79"``. Kept as a string to preserve the analyzer's format."""
    language: str = "python"
    severity: Severity = "Low"
    """Bucketed from ``final_risk`` via :func:`severity_from_risk`."""
    category: str = ""
    """Migration category bucket (e.g. ``"RDD / SparkContext"``, ``"Streaming"``),
    derived from ``root_cause`` keywords — same bucketing used by the Issue
    Summary rows so the per-file drill-down and the rollup agree."""
    final_risk: float = 0.0
    confidence: str = ""
    code: str = ""
    """The offending source snippet. May be multi-line."""
    root_cause: str = ""
    explanation: str = ""
    fix: Optional[str] = None
    kind: str = ""


# ---------------------------------------------------------------------------
# Migration Plan tab
# ---------------------------------------------------------------------------


class DependencyFile(BaseModel):
    """A row in 'Most Depended-Upon Files' or 'Most Complex Files'."""

    path: str
    name: str
    metric: int
    """Dependents count (for 'most-depended') or complexity item count (for 'most-complex')."""


class MigrationWave(BaseModel):
    name: str
    """e.g. 'Wave 1: Foundation'."""
    layer: str = ""
    """e.g. 'Foundation Layer', 'Core Layer', 'Complex Layer'."""
    depends_on_waves: list[int] = Field(default_factory=list)
    description: str = ""
    files: list[FileCompatibilityRow] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Additional Discovery tab
# ---------------------------------------------------------------------------


class WorkloadClassification(BaseModel):
    # ---- Primary archetype (4-category 2×2 matrix) ----------------------
    classification: str = "Unknown"
    """One of the four project archetypes: 'End-to-End ETL Pipeline',
    'Data Router / Ingestion Hub', 'In-Warehouse Compute',
    'Lightweight Orchestration'."""
    ingestion_level: str = "Low"
    """'High' when ≥ the ingestion-footprint threshold of files touch external
    data; 'Low' otherwise."""
    compute_level: str = "Low"
    """'High' when the codebase averages ≥ the compute-intensity threshold of
    Spark API calls per 10 lines of code; 'Low' otherwise."""
    archetype_color: str = "gray"
    """Badge color for the archetype pill: 'green', 'yellow', 'orange', or 'gray'."""
    migration_profile: str = ""
    """One-paragraph plain-English description of the archetype for the report."""
    # ---- File-based metrics (normalised, not raw operation counts) --------
    ingestion_file_count: int = 0
    """Number of files that contain at least one external read or write."""
    ingestion_file_pct: float = 0.0
    """Fraction [0, 1] of code files that have external I/O."""
    total_files: int = 0
    """Total number of code files scanned (denominator for ingestion_file_pct)."""
    spark_per_10_loc: float = 0.0
    """Spark API call count per 10 lines of code across the whole codebase."""
    # ---- Legacy fields kept for backward compatibility -------------------
    io_operations: int = 0
    transform_operations: int = 0
    description: str = ""
    """Factual one-liner derived from the I/O vs Transform counts. No editorializing."""


class ProjectType(BaseModel):
    label: str = ""
    """One of the three code-complexity tiers:
    'Standard Conversion', 'AI-Assisted Refactoring', 'Specialized Architecture'.
    Infrastructure concerns (EAI, custom packages) do not affect this label."""
    color: Literal["green", "blue", "yellow", "orange", "red", "gray"] = "green"
    resolution_engine: str = ""
    """The primary tool engine(s) that handle this project's code conversion, e.g.
    'Deterministic Transpiler', 'Deterministic Transpiler + Cortex LLM Engine',
    'Cortex LLM Engine + Platform Mapping'."""
    description: str = ""
    """Factual one-liner listing which indicators triggered the label.
    Never includes industry-benchmark style claims."""
    indicators: list[str] = Field(default_factory=list)
    """Each indicator is a fact pulled from the scan (e.g. ``"180 .scala files"``)."""
    expected_action: str = ""
    """Tier-specific expected action HTML fragment, sourced from llm_capabilities.json _tiers.
    Rendered inside the project type advisory when finding counts are available."""
    scan_advisory: str = ""
    """Full advisory HTML for scan-only mode (no analyzer findings yet), sourced from
    llm_capabilities.json _tiers. Replaces the fallback when findings are absent."""


class CodeChurnEstimate(BaseModel):
    category: Readiness = "High"
    """Overall workload code-churn bucket from a multi-signal composite
    (file fraction, issue concentration, code-surface fraction) —
    see :func:`code_churn_from_files`. ``High`` = Ready, ``Medium`` = Light
    Refactor, ``Low`` = Active Refactor. No numeric score: the analyzer's
    0-100 confidence was nondeterministic, so churn is deterministic categories."""
    files_ready: int = 0
    """Count of files needing no changes (per-file status ``High``)."""
    files_light_refactor: int = 0
    """Count of files needing small touch-ups (per-file status ``Medium``)."""
    files_active_refactor: int = 0
    """Count of files needing a focused chunk of work (per-file status ``Low``)."""
    description: str = ""
    """Factual description of the per-file category distribution."""


class SourceSinkInventoryRow(BaseModel):
    direction: Literal["Source", "Sink"]
    category: str
    occurrences: int = 0
    detected: bool = False


class HighRiskFormatRow(BaseModel):
    format: str
    risk: Severity
    detail: str
    recommended_action: str


class RefactorCheckRow(BaseModel):
    name: str
    description: str
    checked: bool = False


class SectionNarratives(BaseModel):
    """Optional plain-language, advisory explanations for the dense factual
    sections (R11). Each field pairs with one report section. Authored by the
    reporter LLM agent grounded in the IR, or left empty so the adapter falls
    back to a deterministic explanation derived from the same IR data.

    Everything here is advisory and never alters the deterministic facts."""

    executive_summary: str = ""
    complex_patterns: str = ""
    workload_classification: str = ""
    project_type: str = ""
    code_churn: str = ""


# ---------------------------------------------------------------------------
# Graph IR — pre-laid-out coordinates for the SVG diagrams
# ---------------------------------------------------------------------------
#
# Layout is computed in Python (``scan_codebase._build_*_graph``) so the
# Jinja template's job is just to emit ``<rect>``/``<text>``/``<path>``
# elements at the IR-provided coordinates. Keeping the layout math in
# Python keeps the template trivial and the IR is the stable contract.


class GraphNode(BaseModel):
    """One file node in a per-module dependency diagram."""

    id: str
    """Unique within the graph (typically the file's rel_path)."""
    label: str
    """Short display label (basename, truncated for fit)."""
    full_label: str = ""
    """Untruncated basename, surfaced in the ``title=`` tooltip."""
    path: str = ""
    """Full file path, surfaced as a deeper tooltip."""
    x: int
    y: int
    width: int = 155
    height: int = 28
    status: Readiness = "High"
    """High → green / Medium → yellow / Low → red. Per-file readiness."""
    in_degree: int = 0
    """Number of project files that directly import this file."""
    blast_radius: int = 0
    """Number of project files that transitively depend on this file —
    the count of jobs that break if this file's interface changes."""
    group: str = ""
    """Semantic group used by the data DAG layout. One of:
    ``"chain"``       — reader / transformer / writer on the execution chain,
    ``"framework"``   — base class / utility / ``__init__.py`` / ``main.py``
                        prerequisite drawn inside the Framework cluster,
    ``""``            — default: no grouping (e.g. isolated island in the
                        legacy top-to-bottom import DAG).

    Historically ``"external-source"`` / ``"external-sink"`` values were
    also emitted for external-endpoint pseudo-nodes drawn as pills above
    and below the chain. That layout was too noisy for real-world
    workloads (7 source + 13 sink pills on Verisk), so external
    endpoints are now attached as metadata on the chain node itself
    (``external_sources`` / ``external_sinks``) and surfaced via a
    tooltip preview + click-opened side panel — the pseudo-nodes are
    no longer emitted."""
    external_sources: list[str] = Field(default_factory=list)
    """Full URIs / table names this file reads from outside the pipeline.
    Populated only for chain nodes (``group == "chain"``). Order is
    preserved from the original signature-extraction pass; duplicates
    within a single file are collapsed by the caller. Surfaced in the
    node's SVG ``<title>`` tooltip (truncated preview) and in a
    click-opened detail panel (full list)."""
    external_sinks: list[str] = Field(default_factory=list)
    """Full URIs / table names this file writes to outside the pipeline.
    Populated only for chain nodes; same order / dedup semantics as
    ``external_sources``."""
    llm_enriched: bool = False
    """True when one or more of this node's ``external_sources`` /
    ``external_sinks`` was contributed by the LLM resolution pass rather
    than the deterministic AST scanner. Renders an "LLM" badge on the
    node tooltip and a ``data-source="llm"`` SVG attribute."""


class GraphCluster(BaseModel):
    """A bounding box drawn around a semantic group of :class:`GraphNode`s.

    Currently used by the data DAG to visually enclose the ``framework``
    group (base classes, utility modules, ``__init__.py`` markers) so users
    see them as a coherent set of migration prerequisites rather than a spray
    of disconnected orphan tiles. Coordinates are pre-computed by the
    scanner; the template just emits an SVG ``<rect>`` at ``(x, y,
    width, height)`` with a dashed border and prints ``label`` above it.
    """

    label: str
    """Human-readable heading, e.g. ``"Framework (migration prerequisites)"``."""
    x: int
    y: int
    width: int
    height: int
    node_ids: list[str] = Field(default_factory=list)
    """IDs of the nodes inside this cluster. Purely informational — the
    node coords themselves already sit within the rect."""


class GraphEdge(BaseModel):
    """A directional dependency edge: ``(x1,y1) -> (x2,y2)``.

    Endpoints are pre-clipped to the source-node bottom and target-node
    top so the SVG ``<line>`` renders cleanly without overlapping the
    rect borders.

    ``source``/``target`` carry the node ids (file rel_paths) the edge
    connects so the rendered SVG can be made interactive (blast-radius
    highlighting) without the JS having to re-derive adjacency from
    coordinates.
    """

    x1: int
    y1: int
    x2: int
    y2: int
    source: str = ""
    target: str = ""
    kind: str = "import"
    """Edge type: ``"import"`` (static import edge), ``"data"`` (data-flow
    edge), ``"framework"`` (dashed summary arrow from the Framework cluster
    to the reader — legacy; no longer emitted after the TB layout),
    ``"orchestrates"`` (dashed blue arrow from an orchestrator file in the
    Framework cluster to the reader node, indicating "this file drives the
    pipeline"), or ``"factory_dispatch"`` (data-flow fan-out edge from a
    factory-dict candidate class into its dispatch orchestrator, rendered
    dashed with a "1-of-N" annotation)."""
    label: str = ""
    """Optional label rendered near the edge midpoint. Populated for
    ``factory_dispatch`` edges (``"1-of-N"``); empty for other kinds."""
    path_d: str = ""
    """Optional SVG path ``d`` attribute. When non-empty the template renders
    the edge as a ``<path>`` bezier instead of a straight ``<line>``; used for
    ``orchestrates`` arrows so they can curve around the framework cluster
    box without crossing through it."""
    label_x: int = 0
    label_y: int = 0
    """Explicit label position (overrides the midpoint-based default). Used by
    curved orchestrates arrows to place their label BELOW the framework
    cluster box so the label doesn't overlap the cluster rectangle."""


class DependencyGraph(BaseModel):
    """A per-module dependency subgraph laid out for SVG rendering."""

    module: str
    """Module bucket name (e.g. ``"common"``, ``"transformers"``)."""
    width: int
    height: int
    file_count: int
    edge_count: int
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    clusters: list[GraphCluster] = Field(default_factory=list)
    """Optional bounding-box overlays drawn behind the nodes. Used by the
    data DAG to visually enclose the Framework prerequisites group; empty
    for the import DAG."""
    pipeline_count: int = 0
    """Number of independent execution pipelines rendered in this graph.
    Populated by the data DAG builder (0 for import graphs). When >1, the
    section-header banner surfaces the pipeline count so viewers can see
    the workload has multiple independent chains rendered side-by-side."""


class WaveGraphNode(BaseModel):
    """One wave rect in the wave-dependencies diagram."""

    wave_index: int
    """1-based wave ordinal."""
    label: str
    """e.g. ``"Wave 1"``."""
    sublabel: str
    """e.g. ``"8 files"``."""
    x: int
    y: int
    width: int = 70
    height: int = 50
    independent: bool = True
    """No prerequisites → drawn green; otherwise drawn blue."""


class WaveGraphEdge(BaseModel):
    """A wave-prerequisite arrow drawn as a quadratic Bezier.

    Path: ``M x1 y1 Q cx cy x2 y2`` — control point ``(cx, cy)`` produces
    the gentle bow that keeps overlapping arrows readable.
    """

    x1: int
    y1: int
    cx: int
    cy: int
    x2: int
    y2: int


class WaveGraph(BaseModel):
    """The wave-dependency diagram, laid out for SVG rendering."""

    width: int
    height: int
    nodes: list[WaveGraphNode] = Field(default_factory=list)
    edges: list[WaveGraphEdge] = Field(default_factory=list)


class CircularDependency(BaseModel):
    """One strongly-connected component of size > 1 in the import graph.

    Files inside a cycle must be deployed together — they can't be ordered
    into separate waves — so they're called out explicitly so the migration
    planner doesn't get a "but why is this one wave?" surprise.
    """

    files: list[str] = Field(default_factory=list)
    """Basenames of the files in the cycle, sorted for stable rendering."""


class IsolatedModuleFile(BaseModel):
    """One file inside an isolated (Quick-Win) module."""

    path: str
    name: str
    lines: int = 0
    status: Readiness = "High"
    """Per-file readiness; backfilled from analyzer findings in ``merge``."""


class IsolatedModule(BaseModel):
    """A connected component that shares zero code with the main project mass.

    These are the "safe pilots": migrating them carries no risk of breaking
    the rest of the pipeline because nothing else imports them and they import
    nothing else in the project. Surfaced as Quick-Win cards in the report.
    """

    files: list[IsolatedModuleFile] = Field(default_factory=list)
    file_count: int = 0
    total_lines: int = 0
    edges: list[tuple[int, int]] = Field(default_factory=list)
    """Intra-module dependency edges as ``(importer_index, importee_index)``
    pairs into ``files`` — lets the report draw a small cluster diagram for
    multi-file islands without re-deriving adjacency."""


# ---------------------------------------------------------------------------
# Recipe-resolved panel (informational only — strictly isolated)
# ---------------------------------------------------------------------------


RecipeKind = Literal["rewrite", "annotate", "comment", "other"]
"""Recipe classification mirrors ``analyze_pyspark._classify_recipe_kind``:
``_rewrite`` suffix → rewrite (deterministic AST change); ``_annotate`` /
``_comment`` → comment-only annotation; everything else → ``other``."""


class RecipeResolvedRow(BaseModel):
    """One row in the standalone 'auto-resolved' panel table.

    Sourced exclusively from ``migration_state.json[recipe_edits]`` via
    ``recipe_resolved_panel.build_recipe_resolved_panel``. It is the
    informational record that a deterministic LibCST recipe touched this
    line during Phase 0.5. It MUST NOT influence any risk / score /
    compatibility / readiness field; see the "Recipe-data isolation
    guarantee" in the Tier-B plan.
    """

    file: str
    """Repo-relative path of the touched file (matches ``recipe_edits`` keys)."""
    line: int
    """Line number of the affected statement. Coordinate system is
    indicated by ``coord_system``; by default the panel rebases marker
    targets to the **original** source so this field aligns with the
    line numbers used everywhere else in the report (Issue Summary,
    Per-File Compatibility). Falls back to post-recipe coords only when
    the line has no faithful original equivalent (see ``coord_system``)."""
    end_line: int = 0
    """Last line of the enclosing statement. Same coordinate system as
    ``line``. Equals ``line`` for single-line statements; equals 0
    when the AST walk was skipped or failed. The template renders
    ``Line N–M`` when ``end_line > line`` and ``Line N`` otherwise."""
    coord_system: str = "original"
    """Either ``"original"`` (the default and overwhelming majority case
    — line numbers reference the pre-Phase-0.5 source so they align
    with the rest of the report) or ``"post"`` (line numbers reference
    the post-Phase-0.5 ``Output/`` file because the marker landed on a
    wholly-recipe-introduced line with no original pre-image). The
    template surfaces ``"post"`` rows with a small "(post-rewrite line)"
    annotation so reviewers know the coords differ."""
    code: str = ""
    """Source-snippet for the rendered ``<pre>`` block, preserving
    original indentation. Source depends on the row type:

    * **Marker-driven rows** (the common case when SCOS markers exist
      in the post-recipe file) — snippet is pulled from the
      **post-recipe** source so the SCOS marker comment the skill
      inserted stays visible alongside the code it describes. This is
      intentional: the marker is the "ground truth" of what the recipe
      did, and the user needs to see comment + code together to make
      sense of the row. Note that ``line`` / ``end_line`` are still
      rebased to original coords (see ``coord_system``), so the
      displayed line range and the snippet's true file location may
      differ by the count of marker comments the recipe inserted —
      that's expected.
    * **Silent-rewrite fallback rows** (no marker, e.g. recipes that
      rewrite without annotating) — snippet is pulled from the
      **original** source at ``line``..``end_line``.

    Empty when the source dir was not supplied or the file/lines could
    not be read; the template hides the ``<pre>`` block in that case."""
    recipe_id: str
    """Recipe identifier (folder name under ``scripts/recipes/``)."""
    kind: RecipeKind = "other"
    """Recipe classification — rewrite, annotate, comment, or other."""
    message: str = ""
    """One-paragraph human-readable description of what the recipe does,
    extracted from the recipe's module docstring. Empty when the recipe
    folder / docstring is missing or when ``recipes_dir`` was not passed
    to the panel builder; the template falls back to ``recipe_id`` in
    that case."""


class UnresolvedDynamicImport(BaseModel):
    """A dynamic-import call site that could NOT be resolved to a workload file.

    Surfaced in the assessment report as an amber warning block so migration
    engineers know the data DAG has blind spots — the workload uses a
    dispatch pattern (``importlib``, ``__import__``, ``spec_from_file``,
    ``entry_points``, or a factory dict) but the target module can't be
    determined from static analysis of the workload's code and config.
    """

    file: str
    """Workload-relative path of the file containing the call."""
    line: int
    """Source line of the dynamic-import call."""
    kind: str
    """One of the detection kinds from ``schema_mine._find_dynamic_import_sites``:
    ``"import_module"``, ``"__import__"``, ``"spec_from_file"``,
    ``"imp_load_source"``, ``"entry_point"``, ``"factory_dict"``."""
    reason: str
    """Specific human-readable diagnostic (e.g.
    ``"config key 'X' not found in any workload config"``, ``"path 'X' did
    not match any workload file"``). Never the string ``"unknown"``."""
    raw_expr: str
    """The original ``ast.unparse`` output for the argument or call, so
    engineers can jump straight to the code without opening the file."""


class UnresolvedDataEdge(BaseModel):
    """A read / write call site whose path argument the AST walker could
    NOT statically resolve to a signature.

    Surfaced in the report as a warning block below the Data Dependency
    Graph diagram (see ``prototype_v1.html.j2``). Each entry's ``reason``
    field is **dynamically derived** at the failure site: it describes the
    AST node type the walker stopped at (e.g. ``ast.Call``, ``ast.Subscript``
    with a dynamic key, ``ast.IfExp``) plus the raw expression. Two
    workloads with different unresolved patterns produce different reasons
    — never drawn from a hardcoded enum.
    """

    file: str
    """Workload-relative path of the file containing the call."""
    line: int
    """Source line of the read/write call."""
    kind: str
    """``"read"`` or ``"write"``."""
    call_expr: str
    """``ast.unparse(node.func)`` — e.g. ``spark.read.parquet``. Lets
    engineers scan the report and identify the API being used."""
    arg_expr: str
    """``ast.unparse(node.args[0])`` (or the equivalent portion of a
    builder chain that failed to resolve). Truncated to keep the row
    readable; the tooltip / underlying JSON carries the full text."""
    reason: str
    """Human-readable diagnostic derived from the AST node the walker
    stopped at. See :mod:`data_edge_ast._describe_ast_shape` for the
    branching logic — the reason IS NOT a fixed string; it names the
    node type (``ast.Call``, ``ast.Subscript``, ``ast.IfExp``, etc.),
    the specific dynamic sub-expression, and (when possible) the
    variable / attribute name that could not be traced."""


EdgeSource = Literal["resolved_unresolved", "newly_discovered"]
"""Provenance of an LLM-produced data edge.

* ``"resolved_unresolved"`` — this edge's ``(file, line, kind)`` was listed in
  the IR's ``unresolved_data_edges`` and the LLM has now resolved it.  The key
  MUST match the original unresolved entry exactly, or the reconciliation gate
  will report it as still-unresolved (a leak).
* ``"newly_discovered"`` — the static AST walker never saw this edge (an API it
  doesn't recognise, or a table inside a SQL file); the LLM found it fresh."""

DataResolutionType = Literal["literal_found", "traced", "inferred"]
"""How confidently a data-edge signature was resolved.  All three are drawn in
the DAG — ``inferred`` is a lower-confidence resolution, not an excluded one, so
that reconciliation (which removes any resolved edge from the unresolved table)
never leaves an inferred edge with nowhere to appear.  The confidence level is
retained in the IR for anyone who wants to filter."""

ImportResolutionType = Literal["literal_found", "traced", "inferred", "unresolvable"]
"""How a dynamic-import site was resolved.  ``unresolvable`` means the LLM read
the code and confirmed the target genuinely cannot be determined (runtime-only
dispatch, missing orchestrator, dead code)."""

UnresolvableSeverity = Literal["critical", "informational", "benign"]
"""How much a confirmed-unresolvable edge/import matters for the migration —
judged by the LLM that read the code, so downstream reporting groups on real
judgment instead of pattern-matching free-form prose.

  ``critical``      — resolution failed because a required input is ABSENT from
                      the workload export: a caller, source file, module, or
                      upstream table that was not included.  A real gap that can
                      block a correct migration; the user likely must supply the
                      missing piece.
  ``informational`` — a genuine external read/write whose exact target is only
                      knowable at runtime (config-driven path, a parameter with
                      no static caller in scope).  The code migrates fine, but
                      this endpoint is a known data-lineage blind spot worth
                      noting.
  ``benign``        — not actually an external data dependency: a scanner
                      misclassification (e.g. an in-memory DataFrame op), dead
                      code, or a destructive / no-op call.  Safe to ignore."""


# --- Edge-kind → lineage role -------------------------------------------------
# The LLM emits richer verbs than plain read/write — observed: read, write,
# delete, drop, merge (and DDL like truncate/create).  Writer→reader lineage
# must classify each verb by whether it PRODUCES or CONSUMES a dataset:
#
#   source  — the file CONSUMES the dataset (it is a downstream reader)
#   sink    — the file PRODUCES the dataset (it is an upstream writer)
#   neutral — a destructive / teardown op (DROP/DELETE/TRUNCATE) that neither
#             produces nor consumes data for lineage purposes
#
# A ``DROP``/``DELETE`` is NOT a data write: treating it as a sink makes a
# teardown look like a producer and fabricates a backward edge to whoever reads
# that table (the Part_2-drops-temp → Part_1-reads-temp cycle bug).  Destructive
# kinds are therefore excluded from edge building entirely.
LINEAGE_SOURCE_KINDS = frozenset({"read"})
LINEAGE_SINK_KINDS = frozenset({
    "write", "merge", "insert", "overwrite", "create", "upsert",
    "save", "saveastable", "append",
})
LINEAGE_NEUTRAL_KINDS = frozenset({"delete", "drop", "truncate"})


def edge_lineage_role(kind: str) -> str | None:
    """Map an edge ``kind`` to its lineage role.

    Returns ``"source"``, ``"sink"``, ``"neutral"``, or ``None`` for an
    unrecognised kind.  Callers building writer→reader edges treat ``neutral``
    and ``None`` the same way — excluded — but ``None`` is worth a warning so a
    genuinely new producing verb doesn't get silently dropped.
    """
    k = (kind or "").strip().lower()
    if k in LINEAGE_SOURCE_KINDS:
        return "source"
    if k in LINEAGE_SINK_KINDS:
        return "sink"
    if k in LINEAGE_NEUTRAL_KINDS:
        return "neutral"
    return None


class _LlmSchemaModel(BaseModel):
    """Base for the LLM-resolution models.  ``use_attribute_docstrings`` makes
    pydantic surface each field's docstring as its JSON-Schema ``description``,
    so the generated ``llm_resolved_data_edges.schema.json`` is self-documenting
    and stays in lockstep with these definitions (one source of truth)."""

    model_config = ConfigDict(use_attribute_docstrings=True)


class LLMResolvedEdge(_LlmSchemaModel):
    """One data edge produced by the LLM data-edge resolution pass.

    Stored in :class:`LLMResolvedDataEdges` and consumed by
    ``render_assessment --llm-resolved-edges`` to augment the data DAG.
    """

    file: str
    """Workload-relative path of the file containing the call."""
    line: int
    """Source line of the read/write call.  For a ``resolved_unresolved`` edge
    this MUST equal the ``line`` of the matching ``unresolved_data_edges``
    entry so the reconciliation gate can pair them."""
    kind: str
    """The I/O verb: ``read`` / ``write`` most commonly, but the LLM also emits
    ``merge`` / ``delete`` / ``drop`` / ``truncate`` when the source uses them.
    :func:`edge_lineage_role` maps each verb to source / sink / neutral for
    edge building — destructive verbs (``drop`` / ``delete`` / ``truncate``) are
    neutral and never create writer→reader edges.  Kept as a free string
    (not a strict enum) so an unforeseen verb degrades to a logged warning
    rather than failing IR validation and silently dropping all LLM data."""
    resolved_signature: str
    """Normalised path/table/bucket signature, ready for the data DAG."""
    resolution_type: DataResolutionType
    """Confidence of the resolution.  All three levels are drawn in the DAG;
    the level is kept for audit / optional filtering."""
    explanation: str = ""
    """Human-readable derivation chain — populated for audit, not rendering."""
    source: EdgeSource
    """See :data:`EdgeSource`.  Required — a missing/typo'd value used to
    silently break reconciliation, so it is now a validated enum."""
    call_expr: str = ""
    """Call expression, populated for ``"newly_discovered"`` edges."""


class LLMUnresolvableEdge(_LlmSchemaModel):
    """A read/write call site the LLM read and confirmed is unresolvable.

    Replaces the former untyped ``list[dict]`` so ``line``/``kind`` are
    guaranteed present (they render as table columns AND key the
    reconciliation gate — an entry missing either silently failed to suppress
    its matching ``unresolved_data_edges`` row).
    """

    file: str
    """Workload-relative path.  Must match the ``unresolved_data_edges`` entry
    this confirms, so the renderer can move the row out of the plain
    'unresolved' table into the 'confirmed unresolvable' table."""
    line: int
    kind: str
    """``"read"`` or ``"write"``."""
    call_expr: str = ""
    arg_expr: str = ""
    why_unresolvable: str
    """Clear reason: dead code, pure runtime value, delegated to a SQL node,
    stale reference outside the workload, etc."""
    severity: UnresolvableSeverity
    """See :data:`UnresolvableSeverity`.  ``critical`` = a required input is
    missing from the export (can block migration); ``informational`` = real I/O
    whose target is only known at runtime (a lineage blind spot); ``benign`` =
    a scanner misclassification / dead code.  Required — this is the LLM's
    severity judgment that the outcome report groups on, so it must reflect the
    reason in ``why_unresolvable``."""


class LLMResolvedImport(_LlmSchemaModel):
    """The LLM's verdict on one dynamic-import site.

    The static dispatch (``scan_codebase._resolve_dynamic_import_site``) gives
    up on any runtime-computed target and files it under
    ``unresolved_dynamic_imports``.  The LLM reads the orchestrator and either
    resolves the target file(s) or confirms it is genuinely unresolvable — so
    the import graph gets the same correctness pass the data graph does.
    """

    file: str
    """Workload-relative path of the orchestrator containing the import call.
    Must match the ``unresolved_dynamic_imports`` entry's ``file``."""
    line: int
    """Must match the ``unresolved_dynamic_imports`` entry's ``line``."""
    kind: str
    """The dispatch kind (``spec_from_file``, ``import_module``,
    ``__import__``, ``imp_load_source``, ``entry_point``, ``factory_dict``)."""
    resolved_targets: list[str] = Field(default_factory=list)
    """Workload-relative path(s) the import loads at runtime.  Non-empty when
    ``resolution_type != "unresolvable"``; empty when unresolvable."""
    resolution_type: ImportResolutionType
    explanation: str = ""
    """Derivation chain for a resolved import."""
    why_unresolvable: str = ""
    """Reason, required when ``resolution_type == "unresolvable"``."""
    severity: Optional[UnresolvableSeverity] = None
    """Severity of an *unresolvable* import (see :data:`UnresolvableSeverity`) —
    set when ``resolution_type == "unresolvable"``, else ``null``.  A missing
    orchestrator/module the workload needs is ``critical``; runtime-only
    dispatch is ``informational``; dead code is ``benign``."""


class OrchestrationEdge(_LlmSchemaModel):
    """A pipeline-ordering / handoff relationship between two workload files.

    Data-signature matching links a *writer* file to a *reader* file only when
    they share a table/path.  But pipeline stages are often chained by control
    or parameter handoffs that share NO table — a Databricks
    ``dbutils.jobs.taskValues.set`` in one notebook read by ``taskValues.get``
    in the next, a ``%run`` include, or an external job-dependency edge.  The
    LLM understands the pipeline order (it writes the advisory), so it records
    those handoffs here; the renderer draws them as dashed ``orchestrates``
    arrows so an orchestrator stage is never left an isolated island.
    """

    from_file: str
    """Workload-relative path of the upstream / orchestrating stage."""
    to_file: str
    """Workload-relative path of the downstream stage it hands off to."""
    mechanism: str = ""
    """How the handoff happens: ``dbutils.taskValues``, ``%run``,
    ``job_dependency``, ``notebook_workflow``, etc.  Shown in the tooltip."""
    explanation: str = ""
    """One line on how this was determined (e.g. which task-value keys)."""


class LLMResolvedDataEdges(_LlmSchemaModel):
    """Container for all LLM data-edge resolution output stored in the IR.

    Written by the ``data_edge_resolver`` agent (see
    ``agents/data_edge_resolver.md``) and read by
    ``render_assessment --llm-resolved-edges``.
    """

    generated_at: Optional[datetime] = None
    model: str = ""
    dispatch_units_processed: int = 0
    edges: list[LLMResolvedEdge] = Field(default_factory=list)
    """All data edges produced by the LLM — both resolved-unresolved and
    newly-discovered.  All resolution types (``literal_found`` / ``traced`` /
    ``inferred``) are drawn in the DAG; ``resolution_type`` records confidence."""
    analyzed_files: list[str] = Field(default_factory=list)
    """Workload-relative paths of every file the LLM read and found to have
    data I/O.  Every path that appears in ``edges`` must also appear here.
    Together with ``excluded_files`` these must cover every
    ``.py`` / ``.sql`` / ``.ipynb`` in the workload — the gate checks this."""
    excluded_files: list[str] = Field(default_factory=list)
    """Workload-relative paths of every file the LLM read and confirmed has
    NO data I/O (pure utility code, init files, test stubs, etc.)."""
    unresolvable_edges: list[LLMUnresolvableEdge] = Field(default_factory=list)
    """Read/write edges the LLM confirmed are runtime-only or otherwise
    structurally unresolvable.  Their ``(file, line, kind)`` keys move the
    matching ``unresolved_data_edges`` rows into the audited 'confirmed
    unresolvable' table."""
    resolved_imports: list[LLMResolvedImport] = Field(default_factory=list)
    """The LLM's verdict on every ``unresolved_dynamic_imports`` site — each
    either resolved to target file(s) or confirmed unresolvable.  Drives the
    reconciliation of the dynamic-import table (parallel to how ``edges`` +
    ``unresolvable_edges`` reconcile the data-edge table)."""
    orchestration_edges: list[OrchestrationEdge] = Field(default_factory=list)
    """Pipeline-ordering handoffs between files that share no table (task
    values, ``%run``, job dependencies).  Drawn as dashed ``orchestrates``
    arrows so orchestrator stages join the DAG instead of rendering as
    isolated islands."""
    llm_insights: list[str] = Field(default_factory=list)
    """Short advisory bullets (one sentence each) the LLM agent writes after
    analysing all unresolvable edges — e.g. missing orchestration files,
    dead-code patterns, incomplete workload exports.  Rendered in the HTML
    report as an "Advisory — what this means" callout."""


# ---------------------------------------------------------------------------
# Top-level IR
# ---------------------------------------------------------------------------


class Assessment(BaseModel):
    """The top-level IR. One instance per assessment run.

    The IR is a strict superset of what the adapter renders. New top-level
    fields go here (rather than as render-time computations) so the IR JSON
    stays the stable contract.
    """

    metadata: AssessmentMetadata = Field(default_factory=AssessmentMetadata)
    workload: WorkloadSummary = Field(default_factory=WorkloadSummary)

    # Overview tab
    file_types: list[FileTypeRow] = Field(default_factory=list)
    data_sources: list[DataSourceRow] = Field(default_factory=list)
    complex_patterns: list[ComplexPatternRow] = Field(default_factory=list)
    compatibility: CompatibilitySummary = Field(default_factory=CompatibilitySummary)
    recommendations: list[str] = Field(default_factory=list)
    migration_stages: list[MigrationStage] = Field(default_factory=list)

    # Detailed Compatibility tab
    file_summary_by_type: list[FileSummaryByType] = Field(default_factory=list)
    file_summary_by_technology: list[FileSummaryByTechnology] = Field(default_factory=list)
    spark_api_by_category: list[SparkApiByCategory] = Field(default_factory=list)
    spark_api_by_status: list[SparkApiByStatus] = Field(default_factory=list)
    third_party_libs: list[ThirdPartyLibRow] = Field(default_factory=list)
    migration_categories: list[MigrationCategoryRow] = Field(default_factory=list)
    issues: list[IssueRow] = Field(default_factory=list)
    files: list[FileCompatibilityRow] = Field(default_factory=list)
    detailed_findings: list[DetailedFinding] = Field(default_factory=list)
    """Per-finding drill-down rendered under each expandable Per-File
    Compatibility row. Analyzer-only; the codebase scanner never populates this."""

    file_info: list[FileInfoRow] = Field(default_factory=list)
    """Rows for the File Information table (Detailed Compatibility tab). One
    per code file, populated by the codebase scanner from per-file I/O and
    imports. Strictly local — no DAG-inherited lineage. Empty when no
    workload-dir scan ran."""

    # Migration Plan tab
    migration_strategy: str = ""
    most_depended_files: list[DependencyFile] = Field(default_factory=list)
    most_complex_files: list[DependencyFile] = Field(default_factory=list)
    cross_module_dependencies: int = 0
    migration_waves: list[MigrationWave] = Field(default_factory=list)
    dependency_graphs: list[DependencyGraph] = Field(default_factory=list)
    """Legacy per-module subgraphs (folder-bucketed, intra-module deps only).
    Superseded by ``dependency_graph`` (unified, cross-folder); retained for
    back-compat with older IR payloads but no longer populated or rendered."""
    dependency_graph: Optional[DependencyGraph] = None
    """Unified, cross-folder dependency graph laid out by global dependency
    depth (leaves at top). Edges carry source/target ids for blast-radius
    highlighting. This is the diagram the report actually renders."""
    data_dependency_graph: Optional[DependencyGraph] = None
    """Data-flow graph: edges are writer→reader pairs discovered by schema_mine.
    Separate from ``dependency_graph`` (import edges) so both can be rendered
    side-by-side with distinct visual styling. None when schema_mine is
    unavailable or no writer→reader matches are found."""
    unresolved_dynamic_imports: list[UnresolvedDynamicImport] = Field(default_factory=list)
    """Dynamic-import call sites that the resolver couldn't tie to any workload
    file — surfaced in an amber warning block below the data DAG so engineers
    see the blind spots in the graph."""
    unresolved_data_edges: list[UnresolvedDataEdge] = Field(default_factory=list)
    """Read / write call sites whose path argument the AST walker couldn't
    statically resolve to a signature. Surfaced in a second amber warning
    block below the data DAG diagram. Each entry's ``reason`` field is
    dynamically derived from the AST node the walker stopped at — see
    :class:`UnresolvedDataEdge`."""
    wave_graph: Optional[WaveGraph] = None
    """Pre-laid-out wave-dependency diagram (curved Bezier arrows)."""
    circular_dependencies: list[CircularDependency] = Field(default_factory=list)
    """SCCs of size > 1 in the import graph — files locked into the same wave."""
    isolated_modules: list[IsolatedModule] = Field(default_factory=list)
    """Connected components disconnected from the main project mass — safe
    "Quick-Win" pilots that can be migrated first without breaking anything."""
    largest_component_size: int = 0
    """File count of the largest connected component (the "main mass"), used to
    contextualize how isolated the Quick-Win modules are."""
    main_cluster: Optional[IsolatedModule] = None
    """The largest connected component — files coupled tightly enough that they
    should migrate together. Shown alongside the islands so every logic file is
    accounted for (islands + main cluster == all logic files)."""
    package_marker_count: int = 0
    """Number of ``__init__.py`` package markers (no migratable logic),
    reported so the file accounting reconciles to the total file count."""

    # Additional Discovery tab
    workload_classification: WorkloadClassification = Field(default_factory=WorkloadClassification)
    project_type: ProjectType = Field(default_factory=ProjectType)
    code_churn: CodeChurnEstimate = Field(default_factory=CodeChurnEstimate)
    sources_sinks_inventory: list[SourceSinkInventoryRow] = Field(default_factory=list)
    high_risk_formats: list[HighRiskFormatRow] = Field(default_factory=list)
    common_refactors: list[RefactorCheckRow] = Field(default_factory=list)

    # Advisory narrative layer (R11). Populated by the reporter agent via
    # ``render_assessment --narratives-inline-json``; the adapter renders a
    # deterministic fallback for any field left empty.
    narratives: SectionNarratives = Field(default_factory=SectionNarratives)

    # Phase-0.5 auto-resolved panel (informational only). Populated POST-MERGE
    # by ``render_assessment.build_assessment`` from
    # ``migration_state.json[recipe_edits]`` via
    # ``recipe_resolved_panel.build_recipe_resolved_panel``. The merge()
    # function deliberately does NOT touch this field — keeping recipe data
    # out of all risk/score/compatibility math (Recipe-data isolation
    # guarantee, Tier-B plan).
    recipe_resolved: list[RecipeResolvedRow] = Field(default_factory=list)

    # LLM data-edge resolution results (Part B). Populated POST-RENDER by
    # the ``data_edge_resolver`` agent; consumed by
    # ``render_assessment --llm-resolved-edges``. The merge() function
    # deliberately does NOT touch this field.
    llm_resolved_data_edges: Optional[LLMResolvedDataEdges] = None

    def merge(self, other: "Assessment") -> "Assessment":
        """Deterministic field-wise merge.

        Used when both ``transform_analysis`` and ``scan_codebase`` contribute
        to the same report. Rules:

          * Scalars on ``workload``: pointwise ``max`` for counts; the richer
            source's non-numeric fields win when the merged copy is empty.
          * Free lists (``file_types``, ``data_sources``, …): ``self + other``.
          * Lists with a natural key (``files`` by basename, ``issues`` by
            ``(code, description)``): merged with dedup.
          * Metadata mode becomes ``HYBRID`` whenever the two sources disagree.
        """
        merged = self.model_copy(deep=True)

        if other.metadata.mode != merged.metadata.mode:
            merged.metadata.mode = "HYBRID"
        if not merged.metadata.analysis_json_path and other.metadata.analysis_json_path:
            merged.metadata.analysis_json_path = other.metadata.analysis_json_path

        w = merged.workload
        ow = other.workload
        for fld in (
            "files_scanned", "lines_of_code", "file_dependencies",
            "library_imports", "changes_needed", "code_file_count",
        ):
            setattr(w, fld, max(getattr(w, fld), getattr(ow, fld)))
        if w.primary_language in ("", "unknown", "Python") and ow.primary_language not in ("", "unknown"):
            w.primary_language = ow.primary_language
        if not w.executive_summary:
            w.executive_summary = ow.executive_summary

        # Pointwise max on the compatibility summary (rare to have both populated)
        cs = merged.compatibility
        ocs = other.compatibility
        cs.supported_usages = max(cs.supported_usages, ocs.supported_usages)
        cs.not_supported_usages = max(cs.not_supported_usages, ocs.not_supported_usages)
        cs.highly_compatible_files = max(cs.highly_compatible_files, ocs.highly_compatible_files)
        cs.total_code_files = max(cs.total_code_files, ocs.total_code_files)

        # Free-concat lists
        for fld in (
            "file_types", "data_sources", "complex_patterns",
            "recommendations", "migration_stages",
            "file_summary_by_type", "file_summary_by_technology",
            "spark_api_by_category", "spark_api_by_status", "third_party_libs",
            "migration_categories", "most_depended_files", "most_complex_files",
            "migration_waves", "sources_sinks_inventory", "high_risk_formats",
            "common_refactors",
        ):
            getattr(merged, fld).extend(getattr(other, fld))

        # Single-instance models: prefer self when populated, else other
        if not merged.workload_classification.classification or merged.workload_classification.classification == "Unknown":
            merged.workload_classification = other.workload_classification
        if not merged.project_type.label:
            merged.project_type = other.project_type
        # ``code_churn`` is recomputed from the merged per-file readiness
        # distribution once the file rows are finalized below (see
        # ``code_churn_from_files``), so no side's value is merged here.
        if not merged.migration_strategy:
            merged.migration_strategy = other.migration_strategy
        # Advisory narratives: field-wise, self wins unless empty. Neither
        # transformer sets these by construction (they're injected later via
        # ``--narratives-inline-json``), but merge defensively in case one side does.
        for fld in ("complex_patterns", "workload_classification", "project_type", "code_churn"):
            if not getattr(merged.narratives, fld) and getattr(other.narratives, fld):
                setattr(merged.narratives, fld, getattr(other.narratives, fld))
        merged.cross_module_dependencies = max(merged.cross_module_dependencies, other.cross_module_dependencies)
        # Pre-laid-out graphs come from the codebase scan only — the analyzer
        # transformer doesn't know the import graph. ``self`` wins unless it's
        # empty (e.g. a CSV-only analysis-only run with no workload_dir).
        if not merged.dependency_graphs and other.dependency_graphs:
            merged.dependency_graphs = list(other.dependency_graphs)
        if merged.dependency_graph is None and other.dependency_graph is not None:
            merged.dependency_graph = other.dependency_graph
        if merged.wave_graph is None and other.wave_graph is not None:
            merged.wave_graph = other.wave_graph
        # Unresolved diagnostics: always concatenate + deduplicate so both
        # scan results contribute entries. The prior "self wins if non-empty"
        # logic silently dropped the other result's edges whenever both were
        # populated (regression: second scan result's edges were lost).
        if other.unresolved_dynamic_imports:
            seen_dyn: set[tuple[str, int, str]] = {
                (e.file, e.line, e.kind) for e in merged.unresolved_dynamic_imports
            }
            for e in other.unresolved_dynamic_imports:
                key_dyn = (e.file, e.line, e.kind)
                if key_dyn not in seen_dyn:
                    merged.unresolved_dynamic_imports.append(e)
                    seen_dyn.add(key_dyn)
        if other.unresolved_data_edges:
            seen_data: set[tuple[str, int, str]] = {
                (e.file, e.line, e.kind) for e in merged.unresolved_data_edges
            }
            for e in other.unresolved_data_edges:
                key_data = (e.file, e.line, e.kind)
                if key_data not in seen_data:
                    merged.unresolved_data_edges.append(e)
                    seen_data.add(key_data)
        if not merged.circular_dependencies and other.circular_dependencies:
            merged.circular_dependencies = list(other.circular_dependencies)
        # Isolated modules come from the codebase scan only (the analyzer
        # doesn't know the import graph). ``self`` wins unless empty.
        if not merged.isolated_modules and other.isolated_modules:
            merged.isolated_modules = list(other.isolated_modules)
        if merged.main_cluster is None and other.main_cluster is not None:
            merged.main_cluster = other.main_cluster
        merged.largest_component_size = max(
            merged.largest_component_size, other.largest_component_size
        )
        merged.package_marker_count = max(
            merged.package_marker_count, other.package_marker_count
        )
        # Per-finding drill-down is analyzer-only; the codebase scan never
        # produces findings. ``self`` wins unless it's empty (e.g. a
        # codebase-only side merging in the analyzer side).
        if not merged.detailed_findings and other.detailed_findings:
            merged.detailed_findings = list(other.detailed_findings)

        # files: dedup by path (unique), re-derive status from merged
        # issues. Basename is only used as a last-resort fallback for the
        # legacy case where one producer emits a bare filename and the
        # other emits a rel_path. Keying on basename alone would silently
        # collapse every ``__init__.py`` across the project into one row
        # and over-count the issues on each.
        merged.files = _dedup_files(merged.files + other.files)

        # file_info: only the codebase scanner populates this. If ``self``
        # is empty (analyzer-only run merging with codebase), take other's
        # rows verbatim. Otherwise keep self's rows — the scanner has the
        # richer per-file signal.
        if not merged.file_info and other.file_info:
            merged.file_info = list(other.file_info)

        # Backfill the post-merge per-file readiness onto every migration-
        # wave file row. Wave file rows are produced by ``scan_codebase``
        # *before* the analyzer findings exist, so they all start out at
        # the scanner's optimistic default (``issues=0``, ``status="High"``).
        # Without this step the wave-list badges in the Migration Plan tab
        # don't agree with the per-file table in the Detailed tab — a file
        # marked Low there would still show High in its wave.
        files_by_path = {f.path: f for f in merged.files}
        files_by_name_unique: dict[str, FileCompatibilityRow | None] = {}
        for f in merged.files:
            if f.name in files_by_name_unique:
                files_by_name_unique[f.name] = None  # ambiguous: don't use
            else:
                files_by_name_unique[f.name] = f
        for wave in merged.migration_waves:
            for wf in wave.files:
                canonical = files_by_path.get(wf.path) or files_by_name_unique.get(wf.name)
                if canonical is None:
                    continue
                wf.issues = canonical.issues
                wf.spark_usages = max(wf.spark_usages, canonical.spark_usages)
                wf.lines = max(wf.lines, canonical.lines)
                wf.status = canonical.status

        # Same backfill for dependency-graph nodes. The scanner stamps every
        # node with ``status="High"`` because it has no access to analyzer
        # findings; we re-color them here so the SVG nodes match the per-file
        # readiness table. Covers both the unified graph (rendered), the data
        # DAG (which now includes every code file — see
        # ``_build_unified_dependency_graph(include_all_files=True)``), and any
        # legacy per-module subgraphs (back-compat).
        _graphs_to_recolor = list(merged.dependency_graphs)
        if merged.dependency_graph is not None:
            _graphs_to_recolor.append(merged.dependency_graph)
        if merged.data_dependency_graph is not None:
            _graphs_to_recolor.append(merged.data_dependency_graph)
        for graph in _graphs_to_recolor:
            for node in graph.nodes:
                canonical = files_by_path.get(node.id) or files_by_path.get(node.path)
                if canonical is None:
                    canonical = files_by_name_unique.get(node.full_label or node.label)
                if canonical is not None:
                    node.status = canonical.status

        # Recolor isolated-module and main-cluster files from the merged
        # readiness too, so each card flags any file that turned out to need work.
        cluster_modules = list(merged.isolated_modules)
        if merged.main_cluster is not None:
            cluster_modules.append(merged.main_cluster)
        for module in cluster_modules:
            for mf in module.files:
                canonical = files_by_path.get(mf.path) or files_by_name_unique.get(mf.name)
                if canonical is not None:
                    mf.status = canonical.status

        # issues: dedup by (code, description)
        issues_by_key = {(i.code, i.description): i for i in merged.issues}
        for i in other.issues:
            key = (i.code, i.description)
            if key in issues_by_key:
                existing = issues_by_key[key]
                existing.count += i.count
                existing.files = sorted(set(existing.files) | set(i.files))
                # Prefer rule-provided ewi_code/status_class over empty.
                if i.ewi_code and not existing.ewi_code:
                    existing.ewi_code = i.ewi_code
                    existing.status_class = i.status_class
                    existing.rule_id = i.rule_id
                if i.issue_type and i.issue_type != "Other" and existing.issue_type == "Other":
                    existing.issue_type = i.issue_type
            else:
                merged.issues.append(i)
                issues_by_key[key] = i

        # Regenerate the executive summary using post-merge counts. The
        # analyzer-side string was baked from the analyzer's pre-merge
        # ``files_agg`` (one entry per distinct path *string*), which
        # over-counts when the analyzer's findings have multiple path
        # shapes for the same file (e.g. abs paths that don't sit under
        # workload_root get basename-only fallback, splitting "utils.py"
        # off from "src/common/utils.py"). After merge we know the true
        # set of files, so we re-render.
        files_with_findings = sum(1 for f in merged.files if f.issues > 0)
        total_findings = sum(i.count for i in merged.issues)
        # Build severity counts from issue_type (preferred) with a code-suffix
        # fallback for older IR payloads that pre-date the issue_type field.
        sev_counts = {"High": 0, "Medium": 0, "Low": 0}
        for i in merged.issues:
            it = (i.issue_type or "").strip()
            if it == "Conversion":
                sev_counts["High"] += i.count
            elif it in ("Warning", "Other"):
                sev_counts["Medium"] += i.count
            elif it in ("Parsing", "Fixed"):
                sev_counts["Low"] += i.count
            else:
                # Legacy fallback: code suffix -H/-M/-L
                code = (i.code or "").upper()
                if code.endswith("-H"):
                    sev_counts["High"] += i.count
                elif code.endswith("-M"):
                    sev_counts["Medium"] += i.count
                elif code.endswith("-L"):
                    sev_counts["Low"] += i.count
        # Re-derive CompatibilitySummary counts from the merged files. The
        # analyzer-side ``total_code_files`` is ``len(files_agg)`` which
        # over-counts when paths are fragmented (same bug as the exec
        # summary); ``highly_compatible_files`` is per-producer and only
        # makes sense after the per-file statuses have been backfilled.
        # Doing this in merge keeps the "X of Y compatible" line in
        # Compatibility Summary in sync with the per-file table.
        if merged.files:
            merged.compatibility.total_code_files = len(merged.files)
            merged.compatibility.highly_compatible_files = sum(
                1 for f in merged.files if f.status == "High"
            )
            # Re-render the four "Migration Approach (Summary)" cards from
            # the merged file table. The scanner ran these counts off its
            # own pre-merge ``files_rows`` where every file looked "High"
            # because analyzer issues weren't joined in yet, so the cards
            # drifted from the wave plan and the per-file readiness table.
            high = merged.compatibility.highly_compatible_files
            medium = sum(1 for f in merged.files if f.status == "Medium")
            low = sum(1 for f in merged.files if f.status == "Low")
            merged.migration_stages = render_migration_stages(
                high=high, medium=medium, low=low
            )
            # Code churn follows the same finalized per-file readiness table.
            merged.code_churn = code_churn_from_files(merged.files)

        if total_findings > 0 and files_with_findings > 0:
            merged.workload.executive_summary = render_executive_summary(
                total_findings=total_findings,
                files_count=files_with_findings,
                primary_language=merged.workload.primary_language or "Python",
                severity_counts=sev_counts,
            )

        return merged


# ---------------------------------------------------------------------------
# Helpers shared by transformers and adapter
# ---------------------------------------------------------------------------


def severity_from_risk(final_risk: float) -> Severity:
    """Map a 0-1 analyzer risk score to a Severity bucket.

    Thresholds mirror ``migrate-pyspark-to-snowpark-connect/agents/fixer.md``:
    ``>= 0.7`` ⇒ ``High``, ``>= 0.3`` ⇒ ``Medium``, else ``Low``.
    """
    if final_risk >= 0.7:
        return "High"
    if final_risk >= 0.3:
        return "Medium"
    return "Low"


def render_migration_stages(*, high: int, medium: int, low: int) -> list[MigrationStage]:
    """Single source of truth for the four "Migration Approach (Summary)" cards.

    Called by ``scan_codebase`` (with scanner-only file statuses, before any
    analyzer issues are merged in) and again by :meth:`Assessment.merge`
    once the merged file table tells us the true post-merge per-file
    readiness distribution. Without this regen the cards lag the wave
    plan — every file looks "High" pre-merge because the scanner has no
    issue signal yet, but the waves and per-file table show the real
    High/Medium/Low mix after analyzer issues are joined in.

    Card names, colors, and the Stage-4 description are verbatim from the
    reference prototypes (``agents/workloads_migration/migration_readiness_report.html``
    and ``Flashfood_Codebase_Report.html``); only the per-bucket file
    counts in Stages 1–3 are interpolated.
    """
    return [
        MigrationStage(
            name="Stage 1: Foundation & Quick Wins",
            description=(
                f"Migrate {high} highly-compatible files first. Set up Snowpark sessions, "
                "convert simple DataFrame operations. Build confidence and establish patterns."
            ),
            color="green",
        ),
        MigrationStage(
            name="Stage 2: Core Transformation",
            description=(
                f"Migrate {medium} medium-complexity files. Handle Spark API renames, "
                "helper conversions, and supported transformations."
            ),
            color="yellow",
        ),
        MigrationStage(
            name="Stage 3: Complex Refactoring",
            description=(
                f"Tackle {low} complex files requiring significant rework "
                "(RDD replacement, streaming redesign, ML migration)."
            ),
            color="red",
        ),
        MigrationStage(
            name="Stage 4: Validation & Cutover",
            description=(
                "End-to-end testing, data validation, performance benchmarking, "
                "and production cutover."
            ),
            color="gray",
        ),
    ]


def render_executive_summary(
    *,
    total_findings: int,
    files_count: int,
    primary_language: str,
    severity_counts: dict[str, int],
) -> str:
    """Single source of truth for the Overview-tab executive summary string.

    Called by ``transform_analysis`` (with analyzer-only counts) and again
    by :meth:`Assessment.merge` once the merged file rows give us the true
    post-dedup ``files_count``. Keeping the formatting here means the
    merge can substitute corrected numbers without re-implementing the
    text.
    """
    files_word = "file" if files_count == 1 else "files"
    return (
        f"This <strong>{primary_language}</strong> workload has "
        f"<strong>{total_findings}</strong> Snowpark Connect compatibility "
        f"finding(s) across <strong>{files_count}</strong> {files_word}."
    )


def readiness_from_issues(issues: int) -> Readiness:
    """Bucket a file's per-file readiness: 0 issues ⇒ ``High``;
    1-2 ⇒ ``Medium``; 3+ ⇒ ``Low``."""
    if issues == 0:
        return "High"
    if issues <= 2:
        return "Medium"
    return "Low"


def code_churn_from_files(files: list["FileCompatibilityRow"]) -> "CodeChurnEstimate":
    """Multi-signal code-churn category from the per-file readiness table.

    Three signals are combined into a composite score for each tier:

    * **File fraction (50%)** — what portion of files fall in this tier.
    * **Issue concentration (30%)** — share of total compatibility issues in
      this tier, scaled by ``min(total_issues / 10, 1)`` so a single hard file
      in a 100-file workload with three total issues cannot dominate the score.
    * **Code surface (20%)** — fraction of total lines that live in this tier
      (proxy for how much code needs attention, not how many lines change).

    Thresholds for the **Active-Refactor** composite:

    * ``>= 0.20`` → ``"Low"`` (Active Refactor) — enough concentrated effort
      in the hard-work zone to plan focused development sprints.
    * ``>= 0.02`` or light composite ``>= 0.08`` → ``"Medium"`` (Light Refactor)
      — detectable work but mostly small targeted fixes.
    * below both thresholds → ``"High"`` (Ready) — essentially drop-in.
    """
    ready_files = [f for f in files if f.status == "High"]
    light_files = [f for f in files if f.status == "Medium"]
    active_files = [f for f in files if f.status == "Low"]
    n_ready, n_light, n_active = len(ready_files), len(light_files), len(active_files)
    total = n_ready + n_light + n_active
    if total == 0:
        return CodeChurnEstimate()

    total_issues = sum(f.issues for f in files)
    total_lines = sum(f.lines for f in files)
    # Scale the issue signal: when total issues is tiny, the fraction of issues
    # in any one tier can be misleadingly large (e.g. 1 file with 3 issues in a
    # 100-file all-clean workload → 100% of issues in that tier). Ramp the
    # weight from 0 at 0 issues to full at 10+.
    issue_scale = min(total_issues / 10.0, 1.0)

    def _composite(tier: list) -> float:
        file_frac = len(tier) / total
        issue_frac = sum(f.issues for f in tier) / total_issues if total_issues else 0.0
        line_frac = sum(f.lines for f in tier) / total_lines if total_lines else 0.0
        return 0.50 * file_frac + 0.30 * issue_scale * issue_frac + 0.20 * line_frac

    active_score = _composite(active_files)
    light_score = _composite(light_files)

    if active_score >= 0.20:
        category: Readiness = "Low"
    elif active_score >= 0.02 or light_score >= 0.08:
        category = "Medium"
    else:
        category = "High"

    label = {"High": "Ready", "Medium": "Light Refactor", "Low": "Active Refactor"}[category]
    files_word = "file" if total == 1 else "files"
    description = (
        f"{n_ready} ready, {n_light} light-refactor, {n_active} active-refactor "
        f"across {total} code {files_word}. Overall: {label}."
    )
    return CodeChurnEstimate(
        category=category,
        files_ready=n_ready,
        files_light_refactor=n_light,
        files_active_refactor=n_active,
        description=description,
    )


def _dedup_files(rows: list[FileCompatibilityRow]) -> list[FileCompatibilityRow]:
    """Merge per-file rows from multiple producers.

    Three coalescing stages:

    1. **Exact path match.** The codebase scanner and the analyzer both
       prefer to emit ``src/foo/bar.py``-shaped paths; identical strings
       collapse here.
    2. **Bare-basename → unique long path.** If the analyzer falls back
       to emitting just ``"bar.py"`` (e.g. when its finding's absolute
       path doesn't sit under the chosen workload_root, or its
       sub-transformer never knew the full path), fold those rows into
       the long-path row whose basename uniquely matches. If multiple
       long paths share that basename (e.g. ``__init__.py``), the bare
       row stays orphan rather than over-counting one bucket.
    3. **Empty-path fallback.** Same idea, but for rows that arrived
       with no ``path`` at all.

    Issue counts merge by SUM rather than MAX so that split findings
    (the most common cause of fragmentation in stage 2) actually
    re-aggregate. The codebase scanner always emits ``issues=0`` so
    summing is equivalent to max in the normal one-producer-per-bucket
    case.
    """
    def _is_bare(p: str) -> bool:
        return bool(p) and "/" not in p and "\\" not in p

    by_path: dict[str, FileCompatibilityRow] = {}
    pending: list[FileCompatibilityRow] = []
    for row in rows:
        if row.path and not _is_bare(row.path) and row.path in by_path:
            _absorb(by_path[row.path], row)
        elif row.path and not _is_bare(row.path):
            by_path[row.path] = row.model_copy()
        else:
            pending.append(row)

    # Build a name -> long-path index, marking ambiguous basenames as None.
    name_to_long: dict[str, FileCompatibilityRow | None] = {}
    for f in by_path.values():
        name_to_long[f.name] = f if f.name not in name_to_long else None

    for row in pending:
        target = name_to_long.get(row.name)
        if target is not None:
            _absorb(target, row)
        else:
            # Truly orphan: keep as its own row keyed by name/path.
            key = row.path or row.name
            if key in by_path:
                _absorb(by_path[key], row)
            else:
                by_path[key] = row.model_copy()

    return list(by_path.values())


def _absorb(existing: FileCompatibilityRow, other: FileCompatibilityRow) -> None:
    # SUM (not MAX) for issues: when the analyzer fragments findings for
    # the same file across multiple path shapes (see stage 2 in
    # _dedup_files), MAX would silently drop one half. The scanner always
    # contributes issues=0 so summing is safe in the normal case.
    existing.issues = existing.issues + other.issues
    existing.spark_usages = max(existing.spark_usages, other.spark_usages)
    existing.lines = max(existing.lines, other.lines)
    if other.technology and existing.technology in ("", "Unknown"):
        existing.technology = other.technology
    existing.status = readiness_from_issues(existing.issues)
