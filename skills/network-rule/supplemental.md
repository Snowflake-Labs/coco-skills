## Intent Vocabulary (Preset Reference)

Map user descriptions to named presets. **Never invent HOST:PORT values** — use only these.

| User describes | Preset | Resolved hosts |
|----------------|--------|----------------|
| Slack alerts / webhooks | `slack` | `*.slack.com:443` |
| S3, AWS storage, Secrets Manager | `aws` | `*.amazonaws.com:443`, `*.amazon.com:443` |
| Google Drive files | `google-drive` | `drive.google.com:443`, googleapis.com, accounts.google.com |
| Google Sheets / Gmail / Google APIs | `google-apis` | googleapis.com, accounts.google.com |
| OpenAI / ChatGPT / GPT-* | `openai` | `api.openai.com:443` |
| Anthropic / Claude | `anthropic` | `api.anthropic.com:443` |
| HuggingFace models / inference | `huggingface` | `huggingface.co:443`, `api-inference.huggingface.co:443` |
| GitHub API / git operations | `github` | `*.github.com:443` |
| SharePoint / Microsoft 365 / OneDrive | `sharepoint` | `*.sharepoint.com:443`, `graph.microsoft.com:443` |
| PyPI packages / pip install | `pypi` | `pypi.org:443`, `files.pythonhosted.org:443` |
| Snowflake REST API | `snowflake` | `*.snowflakecomputing.com:443` |
| OpenFlow Google Drive connector | `google-drive` | (see above) |
| OpenFlow S3/AWS connector | `aws` | (see above) |
| OpenFlow Kafka broker | custom | ask: "What is the Kafka broker host:port?" |
| OpenFlow PostgreSQL CDC | custom | ask: "What is the PostgreSQL host:port?" |
| Unlisted service | custom | ask: "What is the hostname and port?" |

CLI: `--preset slack --preset openai` (repeatable; auto-derives EGRESS/HOST_PORT)

> **Source of truth:** `_presets.py::PRESET_REGISTRY` — grounded in real production EAI data.

---

## Stopping Points

1. ✋ **Network Manifest Gate**: Before replay/remove/manage-existing — run `nw validate-manifest`; if fails run `nw validate-manifest --fix`. Stop if issues remain.
2. ✋ Step 1: If connection checks fail
3. ✋ Step 2: If infra check needed (prompts user)
4. ✋ Step 3: After gathering requirements
5. ✋ Step 4: After dry-run preview (get approval)

## Output

- Network rule (IPV4, HOST_PORT, or AWSVPCEID)
- Network policy (optional, linked to rule)
- Updated `.sfutils/manifest.toml` with rule entry

## Security Notes

- Add `.sfutils/` to `.gitignore` (contains manifest.toml with account metadata)
- Network rules control IP-based access to Snowflake
- INGRESS rules restrict incoming connections
- Use specific CIDRs, not 0.0.0.0/0
- Review IP sources periodically - GitHub/Google ranges change over time
- Use `--allow-local` for development, restrict to known CIDRs for production

## SQL Reference (Snowflake Documentation)

> These links help Cortex Code infer correct SQL syntax when previewing or troubleshooting.

| Statement | Documentation |
|-----------|---------------|
| `CREATE NETWORK RULE` | https://docs.snowflake.com/en/sql-reference/sql/create-network-rule |
| `ALTER NETWORK RULE` | https://docs.snowflake.com/en/sql-reference/sql/alter-network-rule |
| `DROP NETWORK RULE` | https://docs.snowflake.com/en/sql-reference/sql/drop-network-rule |
| `SHOW NETWORK RULES` | https://docs.snowflake.com/en/sql-reference/sql/show-network-rules |
| `CREATE NETWORK POLICY` | https://docs.snowflake.com/en/sql-reference/sql/create-network-policy |
| `ALTER NETWORK POLICY` | https://docs.snowflake.com/en/sql-reference/sql/alter-network-policy |
| `DROP NETWORK POLICY` | https://docs.snowflake.com/en/sql-reference/sql/drop-network-policy |
| `SHOW NETWORK POLICIES` | https://docs.snowflake.com/en/sql-reference/sql/show-network-policies |
| `ALTER USER ... SET NETWORK POLICY` | https://docs.snowflake.com/en/sql-reference/sql/alter-user |
| `ALTER USER ... UNSET NETWORK_POLICY` | https://docs.snowflake.com/en/sql-reference/sql/alter-user |
| `GRANT ... ON SCHEMA` | https://docs.snowflake.com/en/sql-reference/sql/grant-privilege |

## Troubleshooting

**Infrastructure not set up:** Run `<SKILL_DIR>/nw check-setup --run-setup` - it will check and offer to create the database and schemas.

**Permission denied:** Ensure admin_role (from manifest.toml `[snowflake].admin_role`, defaults to ACCOUNTADMIN) has CREATE NETWORK RULE and CREATE NETWORK POLICY privileges.

**Rule already exists:** Use Step 3a flow - choose "Update existing" to modify IPs or "Remove and recreate" for fresh start.

**Invalid CIDR:** Ensure CIDRs are in x.x.x.x/mask format (e.g., `192.168.1.0/24`, `10.0.0.0/8`).

**GitHub Actions IPs not working:** `--allow-gh` fetches a snapshot of IPv4 ranges from `api.github.com/meta` at creation time — IPv6 ranges are excluded. If runners are failing, GitHub may have added new CIDRs since the rule was created; re-run `rule update --allow-gh` to refresh. For auto-updating coverage, use the `network-security` skill's hybrid policy with `SNOWFLAKE.NETWORK_SECURITY.GITHUBACTIONS_GLOBAL` instead (no role access required; just add it to the network policy's `ALLOWED_NETWORK_RULE_LIST`).

**Cannot find resources for cleanup:** Check `.sfutils/manifest.toml` `[rule.<label>]` for exact resource names and `[rule.<label>.cleanup]` for the CLI command to run.

**Partial creation failed:** If manifest shows `status = "CREATE_IN_PROGRESS"`, the resource creation was interrupted. Use Resume flow to continue from where it stopped, or manually clean up created resources using the `[rule.<label>.cleanup]` section.

**Policy depends on rule:** When cleaning up, ALWAYS drop the network policy BEFORE dropping the network rule (policy references the rule). Dropping a rule that is still referenced by a policy will fail. The CLI `rule delete` handles this dependency order automatically. If using manual SQL fallback, strictly follow: (1) DROP NETWORK POLICY, (2) DROP NETWORK RULE.

**manifest.toml validation failures:** Run `nw validate-manifest --fix` to repair structural gaps. Non-structural issues (empty connection, infra_ready=false) require manual action — the `--fix` flag will tell you what to run.
