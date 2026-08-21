# SMA Dashboard - User Guide

Interactive dashboard to track and manage EWIs (Errors, Warnings, Issues) from Snowflake Migration Accelerator conversions.

## Getting Started

### Prerequisites

- Python 3.10+
- SMA conversion output with `Reports/Issues.csv`
- **Supported platforms**: Windows, Linux, macOS

### Python Environment Setup (if needed)

If your default Python version is older than 3.10, create a virtual environment:

```bash
# Check your Python version
python3 --version

# If < 3.10, install Python 3.10+ first (macOS with Homebrew)
brew install python@3.10

# Create virtual environment with Python 3.10
python3.10 -m venv ~/.venvs/sma-dashboard

# Activate the environment
source ~/.venvs/sma-dashboard/bin/activate

# Verify version
python --version  # Should show 3.10.x or higher
```

For other systems:
- **Ubuntu/Debian**: `sudo apt install python3.10 python3.10-venv`
- **Windows**: Download from [python.org](https://www.python.org/downloads/)
  ```cmd
  # Create virtual environment (Windows CMD)
  python -m venv %USERPROFILE%\.venvs\sma-dashboard
  
  # Activate (Windows CMD)
  %USERPROFILE%\.venvs\sma-dashboard\Scripts\activate
  
  # Activate (PowerShell)
  & $env:USERPROFILE\.venvs\sma-dashboard\Scripts\Activate.ps1
  ```

### Generate Dashboard

Navigate to your workload directory (the folder containing `Reports/Issues.csv`) and run:

```bash
$sma-dashboard-generator
```

The dashboard will be generated in `sma-dashboard/` and automatically open in your browser.

## Dashboard Overview

### Side Panel

The left side panel provides:

- **Snowflake Logo**: Branding header
- **Navigation**: Switch between modules (Overview, EWI Tracker, File Tracker)
- **Server Status**: Shows connection status (green = connected)
- **Stop Server**: Button to stop the server (visible when connected)
- **Copy Command**: When server is offline, copy the command to restart it
- **Dark Mode Toggle**: Switch between light and dark themes

---

## Overview Module

The Overview module provides a high-level summary of your migration readiness and key metrics.

### Summary Cards

At the top, you'll see:
- **Total Files**: Number of files analyzed
- **Total EWIs**: Unique EWI codes found
- **Total Occurrences**: Total number of EWI instances across all files

### EWI Breakdown

Visual breakdown of EWIs by category:
- **ConversionError**: Code that cannot be automatically converted
- **Warning**: Code that may need manual review
- **ParsingError**: Code that could not be parsed

### Migration Readiness

Three clickable cards showing file classification:

| Category | Criteria | Description |
|----------|----------|-------------|
| **Ready** | ≤5 issues, no blockers | Files likely ready for migration with minimal changes |
| **Needs Review** | >5 issues, no blockers | Files requiring more extensive review |
| **Blocked** | Has blocker EWIs | Files with critical issues that prevent migration |

**Click on any card** to expand and see the list of files in that category. Click on a file to navigate directly to its EWI details page.

### Critical Blockers

This section lists EWI codes that are considered **critical blockers** for migration. These are issues that:
1. Have **Category: ConversionError** - Code cannot be converted
2. Have **Category: ParsingError** - Code cannot be parsed
3. Have **no recommended fix** in the SMA documentation

#### Blocker EWI Codes

Based on SMA documentation, the following codes are classified as blockers:

| Code | Description | Reason |
|------|-------------|--------|
| **PNDSPY1001** | Pandas element not supported | Conversion Error |
| **PNDSPY1003** | Pandas element not yet recognized | Conversion Error |
| **SPRKPY1001** | Parsing errors in code | Parsing Error |
| **SPRKPY1002** | Spark element not supported | Conversion Error |
| **SPRKPY1003** | Spark element not yet recognized | Conversion Error |
| **SPRKPY1004** | Parsing errors in code | Parsing Error |
| **SPRKPY1032** | Element is not defined | Conversion Error |
| **SPRKPY1038** | Element is not yet recognized | Conversion Error |
| **SPRKPY1054** | JDBC format not supported | No fix available |
| **SPRKPY1058** | RuntimeConfig platform-specific keys | No fix available |
| **SPRKPY1067** | Split with regex pattern | No fix available |
| **SPRKPY1074** | Mixed indentation (tabs/spaces) | Parsing Error |
| **SPRKPY1084** | ML element not supported | No fix available |
| **SPRKPY1085** | VectorAssembler not supported | No fix available |
| **SPRKPY1086** | VectorUDT not supported | No fix available |

### File Complexity

Visual breakdown of files by complexity:

| Level | Criteria | Description |
|-------|----------|-------------|
| **Simple** | 0-2 issues | Minimal changes needed |
| **Moderate** | 3-5 issues | Some refactoring required |
| **Complex** | 6+ issues | Significant work needed |

---

## EWI Tracker Module

The main module for tracking conversion issues by EWI code.

### Summary Cards

At the top, you'll see status counts:
- **Total EWIs**: All unique EWI codes found
- **Pending**: Not yet reviewed
- **In Progress**: Currently being worked on
- **Resolved**: Fixed or addressed
- **Won't Fix**: Accepted as-is

### Progress Bar

Visual representation of overall progress with color-coded segments.

### Filters and Search

- **Search**: Filter by EWI code or description
- **Category**: Filter by EWI category (Error, Warning, Info, etc.)
- **Status**: Filter by current status
- **Refresh**: Reload data from server

### EWI Table

| Column | Description |
|--------|-------------|
| Code | EWI code (clickable link to documentation) |
| Description | Brief description of the issue |
| Category | Classification (Error, Warning, Info) |
| Occurrences | Total number of times this EWI appears |
| Files Affected | Number of files with this EWI (clickable) |
| Status | Dropdown to change status |
| Notes | Free-text field for comments |

Click on column headers to sort.

### Files Detail Page (per EWI)

Click on "X files" in the Files Affected column to see:
- List of all files affected by that specific EWI
- Line numbers where the EWI occurs
- Status badges showing line-level progress
- File status dropdown to update all lines at once
- Individual line status dropdowns
- VS Code links to open files directly

---

## File Tracker Module

Track progress by file, showing all EWIs affecting each file.

### Summary Cards

- **Total Files**: All files with EWIs
- **Pending**: Files not yet reviewed
- **In Progress**: Files being worked on
- **Resolved**: Files completed
- **Won't Fix**: Files accepted as-is

### File Table

| Column | Description |
|--------|-------------|
| File Path | Full path to the file |
| EWIs | Number of unique EWIs in this file (clickable) |
| Status | Dropdown to change file status (cascades to all lines) |
| Pending | Count of pending lines |
| In Progress | Count of in-progress lines |
| Manual Resolved | Count of manually resolved lines |
| Auto Resolved | Count of auto-resolved lines |
| Not Auto Resolved | Count of lines not auto-resolved |
| Won't Fix | Count of won't-fix lines |
| Total | Total lines affected |

Click on column headers to sort. All numeric columns sort descending by default.

### File EWIs Detail Page

Click on the EWI count to see all EWIs for that specific file:

- **EWI Cards**: Each EWI affecting the file shown as a card
- **Status Badges**: Visual count of line statuses per EWI
- **Set All Dropdown**: Change status for all lines of an EWI at once
- **Line Status**: Individual status dropdowns per line
- **Summary Cards**: Total counts across all EWIs in the file

---

## Status Consistency

The dashboard automatically synchronizes status consistency:

### File Status Sync
When all lines of all EWIs in a file have the same status, the file status is automatically updated to match.

### EWI Status Sync
When all lines of an EWI across all files have the same status, the EWI status is automatically updated to match.

### Cascading Updates

| Action | Effect |
|--------|--------|
| Change EWI status (EWI Tracker) | Updates all lines of that EWI in all files |
| Change file status (File Tracker) | Updates all lines of all EWIs in that file |
| Change EWI status in file (File EWIs page) | Updates all lines of that EWI in that file |
| Change individual line status | Triggers consistency check for file and EWI |

---

## Server Management

### Automatic Server

When generating the dashboard, a local server starts automatically in the background on port 8080 (or next available port).

### Starting the Server Manually

Use the included `start_server.py` script:

```bash
cd sma-dashboard

# Start server (opens browser automatically)
python start_server.py

# Start on specific port
python start_server.py --port 9000

# Start without opening browser
python start_server.py --no-open

# Check server status
python start_server.py --status

# Stop the server
python start_server.py --stop

# Restart the server (stop + start)
python start_server.py --restart

# List all active servers on ports 8080-8099
python start_server.py --list
```

### Alternative: Direct Server Script

You can also run the server script directly:

```bash
cd sma-dashboard
python server/sma_server.py . --port 8080
```

### Server Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/ewi/update` | POST | Update EWI status/notes (cascades to all files) |
| `/api/file/update` | POST | Update file status (cascades to all lines) |
| `/api/file/ewi/update` | POST | Update single line status |
| `/api/file/ewi/update-all` | POST | Update all lines of an EWI in a file |
| `/api/shutdown` | POST | Stop the server |

---

## Data Persistence

All changes are saved automatically to JSON files:

- `ewi-tracker/ewi_status.json` - EWI statuses and notes
- `ewi-tracker/file_status.json` - File-level and line-level statuses

When regenerating the dashboard, existing statuses and notes are preserved.

---

## Dark Mode

The dashboard supports light and dark themes:

1. Click the **Dark Mode** toggle in the bottom of the side panel
2. Your preference is saved automatically (persists across sessions)
3. The theme applies to all modules and pages

---

## Keyboard Shortcuts

- **Tab**: Navigate between fields
- **Enter**: Confirm selection in dropdowns

---

## Tips

1. **Start with high-occurrence EWIs**: Sort by "Occurrences" to prioritize
2. **Use categories**: Filter by "Error" first to address critical issues
3. **Add notes**: Document decisions for team reference
4. **Use VS Code links**: Quickly navigate to affected files
5. **Use File Tracker**: Track progress by file when working file-by-file
6. **Bulk updates**: Use "Set All" dropdowns to quickly mark multiple lines

---

## Troubleshooting

### Dashboard won't load

1. Check if server is running (green dot in side panel)
2. If offline, click "Copy start command" and run in terminal
3. Verify you're accessing via `http://localhost:PORT` not `file://`

### Changes not saving

1. Verify server is connected (green status dot)
2. Check browser console for errors
3. Ensure write permissions on the dashboard folder

### Port already in use

The server automatically tries ports 8080-8099. If all are in use:

```bash
python start_server.py --port 9000
```

To see which servers are running:

```bash
python start_server.py --list
```

### Status not syncing

If file or EWI status doesn't auto-update:
1. Refresh the page
2. Check that ALL lines have the same status (mixed statuses won't trigger sync)

---

## File Structure

```
sma-dashboard/
├── index.html              # Main dashboard with side panel
├── manifest.json           # Dashboard configuration
├── start_server.py         # Server launcher script
├── assets/
│   ├── styles.css          # Styles
│   └── snowflake_logo.svg  # Logo
├── ewi-tracker/
│   ├── overview.html       # Overview module (migration readiness)
│   ├── ewi_tracker.html    # EWI tracker module
│   ├── file_tracker.html   # File tracker module
│   ├── ewi_status.json     # EWI data and statuses
│   ├── file_status.json    # File-level and line-level data
│   └── content/
│       ├── files_*.html    # EWI file detail pages
│       └── file_ewis_*.html # File EWI detail pages
└── server/
    ├── sma_server.py       # Local HTTP server
    └── .server.pid         # PID file (auto-generated)
```
