# Query Reference — Org Health Report (Dehydrated)

All physical table/column references are resolved at runtime by the People hydrator against the customer's HRIS data in Snowflake. This file describes **what** each query needs to compute, not **how** to compute it against a specific schema.

---

## Data Shape (resolved at runtime)

**Primary sources:**
- Employee snapshot table (one row per employee per snapshot date)
- Exit records table (one row per exit event)
- Performance ratings table (one row per employee per review cycle)
- PTO / well-being usage table (one row per employee per period)
- AI tool usage table (one row per user per tool per period)
- Recruiting / open requisitions table (one row per req)
- New hire / starts table (one row per hire event)

**Supporting sources:**
- Pre-built peer org benchmark table (one row per executive-level org, pre-aggregated)
- Prior-year bundle (same metrics, T12M window shifted 12 months back)

**Optional sources:**
- New hire survey / QoH table
- Semantic view for Cortex Analyst follow-ups

---

## Phase 1 — Identity Resolution + Warmup

**Purpose:** Resolve the session user's identity and warm the warehouse before Phase 2 parallel execution.

**Logic:**
1. Look up the current session user in the employee directory (match on session login name or email).
2. Return: employee ID, full name, management level, reporting chain context (who they report to / peer org indicator), and the latest available snapshot date.
3. Determine if the leader is a top-level leader (at the very top of the hierarchy with no peer org to compare against).

**Output grain:** One row (single leader's identity context).

---

## Phase 2 — Parallel Metric Blocks

All blocks below are submitted as parallel queries in a single assistant message. Do NOT run sequentially.

### Block 1E — Direct Reports

**Purpose:** List of the leader's immediate direct reports with level, tenure, and status.

**Logic:**
1. Filter employee snapshot to the latest date.
2. Filter to employees where their direct manager ID = the leader's employee ID.
3. Return: name, management level, tenure (in months or years), active status.

**Output grain:** One row per direct report.

---

### Block S6 — Structural Context

**Purpose:** Single-row summary of org shape metrics.

**Logic:**
1. From the employee snapshot, scope to all employees in the leader's full management chain (all levels).
2. Compute: team size, manager count, IC count, manager %, avg span of control, span distribution (0 / 1–2 / 3–9 / 10+ DRs).
3. Compute new manager % (managers hired into the role within a configurable recency window).
4. Compute growth rate using constant-cohort method (today's org membership traced backward to prior year — see Guard Rail 16 in SKILL.md).

**Output grain:** One summary row.

---

### Block 4S — Talent Performance

**Purpose:** HP retention and LP exit rates.

**Logic:**
1. Identify active review cycles for the org using cycle discovery (query performance table for distinct cycle end dates where rated employee count > 0 for this org).
2. For the most recent cycle in the L12M rolling window:
   - HP cohort: employees rated in top rating tiers. HP retention = (cohort size - HP exits in period) / cohort size × 100.
   - LP cohort: employees rated in bottom rating tiers. LP exit rate = LP exits in period / LP cohort size × 100.
3. Return both rates with cohort sizes for confidence context.

**Output grain:** One summary row.

**Notes:** If no cycles exist for the org (off-cycle), return "off-cycle" indicator — not "0 LPs".

---

### Block 3I — Retention & Attrition Bundle

**Purpose:** Full attrition summary including RIF detection and new hire attrition.

**Logic:**
1. Compute T12M attrition: total exits / avg monthly HC × 100.
2. Compute voluntary and involuntary splits.
3. Compute regrettable rate.
4. **RIF detection:** Check whether any involuntary exits are categorized as reduction-in-force. If RIF exits exist, compute organic attrition = (total exits - RIF exits) / ((HC_start + HC_end) / 2) × 100.
5. **NHA (New Hire Attrition):** Identify the cohort hired in the T12M window. Count exits from that cohort within the same window. NHA rate = NHA exits / NHA cohort size × 100.

**Output grain:** One summary row with all attrition metrics.

---

### Block 5D — Well-Being / PTO Bundle

**Purpose:** PTO utilization and burnout risk composite.

**Logic:**
1. Compute for the leader's org over the L12M period: avg PTO days taken, avg PTO utilization %.
2. Compute PTO risk %: share of employees with PTO utilization below a configurable low-utilization threshold.
3. Compute new hire PTO bands: PTO utilization for employees in their first 6 months (separate from the main cohort).

**Output grain:** One summary row.

---

### Block 6E — AI Adoption Bundle

**Purpose:** AI tool adoption metrics for the leader's org.

**Logic:**
1. From the AI usage table, scope to the leader's org.
2. Compute adoption %: employees with at least one AI tool session in the trailing 90 days / total employees × 100.
3. Compute daily active %: employees with an AI session on an average weekday in the trailing 30 days / total employees × 100.
4. Return tool-level breakdown (tool name → adoption %) as a summary.

**Output grain:** One summary row with a nested tool breakdown.

---

### Block 2I — Recruiting KPIs

**Purpose:** Single-row recruiting summary.

**Logic:**
1. Count open requisitions for the leader's org by status (approved, draft, on hold).
2. Count pending starts (accepted offers with future start dates).
3. Compute offer acceptance rate for the trailing period.

**Output grain:** One summary row.

---

### Block 2H — Open Requisitions Detail

**Purpose:** List of open requisitions for the leader's org.

**Logic:**
1. Filter requisitions table to the leader's org and open status.
2. Return: req ID, job title, status, hiring manager, and days open.

**Output grain:** One row per open requisition.

---

### Block YOY — Prior Year Comparison

**Purpose:** The same metrics as the main blocks, computed for the T12M window shifted 12 months back.

**Logic:**
1. Repeat the logic from blocks S6, 4S, 3I, 5D, 6E, 2I using a date window of [today - 24 months] to [today - 12 months].
2. Use the same constant-cohort membership (today's org) for headcount-based metrics.
3. Return all prior-year values in a single row with labeled column names.

**Output grain:** One summary row.

---

### Block PEER-BENCH — Peer Org Benchmark (Non-Top-Level Leaders Only)

**Purpose:** Pre-aggregated peer org metrics for comparison columns.

**Logic:**
1. Query the pre-built benchmark table using the leader's peer org identifier (the executive-level org they report into).
2. Return: attrition rates (total, voluntary, regrettable, organic), HP retention rate, LP exit rate, PTO utilization, AI daily active %, avg span.

**Output grain:** One row for the peer org aggregate.

**Notes:** Only run for leaders who are NOT top-level. If the leader IS at the top of the hierarchy, omit this block entirely and omit the Peer Org / Δ vs Peer Org columns from the rendered table.

---

## Pitfalls

1. **HP Retention Rate is cohort-based, not a share of exits.** `hp_retained / total_exits` is wrong. Use `(cohort - exits from cohort) / cohort`.
2. **LP Exit Rate is cohort-based, not a share of exits.** `lp_exits / total_exits` is wrong. Use `lp_exits / lp_cohort`.
3. **Off-cycle orgs have no LP data — do NOT report "0 LPs."** Run cycle discovery first; if no cycle exists for the org, surface "off-cycle" explicitly.
4. **LP exit dates must filter to >= the review period start date.** Using the review period END date as the filter anchor includes only exits in the last day of the cycle; it misses employees who exited during the review window.
5. **Constant-cohort for headcount YoY.** For leaders who inherited orgs via reorg, hierarchy-at-time produces 2000%+ jumps. Pin membership to today's org.
6. **Attrition rate uses the start/end average denominator (HC_start + HC_end) / 2, not ending HC alone.** Don't divide by a single snapshot (unless your configuration sets a different attrition method).
7. **Top-level leader detection.** Submitting the peer benchmark query for a top-level leader whose peer org IS the company returns zero rows or the wrong aggregate. Detect top-level leaders during identity resolution and skip the block.
8. **RIF before organic attrition.** If you report raw attrition without RIF detection, a large involuntary RIF event makes the organic attrition number appear much worse than it is.
9. **Phase 2 must be parallel.** Sequential Phase 2 queries multiply wall-clock time by the number of blocks. Always submit all blocks in a single parallel tool-call message.
10. **Cycle discovery before performance queries.** Without cycle discovery, you may query performance data for a cycle that has no active employees in the org, returning misleading zeros.
