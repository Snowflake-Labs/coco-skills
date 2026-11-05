# Finance Hydrator (`finance-hydrator`)

> [!WARNING]
> **For internal use only. These skills process personally identifiable information (PII).**
>
> - Once set up on your data, these skills can work with employee and other personal data, for example invoice approvers and requesters, supplier contacts, and payroll and compensation costs. They are intended only for internal use by authorized Finance and Accounting staff within your organization.
> - **These skills do NOT obfuscate, mask or anonymize PII.** Outputs can include named individuals and sensitive financial details.
> - **Do NOT expose the results to the public internet**, publish them, or share them outside the people authorized to see that data.
> - **It is the responsibility of whoever deploys these skills** to make sure their use complies with all applicable local laws and regulations on processing personal data (for example GDPR, CCPA/CPRA and local employment and data-protection laws), and with your organization's own privacy, access-control and data-retention policies.

Finance Hydrator sets up the dehydrated Finance skills on **your own** finance data in Snowflake. You describe your team and what you want to achieve. It scans the databases you choose, recommends the Accounting and Strategic Finance skills your data can support, and generates a customer-owned plugin containing only the skills you select, bound to your tables and columns. These skills are templates designed to get you most of the way there: review each generated skill's bindings, thresholds and guardrails before relying on its output.

## Who it's for
The person setting up the Finance skills for their company, usually a **finance systems or FP&A analytics lead**, or a controller's data partner. You don't need to write code or SQL: you answer questions in chat and confirm what the hydrator finds. You need read access to the finance data in Snowflake. The people who later *use* the skills (CFO, controller, AP and AR teams, FP&A) don't run Finance Hydrator.

## Quick Start
https://github.com/user-attachments/assets/3459eea9-5902-4649-a3da-ebb7809c68b9

```text
Install the plugins from https://github.com/Snowflake-Labs/coco-skills/tree/main/plugins/finance-hydrator and open finance hydrator
```

## Skills it can set up
| Domain | Skills |
|---|---|
| Accounting | `flux_analysis`, `accounts_payable_pulse`, `ar_tracker`, `duplicate_invoice_check`, `good_morning_collector`, `recon_analysis` |
| Strategic Finance | `good_morning_cfo`, `forecast_vs_forecast`, `budget_vs_actuals`, `cloud_cogs_analysis`, `earnings_prep`, `revenue_analysis`, `scenario_planning` |

The plugin also includes [`bva_variance_setup`](skills/bva_variance_setup/), a deployable budget-vs-actuals web app for Snowflake. See its README for setup and known limitations.

## The flow
| Stage | What happens | Your input |
|---|---|---|
| 1 Profile | Your finance function, role, jobs-to-be-done, source systems (ERP, planning tool) and the output you want | team and goals |
| 2 Scope | Snowflake connection and role, the databases (and optionally schemas) to search, and where to write the plugin | connection, scope, output folder |
| 3 Discover | A fast, metadata-only scan (`SHOW`, `DESCRIBE` and one 7-day usage check), capped at 300 seconds per query. Dev, test, backup, snapshot and dated tables are skipped; staging tables are used only if you confirm they are the official source. Columns are mapped to each skill's data needs | confirm scope before the scan |
| 4 Recommend | Each skill is scored on business fit and data readiness and labeled RECOMMENDED, ASK, OPTIONAL, BLOCKED (with the reason) or NOT_RELEVANT. Scores come only from your actual inventory | pick the skills |
| 5 Build | Writes the plugin with only the selected skills, each with a `hydration_hints.yaml` of its bindings. Anything still unbound is listed under `unresolved:` | confirm before writing |
| 6 Validate | Checks every binding, reports each skill as READY, PARTIAL or BLOCKED, gives a smoke-test plan from the skill's golden cases, and shows how to install | none |

The generated plugin (by default `customer-finance/`) is what your team keeps using. Install it with `cortex plugin add <path>/customer-finance`, then `/plugin reload`. Re-running the hydrator into the same folder replaces the previous build; it never overwrites a non-empty folder it didn't create.

## If a skill comes out partial or blocked
The validation summary says, per skill, **READY**, **PARTIAL** or **BLOCKED**, with the reason.
- **READY:** every required table and column is bound and checked. Run the smoke-test plan, then use it.
- **PARTIAL:** the skill works, but something optional is still open (listed under `unresolved:` in its `hydration_hints.yaml`), for example a fiscal calendar or an enrichment column. The skill asks you for it at run time, or you can point it to the right table and rebuild.
- **BLOCKED:** a required table or column wasn't found, or the only match looked non-official (dev, test, snapshot or unconfirmed staging). Grant access to that data, widen the scope, or confirm the official table, and run Finance Hydrator again.
- **NOT_RELEVANT:** the skill doesn't match the profile you described. Change the profile if you do want it.

## Two ways to run
| You have | Experience | Start |
|---|---|---|
| Cortex Code on your computer | **Chat**, guided through the stages | `cortex plugin add <path>/finance-hydrator`, then say "Open finance hydrator" |
| No network access | Same chat flow from a zip | `unzip finance-hydrator.zip -d ~/cortex-plugins/`, then `cortex plugin add ~/cortex-plugins/finance-hydrator` and `/plugin reload` |

If your Cortex Code version only installs from GitHub or the Plugins Catalog, publish the unzipped plugin there instead.

**Requirements:** `python3` (the engine uses the standard library only) and a Snowflake connection with read access to your finance databases. Usage signals need `IMPORTED PRIVILEGES` on the `SNOWFLAKE` database (or the `GOVERNANCE_VIEWER` database role); without it, discovery still runs and records the gap.

## Layout
```
skills/finance_hydrator/
  SKILL.md                         Chat flow, phases and guardrails
  subskills/                       One playbook per phase (profile, scope, discover, recommend, build, validate)
  catalog/accounting.json          Accounting skill catalog (data needs, column aliases, fit signals)
  catalog/strategic_finance.json   Strategic Finance skill catalog
  engine/scripts/                  Engine: scoring, build, validation (standard library only)
  engine/references/               Inventory and scoring contracts
  engine/tests/                    Unit tests, synthetic fixtures only
  templates/<domain>/skills/       Dehydrated skill templates copied into the generated plugin
  templates/<domain>/evals/        Per skill: eval_plan.md and golden_cases.md (smoke tests)
skills/bva_variance_setup/         Budget-vs-actuals web app (separate setup)
```

## Tests
`python3 -m unittest discover -s engine/tests` from `skills/finance_hydrator/` (synthetic data only; no account access).

After changing anything under `engine/` or `templates/`, run `python3 engine/scripts/update_manifests.py` to refresh the provenance manifests, then re-run the tests. Catalog changes: `python3 engine/scripts/validate_catalog.py --catalog catalog/accounting.json --catalog catalog/strategic_finance.json --validate-catalog`.

## Built-in guardrails
Some hydrated skills limit what they do. These guardrails reduce risk; they do not replace review by a qualified person.
- `earnings_prep`: treats pre-release figures as material non-public information. Never sends unreleased figures to external tools, labels outputs confidential, respects quiet periods, and requires IR, Legal or Disclosure Committee sign-off on scripts and non-GAAP reconciliations. Stops on a permission error instead of working around it.
- `scenario_planning`: runs sensitivities on a session-only copy; never modifies your shared planning model without explicit confirmation of the target version.
- `recon_analysis`: read-only on source data; shows a stored profile's SQL before running it and accepts only single `SELECT` source queries.
- `duplicate_invoice_check`: flags potential duplicates for review; it does not block or reverse payments.
- All skills: commentary is grounded only in query output, and figures are never estimated or filled in.

Outputs are analysis for your finance team, not official financial statements or disclosures. Skills such as `ar_tracker`, `accounts_payable_pulse` and `good_morning_collector` can return named customers, suppliers and individuals. Restrict who can run them.

## Privacy and safety during setup
- **Discovery** reads metadata only: object names, column names and types. It doesn't read table rows.
- **Recent usage** comes from one batched 7-day `ACCESS_HISTORY` check on candidate tables; query text is not stored.
- **Writes:** none to your Snowflake account by default. The hydrator writes the customer plugin to the folder you choose. The optional reconciliation profile store is created only if you approve the DDL.

## License

Copyright (c) Snowflake Inc. All rights reserved.
Licensed under the Snowflake Skills License [LICENSE.txt](LICENSE.txt).
