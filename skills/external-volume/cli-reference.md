## CLI Invocation

This skill provides the `vol` wrapper script for simplified CLI access:

```bash
<SKILL_DIR>/vol <command> [options]       # main CLI (sfutils-extvolumes)
<SKILL_DIR>/vol check-setup [options]     # pre-flight tool check
```

> Equivalent to `uv run --project <SKILL_DIR> sfutils-extvolumes ...` and `uv run --project <SKILL_DIR> check-setup ...` respectively.

**Connection:** The CLI reads `[snowflake].connection` from `manifest.toml` and passes `-c <connection>` to all `snow sql` calls automatically. No `.env` sourcing needed.

**`.env` scope:** `.env` is read **only** by `vol migrate` to extract legacy connection values (`SNOWFLAKE_DEFAULT_CONNECTION_NAME`, etc.). All other `vol` commands derive values exclusively from `manifest.toml` and explicit CLI flags. Never set `BUCKET`, `EXTVOLUME_PREFIX`, or `EXTERNAL_VOLUME_NAME` in `.env` for operational use.

**`.env` scope:** `.env` is read **only** by `vol migrate` to extract legacy connection values (`SNOWFLAKE_DEFAULT_CONNECTION_NAME`, etc.). All other `vol` commands derive values exclusively from `manifest.toml` and explicit CLI flags. Never set `BUCKET`, `EXTVOLUME_PREFIX`, or `EXTERNAL_VOLUME_NAME` in `.env` for operational use.

## Tools

### check-setup (bundled in the sfutils-extvolumes package)

**Description:** Pre-flight check for snow CLI and CSP CLI tools (aws/az/gcloud) plus
credential-related environment variables. External volumes are account-level Snowflake
objects — no database setup is required.

On success, writes `[prereqs].tools_verified` to `manifest.toml`.

**Usage:**

```bash
<SKILL_DIR>/vol check-setup
<SKILL_DIR>/vol check-setup --suggest
<SKILL_DIR>/vol check-setup --suggest --provider s3
```

**Options:**

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--suggest` | - | No | false | Output tool-readiness as JSON |
| `--provider` | - | No | `s3` | Which storage provider's CSP CLIs to check: `s3`, `azure`, or `gcs` |
| `--admin-role` | - | No | ACCOUNTADMIN | Admin role to cache in manifest |
| `--manifest-path` | - | No | `.sfutils/manifest.toml` | Path to manifest.toml |

**`--suggest` JSON fields:**

| Field | Description |
|-------|-------------|
| `ready` | True if all required CSP CLI tools are on PATH |
| `csp_tools_ready` | All required CSP CLI executables for `--provider` are on `PATH` |
| `csp_cli_tools` | List of `{ "provider", "tool", "available" }` for that provider |
| `supported_storage_providers` | Backends implemented in sfutils today (e.g. `S3`) |
| `planned_storage_providers` | Roadmap backends not yet wired in the skill/CLI |
| `csp_credential_env` | List of `{ "name", "set" }` for watched credential-related vars (never values) |
| `csp_credential_env_signal` | True if **any one** OR-branch matched for that provider |
| `csp_credential_env_satisfied_by` | Which branch matched first (`static_keys`, `profile`, `web_identity`, etc.) or `null` |
| `credential_env_note` | Neutral hint when no env signal; `null` when signal is true |

For AWS, **one** satisfied branch is enough — do not treat unset watched vars as required.

---

### sfutils-extvolumes CLI

**Description:** Creates and manages Snowflake external volumes.

> **Note:** The CLI currently supports **AWS S3 only**. Azure Blob Storage and GCS support are planned.

**🔴 COMMAND NAMES (exact — do NOT substitute):**

- `create` — NOT "setup", "make", "provision", "init"
- `delete` — NOT "remove", "destroy", "cleanup", "drop"
- `verify` — NOT "check", "test", "validate", "ping"
- `describe` — NOT "show", "get", "info", "status"
- `update-trust` — NOT "sync-trust", "refresh-trust"
- `setup-connection` — NOT "configure", "set-connection", "init-connection"
- `validate-manifest` — NOT "check-manifest", "verify-manifest"
- `list` — NOT "ls", "show-volumes"
- `migrate` — NOT "import", "convert", "upgrade"

**🔴 OPTION NAMES (NEVER guess or invent options):**

> ONLY use options listed in the tables below.
> If a command fails with "No such option", run `<SKILL_DIR>/vol <command> --help` and use ONLY those options.

**Global Options (pass BEFORE the command):**

| Option | Short | Env Var | Default | Description |
|--------|-------|---------|---------|-------------|
| `--region` | `-r` | `AWS_REGION` | `us-west-2` | AWS region |
| `--prefix` | `-p` | - | current username | Prefix for AWS resources |
| `--no-prefix` | - | - | false | Disable username prefix |
| `--verbose` | `-v` | - | false | Enable verbose output |
| `--debug` | - | - | false | Enable debug output (shows SQL) |
| `--comment` | `-c` | - | auto | Comment for external volume |
| `--manifest-path` | - | - | `.sfutils/manifest.toml` | Path to manifest.toml |

---

### `create`

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--bucket` | `-b` | - | Yes | - | S3 bucket base name |
| `--role-name` | - | - | No | `{prefix}-{bucket}-snowflake-role` | IAM role name |
| `--policy-name` | - | - | No | `{prefix}-{bucket}-snowflake-policy` | IAM policy name |
| `--volume-name` | - | - | No | `{PREFIX}_{BUCKET}_EXTERNAL_VOLUME` | Snowflake external volume name |
| `--storage-location-name` | - | - | No | `{prefix}-{bucket}-s3-{region}` | Storage location name |
| `--external-id` | - | - | No | auto-generated | External ID for trust relationship |
| `--aws-profile` | - | `AWS_PROFILE` | No | - | AWS profile name for boto3 session |
| `--no-writes` | - | - | No | false | Create read-only external volume |
| `--skip-verify` | - | - | No | false | Skip external volume verification |
| `--dry-run` | - | - | No | false | Preview what would be created |
| `--force` | `-f` | - | No | false | Overwrite existing volume (CREATE OR REPLACE) |
| `--yes` | `-y` | - | No | false | Accepted for scripting compatibility (no interactive prompt on create) |

---

### `delete`

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--bucket` | `-b` | - | Yes | - | S3 bucket base name |
| `--role-name` | - | - | No | `{prefix}-{bucket}-snowflake-role` | IAM role name |
| `--policy-name` | - | - | No | `{prefix}-{bucket}-snowflake-policy` | IAM policy name |
| `--volume-name` | - | - | No | `{PREFIX}_{BUCKET}_EXTERNAL_VOLUME` | Snowflake volume name |
| `--delete-bucket` | - | - | No | false | Also delete the S3 bucket |
| `--force` | - | - | No | false | Force delete bucket even if not empty |
| `--yes` | `-y` | - | No | false | Skip confirmation prompt |
| `--output` | `-o` | - | No | `text` | Output format: `text` or `json` |

After delete, the CLI marks the volume `status = "REMOVED"` in `manifest.toml`.

---

### `verify`

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--volume-name` | `-v` | - | Yes | - | Snowflake external volume name |
| `--retry` | - | - | No | false | Retry with exponential backoff on failure (for IAM propagation lag) |

---

### `describe`

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--volume-name` | `-v` | - | Yes | - | Snowflake external volume name |

---

### `update-trust`

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--bucket` | `-b` | - | No | - | S3 bucket base name (to derive role/volume names) |
| `--role-name` | `-r` | - | No | - | IAM role name to update |
| `--volume-name` | `-v` | - | No | - | Snowflake external volume name |

> At least `--bucket` or both `--role-name` and `--volume-name` must be provided.

---

### `setup-connection`

Test a Snowflake connection and cache it to `manifest.toml`.

```bash
<SKILL_DIR>/vol setup-connection -c <connection_name>
<SKILL_DIR>/vol setup-connection -c local-oauth --admin-role ACCOUNTADMIN
```

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--connection` | `-c` | Yes | - | Snowflake connection name (from `snow connection list`) |
| `--admin-role` | - | No | ACCOUNTADMIN | Admin role to cache in manifest |

Writes `[snowflake].connection`, `account`, `user`, `account_url`, and `admin_role` to `manifest.toml`.

---

### `validate-manifest`

Validate `manifest.toml` structure and report issues.

```bash
<SKILL_DIR>/vol validate-manifest
<SKILL_DIR>/vol validate-manifest --fix
```

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--fix` | - | No | false | Auto-repair structural gaps (adds missing sections with defaults) |

Exit code 0 = valid, 1 = issues found.

---

### `list`

List all external volumes recorded in `manifest.toml`.

```bash
<SKILL_DIR>/vol list
```

Output columns: `LABEL` / `VOLUME_NAME` / `TYPE` / `STATUS`

No options.

---

### `migrate`

Migrate legacy `sfutils-manifest.md` + `.env` to `manifest.toml`.

```bash
<SKILL_DIR>/vol migrate --dry-run
<SKILL_DIR>/vol migrate
```

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--env-path` | - | No | `.env` | `.env` file to read connection info from |
| `--manifest-md` | - | No | `.sfutils/sfutils-manifest.md` | Legacy markdown manifest to read |
| `--dry-run` | - | No | false | Show what would be written without writing |

- Reads `sfutils-manifest.md` as **primary source** (volume_name, bucket_url, status, admin_role)
- Reads `.env` for connection info only (`SNOWFLAKE_DEFAULT_CONNECTION_NAME`, etc.)
- Always sets `infra_ready = false` — run `vol check-setup` afterwards
- Status defaults to `REMOVED` when unresolvable from markdown

---

## Correct Command Structure

```bash
vol [GLOBAL OPTIONS] <command> [COMMAND OPTIONS]

# Examples
vol --region us-east-1 create --bucket iceberg-data --dry-run
vol --no-prefix create --bucket iceberg-data --aws-profile prod
vol delete --bucket iceberg-data --yes
vol verify --volume-name MY_EXTERNAL_VOLUME
vol setup-connection -c local-oauth
vol validate-manifest --fix
vol list
vol migrate --dry-run
```
