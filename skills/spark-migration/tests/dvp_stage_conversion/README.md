# SMA Replace Embedded File Paths - Test Suite

This directory contains comprehensive tests for the `skills/spark-migration/snowpark-api/stage-conversion` skill that detects and replaces embedded file paths in Snowpark and Jupyter notebooks.

## Test Files

### test_folder_paths.py
Tests the detection and replacement of **folder paths** (directories containing parquet files).

**What it tests:**
- ✅ Basic folder path detection across multiple cloud protocols (S3, HDFS, GCS, Azure)
- ✅ Folder path transformation to Snowflake stage format
- ✅ Distinction between folder paths (ending with `/`) and file paths
- ✅ Partitioned folder structures (Hive-style partitions like `year=2024/month=01/`)
- ✅ Special table formats (Iceberg metadata folders, Delta log folders)
- ✅ Local and relative folder paths

**Example patterns tested:**
```python
# Folder paths
df = spark.read.parquet("s3://bucket/data/")
df = spark.read.parquet("hdfs://cluster/partitioned/year=2024/")

# File paths
df = spark.read.parquet("s3://bucket/data/file.parquet")

# Iceberg/Delta structures
df = spark.read.format("iceberg").load("s3://warehouse/table/metadata/")
```

**Run:** `python3 tests/dvp/sma_replace_embedded_file_paths/test_folder_paths.py`

### test_multiline_operations.py
Tests detection of paths in multiline Spark/Snowpark operations.

**What it tests:**
- ✅ Chained method calls across multiple lines
- ✅ Line continuation with backslashes
- ✅ Complex format/load patterns
- ✅ Multiline write operations

**Example patterns tested:**
```python
df = spark.read \
    .format("csv") \
    .load("s3://bucket/data.csv")
```

### test_polaris.py
Tests detection of Polaris (Snowflake's open-source catalog) specific paths.

**What it tests:**
- ✅ Polaris configuration paths
- ✅ Catalog metadata locations
- ✅ Multi-storage backend configurations
- ✅ Dynamic credential paths

### test_e2e.py
End-to-end integration tests that validate the complete detection and replacement workflow.

### test_unit.py
Unit tests for individual components and edge cases.

## Running Tests

### Run all tests
```bash
python3 tests/dvp/sma_replace_embedded_file_paths/run_all_tests.py
```

### Run specific test
```bash
python3 tests/dvp/sma_replace_embedded_file_paths/test_folder_paths.py
python3 tests/dvp/sma_replace_embedded_file_paths/test_multiline_operations.py
python3 tests/dvp/sma_replace_embedded_file_paths/test_polaris.py
```

## Test Structure

Each test file:
1. Creates temporary test files with sample code
2. Uses the `FileScanner` to detect embedded paths
3. Validates detection accuracy
4. Tests path transformation to Snowflake stage format
5. Cleans up temporary files

## Coverage

The test suite validates detection of:

### Cloud Storage Protocols
- ✅ S3 (`s3://`, `s3a://`)
- ✅ HDFS (`hdfs://`)
- ✅ Google Cloud Storage (`gs://`, `gcs://`)
- ✅ Azure (`abfss://`, `wasbs://`)
- ✅ Local paths (`/path/to/file`)
- ✅ Relative paths (`./path/to/file`)

### Operation Types
- ✅ Read operations (`.read()`, `.load()`)
- ✅ Write operations (`.write()`, `.save()`)
- ✅ Variable assignments
- ✅ F-strings with dynamic paths
- ✅ Function call arguments
- ✅ Multiline/chained operations

### File & Folder Types
- ✅ Single files (`.parquet`, `.csv`, `.json`, etc.)
- ✅ Folder paths (ending with `/`)
- ✅ Partitioned folders (Hive-style: `year=2024/month=01/`)
- ✅ Table formats (Iceberg, Delta Lake)
- ✅ Configuration files (Polaris credentials, configs)

### Edge Cases
- ✅ Commented code
- ✅ String variables
- ✅ Complex nested paths
- ✅ Multiple paths in one line
- ✅ Wildcard patterns

## Expected Results

All tests should pass with 100% detection accuracy for:
- Folder paths pointing to directories with parquet files
- File paths with explicit extensions
- Partitioned data structures
- Cross-cloud storage patterns

## Fixtures

The `fixtures/` directory contains sample files used across multiple tests:
- `sample_code.py` - General Python code samples
- `sample_notebook.ipynb` - Jupyter notebook samples

## Notes

- Tests use temporary files to avoid polluting the workspace
- All tests are self-contained and can run independently
- Tests validate both detection (finding paths) and transformation (converting to stage format)
- The skill uses AST (Abstract Syntax Tree) parsing for accurate Python code analysis
