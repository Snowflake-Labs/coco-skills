## Provider Comparison

| Provider | STORAGE_PROVIDER | Auth Mechanism | CLI Tool | Status |
|----------|------------------|----------------|----------|--------|
| AWS S3 | `S3` | IAM role + external ID | `aws` | Supported |
| Azure Blob | `AZURE` | Service principal + tenant ID | `az` | Planned (not enabled in skill or CLI) |
| GCS | `GCS` | Service account | `gcloud` | Planned (not enabled in skill or CLI) |

## AWS credential environment (reference)

`check-setup --suggest` includes `csp_credential_env` (each watched name with `set: true/false` only — **never values**), `csp_credential_env_signal`, and `csp_credential_env_satisfied_by`.

**OR satisfaction:** The signal is true if **any one** of these holds: static keys (`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`), profile (`AWS_PROFILE` or `AWS_DEFAULT_PROFILE`), or web identity (`AWS_WEB_IDENTITY_TOKEN_FILE`). Region vars (`AWS_REGION`, `AWS_DEFAULT_REGION`) are listed for diagnostics only and are **not** required for the credential signal.

If the signal is false, `credential_env_note` explains that boto3 may still use shared config files, SSO, or an instance role — not a hard failure.

Full credential resolution order and options: [Boto3 credentials](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html).

## SQL Reference (Snowflake Documentation)

> These links help Cortex Code infer correct SQL syntax when previewing or troubleshooting.

| Statement | Documentation |
|-----------|---------------|
| `CREATE EXTERNAL VOLUME` | https://docs.snowflake.com/en/sql-reference/sql/create-external-volume |
| `ALTER EXTERNAL VOLUME` | https://docs.snowflake.com/en/sql-reference/sql/alter-external-volume |
| `DROP EXTERNAL VOLUME` | https://docs.snowflake.com/en/sql-reference/sql/drop-external-volume |
| `DESC EXTERNAL VOLUME` | https://docs.snowflake.com/en/sql-reference/sql/desc-external-volume |
| `SHOW EXTERNAL VOLUMES` | https://docs.snowflake.com/en/sql-reference/sql/show-external-volumes |
| `CREATE ICEBERG TABLE` (Snowflake catalog) | https://docs.snowflake.com/en/sql-reference/sql/create-iceberg-table-snowflake |

## Troubleshooting

**Connection not found:** Ensure `[snowflake].connection` in `.sfutils/manifest.toml` matches a configured connection. Run `snow connection list` to see available connections, then run `vol setup-connection -c <name>` to update the manifest.

**Infrastructure not set up:** Run `<SKILL_DIR>/vol check-setup --run-setup` - it will check and offer to create the database and schemas.

**IAM propagation delay:** Script uses exponential backoff. Run `verify` after a minute if needed.

**S3 403 error:** Bucket name exists in another account. Choose different name.

**Trust policy mismatch:** Run `update-trust` to re-sync.

## Privilege Escalation Hints (for Apps)

**Background:** Apps using sfutils resources should operate with restricted SA_ROLE. However, some operations genuinely require elevated privileges.

**If an app needs elevated access, suggest these patterns:**

| App Need | Recommended Approach |
|----------|---------------------|
| Create Iceberg tables | Grant CREATE ICEBERG TABLE on schema to SA_ROLE |
| Modify external volume | Use admin_role from manifest for one-time setup |
| Grant USAGE to other roles | Stored procedure with CALLER rights + admin_role |

**Example: Stored procedure for controlled escalation:**

```sql
-- Created once by admin, used by SA_ROLE
CREATE OR REPLACE PROCEDURE grant_iceberg_usage(target_role STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
BEGIN
    -- SA_ROLE calls this, but it runs as admin_role
    GRANT USAGE ON EXTERNAL VOLUME <VOLUME_NAME> TO ROLE IDENTIFIER(:target_role);
    RETURN 'Granted USAGE to ' || :target_role;
END;
```

**IMPORTANT:** Never suggest putting admin_role in app .env. Always delegate to manifest.
