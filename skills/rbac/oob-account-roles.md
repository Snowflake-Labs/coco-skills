---
name: oob-account-roles
description: "Guide to Snowflake's out-of-box account roles and account-level access roles. Use when: setting up a new account, understanding ACCOUNTADMIN/SYSADMIN/SECURITYADMIN/USERADMIN, creating account-level access roles for delegated privileges."
---

# Out-of-Box Account Roles

How to use Snowflake's built-in roles correctly, plus account-level access roles that should exist in every account.

## The OOB Roles

Snowflake provides these roles out of the box. **Do not alter their privileges.**

| Role | Purpose | Use For |
|------|---------|---------|
| **ACCOUNTADMIN** | Superuser | Account-level settings, granting account privileges to access roles. **Never own objects with this role.** |
| **SYSADMIN** | Object administration | Creating databases, warehouses. Transfer ownership to delegated admins after creation. |
| **SECURITYADMIN** | Security administration | Granting privileges to roles, managing role hierarchy. |
| **USERADMIN** | User/role administration | Creating users and custom roles. |
| **PUBLIC** | Default role for all users | Minimal privileges, baseline for all sessions. |

## First Principles

1. **Never alter OOB role privileges** - Don't add or remove grants from ACCOUNTADMIN, SYSADMIN, etc.

2. **ACCOUNTADMIN should never own objects** - Use SYSADMIN or a custom role to create/own account objects.

3. **Don't grant privileges directly to functional roles** - Wrap privileges in access roles first, then grant those to functional roles.

4. **Use USERADMIN to create all custom roles** - Keeps role administration separate from object administration.

5. **Don't wrap OOB roles with custom roles of similar names** - Don't create SCIM_ACCOUNTADMIN or CUSTOM_SYSADMIN. If you need to assign OOB roles via SCIM, grant them directly to users via script.

## Account-Level Access Roles

These access roles wrap account-level privileges that would otherwise require ACCOUNTADMIN. Create these in every account regardless of architecture pattern.

### Why Access Roles?

ACCOUNTADMIN has powerful privileges. Rather than granting ACCOUNTADMIN to delegated admins, wrap specific privileges in access roles and grant those instead.

```
ACCOUNTADMIN privilege
    ↓ granted to
Access Role (_AR_EXEC_TASK)
    ↓ granted to
Functional Role (SALES_CREATE)
```

### Standard Access Roles

| Access Role | Privilege | Use Case |
|-------------|-----------|----------|
| `_AR_EXEC_TASK` | EXECUTE TASK ON ACCOUNT | Roles that own/run tasks |
| `_AR_VIEW_AUSG` | IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE | Roles that query Account Usage |
| `_AR_APPLY_TAG` | APPLY TAG ON ACCOUNT | Roles that apply tags to objects |
| `_AR_APPLY_DDM` | APPLY MASKING POLICY ON ACCOUNT | Roles that apply dynamic data masking |
| `_AR_APPLY_RAP` | APPLY ROW ACCESS POLICY ON ACCOUNT | Roles that apply row access policies |

The `_AR_` prefix and leading underscore:
- `_` prefix sorts these to the bottom of role lists
- `AR` indicates "Access Role"
- Distinguishes from functional roles that are granted to users

---

## SQL Template

### Create Access Roles
```sql
SET arPrefix = '_AR_';
SET viewAusgAr = $arPrefix || 'VIEW_AUSG';
SET execTaskAr = $arPrefix || 'EXEC_TASK';
SET applyTagAr = $arPrefix || 'APPLY_TAG';
SET applyDdmAr = $arPrefix || 'APPLY_DDM';
SET applyRapAr = $arPrefix || 'APPLY_RAP';

USE ROLE USERADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($viewAusgAr);
CREATE ROLE IF NOT EXISTS IDENTIFIER($execTaskAr);
CREATE ROLE IF NOT EXISTS IDENTIFIER($applyTagAr);
CREATE ROLE IF NOT EXISTS IDENTIFIER($applyDdmAr);
CREATE ROLE IF NOT EXISTS IDENTIFIER($applyRapAr);
```

### Grant Privileges to Access Roles
```sql
USE ROLE ACCOUNTADMIN;

GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE IDENTIFIER($viewAusgAr);
GRANT EXECUTE TASK ON ACCOUNT TO ROLE IDENTIFIER($execTaskAr);
GRANT APPLY TAG ON ACCOUNT TO ROLE IDENTIFIER($applyTagAr);
GRANT APPLY MASKING POLICY ON ACCOUNT TO ROLE IDENTIFIER($applyDdmAr);
GRANT APPLY ROW ACCESS POLICY ON ACCOUNT TO ROLE IDENTIFIER($applyRapAr);
```

### Grant Access Roles to Functional Roles
```sql
USE ROLE SECURITYADMIN;

GRANT ROLE IDENTIFIER($execTaskAr) TO ROLE <FUNCTIONAL_ROLE_THAT_RUNS_TASKS>;
GRANT ROLE IDENTIFIER($viewAusgAr) TO ROLE <FUNCTIONAL_ROLE_THAT_MONITORS>;
GRANT ROLE IDENTIFIER($applyTagAr) TO ROLE <FUNCTIONAL_ROLE_THAT_GOVERNS>;
GRANT ROLE IDENTIFIER($applyDdmAr) TO ROLE <FUNCTIONAL_ROLE_THAT_GOVERNS>;
GRANT ROLE IDENTIFIER($applyRapAr) TO ROLE <FUNCTIONAL_ROLE_THAT_GOVERNS>;
```

---

## Granting OOB Roles to Users

If using SCIM, don't create wrapper roles for OOB roles. Instead, use a versioned script:

```sql
USE ROLE ACCOUNTADMIN;
GRANT ROLE ACCOUNTADMIN TO USER <admin_user>;
GRANT ROLE SYSADMIN TO USER <admin_user>;

USE ROLE SECURITYADMIN;
GRANT ROLE SECURITYADMIN TO USER <security_user>;
GRANT ROLE USERADMIN TO USER <security_user>;
```

---

## Ownership Model

| Object | Created By | Owned By |
|--------|------------|----------|
| Access Roles | USERADMIN | USERADMIN |
| Databases | SYSADMIN | Delegated Admin (after transfer) |
| Warehouses | SYSADMIN | Delegated Admin (after transfer) |
| Custom Functional Roles | USERADMIN | USERADMIN (or SCIM provisioner) |

## Key Points

- **OOB roles are immutable** - use them as-is, don't modify their privileges
- **Access roles wrap privileges** - never grant account privileges directly to functional roles
- **SYSADMIN creates, then transfers** - databases and warehouses created by SYSADMIN, ownership transferred to delegated admin
- **USERADMIN owns custom roles** - unless you have a SCIM provisioner role
