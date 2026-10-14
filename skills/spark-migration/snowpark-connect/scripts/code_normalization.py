# flake8: noqa: T201

"""
Code normalization utilities for PySpark RAG services.

SCOS Migrator - Code Normalization Module

Normalizes PySpark code and SQL to improve embedding similarity matching
by removing test-specific artifacts and standardizing patterns.
"""

import csv
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def remove_comments(code: str) -> str:
    """
    Remove Python comments from code, preserving # inside strings.

    Handles:
    - Full-line comments: "    # this is a comment"
    - Inline comments: "code()  # this is a comment" -> "code()"

    Args:
        code: Source code

    Returns:
        Code with comments removed
    """
    lines = code.split("\n")
    result_lines = []

    for line in lines:
        # Track if we're inside a string
        new_line = []
        in_string = None  # None, '"', or "'"
        escape_next = False
        i = 0

        while i < len(line):
            char = line[i]

            if escape_next:
                new_line.append(char)
                escape_next = False
                i += 1
                continue

            if char == "\\":
                new_line.append(char)
                escape_next = True
                i += 1
                continue

            # Handle string start/end
            if in_string:
                new_line.append(char)
                if char == in_string:
                    in_string = None
                i += 1
                continue

            if char in ('"', "'"):
                # Check for triple quotes
                if line[i : i + 3] in ('"""', "'''"):
                    # For simplicity, treat triple-quoted strings on same line
                    quote = line[i : i + 3]
                    new_line.append(quote)
                    i += 3
                    # Find closing triple quote
                    end = line.find(quote, i)
                    if end != -1:
                        new_line.append(line[i:end])
                        new_line.append(quote)
                        i = end + 3
                    else:
                        # Triple quote continues to next line, keep rest of line
                        new_line.append(line[i:])
                        i = len(line)
                    continue
                else:
                    in_string = char
                    new_line.append(char)
                    i += 1
                    continue

            # Check for comment start (not in string)
            if char == "#":
                # Rest of line is a comment, stop here
                break

            new_line.append(char)
            i += 1

        # Join and strip trailing whitespace
        processed_line = "".join(new_line).rstrip()

        # Only keep non-empty lines (skip comment-only lines)
        if processed_line:
            result_lines.append(processed_line)

    return "\n".join(result_lines)


def normalize_whitespace(code: str) -> str:
    """
    Normalize whitespace in code.

    - Replace multiple newlines with single newline
    - Convert tabs to spaces
    - Strip trailing whitespace from lines
    - Strip leading/trailing whitespace from entire block

    Args:
        code: Source code

    Returns:
        Code with normalized whitespace
    """
    if not code:
        return code

    # Replace multiple newlines with single newline
    normalized = re.sub(r"\n\s*\n+", "\n", code)

    # Normalize indentation (convert tabs to spaces, strip trailing)
    lines = normalized.split("\n")
    normalized_lines = []
    for line in lines:
        stripped = line.rstrip()
        if stripped:
            stripped = stripped.replace("\t", "    ")
            normalized_lines.append(stripped)
    normalized = "\n".join(normalized_lines)

    return normalized.strip()


def normalize_code_lightweight(code: str) -> str:
    """
    Lightweight normalization for customer PySpark code.

    Applies only:
    - Remove code comments (# ...)
    - Normalize whitespace

    This is used for normalizing customer code before RAG queries,
    matching the normalization applied to RAG training data.

    Args:
        code: Raw PySpark code string

    Returns:
        Normalized code string
    """
    if not code:
        return code

    # Remove comments
    normalized = remove_comments(code)

    # Normalize whitespace
    normalized = normalize_whitespace(normalized)

    return normalized


def normalize_code(code: str) -> str:
    """
    Light normalization of PySpark code for RAG embedding.

    Transformations:
    1. Fix CSV escaping artifacts (double quotes)
    2. Normalize spark session references
    3. Remove test assertions and test-specific code
    4. Normalize whitespace
    5. Normalize file paths in string literals

    Args:
        code: Raw PySpark code string

    Returns:
        Normalized code string
    """
    if not code:
        return code

    # Note: CSV escaping (doubled quotes) is handled automatically by csv module
    normalized = code

    # Step 2: Normalize spark session references
    # self.spark -> spark
    normalized = re.sub(r"\bself\.spark\b", "spark", normalized)
    # self.connect -> spark
    normalized = re.sub(r"\bself\.connect\b", "spark", normalized)
    # session -> spark (when it looks like SparkSession usage)
    # Only replace when followed by typical SparkSession methods
    normalized = re.sub(
        r"\bsession\.(sql|read|createDataFrame|table|catalog|conf|udf|udtf|range)\b",
        r"spark.\1",
        normalized,
    )

    # Step 3: Transform test assertions to preserve the tested code
    # self.assert_eq(left, right) -> _assert(left)
    # This preserves the actual PySpark operation being tested
    normalized = _transform_assertion(normalized, r"self\.assert_eq", num_args=2)
    normalized = _transform_assertion(normalized, r"self\.assertEqual", num_args=2)
    normalized = _transform_assertion(normalized, r"self\.assertIn", num_args=2)
    # For single-arg assertions, keep the argument
    normalized = _transform_assertion(normalized, r"self\.assertTrue", num_args=1)
    normalized = _transform_assertion(normalized, r"self\.assertFalse", num_args=1)
    normalized = _transform_assertion(normalized, r"self\.assertIsNone", num_args=1)
    # assertRaises is different - used in "with self.assertRaises(...):" context
    # Remove the entire "with ... :" line, keeping the indented block content
    normalized = _remove_with_assertraises(normalized)

    # Remove lines with test variable assignments like: expected = [...]
    # or sdf = self.spark... (the spark DataFrames used for comparison)
    normalized = re.sub(r"^\s*expected\s*=\s*.*$", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"^\s*sdf\s*=\s*.*$", "", normalized, flags=re.MULTILINE)
    normalized = re.sub(r"^\s*cdf\s*=\s*.*$", "", normalized, flags=re.MULTILINE)

    # Step 4: Normalize file paths in string literals
    # Replace file paths with generic placeholder
    # Matches patterns like "path/to/file.json" or 'path/to/file.csv'
    normalized = re.sub(
        r'(["\'])([^"\']*[/\\][^"\']*\.(json|csv|parquet|txt|avro|orc|xml))(["\'])',
        r"\1_FILE_\3\4",
        normalized,
        flags=re.IGNORECASE,
    )

    # Step 5: Remove comments (# ...)
    normalized = remove_comments(normalized)

    # Step 6: Normalize whitespace
    normalized = normalize_whitespace(normalized)

    return normalized


def normalize_sql(sql: str) -> str:
    """
    Light normalization of Spark SQL for RAG embedding.

    Transformations:
    1. Fix CSV escaping artifacts
    2. Normalize whitespace
    3. Normalize case for SQL keywords (optional - keep original for now)

    Args:
        sql: Raw Spark SQL string

    Returns:
        Normalized SQL string
    """
    if not sql:
        return sql

    # Note: CSV escaping (doubled quotes) is handled automatically by csv module
    normalized = sql

    # Step 2: Normalize whitespace
    # Replace multiple spaces with single space
    normalized = re.sub(r"[ \t]+", " ", normalized)
    # Replace multiple newlines with single newline
    normalized = re.sub(r"\n\s*\n+", "\n", normalized)
    # Strip leading/trailing whitespace
    normalized = normalized.strip()

    return normalized


def _extract_first_argument(code: str, start_pos: int) -> tuple[str, int]:
    """
    Extract the first argument from a function call starting at start_pos.

    Args:
        code: Source code
        start_pos: Position right after the opening parenthesis

    Returns:
        Tuple of (first_argument, end_position_after_closing_paren)
    """
    # Track nested parentheses, brackets, braces, and strings
    paren_count = 1
    bracket_count = 0
    brace_count = 0
    in_string = None  # None, '"', or "'"
    escape_next = False

    arg_start = start_pos
    j = start_pos

    while j < len(code) and paren_count > 0:
        char = code[j]

        if escape_next:
            escape_next = False
            j += 1
            continue

        if char == "\\":
            escape_next = True
            j += 1
            continue

        # Handle string literals
        if in_string:
            if char == in_string:
                in_string = None
            j += 1
            continue

        if char in ('"', "'"):
            in_string = char
            j += 1
            continue

        # Track nesting
        if char == "(":
            paren_count += 1
        elif char == ")":
            paren_count -= 1
        elif char == "[":
            bracket_count += 1
        elif char == "]":
            bracket_count -= 1
        elif char == "{":
            brace_count += 1
        elif char == "}":
            brace_count -= 1
        elif (
            char == "," and paren_count == 1 and bracket_count == 0 and brace_count == 0
        ):
            # Found the comma separating first arg from rest
            first_arg = code[arg_start:j].strip()
            # Now find the closing paren
            while j < len(code) and paren_count > 0:
                if code[j] == "(":
                    paren_count += 1
                elif code[j] == ")":
                    paren_count -= 1
                j += 1
            return first_arg, j

        j += 1

    # No comma found - single argument or reached end
    first_arg = code[arg_start : j - 1].strip()  # -1 to exclude closing paren
    return first_arg, j


def _transform_assertion(code: str, method_pattern: str, num_args: int = 2) -> str:
    """
    Transform assertion calls to preserve only the first argument.

    self.assert_eq(left, right) -> left

    Args:
        code: Source code
        method_pattern: Regex pattern for the method name
        num_args: Expected number of arguments (2 for comparison, 1 for single-value)

    Returns:
        Code with assertions replaced by their first argument
    """
    pattern = re.compile(method_pattern + r"\s*\(")

    result = []
    i = 0
    while i < len(code):
        match = pattern.search(code, i)
        if not match:
            result.append(code[i:])
            break

        # Add everything before the match
        result.append(code[i : match.start()])

        # Extract the first argument
        first_arg, end_pos = _extract_first_argument(code, match.end())

        if first_arg:
            # Replace assertion with just the first argument
            result.append(first_arg)
        # else: empty assertion, just remove it

        i = end_pos

    return "".join(result)


def _remove_with_assertraises(code: str) -> str:
    """
    Remove 'with self.assertRaises(...):' context manager lines.

    The content inside the with block is preserved but dedented.
    Also handles standalone self.assertRaises() calls.

    Args:
        code: Source code

    Returns:
        Code with assertRaises context managers removed
    """
    lines = code.split("\n")
    result_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        # Check for "with self.assertRaises(...):"
        if stripped.startswith("with ") and "self.assertRaises" in stripped:
            # Find the indentation of the with statement
            with_indent = len(line) - len(stripped)

            # Skip this line (the with statement)
            i += 1

            # Process the indented block - dedent by one level (typically 4 spaces)
            while i < len(lines):
                block_line = lines[i]
                block_stripped = block_line.lstrip()

                # Empty lines are kept
                if not block_stripped:
                    result_lines.append("")
                    i += 1
                    continue

                block_indent = len(block_line) - len(block_stripped)

                # If indentation is greater than with statement, it's part of the block
                if block_indent > with_indent:
                    # Dedent by removing one level of indentation
                    dedent_amount = min(4, block_indent - with_indent)
                    dedented = block_line[dedent_amount:]
                    result_lines.append(dedented)
                    i += 1
                else:
                    # Block ended, don't consume this line
                    break
        else:
            result_lines.append(line)
            i += 1

    return "\n".join(result_lines)


def _remove_method_call(code: str, method_pattern: str) -> str:
    r"""
    Remove method calls matching the pattern, handling nested parentheses.

    Args:
        code: Source code
        method_pattern: Regex pattern for the method name (e.g., r"self\.assertRaises")

    Returns:
        Code with method calls removed
    """
    pattern = re.compile(method_pattern + r"\s*\(")

    result = []
    i = 0
    while i < len(code):
        match = pattern.search(code, i)
        if not match:
            result.append(code[i:])
            break

        # Add everything before the match
        result.append(code[i : match.start()])

        # Find the matching closing parenthesis
        paren_count = 1
        j = match.end()
        while j < len(code) and paren_count > 0:
            if code[j] == "(":
                paren_count += 1
            elif code[j] == ")":
                paren_count -= 1
            j += 1

        # Skip past the entire method call
        i = j

        # Also skip any trailing newline
        while i < len(code) and code[i] in " \t":
            i += 1
        if i < len(code) and code[i] == "\n":
            i += 1

    return "".join(result)


def _should_include_row(code: str) -> bool:
    """
    Check if a row should be included in the normalized output.

    Logic:
    - If code has NO assertions at all → include (raw code, not test code)
    - If code has assertRaises but NO assert_eq/assertEqual → exclude
    - If code has assert_eq/assertEqual → include

    Args:
        code: Original (non-normalized) code

    Returns:
        True if row should be included
    """
    code_lower = code.lower()

    has_assert_eq = "assert_eq" in code_lower or "assertequal" in code_lower
    has_assert_raises = "assertraises" in code_lower

    # If it has meaningful assertions, include it
    if has_assert_eq:
        return True

    # If it ONLY has assertRaises (no assert_eq), exclude it
    if has_assert_raises:
        return False

    # No assertions at all - this is raw code, include it
    return True


def normalize_csv_code(
    input_path: Path,
    output_path: Path,
    code_column: str = "code",
) -> tuple[int, int]:
    """
    Normalize code column in a CSV file and write to a new file.

    Rows that only contain assertRaises (no assert_eq/assertEqual) are skipped.

    Args:
        input_path: Path to input CSV
        output_path: Path to output CSV
        code_column: Name of the column containing code to normalize

    Returns:
        Tuple of (rows_written, rows_skipped)
    """
    rows_written = 0
    rows_skipped = 0

    with open(input_path, encoding="utf-8", newline="") as infile:
        reader = csv.DictReader(infile)
        fieldnames = reader.fieldnames

        if not fieldnames:
            raise ValueError(f"No headers found in {input_path}")

        if code_column not in fieldnames:
            raise ValueError(
                f"Column '{code_column}' not found in {input_path}. "
                f"Available columns: {fieldnames}"
            )

        with open(output_path, "w", encoding="utf-8", newline="") as outfile:
            writer = csv.DictWriter(
                outfile,
                fieldnames=fieldnames,
                extrasaction="ignore",
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()

            for row in reader:
                # Filter out None keys that can appear from malformed CSV
                row = {k: v for k, v in row.items() if k is not None}
                original_code = row.get(code_column, "")

                # Skip rows that only have assertRaises (no assert_eq/assertEqual)
                if not _should_include_row(original_code):
                    rows_skipped += 1
                    continue

                # Normalize the code column
                row[code_column] = normalize_code(original_code)
                writer.writerow(row)
                rows_written += 1

    return rows_written, rows_skipped


def normalize_csv_sql(
    input_path: Path,
    output_path: Path,
    sql_column: str = "Spark SQL",
) -> int:
    """
    Normalize SQL column in a CSV file and write to a new file.

    Args:
        input_path: Path to input CSV
        output_path: Path to output CSV
        sql_column: Name of the column containing SQL to normalize

    Returns:
        Number of rows processed
    """
    rows_processed = 0

    with open(input_path, encoding="utf-8", newline="") as infile:
        reader = csv.DictReader(infile)
        fieldnames = reader.fieldnames

        if not fieldnames:
            raise ValueError(f"No headers found in {input_path}")

        if sql_column not in fieldnames:
            raise ValueError(
                f"Column '{sql_column}' not found in {input_path}. "
                f"Available columns: {fieldnames}"
            )

        with open(output_path, "w", encoding="utf-8", newline="") as outfile:
            writer = csv.DictWriter(
                outfile,
                fieldnames=fieldnames,
                extrasaction="ignore",
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()

            for row in reader:
                # Filter out None keys that can appear from malformed CSV
                row = {k: v for k, v in row.items() if k is not None}
                # Normalize the SQL column
                original_sql = row.get(sql_column, "")
                row[sql_column] = normalize_sql(original_sql)
                writer.writerow(row)
                rows_processed += 1

    return rows_processed


def main():
    """Pre-process all CSV files to create normalized versions."""
    print("=" * 60)
    print("PySpark Code Normalization")
    print("=" * 60)

    # Process oss_code_rca.csv
    code_input = DATA_DIR / "oss_code_rca.csv"
    code_output = DATA_DIR / "oss_code_rca_normalized.csv"
    if code_input.exists():
        print(f"\nProcessing {code_input.name}...")
        written, skipped = normalize_csv_code(code_input, code_output)
        print(f"  ✓ Normalized {written} rows -> {code_output.name}")
        if skipped:
            print(f"    (skipped {skipped} rows with only assertRaises)")
    else:
        print(f"  ✗ {code_input} not found")

    # Process expectation_tests_xfail_rca.csv
    xfail_input = DATA_DIR / "expectation_tests_xfail_rca.csv"
    xfail_output = DATA_DIR / "expectation_tests_xfail_rca_normalized.csv"
    if xfail_input.exists():
        print(f"\nProcessing {xfail_input.name}...")
        written, skipped = normalize_csv_code(xfail_input, xfail_output)
        print(f"  ✓ Normalized {written} rows -> {xfail_output.name}")
        if skipped:
            print(f"    (skipped {skipped} rows with only assertRaises)")
    else:
        print(f"  ✗ {xfail_input} not found")

    # Process oss_sql_rca.csv
    sql_input = DATA_DIR / "oss_sql_rca.csv"
    sql_output = DATA_DIR / "oss_sql_rca_normalized.csv"
    if sql_input.exists():
        print(f"\nProcessing {sql_input.name}...")
        count = normalize_csv_sql(sql_input, sql_output)
        print(f"  ✓ Normalized {count} rows -> {sql_output.name}")
    else:
        print(f"  ✗ {sql_input} not found")

    # ==========================================
    # Process unified data files
    # ==========================================
    print("\n" + "-" * 60)
    print("Processing SCOS RAG data files...")
    print("-" * 60)

    # Process df_test_rca.csv (DataFrame code)
    df_input = DATA_DIR / "df_test_rca.csv"
    df_output = DATA_DIR / "df_test_rca_normalized.csv"
    if df_input.exists():
        print(f"\nProcessing {df_input.name}...")
        written, skipped = normalize_csv_code(df_input, df_output)
        print(f"  ✓ Normalized {written} rows -> {df_output.name}")
        if skipped:
            print(f"    (skipped {skipped} rows with only assertRaises)")
    else:
        print(f"  ✗ {df_input} not found")

    # Process sql_test_rca.csv (SQL code)
    sql_input = DATA_DIR / "sql_test_rca.csv"
    sql_output = DATA_DIR / "sql_test_rca_normalized.csv"
    if sql_input.exists():
        print(f"\nProcessing {sql_input.name}...")
        count = normalize_csv_sql(sql_input, sql_output, sql_column="code")
        print(f"  ✓ Normalized {count} rows -> {sql_output.name}")
    else:
        print(f"  ✗ {sql_input} not found")

    # Process expectation_tests_xfail_rca.csv (DataFrame code)
    xfail_input = DATA_DIR / "expectation_tests_xfail_rca.csv"
    xfail_output = DATA_DIR / "expectation_tests_xfail_rca_normalized.csv"
    if xfail_input.exists():
        print(f"\nProcessing {xfail_input.name}...")
        written, skipped = normalize_csv_code(xfail_input, xfail_output)
        print(f"  ✓ Normalized {written} rows -> {xfail_output.name}")
        if skipped:
            print(f"    (skipped {skipped} rows with only assertRaises)")
    else:
        print(f"  ✗ {xfail_input} not found")

    # Process jira_rca.csv (mixed code)
    jira_input = DATA_DIR / "jira_rca.csv"
    jira_output = DATA_DIR / "jira_rca_normalized.csv"
    if jira_input.exists():
        print(f"\nProcessing {jira_input.name}...")
        written, skipped = normalize_csv_code(jira_input, jira_output)
        print(f"  ✓ Normalized {written} rows -> {jira_output.name}")
        if skipped:
            print(f"    (skipped {skipped} rows with only assertRaises)")
    else:
        print(f"  ✗ {jira_input} not found")

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)


if __name__ == "__main__":
    main()
