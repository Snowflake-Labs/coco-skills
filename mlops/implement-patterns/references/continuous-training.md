# Continuous Training

> **Environment naming**: This file uses canonical names **DEV**, **STAGING**, **PROD**. Substitute the user's preferred names in all outputs. For 2-environment setups, omit STAGING references. For environment isolation strategies and naming conventions, see `ci-cd-testing.md`.

## Trigger Types

### On-Demand
- Manual execution of training pipeline
- Used for: initial training, debugging, ad-hoc experiments
- Available at: all maturity levels

### Scheduled
- Training pipeline runs on a fixed cadence (daily, weekly, monthly)
- Used for: regularly updated data, predictable data patterns
- Available at: L2+
- **Considerations**: frequency depends on data freshness requirements and training cost

### On New Data Availability
- Pipeline triggered when new labeled data lands in source tables
- Used for: irregular data collection, event-driven data sources
- Available at: L3
- **Implementation**: event-based trigger (e.g., data pipeline completion callback, table change notification)

### On Performance Degradation
- Pipeline triggered when model performance metrics drop below threshold
- Used for: production models with accuracy SLAs
- Available at: L3
- **Implementation**: monitoring pipeline detects metric anomaly -> triggers retraining workflow
- **Considerations**: define clear thresholds; avoid retraining loops from noisy metrics

### On Concept Drift
- Pipeline triggered when input data distributions change significantly
- Used for: models sensitive to data distribution shifts
- Available at: L3
- **Implementation**: statistical tests on feature distributions (KS test, PSI, Jensen-Shannon divergence)
- **Considerations**: distinguish between natural distribution shift and data quality issues; data validation should run first

## Patterns by Maturity Level

### L1 - Manual
- Data scientist notices model degradation or gets new data
- Manually triggers retraining (ML Jobs, Distributed Training, HPO available for manual execution)
- Manually compares new model to current production model (Experiments)
- Manual Champion alias switch if improvement confirmed (within the same environment)
- **Cadence**: ad-hoc, typically monthly or less

### L2 - Semi-automated
- Scheduled retraining jobs (e.g., weekly)
- Automated training pipeline with logging
- Automated validation produces metrics
- Human reviews metrics and approves promotion
- **Cadence**: scheduled (weekly/monthly), plus on-demand
- **Key components**:
  - Orchestrated training pipeline (workflow orchestrator)
  - Experiment tracker logging all runs
  - Automated metric comparison against baseline

### L3 - Fully Automated
- All trigger types active (scheduled + data-driven + drift-driven + performance-driven)
- Automated validation + promotion if thresholds pass
- Automated rollback if new model underperforms in production
- Full metadata trail linking trigger -> training run -> model version -> deployment
- **Cadence**: event-driven, can be daily or more frequent
- **Key components**:
  - Data validation gate before training
  - Automated Champion/Challenger comparison
  - Monitoring pipeline that emits retraining triggers
  - Metadata store linking triggers to outcomes

## Retraining Pipeline Design

### Minimal Pipeline (L1-L2)
```
Data Extract -> Data Prep -> Train -> Evaluate -> Register
```

### Full Pipeline (L3)
```
Trigger -> Data Validation -> Feature Computation -> Train + Tune ->
Evaluate -> Model Validation -> Register -> Champion/Challenger ->
Deploy -> Monitor
```

### Key Decisions
- **Data scope**: Retrain on all historical data or rolling window?
- **Hyperparameter handling**: Reuse best known hyperparameters or re-tune each time?
- **Fallback**: What happens if retraining produces a worse model? (Answer: keep current Champion, alert team)
- **Resource isolation**: Retraining should not impact serving latency or availability

## LLM/GenAI Retraining & Iteration Adaptation

LLM workloads have different "retraining" triggers depending on the development approach:

### Prompt Iteration (Code Promotion)
- **Trigger**: Quality regression detected by LLM-as-judge, user feedback trends, new use cases
- **Process**: Update prompt template in Git → CI runs prompt regression tests → deploy to staging → evaluate → promote to prod
- **Cadence**: Can be frequent (daily or more) since prompt changes are lightweight

### RAG Index Refresh
- **Trigger**: New documents available, corpus updated, retrieval quality degradation
- **Process**: Update source data → Cortex Search index rebuilds (scheduled or on new data via Streams) → retrieval quality tests → promote config if changed
- **Cadence**: Tied to data freshness requirements (hourly to weekly)

### Fine-Tuning Re-runs
- **Trigger**: Accumulated human feedback data, domain shift, new training data available
- **Process**: Same as traditional ML retraining — new fine-tuning job → evaluate → register → Champion/Challenger → promote
- **Cadence**: Less frequent than prompt iteration (weekly to monthly), driven by feedback data volume

### Agentic Workflow Updates
- **Trigger**: New tools available, tool behavior changes, routing accuracy degradation
- **Process**: Update agent configuration in Git → integration tests (tool execution, routing) → promote
- **Cadence**: Event-driven (when tools change or new capabilities added)

### Key Difference from Traditional ML
Traditional ML retraining produces a new model. LLM "retraining" may produce a new prompt version (cheap, fast), a refreshed search index (medium cost), or new fine-tuned weights (expensive, slow). The CI/CD pipeline should handle all three artifact types with appropriate validation gates for each.

## See Also

- `monitoring-rollback.md` — Drift detection triggers that feed into continuous training
- `model-lifecycle.md` — Champion/Challenger workflow for validating retrained models
- `data-features.md` — Data validation before retraining begins
