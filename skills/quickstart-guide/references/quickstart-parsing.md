# Quickstart Parsing Reference

How to parse a Snowflake Quickstart markdown file into a staged learning plan.

## Quickstart Markdown Structure

### Frontmatter

The first lines contain codefence-free metadata (not YAML fenced with `---`). Fields appear as `key: value` pairs before the first heading:

```
id: getting-started-with-cortex-agents
summary: Build a sales intelligence agent with Cortex Agents
categories: Getting Started
environments: web
status: Published
feedback link: https://github.com/Snowflake-Labs/sfguides/issues
tags: Cortex, Agents, AI
authors: Author Name
duration: 45
```

**Key fields:**
- `id` — The canonical slug. This is the source of truth for matching to URLs. The folder name in the repo is usually the same but not always.
- `summary` — The Quickstart title. Use this for attribution.
- `categories` — Helps understand the domain (Getting Started, Data Engineering, Machine Learning, etc.)
- `status` — Should be "Published" for live Quickstarts.
- `duration` — Estimated minutes. Useful for setting learner expectations.
- `tags` — Snowflake features and topics covered.

### Step Structure

Steps are delimited by markdown headings. Each step is a section starting with `## <Step Title>`. Within a step:

- Prose explaining the concept
- Code blocks (fenced with triple backticks and a language tag: `sql`, `python`, `yaml`, `json`, `bash`)
- Images (ignore these — they're screenshots of UI flows)
- Bullet lists of instructions

### Common Step Patterns

| Quickstart Step | Maps To |
|-----------------|---------|
| Overview | Skip — extract title and description for attribution |
| Prerequisites | Diagnostics inputs — roles, privileges, features needed |
| Setup / Environment | Stage 1 — create schema, grants, warehouse |
| Core build steps (2-5 typically) | Stages 2-N — one per major deliverable |
| Steps requiring external tools | Optional stages — flag dependency, skip if unavailable |
| Conclusion / What You Learned | Skip — don't build anything. Use for summary content. |

## Mapping Rules

1. **Combine trivially small steps.** If two consecutive steps each do one SQL statement, merge them into one stage.
2. **Split large steps.** If a single step creates 5+ objects or spans 3+ distinct operations, split into multiple stages.
3. **Target 4-8 stages total.**
4. **Stage 1 is always environment setup** — schema creation, grants, warehouse. Even if the Quickstart spreads this across multiple steps.
5. **Optional stages get a clear marker** — note what external dependency is needed and what happens if you skip.
6. **Preserve execution order** — stages build on previous stages. Don't reorder unless there's a clear dependency issue in the source.

## Code Block Extraction

- SQL blocks: these are what gets executed (CLI) or written to files (UI)
- Python blocks: typically Snowpark, stored procedures, or UDFs — write to files
- YAML blocks: often semantic models or config — write to files
- Bash blocks: usually `snow` CLI commands or pip installs — adapt to the execution context

## Companion Repos

Many Quickstarts have a companion GitHub repo containing runnable code (SQL scripts, Python apps, YAML configs, notebooks). These are referenced in two places:

- **Frontmatter field:** `fork repo link: https://github.com/Snowflake-Labs/sfguide-getting-started-with-cortex-agents`
- **Inline in step content:** GitHub URLs in a "Clone this repo" or "Fork this repo" instruction, typically in the Prerequisites or Setup step

**Common naming pattern:** `Snowflake-Labs/sfguide-<slug>` (though not all follow this convention).

The companion repo may contain the actual SQL and Python code that the Quickstart's prose describes. When a companion repo exists, the code in it is often more complete and correct than code blocks extracted from the markdown steps.

## Handling Incomplete Quickstarts

Some Quickstarts are thin or poorly structured:
- If a step has no code blocks, it may be conceptual — explain the concept, don't try to build something that isn't defined
- If code blocks reference objects not created in earlier steps, the Quickstart assumes pre-existing data — check if SNOWFLAKE_SAMPLE_DATA or another common source is intended
- If the Quickstart uses hardcoded database/schema names, replace them with the learning schema (`LEARN_SNOWFLAKE_QUICKSTARTS.<slug>`)
