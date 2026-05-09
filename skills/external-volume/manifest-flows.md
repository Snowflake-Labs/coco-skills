## TOML Manifest Flow (manifest.toml — current)

### Progressive Volume Write

After successful `vol create`, the CLI automatically writes to `.sfutils/manifest.toml`.
The entry looks like this:

```toml
# Machine-managed by Cortex Code. Do not hand-edit.
schema_version = "1"
project_name   = "my-demo"
created_at     = "2026-05-02T10:00:00Z"

# ── Shared Snowflake connection (captured once, reused by all volumes) ────────
[snowflake]
connection   = "local-oauth"
account      = "ABC12345"
user         = "KAMESHS"
account_url  = "https://abc12345.snowflakecomputing.com"
admin_role   = "ACCOUNTADMIN"

# ── Tool / infra pre-flight cache ─────────────────────────────────────────────
[prereqs]
tools_verified = "2026-05-02"
infra_ready    = true

# ── Volume: my-s3-volume ──────────────────────────────────────────────────────
[volume.my-s3-volume]
status               = "COMPLETE"
created_at           = "2026-05-02T10:15:00Z"
updated_at           = "2026-05-02T10:15:00Z"
volume_name          = "MY_S3_VOLUME"
storage_type         = "s3"
bucket_url           = "s3://my-bucket/prefix"
aws_region           = "us-east-1"
aws_profile          = "default"
storage_aws_role_arn = "arn:aws:iam::123456789012:role/MySnowflakeRole"
external_id          = "abc123xyz"
admin_role           = "ACCOUNTADMIN"

[volume.my-s3-volume.cleanup]
volume_name = "MY_S3_VOLUME"
```

**TOML key derivation:** `volume_name.lower().replace("_", "-")`
Example: `MY_S3_VOLUME` → `my-s3-volume`

**No `db` field in `[cleanup]`** — external volumes are account-level objects; cleanup is
S3 bucket + IAM role/policy + `DROP EXTERNAL VOLUME` only.

---

### Multiple Volumes

Each additional volume gets its own `[volume.<label>]` section:

```toml
[volume.my-s3-volume]
...

[volume.my-second-volume]
status      = "COMPLETE"
volume_name = "MY_SECOND_VOLUME"
storage_type = "s3"
...
```

Use `vol list` to see all volumes at a glance.

---

### Remove Flow (TOML)

**Always run Volume Manifest Gate before Remove.**

```bash
<SKILL_DIR>/vol validate-manifest     # gate check
<SKILL_DIR>/vol list                  # confirm the volume name/label
<SKILL_DIR>/vol delete --bucket <BUCKET> --yes --output json
```

After delete the CLI marks the entry as removed in manifest.toml:

```toml
[volume.my-s3-volume]
status     = "REMOVED"
removed_at = "2026-05-02T11:00:00Z"
updated_at = "2026-05-02T11:00:00Z"
...
```

The entry is preserved for audit purposes — never deleted from the manifest.

---

### Validate Manifest

```bash
<SKILL_DIR>/vol validate-manifest          # check structure
<SKILL_DIR>/vol validate-manifest --fix    # repair structural gaps
```

---

## Legacy Markdown Manifest Flow (sfutils-manifest.md — deprecated)

> **⚠️ DEPRECATED** — The `.sfutils/sfutils-manifest.md` format is no longer used for
> new projects. Run `vol migrate` to convert to `manifest.toml`.
>
> Keep this section for users who haven't migrated yet.

### Step 8: Write Success Summary and Cleanup Manifest

**Manifest Location:** `.sfutils/sfutils-manifest.md`

**Create directory if needed:**

```bash
mkdir -p .sfutils && chmod 700 .sfutils
```

**If manifest doesn't exist, create with header:**

```markdown
# SF Utils Manifest

This manifest records all Snowflake resources created by sfutils skills.

---
```

**Append skill section with START/END markers:**

```markdown
<!-- START -- sfutils-extvolumes -->
## External Volume Resources: {COMMENT_PREFIX}

**Created:** {TIMESTAMP}
**Provider:** {STORAGE_PROVIDER}
**Prefix:** {PREFIX}
**Bucket:** {BUCKET}
**Region:** {AWS_REGION}
**Status:** COMPLETE

### AWS Tags (applied to S3, IAM Role, IAM Policy)
| Tag Key | Value |
|---------|-------|
| managed-by | sfutils-extvolumes |
| user | {PREFIX_UPPER} |
| project | {BUCKET_UPPER} |
| snowflake-volume | {PREFIX}_{BUCKET}_EXTERNAL_VOLUME |

### Resources
| # | Type | Name | Location | Status |
|---|------|------|----------|--------|
| 1 | S3 Bucket | {PREFIX}-{BUCKET} | AWS ({AWS_REGION}) | DONE |
| 2 | IAM Policy | {PREFIX}-{BUCKET}-snowflake-policy | AWS | DONE |
| 3 | IAM Role | {PREFIX}-{BUCKET}-snowflake-role | AWS | DONE |
| 4 | External Volume | {PREFIX}_{BUCKET}_EXTERNAL_VOLUME | Snowflake | DONE |

### Cleanup

Run this command to remove all resources:

\```bash
<SKILL_DIR>/vol \
  --region ${AWS_REGION} \
  delete --bucket ${BUCKET} --yes --output json
\```

With S3 bucket deletion:

\```bash
<SKILL_DIR>/vol \
  --region ${AWS_REGION} \
  delete --bucket ${BUCKET} --delete-bucket --force --yes --output json
\```
<!-- END -- sfutils-extvolumes -->
```

**Secure manifest file:**

```bash
chmod 600 .sfutils/sfutils-manifest.md
```

**Display success summary to user:**

```
✅ External Volume Setup Complete!

Resources Created:
  S3 Bucket:        {PREFIX}-{BUCKET} ({AWS_REGION})
  IAM Role:         {PREFIX}-{BUCKET}-snowflake-role
  IAM Policy:       {PREFIX}-{BUCKET}-snowflake-policy
  External Volume:  {PREFIX}_{BUCKET}_EXTERNAL_VOLUME

AWS Tags Applied:
  managed-by:       sfutils-extvolumes
  user:             {PREFIX}
  project:          {BUCKET}
  snowflake-volume: {VOLUME_NAME}

Verification:
  Status:           ✅ PASSED
  Storage Access:   Confirmed
  IAM Trust:        Valid

Applications:
  - Iceberg tables (managed data lake)
  - COPY INTO unload (data export)
  - External stages (data import)

Manifest: .sfutils/manifest.toml
```

**Example Iceberg Table DDL:**

```sql
CREATE OR REPLACE ICEBERG TABLE my_table (
    id INT,
    name STRING,
    created_at TIMESTAMP
)
    CATALOG = 'SNOWFLAKE'
    EXTERNAL_VOLUME = 'MY_S3_VOLUME'
    BASE_LOCATION = 'my_table/';
```
