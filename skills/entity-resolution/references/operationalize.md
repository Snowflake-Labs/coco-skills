# Operationalize — Pipeline and Extensions

Make entity resolution an ongoing, incremental process rather than a one-time batch job.

## Dynamic Tables Pipeline (Path A — Pair-Based)

**Delegate to:** `dynamic-tables` skill with the following ER-specific pipeline topology.

### Pipeline DAG

```
source_entities (base tables)
  └─> dt_normalized_entities (dynamic table, TARGET_LAG = '1 hour')
        └─> dt_candidate_pairs (dynamic table, TARGET_LAG = DOWNSTREAM)
              ├─> dt_tier1_results (dynamic table, TARGET_LAG = DOWNSTREAM)
              ├─> dt_tier2_results (dynamic table, TARGET_LAG = DOWNSTREAM)
              └─> dt_match_results (dynamic table, TARGET_LAG = DOWNSTREAM)
                    └─> dt_entity_groups (dynamic table, TARGET_LAG = DOWNSTREAM)
                          └─> dt_entity_master (dynamic table, TARGET_LAG = DOWNSTREAM)
```

### Key Configuration

- **Root lag:** Set `TARGET_LAG` on `dt_normalized_entities` based on how fresh match results need to be. Typical values: `'1 hour'` for operational matching, `'24 hours'` for batch.
- **Downstream lag:** All downstream tables use `TARGET_LAG = DOWNSTREAM` to cascade from the root.
- **Warehouse sizing:** Tier 2 (embedding) and Tier 3 (LLM) steps are the most expensive. Size the warehouse based on the expected volume of new/changed records per refresh cycle, not total table size. Dynamic tables refresh incrementally.

### Incremental Considerations

- **New records:** Automatically processed through the pipeline when source tables change.
- **Changed records:** Handled by dynamic table refresh if the source table updates in place. If the source appends new versions, add a deduplication step in `dt_normalized_entities`.
- **Deleted records:** Dynamic tables handle this naturally. Entity groups will be recalculated.
- **Threshold changes:** After HITL review and threshold tuning, update the matching dynamic table definition. This triggers a full refresh of downstream tables.

> **For stream + task based incremental pipelines** (higher control over refresh timing and cost), see `references/templates/incremental.md` which covers change detection via streams, incremental MERGE normalization, delta candidate pair generation, and task DAG orchestration with `SYSTEM$STREAM_HAS_DATA()` guards.

## Entity Master Table

The golden record view aggregating the best values per entity group:

```sql
CREATE OR REPLACE DYNAMIC TABLE dt_entity_master
    TARGET_LAG = DOWNSTREAM
    WAREHOUSE = <warehouse_name>
AS
SELECT
    eg.entity_group_id,
    -- Best name: pick the most complete/recent
    FIRST_VALUE(ne.normalized_name) OVER (
        PARTITION BY eg.entity_group_id
        ORDER BY LENGTH(ne.normalized_name) DESC  -- Longest (most complete) name
    ) AS master_name,
    -- Best address: pick the most complete
    FIRST_VALUE(ne.normalized_street) OVER (
        PARTITION BY eg.entity_group_id
        ORDER BY CASE WHEN ne.normalized_street IS NOT NULL THEN 1 ELSE 0 END DESC
    ) AS master_street,
    -- Aggregate all source IDs
    ARRAY_AGG(DISTINCT ne.source_id) AS source_ids,
    ARRAY_AGG(DISTINCT ne.source_table) AS source_tables,
    COUNT(DISTINCT ne.source_id) AS record_count
FROM entity_groups eg
JOIN normalized_entities ne ON eg.entity_id = ne.source_id
GROUP BY eg.entity_group_id;
```

**Note:** The "best value" selection strategy (longest name, most complete address) is a starting point. Customize based on the customer's data quality priorities. Some may prefer the most recent record, or a specific source as the system of record.

## Monitoring

**Delegate to:** `data-quality` skill for source data quality monitoring.

Key metrics to monitor:
- **Source data freshness** — are source tables being updated on schedule?
- **Match rate trends** — is the percentage of matches stable over time? A sudden drop may indicate source data quality issues.
- **Entity group growth** — are entity groups growing too large? Groups >10 records may indicate over-matching.
- **Pipeline lag** — are dynamic tables refreshing within their target lag?

## Agentic Pipeline (Path B — Entity Linking)

When using the agentic matching workflow (Phase 4b), operationalize the 3-tier escalation pipeline.

### Pipeline DAG

```
source_entities (base tables)
  └─> dt_normalized_entities (dynamic table, TARGET_LAG = '1 hour')
        └─> dt_entity_embeddings (dynamic table, TARGET_LAG = DOWNSTREAM)
              └─> dt_match_candidates (dynamic table, TARGET_LAG = DOWNSTREAM)
                    └─> dt_highconf_matches (dynamic table, TARGET_LAG = DOWNSTREAM)
                          └─> [Tier 1.5 + Tier 2 triggered via Task DAG]
                                └─> dt_crosswalk (view, UNION ALL of all tiers)
                                      └─> dt_entity_master (dynamic table, TARGET_LAG = DOWNSTREAM)
```

**Key difference from Path A:** Tier 1.5 (batch search + classify) and Tier 2 (agent) cannot be fully expressed as dynamic tables because they involve stored procedure calls (Cortex Search, AI_COMPLETE, DATA_AGENT_RUN). Instead:

1. **Dynamic tables handle:** normalization, embedding, match candidates, high-confidence triage, crosswalk assembly, and entity master
2. **Task DAG handles:** batch search + classify (Tier 1.5) and agent resolution (Tier 2)
3. **Trigger:** When `dt_highconf_matches` refreshes and new unresolved entities appear, trigger the Task DAG to process them through Tier 1.5 and Tier 2

### State-Based Pipeline Controller

For large reference corpora spanning multiple partitions (e.g., US states), process one partition at a time using a controller pattern:

```sql
-- State queue tracks which partitions have been processed
CREATE OR REPLACE TABLE state_batch_queue (
    state_code    STRING PRIMARY KEY,
    entity_count  INT,
    status        STRING DEFAULT 'PENDING',  -- PENDING | ACTIVE | COMPLETED
    started_at    TIMESTAMP_NTZ,
    completed_at  TIMESTAMP_NTZ
);
```

The Task DAG finalizer calls a controller SP that checks backlog and auto-feeds the next partition. See `templates/orchestration.md` for the controller pattern.

### Agent Reasoning Search Corpus

For operational intelligence, maintain a searchable corpus of agent reasoning across all resolution runs:

```sql
CREATE OR REPLACE TABLE agent_reasoning_corpus (
    source_id               INT,
    reasoning_text           STRING,    -- Concatenated: decision + reasoning + entity context
    agent_reasoning          STRING,
    source_entity_id         STRING,
    decision                 STRING,
    confidence               FLOAT,
    state_code               STRING,
    entity_name              STRING,
    matched_reference_name   STRING,
    web_search_used          BOOLEAN,
    created_at               TIMESTAMP_NTZ
);
```

Build a Cortex Search Service over this corpus to enable natural-language queries like "show me entities where the agent found a closure" or "which pharmacies had name mismatches resolved by web search." Refreshed incrementally by the finalizer after each pipeline cycle.

### Quality Loop — LLM-as-a-Judge

For ongoing quality assurance, add an automated judge layer that evaluates a sample of agent decisions after each pipeline cycle:

1. **Sample:** Stratified sample of agent results (by decision type, confidence range)
2. **Judge prompt:** Provide entity details, agent decision, reasoning, and reference match — ask the judge to evaluate correctness
3. **Output:** Quality metrics per tier, per decision type, and actionable improvement findings
4. **Feedback:** Findings feed into threshold tuning and agent prompt refinement

This can be scheduled as a post-pipeline task that runs after the finalizer.

## Contrastive Embeddings Pipeline (Phase 4c)

When contrastive embeddings are used (standalone or as a Tier 2 replacement), the operationalization differs from the standard pipeline because embedding generation requires a GPU training step that cannot be expressed as a dynamic table.

### Standalone Pipeline DAG

```
source_entities (base tables)
  └─> dt_normalized_entities (dynamic table, TARGET_LAG = '1 hour')
        └─> dt_serialized_entities (dynamic table, TARGET_LAG = DOWNSTREAM)
              └─> [GPU training job triggered manually or via Task]
                    └─> contrastive_embeddings (base table, written by training job)
                          └─> dt_blocking_candidates (dynamic table, TARGET_LAG = DOWNSTREAM)
                                └─> dt_predicted_matches (dynamic table, TARGET_LAG = DOWNSTREAM)
                                      └─> dt_entity_groups (dynamic table, TARGET_LAG = DOWNSTREAM)
                                            └─> dt_entity_master (dynamic table, TARGET_LAG = DOWNSTREAM)
```

### Add-On Pipeline DAG (replaces AI_EMBED in Path A)

```
source_entities (base tables)
  └─> dt_normalized_entities (dynamic table, TARGET_LAG = '1 hour')
        ├─> dt_candidate_pairs (dynamic table, TARGET_LAG = DOWNSTREAM)
        │     ├─> dt_tier1_results (dynamic table, TARGET_LAG = DOWNSTREAM)
        │     ├─> dt_tier2_contrastive_results (dynamic table, reads from contrastive_embeddings)
        │     └─> dt_match_results (dynamic table, TARGET_LAG = DOWNSTREAM)
        │           └─> dt_entity_groups → dt_entity_master
        └─> dt_serialized_entities (dynamic table, TARGET_LAG = DOWNSTREAM)
              └─> [GPU training job — manual or scheduled retrain]
                    └─> contrastive_embeddings (base table)
```

### Retraining Strategy

Contrastive embeddings are not automatically refreshed like dynamic tables. Retrain when:
- **New entity types appear** that were not in the original training data
- **Data distribution shifts** (e.g., new product categories, geographic expansion)
- **HITL review reveals systematic errors** that better embeddings could fix
- **Periodic schedule** — retrain monthly or quarterly as new ground truth accumulates from HITL reviews

Retraining workflow:
1. Collect new ground truth from HITL review decisions (Phase 5 outputs)
2. Merge with original training data
3. Re-run GPU training job with updated `pretrain_entities` table
4. Verify new embeddings improve F1 on a held-out validation set
5. Replace `contrastive_embeddings` table (downstream dynamic tables auto-refresh)

**Note:** Retraining is fast (~5-30 min on GPU_NV_S) and cheap (~1-5 credits). The cost of not retraining is potential F1 degradation as entity data evolves.

## Extension: Cortex Agent

**Delegate to:** `cortex-agent` skill to build an agent for querying match results.

Agent instructions should include:
- Access to `entity_master`, `match_results`, `entity_groups` tables
- Sample questions: "Find all records for entity X", "Show match history for record Y", "Which entities have the most source records?", "Show unresolved probable matches"
- Tool mapping to a semantic view over the entity resolution tables

## Extension: Custom ML Model

**Delegate to:** `machine-learning` skill when the customer wants to go beyond Cortex AI functions.

Use cases:
- **Training a matching model:** Use HITL review decisions as labeled training data (accepted pairs = positive, rejected pairs = negative). Train a binary classifier or learn-to-rank model.
- **Feature engineering:** Use cosine_sim, name_jw, street_jw, and domain-specific signals as features.
- **Model registry:** Register the trained model in Snowflake Model Registry for version tracking.
- **Replacing Tier 2/3:** A well-trained custom model can replace both Tier 2 and Tier 3 with a single inference call, reducing cost and latency.

**Minimum training data:** 500+ labeled pairs (250+ positive, 250+ negative) for a reasonable model. Collect through HITL review.
