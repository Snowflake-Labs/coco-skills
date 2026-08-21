from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text()
    except OSError:
        return None


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _resolve_relative(root: Path, rel_path: str | None) -> Path | None:
    if not rel_path:
        return None
    return root / rel_path


def _safe_name(raw: str) -> str:
    return raw.replace("_", " ").strip().title()


def _line_preview(text: str | None, limit: int = 40) -> list[str]:
    if not text:
        return []
    return text.splitlines()[:limit]


def _truncate_text(text: str | None, limit: int = 180) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def entrypoint_short_name(entrypoint_id: str, source_path: str | None = None) -> str:
    """Human-readable label for long trial ids (e.g. ``compdata.py``)."""
    if source_path:
        name = Path(str(source_path).replace("\\", "/")).name
        if name:
            return name
    for marker in (
        "rouses_notebooks_workflow_tasks_rouses_comp_dag_",
        "rouses_notebooks_workflow_tasks_rouses_dag_",
        "notebooks_workflow_tasks_",
    ):
        if marker in entrypoint_id:
            tail = entrypoint_id.split(marker, 1)[-1]
            if tail:
                return tail
    if len(entrypoint_id) > 44:
        return entrypoint_id[:18] + "…" + entrypoint_id[-22:]
    return entrypoint_id


_BANNER_RE = re.compile(
    r"=+\s*\n?\s*SNOWPARK CONNECT ERROR CODE:\s*(\d+)\s*\n?\s*=+",
    re.IGNORECASE,
)
_SF_ERROR_RE = re.compile(
    r"\((\d+)\):\s*"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):\s*"
    r"(\d+)\s*"
    r"\((\d+)\):\s*"
    r"(.*?)(?=\nTraceback|\n=+\s*\n?SNOWPARK|\Z)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class ParsedWorkloadError:
    error_code: str | None = None
    channel: str | None = None
    query_id: str | None = None
    snowflake_code: str | None = None
    sql_state: str | None = None
    headline: str | None = None
    detail: str | None = None
    traceback: str | None = None
    raw: str = ""


def _clean_workload_traceback(raw: str) -> str | None:
    match = re.search(r"(Traceback \(most recent call last\):.*)", raw, re.DOTALL)
    if not match:
        return None
    tb = match.group(1)
    dup = re.split(r"\n=+\s*\n?SNOWPARK CONNECT ERROR CODE:", tb, maxsplit=1)
    if len(dup) > 1:
        tb = dup[0].rstrip()
        if tb.endswith(":"):
            tb = f"{tb} (see error summary above)"
    return tb.strip()


def parse_workload_error(text: str | None) -> ParsedWorkloadError | None:
    if not text or not text.strip():
        return None

    raw = text.strip()
    error_code: str | None = None
    channel: str | None = None
    query_id: str | None = None
    snowflake_code: str | None = None
    sql_state: str | None = None
    headline: str | None = None
    detail: str | None = None

    banner = _BANNER_RE.search(raw)
    if banner:
        error_code = banner.group(1)

    sf = _SF_ERROR_RE.search(raw)
    if sf:
        channel = sf.group(1)
        query_id = sf.group(2)
        snowflake_code = sf.group(3)
        sql_state = sf.group(4)
        msg_lines = [line.strip() for line in sf.group(5).splitlines() if line.strip()]
        if msg_lines:
            headline = msg_lines[0]
            detail_lines = msg_lines[1:]
            if headline.rstrip().endswith(":") and detail_lines:
                headline = f"{headline.rstrip()} {detail_lines[0]}"
                detail_lines = detail_lines[1:]
            detail = "\n".join(detail_lines) if detail_lines else None

    if not headline and not error_code:
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("=") and "SNOWPARK CONNECT" not in stripped.upper():
                headline = stripped
                break

    return ParsedWorkloadError(
        error_code=error_code,
        channel=channel,
        query_id=query_id,
        snowflake_code=snowflake_code,
        sql_state=sql_state,
        headline=headline,
        detail=detail,
        traceback=_clean_workload_traceback(raw),
        raw=raw,
    )


def format_workload_error_summary(parsed: ParsedWorkloadError | None, fallback: str | None = None) -> str:
    if not parsed:
        return fallback or ""
    parts: list[str] = []
    if parsed.error_code:
        parts.append(f"Error {parsed.error_code}")
    if parsed.headline:
        parts.append(parsed.headline)
    summary = ": ".join(parts) if len(parts) > 1 else (parts[0] if parts else "")
    if parsed.detail:
        first_line = parsed.detail.splitlines()[0]
        if first_line and first_line not in summary:
            summary = f"{summary} — {first_line}" if summary else first_line
    return summary or _truncate_text(fallback)


def workload_error_to_dict(parsed: ParsedWorkloadError | None) -> dict[str, Any] | None:
    if not parsed:
        return None
    return {
        "error_code": parsed.error_code,
        "channel": parsed.channel,
        "query_id": parsed.query_id,
        "snowflake_code": parsed.snowflake_code,
        "sql_state": parsed.sql_state,
        "headline": parsed.headline,
        "detail": parsed.detail,
        "summary": format_workload_error_summary(parsed),
        "traceback": parsed.traceback,
    }


def _load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    try:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"kind": "parse_error", "raw": line})
    except OSError:
        return []
    return events


def _discover_files(directory: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not directory.exists():
        return []
    return sorted([path for path in directory.rglob("*") if path.suffix.lower() in suffixes])


def _load_parquet_preview(path: Path, row_limit: int = 200) -> dict[str, Any]:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        return {
            "path": str(path),
            "error": f"pandas is required for parquet preview: {exc}",
        }

    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        return {
            "path": str(path),
            "error": str(exc),
        }

    preview = frame.head(row_limit)
    return {
        "path": str(path),
        "rows": int(len(frame.index)),
        "columns": list(frame.columns),
        "dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()},
        "preview": preview,
    }


@dataclass
class ValidationRunData:
    root: Path
    run_index: dict[str, Any]
    summary: dict[str, Any] | None
    report_markdown: str | None
    analysis: dict[str, Any] | None
    events: list[dict[str, Any]]
    warnings: list[str]
    entrypoints: list[dict[str, Any]]
    milestones: list[dict[str, Any]]
    event_counts: dict[str, int]
    verdict_counts: dict[str, int]
    run_metrics: dict[str, Any]
    artifact_inventory: list[dict[str, Any]]
    timeline_rows: list[dict[str, Any]]
    highlighted_events: list[dict[str, Any]]
    fixer_dispatches: list[dict[str, Any]]
    documented_divergences: list[dict[str, Any]]
    qualifications: list[dict[str, Any]]
    pipeline_steps: list[dict[str, Any]]


def load_validation_run(root_path: str | Path) -> ValidationRunData:
    root = Path(root_path).expanduser().resolve()
    run_index_path = root / "run_index.json"
    run_index = _read_json(run_index_path)
    if not run_index:
        raise FileNotFoundError(f"Missing or unreadable run_index.json in {root}")

    summary = _read_json(root / "results" / "summary.json")
    report_markdown = _read_text(root / "results" / "REPORT.md")
    # Scala validator keeps a single shared/analysis.json (no schemas/manifest
    # split and no sql_files merge — those are PySpark-only).
    analysis = _read_json(root / "shared" / "analysis.json")
    events = _load_events(root / "events.jsonl")

    entrypoints = [_build_entrypoint(root, entrypoint) for entrypoint in run_index.get("entrypoints", [])]
    milestones = _build_milestones(run_index.get("milestones", {}))
    event_counts = dict(sorted(Counter(event.get("kind", "unknown") for event in events).items()))
    verdict_counts = dict(sorted(Counter(item["overall_verdict"] for item in entrypoints).items()))

    artifact_inventory = _build_artifact_inventory(root, run_index.get("artifacts_index", {}))
    warnings = list(run_index.get("warnings", [])) + list(run_index.get("parse_errors", []))
    if summary:
        warnings.extend(summary.get("warnings", []))
    timeline_rows = _build_timeline_rows(events)
    highlighted_events = [
        row
        for row in timeline_rows
        if row["kind"] in {"milestone_completed", "iter_recorded", "trial_marked", "patch_applied", "diff_written"}
    ]

    return ValidationRunData(
        root=root,
        run_index=run_index,
        summary=summary,
        report_markdown=report_markdown,
        analysis=analysis,
        events=events,
        warnings=warnings,
        entrypoints=entrypoints,
        milestones=milestones,
        event_counts=event_counts,
        verdict_counts=verdict_counts,
        run_metrics=_build_run_metrics(run_index, summary, entrypoints, milestones),
        artifact_inventory=artifact_inventory,
        timeline_rows=timeline_rows,
        highlighted_events=highlighted_events,
        fixer_dispatches=list(run_index.get("fixer_dispatches", [])),
        documented_divergences=list(run_index.get("documented_divergences", [])),
        qualifications=_build_qualifications(summary),
        pipeline_steps=build_pipeline_steps(
            run_index.get("milestones", {}),
            entrypoints,
            summary,
            run_index.get("run", {}).get("status"),
        ),
    )


def _build_run_metrics(
    run_index: dict[str, Any],
    summary: dict[str, Any] | None,
    entrypoints: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
) -> dict[str, Any]:
    run_meta = run_index.get("run", {})
    started_at = _parse_ts(run_meta.get("started_at"))
    completed_at = _parse_ts(run_meta.get("completed_at"))
    duration = None
    if started_at and completed_at:
        duration = completed_at - started_at

    decision = (summary or {}).get("decision", {})
    phase_b_passes = decision.get("phase_b_passes")
    if phase_b_passes is None:
        phase_b_passes = sum(
            1 for ep in entrypoints if (ep.get("overall_verdict") or "").lower() == "passed"
        )

    return {
        "run_id": run_meta.get("id"),
        "status": run_meta.get("status"),
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": int(duration.total_seconds()) if duration else None,
        "connection": run_meta.get("connection"),
        "database": run_meta.get("database"),
        "schema_namespace": run_meta.get("schema_namespace"),
        "entrypoint_count": len(entrypoints),
        "milestones_done": sum(1 for milestone in milestones if milestone["status"] == "done"),
        "milestones_total": len(milestones),
        "manual_review_required": decision.get("manual_review_required", 0),
        "ship_recommendation": decision.get("ship_recommendation"),
        "overall_decision": decision.get("overall") or run_meta.get("status"),
        "non_blocking_qualifications": decision.get("non_blocking_qualifications", []),
        "non_blocking_divergences": decision.get("non_blocking_divergences", 0),
        "blocking_reasons": decision.get("blocking_reasons", []),
        "phase_a_passes": decision.get("phase_a_passes"),
        "phase_b_passes": phase_b_passes,
    }


def _tables_from_phase_dir(root: Path, phase_dir: Path) -> list[dict[str, Any]]:
    """Discover captured sink parquets even when ``_index.json`` is missing."""
    discovered: list[dict[str, Any]] = []
    subdir = phase_dir / "tables"
    if subdir.is_dir():
        for path in sorted(subdir.glob("*.parquet")):
            discovered.append(
                {
                    "name": path.stem,
                    "relative_path": _relative_path(root, path),
                    "row_count": None,
                    "format": "parquet",
                    "capture_kind": "tables",
                }
            )
    return discovered


def _merge_tables_by_name(*table_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for tables in table_lists:
        for table in tables:
            name = table.get("name")
            if not name:
                continue
            if name in by_name:
                merged = dict(by_name[name])
                for key, value in table.items():
                    if value is not None:
                        merged[key] = value
                by_name[name] = merged
            else:
                by_name[name] = dict(table)
    return [by_name[name] for name in sorted(by_name)]


def _tables_from_phase_index(root: Path, index: dict[str, Any] | None, phase_dir: Path) -> list[dict[str, Any]]:
    indexed: list[dict[str, Any]] = []
    if index:
        for table in index.get("tables", []):
            rel_path = table.get("path")
            if not rel_path:
                continue
            parquet_path = phase_dir / rel_path
            indexed.append(
                {
                    "name": table.get("name"),
                    "relative_path": _relative_path(root, parquet_path),
                    "row_count": table.get("row_count"),
                    "format": table.get("format") or "parquet",
                }
            )
    return _merge_tables_by_name(indexed, _tables_from_phase_dir(root, phase_dir))


def _discover_comparison_diffs(
    root: Path,
    phase_b_dir: Path,
    comparison_diffs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Load comparison JSON from disk when ``run_index`` has no diff entries."""
    if comparison_diffs:
        enriched: list[dict[str, Any]] = []
        for diff in comparison_diffs:
            item = dict(diff)
            rel = item.get("relative_path") or item.get("diff_path")
            if rel:
                item["relative_path"] = rel
            if not item.get("payload"):
                diff_path = _resolve_relative(root, rel)
                if diff_path and diff_path.is_file():
                    item["payload"] = _read_json(diff_path)
                else:
                    item["payload"] = None
                    item["missing_on_disk"] = bool(rel)
            enriched.append(item)
        return enriched

    diffs_dir = phase_b_dir / "diffs"
    if not diffs_dir.is_dir():
        return []

    discovered: list[dict[str, Any]] = []
    for diff_file in sorted(diffs_dir.glob("*.json")):
        payload = _read_json(diff_file)
        discovered.append(
            {
                "table": diff_file.stem,
                "diff_path": _relative_path(root, diff_file),
                "relative_path": _relative_path(root, diff_file),
                "verdict": (payload or {}).get("result") or (payload or {}).get("verdict"),
                "payload": payload,
            }
        )
    return discovered


def _build_sink_catalog(
    root: Path,
    entrypoint_id: str,
    trial_dir: Path | None,
    diff_files: list[dict[str, Any]],
    documented_divergences: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    phase_a_dir = root / "results" / "phase_a" / entrypoint_id
    phase_b_dir = trial_dir or (root / "results" / "phase_b" / entrypoint_id)

    phase_a_index = _read_json(phase_a_dir / "_index.json")
    phase_b_index = _read_json(phase_b_dir / "_index.json") if phase_b_dir else None

    phase_a_by_name = {t["name"]: t for t in _tables_from_phase_index(root, phase_a_index, phase_a_dir)}
    phase_b_by_name = {t["name"]: t for t in _tables_from_phase_index(root, phase_b_index, phase_b_dir)}

    diff_by_table = {d.get("table"): d for d in diff_files if d.get("table")}
    div_by_sink: dict[str, list[dict[str, Any]]] = {}
    for div in documented_divergences:
        sink_id = div.get("sink_id") or ""
        div_by_sink.setdefault(sink_id, []).append(div)

    sink_names = sorted(set(phase_a_by_name) | set(phase_b_by_name) | set(diff_by_table))
    catalog: list[dict[str, Any]] = []
    for name in sink_names:
        diff = diff_by_table.get(name, {})
        payload = diff.get("payload") or {}
        divergent_columns = sorted(
            {
                field.get("col")
                for row in payload.get("row_diffs", [])
                for field in row.get("field_diffs", [])
                if field.get("col")
            }
        )
        catalog.append(
            {
                "name": name,
                "phase_a": phase_a_by_name.get(name),
                "phase_b": phase_b_by_name.get(name),
                "diff": diff,
                "diff_result": payload.get("result") or diff.get("verdict"),
                "diff_summary": payload.get("summary"),
                "row_diffs": payload.get("row_diffs", []),
                "tier_results": payload.get("tier_results", {}),
                "documented_divergences": div_by_sink.get(name, []),
                "divergent_columns": divergent_columns,
            }
        )
    return catalog


EXECUTION_PIPELINE_STEPS: list[tuple[str, str, str]] = [
    ("source_baseline", "Source baseline", "Run entrypoints on local Spark + Delta and capture trusted source outputs."),
    ("snowpark_connect", "Snowpark Connect", "Execute migrated code on Snowflake via Snowpark Connect."),
    ("output_comparison", "Output comparison", "Compare Snowpark Connect sinks against the source baseline."),
    ("ship_decision", "Ship decision", "Roll up entrypoint results into a ship recommendation."),
]

MILESTONE_ORDER = [
    "analyzer_survey",
    "entrypoints_selected",
    "analyzer_deep",
    "patches_authored",
    "workload_built",
    "venv_prewarmed",
    "snowflake_provisioned",
    "tests_authored",
]

MILESTONE_DESCRIPTIONS: dict[str, str] = {
    "analyzer_survey": "Scan the migrated Java workload and shortlist candidate entrypoints.",
    "entrypoints_selected": "Lock the entrypoints that will run through validation.",
    "analyzer_deep": "Deep-read each entrypoint to plan mock data, schemas, and expected sinks.",
    "patches_authored": "Author the blueprint patches that rewrite non-Spark I/O/env into native reads.",
    "workload_built": "Make minimal testability edits and compile the migrated workload to a JAR.",
    "venv_prewarmed": "Stage the test kit and warm the sbt/Coursier cache so Phase A starts fast.",
    "snowflake_provisioned": "Provision per-entrypoint golden schemas and stage mock inputs.",
    "tests_authored": "Render the ScalaTest kit spec for each selected entrypoint.",
}


def _execution_step_status(
    step_id: str,
    entrypoints: list[dict[str, Any]],
    summary: dict[str, Any] | None,
    run_status: str | None,
) -> str:
    if step_id == "source_baseline":
        if not entrypoints:
            return "pending"
        ready = {"baseline_produced", "phase_a_skipped", "no_baseline", "passed_no_baseline"}
        return "done" if all((ep.get("phase_a_verdict") or "") in ready for ep in entrypoints) else "pending"
    if step_id == "snowpark_connect":
        if run_status in {"passed", "partial", "blocked"}:
            return "done"
        return "done" if any(ep.get("phase_b_verdict") for ep in entrypoints) else "pending"
    if step_id == "output_comparison":
        return "done" if any(ep.get("comparison", {}).get("verdict") for ep in entrypoints) else "pending"
    if step_id == "ship_decision":
        return "done" if (summary or {}).get("decision", {}).get("overall") else "pending"
    return "pending"


def build_pipeline_steps(
    milestones: dict[str, Any],
    entrypoints: list[dict[str, Any]],
    summary: dict[str, Any] | None,
    run_status: str | None,
) -> list[dict[str, Any]]:
    steps = _build_milestones(milestones)
    for step_id, label, description in EXECUTION_PIPELINE_STEPS:
        steps.append(
            {
                "id": step_id,
                "label": label,
                "description": description,
                "status": _execution_step_status(step_id, entrypoints, summary, run_status),
                "completed_at": None,
            }
        )
    return steps


def _build_milestones(milestones: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in MILESTONE_ORDER:
        if key not in milestones:
            continue
        value = milestones[key]
        seen.add(key)
        items.append(
            {
                "id": key,
                "label": _safe_name(key),
                "description": MILESTONE_DESCRIPTIONS.get(key, ""),
                "status": value.get("status", "unknown"),
                "completed_at": _parse_ts(value.get("completed_at")),
            }
        )
    for key, value in milestones.items():
        if key in seen:
            continue
        items.append(
            {
                "id": key,
                "label": _safe_name(key),
                "description": MILESTONE_DESCRIPTIONS.get(key, ""),
                "status": value.get("status", "unknown"),
                "completed_at": _parse_ts(value.get("completed_at")),
            }
        )
    return items


def _parse_qualification(raw: dict[str, Any]) -> dict[str, Any]:
    detail = raw.get("detail") or ""
    sink_id = ""
    column = ""
    reason = detail
    if ":" in detail:
        location, reason = detail.split(":", 1)
        reason = reason.strip()
        if "." in location:
            sink_id, column = location.rsplit(".", 1)
        else:
            sink_id = location.strip()
    return {
        "trial": raw.get("trial") or "",
        "kind": raw.get("kind") or "documented_divergence",
        "sink_id": sink_id.strip(),
        "column": column.strip(),
        "reason": reason.strip(),
        "detail": detail,
    }


def _build_qualifications(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    decision = (summary or {}).get("decision", {})
    return [_parse_qualification(item) for item in decision.get("non_blocking_qualifications", [])]


def _build_entrypoint(root: Path, entrypoint: dict[str, Any]) -> dict[str, Any]:
    trial_dir = _resolve_relative(root, entrypoint.get("trial_dir"))
    phase_a = entrypoint.get("phase_a", {})
    phase_b = entrypoint.get("phase_b", {})
    comparison = entrypoint.get("comparison", {})

    parquet_files: list[dict[str, Any]] = []
    if trial_dir:
        subdir = trial_dir / "tables"
        for parquet_path in _discover_files(subdir, (".parquet",)):
            parquet_files.append(
                {
                    "label": "captured tables",
                    "path": parquet_path,
                    "relative_path": _relative_path(root, parquet_path),
                }
            )

    phase_b_dir = trial_dir or (root / "results" / "phase_b" / entrypoint.get("id", ""))
    diff_files = _discover_comparison_diffs(root, phase_b_dir, list(comparison.get("diffs", [])))

    manual_review = _read_json(trial_dir / "_manual_review.json") if trial_dir else None
    phase_index = _read_json(trial_dir / "_index.json") if trial_dir else None
    workload_error = _read_text(trial_dir / "workload_error.txt") if trial_dir else None
    parsed_workload_error = parse_workload_error(workload_error)
    phase_a_errors = phase_a.get("errors", [])
    phase_b_errors = phase_b.get("errors", [])
    summary_sentence = _build_entrypoint_summary(
        overall_verdict=entrypoint.get("verdict", {}).get("overall", "unknown"),
        verdict_reason=entrypoint.get("verdict", {}).get("reason", ""),
        phase_a_verdict=phase_a.get("verdict"),
        phase_b_verdict=phase_b.get("verdict"),
        workload_error=workload_error,
        manual_review=manual_review,
        comparison_verdict=comparison.get("verdict"),
    )

    documented_divergences = comparison.get("documented_divergences", [])
    sink_catalog = _build_sink_catalog(root, entrypoint.get("id", ""), trial_dir, diff_files, documented_divergences)
    phase_a_dir = root / "results" / "phase_a" / entrypoint.get("id", "")
    phase_b_dir = trial_dir or (root / "results" / "phase_b" / entrypoint.get("id", ""))
    phase_a_index = _read_json(phase_a_dir / "_index.json")
    phase_b_index = _read_json(phase_b_dir / "_index.json") if phase_b_dir else None

    ep_id = entrypoint.get("id", "")
    source_path = entrypoint.get("source_path")
    short_name = entrypoint_short_name(ep_id, source_path)

    return {
        "id": ep_id,
        "short_name": short_name,
        "source_path": source_path,
        "overall_verdict": entrypoint.get("verdict", {}).get("overall", "unknown"),
        "reason": entrypoint.get("verdict", {}).get("reason", ""),
        "trial_dir": _relative_path(root, trial_dir) if trial_dir else entrypoint.get("trial_dir"),
        "phase_a": phase_a,
        "phase_b": phase_b,
        "comparison": comparison,
        "documented_divergences": documented_divergences,
        "sink_catalog": sink_catalog,
        "phase_a_tables": _tables_from_phase_index(root, phase_a_index, phase_a_dir),
        "phase_b_tables": _tables_from_phase_index(root, phase_b_index, phase_b_dir),
        "diff_files": diff_files,
        "manual_review": manual_review,
        "phase_index": phase_index,
        "workload_error": workload_error,
        "workload_error_parsed": workload_error_to_dict(parsed_workload_error),
        "workload_error_preview": _line_preview(workload_error),
        "workload_error_excerpt": format_workload_error_summary(
            parsed_workload_error, _truncate_text(workload_error)
        ),
        "parquet_files": parquet_files,
        "patches": [*phase_a.get("patches_applied", []), *phase_b.get("patches_applied", [])],
        "migration_fix_commits": phase_b.get("migration_fix_commits", []),
        "phase_a_iters": phase_a.get("iters", 0),
        "phase_b_iters": phase_b.get("iters", 0),
        "phase_a_verdict": phase_a.get("verdict"),
        "phase_b_verdict": phase_b.get("verdict"),
        "phase_a_error_count": len(phase_a_errors),
        "phase_b_error_count": len(phase_b_errors),
        "diff_count": len(diff_files),
        "divergence_count": len(comparison.get("documented_divergences", [])),
        "has_manual_review": bool(manual_review),
        "summary_sentence": summary_sentence,
    }


def _build_artifact_inventory(root: Path, artifacts_index: dict[str, Any]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for key, value in artifacts_index.items():
        if not value:
            continue
        if isinstance(value, str):
            path = root / value
            inventory.append(
                {
                    "group": key,
                    "label": value,
                    "relative_path": value,
                    "payload": _load_artifact_payload(path),
                }
            )
            continue
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    path = root / item
                    inventory.append(
                        {
                            "group": key,
                            "label": item,
                            "relative_path": item,
                            "payload": _load_artifact_payload(path),
                        }
                    )
                elif isinstance(item, dict):
                    label = item.get("trial_id") or key
                    inventory.append(
                        {
                            "group": key,
                            "label": label,
                            "relative_path": None,
                            "payload": item,
                        }
                    )
    return inventory


def _load_artifact_payload(path: Path) -> Any:
    if path.suffix.lower() == ".json":
        return _read_json(path)
    if path.suffix.lower() in {".md", ".txt", ".py", ".sql", ".yaml", ".yml", ".csv"}:
        return _read_text(path)
    return {"path": str(path), "size_bytes": path.stat().st_size if path.exists() else None}


def _build_entrypoint_summary(
    overall_verdict: str,
    verdict_reason: str,
    phase_a_verdict: str | None,
    phase_b_verdict: str | None,
    workload_error: str | None,
    manual_review: dict[str, Any] | None,
    comparison_verdict: str | None,
) -> str:
    if verdict_reason:
        return _truncate_text(verdict_reason, limit=220)
    if manual_review and manual_review.get("reason"):
        return f"Manual review required because {manual_review['reason'].replace('_', ' ')}."
    if workload_error:
        return format_workload_error_summary(parse_workload_error(workload_error), _truncate_text(workload_error, limit=220))
    parts = [part for part in [phase_a_verdict, phase_b_verdict, comparison_verdict] if part]
    if parts:
        return f"Phase flow: {' -> '.join(parts)}. Overall verdict: {overall_verdict}."
    return f"Overall verdict: {overall_verdict}."


def _build_timeline_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        row = {
            "ts": event.get("ts"),
            "kind": event.get("kind", "unknown"),
            "trial": event.get("trial_id"),
            "phase": event.get("phase"),
            "iter": event.get("iter"),
            "status": event.get("status"),
            "milestone": event.get("milestone"),
            "table": event.get("table"),
            "file": event.get("file"),
            "reason": _truncate_text(event.get("reason"), limit=160),
            "raw": event,
        }
        row["headline"] = _build_timeline_headline(row)
        rows.append(row)
    return rows


def _build_timeline_headline(row: dict[str, Any]) -> str:
    kind = row.get("kind")
    if kind == "milestone_completed":
        return f"Milestone completed: {row.get('milestone')}"
    if kind == "iter_recorded":
        return f"{row.get('trial')}: {row.get('phase')} iter {row.get('iter')}"
    if kind == "trial_marked":
        return f"{row.get('trial')}: marked {row.get('status')}"
    if kind == "patch_applied":
        return f"{row.get('trial')}: patch applied to {row.get('file')}"
    if kind == "diff_written":
        return f"{row.get('trial')}: diff written for {row.get('table')}"
    if kind == "capture_completed":
        return f"{row.get('trial')}: capture completed for {row.get('phase')}"
    return kind or "event"


def load_parquet_artifact(root_path: str | Path, relative_path: str) -> dict[str, Any]:
    root = Path(root_path).expanduser().resolve()
    target = (root / relative_path).resolve()
    if not target.is_file() or root not in [target, *target.parents]:
        return {"error": f"File not found: {relative_path}", "rows": 0, "preview": None}
    try:
        return _load_parquet_preview(target)
    except Exception as exc:  # noqa: BLE001 — surface parquet read issues in the UI
        return {"error": f"Could not read {relative_path}: {exc}", "rows": 0, "preview": None}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
