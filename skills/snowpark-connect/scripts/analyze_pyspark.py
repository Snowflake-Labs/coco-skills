# flake8: noqa: T201

"""
SCOS Migration Agent - PySpark Compatibility Analyzer

Analyze PySpark scripts for potential SCOS compatibility issues.

Usage:
    python analyze_pyspark.py --path /path/to/script.py
    python analyze_pyspark.py --path /path/to/scripts/

This script:
1. Parses PySpark files using Python AST (handles multi-line statements)
2. Extracts complete SQL expressions and method chains
3. Checks API compatibility from the compatibility CSV
4. Uses unified RAG to find similar failing SQL and DataFrame patterns
5. Reports results with root causes and workarounds
"""

import argparse
import ast
import csv
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from code_normalization import normalize_code_lightweight
from rag import SCOSCortexRAG, SCOSRAGConfig
from snowflake.cortex import CompleteOptions, complete as cortex_complete

from snowflake.snowpark import Session

logger = logging.getLogger(__name__)

# LLM model for validation
DEFAULT_LLM_MODEL = "claude-opus-4-5"

# Batch LLM validation prompt - analyzes multiple code blocks at once
PROMPT_PREDICT_COMPATIBILITY_BATCH = """
You are analyzing multiple PySpark code blocks for compatibility issues when running on Snowflake SCOS (Snowpark Connect for Spark).
Your goal is to analyze each code block and determine if it will actually fail on SCOS.

## INPUT DATA
You are provided with {num_blocks} code blocks. Each block contains:
1. `block_id`: Unique identifier.
2. `input_code`: The PySpark code snippet to analyze.
3. `preliminary_assessment`: Rule-based warnings (e.g., "API X is unsupported").
4. `matching_patterns`: Similar failing test cases from our database.

## ANALYSIS PROCESS (Apply to EACH block)
1. **Analyze Input**: Understand the intent and syntax of the `input_code`.
2. **Verify RAG Matches**: Compare `input_code` with `matching_patterns`.
   - **Crucial**: Do the failing patterns share the *exact same* root cause as the input?
   - *Example*: If the RAG shows a failure for `.write.format("avro")` but your input is `.write.format("parquet")`, this is a **FALSE POSITIVE**. The risk is LOW.
3. **Verify Rule-Based Warnings**: Check if the `preliminary_assessment` is valid or a false alarm (e.g., `hint()` is just a no-op).

## IMPORTANT RULES FOR RISK SCORING:
- If the similar test cases use DIFFERENT operations/patterns that don't apply to the input code → final_risk should be 0.0 to 0.1
- If there are NO compatibility issues with the input code → final_risk should be 0.0
- If the similar test cases use the SAME problematic pattern as the input code → final_risk should be 0.5 to 1.0
- Only assign high risk (>0.5) if you're confident the input code will ACTUALLY fail for the SAME reason as the similar test cases
- If there are no similar test cases, but the `SCOS Issues Risk` score exists and is above 0, use it as the `final_risk` score.

BE CONSISTENT: If your explanation says "should work correctly" or "issues don't apply", then final_risk MUST be < 0.1

## CODE BLOCKS TO ANALYZE

{code_blocks_text}

## OUTPUT FORMAT
Return ONLY a valid JSON array with EXACTLY {num_blocks} items (one for each code block, in order).
Your response must contain NO text before or after the JSON array.

[
    {{
        "block_id": "<the block_id from the input>",
        "analysis_thought_process": "<Step-by-step reasoning: 1. Input does X. 2. Compare with preliminary assessment and similar test cases. 3. Conclusion.>"
        "final_risk": <0.0-1.0 float - probability of a failure>,
        "root_cause": "<Actual root cause of failure, or null if safe>",
        "explanation": "<Concise summary (1-2 sentences) for the user explaining your assessment>",
        "fix": "<specific fix/workaround if needed, or null if code is fine>",
        "confidence": "<HIGH|MEDIUM|LOW>"
    }},
    ...
]
"""

# Default batch size for LLM calls
DEFAULT_LLM_BATCH_SIZE = 5

DATA_DIR = Path(__file__).parent / "data"


# Compatibility scores (0-1 scale)
COMPAT_SCORES = {
    "D0": 1.0,
    "D1": 0.8,
    "D2": 0.5,
    "NONE": 0.0,
    "UNKNOWN": None,
    "OUTOFSCOPE": 0.0,
}

# RDD patterns - these indicate unsupported RDD usage
RDD_PATTERNS = [
    # SparkContext access
    ".sparkContext",
    ".rdd",
    # RDD imports
    "from pyspark import RDD",
    "from pyspark.rdd import",
    # SparkContext-specific methods - these methods only exist on SparkContext, so any .methodName( is RDD usage
    ".parallelize(",
    ".textFile(",
    ".wholeTextFiles(",
    ".binaryFiles(",
    ".binaryRecords(",
    ".hadoopFile(",
    ".hadoopRDD(",
    ".newAPIHadoopFile(",
    ".newAPIHadoopRDD(",
    ".sequenceFile(",
    ".objectFile(",
    ".pickleFile(",
    ".emptyRDD(",
]

# RDD methods - operations on RDD objects
RDD_METHODS = {
    "map",
    "flatMap",
    "filter",
    "reduce",
    "reduceByKey",
    "reduceByKeyLocally",
    "groupByKey",
    "sortByKey",
    "sortBy",
    "join",
    "leftOuterJoin",
    "rightOuterJoin",
    "fullOuterJoin",
    "cogroup",
    "cartesian",
    "pipe",
    "coalesce",
    "repartition",
    "foreach",
    "foreachPartition",
    "collect",
    "count",
    "first",
    "take",
    "takeSample",
    "takeOrdered",
    "saveAsTextFile",
    "saveAsSequenceFile",
    "saveAsObjectFile",
    "countByKey",
    "countByValue",
    "aggregate",
    "fold",
    "glom",
    "mapPartitions",
    "mapPartitionsWithIndex",
    "zip",
    "zipWithIndex",
    "zipWithUniqueId",
    "keyBy",
    "keys",
    "values",
    "lookup",
    "top",
    "max",
    "min",
    "sum",
    "mean",
    "variance",
    "stdev",
    "sampleStdev",
    "sampleVariance",
    "histogram",
    "randomSplit",
    "union",
    "intersection",
    "subtract",
    "distinct",
    "cache",
    "persist",
    "unpersist",
    "checkpoint",
    "isCheckpointed",
    "getCheckpointFile",
    "toLocalIterator",
    "isEmpty",
    "getNumPartitions",
    "mapValues",
    "flatMapValues",
    "groupWith",
    "combineByKey",
    "aggregateByKey",
    "foldByKey",
    "sampleByKey",
}

# =============================================================================
# UNSUPPORTED SPARK APIs (from Snowflake documentation)
# https://docs.snowflake.com/en/developer-guide/snowpark-connect/snowpark-connect-compatibility
# =============================================================================

# APIs that are completely unsupported or no-op in SCOS (risk on 0-1 scale)
UNSUPPORTED_APIS = {
    # DataFrame methods that are no-ops
    "hint": {
        "risk": 0.2,  # Low risk - just ignored
        "reason": "DataFrame.hint() is ignored in SCOS - Snowflake optimizer handles execution",
        "category": "No-Op API",
    },
    "repartition": {
        "risk": 0.2,
        "reason": "DataFrame.repartition() is a no-op in SCOS - Snowflake manages partitioning",
        "category": "No-Op API",
    },
    "coalesce": {
        "risk": 0.2,
        "reason": "DataFrame.coalesce() is a no-op in SCOS - Snowflake manages partitioning",
        "category": "No-Op API",
    },
}

# Modules/imports that indicate unsupported features (risk on 0-1 scale)
UNSUPPORTED_IMPORTS = {
    "pyspark.ml": {
        "risk": 1.0,
        "reason": "pyspark.ml (MLlib) is not supported in SCOS",
        "category": "Unsupported Module",
        "how_to_fix": "Use Snowflake ML or Snowpark ML instead",
    },
    "pyspark.streaming": {
        "risk": 1.0,
        "reason": "pyspark.streaming is not supported in SCOS",
        "category": "Unsupported Module",
        "how_to_fix": "Use Snowflake Streams and Tasks for streaming workloads",
    },
    "pyspark.mllib": {
        "risk": 1.0,
        "reason": "pyspark.mllib is not supported in SCOS",
        "category": "Unsupported Module",
        "how_to_fix": "Use Snowflake ML or Snowpark ML instead",
    },
}

# =============================================================================
# DATA SOURCE LIMITATIONS
# =============================================================================

# File formats that are completely unsupported (risk on 0-1 scale)
UNSUPPORTED_FORMATS = {
    "avro": {
        "risk": 1.0,
        "reason": "Avro format is not supported in SCOS",
        "category": "Unsupported Format",
        "how_to_fix": "Convert data to Parquet, CSV, or JSON format",
    },
    "orc": {
        "risk": 1.0,
        "reason": "ORC format is not supported in SCOS",
        "category": "Unsupported Format",
        "how_to_fix": "Convert data to Parquet, CSV, or JSON format",
    },
    "xml": {
        "risk": 1.0,
        "reason": "XML format is not supported in SCOS",
        "category": "Unsupported Format",
        "how_to_fix": "Convert data to JSON or parse XML before loading",
    },
    "delta": {
        "risk": 1.0,
        "reason": "Delta format is not supported in SCOS",
        "category": "Unsupported Format",
        "how_to_fix": "Convert data to Parquet, CSV, or JSON format",
    },
    "binaryFile": {
        "risk": 1.0,
        "reason": "Binary format is not supported in SCOS",
        "category": "Unsupported Format",
        "how_to_fix": "Convert data to Parquet, CSV, or JSON format",
    },
}

# File formats with partial support and their limitations
FORMAT_LIMITATIONS = {
    "csv": {
        "unsupported_modes": ["ignore"],
        "unsupported_options": [
            "quote",
            "quoteAll",
            "escapeQuotes",
            "comment",
            "preferDate",
            "enforceSchema",
            "ignoreLeadingWhiteSpace",
            "ignoreTrailingWhiteSpace",
            "nanValue",
            "positiveInf",
            "negativeInf",
            "timestampNTZFormat",
            "enableDateTimeParsingFallback",
            "maxColumns",
            "maxCharsPerColumn",
            "mode",
            "columnNameOfCorruptRecord",
            "charToEscapeQuoteEscaping",
            "samplingRatio",
            "emptyValue",
            "locale",
            "lineSep",
            "unescapedQuoteHandling",
        ],
    },
    "json": {
        "unsupported_modes": ["ignore"],
        "unsupported_options": [
            "timeZone",
            "primitiveSCOSString",
            "prefersDecimal",
            "allowComments",
            "allowUnquotedFieldNames",
            "allowSingleQuotes",
            "allowNumericLeadingZeros",
            "allowBackslashEscapingAnyCharacter",
            "mode",
            "columnNameOfCorruptRecord",
            "timestampNTZFormat",
            "enableDateTimeParsingFallback",
            "allowUnquotedControlChars",
            "encoding",
            "lineSep",
            "samplingRatio",
            "dropFieldIfAllNull",
            "locale",
            "allowNonNumericNumbers",
            "compression",
            "ignoreNullFields",
        ],
    },
    "parquet": {
        "unsupported_modes": ["ignore"],
        "unsupported_options": [
            "datetimeRebaseMode",
            "int96RebaseMode",
            "mergeSchema",
        ],
    },
    "text": {
        "unsupported_modes": ["ignore"],
        "unsupported_options": [],
    },
}

# Unsupported data types (risk on 0-1 scale)
UNSUPPORTED_DATATYPES = {}

# =============================================================================
# SUPPORTED SPARK CONFIGS IN SCOS
# Configs NOT in this set are no-ops (silently ignored by SCOS)
# Based on src/snowflake/snowpark_connect/config.py
# =============================================================================

# Configs that have actual effects in SCOS (Snowflake session, Snowpark behavior, etc.)
SUPPORTED_CONFIGS = {
    # Configs with Snowflake session effects (set_snowflake_parameters)
    "spark.sql.session.timeZone",
    "spark.sql.globalTempDatabase",
    "spark.sql.parquet.outputTimestampType",
    # Configs with Snowpark session effects (snowpark_config_mapping)
    "spark.app.name",
    "snowpark.connect.udf.imports",
    "snowpark.connect.udf.python.imports",
    "snowpark.connect.udf.java.imports",
    # Configs read by SCOS logic (default_global_config)
    "spark.driver.host",
    "spark.sql.pyspark.inferNestedDictAsStruct.enabled",
    "spark.sql.pyspark.legacy.inferArrayTypeFromFirstElement.enabled",
    "spark.sql.repl.eagerEval.enabled",
    "spark.sql.repl.eagerEval.maxNumRows",
    "spark.sql.repl.eagerEval.truncate",
    "spark.sql.session.localRelationCacheThreshold",
    "spark.sql.timestampType",
    "spark.sql.crossJoin.enabled",
    "spark.sql.caseSensitive",
    "spark.sql.mapKeyDedupPolicy",
    "spark.sql.ansi.enabled",
    "spark.sql.legacy.allowHashOnMapType",
    "spark.sql.sources.default",
    "spark.Catalog.databaseFilterInformationSchema",
    "spark.sql.parser.quotedRegexColumnNames",
    "spark.sql.execution.arrow.maxRecordsPerBatch",
    "spark.sql.legacy.dataset.nameNonStructGroupingKeyAsValue",
    # Session config whitelist (AWS/Azure credentials)
    "spark.hadoop.fs.s3a.access.key",
    "spark.hadoop.fs.s3a.secret.key",
    "spark.hadoop.fs.s3a.session.token",
    "spark.hadoop.fs.s3a.server-side-encryption.key",
    "spark.hadoop.fs.s3a.assumed.role.arn",
    "spark.sql.execution.pythonUDTF.arrow.enabled",
    "spark.sql.tvf.allowMultipleTableArguments.enabled",
    "spark.sql.parquet.enable.summary-metadata",
    "spark.jars",
    "mapreduce.fileoutputcommitter.marksuccessfuljobs",
    "parquet.enable.summary-metadata",
    # Snowpark Connect specific configs (these have effects in SCOS)
    # Note: All snowpark.connect.* configs are also matched by prefix, listed here for documentation
    "snowpark.connect.sql.passthrough",  # Enables SQL passthrough mode
    "snowpark.connect.cte.optimization_enabled",  # Enables CTE optimization
    "snowpark.connect.iceberg.external_volume",  # Iceberg external volume
    "snowpark.connect.sql.identifiers.auto-uppercase",  # Identifier case handling
    "snowpark.connect.sql.partition.external_table_location",  # External table location
    "snowpark.connect.udtf.compatibility_mode",  # UDTF compatibility
    "snowpark.connect.views.duplicate_column_names_handling_mode",  # View column handling
    "snowpark.connect.temporary.views.create_in_snowflake",  # Temp view creation
    "snowpark.connect.enable_snowflake_extension_behavior",  # Snowflake extensions
    "snowpark.connect.describe_cache_ttl_seconds",  # Describe cache TTL
    "snowpark.connect.structured_types.fix",  # Structured types fix
    "snowpark.connect.scala.version",  # Scala version for Java UDFs
    "snowpark.connect.integralTypesEmulation",  # Integral types emulation
    "snowpark.connect.localRelation.optimizeSmallData",  # Local relation optimization
    "snowpark.connect.parquet.useVectorizedScanner",  # Parquet vectorized scanner
    "snowpark.connect.parquet.useLogicalType",  # Parquet logical types
    "snowpark.connect.handleIntegralOverflow",  # Integral overflow handling
    "snowpark.connect.version",  # SCOS version (read-only)
    # Snowflake specific configs
    "snowflake.repartition.for.writes",  # Repartition for writes
}


def is_supported_config(config_key: str) -> bool:
    """Check if a Spark config key is supported by SCOS."""
    # Check exact match
    if config_key in SUPPORTED_CONFIGS:
        return True
    return False


def check_config_no_ops(code: str) -> list[dict]:
    """
    Check for Spark config settings that are no-ops in SCOS.

    Detects patterns like:
    - spark.conf.set("key", "value")
    - .config("key", "value") in builder chains
    - SparkConf().set("key", "value")

    Returns:
        List of issues found with no-op configs
    """
    issues = []

    # Pattern 1: spark.conf.set("key", "value") or spark.conf.set('key', 'value')
    conf_set_pattern = r'\.conf\.set\s*\(\s*["\']([^"\']+)["\']\s*,'

    # Pattern 2: .config("key", "value") in builder chains
    config_pattern = r'\.config\s*\(\s*["\']([^"\']+)["\']\s*,'

    # Pattern 3: SparkConf().set("key", "value")
    sparkconf_set_pattern = r'SparkConf\s*\(\s*\).*\.set\s*\(\s*["\']([^"\']+)["\']\s*,'

    all_patterns = [
        (conf_set_pattern, "spark.conf.set()"),
        (config_pattern, ".config()"),
        (sparkconf_set_pattern, "SparkConf().set()"),
    ]

    found_configs = set()  # Track found configs to avoid duplicates

    for pattern, pattern_name in all_patterns:
        for match in re.finditer(pattern, code):
            config_key = match.group(1)

            # Skip if already reported
            if config_key in found_configs:
                continue

            # Check if this config is supported
            if not is_supported_config(config_key):
                found_configs.add(config_key)
                issues.append(
                    {
                        "api": config_key,
                        "risk": 0.2,  # Low risk - config is just ignored
                        "reason": f"Spark config '{config_key}' is a no-op in SCOS - this setting has no effect",
                        "category": "No-Op Config",
                        "how_to_fix": f"Remove or comment out the config setting for '{config_key}'. Add comment: # SCOS: No-op config, ignored",
                        "pattern": pattern_name,
                    }
                )

    return issues


@dataclass
class APIInfo:
    """API compatibility information."""

    name: str
    api_type: str
    compatibility: str
    is_supported: bool
    score: float | None  # 0-1 scale

    @classmethod
    def from_csv_row(cls, row: dict) -> "APIInfo":
        compat = row.get("COMPATIBILITY", "UNKNOWN").strip().upper()
        # Normalize compatibility values
        if compat.startswith("SHEET_"):
            compat = compat.replace("SHEET_", "")
        if compat not in COMPAT_SCORES:
            compat = "UNKNOWN"

        return cls(
            name=row.get("API", ""),
            api_type=row.get("TYPE", ""),
            compatibility=compat,
            is_supported=row.get("IS_SUPPORTED", "").lower() == "true",
            score=COMPAT_SCORES.get(compat),
        )


def load_api_compatibility(csv_path: Path) -> tuple[dict[str, APIInfo], set[str]]:
    """
    Load API compatibility data from CSV.

    Returns:
        - api_map: dict mapping API names to APIInfo
        - all_methods: set of all method/function names for detection
    """
    api_map = {}
    all_methods = set()

    if not csv_path.exists():
        logger.warning(f"Warning: API compatibility CSV not found at {csv_path}")
        return api_map, all_methods

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            info = APIInfo.from_csv_row(row)
            if info.name:
                # Store by full path
                api_map[info.name] = info

                # Also store by short name (last part) for easier lookup
                # Prefer BETTER compatibility when there are conflicts
                short_name = info.name.split(".")[-1]
                if short_name not in api_map:
                    api_map[short_name] = info
                elif info.score is not None:
                    existing = api_map[short_name]
                    # Prefer higher compatibility score (D0=100 > D1=80 > D2=50 > NONE=0)
                    if existing.score is None or info.score > existing.score:
                        api_map[short_name] = info

                # Add to methods set (for function/method types)
                if info.api_type in ("function", "method"):
                    all_methods.add(short_name)

    return api_map, all_methods


def has_rdd_usage(code: str) -> tuple[bool, str | None]:
    """
    Check if code contains RDD patterns.

    Returns:
        - (True, reason) if RDD usage detected
        - (False, None) otherwise
    """
    code_lower = code.lower()

    # Check for RDD access patterns
    for pattern in RDD_PATTERNS:
        if pattern.lower() in code_lower:
            return True, f"Uses '{pattern}' which is not supported in SCOS"

    # Check for RDD type annotations (e.g., -> RDD, : RDD)
    if re.search(r":\s*RDD\b|->.*\bRDD\b", code):
        return True, "Uses RDD type annotation which indicates RDD usage"

    # Check if it looks like RDD method chain (e.g., .map(...).filter(...))
    # Only flag if we see RDD-specific patterns
    if (
        ".rdd" in code_lower
        or "sparkcontext" in code_lower
        or re.search(r"\bsc\.", code_lower)
    ):
        for method in RDD_METHODS:
            if f".{method.lower()}(" in code_lower:
                return True, f"RDD operation '.{method}()' is not supported in SCOS"

    return False, None


def check_unsupported_apis(code: str) -> list[dict]:
    """
    Check for unsupported Spark APIs in code.

    Returns:
        List of issues found, each with risk, reason, category, how_to_fix
    """
    issues = []

    # Check for unsupported imports
    for module, info in UNSUPPORTED_IMPORTS.items():
        # Check for import statements
        if f"import {module}" in code or f"from {module}" in code:
            issues.append(
                {
                    "api": module,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                    "how_to_fix": info.get("how_to_fix"),
                }
            )

    # Check for unsupported/no-op DataFrame methods
    for method, info in UNSUPPORTED_APIS.items():
        if f".{method}(" in code:
            issues.append(
                {
                    "api": method,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                    "how_to_fix": info.get("how_to_fix"),
                }
            )

    # Check for unsupported data types in schema definitions
    for dtype, info in UNSUPPORTED_DATATYPES.items():
        if dtype in code:
            issues.append(
                {
                    "api": dtype,
                    "risk": info["risk"],
                    "reason": info["reason"],
                    "category": info["category"],
                    "how_to_fix": info.get("how_to_fix"),
                }
            )

    return issues


def check_data_source_issues(code: str) -> list[dict]:
    """
    Check for data source compatibility issues.

    Returns:
        List of issues found with format/option problems
    """
    issues = []
    code_lower = code.lower()

    # Check for unsupported file formats
    # Pattern: .format("avro") or .load("file.avro")
    for fmt, info in UNSUPPORTED_FORMATS.items():
        patterns = [
            f'.format("{fmt}")',
            f".format('{fmt}')",
            f".{fmt}(",  # e.g., .avro(), .orc()
            f'.load("{fmt}',
            f".load('{fmt}",
        ]
        for pattern in patterns:
            if pattern.lower() in code_lower:
                issues.append(
                    {
                        "format": fmt,
                        "risk": info["risk"],
                        "reason": info["reason"],
                        "category": info["category"],
                        "how_to_fix": info.get("how_to_fix"),
                    }
                )
                break  # Only report once per format

    # Check file extensions in paths
    for fmt in UNSUPPORTED_FORMATS:
        if f".{fmt}" in code_lower and ("load(" in code_lower or "read" in code_lower):
            info = UNSUPPORTED_FORMATS[fmt]
            # Avoid duplicate if already caught above
            if not any(i.get("format") == fmt for i in issues):
                issues.append(
                    {
                        "format": fmt,
                        "risk": info["risk"],
                        "reason": info["reason"],
                        "category": info["category"],
                        "how_to_fix": info.get("how_to_fix"),
                    }
                )

    # Check for unsupported save modes
    for fmt, limits in FORMAT_LIMITATIONS.items():
        # Only check if this format is being used
        if (
            f'.format("{fmt}")' in code_lower
            or f".format('{fmt}')" in code_lower
            or f".{fmt}(" in code_lower
        ):
            for mode in limits.get("unsupported_modes", []):
                mode_patterns = [
                    f'.mode("{mode}")',
                    f".mode('{mode}')",
                    f'.mode("{mode.lower()}")',
                    f".mode('{mode.lower()}')",
                ]
                for pattern in mode_patterns:
                    if pattern.lower() in code_lower:
                        issues.append(
                            {
                                "format": fmt,
                                "risk": 0.7,
                                "reason": f"Save mode '{mode}' is not supported for {fmt.upper()} in SCOS",
                                "category": "Unsupported Save Mode",
                                "how_to_fix": f"Use 'overwrite' or 'errorifexists' mode instead of '{mode}'",
                            }
                        )
                        break

    # Check for unsupported options
    for fmt, limits in FORMAT_LIMITATIONS.items():
        if (
            f'.format("{fmt}")' in code_lower
            or f".format('{fmt}')" in code_lower
            or f".{fmt}(" in code_lower
        ):
            for opt in limits.get("unsupported_options", []):
                opt_patterns = [
                    f'.option("{opt}"',
                    f".option('{opt}'",
                ]
                for pattern in opt_patterns:
                    if pattern.lower() in code_lower:
                        issues.append(
                            {
                                "format": fmt,
                                "risk": 0.5,
                                "reason": f"Option '{opt}' is not supported for {fmt.upper()} in SCOS",
                                "category": "Unsupported Option",
                                "how_to_fix": f"Remove or work around the '{opt}' option",
                            }
                        )
                        break

    # Check for file read operations - performance optimization
    # Reading from external files (cloud storage, local paths) may be slower than
    # reading from Snowflake internal stages. Add advisory for any file read.
    file_read_patterns = [
        (r"\.read\.csv\s*\(", "csv"),
        (r"\.read\.json\s*\(", "json"),
        (r"\.read\.parquet\s*\(", "parquet"),
        (r"\.read\.text\s*\(", "text"),
        (r"\.read\.orc\s*\(", "orc"),
        (r"\.load\s*\(", "load"),
    ]

    for pattern, read_type in file_read_patterns:
        if re.search(pattern, code, re.IGNORECASE):
            issues.append(
                {
                    "api": f"file read ({read_type})",
                    "risk": 0.2,
                    "reason": (
                        "Reading from external files (S3, Azure, GCS, local paths) may be slower than "
                        "reading from Snowflake internal stage. For better performance, "
                        "consider uploading files to a Snowflake stage first."
                    ),
                    "category": "Performance Optimization",
                    "how_to_fix": (
                        "Upload files to a Snowflake stage using session.file.put() for faster processing. "
                        "Example: session.file.put('file:///local/path/data.csv', '@MY_STAGE/data/', auto_compress=False). "
                    ),
                }
            )
            break

    return issues


def _build_assessment_text(preliminary_assessment: dict) -> str:
    """Build preliminary assessment text for LLM prompt."""
    assessment_parts = []

    scos_issues = preliminary_assessment.get("scos_issues", [])
    if scos_issues:
        assessment_parts.append("SCOS Compatibility Issues:")
        for issue in scos_issues:
            issue_name = issue.get("api") or issue.get("format", "unknown")
            assessment_parts.append(
                f"  - {issue_name}: {issue['reason']} (Risk: {issue['risk'] * 100:.0f}%)"
            )
            if issue.get("how_to_fix"):
                assessment_parts.append(f"    Fix: {issue['how_to_fix']}")

    api_risk = preliminary_assessment.get("api_risk", 0)
    if api_risk > 0:
        assessment_parts.append(f"\nAPI Compatibility Risk: {api_risk * 100:.0f}%")
        func_compat = preliminary_assessment.get("func_compatibility", [])
        for f in func_compat:
            if f.get("score", 1.0) < 1.0:
                assessment_parts.append(
                    f"  - {f['name']}: {f['compatibility']} (score: {f['score'] * 100:.0f}%)"
                )

    scos_risk = preliminary_assessment.get("scos_risk", 0)
    if scos_risk > 0:
        assessment_parts.append(f"\nSCOS Issues Risk: {scos_risk * 100:.0f}%")

    return (
        "\n".join(assessment_parts)
        if assessment_parts
        else "No rule-based issues detected."
    )


def _build_patterns_text(matching_patterns: list[dict]) -> str:
    """Build matching patterns text for LLM prompt."""
    if not matching_patterns:
        return "No similar failing test cases found above similarity threshold."

    patterns_text_parts = []
    for i, p in enumerate(matching_patterns, 1):
        patterns_text_parts.append(
            f"""TEST CASE #{i} (Cosine similarity: {p.get('score', 0.0):.1%})
Test Name: {p.get('test_name', 'N/A')}
Code/SQL:
```
{p.get('code', '')}
```
Root Cause: {p.get('root_cause', 'N/A')}
Additional Notes: {p.get('additional_notes', 'N/A')}"""
        )
    return "\n\n".join(patterns_text_parts)


def predict_compatibility_batch(
    session: Session,
    batch_items: list[dict],
    model: str = DEFAULT_LLM_MODEL,
) -> dict[str, dict]:
    """
    Predict compatibility for multiple code blocks in a single LLM call.

    Args:
        session: Snowflake session
        batch_items: List of dicts with keys:
            - block_id: Unique identifier for this block
            - input_code: The code being analyzed
            - matching_patterns: List of similar failing test cases from RAG
            - preliminary_assessment: Dict with preliminary risk info
        model: LLM model to use

    Returns:
        Dict mapping block_id -> LLM result dict
    """
    if not batch_items:
        return {}

    # Build the combined prompt for all blocks
    code_blocks_parts = []
    for item in batch_items:
        block_id = item["block_id"]
        input_code = item["input_code"]
        matching_patterns = item.get("matching_patterns", [])
        preliminary_assessment = item.get("preliminary_assessment", {})

        assessment_text = _build_assessment_text(preliminary_assessment)
        patterns_text = _build_patterns_text(matching_patterns)

        code_blocks_parts.append(
            f"""### BLOCK {block_id}

```python
{input_code}
```

**Preliminary Assessment:**
{assessment_text}

**Similar Failing Test Cases:**
{patterns_text}

---"""
        )

    code_blocks_text = "\n\n".join(code_blocks_parts)

    prompt = PROMPT_PREDICT_COMPATIBILITY_BATCH.format(
        code_blocks_text=code_blocks_text,
        num_blocks=len(batch_items),
    )

    try:
        # Use temperature=0 for deterministic output
        options = CompleteOptions(temperature=0.0)
        response = cortex_complete(model, prompt, options=options, session=session)

        # Strip markdown code block if present
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            response = "\n".join(lines)

        results_list = json.loads(response)

        # Convert list to dict keyed by block_id
        results_dict = {}
        for result in results_list:
            block_id = result.get("block_id")
            if block_id:
                results_dict[block_id] = result

        # Assert that response contains all input batches
        input_block_ids = {item["block_id"] for item in batch_items}
        response_block_ids = set(results_dict.keys())
        missing_ids = input_block_ids - response_block_ids
        assert not missing_ids, (
            f"LLM response missing {len(missing_ids)} block(s): {missing_ids}. "
            f"Expected {len(input_block_ids)} blocks, got {len(response_block_ids)}."
        )

        return results_dict

    except json.JSONDecodeError as e:
        raise ValueError(
            f"Cortex returned invalid JSON. Response (first 500 chars): {response[:500]}...\n"
            f"JSON error: {e}"
        )
    except AssertionError:
        # Re-raise assertion errors (missing block IDs)
        raise
    except Exception as e:
        raise RuntimeError(f"Batch LLM prediction failed: {e}")


@dataclass
class CodeBlock:
    """A block of code extracted from a PySpark file."""

    code: str
    line_start: int
    line_end: int
    block_type: str  # "sql", "expr", "method_chain", "statement"
    functions: list[str]  # Functions/methods found in this block

    @property
    def normalized_code(self) -> str:
        """Return normalized code for RAG queries (comments removed, whitespace normalized)."""
        return normalize_code_lightweight(self.code)


class PySparkExtractor(ast.NodeVisitor):
    """Extract PySpark code blocks using AST."""

    def __init__(
        self, source_lines: list[str], pyspark_methods: set[str] | None = None
    ):
        self.source_lines = source_lines
        self.blocks: list[CodeBlock] = []
        # Use provided methods or fall back to common ones
        self.pyspark_methods = pyspark_methods or {
            "select",
            "filter",
            "where",
            "groupBy",
            "agg",
            "join",
            "orderBy",
            "sort",
            "withColumn",
            "drop",
            "distinct",
            "union",
            "intersect",
            "subtract",
            "limit",
            "sample",
            "createDataFrame",
            "read",
            "write",
            "show",
            "collect",
        }

    def get_source(self, node: ast.AST) -> str:
        """Get source code for a node."""
        try:
            return ast.get_source_segment("\n".join(self.source_lines), node) or ""
        except Exception:
            # Fallback: get lines
            start = node.lineno - 1
            end = getattr(node, "end_lineno", node.lineno)
            return "\n".join(self.source_lines[start:end])

    def extract_string_value(self, node: ast.AST) -> str | None:
        """Extract string value from a node."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            # f-string - try to get parts
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant):
                    parts.append(str(value.value))
                elif isinstance(value, ast.FormattedValue):
                    # Extract source of the expression inside the f-string
                    parts.append("<" + self.get_source(value.value) + ">")
            return "".join(parts) if parts else None
        return None

    def extract_functions(self, code: str) -> list[str]:
        """Extract function/method names from code."""
        functions = []
        # Pattern for function calls: word followed by (
        pattern = r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
        for match in re.finditer(pattern, code):
            func_name = match.group(1)
            # Skip common Python keywords and builtins
            if func_name not in [
                "if",
                "for",
                "while",
                "with",
                "def",
                "class",
                "print",
                "len",
                "str",
                "int",
                "list",
                "dict",
                "set",
                "tuple",
            ]:
                functions.append(func_name)
        return list(set(functions))

    def _has_call_nodes(self, node: ast.AST) -> bool:
        """Check if an AST node contains any function/method calls."""
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                return True
        return False

    def visit_Call(self, node: ast.Call):
        """Visit function/method calls."""
        # Check for spark.sql(...) or session.sql(...)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "sql":
            if node.args:
                sql_str = self.extract_string_value(node.args[0])
                if sql_str:
                    self.blocks.append(
                        CodeBlock(
                            code=sql_str,
                            line_start=node.lineno,
                            line_end=getattr(node, "end_lineno", node.lineno),
                            block_type="sql",
                            functions=self.extract_functions(sql_str),
                        )
                    )

        # Check for expr(...)
        if isinstance(node.func, ast.Name) and node.func.id == "expr":
            if node.args:
                expr_str = self.extract_string_value(node.args[0])
                if expr_str:
                    self.blocks.append(
                        CodeBlock(
                            code=expr_str,
                            line_start=node.lineno,
                            line_end=getattr(node, "end_lineno", node.lineno),
                            block_type="expr",
                            functions=self.extract_functions(expr_str),
                        )
                    )

        # Check for selectExpr(...)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "selectExpr":
            for arg in node.args:
                expr_str = self.extract_string_value(arg)
                if expr_str:
                    self.blocks.append(
                        CodeBlock(
                            code=expr_str,
                            line_start=node.lineno,
                            line_end=getattr(node, "end_lineno", node.lineno),
                            block_type="selectExpr",
                            functions=self.extract_functions(expr_str),
                        )
                    )

        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr):
        """Visit expression statements (often method chains)."""
        source = self.get_source(node)

        # Check if it contains any known PySpark method
        if any(f".{method}(" in source for method in self.pyspark_methods):
            self.blocks.append(
                CodeBlock(
                    code=source,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    block_type="method_chain",
                    functions=self.extract_functions(source),
                )
            )

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        """Visit assignments (df = spark.read..., etc.)."""
        # Skip simple literal assignments (no function calls)
        # This filters out: var_a = 10, my_list = ["a", "b"], config = {"k": "v"}
        if not self._has_call_nodes(node.value):
            self.generic_visit(node)
            return

        source = self.get_source(node)

        # Check if it involves PySpark operations (spark/session object or any known method)
        has_spark = "spark" in source.lower() or "session" in source.lower()
        has_pyspark_method = any(method in source for method in self.pyspark_methods)

        if has_spark or has_pyspark_method:
            self.blocks.append(
                CodeBlock(
                    code=source,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    block_type="assignment",
                    functions=self.extract_functions(source),
                )
            )

        self.generic_visit(node)


def extract_code_blocks(
    file_path: Path, pyspark_methods: set[str] | None = None
) -> list[CodeBlock]:
    """Extract PySpark code blocks from a Python file using AST."""
    try:
        source = file_path.read_text(encoding="utf-8")
        source_lines = source.splitlines()
        tree = ast.parse(source)

        extractor = PySparkExtractor(source_lines, pyspark_methods)
        extractor.visit(tree)

        return extractor.blocks
    except SyntaxError as e:
        logger.warning(f"Warning: Syntax error in {file_path}: {e}")
        return []
    except Exception as e:
        logger.warning(f"Warning: Could not parse {file_path}: {e}")
        return []


def find_pyspark_files(path: Path) -> list[Path]:
    """Find all Python files in the given path."""
    if path.is_file():
        if path.suffix == ".py":
            return [path]
        return []
    return list(path.rglob("*.py"))


def _process_single_block(
    block: CodeBlock,
    scos_rag: SCOSCortexRAG,
    api_compat: dict[str, APIInfo],
    file_path: Path,
    similarity_threshold: float,
) -> tuple[dict | None, dict | None]:
    """
    Process a single code block for compatibility analysis.

    Returns:
        Tuple of (rdd_result, block_to_analyze) where:
        - rdd_result: If block is RDD, contains the final result dict
        - block_to_analyze: If block needs LLM analysis, contains preliminary data
        Both can be None if block is SCOS compatible.
    """
    # Check for RDD usage first (always 100% risk)
    is_rdd, rdd_reason = has_rdd_usage(block.code)

    if is_rdd:
        # RDD operations are not supported - 100% risk, no LLM needed
        return (
            {
                "file": str(file_path),
                "lines": f"{block.line_start}-{block.line_end}",
                "code": block.code,
                "final_risk": 1.0,
                "root_cause": rdd_reason,
                "explanation": "RDD operations are not supported in SCOS.",
                "fix": "Convert to DataFrame operations. RDD operations are not supported in SCOS.",
                "confidence": "HIGH",
            },
            None,
        )

    # Check for unsupported Spark APIs (from Snowflake docs)
    api_issues = check_unsupported_apis(block.code)

    # Check for data source issues (unsupported formats, modes, options)
    datasource_issues = check_data_source_issues(block.code)

    # Check for Spark configs that are no-ops in SCOS
    config_issues = check_config_no_ops(block.code)

    # Combine all SCOS-specific issues
    scos_issues = api_issues + datasource_issues + config_issues

    # Calculate max risk from SCOS issues
    scos_risk = max((issue["risk"] for issue in scos_issues), default=0)

    # Get unified RAG prediction - use normalized code for better matching
    prediction = scos_rag.predict_failure(block.normalized_code)

    # Collect candidates from unified RAG
    candidates = []

    for p in prediction.get("similar_patterns", []):
        if p.root_cause:  # Only consider if it has a known issue
            candidates.append(
                {
                    "source": "UNIFIED_RAG",
                    "code": p.code,
                    "score": p.score,
                    "root_cause": p.root_cause,
                    "test_name": p.test_name,
                    "additional_notes": p.additional_notes,
                }
            )

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Filter candidates by similarity threshold (both are 0-1)
    candidates = [c for c in candidates if c["score"] >= similarity_threshold]

    # Select top matches (up to 3)
    matching_patterns = []
    failure_likelihood = 0.0

    if candidates:
        # Best match sets the base likelihood
        best_match = candidates[0]
        failure_likelihood = best_match["score"]
        matching_patterns.append(best_match)

        # Add up to 2 more if they have relatively high scores
        # (e.g., at least 85% of the best match's score)
        relative_threshold = failure_likelihood * 0.85
        for c in candidates[1:]:
            if len(matching_patterns) >= 3:
                break
            if c["score"] >= relative_threshold:
                matching_patterns.append(c)

    # If no issues detected from any source and no matching patterns above threshold,
    # skip this code block - it's considered SCOS compatible
    if not scos_issues and not matching_patterns:
        return None, None

    # Get API compatibility for functions in this block
    func_compat = []
    min_compat_score = 1.0
    for func in block.functions:
        if func in api_compat:
            info = api_compat[func]
            func_compat.append(
                {
                    "name": func,
                    "compatibility": info.compatibility,
                    "score": info.score,
                    "supported": info.is_supported,
                }
            )
            if info.score is not None and info.score < min_compat_score:
                min_compat_score = info.score

    # Calculate preliminary risk from rule-based sources (all on 0-1 scale)
    api_risk = 1.0 - min_compat_score if min_compat_score < 1.0 else 0.0
    preliminary_risk = max(failure_likelihood, api_risk, scos_risk)

    # Prepare preliminary assessment for LLM
    preliminary_assessment = {
        "scos_issues": scos_issues,
        "scos_risk": scos_risk,
        "api_risk": api_risk,
        "func_compatibility": func_compat,
        "rag_similarity": failure_likelihood,
    }

    # Return block data for batch LLM processing
    return (
        None,
        {
            "block": block,
            "matching_patterns": matching_patterns,
            "preliminary_assessment": preliminary_assessment,
            "preliminary_risk": preliminary_risk,
            "min_compat_score": min_compat_score,
            "func_compat": func_compat,
            "scos_issues": scos_issues,
            "scos_risk": scos_risk,
            "failure_likelihood": failure_likelihood,
        },
    )


# Default number of parallel workers for block processing
DEFAULT_PARALLEL_WORKERS = 8


def analyze_file(
    scos_rag: SCOSCortexRAG,
    api_compat: dict[str, APIInfo],
    pyspark_methods: set[str],
    file_path: Path,
    risk_threshold: float = 0.1,
    session: Session | None = None,
    similarity_threshold: float = 0.55,
    llm_batch_size: int = DEFAULT_LLM_BATCH_SIZE,
    parallel_workers: int = DEFAULT_PARALLEL_WORKERS,
) -> list[dict]:
    """
    Analyze a PySpark file for compatibility issues.

    Args:
        scos_rag: Unified RAG service for SQL and DataFrame patterns
        api_compat: API compatibility lookup
        pyspark_methods: Set of known PySpark methods
        file_path: Path to the file to analyze
        risk_threshold: Minimum risk (0-1) to report (default: 0.1 = 10%)
        session: Snowflake session (required for LLM validation)
        similarity_threshold: Minimum cosine similarity (0-1) to consider patterns relevant (default: 0.55)
        llm_batch_size: Number of code blocks to analyze per LLM call (default: 10)
        parallel_workers: Number of parallel workers for block processing (default: 8)
    """
    results = []
    blocks = extract_code_blocks(file_path, pyspark_methods)

    if not blocks:
        return results

    # Phase 1: Process all blocks in parallel to collect preliminary data
    blocks_to_analyze = []  # List of block preliminary data for LLM processing

    # Process blocks in parallel using ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        # Submit all block processing tasks
        future_to_block = {
            executor.submit(
                _process_single_block,
                block,
                scos_rag,
                api_compat,
                file_path,
                similarity_threshold,
            ): block
            for block in blocks
        }

        # Collect results as they complete
        for future in as_completed(future_to_block):
            block = future_to_block[future]
            try:
                rdd_result, block_data = future.result()

                if rdd_result is not None:
                    # RDD block - add directly to results
                    results.append(rdd_result)
                elif block_data is not None:
                    # Block needs LLM analysis
                    blocks_to_analyze.append(block_data)
                # else: block is SCOS compatible, skip it

            except Exception as e:
                logger.error(
                    f"Error processing block at lines {block.line_start}-{block.line_end}: {e}"
                )
                raise

    # Phase 2: Run batched LLM calls in parallel
    llm_results = {}  # block_id -> llm_result
    import time as _time

    if session and blocks_to_analyze:
        total_blocks = len(blocks_to_analyze)
        num_batches = (total_blocks + llm_batch_size - 1) // llm_batch_size

        logger.info(
            f"    Running LLM analysis ({parallel_workers} workers): {total_blocks} blocks in {num_batches} batch(es)..."
        )

        # Prepare all batches
        all_batch_items = []
        for batch_idx in range(0, total_blocks, llm_batch_size):
            batch = blocks_to_analyze[batch_idx : batch_idx + llm_batch_size]
            batch_num = batch_idx // llm_batch_size + 1

            batch_items = []
            for item in batch:
                block = item["block"]
                block_id = f"{block.line_start}-{block.line_end}"
                batch_items.append(
                    {
                        "block_id": block_id,
                        "input_code": block.normalized_code,
                        "matching_patterns": item["matching_patterns"],
                        "preliminary_assessment": item["preliminary_assessment"],
                    }
                )
            all_batch_items.append((batch_num, batch_items))

        # Parallel execution using ThreadPoolExecutor
        def _process_batch(args):
            batch_num, batch_items = args
            _start = _time.time()
            result = predict_compatibility_batch(session, batch_items)
            _elapsed = _time.time() - _start
            return batch_num, result, _elapsed

        _llm_start = _time.time()
        with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
            futures = {
                executor.submit(_process_batch, batch): batch[0]
                for batch in all_batch_items
            }
            for future in as_completed(futures):
                batch_num, batch_result, elapsed = future.result()
                logger.info(
                    f"      Batch {batch_num}/{num_batches}: completed in {elapsed:.1f}s"
                )
                llm_results.update(batch_result)

        _total_llm_time = _time.time() - _llm_start
        logger.info(
            f"    ⏱️  Total LLM time: {_total_llm_time:.1f}s for {num_batches} batches"
        )

    # Phase 3: Build final results using LLM responses
    for item in blocks_to_analyze:
        block = item["block"]
        block_id = f"{block.line_start}-{block.line_end}"
        matching_patterns = item["matching_patterns"]
        preliminary_risk = item["preliminary_risk"]
        min_compat_score = item["min_compat_score"]
        func_compat = item["func_compat"]
        scos_issues = item["scos_issues"]
        scos_risk = item["scos_risk"]
        failure_likelihood = item["failure_likelihood"]

        # Get LLM result for this block
        llm_result = llm_results.get(block_id)

        final_risk = preliminary_risk  # Default to preliminary if LLM fails
        root_cause = None
        how_to_fix = None

        if llm_result:
            # LLM determines the final risk
            final_risk = llm_result.get("final_risk", preliminary_risk)
            root_cause = llm_result.get("root_cause")
            how_to_fix = llm_result.get("fix")

        # Fall back to rule-based root cause if LLM didn't provide one
        if not root_cause:
            if matching_patterns:
                best = matching_patterns[0]
                root_cause = best.get("root_cause")

            # If SCOS issues have higher risk, use their info
            if scos_issues and scos_risk >= failure_likelihood:
                top_issue = max(scos_issues, key=lambda x: x["risk"])
                root_cause = root_cause or top_issue["reason"]
                how_to_fix = how_to_fix or top_issue.get("how_to_fix")

        # Only report if final risk is above threshold
        if final_risk >= risk_threshold:
            explanation = (
                llm_result.get("explanation")
                if llm_result
                else f"Potential compatibility issue: {root_cause}"
            )
            confidence = (
                llm_result.get("confidence")
                if llm_result
                else ("HIGH" if final_risk >= 0.9 else "MEDIUM")
            )

            result = {
                "file": str(file_path),
                "lines": f"{block.line_start}-{block.line_end}",
                "code": block.code,
                "final_risk": final_risk,
                "root_cause": root_cause,
                "explanation": explanation,
                "fix": how_to_fix,
                "confidence": confidence,
            }

            results.append(result)

    return results


def print_json_results(results: list[dict]):
    """Print analysis results in JSON format."""
    print(json.dumps(results, indent=2))


def print_results(results: list[dict]):
    """Print analysis results."""
    if not results:
        print("\n✅ No potential issues found above threshold.")
        return

    print("\n" + "=" * 80)
    print("ANALYSIS RESULTS")
    print("=" * 80)
    print(f"Code blocks analyzed with potential issues: {len(results)}")

    for r in results:
        final_risk = r["final_risk"]
        # Choose icon based on risk
        if final_risk >= 0.7:
            risk_icon = "🔴"
        elif final_risk >= 0.3:
            risk_icon = "🟡"
        else:
            risk_icon = "🟢"

        print(f"\n{'-' * 80}")
        print(
            f"{risk_icon} {r['file']}:{r['lines']} - Final Risk: {final_risk * 100:.1f}%"
        )
        print(f"   Code: {r['code']}")

        if r.get("root_cause"):
            print(f"   Root Cause: {r['root_cause']}")

        if r.get("explanation"):
            print(f"   Explanation: {r['explanation']}")

        if r.get("fix"):
            print(f"   Fix: {r['fix']}")

        if r.get("confidence"):
            print(f"   Confidence: {r['confidence']}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze PySpark scripts for SCOS compatibility issues"
    )
    parser.add_argument(
        "--path",
        type=str,
        required=True,
        help="Path to a PySpark file or directory containing PySpark files",
    )
    parser.add_argument(
        "--connection",
        type=str,
        default="default",
        help="Snowflake connection name (default: default)",
    )
    parser.add_argument(
        "--risk-threshold",
        "-t",
        type=float,
        default=0.1,
        help="Minimum risk (0-1) to report (default: 0.1 = 10%%)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize the RAG services and load CSV data",
    )
    parser.add_argument(
        "--similarity-threshold",
        "-s",
        type=float,
        default=0.55,
        help="Minimum cosine similarity [-1.0, 1.0] to consider RAG patterns relevant (default: 0.55)",
    )
    parser.add_argument(
        "--batch-size",
        "-b",
        type=int,
        default=DEFAULT_LLM_BATCH_SIZE,
        help=f"Number of code blocks to analyze per LLM call (default: {DEFAULT_LLM_BATCH_SIZE})",
    )
    parser.add_argument(
        "--parallel-workers",
        "-p",
        type=int,
        default=DEFAULT_PARALLEL_WORKERS,
        help=f"Number of parallel workers for block processing (default: {DEFAULT_PARALLEL_WORKERS})",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    # Configure logging to stderr so it doesn't interfere with stdout (text/json) output
    # Set root logger to WARNING to suppress noisy library logs
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)

    # Set this script's logger to INFO to see our own messages
    logger.setLevel(logging.INFO)

    path = Path(args.path).expanduser()
    if not path.exists():
        logger.error(f"Error: Path does not exist: {path}")
        sys.exit(1)

    # Find PySpark files
    files = find_pyspark_files(path)
    logger.info(f"Found {len(files)} Python file(s) to analyze")

    # Load API compatibility data
    compat_csv = DATA_DIR / "api_compatibility.csv"
    logger.info(f"\nLoading API compatibility data from {compat_csv}...")
    api_compat, pyspark_methods = load_api_compatibility(compat_csv)
    logger.info(
        f"Loaded {len(api_compat)} API entries, {len(pyspark_methods)} methods/functions"
    )

    # Connect to Snowflake
    logger.info(f"\nConnecting to Snowflake (connection: {args.connection})...")
    try:
        session = Session.builder.config("connection_name", args.connection).create()
        # session.sql("use role accountadmin").collect()  # SCOS: Removed - not needed for analysis
    except Exception as e:
        logger.error(f"Error connecting to Snowflake: {e}")
        logger.info("\nMake sure you have a valid connection configured.")
        sys.exit(1)

    # Initialize unified RAG service (uses existing Cortex Search Service)
    scos_rag = SCOSCortexRAG(
        session,
        config=SCOSRAGConfig(
            table="SCOS_COMPAT_ISSUES",
            search_service="SCOS_COMPAT_ISSUES_SERVICE",
        ),
    )

    # Load data if --init flag is set
    if args.init:
        scos_rag.init()
        logger.info("Loading SCOS RAG data from data/...")
        total_count = 0

        # Load all normalized CSV files
        rag_files = [
            "df_test_rca_normalized.csv",
            "sql_test_rca_normalized.csv",
            "expectation_tests_xfail_rca_normalized.csv",
            "jira_rca_normalized.csv",
        ]
        for csv_file in rag_files:
            count = scos_rag.upload_csv(csv_file)
            logger.info(f"  Loaded {count} records from {csv_file}")
            total_count += count

        logger.info(f"  Total: {total_count} failure records loaded")

    # Analyze files
    logger.info(
        f"\nAnalyzing files (risk threshold: {args.risk_threshold * 100:.2f}%, similarity: {args.similarity_threshold}, batch size: {args.batch_size}, workers: {args.parallel_workers})..."
    )
    all_results = []
    for file_path in files:
        logger.info(f"  📄 {file_path.name}")
        results = analyze_file(
            scos_rag,
            api_compat,
            pyspark_methods,
            file_path,
            risk_threshold=args.risk_threshold,
            session=session,
            similarity_threshold=args.similarity_threshold,
            llm_batch_size=args.batch_size,
            parallel_workers=args.parallel_workers,
        )
        all_results.extend(results)

    # Sort by final risk (highest first)
    all_results = sorted(all_results, key=lambda x: x["final_risk"], reverse=True)

    # Print results
    if args.output_format == "json":
        print_json_results(all_results)
    else:
        print_results(all_results)

    # Cleanup
    session.close()


if __name__ == "__main__":
    main()
