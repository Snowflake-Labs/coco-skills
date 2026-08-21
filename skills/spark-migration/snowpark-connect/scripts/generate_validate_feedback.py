#!/usr/bin/env python3
"""Phase 4c: Generate validate feedback report from Validation/results/summary.json.

Reads the summary.json produced by validate.py, extracts blocking failure
reasons and all validator patches (harness patches and migration fix commits)
from every trial where phase_b ran, redacts customer-specific values (schema
names, table names, column names, paths, query IDs), and writes
Feedback/validate_feedback.md.

The report is safe to attach directly to a Jira ticket — all customer-specific
values are replaced with placeholders before writing.

Usage:
    uv run --project <SKILL_DIRECTORY> \\
      python <SKILL_DIRECTORY>/scripts/generate_validate_feedback.py \\
      --conv-root <CONVERSION>

Output:
    <CONVERSION>/Feedback/validate_feedback.md
"""
import json
import re
import sys
import argparse
from pathlib import Path


def read_summary(conv_root: Path) -> dict:
    summary_path = conv_root / "Validation" / "results" / "summary.json"
    if not summary_path.exists():
        print(f"WARNING: summary.json not found at {summary_path}")
        return {}
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as e:
        print(f"WARNING: summary.json is not valid JSON: {e}")
        return {}
    print(f"Read summary.json — overall: {data.get('decision', {}).get('overall', 'unknown')}")
    return data


def get_blocking_reasons(summary: dict) -> list:
    reasons = summary.get("decision", {}).get("blocking_reasons", [])
    print(f"Found {len(reasons)} hard_stuck failure(s)")
    return reasons


# Verdicts where phase_b did not run — no patches to report.
_SKIP_VERDICTS = {"pending", "phase_a_skipped"}


def get_validation_patches(summary: dict) -> list:
    """Collect harness patches and migration fix commits from all completed entrypoints.

    Collects from every entrypoint where phase_b ran (passing OR failing) — the
    patches are feedback regardless of whether the trial ultimately passed.
    Each entry carries a 'verdict' field so the reader knows the outcome.
    """
    result = []
    for ep in summary.get("entrypoints", []):
        phase_b = ep.get("phase_b")
        if not phase_b:
            continue
        verdict = phase_b.get("verdict", "pending")
        if verdict in _SKIP_VERDICTS:
            continue

        trial_id    = ep.get("id", "?")
        source_path = ep.get("source_path", "?")

        for patch in phase_b.get("patches_applied", []):
            if isinstance(patch, dict):
                result.append({
                    "trial_id":    trial_id,
                    "source_path": source_path,
                    "verdict":     verdict,
                    "kind":        "harness_patch",
                    "note":        patch.get("note", patch.get("id", "?")),
                    "file":        patch.get("relative_file", ""),
                    "search":      patch.get("search", ""),
                    "replace":     patch.get("replace", ""),
                })
            else:
                result.append({
                    "trial_id":    trial_id,
                    "source_path": source_path,
                    "verdict":     verdict,
                    "kind":        "harness_patch",
                    "note":        str(patch),
                    "file":        "",
                    "search":      "",
                    "replace":     "",
                })

        for commit in phase_b.get("migration_fix_commits", []):
            result.append({
                "trial_id":    trial_id,
                "source_path": source_path,
                "verdict":     verdict,
                "kind":        "migration_fix",
                "note":        commit.get("subject", ""),
                "file":        "",
                "search":      "",
                "replace":     "",
            })

    total = len(result)
    harness = sum(1 for p in result if p["kind"] == "harness_patch")
    fixes   = sum(1 for p in result if p["kind"] == "migration_fix")
    print(f"Found {total} patch entries ({harness} harness patches, {fixes} migration fixes)")
    return result


def redact_reason(text: str) -> str:
    """Extract the headline error message and redact customer-specific values.

    Strips Python tracebacks (the code snippet is extracted separately by
    extract_snippet). Returns the first meaningful error description line(s).
    """
    if not text:
        return ""

    # Strip everything from "Traceback (most recent call last):" onward
    tb_start = re.search(r'\nTraceback \(most recent call last\):', text)
    if tb_start:
        text = text[:tb_start.start()].strip()

    # Also strip standalone "Traceback" headers at the very start
    text = re.sub(r'^Traceback \(most recent call last\):.*', '', text, flags=re.DOTALL).strip()

    result = text

    # Remove Snowflake query IDs
    result = re.sub(r'(requestId|queryId|query_id)[=:\s]+[0-9a-f\-]{8,}',
                    r'\1=<id>', result, flags=re.IGNORECASE)

    # Redact 3-part table names (DB.SCHEMA.TABLE)
    result = re.sub(
        r'\b[A-Z_][A-Z_0-9]*\.[A-Z_][A-Z_0-9]*\.[A-Z_][A-Z_0-9]*\b',
        '<table>', result, flags=re.IGNORECASE
    )

    # Redact ephemeral validation schema names
    result = re.sub(r'\b[A-Z_]*VALIDATION_[A-Z0-9_]+\b', '<schema>',
                    result, flags=re.IGNORECASE)

    # Redact cloud storage paths
    result = re.sub(r's3://[^\s"\')\]]+',   '<s3-path>', result)
    result = re.sub(r's3a://[^\s"\')\]]+',  '<s3-path>', result)
    result = re.sub(r'hdfs://[^\s"\')\]]+', '<hdfs-path>', result)
    result = re.sub(r'gs://[^\s"\')\]]+',   '<gs-path>', result)
    result = re.sub(r'abfs://[^\s"\')\]]+', '<abfs-path>', result)
    result = re.sub(r'dbfs:/[^\s"\')\]]+',  '<dbfs-path>', result)
    result = re.sub(r'/mnt/[^\s"\')\]]+',   '<path>', result)
    result = re.sub(r'/tmp/[^\s"\')\]]+',   '<path>', result)

    return result.strip()


def extract_snippet(reason: str) -> str:
    """Extract the failing code line from a traceback in the reason text."""
    if not reason:
        return ""

    # Look for a Python traceback — the last "File ..., line N" entry
    # has the failing code on the next line
    lines = reason.splitlines()
    last_code_line = ""
    for i, line in enumerate(lines):
        # Traceback lines: '  File "...", line N, in <func>'
        if re.match(r'\s+File ".*", line \d+', line):
            # The next line is the actual code
            if i + 1 < len(lines):
                candidate = lines[i + 1].strip()
                if candidate and not candidate.startswith("File "):
                    last_code_line = candidate

    return last_code_line


def redact_snippet(snippet: str) -> str:
    if not snippet:
        return ""
    result = snippet
    result = re.sub(r'\.withColumn\s*\(\s*["\'][^"\']+["\']',
                    '.withColumn("<col>"', result)
    result = re.sub(r'\bF\.col\s*\(\s*["\'][^"\']+["\']',
                    'F.col("<col>"', result)
    result = re.sub(r'\bcol\s*\(\s*["\'][^"\']+["\']',
                    'col("<col>"', result)
    result = re.sub(
        r'["\'][A-Z_a-z][A-Z_a-z0-9]*\.[A-Z_a-z][A-Z_a-z0-9]*\.[A-Z_a-z][A-Z_a-z0-9]*["\']',
        '"<table>"', result)
    result = re.sub(r'\b([a-z][a-z0-9_]*_df|df_[a-z][a-z0-9_]*)\b', '<df>', result)
    result = re.sub(r'"s3://[^"]*"',   '"<s3-path>"', result)
    result = re.sub(r'"hdfs://[^"]*"', '"<hdfs-path>"', result)
    result = re.sub(r'"/tmp/[^"]*"',   '"<path>"',     result)
    result = re.sub(r'"/mnt/[^"]*"',   '"<path>"',     result)
    return result


def write_validate_feedback(conv_root: Path, blocking_reasons: list,
                            patches: list) -> Path:
    # Map internal kind values to EWI codes
    KIND_TO_EWI = {
        "hard_stuck":    "SPRKCNTPY9000",
        "soft_stuck":    "SPRKCNTPY9001",
        "timeout":       "SPRKCNTPY9002",
        "crash":         "SPRKCNTPY9003",
    }

    lines = ["## Validation failures (validate)", ""]

    for entry in blocking_reasons:
        kind    = entry.get("kind", "hard_stuck")
        ewi     = KIND_TO_EWI.get(kind, "SPRKCNTPY9000")
        reason  = redact_reason(entry.get("reason", ""))
        snippet = redact_snippet(extract_snippet(entry.get("reason", "")))

        lines.append(f"### {ewi}")
        lines.append(reason)
        if snippet:
            lines.append("Snippet (redacted):")
            lines.append(f"    {snippet}")
        lines.append("")

    if patches:
        lines += ["", "## Patches applied by validator", ""]
        for p in patches:
            verdict_label = f"verdict: {p['verdict']}"
            kind_label    = "harness patch" if p["kind"] == "harness_patch" else "migration fix"
            lines.append(f"### {p['source_path']}  [{kind_label} — {verdict_label}]")
            if p["note"]:
                lines.append(redact_reason(p["note"]))
            if p["file"]:
                lines.append(f"File: `{p['file']}`")
            if p["search"]:
                lines.append("Before:")
                lines.append(f"    {redact_snippet(p['search'][:300])}")
            if p["replace"]:
                lines.append("After:")
                lines.append(f"    {redact_snippet(p['replace'][:300])}")
            lines.append("")

    feedback_dir = conv_root / "Feedback"
    feedback_dir.mkdir(exist_ok=True)
    out_path = feedback_dir / "validate_feedback.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Written: {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--conv-root", required=True,
                        help="Path to the Conversion-SCOS-<timestamp> directory")
    args = parser.parse_args()

    conv_root = Path(args.conv_root).resolve()
    summary_path = conv_root / "Validation" / "results" / "summary.json"
    if not summary_path.exists():
        print(f"ERROR: {summary_path} not found.", file=sys.stderr)
        sys.exit(1)

    summary = read_summary(conv_root)
    if not summary:
        print("Empty summary.json. Nothing to report.")
        sys.exit(0)

    blocking = get_blocking_reasons(summary)
    patches  = get_validation_patches(summary)
    if not blocking and not patches:
        print("No failures or patches found. Nothing to report.")
        sys.exit(0)

    out_path = write_validate_feedback(conv_root, blocking, patches)
    print(f"\nDone. Attach this file to the Jira ticket:\n  {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
