# Phase 4 — Recommend Skills

Turn the profile + inventory into a recommendation using deterministic scorers. No LLM guessing
about fit or coverage — business fit comes from `engine/scripts/score_fit.py`, and data readiness
comes from `engine/scripts/score_readiness.py`.

## Step 1 — Score business fit

Write the Phase-1 profile JSON to `<path>/profile.json`, then run:

```bash
python3 engine/scripts/score_fit.py \
  --profile <path>/profile.json \
  --catalog catalog/finops.json \
  --catalog catalog/accounting.json \
  --catalog catalog/strategic_finance.json
```

Capture the output as `fit_assessment`.

## Step 2 — Score data readiness

```bash
python3 engine/scripts/score_readiness.py \
  --inventory <path>/account_inventory.json \
  --catalog catalog/finops.json \
  --catalog catalog/accounting.json \
  --catalog catalog/strategic_finance.json
```

Capture the output as `readiness`.

## Step 3 — Combine fit + readiness

```bash
python3 engine/scripts/combine_recommendations.py \
  --fit <path>/fit_assessment.json \
  --readiness <path>/readiness.json
```

Capture the output as `recommendations`.

## Step 4 — Present the matrix

Render in this order: `RECOMMENDED`, `ASK`, `OPTIONAL`, `BLOCKED`, `NOT_RELEVANT`; break ties by
business fit, then readiness.

```text
| Domain | Skill | Business Fit | Data Readiness | Final Status | Recommendation | Reason |
|---|---|---:|---:|---|---|---|
```

## Step 5 — Apply defaults and select

- `RECOMMENDED` → default include.
- `ASK` → ask before including; name the readiness gap.
- `OPTIONAL` → ask only if the user wants broader coverage; do not default include.
- `BLOCKED` → exclude unless the user explicitly provides a better binding candidate.
  - **Setup-eligible exception**: before excluding a BLOCKED skill, check whether any of its
    `block_kinds` is `MISSING_SOURCE` AND the missing data-need key has `setup_eligible: true` in
    `account_inventory.json` gaps. If so, present a two-option prompt instead of a plain exclude:

    ```
    ⚠️  recon_analysis — BLOCKED (profile store not found)

    Required binding `accounting.recon.profile_store` was not found in your account.
    The Finance Hydrator can create the full recon_analysis-compatible profile store
    (table + GET procedure + SAVE procedure) right now.

    Options:
      [1] Setup — create the profile store, then include recon_analysis
      [2] Skip  — exclude recon_analysis from this plugin
    ```

    If the user picks **[1] Setup**: load `subskills/setup_profile_store.md`. After setup
    completes, re-run `score_readiness.py` with the updated `account_inventory.json` and
    return to this step — `recon_analysis` should now score PARTIAL or READY and can be
    included normally. If setup fails (insufficient privileges, user cancels), fall back to
    excluding the skill with a note.

    If the user picks **[2] Skip**: exclude the skill as normal.

- `NOT_RELEVANT` → exclude.

Record `selected_skills`. This is a CHECKPOINT — do not build until the user confirms selection.

## Guardrails

- Present only scorer-produced business fit and readiness. Never invent a percentage.
- Business fit never overrides data safety. Missing required sources/columns or unsafe scoping
  columns stay BLOCKED until fixed.
- If every skill in a domain is BLOCKED because `domain_access` is false, say so plainly and
  suggest re-scoping (Phase 2) rather than including blocked skills.

**Next:** load `subskills/build_plugin.md`, passing `selected_skills` + `recommendations`.
