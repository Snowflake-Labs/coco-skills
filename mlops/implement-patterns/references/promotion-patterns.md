# Promotion Patterns

> **Environment naming**: This file uses canonical names **DEV**, **STAGING**, **PROD**. Substitute the user's preferred names in all outputs. For **2-environment setups** (DEV → PROD only), see the 2-env adaptation notes in each pattern section. For environment isolation strategies (same-account vs multi-account), naming conventions, and data architecture layers, see `ci-cd-testing.md`. The term **"catalog"** in this file refers to the set of objects belonging to an environment — this may be a database (same-account isolation) or an entire account (multi-account isolation), depending on the chosen strategy.

## Code Promotion

### Overview
Training code moves from dev -> staging -> prod. The model is retrained in each environment. The production model is trained on production data in the production environment.

### L1 - Manual
- External Git repo with dev branch for experimentation (Snowflake Git Integration is L2)
- Data scientist develops training code in notebooks/scripts
- Manual code review before merging to main
- Training code executed manually in each environment (ML Jobs, Distributed Training available)
- Model registered manually to Model Registry per environment
- Model Serving (SPCS) available for deployment (manual setup)
- Supporting code (inference, monitoring) deployed with training code
- **Environment structure**: Separate catalogs per environment (dev/staging/prod), RBAC for basic isolation
- **Data access**: Production data accessible from prod environment

### L2 - Semi-automated
- PR-based workflow with branch policies
- CI runs unit + integration tests on PR
- Training pipeline deployed as automated job in staging (on data subset)
- Integration tests validate full pipeline end-to-end in staging
- On merge to release branch, CD deploys pipeline to production
- Production pipeline trains model on full production data
- Model registered automatically; human approves Champion alias switch to new version
- **Environment structure**: Separate catalogs + workspaces, CI/CD orchestrates transitions
- **Data access**: Each environment accesses its own catalog; prod pipeline accesses prod data

### L3 - Fully Automated
- Trunk-based development with feature flags
- CI/CD auto-deploys pipeline to production on release
- Production training triggered by schedule, new data, or drift detection
- Automated validation promotes model without human intervention
- Champion/Challenger auto-promotion (offline + online A/B with automated decision)
- Zero-downtime deployment of new model versions
- Automated rollback on performance degradation
- **Environment structure**: Identical containerized pipelines, config-driven per environment
- **Data access**: Production pipeline auto-accesses prod data; resource isolation between training and serving

### 2-Environment Adaptation (Code Promotion)
When no STAGING exists, DEV absorbs staging responsibilities:
- **L1**: Code is reviewed and tested in DEV, then deployed directly to PROD. Manual validation happens in DEV before promotion.
- **L2**: CI runs all tests in DEV (including integration tests that would normally run in staging). CD deploys directly to PROD with a human approval gate.
- **L3**: Full CI/CD validates in DEV; auto-deploys to PROD. DEV must have production-like config for meaningful validation.
- **Model Registry**: Each environment must have its own registry (separate databases). Promotion uses `CREATE MODEL ... FROM MODEL` via CI/CD.

---

## Model Promotion

### Overview
Model artifact is trained in development and promoted to staging -> prod. Only the artifact moves, not the training code.

### L1 - Manual
- Data scientist trains model in dev environment (ML Jobs, Distributed Training, HPO available)
- Model registered to Model Registry in dev
- Artifact manually copied/promoted to staging catalog for validation
- Manual validation (checklist-based, Experiments for comparison)
- Artifact manually promoted to prod catalog
- Model Serving (SPCS) available for deployment (manual setup)
- Supporting code (inference, monitoring) deployed separately
- **Environment structure**: Single workspace or loosely coupled environments
- **Data access**: Dev environment needs access to representative data

### L2 - Semi-automated
- Training pipeline runs in dev (may be scheduled)
- Model registered automatically to dev catalog
- Automated validation pipeline runs in staging on the artifact
- Human approval gate before promotion to prod
- Supporting code has its own CI/CD pipeline (deployed separately)
- **Environment structure**: Separate catalogs; staging used for artifact validation only
- **Data access**: Dev trains on dev-accessible data; validation uses staging/prod data subsets

### L3 - Fully Automated
- Automated retraining in dev on schedule or trigger
- Automated validation + promotion pipeline
- Auto-promote if thresholds pass; auto-reject with notification if not
- Supporting code pipelines also fully automated
- **Risk**: Dev-trained artifacts may not reflect production data distribution. Validation gates must be robust.
- **Environment structure**: Dev pipeline auto-registers; promotion pipeline auto-moves artifact
- **Data access**: Dev must have representative data; validation must cover prod data characteristics

### 2-Environment Adaptation (Model Promotion)
Model Promotion is the **most natural fit** for 2-environment setups **when Code Promotion is not possible** (i.e., production data is not accessible from the production environment), since the artifact already originates in DEV. Confirm this choice with the customer:
- **L1**: Model trained in DEV, validated in DEV, manually promoted to PROD. No staging needed.
- **L2**: Automated validation pipeline runs in DEV on the artifact. Human approval gate before PROD promotion. Validation must be stricter since there is no separate environment to catch issues.
- **L3**: Automated retraining + validation in DEV, auto-promotion to PROD. Risk mitigation: ensure DEV data is representative and validation gates are robust.
- **Model Registry**: Each environment should have its own registry (separate databases) at L1+. Promotion uses `CREATE MODEL ... FROM MODEL` via CI/CD. See the "Promotion Mechanisms and Snowflake Features" section below for details on registry organization and cross-environment promotion.

---

## Hybrid Promotion

### Overview
Training code moves to staging (like Code Promotion). Model is trained in staging with production data access. The resulting artifact is promoted to production (like Model Promotion).

### L1 - Manual
- Data scientist develops code in dev
- Code manually deployed to staging
- Model trained in staging with production data (ML Jobs available for execution)
- Manual validation in staging (Experiments for comparison)
- Artifact manually promoted to prod via Model Registry
- **Environment structure**: Staging has production data access; prod receives artifact only
- **Data access**: Staging reads production data; prod serves model only

### L2 - Semi-automated
- PR-based workflow; CI runs tests
- CD deploys training pipeline to staging
- Staging pipeline trains on production data
- Automated validation pipeline in staging; human approval for prod promotion
- Artifact promoted to prod catalog on approval
- **Environment structure**: CI/CD deploys code to staging; artifact pipeline promotes to prod
- **Data access**: Staging has read access to prod data catalog

### L3 - Fully Automated
- Full CI/CD deploys pipeline to staging automatically
- Staging trains on production data on schedule/trigger
- Automated validation + auto-promotion to prod
- Automated rollback if production model degrades
- **Risk**: Staging bears full training compute cost. Staging-prod data sync must be reliable.
- **Environment structure**: Staging sized for training workloads; prod sized for serving
- **Data access**: Staging has reliable, low-latency access to production data

### 2-Environment Adaptation (Hybrid Promotion)
Hybrid Promotion **fundamentally requires a middle tier** (staging with production data access). In a 2-env setup:
- **If DEV can access production data**: The setup effectively becomes Code Promotion (train in DEV on prod data, deploy code to PROD). Recommend migrating to Code Promotion pattern.
- **If DEV cannot access production data**: A 2-env setup is not viable for Hybrid. Recommend either (a) adding a STAGING tier, or (b) switching to Model Promotion (accept that the model is trained on dev data only).
- **Planning ahead**: If a team on Model Promotion already has a Model Registry and Feature Store set up per environment (or centralized), this infrastructure simplifies a future transition to Hybrid when a STAGING tier is added.

## LLM/GenAI Promotion Adaptation

LLM workloads follow the same promotion patterns. See `mlops-pattern-framework.md` § "What Gets Promoted (by Development Approach)" for the complete mapping of LLM artifacts to promotion patterns.

Key points:
- **Prompts, RAG configs, agent definitions** → Code Promotion (version in Git, promote through environments)
- **Fine-tuned model weights** → Model Promotion (register in Model Registry, promote across environments via CI/CD)
- **Foundation model API calls** → No MLOps promotion needed (standard software CI/CD)

### Environment Considerations for LLM Workloads
- **GPU-aware serving**: When deploying fine-tuned models or custom inference via SPCS, ensure target environment has appropriate GPU compute pools.
- **Search index per environment**: Each environment maintains its own Cortex Search index built from environment-specific data. Do not promote indexes across environments.
- **Cost management**: Foundation model API costs scale with usage. Monitor token consumption per environment to avoid cost surprises during testing.

## Promotion Mechanisms and Snowflake Features

> **Always present this section** when advising on promotion patterns. It maps Snowflake objects to their promotion mechanisms and the features that enable them — essential context for the user to understand how each artifact type moves between environments.

Not all Snowflake objects move between environments in the same way, but **all promotion is executed via CI/CD pipelines** — including model registry operations. The CI/CD pipeline is the single mechanism that deploys, copies, or replicates objects across environments. Business needs (security, compliance, deployment velocity, team structure, risk tolerance) influence which promotion pattern is appropriate. There is no single correct answer; the choice must be validated with the customer.

### Model Artifacts (Model Promotion Pattern)

Model artifacts are promoted across environments via CI/CD using registry commands:

| Object | CI/CD Promotion Command | Snowflake Feature |
|---|---|---|
| **Trained model** (Snowflake-trained or external) | `CREATE MODEL ... FROM MODEL` (same-account) or replication groups (multi-account) | Model Registry |
| **Fine-tuned LLM weights** | Same as trained model | Model Registry + Cortex Fine-tuning |
| **Model serving endpoint** | Deploy/update endpoint configuration per environment | Model Serving (SPCS) |

**Cross-environment promotion** (executed by CI/CD) — each environment has its own registry (separate databases):
- **Same-account**: `CREATE MODEL <target_db>.<schema>.<model> FROM MODEL <source_db>.<schema>.<model>` copies the model to the target environment's database.
- **Multi-account**: Use **replication groups** to replicate Model Registry (ML model objects) across accounts.

> **Note**: A centralized registry (single database for all environments) may be acceptable at L0 for experimentation, but at L1+ each environment should have its own registry to maintain proper isolation.

**Aliases and versions** serve a different purpose — they are **not** the promotion mechanism between environments. Their role is:
- **A/B testing**: Route traffic between model versions (Champion vs Challenger) within the same environment
- **Experimentation**: Compare model versions offline using Experiments
- **Model switching after retraining**: Update the Champion alias to point to a newly validated version within the same environment
- **Rollback**: Revert the alias to a previous version if the new one degrades

### Code/Config Artifacts (Code Promotion Pattern)

For code artifacts, promotion means **redeploying the same parameterized code to the target environment** via CI/CD. The source of truth is Git.

| Object | CI/CD Promotion Command | Snowflake Feature / Tool |
|---|---|---|
| **Tables, views, stages** | DDL redeployed per environment | `snow sql -f` (Snowflake CLI) |
| **Stored procedures, UDFs** | DDL/code redeployed per environment | `snow sql -f` (Snowflake CLI) |
| **Cortex Agents** | Agent definition redeployed per environment | `snow sql -f` (Snowflake CLI) |
| **Semantic views** | Definition redeployed per environment | `snow sql -f` (Snowflake CLI) |
| **Cortex Search indexes** | Config redeployed; index rebuilt per environment from environment-specific data | `snow sql -f` + Cortex Search |
| **Tasks, streams, dynamic tables** | DDL redeployed per environment | `snow sql -f` (Snowflake CLI) |
| **Grants / RBAC** | Grant statements redeployed per environment | `snow sql -f` (Snowflake CLI) |
| **Prompt templates** | Versioned in Git; redeployed as part of application code | Git + CI/CD |
| **Notebooks** (when deployed) | Entity redeployed + executed per environment | `snow notebook deploy` + `EXECUTE NOTEBOOK` |
| **Python scripts** (evaluation, data prep) | Executed per environment with env variable | `python <file>` with `var_environment` |

**Key distinction**: Code-promoted objects are **recreated** in each environment from the same source code. Each environment has its own independent instance of every object. No artifact sharing across environments.

### Hybrid Promotion

Combines both mechanisms (all via CI/CD):
- **Code** is deployed to the training environment (the environment with production data access) via CI/CD (Code Promotion)
- **Model artifact** is trained in that environment, then promoted to the serving/production environment via CI/CD executing `CREATE MODEL ... FROM MODEL` or replication groups (Model Promotion)
- Supporting objects (tables, views, grants) follow Code Promotion

The specific environment names and count depend on the customer's setup — use whatever the customer calls their environments. Examples:
- **3-env** (DEV → STAGING → PROD): Code deploys to STAGING, model trains there, artifact promotes to PROD
- **3-env** (DEV → PREPROD → PROD): Code deploys to PREPROD, model trains there, artifact promotes to PROD
- **2-env** (DEV → PROD): See the "2-Environment Adaptation (Hybrid Promotion)" section above — Hybrid fundamentally requires a middle tier with production data access

### Features That Enable Promotion Workflows

| Feature | Role in Promotion |
|---|---|
| **Model Registry** | Version and store model artifacts; `CREATE MODEL ... FROM MODEL` for cross-database promotion; aliases for A/B testing and model switching within an environment |
| **Replication Groups** | Replicate Model Registry objects across accounts (multi-account promotion) |
| **Model Serving (SPCS)** | Serve models; A/B testing; canary deployment within an environment |
| **Experiments** | Compare model versions before promotion (offline evaluation) |
| **ML Observability** | Validate model performance post-promotion; trigger rollback |
| **Git Integration** | Version control for code artifacts; PR-based promotion gates |
| **Snowflake CLI** | Execute all promotion commands (SQL, Python, notebooks) via CI/CD |
| **AI Observability** | Evaluate LLM/agent quality before promotion (LLM-as-judge) |
| **Cortex Search** | Rebuild search indexes per environment (RAG promotion) |
| **Cortex Fine-tuning** | Produce fine-tuned model versions for registry promotion |

## Concrete Deployment Patterns

### ML Pipeline Step Sequence

The typical ML pipeline follows a sequence of steps, each implemented as a separate file. The sequence differs by promotion pattern:

**Code Promotion** (all steps run in each environment):
1. **Infrastructure setup** — database, schema, roles, grants (SQL, often a one-time prerequisite run outside CI/CD)
2. **Raw data ingestion** — create/load raw data tables (SQL)
3. **Feature engineering** — create Feature Store entities and FeatureViews (Python)
4. **Model training + registration** — train model using Feature Store, evaluate metrics, register with version logic (Python)
5. **Prediction / scoring** — load registered model, score using Feature Store, persist predictions (Python)

Each file runs the **same code in every environment** — environment parameterization resolves which databases/schemas to target.

**Model Promotion** (training in DEV, promotion to PROD):
1. **Infrastructure setup** — same as Code Promotion (one-time, per environment)
2. **Raw data ingestion** — per environment (SQL, deployed via CI/CD)
3. **Feature engineering** — per environment (Python, deployed via CI/CD)
4. **Model training + registration** — runs in DEV only; model registered to DEV registry with version logic (Python)
5. **Model promotion** — CI/CD copies the validated model from DEV to PROD using `CREATE MODEL ... FROM MODEL`, applying the same version logic in the target environment (Python)
6. **Prediction / scoring** — runs in PROD only, loading the promoted model from PROD registry (Python)

The key difference: in Code Promotion, the training script (step 4) runs identically in all environments. In Model Promotion, training runs only in DEV (step 4), and a separate promotion step (step 5) handles cross-environment model copy.

**Single-file dual-role pattern** (Model Promotion): A common approach is a single training/promotion script that branches on the environment variable:
- If `ENV == DEV`: train model, evaluate, register with version logic
- If `ENV == PROD`: find the candidate version in DEV registry, read its metrics, apply version logic, promote via `CREATE MODEL ... WITH VERSION ... FROM MODEL ...`

This keeps the promotion logic colocated with training logic and reduces the number of files.

For version management logic (LIVE/CANDIDATE, metric-gated promotion, archive-before-replace), see `model-lifecycle.md` § "Version Naming and Lifecycle Management."

### Environment-Parameterized Artifacts (All Patterns, L2+)

All deployable artifacts (SQL, Python, configs) should be parameterized so a single codebase deploys to any environment. For the full comparison of parameterization approaches, database naming guidance, and artifact type selection, see `ci-cd-testing.md` § "Environment Parameterization," "Naming Conventions," and "Deployable Artifact Types."

### Agent Promotion Pattern (Code Promotion)

Cortex Agents are code artifacts — agent definitions, semantic views, and tool configurations are versioned in source control and promoted through environments:

**Typical structure:**
- Data table definitions (parameterized)
- Semantic view definitions (parameterized)
- Agent/tool definitions (parameterized)
- Evaluation pipeline (script + config, parameterized)
- Project definition for managed entities (e.g., notebooks)

The CI/CD pipeline deploys the same files to each environment, substituting the environment identifier. Each environment gets its own agent, semantic view, and data — no artifact sharing across environments.

For file naming, one-file-per-object guidance (recommended where practical), and dependency ordering (numeric prefixes, dependency manifests, Snowflake Tasks), see `ci-cd-testing.md` § "File Naming and Organization" and "Selective Deployment."

For agent evaluation as a CI/CD gate, see `monitoring-rollback.md` § "Agent Evaluation Pipeline."

### Project Definitions (`snowflake.yml`)

For Snowflake-managed entities (notebooks, etc.), use `snowflake.yml` project definitions with environment parameterization. The project definition specifies the entity type, target database/schema, and associated files. The environment identifier is resolved at deployment time.

**Note**: `snowflake.yml` uses Snowflake CLI templating syntax, while SQL files may use a different templating engine (e.g., Jinja2). Be aware of which engine applies to which file type.

## See Also

- `ci-cd-testing.md` — CI/CD pipelines and environment structure for each promotion pattern
- `model-lifecycle.md` — Registry, versioning, and Champion/Challenger workflow
- `continuous-training.md` — Retraining triggers and automation by maturity level
