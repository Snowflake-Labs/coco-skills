---
name: job_description_builder
description: >
  AI-powered job description creator for recruiters. Generates, enhances, or
  clones job descriptions using content from the ATS (source of truth), a saved
  JD library, or fresh AI generation. Includes inclusivity and bias scanning,
  fill rate and quality-of-hire intelligence, multi-language support, and export
  to HTML (external / internal modes). Saves finalized JDs to a reusable JD
  library. Use for: create job description, write JD, generate JD, update JD,
  clone JD, post to ATS, JD for [req/role], improve job posting, bias check JD,
  translate JD, save JD to library.
  Do NOT use for: interview feedback (use interview_debrief), offer
  justification (use offer_builder), or recruiting metrics (use
  good_morning_recruiting).
license: Proprietary. See LICENSE.txt for complete terms
---

# Job Description Builder (Dehydrated)

## About this skill

- **What it does:** Creates, improves or clones a job description: starts from the posted description in your ATS, then your saved library, then generates one fresh only if neither exists. Checks for biased or non-inclusive wording, can translate, and exports to HTML.
- **Who it's for:** Recruiters, hiring managers and TA operations.
- **Works with:** The first step of the hiring flow: `job_description_builder` → interviews → `interview_debrief` → `offer_builder`.
- **Before you start:** This skill runs on the data and definitions connected by **People Hydrator** (`plugins/people-hydrator/`). If it hasn't been set up for your account yet, run People Hydrator first.
- **How to use it:** Say "create a JD for [requisition or role]", "improve this JD" or "bias check this JD". It always tells you where the content came from.
- **If you don't get what you expect:** If a section is missing or says data isn't available, that data wasn't connected during setup: re-run People Hydrator to add it. If a number looks wrong, tell People Hydrator what you expected; it turns that into a checked correction. If the skill says a table or column has changed, re-run People Hydrator for this skill.
- **Good to know:** The default export is the external (candidate-facing) version; recruiter notes appear only in the internal version when you ask for it. Pay is never put in a job description. The skill reads your data and only saves to your JD library when you ask.

## Intent

Generate, enhance, or clone job descriptions for open requisitions using a prioritized content source strategy: ATS published JD first, saved JD library second, similar saved JDs third, fresh AI generation last. Designed for recruiters and HR Business Partners who are not technical. Output must be polished, candidate-ready, and free of bias — not a database report. Includes recruiter intelligence annotations (fill rate, quality-of-hire signals) that are kept internal and not shared with candidates or hiring managers.

## Behavior

### Content Source Priority (MANDATORY — follow this order exactly)

| Priority | Source | When to Use |
|----------|--------|------------|
| **1. ATS Published JD** | Query ATS job postings for a published JD for this req | ALWAYS check first. If a published JD exists with valid content, use it as the BASE. Do NOT skip this step. Do NOT replace ATS content with generated content. |
| **2. JD Library (exact match)** | Query saved JD library by req ID + language | Only if no valid ATS JD found. Use a previously saved finalized JD. |
| **3. JD Library (similar match)** | Tiered fuzzy match: same job profile → same job family + grade → same job family | Only if no exact match. Present options to the user. |
| **4. Fresh Generation** | AI generation using job profile metadata and ATS Standard template | ONLY when all three above return nothing. Tell the user explicitly that this is fresh generation. |

**Decision tree:**
```
ATS JD exists with valid content?
  └─ YES → Use as BASE (ATS-First). Enhance with grade/comp context. → Step 4 (ATS-First)
  └─ NO  → JD Library exact match?
              └─ YES → Present saved JD. Offer: use as-is / edit / regenerate. → Step 7
              └─ NO  → JD Library similar match?
                          └─ YES → Present options. User picks template or starts fresh.
                          └─ NO  → Generate fresh from job profile metadata. → Step 4 (Fresh)
```

**Transparency rule:** Always tell the user where the JD content is coming from — ATS, library (exact), library (similar), or freshly generated.

### Step 0: Access Preflight (MANDATORY FIRST)

Before any other query, confirm the session role can access the recruiting data layer. If access fails, tell the user in plain, friendly language:
- Their current role doesn't have access.
- Which roles would give them access (recruiter read-only, HRBP read-only, recruiting coordination roles).
- How to switch: run `USE ROLE [role name]` then re-invoke the skill.
- Who to contact if they don't have any of those roles (People Data & Analytics team).
- STOP. Do not attempt any other queries.

**Do NOT tell the user which specific table or schema failed.** Never expose object names.

### Step 0b: Content Source Check (After Preflight)

Follow the Content Source Priority order above. Be transparent at each step.

**ATS JD validation:** Only treat an ATS posting as valid if its content status is `valid` (not empty, malformed, or too short). Reject malformed content and move to the next priority.

**JD Library tiered fuzzy matching:** When checking the JD library for a similar match, use tiered logic:
- Tier 1: Exact req ID match → use directly.
- Tier 2–3: Same job profile → present as starting point.
- Tier 4–5: Same job family + grade → present as loose template.
- If any tier returns a result, offer: use as-is, edit, or regenerate. User decides.

### Step 1: Resolve Input

Accept input as: req ID, job title + team, or job code + location.

**Requisition number handling:** If no req ID is provided and none found via search, ask the user if they have one. If not, proceed without a req ID — use the job title as the file identifier. **NEVER invent or fabricate a req ID.**

### Step 1b: Location Validation (MANDATORY when a location is present)

After resolving input, validate the location against the approved ATS location list.

**An invalid location:**
- Starts with a prefix indicating it is an internal-only HRIS designation not approved for job postings (the prefix and specific excluded values are configured at customer setup).
- Does not appear in the ATS location list at all.

**If the location is invalid:** Do NOT silently substitute. Tell the user the location isn't available for job postings and present up to 5 valid alternatives from the same country/region. Wait for the user to confirm a replacement before proceeding.

**If the location is valid:** Proceed without comment.

### Step 2: Pre-Flight Completeness Check

Check for missing req fields: grade, level, cost center, location, hiring manager. If anything is missing, surface it in plain language. **Do NOT block generation** — surface gaps without gating.

### Step 3: Gather Context

Pull job profile metadata: title, grade, level, department/team, location, hiring manager. Also check fill rate context for similar roles (see Context Queries).

### Step 3b: Language Option (Non-English Regions)

If the role location is in a non-English-primary region, offer: Full English, Full [Regional Language], or Bilingual. Regional language is inferred from the location. Bilingual mode presents English first, then the regional translation immediately below each section.

### Step 4: Generate JD

**Path A — ATS-First:** Use ATS JD as the BASE content. Parse into sections (overview, responsibilities, requirements, bonus/nice-to-have, why join). Present each section to the user to **keep**, **edit**, or **omit**. Enhance with grade-aligned YOE and any missing sections. Do NOT generate replacements for rich ATS content.

**Path B — Library Hit:** Present the cached JD. User can edit or approve as-is.

**Path C — Fresh Generation:** Generate using the JD Template Format below.

#### JD Template Format

Every JD follows this structure exactly — whether from ATS, library, or generated:

```
## [JOB_TITLE]
**Location:** [LOCATION] | **Workplace:** [REMOTE/HYBRID/ONSITE]
**Team:** [TEAM_NAME] | **Business Unit:** [BUSINESS_UNIT]

[Two standard "Why [Company]" opening paragraphs — verbatim from the customer's brand template at setup]

[Role overview — 1-2 sentences. Sets scene for the candidate: what the team owns, why this role matters. Do NOT start with "As a [title]". Lead with team context.]

### AS A [JOB_TITLE], YOU WILL:
[5-8 responsibility bullets. Active voice. Tool-agnostic framing — lead with what the role decides or influences, not what tools it operates.]

### OUR IDEAL [JOB_TITLE] WILL HAVE:
[5-8 qualification bullets. YOE floor only — never a ceiling. "X+ years preferred" or "X+ years of relevant experience". Grade-aligned per YOE guidance below. Degree requirements include "or equivalent experience".]

### BONUS POINTS FOR THE FOLLOWING:
[3-5 nice-to-have bullets. Aspirational, not exclusionary.]

### WHY JOIN OUR [TEAM_NAME] TEAM?
[1-2 paragraphs about team uniqueness and growth opportunities. If team context is unknown, ask the user for 2-3 sentences — NEVER fabricate a team description.]

[Two standard closing lines — verbatim from the customer's brand template at setup]
```

**Never use em-dashes (—) anywhere in generated JD content.** Replace with commas, colons, or restructured sentences.

#### Grade-to-YOE Mapping (Use Floors Only)

| Grade Level | YOE Guidance |
|-------------|-------------|
| Entry | 0+ years (new grad acceptable) |
| Early Career | 2+ years |
| Senior | 5+ years |
| Staff or first-line manager | 8+ years |
| Principal or senior manager | 10+ years |
| Director level | 12+ years |
| Senior Director / VP+ | 15+ years |
| Intern | Currently enrolled student — no YOE requirement |

**YOE rules:**
1. ALWAYS use floor only ("X+ years") — never write a range ("3-6 years"). Capping years is potentially age-discriminatory.
2. NEVER infer YOE from the job title or tech stack — use only the grade mapping.
3. For interns, use "Currently pursuing a BS/MS" — no YOE.
4. Phrase as preferred where possible: "2+ years of relevant experience preferred".
5. Always include "or equivalent experience" when listing degree requirements.

#### AI-Era Content Philosophy (Applies to ALL Technical and Business-Facing Roles)

**The test for every bullet:** "If an AI agent handled all the tool work tomorrow, would this bullet still matter?" If no — cut it or reframe it.

**Lead with:**
- Problem framing and structured analytical thinking
- Stakeholder influence and decision-making impact
- Proactive insight generation (surface trends before they're asked for)
- End-to-end ownership (scoping → delivery → alignment)
- AI-assisted workflows as core capability

**Demote or remove:**
- "Build and maintain dashboards" as a primary bullet
- "Write and optimize SQL" as a standalone bullet
- Tool-specific experience as top requirements (SQL, Tableau, specific vendors)
- YOE tied to a specific tool (e.g., "3+ years of [specific tool] experience")

**Tools belong in requirements as supporting capabilities, not the job's definition.**

**Team differentiation:** For the same job profile on different teams, the responsibilities and requirements sections MUST differ materially based on team context (team name, business unit). Never produce two JDs that are reworded copies of each other. If team context is insufficient, ask the user: "How do the teams differ so I can tailor each JD?"

### Step 5: Recruiter Intelligence Annotations (Internal Only)

After the JD, append an **Intelligence Brief** visible only to the recruiter — never to candidates or hiring managers.

```
---
### Recruiter Intelligence (Internal Only)

**Fill Rate Context:**
- Similar roles took ~[AVG_DAYS] days (median: [MEDIAN]) to fill
- Based on [N] closed roles in the last 24 months
- Confidence: [High/Medium/Low] | Match basis: [exact profile / fuzzy profile / job family]

**Req Completeness:** [X/5 fields populated] — [list missing]

**JD Source:** [ATS JD / Library match (exact/similar) / Freshly generated]

**Quality-of-Hire Signal:**
- Similar role hires had [X]% favorable quality-of-hire rating ([confidence])
```

### Step 6: Inclusivity & Bias Check

**Restricted content — never permit in any JD:**

| Restricted Item | Why Excluded |
|----------------|-------------|
| Compensation / salary / equity / bonus | Excluded globally — managed through the offer workflow |
| Age references (e.g., "recent grad requirement", "digital native", "young and energetic", YOE ceiling) | May constitute age discrimination |
| Race, ethnicity, national origin | Prohibited |
| Gender or gender identity | Prohibited — use gender-neutral language throughout |
| Immigration / visa / citizenship requirements | Creates legal exposure — handle through HR/Legal separately |
| Marital, family, disability, or religious status | Prohibited |

When restricted content is detected or requested, show an inline alert:
> ⚠️ **This content wasn't included — here's why:**
> - **[Item]** — [Plain-language reason]. [Whose policy or legal reason.]
> If you believe this is needed, reach out to your People Analytics or Legal team before publishing.

**Tone & Language Scan — Before presenting final JD:**
- Flag gendered language (e.g., "rockstar", "ninja", "aggressive") → suggest neutral alternatives.
- Flag age-biased terms (e.g., "digital native", "young and hungry", YOE ceiling) → remove.
- Flag exclusionary requirements (e.g., "must have [specific degree]" without equivalent experience clause).
- Flag unnecessary jargon that reduces applicant pool.
- Assign an **Inclusivity Score** (1–10):
  - Gender-neutral language: 0–3 pts
  - Requirement reasonableness (not inflated beyond grade): 0–3 pts
  - Active / inviting tone: 0–2 pts
  - Clear structure and readability: 0–2 pts
- If score < 7, auto-suggest fixes before presenting to the recruiter.

### Step 7: Conversational Refinement

After presenting the JD, offer adjustments and quick suggestions:
- Adjust tone (more technical, more leadership-focused)
- Add specific skills or technologies
- Remote/hybrid language
- DEI commitment statement
- AI/GenAI skills as nice-to-have
- Make more senior / more junior
- Shorten to under 500 words

### Step 8: Export & Save

**Present in Chat (always):**
- Default: **external** view — JD sections only. No recruiter intelligence, no HM/recruiter names, no inclusivity score, no internal grade number.
- Internal: Full JD + Recruiter Intelligence — only when user explicitly asks for "internal", "full", or "with intelligence".

**HTML Export (after chat preview):**
When offering export, frame it around the recruiter's use case:
1. "Post to ATS" → `mode="external"` — clean, candidate-ready
2. "Archive for debrief prep" → `mode="internal"` — full with recruiter intelligence
3. "Just save for now" → skip export, go to save flow

**Export format:**
- Styled HTML with brand colors, logo (configured at customer setup), print-optimized CSS with "Save as PDF" button.
- Zero external dependencies — uses only built-in modules.
- External mode strips: recruiter intelligence, HM/recruiter names, inclusivity score, internal grade, level.
- Internal mode: full intelligence with visual differentiation and "Confidential" footer.

**Save to JD Library (user-triggered, NOT automatic):**
After export, offer to save. Include clear warning: the library is a learning model — only save final, approved versions. If user confirms, save to the JD library with: req ID, job title, job profile, grade, team, location, source type, language, version number, saved-by user.

### Constraints

- NEVER include compensation, salary, equity, or bonus in any JD content (internal or external).
- NEVER include age references, visa/immigration status, or any legally protected characteristic.
- NEVER fabricate an "About the Team" section — always ask the user for team context.
- NEVER auto-generate or fabricate a requisition number.
- NEVER execute role-switching commands — use only the user's current session role.
- NEVER generate a standalone Python script for the user to run — always execute code inline.

### Error Handling

| Scenario | Response |
|----------|---------|
| Req ID not found | "I couldn't find that requisition. Double-check the req number in the ATS, or tell me the job title and I'll search for it." |
| Job is closed/archived | "That req was closed on [date]. Want me to use it as a starting point for a new JD?" |
| Multiple matches | Show a simple numbered list; ask user to pick one. |
| Invalid location | "The location [NAME] isn't available for job postings. Here are valid options nearby: [list]. Which one should I use?" Do NOT substitute silently. |
| Authorization / permissions error | "I wasn't able to pull data right now. This is usually a quick permissions fix — reach out to your People Analytics team." NEVER expose schema, table, or SQL error. |
| Save fails | "I wasn't able to save this JD. Reach out to your People Analytics team if this keeps happening." |
| Clone source not found | "I couldn't find that original JD. Want me to create a fresh one instead?" |

## Quality

This skill is tested against `evals/job_description_builder/golden_cases.md` and `evals/job_description_builder/eval_plan.md` (People Hydrator runs them in its Evaluate stage). They are tests, not runtime instructions: follow this file and its customer configuration (added at hydration).

## Context Queries

- An ATS jobs / requisitions table at one row per requisition, with req ID, job title, job profile (standardized title), grade, management level, department/team, business unit, cost center, location, workplace type (remote/hybrid/onsite), status (open/closed/archived), hiring manager (may be an array — use first element), and recruiter (may be an array) — used for job metadata to ground JD generation.

- An ATS job postings table at one row per published job posting, with job ID, posted JD content (full text or structured sections), and a content status indicator (valid / empty / malformed / too short) — used as Priority 1 content source. Only use if content status = `valid`.

- A JD library / cache table at one row per saved JD per language, with req ID, job title, job profile, grade, job code, job family, team name, business unit, location, source type (generated / ATS-enhanced / cloned / edited), version number, saved date, saved-by user, language code, and full JD content in markdown — used for Priority 2 and 3 content lookup.

- A fill rate context table at one row per filled/closed requisition per job profile, with req ID, job profile, grade, days to fill, close date, and hire exclusion flag — used for fill rate intelligence. Apply standard hire exclusion filters (exclude rescinded offers, contract workers, interns, fixed-term).

- A quality-of-hire survey table at one row per post-hire survey response, with application ID, numeric rating (1–5 scale), and survey date — used for QoH favorable % intelligence. Favorable = rating ≥ 3. De-duplicate to first survey per application.

- An ATS valid locations table listing all approved job posting locations with location name, country, and region — used for location validation (Step 1b). Locations not on this list or matching the internal-only prefix are invalid for job postings.

- (Optional) An ATS applications / sourcing table with source type group per application — used for sourced % context in the Recruiter Intelligence section.
