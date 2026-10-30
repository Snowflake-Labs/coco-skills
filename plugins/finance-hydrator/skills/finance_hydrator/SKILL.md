---
name: finance_hydrator
description: >
  Build a customer-specific finance plugin. Use when setting up finance,
  accounting, finops, FP&A, CFO, controller, cloud cost, AP, AR, GL, budget,
  forecast, COGS, or revenue skills for a customer. Assesses available data,
  recommends the best-fit finance skills, generates a plugin, validates
  bindings, and gives activation instructions.
license: Proprietary. See LICENSE.txt for complete terms
---

# Finance Plugin Hydrator

Builds a customer-specific finance plugin from Accounting, FinOps, and Strategic Finance
templates. It discovers available finance data cheaply, recommends best-fit skills, generates a
customer-owned plugin with only those skills, then deep-validates the bindings. It does **not**
run the finance analyses itself.

This orchestrator loads a phase playbook from `subskills/` at each step. The `subskills/*.md`
files are phase playbooks, not independently-triggerable skills — load each only when its phase
starts.

The deterministic engine (scorer, builder, validators) is vendored under `engine/scripts/` so
this plugin runs standalone from a bare `python3` (no pip, no monorepo). Skill templates it
generates from are vendored under `templates/<source_plugin>/`. Domain catalogs live in
`catalog/*.json`.

## Phase Flow

```
Phase 1: Collect Profile      --> subskills/collect_profile.md
  |  Finance persona, subdepartment, jobs-to-be-done, source systems, output type.
  v
Phase 2: Collect Scope        --> subskills/collect_scope.md
  |  Connection, role, candidate DB/schema scope, output destination.
  v
Phase 3: Discover Inventory   --> subskills/discover_inventory.md
  |  Cheap, fast, metadata-only crawl (300s/query cap). Emits account_inventory.json.
  v
 CHECKPOINT  <-- confirm scope before any broad discovery
  |
Phase 4: Recommend Skills     --> subskills/recommend_skills.md
  |  Combine business-fit score with data-readiness score; present recommendation matrix.
  v
 CHECKPOINT  <-- user selects skills (ask before including PARTIAL)
  |
Phase 5: Build Plugin         --> subskills/build_plugin.md
  |  build_customer_plugin.py writes the customer plugin + hydration_hints.yaml.
  v
 CHECKPOINT  <-- confirm before writing the generated plugin
  |
Phase 6: Validate & Activate  --> subskills/validate_and_activate.md
  |  Deep-validate selected bindings off the SAME inventory; smoke plan; activation.
  v
 DONE
```

## Phase Transitions

| Completed Phase | Next Action |
|---|---|
| Phase 1 (Collect Profile) | Load `subskills/collect_scope.md`, pass `profile` |
| Phase 2 (Collect Scope) | Load `subskills/discover_inventory.md`, pass `profile` + `scope` |
| Phase 3 (Discover Inventory) | Load `subskills/recommend_skills.md`, pass `profile` + `account_inventory.json` |
| Phase 4 (Recommend Skills) | Load `subskills/build_plugin.md`, pass `selected_skills` + combined assessment |
| Phase 5 (Build Plugin) | Load `subskills/validate_and_activate.md`, pass `generated_plugin_dir` |
| Phase 6 (Validate & Activate) | Present report; finalize on approval |

## State Carried Between Phases

| Variable | Set In | Used In | Description |
|---|---|---|---|
| `profile` | 1 | 3, 4 | finance subdepartment, persona, jobs-to-be-done, source systems, requested emitters |
| `scope` | 2 | 3 | connection, role, candidate databases, output destination |
| `account_inventory` | 3 | 4, 6 | `account_inventory.json` (objects, columns, official, usage, confidence, domain_access) |
| `fit_assessment` | 4 | 4 | business-fit JSON from `score_fit.py` |
| `readiness` | 4 | 4, 5 | data-readiness JSON from `score_readiness.py` |
| `recommendations` | 4 | 5 | combined fit + readiness recommendation JSON |
| `selected_skills` | 4 | 5, 6 | skills the user chose |
| `generated_plugin_dir` | 5 | 6 | path to the written customer plugin |
| `validation_report` | 6 | finalize | per-binding READY/PARTIAL/BLOCKED after deep validation |

## Guardrails

- **Cheap discovery.** Phase 3 is metadata-only (`SHOW`/`DESCRIBE`/`RESULT_SCAN` + one batched
  `ACCESS_HISTORY` check), scoped to Phase-2 databases, never `IN ACCOUNT`, `timeout_seconds:
  300` on every query. On timeout: record a gap, do not widen or retry broadly.
- **Deep only when it pays.** Bounded data probes, wider usage windows, and better-data searches
  happen in Phase 6 on selected skills only — never during discovery.
- **Official data only.** Disqualify dev/demo/test/sandbox/backup, staging/raw, per-user scratch,
  dated snapshots — regardless of search rank or usage.
- **No Cortex Sense.** Discovery uses metadata + `snowflake_object_search` + semantic-view
  discovery only.
- **No fabricated numbers.** Readiness comes only from `score_readiness.py` over the real
  inventory. Never present sample percentages as results.
- **Stop for confirmation** before: broad discovery, including PARTIAL skills, writing the
  generated plugin, publishing to GitHub / Plugins Catalog.

## Scripts

| Script | Purpose |
|---|---|
| `engine/scripts/validate_catalog.py --validate-catalog` | Lint the catalog files |
| `engine/scripts/score_readiness.py --inventory ... --catalog ...` | Inventory -> assessment |
| `engine/scripts/build_customer_plugin.py` | Write the customer plugin + hydration hints |
| `engine/scripts/validate_bindings.py --plugin-dir ...` | Static binding validation |
| `engine/scripts/smoke_test_plan.py --plugin-dir ...` | Golden-case readiness |

## Output

Return: recommendation matrix, generated plugin path, validation table, smoke-test readiness
table, activation instructions.
