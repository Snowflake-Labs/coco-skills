# dvp-testing-status-manager

> Generate an HTML dashboard showing pipeline test status.

## Overview

| Field | Value |
|-------|-------|
| **Category** | data-validator |
| **Status** | Planned |
| **Output** | HTML status report |
| **Depends on** | All test generation skills |

## Responsibility

Shows a list of pipelines and their status across the entire test generation process: test generated, schema inferred, data inferred, test executed, validation status, match with source output, etc.

## Inputs

| Input | Required | Description |
|-------|----------|-------------|
| Entrypoints Inventory | Yes | From dvp-entrypoint-identifier (`entrypoints.json`) |
| I/O Inventory | Yes | From dvp-io-schema-identifier |
| Schema status | Yes | Whether schemas were inferred |
| Data status | Yes | Whether test data was generated |
| Test results | Yes | Execution and validation results |

## Outputs

### Status Dashboard (`04-results/testing_status.html`)

A self-contained HTML file with:

- **Pipeline summary table** with status columns:
  - Pipeline name
  - Entry point identified (Yes/No)
  - I/O mapped (Yes/No)
  - Schema inferred (Yes/No)
  - Test data generated (Yes/No)
  - Stage conversion done (Yes/No)
  - Test setup generated (Yes/No)
  - Test executed (Pass/Fail/Pending)
  - Validation result (Match/Mismatch/Pending)
  - Overall status

- **Aggregate statistics:**
  - Total pipelines discovered
  - Pipelines ready for testing
  - Tests generated / executed / passed
  - Coverage percentage

- **Drill-down details:**
  - Per-pipeline I/O details
  - Schema mismatches
  - Validation failures with specifics

## Dashboard Mockup

```
╔═══════════════════════════════════════════════════════════════╗
║  DVP Testing Status Dashboard                                ║
║  Generated: 2026-02-12 | Pipelines: 45 | Tested: 32 (71%)   ║
╠═══════════════════════════════════════════════════════════════╣
║                                                               ║
║  ┌──────────┬────┬────┬──────┬──────┬──────┬────────┬──────┐ ║
║  │ Pipeline │ EP │ IO │Schema│ Data │Setup │ Result │Status│ ║
║  ├──────────┼────┼────┼──────┼──────┼──────┼────────┼──────┤ ║
║  │ pipe_001 │ ✓  │ ✓  │  ✓   │  ✓   │  ✓   │  PASS  │  ✓  │ ║
║  │ pipe_002 │ ✓  │ ✓  │  ✓   │  ✓   │  ✓   │  FAIL  │  ✗  │ ║
║  │ pipe_003 │ ✓  │ ✓  │  ✗   │  -   │  -   │   -    │  ⏳ │ ║
║  │ pipe_004 │ ✓  │ ✗  │  -   │  -   │  -   │   -    │  ⏳ │ ║
║  └──────────┴────┴────┴──────┴──────┴──────┴────────┴──────┘ ║
║                                                               ║
║  Legend: ✓ Done  ✗ Failed  ⏳ Pending  - Not applicable       ║
╚═══════════════════════════════════════════════════════════════╝
```

## Workflow

1. **Read** all inventory and result files from previous skill outputs
2. **Aggregate** status per pipeline across all stages
3. **Compute** overall statistics
4. **Generate** self-contained HTML with embedded CSS/JS
5. **Output** the HTML report file

## Design Considerations

- HTML should be self-contained (no external dependencies)
- Should support filtering and sorting in the browser
- Should use color-coding for quick visual scanning (green/red/yellow)
- Should be re-generatable as tests progress
- Consider export to CSV for integration with other tools
