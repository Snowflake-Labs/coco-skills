# Phase 4 — Build Plugin

Generate the customer-owned plugin containing only the selected skills, with resolved bindings
written to `hydration_hints.yaml`. The current supported emitter is `skill_plugin`; future
emitters can produce semantic views, agents, or app bundles from the same recommendation contract.

## Step 1 — Build

```bash
python3 engine/scripts/build_customer_plugin.py \
  --assessment <path>/assessment.json \
  --catalog catalog/finops.json \
  --catalog catalog/accounting.json \
  --catalog catalog/strategic_finance.json \
  --output <scope.output_dir> \
  [--include-partial]
```

Pass the combined `recommendations` JSON as `assessment.json`. The builder honors
`recommendation: include|ask|exclude`; pass `--include-partial` only for ASK/OPTIONAL skills the
user explicitly approved in Phase 4.

The generated plugin contains:

- `.cortex-plugin/plugin.json`
- `README.md` (included-skills table + activation)
- `skills/<skill_name>/` for each selected skill (copied from the vendored `templates/` tree)
- `evals/<skill_name>/` when the catalog entry has an `eval_dir` that exists
- `skills/<skill_name>/hydration_hints.yaml` generated from the assessment `bindings`

## Guardrails

- This is a CHECKPOINT: confirm before writing (the script clears `--output` if it exists).
- Do not commit or publish the generated plugin unless the user explicitly asks.

**Next:** load `subskills/validate_and_activate.md`, passing the generated plugin directory.
