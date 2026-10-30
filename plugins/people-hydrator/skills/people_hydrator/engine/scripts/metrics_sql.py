#!/usr/bin/env python3
"""Canonical People metric SQL built from v2 bindings + tuning. Stdlib only.

Single definition of each metric, shared by the validator (check_bindings.py)
and the previews, so validated numbers and preview numbers
agree by construction. Every query returns aggregates only.
"""

from __future__ import annotations

from typing import Any
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts, however this file is run

from _sql import quote_fqn, quote_ident


def lit_list(values: list[Any]) -> str:
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)


class Need:
    """Resolved view of one bound data need."""

    def __init__(self, key: str, binding: dict[str, Any]):
        self.key, self.b = key, binding
        self.fqn = binding.get("fqn")
        self.ok = bool(self.fqn) and binding.get("status") != "unresolved" and binding.get("kind") != "derived"

    def col(self, field: str) -> str | None:
        f = (self.b.get("fields") or {}).get(field) or {}
        if f.get("expression"):
            return f"({f['expression']})"
        return quote_ident(f["column"]) if f.get("column") else None

    def has(self, *fields: str) -> bool:
        return self.ok and all(self.col(f) for f in fields)

    def where(self, extra: list[str] | None = None, apply_filter: bool = True) -> str:
        preds = list(extra or [])
        rf = self.b.get("row_filter")
        if apply_filter and rf:
            preds.append(f"{quote_ident(rf['column'])} in ({lit_list(rf['values'])})")
        return (" where " + " and ".join(preds)) if preds else ""

    @property
    def table(self) -> str:
        return quote_fqn(self.fqn)


def tunable(tuning: dict[str, Any], tid: str, default: Any) -> Any:
    for skill in tuning.values():
        t = skill.get("tunables", {}).get(tid)
        if t is not None and t.get("value") not in (None, [], ""):
            return t["value"]
    return default


def as_of_sql(snap: Need) -> str:
    """Latest month-end snapshot date (falls back to latest date)."""
    d = snap.col("snapshot_date")
    return (f"(select coalesce(max(iff({d} = last_day({d}), {d}, null)), max({d})) "
            f"from {snap.table}{snap.where()})")


def active_regular_hc(snap: Need, tuning: dict[str, Any], apply_filter: bool = True,
                      as_of: str | None = None) -> str | None:
    if not snap.has("snapshot_date", "employee_id"):
        return None
    preds = [f"{snap.col('snapshot_date')} = {as_of or as_of_sql(snap)}"]
    status_col = snap.col("employment_status") or snap.col("is_active")
    active_values = tunable(tuning, "active_status_values", ["Active"])
    if snap.col("employment_status"):
        preds.append(f"{status_col} in ({lit_list(active_values)})")
    elif status_col:
        preds.append(f"{status_col} = true")
    types = tunable(tuning, "regular_employee_types", None)
    if types and snap.col("employee_type"):
        preds.append(f"{snap.col('employee_type')} in ({lit_list(types)})")
    return (f"select count(distinct {snap.col('employee_id')}) as value "
            f"from {snap.table}{snap.where(preds, apply_filter)}")


def monthly_hc_series(snap: Need, tuning: dict[str, Any], months: int) -> str | None:
    if not snap.has("snapshot_date", "employee_id"):
        return None
    d = snap.col("snapshot_date")
    preds = [f"{d} = last_day({d})",  # includes the month-end at the period start: the "start" headcount
             f"{d} >= {period_start_sql(snap, months)}",
             f"{d} <= {as_of_sql(snap)}"]
    if snap.col("employment_status"):
        preds.append(f"{snap.col('employment_status')} in ({lit_list(tunable(tuning, 'active_status_values', ['Active']))})")
    types = tunable(tuning, "regular_employee_types", None)
    if types and snap.col("employee_type"):
        preds.append(f"{snap.col('employee_type')} in ({lit_list(types)})")
    return (f"select {d} as month_end, count(distinct {snap.col('employee_id')}) as hc "
            f"from {snap.table}{snap.where(preds)} group by 1")


def period_start_sql(snap: Need, months: int) -> str:
    return f"last_day(dateadd('month', -{int(months)}, {as_of_sql(snap)}))"


def included_exits(exits: Need, snap: Need, tuning: dict[str, Any]) -> str | None:
    """Exits of the people counted in headcount: the employee type on their latest snapshot row on or before
    the exit (snapshot rows only, via the snapshot's row filter) is one of the included types. Numerator and
    denominator then cover the same population, even when exit records carry their own (or no) type."""
    types = tunable(tuning, "regular_employee_types", None)
    if not (types and snap.col("employee_type") and snap.col("employee_id") and exits.col("employee_id")
            and snap.col("snapshot_date") and exits.col("termination_date")):
        return None
    sid, sd, st = snap.col("employee_id"), snap.col("snapshot_date"), snap.col("employee_type")
    eid, et = f"{exits.table}.{exits.col('employee_id')}", f"{exits.table}.{exits.col('termination_date')}"
    latest = f"(select max_by({st}, {sd}) from {snap.table} latest_snap{snap.where([f'{sid} = {eid}', f'{sd} <= {et}'])})"
    return f"{latest} in ({lit_list(types)})"


def exit_day(exits: Need, tuning: dict[str, Any] | None) -> str:
    """The day an exit counts: termination date + exit_date_offset_days (default 1, the day after)."""
    offset = int(tunable(tuning or {}, "exit_date_offset_days", 1))
    t = exits.col("termination_date")
    return f"dateadd('day', {offset}, {t})" if offset else t


def exits_in_window(exits: Need, snap: Need, months: int, extra: str | None = None,
                    tuning: dict[str, Any] | None = None) -> str | None:
    if not exits.has("termination_date") or not snap.has("snapshot_date"):
        return None
    t = exit_day(exits, tuning)
    preds = [f"{t} > {period_start_sql(snap, months)}", f"{t} <= {as_of_sql(snap)}"]
    if extra:
        preds.append(extra)
    same_people = included_exits(exits, snap, tuning or {})
    if same_people:
        preds.append(same_people)
    return f"select count(*) as value from {exits.table}{exits.where(preds)}"


def rate_method(tuning: dict[str, Any]) -> dict[str, Any]:
    """The analyst's attrition method: {denominator: start_end|average|start|end, annualized: bool}.

    Default: exits in the period / average of headcount at period start and period end, not annualized."""
    for skill in tuning.values():
        t = skill.get("tunables", {}).get("attrition_rate_method") or {}
        m = t.get("value") if isinstance(t.get("value"), dict) else t.get("method")
        if m:
            return {"denominator": m.get("denominator", "start_end"), "annualized": bool(m.get("annualized"))}
    return {"denominator": "start_end", "annualized": False}


def headcount_base(series: str, denominator: str, start: str | None = None) -> str:
    """start: the period-start date; the 'average' method averages the month-ends after it."""
    if denominator == "start":
        return f"(select hc from ({series}) order by month_end asc limit 1)"
    if denominator == "end":
        return f"(select hc from ({series}) order by month_end desc limit 1)"
    if denominator == "start_end":
        return (f"((select hc from ({series}) order by month_end asc limit 1) + "
                f"(select hc from ({series}) order by month_end desc limit 1)) / 2")
    return f"(select avg(hc) from ({series}){f' where month_end > {start}' if start else ''})"


def rate_sql(numerator: str, series: str, method: dict[str, Any], months: str | int,
             start: str | None = None) -> str:
    scale = f" * 12 / nullif({months}, 0)" if method["annualized"] else ""
    base = headcount_base(series, method["denominator"], start)
    return f"select round(100 * ({numerator}) / nullif({base}, 0){scale}, 2) as value"


def attrition_metrics(exits: Need, snap: Need, tuning: dict[str, Any]) -> dict[str, str]:
    months = int(tunable(tuning, "attrition_window_months", 12))
    method = rate_method(tuning)
    out: dict[str, str] = {}
    series = monthly_hc_series(snap, tuning, months)
    total = exits_in_window(exits, snap, months, tuning=tuning)
    if not series or not total:
        return out
    start = period_start_sql(snap, months)
    out["avg_hc"] = (f"select avg(hc) as value, min(hc) as min_hc, max(hc) as max_hc, count(*) as months "
                     f"from ({series}) where month_end > {start}")
    out["exits_window"] = total
    out["exits_month"] = exits_in_window(exits, snap, 1, tuning=tuning)
    out["attrition_rate"] = rate_sql(total, series, method, months, start)
    vol_values = tunable(tuning, "voluntary_values", ["Voluntary"])
    cat = exits.col("termination_category")
    if cat:
        vol = exits_in_window(exits, snap, months, f"{cat} in ({lit_list(vol_values)})", tuning)
        out["voluntary_attrition_rate"] = rate_sql(vol, series, method, months, start)
        ytd = ytd_involuntary_sql(exits, snap, tuning, cat, vol_values, method)
        if ytd:
            out["ytd_involuntary_rate"] = ytd
    if exits.col("regrettable_flag"):
        reg = exits_in_window(exits, snap, months, f"{exits.col('regrettable_flag')} = true", tuning)
        out["regrettable_share"] = f"select round(100 * ({reg}) / nullif(({total}), 0), 2) as value"
    return out


def ytd_involuntary_sql(exits: Need, snap: Need, tuning: dict[str, Any], cat: str, vol_values: list[Any],
                        method: dict[str, Any]) -> str | None:
    """Involuntary exits since the fiscal year started / headcount over the same months (method-aware)."""
    if not snap.has("snapshot_date", "employee_id") or not exits.has("termination_date"):
        return None
    fm = int(tunable(tuning, "fiscal_year_start_month", 1))
    a = as_of_sql(snap)
    fy = f"date_from_parts(year({a}) - iff(month({a}) < {fm}, 1, 0), {fm}, 1)"
    d, t = snap.col("snapshot_date"), exit_day(exits, tuning)
    start = f"last_day(dateadd('month', -1, {fy}))"  # month-end before the fiscal year: the start headcount
    preds = [f"{d} = last_day({d})", f"{d} >= {start}", f"{d} <= {a}"]
    if snap.col("employment_status"):
        preds.append(f"{snap.col('employment_status')} in ({lit_list(tunable(tuning, 'active_status_values', ['Active']))})")
    types = tunable(tuning, "regular_employee_types", None)
    if types and snap.col("employee_type"):
        preds.append(f"{snap.col('employee_type')} in ({lit_list(types)})")
    series = (f"select {d} as month_end, count(distinct {snap.col('employee_id')}) as hc "
              f"from {snap.table}{snap.where(preds)} group by 1")
    invol_preds = [f"{t} >= {fy}", f"{t} <= {a}", f"{cat} is not null", f"{cat} not in ({lit_list(vol_values)})"]
    same_people = included_exits(exits, snap, tuning)
    if same_people:
        invol_preds.append(same_people)
    invol = f"select count(*) from {exits.table}{exits.where(invol_preds)}"
    return rate_sql(invol, series, method, f"datediff('month', {fy}, {a}) + 1", start)


def span_metrics(org: Need, tuning: dict[str, Any]) -> dict[str, str]:
    if not org.has("employee_id", "manager_id"):
        return {}
    preds = []
    if org.col("snapshot_date"):
        preds.append(f"{org.col('snapshot_date')} = {as_of_sql(org)}")
    base = (f"select {org.col('manager_id')} as mgr, count(distinct {org.col('employee_id')}) as drs "
            f"from {org.table}{org.where(preds + [org.col('manager_id') + ' is not null'])} group by 1")
    over = int(tunable(tuning, "span_over_threshold", 10))
    under = int(tunable(tuning, "span_under_threshold", 3))
    return {
        "avg_span": f"select round(avg(drs), 2) as value, count(*) as managers from ({base})",
        "over_spanned_managers": f"select count_if(drs > {over}) as value from ({base})",
        "under_spanned_managers": f"select count_if(drs < {under}) as value from ({base})",
    }


def hires_in_window(hires: Need, snap: Need, months: int) -> str | None:
    if not hires.has("employee_id", "hire_date") or not snap.has("snapshot_date"):
        return None
    h = hires.col("hire_date")
    preds = [f"{h} > dateadd('month', -{int(months)}, {as_of_sql(snap)})", f"{h} <= {as_of_sql(snap)}"]
    return f"select count(distinct {hires.col('employee_id')}) as value from {hires.table}{hires.where(preds)}"


def open_reqs(reqs: Need, tuning: dict[str, Any]) -> str | None:
    if not reqs.has("req_id", "status"):
        return None
    values = tunable(tuning, "open_req_status_values", ["Open"])
    preds = [reqs.col('status') + ' in (' + lit_list(values) + ')']
    close = tunable(tuning, "def_open_req_close_at", None)
    if isinstance(close, dict) and close.get("table") and close.get("key") and close.get("date"):
        # the analyst's policy: a req stops counting as open once an offer is accepted
        preds.append(f"{reqs.col('req_id')} not in (select {quote_ident(close['key'])} from {quote_fqn(close['table'])} "
                     f"where {quote_ident(close['date'])} is not null and {quote_ident(close['key'])} is not null)")
    return (f"select count(distinct {reqs.col('req_id')}) as value from {reqs.table}{reqs.where(preds)}")


def probe_sql(need: Need) -> str | None:
    """Zero-row probe touching every bound column/expression + row filter (for EXPLAIN)."""
    if not need.ok:
        return None
    cols = [need.col(f) for f in (need.b.get("fields") or {}) if need.col(f)]
    if not cols:
        return None
    return f"select {', '.join(cols)} from {need.table}{need.where()} limit 0"


def grain_check_sql(need: Need, grain_fields: list[str]) -> str | None:
    cols = [need.col(f) for f in grain_fields]
    if not need.ok or not all(cols):
        return None
    return (f"select count(*) as rows_total, count(distinct {', '.join(cols)}) as rows_distinct "
            f"from {need.table}{need.where()}")
