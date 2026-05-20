
# Implementation Patterns

> **Platform constraint (inherited from parent):** All recommendations must assume Snowflake as the platform. Do NOT propose non-Snowflake tools or platforms unless the user explicitly asks.

## When to Load

`mlops/SKILL.md` Step 2: When user needs implementation guidance for a specific MLOps capability.

## Setup

**Load** the decision tree (ask about team structure, artifact type, and deployment frequency) if maturity context is needed.

## Workflow

### Step 1: Identify Topic

**Ask** user which area they need guidance on:

1. **Promotion Patterns** - Code / Model / Hybrid workflows, environment structure, LLM artifact promotion
2. **CI/CD & Testing** - Test strategy, deployment automation, pipeline architecture, LLM-specific tests
3. **Continuous Training** - Retraining triggers, scheduling, automation, LLM iteration cycles
4. **Monitoring & Rollback** - Drift detection, alerting, rollback, recovery, LLM evaluation
5. **Model Lifecycle** - Registry, versioning, Champion/Challenger, promotion gates, LLM versioning
6. **Data & Features** - Data validation, feature store, skew prevention, vector DB / search index
7. **Governance & Metadata** - Lineage, compliance, audit, metadata management, LLM access control

### Step 2: Gather Context

**If routed from the parent skill with a roadmap**, use the known promotion pattern, maturity levels, and environment names. Skip to Step 3.

**Otherwise, ask** user:
1. **Promotion pattern**: Before asking, briefly introduce the three promotion patterns so the user has context:
   - **Code Promotion** — Training code moves through environments (DEV → STAGING → PROD). The model is retrained in each environment using that environment's data. Best when production data is accessible from the production environment.
   - **Model Promotion** — The model is trained in one environment (typically DEV) and the trained artifact is promoted to other environments. Only the artifact moves, not the training code. Best when training is expensive or environments cannot access production data.
   - **Hybrid Promotion** — Code moves to a middle environment (e.g., STAGING) that has production data access, the model is trained there, and the artifact is promoted to production. Combines aspects of both patterns.
   Then ask: Which pattern fits your situation? Code / Model / Hybrid (or undecided)
2. **Current maturity level**: Before asking, briefly introduce the maturity levels so the user has context:
   - **L0 (Ad-hoc / Experimental)** — No formal process. Notebooks, manual everything. No production deployment.
   - **L1 (Manual)** — All core AI/ML features available — but every step is executed and approved by humans. No CI/CD, no automated monitoring, no automated governance.
   - **L2 (Semi-automated)** — CI/CD runs tests automatically, but model validation and promotion require human approval gates.
   - **L3 (Fully Automated)** — End-to-end automation including monitoring-triggered retraining, auto-validation, and auto-promotion with rollback.
   Then ask: Where does your current setup fall? L0 / L1 / L2 / L3 (or unknown)
3. **Target maturity level**: L1 / L2 / L3
4. **Environment setup**: How many environments, what names, fully isolated or shared components?

   Use the user's chosen names, environment count, and isolation model in **all outputs** (checklists, diagrams, recommendations). For full environment guidance (2-env vs 3-env trade-offs, isolation models, canonical name table), see the parent mlops skill Step 2.

> **Promotion pattern is a hard prerequisite**: All implementation recommendations in this skill are pattern-specific — CI/CD pipelines, environment structure, promotion gates, and governance all vary fundamentally between Code, Model, and Hybrid promotion. **Do not proceed to Step 3** until the user has explicitly confirmed a promotion pattern.
>
> If the user says "undecided" or doesn't know:
> 1. **Quick path**: Walk them through the decision tree in the decision tree (ask about team structure, artifact type, and deployment frequency) § "Decision Tree" — this takes ~5 minutes and yields a clear pattern choice.
> 2. **Full path**: Recommend a full assessment via the parent mlops skill for comprehensive maturity + pattern evaluation (~15 minutes).
> 3. **Do not skip**: Generating implementation guidance without a promotion pattern leads to rework (e.g., building CI/CD for Code Promotion when the team actually needs Model Promotion).
>
> If maturity level or environments are unknown, these can be estimated — but promotion pattern **must** be explicit.

If maturity level is unknown, estimate based on their answers or suggest running the parent mlops skill first. **Never ask the user to self-assess their maturity level without first explaining what each level means.**

### Step 3: Load and Present Pattern

Based on topic selection, **Load** the corresponding reference:

| Topic | Reference |
|-------|-----------|
| Promotion Patterns | `references/promotion-patterns.md` |
| CI/CD & Testing | `references/ci-cd-testing.md` |
| Continuous Training | `references/continuous-training.md` |
| Monitoring & Rollback | `references/monitoring-rollback.md` |
| Model Lifecycle | `references/model-lifecycle.md` |
| Data & Features | `references/data-features.md` |
| Governance & Metadata | `references/governance-metadata.md` |

Present the relevant maturity level section (L1/L2/L3) for the user's promotion pattern. Include:
- What to implement
- How it works
- Key decisions
- Risk callouts (if applicable)

**When the topic is Promotion Patterns**: Always present the "Promotion Mechanisms and Snowflake Features" section from `references/promotion-patterns.md`. This gives the user a concrete view of how each artifact type moves between environments via CI/CD, which Snowflake commands are used, and which features enable the workflow. Present it alongside the pattern-specific guidance — do not wait for the user to ask.

### Step 4: Actionable Checklist

Produce an implementation checklist tailored to the user's context:

```
Implementation Checklist: [Topic] at L[X] [Pattern] Promotion
==============================================================
[ ] Step 1: [specific action]
[ ] Step 2: [specific action]
[ ] Step 3: [specific action]
...
Prerequisites: [list]
Depends on: [other capabilities that must be in place]
```

## Stopping Points

- ✋ After Step 1: Confirm topic selection
- ✋ After Step 2: **Hard gate** — promotion pattern must be explicitly confirmed before proceeding. Confirm context (promotion pattern, maturity levels, environment setup) before loading reference. **Load only** the corresponding reference (do not preload all references)
- ✋ After Step 3: **Present** the key decisions and risk callouts from the pattern. **Ask** the user to confirm the approach before generating the checklist
- ✋ After Step 4: Review checklist for feasibility

## Output

- Pattern guidance for selected topic at specified maturity level
- Implementation checklist with prerequisites and dependencies

## Troubleshooting

**User doesn't know their maturity level:**
- Suggest running the parent mlops skill first for a full assessment, or estimate based on their answers.

**Pattern doesn't cover the user's use case:**
- Check if a combination of patterns applies. Present the closest match and note gaps explicitly.

**User wants guidance across multiple topics at once:**
- Prioritize by dependency order: promotion patterns -> CI/CD -> data/features -> CT -> monitoring -> governance. Work through one at a time.
