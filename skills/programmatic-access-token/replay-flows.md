#### Replay Flow (Minimal Approvals)

> **🚨 GOAL:** Replay is for less technical users who trust the setup. Minimize friction.
> Cortex Code constructs summary from manifest, runs `--dry-run` to show full SQL preview, gets ONE confirmation, then executes.
> **🔴 CRITICAL:** Even in replay flow, user MUST see the full SQL preview before confirmation. NEVER skip dry-run output.

**Trigger phrases:** "replay pat", "replay pat manifest", "recreate pat", "replay from manifest", "setup from manifest URL", "replay from URL", "use manifest from `<url>`"

> **📍 Manifest Location:** `.sfutils/manifest.toml` (in current working directory)

**IMPORTANT:** This is the **programmatic-access-token** skill. Only replay PAT entries from `[pat.*]` sections. If manifest contains other skills, ignore them.

**If user asks to replay/recreate from manifest:**

0. **Run Manifest Gate FIRST (non-negotiable):**

   ```bash
   <SKILL_DIR>/pat validate-manifest
   ```

   - Passes → continue
   - Fails → `<SKILL_DIR>/pat validate-manifest --fix` → re-validate
   - Still fails → **STOP**, show errors, do not proceed
   - `[snowflake].connection` empty after fix → run Step 1 (connection picker) first

   > **Why first:** Replay reads `[pat.*]` entries to reconstruct commands. A partial manifest produces wrong SQL.

0. **Remote Manifest URL Detection (if user provides a URL):**

   If the user provides a URL (in their prompt or pasted), detect and normalize it **before** local manifest detection:

   **Supported URL patterns and translation rules:**
   - **GitHub blob:** `https://github.com/{owner}/{repo}/blob/{branch}/{path}` → replace host with `raw.githubusercontent.com` and remove `/blob/` segment
   - **GitHub raw:** `https://raw.githubusercontent.com/...` → use as-is
   - **GitHub gist:** `https://gist.github.com/{user}/{id}` → append `/raw` if not already present
   - **Any other HTTPS URL ending in `.md`** → use as-is

   **After translating, show user and confirm:**

   ```
   Found manifest URL. Download URL:
     <translated_raw_url>

   Download to current directory as <filename>? [yes/no]
   ```

   **⚠️ STOP**: Wait for user confirmation.

   **If yes:**

   ```bash
   curl -fSL -o <filename> "<translated_raw_url>"
   ```

   > **Filename derivation:** Extract the filename from the URL path (e.g., `pat-demo-manifest.md`). If the file already exists locally, ask user: overwrite / rename / cancel.

   **If no:** Stop.

   After successful download, continue with step 1 below — the downloaded file will be picked up by the `*-manifest.md` glob.

1. **Detect manifest(s) in current directory:**

   ```bash
   WORKING_MANIFEST=""
   SHARED_MANIFEST=""
   SHARED_MANIFEST_FILE=""

   [ -f .sfutils/manifest.toml ] && WORKING_MANIFEST="EXISTS" && \
     WORKING_STATUS=$(cat .sfutils/manifest.toml | grep "^status" | head -1 | cut -d'"' -f2) && \
     echo "Working manifest: Status=${WORKING_STATUS}"

   for f in *-manifest.md; do
     [ -f "$f" ] && grep -q "## shared_info\|CORTEX_CODE_INSTRUCTION" "$f" 2>/dev/null && \
       SHARED_MANIFEST="EXISTS" && SHARED_MANIFEST_FILE="$f" && echo "Shared manifest: $f"
   done
   ```

   **If BOTH exist, ask user:**

   ```
   ⚠️ Found two manifests:
     1. Working manifest: .sfutils/manifest.toml (Status: <WORKING_STATUS>)
     2. Shared manifest: <SHARED_MANIFEST_FILE>

   Which should we use for PAT replay?
     A. Resume working manifest
     B. Start fresh from shared manifest (adapt values for your account)
     C. Cancel
   ```

   **⚠️ STOP**: Wait for user choice.

   | Choice | Action |
   |--------|--------|
   | **A** | Use working manifest → step 2 |
   | **B** | `mkdir -p .sfutils && chmod 700 .sfutils`, backup working to `.bak`, copy shared to `.sfutils/sfutils-manifest.md` → step 1b |
   | **C** | Stop. |

   **If ONLY shared manifest:** `mkdir -p .sfutils && chmod 700 .sfutils`, then copy to `.sfutils/sfutils-manifest.md` → step 1b.
   **If ONLY working manifest:** Go to step 2.

1b. **Shared manifest adapt-check (ALWAYS run for shared manifests):**

   ```bash
   IS_SHARED=$(grep -c "## shared_info\|CORTEX_CODE_INSTRUCTION" .sfutils/sfutils-manifest.md 2>/dev/null)
   if [ "$IS_SHARED" -gt 0 ]; then
     ADAPT_COUNT=$(grep -c "# ADAPT:" .sfutils/sfutils-manifest.md 2>/dev/null)
     echo "Shared manifest detected. ADAPT markers: ${ADAPT_COUNT}"
   fi
   ```

   **If `ADAPT_COUNT` > 0:** Extract `shared_by` from `## shared_info`, get current user's `SNOWFLAKE_USER`, show adaptation screen for PAT values (SA_USER, SA_ROLE, SFUTILS_DB). Three options: Accept adapted / Edit specific / Keep originals. Apply to manifest.

   **If `ADAPT_COUNT` = 0:** No markers, proceed with values as-is.

2. **Read manifest and find PAT entries:**

   ```bash
   <SKILL_DIR>/pat list
   ```

   - If no `[pat.*]` entries shown: "No PAT resources in manifest. Nothing to replay."
   - If entries shown: present table to user (label, sa_user, status), ask which label to replay
   - Proceed with selected `{LABEL}`

3. **Ensure connection is set (if returning to existing project):**

   ```bash
   cat .sfutils/manifest.toml | grep "^connection" | head -1
   ```

   **If `connection` is already set:** use it — no action needed.

   **If empty:** run connection picker:

   ```bash
   snow connection list --format json
   ```

   Present connections via `ask_user_question` (pre-select default), then:

   ```bash
   <SKILL_DIR>/pat setup-connection -c <chosen_connection>
   ```

   **⚠️ STOP**: Wait for connection selection.

4. **Check PAT status from manifest:**

   Read `[pat.{LABEL}].status` from `manifest.toml`:

   ```bash
   cat .sfutils/manifest.toml | grep -A1 "^\[pat\.{LABEL}\]" | grep "^status"
   ```

| Status | Action |
|--------|--------|
| `REMOVED` | Proceed to step 6 (resources don't exist) |
| `COMPLETE` | **Collision detected** — proceed to step 5 |
| `IN_PROGRESS` | Use Resume Flow instead (partial creation) |

5. **If Status is `COMPLETE` — Collision Strategy:**

   Resources already exist. Check which ones:

   ```bash
   snow sql -q "DESC USER {SA_USER}" 2>/dev/null && echo "EXISTS" || echo "NOT_FOUND"
   ```

   Show collision prompt:

   ```
   ⚠️ PAT resources already exist:

     Resource                    Status
     ─────────────────────────────────────
     Service User:   {SA_USER}                  EXISTS
     Network Rule:   {SA_USER}_NETWORK_RULE     EXISTS
     Network Policy: {SA_USER}_NETWORK_POLICY   EXISTS

   Choose a strategy:
   1. Use existing → skip resource creation, generate fresh PAT only
   2. Replace → run 'remove' then recreate all (DESTRUCTIVE)
   3. Rename → prompt for new service user name, create alongside existing
   4. Cancel → stop replay
   ```

   **⚠️ STOP**: Wait for user choice.

   | Choice | Action |
   |--------|--------|
   | **Use existing** | Skip to PAT generation only (SA_PAT is always fresh). Update `.env` with existing values. |
   | **Replace** | Confirm with "Type 'yes, destroy' to confirm". Run Remove Flow, then proceed to step 6. |
   | **Rename** | Ask for new `SA_USER` name. Derive new resource names per naming convention. Update `.env` and proceed to step 6. |
   | **Cancel** | Stop replay. |

6. **Run dry-run to show full SQL preview:**

   Read expiry values from `[pat.{LABEL}]` in manifest.toml, then:

   ```bash
   <SKILL_DIR>/pat \
     --profile {LABEL} \
     create --db {SFUTILS_DB} \
     --default-expiry-days {DEFAULT_EXPIRY} --max-expiry-days {MAX_EXPIRY} --dry-run
   ```

   **🔴 CRITICAL:** Follow the [Dry-Run Output Rule](cli-reference.md#dry-run-output-rule) — capture and paste the full output into your response.

   Then ask:

   ```
   Proceed with creation? [yes/no]
   ```

   **⚠️ STOP**: Wait for user confirmation.

7. **On "yes":** Execute (ONE bash approval, NO further prompts):

   ```bash
   <SKILL_DIR>/pat \
     --profile {LABEL} \
     create --db {SFUTILS_DB} \
     --default-expiry-days {DEFAULT_EXPIRY} --max-expiry-days {MAX_EXPIRY} --yes
   ```

   > **⚠️ CRITICAL:** ALWAYS include `--yes` (the CLI prompts interactively which does not work in Cortex Code).
   > ALWAYS include `--default-expiry-days` and `--max-expiry-days` using values from the manifest.
   > NEVER omit these flags or use alternative parameter names. If manifest values are missing, use `--default-expiry-days 7 --max-expiry-days 30`.
   > PAT is stored automatically in OS keyring — no additional secret storage step needed.

   - CLI shows progress for each step automatically
   - **NO additional user prompts until complete**

8. **Verify connection (MANDATORY -- do NOT skip, even in replay):**

   ```bash
   <SKILL_DIR>/pat verify --user {SA_USER} --role {SA_ROLE}
   ```

   > PAT is loaded from the OS keyring automatically. If verify fails, stop and present error.

9. **Update manifest** status back to `COMPLETE` after successful creation and verification

#### Resume Flow (Partial Creation Recovery)

**If manifest shows Status: IN_PROGRESS:**

1. **Read which resources have status `DONE`** (already created)
2. **Display resume info:**

```

ℹ️  Resuming from partial creation:

  DONE: Network Rule
  DONE: Network Policy

- Auth Policy:    PENDING
- Service User:   PENDING
- PAT:            PENDING

Continue from Auth Policy creation? [yes/no]

```

1. **On "yes":** Continue from first `PENDING` resource
2. **Update manifest** as each remaining resource is created

**Display success summary to user:**

```

PAT Setup Complete!

Resources Created:
  User:           {SA_USER}
  Role:           {SA_ROLE}
  Network Rule:   {SFUTILS_DB}.NETWORKS.{SA_USER}_NETWORK_RULE
  Network Policy: {SA_USER}_NETWORK_POLICY
  Auth Policy:    {SFUTILS_DB}.POLICIES.{SA_USER}_AUTH_POLICY
  PAT:            stored in OS keyring

Manifest updated: .sfutils/manifest.toml

```

## Replay All Flow (Multi-Skill Sequential)

**Trigger phrases:** "replay all manifests", "replay all sfutils", "recreate all from manifest"

> **Purpose:** Replay ALL skills from manifest in timestamp order. Safer than individual replay when dependencies exist.

**If user asks to replay all:**

0. **Run Manifest Gate first** (same as single replay — see step 0 above). Gate must pass before proceeding.

1. **Read manifest and list all PAT entries:**

   ```bash
   <SKILL_DIR>/pat list
   sfutils-pat validate-manifest
   cat .sfutils/manifest.toml
   ```

3. **Extract Created timestamp from each section** and sort ascending:

   ```
   Found 3 skill sections:
   
   | # | Skill | Created | Status |
   |---|-------|---------|--------|
   | 1 | sfutils-networks | 2026-02-04T14:30:00 | REMOVED |
   | 2 | programmatic-access-token | 2026-02-04T14:35:00 | REMOVED |
   | 3 | sfutils-extvolumes | 2026-02-04T15:00:00 | REMOVED |
   ```

4. **Check all statuses:**
   - If ANY section has `Status: COMPLETE`: Warn user which skills already exist
   - If ANY section has `Status: IN_PROGRESS`: Warn user to resume that skill first
   - Only proceed if ALL sections have `Status: REMOVED`

5. **Display replay plan with single confirmation:**

```
ℹ️  Replay All will recreate resources in original order:

  1. Networks (2026-02-04T14:30:00)
     → Network Rule, Network Policy
  
  2. PAT (2026-02-04T14:35:00)
     → Service User, Auth Policy, PAT
  
  3. Volumes (2026-02-04T15:00:00)
     → S3 Bucket, IAM Role, External Volume

Proceed with sequential creation? [yes/no]
```

1. **On "yes":** Execute each skill's replay in order:

   **For each skill in timestamp order:**
   - Extract values from that skill's manifest section
   - Execute the appropriate create command
   - Update that section's status to `COMPLETE`
   - If ANY skill fails: STOP immediately, report which skill failed
   - Do NOT continue to next skill on failure

2. **On completion:** Display summary:

```
✅ Replay All Complete!

  ✓ Networks:  COMPLETE
  ✓ PAT:       COMPLETE  
  ✓ Volumes:   COMPLETE

All resources recreated successfully.
```

**On failure:**

```
❌ Replay All Failed at: PAT

  ✓ Networks:  COMPLETE (rolled back: NO)
  ✗ PAT:       FAILED - <error message>
  - Volumes:   SKIPPED

Fix the PAT issue, then run "replay all" again to continue.
```

