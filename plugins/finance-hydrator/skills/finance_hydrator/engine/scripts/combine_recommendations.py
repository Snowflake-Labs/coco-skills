#!/usr/bin/env python3
"""Combine business-fit scoring with data-readiness scoring. Stdlib only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def decide(business_fit: int, data_status: str) -> tuple[str, str, str]:
    if data_status == "BLOCKED" and business_fit >= 70:
        return "BLOCKED", "exclude", "High business fit, but required data is blocked."
    if business_fit < 40:
        return "NOT_RELEVANT", "exclude", "Low business fit for the collected profile."
    if business_fit < 70:
        if data_status in {"READY", "PARTIAL"}:
            return "OPTIONAL", "ask", "Some business fit; include only if the user wants it."
        return "NOT_RELEVANT", "exclude", "Low business fit and data is not ready."
    if data_status == "READY":
        return "RECOMMENDED", "include", "High business fit and all required data resolved."
    if data_status == "PARTIAL":
        return "ASK", "ask", "High business fit; required data resolved but optional enrichment is degraded."
    return "BLOCKED", "exclude", "Required data is not available."


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fit", type=Path, required=True)
    parser.add_argument("--readiness", type=Path, required=True)
    args = parser.parse_args()

    fit = load_json(args.fit.resolve())
    readiness = load_json(args.readiness.resolve())
    fit_index = {(item["domain"], item["skill"]): item for item in fit.get("skills", [])}

    out: list[dict[str, Any]] = []
    for item in readiness.get("skills", []):
        match = fit_index.get((item["domain"], item["skill"]), {})
        business_fit = int(match.get("business_fit", 0))
        data_status = item.get("status", "BLOCKED")
        final_status, recommendation, default_reason = decide(business_fit, data_status)
        reasons = list(match.get("fit_reasons", []))
        reasons.append(item.get("reason") or default_reason)
        combined = dict(item)
        combined.update({
            "business_fit": business_fit,
            "fit_status": match.get("fit_status", "LOW"),
            "fit_reasons": match.get("fit_reasons", []),
            "fit_components": match.get("fit_components", {}),
            "data_status": data_status,
            "final_status": final_status,
            "recommendation": recommendation,
            "reason": default_reason if final_status != "BLOCKED" else item.get("reason", default_reason),
            "combined_reasons": reasons,
        })
        out.append(combined)

    print(json.dumps({"skills": out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())