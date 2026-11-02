#!/usr/bin/env python3
"""Detect business-context sources available to this analyst. Stdlib only.

Checks both places MCP can live and maps each server to a registry connector:
  local    MCP servers configured in the analyst's Cortex Code (`cortex mcp list`)
  account  Snowflake external MCP servers set up by an admin (SHOW EXTERNAL MCP SERVERS)
Always offers the upload fallback (`local_files`).

Writes <workspace>/sources.json:
  {"sources": [{"connector", "label", "origin", "name", "usable_here", "note"}], "surface": "local|snowsight"}

`usable_here` reflects what the CURRENT surface can call: a local Cortex Code
agent uses local MCP servers; account connectors are callable from Snowsight
agents (Cortex Agents / CoWork). Usage: detect_sources.py --workspace W --connection C
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sql import SqlError, make_runner  # noqa: E402

REGISTRY = Path(__file__).resolve().parents[2] / "catalog" / "connectors.json"
URL_HINTS = {  # account connectors are best identified by URL
    "confluence": ["atlassian.com", "confluence"], "jira": ["atlassian.com", "jira"],
    "google_drive": ["gdrive.mcp.snowflake.com", "drive"], "email": ["gmail.mcp.snowflake.com", "outlook"],
    "sharepoint": ["sharepoint", "graph.microsoft.com"], "slack": ["slack"], "teams": ["teams"],
}


def registry() -> dict[str, Any]:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def match_connectors(text: str) -> list[str]:
    low = text.lower()
    hits = []
    for c in registry()["connectors"]:
        pats = c.get("detect", []) + URL_HINTS.get(c["id"], [])
        if any(p in low for p in pats):
            hits.append(c["id"])
    return hits


def local_servers() -> list[dict[str, Any]]:
    if not shutil.which("cortex"):
        return []
    try:
        out = subprocess.run(["cortex", "mcp", "list"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in out.splitlines():
        m = re.match(r"^\s*[-*•]?\s*([A-Za-z0-9_.@/-]+)", line)
        if m and match_connectors(line) and m.group(1).lower() not in {"url", "name", "command", "status", "type"}:
            found.append({"name": m.group(1), "text": line.strip()})
    return found


def account_servers(connection: str | None) -> tuple[list[dict[str, Any]], str | None]:
    try:
        rows = make_runner(connection, 120)("show external mcp servers in account")
    except SqlError as exc:
        return [], str(exc)[:200]
    out = []
    for r in rows:
        name = f"{r.get('DATABASE_NAME')}.{r.get('SCHEMA_NAME')}.{r.get('NAME')}"
        text = " ".join(str(v) for v in r.values() if v is not None)
        out.append({"name": name, "text": text, "enabled": str(r.get("STATE", r.get("ENABLED", "ENABLED"))).upper()})
    return out, None


def detect(connection: str | None) -> dict[str, Any]:
    surface = "local" if shutil.which("cortex") and shutil.which("snow") else "snowsight"
    labels = {c["id"]: c["label"] for c in registry()["connectors"]}
    sources: list[dict[str, Any]] = []
    for s in local_servers():
        for cid in match_connectors(s["text"]):
            sources.append({"connector": cid, "label": labels[cid], "origin": "local", "name": s["name"],
                            "usable_here": surface == "local", "note": "configured in your Cortex Code"})
    acct, err = account_servers(connection)
    for s in acct:
        for cid in match_connectors(s["text"]):
            disabled = "DISABLED" in s["enabled"] or s["enabled"] == "FALSE"
            sources.append({"connector": cid, "label": labels[cid], "origin": "account", "name": s["name"],
                            "usable_here": surface == "snowsight" and not disabled,
                            "note": "admin-configured Snowflake connector"
                                    + (" (disabled)" if disabled else "")
                                    + ("" if surface == "snowsight" else " — usable from Snowsight chat; "
                                       "to use it here, add the same tool to your local Cortex Code")})
    sources.append({"connector": "local_files", "label": labels["local_files"], "origin": "upload",
                    "name": "doc exports", "usable_here": True,
                    "note": "upload wiki / Jira / Slack exports (markdown, txt, JSON) — works everywhere"})
    seen, unique = set(), []
    for src in sources:
        key = (src["connector"], src["origin"])
        if key not in seen:
            seen.add(key)
            unique.append(src)
    return {"surface": surface, "sources": unique, "account_check_error": err}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--connection")
    a = p.parse_args()
    result = detect(a.connection)
    a.workspace.mkdir(parents=True, exist_ok=True)
    (a.workspace / "sources.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
