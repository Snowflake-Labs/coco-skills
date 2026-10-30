# Phase 6 — Validate & Activate (deep, no re-crawl)

Confirm the generated plugin's bindings are real and good. This is where the expensive work the
discovery phase deliberately skipped happens — but only on the SELECTED skills, reusing
`account_inventory.json` (no second crawl).

## Step 1 — Static validation

```bash
python3 engine/scripts/validate_bindings.py --plugin-dir <generated_plugin_dir> --metadata-only
```

Statuses: `READY` (required FQNs + columns resolved, no placeholders), `PARTIAL` (required pass,
optional unresolved), `BLOCKED` (required FQN/column/access missing). Fix any placeholder before
continuing.

## Step 2 — Deep confirmation (the depth discovery skipped)

For each selected skill's required bindings, using the resolved FQNs already in the inventory:

- **Bounded data probe** — confirm the table is populated and columns hold real values:
  ```sql
  SELECT <bound columns> FROM <fqn>
  WHERE <recent-window predicate> LIMIT 20;
  ```
  A tight filter + LIMIT keeps this cheap. A generous `timeout_seconds` is acceptable here because
  it is narrow and intentional (unlike the discovery cap).
- **Wider usage window** — for any object that showed 0 events in the 7-day discovery check, re-check
  30 days via `ACCESS_HISTORY`; a still-stale table is a risk — flag it.
- **Find better data** — if a binding is LOW/PARTIAL confidence, search for a stronger/more
  canonical object (`snowflake_object_search`, sibling candidates in the same schema, a semantic
  view). If a better official object is found and confirmed, update the binding in the generated
  `hydration_hints.yaml` before finalizing.

Record results as `validation_report` (per-binding READY/PARTIAL/BLOCKED + any upgrades).

## Step 3 — Smoke-test plan

```bash
python3 engine/scripts/smoke_test_plan.py --plugin-dir <generated_plugin_dir>
```

Missing golden cases → `INCONCLUSIVE`, not failures.

## Step 4 — Optional execution validation (confirmation-gated)

If the user wants deeper assurance and approves query execution, run a bounded validation loop for
the selected skills. Keep it narrow and cheap:

1. Compile or dry-run concrete SQL templates where available.
2. Execute with a tight recent-window predicate and `LIMIT`.
3. Treat zero rows, all-NULL/all-zero key metrics, or obvious fanout as a validation failure unless
   the skill's intent legitimately expects empty results.
4. Optionally ask an LLM judge whether the sampled output matches the skill intent.

If this optional validation fails, return to Phase 3/4 to re-bind or adjust the selected skill set.
Do not mark the generated plugin production-ready on failed execution validation.

## Step 5 — Activation

Give the exact command for the chosen destination:

```bash
cortex plugin add <generated_plugin_dir>
/plugin reload
```

## Guardrails

- Never bind to a non-official object, even during a better-data upgrade.
- Publishing to GitHub / Plugins Catalog is a separate, explicit user request — never automatic.

**Done:** present the recommendation matrix, generated plugin path, validation table, smoke-test
table, and activation instructions.
