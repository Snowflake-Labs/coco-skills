# dvp-ewi-dashboard-generator

> Orchestrate the EWI tracking manager workflow.

## Overview

| Field | Value |
|-------|-------|
| **Category** | tracking-manager |
| **Status** | Planned |
| **Output** | Orchestration (coordinates other skills) |
| **Depends on** | dvp-ewi-extractor, dvp-ewi-tracking-manager, dvp-ewi-fixer |

## Responsibility

Orchestrates the complete EWI tracking flow: extraction from Issues.csv, dashboard generation, and fix coordination. Acts as the entry point for users who want to manage their migration EWIs end-to-end.

## Workflow

```
1. Run dvp-ewi-extractor
   └── Input:  Issues.csv
   └── Output: ewi_inventory.json

2. Run dvp-ewi-tracking-manager
   └── Input:  ewi_inventory.json
   └── Output: ewi_dashboard.html

3. Offer dvp-ewi-fixer
   └── Input:  Source .py files + ewi_inventory.json
   └── Output: Modified .py files + updated inventory

4. Regenerate dashboard
   └── Re-run dvp-ewi-tracking-manager with updated inventory
```

## User Interaction Flow

```
┌─────────────────────────────────────────────────┐
│ "I want to manage my migration EWIs"            │
└───────────────────────┬─────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│ Step 1: Locate Issues.csv                       │
│ "Where is your SMA Issues.csv file?"            │
└───────────────────────┬─────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│ Step 2: Extract & Show Summary                  │
│ "Found 245 EWIs (12 errors, 180 warnings).      │
│  34 unique codes across 28 files."              │
└───────────────────────┬─────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│ Step 3: Generate Dashboard                      │
│ "Dashboard generated at reports/ewi_dashboard.html" │
└───────────────────────┬─────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│ Step 4: Offer Auto-Fix                          │
│ "Would you like to auto-fix EWIs?               │
│  - All at once                                  │
│  - By category (errors first)                   │
│  - By priority                                  │
│  - Specific codes only"                         │
└───────────────────────┬─────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│ Step 5: Apply Fixes & Update                    │
│ "Fixed 120 EWIs. Updated dashboard.             │
│  65 remaining (need manual review)."            │
└─────────────────────────────────────────────────┘
```

## Inputs

| Input | Required | Description |
|-------|----------|-------------|
| Issues.csv | Yes | SMA migration output |
| SMA migrated source code | Yes | For ewi-fixer to modify |

## Outputs

- EWI Inventory (JSON) -- from ewi-extractor
- EWI Dashboard (HTML) -- from ewi-tracking-manager
- Modified source files -- from ewi-fixer
- Updated inventory with resolution status

## Design Considerations

- Should allow running any sub-skill independently
- Should re-generate dashboard after fixes are applied
- Should provide clear progress reporting at each step
- Should handle the case where Issues.csv doesn't exist (prompt user)
- Should preserve state across multiple sessions (inventory persists)
