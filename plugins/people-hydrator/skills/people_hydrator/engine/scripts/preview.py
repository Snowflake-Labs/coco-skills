#!/usr/bin/env python3
"""Output preview for one skill: the real numbers it would show, plus how they are defined.

Aggregates only (counts, rates); breakdown groups smaller than MIN_GROUP are
suppressed. Uses the same metric SQL as the validator, so the preview, the checks
and the hydrated skill agree by construction.

  preview.py --workspace W --connection C --skill S [--skill S2]
Writes W/previews/<skill>.json:
  {skill, signature, profile_version, metrics: [{id, label, value, how}],
   breakdown: {title, rows: [{group, value}], suppressed}, definitions: [..],
   tables: [..], errors: [..], generated_at}
`signature` changes whenever a binding or setting the skill uses changes; the app
compares it to mark a preview out of date.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import check_bindings as CB  # noqa: E402
import metrics_sql as M  # noqa: E402
from _sql import Runner, SqlError, make_runner, to_number  # noqa: E402

MIN_GROUP = 5
LABELS = {
    "active_regular_hc": ("Headcount at the latest month-end", "people"),
    "avg_hc": ("Average monthly headcount (12 months)", "people"),
    "exits_window": ("Exits in the last 12 months", "exits"),
    "attrition_rate": ("Attrition rate (12 months)", "%"),
    "voluntary_attrition_rate": ("Voluntary attrition rate (12 months)", "%"),
    "regrettable_share": ("Regrettable share of exits", "%"),
    "avg_span": ("Average direct reports per manager", ""),
    "over_spanned_managers": ("Managers above the span threshold", "managers"),
    "under_spanned_managers": ("Managers below the span threshold", "managers"),
    "hires_window": ("Hires in the last 12 months", "hires"),
    "hires_month": ("Hires in the latest month", "hires"),
    "exits_month": ("Exits in the latest month", "exits"),
    "ytd_involuntary_rate": ("Involuntary attrition, fiscal year to date", "%"),
    "open_reqs": ("Open requisitions", "reqs"),
}
ORG_FIELDS = ("org_name", "department", "org", "org_group", "org_label", "management_level", "location")
SNAP_FOR = {"attrition_insights": "people.attrition.snapshot_monthly", "good_morning_chro": "people.headcount.active_employees",
            "org_health_report": "people.org_health.employee_snapshot"}


def load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def tuning_of(ws: Path) -> dict[str, Any]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (ws / "tuning").glob("*.json")}


def signature(ws: Path, skill: str) -> str:
    """Hash of everything the skill's numbers depend on (bindings of its needs + its settings)."""
    cat = load(SCRIPTS.parents[1] / "catalog" / "people.json")
    keys = sorted({n["key"] for s in cat["skills"] if s["name"] == skill for n in s["data_needs"]}
                  | set(SNAP_FOR.values()) | {"people.headcount.employee_snapshot", "people.exits.event_records"})
    needs = load(ws / "bindings.json", {}).get("needs", {})
    # only the settings this skill reads, resolved the way the SQL resolves them, so adding or tuning
    # another skill doesn't make this one look changed
    tids = sorted({t["id"] for s in cat["skills"] if s["name"] == skill for t in s.get("tunables", [])})
    tuning = tuning_of(ws)
    tun = {tid: M.tunable(tuning, tid, None) for tid in tids}
    body = {"needs": {k: {"fqn": needs.get(k, {}).get("fqn"), "row_filter": needs.get(k, {}).get("row_filter"),
                          "fields": {f: {"column": (v or {}).get("column"), "expression": (v or {}).get("expression")}
                                     for f, v in (needs.get(k, {}).get("fields") or {}).items()}}
                      for k in keys},
            "tuning": tun}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:12]


def definitions(skill: str, tuning: dict[str, Any], bindings: dict[str, Any]) -> list[str]:
    t = lambda tid, d=None: M.tunable(tuning, tid, d)  # noqa: E731
    out = []
    types, active = t("regular_employee_types"), t("active_status_values", ["Active"])
    out.append("Headcount counts employees with status " + ", ".join(map(str, active))
               + (f" and type {', '.join(map(str, types))}" if types else ""))
    snap = bindings["needs"].get("people.headcount.employee_snapshot") or {}
    if snap.get("row_filter"):
        rf = snap["row_filter"]
        out.append(f"Only {', '.join(map(str, rf['values']))} rows of {snap['fqn'].rsplit('.', 1)[1]} are counted "
                   f"({rf['column']})")
    if skill in {"attrition_insights", "org_health_report", "good_morning_chro"}:
        method = next((x["tunables"]["attrition_rate_method"].get("method") for x in tuning.values()
                       if x.get("tunables", {}).get("attrition_rate_method", {}).get("method")), None) or {}
        out.append(f"Attrition rate = exits in the last {t('attrition_window_months', 12)} months / "
                   + {"start_end": "average of headcount at period start and end",
                      "average": "average month-end headcount", "start": "headcount at period start",
                      "end": "headcount at period end"}[method.get("denominator", "start_end")]
                   + (", annualized" if method.get("annualized") else ""))
        out.append("Voluntary exits: " + ", ".join(map(str, t("voluntary_values", ["Voluntary"]))))
    if t("fiscal_year_start_month"):
        out.append(f"Fiscal year starts in month {t('fiscal_year_start_month')}")
    return out


def breakdown(run: Runner, skill: str, bindings: dict[str, Any], tuning: dict[str, Any],
              index: dict[str, Any] | None = None) -> dict[str, Any] | None:
    index = index or {}
    if skill in {"attrition_insights", "org_health_report", "good_morning_chro"}:
        ex = CB.enriched_need("people.exits.event_records", bindings)
        snap = CB.enriched_need(SNAP_FOR.get(skill, "people.headcount.employee_snapshot"), bindings)
        cat = ex.col("termination_category")
        if ex.has("termination_date") and cat and snap.has("snapshot_date"):
            months = int(M.tunable(tuning, "attrition_window_months", 12))
            t = M.exit_day(ex, tuning)  # same exit day and population as the exits total
            preds = [f"{t} > {M.period_start_sql(snap, months)}", f"{t} <= {M.as_of_sql(snap)}"]
            same_people = M.included_exits(ex, snap, tuning)
            if same_people:
                preds.append(same_people)
            sql = (f"select {cat}::string as grp, count(*) as n from {ex.table}"
                   f"{ex.where(preds)} "
                   "group by 1 order by 2 desc limit 12")
            return rows_out("Exits in the last 12 months by type", run(sql))
    snap = CB.enriched_need("people.headcount.employee_snapshot", bindings)
    base = M.active_regular_hc(snap, tuning)
    if not base:
        return None
    org_sql, org = org_column(snap, index)
    if not org_sql:
        return None
    sql = base.replace(f"select count(distinct {snap.col('employee_id')}) as value",
                       f"select {org_sql}::string as grp, count(distinct {snap.col('employee_id')}) as n", 1)
    return rows_out(f"Headcount by {org}", run(sql + " group by 1 order by 2 desc limit 12"))


def org_column(snap: M.Need, index: dict[str, Any]) -> tuple[str | None, str | None]:
    """A bound org field, else an org-like code column on the same table (display only)."""
    for f in ORG_FIELDS[:5]:
        if snap.col(f):
            return snap.col(f), f.replace("_", " ")
    cols = index.get("objects", {}).get(snap.fqn, {}).get("columns", {})
    for name in ("DEPARTMENT", "ORG_NAME", "ORGANIZATION", "DIVISION", "BUSINESS_UNIT"):
        if cols.get(name, {}).get("is_code"):
            from _sql import quote_ident
            return quote_ident(name), name.lower().replace("_", " ")
    for f in ORG_FIELDS[5:]:
        if snap.col(f):
            return snap.col(f), f.replace("_", " ")
    return None, None


def rows_out(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    kept = [{"group": r["GRP"] if r["GRP"] is not None else "(blank)", "value": int(r["N"])}
            for r in rows if int(r["N"]) >= MIN_GROUP]
    return {"title": title, "rows": kept, "suppressed": sum(1 for r in rows if int(r["N"]) < MIN_GROUP),
            "min_group": MIN_GROUP}


def preview(ws: Path, run: Runner, skill: str) -> dict[str, Any]:
    bindings = load(ws / "bindings.json")
    tuning = tuning_of(ws)
    metrics, errors = [], []
    for mid, sql in CB.skill_metrics(skill, bindings, tuning).items():
        try:
            row = CB.scalar(run, sql)
        except SqlError as exc:
            errors.append(f"{LABELS.get(mid, (mid,))[0]}: {str(exc)[:160]}")
            continue
        label, unit = LABELS.get(mid, (mid.replace("_", " "), ""))
        metrics.append({"id": mid, "label": label, "unit": unit, "value": to_number(row.get("VALUE"))})
    try:
        bd = breakdown(run, skill, bindings, tuning, load(ws / "data_index.json", {}))
    except SqlError as exc:
        bd, errors = None, errors + [f"breakdown: {str(exc)[:160]}"]
    as_of = None
    snap = CB.enriched_need(SNAP_FOR.get(skill, "people.headcount.employee_snapshot"), bindings)
    if snap.has("snapshot_date"):
        try:
            as_of = CB.scalar(run, f"select {M.as_of_sql(snap)}::string as d").get("D")
        except SqlError:
            pass
    used = sorted({b["fqn"] for b in bindings["needs"].values()
                   if skill in b.get("skills", []) and b.get("fqn") and b.get("status") != "unresolved"})
    out = {"skill": skill, "signature": signature(ws, skill),
           "profile_version": (load(ws / "profile.json", {}) or {}).get("version"),
           "as_of": as_of, "metrics": metrics, "breakdown": bd, "definitions": definitions(skill, tuning, bindings),
           "tables": used, "errors": errors,
           "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    (ws / "previews").mkdir(exist_ok=True)
    (ws / "previews" / f"{skill}.json").write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--connection", required=True)
    p.add_argument("--skill", action="append", required=True)
    a = p.parse_args()
    run = make_runner(a.connection, 180)
    for i, s in enumerate(a.skill, 1):
        print(json.dumps({"event": "preview", "skill": s, "done": i - 1, "total": len(a.skill)}), flush=True)
        res = preview(a.workspace.resolve(), run, s)
        print(json.dumps({"event": "preview_done", "skill": s, "metrics": len(res["metrics"]),
                          "errors": res["errors"], "done": i, "total": len(a.skill)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
