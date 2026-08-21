# dvp-io-identifier

> Identify input and output data (files and tables) for each pipeline.

## Overview

| Field | Value |
|-------|-------|
| **Category** | data-validator |
| **Status** | Planned |
| **Output** | I/O Inventory (JSON) |
| **Depends on** | dvp-entrypoint-identifier |

## Responsibility

Scan source code and identify input and output files/tables for each pipeline. Can use the SMA IO Inventory as a reference. Maps which data each pipeline reads from and writes to.

## Inputs

| Input | Required | Description |
|-------|----------|-------------|
| SMA migrated source code | Yes | The converted Python/Snowpark source files |
| Entrypoints Inventory | Yes | Output from dvp-entrypoint-identifier (`entrypoints.json`) |
| SMA IO Inventory | Optional | Pre-existing I/O mapping from SMA |

## Outputs

### Tables Inventory (`tables.json`) and Files Inventory (`files.json`)

```json
{
  "pipelines": [
    {
      "pipeline_name": "pipeline_name",
      "inputs": [
        {
          "type": "table|file",
          "name": "database.schema.table_name",
          "format": "parquet|csv|delta|snowflake_table",
          "path_or_location": "s3://bucket/path or schema.table",
          "source_reference": "file.py:line_number"
        }
      ],
      "outputs": [
        {
          "type": "table|file",
          "name": "database.schema.output_table",
          "format": "snowflake_table",
          "path_or_location": "schema.output_table",
          "write_mode": "overwrite|append|merge",
          "source_reference": "file.py:line_number"
        }
      ]
    }
  ]
}
```

## Detection Patterns

The skill should detect common I/O patterns in migrated code:

### Input patterns
- `spark.read.parquet(...)`, `spark.read.csv(...)`
- `spark.table(...)`, `spark.sql("SELECT ... FROM ...")`
- `session.table(...)` (Snowpark)
- File reads from S3, Azure Blob, HDFS paths

### Output patterns
- `.write.parquet(...)`, `.write.csv(...)`
- `.write.saveAsTable(...)`
- `.write.mode("overwrite").save(...)`
- `session.write_pandas(...)` (Snowpark)
- SQL INSERT/MERGE/CREATE TABLE AS statements

## Workflow

1. **Load** the Entrypoints Inventory from entrypoint-identifier
2. **For each pipeline**, trace the code flow from entry point
3. **Detect** all read/input operations
4. **Detect** all write/output operations
5. **Cross-reference** with SMA IO Inventory if available
6. **Output** the I/O inventory JSON

## Design Considerations

- Must handle dynamic table/file names (parameterized paths)
- Should resolve variable references to determine actual paths
- Should distinguish between intermediate data and final outputs
- May need to handle multiple output destinations per pipeline
