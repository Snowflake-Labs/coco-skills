# Phase 2 — Collect Scope

Gather the inputs the discovery crawl needs. The previous phase already collected `profile`; use
that to suggest likely scopes (for example, finance/ERP/GL/AP/AR databases for Controller and AP/AR
personas, planning/FP&A databases for CFO and FP&A). Keep this to one round of questions.

Use `ask_user_question` to collect:

- **Snowflake connection name** — which configured connection to run discovery against.
- **Active role to test** — discovery and validation run as this role; RBAC is respected, so only
  data this role can read will resolve.
- **Candidate database scope** — one or more databases to crawl. Ask for the customer's
  finance/GL/AP/AR/FP&A database(s).
- **Optional schema scope** — specific `<DATABASE>.<SCHEMA>` entries. When given, discovery runs
  `IN SCHEMA` against only these schemas instead of `IN DATABASE`, which is faster and avoids
  dev/staging schemas living in the same database. Prefer scoped schemas over whole databases.
- **Output destination** — local folder for the generated customer plugin (default:
  `/tmp/customer-finance`). It must be new, empty, or a previous Finance Hydrator output; the
  builder refuses to overwrite any other non-empty folder.

Record these as `scope`:

```json
{"connection": "...", "role": "...", "databases": ["..."], "schemas": ["DB.SCHEMA"], "output_dir": "/tmp/customer-finance"}
```

`schemas` is optional; omit it (or use `[]`) to crawl whole databases.

## Guardrails

- Never proceed to a crawl without at least one candidate database. If the user is unsure, list
  databases with `SHOW TERSE DATABASES;` and let them pick.
- This is a CHECKPOINT: confirm the scope before Phase 3 runs any discovery.

**Next:** load `subskills/discover_inventory.md`, passing `profile` + `scope`.
