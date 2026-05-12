# External Volume Prerequisites

## Migration Detection

Before running prerequisites, check which state the project is in:

```bash
ls .sfutils/sfutils-manifest.md 2>/dev/null && echo "LEGACY" || true
ls .sfutils/manifest.toml 2>/dev/null && echo "TOML" || true
```

- **Legacy manifest found (`sfutils-manifest.md`):** run `vol migrate` first, then check-setup
- **TOML manifest found (`manifest.toml`):** run Volume Manifest Gate, then proceed
- **Neither found:** initialize manifest below

---

## Initialize manifest.toml (New Project)

```bash
mkdir -p .sfutils && chmod 700 .sfutils
```

Create a skeleton manifest (the CLI will fill it in via `vol setup-connection`):

```toml
# .sfutils/manifest.toml
schema_version = "1"
project_name   = "<basename of project directory>"
created_at     = "<ISO timestamp — set by CLI>"

[snowflake]
connection   = ""
account      = ""
user         = ""
account_url  = ""
admin_role   = "ACCOUNTADMIN"

[prereqs]
tools_verified = ""
infra_ready    = false

[volume]
# populated automatically by vol create
```

```bash
chmod 600 .sfutils/manifest.toml
```

Then run `vol setup-connection -c <connection>` to fill in the `[snowflake]` section.

---

## Tool Verification

```bash
<SKILL_DIR>/vol check-setup --provider s3
```

This checks:
- `snow` CLI is on PATH
- `aws` CLI is on PATH (S3 provider)
- AWS credential env signal (profile, static keys, web identity)

On success, writes `[prereqs].infra_ready = true` to `manifest.toml`.

**For other providers (future):**
```bash
<SKILL_DIR>/vol check-setup --provider azure   # checks az CLI
<SKILL_DIR>/vol check-setup --provider gcs     # checks gcloud CLI
```

**Install missing tools:**

| Tool | Install Command |
|------|-----------------|
| `uv` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `snow` | `pip install snowflake-cli` or `uv tool install snowflake-cli` |
| `aws` | `pip install awscli` or via your package manager |

---

## Required Snowflake Privilege

External volumes are **account-level objects**. No database or schema is needed.

Creation requires `CREATE EXTERNAL VOLUME` privilege, held by `ACCOUNTADMIN` by default.

```bash
<SKILL_DIR>/vol setup-connection -c <connection> --admin-role ACCOUNTADMIN
```

This caches the admin role in `manifest.toml [snowflake].admin_role`.

---

## Verify Prerequisites Are Met

```bash
<SKILL_DIR>/vol validate-manifest
```

Expected output:
```
✓ manifest.toml is valid  (connection: local-oauth, volumes: 0)
```

If validation fails with `infra_ready = false`, run `vol check-setup` to resolve.
