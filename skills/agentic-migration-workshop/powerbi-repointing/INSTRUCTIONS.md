
# Workshop Session: Power BI Repointing (Day 5 — Data Consumption)

## Session Overview

**Present to user:**
> Welcome to **Power BI Repointing** — part of Day 5: Data Consumption. We'll redirect your Power BI reports from your source database to Snowflake, so your reporting layer continues working seamlessly.
>
> SnowConvert AI automates most of this — it swaps connection strings, updates schema references, and flags any reports that need manual attention.
>
> Here's our plan:
> 1. Prepare your Power BI files
> 2. Run SnowConvert AI repointing
> 3. Review and validate the repointed reports
>
> Let's get your reports ready.

## Prerequisites
- SnowConvert AI installed
- Power BI reports saved as `.pbit` files (template format)
- DDLs migrated (recommended — helps SnowConvert AI identify tables and views)

## Supported Sources

| Source Platform | Supported |
|----------------|-----------|
| SQL Server | Yes |
| Oracle | Yes |
| Teradata | Yes |
| Redshift | Yes |
| Azure Synapse | Yes |
| PostgreSQL | Yes |

## Session Flow

### Part 1: Prepare Power BI Files

**Ask the user** (via `ask_user_question`):
- Have you saved your Power BI projects as `.pbit` (template) format?
- Do you have DDL files for the underlying database objects?
- Which source platform do your reports currently connect to?

**If user hasn't saved as .pbit:**
> Power BI reports need to be in `.pbit` (template) format for SnowConvert AI to process them. In Power BI Desktop: File → Save As → Power BI Template (.pbit).

### Part 2: Run SnowConvert AI Repointing

**Walk the user through the process:**

> In SnowConvert AI:
> 1. Optionally add your DDLs (improves object identification)
> 2. Select the source language used in your Power BI reports (e.g., SQL Server)
> 3. Add your `.pbit` files in the Power BI repointing section
> 4. Click **"Continue to Conversion"**

### Part 3: Review and Validate

**Guide the user through validation:**

> Let's verify your repointed reports work correctly:
> 1. Open the repointed Power BI report
> 2. Fill in the Snowflake parameters (SnowConvert AI adds these automatically):
>    - Server link
>    - Warehouse name
>    - Database name
> 3. Refresh data
> 4. Compare against the original report — same numbers, same charts, same filters
> 5. Save in your preferred format (`.pbix`)

**Review the assessment report:**
> Check the "ETLAndBiRepointing" report for a summary of which connectors were changed and any items requiring attention.

**CHECKPOINT:**
> How do the repointed reports look? Do the numbers match your original reports?

## Estimation Reference

**Share with user for planning:**

> For context, here's what a typical Power BI repointing engagement looks like:

**Sample Timeline (500 Reports, 3-4 BI Developers + 1 Architect):**

| Phase | Duration | Key Activities |
|-------|----------|---------------|
| Assessment & Analysis | 1 week | SnowConvert AI scans all PBIT/PBIX files; outputs Repointing Automation Score |
| Automated Repointing | 1-2 weeks | Auto-swap connections for low-risk reports (typically ~75%) |
| Manual Refactoring | 2-3 weeks | BI developers refactor Custom T-SQL/M-Code in Power Query/DAX (~25%) |
| Functional Validation (UAT) | 2-3 weeks | Business users validate data integrity, filters, measures, charts |
| **Total** | **6-9 weeks** | Production-ready BI layer |

**Report risk categories:**

| Category | Risk | Effort | Description |
|----------|------|--------|-------------|
| Direct table queries | Low | Auto-repointed | Connection string swap only |
| Standard SQL queries | Low-Medium | Mostly automated | Minor ANSI SQL adjustments |
| Custom SQL in Power Query | High | Manual refactoring | Proprietary T-SQL in Power Query/M-Code |
| DAX with source-specific logic | Medium-High | Manual review | DAX measures referencing source patterns |

**Key metrics:**
- **Repointing Automation Score** — % of reports auto-updated (the higher, the faster)
- **High-risk reports** — number requiring query refactoring (largest time driver)
- **UAT bottleneck** — business user testing is often the longest phase

## Session Wrap-Up

**Present to user:**
> Power BI Repointing complete! Here's the summary:
> - [X] reports repointed to Snowflake
> - [Y]% automated (connection swap only)
> - [Z] required manual refactoring
> - All reports validated with data refresh
>
> Your reporting layer is now running on Snowflake.

## Broader Data Consumption Context

When repointing is part of a larger migration, also consider:
- **Outbound integration inventory** — all systems consuming data (reports, analytics, APIs, extracts)
- **Platform compatibility** — verify each tool's Snowflake connector support
- **User training** — developers and users need to learn Snowflake access patterns
- **Data governance** — maintain or enhance cataloging in the new environment

## Deliverables
- Repointed `.pbit`/`.pbix` files with Snowflake connectors
- ETLAndBiRepointing assessment report
- Repointing Automation Score report
