---
id: semantic-view-patterns
name: semantic-view-patterns
skill-name: $sv-patterns
description: Two modes for 21 Snowflake Semantic View modeling patterns — Tutorial mode deploys working examples and explains them live; Apply mode adapts a pattern to the user's own tables and generates ready-to-use DDL.
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

Interactive, end-to-end tutorials for 18 Snowflake Semantic View modeling patterns. Each tutorial deploys a working example into your Snowflake account, walks through the annotated DDL, runs live queries, and surfaces what works and what doesn't.

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

A library of 21 executable, self-contained Semantic View modeling patterns bundled alongside this skill, each with:
- Real problem statement and BI tool comparison
- Minimal but realistic seed data
- Fully annotated SV DDL
- Working `SEMANTIC_VIEW()` queries with live output
- Explicit gotchas and what-doesn't-work notes

**Tutorial mode**: The `run_snippet.py` script deploys each snippet into a user-specified database/schema, then runs live queries so the user can see the pattern in action against real data.

**Apply mode**: The snippet files serve as annotated reference patterns. The skill reads the user's existing SV DDL, maps the snippet's structure to their tables/columns, and generates adapted DDL ready to paste or deploy — no example data needed.

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

# Instructions

## Step 0 — Detect Mode

Before doing anything else, determine which mode the user wants:

- If the user said something like "walk me through", "teach me", "explain", "show me in action", "what snippets" → **Tutorial mode** → go to Tutorial Steps
- If the user said something like "apply to my SV", "add X to my semantic view", "my tables are...", "help me implement", "update my SV" → **Apply mode** → go to Apply Steps
- If ambiguous (e.g. "help me with time intelligence"), ask:
  > "Do you want me to walk you through the time intelligence pattern with a live example, or help you apply it directly to your own Semantic View?"

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

## Step 3 — Ask for Connection and Target Schema (First Time Only)

Ask two questions upfront before deploying anything.

**Question 1 — Which Snowflake account should I use?**

Cortex Code may be running on a different Snowflake account than the one the user wants to deploy and run the tutorial against. Ask:

> "Which Snowflake connection should I use? This is the account where I'll deploy the tables and run the queries."

Options to present:
1. **Default connection** — the active Cortex Code account (no connection name needed)
2. **Named connection** — user specifies a connection name from their `~/.snowflake/connections.toml` (e.g. `pm`, `dev`, `prod`)

Store as `TARGET_CONNECTION` (empty string = default, otherwise the named connection).
- Use `TARGET_CONNECTION` in all `snowflake_sql_execute` calls via the `connection` parameter
- Pass as `--connection <TARGET_CONNECTION>` to `run_snippet.py` (omit the flag entirely if default)

**Question 2 — Where in that account should I deploy the tables?**

> "Where should I deploy the snippet tables? I recommend the Snowflake Learning Environment (`SNOWFLAKE_LEARNING_DB`) — it's pre-provisioned on most accounts and requires no setup. Or specify any database and schema."

Options:
1. **Snowflake Learning Environment** (recommended) — `SNOWFLAKE_LEARNING_DB.PUBLIC`, uses pre-provisioned role `SNOWFLAKE_LEARNING_ROLE` and warehouse `SNOWFLAKE_LEARNING_WH`
2. **Custom location** — user specifies any `DB.SCHEMA`

If they choose the Learning Environment, set `TARGET_DB = SNOWFLAKE_LEARNING_DB`, `TARGET_SCHEMA = PUBLIC`. Note they may need `USE ROLE SNOWFLAKE_LEARNING_ROLE` if their session doesn't have access.

If they choose a custom location, ask for `DB` and `SCHEMA`. If the DB doesn't exist, `run_snippet.py` will create it.

Store `TARGET_CONNECTION`, `TARGET_DB`, and `TARGET_SCHEMA` for the rest of the session — don't re-ask.

## Step 4 — Read All Five Files

Before presenting anything, read all five files for the chosen snippet:
- `snippets/<name>/README.md`
- `snippets/<name>/schema.sql`
- `snippets/<name>/seed_data.sql`
- `snippets/<name>/semantic_view.sql`
- `snippets/<name>/queries.sql`

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

Then deploy schema + seed using `run_snippet.py`:

```bash
python <skill_dir>/run_snippet.py <snippet> --step schema --db <TARGET_DB> --schema <TARGET_SCHEMA>
python <skill_dir>/run_snippet.py <snippet> --step seed   --db <TARGET_DB> --schema <TARGET_SCHEMA>
```

After deployment, show 3–5 sample rows from each table using `snowflake_sql_execute`:
```sql
SELECT * FROM <TARGET_DB>.<TARGET_SCHEMA>.<TABLE> LIMIT 5;
```

## Step 7 — Act 3: The SV Pattern

Walk through `semantic_view.sql` **section by section** — TABLES, RELATIONSHIPS, FACTS, DIMENSIONS, METRICS — stopping to explain each novel concept. Don't paste the full file; excerpt and annotate only the parts that are specific to this pattern.

Format each stop as:
> **[Section]** — Here's what this part does: [explanation]
> ```sql
> [excerpt]
> ```
> Key things to notice: [2–3 bullet points]

Then deploy the SV:
```bash
python <skill_dir>/run_snippet.py <snippet> --step sv --db <TARGET_DB> --schema <TARGET_SCHEMA>
```

## Step 8 — Act 4: Live Queries

Run each numbered working query from `queries.sql` one at a time using `snowflake_sql_execute`. Before each query:
1. State what it demonstrates
2. Run it (substitute `SNIPPETS.PUBLIC` → `<TARGET_DB>.<TARGET_SCHEMA>`)
3. Show the output
4. Narrate what the specific numbers demonstrate — point to concrete rows/values

After every 2–3 queries, check if the user wants to continue or dig deeper.

## Step 9 — Act 5: Gotchas

Read the `-- GOTCHAS` and `-- HOW ... WORKS` sections from `queries.sql` and the `## What Doesn't Work` section from `README.md`. Present each gotcha plainly: what trap exists, why it happens, how to avoid it.

## Step 10 — Wrap-Up

Summarize in 3–5 key takeaways. Show the `## Docs` links from `README.md`. Offer to run a different snippet or switch to Apply mode to adapt the pattern to the user's own tables.

---

# Apply Steps

## A1 — Identify the Pattern

Match the user's request to the closest snippet in the Available Patterns table. If the use case is ambiguous (e.g. "I want year-over-year comparisons"), confirm: "That maps to the `time_intelligence` pattern — role-playing aliases + computed FK facts for SPLY/YoY/MoM. Does that sound right?"

If the user isn't sure which pattern fits, ask them to describe:
- What tables they have and how they relate
- What metric or question they're trying to answer

Then recommend the best-fit snippet with a one-sentence explanation of why.

## A2 — Read the Snippet Reference

Read `snippets/<name>/README.md` and `snippets/<name>/semantic_view.sql` in full. These are your reference for the pattern — understand the structural intent before touching the user's data.

Do NOT read `schema.sql`, `seed_data.sql`, or `queries.sql` — those are for Tutorial mode.

## A3 — Get the User's Existing SV

Ask for their current Semantic View DDL. Accept any of:
- Paste directly into the chat
- A local file path → use `read` to load it
- A Snowflake stage path → use `snowflake_sql_execute` with `GET_DDL('semantic_view', '<name>')`
- "I'm building one from scratch" → ask for table names and a brief description of what they're trying to measure

If they have no existing SV yet, proceed with just the table descriptions — you'll generate the full SV DDL.

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

## A5 — Generate Adapted DDL

Using their mapping, generate fully adapted SV DDL:

1. **If they have an existing SV**: produce a diff — show the exact blocks (TABLES, RELATIONSHIPS, FACTS, METRICS) that need to be added or modified. Don't rewrite the whole SV; just show the changes.
2. **If they're starting from scratch**: produce the complete SV DDL with their table/column names substituted throughout.

Annotate each adapted block with a brief comment explaining what it does and why, mirroring the style of the snippet's own inline comments.

After presenting the DDL, summarize in plain English what changed and why each change was needed.

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
Assistant: Lists all 18 patterns with one-line descriptions, asks which one to walk through.

## Example 4: Apply mode — existing SV
User: `$sv-patterns help me add year-over-year to my existing SV`
Assistant: Matches to `time_intelligence`, reads the snippet reference (README + semantic_view.sql only). Asks the user to paste their SV DDL. Shows the mapping table (fact table, date key, measures). User fills in their names. Generates only the new FACTS + METRICS blocks and an updated TABLES/RELATIONSHIPS section with the `_ly` role-playing alias — as a diff against their existing DDL. Explains what each change does and flags the computed-FK gotcha.

## Example 5: Apply mode — building from scratch
User: `$sv-patterns I'm building a SV for subscription churn analysis — I have a subscriptions table and a customers table. What pattern do I need?`
Assistant: Asks clarifying questions (what's the grain? what do you want to measure?). Determines `semi_additive_metric` (NON ADDITIVE BY) fits for a subscriber headcount metric that shouldn't sum across time. Reads the snippet, maps their tables, generates full SV DDL with their table/column names.

## Example 6: Ambiguous trigger → mode clarification
User: `$sv-patterns time intelligence`
Assistant: "Do you want me to walk you through the time intelligence pattern with a working example deployed to your account, or help you apply it directly to your own Semantic View?"
