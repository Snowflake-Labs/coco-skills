#!/usr/bin/env python3
"""v2 readiness: v1 deterministic scorer over bindings-derived inventory, plus
per-skill review load (low-confidence bindings + tunables awaiting the analyst).

Writes <workspace>/readiness.json in the v1 assessment shape with extra keys:
  review_bindings, review_tunables, unresolved_optional
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_readiness  # noqa: E402


def assess(workspace: Path, catalog: dict[str, Any]) -> dict[str, Any]:
    inventory = json.loads((workspace / "account_inventory.json").read_text(encoding="utf-8"))
    bindings = json.loads((workspace / "bindings.json").read_text(encoding="utf-8"))
    inv_index = score_readiness.index_inventory(inventory)
    domain_ok = inventory.get("domain_access", {}).get(catalog["domain"], True)
    skills = []
    for skill in catalog["skills"]:
        row = score_readiness.score_skill(skill, catalog["domain"], domain_ok, inv_index)
        review_b = 0
        unresolved_opt = []
        for need in skill["data_needs"]:
            b = bindings["needs"].get(need["key"], {})
            for fname in need["fields"]:
                f = b.get("fields", {}).get(fname, {})
                if f.get("status") == "needs_review":
                    review_b += 1
            if b.get("row_filter") and b["row_filter"].get("confidence") != "HIGH":
                review_b += 1
            if not need.get("required") and b.get("status") == "unresolved":
                unresolved_opt.append(need["key"])
        tuning_path = workspace / "tuning" / f"{skill['name']}.json"
        review_t = 0
        if tuning_path.exists():
            tuning = json.loads(tuning_path.read_text(encoding="utf-8"))
            review_t = sum(1 for t in tuning["tunables"].values() if t["status"] == "needs_review")
        row.update({"review_bindings": review_b, "review_tunables": review_t,
                    "unresolved_optional": unresolved_opt})
        skills.append(row)
    return {"skills": skills}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    result = assess(args.workspace, catalog)
    (args.workspace / "readiness.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for s in result["skills"]:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    print(json.dumps({"event": "readiness", **counts}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
