# dvp-ewi-tracking-manager

> Create a user-friendly HTML interface for EWI tracking.

## Overview

| Field | Value |
|-------|-------|
| **Category** | tracking-manager |
| **Status** | Planned |
| **Output** | HTML Dashboard |
| **Depends on** | dvp-ewi-extractor |

## Responsibility

Creates a user-friendly HTML interface for EWIs. Should track which EWI was resolved by Cortex or by User, providing visibility into migration quality and remaining work.

## Inputs

| Input | Required | Description |
|-------|----------|-------------|
| EWI Inventory (JSON) | Yes | From dvp-ewi-extractor |

## Outputs

### EWI Dashboard (`reports/ewi_dashboard.html`)

A self-contained HTML file with interactive tracking capabilities.

## Dashboard Features

### Summary Panel
- Total EWIs count with severity breakdown
- Resolution progress (resolved vs. unresolved)
- Resolution source breakdown (Cortex vs. EWI Fixer vs. User)

### Issues Table
- Sortable and filterable by: code, severity, file, status, resolver
- Search functionality
- Group-by options (by code, by file, by severity)

### Charts/Visualizations
- Severity distribution (pie/donut chart)
- Resolution progress (progress bar)
- Top EWI codes (bar chart)
- Resolution by source (stacked bar)

### Per-File View
- List of files with EWI counts
- Click to see EWIs in that file
- File-level resolution status

## Dashboard Mockup

```
╔═══════════════════════════════════════════════════════════════════╗
║  EWI Tracking Dashboard                                          ║
║  Total: 245 | Resolved: 180 (73%) | Remaining: 65               ║
╠═══════════════════════════════════════════════════════════════════╣
║                                                                   ║
║  Resolution Source:                                               ║
║  ████████████████░░░░░░░░  Cortex (120)                          ║
║  ████████░░░░░░░░░░░░░░░░  EWI Fixer (40)                       ║
║  ████░░░░░░░░░░░░░░░░░░░░  User (20)                            ║
║  ░░░░░░░░░░░░░░░░░░░░░░░░  Unresolved (65)                      ║
║                                                                   ║
║  ┌──────┬──────────┬──────────┬────────┬──────────┬────────────┐ ║
║  │ Code │ Severity │   File   │  Line  │  Status  │ Resolved By│ ║
║  ├──────┼──────────┼──────────┼────────┼──────────┼────────────┤ ║
║  │ 1045 │ Warning  │ main.py  │   42   │ Resolved │  Cortex    │ ║
║  │ 1023 │ Error    │ etl.py   │   15   │ Open     │     -      │ ║
║  │ 1078 │ Info     │ util.py  │  103   │ Resolved │  User      │ ║
║  └──────┴──────────┴──────────┴────────┴──────────┴────────────┘ ║
╚═══════════════════════════════════════════════════════════════════╝
```

## Status Lifecycle

```
Unresolved ──► In Progress ──► Resolved (by Cortex | EWI Fixer | User)
     │                              │
     └──────── Dismissed ◄──────────┘ (false positive / not applicable)
```

## Workflow

1. **Read** EWI Inventory JSON from dvp-ewi-extractor
2. **Compute** aggregated statistics
3. **Generate** HTML with embedded CSS and JavaScript
4. **Include** interactive filtering, sorting, and search
5. **Include** charts using lightweight embedded library (or pure CSS)
6. **Output** self-contained HTML file

## Design Considerations

- HTML must be self-contained (no external CDN dependencies)
- Should support client-side filtering and sorting (JavaScript)
- Color-coding: red (error), yellow (warning), blue (info), green (resolved)
- Should be regeneratable when the EWI inventory is updated
- Consider a "print-friendly" view for stakeholder reports
- Should handle large EWI counts efficiently (virtual scrolling for 1000+ items)
