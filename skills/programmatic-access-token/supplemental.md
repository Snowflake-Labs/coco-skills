## Stopping Points

1. Step 0: **Manifest Gate** fails after `--fix` (non-repairable issues — stop before any operation)
2. Step 1: If connection picker needed (no connection in manifest) — wait for selection
3. Step 2: If infra check needed (prompts user)
4. Step 3: After gathering requirements
5. Step 3a: If PAT exists (ask rotate/recreate/cancel)
6. Step 4: After dry-run preview (get approval)

## Output

- Service user (TYPE = SERVICE)
- Network rule with local IP
- Network policy (ENFORCED_REQUIRED)
- Authentication policy (PROGRAMMATIC_ACCESS_TOKEN only)
- PAT stored in OS keyring (never written to disk)
- manifest.toml updated with PAT entry (`[pat.<label>]`)
- Cleanup manifest (`.sfutils/manifest.toml`)

## SQL Reference (Snowflake Documentation)

> These links help Cortex Code infer correct SQL syntax when previewing or troubleshooting.

| Statement | Documentation |
|-----------|---------------|
| `CREATE USER` | https://docs.snowflake.com/en/sql-reference/sql/create-user |
| `DROP USER` | https://docs.snowflake.com/en/sql-reference/sql/drop-user |
| `CREATE ROLE` | https://docs.snowflake.com/en/sql-reference/sql/create-role |
| `GRANT ROLE` | https://docs.snowflake.com/en/sql-reference/sql/grant-role |
| `CREATE NETWORK RULE` | https://docs.snowflake.com/en/sql-reference/sql/create-network-rule |
| `DROP NETWORK RULE` | https://docs.snowflake.com/en/sql-reference/sql/drop-network-rule |
| `CREATE NETWORK POLICY` | https://docs.snowflake.com/en/sql-reference/sql/create-network-policy |
| `DROP NETWORK POLICY` | https://docs.snowflake.com/en/sql-reference/sql/drop-network-policy |
| `CREATE AUTHENTICATION POLICY` | https://docs.snowflake.com/en/sql-reference/sql/create-authentication-policy |
| `ALTER USER ... SET AUTHENTICATION POLICY` | https://docs.snowflake.com/en/sql-reference/sql/alter-user |
| `ALTER USER ... UNSET AUTHENTICATION POLICY` | https://docs.snowflake.com/en/sql-reference/sql/alter-user |
| `ALTER USER ... UNSET NETWORK_POLICY` | https://docs.snowflake.com/en/sql-reference/sql/alter-user |
| `ALTER USER ... ADD PROGRAMMATIC ACCESS TOKEN` | https://docs.snowflake.com/en/sql-reference/sql/alter-user |
| `ALTER USER ... REMOVE PROGRAMMATIC ACCESS TOKEN` | https://docs.snowflake.com/en/sql-reference/sql/alter-user |
| `GRANT ... ON SCHEMA` | https://docs.snowflake.com/en/sql-reference/sql/grant-privilege |
| `MANAGE GRANTS` | https://docs.snowflake.com/en/sql-reference/sql/grant-privilege |

## Troubleshooting

**Infrastructure not set up:** Run `<SKILL_DIR>/pat check-setup --run-setup` - it will check and offer to create the database and schemas.

**Network policy blocking:** Ensure your IP is in the network rule. Use --local-ip to specify.

**PAT already exists:** Use --rotate to replace existing PAT.

**Connection verification failed:** Check network policy allows your IP.

**Cannot drop database (policy attached):** Run `sfutils-pat remove --drop-user` - it handles dependency order automatically. **NEVER run raw SQL for cleanup.**

**Cannot drop network rule (associated with policies):** Run `sfutils-pat remove` - it detaches rules from policies before dropping. **NEVER run raw SQL for cleanup.**

## Security Notes

- PAT tokens stored in OS keyring only (never written to disk or .env)
- PAT tokens NEVER displayed in diffs or logs (masked as ***REDACTED***)
- Network policy restricts access to specified IPs only
- Auth policy enforces PAT-only authentication
- Tokens have configurable expiry (default 30 days)

## Security Checklist

After PAT creation, verify:

- [ ] PAT active and keyring valid: run `<SKILL_DIR>/pat verify --user {SA_USER} --role {SA_ROLE}`
- [ ] No temp files in `/tmp/pat_*`
- [ ] `.sfutils/` is `chmod 700`, manifest is `chmod 600`
- [ ] `.gitignore` excludes `.sfutils/`
- [ ] Network policy restricts to intended IPs
- [ ] Auth policy enforces PAT-only authentication
- [ ] Expiry appropriate for use case (max 15 active PATs per user)
