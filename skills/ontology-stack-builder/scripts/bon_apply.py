"""Apply export_to_bon.py output to Snowflake Business Ontology.

Follows the BON backend model: draft, then approve ONLY the items this run
drafted (never the account-wide no-argument APPROVE_ALL forms), then let
bon_reconcile.py read everything back. Idempotent: nodes, edges and
representations that already exist in the domain are skipped, so a re-run only
writes what is missing.

Usage:
  python scripts/bon_apply.py --connection C --input-dir /tmp/bon_migration \
      --domain "Stock Market" [--save-as-draft] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bon_migration_common import (  # noqa: E402
    BON_ITEM_KINDS,
    BON_RELATIONSHIP_TYPES,
    asset_key,
    call_system,
    connect,
    dumps,
    norm_key,
)

APPROVE_CHUNK = 100
METRIC_ONLY_FIELDS = ("formula", "formulaSource", "exclusions")


class Applier:
    def __init__(self, cur, domain: str, dry_run: bool, log: list):
        self.cur, self.domain, self.dry_run, self.log = cur, domain, dry_run, log

    def call(self, fn: str, *args, write: bool = True) -> dict:
        # SYSTEM$ glossary functions raise "inputs may not be null" for NULL
        # arguments; optional trailing arguments must be omitted instead.
        while args and args[-1] is None:
            args = args[:-1]
        if self.dry_run and write:
            out = {"error": False, "dryRun": True}
        else:
            try:
                out = call_system(self.cur, fn, *args)
            except Exception as exc:  # keep going; the failure is reported and reconcile is the truth
                out = {"error": True, "errorMessage": f"{type(exc).__name__}: {exc}"}
        self.log.append({"ts": time.time(), "fn": fn, "args": list(args), "response": out})
        return out

    # ------------------------------------------------------------------ domain
    def ensure_domain(self, description: str) -> str | None:
        summary = self.call("SYSTEM$GET_GLOSSARY_SUMMARY", write=False)
        if summary.get("error"):
            raise SystemExit(f"Business Ontology unavailable: {summary.get('errorMessage')} "
                             "(is FEATURE_BUSINESS_GLOSSARY enabled on this account?)")
        for d in summary.get("domains", []):
            if d.get("name") == self.domain:
                return str(d["domainId"])
        args = (self.domain, description) if description else (self.domain,)
        out = self.call("SYSTEM$CREATE_GLOSSARY_DOMAIN", *args)
        if out.get("error"):
            raise SystemExit(f"Cannot create domain {self.domain!r}: {out.get('errorMessage')}")
        return str(out.get("domainId")) if out.get("domainId") else None

    # ------------------------------------------------------------------ reads
    def existing_terms(self) -> dict[str, dict]:
        """name(normalised) -> {termId, status} for ACTIVE and DRAFT terms in the domain."""
        found: dict[str, dict] = {}
        lst = self.call("SYSTEM$GET_GLOSSARY_TERM_LIST", self.domain, "TERM", "", write=False)
        for t in lst.get("terms", []) or []:
            if t.get("domain") == self.domain:
                found[norm_key(t["name"])] = {"termId": str(t["termId"]), "status": "ACTIVE", "term": t}
        drafts = self.call("SYSTEM$GET_GLOSSARY_TERM_DRAFTS", self.domain, write=False)
        for t in drafts.get("terms", []) or []:
            if t.get("domain") == self.domain:
                found.setdefault(norm_key(t["name"]), {"termId": str(t["termId"]), "status": "DRAFT", "term": t})
        return found

    def graph(self) -> dict:
        g = self.call("SYSTEM$GET_GLOSSARY_GRAPH", self.domain, write=False)
        return {} if g.get("error") else g


def _term_payload(c: dict, domain_id: str | None, domain: str) -> dict:
    kind = c.get("itemKind", "TERM")
    if kind not in BON_ITEM_KINDS:
        raise ValueError(f"{c['name']}: invalid itemKind {kind}")
    p = {"name": c["name"], "itemKind": kind, "description": c.get("description") or ""}
    if domain_id:
        p["domainId"] = domain_id
    else:
        p["domainName"] = domain
    syn = [s if isinstance(s, str) else s.get("text") for s in (c.get("synonyms") or [])]
    syn = [s for s in syn if s][:5]
    if syn:
        p["synonyms"] = syn
    if kind == "METRIC":  # metric-only fields reject the whole payload on any other kind
        for f in METRIC_ONLY_FIELDS:
            if c.get(f):
                p[f] = c[f]
    return p


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _ref_key(term_id: str, ref: dict) -> tuple:
    return asset_key(str(term_id), ref)


def apply(cur, input_dir: Path, domain: str, save_as_draft: bool, dry_run: bool) -> dict:
    candidates = json.loads((input_dir / "candidates.json").read_text())
    relationships = json.loads((input_dir / "relationships.json").read_text())
    associations = json.loads((input_dir / "associations.json").read_text())
    manifest = json.loads((input_dir / "export_manifest.json").read_text())
    if manifest.get("domain") != domain:
        raise SystemExit(f"--domain {domain!r} does not match the export ({manifest.get('domain')!r}); re-run export_to_bon.py")

    log: list = []
    ap = Applier(cur, domain, dry_run, log)
    summary = {"domain": domain, "dry_run": dry_run, "save_as_draft": save_as_draft,
               "nodes": {"drafted": 0, "approved": 0, "updated": 0, "existing": 0, "failed": []},
               "relationships": {"drafted": 0, "approved": 0, "existing": 0, "skipped": [], "failed": []},
               "representations": {"drafted": 0, "approved": 0, "existing": 0, "skipped": [], "failed": []}}

    domain_id = ap.ensure_domain(manifest.get("domain_description", ""))
    existing = ap.existing_terms()

    # Updates come from the export's delta detection (source row changed since last run).
    update_names = {o["bon"]["name"] for o in manifest.get("objects", [])
                    if o.get("action") == "update" and o.get("bon") and "name" in o["bon"] and "itemKind" in o["bon"]}

    # ---------------------------------------------------------------- nodes
    to_approve: list[str] = []
    term_ids: dict[str, str] = {}
    for c in candidates:
        key = norm_key(c["name"])
        if key in existing:
            ex = existing[key]
            term_ids[key] = ex["termId"]
            summary["nodes"]["existing"] += 1
            if ex["status"] == "DRAFT" and not save_as_draft:
                to_approve.append(ex["termId"])
            if c["name"] in update_names and ex["status"] == "ACTIVE":
                patch = {"description": c.get("description") or ""}
                if c.get("synonyms"):
                    patch["synonyms"] = c["synonyms"][:5]
                if c.get("itemKind") == "METRIC":
                    for f in ("formula", "formulaSource"):
                        if c.get(f):
                            patch[f] = c[f]
                out = ap.call("SYSTEM$UPDATE_GLOSSARY_TERM", ex["termId"], dumps(patch))
                if out.get("error"):
                    summary["nodes"]["failed"].append({"name": c["name"], "op": "update", "error": out.get("errorMessage")})
                else:
                    summary["nodes"]["updated"] += 1
            continue
        out = ap.call("SYSTEM$DRAFT_GLOSSARY_TERM", dumps(_term_payload(c, domain_id, domain)))
        if out.get("error"):
            summary["nodes"]["failed"].append({"name": c["name"], "op": "draft", "error": out.get("errorMessage")})
            continue
        summary["nodes"]["drafted"] += 1
        tid = str(out.get("termId") or f"dry:{c['name']}")
        term_ids[key] = tid
        to_approve.append(tid)

    if not save_as_draft and not dry_run:
        for chunk in _chunks(to_approve, APPROVE_CHUNK):
            out = ap.call("SYSTEM$APPROVE_ALL_GLOSSARY_TERMS", dumps(chunk))
            summary["nodes"]["approved"] += int(out.get("approved", 0) or 0)
            for r in out.get("results", []) or []:
                if r.get("status") == "FAILED":
                    summary["nodes"]["failed"].append({"termId": r.get("requested"), "op": "approve", "error": r.get("error")})
    if save_as_draft:
        summary["note"] = ("Saved as drafts. Relationships and representations need ACTIVE endpoints, so they were "
                           "not written; approve the nodes in Snowsight, then re-run without --save-as-draft.")
        return {"summary": summary, "log": log}

    # Relationships and representations need ACTIVE endpoints: re-read state after approval.
    g = ap.graph() if not dry_run else {}
    active_ids = {str(t["termId"]) for t in g.get("terms", []) or []}
    if not dry_run and not g:
        # GET_GLOSSARY_GRAPH can fail on large accounts (error 300002): fall back to the term list.
        # Existing edges/representations are then unknown; BON CREATE/DRAFT calls are idempotent.
        summary["graph_fallback"] = True
        for key, t in ap.existing_terms().items():
            if t["status"] == "ACTIVE":
                active_ids.add(t["termId"])
                term_ids.setdefault(key, t["termId"])
    if not dry_run and g:
        # Prefer ids the graph reports (covers terms approved by another session meanwhile).
        for t in g.get("terms", []) or []:
            term_ids.setdefault(norm_key(t["name"]), str(t["termId"]))

    # ---------------------------------------------------------------- relationships
    existing_edges = {(str(r["sourceTermId"]), str(r["targetTermId"]), r["relationshipType"])
                      for r in g.get("relationships", []) or []}
    rel_items = []
    for e in relationships:
        t = e["type"]
        if t not in BON_RELATIONSHIP_TYPES:
            summary["relationships"]["skipped"].append({**e, "reason": "invalid BON type (R-00)"})
            continue
        s_id, d_id = term_ids.get(norm_key(e["source"])), term_ids.get(norm_key(e["target"]))
        if not s_id or not d_id or (not dry_run and (s_id not in active_ids or d_id not in active_ids)):
            summary["relationships"]["skipped"].append({**e, "reason": "endpoint not ACTIVE"})
            continue
        if (s_id, d_id, t) in existing_edges:
            summary["relationships"]["existing"] += 1
            continue
        label = e.get("label") if t == "CUSTOM" else None  # label is rejected on every non-CUSTOM type
        out = ap.call("SYSTEM$DRAFT_GLOSSARY_RELATIONSHIP", s_id, d_id, t, label)
        if out.get("error"):
            summary["relationships"]["failed"].append({**e, "op": "draft", "error": out.get("errorMessage")})
            continue
        summary["relationships"]["drafted"] += 1
        rel_items.append({"source": s_id, "target": d_id, "type": t})
    for chunk in _chunks(rel_items, APPROVE_CHUNK):
        out = ap.call("SYSTEM$APPROVE_ALL_GLOSSARY_RELATIONSHIPS", dumps(chunk))
        summary["relationships"]["approved"] += int(out.get("approved", 0) or 0)
        for r in out.get("results", []) or []:
            # Some drafts auto-approve; approve then reports them as skipped/failed. Reconcile is the truth.
            if r.get("status") == "FAILED":
                summary["relationships"]["failed"].append({"item": r, "op": "approve", "error": r.get("error")})

    # ---------------------------------------------------------------- representations
    existing_refs = {_ref_key(a["termId"], a) for a in g.get("associations", []) or []}
    asset_items = []
    for a in associations:
        tid = term_ids.get(norm_key(a["termName"]))
        ref = a["assetRef"]
        if not tid or (not dry_run and tid not in active_ids):
            summary["representations"]["skipped"].append({**a, "reason": "node not ACTIVE"})
            continue
        if _ref_key(tid, ref) in existing_refs:
            summary["representations"]["existing"] += 1
            continue
        out = ap.call("SYSTEM$DRAFT_GLOSSARY_ASSET", tid, dumps(ref), a.get("role", "DESCRIBES"))
        if out.get("error"):
            summary["representations"]["failed"].append({**a, "op": "draft", "error": out.get("errorMessage")})
            continue
        summary["representations"]["drafted"] += 1
        asset_items.append({"term": tid, **ref})
    for chunk in _chunks(asset_items, APPROVE_CHUNK):
        out = ap.call("SYSTEM$APPROVE_ALL_GLOSSARY_ASSETS", dumps(chunk))
        summary["representations"]["approved"] += int(out.get("approved", 0) or 0)
        for r in out.get("results", []) or []:
            if r.get("status") == "FAILED":
                summary["representations"]["failed"].append({"item": r, "op": "approve", "error": r.get("error")})

    summary["note"] = "Write counts are not proof of persistence. Run bon_reconcile.py next."
    return {"summary": summary, "log": log}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--connection", "-c", required=True)
    ap.add_argument("--input-dir", default="/tmp/bon_migration")
    ap.add_argument("--domain", required=True)
    ap.add_argument("--save-as-draft", action="store_true", help="draft nodes only; do not approve")
    ap.add_argument("--dry-run", action="store_true", help="read state and print what would be written")
    args = ap.parse_args(argv)

    input_dir = Path(args.input_dir)
    with connect(args.connection) as conn:
        result = apply(conn.cursor(), input_dir, args.domain, args.save_as_draft, args.dry_run)
    log_name = "apply_log.dry_run.json" if args.dry_run else "apply_log.json"
    (input_dir / log_name).write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result["summary"], indent=2, default=str))
    s = result["summary"]
    return 1 if (s["nodes"]["failed"] or s["relationships"]["failed"] or s["representations"]["failed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
