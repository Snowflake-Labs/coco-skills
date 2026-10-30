#!/usr/bin/env python3
"""Evaluate stage: run a skill's golden cases against its draft build, then decide its status.

  evaluate.py plan    --workspace W --skill S [--skill S2] [--all] [--draft W/draft]
  evaluate.py record  --workspace W --skill S --case N --result pass|fail|not_tested
                      [--failed "G3: ..."] [--note "..."]
  evaluate.py summary --workspace W [--skill S ...] [--draft W/draft]

The agent runs each planned case by acting as the draft skill (its SKILL.md, customer
configuration and hydration hints) on the customer's data, aggregates only, and scores the
answer against the case's expected behavior and guard checks (defined in eval_plan.md).
This script only does the bookkeeping, so results are reproducible:

W/evaluation.json: {skill: {fingerprint, cases: [{id, title, input, expected, guards,
status, failed, note, at}], at}}. A draft that changed after its cases ran is "stale".
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[2]
CATALOG = SKILL_ROOT / "catalog" / "people.json"
TEMPLATES = SKILL_ROOT / "templates"
DEFAULT_CASES = 3
RESULTS = ("pass", "fail", "not_tested")
DRAFT_FILES = ("SKILL.md", "hydration_hints.yaml", "tuning.json")

CASE_RE = re.compile(r"^##\s+Case\s+(\d+)\s*[:.\-\u2014]?\s*(.*)$", re.M)
SECTION_RE = re.compile(r"^##\s", re.M)
GUARD_RE = re.compile(r"\bG(\d+)\b(?:\s*[\u2013\-]\s*G(\d+))?")
LABEL_RE = re.compile(r"^\*\*([^*]+?):?\*\*:?\s*(.*)$")
PART_RE = re.compile(r"^\*\*(Part\s+[A-Z])\b[^*]*\*\*", re.M)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load(ws: Path) -> dict[str, Any]:
    try:
        return json.loads((ws / "evaluation.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(ws: Path, state: dict[str, Any]) -> None:
    (ws / "evaluation.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def eval_dir(skill: str, catalog: Path = CATALOG, templates: Path = TEMPLATES) -> Path | None:
    """The skill's golden-case folder from its catalog `eval_dir`, or None."""
    for entry in json.loads(catalog.read_text(encoding="utf-8"))["skills"]:
        if entry["name"] == skill and entry.get("eval_dir"):
            path = templates / entry.get("source_plugin", "people") / entry["eval_dir"]
            return path if (path / "golden_cases.md").is_file() else None
    return None


def guard_labels(text: str) -> list[str]:
    """G-labels in text, expanding ranges such as 'G1-G5'."""
    out: list[str] = []
    for a, b in GUARD_RE.findall(text):
        for n in range(int(a), int(b or a) + 1):
            label = f"G{n}"
            if label not in out:
                out.append(label)
    return out


def guard_definitions(plan_text: str) -> dict[str, str]:
    """G-label -> plain definition, from table rows like '| G1 | name | criteria |'."""
    defs: dict[str, str] = {}
    for line in plan_text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and re.fullmatch(r"G\d+", cells[0]):
            defs[cells[0]] = " - ".join(c for c in cells[1:3] if c)
    return defs


def _blocks(body: str) -> dict[str, list[str]]:
    """Group a case body by its bold labels (**Input:**, **Expected behavior:**, ...)."""
    blocks: dict[str, list[str]] = {}
    current, fenced = "_intro", False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else LABEL_RE.match(line.strip())
        if m:
            current = m.group(1).strip().lower()
            blocks.setdefault(current, [])
            if m.group(2).strip():
                blocks[current].append(m.group(2).strip())
            continue
        if line.strip():
            blocks.setdefault(current, []).append(line.rstrip())
    return blocks


def _pick(blocks: dict[str, list[str]], prefix: str) -> list[str]:
    for key, lines in blocks.items():
        if key.startswith(prefix):
            return lines
    return []


def parse_cases(text: str) -> list[dict[str, Any]]:
    """Every '## Case N:' section: id, title, input prompt, expected behavior, guards."""
    cases = []
    starts = list(CASE_RE.finditer(text))
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        nxt = SECTION_RE.search(text, m.end())  # stop at the next non-case '## ' heading
        if nxt and nxt.start() < end:
            end = nxt.start()
        blocks = _blocks(text[m.end():end])
        bullets = lambda ls: [re.sub(r"^\s*[-*]\s*(\[ \]\s*)?", "", ln).strip() for ln in ls
                              if ln.strip().startswith(("-", "*"))]
        guard_text = " ".join(_pick(blocks, "guard"))
        body = text[m.end():end]
        cases.append({
            "id": int(m.group(1)),
            "title": m.group(2).strip(),
            "input": " ".join(ln.strip() for ln in _pick(blocks, "input")).strip(),
            "expected": bullets(_pick(blocks, "expected")),
            "guards": guard_labels(guard_text),
            # multi-part cases (Part A/B/C) are run part by part; each part's key assertions apply
            "parts": PART_RE.findall(body),
        })
    return cases


def fingerprint(draft_skill: Path) -> str | None:
    """Hash of the draft skill's files; changes when bindings, tuning or rules change."""
    h = hashlib.sha256()
    found = False
    for name in DRAFT_FILES:
        f = draft_skill / name
        if f.is_file():
            h.update(name.encode() + b"\0" + f.read_bytes())
            found = True
    return h.hexdigest()[:16] if found else None


def plan(ws: Path, skills: list[str], all_cases: bool = False, draft: Path | None = None,
         catalog: Path = CATALOG, templates: Path = TEMPLATES) -> dict[str, Any]:
    draft = draft or ws / "draft"
    state = load(ws)
    for skill in skills:
        folder = eval_dir(skill, catalog, templates)
        if folder is None:
            state[skill] = {"fingerprint": fingerprint(draft / "skills" / skill), "cases": [],
                            "guards": {}, "at": now(), "reason": "no golden cases for this skill"}
            continue
        cases = parse_cases((folder / "golden_cases.md").read_text(encoding="utf-8"))
        plan_path = folder / "eval_plan.md"
        guards = guard_definitions(plan_path.read_text(encoding="utf-8")) if plan_path.is_file() else {}
        chosen = cases if all_cases else cases[:DEFAULT_CASES]
        state[skill] = {
            "fingerprint": fingerprint(draft / "skills" / skill),
            "cases": [{**c, "status": "pending", "failed": [], "note": None, "at": None} for c in chosen],
            "guards": {g: guards.get(g, "") for c in chosen for g in c["guards"]},
            "at": now(),
        }
    save(ws, state)
    return {s: state[s] for s in skills}


def record(ws: Path, skill: str, case: int, result: str, failed: list[str] | None = None,
           note: str | None = None) -> dict[str, Any]:
    if result not in RESULTS:
        raise SystemExit(f"result must be one of {', '.join(RESULTS)}")
    state = load(ws)
    entry = state.get(skill)
    if not entry:
        raise SystemExit(f"no evaluation planned for {skill}; run `evaluate.py plan` first")
    for c in entry["cases"]:
        if c["id"] == case:
            c.update(status=result, failed=list(failed or []) if result == "fail" else [],
                     note=note, at=now())
            save(ws, state)
            return c
    raise SystemExit(f"{skill} has no planned case {case} (planned: "
                     f"{', '.join(str(c['id']) for c in entry['cases']) or 'none'})")


def skill_status(entry: dict[str, Any] | None, current_fingerprint: str | None) -> dict[str, Any]:
    """passed | failed | not_tested | stale | pending, with counts and a plain line."""
    if not entry:
        return {"status": "not_tested", "passed": 0, "run": 0, "line": "golden cases not run"}
    cases = entry.get("cases", [])
    run = [c for c in cases if c["status"] in ("pass", "fail")]
    passed = [c for c in run if c["status"] == "pass"]
    failed = [c for c in run if c["status"] == "fail"]
    pending = [c for c in cases if c["status"] == "pending"]
    if not cases or (not run and not pending):
        reason = entry.get("reason") or "no case could run on this data"
        return {"status": "not_tested", "passed": 0, "run": 0, "line": f"golden cases not run ({reason})"}
    if entry.get("fingerprint") != current_fingerprint and run:
        return {"status": "stale", "passed": len(passed), "run": len(run),
                "line": "the skill changed after its golden cases ran; run Evaluate again"}
    if pending:
        return {"status": "pending", "passed": len(passed), "run": len(run),
                "line": f"{len(pending)} golden case(s) still to run"}
    if failed:
        first = failed[0]
        why = "; ".join(first["failed"]) or first.get("note") or "see evaluation"
        return {"status": "failed", "passed": len(passed), "run": len(run),
                "line": f"{len(passed)} of {len(run)} golden cases passed; Case {first['id']} failed: {why}"}
    return {"status": "passed", "passed": len(passed), "run": len(run),
            "line": f"{len(passed)} of {len(run)} golden cases passed"}


def summary(ws: Path, skills: list[str] | None = None, draft: Path | None = None) -> dict[str, Any]:
    draft = draft or ws / "draft"
    state = load(ws)
    out = {}
    for skill in skills or sorted(state):
        out[skill] = skill_status(state.get(skill), fingerprint(draft / "skills" / skill))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("plan")
    pp.add_argument("--workspace", type=Path, required=True)
    pp.add_argument("--skill", action="append", required=True)
    pp.add_argument("--all", action="store_true", help="run every golden case, not just the first 3")
    pp.add_argument("--draft", type=Path)
    pr = sub.add_parser("record")
    pr.add_argument("--workspace", type=Path, required=True)
    pr.add_argument("--skill", required=True)
    pr.add_argument("--case", type=int, required=True)
    pr.add_argument("--result", required=True, choices=RESULTS)
    pr.add_argument("--failed", action="append", default=[], help="a failed check, e.g. 'G3: ...'")
    pr.add_argument("--note")
    ps = sub.add_parser("summary")
    ps.add_argument("--workspace", type=Path, required=True)
    ps.add_argument("--skill", action="append")
    ps.add_argument("--draft", type=Path)
    a = p.parse_args()
    if a.cmd == "plan":
        result = plan(a.workspace, a.skill, a.all, a.draft)
    elif a.cmd == "record":
        result = record(a.workspace, a.skill, a.case, a.result, a.failed, a.note)
    else:
        result = summary(a.workspace, a.skill, a.draft)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
