# AI Metadata in DDL

## The Problem

Out-of-the-box, Cortex Analyst uses the SV's metric/dimension definitions to generate SQL. But you want to:
1. **Steer query style** (e.g. always round amounts, never include refunded orders)
2. **Control topic scope** (reject or redirect off-topic questions)
3. **Pre-approve SQL** for common questions so the AI reuses exact, verified SQL instead of regenerating

## The Three AI Metadata Blocks

### `AI_SQL_GENERATION`
Free-text instructions injected into every SQL generation call for this SV. Used to encode:
- Formatting preferences (`round to 2 decimal places`)
- Implicit business rules (`never include refunded orders`)
- Disambiguation hints (`use customer_name for customer breakdowns`)

```sql
AI_SQL_GENERATION 'Always round monetary values to 2 decimal places.
When asked about revenue, never include orders with status = ''refunded''.'
```

### `AI_QUESTION_CATEGORIZATION`
Instructions for the intent classification step — before SQL generation. Used to:
- Define which topics the SV handles
- Reject or redirect out-of-scope questions with a natural language message

```sql
AI_QUESTION_CATEGORIZATION 'Answer questions about revenue, orders, and customers.
Politely decline questions about PII or internal cost structure.'
```

### `AI_VERIFIED_QUERIES`
Pre-approved SQL paired with a natural language question. When a user's question closely matches, the engine uses this SQL verbatim — bypassing generation.

```sql
AI_VERIFIED_QUERIES (
    order_count_by_customer AS (
        QUESTION 'How many orders does each customer have?'
        VERIFIED_BY 'jklahr'
        VERIFIED_AT 1750000000
        SQL 'SELECT * FROM SEMANTIC_VIEW(
                SNIPPETS.PUBLIC.ORDERS_AI_SV
                METRICS ai_orders.order_count
                DIMENSIONS ai_customers.customer_name
             ) ORDER BY order_count DESC'
    )
)
```

## Physical SQL VQR vs SEMANTIC_VIEW() VQR

| | Physical SQL | SEMANTIC_VIEW() SQL |
|--|-------------|---------------------|
| Works in | AUTO mode only | AUTO + REQUIRE modes |
| Format | `SELECT col FROM table WHERE...` | `SELECT * FROM SEMANTIC_VIEW(sv METRICS ... DIMENSIONS ...)` |
| **Recommended** | Legacy | Preferred |

Use `SEMANTIC_VIEW()` format in VQRs to ensure they work in both modes.

## Docs

- [CREATE SEMANTIC VIEW — AI_SQL_GENERATION / AI_QUESTION_CATEGORIZATION / AI_VERIFIED_QUERIES](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#ai-sql-generation)
- [Cortex Analyst overview](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/cortex-analyst-overview)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `ai_orders` + `ai_customers` |
| `seed_data.sql` | 6 orders, 3 customers |
| `semantic_view.sql` | SV with all three AI metadata blocks + 2 VQRs |
| `queries.sql` | Working queries + explanation of how each AI block functions |
