"""Data-edge resolution coverage gate.

Checks that the LLM agent analyzed every data-relevant file in the workload.
The agent must report, inside ``llm_resolved_data_edges``:

  ``analyzed_files``
      Workload-relative paths of every file the agent read and found to
      have data I/O.

  ``excluded_files``
      Workload-relative paths of files the agent explicitly checked and
      found to have **no** data I/O (pure utility code, empty files, etc.).

This gate scans *workload_dir* for every ``.py`` / ``.sql`` / ``.ipynb`` file
and verifies that each appears in ``analyzed_files`` **or** ``excluded_files``.
An uncovered file is a gap: the agent may have missed data edges inside it.

Exit codes
----------
0   Full coverage — every file in *workload_dir* is accounted for.
    Prints a JSON summary with counts.
2   Coverage gap — one or more files were neither analyzed nor excluded.
    Prints JSON: ``{"status": "fail", "gap_count": N, "gaps": [paths], ...}``.
    Pass the ``gaps`` list back to the resolver as focused input for another
    round.
3   IO / parse error — do **not** retry; escalate to the user immediately.
    Prints JSON: ``{"status": "error", "message": "..."}``.

Usage
-----
::

    # via uv (from the snowpark-connect project root):
    uv run --project . python scripts/assessment/check_data_edges_gate.py \\
        path/to/Reports/AssessmentIR.json \\
        path/to/Output/              # workload_dir

    # plain python (no project imports required):
    python check_data_edges_gate.py path/to/AssessmentIR.json path/to/Output/
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import edge_reconcile  # noqa: E402

_DATA_SUFFIXES = {".py", ".sql", ".ipynb", ".scala", ".java"}

# Published JSON Schema for the llm_resolved_data_edges block — generated from
# the pydantic models (scripts/assessment/assess_ir.py) and kept in sync by
# tests/test_schema_export.py.  Lives with the agent references so the resolver
# agent and this gate validate against one artifact.
_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "migrate-pyspark-to-snowpark-connect"
    / "references"
    / "llm_resolved_data_edges.schema.json"
)


def _schema_errors(llm: dict) -> list[str]:
    """Validate the llm block against the JSON Schema; return error messages.

    Degrades gracefully to ``[]`` (skip) if ``jsonschema`` or the schema file is
    unavailable — structural validation is a bonus check, and pydantic still
    enforces the same contract when the renderer loads the IR.
    """
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []
    try:
        schema = json.loads(_SCHEMA_PATH.read_text())
    except OSError:
        return []
    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(llm), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{loc}: {err.message}")
    return errors


def _emit_reconcile_fail(data_leaks: list[dict], import_leaks: list[dict]) -> int:
    """Print a reconciliation-only failure (used when workload_dir is absent).

    Coverage cannot be checked without the workload dir, but reconciliation is
    IR-only, so leaked edges/imports still fail the gate with exit 2.
    """
    output: dict = {"status": "fail"}
    if data_leaks:
        output["data_leak_count"] = len(data_leaks)
        output["data_leaks"] = data_leaks
        output["data_leak_message"] = (
            f"{len(data_leaks)} unresolved_data_edges call site(s) were neither resolved "
            "nor confirmed unresolvable. Add a resolved_unresolved edge OR an "
            "unresolvable_edges entry reusing the EXACT (file, line, kind)."
        )
    if import_leaks:
        output["import_leak_count"] = len(import_leaks)
        output["import_leaks"] = import_leaks
        output["import_leak_message"] = (
            f"{len(import_leaks)} unresolved_dynamic_imports site(s) were not accounted for. "
            "Add a resolved_imports entry reusing the EXACT (file, line, kind)."
        )
    print(json.dumps(output))
    return 2


def _scan_workload(workload_dir: Path) -> set[str]:
    """Return workload-relative paths of every data-relevant file."""
    results: set[str] = set()
    for dirpath, _dirs, files in os.walk(workload_dir):
        for fname in files:
            if Path(fname).suffix in _DATA_SUFFIXES:
                abs_path = Path(dirpath) / fname
                rel = abs_path.relative_to(workload_dir).as_posix()
                results.add(rel)
    return results


def run(ir_path: Path, workload_dir: Path | None = None) -> int:
    try:
        ir = json.loads(ir_path.read_text())
    except FileNotFoundError:
        print(json.dumps({"status": "error", "message": f"IR not found: {ir_path}"}))
        return 3
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "error", "message": f"IR parse error: {exc}"}))
        return 3
    except OSError as exc:
        print(json.dumps({"status": "error", "message": f"IO error: {exc}"}))
        return 3

    llm = ir.get("llm_resolved_data_edges")
    if llm is None:
        print(json.dumps({
            "status": "error",
            "message": (
                "llm_resolved_data_edges is absent from the IR. "
                "Run agents/data_edge_resolver.md first."
            ),
        }))
        return 3

    # Structural validation against the published JSON Schema (deterministic).
    # A schema violation is a malformed resolver output the agent can fix, so
    # it surfaces as a fixable exit-2 failure category (schema_errors).
    schema_errors = _schema_errors(llm)
    if schema_errors:
        print(json.dumps({
            "status": "fail",
            "schema_error_count": len(schema_errors),
            "schema_errors": schema_errors,
            "schema_error_message": (
                f"{len(schema_errors)} entr(y/ies) in llm_resolved_data_edges violate "
                "references/llm_resolved_data_edges.schema.json (missing required field, "
                "wrong type, or bad enum). Fix each and re-write the block."
            ),
        }))
        return 2

    analyzed: set[str] = set(llm.get("analyzed_files") or [])
    excluded: set[str] = set(llm.get("excluded_files") or [])
    covered = analyzed | excluded

    edges = llm.get("edges", [])
    resolved_count = sum(1 for e in edges if e.get("source") == "resolved_unresolved")
    discovered_count = sum(1 for e in edges if e.get("source") == "newly_discovered")
    unresolvable_count = len(llm.get("unresolvable_edges", []))

    # ------------------------------------------------------------------
    # Reconciliation.  An LLM run is only complete when every edge the static
    # scanner left unresolved is *accounted for* — resolved or confirmed
    # unresolvable.  We delegate the (file, line, kind) matching to
    # edge_reconcile — the SAME helper the renderer uses to compute the
    # "still unresolved" rows — so the gate's leaks and the report's display
    # cannot drift.  Baseline = the IR's static unresolved lists (never mutated
    # by the render, which dumps the baseline and reduces only for display).
    # ------------------------------------------------------------------
    orig_data_unresolved = ir.get("unresolved_data_edges") or []
    orig_import_unresolved = ir.get("unresolved_dynamic_imports") or []

    data_leaks = [
        {"file": e.get("file"), "line": e.get("line"), "kind": e.get("kind"),
         "call_expr": e.get("call_expr", "")}
        for e in edge_reconcile.data_leaks(
            [e for e in orig_data_unresolved if isinstance(e, dict)], llm
        )
    ]
    import_leaks = [
        {"file": e.get("file"), "line": e.get("line"), "kind": e.get("kind"),
         "raw_expr": e.get("raw_expr", "")}
        for e in edge_reconcile.import_leaks(
            [e for e in orig_import_unresolved if isinstance(e, dict)], llm
        )
    ]

    imports_resolved_count = len(edge_reconcile.resolved_import_keys(llm))
    imports_unresolvable_count = len(edge_reconcile.unresolvable_import_reasons(llm))

    if workload_dir is None:
        # No workload directory provided — only verify the IR is structurally valid.
        if not analyzed and not excluded:
            print(json.dumps({
                "status": "error",
                "message": (
                    "llm_resolved_data_edges.analyzed_files and excluded_files are both "
                    "absent or empty.  Pass workload_dir so the gate can check coverage, "
                    "or ensure the resolver populates these lists."
                ),
            }))
            return 3
        # Reconciliation is IR-only (no workload_dir needed): even in the
        # structural-check path, leaked unresolved edges/imports are a failure.
        if data_leaks or import_leaks:
            return _emit_reconcile_fail(data_leaks, import_leaks)
        print(json.dumps({
            "status": "pass",
            "analyzed_count": len(analyzed),
            "excluded_count": len(excluded),
            "resolved_count": resolved_count,
            "unresolvable_count": unresolvable_count,
            "newly_discovered_count": discovered_count,
            "imports_resolved_count": imports_resolved_count,
            "imports_unresolvable_count": imports_unresolvable_count,
            "data_edges_accounted": f"{len(orig_data_unresolved)}/{len(orig_data_unresolved)}",
            "imports_accounted": f"{len(orig_import_unresolved)}/{len(orig_import_unresolved)}",
            "note": "workload_dir not provided — coverage check skipped",
        }))
        return 0

    if not workload_dir.is_dir():
        print(json.dumps({
            "status": "error",
            "message": f"workload_dir not found or not a directory: {workload_dir}",
        }))
        return 3

    workload_files = _scan_workload(workload_dir)
    gaps = sorted(workload_files - covered)

    # Edge-density check: every analyzed file must have ≥1 edge or unresolvable entry.
    # A file in analyzed_files with zero edges means the resolver claimed to read it
    # but found no data I/O — either it missed the I/O or the file should be excluded.
    files_with_edges: set[str] = set()
    for e in edges:
        files_with_edges.add(e.get("file", ""))
    for u in llm.get("unresolvable_edges", []):
        if isinstance(u, dict):
            files_with_edges.add(u.get("file", ""))
    edge_gaps = sorted(f for f in analyzed if f not in files_with_edges)

    all_gaps = gaps + edge_gaps
    if not all_gaps and not data_leaks and not import_leaks:
        n_data = len(orig_data_unresolved)
        n_imp = len(orig_import_unresolved)
        print(json.dumps({
            "status": "pass",
            "total_files": len(workload_files),
            "analyzed_count": len(analyzed),
            "excluded_count": len(excluded),
            "resolved_count": resolved_count,
            "unresolvable_count": unresolvable_count,
            "newly_discovered_count": discovered_count,
            "imports_resolved_count": imports_resolved_count,
            "imports_unresolvable_count": imports_unresolvable_count,
            # Reliability metrics: how much of the static scanner's unresolved
            # backlog the LLM actually accounted for (resolved OR confirmed
            # unresolvable).  A clean run reads N/N on both.
            "data_edges_accounted": f"{n_data}/{n_data}",
            "imports_accounted": f"{n_imp}/{n_imp}",
        }))
        return 0

    output: dict = {"status": "fail"}
    if gaps:
        output["gap_count"] = len(gaps)
        output["gaps"] = gaps
        output["gap_message"] = (
            f"{len(gaps)} file(s) in workload_dir were not analyzed or excluded. "
            "Re-run agents/data_edge_resolver.md with these specific files as focused input."
        )
    if edge_gaps:
        output["edge_gap_count"] = len(edge_gaps)
        output["edge_gaps"] = edge_gaps
        output["edge_gap_message"] = (
            f"{len(edge_gaps)} analyzed file(s) have zero edges and zero unresolvable entries. "
            "SQL files executed by Python must have their OWN edges (file = sql_file_path). "
            "Re-run agents/data_edge_resolver.md with these specific files as focused input."
        )
    if data_leaks:
        n_data = len(orig_data_unresolved)
        output["data_leak_count"] = len(data_leaks)
        output["data_leaks"] = data_leaks
        output["data_edges_accounted"] = f"{n_data - len(data_leaks)}/{n_data}"
        output["data_leak_message"] = (
            f"{len(data_leaks)} unresolved_data_edges call site(s) were neither resolved "
            "nor confirmed unresolvable by the LLM. For each, add an edge with "
            "source='resolved_unresolved' OR an unresolvable_edges entry — reusing the "
            "EXACT (file, line, kind) shown here, or reconciliation cannot pair them."
        )
    if import_leaks:
        n_imp = len(orig_import_unresolved)
        output["import_leak_count"] = len(import_leaks)
        output["import_leaks"] = import_leaks
        output["imports_accounted"] = f"{n_imp - len(import_leaks)}/{n_imp}"
        output["import_leak_message"] = (
            f"{len(import_leaks)} unresolved_dynamic_imports site(s) were not accounted for. "
            "For each, add a resolved_imports entry — with resolved_targets set, or "
            "resolution_type='unresolvable' + why_unresolvable — reusing the EXACT "
            "(file, line, kind) shown here."
        )
    print(json.dumps(output))
    return 2


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(
            json.dumps({
                "status": "error",
                "message": "Usage: check_data_edges_gate.py <AssessmentIR.json> [workload_dir]",
            }),
            file=sys.stderr,
        )
        return 3
    ir_path = Path(args[0])
    workload_dir = Path(args[1]) if len(args) > 1 else None
    return run(ir_path, workload_dir)


if __name__ == "__main__":
    raise SystemExit(main())
