# dvp-ewi-extractor

> Extract EWI issues from Issues.csv into structured JSON.

## Overview

| Field | Value |
|-------|-------|
| **Category** | tracking-manager |
| **Status** | Planned |
| **Output** | EWI Inventory (JSON) |
| **Depends on** | None (first in EWI chain) |

## Responsibility

Analyzes the `Issues.csv` file from SMA migration output and creates a structured JSON codebase. This JSON serves as the foundation for the tracking dashboard and the fixer skill.

## Inputs

| Input | Required | Description |
|-------|----------|-------------|
| Issues.csv | Yes | SMA migration output file listing all EWIs |

## Outputs

### EWI Inventory (`ewi_inventory.json`)

```json
{
  "extracted_at": "2026-02-12T10:00:00Z",
  "source_file": "Issues.csv",
  "summary": {
    "total_issues": 245,
    "by_severity": {
      "error": 12,
      "warning": 180,
      "info": 53
    },
    "by_code": {
      "SPRKPY1045": 15,
      "SPRKPY1023": 8
    },
    "unique_codes": 34,
    "affected_files": 28
  },
  "issues": [
    {
      "id": "ewi-001",
      "code": "SPRKPY1045",
      "severity": "warning",
      "message": "This function is not supported in Snowpark",
      "file": "pipeline_x/transform.py",
      "line": 42,
      "column": 10,
      "status": "unresolved",
      "resolved_by": null,
      "resolved_at": null,
      "category": "unsupported_function",
      "priority": "high"
    }
  ]
}
```

## Issue Categories

The extractor should classify EWIs into categories:

| Category | Description | Example Codes |
|----------|-------------|---------------|
| `unsupported_function` | Spark API not available in Snowpark | SPRKPY1045, SPRKPY1046 |
| `type_mismatch` | Data type differences | SPRKPY1030-1039 |
| `syntax_change` | Syntax differs between Spark and Snowpark | SPRKPY1020-1029 |
| `missing_dependency` | Required library not available | SPRKPY1050+ |
| `behavioral_difference` | Same API, different behavior | SPRKPY1060+ |
| `manual_review` | Requires human judgment | Various |

## Workflow

1. **Read** the Issues.csv file from SMA output
2. **Parse** each row extracting: code, severity, message, file, line
3. **Classify** each issue by category and priority
4. **Aggregate** statistics (by severity, code, file)
5. **Generate** unique IDs for tracking
6. **Initialize** status as "unresolved" for all issues
7. **Output** the structured JSON inventory

## CSV Parsing Rules

- Handle both comma and semicolon delimiters
- Handle quoted fields with embedded commas
- Skip header row
- Handle missing/empty fields gracefully
- Preserve original line numbers and file paths

## Design Considerations

- JSON should be the single source of truth for EWI tracking
- IDs should be stable across re-extractions (based on code + file + line)
- Should support incremental updates (re-extract without losing resolution status)
- Category/priority classification should be configurable
- Output should be directly consumable by dvp-ewi-tracking-manager and dvp-ewi-fixer
