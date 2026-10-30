# Golden Test Cases: earnings_prep

7 capability test cases + 3 pitfall regression cases.

---

## Capability 1 — Financial Package (Mode 1)

### Case EARN-01: Default financial package

**Input:**
```
earnings prep for last quarter
```

**Expected behavior:**
- Defaults to most recent completed fiscal quarter
- Assembles key metrics: revenue, gross margin %, operating income/margin %, key operating KPIs
- Builds vs-Guidance, vs-PY (YoY), and vs-PQ (QoQ) comparison columns
- Flags any metric outside +/-5% of guidance midpoint as BEAT or MISS

**Guard checks:** G1-G5 all pass

**Expected output shape:**
- `# Financial Package - Q<N> FY<YYYY>`
- `## Key Metrics` table with Actual / vs Guidance / vs PY / vs PQ / vs Consensus columns
- `## Guidance Flags` listing BEAT/MISS with absolute and % deviation
- Footer citing guidance source and consensus source

---

### Case EARN-02: Guidance range without midpoint

**Setup:** Guidance table stores `guidance_low` and `guidance_high` but no midpoint column.

**Input:**
```
assemble the earnings pack for Q3
```

**Expected behavior:**
- Computes midpoint = (guidance_low + guidance_high) / 2
- BEAT/MISS flags computed against the derived midpoint

---

## Capability 2 — Script / Language (Mode 2)

### Case EARN-03: Draft CFO script from package

**Input (after Mode 1 has run):**
```
draft the earnings script
```

**Expected behavior:**
- Produces four paragraphs: Opening Summary, Topline Performance, Operating Metrics, Forward Guidance
- Historical sections use declarative sentences; hedging language confined to the Forward Guidance paragraph
- Presents draft and offers a revision loop

**Expected output shape:**
- `# Earnings Script - Q<N> FY<YYYY>` with a draft / legal-IR-review disclaimer
- Forward Guidance paragraph explicitly labeled as requiring legal/IR sign-off

---

## Capability 3 — Q&A Prep (Mode 3)

### Case EARN-04: Ranked analyst Q&A

**Input (after Mode 1 has run):**
```
prep the analyst Q&A
```

**Expected behavior:**
- Surfaces 10-15 likely questions from three sources: Mode 1 BEAT/MISS flags, structural patterns (deceleration, margin compression, RPO), and the recurring seed list
- Each question gets a 3-5 sentence answer citing specific numbers from the package
- Questions grouped HIGH / MEDIUM / LOW sensitivity

**Expected output shape:**
- `# Q&A Prep - Q<N> FY<YYYY>` with High / Medium / Standard-Recurring sections
- Competitive-threat questions marked "[requires additional market context]" with a placeholder

---

## Capability 4 — Multi-table composition

### Case EARN-05: Summary spread across tables

**Setup:** P&L metrics live in one wide table; operating KPIs (NRR, RPO, customer count) in another.

**Input:**
```
earnings package for the quarter
```

**Expected behavior:**
- Composes the package by joining the sources on fiscal quarter
- Metric rows may originate from different tables; this is treated as normal, not a degradation
- Output labels the fiscal quarter analyzed

---

## Capability 5 — Optional source unavailable

### Case EARN-06: No guidance history

**Setup:** No guidance source resolved (absent, empty, or RBAC-blocked).

**Input:**
```
earnings prep Q2
```

**Expected behavior:**
- Mode 1 runs without the vs-Guidance column and notes "no guidance history available"
- Attempts `web_search` for publicly issued guidance as a fallback
- Does NOT block the skill on the missing optional source

---

## Capability 6 — Critical binding missing

### Case EARN-07: No financial actuals source

**Input:**
```
earnings prep
```

**Setup:** No quarterly financial-summary source of any kind resolves.

**Expected behavior:**
- Blocks Mode 1
- Outputs: "Earnings prep requires quarterly financial metrics. Add a binding under finance.earnings.financial_summary in hydration_hints.yaml and re-verify."
- Does NOT fabricate a package from memory

---

## Pitfall Regression Cases

### Case P-01: Constant-currency not passed off as GAAP

**Setup:** The only resolved revenue/margin source is constant-currency (FX-adjusted / non-GAAP).

**Input:**
```
earnings package for the quarter
```

**Check:** Output labels the figures as constant-currency / non-GAAP and flags the GAAP-vs-adjusted materiality risk. Does NOT present them as reported GAAP actuals in the package or script.

---

### Case P-02: No invented numbers for missing metric

**Setup:** Operating income is not stored; only revenue and gross margin % are available.

**Input:**
```
earnings prep last quarter
```

**Check:** The operating income cell is left blank with "not available" (or gross profit $ is shown as "derived" from revenue x GM%). The skill does NOT approximate or invent the missing figure.

---

### Case P-03: Mode 3 invoked before Mode 1

**Input:**
```
what will analysts ask us this quarter
```

**Setup:** Mode 1 has not been run in this session.

**Check:** Skill prompts "Mode 3 requires the financial package from Mode 1. Would you like me to run Mode 1 first?" rather than generating Q&A answers from memory or invented data.

---

## Running

```
/skill_eval - point it at this file for golden case assertions.
```
