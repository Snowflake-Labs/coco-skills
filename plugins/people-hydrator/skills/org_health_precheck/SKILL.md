---
name: org_health_precheck
description: >
  Read-only readiness check for the org_health_report skill, run before setup
  in CoWork or Cortex Code. Answers aggregate questions about the data
  org_health_report needs (employee snapshots, exits,
  performance ratings, PTO, AI usage, recruiting), checks how fresh it is and
  how far back it goes, and returns a PASS / WARN / BLOCKED readiness report
  to send to Snowflake. Uses a people-data agent where one is available, or
  read-only aggregate SQL where it can query directly. Checks data availability only; it does not ask for
  definitions or policy decisions. Triggers: org health precheck, run the
  precheck, org health readiness check, are we ready for org health.
  Do NOT use for: running the org health report itself (use
  org_health_report), or setting skills up on your data (use People Hydrator).
license: Proprietary. See LICENSE.txt for complete terms
---

# Org Health Precheck

## About this skill

- **What it does:** Checks whether your people data can support `org_health_report`, before anyone sets it up, and produces one report you send to your Snowflake contact.
- **Who it's for:** The People Analytics or HR person preparing for an Org Health pilot.
- **Works with:** Runs before `org_health_report`. It is a quick availability check; People Hydrator (`plugins/people-hydrator/`) does the full connection and validation during setup.
- **Before you start:** Run it in CoWork or Cortex Code, as someone with read access to your HR data: through a people-data agent, or by querying the tables or semantic views directly. It needs nothing else and is not hydrated.
- **How to use it:** Say "run the org health precheck", then copy the report it produces and send it to your Snowflake contact.
- **Good to know:** It only asks for counts, dates and yes / no answers. It never shows employee names, IDs or pay, and it never changes anything.

## Purpose

Find out, before setup, whether the data this user can reach can support
`org_health_report`: which data exists, how fresh it is, and whether there is
enough history for its trailing-12-month metrics and prior-year comparison.
This is a data-availability check only: do not ask how metrics are defined or
for policy decisions. People Hydrator settles those during setup.

## Rules

- **Read-only.** Never create, change, or delete anything. Never schedule
  automations.
- **Aggregates only.** Ask only for counts, dates, and yes / no availability.
  Never ask for or display employee names, IDs, or individual pay. If an
  answer comes back with individual-level rows, summarize it as a count and do
  not show the rows.
- **Read-only queries only.** When querying directly, run only `SELECT`,
  `SHOW` and `DESCRIBE`. Never run `CREATE`, `ALTER`, `INSERT`, `UPDATE`,
  `DELETE`, `MERGE`, `GRANT` or any other statement that changes objects,
  data or access. Every query must return aggregates (counts, `MIN` / `MAX`
  dates, yes / no); never select employee-level rows.
- **Do not guess.** If a question cannot be answered from the data, record the
  check as not available. Never fill in a plausible value.
- **Do not name objects in the report.** Do not put table, column or schema
  names in the report; describe data in business words.
- Keep the user informed with one short line per step.

## Step 1: Choose the data source

Use the first of these that works here:

1. **Direct queries.** If you can run SQL (for example in Cortex Code),
   find the people data you can read: prefer semantic views over HR data,
   then tables or views holding employee snapshots, exits, performance
   ratings, PTO, AI-tool usage and requisitions. Use `SHOW` / `DESCRIBE`
   and column names to confirm what each holds; do not assume from a name
   alone. Record the source as `direct read-only queries`.
2. **A people-data agent.** If you cannot run SQL (for example in CoWork),
   route questions to the agent that answers people / HR data questions. If
   several exist, prefer the one whose description mentions org health or
   people analytics. Record its display name.

If neither is available, stop and report:
`BLOCKED: no people data is reachable from here (no readable HR data and no people-data agent).`

## Step 2: Probe each data area

Answer the questions below from the data source. With direct queries, answer
each with an aggregate query and keep the number of queries small; with an
agent, group the questions into as few requests as possible. For each area
record:
- **Available:** yes / partial / no.
- **Latest date** seen.
- **Earliest date** available, as an actual date. A duration such as "about two
  years" or "several cycles" is not enough: get the date, and if the data
  source still cannot give one, record `not reported`.
- **History:** months between the earliest and latest dates (for area 3, the
  number of review cycles instead). Work this out yourself from the two dates;
  do not ask an agent for it.

The two history columns come from how `org_health_report` computes its
numbers. **Min history** is what the current trailing-12-month metrics need;
attrition uses headcount snapshots at the start and end of the period, so less
than this silently produces wrong rates. **For prior year** is what the
report's prior-year (YoY) column needs, because it repeats each metric for the
same window one year earlier.

| # | Area | Questions to answer (aggregates only) | Required? | Min history | For prior year |
|---|------|----------------------------------------------|-----------|-------------|----------------|
| 1 | Identity and roster | Can the person running the precheck be found in the employee directory (with direct queries, match the current user's login or email)? Answer yes / no only. How many active employees are there? Is each employee's manager and full management chain available? Is employee type available, so interns and contingent workers can be excluded (yes / no)? Is hire date available (yes / no)? What are the dates of the latest and the earliest employee snapshot? | Required | 12 months | 24 months |
| 2 | Exits | How many exits in the last 12 months? Is there a voluntary / involuntary split? Is there a regrettable or non-regrettable flag? Can reduction-in-force (RIF) exits be told apart from other involuntary exits (yes / no)? Does each exit record the employee's management chain (yes / no)? What are the earliest and latest exit dates on record? | Required | 12 months | 24 months |
| 3 | Performance ratings | How many rating levels does the scale have? How many review cycles exist? What are the end dates of the earliest and the two most recent cycles? Does each rating record the employee's management chain at review time (yes / no)? Are promotion events available (yes / no)? | Optional | 2 consecutive cycles | A cycle ending 12+ months ago |
| 4 | Team structure and span | Can the number of direct reports per manager be calculated? | Optional | Current only | Covered by area 1 |
| 5 | Well-being / PTO | Is PTO usage available? Is each employee's PTO allotment available, so utilization can be calculated (yes / no)? What are the earliest and latest periods covered? | Optional | 12 months | 24 months |
| 6 | AI adoption | Is AI-tool usage available per employee, by tool and date? What are the earliest and latest dates covered? | Optional | 90 days | 15 months |
| 7 | Recruiting | Are open requisitions with a status and an opened date available? How many are open now? Are accepted hires with a future start date available (yes / no)? Are offers with an accepted / declined outcome available (yes / no)? | Optional | Current only | Not used |

## Step 3: Grade and report

Grade each area:
- **PASS:** data available, fresh (see below), and history meets both the Min
  history and For prior year columns. Areas marked Current only need no
  history check.
- **WARN:** any of: partial data; stale; earliest date `not reported`; or
  history short of either column. Say which in Notes. Short of Min history
  means the area's trailing-12-month numbers will come out wrong; short of
  For prior year means the report runs but its prior-year column shows "—".
  For area 1, also WARN if employee type or hire date is missing; for area 2,
  if RIF exits can't be identified (organic attrition can't be shown); for
  area 3, if promotion events are missing (promotion rate shows "—"); for
  area 5, if allotment is missing (utilization can't be calculated).
- **BLOCKED:** data not available.

**Fresh** means the latest date is within the last 45 days, except for
performance ratings, which run on a review cadence. For area 3, fresh means
the latest cycle ended no longer ago than the gap between the two most recent
cycles plus 60 days. A semi-annual cycle that ended four months ago is fresh;
do not mark it stale just because it is older than 45 days.

Overall status: BLOCKED if area 1 or area 2 is BLOCKED, or if the person
running the precheck could not be found in the employee directory (`org_health_report`
scopes every report to that person's org, so it cannot run without it).
Otherwise WARN if any area is not PASS; otherwise PASS. Areas 3 to 7 are
optional: when BLOCKED, `org_health_report` marks those sections "data
unavailable", so they do not by themselves make the overall status BLOCKED.

Render exactly this structure, then tell the user: *"Copy this report and send
it to your Snowflake contact."*

```
# Org Health Precheck Report
Run by: [user name] · [date] · Data source: [agent name | direct read-only queries]
Overall: [PASS | WARN | BLOCKED]

## Data readiness
| # | Area | Status | Latest date | History (earliest date · length vs min / prior year) | Notes |
|---|------|--------|-------------|------------------------------------------------------|-------|
| 1 | Identity and roster | ... |
| 2 | Exits | ... |
...
| 7 | Recruiting | ... |

## Fixes needed
- [one line per WARN / BLOCKED item: what is missing and who likely fixes it.
  For a history shortfall, state the gap, e.g. "exits: 14 of 24 months, so the
  prior-year column will be empty; backfill earlier terminations"]

## Not checked here
Business definitions (fiscal year, who counts in headcount, which ratings count
as high and low performers), the peer-org benchmark, and the exact tables and
columns are confirmed during setup with People Hydrator.
```

## Error handling

| Situation | Response |
|-----------|----------|
| No people data reachable | Overall BLOCKED; explain that the user needs read access to HR data, or a people-data agent. |
| A query or the agent returns a permission error | Mark that area BLOCKED with "access denied for this user". Do not show raw error text or object names. |
| An answer is unclear | Mark WARN and quote the answer in one short line. |
| The person running the precheck cannot be looked up | Overall BLOCKED; note "could not find the person running the precheck in the employee directory". |
