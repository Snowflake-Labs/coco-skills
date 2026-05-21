# Cost Estimator — Entity Resolution Pipeline

Use this template after Phase 1 profiling to estimate costs before proceeding. Present the estimate to the user and wait for explicit acknowledgment before continuing with implementation.

## Input Parameters

- **N** = total source entity count (from profiling output)
- **R** = reference corpus size (Path B only)
- **Tier distribution** = expected % of records reaching each tier (use defaults below if unknown)

## Path A Cost Model (Pair-Based Deduplication)

| Component | Driver | Unit Cost | Formula |
|-----------|--------|-----------|---------|
| Embedding generation | N records | ~$0.0001/record (`snowflake-arctic-embed-l-v2.0`) | `N × $0.0001` |
| Tier 3 AI classify | Probable-match pairs (~5–15% of candidates) | ~$0.001–0.003/pair | `probable_pairs × $0.002` |
| Warehouse compute | Size by volume | MEDIUM (<1M records), LARGE (>1M) | ~2–10 credits/run |

**Estimated candidate pairs** (after blocking): typically `N × 10–50` pairs reviewed, of which 5–15% reach Tier 3.

```
total_cost_a ≈ (N × $0.0001) + (probable_match_pairs × $0.002) + warehouse_credits
```

## Path B Cost Model (Agentic Entity Linking)

| Tier | Records Processed | Cost per Entity | Subtotal Formula |
|------|-------------------|-----------------|------------------|
| 1 — Deterministic SQL triage | N (all) | ~$0 (SQL only) | $0 |
| 1.5 — Batch search + classify | ~20–40% of N | ~$0.001–0.005 | `0.3N × $0.003` |
| 2 — Agent with tools | ~5–20% of N | ~$0.05–0.20 | `0.1N × $0.10` |

**Additional costs:**

| Component | Notes |
|-----------|-------|
| Cortex Search Service | Index build + incremental refresh credits (varies by corpus size) |
| Embedding generation | Corpus indexing: `R × $0.0001`; source records: `N × $0.0001` |
| Semantic model | Minimal — one-time YAML upload, no ongoing cost |
| Warehouse | Task DAG workers × runtime; typically MEDIUM warehouse |

```
total_cost_b ≈ (0.3N × $0.003) + (0.1N × $0.10) + (N × $0.0001) + (R × $0.0001) + warehouse_credits
```

## Contrastive Embeddings Cost Model (Phase 4c — Standalone or Add-On)

| Component | Driver | Unit Cost | Formula |
|-----------|--------|-----------|---------|
| GPU training (one-time) | Compute pool credits, ~5-30 min on GPU_NV_S (T4) | ~1-5 credits/run | Fixed per training run |
| Embedding generation | Included in GPU training job | $0 marginal | Included |
| Blocking + threshold sweep | Warehouse compute (XS-MEDIUM) | ~0.5-2 credits | Warehouse runtime |
| NER preprocessing (optional) | CPU within GPU container | +10-17% training time | Included in GPU cost |
| Retraining (periodic) | Same as initial training | ~1-5 credits/retrain | As needed |

**Break-even vs `AI_EMBED`:** Contrastive embeddings have a fixed training cost (~1-5 credits) but $0 per-record embedding cost. `AI_EMBED` costs ~$0.0001/record with no training. Break-even is at ~50K records — above that, contrastive is cheaper.

```
total_cost_contrastive ≈ training_credits + warehouse_credits
                       ≈ 1-5 credits + 0.5-2 credits
                       ≈ 1.5-7 credits (one-time, plus retraining as needed)
```

**Add-on mode savings:** When contrastive embeddings replace `AI_EMBED` in Tier 2, the cost savings come from:
1. Eliminating per-record embedding cost (`N × $0.0001` saved)
2. Reducing Tier 3 escalation (higher-quality embeddings produce fewer `probable_match` results needing LLM classification)

## Volume-Based Path Recommendation

| Source Records | Reference Corpus | Recommended Path | Estimated Cost Range |
|----------------|-----------------|-----------------|---------------------|
| <10K | None | Path A | $1–10 |
| <10K | Available | Path B | $5–50 |
| 10K–100K | None | Path A | $10–100 |
| 10K–100K | Available | Path B | $30–500 |
| 100K–1M | None | Path A | $100–1,000 |
| 100K–1M | Available | Path B | $300–5,000 |
| >1M | Any | Either (discuss with customer) | $1,000+ |
| Any (with ground truth + GPU) | Any | Contrastive (standalone or add-on) | $3-15 (one-time training + warehouse) |

## Presenting the Estimate

Use this template when surfacing the cost estimate to the user:

```
Estimated pipeline cost for [N] source entities using Path [A/B]:

- Embedding generation:          ~$X
- Tier [1.5 / 3] LLM classify:  ~$Y
- Tier [2 / 3] agent/classify:  ~$Z
- Warehouse compute:             ~$W credits (~$W_dollars at standard rates)
- Total estimated:               ~$TOTAL

These are estimates based on typical tier distributions. Actual costs depend on
data quality (cleaner data = more Tier 1 matches = lower LLM cost).
```

## MANDATORY STOPPING POINT

Present the cost estimate to the user. **Do NOT proceed to any implementation phase until the user explicitly acknowledges and accepts the estimated cost.**
