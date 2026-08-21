# dvp-test-validation-generator

> Create Python test validation code -- the Assert/Then phase.

## Overview

| Field | Value |
|-------|-------|
| **Category** | data-validator |
| **Status** | Planned |
| **Output** | Python test assertion files (.py) |
| **Depends on** | dvp-io-schema-identifier |

## Responsibility

Creates the Python files to validate the outputs for each pipeline. Tests that output has rows, matches expected schema, and data matches expectations. This is the **Assert/Then** phase of the test pattern.

## Inputs

| Input | Required | Description |
|-------|----------|-------------|
| I/O Inventory | Yes | From dvp-io-schema-identifier (output targets) |
| Schema Definitions | Yes | From dvp-io-schema-identifier (columns in data_io_schema.json) |
| Expected Output Data | Optional | Reference data for exact match validation |

## Outputs

### Test Validation Files (`tests/validation/`)

Generated Python files that validate:
- Output tables/files exist
- Output has the expected number of rows (non-empty)
- Output schema matches expectations (column names, types)
- Output data matches expected values (when reference data is available)

### Example Output

```python
# tests/validation/validate_pipeline_x.py

def validate_pipeline_x_outputs(session):
    """Validate outputs of pipeline_x."""
    
    # Check output table exists and has rows
    output_df = session.table("schema.output_table_1")
    row_count = output_df.count()
    assert row_count > 0, "Output table is empty"
    
    # Validate schema
    expected_columns = ["id", "name", "total_amount", "processed_at"]
    actual_columns = [f.name for f in output_df.schema.fields]
    assert set(expected_columns).issubset(set(actual_columns)), \
        f"Missing columns: {set(expected_columns) - set(actual_columns)}"
    
    # Validate data types
    schema_map = {f.name: f.datatype for f in output_df.schema.fields}
    assert "INTEGER" in str(schema_map["id"])
    assert "VARCHAR" in str(schema_map["name"])
    
    # Validate data content (when expected data available)
    # expected_df = session.read.csv("@stage/expected/output_table_1.csv")
    # assert_dataframes_equal(output_df, expected_df)
    
    return {
        "table": "schema.output_table_1",
        "row_count": row_count,
        "schema_valid": True,
        "data_match": None  # or True/False when expected data available
    }
```

## Validation Levels

| Level | Description | When to Use |
|-------|-------------|-------------|
| **Existence** | Table/file exists | Always |
| **Non-empty** | Has at least 1 row | Always |
| **Schema match** | Columns and types match | Always |
| **Row count match** | Exact row count matches expected | When reference available |
| **Data match** | Row-level data comparison | When reference data available |

## Workflow

1. **Read** I/O Inventory to identify output tables/files
2. **Read** Schema Definitions for expected output schemas
3. **Generate** existence checks for each output
4. **Generate** schema validation code
5. **Generate** data comparison code (when expected data exists)
6. **Generate** result summary reporting
7. **Output** Python validation files per pipeline

## Design Considerations

- Should support multiple validation levels (existence, schema, data)
- Should generate clear assertion messages for debugging
- Should handle nullable columns and type coercion
- Should support approximate matching for floating-point values
- Results should feed into dvp-testing-status-manager
