#!/usr/bin/env python3
"""Scan Scala source trees for non-deterministic date/timestamp calls.

The patch-author auto-glob catches ``functions.current_date()`` /
``functions.current_timestamp()`` only. Wildcard-imported bare ``current_date()``
and aliased ``F.current_date()`` must be detected separately.

Emits a patch batch JSON suitable for ``scos_state.py patch-add``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_QUALIFIED_DATE = re.compile(r"\bfunctions\.current_date\s*\(\s*\)")
_QUALIFIED_TS = re.compile(r"\bfunctions\.current_timestamp\s*\(\s*\)")
_BARE_DATE = re.compile(r"(?<![.\w])current_date\s*\(\s*\)")
_BARE_TS = re.compile(r"(?<![.\w])current_timestamp\s*\(\s*\)")

_PINNED_DATE = (
    'functions.to_date(functions.lit(System.getProperty("SCOS_PINNED_DATE", '
    "java.time.LocalDate.now().toString)))"
)
_PINNED_TS = (
    'functions.to_timestamp(functions.lit(System.getProperty("SCOS_PINNED_TIMESTAMP", '
    'java.time.LocalDate.now().toString + " 00:00:00")))'
)


def _die(code: int, msg: str) -> None:
    print(f"[scan_date_calls.py] error: {msg}", file=sys.stderr)
    sys.exit(code)


def _scan_tree(root: Path) -> list[dict]:
    hits: list[dict] = []
    if not root.is_dir():
        return hits
    for path in sorted(root.rglob("*.scala")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(root))
        for pattern, kind, replace in (
            (_QUALIFIED_DATE, "functions.current_date()", _PINNED_DATE),
            (_QUALIFIED_TS, "functions.current_timestamp()", _PINNED_TS),
            (_BARE_DATE, "current_date()", _PINNED_DATE),
            (_BARE_TS, "current_timestamp()", _PINNED_TS),
        ):
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append({
                    "file": rel,
                    "line": line,
                    "kind": kind,
                    "match": m.group(0),
                    "replace": replace,
                })
    return hits


def build_patch_batch(hits: list[dict], *, relative_glob: str = "src/**/*.scala") -> dict:
    """Collapse per-hit findings into regex glob patches for patch-add."""
    patches: list[dict] = []
    kinds = sorted({h["kind"] for h in hits})
    mapping = {
        "functions.current_date()": (
            r"functions\.current_date\(\)",
            _PINNED_DATE,
            "functions.current_date -> pinned lit",
        ),
        "functions.current_timestamp()": (
            r"functions\.current_timestamp\(\)",
            _PINNED_TS,
            "functions.current_timestamp -> pinned lit",
        ),
        "current_date()": (
            r"(?<![.\w])current_date\(\)",
            _PINNED_DATE,
            "bare current_date -> pinned lit",
        ),
        "current_timestamp()": (
            r"(?<![.\w])current_timestamp\(\)",
            _PINNED_TS,
            "bare current_timestamp -> pinned lit",
        ),
    }
    for kind in kinds:
        if kind not in mapping:
            continue
        search, replace, note = mapping[kind]
        pid = kind.replace("()", "").replace(".", "_").replace(" ", "_")
        patches.append({
            "id": f"date_pin_{pid}",
            "relative_file": relative_glob,
            "regex": True,
            "replace_all": True,
            "note": note,
            "search": search,
            "replace": replace,
        })
    return {"patches": patches}


def run(conv_root: Path, *, output: Path | None = None) -> dict:
    source_root = conv_root / "Validation" / "source"
    output_root = conv_root / "Output"
    if not source_root.is_dir():
        _die(2, f"Validation/source not found: {source_root}")

    source_hits = _scan_tree(source_root)
    migrated_hits = _scan_tree(output_root) if output_root.is_dir() else []
    batch = build_patch_batch(source_hits + migrated_hits)

    report = {
        "source_hits": len(source_hits),
        "migrated_hits": len(migrated_hits),
        "hits": source_hits + migrated_hits,
        "patches": batch["patches"],
        "clean": not (source_hits or migrated_hits),
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(batch, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="scan_date_calls.py",
        description="Detect current_date/current_timestamp calls and emit patch-add JSON.",
    )
    ap.add_argument("--conv-root", required=True)
    ap.add_argument(
        "--output",
        default=None,
        help="Write patch batch JSON for scos_state.py patch-add --from-file",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Print full scan report as JSON (default: summary only)",
    )
    args = ap.parse_args(argv)
    conv_root = Path(args.conv_root).expanduser().resolve()
    out_path = Path(args.output).expanduser().resolve() if args.output else None
    report = run(conv_root, output=out_path)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"[scan_date_calls.py] source_hits={report['source_hits']} "
            f"migrated_hits={report['migrated_hits']} "
            f"patches={len(report['patches'])} clean={report['clean']}"
        )
        if out_path:
            print(f"[scan_date_calls.py] wrote {out_path}")
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
