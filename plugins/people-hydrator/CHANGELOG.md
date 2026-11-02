# Changelog

## 3.0.0

- Four stages: Discover, Bind, Tune, Hydrate. Golden cases now run as the last step of Tune (the separate Evaluate stage is gone).
- Bind grades each skill's data connection against the `binding-gate` rules in its eval plan (`eval_gate.py`, `binding_gate.json`): everything connected, connected with gaps, or key data not confirmed. Each failed rule becomes a "which table holds X?" question in Tune, answered through a validated fix.
- At most 5 Tune questions per skill; the rest are shown as assumed defaults.
- The preview asks "Does this match what you report?" for each skill's headline number.
- Hydration status: key data not confirmed is hydrated as partial with a warning; a preview nobody reviewed keeps the skill partial.
- Each hydrated skill starts with a data-connection check (one metadata query per database) and asks for re-hydration if a bound table or column is gone.
- Templates are generated from `plugins/external/people/skills` and `evals` (`plugins/external/people/_tools/sync_templates.py`); the release copy uses an allowlist (`_tools/sync_staging.py`). Maintainer tools stay in `_tools/` and never ship.
- Removed unused scripts: `score_fit`, `combine_recommendations`, `smoke_test_plan`, `validate_bindings`.
