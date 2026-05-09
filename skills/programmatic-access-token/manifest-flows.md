### Step 7: Resource Manifest Format Reference

> **When to write:** Manifest is updated PROGRESSIVELY in Steps 5a, 5b, 5c - NOT here.
> This section defines the TOML FORMAT to use.

**Manifest Location:** `.sfutils/manifest.toml`

**Create directory if needed:**

```bash
mkdir -p .sfutils && chmod 700 .sfutils
```

---

#### TOML PAT Entry Template

Write or append a `[pat.{LABEL}]` block to `.sfutils/manifest.toml` using the Edit tool. The label is the TOML key — it must be a valid bare identifier (letters, digits, hyphens, underscores). If `manifest.toml` does not yet exist, initialise it first (see [prereqs.md](prereqs.md)), then append the block below.

**State lifecycle:**
```
Create: CREATE_IN_PROGRESS → COMPLETE
Remove: DELETE_IN_PROGRESS → REMOVED
```

**⛔ Write order is mandatory:** the `[pat.{LABEL}]` block with `status = "CREATE_IN_PROGRESS"` is written **before the first SQL command runs** (the CLI does this automatically via `_begin_pat_create()`). This ensures the manifest always reflects current intent even if creation fails mid-way.

**Before first SQL** — write immediately after user approves (before any resource creation):

```toml
[pat.{LABEL}]
status              = "CREATE_IN_PROGRESS"
created_at          = "{TIMESTAMP}"
rotated_at          = "{TIMESTAMP}"
sa_user             = "{SA_USER}"
sa_role             = "{SA_ROLE}"
pat_name            = "{SA_USER}_PAT"
comment_prefix      = "{COMMENT_PREFIX}"
sf_utils_db         = "{SFUTILS_DB}"
admin_role          = "{ADMIN_ROLE}"
default_expiry_days = {DEFAULT_EXPIRY}
max_expiry_days     = {MAX_EXPIRY}
local_ip            = "{LOCAL_IP}"
allow_github        = false
allow_google        = false
extra_cidrs         = []

[pat.{LABEL}.resources]
network_rule        = "{SFUTILS_DB}.NETWORKS.{SA_USER}_NETWORK_RULE"
network_policy      = "PENDING"
auth_policy         = "PENDING"
service_user        = "PENDING"
service_role        = "PENDING"
pat                 = "PENDING"

[pat.{LABEL}.cleanup]
user                = "{SA_USER}"
db                  = "{SFUTILS_DB}"
drop_user           = true
```

**After each subsequent resource is confirmed**, use the Edit tool to update the relevant `[pat.{LABEL}.resources]` field from `"PENDING"` to its actual value, and update `rotated_at`.

**After all resources are created and verify passes**, change `status = "COMPLETE"`:

```toml
[pat.{LABEL}]
status              = "COMPLETE"
created_at          = "{TIMESTAMP}"
rotated_at          = "{TIMESTAMP}"
sa_user             = "{SA_USER}"
sa_role             = "{SA_ROLE}"
pat_name            = "{SA_USER}_PAT"
comment_prefix      = "{COMMENT_PREFIX}"
sf_utils_db         = "{SFUTILS_DB}"
admin_role          = "{ADMIN_ROLE}"
default_expiry_days = {DEFAULT_EXPIRY}
max_expiry_days     = {MAX_EXPIRY}
local_ip            = "{LOCAL_IP}"
allow_github        = false
allow_google        = false
extra_cidrs         = []

[pat.{LABEL}.resources]
network_rule        = "{SFUTILS_DB}.NETWORKS.{SA_USER}_NETWORK_RULE"
network_policy      = "{SA_USER}_NETWORK_POLICY"
auth_policy         = "{SFUTILS_DB}.POLICIES.{SA_USER}_AUTH_POLICY"
service_user        = "{SA_USER}"
service_role        = "{SA_ROLE}"
pat                 = "{SA_USER}_PAT"

[pat.{LABEL}.cleanup]
user                = "{SA_USER}"
db                  = "{SFUTILS_DB}"
drop_user           = true
```

> **For multi-PAT projects:** Each additional PAT is a new `[pat.{LABEL}]` block. The label is the key used with `--profile` on the CLI — choose something meaningful like `app-runner` or `ci-runner`.

#### Multi-PAT: Independent Status Per Label

Each `[pat.<label>]` tracks its own lifecycle independently. Creating or removing one label never touches another:

```
[pat.app-runner]   status = "COMPLETE"           ← running, untouched
[pat.ci-runner]    status = "CREATE_IN_PROGRESS"  ← currently being created
```

After ci-runner completes:
```
[pat.app-runner]   status = "COMPLETE"
[pat.ci-runner]    status = "COMPLETE"
```

Removing app-runner only affects that label:
```
[pat.app-runner]   status = "DELETE_IN_PROGRESS" → REMOVED  (removed_at set)
[pat.ci-runner]    status = "COMPLETE"            ← unchanged
```

Use `sfutils-pat list` to see all labels and statuses at a glance.

---

#### Legacy Markdown Manifest (`sfutils-manifest.md`)

> **⚠️ DEPRECATED:** New projects use `manifest.toml`. The sections below are retained for projects that have not yet migrated. Use `sfutils-pat migrate` to convert.

**Manifest Location (legacy):** `.sfutils/sfutils-manifest.md`

**Create directory if needed:**

```bash
mkdir -p .sfutils
```

**If manifest doesn't exist, create with header:**

```markdown
# SF Utils Manifest

This manifest records all Snowflake resources created by sfutils skills.
Each skill section is bounded by START/END markers for easy identification.
Cortex Code uses this manifest to track, audit, and cleanup resources.

---
```

#### Progressive Manifest Writing

**Update manifest AFTER EACH resource is successfully created (not at the end).** Use the **file editing tool** (Edit/StrReplace) for all manifest updates.

This enables recovery if Cortex Code loses context mid-creation.

**After Step 1 (Network Rule created):**

```markdown
<!-- START -- programmatic-access-token -->
## PAT Resources: {COMMENT_PREFIX}

**Created:** {TIMESTAMP}
**User:** {SA_USER}
**Role:** {SA_ROLE}
**Database:** {SFUTILS_DB}
**Comment:** {COMMENT_PREFIX}
**Default Expiry (days):** {DEFAULT_EXPIRY}
**Max Expiry (days):** {MAX_EXPIRY}
**Actual Expiry:** {ACTUAL_EXPIRY}
**Status:** IN_PROGRESS

### Resources (creation order)

| # | Type | Name | Location | Status |
|---|------|------|----------|--------|
| 1 | Network Rule | {SA_USER}_NETWORK_RULE | {SFUTILS_DB}.NETWORKS | DONE |
| 2 | Network Policy | {SA_USER}_NETWORK_POLICY | Account | PENDING |
| 3 | Auth Policy | {SA_USER}_AUTH_POLICY | {SFUTILS_DB}.POLICIES | PENDING |
| 4 | Service User | {SA_USER} | Account | PENDING |
| 5 | PAT | {SA_USER}_PAT | Attached to {SA_USER} | PENDING |
<!-- END -- programmatic-access-token -->
```

**After each subsequent resource, update status from `PENDING` to `DONE`.**

**After all resources created, update Status to COMPLETE and add cleanup instructions section:**

```markdown
<!-- START -- programmatic-access-token -->
## PAT Resources: {COMMENT_PREFIX}

**Created:** {TIMESTAMP}
**User:** {SA_USER}
**Role:** {SA_ROLE}
**Database:** {SFUTILS_DB}
**Comment:** {COMMENT_PREFIX}
**Default Expiry (days):** {DEFAULT_EXPIRY}
**Max Expiry (days):** {MAX_EXPIRY}
**Actual Expiry:** {ACTUAL_EXPIRY}
**Status:** COMPLETE

### Resources (creation order)

| # | Type | Name | Location | Status |
|---|------|------|----------|--------|
| 1 | Network Rule | {SA_USER}_NETWORK_RULE | {SFUTILS_DB}.NETWORKS | DONE |
| 2 | Network Policy | {SA_USER}_NETWORK_POLICY | Account | DONE |
| 3 | Auth Policy | {SA_USER}_AUTH_POLICY | {SFUTILS_DB}.POLICIES | DONE |
| 4 | Service User | {SA_USER} | Account | DONE |
| 5 | PAT | {SA_USER}_PAT | Attached to {SA_USER} | DONE |

### Cleanup Instructions

> **🚨 CRITICAL: ALWAYS USE CLI COMMAND FOR CLEANUP**
>
> The CLI command handles dependency order, syntax, and error recovery automatically.
> **NEVER run raw SQL for cleanup** - use the script command below.

#### CLI Cleanup (REQUIRED)

> **Do not run this block from automation until** the user has confirmed cleanup in chat per **Remove Flow (Manifest-Driven Cleanup)** steps 4–5. The embedded `--yes` is for the subprocess only, not a substitute for that confirmation.

```bash
<SKILL_DIR>/pat remove --user {SA_USER} --db {SFUTILS_DB} --drop-user --yes
```

#### SQL Reference (FALLBACK ONLY - if CLI unavailable)

<details>
<summary>Manual SQL cleanup (dependency order - reverse of creation)</summary>

```sql
USE ROLE {ADMIN_ROLE};
-- 1. Remove PAT first (depends on user)
ALTER USER {SA_USER} REMOVE PAT {SA_USER}_PAT;
-- 2. Unassign auth policy (MUST do before drop)
ALTER USER {SA_USER} UNSET AUTHENTICATION POLICY;
-- 3. Unassign network policy (MUST do before drop) - NOTE: underscore required!
ALTER USER {SA_USER} UNSET NETWORK_POLICY;
-- 4. Drop user (now safe - no policy dependencies)
DROP USER IF EXISTS {SA_USER};
-- 5. Drop auth policy
DROP AUTHENTICATION POLICY IF EXISTS {SFUTILS_DB}.POLICIES.{SA_USER}_AUTH_POLICY;
-- 6. Drop network policy (frees the rule)
DROP NETWORK POLICY IF EXISTS {SA_USER}_NETWORK_POLICY;
-- 7. Drop network rule (last - policy depended on it)
DROP NETWORK RULE IF EXISTS {SFUTILS_DB}.NETWORKS.{SA_USER}_NETWORK_RULE;
```

</details>
<!-- END -- programmatic-access-token -->
```

#### Export for Sharing Flow

**Trigger phrases:** "export manifest for sharing"

**Purpose:** Create a portable copy of the manifest for another developer. See BEST_PRACTICES "Export for Sharing Flow" for the full specification.

**Summary:**

1. Verify ALL skill sections have `Status: COMPLETE`
2. Read `project_name` from `## project_recipe`
3. Ask user for export location (default: project root)
4. Create `{project_name}-manifest.md` with:
   - `<!-- CORTEX_CODE_INSTRUCTION -->` at top
   - `## shared_info` with origin metadata
   - ALL statuses set to `REMOVED`
   - `# ADAPT: user-prefixed` markers on user-prefixed values
   - Cleanup instructions stripped

**Setup from shared manifest:** See hirc-duckdb-demo SKILL.md for the full "setup from shared manifest" flow (project directory creation, manifest placement, then replay with name adaptation).

#### Remove Flow (Manifest-Driven Cleanup)

> **🚨 CRITICAL: Cleanup MUST be driven by the manifest.**
>
> The manifest contains the exact CLI command to run. NEVER construct cleanup SQL manually.

**Agent guardrail (non-negotiable):** Do **not** invoke `pat remove` (with or without `--yes` / `--drop-user`) from a terminal or tool until the user has **explicitly confirmed in this chat** (step 4 → step 5 below). The `--yes` flag only skips **in-process** `click.confirm` prompts inside the CLI when stdin is not a TTY; it **does not** replace asking the user **here** first. Running `remove … --yes` without that chat confirmation is a policy violation.

**⛔ Run Manifest Gate BEFORE anything else:**

```bash
<SKILL_DIR>/pat validate-manifest
```

- Passes → continue
- Fails → `<SKILL_DIR>/pat validate-manifest --fix` → re-validate → **STOP if still failing**

> A corrupted or partial manifest could produce a wrong cleanup command targeting the wrong user, database, or missing resources entirely. The gate is non-negotiable.

**On `remove` / `cleanup` / `delete` request:**

1. **Check manifest exists:**

   ```bash
   cat .sfutils/manifest.toml 2>/dev/null || echo "NOT_FOUND"
   ```

2. **If manifest NOT_FOUND:**
   - Inform user: "No manifest found. Cannot determine resources to clean up."
   - Ask: "Do you want to specify cleanup parameters manually?"
   - If yes, ask for SA_USER and SFUTILS_DB values

3. **If manifest EXISTS:**
   - Find the `[pat.{LABEL}]` entry where `sa_user = "{SA_USER}"` (or match by label directly)
   - Read `[pat.{LABEL}.cleanup]` fields: `user`, `db`, `drop_user`
   - Construct the CLI command:
     ```
     <SKILL_DIR>/pat remove --user {user} --db {db} --drop-user --yes
     ```
   - **Copy the exact CLI command** for use in step 5 — **do not run it until** the user answers **yes** to step 4

4. **Before executing, show user:**

   ```
   🗑️  Cleanup from manifest:

   Will remove resources for: {SA_USER}
   Using command from manifest:

   <CLI command from manifest>

   Proceed? [yes/no]
   ```

5. **On confirmation:** Only after the user answers **yes** to step 4:

   a. The CLI writes `status = "DELETE_IN_PROGRESS"` to the matching `[pat.{LABEL}]` entry automatically before any DROP commands execute. If running manually, use the Edit tool first:
   ```toml
   [pat.{LABEL}]
   status = "DELETE_IN_PROGRESS"
   ```

   b. Execute the CLI command from the manifest **once**, exactly as written (including `--yes` — that flag is required for non-interactive runs **after** the user has already approved in chat).

6. **After cleanup success:** The CLI writes `status = "REMOVED"` with `removed_at` automatically. If running manually, use the Edit tool:
   - Change `status = "DELETE_IN_PROGRESS"` → `status = "REMOVED"` on the matching `[pat.{LABEL}]` entry
   - Add `removed_at = "{TIMESTAMP}"` to that entry
   - **DO NOT delete the manifest** — preserve for audit/reference
   - User can manually delete `.sfutils/` folder if desired

> **Why preserve manifest?** The manifest serves as audit trail and reference for recreating resources.
> User can manually delete if no longer needed.

---

## Step 5c: Manifest Template for PAT Resources

Write to `.sfutils/sfutils-manifest.md`:

```bash
mkdir -p .sfutils
```

```markdown
# SF Utils Manifest

This manifest records all Snowflake resources created by sfutils skills.

---

<!-- START -- programmatic-access-token -->
## PAT Resources: {COMMENT_PREFIX}

**Created:** {TIMESTAMP}
**User:** {SA_USER}
**Role:** {SA_ROLE}
**Database:** {SFUTILS_DB}
**Comment:** {COMMENT_PREFIX}
**Default Expiry (days):** {DEFAULT_EXPIRY}
**Max Expiry (days):** {MAX_EXPIRY}
**Actual Expiry:** {ACTUAL_EXPIRY}
**Secret Key:** {SA_USER}_PAT *(optional; same as PAT resource name — not used by replay extraction; omit for a shorter manifest)*

**Status:** COMPLETE

| # | Type | Name | Location | Status |
|---|------|------|----------|--------|
| 1 | Network Rule | {SA_USER}_NETWORK_RULE | {SFUTILS_DB}.NETWORKS | DONE |
| 2 | Network Policy | {SA_USER}_NETWORK_POLICY | Account | DONE |
| 3 | Policy Assignment | → {SA_USER} | Account | DONE |
| 4 | Service Role | {SA_ROLE} | Account | DONE |
| 5 | Service User | {SA_USER} | Account | DONE |
| 6 | Auth Policy | {SA_USER}_AUTH_POLICY | {SFUTILS_DB}.POLICIES | DONE |
| 7 | PAT | {SA_USER}_PAT | Attached to {SA_USER} | DONE |

### Cleanup Instructions

Run this command to remove all resources (add `--drop-user` to also drop the service user). **Same rule as above:** confirm with the user in chat before running; `--yes` is only for non-interactive CLI after approval.

```bash
<SKILL_DIR>/pat \
  remove --user {SA_USER} --db {SFUTILS_DB} --yes
```
<!-- END -- programmatic-access-token -->
```

