---
name: secondary-roles
description: "Understanding and implementing secondary roles. Use when: users need privileges from multiple roles simultaneously, simplifying end-user experience, avoiding role-switching, dispelling security myths."
---

# Secondary Roles

## Dispelling the Myths

Secondary roles cause trepidation in some users. **This concern is unfounded.**

### "Secondary roles are a security weakness"
**FALSE.** A determined bad actor could accomplish the same outcomes without secondary roles as they could with them. Secondary roles do not grant any privileges the user doesn't already have - they simply allow using multiple granted roles simultaneously rather than switching between them.

### "Secondary roles bypass access control"
**FALSE.** All roles (primary and secondary) must first be **granted to the user** before they can be activated. Secondary roles aggregate privileges the user already has - nothing more.

### "We should disable secondary roles for security"
**COUNTERPRODUCTIVE.** Disabling secondary roles forces workarounds that are harder to audit:
- Creating custom "super roles" per user that aggregate privileges
- Building utilities to stitch together role grants
- Users switching roles mid-execution (harder to trace)

Secondary roles make access patterns **more transparent**, not less.

---

## What Are Secondary Roles

- Every Snowflake session has exactly one **primary role** (the current role in context)
- A set of **secondary roles** can be activated simultaneously
- Effective privileges = **primary role + all active secondary roles**
- Only roles **already granted to the user** can be activated

```
User: analyst_jane
├── Granted: SALES_READER, MARKETING_READER, FINANCE_READER
│
└── Session with secondary roles:
    Primary: SALES_READER
    Secondary: MARKETING_READER, FINANCE_READER
    ─────────────────────────────────────────────
    Effective: All privileges from all three roles
```

---

## Why Secondary Roles Matter

### The Problem They Solve
Users often need privileges from **multiple roles at once**:
- An analyst querying data from Sales, Marketing, AND Finance
- A dashboard pulling from multiple data products
- A report spanning multiple subject areas

### Without Secondary Roles
The workarounds are painful:
1. **One custom role per user** - Grants all needed privileges, but becomes unmanageable at scale
2. **Role switching mid-execution** - User runs `USE ROLE` repeatedly, breaking workflows
3. **Custom utilities** - Building tools to "stitch together" role grants

### With Secondary Roles
- Users work with **all their granted roles active** in a single session
- No need to know which role grants access to which object
- Simplified administration - grant roles normally, let secondary roles handle the rest

---

## When to Use Secondary Roles

### YES - Use For:
| Use Case | Why |
|----------|-----|
| **Analytics/Reporting users** | Access all data products without role switching |
| **Dashboard consumers** | Single session spans multiple subject areas |
| **Read-only access patterns** | Users with multiple READER roles |
| **Cross-domain data consumers** | Query data from multiple domains simultaneously |

### NO - Do Not Use For:
| Use Case | Why |
|----------|-----|
| **Admin/maintenance roles** | Powerful roles should be explicit, not aggregated |
| **Engineering/pipeline roles** | Clear ownership semantics required |
| **Object creation (DDL)** | Ownership tied to primary role only |

---

## How Secondary Roles Work

### User-Level Default (Recommended)
Set once, applies to every session:

```sql
-- Enable all granted roles as secondary by default
ALTER USER my_user SET DEFAULT_SECONDARY_ROLES = ('ALL');

-- Or specific roles only
ALTER USER my_user SET DEFAULT_SECONDARY_ROLES = ('SALES_READER', 'MARKETING_READER');
```

### Session-Level Commands
For ad-hoc activation:

```sql
-- Activate all eligible secondary roles
USE SECONDARY ROLES ALL;

-- Activate specific roles only
USE SECONDARY ROLES SALES_READER, MARKETING_READER;

-- Deactivate all secondary roles
USE SECONDARY ROLES NONE;
```

### Default Behaviour
As of BCR-1692 (2024_08 bundle), new users are created with `DEFAULT_SECONDARY_ROLES = ('ALL')` - secondary roles are active by default.

To change the default for a user:
```sql
-- Set default to no secondary roles
ALTER USER my_user SET DEFAULT_SECONDARY_ROLES = ();

-- Set default to specific roles only
ALTER USER my_user SET DEFAULT_SECONDARY_ROLES = ('SALES_READER', 'MARKETING_READER');
```

**Important**: This only sets the default. Users can still manually run `USE SECONDARY ROLES ALL` in their session. To truly prevent secondary roles, use a **session policy**.

### Enforcing with Session Policies
Session policies (Enterprise Edition) can restrict which secondary roles users can activate in-session:

```sql
-- Disallow all secondary roles
CREATE SESSION POLICY no_secondary_roles
  ALLOWED_SECONDARY_ROLES = ();

-- Allow only specific roles as secondary
CREATE SESSION POLICY limited_secondary_roles
  ALLOWED_SECONDARY_ROLES = ('SALES_READER', 'MARKETING_READER');

-- Block specific roles from being used as secondary (takes precedence over allowed)
CREATE SESSION POLICY block_admin_secondary
  BLOCKED_SECONDARY_ROLES = ('ACCOUNTADMIN', 'SECURITYADMIN', 'SYSADMIN');

-- Apply to account or user
ALTER ACCOUNT SET SESSION POLICY no_secondary_roles;
ALTER USER my_user SET SESSION POLICY limited_secondary_roles;
```

| Parameter | Effect |
|-----------|--------|
| `ALLOWED_SECONDARY_ROLES = ()` | Disallows all secondary roles |
| `ALLOWED_SECONDARY_ROLES = ('ALL')` | Allows all secondary roles (default) |
| `ALLOWED_SECONDARY_ROLES = ('role1', 'role2')` | Only these roles can be secondary |
| `BLOCKED_SECONDARY_ROLES = ('role1')` | These roles cannot be secondary (overrides allowed) |

---

## Ownership and DDL

**Critical distinction**: Secondary roles authorize **DML operations** but **ownership and DDL authority remain tied to the primary role**.

| Operation | Uses Secondary Roles? |
|-----------|----------------------|
| SELECT, INSERT, UPDATE, DELETE | Yes - aggregated privileges |
| CREATE TABLE, CREATE VIEW | **No** - primary role only |
| DROP, ALTER | **No** - primary role owns |
| GRANT, REVOKE | **No** - primary role context |

Objects created in a session are **owned by the primary role**, regardless of which secondary roles are active.

---

## Features That Don't Support Secondary Roles

Features using **owner's rights execution** do NOT use secondary roles:

| Feature | Reason |
|---------|--------|
| Dynamic Tables | Refreshes execute as owner role only |
| Streamlit Apps | Run with owner's rights execution model |
| Materialized Views | Background maintenance runs as owner role |
| Tasks (default) | Run with privileges of task owner role |
| Owner's Rights Stored Procedures | Execute with owner role privileges only |
| UDFs | Execute as owner by default |

### Exception: Tasks with EXECUTE AS USER
Tasks can be configured to run with a specific user's privileges using `EXECUTE AS USER`:
```sql
CREATE TASK my_task
  WAREHOUSE = my_wh
  SCHEDULE = '1 HOUR'
  EXECUTE AS USER service_user
  AS
    SELECT * FROM my_table;
```
When using `EXECUTE AS USER`:
- The task runs on behalf of the specified user
- **The user's DEFAULT_SECONDARY_ROLES are activated automatically**
- The owner role must have `IMPERSONATE` privilege on the user
- The user must be granted the task owner role

This is valuable when:
- Tasks need access across multiple roles
- Data masking/row access policies depend on the querying user
- Clear audit trails attributing activity to specific users are required
- Data Product architecture where a developer with access to many data products wants to combine and publish them without requiring each data provider to grant access to their domain SYSADMIN

### Solution for Other Features: Grant Required Roles to Owner
```sql
-- Owner role inherits privileges from child roles
GRANT ROLE SALES_READER TO ROLE DYNAMIC_TABLE_OWNER;
GRANT ROLE WAREHOUSE_USER TO ROLE DYNAMIC_TABLE_OWNER;
```

### Caller's Rights Procedures
Use caller's rights when secondary role access is needed:
```sql
CREATE PROCEDURE my_proc()
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER  -- Will use caller's primary + secondary roles
AS
$$
  SELECT * FROM sales.data;
$$;
```

---

## API and Connector Support

### Connectors/Drivers
| Connector | Support | How |
|-----------|---------|-----|
| JDBC | ✓ | Execute `USE SECONDARY ROLES` after connection |
| ODBC | ✓ | Execute `USE SECONDARY ROLES` after connection |
| Python | ✓ | Execute `USE SECONDARY ROLES` after connection |
| Snowpark | ✓ | Session-based, supports secondary roles |

**No connector has a direct connection parameter for secondary roles.** Options:
1. Execute `USE SECONDARY ROLES` after connecting
2. Set `DEFAULT_SECONDARY_ROLES` at user level (recommended - applies automatically)
3. For scheduled workloads: Use Tasks with `EXECUTE AS USER` to inherit user's secondary roles

```python
import snowflake.connector

conn = snowflake.connector.connect(...)
conn.cursor().execute("USE SECONDARY ROLES ALL")
```

### REST API
- `X-Snowflake-Role` header sets **primary role only**
- No header exists for secondary roles
- **Workaround**: Execute `USE SECONDARY ROLES` via SQL API endpoint
- **Better**: Set `DEFAULT_SECONDARY_ROLES` at user level

```http
POST /api/v2/statements HTTP/1.1
Content-Type: application/json
Authorization: Bearer <jwt>

{
  "statement": "USE SECONDARY ROLES ALL",
  "warehouse": "MY_WAREHOUSE",
  "role": "MY_PRIMARY_ROLE"
}
```

---

## OAuth Support

| OAuth Type | Secondary Roles | Configuration |
|------------|-----------------|---------------|
| External OAuth (Okta, Entra ID, PingFederate) | **Supported** | Set `EXTERNAL_OAUTH_ANY_ROLE_MODE` |
| Snowflake OAuth | **Not Supported** | N/A |

### External OAuth Configuration
```sql
ALTER SECURITY INTEGRATION my_oauth_integration
SET EXTERNAL_OAUTH_ANY_ROLE_MODE = 'ENABLE';
```

| Value | Behaviour |
|-------|-----------|
| `DISABLE` | Default. Users cannot switch roles |
| `ENABLE` | All users can switch roles |
| `ENABLE_FOR_PRIVILEGE` | Only users/roles with `USE_ANY_ROLE` privilege can switch |

---

## Integration with RBAC Hierarchy

Secondary roles work **with** your role hierarchy, not against it.

### Pattern: READER Roles with Secondary Roles
Each data product has a READER role. Users are granted the READER roles they need, then use secondary roles to access all simultaneously:

```sql
-- User granted multiple READER roles
GRANT ROLE SALES_READER TO USER analyst_jane;
GRANT ROLE MARKETING_READER TO USER analyst_jane;
GRANT ROLE FINANCE_READER TO USER analyst_jane;

-- Enable secondary roles for seamless access
ALTER USER analyst_jane SET DEFAULT_SECONDARY_ROLES = ('ALL');
```

Jane can now query Sales, Marketing, and Finance data in a single session without switching roles.

### Pattern: Domain Consumers
Users outside a domain who need read access bring their own compute and use secondary roles:

```sql
-- Marketing user needs Finance data
GRANT ROLE FINANCE.DB_R TO USER marketing_analyst;

-- Uses their own warehouse + secondary roles
ALTER USER marketing_analyst SET DEFAULT_SECONDARY_ROLES = ('ALL');
```

---

## SQL Templates

### Enable Secondary Roles for All Users (Bulk)
```sql
-- Enable for all users in a role
DECLARE
  c1 CURSOR FOR SELECT user_name FROM SNOWFLAKE.ACCOUNT_USAGE.USERS WHERE deleted_on IS NULL;
BEGIN
  FOR record IN c1 DO
    EXECUTE IMMEDIATE 'ALTER USER ' || record.user_name || ' SET DEFAULT_SECONDARY_ROLES = (''ALL'')';
  END FOR;
END;
```

### Check Current Secondary Roles Status
```sql
-- Current session
SELECT CURRENT_SECONDARY_ROLES();

-- User defaults
SHOW PARAMETERS LIKE 'DEFAULT_SECONDARY_ROLES' IN USER my_user;
```

### Audit Secondary Roles Usage
```sql
-- Who has secondary roles enabled
SELECT user_name, default_secondary_roles
FROM SNOWFLAKE.ACCOUNT_USAGE.USERS
WHERE deleted_on IS NULL
  AND default_secondary_roles IS NOT NULL;
```

---

## Summary

| Myth | Reality |
|------|---------|
| Security weakness | No - aggregates existing grants only |
| Bypasses access control | No - all roles must be granted first |
| Should be disabled | No - makes access patterns MORE transparent |
| Complicates RBAC | No - simplifies end-user experience |

| Do | Don't |
|----|-------|
| Use for analytics/reporting users | Use for admin roles |
| Set `DEFAULT_SECONDARY_ROLES = ('ALL')` | Expect DDL from secondary roles |
| Grant READER roles liberally | Use for object ownership patterns |
| Rely on role hierarchy | Build custom per-user aggregation roles |
