# Agentic Matching — 3-Tier Entity Linking Workflow

Resolve source entities against a reference corpus using a progressive escalation funnel. Each tier is more capable and more expensive than the previous. Only unresolved entities escalate to the next tier.

**Use this workflow when:**
- Matching source records against a known reference corpus (entity linking), not comparing records within/across datasets (deduplication)
- The reference corpus is available as a Cortex Search Service
- Entities may require web validation (closures, rebrands, acquisitions)
- The domain includes abbreviations, name variants, or Class of Trade distinctions that require reasoning

**Do NOT use when:**
- Performing symmetric deduplication (no "reference side") — use `matching.md` instead
- The reference corpus is small enough to compare all pairs directly (<10K records)

## Architecture Overview

```
Source Entities (normalized, embedded)
  │
  ├─── Tier 1: High-Confidence Triage (pure SQL)
  │     ├── RESOLVED ──→ highconf_matches
  │     └── UNRESOLVED ──↓
  │
  ├─── Tier 1.5: Batch Search + Classify (Cortex Search + AI_COMPLETE)
  │     ├── RESOLVED ──→ batch_classify_matches
  │     └── UNRESOLVED ──↓
  │
  └─── Tier 2: Agentic Search (Cortex Agent with tools + web search)
        ├── RESOLVED ──→ agent_results
        └── UNRESOLVED ──→ no_match / investigate / new_record_needed
                              │
                              ↓
                    Crosswalk (UNION ALL of all tiers)
```

**Expected volume reduction:**
- Tier 1 resolves ~60-80% of entities (high-confidence embedding + JW matches)
- Tier 1.5 resolves ~10-20% (medium-confidence, search-assisted)
- Tier 2 resolves the remaining ~5-20% (hardest cases requiring reasoning/web search)

## Prerequisites

Before starting the agentic matching workflow:

1. **Normalized entities** — Source entities must be normalized (Phase 2 output)
2. **Embeddings** — Both source and reference entities must have embeddings (`AI_EMBED` with `snowflake-arctic-embed-l-v2.0`)
3. **Match candidates** — Top-N embedding matches per source entity (from Phase 3 blocking or direct vector search)
4. **Cortex Search Service** — Over the reference corpus (see `search-service.md`)
5. **Semantic model** — YAML file for the reference corpus (for the agent's SQL fallback tool)

---

## Tier 1: High-Confidence Triage

**Goal:** Resolve entities where embedding similarity + string similarity scores exceed tight thresholds. Pure SQL, zero AI cost.

**Delegate to:** Standard SQL execution. No bundled skill needed.

### Match Rules

Apply these rules to the top-1 embedding candidate per source entity:

```sql
CREATE OR REPLACE TABLE highconf_matches AS
WITH top1 AS (
    SELECT *
    FROM match_candidates
    WHERE rn = 1
)
SELECT
    t.id_left       AS source_entity_id,
    t.id_right      AS reference_entity_id,
    t.cosine_sim,
    t.name_jw,
    t.street_jw,
    t.zip_exact,
    t.composite_score AS confidence,
    'tier1_highconf' AS match_method,
    'match'          AS decision
FROM top1 t
WHERE
    -- Path 1: Strong embedding + strong name
    (
        (cosine_sim >= 0.92 AND name_jw >= 0.85)
        OR (cosine_sim >= 0.90 AND name_jw >= 0.80
            AND street_jw >= 0.90 AND zip_exact = 1)
    )
    -- Address floor guard: require some address alignment
    AND (street_jw >= 0.70 OR zip_exact = 1);
```

### Domain-Specific Guard Rails

Add guard rails based on the loaded domain profile. Common patterns:

**Chain-store guard** (retail, pharma): If both entities match a known chain brand, require store number match:
```sql
-- Add to the WHERE clause:
AND (both_are_chains = 0 OR store_num_match = 1)
```

**Multi-tenant building guard** (healthcare, office buildings): At addresses with 4+ reference entities, require tighter name match to prevent wrong-tenant matches:
```sql
-- Add to the WHERE clause:
AND (
    is_multi_tenant = 0           -- standard addresses: normal thresholds
    OR name_jw >= 0.92            -- high-density: very strong name match
    OR zip4_exact = 1             -- high-density: ZIP+4 confirms same suite
)
```

**Chain-brand consolidation** (optional): Same chain brand + strong address match can override store number mismatch (e.g., store renumbering during remodeling). Flag for human review:
```sql
-- Additional OR path in WHERE clause:
OR (
    chain_brand_match = 1
    AND street_jw >= 0.90
    AND zip_exact = 1
    AND name_jw >= 0.75
    AND cosine_sim >= 0.80
)
```

### Threshold Starting Points

These are starting points. Tune based on manual review of results.

| Parameter | Starting Value | Notes |
|-----------|---------------|-------|
| `cosine_sim` (primary path) | >= 0.92 | Tighten to 0.94 if over-matching |
| `name_jw` (primary path) | >= 0.85 | Core name similarity |
| `cosine_sim` (address-confirmed path) | >= 0.90 | Lower cosine OK if address is strong |
| `name_jw` (address-confirmed path) | >= 0.80 | |
| `street_jw` (address-confirmed path) | >= 0.90 | |
| `street_jw` (address floor) | >= 0.70 | Prevents wrong-location matches |
| `name_jw` (multi-tenant override) | >= 0.92 | Only for buildings with 4+ tenants |

**MANDATORY STOPPING POINT**: Present Tier 1 match counts and sample matches for user review before proceeding.

---

## Tier 1.5: Batch Search + Classify

**Goal:** Resolve medium-confidence entities using Cortex Search retrieval + LLM classification. ~10-50x cheaper than a full agent call.

**Delegate to:** `cortex-ai-functions` for `AI_COMPLETE` calls. Cortex Search is called via `SNOWFLAKE.CORTEX.SEARCH_PREVIEW`.

### Step 1: Batch Search

For each unresolved entity, query the Cortex Search Service with the entity's name + address as the search query. Retrieve top-N results (typically 10).

```sql
-- Search query construction per entity:
-- CONCAT(expanded_name, ' ', normalized_street, ' ', city, ' ', state, ' ', zip)

-- Filter by state if the reference corpus is nationwide
-- {"filter": {"@eq": {"state": "<entity_state>"}}}
```

See `search-service.md` for the search SP template (`RUN_BATCH_SEARCH`). The SP:
1. Iterates over unresolved entities
2. Calls `SNOWFLAKE.CORTEX.SEARCH_PREVIEW` per entity
3. Bulk-inserts results with set-based JW scoring (name_jw, street_jw, zip_exact, composite_score)

### Step 2: Classify Top Candidate

For each entity's top search result (by composite score), use `AI_COMPLETE` to classify whether it's the same entity.

```sql
-- Classification prompt template:
SNOWFLAKE.CORTEX.COMPLETE(
    '<model>',  -- Use a cost-effective model: 'claude-haiku-4-5' or 'mistral-large2'
    CONCAT(
        'You are an entity resolution expert. Determine if these two records '
        'refer to the same entity.\n\n',
        'ENTITY A (Source record):\n',
        '  Name: ', COALESCE(source_name, 'N/A'), '\n',
        '  Address: ', COALESCE(source_street, 'N/A'), '\n',
        '  City: ', COALESCE(source_city, 'N/A'), '\n',
        '  State: ', COALESCE(source_state, 'N/A'), '\n',
        '  ZIP: ', COALESCE(source_zip, 'N/A'), '\n\n',
        'ENTITY B (Reference record):\n',
        '  Name: ', COALESCE(ref_name, 'N/A'), '\n',
        '  Address: ', COALESCE(ref_address, 'N/A'), '\n',
        '  City: ', COALESCE(ref_city, 'N/A'), '\n',
        '  State: ', COALESCE(ref_state, 'N/A'), '\n',
        '  ZIP: ', COALESCE(ref_zip, 'N/A'), '\n\n',
        'SCORING CONTEXT:\n',
        '  Name similarity (Jaro-Winkler): ', name_jw, '\n',
        '  Address similarity (Jaro-Winkler): ', street_jw, '\n',
        '  ZIP exact match: ', CASE WHEN zip_exact = 1 THEN 'Yes' ELSE 'No' END, '\n\n',
        '<DOMAIN_SPECIFIC_RULES>\n\n',  -- Insert domain profile rules here
        'Respond with ONLY a JSON object:\n',
        '{"decision": "match|no_match|uncertain", "confidence": 0.0-1.0, "reasoning": "brief explanation"}'
    )
)
```

**Domain-specific rules:** Insert the domain profile's abbreviation mappings, entity type distinctions (e.g., different Class of Trade = different entity), and chain-store rules into the `<DOMAIN_SPECIFIC_RULES>` placeholder.

### Step 3: Accept Matches with Guard Rails

Accept classified matches only if guard rails pass:

```sql
INSERT INTO batch_classify_matches
SELECT ...
FROM batch_classify_results
WHERE decision = 'match'
  AND confidence >= 0.80
  -- Guard rails: require minimum name OR address alignment
  AND (name_jw >= 0.70 OR street_jw >= 0.85)
  AND (street_jw >= 0.60 OR zip_exact = 1);
```

### Model Selection

| Model | Cost | Use When |
|-------|------|----------|
| `claude-haiku-4-5` | Low | Default for batch classify — fast, cheap, good at structured JSON |
| `mistral-large2` | Low | Alternative if Claude is unavailable |
| `claude-sonnet-4` | Medium | Use if haiku produces too many `uncertain` results |

### Composite Score Formula

Weight name similarity, address similarity, and ZIP match:

```sql
ROUND(0.40 * name_jw + 0.35 * street_jw + 0.25 * zip_exact, 4) AS composite_search_score
```

Adjust weights based on domain. Address-heavy domains (retail pharmacies) may increase street_jw weight. Name-heavy domains (financial services) may increase name_jw weight.

**MANDATORY STOPPING POINT**: Present Tier 1.5 results (classified count, match count, sample matches) for user review.

---

## Tier 2: Agentic Search

**Goal:** Resolve the hardest cases using a Cortex Agent with multi-tool access and reasoning capabilities.

**Delegate to:** `cortex-agent` skill for agent definition. This section provides the ER-specific agent configuration.

### Agent Design

The agent has three tools, in strict priority order:

1. **Corpus_Search** (`cortex_search`): Fuzzy search against the reference Cortex Search Service. PRIMARY — always try first.
2. **Corpus_Query** (`cortex_analyst_text_to_sql`): SQL queries against the reference table via semantic model. FALLBACK — use when search returns no results.
3. **Web_Search** (`web_search`): Public web search. LAST RESORT — use only after both internal tools fail. Useful for confirming closures, rebrands, acquisitions.

See `agent-definition.md` for the full agent specification template.

### Decision Types

The agent must use one of these decisions:

| Decision | When | Confidence |
|----------|------|-----------|
| `match` | Same entity confirmed (name + address align) | 0.80-1.0 |
| `probable_match` | Likely same but some ambiguity (minor discrepancy) | 0.60-0.80 |
| `no_match` | Exhausted all search strategies, no match found | 0.0-0.50 |
| `investigate` | Ambiguous, requires human review | 0.40-0.70 |
| `location_closed` | Entity identified but confirmed permanently closed | 0.70-1.0 |
| `new_record_needed` | Entity confirmed active but missing from reference corpus | 0.70-1.0 |

### Budget Constraints

Configure the agent with strict budget limits to control cost:

```yaml
orchestration:
  budget:
    seconds: 90       # Wall-clock limit per entity
    tokens: 16000     # Token limit per entity
```

Additionally, enforce tool call limits in the orchestration instructions:
- Maximum 3 Corpus_Search calls per entity
- Maximum 1 Corpus_Query call per entity
- Maximum 1 Web_Search call per entity
- Total maximum 6 tool calls — decide with best evidence after limit reached

### Work Queue

Create a work queue table to track agent processing:

```sql
CREATE OR REPLACE TABLE agent_work_queue (
    queue_id                INT AUTOINCREMENT,
    source_entity_id        STRING NOT NULL,
    batch_id                INT,
    -- Entity details (for prompt building)
    entity_name             STRING,
    entity_address          STRING,
    entity_city             STRING,
    entity_state            STRING,
    entity_zip              STRING,
    -- Context
    name_variants           VARIANT,     -- JSON array of name variants
    match_candidates        VARIANT,     -- JSON array of top embedding candidates
    domain_context          VARIANT,     -- Domain-specific context (class of trade, etc.)
    -- Processing state
    status                  STRING DEFAULT 'PENDING',
    worker_id               STRING,
    started_at              TIMESTAMP_NTZ,
    completed_at            TIMESTAMP_NTZ,
    error_message           STRING,
    retry_count             INT DEFAULT 0,
    PRIMARY KEY (queue_id)
);
```

Populate with entities not resolved by Tier 1 or Tier 1.5:
```sql
INSERT INTO agent_work_queue (source_entity_id, ...)
SELECT ...
FROM source_entities
WHERE source_entity_id NOT IN (SELECT source_entity_id FROM highconf_matches)
  AND source_entity_id NOT IN (SELECT source_entity_id FROM batch_classify_matches);
```

Assign balanced batches using `NTILE(<num_workers>)` for parallel processing.

### Orchestration

See `orchestration.md` for the full orchestration SP template. Key components:

1. **Prompt builder** — Constructs per-entity prompt with: entity details, name variants, embedding candidates, domain context, search strategies, and response format
2. **Agent caller** — Invokes `SNOWFLAKE.CORTEX.DATA_AGENT_RUN` with the built prompt
3. **Response parser** — Extracts structured JSON from agent response (handles code fences, partial JSON, fallback)
4. **Validation scorer** — Computes JW scores between matched entity and original for audit
5. **Entity discovery recorder** — When the agent discovers entities missing from the reference corpus, records them for future enrichment
6. **Retry logic** — Exponential backoff for rate limits, configurable max retries

### Parallel Execution

Use a Snowflake Task DAG for parallel processing:
```
root_task → N worker tasks (one per batch) → finalizer_task
```

Worker count: Use `NTILE(N)` where N is under 100 (Snowflake child task limit is 100). Typical: 50-98 workers depending on volume.

See `orchestration.md` for the Task DAG DDL template.

### Entity Discovery

When the agent uses web search and confirms an entity exists but the reference corpus lacks it, record the discovery:

```sql
CREATE OR REPLACE TABLE discovered_entities (
    discovery_id            INT AUTOINCREMENT,
    source_entity_id        STRING NOT NULL,
    discovered_name         STRING NOT NULL,
    discovered_address      STRING,
    discovered_city         STRING,
    discovered_state        STRING,
    discovered_zip          STRING,
    discovery_source        STRING,       -- 'web_search' | 'agent_inference'
    discovery_reasoning     STRING,
    confidence              FLOAT,
    web_sources             VARIANT,      -- Array of {url, title, snippet}
    verified                BOOLEAN DEFAULT FALSE,
    created_at              TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (discovery_id)
);
```

These discoveries feed back into the reference corpus after human verification, improving future match rates.

**MANDATORY STOPPING POINT**: Present Tier 2 agent results summary (decision distribution, web search usage, sample matches/non-matches/closures) for user review.

---

## Crosswalk Assembly

Combine all tiers into a unified crosswalk with a uniform schema:

```sql
CREATE OR REPLACE VIEW crosswalk AS

-- Tier 1: High-confidence matches
SELECT
    source_entity_id,
    reference_entity_id,
    decision,
    confidence,
    match_method,
    NULL AS agent_reasoning,
    NULL AS web_sources
FROM highconf_matches

UNION ALL

-- Tier 1.5: Batch classify matches
SELECT
    source_entity_id,
    reference_entity_id,
    decision,
    confidence,
    'tier15_batch_classify' AS match_method,
    classify_reasoning AS agent_reasoning,
    NULL AS web_sources
FROM batch_classify_matches

UNION ALL

-- Tier 2: Agent matches (match and probable_match)
SELECT
    source_entity_id,
    matched_reference_id AS reference_entity_id,
    decision,
    confidence,
    'tier2_agent' AS match_method,
    agent_reasoning,
    web_sources
FROM agent_results
WHERE decision IN ('match', 'probable_match')
  -- Guard rails for agent probable_match:
  AND (decision = 'match'
       OR (confidence >= 0.70 AND (zip_exact = 1 OR address_jw >= 0.80)))

UNION ALL

-- Tier 2: Location closed (no reference match, but entity identified)
SELECT
    source_entity_id,
    matched_reference_id AS reference_entity_id,
    decision,
    confidence,
    'tier2_agent_closed' AS match_method,
    agent_reasoning,
    web_sources
FROM agent_results
WHERE decision = 'location_closed'

UNION ALL

-- Tier 2: New record needed (entity exists but not in reference)
SELECT
    source_entity_id,
    NULL AS reference_entity_id,
    decision,
    confidence,
    'tier2_agent_new_record' AS match_method,
    agent_reasoning,
    web_sources
FROM agent_results
WHERE decision = 'new_record_needed';
```

---

## Cost Control

### Model Selection by Tier

| Tier | Model | Cost per Entity | Notes |
|------|-------|----------------|-------|
| 1 | N/A (SQL only) | ~$0 | Pure SQL, no LLM |
| 1.5 | `claude-haiku-4-5` | ~$0.001-0.005 | Single AI_COMPLETE call |
| 2 | `claude-haiku-4-5` (orchestration) | ~$0.05-0.20 | Multi-tool agent, 2-6 calls |

### Volume Planning

For a dataset of N source entities:
- Tier 1 processes all N, resolves ~0.6N-0.8N — cost: ~$0
- Tier 1.5 processes ~0.2N-0.4N, resolves ~0.05N-0.15N — cost: ~$0.001 * 0.3N
- Tier 2 processes ~0.05N-0.2N — cost: ~$0.10 * 0.1N

**Example:** 10,000 entities → Tier 1 resolves ~7,000, Tier 1.5 resolves ~1,500, Tier 2 processes ~1,500 → total LLM cost ~$150-300.

### Cost Monitoring

Track cost per tier and per entity using execution metadata (tokens_used, processing_time_seconds, model_used) stored in agent_results.

---

## Threshold Tuning

### Manual Review Approach

1. Sample 50-100 entities from each tier's output
2. Human-verify each match as correct/incorrect
3. Compute precision per tier
4. Adjust thresholds:
   - If Tier 1 precision < 95%: tighten cosine_sim or name_jw thresholds by 0.02
   - If Tier 1 recall is too low (too many entities going to Tier 1.5): loosen thresholds by 0.01-0.02
   - If Tier 1.5 `uncertain` rate > 30%: upgrade model from haiku to a more capable model
   - If Tier 2 `no_match` rate > 40%: review whether the reference corpus is comprehensive enough

### LLM-as-a-Judge (Automated Quality)

For ongoing quality monitoring, use an LLM judge to evaluate a sample of decisions:

1. Sample N agent results (stratified by decision type)
2. Provide the judge with: entity details, agent decision, agent reasoning, reference match details
3. Judge evaluates: decision correctness, reasoning quality, whether web search was appropriately used
4. Aggregate findings into quality metrics and improvement recommendations

This can be operationalized as a post-pipeline quality gate. See `operationalize.md` for the quality loop pattern.
