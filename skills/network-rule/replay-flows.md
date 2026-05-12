# Replay Flows

> **📍 Manifest Location:** `.sfutils/manifest.toml` (TOML format — source of truth for all rule state)
>
> **🔗 Connection:** Read automatically from `manifest.toml [snowflake].connection`. No `.env` sourcing needed.

---

## Replay Single Skill Flow

**Trigger phrases:** "replay network rules", "recreate rule from manifest", "replay manifest", "setup again from manifest"

> **Purpose:** Recreate network rule resources from an existing `manifest.toml`.

### Steps

1. **Detect manifest state:**

   ```bash
   # Check for TOML manifest (current format)
   ls .sfutils/manifest.toml 2>/dev/null && echo "TOML_FOUND" || echo "NO_TOML"

   # Check for legacy markdown manifest
   ls .sfutils/sfutils-manifest.md 2>/dev/null && echo "LEGACY_FOUND" || echo "NO_LEGACY"

   # Check for shared manifests
   ls *-manifest.toml 2>/dev/null || true
   ```

   **Decision tree:**

   | State | Action |
   |-------|--------|
   | TOML manifest found | Run Network Manifest Gate → proceed to step 2 |
   | Legacy markdown found, no TOML | Run `nw migrate` first, then proceed |
   | Shared `.toml` file downloaded | `mkdir -p .sfutils && cp <shared>.toml .sfutils/manifest.toml` → run `nw setup-connection` → proceed |
   | Nothing found | "No manifest found. Run 'nw setup-connection' to start a new project." |

   **If BOTH TOML and a shared file exist, ask user:**

   ```
   ⚠️ Found two manifests:
     1. Working manifest: .sfutils/manifest.toml
     2. Shared manifest: <shared_file>

   Which should we use for replay?
     A. Resume working manifest
     B. Start fresh from shared manifest (will replace working manifest)
     C. Cancel
   ```

   **⚠️ STOP**: Wait for user choice.

2. **Run Network Manifest Gate:**

   ```bash
   <SKILL_DIR>/nw validate-manifest
   ```

   If fails → `nw validate-manifest --fix`. If still failing → stop and show errors.

3. **Check connection:**

   ```bash
   cat .sfutils/manifest.toml | grep "^connection"
   ```

   **If connection is empty:** Ask user to select from `snow connection list`, then:

   ```bash
   <SKILL_DIR>/nw setup-connection -c <chosen_connection>
   ```

   **If connection is set:** proceed.

4. **Read rules from manifest:**

   ```bash
   <SKILL_DIR>/nw list
   ```

   Shows all rules with label / rule_name / mode / type / status.

   - If no rules found: "No rule entries in manifest.toml. Nothing to replay."
   - If rules found: continue to step 5.

5. **For each rule with `status = "REMOVED"` or `"CREATE_IN_PROGRESS"`:**

   Read values from the manifest entry:

   ```bash
   cat .sfutils/manifest.toml
   ```

   Extract: `rule_name`, `rule_mode`, `rule_type`, `value_list`, `policy_name`, `sf_utils_db`, `admin_role`, `allow_github`, `allow_google`.

   **Collision check — if `status = "COMPLETE"`:**

   ```
   ⚠️ Network rule resources already exist:

     Resource          Status
     ────────────────────────────────
     Rule:   {RULE_NAME}    COMPLETE
     Policy: {POLICY_NAME}  COMPLETE

   Choose a strategy:
   1. Use existing → skip creation, continue
   2. Replace → run 'delete' then recreate (DESTRUCTIVE)
   3. Rename → prompt for new rule name, create alongside existing
   4. Cancel → stop replay
   ```

   **⚠️ STOP**: Wait for user choice.

   | Choice | Action |
   |--------|--------|
   | **Use existing** | Skip rule creation. Run `nw validate-manifest` to confirm. |
   | **Replace** | Confirm "Are you sure?". Run `nw rule delete --name {RULE_NAME} --db {DB} --yes`, then proceed to step 6. |
   | **Rename** | Ask for new rule name. Use new name in step 6. |
   | **Cancel** | Stop replay. |

6. **Run dry-run to show full SQL preview:**

   Build the create command from manifest values:

   ```bash
   <SKILL_DIR>/nw rule create \
     --name <RULE_NAME> --db <SF_UTILS_DB> \
     [--allow-local] [--allow-gh] [--allow-google] \
     [--values "<value_list_csv>"] \
     [--mode <rule_mode>] [--type <rule_type>] \
     [--policy <POLICY_NAME>] --dry-run
   ```

   **🔴 CRITICAL:** Paste the ENTIRE dry-run output into your response.

   Then ask:
   ```
   Proceed with rule recreation? [yes/no]
   ```

   **⚠️ STOP**: Wait for user confirmation.

7. **On "yes":** Run actual command with `--yes`:

   ```bash
   <SKILL_DIR>/nw rule create \
     --name <RULE_NAME> --db <SF_UTILS_DB> \
     [--allow-local] [--allow-gh] [--allow-google] \
     [--values "<value_list_csv>"] \
     [--mode <rule_mode>] [--type <rule_type>] \
     [--policy <POLICY_NAME>] --yes
   ```

   The CLI automatically updates `manifest.toml` with `status = "COMPLETE"` and resource details.

8. **Confirm manifest is well-formed:**

   ```bash
   <SKILL_DIR>/nw validate-manifest
   <SKILL_DIR>/nw list
   ```

---

## Replay All Flow (Multiple Rules)

**Trigger phrases:** "replay all rules", "recreate all network rules from manifest"

> **Purpose:** Replay ALL rules from `manifest.toml` in `created_at` order.

1. **Read manifest:**

   ```bash
   <SKILL_DIR>/nw list
   ```

2. **Find ALL rules** in `manifest.toml [rule.*]` sections. Sort by `created_at` ascending.

   Display replay plan:

   ```
   Found 2 rule(s) in manifest.toml:

   | # | LABEL | RULE_NAME | MODE | TYPE | STATUS |
   |---|-------|-----------|------|------|--------|
   | 1 | my-ingress | MY_INGRESS_RULE | INGRESS | IPV4 | REMOVED |
   | 2 | my-egress | MY_EGRESS_RULE | EGRESS | HOST_PORT | REMOVED |
   ```

3. **Check statuses:**
   - If ANY rule has `status = "COMPLETE"`: Warn user which rules already exist
   - Only proceed if all target rules have `status = "REMOVED"` or `"CREATE_IN_PROGRESS"`

4. **Single confirmation:**

   ```
   ℹ️  Replay All will recreate rules in created_at order:

     1. my-ingress → MY_INGRESS_RULE (INGRESS/IPV4)
     2. my-egress  → MY_EGRESS_RULE (EGRESS/HOST_PORT)

   Proceed with sequential creation? [yes/no]
   ```

5. **On "yes":** Execute each rule's replay in order (steps 6-8 above for each).

   - If ANY rule fails: STOP immediately, report which failed
   - Do NOT continue to next rule on failure

6. **On completion:**

   ```
   ✅ Replay All Complete!

     ✓ my-ingress: COMPLETE
     ✓ my-egress:  COMPLETE

   All rules recreated successfully.
   ```

**On failure:**

```
❌ Replay All Failed at: my-egress

  ✓ my-ingress: COMPLETE
  ✗ my-egress:  FAILED - <error message>

Fix the issue, then run "replay all" again to continue.
```
