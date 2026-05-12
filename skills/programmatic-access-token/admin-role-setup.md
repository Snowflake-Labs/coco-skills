### Step 2a: Admin Role from Manifest

**Purpose:** PAT skill requires elevated privileges for account-level objects. Get admin_role from manifest.

> **📍 MANIFEST FILE:** `.sfutils/manifest.toml` - always use this exact path, never search for other patterns

**Required privileges for PAT skill:**

| Privilege | Scope | Required For | Default Role |
|-----------|-------|--------------|--------------|
| CREATE USER | Account | Creating service user | USERADMIN+ |
| CREATE ROLE | Account | Creating SA_ROLE | USERADMIN+ |
| MANAGE GRANTS | Account | Granting role to user | SECURITYADMIN+ |
| CREATE AUTHENTICATION POLICY | Schema | Creating auth policy | Schema owner |

> **Note:** Only ACCOUNTADMIN has ALL these privileges by default.

**Ensure secured .sfutils directory:**

```bash
mkdir -p .sfutils && chmod 700 .sfutils
```

**Check manifest for existing admin_role:**

```bash
cat .sfutils/manifest.toml 2>/dev/null | grep "admin_role"
```

**If admin_role exists in `[snowflake]` or in a `[pat.*]` entry:** Use it, continue to Step 2b (privilege verification).

**If admin_role NOT set, check other skills' manifests:**

```bash
cat .sfutils/manifest.toml 2>/dev/null | grep -A10 "\[snowflake\]" | grep "admin_role"
```

**If another skill has admin_role, ask user:**

```
Found admin_role from another skill:
  sfutils-extvolumes: ACCOUNTADMIN
  
PAT creation requires CREATE USER + CREATE ROLE + MANAGE GRANTS + CREATE AUTHENTICATION POLICY.

Options:
1. Use existing: ACCOUNTADMIN
2. Use Snowflake default: ACCOUNTADMIN
3. Specify a different role
```

**If NO admin_role exists anywhere, prompt:**

```
PAT creation requires these privileges:
  - CREATE USER (Account)
  - CREATE ROLE (Account)
  - MANAGE GRANTS (Account)
  - CREATE AUTHENTICATION POLICY (Schema)

Snowflake recommends: ACCOUNTADMIN (has all privileges by default)

Enter admin role for PAT [ACCOUNTADMIN]: 
```

**⚠️ STOP**: Wait for user input.

**IMMEDIATELY write to manifest (before ANY resource creation):**

Use the Edit tool to update `[snowflake].admin_role` in `.sfutils/manifest.toml`:

```toml
[snowflake]
admin_role = "<USER_ROLE>"
```

Or run `<SKILL_DIR>/pat setup-connection -c <connection> --admin-role <USER_ROLE>` to write it.

```bash
chmod 600 .sfutils/manifest.toml
```

**Update memory:**

```
Update /memories/sfutils-prereqs.md:
pat_admin_role: <USER_ROLE>
```

Continue to Step 2b.

### Step 2b: Verify Admin Role Privileges

**If admin_role is ACCOUNTADMIN:** Skip verification, continue to Step 3.

**If admin_role is a custom role**, verify it has required privileges:

```bash
set -a && source .env && set +a && snow sql --role <ADMIN_ROLE> -q "
SHOW GRANTS TO ROLE <ADMIN_ROLE>;
" --format json
```

**Check for these grants in the output:**

| Look For | Privilege | On |
|----------|-----------|-----|
| CREATE USER | `CREATE USER` | ACCOUNT |
| CREATE ROLE | `CREATE ROLE` | ACCOUNT |
| MANAGE GRANTS | `MANAGE GRANTS` | ACCOUNT |
| CREATE AUTHENTICATION POLICY | `CREATE AUTHENTICATION POLICY` | SCHEMA (SFUTILS_DB.POLICIES) |

**If any privilege is missing**, use `ask_user_question` with options:

| Option | Action |
|--------|--------|
| Grant missing privileges | Show GRANT statements for user to execute with elevated role |
| Use a different role | Go back to Step 2a to select different role |
| Cancel | Stop workflow |

**If user chooses "Grant missing privileges":**

Show SQL for each missing privilege:

```sql
-- Run as ACCOUNTADMIN or SECURITYADMIN
GRANT CREATE USER ON ACCOUNT TO ROLE <ADMIN_ROLE>;
GRANT CREATE ROLE ON ACCOUNT TO ROLE <ADMIN_ROLE>;
GRANT MANAGE GRANTS ON ACCOUNT TO ROLE <ADMIN_ROLE>;
GRANT CREATE AUTHENTICATION POLICY ON SCHEMA <SF_UTILS_DB>.POLICIES TO ROLE <ADMIN_ROLE>;
```

**STOP**: Wait for user to confirm privileges have been granted, then re-verify.

Continue to Step 3.
