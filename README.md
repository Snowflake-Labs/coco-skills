# Cortex Code Skills

Welcome to the community skills repo for [Cortex Code](https://www.snowflake.com/en/product/features/cortex-code/) ("CoCo"). CoCo already ships with 50+ [bundled skills](https://docs.snowflake.com/en/user-guide/cortex-code/bundled-skills) (type `/skill` to browse them).

Skills here are contributed and maintained by the broader developer community, including Data Superheroes and Snowflake employees. Every skill published here is a workflow someone already figured out, packaged so anyone can load it in seconds. 

Licenses are assigned at the skill folder level: Apache 2.0 for community contributors, Snowflake license for employees.

- [How to contribute a skill](CONTRIBUTING.md)
- [Using skills in CoCo](#using-skills)
- [Repo structure](#repo-structure)
- [Disclaimer](#disclaimer)

## What are skills?

Skills are directories containing a `SKILL.md` file that injects domain-specific knowledge and instructions into a Cortex Code conversation, teaching it your workflows, coding standards, or best practices for the duration of a session.

---

## Using skills

### Add this repo

You can add the entire repo (all skills at once) or a specific skill:

**All skills:**
```
/skill add https://github.com/Snowflake-Labs/cortex-code-skills.git
```

**A specific skill:**
```
/skill add https://github.com/Snowflake-Labs/cortex-code-skills.git/skills/<skill-name>
```

Remote skills are cached locally. To pull the latest updates, run `/skill sync`. See the [skill management docs](https://docs.snowflake.com/en/user-guide/cortex-code/extensibility#label-extensibility-skills) for more options.

### Invoke a skill

```
$skill-name your prompt here
```

Each skill's `SKILL.md` includes example prompts. Run `/skill list` to see what's available, or `$$` to verify what's loaded in the current session.

### Resolution order

Cortex Code uses first-match-wins:

| Priority | Level | Path |
|---|---|---|
| 1 | Project | `.cortex/skills/`, `.claude/skills/`, or `.snova/skills/` |
| 2 | Global | `~/.snowflake/cortex/skills/` |
| 3 | Git-sourced | Cached from `/skill add` |
| 4 | Bundled | Ships with Cortex Code |

A skill from this repo will silently shadow a bundled skill with the same name. Use `/skill` to check for collisions before submitting.

---

## Repo structure

```
skills/
  _template-skill-name/   # starter template: copy this and rename the folder to your skill name
    SKILL.md              # Required: skill instructions and frontmatter
    LICENSE               # Required: Apache 2.0 (community) or Snowflake license (employee)
  my-skill-name/          # real skill example
    SKILL.md
    LICENSE
    references/           # Optional: supporting reference material
```

---

## Disclaimer

**Skills are provided for community use only.** Snowflake reviews PRs before merging but cannot guarantee quality, accuracy, or security. Review each skill's `SKILL.md` and `LICENSE` before loading it, especially skills that execute SQL, modify Snowflake objects, or interact with external services.

To report a concern, open an issue or email [devrel@snowflake.com](mailto:devrel@snowflake.com). Snowflake reserves the right to remove skills that violate our [contribution guidelines](CONTRIBUTING.md) or [terms of use](https://www.snowflake.com/legal/).
