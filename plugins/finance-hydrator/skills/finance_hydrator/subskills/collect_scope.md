# Phase 2 — Collect Scope

Gather the inputs the discovery crawl needs. The previous phase already collected `profile`; use
that to suggest likely scopes (for example, `SNOWFLAKE.ACCOUNT_USAGE` for FinOps, finance/ERP
databases for FP&A/AP/Controller). Keep this to one round of questions.

Use `ask_user_question` to collect:

- **Snowflake connection name** — which configured connection to run discovery against.
- **Active role to test** — discovery and validation run as this role; RBAC is respected, so only
  data this role can read will resolve.
- **Candidate database/schema scope** — one or more databases (optionally schemas) to crawl.
  Prefer scoped schemas over account-wide. Offer `SNOWFLAKE.ACCOUNT_USAGE` as the default FinOps
  scope; ask for the customer's finance/GL/AP/AR/FP&A database(s) for the other two domains.
- **Output destination** — local folder for the generated customer plugin (default:
  `/tmp/customer-finance`).

Record these as `scope`:

```json
{"connection": "...", "role": "...", "databases": ["..."], "output_dir": "/tmp/customer-finance"}
```

## Guardrails

- Never proceed to a broad crawl without at least one candidate database (or the ACCOUNT_USAGE
  default). If the user is unsure, list databases with `SHOW TERSE DATABASES;` and let them pick.
- This is a CHECKPOINT: confirm the scope before Phase 3 runs any discovery.

**Next:** load `subskills/discover_inventory.md`, passing `profile` + `scope`.
