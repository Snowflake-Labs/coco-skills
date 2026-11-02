# Query Reference — Attrition Insights (Dehydrated)

All physical table/column references are resolved at runtime by the People hydrator against the customer's HRIS data in Snowflake. This file describes **what** each query needs to compute, not **how** to compute it against a specific schema.

---

## Data Shape (resolved at runtime)

**Primary sources:**
- Attrition snapshot table (one row per employee per month)
- Cost center → finance org mapping table (one row per cost center)
- Exit records table (one row per exit event)

**Optional sources:**
- Fiscal calendar / date reference table
- User identity / org scoping table (for executive mode)
- Pre-computed executive summary table (fast-path option)
- Semantic view for Cortex Analyst follow-ups

---

## Critical Data Rules

**Exit date offset (MANDATORY):** Count an exit one day after the termination date. Filter logic: `exit_date = termination_date + 1 day`. Using the raw snapshot date produces incorrect exit counts.

**Attrition rate formula:** Attrition rate = exits in the period ÷ average of headcount at period start and period end × 100, i.e. denominator = (HC_start + HC_end) / 2. Period start HC = snapshot at (or nearest before) the period start; period end HC = latest snapshot in the period; state both dates. Default period is trailing 12 months (T12M); the same formula applies to any requested period (YTD, month, each month, quarter, custom range), with no annualization. Voluntary / involuntary / regrettable rates use the same denominator with the matching exit subset. This is the default; your hydrated configuration may set a different attrition method (attrition_rate_method). Never use ending-period HC alone as the denominator.

**Masking awareness:** The voluntary exit flag, involuntary exit flag, termination category, and termination reason columns may be masked for lower-privilege session roles and return NULL. Always check whether these fields are accessible before including them in output.

---

## Query Type 1 — Access Preflight

**Purpose:** Confirm the session role can read exit data before doing any other work.

**Logic:**
1. Issue a minimal probe against the attrition snapshot source.
2. Test whether the regrettable flag and termination category columns return non-NULL values (access check for masked columns).
3. On failure, do not proceed. Surface a friendly access-issue message; never expose schema or error text.

**Output grain:** Success / fail signal per data layer.

---

## Query Type 2 — Snapshot: Core Rates + MoM Trend

**Purpose:** Current-period attrition rates and month-over-month exit trend in a single pass.

**Logic:**
1. Compute for the requested period:
   - Total exits (applying exit date offset)
   - Voluntary exits (if accessible)
   - Involuntary exits (if accessible)
   - Regrettable exits (if accessible)
   - Start/end average headcount: (HC_start + HC_end) / 2 (state both snapshot dates)
2. Compute rates: total attrition %, voluntary %, involuntary %, regrettable %.
3. For MoM trend: compute monthly exit counts over the trailing 13 months.
4. Return both the current-period summary and the monthly series.

**Output grain:** One summary row + one row per month for the trend series.

---

## Query Type 3 — Watch Areas (Unified Scan)

**Purpose:** Identify cohorts across four dimensions with elevated attrition risk.

**Logic:**
1. Scan the attrition snapshot once and branch results by dimension: org group, management level, tenure group, region.
2. For each dimension value, compute: exit count, start/end average HC, attrition rate, regrettable rate, trend direction (current vs prior period).
3. Compute composite risk score per cohort (configurable weights for rate deviation, trend direction, regrettable share).
4. Rank all cohorts across all dimensions by risk score.
5. Return top N cohorts (configurable).

**Output grain:** One row per (dimension_type, dimension_value) cohort, with risk score and component metrics.

---

## Query Type 4 — Drivers: Top Termination Reasons

**Purpose:** Show the top termination reason categories for the current period and the QoQ shift.

**Logic:**
1. Group exits by termination reason category for the current period.
2. Compute count and share (% of total exits) per reason.
3. Repeat for the prior period.
4. Compute QoQ change (absolute count delta and percentage point shift).
5. Return both current period and QoQ shift in a single result set (use a `query_id` or `period` discriminator column).

**Output grain:** One row per termination reason per period.

**Notes:** Termination reason may be masked for some session roles. If masked, skip this query and surface a note.

---

## Query Type 5 — Regrettable Deep Dive

**Purpose:** Break down regrettable exits by org group and management level for the current period.

**Logic:**
1. Filter exits to regrettable = true.
2. Group by org group and management level.
3. Compute count, share of total regrettable exits, and regrettable rate (regrettable exits / start/end average HC in that cohort).
4. Return top 20 cohorts ranked by regrettable rate.

**Output grain:** One row per (org group, management level) cohort.

**Notes:** Requires regrettable flag to be unmasked. If masked, skip silently and note that elevated access is needed.

---

## Query Type 6 — Future Exits Pipeline

**Purpose:** Surface employees with confirmed future termination dates already entered in the HRIS.

**Logic:**
1. Filter exit records to termination dates > today.
2. Optionally apply org scope filter.
3. Return count by org group, management level, and regrettable status.
4. Do NOT surface individual names unless the session role has appropriate HRBP-level access.

**Output grain:** One row per (org group, management level, regrettable flag) grouping.

---

## Query Type 7 — 4-Week Trailing Exit Trend (Weekly Mode)

**Purpose:** Show exit counts for each of the last 4 weeks, flagging sustained hotspots.

**Logic:**
1. Group exits by week for the trailing 4 weeks (using exit date offset).
2. Optionally break down by org group.
3. Flag orgs with elevated exit counts in 3 or more consecutive weeks as "sustained hotspot".

**Output grain:** One row per (week, org group) combination.

---

## Pitfalls

1. **Exit date offset** — the most common source of exit count discrepancies. Always use termination date + 1 day as the filter anchor. Using the raw snapshot date or raw termination date produces wrong numbers.
2. **Attrition denominator** — use the start/end average (HC_start + HC_end) / 2, not ending HC alone; don't divide by a single snapshot. Unless your configuration sets a different attrition method.
3. **Masked columns returning NULL** — if regrettable, voluntary, or involuntary flags are masked, they return NULL rather than 0. Summing or counting NULL columns produces misleading zeros. Always test column accessibility before building rate metrics on them.
4. **Org mapping gaps** — decommissioned cost centers still have exiting employees. Without the dual-join fallback, those employees appear in an "Unknown org" bucket that inflates that category.
5. **Snapshot date vs termination date** — the attrition snapshot table stores one row per employee per month. Using the snapshot month to filter exits (instead of the actual termination date with the +1 offset) produces overcounting in some months and undercounting in others.
6. **Small cohort risk scores** — a cohort with 2 exits and 10 headcount has a 20% attrition rate that may rank above a cohort with 50 exits and 500 HC at 10%. Apply a minimum cohort size threshold before ranking watch areas.
7. **Regrettable rate vs count** — surfacing only the regrettable count (not rate) can make large orgs appear disproportionately at risk. Always show both count and rate in the watch area output.
