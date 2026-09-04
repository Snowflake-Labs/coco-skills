
# Workshop Session: Schema Conversion (Day 3 — Database Conversion)

## Session Overview

**Present to user:**
> Welcome to **Schema Conversion** — this is Day 3 of the LiftOff framework: Database Conversion. We'll take your source DDL and translate it into Snowflake-ready code.
>
> Here's what we'll work through together:
> 1. Collect and categorize your source DDL
> 2. Map every data type to its Snowflake equivalent (I'll flag anything that needs your input)
> 3. Convert tables, views, sequences, and other objects
> 4. Assemble a deployment script in the correct dependency order
> 5. Validate everything compiles, then deploy when you're ready
>
> Let's get your DDL.

## Prerequisites
- Platform reference file read (from `<SKILL_DIRECTORY>/references/`)
- Source DDL or object definitions available
- `references/best-practices.md` read

## Session Flow

### Part 1: Collect Source DDL

**Ask the user** (via `ask_user_question`) how they'll provide their DDL:
- Paste DDL statements directly
- Provide file paths to `.sql` files
- Provide a database/schema name (if source is queryable)
- They already ran SnowConvert AI and have converted output to review

**Parse and categorize** by object type:
- CREATE TABLE, CREATE VIEW, CREATE INDEX, CREATE SEQUENCE
- CREATE PROCEDURE/FUNCTION
- ALTER TABLE (constraints)
- Other DDL

**Present to user:** *"I've found [N] objects: [X] tables, [Y] views, [Z] procedures... Let me start with the data type mapping."*

### Part 2: Data Type Mapping

**Explain to user:**
> This is one of the most important parts of schema conversion. Every source data type needs a Snowflake equivalent, and some mappings involve trade-offs I want you to be aware of.

**Extract** all data types from the source DDL and map each one using the platform reference. **Flag anything requiring a decision:**

| Consideration | What to Tell the User |
|--------------|----------------------|
| Precision loss | "This type has higher precision in [source] than Snowflake supports. Here's the impact..." |
| LOB types | "Large objects map to VARIANT or VARCHAR(16MB). If you have objects exceeding 16MB, we'll need a different approach." |
| Custom/UDT types | "Snowflake doesn't support user-defined types directly. I'll flatten these to native types." |
| Numeric scale | "Snowflake NUMBER supports 0-38 precision — your source uses [X], so we're good." |
| Timestamp zones | "You have three options: TIMESTAMP_TZ (with timezone), TIMESTAMP_NTZ (without), or TIMESTAMP_LTZ (local). Here's when to use each..." |

**Produce a Data Type Mapping Report:**

```
| Source Column | Source Type | Snowflake Type | Notes |
|--------------|-------------|---------------|-------|
```

**CHECKPOINT:** *"Here's your data type mapping. Please review — especially the items I've flagged. Any precision changes need your sign-off before I proceed."*

### Part 3: Convert Table DDL

**Convert each CREATE TABLE** applying:
- Mapped data types from Part 2
- Remove unsupported clauses (TABLESPACE, STORAGE, PARTITION BY range/list, ENGINE, DISTSTYLE, DISTKEY, SORTKEY, ENCODE, ON [filegroup], etc.)
- Convert identity/auto-increment to Snowflake AUTOINCREMENT or IDENTITY
- Preserve NOT NULL, DEFAULT, PRIMARY KEY, UNIQUE, CHECK, FOREIGN KEY constraints
- Add CLUSTER BY where beneficial (replacing source distribution/sort keys)
- Apply fully qualified naming: DB.SCHEMA.TABLE

**Important teaching moment to share:**
> One critical difference: Snowflake **defines but does not enforce** PK, FK, and UNIQUE constraints (only NOT NULL is enforced). This means your data integrity checks need to move into your ETL/ELT pipelines. I'll flag this in the deployment notes.

**Platform-specific conversions:**

**Oracle:**
- Remove TABLESPACE, STORAGE, PCTFREE
- VARCHAR2 → VARCHAR, DATE → TIMESTAMP_NTZ (Oracle DATE includes time)
- RAW/LONG RAW → BINARY, CLOB → VARCHAR(16777216), BLOB → BINARY(8388608)

**Teradata:**
- Remove PRIMARY INDEX, PARTITION BY, MULTISET/SET keywords, COMPRESS
- BYTEINT → TINYINT, character set conversions → VARCHAR

**Redshift:**
- Remove DISTSTYLE, DISTKEY, SORTKEY, ENCODE, BACKUP
- SUPER → VARIANT, IDENTITY(seed,step) → AUTOINCREMENT(seed,step)

**SQL Server:**
- Remove ON [filegroup], TEXTIMAGE_ON, CLUSTERED/NONCLUSTERED
- NVARCHAR → VARCHAR (Snowflake is native UTF-8), DATETIME/DATETIME2 → TIMESTAMP_NTZ
- UNIQUEIDENTIFIER → VARCHAR(36), BIT → BOOLEAN, MONEY → NUMBER(19,4)

### Part 4: Convert Views and Other Objects

**Views:**
- Apply query translation rules for the embedded SQL
- Materialized views → Snowflake MATERIALIZED VIEW or Dynamic Table
- Indexed views (SQL Server) → Snowflake MATERIALIZED VIEW
- Recursive views → Snowflake recursive CTE syntax
- Validate each view compiles: `snowflake_sql_execute` with `only_compile: true`

**Sequences:**
- Convert CREATE SEQUENCE syntax (Snowflake supports natively)
- Map START WITH, INCREMENT BY, CACHE

**Indexes:**
> "Snowflake doesn't use traditional indexes — its micro-partition pruning handles most use cases automatically. For large tables (>1TB) with frequent range filters, I'll recommend CLUSTER BY instead."

**Synonyms:**
- No direct equivalent; use fully qualified names or views as aliases

**File Formats & Stages:**
- Generate CREATE FILE FORMAT for expected data ingestion patterns
- Generate CREATE STAGE if external storage is involved

### Part 5: Deployment Script

**Assemble** all converted DDL in dependency order:

```sql
-- 1. Databases
-- 2. Schemas
-- 3. Sequences (referenced by tables)
-- 4. Tables (parent tables first, then child tables with FKs)
-- 5. Views (base views first, then dependent views)
-- 6. Functions
-- 7. Stored Procedures
-- 8. File Formats and Stages
```

**Validate** the full script compiles: `snowflake_sql_execute` with `only_compile: true`

**Present the Schema Conversion Summary:**
```
Schema Conversion Summary
- Tables converted: [count]
- Views converted: [count]
- Sequences converted: [count]
- Data type mappings: [count] ([flagged] requiring review)
- Warnings/manual review items: [count]
```

**Pre-deployment checklist:**
- [ ] All EWI errors resolved (if SnowConvert AI was used)
- [ ] FDM warnings reviewed and documented
- [ ] Converted code reviewed
- [ ] Test environment deployment tested first
- [ ] Rollback strategy planned

**CHECKPOINT:**
> "Your deployment script is ready. I can run it statement-by-statement (so we can catch any issues early) or as a batch. Which do you prefer?"

Wait for approval, then execute.

## Session Wrap-Up

**Present to user:**
> Schema Conversion complete! Here's what we accomplished:
> - [X] tables, [Y] views, [Z] sequences converted to Snowflake DDL
> - [N] data type mappings applied
> - All objects compiled and deployed successfully
>
> Your Snowflake database is now structurally ready for data.

## Next Session

If Full Workshop → proceed to **Data Migration** (read `data-migration/SKILL.md`)

## Workshop Context (Day 3)

During the LiftOff engagement, Database Conversion covers:
- Review DDL, DML, metadata, and scripts from the source platform
- Demo SnowConvert AI conversion capabilities
- Convert and deploy DDLs in the customer's Snowflake environment
- Estimate database conversion LOE and timeline

**Key estimation factors:** All database objects, one-time setup (code management, dev patterns), multiple environments, data type mapping, constraint enforcement differences, RBAC deployment
