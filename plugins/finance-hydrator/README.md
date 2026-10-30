# Finance Hydrator

**For customer onboarding across the CFO org.**

Finance Hydrator is a meta-plugin: it does not replace the Accounting, FinOps, or Strategic Finance plugins. It discovers which finance data a customer actually has, recommends the best-fit skills across those plugins, and generates a smaller customer-specific plugin containing only selected skills.

## Why this exists

Customers often do not know whether they need Accounting, FinOps, Strategic Finance, or some mix of all three. Starting with separate plugin hydrators forces them to choose a plugin before discovery has happened.

Finance Hydrator makes onboarding discovery-first. It runs as a thin orchestrator that loads one phase playbook from `skills/finance_hydrator/subskills/` at a time:

1. **Collect profile** — finance subdepartment, persona, jobs-to-be-done, source systems, and requested output.
2. **Collect scope** — connection, role, candidate database/schema scope, output destination.
3. **Discover inventory** — a cheap, fast, metadata-only crawl (`SHOW`/`DESCRIBE`/`RESULT_SCAN` + one batched 7-day `ACCESS_HISTORY` check), scoped to the chosen databases with a 300s-per-query cap. No Cortex Sense, no warehouse compute. Emits one reusable `account_inventory.json`.
4. **Recommend skills** — deterministic scorers combine business fit (`score_fit.py`) with data readiness (`score_readiness.py`) to produce RECOMMENDED / ASK / OPTIONAL / BLOCKED / NOT_RELEVANT. No fabricated percentages.
5. **Build plugin** — generate a customer-owned plugin with selected skills only, plus `hydration_hints.yaml` from the resolved bindings (copied from the vendored `templates/` tree).
6. **Validate & activate** — deep-validate the selected bindings by reusing the same inventory (bounded data probes, wider usage windows, better-data upgrades), then give activation steps.

Discovery is broad, shallow, and cheap; the expensive confirmation work happens only in validation, and only on the skills the customer selected.

## What it can generate

| Domain | Example skills |
|---|---|
| FinOps | Catalog entries only; skill templates are not included in this checkout |
| Accounting | `flux_analysis`, `accounts_payable_pulse`, `ar_tracker`, `duplicate_invoice_check`, `good_morning_collector` |
| Strategic Finance | `good_morning_cfo`, `forecast_vs_forecast`, `budget_vs_actuals`, `cloud_cogs_analysis`, `earnings_prep`, `revenue_analysis`, `scenario_planning` |

## Quick start

```text
set up finance plugin
```

The skill will ask for a Snowflake connection, role, and optional schema scope. It then produces a recommendation matrix and asks which skills to include.

## Customer workflow

1. Install the `finance-hydrator` plugin.
2. Run the `finance_hydrator` skill.
3. Confirm the finance profile: subdepartment, persona, jobs-to-be-done, source systems, and output type.
4. Confirm the Snowflake connection, role, candidate database/schema scope, and output destination.
5. Review the recommendation matrix: business fit + data readiness + final recommendation.
6. Choose which RECOMMENDED / ASK / OPTIONAL skills to include.
7. The hydrator writes a new customer-owned plugin, typically `customer-finance/`.
8. Install the generated `customer-finance` plugin and run the hydrated finance skills from there.

The hydrator itself is an onboarding tool. The generated customer plugin is the artifact the customer keeps using for finance workflows.

## Developer workflow

Paths below are relative to `skills/finance_hydrator/` inside this plugin.

1. Update the onboarding flow in `SKILL.md` and `subskills/`, or the offered skills and data requirements in `catalog/*.json`.
2. Keep engine code under `engine/` and skill templates under `templates/<source_plugin>/`. Each catalog entry selected for generation must have its required templates available.
3. Validate catalog and binding changes with the bundled scripts listed in [SKILL.md](skills/finance_hydrator/SKILL.md), and test affected workflows with synthetic data.
4. Review generated artifacts and package the entire `finance-hydrator/` directory, including its manifest, engine, catalogs, and templates.

There is no separate shared engine package in this repository. Shared-source sync,
CI, and ZIP-building tools referenced by earlier versions of this guide are not
included here; do not assume those commands or automated drift checks are available.

## Outputs

The generated plugin looks like:

```text
customer-finance/
├── .cortex-plugin/plugin.json
├── README.md
├── skills/
│   ├── flux_analysis/
│   ├── accounts_payable_pulse/
│   └── forecast_vs_forecast/
└── evals/
    └── ...
```

Install locally with:

```bash
cortex plugin add /path/to/customer-finance
/plugin reload
```

## Self-contained & zip distribution

This plugin is self-contained: it runs on a bare `python3` with no pip packages and
no access to the monorepo. Everything it needs at runtime is vendored under
`skills/finance_hydrator/`:

```text
finance-hydrator/                 <- zip THIS directory -> finance-hydrator.zip
├── .cortex-plugin/plugin.json    <- one plugin.json = one plugin = one zip
└── skills/finance_hydrator/
    ├── SKILL.md                   <- domain orchestrator (authored)
    ├── subskills/                 <- phase playbooks (authored)
    ├── catalog/*.json             <- domain skill catalog (authored)
    ├── engine/                    <- bundled engine (stdlib-only)
    └── templates/<source_plugin>/ <- vendored source skills + eval dirs
```

The deterministic engine is vendored into `engine/` (never imported across
plugins) so a single zip is closed over its dependencies. Generated
`hydration_hints.yaml` is emitted by a dependency-free writer, so no PyYAML is
needed at customer runtime.

**Customer install (offline):**

```bash
unzip finance-hydrator.zip -d ~/cortex-plugins/
cortex plugin add ~/cortex-plugins/finance-hydrator
/plugin reload
```

> Confirm your CoCo build's `cortex plugin add` accepts a local directory (or a
> `.zip` directly). If it only supports GitHub/Catalog sources, publish the
> unzipped plugin to the Plugins Catalog instead — the self-containment work is
> unchanged.

## Design note

This plugin intentionally keeps the underlying skills modular. It embeds catalogs and templates, not one giant prompt containing every finance workflow.

**Maintainers:** keep the bundled engine, catalogs, templates, and their provenance
metadata consistent. Test the packaged plugin independently of the repository's
other directories before distribution.

## Contributor map

All paths are relative to `skills/finance_hydrator/`.

| Need to change | Location in this plugin | Validation |
|---|---|---|
| Hydrator scoring, build, or validation behavior | `engine/scripts/` | Synthetic regression cases and generated-plugin checks |
| Finance onboarding flow | `SKILL.md` and `subskills/` | Walk through phase transitions and approval gates |
| Offered skills and data requirements | `catalog/*.json` | Bundled catalog validator and template-path checks |
| Generated skill behavior | `templates/<source_plugin>/skills/` | Affected skill's test plan and golden cases |
| Customer smoke-test plan | `templates/<source_plugin>/evals/` | Confirm the cases match available data and expected outputs |

`engine/ENGINE_MANIFEST.json` and `templates/TEMPLATES_MANIFEST.json` record
bundled-file provenance. Do not treat their presence as proof that an automated
sync or CI gate ran.

Never put source-account eval results or output artifacts in templates. Only customer-relevant eval planning files are vendored: `golden_cases.md`, `eval_plan.md`, and `README.md`.

## License

Copyright (c) Snowflake Inc. All rights reserved.
Licensed under the Snowflake Skills License [LICENSE.txt](LICENSE.txt).
