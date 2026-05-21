# CI/CD & Testing

> **Environment naming**: This file uses canonical names **DEV**, **STAGING**, **PROD**. Substitute the user's preferred names in all outputs. For **2-environment setups** (DEV → PROD only), see the 2-env adaptation notes in the Environment Structure section. For isolation strategies, see the "Environment Isolation Strategies" section below.

## CI - Code Tests

### L1 - Manual
- Tests exist but are run manually by data scientists
- Testing is part of notebook or ML Jobs execution
- No automated test suite
- No Git Integration or Snowflake CLI (automation tools — L2)

### L2 - Semi-automated
- Auto-run on PR via CI pipeline
- Unit tests for feature engineering logic (transformations, encodings)
- Unit tests for data processing methods
- Unit tests for utility functions and helpers
- Test coverage tracked but not blocking

### L3 - Fully Automated
- All L2 tests + blocking merge gate (PRs cannot merge if tests fail)
- Test coverage requirements enforced
- Linting and code quality checks automated

## CI - ML-Specific Tests

### L1 - Manual
- Ad-hoc checks in notebooks (Experiments for manual comparison)

### L2 - Semi-automated
- Model training convergence test (loss decreases over iterations on sample data)
- NaN/infinity checks (no NaN values from division by zero or extreme values)
- Artifact production validation (each pipeline step produces expected outputs)
- Model overfits on small sample (sanity check that model can learn)

### L3 - Fully Automated
- All L2 tests + component output tests (each component produces expected schema/format)
- Cross-component integration tests (full pipeline runs end-to-end in staging)
- Model reproducibility tests (same input produces consistent outputs)
- Performance regression tests (new model meets minimum quality bar)

## CD - Infrastructure Validation

### L1 - Manual
- Manual check that packages are installed in serving environment
- Manual verification of compute resources
- SPCS available for containerized workloads (manual container health checks)

### L2 - Semi-automated
- Automated package/dependency compatibility verification before deploy
- Automated check that model format matches serving infrastructure expectations
- Automated verification of API contract (input/output schema)

### L3 - Fully Automated
- All L2 checks + memory/compute/accelerator resource availability checks
- Container image vulnerability scanning
- Network connectivity validation
- Serving environment health checks pre-deployment

## CD - Service Testing

### L1 - Manual
- Manual API call to test prediction service
- Manual spot-check of predictions

### L2 - Semi-automated
- Automated prediction service API tests (expected inputs produce expected outputs)
- Input/output schema validation
- Error handling tests (malformed input, missing features)
- Load testing (QPS benchmarks, latency SLAs)
- Canary deployment testing (small traffic percentage to new model, human promotion decision)
- Shadow mode testing (new model runs alongside old, outputs compared)

### L3 - Fully Automated
- All L2 tests + automated canary validation (auto-promote/rollback based on metrics)
- End-to-end latency profiling with automated regression detection
- Automated traffic ramp-up and rollback without human intervention

## CD - Deployment Execution

### L1 - Manual
- Manual alias update in Model Registry
- Manual endpoint configuration
- Manual notification to stakeholders

### L2 - Semi-automated
- CI/CD triggers deployment
- Human approval gate before production deployment
- Automated deployment to staging/test environments
- Semi-automated deployment to production after approval

### L3 - Fully Automated
- Fully automated deployment with zero-downtime (blue-green or rolling)
- Automated rollback on health check failure
- Automated canary promotion (gradual traffic shift)
- Deployment notifications and audit logging

## Pipeline Architecture

### L1 - Manual
- Same scripts run manually in each environment
- ML Jobs available for manual execution (no orchestration)
- SPCS available for containerized workloads (manually deployed)
- Manual transitions between steps

### L2 - Semi-automated
- Parameterized pipeline, same code across environments with config differences
- Modularized components (reusable across pipelines)
- Pipeline orchestrator manages step transitions
- Shared libraries for common operations
- Containerized pipelines available for teams ready to adopt

### L3 - Fully Automated
- Identical containerized pipeline across all environments (config-driven only)
- Components independently versioned, composable, and auto-tested
- Pipeline DAG defined declaratively
- Automatic retry and failure handling per component

## Environment Isolation Strategies

Environments can be isolated at two levels:

| Strategy | How It Works | When to Use |
|---|---|---|
| **Same-account isolation** (recommended default) | Each environment is a separate **database** within the same Snowflake account. Naming conventions distinguish environments (e.g., `PROJECT_DEV`, `PROJECT_PROD`). | Most teams. Simpler to manage, lower overhead. |
| **Multi-account isolation** | Each environment is a **separate Snowflake account**. | Strict regulatory/compliance requirements, hard network/data boundaries between environments. |

**Schema-level isolation is not recommended** — using schemas within the same database to separate environments leads to fragile naming, complex RBAC, and accidental cross-environment access.

In both strategies, **naming conventions are critical** — they are the primary mechanism for identifying which environment, layer, and purpose each object serves. See the "Naming Conventions" section below.

## Data Architecture Layers

Data architectures typically follow a **three-layer pattern**. Names vary across organizations, but the concept is consistent:

| Layer | Also Called | Purpose |
|---|---|---|
| **Raw** | Bronze, Landing, Ingestion | Raw data as-is from source systems |
| **Integration** | Silver, Curated, Cleaned | Cleaned, conformed, deduplicated data |
| **Presentation** | Gold, Consumption, Analytics | Business-ready data, aggregates, feature tables, ML-ready datasets |

**Layer implementation**: The recommended approach is to implement each layer as a **separate database** (e.g., `RAW_DEV`, `INTEGRATION_DEV`, `PRESENTATION_DEV`). This provides clear isolation between layers, simpler RBAC (grants at the database level), and cleaner naming. Using schemas within a single database to represent layers (e.g., `PROJECT_DEV.RAW`, `PROJECT_DEV.INTEGRATION`) is possible but **not recommended** — it leads to a single large database with complex cross-schema permissions and makes it harder to manage access by layer.

**The data architecture must be identical across all environments.** Each environment (DEV, STAGING, PROD) should have the same layers, same databases, same structure — only the data content and volume may differ. This ensures that code tested in DEV behaves identically when promoted to PROD.

## Environment Structure

For environment isolation strategies (same-account vs multi-account) and data architecture layers, see the dedicated sections above.

### L1 - Manual
- Separate environments for dev/staging/prod — a database per environment (same-account) or a separate account per environment (multi-account). See "Environment Isolation Strategies" above.
- RBAC with basic role separation across environments
- Manual access control
- No formal promotion process between environments
- No Git Integration or Snowflake CLI (L2+)

### L2 - Semi-automated
- Separate environments per environment (database or account — see isolation strategy)
- Role-based access control
- CI/CD orchestrates transitions between environments
- Staging mirrors production configuration

### L3 - Fully Automated
- Fully isolated environments with infrastructure-as-code
- Automated environment provisioning
- Production-like staging (same compute, same data access patterns)
- Resource isolation between training and serving in production

### 2-Environment Adaptation (Environment Structure)
When no STAGING environment exists (DEV → PROD only):
- **L1**: Single DEV workspace for experimentation + validation. PROD receives promoted artifacts or code. Manual access control between environments.
- **L2**: DEV must absorb staging responsibilities — integration tests, validation pipelines, and approval gates all run in DEV. CI/CD gates must be stricter to compensate. Consider a dedicated DEV namespace or schema for pre-production validation.
- **L3**: DEV environment must be production-like (same compute profile, representative data) for meaningful automated validation. Resource isolation between validation workloads and experimentation in DEV.
- **Cross-component integration tests** (normally run in staging at L3) move to DEV. Ensure DEV has sufficient compute for these heavier test suites.
- **Registry and Feature Store per environment**: At L1+, each environment should have its own Model Registry and Feature Store (separate databases). CI/CD pipelines enforce access control at the promotion boundary (e.g., only the CD pipeline can execute `CREATE MODEL ... FROM MODEL` to promote models to production). A centralized registry across environments is only acceptable at L0 for experimentation.
- **Graduation signal**: If DEV validation frequently misses issues caught only in PROD, or if compliance audits require a dedicated pre-production tier, recommend adding a STAGING environment.

For RBAC and security model guidance, see `governance-metadata.md` § "RBAC / Security Model."

## LLM/GenAI CI/CD Adaptation

### CI — LLM-Specific Tests

#### L2 - Semi-automated
- **Prompt regression tests**: Run updated prompts against a golden evaluation set; compare LLM-as-judge scores to baseline
- **RAG retrieval quality tests**: Verify retrieval precision@k and recall@k on known query-document pairs
- **Chain integration tests**: End-to-end test of multi-step LLM pipelines (retrieval → generation → post-processing)
- **Safety/guardrail tests**: Verify prompts don't produce harmful outputs on adversarial test inputs

#### L3 - Fully Automated
- All L2 tests + blocking merge gate for prompt/RAG changes
- **Multi-model comparison**: Automated evaluation across model versions (fine-tuned) using AI Observability
- **Cost regression tests**: Flag prompt changes that significantly increase token usage
- **Latency regression tests**: Flag changes that increase end-to-end response time

### CD — LLM Artifact Deployment

| Artifact | Deployment Mechanism |
|---|---|
| Prompt templates | Git-based deployment (same as application code) |
| RAG index config | Deploy config → Cortex Search rebuilds index per environment |
| Fine-tuned weights | `CREATE MODEL ... FROM MODEL` (cross-env promotion via CI/CD); alias switch for Champion/Challenger within environment |
| Agent definitions | Git-based deployment of tool configs and orchestration code |

For rollback patterns per artifact type, see `monitoring-rollback.md` § "LLM Rollback Patterns."

### Environment Considerations
- **Search index per environment**: Cortex Search index is rebuilt per environment — see `promotion-patterns.md` § "Environment Considerations for LLM Workloads."
- **GPU compute pools**: CD pipeline must verify target environment has available GPU resources before deploying fine-tuned model serving endpoints.
- **Token budget gates**: Optional — CD pipeline can enforce maximum estimated token cost per deployment before promoting to production.

## CI/CD Authentication

CI/CD pipelines need non-interactive authentication to Snowflake. The approach depends on the CI platform and security requirements.

### L1 - Manual
- Personal credentials used interactively
- No service accounts or automation-specific credentials

### L2 - Semi-automated
Pipelines should use **dedicated service accounts** with the **least-privilege principle**:

1. **Snowflake service users** (`TYPE = SERVICE`) — the preferred identity type for CI/CD automation. Service users cannot log in interactively and are purpose-built for programmatic access.
2. **One service identity per environment** — each environment gets its own service account scoped to its own resources, enforcing isolation at the identity layer.
3. **Short-lived credentials preferred** — authentication methods that issue short-lived tokens reduce blast radius if compromised.
4. **No stored secrets when possible** — some CI platforms support identity federation (e.g., OIDC). Snowflake supports Workload Identity Federation (WIF), allowing CI platforms to authenticate via JWT tokens without storing secrets. This requires a one-time API integration setup (ACCOUNTADMIN). Present OIDC/WIF as an option when the customer asks about authentication — it eliminates secret management but requires platform support and initial setup. Do not default to it without confirming it meets customer requirements.
5. **Temporary connections** — when using OIDC/WIF, prefer `--temporary-connection` with the Snowflake CLI. This avoids requiring a `config.toml` file in the CI environment; authentication is handled entirely via the OIDC token and environment variables (e.g., `SNOWFLAKE_ACCOUNT`).
6. **Connection validation step** — always validate the connection before deploying (`snow connection test --temporary-connection`). This catches authentication or network issues early, before any changes are applied.
7. **Python scripts in CI/CD** typically cannot use `get_active_session()` — they must create explicit sessions using environment variables injected by the CI pipeline. For OIDC/WIF, the required environment variables are:
   - `SNOWFLAKE_ACCOUNT` — the Snowflake account identifier
   - `SNOWFLAKE_AUTHENTICATOR` — set to `WORKLOAD_IDENTITY`
   - `SNOWFLAKE_WORKLOAD_IDENTITY_PROVIDER` — set to `OIDC`
   - `SNOWFLAKE_TOKEN` — the JWT token issued by the CI platform (injected automatically by the OIDC action)
   
   The Python script reads these from `os.environ` and passes them to `Session.builder.configs(connection_params).create()`. The environment variable for the target environment (e.g., `var_environment`) is passed separately and used to construct database/schema names at runtime.

**Common authentication patterns** (non-exhaustive):

| Pattern | Secret Storage | Token Lifetime | Availability |
|---|---|---|---|
| **OIDC / WIF** | None — CI platform issues JWT | Minutes | Platforms supporting OIDC |
| **Key-pair rotation** | Private key in CI secrets | Until rotated | Any |
| **OAuth client credentials** | Client ID + secret in CI secrets | Configurable | Any |

### L3 - Fully Automated
- All L2 + network policies restricting CI/CD traffic to known IP ranges
- Automated credential rotation and audit logging
- Least-privilege service roles reviewed periodically

## Environment Parameterization

A single codebase should deploy across all environments by parameterizing environment-specific values (database names, schema names, warehouse names, roles). This works best when the **environment** is a naming segment in the naming convention — the parameterization variable substitutes the environment portion of object names, allowing the same code to target DEV, STAGING, or PROD without modification.

**Approaches** (choose based on team tooling and preferences):

| Approach | How It Works |
|---|---|
| **Snowflake CLI Jinja2** (suggested default) | `snow sql -f <file>.sql --enable-templating JINJA -D "var_environment=PROD"` |
| **CI/CD variable substitution** | `envsubst`, `sed`, or platform-native variable injection |
| **Python-based rendering** | Jinja2 or string formatting in a deploy script |
| **Snowflake Scripting** | Session variables (`SET var_environment = 'PROD';`) |

**Database naming** should include the **environment** segment (highly recommended — see "Naming Conventions" below) along with other relevant segments agreed upon with the user (business entity, layer, etc.). This enables the same code to target different databases per environment by substituting a single variable.

For Python scripts, pass the environment as an environment variable and read it at runtime.

## Naming Conventions

Consistent naming is foundational — it is the primary mechanism for identifying objects across environments, layers, and teams.

### Snowflake Object Naming

A naming convention should be agreed upon with the user during the assessment phase. The following are **naming segments to consider** — present all of them to the user, discuss which are relevant, and let the user decide which to adopt, in what order, and with what separators. Not all segments apply to every object type.

| Segment | Purpose | Examples | Applies To |
|---|---|---|---|
| **Environment** | Which environment this belongs to | `DEV`, `STAGING`, `PROD` | Highly recommended for databases, warehouses, roles. Primary discriminator for environment parameterization. |
| **Business entity / project** | What business domain or project this belongs to | `SUPPLY_CHAIN`, `FRAUD`, `MARKETING` | Databases, schemas, tables |
| **Data architecture layer** | Which layer in the data pipeline | `RAW`, `INTEGRATION`, `PRESENTATION` (or `BRONZE`, `SILVER`, `GOLD`) | Databases (recommended), tables, views |
| **Department / team / function** | Who owns or consumes this | `DATA_ENG`, `ML`, `ANALYTICS`, `FINANCE` | Databases, schemas, roles, warehouses |
| **Source system** | Where the data originates | `SAP`, `SALESFORCE`, `STRIPE`, `KAFKA` | Tables, stages, pipes |
| **Object type** | What kind of object this is (when not obvious from context) | `TBL`, `VW`, `SP`, `TASK`, `AGT` | Schema-level objects (optional — some teams prefer this, others find it redundant) |
| **Region / geography** | Where the data applies geographically | `US`, `EU`, `APAC`, `GLOBAL` | Databases, schemas, tables |
| **Temporal grain / cadence** | Frequency or time granularity | `DAILY`, `MONTHLY`, `HOURLY`, `SNAPSHOT` | Tables, tasks, streams |
| **Version / variant** | Distinguishes variants or versions of the same object | `LEGACY`, `EXPERIMENTAL` | Tables, models, views |
| **Additional modifier(s)** | Extra clarity as needed | `<CUSTOM_SEGMENT>` | Any |

The convention applies to **all Snowflake objects** — databases, schemas, tables, views, agents, warehouses, roles, etc. Database names in particular should include the **environment** segment since it is the primary discriminator for environment parameterization (see "Environment Parameterization" above).

**Do not prescribe or enforce a specific convention** — present the full list of segments, discuss which are relevant for the user's context, and let the user define or confirm a convention that fits their organization. The naming convention should be reviewed and agreed upon during the assessment process (see the parent mlops skill).

### Repository Structure

The repository folder structure should ideally **mirror the Snowflake object hierarchy** when practical: database → schema → schema-level objects. This alignment makes it intuitive to navigate, ensures CI/CD pipelines can map files to their target locations, and keeps the repository in line with the data architecture layers. However, this is not always feasible (e.g., monorepos with non-Snowflake code, legacy project structures, or cross-database shared utilities). When a strict mirror is not possible, aim to preserve the mapping at the level that matters most (typically database folders aligned to data architecture layers).

**Recommended structure** (layers as databases):
```
<repo_root>/
├── <layer_database>/                  # One folder per database (layer + environment parameterized)
│   ├── <schema>/                      # One folder per schema within the database
│   │   ├── tables/                    # Grouped by object type (optional — depends on volume)
│   │   │   ├── <object_name>.sql
│   │   │   └── ...
│   │   ├── views/
│   │   ├── procedures/
│   │   └── ...
│   └── <schema>/
├── <layer_database>/                  # Another layer database
│   └── ...
├── snowflake.yml                      # Project definition (if using managed entities)
└── ...
```

- **Database folders** correspond to Snowflake databases, typically one per data architecture layer (e.g., `raw/`, `integration/`, `presentation/`). When using environment parameterization, the folder name represents the database *template* (the environment segment is resolved at deploy time).
- **Schema folders** represent schemas within each layer database, organized by business domain, function, or source system.
- **Object-type subfolders** (e.g., `tables/`, `views/`, `procedures/`, `tasks/`, `agents/`) are optional — useful when a schema contains many objects, unnecessary when it has few.
- The structure is a recommendation — adapt it to the organization's existing conventions. The key principle is that **navigating the repo should feel like navigating Snowflake**.

### File Naming and Organization

- **One file per Snowflake object is recommended where practical** — ideally, each SQL or Python file defines exactly one Snowflake object (one table, one view, one agent, etc.). This enables selective deployment, clean diffs, and clear ownership. However, this is not always feasible (e.g., tightly coupled objects, migration scripts, or objects with cross-dependencies). When a file must define multiple objects, document the reason and keep the scope as narrow as possible.
- **File names should closely mirror the Snowflake object name** — this makes it easy to find the source file for any object and vice versa. Example: `supply_chain_demand_forecast_features.sql` → `SUPPLY_CHAIN_DEMAND_FORECAST_FEATURES` table.
- **Numeric prefixes** are a simple option to indicate execution order when files have dependencies (e.g., `01_raw_table.sql`, `02_integration_view.sql`, `03_presentation_agent.sql`). For runtime orchestration, Snowflake Tasks can also manage dependency ordering declaratively.
- Apply the same naming discipline to Python and notebook files — the file name should indicate which object or pipeline step it implements.

## Deployable Artifact Types

CI/CD pipelines may deploy different file types to Snowflake. Each has different strengths:

| Type | Best For | CI/CD Deployment |
|---|---|---|
| **SQL files** (`.sql`) | DDL, object definitions (tables, views, agents, semantic views, grants) | `snow sql -f <file> --enable-templating JINJA -D "var_environment=<ENV>"` |
| **Python files** (`.py`) | Complex logic, evaluations, data transformations, API calls | `var_environment=<ENV> python <file>` |
| **Notebooks** (`.ipynb`) | Exploration, experimentation, ad-hoc analysis | `snow notebook deploy <entity>` + `EXECUTE NOTEBOOK <db.schema>.<entity>(...)` |

**Advisory guidance on artifact selection:**

- **SQL and Python are recommended for production deployments** — they are deterministic, easily testable, diffable in code review, and straightforward to parameterize.
- **Notebooks are best suited for experimentation and exploration.** When a notebook matures into a production artifact, consider converting it to a Python script — extract the logic, add proper argument handling, and remove interactive/visualization cells.
- If a notebook must be deployed (e.g., reporting step), it can be managed via `snowflake.yml` project definitions. Deployment is a two-step process: first deploy the entity (`snow notebook deploy <entity_name> --replace`), then execute it (`EXECUTE NOTEBOOK <database.schema>.<entity_name>(...)`). Environment parameters can be passed at both steps.
- When notebook files use numeric prefixes for ordering (e.g., `04_eval_review.ipynb`), strip the prefix to derive the Snowflake entity name (e.g., `eval_review`).
- CI/CD pipelines should **route deployment by file type** — each type has a different execution mechanism. The pipeline should iterate over changed files, detect the extension, and call the appropriate deployment command.

## Selective Deployment

At L2+, CI/CD pipelines should deploy **only changed files** rather than re-executing the entire codebase. This reduces deployment time and blast radius.

**Change detection pattern:**
1. Compare the current commit to the previous deployment baseline (e.g., `HEAD~1` or last deployed tag)
2. Filter for added, copied, modified, and renamed files (exclude deleted) — only deployable extensions (`.sql`, `.py`, `.ipynb`)
3. Sort the results — if files use numeric prefixes for ordering, alphabetical sort produces the correct execution order
4. Route each file to its deployment mechanism based on file type (see "Deployable Artifact Types" above)
5. If no deployable files changed, exit early with a success status

**Ordering matters** — files often have dependencies (e.g., table must exist before semantic view, semantic view before agent). Dependency ordering can be managed via naming conventions (numeric prefixes), explicit dependency manifests, or Snowflake Tasks for runtime orchestration.

## CI/CD Pipeline Structure (L2+)

A well-structured CI/CD pipeline follows a consistent shape regardless of CI platform:

1. **Checkout** — clone the repository with minimal depth (only enough for change detection)
2. **Authenticate** — set up Snowflake CLI with the chosen authentication method
3. **Install dependencies** — install Python packages needed by deployment scripts
4. **Validate connection** — test that authentication works before deploying anything
5. **Detect changes** — identify which files changed since the last deployment
6. **Deploy** — route each changed file to its deployment mechanism by file type

**Key principles:**
- **Pin tool versions** — pin the Snowflake CLI version for reproducible deployments across runs
- **One environment per pipeline run** — use CI platform environments (e.g., GitHub Environments) to scope OIDC trust, variables, and approval gates per environment
- **Environment as a variable** — pass the environment identifier at the command level for every deployment command, not as a global config
- **Fail fast** — validate the connection before deploying; detect empty changesets early
- **Minimal checkout** — shallow clone with just enough history for diff-based change detection

For an annotated example using GitHub Actions with OIDC authentication (one of the supported patterns), see `templates/github-actions-deploy.yml`.

## See Also

- `promotion-patterns.md` — How environment structure varies by promotion pattern
- `continuous-training.md` — Retraining pipelines that CI/CD must support
- `data-features.md` — Data validation tests to include in CI pipeline
