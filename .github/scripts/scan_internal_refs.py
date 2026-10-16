#!/usr/bin/env python3
"""Scan skill files for references that will not resolve outside Snowflake.

Structural markers only -- link shapes, ticket-key formats, confidentiality
banners. It must not contain product codenames or unreleased feature names:
this file is public, so a deny-list of those would itself disclose them.
Judgement-based review of internal content happens before a skill reaches
this repo.

Credential scanning is out of scope for this script.

Pre-existing findings sit in a baseline, so this check can be required without
a cleanup first. New findings fail; baselined ones report as debt. Suppress a
false positive with an `internal-ref-ok` marker on the line or the line above.

Usage:
  scan_internal_refs.py                         # skills changed vs origin/main
  scan_internal_refs.py --all                   # every skill
  scan_internal_refs.py --json PATH             # write findings as JSON
  scan_internal_refs.py --all --write-baseline  # accept current state
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"
BASELINE_PATH = REPO_ROOT / ".github" / "internal-refs-baseline.json"

# Marker a contributor or maintainer can place on the offending line, or the
# line immediately above it, to accept a false positive in place.
SUPPRESS_MARKER = "internal-ref-ok"

SKIP_DIRS = {"placeholder"}

# Only text files are scanned; binaries are skipped.
TEXT_SUFFIXES = {
    ".md", ".txt", ".yml", ".yaml", ".json", ".py", ".sh", ".sql",
    ".toml", ".cfg", ".ini", ".csv", ".ipynb", ".js", ".ts", ".html",
}

MAX_BYTES = 2_000_000

# Regex work is bounded per line. The hostname pattern backtracks superlinearly
# (quadratic in practice), so an adversarial 100KB single-line file could burn
# real CI time. Also bounds how much text can be echoed into a public log.
MAX_LINE_CHARS = 2_000
MAX_MATCH_CHARS = 200

# (label, compiled pattern, guidance shown to the contributor)
PATTERNS = [
    (
        "internal short link",
        re.compile(r"(?<![\w/.-])go/[A-Za-z0-9][\w./-]*"),
        "go/ links only resolve on the Snowflake network. Link to public docs instead.",
    ),
    (
        "Google Docs or Drive link",
        re.compile(r"https?://(?:docs|drive|sheets|slides)\.google\.com/\S+"),
        "Google Workspace links are almost always access-restricted. "
        "Inline the content the reader needs, or link public docs.",
    ),
    (
        "Slack archive link",
        re.compile(r"https?://[\w.-]*slack\.com/archives/\S+"),
        "Slack links resolve only for workspace members.",
    ),
    (
        "internal ticket reference",
        re.compile(r"\b(?:SNOW|CORE|SIT|PROD|SEC)-\d{2,}\b"),
        "Internal ticket keys are not resolvable externally and can leak "
        "roadmap or incident detail.",
    ),
    (
        "Confluence or wiki path",
        re.compile(r"https?://[\w.-]*(?:atlassian\.net|confluence)[\w./-]*/\S+"),
        "Internal wiki pages are not publicly readable.",
    ),
    (
        "non-public Snowflake hostname",
        re.compile(
            r"\b[\w.-]*\.(?:int|internal|corp|dev|staging)\.[\w.-]*snowflake[\w.-]*\b"
            r"|\bsnowflakecomputing\.(?:internal|corp)\b"
        ),
        "Internal hostnames should not appear in a public skill.",
    ),
    (
        "confidentiality marker",
        re.compile(
            r"(?i)\b(?:snowflake[ -]internal[ -]only"
            r"|internal[ -]only"
            r"|do not share externally"
            r"|not for external distribution"
            r"|internal rfc"
            r"|company confidential)\b"
        ),
        "Text marked internal should not be published. Remove it, or rewrite "
        "the passage for a public audience.",
    ),
    (
        "internal reviewer handle in prose",
        re.compile(r"(?<![\w/@-])sfc-gh-[a-z0-9-]+"),
        "sfc-gh- handles belong in the author field or a git trailer, not in "
        "skill prose where they read as public contact points.",
    ),
]

# Lines matching these are exempt: the author field legitimately carries an
# sfc-gh handle, and the scanner's own source documents the patterns it hunts.
EXEMPT_LINE = re.compile(r"^\s*(?:#\s*)?(?:author|authors|maintainer)s?\s*:", re.I)

SELF_EXEMPT_FILES = {
    "scan_internal_refs.py",
    "test_scan_internal_refs.py",
}


def get_changed_skill_dirs():
    result = subprocess.run(
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        print("ERROR: could not diff against origin/main. Is the ref fetched?")
        print(result.stderr.strip())
        sys.exit(1)

    dirs = set()
    for f in result.stdout.strip().splitlines():
        parts = Path(f).parts
        if len(parts) >= 2 and parts[0] in ("skills", "plugins") and parts[1] not in SKIP_DIRS:
            candidate = REPO_ROOT / parts[0] / parts[1]
            if candidate.is_dir():
                dirs.add(candidate)
    return sorted(dirs)


def get_all_skill_dirs():
    dirs = []
    for parent in ("skills", "plugins"):
        p = REPO_ROOT / parent
        if p.is_dir():
            dirs.extend(d for d in p.iterdir() if d.is_dir() and d.name not in SKIP_DIRS)
    return sorted(dirs)


def is_safe_to_read(path):
    """Reject anything that could read outside the repo.

    A pull request can add a symlink, and Path.is_file() follows symlinks -- so
    `skills/x/notes.md -> ~/.ssh/id_rsa` would otherwise be read here, with any
    matched text echoed into a public CI log. Skip symlinks outright, and
    confirm the resolved path is still inside the repo.
    """
    try:
        if path.is_symlink():
            return False
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return resolved.is_relative_to(REPO_ROOT.resolve())


def scan_file(path):
    """Return a list of (line_no, label, matched_text, guidance)."""
    if path.name in SELF_EXEMPT_FILES:
        return []
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return []
    if not is_safe_to_read(path):
        return []
    try:
        if path.stat().st_size > MAX_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    hits = []
    lines = text.splitlines()
    for lineno, line in enumerate(lines, start=1):
        if EXEMPT_LINE.match(line):
            continue
        if SUPPRESS_MARKER in line:
            continue
        # Also honour the marker on the preceding line, so it can sit above a
        # line whose content must stay verbatim.
        if lineno >= 2 and SUPPRESS_MARKER in lines[lineno - 2]:
            continue
        line = line[:MAX_LINE_CHARS]
        for label, pattern, guidance in PATTERNS:
            for m in pattern.finditer(line):
                hits.append((lineno, label, m.group(0)[:MAX_MATCH_CHARS], guidance))
    return hits


def finding_key(finding):
    """Identity of a finding for baseline comparison.

    The matched text is hashed, not stored. A baseline listing the internal
    references verbatim would republish, in this repo, exactly what the scan
    exists to keep out of it.

    Excludes the line number deliberately: editing elsewhere in a file should
    not resurrect a baselined finding, and shifting a line should not count as
    new leakage.
    """
    digest = hashlib.sha256(finding["match"].encode("utf-8")).hexdigest()[:16]
    return f"{finding['file']}|{finding['label']}|{digest}"


def load_baseline():
    if not BASELINE_PATH.exists():
        return set()
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"WARNING: could not read baseline at {BASELINE_PATH}; treating as empty.")
        return set()
    return set(data.get("accepted", []))


def scan_dir(skill_dir):
    findings = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in (".git", "node_modules", "__pycache__") for part in path.parts):
            continue
        for lineno, label, matched, guidance in scan_file(path):
            findings.append(
                {
                    "file": str(path.relative_to(REPO_ROOT)),
                    "line": lineno,
                    "label": label,
                    "match": matched,
                    "guidance": guidance,
                }
            )
    return findings


def main():
    parser = argparse.ArgumentParser(description="Scan for internal references.")
    parser.add_argument("--all", action="store_true", help="scan every skill")
    parser.add_argument("--json", metavar="PATH", help="write findings to a JSON file")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="record all current findings as accepted debt and exit 0",
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="ignore the baseline and report every finding (for auditing debt)",
    )
    args = parser.parse_args()

    dirs = get_all_skill_dirs() if args.all else get_changed_skill_dirs()

    if not dirs:
        print("No skill directories changed. Skipping internal-reference scan.")
        sys.exit(0)

    all_findings = {}
    for d in dirs:
        rel = str(d.relative_to(REPO_ROOT))
        findings = scan_dir(d)
        if findings:
            all_findings[rel] = findings

    flat = [f for v in all_findings.values() for f in v]

    if args.write_baseline:
        if not args.all:
            print("ERROR: --write-baseline requires --all, so the baseline is complete.")
            sys.exit(2)
        accepted = sorted({finding_key(f) for f in flat})
        BASELINE_PATH.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Pre-existing internal references, accepted so the scan can "
                        "be a required check without blocking on cleanup. Entries "
                        "should only ever be removed, never added by hand -- new "
                        "findings must fail. Regenerate with: "
                        "python3 .github/scripts/scan_internal_refs.py --all --write-baseline"
                    ),
                    "accepted": accepted,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {len(accepted)} accepted finding(s) to {BASELINE_PATH}.")
        sys.exit(0)

    baseline = set() if args.no_baseline else load_baseline()

    new_findings = {}
    baselined_count = 0
    for rel, findings in all_findings.items():
        fresh = []
        for f in findings:
            if finding_key(f) in baseline:
                baselined_count += 1
            else:
                fresh.append(f)
        if fresh:
            new_findings[rel] = fresh

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"new": new_findings, "baselined_count": baselined_count}, indent=2
            ),
            encoding="utf-8",
        )

    if baselined_count:
        print(
            f"{baselined_count} pre-existing internal reference(s) accepted via "
            f"baseline. Not blocking, but they are still debt.\n"
        )

    if not new_findings:
        print(f"{len(dirs)} directory/ies scanned. No new internal references found.")
        sys.exit(0)

    total = sum(len(v) for v in new_findings.values())
    print(f"Found {total} new internal reference(s) in {len(new_findings)} directory/ies.\n")

    for rel, findings in new_findings.items():
        print(f"--- {rel} ---")
        seen_guidance = set()
        for f in findings:
            print(f"  {f['file']}:{f['line']}  [{f['label']}]  {f['match']}")
            if f["label"] not in seen_guidance:
                print(f"      -> {f['guidance']}")
                seen_guidance.add(f["label"])
        print()

    print(
        "These references will not resolve for readers outside Snowflake.\n"
        "Remove or replace them, then push again.\n"
        f"If a match is a genuine false positive, add an '{SUPPRESS_MARKER}' "
        "marker on that line or the line above, and say so on the PR."
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
