"""Single source of truth for reconciling the static-scan unresolved baseline
against the LLM resolver's output.

Both the coverage gate (``check_data_edges_gate.py``) and the renderer
(``render_assessment.py``) must agree on exactly which unresolved edges/imports
the LLM accounted for — otherwise the gate can pass ``N/N accounted`` while the
report still shows unresolved rows (baseline drift).  They therefore both call
the helpers here rather than re-implementing the ``(file, line, kind)`` matching
each on their own.

The helpers are tolerant of their input shape: the gate reads raw JSON (dicts),
the renderer holds pydantic models.  :func:`_triple` handles both.

Reconciliation rule (identical for gate and render):

* A **data edge** in the baseline is *accounted for* when its ``(file, line,
  kind)`` appears as a ``resolved_unresolved`` edge (any ``resolution_type`` —
  ``literal_found`` / ``traced`` / ``inferred`` all count, and all are drawn)
  **or** as an ``unresolvable_edges`` entry.  What remains is a leak (gate) /
  a still-unresolved row (render) — the same set.
* A **dynamic import** in the baseline is *accounted for* when its key appears
  in ``resolved_imports`` (resolved to targets, or confirmed unresolvable).
  For display the confirmed-unresolvable ones are *kept* (with the LLM reason
  swapped in) while resolved ones drop out; for the gate both count as
  accounted.  Both views derive from the same key sets here.
"""
from __future__ import annotations

from typing import Any


def _triple(x: Any) -> tuple:
    """Return the ``(file, line, kind)`` identity of an edge/import.

    Accepts a dict (raw IR JSON, used by the gate) or an object with
    ``file``/``line``/``kind`` attributes (pydantic models, used by the render).
    """
    if isinstance(x, dict):
        return (x.get("file"), x.get("line"), x.get("kind"))
    return (getattr(x, "file", None), getattr(x, "line", None), getattr(x, "kind", None))


def _iter(obj: Any, name: str) -> list:
    """Read attribute/key ``name`` off a model-or-dict, defaulting to []."""
    if isinstance(obj, dict):
        return obj.get(name) or []
    return getattr(obj, name, None) or []


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# --- data edges ---------------------------------------------------------------

def accounted_data_keys(llm: Any) -> set[tuple]:
    """``(file, line, kind)`` of every baseline data edge the LLM accounted for:
    resolved (``source == "resolved_unresolved"``) or confirmed unresolvable."""
    keys: set[tuple] = set()
    for e in _iter(llm, "edges"):
        if _get(e, "source") == "resolved_unresolved":
            keys.add(_triple(e))
    for u in _iter(llm, "unresolvable_edges"):
        keys.add(_triple(u))
    return keys


def remaining_data_edges(baseline: list, llm: Any) -> list:
    """Baseline data edges the LLM did NOT account for.

    This is simultaneously the gate's *leak* set and the report's
    *still-unresolved read/write* list — one computation, so they can't drift.
    Returns the same element type as ``baseline`` (dicts or models).
    """
    acc = accounted_data_keys(llm)
    return [e for e in baseline if _triple(e) not in acc]


# --- dynamic imports ----------------------------------------------------------

def resolved_import_keys(llm: Any) -> set[tuple]:
    """Keys of dynamic imports the LLM resolved to at least one target file."""
    return {
        _triple(i) for i in _iter(llm, "resolved_imports")
        if _get(i, "resolved_targets")
    }


def unresolvable_import_reasons(llm: Any) -> dict[tuple, str]:
    """``{key: why_unresolvable}`` for imports the LLM confirmed unresolvable."""
    return {
        _triple(i): (_get(i, "why_unresolvable", "") or "")
        for i in _iter(llm, "resolved_imports")
        if _get(i, "resolution_type") == "unresolvable"
    }


def accounted_import_keys(llm: Any) -> set[tuple]:
    """Keys of dynamic imports the LLM accounted for — resolved OR confirmed
    unresolvable.  Used by the gate: anything in the baseline NOT here leaks."""
    return resolved_import_keys(llm) | set(unresolvable_import_reasons(llm))


def remaining_dynamic_imports(baseline: list, llm: Any) -> list:
    """Dynamic imports to DISPLAY after the LLM pass.

    Drops imports the LLM resolved to a target (no longer a blind spot); keeps
    the ones it confirmed unresolvable, swapping in the LLM's reason so the row
    reflects the deeper analysis; keeps any un-accounted leak with its original
    reason.  Mutates ``reason`` on kept model entries when a better one exists.
    """
    resolved = resolved_import_keys(llm)
    reasons = unresolvable_import_reasons(llm)
    kept = []
    for imp in baseline:
        key = _triple(imp)
        if key in resolved:
            continue
        why = reasons.get(key)
        if why:
            if isinstance(imp, dict):
                imp["reason"] = f"LLM: {why}"
            else:
                imp.reason = f"LLM: {why}"
        kept.append(imp)
    return kept


# --- gate leak reporting ------------------------------------------------------

def data_leaks(baseline: list, llm: Any) -> list:
    """Baseline data edges neither resolved nor confirmed unresolvable."""
    return remaining_data_edges(baseline, llm)


def import_leaks(baseline: list, llm: Any) -> list:
    """Baseline dynamic imports neither resolved nor confirmed unresolvable."""
    acc = accounted_import_keys(llm)
    return [i for i in baseline if _triple(i) not in acc]
