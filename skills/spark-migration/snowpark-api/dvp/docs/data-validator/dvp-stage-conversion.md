# stage-conversion

> Convert cloud storage paths to Snowflake stage syntax.

## Overview

| Field | Value |
|-------|-------|
| **Category** | data-validator |
| **Status** | Planned |
| **Output** | Modified Python files (.py) |
| **Depends on** | None (operates on source code directly) |

## Responsibility

Parse Python code and detect uses of files located at S3 buckets, Azure Blobs, GCS, HDFS, or other external storage. Replace them with stage syntax as the equivalence for Snowflake migration.

## Inputs

| Input | Required | Description |
|-------|----------|-------------|
| SMA migrated source code | Yes | Python files with cloud storage references |

## Outputs

### Modified Source Files

Python files with cloud storage paths replaced by Snowflake stage references.

### Conversion Report (`reports/stage_conversion.json`)

```json
{
  "conversions": [
    {
      "file": "pipeline_x/main.py",
      "line": 42,
      "original": "s3://my-bucket/data/input/customers.parquet",
      "converted": "@my_stage/data/input/customers.parquet",
      "storage_type": "s3",
      "stage_name": "my_stage"
    }
  ],
  "summary": {
    "files_modified": 5,
    "conversions_applied": 12,
    "by_storage_type": {
      "s3": 8,
      "azure_blob": 3,
      "hdfs": 1
    }
  }
}
```

## Detection Patterns

### S3
```python
# Before
"s3://bucket-name/path/to/file.parquet"
"s3a://bucket-name/path/to/file.csv"

# After
"@s3_stage/path/to/file.parquet"
"@s3_stage/path/to/file.csv"
```

### Azure Blob Storage
```python
# Before
"wasbs://container@account.blob.core.windows.net/path/file.parquet"
"abfss://container@account.dfs.core.windows.net/path/file.parquet"

# After
"@azure_stage/path/file.parquet"
```

### HDFS
```python
# Before
"hdfs://namenode:8020/data/path/file.parquet"
"/data/path/file.parquet"  # implicit HDFS

# After
"@hdfs_stage/data/path/file.parquet"
```

### GCS
```python
# Before
"gs://bucket-name/path/to/file.parquet"

# After
"@gcs_stage/path/to/file.parquet"
```

## Stage Mapping Configuration

The skill should support a configurable mapping:

```json
{
  "stage_mappings": {
    "s3://my-bucket": "@my_s3_stage",
    "wasbs://my-container@account.blob.core.windows.net": "@my_azure_stage",
    "hdfs://namenode:8020": "@my_hdfs_stage"
  }
}
```

## Workflow

1. **Scan** all `.py` files for cloud storage path patterns
2. **Identify** the storage type (S3, Azure, HDFS, GCS)
3. **Extract** bucket/container and path components
4. **Map** to corresponding Snowflake stage syntax
5. **Replace** paths in the source code
6. **Generate** conversion report
7. **Output** modified files and report

## Design Considerations

- Should handle paths in string literals, f-strings, and variable assignments
- Must preserve the file path structure after the bucket/container
- Should detect and handle parameterized paths (variables containing paths)
- Should offer dry-run mode (report only, no modifications)
- Might need to create the stage DDL statements as well
- Should handle both read and write paths
