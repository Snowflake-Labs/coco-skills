# Setup Profile Store

This sub-phase is loaded from `recommend_skills.md` only when the user picks **Setup** for a
BLOCKED recon_analysis skill. It creates the full recon_analysis-compatible profile store infrastructure:
one backing table and two stored procedures (GET + SAVE), matching the schema and signatures
documented in `setup_ddl.md`.

Do NOT trigger this sub-phase independently. It expects `profile` and `scope` state from Phases 1–2
and the `account_inventory.json` path from Phase 3.

---

## Step 1 — Explain and confirm intent

Tell the user:

> The `recon_analysis` skill needs a profile store to save reconciliation configurations. No
> existing store was found in your account.
>
> Finance Hydrator can create the full recon_analysis-compatible profile store:
>
> | Object | Type | Purpose |
> |---|---|---|
> | `<TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>` | Table | Append-only config store (one row per profile version) |
> | `<PROC_DB>.<PROC_SCHEMA>.<GET_PROC_NAME>` | Procedure | List and load profiles |
> | `<PROC_DB>.<PROC_SCHEMA>.<SAVE_PROC_NAME>` | Procedure | Insert, update, and soft-delete profiles |
>
> This requires `CREATE TABLE` on the table schema and `CREATE PROCEDURE` on the procedure schema.
> If you lack either privilege, the DDL will be shown for an admin to run manually.

Ask:
```json
{
  "questions": [{
    "header": "Create store?",
    "question": "Create the recon profile store in your account?",
    "type": "options",
    "options": [
      {"label": "Yes, create it", "description": "Finance Hydrator will generate and execute the DDL"},
      {"label": "No, skip recon_analysis", "description": "Exclude recon_analysis from this plugin"}
    ]
  }]
}
```

If "No, skip recon_analysis" → return to `recommend_skills.md` and exclude the skill. Stop here.

---

## Step 2 — Collect parameters

Pre-fill from the Phase 3 inventory scope (the database where most accounting objects were found).

```json
{
  "questions": [
    {
      "header": "Table DB.Schema",
      "question": "Schema for the backing table (DB.SCHEMA):",
      "type": "text",
      "defaultValue": "<discovered_db>.STREAMLIT_DATA"
    },
    {
      "header": "Table name",
      "question": "Name for the backing table:",
      "type": "text",
      "defaultValue": "RECON_USER_PROFILES"
    },
    {
      "header": "Proc DB.Schema",
      "question": "Schema for the GET and SAVE procedures (DB.SCHEMA):",
      "type": "text",
      "defaultValue": "<discovered_db>.<accounting_schema>"
    },
    {
      "header": "GET proc name",
      "question": "Name for the GET (read) procedure:",
      "type": "text",
      "defaultValue": "RECON_GET_PROFILES"
    },
    {
      "header": "SAVE proc name",
      "question": "Name for the SAVE (write) procedure:",
      "type": "text",
      "defaultValue": "RECON_SAVE_PROFILE"
    },
    {
      "header": "Grant role",
      "question": "Role to GRANT USAGE on the procedures:",
      "type": "text",
      "defaultValue": "<Phase-2 role>"
    }
  ]
}
```

Also ask for execution mode:
```json
{
  "questions": [{
    "header": "Execute as",
    "question": "How should the procedures execute?",
    "type": "options",
    "options": [
      {
        "label": "EXECUTE AS OWNER",
        "description": "Procedures run as the owner's role. Backing table stays private — users never need direct table access. Recommended."
      },
      {
        "label": "EXECUTE AS CALLER",
        "description": "Procedures run as the calling user's role. The backing table must be GRANTed directly to each user. Simpler to audit but requires broader table grants."
      }
    ]
  }]
}
```

Derive the four placeholder values from user answers:
- `<TABLE_DB>` and `<TABLE_SCHEMA>` — split the "Table DB.Schema" input on `.`
- `<TABLE_NAME>` — "Table name" input, uppercased
- `<PROC_DB>` and `<PROC_SCHEMA>` — split "Proc DB.Schema" on `.`
- `<GET_PROC_NAME>` — "GET proc name" input, uppercased
- `<SAVE_PROC_NAME>` — "SAVE proc name" input, uppercased
- `<GRANT_ROLE>` — "Grant role" input
- `<EXECUTE_MODE>` — `OWNER` or `CALLER` from the options answer

---

## Step 3 — Validate schemas exist

Verify both target schemas are present before generating DDL:

```sql
-- Table schema
SELECT COUNT(*) AS n FROM <TABLE_DB>.INFORMATION_SCHEMA.SCHEMATA
WHERE UPPER(SCHEMA_NAME) = UPPER('<TABLE_SCHEMA>');

-- Procedure schema
SELECT COUNT(*) AS n FROM <PROC_DB>.INFORMATION_SCHEMA.SCHEMATA
WHERE UPPER(SCHEMA_NAME) = UPPER('<PROC_SCHEMA>');
```

If a schema is missing, ask:
```json
{
  "questions": [{
    "header": "Missing schema",
    "question": "<SCHEMA> does not exist in <DB>. Create it?",
    "type": "options",
    "options": [
      {"label": "Yes, create it", "description": "Adds CREATE SCHEMA to the DDL"},
      {"label": "No, I'll enter a different schema", "description": "Go back to Step 2"}
    ]
  }]
}
```

If "Yes" — prepend `CREATE SCHEMA IF NOT EXISTS <DB>.<SCHEMA>;` to the DDL block for that
schema. If "No" — return to Step 2.

---

## Step 4 — Show full DDL and confirm (CHECKPOINT)

Generate the DDL by substituting all placeholders in `subskills/setup_ddl.md`.

Display each statement with a numbered header and syntax-highlighted SQL block:

```
STATEMENT 1 OF 4 — Backing Table
CREATE TABLE IF NOT EXISTS ...

STATEMENT 2 OF 4 — GET_PROFILES Procedure
CREATE OR REPLACE PROCEDURE ...

STATEMENT 3 OF 4 — SAVE_PROFILE Procedure
CREATE OR REPLACE PROCEDURE ...

STATEMENT 4 OF 4 — GRANT USAGE on Procedures
GRANT USAGE ON PROCEDURE ...
GRANT USAGE ON PROCEDURE ...
```

If `EXECUTE AS OWNER` is selected, add a note:
> Because these procedures use EXECUTE AS OWNER, the backing table does not need to be granted
> to individual users. The procedure owner must have INSERT and SELECT on the table.

If `EXECUTE AS CALLER` is selected, add an additional GRANT statement and note:
> GRANT SELECT, INSERT ON TABLE <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME> TO ROLE <GRANT_ROLE>;
> (Required because EXECUTE AS CALLER means the table is accessed with the caller's privileges.)

**MANDATORY STOP.** Ask for confirmation before any execution:
```json
{
  "questions": [{
    "header": "Confirm",
    "question": "Execute this DDL in your Snowflake account?",
    "type": "options",
    "options": [
      {"label": "Yes, execute", "description": "Run all statements now"},
      {"label": "Export script only", "description": "Show me the DDL to run manually — skip execution"},
      {"label": "Cancel", "description": "Go back to skill selection"}
    ]
  }]
}
```

If "Export script only" — display the full DDL as a copyable code block labeled
`-- Run manually with a role that has CREATE TABLE and CREATE PROCEDURE on the target schemas.`
then offer to save it to a file. Return to `recommend_skills.md` and exclude recon_analysis
(the store has not been created yet).

If "Cancel" — return to `recommend_skills.md`.

---

## Step 5 — Execute DDL

Run each statement in order. Use `timeout_seconds: 120` on each.

**Statement 1 — table:**
```sql
CREATE TABLE IF NOT EXISTS <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME> (
    PROFILE_NAME     VARCHAR(255),
    PROFILE_OWNER    VARCHAR(255),
    PROFILE_SETTINGS VARIANT,
    IS_PUBLIC        BOOLEAN,
    UPDATED_AT       TIMESTAMP_NTZ(9),
    IS_DELETED       BOOLEAN DEFAULT FALSE
)
COMMENT = 'Reconciliation profile store — created by Finance Hydrator';
```

After table creation, verify before creating procedures:
```sql
SELECT COUNT(*) AS n
FROM <TABLE_DB>.INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = UPPER('<TABLE_SCHEMA>')
  AND TABLE_NAME   = UPPER('<TABLE_NAME>');
```
If count = 0 → stop, report the error, do not attempt procedure creation.

**Statement 2 — GET procedure** (substitute full body from `subskills/setup_ddl.md` Section B).

**Statement 3 — SAVE procedure** (substitute full body from `subskills/setup_ddl.md` Section C).

**Statement 4 — GRANTs** (from `subskills/setup_ddl.md` Section D).

On any failure: surface the exact Snowflake error message and stop. Offer:
> "Would you like me to export the remaining statements as a script for an admin to run?"

---

## Step 6 — Update inventory and re-score

After all statements succeed, update `account_inventory.json`:

1. Remove the `accounting.recon.profile_store` entry from `gaps[]`.

2. Append to `objects[]`:
```json
{
  "data_need_key": "accounting.recon.profile_store",
  "fqn": "<PROC_DB>.<PROC_SCHEMA>.<GET_PROC_NAME>",
  "object_type": "procedure",
  "columns_present": {},
  "official": true,
  "confidence": "HIGH",
  "extra": {
    "save_procedure": "<PROC_DB>.<PROC_SCHEMA>.<SAVE_PROC_NAME>",
    "backing_table": "<TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>"
  }
}
```

3. Re-run `score_readiness.py` with the updated inventory, passing all catalog files present in `catalog/`:
```bash
python3 engine/scripts/score_readiness.py \
  --inventory <path>/account_inventory.json \
  $(for f in catalog/*.json; do echo "--catalog $f"; done)
```
(Omit any catalog file that does not exist in this plugin's `catalog/` directory.)

4. Report to the user:
> Profile store created successfully.
> - Table: `<TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>`
> - GET procedure: `<PROC_DB>.<PROC_SCHEMA>.<GET_PROC_NAME>`
> - SAVE procedure: `<PROC_DB>.<PROC_SCHEMA>.<SAVE_PROC_NAME>`
>
> recon_analysis is now **[PARTIAL/READY]** — returning to skill selection.

---

## Step 7 — Write procedure FQNs into hydration_hints.yaml (post-build)

After `build_customer_plugin.py` has written the customer plugin (Phase 5), the hydration_hints.yaml
for recon_analysis will contain the GET procedure FQN in `sources[0].fqn`. Augment `extra_settings`
in that file directly to also record the SAVE procedure:

```python
# Pseudocode — executed as part of this setup sub-phase after Phase 5 completes
hints_path = Path(generated_plugin_dir) / "skills" / "recon_analysis" / "hydration_hints.yaml"
data = _minyaml.load(hints_path.read_text())
need = data["data_needs"]["accounting.recon.profile_store"]
extra = need.setdefault("extra_settings", {})
extra["get_procedure"]  = "<PROC_DB>.<PROC_SCHEMA>.<GET_PROC_NAME>"
extra["save_procedure"] = "<PROC_DB>.<PROC_SCHEMA>.<SAVE_PROC_NAME>"
extra["backing_table"]  = "<TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>"
extra["access_mode"]    = "procedure"
hints_path.write_text(_minyaml.dump(data))
```

This means the deployed recon_analysis skill's `hydration_hints.yaml` will contain both procedure
names so the skill knows exactly how to call each operation without further discovery.

---

## Guardrails

- **Never execute without explicit user confirmation** at Step 4.
- **IF NOT EXISTS / CREATE OR REPLACE** on all objects — idempotent.
- **Use only the Phase-2 role** for DDL. Never attempt SYSADMIN/ACCOUNTADMIN operations.
- **Stop on any error** — do not skip failed statements.
- **No data reads from the new (empty) table** — validation happens in Phase 6 on the skill's real
  profile data, not here.
- **Do not widen scope** — only create objects in the schemas the user confirmed in Step 2.

**Next:** return to `recommend_skills.md` with the updated `account_inventory.json`.
