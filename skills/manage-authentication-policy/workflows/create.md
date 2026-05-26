---
name: create-authentication-policy-workflow
parent_skill: manage-authentication-policy
---

# Workflow A: Create New Policy

## When to Load

Loaded from `manage-authentication-policy/SKILL.md` when user selects **Create new policy**.

## Prerequisites

- Step 1 (intent selection) and Step 2 (privilege check) completed

---

## A1: Location

Check if a database and schema are already active:

```sql
SELECT CURRENT_DATABASE() AS database, CURRENT_SCHEMA() AS schema;
```

**If both are set:** Confirm with the user (e.g. "Policy will be created in `db.schema`. Good to continue?"). Proceed to A2 on confirmation.

**If database is NULL:** Run `SHOW DATABASES;` and present results as a selectable list. Always include a "Create new database" option at the end:

```python
AskUserQuestion(
  questions=[{
    "question": "Which database should the policy be created in?",
    "header": "Database",
    "multiSelect": false,
    "options": [
      # One {"label": "<db_name>"} per row from SHOW DATABASES
      {"label": "Create new database", "description": "I need to create a new database first"}
    ]
  }]
)
```

After database is selected, run `USE DATABASE <selected_db>;`.

**If schema is NULL (or after selecting database):** Run `SHOW SCHEMAS IN DATABASE <db>;` and present results. Always include a "Create new schema" option at the end:

```python
AskUserQuestion(
  questions=[{
    "question": "Which schema in <db> should the policy be created in?",
    "header": "Schema",
    "multiSelect": false,
    "options": [
      # One {"label": "<schema_name>"} per row from SHOW SCHEMAS IN DATABASE
      {"label": "Create new schema", "description": "I need to create a new schema first"}
    ]
  }]
)
```

After schema is selected, run `USE SCHEMA <selected_db>.<selected_schema>;`. Confirm the final `database.schema` before proceeding.

---

## A2: Gather Requirements

**⚠️ MANDATORY FIRST ACTION — before presenting any options to the user:**
Read [references/property-reference.md](../references/property-reference.md). You need its valid values, defaults, and compatibility notes for every step that follows.

### A2a: Select Properties to Configure

Present a single multi-select menu of all configurable properties. Unselected properties keep their defaults.

```python
AskUserQuestion(
  questions=[{
    "question": "Which properties do you want to configure? Unselected properties use defaults (ALL/OPTIONAL). Pick 'Recommend for me' if you're not sure.",
    "header": "Policy Properties",
    "multiSelect": true,
    "options": [
      {"label": "AUTHENTICATION_METHODS", "description": "Which auth methods are allowed (default: ALL)"},
      {"label": "CLIENT_TYPES", "description": "Which clients can connect (default: ALL)"},
      {"label": "MFA_ENROLLMENT", "description": "MFA requirement level (default: OPTIONAL)"},
      {"label": "SECURITY_INTEGRATIONS", "description": "Restrict allowed SAML/OAuth integrations (default: ALL)"},
      {"label": "PAT_POLICY", "description": "PAT expiry limits, network policy, role restrictions"},
      {"label": "WORKLOAD_IDENTITY_POLICY", "description": "Restrict cloud providers and accounts for WIF"},
      {"label": "MFA_POLICY", "description": "MFA method restrictions and SSO enforcement"},
      {"label": "CLIENT_POLICY", "description": "Minimum driver/connector version requirements"},
      {"label": "Recommend for me", "description": "I'm not sure — analyze SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY and suggest values for all properties"}
    ]
  }]
)
```

**Exclusive option — "Recommend for me":**
If the user selects `Recommend for me`, treat it as exclusive — ignore any other property selections in the same response and route into [workflows/recommend.md](recommend.md). Run R1 → R6; when the user picks an option in R6's gate that involves creating *this* policy, return here with `<authentication_methods>`, `<client_types>`, and any sub-policy values pre-decided. Skip A2b's interactive value collection and surface the recommended values for confirmation directly. The user can still override any value before A4.

> **Anti-loop:** If `recommend.md` was already run earlier in the session and the routing brought us into create.md with pre-decided values, do NOT re-show A2a — go straight to confirming the pre-decided values, then to A3.

### A2b: Configure Selected Properties

Build a **single multi-step AskUserQuestion** with one step per selected property. Only include steps for properties the user selected in A2a.

⚠️ Only present values that appear in [references/property-reference.md](../references/property-reference.md) including 'All' if present, or the official Snowflake documentation. Never infer or guess values.
```python
AskUserQuestion(
  questions=[
    # Include only steps for properties selected in A2a. Example for AUTHENTICATION_METHODS + CLIENT_TYPES + MFA_ENROLLMENT:
    {
      "question": "Which authentication methods should this policy allow?",
      "header": "Authentication Methods",
      "multiSelect": true,
      "options": [
        # Valid values from property-reference.md section 1: ALL, PASSWORD, SAML, OAUTH, KEYPAIR, PROGRAMMATIC_ACCESS_TOKEN, WORKLOAD_IDENTITY
      ]
    },
    {
      "question": "Which client types should be allowed to connect?",
      "header": "Client Types",
      "multiSelect": true,
      "options": [
        # Valid values from property-reference.md section 2: ALL, SNOWFLAKE_UI, DRIVERS, SNOWFLAKE_CLI, SNOWSQL
      ]
    },
    {
      "question": "What MFA enrollment level is required?",
      "header": "MFA Enrollment",
      "multiSelect": false,
      "options": [
        # Valid settable values from property-reference.md section 5: OPTIONAL, REQUIRED, REQUIRED_PASSWORD_ONLY
      ]
    }
    # ... one step per selected property, using valid values from the respective property-reference.md sections
  ]
)
```

**Property-specific notes:**
- **SECURITY_INTEGRATIONS** — before including this step, run `SHOW INTEGRATIONS;` and filter to SAML/OAUTH types. Use the results as options.
- **Sub-policies** (PAT_POLICY, WORKLOAD_IDENTITY_POLICY, MFA_POLICY, CLIENT_POLICY) — each sub-policy needs its own step(s) for its sub-properties. Use valid values and defaults from [references/property-reference.md](../references/property-reference.md).

### A2c: Validate Compatibility

**Immediately after A2b returns**, check selected values against the **Compatibility Rules** table in [references/property-reference.md](../references/property-reference.md) (already loaded in A2).

If an incompatible combination is found, present each conflict and its resolution options via AskUserQuestion. Resolve one conflict at a time, re-validate after each. If the user chooses to go back, return to A2a.

---

## A3: Policy Name and Comment

Ask the user for plain text input:

1. *"What would you like to name the policy?"*
2. *"Optional: add a comment/description? (or say 'skip')"*

---

## A4: Generate, Approve, Execute

1. Generate the `CREATE AUTHENTICATION POLICY` SQL based on requirements (include `COMMENT` if provided in A3)

**⚠️ Role / context persistence (re-read `SKILL.md` Step 2 → Role Persistence Rule):**
The SQL block presented below MUST start with `USE ROLE <chosen_role>;` and the `USE DATABASE` / `USE SCHEMA` from A1. Submit the role-set + context-set + `CREATE` statements as a **single multi-statement `sql_execute` call**. Do NOT split them across calls — `USE ROLE` is not guaranteed to persist between `sql_execute` invocations.

Example shape:
```sql
USE ROLE <chosen_role>;
USE DATABASE <db>;
USE SCHEMA <schema>;
CREATE AUTHENTICATION POLICY <name>
  AUTHENTICATION_METHODS = (...)
  ...;
```

**⚠️ MANDATORY CHECKPOINT**: Present the complete SQL and a summary of what each property does. Ask for approval once as selectable menu (Yes/No). When the user approves, execute immediately.

2. Execute the SQL. If it fails, check the Error Reference in SKILL.md and address the issue before retrying.
3. Verify with `DESC AUTHENTICATION POLICY <name>`
4. Ask if user wants to attach the policy. If yes, **you MUST read** [workflows/attach-detach.md](attach-detach.md) and begin at **D2b** (Select Scope) — the policy name from A3 is already known, so skip D1 (attach vs detach choice) and D2a (policy selection). Follow D2b → D2c → D3 → D4 as written. Do not summarize or improvise the attach flow.
5. Present the policy name and location, then proceed to **Step 4** in `SKILL.md`.

> **Routed entry from `recommend.md`:** When this workflow is invoked from `workflows/recommend.md` R7, the A1 (location), A2 (properties), and A3 (policy name) values are already determined by the recommendation. Confirm them with the user, then jump straight to A4. Do not re-collect from scratch.
