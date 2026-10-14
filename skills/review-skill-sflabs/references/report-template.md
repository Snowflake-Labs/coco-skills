# Report Template

> Authoritative markdown shape for the final report rendered by the router. Do not paraphrase. Substitute placeholders only — wording stays as-is.

## Template

```markdown
# Skill Review: <skill-name>

**Verdict:** <🟢 promote | 🟡 adapt | 🟠 skip>
**Confidence:** <high | medium | low>

<one-paragraph rationale, 2-3 sentences>

## Summary

| Check | Status |
|---|---|
| Format | <🟢 / 🟡 / 🟠> |
| Public-duplicate search | <🟢 / 🟡 / 🟠> |
| Data policy | <🟢 / 🟡 / 🟠> |
| Catalog fit | <🟢 / 🟡 / 🟠> |

## Mechanical fixes applied

- `<file:line>` — <what was changed and why>
- ...

(or "None — no automatic fixes needed" when nothing was applied.)

## Issues for you to fix

### 🟠 Needs Attention

- **<finding title>** (`<file>:<line>`)
  <one-sentence explanation>
  *Suggested fix:* <concrete change>

### 🟡 Opportunity for Improvement

- **<finding title>** (`<file>:<line>`)
  <one-sentence explanation>
  *Suggested fix:* <concrete change>

(Omit the entire "Issues for you to fix" section if there are no findings at either severity.)

## Disclosures recorded

(Only present if duplicate-search found anything.)

- "Adapted from `<url>`" — recorded for reviewer visibility.
- "Original work, similar public skill at `<url>`" — recorded for reviewer visibility.

## What was checked

- Bundled-skill catalog source: <disk | docs | disk+docs | skipped>
- Public-duplicate search queries: <list>
- Skipped checks: <list with reason>

---
*This review is advisory. Final acceptance is decided by reviewers on the pull request.*
```

## Verdict thresholds

- **🟢 promote** — All four checks 🟢. No `🟠 Needs Attention` findings. No mechanical fixes were needed, or they were applied cleanly with contributor approval.
- **🟡 adapt** — At least one check is 🟡 or 🟠, but format-check passes (after mechanical fixes). The contributor has issues to address before opening the PR, but the skill is fundamentally sound.
- **🟠 skip** — One of:
  - Catalog-fit shows the skill duplicates a bundled skill (shadowing per Labs priority order).
  - Format-check has unfixable structural problems (frontmatter doesn't parse even after attempted normalization, required fields missing with no clear fix).
  - Duplicate-search disclosed `adapted` AND no clear differentiation from the upstream source.

## Confidence

- **high** — All checks ran. No skipped checks.
- **medium** — 1-2 checks skipped (e.g. bundled-skill catalog fetch failed, web_search returned empty for unrelated reasons).
- **low** — 3 or more checks skipped, or both bundled-skill sources unavailable, or web_search blocked for the duration of the run.

## Tone rule (binding)

Every line in the rendered report must use advisory language from `data-policy-principles.md` Section 4. Never `critical`, `failure`, `violation`, `danger`, `urgent`, `high-risk`. Always `needs attention`, `opportunity for improvement`, `consider`, `recommended`.

Status indicators use only 🟢, 🟡, 🟠. Never 🔴.
