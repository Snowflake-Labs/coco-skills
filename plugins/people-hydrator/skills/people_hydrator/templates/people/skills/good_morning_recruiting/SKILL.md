---
name: good_morning_recruiting
description: >
  Recruiting health briefing for Talent Acquisition (TA) leaders. Runs eight TA
  metrics in one briefing, or any one of them on demand:
  hiring manager sentiment, sourced percentage, req load by recruiter, stale
  candidates, competitor hires, competitor hiring intelligence (web), stale job
  openings, and recent offer declines. Supports PDF report generation. Use for
  hiring manager sentiment, recruiter sourced percentage, competitor hiring
  intelligence, recruiter performance, TA OKR metrics, recruiting health check.
  Triggers: TA metrics, recruiting metrics, GM TA report, hiring manager
  sentiment, sourced percentage, req load, stale candidates, competitor hires,
  stale openings, offer declines, recruiting briefing.
  Do NOT use for: a single candidate's debrief (use interview_debrief),
  workforce headcount (use headcount_insights), or a leader's weekly hiring PDF
  (use hiring_brief).
license: Proprietary. See LICENSE.txt for complete terms
---

# Good Morning Recruiting (Dehydrated)

## About this skill

- **What it does:** A recruiting health briefing across eight metrics: hiring manager sentiment, sourced % by recruiter, requisition load, stale candidates, hires from competitors, competitor hiring news, stale job openings and recent offer declines. Can produce a PDF.
- **Who it's for:** Heads of Talent Acquisition and TA operations leads.
- **Works with:** Stands alone. For a single candidate use `interview_debrief`; for workforce headcount use `headcount_insights`; for a leader-ready weekly report use `hiring_brief`.
- **Before you start:** This skill runs on the data and definitions connected by **People Hydrator** (`people/people-hydrator/`). If it hasn't been set up for your account yet, run People Hydrator first.
- **How to use it:** Say "TA metrics" or "recruiting briefing" for all eight, or name one ("stale candidates", "offer declines").
- **If you don't get what you expect:** If a section is missing or says data isn't available, that data wasn't connected during setup: re-run People Hydrator to add it. If a number looks wrong, tell People Hydrator what you expected; it turns that into a checked correction. If the skill says a table or column has changed, re-run People Hydrator for this skill.
- **Good to know:** Competitor hiring news uses web search, so it needs web access in your Cortex Code setup; without it, that section is marked as not available. Hires from competitors needs prior-employer data and is marked as not available if it wasn't connected. The skill reads your data and never changes it.

## Intent

Provide Talent Acquisition leaders with a unified view of recruiting health across eight core metrics in a single briefing. Orchestrates multiple focused metric analyses into one output with optional PDF report generation. Designed for TA leadership reviewing recruiter performance and pipeline health — not for individual candidate evaluation (use `interview_debrief`) or workforce-level headcount analytics (use `headcount_insights`).

## Behavior

### Metric Routing

Eight metrics, each with its own data requirement:

| Metric Key | Metric Name | Data Source Type |
|------------|-------------|-----------------|
| hm-sentiment | Hiring Manager Sentiment | Structured data (survey / feedback scores) |
| sourced-pct | Sourced % by Recruiter | ATS application / sourcing data |
| req-load | Req Load by Recruiter | ATS requisition data |
| stale-candidates | Stale Candidates | ATS candidate stage + duration data |
| competitor-hires | Hires from Competitors | Employee prior company data |
| competitor-intel | Competitor Hiring Intelligence | Web search (live) |
| stale-openings | Stale Job Openings | ATS job opening data |
| offer-declines | Recent Offer Declines (last 14 days) | ATS offer data |

**Default behavior:** When invoked without a `--metric` flag, run all 8 metrics in order.

### Input Parameters

| Parameter | Options | Default | Required? |
|-----------|---------|---------|-----------|
| `--metric` | hm-sentiment, sourced-pct, req-load, stale-candidates, competitor-hires, competitor-intel, stale-openings, offer-declines, all | all | No |
| `--timeframe` | Current fiscal quarter, trailing 90 days, trailing 12 months, or custom dates | Current fiscal quarter | No |
| `--recruiter` | Filter to specific recruiter name(s) | all | No |
| `--department` | Filter to specific department(s) | all | No |

### Agent Architecture

This skill uses 5 agents executing in sequence:

| Agent | Purpose | Visible to User? |
|-------|---------|-----------------|
| **Agent 1: Data Extractor** | Run all SQL queries and web searches | SILENT |
| **Agent 2: Insight Drafter** | Format results into inline chat output | **VISIBLE** |
| **Agent 3: Validation Engine** | Enforce data quality rules | SILENT |
| **Agent 4: JSON Builder** | Assemble PDF payload | SILENT |
| **Agent 5: PDF Generator** | Generate PDF and confirm output | **VISIBLE** |

**Visibility rules:**
- The only user-visible output from Agent 1 is nothing — all raw data gathering is silent.
- Agent 2 shows inline results after all metrics complete.
- Agent 3 silently validates data; NEVER prints validation details, drop logs, or intermediate calculations to chat.
- Agent 4 silently builds the data payload; NEVER prints JSON or progress to chat.
- Agent 5's only visible output: one sentence confirming the PDF was generated and its file name. No path details, byte counts, or output logs.

**DO NOT display in chat:** raw SQL results, validation details, JSON payloads, JSON-building progress, Python code, bash commands, file sizes, or any internal processing. **This includes narrating reasoning** — do NOT output article-by-article decisions, intermediate arithmetic, agent transition labels, or any "thinking out loud" text.

### Execution Flow

```
User invokes skill
    ↓
Agent 1: Data Extractor
    Run SQL queries for metrics 1-5, 7, 8 (in parallel where possible)
    Run web searches for metric 6 (in parallel — 18 total searches)
    ↓
Agent 3: Validation Engine (SILENT)
    Validate dates, types, thresholds, logical consistency
    Drop invalid records; log internally
    ↓
Agent 2: Insight Drafter
    Format inline results for each metric
    Display to user
    ↓
*** USER PROMPT: "Would you like me to generate a PDF report?" ***
    If NO → STOP
    If YES → Continue
    ↓
Agent 4: JSON Builder (SILENT)
    Assemble validated data into JSON payload
    Write to skill directory (NOT Desktop)
    ↓
Agent 5: PDF Generator (VISIBLE confirmation only)
    Run PDF generation
    Confirm: "PDF generated successfully and placed on your desktop with this title: [filename]"
```

### Visual Formatting Rules

1. After EVERY metric section, output 3 blank lines to create clear visual separation.
2. Every metric heading uses `##` (h2) markdown with a colored circle indicating overall health: 🟢 (healthy), 🟡 (watch), 🔴 (action needed).
3. Sub-sections use `###` (h3).
4. Within tables, use 🔴 for violations, 🟡 for approaching/warning rows, and 🟢 for healthy/clear rows.
5. All metrics include an "Analytic status indicator" in the heading.

### Metric 6: Competitor Hiring Intelligence

**Source:** Live web search (not structured data).

**Topics to surface:**
- Compensation changes (base pay, vesting, sign-on)
- Hiring freezes, layoffs, or headcount expansion
- Recruiting strategy changes (remote/hybrid policy, RTO, new offices)
- Notable leadership hires or departures (especially engineering, sales, recruiting)
- Benefits and perks changes
- Acquisitions / mergers affecting headcount or team integration
- Office openings, closings, or geographic expansion
- Talent strategy shifts

**Execution:** 6 web searches per competitor, covering: hiring changes/compensation, layoffs/headcount, acquisitions, office expansion/remote policy, engineering hiring plan, leadership hires/departures. Run all searches in parallel.

**Result processing:**
1. Deduplicate by URL across all searches.
2. Filter to articles published within the last 30 calendar days (calendar days, NOT fiscal calendar).
3. **Topic relevance gate (MANDATORY):** Include only articles directly about compensation changes, hiring freezes/layoffs/expansion, recruiting strategy, notable leadership hires/departures, benefits changes, workforce-affecting acquisitions, office openings/closings, talent strategy announcements. **Exclude:** product launches, research papers, board appointments (unless CPO/Head of Recruiting/CHRO), partnerships not involving headcount, marketing, fundraising without explicit headcount plans, general corporate news.
4. Rank newest first within each competitor.
5. Extract: date, headline, 1-2 sentence summary, source URL.
6. **Source URLs are MANDATORY** — every competitor intel item MUST include a clickable markdown link. Never omit source links.
7. If no results survive: `> No significant hiring, compensation, acquisition, or workforce changes found in the last 30 days.`

**Competitor list:** Configurable at customer setup — do not hardcode specific competitor names in the dehydrated skill.

### Metric 8: Recent Offer Declines

**Time window:** Last 14 calendar days only.

**Output:** Count of declines + detail table (candidate, job title, location, recruiter, decline reason, date).

**Scope limitation:** This metric surfaces decline detail only — it does NOT include offer acceptance rate or per-recruiter offer counts (those are available via the standalone TA metrics skills).

### PDF Report Sections

When generating the PDF, include one section per metric:
- **HM Sentiment:** Executive summary, flagged recruiters (below threshold), full recruiter breakdown, methodology.
- **Sourced %:** Executive summary, flagged recruiters (below sourcing threshold), full recruiter breakdown, methodology.
- **Req Load:** Executive summary (total reqs, recruiters, unassigned), full recruiter table, methodology.
- **Stale Candidates:** Description, violations table, approaching table. Red background for violations, orange for approaching.
- **Competitor Hires:** Recent hires table. Green background. Methodology.
- **Competitor Intel:** Per-competitor tables with clickable URLs, key takeaways.
- **Stale Job Openings:** Executive summary, counts by department, non-evergreen table, evergreen table. Red for 180+ days open, orange for 120–179 days.
- **Offer Declines:** Declined offers table (last 14 days). Candidate, job title, location, recruiter, decline reason, date.

**PDF file name:** `TA_Metrics_Report_[period_label].pdf`
**PDF location:** User's desktop — NEVER save inside the skill directory.

### Agent 3: Validation Engine Rules (SILENT)

**Check 1: Competitor Intel — 30-Day Date Enforcement**
- Full date (YYYY-MM-DD): Include only if ≥ today - 30 days.
- Year-month only (YYYY-MM): Include only if matches current or prior month.
- Year only (YYYY): Always drop.
- No date: Include only if body explicitly references a specific event within 30 days; otherwise drop.

**Check 2: Data Freshness Note**
Include a `data_note` in the JSON payload for HM Sentiment, Sourced %, and Stale Candidates noting that data may be slightly behind the live ATS view.

**Check 3: Logical Consistency**
- No negative "days in current interview stage" values.
- No future hire dates beyond today.
- Stale candidate counts ≥ 0.

### Fiscal Year Convention

Resolve fiscal year and quarter boundaries from the customer's fiscal calendar — do not hardcode specific dates.

**Data Quality Note:** Candidate data from before an ATS migration cutoff date may be incomplete. When the customer has defined a migration cutoff date, focus analysis on data after that date for reliable insights.

### Confidence Handling

- **HIGH confidence:** All 8 metric data layers resolve. Run full briefing.
- **MEDIUM confidence:** Most metrics resolve; one or two have thin data. Run with affected metrics marked "(limited data)".
- **LOW confidence on non-critical metrics** (e.g., competitor hires, HM sentiment): Run with those sections noting "data unavailable for this period".
- **LOW confidence on critical bindings** (ATS data is inaccessible): Skip skill execution; output the data domain description and recommend the customer connect their ATS data.

### Stopping Points

- After displaying inline results for all metrics, ask the user: "Would you like me to generate a PDF report?" Only proceed to PDF generation if the user confirms.
- If no recruiters fall below threshold for a given metric, confirm "all clear" to the user.
- Competitor intel uses live web data — results vary between invocations.

### Guardrails

- Never display raw SQL results, table names, column names, schema paths, or role names to the user.
- Never display JSON payloads, Python code, bash commands, or agent transition labels.
- Never save the PDF to the skill directory — only to the user's desktop.
- Source links are MANDATORY for competitor intel — never output competitor intelligence without URLs.

### Error Handling

| Scenario | Response |
|----------|---------|
| Authorization / permissions error on ATS data | "I wasn't able to pull recruiting data right now — this usually means your account needs a permissions update. Reach out to your People Analytics team." |
| Web search returns no results within 30 days | "No significant hiring or workforce changes found in the last 30 days for [competitor]." |
| PDF generation fails | Report failure to the user and offer to retry. |
| Query timeout | "This is taking longer than expected — the system might be busy. Want me to try again?" |

## Quality

This skill is tested against `evals/good_morning_recruiting/golden_cases.md` and `evals/good_morning_recruiting/eval_plan.md` (People Hydrator runs them in its Evaluate stage). They are tests, not runtime instructions: follow this file and its customer configuration (added at hydration).

## Context Queries

- A hiring manager sentiment / survey scores table at one row per survey response per recruiter per period, with recruiter ID, hiring manager ID, sentiment score (numeric), survey date, and optional feedback text — used for HM sentiment average and flagging recruiters below threshold.

- An ATS applications / sourcing table at one row per candidate application, with recruiter ID, application source type (sourced vs inbound vs referral), application date, job ID, and disposition — used for sourced % by recruiter.

- A requisitions table at one row per requisition per recruiter assignment, with req ID, recruiter ID, status (open / filled / closed), job title, department, and days open — used for req load by recruiter.

- A candidates-in-stage table at one row per candidate per active stage, with candidate ID, application ID, recruiter ID, stage name, stage start date, and days in current stage — used for stale candidate detection (days in stage exceeding configurable thresholds).

- An employee prior company / hire source table at one row per employee with their prior company name and hire date — used for competitor hires (employees hired from configured competitor companies in the period).

- A job openings table at one row per opening, with opening ID, job ID, title, status, open date, and whether the opening is evergreen — used for stale job openings (openings exceeding configurable staleness thresholds, segmented by evergreen vs non-evergreen).

- An offers table at one row per offer, with offer ID, application ID, candidate name, job title, recruiter ID, location, offer status (accepted / declined / open / withdrawn), decline reason, and offer declined date — used for offer declines (last 14 days only; this metric does NOT include acceptance rate or per-recruiter offer counts).

- (Optional) A semantic view over the ATS tables for Cortex Analyst-driven follow-up questions like "show me sourced % trend for [recruiter] over the last 4 quarters".
