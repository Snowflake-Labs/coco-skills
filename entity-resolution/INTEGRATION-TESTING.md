# Integration Testing — Entity Resolution Skill

Benchmark the entity-resolution skill against classic academic ER datasets with known ground truth. Measures precision, recall, and F1 across all applicable matching techniques.

## 1. Introduction

### Purpose

The entity-resolution skill includes [domain-specific functional tests](src/tests/) that validate workflow correctness using small synthetic fixtures (10-12 records per domain). This document defines **benchmark integration tests** that evaluate the skill against published research datasets where ground truth is known, enabling:

- Quantitative skill quality assessment (precision, recall, F1)
- Technique comparison (Fuzzy vs AI-Judged vs Contrastive vs Full Pipeline)
- Regression detection across skill changes
- Evidence generation for `skill_evidence.yaml`

### Relationship to Existing Tests

| Test Suite | Scope | Records | Ground Truth | Goal |
|------------|-------|---------|-------------|------|
| Functional (existing) | 5 domain profiles | 10-12 each | Hand-crafted expected pairs | Workflow correctness |
| Benchmark (this doc) | 5 academic datasets | 1K-67K each | Published labeled pairs | Quality measurement |

### Methodology

Each benchmark follows the skill's standard workflow (Profile → Normalize → Block → Match → Score) but adds a held-out evaluation step. Ground truth pairs are split 70/30:

- **Training set (70%)** — Used by contrastive embeddings (Phase 4c) for supervised training
- **Test set (30%)** — Held out for evaluation of ALL techniques (including contrastive)

Non-contrastive techniques (Fuzzy, AI-Judged, Full Pipeline) do not use the training set at all — they are unsupervised/zero-shot. The split exists solely to give contrastive a fair comparison: it trains on the 70% and is evaluated on the same 30% as everyone else.

---

## 2. Dataset Catalog

All datasets are from the [Magellan Data Repository](https://sites.google.com/site/anhabordeaux/magellan/dataset-library) and the [DITTO benchmark suite](https://github.com/megagonlabs/ditto) — standard references in entity resolution research.

### 2.1 DBLP-ACM

| Property | Value |
|----------|-------|
| Domain | Bibliographic (academic publications) |
| Task | Cross-source matching |
| Table A records | 2,616 (DBLP) |
| Table B records | 2,294 (ACM) |
| Ground truth pairs | 2,224 |
| Key columns | `title`, `authors`, `venue`, `year` |
| Characteristics | Clean, well-structured; title and author fields are highly discriminative |
| Difficulty | Easy — high textual overlap between matching records |
| Skill profile | Generic (no authoritative IDs) |
| Best known contrastive F1 | 0.9904 (RoBERTa-base, NER disabled) |

### 2.2 DBLP-Scholar

| Property | Value |
|----------|-------|
| Domain | Bibliographic (academic publications) |
| Task | Cross-source matching (asymmetric scale) |
| Table A records | 2,616 (DBLP) |
| Table B records | 64,263 (Google Scholar) |
| Ground truth pairs | 5,347 |
| Key columns | `title`, `authors`, `venue`, `year` |
| Characteristics | Highly asymmetric (1:25 ratio); Scholar records are noisy with truncated titles and inconsistent author formatting |
| Difficulty | Medium — scale asymmetry + noise amplify false positive risk |
| Skill profile | Generic (no authoritative IDs) |

### 2.3 Walmart-Amazon

| Property | Value |
|----------|-------|
| Domain | Product (e-commerce) |
| Task | Cross-source matching |
| Table A records | 2,554 (Walmart) |
| Table B records | 22,074 (Amazon) |
| Ground truth pairs | 1,154 |
| Key columns | `title`, `category`, `brand`, `modelno`, `price` |
| Characteristics | Heterogeneous product descriptions; brand/model info inconsistent; price varies across sources |
| Difficulty | Hard — noisy, sparse, heterogeneous attributes |
| Skill profile | Retail/CPG or Generic |
| Best known contrastive F1 | 0.8701 (RoBERTa-base, NER disabled) |

### 2.4 Amazon-Google

| Property | Value |
|----------|-------|
| Domain | Product (e-commerce) |
| Task | Cross-source matching |
| Table A records | 1,363 (Amazon) |
| Table B records | 3,226 (Google Products) |
| Ground truth pairs | 1,300 |
| Key columns | `title`, `manufacturer`, `price` |
| Characteristics | Google side has very sparse attributes; manufacturer field often missing; price inconsistent |
| Difficulty | Hard — attribute sparsity on one side |
| Skill profile | Generic |

### 2.5 Abt-Buy

| Property | Value |
|----------|-------|
| Domain | E-commerce products |
| Task | Cross-source matching |
| Table A records | 1,081 (Abt.com) |
| Table B records | 1,092 (Buy.com) |
| Ground truth pairs | 1,098 |
| Key columns | `name`, `description`, `price` |
| Characteristics | Long product descriptions; name field contains most signal; description field has varying verbosity |
| Difficulty | Medium — description-heavy matching |
| Skill profile | Generic |

---

## 3. Data Preparation

### 3.1 Obtaining the Data

Download the datasets from the Magellan Data Repository or the DITTO benchmark GitHub. Each dataset consists of two CSV files (tableA.csv, tableB.csv) and a ground truth mapping file.

Stage the CSV files in Snowflake:

```sql
-- Create a named stage for benchmark data
CREATE STAGE IF NOT EXISTS ER_BENCHMARK_STAGE
    FILE_FORMAT = (TYPE = CSV FIELD_OPTIONALLY_ENCLOSED_BY = '"' SKIP_HEADER = 1
                   FIELD_DELIMITER = ',' ESCAPE_UNENCLOSED_FIELD = NONE);

-- Upload CSVs (from local machine)
-- PUT file:///path/to/dblp_acm/tableA.csv @ER_BENCHMARK_STAGE/dblp_acm/;
-- PUT file:///path/to/dblp_acm/tableB.csv @ER_BENCHMARK_STAGE/dblp_acm/;
-- PUT file:///path/to/dblp_acm/matches.csv @ER_BENCHMARK_STAGE/dblp_acm/;
-- (repeat for each dataset)
```

### 3.2 Schema Definitions

Each dataset gets three tables: `<DATASET>_A`, `<DATASET>_B`, and `<DATASET>_GROUND_TRUTH`.

#### DBLP-ACM

```sql
CREATE OR REPLACE TABLE DBLP_ACM_A (
    id        VARCHAR,
    title     VARCHAR,
    authors   VARCHAR,
    venue     VARCHAR,
    year      INT
);

CREATE OR REPLACE TABLE DBLP_ACM_B (
    id        VARCHAR,
    title     VARCHAR,
    authors   VARCHAR,
    venue     VARCHAR,
    year      INT
);

CREATE OR REPLACE TABLE DBLP_ACM_GROUND_TRUTH (
    source_id_left   VARCHAR,   -- References DBLP_ACM_A.id
    source_id_right  VARCHAR    -- References DBLP_ACM_B.id
);
```

#### DBLP-Scholar

```sql
CREATE OR REPLACE TABLE DBLP_SCHOLAR_A (
    id        VARCHAR,
    title     VARCHAR,
    authors   VARCHAR,
    venue     VARCHAR,
    year      INT
);

CREATE OR REPLACE TABLE DBLP_SCHOLAR_B (
    id        VARCHAR,
    title     VARCHAR,
    authors   VARCHAR,
    venue     VARCHAR,
    year      INT
);

CREATE OR REPLACE TABLE DBLP_SCHOLAR_GROUND_TRUTH (
    source_id_left   VARCHAR,
    source_id_right  VARCHAR
);
```

#### Walmart-Amazon

```sql
CREATE OR REPLACE TABLE WALMART_AMAZON_A (
    id        VARCHAR,
    title     VARCHAR,
    category  VARCHAR,
    brand     VARCHAR,
    modelno   VARCHAR,
    price     FLOAT
);

CREATE OR REPLACE TABLE WALMART_AMAZON_B (
    id        VARCHAR,
    title     VARCHAR,
    category  VARCHAR,
    brand     VARCHAR,
    modelno   VARCHAR,
    price     FLOAT
);

CREATE OR REPLACE TABLE WALMART_AMAZON_GROUND_TRUTH (
    source_id_left   VARCHAR,
    source_id_right  VARCHAR
);
```

#### Amazon-Google

```sql
CREATE OR REPLACE TABLE AMAZON_GOOGLE_A (
    id            VARCHAR,
    title         VARCHAR,
    description   VARCHAR,
    manufacturer  VARCHAR,
    price         FLOAT
);

CREATE OR REPLACE TABLE AMAZON_GOOGLE_B (
    id            VARCHAR,
    title         VARCHAR,
    description   VARCHAR,
    manufacturer  VARCHAR,
    price         FLOAT
);

CREATE OR REPLACE TABLE AMAZON_GOOGLE_GROUND_TRUTH (
    source_id_left   VARCHAR,
    source_id_right  VARCHAR
);
```

#### Abt-Buy

```sql
CREATE OR REPLACE TABLE ABT_BUY_A (
    id           VARCHAR,
    name         VARCHAR,
    description  VARCHAR,
    price        FLOAT
);

CREATE OR REPLACE TABLE ABT_BUY_B (
    id           VARCHAR,
    name         VARCHAR,
    description  VARCHAR,
    price        FLOAT
);

CREATE OR REPLACE TABLE ABT_BUY_GROUND_TRUTH (
    source_id_left   VARCHAR,
    source_id_right  VARCHAR
);
```

### 3.3 Data Loading

Load from the staged CSVs:

```sql
COPY INTO DBLP_ACM_A FROM @ER_BENCHMARK_STAGE/dblp_acm/tableA.csv;
COPY INTO DBLP_ACM_B FROM @ER_BENCHMARK_STAGE/dblp_acm/tableB.csv;
COPY INTO DBLP_ACM_GROUND_TRUTH FROM @ER_BENCHMARK_STAGE/dblp_acm/matches.csv;
-- Repeat for each dataset
```

### 3.4 Combined Source Table

The entity-resolution skill expects a single source table with a `source_table` discriminator. Create a combined view per dataset:

```sql
-- Example for DBLP-ACM (bibliographic)
CREATE OR REPLACE TABLE DBLP_ACM_SOURCE AS
SELECT id AS source_id, 'dblp' AS source_table,
       title AS raw_name, authors AS raw_authors, venue AS raw_venue, year AS raw_year
FROM DBLP_ACM_A
UNION ALL
SELECT id AS source_id, 'acm' AS source_table,
       title AS raw_name, authors AS raw_authors, venue AS raw_venue, year AS raw_year
FROM DBLP_ACM_B;

-- Example for Walmart-Amazon (product)
CREATE OR REPLACE TABLE WALMART_AMAZON_SOURCE AS
SELECT id AS source_id, 'walmart' AS source_table,
       title AS raw_name, category AS raw_category, brand AS raw_brand,
       modelno AS raw_modelno, price AS raw_price
FROM WALMART_AMAZON_A
UNION ALL
SELECT id AS source_id, 'amazon' AS source_table,
       title AS raw_name, category AS raw_category, brand AS raw_brand,
       modelno AS raw_modelno, price AS raw_price
FROM WALMART_AMAZON_B;

-- Example for Amazon-Google (product)
CREATE OR REPLACE TABLE AMAZON_GOOGLE_SOURCE AS
SELECT id AS source_id, 'amazon' AS source_table,
       title AS raw_name, description AS raw_description,
       manufacturer AS raw_manufacturer, price AS raw_price
FROM AMAZON_GOOGLE_A
UNION ALL
SELECT id AS source_id, 'google' AS source_table,
       title AS raw_name, description AS raw_description,
       manufacturer AS raw_manufacturer, price AS raw_price
FROM AMAZON_GOOGLE_B;

-- Example for Abt-Buy (e-commerce)
CREATE OR REPLACE TABLE ABT_BUY_SOURCE AS
SELECT id AS source_id, 'abt' AS source_table,
       name AS raw_name, description AS raw_description, price AS raw_price
FROM ABT_BUY_A
UNION ALL
SELECT id AS source_id, 'buy' AS source_table,
       name AS raw_name, description AS raw_description, price AS raw_price
FROM ABT_BUY_B;

-- DBLP-Scholar follows the same pattern as DBLP-ACM
CREATE OR REPLACE TABLE DBLP_SCHOLAR_SOURCE AS
SELECT id AS source_id, 'dblp' AS source_table,
       title AS raw_name, authors AS raw_authors, venue AS raw_venue, year AS raw_year
FROM DBLP_SCHOLAR_A
UNION ALL
SELECT id AS source_id, 'scholar' AS source_table,
       title AS raw_name, authors AS raw_authors, venue AS raw_venue, year AS raw_year
FROM DBLP_SCHOLAR_B;
```

### 3.5 Train/Test Split

Split ground truth 70/30 using a deterministic hash for reproducibility:

```sql
-- Template: replace <DATASET> with the dataset name
CREATE OR REPLACE TABLE <DATASET>_GT_TRAIN AS
SELECT * FROM <DATASET>_GROUND_TRUTH
WHERE ABS(HASH(source_id_left || '|' || source_id_right)) % 100 < 70;

CREATE OR REPLACE TABLE <DATASET>_GT_TEST AS
SELECT * FROM <DATASET>_GROUND_TRUTH
WHERE ABS(HASH(source_id_left || '|' || source_id_right)) % 100 >= 70;
```

Verify split proportions:

```sql
SELECT
    'train' AS split, COUNT(*) AS pairs FROM <DATASET>_GT_TRAIN
UNION ALL
SELECT
    'test' AS split, COUNT(*) AS pairs FROM <DATASET>_GT_TEST;
```

**Split purpose:**
- `GT_TRAIN` — Used ONLY by contrastive embeddings (Phase 4c) as supervised training signal
- `GT_TEST` — Used by ALL techniques for evaluation (precision/recall/F1)

Non-contrastive techniques (Fuzzy, AI-Judged) are unsupervised and never see any ground truth during execution.

---

## 4. Technique-Dataset Matrix

Every applicable technique is benchmarked on every dataset.

| Technique | Phase | DBLP-ACM | DBLP-Scholar | Walmart-Amazon | Amazon-Google | Abt-Buy |
|-----------|-------|----------|--------------|---------------|--------------|---------|
| Deterministic (Tier 1) | 4 | N/A | N/A | N/A | N/A | N/A |
| Fuzzy Embedding (Tier 2) | 4 | yes | yes | yes | yes | yes |
| AI-Judged (Tier 3) | 4 | yes | yes | yes | yes | yes |
| Contrastive Embeddings | 4c | yes | yes | yes | yes | yes |
| Full Pipeline (Tier 2 → 3) | 4 | yes | yes | yes | yes | yes |
| Agentic (Tier 1→1.5→2) | 4b | N/A | N/A | N/A | N/A | N/A |

**Why N/A for Deterministic:** These academic datasets contain no authoritative identifiers (NPI, DUNS, GTIN, etc.). All matching is attribute-based.

**Why N/A for Agentic:** Phase 4b requires a reference corpus + Cortex Search Service. These benchmarks are symmetric cross-source matching tasks, not entity linking against a reference.

### Contrastive Model Selection

| Dataset | Language | Recommended Model | NER Mode | Rationale |
|---------|----------|-------------------|----------|-----------|
| DBLP-ACM | English | `roberta-base` | `none` | English bibliographic; best known F1=0.9904 |
| DBLP-Scholar | English | `roberta-base` | `none` | Same domain as DBLP-ACM |
| Walmart-Amazon | English | `roberta-base` | `none` | English product data; NER is neutral (see contrastive benchmarks) |
| Amazon-Google | English | `roberta-base` | `none` | English product data |
| Abt-Buy | English | `roberta-base` | `none` | English e-commerce |

All datasets are English-only, so `roberta-base` without NER is the recommended encoder per the model selection logic in `references/templates/contrastive-embeddings.md`.

---

## 5. Per-Benchmark Test Protocols

### 5.1 CoCo Prompt Templates

Each benchmark test invokes the skill via `cortex -p` with a structured prompt. The prompt bypasses Phase 0 discovery by providing all answers inline.

#### Single-Technique Prompt (Fuzzy Only)

```
I need to run entity resolution on the table {db}.{schema}.DBLP_ACM_SOURCE.
This is a bibliographic dataset with columns: source_id, source_table, raw_name,
raw_authors, raw_venue, raw_year. No authoritative identifiers.
Use the generic domain profile.

IMPORTANT: Use ONLY Tier 2 fuzzy matching (AI_EMBED + Jaro-Winkler similarity).
Do NOT use Tier 3 AI-judged classification. Do NOT use contrastive embeddings.

Write all output tables (normalized_entities, candidate_pairs, match_results,
entity_groups) to the schema {db}.{schema}.

Use table name prefix DBLP_ACM_FUZZY_ for all output tables.
```

#### Single-Technique Prompt (AI-Judged Only)

```
I need to run entity resolution on the table {db}.{schema}.DBLP_ACM_SOURCE.
This is a bibliographic dataset with columns: source_id, source_table, raw_name,
raw_authors, raw_venue, raw_year. No authoritative identifiers.
Use the generic domain profile.

Run the full Tier 2 + Tier 3 matching pipeline:
- Tier 2: AI_EMBED + Jaro-Winkler for initial scoring
- Tier 3: AI_CLASSIFY on all probable_match results from Tier 2

Write all output tables to {db}.{schema} with prefix DBLP_ACM_AIJUDGED_.
```

#### Single-Technique Prompt (Contrastive Embeddings)

```
I need to run entity resolution on the table {db}.{schema}.DBLP_ACM_SOURCE.
This is a bibliographic dataset with columns: source_id, source_table, raw_name,
raw_authors, raw_venue, raw_year. No authoritative identifiers.
Use the generic domain profile.

Use contrastive embeddings as the SOLE matching approach (Phase 4c standalone mode).
Ground truth training pairs are in {db}.{schema}.DBLP_ACM_GT_TRAIN (columns:
source_id_left, source_id_right).
Use roberta-base encoder with NER disabled.
GPU compute pool: <COMPUTE_POOL_NAME>

Write all output tables to {db}.{schema} with prefix DBLP_ACM_CONTRASTIVE_.
```

#### Full Pipeline Prompt

```
I need to run entity resolution on the table {db}.{schema}.DBLP_ACM_SOURCE.
This is a bibliographic dataset with columns: source_id, source_table, raw_name,
raw_authors, raw_venue, raw_year. No authoritative identifiers.
Use the generic domain profile.

Run the FULL matching pipeline:
- Tier 2: Fuzzy (AI_EMBED + Jaro-Winkler)
- Tier 3: AI-judged on probable_match results

Write all output tables to {db}.{schema} with prefix DBLP_ACM_FULL_.
```

### 5.2 Dataset-Specific Column Mappings

The prompt must map each dataset's columns to the skill's expected format. The skill's normalization phase adapts to whatever columns are present.

| Dataset | raw_name maps to | Additional columns |
|---------|-----------------|-------------------|
| DBLP-ACM | `title` | `raw_authors`, `raw_venue`, `raw_year` |
| DBLP-Scholar | `title` | `raw_authors`, `raw_venue`, `raw_year` |
| Walmart-Amazon | `title` | `raw_category`, `raw_brand`, `raw_modelno`, `raw_price` |
| Amazon-Google | `title` | `raw_description`, `raw_manufacturer`, `raw_price` |
| Abt-Buy | `name` | `raw_description`, `raw_price` |

### 5.3 Expected Output Tables

Each technique run produces a set of output tables with a technique-specific prefix:

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `<PREFIX>NORMALIZED_ENTITIES` | Standardized entity fields | `SOURCE_ID`, `NORMALIZED_NAME`, ... |
| `<PREFIX>CANDIDATE_PAIRS` | Blocked candidate pairs | `ID_LEFT`, `ID_RIGHT` |
| `<PREFIX>MATCH_RESULTS` | Final match decisions | `ID_LEFT`, `ID_RIGHT`, `DECISION`, `CONFIDENCE`, `MATCH_METHOD` |
| `<PREFIX>ENTITY_GROUPS` | Transitive closure groups | `ENTITY_ID`, `ENTITY_GROUP_ID` |

Contrastive runs additionally produce:

| Table | Purpose |
|-------|---------|
| `<PREFIX>CONTRASTIVE_EMBEDDINGS` | Trained embeddings per entity |
| `<PREFIX>BLOCKING_CANDIDATES` | Cosine similarity pairs |
| `<PREFIX>THRESHOLD_RESULTS` | Threshold sweep with P/R/F1 |
| `<PREFIX>PREDICTED_MATCHES` | Matches at optimal threshold |

---

## 6. Evaluation Framework

### 6.1 Core Evaluation Query

Compute precision, recall, and F1 for any technique's match results against the held-out test set:

```sql
WITH predicted_matches AS (
    -- Normalize pair ordering for consistent join
    SELECT
        LEAST(ID_LEFT, ID_RIGHT) AS id_a,
        GREATEST(ID_LEFT, ID_RIGHT) AS id_b
    FROM <PREFIX>MATCH_RESULTS
    WHERE DECISION = 'match'
),
ground_truth AS (
    SELECT
        LEAST(SOURCE_ID_LEFT, SOURCE_ID_RIGHT) AS id_a,
        GREATEST(SOURCE_ID_LEFT, SOURCE_ID_RIGHT) AS id_b
    FROM <DATASET>_GT_TEST
),
metrics AS (
    SELECT
        (SELECT COUNT(*) FROM predicted_matches p
         INNER JOIN ground_truth g ON p.id_a = g.id_a AND p.id_b = g.id_b) AS tp,
        (SELECT COUNT(*) FROM predicted_matches p
         LEFT JOIN ground_truth g ON p.id_a = g.id_a AND p.id_b = g.id_b
         WHERE g.id_a IS NULL) AS fp,
        (SELECT COUNT(*) FROM ground_truth g
         LEFT JOIN predicted_matches p ON g.id_a = p.id_a AND g.id_b = p.id_b
         WHERE p.id_a IS NULL) AS fn
)
SELECT
    tp, fp, fn,
    ROUND(tp / NULLIF(tp + fp, 0), 4) AS precision,
    ROUND(tp / NULLIF(tp + fn, 0), 4) AS recall,
    ROUND(2.0 * (tp / NULLIF(tp + fp, 0)) * (tp / NULLIF(tp + fn, 0))
        / NULLIF((tp / NULLIF(tp + fp, 0)) + (tp / NULLIF(tp + fn, 0)), 0), 4) AS f1
FROM metrics;
```

### 6.2 Per-Technique Evaluation

For the full pipeline, also break down by match method:

```sql
SELECT
    MATCH_METHOD,
    COUNT(*) AS total_decisions,
    SUM(CASE WHEN DECISION = 'match' THEN 1 ELSE 0 END) AS matches,
    SUM(CASE WHEN DECISION = 'probable_match' THEN 1 ELSE 0 END) AS probable,
    SUM(CASE WHEN DECISION = 'no_match' THEN 1 ELSE 0 END) AS no_match
FROM <PREFIX>MATCH_RESULTS
GROUP BY MATCH_METHOD
ORDER BY MATCH_METHOD;
```

### 6.3 Contrastive Threshold Sweep Evaluation

For contrastive embeddings, evaluate across the full threshold sweep against the test set (not the training set):

```sql
-- Adapt the threshold sweep from contrastive-embeddings.md Section 5
-- Replace ground_truth with <DATASET>_GT_TEST
CREATE OR REPLACE TABLE <PREFIX>THRESHOLD_EVAL AS
WITH thresholds AS (
    SELECT column1 AS threshold FROM VALUES
        (0.50),(0.55),(0.60),(0.65),(0.70),(0.72),(0.74),(0.76),
        (0.78),(0.80),(0.82),(0.84),(0.86),(0.88),(0.90),(0.92),(0.94),(0.96)
),
test_gt AS (
    SELECT
        LEAST(SOURCE_ID_LEFT, SOURCE_ID_RIGHT) AS id_a,
        GREATEST(SOURCE_ID_LEFT, SOURCE_ID_RIGHT) AS id_b
    FROM <DATASET>_GT_TEST
),
predictions AS (
    SELECT
        t.threshold,
        LEAST(bc.ID_LEFT, bc.ID_RIGHT) AS id_a,
        GREATEST(bc.ID_LEFT, bc.ID_RIGHT) AS id_b
    FROM thresholds t
    CROSS JOIN <PREFIX>BLOCKING_CANDIDATES bc
    WHERE bc.COSINE_SIM >= t.threshold
),
metrics AS (
    SELECT
        p.threshold,
        COUNT(DISTINCT CASE WHEN g.id_a IS NOT NULL
              THEN p.id_a || '|' || p.id_b END) AS tp,
        COUNT(DISTINCT CASE WHEN g.id_a IS NULL
              THEN p.id_a || '|' || p.id_b END) AS fp,
        (SELECT COUNT(*) FROM test_gt)
            - COUNT(DISTINCT CASE WHEN g.id_a IS NOT NULL
              THEN p.id_a || '|' || p.id_b END) AS fn
    FROM predictions p
    LEFT JOIN test_gt g ON p.id_a = g.id_a AND p.id_b = g.id_b
    GROUP BY p.threshold
)
SELECT
    threshold, tp, fp, fn,
    ROUND(tp / NULLIF(tp + fp, 0), 4) AS precision,
    ROUND(tp / NULLIF(tp + fn, 0), 4) AS recall,
    ROUND(2.0 * (tp / NULLIF(tp + fp, 0)) * (tp / NULLIF(tp + fn, 0))
        / NULLIF((tp / NULLIF(tp + fp, 0)) + (tp / NULLIF(tp + fn, 0)), 0), 4) AS f1
FROM metrics
ORDER BY threshold;
```

### 6.4 Cross-Technique Comparison Report

After running all techniques on a dataset, produce a comparison table:

```sql
-- Collect results from each technique's evaluation into a summary
SELECT 'Fuzzy (Tier 2)' AS technique, precision, recall, f1
FROM <DATASET>_FUZZY_EVAL
UNION ALL
SELECT 'AI-Judged (Tier 2+3)', precision, recall, f1
FROM <DATASET>_AIJUDGED_EVAL
UNION ALL
SELECT 'Contrastive', precision, recall, f1
FROM <DATASET>_CONTRASTIVE_EVAL
UNION ALL
SELECT 'Full Pipeline', precision, recall, f1
FROM <DATASET>_FULL_EVAL
ORDER BY f1 DESC;
```

---

## 7. Expected Results & Baselines

### 7.1 Known Contrastive Baselines

From the existing 10-experiment ablation study (`skill_evidence.yaml`):

| Dataset | Model | NER | F1 | Precision | Recall |
|---------|-------|-----|-----|-----------|--------|
| DBLP-ACM | RoBERTa-base | none | **0.9904** | 0.982 | 0.999 |
| DBLP-ACM | XLM-RoBERTa-base | ditto-general | 0.9818 | 0.970 | 0.994 |
| DBLP-ACM | MiniLM-L6-v2 | none | 0.9797 | 0.970 | 0.990 |
| Walmart-Amazon | RoBERTa-base | none | **0.8701** | 0.795 | 0.961 |
| Walmart-Amazon | XLM-RoBERTa-base | ditto-product | 0.8556 | 0.830 | 0.883 |
| Walmart-Amazon | MiniLM-L6-v2 | none | 0.7706 | 0.692 | 0.752 |

### 7.2 Expected Ranges for Other Techniques

These are expected ranges based on the characteristics of each technique and dataset. Actual results will vary.

| Dataset | Fuzzy (Tier 2) | AI-Judged (Tier 2+3) | Contrastive | Full Pipeline |
|---------|---------------|---------------------|------------|---------------|
| DBLP-ACM | F1 0.85-0.95 | F1 0.90-0.97 | F1 0.97-0.99 | F1 0.90-0.97 |
| DBLP-Scholar | F1 0.70-0.85 | F1 0.80-0.90 | F1 0.90-0.97 | F1 0.80-0.92 |
| Walmart-Amazon | F1 0.50-0.70 | F1 0.65-0.80 | F1 0.85-0.90 | F1 0.65-0.82 |
| Amazon-Google | F1 0.45-0.65 | F1 0.60-0.75 | F1 0.80-0.88 | F1 0.60-0.78 |
| Abt-Buy | F1 0.55-0.70 | F1 0.65-0.80 | F1 0.82-0.90 | F1 0.65-0.82 |

**Key expectations:**
1. Contrastive embeddings should outperform all other techniques on every dataset (supervised advantage)
2. AI-Judged should beat Fuzzy-only (LLM reasoning resolves ambiguous pairs)
3. Full Pipeline should approximate AI-Judged (Tier 3 upgrades probable_match results)
4. The gap between Contrastive and others should be largest on hard/noisy datasets (Walmart-Amazon, Amazon-Google)

### 7.3 Minimum Quality Gates

A benchmark run **passes** if:

| Dataset | Technique | Minimum F1 |
|---------|-----------|-----------|
| DBLP-ACM | Fuzzy | 0.80 |
| DBLP-ACM | Contrastive | 0.95 |
| DBLP-ACM | Full Pipeline | 0.85 |
| Walmart-Amazon | Fuzzy | 0.45 |
| Walmart-Amazon | Contrastive | 0.80 |
| Walmart-Amazon | Full Pipeline | 0.60 |

Other dataset/technique combinations: F1 > 0.40 (sanity floor). These gates are intentionally conservative — they catch regressions, not aspirational targets.

---

## 8. Test Execution Guide

### 8.1 Prerequisites

| Requirement | Needed For | Notes |
|-------------|-----------|-------|
| Snowflake account + warehouse | All | MEDIUM warehouse recommended |
| Cortex Code CLI (`cortex`) | Phase 2 (invoke) | Latest version |
| GPU compute pool (`GPU_NV_S`) | Contrastive only | NVIDIA T4 sufficient |
| External access integrations | Contrastive only | `PYPI_EAI`, `HF_EAI` |
| Benchmark CSV files staged | Phase 1 (setup) | See Section 3 |

### 8.2 Environment Variables

```bash
export SNOWFLAKE_ACCOUNT=...
export SNOWFLAKE_USER=...
export SNOWFLAKE_PASSWORD=...
export SNOWFLAKE_WAREHOUSE=COMPUTE_WH
export SNOWFLAKE_DATABASE=ENTITY_RESOLUTION_TEST
export SNOWFLAKE_ROLE=...
export CORTEX_CONNECTION=demoaccount
# For contrastive benchmarks:
export ER_COMPUTE_POOL=GPU_NV_S_POOL
```

### 8.3 Running Benchmarks

#### Pytest Markers

Benchmarks use dedicated markers that are separate from the functional test markers:

```
benchmark          — All benchmark tests
bench_dblp_acm     — DBLP-ACM dataset
bench_dblp_scholar — DBLP-Scholar dataset
bench_walmart_amazon — Walmart-Amazon dataset
bench_amazon_google — Amazon-Google dataset
bench_abt_buy      — Abt-Buy dataset
```

#### Makefile Targets

```bash
# Phase 1: Load benchmark data into Snowflake
make bench-setup-dblp-acm
make bench-setup-all

# Phase 2: Invoke skill (one technique at a time)
make bench-invoke-dblp-acm-fuzzy
make bench-invoke-dblp-acm-contrastive
make bench-invoke-dblp-acm-full
make bench-invoke-dblp-acm-all       # All techniques for DBLP-ACM

# Phase 3: Evaluate results
make bench-validate-dblp-acm
make bench-validate-all

# Full run (all 3 phases, all datasets, all techniques)
make bench-all
```

#### Running a Single Dataset End-to-End

```bash
# Setup → Invoke (all techniques) → Validate
make bench-dblp-acm
```

#### Running All Benchmarks

```bash
make bench-all
```

**Estimated wall-clock time:**

| Component | Time per Dataset | Notes |
|-----------|-----------------|-------|
| Setup (data load) | 1-5 min | Depends on dataset size |
| Fuzzy invoke | 5-15 min | Embedding generation is the bottleneck |
| AI-Judged invoke | 10-30 min | LLM calls on probable matches |
| Contrastive invoke | 15-45 min | GPU training + embedding + sweep |
| Full Pipeline invoke | 10-30 min | Tier 2 + Tier 3 cascade |
| Validation | 1-2 min | SQL evaluation queries only |

Total for all 5 datasets × 4 techniques: approximately 3-8 hours.

### 8.4 Test File Structure

```
src/tests/
├── conftest.py                          # Shared fixtures (existing)
├── fixtures/
│   ├── benchmark_dblp_acm.sql           # Schema + data load for DBLP-ACM
│   ├── benchmark_dblp_scholar.sql       # Schema + data load for DBLP-Scholar
│   ├── benchmark_walmart_amazon.sql     # Schema + data load for Walmart-Amazon
│   ├── benchmark_amazon_google.sql      # Schema + data load for Amazon-Google
│   └── benchmark_abt_buy.sql           # Schema + data load for Abt-Buy
├── phase1_setup/
│   ├── test_setup_bench_dblp_acm.py
│   ├── test_setup_bench_dblp_scholar.py
│   ├── test_setup_bench_walmart_amazon.py
│   ├── test_setup_bench_amazon_google.py
│   └── test_setup_bench_abt_buy.py
├── phase2_invoke/
│   ├── test_invoke_bench_dblp_acm.py    # Invokes all 4 techniques
│   ├── test_invoke_bench_dblp_scholar.py
│   ├── test_invoke_bench_walmart_amazon.py
│   ├── test_invoke_bench_amazon_google.py
│   └── test_invoke_bench_abt_buy.py
└── phase3_validate/
    ├── test_validate_bench_dblp_acm.py  # Evaluates all techniques
    ├── test_validate_bench_dblp_scholar.py
    ├── test_validate_bench_walmart_amazon.py
    ├── test_validate_bench_amazon_google.py
    └── test_validate_bench_abt_buy.py
```

---

## 9. Updating skill_evidence.yaml

After running benchmarks, update `skill_evidence.yaml` with new evidence entries:

```yaml
evidence_links:
  # ... existing contrastive experiment entries ...

  # Benchmark integration tests
  - description: 'Benchmark: DBLP-ACM Full Pipeline (F1=X.XXXX)'
    type: benchmark
    schema: ER_BENCHMARK.BENCH_DBLP_ACM
  - description: 'Benchmark: DBLP-ACM Fuzzy-only (F1=X.XXXX)'
    type: benchmark
    schema: ER_BENCHMARK.BENCH_DBLP_ACM_FUZZY
  - description: 'Benchmark: Walmart-Amazon Full Pipeline (F1=X.XXXX)'
    type: benchmark
    schema: ER_BENCHMARK.BENCH_WALMART_AMAZON
  # ... etc for each dataset × technique ...
```

Each benchmark run should also produce a comparison view:

```sql
CREATE OR REPLACE VIEW BENCHMARK_COMPARISON AS
SELECT
    '<DATASET>' AS dataset,
    '<TECHNIQUE>' AS technique,
    precision, recall, f1,
    CURRENT_TIMESTAMP() AS run_timestamp
FROM <DATASET>_<TECHNIQUE>_EVAL
-- UNION ALL for each dataset × technique combination
ORDER BY dataset, f1 DESC;
```

This view provides a single-query summary of all benchmark results for evidence and reporting.
