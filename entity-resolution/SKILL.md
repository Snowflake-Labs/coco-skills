---
name: entity-resolution
title: Resolve Entities
summary: End-to-end entity resolution pipeline using Snowflake Cortex AI Functions to match, link, and dedupe records.
description: Use when you need to match records across datasets, deduplicate within a dataset, build a golden record, or link source records to a reference corpus. Orchestrates profiling, normalization, blocking, multi-tier matching (deterministic, fuzzy, AI-judged, agentic, contrastive), human review, and operationalization via dynamic tables. Industry-agnostic with optional domain profiles for pharma, financial services, retail/CPG, healthcare, and insurance. Triggers: entity resolution, record matching, deduplication, record linkage, fuzzy matching, golden record, master data, MDM, merge records, match entities, link records, dedupe, duplicate detection, identity resolution.
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: Help me resolve entities across my customer and prospect tables.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Resolve Entities

## Overview

Determine whether records refer to the same real-world entity. This skill orchestrates Cortex AI Functions, dynamic tables, Streamlit, and optionally Cortex Agents and Snowflake ML through a structured pipeline: profile → normalize → block → match → score → review → operationalize.

Two workflow paths:

- **Path A — Pair-Based Matching (Deduplication / Cross-Match):** generate candidate pairs via blocking, score through 3 tiers.
- **Path B — Agentic Matching (Entity Linking):** resolve source records against a reference corpus via tiered escalation.

Optional add-on:

- **Contrastive Embeddings:** train a domain-adapted encoder via SupConLoss when labeled data and GPU compute are available.

## When to Use

- Match or link records across datasets
- Deduplicate records within a single dataset
- Build a golden record from multiple sources
- Assess match-readiness of source data
- Operationalize an ongoing matching pipeline

For unstructured inputs (PDFs, scans), call `cortex-ai-functions` (`AI_EXTRACT`, `AI_PARSE_DOCUMENT`) first, then feed structured output here.

## Domain Profiles

Load the matching profile from `references/profiles/`:

| Keywords | Profile | Tier 1 IDs |
|---|---|---|
| pharma, NPI, DEA, NCPDP | `pharma.md` | NPI, DEA, NCPDP |
| bank, KYC, AML, LEI | `financial-services.md` | LEI, DUNS, Tax ID, CRD |
| retail, CPG, GTIN, UPC | `retail-cpg.md` | GTIN/UPC, GLN, Supplier ID |
| provider, hospital, taxonomy | `healthcare-provider.md` | NPI, Taxonomy Code |
| insurance, payer, NAIC | `insurance-payer.md` | NAIC, CMS Payer ID, Plan ID |
| *(none)* | `generic.md` | Name + address only |

## Workflow

### Step 0: Discovery

Use `ask_user_question` to collect: goal (dedupe / cross-match / link to reference / profile-only), domain, source and reference tables, volume, output schema, HITL preference, pipeline cadence (one-time vs ongoing), data language, and approach (recommended / specific / benchmark multiple). Present a discovery summary.

⚠️ STOPPING POINT: User confirms the summary before any work begins.

### Step 1: Profiling

Delegate to `profiling-tables`. ER-specific checks:

1. Identifier detection from the loaded domain profile (Tier 1 candidates)
2. Name/address column detection via `AI_CLASSIFY`
3. Completeness, format consistency, duplicate density, volume

⚠️ STOPPING POINT: Present profiling report and match-readiness checklist.

### Step 1b: Cost Estimation

Load `references/templates/cost-estimator.md`. Estimate based on volume, path, expected tier distribution, and warehouse sizing.

⚠️ STOPPING POINT: User acknowledges and accepts the estimate.

### Step 2: Normalization

Delegate to `cortex-ai-functions` for `AI_EXTRACT` (address parsing) and `AI_COMPLETE` (edge-case names). Load `references/templates/normalization.md`. Materialize `normalized_entities` with `source_id`, normalized fields, raw originals, and `blocking_key`.

⚠️ STOPPING POINT: Validate 10–20 sample rows, NULL counts per normalized field, and identifier format compliance.

### Step 3: Blocking

Load `references/templates/blocking.md`. Reduce O(n²) pair space via blocking keys (geographic, category, phonetic, or ID prefix). Self-join within blocks: `a.source_id < b.source_id`.

⚠️ STOPPING POINT: Review blocking statistics. Red flags: any block > 100K pairs, reduction ratio > 10%, or very few pairs.

### Step 4: Multi-Tier Matching (Path A)

Load `references/templates/matching.md`.

- **Tier 1 — Deterministic:** exact match on authoritative IDs. Pure SQL, no AI cost.
- **Tier 2 — Fuzzy:** `AI_EMBED` (`snowflake-arctic-embed-l-v2.0`) + `VECTOR_COSINE_SIMILARITY`, supplemented by `JAROWINKLER_SIMILARITY` on name/street. Starting thresholds: ≥ 0.92 match, ≥ 0.80 probable_match, < 0.80 no_match.
- **Tier 3 — AI-Judged:** `AI_CLASSIFY` on Tier 2 `probable_match` rows only (cost control).

Resolve transitive matches and assign `entity_group_id` to connected components.

⚠️ STOPPING POINT: Review counts by tier and decision, sample rows, and threshold tuning (adjust in 0.02–0.03 increments against a labeled sample).

### Step 4b: Agentic Matching (Path B)

Load `references/templates/agentic-matching.md`. Prerequisites: normalized entities, embeddings on source and reference, top-N candidates, Cortex Search Service over the reference corpus (`references/templates/search-service.md`), and a semantic model YAML.

- **Tier 1 — High-Confidence Triage:** cosine + Jaro-Winkler with domain guards (chain stores, multi-tenant buildings, address floor).
- **Tier 1.5 — Search + Classify:** Cortex Search top-N then `AI_COMPLETE` (cost-effective model). Confidence ≥ 0.80 + name/address alignment.
- **Tier 2 — Cortex Agent:** 3 tools — `cortex_search` (primary), `cortex_analyst_text_to_sql` (fallback), `web_search` (last resort). Budget: 6 tool calls, 90s, 16K tokens per entity. See `references/templates/agent-definition.md` and `references/templates/orchestration.md`. Delegate to `cortex-agent`.

UNION ALL all tiers into a crosswalk with tier attribution. Records entities confirmed active but missing from the reference corpus as discoveries.

⚠️ STOPPING POINT: Review per-tier match/closure/discovery distributions and web search usage.

### Step 4c: Contrastive Embeddings (Standalone or Tier 2 Replacement)

Load `references/templates/contrastive-embeddings.md`. Prerequisites: ≥ 500 labeled entities across ≥ 200 clusters, GPU pool (`GPU_NV_S`), `PYPI_EAI` and `HF_EAI` external access integrations.

1. **Model selection:** English-only → `roberta-base` (NER off); multilingual → `xlm-roberta-base` (NER on); resource-constrained → `all-MiniLM-L6-v2`.
2. **Serialize** entities with `[COL]/[VAL]` tokens; derive cluster IDs via Union-Find.
3. **Train** via stored procedure on the GPU pool — delegate to `machine-learning` for setup and monitoring.
4. **Block** by cosine ≥ 0.50, run threshold sweep against ground truth, materialize matches at optimal F1.
5. **Add-on mode:** replace `AI_EMBED` in Tier 2 of Path A; use match/no-match thresholds with escalation to Tier 3 in between.

⚠️ STOPPING POINT: Review threshold sweep, optimal F1/precision/recall, and (if add-on) comparison with `AI_EMBED` Tier 2.

### Step 5: Human-in-the-Loop Review

Delegate to `developing-with-streamlit`. Load `references/hitl-app.md`. App requirements:

- Side-by-side source vs. resolved entity with field-level match indicators
- Accept / reject / flag with optional comment
- Sequential nav, progress tracking
- Decisions persisted to `REVIEW_DECISIONS`
- Material Design CSS, no emojis, no third-party MUI

⚠️ STOPPING POINT: Deploy app and wait for the review cycle to complete.

### Step 6: Operationalize

Load `references/operationalize.md`.

1. **Dynamic tables pipeline** — delegate to `dynamic-tables` (normalize → block → match cascade)
2. **Entity master table** — golden record view aggregating best values per `entity_group_id`
3. **Source quality monitoring** — delegate to `data-quality`

Extensions: `cortex-agent` for NL queries over match results; `machine-learning` for a custom classifier replacing or augmenting Tier 2/3.

⚠️ STOPPING POINT: User approves pipeline design before creating dynamic tables.

## Benchmark Mode

When the user selects benchmark in Step 0, run each chosen approach on the same labeled sample (200–500 stratified pairs; if absent, deploy a lightweight HITL labeling app). Produce a comparison table of precision, recall, F1, cost ($), and latency (sec). Then ask which approach to use for the full run.

## Common Mistakes

- **Skipping profiling** — jumping straight to matching without identifying authoritative IDs over-relies on fuzzy tiers and inflates cost.
- **Coarse blocking** — any block > 100K pairs explodes the comparison space; tighten keys.
- **Skipping Tier 1** — deterministic ID matches are free and high-precision; always run them first.
- **Sending all pairs to Tier 3** — `AI_CLASSIFY` should only see Tier 2 `probable_match` rows. `match` and `no_match` are already decided.
- **Hardcoded thresholds** — 0.92 / 0.80 are starting points. Tune in 0.02–0.03 steps against a labeled sample.
- **No transitive resolution** — A=B and B=C implies A=C; missing this fragments entity groups.
- **Cosine without name check** — high cosine on short text can match unrelated entities. Supplement with `JAROWINKLER_SIMILARITY` on names and streets.
- **Unbudgeted agents** — Cortex Agents can recurse expensively. Enforce tool-call, token, and wall-clock limits per entity.
- **Contrastive without ground truth** — fewer than ~500 labeled entities across ~200 clusters yields unstable embeddings; fall back to `AI_EMBED`.
- **Reusing thresholds across domains** — pharma name matching is not retail product matching; recalibrate per domain profile.

## Stopping Points

- Step 0 — confirm discovery summary
- Step 1 — confirm profiling and match-readiness
- Step 1b — accept cost estimate
- Step 2 — validate sample normalization
- Step 3 — confirm blocking statistics
- Step 4 — review match results and thresholds (or benchmark comparison)
- Step 4b — review per-tier agentic results
- Step 4c — review contrastive threshold sweep
- Step 5 — wait for HITL review completion
- Step 6 — approve pipeline design before operationalizing

## Output

- Discovery summary, profiling report, normalized entities table
- Candidate pairs with blocking diagnostics (Path A) or top-N candidates (Path B)
- Match results with confidence, tier attribution, `entity_group_id`
- Crosswalk and entity discoveries (Path B)
- Contrastive embeddings table and threshold sweep (Step 4c)
- Benchmark comparison report (benchmark mode)
- Streamlit review app (if HITL selected)
- Dynamic tables pipeline and entity master table (if ongoing)

## References

See `references/profiles/` for domain profiles and `references/templates/` for SQL patterns: `normalization.md`, `blocking.md`, `matching.md`, `agentic-matching.md`, `search-service.md`, `agent-definition.md`, `orchestration.md`, `contrastive-embeddings.md`, `cost-estimator.md`, `incremental.md`. App spec: `references/hitl-app.md`. Operationalization: `references/operationalize.md`.
