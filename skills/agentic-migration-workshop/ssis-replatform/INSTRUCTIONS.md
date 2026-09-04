
# Workshop Session: SSIS Re-platforming (SQL Server ETL Migration)

## Session Overview

**Present to user:**
> Welcome to the **SSIS Re-platforming** session. We'll convert your SQL Server Integration Services packages into Snowflake-native components: **Snowflake Tasks** for orchestration, **stored procedures** for control flow, and **dbt projects** for data transformations.
>
> This is one of the more complex parts of a SQL Server migration, but SnowConvert AI handles the heavy lifting. Here's our plan:
> 1. Set up a SnowConvert AI project for SSIS replatforming
> 2. Run the automated conversion
> 3. Deploy the converted components
> 4. Validate that your ETL logic produces the same results
>
> Let's get started.

## Prerequisites
- SnowConvert AI installed
- Valid `.dtsx` SSIS package files
- All dependent database objects (DDL scripts for tables, views, functions, procedures referenced by SSIS packages)

## Session Flow

### Part 1: Project Setup

**Guide the user:**

> In SnowConvert AI:
> 1. Create a New Project
> 2. **Important:** Select "SQL Server" as the source platform
> 3. In extraction configuration, select the **"Replatform"** option — this tells SnowConvert AI to treat your SSIS packages specially
> 4. Point to your `.dtsx` files
> 5. Optionally include dependent DDL scripts (recommended — this gives SnowConvert AI more context for better conversion)

### Part 2: Run Conversion

**Execute** the standard SnowConvert AI conversion workflow.

**Explain the output structure:**
> SnowConvert AI separates your SSIS packages into two clean components:
> - **Control Flow Orchestration** → SQL scripts with Snowflake Tasks and stored procedures
> - **Data Flow Tasks** → Individual dbt projects per data flow component

```
output/
└── ETL/
    └── [Package_Name]/
        ├── Orchestration.sql          # Control flow → Tasks + Procedures
        ├── Data Flow Task 1/
        │   ├── dbt_project.yml
        │   ├── models/
        │   └── sources/
        └── [Additional Data Flow Tasks]/
```

**Review conversion reports:**
- `ETL.Elements.NA.csv` — Details about converted ETL elements
- `ETL.Issues.NA.csv` — Issues encountered during conversion

**CHECKPOINT:**
> Here's what SnowConvert AI produced from your [N] SSIS packages. Let me walk you through the conversion results before we deploy.

### Part 3: Deploy Converted Components

**Deploy Snowflake Tasks and stored procedures** from `Orchestration.sql`

**Deploy dbt projects:**
1. Set up dbt profiles for Snowflake connection
2. Run `dbt run` for each data flow project
3. Fix any failing components
4. Validate model compilation and execution

### Part 4: Validate ETL Logic

**Explain to user:**
> The most important step: making sure your converted ETL produces the same results as your SSIS packages.

1. Run converted pipelines with test data
2. Compare output against SSIS execution results
3. Document any behavioral differences
4. Fix and re-test as needed

**CHECKPOINT:**
> ETL validation complete. Here's the comparison of SSIS vs. Snowflake output for each pipeline. Does everything look correct?

## Estimation Reference

**Share with user for planning:**

> For context, here's what a typical SSIS re-platforming looks like:

**Sample Timeline (100 SSIS Jobs, team of 2-3 developers + 1 architect):**

| Phase | Duration | Key Activities |
|-------|----------|---------------|
| Assessment & Inventory | 1 week | SnowConvert AI analyzes all packages, T-SQL, metadata |
| Pipeline Design & Setup | 2 weeks | Architect designs ELT flow, reusable Snowpark procedures |
| Conversion & Remediation | 4-6 weeks | Rewrite high-risk logic into Dynamic Tables or dbt models |
| Integration & Testing (SIT) | 3-4 weeks | Full functional testing, data integrity checks |
| **Total** | **10-13 weeks** | Production-ready ELT platform |

**Complexity variability:**
- Simple packages (source-to-target copies): closer to 8 weeks
- Complex packages (dense T-SQL, error handling): could exceed 13 weeks

**Re-platforming strategy by component:**

| SSIS Component | Snowflake Target | Complexity |
|---------------|-----------------|-----------|
| Simple data flows | Dynamic Tables | Low-Medium |
| Complex flow-control | Snowpark Python Stored Procedures | High |
| Connection managers | Storage integrations / stages | Low |
| SQL tasks with T-SQL | Snowflake SQL tasks / procedures | Medium |
| Custom .NET code tasks | Snowpark Python UDFs | High |
| SSIS orchestration | Snowflake Tasks (DAGs) | Medium |

## Session Wrap-Up

**Present to user:**
> SSIS Re-platforming complete! Your [N] SSIS packages have been converted to:
> - [X] Snowflake Tasks for orchestration
> - [Y] stored procedures for control flow
> - [Z] dbt projects for data transformations
> - All validated against original SSIS output

## Best Practices
- Document SSIS package dependencies and custom components before starting
- Include all dependent DDL for better conversion quality
- Review `ETL.Elements.NA.csv` and `ETL.Issues.NA.csv` thoroughly
- Test each converted data flow independently before orchestrating

## Deliverables
- Snowflake Tasks + stored procedures (control flow)
- dbt projects (data flow)
- Conversion detail CSVs
- Validation comparison results
