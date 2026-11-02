#!/usr/bin/env python3
"""v2 validator: runnability gates + accuracy gates. Stdlib only.

Runnability (per skill):
  G1 parse        bindings / tuning files load and every required need is bound
  G2 references   bound objects + columns exist in the discovered index
  G3 compile      EXPLAIN a probe touching every bound column, expression, row filter
  G4 execute      bounded aggregate run returns rows; required columns not mostly NULL

Accuracy:
  A1 sanity       metric bounds (HC > 0, 0 <= rate <= 100, min <= avg HC <= max, span plausible)
  A2 grain        grain columns unique after the row filter (no fanout / double counting)
  A3 consistency  the same metric (active regular HC) agrees across every skill binding it
  A4 reconcile    computed numbers match business-context reference numbers (tolerance)
  A5 definitions  confirmed tuning values / row-filter values exist in the bound columns

Only aggregates are executed and reported; no person-level rows are read.
Writes <workspace>/validation_report.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics_sql as M  # noqa: E402
from _sql import Runner, SqlError, make_runner  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_NEEDS = ["people.headcount.employee_snapshot", "people.org_health.employee_snapshot",
                  "people.attrition.snapshot_monthly", "people.headcount.active_employees",
                  "people.offer_builder.employee_snapshot"]
STATUS_FIELDS = ("employment_status", "is_active", "employee_type", "snapshot_date", "employee_id")
REL_TOL, ABS_TOL = 0.02, 5


def gate(name: str, ok: bool | None, detail: str, **data: Any) -> dict[str, Any]:
    status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    return {"gate": name, "status": status, "detail": detail, **data}


def scalar(run: Runner, sql: str) -> dict[str, Any]:
    rows = run(sql)
    return rows[0] if rows else {}


def enriched_need(key: str, bindings: dict[str, Any]) -> M.Need:
    """Borrow status/type/date columns from sibling bindings on the same table, so
    every skill applies the same headcount definition (the runtime contract)."""
    b = json.loads(json.dumps(bindings["needs"].get(key) or {}))
    for other in bindings["needs"].values():
        if other is b or other.get("fqn") != b.get("fqn") or other.get("row_filter") != b.get("row_filter"):
            continue
        for f in STATUS_FIELDS:
            if not (b.get("fields") or {}).get(f, {}).get("column") and (other.get("fields") or {}).get(f, {}).get("column"):
                b.setdefault("fields", {})[f] = other["fields"][f]
    return M.Need(key, b)


def runnability(run: Runner, skill: dict[str, Any], bindings: dict[str, Any],
                index: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    req = [n for n in skill["data_needs"] if n.get("required")]
    unbound = [n["key"] for n in req if bindings["needs"].get(n["key"], {}).get("status") == "unresolved"]
    results.append(gate("G1_parse", not unbound,
                        "all required needs bound" if not unbound else f"unbound: {unbound}"))
    missing = []
    for n in skill["data_needs"]:
        b = bindings["needs"].get(n["key"]) or {}
        if b.get("status") == "unresolved" or b.get("kind") in {"derived", "semantic_view"}:
            continue
        cols = index["objects"].get(b.get("fqn"), {}).get("columns", {})
        if not cols:
            missing.append(b.get("fqn"))
            continue
        for fname, f in (b.get("fields") or {}).items():
            if f.get("column") and f["column"] not in cols:
                missing.append(f"{b['fqn']}.{f['column']}")
        if b.get("row_filter") and b["row_filter"]["column"] not in cols:
            missing.append(f"{b['fqn']}.{b['row_filter']['column']}")
    results.append(gate("G2_references", not missing, "all objects/columns exist" if not missing
                        else f"missing: {missing[:5]}"))
    compile_errors, exec_issues, checked = [], [], 0
    for n in skill["data_needs"]:
        need = M.Need(n["key"], bindings["needs"].get(n["key"]) or {})
        probe = M.probe_sql(need)
        if not probe:
            continue
        checked += 1
        try:
            run(f"explain {probe}")
        except SqlError as exc:
            compile_errors.append(f"{n['key']}: {str(exc)[:160]}")
            continue
        try:
            rows = scalar(run, f"select count(*) as n from {need.table}{need.where()}")["N"]
            if not rows and n.get("required"):
                exec_issues.append(f"{n['key']}: 0 rows after row filter")
        except SqlError as exc:
            exec_issues.append(f"{n['key']}: {str(exc)[:160]}")
        for fname, spec in n["fields"].items():
            f = (need.b.get("fields") or {}).get(fname) or {}
            meta = index["objects"].get(need.fqn, {}).get("columns", {}).get(f.get("column") or "", {})
            if spec.get("required") and meta.get("null_rate") is not None and meta["null_rate"] > 0.95:
                exec_issues.append(f"{n['key']}.{fname}: {meta['null_rate']:.0%} NULL")
    results.append(gate("G3_compile", not compile_errors if checked else None,
                        f"{checked} probes compiled" if not compile_errors else "; ".join(compile_errors[:3])))
    results.append(gate("G4_execute", not exec_issues if checked else None,
                        "rows returned, required columns populated" if not exec_issues else "; ".join(exec_issues[:3])))
    return results


def skill_metrics(skill: str, bindings: dict[str, Any], tuning: dict[str, Any]) -> dict[str, str]:
    snap_key = {"attrition_insights": "people.attrition.snapshot_monthly",
                "good_morning_chro": "people.headcount.active_employees",
                "org_health_report": "people.org_health.employee_snapshot",
                "offer_builder": "people.offer_builder.employee_snapshot"}.get(skill, "people.headcount.employee_snapshot")
    snap = enriched_need(snap_key, bindings)
    exits = enriched_need("people.exits.event_records", bindings)
    out: dict[str, str] = {}
    if skill in {"headcount_insights", "good_morning_chro", "org_health_report", "attrition_insights", "hiring_brief"}:
        sql = M.active_regular_hc(snap, tuning)
        if sql:
            out["active_regular_hc"] = sql
    if skill in {"attrition_insights", "good_morning_chro", "org_health_report"}:
        out.update(M.attrition_metrics(exits, snap, tuning))
    if skill in {"headcount_insights", "org_health_report"}:
        org = enriched_need("people.org_health.employee_snapshot", bindings)
        out.update(M.span_metrics(org, tuning))
        hires = enriched_need("people.hires.event_records", bindings)
        h = M.hires_in_window(hires, snap, int(M.tunable(tuning, "new_hire_window_months", 12)))
        if h:
            out["hires_window"] = h
    if skill in {"headcount_insights", "org_health_report", "hiring_brief"}:
        hm = M.hires_in_window(enriched_need("people.hires.event_records", bindings), snap, 1)
        if hm:
            out["hires_month"] = hm
    if skill in {"good_morning_chro", "good_morning_recruiting", "hiring_brief", "org_health_report"}:
        r = M.open_reqs(enriched_need("people.ta.requisitions", bindings) if bindings["needs"].get(
            "people.ta.requisitions", {}).get("status") != "unresolved" else enriched_need("people.reqs.open_positions", bindings), tuning)
        if r:
            out["open_reqs"] = r
    return out


def sanity(values: dict[str, Any]) -> list[str]:
    issues = []
    hc = values.get("active_regular_hc", {}).get("VALUE")
    if hc is not None and float(hc) <= 0:
        issues.append("active regular headcount is 0")
    for rate in ("attrition_rate", "voluntary_attrition_rate", "regrettable_share"):
        v = values.get(rate, {}).get("VALUE")
        if v is not None and not (0 <= float(v) <= 100):
            issues.append(f"{rate}={v} outside 0-100")
    avg = values.get("avg_hc", {})
    if avg.get("VALUE") is not None and not (float(avg["MIN_HC"]) <= float(avg["VALUE"]) <= float(avg["MAX_HC"])):
        issues.append("average headcount outside monthly min/max")
    span = values.get("avg_span", {}).get("VALUE")
    if span is not None and not (1 <= float(span) <= 30):
        issues.append(f"average span {span} implausible")
    total, vol = values.get("attrition_rate", {}).get("VALUE"), values.get("voluntary_attrition_rate", {}).get("VALUE")
    if total is not None and vol is not None and float(total) > 0 and float(vol) == float(total):
        issues.append("voluntary attrition equals total attrition: voluntary definition matches every exit")
    for k, v in values.items():
        if isinstance(v, dict) and v.get("VALUE") is None and k not in {"regrettable_share"}:
            issues.append(f"{k} returned NULL")
    return issues


def grain_gate(run: Runner, skill: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    """Grain must be unique after the row filter. Entity grains on snapshot tables
    are checked at the as-of snapshot date; lookup needs carried by a wider table
    (e.g. cost center -> org) are checked as functional dependencies instead."""
    checks, failures = [], []
    for n in skill["data_needs"]:
        need = enriched_need(n["key"], bindings)
        if not need.ok:
            continue
        grain_fields = []
        for g in n.get("grain", []):
            for fname in n["fields"]:
                if fname == f"{g}_id" or fname == g or (g == "snapshot_month" and fname == "snapshot_date"):
                    grain_fields.append(fname)
                    break
        if len(grain_fields) != len(n.get("grain", [])) or not grain_fields:
            continue
        cols = [need.col(f) for f in grain_fields]
        if not all(cols):
            continue
        preds = []
        if "snapshot_date" not in grain_fields and need.col("snapshot_date"):
            preds.append(f"{need.col('snapshot_date')} = {M.as_of_sql(need)}")
        dependents = [need.col(f) for f, s in n["fields"].items()
                      if s.get("required") and f not in grain_fields and need.col(f)]
        dep_sql = (f", count(distinct {', '.join(cols + dependents)}) as rows_dep" if dependents else "")
        sql = (f"select count(*) as rows_total, count(distinct {', '.join(cols)}) as rows_distinct{dep_sql} "
               f"from {need.table}{need.where(preds)}")
        try:
            row = scalar(run, sql)
        except SqlError:
            continue
        total, distinct = int(row.get("ROWS_TOTAL") or 0), int(row.get("ROWS_DISTINCT") or 0)
        mode = "unique"
        ok = total == distinct
        if not ok and dependents:
            mode = "functional_dependency"
            ok = int(row.get("ROWS_DEP") or 0) == distinct
        checks.append({"need": n["key"], "grain": grain_fields, "mode": mode, "rows": total,
                       "distinct": distinct, "at_as_of": bool(preds), "required": bool(n.get("required")),
                       "status": "PASS" if ok else "FAIL"})
        if not ok:
            failures.append(f"{n['key']}: {total - distinct} duplicate rows at grain {grain_fields}"
                            + (" (as-of snapshot)" if preds else ""))
    return gate("A2_grain", not failures if checks else None,
                f"{len(checks)} grains verified" if not failures else "; ".join(failures[:3]), checks=checks)


def definitions_gate(skill_name: str, tuning: dict[str, Any], bindings: dict[str, Any],
                     index: dict[str, Any]) -> dict[str, Any]:
    issues, checked = [], 0
    t = tuning.get(skill_name, {}).get("tunables", {})
    field_for = {"regular_employee_types": ("employee_type", ("people.headcount.", "people.org_health.")),
                 "active_status_values": ("employment_status", ("people.headcount.", "people.org_health.")),
                 "voluntary_values": ("termination_category", ("people.exits.",)),
                 "open_req_status_values": ("status", ("people.ta.requisitions", "people.reqs."))}
    for tid, (field, prefixes) in field_for.items():
        if tid not in t or not t[tid].get("value"):
            continue
        for key, b in bindings["needs"].items():
            if not key.startswith(prefixes) or b.get("status") == "unresolved":
                continue
            f = (b.get("fields") or {}).get(field) or {}
            meta = index["objects"].get(b.get("fqn"), {}).get("columns", {}).get(f.get("column") or "", {})
            values = {str(v) for v, _n in meta.get("top_values") or []}
            if not values:
                continue
            checked += 1
            unknown = [v for v in t[tid]["value"] if str(v) not in values]
            if unknown:
                issues.append(f"{tid} values {unknown} not found in {f['column']}")
            break
    for key, b in bindings["needs"].items():
        rf = b.get("row_filter")
        if not rf or skill_name not in b.get("skills", []):
            continue
        meta = index["objects"].get(b["fqn"], {}).get("columns", {}).get(rf["column"], {})
        values = {str(v) for v, _n in meta.get("top_values") or []}
        checked += 1
        if not set(map(str, rf["values"])) <= values:
            issues.append(f"row filter {rf['values']} not in {rf['column']}")
    return gate("A5_definitions", not issues if checked else None,
                f"{checked} definitions present in data" if not issues else "; ".join(sorted(set(issues))[:3]))


def reconcile_gate(values_by_skill: dict[str, dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    refs = [i for i in context.get("items", []) if i["kind"] == "reference_number" and i["status"] != "rejected"]
    results, failures = [], []
    for ref in refs:
        metric = ref["value"].get("metric")
        key = {"active_headcount": "active_regular_hc", "attrition_rate": "attrition_rate",
               "voluntary_attrition_rate": "voluntary_attrition_rate"}.get(metric)
        computed = next((v[key]["VALUE"] for v in values_by_skill.values()
                         if key in v and v[key].get("VALUE") is not None), None)
        if computed is None:
            continue
        expected = float(ref["value"]["value"])
        delta = float(computed) - expected
        ok = abs(delta) <= max(ABS_TOL if metric == "active_headcount" else 0.5, REL_TOL * abs(expected))
        results.append({"metric": metric, "expected": expected, "computed": float(computed),
                        "delta": round(delta, 2), "source": ref["source"]["connector"],
                        "as_of": ref["value"].get("as_of"), "status": "PASS" if ok else "FAIL"})
        if not ok:
            failures.append(f"{metric}: computed {computed} vs {expected} from {ref['source']['connector']}")
    return gate("A4_reconcile", not failures if results else None,
                "matches business-context reference numbers" if not failures else "; ".join(failures),
                comparisons=results)


def validate(run: Runner, workspace: Path, catalog: dict[str, Any], skills: list[str]) -> dict[str, Any]:
    bindings = json.loads((workspace / "bindings.json").read_text(encoding="utf-8"))
    index = json.loads((workspace / "data_index.json").read_text(encoding="utf-8"))
    context = json.loads((workspace / "context.json").read_text(encoding="utf-8")) \
        if (workspace / "context.json").exists() else {"items": []}
    tuning = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (workspace / "tuning").glob("*.json")}
    readiness = {s["skill"]: s for s in json.loads((workspace / "readiness.json").read_text(encoding="utf-8"))["skills"]}
    report: dict[str, Any] = {"skills": {}, "cross_skill": {}}
    values_by_skill: dict[str, dict[str, Any]] = {}
    import preview as P
    (workspace / "validation_cache").mkdir(exist_ok=True)
    for skill in catalog["skills"]:
        name = skill["name"]
        if skills and name not in skills:
            continue
        if not skills and readiness.get(name, {}).get("status") == "BLOCKED":
            continue
        sig = P.signature(workspace, name)
        cache_path = workspace / "validation_cache" / f"{name}.json"
        cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        if cached.get("signature") == sig and cached.get("entry", {}).get("gates"):
            report["skills"][name] = {**cached["entry"], "cached": True}
            values_by_skill[name] = cached.get("values", {})
            continue
        gates = runnability(run, skill, bindings, index)
        values: dict[str, Any] = {}
        errors = []
        skill_tuning = {name: tuning.get(name, {})}
        for metric, sql in skill_metrics(name, bindings, {**tuning, **skill_tuning}).items():
            try:
                values[metric] = scalar(run, sql)
            except SqlError as exc:
                errors.append(f"{metric}: {str(exc)[:160]}")
        values_by_skill[name] = values
        issues = sanity(values) + errors
        gates.append(gate("A1_sanity", not issues if values or errors else None,
                          "metrics within bounds" if not issues else "; ".join(issues[:4])))
        gates.append(grain_gate(run, skill, bindings))
        gates.append(definitions_gate(name, tuning, bindings, index))
        report["skills"][name] = {"gates": gates,
                                  "metrics": {k: {kk: vv for kk, vv in v.items()} for k, v in values.items()}}
        if all(g["status"] != "FAIL" for g in gates):  # only clean passes are reused
            cache_path.write_text(json.dumps({"signature": sig, "entry": report["skills"][name], "values": values},
                                             default=str) + "\n", encoding="utf-8")
        else:
            cache_path.unlink(missing_ok=True)
    hcs = {s: v["active_regular_hc"]["VALUE"] for s, v in values_by_skill.items()
           if v.get("active_regular_hc", {}).get("VALUE") is not None}
    report["cross_skill"]["A3_consistency"] = gate(
        "A3_consistency", (len(set(hcs.values())) <= 1) if len(hcs) > 1 else None,
        "active regular headcount agrees across skills" if len(set(hcs.values())) <= 1
        else f"active regular headcount differs across skills: {hcs}", values=hcs)
    report["cross_skill"]["A4_reconcile"] = reconcile_gate(values_by_skill, context)
    import corrections
    import review
    report["cross_skill"]["A6_corrections"] = corrections.check(workspace, values_by_skill,
                                                                skills or list(report["skills"]))
    report["review"] = review.status(workspace, list(report["skills"]))
    report["verified"] = bool(report["review"]) and all(v == "reviewed" for v in report["review"].values())
    for name, entry in report["skills"].items():
        failing = [g for g in entry["gates"] if g["status"] == "FAIL"]
        entry["result"] = "FAIL" if failing else "PASS"
        entry["reason"] = (f"{GATE_PLAIN.get(failing[0]['gate'], failing[0]['gate'])}: {plain_keys(failing[0]['detail'])[:160]}"
                           if failing else "queries compile and run; numbers are within expected ranges")
        if name in hcs and report["cross_skill"]["A3_consistency"]["status"] == "FAIL":
            entry["result"], entry["reason"] = "FAIL", report["cross_skill"]["A3_consistency"]["detail"]
    return report


def plain_keys(text: str) -> str:
    """people.ta.hm_sentiment -> 'Hiring manager sentiment' (the need's own business label)."""
    import re
    import discover_topics as T
    labels = {n["key"]: T.need_label(n) for s in T.load(T.CATALOG_DIR / "people.json")["skills"]
              for n in s["data_needs"]}

    def one(m: "re.Match[str]") -> str:
        key = m.group(0)
        return labels.get(key) or key.split(".", 1)[1].replace(".", " ").replace("_", " ")
    return re.sub(r"people(?:\.[a-z0-9_]+)+", one, text).replace("['", "").replace("']", "").replace("unbound: ", "")


GATE_PLAIN = {"G1_parse": "Required data isn't connected", "G2_references": "A table or column no longer exists",
              "G3_compile": "A query doesn't compile", "G4_execute": "A query returns no rows",
              "A1_sanity": "A number is out of range", "A2_grain": "Rows are double counted",
              "A5_definitions": "A definition uses values that aren't in the data"}


def feedback(report: dict[str, Any], workspace: Path) -> list[dict[str, Any]]:
    """Turn gate failures into re-bind actions. Optional needs that fail grain are
    dropped automatically (learned.json drop_needs); required needs go to the analyst."""
    learned_path = workspace / "learned.json"
    learned = json.loads(learned_path.read_text(encoding="utf-8")) if learned_path.exists() else {}
    actions, changed = [], False
    for name, entry in report["skills"].items():
        for g in entry["gates"]:
            if g["gate"] != "A2_grain":
                continue
            for c in g.get("checks", []):
                if c["status"] != "FAIL":
                    continue
                reason = f"validator: duplicate rows at grain {c['grain']} (object is finer than the need)"
                if c["required"]:
                    actions.append({"need": c["need"], "action": "analyst_review", "reason": reason})
                elif c["need"] not in learned.get("drop_needs", {}):
                    learned.setdefault("drop_needs", {})[c["need"]] = reason
                    actions.append({"need": c["need"], "action": "dropped_optional", "reason": reason})
                    changed = True
    if changed:
        learned_path.write_text(json.dumps(learned, indent=2) + "\n", encoding="utf-8")
    return actions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--connection", required=True)
    parser.add_argument("--catalog", type=Path, default=SKILL_ROOT / "catalog" / "people.json")
    parser.add_argument("--skill", action="append", default=[])
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    skills = args.skill
    if skills:  # adding skills keeps the earlier ones; unchanged ones come from the cache without SQL
        try:
            prior = json.loads((args.workspace / "validation_report.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            prior = {}
        skills = sorted(set(skills) | set(prior.get("skills", {})))
    report = validate(make_runner(args.connection, 300), args.workspace, catalog, skills)
    report["feedback"] = feedback(report, args.workspace)
    (args.workspace / "validation_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n",
                                                           encoding="utf-8")
    summary = {name: entry["result"] for name, entry in report["skills"].items()}
    print(json.dumps({"event": "validated", **summary,
                      "feedback": [f"{a['action']}:{a['need']}" for a in report["feedback"]]}))
    return 0  # the report says which skills fail; the job itself succeeded


if __name__ == "__main__":
    raise SystemExit(main())
