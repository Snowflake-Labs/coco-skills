---
id: semantic-view-patterns
name: semantic-view-patterns
skill-name: $sv-patterns
description: Two modes for 25 Snowflake Semantic View modeling patterns — Tutorial mode deploys working examples and explains them live; Apply mode adapts a pattern to the user's own tables and generates ready-to-use DDL or YAML.
prompt: "$sv-patterns walk me through time intelligence"
language: en
categories: snowflake-site:taxonomy/product/ai, snowflake-site:taxonomy/snowflake-feature/build
status: Published
authors: Josh Klahr
type: snowflake
tools:
  - snowflake_sql_execute
  - bash
---

# Semantic View Patterns

Interactive, end-to-end tutorials for 25 Snowflake Semantic View modeling patterns. Each tutorial deploys a working example into your Snowflake account, walks through the annotated DDL or YAML, runs live queries, and surfaces what works and what doesn't.

# When to Use

This skill has two modes — use the right one based on what the user is trying to do:

**Tutorial mode** — user wants to *learn* a pattern:
- "walk me through `<pattern>`", "teach me `<concept>`", "explain how `<snippet>` works"
- "how does time intelligence work in SVs", "show me ASOF joins in action"
- "what snippets do you have", "what patterns are available"

**Apply mode** — user wants to *use* a pattern on their own data:
- "help me add time intelligence to my SV"
- "my SV has tables X, Y, Z — how do I model SPLY?"
- "I want to add window metrics to my existing semantic view"
- "can you update my SV to handle SCD2 dimensions?"
- "I'm building a SV for [use case] — what pattern do I need and how do I implement it?"

**Example triggers**: `$sv-patterns time intelligence` (Tutorial), `$sv-patterns apply time intelligence to my SV` (Apply), `$sv-patterns what snippets are available` (Discovery)

# What This Skill Provides

A library of 25 executable, self-contained Semantic View modeling patterns bundled alongside this skill, each with:
- Real problem statement and BI tool comparison
- Minimal but realistic seed data
- Fully annotated SV DDL **and** YAML (`semantic_view.sql` + `semantic_view.yaml`)
- Working `SEMANTIC_VIEW()` queries with live output
- Explicit gotchas and what-doesn't-work notes

**Tutorial mode**: Deploys each snippet directly via `snowflake_sql_execute`, walks through the annotated DDL or YAML section by section, runs live queries, and offers to clean up all created objects at the end.

**Apply mode**: The snippet files serve as annotated reference patterns. The skill reads the user's existing SV definition, maps the snippet's structure to their tables/columns, and generates adapted DDL or YAML ready to paste or deploy — no example data needed.

## Available Patterns

| Snippet | Concept |
|---------|---------|
| `range_join` | BETWEEN EXCLUSIVE — SCD2 temporal join |
| `asof_join` | ASOF — join to most recent record at event time |
| `multi_path_metrics` | USING — disambiguate multiple join paths |
| `shared_degenerate_dimension` | Shared degenerate dimension across two facts |
| `semi_additive_metric` | NON ADDITIVE BY — snapshot / headcount / balance |
| `window_metrics` | LAG, rolling avg, YTD window functions |
| `derived_metrics` | Cross-table derived metrics and ratios |
| `time_intelligence` | Role-playing aliases + computed-FK FACTS for SPLY/YoY/MoM |
| `entity_facts` | Aggregated entity-level facts and calculated dims |
| `variables` | VARIABLES clause for parameterized SVs |
| `multi_fact_table` | Multiple facts sharing product and date dims |
| `ai_metadata` | AI_SQL_GENERATION, AI_QUESTION_CATEGORIZATION, AI_VERIFIED_QUERIES |
| `tags` | WITH TAG on metrics |
| `introspection` | SHOW METRICS, SHOW DIMENSIONS, get_lineage() |
| `fact_as_relationship_key` | Computed FK fact — derive a join key from an expression when no physical FK column exists |
| `system_explain_semantic_query` | SYSTEM$EXPLAIN_SEMANTIC_QUERY — inspect generated SQL, debug errors without running the query |
| `caller_rights` | Ownership separation trick — make the SV owner have no base table access, forcing callers to supply their own; no privilege escalation ⚠️ Requires ACCOUNTADMIN |
| `standard_sql` | Plain SELECT on SVs — ANY_VALUE, metric-less queries |
| `inline_sv` | Inline SV CTEs ⚠️ Private Preview |
| `materialization` | SV materialization ⚠️ Private Preview |
| `scoped_dataset` | SQL query as logical table ⚠️ Private Preview |
| `row_access_policies` | RAP gotcha + two workarounds — prevent NULL rows when filtering dimension tables ⚠️ Requires ACCOUNTADMIN |
| `role_playing_dimensions` | Alias the same physical dimension table multiple times — independent ORDER_YEAR, SHIP_YEAR dimensions without USING |
| `accumulating_snapshot` | Kimball Accumulating Snapshot Fact Table — one row per pipeline entity, USING per milestone stage metric |
| `sv_diagnostics` | Six runtime and deploy-time failure modes — ambiguous path, fan trap, missing relationship, duplicate names/synonyms, wrong cardinality (silent inflation), semi-additive heuristic — with exact error messages and fixes |}

# Instructions

## Step 0 — Detect Mode and Authoring Format

Before doing anything else, determine two things:

### 0a — Detect Mode

Determine which mode the user wants:

- If the user said something like "walk me through", "teach me", "explain", "show me in action", "what snippets" → **Tutorial mode** → go to Tutorial Steps
- If the user said something like "apply to my SV", "add X to my semantic view", "my tables are...", "help me implement", "update my SV" → **Apply mode** → go to Apply Steps
- If ambiguous (e.g. "help me with time intelligence"), ask:
  > "Do you want me to walk you through the time intelligence pattern with a live example, or help you apply it directly to your own Semantic View?"

### 0b — Detect Authoring Format

Once mode is determined, ask which authoring format the user prefers. Use `ask_user_question` with these options:

| Option | Label | Description |
|--------|-------|-------------|
| DDL | `CREATE SEMANTIC VIEW` DDL | SQL-first; deploy with `CREATE OR REPLACE SEMANTIC VIEW`. Best for programmatic scripts, stored procedures, full feature access. |
| YAML | YAML + `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML` | Config-file-first; human-readable, version-control-friendly, includes `verify_only` dry-run. Some DDL-only features require post-deploy DDL. |

Store `AUTHORING_FORMAT = DDL` or `AUTHORING_FORMAT = YAML` and use it in all subsequent steps.

**Skip this question** if the user already indicated a preference (e.g. "show me the YAML", "give me the DDL").

### YAML Authoring — Key Facts

When `AUTHORING_FORMAT = YAML`:

**Deployment:**
```sql
-- Deploy:
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
  'TARGET_DB.TARGET_SCHEMA',
  $$ <yaml_contents> $$
);

-- Verify without deploying (dry-run — catch errors before they hit production):
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
  'TARGET_DB.TARGET_SCHEMA',
  $$ <yaml_contents> $$,
  TRUE  -- verify_only
);

-- Export an existing DDL SV to YAML:
SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('DB.SCHEMA.MY_SV');
```

**YAML ↔ DDL feature map:**

| DDL feature | YAML equivalent |
|---|---|
| `USING (relationship)` on metrics | `using_relationships: [rel_name]` |
| `NON ADDITIVE BY (dim)` | `non_additive_dimensions: [{table, dimension, sort_direction}]` |
| `PRIVATE` fact/metric | `access_modifier: private_access` |
| `AI_VERIFIED_QUERIES` | `verified_queries: [{question, sql, ...}]` |
| `WITH SYNONYMS (...)` | `synonyms: [list]` |
| `COMMENT = '...'` | `description: ...` |

**DDL-only features (no YAML equivalent):**
- `AI_SQL_GENERATION` / `AI_QUESTION_CATEGORIZATION` — set post-deploy via `ALTER SEMANTIC VIEW`
- `WITH TAG` — apply post-deploy via `ALTER SEMANTIC VIEW ... ADD TAG`
- `MAX_STALENESS` / `ADD MATERIALIZATION` — DDL only
- `VARIABLES` clause — DDL only
- `BETWEEN EXCLUSIVE` / `ASOF` range relationship syntax — DDL only
- Inline SQL subqueries in `TABLES` clause — DDL only
- `WITH ... AS SEMANTIC VIEW` inline CTE — DDL only

When a snippet has DDL-only features and `AUTHORING_FORMAT = YAML`, note the limitation and show the YAML for the base structure plus the DDL commands to apply the unsupported features post-deploy.

---

# Tutorial Steps

## Step 1 — Identify the Snippet

If the user named a snippet or concept, match it to the closest entry in the table above. If the user said something general like "what can you teach me" or "what's available", list the snippets with one-line descriptions and use `ask_user_question` to let them choose.

## Step 2 — Locate the Snippets Directory

The `snippets/` directory is bundled alongside this `SKILL.md`. Find the skill's location by checking where this SKILL.md lives (use `glob` to search common skill paths). The snippets are at `<skill_dir>/snippets/<snippet_name>/`.

If the skill directory cannot be found automatically, ask the user:
```
Where is the cortex-code-skills repo cloned on your machine?
```
Then construct the path as `<repo_root>/skills/semantic-view-patterns/snippets/`.

## Step 3 — Pre-Flight Check and Deploy Target (First Time Only)

### 3a — Access-control snippets

If the chosen snippet is `caller_rights` or `row_access_policies`:
- These snippets create **roles and a dedicated database** and require ACCOUNTADMIN (or both SECURITYADMIN + SYSADMIN).
- Run `SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE()` to confirm. If the role is insufficient, warn the user before proceeding.
- These snippets use **hardcoded database names** (`SV_CALLER_TEST` or `RAP_TEST`) — no target DB question is needed.
- **Warehouse**: do NOT create a dedicated warehouse. Instead, ask: _"Which warehouse should the analyst roles use for running queries? I'll grant them USAGE on it."_ Default to `CURRENT_WAREHOUSE()` if the user has no preference. Then run: `GRANT USAGE, OPERATE ON WAREHOUSE <wh> TO ROLE <analyst_role>` for each role.
- Track the dedicated objects created so you can offer cleanup in Step 10.

### 3b — Probe for Snowflake Learning Environment

For all other snippets, silently run both checks:
```sql
SHOW DATABASES LIKE 'SNOWFLAKE_LEARNING_DB';
SHOW ROLES LIKE 'SNOWFLAKE_LEARNING_ROLE';
```

- **Both found** → include `SNOWFLAKE_LEARNING_DB.PUBLIC` as a recommended option in the next question, noting it uses `SNOWFLAKE_LEARNING_ROLE` / `SNOWFLAKE_LEARNING_WH`.
- **Either missing** → don't offer it; go straight to asking for a custom location.

### 3c — Ask for target location and role

Ask a single question. Options depend on 3b:

- If Learning Environment is available: offer `SNOWFLAKE_LEARNING_DB.PUBLIC` (recommended) + custom location
- If not available: only offer custom location

For a **custom location**, follow up with:
- Target `DATABASE.SCHEMA` (you'll create the DB if it doesn't exist)
- Which **role** to use — needs `CREATE TABLE`, `CREATE SEMANTIC VIEW`, `CREATE SCHEMA` on that database
- Which **warehouse** to use

For the **Learning Environment**, set:
- `TARGET_DB = SNOWFLAKE_LEARNING_DB`, `TARGET_SCHEMA = PUBLIC`
- `TARGET_ROLE = SNOWFLAKE_LEARNING_ROLE`, `TARGET_WAREHOUSE = SNOWFLAKE_LEARNING_WH`

Store `TARGET_DB`, `TARGET_SCHEMA`, `TARGET_ROLE`, `TARGET_WAREHOUSE` for the rest of the session — don't re-ask.

### 3d — Track objects created

Before deploying anything, record an explicit list of every object you are about to create (tables, views, semantic views, DB if new). You'll use this list in the Step 10 cleanup offer.

## Step 4 — Read Snippet Files

Before presenting anything, read the relevant files for the chosen snippet:
- `snippets/<name>/README.md`
- `snippets/<name>/schema.sql`
- `snippets/<name>/seed_data.sql`
- `snippets/<name>/queries.sql`
- If `AUTHORING_FORMAT = DDL`: read `snippets/<name>/semantic_view.sql`
- If `AUTHORING_FORMAT = YAML`: read `snippets/<name>/semantic_view.yaml` (and `semantic_view.sql` for context on DDL-only features not in YAML)

## Step 5 — Act 1: The Problem

Present the framing conversationally — do NOT just paste the README. Synthesize:
1. What problem this snippet solves (2–3 sentences)
2. The "How You Might Express This Need" list

End with: _"Here's how Snowflake Semantic Views handle it — without any of those workarounds."_

Then add this prompt hint on its own line:
> 💡 _Want to learn how other tools tackle this problem? Ask me "Tell me about other approaches."_

## Step 5b — Other Approaches Handler

If at any point the user says "tell me about other approaches", "how does [tool] handle this", or "what would this look like in Power BI / Tableau / dbt / SQL":

Read the `## Equivalent in Other Tools` table from the snippet's README and present it conversationally. For each tool, briefly explain:
- What mechanism or feature they use
- Why it's more work or less reliable than the SV approach
- Any genuine strengths that tool has for this use case (be honest)

End with: _"The SV approach encodes the constraint in the model definition itself — the right answer is the only possible answer, regardless of who writes the query."_

## Step 6 — Act 2: The Data Model

Walk through `schema.sql` with inline annotations explaining what each table represents and which columns matter.

Then deploy schema + seed by executing the SQL files directly via `snowflake_sql_execute`. **Do not use `run_snippet.py`** — execute statements directly in the active Snowflake session:

1. Read `schema.sql` and `seed_data.sql`
2. For **standard snippets**: substitute `SNIPPETS.PUBLIC` → `TARGET_DB.TARGET_SCHEMA`, `USE DATABASE SNIPPETS` → `USE DATABASE TARGET_DB`, `USE SCHEMA PUBLIC` → `USE SCHEMA TARGET_SCHEMA` throughout
3. For **access-control snippets** (`caller_rights`, `row_access_policies`): no substitution — execute as-is
4. Run a `USE ROLE TARGET_ROLE` and `USE WAREHOUSE TARGET_WAREHOUSE` before executing
5. Execute each statement via `snowflake_sql_execute`, confirm each succeeds before continuing

After deployment, show 3–5 sample rows from each table:
```sql
SELECT * FROM TARGET_DB.TARGET_SCHEMA.TABLE_NAME LIMIT 5;
```

## Step 7 — Act 3: The SV Pattern

Walk through `semantic_view.sql` **section by section** — TABLES, RELATIONSHIPS, FACTS, DIMENSIONS, METRICS — stopping to explain each novel concept. Don't paste the full file; excerpt and annotate only the parts that are specific to this pattern.

Format each stop as:
> **[Section]** — Here's what this part does: [explanation]
> ```sql
> [excerpt]
> ```
> Key things to notice: [2–3 bullet points]

Then deploy the SV using the format appropriate to `AUTHORING_FORMAT`:

**If DDL:** Read file, substitute `SNIPPETS.PUBLIC` → `TARGET_DB.TARGET_SCHEMA`, execute via `snowflake_sql_execute`.

**If YAML:** Read `semantic_view.yaml`. Substitute `TARGET_DB` and `TARGET_SCHEMA` throughout. Then deploy:
```sql
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
  'TARGET_DB.TARGET_SCHEMA',
  $$ <adapted yaml> $$
);
```
If the YAML has DDL-only features flagged in comments (e.g. `AI_SQL_GENERATION`, `MAX_STALENESS`, `WITH TAG`), note them and execute the corresponding DDL follow-up commands from `semantic_view.sql` after the YAML deploy.

## Step 8 — Act 4: Live Queries

Run each numbered working query from `queries.sql` one at a time using `snowflake_sql_execute`. Before each query:
1. State what it demonstrates
2. Adapt table/SV references if needed (`SNIPPETS.PUBLIC` → `TARGET_DB.TARGET_SCHEMA`)
3. Run it and show the output
4. Narrate what the specific numbers demonstrate — point to concrete rows/values

For queries that include `USE ROLE` switches (access-control snippets), execute those role-switch statements directly via `snowflake_sql_execute` before the query that follows.

After every 2–3 queries, check if the user wants to continue or dig deeper.

## Step 9 — Act 5: Gotchas

Read the `-- GOTCHAS` and `-- HOW ... WORKS` sections from `queries.sql` and the `## What Doesn't Work` section from `README.md`. Present each gotcha plainly: what trap exists, why it happens, how to avoid it.

## Step 10 — Wrap-Up and Cleanup

Summarize in 3–5 key takeaways. Show the `## Docs` links from `README.md`.

Then **always offer cleanup** — list every object created during this tutorial before asking:

For **standard snippets**, list:
- Tables created: `TARGET_DB.TARGET_SCHEMA.TABLE_1`, `TABLE_2`, ...
- Semantic views created: `TARGET_DB.TARGET_SCHEMA.SV_NAME`
- If the database was created fresh: `TARGET_DB` itself
- Do **not** offer to drop a DB that existed before the tutorial — only drop tables/SVs/views you created inside it

For **access-control snippets**, list the full dedicated environment:
- Database: `SV_CALLER_TEST` or `RAP_TEST`
- Warehouse: `SV_CALLER_TEST` or `RAP_TEST_WH`
- Roles: (all roles created)

Ask: _"Want me to clean all of this up now, or leave it so you can keep exploring?"_

If yes: execute the `-- CLEANUP` block from `queries.sql` via `snowflake_sql_execute`. Switch back to SYSADMIN / SECURITYADMIN as needed per the cleanup SQL. Confirm each drop succeeded.

Finally, offer to run a different snippet or switch to Apply mode to adapt the pattern to the user's own tables.

---

# Apply Steps

## A1 — Identify the Pattern

Match the user's request to the closest snippet in the Available Patterns table. If the use case is ambiguous (e.g. "I want year-over-year comparisons"), confirm: "That maps to the `time_intelligence` pattern — role-playing aliases + computed FK facts for SPLY/YoY/MoM. Does that sound right?"

If the user isn't sure which pattern fits, ask them to describe:
- What tables they have and how they relate
- What metric or question they're trying to answer

Then recommend the best-fit snippet with a one-sentence explanation of why.

## A2 — Read the Snippet Reference

Read `snippets/<name>/README.md` in full.

Then read the authoring format file:
- If `AUTHORING_FORMAT = DDL`: read `snippets/<name>/semantic_view.sql`
- If `AUTHORING_FORMAT = YAML`: read `snippets/<name>/semantic_view.yaml` (and note any DDL-only features flagged in comments)

Do NOT read `schema.sql`, `seed_data.sql`, or `queries.sql` — those are for Tutorial mode.

## A3 — Get the User's Existing SV

Ask for their current Semantic View definition. Accept any of:
- Paste DDL or YAML directly into the chat
- A local file path → use `read` to load it
- A Snowflake stage path → use `snowflake_sql_execute` with `GET_DDL('semantic_view', '<name>')`
- Export YAML from an existing SV: `SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('DB.SCHEMA.SV_NAME')`
- "I'm building one from scratch" → ask for table names and a brief description of what they're trying to measure

If they have no existing SV yet, proceed with just the table descriptions — you'll generate the full definition.

## A4 — Map the Pattern to Their Schema

Show the user the core structural roles in the snippet (e.g. for `time_intelligence`: FACT table, date key column, measure columns). Then ask them to map each role to their actual tables/columns:

> Here's what the time intelligence pattern needs:
> | Role | Snippet uses | Your equivalent? |
> |------|-------------|------------------|
> | Fact table | `FACT_SALES` | ? |
> | Date key column | `SALE_MONTH` (DATE) | ? |
> | Measure(s) to compare | `revenue`, `units` | ? |
> | Calendar/date dimension (optional) | `DIM_CALENDAR` | ? |

Ask only for what the pattern actually requires — don't over-ask. If they have a calendar dim, great; if not, note that a self-join on the fact works too.

## A5 — Generate Adapted Definition

Using their mapping, generate fully adapted SV definition in `AUTHORING_FORMAT`:

**If DDL:**
1. **Existing SV**: produce a diff — show the exact blocks that need to be added or modified.
2. **From scratch**: produce complete `CREATE OR REPLACE SEMANTIC VIEW` DDL.

**If YAML:**
1. **Existing SV**: produce a YAML diff — show the table entries, relationship entries, or metric entries to add or modify.
2. **From scratch**: produce complete YAML ready to pass to `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML`.
3. If the pattern has DDL-only features (ASOF/range join, VARIABLES, WITH TAG, etc.), show the YAML base + call out the follow-up DDL commands needed.

For YAML output, always include the deployment snippet at the top:
```sql
-- Verify (dry-run):
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('DB.SCHEMA', $$ <yaml> $$, TRUE);
-- Deploy:
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('DB.SCHEMA', $$ <yaml> $$);
```

Annotate each adapted block with a brief comment explaining what it does and why.

## A6 — Gotchas for Their Case

Read the `## What Doesn't Work` section of the snippet's README. Flag any gotchas that are specifically relevant given their schema (e.g. if they have a non-standard date granularity, a composite key, or a table used in multiple roles).

## A7 — Offer Next Steps

Offer any of:
- Deploy the adapted SV to their account via `snowflake_sql_execute`
- Run test `SEMANTIC_VIEW()` queries against their data to verify the pattern works
- Switch to Tutorial mode if they want to see a live walkthrough with example data first
- Apply a second pattern on top of the same SV

# Best Practices

**Both modes:**
- Be honest about limitations — when a pattern doesn't work or has caveats, explain exactly why
- For ⚠️ Private Preview snippets (`inline_sv`, `materialization`, `scoped_dataset`), note upfront that the user may need to contact their Snowflake account team to enable the feature
- For `caller_rights`, note upfront that it requires ACCOUNTADMIN (or both SECURITYADMIN + SYSADMIN), creates its own dedicated database/warehouse/roles (`SV_CALLER_TEST`), and includes a cleanup block — run it when done
- For `row_access_policies`, note upfront that it requires ACCOUNTADMIN (or both SECURITYADMIN + SYSADMIN), creates roles (`REGION_A_ANALYST`, `REGION_B_ANALYST`) and a dedicated database (`RAP_TEST`), and grants those roles USAGE on an existing warehouse — no new warehouse is created
- Match the user's energy — if they're exploring, be expansive; if they're in a hurry, be terse

**Tutorial mode:**
- Teach, don't just execute — every output needs a sentence explaining what it means
- Connect abstract to concrete: "Notice how `yoy_pct` for Jan 2024 is +12.4% — East revenue went from 105,000 to 118,000"
- Keep momentum — check in for pacing but don't block on confirmations

**Apply mode:**
- Never rewrite their whole SV unprompted — make surgical additions only
- When mapping their schema to the snippet's roles, use their exact column/table names throughout; don't revert to snippet names like `FACT_SALES` or `SALE_MONTH` in the output DDL
- If their schema has edge cases the snippet doesn't cover (composite keys, non-standard date grains, many-to-many relationships), flag it explicitly rather than silently generating broken DDL
- Always verify: after generating adapted DDL, ask "Does this mapping look right before I generate the full DDL?" unless the mapping is obvious

# Examples

## Example 1: Named snippet
User: `$sv-patterns walk me through time intelligence`
Assistant: Reads all five files, presents the problem (no PREVIOUSYEAR in SVs), shows BI tool equivalents, deploys schema/seed, annotates the role-playing alias + computed-FK FACT pattern, runs live SPLY/YoY queries, explains the results.

## Example 2: Concept match
User: `$sv-patterns how do I handle SCD2 dimensions`
Assistant: Matches to `range_join`, runs the full tutorial showing BETWEEN EXCLUSIVE range relationships and how historical dimension versions auto-resolve.

## Example 3: Discovery
User: `$sv-patterns what snippets do you have`
Assistant: Lists all 22 patterns with one-line descriptions, asks which one to walk through.

## Example 4: Apply mode — existing SV
User: `$sv-patterns help me add year-over-year to my existing SV`
Assistant: Matches to `time_intelligence`, reads the snippet reference (README + semantic_view.sql only). Asks the user to paste their SV DDL. Shows the mapping table (fact table, date key, measures). User fills in their names. Generates only the new FACTS + METRICS blocks and an updated TABLES/RELATIONSHIPS section with the `_ly` role-playing alias — as a diff against their existing DDL. Explains what each change does and flags the computed-FK gotcha.

## Example 5: Apply mode — building from scratch
User: `$sv-patterns I'm building a SV for subscription churn analysis — I have a subscriptions table and a customers table. What pattern do I need?`
Assistant: Asks clarifying questions (what's the grain? what do you want to measure?). Determines `semi_additive_metric` (NON ADDITIVE BY) fits for a subscriber headcount metric that shouldn't sum across time. Reads the snippet, maps their tables, generates full SV DDL with their table/column names.

## Example 6: Ambiguous trigger → mode clarification
User: `$sv-patterns time intelligence`
Assistant: "Do you want me to walk you through the time intelligence pattern with a working example deployed to your account, or help you apply it directly to your own Semantic View?"
