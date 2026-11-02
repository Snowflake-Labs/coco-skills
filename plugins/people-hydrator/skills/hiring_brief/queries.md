# Query Reference — Hiring Brief (Weekly Hiring Update) (Dehydrated)

All physical table/column references are resolved at runtime by the People hydrator against the customer's HRIS data in Snowflake. This file describes **what** each query needs to compute, not **how** to compute it against a specific schema.

---

## Data Shape (resolved at runtime)

**Primary sources:**
- Weekly headcount reporting / executive summary table (pre-aggregated, one row per leader per snapshot date)
- Management hierarchy table (one row per employee with chain levels)
- Fiscal calendar / date reference table

**Optional sources:**
- Headcount forecast table (one row per org group per forecast version per period)
- Starts / new hires event table (if pre-aggregation not available)
- Exits / terminations event table (if pre-aggregation not available)

---

## Query Type 1 — Latest Snapshot Date Discovery

**Purpose:** Find the most recent available snapshot date to suggest as the default end date.

**Logic:**
1. Query the maximum available snapshot date from the headcount reporting table.
2. Return as the suggested default for the user's end date selection.

**Output grain:** One scalar value (latest date).

---

## Query Type 2 — Leader List Discovery

**Purpose:** Populate the leader selection checklist with available leaders from the management hierarchy.

**Logic:**
1. Query the management hierarchy for distinct leaders at the desired org levels (top-level executive, leaders one level below the top of the hierarchy, leaders two levels below the top).
2. Return: leader name, org level indicator, org group label.
3. Sort by org level then alphabetically by name.

**Output grain:** One row per available leader.

---

## Query Type 3 — Core Headcount Data (Per Leader)

**Purpose:** Fetch the KPI banner values and org table rows for a specific leader.

**Logic:**
1. Filter the headcount reporting table to the requested leader and end date (if that date has no snapshot, offer the most recent available date and ask before using it).
2. Compute YTD window: start date = fiscal year start date for the fiscal year containing the end date (dynamic from fiscal calendar).
3. For the KPI banner, aggregate to a single row for the leader:
   - Current HC (as of end date)
   - Open Positions (approved unfilled requisitions as of end date)
   - Future Starts (accepted offers with start date > end date)
   - Future Exits (confirmed terminations with termination date > end date)
   - Projected HC = Current HC + Future Starts − Future Exits
4. For the org table, group by the next level down (each leader → their direct reports):
   - For each sub-org: HC, Open Positions, YTD Starts (from fiscal year start to end date), YTD Exits (from fiscal year start to end date), Projected HC.

**Output grain:** One KPI banner row + one row per sub-org in the org table.

---

## Query Type 4 — Forecast Data (Top-Level Executive / Senior Leaders with Forecast Mapping)

**Purpose:** Add Forecast and Adjusted Plan targets to the KPI banner for eligible leaders.

**Logic:**
1. Determine the most recent forecast version available (sorted by fiscal year, quarter, and forecast sequence number — all descending, pick the top record).
2. Determine the calendar month parameter for the forecast lookup (typically January 1 of the end date's fiscal year — but resolve dynamically from the fiscal calendar).
3. Filter the forecast table to the resolved leader / org group, forecast version, and calendar month.
4. Return: Forecast target, Adjusted Plan target.
5. Compute: % to Forecast = Projected HC / Forecast × 100; % to Adjusted Plan = Projected HC / Adjusted Plan × 100.

**Output grain:** One row per leader with forecast and plan targets.

**Notes:** If no forecast record exists for the leader, return null and the skill generates a 3-KPI report instead of 6-KPI.

---

## Query Type 5 — Executive Drill-Down (Top-Level Executive Reports Only)

**Purpose:** Detailed sub-org breakdown for the top-level executive's largest or most strategically significant orgs.

**Logic:**
1. For each configured drill-down org (typically the top 2 orgs — configurable at customer setup), run the same org table query as Query Type 3 but scoped to that org's direct reports (one level below the top-level executive).
2. Return the same columns as the main org table.

**Output grain:** One row per leader one or two levels below the top of the hierarchy within each drill-down org.

---

## Pitfalls

1. **Fiscal year start computation** — hardcoding a fixed fiscal year start date breaks at year boundaries. Always compute dynamically: find the fiscal year containing the end date, then use that fiscal year's start date.
2. **Forecast version resolution** — the customer's forecast table typically has multiple versions per quarter (e.g., "Q1 Forecast 1", "Q1 Forecast 2"). Always sort descending to get the most recent version — do not pick arbitrarily.
3. **Forecast availability by leader** — not all leaders map to a cost center that has a forecast record. Always check for null before computing % to Forecast; fall back to 3-KPI gracefully.
4. **YTD vs as-of-date confusion** — Starts and Exits in the org table are YTD (from fiscal year start to end date), while Current HC is a point-in-time snapshot at the end date. Never mix these windows.
5. **Projected HC formula order** — Projected HC = Current HC + Future Starts − Future Exits. "Future" means after the end date. Do not include YTD starts/exits in the projection.
6. **Leader level grouping** — the "next level down" grouping must be resolved from the actual management hierarchy, not inferred from org labels. A "VP of Engineering" who reports to the CTO is two levels below the top of the hierarchy when the top-level executive's view is requested, but one level below the top when the CTO view is requested.
7. **Snapshot date** — if the exact end date has no snapshot, offer the most recent available date and ask the user before using it. Always note the snapshot date used in the report header.
