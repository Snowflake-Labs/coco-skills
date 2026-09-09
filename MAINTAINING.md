# Maintaining this repo

How skills get reviewed, merged, and published. Read this alongside [CONTRIBUTING.md](./CONTRIBUTING.md) if you're submitting a skill, or if you're on the reviewer list.

Contributors come from three places: Snowflake employees, Snowflake partners, and the wider community. The review process is the same for all three; licensing differs, and employee submissions clear an extra step before they arrive.

## Reviewers

Skills are approved by a small, named set of maintainers rather than an open rotation. This is deliberate — skills execute against a user's Snowflake account, and a consistent bar matters more than throughput.

Current maintainers:

- Celeste Horgan (DevRel) — owner
- Luke Hartzell (DevRel)

Ownership is enforced through [CODEOWNERS](.github/CODEOWNERS), not convention. Paths that run with repository credentials require a security owner's approval in addition to a maintainer's.

**TODO — needs stakeholder input.** This section must answer:

1. Who is the third reviewer on the field/FDE side, and are they a full approver or a domain consult?
2. What's the coverage plan when a maintainer is out — does a PR wait, or is there a named backup?
3. Two maintainers is a bottleneck at current PR volume. What's the trigger for adding more, and who decides?
4. Who is the security owner for `.github/` and `scripts/`? Until that's filled in, those paths fall back to maintainer-only review, which is the gap CODEOWNERS exists to close.

## Scope

**TODO — needs stakeholder input.** The name of this repo is narrower than what people are submitting. This section must answer:

1. Does this repo accept skills for other Snowflake agent surfaces, or CoCo only? Submissions already arrive for adjacent products.
2. Do we accept general-purpose agent skills with no Snowflake dependency, or is a Snowflake tie-in required for merge?
3. Do plugins stay a separate contribution path (`plugins/`) or fold into the same review flow as skills?

Until this is settled, maintainers should merge anything that works in CoCo and flag out-of-scope submissions on the PR rather than closing them.

## Incubation

Snowflake-authored skills are developed in an internal repository and clear a disclosure gate before being promoted here. See [the promotion gate spec](.github/internal-promotion-gate.md).

The gate is the reason the internal stage exists. Opening a public PR *is* publication — the diff is world-readable and forkable the moment it opens, and deleting a commit does not unpublish it. So checking for internal content on a public PR runs too late by construction. Every other quality concern (structure, licensing, trigger hygiene, whether the skill works) is caught as well or better by public review, where more people see it. This one cannot be.

Two consequences worth stating:

- If a skill arrives here without a disclosure attestation, that is a process failure, not a paperwork gap. Public maintainers do not repeat the review — they rely on it.
- If incubation becomes a waiting period with no disclosure gate attached, it isn't buying anything public review wouldn't have caught, and should be dropped.

Community and partner skills do not incubate. They are reviewed and merged here.

**TODO — needs stakeholder input.** This section must answer:

1. Who owns the internal gate? It's load-bearing — nothing downstream catches internal content, by design.
2. Is the gate enforced in CI internally, or a documented manual step? If manual, who verifies the attestation is present before merge?
3. Is there a minimum bake period beyond "the gate has run", and what does it buy?

## What automation checks

Reviewers should know what a green checkmark does and does not cover. Do not re-check the left column by hand; do read for the right one.

| Machine-checked (blocking) | Human-only |
| --- | --- |
| `SKILL.md` present, frontmatter is valid YAML | Whether `scripts/` code is genuinely *safe* — CI sees that a call is made, not whether it's reasonable |
| Required and recommended frontmatter fields | Whether destructive operations are actually gated on user confirmation |
| Folder name matches `name` | Trigger hygiene and scoping |
| `type` and `status` values valid | Whether the skill works against a real account |
| Licence present and matching the declared `type` | Overlap judgement when two skills compete for the same triggers |
| Name collisions with repo or bundled skills | Positioning and product claims |
| Internal-reference patterns (see below) | Semantic internal content — handled upstream for employee skills |

Implemented in [validate_skill.py](.github/scripts/validate_skill.py) and [scan_internal_refs.py](.github/scripts/scan_internal_refs.py), with fixtures under [.github/scripts/tests/](.github/scripts/tests/). Both run read-only, with no secrets and no execution of contributor code.

### Internal-reference scan

Blocks on structural markers that won't resolve for an external reader: `go/` links, Google Workspace and Slack links, internal ticket keys, non-public hostnames, confidentiality banners, and `sfc-gh-` handles in prose. It deliberately contains no product codenames — this repo is public, so a deny-list of codenames would itself disclose them. Semantic review lives in the [disclosure rubric](.github/disclosure-rubric.md).

Pre-existing findings are recorded in a baseline so the check could be turned on without blocking every PR behind a cleanup. New findings fail; baselined ones are reported as debt. False positives are suppressed with an `internal-ref-ok` marker on the line or the line above.

### Escalation labels

[route-review.yml](.github/workflows/route-review.yml) reads the diff and applies labels — `needs: sql-write-review`, `needs: security-review`, `needs: code-review`, `needs: egress-review`, `needs: product-confirmation`, `needs: positioning-review`. These prompt a human; they do not gate. CODEOWNERS gates. Keeping it that way matters: a keyword labeler that blocks merge produces false positives and gets routed around within a month.

## Evaluation criteria

A "no" on anything **Blocking** means the PR does not merge until it's resolved. Items marked (CI) are enforced automatically.

### Blocking

- **Structure.** (CI) `SKILL.md` and `LICENSE` present. Frontmatter has `name`, `title`, `summary`, `description`, `type`, `author`. Folder name matches `name`.
- **License.** (CI) Apache 2.0 for community and partner contributions; Snowflake license for employee contributions, cross-checked against `type` and the author's email domain.
- **No secrets or customer data.** Credentials are covered org-wide by ArcticOwl. Internal link and reference shapes are (CI). Customer identification is a human read.
- **Scripts are readable and safe.** Anything in `scripts/` is reviewed line by line. No piping remote content to a shell, no writes outside the working directory. Dependencies named and justified.
- **Destructive operations are gated.** Any skill that drops, replaces, or grants must instruct the agent to confirm with the user before executing. `CREATE OR REPLACE` against user-named objects counts.
- **Trigger hygiene.** `description` lists concrete user phrases and, where an adjacent skill exists, explicit "Do NOT use for…" cases. Overly broad triggers hijack unrelated sessions and are the most common reason a PR gets held.
- **No name collision.** (CI) Doesn't shadow a bundled skill or an existing skill here.
- **Tested.** Contributor attests they ran it against representative tasks. Reviewer spot-checks at least one path.

### Non-blocking, request changes

- `SKILL.md` under ~500 words, with reference material moved to `references/`. (CI warns)
- Workflow is numbered, ordered, and has a Common Mistakes section.
- Correct Snowflake SQL idioms — fully qualified names where it matters, no assumptions about the active role or warehouse.
- Catalog entry added to the README table.

## Escalation

A maintainer requests an additional approval before merging when the PR:

- **Executes SQL that writes.** Any DDL or DML, grants, role changes, or policy changes — route to a reviewer who owns that domain.
- **Touches security or governance.** RBAC, masking, row access, network policies, authentication, secrets handling.
- **Ships executable code with dependencies.** Anything in `scripts/` that installs packages or calls external services.
- **Adds or changes CI.** Workflow files run with repository credentials. Treat every change under `.github/` as security-sensitive and review it as carefully as you would a `scripts/` change.
- **Covers a preview or unreleased feature.** Needs the product owner's confirmation that it's public-safe.
- **Comes from a partner and names their product.** Needs a second reader on positioning claims, not just technical content.
- **Reads or transmits data outside Snowflake.** Any egress at all.
- **Overlaps an existing skill.** Two skills competing for the same triggers degrade both. Decide whether to merge, scope down, or reject.

When in doubt, ask for the second approval. It's cheap.

**TODO — needs stakeholder input.** This section must answer:

1. What triggers a Legal review, and how does a maintainer request one? Partner-authored skills and third-party product claims are the likely cases.
2. What triggers a Product Security review? Automation that runs with repo credentials, and skills bundling executable code, are the likely cases.
3. Is either review blocking, or advisory with the maintainer still owning the merge decision?

## SLA

Business days, from PR open.

| Stage | Target |
| --- | --- |
| First response / triage | 2 business days |
| Full review on a straightforward skill | 5 business days |
| Full review when escalated for a second approval | 10 business days |
| Merge after contributor addresses all feedback | 2 business days |

If a PR has had no maintainer response past the triage target, comment on the thread and tag a maintainer. That's a legitimate nudge, not a nuisance.

Stale PRs are closed after two weeks with no contributor response, and can be reopened.

These are targets, not guarantees. Review capacity is best-effort — see the [disclaimer](./README.md#disclaimer).

## Publishing beyond this repo

Merged skills may also be surfaced on Snowflake developer properties outside GitHub. Contributors should assume a merged skill is public and attributable to them.

Any automated sync between this repo and an external destination runs with repository credentials, and is treated as security-sensitive infrastructure: it is reviewed under the escalation rules above and may be disabled at any time without affecting the repo itself.

## Removal

Skills may be removed without notice if they break, become misleading, violate the contribution guidelines, or duplicate a bundled skill. Where an owner is reachable, we open an issue first and give them a chance to fix it.
