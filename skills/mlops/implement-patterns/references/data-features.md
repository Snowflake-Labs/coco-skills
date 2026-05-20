# Data & Features

> **Scope**: This file covers the *process* layer — when to adopt a Feature Store, what validation gates to enforce, how to prevent skew across environments. For the *technical implementation* (Feature Store API, Cortex Search setup code), use the `machine-learning` skill.

> **Environment naming**: This file uses canonical names **DEV**, **STAGING**, **PROD**. Substitute the user's preferred names in all outputs. For 2-environment setups, omit STAGING references. For environment isolation strategies and naming conventions, see `ci-cd-testing.md`.

## Data Validation

### Pre-Training Validation

#### L1 - Manual
- Data scientist manually inspects data in notebook
- Ad-hoc checks on row counts, null rates, distributions
- No automated gates

#### L2 - Semi-automated
- Automated schema validation before training pipeline runs:
  - All expected features present
  - No unexpected features
  - Data types match expectations
  - No schema version mismatch
- Pipeline halts on schema skew; team notified
- Basic statistical checks (null rate thresholds, row count minimums)

#### L3 - Fully Automated
- All L2 checks + statistical distribution validation:
  - Feature distributions compared to reference baseline (KS test, PSI)
  - Significant data value skew triggers retraining (not halt)
  - Anomalous records quarantined for review
- Auto-decision: schema skew -> halt pipeline; value skew -> trigger retraining
- Validation results logged to metadata store

### Data Schema Skews (anomalies that should halt pipeline)
- Unexpected features received
- Expected features missing
- Feature data type changed
- Feature value range outside expected bounds

### Data Value Skews (changes that should trigger retraining)
- Significant shift in feature distributions
- Change in class balance (classification)
- Change in target variable distribution (regression)
- New categorical values appearing

## Feature Store

At L1+, each environment should have its own Feature Store (separate databases), following the same per-environment isolation principle as Model Registry. A centralized Feature Store across environments is only acceptable at L0 for experimentation. This ensures that feature definitions, refresh schedules, and data access are isolated per environment.

### L1 - Manual (Feature Store Available)
- Per-environment Feature Store (each environment has its own database)
- Feature Store available for centralized definitions within the environment (manually maintained)
- Features can also be computed inline in training code
- Feature logic may be duplicated between training and serving
- Training-serving skew risk managed by manual review

### L2 - Semi-automated (Automated Feature Store)
- Centralized feature definitions and storage (upgrade from manual L1 setup)
- Automated incremental refresh from batch/streaming sources
- Offline serving for training (batch feature retrieval)
- Feature discovery: data scientists can find and reuse existing features
- Feature metadata tracked (owner, description, freshness)
- Training pipeline reads from feature store
- **Benefit**: Feature reuse, consistent definitions, reduced duplication

### L3 - Fully Automated (Feature Store Expected)
- Unified offline (training) and online (serving) feature serving
- Feature versioning and lineage tracking
- Automated feature freshness monitoring
- Point-in-time correct feature retrieval for training
- Low-latency online serving for real-time predictions
- **Benefit**: Eliminates training-serving skew, enables real-time features

### Feature Store Key Decisions
- **Offline vs online**: Do you need real-time feature serving or batch only?
- **Freshness**: How stale can features be before predictions degrade?
- **Compute**: Where are features computed? (batch pipeline, streaming, on-demand)
- **Storage**: Unified platform (Snowflake Feature Store) vs external online store (if latency requirements exceed platform capabilities)

## Training-Serving Skew Prevention

### What Causes Skew
- Different feature computation code in training vs serving
- Different data sources or preprocessing between environments
- Stale features in online store
- Time-of-prediction features computed differently than time-of-training

### Prevention Patterns

#### L1 - Manual
- Code review to verify feature logic matches between training and serving
- Manual testing with sample data through both paths

#### L2 - Semi-automated
- Shared transformation code between training and serving pipelines
- Or: feature store provides consistent feature values for both
- Automated tests comparing feature outputs from training and serving paths on same input

#### L3 - Fully Automated
- Feature store as single source of truth for both training and serving
- Automated skew detection: compare feature distributions at training time vs serving time
- Alerts on significant divergence
- Feature monitoring dashboard

## LLM/GenAI Data Adaptation

### Vector DB / Search Index as Feature Store Equivalent

For RAG workloads, Cortex Search plays the role that Feature Store plays for traditional ML:

| Aspect | Feature Store (Traditional ML) | Cortex Search (RAG/LLM) |
|---|---|---|
| Purpose | Consistent feature vectors for training + serving | Consistent document retrieval for generation |
| Consistency concern | Training-serving skew | Retrieval quality drift |
| Per-environment | Feature computations run per environment | Search index rebuilt per environment |
| Freshness | Feature refresh on schedule or data change | Index refresh on schedule or new documents |
| Monitoring | Feature distribution drift | Retrieval precision@k, recall@k |

### Data Validation for LLM Workloads

#### RAG Corpus Validation
- **Schema**: Document format, metadata fields present, no empty/corrupt documents
- **Quality**: Duplicate detection, stale content flagging, language validation
- **Coverage**: New topics or domains missing from corpus

#### Fine-Tuning Data Validation
- **Format**: Training examples match expected schema (instruction/response pairs, etc.)
- **Quality**: Label quality checks, deduplication, toxicity/bias screening
- **Volume**: Minimum training set size met; class balance acceptable

### Training-Serving Skew for LLM Workloads
- **RAG skew**: Development corpus differs significantly from production corpus → retrieval quality degrades in production
- **Prevention**: Per-environment Cortex Search indexes built from environment-specific data; retrieval quality tests in CI compare against known query-document pairs

## See Also

- `ci-cd-testing.md` — Data validation tests integrated into CI pipeline
- `continuous-training.md` — Data-availability triggers for retraining
- `monitoring-rollback.md` — Feature drift as a monitoring signal
