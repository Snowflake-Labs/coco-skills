---
name: review-skill-sflabs
title: Review Skill for SF Labs
summary: Pre-PR self-check that audits a local skill directory for Snowflake Labs catalog readiness.
description: >-
  Use when a contributor has built a Cortex Code skill locally and wants a
  pre-PR readiness check before opening a pull request against
  Snowflake-Labs/cortex-code-skills. Verdict: promote, adapt, or skip — with
  concrete fixes. Triggers: "review a skill", "is this skill ready for labs",
  "check this skill before PR", "audit skill for promotion", "does this skill
  belong in labs".
tools:
  - Read
  - Edit
  - Glob
  - Grep
  - Bash
  - web_search
  - web_fetch
prompt: "$review-skill-sflabs ~/.snowflake/cortex/skills/my-skill"
language: en
status: Published
author: Daniel Myers, Cortex Code DevRel
type: snowflake
demo-url: ""
---

# Review Skill for SF Labs

## When to Use

- A contributor has authored a skill locally and wants a pre-PR readiness check
- Before forking `Snowflake-Labs/cortex-code-skills` to submit a new skill
- To get a checklist of mechanical fixes plus advisory findings before reviewers see the work

## Overview

This skill inspects a local skill directory and produces an advisory report telling the contributor whether the skill is a fit for the public Labs catalog. It applies safe mechanical fixes in place (with the contributor's confirmation) and flags content rewrites that need human judgment. It does not fork, push, or open a PR — that is the contributor's call after seeing the report.

The review is advisory. Final acceptance is decided by reviewers on the pull request.

## Inputs

The skill takes one argument: a path to a skill directory containing a `SKILL.md`.

```
$review-skill-sflabs <path>
```

If no argument is provided, the skill uses the current working directory. If the path does not contain a `SKILL.md`, stop with a clear error.

## Workflow

### Phase 1: Locate and load

1. Resolve the input path. Validate that `<path>/SKILL.md` exists.
2. Read `SKILL.md` plus all sibling files under `<path>/references/`, `<path>/workflows/`, and `<path>/scripts/` into context. The `Glob` tool can enumerate them.

### Phase 2: Run sub-workflows in order

Run each sub-workflow and accumulate findings.

1. **Format check** — read `workflows/format-check.md` and execute its steps. Output: list of `(check_id, severity, evidence, fix, suggested_change)` findings.
2. **Duplicate search** — read `workflows/duplicate-search.md` and execute. May reach a stopping point at "your work or adapted from `<url>`?" — wait for the contributor's selection before proceeding.
3. **Data policy scan** — read `workflows/data-policy-scan.md` and execute. Loads `references/data-policy-principles.md` first.
4. **Catalog fit** — read `workflows/catalog-fit.md` and execute. Includes both bundled-skill overlap (disk + docs) and scope flexibility reasoning.

If any later phase depends on data an earlier phase failed to gather, mark that phase as `skipped` and continue.

### Phase 3: Apply mechanical fixes

Read `references/mechanical-fix-rules.md`. For each finding with `fix: mechanical`:

1. Show the proposed change to the contributor (a diff or a one-line description).
2. Ask for confirmation.
3. On approval, apply the change with the `Edit` tool (or create the file with `Write` for missing LICENSE).
4. Record the fix in the report's "Mechanical fixes applied" section.

Never apply mechanical fixes silently. The contributor stays in control.

### Phase 4: Render the report

Read `references/report-template.md`. Substitute findings into the template. The template defines verdict thresholds, confidence levels, and tone rules.

### Phase 5: Stop

After rendering the report, stop. Do not offer to fork the repo, push, or open a PR. Do not suggest "next actions". The contributor decides what to do with the report.

## Stopping Points

- ✋ End of Phase 1 if `SKILL.md` is not found at the path.
- ✋ During Phase 2, sub-workflow `duplicate-search` asks the contributor "your work or adapted?" — wait for an explicit answer.
- ✋ Before any mechanical fix in Phase 3 — confirm before applying.
- ✋ After Phase 5 — stop. Do not offer to PR.

**Resume rule:** Once the contributor confirms a stopping point, proceed without re-asking.

## Output

A single rendered report per `references/report-template.md`. Includes verdict, confidence, summary table of the four checks, mechanical fixes applied, manual issues to address, disclosures (if any), and an attribution of which sources were consulted.

## Common Mistakes

| Pitfall | Fix |
|---|---|
| Path argument points to a directory without `SKILL.md` | Stop in Phase 1 with a clear error; do not invent content |
| `Bash` probes for bundled-skill paths that don't exist | Treat each missing path as a non-event; the catalog-fit workflow handles this gracefully |
| Multiple findings overlap (e.g. tone violation that is also a hardcoded specific) | Emit each finding once, deduplicated by `check_id`; do not double-count in the verdict heuristic |
| Contributor selects "Adapted" in duplicate search | Continue running the rest of the checks; do not short-circuit the report |
| Sub-workflow file is missing | This is a `format-check` blocking finding; report it and skip the corresponding phase |
| `web_search` or `web_fetch` returns an error | Mark the affected check as `skipped`, drop confidence to medium, continue |

## Notes

- This skill is purely advisory. It does not gate PR acceptance. Reviewers on the pull request make the final call.
- Every check is heuristic, sourced from on-disk + live data + reasoning. There is no closed list of approved skills, banned patterns, or canonical sources baked into this skill.
- When the data-policy principles update, only `references/data-policy-principles.md` changes — the workflows reason against whatever the current file says.



