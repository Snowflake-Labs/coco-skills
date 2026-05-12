## Export for Sharing Flow

**Trigger phrases:** "export manifest for sharing"

**Purpose:** Create a portable copy of the manifest for another developer.

**Summary:**

1. Verify ALL volumes in `manifest.toml` have `status = "COMPLETE"`
2. Read `project_name` from manifest root
3. Ask user for export location (default: project root)
4. Create `{project_name}-manifest.toml` with:
   - All statuses set to `REMOVED`
   - Connection fields blanked (connection is per-account)
   - Comment at top: `# Shared manifest — run 'vol migrate' or 'vol setup-connection' before replaying`

---

## Replay Flow (Minimal Approvals)

> **🚨 GOAL:** Replay is for less technical users who trust the setup. Minimize friction.
> Cortex Code constructs summary from manifest, runs `--dry-run` to show full SQL/JSON preview, gets ONE confirmation, then executes.
> **🔴 CRITICAL:** Even in replay flow, user MUST see the full SQL/JSON preview before confirmation. NEVER skip dry-run output.

**Trigger phrases:** "replay manifest", "replay volumes", "recreate external volume", "replay from manifest", "setup from manifest URL", "replay from URL", "use manifest from `<url>`"

> **📍 Manifest Location:** `.sfutils/manifest.toml` (TOML format — current)
> Legacy `.sfutils/sfutils-manifest.md` requires `vol migrate` first.

**IMPORTANT:** This is the **external-volume** skill. Only replay volumes from `manifest.toml [volume.*]` entries.

**If user asks to replay/recreate from manifest:**

0. **Remote Manifest URL Detection (if user provides a URL):**

   If the user provides a URL, detect and normalize it **before** local manifest detection:

   **Supported URL patterns:**
   - **GitHub blob:** `https://github.com/{owner}/{repo}/blob/{branch}/{path}` → replace host with `raw.githubusercontent.com` and remove `/blob/` segment
   - **GitHub raw:** `https://raw.githubusercontent.com/...` → use as-is
   - **Any other HTTPS URL ending in `.toml`** → use as-is

   **After translating, show user and confirm:**

   ```
   Found manifest URL. Download URL:
     <translated_raw_url>

   Download to current directory as <filename>? [yes/no]
   ```

   **⚠️ STOP**: Wait for user confirmation.

   ```bash
   curl -fSL -o <filename> "<translated_raw_url>"
   ```

   After download, continue with step 1.

1. **Detect manifest(s) in current directory:**

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
   | TOML manifest found | Run Volume Manifest Gate → proceed to step 2 |
   | Legacy markdown found, no TOML | Run `vol migrate` first, then proceed |
   | Shared `.toml` file downloaded | `mkdir -p .sfutils && cp <shared>.toml .sfutils/manifest.toml` → run `vol setup-connection` → proceed |
   | Nothing found | "No manifest found. Run 'vol setup-connection' to start a new project." |

   **If BOTH TOML and a shared file exist, ask user:**

   ```
   ⚠️ Found two manifests:
     1. Working manifest: .sfutils/manifest.toml
     2. Shared manifest: <shared_file>

   Which should we use for volumes replay?
     A. Resume working manifest
     B. Start fresh from shared manifest (will replace working manifest)
     C. Cancel
   ```

   **⚠️ STOP**: Wait for user choice.

2. **Run Volume Manifest Gate:**

   ```bash
   <SKILL_DIR>/vol validate-manifest
   ```

   If fails → `vol validate-manifest --fix`. If still failing → stop and show errors.

3. **Check connection:**

   ```bash
   cat .sfutils/manifest.toml | grep "^connection"
   ```

   **If connection is empty:** Ask user to select from `snow connection list`, then:

   ```bash
   <SKILL_DIR>/vol setup-connection -c <chosen_connection>
   ```

   **If connection is set:** proceed.

4. **Read volumes from manifest:**

   ```bash
   <SKILL_DIR>/vol list
   ```

   Shows all volumes with label / volume_name / storage_type / status.

   - If no volumes found: "No volume entries in manifest.toml. Nothing to replay."
   - If volumes found: continue to step 5.

5. **For each volume with `status = "REMOVED"`:**

   Read values from the manifest entry:

   ```bash
   # Read volume details from manifest
   cat .sfutils/manifest.toml
   ```

   Extract: `volume_name`, `bucket_url`, `aws_region`, `aws_profile`, `storage_aws_role_arn`.
   Derive bucket base name from `bucket_url` (e.g. `s3://prefix-bucket` → bucket = `prefix-bucket`).

   **Collision check — if `status = "COMPLETE"`:**

   ```
   ⚠️ External volume resources already exist:

     Resource                    Status
     ─────────────────────────────────────
     External Volume: {VOLUME_NAME}   COMPLETE
     S3 Bucket:       {BUCKET}        EXISTS

   Choose a strategy:
   1. Use existing → skip creation, continue
   2. Replace → run 'delete' then recreate (DESTRUCTIVE)
   3. Rename → prompt for new volume name, create alongside existing
   4. Cancel → stop replay
   ```

   **⚠️ STOP**: Wait for user choice.

   | Choice | Action |
   |--------|--------|
   | **Use existing** | Skip volume creation. |
   | **Replace** | Confirm "Type 'yes, destroy' to confirm". Run delete flow, then proceed to step 6. |
   | **Rename** | Ask for new volume name. Pass `--volume-name <new>` to `vol create`. |
   | **Cancel** | Stop replay. |

6. **Run dry-run to show full SQL/JSON preview:**

   ```bash
   <SKILL_DIR>/vol \
     --region <AWS_REGION> \
     create --bucket <BUCKET> --dry-run
   ```

   Add `--aws-profile <PROFILE>` if `aws_profile` is set in manifest.

   **🔴 CRITICAL:** Paste the ENTIRE dry-run output into your response using language-tagged code blocks.

   Then ask:

   ```
   Proceed with creation? [yes/no]
   ```

   **⚠️ STOP**: Wait for user confirmation.

7. **On "yes":** Run actual command:

   ```bash
   <SKILL_DIR>/vol \
     --region <AWS_REGION> \
     create --bucket <BUCKET> --output json
   ```

   The CLI automatically updates `manifest.toml` with `status = "COMPLETE"` and volume details.

8. **Verify (MANDATORY — do NOT skip):**

   ```bash
   <SKILL_DIR>/vol verify --volume-name <VOLUME_NAME>
   ```

   Wait for IAM propagation (up to 60s). Retry once after 30s if fails.

9. **Confirm manifest is well-formed:**

   ```bash
   <SKILL_DIR>/vol validate-manifest
   ```

---

## Replay All Flow (Multi-Skill Sequential)

**Trigger phrases:** "replay all manifests", "replay all sfutils", "recreate all from manifest"

> **Purpose:** Replay ALL volumes from `manifest.toml` in created_at order.

**If user asks to replay all:**

1. **Read manifest:**

   ```bash
   <SKILL_DIR>/vol list
   ```

2. **Find ALL volumes** in `manifest.toml [volume.*]` sections.

3. **Sort by `created_at` timestamp ascending.**

   Display replay plan:

   ```
   Found 2 volume(s) in manifest.toml:

   | # | LABEL | VOLUME_NAME | TYPE | STATUS |
   |---|-------|-------------|------|--------|
   | 1 | my-s3-volume | MY_S3_VOLUME | s3 | REMOVED |
   | 2 | my-second-volume | MY_SECOND_VOLUME | s3 | REMOVED |
   ```

4. **Check statuses:**
   - If ANY volume has `status = "COMPLETE"`: Warn user which volumes already exist
   - Only proceed if all target volumes have `status = "REMOVED"` or "IN_PROGRESS"

5. **Display replay plan with single confirmation:**

   ```
   ℹ️  Replay All will recreate volumes in created_at order:

     1. my-s3-volume → MY_S3_VOLUME (s3)
     2. my-second-volume → MY_SECOND_VOLUME (s3)

   Proceed with sequential creation? [yes/no]
   ```

6. **On "yes":** Execute each volume's replay in order (steps 6-8 above for each).

   - If ANY volume fails: STOP immediately, report which failed
   - Do NOT continue to next volume on failure

7. **On completion:**

   ```
   ✅ Replay All Complete!

     ✓ my-s3-volume:    COMPLETE
     ✓ my-second-volume: COMPLETE

   All volumes recreated successfully.
   ```

**On failure:**

```
❌ Replay All Failed at: my-second-volume

  ✓ my-s3-volume:     COMPLETE
  ✗ my-second-volume: FAILED - <error message>

Fix the issue, then run "replay all" again to continue.
```
