#!/usr/bin/env python3
"""Build a customer People plugin from v2 bindings + tuning. Stdlib only.

Differences from the v1 builder:
  * bindings come from bindings.json (evidence-backed, analyst-reviewed), not
    exact-alias inventory matching;
  * hydration_hints.yaml carries row filters, column EXPRESSIONS (e.g.
    exit_flag -> EMPLOYMENT_STATUS = 'Terminated') and derived sources (fiscal
    calendar from business context);
  * each skill ships tuning.json and a generated "Customer configuration"
    section appended to SKILL.md so the agent applies confirmed definitions,
    thresholds and filters at runtime; the template body itself is untouched,
    keeping templates upgradable;
  * each skill's golden cases and eval plan (the behavior standard) are copied
    to evals/<skill>/; the Evaluate stage runs them against a draft build
    (evaluate.py) and its result decides hydrated vs partial here.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _minyaml  # noqa: E402
import evaluate  # noqa: E402


def load_catalogs(paths: list[Path]) -> dict[tuple[str, str], dict[str, Any]]:
    """Index catalog skills by (domain, skill name)."""
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        catalog = json.loads(path.read_text(encoding="utf-8"))
        for skill in catalog.get("skills", []):
            entry = dict(skill)
            entry["domain"] = catalog["domain"]
            entry["source_plugin"] = catalog["source_plugin"]
            lookup[(catalog["domain"], skill["name"])] = entry
    return lookup


SKILL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATES_ROOT = SKILL_ROOT / "templates"
CONFIG_MARKER = "<!-- people_hydrator:customer-configuration -->"
EVAL_FILES = ("golden_cases.md", "eval_plan.md")  # customer-relevant eval planning files only


def selected_skills(readiness: dict[str, Any], explicit: list[str], include_partial: bool) -> list[dict[str, Any]]:
    rows = readiness.get("skills", [])
    if explicit:
        return [r for r in rows if r["skill"] in explicit]
    return [r for r in rows if r["status"] == "READY" or (include_partial and r["status"] == "PARTIAL")]


def need_hints(entry: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for need in entry.get("data_needs", []):
        b = bindings["needs"].get(need["key"])
        if not b or b.get("status") == "unresolved" or not b.get("fqn"):
            continue
        source: dict[str, Any] = {"fqn": b["fqn"]}
        if b.get("kind") == "derived":
            source["derived"] = b["derived"]
        if b.get("kind") == "semantic_view":
            source["object_type"] = "semantic_view"
        columns, expressions = {}, {}
        for fname in need["fields"]:
            f = b["fields"].get(fname) or {}
            if not f.get("column"):
                continue
            if f.get("expression"):
                expressions[fname] = f["expression"]
            else:
                columns[fname] = f["column"]
        source["columns"] = columns
        if expressions:
            source["expressions"] = expressions
        if b.get("row_filter"):
            rf = b["row_filter"]
            source["row_filter"] = {"column": rf["column"], "op": rf["op"], "values": rf["values"]}
        out[need["key"]] = {"required": "yes" if need.get("required") else "no", "sources": [source]}
    return out


def sql_literal_list(values: list[str]) -> str:
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)


def plain_value(t: dict[str, Any]) -> str:
    """A setting as the agent should read it ("days from REQ_OPEN_DATE to FILLED_DATE on REQUISITION")."""
    v = t["value"]
    if isinstance(v, dict) and {"table", "column"} <= set(v):
        return f"use the pre-calculated `{v['column']}` on `{v['table']}`"
    if isinstance(v, dict) and "end_table" in v:
        return (f"days from `{v['start']}` on `{v['table']}` to the first `{v['end']}` on `{v['end_table']}` "
                f"(joined on `{v['key']}`)")
    if isinstance(v, dict) and {"start", "end", "table"} <= set(v):
        return f"days from `{v['start']}` to `{v['end']}` on `{v['table']}`"
    if t.get("type") == "date_choice":
        return f"use column `{v}`"
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return str(v).replace("_", " ") if t.get("type") == "choice" and isinstance(v, str) else str(v)


def config_section(entry: dict[str, Any], bindings: dict[str, Any], tuning: dict[str, Any] | None,
                   context: dict[str, Any], unavailable: list[str] | None = None,
                   rules_text: list[str] | None = None) -> str:
    """Markdown the agent reads at runtime. Uses only names/values the customer
    confirmed; never person-level data."""
    lines = ["", CONFIG_MARKER, "## Customer configuration (generated by People Hydrator)", "",
             "Apply these rules to every query this skill runs. They override template defaults. "
             "Resolve physical objects from `hydration_hints.yaml` in this directory.", ""]
    rules = []
    for need in entry.get("data_needs", []):
        b = bindings["needs"].get(need["key"]) or {}
        rf = b.get("row_filter")
        if rf and b.get("fqn"):
            rules.append(f"- `{need['key']}`: always filter `{rf['column']} IN ({sql_literal_list(rf['values'])})` "
                         f"on `{b['fqn']}`.")
        for fname, f in (b.get("fields") or {}).items():
            if f.get("expression") and fname in need["fields"]:
                rules.append(f"- `{need['key']}.{fname}` is computed as `{f['expression']}`.")
        if b.get("kind") == "derived" and b["derived"]["type"] == "fiscal_calendar":
            rules.append(f"- No fiscal calendar table exists: derive fiscal year/quarter from the date with the "
                         f"fiscal year starting in month {b['derived']['fiscal_year_start_month']}.")
    if rules:
        lines += ["### Data rules", *rules, ""]
    layout: list[str] = []
    need_keys = {n["key"] for n in entry.get("data_needs", [])}
    for note in bindings.get("layout_notes", []):
        if note.get("need") not in need_keys or not note.get("layout"):
            continue
        lines_for_note = [f"- {note['layout']}."]
        for d in note.get("dropped", []):
            lines_for_note.append(f"- `{note['need']}.{d['field']}`: {d['reason']}. Not bound; take it from "
                                  "another bound source of this skill or say it's unavailable.")
        for d in note.get("constant", []):
            lines_for_note.append(f"- `{note['need']}.{d['field']}`: {d['reason']}. Fine as a filter, but don't "
                                  "count or split by it here; if the skill needs it varied, use another bound "
                                  "source of this skill.")
        layout += [ln for ln in lines_for_note if ln not in layout]
    if layout:
        lines += ["### Data layout (checked on your data)", *layout, ""]
    if tuning:
        lines += ["### Definitions and thresholds", "| Setting | Value | Source |", "|---|---|---|"]
        for tid, t in tuning["tunables"].items():
            if t.get("value") in (None, "", []) or t.get("value") == {"not_tracked": True}:  # unsettled or not tracked
                continue
            lines.append(f"| {t['label']} (`{tid}`) | {plain_value(t)} | {t['source']} ({t['status']}) |")
        lines.append("")
    if unavailable:
        lines += ["### Not available in this customer's data", *[f"- {n.rstrip('.')}. Say so instead of estimating." for n in unavailable], ""]
    if rules_text:
        lines += ["### Customer rules (from the analyst)", *[f"- {r}" for r in rules_text], ""]
    caveats = [i for i in context.get("items", []) if i["kind"] == "caveat" and i["status"] == "confirmed"]
    if caveats:
        lines += ["### Known data caveats (confirmed)", *[f"- {c['text']}" for c in caveats[:6]], ""]
    return "\n".join(lines) + "\n"


def copy_evals(entry: dict[str, Any], templates_root: Path, output: Path) -> str | None:
    """Copy the skill's golden cases and eval plan to evals/<skill>/; never eval results."""
    if not entry.get("eval_dir"):
        return None
    src = (templates_root / entry["source_plugin"] / entry["eval_dir"]).resolve()
    files = [src / name for name in EVAL_FILES if (src / name).is_file()]
    if not files:
        return None
    dest = output / "evals" / entry["name"]
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, dest / f.name)
    return str(dest / "golden_cases.md") if (dest / "golden_cases.md").exists() else None


def write_skill(dest: Path, entry: dict[str, Any], bindings: dict[str, Any],
                tuning: dict[str, Any] | None, context: dict[str, Any],
                unavailable: list[str] | None = None, rules_text: list[str] | None = None) -> None:
    hints = {"format_version": 2, "skill": entry["name"], "data_needs": need_hints(entry, bindings)}
    (dest / "hydration_hints.yaml").write_text(_minyaml.dump(hints), encoding="utf-8")
    sidecar = dest / "hydration_hints.json"
    if sidecar.exists():
        sidecar.unlink()
    if tuning:
        (dest / "tuning.json").write_text(json.dumps(tuning, indent=2) + "\n", encoding="utf-8")
    skill_md = dest / "SKILL.md"
    body = skill_md.read_text(encoding="utf-8").split(CONFIG_MARKER)[0].rstrip() + "\n"
    section = config_section(entry, bindings, tuning, context, unavailable, rules_text)
    skill_md.write_text(body + section + drift_section(entry, bindings), encoding="utf-8")


def bound_columns(entry: dict[str, Any], bindings: dict[str, Any]) -> dict[str, set[str]]:
    """FQN -> physical columns this skill reads (tables only; semantic views and derived sources skipped)."""
    out: dict[str, set[str]] = {}
    for key, hint in need_hints(entry, bindings).items():
        for src in hint["sources"]:
            if src.get("object_type") == "semantic_view" or src.get("derived"):
                continue
            cols = set(src.get("columns", {}).values())
            if src.get("row_filter"):
                cols.add(src["row_filter"]["column"])
            out.setdefault(src["fqn"], set()).update(cols)
    return out


def drift_section(entry: dict[str, Any], bindings: dict[str, Any]) -> str:
    """'Before you answer': one metadata query per database confirms the bound tables and columns still exist."""
    tables = bound_columns(entry, bindings)
    if not tables:
        return ""
    by_db: dict[str, list[tuple[str, str, set[str]]]] = {}
    for fqn, cols in sorted(tables.items()):
        db, schema, name = fqn.split(".", 2)
        by_db.setdefault(db, []).append((schema, name, cols))
    lines = ["", "## Before you answer: check the data connection", "",
             "Run this once per session, before the first query. It reads metadata only.", ""]
    for db, rows in by_db.items():
        def q(x: str) -> str:
            return "'" + x.replace("'", "''") + "'"
        pairs = ", ".join(f"({q(s)}, {q(t)}, {q(c)})" for s, t, cols in rows for c in sorted(cols))
        lines += ["```sql",
                  "with expected (table_schema, table_name, column_name) as (",
                  f"  select * from values {pairs}",
                  ")",
                  "select e.table_schema, e.table_name, e.column_name",
                  "from expected e",
                  f"left join {db}.information_schema.columns c",
                  "  on c.table_schema = e.table_schema and c.table_name = e.table_name",
                  "  and c.column_name = e.column_name",
                  "where c.column_name is null;",
                  "```", ""]
    lines += ["- **No rows:** the connection is intact; continue.",
              "- **Any rows:** stop. Tell the user which table or column is missing and that the skill needs",
              "  to be re-hydrated with People Hydrator (it starts from the saved profile and only re-connects",
              "  this skill). Do not guess a replacement column.", ""]
    return "\n".join(lines)


def build(workspace: Path, catalog_path: Path, output: Path, skills: list[str],
          include_partial: bool, templates_root: Path = DEFAULT_TEMPLATES_ROOT) -> dict[str, Any]:
    readiness = json.loads((workspace / "readiness.json").read_text(encoding="utf-8"))
    bindings = json.loads((workspace / "bindings.json").read_text(encoding="utf-8"))
    context = json.loads((workspace / "context.json").read_text(encoding="utf-8")) \
        if (workspace / "context.json").exists() else {"items": []}
    validation = load_json(workspace / "validation_report.json", {}).get("skills", {})
    evaluation = evaluate.load(workspace)
    gates = load_json(workspace / "binding_gate.json", {}).get("skills", {})
    catalog = load_catalogs([catalog_path])
    earlier = {r["skill"]: r for r in (load_json(workspace / "hydration_summary.json", {}) or {}).get("summary", [])
               if r.get("status") != "blocked"}
    if skills:  # adding skills keeps the ones hydrated before (rebuilding their files is instant)
        skills = sorted(set(skills) | set(earlier))
    chosen = selected_skills(readiness, skills, include_partial)
    if output.exists():
        shutil.rmtree(output)
    (output / ".cortex-plugin").mkdir(parents=True)
    profile_version = (load_json(workspace / "profile.json", {}) or {}).get("version")
    (output / ".cortex-plugin" / "plugin.json").write_text(json.dumps({
        "name": "customer-people",
        "description": "Customer-specific People plugin generated by People Hydrator.",
        "version": "0.3.0", "profile_version": profile_version}, indent=2) + "\n", encoding="utf-8")
    progress_path = workspace / "hydrate_progress.jsonl"
    progress_path.write_text("", encoding="utf-8")
    built, summary = [], []
    for i, row in enumerate(chosen, 1):
        entry = catalog[(row["domain"], row["skill"])]
        emit(progress_path, {"skill": row["skill"], "step": i, "of": len(chosen), "status": "hydrating"})
        status, reason = hydration_status(row, validation.get(row["skill"]))
        if status == "blocked":
            summary.append({**summary_row(entry, row, status, reason, None),
                            "golden_cases": None, "evaluation": "not run (blocked)"})
            emit(progress_path, {"skill": row["skill"], "step": i, "of": len(chosen), "status": status})
            continue
        src = (templates_root / entry["source_plugin"] / entry["source_dir"]).resolve()
        dest = output / "skills" / row["skill"]
        shutil.copytree(src, dest)
        tuning_path = workspace / "tuning" / f"{row['skill']}.json"
        tuning = json.loads(tuning_path.read_text(encoding="utf-8")) if tuning_path.exists() else None
        unavailable = [c["note"] for c in (load_json(workspace / "questions.json", {}) or {}).get("cannot_compute", [])
                       if row["skill"] in c.get("affects", [])]
        unavailable += not_available(workspace, row["skill"])
        rules_text = [r["text"] for r in (load_json(workspace / "customer_rules.json", {}) or {}).get("rules", [])
                      if r["skill"] == row["skill"]]
        write_skill(dest, entry, bindings, tuning, context, unavailable, rules_text)
        golden = copy_evals(entry, templates_root, output)
        # compare against what Evaluate tested: a different build means its results are stale
        result = evaluate.skill_status(evaluation.get(row["skill"]), evaluate.fingerprint(dest))
        status, reason = hydration_status(row, validation.get(row["skill"]), result,
                                          gates.get(row["skill"]))
        built.append({"skill": row["skill"], "status": row["status"], "readiness": row["readiness"],
                      "golden_cases": golden, "evaluation": result["status"],
                      "binding_grade": (gates.get(row["skill"]) or {}).get("grade")})
        fp = evaluate.fingerprint(dest)
        prev = earlier.get(row["skill"])
        summary.append({**summary_row(entry, row, status, reason, str(dest)), "golden_cases": golden,
                        "evaluation": result["line"], "fingerprint": fp,
                        "change": "new" if not prev else
                                  ("unchanged" if prev.get("fingerprint") == fp else "updated (its data or definitions changed)")})
        emit(progress_path, {"skill": row["skill"], "step": i, "of": len(chosen), "status": status})
    import review
    reviewed = review.status(workspace, [s["skill"] for s in summary])
    for s in summary:
        s["reviewed"] = {"reviewed": "reviewed by you", "corrected": "corrected, not re-checked",
                         "open": "not reviewed"}[reviewed[s["skill"]]]
        if s["status"] == "hydrated" and reviewed[s["skill"]] != "reviewed":
            s["status"], s["reason"] = "partial", f"preview {s['reviewed']}; {s['reason']}"
    lines = ["# Customer People plugin", "", "Generated by People Hydrator"
             + (f" from customer profile version {profile_version}." if profile_version else "."), ""]
    lines += ["| Skill | Hydration | Why | Since last build | Preview | Golden cases | How to use |",
              "|---|---|---|---|---|---|---|"]
    lines += [f"| `{s['skill']}` | {s['status']} | {s['reason']} | {s.get('change', 'new')} | {s['reviewed']} "
              f"| {s['evaluation']} | {s['invoke']} |"
              for s in summary]
    lines += ["", "## Behavior standard", "",
              "Each skill was evaluated against the golden cases in `evals/<skill>/` (scored with its `eval_plan.md`). "
              "Run them again after changing data or definitions to confirm it still behaves as expected.",
              "", "## Activate", "", "```bash", f"cortex plugin add {output}", "/plugin reload", "```", ""]
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")
    result = {"status": "OK", "output": str(output), "skills": built, "summary": summary,
              "verified": all(v == "reviewed" for v in reviewed.values()) and bool(reviewed),
              "profile_version": profile_version}
    (workspace / "hydration_summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def not_available(workspace: Path, skill: str) -> list[str]:
    """Data the analyst told us they don't have (a data-connection question answered "We don't have this")."""
    state = load_json(workspace / "questions_state.json", {}) or {}
    return [f"{s['label'][:1].upper()}{s['label'][1:]} ({s.get('need_label', 'data')}) isn't available "
            "(you told us); say so instead of estimating."
            for qid, s in sorted(state.items())
            if qid.startswith("gate:") and s.get("value") == "__not_available__" and skill in s.get("skills", [])]


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def emit(path: Path, event: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
    print(json.dumps(event), file=sys.stderr, flush=True)


def plain_need(key: str) -> str:
    """people.ta.hm_sentiment -> 'Hiring manager sentiment' (the need's own business label)."""
    import discover_topics as T
    for s in T.load(T.CATALOG_DIR / "people.json")["skills"]:
        for n in s["data_needs"]:
            if n["key"] == key:
                return T.need_label(n)
    return key.split(".", 2)[-1].replace("_", " ").replace(".", " ")


def hydration_status(row: dict[str, Any], check: dict[str, Any] | None,
                     evaluation: dict[str, Any] | None = None,
                     gate: dict[str, Any] | None = None) -> tuple[str, str]:
    """hydrated | partial | blocked, with a plain-language reason.

    `evaluation` is evaluate.skill_status(): failed, stale or unfinished golden cases
    keep the skill partial; not_tested leaves the data verdict as is.
    `gate` is eval_gate.grade_skill(): INCOMPATIBLE (a must-have rule failed) is hydrated
    as partial with a warning; DEGRADED stays hydrated and names the gaps.
    """
    gaps = sorted({plain_need(g.split(" for ")[-1]) for g in row.get("gaps", [])})
    if row["status"] == "BLOCKED":
        return "blocked", "missing required data: " + (", ".join(gaps[:3]) or "see readiness")
    gate_failed = [r for r in (gate or {}).get("rules", []) if not r["passed"] and r["level"] in {"G", "P"}]
    if (gate or {}).get("grade") == "INCOMPATIBLE":
        must = [r for r in gate_failed if r["level"] == "G"]
        return "partial", "warning: key data not confirmed: " + "; ".join(
            f"{r['title']} ({r['reason']})" for r in must[:3])
    failing = [g["gate"] for g in (check or {}).get("gates", []) if g["status"] == "FAIL"]
    if failing:
        return "partial", (check or {}).get("reason") or ("validation checks failing: " + ", ".join(failing[:3]))
    if evaluation and evaluation["status"] in ("failed", "stale", "pending"):
        return "partial", evaluation["line"]
    if row["status"] == "PARTIAL" and not (gate or {}).get("grade"):
        return "partial", "runs; some optional data missing" + (f" ({', '.join(gaps[:2])})" if gaps else "")
    if not check:
        return "partial", "not validated yet"
    note = ""
    if (gate or {}).get("grade") == "DEGRADED":
        note = "; works with gaps: " + ", ".join(r["title"] for r in gate_failed[:3])
    if row["status"] == "PARTIAL" and gaps:
        note += "; optional data not connected: " + ", ".join(gaps[:3])
    if evaluation and evaluation["status"] == "passed":
        return "hydrated", f"all required data bound and validated; {evaluation['line']}{note}"
    return "hydrated", f"all required data bound and validated{note}"


def summary_row(entry: dict[str, Any], row: dict[str, Any], status: str, reason: str, path: str | None) -> dict[str, Any]:
    triggers = entry.get("triggers", [])[:2]
    invoke = "" if status == "blocked" else (f"`${row['skill']}` or ask: " + " / ".join(f'"{t}"' for t in triggers))
    return {"skill": row["skill"], "status": status, "reason": reason, "readiness": row.get("readiness"),
            "path": path, "invoke": invoke, "can_answer": entry.get("jobs_to_be_done", [])[:3],
            "examples": entry.get("triggers", [])[:3]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=SKILL_ROOT / "catalog" / "people.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skill", action="append", default=[])
    parser.add_argument("--include-partial", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.workspace, args.catalog, args.output, args.skill, args.include_partial), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
