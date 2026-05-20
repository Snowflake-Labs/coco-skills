---
name: devops-guidance
title: Snowflake DevOps Guidance
summary: Opinionated guidance on CI/CD, Database Change Management, and deployment tooling for Snowflake.
description: "Use when choosing deployment tools, setting up CI/CD pipelines, comparing schemachange/Flyway/Liquibase/Terraform, deciding between imperative and declarative approaches, deploying simple vs complex Snowflake objects, evaluating DCM Projects, or asking about Snowflake DevOps best practices. Triggers: devops, CI/CD, database change management, DCM, schema migration, deployment pipeline, schemachange, flyway, liquibase, terraform, snowflake CLI deploy, DCM Projects, imperative vs declarative, rollback, infrastructure as code, database versioning."
tools:
  - Read
  - Write
  - Edit
  - Bash
  - snowflake_sql_execute
  - snowflake_object_search
prompt: "How should I set up a CI/CD pipeline for Snowflake schema changes?"
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Snowflake DevOps Guidance

Opinionated technical advice on Database Change Management (DCM), CI/CD pipelines, and deployment tooling for Snowflake projects.

## Overview

This skill answers questions about how to deploy and version Snowflake objects safely. It covers terminology, tool selection (schemachange, Flyway, Liquibase, Terraform, Snowflake CLI), imperative vs declarative tradeoffs, native Snowflake features (CREATE OR ALTER, EXECUTE IMMEDIATE FROM, DCM Projects), and where the deployment process should run.

Load `references/raw-guidance.md` for the full reference content (terminology, tool landscape, deployment approaches, native features, curated links).

## When to Use

- Setting up CI/CD or deployment pipelines for Snowflake
- Choosing a deployment tool (schemachange, Flyway, Liquibase, Terraform)
- Deciding between imperative and declarative approaches
- Deploying simple vs complex objects (Streamlit, Notebooks, Snowpark procs, dbt projects)
- Evaluating DCM Projects, EXECUTE IMMEDIATE FROM, or Git Repositories
- Planning rollback / failure recovery strategy

## Key Positions

1. **Imperative for pipelines.** Declarative tools (especially Terraform) should not drive CI/CD for most scenarios.
2. **Declarative still has value** for storing definitions in source control, generating initial scripts, and drift detection.
3. **Snowflake CLI (`snow`)** is the primary tool for complex objects: Streamlit, Notebooks, Snowpark stored procedures, dbt Projects.
4. **schemachange** is the recommended starting point for imperative DCM on Snowflake.
5. **External CI/CD tools** (GitHub Actions, GitLab CI, Azure DevOps, Jenkins) beat running deployments from inside Snowflake.
6. **Roll forward, not back.** Undo/rollback features are unreliable for databases — fail forward with a new migration.
7. **Combining tools is normal.** Real pipelines often mix a DCM tool, the Snowflake CLI, and others.
8. **DCM Projects** are not recommended for most deployment scenarios today (preview, limited object coverage, declarative limitations).
9. **EXECUTE IMMEDIATE FROM** is a script runner, not a DCM tool.
10. **Git Repository integration** is a development feature, not a deployment feature.

## Decision Framework

| Scenario | Recommendation |
|----------|----------------|
| New to DCM on Snowflake | schemachange |
| Already on Flyway / Liquibase | Stay on it |
| Only simple objects (tables, views, schemas) | Any imperative DCM tool |
| Only complex objects | Snowflake CLI |
| Mixed simple + complex | schemachange + Snowflake CLI in migration scripts |
| Cloud infra adjacent to Snowflake | Terraform is acceptable for infra-adjacent objects |
| Account-level / environment provisioning | Terraform is acceptable |
| No CI/CD tool, limited engineering capacity | DCM Projects may be acceptable, with caveats |

## Terraform Nuance

- Generally not recommended for object-level DCM, especially for newcomers.
- Acceptable for two scenarios: cloud-infra-adjacent objects and environment/account management.
- Real challenges: steep learning curve, state file management, incomplete provider coverage, HCL format lock-in.

## Important Distinctions

- **DCM ≠ IaC.** Database Change Management is not Infrastructure as Code.
- **Git Repository ≠ DevOps.** Connecting Snowflake to Git is not a deployment pipeline.
- **CREATE OR ALTER ≠ DCM tool.** Helpful SQL syntax, not a complete solution.
- **EXECUTE IMMEDIATE FROM ≠ pipeline.** It runs scripts; it doesn't track state or order.
- **Snowflake CLI ≠ SnowSQL.** `snow` is current; `snowsql` is the older, deprecated CLI.

## Common Mistakes

- Using Terraform to manage tables, views, and stored procedures in CI/CD — leads to brittle state files and partial drift.
- Treating Git Repositories + EXECUTE IMMEDIATE FROM as a deployment pipeline — no migration ordering, no state tracking, no idempotency guarantees.
- Skipping a DCM tool because CREATE OR ALTER exists — you still need migration history and ordering.
- Relying on rollback features (Flyway undo) — database rollbacks frequently fail; design for forward-only migrations.
- Mixing simple and complex objects in one declarative tool — Streamlit, Notebooks, and Snowpark procs need the Snowflake CLI deploy commands.
- Running deployments from inside Snowflake worksheets — no audit trail, no PR review, no environment promotion.
- Following the Snowflake DevOps landing page's EXECUTE IMMEDIATE FROM example as a production pattern — it's a primitive, not a pipeline.

## Resources

Point users to the schemachange repo, Flyway/Liquibase Snowflake docs, and Snowflake CLI deploy guides. Warn about outdated material that frames Git Repositories + EXECUTE IMMEDIATE FROM as a complete DevOps solution.

## Output

Clear, opinionated DevOps guidance with specific tool recommendations, decision criteria, and warnings about common anti-patterns. Ask a clarifying question first when the scenario is ambiguous (object types, team size, existing tooling).
