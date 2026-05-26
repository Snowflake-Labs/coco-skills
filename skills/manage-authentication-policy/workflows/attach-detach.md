---
name: attach-detach-authentication-policy-workflow
parent_skill: manage-authentication-policy
---

# Workflow D: Attach/Detach Policy

## When to Load

Loaded from `manage-authentication-policy/SKILL.md` when user selects **Attach/detach policy**, or from `workflows/create.md` after a new policy is created.

## Prerequisites

- Step 1 (intent selection) and Step 2 (privilege check) completed
- If arriving from `workflows/create.md`: policy name is already known — skip D2a (policy selection), go straight to D2b (scope)
- For user-level operations: requires `APPLY AUTHENTICATION POLICY` on Account (global) or on the specific user (scoped)

---

## D1: Attach or Detach?

```python
AskUserQuestion(
    questions=[{
        "question": "Do you want to attach or detach an authentication policy?",
        "header": "Operation",
        "multiSelect": false,
        "options": [
            {"label": "Attach a policy", "description": "Apply an authentication policy to account or users"},
            {"label": "Detach a policy", "description": "Remove an authentication policy from account or users"}
        ]
    }]
)
```

| Selection | Action |
|-----------|--------|
| **Attach a policy** | Proceed to D2a |
| **Detach a policy** | Proceed to D5 |

---

## Attach Flow

### D2a: Select Policy

Run `SHOW AUTHENTICATION POLICIES IN ACCOUNT;` and present the results as a selectable list.

```python
AskUserQuestion(
    questions=[{
        "question": "Which policy do you want to attach?",
        "header": "Select Policy",
        "multiSelect": false,
        "options": [
            # One {"label": "<db>.<schema>.<policy_name>"} per row from SHOW AUTHENTICATION POLICIES
        ]
    }]
)
```

**If arriving from `workflows/create.md`:** The policy name is already known — skip this step entirely.

### D2b: Select Scope

```python
AskUserQuestion(
    questions=[{
        "question": "Where should this policy be applied?",
        "header": "Attach Scope",
        "multiSelect": false,
        "options": [
            {"label": "Account — all users", "description": "Apply to all users in the account"},
            {"label": "Account — person users only", "description": "Apply to all person users"},
            {"label": "Account — service users only", "description": "Apply to all service users"},
            {"label": "Specific user(s)", "description": "Apply to one or more named users"}
        ]
    }]
)
```

**If "Specific user(s)":** Ask the user for the username(s).

### D2c: Break-Glass Gate (Account-Level Only)

**Skip this step for user-level attach.**

Account-wide attaches are the highest-risk operation in this skill — a misconfigured policy can lock out **every** admin. Don't proceed without an explicit break-glass user with a permissive policy.

#### D2c-1: Identify the break-glass user (mandatory)

Ask the user to name a single trusted admin user that should always be able to log in (preferably one with `ACCOUNTADMIN` and a working password they have tested in the last 24 hours):

```python
AskUserQuestion(
    questions=[{
        "question": "Account-wide attaches can lock out every user. Name the admin user that should keep unrestricted access (your 'break-glass' user). If you don't have one, pick 'Set one up'.",
        "header": "Break-Glass User",
        "multiSelect": false,
        "options": [
            # If recommend.md was run earlier and a candidate is known, list it here
            {"label": "Set one up now", "description": "I don't have a dedicated break-glass user — guide me through creating the policy and assignment"},
            {"label": "Cancel attach", "description": "I'm not ready — go back to Step 4"}
        ]
    }]
)
```

If the user types a username (free text mapped to "Something else"), treat that as `<break_glass_user>` and continue.

If the user picks **Cancel attach**: return to Step 4 in `SKILL.md`.

#### D2c-2: Check whether the break-glass user already has a permissive policy

Run:
```sql
SHOW AUTHENTICATION POLICIES ON USER <break_glass_user>;
```

Then for any policy returned, run:
```sql
DESC AUTHENTICATION POLICY <db>.<schema>.<policy_name>;
```

A policy counts as **permissive enough** when:
- `AUTHENTICATION_METHODS` is `ALL` **OR** includes both `PASSWORD` and `SAML`
- `CLIENT_TYPES` is `ALL` **OR** includes `SNOWFLAKE_UI`
- `SECURITY_INTEGRATIONS` is `ALL` (or covers the integration the admin uses)

**Branch:**

| Condition | Action |
|-----------|--------|
| Permissive policy already attached to `<break_glass_user>` | Confirm with the user ("`<user>` already has policy `<policy>` covering all auth methods + Snowsight — no break-glass setup needed"), proceed to D3 |
| No policy attached, OR attached policy is restrictive | Proceed to D2c-3 (mandatory escape-hatch setup) |
| User chose **Set one up now** | Proceed to D2c-3 |

#### D2c-3: Mandatory Escape-Hatch Setup

⚠️ MANDATORY GATE — present the SQL and require approval before proceeding to D3.

```sql
-- Submit as a single multi-statement sql_execute call (per Role Persistence Rule)
USE ROLE <chosen_role>;

-- Step 1: Create a permissive admin policy
CREATE AUTHENTICATION POLICY admin_escape_hatch
  AUTHENTICATION_METHODS = ('ALL')
  CLIENT_TYPES = ('ALL')
  SECURITY_INTEGRATIONS = ('ALL')
  COMMENT = 'Permissive fallback for admin - prevents lockout';

-- Step 2: Assign to admin FIRST
ALTER USER <break_glass_user> SET AUTHENTICATION POLICY admin_escape_hatch;
```

Present:
```
WARNING: I will assign a permissive policy to <break_glass_user> BEFORE applying the
restrictive account-wide policy. After this completes, you must verify you can still
log in as <break_glass_user> in a separate session before proceeding to D3.

Do you approve? (Yes / No / Cancel attach)
```

| Selection | Action |
|-----------|--------|
| **Yes** | Execute the multi-statement block, verify with `SHOW AUTHENTICATION POLICIES ON USER <break_glass_user>`, then ask: *"Have you tested logging in as `<break_glass_user>` in a separate session and confirmed it works? (Yes / No)"* If **Yes**, proceed to D3. If **No**, stop and ask the user to test before continuing — do NOT proceed to D3. |
| **No / Cancel attach** | Return to Step 4 in `SKILL.md`. Do NOT proceed to D3. |

> **Acknowledgement override:** If the user explicitly states they understand the risk, have an out-of-band recovery plan, and want to skip D2c-3, require them to type the exact phrase **"I accept the lockout risk"** in chat. Only on receiving that exact phrase may you proceed to D3 without an escape-hatch in place. Anything weaker (e.g. "yeah it's fine") does not satisfy this gate — re-prompt for the phrase.

### D3: Check for Existing Policy

Check if a policy is already set at the target level:
- **Account-level:** `SHOW AUTHENTICATION POLICIES ON ACCOUNT;`
- **User-level:** `SHOW AUTHENTICATION POLICIES ON USER <username>;`

**If a policy is already set at the same level**, present:

```python
AskUserQuestion(
    questions=[{
        "question": "A policy is already set at this level: <existing_policy_name>. How do you want to proceed?",
        "header": "Policy Conflict",
        "multiSelect": false,
        "options": [
            {"label": "UNSET existing, then attach new", "description": "Remove the current policy first, then set the new one"},
            {"label": "Use FORCE to replace", "description": "Overwrite the existing policy in one command"},
            {"label": "Cancel", "description": "Do not attach — go back to Step 4"}
        ]
    }]
)
```

| Selection | Action |
|-----------|--------|
| **UNSET existing, then attach new** | Generate both UNSET + SET commands in sequence |
| **Use FORCE to replace** | Add `FORCE` to the SET command |
| **Cancel** | Proceed to Step 4 in SKILL.md |

**If no policy is set**, skip this step and proceed to D4.

### D4: Approve and Execute (Attach)

**⚠️ Role / context persistence (re-read `SKILL.md` Step 2 → Role Persistence Rule):**
The SQL block below MUST start with `USE ROLE <chosen_role>;` and (when applicable) `USE DATABASE` / `USE SCHEMA`. Submit it as a **single multi-statement `sql_execute` call** — `USE ROLE` is not guaranteed to persist between `sql_execute` invocations.

Example shape (account-level attach):
```sql
USE ROLE <chosen_role>;
ALTER ACCOUNT SET AUTHENTICATION POLICY <db>.<schema>.<policy>
  [ FOR ALL PERSON USERS | FOR ALL SERVICE USERS ];
```

**⚠️ MANDATORY CHECKPOINT**: Present the SQL and ask for approval.

For **account-level attachment**, include the impact warning:
```
WARNING: This will change authentication requirements IMMEDIATELY for all affected users.
Users who don't meet the new requirements will be blocked from logging in.

Scope: [ALL USERS | ALL PERSON USERS | ALL SERVICE USERS]
Policy: <policy_name>
```

For **all attach operations**, present:
```
SQL to execute:
[SQL command(s)]

Impact: [What this changes and who is affected]
Revert command (save this):
[Command to undo this]

Do you approve? (Yes/No)
```

Wait for explicit approval. NEVER proceed without user confirmation.

1. Execute
2. Verify with `SHOW AUTHENTICATION POLICIES ON ACCOUNT` or `SHOW AUTHENTICATION POLICIES ON USER <username>`
3. Provide revert instructions.
4. **⚠️ You MUST immediately proceed to Step 4 in SKILL.md.** Do not offer follow-up suggestions, do not ad-lib. Step 4 handles what comes next.

---

## Detach Flow

### D5: Select Detach Scope

```python
AskUserQuestion(
    questions=[{
        "question": "Where should the policy be detached from?",
        "header": "Detach Scope",
        "multiSelect": false,
        "options": [
            {"label": "Account — all users", "description": "Remove the account-wide authentication policy"},
            {"label": "Account — person users", "description": "Remove the policy for all person users"},
            {"label": "Account — service users", "description": "Remove the policy for all service users"},
            {"label": "Specific user", "description": "Remove policy from a named user"}
        ]
    }]
)
```

**If "Specific user":** Ask for the username.

### D6: Execute (Detach)

**⚠️ Role / context persistence (re-read `SKILL.md` Step 2 → Role Persistence Rule):**
The detach statement below MUST be submitted alongside `USE ROLE <chosen_role>;` as a **single multi-statement `sql_execute` call**.

⚠️ MANDATORY CHECKPOINT: Present the SQL to the user. Ask for approval once as selectable menu (Yes/No). When the user approves, execute immediately.

**Detach from account:**
```sql
USE ROLE <chosen_role>;
ALTER ACCOUNT UNSET AUTHENTICATION POLICY
  [ FOR ALL PERSON USERS | FOR ALL SERVICE USERS ];
```

**Detach from user:**
```sql
USE ROLE <chosen_role>;
ALTER USER <username> UNSET AUTHENTICATION POLICY;
```

Execute the command. **If it fails** with "no policy set" or similar, inform the user that no policy was attached at that level — no action needed.

**⚠️ AFTER EXECUTION (success or failure): You MUST immediately proceed to Step 4 in SKILL.md.** Do not offer follow-up suggestions, do not ask about other scopes, do not ad-lib. Step 4 handles what comes next.
