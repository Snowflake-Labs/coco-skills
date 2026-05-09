# Manifest Flows

The manifest file is `.sfutils/manifest.toml` — machine-managed by Cortex Code.

---

## TOML Manifest Format

### State lifecycle

```
Create: CREATE_IN_PROGRESS → COMPLETE
Remove: DELETE_IN_PROGRESS → REMOVED
```

Each `[rule.<label>]` tracks its own state. EAIs and Policies are first-class sections
that group rules — each with an `operation` field that drives cleanup semantics.

### Operation field

| `operation` | Meaning | Cleanup action |
|-------------|---------|----------------|
| `CREATED` | This project created the EAI/Policy | `DROP EAI` / `DROP POLICY` on cleanup |
| `ALTERED` | This project added a rule to an existing EAI/Policy | `ALTER EAI/POLICY REMOVE` the rule only |

### Template — current schema example

```toml
# Machine-managed by Cortex Code. Do not hand-edit.
schema_version = "1"
project_name   = "my-demo"
created_at     = "2026-05-02T10:00:00Z"

# ── Shared Snowflake connection ───────────────────────────────────────────────
[snowflake]
connection   = "local-oauth"
account      = "ABC12345"
user         = "KAMESHS"
account_url  = "https://abc12345.snowflakecomputing.com"
sf_utils_db  = "KAMESHS_SF_UTILS"
admin_role   = "ACCOUNTADMIN"

# ── Tool / infra pre-flight cache ────────────────────────────────────────────
[prereqs]
tools_verified = "2026-05-02"
infra_ready    = true

# ── External Access Integration ──────────────────────────────────────────────
[eai.kameshs-app-eai]
name       = "KAMESHS_APP_EAI"
status     = "COMPLETE"
operation  = "CREATED"           # CREATED → DROP on cleanup
created_at = "2026-05-02T10:15:00Z"
updated_at = "2026-05-02T10:20:00Z"
admin_role = "ACCOUNTADMIN"

[eai.kameshs-app-eai.rules]     # O(1) EAI → Rules index
kameshs-slack-egress  = "KAMESHS_SF_UTILS.NETWORKS.KAMESHS_SLACK_APP_EGRESS_RULE"
kameshs-openai-egress = "KAMESHS_SF_UTILS.NETWORKS.KAMESHS_OPENAI_EGRESS_RULE"

# ── Network Policy ────────────────────────────────────────────────────────────
[policy.kameshs-app-ingress-policy]
name       = "KAMESHS_APP_INGRESS_POLICY"
status     = "COMPLETE"
operation  = "ALTERED"           # ALTERED → remove rule only on cleanup (don't DROP policy)
created_at = "2026-05-02T10:10:00Z"
updated_at = "2026-05-02T10:10:00Z"
admin_role = "ACCOUNTADMIN"

[policy.kameshs-app-ingress-policy.rules]
kameshs-local-ingress = "KAMESHS_SF_UTILS.NETWORKS.KAMESHS_APP_INGRESS_RULE"

# ── Rule: Slack EGRESS (CREATE_IN_PROGRESS written before first SQL) ──────────
[rule.kameshs-slack-egress]
status     = "COMPLETE"
rule_name  = "KAMESHS_SLACK_APP_EGRESS_RULE"
rule_mode  = "EGRESS"
rule_type  = "HOST_PORT"
value_list = ["*.slack.com:443"]
eai        = "kameshs-app-eai"   # ← O(1) back-reference to parent EAI
sf_utils_db = "KAMESHS_SF_UTILS"
admin_role = "ACCOUNTADMIN"
created_at = "2026-05-02T10:15:00Z"
updated_at = "2026-05-02T10:15:00Z"

[rule.kameshs-slack-egress.cleanup]
rule_name = "KAMESHS_SLACK_APP_EGRESS_RULE"
db        = "KAMESHS_SF_UTILS"
# EAI cleanup driven by [eai.kameshs-app-eai].operation

# ── Rule: Local IP INGRESS ────────────────────────────────────────────────────
[rule.kameshs-local-ingress]
status     = "COMPLETE"
rule_name  = "KAMESHS_APP_INGRESS_RULE"
rule_mode  = "INGRESS"
rule_type  = "IPV4"
value_list = ["203.0.113.1/32", "198.51.100.0/24"]
policy     = "kameshs-app-ingress-policy"   # ← O(1) back-reference to parent policy
allow_github = false
allow_google = false
sf_utils_db  = "KAMESHS_SF_UTILS"
admin_role   = "ACCOUNTADMIN"
created_at   = "2026-05-02T10:10:00Z"
updated_at   = "2026-05-02T10:10:00Z"

[rule.kameshs-local-ingress.cleanup]
rule_name = "KAMESHS_APP_INGRESS_RULE"
db        = "KAMESHS_SF_UTILS"
# Policy cleanup driven by [policy.kameshs-app-ingress-policy].operation
# ALTERED → ALTER NETWORK POLICY REMOVE ALLOWED_NETWORK_RULE_LIST = (this rule)

# ── Standalone rule (no EAI or Policy) ───────────────────────────────────────
[rule.kameshs-internal-stage-rule]
status     = "COMPLETE"
rule_name  = "KAMESHS_INTERNAL_STAGE_RULE"
rule_mode  = "INTERNAL_STAGE"
rule_type  = "IPV4"
value_list = ["203.0.113.0/24"]
# no eai or policy field — standalone rule
sf_utils_db = "KAMESHS_SF_UTILS"
admin_role  = "ACCOUNTADMIN"
created_at  = "2026-05-02T10:05:00Z"
updated_at  = "2026-05-02T10:05:00Z"

[rule.kameshs-internal-stage-rule.cleanup]
rule_name = "KAMESHS_INTERNAL_STAGE_RULE"
db        = "KAMESHS_SF_UTILS"
```

### Progressive Write Flow

1. **Before first SQL** (after user approves): CLI writes rule `status = "CREATE_IN_PROGRESS"` with minimal fields.
2. **After EAI/Policy created**: CLI writes `[eai.<label>]` or `[policy.<label>]` with `operation = "CREATED"` or `"ALTERED"`.
3. **After rule confirmed**: CLI overwrites rule with full fields + `status = "COMPLETE"` + `eai`/`policy` back-reference.

### Automatic legacy promotion

When `ensure_manifest_defaults()` runs (on every `nw` invocation), any legacy
`integration_name` or `policy_name` fields in `[rule.*.cleanup]` are silently
promoted to top-level `[eai.*]` / `[policy.*]` sections. No manual migration step required.
`schema_version` stays `"1"` — the EAI/Policy sections are purely additive.


## Network Manifest Gate

**Run before any Remove, Replay, or Manage-Existing operation:**

```bash
<SKILL_DIR>/nw validate-manifest
```

If issues found:
```bash
<SKILL_DIR>/nw validate-manifest --fix
```

**STOP** if issues remain after `--fix`.

---

## Remove Flow (Manifest-Driven Cleanup)

> **🚨 CRITICAL: Cleanup MUST be driven by the manifest.**
>
> The manifest `[rule.<label>.cleanup]` section contains the exact resource names. NEVER construct cleanup SQL manually.
>
> **⚠️ DEPENDENCY ORDER:** Network policies reference network rules. ALWAYS drop the network policy FIRST, then the network rule. The CLI `rule delete` handles this automatically.

**On `remove` / `cleanup` / `delete` request:**

1. **Run Network Manifest Gate** (see above)

2. **Check manifest exists:**

   ```bash
   cat .sfutils/manifest.toml 2>/dev/null || echo "NOT_FOUND"
   ```

3. **If manifest NOT_FOUND:**
   - Inform user: "No manifest found. Cannot determine resources to clean up."
   - Ask: "Do you want to specify cleanup parameters manually?"
   - If yes, ask for rule name and database

4. **If manifest EXISTS:**
   - Run `<SKILL_DIR>/nw list` to show all rules
   - Identify rules with `status = COMPLETE` — only these are eligible for removal. Skip REMOVED rules silently.
   - Use `ask_user_question` with **`multiSelect: true`** so the user can select one or all COMPLETE rules in a single step.
   - If the user selects **fewer than all** COMPLETE rules, surface what will NOT be removed:
     ```
     The following rules will NOT be removed: {unselected list}
     Proceed with partial cleanup? [yes/no]
     ```
   - Read `[rule.<label>.cleanup]` for the exact resource names of each selected rule.

5. **Before executing, show user** (one blast-radius block per selected rule):

   ```
   🗑️  Cleanup from manifest:

   Will remove resources:
     Network Rule:   {rule_name}
     Network Policy: {policy_name}  (or EAI: {eai_name})
     Database:       {db}

   The CLI will:
     1. Write status = "DELETE_IN_PROGRESS" to manifest
     2. Drop network policy / remove rule from ALTERED policy
     3. Drop network rule
     4. Write status = "REMOVED" + removed_at to manifest

   Proceed? [yes/no]
   ```

6. **On confirmation:**

   ```bash
   <SKILL_DIR>/nw rule delete \
     --name {rule_name} --db {db} --yes
   ```

   Repeat steps 5–6 for each additional selected rule in sequence.

7. **The CLI automatically:**
   - Writes `status = "DELETE_IN_PROGRESS"` before any DROP
   - Drops policy (from `[cleanup].policy_name`), then rule
   - Writes `status = "REMOVED"` with `removed_at` after success

8. **Post-removal check (loop gate):**

   After all selected rules are removed, run:

   ```bash
   <SKILL_DIR>/nw list
   ```

   - **If COMPLETE rules remain** (rules the user did not select): inform the user:
     ```
     Remaining active rules: {list}
     Would you like to remove any of these?
     ```
     If yes → return to Step 4.
   - **If no COMPLETE rules remain**: report `"All rules cleaned up."` and stop.

> **Why manifest-driven?** The manifest captures exact resource names. Using CLI ensures proper dependency order, syntax, and error handling.

---

## Export for Sharing Flow

**Trigger phrases:** "export manifest for sharing"

**Purpose:** Create a portable copy of the manifest for another developer.

1. Verify all rules have `status = "COMPLETE"`
2. Read `project_name` from manifest
3. Ask user for export location (default: project root)
4. Create `{project_name}-manifest.toml.example` with:
   - All statuses set to `REMOVED`
   - Connection and account fields cleared
   - `infra_ready = false`
   - `# ADAPT:` markers on user-prefixed values

---

## Legacy Markdown Manifest (deprecated)

> ⚠️ **DEPRECATED** — projects created before May 2026 used `.sfutils/sfutils-manifest.md`.
> Run `<SKILL_DIR>/nw migrate` to convert to the TOML format.

The old markdown format used `<!-- START -- sfutils-networks:{NW_RULE_NAME} -->` / `<!-- END -->` markers with a resources table and cleanup instructions. The `nw migrate` command reads this file as the primary source and produces a valid `manifest.toml`.
