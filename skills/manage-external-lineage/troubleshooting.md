# Troubleshooting External Lineage

## Common Errors

### HTTP 401 - Unauthorized
**Cause:** Invalid or expired token

**Fix:**
1. Verify token file exists and contains valid token
2. Check token hasn't expired (PATs have configurable expiry)
3. Regenerate token if needed (see [token_setup.md](token_setup.md))

```bash
# Test token validity
curl -s -H "Authorization: Bearer $(cat /path/to/token.txt)" \
  "https://ACCOUNT.snowflakecomputing.com/api/v2/statements" \
  -d '{"statement": "SELECT 1"}'
```

### HTTP 403 - Forbidden
**Cause:** Missing `INGEST LINEAGE` privilege

**Fix:**
```sql
-- Check current grants
SHOW GRANTS ON ACCOUNT;

-- Grant if missing (requires ACCOUNTADMIN)
GRANT INGEST LINEAGE ON ACCOUNT TO ROLE <your_role>;
```

### HTTP 404 - Not Found
**Cause:** Wrong endpoint URL or account identifier

**Fix:**
1. Verify account identifier format: `ORG-ACCOUNT` (not legacy locator)
2. Check endpoint: `https://<ACCOUNT>.snowflakecomputing.com/api/v2/lineage/external-lineage`
3. Ensure no trailing slash

### HTTP 400 - Bad Request
**Cause:** Malformed JSON payload

**Common issues:**
- Missing required fields (`eventType`, `job`, `run`, `inputs`, `outputs`)
- Invalid `eventType` (must be `COMPLETE`)
- Empty `inputs` or `outputs` array
- Invalid UUID format in `runId`

**Fix:**
```bash
# Validate JSON syntax
cat payload.json | python3 -m json.tool
```

### Lineage Not Appearing in Snowsight
**Causes:**
1. Target Snowflake object doesn't exist
2. Namespace format mismatch
3. Propagation delay (wait 1-2 minutes)

**Fix:**
```sql
-- Verify target object exists
DESCRIBE TABLE DATABASE.SCHEMA.TABLE;

-- Check namespace matches account
SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME();
```

### "External Node" Not Showing
**Cause:** Both inputs AND outputs are Snowflake objects (no external source)

**Fix:** Ensure at least one input OR output is an external namespace (not `snowflake://`)

## Validation Checklist

Before sending a lineage event, verify:

- [ ] Token file exists and is readable
- [ ] `INGEST LINEAGE` privilege granted
- [ ] Target Snowflake object exists
- [ ] `eventType` is `COMPLETE`
- [ ] `runId` is valid UUID format
- [ ] Snowflake namespace uses `snowflake://ORG-ACCOUNT` format
- [ ] At least one non-Snowflake source in inputs or outputs
- [ ] JSON is valid (no trailing commas, proper quoting)

## Debug Mode

Run the send script with verbose output:
```bash
# Add -v flag for verbose curl output
curl -v -X POST ... 
```
