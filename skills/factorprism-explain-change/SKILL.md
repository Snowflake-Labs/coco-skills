---
name: factorprism-explain-change
title: Explain a Metric Change
summary: Use an installed FactorPrism app to find which segments drove a metric change and by how much, from SQL in Cortex Code.
description: |
  Use when FactorPrism (Snowflake Native App) is already installed in the account and the user asks why revenue, margin, cost, churn, units, claims, denials, or another business metric changed; which region, product, payer, segment, or combination drove it; how much each driver contributed; or wants a scheduled check that flags unusual moves. Runs the app's API.RUN_DECOMPOSITION and API.RUN_VERDICT procedures and explains the result. Triggers: why did this metric move, what drove the change, explain this drop, explain this spike, revenue variance, margin variance, budget vs actual, driver analysis, root cause of a KPI change, factorprism. Do not use when FactorPrism is not installed, or for forecasting, anomaly detection on raw events, marketing multi-touch attribution, or data outside Snowflake.
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Read
prompt: Use FactorPrism to explain why weekly revenue dropped in Q2 across region and product.
language: en
status: Published
author: David Rimshnick
type: partner
---

# Explain a Metric Change with FactorPrism

## Overview

This skill drives the **FactorPrism** Snowflake Native App, which must already be
installed in the account. FactorPrism answers: *why did this business metric move,
and where in the business did the change originate?* It returns ranked factors,
each placed where it acts (a region, a product, a product within one region, or the
whole business), with contributions that add up to the observed change. The
analysis runs inside the account, on the user's warehouse, against one table or
view the user has bound to the app with read-only access.

Read [references/api.md](references/api.md) before constructing any call.

## Workflow

1. **Find the app.** Run `SHOW APPLICATIONS LIKE '%FACTORPRISM%';` and use the
   returned name in place of `FACTORPRISM` in every call. If nothing is returned,
   tell the user this skill needs the FactorPrism app installed and stop. Do not
   install anything.
2. **Confirm the question.** Get the metric (or row count), the date column, the
   window to explain, the time grain, and the business dimensions. Ask whether
   dimensions are independent (region, product) or nested broad-to-narrow (region,
   then state).
3. **Inspect the source.** Use object search or `DESCRIBE` to confirm column names
   and types. Never pull row-level data into the chat.
4. **Bind only when needed.** If the table or view is not already bound, explain
   that binding gives the app persistent read-only access to that one object, and
   run `API.SET_SOURCE` only after the user agrees.
5. **Run once.** Call `API.RUN_DECOMPOSITION` with named parameters. Use
   `persist => FALSE` for an ad-hoc answer unless the user wants the run saved.
6. **Explain.** Lead with the top one or two factors: where each acts, direction,
   contribution per period, share of the net change, and timing (`PATTERN`).
   Say whether the movement is concentrated in a few factors or spread out. Gains
   and declines can offset, so one share can exceed 100%.
7. **Offer one next step.** Another window, metric, or scope; or a scheduled check
   with `API.RUN_VERDICT` (see the reference). Do not create tasks, schedules, or
   new bindings without the user's approval.

## Examples

**Ad-hoc question**

User: "Use FactorPrism to explain why weekly revenue dropped in Q2 across region
and product."

Expected behavior: find the app, confirm `SOURCE_VIEW` is bound to the sales
view, check the columns, call `RUN_DECOMPOSITION` with
`hierarchies => PARSE_JSON('[["REGION","STATE"],["PRODUCT"]]')`, `rollup => 'WEEK'`,
and the Q2 dates, then summarize, for example: "Most of the drop sits in the
West region (about -42,000 a week, 61% of the net change), starting in May; a
smaller decline in Product B within Texas adds most of the rest."

**Scheduled check**

User: "Tell me each Monday if weekly revenue moved more than usual, and why."

Expected behavior: show `CALL FACTORPRISM.API.RUN_VERDICT();` and the
`RESULT_SCAN` filter on `MODE NOT IN ('steady','flat')`, explain the columns, and
offer to wrap it in a Snowflake task only if the user confirms the warehouse and
schedule.

## Common mistakes

- Treating independent dimensions as nested levels, or the reverse.
- Omitting `metric_field` when counting rows; pass `NULL` with `use_row_count => TRUE`.
- Putting a baseline date inside the analysis window. Every baseline date must be
  before `period_start`.
- Reading `SHARE_OF_NET_CHANGE_PCT` when it is `NULL`. The metric was roughly
  flat while its mix shifted; rank by `AVG_MOVEMENT_PER_PERIOD` instead.
- Naming one cause when the result says the movement is spread out, or treating a
  `Diffuse` row as a single segment.
- Ignoring `COLLINEAR_FACTORS`: when it is non-empty, the listed locations explain
  the movement equally well and the data cannot tell them apart. Say so.
- Describing results as proof that an intervention caused the change. They locate
  and size where the movement originated.
