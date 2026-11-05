# Phase 3 — Discover Inventory (cheap, fast, shallow)

Produce one `account_inventory.json` from a metadata-only crawl. This is the single most important
efficiency guarantee of the hydrator: discover once, reuse for scoring (Phase 4) and validation
(Phase 6). No warehouse compute, no Cortex Sense, no data reads.

## Hard rules

- **`timeout_seconds: 300` on EVERY `snowflake_sql_execute` call in this phase.** Guards against a
  giant schema or a data-heavy account. On timeout: record a `gap` for that data-need key and move
  on — do NOT widen the query, lower specificity, or retry broadly.
- **Metadata only:** `SHOW`, `DESCRIBE`, `RESULT_SCAN`, and one batched `ACCESS_HISTORY` count.
  No `SELECT` against business tables in this phase (that is Phase 6's job).
- **Never `SHOW ... IN ACCOUNT`.** Always scope to the Phase-2 `scope.databases`, or to
  `scope.schemas` when the user named schemas.
- **`SHOW` → `RESULT_SCAN` is strictly sequential.** `RESULT_SCAN(LAST_QUERY_ID())` reads the result
  of the previous query *in the same session*. Run each `SHOW` and then its `RESULT_SCAN` back to
  back, one pair at a time, on the same connection. Never run `SHOW` calls in parallel and never put
  another statement between a `SHOW` and its `RESULT_SCAN`, or `LAST_QUERY_ID()` points at the wrong
  result.
- **Official data only** (see Step 2's deny-list) — regardless of name match or usage.

## Step 1 — Concept keywords

Union the `concepts` phrases and `key` stems from every `catalog/*.json` file into a small keyword
set (e.g. `invoice`, `journal`, `ledger`, `aging`, `receivable`, `payable`, `forecast`, `budget`,
`cogs`, `revenue`, `customer`, `calendar`). Cap at ~2–3 variants per data-need key.

## Step 2 — Table and view sweep

Choose the scope clause once:

- `scope.schemas` set (e.g. `["ERP_PROD.GL", "ERP_PROD.AP"]`) → `IN SCHEMA <db>.<schema>` for each.
- otherwise → `IN DATABASE <db>` for each entry in `scope.databases`.

For each keyword stem and each scope entry, run these pairs **sequentially**:

```sql
SHOW TERSE TABLES LIKE '%<stem>%' IN DATABASE <db>;   -- or IN SCHEMA <db>.<schema>
SELECT "database_name"||'.'||"schema_name"||'.'||"name" AS fqn, "kind"
FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));

SHOW TERSE VIEWS LIKE '%<stem>%' IN DATABASE <db>;    -- or IN SCHEMA <db>.<schema>
SELECT "database_name"||'.'||"schema_name"||'.'||"name" AS fqn, "kind"
FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));
```

Then drop non-official candidates without touching data. Match **whole tokens**: split each FQN part
on `.` and `_` and compare tokens case-insensitively against the deny-list — never use substring
`LIKE '%dev%'`, which wrongly drops names such as `DEVICE` or `CONTEST`.

| Deny-list (whole tokens) | Also disqualify |
|---|---|
| `dev`, `demo`, `test`, `sandbox`, `scratch`, `tmp`, `temp`, `bkp`, `backup`, `clone`, `snapshot`, `archive` | Names ending in a date suffix `_YYYYMMDD` or `_YYYYMM`; per-user scratch schemas |

Examples: `DEV_FINANCE.GL.LINES`, `ERP.SNAPSHOT.AP`, `ERP.AR.AGING_20260101` are dropped;
`DEVICE_SALES.GL.LINES` is kept.

**Staging and raw layers** (`STG_*`, `STAGING`, `RAW`, `LANDING`, `BRONZE`, `PREP_*`, `SNAP_*`) are
disqualified unless the user confirms that layer is the official, reporting-grade source for that
data. Ask once; if confirmed, set `staging_confirmed: true` on each such inventory object. The
scorer blocks these tokens as `NON_OFFICIAL` otherwise.

`score_readiness.py` re-applies the same token deny-list to every bound FQN and blocks the need with
`block_kind: NON_OFFICIAL` even if the inventory says `"official": true`, so a slip here cannot reach
the generated plugin.

`snowflake_object_search` on the same key nouns is a valid supplementary nominator; apply the same
official-only filter to its results.

## Step 2b — Stored procedure sweep (procedure-backed data needs only)

Some data needs may be backed by a stored procedure rather than a table — specifically
`accounting.recon.profile_store`, whose `context_queries` description mentions "stored procedure".
Run this step **only for those keys**, after the table/view sweep, keeping each `SHOW` and its
`RESULT_SCAN` back to back:

```sql
SHOW PROCEDURES LIKE '%recon%' IN SCHEMA <db>.<schema>;
SELECT "catalog_name"||'.'||"schema_name"||'.'||"name" AS fqn, "arguments"
FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))
WHERE "is_builtin" = 'N';
```

Repeat with `'%profile%'`. Filter `is_builtin = 'N'` so system procedures never match. A profile store
normally has a GET (read) and a SAVE procedure: **bind the GET/read procedure** as the data-need
`fqn` (names containing `GET`, `READ`, `LOAD`, or `LIST`) and record the SAVE procedure under the
object's `extra.save_procedure`. Apply the same official-only filter. If a matching procedure is
found:

- Record it as an inventory object with **`"object_type": "procedure"`** and
  **`"columns_present": {}`** — procedure return schemas cannot be described at inventory time;
  the skill discovers output columns at runtime via CALL.
- Skip Step 3 (DESCRIBE) for this entry.
- Set confidence **MEDIUM** (procedure located but output schema unverified).
- If a table match was also found for the same key, prefer the table (confidence is fully
  verifiable via DESCRIBE); record the procedure as a secondary note in `gaps`.

Procedure FQN format: `<db>.<schema>.<procedure_name>` (no argument signature; the skill
resolves overloads at runtime).

## Step 3 — Confirm columns once (cap ≤3 per data-need key)

For the top surviving **table/view** candidates per data-need key (skip procedure entries — they
have no describable columns):

```sql
DESCRIBE TABLE <fqn>;   -- DESCRIBE VIEW <fqn> for views
```

Map each catalog field of the need (`fields` with `aliases`, or legacy `columns`) to a real column.
Aliases list the common ERP names (NetSuite, SAP, Oracle, Workday, Dynamics) in preference order;
the `comment` column on each field is useful context. When no alias matches, a column may still be
mapped by judgment if its name or comment clearly means the same thing.

**Required format — `columns_present` is a `{canonical: physical}` mapping**, keyed by the catalog
field name, with the exact physical column name as the value:

```json
"columns_present": {"period": "POSTING_PERIOD", "account": "GL_ACCOUNT", "amount": "NET_AMOUNT"}
```

Include only fields you actually mapped; leave unmapped fields out (never write placeholders). Keep
quoted mixed-case identifiers exactly as Snowflake reports them. A flat list of physical names is
still accepted by the scorer for backward compatibility, but then only exact alias matches resolve,
so always prefer the mapping.

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

Reading `ACCESS_HISTORY` requires `IMPORTED PRIVILEGES` on the `SNOWFLAKE` database or the
`GOVERNANCE_VIEWER` database role. If the query is denied, do not ask for elevated access or retry
with another role: record a gap (`{"data_need_key": "*", "reason": "ACCESS_HISTORY not readable by
<role>; usage unverified"}`), skip usage, cap confidence at MEDIUM, and continue.

Assign `confidence`: HIGH (official + all required columns present + recent multi-user access),
MEDIUM (official + required columns present but light/older usage, or usage unverifiable), LOW
(official but thin column match or single nominator).

## Step 5 — Emit account_inventory.json

Write `account_inventory.json` in the shape from `engine/references/output_contract.md`: `scope`,
`domain_access`, `objects[]` (exactly **one per resolved data-need key** with `fqn`,
`columns_present`, `official`, `usage`, `confidence`), and `gaps[]` (unresolved or timed-out needs).
This single file is the input to Phase 4 and Phase 6. `score_readiness.py` rejects duplicate
`data_need_key` entries.

**Setup-eligible gaps:** If `accounting.recon.profile_store` ends up in `gaps[]` — that is,
neither a matching table (Step 2) nor a matching procedure (Step 2b) was found in scope — add
`"setup_eligible": true` to that gap entry. This flag signals Phase 4 (`recommend_skills.md`) to
offer the user the option to create the profile store rather than simply excluding
`recon_analysis` as BLOCKED.

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
recommend here — that is Phase 4.

**Next:** load `subskills/recommend_skills.md`, passing `account_inventory.json`.
