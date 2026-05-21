# Search Service — Cortex Search Service for Entity Resolution

Set up a Cortex Search Service over a reference entity corpus to enable fuzzy search for Tier 1.5 (batch search + classify) and Tier 2 (agentic search).

## When to Use

Create a Cortex Search Service when:
- The agentic matching workflow (see `agentic-matching.md`) is being used
- The reference corpus has >10K entities (justifies the index build time)
- Fuzzy search by name + address is needed (not just exact ID matching)

## Step 1: Search Corpus Table

Create a denormalized, search-optimized table from the normalized reference entities. Concatenate key fields into a single `search_text` column for hybrid search.

```sql
CREATE OR REPLACE TABLE <schema>.search_corpus AS
SELECT
    source_id                   AS entity_id,
    '<source_name>'             AS entity_source,
    -- Entity name (adapt based on entity type: org name, person name, etc.)
    UPPER(TRIM(<name_column>))  AS entity_name,
    -- DBA / trade name if available (improve recall for entities known by multiple names)
    <dba_name_expression>       AS dba_name,
    -- Address components
    UPPER(TRIM(<street_column>))    AS address_line_1,
    UPPER(TRIM(<city_column>))      AS city,
    UPPER(TRIM(<state_column>))     AS state,
    TRIM(<zip_column>)              AS zip5,
    -- Domain-specific attributes (carry through for filtering/display)
    <domain_attribute_1>        AS attribute_1,
    <domain_attribute_2>        AS attribute_2,
    -- Concatenated search text: name + address + city + state + ZIP + DBA
    UPPER(TRIM(
        COALESCE(<name_column>, '') || ' ' ||
        COALESCE(<street_column>, '') || ' ' ||
        COALESCE(<city_column>, '') || ' ' ||
        COALESCE(<state_column>, '') || ' ' ||
        COALESCE(<zip_column>, '') ||
        CASE
            WHEN <dba_name_expression> IS NOT NULL
            THEN ' ' || <dba_name_expression>
            ELSE ''
        END
    )) AS search_text
FROM <schema>.normalized_entities
WHERE <active_filter>;  -- e.g., deactivation_date IS NULL
```

### Design Principles

- **search_text** includes all searchable fields concatenated — this enables hybrid (semantic + keyword) retrieval
- **entity_name** and **dba_name** are separate columns for targeted searches and display
- **state** and **zip5** are filterable attributes (exact match filtering within search)
- Include **entity_id** and **entity_source** for joining back to the full reference table
- Keep the corpus table denormalized — one row per entity, no joins at search time

## Step 2: Cortex Search Service

```sql
CREATE OR REPLACE CORTEX SEARCH SERVICE <schema>.reference_search_svc
    ON search_text
    ATTRIBUTES entity_id, entity_source, entity_name, dba_name,
               address_line_1, city, state, zip5
    WAREHOUSE = <warehouse_name>
    TARGET_LAG = '7 days'
AS (
    SELECT
        search_text,
        entity_id,
        entity_source,
        entity_name,
        dba_name,
        address_line_1,
        city,
        state,
        zip5
    FROM <schema>.search_corpus
);
```

### Configuration Notes

- **TARGET_LAG**: Set based on how frequently the reference corpus changes. `7 days` for stable corpora (e.g., monthly NPPES loads). `1 day` for frequently updated sources.
- **Warehouse sizing**: Index build can be resource-intensive for large corpora (>1M records). Use at least a MEDIUM warehouse. Build time scales with corpus size (e.g., ~1 hour for ~5M records on MEDIUM).
- **Filterable columns**: `state` and `zip5` are commonly used as filters to narrow search results to a geographic area. Add other filterable columns based on domain needs.
- **ATTRIBUTES vs ON**: The `ON` clause specifies the primary search column (search_text). `ATTRIBUTES` are returned in results and optionally filterable.

## Step 3: Verification

```sql
-- Verify service exists
SHOW CORTEX SEARCH SERVICES IN SCHEMA <schema>;

-- Test search
SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        '<database>.<schema>.reference_search_svc',
        '{"query": "<test_entity_name>", "columns": ["entity_name","city","zip5","entity_id"], "limit": 5}'
    )
) AS results;

-- Test with state filter
SELECT PARSE_JSON(
    SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
        '<database>.<schema>.reference_search_svc',
        '{"query": "<test_entity_name>", "columns": ["entity_name","city","zip5","entity_id"], "filter": {"@eq": {"state": "<state_code>"}}, "limit": 5}'
    )
) AS results;
```

## Calling from Stored Procedures

When calling Cortex Search from a Python SP (for batch search in Tier 1.5):

```python
import json

SEARCH_SVC = '<database>.<schema>.reference_search_svc'

search_request = json.dumps({
    "query": search_query,
    "columns": ["entity_id", "entity_name", "dba_name",
                 "address_line_1", "city", "state", "zip5"],
    "filter": {"@eq": {"state": state}} if state else {},
    "limit": 10
})
safe_req = search_request.replace("'", "''")

rows = session.sql(
    f"SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW("
    f"'{SEARCH_SVC}', "
    f"'{safe_req}'"
    f")) AS result"
).collect()

result = rows[0]["RESULT"]
if isinstance(result, str):
    result = json.loads(result)
results_arr = result.get("results", [])
```

### Bulk JW Scoring

After retrieving search results, compute JW scores set-based (not per-row) for performance:

```sql
WITH raw_results(...) AS (
    SELECT * FROM VALUES ...
),
scored AS (
    SELECT
        r.*,
        ROUND(JAROWINKLER_SIMILARITY(
            source_fuzzy_name, r.entity_name
        ) / 100.0, 4) AS name_jw,
        ROUND(JAROWINKLER_SIMILARITY(
            source_norm_street, r.address_line_1
        ) / 100.0, 4) AS street_jw,
        CASE WHEN TRIM(source_zip) = TRIM(r.zip5)
             THEN 1 ELSE 0 END AS zip_exact
    FROM raw_results r
)
SELECT
    s.*,
    ROUND(0.40 * s.name_jw + 0.35 * s.street_jw + 0.25 * s.zip_exact, 4)
        AS composite_search_score
FROM scored s;
```

Batch search results into groups of 500 entities before INSERT to balance memory usage with round-trip reduction.
