# Workflow: Duplicate Search

> Detect whether the target skill is a verbatim or near-verbatim copy of content already published on the public web. Use `web_search`, not a closed catalog. The detection is heuristic; the contributor confirms intent interactively.

## Inputs

- `<skill_dir>` — path to the target skill directory.
- The parsed frontmatter from format-check (specifically `name` and `description`).
- The body of `SKILL.md` (everything after the second `---`).

## Outputs

A single finding with:

- `check_id` — `duplicate.public_overlap`
- `severity` — `pass` if no overlap, `advisory` if disclosed as own work, `attention` if disclosed as adapted
- `evidence` — top URLs, contributor's disclosure
- `suggested_change` — only present if the skill duplicates upstream content with no differentiation

## Steps

### Step 1: Generate distinctive search queries

Build 2-3 queries from the target skill's content. The goal is to surface verbatim or near-verbatim copies, so prefer specific phrasing over keywords.

1. **Quoted skill name** — `"<name>" SKILL.md` (use the `name:` value verbatim, in quotes).
2. **Description fragment** — pick a 6-12 word fragment from the middle of the `description:` field. Avoid the leading `Use when` clause (too generic). Quote the fragment.
3. **Body section header phrase** — pick a top-level heading or distinctive sub-heading from the body (e.g. a step name, a table caption). Quote it. Skip if every heading is a generic word like "Overview" or "Inputs".

Aim for 2 queries minimum. A third only if a distinctive section header is available.

### Step 2: Run web_search per query

For each query, call `web_search` and capture the top 5 results.

### Step 3: Filter for likely duplicates

A result is a likely duplicate if any of these hold:

- URL ends in `SKILL.md` or `/SKILL` and content references skill frontmatter conventions
- URL is on `github.com`, `gitlab.com`, or any code-hosting domain, and points at a directory or markdown file with `SKILL.md` siblings
- URL is a blog post, X (Twitter) thread, or LinkedIn post that shows the skill body verbatim or with minor edits
- Title or snippet contains the skill name and at least one distinctive phrase from the queries

Aggregate up to 3 distinct URLs across all queries. De-duplicate by domain+path.

### Step 4: If no likely duplicates found

Emit:

```
{check_id: "duplicate.public_overlap", severity: "pass",
 evidence: "Searched <N> queries; no likely public duplicates surfaced."}
```

Skip to the end of this workflow.

### Step 5: Surface URLs and ask the contributor

Present the top URLs to the contributor:

```
I searched the public web for content matching this skill and surfaced these URLs:

  1. <url>
  2. <url>
  3. <url>

Is this your own work, or did you adapt it from one of these sources?

Choose one:
  - Mine — I authored this from scratch
  - Adapted — I started from one of the sources above
```

Wait for the contributor's selection. This is a **stopping point** — do not proceed without an answer.

### Step 6a: Contributor selects "Mine"

Emit:

```
{check_id: "duplicate.public_overlap", severity: "advisory",
 evidence: "Original work. Similar public skills found at: <urls>",
 disclosure: "Original work, similar public skill at <top-url>"}
```

The disclosure goes into the report's "Disclosures recorded" section so reviewers can see what surfaced.

### Step 6b: Contributor selects "Adapted"

Tell the contributor:

> Recorded. Note that PR review is human-in-the-loop and may decline duplicate skills regardless of authorship. Continuing with the rest of the checks.

Emit:

```
{check_id: "duplicate.public_overlap", severity: "attention",
 evidence: "Adapted from <top-url>. Other matches: <other urls>",
 disclosure: "Adapted from <top-url>"}
```

The disclosure goes into the report prominently. Verdict computation continues — do not stop the workflow.

## Summary status

- 🟢 — `pass` (no overlap)
- 🟡 — `advisory` (mine, but similar work exists)
- 🟠 — `attention` (adapted from a public source)

## Notes

- This workflow does not maintain a list of "approved" or "banned" upstream sources. Every check is a `web_search`. New skill repos and new public posts appear constantly; the workflow has to discover them, not match a list.
- If `web_search` returns an error or zero results across all queries, emit `severity: "skipped"` with `evidence: "web_search unavailable or returned no results"`. The router treats this as a skipped check and lowers confidence accordingly.
- Do not block the verdict on a skipped duplicate-search — the rest of the checks still produce a useful report.
