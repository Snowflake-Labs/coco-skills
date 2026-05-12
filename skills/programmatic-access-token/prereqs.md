## Prerequisites

### Step 0: Check Prerequisites (Manifest-Cached)

**First, check if this is an existing project that needs migration:**

```bash
ls .sfutils/manifest.toml 2>/dev/null || echo "MISSING_TOML"
```

If `manifest.toml` is missing but `.env` or `.sfutils/sfutils-manifest.md` exist → run `sfutils-pat migrate` before proceeding (see Step 0 in SKILL.md).

**Otherwise, check manifest for cached prereqs:**

```bash
cat .sfutils/manifest.toml 2>/dev/null | grep "tools_verified" || echo "not_cached"
```

**If `tools_verified` exists with a date:** Skip tool checks, continue to Step 1.

**Otherwise, run the pre-flight check:**

```bash
<SKILL_DIR>/pat check-setup --suggest
```

**If `ready: false`**, the tool reports what is missing and suggests next steps.

**If `uv` or `snow` are not installed**, install them first:

| Tool | Install Command |
|------|-----------------|
| `uv` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `snow` | `pip install snowflake-cli` or `uv tool install snowflake-cli` |

**STOP**: Do not proceed until all prerequisites pass.

**After tools verified, initialize manifest** (if `.sfutils/manifest.toml` doesn't exist):

```bash
mkdir -p .sfutils && chmod 700 .sfutils
if [ ! -f .sfutils/manifest.toml ]; then
  PROJECT=$(basename $(pwd))
  DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  TODAY=$(date +%Y-%m-%d)
  cat > .sfutils/manifest.toml << TOML
# Machine-managed by Cortex Code. Do not hand-edit.
schema_version = "1"
project_name   = "${PROJECT}"
created_at     = "${DATE}"

[snowflake]
connection   = ""
account      = ""
user         = ""
account_url  = ""
sf_utils_db  = ""
admin_role   = "ACCOUNTADMIN"

[prereqs]
tools_verified = "${TODAY}"
infra_ready    = false
TOML
fi
chmod 600 .sfutils/manifest.toml
```

### Step 0b: Optional Dependent Skills

**`sfutils-pat create` is self-contained.** It handles network rule and policy creation natively — no other skill is required. By default, `--allow-local` is enabled and the tool creates all necessary network objects automatically.

**`sfutils-networks` is never required.** Only use it if you explicitly want a shared network policy that is reused across multiple service users. In that case, run `sfutils-networks` first to set up the shared policy, then pass `--skip-network` to `sfutils-pat create` to skip the built-in network setup.
