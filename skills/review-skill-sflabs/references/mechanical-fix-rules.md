# Mechanical Fix Rules

> Authoritative table for the `format-check` workflow. The router consults this file to decide whether a finding is auto-fixable or must be flagged for the contributor.

**Always confirm before applying any mechanical fix.** Show the proposed diff and ask the contributor.

## Auto-fix vs flag

| Issue | Mechanical fix? | Action |
|---|---|---|
| Frontmatter YAML doesn't parse | No | Stop. Show parse error to the contributor. |
| Required field missing entirely (e.g. no `prompt:`) | No | Flag with a suggested template value. |
| Required field present but empty | No | Flag; the agent does not invent content. |
| `name:` doesn't match folder name | Yes | Update `name:` to match the folder. |
| `name:` has uppercase, underscores, or more than 3 hyphens | No | Flag — renaming the folder has side effects the contributor must drive. |
| `title:` longer than 30 chars | No | Flag with a suggested truncation; let the contributor pick. |
| `summary:` longer than 140 chars | No | Flag with a suggested trim. |
| `description:` doesn't start with `Use when` | Yes | Replace leading `Use for` / `Used to` / similar with `Use when`. |
| `description:` contains first-person pronouns (`\b(I|My|my|We|we|Our|our|Us|us)\b`) | No | Flag the matches with a rewritten alternative. |
| `description:` missing `Triggers:` list | No | Flag with a suggested template. |
| `language:` missing | Yes | Add `language: en`. |
| `status:` missing | Yes | Add `status: Published`. |
| `type:` missing | No | Flag — value depends on author (`community` / `snowflake` / `partner`). |
| `LICENSE` missing, `type: community` | Yes | Create `LICENSE` with Apache 2.0. |
| `LICENSE` missing, `type: snowflake` | No | Flag — Snowflake employees supply their own license file. |
| `skill_metadata/` directory present | No | Flag — internal-only seed file; remove before committing. |
| Referenced sibling file missing (`workflows/X.md` / `references/Y.md` / `scripts/Z` linked in body) | No | Flag — the agent does not fabricate workflow content. |
| Tone violation in body (`critical` / `failure` / `violation` / `danger` / `urgent` / `high-risk`) | No | Flag with the replacement word from `data-policy-principles.md`. |
| 🔴 red emoji in body | No | Flag with 🟠 amber as the replacement. |
| Hardcoded customer identifier in body (account locator, specific schema name with `_PROD` etc., specific role name) | No | Flag — context-dependent rewrite, not mechanical. |
| Disclaimer missing on assessment-style output | No | Flag with the appropriate template from `data-policy-principles.md` Section 6. |

## Rule of thumb

Auto-fix only when there is exactly one correct value:

- Folder-name match → one correct `name:`
- Default language → `en`
- Default status → `Published`
- Default community LICENSE → Apache 2.0 (the canonical text in this repo's template)

Anything that requires choosing between alternatives, anything that touches body content, anything that changes meaning — **flag, don't apply**. The contributor stays in control of their own writing.
