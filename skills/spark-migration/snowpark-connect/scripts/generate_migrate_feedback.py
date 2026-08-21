#!/usr/bin/env python3
"""Phase 4b: Generate migrate feedback report from Reports/Issues.csv.

Reads the Issues.csv produced by generate_scos_reports.py, filters to rows
that require human intervention, extracts a redacted code snippet for each
issue from the migrated Output/ files, and writes Feedback/migrate_gaps.md.

Filtering logic (two formats supported):
  - New format (PR #3392+): Issues.csv has a ``Status`` column.
    Keep rows where Status is ``Error`` or ``IO`` — both need human action.
    ``Fixed`` and ``Warning`` rows are excluded.
  - Legacy format (no Status column): keep rows where Category is not
    ``Information`` or ``Warning`` (i.e. keep ``ConversionError`` rows).

The report is safe to attach directly to a Jira ticket — customer-specific
values are replaced with placeholders before writing:
  - s3://company-bucket/data/file.csv  →  <s3-path>
  - col("customer_id")                 →  col("<col>")
  - "PROD_DB.SALES.FACT_ORDERS"        →  "<table>"
  - customer_df                        →  <df>
  - /tmp/checkpoints                   →  <path>

Usage:
    uv run --project <SKILL_DIRECTORY> \\
      python <SKILL_DIRECTORY>/scripts/generate_migrate_feedback.py \\
      --conv-root <CONVERSION>

Output:
    <CONVERSION>/Feedback/migrate_gaps.md
"""
import csv
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

# New-format Issues.csv (PR #3392+): Code column has a "-STATUS" suffix.
# Keep only codes whose suffix signals human action is needed.
HUMAN_SUFFIXES = {"Error", "IO"}

# Legacy-format Issues.csv (no suffix on Code): exclude these Categories.
EXCLUDE_CATEGORIES = {"Information", "Warning"}


def read_issues(conv_root: Path) -> list:
    issues_path = conv_root / "Reports" / "Issues.csv"
    if not issues_path.exists():
        print(f"WARNING: Issues.csv not found at {issues_path}")
        return []
    rows = []
    with open(issues_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    print(f"Read {len(rows)} rows from Issues.csv")
    return rows


def is_human_intervention(row: dict) -> bool:
    code = row.get("Code", "")
    # New format (PR #3392+): Code has a "-STATUS" suffix, e.g. SPRKCNTPY3400-Error.
    # Keep only rows where the suffix is Error or IO — both need human action.
    if "-" in code:
        suffix = code.split("-", 1)[1]
        return suffix in HUMAN_SUFFIXES

    # Legacy format (no suffix): filter by Category.
    category = row.get("Category", "")
    if category in EXCLUDE_CATEGORIES:
        return False
    description = row.get("Description", "").lower()
    if description.startswith("performance tip"):
        return False
    return True


def filter_issues(rows: list) -> list:
    filtered = [r for r in rows if is_human_intervention(r)]
    print(f"After filter: {len(filtered)} human-intervention markers")
    return filtered


def get_snippet(conv_root: Path, file_id: str, line_str: str) -> str:
    if not file_id or not line_str:
        return ""
    if line_str.startswith("cell:"):
        return ""
    try:
        line_num = int(line_str)
    except ValueError:
        return ""
    output_file = conv_root / "Output" / file_id
    if not output_file.exists():
        return ""
    try:
        lines = output_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    if line_num < 1:
        return ""
    comment_idx = line_num - 1
    if comment_idx >= len(lines):
        return ""

    # If the flagged line is itself code (not a comment), return it directly.
    # Some issues point to the code line rather than a preceding # SCOS: comment.
    this_line = lines[comment_idx].strip()
    if this_line and not this_line.startswith("#") and not this_line.startswith("//"):
        return this_line

    # Flagged line is a comment — look ahead up to 10 lines.
    # Window expanded from 3: multi-line SCOS annotation blocks can push
    # the actual code line beyond comment_idx+3.
    for i in range(comment_idx + 1, min(comment_idx + 11, len(lines))):
        candidate = lines[i].strip()
        if candidate and not candidate.startswith("#") and not candidate.startswith("//"):
            return candidate
    return ""


def redact_description(desc: str) -> str:
    """Remove customer-specific paths embedded in multi-line SCOS comment bodies."""
    result = desc
    result = re.sub(r's3://[^\s"\')\]]+',   '<s3-path>', result)
    result = re.sub(r's3a://[^\s"\')\]]+',  '<s3-path>', result)
    result = re.sub(r'hdfs://[^\s"\')\]]+', '<hdfs-path>', result)
    result = re.sub(r'gs://[^\s"\')\]]+',   '<gs-path>', result)
    result = re.sub(r'abfs://[^\s"\')\]]+', '<abfs-path>', result)
    result = re.sub(r'dbfs:/[^\s"\')\]]+',  '<dbfs-path>', result)
    result = re.sub(r'/mnt/[^\s"\')\]]+',   '<path>', result)
    result = re.sub(r'/tmp/[^\s"\')\]]+',   '<path>', result)
    return result


def redact_snippet(snippet: str) -> str:
    if not snippet:
        return ""
    result = snippet
    result = re.sub(r'"s3://[^"]*"',   '"<s3-path>"', result)
    result = re.sub(r"'s3://[^']*'",   "'<s3-path>'", result)
    result = re.sub(r'"s3a://[^"]*"',  '"<s3-path>"', result)
    result = re.sub(r'"hdfs://[^"]*"', '"<hdfs-path>"', result)
    result = re.sub(r'"gs://[^"]*"',   '"<gs-path>"', result)
    result = re.sub(r'"abfs://[^"]*"', '"<abfs-path>"', result)
    result = re.sub(r'"dbfs:/[^"]*"',  '"<dbfs-path>"', result)
    result = re.sub(r'"/mnt/[^"]*"',   '"<path>"', result)
    result = re.sub(r'"/tmp/[^"]*"',   '"<path>"', result)
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
    result = re.sub(r'setCheckpointDir\s*\(\s*["\'][^"\']+["\']',
                    'setCheckpointDir(<path>', result)
    return result


def write_migrate_feedback(conv_root: Path, filtered_rows: list) -> Path:
    by_code: dict = defaultdict(list)
    for row in filtered_rows:
        code = row.get("Code", "SPRKCNTPY1000")
        by_code[code].append(row)

    lines = ["## Human-intervention markers (migrate)", ""]
    for code in sorted(by_code.keys()):
        for row in by_code[code]:
            description = redact_description(row.get("Description", "").strip())
            file_id  = row.get("FileId", "")
            line_str = row.get("Line", "")
            raw_snippet      = get_snippet(conv_root, file_id, line_str)
            redacted_snippet = redact_snippet(raw_snippet)
            lines.append(f"### {code}")
            lines.append(description)
            if redacted_snippet:
                lines.append("Snippet (redacted):")
                lines.append(f"    {redacted_snippet}")
            lines.append("")

    feedback_dir = conv_root / "Feedback"
    feedback_dir.mkdir(exist_ok=True)
    out_path = feedback_dir / "migrate_gaps.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Written: {out_path}")

    # Also write legacy name for backward compatibility with older tooling
    legacy_path = feedback_dir / "migrate_feedback.md"
    legacy_path.write_text("\n".join(lines), encoding="utf-8")

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--conv-root", required=True,
                        help="Path to the Conversion-SCOS-<timestamp> directory")
    args = parser.parse_args()

    conv_root = Path(args.conv_root).resolve()
    issues_path = conv_root / "Reports" / "Issues.csv"
    if not issues_path.exists():
        print(f"ERROR: {issues_path} not found.", file=sys.stderr)
        sys.exit(1)

    rows = read_issues(conv_root)
    if not rows:
        print("No issues found. Nothing to report.")
        sys.exit(0)

    filtered = filter_issues(rows)
    if not filtered:
        print("No human-intervention markers found.")
        sys.exit(0)

    out_path = write_migrate_feedback(conv_root, filtered)
    print(f"\nDone. Attach this file to the Jira ticket:\n  {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
