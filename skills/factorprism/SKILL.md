---
name: factorprism
title: Explain Metric Changes
summary: Find and quantify the business locations driving a metric change with FactorPrism inside Snowflake.
description: "Use when a Snowflake user asks why revenue, margin, cost, churn, units, denials, or another business metric changed; which region, product, payer, segment, or intersection drove it; wants a reconciled variance explanation; or needs help installing or evaluating FactorPrism for that job. Triggers: why did this metric move, what drove the change, revenue variance, margin variance, root cause, explain this spike, explain this drop. Do not use for forecasting, anomaly detection, marketing multi-touch attribution, price-volume-mix analysis, or data outside Snowflake."
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Read
prompt: why did weekly revenue drop across region and product?
language: en
status: Published
author: David Rimshnick
type: community
---

# FactorPrism

## Overview

Use FactorPrism to answer: **Why did this business metric move, and where did the
change originate?** FactorPrism runs inside the customer's Snowflake account and
returns a ranked explanation whose contributions reconcile to the observed change.

Read [references/api.md](references/api.md) before constructing the SQL call.

## Workflow

1. **Confirm the job.** Ask for the metric, date field, analysis window, time grain,
   source table or view, and business dimensions. Clarify whether dimensions are
   independent or form a broad-to-narrow hierarchy.
2. **Confirm availability.** Verify that FactorPrism is installed and identify its
   application database name. Do not invent an installation name. If it is not
   installed, stop before inspecting or binding customer data and offer the two
   paths in [Getting FactorPrism](#getting-factorprism). Continue only after the
   user chooses a path and any required installation is complete.
3. **Inspect the source.** Use object search or `DESCRIBE` to verify column names and
   types. Never request row-level sensitive data in chat.
4. **Bind only when necessary.** If the requested table or view is not already bound,
   explain that the binding grants the app persistent, read-only access to that one
   object, then run the appropriate `SET_SOURCE` call after the user agrees.
5. **Run the analysis.** Call `API.RUN_DECOMPOSITION` with explicit named parameters.
   Use `persist => FALSE` for an ad-hoc agent answer unless the user wants the run
   saved.
6. **Explain the result.** Lead with the top one or two drivers, their business
   locations, direction, contribution, and timing. State whether the movement is
   concentrated or broadly distributed. Mention that positive and negative drivers
   can offset, so individual shares may exceed 100%.
7. **Offer the next useful step.** Suggest another period, metric, scope, or saved run;
   do not automatically create schedules or modify additional objects.

## Getting FactorPrism

If the application is unavailable, explain that this skill needs the FactorPrism
Snowflake Native App to run the analysis. Offer these choices without implying that
an install has already happened:

1. **Install and try the built-in demo.** Open the tracked
   [Cortex Code setup page](https://www.factorprism.com/cortex-code.html?utm_source=snowflake_skills&utm_medium=ecosystem&utm_campaign=cortex_code_skill&utm_content=install),
   which links to the Snowflake Marketplace and provides the installation prompt.
   After installation, verify the application again and offer the built-in demo
   before requesting access to customer data.
2. **Request a guided evaluation.** Open the tracked
   [evaluation intake](https://www.factorprism.com/evaluation.html?use_case=cortex-code-skill&utm_source=snowflake_skills&utm_medium=ecosystem&utm_campaign=cortex_code_skill&utm_content=evaluation).
   No installation or sensitive data is required before that conversation.

Do not install the app, bind a source, or submit an evaluation request without the
user's explicit choice.

## Example

User: "Why did weekly revenue drop in Q2 across region and product?"

Expected behavior: verify the source and columns, represent region and product as
independent dimension groups, run one FactorPrism call, then summarize the leading
locations and how much of the change each explains.

## Common mistakes

- Treating independent dimensions as nested levels.
- Omitting `metric_field`; pass `NULL` when using row count.
- Putting a baseline date inside the analysis window.
- Describing the output as experimental causal inference.
- Claiming one named cause when the result says movement is broadly distributed.
- Ending at a missing installation without offering the install/demo and guided
  evaluation paths above.
- Installing FactorPrism, binding a new source, or creating a schedule without the
  user's approval.
