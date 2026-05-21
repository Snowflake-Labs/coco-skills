# Contrastive Embeddings for Entity Resolution

Fine-tune a transformer encoder via supervised contrastive learning (SupConLoss) to produce domain-adapted embeddings for entity matching. Replaces or supplements `AI_EMBED`-based Tier 2 fuzzy matching with embeddings trained specifically on the customer's entity data.

## When to Use

| Condition | Use Contrastive | Use `AI_EMBED` |
|-----------|----------------|----------------|
| Labeled clusters or ground truth pairs available (500+) | Yes | Fallback |
| GPU compute pool available | Yes | N/A (serverless) |
| High-stakes matching (medical, financial, legal) | Recommended | Acceptable |
| Quick prototype or <500 labeled pairs | No | Yes |
| Multilingual entity data | Yes (with NER) | Acceptable |
| Need per-record cost = $0 at inference time | Yes (one-time training cost) | No ($0.0001/record) |

**Standalone mode:** Contrastive embeddings can be used as the sole matching approach — the trained encoder produces embeddings, cosine similarity does blocking, and a threshold sweep finds the optimal decision boundary. No Tier 1 (deterministic) or Tier 3 (AI-judged) tiers are required, though both can be layered on top.

**Add-on mode:** Contrastive embeddings replace the `AI_EMBED` call in Tier 2 of Path A (or the embedding step of Path B). The rest of the pipeline (Tier 1 deterministic, Tier 3 AI-judged, blocking, HITL review) remains unchanged. This improves cosine similarity quality, which means fewer pairs need Tier 3 escalation — reducing both cost and latency.

## Prerequisites

1. **Ground truth** — Either:
   - Existing labeled match/non-match pairs (from a prior HITL review cycle or external source)
   - Cluster assignments (records grouped by known entity identity)
   - Minimum: ~500 entities across ~200+ clusters for reasonable training
2. **GPU compute pool** — `GPU_NV_S` (NVIDIA T4, 16 GB VRAM) is sufficient for all three recommended models
3. **External access integrations:**
   - `PYPI_EAI` — pip package installation (transformers, torch, sentencepiece)
   - `HF_EAI` — HuggingFace model download + GitHub access (for spaCy NER models when NER enabled)
4. **Network rule** — Must include HuggingFace and (when NER is enabled) GitHub domains:

```sql
CREATE OR REPLACE NETWORK RULE <database>.<schema>.HF_RULE
    MODE = EGRESS
    TYPE = HOST_PORT
    VALUE_LIST = (
        'huggingface.co', 'hf.co',
        'cdn-lfs.huggingface.co', 'cdn-lfs.hf.co',
        'cdn-lfs-us-1.huggingface.co', 'cdn-lfs-us-1.hf.co',
        'data.hub.huggingface.co', 'hub-ci.huggingface.co',
        'cas-bridge.xethub.hf.co',
        -- GitHub domains (required for spaCy NER model download)
        'github.com',
        'raw.githubusercontent.com',
        'objects.githubusercontent.com',
        'release-assets.githubusercontent.com'
    );
```

**Note:** The GitHub domains are required when NER is enabled — spaCy's `en_core_web_sm` model is hosted on GitHub, not PyPI. The download chain involves redirects through `raw.githubusercontent.com` (compatibility check), `github.com` (release page), and `release-assets.githubusercontent.com` (actual wheel).

## Model Selection

Based on a 10-experiment ablation study across two benchmark datasets (DBLP-ACM bibliographic matching, Walmart-Amazon product matching):

| Model | Params | Dim | Best For | DBLP-ACM F1 | Walmart-Amazon F1 |
|-------|--------|-----|----------|-------------|-------------------|
| `roberta-base` | 125M | 768 | English-only ER | **0.9904** | **0.8701** |
| `xlm-roberta-base` | 278M | 768 | Multilingual ER | 0.9646 (0.9818 w/ NER) | 0.8472 (0.8556 w/ NER) |
| `sentence-transformers/all-MiniLM-L6-v2` | 33M | 384 | Latency-critical / resource-constrained | 0.9797 | 0.7706 |

**Recommendation logic:**

```
IF data is English-only:
    Use roberta-base (best F1, NER disabled)
ELSE IF data is multilingual or mixed-language:
    Use xlm-roberta-base (enable NER)
ELSE IF GPU memory is severely constrained OR latency is critical:
    Use all-MiniLM-L6-v2 (smallest, fastest)
```

**Key finding:** `roberta-base` (125M params) outperforms `xlm-roberta-base` (278M params) on English ER because it dedicates all capacity to English. Bigger is not always better — language-specific capacity matters more than raw parameter count.

## NER Mode Selection

DITTO-style NER tag injection (Li et al., VLDB 2021) inserts entity-type tags from spaCy NER into serialized record text within `[VAL]` regions.

| Data Characteristics | Model | NER Mode | Rationale |
|---------------------|-------|----------|-----------|
| English structured data (databases, academic records) | roberta-base | `none` | RoBERTa already encodes English entity types from pretraining |
| English product/e-commerce data | roberta-base | `none` | NER is neutral (-0.23pp F1 on Walmart-Amazon) |
| English data + multilingual model | xlm-roberta-base | `ditto-general` or `ditto-product` | NER compensates for XLM-R's diluted English capacity (+1.7pp on DBLP-ACM) |
| Multilingual data | xlm-roberta-base | `ditto-general` | NER provides explicit entity-type signals across languages |

**NER modes:**

- `ditto-general` — Preserves fine-grained entity labels. Use for structured/bibliographic data.
  - PERSON, ORG, PRODUCT, NUM, ID tags
- `ditto-product` — Collapses to coarser categories. Use for e-commerce/product data.
  - PRODUCT (absorbs PERSON, GPE, LOC, NORP), NUM, ID tags
- `none` — No NER preprocessing. Use with English-specialized encoders.

**NER special tokens** (added to tokenizer alongside `[COL]`, `[VAL]`):
`[PERSON]`, `[ORG]`, `[PRODUCT]`, `[NUM]`, `[ID]`

The `[ID]` tag is also applied via regex to alphanumeric strings of 7+ characters (model numbers, SKUs) regardless of spaCy NER output.

## Section 1: Data Preparation

### Record serialization with [COL]/[VAL] tokens

Serialize each entity record into a flat text string. Adapt the column list to the customer's schema.

```sql
-- Example for name+address entities
CREATE OR REPLACE TABLE serialized_entities AS
SELECT
    source_id,
    source_table,
    '[COL] name [VAL] ' || COALESCE(normalized_name, '') ||
    ' [COL] street [VAL] ' || COALESCE(normalized_street, '') ||
    ' [COL] city [VAL] ' || COALESCE(normalized_city, '') ||
    ' [COL] state [VAL] ' || COALESCE(normalized_state, '') ||
    ' [COL] zip [VAL] ' || COALESCE(normalized_zip, '') AS serialized_text
FROM normalized_entities;
```

```sql
-- Example for product entities
CREATE OR REPLACE TABLE serialized_entities AS
SELECT
    source_id,
    source_table,
    '[COL] brand [VAL] ' || COALESCE(brand, '') ||
    ' [COL] title [VAL] ' || COALESCE(title, '') ||
    ' [COL] modelno [VAL] ' || COALESCE(model_number, '') ||
    ' [COL] category [VAL] ' || COALESCE(category, '') ||
    ' [COL] price [VAL] ' || COALESCE(price::STRING, '') AS serialized_text
FROM normalized_entities;
```

**Adapt columns** to the customer's entity schema. Every attribute that could help disambiguate entities should be included. Column names in `[COL]` tokens should be short, lowercase descriptors.

### Cluster assignment for training

Contrastive learning requires cluster labels (which records refer to the same entity). Derive from ground truth pairs:

```sql
-- From ground truth match pairs, assign cluster IDs via iterative Union-Find
-- (see matching.md Section 4 for the full iterative Union-Find pattern)

-- Step 1: Initialize each entity as its own cluster
CREATE OR REPLACE TABLE entity_clusters AS
SELECT DISTINCT source_id AS entity_id, source_id AS cluster_id
FROM (
    SELECT source_id_left AS source_id FROM ground_truth
    UNION
    SELECT source_id_right AS source_id FROM ground_truth
);

-- Step 2: Propagate smallest cluster_id across ground truth edges
-- (iterate until convergence — typically 3-10 iterations)
DECLARE changes INT DEFAULT 1;
BEGIN
    WHILE (changes > 0) DO
        UPDATE entity_clusters ec
        SET cluster_id = sub.min_cluster
        FROM (
            SELECT ec2.entity_id,
                   LEAST(ec2.cluster_id, MIN(ec_linked.cluster_id)) AS min_cluster
            FROM entity_clusters ec2
            JOIN ground_truth gt
                ON ec2.entity_id = gt.source_id_left OR ec2.entity_id = gt.source_id_right
            JOIN entity_clusters ec_linked
                ON ec_linked.entity_id = CASE
                    WHEN ec2.entity_id = gt.source_id_left THEN gt.source_id_right
                    ELSE gt.source_id_left
                END
            GROUP BY ec2.entity_id, ec2.cluster_id
            HAVING LEAST(ec2.cluster_id, MIN(ec_linked.cluster_id)) < ec2.cluster_id
        ) sub
        WHERE ec.entity_id = sub.entity_id;
        changes := SQLROWCOUNT;
    END WHILE;
END;

-- Step 3: Include singleton entities (entities with no ground truth pairs)
INSERT INTO entity_clusters (entity_id, cluster_id)
SELECT source_id, source_id
FROM serialized_entities
WHERE source_id NOT IN (SELECT entity_id FROM entity_clusters);
```

### Pretrain entities (training-ready table)

```sql
CREATE OR REPLACE TABLE pretrain_entities AS
SELECT
    s.source_id,
    s.source_table,
    s.serialized_text,
    ec.cluster_id
FROM serialized_entities s
JOIN entity_clusters ec ON s.source_id = ec.entity_id;
```

## Section 2: Training Job Submission

### Stored procedure template

Create a stored procedure that submits the contrastive training job to the GPU compute pool:

```sql
CREATE OR REPLACE PROCEDURE submit_contrastive_training_job()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-ml-python')
HANDLER = 'run'
AS
$$
def run(session):
    from snowflake.ml.jobs import submit_file

    job = submit_file(
        file="train_contrastive_ner.py",       -- Training script (upload to stage first)
        compute_pool="<COMPUTE_POOL_NAME>",     -- GPU_NV_S or GPU_NV_M
        stage_name="<SCHEMA>.ML_JOB_STAGE",
        pip_requirements=[
            "transformers>=4.30.0",
            "torch>=2.0.0",
            "sentencepiece",
            "spacy>=3.5.0,<3.8",               -- Only needed if NER enabled
        ],
        external_access_integrations=["PYPI_EAI", "HF_EAI"],
        env_vars={
            "ER_DATABASE": "<DATABASE>",
            "ER_SCHEMA": "<SCHEMA>",
            "ER_HF_MODEL_NAME": "roberta-base",         -- or xlm-roberta-base, all-MiniLM-L6-v2
            "ER_NER_MODE": "none",                       -- or ditto-general, ditto-product
            "ER_EMBEDDING_DIM": "768",                   -- 768 for roberta/xlm-r, 384 for minilm
            "ER_NUM_EPOCHS": "10",
            "ER_BATCH_SIZE": "64",                       -- 32 for xlm-roberta-base (larger model)
            "ER_LEARNING_RATE": "2e-5",
            "ER_TEMPERATURE": "0.07",
            "ER_WARMUP_RATIO": "0.05",
            "ER_WEIGHT_DECAY": "0.01",
        },
        session=session,
    )
    return f"Submitted job: {job.id}"
$$;
```

**Replace** all `<PLACEHOLDER>` values with the customer's database, schema, compute pool, and model choices from discovery.

### Training hyperparameters

| Parameter | Default | Notes |
|-----------|---------|-------|
| Epochs | 10 | Sufficient for convergence on most ER datasets |
| Batch size | 64 | Use 32 for XLM-RoBERTa-base (278M params, higher memory) |
| Learning rate | 2e-5 | Standard for transformer fine-tuning |
| Temperature (tau) | 0.07 | Controls sharpness of contrastive similarity distribution |
| Warmup ratio | 0.05 | 5% of training steps for learning rate warmup |
| Weight decay | 0.01 | AdamW regularization |
| Gradient clipping | 1.0 | Max gradient norm |

### Training script overview

The training script (`train_contrastive_ner.py`) implements:

1. **SupConLoss** (Khosla et al., 2020) — pulls same-cluster embeddings together, pushes different-cluster embeddings apart
2. **[COL]/[VAL] special tokens** — registered in tokenizer so the model learns attribute structure
3. **NER tag injection** (optional) — spaCy `en_core_web_sm` tags inserted within `[VAL]` spans at training time and embedding time
4. **Mean pooling + L2 normalization** — attention-weighted mean pool over token hidden states, normalized to unit hypersphere
5. **Embedding upload** — batch-encodes all entities post-training, uploads as `VECTOR(FLOAT, n)` via Snowpark

**Delegate to:** `machine-learning` skill for compute pool setup, stage management, and job monitoring.

## Section 3: Post-Training Embedding Table

After the training job completes, the `CONTRASTIVE_EMBEDDINGS` table is populated:

```sql
-- Schema of the output table (created by training script)
-- CONTRASTIVE_EMBEDDINGS (
--     SOURCE_ID     STRING,
--     SOURCE_TABLE  STRING,
--     EMBEDDING     VECTOR(FLOAT, 768)   -- or 384 for MiniLM
-- )

-- Verify embeddings were generated
SELECT COUNT(*) AS embedding_count FROM contrastive_embeddings;
SELECT SOURCE_TABLE, COUNT(*) FROM contrastive_embeddings GROUP BY SOURCE_TABLE;
```

## Section 4: Blocking via Cosine Similarity

Contrastive embeddings replace traditional blocking + Tier 2 fuzzy matching with a single cosine similarity computation. The trained embeddings produce much tighter clusters than `AI_EMBED`, so cosine similarity alone is highly discriminative.

### Cross-source blocking (matching across datasets)

```sql
CREATE OR REPLACE TABLE blocking_candidates AS
SELECT
    e1.SOURCE_ID AS ID_LEFT,
    e2.SOURCE_ID AS ID_RIGHT,
    e1.SOURCE_TABLE AS SOURCE_LEFT,
    e2.SOURCE_TABLE AS SOURCE_RIGHT,
    VECTOR_COSINE_SIMILARITY(e1.EMBEDDING, e2.EMBEDDING) AS COSINE_SIM
FROM contrastive_embeddings e1
JOIN contrastive_embeddings e2
    ON e1.SOURCE_TABLE != e2.SOURCE_TABLE
    AND e1.SOURCE_ID < e2.SOURCE_ID
WHERE VECTOR_COSINE_SIMILARITY(e1.EMBEDDING, e2.EMBEDDING) > 0.50;
```

### Deduplication blocking (within same dataset)

```sql
CREATE OR REPLACE TABLE blocking_candidates AS
SELECT
    e1.SOURCE_ID AS ID_LEFT,
    e2.SOURCE_ID AS ID_RIGHT,
    VECTOR_COSINE_SIMILARITY(e1.EMBEDDING, e2.EMBEDDING) AS COSINE_SIM
FROM contrastive_embeddings e1
JOIN contrastive_embeddings e2
    ON e1.SOURCE_ID < e2.SOURCE_ID
WHERE VECTOR_COSINE_SIMILARITY(e1.EMBEDDING, e2.EMBEDDING) > 0.50;
```

**Threshold 0.50** is deliberately low for blocking — it captures all plausible candidates. The actual match/no-match decision is made during the threshold sweep below.

**Scaling note:** For large datasets (>100K entities), the cross-join becomes expensive. Use a pre-filter (e.g., geographic blocking key) to partition entities before computing cosine similarity within partitions:

```sql
-- Partitioned blocking for large datasets
CREATE OR REPLACE TABLE blocking_candidates AS
SELECT
    e1.SOURCE_ID AS ID_LEFT,
    e2.SOURCE_ID AS ID_RIGHT,
    VECTOR_COSINE_SIMILARITY(e1.EMBEDDING, e2.EMBEDDING) AS COSINE_SIM
FROM contrastive_embeddings e1
JOIN normalized_entities n1 ON e1.SOURCE_ID = n1.SOURCE_ID
JOIN contrastive_embeddings e2
    ON e1.SOURCE_TABLE != e2.SOURCE_TABLE
    AND e1.SOURCE_ID < e2.SOURCE_ID
JOIN normalized_entities n2 ON e2.SOURCE_ID = n2.SOURCE_ID
WHERE n1.NORMALIZED_STATE = n2.NORMALIZED_STATE     -- Geographic partition
    AND VECTOR_COSINE_SIMILARITY(e1.EMBEDDING, e2.EMBEDDING) > 0.50;
```

## Section 5: Threshold Sweep

Evaluate precision, recall, and F1 at multiple cosine similarity thresholds to find the optimal decision boundary.

```sql
CREATE OR REPLACE TABLE threshold_results AS
WITH thresholds AS (
    SELECT column1 AS threshold FROM VALUES
        (0.50),(0.55),(0.60),(0.65),(0.70),(0.72),(0.74),(0.76),
        (0.78),(0.80),(0.82),(0.84),(0.86),(0.88),(0.90),(0.92),(0.94),(0.96)
),
predictions AS (
    SELECT
        t.threshold,
        bc.ID_LEFT,
        bc.ID_RIGHT,
        bc.COSINE_SIM
    FROM thresholds t
    CROSS JOIN blocking_candidates bc
    WHERE bc.COSINE_SIM >= t.threshold
),
metrics AS (
    SELECT
        p.threshold,
        COUNT(DISTINCT CASE WHEN gt.SOURCE_ID_LEFT IS NOT NULL
              THEN p.ID_LEFT || '-' || p.ID_RIGHT END) AS tp,
        COUNT(DISTINCT CASE WHEN gt.SOURCE_ID_LEFT IS NULL
              THEN p.ID_LEFT || '-' || p.ID_RIGHT END) AS fp,
        (SELECT COUNT(*) FROM ground_truth)
            - COUNT(DISTINCT CASE WHEN gt.SOURCE_ID_LEFT IS NOT NULL
              THEN p.ID_LEFT || '-' || p.ID_RIGHT END) AS fn
    FROM predictions p
    LEFT JOIN ground_truth gt
        ON (p.ID_LEFT = gt.SOURCE_ID_LEFT AND p.ID_RIGHT = gt.SOURCE_ID_RIGHT)
        OR (p.ID_LEFT = gt.SOURCE_ID_RIGHT AND p.ID_RIGHT = gt.SOURCE_ID_LEFT)
    GROUP BY p.threshold
)
SELECT
    threshold,
    tp, fp, fn,
    ROUND(tp / NULLIF(tp + fp, 0), 4) AS precision,
    ROUND(tp / NULLIF(tp + fn, 0), 4) AS recall,
    ROUND(2.0 * (tp / NULLIF(tp + fp, 0)) * (tp / NULLIF(tp + fn, 0))
        / NULLIF((tp / NULLIF(tp + fp, 0)) + (tp / NULLIF(tp + fn, 0)), 0), 4) AS f1
FROM metrics
ORDER BY threshold;
```

### Optimal threshold selection

```sql
-- Select the threshold that maximizes F1
SELECT * FROM threshold_results ORDER BY f1 DESC LIMIT 1;
```

### Materialize final matches

```sql
CREATE OR REPLACE TABLE predicted_matches AS
SELECT
    ID_LEFT, ID_RIGHT, SOURCE_LEFT, SOURCE_RIGHT, COSINE_SIM,
    'match' AS decision,
    COSINE_SIM AS confidence,
    'contrastive_embedding' AS match_method
FROM blocking_candidates
WHERE COSINE_SIM >= <optimal_threshold>;   -- Replace with threshold from sweep
```

## Section 6: Integration with Existing Tiers

When used as an add-on to the standard Path A pipeline, contrastive embeddings replace the `AI_EMBED` step in Tier 2. This improves similarity quality and reduces the number of pairs that need expensive Tier 3 (AI-judged) escalation.

### Replace Tier 2 embedding generation

Instead of:
```sql
-- Old: AI_EMBED (general-purpose, per-record cost)
AI_EMBED('snowflake-arctic-embed-l-v2.0', concatenated_text) AS embedding
```

Use the contrastive embeddings table directly:
```sql
-- New: Pre-computed contrastive embeddings (zero marginal cost)
SELECT source_id, embedding FROM contrastive_embeddings
```

### Layer with Tier 1 and Tier 3

```sql
-- Score consolidation: Tier 1 (exact ID) + Contrastive (replaces Tier 2) + Tier 3 (AI-judged)
CREATE OR REPLACE TABLE match_results AS
-- Tier 1: Deterministic
SELECT id_left, id_right, decision, confidence, match_method, matched_on
FROM tier1_results
UNION ALL
-- Contrastive: High-confidence matches (above match threshold)
SELECT id_left, id_right, 'match' AS decision, cosine_sim AS confidence,
       'contrastive_embedding' AS match_method, NULL AS matched_on
FROM blocking_candidates
WHERE cosine_sim >= <match_threshold>
    AND (id_left, id_right) NOT IN (SELECT id_left, id_right FROM tier1_results)
UNION ALL
-- Contrastive: Clear non-matches (below no_match threshold)
SELECT id_left, id_right, 'no_match' AS decision, cosine_sim AS confidence,
       'contrastive_embedding' AS match_method, NULL AS matched_on
FROM blocking_candidates
WHERE cosine_sim < <no_match_threshold>
    AND (id_left, id_right) NOT IN (SELECT id_left, id_right FROM tier1_results)
UNION ALL
-- Tier 3: AI-judged on probable matches (between thresholds)
SELECT id_left, id_right, ai_decision:label::STRING AS decision,
       ai_decision:score::FLOAT AS confidence, 'tier3_ai_judged' AS match_method, NULL
FROM tier3_results;
```

## Expected Performance Benchmarks

Results from the 10-experiment ablation study (for reference when discussing with customers):

| Dataset | Model | NER | F1 | Precision | Recall |
|---------|-------|-----|-----|-----------|--------|
| DBLP-ACM (bibliographic) | RoBERTa-base | none | **0.9904** | 98.2% | 99.9% |
| DBLP-ACM | RoBERTa-base | ditto-general | 0.9895 | 98.4% | 99.6% |
| DBLP-ACM | XLM-RoBERTa-base | ditto-general | 0.9818 | 97.0% | 99.4% |
| DBLP-ACM | MiniLM-L6-v2 | none | 0.9797 | 97.0% | 99.0% |
| DBLP-ACM | XLM-RoBERTa-base | none | 0.9646 | 94.4% | 98.6% |
| Walmart-Amazon (product) | RoBERTa-base | none | **0.8701** | 79.5% | 96.1% |
| Walmart-Amazon | RoBERTa-base | ditto-product | 0.8678 | 82.7% | 91.3% |
| Walmart-Amazon | XLM-RoBERTa-base | ditto-product | 0.8556 | 83.0% | 88.3% |
| Walmart-Amazon | XLM-RoBERTa-base | none | 0.8472 | 81.1% | 88.7% |
| Walmart-Amazon | MiniLM-L6-v2 | none | 0.7706 | 69.2% | 75.2% |

**Key observations for customer conversations:**
1. Model capacity matters most on noisy data (MiniLM-to-RoBERTa gap: +1.1pp on clean DBLP-ACM vs +10.0pp on noisy Walmart-Amazon)
2. NER helps multilingual models but not English-specialized ones
3. Clean, structured data reaches F1 > 0.98 even with the smallest model
4. Noisy, heterogeneous data benefits most from larger English-specialized encoders

## Cost Model

| Component | Cost Driver | Estimate |
|-----------|------------|----------|
| GPU training (one-time) | Compute pool credits, ~5-30 min on GPU_NV_S (T4) | ~1-5 credits per training run |
| Embedding generation | Included in training job (runs on same GPU) | $0 marginal |
| Blocking + threshold sweep | Warehouse compute (XS-MEDIUM) | ~0.5-2 credits |
| NER preprocessing | CPU within GPU container, adds ~10-17% to training time | Included in GPU cost |
| Retraining (periodic) | Same as initial training | ~1-5 credits per retrain |

**Comparison with `AI_EMBED` approach:**

| Metric | Contrastive | `AI_EMBED` |
|--------|------------|------------|
| Per-record embedding cost | $0 (pre-computed) | ~$0.0001/record |
| Training cost | ~1-5 credits (one-time) | $0 (no training) |
| F1 quality | Higher (domain-adapted) | Lower (general-purpose) |
| Setup complexity | Higher (GPU pool, EAIs, training script) | Lower (single SQL call) |
| Time to first result | ~30 min (training + embedding) | ~seconds (immediate) |
| Retraining needed? | Yes, when entity types or data distribution shift | No |
| Break-even volume | ~50K records (contrastive cheaper above this) | Below ~50K records |
