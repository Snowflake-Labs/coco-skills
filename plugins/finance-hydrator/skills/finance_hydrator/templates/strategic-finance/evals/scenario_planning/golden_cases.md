# Golden Test Cases: scenario_planning

7 capability test cases + 3 pitfall regression cases.

---

## Capability 1 — What-If Scenario Modeling (Mode 1)

### Case SCEN-01: Match a what-if to history

**Setup:** Scenario-notes table populated with past planning scenarios.

**Input:**
```
what if we opened a new EMEA office?
```

**Expected behavior:**
- Parses the question into a searchable concept (facilities expansion / capex)
- Semantic-matches against historical scenario notes; ranks by relevance
- Catalogs the adjustments in the top-matched scenario (value vs default per domain)
- Presents recommended adjustments and offers to apply them to the current forecast (route to Mode 2)

**Guard checks:** G1-G5 all pass

**Expected output shape:**
- `# Scenario Match - "<question>"` with matched scenario + relevance
- `## Recommended Adjustments` table (Domain / Assumption / Change)
- Offer to apply adjustments and show P&L impact

---

### Case SCEN-02: No matching scenario

**Setup:** Scenario-notes populated, but nothing resembling the user's question.

**Input:**
```
what if we switched to a usage-based pricing model?
```

**Expected behavior:**
- Acknowledges no close historical match honestly
- Suggests which planning domains might be relevant (e.g. revenue, pricing assumptions)
- Does NOT force a low-relevance match

---

## Capability 2 — Sensitivity Analysis (Mode 2)

### Case SCEN-03: Direct assumption adjustment

**Input:**
```
show me the P&L impact if headcount growth increases by 10%
```

**Expected behavior:**
- Resolves domain (headcount), parameter, and magnitude
- Captures baseline P&L metrics, applies the adjustment, captures post-adjustment state
- Presents a before / after / delta table across the horizon with FAV/UNFAV annotation
- Notes secondary effects (e.g. headcount affects both OPEX and revenue capacity)

**Expected output shape:**
- `# Sensitivity Analysis - <adjustment>` with a P&L Impact table, Secondary Effects, and a Recommendation

---

## Capability 3 — Root Cause Analysis (Mode 3)

### Case SCEN-04: OPEX movement decomposition

**Input:**
```
why did R&D expense grow this year?
```

**Setup:** headcount_cph source available.

**Expected behavior:**
- Verifies R&D actually grew before decomposing
- Decomposes into headcount (volume) x cost-per-head (rate)
- Leads with a one-sentence causal answer; ranks top drivers by dollar impact with root-cause explanations

**Expected output shape:**
- `# Root Cause Analysis - R&D, <period>` with a one-sentence Summary, ranked Key Drivers, and a "How Root Cause is Identified" formula block

---

### Case SCEN-05: Revenue movement decomposition

**Input:**
```
what is driving the change in revenue growth?
```

**Setup:** revenue_detail (cohort x region) source available.

**Expected behavior:**
- Decomposes revenue into base + adjustments, then base by cohort (acquisition year) and region
- Drivers are causal (e.g. "mature cohorts approaching steady-state consumption"), not observational restatements

---

## Capability 4 — Mode 1 unavailable, Modes 2/3 offered

### Case SCEN-06: Scenario-notes empty

**Setup:** The scenario-notes table exists but is empty.

**Input:**
```
what if we doubled the sales team?
```

**Expected behavior:**
- Reports that no populated historical-scenario repository was found, so Mode 1 matching is unavailable
- Offers Mode 2 (model the scenario directly) or Mode 3 (decompose a movement)
- Does NOT fabricate historical scenarios

---

## Capability 5 — Critical binding missing

### Case SCEN-07: No P&L or forecast data

**Input:**
```
model a scenario where we cut marketing spend
```

**Setup:** No P&L source resolves.

**Expected behavior:**
- Does not run
- Outputs guidance on which planning data to land (P&L summary + assumptions) and to re-verify
- Does NOT invent a baseline

---

## Pitfall Regression Cases

### Case P-01: Historical scenario P&L impact NOT shown

**Setup:** Mode 1 matches a historical scenario that carries its own computed P&L impact.

**Input:**
```
what if we expanded into APAC?
```

**Check:** The response shows only the recommended adjustments from the matched scenario. It does NOT display that historical scenario's P&L impact (it was computed against a different baseline and would mislead). Impact is offered only after applying the adjustments to the user's current forecast.

---

### Case P-02: User's claim verified before decomposition

**Setup:** User asserts revenue is decreasing; data shows revenue growing while the growth rate decelerates.

**Input:**
```
why is revenue decreasing?
```

**Check:** The skill states what the data actually shows (growing but decelerating) and asks the user to confirm the framing before running the decomposition. It does not accept the false premise.

---

### Case P-03: Domains and defaults discovered, not hardcoded

**Setup:** Assumptions are fragmented across per-domain tables; a defaults table exists for some domains but not others.

**Input:**
```
what did we change in the latest scenario vs default?
```

**Check:** Adjustment detection uses the real per-domain parameter names discovered from the data. For domains with no defaults table, the skill shows the assumption value and notes the delta-vs-default is unavailable — it does not invent default values or friendly parameter names.

---

## Running

```
/skill_eval - point it at this file for golden case assertions.
```
