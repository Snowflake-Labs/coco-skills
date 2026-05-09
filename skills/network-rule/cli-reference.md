## CLI Invocation

This skill provides the `nw` wrapper script for simplified CLI access:

```bash
<SKILL_DIR>/nw <command> [options]        # main CLI (sfutils-networks)
<SKILL_DIR>/nw check-setup [options]      # pre-flight infra check
```

> Equivalent to `uv run --project <SKILL_DIR> sfutils-networks ...` and `uv run --project <SKILL_DIR> check-setup ...` respectively.
> The CLI reads the Snowflake connection from `manifest.toml [snowflake].connection` and auto-injects `-c <connection>` for every `snow sql` call.

## Tools

### check-setup (bundled in the sfutils-networks package)

**Description:** Pre-flight check for sfutils infrastructure (database + schemas).

**Usage:**

```bash
<SKILL_DIR>/nw check-setup
```

**Options:**

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--database` | `-d` | No | from `SNOW_UTILS_DB` env or `{USER}_SNOW_UTILS` | Database name to check/create |
| `--run-setup` | - | No | false | Run setup SQL if infrastructure missing |
| `--suggest` | - | No | false | Output suggested defaults as JSON |

### sfutils-networks CLI

**Description:** Creates and manages Snowflake network rules and policies.

**CRITICAL RULES FOR COCO:**

| Rule | Description |
|------|-------------|
| **Always confirm** | NEVER execute create, update, or delete without explicit user confirmation |
| **Show what will happen** | Display SQL preview or summary BEFORE asking for confirmation |
| **One operation at a time** | Don't chain multiple destructive operations |
| **Fail fast** | Check prerequisites before running; stop with clear error if not met |

**Pre-Check Rules (Fail Fast):**

| Command | Pre-Check | If Fails |
|---------|-----------|----------|
| `rule create` | Rule doesn't exist | Stop: "Rule {name} already exists. Use `update` to modify or `delete` first." |
| `rule update` | Rule exists | Stop: "Rule {name} not found. Use `create` instead." |
| `rule delete` | Rule exists | Proceed gracefully (idempotent with IF EXISTS) |

**Command Selection Rules:**

| Scenario | Command | When to Use |
|----------|---------|-------------|
| New rule needed | `rule create` | No existing rule, or chose "Remove and recreate" in Step 3.5 |
| Rule exists, modify IPs | `rule update` | User chose "Update existing" in Step 3.5 |
| Full cleanup | `rule delete` | User explicitly requests cleanup |
| List rules | `rule list` | Verify creation or troubleshoot |

**Confirmation Flow:**

1. **create**: Show SQL preview (Step 4) → Ask "Proceed with creation?" → Execute only on "yes"
2. **update**: Show current vs new IPs → Ask "Update rule with these IPs?" → Execute only on "yes"
3. **delete**: Show resources to be deleted → Ask "Confirm deletion?" → Execute only on "yes"

**Post-Operation Rules:**

| Command | After Success |
|---------|---------------|
| `create` | CLI writes `[rule.<label>]` with `status = "COMPLETE"` to `.sfutils/manifest.toml` |
| `update` | Update manifest with new IP list via `nw rule update` |
| `delete` | CLI writes `status = "REMOVED"` + `removed_at` to `.sfutils/manifest.toml` |

**Command Groups:**

- `rule` - Manage network rules (create, update, list, delete)
- `policy` - Manage network policies (create, alter, list, delete)
- `integration` - Manage External Access Integrations (create, alter, list, delete)

**🔴 COMMAND NAMES (exact -- do NOT substitute):**

- `rule create` -- NOT "rule setup", "rule make", "rule add"
- `rule delete` -- NOT "rule remove", "rule destroy", "rule drop"
- `rule update` -- NOT "rule modify", "rule change", "rule edit"
- `rule list` -- NOT "rule show", "rule get", "rule describe"
- `policy create` -- NOT "policy setup", "policy make"
- `policy delete` -- NOT "policy remove", "policy destroy"
- `policy assign` -- NOT "policy attach", "policy apply", "policy set"

**🔴 OPTION NAMES (NEVER guess or invent options):**

> ONLY use options listed in the CLI Reference tables below.
> If a command fails with "No such option", run `sfutils-networks <subcommand> --help` to see actual available options and use ONLY those.
> NEVER invent, abbreviate, or rename options.

**Global Options (BEFORE subcommand):**

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--verbose` | `-v` | false | Enable verbose output |
| `--debug` | `-d` | false | Enable debug output |
| `--manifest-path` | `-m` | `.sfutils/manifest.toml` | Path to TOML manifest |

### `rule create`

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--name` | `-n` | `NW_RULE_NAME` | Yes | - | Network rule name |
| `--db` | - | `NW_RULE_DB` | Yes | - | Database for rule |
| `--schema` | `-s` | `NW_RULE_SCHEMA` | No | `NETWORKS` | Schema for rule |
| `--mode` | `-m` | - | No | `INGRESS` | Rule mode (see constraints below) |
| `--type` | `-t` | - | No | auto | Rule type (see constraints below) |
| `--values` | - | - | No | - | Comma-separated values (CIDRs, hosts, VPC IDs) |
| `--allow-local/--no-local` | - | - | No | true | Include auto-detected local IP (IPV4 only) |
| `--allow-gh` | `-G` | - | No | false | Add `SNOWFLAKE.NETWORK_SECURITY.GITHUBACTIONS_GLOBAL` (Snowflake-managed, always current) to the policy's `ALLOWED_NETWORK_RULE_LIST`. Requires `--policy`. Not available in gov regions. |
| `--allow-google` | `-g` | - | No | false | Include Google IPs (IPV4 only) |
| `--preset` | - | - | No | - | Intent vocabulary preset for HOST_PORT rules (repeatable). Resolves to HOST:PORT list. Example: `--preset slack --preset aws`. Only active for `--type host_port`. |
| `--dry-run` | - | - | No | false | Preview SQL without executing |
| `--force` | `-f` | - | No | false | Overwrite existing rule (CREATE OR REPLACE) |
| `--policy` | `-p` | - | No | - | Also create/alter a network policy with this name |
| `--policy-mode` | - | - | No | `create` | `create` or `alter` the policy |
| `--output` | `-o` | - | No | `text` | Output format: `text` or `json` |
| `--yes` | `-y` | - | No | false | Skip interactive confirmation (REQUIRED for Cortex Code automation) |

### `rule update`

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--name` | `-n` | `NW_RULE_NAME` | Yes | - | Network rule name |
| `--db` | - | `NW_RULE_DB` | Yes | - | Database name |
| `--schema` | `-s` | `NW_RULE_SCHEMA` | No | `NETWORKS` | Schema name |
| `--values` | - | - | No | - | Comma-separated values to replace existing |
| `--allow-local/--no-local` | - | - | No | true | Include auto-detected local IP (IPV4 only) |
| `--allow-gh` | `-G` | - | No | false | **Not supported — raises an error.** To add `GITHUBACTIONS_GLOBAL` to a policy: `nw policy alter --name <POLICY> --rules <DB.SCHEMA.RULE>` |
| `--allow-google` | `-g` | - | No | false | Include Google IPs (IPV4 only) |
| `--dry-run` | - | - | No | false | Preview SQL without executing |

### `rule delete`

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--name` | `-n` | `NW_RULE_NAME` | Yes | - | Network rule name |
| `--db` | - | `NW_RULE_DB` | Yes | - | Database name |
| `--schema` | `-s` | `NW_RULE_SCHEMA` | No | `NETWORKS` | Schema name |
| `--yes` | - | - | No | - | Auto-confirm deletion |

### `rule list`

| Option | Short | Env Var | Required | Default | Description |
|--------|-------|---------|----------|---------|-------------|
| `--db` | - | `NW_RULE_DB` | Yes | - | Database name |
| `--schema` | `-s` | `NW_RULE_SCHEMA` | No | `NETWORKS` | Schema name |
| `--admin-role` | `-a` | - | No | `accountadmin` | Role for listing |

### `policy create`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--name` | `-n` | Yes | - | Network policy name |
| `--rules` | `-r` | Yes | - | Comma-separated FQN of allowed network rules |
| `--dry-run` | - | No | false | Preview SQL without executing |
| `--force` | `-f` | No | false | Overwrite existing policy (CREATE OR REPLACE) |
| `--output` | `-o` | No | `text` | Output format: `text` or `json` |
| `--yes` | `-y` | No | false | Skip interactive confirmation (REQUIRED for Cortex Code automation) |

### `policy alter`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--name` | `-n` | Yes | - | Network policy name |
| `--rules` | `-r` | Yes | - | Comma-separated FQN of allowed network rules |
| `--dry-run` | - | No | false | Preview SQL without executing |
| `--output` | `-o` | No | `text` | Output format: `text` or `json` |
| `--yes` | `-y` | No | false | Skip interactive confirmation (REQUIRED for Cortex Code automation) |

### `policy delete`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--name` | `-n` | Yes | - | Network policy name |
| `--user` | `-u` | No | - | Also unset policy from this user first |
| `--admin-role` | `-a` | No | `accountadmin` | Role for deleting |
| `--yes` | - | No | - | Auto-confirm deletion |

### `policy list`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--admin-role` | `-a` | No | `accountadmin` | Role for listing |

### `policy assign`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--name` | `-n` | Yes | - | Network policy name |
| `--user` | `-u` | Yes | - | User to assign policy to |
| `--admin-role` | `-a` | No | `accountadmin` | Role for assigning |

### `integration create`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--name` | `-n` | Yes | - | Integration name |
| `--rules` | `-r` | Yes | - | Comma-separated fully qualified EGRESS rule FQNs |
| `--secrets` | - | No | - | Comma-separated secret FQNs (optional) |
| `--dry-run` | - | No | false | Preview SQL without executing |
| `--force` | `-f` | No | false | CREATE OR REPLACE |
| `--admin-role` | `-a` | No | from manifest | Admin role |
| `--yes` | `-y` | No | false | Skip confirmation |

### `integration alter`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--name` | `-n` | Yes | - | Integration name |
| `--add-rules` | - | Yes | - | Comma-separated FQN rule names to add |
| `--dry-run` | - | No | false | Preview SQL without executing |
| `--admin-role` | `-a` | No | from manifest | Admin role |
| `--yes` | `-y` | No | false | Skip confirmation |

### `integration list`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--output` | `-o` | No | `text` | Output format: `text` or `json` |
| `--admin-role` | `-a` | No | `accountadmin` | Admin role |

### `integration delete`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--name` | `-n` | Yes | - | Integration name |
| `--admin-role` | `-a` | No | from manifest | Admin role |
| `--yes` | `-y` | No | false | Skip confirmation |

### `list` (top-level)

Lists all rule entries from `manifest.toml` in a tabular format (no options required).

```bash
<SKILL_DIR>/nw list
```

Output columns: `LABEL` / `RULE_NAME` / `MODE` / `TYPE` / `STATUS`

### `validate-manifest`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--fix` | - | No | false | Fill in missing sections with defaults before validating |

```bash
<SKILL_DIR>/nw validate-manifest
<SKILL_DIR>/nw validate-manifest --fix
```

### `setup-connection`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--connection` | `-c` | Yes | - | Snowflake connection name (from `snow connection list`) |
| `--admin-role` | - | No | `ACCOUNTADMIN` | Admin role to cache in manifest |

```bash
<SKILL_DIR>/nw setup-connection -c local-oauth
```

### `migrate`

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--env-path` | - | No | `.env` | `.env` file for connection name (supplementary only) |
| `--manifest-md` | - | No | `.sfutils/sfutils-manifest.md` | Legacy markdown manifest (primary source) |
| `--dry-run` | - | No | false | Print what would be written without writing |

```bash
<SKILL_DIR>/nw migrate --dry-run
<SKILL_DIR>/nw migrate
```

**⚠️ Mode-Type Constraints:**

| Mode | Valid Types |
|------|-------------|
| INGRESS, INTERNAL_STAGE, POSTGRES_INGRESS | IPV4, AWSVPCEID |
| EGRESS, POSTGRES_EGRESS | HOST_PORT, IPV4 |

> IP source flags (`--allow-local`, `--allow-gh`, `--allow-google`) only work with `--type IPV4`

**⚠️ IPv4-Only Note for GitHub Actions:**

`--allow-gh` fetches IP ranges from `https://api.github.com/meta` at creation time and inlines them into the rule's `VALUE_LIST`. GitHub publishes both IPv4 and IPv6 ranges; only IPv4 is included (Snowflake `TYPE = IPV4` rules do not support IPv6). These CIDRs are a **snapshot** — re-run `rule update --allow-gh` periodically to refresh them as GitHub's runner ranges change.

> **Want auto-updating ranges?** Use the `network-security` skill's hybrid policy approach, which references `SNOWFLAKE.NETWORK_SECURITY.GITHUBACTIONS_GLOBAL` — a Snowflake-managed SaaS rule that is kept current automatically. No role access to `SNOWFLAKE.NETWORK_SECURITY` is required; it is sufficient to add the FQN to the network policy's `ALLOWED_NETWORK_RULE_LIST`.

