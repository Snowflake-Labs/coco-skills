**Part 1b - Rule Mode Selection:**

> **Note:** ask_user_question supports max 4 options. POSTGRES modes are grouped.

Use `ask_user_question` with options (INGRESS pre-selected as default):

```
Network rule mode:

○ INGRESS (default)
  Control who can connect TO Snowflake

○ EGRESS
  Control what Snowflake can connect TO (external access)

○ INTERNAL_STAGE
  Internal stage access rules

○ POSTGRES
  PostgreSQL interface (Iceberg, external tables)
```

**If user selects POSTGRES:** Follow-up with direction question:

```
PostgreSQL interface direction:

○ POSTGRES_INGRESS (default)
  Incoming connections to PostgreSQL interface

○ POSTGRES_EGRESS
  Outbound connections from PostgreSQL interface
```

**Cortex Code Conversion:** Selected mode → `--mode <value>`

**Part 1c - Rule Type Selection (mode-dependent):**

> **⚠️ CRITICAL:** Mode and Type have constraints. Use wrong combination = Snowflake error!

**Mode-Type Compatibility Matrix:**

| Mode | Valid Types | Default | Notes |
|------|-------------|---------|-------|
| INGRESS | IPV4, AWSVPCEID | IPV4 | IP allowlisting |
| INTERNAL_STAGE | IPV4, AWSVPCEID | IPV4 | Stage access |
| EGRESS | HOST_PORT, IPV4 | HOST_PORT | Use HOST_PORT for hostname:port targets |
| POSTGRES_INGRESS | IPV4, AWSVPCEID | IPV4 | PostgreSQL incoming |
| POSTGRES_EGRESS | HOST_PORT, IPV4 | HOST_PORT | Use HOST_PORT for hostname:port targets |

**If mode is INGRESS, INTERNAL_STAGE, or POSTGRES_INGRESS:**

Use `ask_user_question` with IPV4 pre-selected:

```
Rule type:

○ IPV4 (default)
  IP addresses/CIDR ranges (e.g., 192.168.1.0/24)

○ AWSVPCEID
  AWS VPC Endpoint IDs
```

**If mode is EGRESS or POSTGRES_EGRESS:**

Use `ask_user_question` with HOST_PORT pre-selected:

```
Rule type:

○ HOST_PORT (recommended)
  Hostname:port targets (e.g., api.github.com:443)

○ IPV4
  Specific external IP addresses to connect to
```

**Cortex Code Conversion:** Selected type → `--type <value>`

**Part 2 - Value Input (type-dependent):**

> **⚠️ IMPORTANT:** IP source presets (--allow-local, --allow-gh, --allow-google) only work with IPV4 type!

**If type is IPV4:** Show IP Sources Selection (multi-select)

Use `ask_user_question` with `multiSelect: true`:

```
Which IP sources should be allowed access?
(Select all that apply)

☐ My current IP
  Auto-detected local IP for development

☐ GitHub Actions
  Allow CI/CD workflows from GitHub (IPv4 only)

☐ Google Cloud
  Allow Cloud Run, GKE, Compute Engine

☐ Custom CIDRs
  I'll specify IP ranges
```

**If "GitHub Actions" is selected**, ask a follow-up with `ask_user_question`:

```
How should GitHub Actions be allowed?

● Snowflake-managed SaaS rule (recommended)
  References SNOWFLAKE.NETWORK_SECURITY.GITHUBACTIONS_GLOBAL — always current,
  no manual refresh needed. Requires --policy. Not available in gov regions.

○ IP snapshot (gov regions / no policy)
  Fetches current CIDRs from api.github.com/meta (~4,700 ranges). Must be
  refreshed manually as GitHub's IP ranges change. No policy required.
```

- **If SaaS rule chosen:** Use `--allow-gh` **and** ensure `--policy <POLICY_NAME>` is set (prompt if not already provided). The CLI will add `GITHUBACTIONS_GLOBAL` to the policy's `ALLOWED_NETWORK_RULE_LIST` automatically.
- **If IP snapshot chosen (gov / no policy):** Fetch snapshot via `get_github_actions_ips()` — pass `--values <cidrs>` manually after fetching, or note to user that `--allow-gh` will error without `--policy`.

**Cortex Code Conversion Table (IPV4 only):**

| User Selection | CLI Flag |
|----------------|----------|
| My current IP | `--allow-local` |
| GitHub Actions (SaaS rule) | `--allow-gh --policy <POLICY_NAME>` |
| GitHub Actions (IP snapshot) | `--values "<snapshot-cidrs>"` (fetch manually) |
| Google Cloud | `--allow-google` |
| Custom CIDRs | → Follow-up prompt → `--values "..."` |

**If "Custom CIDRs" selected, prompt:**

```
Enter custom CIDRs (comma-separated):
Example: 10.0.0.0/8, 192.168.1.0/24
```

**If type is HOST_PORT — Step 2a: Select known service presets (multi-select)**

Use `ask_user_question` with `multiSelect: true`:

```
Which services should this rule allow access to?
(Select all that apply — hosts resolved automatically from Intent Vocabulary)

☐ Slack           *.slack.com:443
☐ GitHub          *.github.com:443
☐ Google APIs     googleapis.com endpoints, accounts.google.com
☐ Google Drive    drive.google.com + googleapis.com OAuth
☐ AWS             *.amazonaws.com, *.amazon.com
☐ Snowflake API   *.snowflakecomputing.com
☐ OpenAI          api.openai.com:443
☐ Anthropic       api.anthropic.com:443
☐ HuggingFace     huggingface.co, api-inference.huggingface.co
☐ PyPI            pypi.org, files.pythonhosted.org
☐ SharePoint      *.sharepoint.com, graph.microsoft.com
☐ Custom hosts    I'll specify host:port manually
```

**Cortex Code Conversion for selected presets:**
```bash
--preset slack --preset github --preset aws  # etc., one --preset per selection
```

**If "Custom hosts" also selected:** continue to Step 2b (manual host:port entry), append to `--values`.

**If only presets selected:** skip Step 2b, proceed to Step 2c.

**Step 2c: EAI vs Network Policy** — See [EGRESS Flows → Step E3](egress-flows.md) for the ownership gate (`CREATED` vs `ALTERED`) and blast-radius review.

**If type is HOST_PORT:** Prompt for hostnames directly

```
Enter hostname:port targets (comma-separated):
Example: api.github.com:443, storage.googleapis.com:443
```

**Cortex Code Conversion:** → `--values "<comma-separated-hosts>"`

**If type is AWSVPCEID:** Prompt for VPC endpoint IDs

```
Enter AWS VPC Endpoint IDs (comma-separated):
Example: vpce-1234567890abcdef0
```

**Cortex Code Conversion:** → `--values "<comma-separated-vpce-ids>"`

**⚠️ STOP**: Wait for user input on ALL values.

