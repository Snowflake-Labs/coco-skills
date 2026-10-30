---
name: interview_debrief
description: >
  Synthesize all interview scorecards for a candidate into a structured debrief with
  panel-alignment, interviewer-calibration, and signal-gap analysis. Also supports
  cross-candidate comparison and competency stack-ranking for an open requisition.
  Triggers: debrief for [candidate], debrief application [id], compare candidates for
  [req], stack rank candidates, who's the strongest [competency], calibration-weighted
  ranking, candidate debrief.
  Do NOT use for: offer or compensation justification (use offer_builder),
  recruiting pipeline reporting (use good_morning_recruiting), or writing job
  descriptions (use job_description_builder).
license: Proprietary. See LICENSE.txt for complete terms
---

# Interview Debrief (Dehydrated)

## About this skill

- **What it does:** Pulls every interview scorecard for a candidate into one debrief: overall recommendation, panel alignment, interviewer calibration (who tends to score high or low), and gaps against the role. Also compares or ranks candidates for one requisition.
- **Who it's for:** Recruiters and hiring managers, written for non-technical readers.
- **Works with:** Part of the hiring flow: `job_description_builder` (write the role) → interviews → `interview_debrief` → `offer_builder` (justify the offer).
- **Before you start:** This skill runs on the data and definitions connected by **People Hydrator** (`plugins/people-hydrator/`). If it hasn't been set up for your account yet, run People Hydrator first.
- **How to use it:** Say "debrief for [candidate]" or give an application ID; "compare candidates for [requisition]" for a side-by-side.
- **If you don't get what you expect:** If a section is missing or says data isn't available, that data wasn't connected during setup: re-run People Hydrator to add it. If a number looks wrong, tell People Hydrator what you expected; it turns that into a checked correction. If the skill says a table or column has changed, re-run People Hydrator for this skill.
- **Good to know:** It only uses submitted scorecards; missing feedback is called out, not guessed. The skill reads your data and never changes it.

## Intent

Synthesize all interview scorecards for a single candidate into a structured, executive-ready debrief that recruiters and hiring managers can act on. Cross-references the role spec to surface signal gaps, weights feedback by interviewer calibration (hawk/dove patterns), and reports panel alignment so hiring decisions are grounded in evidence rather than the loudest voice.

Designed for **HRBPs and recruiters who are not technical**. Output must read like a hiring brief, not a database dump — never expose table names, column names, SQL, or row counts to the user.

This skill is for **per-candidate or per-requisition debriefs**. Not for: bulk pipeline reporting, recruiter scorecard automation, comp-band analysis, or workforce-level talent analytics.

## Behavior

### Audience & Tone

- Plain, friendly language. Never display schema/table/column names, SQL errors, or stack traces.
- Frame insights as actionable recruiting context, not data analysis.
- If a data issue occurs, explain it simply (e.g., "I couldn't find scorecards for this candidate") rather than exposing internals.

### UX Execution Pattern

**Never expose SQL queries, table names, or execution logs to the user.** When data gathering is required:

1. **Before running queries** — show one friendly progress line with an estimated time:
   - Candidate search by name: "Searching for that candidate across active applications... (~5–10s)"
   - Full debrief: "Pulling scorecards, interview timeline, calibration data, and panel alignment for this candidate... (~20–30s)"
   - Cross-candidate comparison: "Comparing all candidates for this requisition with calibration-adjusted rankings... (~10–15s)"
2. **While queries run** — execute silently. Do not echo query text, table names, row counts, or "Running query..." messages.
3. **After queries complete** — go straight to the synthesized output. No "I found N rows in X" preamble.

### Invocation Routing

| Trigger | Action |
|---------|--------|
| Application ID provided | Resolve by ID, run full debrief |
| Candidate name + role | Search active applications, present matches, confirm, then debrief |
| Job-level pending debriefs | List candidates for the requisition with debrief status |
| Cross-candidate comparison / stack rank | Comparison summary table + optional competency heatmap |
| Calibration-only request | Skip full debrief, return interviewer pattern profile |
| Competency deep-dive | Cross-candidate competency heatmap with score bands |

### Workflow

**Step 0 — Access preflight.** Probe access to the underlying scorecard/application data before any other query. If access fails, do not surface the technical error. Tell the user their session may need a permissions update and to reach out to their People Analytics or HRIS team. Stop.

**Step 1 — Resolve candidate.** By application ID, or by name+role with disambiguation if multiple matches.

**Step 2 — Pre-debrief checks.** Detect interviewers who have not submitted scorecards. If any are missing, warn the user up front so they understand the debrief is incomplete.

**Step 3 — Gather data in parallel.** Pull scorecards, per-question rubric answers, role spec, interview timeline, and offer details (if any). The offer pull may legitimately return empty — Comp/Offer Context becomes conditional.

**Step 4 — Panel intelligence in parallel.** Compute interviewer calibration (hawk/dove/neutral), panel alignment, and quality-of-hire pattern matches against historical scorecard distributions.

**Step 5 — Generate debrief.** Strict format enforcement (see below).

**Step 6 — Decision support.** Offer follow-ups: hiring-manager summary draft, pipeline comparison, deeper competency dive.

**Step 7 — Cross-candidate comparison (when invoked).** Stack-rank candidates for a requisition with calibration-adjusted scoring and panel-alignment classification, then optionally drill into competency heatmaps.

### Calibration Logic

**Interviewer pattern classification** based on trailing 12-month recommendation distribution:

| Pattern | Criteria |
|---------|----------|
| Hawk | Bottom-tier ("no" + "definitely not") rate > 60% of total scorecards |
| Dove | Top-tier ("yes" + "strong yes") rate > 80% of total scorecards |
| Neutral | Neither hawk nor dove |

**Panel alignment** = (max recommendation count / total scorecards) × 100:

- Aligned (≥75%): strong consensus
- Mixed (50–74%): moderate alignment, discuss divergent views
- Split (<50%): no consensus, debrief discussion is critical

**Calibration-adjusted scoring** (cross-candidate ranking only):

- Hawk positive vote × 1.3 (rare positive signal worth more)
- Dove positive vote × 0.8 (common positive signal worth less)
- Neutral × 1.0
- This can change rankings: a "yes" from a Hawk should outweigh a "yes" from a Dove.

### Confidence Handling

Apply to all panel-intelligence metrics (calibration sample size, QoH pattern matches):

| Sample Size | Confidence | Display |
|-------------|------------|---------|
| n ≥ 10 | HIGH | Report normally |
| n = 5–9 | MEDIUM | Suffix "(limited data)" |
| n < 5 | LOW | Suffix "(low confidence)" |

**Skill-level confidence:**
- HIGH: scorecards + rubric + role spec + timeline all resolve. Run full debrief.
- MEDIUM: core resolves but calibration history or QoH pattern data is too thin. Run debrief; mark calibration / QoH sub-sections "(low confidence)" or omit per rules below.
- LOW on non-critical bindings (offer, QoH, calibration history): run with those sections gracefully omitted.
- LOW on critical bindings (scorecards or applications): skip skill execution; output the data domain description and recommend the customer connect their ATS scorecard data.

### Quality of Hire (QoH) Scoring

If a post-hire QoH survey or rating exists (1–5 scale):

- Favorable: rating ≥ 3
- Unfavorable: rating ≤ 2
- QoH Favorable % = favorable / total × 100
- De-duplicate to first survey per application
- Apply standard hire-exclusion filters (exclude internal transfers, rescinded offers, contractors, interns, fixed-term)

QoH pattern match: find historical hires with the same scorecard recommendation distribution as the current candidate, report their favorable% with a confidence label.

### Format Enforcement (MANDATORY)

The debrief output **must** follow this exact structure — no reordering, no merging, no invented sections.

```
## Candidate Debrief: [CANDIDATE_NAME]

---

### 1. Candidate Context
| Field | Detail |
|-------|--------|
| Role | [job title] |
| Grade / Level | [grade] — [level] |
| Team | [department / team] |
| Source | [source] ([source group]) |
| Recruiter | [recruiter name] |
| Current Stage | [stage] |
| Applied | [application created date] |
| Application ID | [id] |
| Status | [status] |

---

### 2. Evaluation Summary

**Strengths:**
- 2–3 themes synthesized across interviewers, not per-person raw dump
- Ground each strength in which interviews / competencies surfaced it

**Concerns:**
- 2–3 gaps or risks, calibration-adjusted where relevant
- Note when a concern came from a Hawk (expected) vs Neutral/Dove (more significant)

**Panel Signal:** [ALIGNED / MIXED / SPLIT] — [X]% agreement
| Recommendation | Count | Interviewers |
|----------------|-------|--------------|
| Strong Yes | X | [names] |
| Yes | X | [names] |
| No | X | [names] |
| Definitely Not | X | [names] |

**Interview Feedback Highlights:**
| Interview Round | What Went Well | Areas of Concern | Interviewer |
|-----------------|----------------|------------------|-------------|

---

### 3. Decision / Recommendation

**Recommendation:** [HIRE / NO HIRE / CONDITIONAL]
**Role Fit:** [Strong fit / Fit with gaps / Not a fit] for [role] at [grade]
**Rationale:** 2–3 sentences grounded in Section 2 and 4 evidence

[If CONDITIONAL]
**Conditions:** what needs to happen before extending

[If QoH pattern available]
**QoH Signal:** Candidates with similar scorecard patterns ([X] strong_yes, [Y] yes, [Z] no) had [X]% favorable QoH outcomes ([confidence label])

---

### 4. Supporting Evidence

**Key Interviewer Quotes:**
> "[Quote]" — [Name], [Interview Type]
> "[Quote]" — [Name], [Interview Type]

**Competency Breakdown:** (per competency / question)
| Interviewer | Response | Notes |
|-------------|----------|-------|

**Consensus:** [Agree/Disagree] — summary

**Interview Timeline:**
| Stage | Interview | Interviewer | Date | Recommendation | Calibration |
|-------|-----------|-------------|------|----------------|-------------|

**Signal Gaps vs Role Spec:**
- [Competency X] was must-have but not assessed
- [Competency Y] had split signals — needs discussion

**Interviewer Calibration:**
| Interviewer | T12M Strong Yes% | T12M No% | Pattern |
|-------------|------------------|----------|---------|

**Missing Scorecards:** [None / list]

---

### 5. Comp / Offer Context  *(only when offer data exists)*
| Component | Value |
|-----------|-------|
| Base Salary | … |
| Target Bonus | … |
| Equity | … |
| Equity Compa-Ratio | … |
| Sign-On | … |
| TTC / OTE | … |
| TTC Compa-Ratio | … |
| Relocation | … |
| Commission | … |
| Offer Status | … |
| Start Date | … |
| Days to Decision | … |
| Location | … |

[If role comp ranges are available]
**Market Context:** TTC compa-ratio is [X] — [at / above / below] market.
```

**Rules:**
1. Sections 1–4 are always present. Empty fields show "—", never blank.
2. Section 5 is conditional — include **only** if offer data exists; otherwise omit entirely.
3. Sub-elements within a section may be omitted if data is unavailable (e.g., calibration table, QoH line) but never the section heading.
4. Tables use the exact column headers shown — no additions, removals, renames, or reorders.
5. Quotes use blockquote (`>`) with `— [Name], [Type]` attribution.
6. Whether the candidate has 2 scorecards or 20, the structure is identical. Only the content changes.

#### Cross-Candidate Comparison Format

```
## Cross-Candidate Comparison: [JOB_TITLE] ([REQ_ID])

| Rank | Candidate | Company | Scorecards | Strong Yes | Yes | No | Def Not | Avg Score | Positive% | Cal. Score | Alignment |
```

Optional competency heatmap (when user requests deep-dive):

```
### Competency Comparison
| Interview Type | Competency | Candidate A | Candidate B | Candidate C |
|----------------|------------|-------------|-------------|-------------|
| Coding Interview | Coding (25) | 80% (Strong) | 50% (Weak) | 70% (Good) |
```

Score bands: Strong (≥80%), Good (60–79%), Weak (<60%).

### Interviewer Feedback Extraction Rules

The richest hiring signal is in free-text rubric answers, not numeric scores. Extract narrative feedback from qualitative questions ("What did the candidate do well", "What could the candidate have done better", "Feedback", "Interviewer Notes", "Questions asked").

1. **Summarize, do not dump.** 1–2 most actionable points per interviewer per round.
2. **Look for themes across interviewers.** 3 of 4 mentioning strong communication is high-confidence. 1 of 4 flagging a concern is noted but weighted accordingly.
3. **Flag contradictions.** If one interviewer says "excellent problem-solving" and another says "struggled with the approach", surface both with the split.
4. **Connect Section 2 to Section 4.** Every Strength and Concern must be traceable to a specific quote or rubric answer in Section 4.
5. **Hiring-manager screen feedback** carries strongest culture-fit signal — call out values alignment and communication clarity.

### Rubric JSON Parsing (Behavior Rule)

Many ATS systems store rubric answers as nested JSON objects (rich-text doc format) rather than flat strings. The skill must:

- Parse nested JSON to extract the leaf text value (typically a `content[].content[].text` style path).
- Recognize three answer shapes: pure numeric ("15"), text-with-embedded-score ("8 out of 10, communicated well"), and free-text feedback ("did not cover the topic").
- Extract max-weight from question titles when present (e.g., "Coding (max weight 25)") to normalize scores to a percentage.
- For "X out of Y" answers, normalize: `score = raw × max_weight / Y`.
- Score-band a normalized percentage: Strong (≥80%), Good (60–79%), Weak (<60%).

### Guardrails

- Recommendation values come in 4 levels (e.g., strong_yes, yes, no, definitely_not). The skill must handle the canonical 4-level scale and degrade gracefully to a 3-level or 2-level scale if the ATS uses a different schema.
- Performance ratings (if cross-referenced for QoH) are sensitive — never surface them as the justification in a candidate-facing or HM-facing summary.
- Never compare named interviewers to other named interviewers in a way that would shame a calibration-Hawk or Dove. Report patterns descriptively, not pejoratively.
- Data from before an ATS migration boundary may be incomplete — if the customer has indicated a migration cutover date, focus on data after it for reliable insights.

### Error Handling

| Scenario | Action |
|----------|--------|
| Application not found | "No application found for [ID]. Try searching by candidate name." |
| No scorecards submitted | "No scorecards found for this application. Interviews may not have occurred yet." |
| Multiple name matches | Present table of matches with job title + status, ask user to select |
| Application archived/rejected | Proceed but note: "This application is [status]. Debrief is historical." |
| Authorization / permissions error | "I wasn't able to pull data for this candidate right now — this usually means your account needs a permissions update. Reach out to your People Analytics team and they can get you set up." Never expose schema names, table names, SQL errors, or "object does not exist" messages. |
| Non-critical query fails (calibration / QoH / offer) | Skip that section silently and continue generating the debrief with what's available. |
| Query timeout | "This is taking longer than expected — the system might be busy. Want me to try again in a moment?" |

**Authorization error rules:**
1. Never expose the error text, schema, table, or query reference.
2. Never tell the user what specific role they lack.
3. Always offer an alternative path: "In the meantime, can you share the candidate name and role so I can try a different lookup?"
4. Log the underlying error in tool output for debugging, but never in the user-facing response.

## Quality

This skill is tested against `evals/interview_debrief/golden_cases.md` and `evals/interview_debrief/eval_plan.md` (People Hydrator runs them in its Evaluate stage). They are tests, not runtime instructions: follow this file and its customer configuration (added at hydration).

## Context Queries

- A scorecards table at one row per scorecard submission per (application, interviewer), with columns for application ID, scorecard ID, interviewer user ID, scorecard title (interview type), overall recommendation (4-level: strong_yes / yes / no / definitely_not), submission timestamp — used to compute panel signal, count by recommendation, and link interviewers to interviews.

- A scorecard rubric / per-question answers table at one row per (scorecard, question), with question text, answer text or JSON, scorecard reference, interviewer reference. Free-text answer fields contain the richest narrative feedback. Used for Strengths/Concerns synthesis, key quotes, and competency breakdown. May require nested-JSON parsing to extract leaf text.

- An applications table at one row per application, with application ID, candidate name and basics (background, education, experience), job ID, current stage, status, source, recruiter ID, application created date — used for Section 1 candidate context.

- A jobs / requisitions table at one row per job, with job ID, title, grade, level, profile/competencies, department/team, optional comp ranges (base, OTE, equity) — used for role-spec gap analysis and Section 5 market context.

- An interviews table at one row per interview event, with application ID, scorecard ID link, interviewer participation, stage name, stage group, stage order, interview date — used for the interview timeline and stage ordering.

- A users / interviewer directory at one row per interviewer, mapping interviewer user ID to full name and email — used for human-readable interviewer names everywhere they appear.

- (Optional) An offers table at one row per offer per application, with status (accepted / declined / open / withdrawn), offer extended date, accept-by date, base salary, target bonus, equity grant and value, sign-on, TTC/OTE, compa-ratios, relocation, commission, work location, region, days to decision — used for Section 5. Empty result is normal.

- (Optional) A post-hire quality-of-hire / new-hire survey table at one row per (application, survey response) with a 1–5 numeric rating and a hire/exclusion flag — used for QoH pattern matching when sample size permits.

- (Optional) A semantic view over the application + scorecard + interview tables for Cortex Analyst-driven follow-up questions like "show me all candidates for REQ-12345 with a Strong Yes from 3+ interviewers".

- (Optional) A Cortex Search service over candidate / application narrative fields for fuzzy candidate name lookup when an exact name match fails.
