#!/usr/bin/env python3
"""Per-skill mapping review: which table serves each data need, why, and what still needs input.

  mapping_review.py --workspace W [--skill S ...]   -> prints and writes W/mapping_review.json

The analyst checks this after Bind and corrects it in plain words ("use HM_FEEDBACK_SURVEY
for hiring manager sentiment"); fix_ledger turns that into a validated `bind_table` change.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discover_topics as T  # noqa: E402


def short(fqn: str | None) -> str | None:
    return fqn.rsplit(".", 1)[-1] if fqn else None


def need_row(key: str, spec: dict[str, Any], b: dict[str, Any], unit: dict[str, Any]) -> dict[str, Any]:
    label = T.need_label(spec)
    reasons = next((c.get("reasons", []) for c in unit.get("candidates", []) if c["fqn"] == b.get("fqn")), [])
    if b.get("fqn") and b.get("status") != "unresolved":
        cols = {f: v["column"] for f, v in b.get("fields", {}).items() if v.get("column")}
        review = [f for f, v in b.get("fields", {}).items() if v.get("status") == "needs_review"]
        return {"need": key, "label": label, "required": bool(spec.get("required")), "status": "found",
                "table": b["fqn"], "columns": cols, "confidence": "check" if review or b.get("status") == "needs_review"
                else "high", "check_fields": review, "why": reasons[:3]}
    sug = b.get("suggestion") or next(({"fqn": c["fqn"], "missing": []} for c in unit.get("candidates", [])), None)
    return {"need": key, "label": label, "required": bool(spec.get("required")), "status": "needs_input",
            "table": None, "suggestion": sug and {"table": sug["fqn"], "missing": sug.get("missing", [])},
            "why": ["no table has the data this needs"] if not sug else
            [f"closest is {short(sug['fqn'])}" + (f", but it has no {', '.join(sug['missing'])}" if sug.get("missing") else "")]}


def review(ws: Path, skills: list[str] | None = None) -> dict[str, Any]:
    catalog = T.load(T.CATALOG_DIR / "people.json")
    bindings = (T.load(ws / "bindings.json", {}) or {}).get("needs", {})
    units = {t["id"]: t for t in (T.load(ws / "topics.json", {}) or {}).get("topics", [])}
    skills = skills or (T.load(ws / "topics.json", {}) or {}).get("skills") or []
    out: dict[str, Any] = {"skills": {}}
    for s in catalog["skills"]:
        if s["name"] not in skills:
            continue
        rows = [need_row(n["key"], n, bindings.get(n["key"], {}), units.get(n["key"], {}))
                for n in s["data_needs"] if not n["key"].endswith(".semantic_view")]
        rows.sort(key=lambda r: (r["status"] != "needs_input" or not r["required"], not r["required"], r["label"]))
        out["skills"][s["name"]] = {
            "tables": sorted({r["table"] for r in rows if r["table"]}),
            "needs": rows,
            "needs_input": [r["label"] for r in rows if r["status"] == "needs_input" and r["required"]],
            "check": [r["label"] for r in rows if r.get("confidence") == "check"]}
    (ws / "mapping_review.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--skill", action="append", default=[])
    a = p.parse_args()
    print(json.dumps(review(a.workspace, a.skill or None), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
