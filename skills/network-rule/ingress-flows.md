# INGRESS Flows

> **Prerequisite:** Complete SKILL.md Steps 0–2 (manifest, connection, infra) before this flow.
>
> **Scope:** INGRESS, POSTGRES_INGRESS, and INTERNAL_STAGE rules — controls who connects
> TO Snowflake (IP allowlists, VPC endpoints, GitHub Actions, PostgreSQL interface).
>
> **Not for EGRESS HOST_PORT.** Those use External Access Integrations → [egress-flows.md](egress-flows.md).

---

### Step 2b: Multi-Rule Selection

**Check if rules already exist in manifest:**

```bash
<SKILL_DIR>/nw list
```

**If no rules exist:** Continue to Step 3 (new rule).

**If rules exist**, use `ask_user_question`:

```
Existing network rules found (see table above).
What would you like to do?
- Add a new rule
- Manage an existing rule (update/remove)
```

- **Add new rule:** Continue to Step 3.
- **Manage existing:** Identify rule by label/name → update → Step 4; remove → [Remove Flow](manifest-flows.md).

---

### Step 3: Gather Requirements

Read existing values from manifest for suggestions:

```bash
cat .sfutils/manifest.toml 2>/dev/null | grep -E "sf_utils_db|user|admin_role"
```

**Prompt for rule configuration:**

| Value | Semantic Default | Prompt |
|-------|-----------------|--------|
| Rule name | `{USER}_LOCAL_ACCESS` | "Rule name:" |
| Database | `sf_utils_db` from manifest | "Database for network objects [use {sf_utils_db}?]:" |
| Schema | `NETWORKS` | "Schema [default: NETWORKS]:" |

**Part 1b–2: Rule mode, type, and value configuration.** See [Mode & Type Reference](mode-type-reference.md) for the full mode selection UI, mode-type compatibility matrix, and value input prompts.

**⚠️ STOP**: Wait for user input on ALL values.

---

### Step 3b: Policy Selection

> **Mode guard:**
> This step applies to **INGRESS**, **POSTGRES_INGRESS**, and **INTERNAL_STAGE** rules, AND to
> **EGRESS + Network Policy** rules (egress-flows.md Section 2).
>
> **Skip this step only when the EAI path was chosen** for an EGRESS HOST_PORT rule — the
> ownership gate was already answered in Step E3 of [egress-flows.md](egress-flows.md).
> Each rule independently answers "new or existing": the EAI answer for one rule does NOT
> carry over to the policy question for another rule.

After gathering INGRESS rule configuration, ask whether the user wants a network policy.
**Show cleanup semantics for each option** — the user's choice determines `operation` in the manifest:

```
Do you want to add this rule to a network policy?

● Create new policy  [operation: CREATED]
  A new dedicated policy for this rule.
  Cleanup: this policy will be DROPPED when the rule is deleted.

○ Add to existing policy  [operation: ALTERED]
  Append to a policy that already exists in your account.
  Cleanup: only this rule is removed from the policy — the policy itself is preserved.

○ No policy (rule only)
  Create the rule now, attach to a policy later.
```

**If "Create new policy":**
- Default name: derive from rule name (e.g. `KAMESHS_APP_INGRESS_RULE` → `KAMESHS_APP_INGRESS_POLICY`)
- Ask: "Policy name [default: {derived_name}]:" — allow override
- CLI flags: `--policy <name>` (default `--policy-mode create`)

**If "Add to existing policy":**

Discover all account-level policies:

```bash
<SKILL_DIR>/nw policy list --output json
```

Parse JSON array → populate `ask_user_question` options (up to 5 names + "Something else" auto-added).

If 0 results → skip this option, fall back to "Create new policy".

CLI flags: `--policy <existing_policy_name> --policy-mode alter`

**If "No policy":**
- Omit `--policy` entirely
- Note: `--allow-gh` (managed GitHub rule) requires a policy — if user selected GitHub SaaS rule, go back to "Create new policy"

> **Multi-rule projects (INGRESS rules only):** If multiple INGRESS rules should share
> one network policy, use "Add to existing policy" when creating the second rule.
>
> ⚠️ **EGRESS HOST_PORT rules use External Access Integrations (EAI), not network policies.**
> EAI rules and INGRESS rules cannot be combined under one network policy. They are different
> Snowflake object types for different purposes. See [egress-flows.md](egress-flows.md).

---

### Step 3a: Check for Existing Network Rule

```bash
<SKILL_DIR>/nw validate-manifest 2>&1 | grep -i <RULE_NAME> || true
```

Also check Snowflake directly:

```bash
<SKILL_DIR>/nw rule list --db <NW_RULE_DB>
```

**If rule already exists**, use `ask_user_question`:

| Option | Action |
|--------|--------|
| Update existing | Use `nw rule update` — modifies IPs, keeps policy intact |
| Remove and recreate | Use `nw rule delete --yes` then `rule create` |
| Cancel | Stop workflow |

**If no rule exists:** Continue to Step 4.

---

### Step 4: Dry-Run Preview

```bash
<SKILL_DIR>/nw \
  rule create --name <NW_RULE_NAME> --db <NW_RULE_DB> \
  [--allow-local] [--allow-gh] [--allow-google] [--values <CIDRs>] \
  [--policy <POLICY_NAME>] --dry-run
```

**🔴 CRITICAL: Run the CLI dry-run, capture its output, and present it IN YOUR RESPONSE.**

> 🔄 **On pause/resume:** Re-run `--dry-run` and paste the complete output again before asking for confirmation.

**⚠️ STOP**: Wait for explicit user approval ("yes", "ok", "proceed") before creating resources.

---

### Step 5: Create Resources

**Execute (ALWAYS include `--yes`):**

```bash
<SKILL_DIR>/nw \
  rule create --name <NW_RULE_NAME> --db <NW_RULE_DB> \
  [--allow-local] [--allow-gh] [--allow-google] [--values <CIDRs>] \
  [--policy <POLICY_NAME>] --output json --yes
```

**If GitHub SaaS rule was chosen** (`allow_github = true`):

```bash
<SKILL_DIR>/nw rule create --name <NW_RULE_NAME> --db <NW_RULE_DB> \
  --allow-gh --policy <POLICY_NAME> --yes
```

> `SNOWFLAKE.NETWORK_SECURITY.GITHUBACTIONS_GLOBAL` is stored as `allow_github = true` in the manifest — NOT in `value_list`.

**On success:**

1. CLI writes `[rule.<label>]` with `status = "COMPLETE"` to `manifest.toml`
2. Confirm:

   ```bash
   <SKILL_DIR>/nw validate-manifest
   ```

3. Show created resources summary.

---

### Step 6: Verify

```bash
<SKILL_DIR>/nw list
<SKILL_DIR>/nw rule list --db <NW_RULE_DB>
```

Confirm the rule appears with `status = COMPLETE` in the manifest list and in Snowflake.
