---
name: solutions-installer
id: solutions-installer
title: Install Snowflake Solutions
summary: Install pre-built industry solutions into your Snowflake account from the sf-solutions repository.
description: >-
  Use for ALL requests to install, set up, deploy, or tear down a pre-built Snowflake industry solution.
  This skill reads a solution's manifest.json for metadata, then executes its setup SQL scripts
  against the user's Snowflake account.
  Triggers: install solution, coco solutions install, set up solution, deploy solution,
  teardown solution, uninstall solution, sf-solutions, solution accelerator, demo environment.
  Do NOT use for: adding pages to the solutions catalog website (use snowflake-solutions-catalog instead),
  building custom solutions from scratch, or general SQL authoring.
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Bash
  - Read
  - Write
  - Glob
  - Grep
  - WebFetch
prompt: "$solutions-installer <solution-name>"
language: en
status: beta
authors: Sho Tanaka
type: snowflake
categories: solutions, demo, installer
---

# Install Snowflake Industry Solutions

Installs a pre-built industry solution from the [sf-solutions](https://github.com/Snowflake-Labs/sf-solutions) repository into the user's Snowflake account. Each solution contains a `manifest.json` describing its metadata and a `scripts/` directory with SQL setup/teardown files.

# When to Use
- User wants to install a solution: `$solutions-installer manufacturing-predictive-maintenance`
- User wants to tear down / uninstall a solution
- User wants to set up a demo environment for an industry use case
- Do NOT use for editing the solutions catalog website

# What This Skill Provides
- Clones or locates the sf-solutions repository
- Reads the solution's `manifest.json` to understand what will be created
- Executes `install_scripts` (setup SQL) against the user's Snowflake account
- Supports teardown via `teardown_scripts`
- Verifies the installation by checking created objects

# Solution Directory Convention

Each solution follows this structure:

```
<solution-slug>/
├── manifest.json          # REQUIRED: solution metadata (see schema below)
├── README.md              # Solution overview, architecture, prerequisites
└── scripts/
    ├── setup.sql          # Main installation script
    └── teardown.sql       # Cleanup script (drops all created objects)
```

## manifest.json Schema

```json
{
  "name": "solution-slug",
  "display_name": "Human-Readable Solution Name",
  "version": "1.0.0",
  "industry": "Manufacturing",
  "source": "https://github.com/...",
  "license": "MIT",
  "database": "DATABASE_NAME",
  "schemas": ["SCHEMA_A", "SCHEMA_B"],
  "role": "ACCOUNTADMIN",
  "requires_warehouse": true,
  "install_scripts": ["scripts/setup.sql"],
  "teardown_scripts": ["scripts/teardown.sql"],
  "features": ["Cortex Analyst", "Semantic View", "Streamlit in Snowflake"]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Solution slug, matches the directory name |
| `display_name` | Yes | Human-readable name shown to the user |
| `version` | Yes | Semver version string |
| `industry` | Yes | Primary industry vertical |
| `database` | Yes | Database that will be created |
| `schemas` | Yes | List of schemas that will be created |
| `role` | Yes | Required Snowflake role to run the setup |
| `install_scripts` | Yes | Ordered list of SQL scripts to execute for setup |
| `teardown_scripts` | Yes | Ordered list of SQL scripts to execute for cleanup |
| `requires_warehouse` | No | Whether the setup creates its own warehouse(s) |
| `features` | No | Snowflake features used in this solution |
| `source` | No | URL to the original source repository |
| `license` | No | License of the source material |

# Instructions

## Step 0: Resolve the Solution

**Actions:**
1. Parse the solution slug from the user's prompt (e.g., `manufacturing-predictive-maintenance`)
2. If no slug is provided, list available solutions by scanning `<repo>/**/manifest.json` and present a table:
   ```
   | Slug | Name | Industry | Database | Role Required |
   ```
3. **Ask** the user to pick one if not specified

**If the slug does not match any directory:**
- Check if the repo has new solutions (git pull or re-scan)
- If still not found, inform the user and stop

## Step 1: Fetch the Solution Repository

**Actions:**
1. Check if the sf-solutions repo exists locally. Search these paths in order:
   - `~/project/sf-solutions/`
   - `./sf-solutions/`
   - The current working directory (check for `manifest.json` files)
2. If not found locally, clone it:
   ```bash
   git clone https://github.com/Snowflake-Labs/sf-solutions.git /tmp/sf-solutions
   ```
3. Verify the solution directory exists: `<repo>/<slug>/manifest.json`

**Output:** Path to the solution directory.

## Step 2: Read manifest.json and Present Installation Plan

**Actions:**
1. Read `<slug>/manifest.json`
2. Validate all required fields are present
3. Read `<slug>/README.md` for additional context (architecture, prerequisites)
4. List the install scripts that will be executed

**⚠️ STOPPING POINT:** Present the installation plan to the user:
```
Solution: <display_name> (v<version>)
Industry: <industry>
Source:   <source>

Will create:
  Database: <database>
  Schemas:  <schemas joined with ", ">
  Role required: <role>
  Features: <features joined with ", ">

Scripts to execute:
  1. <install_scripts[0]>
  2. <install_scripts[1]> (if any)

Proceed with installation?
```

Wait for user confirmation before proceeding.

## Step 3: Execute Install Scripts

**Actions:**
1. For each script in `install_scripts` (in order):
   a. Read the SQL file
   b. Split into individual SQL statements (split on `;`, respecting comments and string literals)
   c. Execute each statement sequentially using `snowflake_sql_execute`
2. For long-running statements (ML models, large data generation, SPCS compute pools):
   - Use `timeout_seconds: 600`
   - Inform the user that the statement may take several minutes
3. For statements that depend on `RESULT_SCAN(LAST_QUERY_ID())`:
   - Execute them immediately after the preceding statement
   - Do NOT batch these
4. After each major section, log progress:
   - "Created database <name>"
   - "Created schema <name>"
   - "Loaded <N> rows into <table>"

**If a statement fails:**
- "insufficient privileges": Show the required role from manifest.json, ask the user to switch roles or grant privileges
- "object already exists" (without CREATE OR REPLACE): Ask user whether to skip or drop-and-recreate
- Other errors: Show the error and the failing SQL, ask user for guidance

**Output:** Confirmation of each script completed.

## Step 4: Verify Installation

**Actions:**
1. Run verification queries using the `database` and `schemas` from manifest.json:
   ```sql
   SELECT TABLE_SCHEMA, TABLE_NAME, ROW_COUNT
   FROM <database>.INFORMATION_SCHEMA.TABLES
   WHERE TABLE_SCHEMA IN (<schemas>)
   ORDER BY TABLE_SCHEMA, TABLE_NAME;
   ```
2. Check that all schemas from manifest.json exist
3. Report any schemas that are empty or missing

**Output:** Summary table of all created objects and row counts.

## Step 5: Post-Install Summary

**Present to the user:**
```
Solution installed: <display_name> v<version>

Objects created:
  Database: <database>
  Schemas:  <list schemas with object counts>
  Tables:   <count> tables (<total rows> total rows)
  Views:    <count> views

Features enabled: <features>

Next steps:
  - See README: <slug>/README.md
  - Teardown: $solutions-installer teardown <slug>
```

# Teardown Flow

When the user requests teardown / uninstall:

1. Read `manifest.json` to get `teardown_scripts`
2. **⚠️ STOPPING POINT:** Warn the user:
   ```
   This will DROP the following objects:
     Database: <database>
     (and all schemas, tables, views, models within it)

   This action is IRREVERSIBLE. Proceed?
   ```
3. Upon confirmation, execute each script in `teardown_scripts`
4. Verify the database no longer exists

# Best Practices
- Always read manifest.json first — it is the source of truth for the solution
- Execute SQL statements one at a time to isolate errors
- Never modify the source SQL files — read and execute them as-is
- Log each step clearly so the user knows what was created
- For ML model training or large data loads, set timeout to 600s
- Always present the installation plan and wait for confirmation before executing

# Stopping Points
- ✋ Step 2 — After presenting the installation plan, wait for user confirmation
- ✋ Step 3 — If privilege errors occur, stop and ask user for role
- ✋ Teardown — Before dropping any objects, confirm with user

**Resume rule:** Upon user approval, proceed directly to the next step without re-asking.

# Output
- Fully provisioned Snowflake environment with the chosen solution
- Database, schemas, tables, views, and ML models ready to use
- Summary of all created objects with row counts

# Examples

## Example 1: Install manufacturing predictive maintenance
User: $solutions-installer manufacturing-predictive-maintenance
Assistant:
1. Finds repo at ~/project/sf-solutions/
2. Reads manufacturing-predictive-maintenance/manifest.json
3. Presents plan: Database SNOWCORE_INDUSTRIES, schemas BRONZE/SILVER/GOLD, role ACCOUNTADMIN
4. User confirms → executes scripts/setup.sql statement by statement
5. Verifies all tables created, shows row counts
6. Shows summary with next steps

## Example 2: Teardown a solution
User: $solutions-installer teardown manufacturing-predictive-maintenance
Assistant:
1. Reads manifest.json → teardown_scripts: ["scripts/teardown.sql"]
2. Warns: "This will DROP database SNOWCORE_INDUSTRIES. Irreversible. Proceed?"
3. User confirms → executes teardown.sql
4. Verifies database is gone

## Example 3: List available solutions
User: $solutions-installer
Assistant: Scans repo for manifest.json files, shows table of available solutions, asks user to pick one
