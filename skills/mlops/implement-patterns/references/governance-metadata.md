# Governance & Metadata

> **Environment naming**: This file uses canonical names **DEV**, **STAGING**, **PROD**. Substitute the user's preferred names in all outputs. For 2-environment setups, omit STAGING references. For environment isolation strategies and naming conventions, see `ci-cd-testing.md`.

## Metadata Management

### What to Track

#### Per Training Run
- Pipeline and component versions executed
- Start/end time and duration per step
- Who/what triggered the run
- Parameter arguments passed
- Pointers to intermediate artifacts (prepared data, validation results, statistics)
- Input data snapshot or reference (which tables, which date range)

#### Per Model Version
- Training run ID (links to all run metadata above)
- Evaluation metrics (train set + test set)
- Hyperparameters used
- Code commit hash
- Data lineage (which features, which tables)
- Validation status and results
- Tags (model_validation_status, deployment_status, etc.)

#### Per Deployment
- Which model version is serving
- Deployment timestamp
- Environment (staging/prod)
- Endpoint configuration (resources, replicas)
- Traffic split (if A/B testing)

### Patterns by Maturity Level

#### L1 - Manual
- Experiments used to manually log params and metrics per training run
- Datasets used for manual data snapshots
- Model Registry used for manual model registration
- Key metrics recorded but not centralized or searchable

#### L2 - Semi-automated
- Experiment tracker logs params, metrics, artifacts per run
- Model registry stores version metadata
- Tags and annotations on model versions
- Searchable experiment history

#### L3 - Fully Automated
- Full pipeline metadata store:
  - Every pipeline execution recorded with component versions
  - Artifact lineage auto-tracked (which data produced which model)
  - Auto-linked: trigger -> run -> model version -> deployment
- Queryable metadata API for auditing and debugging
- Automated metadata quality checks (no model registered without required tags)

## Lineage

### Data Lineage
- Track which source tables/features went into each model version
- Enable impact analysis: "if this table changes, which models are affected?"

### Model Lineage
- Track which code, data, and parameters produced each model version
- Enable reproducibility: "recreate this exact model version"

### Pipeline Lineage
- Track which pipeline version ran, when, with what configuration
- Enable debugging: "what was different about last Tuesday's run?"

### Patterns by Maturity Level

#### L1 - Manual
- Data scientist documents data sources in notebook or Experiments
- Manual code snapshot (version noted in experiment metadata)

#### L2 - Semi-automated
- Experiment tracker auto-captures code snapshot, data inputs, parameters
- Model registry links model version to training run
- Pipeline orchestrator logs DAG execution history

#### L3 - Fully Automated
- End-to-end lineage graph: data source -> features -> training run -> model -> deployment
- Automated impact analysis queries
- Lineage-aware alerting (upstream data change notifies downstream model owners)

## RBAC / Security Model

MLOps does **not** require a separate RBAC design. The security model for MLOps objects (databases, schemas, tables, models, endpoints, pipelines) must be governed by and follow the **same standards as the customer's existing RBAC framework**.

Key principles:
- **Integrate, don't isolate** — MLOps roles, grants, and access policies should fit within the organization's existing role hierarchy, naming conventions, and governance processes.
- **Environment-scoped roles** — roles should be scoped per environment (e.g., `ML_DEV_ADMIN`, `ML_PROD_READONLY`) following the same environment isolation boundaries as the rest of the data platform.
- **CI/CD service roles** — service users used for automation should have dedicated roles with least-privilege grants, reviewed periodically. These roles follow the same RBAC standards as any other service account in the organization.
- **Do not create shadow governance** — avoid building a parallel permission system for ML objects. Use Snowflake's native RBAC (roles, grants, database roles) consistently.

For CI/CD-specific service user setup, see `ci-cd-testing.md` § "CI/CD Authentication."

## Compliance & Audit

### Patterns by Maturity Level

#### L1 - Manual
- Manual documentation of model purpose and behavior
- Ad-hoc compliance checks
- No formal audit trail

#### L2 - Semi-automated
- Tags on model versions (owner, purpose, data_sensitivity, approval_status)
- Automated approval gates in promotion workflow
- Model cards or documentation attached to registered models
- Access control on model registry (who can register, promote, deploy)

#### L3 - Fully Automated
- Full audit trail: every action on a model version logged (who, when, what)
- Automated compliance checks before promotion (required tags present, documentation complete)
- Policy enforcement (models without required metadata cannot be promoted)
- Regulatory reporting: automated generation of model risk documentation
- Data privacy compliance: track which PII features are used by which models

### Governance Checklist per Maturity Level

**L1 Minimum:**
- [ ] Model purpose documented
- [ ] Training data source identified
- [ ] Model owner assigned
- [ ] Basic performance metrics recorded

**L2 Standard:**
- [ ] All L1 items
- [ ] Model version tagged with required metadata
- [ ] Approval gate before production deployment
- [ ] Experiment history searchable
- [ ] Access control configured on registry

**L3 Comprehensive:**
- [ ] All L2 items
- [ ] End-to-end lineage tracked
- [ ] Automated compliance checks enforced
- [ ] Full audit trail queryable
- [ ] Retention and archival policies active
- [ ] Impact analysis available for upstream changes

## LLM/GenAI Governance Adaptation

### LLM-Specific Metadata to Track

#### Per Prompt Version
- Prompt template text and version (Git commit hash)
- System instructions, few-shot examples
- Target foundation model and parameters (temperature, max_tokens)
- Evaluation scores (LLM-as-judge metrics)

#### Per RAG Configuration
- Corpus source tables and date range
- Chunking strategy and parameters
- Embedding model used
- Retrieval quality metrics (precision@k, recall@k)

#### Per Fine-Tuned Model
- Base model used
- Training data snapshot (Dataset reference)
- Fine-tuning parameters (epochs, learning rate, etc.)
- Evaluation metrics (LLM-as-judge + task-specific)

### LLM Lineage
- **Prompt lineage**: Git history of prompt templates → evaluation results → deployment events
- **RAG lineage**: Source documents → Cortex Search index → retrieval + generation quality metrics
- **Fine-tuned model lineage**: Training data → fine-tuning run → model version → deployment (same as traditional ML, extends to Model Registry)

### LLM Access Control
- **Cortex AI RBAC**: Control which roles can access which foundation models (e.g., restrict expensive models to production use)
- **Prompt access**: Version-controlled prompts inherit Git repo access control
- **Search index access**: Cortex Search service access controlled via Snowflake RBAC (grants on the service)
- **Fine-tuned model access**: Model Registry RBAC (same as traditional ML)

### LLM Governance Checklist Additions

**L1 Minimum (add to existing checklist):**
- [ ] LLM development approach documented (API / prompt / RAG / fine-tuning)
- [ ] Foundation model selection documented with rationale

**L2 Standard (add to existing checklist):**
- [ ] Prompt templates versioned in Git
- [ ] LLM evaluation scores tracked per deployment
- [ ] Cortex AI RBAC configured (model access by role)
- [ ] RAG corpus source documented and refresh schedule defined

**L3 Comprehensive (add to existing checklist):**
- [ ] Automated LLM-as-judge evaluation on production traffic
- [ ] Human feedback loop integrated
- [ ] Token cost tracking and alerting configured
- [ ] Safety/guardrail tests in CI pipeline
- [ ] Full prompt + RAG + fine-tuning lineage tracked

## See Also

- `model-lifecycle.md` — Registry and versioning that governance tracks
- `monitoring-rollback.md` — Incident events that require audit trail
- `data-features.md` — Data lineage and feature provenance
