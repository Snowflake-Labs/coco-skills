# Architecture notes

Reference material for how this app's embedded agent works internally. Not needed to deploy the app (that's `SKILL.md`) or to help someone after deploy (that's `AGENT_PLAYBOOK.md`) — only relevant if you need to understand or modify the app's own agent integration.

## How the embedded Cortex Chat backend works

The embedded Cortex Chat features (setup agent + dashboard chat) call
`SNOWFLAKE.CORTEX.DATA_AGENT_RUN` with the `code_toolset_all` tool type,
through the app's existing `lib/snowflake.ts` connection. This gives
the agent the same bash/read/write/edit/grep/glob/web_search/SQL/skill
toolset that powers Cortex Code, run entirely on Snowflake's managed backend.
`code_toolset_all` provides the agent the managed Cortex Code toolset and
requires `code_toolset_all` access on the deployment account. The app treats
it as a standard part of its deployment target. The app probes availability once per container lifetime (cached,
so this never adds per-turn latency); if the probe fails, the app transparently
uses the vendored CLI fallback described below instead of failing the turn.

This app also has a **vendored Cortex Code (CoCo) CLI fallback**, for
accounts where `code_toolset_all` REST access isn't available.
`isRestBackendAvailable()` probes the REST path once per container
lifetime; if that probe fails, every agent turn transparently uses
`runTurnCli()` (`lib/agent-backend.ts`) instead, which spawns the vendored
CLI (`scripts/vendor-cortex-cli.sh`) as a subprocess. Both backends produce
the same event shapes, so nothing above this layer needs to know which one
ran. See README.md §2 for the full history and security differences
between the two paths.

### Permission guardrails

`code_toolset_all` is broad by design — it bundles bash, file read/write/edit,
grep/glob, web search, and a `snowflake_sql_execute` tool. That's more surface
area than this app needs, so it's locked down:

- `permission_policy` is set to `always_ask`, not `always_allow`. Every tool
  call requires an explicit approval before it runs.
- The app's own backend code auto-responds to that approval request: it
  approves `snowflake_sql_execute` (platform-enforced read-only, SELECT/SHOW
  only) and denies everything else, with a message back to the model
  explaining the agent is read-only in this session.
- The `dashboard` and `streamlit` bundled skills are disabled, since neither
  is relevant here.

This is a real security boundary, not just a system-prompt instruction —
`always_allow` skips approval for every tool in the bundle, including `bash`,
`write`, and `edit`, which have no SQL-specific restriction at all. Never use
`always_allow` for this reason.

### The capability boundary this creates

Because the read-only-SQL-only restriction above is enforced for every
embedded agent turn (setup agent, dashboard chat, custom-logic agent — see
README.md §14), none of them can ever write to this app's own source code or
trigger a redeploy, no matter how the request is phrased. That's a hard
design boundary, not a gap: a request for an actual new UI/chart type, a
restructured page, or new component behavior always needs a real
CoCo/engineering session against this app's source — see
`AGENT_PLAYBOOK.md`'s "Customer wants a new feature or customization"
section for how to tell that apart from something the embedded agent can
already do live (most natural-language customization requests turn out to
be the latter).

### Caller's rights vs. owner's rights

The permission guardrails above constrain *what* the agent's one approved
tool can do (read-only SQL). A separate, orthogonal question is *whose*
Snowflake identity that SQL runs as — see `lib/agent-access-mode.ts`.

- **Default: caller's rights.** `runAgentTurn()` builds a combined token
  (`serviceToken + "." + callerToken`) from the `Sf-Context-Current-User-Token`
  header App Runtime injects, and both backends run every query as that
  signed-in viewer. Restricted Caller's Rights means the *effective*
  privilege is the intersection of the viewer's own grants and whatever
  `GRANT CALLER` privileges the app's owner role holds — there is no
  fallback if that intersection is empty; the query just fails.
- **Opt-in: owner's rights.** An admin (a viewer whose own role is/contains
  the app's owner role — checked via
  `IS_ROLE_IN_SESSION()` under the caller's own token, never trusted from a
  client) can flip a dedicated, single-row table
  (`getModeTable()`/`ensureModeTable()` in `lib/agent-access-mode.ts`) via
  `app/api/agent-access-mode`. When set, `runAgentTurn()` skips the
  fail-closed caller-token guard and forces every token-resolution point in
  both backends (`getAuthToken()`, `runTurnCli()`'s `effectiveCallerToken`)
  to ignore the caller token entirely for that turn.
- **Why this mode lives in its own table, not `BvaConfig`:** `BvaConfig`
  round-trips through the setup agent's own JSON proposal
  (`onConfigAccepted` in `components/cortex-chat.tsx`), which a human
  approves — but an indirect prompt injection (adversarial text in a
  scanned table/column comment) could in principle bias what gets proposed.
  A security-relevant toggle has no business being inside a blob an LLM can
  propose values for, even if a human is meant to review it. It's also not
  a row in the `BVA_APP_CONFIG` table used by `app/api/setup/config`,
  because that route's `GET` is open to any signed-in viewer by design (the
  dashboard needs to read the active config to render) — reusing that table
  would let anyone read (though not write; `POST`/`DELETE` there are
  admin-gated) the mode via the same open `GET`.
- **Not reachable by the agent itself.** The Cortex Chat agent's only tool
  is the read-only `snowflake_sql_execute` — it has no tool that can call
  this app's own API routes, so no amount of prompt injection can flip this
  setting from inside a chat turn. Changing it is only ever a direct,
  admin-authenticated action against `app/api/agent-access-mode` (surfaced
  in the Settings UI as `components/agent-access-controls.tsx`, which renders
  nothing for non-admins).

## How the setup agent works

The embedded Cortex Chat agent guides users through a structured 5-phase discovery process to map their Snowflake tables into a valid BvA config.

---

### Phase 1 — Find candidate tables

The agent scans `INFORMATION_SCHEMA.TABLES` across financial-sounding databases, filtering on keywords like GL, JOURNAL, BUDGET, FORECAST, ADAPTIVE, FPA, etc. It runs a narrow keyword-filtered scan first and only widens to a full schema scan if no plausible GL + budget pair is found.

**Ranking logic — schema tiers (applied before row count or column evidence):**

| Tier | Schema name contains | Signal |
|------|---------------------|--------|
| 1 — highest | ACCOUNTING, FINANCE, FPA, GL, LEDGER, BUDGET, PLANNING, ERP | Purpose-built finance schemas |
| 2 | STG, PREP, MART, DWH, GOLD, SILVER, ANALYTICS, CURATED, REPORTING | Transformed/curated layer — prefer over raw for the same concept |
| 3 | RAW, BRONZE, INGEST, LANDING, or source-system prefixes (SAP_, NETSUITE_, ADAPTIVE_) | Raw ingestion — valid but deprioritized if a curated version exists |
| 4 — lowest | DEV, TEST, SANDBOX, TEMP, BACKUP, personal schemas | Almost never correct |

**Deduplication rule:** if a raw (Tier 3) table and a curated (Tier 1/2) table cover the same concept (e.g. `MY_RAW_SCHEMA.COST_CENTER_MAPPING` vs `MY_STG_SCHEMA.COST_CENTER_MAPPING`), only the curated one gets `recommended: true`. The raw one is listed as a fallback.

Candidates are surfaced in a selection widget — one picker per table type: GL/Actuals, Budget, Cost Center mapping, Ledger Account mapping.

---

### Phase 2 — Map columns

Once the user selects tables, the agent `DESCRIBE`s each one and maps every required column role from column names alone — date, amount, account code, fiscal columns, budget period, version, etc. Cost center ID is mapped too when the org tracks cost centers, but it's optional — some orgs don't have one, and the wizard treats a missing cost center mapping as a warning, not a blocker, same as ledger account mapping. A sample query runs only when two columns are genuinely ambiguous for the same role.

The result is a pre-filled confirmation widget. The user can correct any mapping before it's written to config.

---

### Phase 2b — Validate cost center mapping (if selected)

Checks that the CC mapping table is a point-in-time lookup and not a snapshot/SCD table by computing rows-per-cost-center:

- **≈1 row per CC** → clean mapping, proceed
- **>2 rows per CC + date column** → snapshot table, flag it and ask the user for a filter or alternative
- **>2 rows per CC, no date** → likely a hierarchy table, flag it

If multiple CC candidates exist, ranks them by group entropy — how evenly cost centers are distributed across department groups. Higher entropy = better signal of a purposeful mapping (vs a coarse rollup like "Division"). Shows the user the final department groupings for visual confirmation before writing config.

---

### Phase 3 — Fiscal year

Determines fiscal year start month. The agent first checks if pre-computed fiscal columns exist in the GL table; if not, it derives the fiscal calendar from data and confirms the start month with the user via a choice widget.

---

### Phase 4 — Amount sign + filters

Samples the GL table to determine whether actuals are stored as negative values (common in double-entry accounting) and whether a filter is needed to isolate the right ledger entries (e.g. exclude intercompany or budget journals). Confirms both with the user.

---

### Phase 5 — Category source

Determines where expense categories come from: the GL account mapping table, the budget table's category column, or a GL account prefix pattern. Runs prefix analysis (GROUP BY first N chars of account code) and presents the groupings for the user to confirm.

---

### Output

At the end of the session, the agent has collected all the config fields needed to populate the BvA settings. The user saves the config by name, which writes a row to the app's config table (location is configurable per deployment; see `lib/config-server.ts`). The config named `default` is the active one used by the dashboard.

---

### Key design principles

- **Evidence over convention** — every suggestion is backed by a SQL query, never just a column name guess.
- **One question at a time** — interactive widgets (table picker, column confirm, question options) are never stacked in the same message.
- **DESCRIBE first, sample only when needed** — avoids scanning large GL tables unnecessarily.
- **Parallel profiling** — when the user selects tables, column metadata is pre-fetched in the background so Phase 2 can map columns instantly.
