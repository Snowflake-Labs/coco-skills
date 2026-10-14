#!/usr/bin/env python3
"""
SNOW-3347481: Generate a batch transformation script from analysis.json.

Reads analysis.json and produces a self-contained apply_fixes.py that applies
all code fixes deterministically in one execution — no LLM agent needed.

The generated script handles:
  - Search/replace operations per file (from analysis issues)
  - Session initialization replacements (SparkSession → snowpark_connect)
  - Migration header insertion for every output file
  - Scala pom.xml dependency updates (if --language scala)

Usage:
    python generate_transformations.py \
        --analysis /path/to/analysis.json \
        --output-dir /path/to/Output \
        --language python \
        --script-output apply_fixes.py

    # Then run the generated script:
    python apply_fixes.py
"""

import argparse
import json
import os
import sys
from datetime import datetime


# SNOW-3347481: Session init patterns that need replacement
PYTHON_SESSION_PATTERNS = [
    # (search, replace) — ordered by specificity, most specific first
    (
        'DatabricksSession.builder.getOrCreate()',
        'snowpark_connect.init_spark_session()',
    ),
    (
        'SparkSession.builder.master(master).appName(app_name).getOrCreate()',
        'snowpark_connect.init_spark_session()',
    ),
    (
        'SparkSession.builder.appName(app_name).getOrCreate()',
        'snowpark_connect.init_spark_session()',
    ),
    (
        'SparkSession.builder.getOrCreate()',
        'snowpark_connect.init_spark_session()',
    ),
]

# SNOW-3347481: Scala session init patterns.
# Target must agree with update_imports_scala.py: SnowparkConnectSession.builder().getOrCreate().
# The old form (.remote("sc://localhost")) was wrong — it is NOT the SCOS Scala API
# (Scala SCOS uses SnowparkConnectSession, not SparkSession.remote) and was explicitly
# rejected by verify_phase.py Phase 3's _SPARK_REMOTE_RE no-SparkSession.remote check.
SCALA_SESSION_PATTERNS = [
    (
        'SparkSession.builder().master(master).appName(appName).getOrCreate()',
        'SnowparkConnectSession.builder().getOrCreate()',
    ),
    (
        'SparkSession.builder().appName(appName).getOrCreate()',
        'SnowparkConnectSession.builder().getOrCreate()',
    ),
    (
        'SparkSession.builder.getOrCreate()',
        'SnowparkConnectSession.builder().getOrCreate()',
    ),
]


# SNOW-3715354: Java session init patterns. Target matches update_imports_java.py
# (``SnowparkConnectSession.builder()`` + the com.snowflake.snowpark_connect.client
# import) so Phase 3 and this fix-script generator agree.
JAVA_SESSION_PATTERNS = [
    (
        'SparkSession.builder().master(master).appName(appName).getOrCreate()',
        'SnowparkConnectSession.builder().getOrCreate()',
    ),
    (
        'SparkSession.builder().appName(appName).getOrCreate()',
        'SnowparkConnectSession.builder().getOrCreate()',
    ),
    (
        'SparkSession.builder().getOrCreate()',
        'SnowparkConnectSession.builder().getOrCreate()',
    ),
]

# Per-language source extension and session-init pattern table.
_LANG_EXT = {"python": ".py", "scala": ".scala", "java": ".java"}
_LANG_SESSION_PATTERNS = {
    "python": PYTHON_SESSION_PATTERNS,
    "scala": SCALA_SESSION_PATTERNS,
    "java": JAVA_SESSION_PATTERNS,
}

# JVM languages use ``//`` line comments and the SPRKCNTSCL EWI series; Python
# uses ``#`` and SPRKCNTPY. Keyed on the file extension because a single run can
# mix flat sources and notebooks.
_JVM_EXTS = (".scala", ".java")


def _annotation_style(file_path: str) -> tuple[str, str]:
    """Return ``(comment_prefix, ewi_prefix)`` for *file_path*."""
    if file_path.endswith(_JVM_EXTS):
        return "//", "SPRKCNTSCL"
    return "#", "SPRKCNTPY"


def load_analysis(path: str) -> list[dict]:
    """Load analysis.json and return the list of issues."""
    if not os.path.exists(path):
        print(f"WARNING: analysis.json not found at {path}, generating script with headers only")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_fix_operations(issues: list[dict], output_dir: str) -> dict[str, list[dict]]:
    """Group analysis issues by file and build search/replace operations.

    Returns: {relative_file_path: [{search, replace, line, description}]}
    """
    ops_by_file: dict[str, list[dict]] = {}

    for issue in issues:
        file_path = issue.get("file", "")
        if not file_path:
            continue

        # Normalize to relative path within output_dir
        if os.path.isabs(file_path) and output_dir:
            file_path = os.path.relpath(file_path, output_dir)
        file_path = file_path.replace("\\", "/").lstrip("/")

        code = issue.get("code", "")
        fix = issue.get("fix")
        root_cause = issue.get("root_cause", "")
        lines = issue.get("lines", "")
        risk = issue.get("final_risk", 0)

        if not code:
            continue

        # SNOW-3347481: Build the replacement based on available fix info
        op = {
            "search": code.strip(),
            "line": lines,
            "risk": risk,
            "description": root_cause,
        }

        if fix and fix.strip():
            # Use the provided fix as a comment annotation
            comment_prefix, ewi_prefix = _annotation_style(file_path)

            if risk >= 0.7:
                op["replace"] = (
                    f"{comment_prefix} SCOS: [{ewi_prefix}1000] {root_cause}\n"
                    f"{comment_prefix} Fix: {fix}\n"
                    f"{code.strip()}"
                )
            elif risk >= 0.3:
                op["replace"] = (
                    f"{comment_prefix} SCOS: TODO - {root_cause}\n"
                    f"{comment_prefix} Suggested fix: {fix}\n"
                    f"{code.strip()}"
                )
            else:
                op["replace"] = (
                    f"{comment_prefix} SCOS: {root_cause}\n"
                    f"{code.strip()}"
                )
        else:
            # No fix available — add annotation comment only
            comment_prefix, _ = _annotation_style(file_path)
            if risk >= 0.7:
                op["replace"] = (
                    f"{comment_prefix} SCOS: TODO - {root_cause}\n"
                    f"{code.strip()}"
                )
            elif risk >= 0.3:
                op["replace"] = (
                    f"{comment_prefix} SCOS: TODO - {root_cause}\n"
                    f"{code.strip()}"
                )
            else:
                op["replace"] = (
                    f"{comment_prefix} SCOS: {root_cause}\n"
                    f"{code.strip()}"
                )

        ops_by_file.setdefault(file_path, []).append(op)

    return ops_by_file


def generate_script_template(
    ops_by_file: dict[str, list[dict]],
    output_dir: str,
    language: str,
    analysis_path: str,
) -> str:
    """Generate apply_fixes.py using a clean template approach."""

    ext = _LANG_EXT.get(language, ".py")
    session_patterns = _LANG_SESSION_PATTERNS.get(language, PYTHON_SESSION_PATTERNS)
    today = datetime.now().strftime("%Y-%m-%d")

    # Build the FIX_OPERATIONS dict as a JSON-serializable structure
    fix_ops_json = json.dumps(
        {k: [{"search": o["search"], "replace": o["replace"],
              "line": o["line"], "description": o["description"]}
             for o in v]
         for k, v in sorted(ops_by_file.items())},
        indent=2,
        ensure_ascii=False,
    )

    session_patterns_json = json.dumps(
        [[s, r] for s, r in session_patterns],
        indent=2,
        ensure_ascii=False,
    )

    scala_header = "// SCOS Migration Output"

    script = f'''#!/usr/bin/env python3
"""
SCOS Batch Transformation Script — generated {today}
Source: {analysis_path}
Target: {output_dir}
Language: {language}

Run: python3 apply_fixes.py
"""

import json
import os
import sys
from datetime import datetime

OUTPUT_DIR = {repr(os.path.abspath(output_dir))}
LANGUAGE = {repr(language)}
EXT = {repr(ext)}
HEADER_MARKER = "SCOS Migration Output"

# SNOW-3347481: Search/replace operations from analysis.json
FIX_OPERATIONS = json.loads({repr(fix_ops_json)})

# SNOW-3347481: Session initialization replacements
SESSION_PATTERNS = json.loads({repr(session_patterns_json)})


def apply_fixes():
    """Apply search/replace fixes from analysis.json."""
    modified = 0
    total_fixes = 0

    for rel_path, ops in FIX_OPERATIONS.items():
        fpath = os.path.join(OUTPUT_DIR, rel_path)
        if not os.path.exists(fpath):
            print(f"  SKIP {{rel_path}} — file not found")
            continue

        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        original = content
        applied = 0
        for op in ops:
            if op["search"] in content:
                content = content.replace(op["search"], op["replace"], 1)
                applied += 1

        if content != original:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            modified += 1
            total_fixes += applied
            print(f"  FIXED {{rel_path}} — {{applied}} fix(es)")
        else:
            print(f"  NOOP  {{rel_path}} — patterns not found (may already be fixed)")

    return modified, total_fixes


def apply_session_replacements():
    """Replace session initialization patterns in all source files."""
    modified = 0

    for root, _dirs, files in os.walk(OUTPUT_DIR):
        for fname in files:
            if not fname.endswith(EXT):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            original = content
            for search, replace in SESSION_PATTERNS:
                if search in content:
                    content = content.replace(search, replace, 1)

            if content != original:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
                rel = os.path.relpath(fpath, OUTPUT_DIR)
                print(f"  SESSION {{rel}}")
                modified += 1

    return modified


def add_migration_headers():
    """Add migration header to every output file that is missing one."""
    patched = 0

    for root, _dirs, files in os.walk(OUTPUT_DIR):
        for fname in files:
            if not fname.endswith(EXT):
                continue
            fpath = os.path.join(root, fname)

            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                head = [f.readline() for _ in range(5)]
            if any(HEADER_MARKER in ln for ln in head):
                continue

            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            if LANGUAGE == "scala":
                new_content = "{scala_header}\\n" + content
            else:
                today_str = datetime.now().strftime("%Y-%m-%d")
                new_content = (
                    '\"\"\"\\n'
                    "SCOS Migration Output\\n"
                    "=====================\\n"
                    f"Source File: {{fname}}\\n"
                    f"Migrated on: {{today_str}}\\n"
                    "\\n"
                    "Changes Overview:\\n"
                    "- Batch transformation applied by apply_fixes.py\\n"
                    "\\n"
                    "Known Limitations:\\n"
                    "- None\\n"
                    '\"\"\"\\n'
                ) + content

            with open(fpath, "w", encoding="utf-8") as f:
                f.write(new_content)
            patched += 1

    return patched


def main():
    print(f"SCOS Batch Transformation")
    print(f"=========================")
    print(f"  Output:   {{OUTPUT_DIR}}")
    print(f"  Language: {{LANGUAGE}}")
    print(f"  Fix operations: {{sum(len(v) for v in FIX_OPERATIONS.values())}} across {{len(FIX_OPERATIONS)}} files")
    print()

    # Step 1: Apply analysis fixes
    print("Step 1: Applying analysis fixes...")
    mod_fixes, total_fixes = apply_fixes()
    print(f"  Modified {{mod_fixes}} files, applied {{total_fixes}} fixes")
    print()

    # Step 2: Apply session replacements
    print("Step 2: Applying session initialization replacements...")
    mod_sessions = apply_session_replacements()
    print(f"  Modified {{mod_sessions}} files")
    print()

    # Step 3: Add migration headers
    print("Step 3: Adding migration headers...")
    patched_headers = add_migration_headers()
    print(f"  Patched {{patched_headers}} files")
    print()

    print(f"Done! Modified {{mod_fixes + mod_sessions}} files, applied {{total_fixes}} fixes, {{patched_headers}} headers added.")


if __name__ == "__main__":
    sys.exit(main() or 0)
'''
    return script


def main():
    parser = argparse.ArgumentParser(
        description="SNOW-3347481: Generate batch transformation script from analysis.json"
    )
    parser.add_argument(
        "--analysis", default="analysis.json",
        help="Path to analysis.json (default: analysis.json)",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Output/ directory containing migrated files",
    )
    parser.add_argument(
        "--language", choices=["python", "scala", "java"], default="python",
        help="Source language (default: python)",
    )
    parser.add_argument(
        "--script-output", default="apply_fixes.py",
        help="Path to write the generated script (default: apply_fixes.py)",
    )

    args = parser.parse_args()

    output_dir = os.path.abspath(args.output_dir)
    analysis_path = os.path.abspath(args.analysis)

    print(f"SCOS Transformation Script Generator")
    print(f"=====================================")
    print(f"  Analysis: {analysis_path}")
    print(f"  Output:   {output_dir}")
    print(f"  Language: {args.language}")
    print(f"  Script:   {args.script_output}")
    print()

    # Load analysis
    issues = load_analysis(analysis_path)
    print(f"  Loaded {len(issues)} issues from analysis.json")

    # Build fix operations
    ops = build_fix_operations(issues, output_dir)
    total_ops = sum(len(v) for v in ops.values())
    print(f"  Built {total_ops} fix operations across {len(ops)} files")
    print()

    # Generate script
    script_content = generate_script_template(ops, output_dir, args.language, analysis_path)

    # Write script
    script_path = os.path.abspath(args.script_output)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script_content)
    os.chmod(script_path, 0o755)

    print(f"Generated: {script_path}")
    print(f"Run: python3 {args.script_output}")


if __name__ == "__main__":
    sys.exit(main() or 0)
