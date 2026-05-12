# Prerequisites

> **Migration detection:** If `.sfutils/sfutils-manifest.md` exists but `.sfutils/manifest.toml` does not, run `nw migrate` before any other operation. See Step 0 in SKILL.md.

## Init: Create manifest.toml skeleton

Run once per project before any other operation (or let `nw setup-connection` create it automatically):

```bash
mkdir -p .sfutils && chmod 700 .sfutils
<SKILL_DIR>/nw validate-manifest --fix
chmod 600 .sfutils/manifest.toml
```

This creates a skeleton `manifest.toml`:

```toml
# Machine-managed by Cortex Code. Do not hand-edit.
schema_version = "1"
project_name   = "<derived from cwd>"
created_at     = "<now>"

# ── Shared Snowflake connection (captured once, reused by all rules) ──────────
[snowflake]
connection   = ""
account      = ""
user         = ""
account_url  = ""
sf_utils_db  = ""
admin_role   = "ACCOUNTADMIN"

# ── Tool / infra pre-flight cache ─────────────────────────────────────────────
[prereqs]
tools_verified = "<today>"
infra_ready    = false
```

Then set the connection:

```bash
<SKILL_DIR>/nw setup-connection -c <your_connection>
```

## Required tools

| Tool | Min Version | Install |
|------|-------------|---------|
| `uv` | any | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `snow` (Snowflake CLI) | ≥ 3.16.0 | `pip install 'snowflake-cli>=3.16.0'` |

Verify with:

```bash
<SKILL_DIR>/nw check-setup --suggest
```

## Snowflake privileges required

| Privilege | Scope | Default Role |
|-----------|-------|--------------|
| `CREATE NETWORK RULE` | Schema (`SF_UTILS_DB.NETWORKS`) | ACCOUNTADMIN |
| `CREATE NETWORK POLICY` | Account | ACCOUNTADMIN / SECURITYADMIN |
| `USAGE` | Database (`SF_UTILS_DB`) | DB owner / ACCOUNTADMIN |

> Only ACCOUNTADMIN has all privileges by default. SECURITYADMIN lacks database USAGE.
