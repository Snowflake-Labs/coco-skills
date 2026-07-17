# Amazon Redshift to Snowflake Reference

Based on Snowflake's official Amazon Redshift migration guide. Intended for solution architects, data engineers, program managers and Snowflake solution partners.

## Architecture Differences

Amazon Redshift is a **cluster-based, massively parallel processing (MPP)** data warehouse where compute and storage are tightly coupled within a cluster. Performance tuning relies on node selection, distribution styles, sort keys and ongoing maintenance. Scaling typically requires resizing or rebuilding clusters, which introduces operational overhead.

Snowflake is a **cloud services-based platform** where compute, storage and cloud services are fully decoupled. Compute is delivered via independent virtual warehouses that can scale up or out instantly. Storage is centralized, automatically optimized and shared across all compute. Platform services (metadata, optimization, security, governance) are fully managed.

| Area | Amazon Redshift | Snowflake |
|------|----------------|-----------|
| Architecture | Traditional MPP shared-nothing, cluster-based | Multi-cluster, shared-data with fully decoupled compute and storage |
| Scaling Model | Cluster resizing requires full data redistribution; cluster enters read-only mode for potentially hours | No data redistribution; compute scales independently and instantly |
| Storage/Compute Coupling | Fixed ratio; scaling one requires scaling both | Storage and compute scale independently based on workload demand |
| Compute Cost Model | Compute must remain running to access data; pay for idle compute unless data is unloaded and reloaded | Compute fully suspends without unloading data; true pay-for-use |
| Semi-Structured Data | Limited; JSON stored as strings, not optimized for large volumes; fields often extracted at load time | Native VARIANT supports JSON, Avro, XML with optimized storage and performance |
| Concurrency | Constrained by limited query slots managed through WLM queues | Elastic via separate or multi-cluster virtual warehouses without manual tuning |
| Performance Management | Manual tuning of distribution keys, sort keys and WLM configurations | Automatic data optimization and workload isolation; no distribution/sort key management |
| Security Management | Encryption and key management optional; customer-configured via AWS services | Always-on encryption with automatic key management and rotation |
| Metadata/File Management | Manual management of files, metadata and storage layout | Fully managed, transparent file and metadata management |
| Disaster Recovery | Single availability zone; depends on snapshots and customer-managed restore | Built-in multi-datacenter deployment managed by Snowflake |
| Operational Overhead | Significant ongoing administration and infrastructure management | No data warehouse management; platform services fully managed |

### Redshift Operational Constraints

Redshift is a tightly coupled, cluster-based system in which compute, storage and query coordination are bound to fixed infrastructure:

- **Leader node bottleneck**: Queries coordinated by a leader node that can become a bottleneck at scale
- **Slice-based distribution**: Compute nodes subdivided into slices, requiring careful data distribution to avoid skew
- **Manual tuning**: Performance depends heavily on manual selection of distribution styles and sort keys
- **VACUUM overhead**: Must be run incrementally to maintain performance; resource-intensive operations
- **Free space requirements**: Requires 20%+ free space (or up to 3x the largest table) for VACUUM and re-sorting
- **Snapshot-based recovery**: Backup/recovery relies on periodic snapshots with restores required for failover

Snowflake eliminates these constraints through decoupled compute, storage and services, enabling elastic scaling, automatic optimization and zero infrastructure management.

### Scalability, Concurrency and Cost Model

| Feature | Amazon Redshift | Snowflake | Value Proposition |
|---------|----------------|-----------|------------------|
| Scalability | Manual cluster resize; node-based | Instant elastic scaling of compute | Agility without downtime |
| Concurrency | Limited by cluster resources and WLM | Multi-cluster virtual warehouses | Predictable performance |
| Cost Model | Node-hour based | Pay-per-use (per-second compute) | Cost transparency and control |

**Cost governance:** While Snowflake enables elastic scaling, cost efficiency requires intentional governance. Establish workload-specific warehouse sizing standards, enable auto-suspend/auto-resume, and use resource monitors to prevent unplanned consumption.

### Important: Redshift ≠ PostgreSQL

Although Amazon Redshift is derived from PostgreSQL, it is **not fully compatible** with PostgreSQL semantics. Teams should not rely on PostgreSQL behavior alone to validate Redshift logic when planning a Snowflake migration.

## Migration Methodology (8 Phases)

| Phase | Focus |
|-------|-------|
| 1. Planning and Design | Scope, strategy, team, budget, test plan, automated assessment |
| 2. Environments and Security | Warehouse setup, RBAC hierarchy (IAM→RBAC), case sensitivity, environment separation |
| 3. Database Code Conversion | Automated SQL translation (SnowConvert AI 96%+ conversion rate), SQL refactoring, procedural rewrite |
| 4. Data Migration and Ingestion | UNLOAD→S3→COPY INTO, Snowpipe/Streams/Tasks for ongoing, legacy batch simplification |
| 5. Reporting and Analytics | Tool repointing, workload isolation, semantic/behavioral validation, access modernization |
| 6. Data Validation and Testing | Structural + behavioral validation, numeric precision, timestamp handling, NULL semantics |
| 7. Deployment | Parallel run, cutover, Redshift cluster decommissioning |
| 8. Optimize and Run | Warehouse sizing, clustering keys, resource monitors, zero maintenance advantage |

### Phase 1: Planning and Design

**Strategic goals:** This migration is not simply a platform swap — it is a strategic modernization initiative designed to support long-term scalability, analytics and AI readiness. Snowflake is optimized for OLAP workloads, semi-structured data, data sharing and AI/ML use cases.

**Common Redshift migration drivers:** Concurrency bottlenecks, cluster management overhead, scaling delays, maintenance requirements (VACUUM/ANALYZE), WLM tuning complexity.

**Automated assessment:** Run SnowConvert AI assessment first (free) to inventory Redshift objects, estimate conversion effort and identify potential incompatibilities early.

**Document the existing environment:**
- Databases, schemas, tables, views, materialized views
- Distribution styles (DISTKEY) and sort keys (SORTKEY)
- Data ingestion pipelines (COPY jobs, Glue, Airflow, Fivetran, custom scripts)
- Downstream consumers (QuickSight, Tableau, Power BI, custom applications)
- Security model (IAM roles, database users, schema privileges)
- Rationalize data and decommission unused objects

**Migration approach:**

| Approach | Description | Recommendation |
|----------|-------------|---------------|
| Lift and shift | Minimal change for speed | Fastest but retains Redshift-specific constructs |
| Lift and adjust | Remove Redshift constructs, adopt Snowflake best practices | **Recommended** for faster time-to-value while reducing technical debt |
| Modernize | Re-architect pipelines and models | Highest value but highest effort |

**Project logistics:**
- Prioritize datasets for early migration (quick wins)
- Define Dev/QA/Prod environments
- Identify migration team and responsibilities
- Establish timelines, budget and success criteria (e.g., Redshift cluster decommissioning date)

**Test plan:** Define repeatable, automated testing for schema validation, data reconciliation, transformation logic validation and performance benchmarking. Automation is critical to reducing risk and shortening parallel run duration.

### Phase 2: Environments and Security

**Security model shift (IAM → RBAC):**
- Redshift security is optional and heavily integrated with AWS IAM and security groups; encryption/key rotation require explicit configuration
- Snowflake security is always-on and fully managed: end-to-end encryption, automatic key rotation, native RBAC, built-in data masking and governance
- Redshift relies on AWS IAM integration and database-level permissions → Snowflake uses centralized, hierarchical RBAC
- Grant privileges to roles, not users; assign roles to users
- Integrate with enterprise IdPs (Okta, Entra ID) via SSO and SCIM

**Warehouse setup best practices:**
- Separate warehouses by environment (dev/QA/prod)
- Separate warehouses by workload (ELT, BI, ad hoc)
- Enable auto-suspend and auto-resume
- Use resource monitors for cost governance

**System catalog migration:**
- `pg_*` system tables have no direct equivalent in Snowflake
- Use `INFORMATION_SCHEMA` and `ACCOUNT_USAGE` views instead
- ACLs must be reinterpreted using Snowflake RBAC, not system catalogs

**Case sensitivity:**
- Redshift lowercases unquoted identifiers by default; Snowflake uppercases unquoted identifiers
- Quoted identifiers are case-sensitive in Snowflake
- **Recommendation:** Set `QUOTED_IDENTIFIERS_IGNORE_CASE = TRUE` to minimize BI tool compatibility issues
- Avoid quoted identifiers in Snowflake long-term; use the parameter during transition only

### Phase 3: Database Code Conversion

**SnowConvert AI achieves 96%+ average automated conversion rate** for supported SQL and DDL constructs, enabling teams to focus on targeted refactoring, testing and optimization rather than bulk code translation.

**Areas requiring manual review:**
- Timestamp and time zone semantics
- Numeric precision and rounding behavior
- Business logic embedded in stored procedures
- Performance optimization and warehouse sizing
- Validation of analytic and reporting workloads

**High-frequency Redshift SQL incompatibilities:**

**Identifier casing:** Redshift lowercases unquoted identifiers; Snowflake uppercases them. Queries referencing quoted lowercase identifiers may fail. Avoid quoted identifiers; use `QUOTED_IDENTIFIERS_IGNORE_CASE = TRUE` during transition.

**Date/timestamp arithmetic:** PostgreSQL-style timestamp arithmetic (`timestamp - timestamp`, `timestamp - interval`, `TRUNC(timestamp)`) is not supported. Use `DATEDIFF()`, `DATEADD()`, and explicit casting to DATE.

**Time zone handling:** Redshift implicitly assumes timestamps without time zones are UTC. Snowflake supports `TIMESTAMP_NTZ`, `TIMESTAMP_TZ`, and `TIMESTAMP_LTZ`. **Recommendation:** Normalize all ingested timestamps to UTC, store as `TIMESTAMP_NTZ`, perform localization in downstream BI tools.

**SQL function and casting differences:** Redshift allows flexible numeric parsing without explicit precision/scale. Snowflake requires explicit precision and scale when a format mask is used, and fails fast when the format does not exactly match the input string. SnowConvert AI flags these cases, but manual review is required for correctness, especially in financial datasets.

**Unsupported or changed SQL constructs:**

| Redshift Construct | Snowflake Equivalent |
|-------------------|---------------------|
| `SELECT INTO` | `CREATE TABLE AS SELECT` |
| `ALTER TABLE … APPEND` | `INSERT INTO … SELECT` |
| `REFRESH MATERIALIZED VIEW` | Not required (automatic in Snowflake) |
| `VARCHAR(MAX)` | `VARCHAR` (max length by default) |
| `IS TRUE / IS FALSE` | Boolean predicates (`NOT col`, `col`) |
| `IN TIMEZONE` | `CONVERT_TIMEZONE()` |
| `COALESCE(expr)` | Requires at least two arguments |

**Stored procedures:** Redshift PL/pgSQL must be rewritten using Snowflake Scripting (SQL), JavaScript procedures, or Snowpark Python.

### Phase 4: Data Migration and Ingestion

**Redshift data layout considerations:** Redshift data layouts are often optimized for cluster-based execution and may reflect historical VACUUM operations, fragmented sort order or skewed distribution. During migration:
- Distribution styles and sort keys should **not** be preserved
- Data extracted from Redshift may not be physically ordered
- Snowflake automatically optimizes data layout during ingestion
- No post-load maintenance (VACUUM/ANALYZE) required

**Initial data transfer (common approach):** UNLOAD from Redshift to S3 (PARQUET), then COPY INTO Snowflake. Also: SnowConvert AI data migration accelerators, external tables for staged validation.

**Modern ingestion patterns:**
- **Snowpipe**: Continuous ingestion from S3
- **Streams + Tasks**: CDC and orchestration
- **dbt**: Transformations with incremental materializations
- Legacy Redshift batch jobs can often be simplified or eliminated

### Phase 5: Reporting and Analytics

**Tool repointing:** Update JDBC/ODBC drivers, repoint BI tools and semantic layers, validate queries/dashboards/scheduled reports, verify authentication and RBAC.

**Workload isolation advantage:** In Redshift, reporting competes with batch processing for cluster resources via WLM queues. Snowflake eliminates this through dedicated virtual warehouses, multi-cluster warehouses for burst concurrency, and independent scaling.

**Semantic and behavioral differences to validate:**
- Case sensitivity of quoted identifiers
- Timestamp and time zone handling
- NULL behavior in aggregate functions
- Numeric precision and rounding differences
- Implicit casting differences in filters and joins
- Validate dashboards for visual parity, calculated fields and business KPIs (not just row counts)

**Performance:** Right-size warehouses for reporting concurrency, monitor with Query Profile, separate ad hoc from production dashboards, evaluate clustering keys only for very large fact tables.

**Access modernization:** Map Redshift IAM-based access to Snowflake RBAC, ensure roles align with functional reporting groups, validate row/column-level access controls, review dynamic data masking policies.

**Post-migration opportunities:** Direct querying of semi-structured data via VARIANT, secure data sharing, integration with AI/ML via Snowpark, consolidation of reporting/engineering/AI on a single platform.

### Phase 6: Data Validation and Testing

**Structural validation:** Row counts, aggregates, schema comparison.

**Behavioral validation (critical for Redshift):** Teams must validate behavioral equivalence beyond structure:
- Numeric precision validation for `TO_NUMBER`
- NULL-handling validation for `GREATEST`/`LEAST` (Snowflake returns NULL if any argument is NULL; Redshift returns non-NULL value)
- Timestamp/timezone validation (`TIMESTAMP_NTZ` vs `TIMESTAMPTZ`)
- Hash consistency checks when replacing `FNV_HASH` with `HASH()`
- BI/reporting layer validation (differences surface only in dashboards/visualizations)

**Validation methods:** Aggregate comparisons, hash-based validation, business metric validation, targeted query benchmarking. Automate wherever possible. Passing structural validation does not guarantee behavioral equivalence — business-critical queries must be validated directly.

### Phase 7: Deployment

**Parallel run:** Run Redshift and Snowflake simultaneously, validate pipelines and analytics, minimize overlap through automation.

**Cutover readiness checklist:**
- Final data reconciliation and validation complete
- BI tools and downstream consumers validated against Snowflake
- Ingestion and upstream writes to Redshift disabled
- Snowflake resource monitors and warehouse sizing controls enabled
- Redshift decommissioning plan reviewed and approved

**Cutover:** Disable Redshift ingestion → redirect consumers to Snowflake → decommission Redshift clusters.

### Phase 8: Optimize and Run

**Zero maintenance advantage:** Eliminate VACUUM, ANALYZE, distribution/sort key tuning.

**Performance and cost optimization:**
- Right-size warehouses (primary cost/performance lever)
- Use multi-cluster warehouses for concurrency
- Apply clustering keys for very large tables (>1TB) with frequent range filters
- Monitor with Query Profile and resource monitors

**Redshift migration lessons learned:**
- Do not migrate distribution styles or sort keys
- Rewrite timestamp arithmetic early in the project
- Normalize timestamps to UTC
- Avoid quoted identifiers
- Explicitly validate numeric precision and rounding
- Expect significantly reduced operational overhead post-migration

## Data Type Mapping

| Redshift | Snowflake | Notes |
|----------|-----------|-------|
| SMALLINT / INT2 | SMALLINT | |
| INTEGER / INT / INT4 | INTEGER | |
| BIGINT / INT8 | BIGINT | |
| DECIMAL(p,s) / NUMERIC(p,s) | NUMBER(p,s) | |
| REAL / FLOAT4 | FLOAT | Single-precision |
| DOUBLE PRECISION / FLOAT8 / FLOAT | FLOAT | Double-precision |
| BOOLEAN / BOOL | BOOLEAN | |
| CHAR(n) / CHARACTER(n) / NCHAR(n) / BPCHAR | CHAR(n) | |
| VARCHAR(n) / CHARACTER VARYING(n) / NVARCHAR(n) / TEXT | VARCHAR(n) | Redshift max 65535; Snowflake max 16MB |
| DATE | DATE | |
| TIMESTAMP / TIMESTAMP WITHOUT TIME ZONE | TIMESTAMP_NTZ | |
| TIMESTAMPTZ / TIMESTAMP WITH TIME ZONE | TIMESTAMP_TZ | |
| TIME / TIME WITHOUT TIME ZONE | TIME | |
| TIMETZ / TIME WITH TIME ZONE | TIME | Snowflake TIME does not store timezone; consider TIMESTAMP_TZ |
| SUPER | VARIANT | Semi-structured type |
| HLLSKETCH | Not direct | Use APPROX_COUNT_DISTINCT() |
| GEOMETRY | GEOMETRY | |
| GEOGRAPHY | GEOGRAPHY | |
| VARBYTE / VARBINARY / BINARY VARYING | VARBINARY | |

## Feature Mapping

| Redshift Feature | Snowflake Equivalent |
|-----------------|---------------------|
| DISTSTYLE EVEN/KEY/ALL | Not needed (Snowflake auto-distributes) |
| DISTKEY | Not needed |
| SORTKEY (compound) | CLUSTER BY (similar intent, automatic maintenance) |
| SORTKEY (interleaved) | CLUSTER BY (Snowflake handles multi-column pruning) |
| ENCODE compression | Not needed (Snowflake auto-compresses) |
| BACKUP YES/NO | Not applicable; remove |
| WLM (Workload Management) | Warehouses (multi-cluster, auto-scaling) |
| Concurrency scaling | Multi-cluster warehouse auto-scaling |
| Redshift Spectrum | External tables on S3/Azure/GCS |
| Late-binding views | Standard views (Snowflake views are always late-binding) |
| Materialized views | MATERIALIZED VIEW or Dynamic Tables |
| COPY from S3 | COPY INTO from S3 stage (via storage integration) |
| UNLOAD to S3 | COPY INTO @stage (to S3/Azure/GCS) |
| Stored procedures (PL/pgSQL) | Snowflake Scripting or JavaScript procedures |
| UDFs (SQL) | Snowflake UDFs (SQL, JavaScript, Python, Java) |
| UDFs (Python) | Snowflake Python UDFs |
| Lambda UDFs | External functions |
| Federated queries | External tables or data sharing |
| Data sharing (Redshift) | Snowflake Data Sharing (native, cross-account) |
| RA3 managed storage | Not applicable (Snowflake decouples natively) |
| Snapshot / backup | Time Travel + Fail-Safe |
| Row-level security | Row Access Policies |
| Column-level access | Column-level masking policies |
| Leader node functions | Not applicable; all functions run on compute |
| System tables (STL, STV, SVL, SVV) | INFORMATION_SCHEMA / ACCOUNT_USAGE views |
| VACUUM | Not needed (Snowflake auto-manages) |
| ANALYZE | Not needed (Snowflake auto-manages statistics) |
| Query monitoring rules | Resource monitors + query tag-based monitoring |
| Cross-database queries | Cross-database queries supported natively |

## Common Redshift to Snowflake Patterns

### COPY Command
```sql
-- Redshift
COPY my_table FROM 's3://mybucket/data/'
IAM_ROLE 'arn:aws:iam::123456789:role/MyRole'
FORMAT AS CSV
DELIMITER ','
IGNOREHEADER 1
DATEFORMAT 'auto'
TIMEFORMAT 'auto'
REGION 'us-west-2'
MAXERROR 100
BLANKSASNULL
EMPTYASNULL
ACCEPTINVCHARS;

-- Snowflake
CREATE OR REPLACE STAGE my_s3_stage
  URL = 's3://mybucket/data/'
  STORAGE_INTEGRATION = my_s3_integration;

COPY INTO my_table
  FROM @my_s3_stage
  FILE_FORMAT = (
    TYPE='CSV'
    SKIP_HEADER=1
    FIELD_DELIMITER=','
    EMPTY_FIELD_AS_NULL=TRUE
    NULL_IF=('NULL','')
    ERROR_ON_COLUMN_COUNT_MISMATCH=FALSE
  )
  ON_ERROR='CONTINUE';
```

### UNLOAD Command
```sql
-- Redshift
UNLOAD ('SELECT * FROM my_table')
TO 's3://mybucket/unload/'
IAM_ROLE 'arn:aws:iam::123456789:role/MyRole'
FORMAT AS PARQUET
ALLOWOVERWRITE
PARALLEL ON
MAXFILESIZE 256 MB;

-- Snowflake
COPY INTO @my_s3_stage/unload/
  FROM my_table
  FILE_FORMAT = (TYPE='PARQUET')
  MAX_FILE_SIZE = 268435456
  OVERWRITE = TRUE;
```

### JSON Handling (SUPER Type → VARIANT)
```sql
-- Redshift
SELECT JSON_EXTRACT_PATH_TEXT(json_col, 'key1', 'key2') FROM my_table;
SELECT JSON_EXTRACT_ARRAY_ELEMENT_TEXT(json_col, 0) FROM my_table;
SELECT json_col.key1.key2 FROM my_table;  -- PartiQL syntax (Redshift SUPER)

-- Snowflake (dot notation)
SELECT json_col:key1.key2::STRING FROM my_table;
SELECT json_col[0]::STRING FROM my_table;
-- or function-based
SELECT GET_PATH(json_col, 'key1.key2')::STRING FROM my_table;
```

### SUPER Type Querying → VARIANT + FLATTEN
```sql
-- Redshift (SUPER type with PartiQL)
SELECT c.customer_id, o.order_id
FROM customers c, c.orders o
WHERE o.amount > 100;

-- Snowflake (VARIANT + LATERAL FLATTEN)
SELECT c.customer_id, f.value:order_id::INT AS order_id
FROM customers c,
LATERAL FLATTEN(INPUT => c.orders) f
WHERE f.value:amount::NUMBER > 100;
```

### Identity Columns
```sql
-- Redshift
CREATE TABLE t (id INT IDENTITY(1,1), name VARCHAR(100));
-- Or: id BIGINT GENERATED BY DEFAULT AS IDENTITY(1,1)

-- Snowflake
CREATE TABLE t (id INT AUTOINCREMENT START 1 INCREMENT 1, name VARCHAR(100));
-- Or: id INT IDENTITY(1,1)  -- Snowflake also supports IDENTITY keyword
```

### Approximate Functions
```sql
-- Redshift
SELECT APPROXIMATE COUNT(DISTINCT user_id) FROM events;

-- Snowflake
SELECT APPROX_COUNT_DISTINCT(user_id) FROM events;
```

### Spectrum (External Tables)
```sql
-- Redshift Spectrum
CREATE EXTERNAL SCHEMA spectrum_schema
FROM DATA CATALOG
DATABASE 'mydb'
IAM_ROLE 'arn:aws:iam::123456789:role/MyRole';

SELECT * FROM spectrum_schema.external_table;

-- Snowflake
CREATE OR REPLACE EXTERNAL TABLE external_table
  WITH LOCATION = @my_s3_stage/path/
  FILE_FORMAT = (TYPE = 'PARQUET')
  AUTO_REFRESH = TRUE;

SELECT * FROM external_table;
```

### Window Functions with Default Frame
```sql
-- Both Redshift and Snowflake:
-- Default window frame with ORDER BY: ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
-- Default without ORDER BY: ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
-- Generally compatible, but verify edge cases with RANGE frames
```

### Stored Procedure (PL/pgSQL → Snowflake Scripting)
```sql
-- Redshift (PL/pgSQL)
CREATE OR REPLACE PROCEDURE update_status(p_id INTEGER, p_status VARCHAR)
LANGUAGE plpgsql
AS $$
DECLARE
  v_count INTEGER;
BEGIN
  SELECT COUNT(*) INTO v_count FROM orders WHERE id = p_id;
  IF v_count > 0 THEN
    UPDATE orders SET status = p_status WHERE id = p_id;
  ELSE
    RAISE EXCEPTION 'Order not found: %', p_id;
  END IF;
END;
$$;

-- Snowflake Scripting
CREATE OR REPLACE PROCEDURE update_status(p_id INTEGER, p_status VARCHAR)
  RETURNS VARCHAR
  LANGUAGE SQL
  EXECUTE AS CALLER
AS
BEGIN
  LET v_count INTEGER := (SELECT COUNT(*) FROM orders WHERE id = :p_id);
  IF (v_count > 0) THEN
    UPDATE orders SET status = :p_status WHERE id = :p_id;
    RETURN 'Updated';
  ELSE
    RETURN 'Order not found: ' || :p_id;
  END IF;
END;
```

### Date/Time Functions
```sql
-- Redshift                                -- Snowflake
GETDATE()                                  CURRENT_TIMESTAMP()
SYSDATE                                    CURRENT_TIMESTAMP()
DATE_TRUNC('month', dt)                    DATE_TRUNC('month', dt)  -- same
DATEADD(day, 7, dt)                        DATEADD('day', 7, dt)  -- quote the part
DATEDIFF(day, d1, d2)                      DATEDIFF('day', d1, d2)  -- quote the part
EXTRACT(year FROM dt)                      EXTRACT(year FROM dt)  -- same
TO_CHAR(dt, 'YYYY-MM-DD')                 TO_CHAR(dt, 'YYYY-MM-DD')  -- same
CONVERT_TIMEZONE('US/Eastern', ts)         CONVERT_TIMEZONE('US/Eastern', ts)  -- same
ADD_MONTHS(dt, 3)                          DATEADD('month', 3, dt)
LAST_DAY(dt)                               LAST_DAY(dt)  -- same
MONTHS_BETWEEN(d1, d2)                     DATEDIFF('month', d2, d1)
```

### String Functions
```sql
-- Redshift                                -- Snowflake
LEN(str)                                   LENGTH(str)
CHARINDEX(sub, str)                        CHARINDEX(sub, str)  -- same
POSITION(sub IN str)                       POSITION(sub IN str)  -- same
REPLACE(str, old, new)                     REPLACE(str, old, new)  -- same
CONCAT(a, b)                               CONCAT(a, b) or a || b  -- same
REGEXP_SUBSTR(str, pattern)                REGEXP_SUBSTR(str, pattern)  -- same
STRTOL(str, base)                          Custom UDF or TRY_TO_NUMBER with base conversion
LISTAGG(col, delim)                        LISTAGG(col, delim)  -- same
NVL(a, b)                                  NVL(a, b) or COALESCE(a, b)  -- same
NVL2(expr, val1, val2)                     NVL2(expr, val1, val2)  -- same
BTRIM(str)                                 TRIM(str)
ENCODE(col, 'base64')                      BASE64_ENCODE(col)
DECODE(col, 'base64')                      BASE64_DECODE_STRING(col)
```

### System/Admin Functions
```sql
-- Redshift                                -- Snowflake
PG_LAST_COPY_ID()                          LAST_QUERY_ID()
PG_LAST_COPY_COUNT()                       RESULT_SCAN(LAST_QUERY_ID())
SVL_QUERY_SUMMARY / STL_QUERY              ACCOUNT_USAGE.QUERY_HISTORY
SVV_TABLE_INFO                             INFORMATION_SCHEMA.TABLES
STV_BLOCKLIST                              Not applicable (auto-managed)
STV_TBL_PERM                               Not applicable
SVV_EXTERNAL_SCHEMAS                       SHOW EXTERNAL TABLES
PG_CATALOG tables                          INFORMATION_SCHEMA views
```

## DDL Conversion Checklist

1. **Remove** `DISTSTYLE` (EVEN/KEY/ALL), `DISTKEY(col)`
2. **Remove** `SORTKEY(col1, col2)` and `INTERLEAVED SORTKEY`; consider CLUSTER BY for large tables
3. **Remove** `ENCODE` compression directives (auto/bytedict/lzo/zstd/etc.)
4. **Remove** `BACKUP YES/NO`
5. **Convert** `IDENTITY(seed,step)` → `AUTOINCREMENT START seed INCREMENT step`
6. **Convert** `SUPER` → `VARIANT`
7. **Convert** `TIMETZ` → `TIME` or `TIMESTAMP_TZ` (assess timezone needs)
8. **Convert** `HLLSKETCH` → remove; use `APPROX_COUNT_DISTINCT()`
9. **Replace** `CREATE EXTERNAL SCHEMA` → external tables with stages
10. **Replace** PL/pgSQL procedures → Snowflake Scripting
11. **Remove** `VACUUM` and `ANALYZE` statements
12. **Note** constraints: PK, FK, UNIQUE defined but **not enforced** in Snowflake

## Migration via S3 (Recommended Path)

The most common Redshift → Snowflake migration path uses S3 as an intermediary:

1. **UNLOAD** from Redshift to PARQUET files in S3:
   ```sql
   UNLOAD ('SELECT * FROM schema.table')
   TO 's3://migration-bucket/table/'
   IAM_ROLE 'arn:aws:iam::123456789:role/RedshiftUnloadRole'
   FORMAT AS PARQUET
   ALLOWOVERWRITE;
   ```

2. **Create stage** in Snowflake pointing to S3:
   ```sql
   CREATE OR REPLACE STAGE migration_stage
     URL = 's3://migration-bucket/'
     STORAGE_INTEGRATION = my_s3_integration;
   ```

3. **COPY INTO** Snowflake:
   ```sql
   COPY INTO target_table
     FROM @migration_stage/table/
     FILE_FORMAT = (TYPE = 'PARQUET')
     MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;
   ```

**Requirements:**
- S3 bucket in same region as Redshift cluster (minimize transfer costs)
- IAM Role for Redshift: `s3:PutObject`, `s3:GetObject`, `s3:ListBucket`
- Storage integration or IAM User for Snowflake: `s3:GetObject`, `s3:ListBucket`

## Data Extraction Methods

| Method | Best For |
|--------|---------|
| UNLOAD to S3 (PARQUET) | Primary method; best performance, schema preservation |
| UNLOAD to S3 (CSV) | Legacy or simple tables |
| Redshift Data API | Programmatic extraction in small batches |
| Fivetran / Airbyte | Managed CDC replication |
| AWS DMS | Change data capture for ongoing replication |
| SnowConvert AI (Redshift) | Automated DDL/SQL conversion + S3-based data migration |

## Common Pitfalls

1. **Distribution/sort keys**: Simply remove; don't try to replicate distribution logic. Snowflake handles it automatically.
2. **VACUUM/ANALYZE**: Remove all maintenance commands; Snowflake auto-manages.
3. **Leader-node-only functions**: Some Redshift functions only run on the leader node; verify Snowflake equivalents exist.
4. **SUPER vs VARIANT**: PartiQL syntax (`table.array[0].field`) must be rewritten to Snowflake dot notation (`col:array[0].field`).
5. **Timestamp precision**: Redshift default TIMESTAMP is microseconds; Snowflake TIMESTAMP is nanoseconds. Verify comparisons.
6. **TIMETZ**: Snowflake TIME does not store timezone offset; use TIMESTAMP_TZ if timezone needed.
7. **Redshift-specific SQL**: Functions like `STRTOL()`, `APPROXIMATE COUNT(DISTINCT)` need rewriting.
8. **Spectrum tables**: Must be recreated as Snowflake external tables with proper stages.
9. **WLM queues**: Translate queue-based workload isolation to separate Snowflake warehouses.
10. **Case sensitivity**: Redshift lowercases unquoted identifiers; Snowflake uppercases them. Quoted identifiers are case-sensitive in Snowflake.
11. **Constraint enforcement**: Redshift enforces UNIQUE/PK on some node types; Snowflake never enforces. Move checks to ETL.
12. **GREATEST/LEAST NULL handling**: Snowflake returns NULL if any argument is NULL; Redshift returns the non-NULL value. Validate and apply COALESCE if required.
13. **Numeric precision**: Redshift allows flexible numeric parsing without explicit precision/scale; Snowflake requires explicit precision/scale with format masks and fails fast on mismatch.
14. **Timestamp arithmetic**: PostgreSQL-style `timestamp - timestamp` and `timestamp - interval` not supported; rewrite using DATEDIFF/DATEADD.
15. **FNV_HASH → HASH**: Hash functions produce different outputs; validate hash consistency post-conversion.
16. **pg_* system tables**: No direct equivalent; use INFORMATION_SCHEMA and ACCOUNT_USAGE views.

## High-Risk SQL Conversions

| Redshift Pattern | Snowflake Change | Risk | Mitigation |
|-----------------|-----------------|------|-----------|
| `TO_NUMBER(str, format)` | Add precision and scale | High | Silent truncation or runtime error; add explicit precision/scale |
| `SYSDATE` | `CURRENT_TIMESTAMP` | Low | Direct replacement; validate timestamp type in comparisons |
| `ARRAY_UPPER()` | `ARRAY_SIZE()` | Medium | Rewrite using ARRAY_SIZE |
| `ISNULL()` | `IFNULL()` | Low | Replace function name; validate boolean expressions |
| `FNV_HASH()` | `HASH()` | Medium | Validate hash consistency post-conversion |
| `GREATEST()`/`LEAST()` | Same, but NULL behavior differs | **High** | Snowflake returns NULL if any arg is NULL; validate and apply COALESCE |

## Appendix: Feature and SQL Mapping

### Architecture and Platform

| Redshift Feature | Snowflake Equivalent | Notes |
|-----------------|---------------------|-------|
| Cluster | Account + Virtual Warehouses | Decoupled compute |
| WLM | Multi-cluster Warehouses | Automatic concurrency |
| Spectrum | External Tables | Native support |

### Performance and Maintenance

| Redshift Feature | Snowflake Equivalent | Notes |
|-----------------|---------------------|-------|
| DISTKEY | N/A | Not required |
| SORTKEY | Clustering Keys (optional) | Use sparingly |
| VACUUM | N/A | Fully managed |
| ANALYZE | N/A | Automatic |

### Procedural Logic

| Redshift Feature | Snowflake Equivalent | Notes |
|-----------------|---------------------|-------|
| PL/pgSQL Procedures | Snowflake Scripting / JS / Python | Must be rewritten |
| Temporary Tables | Temporary Tables | Session-scoped |

## Professional Services and Partners

- **Snowflake Professional Services**: Accelerated Redshift migrations leveraging SnowConvert AI and migration accelerators. Convert Redshift SQL, refactor PL/pgSQL stored procedures, streamline data movement from S3 to Snowflake. Modernized architectures eliminating DISTKEY, SORTKEY, WLM queues and VACUUM/ANALYZE. Support from assessment through secure cutover and Redshift cluster decommissioning.
- **Global Solution Partners**: Code and pipeline conversion (Redshift SQL, materialized views, stored procedures, ETL/ELT → Snowflake-native patterns using dbt, Snowflake Scripting, Snowpark). Data engineering and AI/ML enablement. End-to-end delivery including validation, performance tuning, FinOps, governance and compliance.
- Contact: Snowflake account team or Snowflake Community
