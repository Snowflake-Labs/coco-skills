# Sub-Skill: Flashcard Generator

This file is read and followed by the router skill (`SKILL.md`) when the user requests flashcards.

---

## Anti-Hallucination Protocol (Inherited from SKILL.md)

Before generating flashcards:
1. **Use the Documentation Access Strategy** from SKILL.md. Prefer `cortex search docs` if available, then `web_fetch`, then mark `[VERIFY]`.
2. **Query documentation:** For each domain being carded, query at least 1 relevant topic to confirm facts (defaults, syntax, limits). Use `cortex search docs "{topic}"` when available.
3. **Verify every "number" claim:** Any flashcard stating a specific value (e.g., "default temperature = 0", "max tokens = 8192", "context window = 32K") must be confirmed from documentation or marked `[VERIFY]`.
4. **No invented distinctions:** If creating a card about "X vs. Y," ensure both X and Y actually exist and behave as described. Query the relevant docs.
5. **Prefer omission over invention:** If you cannot confirm a fact, skip that card rather than risk generating a false flashcard. A wrong flashcard is worse than no flashcard.

---

## Format Selection

Before generating, ask the user via `ask_user_question`:

| Option | Description |
|--------|-------------|
| **Markdown Q/A** | Readable in any editor, good for on-screen review |
| **Anki CSV** | Tab-separated file importable into Anki (front/back/tags columns) |
| **Both** | Generate both formats side by side |

---

## Output Files

- Markdown: `SnowPro_{CertName}_Flashcards_{DomainN|All}.md`
- Anki CSV: `SnowPro_{CertName}_Flashcards_{DomainN|All}.csv`

---

## Markdown Format

```markdown
# SnowPro {Cert Name} — Flashcards: {Domain N: Name | "All Domains"}

> Generated from Snowflake documentation as of: {date}
> Cards per domain: 15-25

---

## Domain {N}: {Name}

### Card 1
**Q:** {Question — concise, targets one testable fact}
**A:** {Answer — brief (1-3 sentences), precise, includes the key distinguishing detail}

---

### Card 2
**Q:** {Question}
**A:** {Answer}

---
...
```

---

## Anki CSV Format

Tab-separated values. First row is a header. Tags use `::` hierarchy for Anki sub-decks.

```
front	back	tags
"{Question text}"	"{Answer text}"	"snowpro::{cert}::domain{N}::{subtopic}"
"{Question 2}"	"{Answer 2}"	"snowpro::{cert}::domain{N}::{subtopic}"
```

**Rules:**
- Escape any tabs or newlines within field values
- Use `<br>` for line breaks within Anki fields
- Double-quote all fields
- Tags are lowercase with `::` separators (Anki subdeck notation)

---

## Card Design Principles

### What makes a good flashcard:

1. **Atomic:** One fact per card. Never combine multiple testable points.
2. **Precise question:** The question must have exactly one correct answer — no ambiguity.
3. **Distinguishing answer:** The answer should include the detail that separates this fact from the most common misconception.
4. **Exam-relevant:** Every card should target something that could appear as a correct answer OR a plausible distractor on the exam.

### Card categories to cover per domain (15-25 cards each):

| Category | Count | Examples |
|----------|-------|----------|
| Exact defaults & limits | 3-5 | "Default temperature in COMPLETE?", "Max output tokens?" |
| Syntax & function signatures | 3-5 | "What function generates 768-dim embeddings?", "Required args for CORTEX SEARCH SERVICE?" |
| Feature distinctions | 3-5 | "RAG vs. fine-tuning: which modifies model weights?", "COMPLETE vs. AI_COMPLETE: what's different?" |
| Required privileges & roles | 2-3 | "What role is needed for Cortex functions?", "Default privilege for AI functions?" |
| Behavioral facts | 3-5 | "Is COMPLETE stateful or stateless?", "Does Cortex Guard add cost?" |
| Architecture & design choices | 2-4 | "When to use Cortex Search vs. manual RAG?", "What does fine-tuning create (full model or adapter)?" |

### What to avoid:

- **Trivial/obvious cards:** "What does LLM stand for?" — too easy, not exam-differentiating
- **Overly broad cards:** "Explain RAG" — not atomic, answer would be a paragraph
- **Unverifiable claims:** If you can't confirm it from docs, skip it
- **Implementation details that change:** Avoid cards about specific model version availability unless it's a key exam point

---

## Generation Process

1. **Receive context from router:** Certification name, domain(s) to cover, topic lists from cache.
2. **Fetch docs:** For each domain, fetch 1-2 key documentation pages.
3. **Extract facts:** From fetched docs, identify concrete testable facts (numbers, syntax, behaviors, distinctions).
4. **Generate cards:** Create 15-25 cards per domain following the categories above.
5. **Self-check:** Review each card — is the answer unambiguous? Is it confirmed from docs? Does it target an exam-relevant distinction?
6. **Format output:** Write in the user's chosen format(s).

---

## Multi-Domain Generation

When generating flashcards for "All Domains":
- Generate cards grouped by domain with clear section headers
- Include a count summary at the top: "Total: {N} cards across {M} domains"
- For Anki CSV, all cards go in one file with domain-specific tags for filtering
