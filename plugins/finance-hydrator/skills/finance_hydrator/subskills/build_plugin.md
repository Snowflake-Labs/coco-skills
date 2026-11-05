# Phase 5 — Build Plugin

Generate the customer-owned plugin containing only the selected skills, with resolved bindings
written to `hydration_hints.yaml`. The current supported emitter is `skill_plugin`; future
emitters can produce semantic views, agents, or app bundles from the same recommendation contract.

## Step 1 — Build

Run every command from the skill directory (`skills/finance_hydrator/`, referred to as
`<SKILL_DIR>`), or prefix each path with the absolute `<SKILL_DIR>`. Pass one `--catalog` per
file that exists in `catalog/` (currently `catalog/accounting.json` and
`catalog/strategic_finance.json`).

```bash
python3 engine/scripts/build_customer_plugin.py \
  --assessment <path>/assessment.json \
  --catalog catalog/accounting.json \
  --catalog catalog/strategic_finance.json \
  --output <scope.output_dir> \
  --skills <comma-separated skills the user selected in Phase 4>
```

Pass the combined `recommendations` JSON as `assessment.json`. Always pass `--skills` with the
exact skills the user chose; the builder refuses unknown or BLOCKED skills. Without `--skills` it
falls back to `recommendation: include` rows (plus ASK/OPTIONAL rows with `--include-partial`).
Write the `--catalog` arguments out literally; in zsh an unquoted shell variable holding several
arguments is not split. If no skill is selected the builder exits non-zero
instead of writing an empty plugin.

The generated plugin contains:

- `.cortex-plugin/plugin.json`
- `README.md` (included-skills table + activation)
- `skills/<skill_name>/` for each selected skill (copied from the vendored `templates/` tree)
- `evals/<skill_name>/` when the catalog entry has an `eval_dir` that exists
- `skills/<skill_name>/hydration_hints.yaml` generated from the assessment `bindings`; unbound
  optional needs, still-open optional columns and placeholder `extra_settings` are collected in a
  top-level `unresolved:` block instead of being written inline
- `.finance-hydrator` marker file identifying the folder as generated output

## Guardrails

- This is a CHECKPOINT: confirm before writing. The builder replaces `--output` only when it is
  new, empty, or carries the `.finance-hydrator` marker from a previous build. Any other non-empty
  folder is refused (exit 2) unless the user explicitly approves `--force`. The filesystem root,
  the home directory, and the hydrator plugin directory (or any folder containing it) are always
  refused.
- Do not commit or publish the generated plugin unless the user explicitly asks.

**Next:** load `subskills/validate_and_activate.md`, passing the generated plugin directory.
