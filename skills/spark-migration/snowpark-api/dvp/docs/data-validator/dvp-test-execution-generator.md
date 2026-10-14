# dvp-test-execution-generator

> Create Python test execution code -- the Act/When phase.

## Overview

| Field | Value |
|-------|-------|
| **Category** | data-validator |
| **Status** | Planned |
| **Output** | Python test execution files (.py) |
| **Depends on** | dvp-entry-point-identifier, dvp-io-identifier |

## Responsibility

Generates the code that invokes each pipeline under test. This is the **Act/When** phase of the test pattern -- the actual execution of the migrated pipeline with the test data.

## Inputs

| Input | Required | Description |
|-------|----------|-------------|
| Entrypoints Inventory | Yes | From dvp-entry-point-identifier (`entrypoints.json`) |
| I/O Inventory | Yes | From dvp-io-identifier (parameter context) |

## Outputs

### Test Execution Files (`tests/execution/`)

Generated Python files that:
- Import the pipeline under test
- Configure required parameters
- Execute the pipeline entry point
- Capture execution results and any errors

### Example Output

```python
# tests/execution/exec_pipeline_x.py

from pipeline_x.main import run_pipeline
from tests.setup.setup_pipeline_x import create_session

def execute_pipeline_x(session):
    """Execute pipeline_x with test data."""
    try:
        result = run_pipeline(
            session=session,
            input_table="schema.input_table_1",
            output_table="schema.output_table_1"
        )
        return {
            "status": "success",
            "result": result,
            "error": None
        }
    except Exception as e:
        return {
            "status": "failed",
            "result": None,
            "error": str(e)
        }
```

## Workflow

1. **Read** Entrypoints Inventory to get entry points and function signatures
2. **Read** I/O Inventory to understand parameters and context
3. **Generate** import statements for the pipeline under test
4. **Generate** parameter setup based on I/O mappings
5. **Generate** execution wrapper with error handling
6. **Output** Python execution files per pipeline

## Design Considerations

- Must correctly identify and call the pipeline entry point
- Should pass the right parameters (session, table names, config)
- Should capture both successful results and exceptions
- Should support pipelines with different invocation patterns
- Execution should be idempotent (safe to re-run)
