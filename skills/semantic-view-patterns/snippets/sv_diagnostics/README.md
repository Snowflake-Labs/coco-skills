# SV Diagnostics

## The Problem

You've written a Semantic View. It deploys without errors. But then an analyst reports that a query isn't working, or Cortex Analyst gives them the wrong answer, or the numbers don't add up. Where do you start?

This snippet is a diagnostic reference: six real failure modes, the exact error messages they produce, how to tell them apart, and what to fix.

**How You Might Express This Need:**
- "My SV deployed fine but queries are erroring — what's wrong?"
- "Cortex Analyst keeps saying it can't answer my question."
- "I added a date dimension and now nothing works."
- "My revenue numbers look wrong when I break down by product."
- "The fan trap error went away after I changed something, but now I'm not sure the numbers are right."
- "How do I know if my model is structured correctly before I ship it?"

---

## The Six Diagnostics

### 1. Ambiguous Path Relationship

**Symptom:** The SV deploys. Queries without date dimensions work fine. The moment an analyst tries to group by `year` or `month`, it errors.

**Error:**
```
Invalid dimension specified: Multi-path relationship between the dimension
entity 'DATE_DIM' and the base metric or dimension entity 'DEALS' is not supported.
```

**Root cause:** A fact table has two FKs that both reference the same dimension table (e.g., `CREATED_DATE` and `CLOSE_DATE` both pointing to `DIM_DATE`). Two relationships exist with no disambiguation.

**Why it's insidious:** Many queries succeed. The bug hides until someone tries a time-series breakdown.

**Fix:** Add `USING (relationship)` to every metric, explicitly declaring which date path that metric uses.

```sql
-- BROKEN: no USING — ambiguous at query time
deals.total_amount AS SUM(AMOUNT)

-- FIXED: USING before AS — each metric owns its date path
deals.total_amount_created USING (deals_to_created_date) AS SUM(AMOUNT)
deals.total_amount_closed  USING (deals_to_close_date)   AS SUM(AMOUNT)
```

> **See also:** `multi_path_metrics` snippet for the full USING pattern; `accumulating_snapshot` for USING on a multi-milestone fact table.

---

### 2. Fan Trap

**Symptom:** Querying a metric grouped by a "downstream" dimension errors at query time.

**Error:**
```
Invalid dimension specified: The dimension entity 'PRODUCTS' must be related to
and have an equal or lower level of granularity compared to the base metric or
dimension entity 'DEALS'.
```

**Root cause:** The metric is at a coarser grain than the dimension it's being grouped by. Revenue lives at the `DEALS` header (one row per deal), but `DIM_PRODUCT` is only reachable through `DEAL_ITEMS` (many rows per deal). The SV engine detects the potential fan-out and refuses.

**Distinguishing from Scenario 3:** Same error message. Check your RELATIONSHIPS clause — in a fan trap the relationship exists but at the wrong grain; in Scenario 3 the relationship is simply missing.

**Fix:** Move the metric to the table that directly joins the dimension.

```sql
-- BROKEN: metric at DEALS grain, dimension only reachable via DEAL_ITEMS
FACTS   ( deals.amount AS AMOUNT )
METRICS ( deals.total_amount AS SUM(AMOUNT) )  -- can't group by products.category

-- FIXED: metric at DEAL_ITEMS grain — same level as DIM_PRODUCT
FACTS   ( deal_items.line_amount AS LINE_AMOUNT )
METRICS ( deal_items.total_revenue AS SUM(LINE_AMOUNT) )  -- ✓
```

---

### 3. Table With No Relationship

**Symptom:** A table is listed in `TABLES` and its dimensions are defined, but any query using those dimensions errors.

**Error:**
```
Invalid dimension specified: The dimension entity 'DIM_REGION' must be related
to and have an equal or lower level of granularity compared to the base metric
or dimension entity 'DEALS'.
```

**Root cause:** The table was added to `TABLES` but no `RELATIONSHIPS` entry connects it. The engine can't build a join path.

**Distinguishing from Scenario 2:** Same error message. Search the `RELATIONSHIPS` clause for the orphaned table's name — it won't appear on either side of any relationship.

**Fix:** Add the missing relationship, or remove the orphaned table from `TABLES`.

```sql
-- BROKEN: no relationship for dim_region
RELATIONSHIPS (
    deals_to_rep AS deals(REP_ID) REFERENCES rep_dim(REP_ID)
)

-- FIXED: add the missing link
RELATIONSHIPS (
    deals_to_rep    AS deals(REP_ID)   REFERENCES rep_dim(REP_ID)
    , rep_to_region AS rep_dim(REGION) REFERENCES dim_region(REGION_CODE)
)
```

---

### 4. Duplicate Names and Ambiguous Synonyms

Two flavors with different consequences.

#### 4a. Duplicate Logical Name — Deploy-Time Error

**Error:** `SQL compilation error: invalid identifier '<name>'` at CREATE time.

**Root cause:** Two dimensions (or metrics, or a dimension and a metric) share the same logical name. Logical names must be globally unique within a SV.

**Fix:** Entity-scope your logical names.

```sql
-- BROKEN
rep_dim.segment  AS REGION    -- logical: "segment" — duplicate
products.segment AS CATEGORY  -- logical: "segment" — duplicate → deploy error

-- FIXED
rep_dim.rep_segment      AS REGION
products.product_segment AS CATEGORY
```

#### 4b. Overlapping Synonyms — Cortex Analyst Ambiguity

**Symptom:** SV deploys, SQL queries work. Cortex Analyst refuses natural language questions.

**CA response:**
```
The term 'segment' is ambiguous. It could refer to 'product_segment' or
'rep_segment'. Could you clarify which segment you mean?
```

**Root cause:** Multiple definitions share the same synonym. CA never silently picks the wrong one — it refuses. This is correct behavior, but your analysts hit a wall.

**Fix:** Give each definition a synonym set that is unique and scoped. Never share high-value terms (`revenue`, `total`, `count`, `segment`, `area`) across multiple definitions.

---

### 5. Wrong Relationship Direction and Wrong Cardinality

Two flavors. One fails loudly; the other is the most dangerous issue in this entire guide.

#### 5a. Reversed Direction — Deploy-Time Error

**Error:**
```
The referenced key in the relationship 'REP_DIM REFERENCES DEALS' must be the
primary or unique key of the referenced entity.
```

**Root cause:** The relationship direction is flipped — the dimension table is on the left of `REFERENCES`, pointing to the fact table on the right. The engine enforces that the RHS of `REFERENCES` must be a declared PK/UK. Since `DEALS.REP_ID` is not a PK of DEALS, it errors immediately.

**The guardrail limit:** This protection only works when the FK column is not the PK of its own table. If both sides happen to declare the same column as PK (Scenario 5b), the engine can't detect the lie.

**Fix:** Always write relationships as `many_side(FK) REFERENCES one_side(PK)`. The right-hand side is always the dimension/parent primary key.

#### 5b. Wrong Cardinality (Lying About the PK) — Silent Wrong Results

**This is the most dangerous diagnostic in this guide. No error. Ever.**

**Symptom:** The SV deploys. Most queries return correct results. But certain queries — specifically, header-level metrics grouped by fine-grain dimensions — return silently inflated numbers.

**Root cause:** `DEAL_ITEMS` has `ITEM_ID` as its real PK (many items per deal). The modeler accidentally declares `PRIMARY KEY (DEAL_ID)` instead — asserting 1:1 with DEALS. Snowflake doesn't enforce PK uniqueness, so the model deploys.

The SV engine uses the declared PK to assess join cardinality. Believing the relationship is 1:1, it disables its fan trap guard. The exact query that would correctly error on a properly-declared model now runs — and inflates every number by the average number of items per parent row:

```
Correct model  → deals.total_amount by products.category → ERROR (fan trap caught ✓)
Wrong PK model → deals.total_amount by products.category → $430k instead of ~$240k ✗
```

Multi-item deals get their `AMOUNT` counted once per item. The numbers look plausible. They're wrong.

**Detection:** Compare the SV metric total against a raw `SELECT SUM(...)` on the table. If they don't match when grouping across all rows, there's a cardinality lie in your `TABLES` clause.

```sql
-- Detection query: does the SV total match raw SQL?
SELECT SUM(amount) FROM DEALS;  -- should equal SV total_amount with no grouping
```

**Fix:** Declare `PRIMARY KEY` on the column that is actually unique in that table. For bridge and line-item tables, that is the surrogate item key — not the FK back to the parent.

```sql
-- WRONG: declaring the FK column as PK
deal_items AS DEAL_ITEMS PRIMARY KEY (DEAL_ID)   -- DEAL_ID is not unique in DEAL_ITEMS

-- CORRECT: declare the actual unique key
deal_items AS DEAL_ITEMS PRIMARY KEY (ITEM_ID)   -- ITEM_ID is unique ✓
```

---

### 6. Forgotten Semi-Additive Behavior

**No error. No query failure. No CA refusal. Just wrong answers.**

This is a model design review item, not a detectable error. Ask this question for every `FACT` and `METRIC` before you deploy:

> **"Does this column represent a SNAPSHOT or a FLOW?"**

| Type | Examples | Correct aggregation |
|------|----------|-------------------|
| **Flow** — accumulates over time | Revenue, quantity sold, transactions | `SUM` ✓ |
| **Snapshot** — point-in-time | Account balance, headcount, inventory, open pipeline | `SUM` across time = **wrong** |

Summing daily account balances across 30 days gives a number 30× too large. The same deal counted in open pipeline every day it's open gets multiplied by its age in days.

**The fix:** Use `NON ADDITIVE BY` in your metric definition. See the `semi_additive_metric` snippet for the full pattern. The checklist question above is your trigger to go look at that snippet.

---

## Diagnostic Cheat Sheet

| Error / Symptom | Possible Causes | How to Tell Apart | Fix |
|---|---|---|---|
| "Multi-path relationship not supported" | Two relationships to same dim, no USING | Only one root cause — check RELATIONSHIPS for duplicate target | Add USING to each metric |
| "Dimension must be equal or lower granularity" | Fan trap OR missing relationship | Check RELATIONSHIPS — is the table connected at all? | Fan trap → move metric to bridge grain; Missing rel → add relationship |
| "invalid identifier" at CREATE time | Duplicate logical name | Scan DIMENSIONS/METRICS for repeated names | Entity-scope all logical names |
| CA refuses with ambiguity explanation | Overlapping synonyms | Scan WITH SYNONYMS for shared terms | Remove shared terms; unique synonym sets per definition |
| "Referenced key must be PK/UK" at CREATE time | Reversed relationship direction | FK is on the RHS instead of LHS | Flip: `many(FK) REFERENCES one(PK)` |
| No error, silently inflated numbers | Wrong PK declaration (cardinality lie) | Compare SV total to raw SQL total | Declare PK on the actually-unique column |
| No error, subtly wrong aggregations over time | Snapshot metric using SUM | Ask: snapshot or flow? | Use `NON ADDITIVE BY` — see `semi_additive_metric` snippet |

---

## Pre-Deployment Checklist

Before running `CREATE SEMANTIC VIEW`, scan your DDL for these patterns:

- [ ] **Every fact table with two or more date FKs**: does every metric have `USING`?
- [ ] **Every table in TABLES**: does it appear in at least one RELATIONSHIP?
- [ ] **Every metric**: is it defined at the same or lower grain as the dimensions it will be grouped by?
- [ ] **Every logical name in DIMENSIONS and METRICS**: is it globally unique within the SV?
- [ ] **Every synonym**: does it appear in only one definition? Check especially: `revenue`, `count`, `total`, `amount`, `segment`, `type`, `name`, `date`, `area`.
- [ ] **Every RELATIONSHIP**: is it written as `many_side(FK) REFERENCES one_side(PK)`?
- [ ] **Every PRIMARY KEY declaration in TABLES**: is it the column that is actually unique in that table (not a FK, not a non-unique attribute)?
- [ ] **Every FACT and METRIC**: is it a flow (SUM is correct) or a snapshot (needs NON ADDITIVE BY)?

---

## What Doesn't Work

- **Pre-deployment dry-run**: There is no `VALIDATE SEMANTIC VIEW` command. The only way to test deploy-time errors is to attempt `CREATE`. For query-time errors, deploy first and run test queries.

- **DESCRIBE as a validator**: `DESCRIBE SEMANTIC VIEW` shows structure after deployment but cannot detect query-time issues like fan traps or ambiguous paths.

- **PK enforcement**: Snowflake does not enforce PRIMARY KEY uniqueness on tables. The SV engine trusts whatever you declare. A wrong PK declaration deploys silently and disables cardinality guards — always verify your PK declarations against actual data.

- **Fixing overlapping synonyms at query time**: Once deployed with ambiguous synonyms, CA will refuse those questions until the SV is altered.

---

## Docs

- [Semantic View DDL reference](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view)
- [SEMANTIC_VIEW() table function](https://docs.snowflake.com/en/sql-reference/functions/semantic_view)
- [Cortex Analyst — semantic views](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/semantic-model-spec)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `DIM_REP`, `DIM_PRODUCT`, `DIM_REGION` (orphaned), `DEALS`, `DEAL_ITEMS` |
| `seed_data.sql` | 4 reps, 4 products, 12 deals, 18 line items, 15 DIM_DATE rows |
| `semantic_view.sql` | All broken and fixed SVs for scenarios 1–5; scenario 6 is checklist only |
| `queries.sql` | Error-triggering queries with exact messages, fixed queries with verified output, and the semi-additive checklist |
