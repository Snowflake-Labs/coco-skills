# EGRESS Flows

> **Prerequisite:** Complete SKILL.md Steps 0–2 (manifest, connection, infra) before this flow.
>
> **Scope:** EGRESS HOST_PORT rules — used when Snowflake services (SPCS, Snowpark, Cortex,
> OpenFlow, etc.) need to call external endpoints.
>
> Two paths (chosen in mode-type-reference.md Step 2c):
> - **Section 1 — EAI Builder** → Snowflake services calling external APIs (most common)
> - **Section 2 — Network Policy (EGRESS)** → account/user/role-level outbound restriction

---

## Section 1: EAI Builder

> Implements Infrastructure-as-Intent for external access. The user expresses WHAT
> their Snowflake service needs to reach. The skill resolves this through the Intent
> Vocabulary to a structured model, confirms it (blast-radius review), then executes.
> The manifest records the resolved model as Architectural Memory.

### Step E1 — Capture intent (one question only)

Ask the user:

  "What external services does your Snowflake service need to reach?"

Do NOT ask about HOST:PORT, modes, types, or EAI syntax. Let them answer naturally.

> **Mixed intent:** If the user also described an INGRESS need (local IP, CIDR, VPC,
> GitHub Actions connecting TO Snowflake), note it and complete this EAI flow first.
> After Step E5, return to SKILL.md and follow [ingress-flows.md](ingress-flows.md)
> for the pending INGRESS rule.

---

### Step E2 — Resolve through the Intent Vocabulary

Map the user's description to preset name(s).
**The AI MUST use named presets — never invent HOST:PORT values directly.**
This prevents the Hallucination Tax (generating plausible-but-wrong specs).

| User describes | Preset(s) |
|----------------|-----------|
| Slack alerts / notifications / webhooks | `slack` |
| S3 files, AWS storage, Secrets Manager | `aws` |
| Google Drive files | `google-drive` |
| Google Sheets / Gmail / Google APIs | `google-apis` |
| OpenAI / ChatGPT / GPT-* | `openai` |
| Anthropic / Claude | `anthropic` |
| HuggingFace models / inference | `huggingface` |
| GitHub API / git operations | `github` |
| SharePoint / Microsoft 365 / OneDrive | `sharepoint` |
| PyPI packages / pip install | `pypi` |
| Snowflake REST API | `snowflake` |
| OpenFlow Google Drive connector | `google-drive` |
| OpenFlow S3 / AWS connector | `aws` |
| OpenFlow Slack connector | `slack` |
| OpenFlow GitHub connector | `github` |
| OpenFlow SharePoint connector | `sharepoint` |
| OpenFlow Kafka connector | custom → ask: "What is the Kafka broker host:port?" |
| OpenFlow PostgreSQL CDC | custom → ask: "What is the PostgreSQL host:port?" |
| Unlisted service | custom → ask: "What is the hostname and port?" |

Multiple services → combine into one rule (multiple `--preset` flags, auto-derives EGRESS/HOST_PORT).

**Naming conventions (consistent with network rule prefix pattern):**
- Rule name: `{USER}_{SERVICE}_{SCOPE}_EGRESS_RULE`
  (e.g. `KAMESHS_SLACK_APP_EGRESS_RULE` — use `SNOWFLAKE_USER` from manifest as prefix)
- EAI name: derived from rule name — replace `_EGRESS_RULE` suffix with `_EAI`
  (e.g. `KAMESHS_SLACK_APP_EAI`). If no `_EGRESS_RULE` suffix: replace last `_RULE` with `_EAI`

---

### Step E3 — EAI selection gate + blast-radius review

**First ask the user about EAI ownership** — the choice determines `operation` in the manifest:

```
What do you want to do with the External Access Integration?

● Create a new EAI  [operation: CREATED]
  A new EAI dedicated to this rule.
  Cleanup: this EAI will be DROPPED when the rule is deleted.

○ Add rule to an existing EAI  [operation: ALTERED]
  Append to an EAI already in your account.
  Cleanup: only this rule is removed from the EAI — the EAI itself is preserved.
```

**If "Add to existing EAI"**, discover account-level EAIs:
```bash
<SKILL_DIR>/nw integration list --output json
```
Populate `ask_user_question` with up to 5 names + "Something else".

**Then present the blast-radius review** before any execution:

```
Based on your intent, I'll create:

  Intent:    "{user's description}"
  Resolved:  presets=[{list}]{", custom=[...]" if applicable}

  Network rule: {RULE_NAME}  (EGRESS / HOST_PORT)
    Hosts: {resolved HOST:PORT list}

  EAI: {EAI_NAME}  [CREATED — will be dropped with the rule]
  — or —
  EAI: {EXISTING_EAI}  [ALTERED — rule removed on cleanup, EAI preserved]
    Reference: EXTERNAL_ACCESS_INTEGRATIONS = ('{EAI_NAME}')

This will be recorded in manifest.toml as Architectural Memory.

Proceed? [yes/no]
```

**⚠️ STOP**: This is the only place infrastructure details surface. Wait for confirmation.

---

### Step E4 — Execute

> **⚠️ `--preset` auto-derives EGRESS/HOST_PORT** — no need for `--mode egress --type host_port`.
> Never include `--allow-gh` or `--allow-google` alongside `--integration` (IPV4-only flags).

```bash
# Option A: New rule + new EAI in one command
<SKILL_DIR>/nw rule create \
  --name {RULE_NAME} --db {SF_UTILS_DB} \
  [--preset slack] [--preset aws] \
  [--values "custom.host:443"] \
  --integration {EAI_NAME} \
  --yes

# Option B: New rule, add to existing EAI
<SKILL_DIR>/nw rule create \
  --name {RULE_NAME} --db {SF_UTILS_DB} \
  [--preset slack] [--preset aws] \
  --integration {EXISTING_EAI} --integration-mode alter \
  --yes

# Option C: Rule only, attach to EAI separately
<SKILL_DIR>/nw rule create \
  --name {RULE_NAME} --db {SF_UTILS_DB} \
  [--preset slack] [--preset aws] \
  --yes
# Then:
<SKILL_DIR>/nw integration create --name {EAI_NAME} --rules {FQN} --yes
```

---

### Step E5 — Architectural Memory

After execution, show the user:

```
✓ Architectural Memory updated: .sfutils/manifest.toml

  rule: {label}  (EGRESS / HOST_PORT)
  Hosts: {preset-resolved list}
  EAI:   {EAI_NAME}

  Reference in your function/procedure/SPCS service:
    EXTERNAL_ACCESS_INTEGRATIONS = ('{EAI_NAME}')

Run 'nw list' to see all rules.
Run 'nw validate-manifest' to confirm the model is well-formed.
```

**If mixed intent was noted in Step E1:** Return to [ingress-flows.md](ingress-flows.md) for the pending INGRESS rule.

---

## Section 2: EGRESS + Network Policy

> Use this path when the user wants **account/user/role-level outbound restriction** —
> not Snowflake-service external API access. A Network Policy with EGRESS rules restricts
> what hosts a service user can connect to at the account level.
>
> This is distinct from EAI (Section 1): EAI grants specific functions/procedures the ability
> to call external APIs; Network Policy EGRESS restricts outbound at the user/role level.

For EGRESS + Network Policy, follow [ingress-flows.md](ingress-flows.md) Steps 3–6 with these differences:

- Use `--mode egress` and `--type host_port` (or `--type ipv4`) as appropriate
- **Do NOT use** `--allow-local`, `--allow-gh`, `--allow-google` (IPV4 INGRESS presets)
- Use `--values "host:port,..."` for HOST_PORT or `--values "CIDR,..."` for IPV4
- Policy is applied to users/roles; rule restricts their outbound connections

**EAI management reference:**

```bash
nw integration list [--output json]                  # discover account-level EAIs
nw integration create --name N --rules FQN [--yes]   # new EAI
nw integration alter  --name N --add-rules F [--yes] # add rule to existing EAI
nw integration delete --name N [--yes]               # remove EAI
```
