# Monitoring & Rollback

> **Scope**: This file covers the *process* layer — what to monitor, when to alert, when to roll back, what runbooks to follow. For the *technical implementation* (setting up monitoring dashboards, ML Observability API, alerting code), use the `machine-learning` skill.

## What to Monitor

### Model Performance Metrics
- Accuracy, precision, recall, F1 (classification)
- RMSE, MAE, MAPE (regression)
- Business-specific KPIs (conversion rate, revenue impact)
- Prediction confidence distribution

### Data Drift
- Feature distribution shifts (KS test, PSI, Jensen-Shannon divergence)
- Schema changes (new/missing features, type changes)
- Data volume anomalies (sudden increase/decrease in input data)

### Concept Drift
- Relationship between features and target has changed
- Detected by comparing model predictions to delayed ground truth
- Leading indicator: performance degradation on recent data

### Infrastructure Metrics
- Prediction latency (p50, p95, p99)
- Queries per second (QPS) / throughput
- Error rates (4xx, 5xx)
- Resource utilization (CPU, memory, GPU)

## Patterns by Maturity Level

### L1 - Manual
- Manual dashboard checks (ad-hoc)
- Data scientists periodically review model predictions
- Model Serving Autocapture available (inference logs collected, manually reviewed)
- No automated alerting (ML Observability is L2+)
- Performance issues discovered reactively
- **Tools**: Notebooks, manual queries against prediction logs, Experiments (metric comparison)

### L2 - Semi-automated
- Automated dashboards tracking key metrics
- Alerting rules for threshold violations (email, Slack)
- Inference tables capture request/response data automatically
- Scheduled jobs compute drift metrics
- **Tools**: Monitoring dashboards, SQL alerts, inference tables
- **Key setup**:
  - Define metric baselines from initial model deployment
  - Set alert thresholds (e.g., accuracy drops >5% from baseline)
  - Schedule weekly drift analysis jobs

### L3 - Fully Automated
- Real-time monitoring with automated drift detection
- Alerts trigger automated actions (retraining, rollback)
- A/B test monitoring with automated winner selection and auto-promotion
- Anomaly detection on metrics (not just threshold-based)
- Full observability pipeline: logs -> metrics -> traces -> alerts -> actions
- **Tools**: Real-time monitoring, automated trigger pipelines
- **Key setup**:
  - Monitoring pipeline feeds into CT trigger system
  - Automated rollback rules (e.g., if p95 latency > 500ms for 5 min, rollback)
  - Canary analysis automation (compare canary metrics to baseline)

## Rollback Patterns

Rollback is done by updating the version name and alias to point to a previous known-good version. The archive-before-replace pattern (see `model-lifecycle.md` § "Version Naming and Lifecycle Management") ensures previous versions are always available for rollback.

### L1 - Manual Alias Revert
- Identify previous model version in registry
- Manually switch alias to point to previous version, or set default version to a known-good version
- Verify serving endpoint picks up the change
- **RTO**: Minutes to hours depending on team availability

### L2 - Documented Runbook
- Written procedure for rollback
- Previous model version tagged and easily identifiable
- Semi-automated: human triggers rollback, automation executes
- Post-rollback validation checklist
- **RTO**: Minutes (once triggered)

### L3 - Automated Rollback
- Monitoring detects degradation automatically
- Rollback triggered if performance drops below threshold for sustained period
- Previous version auto-restored via alias switch or `ALTER MODEL SET DEFAULT_VERSION` to a known-good version
- Serving endpoint auto-switches with zero downtime
- Automated notification to team
- Post-rollback diagnostic job runs automatically
- **RTO**: Seconds to minutes (fully automated)

## Alerting Strategy

| Severity | Condition | Action |
|----------|-----------|--------|
| **P1 - Critical** | Model returning errors, endpoint down | Auto-rollback + page on-call |
| **P2 - High** | Performance below SLA threshold | Auto-rollback or trigger retraining + alert team |
| **P3 - Medium** | Drift detected, performance trending down | Trigger retraining + notify data scientist |
| **P4 - Low** | Minor metric changes, informational | Log + dashboard update |

## LLM/GenAI Monitoring Adaptation

### LLM-Specific Metrics
- **Hallucination rate**: Frequency of factually incorrect or unsupported claims
- **Groundedness**: Degree to which responses are grounded in provided context (RAG) or training data
- **Answer relevance**: How well responses address the user's question
- **Token cost**: Input + output token consumption per request (cost tracking)
- **Safety violations**: Responses flagged for harmful, biased, or inappropriate content
- **Retrieval quality** (RAG): Precision@k and recall@k of retrieved context chunks
- **Latency breakdown**: Time spent in retrieval vs generation vs tool execution (agentic)

### LLM Evaluation Patterns

These expand on the LLM Evaluation capability dimension from `mlops-pattern-framework.md`:

#### L1 - Manual
- Human reviewers spot-check a sample of LLM outputs
- Manual assessment of quality, relevance, safety

#### L2 - Semi-automated
- **LLM-as-judge**: Automated evaluation using AI Observability — metrics for accuracy, groundedness, relevance scored by evaluator LLM
- Human review on flagged outputs (low-confidence or safety-flagged)
- Evaluation runs on each deployment (prompt change, RAG update, fine-tune)
- Multi-version comparison: A/B test prompt versions or RAG configurations (human decision)

#### L3 - Fully Automated
- Continuous LLM-as-judge evaluation on production traffic (sampled)
- Human feedback loops integrated (thumbs up/down, corrections feed back into evaluation)
- Automated multi-version comparison with auto-promotion based on evaluation metrics
- Auto-alert on quality regression; auto-rollback if metrics drop below threshold

### LLM Rollback Patterns
- **Prompt rollback**: Revert to previous prompt template version in Git (fast, zero-downtime)
- **RAG rollback**: Revert search index configuration or switch to previous index version
- **Fine-tuned model rollback**: Revert Model Registry alias to previous fine-tuned version (same as traditional ML)
- **Agentic rollback**: Revert agent configuration (tool definitions, routing rules) via Git

## Agent Evaluation Pipeline (CI/CD-Integrated)

Cortex Agents can be evaluated automatically in CI/CD using `EXECUTE_AI_EVALUATION`. This enables regression testing on every agent deployment.

### Pipeline Concepts

1. **Evaluation data table** — a table of ground-truth Q&A pairs (input queries + expected outputs), curated from domain experts or historical validated interactions.
2. **Evaluation configuration** — a YAML config specifying the dataset, agent reference, run metadata, and metrics to evaluate (e.g., `answer_correctness`, `logical_consistency`). The config should be parameterized for multi-environment deployment (database/schema names vary per environment).
3. **Execution** — upload the rendered config to a stage and call `EXECUTE_AI_EVALUATION`. Use unique run identifiers (e.g., timestamp suffixes) to avoid collisions across concurrent runs.
4. **Deployment gate** — block promotion if evaluation metrics fall below a threshold.

### Integration with CI/CD
- **L2**: Evaluation runs on every agent deployment; human reviews results before promotion
- **L3**: Automated evaluation with auto-reject if metrics regress below baseline

### Key Practices
- Use **unique identifiers** on dataset/run names to avoid collisions across concurrent or parallel runs
- Store evaluation configs alongside agent code in version control
- Compare evaluation results across deployments to detect quality regressions
- Parameterize environment-specific references (database, schema, agent name) in the eval config

## See Also

- `continuous-training.md` — Automated retraining triggered by monitoring alerts
- `model-lifecycle.md` — Rollback via Champion/Challenger alias swap
- `governance-metadata.md` — Audit trail for rollback events and incident response
