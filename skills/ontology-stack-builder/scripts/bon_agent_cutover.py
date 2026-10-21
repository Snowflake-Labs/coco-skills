"""Path M step M6: create a Business Ontology-grounded copy of a stack's Cortex Agent
and compare its answers with the original.

The original agent is never modified. The copy keeps data and graph tools, drops
the tools whose job BON now does (ontology / metadata semantic models), and adds
native Business Ontology search.

Usage:
  python scripts/bon_agent_cutover.py create -c CONN --source DB.SC.AGENT --target DB.SC.NEW_AGENT \
      --domain "Soccer Ontology" [--drop TOOL ...] [--out /tmp/bon_migration]
  python scripts/bon_agent_cutover.py compare -c CONN --before DB.SC.AGENT --after DB.SC.NEW_AGENT \
      -q "question 1" -q "question 2" [--out /tmp/bon_migration]
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bon_migration_common import check_identifier, connect  # noqa: E402

BON_TOOL = "business_ontology"
# Tools whose semantic model describes the ontology / metadata layer that BON replaces.
REPLACED_HINTS = ("ontology", "metadata", "governance")


def split_fqn(fqn: str) -> tuple[str, str, str]:
    parts = fqn.split(".")
    if len(parts) != 3:
        raise SystemExit(f"Expected DB.SCHEMA.AGENT, got {fqn!r}")
    return tuple(check_identifier(p, "agent name part") for p in parts)  # type: ignore[return-value]


def describe_agent(cur, fqn: str) -> dict:
    db, sc, name = split_fqn(fqn)
    cur.execute(f"DESCRIBE AGENT {db}.{sc}.{name}")
    cols = [c[0].lower() for c in cur.description]
    return json.loads(dict(zip(cols, cur.fetchone()))["agent_spec"])


def replaced_tools(spec: dict) -> list[str]:
    """Analyst tools pointing at the stack's ontology/metadata semantic models.

    Only the tool name and the model's own name (file or semantic view, last path
    segment) are matched: database names such as ONTOLOGY_DB must not count.
    """
    out = []
    for t in spec.get("tools", []):
        ts = t["tool_spec"]
        if ts.get("type") != "cortex_analyst_text_to_sql":
            continue
        res = spec.get("tool_resources", {}).get(ts["name"], {})
        if res.get("semantic_model_file"):  # @DB.SC.STAGE/path/model.yml -> model
            model_name = Path(str(res["semantic_model_file"])).stem
        else:  # DB.SC.SEMANTIC_VIEW -> SEMANTIC_VIEW
            model_name = str(res.get("semantic_view") or "").split(".")[-1]
        if any(h in ts["name"].lower() or h in model_name.lower() for h in REPLACED_HINTS):
            out.append(ts["name"])
    return out


def build_spec(before: dict, domain: str, drop: list[str]) -> dict:
    spec = copy.deepcopy(before)
    spec["tools"] = [t for t in spec.get("tools", []) if t["tool_spec"]["name"] not in drop and t["tool_spec"]["name"] != BON_TOOL]
    spec["tools"].insert(0, {"tool_spec": {
        "type": "snowscope_search",
        "name": BON_TOOL,
        "description": (
            f"Search the governed Snowflake Business Ontology (domain '{domain}') for what business concepts mean: "
            "entity definitions, class hierarchy, relationships, derived terms and metrics, and which tables, views "
            "and columns implement each concept. Use for meaning and 'what is / how is X related to Y' questions. "
            "Do not use it for numbers."
        ),
    }})
    res = {k: v for k, v in spec.get("tool_resources", {}).items() if k not in drop}
    res[BON_TOOL] = {"corpus": "businessOntology"}
    spec["tool_resources"] = res
    # Private Preview: without this flag every call fails with
    # "businessOntology corpus is experimental and currently disabled".
    spec.setdefault("experimental", {})["EnableSnowscopeBusinessOntologySearch"] = True
    guidance = (
        "Business meaning comes from Business Ontology; numbers come from data tools.\n"
        f"- Use {BON_TOOL} for definitions, entity types, hierarchy, relationship types and which objects implement a "
        f"concept. Always start the {BON_TOOL} query with the domain name '{domain}' (the search spans every domain in "
        "the account) and ignore results from other domains.\n"
        f"- Replaced tools ({', '.join(drop) or 'none'}): their questions now go to {BON_TOOL}.\n"
        "- Use the remaining data and graph tools exactly as before. Call independent tools in parallel.\n\n"
    )
    instr = spec.setdefault("instructions", {})
    instr["orchestration"] = guidance + instr.get("orchestration", "")
    return spec


def cmd_create(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    db, sc, name = split_fqn(args.target)
    with connect(args.connection) as conn:
        cur = conn.cursor()
        before = describe_agent(cur, args.source)
        drop = args.drop if args.drop else replaced_tools(before)
        spec = build_spec(before, args.domain, drop)
        (out / "agent_spec_before.json").write_text(json.dumps(before, indent=2))
        (out / "agent_spec_after.json").write_text(json.dumps(spec, indent=2))
        body = json.dumps(spec)
        if "$$" in body:
            raise SystemExit("agent spec contains '$$'; cannot embed it in a dollar-quoted literal")
        comment = f"Business Ontology-grounded copy of {args.source} (ontology-stack-builder Path M)".replace("'", "")
        cur.execute(f"CREATE OR REPLACE AGENT {db}.{sc}.{name} COMMENT = '{comment}' FROM SPECIFICATION $${body}$$")
        print(cur.fetchone()[0])
    print(json.dumps({"target": args.target, "dropped": drop, "tools": [t["tool_spec"]["name"] for t in spec["tools"]]}, indent=2))
    return 0


def run_agent(conn, fqn: str, question: str, domain: str | None = None) -> dict:
    import requests  # optional dependency, only needed for compare

    db, sc, name = split_fqn(fqn)
    url = f"https://{conn.host}/api/v2/databases/{db}/schemas/{sc}/agents/{name}:run"
    headers = {"Authorization": f'Snowflake Token="{conn.rest.token}"',
               "Content-Type": "application/json", "Accept": "text/event-stream"}
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}]}
    t0 = time.time()
    r = requests.post(url, headers=headers, json=body, stream=True, timeout=600)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
    tools, tool_errors, text, event = [], [], [], None
    bon_hits = {"domain": 0, "other": 0}
    for raw in r.iter_lines(decode_unicode=True):
        if not raw:
            continue
        if raw.startswith("event:"):
            event = raw[6:].strip()
            continue
        if not raw.startswith("data:"):
            continue
        try:
            data = json.loads(raw[5:].strip())
        except json.JSONDecodeError:
            continue
        if event == "response.tool_use":
            tools.append(data.get("name"))
        elif event == "response.tool_result" and data.get("status") == "error":
            tool_errors.append({"tool": data.get("name"), "result": json.dumps(data.get("content"))[:300]})
        elif event == "response.tool_result" and data.get("name") == BON_TOOL and domain:
            # snowscope_search lists hits as "N. <Domain>.<Name> (<KIND>)"
            for c in data.get("content", []):
                msg = (c.get("json") or {}).get("message", "")
                for line in msg.splitlines():
                    head = line.strip().split(" ", 1)
                    if len(head) == 2 and head[0].rstrip(".").isdigit():
                        bon_hits["domain" if head[1].startswith(f"{domain}.") else "other"] += 1
        elif event == "response.text.delta":
            text.append(data.get("text", ""))
        elif event == "error":
            return {"error": json.dumps(data)[:300]}
    return {"tools": tools, "tool_errors": tool_errors, "bon_hits": bon_hits, "seconds": round(time.time() - t0, 1),
            "answer": "".join(text).strip()}


def cmd_compare(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results, lines = [], ["# Agent comparison", "", f"Before: `{args.before}`  After: `{args.after}`", ""]
    with connect(args.connection) as conn:
        for q in args.question:
            lines += [f"## {q}", ""]
            for label, fqn in (("before", args.before), ("after", args.after)):
                res = run_agent(conn, fqn, q, args.domain if label == "after" else None)
                results.append({"question": q, "agent": label, **res})
                hits = res.get("bon_hits") or {}
                lines += [f"**{label}**: tools `{res.get('tools')}`, {res.get('seconds')}s"
                          + (f", BON hits from '{args.domain}': {hits.get('domain')} (other domains: {hits.get('other')})"
                             if label == "after" and args.domain and BON_TOOL in (res.get("tools") or []) else "")
                          + (f", tool errors `{res['tool_errors']}`" if res.get("tool_errors") else "")
                          + (f", error `{res['error']}`" if res.get("error") else ""), "",
                          (res.get("answer") or "")[:1500], ""]
    (out / "agent_compare.json").write_text(json.dumps(results, indent=2))
    (out / "agent_compare.md").write_text("\n".join(lines))
    bon_errors = [r for r in results if r["agent"] == "after"
                  and any(e["tool"] == BON_TOOL for e in r.get("tool_errors", []))]
    other_errors = [{"question": r["question"], "agent": r["agent"], "tools": [e["tool"] for e in r["tool_errors"]]}
                    for r in results if r.get("tool_errors") and r not in bon_errors]
    # Only BON tool errors fail the comparison. Data-tool errors (e.g. a semantic view over a
    # dropped table) usually hit both agents and are reported separately.
    # A BON call that only returned other domains' nodes means the migrated domain was not used
    # (typically search-index lag right after creation): the answer is not evidence of migration.
    not_grounded = [r["question"] for r in results if r["agent"] == "after" and args.domain
                    and BON_TOOL in (r.get("tools") or []) and not (r.get("bon_hits") or {}).get("domain")]
    print(json.dumps({"questions": len(args.question), "bon_tool_errors": bon_errors,
                      "not_grounded_in_domain": not_grounded,
                      "other_tool_errors": other_errors, "report": str(out / "agent_compare.md")}, indent=2))
    return 1 if (bon_errors or not_grounded) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--connection", "-c", required=True)
    c.add_argument("--source", required=True, help="existing agent DB.SCHEMA.NAME (not modified)")
    c.add_argument("--target", required=True, help="new agent DB.SCHEMA.NAME")
    c.add_argument("--domain", required=True)
    c.add_argument("--drop", action="append", default=[], help="tool to remove; default: ontology/metadata analyst tools")
    c.add_argument("--out", default="/tmp/bon_migration")
    c.set_defaults(fn=cmd_create)
    k = sub.add_parser("compare")
    k.add_argument("--connection", "-c", required=True)
    k.add_argument("--before", required=True)
    k.add_argument("--after", required=True)
    k.add_argument("--question", "-q", action="append", required=True)
    k.add_argument("--domain", help="migrated BON domain; fail when BON answers come only from other domains")
    k.add_argument("--out", default="/tmp/bon_migration")
    k.set_defaults(fn=cmd_compare)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
