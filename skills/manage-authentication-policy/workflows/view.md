---
name: view-authentication-policy-workflow
parent_skill: manage-authentication-policy
---

# Workflow C: View/Describe Policy

## When to Load

Loaded from `manage-authentication-policy/SKILL.md` when user selects **View/describe policy**.

**Prerequisites:** Step 1 (intent selection) and Step 2 (privilege check) completed.

---

## C2: Select View Type

```python
AskUserQuestion(
    questions=[{
        "question": "What would you like to view?",
        "header": "View",
        "multiSelect": false,
        "options": [
            {"label": "List all policies", "description": "Show all authentication policies in the account"},
            {"label": "Account-level attachments", "description": "Show which policies are attached at the account level"},
            {"label": "User-level attachment", "description": "Show which policy is attached to a specific user"},
            {"label": "Inspect a policy", "description": "Show full configuration for a specific policy"}
        ]
    }]
)
```

| Selection | Go to |
|-----------|-------|
| List all policies | C2a |
| Account-level attachments | C2b |
| User-level attachment | C2c |
| Inspect a policy | C2d |

---

## C2a: List All Policies

```sql
SHOW AUTHENTICATION POLICIES IN ACCOUNT;
```

Display all rows returned. Then go to **Next Step**.

---

## C2b: Account-Level Attachments

```sql
SHOW AUTHENTICATION POLICIES ON ACCOUNT;
```

Display all rows returned. Then go to **Next Step**.

---

## C2c: User-Level Attachment

Ask the user which Snowflake username to check (a username, not a role name — e.g. `JSMITH`, not `SYSADMIN`).

```sql
SHOW AUTHENTICATION POLICIES ON USER <username>;
```

Display all rows returned. Then go to **Next Step**.

---

## C2d: Inspect a Policy

First, list available policies:

```sql
SHOW AUTHENTICATION POLICIES IN ACCOUNT;
```

Present the results as a selectable list:

```python
AskUserQuestion(
    questions=[{
        "question": "Which policy would you like to inspect?",
        "header": "Select Policy",
        "multiSelect": false,
        "options": [
            # One {"label": "<db>.<schema>.<policy_name>"} per row from SHOW AUTHENTICATION POLICIES
        ]
    }]
)
```

Then run:

```sql
DESC AUTHENTICATION POLICY <db>.<schema>.<policy_name>;
```

Display all rows returned. Then go to **Next Step**.

---

## C3: Next Step

Proceed to **Step 4** in `SKILL.md`. Additionally offer a "View something else" option that returns to C2.
