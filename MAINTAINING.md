# Maintaining this repo

How skills get reviewed and merged. Read alongside [CONTRIBUTING.md](./CONTRIBUTING.md) if you're submitting, or if you're reviewing.

## Reviewers

A small named set of maintainers rather than an open rotation — skills execute against a user's Snowflake account, and a consistent bar matters more than throughput.

- Celeste Horgan (DevRel) — owner
- Luke Hartzell (DevRel)

Enforced through [CODEOWNERS](.github/CODEOWNERS), not convention.

**TODO — needs stakeholder input:**

1. Who is the third reviewer on the field/FDE side, and are they a full approver or a domain consult?
2. Coverage when a maintainer is out — does a PR wait, or is there a named backup?
3. Two maintainers is a bottleneck at current volume. What's the trigger for adding more?

## Scope

**TODO — needs stakeholder input.** The repo name is narrower than what people submit:

1. Skills for other Snowflake agent surfaces, or CoCo only?
2. General-purpose agent skills with no Snowflake dependency?
3. Do plugins stay a separate path (`plugins/`) or fold into the same flow?

Until settled: merge anything that works in CoCo, and flag out-of-scope submissions on the PR rather than closing them.

## Employee submissions

Snowflake-authored skills are reviewed internally before being opened here, and arrive with an attestation naming the person who cleared them. Maintainers rely on that rather than repeating the review — a missing attestation is a blocker, not a formality.

Community and partner skills go straight to review here.

## What automation checks

Don't re-check the left column by hand; do read for the right one.

| Machine-checked (blocking) | Human-only |
| --- | --- |
| Frontmatter present and valid YAML | Whether `scripts/` code is genuinely *safe* |
| Required fields, valid `type` and `status` | Whether destructive ops are gated on confirmation |
| Folder name matches `name` | Trigger hygiene and scoping |
| Licence matches declared `type` | Whether the skill actually works |
| Name collisions with repo or bundled skills | Overlap judgement between competing skills |
| Internal-reference patterns | Positioning and product claims |

Implemented in [validate_skill.py](.github/scripts/validate_skill.py) and [scan_internal_refs.py](.github/scripts/scan_internal_refs.py). Both run read-only, with no secrets, and never execute contributor code.

Credential scanning is handled separately and is not rechecked here.

### Internal-reference scan

Blocks structural markers that won't resolve for an external reader: `go/` links, Google Workspace and Slack links, internal ticket keys, non-public hostnames, confidentiality banners. Contains no product names — this repo is public.

Pre-existing findings sit in a baseline so the check could be required without a cleanup first. New findings fail; baselined ones report as debt. Suppress a false positive with an `internal-ref-ok` marker on the line or the line above.

### Escalation labels

[route-review.yml](.github/workflows/route-review.yml) reads the diff and applies `needs: sql-write-review`, `needs: security-review`, `needs: code-review`, `needs: egress-review`, `needs: product-confirmation`, or `needs: positioning-review`.

Labels prompt a human; CODEOWNERS gates. Keep it that way — a keyword labeler that blocks merge produces false positives and gets routed around within a month.

## Evaluation criteria

A "no" on anything blocking means it doesn't merge. (CI) marks what's automated.

- **Structure and licence.** (CI) Required frontmatter, folder matches `name`, licence matches `type` and author domain.
- **No name collision.** (CI)
- **No secrets, internal references, or customer data.** Link and reference shapes are (CI). Customer identification is a human read.
- **Scripts are readable and safe.** Reviewed line by line. No piping remote content to a shell, no writes outside the working directory, dependencies justified.
- **Destructive operations are gated.** Anything that drops, replaces, or grants must confirm with the user first. `CREATE OR REPLACE` against user-named objects counts.
- **Trigger hygiene.** Concrete trigger phrases, plus explicit "Do NOT use for…" where an adjacent skill exists. Overly broad triggers are the most common reason a PR gets held.
- **Tested.** Contributor attests; reviewer spot-checks one path.

Non-blocking, request changes: `SKILL.md` under ~500 words with reference material in `references/` (CI warns); numbered workflow with a Common Mistakes section; correct Snowflake SQL idioms; README catalog entry.

## Escalation

Get a second approval before merging when the PR:

- **Writes to Snowflake** — any DDL, DML, grants, role or policy changes.
- **Touches security or governance** — RBAC, masking, row access, network policy, auth, secrets.
- **Ships executable code** with dependencies or external calls.
- **Changes CI.** Workflows run with repository credentials.
- **Covers a preview or unreleased feature.** Needs product confirmation it's public-safe.
- **Comes from a partner and names their product.** Second reader on positioning claims.
- **Sends data outside Snowflake.** Any egress.
- **Overlaps an existing skill.** Decide whether to merge, scope down, or reject.

When in doubt, ask. It's cheap.

**TODO — needs stakeholder input:**

1. What triggers a Legal review, and how is one requested?
2. What triggers a Product Security review?
3. Is either blocking, or advisory with the maintainer still owning the merge?

## SLA

Business days from PR open. Targets, not guarantees — capacity is best-effort, see the [disclaimer](./README.md#disclaimer).

| Stage | Target |
| --- | --- |
| First response / triage | 2 |
| Full review, straightforward skill | 5 |
| Full review, escalated | 10 |
| Merge after feedback addressed | 2 |

Past the triage target, comment and tag a maintainer. That's a legitimate nudge.

Stale PRs close after two weeks with no contributor response, and can be reopened.

## Publishing and removal

Merged skills may also appear on Snowflake developer properties. Assume a merged skill is public and attributable to you.

Skills may be removed without notice if they break, mislead, violate the guidelines, or duplicate a bundled skill. Where an owner is reachable we open an issue first.
