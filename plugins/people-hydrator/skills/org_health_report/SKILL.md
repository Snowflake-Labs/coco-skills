---
name: org_health_report
description: >
  Org Health skill for People Analytics. Provides a full org health report
  (4-KPI dashboard), team health across six analysis areas (Retention & Attrition,
  Performance & low-performer (LP) outcomes, Team Structure & Span, Well-Being / PTO, AI Adoption,
  Recruiting), structural context metrics, and dynamic org benchmarks. Auto-resolves
  the session user's identity and routes to the appropriate org scope. Triggers:
  org health report, org health for [name], org health summary, executive scorecard,
  manager scorecard, attrition for [name]'s org, high-performer (HP) retention, HP retention, LP outcomes, span
  of control, PTO utilization, AI adoption for team, new hire attrition, org health
  snapshot.
  Do NOT use for: company-wide headcount sizing (use headcount_insights),
  company-wide attrition trends (use attrition_insights), or evaluating a
  candidate (use interview_debrief).
license: Proprietary. See LICENSE.txt for complete terms
---

# Org Health Report (Dehydrated)

## About this skill

- **What it does:** An org health report for a leader's team across retention and attrition, performance outcomes (high performers retained, low performers exited), team structure and span, well-being (PTO), AI adoption and recruiting, compared with peer orgs.
- **Who it's for:** People leaders and the HR Business Partners who support them.
- **Works with:** Goes deeper than `attrition_insights` and `headcount_insights` for one leader's org. For company-wide headcount use `headcount_insights`; for a candidate use `interview_debrief`.
- **Before you start:** This skill runs on the data and definitions connected by **People Hydrator** (`plugins/people-hydrator/`). If it hasn't been set up for your account yet, run People Hydrator first.
- **How to use it:** Say "org health report" (for your own org) or "org health for [name]". You can choose direct reports, full org or company.
- **If you don't get what you expect:** If a section is missing or says data isn't available, that data wasn't connected during setup: re-run People Hydrator to add it. If a number looks wrong, tell People Hydrator what you expected; it turns that into a checked correction. If the skill says a table or column has changed, re-run People Hydrator for this skill.
- **Good to know:** The skill finds you in the employee directory to set your default scope. HP = high performer and LP = low performer, based on your performance ratings. Sections whose data wasn't connected (for example AI adoption or PTO) are marked as not available. The skill reads your data and never changes it.

## Intent

Provide people leaders and HR Business Partners with a complete, consistent view of org health across six dimensions: retention, talent performance, team structure, well-being, AI adoption, and recruiting. Auto-resolves the session user's identity to scope analysis to their management chain by default. The **Org Health Report** is the flagship output — a fixed 8-section template that must be rendered identically every time it is requested, regardless of the data values.

Not for: company-wide headcount sizing (use `headcount_insights`), individual candidate evaluation (use `interview_debrief`), or bulk attrition trending (use `attrition_insights`).

## Behavior

### Audience & Auto-Identity Resolution (Step 0 — MANDATORY)

Before any analysis query, auto-resolve the session user's identity:
1. Query the employee directory for the current session user.
2. Capture: employee ID, full name, management level, reporting chain context (who they report to).
3. Store for use as the default scope in all subsequent queries.
4. Greet the user: "Hi [Full Name], which analysis would you like to run?"
5. If identity resolution returns zero rows: abort with "I couldn't find you in the employee directory, so I can't scope this to your org. Ask the person who set up these skills to check the directory connection in People Hydrator."

**Do not skip Step 0.** All analysis queries require the resolved employee ID as the default scope anchor.

### Scope Selection (Step 1)

After identity resolution, determine the scope of analysis:

| User Says | Scope | Filter |
|-----------|-------|--------|
| "Direct reports" / "team" | Direct | Manager filter: immediate direct reports only |
| "Full org" / "organization" | Extended | All-levels filter using the leader's employee ID across the management chain |
| "Company" / "all" | Company | No filter |
| Not specified | Default: **Full Org** for leaders; **Direct** for frontline managers |

### Analysis Menu (Step 2)

When the user hasn't specified a category, present the following options via interactive selector (do NOT render a markdown table):

| Option | What It Covers |
|--------|---------------|
| **Org Health Report** | 4-KPI dashboard, full org snapshot with priority actions — the flagship 8-section report |
| **Attrition & Retention** | T12M attrition, vol/invol splits, regrettable exits, HP retention, new hire attrition |
| **Performance & LP Outcomes** | LP/HP outcomes, exit velocity, repeat LPs, stagnation, off-cycle detection, rating trends |
| **Team Structure & Span** | Org tree, span of control, over/under-spanned managers, new managers, growth rate |
| **PTO & Well-Being** | PTO utilization, burnout risk composite, low-PTO individuals |
| **AI Adoption** | Adoption %, daily active rate, tool breakdown, non-adopter list |

If the user's intent is clear from their message, skip the menu and route directly to the correct analysis.

### Org Health Report — Locked Template (NON-NEGOTIABLE)

**The Org Health Report is the highest-priority output in this skill.** When any of the following trigger phrases are detected, the report MUST follow the exact structure below — no reordering, no merging, no added sections:

**Trigger phrases:** "show me my org health", "org health for [name]", "org health report", "org health snapshot", "org health summary", "executive scorecard", "scorecard", "how is [name]'s org doing", any phrase containing "org health".

**Output structure (8 sections, always in this order):**

```
## Org Health Report: [LEADER_NAME]
[Scope] | [Date] | [Data Freshness]

### Section 1: Health Summary
| KPI | Value | vs Peer Org | YoY Δ | Status |
|-----|-------|-------------|-------|--------|
| HP Retention Rate | | | | |
| LP Exit Rate | | | | |
| PTO Risk % | | | | |
| AI Daily Active % | | | | |

### Section 2: Direct Reports
| Name | Level | Tenure | Status |
|------|-------|--------|--------|

### Section 3: Structural Context
| Metric | Value | vs Company | Status |
|--------|-------|-----------|--------|
[Team size, manager %, avg span, new manager %, growth rate]

### Section 4: Talent Performance
| Metric | Value | vs Peer Org | YoY Δ | Status |
|--------|-------|-------------|-------|--------|
[HP retention rate, LP exit rate, rating distribution, promotion rate]

### Section 5: Retention & Attrition (T12M)
| Metric | Value | vs Peer Org | YoY Δ | Status |
|--------|-------|-------------|-------|--------|
[Total attrition, voluntary, regrettable, organic (ex-RIF), new hire attrition]

### Section 6: Well-Being (PTO)
| Metric | Value | vs Peer Org | YoY Δ | Status |
|--------|-------|-------------|-------|--------|
[Avg PTO days, PTO utilization %, risk tier breakdown]

### Section 7: AI Adoption
| Metric | Value | vs Peer Org | YoY Δ | Status |
|--------|-------|-------------|-------|--------|
[Adoption %, daily active %, tool breakdown]

### Section 8: Recruiting
| Metric | Value | Status |
|--------|-------|--------|
[Open reqs, pending starts, offer acceptance rate]
```

**Template rules:**
1. Sections 1–8 are always present. Empty fields show "—", never blank.
2. Tables use the exact column headers shown — no additions, removals, renames, or reorders.
3. Status indicators (✅ 👁️ ⚠️ 🔴) on every row — do not substitute other emoji.
4. Commentary (one-liner insight under each section) may vary based on data; everything else is locked.
5. Do NOT add new sections, executive summary paragraphs, charts, or narrative acts.
6. If the user asks for a different format (e.g., "give me a narrative"), treat that as a separate follow-up in conversational mode — do NOT alter the locked template for the initial output.
7. Never include internal source names, table names, column names, or schema details in any section.

### Execution Protocol — Two-Phase Parallel Execution

The Org Health Report requires two phases:

**Phase 1 (parallel):** Identity resolution + warehouse warmup in a single message with multiple parallel queries:
- Resolve leader identity (employee ID, management level, org context)
- Warm the warehouse

**Phase 2 (parallel):** All metric queries in ONE message as parallel tool calls. Do NOT run them sequentially. The following independent blocks run simultaneously:
- Team structure / direct reports
- Structural context metrics
- Talent performance (HP retention, LP exit rate)
- Retention & attrition bundle (total, vol, regrettable, organic, NHA)
- Well-being / PTO bundle
- AI adoption bundle
- Recruiting pipeline
- Open requisitions detail
- Prior year (YoY) comparison
- Peer org benchmark (omit for top-level leaders who have no peer org)

**Anti-patterns (forbidden):**
- Running blocks sequentially (one at a time) — multiplies wall-clock time by N.
- Splitting Phase 2 across multiple assistant messages.
- Rendering the report with "—" in comparison columns because the benchmark query was skipped.
- Guessing or hardcoding benchmark values instead of querying the pre-built benchmark table.

### Guard Rails (MANDATORY Correctness Rules)

| # | Guard Rail | Wrong Pattern | Correct Pattern |
|---|-----------|---------------|-----------------|
| 1 | HP Retention Rate is cohort-based | `hp_retained / total_exits` | `(hp_cohort - hp_exits) / total_hp_cohort` for rated employees (top ratings) |
| 2 | LP Exit Rate is cohort-based | `lp_exits / total_exits` | `lp_exited / total_lp_cohort` (bottom ratings) |
| 3 | Cycle discovery before performance queries | Assume cycles exist | Run cycle discovery first; use only cycles with active rated employees for the target org |
| 4 | RIF check before attrition grading | Present raw attrition as organic | Run RIF detection first; show both raw and organic attrition if RIF exits detected |
| 5 | Financial year starts the first day of the customer's FY | Calendar year | Use customer's fiscal calendar for all period math |
| 6 | All-levels lookup uses employee ID pattern | Name-based ILIKE match | Use employee ID-based management chain filter |
| 7 | Off-cycle orgs have no LP data in some quarters | Report "0 low performers" | Run cycle discovery; surface as "off-cycle — no review conducted" |
| 8 | LP exit dates must be during/after the review cycle start | Count historical exits from prior stints | Filter termination date >= start of the review period |
| 9 | Attrition scoping uses historical snapshot | Current-state snapshot for T12M | Use the historical snapshots at period start and period end; denominator = (HC_start + HC_end) / 2 |
| 10 | HP cohort uses rolling L12M window | Hardcoded cycle dates | Use a 12-month rolling window from current date for headline HP metrics |
| 11 | Per-employee LP tracking anchors to a specific cycle | Aggregate LP status across cycles | Track each LP per review cycle independently; partition by cycle end date |
| 12 | Predecessor mapping for dissolved orgs | Report exits under departed leader | Map dissolved org leaders to their successor leaders in all attribution queries |
| 13 | LP improved then exited = "improved then exited", NOT "resolved" | Count improvement as resolution if employee later exits | Resolution requires the employee to still be active |
| 14 | `active_status` is an integer | `WHERE active_status = TRUE` | `WHERE active_status = 1` |
| 15 | Standard employee type filter | Filter only on 'Regular' | Include all standard types; exclude interns and contingent workers unless explicitly requested |
| 16 | Headcount YoY uses constant-cohort for org-inherited leaders | Hierarchy-at-time for all leaders | For reorged leaders, pin membership to today's org and trace backward — avoids artificial jumps from reorgs |
| 17 | Top-level leaders skip the peer benchmark query | Submit benchmark for all leaders | Detect top-level leaders; omit peer comparison columns for them |

### Analysis Subskill Details

#### Attrition & Retention

Core metrics: total T12M attrition rate, voluntary rate, involuntary rate, regrettable rate, organic attrition rate (excluding RIF), new hire attrition (NHA).

**NHA definition:** Attrition among employees hired within the last 12 months. Track as a cohort: hires in the T12M window, exits from that cohort in the same window.

**RIF detection:** Before computing organic attrition, check whether involuntary exits in the period include any reduction-in-force events. If RIF exits are detected, surface both raw attrition and organic attrition (excluding RIF) and flag which metric the leader should focus on.

**HP Retention Rate:** `(hp_cohort_size - hp_exits_in_period) / hp_cohort_size × 100`. HP cohort = employees rated in the top performance tiers. Use rolling L12M window.

#### Performance & LP Outcomes

For each available review cycle:
- LP Exit Rate: `lp_exited / total_lp_cohort × 100` (cohort-based)
- LP resolution rate: employees who improved their rating in the subsequent cycle and are still active
- LP improved-then-exited: a separate category (not "resolved")
- Repeat LPs: employees rated low in 2+ consecutive cycles
- Exit velocity: distribution of how many days after a low performance rating employees exited (buckets: ≤60 days, 61–90 days, 91–180 days, 181+ days)

**Off-cycle detection:** Run cycle discovery first. If an org has no review cycle data for a given quarter, surface "off-cycle — no review conducted" rather than "0 low performers".

#### Team Structure & Span

- Team size, manager %, avg span of control
- Span distribution: 0 DRs, 1–2 DRs, 3–9 DRs, 10+ DRs
- Healthy span: 5–8 direct reports (✅); 3–4 or >8–10 (🟡); <3 or >10 (🔴)
- Outliers: ICs with direct reports (unusual), managers with zero reports
- Growth rate: current HC vs prior-year HC for the leader's org (constant-cohort method)

#### Well-Being / PTO

- Average PTO days taken in the L12M
- PTO utilization % (days taken / days allotted × 100)
- Burnout risk composite: share of employees with PTO utilization < a configurable low-PTO threshold
- New hire PTO bands: PTO utilization for employees in their first 6 months

#### AI Adoption

- Adoption %: share of team members who have used any AI tool in the trailing 90 days
- Daily active %: share who used an AI tool on an average day in the trailing 30 days
- Tool breakdown: adoption by specific AI tool (configurable list at customer setup)
- Non-adopter list: employees with zero AI usage in the trailing 90 days

#### Recruiting Pipeline

- Open requisitions: count by status (approved, draft, on hold)
- Pending starts: future hires already accepted with a confirmed start date
- Offer acceptance rate for the trailing period

### Benchmark Comparisons

The Org Health Report includes comparison columns for the leader's peer org (the executive-level org group) and for prior year. These comparisons require:
1. A pre-built benchmark table that aggregates the same metrics at the peer org level — query this table rather than computing from raw data (to keep Phase 2 fast).
2. A prior-year bundle query that computes T12M metrics shifted 12 months back.

**Top-level leader exception:** Leaders at the very top of the org hierarchy (whose peer org IS the company) skip the peer org comparison. Detect during identity resolution and omit comparison columns accordingly.

**Rendering rules for comparison columns:**
- `Peer Org` column: show the peer-org aggregate for rate metrics (%, days). Show "—" for absolute counts.
- `Δ vs Peer Org` column: show `[±X.X]pp` for percentages, `[±X.X]d` for days.
- `Prior Yr` column: show the same metric from the T12M window shifted 12 months back. "—" if unavailable (new org).
- `YoY Δ` column: directional arrow + magnitude (`↑ +X.Xpp` or `↓ -X.Xpp`). Use `→` for <0.5pp change.

### Confidence Handling

- **HIGH confidence:** All Phase 2 blocks return data. Render full 8-section report.
- **MEDIUM confidence:** Core attrition and headcount resolve; performance cycle data or AI adoption data is thin. Run with affected sections marked "(limited data)".
- **LOW confidence on non-critical bindings** (AI adoption, recruiting pipeline): Render with those sections noting "data unavailable".
- **LOW confidence on critical bindings** (employee snapshot, exit data): Skip org health report; output the data domain description and recommend connecting HRIS and exit data.

### Output Mode

**Mode A — Structured Tabular:** For all initial Org Health Report outputs and first-time analysis requests. Tables with status indicators.

**Mode B — Conversational:** For focused follow-up questions ("tell me more about the HP retention drop"). 2–5 sentence responses with inline data.

### Error Handling

| Scenario | Response |
|----------|---------|
| Identity resolution returns 0 rows | "I couldn't find you in the employee directory, so I can't scope this to your org. Ask the person who set up these skills to check the directory connection in People Hydrator." |
| Phase 2 query returns 0 rows for a section | Render "No data available" for that section's rows — do NOT drop the section heading |
| Authorization / permissions error | "I wasn't able to pull data right now — this usually means your account needs a permissions update. Reach out to your People Analytics team." Never expose schema or error text. |
| Query timeout | "This is taking longer than expected. Want me to try again?" |
| Peer benchmark returns 0 rows for a non-top-level leader | Re-run the benchmark query before rendering — do NOT render comparison columns as "—" for a non-top-level leader |

## Quality

This skill is tested against `evals/org_health_report/golden_cases.md` and `evals/org_health_report/eval_plan.md` (People Hydrator runs them in its Evaluate stage). They are tests, not runtime instructions: follow this file and its customer configuration (added at hydration).

## Context Queries

- A current employee snapshot at one row per employee per snapshot date, with columns for employee ID, full name, management level, employment status, employee type, active flag, number of direct reports, manager name, management chain levels (by name and ID, as deep as the customer's hierarchy provides), cost center ID, tenure, and hire date — used for identity resolution, team structure, span of control, and all-levels management chain filtering.

- An exit records table at one row per exit, with employee ID, termination date, termination category (voluntary / involuntary), regrettable flag, termination reason, and management chain IDs — used for attrition and retention analysis.

- A performance ratings table at one row per employee per review cycle, with employee ID, cycle identifier, cycle end date, performance rating (ordinal scale), and management chain IDs at review time — used for LP/HP cohort tracking, exit velocity, and repeat LP detection.

- A well-being / PTO usage table at one row per employee per period, with employee ID, PTO days taken, PTO allotment, and period — used for PTO utilization and burnout risk composite.

- An AI tool usage table at one row per user per tool per day (or aggregated to weekly), with employee ID, tool name, active flag, and date — used for AI adoption %, daily active %, and non-adopter list.

- A recruiting / open requisitions table at one row per open req, with req ID, job title, status (approved / draft / on hold), hiring manager, team, and open date — used for recruiting pipeline section.

- A starts / new hires table at one row per hire event, with employee ID, hire date, and management chain IDs — used for pending starts and new hire attrition (NHA) cohort.

- A pre-built peer org benchmark table at one row per executive-level org, with pre-aggregated rate metrics (attrition, HP retention, LP exit, PTO, AI adoption, span) — used for the Peer Org and Δ vs Peer Org comparison columns in Phase 2. Refreshed on a scheduled cadence.

- A prior-year comparison bundle: the same metrics from the T12M window shifted 12 months back, used for the Prior Yr and YoY Δ columns.

- (Optional) A quality-of-hire / new hire survey table at one row per post-hire survey response, with a numeric rating (1–5) and employee ID — used for new hire satisfaction trends.

- (Optional) A semantic view over the employee snapshot and exit tables for Cortex Analyst-driven follow-up questions like "show me HP retention trend for [leader]'s org over the last 4 quarters".
