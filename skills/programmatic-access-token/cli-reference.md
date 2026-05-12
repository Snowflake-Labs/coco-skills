## Dry-Run Output Rule

**Terminal output gets collapsed/truncated by the UI.** After running any `--dry-run` command, you MUST:

1. Read the terminal output
2. Copy-paste the ENTIRE result into your response
3. Use language-tagged code blocks: `` ```text `` for summary, `` ```sql `` for SQL
4. Never just run the command silently or say "see output above"

> On pause/resume: Re-run `--dry-run` and paste the complete output again before asking for confirmation.

## PAT Security

**🚨 CRITICAL: NEVER display PAT tokens in ANY output:**

- Diff output (use `***REDACTED***` placeholder)
- Log messages
- Console output
- Summary displays
- Error messages
- Debug output

**Always mask as:** `***REDACTED***`

**PAT storage:** PAT is stored in the OS keyring automatically by `create` and `rotate`. It is **never** written to `.env` or any file. Only `SA_USER` and `SA_ROLE` are written to `.env` (via `--dot-env-file`). If you accidentally display a raw token value:

1. Immediately inform the user
2. Recommend rotating: `sfutils-pat rotate --user <SA_USER> --role <SA_ROLE>`

## CLI Invocation

This skill provides the `pat` wrapper script for simplified CLI access:

```bash
<SKILL_DIR>/pat <command> [options]       # main CLI (sfutils-pat)
<SKILL_DIR>/pat check-setup [options]     # pre-flight infra check
```

> Equivalent to `uv run --project <SKILL_DIR> sfutils-pat ...` and `uv run --project <SKILL_DIR> check-setup ...` respectively.
> Connection is read from `manifest.toml [snowflake].connection` automatically and passed as `-c <connection>` to all `snow sql` calls.

## Tools

### check-setup (bundled in the sfutils-pat package)

**Description:** Pre-flight check for sfutils infrastructure (database + schemas).

**Usage:**

```bash
<SKILL_DIR>/pat check-setup
```

**Options:**

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--database` | `-d` | No | from `SFUTILS_DB` env or `{USER}_SNOW_UTILS` | Database name to check/create |
| `--run-setup` | - | No | false | Run setup SQL if infrastructure missing |
| `--suggest` | - | No | false | Output suggested defaults as JSON |

### sfutils-pat CLI

**Description:** Creates and manages Snowflake PATs with network and auth policies.

**Global Options (BEFORE command):**

| Option | Short | Env Var | Default | Description |
|--------|-------|---------|---------|-------------|
| `--verbose` | `-v` | - | false | Enable verbose output |
| `--debug` | `-d` | - | false | Enable debug output (shows SQL and subprocess commands) |
| `--comment` | `-c` | `SF_UTILS_COMMENT` | auto | Comment prefix for SQL resources (inferred from SA_USER if not provided) |
| `--manifest-path` | `-m` | - | `.sfutils/manifest.toml` | Path to manifest.toml |

**CRITICAL RULES FOR COCO:**

| Rule | Description |
|------|-------------|
| **Always confirm** | NEVER execute create, remove, or rotate without explicit user confirmation |
| **Show what will happen** | Display SQL preview or summary BEFORE asking for confirmation |
| **One operation at a time** | Don't chain multiple destructive operations |
| **Fail fast** | Check prerequisites before running; stop with clear error if not met |

**Pre-Check Rules (Fail Fast):**

| Command | Pre-Check | If Fails |
|---------|-----------|----------|
| `create` | User doesn't exist | Stop: "User {SA_USER} already exists. Use `rotate` to refresh token or `remove` first." |
| `rotate` | PAT exists for user | Stop: "No existing PAT found for {SA_USER}. Use `create` instead." |
| `remove` | User/resources exist | Proceed gracefully (idempotent with IF EXISTS) |

**Command Selection Rules:**

| Scenario | Command | When to Use |
|----------|---------|-------------|
| New PAT needed | `create` | User has no existing PAT, or chose "Remove and recreate" in Step 3a |
| PAT exists, refresh token | `rotate` | User chose "Rotate existing" in Step 3a - keeps all policies |
| Full cleanup | `remove` | User explicitly requests cleanup, or "Remove and recreate" before create |
| Test connection | `verify` | After create or rotate to confirm PAT works |

**Confirmation Flow:**

1. **create**: Show SQL preview (Step 4) → Ask "Proceed with creation?" → Execute only on "yes"
2. **remove**: Show resources to be deleted → Ask "Confirm deletion of these resources?" → Execute only on "yes" in **this chat** → then pass `--yes` to the CLI only so a non-interactive subprocess can run (see [Manifest Flows](manifest-flows.md) for manifest-driven cleanup)  
3. **rotate**: Show current PAT info → Ask "Rotate PAT for user X?" → Execute only on "yes"

**Post-Operation Rules:**

| Command | After Success |
|---------|---------------|
| `create` | PAT stored in OS keyring; SA_USER and SA_ROLE written to manifest.toml → Run `verify` → manifest entry status set to COMPLETE |
| `rotate` | New PAT stored in OS keyring automatically → Run `verify` |
| `remove` | Read manifest first for exact names → Remove skill section from manifest |
| `replay` | Read manifest → Single info confirmation → Execute all steps → Update manifest progressively |
| `resume` | Read manifest (IN_PROGRESS) → Show completed/PENDING → Continue from first PENDING |

**Commands:**

| Command | Description |
|---------|-------------|
| `create` | Create service user, policies, and PAT |
| `remove` | Remove all PAT-related resources (dependency-aware order) |
| `rotate` | Regenerate PAT token, keeps all policies intact |
| `verify` | Test PAT connection using the Python connector (`authenticator='PROGRAMMATIC_ACCESS_TOKEN'`) |
| `list` | List all PATs from manifest.toml (label, sa_user, status, expiry) |
| `setup-connection` | Pick a Snowflake connection and persist it to manifest.toml |
| `validate-manifest` | Validate manifest.toml structure; `--fix` to auto-repair missing sections |
| `migrate` | Migrate legacy `.env` + `sfutils-manifest.md` to `manifest.toml` |

**🔴 COMMAND NAMES (exact -- do NOT substitute):**

- `create` -- NOT "setup", "make", "provision", "init"
- `remove` -- NOT "delete", "destroy", "cleanup", "drop"
- `rotate` -- NOT "refresh", "renew", "regenerate"
- `verify` -- NOT "check", "test", "validate", "ping"

**🔴 OPTION NAMES (NEVER guess or invent options):**

> ONLY use options listed in the CLI Reference tables below.
> If a command fails with "No such option", run `sfutils-pat <command> --help` to see actual available options and use ONLY those.
> NEVER invent, abbreviate, or rename options (e.g., `--dot-env-file` is NOT `--env-file`, `--default-expiry-days` is NOT `--expiry`).

#### create

> **⚠️ ALWAYS include `--default-expiry-days` and `--max-expiry-days` explicitly.**
> These are the exact CLI parameter names. NEVER substitute (e.g., `--expiry`, `--validity-days`, `--pat-expiry`).

```bash
<SKILL_DIR>/pat \
  create --user <SA_USER> --role <SA_ROLE> --db <SF_UTILS_DB> \
  --default-expiry-days <DEFAULT_EXPIRY> --max-expiry-days <MAX_EXPIRY> --output json
```

**Example with hardened baseline (7/30):**

```bash
<SKILL_DIR>/pat \
  create --user <SA_USER> --role <SA_ROLE> --db <SF_UTILS_DB> \
  --default-expiry-days 7 --max-expiry-days 30 --output json
```

**Example with custom expiry (7/30):**

```bash
<SKILL_DIR>/pat \
  create --user <SA_USER> --role <SA_ROLE> --db <SF_UTILS_DB> \
  --default-expiry-days 7 --max-expiry-days 30 --output json
```

**Options:**

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--user` | `-u` | `SA_USER` | No | - | Service user name (or use --profile) |
| `--role` | `-r` | `SA_ROLE` | No | - | PAT role restriction (or use --profile) |
| `--profile` | `-p` | - | No | - | PAT label from manifest.toml — resolves --user and --role |
| `--connection` | - | - | No | - | Snowflake connection for this PAT (overrides manifest default; stores metadata in PAT entry) |
| `--db` | `-d` | `SFUTILS_DB` | Yes | - | Database for policy objects |
| `--pat-name` | - | `PAT_NAME` | No | `{USER}_PAT` | Name for the PAT token |
| `--rotate/--no-rotate` | - | - | No | true | Rotate existing PAT |
| `--env-path` | - | - | No | `.env` | Path to .env file |
| `--skip-verify` | - | - | No | false | Skip connection verification after creation |
| `--allow-local/--no-local` | - | - | No | true | Include auto-detected local IP |
| `--allow-gh` | - | - | No | false | Add `SNOWFLAKE.NETWORK_SECURITY.GITHUBACTIONS_GLOBAL` (Snowflake-managed SaaS rule) to the network policy — auto-updated by Snowflake; no role access to `SNOWFLAKE.NETWORK_SECURITY` required; not available in gov regions |
| `--allow-google` | - | - | No | false | Include Google Cloud IPs |
| `--extra-cidrs` | - | - | No | - | Additional CIDRs (can be repeated) |
| `--default-expiry-days` | - | - | No | 7 | PAT default expiry |
| `--max-expiry-days` | - | - | No | 30 | PAT max expiry |
| `--dry-run` | - | - | No | false | Preview SQL without executing |
| `--admin-role` | `-a` | - | No | `accountadmin` | Admin role for creating resources |
| `--force` | `-f` | - | No | false | Overwrite existing network rule/policy (CREATE OR REPLACE) |
| `--output` | `-o` | - | No | `text` | Output format: `text` or `json` |
| `--skip-network` | - | - | No | false | Skip built-in network rule/policy creation (only pass when you separately ran sfutils-networks for a shared network policy) |
| `--dot-env-file` | - | - | No | - | Write SA_USER and SA_ROLE only to this .env file (PAT never written to disk) |
| `--yes` | `-y` | - | No | false | Skip interactive confirmation (REQUIRED for Cortex Code automation) |

#### remove

Removes all PAT-related resources in correct dependency order:
PAT → Auth Policy (unset) → User → Auth Policy (drop) → Network Policy → Network Rule

```bash
<SKILL_DIR>/pat \
  remove --user <SA_USER> --db <SF_UTILS_DB>
```

**Options:**

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--user` | `-u` | `SA_USER` | Yes | - | Service user to remove |
| `--db` | `-d` | `SFUTILS_DB` | Yes | - | Database containing policies |
| `--pat-name` | - | `PAT_NAME` | No | `{USER}_PAT` | Name of the PAT to remove |
| `--drop-user` | - | - | No | false | Also drop the service user |
| `--pat-only` | - | - | No | false | Only remove PAT, keep policies |
| `--admin-role` | `-a` | - | No | `accountadmin` | Admin role for removing resources |
| `--env-path` | - | - | No | `.env` | .env file path |
| `--yes` | - | - | No | - | Auto-confirm removal (Click confirmation_option) |

#### rotate

Regenerates PAT token while keeping all existing policies intact.

```bash
<SKILL_DIR>/pat \
  rotate --user <SA_USER> --role <SA_ROLE>
```

**Options:**

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--user` | `-u` | `SA_USER` | Yes | - | Service user with existing PAT |
| `--role` | `-r` | `SA_ROLE` | Yes | - | Role restriction for new PAT |
| `--pat-name` | - | `PAT_NAME` | No | `{USER}_PAT` | Name for the PAT token |
| `--admin-role` | `-a` | - | No | `accountadmin` | Admin role for rotation |
| `--env-path` | - | - | No | `.env` | .env file path |
| `--skip-verify` | - | - | No | false | Skip connection verification after rotation |
| `--output` | `-o` | - | No | `text` | Output format: `text` or `json` |
| `--print` | - | - | No | false | Print PAT to stdout after storing (insecure; cannot use with `-o json`) |

**After rotation:** New token is stored automatically in the OS keyring. Run verify to confirm:

```bash
<SKILL_DIR>/pat \
  verify --user <SA_USER> --role <SA_ROLE>
```

#### verify

Tests PAT authentication using the Python connector (`authenticator='PROGRAMMATIC_ACCESS_TOKEN'`). Does not use `snow sql` or `SNOWFLAKE_PASSWORD`.

```bash
<SKILL_DIR>/pat \
  verify --user <SA_USER> --role <SA_ROLE>
```

**Options:**

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--user` | `-u` | `SA_USER` | Yes | - | Service user to verify |
| `--role` | `-r` | `SA_ROLE` | Yes | - | Role to test with |
| `--pat-name` | - | `PAT_NAME` | No | `{USER}_PAT` | Name of the PAT token to verify |

> **PAT loaded from OS keyring only.** No `--password` flag or `SA_PAT` env fallback. The token must be in the keyring (stored automatically by `create` and `rotate`).

**Verification runs:** `SELECT current_timestamp()` to confirm auth works.

#### show-pat

Prints the raw PAT token for a service user from the OS keyring.

**⛔ Cortex Code must NEVER run this command.** In non-interactive mode it requires `--yes`, which would print the raw token into bash output and conversation history, leaking the secret.

**If the user needs the raw token**, tell them to run this command themselves in their own terminal:

```bash
sfutils-pat show-pat --user <SA_USER>
```

The CLI will prompt for confirmation before printing. The token is shown in the terminal only and never captured by Cortex Code.

---

#### setup-connection

Persist a Snowflake connection to manifest.toml as the project default.

```bash
<SKILL_DIR>/pat setup-connection -c <connection_name>
```

Runs `snow connection test`, caches `account`/`user`/`account_url` in `[snowflake]`, and makes manifest.toml the source of truth. Run this once per project after picking a connection from `snow connection list`.

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--connection` | `-c` | Yes | - | Snowflake connection name (from `snow connection list`) |
| `--admin-role` | - | No | `ACCOUNTADMIN` | Admin role to cache in manifest.toml |

---

#### validate-manifest

Validate manifest.toml structure. Exits 1 if invalid (usable as a CI gate).

```bash
<SKILL_DIR>/pat validate-manifest
<SKILL_DIR>/pat validate-manifest --fix   # repair structural gaps then validate
```

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--fix` | No | false | Fill missing sections with defaults before validating |

---

#### list

List all PATs in manifest.toml with status.

```bash
<SKILL_DIR>/pat list
```

Output: label / sa_user / status / expiry (def/max). Use to find `--profile` labels.

---

#### migrate

Migrate legacy `.env` + `sfutils-manifest.md` to `manifest.toml`.

```bash
<SKILL_DIR>/pat migrate --dry-run   # preview
<SKILL_DIR>/pat migrate             # write
```

Does NOT delete old files.

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `--env-path` | No | `.env` | Source .env file |
| `--manifest-md` | No | `.sfutils/sfutils-manifest.md` | Source markdown manifest |
| `--dry-run` | No | false | Preview without writing |
