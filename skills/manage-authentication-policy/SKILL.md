---
name: manage-authentication-policy
title: Manage Authentication Policy
summary: Create, modify, view, attach, detach, drop, or recommend Snowflake authentication policies.
description: >-
  Use when a request involves Snowflake authentication policies — create,
  modify, view, attach, detach, drop, or recommend. Covers AUTHENTICATION_METHODS
  (PASSWORD, SAML, OAUTH, KEYPAIR), MFA enforcement, PAT_POLICY, workload
  identity federation, CLIENT_TYPES, and minimum driver versions. Triggers:
  authentication policy, auth policy, MFA policy, PAT policy, workload identity,
  keypair only, SAML only, require MFA, harden authentication.
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Read
  - Write
  - Edit
prompt: "$manage-authentication-policy recommend authentication policies for my account based on recent login activity"
language: en
status: Published
author: Snowflake
type: snowflake
demo-url: ""
---

# Authentication Policy

Manage Snowflake authentication policies — controls for how users authenticate, which clients they use, MFA requirements, PAT restrictions, and workload identity federation.

## When to Use

- User wants to create, modify, view, attach, detach, or drop an authentication policy
- User asks about restricting authentication methods or client types
- User wants to enforce MFA, configure PAT expiry, or set up workload identity
- User asks about minimum driver version enforcement
- User asks for a recommendation, audit, or starting-point set of policies for their account ("help me set up auth policies", "what should I do?", "harden authentication")

## When NOT to Use

This skill is scoped to **authentication** policies. Do NOT load it for:

- **Session policies** — `CREATE SESSION POLICY`, `SESSION_IDLE_TIMEOUT_MINS`, etc. Different SQL surface, different attachment semantics.
- **Network policies** — `CREATE NETWORK POLICY`, IP allowlists / blocklists. Use the network-security skill.
- **Password policies** — `CREATE PASSWORD POLICY`, password length / rotation / history rules. Different DDL.
- **Row access / masking / aggregation policies** — those are data-governance concerns; route to the data-governance skill.
- **`GRANT` / `REVOKE` of privileges** — RBAC plumbing, not authentication-policy operations. Use the access-troubleshooter or general SQL author skill.
- **Login troubleshooting for a single user** ("why can't user X log in?") — that's a diagnostic flow, not a policy operation. Route to the access-troubleshooter or security-investigation skill; come back here only if the diagnosis points at an attached authentication policy.

## Workflows

| Workflow | Description |
|----------|-------------|
| `workflows/create.md` | Create a new authentication policy |
| `workflows/modify.md` | Modify an existing authentication policy |
| `workflows/view.md` | View or describe authentication policies |
| `workflows/attach-detach.md` | Attach or detach a policy from account or users |
| `workflows/drop.md` | Drop an authentication policy |
| `workflows/recommend.md` | **Recommend** policies based on actual `LOGIN_HISTORY` activity (start here when user is unsure what to do) |

## References

Load [references/property-reference.md](references/property-reference.md) on-demand for detailed syntax, valid values, and examples for all 8 policy properties.

---

## Agent Behavior Rules (Apply to ALL Workflows)

1. **Safety protocols** — Never run DDL/DML without explicit user approval. When modifying or dropping an existing policy, capture current state (`DESC` + `GET_DDL`) before changes — skip this for create (no existing policy). Warn about account-level impact. Provide revert instructions after changes. Execute SQL in presented order.

2. **Follow step order** — Execute steps sequentially as defined. Do not skip steps, pre-select options, or jump ahead based on the user's opening message. Route based on AskUserQuestion selections, not inferred intent.

3. **Hard stops are mandatory** — Steps marked ⚠️ are gates. Do not proceed until the required user input is received. Once approved, proceed directly without re-asking.

4. **Choices vs. free text** — When the user must choose between options (routing, conflict resolution, selecting from query results like databases or policies), use AskUserQuestion with a selectable list. Only collect as plain free text when creating new names (policy name, comment).

5. **Handling "Something else" or free text responses** — When the user selects "Something else" or replies with free text instead of choosing a presented option, try to map their intent to one of the existing options. If the mapping is clear, treat it as that selection and proceed. If the intent doesn't match any option, re-present the menu and ask the user to pick the closest one.

---

## Canonical Sources of Truth (Anti-Hallucination)

⚠️ **Strict rule:** Every SHOW command, table function, or `ACCOUNT_USAGE` view used to discover authentication-policy data MUST come from the **Canonical Sources of Truth** section in [references/property-reference.md](references/property-reference.md). That section also lists commonly-hallucinated names that are **not** valid (e.g. `SYSTEM$GET_ACCOUNT_AUTHENTICATION_POLICY_DETAILS()`, `ACCOUNT_USAGE.AUTHENTICATION_POLICIES`).

Load `references/property-reference.md` on-demand from any workflow before issuing discovery queries.

---

## Main Workflow
### Step 1: Determine Intent

Ask what the user wants to do. **Do NOT load any workflow file yet** — just record the selection for Step 3.

```python
AskUserQuestion(
    questions=[{
        "question": "What would you like to do with authentication policies?",
        "header": "Operation",
        "multiSelect": false,
        "options": [
            {"label": "Create a new policy", "description": "Create a new authentication policy from scratch"},
            {"label": "Modify an existing policy", "description": "Update properties of an existing authentication policy"},
            {"label": "Show/describe a policy", "description": "Show details of existing authentication policies"},
            {"label": "Attach/detach a policy", "description": "Apply policy to account/users or remove it"},
            {"label": "Drop a policy", "description": "Delete an authentication policy"},
            {"label": "Recommend policies for me", "description": "Analyze recent LOGIN_HISTORY and recommend policies tailored to my account (use this if unsure where to start)"}
        ]
    }]
)
```

Record the selection, then proceed to **Step 2**.

---

### Step 2: Privilege Check

Tell the user: *"Checking your current role to verify you have the required privileges."* Then run:

```sql
SELECT CURRENT_ROLE() AS active_role;
```

Present the active role and the required privileges for the selected operation:

| Operation | Required Privilege(s) | Notes |
|-----------|----------------------|-------|
| Recommend policies | Read access to `SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY` and `SNOWFLAKE.ACCOUNT_USAGE.USERS` (typically via `IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE` or `ACCOUNTADMIN`) | Read-only — generates recommendations as copy-paste SQL; no DDL until user routes to create/attach |
| SHOW policies | `OWNERSHIP` on the policy **OR** `USAGE` on the schema | Only returns objects the current role has at least one access privilege on
| DESC policy | `APPLY AUTHENTICATION POLICY` on Account **OR** `OWNERSHIP` on the policy | |
| Create policy | `CREATE AUTHENTICATION POLICY` on the schema | |
| Modify policy | `OWNERSHIP` on the policy | |
| Drop policy | `OWNERSHIP` on the policy | |
| Attach/detach (account-level) | `APPLY AUTHENTICATION POLICY` on Account | |
| Attach/detach (user-level) | `APPLY AUTHENTICATION POLICY` on Account (global) **OR** `APPLY AUTHENTICATION POLICY` on the specific user | |

> **Note:** All operations require at least one privilege on the parent database and schema.

Then ask:

```python
AskUserQuestion(
    questions=[{
        "question": "Active role: [ROLE]. Required: [privileges for selected operation]. Good to continue?",
        "header": "Privilege Check",
        "multiSelect": false,
        "options": [
            {"label": "Yes, continue", "description": "Proceed with current role"},
            {"label": "Switch role first", "description": "I need to USE ROLE — help me switch"}
        ]
    }]
)
```

**If "Switch role first":** Ask which role, run `USE ROLE <role>;`, then re-present the check.
**If "Yes, continue":** Proceed immediately to **Step 3**.

Do NOT hard-block on privilege concerns. Proceed and handle permission errors if they occur. **On permission error:** present the error, suggest the appropriate `GRANT` or `USE ROLE`, and retry once. If it fails again, stop and ask for guidance.

#### ⚠️ Role Persistence Rule (Critical — read every time)

`USE ROLE` issued in one `sql_execute` call **does not necessarily persist** to subsequent `sql_execute` calls in this environment. If you switch role here OR at any later point in any workflow, the role is **not guaranteed** to carry forward.

**Required pattern for every state-changing statement (`CREATE`, `ALTER`, `DROP`, `GRANT`, `REVOKE`, `USE DATABASE`, `USE SCHEMA`):**

1. Track the role chosen in this step as `<chosen_role>` and carry it through the rest of the workflow.
2. Whenever you present SQL that mutates state, prepend `USE ROLE <chosen_role>;` and execute the role-set + the operative statement(s) as a **single multi-statement `sql_execute` call** (semicolon-separated).
3. Do the same for `USE DATABASE` / `USE SCHEMA` set in workflow A1 — re-issue them alongside the operative DDL.

**Example — correct:**
```sql
USE ROLE SECURITYADMIN;
USE DATABASE MY_DB;
USE SCHEMA MY_SCHEMA;
CREATE AUTHENTICATION POLICY my_policy
  AUTHENTICATION_METHODS = ('SAML', 'OAUTH');
```
(All four statements submitted in one `sql_execute` call.)

**Example — incorrect (causes the role-reverting bug seen in past sessions):**
- Call 1: `USE ROLE SECURITYADMIN;`
- Call 2: `CREATE AUTHENTICATION POLICY ...;` ← may run under the original session role and fail with permission errors

If a permission error occurs anyway, **do not** start adding `GRANT` statements — re-issue the failed statement with `USE ROLE` prepended in a single call. Only consider grants after that pattern has been tried.

---

### Step 3: Load and Execute Workflow

**⚠️ CRITICAL — DO NOT SKIP THIS STEP. DO NOT AD-LIB THE WORKFLOW.**

You MUST read the relevant workflow file below and then follow it step by step. The workflow files contain the full procedure.

| Step 1 Selection | File to read (use Read tool) |
|-----------|--------|
| **Recommend policies for me** | `workflows/recommend.md` |
| **Create a new policy** | `workflows/create.md` |
| **Modify an existing policy** | `workflows/modify.md` |
| **Show/describe a policy** | `workflows/view.md` |
| **Attach/detach a policy** | `workflows/attach-detach.md` |
| **Drop a policy** | `workflows/drop.md` |

**If you proceed without reading the file, the workflow will be wrong.**

Once the workflow completes its final step, proceed to **Step 4**.

---

### Step 4: Repeat or Done

```python
AskUserQuestion(
    questions=[{
        "question": "What would you like to do next?",
        "header": "Next Step",
        "multiSelect": false,
        "options": [
            {"label": "Start another operation", "description": "Return to the authentication policy main menu"},
            {"label": "Done", "description": "Exit"}
        ]
    }]
)
```

| Selection | Action |
|-----------|--------|
| **Start another operation** | Go back to **Step 1** and present its exact AskUserQuestion again (the 5-option menu above). Do NOT infer the next operation, do NOT run any other commands or tools — just show Step 1's menu. |
| **Done** | Workflow complete — stop. Do not suggest further actions. |

---

## Quick Reference

### All Parameters

| Parameter | Purpose | Default |
|-----------|---------|---------|
| `AUTHENTICATION_METHODS` | Which auth methods allowed | `ALL` |
| `CLIENT_TYPES` | Which clients can connect | `ALL` |
| `CLIENT_POLICY` | Minimum driver versions | None |
| `SECURITY_INTEGRATIONS` | Allowed SAML/OAuth integrations | `ALL` |
| `MFA_ENROLLMENT` | MFA requirement level | `OPTIONAL` |
| `MFA_POLICY` | MFA method restrictions and SSO enforcement | None |
| `PAT_POLICY` | PAT expiry, network policy, role restrictions | See defaults |
| `WORKLOAD_IDENTITY_POLICY` | Cloud provider restrictions | `ALL` |

### Compatibility Rules

See [references/property-reference.md](references/property-reference.md) **Compatibility Rules** section for the full list. Key rule: only `PASSWORD` and `SAML` work through `SNOWFLAKE_UI` — but this only applies when `SNOWFLAKE_UI` is **explicitly** listed in `CLIENT_TYPES`, not when `CLIENT_TYPES = ALL`.

### Policy Precedence (Fixed Hierarchy)

1. **User-level policy** (highest) — `ALTER USER <name> SET AUTHENTICATION POLICY`
2. **Account user-type policy** (middle) — `ALTER ACCOUNT SET ... FOR ALL PERSON/SERVICE USERS`
3. **Account-level policy** (lowest) — `ALTER ACCOUNT SET AUTHENTICATION POLICY`

The `FORCE` flag does NOT change precedence. It only allows setting a policy when one already exists at that level. User-level policies always win.

---

## Error Reference

| Cause | Fix |
|-------|-----|
| Security integration doesn't exist or inactive | Create/enable the integration first |
| Integration type doesn't match AUTHENTICATION_METHODS | Match integration type to auth methods |
| Invalid PAT_POLICY values | Check DEFAULT_EXPIRY <= MAX_EXPIRY, both 1-365 |
| CLIENT_POLICY driver set without DRIVERS in CLIENT_TYPES | Add DRIVERS to CLIENT_TYPES |
| Invalid or misspelled field name | Check for typos in property names |
| Policy already attached at that level | UNSET existing policy first, or use FORCE |
| Cannot drop policy (still in use) | UNSET from all accounts/users before dropping |

---

## Stopping Points

- ✋ Step 1: always wait for user selection before routing
- ✋ Step 2: privilege check — wait for user confirmation before loading workflow
- ✋ Step 3: must load the workflow file — do not skip
- ✋ `workflows/recommend.md`: existing-policy inventory branch (R2), lookback window (R3), per-group review of generated SQL (R6), routing decision (R7)
- ✋ `workflows/create.md`: location (A1), property selection (A2a — including the exclusive "Recommend for me" option), multi-step property values (A2b), compatibility conflicts (A2c), policy name (A3), then A4 SQL approval
- ✋ `workflows/modify.md`: AskUserQuestion at policy selection, ready-to-proceed confirmation, property selection, **B4c break-glass gate when modifying an account-attached policy**, then B5 SQL approval
- ✋ `workflows/attach-detach.md` D1: attach or detach, D2a: policy selection, D2b: scope, D2c: break-glass gate (now stricter), D3: conflict check, D4: approval
- ✋ `workflows/attach-detach.md` D5: detach scope, D6: approval before execution
- ✋ `workflows/drop.md`: AskUserQuestion at policy selection, then E3 SQL approval
- ✋ Any time an incompatible combination is detected: stop and explain before proceeding

**Resume rule:** Upon user approval, proceed directly to the next step without re-asking.

---

## Output

- Created or modified authentication policy with verified configuration
- `DESC AUTHENTICATION POLICY` output confirming properties
- Revert instructions (saved DDL) for any changes made
- Attachment confirmation with precedence explanation
