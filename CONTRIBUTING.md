# Contributing to Cortex Code Skills

We are thrilled you want to contribute! The Snowflake community is full of people who have figured out clever ways to work with data, build pipelines, and deploy AI applications. This repo is the place to share that expertise so others can load it in seconds. Whether you are packaging a workflow you reach for every day or building something entirely new, this guide covers everything you need to format and submit a skill.

- [Before you start](#before-you-start)
- [Skill format](#skill-format)
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

For full details on skill format, see the [Cortex Code extensibility docs](https://docs.snowflake.com/en/user-guide/cortex-code/extensibility#label-extensibility-skills).

| Field | Description |
|---|---|
| `name` | Unique identifier matching the skill's folder name. Max three words, lowercase, hyphens only. Users can invoke explicitly with `$name`, or Cortex Code will activate the skill automatically when a prompt matches the `description`. |
| `title` | Human-readable display name, similar to `name` without hyphens. Max 30 characters. (e.g., `cost-intelligence` → "Analyze Snowflake Costs") |
| `summary` | One sentence explaining what this skill does. Max 140 characters. Displayed in the skill catalog and search results. |
| `description` | Longer explanation including when to use, trigger keywords, and anti-patterns. Cortex Code matches user prompts against this field to automatically activate your skill. |
| `tools` | List of tools to enable when the skill is active (one per line, each prefixed with `- `). See the [full tools reference](https://docs.snowflake.com/en/user-guide/cortex-code/tools). Common tools: `snowflake_sql_execute`, `snowflake_object_search`, `Bash`, `Read`, `Write`, `Edit`, `Grep`, `Glob`. |
| `prompt` | A sample user prompt that demonstrates how to trigger the skill. |
| `language` | Language code: `en`, `es`, `it`, `fr`, `ja`, `ko`, or `pt_br`. |
| `status` | `Published` (active), `Archived` (deprecated), or `Hidden` (not yet ready). |
| `author` | Full name of the author(s). Multiple authors are comma-separated. |
| `type` | `community` (default), `snowflake` (Snowflake employee), or `partner`. |


### SKILL.md template

Use the `skills/_template-skill-name/` directory as your starting point. Copy it and rename the folder to your skill's `name`.

### Recommendations

- **Naming:** We recommend verb-noun format for skill names: `deploy-agent`, `analyze-pipeline`, `build-dashboard`, `validate-model`.
- **Tools:** If your skill uses scripts or external commands, document them in your `SKILL.md` so Cortex Code knows how to invoke them. See the template for the recommended format.
- **Scripts:** If your skill includes Python scripts, place them in a `scripts/` directory and add a `pyproject.toml` for dependency management. We recommend using `uv` to run scripts. See the template for guidance on when scripts make sense vs. keeping logic in markdown.
- **Be specific:** Clear instructions produce better results than vague guidance.
- **Provide examples:** Show expected inputs and outputs in the `# Examples` section.
- **Include edge cases:** Handle common errors and exceptions.
- **Keep focused:** One skill should cover one domain or capability.

---

## Submit a skill from Cortex Code CLI

There is no `cortex skill export` command. A skill is just a directory with a `SKILL.md` file, so the process is to locate it, copy it into this repo, and fill in the required frontmatter fields.

**If you built your skill using `$skill-development` or `/skill`:**

Skills created interactively are saved to `~/.snowflake/cortex/skills/<skill-name>/`. Find your skill there and copy the directory into your fork.

**Steps:**

1. [Fork this repo](https://github.com/Snowflake-Labs/cortex-code-skills/fork) and clone your fork locally
2. Copy `skills/_template-skill-name/` into `skills/` as a new folder named your skill's `name` (lowercase, hyphens)
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
