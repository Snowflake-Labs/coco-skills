# Blocking SQL Templates

Reduce the comparison space from O(n^2) to manageable partitions. Without blocking, matching 1M records requires 500B comparisons.

## Section 1: Blocking Key Assignment

### Geographic blocking (state + zip prefix)

```sql
UPDATE normalized_entities
SET blocking_key = normalized_state || '-' || LEFT(normalized_zip, 3)
WHERE normalized_state IS NOT NULL AND normalized_zip IS NOT NULL;
```

### Phonetic blocking (SOUNDEX + state)

```sql
UPDATE normalized_entities
SET blocking_key = SOUNDEX(normalized_name) || '-' || normalized_state
WHERE normalized_name IS NOT NULL AND normalized_state IS NOT NULL;
```

### Name prefix blocking (for large datasets or name-only matching)

```sql
UPDATE normalized_entities
SET blocking_key = LEFT(UPPER(normalized_name), 4) || '-' || LEFT(normalized_zip, 3)
WHERE normalized_name IS NOT NULL AND normalized_zip IS NOT NULL;
```

### Identifier prefix blocking (when authoritative IDs present)

```sql
-- Example for NPI
UPDATE normalized_entities
SET blocking_key = LEFT(normalized_npi, 6)
WHERE normalized_npi IS NOT NULL;
```

### Compound blocking (multiple strategies via UNION)

For better recall at the cost of more pairs, use multiple blocking keys:

```sql
CREATE OR REPLACE TABLE blocking_assignments AS
-- Strategy 1: Geographic
SELECT source_id, normalized_state || '-' || LEFT(normalized_zip, 3) AS block_key, 'geo' AS strategy
FROM normalized_entities
WHERE normalized_state IS NOT NULL AND normalized_zip IS NOT NULL
UNION ALL
-- Strategy 2: Phonetic
SELECT source_id, SOUNDEX(normalized_name) || '-' || normalized_state AS block_key, 'phonetic' AS strategy
FROM normalized_entities
WHERE normalized_name IS NOT NULL AND normalized_state IS NOT NULL;
```

## Section 2: Candidate Pair Generation

### Cross-source matching (different source tables)

```sql
CREATE OR REPLACE TABLE candidate_pairs AS
SELECT
    a.source_id AS id_left,
    b.source_id AS id_right,
    a.blocking_key,
    a.source_table AS source_left,
    b.source_table AS source_right
FROM normalized_entities a
JOIN normalized_entities b
    ON a.blocking_key = b.blocking_key
    AND a.source_table != b.source_table
    AND a.source_id < b.source_id;  -- Avoid duplicates and self-matches
```

### Deduplication (same source table)

```sql
CREATE OR REPLACE TABLE candidate_pairs AS
SELECT
    a.source_id AS id_left,
    b.source_id AS id_right,
    a.blocking_key
FROM normalized_entities a
JOIN normalized_entities b
    ON a.blocking_key = b.blocking_key
    AND a.source_id < b.source_id;  -- Allow same-source pairs
```

### With compound blocking (multiple strategies)

```sql
CREATE OR REPLACE TABLE candidate_pairs AS
SELECT DISTINCT
    a.source_id AS id_left,
    b.source_id AS id_right
FROM blocking_assignments a
JOIN blocking_assignments b
    ON a.block_key = b.block_key
    AND a.source_id < b.source_id;
```

### Multi-Pass Deduplication Cost

When using compound blocking (multiple strategies via UNION ALL + DISTINCT), the DISTINCT operation can become expensive at scale. Diagnostic query to measure duplicate pair rate:

```sql
-- Measure how many duplicate pairs compound blocking produces
WITH raw_pairs AS (
    SELECT a.source_id AS id_left, b.source_id AS id_right
    FROM blocking_assignments a
    JOIN blocking_assignments b
        ON a.block_key = b.block_key
        AND a.source_id < b.source_id
)
SELECT
    COUNT(*) AS raw_pair_count,
    COUNT(DISTINCT id_left || '-' || id_right) AS unique_pair_count,
    ROUND(1.0 - COUNT(DISTINCT id_left || '-' || id_right) / NULLIF(COUNT(*), 0), 4) AS duplicate_rate
FROM raw_pairs;
```

**Guidance by scale:**

| Unique Pairs | DISTINCT Cost | Recommendation |
|-------------|---------------|----------------|
| <10M | Low | Use `SELECT DISTINCT` — straightforward and fast |
| 10M-50M | Moderate | Use `SELECT DISTINCT` but monitor query profile for spilling |
| >50M | High | Consider single-strategy blocking or sorted-neighborhood (below) |

### Sorted-Neighborhood Blocking (Large Datasets)

For datasets >5M records where compound blocking produces too many pairs, sorted-neighborhood blocking is an alternative that avoids the O(n²) pair explosion:

```sql
-- Sort entities by a composite key, then compare only within a sliding window
CREATE OR REPLACE TABLE candidate_pairs AS
WITH sorted AS (
    SELECT
        source_id,
        blocking_key,
        normalized_name,
        ROW_NUMBER() OVER (
            PARTITION BY blocking_key
            ORDER BY normalized_name
        ) AS sort_pos
    FROM normalized_entities
)
SELECT
    a.source_id AS id_left,
    b.source_id AS id_right,
    a.blocking_key
FROM sorted a
JOIN sorted b
    ON a.blocking_key = b.blocking_key
    AND b.sort_pos BETWEEN a.sort_pos + 1 AND a.sort_pos + 10  -- Window size = 10
    AND a.source_id < b.source_id;
```

**Window size tuning:** Start with 10. Increase to 20 if recall is too low (missing known matches). Decrease to 5 if pair volume is still too high. Validate by checking whether known match pairs appear in the candidate set.

## Section 3: Blocking Diagnostics

### Block size distribution

```sql
SELECT
    blocking_key,
    COUNT(*) AS entities_in_block,
    (COUNT(*) * (COUNT(*) - 1)) / 2 AS potential_pairs
FROM normalized_entities
GROUP BY blocking_key
ORDER BY potential_pairs DESC
LIMIT 20;
```

### Aggregate statistics

```sql
SELECT
    COUNT(DISTINCT blocking_key) AS total_blocks,
    SUM(entity_count) AS total_entities,
    SUM(pair_count) AS total_candidate_pairs,
    MAX(pair_count) AS largest_block_pairs,
    AVG(pair_count) AS avg_block_pairs,
    -- Reduction ratio: candidate_pairs / total_possible_pairs
    ROUND(SUM(pair_count) / (SUM(entity_count) * (SUM(entity_count) - 1) / 2) * 100, 4) AS reduction_ratio_pct
FROM (
    SELECT
        blocking_key,
        COUNT(*) AS entity_count,
        (COUNT(*) * (COUNT(*) - 1)) / 2 AS pair_count
    FROM normalized_entities
    GROUP BY blocking_key
);
```

### Red flag checks

```sql
-- Check 1: Any block with >100K pairs (too coarse)
SELECT blocking_key, COUNT(*) AS entity_count, (COUNT(*) * (COUNT(*) - 1)) / 2 AS pairs
FROM normalized_entities
GROUP BY blocking_key
HAVING pairs > 100000
ORDER BY pairs DESC;

-- Check 2: Total candidate pairs
SELECT COUNT(*) AS total_pairs FROM candidate_pairs;
-- If >10M: add second blocking pass or tighten criteria

-- Check 3: Entities not assigned to any block (lost records)
SELECT COUNT(*) AS unblocked_entities
FROM normalized_entities
WHERE blocking_key IS NULL;
```

### Red flag thresholds

| Metric | Threshold | Action |
|--------|-----------|--------|
| Largest block pairs | >100K | Refine blocking key (add more fields) |
| Total pairs | >10M | Add second blocking pass |
| Total pairs | Very few | Loosen blocking key |
| Reduction ratio | >10% | Blocking is too loose |
| Unblocked entities | >5% of total | Fix NULL blocking keys or add fallback strategy |
