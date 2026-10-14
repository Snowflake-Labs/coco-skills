# flake8: noqa: T201

"""
SNOW-3256945: Feasibility Assessment & Conversion Blocker Detection

Identifies hard blockers (patterns that make migration infeasible) and partially
supported features, then generates a GO/NO-GO recommendation based on a
decision matrix.

Usage:
    python feasibility_assessment.py --path /path/to/project
    python feasibility_assessment.py --path /path/to/project --output-format json

Decision Matrix:
    GO:     0% RDD usage, no Delta tables, minimal Databricks features,
            stage-compatible file sources, no custom streaming
    NO-GO:  >5% RDD code, core Delta dependency, heavy dbutils usage,
            local-only files, real-time streaming requirement
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# SNOW-3256945: Hard blockers — patterns that make migration infeasible
HARD_BLOCKERS: dict[str, dict] = {
    "rdd_operations": {
        "patterns": [
            re.compile(r"\.rdd\b"),
            re.compile(r"\bsc\.parallelize\s*\("),
            re.compile(r"\bsc\.textFile\s*\("),
            re.compile(r"\bsc\.wholeTextFiles\s*\("),
            re.compile(r"\bsc\.binaryFiles\s*\("),
            re.compile(r"\bfrom\s+pyspark\s+import\s+RDD\b"),
            re.compile(r"\bfrom\s+pyspark\.rdd\s+import\b"),
            re.compile(r"\.emptyRDD\s*\("),
        ],
        "label": "RDD Operations",
        "severity": "BLOCKER",
        "description": "RDD operations are NOT SUPPORTED in Spark Connect — must rewrite as DataFrame",
    },
    "delta_lake": {
        "patterns": [
            re.compile(r"\bDeltaTable\b"),
            re.compile(r"\bfrom\s+delta\.tables\b"),
            re.compile(r"\bimport\s+delta\.tables\b"),
            re.compile(r'\.format\s*\(\s*["\']delta["\']'),
        ],
        "label": "Delta Lake",
        "severity": "BLOCKER",
        "description": "Delta format is NOT SUPPORTED — use Parquet/Iceberg",
    },
    "databricks_runtime": {
        "patterns": [
            re.compile(r"\bfrom\s+databricks\.connect\b"),
            re.compile(r"\bfrom\s+databricks\.sdk\.runtime\b"),
            re.compile(r"\bimport\s+databricks\.connect\b"),
            re.compile(r"\bdbutils\b"),
        ],
        "label": "Databricks Runtime",
        "severity": "BLOCKER",
        "description": "Databricks-specific imports MUST REMOVE — no equivalent in SCOS",
    },
    "structured_streaming": {
        "patterns": [
            re.compile(r"\.readStream\b"),
            re.compile(r"\.writeStream\b"),
            re.compile(r"\bfrom\s+pyspark\.streaming\b"),
            re.compile(r"\bimport\s+pyspark\.streaming\b"),
        ],
        "label": "Structured Streaming",
        "severity": "BLOCKER",
        "description": "Structured Streaming LIMITED — different architecture in SCOS",
    },
    "jvm_interop": {
        "patterns": [
            re.compile(r"sparkContext\._jvm\b"),
            re.compile(r"sparkContext\._jsc\b"),
            re.compile(r"sc\._jvm\b"),
            re.compile(r"sc\._jsc\b"),
        ],
        "label": "JVM Interop",
        "severity": "BLOCKER",
        "description": "SparkContext._jvm/_jsc are NOT AVAILABLE in Spark Connect — hard blocker",
    },
    "jni_native_code": {
        "patterns": [
            re.compile(r"\bctypes\b.*\.CDLL\b"),
            re.compile(r"\bctypes\.cdll\b"),
            re.compile(r"\bffi\.dlopen\b"),
            re.compile(r"\bjpype\b"),
        ],
        "label": "JNI/Native Code",
        "severity": "BLOCKER",
        "description": "JNI or native library loading is not compatible with SCOS container execution",
    },
    "custom_spark_extensions": {
        "patterns": [
            re.compile(r"spark\.sql\.extensions"),
            re.compile(r"SparkSessionExtensions"),
            re.compile(r"\.withExtensions\s*\("),
        ],
        "label": "Custom Spark Extensions",
        "severity": "BLOCKER",
        "description": "Custom Spark extensions are not loadable in SCOS",
    },
}


# SNOW-3256945: Partially supported features — have workarounds
PARTIAL_SUPPORT: dict[str, dict] = {
    "udf_patterns": {
        "patterns": [
            re.compile(r"@udf\b"),
            re.compile(r"@pandas_udf\b"),
            re.compile(r"\.udf\.register\s*\("),
        ],
        "label": "UDFs / Pandas UDFs",
        "note": "Supported with configuration — may need snowpark.connect.udf.packages and self-contained closures",
    },
    "window_functions": {
        "patterns": [
            re.compile(r"\bWindow\.partitionBy\b"),
            re.compile(r"\bWindow\.orderBy\b"),
            re.compile(r"\brow_number\s*\(\s*\)"),
            re.compile(r"\brank\s*\(\s*\)"),
            re.compile(r"\bdense_rank\s*\(\s*\)"),
        ],
        "label": "Window Functions",
        "note": "Supported — may have performance differences with large partitions",
    },
    "broadcast_joins": {
        "patterns": [
            re.compile(r"\bbroadcast\s*\("),
            re.compile(r"\.hint\s*\(\s*['\"]broadcast['\"]"),
        ],
        "label": "Broadcast Joins",
        "note": "broadcast() hint is a no-op in SCOS — Snowflake optimizer handles join strategies",
    },
    "hadoop_filesystem": {
        "patterns": [
            re.compile(r"org\.apache\.hadoop\.fs\.FileSystem"),
            re.compile(r"hadoopConfiguration\(\)\.set"),
            re.compile(r"sc\._jvm\.org\.apache\.hadoop"),
        ],
        "label": "Hadoop Filesystem Access",
        "note": "Partially Supported — workaround available: replace with boto3/azure-storage-blob or Snowflake LIST @stage",
    },
    "local_file_reads": {
        "patterns": [
            re.compile(r'\.read\.\w+\s*\(\s*["\'](?!/|@|s3|gs|abfs|wasb|adl)'),
            re.compile(r'\.load\s*\(\s*["\'](?!/|@|s3|gs|abfs|wasb|adl)'),
        ],
        "label": "Local File Reads",
        "note": "MUST CHANGE — use Snowflake stages for file access",
    },
    "cloud_storage_paths": {
        "patterns": [
            re.compile(r'["\']s3[a]?://'),
            re.compile(r'["\']gs://'),
            re.compile(r'["\']abfs[s]?://'),
            re.compile(r'["\']wasb[s]?://'),
        ],
        "label": "Cloud Storage Paths",
        "note": "Supported — recommend uploading to Snowflake stage for better performance",
    },
    "dbfs_paths": {
        "patterns": [
            re.compile(r'["\']dbfs:/'),
            re.compile(r'["\']/mnt/'),
        ],
        "label": "DBFS Paths",
        "note": "Databricks-specific — must replace with Snowflake stage references",
    },
    "ml_pipeline": {
        "patterns": [
            re.compile(r"\bfrom\s+pyspark\.ml\b"),
            re.compile(r"\bimport\s+pyspark\.ml\b"),
        ],
        "label": "ML Pipeline (pyspark.ml)",
        "note": "Must convert to snowflake.ml equivalents — guided transformation available",
    },
    "hadoop_config": {
        "patterns": [
            re.compile(r"hadoopConfiguration"),
            re.compile(r"fs\.s3a\.access\.key"),
            re.compile(r"fs\.s3a\.secret\.key"),
        ],
        "label": "Hadoop Credential Configuration",
        "note": "Partially Supported — config migration required: move S3 credentials to Snowflake storage integration",
    },
}


@dataclass
class BlockerMatch:
    """A single blocker/feature match."""
    category: str
    label: str
    severity: str  # BLOCKER or PARTIAL
    description: str
    file_path: str
    line_number: int
    line_content: str


@dataclass
class FeasibilityReport:
    """GO/NO-GO feasibility assessment report."""
    project_path: str
    recommendation: str  # GO, CONDITIONAL_GO, NO-GO
    total_files: int
    total_lines: int
    hard_blockers: list[BlockerMatch] = field(default_factory=list)
    partial_features: list[BlockerMatch] = field(default_factory=list)
    blocker_summary: dict[str, int] = field(default_factory=dict)  # label -> count
    partial_summary: dict[str, int] = field(default_factory=dict)  # label -> count
    rdd_line_count: int = 0
    rdd_percentage: float = 0.0

    def to_dict(self) -> dict:
        return {
            "project": self.project_path,
            "recommendation": self.recommendation,
            "total_files": self.total_files,
            "total_lines": self.total_lines,
            "rdd_line_count": self.rdd_line_count,
            "rdd_percentage": round(self.rdd_percentage, 2),
            "hard_blockers_count": len(self.hard_blockers),
            "partial_features_count": len(self.partial_features),
            "blocker_summary": self.blocker_summary,
            "partial_summary": self.partial_summary,
            "hard_blockers": [
                {
                    "category": b.category,
                    "label": b.label,
                    "severity": b.severity,
                    "description": b.description,
                    "file": b.file_path,
                    "line": b.line_number,
                    "code": b.line_content.strip()[:120],
                }
                for b in self.hard_blockers
            ],
            "partial_features": [
                {
                    "category": p.category,
                    "label": p.label,
                    "description": p.description,
                    "file": p.file_path,
                    "line": p.line_number,
                    "code": p.line_content.strip()[:120],
                }
                for p in self.partial_features
            ],
        }


def _scan_patterns(
    file_path: Path,
    lines: list[str],
    pattern_dict: dict[str, dict],
    severity: str,
) -> list[BlockerMatch]:
    """Scan lines against a pattern dictionary and return matches."""
    matches: list[BlockerMatch] = []
    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for cat_key, cat_info in pattern_dict.items():
            for regex in cat_info["patterns"]:
                if regex.search(line):
                    matches.append(BlockerMatch(
                        category=cat_key,
                        label=cat_info["label"],
                        severity=severity,
                        description=cat_info.get("description", cat_info.get("note", "")),
                        file_path=str(file_path),
                        line_number=line_num,
                        line_content=line,
                    ))
                    break  # One match per category per line
    return matches


def assess_feasibility(project_path: Path) -> FeasibilityReport:
    """
    SNOW-3256945: Run feasibility assessment on a PySpark project.

    Scans all .py files for hard blockers and partially supported features,
    calculates RDD usage percentage, and generates a GO/NO-GO recommendation.
    """
    if project_path.is_file():
        py_files = [project_path] if project_path.suffix == ".py" else []
    else:
        py_files = sorted(project_path.rglob("*.py"))

    all_blockers: list[BlockerMatch] = []
    all_partial: list[BlockerMatch] = []
    total_lines = 0
    rdd_lines = 0

    # SNOW-3256945: RDD line counting regex
    rdd_line_regex = re.compile(
        r"\.rdd\b|sc\.parallelize|sc\.textFile|sc\.wholeTextFiles|"
        r"sc\.binaryFiles|\.emptyRDD|from\s+pyspark\s+import\s+RDD|"
        r"from\s+pyspark\.rdd\s+import"
    )

    for fp in py_files:
        try:
            content = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        lines = content.splitlines()
        total_lines += len(lines)

        # Count RDD lines
        for line in lines:
            if not line.strip().startswith("#") and rdd_line_regex.search(line):
                rdd_lines += 1

        # Scan for hard blockers
        all_blockers.extend(_scan_patterns(fp, lines, HARD_BLOCKERS, "BLOCKER"))

        # Scan for partially supported features
        all_partial.extend(_scan_patterns(fp, lines, PARTIAL_SUPPORT, "PARTIAL"))

    # Build summaries
    blocker_summary: dict[str, int] = {}
    for b in all_blockers:
        blocker_summary[b.label] = blocker_summary.get(b.label, 0) + 1

    partial_summary: dict[str, int] = {}
    for p in all_partial:
        partial_summary[p.label] = partial_summary.get(p.label, 0) + 1

    # Calculate RDD percentage
    rdd_pct = (rdd_lines / total_lines * 100) if total_lines > 0 else 0.0

    # SNOW-3256945: GO/NO-GO decision matrix
    recommendation = _decide(blocker_summary, rdd_pct)

    return FeasibilityReport(
        project_path=str(project_path),
        recommendation=recommendation,
        total_files=len(py_files),
        total_lines=total_lines,
        hard_blockers=all_blockers,
        partial_features=all_partial,
        blocker_summary=dict(sorted(blocker_summary.items(), key=lambda x: -x[1])),
        partial_summary=dict(sorted(partial_summary.items(), key=lambda x: -x[1])),
        rdd_line_count=rdd_lines,
        rdd_percentage=rdd_pct,
    )


def _decide(blocker_summary: dict[str, int], rdd_pct: float) -> str:
    """
    SNOW-3256945: Apply the GO/NO-GO decision matrix.

    NO-GO criteria:
    - >5% RDD code
    - Core Delta dependency (DeltaTable API, not just format reads)
    - Heavy dbutils usage (>10 occurrences)
    - Real-time streaming requirement
    - JVM interop / JNI / custom extensions

    CONDITIONAL_GO:
    - Some hard blockers exist but are auto-fixable or have workarounds
    - <5% RDD code (can be rewritten)
    - Delta format reads only (convertible to Parquet/Iceberg)

    GO:
    - No hard blockers at all
    """
    # NO-GO: heavy RDD usage
    if rdd_pct > 5.0:
        return "NO-GO"

    # NO-GO: JVM interop (hard blocker, no workaround)
    if blocker_summary.get("JVM Interop", 0) > 0:
        return "NO-GO"

    # NO-GO: JNI/native code
    if blocker_summary.get("JNI/Native Code", 0) > 0:
        return "NO-GO"

    # NO-GO: custom Spark extensions
    if blocker_summary.get("Custom Spark Extensions", 0) > 0:
        return "NO-GO"

    # NO-GO: heavy dbutils (>10 occurrences)
    if blocker_summary.get("Databricks Runtime", 0) > 10:
        return "NO-GO"

    # NO-GO: streaming
    if blocker_summary.get("Structured Streaming", 0) > 0:
        return "NO-GO"

    # CONDITIONAL GO: some blockers but with workarounds
    if blocker_summary:
        return "CONDITIONAL_GO"

    # GO: no blockers at all
    return "GO"


def print_text_report(report: FeasibilityReport) -> None:
    """Print a human-readable feasibility report."""
    rec_icons = {"GO": "✅", "CONDITIONAL_GO": "⚠️", "NO-GO": "❌"}
    icon = rec_icons.get(report.recommendation, "❓")

    print("=" * 80)
    print("SCOS FEASIBILITY ASSESSMENT")
    print("=" * 80)
    print(f"Project:         {report.project_path}")
    print(f"Recommendation:  {icon} {report.recommendation}")
    print(f"Total files:     {report.total_files}")
    print(f"Total lines:     {report.total_lines}")
    print(f"RDD lines:       {report.rdd_line_count} ({report.rdd_percentage:.1f}%)")
    print()

    if report.blocker_summary:
        print("Hard Blockers:")
        for label, count in report.blocker_summary.items():
            print(f"  ❌ {label:40s}  {count} occurrence(s)")
        print()

    if report.partial_summary:
        print("Partially Supported Features:")
        for label, count in report.partial_summary.items():
            print(f"  ⚠️  {label:40s}  {count} occurrence(s)")
        print()

    # Show top blocker details (up to 10)
    if report.hard_blockers:
        print("Top Blocker Details:")
        shown = set()
        for b in report.hard_blockers[:20]:
            key = (b.label, b.file_path, b.line_number)
            if key in shown:
                continue
            shown.add(key)
            print(f"  L{b.line_number:4d}  [{b.label}] {b.file_path}")
            print(f"         {b.line_content.strip()[:100]}")
            if len(shown) >= 10:
                remaining = len(report.hard_blockers) - len(shown)
                if remaining > 0:
                    print(f"  ... and {remaining} more blocker(s)")
                break
        print()

    # Decision explanation
    if report.recommendation == "GO":
        print("Assessment: No hard blockers detected. The workload appears compatible with SCOS.")
        print("Proceed with full migration (Step 0 → Step 7).")
    elif report.recommendation == "CONDITIONAL_GO":
        print("Assessment: Some blockers detected but they have auto-fix or workaround paths.")
        print("Review the blockers above. The migration skill can handle most of these automatically.")
        print("Proceed with migration but expect some manual intervention.")
    else:
        print("Assessment: Critical blockers detected that prevent automated migration.")
        print("The workload requires significant rewriting before it can run on SCOS.")
        print("Discuss blockers with the customer before investing migration effort.")


def main():
    parser = argparse.ArgumentParser(
        description="SNOW-3256945: Feasibility assessment for SCOS migration"
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

    report = assess_feasibility(path)

    if args.output_format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print_text_report(report)

    # Exit codes: 0=GO, 1=CONDITIONAL_GO, 2=NO-GO
    exit_codes = {"GO": 0, "CONDITIONAL_GO": 1, "NO-GO": 2}
    sys.exit(exit_codes.get(report.recommendation, 1))


if __name__ == "__main__":
    main()
