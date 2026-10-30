# Query Reference — Job Description Builder (Dehydrated)

All physical table/column references are resolved at runtime by the People hydrator against the customer's ATS data in Snowflake. This file describes **what** each query needs to compute, not **how** to compute it against a specific schema.

---

## Data Shape (resolved at runtime)

**Primary sources:**
- ATS jobs / requisitions table (one row per req)
- ATS job postings table (one row per published posting with content status)
- JD library / cache table (one row per saved JD per language)

**Supporting sources:**
- Fill rate context table (one row per filled req)
- Quality-of-hire survey table (one row per survey response)
- ATS valid locations table (approved posting locations)

---

## Query Type 1 — Access Preflight

**Purpose:** Confirm the session role can access the ATS data layer before any other query.

**Logic:**
1. Issue a minimal probe against the primary ATS data source (e.g., `SELECT 1 LIMIT 1`).
2. On failure, surface a friendly access-issue message and STOP. Never expose the schema, table, or error text.

**Output grain:** Success / fail signal only.

---

## Query Type 2 — Fetch Job Profile by Req ID

**Purpose:** Retrieve all requisition metadata needed to ground JD generation.

**Logic:**
1. Filter the jobs/requisitions table by the provided req ID.
2. Return: job title, job profile (standardized), grade, management level, department/team, business unit, cost center, location, workplace type, status, hiring manager (first element if array), recruiter (first element if array).

**Output grain:** One row per req.

---

## Query Type 3 — Search Open Roles (by Title or Team)

**Purpose:** Find matching open requisitions when the user provides a title or team instead of a req ID.

**Logic:**
1. Case-insensitive substring match on job title.
2. Optionally filter by team name or department.
3. Filter to open status only.
4. Return top matches: req ID, job title, grade, team, location, status.

**Output grain:** One row per matching requisition.

---

## Query Type 4 — Fetch ATS Published JD (Content Source Priority 1)

**Purpose:** Retrieve the published job description from the ATS for this requisition.

**Logic:**
1. Filter job postings table by job ID from the resolved req.
2. Return: JD content (full text), content status (valid / empty / malformed / too short).
3. Only proceed with ATS content if status = `valid`.

**Output grain:** One row per posting.

---

## Query Type 5 — Fetch Valid Locations

**Purpose:** Validate the role's location against the approved ATS posting locations.

**Logic:**
1. Query the approved locations table.
2. Return: location name, country, region for all valid posting locations.
3. When validating a specific location, check: (a) does it appear in the list? (b) does it match the internal-only prefix pattern?
4. If invalid, filter the list to the same country/region and return up to 5 alternatives.

**Output grain:** Full location list for validation; 0–5 alternative rows for the user to choose from.

---

## Query Type 6 — JD Library Exact Lookup

**Purpose:** Check the JD library for a previously saved JD for this exact req and language.

**Logic:**
1. Filter JD library by req ID and language code (exact match on both).
2. Return: JD content, job title, source type, version number, saved date.

**Output grain:** Zero or one row.

---

## Query Type 7 — JD Library Tiered Fuzzy Match

**Purpose:** Find similar saved JDs when no exact match exists.

**Logic:**
1. Apply tiered matching in order:
   - Tier 1: Exact req ID match
   - Tier 2: Same job profile (exact)
   - Tier 3: Same job profile (substring match — partial profile match)
   - Tier 4: Same job code + same grade
   - Tier 5: Same job family + same grade
2. Filter to the requested language.
3. Return top 5 results ordered by tier (best match first) then by saved date (most recent first).
4. Include: req ID, job title, job profile, grade, team name, location, source type, version number, updated date, and the first 200 characters of JD content (preview snippet).

**Output grain:** Zero to 5 rows ranked by match quality.

---

## Query Type 8 — Fill Rate Context

**Purpose:** How long have similar roles taken to fill? Used for the Recruiter Intelligence annotation.

**Logic:**
1. Identify similar closed roles using tiered matching: exact job profile → fuzzy job profile → job family.
2. Filter to roles closed within the last 24 months.
3. Apply standard hire exclusion filters (exclude rescinded offers, contract workers, interns, fixed-term employees).
4. Compute: count of roles, average days to fill, median days to fill.
5. Assign confidence: HIGH (n ≥ 10), MEDIUM (n = 5–9), LOW (n < 5).
6. Return: count, average, median, confidence label, match basis (exact profile / fuzzy profile / job family).

**Output grain:** One summary row per confidence tier (or single row).

---

## Query Type 9 — Quality-of-Hire Signal

**Purpose:** What % of similar role hires had favorable post-hire quality ratings?

**Logic:**
1. Identify the same set of similar closed roles as in Query Type 8.
2. Join to the QoH survey table on application ID.
3. De-duplicate to the first survey per application.
4. Classify: favorable = rating ≥ 3, unfavorable = rating ≤ 2.
5. Compute: total responses, favorable count, QoH favorable %.
6. Assign confidence: HIGH (n ≥ 10), MEDIUM (n = 5–9), LOW (n < 5).
7. Return: favorable %, total n, confidence label.

**Output grain:** One summary row.

---

## Pitfalls

1. **ATS content validation** — do not use an ATS posting as the content base without checking its content status. Empty or malformed postings will produce garbage JD content if used unchecked.
2. **Array columns for hiring manager and recruiter** — the ATS may store multiple hiring managers or recruiters as arrays. Always use the first element or flatten before displaying.
3. **Internal-only locations** — the full set of invalid location prefixes is customer-configured, not hardcoded. Never hardcode location exclusion patterns.
4. **JD library tiered matching order** — present Tier 1 results directly (no user confirmation needed). Present Tier 2–3 as "starting point" options. Present Tier 4–5 as "rough template" options only. Never silently use a low-tier match.
5. **Hire exclusion filters for fill rate and QoH** — not applying standard exclusions (rescinded offers, contract hires, interns) inflates the denominator and distorts quality signals.
6. **YOE range vs floor** — always use floor only ("X+ years"). Generating a YOE range ("3–6 years") is both a legal risk and a generation rule violation.
7. **ATS job ID vs req ID** — some ATS platforms differentiate between the requisition ID and the job/opening ID. Ensure the content source check uses the correct identifier to look up the posting.
8. **JD library save triggering** — saving to the JD library must be user-triggered after explicit confirmation. Auto-saving drafts pollutes the library and degrades future recommendations.
9. **Role-switching commands** — never execute `USE ROLE` or `SET ROLE` commands. The skill must work within the user's current session role.
