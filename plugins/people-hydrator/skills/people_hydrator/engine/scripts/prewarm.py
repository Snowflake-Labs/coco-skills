#!/usr/bin/env python3
"""Pre-warm: everything that doesn't depend on the analyst's choices. Stdlib only.

Runs while the analyst reads the Welcome page (app: background job; chat: first
step). Writes <workspace>/prewarm.json:
  {surface, connection_ok, role, warehouse, available_roles, databases,
   sources (same shape as sources.json), skills, error}

  prewarm.py --workspace W [--connection C]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import detect_sources  # noqa: E402
from _sql import Runner, SqlError, make_runner  # noqa: E402

CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "people.json"
SYSTEM_DATABASES = {"SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA"}


def session_info(run: Runner) -> dict[str, Any]:
    row = run("select current_role() as role, current_warehouse() as warehouse, "
              "current_available_roles() as roles")[0]
    roles = row.get("ROLES")
    if isinstance(roles, str):
        try:
            roles = json.loads(roles)
        except json.JSONDecodeError:
            roles = [roles]
    return {"role": row.get("ROLE"), "warehouse": row.get("WAREHOUSE"), "available_roles": sorted(roles or [])}


def list_databases(run: Runner) -> list[dict[str, Any]]:
    out = []
    for r in run("show databases"):
        name = r.get("NAME") or r.get("name")
        kind = str(r.get("KIND") or r.get("kind") or "").upper()
        if not name or name.upper() in SYSTEM_DATABASES or name.upper().startswith("USER$") or kind == "APPLICATION":
            continue
        out.append({"name": name, "comment": r.get("COMMENT") or "", "kind": kind or "STANDARD"})
    return out


def skills_summary() -> list[dict[str, Any]]:
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    return [{"name": s["name"], "persona": s.get("persona", []), "business_value": s.get("business_value", "")}
            for s in cat["skills"]]


def prewarm(connection: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                              "connection": connection, "connection_ok": False, "databases": [],
                              "skills": skills_summary(), "error": None}
    try:
        run = make_runner(connection, 120)
        result.update(session_info(run))
        result["connection_ok"] = True
        result["databases"] = list_databases(run)
    except SqlError as exc:
        result["error"] = str(exc)[:300]
    src = detect_sources.detect(connection)
    result["surface"], result["sources"] = src["surface"], src["sources"]
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--connection")
    a = p.parse_args()
    res = prewarm(a.connection)
    a.workspace.mkdir(parents=True, exist_ok=True)
    (a.workspace / "prewarm.json").write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    (a.workspace / "sources.json").write_text(json.dumps({"surface": res["surface"], "sources": res["sources"]},
                                                         indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("surface", "connection_ok", "role", "warehouse", "error")
                      if k in res} | {"databases": [d["name"] for d in res["databases"]],
                                      "sources": [s["connector"] for s in res["sources"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
