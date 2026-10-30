# Query Reference — Good Morning CHRO (Dehydrated)

All physical table/column references resolved at runtime by the People hydrator.

---

## Data Shape (resolved at runtime)

**Primary sources:**
- Employee exits table (one row per exited employee)
- Headcount snapshot (one row per org per snapshot_date, or one row per active employee)
- (Optional) Offer/pipeline table + job/req table
- (Optional) Engagement/pulse survey table
- (Optional) PTO/leave balance table

---

## Query Type 1 — New Exits (24h)

**Purpose**: Who left in the last 24 hours.

**Logic**:
1. Filter exits table by exit_date >= DATEADD(day, -1, CURRENT_DATE())
2. Return: employee_name, org, tenure_months, voluntary_flag, regrettable_flag
3. If >5 exits, aggregate: COUNT(*) GROUP BY org instead of listing names

**Output grain**: list of exits or org-level count summary

---

## Query Type 2 — Trailing Attrition Rate

**Purpose**: T12M voluntary and total attrition rate by org (no annualization).

**Logic**:
1. Numerator: COUNT exits where exit_date >= DATEADD(month, -12, CURRENT_DATE()), GROUP BY org (voluntary rate: AND voluntary_flag = true; total rate: all exits)
2. Denominator: (HC_start + HC_end) / 2 per org — HC_start from the snapshot at (or nearest before) the period start, HC_end from the latest snapshot in the period; return both dates
3. Rate: numerator / denominator × 100 (unless your configuration sets a different attrition method)
4. Company benchmark: same calculation without org GROUP BY
5. Hot spot: any org where rate > 2x company benchmark

**Output grain**: one row per org

---

## Query Type 3 — Hiring Velocity

**Purpose**: Offer pipeline health this week.

**Logic**:
1. Filter offer table by extended_date >= DATEADD(day, -7, CURRENT_DATE())
2. COUNT(*) WHERE status = 'extended', COUNT(*) WHERE status = 'accepted', COUNT(*) WHERE status = 'declined'
3. For open reqs: COUNT jobs WHERE status = 'Open'
4. For stale reqs: COUNT jobs WHERE status = 'Open' AND DATEDIFF(day, created_date, CURRENT_DATE()) > 60

**Output grain**: summary counts

---

## Query Type 4 — Engagement Signals

**Purpose**: Latest pulse score + PTO risk.

**Logic**:
1. Pull most recent pulse survey row per org (MAX survey_date)
2. Compare to prior pulse (second-most-recent survey_date) for delta
3. For PTO risk: AVG(used_days / accrued_days) per org, flag orgs < 0.50

**Output grain**: one row per org (pulse) + flagged orgs (PTO)

---

## Query Type 5 — Hot Spot Detection (composite)

**Purpose**: Synthesize red flags across sections.

**Logic**:
1. For each org, count flags: attrition > 2x benchmark (+1), stale reqs > 30% of open (+1), pulse < 70 or bottom quartile (+1)
2. Rank orgs by total flag count DESC
3. Top 3 orgs with ≥2 flags = hot spots

**Output grain**: ranked list of orgs with their triggered flags

---

## Pitfalls

1. **Attrition period** — use the T12M count directly; do not annualize a short window. With less than 12 months of history, use the available period and state it.
2. **Regrettable proxy** — if no regrettable_flag column, the proxy (voluntary + high performer + tenure > 12mo) over-counts; always note "estimated" in output.
3. **Engagement data staleness** — if survey_date > 90 days ago, flag it: "Last pulse was <N> days ago — results may not reflect current state."
4. **Zero-headcount orgs** — new orgs or recently-merged orgs may have 0 in the denominator. Guard with NULLIF.
5. **Small-org noise** — an org with 3 people losing 1 = 33% monthly rate. Suppress hot-spot flag for orgs < 10 headcount to avoid noise.
