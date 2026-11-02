# Query Reference — Headcount Insights (Dehydrated)

All physical table/column references are resolved at runtime by the People hydrator against the customer's HRIS data in Snowflake. This file describes **what** each query needs to compute, not **how** to compute it against a specific schema.

---

## Data Shape (resolved at runtime)

**Primary sources:**
- Employee snapshot table (one row per employee per snapshot date)
- Cost center → organization mapping table (one row per cost center)
- Hires / starts event table (one row per hire event)
- Exits / terminations event table (one row per exit event)

**Optional sources:**
- Fiscal calendar / date reference table
- User identity / org scoping table (for executive mode)
- Pre-computed executive summary table (fast-path validation)
- Semantic view for Cortex Analyst follow-ups

---

## Query Type 1 — Ending Headcount (Point-in-Time)

**Purpose:** Return the count of active employees at the most recent (or requested) snapshot date.

**Logic:**
1. Filter employee snapshot to the requested snapshot date (latest available by default).
2. Apply active status filter.
3. Apply employee type filter (the regular employee types configured for this customer; remove for Total HC).
4. Count distinct employees.
5. Optionally apply org scope filter for executive mode.

**Output grain:** One summary row (or one row per org group if broken down).

**Variants:**
- Active Regular HC (default employee type filter applied)
- Total HC (all employee types, no type filter)
- Org-scoped HC (exec's management chain only)

---

## Query Type 2 — Headcount Trends

**Purpose:** Show quarter-end or month-end headcount over a rolling window (typically 5 quarters or 12 months).

**Logic:**
1. Identify quarter-end or month-end snapshot dates within the requested window.
2. For each date, count active employees meeting the type filter.
3. Compute QoQ and YoY absolute and percentage change.
4. Return series ordered by date ascending.

**Output grain:** One row per period (quarter or month).

---

## Query Type 3 — Headcount by Organization

**Purpose:** Break down current headcount by organizational unit (org group, executive org, cost center, management chain level).

**Logic:**
1. Join employee snapshot to the cost center → org mapping table using the dual-join fallback for decommissioned cost centers.
2. Group by the requested org dimension (org group, cost center, executive org level, etc.).
3. Compute count, share of total (%), and QoQ change.
4. Flag org concentration risk if any single org exceeds a configurable share threshold (default 30%).

**Output grain:** One row per org group.

**Variants:** By org group / By executive org / By cost center / By cost center leader

---

## Query Type 4 — Headcount by Geography

**Purpose:** Break down current headcount by location dimension (region, country, location).

**Logic:**
1. Filter employee snapshot to the latest snapshot date with active status and type filters.
2. Group by region (e.g., AMS/EMEA/APAC), country, or location city.
3. Compute count and share of total.

**Output grain:** One row per geographic dimension value.

---

## Query Type 5 — Headcount Change (QoQ / YoY)

**Purpose:** Rank organizations by headcount growth or decline between two snapshot dates.

**Logic:**
1. Compute ending HC for both the current period and the comparison period (prior quarter or prior year).
2. Compute absolute change and percentage change.
3. Rank orgs by change (growth ranking and decline ranking).
4. Apply 🔴/🟡/✅ status based on whether change is positive, flat, or negative.

**Output grain:** One row per org group.

---

## Query Type 6 — Movement Drivers (Hires + Exits → Net Change)

**Purpose:** Show what's driving net headcount change — how many hires and exits occurred in a period, and whether the organization is net-growing.

**Logic:**
1. From the hires table, count starts in the period. Apply org scope and employee type filters.
2. From the exits table, count terminations in the period. Apply exit date logic: count exit one day after termination date.
3. Compute net change = hires − exits.
4. Compute hire-to-exit ratio (>1 = growth).
5. Optional: Break down exits by type (voluntary / involuntary) and regrettable flag.

**Output grain:** One summary row (or one row per org group for breakdown).

---

## Query Type 7 — Org Shape & Span of Control

**Purpose:** Compute manager percentage, average span of control, span distribution, and flag structural outliers.

**Logic:**
1. From the latest employee snapshot, identify employees with at least one direct report (manager definition — based on headcount of direct reports, not job grade or title).
2. Compute: total managers, total ICs, manager %, avg direct reports per manager.
3. Build span distribution: 0 DRs, 1–2 DRs, 3–9 DRs, 10+ DRs.
4. Flag outliers: over-spanned (>10 DRs), under-spanned managers (<3 DRs), ICs with direct reports, managers with zero reports.
5. Optional: Break down by org group.

**Output grain:** One summary row + optional one row per manager for outlier list.

---

## Query Type 8 — Workforce Composition (% New, % Retained)

**Purpose:** Show what share of the workforce is recently hired vs tenured.

**Logic:**
1. From the latest employee snapshot, classify employees by tenure:
   - New: hired within a configurable window (default: ≤ 12 months from snapshot date)
   - Retained: hired more than that window ago
2. Compute % new and % retained as shares of total active HC.
3. Optional: Break down by org group.

**Output grain:** One summary row (or one row per org group).

---

## Query Type 9 — Parameterized Filtered Headcount

**Purpose:** Apply any combination of dimensional filters to headcount queries.

**Logic:**
1. Start from the base ending headcount or trend query.
2. Apply any subset of available filter dimensions simultaneously:
   - Org group / executive org / cost center / cost center leader
   - Region / country / location
   - Management level (IC grade, manager grade)
   - Tenure group
   - Employee type
   - Acquisition / hire source
3. Return filtered count with the active filter dimensions shown in output.

**Output grain:** One summary row (or trend series) with filter context shown.

---

## Query Type 10 — Validation Cross-Check

**Purpose:** Spot-check key metric results against a pre-computed executive summary for consistency.

**Logic:**
1. Pull the company-wide active HC total from a pre-computed executive summary table (if available).
2. Compare to the result from the snapshot query.
3. Flag if delta exceeds a configurable tolerance (e.g., ±5 employees or ±0.1%).
4. If validation is unavailable (no pre-computed table), skip silently.

**Output grain:** Pass / fail signal for internal quality check only.

---

## Pitfalls

1. **Total HC vs Regular HC** — the most common source of number mismatches is forgetting to toggle the employee type filter. Default to Regular HC; label "Total HC" explicitly when the filter is removed.
2. **Exit date offset** — counting exits on the termination date (rather than the day after) produces wrong net-change numbers. Always apply the +1-day rule.
3. **Attrition denominator** — for attrition-related references use the start/end average (HC_start + HC_end) / 2, not current/ending HC alone or a single snapshot (unless your configuration sets a different attrition method).
4. **Cost center mapping gaps** — decommissioned cost centers still have active employees. Without the fallback lookup, those employees appear as "Unknown org" and inflate the unknown bucket.
5. **Quarter-end date alignment** — if the exact quarter-end date has no snapshot, using the wrong nearest date can produce a count that's off by a day's worth of starts/exits.
6. **Reorg vs net change** — headcount changes that result purely from reporting-line reorganizations appear as movement (exits + starts) when they are not real workforce changes. For YoY comparisons across leaders who inherited orgs via reorg, use a constant-cohort method (pin membership to today's org, trace backward) rather than hierarchy-at-time.
7. **Manager % definition** — using job grade or title to identify managers instead of actual direct-report count produces different numbers. Always use the direct-report-count definition to match the source-of-truth dashboard.
