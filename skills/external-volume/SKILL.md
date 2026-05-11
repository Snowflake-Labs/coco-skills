---
name: external-volume
title: Setup Snowflake External Volumes
summary: Create and manage Snowflake external volumes with cloud storage (AWS S3) for Iceberg tables and data lake access.
description: >-
  Create Snowflake external volumes with cloud storage.
  Use for ALL requests that mention: setting up external storage, creating
  external volume, configuring S3 for Snowflake, Iceberg tables, unloading
  data, cloud storage, COPY INTO unload, external stage storage, data lake
  storage, IAM trust policy, volume verification.
  Triggers: sfutils-extvolumes, setup-external-volume, external volume,
  create external volume, s3 snowflake, iceberg storage, data lake storage,
  COPY INTO location, external stage, unload to S3, replay volumes,
  replay volume manifest, recreate external volume, replay all manifests,
  replay all sfutils, export manifest for sharing, setup from shared manifest,
  replay from shared manifest, setup from manifest URL, replay from URL,
  use manifest from URL, azure, gcs, blob storage, cloud storage,
  multiple volumes, list volumes, setup connection, validate manifest,
  migrate manifest, second external volume.
  Do NOT use for: network rules, PAT creation, general SQL queries,
  or non-storage tasks.
aliases:
  - setup-external-volume
  - external-volumes
  - setup-external-volumes
tools:
  - Bash
  - Read
  - Edit
  - Glob
  - Grep
  - ask_user_question
prompt: "$external-volume Create an external volume backed by my S3 bucket for Iceberg tables"
language: en
status: Published
author: Kamesh Sampath
type: Snowflake Staff
---

# External Volume Setup

Creates cloud storage resources and a Snowflake external volume for storage access. **Today only the AWS S3 path is implemented**; other backends are roadmap-only (see Step 4).

**Supported now:** AWS S3

**Roadmap (not enabled):** Azure Blob Storage, Google Cloud Storage — do not offer as multiple-choice options; mention in prose only until workflows ship.

**Use cases:** Iceberg tables, data lake access, COPY INTO unload, external stages.

## Workflow

**⚠️ CONNECTION USAGE:** This skill reads the Snowflake connection from `[snowflake].connection` in `manifest.toml`. The CLI passes `-c <connection>` to all `snow sql` calls automatically — no manual env export needed. Admin_role defaults to `[snowflake].admin_role` or ACCOUNTADMIN.

**📌 CONNECTION NOTE:** The CLI reads connection from `manifest.toml` and auto-injects `-c <connection>` to all `snow sql` calls. No `.env` sourcing needed. Always run `vol setup-connection` to set the connection in `manifest.toml` before any other command.

**📋 NO PREREQUISITE:** This skill does NOT require sfutils-pat. It operates independently.

> **📋 MANIFEST AS SOURCE OF TRUTH**
>
> **📍 Location:** `.sfutils/manifest.toml` (ALWAYS this exact path — TOML format, NOT the legacy `.sfutils/sfutils-manifest.md`)
>
> **⛔ DO NOT hand-edit manifests.** Manifests are machine-managed by Cortex Code. Manual edits can corrupt the format and break replay, cleanup, and export flows. Use skill commands to modify resources instead.
>
> **🔒 Security:** Secured like `.ssh` (chmod 700 directory, chmod 600 files)
>
> **Skill-Scoped Admin Roles:**
>
> - Volumes default: `ACCOUNTADMIN` (CREATE EXTERNAL VOLUME privilege)
> - **External volumes are account-level objects — no database or schema setup needed**

**🚫 FORBIDDEN ACTIONS - NEVER DO THESE:**

- NEVER run SQL queries to discover/find/check values (no SHOW ROLES, SHOW DATABASES, SHOW EXTERNAL VOLUMES)
- NEVER reference SF_UTILS_DB, SNOW_UTILS_DB, or sf_utils_db — external volumes are account-level objects with no database dependency
- NEVER use `source .env`, `set -a && source .env && set +a`, or `load_dotenv()` for any `vol` command
- NEVER read `BUCKET`, `EXTVOLUME_PREFIX`, `EXTERNAL_VOLUME_NAME`, or `AWS_REGION` from `.env` to pass to `vol` commands — all values MUST be explicit CLI flags or derived from `manifest.toml`
- NEVER auto-populate empty manifest values by querying Snowflake
- NEVER use flags that bypass user interaction: `--auto-setup`, `--auto-approve`, `--quiet`, `--non-interactive`
- **`--yes` / `-y` is REQUIRED** when executing commands after user has approved the dry-run
- NEVER assume user consent - always ask and wait for explicit confirmation
- NEVER skip SQL/JSON in dry-run output - always show BOTH summary AND full SQL/JSON
- NEVER hardcode admin roles - get admin_role from manifest
- NEVER skip manifest - always update manifest IMMEDIATELY after user input
- NEVER leave .sfutils unsecured - always chmod 700/600
- NEVER delete .sfutils directory or manifest file - preserve for audit/cleanup/replay
- **NEVER guess or invent CLI options** - ONLY use options from the CLI Reference tables
- **NEVER use sed/awk/bash to edit manifest files** — use the file editing tool (Edit/StrReplace)
- **NEVER run raw SQL for cleanup** — ALWAYS use `vol delete`
- **NEVER run `vol create` without first running `vol ... create --dry-run`** — mandatory, non-negotiable
- **NEVER show Step 4 as only** "Only AWS S3 is supported" without the full three-provider list below
- **NEVER assume cwd is the project directory without first confirming project root**

**✅ REQUIRED ACTIONS - ALWAYS DO THESE:**

- **ALWAYS run `--dry-run` before any `vol create --yes`. Non-negotiable even if user asks to skip.**
- **ALWAYS run the [Volume Manifest Gate](#volume-manifest-gate) before any manifest-dependent operation (replay, remove, manage-existing).**
- **ALWAYS use `ask_user_question` tool (not plain text output) to collect destructive confirmations.**

**✅ INTERACTIVE PRINCIPLE:** This skill is designed to be interactive. At every decision point, ASK the user and WAIT for their response before proceeding.

> **📋 `.env` SCOPE:** `.env` is read **only** by `vol migrate` to extract legacy Snowflake connection values (`SNOWFLAKE_DEFAULT_CONNECTION_NAME`, etc.). No other `vol` command reads `.env`. All operational values — bucket, region, prefix, volume name — come from `manifest.toml` or explicit CLI flags. If a `.env` file exists in the project after migration, **ignore it entirely** for all `vol` commands.

> **📋 `.env` SCOPE:** `.env` is read **only** by `vol migrate` to extract legacy Snowflake connection values (`SNOWFLAKE_DEFAULT_CONNECTION_NAME`, etc.). No other `vol` command reads `.env`. All operational values — bucket, region, prefix, volume name — come from `manifest.toml` or explicit CLI flags. If a `.env` file exists in the project after migration, **ignore it entirely** for all `vol` commands.

---

## Volume Manifest Gate

**Non-negotiable precondition for ALL manifest-dependent flows: replay, remove, manage-existing.**

Run before ANY of those operations:

```bash
<SKILL_DIR>/vol validate-manifest
```

**If validation passes:** proceed immediately.

**If validation fails:**

```bash
<SKILL_DIR>/vol validate-manifest --fix
```

Re-run `validate-manifest`. If still failing after `--fix`:

- These are **non-structural** issues (e.g. empty `connection`) that `--fix` cannot auto-repair
- Show the errors to the user, **STOP**, do not proceed
- Provide actionable next steps (e.g. "run `vol setup-connection` to set connection")

**If `[snowflake].connection` is empty after fix:** redirect to Step 1 (connection picker) before continuing.

---

### Locate Project Directory

**Run this before any other step — never skip or assume you are already in the right place.**

1. **If the user provided a project directory path** → create it if needed and `cd` to it.
2. Check if `.sfutils/manifest.toml` exists in the current directory:

   ```bash
   ls -la .sfutils/manifest.toml 2>/dev/null && echo "FOUND" || echo "NOT_FOUND"
   ```

3. **If found** → current directory is your project root. Proceed to Step 0.
4. **If not found** — check for legacy manifest:

   ```bash
   ls -la .sfutils/sfutils-manifest.md 2>/dev/null && echo "LEGACY" || echo "NOT_FOUND"
   ```

   - **Legacy found** → run `vol migrate` (Step 0, Case A)
   - **Neither found** and user mentioned existing project → ask for project directory path
   - **Fresh setup** → use current directory, proceed to Step 0

---

### Step 0: Migration / Prerequisite Detection

Check project state:

```bash
ls .sfutils/sfutils-manifest.md 2>/dev/null && echo "LEGACY" || true
ls .sfutils/manifest.toml 2>/dev/null && echo "TOML" || true
```

**Case A — legacy manifest found, no TOML:**

```bash
<SKILL_DIR>/vol migrate --dry-run
```

Review dry-run output, confirm with user, then:

```bash
<SKILL_DIR>/vol migrate
```

After migrate, always run `vol check-setup` (Step 2) since `tools_verified` will be empty.

**Case B — TOML manifest found:**

Run Volume Manifest Gate. If issues → `vol validate-manifest --fix`. Proceed to Step 1.

**Case C — neither found (new project):**

Proceed directly to Step 1 to set up connection and create `manifest.toml`.

> **✅ FAST-PATH:** If manifest exists and `tools_verified` has a date → skip Step 2 check-setup entirely.

---

### Step 1: Connection Setup

Check manifest for existing connection:

```bash
cat .sfutils/manifest.toml 2>/dev/null | grep "^connection"
```

**If connection is set and non-empty:** use it, skip to Step 2.

**If connection is empty or manifest doesn't exist:**

```bash
snow connection list --format json
```

Use `ask_user_question` tool to present connection options to the user.

Then test and cache to manifest:

```bash
<SKILL_DIR>/vol setup-connection -c <chosen_connection>
```

This writes `[snowflake].connection`, `account`, `user`, and `account_url` to `manifest.toml`.

**If connection test fails:** show error, ask user to check `snow connection list` and try again.

---

### Step 2: Prerequisites Check (Tool Verification)

Read prereqs from manifest:

```bash
cat .sfutils/manifest.toml 2>/dev/null | grep "tools_verified"
```

**If `tools_verified` has a date:** Skip to Step 2c — tools already verified.

**If `tools_verified` is empty or missing:**

```bash
<SKILL_DIR>/vol check-setup --provider s3
```

This checks:
- `snow` CLI available
- `aws` CLI available (for S3 provider)
- AWS credential env signal (profile, static keys, web identity)

On success, sets `[prereqs].tools_verified` to today's date in `manifest.toml`.

**If `csp_tools_ready: false`** in the output: the required CSP CLI tool is missing. Show what's missing and stop.

**⚠️ STOP:** Do not proceed until tools are verified.

---

### Step 2a: Admin Role from Manifest

Admin role is read from `manifest.toml [snowflake].admin_role` (default: ACCOUNTADMIN).

Check manifest:

```bash
cat .sfutils/manifest.toml 2>/dev/null | grep "admin_role"
```

**If admin_role is set:** use it.

**If NOT set**, prompt user:

```
External volume creation requires CREATE EXTERNAL VOLUME privilege.

Snowflake recommends: ACCOUNTADMIN (has this privilege by default)

Enter admin role for volumes [ACCOUNTADMIN]:
```

**⚠️ STOP:** Wait for user input. Then cache to manifest via `vol setup-connection --admin-role <role>`.

---

### Step 2c: Multi-Volume Selection

```bash
<SKILL_DIR>/vol list
```

**If no volumes exist:** proceed to Step 3 (new volume).

**Ask user:**
- Add a new external volume → proceed to Step 3
- Manage an existing volume → show describe / update-trust / remove options

**Multi-volume note:** After each volume creation fully completes Steps 4–8 (dry-run → create → IAM propagation → verify → `vol validate-manifest`), return here to offer creating another. Do NOT proceed to the next volume until the current one is COMPLETE in the manifest.

---

### Step 3: Check Existing External Volume

If user is managing an existing volume or provided a volume name:

```bash
<SKILL_DIR>/vol describe --volume-name <VOLUME_NAME>
```

**If volume exists:** Ask user:
1. Use existing volume (done — provide connection details)
2. Delete and recreate
3. Create new volume with different name

**If volume doesn't exist or new volume requested:** Continue to Step 4.

---

### Step 4: Storage provider (S3 only today)

> **📝 NOTE:** This limitation is about **creating Storage Provider resources** (cloud-side infrastructure). Snowflake supports S3, Azure Blob Storage, and GCS as external volume backends. This skill currently only automates provisioning of **AWS S3** cloud resources.

**Only AWS S3 is implemented.** Always show all three providers — never compress to a one-line question.

> **✅ SELF-CHECK before sending your response:** Does your message contain all three provider lines? If not, insert the full block below.

**MANDATORY — paste the following to the user as one unit:**

```
Storage provider:

  (•) AWS S3 — supported in this skill and CLI today
  ( ) Azure Blob Storage — unavailable (planned / work in progress; not in sfutils-extvolumes yet)
  ( ) Google Cloud Storage — unavailable (planned / work in progress; not in sfutils-extvolumes yet)

Only AWS S3 is supported right now. Proceed with AWS S3 for this external volume?
```

**⚠️ STOP:** Wait for confirmation unless user already named S3 as provider.

**Then:**

1. Collect S3-specific inputs (bucket, region, aws-profile if applicable)
2. Run S3 provider prereq checks (from [S3 Workflow](workflow-s3.md))
3. Execute [S3 Workflow](workflow-s3.md) Steps 4-6

**After workflow-s3.md Step 6 completes, return here for Step 7.**

---

### Step 7: Verify

```bash
<SKILL_DIR>/vol verify --volume-name <VOLUME_NAME>
```

The CLI automatically retries with exponential backoff on IAM propagation lag — no manual `sleep` needed. If verification still fails after all retries, the error is shown with the last `storageLocationSelectionResult`.

**Present** verification result and continue to Step 8.

---

### Step 8: Write Success Summary and Validate Manifest

The CLI automatically writes the volume entry to `.sfutils/manifest.toml` after creation.

Run manifest validation to confirm it is well-formed:

```bash
<SKILL_DIR>/vol validate-manifest
```

If validation fails: run `vol validate-manifest --fix` and re-check.

See [Manifest Flows](manifest-flows.md) for the full manifest template format and example Iceberg DDL.

> **Iceberg users:** If you encounter `Access Denied`, trust policy errors, or `SYSTEM$VERIFY_EXTERNAL_VOLUME` failures after the volume is created, load the **`iceberg/external-volume`** sub-skill for cloud-provider-specific troubleshooting.

---

## Maintenance Operations

Use these workflows when the user asks to inspect or repair an existing external volume.

### List All Volumes

```bash
<SKILL_DIR>/vol list
```

Shows: LABEL / VOLUME_NAME / TYPE / STATUS for all volumes in manifest.toml.

### Describe Volume

```bash
<SKILL_DIR>/vol describe --volume-name <VOLUME_NAME>
```

Presents: storage location, IAM user ARN, external ID, and status.

### Update Trust Policy

When the user reports IAM trust policy mismatch:

```bash
<SKILL_DIR>/vol update-trust --bucket <BUCKET>
```

After update completes, run `vol verify --volume-name <VOLUME_NAME>` to confirm access is restored.

### Remove Volume

**Always run Volume Manifest Gate first.**

```bash
<SKILL_DIR>/vol list                                          # confirm label/name
<SKILL_DIR>/vol validate-manifest                             # gate check
<SKILL_DIR>/vol delete --bucket <BUCKET> --yes --output json  # remove resources
```

After delete: volume entry in manifest.toml is marked `status = "REMOVED"`, entry preserved.

---

## Reference

- [CLI Reference](cli-reference.md) — all commands, options, and usage
- [Manifest Flows](manifest-flows.md) — TOML manifest template, progressive write, remove flow
- [Prereqs](prereqs.md) — initialization, migration detection, tool verification
- [Replay Flows](replay-flows.md) — export for sharing, replay flows
- [Supplemental](supplemental.md) — SQL reference, troubleshooting, privilege escalation hints
- [S3 Workflow](workflow-s3.md) — self-contained AWS S3 provider workflow (Steps 4-6)

## Stopping Points

1. Step 0: Migration detected — confirm with user before `vol migrate`
2. Step 1: If connection test fails
3. Step 2: If CSP tools missing
4. Step 2a: If admin_role not set (prompts user)
5. Step 2c: Ask user Add new / Manage existing
6. Step 3: If volume exists (ask user what to do)
7. Step 4: Show S3 vs Azure/GCS (Azure/GCS disabled); optional "Proceed with S3?" confirmation
8. Step 4 (S3 workflow): Provider prereqs, requirements gathering, dry-run approval

## Output

- Cloud storage bucket/container with appropriate settings
- IAM policy/role or service principal for access
- Snowflake external volume
- Updated `.sfutils/manifest.toml` with volume entry
