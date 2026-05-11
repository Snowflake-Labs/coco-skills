---
name: network-rule
title: Setup Snowflake Network Rules
summary: Create and manage Snowflake network rules and policies for IP-based access control with TOML manifest tracking.
description: >-
  Setup and manage Snowflake network rules and policies using sfutils CLI
  with TOML manifest tracking.
  Use for ALL requests that mention: setting up network rules, creating network
  rules with GitHub Actions or Google IPs or local IP allowlisting, managing
  INGRESS/EGRESS/POSTGRES/INTERNAL_STAGE rules, CIDR allowlisting, firewall
  setup, external access integrations, EAI for SPCS or Snowpark.
  Triggers: sfutils-networks, setup-network-rules, network-rule, setup network,
  setup nw, allow gh, allow github, allow google, allow local, CIDR, egress rule,
  postgres ingress, AWSVPCEID, HOST_PORT, dry-run network, replay network,
  replay manifest, recreate network, export manifest, cleanup network,
  remove network rule, network manifest, multiple network rules, external access,
  call external API, OpenFlow connector, SPCS app, Snowpark external, EAI,
  integration, slack alert, pull from S3, HuggingFace, OpenAI from Snowflake.
  Do NOT use for: external volumes, PAT creation, general SQL queries,
  or non-network tasks.
aliases:
  - setup-network-rules
tools:
  - Bash
  - Read
  - Edit
  - Glob
  - Grep
  - ask_user_question
prompt: "$network-rule Setup an ingress network rule to allow GitHub Actions IPs to connect to Snowflake"
language: en
status: Published
author: Kamesh Sampath
type: Snowflake Staff
---

# Snowflake Network Rules & Policies

Creates and manages network rules and policies for IP-based access control in Snowflake.
Supports multiple rules per project via a TOML manifest — no `.env` required.

## Workflow

**📋 PREREQUISITE:** None. This skill can be used standalone or alongside other sfutils skills.

**📍 MANIFEST FILE:** `.sfutils/manifest.toml` (ALWAYS this exact path and filename)

> **⛔ DO NOT hand-edit manifests.** Manifests are machine-managed by Cortex Code. Manual edits can corrupt the format and break replay, cleanup, and export flows. Use skill commands to modify resources instead.

**🔗 CONNECTION NOTE:** The `nw` CLI reads the Snowflake connection directly from `manifest.toml [snowflake].connection` and auto-injects `-c <connection>` for every `snow sql` call. No `source .env` or `set -a && source .env && set +a` is needed.

**🔄 IDEMPOTENCY NOTE:** Network rules use `CREATE OR REPLACE` (Snowflake does not support `IF NOT EXISTS` for network rules). Network policies use `CREATE IF NOT EXISTS` to preserve existing policies. Re-running create operations is safe for automation.

**🚫 FORBIDDEN ACTIONS - NEVER DO THESE:**

- NEVER run SQL queries to discover/find/check values (no SHOW ROLES, SHOW DATABASES, SHOW NETWORK RULES)
- NEVER auto-populate empty manifest values by querying Snowflake
- NEVER use flags that bypass user interaction: `--auto-setup`, `--auto-approve`, `--quiet`, `--non-interactive`
- **`--yes` / `-y` is REQUIRED** when executing commands after user has approved the dry-run (CLIs prompt interactively which does not work in Cortex Code's non-interactive shell)
- NEVER assume user consent - always ask and wait for explicit confirmation
- NEVER skip SQL in dry-run output - always show BOTH summary AND full SQL
- **NEVER run raw SQL for cleanup** - ALWAYS use CLI commands (handles dependency order and detach/reattach)
- **NEVER drop a network rule before its network policy** - policies reference rules; dropping a rule that a policy still references will fail. Order: drop policy first, then rule. The CLI `rule delete` handles this automatically.
- **NEVER offer to drop SNOW_UTILS_DB** - it is shared infrastructure; cleanup only drops resources *inside* it (network rules, schemas), never the database itself
- **NEVER guess or invent CLI options** - ONLY use options from the CLI Reference tables; if a command fails with "No such option", run `<command> --help` and use ONLY those options
- NEVER use sed/awk/bash to edit manifest files — use the file editing tool (Edit/StrReplace)
- NEVER rely on `.env` for connection or configuration — manifest.toml is the source of truth

**✅ INTERACTIVE PRINCIPLE:** This skill is designed to be interactive. At every decision point, ASK the user and WAIT for their response before proceeding.

---

## Intent Router

Classify the user's request and dispatch to the appropriate workflow:

| User intent | Dispatch to |
|-------------|-------------|
| INGRESS — IP allowlist, CIDR, VPC, GitHub Actions connecting TO Snowflake | **→ [ingress-flows.md](ingress-flows.md)** |
| EGRESS HOST_PORT — SPCS/Snowpark/OpenFlow/Cortex calling external services, EAI | **→ [egress-flows.md](egress-flows.md)** (Section 1: EAI Builder) |
| EGRESS + Network Policy — account/user-level outbound restriction | **→ [egress-flows.md](egress-flows.md)** (Section 2) |
| POSTGRES_INGRESS / INTERNAL_STAGE | **→ [ingress-flows.md](ingress-flows.md)** |
| Both EGRESS and INGRESS in the same message | **→ [egress-flows.md](egress-flows.md) first**, complete it, then **→ [ingress-flows.md](ingress-flows.md)** |
| Replay, export, share manifest | **→ [Replay Flows](replay-flows.md)** |
| Validate, migrate, or repair manifest | **→ Network Manifest Gate + Step 0 below** |

> After completing Steps 0–2 (connection + infra), dispatch to the appropriate flow above.
> If the user message contains both EGRESS signals ("my SPCS app calls Slack") AND INGRESS
> signals ("allow my local IP"), use the "Both" row — complete EAI Builder, then INGRESS.

---


## Network Manifest Gate

**Run this check before any replay, remove, or manage-existing operation:**

```bash
<SKILL_DIR>/nw validate-manifest
```

- **If validation passes:** Continue with the operation.
- **If validation fails:** Run `<SKILL_DIR>/nw validate-manifest --fix`, then re-validate.
- **If issues remain after `--fix`:** Show the issues to the user and **STOP**. Do not proceed until the manifest is valid.

> This gate is also enforced automatically by the CLI before every subcommand. The output will warn you with `⚠️ manifest.toml has N issue(s)` if the manifest needs attention.

---

### Step 0: Migration Detection

**Check what state the project is in:**

```bash
ls .sfutils/ 2>/dev/null
```

**Case A — Legacy `.sfutils/sfutils-manifest.md` exists (no `manifest.toml` yet):**

```bash
<SKILL_DIR>/nw migrate --dry-run
```

Show the dry-run output. Ask user to confirm migration:

```
The project has a legacy sfutils-manifest.md.
Migrate to manifest.toml format? [yes/no]
```

On confirmation:
```bash
<SKILL_DIR>/nw migrate
```

After migration, `infra_ready = false` is always set — proceed to Step 2 to re-verify infrastructure.

**Case B — `manifest.toml` exists but has issues:**

```bash
<SKILL_DIR>/nw validate-manifest
```

If issues found:
```bash
<SKILL_DIR>/nw validate-manifest --fix
```

Re-validate. If issues remain, show them and STOP.

**Case C — New project (no `.sfutils/` or no manifest):**

Continue to Step 1.

**⚠️ STOP**: Resolve migration/validation issues before any resource creation.

### Step 1: Connection Setup

**Check manifest for existing connection:**

```bash
<SKILL_DIR>/nw validate-manifest 2>&1 | grep connection || true
cat .sfutils/manifest.toml 2>/dev/null | grep "connection"
```

**If `[snowflake].connection` is already set and valid:** Skip to Step 2.

**If connection is empty or manifest does not exist:**

1. List available connections:

   ```bash
   snow connection list --format json
   ```

2. Use `ask_user_question` to let the user pick a connection:

   ```
   Which Snowflake connection should this project use?
   (Options from snow connection list output)
   ```

3. Test and cache the connection:

   ```bash
   <SKILL_DIR>/nw setup-connection -c <chosen_connection>
   ```

   This writes `[snowflake].connection`, `account`, `user`, and `account_url` to `manifest.toml`.

**⚠️ STOP**: Do not proceed without a working connection.

### Step 2: Infrastructure Check

**Read infra status from manifest:**

```bash
cat .sfutils/manifest.toml 2>/dev/null | grep -E "sf_utils_db|infra_ready"
```

Strict ordered evaluation:

1. **`sf_utils_db` is empty** → Run check-setup (always):

   ```bash
   <SKILL_DIR>/nw check-setup --suggest
   ```

   If not ready, run with `--run-setup`:

   ```bash
   <SKILL_DIR>/nw check-setup --run-setup
   ```

2. **`sf_utils_db` set AND `infra_ready = true`** → Skip infra check, go to Step 2a.

3. **`sf_utils_db` set AND `infra_ready = false`** → Run check-setup to verify:

   ```bash
   <SKILL_DIR>/nw check-setup --suggest
   ```

   On success, `check-setup` writes `infra_ready = true` to manifest automatically.

**⚠️ STOP**: Do not proceed until `infra_ready = true` in manifest.

### Step 2a: Admin Role

Check admin_role from manifest `[snowflake].admin_role` (defaults to ACCOUNTADMIN).

If a non-ACCOUNTADMIN role is configured, verify it has required privileges:

| Privilege | Scope | Required For |
|-----------|-------|--------------|
| USAGE | Database | Accessing SF_UTILS_DB |
| CREATE NETWORK RULE | Schema | Creating network rules |
| CREATE NETWORK POLICY | Account | Creating network policies |

If privileges are missing, show the required GRANT statements and **STOP** for user to execute.

### Step 2b onwards: Dispatch to flow

After completing Step 2a (admin role check), dispatch based on rule intent:

- **INGRESS / POSTGRES_INGRESS / INTERNAL_STAGE** → continue in [ingress-flows.md](ingress-flows.md) starting at Step 2b
- **EGRESS HOST_PORT (EAI)** → continue in [egress-flows.md](egress-flows.md) Section 1
- **EGRESS + Network Policy** → continue in [egress-flows.md](egress-flows.md) Section 2

## Reference

- [INGRESS Flows](ingress-flows.md) — Steps 3–6 for INGRESS, POSTGRES_INGRESS, INTERNAL_STAGE rules + policy selection
- [EGRESS Flows](egress-flows.md) — EAI Builder (Section 1) + EGRESS Network Policy path (Section 2)
- [CLI Reference](cli-reference.md) — `check-setup` and `nw` commands, options, and mode-type constraints
- [Mode & Type Reference](mode-type-reference.md) — Rule mode selection UI, mode-type compatibility matrix, value input prompts, and EAI vs Policy decision (Step 2c)
- [Manifest Flows](manifest-flows.md) — Manifest template, export for sharing, and remove/cleanup flows
- [Replay Flows](replay-flows.md) — Replay single skill and replay all skills flows
- [Supplemental](supplemental.md) — Stopping points, output, SQL reference, troubleshooting, and security notes

