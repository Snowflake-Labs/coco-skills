# Migration Workshop Skill — Setup Guide

## Prerequisites

- A Snowflake account (free trial works: https://signup.snowflake.com)
- macOS (Apple Silicon or Intel), Linux (x64/arm64), or Windows (WSL or native preview)

## Step 1: Install Cortex Code CLI

**macOS / Linux / WSL:**
```bash
curl -LsS https://ai.snowflake.com/static/cc-scripts/install.sh | sh
```

**Windows (PowerShell):**
```powershell
irm https://ai.snowflake.com/static/cc-scripts/install.ps1 | iex
```

No Snowflake account yet? Sign up for a free Cortex Code trial at https://signup.snowflake.com/cortex-code — it includes Cortex Code CLI usage for 30 days.

## Step 2: Connect to Snowflake

Run `cortex` in your terminal. A setup wizard walks you through connecting to your Snowflake account. You can use browser-based SSO, key-pair auth, or username/password.

If your account requires cross-region inference for the AI models, an ACCOUNTADMIN must run:
```sql
ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'AWS_US';
```

## Step 3: Install the Skill

Copy the entire `migration-workshop/` folder to the Cortex Code global skills directory:

```bash
mkdir -p ~/.snowflake/cortex/skills
cp -R migration-workshop ~/.snowflake/cortex/skills/
```

That's it. Cortex Code auto-discovers skills in `~/.snowflake/cortex/skills/`.

## Step 4: Verify

Inside a Cortex Code session, type:

```
/skill
```

You should see `agentic-migration-workshop` listed under Global skills. To use it:

```
$agentic-migration-workshop I need to migrate our Oracle database to Snowflake
```

Or simply describe your migration task — the skill triggers automatically on keywords like `migrate`, `migration`, `convert`, `translate SQL`, `SnowConvert`, `SSIS`, `Power BI repointing`.

## What's Included

```
migration-workshop/
├── SKILL.md                        # Main router — welcome flow, intent detection
├── assessment/SKILL.md             # Migration assessment & effort estimation
├── schema-conversion/SKILL.md      # DDL conversion & data type mapping
├── data-migration/SKILL.md         # Staging, loading, validation
├── query-translation/SKILL.md      # SQL/stored procedure conversion
├── snowconvert-ai/SKILL.md         # SnowConvert AI automated conversion
├── ssis-replatform/SKILL.md        # SSIS package replatforming
├── powerbi-repointing/SKILL.md     # Power BI datasource repointing
├── references/                     # Platform-specific migration guides
│   ├── oracle.md
│   ├── teradata.md
│   ├── redshift.md
│   ├── sqlserver.md
│   └── best-practices.md
├── scripts/assess_complexity.py    # DDL complexity scoring tool
└── pyproject.toml                  # Python dependencies for scripts
```

## Supported Source Platforms

- Oracle
- Teradata
- Amazon Redshift
- SQL Server (including SSIS and Power BI)
