---
name: programmatic-access-token
title: Setup Snowflake PATs
summary: Create and manage Snowflake Programmatic Access Tokens (PATs) for service accounts with network and auth policies.
description: >-
  Create Snowflake Programmatic Access Tokens (PATs) for service accounts.
  Use for ALL requests that mention: setting up service user, creating PAT,
  configuring authentication policy, network policy for PAT, rotating tokens,
  verifying PAT connectivity, service account automation.
  Triggers: programmatic-access-token, sfutils-pat, create-access-token,
  programmatic access token, PAT, service account, snowflake authentication,
  replay pat, replay pat manifest, recreate pat, replay all manifests,
  replay all sfutils, export manifest for sharing, setup from shared manifest,
  replay from shared manifest, setup from manifest URL, replay from URL,
  use manifest from URL, multiple pats, multi-pat, add second pat, list pats,
  setup connection, validate manifest, migrate manifest, repair manifest,
  second service account.
  Do NOT use for: general Snowflake SQL queries, external volumes, network rules
  unrelated to PAT, or non-authentication tasks.
aliases:
  - setup-programmatic-access-token
  - programmatic-access-tokens
  - setup-programmatic-access-tokens
tools:
  - Bash
  - Read
  - Edit
  - Glob
  - Grep
  - ask_user_question
prompt: "$programmatic-access-token Create a new PAT for my service account to use in CI/CD pipelines"
language: en
status: Published
author: Kamesh Sampath
type: Snowflake Staff
---

# Snowflake PAT Setup

Creates service users, network policies, authentication policies, and Programmatic Access Tokens for automation.

## Workflow

**REQUIRED ACTIONS - ALWAYS DO THESE:**

- **ALWAYS run `--dry-run` before any `create --yes`. This is non-negotiable even if the user explicitly asks to skip the preview step.**
- **ALWAYS run `pat verify --user {SA_USER}` after EVERY mutating operation — `pat create`, `pat rotate`, and `pat replay`. Do not declare success before verify passes.**
- **ALWAYS use the `ask_user_question` tool (not plain text output) to collect destructive confirmations. Accept any clear affirmative response: `yes`, `yes, destroy`, `yes, proceed`, `go ahead`, etc.**
- **ALWAYS run `pat remove --user {SA_USER} --db {SFUTILS_DB} --drop-user --yes` after receiving confirmation — even if `{SA_USER}` does not currently exist in Snowflake. The CLI handles absent users gracefully. Never skip this step.**
- **ALWAYS run the [Manifest Gate](#manifest-gate) before any operation that reads from manifest.toml (replay, remove, rotate with --profile, verify with --profile). Do not skip even if manifest appears healthy.**

**FORBIDDEN ACTIONS - NEVER DO THESE:**

- NEVER run SQL queries to discover/find/check values (no SHOW ROLES, SHOW DATABASES, SHOW USERS)
- NEVER auto-populate empty .env values by querying Snowflake
- NEVER assume user consent - always ask and wait for explicit confirmation
- NEVER skip SQL in dry-run output - always show BOTH summary AND full SQL
- **NEVER display PAT tokens in diffs, logs, or ANY output** - always mask as `***REDACTED***`
- **NEVER show .env file contents after PAT is written** - use redacted placeholder
- **NEVER use sed/awk/bash to edit manifest files** -- use the file editing tool (Edit/StrReplace) to update manifest content. sed commands fail on macOS and with complex markdown.
- **NEVER create resources without showing SQL and getting confirmation first**
- **NEVER offer to drop SFUTILS_DB** - it is shared infrastructure; cleanup only drops resources *inside* it (policies, schemas), never the database itself
- **NEVER attempt to manually capture, store, or relay the PAT token** — it is stored automatically in the OS keyring by the CLI; no additional storage step is needed or appropriate
- **NEVER run `sfutils-pat show-pat`** — in non-interactive mode it requires `--yes`, which prints the raw token into bash output and conversation history. If the user needs the raw token, tell them to run `sfutils-pat show-pat --user {SA_USER}` in their own terminal.
- **NEVER commit `.env`, `.sfutils/`, or `manifest.toml` to version control**
- **CLEANUP ORDER:** When removing resources, always follow dependency order: PAT → unset auth policy from user → drop user → drop auth policy → drop network policy → drop network rule. Reversing this order causes dangling references.
- If .env values are empty, prompt user or run `check-setup`

**INTERACTIVE PRINCIPLE:** This skill is designed to be interactive. At every decision point, ASK the user and WAIT for their response before proceeding.

**📍 MANIFEST FILE:** `.sfutils/manifest.toml` — TOML format, replaces `sfutils-manifest.md`. Never search for `*.yaml` or other patterns.

> **⛔ DO NOT hand-edit manifests.** Manifests are machine-managed by Cortex Code. Manual edits can corrupt the format and break replay, cleanup, and export flows. Use skill commands to modify resources instead.

**⚠️ CONNECTION USAGE:** This skill reads the Snowflake connection from `[snowflake].connection` in `manifest.toml`. The CLI passes `-c <connection>` to all `snow sql` calls automatically — no manual env export needed. Admin_role defaults to `[snowflake].admin_role` or ACCOUNTADMIN.

**🔄 IDEMPOTENCY NOTE:** Network rules use `CREATE OR REPLACE` (Snowflake does not support `IF NOT EXISTS` for network rules). Network policies use `CREATE IF NOT EXISTS` to preserve existing policies. Re-running create operations is safe for automation.

**📌 ROLE MODEL:**

- **admin_role** (from manifest, default ACCOUNTADMIN): Creates and owns all objects
- **SA_ROLE** (`{PROJECT}_ACCESS`): Consumer-only role for PAT restriction. Apps/demos grant it access to their resources.
- **SA_USER** (`{PROJECT}_RUNNER`): Service user with PAT, restricted to SA_ROLE

**📌 CONNECTION NOTE:** The CLI reads connection from `manifest.toml` and passes `-c <connection>` to `snow sql` automatically. No `source .env` required. `SNOWFLAKE_DEFAULT_CONNECTION_NAME` env var is still accepted as a fallback if the manifest has no connection set.

## Manifest Gate

**Non-negotiable precondition for ALL manifest-dependent flows: replay, remove, rotate/verify with `--profile`.**

Run this gate before ANY of those operations. For a new create flow, the gate runs inside Step 0.

```bash
<SKILL_DIR>/pat validate-manifest
```

**If validation passes:** proceed immediately.

**If validation fails:**

```bash
<SKILL_DIR>/pat validate-manifest --fix
```

Re-run `validate-manifest`. If it still reports errors after `--fix`:

- These are **non-structural** issues (e.g. missing `sa_user` in a PAT entry, empty `connection`) that `--fix` cannot auto-repair
- Show the errors to the user, **STOP**, do not proceed with the requested operation
- Provide actionable next steps (e.g. "run `sfutils-pat setup-connection` to set connection")

**If `[snowflake].connection` is empty after fix:** redirect to Step 1 (connection picker) before continuing, regardless of which flow triggered the gate.

> **Why this exists:** A partial or corrupted manifest causes replay and remove to operate on wrong values, silently produce incorrect cleanup SQL, or skip resources. The gate is cheap; the cost of proceeding with a broken manifest is not.

---

### Step 0: Check Prerequisites

**⛔ MANDATORY — Run every time, even when `.env` is already populated:**

1. **Check for existing project needing migration** (one-time, first-run only):

   ```bash
   ls .sfutils/manifest.toml 2>/dev/null || echo "MISSING"
   ls .env .sfutils/sfutils-manifest.md 2>/dev/null && echo "LEGACY_FOUND" || echo "LEGACY_NOT_FOUND"
   ```

   **Case A — `manifest.toml` missing, legacy files exist** → migrate first:

   ```bash
   <SKILL_DIR>/pat migrate --dry-run   # show what will be written
   ```

   After user confirms:

   ```bash
   <SKILL_DIR>/pat migrate
   ```

   **Case B — `manifest.toml` exists but may be incomplete** → run the **Manifest Gate** now:

   ```bash
   <SKILL_DIR>/pat validate-manifest
   ```

   If issues reported → `<SKILL_DIR>/pat validate-manifest --fix` → re-validate → STOP if still failing.

   After gate passes, continue to Step 1 to ensure `[snowflake].connection` is set.

   **Case C — manifest.toml missing, no legacy files** → new project, continue to Step 1 (the skill will create it).

   Then continue with the rest of Step 0.

2. **Read the manifest to check for cached prereq state:**

   ```bash
   cat .sfutils/manifest.toml 2>/dev/null | grep "tools_verified" || echo "not_cached"
   ```

3. If `tools_verified` has **today's date** → prereqs are cached, skip to Step 1.
4. Otherwise → run `<SKILL_DIR>/pat check-setup --suggest` and follow the output.

See [Prerequisites](prereqs.md) for the full initialization flow.

**STOP**: Do not proceed until all prerequisites pass.

### Step 1: Resolve and Persist Snowflake Connection

**This step ensures `manifest.toml [snowflake].connection` is set before any other step runs.**

1. **Check if `manifest.toml` already has a connection:**

   ```bash
   cat .sfutils/manifest.toml 2>/dev/null | grep "^connection" | head -1
   ```

   **If `connection` is set (non-empty):** manifest is the source of truth — skip to Step 2. Do NOT re-prompt.

2. **Connection not set in manifest — resolve it:**

   Run `snow connection list --format json` to get all available connections:

   ```bash
   snow connection list --format json
   ```

   Determine the best default to pre-select (in priority order):
   - `SNOWFLAKE_DEFAULT_CONNECTION_NAME` env var if set
   - The connection marked `is_default: true` in `snow connection list` output
   - First connection in the list

   Use `ask_user_question` presenting all available connections, with the best default pre-selected:

   ```
   Which Snowflake connection should this project use?
   [local-oauth]  ← pre-selected (default)
   [prod-oauth]
   [ci-service]
   ```

   **⚠️ STOP**: Wait for user selection.

3. **Persist the chosen connection to `manifest.toml`:**

   ```bash
   <SKILL_DIR>/pat setup-connection -c <chosen_connection>
   ```

   This command:
   - Runs `snow connection test -c <chosen_connection>`
   - Writes `[snowflake].connection`, `account`, `user`, `account_url` to `manifest.toml`
   - `manifest.toml` is now the source of truth — `~/.snowflake/config.toml` is not consulted again for this project

4. **Verify manifest was written:**

   ```bash
   cat .sfutils/manifest.toml | grep -A5 "\[snowflake\]"
   ```

**If in a git repo, ensure `.gitignore` excludes `.sfutils/`:**

```bash
git rev-parse --git-dir 2>/dev/null && echo "GIT_REPO" || echo "NOT_GIT"
```

If GIT_REPO: verify `.gitignore` contains `.sfutils/`. Add if missing using Edit tool.

### Step 2: Check Infrastructure (Conditional)

**First, check `manifest.toml` for cached infra status:**

```bash
cat .sfutils/manifest.toml 2>/dev/null | grep -E "sf_utils_db|infra_ready"
```

Evaluate these conditions **strictly in order**:

1. If `sf_utils_db` is **empty or missing** → always run check-setup, regardless of `infra_ready`. An empty db means infra was never confirmed for this project.
2. If `sf_utils_db` has a value AND `infra_ready = true` → skip check-setup, use the cached value, proceed to Step 3.
3. If `sf_utils_db` has a value BUT `infra_ready = false` → run `check-setup --suggest` to confirm the db exists before proceeding.

> **Why strict order matters:** `migrate` sets `infra_ready = false` when `sf_utils_db` could not be resolved from legacy files. A combined check on both fields prevents silently skipping infra setup.

**Otherwise, read from .env (fallback):**

```bash
grep -E "^SFUTILS_DB=" .env
```

**If SFUTILS_DB has value:** Skip to Step 3.

**If empty**, run `check-setup` with --suggest flag:

```bash
<SKILL_DIR>/pat check-setup --suggest
```

Parse the JSON response:

- `ready: true` → Database exists, skip to Step 3
- `ready: false` → Need to create database

**If not ready**, use `ask_user_question` to confirm:

- Show suggested database name from JSON (`suggested_database`)
- Ask user to confirm or provide custom value

**If user confirms setup**, run:

```bash
<SKILL_DIR>/pat check-setup --database <DB> --run-setup
```

**After setup completes:**

- `check-setup` writes `sf_utils_db` and `infra_ready = true` to `manifest.toml` automatically.
- Update `.env` as well for backward compat: `SFUTILS_DB=<value user confirmed>`

**Note:** SA_ROLE ({PROJECT}_ACCESS) is created in Step 5 by this skill, not by `check-setup`.

**Update memory:**

```
Update /memories/sfutils-prereqs.md:
tools_checked: true
infra_ready: true
sf_utils_db: <VALUE>
```

### Step 2a: Admin Role from Manifest

PAT skill requires elevated privileges (CREATE USER, CREATE ROLE, MANAGE GRANTS, CREATE AUTHENTICATION POLICY). Check `manifest.toml` for cached `admin_role` in `[snowflake]`, prompt user if not set, write choice to manifest before any resource creation. See [Admin Role Setup](admin-role-setup.md) for the full privilege matrix, manifest lookup flow, and user prompts.

```bash
cat .sfutils/manifest.toml 2>/dev/null | grep "admin_role"
```

**⚠️ STOP**: Wait for user input if admin_role not cached.

### Step 2b: Verify Admin Role Privileges

If admin_role is ACCOUNTADMIN, skip verification. For custom roles, verify required grants and prompt user to fix missing privileges. See [Admin Role Setup](admin-role-setup.md#step-2b-verify-admin-role-privileges) for verification queries and remediation flow.

### Step 2c: Multi-PAT Selection

**Check if any PATs already exist in manifest.toml:**

```bash
cat .sfutils/manifest.toml 2>/dev/null | grep -E "^(status|sa_user)" | head -10
```

**If no `[pat.*]` entries exist or manifest missing:** Continue to Step 2d (first PAT — connection already set in Step 1).

**If PATs exist**, show table using:

```bash
<SKILL_DIR>/pat list
```

Use `ask_user_question` to present:

| Option | Action |
|--------|--------|
| Add new PAT | Continue to Step 2d to pick connection for new PAT |
| Manage existing PAT | Pick from table by label to rotate / remove / verify |

**If user selects "Manage existing":**
- Use `--profile <label>` on subsequent CLI commands — connection switches automatically
- Jump to relevant step: rotate → Step 3a, remove → Remove Flow, verify → Step 6

**If user selects "Add new PAT":** Continue to Step 2d.

**⚠️ STOP**: Wait for user selection.

### Step 2d: Connection for New PAT

**Used when adding a 2nd (or later) PAT. Step 1 already handled the first PAT's connection.**

1. Read the project's default connection from manifest.toml:

   ```bash
   cat .sfutils/manifest.toml | grep "^connection" | head -1
   ```

2. Use `ask_user_question`:

   ```
   Which connection should this PAT use?
   [local-oauth]   ← pre-selected (project default from manifest.toml)
   [prod-oauth]
   [ci-service]
   [Choose a different connection — list all]
   ```

   If user picks the default → pass **no** `--connection` flag (PAT inherits root).

   If user picks a different one → note as `{PAT_CONNECTION}`.

3. **If a different connection was picked**, run `snow connection test` to confirm it works, then pass `--connection {PAT_CONNECTION}` on the `create` command in Step 5. The CLI will automatically cache `account`/`user`/`account_url` in `[pat.{LABEL}]`.

**⚠️ STOP**: Wait for user selection before proceeding to Step 3.

### Step 3: Gather Requirements (User-Prefixed Demo-Context Naming)

**Detect demo context from current directory:**

```bash
basename $(pwd)
```

Example: `hirc-duckdb-demo` → demo context = `HIRC_DUCKDB_DEMO`

**Read existing values — prefer `manifest.toml`, fall back to `.env`:**

```bash
# Check manifest.toml first
cat .sfutils/manifest.toml 2>/dev/null | grep -E "user|sf_utils_db|admin_role" || \
grep -E "^(SNOWFLAKE_USER|SA_ROLE|SFUTILS_DB)=" .env
```

**NAMING CONVENTION (User-Prefixed Demo-Context):**

> 💡 **TIP:** Using `{USER}_{DEMO}` prefix is recommended for shared accounts. This prevents naming collisions when multiple users create resources in the same account. The pattern `KAMESHS_MYAPP_RUNNER` clearly identifies the owner and purpose.

Both SA_ROLE and service user should use user-prefixed demo context for consistency:

| Variable | Pattern | Example (user=KAMESHS, demo=myapp) |
|----------|---------|--------------------------------|
| SA_ROLE | `{USER}_{DEMO}_ACCESS` | `KAMESHS_MYAPP_ACCESS` |
| SA_USER | `{USER}_{DEMO}_RUNNER` | `KAMESHS_MYAPP_RUNNER` |

**Prompt for naming preference first:**

Use `ask_user_question`:

```
💡 Service Account Naming

Using a user prefix is recommended for shared accounts to avoid collisions.

☑ Use prefix: KAMESHS_MYAPP_RUNNER (recommended)
☐ No prefix: MYAPP_RUNNER
```

**Then prompt for skill-specific values with appropriate defaults:**

```
PAT Configuration for demo: <DEMO_CONTEXT>

1. Service user name [default: <USER>_<DEMO>_RUNNER or <DEMO>_RUNNER based on choice]:
2. PAT role [default: <USER>_<DEMO>_ACCESS or <DEMO>_ACCESS based on choice]:
3. Admin role for setup [default: from manifest, or ACCOUNTADMIN]:
4. Database for policy objects [default: from SFUTILS_DB]:
```

**Auth policy expiry settings (ALWAYS ask user to confirm):**

Use `ask_user_question` with preset options:

```
PAT Expiry Profile:

☐ Snowflake platform defaults (15/365 days)
☐ Hardened baseline (7/30 days) - tool default, recommended
☐ Medium (30/90 days) - Standard development
☐ Custom - I'll specify values
```

| Profile | Default Expiry | Max Expiry | Use Case |
|---------|---------------|------------|----------|
| Snowflake platform defaults | 15 days | 365 days | Snowflake platform defaults |
| Hardened baseline | 7 days | 30 days | Tool default, recommended |
| Medium | 30 days | 90 days | Standard development |
| Custom | (prompt) | (prompt) | User-specified |

**If user selects "Custom":**

```
5. PAT default expiry days [default: 15]:
6. PAT max expiry days [default: 365]:
```

**STOP**: Wait for user input on ALL values including expiry settings.

**After user provides input:**

- SA_USER and SA_ROLE will be written to `manifest.toml` (`[pat.<label>]` entry) automatically by the CLI in Step 5. Do NOT write them to `.env` manually.
- For backward compat only: the CLI also updates `.env` with `SA_USER=<value>` and `SA_ROLE=<value>` automatically — no manual `.env` edit needed.

### Step 3a: Check for Existing PAT

**Check if PAT already exists for the user (using elevated role):**

```bash
snow sql -c {CONNECTION} --role <ADMIN_ROLE> -q "SHOW USER PATS FOR USER <SA_USER>" --format json
```

> Where `{CONNECTION}` is read from `manifest.toml` `[snowflake].connection`.

> ⚠️ **IMPORTANT:** All account-level operations from this step onwards MUST use `--role <ADMIN_ROLE>` (from manifest) to ensure proper privileges.

**If PAT exists**, use `ask_user_question` to ask:

| Option | Action |
|--------|--------|
| Rotate existing | Rotate token using `ROTATE PAT` command — generates new secret, keeps all policies intact |
| Remove and recreate | Clean removal of all resources, then fresh start |
| Cancel | Stop workflow |

**If user chooses "Rotate existing":**

Use `ask_user_question` to confirm and gather preferences before executing:

```
Rotate PAT for user: {SA_USER}

Old token behaviour after rotation:
  A. Expire in 24 hours (default — allows a grace period)
  B. Expire immediately
  C. Cancel
```

**⚠️ STOP**: Wait for user choice. On "C", stop the workflow.

```bash
<SKILL_DIR>/pat \
  rotate --user {SA_USER} --role {SA_ROLE} --yes
```

> Append `--expire-rotated-after-hours 0` if user chose "B" and the option is supported.

**⛔ MANDATORY NEXT STEP:** Proceed immediately to Step 6 (Verify Connection). Do not declare success or end the workflow before `pat verify` completes successfully.

**If user chooses "Remove and recreate":**

Use `ask_user_question` tool to present what will be removed and collect confirmation:

```
⚠️  DESTRUCTIVE: This will permanently remove ALL resources for {SA_USER}:
  - PAT token
  - Authentication policy ({SA_USER}_AUTH_POLICY)
  - Network policy ({SA_USER}_NETWORK_POLICY)
  - Network rule ({SA_USER}_NETWORK_RULE)
  - Service user ({SA_USER})
  - Service role ({SA_ROLE})

Confirm to proceed with removal?
```

Options: `Yes, destroy all resources` / `Cancel`

**⚠️ STOP**: Wait for confirmation via `ask_user_question`. Accept any clear affirmative (`yes`, `yes, destroy`, `yes, proceed`, `go ahead`). Any non-affirmative → stop the workflow.

**⚠️ DO NOT RUN** the `remove` command below **until** the user has confirmed via `ask_user_question`. The `--yes` flag only satisfies non-interactive CLI behavior; it does **not** count as user approval.

**After receiving confirmation, run `pat remove` even if `{SA_USER}` does not currently exist in Snowflake.** The CLI handles absent users gracefully. Never skip or short-circuit this step.

**State lifecycle: `DELETE_IN_PROGRESS → REMOVED`** — the CLI writes `DELETE_IN_PROGRESS` before any DROP commands run, then `REMOVED` after all resources are confirmed deleted.

```bash
<SKILL_DIR>/pat \
  remove --user {SA_USER} --db {SFUTILS_DB} --drop-user --yes
```

> **Note:** Dropping the service role `{SA_ROLE}` is separate from `remove`. If needed, run [`DROP ROLE`](https://docs.snowflake.com/en/sql-reference/sql/drop-role) (or your org’s equivalent) with a sufficiently privileged role.

Then continue to Step 4.

**If no PAT exists:** Continue to Step 4.

### Step 4: Preview (Dry Run)

Run dry-run to preview ALL SQL that will be executed:

```bash
<SKILL_DIR>/pat \
  --comment "{COMMENT_PREFIX}" \
  create --user {SA_USER} --role {SA_ROLE} --db {SFUTILS_DB} \
  --default-expiry-days {DEFAULT_EXPIRY} --max-expiry-days {MAX_EXPIRY} --dry-run
```

> **⛔ MANDATORY — CANNOT BE SKIPPED:** Always run `--dry-run` first. Even if the user explicitly asks to skip the preview, run dry-run anyway before proceeding.

Follow the [Dry-Run Output Rule](cli-reference.md#dry-run-output-rule): capture and paste the full output into your response.

> 🔄 **On pause/resume:** Re-run `--dry-run` and paste the complete output again before asking for confirmation.

**STOP**: Wait for explicit user approval ("yes", "ok", "proceed") before creating resources.

### Step 5: Create PAT Resources

> Step 4 already showed SQL and got user approval. Now executing.

```bash
<SKILL_DIR>/pat \
  --comment "{COMMENT_PREFIX}" \
  create --user {SA_USER} --role {SA_ROLE} --db {SFUTILS_DB} \
  --default-expiry-days {DEFAULT_EXPIRY} --max-expiry-days {MAX_EXPIRY} --yes
```

PAT is stored automatically in the OS keyring. No additional secret storage step required.

**Write PAT record** to `.sfutils/manifest.toml`. The manifest write happens in two stages:

1. **Before any SQL runs** — the CLI automatically writes `status = "CREATE_IN_PROGRESS"` as soon as the user confirms (via `_begin_pat_create()`). This ensures the manifest always reflects current intent even if creation fails mid-way. No manual action needed.

2. **After all resources confirmed + verify passes** — the CLI updates the entry to `status = "COMPLETE"` with the full resource details. See [Manifest Flows — TOML PAT Entry](manifest-flows.md#toml-pat-entry-template) for the template and progressive write instructions.

**State lifecycle: `CREATE_IN_PROGRESS → COMPLETE`**

**After writing the manifest entry**, always validate:

```bash
<SKILL_DIR>/pat validate-manifest
```

If validation fails → fix the reported issues before proceeding to Step 6. Use [manifest.toml.example](manifest.toml.example) as the schema reference.

**On failure:** Present error and remediation steps. Do NOT proceed to Step 6.

### Step 6: Verify Connection (MANDATORY)

**Always verify the PAT works after creation:**

```bash
<SKILL_DIR>/pat \
  verify --user {SA_USER} --role {SA_ROLE}
```

**After success, show:**

```
PAT verified successfully.
Expiry: {DEFAULT_EXPIRY} days from creation
Max: {MAX_EXPIRY} days
Snowflake limit: up to 15 active PATs per user
Security: PAT stored in OS keyring only — never written to disk.
```

**If verification fails:**

- Check network policy allows current IP
- Verify auth policy is attached to user
- Verify network policy is assigned to user BEFORE auth policy (required when `NETWORK_POLICY_EVALUATION = ENFORCED_REQUIRED`)
- If keyring entry missing: tell user to run `sfutils-pat show-pat --user {SA_USER}` in their own terminal to confirm the token exists


### Step 7: Resource Manifest Format Reference

See [Manifest Flows](manifest-flows.md) for the TOML `[pat.{LABEL}]` entry template, progressive writing, export for sharing, and remove/cleanup flows.

## Reference

- [Prerequisites](prereqs.md) — Step 0/0b tool checks and manifest init
- [Admin Role Setup](admin-role-setup.md) — Step 2a/2b admin role discovery, privilege verification, and remediation
- [CLI Reference](cli-reference.md) — `check-setup`, `setup-connection`, `validate-manifest` command reference and PAT security notes
- [Manifest Flows](manifest-flows.md) — TOML `[pat.{LABEL}]` entry template, progressive writing, export for sharing, and remove/cleanup flows
- [manifest.toml.example](manifest.toml.example) — **Canonical schema reference** with annotations; covers single-PAT, multi-PAT, and multi-connection scenarios
- [Replay Flows](replay-flows.md) — Replay single skill and replay all skills flows
- [Supplemental](supplemental.md) — Stopping points, output, SQL reference, troubleshooting, security notes, and security checklist
