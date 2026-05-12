# S3 Workflow

Self-contained workflow for creating an external volume backed by AWS S3. Called from [SKILL.md](SKILL.md) after shared Steps 1-3 complete.

## Provider Prerequisites

**Check for AWS CLI:**

```bash
command -v aws &>/dev/null && echo "aws: OK" || echo "aws: MISSING"
```

**If MISSING, stop and provide installation instructions:**

| Tool | Install Command |
|------|-----------------|
| `aws` | `brew install awscli` or see [AWS CLI Install](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |

**⚠️ STOP**: Do not proceed until `aws` CLI is installed.

## Step 4: Gather S3 Requirements

**Verify AWS credentials:**

```bash
aws sts get-caller-identity
```

Show output to confirm correct AWS account. **If check fails:** Stop and help user resolve.

`<SKILL_DIR>/vol check-setup --suggest` also reports **credential-related environment variables** (names only: which are set). That is informational and **not** a substitute for `aws sts get-caller-identity`. If `csp_credential_env_signal` is true, one auth path is enough — do not ask the user to set other credential env vars "for completeness." See [Supplemental](supplemental.md) and [CLI Reference](cli-reference.md).

**Pre-stated preference short-circuit**: Before running context commands or asking anything, check if the user's original request already contains all required configuration values. If **bucket name** and **AWS region** are both stated in the original request, use the values below and proceed directly to Step 5:

- **Bucket** — use the stated base name
- **Prefix** — check the user's original request:
  - User said "no prefix" → use `--no-prefix` flag
  - User named a specific prefix value (e.g., "use prefix 'myteam'") → use `--prefix myteam` exactly
  - No prefix preference stated → derive from manifest `[snowflake].user` (see below)
- **Region** — use the stated region
- **Writes** — if the user said "read-only", "no writes", or "read only", add `--no-writes`; otherwise default to allowing writes

If bucket name or region is not stated in the original request, proceed with the interactive steps below (starting with "Get context for defaults").

**Get context for defaults:**

```bash
# Get prefix from manifest [snowflake].user (lowercase)
cat .sfutils/manifest.toml 2>/dev/null | grep "^user" | cut -d'"' -f2 | tr '[:upper:]' '[:lower:]'

# Get project name from current directory for bucket suggestion
basename "$(pwd)" | tr '[:upper:]' '[:lower:]' | tr '_' '-'
```

**Step 4a: Ask about prefix FIRST:**

```
External Volume Configuration:

Detected prefix (from manifest [snowflake].user): <prefix_value>

> **Note:** By default, all AWS resources (S3 bucket, IAM role, IAM policy) are prefixed 
> with your Snowflake username to avoid naming conflicts in shared AWS accounts.

1. Prefix option:
   - Use detected prefix: <prefix_value>
   - Use custom prefix: (specify your own)
   - No prefix: (use raw bucket name)
```

**⚠️ STOP**: Wait for user to select prefix option.

**Step 4b: Ask for bucket name (show WITH prefix applied):**

If user selected prefix (detected or custom), show bucket input WITH preview:

```
Prefix selected: <prefix_value>
Suggested bucket (from project): <project_name>

2. Bucket name (base name) [default: <project_name>]: 

With your prefix, resources will be:
  S3 Bucket:        <prefix>-<project_name>
  IAM Role:         <prefix>-<project_name>-snowflake-role  
  External Volume:  <PREFIX>_<PROJECT_NAME>_EXTERNAL_VOLUME
```

If user selected no prefix:

```
No prefix selected (raw names)
Suggested bucket (from project): <project_name>

2. Bucket name [default: <project_name>]: 

Resources will be:
  S3 Bucket:        <project_name>
  IAM Role:         <project_name>-snowflake-role
  External Volume:  <PROJECT_NAME>_EXTERNAL_VOLUME
```

**⚠️ STOP**: Wait for user input. After bucket entered, update preview with actual bucket name.

**Step 4c: Ask remaining options:**

```
3. AWS region [default: us-west-2]:
4. Allow writes? [default: yes]:
5. AWS profile? (leave blank to use default credential chain):
```

**⚠️ STOP**: Wait for user input.

> **`--no-writes` mapping:** If the user answers "no", "read-only", "no writes", or "read only" to "Allow writes?", pass `--no-writes` to **both** the dry-run and create commands (Steps 5 and 6).

> **`--aws-profile` mapping:** If user provides a profile name, add `--aws-profile <name>` to the `vol create` command. If blank, omit the flag — boto3 uses the standard credential chain (env vars, instance role, SSO, etc.) automatically.

## Step 5: Preview (Dry Run)

**IMPORTANT:** The `--region` flag is a GLOBAL option (before `create`), not on `create`.

**Working directory:** Run `<SKILL_DIR>/vol` from the project root. Connection is read automatically from `.sfutils/manifest.toml` — no env sourcing needed.

**Execute (with prefix):**

```bash
<SKILL_DIR>/vol \
  --region <AWS_REGION> \
  create --bucket <BUCKET> --dry-run
```

**Execute (without prefix):**

```bash
<SKILL_DIR>/vol \
  --region <AWS_REGION> --no-prefix \
  create --bucket <BUCKET> --dry-run
```

**Execute (read-only / no-writes):** add `--no-writes` when user requested read-only:

```bash
<SKILL_DIR>/vol \
  --region <AWS_REGION> \
  create --bucket <BUCKET> --no-writes --dry-run
```

**Execute (with AWS profile):** add `--aws-profile` when user provided a profile name:

```bash
<SKILL_DIR>/vol \
  --region <AWS_REGION> \
  create --bucket <BUCKET> --aws-profile <PROFILE> --dry-run
```

**🔴 CRITICAL: Run the CLI dry-run, capture its output, and present it IN YOUR RESPONSE.** Terminal output gets collapsed/truncated by the UI — you MUST copy-paste the ENTIRE output into your response using language-tagged code blocks (` ```text ` for summary, ` ```json ` for IAM policies, ` ```sql ` for DDL). See [Supplemental](supplemental.md) for a full formatting example.

> 🔄 **On pause/resume:** Re-run `--dry-run` and paste the complete output again before asking for confirmation.

**⚠️ STOP**: Wait for explicit user approval ("yes", "ok", "proceed") before creating resources.

## Step 6: Create Resources

Same working directory rule as Step 5: project root; no env sourcing needed.

**Execute (with prefix):**

```bash
<SKILL_DIR>/vol \
  --region <AWS_REGION> \
  create --bucket <BUCKET> --output json
```

**Execute (without prefix):**

```bash
<SKILL_DIR>/vol \
  --region <AWS_REGION> --no-prefix \
  create --bucket <BUCKET> --output json
```

**Execute (read-only / no-writes):** add `--no-writes` when user requested read-only:

```bash
<SKILL_DIR>/vol \
  --region <AWS_REGION> \
  create --bucket <BUCKET> --no-writes --output json
```

**On success:**

- The CLI automatically writes the volume entry to `.sfutils/manifest.toml`
- **Return to [SKILL.md](SKILL.md) Step 7** (verify)

**Note:** External volumes have many applications:

- Iceberg tables (managed data lake)
- COPY INTO unload (data export)
- External stages (data import)
- Data sharing with other platforms

**On failure:** Rollback is automatic. Present error.
