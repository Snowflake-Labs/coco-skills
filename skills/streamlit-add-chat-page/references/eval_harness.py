#!/usr/bin/env python3
"""Regression harness for a Cortex Analyst semantic view.

Runs a fixed set of GOLD questions through the model and flags when a question
that used to work now returns no SQL or errors. Run it after any change to the
semantic view (dimensions, metrics, synonyms, verified queries, search services).

Usage:
    python eval_harness.py            # uses the active `cortex` connection
Each question is sent via the `cortex analyst query --view <fqn>` CLI, which
exercises the exact same semantic view the app uses.
"""
import json
import subprocess
import sys

SEMANTIC_VIEW = "<DB>.<SCHEMA>.<SEMANTIC_VIEW>"

# (question, substring that MUST appear in the generated SQL) — keep ~10-20.
GOLD = [
    ("top 5 <entities> by <metric>", "composite"),
    ("<entity> with the highest <metric>", "ORDER BY"),
    ("<metric> for the '<a real dimension value>' <dimension>", "<canonical value>"),
    # add the questions your users actually ask, especially ones you've fixed
]


def _ask(question: str) -> dict:
    out = subprocess.run(
        ["cortex", "analyst", "query", question, "--view", SEMANTIC_VIEW],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(out.stdout)
    except Exception:
        return {"result": out.stdout + out.stderr}


def main() -> int:
    failures = 0
    for q, needle in GOLD:
        res = _ask(q).get("result", "")
        ok = "```sql" in res and needle.lower() in res.lower()
        print(f"[{'PASS' if ok else 'FAIL'}] {q}")
        if not ok:
            failures += 1
            print(f"        expected SQL containing: {needle}")
    print(f"\n{len(GOLD) - failures}/{len(GOLD)} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
