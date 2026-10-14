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

  Triggers: check if we have access to a model, can I use model X in AI_COMPLETE or in
  CoCo, are there any new LLM models, any new versions of GPT/Opus/Sonnet/grok/gemini,
  test a model to see that it works, is this model available agentically, can I benchmark
  this model in base and agentic mode.

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
  UPPERCASE and sometimes carry a `1P-` provider prefix (e.g. `<PROVIDER>-1P-<MODEL-NAME>`).
- **Agentic mode** — the curated allow-list enforced by the Cortex Code Agent. Names are
  lowercase and often drop the `1p-` prefix (e.g. `openai-gpt-5.6-sol`). Availability is gated by
  the account allowlist (`CORTEX_MODELS_ALLOWLIST`), the region, and the cross-region setting
  (`CORTEX_ENABLED_CROSS_REGION`). The CoCo docs publish a supported-models list (see
  References), but it lags the live set — the docs themselves say the interactive `/model`
  command / model picker is the always-current, authoritative source.

Base-callable does **not** imply agentic-callable. Presence in `SHOW CORTEX BASE MODELS`
does not even guarantee base-callable — some listed models return `unknown model` from
`AI_COMPLETE`. Always confirm with a live probe.

## When to Use

- "Do we have access to `<model>`?" / "Is `<model>` available?"
- "Can I use `<model>` in AI_COMPLETE or in CoCo?"
- "Are there any new LLM models or new versions of GPT / Opus / Sonnet / grok / gemini?"
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
   succeeds; `LEGACY` = account-usage-dependent — an account that **already called the model
   before its `legacy_date`** keeps access until `eol_date`, but an account that **never used
   it can no longer start** (calls fail), so a legacy model is often *not* addable to a new
   benchmark — the Step 2 probe is the source of truth; `EOL` = unavailable to every account
   regardless of prior use; `None` (NULL) = probe to confirm. `in_region_availability: []`
   with a populated `cross_region_availability` means the model runs only via cross-region
   inference — it fails unless the account has cross-region inference enabled (some models,
   e.g. `gemini-3.1-pro` and `openai-gpt-5-mini`, are cross-region only). See the docs
   reference below for authoritative lifecycle definitions and the per-region matrix.

   ⚠️ **STOP** — Before Step 2, list the models you plan to probe, tell the user that each
   `AI_COMPLETE` call consumes credits, and get their OK before running any.

2. **Confirm base mode with a real call** — Set a warehouse only if none is active, then
   `SELECT AI_COMPLETE('<base-model-name>', 'Reply with the single word: ok') AS resp;`
   A text reply means base-capable. `unknown model` / `not authorized` /
   `not available in region` means not base-capable — record the exact message.

3. **Harvest the agentic allow-list in one shot** — Run ONE probe with a bogus model name;
   the CLI error dumps every allowed agentic model:
   `cortex exec -c <active-connection-name> -m __definitely_not_a_model__ --no-history --max-turns 1 "Reply with exactly: ok" 2>&1 | tail -20`
   Use the same connection as the SQL probes in Steps 1–2 (the session's active connection), so
   base and agentic results come from the same account.
   The error body contains `- Available models: <comma-separated lowercase list>`. That
   list **is** the allow-list (equivalent to the interactive `/model` command the CoCo docs
   call authoritative), and it also echoes the two gating controls — `Model allowlist:`
   (`CORTEX_MODELS_ALLOWLIST`) and `Cross-region setting:` (`CORTEX_ENABLED_CROSS_REGION`).
   Match your targets against it — this avoids probing each model separately and spending
   agent turns, and reflects models newer than the published docs list.

   ⚠️ **STOP** — Before Step 4, list the models you plan to run as child agents, tell the user
   that each run consumes credits, and get their OK before running any.

4. **Confirm a specific target agentically** — For a target you want verified end-to-end:
   `cortex exec -c <active-connection-name> -m <agentic-model-name> --allowed Read --no-history --max-turns 3 "Reply with exactly the word ok and nothing else. Do not call any tools." 2>&1 | tail -4`
   `--allowed Read` blocks every tool except read-only file reads for the child run.
   A short reply → agentic-capable. `is not authorized or not available in your region` or
   `is not an allowed model` → not agentic. `Max tool call iterations reached` → the model
   works but hit the turn cap; treat as capable.

5. **Apply name-variant fallback** — Before declaring a model unavailable, retry variants:
   drop/add the `1p-` prefix (`<provider>-1p-<model-name>` ↔ `<provider>-<model-name>`); normalize
   case (base UPPERCASE vs agentic lowercase); try sibling suffixes (`-sol`, `-terra`,
   `-luna`); and try version-spelling variants (`opus-4-8` vs `opus-4.8`, family vs
   `-mini`/`-nano`).

6. **Report the matrix** — One row per model: **Model | Base? | Agentic? | Lifecycle |
   Notes**. State explicitly whether the user can do base-only or full base + agentic
   benchmarking. Flag `EOL` models as unusable, and for `LEGACY` models note that access
   is account-usage-dependent (usable only if the account already called the model before
   its `legacy_date`) and include `legacy_date`/`eol_date`.

## Common Mistakes

- **Trusting `SHOW` over a probe** — A model can appear in `SHOW CORTEX BASE MODELS` and
  still return `unknown model` from `AI_COMPLETE`. The live call is the source of truth.
- **Misreading `LEGACY` as "still callable for everyone"** — Legacy access is per-account:
  an account that never used the model before its `legacy_date` **cannot start** and its
  calls fail. Do not assume a legacy model is addable to a new benchmark — probe it.
- **Assuming base implies agentic** — They are separate allow-lists that drift
  independently. Always harvest the agentic list fresh (Step 3); never cache it.
- **Comparing raw names** — Base is UPPERCASE with an optional `1P-` prefix; agentic is
  lowercase without it. Normalize before matching or you will report false negatives.
- **Ignoring cross-region-only models** — A model with empty `in_region_availability` but a
  populated `cross_region_availability` fails unless cross-region inference is enabled.
- **Probing every model agentically** — Wastes agent turns. Harvest the whole list once,
  then only probe the specific targets that matter.

## Examples

**Scan for new models across families:**
> "Are there any new LLM models or new versions for GPT, Opus, Sonnet?"

Expected output: a base/agentic matrix grouped by family, highlighting agentic-only models,
base-only models, and EOL/LEGACY models to avoid.

**Confirm one model both ways:**
> "Check if we have access to gpt-5.6 and that it works."

Expected output: `SHOW` lifecycle for the matching GPT-5.6 names, a base `AI_COMPLETE` probe,
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

## References

### Base mode (AI SQL)
- **Models and regional availability for Cortex AI Functions** —
  <https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-regional-availability>
  Authoritative companion to `SHOW CORTEX BASE MODELS`: per-region in-region vs cross-region
  matrix, context-window / max-output-token limits, and the model-lifecycle definitions.

### Agentic mode (Cortex Code / CoCo)
- **CoCo CLI — Supported models + Cloud regions** —
  <https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-cli>
  Published supported-models list and a cross-region availability table (model ×
  `CORTEX_ENABLED_CROSS_REGION` scope). Defers to the in-session `/model` command for the
  full, current list — treat the live list (Step 3 probe) as authoritative when they differ.
- **CoCo Desktop — Supported models** —
  <https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-desktop>
  States the model picker is the "authoritative, always-current list"; availability depends
  on region, `CORTEX_MODELS_ALLOWLIST`, and cross-region setting.
- **Configure model settings (default orchestration model)** —
  <https://docs.snowflake.com/en/user-guide/cortex-code/configure-model-settings>
- **CoCo in Snowsight — model selection & cross-region** —
  <https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-snowsight>

### Lifecycle & cross-region
- **Lifecycle (from the docs):** Snowflake retires models in two stages —
  - `LEGACY`: after the `legacy_date`, an account that used the model before that date keeps
    access until `eol_date`; an account that never used it can no longer start (calls fail).
  - `EOL`: after the `eol_date`, the model is unavailable to every account regardless of
    prior use.
  `SHOW CORTEX BASE MODELS` reports `lifecycle_status`, `legacy_date`, `eol_date`.
- **Cross-region inference** —
  <https://docs.snowflake.com/en/user-guide/snowflake-cortex/cross-region-inference>
  Required for models not available in the account's region (empty `in_region_availability`);
  also required for CoCo to reach models outside the account's region. Set via
  `ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = '<scope>'` (e.g. `AWS_GLOBAL`,
  `AZURE_GLOBAL`, `ANY_REGION`). Do NOT run this yourself: it is an account-level change that
  requires ACCOUNTADMIN. Tell the user it is needed and let them or their admin decide.
