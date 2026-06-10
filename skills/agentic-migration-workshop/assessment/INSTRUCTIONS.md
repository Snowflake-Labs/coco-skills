
# Workshop Session: Migration Assessment

## Session Overview

**Present to user:**
> Welcome to the **Migration Assessment** session. This is where we build a clear, data-driven picture of your migration — what you're working with, how complex it is, and how long it will take.
>
> By the end of this session, you'll have:
> - A complete inventory of your source objects
> - A complexity scorecard with risk ratings
> - A feature gap analysis with Snowflake alternatives
> - A Migration Readiness Report you can share with stakeholders
> - An effort estimate and timeline (if you have SnowConvert AI data)
>
> Let's start by understanding what we're migrating.

## Prerequisites
- Platform reference file read (from `<SKILL_DIRECTORY>/references/`)
- `references/best-practices.md` read

## Session Flow

### Part 1: Source Inventory

**Goal:** Build a complete catalog of objects in scope.

**Context to share with user:** This migration is a strategic modernization initiative — not just a cost-saving exercise. Beyond the immediate move, Snowflake unlocks capabilities your current platform can't easily deliver: lakehouse architecture, semi-structured data, cross-cloud data sharing, and Snowpark ML. Common drivers by platform:
- **SQL Server**: License costs, vertical scaling limits, DBA overhead, SSIS/SSRS complexity
- **Redshift**: Concurrency bottlenecks, cluster management, WLM tuning, VACUUM/ANALYZE overhead, scaling delays
- **Oracle**: License/support costs, RAC complexity, Exadata lock-in, PL/SQL maintenance burden
- **Teradata**: Cost per TB, hardware refresh cycles, BTEQ/TPT tooling limitations

**Ask the user** (via `ask_user_question`) how they'd like to provide their source inventory:
- Paste or upload DDL export files
- Provide a database/schema name (if source is queryable)
- Share a manual list of objects
- Provide SnowConvert AI extraction results
- They need help extracting DDL first (guide them to https://github.com/Snowflake-Labs/SC.DDLExportScripts)

**Then categorize** every object into this inventory table and present it:

| Category | Examples | Count |
|----------|----------|-------|
| Tables | Heap, partitioned, temporary, external | |
| Views | Standard, materialized, recursive | |
| Procedures | Stored procedures, functions, packages (Oracle) | |
| Indexes | B-tree, bitmap, function-based, columnstore | |
| Constraints | PK, FK, unique, check, default | |
| Sequences | Auto-increment, identity columns | |
| Triggers | DML triggers, DDL triggers | |
| Other | Synonyms, DBLinks, user-defined types | |

**Present the inventory** to the user with a summary: *"Here's what I found — [N] total objects across [M] categories."*

### Part 2: Complexity Scoring

**Goal:** Rate each object category and identify high-risk items.

**Explain to user:**
> Now let's assess how difficult each part of your migration will be. I'll score everything on a 1-5 scale based on how directly it maps to Snowflake equivalents.

**Scoring rubric (share with user):**

| Score | Difficulty | What It Means |
|-------|-----------|---------------|
| 1 | Trivial | Direct 1:1 mapping — Snowflake handles this natively |
| 2 | Simple | Minor syntax changes needed |
| 3 | Moderate | Significant rewrite, but Snowflake has a clear alternative |
| 4 | Complex | Major redesign required, no direct equivalent |
| 5 | Critical | Requires architectural change or external tooling |

**Platform-specific complexity drivers** (use when scoring):

- **Oracle**: PL/SQL packages (4), DBLinks (4), bitmap indexes (1), materialized views (2), sequences (2), synonyms (2)
- **Teradata**: BTEQ scripts (3), MultiValue compression (1), temporal tables (3), MERGE with complex conditions (2), hash indexes (1)
- **Redshift**: Distribution keys (1), sort keys (2), COPY from S3 (2), spectrum tables (3), late-binding views (2), WLM queues (3), PL/pgSQL procedures (3), VACUUM/ANALYZE dependencies (1)
- **SQL Server**: CLR procedures (5), linked servers (4), SSRS reports (4), SSIS packages (4), temporal tables (3), columnstore indexes (1)

**Produce a complexity scorecard:**
1. Score each object category
2. Calculate weighted complexity: `SUM(count * score) / SUM(count)`
3. Highlight anything scoring >= 4 as a critical item requiring special attention

**Present to user:** *"Your overall complexity score is [X]/5. Here are the items that need the most attention..."*

### Part 3: Feature Gap Analysis

**Goal:** Identify source features without direct Snowflake equivalents — and their alternatives.

**Explain to user:**
> Every platform has features that don't translate directly to Snowflake. The good news is that Snowflake almost always has a modern alternative. Let me map those for you.

**Check the platform reference** for known gaps, then build the gap analysis:

| Source Feature | Snowflake Alternative | Effort | Risk |
|---------------|----------------------|--------|------|
| Row-level security | Row Access Policies | | |
| Column masking | Dynamic Data Masking | | |
| Stored procedures with cursors | Snowflake Scripting or JavaScript UDFs | | |
| Database links/remote queries | Data sharing or external tables | | |
| Scheduled jobs | Snowflake Tasks | | |
| Change Data Capture | Streams + Tasks | | |

**Present with context:** For each gap, briefly explain *why* the Snowflake alternative is different and what the migration implication is.

### Part 4: Migration Readiness Report

**Goal:** Produce a polished, stakeholder-ready report.

**Compile** results from Parts 1-3 and present as a formatted report:

```
# Migration Readiness Report
## [Source Platform] → Snowflake
## Date: [Today's Date]

### Executive Summary
- Total objects in scope: [count]
- Overall complexity score: [weighted average] / 5
- Estimated effort: [hours/days]
- Migration readiness: [Ready / Ready with caveats / Needs redesign]

### Object Inventory
[Table from Part 1]

### Complexity Scorecard
[Table from Part 2]

### Critical Items (Score >= 4)
[List with specific mitigation strategies for each]

### Feature Gap Analysis
[Table from Part 3]

### Risk Register
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|

### Recommended Migration Order
1. [Phase 1 — lowest complexity first, to build momentum]
2. [Phase 2]
3. [Phase 3 — highest complexity last]

### Next Steps
- [ ] Review and approve this readiness report
- [ ] Proceed to Schema Conversion
- [ ] Address critical items before full migration
```

**Present to user:**
> Here's your Migration Readiness Report. Take a moment to review — I want to make sure everything looks right before we move on.

**CHECKPOINT:** Wait for user approval before continuing.

### Part 5: SnowConvert AI Assessment (Optional)

**Introduce to user:**
> If you have SnowConvert AI installed, we can augment this assessment with automated metrics. SnowConvert AI achieves a **96%+ automated conversion rate** for Redshift and reduces manual effort by **50-70%** for SQL Server. The free assessment alone gives you data-driven scope and complexity estimates.

**If user has SnowConvert AI**, guide them through running the assessment and interpret results:

| Status | Meaning | How to Score |
|--------|---------|-------------|
| Green | Successfully converted | Trivial/Simple (1-2) |
| Yellow (FDM) | Further Development Mandatory | Moderate/Complex (3-4) |
| Red (EWI) | Error with Impact | Critical (5) — must resolve manually |

Merge SnowConvert AI results with the manual complexity scores and update the readiness report.

### Part 6: Effort Estimation (6-Step Process)

**Introduce to user:**
> Now let's build a rigorous effort estimate using SnowConvert AI's reports. This is the same 6-step methodology Snowflake's partners use for engagement planning.

**Step 6.1 — Code Extraction & Automated Scoring:**
- Feed all source code to SnowConvert AI
- Key metric: Total Conversion Percentage (e.g., 85% automated)
- SnowConvert builds an AST for semantic analysis, not just pattern matching

**Step 6.2 — Code Inventory & Workload Sizing:**
- Reports: Top-Level Code Unit Report + Elements Report
- Key metrics: Object counts, total LOC, LOC breakdown by complexity

**Step 6.3 — ETL Re-platforming Analysis:**
- Reports: ETL Replatform Component Summary + Issues Report
- Strategy: simple flows → Dynamic Tables; complex flows → Snowpark Python

**Step 6.4 — BI Repointing Analysis:**
- Report: Power BI Repointing Automation Score
- Distinguishes auto-repointed (low risk) from manual refactoring (high risk)

**Step 6.5 — Manual Effort Quantification:**
- Reports: Issues Report (EWIs) + Functions Usage Report
- Apply complexity multipliers:

| Complexity | Multiplier (Days/100 LOC) | Examples |
|-----------|--------------------------|---------|
| Low | 1.0 | Simple SQL DML fixes |
| Medium | 2.5 | Functions/UDFs with proprietary logic |
| High | 4.0 | T-SQL cursor rewrites to Snowpark Python |

**Step 6.6 — Timeline:**
- Automated deployment + Manual rework + Testing buffer (30-40% of coding time)

**Present a sample estimation** to calibrate expectations:

| Phase | Duration | Notes |
|-------|----------|-------|
| Schema & Data Model Deployment | 1 week | Automated DDL deployment |
| Manual Code Refactoring | 3 weeks | Resolving high-effort EWIs |
| Data Migration & Initial Load | 1 week | Parallel to code fixes |
| Testing & Validation (SIT/UAT) | 3 weeks | 100% of converted objects + BI |
| Go-Live (Cutover) | 1 day | Clone and Swap methodology |
| **Total** | **~8 weeks** | 30K LOC, 50TB example |

**CHECKPOINT:** Review effort estimate with user.

### Part 7: Pilot Evaluation (Large/Complex Migrations)

**Ask the user** if their migration is large or complex enough to warrant a pilot:

| Indicator | Discovery Workshops | Migration Pilot |
|-----------|-------------------|----------------|
| Platform | Standard RDBMS | Complex/heterogeneous |
| Approach | Lift-and-shift | Data modernization |
| Size | Small to medium | Large |

If a pilot is appropriate, guide through:
- **Lineage-based use case selection** — start at consumption (reports), trace backward to source
- **Three parallel workstreams:** Planning & Discovery, E2E Pilot, User Pilot (repoint reports quickly)
- **Entry/exit criteria** and scaling to wave-based delivery

## Session Wrap-Up

**Present to user:**
> Here's what we accomplished in this Assessment session:
> - [Summary of deliverables produced]
>
> Your migration readiness is [Ready / Ready with caveats / Needs redesign] with an overall complexity of [X]/5.

**CHECKPOINT:** Confirm assessment is complete before transitioning.

## Next Session

If Full Workshop → proceed to **Schema Conversion** (read `schema-conversion/SKILL.md`)
