# Eval Plan: recon_analysis (Hydration Test)

## Purpose

Verify that `<customer-plugin>/skills/recon_analysis/` (dehydrated)
can be hydrated against a Snowflake account containing a reconciliation profile store
(table or procedure-backed). The hydrator must discover the profile store, resolve
access mode (table vs procedure), and optionally offer to create the infrastructure
if it does not exist.

## Verification Harness

```bash
# Step 1 — Hydrate against <SNOWFLAKE_CONNECTION> with <ROLE_WITH_FINANCE_READ>
cortex -p "Use <HYDRATOR_SKILL> to hydrate <customer-plugin>/skills/recon_analysis/. \
Use connection <SNOWFLAKE_CONNECTION> with role <ROLE_WITH_FINANCE_READ>."

# Step 2 — Run golden cases against hydrated skill
cortex -p "Run the golden cases from <customer-plugin>/evals/recon_analysis/golden_cases.md \
against the hydrated recon_analysis skill."
```

## Pass Criteria

| # | Check | Pass |
|---|---|---|
| H1 | Profile store resolved (table or procedure FQN in hydration_hints.yaml) | |
| H2 | If procedure-backed: `object_type: procedure` in binding, no column mismatch | |
| H3 | If profile store missing: setup path offered (not hard BLOCKED) | |
| H4 | All golden cases return expected behavior (see golden_cases.md) | |
| H5 | No fabricated FQNs — all bindings reference real objects | |
| H6 | Read-only guardrail respected — no DML outside profile CRUD | |
