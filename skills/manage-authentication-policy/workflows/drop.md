---
name: drop-authentication-policy-workflow
parent_skill: manage-authentication-policy
---

# Workflow E: Drop Policy

## When to Load

Loaded from `manage-authentication-policy/SKILL.md` when user selects **Drop policy**.

## Prerequisites

- Step 1 (intent selection) and Step 2 (privilege check) completed

---

## E1: List All Policies

```sql
SHOW AUTHENTICATION POLICIES IN ACCOUNT;
```

Present the results as a selectable list:

```python
AskUserQuestion(
    questions=[{
        "question": "Which policy would you like to drop?",
        "header": "Select Policy",
        "multiSelect": false,
        "options": [
            # One {"label": "<db>.<schema>.<policy_name>"} per row from SHOW AUTHENTICATION POLICIES
        ]
    }]
)
```

---

## E2: Check If Policy Is Attached

Use the fully qualified name as a single-quoted string literal, matching exactly as shown in SHOW AUTHENTICATION POLICIES. **Qualify the table function with the policy's database** — `INFORMATION_SCHEMA.POLICY_REFERENCES` is per-database and the unqualified form fails when no current database is set.

```sql
-- Example: POLICY_NAME must be a single-quoted, fully qualified string
SELECT * FROM TABLE(mydb.INFORMATION_SCHEMA.POLICY_REFERENCES(
  POLICY_NAME => 'mydb.myschema.my_policy'
));
```

If any rows are returned, the policy is **in use** and cannot be dropped. Detach first — **Load** `workflows/attach-detach.md`.

---

## E3: Capture DDL Before Dropping

```sql
SELECT GET_DDL('POLICY', '<db>.<schema>.<name>');
```

Save the output so the policy can be recreated if needed.

---

## E4: Drop

**⚠️ Role / context persistence (re-read `SKILL.md` Step 2 → Role Persistence Rule):**
The DROP statement below MUST be submitted alongside `USE ROLE <chosen_role>;` (and `USE DATABASE` / `USE SCHEMA` if applicable) as a **single multi-statement `sql_execute` call** — `USE ROLE` is not guaranteed to persist between `sql_execute` invocations.

Example shape:
```sql
USE ROLE <chosen_role>;
DROP AUTHENTICATION POLICY [ IF EXISTS ] <db>.<schema>.<name>;
```

**⚠️ MANDATORY CHECKPOINT**: Before dropping:

Present to user:
```
I will drop the following policy:

[DROP statement]

Saved DDL for recreation if needed:
[GET_DDL output from E3]

This policy is [not attached / attached to X — must detach first].

Do you approve? (Yes/No/Modify)
```

Wait for explicit approval. NEVER proceed without user confirmation.

```sql
DROP AUTHENTICATION POLICY [ IF EXISTS ] <name>;
```

Execute, confirm the policy no longer appears in `SHOW AUTHENTICATION POLICIES IN ACCOUNT`, and present confirmation to user. Then proceed to **Step 4** in `SKILL.md`.
