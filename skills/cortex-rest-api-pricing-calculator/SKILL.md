---
id: cortex-rest-api-pricing-calculator
name: cortex-rest-api-pricing-calculator
title: Cortex REST API Pricing
summary: Calculate monthly/annual Cortex REST API costs for Claude, GPT, DeepSeek, Mistral, Llama models.
description: >-
  Calculate Snowflake Cortex REST API costs for customers. This is specifically for the Cortex REST API
  (token-based LLM inference endpoint), NOT Cortex AI SQL functions. Estimate monthly/annual token-based
  pricing for Claude, GPT, DeepSeek, Mistral, Llama models. Supports prompt caching (Table 6b) and
  non-caching (Table 6c) rates. Use when: pricing estimate, cost calculator, REST API cost, token pricing,
  cortex REST pricing, how much will cortex REST API cost, annual commit, monthly cost estimate, credit
  consumption table. Do NOT use for Cortex AI SQL functions (COMPLETE, EXTRACT, SENTIMENT) which are
  credit-based, not token-based.
tools:
  - Read
prompt: "$cortex-rest-api-pricing-calculator estimate monthly cost for claude-sonnet-4-5 at 300M input and 130M output tokens"
language: en
status: stable
authors:
  - Navnit Shukla
categories:
  - pricing
  - cortex
type: snowflake
---

# Cortex REST API Pricing Calculator

## Scope

This skill is ONLY for **Cortex REST API** pricing — the token-based LLM inference endpoint (POST /api/v2/cortex/inference:complete). It is NOT for:
- Cortex AI SQL functions (COMPLETE, EXTRACT, SENTIMENT, etc.) — those are credit-based, not token-based
- Cortex Search, Cortex Analyst, or other Cortex services

# When to Use

- User asks for Cortex REST API cost estimates
- User wants to calculate token-based pricing for a customer using the REST endpoint
- User mentions models like Claude, GPT, DeepSeek, Mistral, Llama in a REST API pricing context
- User wants monthly/annual commit projections for REST API usage
- User asks about prompt caching cost savings on the REST API

# Instructions

## Step 1: Determine Mode

**Ask** user what they need:

1. **Quick Estimate** — Calculate costs conversationally right here
2. **Interactive App** — Open the full Streamlit calculator with editable rates, PDF viewer, and Excel export

**If Quick Estimate** → Continue to Step 2
**If Interactive App** → Jump to Step 5

## Step 2: Gather Usage Parameters

**Ask** user for:
- Model name(s) (reference `references/pricing-rates.md` for available models)
- Monthly token volumes (in millions):
  - Input tokens (M)
  - Output tokens (M)
  - Cache Read tokens (M) — only for Table 6b models
  - Cache Write tokens (M) — only for Table 6b models
- Discount percentage (if any, contract-dependent)

**⚠️ STOPPING POINT:** Confirm parameters with user before calculating.

## Step 3: Calculate Costs

**Load** `references/pricing-rates.md` for current rates.

**Formula** (per model, per million tokens):
```
input_cost       = input_M × input_rate
cache_write_cost = cache_write_M × cache_write_rate
cache_read_cost  = cache_read_M × cache_read_rate
output_cost      = output_M × output_rate
subtotal         = input_cost + cache_write_cost + cache_read_cost + output_cost
```

**Summary:**
```
baseline_monthly = sum of all model subtotals
discount_amount  = baseline_monthly × (discount_pct / 100)
monthly_after_discount = baseline_monthly - discount_amount
annual_commit    = monthly_after_discount × 12
```

## Step 4: Present Results

Present a clear table with:
- Per-model breakdown (tokens × rate = cost for each token type)
- Subtotal per model
- Baseline monthly total
- Discount applied
- Monthly after discount
- Annual commit (12 months)

**Add caveat:** "Discounts are contract-dependent. Final calculations visible only upon invoicing."

**Include source link:** [Snowflake Credit Consumption Table (PDF)](https://www.snowflake.com/legal-files/CreditConsumptionTable.pdf) — refer to Table 6(b) and 6(c) for REST API rates.

**Done.** Ask if user wants to adjust parameters or open the interactive app.

## Step 5: Interactive App

**Direct the user to the published Streamlit app on Snowhouse:**

https://app.snowflake.com/SFCOGSOPS/snowhouse_aws_us_west_2/#/streamlit-apps/TEMP.NASHUKLA.CORTEX_REST_API_PRICE_CALCULATOR

Features of the interactive app:
- Editable pricing table (add/modify rates for new models)
- Embedded PDF viewer for Snowflake Credit Consumption Table
- Monthly usage entry with model selector
- Cost breakdown per model with calculation details
- Discount input with summary metrics
- Excel export (Cost Breakdown + Summary + Pricing Rates sheets)

## Best Practices

- Regional rates are 1.1× Global rates (10% premium)
- Cache Write rate is typically 1.25× the Input rate
- Cache Read rate is typically 0.1× the Input rate (90% savings vs input)
- Opus-tier models are ~5× Sonnet-tier pricing
- Haiku-tier models are ~0.33× Sonnet-tier pricing
- All rates are per 1M tokens in USD
- Table 6(b): REST API with Prompt Caching — supports input, cache_write, cache_read, output
- Table 6(c): REST API without Prompt Caching — input and output only

# Stopping Points

- ✋ After Step 2 — confirm usage parameters before calculating
- ✋ After Step 4 — offer adjustments or app link

**Resume rule:** Upon user approval, proceed directly to next step without re-asking.

# Output

A formatted cost breakdown table with monthly and annual totals, plus a link to the source PDF and interactive app.

# Examples

## Example 1: Quick estimate without caching
User: $cortex-rest-api-pricing-calculator how much for claude-sonnet-4-6 at 300M input and 130M output monthly?
Assistant: Calculates using Global rates: (300 × $3.00) + (130 × $15.00) = $900 + $1,950 = $2,850/month, $34,200/year

## Example 2: With prompt caching
User: $cortex-rest-api-pricing-calculator estimate claude-sonnet-4-5 with 300M input, 130M output, 20000M cache read, 4000M cache write
Assistant: Calculates: input $900 + cache write $15,000 + cache read $6,000 + output $1,950 = $23,850/month, $286,200/year
