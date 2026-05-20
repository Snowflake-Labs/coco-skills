---
name: entity-resolution
title: Entity Resolution
summary: Match, deduplicate, and link records using Snowflake Cortex AI functions with a profile-normalize-block-match-review-operationalize workflow.
description: |
  Use when you need to determine whether records refer to the same real-world entity — deduplicating within a dataset, matching across datasets, or linking source records to a reference corpus. Orchestrates Cortex AI functions, Streamlit, dynamic tables, Cortex Agents, and ML training through a structured pipeline with pluggable domain profiles (pharma, financial services, retail/CPG, healthcare, insurance, generic name+address). Triggers: entity resolution, record matching, deduplication, record linkage, fuzzy matching, golden record, master data, MDM, merge records, match entities, link records, dedupe, duplicate detection, identity resolution.
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: I have a customer table with duplicates and a reference master list. Help me build an entity resolution pipeline to dedupe and link records.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Entity Resolution

## Overview

Determine whether two or more records refer to the same real-world entity. This skill orchestrates other bundled skills (`cortex-ai-function-studio`, `developing-with-streamlit-in-snowflake`, `dynamic-tables`, `cortex-agent`, `machine-learning`, `data-quality`) through an entity-resolution-specific workflow rather than reimplementing them.

**Use this skill to:**
- Match or link records across datasets
- Deduplicate within a single dataset
- Build a golden record from multiple sources
- Assess match-readiness via profiling
- Operationalize an incremental matching pipeline

For unstructured inputs (PDFs, scans), run `AI_EXTRACT` / `AI_PARSE_DOCUMENT` first, then feed structured output here.

## Domain Profiles

Load the matching profile from `references/profiles/` based on input data:

| Domain | Tier 1 Identifiers |
|---|---|
| Pharma / life sciences | NPI, DEA, NCPDP |
| Financial services | LEI, DUNS, Tax ID, CRD |
| Retail / CPG | GTIN/UPC, GLN, Supplier ID |
| Healthcare provider | NPI, Taxonomy Code |
| Insurance / payer | NAIC, CMS Payer ID, Plan ID |
| Generic | Name + address only |

## Workflow

### Step 0 — Discovery (blocking)
Use `ask_user_question` (batched, max 4 per round) to capture goal (dedupe / cross-match / link-to-reference / profile-only), domain, source FQNs, reference table FQN if applicable, volume tier, output schema, HITL preference, one-time vs ongoing pipeline, language (English vs multilingual — affects encoder choice), and approach (recommended / specific / benchmark). Present a summary and require explicit confirmation before any work.

### Step 1 — Profiling
Delegate general profiling to `profiling-tables`. Add ER checks: identifier detection, name/address column classification via `AI_CLASSIFY`, address completeness (freeform vs parsed), duplicate density, format consistency, volume.

### Step 1b — Cost estimate
Use `references/templates/cost-estimator.md`. Present credit/cost projection and wait for acknowledgment.

### Step 2 — Normalization
Apply `AI_EXTRACT` for freeform addresses, domain-specific name cleanup, identifier formatting. Materialize `normalized_entities` with source_id, normalized fields, originals, and blocking_key. Validate with NULL counts and format checks (e.g., NPI = 10 digits).

### Step 3 — Blocking
Reduce O(n²) comparisons via geographic, category, phonetic, or identifier-prefix keys. Diagnose: max block size <100K pairs, reduction ratio <10%, unblocked entity count.

### Step 4 — Multi-tier matching (Path A: pair-based)
- **Tier 1 — Deterministic:** exact ID match, confidence 1.0, no AI cost.
- **Tier 2 — Fuzzy:** `AI_EMBED` (`snowflake-arctic-embed-l-v2.0`) + `VECTOR_COSINE_SIMILARITY` + `JAROWINKLER_SIMILARITY`. Starting thresholds: ≥0.92 match, ≥0.80 probable.
- **Tier 3 — AI-judged:** `AI_CLASSIFY` only on Tier 2 probable_match results.

Resolve transitive matches into entity_group_id via connected components.

### Step 4b — Agentic linking (Path B)
For matching against a reference corpus: cosine + Jaro-Winkler triage → batch Cortex Search + `AI_COMPLETE` classification → `cortex-agent` with `cortex_search`, `cortex_analyst_text_to_sql`, and `web_search` tools. Budget: 6 tool calls, 90s, 16K tokens per entity.

### Step 4c — Contrastive embeddings (optional)
With ≥500 labeled entities and a GPU pool (`GPU_NV_S`), train a domain-adapted encoder: `[COL]/[VAL]` serialization, Union-Find clusters, SupConLoss. English → `roberta-base`; multilingual → `xlm-roberta-base`. Sweep thresholds against ground truth.

### Step 5 — HITL review
Delegate to `developing-with-streamlit-in-snowflake`. Side-by-side comparison, accept/reject/flag, decisions persisted to `REVIEW_DECISIONS`.

### Step 6 — Operationalize
Build incremental pipeline via `dynamic-tables`. Materialize golden record view. Monitor source freshness via `data-quality`.

## Common Mistakes

- **Skipping discovery.** Profiling without confirmed goal/domain/FQNs wastes cycles.
- **Blocking too coarse.** A single block >100K pairs explodes cost — split by geography or identifier prefix.
- **Tuning thresholds globally.** Adjust in 0.02–0.03 increments per domain; never copy thresholds across engagements.
- **Running Tier 3 on every pair.** Restrict `AI_CLASSIFY` to Tier 2 `probable_match` only.
- **Embedding raw fields.** Always normalize before `AI_EMBED` or contrastive training.
- **Ignoring transitive matches.** A=B and B=C must collapse into one entity group.
- **Contrastive training without ground truth.** Needs ≥500 labeled entities across ≥200 clusters; otherwise stick with Tier 2.

## References

- `references/profiles/{pharma,financial-services,retail-cpg,healthcare-provider,insurance-payer,generic}.md`
- `references/templates/{normalization,blocking,matching,agentic-matching,search-service,agent-definition,orchestration,contrastive-embeddings,cost-estimator,incremental}.md`
- `references/hitl-app.md`, `references/operationalize.md`
