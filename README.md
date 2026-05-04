# Cortex Code Skills

This is the public repo for [Cortex Code](https://docs.snowflake.com/en/user-guide/cortex-code/extensibility#label-extensibility-skills) skills: reusable, shareable instruction sets that extend what Cortex Code knows how to do. Skills here are contributed by Snowflake employees and the broader developer community, including Data Superheroes and partners. They complement the bundled skills that ship with the Cortex Code CLI. All skills follow the open source [SKILL.md standard](https://docs.snowflake.com/en/user-guide/cortex-code/extensibility#label-extensibility-skills).

Cortex Code gets more useful the more skills exist for it. Every skill published here is a workflow someone already figured out, packaged so anyone can load it in seconds. The most valuable contributions come from people solving real problems: data engineers building pipelines, analysts working with Cortex Analyst, developers deploying agents. If you have a workflow you reach for repeatedly, it belongs here. Skills in this repo are community-maintained: please [review them before use](#disclaimer).

Licenses are assigned at the skill folder level (Apache 2.0 for community contributors, Snowflake license for employees). See the [License](#license) section for details.

---

## What's in this README

- [What are skills?](#what-are-skills)
- [Using a skill in Cortex Code](#using-a-skill-in-cortex-code)
- [Repo structure](#repo-structure)
- [Contribute a skill](#contribute-a-skill)
- [Disclaimer](#disclaimer)
- [License](#license)

---

## What are skills?

Skills are directories containing a `SKILL.md` file that injects domain-specific knowledge and instructions into a Cortex Code conversation. When you invoke a skill, its instructions become part of the active context, teaching Cortex Code your organization's best practices, coding standards, or specialized workflows for the duration of that session.

Cortex Code supports two types of skills:

- **Bundled skills** ship with the Cortex Code CLI and are available immediately after installation. They cover core Snowflake workflows and are maintained by the Snowflake product team.
- **Public skills** live in this repo. They are contributed by Snowflake employees and the developer community and can be added to any Cortex Code session with a single command.

---

## Using a skill in Cortex Code

### Add this repo as a remote skill source

Inside a Cortex Code CLI session, run:

```
/skill add https://github.com/Snowflake-Labs/cortex-code-skills.git
```

Remote skills are cached locally. To pull the latest updates, run `/skill sync`.

Cortex Code resolves skills using a first-match-wins priority order:

| Priority | Level | Path | Scope |
|---|---|---|---|
| 1 (highest) | Project | `.cortex/skills/`, `.claude/skills/`, or `.snova/skills/` | Inside your current project folder; checked into git and shared with your team |
| 2 | Global | `~/.snowflake/cortex/skills/` | Personal to you; available across all projects on your machine |
| 3 | Git-sourced | Cached from Git repos | Skills added via `/skill add`, including this repo |
| 4 | Bundled | Ships with Cortex Code | Available in every session by default |

A skill from this repo will silently shadow any bundled skill that shares the same name. This is intentional behavior and can be used to override a bundled skill, but contributors should avoid reusing bundled skill names unintentionally. Use the `/skill` interactive manager in the CLI to see skills grouped by location and identify any name collisions before submitting.

### List available skills

```
/skill list
```

Or verify a specific skill loaded by running `$$` in the session.

### Invoke a skill

```
$skill-name your prompt here
```

For example:

```
$cortex-agent-builder Create a new Cortex Agent that answers questions about my sales data
```

Each skill's `SKILL.md` includes example prompts to show exactly how to invoke it.

### Manage skills from the command line

```bash
cortex skill list                  # List all available skills
cortex skill add <path>            # Add a skill by local path
cortex skill remove <path>         # Remove a skill
```

---

## Repo structure

Each skill lives in its own directory under `skills/`. The directory name must match the `id` field in `SKILL.md` (lowercase, hyphens, no underscores). The `_template-skill-name/` folder is a starter template — not a real skill.

```
skills/
  _template-skill-name/   # starter template — copy this, rename the folder to your skill id
    SKILL.md              # Required: skill instructions and frontmatter
    LICENSE               # Required: Apache 2.0 (community) or Snowflake license (employee)
  my-skill-name/          # example real skill
    SKILL.md
    LICENSE
    references/           # Optional: supporting reference material
```

---

## Contribute a skill

Skills follow the [standard SKILL.md format](https://docs.snowflake.com/en/user-guide/cortex-code/extensibility#label-extensibility-skills) defined by Cortex Code. A starter template is available at `skills/_template-skill-name/` — copy it, rename the folder to your skill's id, and fill in the `SKILL.md`.

The fastest way to build a skill is to use the bundled `$skill-development` skill directly in a Cortex Code session:

| What you want to do | Prompt |
|---|---|
| Build a new skill from scratch | `$skill-development create a new skill for [your use case]` |
| Turn a workflow you already ran into a skill | `$skill-development summarize this session into a skill` |
| Improve or audit an existing skill | `$skill-development audit skill @path/to/SKILL.md` |

For full formatting requirements, best practices, and step-by-step submission instructions, see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Disclaimer

**These skills are provided for demonstration and community use only.** While Snowflake reviews pull requests before merging, we cannot guarantee the quality, accuracy, or security of every skill in this repository. Skill behavior may vary depending on your Cortex Code version, account configuration, and data.

We do our best to maintain a safe ecosystem, but we encourage you to review skills before adding them to your session and use your own judgment.

Before using any skill:

- Check the `LICENSE` file in the skill's directory to understand the terms that apply
- Read the `SKILL.md` to understand what the skill does and which tools it enables
- Be cautious with skills that execute SQL, modify Snowflake objects, or interact with external services
- Check the author's GitHub profile and the skill's commit history if you have questions about its origin

To report a concern, open an issue in this repo or email [devrel@snowflake.com](mailto:devrel@snowflake.com). Snowflake reserves the right to remove skills that violate our [contribution guidelines](CONTRIBUTING.md) or [terms of use](https://www.snowflake.com/legal/).

---

## License

Licenses are assigned at the skill folder level, not at the repository level. Each skill directory includes its own `LICENSE` file.

- **Community-contributed skills** (submitted by developers outside Snowflake) are licensed under the [Apache 2.0 License](https://www.apache.org/licenses/LICENSE-2.0).
- **Snowflake employee-contributed skills** are licensed under the [Snowflake License](https://www.snowflake.com/legal/).

Check the `LICENSE` file in each skill's directory before use.
