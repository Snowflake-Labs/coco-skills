# Phase 2 — Discover Inventory (cheap, fast, shallow)

Produce one `account_inventory.json` from a metadata-only crawl. This is the single most important
efficiency guarantee of the hydrator: discover once, reuse for scoring (Phase 3) and validation
(Phase 5). No warehouse compute, no Cortex Sense, no data reads.

## Hard rules

- **`timeout_seconds: 300` on EVERY `snowflake_sql_execute` call in this phase.** Guards against a
  giant schema or a data-heavy account. On timeout: record a `gap` for that data-need key and move
  on — do NOT widen the query, lower specificity, or retry broadly.
- **Metadata only:** `SHOW`, `DESCRIBE`, `RESULT_SCAN`, and one batched `ACCESS_HISTORY` count.
  No `SELECT` against business tables in this phase (that is Phase 5's job).
- **Never `SHOW ... IN ACCOUNT`.** Always scope to the Phase-1 databases.
- **Official data only.** Discard dev/demo/test/sandbox/backup, staging/raw, per-user scratch,
  dated snapshots — regardless of usage.

## Step 1 — FinOps shortcut (near-zero cost)

The FinOps catalog binds to fixed `SNOWFLAKE.ACCOUNT_USAGE.*` objects, so no keyword crawl is
needed — just an existence/access probe (run as the Phase-1 role):

```sql
SELECT table_schema, table_name
FROM SNOWFLAKE.ACCOUNT_USAGE.TABLES
WHERE table_schema = 'ACCOUNT_USAGE'
  AND table_name IN ('WAREHOUSE_METERING_HISTORY','METERING_DAILY_HISTORY','QUERY_HISTORY');
```

- Rows returned → set `domain_access.finops = true` and add an inventory object for each matched
  FinOps data-need key (map: `finops.account_usage.metering` and
  `finops.account_usage.warehouse_metering` → `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY`;
  `finops.account_usage.daily_metering` → `METERING_DAILY_HISTORY`;
  `finops.account_usage.query_history` → `QUERY_HISTORY`). Confidence HIGH (these are system
  views). `finops.cost_allocation.mapping` has no ACCOUNT_USAGE equivalent → leave as a gap unless
  the user names a mapping table.
- Error / empty → `domain_access.finops = false`; skip FinOps objects.

## Step 2 — Concept-index sweep (accounting + strategic finance)

These domains live in customer-specific schemas. Union the `concepts` phrases and `key` stems from
`catalog/accounting.json` + `catalog/strategic_finance.json` into a small keyword set (e.g.
`invoice`, `journal`, `ledger`, `aging`, `forecast`, `budget`, `cogs`, `revenue`, `customer`).
Cap at ~2–3 variants per data-need key.

Per candidate database from `scope.databases`:

```sql
SHOW TERSE TABLES LIKE '%<stem>%' IN DATABASE <db>;
```

Immediately filter non-official candidates without touching data:

```sql
SELECT "database_name"||'.'||"schema_name"||'.'||"name" AS fqn
FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))
WHERE NOT (LOWER("database_name"||'.'||"schema_name"||'.'||"name")
           LIKE ANY ('%dev%', '%demo%', '%test%', '%sandbox%', '%backup%'));
-- also drop staging/raw, clones, dated snapshots, and per-user scratch by judgment.
```

Run independent `SHOW` calls in parallel where possible. `snowflake_object_search` on the same key
nouns is a valid supplementary nominator; apply the same official-only filter to its results.

## Step 2b — Stored procedure sweep (procedure-backed data needs only)

Some data needs may be backed by a stored procedure rather than a table — specifically
`accounting.recon.profile_store`, whose `context_queries` description mentions "stored procedure".
Run this step **only for those keys**, in parallel with the table sweep:

Per candidate schema in the scoped databases:

```sql
SHOW PROCEDURES LIKE '%recon%' IN SCHEMA <db>.<schema>;
SHOW PROCEDURES LIKE '%profile%' IN SCHEMA <db>.<schema>;
```

Apply the same official-only filter (discard dev/test/demo/sandbox). If a matching procedure is
found:

- Record it as an inventory object with **`"object_type": "procedure"`** and
  **`"columns_present": {}`** — procedure return schemas cannot be described at inventory time;
  the skill discovers output columns at runtime via CALL.
- Skip Step 3 (DESCRIBE TABLE) for this entry.
- Set confidence **MEDIUM** (procedure located but output schema unverified).
- If a table match was also found for the same key, prefer the table (confidence is fully
  verifiable via DESCRIBE); record the procedure as a secondary note in `gaps`.

Procedure FQN format: `<db>.<schema>.<procedure_name>` (no argument signature; the skill
resolves overloads at runtime).

## Step 3 — Confirm columns once (cap ≤3 per data-need key)

For the top surviving **table** candidates per data-need key (skip procedure entries — they have
no describable columns):

```sql
DESCRIBE TABLE <fqn>;
```

Match the catalog need's `columns` (required + optional) to real column names. The `comment`
column on each field is useful context. Record `columns_present` as the actual column names found.

## Step 4 — One batched usage check

Confirm recent usage for ALL surviving FQNs in a single query (not per table):

```sql
SELECT f.value:objectName::string AS fqn,
       COUNT(*) AS events_7d,
       COUNT(DISTINCT user_name) AS distinct_users,
       MAX(query_start_time) AS last_access
FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY,
     LATERAL FLATTEN(input => base_objects_accessed) f
WHERE query_start_time >= DATEADD(day, -7, CURRENT_TIMESTAMP())
  AND f.value:objectName::string IN (<comma-separated FQNs>)
GROUP BY 1;
```

Assign `confidence`: HIGH (official + all required columns present + recent multi-user access),
MEDIUM (official + required columns present but light/older usage), LOW (official but thin column
match or single nominator). If `ACCESS_HISTORY` is not readable by the role, skip usage and cap
confidence at MEDIUM (note the degradation).

## Step 5 — Emit account_inventory.json

Write `account_inventory.json` in the shape from `references/output_contract.md`: `scope`,
`domain_access`, `objects[]` (one per resolved data-need key with `fqn`, `columns_present`,
`official`, `usage`, `confidence`), and `gaps[]` (unresolved or timed-out needs). This single file
is the input to Phase 3 and Phase 5.

**Setup-eligible gaps:** If `accounting.recon.profile_store` ends up in `gaps[]` — that is,
neither a matching table (Step 2) nor a matching procedure (Step 2b) was found in the scoped
databases — add `"setup_eligible": true` to that gap entry. This flag signals Phase 4
(`recommend_skills.md`) to offer the user the option to create the profile store rather than
simply excluding `recon_analysis` as BLOCKED.

```json
// Example gap entry for a setup-eligible missing profile store
{
  "data_need_key": "accounting.recon.profile_store",
  "reason": "no official match found (table or procedure)",
  "setup_eligible": true
}
```

Only `accounting.recon.profile_store` is currently setup-eligible. All other gaps are written
without the flag.

## Checkpoint

Present a one-line-per-object summary and the `gaps`. Confirm before moving on. Do NOT score or
recommend here — that is Phase 3.

**Next:** load `subskills/recommend_skills.md`, passing `account_inventory.json`.
