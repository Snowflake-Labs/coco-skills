---
name: devops-guidance
title: Snowflake DevOps Guidance
summary: Opinionated advice on CI/CD, Database Change Management, and deployment tooling for Snowflake.
description: "Use when picking a DCM tool, designing a CI/CD pipeline, comparing imperative vs declarative deployment, deploying simple vs complex Snowflake objects, evaluating schemachange/Flyway/Liquibase/Terraform, or weighing native features like DCM Projects and EXECUTE IMMEDIATE FROM. Triggers: devops, ci/cd, database change management, dcm, schema migration, deployment pipeline, schemachange, flyway, liquibase, terraform, snowflake cli, dcm projects, imperative vs declarative, rollback, infrastructure as code, database versioning."
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: "Help me design a CI/CD pipeline to deploy schema changes and a Streamlit app to my Snowflake account."
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Snowflake DevOps Guidance

## Overview

This skill gives opinionated guidance on how to ship changes to Snowflake — schema migrations, stored procedures, Streamlit apps, Notebooks, Snowpark code — using CI/CD and Database Change Management (DCM) tools. It helps you pick a tool, structure a pipeline, and avoid common anti-patterns.

Load `references/raw-guidance.md` for the full reference (terminology, tool comparisons, native features, curated links).

## When to Use

- Picking a DCM tool (schemachange, Flyway, Liquibase, Terraform, DCM Projects)
- Designing a CI/CD pipeline for Snowflake
- Deciding between imperative and declarative deployment
- Deploying "complex" objects (Streamlit, Notebooks, Snowpark procs, dbt projects)
- Choosing where the pipeline runs (external CI vs inside Snowflake)
- Handling rollback / failed deployments
- Evaluating Snowflake-native features: `CREATE OR ALTER`, `EXECUTE IMMEDIATE FROM`, DCM Projects, Git Repositories

## Workflow

### Step 1: Classify the question

| Category | Example |
|---|---|
| Terminology | "What is DCM?" "DevOps vs CI/CD?" |
| Tool selection | "schemachange vs Flyway?" "Should I use Terraform?" |
| Deployment approach | "Imperative or declarative?" |
| Object types | "How do I deploy a Streamlit app?" |
| Where to run | "Can the pipeline run from Snowflake itself?" |
| Native features | "Should I use DCM Projects or `EXECUTE IMMEDIATE FROM`?" |
| Rollback | "How do I undo a failed deploy?" |

### Step 2: Apply the core positions

1. **Imperative beats declarative for pipelines.** Versioned migration scripts are predictable and reviewable.
2. **Declarative tools still have value** for source-of-truth definitions, scaffolding, and drift detection — just not as the pipeline driver.
3. **Snowflake CLI (`snow`)** is the right tool for complex objects (Streamlit, Notebooks, Snowpark stored procs, dbt projects).
4. **schemachange** is the recommended starting point for imperative DCM.
5. **Run the pipeline from an external CI/CD system** (GitHub Actions, GitLab CI, Jenkins, Azure DevOps).
6. **Roll forward, not back.** Treat undo features as a last resort; write a forward fix.
7. **Combining tools is normal** — DCM tool for SQL migrations + Snowflake CLI for complex objects.
8. **DCM Projects** is not yet a strong fit for most pipelines (preview, limited object coverage, declarative).
9. **`EXECUTE IMMEDIATE FROM`** is not a deployment pipeline.
10. **Git Repository in Snowflake** is a dev-loop convenience, not a CI/CD substitute.

### Step 3: Recommend a tool

| Scenario | Pick |
|---|---|
| New to DCM | schemachange |
| Already using Flyway/Liquibase | Stay |
| Only simple objects | Any imperative DCM tool |
| Only complex objects | Snowflake CLI |
| Mixed simple + complex | schemachange + Snowflake CLI |
| Cloud infra adjacent to Snowflake | Terraform is fine for that slice |
| Account-level environment setup | Terraform is fine |

For Terraform, be specific about tradeoffs: state file management, HCL format, incomplete provider, learning curve.

### Step 4: Point to resources

Pull links from `references/raw-guidance.md`. Flag the official Snowflake DevOps landing page as outdated — it promotes `EXECUTE IMMEDIATE FROM` as a pipeline pattern.

### Step 5: Summarize and ask for follow-ups

Close with the headline recommendation and ask what to dig into next.

## Common Mistakes

- **Calling DCM "IaC".** They overlap but aren't the same; keep the terms separate.
- **Treating `CREATE OR ALTER` as a DCM tool.** It's a useful SQL primitive, not a migration system.
- **Using `EXECUTE IMMEDIATE FROM` as a deploy pipeline.** It runs a script; it doesn't track state, order, or environments.
- **Wiring Terraform directly into the deploy pipeline for app objects.** State drift and partial provider coverage will bite.
- **Confusing Snowflake CLI (`snow`) with SnowSQL (`snowsql`).** SnowSQL is legacy; use `snow`.
- **Believing a Snowflake Git Repository connection equals DevOps.** It doesn't.
- **Designing for rollback first.** Optimize for fast forward-fixes and observability.

## Stopping Points

This skill is advisory and runs no destructive operations, so no execution stopping points are required. Two soft checkpoints:

- ✋ If the question is ambiguous, ask before recommending a tool.
- ✋ If the user wants an approach this guidance argues against (e.g., Terraform-driven pipeline, `EXECUTE IMMEDIATE FROM` as CI), surface the tradeoffs and confirm before proceeding.

## Output

A direct recommendation plus the reasoning, links to the right quickstarts and docs, and a flag on any anti-patterns in the user's current setup.
