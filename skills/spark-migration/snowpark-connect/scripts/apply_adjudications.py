#!/usr/bin/env python3
"""Merge Phase 1.1 adjudicator verdict sidecars into analysis.json.

Parallel adjudicator workers must NOT write ``analysis.json`` concurrently (they
would race on one file). Instead each worker writes a per-chunk verdict sidecar
``Adjudication/chunk_<id>.json``; this script is run ONCE by the coordinator
after all waves complete and is the single writer of the verdicts.

Each sidecar is a JSON list of verdicts:
    [
      {"file": "<abs path exactly as in analysis.json>", "lines": "12-15",
       "cell_id": "<cell_id from the analysis.json row>",   # STRONGLY recommended
       "ewi_code": "<ewi_code from the row>",               # STRONGLY recommended
       "decision": "confirm" | "dismiss",
       "final_risk": 0.7,                 # confirm only (optional)
       "fix": "recommended approach",     # confirm only (optional)
       "resolution_reason": "why safe"},  # dismiss only (recommended)
      ...
    ]

Applied to the matching ``kind == "needs_adjudication"`` row:
  * confirm -> kind=standard, adjudicated=true, final_risk?=<v>, fix?=<v>
  * dismiss -> resolution=safe, resolution_reason=<v>, adjudicated=true

Matching (why it is not just ``(file, lines)``)
----------------------------------------------
``lines`` is **cell-relative** for notebook sources, so it is NOT unique within a
file: every cell has a line 1, and unrelated issues in different cells all end up
labelled e.g. ``"1-7"``. Indexing rows one-to-one by ``(file, lines)`` therefore
made most rows in a collision group **unreachable** — a dict keeps only the last
row per key — so verdicts for the others were counted as ``already_adjudicated``
and silently discarded. Measured on real workloads: 68 of 176 rows unreachable
(Verisk_Claims) and 7 of 71 (RAD_Property_Process_Clash). Worse than loss, it
also *misattributed*: the first verdict in a group landed on the last row of that
group, so a "confirm" reasoned about a Delta MERGE could be stamped onto a benign
``count()`` while the real blocker stayed untouched.

Rows are now keyed by a content fingerprint — see ``issue_key()`` — built from
fields the analyzers already emit (``file``, ``cell_id``, ``lines``, ``ewi_code``,
``code``). No new persisted field and no analyzer change is required, and old
artifacts work unchanged. Measured: 0 unreachable rows on all three workloads.

Matching proceeds strongest-first, so sidecars written before this change still
apply correctly:
  1. full fingerprint  (file, cell_id, lines, ewi_code, code)
  2. (file, cell_id, lines)
  3. (file, lines) positionally within the collision group, in analysis.json order

Exit codes:
  0 every verdict was applied
  1 at least one verdict could not be applied (submitted != applied)
  3 IO / usage error

Usage:
    python3 apply_adjudications.py --analysis <analysis.json> --verdicts-dir <Adjudication/>
"""

import argparse
import collections
import glob
import hashlib
import json
import sys
from pathlib import Path


def _norm(v) -> str:
    return "" if v is None else str(v).strip()


def issue_key(d: dict) -> str:
    """Stable content fingerprint for one analysis row or one verdict.

    Derived only from fields the analyzers already emit, so a verdict and the row
    it refers to hash identically without either side persisting a new id.
    ``code`` is included because a single cell line can host several distinct
    findings; it is whitespace-normalised so cosmetic reflowing does not break
    the match.
    """
    code = " ".join(_norm(d.get("code")).split())
    parts = [_norm(d.get("file")), _norm(d.get("cell_id")), _norm(d.get("lines")),
             _norm(d.get("ewi_code")), code]
    return hashlib.sha1("\u0000".join(parts).encode("utf-8")).hexdigest()


def _mid_key(d: dict) -> tuple:
    return (_norm(d.get("file")), _norm(d.get("cell_id")), _norm(d.get("lines")))


def _weak_key(d: dict) -> tuple:
    return (_norm(d.get("file")), _norm(d.get("lines")))


def _load_rows(p: Path):
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):
                return v, (data, k)
    return [], None


def _apply(row: dict, v: dict) -> str:
    """Mutate row per verdict; return 'confirm'|'dismiss'|'skip'."""
    dec = (v.get("decision") or "").lower()
    if dec == "confirm":
        row["kind"] = "standard"
        row["adjudicated"] = True
        if v.get("final_risk") is not None:
            row["final_risk"] = v["final_risk"]
        if v.get("fix"):
            row["fix"] = v["fix"]
        return "confirm"
    if dec == "dismiss":
        row["resolution"] = "safe"
        row["adjudicated"] = True
        row["resolution_reason"] = v.get("resolution_reason") or "adjudicator: not a real SCOS incompatibility in context"
        return "dismiss"
    return "skip"


class Matcher:
    """Resolve a verdict to exactly one unadjudicated row, strongest key first."""

    def __init__(self, rows):
        deferred = [r for r in rows if r.get("kind") == "needs_adjudication"]
        self.by_fp = collections.defaultdict(collections.deque)
        self.by_mid = collections.defaultdict(collections.deque)
        self.by_weak = collections.defaultdict(collections.deque)
        for r in deferred:
            self.by_fp[issue_key(r)].append(r)
            self.by_mid[_mid_key(r)].append(r)
            self.by_weak[_weak_key(r)].append(r)

    @staticmethod
    def _take(bucket):
        """Pop the first row in the bucket that is not yet adjudicated."""
        while bucket:
            row = bucket[0]
            if row.get("adjudicated"):
                bucket.popleft()
                continue
            return row
        return None

    def match(self, v: dict):
        """Return (row, how) or (None, 'unmatched')."""
        row = self._take(self.by_fp.get(issue_key(v), collections.deque()))
        if row is not None:
            return row, "fingerprint"
        row = self._take(self.by_mid.get(_mid_key(v), collections.deque()))
        if row is not None:
            return row, "cell_lines"
        row = self._take(self.by_weak.get(_weak_key(v), collections.deque()))
        if row is not None:
            return row, "positional"
        return None, "unmatched"


def _self_check() -> None:
    # 1. basic apply semantics
    rows = [{"file": "a.py", "lines": "1-2", "kind": "needs_adjudication"},
            {"file": "a.py", "lines": "9-9", "kind": "needs_adjudication"}]
    m = Matcher(rows)
    r, _ = m.match({"file": "a.py", "lines": "1-2", "decision": "confirm"})
    _apply(r, {"decision": "confirm", "final_risk": 0.7, "fix": "x"})
    r, _ = m.match({"file": "a.py", "lines": "9-9", "decision": "dismiss"})
    _apply(r, {"decision": "dismiss", "resolution_reason": "safe here"})
    assert rows[0]["kind"] == "standard" and rows[0]["adjudicated"] and rows[0]["final_risk"] == 0.7
    assert rows[1]["resolution"] == "safe" and rows[1]["adjudicated"] and rows[1]["kind"] == "needs_adjudication"

    # 2. the regression this fix exists for: N distinct issues sharing (file, lines).
    #    Every verdict must land, and each on its OWN row.
    coll = [{"file": "nb.py", "cell_id": "c1", "lines": "2-2", "ewi_code": "E1",
             "code": "DeltaTable.forName(...)", "kind": "needs_adjudication"},
            {"file": "nb.py", "cell_id": "c2", "lines": "2-2", "ewi_code": "E2",
             "code": "df.count()", "kind": "needs_adjudication"},
            {"file": "nb.py", "cell_id": "c3", "lines": "2-2", "ewi_code": "E3",
             "code": "spark.read.parquet(...)", "kind": "needs_adjudication"}]
    m2 = Matcher(coll)
    for v in [{"file": "nb.py", "cell_id": "c1", "lines": "2-2", "ewi_code": "E1",
               "code": "DeltaTable.forName(...)", "decision": "confirm", "final_risk": 0.95},
              {"file": "nb.py", "cell_id": "c2", "lines": "2-2", "ewi_code": "E2",
               "code": "df.count()", "decision": "dismiss", "resolution_reason": "supported"},
              {"file": "nb.py", "cell_id": "c3", "lines": "2-2", "ewi_code": "E3",
               "code": "spark.read.parquet(...)", "decision": "confirm"}]:
        row, how = m2.match(v)
        assert row is not None and how == "fingerprint", f"lost verdict: {v['ewi_code']} ({how})"
        _apply(row, v)
    assert all(r.get("adjudicated") for r in coll), "a colliding row was left unadjudicated"
    # the confirm meant for the Delta row must be on the Delta row, not another
    assert coll[0]["final_risk"] == 0.95 and coll[0]["kind"] == "standard"
    assert coll[1]["resolution"] == "safe"

    # 3. legacy sidecars (no cell_id / ewi_code / code) still apply, one row each
    legacy = [{"file": "nb.py", "cell_id": "c1", "lines": "3-3", "ewi_code": "E1",
               "code": "x", "kind": "needs_adjudication"},
              {"file": "nb.py", "cell_id": "c2", "lines": "3-3", "ewi_code": "E2",
               "code": "y", "kind": "needs_adjudication"}]
    m3 = Matcher(legacy)
    for v in [{"file": "nb.py", "lines": "3-3", "decision": "dismiss", "resolution_reason": "a"},
              {"file": "nb.py", "lines": "3-3", "decision": "dismiss", "resolution_reason": "b"}]:
        row, how = m3.match(v)
        assert row is not None and how == "positional", f"legacy verdict lost ({how})"
        _apply(row, v)
    assert all(r.get("adjudicated") for r in legacy)


def main(argv=None) -> int:
    _self_check()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analysis", type=Path, required=True)
    ap.add_argument("--verdicts-dir", type=Path, required=True,
                    help="Directory holding chunk_*.json verdict sidecars.")
    ap.add_argument("--allow-unapplied", action="store_true",
                    help="Exit 0 even when some verdicts could not be applied "
                         "(default: exit 1, because a dropped verdict is silent data loss).")
    args = ap.parse_args(argv)

    if not args.analysis.is_file():
        print(f"ERROR: analysis.json not found: {args.analysis}", file=sys.stderr)
        return 3
    rows, container = _load_rows(args.analysis)
    matcher = Matcher(rows)

    sidecars = sorted(glob.glob(str(args.verdicts_dir / "chunk_*.json")))
    if not sidecars:
        print(f"ERROR: no chunk_*.json verdict sidecars in {args.verdicts_dir}", file=sys.stderr)
        return 3

    confirmed = dismissed = skipped = unmatched = submitted = 0
    how_counts: collections.Counter = collections.Counter()
    for sc in sidecars:
        try:
            verdicts = json.loads(Path(sc).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"WARN: unreadable sidecar {sc}: {e}", file=sys.stderr)
            continue
        for v in verdicts if isinstance(verdicts, list) else []:
            submitted += 1
            row, how = matcher.match(v)
            how_counts[how] += 1
            if row is None:
                unmatched += 1
                continue
            outcome = _apply(row, v)
            if outcome == "confirm":
                confirmed += 1
            elif outcome == "dismiss":
                dismissed += 1
            else:
                skipped += 1

    # single write
    if container is None:
        args.analysis.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    else:
        obj, key = container
        obj[key] = rows
        args.analysis.write_text(json.dumps(obj, indent=2), encoding="utf-8")

    applied = confirmed + dismissed + skipped
    leftover = sum(1 for r in rows
                   if r.get("kind") == "needs_adjudication" and not r.get("adjudicated"))
    matched_by = " ".join(f"{k}={v}" for k, v in sorted(how_counts.items()))
    print(f"apply_adjudications: sidecars={len(sidecars)} submitted={submitted} "
          f"applied={applied} confirmed={confirmed} dismissed={dismissed} "
          f"skipped={skipped} unmatched={unmatched} UNRESOLVED_left={leftover}")
    print(f"apply_adjudications: matched_by {matched_by}")
    print(f"ADJUDICATION_RESULT confirmed={confirmed} dismissed={dismissed}")

    if applied != submitted:
        print(f"ERROR: {submitted - applied} of {submitted} verdict(s) were NOT applied. "
              f"Adjudicator reasoning would be silently discarded and the Phase-2 fixer "
              f"would judge those rows itself, which Phase 1.1 exists to prevent. "
              f"Re-dispatch the missing chunk, or pass --allow-unapplied to override.",
              file=sys.stderr)
        if not args.allow_unapplied:
            return 1
    if leftover:
        print(f"WARN: {leftover} needs_adjudication row(s) received no verdict — no sidecar "
              f"covered them. The Phase-2 fixer fallback will handle them, but they were "
              f"never independently adjudicated.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
