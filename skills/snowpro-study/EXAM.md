# Sub-Skill: Practice Exam Generator & Interactive Executor

This file is read and followed by the router skill (`SKILL.md`) when the user requests practice exams, quizzes, or score history.

---

## Anti-Hallucination Protocol (Inherited from SKILL.md)

For practice exam questions — accuracy is CRITICAL. A wrong "correct answer" trains the user to fail.

1. **Use the Documentation Access Strategy** from SKILL.md. Prefer `cortex search docs` if available, then `web_fetch`, then mark `[VERIFY]`.
2. **Query before writing questions:** For each domain being tested, query at least 2 relevant topics covering the key concepts. Use `cortex search docs "{concept}"` when the Knowledge extension is available. Questions must be grounded in confirmed behavior.
3. **Verify every correct answer:** The designated correct answer must be explicitly supported by documentation. If you cannot confirm it, do not include that question.
4. **Verify distractors are wrong:** Each distractor must be confirmably incorrect — not just "probably wrong." If a distractor might actually be correct, it's a bad question. Remove it.
5. **No ambiguous questions:** If reasonable experts could disagree on the answer, the question is flawed. Rewrite or discard it.
6. **Reference every answer:** Each answer explanation must cite the specific documentation source (search query or URL) that confirms it.
7. **Mark uncertain questions:** If forced to include a question where confidence is <95%, prefix it with `[VERIFY]` and note the uncertainty in the explanation.

---

## Capabilities

This sub-skill handles three activities:
1. **Generate** practice exam files (write to disk)
2. **Execute** practice exams interactively (in-session quiz)
3. **Track** score history and display trends

---

## ANSI/ISO/IEC 17024 Compliance Framework

All generated practice exam questions MUST adhere to the following psychometric and structural standards aligned with ANSI/ISO/IEC 17024 requirements for personnel certification examinations.

### Item Construction Rules

1. **Stem requirements:**
   - Must be a complete, standalone question ending with `?`
   - Must contain all information needed to answer without reading the options
   - Must test one specific knowledge point (not compound questions)
   - Must be free of irrelevant information or unnecessary complexity
   - Must avoid absolute terms ("always", "never") unless factually absolute
   - Must not use negatively worded stems ("Which is NOT...") UNLESS testing critical safety/security knowledge — in which case "NOT" or "EXCEPT" must be **bolded**

2. **Option requirements:**
   - Exactly 4 options per question (A, B, C, D) — 1 correct, 3 distractors
   - All options must be plausible to someone with partial knowledge
   - All options must be grammatically consistent with the stem
   - All options must be similar in length and specificity (no "obvious long answer")
   - Options must be mutually exclusive (no overlapping answers)
   - Options ordered logically: alphabetical, numerical, or conceptual progression
   - No "All of the above", "None of the above", or combination answers ("A and B")
   - No trick options, joke answers, or implausible distractors

3. **Prohibited patterns:**
   - Stems that give away the answer through grammatical cues
   - Options that are true statements but don't answer the specific question asked
   - "Gotcha" questions testing trivial or obscure details
   - Questions answerable through test-taking strategy rather than knowledge
   - Culturally biased or unnecessarily gendered language

### Cognitive Level Distribution (Bloom's Taxonomy)

| Level | Target % | Question Type |
|-------|----------|---------------|
| Remember / Understand | ~20% | Definitions, factual recall, concept identification |
| Apply / Analyze | ~50% | Scenarios, "which approach would you use", cause-effect analysis |
| Evaluate / Create | ~30% | Architecture decisions, troubleshooting, multi-factor trade-off analysis |

### Difficulty Distribution

| Difficulty | Target % | Description |
|------------|----------|-------------|
| 1 (Foundation) | ~25% | Core concepts any certified professional should know |
| 2 (Intermediate) | ~50% | Requires applied understanding and scenario analysis |
| 3 (Advanced) | ~25% | Complex scenarios requiring synthesis of multiple concepts |

### Question Domain Proportionality

For full mock exams, distribute questions proportional to domain weights from the exam guide:
- Calculate: `domain_questions = round(total_questions * domain_weight_pct)`
- Ensure total sums to the target question count
- Adjust rounding by giving extra questions to higher-weighted domains

---

## Output Modes

When the user requests a practice exam, determine the mode:

| User Intent | Mode |
|-------------|------|
| "practice exam", "generate questions" | Ask: Interactive or Write to file? |
| "exam", "quiz me", "test me" | Interactive (default) |
| "write practice exam", "generate exam file" | Write to file |
| "progress", "scores", "history" | Trend report |

---

## Mode 1: Write to File

### Output File
`SnowPro_{CertName}_Practice_{DomainN|Full}.md`

### Per-Domain Format (10-15 questions)

```markdown
# SnowPro {Cert Name} — Practice Questions: Domain {N}

> Generated from Snowflake documentation as of: {date}

**Domain:** {Domain Name} ({weight})
**Questions:** {count}
**Suggested Time:** {count * 1.5} minutes

---

### Question 1
**Domain:** {N} — {Domain Name} | **Level:** {Apply} | **Difficulty:** {2}

{Stem — complete scenario or question}

A. {Option A}
B. {Option B}
C. {Option C}
D. {Option D}

---

### Question 2
...

---
(All questions first, then answer key)
---

## Answer Key

### Question 1 — Answer: {B}

**Why B is correct:**
{Explanation grounded in Snowflake documentation/behavior}

**Why other options are wrong:**
- **A:** {Specific misconception this targets}
- **C:** {Why this is plausible but incorrect}
- **D:** {Why this is plausible but incorrect}

**Reference:** [{Doc page title}]({url})

---
```

### Full Mock Exam Format

```markdown
# SnowPro {Cert Name} — Full Practice Exam

> Generated from Snowflake documentation as of: {date}

**Exam Code:** {code}
**Questions:** {count — proportional to real exam}
**Time Limit:** {minutes — matching real exam}
**Passing Target:** {score}

**Instructions:**
- Answer all questions. There is no penalty for guessing.
- Questions are weighted equally.
- Mark questions you're unsure about and review before submitting.

---

## Exam Questions

### Question 1
**Domain:** {N} | **Level:** {level} | **Difficulty:** {diff}

{Stem}

A. {Option A}
B. {Option B}
C. {Option C}
D. {Option D}

---
(All questions without answers)
---

## Answer Key & Explanations

(Same detailed format as per-domain, with full explanations and references)

---

## Score Interpretation

| Score Range | Assessment |
|-------------|------------|
| 90-100% | Exam-ready. Focus review on any missed questions. |
| 75-89% | Strong foundation. Review weak domains before scheduling. |
| 60-74% | Additional study needed. Focus on domains below 70%. |
| Below 60% | Significant preparation needed. Work through deep-dives first. |

## Domain Performance Tracker

| Domain | Questions | Correct | Score |
|--------|-----------|---------|-------|
| 1. {Name} | {n} | ___ / {n} | ___% |
| ... | ... | ... | ... |
| **Total** | **{total}** | **___ / {total}** | **___% ** |
```

---

## Mode 2: Interactive Exam Execution

### Content-First Principle

**CRITICAL:** All exam content (questions, options, correct answers, explanations, references) MUST be fully generated upfront before any interactive delivery begins. This means:

1. **Generate the full exam** using the same question generation process and format as Mode 1 (Write to File)
2. **Write it to a file** — save as `SnowPro_{CertName}_Practice_{DomainN|Full}.md` (identical to Mode 1 output)
3. **Then deliver interactively** by reading from the generated file during the session

This approach:
- Reduces context/state overhead during the interactive session (questions are referenced, not held in memory)
- Ensures exam content is reproducible and reviewable after the session
- Allows the user to review all questions and explanations later regardless of how far they got interactively
- Decouples content quality (generation step) from delivery mechanics (interaction step)

### Exam Mode Selection

After generating the exam file, ask the user via `ask_user_question`:

| Mode | Behavior |
|------|----------|
| **One at a time + instant feedback** | Present one question, user answers, immediately show correct/wrong with full explanation, then next question |
| **One at a time, results at end** | Present questions one by one, collect answers silently, show all results only after completion (simulates real exam) |
| **Batched (5-10 questions)** | Present a batch, user answers all, show batch feedback, then next batch |

### Execution Flow

#### Setup (All Modes)

```
1. Generate full exam content (questions + answer key) following the Question Generation Process
2. Write to file: SnowPro_{CertName}_Practice_{scope}.md
3. Inform the user: "Exam generated and saved to {filename}. Starting interactive session..."
4. Read questions from the file during delivery (do NOT regenerate or hold in context)
```

#### Mode: One at a Time + Instant Feedback

```
1. Read next question from generated file, present with options (no answer visible)
2. Wait for user to respond (A/B/C/D or the text of their choice)
3. Read the corresponding answer from the file, immediately show:
   - Correct or Incorrect (your answer: X, correct: Y)
   - Full explanation (why correct answer is right, why chosen distractor is wrong)
   - Reference link
4. Running score: "Score so far: 7/10 (70%)"
5. Present next question
6. After final question: show full results summary
```

#### Mode: One at a Time, Results at End

```
1. Read next question from generated file, present with options
2. Wait for user response
3. Acknowledge only: "Recorded: B. Next question (4 of 15):"
4. Do NOT reveal correctness until all questions are answered
5. After final question: read answer key from file, show complete results with explanations
6. Show score summary and domain breakdown
```

#### Mode: Batched

```
1. Read next 5-10 questions from generated file, present in a numbered block
2. User responds with answers (e.g., "1-B, 2-A, 3-D, 4-C, 5-B")
3. Read corresponding answers from file, show batch results:
   - Per-question: correct/incorrect + brief explanation
   - Batch score: "Batch 1: 4/5 (80%)"
4. Present next batch
5. After final batch: show full results summary
```

### During Execution

- Track question number, domain, cognitive level, and user's answer
- Accept flexible answer formats: "B", "b", "Option B", full option text, or key phrase
- If ambiguous, ask for clarification
- Reference the generated file for all content — do not reconstruct questions or explanations from memory
- Allow user commands mid-exam:
  - **skip** / **pass** — mark unanswered (counted as incorrect)
  - **flag** / **mark** — note for review, still require an answer
  - **quit** / **stop** — end early, score only answered questions
  - **score** / **progress** — show current running tally

### Post-Exam Results

After completing (or quitting) the interactive exam:

```markdown
## Exam Results: SnowPro {Cert Name}

**Date:** {today}
**Mode:** {mode selected}
**Questions:** {answered}/{total} ({skipped} skipped)

### Overall Score: {correct}/{answered} ({pct}%)

{Pass/Fail assessment based on typical passing threshold}

### Domain Breakdown
| Domain | Correct | Total | Score | Status |
|--------|---------|-------|-------|--------|
| 1. {Name} | {n} | {n} | {pct}% | {Pass / Below threshold} |
| ... | ... | ... | ... | ... |

### Cognitive Level Performance
| Level | Correct | Total | Score |
|-------|---------|-------|-------|
| Remember/Understand | {n} | {n} | {pct}% |
| Apply/Analyze | {n} | {n} | {pct}% |
| Evaluate/Create | {n} | {n} | {pct}% |

### Flagged Questions for Review
- Q{n}: {stem preview} — Your answer: {X}, Correct: {Y}

### Missed Questions Summary
| # | Domain | Level | Your Answer | Correct | Key Misconception |
|---|--------|-------|-------------|---------|-------------------|
| {n} | {domain} | {level} | {X} | {Y} | {brief explanation} |
```

---

## Mode 3: Score History & Trending

### Persistent Score Tracking

After any practice exam completion (interactive or self-reported from a file-based exam), offer to record scores.

**File:** `SnowPro_{CertName}_Score_History.csv` (in the same directory as practice exam files)

**Format:**
```csv
date,exam_type,exam_scope,overall_correct,overall_total,overall_pct,domain_1_correct,domain_1_total,domain_1_pct,domain_2_correct,domain_2_total,domain_2_pct,...,domain_N_correct,domain_N_total,domain_N_pct,notes
2025-06-10,full,All Domains,48,65,73.8,12,16,75.0,10,13,76.9,...,,,,"First attempt"
2025-06-12,domain,Domain 3,9,12,75.0,,,,,,,9,12,75.0,,,,"Post deep-dive review"
```

**Columns are dynamic** based on the number of domains for that cert. Header row written on first creation.

### Recording Workflow

1. After displaying exam results (interactive) or when user self-reports scores:
   - "Would you like to record these scores to your history?"
2. If yes, read the existing CSV (if any) to get the header structure
3. Append the new row
4. If 2+ entries exist, automatically display the Trend Report

### Trend Report

Triggered by:
- Automatic: after recording scores when history has 2+ entries
- Manual: `$snowpro-study {cert} progress` / `scores` / `history`

```markdown
## Score Trend: SnowPro {Cert Name}

**Attempts:** {count} | **Date Range:** {first} to {latest}

### Overall Progress
| Attempt | Date | Type | Score | Change |
|---------|------|------|-------|--------|
| 1 | 2025-06-10 | Full | 73.8% | — |
| 2 | 2025-06-15 | Full | 78.5% | +4.7% |
| 3 | 2025-06-20 | Full | 82.3% | +3.8% |

### Domain Trends
| Domain | First | Latest | Change | Status |
|--------|-------|--------|--------|--------|
| 1. {Name} | 62.5% | 81.3% | +18.8% | Improving |
| 2. {Name} | 76.9% | 76.9% | +0.0% | Plateau |
| 3. {Name} | 83.3% | 75.0% | -8.3% | Needs review |

### Recommendations
- **Strengths (consistently >=80%):** {list domains}
- **Improving (upward trend):** {list domains}
- **Plateau (no change across 2+ attempts):** {list domains} — try a different study approach (deep-dive, flashcards, or hands-on labs)
- **Declining or weak (<70% latest):** {list domains} — prioritize deep-dive review before next attempt

### Readiness Assessment
- **Overall latest score:** {pct}% — {above/below} typical passing threshold ({passing_score})
- **Weakest domain:** {name} at {pct}%
- **Suggested next action:** {e.g., "Review Domain 3 deep-dive, then retake domain-specific practice questions"}
- **Estimated readiness:** {Ready to schedule / Needs more preparation / Not yet ready}
```

### Status Determination Logic

| Condition | Status |
|-----------|--------|
| Latest >= 80% AND trend is flat or up | Strength |
| Trend is up (latest > first by 5%+) | Improving |
| Latest within 3% of first across 2+ attempts | Plateau |
| Latest < first by 5%+ OR latest < 70% | Needs review |

---

## Question Generation Process

1. **Receive context from router:** Cert name, exam code, domain(s), topic lists, question counts.
2. **Fetch documentation:** 2+ pages per domain being tested.
3. **Generate questions:**
   - Distribute across cognitive levels per the Bloom's targets
   - Distribute across difficulty levels
   - For full exams, distribute across domains proportionally
4. **Self-validate each question:**
   - Is the correct answer confirmed by fetched docs?
   - Are all distractors definitively wrong?
   - Does the stem stand alone without the options?
   - Are options balanced in length and structure?
   - Does it test a single knowledge point?
5. **Compile into output format** (file or interactive session).
