---
name: bva_variance_setup
description: >
  Deploy and run the BvA Variance app — a full-stack Next.js web app
  that ships alongside this skill,
  for Budget vs. Actuals analysis with an embedded Cortex Chat setup agent.
  Use this skill whenever someone asks to deploy, install, set up, or stand
  up the BvA Variance app on a Snowflake account — including first-time
  customer installs where the user has no Snowflake CLI connection
  configured yet. Also covers running the app locally and understanding
  how its setup agent works.
created_date: 2026-07-26
last_updated: 2026-08-13
owner_name: Snowflake
version: 1.0.0
license: Proprietary. See LICENSE.txt for complete terms
---

## Overview

This skill hosts the **BvA Variance** deploy playbook. The React application source code **ships alongside this skill** (in the bundled app source folder). This skill can drive the entire deploy for someone who has never used the Snowflake CLI before — it runs its steps against that bundled app source.

> **Path placeholder used throughout this skill:** every reference below to `<APP_SOURCE>` is the absolute path to the BvA Variance app source folder that ships with this skill (the folder containing `snowflake.yml`). Step 0 establishes this. Never leave `<APP_SOURCE>` as a literal placeholder in a shell command — always substitute the concrete path.

The app is a full-stack Next.js application (App Router) that:
- Provides a 7-step setup wizard for configuring BvA data sources
- Materializes a BvA dataset in Snowflake from user-supplied GL, budget, cost center, and ledger mapping tables
- Renders an interactive BvA Working dashboard and Expense Breakdown view
- Includes an embedded Cortex Chat agent for AI-guided setup
- Seeds a fully synthetic demo dataset on first load, so there's something real to look at before anyone connects their own tables (see [Demo data](#demo-data))

## Deploying — step by step

Deploying means: install the Snowflake CLI if it's missing, connect it to a Snowflake account, point the app at a database/schema/warehouse in that account, and run two CLI commands. It takes about 10 minutes. Follow these steps **in the exact order given**. Each step below tells you precisely what to say and, where marked **STOP**, you must not proceed — not read a file, not edit a file, not run a tool — until the user has actually replied.

**HARD RULE — read before doing anything else:** `<APP_SOURCE>/snowflake.yml` and `<APP_SOURCE>/app.yml` ship with **unset placeholder values** (`REPLACE_WITH_TARGET_DATABASE`, `REPLACE_WITH_TARGET_SCHEMA`, `REPLACE_WITH_TARGET_WAREHOUSE`) — deliberately, so that a deploy attempted without Step 2/3 fails loudly instead of silently landing somewhere wrong. **Never read these files to report "here are the deployment settings" to the user — that information does not exist yet.** The only correct source for database/schema/warehouse is the user's own answer to Step 2. `snow app deploy` is forbidden until Step 2 has been asked and answered by the user in this conversation, and Step 3 has actually written that answer into both files.

### Step 0 — Locate the app source

The BvA Variance app source **ships with this skill** — there is no repository to clone. First check for it co-located with this skill, in an `app/` folder alongside `SKILL.md` (the sibling folder that contains `snowflake.yml`).

- **If found**, use it — record its absolute path as `<APP_SOURCE>` and continue to Step 1.
- **If not found** (e.g. the app source was unpacked to a different location), ask the user:

  > "Where is the BvA Variance app source on this machine? Give me the absolute path to the folder that contains `snowflake.yml`."

  **STOP.** Wait for the user's reply. Verify the path exists and contains `snowflake.yml`; if not, tell them what's missing and ask again.

Record the absolute path as `<APP_SOURCE>`. Every subsequent file read, edit, or `cd` in this skill uses `<APP_SOURCE>/...` — substitute the concrete path before running anything.

### Step 1 — Check prerequisites

Run `snow --version`. If it's not found, say:

> "You'll need the Snowflake CLI installed first — it's the command-line tool used to deploy apps to Snowflake. I can install it for you with Homebrew (`brew install snowflake-cli`), or you can use `pipx install snowflake-cli-labs` / `uv tool install snowflake-cli-labs`. Which would you like?"

**STOP.** Wait for the user's choice, then run the install, then continue.

Run `snow connection list`. If there's no usable connection, say:

> "I don't see a Snowflake connection configured yet. I'll walk you through adding one — it's a one-time setup that saves your account and username so the CLI knows where to deploy. I'll run `snow connection add` now and you'll answer its prompts (account identifier, username, role, and authenticator — most Snowflake accounts use `externalbrowser` for SSO)."

Then run `snow connection add` interactively so the user answers the CLI's own prompts directly — do not pre-fill answers on their behalf.

If a connection already exists, say which one you're about to use and run `snow connection test --connection <name>` to confirm it works — do not silently assume the default connection is correct.

Confirm `code_toolset_all` (Cortex Agents REST tool access) is enabled on the target account — without it, the app's embedded setup agent and chat won't work at all, though the dashboard itself will still deploy fine. This requires `code_toolset_all` access on the account; if unsure, mention this once to the user and move on — treat a clear agent failure after deploying as a signal to follow up on availability, not a bug in this app.

### Step 2 — Ask where the app should live — **STOP, this is a hard gate**

Before checking anything else, check: did the user's own request already unambiguously state all three of database, schema, and warehouse (e.g. "deploy the BvA app to `X.Y` with warehouse `Z`")? If yes, treat that as the answer and skip straight to Step 3 — do not ask again, and do not go read `snowflake.yml`/`app.yml` first to see if there's already an answer there (there isn't — they ship blank). Otherwise, run `SHOW WAREHOUSES` first so you can suggest real options instead of asking someone to recall warehouse names off the top of their head — prefer one that's `X-Small` or `Small`, ideally with `auto_resume` true (a suspended one with `auto_resume: false` will just fail to start). Then say exactly this, filling in one concrete suggestion from that list where marked:

> "Before I deploy, I need three things from you:
> 1. **Database and schema** — where the app's own tables (saved configs, materialized BvA data) will live. Any schema you can create tables in works — it doesn't need your real GL/budget data, those get connected later inside the app.
> 2. **Warehouse** — any existing warehouse works; something small is plenty. `<SUGGESTED_WAREHOUSE>` looks like a good fit if you don't have a preference.
> 3. **App name** — defaults to `BVA_VARIANCE_SETUP` if you don't have a preference, but if that database/schema might already have an app by that name, a distinct name (e.g. `BVA_VARIANCE_SETUP_TEST`) avoids overwriting it.
>
> What would you like to use for each?"

**STOP. Do not call any tool. Do not read or edit `snowflake.yml` or `app.yml`. Do not run `snow app deploy`.** Wait for the user's reply. If their reply is missing database or schema or warehouse, ask again for just the missing piece and **STOP** again — do not fill it in yourself, and do not go looking in the files for a default to fall back to (they're placeholders, not defaults). If the user's reply doesn't mention an app name at all, that's fine — `BVA_VARIANCE_SETUP` is the real, intentional default; note that explicitly to yourself as "using the default" rather than silently treating "no name given" as "nothing to confirm" — it still needs to be echoed back in Step 3's confirmation line and checked for collisions in the pre-flight check below.

Only once you have an explicit database, schema, and warehouse from the user (either from this exchange or from their original request) may you proceed to Step 3.

**Before Step 3, check for a name collision:** run `SHOW APPLICATION SERVICES LIKE '<app name>' IN SCHEMA <database>.<schema>` (using whatever app name is about to be used — the user's choice, or `BVA_VARIANCE_SETUP` if they didn't give one). If a row comes back, **STOP and say exactly this**, filling in the real values:

> "Heads up — there's already an app called `<app name>` in `<database>.<schema>` (last updated `<created_on>`). Deploying will upgrade that existing app to a new version rather than create a separate one. Do you want to proceed with that name, or use a different one?"

Wait for the user's answer before continuing. If they give a different name, treat it as a corrected answer to Step 2 and re-run this collision check on the new name before moving on.

### Step 3 — Point the app at that location

Read the current contents of `<APP_SOURCE>/snowflake.yml` and `<APP_SOURCE>/app.yml` first — you should see the `REPLACE_WITH_TARGET_*` placeholders (or values a previous run of this skill already filled in). Then write the values the user just gave you in Step 2 into both files, replacing the placeholders:

- `snowflake.yml` → `entities.BVA_VARIANCE_SETUP.identifier.database`, `.schema`, and `.name`; and `query_warehouse`. Write `.name` explicitly every time — even when using the default `BVA_VARIANCE_SETUP` — don't leave it untouched on the assumption that "no custom name given" means "nothing to do here."
- `app.yml` → the seven `environment_variables` entries:
  - `BVA_CONFIG_TABLE`, `BVA_HISTORY_TABLE`, `BVA_MATERIALIZED_TABLE`, `BVA_COMMENTARY_TABLE` — each rewritten to `<database>.<schema>.BVA_VARIANCE_SETUP_*` using the chosen database/schema. These are what `getConfigTable()` (`lib/config-server.ts`) reads — if they're left pointing at the values already in the file, the deploy will try to write into a schema the customer doesn't have access to.
  - `BVA_AGENT_ACCESS_MODE_TABLE` → `<database>.<schema>.BVA_VARIANCE_SETUP_AGENT_ACCESS_MODE`
  - `BVA_DISCOVERY_SCOPE_TABLE` → `<database>.<schema>.BVA_VARIANCE_SETUP_DISCOVERY_SCOPE`
  - `BVA_WAREHOUSE` → the warehouse the user gave in Step 2 (just the warehouse name, no db/schema prefix)

Do not add `build_compute_pool`, `service_compute_pool`, or `build_eai` to `snowflake.yml`. App Runtime compute is Snowflake-managed, so declaring compute pools breaks the initial deploy (first-create); and the build makes no external network calls (npm and fonts are covered by the default build egress), so a named `build_eai` — which is account-specific and may not exist on the target account — is unnecessary and can itself fail the deploy. Only revisit `build_eai` if `snow app deploy` explicitly reports a blocked build-egress host, and then attach an external access integration that actually exists on that account.

After editing, re-read both files and say exactly this, filling in the real values you just wrote:

> "I've configured the app to deploy to **`<database>.<schema>`** with warehouse **`<warehouse>`**, app name **`<app name>`**. Ready to deploy — this will open a browser window for you to log in to Snowflake via SSO. Say go and I'll start it."

**STOP.** Wait for the user's explicit go-ahead before running anything in Step 4.

### Step 4 — Deploy — **pre-flight check, then run**

Before running `snow app deploy`, re-read `snowflake.yml` and `app.yml` right now (not from memory) and check: does either file still contain any `REPLACE_WITH_TARGET_*` placeholder, or a value from a *previous, different* deployment that the current user never asked for? If so, **STOP immediately and do not run the deploy.** Say:

> "Wait — the config still isn't pointed at the location you gave me. Let me fix that before I deploy anything."

Then go back to Step 3 (or Step 2 if you never actually got an answer). This check exists because a placeholder value in the deploy command produces either a hard failure or — worse — deploys into whatever the CLI's default context happens to resolve to, neither of which is the user's intended target.

Whichever role that connection resolves to becomes the app's **owner role** — the role with `OWNERSHIP` on the resulting application service, and the only one with access to open it until someone grants further access (see Step 5). If the user has a specific role in mind for running/administering this app, confirm the connection they're about to use resolves to that role before deploying, rather than after.

If the check passes, run, from inside `<APP_SOURCE>/`:

```
snow app deploy --connection <connection name>
```

Tell the user right before running it: **a browser window will open asking them to log in to Snowflake (SSO)** — this is expected, not an error, and the command will not continue until login completes.

Run this in the foreground, not in the background — if it's interrupted (e.g. Ctrl+C) partway through, the CLI process stopping does **not** guarantee the server-side build stops; Snowflake may finish publishing the new version anyway. Do not treat a client-side interruption as "nothing happened" — always verify with Step 5 after any interruption, not just after a clean finish.

The build/upgrade pipeline takes a few minutes. Watch the CLI output for errors; a missing compute pool or EAI (see Step 3) is the most common one for a brand-new account.

### Step 5 — Verify and hand off

Confirm the deploy worked: run `SHOW APPLICATION SERVICES LIKE '<app name>'` — it should show status `RUNNING` for the database/schema from Step 2 (not some other database), and the service will have a URL. Give that URL to the user — that's the whole handoff. The first page load auto-seeds a synthetic demo dataset (see [Demo data](#demo-data)), so there's a working app to explore immediately; connecting real GL/budget tables happens later, inside the app's own Settings wizard, and needs no further CLI or engineering work.

**Access note — mention this, don't just leave it implicit:** only the owner role from Step 4 (and roles that inherit it) can open the app right now — Snowflake doesn't grant anyone else access automatically, not even by default roles. If the user wants other people or roles to be able to open the URL, that's their own access decision to make: they (or their Snowflake admin) can run `GRANT USAGE ON APPLICATION SERVICE <database>.<schema>.<app name> TO ROLE <role>` for whichever role(s) fit their own access model. Don't prescribe which role that should be or run the grant yourself — every org's role structure is different, and a wrong recommendation here is worse than no recommendation. Just make sure they know the app is owner-scoped by default and that widening it is a grant they run themselves, when and how they decide.

Before handing off, explain the agent's access model to the user — **no post-deploy SQL command is needed**:

> "The embedded agent runs with **caller's rights** by default: it queries as you (the signed-in user), not as the app's owner role, so it only sees tables you have access to. App Runtime turns this on automatically — there's nothing to run."

Note on session token lifetime: there is **no `ALTER APPLICATION SERVICE` property** to change it. The older `SERVICE_CALLER_TOKEN_VALIDITY_SECS` command is **not valid** on current App Runtime and will error — do not run it. The platform default token lifetime applies. If a long agent turn ever times out on that default, see Snowflake's "Configuring the login token validity" documentation for the current mechanism; do not guess the syntax.

**Caller's rights is the default, and there is no silent fallback to owner's rights.** The moment a viewer's browser hits an App Runtime deployment, the `Sf-Context-Current-User-Token` header is present (App Runtime injects it by default), so the app builds the combined caller+service token and runs the agent's queries as that viewer — unless an admin has explicitly switched the app to owner's rights (see below). Restricted Caller's Rights means the query's privileges are the *intersection* of the viewer's own privileges and explicit `GRANT CALLER` privileges held by the app's **owner role** — if that intersection is empty for an object, the query fails with a permission error inside the agent's tool result, it does not silently degrade. Practically, this means **on a fresh deployment with no caller grants configured, the agent — including the first-run setup table scan — will not work for any viewer until either an admin grants caller rights, or the app is switched to owner's rights.**

Check this before declaring the deploy done, not after — this is the one behavioral test that actually reflects real access, since `SHOW CALLER GRANTS TO ROLE` can under-report grants inherited through role hierarchy. Open the app's **Settings** page while signed in as the deploying admin and click **"Check caller's rights"** under the "Agent access mode" control — it runs the same behavioral test as before, but now reports a plain pass/fail sentence instead of raw diagnostic JSON (the underlying route, `app/api/agent/debug-callers-rights`, is still admin-gated the same way the mode toggle is — an ordinary viewer gets a 403, so only an admin can run this from the UI). If it reports caller's rights is working, nothing more to do. If it reports a failure, this is a **blocking choice for the user to make explicitly, not something to decide for them**:

- **Option A — set up caller grants** (per-viewer scoping, the more secure default). An administrator (a role with `MANAGE CALLER GRANTS` — typically `SECURITYADMIN`/`ACCOUNTADMIN`) runs, scoped to wherever the app's data lives:
  ```sql
  GRANT CALLER USAGE ON DATABASE <db> TO ROLE <app_owner_role>;
  GRANT INHERITED CALLER USAGE ON ALL SCHEMAS IN DATABASE <db> TO ROLE <app_owner_role>;
  GRANT INHERITED CALLER SELECT ON ALL TABLES IN DATABASE <db> TO ROLE <app_owner_role>;
  GRANT INHERITED CALLER SELECT ON ALL VIEWS IN DATABASE <db> TO ROLE <app_owner_role>;
  GRANT CALLER USAGE ON CORTEX_AGENT TO ROLE <app_owner_role>;
  ```
  This can also be satisfied by granting a role that already holds broad caller grants (e.g. an account-wide read-only caller-rights role, if one exists) to the app's owner role — check for one before writing narrower per-database grants from scratch.
- **Option B — switch to owner's rights.** As the deploying admin (a viewer whose role is/contains the app's owner role), open the app's **Settings** page and use the "Agent access mode" control to switch to owner's rights — this works immediately, but every viewer then shares this app's own access rather than their own. This is a real, admin-gated app setting (`app/api/agent-access-mode`, `lib/agent-access-mode.ts`), not a code change — do not implement this by editing `lib/spcs-auth.ts` or `lib/agent-backend.ts`, and do not flip it on someone's behalf without them choosing it. It can be changed back to caller's rights at any time from the same Settings control, by the same admin check — this is not a one-way decision.

Explain both options plainly and let the user pick — don't default to Option B just because it's faster, and don't insist on Option A if the user has a reason to prefer shared access (e.g. a small team where everyone already has the same underlying access, or a deliberately shared company-wide view). If `code_toolset_all` is unavailable, the app falls back to CLI, which needs `sh scripts/vendor-cortex-cli.sh` (from `<APP_SOURCE>/`) run once on a machine with normal internet access **before** the first `snow app deploy` — the App Runtime build container has no network access, so this can't happen automatically. Safe to re-run any time to bump the CLI version; it's git-ignored on purpose (it's a large, machine-fetched binary, not something to hand-edit or commit).

Then add one brief, low-key line: "That's it — the app is deployed and ready to use. If anything comes up later, like something not working right or wanting to change how it looks, feel free to come back and ask." If the user later does ask for a fix or a change, see `AGENT_PLAYBOOK.md` before starting — it has nothing to do with this deploy flow and only matters at that point.

## Demo data

A brand-new deployment has no data yet. Rather than fail or fall back to some placeholder, the first page load calls `ensureDemoDataSeeded()` (`lib/demo-seed.ts`): it creates a handful of small tables in the same schema as the app's config table, inserts a fully synthetic dataset generated in code (`lib/demo-seed-data.ts` — not copied from or resembling any real company's data), and saves a `Demo` config pointing to them. This is idempotent and safe to call repeatedly.

## Custom logic (allocated/unallocated, and similar requests)

Customers can add their own business logic on top of the dashboard — the canonical example is splitting expenses into allocated and unallocated — entirely inside the running app, in natural language, at Step 7 (Validate) of the Settings wizard. This never requires a CoCo/engineering session: the customer describes the rule in a freeform box, the embedded custom-logic agent (read-only-SQL-only, same restriction as the setup agent) proposes a structured rule, and the customer reviews/accepts it before it takes effect. Each rule can be toggled off or deleted independently at any time, which always reverts the dashboard to its exact baseline behavior — see README.md §14 for the full architecture (`buildFinalSql()`, `lib/custom-logic-guard.ts`, the ambiguity-first agent protocol).

If a customer already has a Snowflake semantic view for this data, they can point at it directly in Step 1 (optional) — this raises confidence in table/column mapping and gives the custom-logic agent existing business vocabulary to reuse. Never required; the setup agent asks once, non-forcing, and falls back to normal discovery if there isn't one.

**Where this stops being in-app:** this covers new filter *values* and new derived columns on the existing dashboard chrome (filters, toggles) — not new UI/chart types, page layouts, or component behavior. That's a different tier of change (real source-code edits + redeploy) and requires a CoCo/engineering session against this app's source — see `AGENT_PLAYBOOK.md`'s "Customer wants a new feature or customization" section, which walks through telling these two cases apart before doing anything.

## Running locally

```bash
cd <APP_SOURCE>
SNOWFLAKE_CONNECTION_NAME=<your_connection> npm run dev
```

Requires a Snowflake connection configured in `~/.snowflake/connections.toml` (see Step 1 above if one doesn't exist yet).

## Architecture notes

If you ever need to help someone after they've already deployed — fixing something or changing how the app works — see `AGENT_PLAYBOOK.md` first. It doesn't apply to the deploy flow above.

For how the embedded Cortex Chat backend and the setup agent's 5-phase discovery process actually work, see `ARCHITECTURE.md`. Neither is needed to run the deploy above.

## Related skills

- `bva_sql_setup` — a separate, unrelated skill for generating static SQL for BvA reporting. If you mean to deploy this app, be explicit about wanting the `bva_variance_setup` skill rather than a generic request, since the similar names can otherwise get confused.

