# Tags on Metrics

## The Problem

You have dozens of metrics across a Semantic View. You need a way to:
- Track **ownership** ("who is responsible for `store_revenue`?")
- Communicate **certification status** ("is this metric approved for reporting?")
- Enable **governance discovery** ("show me all certified finance metrics")

**WITH TAG** attaches Snowflake governance tag key-value pairs directly to metrics in the SV DDL. These are queryable via `tag_references()` using standard Snowflake governance tooling.

## How You Might Express This Need

- "Mark our finance-owned metrics as 'certified' and analytics-owned ones as 'in_development'"
- "I want to build a data catalog that shows which SV metrics are ready for production"
- "Alert me if anyone queries a 'deprecated' metric in their BI tool"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **dbt** | `meta: {owner: ..., tier: ...}` in metrics YAML |
| **Atlan / Alation** | Manual tag assignment in catalog UI |
| **Power BI / Tableau** | Certification flags in dataset/workbook metadata |
| **LookML** | `tags: ["certified", "finance"]` on measures |

## The SV Approach

**Step 1: Create the tags** (one-time DDL):
```sql
CREATE TAG metric_owner;
CREATE TAG metric_status;
```

**Step 2: Apply tags in the SV METRICS block:**
```sql
store_revenue AS SUM(revenue)
    WITH SYNONYMS ('store revenue')
    WITH TAG (metric_owner = 'finance_team', metric_status = 'certified'),
```

**Step 3: Query tags via `tag_references()`:**
```sql
SELECT OBJECT_NAME, TAG_NAME, TAG_VALUE
FROM TABLE(SNIPPETS.INFORMATION_SCHEMA.TAG_REFERENCES(
    'SNIPPETS.PUBLIC.CHANNEL_SALES_TAGGED_SV!TAG_STORE_SALES.STORE_REVENUE',
    'semantic metric'
));
```

## `tag_references()` Object Name Format

```
'DATABASE.SCHEMA.VIEW_NAME!ENTITY_TABLE.METRIC_LOGICAL_NAME'
```

The `!` separates the SV fully-qualified name from the metric reference.

## Docs

- [CREATE SEMANTIC VIEW — WITH TAG clause](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#with-tag)
- [TAG_REFERENCES function](https://docs.snowflake.com/en/sql-reference/functions/tag_references)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | Tag objects, store/web sales tables, date dim |
| `seed_data.sql` | 4 months × 2 channels |
| `semantic_view.sql` | SV with 5 tagged metrics (3 owners, 2 statuses) |
| `queries.sql` | SV queries + `tag_references()` discovery queries |
