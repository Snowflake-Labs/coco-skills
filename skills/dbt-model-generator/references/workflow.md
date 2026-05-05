# dbt Model Generator — Workflow Detail

Loaded when executing the generation workflow. Contains full SQL, dbt templates, decision heuristics, and troubleshooting for Steps 1–8.

---

### Step 1: Collect Parameters

**Ask** the user:
```
To generate dimensional models, I need:
1. Source database name?
2. Source schema (or * for all)?
3. Specific table (or all tables in schema)?
4. GitHub repo for the PR (owner/repo)?
```

If the user provided a database/table in their request, extract those values and only ask for missing parameters.

**Detect active Snowflake connection:** Run `cortex connections list` to get the current connection's account, warehouse, role, and database. Use these as defaults — don't ask the user to re-specify what CoCo already knows.

### Step 2: Discover & Profile Raw Tables

**Goal:** Understand every table's structure, row counts, column types, cardinality, and relationships.

**⚠️ Cost awareness:** Profiling all tables in a large schema can be expensive. For schemas with >20 tables, ask the user before proceeding. Use `SAMPLE` for tables with >1M rows. An XSMALL warehouse is sufficient for profiling.

**Actions:**

1. **List tables** in the source database/schema:
   ```sql
   SELECT table_schema, table_name, row_count, bytes
   FROM <SOURCE_DATABASE>.INFORMATION_SCHEMA.TABLES
   WHERE table_schema = '<SOURCE_SCHEMA>' AND table_type = 'BASE TABLE'
   ORDER BY table_schema, table_name
   ```
   If `<SOURCE_SCHEMA>` is `*`, use `table_schema NOT IN ('INFORMATION_SCHEMA')`.

2. **For each table**, get full column metadata:
   ```sql
   SELECT column_name, data_type, is_nullable, ordinal_position,
          character_maximum_length, numeric_precision, numeric_scale
   FROM <SOURCE_DATABASE>.INFORMATION_SCHEMA.COLUMNS
   WHERE table_schema = '<schema>' AND table_name = '<table>'
   ORDER BY ordinal_position
   ```

3. **Statistical profiling** per table — do NOT use `SELECT * LIMIT N` (too weak for understanding distributions):
   ```sql
   -- For tables ≤1M rows, scan directly. For >1M rows, use SAMPLE.
   SELECT
     '<col>' AS column_name,
     COUNT(*) AS total_rows,
     COUNT("<col>") AS non_null_count,
     ROUND(COUNT_IF("<col>" IS NULL) * 100.0 / COUNT(*), 1) AS null_pct,
     APPROX_COUNT_DISTINCT("<col>") AS approx_distinct,
     MIN("<col>")::VARCHAR AS min_val,
     MAX("<col>")::VARCHAR AS max_val
   FROM <SOURCE_DATABASE>.<schema>.<table>
   -- Add: SAMPLE (100000 ROWS) for tables with row_count > 1000000
   ```
   Run one query per table with a UNION ALL across all columns, or use a loop. Also sample 5 rows for visual inspection:
   ```sql
   SELECT * FROM <SOURCE_DATABASE>.<schema>.<table> TABLESAMPLE (5 ROWS)
   ```

4. **Check for existing semantic views, tags, or comments** on the tables for additional context.

5. **Identify relationships** across tables by matching column names (e.g., `*_ID` columns that appear in multiple tables).

**Output:** A profiling summary per table: column list, types, cardinality, null rates, candidate keys, foreign key relationships, estimated scan cost.

### Step 3: Recommend Modeling Pattern

**Goal:** Based on profiling results, recommend the right modeling pattern — not every dataset needs a star schema.

**Decision heuristics:**

| Signal | Recommended Pattern |
|---|---|
| 1-2 source tables, no clear grain differences | **OBT (One Big Table)** — single wide denormalized model |
| 1-3 tables, <100K rows total, simple analytics use case | **Wide denormalized table** — join + flatten in staging |
| 3+ tables with distinct grains, shared dimensions, >100K rows | **Star schema** — facts + dimensions |
| User explicitly requests a pattern | **Whatever the user asked for** |

**Present recommendation to user:**
```
Based on profiling:
- [N] source tables, [total rows] total rows
- [describe key relationships found]

Recommended pattern: [Star Schema / OBT / Wide Table]
Reason: [explain why]

If Star Schema:
  Facts: [list with grain]
  Dimensions: [list with keys and attributes]
  Measures: [list]

  [Star schema ASCII diagram]

Classification rationale for each column...
Data quality warnings: [any issues found]

Do you approve this model? (Yes / Modify / Switch to [alternative pattern])
```

**⚠️ MANDATORY STOPPING POINT**: Do NOT proceed until user approves the pattern and classification.

**Column classification rules (for star schema):**

| Signal | Classification |
|---|---|
| Numeric columns with high cardinality (amounts, quantities, scores) | **Measure** (fact) |
| `*_ID` columns with low-to-medium cardinality | **Foreign Key** to dimension |
| `*_ID` column that is unique per row | **Primary Key / Natural Key** |
| Categorical text with low cardinality | **Dimension attribute** |
| Timestamps / dates | **Date dimension FK** or degenerate dimension |
| Free text (long VARCHAR, reviews, descriptions) | **Degenerate dimension** |
| String-encoded structured data (e.g., `"[2, 3]"`) | **Needs parsing** — note transformation needed |

### Step 4: Generate dbt Project Scaffold

**Goal:** Create a complete dbt project if one doesn't already exist, or integrate into an existing one.

**Actions:**

1. **Detect existing project:** Check if `<PROJECT_PATH>/dbt_project.yml` exists.
   - **If exists:** Read it. Inspect directory structure (`models/` layout). Read existing `sources.yml` if present. Adapt generated models to match existing conventions.
   - **If not exists:** Create a new scaffold (step 2 below).

2. **For new projects**, create directory structure:
   ```
   <PROJECT_PATH>/
   ├── dbt_project.yml
   ├── profiles.yml
   ├── packages.yml
   ├── .gitignore
   ├── models/
   │   ├── sources.yml
   │   ├── staging/
   │   ├── dimensions/
   │   └── facts/
   ```

3. **dbt_project.yml**: Configure model materializations:
   - `staging/` → `materialized: view`
   - `dimensions/` → `materialized: table`
   - `facts/` → `materialized: table`

4. **profiles.yml**: Generate from the **active CoCo Snowflake connection**:
   ```yaml
   <PROJECT_NAME>:
     target: dev
     outputs:
       dev:
         type: snowflake
         account: "{{ env_var('SNOWFLAKE_ACCOUNT') }}"
         user: "{{ env_var('SNOWFLAKE_USER') }}"
         authenticator: externalbrowser  # interactive-only — breaks in CI/CD. For non-interactive use, set authenticator: snowflake with username/password, or use the Snowflake CLI connection instead.
         warehouse: <WAREHOUSE>
         database: <SOURCE_DATABASE>
         schema: <TARGET_SCHEMA>
         role: <ROLE>
         threads: 4
   ```
   Use `env_var()` for account/user — never hardcode credentials.

5. **packages.yml**: Include dbt_utils with version pin:
   ```yaml
   packages:
     - package: dbt-labs/dbt_utils
       version: [">=1.0.0", "<2.0.0"]
   ```
   Note: dbt-core 1.9+ includes `dbt.generate_surrogate_key()` natively. Prefer the built-in over dbt_utils if on 1.9+.

6. **sources.yml**: Define all source tables discovered in Step 2. If an existing `sources.yml` exists, **merge** — append new sources without overwriting existing entries.

7. **.gitignore**: Include `target/`, `dbt_packages/`, `logs/`, `.user.yml`, `profiles.yml`.

### Step 5: Generate Models

**Naming convention:** `stg_<source_table>`, `dim_<entity>`, `fact_<business_process>`, `obt_<domain>`

#### 5a: Staging Models (`stg_*.sql`)

One staging model per source table. Each must:
- Reference source via `{{ source('...', '...') }}`
- Rename columns to snake_case
- Cast types (string-encoded numbers, dates)
- Parse structured strings (e.g., `"[x, y]"` → separate integer columns)
- Convert unix timestamps to `TIMESTAMP` / `DATE`
- Derive useful computed columns

#### 5b: Dimension Models (`dim_*.sql`) — Star Schema only

One model per identified dimension. Each must:
- Use `{{ config(materialized='table') }}`
- Generate surrogate key: `{{ dbt_utils.generate_surrogate_key(['natural_key']) }}` (or `{{ dbt.generate_surrogate_key(['natural_key']) }}` on dbt 1.9+)
- Reference staging models via `{{ ref('stg_xxx') }}` — **never raw table names**
- Select distinct dimension members from staging
- For Type 1 SCD: use `ROW_NUMBER()` ordered by most recent timestamp
- Include a `dim_date` model as a date spine with: `full_date`, `year`, `quarter`, `month`, `month_name`, `day_of_month`, `day_of_week`, `day_name`, `week_of_year`, `is_weekend`

#### 5c: Fact Models (`fact_*.sql`) — Star Schema only

One model per identified fact table. Each must:
- Use `{{ config(materialized='table') }}`
- Generate surrogate key for the fact row
- Join to all dimension models using `{{ ref('dim_xxx') }}` — **all inter-model references MUST use `{{ ref() }}`**
- Include all measures from staging
- Include degenerate dimensions (long text kept on fact, not in a dim)

#### 5d: OBT Model (`obt_*.sql`) — OBT pattern only

Single wide model that:
- Joins all staging models via `{{ ref('stg_xxx') }}`
- Flattens all columns into one wide table
- Uses `{{ config(materialized='table') }}`
- Adds surrogate key for the primary grain

### Step 6: Generate Schema Tests & Docs

For every model, create a `schema.yml` with:

**Tests:**
- `unique` + `not_null` on all surrogate keys and natural keys
- `relationships` tests between fact FKs and dimension PKs
- `accepted_values` on categorical/enum columns where the domain is known
- `not_null` on measures

**Documentation:**
- Column-level `description` for every column in every model

### Step 7: Validate

**Actions:**

1. **Detect dbt runner** — use the first available:
   ```
   uv run --with 'dbt-snowflake>=1.7,<2.0'  →  preferred
   pipx run --spec 'dbt-snowflake>=1.7,<2.0' dbt  →  fallback
   dbt (if globally installed and version ≥1.7)  →  last resort
   ```
   Verify version: `dbt --version` must show ≥1.7.

2. Install packages:
   ```bash
   <runner> dbt deps --project-dir <PROJECT_PATH> --profiles-dir <PROJECT_PATH>
   ```

3. Parse/compile the project:
   ```bash
   <runner> dbt parse --project-dir <PROJECT_PATH> --profiles-dir <PROJECT_PATH>
   ```

4. **If parse fails:** Fix errors and retry (max 3 attempts).

5. Report results:
   ```
   ✅ dbt parse: X models, Y tests, Z sources — 0 errors
   ```

6. **If Snowflake connection is available**, also run:
   ```bash
   <runner> dbt run --project-dir <PROJECT_PATH> --profiles-dir <PROJECT_PATH>
   <runner> dbt test --project-dir <PROJECT_PATH> --profiles-dir <PROJECT_PATH>
   ```

**⚠️ MANDATORY STOPPING POINT**: Present all generated models to the user for review before proceeding to Git/PR.

### Step 8: Git Commit & PR

**Actions:**

1. **Detect git state:**
   - If `<PROJECT_PATH>/.git` exists: check for conflicts or uncommitted changes.
   - If no `.git`: run `git init`.
   - Check if remote `origin` exists. Only add if missing:
     ```bash
     git remote add origin https://github.com/<GITHUB_REPO>.git
     ```
   - If remote exists but points to a different repo, warn the user and ask before changing it.

2. **Create `.gitignore`** if it doesn't exist (exclude `target/`, `dbt_packages/`, `logs/`, `.user.yml`, `profiles.yml`).

3. **Create feature branch** per source table:
   - Branch name: `feature/dim-model-<source_table_lowercase>`
   - If branch already exists, append a timestamp suffix.

4. **Commit** with descriptive message listing all models, measures, and dimensions.

5. **Push** branch and **create PR** via `gh pr create` with body containing:
   - Summary of models generated
   - Star schema ASCII diagram (or OBT column list)
   - Data profiling stats (row counts, cardinality)
   - Classification rationale (why each column was assigned its role)
   - Data quality warnings
   - Test plan checklist

**PR body template:**
```markdown
## Summary
- AI-generated [star schema / OBT / wide table] from `<SOURCE_DATABASE>.<SOURCE_SCHEMA>.<SOURCE_TABLE>`
- [N] staging models, [N] dimensions, [N] fact tables, [N] schema tests

## Model Diagram
[ASCII diagram or column list]

## Data Profiling
| Table | Rows | Columns | Null % (max) | Candidate Keys |
|---|---|---|---|---|

## Column Classification Rationale
| Column | Classification | Reason |
|---|---|---|

## Data Quality Warnings
- [any issues found]

## Test Plan
- [ ] `dbt deps`
- [ ] `dbt compile`
- [ ] `dbt run`
- [ ] `dbt test`
- [ ] Spot-check joins and transformations
```

---

## Troubleshooting

| Issue | Solution |
|---|---|
| `dbt parse` fails | Check column name quoting — mixed-case columns from Iceberg/Parquet need double quotes (e.g., `"reviewText"`) |
| Snowflake connection fails during `dbt run` | Verify `profiles.yml` auth. Use `dbt parse` for offline validation |
| `gh pr create` permission denied | Token needs `Contents (R&W)` and `Pull requests (R&W)` fine-grained permissions |
| String-encoded data (e.g., `"[2, 3]"`) | Use `TRY_CAST` + `REGEXP_SUBSTR` in staging model for safe parsing |
| `uv` not installed | Fall back to `pipx run --spec 'dbt-snowflake>=1.7' dbt` or global `dbt` |
| Existing project has different directory layout | Read `dbt_project.yml` to detect `model-paths` and folder conventions; adapt accordingly |
| Profiling is slow on large tables | Add `SAMPLE (100000 ROWS)` clause; use XSMALL warehouse for profiling |
| Remote already configured to different repo | Warn user; don't silently overwrite. Ask before changing `origin` |
