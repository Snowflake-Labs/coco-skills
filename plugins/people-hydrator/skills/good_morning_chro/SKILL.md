---
name: good_morning_chro
description: >
  Daily CHRO executive briefing: attrition watch (new exits, trending orgs),
  hiring velocity (offers out, acceptances), engagement signals (pulse survey,
  PTO risk), and workforce hot spots needing attention. Triggers: good morning
  CHRO, people pulse, daily HR update, workforce health, what's happening in HR,
  morning briefing, daily people update.
  Do NOT use for: deep attrition, headcount or org health analysis (use
  attrition_insights, headcount_insights or org_health_report), individual
  employee lookups, or compensation analysis.
license: Proprietary. See LICENSE.txt for complete terms
---

# Good Morning CHRO (Dehydrated)

## About this skill

- **What it does:** A one-page daily briefing on workforce health: new exits and attrition hot spots, hiring velocity (offers out, accepted, declined), engagement signals, and orgs that need attention.
- **Who it's for:** The CHRO / CPO and People leadership.
- **Works with:** A summary built on the same data and definitions as `attrition_insights`, `headcount_insights` and the recruiting data, so its numbers match theirs. Use those skills to go deeper on anything the briefing flags.
- **Before you start:** This skill runs on the data and definitions connected by **People Hydrator** (`plugins/people-hydrator/`). If it hasn't been set up for your account yet, run People Hydrator first.
- **How to use it:** Say "good morning CHRO" or "people pulse". Name an org to scope it ("good morning, just engineering").
- **If you don't get what you expect:** If a section is missing or says data isn't available, that data wasn't connected during setup: re-run People Hydrator to add it. If a number looks wrong, tell People Hydrator what you expected; it turns that into a checked correction. If the skill says a table or column has changed, re-run People Hydrator for this skill.
- **Good to know:** Engagement, PTO and offer sections are optional: if that data wasn't connected, the briefing leaves the section out and says so. The skill reads your data and never changes it.

## Intent

Daily briefing for the CHRO and People leadership. Covers: attrition watch (new exits in last 24h, trailing 12-month (T12M) rate by org, regrettable-loss flags), hiring velocity (offers extended/accepted/declined this week), engagement signals (latest pulse score or PTO utilization anomalies), and hot spots (orgs with attrition > 2x company average or hiring stalled > 14 days).

Designed to replace the morning routine of checking HRIS dashboards, recruiting pipeline tools, and engagement survey results separately. Produces one 1-page view of workforce health.

This skill is for executive situational awareness. Not for: deep HR analytics (use `attrition_insights`, `headcount_insights`, `org_health_report`), individual employee lookups, or comp-band analysis.

## Behavior

### Routing Logic

**MANDATORY CHECKPOINT — Determine scope before queries.**

- Default: company-wide (all orgs).
- If user says "good morning — just engineering" or names an org, scope all queries to that org.
- If user says "good morning" with no qualifier, run company-wide without confirming (the briefing is designed to be fast).

### Workflow

1. **Resolve scope** — company-wide or org-filtered. No confirmation prompt for default.

2. **Run 4 sections in parallel** (each reads different bindings):
   - **Attrition Watch** (binding: `workforce.attrition` + `workforce.headcount`)
     - New exits in last 24 hours (names, orgs, tenure, vol/invol, regrettable flag)
     - T12M voluntary and total attrition rate vs company benchmark
     - Any org with rate > 2x company average = attrition hot spot flag
   - **Hiring Velocity** (binding: `talent_acquisition.pipeline`)
     - Offers extended this week, offers accepted, offers declined
     - Open reqs vs filled reqs (trailing 30 days)
     - Any req open > 60 days = flag
   - **Engagement Signals** (binding: `workforce.engagement` — optional)
     - Latest pulse survey score (if available) with delta vs prior pulse
     - PTO utilization rate: flag orgs with avg < 50% of accrued (burnout risk)
     - eNPS if available
   - **Hot Spots** (computed from above)
     - Orgs appearing in multiple red flags (high attrition + stale reqs + low pulse)
     - Synthesize into a priority-ranked watch list

3. **Produce 1-page briefing** under 500 words. Lead with headline: "Workforce steady" or "X hot spots need attention."

### Query Construction Rules

**Attrition rate calculation**:
- Period: trailing 12 months (T12M). Needs ~12 months of history; with less, use the available period and state it.
- Numerator: exits in the period (voluntary rate: voluntary_flag = true; total rate: all exits)
- Denominator: (HC_start + HC_end) / 2 — HC_start = snapshot at (or nearest before) the period start, HC_end = latest snapshot in the period; state both dates
- Rate: exits / denominator × 100 (no annualization)
- This is the default; your hydrated configuration may set a different attrition method
- Company benchmark: compute company-wide rate as the comparison point

**Hot spot detection**:
- An org is a hot spot if ANY of: attrition rate > 2x company avg, pulse score < 70 (or bottom quartile), open reqs stale > 60 days for > 30% of positions
- Rank hot spots by number of flags triggered (most flags first)

**Regrettable-loss flag**:
- If exit table has `regrettable_flag`, use it directly
- If not, proxy: voluntary exit + performance_rating in top 2 tiers + tenure > 12 months = regrettable

**Fiscal period handling**:
- No fiscal calendar dependency for this skill — all windows are rolling (last 24h, last 7d, last 30d, T12M for attrition rate)
- Windows end at the latest date in the exit data (not the current clock time); state that date in the briefing header
- "This week" starts on Monday

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (attrition + headcount + pipeline resolve) | Full briefing with all 4 sections |
| MEDIUM (attrition resolves but pipeline missing) | Briefing without Hiring Velocity; note "recruiting data unavailable" |
| MEDIUM (no regrettable_flag column) | Use proxy logic; note "regrettable classification is estimated" |
| LOW on engagement (very common — many customers don't have pulse data in SF) | Skip Engagement Signals section silently; this is expected |
| LOW on critical (no exits table) | Skip skill; output hydration guidance |

### Output Format

```
# Good Morning, CHRO

**Workforce status: <Steady | X hot spots need attention>**

## Attrition Watch
- New exits (24h): <N> (<names if <5, else "N exits across M orgs">)
  - Regrettable: <N>
- T12M attrition: voluntary <X.X%>, total <X.X%> (company benchmark: <Y.Y%>; HC <start date> → <end date>)
- Hot orgs: <Org1> at <Z%>, <Org2> at <Z%>

## Hiring Velocity (7d)
- Offers extended: <N> | Accepted: <N> | Declined: <N>
- Open reqs: <N> | Filled (30d): <N>
- Stale (>60d): <N> reqs flagged

## Engagement Signals  [omit if unavailable]
- Latest pulse: <score> (Δ <+/-X> vs prior)
- PTO risk: <Org> at <X%> utilization (below 50% threshold)

## Hot Spots (priority order)
1. <Org> — <flags triggered: high attrition + stale reqs + low pulse>
2. ...

---
Hydration: <binding sources>
```

### Guardrails

- New exits section: if > 5 exits in 24h, show count by org rather than individual names (avoid a "wall of names" that reads like a layoff announcement)
- Regrettable flag is sensitive — never surface the underlying performance rating to justify the classification
- If engagement data is from a survey, note the survey date (stale survey data > 90 days should be flagged)
- Never compare one org's pulse score to another org's by name in a way that could shame a leader

### Error Handling

| Scenario | Action |
|---|---|
| Zero exits in 24h | Report it positively: "No new exits in the last 24 hours." |
| Engagement binding missing | Skip section (expected — many companies don't store pulse in SF) |
| Pipeline binding missing | Skip Hiring Velocity; note "connect your ATS to enable this section" |
| Hot spot section has 0 flags | "No hot spots today — all orgs within healthy thresholds." |

## Quality

This skill is tested against `evals/good_morning_chro/golden_cases.md` and `evals/good_morning_chro/eval_plan.md` (People Hydrator runs them in its Evaluate stage). They are tests, not runtime instructions: follow this file and its customer configuration (added at hydration).

## Context Queries

- An employee exits table at one row per exited employee with columns: employee_id,
  employee_name, exit_date, org, department, level, tenure_months, voluntary_flag (boolean),
  regrettable_flag (boolean, nullable), performance_rating (nullable). Used for attrition
  watch section and hot spot detection.

- A headcount snapshot or current employee table with one row per active employee (or
  one row per org per snapshot_date) providing headcount counts per org at period start and end. Used as
  the denominator for attrition rate calculation.

- (Optional) An offer/pipeline table at one row per offer extended with columns: offer_id,
  status (extended/accepted/declined), extended_date, job_id, recruiter_id. Plus a job/req
  table for open req counts and created_date (for stale-req detection). Used for Hiring
  Velocity section.

- (Optional) An engagement table at one row per (org, survey_date) with pulse_score,
  eNPS, survey_response_rate. Used for Engagement Signals section. Expected to be missing
  for many customers — skill handles this gracefully.

- (Optional) A PTO/leave balance table at one row per employee with accrued_days,
  used_days, utilization_pct. Used for burnout-risk flag in Engagement section.

- (Optional) A semantic view over the exits/headcount data for Cortex Analyst
  follow-up questions like "why is engineering attrition spiking?"
