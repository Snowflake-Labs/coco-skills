# Tracking Manager Skills

Skills focused on **EWI (Errors, Warnings, Informational messages) tracking and resolution** from the Snowpark Migration Accelerator (SMA) output.

## What are EWIs?

EWIs are issues identified during the automated migration of Spark code to Snowpark by the SMA tool. They appear as comments in the converted code:

```python
#EWI: SPRKPY1045 => This function is not supported in Snowpark
```

Each EWI has:
- A **code** (e.g., `SPRKPY1045`) identifying the issue type
- A **description** explaining what was detected
- A **resolution path** (automatic, manual, or partial)

## Execution Flow

The tracking-manager skills form a pipeline, orchestrated by `dvp-ewi-dashboard-generator`:

```
1. dvp-ewi-extractor          ──► Parse Issues.csv to JSON inventory
2. dvp-ewi-tracking-manager   ──► Generate HTML tracking dashboard
3. dvp-ewi-fixer              ──► Auto-resolve EWIs in source code
4. dvp-ewi-dashboard-generator──► Orchestrate the above flow
```

## Dependency Graph

```
Issues.csv (SMA output)
       │
       ▼
ewi-extractor ──► EWI Inventory (JSON)
       │                  │
       │                  ▼
       │          ewi-tracking-manager ──► EWI Dashboard (HTML)
       │                  │
       │                  ▼
       └──────────► ewi-fixer ──► Modified .py files + updated inventory
                          │
               ewi-dashboard-generator (orchestrates all three)
```

## Skills

| Skill | Phase | Output | Status |
|-------|-------|--------|--------|
| [dvp-ewi-extractor](dvp-ewi-extractor.md) | Extraction | EWI Inventory (JSON) | Planned |
| [dvp-ewi-tracking-manager](dvp-ewi-tracking-manager.md) | Reporting | HTML Dashboard | Planned |
| [dvp-ewi-fixer](dvp-ewi-fixer.md) | Resolution | Modified source code | **Implemented** |
| [dvp-ewi-dashboard-generator](dvp-ewi-dashboard-generator.md) | Orchestration | Coordinates all | Planned |

## Resolution Tracking

A key feature of the tracking-manager is tracking **who** resolved each EWI:

| Resolver | Description |
|----------|-------------|
| **Cortex** | Automatically resolved by the AI during migration |
| **EWI Fixer** | Resolved by the dvp-ewi-fixer skill |
| **User** | Manually resolved by a developer |
| **Unresolved** | Still pending resolution |

This distinction helps measure automation effectiveness and identify areas needing manual attention.
