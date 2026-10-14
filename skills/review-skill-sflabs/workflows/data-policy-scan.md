# Workflow: Data Policy Scan

> Reason about the target skill against `references/data-policy-principles.md` to surface findings about output content design, tone, hardcoded specifics, and disclaimers. No closed list of bad patterns — the agent applies judgment using the principles file as ground truth.

## Inputs

- `<skill_dir>` — path to the target skill directory.
- `references/data-policy-principles.md` — the principles file (loaded into context first).
- `SKILL.md` body and any sibling files under `workflows/`.

## Outputs

A list of findings, each with:

- `check_id` — `policy.<class>.<short-name>` (e.g. `policy.pii.user_name`)
- `severity` — `🟢` / `🟡` / `🟠`
- `evidence` — what was found, with file:line where possible
- `suggested_change` — the principles-aligned alternative

## Steps

### Step 1: Load principles into context

Read `references/data-policy-principles.md` end-to-end. Every reasoning step below references it.

### Step 2: Decide what content to scan

If the target skill has only `SKILL.md` (no `workflows/` directory), scan `SKILL.md` body in full.

If the target skill is a router with sub-workflows (any `workflows/<x>.md` referenced from the body), use **spot-check sampling**:

- Always scan `SKILL.md` body in full.
- Scan up to 3 workflow files, prioritizing those that look most likely to surface output (filenames containing `report`, `recommend`, `analyze`, `audit`, `summary`, or that are referenced from sections about output formatting).
- Skip workflows that are clearly not output-producing (e.g. `setup.md`, `prerequisites.md`).

This bounds token cost while still catching the common policy violations.

### Step 3: Output content design (per principles §1 and §2)

Read the body and the sampled workflows. Identify any place where the skill instructs the agent to surface fields from principles §1's "PII fields to flag" list. Examples of flag-worthy mentions:

- The skill's example output, report template, or SQL examples reference any user-identity column (e.g. `USER_NAME`, `LOGIN_NAME`, `EMAIL`, `DISPLAY_NAME`).
- The skill's body says to display per-user breakdowns where an aggregate would answer the question.
- The skill returns raw query history rows joined to user identity.

For each match, emit:

```
{check_id: "policy.pii.<field>", severity: "🟠",
 evidence: "<file>:<line> instructs surfacing <field>",
 suggested_change: "Replace with an aggregate (count, distribution, breakdown by <non-user dimension>) per principles §2."}
```

If the skill's body shows that PII is collected internally but never appears in output, that is **acceptable** — the policy is about output, not access. Note this in evidence and emit `severity: 🟢` if the design clearly aggregates before surfacing.

### Step 4: Tone and severity language (per principles §4 and §5)

Run a case-insensitive search across the body and sampled workflows for:

- `\bcritical\b`, `\bfailure\b`, `\bviolation\b`, `\bdanger\b`, `\burgent\b`, `\bhigh-risk\b`
- `🔴` emoji
- the literal word `red` in a status-indicator context (table cell, status column, severity label)

For each match, emit:

```
{check_id: "policy.tone.<word>", severity: "🟡",
 evidence: "<file>:<line>: '<matched text in context>'",
 suggested_change: "<replacement word from principles §4 table>"}
```

If a banned word appears in a "do not use" instruction context (e.g. principles §4 itself), that is acceptable — recognize the banned-context pattern (the surrounding sentence forbids the word) and skip it. The agent must distinguish between *using* the word and *forbidding* the word.

### Step 5: Hardcoded specifics (per principles §3)

Look for hardcoded customer identifiers in the body and sampled workflows:

- Account locators (any string matching common account identifier patterns, in SQL examples or instructions)
- Specific schema names with identifying suffixes (`_PROD`, `_DEV`, `_FINANCE`, named-team patterns) used as if they were universal
- Specific role names, warehouse names, integration names that are not introduced as parameters
- Anything that looks like a credential, API token, secret key, or password

Apply judgment: a generic example like `MY_SCHEMA` or `<your_warehouse>` is fine. A specific name like `ACME_FINANCE_PROD_RL` baked into a SQL example is not.

For each match:

```
{check_id: "policy.hardcoded.<kind>", severity: "🟠",
 evidence: "<file>:<line>: <matched identifier>",
 suggested_change: "Parameterize: replace with a placeholder (e.g. <your_warehouse>) and instruct the user to supply their own value."}
```

Anything that looks like a credential or secret is a **hard error** — emit `severity: 🟠` and recommend the contributor remove the secret entirely before proceeding (and rotate it if it was a real secret).

### Step 6: Disclaimer presence (per principles §6)

Decide whether the skill produces assessment-style output. Indicators:

- Body contains words like `score`, `rating`, `recommend`, `audit`, `assessment`, `evaluation`
- Body describes output that surfaces telemetry or metrics
- Body produces a report-shaped artifact (sections, summaries, traffic-light statuses)

If yes, search the body, the workflow files sampled, and any `references/report-template.md` for one of the disclaimer templates from principles §6. If no disclaimer is present, emit:

```
{check_id: "policy.disclaimer_missing", severity: "🟡",
 evidence: "Skill produces an assessment-style output but no disclaimer is included.",
 suggested_change: "Add the most relevant template from principles §6 near the top of the rendered output."}
```

If the skill is purely action-oriented (executes a task, returns a result, but is not advisory in nature), skip this step and emit `severity: 🟢` with `evidence: "Not assessment-style output."`.

### Step 7: Flexibility / fork pattern (per principles §7)

Reason about whether the skill is parameterized or hardcoded for a single use case. Indicators of a hardcoded skill that will produce forks:

- Single account/scope assumed throughout, no parameter for filtering
- Single output detail level (deep-dive only, or summary only) with no mode switch
- Single environment (e.g. only handles production accounts) with no env parameter

If found, emit:

```
{check_id: "policy.scope_inflexible", severity: "🟡",
 evidence: "<short description of the assumed-fixed scope>",
 suggested_change: "Accept a parameter for <dimension>; principles §7 explains why hardcoded scope encourages forks."}
```

This is a **🟡 Opportunity for Improvement**, not a 🟠. A hardcoded skill is still useful — it just has a higher chance of being copied and edited later, which the contributor should know up front.

## Summary status

After running steps 3-7, the workflow's overall status:

- 🟢 — zero findings
- 🟡 — only `🟡 Opportunity for Improvement` findings
- 🟠 — at least one `🟠 Needs Attention` finding (PII surfacing, hardcoded customer identifier, secret)

Pass that status to the router.

## Notes

- The agent does not maintain a closed list of "bad" identifiers or fields beyond what principles §1 enumerates. New patterns appear; the principles file is the source of truth and updates centrally.
- Examples in the body that use clearly-generic placeholders (`<your_db>`, `MY_TABLE`, `EXAMPLE_ROLE`) are not findings.
- When in doubt, prefer the advisory tone over silence: surface the finding, let the contributor decide whether it applies.
