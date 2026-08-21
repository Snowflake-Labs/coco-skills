# dvp-entrypoint-identifier

> Generate `dvp/04-results/entrypoints.json` from an ASG.

## Overview

| Field | Value |
|-------|-------|
| **Category** | data-validator |
| **Status** | **Implemented** |
| **Output** | Entrypoints Inventory (JSON) |
| **Depends on** | dvp-asg-generation (ASG must exist) |
| **SKILL.md** | [`dvp-entrypoint-identifier/SKILL.md`](../../dvp-entrypoint-identifier/SKILL.md) |

## Responsibility

Hybrid approach:
- **Deterministic pass:** consume an ASG JSON (`dvp/04-results/*_asg.json`) and generate a baseline `entrypoints.json` using the embedded WARP `EntrypointDetector`.
- **Non-deterministic IA pass:** evaluate that baseline (including empty/incomplete output) and patch it by adding/adjusting/disabling entrypoints when needed.

Produces `dvp/04-results/entrypoints.json` for downstream skills.

## Workflow

1. Load ASG JSON from `dvp/04-results/*_asg.json`.
   - If **0** ASG files are found: stop (run `dvp-asg-generation` first).
   - If **>1** ASG files are found: stop and ask the user which ASG to use.
2. Detect entry points using `EntrypointDetector`.
3. Write `dvp/04-results/entrypoints.json` (canonical results directory is `dvp/04-results/`).

Notes:
- Detection is deterministic and based only on ASG structures (e.g., `source_files`, `execution_calls`, `data_in`, `data_out`).
- The detector rolls up I/O per entry point by tracing transitive dependencies via the ASG call graph.

## Inputs

| Input | Required | Location |
|-------|----------|----------|
| ASG JSON | Yes | `dvp/04-results/*_asg.json` |

## Outputs

| Output | Format | Location |
|--------|--------|----------|
| Entrypoints inventory | JSON | `dvp/04-results/entrypoints.json` |

## Entrypoints Inventory Schema

The output is a JSON array:

```json
[
  {
    "name": "daily_report",
    "origin": "ASG",
    "status": "detected",
    "source": "jobs/daily_report.py:134",
    "type": "script",
    "reason": "main_guard",
    "inputs": {"total": 2, "by_type": {"parquet": 2}},
    "outputs": {"total": 1, "by_type": {"delta": 1}}
  }
]
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Entrypoint name (usually file stem) |
| `origin` | string | **Required**: `ASG` or `IA` |
| `status` | string | **Required**: `detected` or `disabled` |
| `source` | string | Hybrid locator `<path>:<lineno>(::segment)*`. Last `::` segment is the method; preceding segments are scope. Python: `workload.py:134`, Scala: `App.scala:5::MyApp::main`, Notebook: `notebook.py:1` |
| `type` | string | `script`, `module`, or `databricks_notebook` |
| `reason` | string | Detection reason (`main_guard`, `notebook`, `spark_session_creation`, `main_method`) or disable justification |
| `inputs` | object | `{ total, by_type }` rollup across transitive deps |
| `outputs` | object | `{ total, by_type }` rollup across transitive deps |
| `adapted_source` | string | *(set by dvp-code-adapter)* Post-adaptation callable, same hybrid format. See [entrypoints-source-spec.md](../entrypoints-source-spec.md) |

## Entry Point Detection

The `EntrypointDetector` uses ASG metadata (`source_files[*].is_entry_point`, `source_files[*].source_type`, `execution_calls`, `data_in`, `data_out`) to identify entrypoints and roll up I/O.

## Stopping Points

- No `*_asg.json` found in `dvp/04-results/` → run `dvp-asg-generation` first
- Multiple `*_asg.json` found in `dvp/04-results/` → ask the user which ASG to use
- ASG file cannot be read/parsed


## Execution

See [`dvp-entrypoint-identifier/SKILL.md`](../../dvp-entrypoint-identifier/SKILL.md) for an execution snippet.
