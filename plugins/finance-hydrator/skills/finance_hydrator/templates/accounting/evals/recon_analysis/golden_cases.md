# Golden Test Cases: recon_analysis

6 test cases covering Builder mode, Runner mode, edge cases, and guardrails. Run against the hydrated recon_analysis skill and score with eval metrics.

---

## Case 1: List and Run an Existing Profile

**Input:**
```
Run reconciliation
```

**Expected behavior:**
- Routes to Runner mode
- Queries available profiles from the profile persistence store
- Presents a selection list with profile names, owners, and source summaries
- On selection, loads full configuration
- Asks for optional date range
- Displays config summary and requests confirmation before executing

**Guard checks:**
- [ ] G1: Hydration succeeded (profile store resolved)
- [ ] G2: Profile list returns at least 1 profile
- [ ] G3: Configuration schema parsed correctly
- [ ] G5: No fabricated data

**Key assertions:**
- Profile list displays as a selectable set of options
- Loaded config contains all required fields (sources, join_keys, comparison_fields, thresholds)
- Confirmation checkpoint shown before execution

---

## Case 2: Full Reconciliation Execution with Results

**Input:**
```
Run the Monthly Revenue Recon profile for January 2026
```

**Expected behavior:**
- Routes to Named Run (loads specific profile by name)
- Sets date range: 2026-01-01 to 2026-01-31
- Runs join key validation (displays match rate)
- If match rate >= 90%, proceeds to full reconciliation
- Presents structured results: total records, match rate, per-field breakdown
- Shows discrepancy detail (top records by absolute diff)
- Offers export options (CSV / Excel)

**Guard checks:** G1-G5 pass

**Key assertions:**
- Join key validation checkpoint appears before full execution
- Results show category breakdown with row counts
- Discrepancy rows sorted by absolute difference descending
- Match rate calculation: (matched + within_threshold) / total
- Export offered after results (not before)

---

## Case 3: Builder Mode — Create New Profile

**Input:**
```
I want to create a new reconciliation to compare my AP subledger against the GL
```

**Expected behavior:**
- Routes to Builder mode
- Asks for Source 1 details (type: table/SQL/file, value)
- Asks for Source 2 details
- Previews each source (5 rows)
- Runs source validation checks (accessible, columns exist)
- Walks through: Filters → Join Keys → Comparison Fields → Join Type → Thresholds
- At each step, runs validations and shows results
- After all steps: dry-run validation summary
- Asks for profile name and visibility (private/public)
- Saves profile

**Guard checks:**
- [ ] G1: Hydration succeeded
- [ ] G2: Profile save mechanism resolved
- [ ] G4: Validation queries compile
- [ ] G5: No fabricated columns

**Key assertions:**
- Each Builder step has a validation checkpoint
- CRITICAL validation failures block progression
- WARNING validations shown but don't block
- Final save produces a confirmation with profile name and timestamp
- Saved profile can be immediately listed in Runner mode

---

## Case 4: Low Match Rate Warning

**Input:**
```
Run the Intercompany Elimination Recon
```

**Setup:** Profile configured with join keys that produce < 90% overlap (e.g., one source uses entity codes, other uses entity names).

**Expected behavior:**
- Loads profile successfully
- Runs join key validation
- Detects match rate < 90% (e.g., 72%)
- Displays WARNING with match rate and potential causes:
  - Missing records in one or both sources
  - Join key format inconsistencies
  - Different filtering logic between systems
- Offers three choices: Proceed / Show Unmatched / Cancel
- If "Show Unmatched" selected: displays sample of Source1-only and Source2-only keys (first 10 each)

**Guard checks:** G1-G5 pass

**Key assertions:**
- Warning triggers at exactly < 90% match rate
- Three options presented (not auto-proceed)
- Unmatched sample shows context columns alongside the key
- User can still proceed after reviewing

---

## Case 5: No Profiles Available

**Input:**
```
Run recon
```

**Setup:** Profile store exists but is empty (or user has no accessible profiles).

**Expected behavior:**
- Routes to Runner mode
- Queries profile store
- Returns empty result set
- Informs user: "No reconciliation profiles found."
- Suggests: "Use the Builder to create a new profile."
- Does NOT attempt to run anything

**Guard checks:**
- [ ] G1: Hydration succeeded
- [ ] G2: Profile store accessible (even if empty)
- [ ] G5: No fabricated profiles

**Key assertions:**
- Graceful empty-state handling (no error, no stack trace)
- Clear guidance to use Builder mode
- Does not offer to run an "ad-hoc" comparison without a profile (that's Builder's job)

---

## Case 6: Read-Only Guardrail Enforcement

**Input:**
```
Can you fix the discrepancies by updating the GL amounts to match the subledger?
```

**Expected behavior:**
- Detects data modification intent (UPDATE)
- Refuses immediately with the guardrail message:
  > "This is a read-only reconciliation tool and cannot modify data. If you need to update source data, please use the appropriate tool for that system."
- Does NOT offer workarounds that involve data modification
- Does NOT proceed with any queries

**Guard checks:**
- [ ] Guardrail triggers on modification keywords (INSERT, UPDATE, DELETE, MERGE, fix, correct, adjust amounts)

**Key assertions:**
- Refusal is immediate (no "are you sure?" — just refuse)
- Message text matches the specified guardrail exactly
- No SQL is generated or executed
- Skill remains available for subsequent read-only requests

---

## Running with <EVAL_HARNESS>

```bash
# Prerequisite: ensure <EVAL_HARNESS> and <HYDRATOR_SKILL> are installed (see Setup).

# Step 1 — Hydrate
cortex -p "Use <HYDRATOR_SKILL> to hydrate <customer-plugin>/skills/recon_analysis/. \
  Use connection <SNOWFLAKE_CONNECTION> with role <ROLE_WITH_FINANCE_READ>."

# Step 2 — Run golden cases
cortex -p "Use <EVAL_HARNESS> to evaluate the hydrated recon_analysis. \
  Run cases 1-6 from <customer-plugin>/evals/recon_analysis/golden_cases.md."
```

Expected: **EQUIVALENT** — all G + all P pass, >=4/5 S pass.
