# Query Reference — Recon Analysis

All physical table/column references are discovered at runtime based on the user's connected environment. Bindings come from `hydration_hints.yaml` in the parent `recon_analysis` directory: `{{profile_store_fqn}}` (GET procedure or table), `{{settings.save_procedure}}` (SAVE procedure), `{{cols.*}}` for profile-store columns. Comparison sources (`{{source1}}`, `{{source2}}`) come from the loaded profile, not from the hints file.

**Writes go through the SAVE procedure only.** The skill never issues INSERT / UPDATE / DELETE itself — profile changes are `CALL {{settings.save_procedure}}(...)`, after user confirmation. If only a table (no procedure) is bound, profile saving is unavailable; tell the user to run the hydrator's profile-store setup.

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
4. **Latest version only:** a profile is its latest row; if that row is a deleted tombstone, the profile is not listed

**Output grain**: one row per profile

**Reference SQL:**

```sql
-- Procedure-backed store (preferred): returns a JSON array of current profiles
CALL {{profile_store_fqn}}();

-- Table-backed store: latest row per profile, then filter
SELECT {{cols.profile_name}}, {{cols.owner}}, {{cols.visibility}}, {{cols.updated_at}}
FROM {{profile_store_fqn}}
QUALIFY ROW_NUMBER() OVER (PARTITION BY {{cols.profile_name}} ORDER BY {{cols.updated_at}} DESC) = 1
-- then keep rows that are not deleted and are public or owned by CURRENT_USER()
ORDER BY {{cols.updated_at}} DESC;
```

---

## Query Type 2 — Load Profile Configuration

**Purpose**: Retrieve full settings for a named profile.

**Logic**:
1. Call the profile persistence mechanism with the profile name
2. Return: full JSON settings payload
3. Parse into: sources, filters, date_columns, join_keys, comparison_fields, join_type, thresholds

**Output grain**: single profile object

**Reference SQL:** `CALL {{profile_store_fqn}}(:profile_name);` — returns `null` when the profile does not exist or its latest version is deleted.

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

**Reference SQL** (`{{key}}` = the profile's join key column(s); source filters bound as parameters):

```sql
WITH s1 AS (SELECT DISTINCT {{key}} AS k FROM {{source1}} WHERE {{source1_filters}}),
     s2 AS (SELECT DISTINCT {{key}} AS k FROM {{source2}} WHERE {{source2_filters}})
SELECT
  COUNT(s1.k)                                            AS source1_total_keys,
  COUNT(s2.k)                                            AS source2_total_keys,
  COUNT_IF(s1.k IS NOT NULL AND s2.k IS NOT NULL)        AS matched_keys,
  COUNT_IF(s2.k IS NULL)                                 AS source1_only_keys,
  COUNT_IF(s1.k IS NULL)                                 AS source2_only_keys,
  DIV0(matched_keys, GREATEST(source1_total_keys, source2_total_keys)) AS match_rate
FROM s1 FULL OUTER JOIN s2 ON s1.k = s2.k;
```

---

## Query Type 4 — Full Reconciliation (SQL-native path)

**Purpose**: Execute the core comparison logic entirely in Snowflake.

**Logic**:
1. CTE for Source 1 (with filters + date range — values bound as parameters; custom-SQL source must be a single SELECT)
2. CTE for Source 2 (same rules; apply `natural_sign` normalization identically to both sides)
3. JOIN on configured join keys (join type per profile)
4. For each comparison field, classify:
   - Both NULL → Match
   - One NULL → Discrepancy
   - Numeric: diff = 0 → Match; else apply the profile's `numeric_gate` — `OR`: diff <= nominal OR pct_diff <= pct_threshold; `AND`: both — → Match (threshold); else Discrepancy
   - Text: similarity >= fuzzy_threshold → Match (fuzzy); else Discrepancy
   - Date: day_diff <= `date_tolerance_days` → Match (date); else Discrepancy
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

**Logic** (always via the SAVE procedure, after user confirmation — never direct DML):
1. INSERT: `CALL {{settings.save_procedure}}('INSERT', :profile_name, :settings_json, :is_public)` — creates a profile; a previously deleted name may be re-used
2. UPDATE: `CALL {{settings.save_procedure}}('UPDATE', :profile_name, :settings_json, :is_public)` — appends a new version row (owner-only; fails if the profile is deleted)
3. DELETE: `CALL {{settings.save_procedure}}('DELETE', :profile_name)` — appends a tombstone row (owner-only; fails if already deleted)
4. Return: the procedure's `{"success": ..., "updated_at" | "error": ...}` response, shown to the user

**Output grain**: single status response

---

## Query Type 9 — Profile History/Revert

**Purpose**: View profile version history or restore a prior version.

**Logic**:
1. Query all versions of a named profile ordered by timestamp descending
2. For revert: `CALL {{profile_store_fqn}}(:profile_name, :as_of)` to load the version current at that timestamp, then save it as the new current version with the SAVE procedure (`UPDATE`, or `INSERT` if the profile is currently deleted)

**Output grain**: one row per historical version
