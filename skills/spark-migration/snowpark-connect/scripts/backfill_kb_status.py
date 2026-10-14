#!/usr/bin/env python3
"""Deterministically backfill the ``status`` field on ``data/kb_rules.json``.

``status`` records the Snowpark Connect (SCOS) support level of the API/SQL
construct a rule anchors on. It is NOT a judgment call — it is a pure
projection of a rule's provenance (``sources``) and ``kind``:

    * mined from the ``api-catalog`` (the authoritative SCOS API-compatibility
      catalog):
        - ``kind == "signature"``  -> ``"Partial"``     (API exists, but the
          documented signature is narrowed; the rule fires only on the
          unsupported kwarg slice)
        - otherwise                -> ``"Unsupported"`` (API is not implemented;
          a real call is a guaranteed runtime failure)
    * mined from any behavioral source (``behavioral-differences``,
      ``gaps-report``, ``csv`` test-cases, ``manual``, ``telemetry``):
        - status is left ABSENT, which downstream reads as ``None`` — i.e. a
          behavioral difference whose impact is context-dependent.

This mirrors the convention already shipped on the hardened trigger-KB: only
``api-catalog`` rules carry an explicit ``status`` key. Verified to reproduce
that KB's labels with zero mismatches.

The real fix belongs in the (internal, unshipped) KB mining pipeline; until it
emits ``status`` directly, this idempotent stopgap keeps the checked-in
artifact self-describing so the analyzer's decidability gate has the signal it
needs. Re-running is safe.

Usage:
    python scripts/backfill_kb_status.py            # edit in place
    python scripts/backfill_kb_status.py --check     # report only, no write
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

KB_PATH = Path(__file__).resolve().parent / "data" / "kb_rules.json"


def derive_status(rule: dict) -> str | None:
    """Return ``"Unsupported"`` / ``"Partial"`` / ``None`` from provenance."""
    sources = rule.get("sources") or []
    source_prefix = sources[0].split("::")[0] if sources else ""
    kind = rule.get("kind") or rule.get("trigger_kind")
    if source_prefix == "api-catalog":
        return "Partial" if kind == "signature" else "Unsupported"
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="report only, do not write")
    ap.add_argument("--path", default=str(KB_PATH))
    args = ap.parse_args()

    path = Path(args.path)
    rules = json.loads(path.read_text(encoding="utf-8"))

    counts: dict[str | None, int] = {"Unsupported": 0, "Partial": 0, None: 0}
    for rule in rules:
        status = derive_status(rule)
        counts[status] = counts.get(status, 0) + 1
        # Match the trigger-KB convention: only persist a status key when the
        # rule is api-catalog-classified; behavioral rules omit it (== None).
        if status is None:
            rule.pop("status", None)
        else:
            rule["status"] = status

    print(f"rules: {len(rules)}")
    print(f"  Unsupported : {counts['Unsupported']}")
    print(f"  Partial     : {counts['Partial']}")
    print(f"  None (behavioral, key omitted): {counts[None]}")

    if args.check:
        print("--check: no changes written.")
        return

    # json.dumps(indent=2) with default ensure_ascii reproduces the existing
    # file byte-for-byte (no trailing newline), so the diff is only the added
    # status keys.
    path.write_text(json.dumps(rules, indent=2), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
