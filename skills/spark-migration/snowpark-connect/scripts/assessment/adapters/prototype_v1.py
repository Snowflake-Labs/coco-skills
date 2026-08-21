"""HTML adapter for the v1 migration-readiness report template.

Renders the redesigned sidebar layout (Inter font, Snowflake branding sidebar,
card borders, Chart.js visualizations, migration checklist, etc.).

The template is ``templates/prototype_v1.html.j2``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from assess_ir import Assessment  # noqa: E402
from partition_by import build_partition_table_data  # noqa: E402

# Data-shaping + deterministic-fallback helpers. v1 is the sole adapter, so
# these live here; the palette and label helpers further below have v1's own
# (newer) color/wording story.


def _color_badge(color: str) -> str:
    return {
        "green": "badge-green",
        "blue": "badge-blue",
        "yellow": "badge-yellow",
        "orange": "badge-orange",
        "red": "badge-red",
    }.get(color, "badge-green")


def _effort_label(effort: str) -> str:
    """Map a Severity-shaped effort bucket to the prototype's label set."""
    return {"High": "Major", "Medium": "Medium", "Low": "Minor"}.get(effort, effort)


def _plural(count: int, word: str) -> str:
    """Return the pluralized word only: 'file' or 'files'. No number included."""
    return f"{word}{'s' if count != 1 else ''}"


def _n(count: int, word: str) -> str:
    """Return '1 file' or '2 files' — number plus pluralized word, no (s) parenthetical."""
    return f"{count} {_plural(count, word)}"


def _default_exec_summary(workload: dict) -> str:
    """Deterministic, side-effect-free fallback when no executive summary was authored."""
    loc = workload.get("lines_of_code", 0)
    files = workload.get("files_scanned") or workload.get("code_file_count") or 0
    lang = workload.get("primary_language", "Python")
    return (
        f"This is a <strong>{lang}</strong>-based workload comprising "
        f"{loc:,} lines of code across {files} files."
    )


def _fallback_executive_summary(ctx: dict) -> str:
    """Deterministic executive summary: scale, archetype, migration path, readiness, top risks."""
    workload = ctx.get("workload") or {}
    lang = workload.get("primary_language", "Python")
    loc = workload.get("lines_of_code", 0)
    total_files = workload.get("files_scanned") or workload.get("code_file_count") or 0

    # ── Workload archetype ───────────────────────────────────────────────────
    wc = ctx.get("workload_classification") or {}
    wc_label = wc.get("classification", "")
    ingestion_level = wc.get("ingestion_level", "").lower()
    compute_level = wc.get("compute_level", "").lower()

    # ── Project type / conversion tier ──────────────────────────────────────
    pt = ctx.get("project_type") or {}
    pt_label = pt.get("label", "")

    # ── Code churn / readiness ───────────────────────────────────────────────
    cc = ctx.get("code_churn") or {}
    files_ready = cc.get("files_ready", 0)
    files_light = cc.get("files_light_refactor", 0)
    files_active = cc.get("files_active_refactor", 0)

    # ── High-impact complex patterns ─────────────────────────────────────────
    patterns = ctx.get("complex_patterns") or []
    high_patterns = [p.get("pattern", "") for p in patterns if p.get("impact") == "High"]

    # ── Ingestion / compute descriptors ──────────────────────────────────────
    _io_desc = {"high": "high external I/O", "medium": "moderate external I/O",
                "low": "low external I/O"}
    _compute_desc = {"high": "high compute intensity", "medium": "moderate compute intensity",
                     "low": "low compute intensity"}
    io_phrase = _io_desc.get(ingestion_level, "")
    compute_phrase = _compute_desc.get(compute_level, "")

    # ── Assemble sentences ────────────────────────────────────────────────────
    sentences: list[str] = []

    # Sentence 1: scale + classification + I/O characterization
    s1 = f"This {total_files}-file {lang} workload ({loc:,} LOC)"
    if wc_label:
        s1 += f" is classified as a <strong>{wc_label}</strong>"
        if io_phrase and compute_phrase:
            s1 += f" characterized by {io_phrase} and {compute_phrase}"
        elif io_phrase or compute_phrase:
            s1 += f" characterized by {io_phrase or compute_phrase}"
    s1 += "."
    sentences.append(s1)

    # Sentence 2: migration path / project tier
    if pt_label:
        sentences.append(f"Overall migration effort is under the {pt_label} path.")

    # Sentence 3: file readiness breakdown + risk tail
    if files_ready or files_light or files_active:
        parts = []
        if files_ready:
            parts.append(f"{files_ready} {'file is' if files_ready == 1 else 'files are'} fully migration-ready")
        if files_light:
            parts.append(f"{files_light} require{'s' if files_light == 1 else ''} light refactoring")
        if files_active:
            parts.append(f"{files_active} need{'s' if files_active == 1 else ''} active refactoring")

        if len(parts) == 1:
            readiness_prose = parts[0]
        elif len(parts) == 2:
            readiness_prose = parts[0] + " and " + parts[1]
        else:
            readiness_prose = ", ".join(parts[:-1]) + ", and " + parts[-1]

        s3 = f"Of the codebase, {readiness_prose}"
        if high_patterns:
            verb = "warrants" if len(high_patterns) == 1 else "warrant"
            qualifier = "a single high-impact pattern" if len(high_patterns) == 1 else f"{len(high_patterns)} high-impact patterns"
            names = ", ".join(high_patterns[:3]) + ("…" if len(high_patterns) > 3 else "")
            s3 += f"—primarily driven by {qualifier} ({names}) that {verb} immediate priority."
        else:
            s3 += "."
        sentences.append(s3)
    elif high_patterns:
        names = ", ".join(high_patterns[:3]) + ("…" if len(high_patterns) > 3 else "")
        if len(high_patterns) == 1:
            sentences.append(f"A single high-impact pattern ({names}) warrants immediate priority.")
        else:
            sentences.append(f"These {len(high_patterns)} high-impact patterns ({names}) warrant immediate priority.")

    return " ".join(sentences)


def _fallback_complex_patterns(ctx: dict) -> str:
    """Deterministic explanation of the Complex Patterns impact mix."""
    patterns = ctx.get("complex_patterns") or []
    if not patterns:
        return (
            "No complex Spark patterns were detected, so no special-attention "
            "refactors are expected from this signal alone."
        )
    buckets = {"High": [], "Medium": [], "Low": []}
    for row in patterns:
        buckets.get(row.get("impact"), buckets["Low"]).append(row.get("pattern", ""))
    high, medium, low = buckets["High"], buckets["Medium"], buckets["Low"]
    parts = []
    if high:
        parts.append(
            f"{len(high)} high-impact ({', '.join(high)}) — these usually need a "
            "redesign rather than a like-for-like API swap"
        )
    if medium:
        parts.append(f"{len(medium)} medium-impact ({', '.join(medium)})")
    if low:
        parts.append(f"{len(low)} low-impact ({', '.join(low)})")
    return (
        f"The scan flagged {_n(len(patterns), 'complex pattern type')}: "
        + "; ".join(parts)
        + ". Focus migration effort on the high-impact items first."
    )


def _fallback_workload_classification(ctx: dict) -> str:
    """Deterministic explanation of the 4-archetype workload classification."""
    wc = ctx.get("workload_classification") or {}
    label = wc.get("classification", "Unknown")
    ingestion_level = wc.get("ingestion_level", "")
    compute_level = wc.get("compute_level", "")
    ingestion_pct = wc.get("ingestion_file_pct", 0.0)
    spark_per_10 = wc.get("spark_per_10_loc", 0.0)
    if not ingestion_level or not compute_level:
        # Legacy IR — fall back to operation-count explanation.
        ingestion = wc.get("io_operations", 0)
        compute = wc.get("transform_operations", 0)
        return (
            f"Classified <strong>{label}</strong>: "
            f"{_n(ingestion, 'ingestion operation')} vs {_n(compute, 'compute operation')} detected."
        )
    return (
        f"Classified <strong>{label}</strong> "
        f"({ingestion_level} Ingestion, {compute_level} Compute): "
        f"{ingestion_pct:.0%} of files perform external reads or writes; "
        f"the codebase averages {spark_per_10:.1f} Spark API calls per 10 lines of code."
    )


def _finding_resolution_counts(ctx: dict) -> dict[str, int]:
    """Count issues by resolution type using the rolled-up ``issues`` list.

    ``issues`` (populated by the analyzer from analysis.json) are category-level
    summaries whose ``count`` field reflects the number of individual findings
    in that category.  Using ``issues`` instead of ``detailed_findings`` gives
    totals that match what the rest of the report already shows.

    ``issue_type`` values (derived from the KB ``status_class`` and finding ``kind``):
      * ``Fixed``      — auto-resolved by a deterministic recipe; no manual follow-up.
      * ``Conversion`` — the AI engine will convert these patterns to Snowpark equivalents.
      * ``Warning``    — code will run but behaviour may differ in edge cases.
      * ``Other``/``Parsing`` — miscellaneous; treat as needing review.
    """
    counts: dict[str, int] = {
        "auto_resolved": 0,
        "llm_conversion": 0,
        "warning": 0,
        "other": 0,
    }
    for issue in ctx.get("issues", []):
        issue_type = (issue.get("issue_type") or "").strip()
        n = int(issue.get("count") or 1)
        if issue_type == "Fixed":
            counts["auto_resolved"] += n
        elif issue_type in ("Conversion", "Parsing"):
            counts["llm_conversion"] += n
        elif issue_type == "Warning":
            counts["warning"] += n
        else:
            counts["other"] += n
    return counts



def _deployment_prerequisites(ctx: dict) -> list[dict]:
    """Return a list of deployment prerequisite items derived from EAI and AR signals.

    These are environment-setup tasks that must be completed before the migrated
    code can run.  They do NOT affect the project type tier because they are
    infrastructure concerns, not code-complexity concerns.

    Each item has: ``kind`` (str), ``count`` (int), ``detail`` (str).
    """
    items: list[dict] = []
    eai = ctx.get("wc_eai_count", 0)
    deps = ctx.get("wc_custom_dep_count", 0)
    if eai:
        items.append({
            "kind": "eai",
            "icon": "🌐",
            "label": "External Access Integration",
            "count": eai,
            "detail": (
                f"{_n(eai, 'file')} make external network calls and require "
                "Snowflake Network Rules and External Access Integrations "
                "to be provisioned by your team before the migrated code can run."
            ),
        })
    if deps:
        items.append({
            "kind": "packages",
            "icon": "📦",
            "label": "Custom Package Staging",
            "count": deps,
            "detail": (
                f"{_n(deps, 'package')} are outside the Snowflake Anaconda channel "
                "and must be uploaded to a Snowflake Stage before deployment."
            ),
        })
    return items


def _fallback_project_type(ctx: dict) -> str:
    """Calibrated, fact-based advisory for the project type section.

    When analyzer findings are present the advisory is derived from actual
    ``kind`` and ``final_risk`` data — no fabricated claims about resolution
    outcomes.  High-risk LLM findings are called out explicitly: they are
    likely to produce ``SCOS-TODO`` markers after Phase 2 and will require
    manual engineering work.  "Minimal manual intervention" language is never
    used because the tool cannot guarantee LLM conversion success.
    """
    pt = ctx.get("project_type") or {}
    label = pt.get("label", "")
    if not label:
        return ""

    counts = ctx.get("pt_finding_counts") or {}
    auto_resolved  = counts.get("auto_resolved", 0)
    llm_conversion = counts.get("llm_conversion", 0)
    warning        = counts.get("warning", 0)
    other          = counts.get("other", 0)
    total_findings = auto_resolved + llm_conversion + warning + other

    # --- Finding-level advisory (when analyzer findings are available) ------
    if total_findings > 0:
        lines: list[str] = []

        if auto_resolved:
            lines.append(
                f"<strong>{auto_resolved}</strong> {_plural(auto_resolved, 'finding')} auto-resolved by "
                "deterministic recipes, zero manual work required."
            )
        if llm_conversion:
            lines.append(
                f"<strong>{llm_conversion}</strong> {_plural(llm_conversion, 'finding')} queued for "
                "AI-powered conversion. The Cortex engine will automatically restructure "
                "these Spark patterns into Snowpark equivalents. "
                "Review the converted code and resolve any <code>SCOS-TODO</code> "
                "markers before validation."
            )
        if warning:
            lines.append(
                f"<strong>{warning}</strong> {_plural(warning, 'finding')} with known behavioral "
                "differences. Code will run but edge-case outputs may differ "
                "from the original Spark behavior. Validate against your test suite."
            )
        if other:
            lines.append(
                f"<strong>{other}</strong> {_plural(other, 'finding')} without a confirmed "
                "resolution path. These require manual review."
            )

        # Expected action sourced from llm_capabilities.json _tiers via the IR.
        expected = pt.get("expected_action", "")
        if expected:
            lines.append(expected)
        else:
            lines.append("<strong>Expected action:</strong> Proceed to conversion.")
        return "<br>".join(lines)

    # --- Scan-only fallback (no analyzer findings yet) ----------------------
    # scan_advisory is populated from llm_capabilities.json _tiers by scan_codebase.py.
    return pt.get("scan_advisory") or f"Classified <strong>{label}</strong>."


def _fallback_code_churn(ctx: dict) -> str:
    """Advisory explanation of what the code-churn category means for the team."""
    cc = ctx.get("code_churn") or {}
    category = cc.get("category", "High")
    active = cc.get("files_active_refactor", 0)
    light = cc.get("files_light_refactor", 0)
    if category == "Low":
        return (
            f"The <strong>{active}</strong> Active Refactor {_plural(active, 'file')} need focused "
            "development effort. Plan dedicated sprint time before expecting the workload "
            "to run on Snowflake. The automated migration skill handles the mechanical "
            "rewrites; reserve engineering capacity for logic that cannot be auto-converted."
        )
    if category == "Medium":
        return (
            f"The <strong>{light}</strong> Light Refactor {_plural(light, 'file')} need small targeted "
            "fixes, typically a few API swaps or import updates with no deep rewrites. "
            "The automated migration skill covers most of these; a brief review pass "
            "is usually enough to clear the remaining touch-ups."
        )
    return (
        "All files are drop-in compatible. The automated migration skill should "
        "handle the full conversion without manual intervention beyond standard "
        "validation."
    )


def _narrative_fallbacks(ctx: dict) -> dict[str, str]:
    """Deterministic explanations for all narrative sections, used whenever the
    matching ``narratives`` field is empty."""
    return {
        "executive_summary": _fallback_executive_summary(ctx),
        "complex_patterns": _fallback_complex_patterns(ctx),
        "workload_classification": _fallback_workload_classification(ctx),
        "project_type": _fallback_project_type(ctx),
        "code_churn": _fallback_code_churn(ctx),
    }


def _issue_rollup(issues: list[dict[str, Any]]) -> dict[str, int]:
    """Count Issue Summary tiles by ``issue_type`` for the v1 report.

    Counts UNIQUE ISSUE ROWS (not summed occurrence counts) so tile numbers
    match the table rows the user can see.  Falls back to the legacy
    code-suffix heuristic for IR payloads that pre-date ``issue_type``.

    ``-L`` suffix is NOT mapped to "parsing" — low-risk LLM findings are
    advisory observations, not parse errors.  Only explicit
    ``issue_type='Parsing'`` rows count in the Parsing tile.

    Fixed rows have already been removed from the issues list by the
    render() function before this is called, so they never appear here.
    """
    counts = {"warnings": 0, "conversion": 0, "parsing": 0}
    for row in issues:
        it = (row.get("issue_type") or "").strip()
        if it == "Conversion":
            counts["conversion"] += 1
        elif it == "Warning":
            counts["warnings"] += 1
        elif it == "Parsing":
            counts["parsing"] += 1
        else:
            # Legacy fallback: code suffix -H/-M for pre-issue_type IR payloads.
            code = (row.get("code") or "").upper()
            if code.endswith("-H"):
                counts["conversion"] += 1
            elif code.endswith("-M"):
                counts["warnings"] += 1
            # -L → ignored (other/advisory, not displayed as parsing)
    return counts


def _severity_badge(level: str) -> str:
    """Severity → badge color (v1 palette).

    Mirrors the readiness palette with inverted polarity so the per-file
    table and its expanded findings agree at a glance:

      * ``High``   (``"Resolution Planned"``)   → orange ↔ Active Refactor
      * ``Medium`` (``"Adjustments Planned"``)  → yellow ↔ Light Refactor
      * ``Low``    (``"Minor"``)                → green  ↔ Ready

    Red has been retired from the *readiness/risk* palette in v1 (it
    survives only as a factual signal in the compatibility breakdown,
    e.g. "unsupported APIs"). Downstream tone is softened by
    :func:`_risk_label` so the badge text reads as a plan instead of a
    verdict.
    """
    return {
        "High": "badge-orange",
        "Medium": "badge-yellow",
        "Low": "badge-green",
        "Green": "badge-green",
        "Yellow": "badge-yellow",
        "Red": "badge-orange",
    }.get(level, "badge-green")


def _readiness_badge(level: str) -> str:
    """Readiness → badge color (v1 palette).

    Inverted polarity from severity: ``High`` is green, ``Medium`` is
    yellow, and ``Low`` picks up its own orange track so the per-file
    table, the dependency-graph nodes, and the prerequisites bar chart
    all signal "Active Refactor" with the same color.
    :func:`_readiness_label` carries the matching wording.
    """
    return {
        "High": "badge-green",
        "Medium": "badge-yellow",
        "Low": "badge-orange",
    }.get(level, "badge-green")


def _risk_label(level: str) -> str:
    """Display label for finding severity badges (v1 wording).

    The report used to surface findings as ``"Risk: High"`` in red, which
    read as a verdict on the migration. v1 describes the same triage as
    a plan: ``High`` becomes ``"Resolution Planned"`` so the badge says
    *we will handle this*, not *this is broken*. Medium/Low pick up
    neutral wording so the badge family reads in one tone.
    """
    return {
        "High": "Resolution Planned",
        "Medium": "Adjustments Planned",
        "Low": "Minor",
    }.get(level, level)


def _readiness_label(level: str) -> str:
    """Display label for per-file readiness badges (v1 wording).

    The three buckets need to read as visibly different scales of work
    so a reviewer can scan the file table without parsing colors. v1
    pairs an adjective signalling magnitude with the same noun so the
    difference is in the qualifier, not the category:

        High    → "Ready"             (no work)
        Medium  → "Light Refactor"    (small, scattered touch-ups)
        Low     → "Active Refactor"   (a meaningful, focused chunk of work)
    """
    return {
        "High": "Ready",
        "Medium": "Light Refactor",
        "Low": "Active Refactor",
    }.get(level, level)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_TEMPLATE_NAME = "prototype_v1.html.j2"

_CHART_COLORS = {
    "teal": "#51CAA5",
    "yellow": "#ECB700",
    "blue": "#1A6CE7",
    "gray": "#8A999E",
}

# Ordered palette for the file-type doughnut (cycles if more types exist).
_FILE_TYPE_COLORS = [
    "#1A6CE7",  # blue
    "#51CAA5",  # teal
    "#ECB700",  # yellow
    "#7157F4",  # purple
    "#FF7C1D",  # orange
    "#70DDFF",  # light-blue
    "#D45CFF",  # pink
    "#8A999E",  # gray
]

# Readiness → bar colour for the Wave 0 prerequisites chart. Matches the
# dependency-diagram node stroke colours and the per-file readiness badge
# so the same file shows the same color across the three views. ``Low``
# tracks the "Active Refactor" orange.
_STATUS_COLORS = {
    "High": "#2ecc71",
    "Medium": "#f39c12",
    "Low": "#FF7C1D",
}


def _file_compat_breakdown(files: list[dict[str, Any]]) -> dict[str, int]:
    """Derive file compatibility counts from the files list.

    High status = fully compatible, Medium = require changes, Low = unsupported.
    """
    fully_compatible = 0
    require_changes = 0
    unsupported = 0

    for f in files:
        status = (f.get("status") or "").lower()
        if status == "high":
            fully_compatible += 1
        elif status == "medium":
            require_changes += 1
        else:
            unsupported += 1

    return {
        "fully_compatible": fully_compatible,
        "require_changes": require_changes,
        "unsupported": unsupported,
    }


def _lib_deps_classification(third_party_libs: list[dict[str, Any]]) -> dict[str, int]:
    """Classify libraries into display buckets for the doughnut chart.

    Counts mirror exactly what the table shows: ``snowpark_supported=True``
    rows count as "supported" (green badge), ``False`` rows as "unsupported"
    (orange/no badge).  This keeps the overview chart and the detail table
    consistent — if 15 entries show "No" in the table, 15 should appear in
    the unsupported slice of the chart.

    The ``role`` field is NOT used for bucketing here — roles like
    "migration-scope" still show "No" in the table (the tool will rewrite
    them away, but they're not natively supported in Snowflake), so they
    count as unsupported in the chart too.

    The "internal" bucket is retained for legacy IR payloads where internal
    modules were not yet filtered out by scan_codebase.
    """
    supported = 0
    unsupported = 0
    internal = 0

    for lib in third_party_libs:
        role = lib.get("role", "")
        if role == "internal":
            # Shouldn't appear here after scan_codebase filtering, but handle.
            internal += 1
        elif lib.get("snowpark_supported"):
            supported += 1
        else:
            unsupported += 1

    return {
        "supported": supported,
        "unsupported": unsupported,
        "internal": internal,
        "unknown": 0,
    }


def _lib_deps_chart_json(deps: dict[str, int]) -> str:
    """Produce the JSON array for the Chart.js doughnut."""
    items = [
        {"key": "supported", "label": "Supported third-party libraries", "color": _CHART_COLORS["teal"], "count": deps["supported"]},
        {"key": "unsupported", "label": "Unsupported third-party libraries", "color": _CHART_COLORS["yellow"], "count": deps["unsupported"]},
        {"key": "internal", "label": "Internal", "color": _CHART_COLORS["blue"], "count": deps["internal"]},
        {"key": "unknown", "label": "Unknown", "color": _CHART_COLORS["gray"], "count": deps["unknown"]},
    ]
    return json.dumps(items)


def _file_summary_chart_json(file_summary_by_type: list[dict[str, Any]]) -> str:
    """JSON array for the file-type doughnut in the detailed compatibility tab.

    Each item carries the color assigned by position in ``_FILE_TYPE_COLORS``
    so the JS legend and the Chart.js arc use exactly the same value.
    """
    return json.dumps([
        {
            "label": row.get("type", ""),
            "count": row.get("files", 0),
            "lines": row.get("lines", 0),
            "percent": row.get("percent", 0.0),
            "color": _FILE_TYPE_COLORS[i % len(_FILE_TYPE_COLORS)],
        }
        for i, row in enumerate(file_summary_by_type)
    ])


def _spark_api_chart_json(spark_api_by_category: list[dict[str, Any]]) -> str:
    """JSON for the Spark API horizontal stacked bar chart.

    Returns a dict with ``categories`` (per-category supported/unsupported
    counts) plus pre-summed totals so the template can render stat pills
    without re-summing in Jinja2.

    Colors: supported = teal (#51CAA5), needs attention = yellow (#ECB700),
    matching the user's requested green/yellow palette.
    """
    categories = [
        {
            "category": row.get("category", ""),
            "supported": row.get("supported", 0),
            "unsupported": row.get("unsupported", 0),
        }
        for row in spark_api_by_category
    ]
    total_supported = sum(c["supported"] for c in categories)
    total_unsupported = sum(c["unsupported"] for c in categories)
    total = total_supported + total_unsupported
    return json.dumps({
        "categories": categories,
        "total_supported": total_supported,
        "total_unsupported": total_unsupported,
        "total": total,
        "supported_pct": round(100 * total_supported / total, 1) if total else 0.0,
        "unsupported_pct": round(100 * total_unsupported / total, 1) if total else 0.0,
    })


def _data_distribution_chart_json(data_sources: list[dict[str, Any]]) -> str:
    """Build JSON for the stacked bar charts (Sources + Targets).

    X-axis = connection types (S3, HDFS, etc.).
    Stacked segments = formats (Json, Parquet, etc.).
    Only rows with a non-empty connection are included (code-only detections
    without a URL are already merged into a connection by the pipeline).
    """
    # Only include rows that have a connection label
    conn_labels: list[str] = []
    for ds in data_sources:
        conn = ds.get("connection", "")
        if conn and conn not in conn_labels:
            conn_labels.append(conn)

    if not conn_labels:
        return json.dumps({"labels": ["—"], "sources": {"None": [0]}, "targets": {"None": [0]}})

    sources: dict[str, list[int]] = {}
    targets: dict[str, list[int]] = {}

    for ds in data_sources:
        conn = ds.get("connection", "")
        if not conn:
            continue
        fmt = ds.get("format", "Unknown")
        idx = conn_labels.index(conn)
        reads = ds.get("reads", 0)
        writes = ds.get("writes", 0)

        if reads > 0:
            if fmt not in sources:
                sources[fmt] = [0] * len(conn_labels)
            sources[fmt][idx] += reads

        if writes > 0:
            if fmt not in targets:
                targets[fmt] = [0] * len(conn_labels)
            targets[fmt][idx] += writes

    if not sources:
        sources = {"None": [0] * len(conn_labels)}
    if not targets:
        targets = {"None": [0] * len(conn_labels)}

    return json.dumps({"labels": conn_labels, "sources": sources, "targets": targets})


def _safe_script_json(obj: Any) -> str:
    """Serialize ``obj`` as JSON safe to embed via ``|safe`` inside a
    ``<script>`` block.

    Escapes ``</`` so a workload-controlled filename like
    ``foo</script>bar.py`` cannot break out of the script context and inject
    HTML. Defense-in-depth for CWE-79 on the adjacency-map embeds this
    adapter produces.
    """
    return json.dumps(obj).replace("</", "<\\/")


def _middle_truncate(text: str, max_len: int = 60) -> str:
    """Middle-truncate ``text`` to ``max_len`` chars with a horizontal
    ellipsis in the middle so both ends stay visible.

    A URI like ``s3://your-bucket/deep/deep/deep/incremental_daily/``
    truncated at 60 becomes ``s3://your-bucket/…/incremental_daily/`` —
    scheme+bucket at the front, last segment at the back — instead of
    the head-only ``s3://your-bucket/deep/deep/deep/incremental…``
    which loses the meaningful trailing segment.

    Returns ``text`` unchanged when ``len(text) <= max_len``. Uses a
    single-char horizontal ellipsis (U+2026) for the marker so the
    reported truncated length ~= ``max_len``.
    """
    if len(text) <= max_len:
        return text
    if max_len < 4:
        # Degenerate case — just clip.
        return text[: max_len - 1] + "…"
    # Split the budget roughly 60/40 favoring the head so the scheme
    # and bucket / db name stay legible; the tail keeps the last
    # ``max_len - head`` chars.
    keep = max_len - 1  # room for the ellipsis
    head_len = (keep * 3) // 5
    tail_len = keep - head_len
    return text[:head_len] + "…" + text[-tail_len:]


def _endpoint_preview(
    uris: list[str], max_items: int = 3, max_len: int = 60,
) -> list[dict[str, Any]]:
    """Precompute a truncated preview of ``uris`` for the tooltip.

    Returns a list of at most ``max_items`` display dicts, followed by
    a single ``{"remaining": N}`` entry when the input has more than
    ``max_items`` items::

        [{"display": "s3://your-bucket/…/incremental_daily/",
          "truncated": True,
          "full": "s3://your-bucket/prod/deduplication/incremental_daily/"},
         ...,
         {"remaining": 2}]

    Truncation is middle-based so both the scheme and the last segment
    remain visible (see :func:`_middle_truncate`). ``truncated`` is
    ``False`` when the raw URI fits under ``max_len`` characters and
    the display and full strings are identical.

    Called once per assessment for every chain node's
    ``external_sources`` and ``external_sinks`` list; the results are
    embedded in the SVG ``<title>`` tooltip via
    :func:`_endpoint_tooltip_lines`.
    """
    if not uris:
        return []
    out: list[dict[str, Any]] = []
    for uri in uris[:max_items]:
        display = _middle_truncate(uri, max_len=max_len)
        out.append({
            "display": display,
            "truncated": display != uri,
            "full": uri,
        })
    remaining = max(0, len(uris) - max_items)
    if remaining > 0:
        out.append({"remaining": remaining})
    return out


def _endpoint_tooltip_text(
    sources_preview: list[dict[str, Any]],
    sinks_preview: list[dict[str, Any]],
) -> str:
    """Render the "Reads from" / "Writes to" tooltip lines for a chain
    node's SVG ``<title>``.

    Native SVG ``<title>`` is plain text — newlines and bullets are the
    only formatting available. Returns an empty string when both
    previews are empty so the caller can skip appending anything to
    the base tooltip.
    """
    if not sources_preview and not sinks_preview:
        return ""
    lines: list[str] = []
    if sources_preview:
        lines.append("")
        lines.append("Reads from:")
        for item in sources_preview:
            if "remaining" in item:
                lines.append(
                    f"• … +{item['remaining']} more (click for full list)"
                )
            else:
                lines.append(f"• {item['display']}")
    if sinks_preview:
        lines.append("")
        lines.append("Writes to:")
        for item in sinks_preview:
            if "remaining" in item:
                lines.append(
                    f"• … +{item['remaining']} more (click for full list)"
                )
            else:
                lines.append(f"• {item['display']}")
    return "\n".join(lines)


def _build_endpoint_tooltip_map(
    data_dependency_graph: dict[str, Any] | None,
    file_info: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Precompute the tooltip suffix for every chain node.

    Appends I/O endpoint preview (if any) and file-info metadata
    (Source, Target, EAI, AR) so the SVG ``<title>`` tooltip is
    informative for all chain nodes, not just readers/writers.

    Returned dict is keyed by node id (``rel_path``); values are ready
    to concatenate to the base ``<title>`` string in the Jinja template."""
    fi_by_path: dict[str, dict[str, Any]] = {
        fi["path"]: fi for fi in (file_info or []) if fi.get("path")
    }
    out: dict[str, str] = {}
    if not data_dependency_graph:
        return out
    for n in data_dependency_graph.get("nodes", []):
        if n.get("group") != "chain":
            continue
        node_id = n.get("id") or ""
        sources = n.get("external_sources") or []
        sinks = n.get("external_sinks") or []
        parts: list[str] = []
        io_text = _endpoint_tooltip_text(
            _endpoint_preview(sources),
            _endpoint_preview(sinks),
        )
        if io_text:
            parts.append(io_text)
        fi = fi_by_path.get(node_id) or fi_by_path.get(n.get("path") or "")
        if fi:
            src_raw = fi.get("source_system") or ["N/A"]
            tgt_raw = fi.get("target_type") or ["N/A"]
            # Handle both list (new) and str (legacy) field shapes.
            src = ", ".join(src_raw) if isinstance(src_raw, list) else (src_raw or "N/A")
            tgt = ", ".join(tgt_raw) if isinstance(tgt_raw, list) else (tgt_raw or "N/A")
            eai = fi.get("eai_required") or "No"
            ar = fi.get("ar_required") or "No"
            parts.append(f"\nSource: {src}  Target: {tgt}\nEAI: {eai}  AR: {ar}")
        if parts:
            out[node_id] = "".join(parts)
    return out


def _build_endpoint_detail_map(
    data_dependency_graph: dict[str, Any] | None,
    file_info: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Precompute the FULL per-node detail data for the click-opened panel.

    Shape::

        {
          node_id: {
            "name": <basename>,
            "path": <full path>,
            "sources": [uri, ...],
            "sinks": [uri, ...],
            "source_system": "S3",
            "target_type": "Cloud Storage",
            "eai_required": "No",
            "ar_required": "Yes",
            "ar_packages": ["pygeohash"],
          },
          ...
        }

    All chain nodes are included so the panel opens for every file in
    the data pipeline, even pure transformers with no external I/O.
    Serialized via :func:`_safe_script_json` in the template."""
    fi_by_path: dict[str, dict[str, Any]] = {
        fi["path"]: fi for fi in (file_info or []) if fi.get("path")
    }
    out: dict[str, dict[str, Any]] = {}
    if not data_dependency_graph:
        return out
    for n in data_dependency_graph.get("nodes", []):
        if n.get("group") != "chain":
            continue
        node_id = n.get("id") or ""
        sources = list(n.get("external_sources") or [])
        sinks = list(n.get("external_sinks") or [])
        fi = fi_by_path.get(node_id) or fi_by_path.get(n.get("path") or "")
        fi_src = (fi or {}).get("source_system", ["N/A"])
        fi_tgt = (fi or {}).get("target_type", ["N/A"])
        # Normalize to list for the JS panel (handles both old str and new list).
        if isinstance(fi_src, str):
            fi_src = [fi_src] if fi_src else ["N/A"]
        if isinstance(fi_tgt, str):
            fi_tgt = [fi_tgt] if fi_tgt else ["N/A"]
        fi_fmts = list((fi or {}).get("file_formats") or [])
        out[node_id] = {
            "name": n.get("full_label") or n.get("label") or node_id,
            "path": n.get("path") or node_id,
            "sources": sources,
            "sinks": sinks,
            "source_system": fi_src,
            "target_type": fi_tgt,
            "file_formats": fi_fmts,
            "eai_required": (fi or {}).get("eai_required", "No"),
            "ar_required": (fi or {}).get("ar_required", "No"),
            "ar_packages": list((fi or {}).get("ar_packages") or []),
        }
    return out


def _dependents_adjacency_json(dependency_graph: dict[str, Any] | None) -> str:
    """Map each node id → list of its DIRECT importer ids (who imports it).

    An edge ``source -> target`` means ``source`` imports ``target``, so
    ``target``'s importers include ``source``. The diagram JS walks this map
    outward from a clicked node to compute the transitive blast radius — every
    file that would break if the clicked file's interface changes.
    """
    importers: dict[str, list[str]] = {}
    if dependency_graph:
        for e in dependency_graph.get("edges", []):
            src = e.get("source")
            tgt = e.get("target")
            if not src or not tgt:
                continue
            importers.setdefault(tgt, []).append(src)
    return json.dumps(importers)


def _data_consumers_adjacency_json(data_dependency_graph: dict[str, Any] | None) -> str:
    """Map each node id → list of nodes that DIRECTLY consume its data output.

    An edge ``source -> target`` in the data graph means ``source`` writes data
    that ``target`` reads.  The diagram JS walks this forward adjacency from a
    clicked node to find every downstream consumer — files that would be
    affected if this data source changed or was not yet migrated.

    Non-data edges (the summary ``framework``-cluster arrow, and the
    ``orchestrates`` arrow from the orchestrator to the reader) are excluded
    so clicking a chain file's producer doesn't spuriously highlight every
    utility file, and clicking an orchestrator doesn't highlight the reader
    (orchestration is not data consumption).
    """
    consumers: dict[str, list[str]] = {}
    if data_dependency_graph:
        for e in data_dependency_graph.get("edges", []):
            if e.get("kind") not in (None, "data", "import", "factory_dispatch"):
                continue
            src = e.get("source")
            tgt = e.get("target")
            if not src or not tgt:
                continue
            consumers.setdefault(src, []).append(tgt)
    return _safe_script_json(consumers)


def _import_adjacency_json(
    dependency_graph: dict[str, Any] | None,
    data_dependency_graph: dict[str, Any] | None,
) -> str:
    """Map each data-DAG node id → its bidirectional structural adjacency.

    Result shape::

        {node_id: {
            "orchestrates": [...],        # dynamic-import targets OUT
            "orchestrated_by": [...],     # dynamic-import sources IN
            "data_produces_to": [...],    # data-flow targets OUT (readers of my sinks)
            "data_consumes_from": [...],  # data-flow sources IN (writers of my sources)
        }, ...}

    Powers the symmetric hover-highlight in the Data Dependency Graph section.
    The four categories are ALL about data flow: ``orchestrates`` /
    ``orchestrated_by`` describe dynamic-import runtime wiring (the dashed
    blue arrows), ``data_produces_to`` / ``data_consumes_from`` are the amber
    writer→reader arrows.

    STATIC PYTHON IMPORTS ARE INTENTIONALLY EXCLUDED here — the report's
    separate "Import dependency graph" section already exposes them via its
    own click-to-highlight interaction. Mixing the two on hover clutters
    the data-flow narrative that this section is meant to convey.
    """
    if not data_dependency_graph:
        return _safe_script_json({})
    data_node_ids = {
        n.get("id") for n in data_dependency_graph.get("nodes", [])
        if n.get("id") and not str(n.get("id", "")).startswith("ext:")
    }
    if not data_node_ids:
        return _safe_script_json({})
    orchestrates_map: dict[str, list[str]] = {nid: [] for nid in data_node_ids}
    orchestrated_by_map: dict[str, list[str]] = {nid: [] for nid in data_node_ids}
    data_out_map: dict[str, list[str]] = {nid: [] for nid in data_node_ids}
    data_in_map: dict[str, list[str]] = {nid: [] for nid in data_node_ids}
    seen_orch: dict[str, set[str]] = {nid: set() for nid in data_node_ids}
    seen_orch_by: dict[str, set[str]] = {nid: set() for nid in data_node_ids}
    seen_data_out: dict[str, set[str]] = {nid: set() for nid in data_node_ids}
    seen_data_in: dict[str, set[str]] = {nid: set() for nid in data_node_ids}

    # Data-flow + orchestrates edges from the data DAG.
    for e in data_dependency_graph.get("edges", []):
        kind = e.get("kind")
        src = e.get("source")
        tgt = e.get("target")
        if not src or not tgt:
            continue
        if kind == "orchestrates":
            if src in data_node_ids and tgt not in seen_orch[src]:
                seen_orch[src].add(tgt)
                orchestrates_map[src].append(tgt)
            if tgt in data_node_ids and src not in seen_orch_by[tgt]:
                seen_orch_by[tgt].add(src)
                orchestrated_by_map[tgt].append(src)
        elif kind in (None, "data", "factory_dispatch"):
            if src in data_node_ids and tgt not in seen_data_out[src]:
                seen_data_out[src].add(tgt)
                data_out_map[src].append(tgt)
            if tgt in data_node_ids and src not in seen_data_in[tgt]:
                seen_data_in[tgt].add(src)
                data_in_map[tgt].append(src)

    return _safe_script_json({
        nid: {
            "orchestrates": orchestrates_map[nid],
            "orchestrated_by": orchestrated_by_map[nid],
            "data_produces_to": data_out_map[nid],
            "data_consumes_from": data_in_map[nid],
        }
        for nid in data_node_ids
    })


_ISSUE_TYPE_ORDER = {"Conversion": 0, "Parsing": 1, "Warning": 2, "Other": 3}


def _sort_issues_by_type(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort issues: Conversion first, then Parsing, Warning, Fixed, Other.
    Within each type, sort by count descending."""
    def _key(row: dict[str, Any]) -> tuple[int, int]:
        it = (row.get("issue_type") or "Other").strip()
        order = _ISSUE_TYPE_ORDER.get(it, 4)
        return (order, -(row.get("count") or 0))
    return sorted(issues, key=_key)


def _unresolved_dynamic_imports_summary(
    entries: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Summarize the unresolved dynamic-import list for template rendering.

    Returns::

        {
          "count": int,
          "by_kind": {kind: n},
          "entries": [<sorted by (file, line)>],
        }

    ``entries`` sort deterministic so the rendered table order is stable
    across runs.
    """
    entries = list(entries or [])
    entries_sorted = sorted(
        entries,
        key=lambda e: (e.get("file") or "", int(e.get("line") or 0)),
    )
    by_kind: dict[str, int] = {}
    for e in entries_sorted:
        k = e.get("kind") or "unknown"
        by_kind[k] = by_kind.get(k, 0) + 1
    return {
        "count": len(entries_sorted),
        "by_kind": by_kind,
        "entries": entries_sorted,
    }


def _unresolved_data_edges_summary(
    entries: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Summarize the unresolved read/write call list for template rendering.

    Mirrors :func:`_unresolved_dynamic_imports_summary` — the two warning
    blocks share visual styling in the template so their summary shapes are
    parallel. ``by_kind`` here splits ``"read"`` vs ``"write"`` so the
    heading can call out the read/write breakdown at a glance.

    Returns::

        {
          "count": int,
          "by_kind": {"read": n, "write": m},
          "entries": [<sorted by (file, line)>],
        }
    """
    entries = list(entries or [])
    entries_sorted = sorted(
        entries,
        key=lambda e: (e.get("file") or "", int(e.get("line") or 0)),
    )
    by_kind: dict[str, int] = {"read": 0, "write": 0}
    for e in entries_sorted:
        k = e.get("kind") or "read"
        # Fold unrecognized kinds into "read" — keeps the heading total
        # honest without inventing a bucket for a malformed IR record.
        if k not in by_kind:
            k = "read"
        by_kind[k] = by_kind.get(k, 0) + 1
    return {
        "count": len(entries_sorted),
        "by_kind": by_kind,
        "entries": entries_sorted,
    }


def _prerequisite_rows(
    most_depended_files: list[dict[str, Any]],
    files: list[dict[str, Any]],
    dependency_graph: dict[str, Any] | None,
    file_info: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build the 'Migration Prerequisites (Wave 0)' rows.

    Each entry pairs a file's in-degree (how many project files import it)
    with its readiness ``status`` and ``lines`` (joined from the merged
    ``files`` table) plus its transitive ``blast_radius`` (from the unified
    dependency graph). The bar length is the in-degree; the colour is the
    readiness — so a long red bar is the highest-priority bottleneck (widely
    depended-upon AND hard to migrate). Drives both the horizontal bar chart
    and the companion table.

    Also includes ``importers`` (list of basenames that directly import this
    file) and ``source_system`` / ``target_type`` labels from the file-info
    table for tooltip drill-down on the prerequisites chart.
    """
    files_by_path = {f.get("path"): f for f in files}
    files_by_name: dict[str, dict[str, Any] | None] = {}
    for f in files:
        name = f.get("name")
        files_by_name[name] = None if name in files_by_name else f

    blast_by_path: dict[str, int] = {}
    graph_indegree_by_path: dict[str, int] = {}
    # Map target path → list of importer basenames (direct importers only).
    importers_by_path: dict[str, list[str]] = {}
    if dependency_graph:
        for n in dependency_graph.get("nodes", []):
            nid = n.get("id")
            blast_by_path[nid] = n.get("blast_radius", 0)
            # Graph in_degree counts import edges only — consistent with blast_radius.
            # metric (bar length) may be larger because it also counts data-flow edges.
            graph_indegree_by_path[nid] = n.get("in_degree", 0)
        for e in dependency_graph.get("edges", []):
            src = e.get("source")
            tgt = e.get("target")
            if src and tgt:
                importers_by_path.setdefault(tgt, []).append(
                    src.split("/")[-1]  # basename only for readability
                )

    # file_info lookup: path → {source_system, target_type}.
    file_info_by_path: dict[str, dict[str, Any]] = {}
    for fi in (file_info or []):
        p = fi.get("path")
        if p:
            file_info_by_path[p] = fi

    rows: list[dict[str, Any]] = []
    for row in most_depended_files:
        path = row.get("path")
        has_import_graph = (path or "") in blast_by_path
        # This chart lives under "File dependencies" — only show files that
        # actually appear in the import graph. Files whose only connections are
        # data-flow edges are not import prerequisites and must not appear here.
        if not has_import_graph:
            continue
        name = row.get("name", path)
        canonical = files_by_path.get(path) or files_by_name.get(name)
        status = (canonical or {}).get("status", "High")
        lines = (canonical or {}).get("lines", 0)
        fi = file_info_by_path.get(path or "")
        fi_src = (fi or {}).get("source_system", ["N/A"])
        fi_tgt = (fi or {}).get("target_type", ["N/A"])
        # Normalize to comma-joined string for the chart tooltip (chart only
        # shows the first/primary value; full list is in the click panel).
        if isinstance(fi_src, list):
            source_system = ", ".join(fi_src)
        else:
            source_system = fi_src or "N/A"
        if isinstance(fi_tgt, list):
            target_type = ", ".join(fi_tgt)
        else:
            target_type = fi_tgt or "N/A"
        raw_importers = importers_by_path.get(path or "", [])
        importers = sorted(set(raw_importers))[:10]
        importers_overflow = max(0, len(set(raw_importers)) - 10)
        graph_in_degree = graph_indegree_by_path[path]
        rows.append({
            "name": name,
            "path": path,
            "in_degree": graph_in_degree,  # bar length: import edges only
            "graph_in_degree": graph_in_degree,
            "blast_radius": blast_by_path[path],
            "has_import_graph": True,
            "lines": lines,
            "status": status,
            "color": _STATUS_COLORS.get(status, _STATUS_COLORS["High"]),
            "importers": importers,
            "importers_overflow": importers_overflow,
            "source_system": source_system,
            "target_type": target_type,
        })
    rows.sort(key=lambda r: r["in_degree"], reverse=True)
    return rows


def _derive_checklist(
    detailed_findings: list[dict[str, Any]],
    recommendations: list[str],
    data_sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Derive migration checklist items and groups from findings data.

    Returns (all_items, groups, summary_text).
    """
    all_items: list[dict[str, Any]] = []
    blockers_items: list[dict[str, Any]] = []
    infra_items: list[dict[str, Any]] = []
    cleanup_items: list[dict[str, Any]] = []

    high_findings = [f for f in detailed_findings if (f.get("severity") or "").lower() == "high"]
    seen_files: set[str] = set()
    for f in high_findings:
        file_name = f.get("name") or f.get("file", "")
        key = f"{file_name}:{f.get('root_cause', '')}"
        if key in seen_files:
            continue
        seen_files.add(key)
        blockers_items.append({
            "file": file_name,
            "label": "",
            "detail": f.get("root_cause") or f.get("explanation", ""),
            "status": "pending",
            "is_blocker": True,
        })

    infra_items = [
        {"file": "", "label": "Snowflake account with SCOS enabled", "detail": "", "status": "pending", "is_blocker": False},
        {"file": "", "label": "SPARK_REMOTE env var set", "detail": "", "status": "pending", "is_blocker": False},
    ]

    has_s3 = any(
        ds.get("connection", "").upper() == "S3" or ds.get("format", "").upper() == "S3"
        for ds in data_sources
    )
    if has_s3:
        infra_items.append({"file": "", "label": "External stage created for S3 paths", "detail": "", "status": "pending", "is_blocker": False})

    for rec in recommendations[:5]:
        cleanup_items.append({
            "file": "",
            "label": rec,
            "detail": "",
            "status": "pending",
            "is_blocker": False,
        })

    all_items = blockers_items + infra_items + cleanup_items

    groups = []
    if blockers_items:
        groups.append({"title": "Blockers", "note": "Must resolve before any test", "entries": blockers_items})
    if infra_items:
        groups.append({"title": "Infrastructure", "note": "", "entries": infra_items})
    if cleanup_items:
        groups.append({"title": "Code cleanup", "note": "Before test", "entries": cleanup_items})

    blocker_files = [item["file"] for item in blockers_items if item["file"]][:3]
    summary = ""
    if blocker_files:
        summary = f"Resolve blockers in {', '.join(blocker_files)}. Once cleared, set up infrastructure and complete code cleanup items."

    return all_items, groups, summary


def _make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "htm", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["severity_badge"] = _severity_badge
    env.globals["readiness_badge"] = _readiness_badge
    env.globals["color_badge"] = _color_badge
    env.globals["effort_label"] = _effort_label
    env.globals["risk_label"] = _risk_label
    env.globals["readiness_label"] = _readiness_label
    return env


def render(assessment: Assessment) -> str:
    """Render ``assessment`` to HTML and return it as a string."""
    env = _make_env()
    template = env.get_template(_TEMPLATE_NAME)

    ctx = assessment.model_dump(mode="python")
    ctx["default_exec_summary"] = _default_exec_summary(ctx["workload"])
    ctx["issue_rollup"] = _issue_rollup(ctx["issues"])

    # Finding resolution counts — derived from issues.issue_type, which rolls up
    # all findings by resolution category and matches what the rest of the report
    # displays.  Must be computed before narrative_fallbacks.
    ctx["pt_finding_counts"] = _finding_resolution_counts(ctx)

    files = ctx.get("files", [])
    ctx["file_compat_breakdown"] = _file_compat_breakdown(files)

    third_party_libs = ctx.get("third_party_libs", [])
    lib_deps = _lib_deps_classification(third_party_libs)
    ctx["lib_deps"] = lib_deps
    ctx["lib_deps_total"] = sum(lib_deps.values())
    ctx["lib_deps_chart_json"] = _lib_deps_chart_json(lib_deps)
    ctx["file_summary_chart_json"] = _file_summary_chart_json(ctx.get("file_summary_by_type", []))
    ctx["spark_api_chart_json"] = _spark_api_chart_json(ctx.get("spark_api_by_category", []))

    # Workload classification risk-modifier badges + project type elevation.
    # EAI/AR counts must be computed BEFORE narrative_fallbacks so that
    # _elevate_project_type_tier and _fallback_project_type can use them.
    ctx["wc_eai_count"] = sum(
        1 for r in ctx.get("file_info", []) if r.get("eai_required", "No") != "No"
    )
    ctx["wc_udf_count"] = next(
        (int(p.get("occurrences", 0)) for p in ctx.get("complex_patterns", [])
         if p.get("pattern") == "Custom UDFs"),
        0,
    )
    ctx["wc_custom_dep_count"] = lib_deps["unsupported"]

    # Deployment prerequisites — infrastructure items (EAI, custom packages) that
    # cannot be automated.  These are shown as a separate section in the report,
    # NOT folded into the project type tier (which classifies code complexity only).
    ctx["deployment_prerequisites"] = _deployment_prerequisites(ctx)

    ctx["narrative_fallbacks"] = _narrative_fallbacks(ctx)

    data_sources = ctx.get("data_sources", [])
    ctx["data_distribution_chart_json"] = _data_distribution_chart_json(data_sources)

    dependency_graph = ctx.get("dependency_graph")
    ctx["dependents_adjacency_json"] = _dependents_adjacency_json(dependency_graph)
    prereq_rows = _prerequisite_rows(
        ctx.get("most_depended_files", []), files, dependency_graph,
        ctx.get("file_info", []),
    )
    ctx["prereq_rows"] = prereq_rows
    ctx["prerequisites_chart_json"] = json.dumps(prereq_rows)

    data_dependency_graph = ctx.get("data_dependency_graph")
    ctx["data_consumers_adjacency_json"] = _data_consumers_adjacency_json(data_dependency_graph)
    ctx["import_adjacency_json"] = _import_adjacency_json(
        dependency_graph, data_dependency_graph
    )
    # Chain-node endpoint metadata for the Data Dependency Graph section:
    #   * ``endpoint_tooltip_data`` — precomputed truncated preview
    #     appended to each node's ``<title>`` tooltip.
    #   * ``endpoint_detail_json`` — full per-node detail (I/O URIs +
    #     source_system/target_type/EAI/AR), embedded as a JS variable
    #     and consumed by the click-opened side panel.
    file_info_list = ctx.get("file_info", [])
    ctx["endpoint_tooltip_data"] = _build_endpoint_tooltip_map(data_dependency_graph, file_info_list)
    ctx["endpoint_detail_json"] = _safe_script_json(
        _build_endpoint_detail_map(data_dependency_graph, file_info_list)
    )
    ctx["unresolved_dyn_imports_summary"] = _unresolved_dynamic_imports_summary(
        ctx.get("unresolved_dynamic_imports") or []
    )
    ctx["unresolved_data_edges_summary"] = _unresolved_data_edges_summary(
        ctx.get("unresolved_data_edges") or []
    )

    # LLM-enriched node IDs — nodes whose external_sources / external_sinks
    # were augmented by the LLM resolution pass.  Used in the template to
    # render an "LLM" badge on the node tooltip.
    ctx["llm_enriched_node_ids"] = {
        n["id"]
        for n in (data_dependency_graph.get("nodes", []) if data_dependency_graph else [])
        if n.get("llm_enriched")
    }
    # Count of LLM-enriched edges for the DAG section header annotation.
    llm_data = ctx.get("llm_resolved_data_edges") or {}
    llm_edges = llm_data.get("edges", []) if isinstance(llm_data, dict) else []
    ctx["llm_enriched_edge_count"] = sum(
        1 for e in llm_edges
        if (e.get("resolution_type") if isinstance(e, dict) else getattr(e, "resolution_type", ""))
        in ("literal_found", "traced")
    )
    # How many previously-unresolved edges the LLM resolved (so the unresolved
    # table can say "N resolved by LLM, M still remain" when LLM was run).
    ctx["llm_resolved_unresolved_count"] = sum(
        1 for e in llm_edges
        if (e.get("source") if isinstance(e, dict) else getattr(e, "source", ""))
        == "resolved_unresolved"
    )

    detailed_findings = ctx.get("detailed_findings", [])
    recommendations = ctx.get("recommendations", [])
    checklist_items, checklist_groups, checklist_summary = _derive_checklist(
        detailed_findings, recommendations, data_sources
    )
    ctx["checklist_items"] = checklist_items
    ctx["checklist_groups"] = checklist_groups
    ctx["checklist_summary"] = checklist_summary

    # Drop Fixed issues from the table — they are already fully covered by
    # the "Auto-resolved by tool" panel (recipe_resolved).  Keeping them
    # here would duplicate information the user already has.
    ctx["issues"] = _sort_issues_by_type(
        [i for i in ctx.get("issues", []) if (i.get("issue_type") or "") != "Fixed"]
    )

    # Partition-by table: pre-compute all strategy assignments from the
    # Assessment IR.  The result is embedded as a single JSON variable in the
    # HTML; client-side JavaScript handles strategy selection and table rendering.
    # build_partition_table_data operates on the Assessment model directly (not
    # the ctx dict) so it can access typed helpers on the model objects.
    ctx["partition_table_json"] = _safe_script_json(build_partition_table_data(assessment))

    # Pre-compute insignificant file count so the template doesn't need a
    # mutable namespace counter (Jinja2 namespace mutation has version quirks).
    def _is_insignificant(fi: dict[str, Any]) -> bool:
        src = fi.get("source_system") or ["N/A"]
        tgt = fi.get("target_type") or ["N/A"]
        if isinstance(src, str):
            src = [src]
        if isinstance(tgt, str):
            tgt = [tgt]
        src_all_na = all(s == "N/A" for s in src)
        tgt_all_na = all(t == "N/A" for t in tgt)
        return src_all_na and tgt_all_na and fi.get("eai_required") == "No" and fi.get("ar_required") != "Yes"

    ctx["file_info_insignificant_count"] = sum(
        1 for fi in ctx.get("file_info", []) if _is_insignificant(fi)
    )

    return template.render(**ctx)


def render_to_file(assessment: Assessment, path: Path) -> Path:
    """Render and write to ``path``. Creates parent directories if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(assessment), encoding="utf-8")
    return path.resolve()


__all__ = ["render", "render_to_file"]
