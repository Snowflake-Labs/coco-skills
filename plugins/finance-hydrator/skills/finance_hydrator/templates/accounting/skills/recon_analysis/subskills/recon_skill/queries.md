# Query Reference — Recon Analysis

All physical table/column references are discovered at runtime based on the user's connected environment.

---

## Data Shape (resolved at runtime)

**Primary sources:**
- Profile persistence store (one row per profile version — append-only)
- Two arbitrary comparison datasets (defined per-profile at runtime)
- (Optional) Date/calendar reference for period-scoped reconciliations
- (Optional) Semantic view over results for Cortex Analyst follow-up

---

## Query Type 1 — List Available Profiles

**Purpose**: Show all reconciliation profiles the current user can access.

**Logic**:
1. Call the profile persistence mechanism (procedure or query)
2. Return: profile_name, owner, visibility, last_updated, source1 summary, source2 summary
3. Filter to profiles where visibility = 'public' OR owner = current_user

**Output grain**: one row per profile

---

## Query Type 2 — Load Profile Configuration

**Purpose**: Retrieve full settings for a named profile.

**Logic**:
1. Call the profile persistence mechanism with the profile name
2. Return: full JSON settings payload
3. Parse into: sources, filters, date_columns, join_keys, comparison_fields, join_type, thresholds

**Output grain**: single profile object

---

## Query Type 3 — Join Key Validation

**Purpose**: Check join key overlap before full reconciliation.

**Logic**:
1. Get distinct join key values from Source 1 (with any filters + date range applied)
2. Get distinct join key values from Source 2 (with any filters + date range applied)
3. FULL OUTER JOIN on key to compute:
   - source1_total_keys
   - source2_total_keys
   - matched_keys (intersection)
   - source1_only_keys
   - source2_only_keys
   - match_rate = matched / GREATEST(source1_total, source2_total)

**Output grain**: single summary row

---

## Query Type 4 — Full Reconciliation (SQL-native path)

**Purpose**: Execute the core comparison logic entirely in Snowflake.

**Logic**:
1. CTE for Source 1 (with filters + date range)
2. CTE for Source 2 (with filters + date range)
3. JOIN on configured join keys (join type per profile)
4. For each comparison field, classify:
   - Both NULL → Match
   - One NULL → Discrepancy
   - Numeric: diff = 0 → Match; diff <= nominal → Match (threshold); pct_diff <= pct_threshold → Match (%); else Discrepancy
   - Text: similarity >= fuzzy_threshold → Match (fuzzy); else Discrepancy
   - Date: day_diff <= 3 → Match (date); else Discrepancy
5. Compute overall status per row (worst-case across all fields)
6. ORDER BY: Discrepancies first, then by absolute difference descending

**Output grain**: one row per join-key match (or unmatched record)

---

## Query Type 5 — Discrepancy Detail Drill-down

**Purpose**: Show full detail for discrepancies of a specific type or field.

**Logic**:
1. Filter reconciliation results to status = 'Discrepancy' (or specific category)
2. Include: join key, both-side values for all comparison fields, absolute diff, percent diff
3. ORDER BY absolute diff descending
4. LIMIT to top N (default 20)

**Output grain**: one row per discrepancy record

---

## Query Type 6 — Unmatched Records

**Purpose**: Show records present in only one source.

**Logic**:
1. Filter reconciliation results to status IN ('Source1 Only', 'Source2 Only')
2. Include: join key, source identifier, all available context columns from that source
3. GROUP BY source side for separate displays

**Output grain**: one row per orphan record

---

## Query Type 7 — Summary Statistics

**Purpose**: Compute reconciliation summary for the results header.

**Logic**:
1. COUNT(*) total records
2. COUNT by status bucket (Match, Match within threshold, Discrepancy, Source1 Only, Source2 Only)
3. Per comparison field: count matches vs discrepancies
4. Compute overall match_rate = (matched + within_threshold) / total

**Output grain**: single summary object

---

## Query Type 8 — Profile Save/Update/Delete

**Purpose**: Persist profile configuration changes.

**Logic**:
1. INSERT: Write new profile row with settings JSON, owner = current_user, visibility flag
2. UPDATE: Append new version row for existing profile (owner-only)
3. DELETE: Append tombstone row (soft-delete, owner-only)
4. Return: success/failure with timestamp

**Output grain**: single status response

---

## Query Type 9 — Profile History/Revert

**Purpose**: View profile version history or restore a prior version.

**Logic**:
1. Query all versions of a named profile ordered by timestamp descending
2. For revert: load settings from a specific historical timestamp, then save as new current version

**Output grain**: one row per historical version
