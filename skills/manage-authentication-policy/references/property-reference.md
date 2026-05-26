# Authentication Policy Property Reference

Detailed syntax and valid values for the 8 policy properties, plus the canonical SQL queries the agent is allowed to use for any discovery / metadata operation in this skill.

### Strict rules (read first)

- **⚠️ Strict VALUE rule** — for *property values* (e.g. `AUTHENTICATION_METHODS`, `CLIENT_TYPES`, `MFA_ENROLLMENT`). Only use values listed below in **Compatibility Rules** OR confirmed in the official Snowflake documentation linked below. Never infer, guess, or add other values: if a value does not appear in this reference or the official docs, it will be rejected at execution time.
- **⚠️ Strict SOURCE rule** — for *discovery queries* (looking up users, integrations, current attachments, login activity). Only use the queries listed in the **Canonical Sources of Truth** section below. Do NOT invent system functions, view names, or columns. If a query you'd like to run is not on this list, run `cortex search docs "<topic>"` first to verify before executing.

---

## Official Documentation

Use `snowflake_product_docs` to verify and cross-reference any property behavior covered below, especially if the context below is stale or when encountering unexpected errors.

- [CREATE AUTHENTICATION POLICY](https://docs.snowflake.com/en/sql-reference/sql/create-authentication-policy)
- [ALTER AUTHENTICATION POLICY](https://docs.snowflake.com/en/sql-reference/sql/alter-authentication-policy)
- [Authentication Policies Guide](https://docs.snowflake.com/en/user-guide/authentication-policies)

---

## Canonical Sources of Truth (Anti-Hallucination)

Every discovery / metadata query in any workflow must come from this list. Workflows reference this section by name; do not improvise.

### Discovery / metadata
- `SHOW AUTHENTICATION POLICIES IN ACCOUNT` — list all policies
- `SHOW AUTHENTICATION POLICIES ON ACCOUNT` — list policies attached at account level (incl. `FOR ALL PERSON USERS`, `FOR ALL SERVICE USERS`)
- `SHOW AUTHENTICATION POLICIES ON USER <username>` — list policy attached to a specific user
- `DESC AUTHENTICATION POLICY <name>` — full configuration of a policy
- `SELECT GET_DDL('POLICY', '<db>.<schema>.<name>')` — DDL for revert
- `SELECT * FROM TABLE(<policy_db>.INFORMATION_SCHEMA.POLICY_REFERENCES(POLICY_NAME => '<policy_db>.<schema>.<name>'))` — what objects a policy is attached to (qualify with the policy's DB; the unqualified form fails when no current database is set)
- `SHOW USERS` — canonical list of users with `TYPE` (PERSON / SERVICE / LEGACY_SERVICE / NULL), `DISABLED`, `DEFAULT_ROLE`, etc.
- `SHOW INTEGRATIONS` — canonical list of security integrations with type (SAML2, OAUTH, etc.)

### Login / usage analysis (used by `workflows/recommend.md`)
- `SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY` — actual auth method usage. Useful columns: `EVENT_TIMESTAMP`, `USER_NAME`, `CLIENT_IP`, `REPORTED_CLIENT_TYPE`, `REPORTED_CLIENT_VERSION`, `FIRST_AUTHENTICATION_FACTOR`, `SECOND_AUTHENTICATION_FACTOR`, `IS_SUCCESS`, `ERROR_CODE`, `ERROR_MESSAGE`, `RELATED_EVENT_ID`. Latency: up to 2 hours.
- `SNOWFLAKE.ACCOUNT_USAGE.USERS` — user inventory. Useful columns: `NAME`, `TYPE` (PERSON/SERVICE/LEGACY_SERVICE/NULL — **not** `USER_TYPE`), `DISABLED` (**VARIANT** — cast with `::STRING` or `::BOOLEAN` before comparing), `DELETED_ON`, `LAST_SUCCESS_LOGIN`, `HAS_PASSWORD`, `HAS_RSA_PUBLIC_KEY`, `HAS_MFA`, `HAS_PAT`, `HAS_WORKLOAD_IDENTITY`. Filter `WHERE DELETED_ON IS NULL` for active users.
- `SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_USERS` / `GRANTS_TO_ROLES` — role assignments (relevant for service-user scoping).

### Forbidden — DO NOT use these (commonly hallucinated)
- ❌ `SYSTEM$GET_ACCOUNT_AUTHENTICATION_POLICY_DETAILS()` — does not exist. Use `SHOW AUTHENTICATION POLICIES ON ACCOUNT` instead.
- ❌ Any `SYSTEM$...AUTH_POLICY...` function not documented at [docs.snowflake.com/sql-reference/functions](https://docs.snowflake.com/en/sql-reference-functions).
- ❌ `SNOWFLAKE.ACCOUNT_USAGE.AUTHENTICATION_POLICIES` — does not exist. Use `SHOW AUTHENTICATION POLICIES IN ACCOUNT`.
- ❌ `INFORMATION_SCHEMA.LOGIN_HISTORY` for usage analysis — retention is too short (~7 days) and is per-database. Use `SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY`.
- ❌ Inventing an `ACCOUNT_USAGE.USERS.USER_TYPE` column — the real column is `TYPE`.

### Terminology note: user TYPE vs policy scope keyword

These two concepts look similar but are NOT the same — do not conflate them:

| Concept | Where it lives | Values |
|---------|---------------|--------|
| **`USERS.TYPE` column** | `SHOW USERS` and `ACCOUNT_USAGE.USERS` (per-user metadata) | `PERSON`, `SERVICE`, `LEGACY_SERVICE`, `NULL` (treat NULL as "unknown / likely person") |
| **Account-level scope keyword** | `ALTER ACCOUNT SET AUTHENTICATION POLICY <p> FOR ALL { PERSON \| SERVICE } USERS` | `PERSON USERS`, `SERVICE USERS` (no `LEGACY_SERVICE` form) |

When you generate `ALTER ACCOUNT SET ... FOR ALL PERSON USERS`, Snowflake applies the policy to users where `TYPE = 'PERSON'`. Users with `TYPE = 'LEGACY_SERVICE'` or `TYPE IS NULL` are NOT covered by either `FOR ALL PERSON USERS` or `FOR ALL SERVICE USERS` — they need user-level policies.

If you need data not covered by the list above, **stop and ask the user** rather than guessing a function or view.

---

## Compatibility Rules

Check these after gathering property selections. Violations will cause the CREATE/ALTER to fail.

| Rule | Detail                                                                                                                                                                                                                                                                                                                             |
|------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **SNOWFLAKE_UI requires PASSWORD or SAML** | Only `PASSWORD` and `SAML` can authenticate through `SNOWFLAKE_UI`. This rule only applies when `SNOWFLAKE_UI` is **explicitly** listed in `CLIENT_TYPES` — `CLIENT_TYPES = ALL` does **not** trigger this requirement. When `SNOWFLAKE_UI` is explicit, at least one of `PASSWORD` or `SAML` must be in `AUTHENTICATION_METHODS`. |
| **MFA_ENROLLMENT requires SNOWFLAKE_UI** | `MFA_ENROLLMENT = REQUIRED` or `REQUIRED_PASSWORD_ONLY` requires `SNOWFLAKE_UI` in `CLIENT_TYPES` (enrollment happens in Snowsight).                                                                                                                                                                                               |
| **CLIENT_POLICY requires DRIVERS** | Can only specify drivers in `CLIENT_POLICY` if `CLIENT_TYPES` includes `DRIVERS` or `ALL`.                                                                                                                                                                                                                                          |
| **PAT_POLICY expiry bounds** | `DEFAULT_EXPIRY_IN_DAYS` <= `MAX_EXPIRY_IN_DAYS` <= 365.                                                                                                                                                                                                                                                                           |
| **SECURITY_INTEGRATIONS must match auth methods** | Integration types must be compatible with `AUTHENTICATION_METHODS` (e.g., a SAML integration requires SAML in the methods list; errno 3621).                                                                                                                                                                                       |

---

## 1. AUTHENTICATION_METHODS

Controls which authentication methods are allowed.

**Valid values:** `ALL` (default), `PASSWORD`, `SAML`, `OAUTH`, `KEYPAIR`, `PROGRAMMATIC_ACCESS_TOKEN`, `WORKLOAD_IDENTITY`

**Compatibility:** Only `PASSWORD` and `SAML` can authenticate through `SNOWFLAKE_UI`. This constraint only applies when `SNOWFLAKE_UI` is **explicitly** listed in `CLIENT_TYPES` — it does not apply when `CLIENT_TYPES = ALL`.

```sql
CREATE AUTHENTICATION POLICY saml_oauth_policy
  AUTHENTICATION_METHODS = ('SAML', 'OAUTH')
  COMMENT = 'SSO and OAuth only';
```

---

## 2. CLIENT_TYPES

Restricts which client applications can authenticate. **Best-effort control** — does NOT restrict Snowflake REST API access.

**Valid values:** `ALL` (default), `SNOWFLAKE_UI`, `DRIVERS`, `SNOWFLAKE_CLI`, `SNOWSQL`

**Compatibility:** Only `PASSWORD` and `SAML` auth methods work through `SNOWFLAKE_UI`. This constraint only applies when `SNOWFLAKE_UI` is **explicitly** listed — `CLIENT_TYPES = ALL` does not trigger it. When `SNOWFLAKE_UI` is explicit, ensure at least one of `PASSWORD` or `SAML` is in `AUTHENTICATION_METHODS`. If `MFA_ENROLLMENT = REQUIRED`, you **must** include `SNOWFLAKE_UI` (enrollment happens in Snowsight). If `DRIVERS` is excluded, automated ingestion may stop working.

```sql
CREATE AUTHENTICATION POLICY programmatic_only_policy
  CLIENT_TYPES = ('DRIVERS', 'SNOWFLAKE_CLI', 'SNOWSQL')
  COMMENT = 'No web UI access';
```

---

## 3. CLIENT_POLICY

Specifies minimum version requirements for specific driver clients. **Best-effort control.**

**Syntax:**
```sql
CLIENT_POLICY = (
  <client_type> = ( MINIMUM_VERSION = '<version>' )
  [, ...]
)
```

**Valid client_type values** (unquoted): `JDBC_DRIVER`, `ODBC_DRIVER`, `PYTHON_DRIVER`, `JAVASCRIPT_DRIVER`, `C_DRIVER`, `GO_DRIVER`, `PHP_DRIVER`, `DOTNET_DRIVER`, `SQL_API`, `SNOWPIPE_STREAMING_CLIENT_SDK`, `PY_CORE`, `SPROC_PYTHON`, `PYTHON_SNOWPARK`, `SQL_ALCHEMY`, `SNOWPARK`, `SNOWFLAKE_CLIENT`

**Version format:** Three digits delimited by periods in single quotes (e.g., `'3.25.0'`)

**Compatibility:** Requires `DRIVERS` (or `ALL`) in `CLIENT_TYPES`. Fails if `DRIVERS` is not present.

```sql
CREATE AUTHENTICATION POLICY driver_version_policy
  CLIENT_TYPES = ('DRIVERS')
  CLIENT_POLICY = (
    JDBC_DRIVER = (MINIMUM_VERSION = '3.25.0'),
    PYTHON_DRIVER = (MINIMUM_VERSION = '3.0.0')
  );
```

---

## 4. SECURITY_INTEGRATIONS

A list of security integrations the authentication policy is associated with. This parameter has no effect when SAML or OAUTH are not in the AUTHENTICATION_METHODS list.
**Valid values:** List of existing integration names, or `ALL` (default)

**Errors:** Fails if integration does not exist or is inactive (errno=3620). Fails if integration type doesn't match AUTHENTICATION_METHODS (errno=3621).

**Compatibility:** All integrations must match the `AUTHENTICATION_METHODS` list — e.g., a SAML integration requires SAML in the methods. Mismatches fail with errno 3621.

```sql
CREATE AUTHENTICATION POLICY sso_restricted_policy
  AUTHENTICATION_METHODS = ('SAML')
  SECURITY_INTEGRATIONS = ('okta_integration')
  COMMENT = 'Only Okta SSO allowed';
```

---

## 5. MFA_ENROLLMENT

Determines whether users must enroll in multi-factor authentication.

**Valid settable values (for CREATE/ALTER):**
- `REQUIRED` — Human users using password or SSO must enroll in MFA
- `REQUIRED_PASSWORD_ONLY` — Human users using password must enroll; SSO users exempt
- `OPTIONAL` — Backwards compatibility only (default).

**⚠️ DESC display value differs from settable value:** `DESC AUTHENTICATION POLICY` returns `REQUIRED_SNOWFLAKE_UI_PASSWORD_ONLY` when the policy uses password-only MFA, but this is a **display-only** value. The correct settable value for CREATE/ALTER is `REQUIRED_PASSWORD_ONLY`. Never use `REQUIRED_SNOWFLAKE_UI_PASSWORD_ONLY` in SQL statements.

**Compatibility:** `REQUIRED` and `REQUIRED_PASSWORD_ONLY` require `SNOWFLAKE_UI` in `CLIENT_TYPES` (Snowsight is the only MFA enrollment interface).

```sql
CREATE AUTHENTICATION POLICY mfa_required_policy
  MFA_ENROLLMENT = 'REQUIRED'
  CLIENT_TYPES = ('SNOWFLAKE_UI', 'DRIVERS');
```

---

## 6. MFA_POLICY

Specifies the policies that affect how multi-factor authentication (MFA) is enforced.

### ALLOWED_METHODS
MFA methods users can use as a second factor. Can specify multiple in a comma delimited list.

**Valid values:** `ALL` (default), `PASSKEY`, `TOTP`, `OTP`, `DUO`

### ENFORCE_MFA_ON_EXTERNAL_AUTHENTICATION
Whether MFA is required when authenticating via SSO.

**Valid values:** `ALL` (require MFA for SSO), `NONE` (default)

**Note:** If your IdP already enforces MFA, setting `ALL` may cause double MFA prompts.

```sql
CREATE AUTHENTICATION POLICY mfa_strict_policy
  MFA_ENROLLMENT = 'REQUIRED'
  MFA_POLICY = (
    ALLOWED_METHODS = ('PASSKEY', 'TOTP')
    ENFORCE_MFA_ON_EXTERNAL_AUTHENTICATION = 'ALL'
  );
```

---

## 7. PAT_POLICY (Programmatic Access Token Policy)

Controls programmatic access token behavior.

### Properties

| Property | Description | Default | Range |
|----------|-------------|---------|-------|
| `DEFAULT_EXPIRY_IN_DAYS` | Default token expiration | 15 | 1 to MAX_EXPIRY |
| `MAX_EXPIRY_IN_DAYS` | Maximum allowed expiration | 365 | DEFAULT_EXPIRY to 365 |
| `NETWORK_POLICY_EVALUATION` | Network policy enforcement for PATs | `ENFORCED_REQUIRED` | See below |
| `REQUIRE_ROLE_RESTRICTION_FOR_SERVICE_USERS` | Require role-scoped PATs for service users | TRUE | TRUE/FALSE |

**NETWORK_POLICY_EVALUATION:**
Specifies how network policy requirements are handled for programmatic access tokens.

By default, a user must be subject to a network policy with one or more network rules to generate or use programmatic access tokens

Values:
- `ENFORCED_REQUIRED` — Must have network policy to generate/use PATs (default)
- `ENFORCED_NOT_REQUIRED` — Network policy not required, but enforced if present
- `NOT_ENFORCED` — Network policy not required and not enforced

**Validation:** `DEFAULT_EXPIRY_IN_DAYS` <= `MAX_EXPIRY_IN_DAYS` <= 365 (errno=3618). Changing `REQUIRE_ROLE_RESTRICTION_FOR_SERVICE_USERS` from FALSE to TRUE invalidates existing unrestricted PATs.

```sql
CREATE AUTHENTICATION POLICY restricted_pat_policy
  PAT_POLICY = (
    DEFAULT_EXPIRY_IN_DAYS = 7
    MAX_EXPIRY_IN_DAYS = 30
    NETWORK_POLICY_EVALUATION = ENFORCED_REQUIRED
    REQUIRE_ROLE_RESTRICTION_FOR_SERVICE_USERS = TRUE
  );
```

---

## 8. WORKLOAD_IDENTITY_POLICY

Controls workload identity federation (cloud provider service accounts).

### Properties

| Property | Description | Default |
|----------|-------------|---------|
| `ALLOWED_PROVIDERS` | Allowed identity providers | `ALL` |
| `ALLOWED_AWS_ACCOUNTS` | AWS account IDs allowed | `ALL` |
| `ALLOWED_AZURE_ISSUERS` | Azure Entra ID tenant issuers | `ALL` |
| `ALLOWED_OIDC_ISSUERS` | OIDC provider issuers | `ALL` |

**ALLOWED_PROVIDERS values:** `ALL`, `AWS`, `AZURE`, `GCP`, `OIDC` (comma-delimited list)

**Format requirements:**
- AWS accounts: 12-digit string (e.g., `'123456789012'`)
- Azure issuers: `https://login.microsoftonline.com/<tenantId>/v2.0`
- OIDC issuers: Valid HTTPS URL, no query/fragment, max 2048 chars

```sql
CREATE AUTHENTICATION POLICY cloud_workload_policy
  AUTHENTICATION_METHODS = ('WORKLOAD_IDENTITY')
  CLIENT_TYPES = ('DRIVERS')
  WORKLOAD_IDENTITY_POLICY = (
    ALLOWED_PROVIDERS = (AWS, AZURE)
    ALLOWED_AWS_ACCOUNTS = ('123456789012')
    ALLOWED_AZURE_ISSUERS = (
      'https://login.microsoftonline.com/8c7832f5-de56-4d9f-ba94-3b2c361abe6b/v2.0'
    )
  );
```
