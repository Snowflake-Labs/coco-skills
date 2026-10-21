# Contributing to Cortex Code Skills

We are thrilled you want to contribute! The Snowflake community is full of people who have figured out clever ways to work with data, build pipelines, and deploy AI applications. This repo is the place to share that expertise so others can load it in seconds. Whether you are packaging a workflow you reach for every day or building something entirely new, this guide covers everything you need to format and submit a skill.

- [Before you start](#before-you-start)
- [Skill format](#skill-format)
- [Review your skill before submitting](#review-your-skill-before-submitting)
- [Submit a skill from Cortex Code CLI](#submit-a-skill-from-cortex-code-cli)
- [Review criteria](#review-criteria)

---

## Before you start

> ❄️ **Snowflake employees:** Before contributing here, follow the internal instructions at [go/skills](https://go/skills) first.

The fastest way to build a skill is to use the bundled `$skill-development` skill inside a Cortex Code session:

| What you want to do | Prompt |
|---|---|
| Build a new skill from scratch | `$skill-development create a new skill for [your use case]` |
| Turn a workflow you already ran into a skill | `$skill-development summarize this session into a skill` |
| Improve or audit an existing skill | `$skill-development audit skill @path/to/SKILL.md` |

You can also create a skill interactively: open a Cortex Code session, run `/skill`, and press `a`.

---

## Skill format

Skills in this repo follow the [standard SKILL.md format](https://docs.snowflake.com/en/user-guide/cortex-code/extensibility#label-extensibility-skills) defined by Cortex Code.

### Frontmatter contract

The versioned reviewer policy in [scripts/policy.py](skills/review-skill-sflabs/scripts/policy.py)
is authoritative for mechanical checks (rules 1.2; metadata/license decisions unchanged from 1.1). The table below is checked
against that matrix by unit tests. It does not claim to define every Cortex Code
client's metadata behavior. All listed fields reject unsupported YAML types;
unknown fields are retained without validation. Duplicate keys and aliases fail.

<!-- reviewer-policy-table:start -->
| Field | Reviewer contract |
|---|---|
| `name` | Required nonblank string; directory match and 1-4 lowercase alphanumeric words separated by hyphens are advisory. |
| `description` | Required nonblank string; Use when prefix, Triggers: guidance and third-person wording are advisory. |
| `id` | Optional; recommended. If present, must match the directory and [a-z0-9-]+ (error otherwise). |
| `author` | Optional string, comma-separated names supported. Either author or authors satisfies the authorship recommendation; differing aliases require human review. |
| `authors` | Optional string or list of strings. Same authorship and license-claim rules as author; neither spelling proves identity. |
| `type` | Optional; recommended. Nonempty values: community or snowflake only (case-sensitive). Partner and all other values are unsupported errors pending maintainer approval; empty string warns, no default is inserted. |
| `status` | Optional; recommended values: stable, beta, draft (case-sensitive). Legacy Published, Archived, Hidden and any other nonempty string warn, never fail on value alone; empty string warns. |
| `categories` | Optional string or list of strings; missing or empty is advisory. No tag enum is enforced. |
| `title` | Optional string; more than 30 characters is advisory, not a required catalog field. |
| `summary` | Optional string; more than 140 characters is advisory, not a required catalog field. |
| `tools` | Optional string or list of strings; no tool-name enum is enforced. This field grants no execution authority. |
| `prompt` | Optional sample prompt string. |
| `language` | Optional string; no language enum is enforced. en, es, it, fr, ja, ko and pt_br are examples, not an allowlist. |
<!-- reviewer-policy-table:end -->

`partner` is not accepted: earlier guidance contradicted the existing validator.
Do not migrate legacy status strings merely to satisfy an advisory check.
An `author` list remains an unsupported type; use the established `authors` list
form instead. Blank/absent author claims produce a recommendation, not a failure.
When both aliases are present, comma-separated, trimmed, case-insensitive sets
are compared; conflicting claims warn and neither alias overrides the other.

### License content and human approval

A root `LICENSE` must match an entire pinned repository source template, not
just contain a license name. See the [policy reference](skills/review-skill-sflabs/workflows/format-check.md)
for exact source paths, normalization and limitations. The public repository
LICENSE files also supply the full test fixtures; tests do not substitute headings.

Community guidance remains Apache 2.0; employee guidance remains the Snowflake
Skills License. The existing mechanical employee-email rule applies equally to
`author` and `authors`: a case-insensitive `@snowflake.com` marker in either
requires Snowflake license content, even if the aliases conflict. It is a claim,
not authenticated employment. A name alone or `type: snowflake` does not add a
new licensing failure; ambiguous or contradictory affiliation is for human review.
Every result includes a license-applicability advisory. A complete legacy Apache
notice match also warns that it is not the full terms.

Matching verifies content against local evidence, not legal correctness, identity,
attribution, the applicable agreement, or permission to distribute. External
maintainer/legal approval of templates and permitted substitutions is outstanding.
Do not copy a source author's copyright/name to make a new skill pass. Changed
attributions, new templates, and other unmatched content need maintainer review;
this implementation does not authorize automatic licensing edits.


### Starting point

Pick any existing skill in `skills/` whose shape matches what you're building, copy the folder, and rename it to your skill's `name`. Good starting points:

- A router skill that delegates to sub-flows: `skills/rbac/`
- A linear multi-step pipeline: `skills/dcr-v1-to-v2/`
- A docs/lookup skill: `skills/snowflake-docs/`

### Recommendations

- **Naming:** We recommend verb-noun format for skill names: `deploy-agent`, `analyze-pipeline`, `build-dashboard`, `validate-model`.
- **Tools:** If your skill uses scripts or external commands, document them in your `SKILL.md` so Cortex Code knows how to invoke them.
- **Scripts:** If your skill includes Python scripts, place them in a `scripts/` directory and add a `pyproject.toml` for dependency management. We recommend using `uv` to run scripts.
- **Be specific:** Clear instructions produce better results than vague guidance.
- **Provide examples:** Show expected inputs and outputs in the `# Examples` section.
- **Include edge cases:** Handle common errors and exceptions.
- **Keep focused:** One skill should cover one domain or capability.

---

## Review your skill before submitting

Before opening a pull request, run `$review-skill-sflabs` against your skill directory using a separately installed, reviewed copy of the reviewer. It runs deterministic checks in a restricted Docker container without executing candidate scripts or loading their instructions into the host agent. Docker must be running; there is no host-native fallback. The reviewer requires a trusted local image ID and does not automatically build an image from the candidate. See the reviewer's prerequisites for provisioning.

Installation and distribution of the contained reviewer are not approved yet.
Use only an already independently approved copy; do not install candidate tooling
or treat a repository URL as a release channel. The offline local snapshot recipe,
image-provisioning gates, and CI owner checklist are in the
[packaging reference](skills/review-skill-sflabs/workflows/format-check.md#dependencies-and-packaging).

The output separates mechanical validation (`passed`, `failed`, `incomplete`, `blocked`, or `unavailable`) from human editorial judgment. File IDs and line numbers avoid copying candidate text or secrets into agent context. Binary/unsupported files are reported as incomplete, not silently skipped. No automatic fixes, public-web searches, or live model calls are performed. Catalog matching uses a partial pinned 26-name public Labs snapshot and returns at most three hashed related-entry IDs with fixed human questions; it does not establish originality or usefulness. Bounded Markdown link/anchor/fence/routing checks are advisory and exclude code examples; they do not establish workflow termination. CI compares complete base/head bundles with the same trusted image. Missing, unsafe or incomplete baselines are not comparable, never proof of resolution; head failures remain failures.

Required mechanical checks follow the [frontmatter contract](#frontmatter-contract) and [license content rules](#license-content-and-human-approval). Catalog display and wording guidance is advisory. Secret detection is best effort, not a security certification. Acceptance still requires human review.

The CI integration is an inert template, not an enforced admission gate. Its thin Action uses independently approved tooling and a preloaded immutable image, separate from both PR base and head data. There are no automatic builds, pulls, installs or uploads. Bootstrap needs an independently reviewed launcher/image pair plus an external, separately managed authoritative required workflow; a historical base without the validator and a candidate's proposed tests cannot establish that trust. A PR can replace this workflow, its Action or claimed digest, so PR metadata and same-name status checks do not prove authority. Organization/ruleset IDs, enforcement bindings, owners, runner provisioning and distribution rights remain unknown or unapproved; the linked owner checklist records required evidence. This change does not configure or enable those controls.

Running it does not gate PR acceptance — it is advisory. Final review is human-in-the-loop and reviewers may decline or request changes regardless of what the skill reports.

---

## Submit a skill from Cortex Code CLI

There is no `cortex skill export` command. A skill is just a directory with a `SKILL.md` file, so the process is to locate it, copy it into this repo, and fill in the required frontmatter fields.

**If you built your skill using `$skill-development` or `/skill`:**

Skills created interactively are saved to `~/.snowflake/cortex/skills/<skill-name>/`. Find your skill there and copy the directory into your fork.

**Steps:**

1. [Fork this repo](https://github.com/Snowflake-Labs/cortex-code-skills/fork) and clone your fork locally
2. Copy an existing skill folder from `skills/` (see "Starting point" above) into a new folder named your skill's `name` (lowercase, hyphens)
3. Open `SKILL.md` and fill in `name` and `description`, then review the recommended fields and add a `LICENSE` under the content and human-approval rules above
4. Confirm your skill name is not already used by a bundled skill: run `/skill` in a Cortex Code session to see all skills grouped by location, or inspect `~/.local/share/cortex/<version>/bundled_skills/` directly
5. Test your skill against your example prompt and confirm the behavior matches what you described
6. Submit your pull request. You can do this entirely from inside a Cortex Code session:

   **Prerequisites:** Install and authenticate the [GitHub CLI](https://cli.github.com/) (`gh auth login`) if you haven't already. Cortex Code uses `gh` to interact with GitHub.

   Then open a Cortex Code session in your fork's directory and run:

   ```
   Create a pull request to Snowflake-Labs/cortex-code-skills with my new skill
   ```

   Cortex Code will:
   - Review your changes with `git status` and `git diff`
   - Push your branch to your fork
   - Open a PR with a structured title and description using `gh pr create`

   Or submit the PR manually via the GitHub web UI if you prefer.

---

## Review criteria

Pull requests are reviewed by the Cortex Code team. We look for:

- A clear, specific use case with a working example prompt
- Instructions that reliably produce the intended behavior
- No significant overlap with existing bundled or community skills
- No credentials, secrets, or proprietary data in the skill files
