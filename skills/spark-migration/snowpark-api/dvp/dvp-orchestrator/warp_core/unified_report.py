"""
Unified Report Generator - Comprehensive markdown report for a workload.

Consolidates:
- Parsing report (from ASG)
- Anomaly report
- Diagnostic report  
- Issues report
- Schema/synthetic data stats

Generates a single human-readable markdown summary with a score.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from warp_core.diagnostics import DiagnosticReport, Severity


# ---------------------------------------------------------------------------
# Confidence weights for weighted Schema scoring.
# Each typed column contributes its weight to the Schema ratio instead of a
# flat 1.0, reflecting how reliable the type inference source is.
# ---------------------------------------------------------------------------
CONFIDENCE_WEIGHTS: dict[str, float] = {
    # Evidence tier — column explicitly observed in code (confidence = 1.0)
    "explicit": 1.0, "filter_condition": 1.0, "join_key": 1.0,
    "group_by": 1.0, "aggregation": 1.0, "select": 1.0,
    "order_by": 1.0, "function_arg": 1.0, "catalog": 1.0,
    "udf_semantic": 1.0, "usage": 1.0,
    # Declaration tier — column declared explicitly by the developer in source code
    # (StructType / DDL / companion config file): reliable, deterministic.
    "schema_definition": 0.85,
    "config": 0.85,            # columns extracted from pipeline config files (e.g. Kipawa JSON)
    # Cross-reference structural tier — column inferred from a matching function
    # signature; stronger than a name heuristic because it follows data-flow.
    "xref_function": 0.80,
    # Pattern / heuristic tier — column inferred from naming conventions or widget
    # defaults; correct more often than not, but not deterministic.
    "naming_convention": 0.70,
    "widget_default": 0.70,
    # Cross-reference name tier — column inferred from a name match across nodes.
    "xref_output": 0.5, "xref_input": 0.5,
    # Uncertain — column appears in lineage but source attribution is ambiguous.
    "ambiguous_join": 0.3,
}


# ---------------------------------------------------------------------------
# Data container — pre-computed stats shared across sections
# ---------------------------------------------------------------------------

@dataclass
class ReportContext:
    """Pre-computed stats shared across report sections."""

    project_name: str
    asg: dict[str, Any]
    data_io: list[dict[str, Any]]
    entrypoints: list[dict[str, Any]]
    elapsed_secs: float | None

    # Source file breakdown
    source_files: list[dict] = field(default_factory=list)
    test_files: list[dict] = field(default_factory=list)
    notebooks: list[dict] = field(default_factory=list)
    scripts: list[dict] = field(default_factory=list)
    modules: list[dict] = field(default_factory=list)

    # I/O
    data_in: list[dict] = field(default_factory=list)
    data_out: list[dict] = field(default_factory=list)
    ep_count: int = 0

    # Schema stats
    real_total_cols: int = 0
    real_typed_cols: int = 0
    schema_coverage: float = 0.0
    by_evidence: int = 0
    by_pattern: int = 0
    by_xref: int = 0
    by_xref_output: int = 0
    by_xref_input: int = 0
    by_xref_function: int = 0
    weighted_typed_cols: float = 0.0
    by_placeholder: int = 0
    placeholder_inputs: int = 0
    synthetic_ready: int = 0
    inferred_ready: int = 0

    # Lineage connectivity stats
    total_inputs: int = 0
    connected_inputs: int = 0
    connectivity_score: float | None = None  # None = N/A (no inputs to measure)

    # Widgets
    widget_active: int = 0
    widget_unused: int = 0


def _build_context(
    asg: dict[str, Any],
    data_io: list[dict[str, Any]] | None,
    entrypoints: list[dict[str, Any]] | None,
    project_name: str,
    elapsed_secs: float | None,
) -> ReportContext:
    """Pre-compute all stats needed by report sections."""

    ctx = ReportContext(
        project_name=project_name,
        asg=asg,
        data_io=data_io or [],
        entrypoints=entrypoints or [],
        elapsed_secs=elapsed_secs,
    )

    # Source files
    ctx.source_files = asg.get("source_files", [])
    test_patterns = ["test_", "_test.py", "/tests/", "/test/", "unittest", "pytest"]
    ctx.test_files = [
        f for f in ctx.source_files
        if any(p in f.get("path", "").lower() for p in test_patterns)
    ]
    non_test = [f for f in ctx.source_files if f not in ctx.test_files]
    ctx.notebooks = [f for f in non_test if f.get("source_type") == "notebook"]
    ctx.scripts = [f for f in non_test if f.get("source_type") == "script"]
    ctx.modules = [f for f in non_test if f.get("source_type") not in ("notebook", "script")]

    # I/O from ASG
    ctx.data_in = asg.get("data_in", [])
    ctx.data_out = asg.get("data_out", [])
    ctx.ep_count = len(ctx.entrypoints)

    # Widgets
    for d_in in ctx.data_in:
        if d_in.get("type") != "config":
            continue
        for col in d_in.get("inferred_columns", []):
            src = col.get("source", "")
            if src == "widget_default":
                ctx.widget_active += 1
            elif src == "widget_unused":
                ctx.widget_unused += 1

    # Schema coverage — single source of truth: ctx.data_in (production, non-artifact).
    # Artifact nodes (config files, logs, debug outputs) are excluded from the
    # denominator so they don't drag down Typing with inevitable UNKNOWNs.
    # Maps InferenceSource values to report categories:
    #   evidence = explicit, filter_condition, join_key, group_by, aggregation,
    #              select, order_by, function_arg, catalog, udf_semantic, usage
    #   pattern  = naming_convention, schema_definition, widget_default
    #   xref_*   = xref_output, xref_input, xref_function
    EVIDENCE_SOURCES = {
        "explicit", "filter_condition", "join_key", "group_by",
        "aggregation", "select", "order_by", "function_arg",
        "catalog", "udf_semantic", "usage",
    }
    PATTERN_SOURCES = {"naming_convention", "schema_definition", "widget_default"}

    prod_inputs = [
        d for d in ctx.data_in
        if not d.get("is_test_file") and d.get("nature", "data") != "artifact"
    ]
    for d in prod_inputs:
        for col in d.get("inferred_columns", []):
            raw_src = col.get("source", "")
            src = raw_src.value if hasattr(raw_src, "value") else str(raw_src)
            if "." in src:
                src = src.rsplit(".", 1)[-1].lower()
            raw_type = col.get("inferred_type", "UNKNOWN")
            inf_type = raw_type.value if hasattr(raw_type, "value") else str(raw_type)

            ctx.real_total_cols += 1
            if inf_type != "UNKNOWN":
                ctx.real_typed_cols += 1
                ctx.weighted_typed_cols += CONFIDENCE_WEIGHTS.get(src, 0.5)

            if src in EVIDENCE_SOURCES:
                ctx.by_evidence += 1
            elif src in PATTERN_SOURCES:
                ctx.by_pattern += 1
            elif src == "xref_function":
                ctx.by_xref += 1
                ctx.by_xref_function += 1
            elif src == "xref_output":
                ctx.by_xref += 1
                ctx.by_xref_output += 1
            elif src == "xref_input":
                ctx.by_xref += 1
                ctx.by_xref_input += 1

    ctx.schema_coverage = (
        (ctx.real_typed_cols / ctx.real_total_cols * 100)
        if ctx.real_total_cols > 0 else 0.0
    )
    ctx.synthetic_ready = sum(
        1 for d in prod_inputs
        if d.get("inferred_columns") and d.get("nature", "data") == "data"
    )
    ctx.inferred_ready = ctx.synthetic_ready

    # Lineage connectivity: fraction of production *data* in_* nodes that appear
    # as an input to at least one transformation.
    # Nodes with nature="metadata" (orchestration queries like SELECT MAX) or
    # nature="fixture" (test mocks) are excluded from the denominator — they are
    # detected but not expected to have downstream business lineage.
    data_prod_inputs = [
        d for d in prod_inputs if d.get("nature", "data") == "data"
    ]
    prod_in_ids: set[str] = {d["id"] for d in data_prod_inputs if d.get("id")}
    if prod_in_ids:
        all_tx_inputs: set[str] = {
            inp
            for tx in asg.get("transformations", [])
            for inp in (tx.get("inputs") or [])
        }
        ctx.total_inputs = len(prod_in_ids)
        ctx.connected_inputs = len(prod_in_ids & all_tx_inputs)
        ctx.connectivity_score = ctx.connected_inputs / ctx.total_inputs * 100
    # else: connectivity_score stays None (N/A)

    return ctx


# ---------------------------------------------------------------------------
# Section renderers — each returns a list of markdown lines
# ---------------------------------------------------------------------------

def _render_header(ctx: ReportContext) -> list[str]:
    return [
        f"# {ctx.project_name} - Workload Analysis Report",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
    ]


def _render_executive_summary(ctx: ReportContext) -> list[str]:
    lines: list[str] = ["## Executive Summary", ""]

    total_files = len(ctx.source_files)
    lines.extend([
        f"This workload consists of **{total_files} Python files**:",
        f"- {len(ctx.scripts)} Python scripts (.py)",
        f"- {len(ctx.notebooks)} DBX notebooks",
        f"- {len(ctx.modules)} modules/libraries",
    ])
    if ctx.test_files:
        lines.append(f"- {len(ctx.test_files)} test files (excluded)")
    lines.append("")

    indirect_count = sum(1 for o in ctx.data_out if o.get("is_indirect", False))
    direct_count = len(ctx.data_out) - indirect_count

    io_desc = (
        f"**{len(ctx.data_in)} data sources** and write to **{len(ctx.data_out)} destinations** "
        f"({direct_count} direct + {indirect_count} via utility functions)"
        if indirect_count > 0
        else f"**{len(ctx.data_in)} data sources** and write to **{len(ctx.data_out)} destinations**"
    )
    lines.append(
        f"We detected **{ctx.ep_count} entry points** (workloads) that read from {io_desc}."
    )
    lines.append("")

    synth_detail = (
        f" ({ctx.inferred_ready} with inferred schema, {ctx.placeholder_inputs} with placeholder schema)."
        if ctx.placeholder_inputs else "."
    )
    lines.append(
        f"Schema coverage is **{ctx.schema_coverage:.1f}%** "
        f"({ctx.real_typed_cols}/{ctx.real_total_cols} columns typed)."
        f" We can generate synthetic data for **{ctx.synthetic_ready}** inputs{synth_detail}"
    )
    lines.extend(["", "---", ""])
    return lines


def _render_workload_overview(ctx: ReportContext) -> list[str]:
    lines: list[str] = ["## 1. Workload Overview", "", "### 1.1 Source Files", ""]

    total_files = len(ctx.source_files)
    lines.extend([
        "| Category | Count |",
        "|----------|-------|",
        f"| Python Scripts (.py) | {len(ctx.scripts)} |",
        f"| DBX Notebooks | {len(ctx.notebooks)} |",
        f"| Modules/Libraries | {len(ctx.modules)} |",
    ])
    if ctx.test_files:
        lines.append(f"| Test Files (excluded) | {len(ctx.test_files)} |")
    lines.extend([f"| **Total** | **{total_files}** |", ""])

    # Parsing status
    parsing = ctx.asg.get("parsing_report", {})
    syntax = parsing.get("syntax", {})
    understanding = parsing.get("understanding", {})

    if syntax or understanding:
        lines.extend([
            "### 1.2 Parsing Status", "",
            "| Phase | OK | Corrected/Skipped | Errors |",
            "|-------|-----|-------------------|--------|",
            f"| Syntax | {syntax.get('ok', 0)} | {syntax.get('corrected', 0)} | {syntax.get('errors', 0)} |",
            f"| Semantic (Understanding) | {understanding.get('ok', 0)} | {understanding.get('skipped', 0)} | {understanding.get('errors', 0)} |",
            "",
        ])
        if parsing.get("errors"):
            lines.extend(["#### Parsing Errors", ""])
            for err in parsing["errors"][:5]:
                lines.append(f"- `{err.get('file', 'unknown')}`: {err.get('message', '')}")
            if len(parsing.get("errors", [])) > 5:
                lines.append(f"- ... and {len(parsing['errors']) - 5} more")
            lines.append("")

    parsed_files = parsing.get("files", [])
    corrected = [f for f in parsed_files if f.get("syntax_status") == "corrected"]
    if corrected:
        lines.append("> **Auto-corrected files:**")
        for cf in corrected:
            lines.append(f"> - `{cf.get('path', 'unknown')}`: {cf.get('syntax_correction', 'unknown correction')}")
        lines.append("")

    # Data flow
    txs = ctx.asg.get("transformations", [])
    if txs:
        ops = Counter(t.get("operation", "unknown") for t in txs)
        joins = ops.get("join", 0) + ops.get("join_custom", 0)
        filters = ops.get("filter", 0)
        selects = ops.get("select", 0)
        aggs = ops.get("groupBy_agg", 0) + ops.get("agg", 0)
        with_cols = ops.get("withColumn", 0)
        unions = ops.get("union", 0) + ops.get("union_custom", 0)

        din_count = len(ctx.asg.get("data_in", []))
        dout_count = len(ctx.asg.get("data_out", []))
        lines.extend([
            "### 1.3 Data Flow", "",
            "```",
            f"  {din_count} data sources  ──▶  {len(txs)} transformations  ──▶  {dout_count} outputs",
            "```", "",
            "| Operation | Count |",
            "|-----------|------:|",
        ])
        op_rows = [
            ("Columns added/modified (`withColumn`)", with_cols),
            ("Selections (`select`)", selects),
            ("Filters (`filter`/`where`)", filters),
            ("Joins", joins),
            ("Aggregations (`groupBy`/`agg`)", aggs),
            ("Unions", unions),
            ("Other", len(txs) - with_cols - selects - filters - joins - aggs - unions),
        ]
        for label, count in op_rows:
            if count > 0:
                lines.append(f"| {label} | {count} |")
        lines.append("")

    return lines


def _render_entrypoints(ctx: ReportContext) -> list[str]:
    lines: list[str] = ["## 2. Entry Points (Workloads)", ""]

    if ctx.entrypoints:
        ep_notebooks = [e for e in ctx.entrypoints if e.get("type") == "databricks_notebook"]
        ep_scripts = [e for e in ctx.entrypoints if e.get("type") == "script"]
        lines.extend([
            "| Type | Count |",
            "|------|-------|",
            f"| Python Scripts | {len(ep_scripts)} |",
            f"| DBX Notebooks | {len(ep_notebooks)} |",
            f"| **Total** | **{len(ctx.entrypoints)}** |", "",
            "### Entry Point Details", "",
            "| Name | Type | Inputs | Outputs | |",
            "|------|------|--------|---------|--------|",
        ])
        for ep in ctx.entrypoints[:25]:
            name = ep.get("name", "?")
            ep_type = ep.get("type", "?").replace("databricks_notebook", "dbx_notebook")
            inputs_obj = ep.get("inputs", {})
            outputs_obj = ep.get("outputs", {})
            total_inputs = inputs_obj.get("total", 0) if isinstance(inputs_obj, dict) else getattr(inputs_obj, "total", 0)
            outputs = outputs_obj.get("total", 0) if isinstance(outputs_obj, dict) else getattr(outputs_obj, "total", 0)
            lines.append(f"| {name} | {ep_type} | {total_inputs} | {outputs} | ✅ |")
        if len(ctx.entrypoints) > 25:
            lines.append(f"| ... | ... | ... | ... | ({len(ctx.entrypoints) - 25} more) |")
        lines.append("")
    else:
        lines.extend(["No entry points detected.", ""])

    # Notebook dependencies
    dep_targets: Counter[str] = Counter()
    nb_with_deps = 0
    nb_deps_total = 0
    for sf in ctx.asg.get("source_files", []):
        deps = sf.get("notebook_dependencies", [])
        if deps:
            nb_with_deps += 1
            nb_deps_total += len(deps)
            for d in deps:
                target = d.get("resolved_path") or d.get("target", "?")
                dep_targets[target.rsplit("/", 1)[-1].replace(".py", "")] += 1

    if nb_deps_total > 0:
        lines.extend([
            "### Notebook Dependencies (`%run`)", "",
            f"**{nb_with_deps} notebooks** depend on **{len(dep_targets)} shared notebook(s)** "
            f"via `%run` ({nb_deps_total} total references).", "",
            "| Shared Notebook | Used By |",
            "|-----------------|--------:|",
        ])
        for target, count in dep_targets.most_common():
            lines.append(f"| `{target}` | {count} |")
        lines.append("")
    elif sum(1 for sf in ctx.asg.get("source_files", []) if sf.get("source_type") == "notebook") > 1:
        lines.extend(["> **Note:** Multiple DBX notebooks detected but no `%run` dependencies found.", ""])

    # UDFs
    udf_funcs = [f for f in ctx.asg.get("functions", []) if f.get("is_udf")]
    if udf_funcs:
        lines.extend([
            "### User-Defined Functions (UDFs)", "",
            "| Function | Class | Return Schema |",
            "|----------|-------|---------------|",
        ])
        for f in udf_funcs:
            cls = f.get("containing_class") or "-"
            schema = f.get("udf_return_schema") or "unknown"
            lines.append(f"| `{f.get('name', '?')}` | {cls} | `{schema}` |")
        lines.append("")

    return lines


def _render_data_io(ctx: ReportContext) -> list[str]:
    lines: list[str] = ["## 3. Data Inputs/Outputs", ""]

    in_by_type = dict(Counter(i.get("type", "unknown") for i in ctx.data_in))
    out_by_type = dict(Counter(i.get("type", "unknown") for i in ctx.data_out))

    all_types: dict[str, int] = {}
    for t, c in {**in_by_type, **out_by_type}.items():
        all_types[t] = in_by_type.get(t, 0) + out_by_type.get(t, 0)
    top_types = [t for t, _ in sorted(all_types.items(), key=lambda x: -x[1])[:3]]

    fmt_cols = " | ".join(top_types)
    fmt_sep = " | ".join("---:" for _ in top_types)

    def _vals(by_type: dict[str, int]) -> str:
        return " | ".join(str(by_type.get(h, 0)) for h in top_types)

    detail_in = ", ".join(f"{t}({c})" for t, c in sorted(in_by_type.items()) if t not in top_types)
    detail_out = ", ".join(f"{t}({c})" for t, c in sorted(out_by_type.items()) if t not in top_types)

    lines.extend([
        "### 3.1 Summary", "",
        f"| Direction | Total | {fmt_cols} | Other |",
        f"|-----------|------:| {fmt_sep} |-------|",
        f"| Inputs | {len(ctx.data_in)} | {_vals(in_by_type)} | {detail_in or '-'} |",
        f"| Outputs | {len(ctx.data_out)} | {_vals(out_by_type)} | {detail_out or '-'} |",
        "",
    ])

    # Output coverage note
    if ctx.data_io and ctx.entrypoints:
        asg_data_out = ctx.asg.get("data_out", [])
        asg_indirect = sum(1 for o in asg_data_out if o.get("is_indirect", False))
        asg_direct = len(asg_data_out) - asg_indirect
        if asg_indirect > 0:
            lines.extend([
                f"> Of **{len(asg_data_out)} total outputs**, "
                f"**{asg_direct}** are direct `df.write` calls and "
                f"**{asg_indirect}** are written via utility functions "
                f"(automatically resolved from execution calls).", "",
            ])

    # Schema coverage
    evidence_pct = (ctx.by_evidence / ctx.real_total_cols * 100) if ctx.real_total_cols else 0
    pattern_pct = (ctx.by_pattern / ctx.real_total_cols * 100) if ctx.real_total_cols else 0

    lines.extend([
        "### 3.2 Schema Coverage", "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total columns detected | {ctx.real_total_cols} |",
        f"| Columns with inferred types | {ctx.real_typed_cols} |",
        f"| **Coverage** | **{ctx.schema_coverage:.1f}%** |", "",
        "**Inference Breakdown:**", "",
        "| Method | Columns | % of Total | Confidence |",
        "|--------|---------|------------|------------|",
        f"| Code Analysis (casts, filters, functions) | {ctx.by_evidence} | {evidence_pct:.1f}% | High |",
        f"| Pattern Matching (column naming) | {ctx.by_pattern} | {pattern_pct:.1f}% | Medium |",
    ])
    if ctx.by_xref_function > 0:
        xf_pct = (ctx.by_xref_function / ctx.real_total_cols * 100) if ctx.real_total_cols else 0
        lines.append(f"| Logic-based (shared functions) | {ctx.by_xref_function} | {xf_pct:.1f}% | 70% confidence |")
    xref_name_total = ctx.by_xref_output + ctx.by_xref_input
    if xref_name_total > 0:
        xn_pct = (xref_name_total / ctx.real_total_cols * 100) if ctx.real_total_cols else 0
        xref_detail = (
            f"{ctx.by_xref_output} from outputs, {ctx.by_xref_input} from inputs"
            if ctx.by_xref_output and ctx.by_xref_input
            else (f"{ctx.by_xref_output} from outputs" if ctx.by_xref_output else f"{ctx.by_xref_input} from inputs")
        )
        lines.append(f"| Name-based ({xref_detail}) | {xref_name_total} | {xn_pct:.1f}% | 50% confidence |")
    if ctx.by_placeholder > 0:
        lines.append(f"| Placeholder (injected for testability) | {ctx.by_placeholder} | — | Placeholder |")
    lines.extend([
        "",
        "> **Note:** 'High confidence' types are derived from explicit code evidence (e.g., `.cast('int')`, ",
        "> `col > 5`). 'Medium confidence' types are inferred from column naming conventions (e.g., `_id` → INT).",
    ])
    if ctx.by_xref_function > 0:
        lines.append("> 'Logic-based' columns are propagated from test fixtures that feed the same function as production code (70% confidence).")
    if ctx.by_xref_output + ctx.by_xref_input > 0:
        lines.append("> 'Name-based' columns are propagated from a data source/sink with the same name — higher collision risk in global-scope scripts (50% confidence).")
    if ctx.by_placeholder > 0:
        lines.append(f"> 'Placeholder' columns ({ctx.placeholder_inputs} inputs) have injected minimal schemas for end-to-end testability.")
    lines.append("")

    # Lineage confidence table: show inputs that have ambiguous columns
    amb_inputs = []
    for d in ctx.data_io:
        if d.get("role") != "input":
            continue
        amb_cols = [c for c in d.get("columns", []) if c.get("confidence") == "ambiguous"]
        if amb_cols:
            amb_inputs.append((d.get("name", "?"), d.get("columns", []), amb_cols))

    if amb_inputs:
        lines.extend(["### 3.3 Lineage Confidence", ""])
        for src_name, all_cols, amb_cols in amb_inputs:
            lines.extend([
                f"**{src_name}**", "",
                "| Column | Type | Lineage | Source |",
                "|--------|------|---------|--------|",
            ])
            for col in all_cols:
                cname = col.get("name", "?")
                ctype = col.get("type", "UNKNOWN")
                conf = col.get("confidence", "unknown")
                if conf == "ambiguous":
                    icon = "\u26a0\ufe0f Ambiguous"
                    src_detail = "Join inference"
                elif conf == "evidence":
                    icon = "\u2705 High"
                    src_detail = "Code analysis"
                elif conf == "pattern":
                    icon = "\u2705 High"
                    src_detail = "Pattern match"
                else:
                    icon = "\u2705 High"
                    src_detail = "Inferred"
                lines.append(f"| {cname} | {ctype} | {icon} | {src_detail} |")
            lines.append("")

        lines.extend([
            "> **Note:** Columns with \u26a0\ufe0f Ambiguous confidence were detected in multiple sources ",
            "> without a clear owner in the code. While Warp-Suite made a probabilistic attribution, ",
            "> explicit `.select()` before joins is recommended for production-grade lineage.", "",
        ])

    return lines


def _render_prepare_testing(ctx: ReportContext) -> list[str]:
    lines: list[str] = ["## 4. Prepare Testing", ""]

    if ctx.elapsed_secs is not None:
        mins = int(ctx.elapsed_secs // 60)
        secs = ctx.elapsed_secs % 60
        elapsed_str = f"{mins}m {secs:.1f}s" if mins > 0 else f"{secs:.1f}s"
        lines.extend([f"Analysis completed in **{elapsed_str}**.", ""])

    if not ctx.data_io:
        lines.extend(["No data I/O information available.", ""])
        return lines

    inputs_with_cols = [d for d in ctx.data_io if d.get("role") == "input" and d.get("columns")]
    inputs_no_cols = [d for d in ctx.data_io if d.get("role") == "input" and not d.get("columns")]

    files = [d for d in inputs_with_cols if d.get("type") == "file"]
    tables = [d for d in inputs_with_cols if d.get("type") in ("table", "database")]
    configs = [d for d in inputs_with_cols if d.get("type") == "config"]
    other = [d for d in inputs_with_cols if d.get("type") not in ("file", "table", "database", "config")]

    total_inputs = len(inputs_with_cols) + len(inputs_no_cols)
    all_data_in = ctx.asg.get("data_in", [])
    memory_sources = sum(1 for d in all_data_in if d.get("type") == "memory")

    lines.extend([
        "| Metric | Count |",
        "|--------|-------|",
        f"| Inputs with schema (✅ ready) | {len(inputs_with_cols)} |",
        f"| Inputs without schema (no columns detected) | {len(inputs_no_cols)} |",
        f"| **Total (external sources)** | **{total_inputs}** |", "",
        "| Source Type | Count |",
        "|-------------|-------|",
        f"| Files (CSV, Parquet, JSON) | {len(files)} |",
        f"| Tables/Databases | {len(tables)} |",
    ])
    if configs:
        lines.append(f"| Widget Parameters | {len(configs)} |")
    if other:
        lines.append(f"| Other | {len(other)} |")
    lines.append("")

    total_sources = len(all_data_in)
    if total_sources != total_inputs:
        lines.extend([
            f"> **Note:** The ASG contains {total_sources} total data sources. "
            f"Of these, {memory_sources} are in-memory (`createDataFrame` in tests/utilities) "
            f"and are excluded from synthetic data generation. "
            f"{total_inputs} external sources remain.", "",
        ])

    if inputs_no_cols:
        lines.extend(["#### Inputs Without Schema (cannot generate synthetic data)", ""])
        for inp in inputs_no_cols[:10]:
            lines.append(f"- `{inp.get('name', 'unknown')}` ({inp.get('type', '?')})")
        if len(inputs_no_cols) > 10:
            lines.append(f"- ... and {len(inputs_no_cols) - 10} more")
        lines.append("")

    return lines


def compute_score(ctx: ReportContext) -> tuple[float, list[tuple[str, float, int, str]]]:
    """Compute the ASG Structural Integrity Score (B+ model).

    Adopted 2026-03-14. Replaces the previous Synthetic-Data-centric model.
    Measures fidelity of the Abstract Semantic Graph across four orthogonal
    levels of understanding, regardless of downstream use (testing, DBT,
    migration, documentation).

    Returns (score, parts) where parts is a list of
    (label, ratio, weight, detail) tuples.

    Dimensions
    ----------
    Discovery    (20%) – Entrypoints detected vs declared.  The "map" of the
                         pipeline: how many entry gates did we find?
    Identity     (25%) – I/O nodes with resolved real names vs total nodes.
                         Without identity, there is no traceability.
    Structure    (25%) – I/O nodes with ≥1 known column name vs total, with
                         data_in weighted ×1.0 and data_out weighted ×0.5
                         (outputs are observational contracts, not preparation
                         requirements; their schema gap is important but not
                         as blocking as an input gap).
    Typing       (15%) – Confidence-weighted typed columns / total input
                         columns (data_in only).  Output column types are
                         almost always inherited from the internal data flow;
                         penalising their absence is statistical noise.
    Connectivity (15%) – Fraction of input nodes traceable to ≥1
                         transformation.  The "glue" of the graph; a
                         disconnected graph (0%) receives no credit even if
                         schema coverage is perfect.
    """
    WEIGHT_DISCOVERY    = 20
    WEIGHT_IDENTITY     = 25
    WEIGHT_STRUCTURE    = 25
    WEIGHT_TYPING       = 15
    WEIGHT_CONNECTIVITY = 15

    parts: list[tuple[str, float, int, str]] = []

    _non_artifact = {"data", "metadata", "fixture"}
    prod_in = [
        d for d in ctx.data_in
        if not d.get("is_test_file") and d.get("nature", "data") in _non_artifact
    ]
    prod_out = [
        d for d in ctx.data_out
        if not d.get("is_test_file") and d.get("nature", "data") in _non_artifact
    ]

    # ── Discovery (20%): entrypoints detected / declared ────────────────────
    if ctx.entrypoints and ctx.ep_count > 0:
        ep_detected = sum(1 for e in ctx.entrypoints if e.get("status") == "detected")
        ep_ratio = ep_detected / ctx.ep_count
        ep_detail = f"{ep_detected}/{ctx.ep_count} detected"
    else:
        ep_ratio, ep_detail = 1.0, "N/A"
    parts.append(("Discovery", ep_ratio, WEIGHT_DISCOVERY, ep_detail))

    # ── Identity (25%): real-named I/O nodes / total ────────────────────────
    # "Real name" excludes WARP-generated placeholders (PTH_/SNK_/SQL_/NOD_/VAR_)
    # and unresolved runtime expressions.  Nodes with these names still count in
    # the denominator so that the .replace()-extractor fix genuinely raises the score.
    _PLACEHOLDER_RE = re.compile(r"^(PTH|SNK|SQL|NOD|VAR)_\d+$")

    def _is_real_name(node: dict) -> bool:
        name = (node.get("name") or "").strip()
        if not name:
            return False
        if _PLACEHOLDER_RE.match(name):
            return False
        if name.startswith("runtime:"):
            return False
        return True

    io_total = len(prod_in) + len(prod_out)
    io_named = (
        sum(1 for d in prod_in  if _is_real_name(d))
        + sum(1 for d in prod_out if _is_real_name(d))
    )
    io_ratio = (io_named / io_total) if io_total > 0 else 1.0
    parts.append(("Identity", io_ratio, WEIGHT_IDENTITY,
                  f"{io_named}/{io_total} named nodes"))

    # ── Structure (25%): nodes with ≥1 column (B+: data_out × 0.5) ─────────
    def _has_cols(node: dict) -> bool:
        return bool(node.get("inferred_columns") or node.get("schema"))

    in_with  = sum(1 for n in prod_in  if _has_cols(n))
    out_with = sum(1 for n in prod_out if _has_cols(n))
    struct_num = float(in_with) + out_with * 0.5
    struct_den = float(len(prod_in)) + len(prod_out) * 0.5
    struct_ratio = (struct_num / struct_den) if struct_den > 0 else 1.0
    parts.append(("Structure", struct_ratio, WEIGHT_STRUCTURE,
                  f"{in_with}/{len(prod_in)} inputs + {out_with}/{len(prod_out)} outputs×½"))

    # ── Typing (15%): confidence-weighted typed cols, data_in only ───────────
    # ctx.weighted_typed_cols / ctx.real_total_cols are already computed from
    # data_in exclusively (see build_context → prod_inputs loop).
    typing_ratio = (
        ctx.weighted_typed_cols / ctx.real_total_cols
    ) if ctx.real_total_cols > 0 else 0.0
    parts.append(("Typing", typing_ratio, WEIGHT_TYPING,
                  f"{ctx.weighted_typed_cols:.0f}/{ctx.real_total_cols} input cols weighted"))

    # ── Connectivity (15%): connected inputs / total inputs ──────────────────
    # N/A (no inputs present) → full credit so inputless workloads are not
    # penalised.  0/N inputs traced → 0 pts (no maquillaje).
    if ctx.connectivity_score is not None:
        conn_ratio = ctx.connectivity_score / 100.0
        conn_detail = f"{ctx.connected_inputs}/{ctx.total_inputs} inputs traced"
    else:
        conn_ratio, conn_detail = 1.0, "N/A"
    parts.append(("Connectivity", conn_ratio, WEIGHT_CONNECTIVITY, conn_detail))

    score = max(0.0, min(100.0, sum(r * w for _, r, w, _ in parts)))
    return score, parts


def _render_score(ctx: ReportContext) -> list[str]:
    lines: list[str] = ["## 5. Overall Score", ""]
    score, parts = compute_score(ctx)

    if score >= 90:
        badge, comment = "🟢 Excellent", "Excellent understanding - ready for comprehensive test generation."
    elif score >= 75:
        badge, comment = "🟡 Good", "Good understanding - most workloads can be tested effectively."
    elif score >= 50:
        badge, comment = "🟠 Fair", "Fair understanding - some workloads may need manual review."
    else:
        badge, comment = "🔴 Needs Work", "Limited understanding - manual analysis recommended."

    lines.extend([
        "```",
        "╔══════════════════════════════════════════╗",
        f"║         WORKLOAD SCORE: {score:5.1f}/100          ║",
        f"║              {badge:^20}         ║",
        "╚══════════════════════════════════════════╝",
        "```", "",
        f"_{comment}_", "",
        "### Score Breakdown", "",
        "| Dimension | Weight | Measured | Score |",
        "|-----------|-------:|----------|------:|",
    ])
    for label, ratio, weight, detail in parts:
        if label == "Confidence Penalty":
            lines.append(f"| ⚠️ {label} | — | {detail} | {ratio:.1f} |")
            continue
        contribution = round(ratio * weight, 1)
        pct = f"{ratio * 100:.1f}%"
        icon = "✅" if ratio >= 0.95 else ("⚠️" if ratio >= 0.75 else "❌")
        lines.append(f"| {icon} {label} | {weight} | {detail} ({pct}) | {contribution} |")
    lines.extend([f"| **Total** | **100** | | **{score:.1f}** |", ""])

    # Connectivity note: surface the orphan-input situation when relevant
    if ctx.connectivity_score is not None and ctx.connectivity_score < 100.0:
        orphan_count = ctx.total_inputs - ctx.connected_inputs
        if orphan_count > 0:
            lines.extend([
                "### Lineage Connectivity Note", "",
                f"> **{orphan_count} of {ctx.total_inputs} inputs** could not be traced to any "
                f"transformation (orphan inputs). This is common in codebases that use dynamic "
                f"SQL, runtime-resolved table names, or deeply nested wrapper functions, where "
                f"static analysis cannot follow the data flow across function boundaries.",
                "> Orphan inputs are still registered in Data I/O and contribute to schema "
                "coverage — they are not hidden.",
                "",
            ])

    lines.extend([
        "### About the Score", "",
        "> The **Workload Score** (0-100) measures the **ASG Structural Integrity** — how",
        "> completely WARP understands the pipeline's structure, independent of downstream",
        "> use (testing, DBT migration, lineage documentation, stored procedures, etc.).", ">",
        "> - **90+**: Excellent — deep structural understanding; ready for any downstream use",
        "> - **75-89**: Good — solid understanding with minor gaps",
        "> - **50-74**: Fair — partial understanding; some manual review recommended",
        "> - **<50**: Limited — significant structural gaps; manual analysis required", ">",
        "> Dimensions: **Discovery** (20 pts) · **Identity** (25 pts) · **Structure** (25 pts)",
        "> · **Typing** (15 pts) · **Connectivity** (15 pts)",
        ">",
        "> _B+ model (adopted 2026-03-14): Structure weights data_out nodes at 0.5×; "
        "Typing measures data_in columns only. Replaces Synthetic-Data-centric model._",
        "",
    ])
    return lines


def _render_gap_summary(gap_report: Any | None) -> list[str]:
    """Render a human-readable summary of the gap analysis."""
    if gap_report is None:
        return []

    lines: list[str] = ["## 6. Gap Analysis", ""]

    s = gap_report.summary
    active_cats = [c for c, n in s.by_category.items() if n > 0]
    lines.extend([
        f"**{s.total} gaps** identified across {len(active_cats)} categories "
        f"(estimated score impact: **{s.total_score_impact:+.1f}** points).",
        "",
        "| Severity | Count |",
        "|----------|------:|",
    ])
    sev_icons = {
        "blocker": "\u26d4",
        "critical": "\U0001f534",
        "high": "\U0001f7e0",
        "medium": "\U0001f7e1",
        "low": "\u2139\ufe0f",
    }
    for sev in ("blocker", "critical", "high", "medium", "low"):
        cnt = s.by_severity.get(sev, 0)
        if cnt > 0:
            icon = sev_icons.get(sev, "")
            lines.append(f"| {icon} {sev.capitalize()} | {cnt} |")
    lines.append("")

    lines.extend([
        "| Category | Count |",
        "|----------|------:|",
    ])
    for cat in ("parsing", "structural", "naming", "inference", "confidence"):
        cnt = s.by_category.get(cat, 0)
        if cnt > 0:
            lines.append(f"| {cat.capitalize()} | {cnt} |")
    lines.append("")

    top_gaps = gap_report.sorted_gaps()[:10]
    if top_gaps:
        lines.extend([
            "### Top Gaps (highest impact)", "",
            "| Severity | Subject | Detail | Impact |",
            "|----------|---------|--------|-------:|",
        ])
        for g in top_gaps:
            detail_short = g.detail[:60] + "..." if len(g.detail) > 60 else g.detail
            lines.append(
                f"| {g.severity.value} | `{g.subject}` | {detail_short} | {g.score_impact:+.2f} |"
            )
        lines.append("")

    lines.extend([
        "> Full machine-readable gap report available in `gaps.json`.",
        "> Use this file as input for AI-assisted remediation agents.",
        "",
    ])

    return lines


def _render_config_files(asg: dict[str, Any]) -> list[str]:
    """Render a section for companion config files (config-driven pipelines)."""
    config_files: list[dict] = asg.get("config_files", [])
    if not config_files:
        return []

    # Separate high-value from general
    high_value = [cf for cf in config_files if cf["classification"] != "general_config"]
    general    = [cf for cf in config_files if cf["classification"] == "general_config"]

    lines: list[str] = [
        "## 7. Companion Configuration Files",
        "",
        (
            f"> **{len(config_files)} configuration file(s)** were found in this project "
            "but are not currently analysed by WARP. They may contain data source paths, "
            "schemas, or pipeline wiring that could improve analysis quality if parsed."
        ),
        "",
    ]

    # Detect config-driven architecture pattern
    pipeline_configs = [cf for cf in config_files if cf["classification"] == "pipeline_config"]
    if pipeline_configs:
        lines.extend([
            "> ⚠️ **Config-driven architecture detected.** This project loads its pipeline "
            "structure (readers, writers, transformers) from external JSON/YAML files at runtime. "
            "Static analysis of the Python/Scala code alone cannot resolve data source paths or "
            "schemas — they live in the config files listed below. "
            "See `docs/33_CONFIG_DRIVEN_PIPELINES.md` for the roadmap to support this pattern.",
            "",
        ])

    if high_value:
        _CLASS_LABELS = {
            "pipeline_config":   "Pipeline config",
            "schema_definition": "Schema definition",
            "env_config":        "Environment / connections",
            "databricks_job":    "Databricks Job",
            "column_types":      "Column types (WARP)",
        }
        lines.extend([
            "### High-value files",
            "",
            "| File | Type | Signals | Potential value |",
            "|------|------|---------|-----------------|",
        ])
        for cf in high_value:
            label   = _CLASS_LABELS.get(cf["classification"], cf["classification"])
            signals = ", ".join(f"`{s}`" for s in cf["signals"][:4])
            value   = cf["potential_value"][:90] + "…" if len(cf["potential_value"]) > 90 else cf["potential_value"]
            lines.append(f"| `{cf['path']}` | {label} | {signals} | {value} |")
        lines.append("")

    if general:
        lines.extend([
            f"<details><summary>{len(general)} general config file(s) (no pipeline patterns detected)</summary>",
            "",
            "| File | Format | Size |",
            "|------|--------|-----:|",
        ])
        for cf in general:
            lines.append(f"| `{cf['path']}` | {cf['format']} | {cf['size_bytes']:,} B |")
        lines.extend(["", "</details>", ""])

    lines.extend(["---", ""])
    return lines


def _render_footer() -> list[str]:
    return ["---", "", "*Report generated by WARP Analysis Suite*", ""]


# ---------------------------------------------------------------------------
# Public API — assembles all sections
# ---------------------------------------------------------------------------

def generate_unified_report(
    asg: dict[str, Any],
    anomalies: dict[str, Any] | list | None = None,
    data_io: list[dict[str, Any]] | None = None,
    entrypoints: list[dict[str, Any]] | None = None,
    diagnostic: dict[str, Any] | None = None,
    issues: list[DiagnosticReport] | None = None,
    project_name: str = "Workload",
    elapsed_secs: float | None = None,
    gap_report: Any | None = None,
) -> str:
    """Generate a comprehensive unified report as markdown."""

    ctx = _build_context(asg, data_io, entrypoints, project_name, elapsed_secs)

    sections = [
        _render_header(ctx),
        _render_executive_summary(ctx),
        _render_workload_overview(ctx),
        _render_entrypoints(ctx),
        _render_data_io(ctx),
        _render_prepare_testing(ctx),
        _render_score(ctx),
        _render_gap_summary(gap_report),
        _render_config_files(asg),
        _render_footer(),
    ]

    lines: list[str] = []
    for section in sections:
        lines.extend(section)

    return "\n".join(lines)
