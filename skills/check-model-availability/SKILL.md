---
name: check-model-availability
title: Check Model Availability
summary: Verify whether a Cortex model is callable in base mode (AI SQL) and agentic mode (the Cortex Code allow-list), with name-variant fallback.
description: |
  Determines, for one or more Cortex models, whether each is usable in base mode
  (AI SQL: AI_COMPLETE / SNOWFLAKE.CORTEX.COMPLETE) and in agentic mode (the Cortex
  Code / CoCo Agent curated allow-list). These are two independent gates that use
  different name spellings — a model can be GA in base mode yet rejected agentically,
  and vice-versa. The skill reads SHOW CORTEX BASE MODELS for authoritative names,
  lifecycle, and regions; probes base mode with a real AI_COMPLETE call; harvests the
  full agentic allow-list in a single call; retries plausible name variants before
  declaring a model unavailable; and reports a base/agentic matrix.

  Triggers: check if we have access to a model, are there any new LLM models, any new
  versions of GPT/Opus/Sonnet/GLM/grok/gemini, test a model to see that it works, is
  this model available agentically, can I benchmark this model in base and agentic mode.

  Do NOT use for: enabling model access via RBAC/allowlist grants (that is a governance
  task), Cortex Search or Analyst model selection, or fine-tuned/custom model registration.
prompt: Check whether the new Cortex models are available in base and agentic mode.
language: en
status: Published
author: Chanin Nantasenamat, Lead Developer Advocate, OSS
type: snowflake
tools:
  - snowflake_sql_execute
  - Bash
  - ask_user_question
---

## Overview

Cortex exposes models through two independent surfaces, each with its own gate and its
own name spelling:

- **Base mode** — AI SQL functions `AI_COMPLETE` / `SNOWFLAKE.CORTEX.COMPLETE`. Names are
  UPPERCASE and sometimes carry a `1P-` provider prefix (e.g. `OPENAI-1P-GPT-5.6-SOL`).
- **Agentic mode** — the curated allow-list enforced by the Cortex Code Agent. Names are
  lowercase and often drop the `1p-` prefix (`openai-gpt-5.6-sol`).

Base-callable does **not** imply agentic-callable. Presence in `SHOW CORTEX BASE MODELS`
does not even guarantee base-callable — some listed models return `unknown model` from
`AI_COMPLETE`. Always confirm with a live probe.

## When to Use

- "Do we have access to `<model>`?" / "Is `<model>` available?"
- "Are there any new LLM models or new versions of GPT / Opus / Sonnet / GLM / grok / gemini?"
- "Test `<model>` to see that it works."
- "Can I benchmark `<model>` in base and agentic mode?"

## When NOT to Use

| Topic | Delegate to |
|---|---|
| Granting model access via RBAC / allow-list | a governance / access skill |
| Choosing a model for Cortex Search or Analyst | those product skills |
| Registering a fine-tuned or custom model | a model-registry / ML skill |

## Workflow

1. **List authoritative base names, lifecycle, and regions** — Run
   `SHOW CORTEX BASE MODELS;` then filter with
   `SELECT "name","lifecycle_status","in_region_availability","cross_region_availability","legacy_date","eol_date" FROM TABLE(RESULT_SCAN(LAST_QUERY_ID())) WHERE "name" ILIKE '%<pattern>%';`
   Interpret `lifecycle_status`: `GA` = usable; `PUPR`/`PRPR` = preview, usable if the probe
   succeeds; `LEGACY` = deprecated but callable until `eol_date` (flag it); `EOL` = not
   callable; `INTERNAL`/`None` = probe to confirm. `in_region_availability: []` with a
   populated `cross_region_availability` means it needs cross-region inference enabled.

2. **Confirm base mode with a real call** — Set a warehouse only if none is active, then
   `SELECT AI_COMPLETE('<base-model-name>', 'Reply with the single word: ok') AS resp;`
   A text reply means base-capable. `unknown model` / `not authorized` /
   `not available in region` means not base-capable — record the exact message.

3. **Harvest the agentic allow-list in one shot** — Run ONE probe with a bogus model name;
   the CLI error dumps every allowed agentic model:
   `cortex exec -m __definitely_not_a_model__ --no-history --max-turns 1 "Reply with exactly: ok" 2>&1 | tail -20`
   The error body contains `- Available models: <comma-separated lowercase list>`. That
   list **is** the allow-list. Match your targets against it — this avoids probing each
   model separately and spending agent turns.

4. **Confirm a specific target agentically** — For a target you want verified end-to-end:
   `cortex exec -m <agentic-model-name> --no-history --max-turns 3 "Reply with exactly the word ok and nothing else. Do not call any tools." 2>&1 | tail -4`
   A short reply → agentic-capable. `is not authorized or not available in your region` or
   `is not an allowed model` → not agentic. `Max tool call iterations reached` → the model
   works but hit the turn cap; treat as capable.

5. **Apply name-variant fallback** — Before declaring a model unavailable, retry variants:
   drop/add the `1p-` prefix (`openai-1p-gpt-5.6-sol` ↔ `openai-gpt-5.6-sol`); normalize
   case (base UPPERCASE vs agentic lowercase); try sibling suffixes (`-sol`, `-terra`,
   `-luna`); and try version-spelling variants (`opus-4-8` vs `opus-4.8`, family vs
   `-mini`/`-nano`).

6. **Report the matrix** — One row per model: **Model | Base? | Agentic? | Lifecycle |
   Notes**. State explicitly whether the user can do base-only or full base + agentic
   benchmarking, and flag any `LEGACY`/`EOL` models with their dates.

## Common Mistakes

- **Trusting `SHOW` over a probe** — A model can appear in `SHOW CORTEX BASE MODELS` and
  still return `unknown model` from `AI_COMPLETE`. The live call is the source of truth.
- **Assuming base implies agentic** — They are separate allow-lists that drift
  independently. Always harvest the agentic list fresh (Step 3); never cache it.
- **Comparing raw names** — Base is UPPERCASE with an optional `1P-` prefix; agentic is
  lowercase without it. Normalize before matching or you will report false negatives.
- **Probing every model agentically** — Wastes agent turns. Harvest the whole list once,
  then only probe the specific targets that matter.

## Examples

**Scan for new models across families:**
> "Are there any new LLM models or new versions for GPT, GLM, Opus, Sonnet?"

Expected output: a base/agentic matrix grouped by family, highlighting agentic-only models
(e.g. `glm-5.2`), base-only models, and EOL/LEGACY models to avoid.

**Confirm one model both ways:**
> "Check if we have access to gpt-5.6 and that it works."

Expected output: `SHOW` lifecycle for `OPENAI-1P-GPT-5.6-*`, a base `AI_COMPLETE` probe,
an agentic `cortex exec` probe on `openai-gpt-5.6-sol`, and a verdict.

**Pre-benchmark check:**
> "Which of these can I benchmark in both base and agentic mode?"

Expected output: per-model Base?/Agentic? columns and an explicit base-only vs full
base+agentic recommendation.

## Stopping Points

⚠️ **STOPPING POINT** — If a base call needs a warehouse and none is active, ask which
warehouse to use before probing.

⚠️ **STOPPING POINT** — If the user cares about only one surface (base OR agentic), skip
the other probe and say so, to avoid spending agent turns unnecessarily.
