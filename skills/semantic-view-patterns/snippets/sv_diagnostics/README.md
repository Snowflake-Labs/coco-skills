# SV Diagnostics

## The Problem

You've written a Semantic View. It deploys without errors. But then an analyst reports that a query isn't working, or Cortex Analyst gives them the wrong answer, or the numbers don't add up. Where do you start?

This snippet is a diagnostic reference: four real failure modes, the exact error messages they produce, how to tell them apart, and what to fix.

**How You Might Express This Need:**
- "My SV deployed fine but queries are erroring — what's wrong?"
- "Cortex Analyst keeps saying it can't answer my question."
- "I added a date dimension and now nothing works."
- "My revenue numbers look wrong when I break down by product."
- "How do I know if my model is structured correctly before I ship it?"

---

## The Four Diagnostics

### 1. Ambiguous Path Relationship

**Symptom:** The SV deploys. Queries without date dimensions work fine. The moment an analyst tries to group by `year` or `month`, it errors.

**Error:**
```
Invalid dimension specified: Multi-path relationship between the dimension
entity 'DATE_DIM' and the base metric or dimension entity 'DEALS' is not supported.
```

**Root cause:** A fact table has two FKs that both reference the same dimension table (e.g., `CREATED_DATE` and `CLOSE_DATE` both pointing to `DIM_DATE`). Two relationships exist with no disambiguation. The engine doesn't know which path to follow when aggregating.

**Why it's insidious:** Many queries succeed. The bug hides until someone tries a time-series breakdown — often the most important type of analysis.

**Fix:** Add `USING (relationship)` to every metric, explicitly declaring which date path that metric uses.

```sql
-- BROKEN: no USING — ambiguous which date to use
deals.total_amount AS SUM(AMOUNT)

-- FIXED: USING before AS — each metric owns its date path
deals.total_amount_created USING (deals_to_created_date) AS SUM(AMOUNT)
deals.total_amount_closed  USING (deals_to_close_date)   AS SUM(AMOUNT)
```

> **See also:** `multi_path_metrics` snippet for the full USING pattern, and `accumulating_snapshot` for USING applied to a multi-milestone fact table.

---

### 2. Fan Trap

**Symptom:** Querying a metric grouped by a dimension from a "downstream" table errors at query time.

**Error:**
```
Invalid dimension specified: The dimension entity 'PRODUCTS' must be related to
and have an equal or lower level of granularity compared to the base metric or
dimension entity 'DEALS'.
```

**Root cause:** The metric is defined at a coarser grain than the dimension it's being grouped by. Classic example: revenue lives at the `DEALS` header (one row per deal), but `DIM_PRODUCT` is only reachable through `DEAL_ITEMS` (many rows per deal). Joining DEALS revenue through DEAL_ITEMS to get to products would fan out and multiply the revenue — the SV engine refuses.

**Distinguishing from Scenario 3:** Same error message. Diagnosis: check your RELATIONSHIPS clause. In a fan trap, the relationship exists but at the wrong grain. In Scenario 3, the relationship is simply missing.

**Fix:** Move the metric to the table that directly joins the dimension — in this case, `DEAL_ITEMS.LINE_AMOUNT` rather than `DEALS.AMOUNT`.

```sql
-- BROKEN: metric at DEALS grain, dimension only reachable via DEAL_ITEMS
FACTS   ( deals.amount AS AMOUNT )
METRICS ( deals.total_amount AS SUM(AMOUNT) )  -- can't group by products.category

-- FIXED: metric at DEAL_ITEMS grain — same level as DIM_PRODUCT
FACTS   ( deal_items.line_amount AS LINE_AMOUNT )
METRICS ( deal_items.total_revenue AS SUM(LINE_AMOUNT) )  -- can group by products.category ✓
```

**What this means for model design:** If you only have header-level revenue and still want product analysis, the data must be restructured to capture per-product amounts at the line-item level. The SV can't manufacture a product breakdown from a single deal total.

---

### 3. Table With No Relationship

**Symptom:** A table is listed in the `TABLES` clause. Dimensions from it are defined. The SV deploys. But querying those dimensions errors.

**Error:**
```
Invalid dimension specified: The dimension entity 'DIM_REGION' must be related
to and have an equal or lower level of granularity compared to the base metric
or dimension entity 'DEALS'.
```

**Root cause:** `DIM_REGION` was added to `TABLES` but no `RELATIONSHIPS` entry connects it to the rest of the model. The SV engine can't build a join path.

**Distinguishing from Scenario 2:** Same error message. Diagnosis: search the `RELATIONSHIPS` clause for the orphaned table's name — it won't appear on either side of any relationship definition.

**Fix:** Either add the missing relationship or remove the orphaned table from `TABLES`.

```sql
-- BROKEN: no relationship for dim_region
RELATIONSHIPS (
    deals_to_rep AS deals(REP_ID) REFERENCES rep_dim(REP_ID)
    -- dim_region is not connected to anything
)

-- FIXED: add the missing link
RELATIONSHIPS (
    deals_to_rep    AS deals(REP_ID)   REFERENCES rep_dim(REP_ID)
    , rep_to_region AS rep_dim(REGION) REFERENCES dim_region(REGION_CODE)
)
```

---

### 4. Duplicate Names and Ambiguous Synonyms

This scenario has two flavors with very different consequences.

#### 4a. Duplicate Logical Name — Deploy-Time Error

**Symptom:** The `CREATE SEMANTIC VIEW` statement fails immediately.

**Error:**
```
SQL compilation error: invalid identifier '<name>'
```

**Root cause:** Two dimensions (or two metrics, or a dimension and a metric) in the same SV share the same logical name. Logical names must be globally unique within a SV — even across different entities.

**Fix:** Give each definition a unique logical name that reflects its entity context.

```sql
-- BROKEN: both claim "segment" as the logical name
rep_dim.segment  AS REGION    -- logical: "segment" ← first definition
products.segment AS CATEGORY  -- logical: "segment" ← duplicate → deploy error

-- FIXED: entity-scoped logical names
rep_dim.rep_segment      AS REGION    -- logical: "rep_segment"
products.product_segment AS CATEGORY  -- logical: "product_segment"
```

#### 4b. Overlapping Synonyms — Cortex Analyst Ambiguity

**Symptom:** The SV deploys. SQL queries work fine. But Cortex Analyst refuses to answer natural language questions, responding with an ambiguity explanation.

**CA response (example):**
```
The term 'segment' is ambiguous. It could refer to 'product_segment'
(the product category/segment) or 'rep_segment' (the rep region/territory/segment).
Could you clarify which segment you mean?
```

**Root cause:** Two or more definitions share the same synonym (e.g., both `rep_segment` and `product_segment` claim `'segment'`, `'area'`). Or two metrics both claim `'revenue'` and `'total revenue'`. CA's disambiguation logic can't pick one and refuses rather than guess.

**What CA gets right:** It never silently picks the wrong metric. When synonyms collide, CA tells the user exactly what the conflict is. This is correct behavior — but it means your analysts hit a wall instead of getting an answer.

**Fix:** Give each definition a synonym set that is unique and scoped. Avoid sharing high-value terms (`revenue`, `count`, `total`, `segment`, `area`) across multiple definitions in the same SV.

```sql
-- BROKEN: "revenue" and "total revenue" claimed by both
deals.total_amount     ... WITH SYNONYMS ('revenue', 'total revenue', 'sales')
deal_items.total_revenue ... WITH SYNONYMS ('revenue', 'total revenue', 'product revenue')

-- FIXED: non-overlapping synonym sets
deals.deal_value       ... WITH SYNONYMS ('deal value', 'total deal value', 'pipeline value')
deal_items.product_revenue ... WITH SYNONYMS ('product revenue', 'revenue by product', 'line item revenue')
```

---

## Diagnostic Cheat Sheet

| Error Message | Possible Causes | How to Tell Apart | Fix |
|---|---|---|---|
| "Multi-path relationship ... not supported" | Two relationships to same dim, no USING | Only one cause — check RELATIONSHIPS for duplicate target | Add USING to each metric |
| "Dimension entity must be related to and have equal or lower granularity" | Fan trap OR missing relationship | Check RELATIONSHIPS clause — is the table present at all? | Fan trap → move metric to bridge grain. Missing rel → add the relationship |
| "invalid identifier" at CREATE time | Duplicate logical name | Scan DIMENSIONS/METRICS for repeated names | Rename to entity-scoped logical names |
| CA refuses with ambiguity explanation | Overlapping synonyms | Scan WITH SYNONYMS across all definitions for shared terms | Remove shared terms; give each definition a unique synonym set |

---

## Pre-Deployment Checklist

Before running `CREATE SEMANTIC VIEW`, scan your DDL for these patterns:

- [ ] **Every fact table with two+ date FKs**: does every metric have `USING`?
- [ ] **Every table in TABLES**: does it appear in at least one RELATIONSHIP?
- [ ] **Every metric**: is it defined on the entity at the same or lower grain as the dimensions it will be grouped by?
- [ ] **Every logical name in DIMENSIONS and METRICS**: is it globally unique within the SV?
- [ ] **Every synonym**: does it appear in only one definition? Check especially: `revenue`, `count`, `total`, `amount`, `segment`, `type`, `name`, `date`.

---

## What Doesn't Work

- **Pre-deployment dry-run**: There is no `VALIDATE SEMANTIC VIEW` command. The only way to test deploy-time errors is to attempt the `CREATE`. For query-time errors, you must deploy first and then run test queries.

- **DESCRIBE as a validator**: `DESCRIBE SEMANTIC VIEW` shows structure after deployment but cannot detect query-time issues like fan traps or ambiguous paths. Use it to confirm your logical names and relationships are registered correctly, not to catch errors.

- **Fixing overlapping synonyms at query time**: Once the SV is deployed with ambiguous synonyms, CA will refuse those queries until the SV is altered. There is no per-query synonym override.

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
| `semantic_view.sql` | All broken and fixed SVs for all four scenarios |
| `queries.sql` | Error-triggering queries with exact error messages, then fixed queries with verified output |
