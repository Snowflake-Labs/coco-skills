# Snowflake CoCo Skills

This is a curated collection of [Agent Skills](https://agentskills.io) for [Cortex Code](https://www.snowflake.com/en/product/features/cortex-code/) ("CoCo") — Snowflake's coding agent for building with AI faster.

Each skill is a folder containing a `SKILL.md` that teaches CoCo a workflow, coding standard, or domain-specific best practice. CoCo already ships with 50+ [bundled skills](https://docs.snowflake.com/en/user-guide/cortex-code/bundled-skills) (run `/skill` to browse). The skills here extend that catalog with workflows contributed by Data Superheroes, Snowflake employees, and partners.

- [Install these skills](#install-these-skills)
- [Skill catalog](#skill-catalog)
- [Authoring a skill](#authoring-a-skill)
- [Repo structure](#repo-structure)
- [Troubleshooting](#troubleshooting)
- [Disclaimer](#disclaimer)

---

## Install these skills

Open CoCo and ask:

```
Install the skills from https://github.com/Snowflake-Labs/cortex-code-skills
```

CoCo will clone, cache, and register every skill in this repo. To pull updates later:

```
Sync my skills
```

To install a single skill rather than the whole repo:

```
Install the skill at https://github.com/Snowflake-Labs/cortex-code-skills/tree/main/skills/<skill-name>
```

Once installed, invoke a skill by typing `$<skill-name>` followed by your prompt. Run `/skill list` to see what's loaded, or `$$` to verify the active session's skill set.

> **Snowflake connection required.** Most skills here run SQL or call Cortex services. Set your active connection with `cortex connections set <name>` before invoking.

---

## Skill catalog

### Snowflake docs & learning


| Skill                                         | What it does                                                                                                     |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| [`snowflake-docs`](skills/snowflake-docs)     | Answer any Snowflake question by searching official docs via the Cortex Knowledge Extension.                     |
| [`quickstart-guide`](skills/quickstart-guide) | Paste a [Snowflake Quickstart](https://quickstarts.snowflake.com) URL and get a guided, interactive walkthrough. |


### Analytics & semantic modeling


| Skill                                                     | What it does                                                                                                                                             |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`semantic-view-patterns`](skills/semantic-view-patterns) | Apply 25 production-tested Semantic View patterns covering joins, metrics, dimensions, and access policies.                                              |
| [`ontology-stack-builder`](skills/ontology-stack-builder) | Build a 5-layer Ontology-on-Snowflake stack (physical → metadata → abstract views → semantic views → Cortex Agent) from a relational schema or OWL file. |


### Data engineering & integration


| Skill                                                           | What it does                                                                                                 |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| [`snowpipe-bcdr`](skills/snowpipe-bcdr)                         | Snowpipe disaster-recovery patterns for Azure ADLS Gen2 — failover, failback, and catchup.                   |
| [`openflow-spcs-privatelink`](skills/openflow-spcs-privatelink) | Set up AWS PrivateLink between OpenFlow on SPCS and private sources like RDS or on-prem databases.           |
| [`manage-zerocopy-sapbdc`](skills/manage-zerocopy-sapbdc)       | Manage the SAP Business Data Cloud zero-copy connector lifecycle: create, enroll, consume, publish, analyze. |


### Operations, MLOps & governance


| Skill                                 | What it does                                                                                                  |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| [`rbac`](skills/rbac)                 | Design Snowflake RBAC hierarchies and access-role patterns.                                                   |
| [`mlops`](skills/mlops)               | Router skill for MLOps on Snowflake — maturity assessment, promotion patterns, CI/CD, monitoring, governance. |
| [`dcr-v1-to-v2`](skills/dcr-v1-to-v2) | Migrate a Data Clean Room from the V1 SAMOOHA Provider/Consumer API to the V2 Collaboration API.              |


---

## Authoring a skill

1. **Start from an existing skill** — pick any skill in `skills/` whose shape matches what you're building (e.g. [`rbac`](skills/rbac) for a router skill, [`dcr-v1-to-v2`](skills/dcr-v1-to-v2) for a multi-step pipeline). Copy its folder and rename to your skill's `name`.
2. **Fill in the frontmatter** — `name`, `title`, `summary`, `description` with triggers, `type` (`community` | `snowflake` | `partner`), and `tools` you'll use.
3. **Write the body** — Overview, Workflow with numbered steps, Common Mistakes. Aim for under ~500 words; move reference material to `references/<topic>.md`, executable helpers to `scripts/`.
4. **Pick a license** — Apache 2.0 for community contributors, Snowflake license for employees. The license file lives **inside the skill folder**, not at the repo root.
5. **Test in CoCo** — load your local skill with `Install the skill at <path>` and run a few sessions against representative tasks.
6. **Open a PR** — see [CONTRIBUTING.md](CONTRIBUTING.md) for the review checklist.

---

## Repo structure

```
skills/
  your-skill-name/
    SKILL.md                   # required
    LICENSE                    # required (Apache 2.0 or Snowflake license)
    references/                # optional — additional docs loaded on demand
      patterns.md
    scripts/                   # optional — executable helpers (Python, bash)
      validate.py
    assets/                    # optional — templates, fixtures, sample data
      example.csv
```

`references/` and `scripts/` are first-class. Use them to keep `SKILL.md` focused on the workflow itself; CoCo loads supporting files only when the workflow points at them.

---

## Troubleshooting


| Problem                                           | Fix                                                                                                                        |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `/skill list` doesn't show a skill from this repo | Re-run the install prompt above, then run `/skill sync`.                                                                   |
| Skill triggers in unexpected sessions             | Tighten the `description` field — keep triggers specific, list "Do NOT use for…" cases for adjacent skills.                |
| Skill never triggers                              | Trigger keywords may be too narrow or too generic. Add concrete user phrases the skill should respond to.                  |
| Name collision with a bundled skill               | A repo skill silently shadows a bundled skill of the same name. Run `/skill list` to spot duplicates and rename if needed. |
| SQL fails immediately                             | Most skills need an active Snowflake connection. Run `cortex connections list` and `cortex connections set <name>`.        |


For deeper debugging, read [`docs.snowflake.com/.../cortex-code/extensibility`](https://docs.snowflake.com/en/user-guide/cortex-code/extensibility#label-extensibility-skills).

---

## Disclaimer

These skills are contributed by the community for educational and reference use. Snowflake reviews PRs before merging but cannot guarantee correctness, completeness, or security. Review each skill's `SKILL.md`, `LICENSE`, and any bundled `scripts/` before loading — especially skills that execute SQL, modify Snowflake objects, or call external services.

Snowflake reserves the right to remove skills that violate our [contribution guidelines](CONTRIBUTING.md) or [terms of use](https://www.snowflake.com/legal/).
