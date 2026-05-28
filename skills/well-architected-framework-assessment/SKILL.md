---
name: well-architected-framework-assessment
title: WAF Assessment
language: en
status: Published
author: Post Go-Live
type: snowflake
tools: [snowflake_sql_execute, ask_user_question, visualize_data, task, read, write, bash]
prompt: "Run a Well-Architected Framework assessment for this account"
summary: "Assess a Snowflake account against all five Well-Architected Framework pillars."
description: "Use when running a Snowflake Well-Architected Framework (WAF) assessment for a customer account. Three-tier scale: On Track / Needs Improvement / Needs Attention. Results displayed inline; optional HTML export. Triggers: WAF assessment, well-architected, architecture review, WAF review, security assessment, pillar assessment, security and governance, reliability, cost optimization, operational excellence, performance."
---

# Well-Architected Framework Assessment

## Prerequisites

- Active Snowflake connection with `ACCOUNTADMIN` role (or role with access to `SNOWFLAKE.ACCOUNT_USAGE`)
- Account must be the customer's own account (not Snowhouse/internal)

## When to Use

- Customer needs a structured assessment of their Snowflake account against WAF best practices
- Engagement kickoffs, health checks, or architecture reviews

## Workflow

### Step 1: Confirm Active Connection

The WAF checks run against the **customer's own** `SNOWFLAKE.ACCOUNT_USAGE` — the customer's Snowflake connection must be active. Run this query to confirm and capture header metadata:

```sql
SELECT
  CURRENT_ACCOUNT()          AS account_name,
  CURRENT_ORGANIZATION_NAME() AS org_name;
```

If a permission error occurs (e.g., the active role is not ACCOUNTADMIN), suggest: "Please switch to ACCOUNTADMIN: `USE ROLE ACCOUNTADMIN;`"

If the query itself fails (connection error, timeout), ask the user to verify their Snowflake connection is active before retrying.

After confirming the connection, attempt to detect the account edition automatically:

```sql
SELECT PARSE_JSON(SYSTEM$BOOTSTRAP_DATA_REQUEST('ACCOUNT')):accountInfo:serviceLevelName::STRING AS service_level;
```

If this succeeds, map the returned value to the edition (e.g., `BUSINESS_CRITICAL` → Business Critical, `ENTERPRISE` → Enterprise, `STANDARD` → Standard, `VPS` → VPS). Use this as `EDITION` and skip the edition question below.

If this fails (permission error, unsupported function, etc.), fall back to asking the user for their edition in the `ask_user_question` call below.

Then use `ask_user_question` for the pillar selection (and edition only if auto-detection failed):

```
(Only if edition auto-detection failed):
Question 1:
  Header: "Edition"
  Question: "What Snowflake edition is this account?"
  multiSelect: false
  Options:
    - Standard
    - Enterprise
    - Business Critical
    - VPS

Question (always):
  Header: "WAF Pillars"
  Question: "Which pillars would you like to assess? Select one or many."
  multiSelect: true
  Options (label only — leave description empty ""):
    - Security & Governance
    - Operational Excellence
    - Cost Optimization
    - Performance
    - Reliability
```

Use the results to populate:
- `CUSTOMER_NAME` — always use the org name directly as the customer name. Do NOT ask the user to confirm or provide a friendly name.
- `ACCOUNT_NAME` — from `CURRENT_ACCOUNT()`
- `EDITION` — from the auto-detected value or user's answer (if auto-detection failed)

> **Note:** All five pillars (Security & Governance, Operational Excellence, Cost Optimization, Performance, and Reliability) are currently active.

Output the detected account information to the user (org name, account name, edition, selected pillars) and proceed directly to Step 2. (This skill uses `ask_user_question` checkpoints as its approval gates.)

✋ **STOP** — wait for `ask_user_question` response before proceeding.

### Step 2: Load References, Dispatch Subagents & Collect Manual Input

> **PERFORMANCE OPTIMIZATION:** This step loads all reference files, dispatches automated subagents FIRST, then collects manual user input while subagents run in background. Subagents execute SQL checks concurrently while the user answers manual questions — no sequential bottleneck.

Resolve `SKILL_DIR` = the absolute path to the directory containing this SKILL.md file (available from the skill launch context).

#### 2a. Load All Reference Files in Parallel

In a **single message**, read ALL of the following files simultaneously:
- `<SKILL_DIR>/references/subagent_prompt_template.md`
- All selected pillar YAML files (see Pillar paths table below)

This ensures the subagent template and all YAML metadata are available immediately.

#### Pillar paths

| Pillar | PILLAR_KEY | CHECKS_YAML_PATH |
|--------|-----------|-----------------|
| Security & Governance | `sec` | `<SKILL_DIR>/references/controls/security_governance.yaml` |
| Cost Optimization | `cost` | `<SKILL_DIR>/references/controls/cost_optimization.yaml` |
| Reliability | `rel` | `<SKILL_DIR>/references/controls/reliability.yaml` |
| Operational Excellence | `opex` | `<SKILL_DIR>/references/controls/operational_excellence.yaml` |
| Performance | `perf` | `<SKILL_DIR>/references/controls/performance.yaml` |

> **Note:** Control/check IDs within YAML files are not necessarily sequential (e.g. SEC-1, SEC-3, SEC-6 — gaps are intentional).

#### 2b. Dispatch Subagents FIRST, Then Ask Manual Questions While They Run

> **CRITICAL ORDERING:** Launch ALL subagents with `run_in_background: true` FIRST in a single tool-calling message. Only AFTER the subagents are dispatched and running in the background, proceed to ask manual questions via `ask_user_question`. The subagents execute SQL concurrently in the background while the user answers manual questions — minimizing total wall-clock time.

**Subagent dispatch (do this FIRST):**
- In your FIRST tool-calling message for this step, launch ALL pillar subagents using the `runSubagent` tool with `run_in_background: true`.
- Do NOT include `ask_user_question` in the same message as the subagent launches — send subagents first, then ask manual questions in the next message.
- Each subagent runs independently and returns a structured JSON result block.
- The subagent receives the consolidated YAML but only processes checks with `type: automated`. It skips `type: manual` checks.

**Manual questions (do this SECOND, while subagents run):**
- After all subagents are dispatched and confirmed running, immediately ask manual questions via `ask_user_question`.

**After all manual questions are answered**, call `wait_agent` with all subagent IDs (**timeout: 600000ms — 10 minutes**) to collect their results. If any subagent times out, retry it once with `run_in_background: true` and `wait_agent` again (maximum 2 total attempts per pillar).

**Subagent prompt template:**

Use the template loaded from `references/subagent_prompt_template.md`. Before sending each prompt, substitute:
- `<PILLAR_DISPLAY_NAME>` → e.g. `Security & Governance`
- `<EDITION>` → edition from Step 1
- `<CHECKS_YAML_PATH>` → absolute path (see table above)
- `<PILLAR_KEY>` → `sec` / `opex` / `cost` / `perf` / `rel`

**Manual question details:**

> **Current state:** Security & Governance and Performance have no manual checks (all type: automated). Operational Excellence, Cost Optimization, and Reliability have manual checks. When only Security & Governance and/or Performance are selected, skip manual questions entirely.

> **CRITICAL: SILENT COLLECTION.** Do NOT output ANY text about manual checks to the user — no lists, no summaries, no "let me collect them", no bullet points of check names, no intermediate narration. Go DIRECTLY to calling `ask_user_question`. The user should see ONLY the `ask_user_question` prompts and nothing else.

Immediately ask ALL manual questions from ALL pillars via `ask_user_question` calls (max 4 questions per call — if more than 4, use the minimum number of calls needed). Use the format `<Pillar Name>: <Control or Check Name>` as the question header (e.g., "Reliability: Cross-Region DR Confirmation", "Operational Excellence: IaC Tool Adoption"). Use the human-readable `name` field from the YAML — do NOT use IDs like `SEC-3` or `REL-5a`.

✋ **STOP** — After all manual check answers are received, call `wait_agent` with all subagent IDs to collect their results. Proceed to Step 3 only when both manual results and subagent results are available.

**Check type handling within each pillar:**

**`type: manual`** — has `question`:
- Ask question via `ask_user_question`
- **Options MUST come from the YAML `question_options` field.** If the check has a `question_options` list, use EVERY entry in that list as an option in `ask_user_question` (each entry becomes one option label). Do NOT omit any options. Do NOT invent options that are not in the YAML.
- When presenting options, do NOT prefix them with letters (A, B, C, D) or numbers. Just use the option text directly as the label.
- **Grading:** Match the user's selected option text against the control's `thresholds` field. Threshold values now reference the exact option text (or `|`-separated options for multi-option tiers). If the user's answer matches a threshold value exactly (or is one of the `|`-separated values), assign that tier. "I don't know" always maps to `needs_attention`.
- For **mixed controls** (both manual and automated checks), grade only the manual check here. The control-level grade is determined in Step 3a by the main agent after merging manual and automated check results.
- Store results as `{id, result, value}` objects

#### 2c. Outputs

- **Manual results list**: `[{id, result, value}, ...]` — graded manual checks to merge with subagent results
- **Subagent results**: JSON blocks from each pillar subagent

If a subagent fails or errors, record that pillar as `{"pillar": "<key>", "error": "<message>"}` and continue.

**Parsing subagent responses:** Subagents may include reasoning text before/after the JSON block. Extract the JSON by finding the outermost `{...}` that matches the expected schema (has `"pillar"`, `"controls"`, `"checks"` keys). If no valid JSON is found, treat the pillar as errored.

**On partial failure:** If some pillars succeed and others fail, present the successful results in Step 3 and note which pillars errored with the error message. Retry failed pillars once (maximum 2 total attempts per pillar). If the retry also fails, skip that pillar and inform the user.

### Step 3: Merge & Present Results Inline in CoCo

> **IMPORTANT: Minimize verbosity.** Do NOT explain how results were merged, how scores were calculated, or narrate your process. Go straight to the visualized output (metric cards, tables, charts). The user only wants to see the results, not how you arrived at them.

> **TONE & SEVERITY RULES (mandatory for all output):**
> - **Color system:** Only use 🟢 On Track, 🟡 Needs Improvement, 🟠 Needs Attention. Do NOT use red or any red-coded indicators.
> - **Prohibited words in output:** Never use "critical", "high-risk", "danger", "failure", "violation", or "urgent" in any user-facing text (recommendations, value summaries, table cells). Use "needs attention", "opportunity for improvement", "below best practice", or "recommended action" instead.
> - **Disclaimer (required — DO NOT SKIP):** Immediately after the overall metric card (Step 3c), you MUST output the following disclaimer verbatim in italics. Copy it exactly as shown — do not shorten or omit any part:
>   *This report is intended to help you evaluate your account against a set of recommended well-architected framework best practices; it is not a comprehensive audit. Results may vary based on the model chosen by the user.*

#### 3a. Merge Results

Subagent results already include `name`, `domain`, `value`, and `remediation` fields for each control and check. No YAML re-read is needed. Simply concatenate subagent pillar results with manual-phase results (which the main agent already graded with full metadata). Do NOT output any explanation of the merge process or how scores were calculated.

**Merge rules:**
- For **manual-only controls** (all checks are `type: manual`): the main agent already graded the control by matching the user's answer to the threshold. Use that result directly. These controls are NOT included in subagent output.
- For **automated-only controls**: use the subagent's control-level grade directly (already enriched with name, domain, remediation).
- For **mixed controls** (both manual and automated checks): re-grade the control holistically by combining the manual check result and the subagent's automated check results, then matching against the control's `thresholds`. The thresholds for mixed controls describe combined criteria.

**Mixed-control grading examples:**
- **OPEX-10 (IaC in Use):** Automated check finds 0 Git repos (needs_attention). Manual check: user says "No — all manual" (needs_attention). Both signals align → control = `needs_attention`.
- **OPEX-10 (IaC in Use):** Automated check finds 2 Git repos (on_track signal). Manual check: user says "Partial — some adoption" (needs_improvement signal). Threshold says "User confirms active IaC usage, or Git repositories exist with evidence of automated deployments" — repos exist but user confirms partial → control = `needs_improvement`.
- **REL-3 (Cross-Region DR):** Automated check finds 2 distinct regions (on_track signal). Manual check: user says "Yes — different region/cloud" (on_track). Both align → control = `on_track`.

#### 3b. Build the Canonical JSON Object (Single Source of Truth)

> **CRITICAL — prevents score mismatches between inline and HTML output.**
>
> Before outputting ANY metric cards, tables, or charts, you MUST first assemble the complete JSON object
> (same schema as Step 5's "Minimal JSON Schema") containing the final `controls` and `checks` arrays
> for every pillar — including merged manual-only controls.
>
> **How to build it:** After merging in Step 3a, construct the full JSON structure with all pillars,
> controls, and checks. Then **count directly from this structure** to derive all scores:
> - Per-pillar on_track = count of controls where `result === "on_track"` in that pillar's `controls` array
> - Per-pillar total = length of that pillar's `controls` array
> - Overall on_track = sum of per-pillar on_track counts
> - Overall total = sum of per-pillar totals
> - Overall % = round(overall on_track / overall total * 100)
>
> Do NOT manually tally or estimate scores. Always count from the arrays.
>
> **Reuse this exact JSON in Step 5** (HTML export) — do not rebuild it. This guarantees the inline
> CoCo numbers and the HTML report show identical scores.

#### 3c. Overall Score (metric card)

> **Scoring note:** Scoring is based on **controls**, not individual checks.
> Each control is graded holistically using its YAML `thresholds` after evaluating all nested checks.
> The overall score = controls on track / total controls.
> **Derive all scores by counting from the canonical JSON built in Step 3b — never by mental arithmetic.**

Use `visualize_data` metric_card calls **sequentially** (not in parallel) to ensure correct rendering order:
- **Card 1** (always output first): Overall score only — one metric (label: "Overall On Track", value: "X%", subvalue: "X/Y controls on track"). This card MUST contain only the overall score and nothing else.
- **Card 2**: Security & Governance, Operational Excellence, Reliability (label: pillar name, value: "X/Y (Z%)").
- **Card 3**: Cost Optimization, Performance (label: pillar name, value: "X/Y (Z%)").

If fewer than 5 pillars are assessed, distribute them across Card 2 and Card 3 in the same order (Security & Governance first, then Operational Excellence, Reliability, Cost Optimization, Performance), skipping any that were not selected. Never put more than 3 pillars on a single card.

Do NOT include pillar scores in the Overall card. Do NOT duplicate the overall score. The metric_card tool has a hard limit of 4 items — never exceed it.

#### 3d. Top Needs Improvement Items

Output a markdown section:
```
### Top Needs Improvement Items

| Control | Pillar | Issue & Remediation |
|---------|--------|---------------------|
| MFA Enforcement | Security | ... |
| ... | ... | ... |
```
Collect all NEEDS_ATTENTION and NEEDS_IMPROVEMENT **controls** from the merged results across all pillars, sort by severity (NEEDS_ATTENTION first, then NEEDS_IMPROVEMENT).
Use the control **name** (not the ID) in the table.
Take the top 6 overall across all pillars.

**Remediation tone:** For the "Issue & Remediation" column, write the issue finding first, then append a friendly suggestion:
- If the YAML `remediation` field contains a `CoCo Prompt: "/skill-name ..."` pattern, rephrase as: "Try prompting CoCo to run the /skill-name skill to [action]."
- If the remediation is a general instruction, rephrase in a suggesting tone (e.g., "Consider enabling..." instead of "Enable...").
- If no remediation field exists, generate a best-effort suggestion based on the issue.

#### 3e. Per-Pillar Detail Tables

For **each assessed pillar**, output:

1. A markdown heading: `### <Pillar Name> — X/Y (Z%)` (X/Y = controls on track / total controls)
2. A `visualize_data` bar chart showing domain on-track rates (xKey=domain name, yKey=on-track percentage)
   - Domain percentage = (on_track controls in domain / total controls in domain) * 100
3. A **Control Summary** table (scoring units) with 5 columns — Control, Domain, Result, Value, Remediation:

```
| Control | Domain | Result | Value | Remediation |
|---------|--------|--------|-------|-------------|
| Network Policies | Network Security | 🟢 On Track | Account-level NP active without wildcard | |
| MFA Enforcement | Authentication | 🟠 Needs Attention | 33% MFA enrolled | Consider enabling MFA by default and setting ENABLE_MFA_BY_DEFAULT = TRUE. |
| ... | ... | ... | ... | ... |
```

**Remediation column rules:**
- For every non-on-track control, populate using the YAML `remediation` field rephrased in a friendly suggestion tone.
- If the remediation contains a `CoCo Prompt:` reference, mention that the user can prompt CoCo with that skill.
- Leave the Remediation column blank only for on-track controls.
- This column is the primary actionable output for the customer — never skip it.

4. **Ask** the user ONCE (for all pillars combined, not per-pillar) if they want the check-level detail table using `ask_user_question`:

```
Header: "Check Detail"
Question: "Would you like to see the full check-level detail table (individual checks per control)?"
Options:
  - "Yes" → Output the check detail table below
  - "No" → Skip to summary line
```

✋ **STOP** — wait for `ask_user_question` response before proceeding.

If **Yes**, output a **Check Detail** table (supporting evidence per control):

```
| Check | Control | Result | Value |
|-------|---------|--------|-------|
| User-level NP coverage | Network Policies | 🟠 Needs Attention | 0% coverage |
| Account-level NP without wildcard | Network Policies | 🟢 On Track | Active (ACCOUNT_VPN_POLICY_SE) |
| ... | ... | ... | ... |
```

Use colored indicators: 🟢 On Track, 🟡 Needs Improvement, 🟠 Needs Attention

#### 3f. Summary Line

End with:
> "WAF Assessment complete: **<OVERALL_ON_TRACK>/<OVERALL_TOTAL> controls on track (<OVERALL_PCT>%)**."

These values MUST match the canonical JSON from Step 3b (same counts used in the metric cards).

✋ **STOP** — Present results to user before proceeding.

### Step 4: Offer HTML Export

After presenting all results inline, ask the user:

Use `ask_user_question`:
```
Header: "HTML Report"
Question: "Would you like to generate a shareable HTML report file?"
Options:
  - "Yes" → Proceed to Step 5
  - "No" → Stop here
```

✋ **STOP** — wait for `ask_user_question` response before proceeding.

If **No**: End the workflow. The inline output is the deliverable.

If **Yes**: Ask where to save the file using `ask_user_question` with `type: "text"`:
```
Header: "Save Location"
Question: "Where should the HTML report be saved?"
type: text
defaultValue: "~/Downloads"
```

Use the provided path (resolve `~` to the user's home directory) and proceed to Step 5.

### Step 5: Generate HTML Report (Optional)

Produce a single self-contained HTML file by reading the template and injecting minimal JSON.

1. **Read** the HTML template at `<SKILL_DIR>/assets/waf_report_template.html`
2. **Reuse** the canonical JSON object already built in Step 3b — do NOT rebuild or re-derive it. This guarantees the HTML shows the same numbers as the inline output.
3. **Replace** the string `__WAF_DATA_PLACEHOLDER__` in the template with the JSON string
4. **Write** the result to `<SAVE_LOCATION>/waf_assessment_<CUSTOMER_SLUG>_<YYYYMMDD>.html`
5. **Open** the file with `open_browser`

> `<CUSTOMER_SLUG>` = lowercase customer name with spaces/special chars replaced by hyphens (e.g., "Acme Corp" → "acme-corp").

**If the target directory doesn't exist**, create it with `mkdir -p` before writing.

#### Minimal JSON Schema

The template JS computes all derived fields (badges, colors, domain groupings, percentages, top failures). CoCo only provides raw data:

```json
{
  "customer_name": "<from Step 1>",
  "account_name": "<from Step 1>",
  "edition": "<from Step 1>",
  "date": "<today's date>",
  "pillars": [
    {
      "pillar": "<PILLAR_KEY>",
      "pillar_name": "<PILLAR_DISPLAY_NAME>",
      "controls": [
        {
          "name": "<control name from YAML>",
          "domain": "<domain from YAML>",
          "result": "<on_track|needs_improvement|needs_attention>",
          "value": "<short summary>",
          "remediation": "<friendly suggestion for non-on-track; empty string for on_track>"
        }
      ],
      "checks": [
        {
          "name": "<check name from YAML>",
          "domain": "<domain from YAML>",
          "control": "<parent control name>",
          "result": "<on_track|needs_improvement|needs_attention>",
          "value": "<short summary>"
        }
      ]
    }
  ]
}
```

Confirm:
> "HTML report saved to `<filename>`."

## Stopping Points

- ✋ **Step 1** — Wait for `ask_user_question` (edition + pillar selection combined)
- ✋ **Step 2b** — Wait for `ask_user_question` manual check answers, then call `wait_agent` to collect subagent results
- ✋ **Step 3** — Present full results inline in CoCo
- ✋ **Step 3e** — Ask user if they want check-level detail table (use `ask_user_question`)
- ✋ **Step 4** — Ask user if they want an HTML file (use `ask_user_question`)

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| Permission error on SQL queries | Active role lacks ACCOUNT_USAGE access | `USE ROLE ACCOUNTADMIN;` |
| Template file not found | Wrong SKILL_DIR path | Verify with `ls <SKILL_DIR>/assets/waf_report_template.html` |
| Subagent returns "needs_attention" for all checks | Snowflake connection inactive or wrong role | Confirm connection and re-run `USE ROLE ACCOUNTADMIN;` |

## Files

| File | Purpose |
|------|---------|
| `assets/waf_report_template.html` | HTML template with `__WAF_DATA_PLACEHOLDER__` — JS enriches minimal JSON |
| `references/subagent_prompt_template.md` | Prompt template for pillar subagents |
| `references/controls/security_governance.yaml` | Security & Governance checks (all automated) |
| `references/controls/operational_excellence.yaml` | Operational Excellence checks (automated + manual) |
| `references/controls/cost_optimization.yaml` | Cost Optimization checks (automated + 1 manual) |
| `references/controls/reliability.yaml` | Reliability checks (automated + manual) |
| `references/controls/performance.yaml` | Performance checks (all automated) |
| `references/controls/TEMPLATE.yaml` | Template for creating new pillar control YAML files (not processed during assessment) |

## Output

- **Primary:** Inline report in CoCo (metric cards, bar charts, markdown tables)
- **Optional:** Single self-contained HTML report file (no sibling files)
