# Sub-Skill: Domain Deep-Dive Generator

This file is read and followed by the router skill (`SKILL.md`) when the user requests domain deep-dives.

---

## Anti-Hallucination Protocol (Inherited from SKILL.md)

Before generating ANY deep-dive content:
1. **Use the Documentation Access Strategy** from SKILL.md. Prefer `cortex search docs` if available, then `web_fetch`, then mark `[VERIFY]`.
2. **Query documentation:** For each subtopic in the domain, query at least 2 relevant topics (ideally 3-4). Use `cortex search docs "{subtopic keyword}"` when the Knowledge extension is available, or `web_fetch` against docs.snowflake.com as fallback.
3. **Ground all claims:** Every factual statement about Snowflake behavior (syntax, defaults, limits, availability) must come from a documentation query or fetched page. Include the source (query or URL) as a reference link.
4. **Mark uncertainties:** If a specific detail cannot be confirmed from documentation, mark it with `[VERIFY]` inline.
5. **No invented syntax:** Never generate SQL examples with made-up function names, parameters, or behaviors. If unsure, query the docs or mark `[VERIFY]`.
6. **Date-stamp content:** Include a "Content sourced from docs as of: {date}" note at the top of each deep-dive.

---

## Output File

`SnowPro_{CertName}_Domain{N}_DeepDive.md`

One file per domain. If multiple domains are requested, generate one file each.

---

## Generation Process

1. **Receive context from router:** Certification name, exam code, domain number, domain name, domain weight, and topic list from the cached exam guide.
2. **Plan subtopics:** Break the domain into 4-8 subtopics based on the exam guide topics. Each becomes a numbered section (e.g., 3.1, 3.2, ...).
3. **Fetch documentation:** For each subtopic, identify and fetch the most relevant Snowflake doc page. Record the URL.
4. **Generate content:** Write each section grounded in the fetched documentation.
5. **Add exam traps:** Synthesize a final section of common misconceptions and exam pitfalls.
6. **Add reference links:** Compile all fetched URLs into a Quick Reference Links section.

---

## Output Structure

```markdown
# Domain {N} Deep-Dive: {Domain Name}

> Content sourced from Snowflake documentation as of: {date}
> Exam: SnowPro {Tier}: {Cert Name} ({Exam Code})
> Domain weight: {weight}

This domain tests your understanding of {brief domain description derived from exam guide topics}.

---

## {N}.1 {Subtopic Title}

**{Concept explanation — what it is and why it matters for Snowflake practitioners}**

{Detailed content with:}
- Bullet points for key facts
- Definitions of terminology
- How this works specifically in Snowflake (not generic theory)

**Snowflake implementation:**
```sql
-- Example demonstrating the concept
-- Must be valid, runnable Snowflake SQL
SELECT ...;
```

**Key details:**
| Aspect | Detail |
|--------|--------|
| Default value | {from docs} |
| Limits | {from docs} |
| Required privileges | {from docs} |
| Availability | {regions, editions, etc.} |

---

## {N}.2 {Next Subtopic}
...

---

## {N}.X Decision Frameworks

When the domain involves choosing between approaches, include a decision table:

| Scenario | Recommended Approach | Why |
|----------|---------------------|-----|
| {scenario 1} | {approach} | {rationale from docs} |
| {scenario 2} | {approach} | {rationale} |

---

## {N}.Y Key Exam Traps for This Domain

Minimum 4 traps. Format:

1. **{Misconception}** — {Why it's wrong and what the correct answer is. Reference the specific doc if applicable.}
2. **{Trap 2}** — ...
3. **{Trap 3}** — ...
4. **{Trap 4}** — ...

---

## Quick Reference Links

- [{Topic 1} Documentation]({fetched_url_1})
- [{Topic 2} Documentation]({fetched_url_2})
- ...
- [Official Exam Guide]({exam_guide_url})
- [Schedule your exam](https://learn.snowflake.com/en/certifications/)
```

---

## Quality Standards

- **Minimum content per section:** Each subtopic section must have:
  - A concept explanation (2-4 sentences minimum)
  - At least one concrete example (SQL, configuration, or ASCII diagram)
  - A key details table OR comparison table where applicable
- **Decision frameworks:** Required whenever a domain involves "when to use X vs. Y" choices
- **Exam traps:** Minimum 4 per domain. These must target real misconceptions, not trivial gotchas.
- **SQL examples:** Must be syntactically valid Snowflake SQL. Use realistic table/column names. Include comments explaining what the query does.
- **ASCII diagrams:** Use for architecture or data flow concepts where a visual aids understanding:
  ```
  Source → [Stage] → [COPY INTO] → Raw Table → [Stream] → [Task] → Curated Table
  ```
- **No padding:** Do not add filler content to increase length. Every sentence should convey testable information.

---

## Content Principles

1. **Snowflake-specific, not generic:** If explaining "embeddings," explain how Snowflake implements them (EMBED_TEXT_768, VECTOR type, etc.), not what embeddings are in general AI theory.
2. **Exam-relevant focus:** Prioritize content that distinguishes between options on a multiple-choice exam. "What makes this the right answer vs. the plausible wrong answers?"
3. **Precise language:** Use exact Snowflake terminology. Say "Cortex Search Service" not "search service." Say "COMPLETE function" not "completion endpoint."
4. **Current features only:** Do not reference deprecated features, preview-only features without noting their status, or features that may not exist. When uncertain, mark `[VERIFY]`.
5. **Practical over theoretical:** A data engineer taking this cert cares about "how do I use this" more than "how does the transformer attention mechanism work internally."
