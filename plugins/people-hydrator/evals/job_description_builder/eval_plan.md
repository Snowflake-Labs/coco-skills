# Eval Plan: job_description_builder

## Purpose

Checks that `job_description_builder` is connected to the right data in your account. People Hydrator grades the connection at the end of Bind with the rules below. The golden cases in `golden_cases.md` then test the skill's answers at the end of Tune.

## Behavior Definition

The hydrated skill must:
- Run an access preflight first and stop with a plain-language message if access fails
- Follow the content source priority strictly: ATS published JD (content status valid) → JD library exact match → JD library similar match → fresh generation, and tell the user which source was used
- Validate the location against the approved posting locations; if invalid, offer up to 5 valid alternatives from the same country / region and wait for the user
- Surface missing requisition fields without blocking generation
- Use the fixed JD template; grade-to-YOE floors only ("X+ years"), never a range
- Append an internal-only Recruiter Intelligence brief (fill rate, req completeness, JD source, quality-of-hire signal)
- Run the inclusivity and bias check with a 1–10 Inclusivity Score; never include compensation, age, visa or protected-characteristic content
- Default to the external view; save to the JD library only when the user asks

## Data the hydrator must find

Data needs from `catalog/people.json` (required ones in bold):

| Data need | What it holds | Used for |
|---|---|---|
| **`people.ats.jobs`** | One row per requisition: req ID, job title; optionally location, workplace type, status | Job metadata, role search |
| **`people.ats.job_postings`** | One row per published posting: job ID, JD content; optionally content status | Priority 1 content source |
| `people.jd.library` | One row per saved JD per language: req ID; optionally language, JD markdown, saved date | Priority 2 and 3 content sources, saving |
| **`people.jd.fill_rate`** | One row per filled requisition: req ID, days to fill; optionally close date | Fill rate intelligence |
| **`people.qoh.application_survey`** | One row per post-hire survey response: application ID, rating | Quality-of-hire signal |
| **`people.ats.valid_locations`** | One row per approved posting location: location name; optionally country, region | Location validation |
| `people.jd.sourced_context` | One row per application: application ID; optionally source type group | Sourced % context |

## Binding rules

G = must pass (otherwise key data isn't confirmed and the skill is hydrated as partial with a warning). P = needed for correct answers. S = optional; reported, not graded.

| ID | Rule | Passes when |
|---|---|---|
| G1 | Requisitions connected | Jobs table with req ID and job title, each at MEDIUM confidence or better |
| G2 | Most required data connected | At least 3 of the 5 required data needs have a table |
| P1 | ATS published JD | Postings table with job ID and JD content |
| P2 | ATS JD validity | Content status bound on the postings table |
| P3 | Location validation | Valid locations table with location name and country |
| P4 | Fill rate | Fill rate table with req ID and days to fill |
| P5 | Quality of hire | Survey table with application ID and rating |
| S1 | JD library exact match | JD library with req ID, language and JD markdown |
| S2 | Requisition context | Location and status bound on the jobs table |
| S3 | Workplace type | Workplace type bound on the jobs table |
| S4 | Sourcing context | Sourced context with application ID and source type group |
| S5 | Job grade | Job grade bound on requisitions (drives the years-of-experience mapping) |

```binding-gate
{"rules": [
  {"id": "G1", "title": "Requisitions connected", "check": "need", "need": "people.ats.jobs", "fields": ["req_id", "job_title"], "min_confidence": "MEDIUM"},
  {"id": "G2", "title": "Most required data connected", "check": "min_needs", "needs": ["people.ats.jobs", "people.ats.job_postings", "people.jd.fill_rate", "people.qoh.application_survey", "people.ats.valid_locations"], "min": 3},
  {"id": "P1", "title": "ATS published JD", "check": "need", "need": "people.ats.job_postings", "fields": ["job_id", "jd_content"], "min_confidence": "MEDIUM"},
  {"id": "P2", "title": "ATS JD validity", "check": "need", "need": "people.ats.job_postings", "fields": ["content_status"], "min_confidence": "MEDIUM"},
  {"id": "P3", "title": "Location validation", "check": "need", "need": "people.ats.valid_locations", "fields": ["location_name", "country"], "min_confidence": "MEDIUM"},
  {"id": "P4", "title": "Fill rate", "check": "need", "need": "people.jd.fill_rate", "fields": ["req_id", "days_to_fill"], "min_confidence": "MEDIUM"},
  {"id": "P5", "title": "Quality of hire", "check": "need", "need": "people.qoh.application_survey", "fields": ["application_id", "rating"], "min_confidence": "MEDIUM"},
  {"id": "S1", "title": "JD library exact match", "check": "need", "need": "people.jd.library", "fields": ["req_id", "language_code", "jd_markdown"], "min_confidence": "MEDIUM"},
  {"id": "S2", "title": "Requisition context", "check": "need", "need": "people.ats.jobs", "fields": ["location", "status"], "min_confidence": "MEDIUM"},
  {"id": "S3", "title": "Workplace type", "check": "need", "need": "people.ats.jobs", "fields": ["workplace_type"], "min_confidence": "MEDIUM"},
  {"id": "S4", "title": "Sourcing context", "check": "need", "need": "people.jd.sourced_context", "fields": ["application_id", "source_type_group"], "min_confidence": "MEDIUM"},
  {"id": "S5", "title": "Job grade", "check": "need", "need": "people.ats.jobs", "fields": ["job_grade"], "min_confidence": "MEDIUM"}
]}
```

## Grading

- **INCOMPATIBLE**: any G rule fails. The skill can still be tuned and is hydrated as partial with a warning.
- **EQUIVALENT**: all G and all P rules pass.
- **DEGRADED**: all G pass, some P fail. The skill works; the failed P rules are named as gaps.

Every failed G or P rule becomes one "which table holds …?" question in Tune.

## Behavior the hydrated skill must preserve

| Component | Required behavior |
|---|---|
| Content source priority | ATS (valid status only) → library exact → library similar → fresh; no skipping; source always stated |
| Location validation | Invalid if not on the approved list or matching the internal-only prefix; up to 5 same-country/region alternatives; never substitutes silently |
| Grade-to-YOE | Entry 0+, Early Career 2+, Senior 5+, Staff / first-line manager 8+, Principal / senior manager 10+, Director 12+, Senior Director / VP+ 15+, Intern: currently enrolled |
| YOE floor | "X+ years" only, never a range; never inferred from title or tech stack |
| Template | Fixed section order; no em-dashes; "Why join" team text never fabricated |
| Restricted content | No compensation, age references, visa / citizenship, or protected characteristics |
| Inclusivity Score | 1–10 from the four defined components; fixes suggested when < 7 |
| Views | External by default; Recruiter Intelligence only on explicit request for internal |
| Fill rate / QoH | Standard hire exclusions; QoH favorable = rating ≥ 3, first survey per application |
| Saving | Only when the user confirms; never fabricates a req ID |

## Expected challenges

1. **Content status may not exist.** Some ATS postings have no explicit validity flag; the skill must still reject empty or malformed content before using it.
2. **Hiring manager / recruiter arrays.** These fields may be arrays; the skill uses the first element.
3. **JD library may be missing at first.** A new customer may have no saved JDs; priorities 2 and 3 return nothing and the skill moves to fresh generation.
4. **Internal-only locations.** The internal-only prefix and excluded values are set at customer setup; without them only the approved-list check applies.
