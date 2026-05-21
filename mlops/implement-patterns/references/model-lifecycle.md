# Model Lifecycle

> **Scope**: This file covers the *process* layer — when to register, how to version, what gates to pass, how to promote between environments. For the *technical implementation* (API calls, SDK code to log/deploy models), use the `machine-learning` skill.

> **Environment naming**: This file uses canonical names **DEV**, **STAGING**, **PROD**. Substitute the user's preferred names in all outputs. For 2-environment setups, omit STAGING references. For environment isolation strategies and naming conventions, see `ci-cd-testing.md`.

## Model Registry

### L1 - Manual
- Per-environment model registry (each environment has its own registry database)
- Models registered to Model Registry (manual registration)
- Manual naming convention for versions
- Aliases used for manual deployment (human updates alias)
- Metadata attached manually (params, metrics from Experiments)

### L2 - Semi-automated
- Models registered automatically by training pipeline
- Version numbers auto-incremented
- Metadata (params, metrics, data lineage) attached to each version
- Aliases used for stage management (Champion, Challenger)

### L3 - Fully Automated
- All L2 + automated lifecycle management
- Auto-archive old versions based on retention policy
- Cross-environment model visibility (same-account: models visible across databases; multi-account: via replication groups)
- Automated compliance checks on registration

## Versioning Strategy

### When to Create a New Version (under same registered model)
- Retrained on new data (same code)
- Hyperparameters tuned
- Minor code changes to training pipeline
- **Benefit**: Unified lineage, alias-based routing, zero pipeline changes for consumers

### When to Create a New Model Object
- Fundamentally different algorithm or architecture
- Different input features or target variable
- Different business problem
- **Benefit**: Clean separation, independent lifecycle

### Version Naming and Lifecycle Management

A common pattern for managing model versions within an environment:

**Concept: active / candidate versioning**
- Only **one active version** exists at a time — this is the version serving predictions (set as default version). Default name suggestion: `LIVE` (alternatives: `PROD`, `CHAMPION`, or any name the team agrees on).
- New model versions start as **candidates** until they pass validation gates. Default name suggestion: `CANDIDATE_<timestamp>` (alternatives: `CANARY_<ts>`, `CHALLENGER_<ts>`, etc.).
- Multiple candidates can accumulate for comparison and audit.
- The version naming convention should be agreed upon with the customer — do not enforce specific names.

**Metric-gated promotion logic** (within an environment):
1. Train new model, evaluate against a defined metric threshold (e.g., accuracy >= 0.6)
2. If metric **below threshold** → register as candidate, do not promote
3. If metric **meets threshold** and no current active version → register as active
4. If metric **meets threshold** and **beats current active** → archive current active to candidate, register new version as active
5. If metric **meets threshold** but **does not beat current active** → register as candidate

**Archive-before-replace pattern**: Before overwriting the active version, copy it to a timestamped candidate name (`ALTER MODEL <model> ADD VERSION <candidate_ts> FROM MODEL <model> VERSION <active>`), then drop the old active. This preserves rollback capability — the previous best model is always available.

**Setting the default version**: After promoting a new active version, set it as the default (`ALTER MODEL <model> SET DEFAULT_VERSION = <active_version_name>`). Serving endpoints and prediction scripts that reference the model without specifying a version will pick up the new default.

**Important considerations:**
- Version names in Snowflake Model Registry are case-sensitive and stored in uppercase — always use uppercase in `log_model(version_name=...)`, `model.version(...)`, and SQL commands
- The metric threshold and improvement delta should be agreed upon with the customer (e.g., minimum improvement of N%, statistical significance, multi-metric gating)
- This pattern works within a single environment; for cross-environment promotion, see `promotion-patterns.md` § "Promotion Mechanisms and Snowflake Features"

### Prediction Pipeline Pattern

After a model is registered and promoted to LIVE, predictions follow a consistent pattern:

1. **Connect to Feature Store** — use the same FeatureView used during training to ensure feature consistency
2. **Read feature data** — retrieve features for the scoring population (consider point-in-time spines for production scoring of only new/recent records)
3. **Load model from registry** — load the active version (or default version) from the current environment's registry
4. **Run predictions** — score using the loaded model
5. **Persist results** (when applicable) — write predictions to a table with metadata columns: model version used, prediction timestamp, and any relevant identifiers. Persisting is recommended for batch scoring, audit trails, and downstream consumption, but may not apply to all use cases (e.g., real-time inference serving results directly to an application). The decision depends on the purpose of inference and business needs.

This pattern ensures that the same features and transformations used in training are applied during inference (prevents training-serving skew). See `data-features.md` § "Training-Serving Skew Prevention" for the full skew prevention framework.

## Champion/Challenger Workflow

### L1 - Manual
- Data scientist trains new model, compares metrics in Experiments
- Manual A/B testing via Model Serving (human deploys Challenger alongside Champion)
- Manual decision to promote or reject based on comparison
- Manual alias update to switch Champion to new version if approved

### L2 - Semi-automated
- Automated offline comparison:
  1. New model registered as version N with "Challenger" alias
  2. Validation pipeline loads both Champion and Challenger
  3. Both evaluated on held-out test set
  4. Metrics compared automatically
  5. Results presented to human for approval
  6. On approval, "Champion" alias moved to new version
- Online A/B testing (when applicable):
  1. Challenger deployed alongside Champion (traffic split or shadow mode)
  2. Online metrics collected for both (A/B test framework)
  3. Results presented to human for promotion decision
  4. Gradual traffic ramp-up for Challenger (canary)

### L3 - Fully Automated
- All L2 capabilities + automated decision-making:
  1. Statistical significance test determines winner automatically
  2. Auto-promote if Challenger wins; auto-reject if not
  3. Auto-rollback if Challenger degrades during ramp
  4. No human gate required (humans notified, not blocking)

### Key Decisions
- **Offline vs online comparison**: Offline is faster/cheaper; online captures real-world behavior
- **Metric selection**: Which metrics determine the winner? (accuracy, latency, business KPI)
- **Statistical significance**: How long to run A/B test? What confidence level?
- **Fallback**: If no Champion exists, compare against business heuristic or baseline threshold

## Promotion Gates

### Pre-Registration Gates
- Training pipeline completed successfully
- Evaluation metrics logged
- No NaN/infinity values in predictions

### Pre-Promotion Gates (Challenger -> Champion)
- Model validation checks pass (format, metadata, compliance)
- Performance on test set meets minimum threshold
- Performance consistent across data segments/slices
- Infrastructure compatibility verified
- (L2) Online A/B test results reviewed by human
- (L3) Online A/B test auto-evaluated; auto-promote if thresholds pass

### Post-Promotion Gates
- Serving endpoint healthy after deployment
- Prediction latency within SLA
- No error rate spike
- Monitoring pipeline active and collecting data

## LLM/GenAI Lifecycle Adaptation

### Versioning by Artifact Type

| Artifact | Where to Version | Strategy |
|---|---|---|
| **Prompt templates** | Git | Semantic versioning or commit-based. |
| **Fine-tuned model weights** | Model Registry | Same as traditional ML — register, version, alias. |
| **RAG index configuration** | Git (config) + Cortex Search (index) | Config versioned in Git. Index rebuilt per environment. |
| **Agent definitions** | Git | Versioned as code. |

Foundation model API calls require no versioning or lifecycle management — see `mlops-pattern-framework.md` § "What Gets Promoted."

### Champion/Challenger for LLMs

#### Prompt Versions
- **Offline**: Run both prompt versions against an evaluation dataset using LLM-as-judge (AI Observability) — compare groundedness, relevance, accuracy scores
- **Online**: A/B test prompt versions on live traffic; measure user satisfaction, task completion rate, safety metrics
- **Decision**: Automated if quality metrics improve; human review if metrics are mixed

#### Fine-Tuned Models
- Same Champion/Challenger workflow as traditional ML (Model Registry aliases)
- Evaluation includes LLM-specific metrics: fluency, factual accuracy, instruction following

#### RAG Configurations
- Compare retrieval quality (precision@k, recall@k) between index versions or chunking strategies
- End-to-end evaluation: does the full RAG pipeline (retrieval + generation) produce better answers?

## See Also

- `promotion-patterns.md` — How model promotion fits into Code/Model/Hybrid workflows
- `monitoring-rollback.md` — Post-deployment monitoring and rollback mechanisms
- `governance-metadata.md` — Metadata and lineage tracking per model version
