---
name: messy-migration-validation
id: messy-migration-validation
description: "Validate data marts rebuilt during a messy (not lift-and-shift) migration against a baseline modeled from the legacy system: profile, build a baseline, diff, root-cause divergences, and sign off. Use when: validating data marts, profiling tables, comparing new marts against legacy data, building baseline models, baseline comparison, divergence analysis, testing records, resuming sessions. Triggers: validate, profile, compare, QA, baseline, baseline model, data quality, divergence, test record, mapping, resume."
title: Validate Messy Migrations
summary: "Validate rebuilt data marts against a legacy-modeled baseline: profile, diff, root-cause divergences, and sign off."
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
prompt: Validate my rebuilt mart ANALYTICS_DB.MARTS.CUSTOMER_MART against the legacy system.
language: en
status: Published
author: Eric Ast
authors: Eric Ast
type: snowflake
categories: migration, data-quality, validation
---

# Messy Migration Validation

**You are using the messy-migration-validation skill. Follow these instructions exactly.**

## Overview

Migrations that rebuild models and business logic from the ground up rarely map 1:1 to the legacy system. Grain, joins, and rules evolve along the way, so a simple row-count or checksum diff is not enough. This skill gives QA analysts a repeatable process to prove a rebuilt mart is correct before stakeholders rely on it, replacing ad hoc queries and manual inspection.

**Terminology** — every comparison has two sides:

| Term | Side | Meaning |
|------|------|---------|
| **Legacy tables** | Old system | Raw tables loaded into Snowflake from the system being replaced |
| **Baseline** | Old system | A view modeled from the legacy tables to match the new mart's grain and columns (built in UC2) |
| **New mart** (or **mart**) | New system | The rebuilt Snowflake mart being validated |

In SQL templates, `GOLDEN_*` placeholders and columns refer to the baseline, and `MART_*` to the new mart.

The baseline is a reference point, not ground truth. A divergence can be a bug in the new mart, a bug in the legacy system, or an intentional business-rule change; the deep dive determines which.

**Use it when:**
- Data marts were rebuilt (not lifted and shifted) during a migration to Snowflake
- Legacy data from the original source systems has been loaded into Snowflake to serve as the comparison point
- The legacy data may already be a mart at the same grain, or may need to be modeled into one

**Not a fit when:** the migration is a like-for-like replication (use a direct table diff), or no legacy data is available to compare against.

**Prerequisites:** fully qualified mart name (`DATABASE.SCHEMA.TABLE`), access to the legacy tables, and a warehouse.
**Outputs:** artifacts in `validation-workspace/` and a `VALIDATION_TEST_LOG` table.

## When to Use

- Validating rebuilt data marts after a migration (not lift-and-shift)
- Profiling a table for summary stats, keys, anomalies
- Building a baseline model from legacy tables
- Comparing a new mart against its baseline for divergences
- Root-cause analysis on identified divergences
- Viewing, annotating, or generating a validation sign-off report

## Personas

This skill supports two personas across its use cases:

- **Use Cases 1–2 (Profile, Baseline Model):** Typically run by an **architect or data engineer in CLI (Cortex Code)**. These use cases produce foundational artifacts (profiles, views, semantic views) that downstream analysts consume.
- **Use Cases 3–4 (Baseline Diff, Deep Dive):** Typically run by a **business analyst in Snowsight**. These use cases assume foundational artifacts already exist and focus on comparison, investigation, and reporting.
- **Use Case 5 (Validation Log):** Used by either persona to view, annotate, or generate validation reports.

Sub-skills inherit this context — do not enforce persona identity as a hard gate.

## Conventions

Before loading any sub-skill, read `references/conventions.md`. It defines:

- **Naming conventions** — fully qualified table names, artifact naming, mart-name derivation
- **Schema standards** — PROFILE_RESULTS, COMPARISON_RESULTS, VALIDATION_TEST_LOG DDLs
- **Artifact metadata header** — required on every generated file
- **Session hygiene** — one-line milestone reminders at every [STOP] point
- **Session continuation** — handoff summaries (CLI) and checkpoint YAML (Snowsight) for resume
- **File generation prompts** — confirmation blocks after every file creation
- **Error handling** — recovery table for common failures

All sub-skills reference these conventions. Do not skip this step.

## Lifecycle Overview

```
UC1: Profile        → UC2: Baseline Model     → UC3: Baseline Diff  → UC4: Deep Dive
  (mart-profile)       (baseline-model)           (baseline-diff)       (divergence-deep-dive)
  Produces:            Produces:                  Requires:             Requires:
  - PROFILE_RESULTS    - Baseline view            - Semantic view       - Comparison report
  - Profile report     - Mapping YAML               (from UC2)         - Mapping YAML
                       - Semantic view (deployed)  Produces:            Produces:
                                                  - Comparison report   - RCA report
                                                  - COMPARISON_RESULTS
```

UC2 terminates at semantic view deployment. UC3 requires the deployed semantic view — if not found, redirect to UC2. UC5 (validation-log) runs alongside every phase.

## Workflow

### Step 1: Gather Context

Ask the analyst:
1. **Which data mart** are you validating? (fully qualified name: `DATABASE.SCHEMA.TABLE`)
2. **What do you want to do?**

Present these options:

```
1. Profile a data mart          — Summary stats, keys, distributions, anomalies
2. Build a baseline model       — Model the legacy tables to match the new mart
3. Compare against the baseline — Aggregate and row-level divergence detection
4. Deep dive on divergences     — Root cause analysis on comparison findings
5. View/update validation log   — Review or annotate the testing record
6. Resume a previous session    — Pick up where you left off
```

If the analyst's prompt already implies intent, skip the menu and route directly.

### Step 2: Route to Sub-Skill

| Intent | Sub-Skill | Load |
|--------|-----------|------|
| Profile, summary stats, distributions, anomalies | `mart-profile/SKILL.md` | Read `references/conventions.md` first |
| Baseline model, legacy table setup, column mapping | `baseline-model/SKILL.md` | Read `references/conventions.md` first |
| Compare, diff, divergence, match rate | `baseline-diff/SKILL.md` | Read `references/conventions.md` first |
| Root cause, why divergence, investigate mismatch, FX check, exchange rate, currency conversion | `divergence-deep-dive/SKILL.md` | Read `references/conventions.md` first |
| Test log, testing record, sign-off, report | `validation-log/SKILL.md` | Read `references/conventions.md` first |
| Resume, continue, pick up, last session | See Resume Protocol below | Read `references/conventions.md` first |

### Resume Protocol (Option 6)

When the analyst selects "Resume a previous session":

1. Scan `validation-workspace/` for `checkpoint_*` and `handoff_*` files matching the current user (derive user key per conventions)
2. Sort by `YYMMDDHH` descending — present the most recent file(s). If nothing relevant is found, prompt the user to provide more detail.
3. Read the file and summarize: "Last session: [date], use case: [UC], completed [steps], next step is [Y]. Continue?"
4. **[STOP]** Wait for analyst confirmation
5. If confirmed, load the appropriate sub-skill and resume from the `next_action` / `pending_items`
6. If no checkpoint/handoff found, inform the user and offer to start fresh from option 1–5

### Step 3: Delegate

Load the sub-skill and hand off the analyst's context (mart name, baseline if known, any prior artifacts).

## Cross-Cutting Behaviors

These apply to all sub-skills (defined in `references/conventions.md`):

- **Session hygiene**: One-line milestone reminder at every [STOP] with steps remaining
- **Session continuation**: CLI generates handoff summaries; Snowsight updates checkpoint YAML
- **File generation prompts**: Emit confirmation after every file creation with path and type
- **Report audience prompt**: Before generating any report (comparison, RCA, validation summary), ask the analyst for audience: (a) Business, (b) Technical, (c) Other
- **Error handling**: Surface errors immediately, never silently skip, follow recovery table
- **Dual persistence**: Validation log writes to both Snowflake table and local markdown

## Stopping Points

- **[STOP]** After presenting the menu — wait for analyst selection
- **[STOP]** If analyst's intent is ambiguous — ask for clarification
- **[STOP]** Resume protocol — confirm session summary before continuing

## Output

- Routing decision to the appropriate sub-skill
- Each sub-skill produces its own artifacts (profiles, mappings, comparison reports, RCA reports, test logs)
- All artifacts are written to `validation-workspace/` and logged to `VALIDATION_TEST_LOG`

## Important Rules

1. Always use fully qualified table names (`DATABASE.SCHEMA.TABLE`)
2. Never skip the conventions check — naming consistency matters across the lifecycle
3. If the analyst references a prior mapping or profile, check the `validation-workspace/` directory for existing artifacts
4. Every sub-skill logs its actions to the validation log — remind the analyst this is happening
5. UC2 must complete before UC3 can run — the semantic view is the handoff artifact
6. At every [STOP], emit a session hygiene reminder per conventions
