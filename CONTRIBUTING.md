# Contributing to Cortex Code Skills

We are thrilled you want to contribute! The Snowflake community is full of people who have figured out clever ways to work with data, build pipelines, and deploy AI applications. This repo is the place to share that expertise so others can load it in seconds. Whether you are packaging a workflow you reach for every day or building something entirely new, this guide covers everything you need to format and submit a skill.

- [Before you start](#before-you-start)
- [Skill format](#skill-format)
- [Submit a skill from Cortex Code CLI](#submit-a-skill-from-cortex-code-cli)
- [Review criteria](#review-criteria)

---

## Before you start

The fastest way to build a skill is to use the bundled `$skill-development` skill inside a Cortex Code session:

| What you want to do | Prompt |
|---|---|
| Build a new skill from scratch | `$skill-development create a new skill for [your use case]` |
| Turn a workflow you already ran into a skill | `$skill-development summarize this session into a skill` |
| Improve or audit an existing skill | `$skill-development audit skill @path/to/SKILL.md` |

You can also create a skill interactively: open a Cortex Code session, run `/skill`, and press `a`.

---

## Skill format

Skills in this repo follow the [standard SKILL.md format](https://docs.snowflake.com/en/user-guide/cortex-code/extensibility#label-extensibility-skills) defined by Cortex Code, with additional frontmatter fields required for catalog metadata.

### Required frontmatter fields

| Field | Description |
|---|---|
| `id` | Unique identifier matching the skill's folder name. Lowercase, hyphens only (no underscores). |
| `name` | Display name used by Cortex Code. Must match `id`. |
| `skill-name` | Invocation shorthand prefixed with `$`. Max two words, hyphenated (e.g., `$cortex-agent`). |
| `description` | One sentence explaining what this skill does and when to use it. Shown in `/skill list`. |
| `prompt` | A sample user prompt that demonstrates how to trigger the skill. |
| `language` | Language code: `en`, `es`, `it`, `fr`, `ja`, `ko`, or `pt_br`. |
| `categories` | One or more taxonomy paths from the [approved tag list](https://www.snowflake.com/en/developers/guides/get-started-with-guides/#language-and-category-tags), comma-separated. |
| `status` | `Published` (active), `Archived` (deprecated), or `Hidden` (not yet ready). |
| `authors` | Full name of the author(s). Multiple authors are comma-separated. |
| `type` | Defaults to `community` for skills submitted to this repo from non-Snowflake employees. For Snowflake employee contributions, this is set to `snowflake`. |

The `tools` field is optional. Use it to declare which Cortex Code tools the skill is designed to activate (e.g., `snowflake_sql_execute`). Declaring tool dependencies helps users understand what the skill will do before they load it.

### SKILL.md template

Copy the `template/` directory at the root of this repo as your starting point. It includes a pre-filled `SKILL.md` and an Apache 2.0 `LICENSE` file.

```markdown
---
id: my-skill-name
name: my-skill-name
skill-name: $my-skill
description: One sentence explaining what this skill does and when to use it.
prompt: "$my-skill do something specific with my data"
language: en
categories: snowflake-site:taxonomy/product/ai, snowflake-site:taxonomy/snowflake-feature/build
status: Published
authors: First Last
type: community        # community (default) or snowflake (Snowflake employees)
tools:
  - snowflake_sql_execute    # optional: declare tools this skill is designed to use
---

# When to Use
- List the user intents or scenarios where this skill should be invoked

# What This Skill Provides
Describe the capabilities and domain knowledge this skill adds to Cortex Code.

# Instructions
Step-by-step guidance for Cortex Code when this skill is active.

## Best Practices
- Best practice 1
- Best practice 2

## Common Patterns

### Pattern 1
Description and example.

### Pattern 2
Description and example.

# Examples

## Example 1: Basic usage
User: $my-skill Do something
Assistant: [Expected behavior]

## Example 2: Advanced usage
User: $my-skill Complex task with @file.sql
Assistant: [Expected behavior]
```

### Best practices

- **Be specific:** Clear instructions produce better results than vague guidance
- **Provide examples:** Show expected inputs and outputs in the `# Examples` section
- **Include edge cases:** Handle common errors and exceptions
- **Keep focused:** One skill should cover one domain or capability

---

## Submit a skill from Cortex Code CLI

There is no `cortex skill export` command. A skill is just a directory with a `SKILL.md` file, so the process is to locate it, copy it into this repo, and fill in the required frontmatter fields.

**If you built your skill using `$skill-development` or `/skill`:**

Skills created interactively are saved to `~/.snowflake/cortex/skills/<skill-name>/`. Find your skill there and copy the directory into your fork.

**Steps:**

1. [Fork this repo](https://github.com/Snowflake-Labs/cortex-code-skills/fork) and clone your fork locally
2. Copy your skill directory (or the `template/` directory) into `skills/` and rename it to your skill's `id` (lowercase, hyphens)
3. Open `SKILL.md` and fill in all required frontmatter fields, then add a `LICENSE` file (Apache 2.0 for community contributors)
4. Confirm your skill name is not already used by a bundled skill: run `/skill` in a Cortex Code session to see all skills grouped by location, or inspect `~/.local/share/cortex/<version>/bundled_skills/` directly
5. Test your skill against your example prompt and confirm the behavior matches what you described
6. Submit your pull request — you can do this entirely from inside a Cortex Code session:

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
