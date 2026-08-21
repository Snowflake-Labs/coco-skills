# flake8: noqa: T201

"""
SNOW-3256943: Pre-Migration Risk Scoring Engine

Scans PySpark files for pattern matching against a risk score table (0-100)
and generates per-file and project-aggregate risk reports.

Usage:
    python risk_scoring.py --path /path/to/project
    python risk_scoring.py --path /path/to/project --output-format json > risk_report.json

Risk Score Table:
    Pattern                              Score
    ─────────────────────────────────────────────
    RDD ops (.rdd, sc.parallelize)        100
    dbutils (Databricks runtime)          100
    databricks.* imports                   100
    DeltaTable / delta.tables             100
    SparkContext._jvm / _jsc              100
    Structured Streaming                  100
    .collect() on large DataFrames         80
    .count() on large DataFrames           80
    sc.textFile / sc.parallelize           80
    .cache()                               60
    sparkContext.statusTracker             60
    UDF with third-party imports           50
    .toPandas()                            50
    unionByName(allowMissing)              40
    sparkContext.master                     40
    sparkContext.getConf                    30
    hint() / repartition() / coalesce()    20
    Snowflake Connector pushdown           20
    Cloud file reads (s3://, gs://)         20
    .config() cluster/runtime no-ops       20
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# SNOW-3256943: Risk score table — maps pattern names to risk scores (0-100)
RISK_SCORE_TABLE: dict[str, dict] = {
    # Critical (100) — hard blockers
    ".rdd": {
        "score": 100, "category": "RDD Operation",
        "description": "RDD access — not supported in Spark Connect",
    },
    "sc.parallelize": {
        "score": 100, "category": "RDD Operation",
        "description": "SparkContext.parallelize — not available in Spark Connect",
    },
    "sc.textFile": {
        "score": 100, "category": "RDD Operation",
        "description": "SparkContext.textFile — not available in Spark Connect",
    },
    "dbutils": {
        "score": 100, "category": "Databricks Runtime",
        "description": "Databricks dbutils — no equivalent in SCOS",
    },
    "databricks": {
        "score": 100, "category": "Databricks Runtime",
        "description": "databricks.* import (connect/sdk/runtime) — Databricks runtime not available in SCOS",
    },
    "DeltaTable": {
        "score": 100, "category": "Delta Lake",
        "description": "DeltaTable API — not available in SCOS",
    },
    "delta.tables": {
        "score": 100, "category": "Delta Lake",
        "description": "delta.tables import — not available in SCOS",
    },
    "sparkContext._jvm": {
        "score": 100, "category": "JVM Interop",
        "description": "SparkContext._jvm — no JVM in Spark Connect",
    },
    "sparkContext._jsc": {
        "score": 100, "category": "JVM Interop",
        "description": "SparkContext._jsc — no JVM in Spark Connect",
    },
    "sparkContext.hadoopConfiguration": {
        "score": 100, "category": "JVM Interop",
        "description": "Hadoop configuration — not available in Spark Connect",
    },
    "pyspark.streaming": {
        "score": 100, "category": "Structured Streaming",
        "description": "Structured Streaming — different architecture in SCOS",
    },
    "readStream": {
        "score": 100, "category": "Structured Streaming",
        "description": "readStream — Structured Streaming not supported",
    },
    "writeStream": {
        "score": 100, "category": "Structured Streaming",
        "description": "writeStream — Structured Streaming not supported",
    },
    "pyspark.ml": {
        "score": 100, "category": "ML Pipeline",
        "description": "pyspark.ml — must convert to snowflake.ml",
    },
    # High (80) — likely breaks
    ".collect()": {
        "score": 80, "category": "Memory Anti-Pattern",
        "description": ".collect() — OOM risk on large datasets in SCOS",
    },
    ".count()": {
        "score": 80, "category": "Memory Anti-Pattern",
        "description": ".count() — can hang on large datasets in SCOS",
    },
    "sparkContext.broadcast": {
        "score": 70, "category": "SparkContext Property",
        "description": "sparkContext.broadcast — not available in Spark Connect",
    },
    "sparkContext.accumulator": {
        "score": 70, "category": "SparkContext Property",
        "description": "sparkContext.accumulator — not available in Spark Connect",
    },
    # Medium (60) — needs attention
    ".cache()": {
        "score": 60, "category": "Memory Anti-Pattern",
        "description": ".cache() — creates temp view refs that may become invalid",
    },
    "sparkContext.statusTracker": {
        "score": 60, "category": "SparkContext Property",
        "description": "sparkContext.statusTracker — not available in Spark Connect",
    },
    ".toPandas()": {
        "score": 50, "category": "Memory Anti-Pattern",
        "description": ".toPandas() — transfers all data to driver memory",
    },
    "applyInPandas": {
        "score": 50, "category": "UDF Serialization",
        "description": "applyInPandas — potential cloudpickle serialization issues",
    },
    "mapInPandas": {
        "score": 50, "category": "UDF Serialization",
        "description": "mapInPandas — potential cloudpickle serialization issues",
    },
    # Low-medium (40) — auto-fixable
    "unionByName": {
        "score": 40, "category": "API Compatibility",
        "description": "unionByName with allowMissingColumns — type mismatch risk",
    },
    "sparkContext.master": {
        "score": 40, "category": "SparkContext Property",
        "description": "sparkContext.master — replace with static string",
    },
    "SparkSession.builder": {
        "score": 30, "category": "Session Initialization",
        "description": "SparkSession.builder — replace with snowpark_connect.init_spark_session()",
    },
    "sparkContext.getConf": {
        "score": 30, "category": "SparkContext Property",
        "description": "sparkContext.getConf — replace with spark.conf",
    },
    # Low (20) — no-ops
    ".hint(": {
        "score": 20, "category": "No-Op API",
        "description": "hint() — silently ignored in SCOS",
    },
    ".repartition(": {
        "score": 20, "category": "No-Op API",
        "description": "repartition() — silently ignored in SCOS",
    },
    ".coalesce(": {
        "score": 20, "category": "No-Op API",
        "description": "coalesce() — silently ignored in SCOS",
    },
    '.format("snowflake")': {
        "score": 20, "category": "Recommended Improvement",
        "description": "Snowflake Connector — works but SnowflakeSession is better",
    },
    "cloud_storage_read": {
        "score": 20, "category": "Cloud Storage Read",
        "description": "Cloud storage path (s3://, gs://, abfss://, wasbs://) — works but slower; stage to @STAGE for best performance (and wildcard/glob reads fail)",
    },
    ".config(": {
        "score": 20, "category": "No-Op Config",
        "description": ".config() — cluster/runtime configs are silent no-ops on SCOS; verify each key is honored",
    },
}

# SNOW-3256943: Regex patterns for risk detection (compiled once)
_RISK_REGEX_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\.rdd\b"), ".rdd"),
    (re.compile(r"\bsc\.parallelize\s*\("), "sc.parallelize"),
    (re.compile(r"\bsc\.textFile\s*\("), "sc.textFile"),
    (re.compile(r"\bdbutils\b"), "dbutils"),
    (re.compile(r"\bfrom\s+databricks\b"), "databricks"),
    (re.compile(r"\bimport\s+databricks\b"), "databricks"),
    (re.compile(r"\bDeltaTable\b"), "DeltaTable"),
    (re.compile(r"from\s+delta\.tables"), "delta.tables"),
    (re.compile(r"import\s+delta\.tables"), "delta.tables"),
    (re.compile(r"sparkContext\._jvm\b"), "sparkContext._jvm"),
    (re.compile(r"sparkContext\._jsc\b"), "sparkContext._jsc"),
    (re.compile(r"sparkContext\.hadoopConfiguration"), "sparkContext.hadoopConfiguration"),
    (re.compile(r"from\s+pyspark\.streaming"), "pyspark.streaming"),
    (re.compile(r"import\s+pyspark\.streaming"), "pyspark.streaming"),
    (re.compile(r"\.readStream\b"), "readStream"),
    (re.compile(r"\.writeStream\b"), "writeStream"),
    (re.compile(r"from\s+pyspark\.ml\b"), "pyspark.ml"),
    (re.compile(r"import\s+pyspark\.ml\b"), "pyspark.ml"),
    (re.compile(r"\.collect\s*\(\s*\)"), ".collect()"),
    (re.compile(r"\.count\s*\(\s*\)"), ".count()"),
    (re.compile(r"sparkContext\.broadcast\b"), "sparkContext.broadcast"),
    (re.compile(r"sparkContext\.accumulator\b"), "sparkContext.accumulator"),
    (re.compile(r"\.cache\s*\(\s*\)"), ".cache()"),
    (re.compile(r"sparkContext\.statusTracker\b"), "sparkContext.statusTracker"),
    (re.compile(r"\.toPandas\s*\(\s*\)"), ".toPandas()"),
    (re.compile(r"\.applyInPandas\s*\("), "applyInPandas"),
    (re.compile(r"\.mapInPandas\s*\("), "mapInPandas"),
    (re.compile(r"\.unionByName\s*\("), "unionByName"),
    (re.compile(r"sparkContext\.master\b"), "sparkContext.master"),
    (re.compile(r"SparkSession\.builder\b"), "SparkSession.builder"),
    (re.compile(r"sparkContext\.getConf\b"), "sparkContext.getConf"),
    (re.compile(r"\.hint\s*\("), ".hint("),
    (re.compile(r"\.repartition\s*\("), ".repartition("),
    (re.compile(r"\.coalesce\s*\("), ".coalesce("),
    (re.compile(r'\.format\s*\(\s*["\']snowflake["\']'), '.format("snowflake")'),
    (re.compile(r'''["'](?:s3a?|s3n|gs|gcs|abfss?|wasbs?)://'''), "cloud_storage_read"),
    (re.compile(r"\.config\s*\("), ".config("),
]


@dataclass
class PatternMatch:
    """A single pattern match within a file."""
    pattern: str
    score: int
    category: str
    description: str
    line_number: int
    line_content: str


@dataclass
class FileRiskReport:
    """Risk report for a single file."""
    file_path: str
    total_lines: int
    risk_score: int  # 0-100, max of all pattern scores
    risk_level: str  # Critical / High / Low
    matches: list[PatternMatch] = field(default_factory=list)

    @property
    def match_count(self) -> int:
        return len(self.matches)

    def to_dict(self) -> dict:
        return {
            "file": self.file_path,
            "total_lines": self.total_lines,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "match_count": self.match_count,
            "matches": [
                {
                    "pattern": m.pattern,
                    "score": m.score,
                    "category": m.category,
                    "description": m.description,
                    "line_number": m.line_number,
                    "line_content": m.line_content.strip()[:120],
                }
                for m in self.matches
            ],
        }


@dataclass
class ProjectRiskReport:
    """Aggregate risk report for a project."""
    project_path: str
    total_files: int
    files_with_issues: int
    project_risk_score: int  # 0-100, max across all files
    project_risk_level: str  # Critical / High / Low
    category_summary: dict[str, int] = field(default_factory=dict)  # category -> count
    file_reports: list[FileRiskReport] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project": self.project_path,
            "total_files": self.total_files,
            "files_with_issues": self.files_with_issues,
            "project_risk_score": self.project_risk_score,
            "project_risk_level": self.project_risk_level,
            "category_summary": self.category_summary,
            "files": [f.to_dict() for f in self.file_reports],
        }


def classify_risk(score: int) -> str:
    """Classify a risk score into Critical/High/Low."""
    if score >= 70:
        return "Critical"
    elif score >= 30:
        return "High"
    return "Low"


def scan_file(file_path: Path) -> FileRiskReport:
    """
    SNOW-3256943: Scan a single .py file for risk patterns.

    Matches each line against compiled regex patterns and collects
    all matches with their risk scores.
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"WARNING: Could not read {file_path}: {exc}", file=sys.stderr)
        return FileRiskReport(
            file_path=str(file_path), total_lines=0,
            risk_score=0, risk_level="Low",
        )

    lines = content.splitlines()
    matches: list[PatternMatch] = []
    seen_patterns: set[str] = set()  # Deduplicate per-pattern per-file

    for line_num, line in enumerate(lines, start=1):
        # Skip comment-only lines
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        for regex, pattern_name in _RISK_REGEX_PATTERNS:
            if regex.search(line):
                info = RISK_SCORE_TABLE.get(pattern_name)
                if info is None:
                    continue
                # Record every occurrence (for line-level detail)
                matches.append(PatternMatch(
                    pattern=pattern_name,
                    score=info["score"],
                    category=info["category"],
                    description=info["description"],
                    line_number=line_num,
                    line_content=line,
                ))

    # File risk score = max of all match scores (0 if no matches)
    max_score = max((m.score for m in matches), default=0)

    return FileRiskReport(
        file_path=str(file_path),
        total_lines=len(lines),
        risk_score=max_score,
        risk_level=classify_risk(max_score),
        matches=matches,
    )


def scan_project(project_path: Path) -> ProjectRiskReport:
    """
    SNOW-3256943: Scan all .py files in a project directory
    and produce an aggregate risk report.
    """
    if project_path.is_file():
        py_files = [project_path] if project_path.suffix == ".py" else []
    else:
        py_files = sorted(project_path.rglob("*.py"))

    file_reports: list[FileRiskReport] = []
    category_counts: dict[str, int] = {}

    for fp in py_files:
        report = scan_file(fp)
        file_reports.append(report)
        for m in report.matches:
            category_counts[m.category] = category_counts.get(m.category, 0) + 1

    files_with_issues = sum(1 for r in file_reports if r.risk_score > 0)
    project_max = max((r.risk_score for r in file_reports), default=0)

    return ProjectRiskReport(
        project_path=str(project_path),
        total_files=len(py_files),
        files_with_issues=files_with_issues,
        project_risk_score=project_max,
        project_risk_level=classify_risk(project_max),
        category_summary=dict(sorted(category_counts.items(), key=lambda x: -x[1])),
        file_reports=sorted(file_reports, key=lambda r: -r.risk_score),
    )


def print_text_report(report: ProjectRiskReport) -> None:
    """Print a human-readable risk report to stdout."""
    print("=" * 80)
    print("SCOS PRE-MIGRATION RISK REPORT")
    print("=" * 80)
    print(f"Project:            {report.project_path}")
    print(f"Total files:        {report.total_files}")
    print(f"Files with issues:  {report.files_with_issues}")
    print(f"Project risk score: {report.project_risk_score}/100")
    print(f"Project risk level: {report.project_risk_level}")
    print()

    if report.category_summary:
        print("Category Summary:")
        for cat, count in report.category_summary.items():
            print(f"  {cat:30s}  {count} occurrence(s)")
        print()

    for fr in report.file_reports:
        if fr.risk_score == 0:
            continue
        icon = {"Critical": "🔴", "High": "🟡", "Low": "🟢"}.get(fr.risk_level, "⚪")
        print(f"{icon} {fr.file_path}  — score: {fr.risk_score}/100 ({fr.risk_level})")
        for m in fr.matches:
            print(f"    L{m.line_number:4d}  [{m.score:3d}] {m.pattern:40s} {m.line_content.strip()[:80]}")
        print()

    if report.files_with_issues == 0:
        print("✅ No risk patterns detected — project appears SCOS-compatible.")


def main():
    parser = argparse.ArgumentParser(
        description="SNOW-3256943: Pre-migration risk scoring for SCOS compatibility"
    )
    parser.add_argument(
        "--path", required=True,
        help="Path to PySpark file or project directory",
    )
    parser.add_argument(
        "--output-format", choices=["text", "json"], default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    path = Path(args.path).expanduser()
    if not path.exists():
        print(f"Error: Path does not exist: {path}", file=sys.stderr)
        sys.exit(1)

    report = scan_project(path)

    if args.output_format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print_text_report(report)

    # Exit with non-zero if critical risk
    if report.project_risk_level == "Critical":
        sys.exit(2)


if __name__ == "__main__":
    main()
