# Commentary Patterns — Flux Analysis

## Core Principles

### Principle 1: "Why Not What" (MANDATORY)

Every driver explanation must answer WHY the movement happened — not restate WHAT the number is.

| Quality | Example |
|---|---|
| Good | "Cloud hosting up $1.2M on incremental GPU reservations for product launch" |
| Bad | "Cloud hosting increased $1.2M (18%)" |
| Good | "Headcount cost decreased $3.4M due to 47 attrition departures not backfilled" |
| Bad | "Headcount cost was $3.4M lower than prior month" |
| Good | "Prepaid insurance decreased $0.8M from scheduled amortization of annual premium" |
| Bad | "Prepaid insurance went down $0.8M" |

The "why" comes from the dimensional data (which vendors, which cost centers, which projects) and theme extraction (what type of activity drove the change).

### Principle 2: "Flag for Controller" (MANDATORY)

When the data does not clearly explain the driver:
- Sparse dimensions (all blank or generic values)
- Ambiguous journal descriptions (no discernible pattern)
- Single large transaction with no descriptive memo
- Multiple offsetting items that net to a significant but unexplained change

Write: **"Driver unclear from available data — flag for controller review"**

NEVER invent an explanation. NEVER use an LLM to speculate about business reasons. The controller will investigate manually.

### Principle 3: "Always Comment" List

Certain account categories always receive full commentary regardless of whether they pass the materiality threshold:

**Default always-comment categories:**
- Revenue lines (all revenue accounts)
- Headcount and payroll cost (salaries, benefits, SBC)
- Cash and cash equivalents

These are customer-configurable. The rationale: management and auditors always review these lines regardless of size. A $50K revenue variance may be immaterial by threshold but highly significant as a trend signal.

---

## Pattern Detection Logic

The agent detects which commentary pattern to apply based on what the DATA contains — not the account name. Look for these signals in the dimension values and journal descriptions:

| Signal in Data | Activity Type | Grouping Dimension |
|---|---|---|
| Multiple revenue stream types (subscription, usage, services) | Revenue Mix | Revenue stream type |
| Service categories with volume or rate metrics | Volume-Driven Services | Service category |
| Compensation components (salary, bonus, severance, equity comp, payroll tax) | People Cost | Compensation component |
| Cloud/infrastructure provider names | Cloud Infrastructure | Provider name |
| Office or geographic location names | Location-Driven | Office/location |
| Project or event names (conferences, campaigns, launches) | Event/Project-Driven | Project name |
| Software or product names with capitalization/amortization | Capitalized Project | Project x amortization status |
| Customer names with individual amounts | Customer-Driven | Customer name |
| Vendor/supplier names with individual amounts | Vendor-Driven | Vendor/supplier |
| Cash flow categories (collections, disbursements, investments) | Cash Movement | Cash flow category |
| Reserve actions (specific reserve, general reserve, reversal) | Reserve Activity | Reserve action type |
| Prepaid types with amortization schedules | Prepaid Amortization | Prepaid category |
| Receivable/payable clearing sub-types | Settlement/Clearing | Sub-type category |
| Reclassification or transfer entries | Reclassification | Source to destination |

### Hierarchy Rule

When multiple signals overlap (e.g., vendor expenses across locations):
1. **Primary driver** — the dimension explaining the LARGEST share of variance
2. **Context dimension** — additional dimension mentioned in parentheses for business context

---

## Commentary Structural Templates

### Template A: Enumerated Drivers (Roman Numeral)

**When**: Summary-level accounts that roll up 3-7 distinct sub-drivers.

**Structure**:
```
{Account} {direction} {amount} {period context}, primarily due to:
• (i) {driver_1_amount} {driver_1_explanation}
• (ii) {driver_2_amount} {driver_2_explanation}
• (iii) {driver_3_amount} {driver_3_explanation}
Partially offset by:
• (iv) {offset_1_amount} {offset_1_explanation}
```

### Template B: Primary + Offset

**When**: One dominant driver (>60% of variance) with smaller offsetting items.

**Structure**:
```
{Account} {direction} {amount}, driven by {primary_driver_explanation} ({driver_amount}).
Partially offset by {offset_explanation} ({offset_amount}).
```

### Template C: Vendor/Supplier List

**When**: Vendor-driven expense accounts (consulting, software, services).

**Structure**:
```
{Account} {direction} {amount}, primarily from:
• {Vendor_1}: {amount} ({explanation})
• {Vendor_2}: {amount} ({explanation})
• Others (N vendors): {amount}
```

### Template D: Volume x Rate

**When**: Accounts driven by measurable volume changes (headcount, units, hours).

**Structure**:
```
{Account} {direction} {amount} due to {volume_change} ({metric} from X to Y),
representing {rate_explanation}.
```

### Template E: Timing/Accrual

**When**: Accounts driven by timing of recognition (deferred revenue, prepaid amortization, accruals).

**Structure**:
```
{Account} {direction} {amount} reflecting {timing_explanation} —
{new_additions_amount} in new {items} offset by {amortization_amount} in scheduled {amortization_type}.
Net: {net_direction} of {net_amount}.
```

### Template F: Single Sentence (Concise)

**When**: Simple, single-driver accounts where one sentence captures the full story.

**Structure**:
```
{Account} {direction} {amount} due to {concise_explanation}.
```

### Template G: Entity/Location Breakdown

**When**: Variance concentrated in specific legal entities or geographic locations.

**Structure**:
```
{Account} {direction} {amount}, concentrated in:
• {Location_1}: {amount} ({explanation})
• {Location_2}: {amount} ({explanation})
Remaining entities: {amount} (within normal range).
```

### Template H: Net Movement (Additions vs Disposals)

**When**: Balance sheet accounts with gross additions offset by gross disposals/amortization.

**Structure**:
```
{Account} net {direction} {amount}:
• Additions: +{amount} ({explanation_of_what_was_added})
• {Reductions_type}: -{amount} ({explanation_of_what_reduced})
Net impact: {net_amount} {direction}.
```

---

## Netting Rules

Related themes that are economically linked should be netted together BEFORE generating commentary:

| Theme A (Increase) | Theme B (Decrease) | Net As | Rationale |
|---|---|---|---|
| New additions/purchases | Scheduled amortization/depreciation | Net asset change | Same asset lifecycle |
| Accrual bookings | Accrual reversals | Net accrual change | Same liability cycle |
| FX gains | FX losses | Net FX impact | Same economic exposure |
| Reclassification in | Reclassification out | Net reclass (or zero) | Balance sheet movement only |
| New hires compensation | Attrition savings | Net headcount cost change | Same workforce dynamic |

After netting, if the net is near zero, mention both sides but note they offset: "Additions of $X offset by amortization of $Y, netting to minimal change."

---

## Quality Rules

| Rule | Description |
|---|---|
| Q1 | Direction word must match the sign (increase/decrease, up/down) |
| Q2 | Amounts in commentary must match query results exactly |
| Q3 | Commentary must reference the actual dimension values from the data |
| Q4 | Percentages belong in the summary table only — not in commentary prose |
| Q5 | Period type labels (MoM, QoQ) are not needed in commentary text |
| Q6 | Use location/region names rather than entity codes in commentary |
| Q7 | Collapse items below 5% of total variance into "Other — multiple small items" |
| Q8 | Every material account MUST have commentary — never leave it blank |
| Q9 | Commentary length: 1-4 sentences for single-driver; 3-8 bullet lines for multi-driver |
| Q10 | If a driver is concentrated in one entity/location, mention it by name |

---

## Number Formatting

| Amount Range | Format | Example |
|---|---|---|
| < $1,000 | Full number | $847.23 |
| $1,000 - $499,999 | K format (1 decimal) | $429.4K, $85.2K |
| >= $500,000 | M format (1 decimal) | $0.5M, $1.1M, $8.3M |

Apply to ALL amounts in commentary including subtotals. Always prefix with +/- to indicate direction.
