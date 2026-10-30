---
name: recon_skill
description: >
  Data reconciliation toolkit for comparing any two datasets and finding
  discrepancies. Supports configurable join keys, threshold-based matching
  (numeric, date, fuzzy text), profile-based saved configurations, and
  structured exports. Triggers: recon, reconciliation, compare data, find
  discrepancies, data matching, run recon, build recon profile, create
  reconciliation, match datasets, break analysis, discrepancy report.
data_domain: "data reconciliation, accounts receivable matching, general ledger reconciliation, subledger comparison, intercompany elimination, bank reconciliation, transaction matching, discrepancy analysis"
created_date: 2026-05-20
version: 1.0.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Recon Analysis

## Intent

General-purpose data reconciliation skill for accounting and finance teams. Compares two datasets (tables, queries, or files), identifies discrepancies using configurable thresholds, and produces structured reports with export capability. Designed for controllers, accounting managers, and operations analysts who need to match records across systems, validate data integrity, and produce reconciliation evidence for close packages and audits.

This skill is for dataset comparison and discrepancy identification. Not for: data modification, journal entry posting, transaction creation, or dashboard building.

## Behavior

### Routing Logic

Detect user intent and route to the appropriate workflow:

| User Intent | Mode | Description |
|---|---|---|
| "Build profile", "create recon", "configure reconciliation" | **Builder** | Create and save a new reconciliation configuration |
| "Run recon", "execute profile", "compare data" | **Runner** | Execute a saved profile or ad-hoc comparison |
| Names a specific saved profile | **Named Run** | Load and execute that specific profile configuration |
| Ambiguous | **Ask** | Clarify intent before proceeding |

If intent is unclear, present a choice:

- **Run Reconciliation** — Execute a saved profile to compare datasets
- **Build Profile** — Create a new reconciliation configuration

### Guardrails

This skill is a **read-only analysis tool**. It compares data — it never modifies it.

If a user requests any data modification (INSERT, UPDATE, DELETE, MERGE, TRUNCATE, DROP, or equivalent), refuse immediately:

> "This is a read-only reconciliation tool and cannot modify data. If you need to update source data, please use the appropriate tool for that system."

This applies to all modes. No exceptions.

### Builder Mode Workflow

**Purpose**: Create and persist a reusable reconciliation configuration.

**Steps:**

1. **Gather Data Sources** — Identify two datasets to reconcile. Each source can be:
   - A Snowflake table (fully qualified name)
   - A custom SQL query
   - A local file (CSV/Excel)
   Preview each source (5 rows) and display column inventory.

2. **Configure Filters (Optional)** — Add row-level WHERE clause filters and configure date columns for runtime date-range filtering.

3. **Define Join Keys** — Identify the column(s) that match records between sources. Support composite keys and cross-source column name mapping (e.g., Source1.CONTRACT_ID = Source2.OPP_ID).

4. **Select Comparison Fields** — Define which fields to compare for discrepancies. For each field, capture:
   - Display name
   - Source 1 column name
   - Source 2 column name
   - Data type: Number, Date, or Text

5. **Configure Join Type** — How to handle unmatched records:
   - Inner (only records in BOTH datasets)
   - Left (all from Source 1, match where possible)
   - Full Outer (all records from both — find orphans)

6. **Set Thresholds** — Define materiality thresholds:
   - Percent threshold (e.g., 1% tolerance on amounts)
   - Nominal dollar threshold (e.g., $0.50 de minimis)
   - Fuzzy text match threshold (0-100 similarity score)

7. **Validate and Save** — Run a dry-run validation (join key overlap check, column existence, type compatibility), present a full validation report, then save the profile with a user-chosen name and visibility (private/public).

**Validation Checks (run at each step):**

| Check | Severity | Description |
|---|---|---|
| Source accessible | CRITICAL | Can query/read the source without error |
| Column exists | CRITICAL | Named columns exist in the source |
| Join key has values | CRITICAL | Join key column is not 100% NULL |
| Join key overlap > 0% | CRITICAL | At least some keys match between sources |
| Type compatibility | WARNING | Comparison fields have compatible types |
| Scale sanity | WARNING | Numeric fields are in the same order of magnitude |
| Duplicate keys | WARNING | Join key is not unique (may cause fan-out) |

**Profile Persistence:**

Profiles are stored via a configuration persistence mechanism (stored procedure, table, or config store) that supports:
- List all available profiles (name, owner, sources, last updated)
- Load a specific profile by name
- Save a new profile (INSERT)
- Update an existing profile (UPDATE) — owner only
- Delete a profile (soft-delete with history) — owner only
- Revert to a prior version by timestamp

**Profile Configuration Schema:**

```
source1: {type, value}
source2: {type, value}
filters: {source1_filter, source2_filter}
date_columns: {enabled, source1_col, source2_col}
join_keys: {source1_key: source2_key}
comparison_fields: [{name, source1_col, source2_col, data_type}]
join_type: inner | left | full_outer
thresholds: {percent, nominal, fuzzy}
```

### Runner Mode Workflow

**Purpose**: Execute a reconciliation and produce a discrepancy report.

**Steps:**

1. **Select Profile** — Query available profiles, present as a selection list. Load the selected profile's configuration.

2. **Collect Runtime Parameters** — Ask for optional date range filter (start/end dates). Confirm configuration summary before executing.

3. **Validate Join Keys** — Before full reconciliation, check join key coverage:
   - Count distinct keys in each source
   - Count matched keys (intersection)
   - Count Source1-only and Source2-only keys
   - Display match rate percentage
   - If match rate < 90%, warn user about potential data quality issues
   - Ask user to proceed, show unmatched keys, or cancel

4. **Execute Reconciliation** — Run comparison logic:

   **For numeric fields:**
   - Exact match (diff = 0) → Match
   - Within nominal threshold → Match (within threshold)
   - Within percent threshold → Match (within %)
   - Otherwise → Discrepancy

   **For text fields (fuzzy matching):**
   - Similarity score >= threshold → Match (fuzzy N%)
   - Otherwise → Discrepancy

   **For date fields:**
   - Difference <= 3 days → Match (N day diff)
   - Otherwise → Discrepancy

   **Unmatched records (outer joins):**
   - Key in Source1 only → Source1 Only
   - Key in Source2 only → Source2 Only

5. **Classify Breaks (Discrepancy Analysis)** — For each discrepancy, determine the business reason:

   **Category Classification:**
   Apply the customer's category rule configuration (resolved at runtime) to assign each discrepancy a typed category and human-readable comment explaining the business reason. Categories are evaluated in priority order — first matching rule wins.

   **Default Category Taxonomy (customer-extensible):**
   The following categories are applied out of the box. Customers can extend or replace this taxonomy by configuring custom category rules in their profile.
   | Category | Pattern | Comment Template | Owner | Action | Expected Clear |
   |---|---|---|---|---|---|
   | Timing | Key matches but posting dates cross period boundary | "{Month} deal posted in prior period — timing difference" | Ops | monitor | Next business day |
   | Multi-Source Aggregation | One-to-many relationship between sources | "One record in Source2 linked to multiple records in Source1. No actual difference in total." | Accounting | suppress | N/A |
   | Threshold Exception | Named exception rule applies (e.g., specific items excluded from one system) | "Exception amount impacts Source2 total but not Source1" | Accounting | suppress | N/A |
   | Round-off | Absolute difference < $1 | "Round off issue" | Accounting | suppress | N/A |
   | Cut-off | Transaction date in Source1 falls outside Source2 recon window | "Source1 record dated {date} — after recon window end" | Ops | monitor | Period close +1 day |
   | Data Quality | Identifier format mismatch or missing linkage | "No matching record found — review source system linkage" | Reference Data | raise-ticket | Per SLA |
   | Discrepancy | No rule matches | "Discrepancy — manual review required" | Controller | adjust | Before close |

   **Operational Triage Fields (per category rule):**
   - **Owner** — Team or role responsible for resolving this break type (Ops, Accounting, Reference Data, Controller, Treasury, Source System)
   - **Action** — What should happen: `monitor` (will self-clear), `adjust` (manual JE or correction needed), `raise-ticket` (system fix required), `suppress` (known/accepted — no action)
   - **Expected Clear** — When this break type typically resolves (e.g., "Next business day", "Period close +1 day", "Per SLA", "N/A" for suppressed items)

   These fields are optional per rule. If not configured, the output omits triage columns and shows only Category + Comment.

   **Root-Cause Statement Generation:**
   For each discrepancy that doesn't match a known category rule, generate a root-cause reason by:
   1. Pull full context from both sides (all columns, not just comparison fields)
   2. Examine the join result: is the key missing from one side entirely, or present with different values?
   3. If key is missing: check if the record exists in the other source with different filters/dates (look back N periods)
   4. Apply CASE-based logic against the differing attributes to produce a reason statement:
      - "No record found in Source2 linked to this key"
      - "Source2 record type is {type} — excluded from recon scope"
      - "Source2 close date {date} is before recon window start"
      - "Record exists in Source2 but did not match — review filter configuration"

   **Principles:**
   - Every discrepancy gets a Category + Comment — never leave unexplained
   - Categories describe the "why" not the "what" (business reason, not just the numeric diff)
   - If no rule matches and the reason cannot be determined from available data: "Manual review required — flag for controller"
   - NEVER fabricate or speculate on reasons — only state what the data shows
   - Category rules are customer-configurable and stored as part of the profile or as a separate rule configuration

6. **Present Results** — Display structured output:

   ```
   RECONCILIATION RESULTS: <Profile Name>
   Date Range: <range or "All dates">
   Total Records: N
   Match Rate: XX.X%

   Per-field breakdown:
     FIELD: <name> — Match: Y | Within Threshold: Z | Discrepancy: W

   CATEGORY BREAKDOWN:
     | Category | Count |
     | Matching | XX |
     | Timing | XX |
     | Round-off | XX |
     | **Discrepancy** | **XX** |

   DISCREPANCY DETAIL (top N by absolute diff):
     <KEY> — Category: <category> | Owner: <owner> | Action: <action>
       Reason: <comment>
       <field>: Source1=X vs Source2=Y (diff=Z)
       Expected clear: <expected_clear_date or "Manual review">
   ```

7. **Export** — Offer CSV or Excel export:
   - CSV: Single file with all results
   - Excel: Multi-sheet workbook (Summary, Discrepancies, Matched, Unmatched, Discrepancy Analysis, Metadata)
   - The **Discrepancy Analysis** sheet provides root-cause reasoning for each discrepancy record: key, both-side values, category, and reason statement

### Named Profile Routes

When a profile is loaded that matches a registered named route (configured per customer), invoke the dedicated workflow for that profile type instead of the generic Runner flow. Named routes enable specialized logic for recurring reconciliations (e.g., monthly close recons with specific category taxonomies, multi-step queries, and custom Excel formatting).

Named routes are customer-configured. This skill ships with the routing mechanism; the specific routes are defined per customer during setup.

### Stopping Points (Mandatory User Confirmation)

- After profile selection and config summary (before execution)
- After join key validation (before full reconciliation)
- After results presentation (before export)

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (profile store resolved, both source tables accessible, join keys validated) | Full reconciliation with all features |
| MEDIUM (profile store resolved but some comparison columns ambiguous or source partially accessible) | Run reconciliation with available fields; note degraded sections; suggest configuration fixes |
| LOW on critical (no profile persistence mechanism found, or neither source table accessible) | Skip execution; output data requirements and recommended setup steps |

### Error Handling

| Scenario | Action |
|---|---|
| No profiles exist | Inform user; suggest using Builder mode to create one |
| Selected profile source table inaccessible | Report access error; suggest checking role/grants |
| Join key match rate = 0% | Block execution; report zero overlap; suggest reviewing key definitions |
| Query timeout on large dataset | Offer to add date filters or LIMIT to narrow scope |
| Mixed source types (file + table) | Load file locally, query table with filters, join in-memory |
| Profile save fails | Surface the error message; suggest checking permissions |

## Context Queries

- A configuration/profile persistence store where each record represents a saved reconciliation configuration. Contains: profile name, owner/creator, visibility (public/private), creation timestamp, last-updated timestamp, and a structured JSON settings payload holding source definitions, join keys, comparison fields, thresholds, and filter settings. Used to list, load, save, update, and soft-delete reconciliation profiles. Grain: one row per profile per version (append-only with latest-wins semantics).

- Two arbitrary comparison datasets (resolved at runtime from the loaded profile). Each dataset is either a Snowflake table, a SQL query result, or a local file. The skill does not know these tables at authoring time — they are defined by the user during Builder mode and stored in the profile configuration. The reconciliation engine must be able to: (a) preview N rows from each source, (b) enumerate columns with types, (c) execute join and comparison logic against them.

- (Optional) A category rule configuration table or embedded rule set that maps discrepancy patterns to business-reason categories. Contains: rule priority/order, category label, matching condition (pattern on field values, threshold ranges, or record attributes), and a comment template describing the business reason. Used during the Break Classification step to assign typed categories and generate root-cause statements. Grain: one row per classification rule. If absent, the skill uses a generic taxonomy (Timing, Round-off, Data Quality, Unmatched, Discrepancy) based on the comparison results alone.

- (Optional) A date/calendar reference for runtime date filtering when the user wants to scope reconciliation to a specific period. The date columns are named per-source in the profile configuration and used to add WHERE clauses at query time.

- (Optional) A semantic view over the profile store or reconciliation results for Cortex Analyst follow-up queries (e.g., "show me all profiles with match rate below 95%", "which recons had the most discrepancies last month").
