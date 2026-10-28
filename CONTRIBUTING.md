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

### Required frontmatter fields

The structure check requires only `name` and `description`, both nonblank YAML strings. Prefer a unique, lowercase, hyphenated `name` matching the folder; naming style and trigger wording remain recommendations, not structural failures. See the [Cortex Code extensibility docs](https://docs.snowflake.com/en/user-guide/cortex-code/extensibility#label-extensibility-skills) for runtime behavior.

| Field | Description |
|---|---|
| `name` | Skill name used for explicit invocation. |
| `description` | Explain what the skill does and when to use it, including trigger keywords. |

### Optional frontmatter fields

These fields are recommended where useful, not required. When present, their types are checked:

| Field | Accepted type and recommendation |
|---|---|
| `id` | String matching the directory exactly: lowercase letters/numbers separated by single hyphens, with no leading/trailing hyphen. |
| `title`, `summary` | Strings; recommended limits of 30 and 140 characters respectively. |
| `prompt`, `author` | Strings: an example prompt and author attribution. |
| `language` | String; suggested values: `en`, `es`, `it`, `fr`, `ja`, `ko`, `pt_br`. |
| `type` | String; suggested values: `community`, `snowflake`, `partner`. No employment inference from names or emails. |
| `status` | String; both `stable`/`beta`/`draft` and `Published`/`Archived`/`Hidden` are recognized. |
| `tools` | List of strings. See the [tools reference](https://docs.snowflake.com/en/user-guide/cortex-code/tools). |
| `authors`, `categories`, `tags`, `allowed-tools` | String or list of strings, supporting legacy and newer metadata. |

Other string values for language/type/status and longer title/summary values produce advisories, not structural failures. Unknown fields are allowed subject to the YAML safety limits below.

### Advisory structure check

The new `skill-structure.yml` checks the entire head tree of each affected retained `skills/<directory>/`, including when only a supporting file changed. Each must contain regular, nonempty UTF-8 `SKILL.md` and `LICENSE` files. Frontmatter must be a YAML mapping with no duplicate keys, aliases, or custom tags. Symlinks and submodules are rejected; executable-mode regular blobs and binary assets are allowed as data, never executed. Entire skill deletion is reported, while deleting a required file from a retained skill fails.

Plugins under `plugins/<plugin>/` are covered too. Any change inside a retained plugin checks that its folder is a lowercase hyphenated slug, that it has a nonempty root `LICENSE`, and that `.cortex-plugin/plugin.json` is a JSON object with nonblank string `name`, `version`, and `description`, where `name` matches the folder. Changed skills in `plugins/<plugin>/skills/<directory>/` get the same full skill check, except that the plugin's root `LICENSE` covers them, so a skill-level `LICENSE` is optional. Changing the plugin `LICENSE` rechecks all of that plugin's skills. The MCP configuration (a root `.mcp.json`, or the file or object named by the manifest's `mcpServers`) must be a JSON object with an `mcpServers` object whose entries each have a string `url` or `command`. Hooks, agents, and other plugin files are not read.

The check also emits advisory findings, which never change the result on their own:

- Missing recommended metadata (`id`, `type`, `status`, `categories`, and `author` or `authors`), and descriptions that don't start with "Use when", lack "Triggers:", or use first-person wording.
- A skill folder or `name` that another skill in `skills/` or `plugins/*/skills/` already uses.
- A `LICENSE` that matches none of the repository's pinned license texts, or matches only a legacy notice. This compares text only and is not legal approval.
- Local links in `SKILL.md` whose target doesn't exist, whose `#heading` anchor isn't in the file, or that escape the bundle. A top-level skill must link within its own folder; a plugin skill may link anywhere inside its plugin. Target files are not read.
- MCP servers that run a local `command` or use a non-HTTPS `url`.
- A plugin that isn't linked from the root `README.md` Plugins table.

Secret-like values (private keys, AWS and GitHub tokens, and `password`/`api_key`/`secret_key`/`access_token` assignments) in `SKILL.md`, `LICENSE`, or MCP configuration fail the check. The matched value is never printed.

Limits are 20 affected skills, 20 affected plugins, 500 entries per skill, depth 12, 64 KiB for `SKILL.md`, `plugin.json`, and MCP configuration, 256 KiB for `LICENSE` and the root `README.md`, 200 local links per `SKILL.md`, plus bounded parsing, diagnostics, and Git operations. Malformed paths, exceeded limits, or unavailable revisions fail as incomplete; ask a maintainer to handle larger submissions. Exit codes: 0 valid/no relevant changes, 1 structural violations, 2 incomplete inspection. Passing checks structure and a fixed set of secret patterns, not execution safety, semantic quality, complete absence of secrets, or legal approval. `LICENSE` presence is not proof of an appropriate or valid license; human review remains required.

The read-only job sparse-checks out only the checker and dependency lock from the maintainer-reviewed PR **base SHA**, then installs the hash-locked PyYAML wheel from PyPI. It reads candidate Git objects without checking out, importing, installing, or running candidate files. It uses Python 3.12 on an x64 Ubuntu runner, no shared cache, and no custom token or repository secrets. It posts no comments or artifacts.

**Bootstrap and checker changes:** if the base lacks the checker or lock, the job fails clearly, never falls back to candidate code, and never skips to green. Maintainers must review and run the proposed tests locally from a reviewed checkout with Python 3.12 and PyYAML 6.0.3 already available:

```sh
python3 -I -B -m unittest discover -s .github/tests -p test_skill_structure.py -v
```

This includes workflow source-contract tests; the PR job does not run candidate tests. Until checker/lock changes merge into the trusted base, the admission result uses the old checker (or fails bootstrap), not the proposed implementation. The wheel lock is Linux x64-specific, not a portable local development lock.

**Advisory, not an authoritative gate:** an ordinary `pull_request` can modify its workflow, so this configuration does not prevent an arbitrarily edited workflow from executing candidate code or forging a result. Authoritative enforcement requires an independently enforced source workflow through an external organization/enterprise ruleset; that setup and its availability have not been verified. A required status name or CODEOWNERS alone is not equivalent. This workflow covers normal PRs, not merge queues. The existing `validate-skill.yml` and manual skill reviewer remain unchanged, parallel advisory signals with their own legacy behavior; neither is reused by this checker.


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

Before opening a pull request, run `$review-skill-sflabs` against your skill directory. The skill is itself published in this repo and provides a pre-PR readiness check: format validation, public-web duplicate search, data-policy reasoning, and catalog-fit check (overlap with bundled skills, scope flexibility). It applies safe mechanical fixes in place (with your confirmation) and flags content rewrites for you to address manually.

Install and run it in a Cortex Code session:

```
/skill add https://github.com/Snowflake-Labs/cortex-code-skills.git/skills/review-skill-sflabs
$review-skill-sflabs <path-to-your-skill>
```

The output is a verdict (🟢 promote / 🟡 adapt / 🟠 skip) plus a concrete checklist of issues. Address any 🟠 *Needs Attention* findings before opening the PR.

Running it does not gate PR acceptance — it is advisory. Final review is human-in-the-loop and reviewers may decline or request changes regardless of what the skill reports.

---

## Submit a skill from Cortex Code CLI

There is no `cortex skill export` command. A skill is just a directory with a `SKILL.md` file, so the process is to locate it, copy it into this repo, and fill in the required frontmatter fields.

**If you built your skill using `$skill-development` or `/skill`:**

Skills created interactively are saved to `~/.snowflake/cortex/skills/<skill-name>/`. Find your skill there and copy the directory into your fork.

**Steps:**

1. [Fork this repo](https://github.com/Snowflake-Labs/cortex-code-skills/fork) and clone your fork locally
2. Copy an existing skill folder from `skills/` (see "Starting point" above) into a new folder named your skill's `name` (lowercase, hyphens)
3. Open `SKILL.md` and fill in all required frontmatter fields, then add a `LICENSE` file (Apache 2.0 for community contributors)
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
