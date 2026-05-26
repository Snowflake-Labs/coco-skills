---
name: recommend-authentication-policy-workflow
parent_skill: manage-authentication-policy
---

# Workflow R: Recommend Policies (LOGIN_HISTORY-driven)

## When to Load

Loaded from `manage-authentication-policy/SKILL.md` when user selects **Recommend policies for me**. Also the recommended starting point when the user's prompt is generic ("help me set up auth policies", "what should I do for authentication", "audit my auth setup") and Step 1 routes here.

## Why this workflow exists

Most customers don't know exactly which policies they want — they want guidance. Picking values blind is risky: "block password login" sounds simple but can lock out the human admin who relies on Snowsight + password, or break service accounts that still use PAT. This workflow grounds recommendations in **actual login activity** so the agent can produce concrete, per-user-aware suggestions.

## Prerequisites

- Step 1 + Step 2 (intent + privilege check) completed. Entry can be either via the top-level "Recommend policies for me" menu option, **or** routed in from `workflows/create.md` A2a's exclusive "Recommend for me" property option.
- Read access to `SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY` and `SNOWFLAKE.ACCOUNT_USAGE.USERS` (typically `ACCOUNTADMIN` or a role with `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE`)

**Strict rule:** Use only the queries listed in [`references/property-reference.md` → "Canonical Sources of Truth"](../references/property-reference.md#canonical-sources-of-truth-anti-hallucination). Do NOT invent system functions or view names.

**Role persistence:** Re-read the Role Persistence Rule in `SKILL.md` Step 2. Any later DDL routed from this workflow into `create.md` / `attach-detach.md` MUST prepend `USE ROLE <chosen_role>;` and run as a single multi-statement `sql_execute` call.

---

## R1: Verify Access to ACCOUNT_USAGE

Run a probe query first; do not yet pull the full data set:

```sql
SELECT COUNT(*) AS row_count
FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY
WHERE EVENT_TIMESTAMP >= DATEADD(day, -1, CURRENT_TIMESTAMP());
```

**If it succeeds** (any row count, including 0): proceed to R2.

**If it fails** with an access error: tell the user the exact role/grant problem, suggest:
```sql
GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE <their_role>;
```
…and stop until they confirm they fixed it (or ask them to switch to `ACCOUNTADMIN`).

Do NOT silently fall back to `INFORMATION_SCHEMA.LOGIN_HISTORY` — its retention is much shorter (~7 days) and is per-database.

---

## R2: Existing Policy Inventory

Before pulling login activity, check what policies and attachments already exist. If a policy is already attached account-wide, recommending from scratch is the wrong default — the user is likely better served by `view.md` or `modify.md`.

```sql
-- Catalog of policies in the account
SHOW AUTHENTICATION POLICIES IN ACCOUNT;
```

```sql
-- Account-level attachments (covers ALL USERS, ALL PERSON USERS, ALL SERVICE USERS)
SHOW AUTHENTICATION POLICIES ON ACCOUNT;
```

For any policy returned by the first query, optionally enumerate its attachment scope (qualify with the policy's database — `INFORMATION_SCHEMA.POLICY_REFERENCES` is per-database):

```sql
SELECT POLICY_NAME, REF_ENTITY_NAME, REF_ENTITY_DOMAIN
FROM TABLE(<policy_db>.INFORMATION_SCHEMA.POLICY_REFERENCES(
  POLICY_NAME => '<policy_db>.<schema>.<policy_name>'
));
```

Branch on what you find:

**State A — No policies in account, nothing attached.** Tell the user: *"Your account has no authentication policies yet. We'll recommend a starting set."* Proceed to R3.

**State B — Policies exist but none attached at account level.** Some may be attached to specific users (per `POLICY_REFERENCES`). Surface the catalog and ask:

```python
AskUserQuestion(
    questions=[{
        "question": "Found <N> policy/policies in your account; none attached at account level. How would you like to proceed?",
        "header": "Existing Policies",
        "multiSelect": false,
        "options": [
            {"label": "Continue with fresh recommendations", "description": "Generate recommendations based purely on LOGIN_HISTORY"},
            {"label": "Inspect existing policies first", "description": "Skip recommendations — load workflows/view.md to inspect what's already defined"},
            {"label": "Attach an existing policy instead", "description": "Skip recommendations — load workflows/attach-detach.md and apply one of the existing policies"},
            {"label": "Cancel", "description": "Return to Step 4 in SKILL.md"}
        ]
    }]
)
```

**State C — A policy is already attached at account level.** Modifying or replacing an account-attached policy is high-risk. Surface the policy name and scope, and ask:

```python
AskUserQuestion(
    questions=[{
        "question": "Account-level policy <policy_name> is already attached for <scope>. How would you like to proceed?",
        "header": "Account Policy In Place",
        "multiSelect": false,
        "options": [
            {"label": "Recommend changes to <policy_name>", "description": "Generate recommendations and present them as proposed modifications; the modify.md break-glass gate will apply"},
            {"label": "Recommend fresh and compare", "description": "Generate recommendations from LOGIN_HISTORY without touching <policy_name>; compare side-by-side before deciding"},
            {"label": "Inspect existing only", "description": "Skip recommendations — load workflows/view.md"},
            {"label": "Cancel", "description": "Return to Step 4 in SKILL.md"}
        ]
    }]
)
```

| Selection (any state) | Action |
|-----------------------|--------|
| **Continue / Recommend fresh / Recommend changes** | Carry the inventory forward (so R6 generated SQL respects existing attachments via `FORCE`/`UNSET` only on explicit user approval) and proceed to R3. |
| **Inspect existing** | **You MUST read** `workflows/view.md` and follow it. Do not return to recommend.md. |
| **Attach an existing policy** | **You MUST read** `workflows/attach-detach.md` and start at D2a. Do not return to recommend.md. |
| **Cancel** | Return to Step 4 in `SKILL.md`. |

---

## R3: Lookback Window

Wait for the user's selection before continuing.

```python
AskUserQuestion(
    questions=[{
        "question": "How far back should I analyze login activity?",
        "header": "Lookback Window",
        "multiSelect": false,
        "options": [
            {"label": "30 days", "description": "Recent activity only — fastest, but may miss monthly batch jobs"},
            {"label": "60 days", "description": "Balanced view"},
            {"label": "90 days", "description": "Recommended for service accounts — captures quarterly jobs"},
            {"label": "180 days", "description": "Slower; good if you have rare-but-real workflows"}
        ]
    }]
)
```

Record the choice as `<lookback_days>`.

---

## R4: Run Analysis Queries

Run these read-only queries. Present a brief summary of each result before moving on, so the user can spot anomalies. (Existing-policy state was already captured in R2; do not re-query here.)

### R4a: User inventory

```sql
SELECT
  TYPE AS user_type,
  COUNT_IF(DELETED_ON IS NULL AND DISABLED::STRING = 'false') AS active_users,
  COUNT_IF(DELETED_ON IS NULL AND DISABLED::STRING = 'true')  AS disabled_users,
  COUNT_IF(DELETED_ON IS NOT NULL)                            AS deleted_users
FROM SNOWFLAKE.ACCOUNT_USAGE.USERS
GROUP BY TYPE
ORDER BY user_type;
```

> **Note on `TYPE` values:** PERSON, SERVICE, LEGACY_SERVICE, NULL. Treat NULL as "unknown — likely person" and surface it for the user to disambiguate.
>
> **Note on `DISABLED`:** the column is `VARIANT` (not BOOLEAN). Cast with `::STRING` (or `::BOOLEAN`) before comparing. Direct `DISABLED = 'false'` may not match.

### R4b: Auth method usage by user, last `<lookback_days>` days

```sql
WITH recent_logins AS (
  SELECT
    USER_NAME,
    UPPER(FIRST_AUTHENTICATION_FACTOR) AS auth_method,
    UPPER(REPORTED_CLIENT_TYPE)        AS client_type,
    IS_SUCCESS,
    EVENT_TIMESTAMP
  FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY
  WHERE EVENT_TIMESTAMP >= DATEADD(day, -<lookback_days>, CURRENT_TIMESTAMP())
)
SELECT
  l.USER_NAME,
  u.TYPE AS user_type,
  l.auth_method,
  l.client_type,
  COUNT(*)                            AS attempts,
  COUNT_IF(l.IS_SUCCESS = 'YES')      AS successes,
  MAX(l.EVENT_TIMESTAMP)              AS last_seen
FROM recent_logins l
LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.USERS u
  ON u.NAME = l.USER_NAME AND u.DELETED_ON IS NULL
GROUP BY 1, 2, 3, 4
ORDER BY l.USER_NAME, attempts DESC;
```

---

## R5: Build Recommendations

Aggregate the R4b results into per-user-type buckets. Use these heuristics:

### R5a: Person users

For PERSON users (and NULL-type users the user confirms are humans):

1. Compute the dominant auth method per user (the method with the most successes in the window).
2. The set of methods to keep is the **union** of all methods used by ≥5% of person users (or used at least once by anyone if the population is small — under 20 people).
3. Default scaffold:
   - `CLIENT_TYPES`: include `SNOWFLAKE_UI` (Snowsight) and `DRIVERS` if drivers were observed
   - `AUTHENTICATION_METHODS`: include `SAML` if any SSO observed; add `OAUTH` if OAUTH observed in any client; add `PASSWORD` only if a non-trivial percentage still uses password (≥10% of users) AND tighten via MFA
   - `MFA_ENROLLMENT`: `REQUIRED_PASSWORD_ONLY` if `PASSWORD` is in the methods, else `OPTIONAL`
4. Per-user impact preview format:
   - `JDOE — last PASSWORD login 2026-01-15; 92 of last 100 logins were SAML → policy will not block JDOE`
   - `KSMITH — only PASSWORD usage in window; will be blocked unless PASSWORD stays allowed`

### R5b: Service users

Service users get **one account-level `FOR ALL SERVICE USERS` policy** with the union of authentication methods observed in `LOGIN_HISTORY` for `TYPE = 'SERVICE'` users. This single policy covers every service user automatically — no per-user `ALTER USER` statements.

Valid methods for service users (`PASSWORD` is **not** supported and must not be proposed):

| Method | When to include | Companion config |
|---|---|---|
| `OAUTH` | Any OAUTH login observed | `SECURITY_INTEGRATIONS = (<observed integration names>)` |
| `KEYPAIR` | Any KEYPAIR login observed | None |
| `PROGRAMMATIC_ACCESS_TOKEN` | Any PAT login observed | Tight `PAT_POLICY` (`MAX_EXPIRY_IN_DAYS = 30`, `REQUIRE_ROLE_RESTRICTION_FOR_SERVICE_USERS = TRUE`, `NETWORK_POLICY_EVALUATION = ENFORCED_REQUIRED`); flag as "older software only" |
| `WORKLOAD_IDENTITY` | Any WIF login observed | `WORKLOAD_IDENTITY_POLICY` listing cloud providers + account/issuer constraints actually observed (see below) |

#### Generated SQL

```sql
CREATE AUTHENTICATION POLICY service_account_policy
  AUTHENTICATION_METHODS = (<union of observed methods>)
  CLIENT_TYPES = (<observed client types, typically 'DRIVERS'>)
  -- Add SECURITY_INTEGRATIONS if OAUTH is in the union
  -- Add PAT_POLICY block if PROGRAMMATIC_ACCESS_TOKEN is in the union
  -- Add WORKLOAD_IDENTITY_POLICY block if WORKLOAD_IDENTITY is in the union
  COMMENT = 'Service-account policy — union of observed login methods';

ALTER ACCOUNT SET AUTHENTICATION POLICY service_account_policy FOR ALL SERVICE USERS;
```

#### `WORKLOAD_IDENTITY_POLICY` shape (when WIF is in the union)

Inspect `LOGIN_HISTORY.LOGIN_DETAILS` to identify which cloud providers showed up, then emit:

```sql
WORKLOAD_IDENTITY_POLICY = (
  ALLOWED_PROVIDERS = (<observed: AWS, AZURE, GCP, OIDC>)
  ALLOWED_AWS_ACCOUNTS = (<observed AWS account IDs>)        -- if AWS observed
  ALLOWED_AZURE_ISSUERS = ('https://login.microsoftonline.com/<tenant>/v2.0')  -- if AZURE observed
  ALLOWED_OIDC_ISSUERS = ('https://<issuer>')                                  -- if OIDC observed
)
```

If exact AWS account / Azure tenant / OIDC issuer values cannot be determined from `LOGIN_HISTORY`, set only `ALLOWED_PROVIDERS` to the observed providers and surface a note asking the user to tighten the issuer lists once they confirm the values.

#### Anomalies to surface (do not silently lump into the default policy)

- **`TYPE = 'SERVICE'` user with PASSWORD logins** — anomalous; surface for investigation; do NOT include `PASSWORD` in `AUTHENTICATION_METHODS` for the service policy.
- **`TYPE = 'LEGACY_SERVICE'` users** — `FOR ALL SERVICE USERS` may not cover them. Surface separately with a migration recommendation (convert to `TYPE = 'SERVICE'` with KEYPAIR or OAUTH).
- **`TYPE IS NULL` users that look service-like** — ask the user to disambiguate; do not auto-assign.



### R5c: Inactive / unknown users

Surface separately — no policy recommendation, but suggest the user either disable them or assign a deny-all break-glass-style policy. Do not generate `ALTER` statements automatically; let the user decide.

### R5d: Break-glass

Always include a recommendation block at the top of the output:
```sql
-- Break-glass: a permissive policy attached to a single trusted admin user.
-- Confirm this user can log in BEFORE applying any account-level restriction.
CREATE AUTHENTICATION POLICY admin_escape_hatch
  AUTHENTICATION_METHODS = ('ALL')
  CLIENT_TYPES = ('ALL')
  SECURITY_INTEGRATIONS = ('ALL')
  COMMENT = 'Permissive fallback for admin - prevents lockout';

ALTER USER <admin_user> SET AUTHENTICATION POLICY admin_escape_hatch;
```

If `SHOW AUTHENTICATION POLICIES ON USER <admin_user>` (e.g. checked from `attach-detach.md` D2c) shows the user already has a permissive policy, mark this block as "already in place — skip" instead of removing it from the output.

### R5e: Reconciliation with existing policies (from R2)

If R2 found existing policies or account-level attachments, the recommendation block must address them explicitly:

- **State C (existing account-level policy):** present recommendations as a **diff** against the attached policy when the user picked "Recommend changes". Generated SQL is `ALTER AUTHENTICATION POLICY <name> SET ...` (route to `modify.md` in R7), not a fresh `CREATE`. The B4c break-glass gate from `modify.md` applies.
- **State C (recommend fresh + compare):** generate a fresh `CREATE` with a new policy name (do NOT collide with the attached one), and surface the side-by-side diff so the user can decide whether to swap.
- **State B (policies exist, none attached):** if any existing policy already matches the recommended shape, propose attaching it (route to `attach-detach.md`) instead of creating a duplicate.

---

## R6: Present as Copy-Paste Blocks (MANDATORY GATE)

⚠️ MANDATORY GATE — present everything for review BEFORE any execution.

Output structure (single message to the user):

```
## Recommendation summary

- <N> active person users analyzed; dominant methods: <list>
- <M> active service users (TYPE='SERVICE') analyzed; observed methods: <OAUTH:x, KEYPAIR:y, PAT:z, WIF:w>
- <L> LEGACY_SERVICE users — separate migration recommendations below
- <K> users had no logins in the last <lookback_days> days
- Existing account-level policy: <name or "none">

## Recommended SQL (copy into a worksheet to review, then approve below)

-- 1. Break-glass (do this FIRST)
<R5d block>

-- 2. Person-user policy
<CREATE + ALTER ACCOUNT FOR ALL PERSON USERS>

-- 3. Service-user policy (default: single FOR ALL SERVICE USERS, union of observed methods)
<CREATE service_account_policy with AUTHENTICATION_METHODS = (<union>) [+ PAT_POLICY] [+ WORKLOAD_IDENTITY_POLICY] [+ SECURITY_INTEGRATIONS]>
<ALTER ACCOUNT SET AUTHENTICATION POLICY service_account_policy FOR ALL SERVICE USERS>

-- 4. LEGACY_SERVICE users (if any) — migration recommendation
<list of LEGACY_SERVICE users, no auto-generated SQL — manual decision required>

## Anomalies / manual decisions

<list:>
- TYPE='SERVICE' users with PASSWORD logins (anomalous — investigate)
- TYPE IS NULL users that look service-like (need disambiguation)

## Per-user impact preview

<bulleted list of "<user> — last <method> login <date>; <N>/<M> recent logins were <method> → will / will not be blocked">

## Inactive / unknown users (no automated recommendation)

<list>
```

Then ask:

```python
AskUserQuestion(
    questions=[{
        "question": "Review the SQL and impact preview above. How would you like to proceed?",
        "header": "Next Step",
        "multiSelect": false,
        "options": [
            {"label": "Apply the break-glass first, then person-user policy", "description": "Route into create.md + attach-detach.md for blocks 1 and 2 only — defer service users"},
            {"label": "Apply everything (with my approval at each step)", "description": "Walk through every block (break-glass → person → service) one at a time, with explicit approval per block"},
            {"label": "Just give me the SQL to paste myself", "description": "Take the output to a worksheet — no further automated execution"},
            {"label": "Adjust the recommendations", "description": "Re-run with different lookback or different heuristics"}
        ]
    }]
)
```

Do NOT execute any DDL/DML in this workflow itself. All execution flows through `create.md` and `attach-detach.md` (or `modify.md` if R2 routed us into "recommend changes" against an attached policy) for their existing approval gates.

---

## R7: Route to Execution

| Selection | Action |
|-----------|--------|
| **Apply break-glass first, then person-user policy** | For each of the 2 blocks in turn: read `workflows/create.md` and execute starting at A2 using the pre-decided values from R5. After A4 succeeds, read `workflows/attach-detach.md` starting at D2b (scope already known). Re-prepend `USE ROLE <chosen_role>;` per the Role Persistence Rule. **If R2 returned State C with "Recommend changes",** route into `workflows/modify.md` at B3 instead of `create.md` for the affected policy. |
| **Apply everything** | Same as above, but walk through all generated blocks (break-glass → person → service-user policy via `FOR ALL SERVICE USERS`), confirming with the user before each `create.md` / `modify.md` invocation. |
| **Just give me the SQL** | Display the final SQL bundle, then proceed to **Step 4** in `SKILL.md`. |
| **Adjust the recommendations** | Return to R3 (lookback) or R5 heuristics — ask which dimension to change, then re-run R4–R6. R2 inventory is still valid; do NOT re-run it. |

After all blocks are applied (or the user is done), proceed to **Step 4** in `SKILL.md`.

---

## Stopping Points

- ✋ R2: existing-policy inventory branch — wait for the user's selection (continue / inspect / attach existing / modify / cancel)
- ✋ R3: lookback window — wait for selection
- ✋ R6: present everything before any execution; wait for the user's "what next?" choice
- ✋ R7: each routed block goes through `create.md` / `modify.md` / `attach-detach.md`'s own approval gates — do not bypass them
- ✋ Any time existing policies conflict with recommendations: stop and surface the conflict; let the user decide UNSET vs FORCE vs cancel

## Output

- A copy-paste-ready SQL bundle (break-glass → person → service-user groups)
- Per-user impact preview tied to actual `LOGIN_HISTORY` activity
- Optional follow-through into `create.md` / `modify.md` / `attach-detach.md` for guided execution
