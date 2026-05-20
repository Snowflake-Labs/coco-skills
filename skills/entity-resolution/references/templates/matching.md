# Matching SQL Templates

Multi-tier matching cascade: deterministic (Tier 1) -> fuzzy (Tier 2) -> AI-judged (Tier 3). Each tier is progressively more expensive. Only escalate unresolved pairs.

## Section 1: Tier 1 — Deterministic Matching (Exact ID Match)

### Single identifier match

```sql
CREATE OR REPLACE TABLE tier1_results AS
SELECT
    cp.id_left,
    cp.id_right,
    'match' AS decision,
    1.0 AS confidence,
    'tier1_exact_id' AS match_method,
    '<identifier_name>' AS matched_on
FROM candidate_pairs cp
JOIN normalized_entities a ON cp.id_left = a.source_id
JOIN normalized_entities b ON cp.id_right = b.source_id
WHERE a.<normalized_id> = b.<normalized_id>
    AND a.<normalized_id> IS NOT NULL;
```

Replace `<normalized_id>` and `<identifier_name>` with the domain profile's Tier 1 identifiers (e.g., `normalized_npi`, `normalized_lei`).

### Cascading identifier match (multiple ID types)

```sql
CREATE OR REPLACE TABLE tier1_results AS
SELECT
    cp.id_left,
    cp.id_right,
    'match' AS decision,
    1.0 AS confidence,
    'tier1_exact_id' AS match_method,
    CASE
        WHEN a.normalized_id_1 = b.normalized_id_1 AND a.normalized_id_1 IS NOT NULL THEN '<id_1_name>'
        WHEN a.normalized_id_2 = b.normalized_id_2 AND a.normalized_id_2 IS NOT NULL THEN '<id_2_name>'
        WHEN a.normalized_id_3 = b.normalized_id_3 AND a.normalized_id_3 IS NOT NULL THEN '<id_3_name>'
    END AS matched_on
FROM candidate_pairs cp
JOIN normalized_entities a ON cp.id_left = a.source_id
JOIN normalized_entities b ON cp.id_right = b.source_id
WHERE (a.normalized_id_1 = b.normalized_id_1 AND a.normalized_id_1 IS NOT NULL)
   OR (a.normalized_id_2 = b.normalized_id_2 AND a.normalized_id_2 IS NOT NULL)
   OR (a.normalized_id_3 = b.normalized_id_3 AND a.normalized_id_3 IS NOT NULL);
```

Replace placeholder identifiers with the domain profile's cascade order.

### Remaining pairs for Tier 2

```sql
CREATE OR REPLACE TABLE tier2_input AS
SELECT cp.*
FROM candidate_pairs cp
LEFT JOIN tier1_results t1 ON cp.id_left = t1.id_left AND cp.id_right = t1.id_right
WHERE t1.id_left IS NULL;  -- Not resolved in Tier 1
```

## Section 2: Tier 2 — Fuzzy Matching (Vector Similarity)

### Embedding generation

> **Higher-quality alternative:** If contrastive embeddings are available (Phase 4c), use the pre-computed `CONTRASTIVE_EMBEDDINGS` table instead of `AI_EMBED`. Contrastive embeddings are domain-adapted and produce tighter clusters (F1 up to 0.99 vs ~0.83-0.89 for general-purpose embeddings), at zero per-record marginal cost. See `references/templates/contrastive-embeddings.md` Section 6 for the integration pattern.

Delegate to `cortex-ai-functions` skill. Embed concatenated entity text:

```sql
CREATE OR REPLACE TABLE entity_embeddings AS
SELECT
    source_id,
    AI_EMBED(
        'snowflake-arctic-embed-l-v2.0',
        normalized_name || ' ' ||
        COALESCE(normalized_street, '') || ' ' ||
        COALESCE(normalized_city, '') || ' ' ||
        COALESCE(normalized_state, '') || ' ' ||
        COALESCE(normalized_zip, '')
    ) AS embedding
FROM normalized_entities;
```

### Similarity computation

```sql
CREATE OR REPLACE TABLE tier2_results AS
SELECT
    t2.id_left,
    t2.id_right,
    VECTOR_COSINE_SIMILARITY(el.embedding, er.embedding) AS cosine_sim,
    JAROWINKLER_SIMILARITY(nl.normalized_name, nr.normalized_name) / 100.0 AS name_jw,
    JAROWINKLER_SIMILARITY(
        COALESCE(nl.normalized_street, ''),
        COALESCE(nr.normalized_street, '')
    ) / 100.0 AS street_jw,
    -- Classification
    CASE
        WHEN VECTOR_COSINE_SIMILARITY(el.embedding, er.embedding) >= 0.92 THEN 'match'
        WHEN VECTOR_COSINE_SIMILARITY(el.embedding, er.embedding) >= 0.80 THEN 'probable_match'
        ELSE 'no_match'
    END AS decision,
    VECTOR_COSINE_SIMILARITY(el.embedding, er.embedding) AS confidence,
    'tier2_fuzzy' AS match_method
FROM tier2_input t2
JOIN entity_embeddings el ON t2.id_left = el.source_id
JOIN entity_embeddings er ON t2.id_right = er.source_id
JOIN normalized_entities nl ON t2.id_left = nl.source_id
JOIN normalized_entities nr ON t2.id_right = nr.source_id;
```

**Threshold adjustment:** Replace 0.92 and 0.80 with the loaded domain profile's starting thresholds. Tune in 0.02-0.03 increments based on manual review.

### Remaining pairs for Tier 3

```sql
CREATE OR REPLACE TABLE tier3_input AS
SELECT *
FROM tier2_results
WHERE decision = 'probable_match';
```

## Section 3: Tier 3 — AI-Judged Matching (LLM Classification)

**Only process Tier 2 `probable_match` results.** This is the most expensive tier.

```sql
CREATE OR REPLACE TABLE tier3_results AS
SELECT
    t3.id_left,
    t3.id_right,
    t3.cosine_sim,
    t3.name_jw,
    t3.street_jw,
    AI_CLASSIFY(
        'Record A: Name="' || nl.normalized_name || '", Address="' ||
            COALESCE(nl.normalized_street, '') || ', ' ||
            COALESCE(nl.normalized_city, '') || ', ' ||
            COALESCE(nl.normalized_state, '') || ' ' ||
            COALESCE(nl.normalized_zip, '') || '"' ||
        '\nRecord B: Name="' || nr.normalized_name || '", Address="' ||
            COALESCE(nr.normalized_street, '') || ', ' ||
            COALESCE(nr.normalized_city, '') || ', ' ||
            COALESCE(nr.normalized_state, '') || ' ' ||
            COALESCE(nr.normalized_zip, '') || '"' ||
        '\nAre these the same real-world entity?',
        ARRAY_CONSTRUCT('match', 'probable_match', 'no_match')
    ) AS ai_decision,
    'tier3_ai_judged' AS match_method
FROM tier3_input t3
JOIN normalized_entities nl ON t3.id_left = nl.source_id
JOIN normalized_entities nr ON t3.id_right = nr.source_id;
```

**Domain-specific prompt enhancement:** Append the domain profile's Tier 3 prompt addition before the `AI_CLASSIFY` call. Build the prompt string dynamically.

## Section 4: Score Consolidation

### Combine all tier results

```sql
CREATE OR REPLACE TABLE match_results AS
-- Tier 1 matches
SELECT id_left, id_right, decision, confidence, match_method, matched_on
FROM tier1_results
UNION ALL
-- Tier 2 definitive results (match or no_match)
SELECT id_left, id_right, decision, confidence, match_method, NULL AS matched_on
FROM tier2_results
WHERE decision IN ('match', 'no_match')
UNION ALL
-- Tier 3 results (overrides Tier 2 probable_match)
SELECT id_left, id_right, ai_decision:label::STRING AS decision,
       ai_decision:score::FLOAT AS confidence, match_method, NULL AS matched_on
FROM tier3_results;
```

### Transitivity resolution (entity group assignment)

Connected components: if A=B and B=C, then A=B=C belong to the same entity group.

```sql
-- Iterative transitive closure using a recursive CTE
CREATE OR REPLACE TABLE entity_groups AS
WITH RECURSIVE edges AS (
    SELECT id_left AS entity_id, id_right AS linked_to FROM match_results WHERE decision = 'match'
    UNION ALL
    SELECT id_right AS entity_id, id_left AS linked_to FROM match_results WHERE decision = 'match'
),
closure AS (
    SELECT entity_id, entity_id AS group_root FROM edges
    UNION ALL
    SELECT e.entity_id, LEAST(c.group_root, e.linked_to) AS group_root
    FROM edges e
    JOIN closure c ON e.linked_to = c.entity_id
    WHERE LEAST(c.group_root, e.linked_to) < c.group_root
)
SELECT entity_id, MIN(group_root) AS entity_group_id
FROM closure
GROUP BY entity_id;
```

**Note:** For very large match sets (>1M matches), the recursive CTE may be slow. Consider an iterative approach using a WHILE loop with convergence check, or use the `machine-learning` skill to implement Union-Find.

### Iterative Union-Find (for >100K matches)

When the recursive CTE is too slow or hits Snowflake recursion limits, use an iterative convergence approach:

```sql
-- Step 1: Initialize — each entity is its own group root
CREATE OR REPLACE TABLE entity_groups AS
SELECT DISTINCT entity_id, entity_id AS entity_group_id
FROM (
    SELECT id_left AS entity_id FROM match_results WHERE decision = 'match'
    UNION
    SELECT id_right AS entity_id FROM match_results WHERE decision = 'match'
);

-- Step 2: Iterate until convergence
DECLARE
    changes INT DEFAULT 1;
    iteration INT DEFAULT 0;
BEGIN
    WHILE (changes > 0 AND iteration < 50) DO
        -- Propagate the smallest group_id across edges
        UPDATE entity_groups eg
        SET entity_group_id = new_groups.min_group
        FROM (
            SELECT
                eg2.entity_id,
                LEAST(
                    eg2.entity_group_id,
                    MIN(eg_linked.entity_group_id)
                ) AS min_group
            FROM entity_groups eg2
            JOIN match_results mr
                ON eg2.entity_id = mr.id_left OR eg2.entity_id = mr.id_right
            JOIN entity_groups eg_linked
                ON eg_linked.entity_id = CASE
                    WHEN eg2.entity_id = mr.id_left THEN mr.id_right
                    ELSE mr.id_left
                END
            WHERE mr.decision = 'match'
            GROUP BY eg2.entity_id, eg2.entity_group_id
            HAVING LEAST(eg2.entity_group_id, MIN(eg_linked.entity_group_id)) < eg2.entity_group_id
        ) new_groups
        WHERE eg.entity_id = new_groups.entity_id;

        changes := SQLROWCOUNT;
        iteration := iteration + 1;
    END WHILE;

    RETURN 'Converged in ' || iteration || ' iterations, last changes: ' || changes;
END;
```

**Expected behavior:** Real-world entity graphs converge in 5-15 iterations (the diameter of the match graph). If iteration hits 50, the match graph may have pathological chains — investigate whether thresholds are too loose.

**When to use which approach:**

| Match Count | Approach | Notes |
|-------------|----------|-------|
| <100K | Recursive CTE | Simpler, single statement |
| 100K-10M | Iterative Union-Find | Reliable convergence, predictable cost |
| >10M | Iterative + partitioned | Partition by blocking_key first, then merge cross-partition groups |

## Section 5: Match Summary Statistics

Present these to the user at the Phase 4 stopping point:

```sql
-- Decision counts by tier
SELECT match_method, decision, COUNT(*) AS pair_count
FROM match_results
GROUP BY match_method, decision
ORDER BY match_method, decision;

-- Entity group sizes
SELECT entity_group_id, COUNT(*) AS group_size
FROM entity_groups
GROUP BY entity_group_id
ORDER BY group_size DESC
LIMIT 20;

-- Confidence distribution for Tier 2
SELECT
    FLOOR(confidence * 20) / 20 AS confidence_bucket,  -- 0.05 buckets
    decision,
    COUNT(*) AS pair_count
FROM tier2_results
GROUP BY confidence_bucket, decision
ORDER BY confidence_bucket;
```
