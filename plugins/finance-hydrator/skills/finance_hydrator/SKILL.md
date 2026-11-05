---
name: finance_hydrator
description: >
  Build a customer-specific finance plugin. Use when setting up finance,
  accounting, FP&A, CFO, controller, close, flux, reconciliation, AP, AR, GL,
  budget, forecast, COGS, or revenue skills for a customer. Assesses available data,
  recommends the best-fit finance skills, generates a plugin, validates
  bindings, and gives activation instructions.
license: Snowflake Skills License. See LICENSE.txt for complete terms
type: snowflake
language: en
status: Published
---

# Finance Plugin Hydrator

Builds a customer-specific finance plugin from Accounting and Strategic Finance
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
- **Official data only.** Disqualify names with a whole-token `dev`/`demo`/`test`/`sandbox`/
  `scratch`/`tmp`/`temp`/`bkp`/`backup`/`clone`/`snapshot`/`archive` part, `_YYYYMMDD`/`_YYYYMM`
  dated snapshots, and per-user scratch — regardless of search rank or usage. Staging/raw layers are
  disqualified unless the user confirms they are the official layer. `score_readiness.py` enforces
  the token rule (`NON_OFFICIAL`) even if discovery marked an object official.
- **No Cortex Sense.** Discovery uses metadata + `snowflake_object_search` + semantic-view
  discovery only.
- **No fabricated numbers.** Readiness comes only from `score_readiness.py` over the real
  inventory. Never present sample percentages as results.
- **Stop for confirmation** before: broad discovery, including PARTIAL skills, writing the
  generated plugin, publishing to GitHub / Plugins Catalog.

## Scripts

Run every script with bare `python3` from this skill's directory (`skills/finance_hydrator/`,
written `<SKILL_DIR>` below), or call it by absolute path (`python3 <SKILL_DIR>/engine/scripts/...`).
Relative paths such as `catalog/accounting.json` assume the skill directory as the working
directory. Pass one `--catalog` for each `catalog/*.json` file that exists.

| Script | Phase | Purpose |
|---|---|---|
| `engine/scripts/validate_catalog.py --validate-catalog --catalog ...` | dev | Lint the catalog files |
| `engine/scripts/score_fit.py --profile ... --catalog ...` | 4 | Profile -> business fit |
| `engine/scripts/score_readiness.py --inventory ... --catalog ...` | 4 | Inventory -> data readiness |
| `engine/scripts/combine_recommendations.py --fit ... --readiness ...` | 4 | Fit + readiness -> recommendation matrix |
| `engine/scripts/build_customer_plugin.py --assessment ... --catalog ... --output ...` | 5 | Write the customer plugin + hydration hints |
| `engine/scripts/validate_bindings.py --plugin-dir ... --metadata-only --inventory ...` | 6 | Static binding validation (no SQL) |
| `engine/scripts/smoke_test_plan.py --plugin-dir ...` | 6 | Golden-case readiness |
| `engine/scripts/update_manifests.py` | dev | Regenerate engine/template manifests |

Engine tests: `python3 -m unittest discover -s engine/tests` from `<SKILL_DIR>`.

The JSON contracts between scripts are in `engine/references/output_contract.md`.

## Output

Return: recommendation matrix, generated plugin path, validation table, smoke-test readiness
table, activation instructions.
