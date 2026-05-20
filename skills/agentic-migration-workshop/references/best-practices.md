# Migration Best Practices

Guidance from Snowflake's official migration guides, SnowConvert AI quickstart, and partner migration workshops.

## Prerequisites and Permissions

**Snowflake account setup:**
```sql
GRANT CREATE MIGRATION ON ACCOUNT TO ROLE <migration_role>;
GRANT USAGE ON WAREHOUSE migration_wh TO ROLE migration_role;
GRANT CREATE DATABASE ON ACCOUNT TO ROLE migration_role;
GRANT CREATE TABLE ON SCHEMA target_db.public TO ROLE migration_role;
GRANT INSERT, SELECT ON ALL TABLES IN SCHEMA target_db.public TO ROLE migration_role;
```

**Source database requirements:**
- Read access to all objects in migration scope
- Ability to extract DDL code for those objects
- For direct extraction (SQL Server, Redshift): network connectivity and auth credentials

## Planning Phase

### Migration Approach Selection

| Approach | When to Use | Risk | Speed |
|----------|------------|------|-------|
| Lift and shift | Minimize changes; fast migration | Lower | Faster |
| Re-architecture | Modernize data models, ETL, procedural logic | Higher | Slower |
| Phased/hybrid | Start with lift-and-shift, optimize post-migration | Medium | Medium |

### Scope Definition

1. **Inventory** all source objects using catalog queries or DDL export scripts
2. **Triage** by usage: remove obsolete, unused, and temporary objects
3. **Classify** by business impact and technical complexity
4. **Prioritize**: Start with high-impact, low-complexity workloads to build momentum
5. **Exclude** system databases (SQL Server: master/msdb/tempdb/model; Teradata: DBC/Sys_Calendar/etc.)

### Team and Governance

- Establish a RACI matrix (Responsible, Accountable, Consulted, Informed)
- Roles: Project Manager, Data Engineer, Source DBA, Snowflake Architect, Security Admin, Business Analyst
- Coordinate with finance early: Snowflake is consumption-based pricing
- Use Snowflake object tagging for cost attribution by department/project

## DDL Extraction

**Direct extraction supported:** SQL Server, Amazon Redshift (via SnowConvert AI)

**File-based extraction (all platforms):**
- Use DDL export scripts: https://github.com/Snowflake-Labs/SC.DDLExportScripts
- Export to `.sql` files organized in logical folder structures
- SnowConvert extraction scripts per platform:
  - Oracle: https://docs.snowconvert.com/sc/general/getting-started/code-extraction/oracle
  - Teradata: https://docs.snowconvert.com/sc/general/getting-started/code-extraction/teradata
  - SQL Server: Direct extraction via SnowConvert AI
  - Redshift: Direct extraction via SnowConvert AI

## Code Conversion

### Status Indicators

| Status | Meaning | Action |
|--------|---------|--------|
| Green | Successfully converted | Ready for deployment |
| Yellow / FDM | Further Development Mandatory | Review for business impact; may deploy with documentation |
| Red / EWI | Error with Impact | MUST resolve manually before deployment |

### Code Completeness
- Score below 100% = missing object references in conversion
- Address missing references by including all dependent objects

### Code Preparation Best Practices
- Clean up source code before conversion (remove commented-out legacy code)
- Ensure consistent encoding across all files (UTF-8 recommended)
- Document complex business logic before conversion
- Organize source code in logical folder structures
- Maintain backup copies of original source code

### Key Constraint Difference (Critical!)
**Source platforms** (Oracle, SQL Server, Teradata, Redshift) enforce PK, FK, UNIQUE constraints.
**Snowflake** defines but does **NOT enforce** PK, FK, UNIQUE constraints (only NOT NULL is enforced).
**Action:** Move all data integrity checks into ETL/ELT pipelines.

## Deployment

### Dependency Order
Objects must deploy in this sequence:
1. Databases
2. Schemas
3. Sequences
4. Tables (parent tables first, then child tables with FKs)
5. Views (base views first, then dependent views)
6. Functions
7. Stored Procedures
8. File Formats and Stages

### Pre-Deployment Checklist
- [ ] All EWI errors resolved
- [ ] FDM warnings reviewed and documented
- [ ] Converted code reviewed in IDE
- [ ] Test environment deployment tested first
- [ ] Rollback strategy planned
- [ ] All permissions and roles configured
- [ ] Service accounts created (not using username/password auth)

## Environment Strategy

### Multi-Account Setup (Recommended for Enterprise)
| Account | Purpose | Security Level |
|---------|---------|---------------|
| Production | Production data and workloads | Strictest controls |
| Development/QA | Development and testing | Moderate; migration team has more freedom |
| Sandbox (optional) | Experimental work | Relaxed; still maintain basic security |

### SQL Server Environment Naming Convention
Best practice: separate databases by environment, not schemas.

| Object Type | Naming Pattern | Example |
|-------------|---------------|--------|
| Database | `[ENVIRONMENT]_[DATABASE]` | `DEV_SALES_DB`, `QA_SALES_DB`, `PROD_SALES_DB` |
| Schema | Mirror source schema names | `PROD_SALES_DB.dbo_schema` |
| Warehouse | `[FUNCTION]_WH_[ENVIRONMENT]` | `ANALYTICS_WH_DEV`, `ETL_WH_PROD` |
| Role | `[FUNCTION]_ROLE_[ENVIRONMENT]` | `DATA_ENGINEER_ROLE_PROD`, `ANALYST_ROLE_QA` |

### Warehouse Strategy for Migration
| Warehouse | Purpose | Sizing |
|-----------|---------|--------|
| WH_MIGRATION_LOAD | Initial data load | Large/X-Large (scale down after) |
| WH_MIGRATION_VALIDATE | Data validation queries | Medium |
| WH_TRANSFORM | ETL/ELT transformations | Medium (adjust based on workload) |
| WH_BI_ANALYTICS | BI tool queries (post-migration) | Small-Medium (auto-scaling) |

**Auto-suspend:** Set to 60 seconds on all warehouses to avoid paying for idle compute.

## Data Migration

### Platform-Specific Strategies

**Redshift → Snowflake (via S3):**
1. Unload from Redshift to PARQUET files in S3
2. Create Snowflake external stage pointing to S3
3. COPY INTO Snowflake tables from stage
4. Automatic cleanup of temporary files
- S3 bucket must be in same region as Redshift cluster
- Requires IAM Role for Redshift (s3:PutObject, GetObject, ListBucket)
- Requires storage integration or IAM User for Snowflake (s3:GetObject, ListBucket)

**SQL Server → Snowflake (direct streaming via SnowConvert AI):**
1. Bulk data extraction from SQL Server via BCP or direct streaming
2. Transfer to cloud storage stage or direct streaming to Snowflake
3. Real-time progress monitoring

**Oracle → Snowflake:**
1. Extract via Data Pump (expdp), SQL*Plus spool, or UTL_FILE to CSV/Parquet
2. Upload to cloud storage (S3/Azure Blob/GCS)
3. COPY INTO from external stage

**Teradata → Snowflake:**
1. Extract via BTEQ .EXPORT, FastExport, or TPT
2. Upload to cloud storage
3. COPY INTO from external stage

### General Data Load Best Practices
- Split large files into 100-250MB chunks for maximum parallelism
- Migrate large tables during off-peak hours
- Use parallel migration for multiple small tables
- Use a dedicated, larger warehouse for initial bulk load; scale down after
- Monitor network bandwidth utilization
- Consider table partitioning for very large datasets
- Implement retry logic for transient failures
- Validate partial migrations before continuing
- Use PARQUET format when possible (preserves schema, better compression)

### Incremental Data Migration
After historical data load, set up ongoing replication:
- Use source platform CDC (e.g., SQL Server CDC, Oracle LogMiner)
- Land changes in cloud storage → Snowpipe for automatic ingestion
- Apply changes to target with MERGE statement
- For complex dependencies, use Streams + Tasks pattern

## Data Validation

### Multi-Layered Validation Strategy

**Level 1 — File/Object Validation:**
- Verify file checksums/hashes after transfer to cloud storage
- Confirm file counts match expected

**Level 2 — Schema Validation:**
- Table names match exactly
- Column names preserved correctly
- Ordinal positions maintained
- Data types converted appropriately
- Character lengths preserved
- Numeric precision and scale maintained

**Level 3 — Reconciliation (Aggregate Validation):**
- Row counts match between source and target
- MIN, MAX, AVG, SUM values per numeric column
- NULL value counts
- DISTINCT value counts

**Level 4 — Cell-Level Validation (Data Diff):**
- For critical tables: cell-by-cell comparison of statistically significant sample
- Compare specific PKs with source data
- Standard deviation and variance checks
- **MD5 hash comparison (SQL Server recommended):** Create MD5 hash across key columns in SQL Server; generate corresponding hash in Snowflake; compare hashes. SnowConvert AI data migration feature automates this.
- **Redshift behavioral validation:** Validate `GREATEST`/`LEAST` NULL handling (Snowflake returns NULL if any arg is NULL; Redshift returns non-NULL), numeric precision with `TO_NUMBER`, timestamp/timezone differences (`TIMESTAMP_NTZ` vs `TIMESTAMPTZ`), hash consistency when replacing `FNV_HASH` with `HASH()`

**Critical platform differences affecting validation:**
- Collation behavior (SQL Server case-insensitive vs Snowflake case-sensitive; Redshift lowercases vs Snowflake uppercases)
- Floating point arithmetic differences between platforms
- Date/time precision (SQL Server DATETIME 3.33ms → Snowflake TIMESTAMP_NTZ nanosecond; Redshift microsecond → Snowflake nanosecond)
- Business users must understand these for UAT sign-off
- **Redshift-specific:** Passing structural and aggregate validation does not guarantee behavioral equivalence; business-critical queries should always be validated directly

**Level 5 — Business Logic Validation:**
- Run key business reports against both source and target
- Compare aggregated outputs
- Custom business metrics (e.g., total revenue, customer counts)

### Validation Result Levels

| Level | Meaning | Action |
|-------|---------|--------|
| Pass | Values match exactly | No action needed |
| Warning | Minor differences (e.g., higher precision) | Reconcile: apply transformation or change ingestion |
| Fail | Values don't match | Investigation required |

### Common Validation Issues

| Issue | Cause | Resolution |
|-------|-------|------------|
| Row count mismatch | Incomplete migration | Re-run data migration for affected tables |
| Precision differences | Data type conversion | Verify acceptable business impact |
| Date format variations | Timezone or format changes | Standardize date handling |
| Null handling differences | Platform-specific null behavior | Update conversion rules |
| Empty string vs NULL | Oracle treats '' as NULL; others don't | Add explicit null handling in ETL |
| Case differences | Collation changes | Normalize with UPPER/LOWER or COLLATE |

### Pre-Validation Checklist
- [ ] Ensure data stability during validation (no concurrent updates)
- [ ] Complete all migration steps before validation
- [ ] Have sufficient system resources available
- [ ] Plan validation during maintenance windows

## Testing Strategy

### Test Types

| Test | When | What |
|------|------|------|
| Functional | After code conversion | All migrated applications and functionalities work as expected |
| Integration | After data load | Migrated components work together, data flows between systems |
| SIT (System Integration) | After integration | Full system behavior validated across all integrated systems |
| Performance | After data load | Query performance, data loading speed, system responsiveness |
| Load & Stress | Before cutover | System handles expected peak concurrency and auto-scaling |
| Security | Before cutover | RBAC, data masking, row access policies, SSO/MFA all validated |
| Regression | After each phase | Previously working features still work |
| UAT (User Acceptance) | Before cutover | Business users validate reports and daily tasks |
| Parallel Run | Before cutover | Both systems running simultaneously, outputs compared |

### Performance Benchmarking
1. **Capture** baseline query set from source platform (top N queries by frequency/cost)
2. **Execute** same queries against Snowflake; compare runtimes
3. **Use** Query Profile tool in Snowflake to analyze slow queries
4. **Right-size** warehouses based on workload patterns
5. **Add** CLUSTER BY for large tables (>1TB) with frequent range filters

### Query Performance Optimization
```sql
-- Check query profile
SELECT * FROM TABLE(GET_QUERY_OPERATOR_STATS(LAST_QUERY_ID()));

-- Review query history for slow queries
SELECT query_id, query_text, execution_time, warehouse_size
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE start_time > DATEADD('day', -7, CURRENT_TIMESTAMP())
ORDER BY execution_time DESC
LIMIT 20;
```

## Cutover Strategy

### Approaches

| Strategy | Risk | Downtime | Complexity |
|----------|------|----------|-----------|
| Big Bang | High | Short (planned window) | Lower |
| Phased Rollout | Low | None (per component) | Higher |
| Parallel Run | Lowest | None | Highest (run both systems) |

### Phased Rollout (Recommended)
1. Migrate applications/reports one at a time
2. Implement bridging strategy so users don't query both systems
3. Validate each component before proceeding to next
4. Data synchronization for non-migrated applications happens behind the scenes

### SQL Server Specific Cutover
- Run SQL Server and Snowflake simultaneously during parallel run
- High confidence from automated testing allows minimal parallel run window
- Cutover only after: initial data migrated, processes keep data current, all testing complete, all tools redirected
- **Cutover action:** Turn off SQL Server data processes, revoke user/tool access
- **Define cutover plan early** — lack of clarity creates parallel environment overhead

### Amazon Redshift Specific Cutover
- Run Redshift and Snowflake in parallel; validate pipelines and analytics
- Minimize overlap duration through automated testing
- **Cutover sequence:** Disable Redshift ingestion → redirect consumers to Snowflake → decommission Redshift clusters
- **Cutover readiness:** Final data reconciliation complete, BI tools validated, upstream writes disabled, resource monitors enabled, decommissioning plan reviewed

### Cutover Checklist
- [ ] All stakeholders aligned and signed off
- [ ] All permissions and roles configured in Snowflake
- [ ] Service accounts created and tested
- [ ] Active Directory / SSO roles configured
- [ ] Final incremental data sync completed
- [ ] All ETL pipelines pointing to Snowflake
- [ ] BI tools repointed and validated
- [ ] Rollback plan documented and tested
- [ ] Legacy platform set to read-only (fallback period)
- [ ] Surrogate keys synchronized between systems
- [ ] Monitoring and alerting active

### Rollback Plan
1. Keep source platform in read-only state for defined fallback period
2. Document exact steps to revert connections
3. Maintain data sync from Snowflake back to source (if bidirectional needed)
4. Define rollback triggers (e.g., data integrity failure, performance SLA breach)
5. Practice rollback in test environment before production cutover

## Security

- Use principle of least privilege for database connections
- Enable MFA on all Snowflake accounts, especially privileged roles
- Configure SSO with corporate identity provider (Azure AD/Entra ID, Okta)
- Prioritize automated provisioning via IdP with SCIM
- Set up network policies to whitelist trusted IP ranges
- Regularly rotate access codes and credentials
- Audit migration activities and access logs
- Encrypt sensitive data during transit (SSL/TLS)
- Use storage integrations (not raw credentials) for cloud storage access
- Implement proper backup strategies
- Maintain audit trails for compliance
- Never commit credentials to version control
- Create migration-specific roles; revoke after migration complete

### SQL Server RBAC Migration Pattern

**SQL Server:** DAC (Discretionary Access Control) + RBAC mix; Login + User separation  
**Snowflake:** Pure hierarchical RBAC; unified User object; authenticate via SSO/OAuth

**Role hierarchy best practice:**

| Role Type | Description | Naming | Example |
|-----------|-------------|--------|--------|
| Access Roles | Low-level; specific permissions on database objects | `[PERMISSION]_[OBJECT]` | `WH_ANALYTICS_USAGE`, `DB_SALES_READ` |
| Functional Roles | High-level; aligned with business functions; granted Access Roles | `[FUNCTION]_ROLE_[ENV]` | `DATA_ANALYST_ROLE`, `DATA_ENGINEER_ROLE_PROD` |

**Key actions:**
- Use **future grants** for auto-applying permissions to new objects
- Establish audit processes for role/user creation, deletion, privilege changes
- Set `QUOTED_IDENTIFIERS_IGNORE_CASE = TRUE` for SQL Server migrations (reporting tool compatibility)

## Performance

- Ensure adequate memory (8GB+ recommended for large conversion projects)
- Monitor disk space for temporary files
- Use SSD storage for better I/O performance during local processing
- Plan migrations during off-peak hours
- Use incremental migration strategies for very large tables
- Right-size warehouses: start small, scale up based on performance data
- Set auto-suspend to 60 seconds on all warehouses

## Post-Migration

### Immediate (Week 1-2)
- Validate application connectivity to Snowflake
- Monitor query performance; identify and optimize slow queries
- Track user adoption and gather feedback
- Resolve any data discrepancies found by users

### Short-Term (Month 1-3)
- Right-size warehouses based on actual usage patterns
- Implement CLUSTER BY on large, frequently queried tables
- Set up resource monitors for cost control
- Implement showback/chargeback model for cost attribution
- Refine RBAC hierarchy; audit roles and permissions
- Implement Dynamic Data Masking and Row Access Policies for sensitive data

### Long-Term (Ongoing)
- Continuous performance monitoring via ACCOUNT_USAGE
- Regular security audits
- Cost optimization reviews
- Explore Snowflake-native features (data sharing, marketplace, Snowpark)
- Decommission legacy platform after sufficient fallback period
- Always test conversions in development environments first
- Maintain detailed migration documentation

## Resources

- SnowConvert AI (free): https://www.snowflake.com/en/migrate-to-the-cloud/snowconvert-ai/
- SnowConvert AI Training (free): https://training.snowflake.com
- DDL Export Scripts: https://github.com/Snowflake-Labs/SC.DDLExportScripts
- SnowConvert AI Docs: https://docs.snowflake.com/en/migrations/snowconvert-docs/overview
- Official Migration Guides:
  - Oracle: https://docs.snowflake.com/en/migrations/guides/oracle
  - SQL Server: https://docs.snowflake.com/en/migrations/guides/sqlserver
  - Teradata: https://docs.snowflake.com/en/migrations/guides/teradata
- SnowConvert Support: snowconvert-support@snowflake.com
- Professional Services: https://www.snowflake.com/en/solutions/professional-services/

## Workshop Deliverables & Templates

### Final Readout Structure (Day 10)
The Workshop Delivery Readout synthesizes all workshop findings:
1. **Objectives**: Components of migration, engagement overview, commitment
2. **Migration scope**: Environment metrics, migration effort estimation, code conversion results
3. **High-level timeline**: Assumptions, resources, milestones, go-live date
4. **Tool recommendations**: Partners, third-party tools, Snowflake features
5. **Assessment findings**: Risk register, mitigation strategies
6. **Go-forward plan**: Execution approach, next steps
7. **Documentation appendix**: Estimation approach, workshop estimates, task list, RACI, open items

### RACI Matrix

Typical migration RACI roles:

| Task Area | Customer | SI/Partner | Snowflake |
|-----------|----------|-----------|-----------|
| Code extraction (DDL/DML) | R, A | C | C |
| SnowConvert AI conversion | C | R, A | C |
| Manual EWI resolution | C | R, A | C |
| Data migration execution | C | R, A | I |
| Data validation | R, A | C | I |
| UAT sign-off | R, A | C | I |
| BI repointing | C | R, A | I |
| Security model (RBAC) | R, A | C | C |
| Cutover execution | C | R, A | C |
| Post-migration support | C | R, A | I |

(R=Responsible, A=Accountable, C=Consulted, I=Informed)

### Delivery Checklist Topics
- [ ] Introduction and Engagement Overview (team intros, scope, questionnaire, architecture review)
- [ ] Database Conversion (code assessment, extraction, analysis, conversion, deployment plan)
- [ ] Data Migration (table scope, data volume, methods, security model review)
- [ ] Data Integration (data sources, ingestion, transformation, orchestration, deployment)
- [ ] Data Validation (validation approach, expectations, remediation plan, environments)
- [ ] Data Consumption (platform inventory, repointing, training plan)
- [ ] Roles and Responsibilities (RACI selection, participant roles)
- [ ] Migration Timeline (calculator inputs, resource assumptions, milestones)
- [ ] Partner and Tool Recommendations
- [ ] Readout Review and Final Delivery

## Migration Questionnaire Areas

When gathering customer requirements, cover these sections:

### Project Section (Green)
- Migration drivers, target completion date
- Preferred migration approach (lift-and-shift, re-architecture, phased)
- Go-live approach (big bang, phased, parallel)
- Logical divisions / segmentation for phased migration
- Business-critical workload SLAs
- Performance integration testing criteria
- Parallel execution requirements
- Governance: PM methodology, change control, escalation process
- Staff: team size, productive hours/week, Snowflake expertise level

### Architecture Section (Green)
- Cloud provider and region (production, DR, non-production)
- Network bandwidth and private networking method
- Snowflake account strategy (single vs. multi-account)
- Environment strategy (Dev, Test, QA, Stage, Prod, DR)
- Complete technology inventory: data modeling, ETL, transformation, reporting, analytics, data science, orchestration, monitoring, data quality, cataloging, CI/CD, identity management

### Data Section (Green)
- Total data volume (MB/GB/TB/PB)
- Historical data transfer method
- Character encoding (UTF-8 compatibility)
- Semi-structured and unstructured data formats
- Data model modifications or in-flight projects
- OLTP workloads, low-latency requirements
- Data masking and row-level security
- Data sharing requirements
- Sensitive data (PII/PHI/PCI) and regulatory compliance (HIPAA, PCI, SOX, CCPA, GDPR)
- Records retention policies

### Platform to Migrate (Yellow)
- Database name, technology, version, edition, character set, hosting location, administration model

### Data Suppliers/Sources (Yellow)
- Name, purpose, technology, hosting, sensitive data, integration technology/method, frequency, strategic plan, job count, volume, execution duration

### Data Consumers/Targets (Yellow)
- Name, purpose, technology/version, consumption method, connection type, hosting, frequency, strategic plan, asset count, semantic models, volume

### Platform-Specific Questions (Blue)
**Oracle:** Exadata form factor, SaaS apps, AWR reports, UTPLSQL, global variables, Advanced Queuing, RAC, Data Guard, multi-tenant, Spatial, Database Vault, Compression, Golden Gate, partitioning
**SQL Server:** Resource usage reports, CLR integration, spatial data types, spatial index, data compression, SQL Server Replication
**Teradata:** Transaction mode (ANSI vs BTET), Bankers Rounding, Data Labs

## Folder Structure

Standard engagement folder organization:

```
LiftoffEngagementPackage/
├── CapN_CustomerFacing_Deck.pptx          # Customer delivery deck (all days)
├── CapN_Liftoff_Runbook.docx              # Partner step-by-step guide (all days)
├── Recommended_Agenda.xlsx                # Workshop agenda with attendees/outcomes
└── MigrationPrototype_MigrationPlanning/
    ├── Prerequisites/
    │   ├── Partner_PreReq_Tracker.xlsx     # Partner prerequisite tracking
    │   └── Files to be Sent Out to Customer/
    │       ├── Migration_Questionnaire.xlsx # Customer questionnaire (Oracle + SQL Server tabs)
    │       └── Engagement_Prerequisites_Checklist.xlsx
    ├── Secure share folder structure/      # Shared drive (Customer + Partner + Snowflake)
    │   ├── Prerequisites/
    │   │   ├── Database conversion (DDL) code extracts/
    │   │   ├── Data integration (ETL) code extracts/
    │   │   ├── Data consumption code extracts/
    │   │   └── Architecture diagrams/
    │   └── Documentation/
    │       ├── White papers and guides/
    │       ├── Analysis/
    │       │   ├── Code conversion reports/
    │       │   └── Scripts/
    │       └── Presentations/
    ├── MigrationPlanningTemplates/
    │   ├── Liftoff_Engagement_Readout_Template.pptx
    │   ├── Migration_Timeline.xlsx         # Schedule with milestones
    │   ├── Engagement_Delivery_Checklist.xlsx
    │   ├── Engagement_Action_Tracker.xlsx
    │   ├── Engagement_Running_Notes.docx
    │   └── Migration_Task_List_and_RACI.xlsx
    └── Email templates/
        └── Daily_Workshop_Summary_Email_Template.docx
```

## Partner Learning Materials

### Migration Overview
| For Developers | For Architects |
|---------------|----------------|
| Intro to Snowflake Migration Master Class | Migrate To The Snowflake AI Data Cloud |
| From Legacy to Cloud: Snowflake's Roadmap (Video) | Migration Master Class Academy |
| End-to-End Migration: Data and Pipelines (Hands-on Lab) | SnowConvert On-Demand Training |

### Database & Object Conversion
| For Developers | For Architects |
|---------------|----------------|
| SnowConvert for Developers (Required) | Migration Master Class Academy |
| Migrate to the Snowflake AI Cloud | Best Practices for Migrating Historical Data |
| AI Feature: Migration Assistant Blog | E2E Migration Hands-on Lab |
| Quickstart: E2E Migration | SnowConvert Docs Overview |
| SnowConvert AI-Powered Migrations (Video) | Accelerate Migrations: What's New in SnowConvert |

### Data Migration
| For Developers | For Architects |
|---------------|----------------|
| Level Up: Data Loading | Level Up: Data Loading |
| Doc: Data Loading Overview | Doc: Data Loading Overview |

### Data Integration & Transformation
| For Developers | For Architects |
|---------------|----------------|
| Snowflake Openflow | 9 Best Practices: On-Premises to Cloud |
| Snowflake Data Integration | |
| Workshop: Data Engineering | |

### Data Validation
| For Developers | For Architects |
|---------------|----------------|
| SnowConvert Migration Assistant | Accelerate Migrations: What's New (Video) |
| SnowConvert AI Documentation | |
| SnowConvert Data Validation | |

### Data Consumption
| For Developers | For Architects |
|---------------|----------------|
| SnowConvert Power BI Repointing | SnowConvert AI Documentation |
| SnowConvert Teradata ETL-BI Repointing | |
| ETL BI Repointing | |
| Power BI Transact Repointing | |
| SSIS Repointing | |

## SnowConvert AI Quick Reference

- **Average conversion rate:** +95% (based on total LOC for Oracle, SQL Server, Teradata migrations)
- **Lines of code converted to date:** 2.0B+
- **Database objects converted:** 46M+
- **Average timeline acceleration:** +88%
- **Supported platforms:** Teradata, Oracle, SQL Server, Amazon Redshift, Synapse, Sybase*, BigQuery*, Netezza*, Postgres*, Greenplum*, Databricks SQL* (*tables and views only)
- **ETL Replatform:** SSIS (Public Preview), Informatica Power Center (Private Preview) → dbt projects
- **BI Repointing:** Power BI (Public Preview)
- **Features:** Code Conversion (GA), Migration Assistant (GA), Code Verification (Public Preview), Data Validation (Public Preview)

### Key Resources
- Download SnowConvert AI: Available from Snowsight → Ingestion/Migrations
- Training: https://learn.snowflake.com/en/courses/OD-SC-D/
- Documentation: https://docs.snowflake.com/en/migrations/snowconvert-docs/overview
- Mastering Migration Planning: On-Demand Course
- E2E Migration Hands-on Lab: Virtual Hands-On Lab
- Power BI Repointing Blog: Available on Snowflake Blog
