---
name: modify-authentication-policy-workflow
parent_skill: manage-authentication-policy
---

# Workflow B: Modify Existing Policy

## When to Load

Loaded from `manage-authentication-policy/SKILL.md` when user selects **Modify existing policy**.

## Prerequisites

- Step 1 (intent selection) and Step 2 (privilege check) completed

---

## B1: Identify Policy

Help user find the policy:

```sql
SHOW AUTHENTICATION POLICIES IN ACCOUNT;
```

Present the results as a selectable list:

```python
AskUserQuestion(
    questions=[{
        "question": "Which policy would you like to modify?",
        "header": "Select Policy",
        "multiSelect": false,
        "options": [
            # One {"label": "<db>.<schema>.<policy_name>"} per row from SHOW AUTHENTICATION POLICIES
        ]
    }]
)
```

---

## B2: Capture Current State

**Before any changes:**

```sql
DESC AUTHENTICATION POLICY <name>;
SELECT GET_DDL('POLICY', '<db>.<schema>.<name>');
```

Show current configuration to user. Save the DDL for revert capability. Then pause:

```python
AskUserQuestion(
  questions=[{
    "question": "Current state is displayed above. Ready to specify your changes?",
    "header": "Confirm Ready",
    "multiSelect": false,
    "options": [
      {"label": "Yes, specify changes", "description": "Proceed to select properties to modify"},
      {"label": "No, review more details first", "description": "I need to review more before changing"}
    ]
  }]
)
```

---

## B3: Determine Changes

Ask which properties to modify:

```python
AskUserQuestion(
  questions=[{
    "question": "Which properties do you want to modify? Select all that apply.",
    "header": "Properties to Modify",
    "multiSelect": true,
    "options": [
      {"label": "AUTHENTICATION_METHODS", "description": "Which auth methods are allowed"},
      {"label": "CLIENT_TYPES", "description": "Which client types can connect"},
      {"label": "MFA_ENROLLMENT", "description": "MFA requirement level (OPTIONAL / REQUIRED / REQUIRED_PASSWORD_ONLY)"},
      {"label": "SECURITY_INTEGRATIONS", "description": "Allowed SAML/OAuth integrations"},
      {"label": "PAT_POLICY", "description": "PAT expiry and network policy settings"},
      {"label": "WORKLOAD_IDENTITY_POLICY", "description": "Cloud provider restrictions"},
      {"label": "MFA_POLICY", "description": "MFA method restrictions and SSO enforcement"},
      {"label": "CLIENT_POLICY", "description": "Minimum driver version requirements"}
    ]
  }]
)
```

For each selected property, ask for the new value via a follow-up AskUserQuestion. Only use values that appear in [references/property-reference.md](../references/property-reference.md) or official Snowflake documentation, never infer or guess values.

**Sub-policy follow-ups** — when a modification enables or adds a sub-policy, ask about its properties in a separate AskUserQuestion:

- **PAT_POLICY:** Ask for `DEFAULT_EXPIRY_IN_DAYS`, `MAX_EXPIRY_IN_DAYS`, `NETWORK_POLICY_EVALUATION`, `REQUIRE_ROLE_RESTRICTION_FOR_SERVICE_USERS`
- **WORKLOAD_IDENTITY_POLICY:** Ask for `ALLOWED_PROVIDERS` and account/issuer restrictions
- **MFA_POLICY:** Ask for `ALLOWED_METHODS`, `ENFORCE_MFA_ON_EXTERNAL_AUTHENTICATION`
- **SECURITY_INTEGRATIONS:** Ask which named integrations to allow (run `SHOW INTEGRATIONS` to present options)
- **CLIENT_POLICY:** Ask for driver names and minimum versions

For full syntax and valid values, **Load** [references/property-reference.md](../references/property-reference.md).

---

## B4: Check If Policy Is Active

```sql
SHOW AUTHENTICATION POLICIES ON ACCOUNT;
```

Cross-reference against the policy being modified. Three branches:

**B4a — Not attached anywhere:** No special gating — proceed to B5.

**B4b — Attached only to specific users (not account-level):** Warn that modifications take effect immediately for those users. Show the revert DDL from B2. Proceed to B5.

**B4c — Attached at account level (highest risk):**

⚠️ MANDATORY GATE — modifying an account-attached policy can lock out every admin just like attaching a new one. Run the **same break-glass check** as `attach-detach.md` D2c (D2c-1 → D2c-2 → D2c-3) before proceeding.

The check is identical:
1. Ask the user to name a break-glass user.
2. Run `SHOW AUTHENTICATION POLICIES ON USER <break_glass_user>`; for each result, `DESC AUTHENTICATION POLICY <db>.<schema>.<policy>`.
3. A policy counts as "permissive enough" when `AUTHENTICATION_METHODS = ALL` (or includes both `PASSWORD` and `SAML`) AND `CLIENT_TYPES = ALL` (or includes `SNOWFLAKE_UI`) AND `SECURITY_INTEGRATIONS = ALL` (or covers the integration the admin uses).
4. If no permissive policy is attached, set up the escape-hatch (D2c-3 SQL block in `attach-detach.md`) BEFORE running the `ALTER AUTHENTICATION POLICY` in B5.
5. The "I accept the lockout risk" phrase override applies here too.

Show the revert DDL from B2 alongside the gate. Proceed to B5 only after the gate passes (or the user types the override phrase).

---

## B5: Generate, Approve, Execute

1. Generate the `ALTER AUTHENTICATION POLICY <name> SET ...` statement. **Combine all property changes into a single ALTER ... SET statement when possible.** Only use separate ALTER statements when changes affect different objects or have sequential dependencies.

**⚠️ Role / context persistence (re-read `SKILL.md` Step 2 → Role Persistence Rule):**
The SQL block presented below MUST start with `USE ROLE <chosen_role>;` (and `USE DATABASE` / `USE SCHEMA` if you needed to switch context to find the policy). Submit the role-set + `ALTER` as a **single multi-statement `sql_execute` call** — `USE ROLE` is not guaranteed to persist between `sql_execute` invocations.

Example shape:
```sql
USE ROLE <chosen_role>;
ALTER AUTHENTICATION POLICY <db>.<schema>.<name> SET
  AUTHENTICATION_METHODS = (...)
  ...;
```

**⚠️ MANDATORY CHECKPOINT**: Before applying changes:

Present to user:
```
I will make the following changes to [policy_name]:

[ALTER SQL statement]

Impact:
- [What changes]
- [Revert DDL if policy is active]

Do you approve? (Yes/No/Modify)
```

Wait for explicit approval (e.g., "approved", "looks good", "proceed").
NEVER proceed without user confirmation.

2. Execute
3. Verify with `DESC AUTHENTICATION POLICY <name>`
4. Provide revert instructions (the saved DDL from B2), then proceed to **Step 4** in `SKILL.md`.
