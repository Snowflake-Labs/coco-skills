
# Workshop Session: SnowConvert AI — Automated Migration

## Session Overview

**Present to user:**
> Welcome to the **SnowConvert AI** session. This is Snowflake's free automated conversion tool — it handles the heavy lifting of translating your source code to Snowflake SQL. In partner engagements, SnowConvert AI typically achieves **95%+ automated conversion** and has converted over **2 billion lines of code** across thousands of migrations.
>
> Here's what we'll do together:
> 1. Make sure SnowConvert AI is set up and ready
> 2. Extract your source database objects
> 3. Run the automated conversion and review results
> 4. Optionally use AI Verification to auto-fix remaining issues
> 5. Deploy converted objects to Snowflake
>
> Let's check your setup first.

## Prerequisites
- SnowConvert AI installed (free from Snowsight → Ingestion/Migrations)
- Snowflake account with `CREATE MIGRATION` privilege
- Access to source database (read + DDL extraction permissions)
- MFA enabled on Snowflake account

## Session Flow

### Part 1: Verify Setup

**Ask the user** (via `ask_user_question`):
- Do you have SnowConvert AI installed?
- Which source platform? (Oracle, Teradata, SQL Server, Redshift)
- Direct database access, or will you provide DDL files?

**If not installed, guide them:**
> SnowConvert AI is completely free. You can download it from Snowsight → Ingestion/Migrations. It runs on Windows 11+, macOS 13.3+, or Linux, and needs 4GB RAM (8GB+ recommended). Access codes auto-generate since v1.2.0 — one code works for all source platforms.

**If using DDL files** (Oracle, Teradata):
> For platforms without direct extraction, you'll need to export your DDL to .sql files first. Use these export scripts: https://github.com/Snowflake-Labs/SC.DDLExportScripts

**Verify migration privileges:**
```sql
GRANT CREATE MIGRATION ON ACCOUNT TO ROLE <your_role>;
```

### Part 2: Create Project

**Walk the user through project creation:**

> Let's create your SnowConvert AI project:
> 1. Launch SnowConvert AI → **"Create New Project"**
> 2. Select your source platform
> 3. Choose the input folder containing your source code
> 4. Select an output folder for converted code
> 5. Enter your access code

*The `.snowct` project file saves everything — you can reopen it anytime to resume work.*

### Part 3: Extract Database Objects

**For SQL Server or Redshift** (direct extraction):
> SnowConvert AI can connect directly to your database and extract objects automatically.

1. Configure connection:
   - SQL Server: Standard auth or Windows Authentication
   - Redshift: IAM Provisioned Cluster, IAM Serverless, or Standard auth
2. Connect → browse schemas → select objects
3. Click **"Extract Objects"** → review results
4. Click **"View Last Extraction Results"** to validate

**For Oracle, Teradata, or other** (file-based):
> Place your exported `.sql` files in the input folder you specified. SnowConvert AI will read them automatically.

**Extractable objects:** Tables, Views, Functions, Stored Procedures, Materialized Views

### Part 4: Run Conversion

**Guide the user through conversion settings:**

> Before we run, let me explain the key settings:
> - **Encoding:** UTF-8 (default — leave this unless you have special characters)
> - **Custom Schema/Database:** Set these if your target names differ from source
> - **Target Language:** SnowScript (recommended) or JavaScript for procedures
> - **Comments:** Enable to annotate nodes with missing dependencies

**Execute:** Click **"Save & Start Assessment"**

**Review results together** using the traffic light system:

| Color | Meaning | What We Do |
|-------|---------|-----------|
| Green | Successfully converted | Ready to deploy as-is |
| Yellow (FDM) | Further Development Mandatory | I'll review the business impact — often deployable with documentation |
| Red (EWI) | Error with Impact | We need to fix these before deployment |

**Code Completeness:** If below 100%, some objects reference dependencies that weren't included. We may need to add more source files.

**Assessment reports generated:**
- Conversion summary statistics
- Object-by-object conversion status
- Complexity analysis and recommendations
- Migration effort estimates

**CHECKPOINT:**
> Here are your conversion results: [X]% converted automatically, [Y] objects green, [Z] yellow, [W] red. Let's review the red items — these need manual attention.

**For EWI errors:**
1. Examine the flagged code in SnowConvert AI or your IDE
2. Fix the converted source code manually
3. Unit test the corrected file
4. Re-run conversion if needed

### Part 5: AI Verification (Optional)

**Introduce to user:**
> SnowConvert AI has an AI Verification feature (currently in Public Preview) that can automatically test and fix conversion errors. AI agents execute your converted code in your Snowflake account and fix issues, grounded with tests over synthetic data.
>
> This is optional — you can skip it if you prefer to review manually.

**If user wants AI Verification:**
1. Select objects to verify (dependencies auto-selected)
2. Click **"VERIFY CODE"** — review disclaimers (AI executes in your Snowflake account via Cortex Complete)
3. Wait for verification (may take significant time for large codebases)
4. Review AI results: summary of fixes + per-object details ("SEE DETAILS")
5. Merge AI fixes with initial conversion (manual review required)

### Part 6: Deploy to Snowflake

**Guide the user through deployment:**

> Your code is ready to deploy. Let me walk you through the process.

**Pre-deployment checklist:**
- [ ] All EWI errors resolved
- [ ] FDM warnings reviewed for acceptability
- [ ] Only successfully converted objects selected
- [ ] Deployment dependencies considered

**Authenticate** to Snowflake:
- SSO Authentication (enterprise identity)
- Standard Authentication (username + password + MFA)
- Account format: `orgname-account-name`

**Configure target:**
```
Account: myorg-myaccount
Warehouse: MIGRATION_WH
Database: TARGET_DB
Schema: PUBLIC
Role: MIGRATION_ROLE
```

**Deployment executes automatically** in dependency order:
1. Databases → 2. Schemas → 3. Tables → 4. Views → 5. Functions → 6. Stored Procedures

**CHECKPOINT:**
> Deployment complete! [X] objects deployed successfully, [Y] failures. Let me help address any failures before we move on.

## Session Wrap-Up

**Present to user:**
> SnowConvert AI conversion complete! Here's what we accomplished:
> - [X] objects extracted from [source platform]
> - [Y]% automated conversion rate
> - [Z] objects deployed to Snowflake
> - [W] items requiring manual attention (documented)

## Next Steps

**Ask the user** what they need next:
- Load data into the new tables → read `data-migration/SKILL.md`
- Convert SSIS packages → read `ssis-replatform/SKILL.md`
- Repoint Power BI reports → read `powerbi-repointing/SKILL.md`
- Validate migrated data → read `data-migration/SKILL.md` (Part 5)

## Deliverables

- SnowConvert AI project file (.snowct)
- Converted Snowflake SQL in output folder
- Assessment reports (conversion stats, complexity, effort estimates)
- Deployed objects in target Snowflake environment
