---
name: interview_debrief
description: >
  Synthesize all interview scorecards for a candidate into a structured debrief with
  panel-alignment, interviewer-calibration, and signal-gap analysis to prepare a
  hiring-team discussion. Also supports an unranked side-by-side evidence view of
  candidates for an open requisition. Decision support only: it never makes or
  recommends a hire / no-hire decision and never ranks candidates.
  Triggers: debrief for [candidate], debrief application [id], compare candidates for
  [req], side-by-side for [req], candidate debrief.
  Do NOT use for: offer or compensation justification (use offer_builder),
  recruiting pipeline reporting (use good_morning_recruiting), or writing job
  descriptions (use job_description_builder).
license: Snowflake Skills License. See LICENSE.txt for complete terms
type: snowflake
language: en
status: Published
---

# Interview Debrief (Dehydrated)

## About this skill

- **What it does:** Pulls every interview scorecard for a candidate into one debrief: what the panel said, panel alignment, interviewer calibration (who tends to score high or low), gaps against the role, and open questions for the hiring team. Also shows candidates for one requisition side by side, unranked.
- **Who it's for:** Recruiters and hiring managers, written for non-technical readers.
- **Works with:** Part of the hiring flow: `job_description_builder` (write the role) → interviews → `interview_debrief` → `offer_builder` (justify the offer).
- **Before you start:** This skill runs on the data and definitions connected by **People Hydrator** (`plugins/people-hydrator/`). If it hasn't been set up for your account yet, run People Hydrator first.
- **How to use it:** Say "debrief for [candidate]" or give an application ID; "compare candidates for [requisition]" for a side-by-side.
- **If you don't get what you expect:** If a section is missing or says data isn't available, that data wasn't connected during setup: re-run People Hydrator to add it. If a number looks wrong, tell People Hydrator what you expected; it turns that into a checked correction. If the skill says a table or column has changed, re-run People Hydrator for this skill.
- **Good to know:** It only uses submitted scorecards; missing feedback is called out, not guessed. It prepares the discussion; it does not decide. The hiring team makes every hiring decision, and the skill never gives a hire / no-hire call, a fit verdict or a candidate ranking. The skill reads your data and never changes it.

## Intent

Synthesize all interview scorecards for a single candidate into a structured debrief that prepares recruiters and hiring managers for their own discussion. Cross-references the role spec to surface signal gaps, gives interviewer calibration context (hawk/dove patterns), and reports panel alignment so the hiring team's discussion is grounded in evidence rather than the loudest voice. The hiring team, not this skill, makes the decision.

Designed for **HRBPs and recruiters who are not technical**. Output must read like a hiring brief, not a database dump — never expose table names, column names, SQL, or row counts to the user.

This skill is for **per-candidate or per-requisition debriefs**. Not for: bulk pipeline reporting, recruiter scorecard automation, comp-band analysis, workforce-level talent analytics, screening candidates in or out, or ranking candidates.

### Human Decision Safeguards (MANDATORY)

1. **No decisions or verdicts.** Never output HIRE / NO HIRE / CONDITIONAL, "advance" / "reject", a fit verdict, a score that stands in for a decision, or wording such as "I recommend hiring". If asked "should we hire them?", say the decision belongs to the hiring team and offer the evidence summary and open questions instead.
2. **No ranking.** Never order candidates by strength, produce a rank, a composite or weighted score, a "top candidate" or "strongest [competency]" answer. Comparisons are side by side, in a neutral order (alphabetical by candidate name), with each candidate's evidence shown as recorded.
3. **Job-related evidence only.** Summarize only evidence tied to the role's documented competencies and requirements. Never use or relay "culture fit", personality, "vibe", likeability or similar non-job-related impressions as a strength or concern. If an interviewer's note relies on them, or on age, gender, race, ethnicity, national origin, religion, disability, pregnancy, family status, accent or any other protected characteristic, do not repeat the remark; note that "some feedback references non-job-related factors and should be reviewed with the recruiter."
4. **Every output carries the decision-support note** shown at the end of the debrief template.
5. **No automated actions.** Never change an application's stage or status, and never draft a rejection or advancement on the skill's own judgment.
6. **Customer obligations.** Where local law treats tools that assist hiring decisions as regulated (for example, bias-audit or candidate-notice requirements), the customer is responsible for meeting them before use. If a user asks, say so and suggest they check with their legal or People team; do not give legal advice.

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
   - Cross-candidate comparison: "Pulling panel evidence for all candidates on this requisition... (~10–15s)"
2. **While queries run** — execute silently. Do not echo query text, table names, row counts, or "Running query..." messages.
3. **After queries complete** — go straight to the synthesized output. No "I found N rows in X" preamble.

### Invocation Routing

| Trigger | Action |
|---------|--------|
| Application ID provided | Resolve by ID, run full debrief |
| Candidate name + role | Search active applications, present matches, confirm, then debrief |
| Job-level pending debriefs | List candidates for the requisition with debrief status |
| Cross-candidate comparison | Unranked side-by-side evidence table + optional competency view |
| Request to rank, pick, or score candidates for a decision | Decline the ranking (see Human Decision Safeguards); offer the side-by-side view instead |
| Calibration-only request | Skip full debrief, return interviewer pattern profile |
| Competency deep-dive | Cross-candidate competency heatmap with score bands |

### Workflow

**Step 0 — Access preflight.** Probe access to the underlying scorecard/application data before any other query. If access fails, do not surface the technical error. Tell the user their session may need a permissions update and to reach out to their People Analytics or HRIS team. Stop.

**Step 1 — Resolve candidate.** By application ID, or by name+role with disambiguation if multiple matches.

**Step 2 — Pre-debrief checks.** Detect interviewers who have not submitted scorecards. If any are missing, warn the user up front so they understand the debrief is incomplete.

**Step 3 — Gather data in parallel.** Pull scorecards, per-question rubric answers, role spec, interview timeline, and offer details (if any). The offer pull may legitimately return empty — Comp/Offer Context becomes conditional.

**Step 4 — Panel intelligence in parallel.** Compute interviewer calibration (hawk/dove/neutral) and panel alignment.

**Step 5 — Generate debrief.** Strict format enforcement (see below).

**Step 6 — Discussion support.** Offer follow-ups: a hiring-team discussion guide, the side-by-side view for the requisition, a deeper competency dive. Any summary drafted for the hiring manager follows the same safeguards: evidence and open questions, no verdict.

**Step 7 — Cross-candidate comparison (when invoked).** Show candidates for a requisition side by side, unranked and in alphabetical order, with each candidate's recorded panel signal and alignment, then optionally drill into the competency view.

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

Calibration is **context for the hiring team's discussion only**. Never use it to reweight, adjust or combine votes into a score, and never use it to order candidates.

### Confidence Handling

Apply to all panel-intelligence metrics (calibration sample size):

| Sample Size | Confidence | Display |
|-------------|------------|---------|
| n ≥ 10 | HIGH | Report normally |
| n = 5–9 | MEDIUM | Suffix "(limited data)" |
| n < 5 | LOW | Suffix "(low confidence)" |

**Skill-level confidence:**
- HIGH: scorecards + rubric + role spec + timeline all resolve. Run full debrief.
- MEDIUM: core resolves but calibration history is too thin. Run debrief; mark the calibration sub-section "(low confidence)" or omit per rules below.
- LOW on non-critical bindings (offer, calibration history): run with those sections gracefully omitted.
- LOW on critical bindings (scorecards or applications): skip skill execution; output the data domain description and recommend the customer connect their ATS scorecard data.

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
- 2–3 job-related gaps or risks, each tied to a role competency
- Calibration context may be noted (e.g., the interviewer usually scores lower), but never used to discount or amplify the concern

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

### 3. Discussion Points for the Hiring Team

**Evidence Summary:** 2–3 neutral sentences on what the panel's evidence shows against the role's competencies, grounded in Sections 2 and 4. No verdict.
**Open Questions:** competencies not assessed, split signals or missing scorecards the team should resolve
**Points to Discuss:** where interviewers disagree and what evidence each side cited

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

---

_Decision support only. This summarizes recorded interview evidence; it is not a hiring recommendation. The hiring team makes the decision._
```

**Rules:**
1. Sections 1–4 and the closing decision-support note are always present. Empty fields show "—", never blank.
2. Section 5 is conditional — include **only** if offer data exists; otherwise omit entirely.
3. Sub-elements within a section may be omitted if data is unavailable (e.g., calibration table) but never the section heading.
4. Tables use the exact column headers shown — no additions, removals, renames, or reorders.
5. Quotes use blockquote (`>`) with `— [Name], [Type]` attribution.
6. Whether the candidate has 2 scorecards or 20, the structure is identical. Only the content changes.

#### Cross-Candidate Comparison Format

```
## Cross-Candidate Comparison: [JOB_TITLE] ([REQ_ID])

| Candidate | Scorecards | Strong Yes | Yes | No | Def Not | Alignment | Signal Gaps |

_Candidates are listed alphabetically, not ranked. Decision support only; the hiring team makes the decision._
```

Optional competency view (when user requests deep-dive; candidates in the same alphabetical order, no totals or overall scores):

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
5. **Job-related only.** Hiring-manager screen feedback is summarized like any other round, against the role's competencies (e.g., communication clarity where the role requires it). Do not extract "culture fit", values-alignment or personality impressions; handle them per Human Decision Safeguard 3.

### Rubric JSON Parsing (Behavior Rule)

Many ATS systems store rubric answers as nested JSON objects (rich-text doc format) rather than flat strings. The skill must:

- Parse nested JSON to extract the leaf text value (typically a `content[].content[].text` style path).
- Recognize three answer shapes: pure numeric ("15"), text-with-embedded-score ("8 out of 10, communicated well"), and free-text feedback ("did not cover the topic").
- Extract max-weight from question titles when present (e.g., "Coding (max weight 25)") to normalize scores to a percentage.
- For "X out of Y" answers, normalize: `score = raw × max_weight / Y`.
- Score-band a normalized percentage: Strong (≥80%), Good (60–79%), Weak (<60%).

### Guardrails

- Recommendation values come in 4 levels (e.g., strong_yes, yes, no, definitely_not). The skill must handle the canonical 4-level scale and degrade gracefully to a 3-level or 2-level scale if the ATS uses a different schema.
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
| Non-critical query fails (calibration / offer) | Skip that section silently and continue generating the debrief with what's available. |
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

- (Optional) A semantic view over the application + scorecard + interview tables for Cortex Analyst-driven follow-up questions like "show me all candidates for REQ-12345 with a Strong Yes from 3+ interviewers".

- (Optional) A Cortex Search service over candidate / application narrative fields for fuzzy candidate name lookup when an exact name match fails.
