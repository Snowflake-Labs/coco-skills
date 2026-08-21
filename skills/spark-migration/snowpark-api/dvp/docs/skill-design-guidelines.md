# Skill Design Guidelines

Guidelines for designing and implementing new DVP skills.

## What is a Skill?

A skill is a self-contained AI capability with a **single responsibility** that produces a concrete artifact. Each skill has its own folder containing a `SKILL.md` file that defines its behavior, and optionally reference materials and scripts.

## Principles

### 1. Single Responsibility

Each skill does **one thing well**. If a skill is doing too much, it should be split into multiple skills.

**Good:** "Identify pipeline entry points and assess feasibility"
**Bad:** "Identify entry points, map I/O, and generate test code"

### 2. Produces Artifacts

Every skill must generate something tangible:

| Artifact Type | Format | Examples |
|--------------|--------|---------|
| Inventory | JSON, CSV | `entrypoints.json`, `data_io_schema.json` |
| Code | Python (.py) | `test_setup_pipeline_x.py` |
| Scripts | SQL (.sql) | `create_tables.sql` |
| Reports | HTML (.html) | `testing_status.html`, `ewi_dashboard.html` |
| Modified files | Various | Source code with fixes applied |

If a skill doesn't produce an artifact, it's probably not a skill -- it might be a step within another skill.

### 3. Composable

Skills should work independently but also compose well when orchestrated. This means:
- Clear input/output contracts (documented in the skill spec)
- File-based communication (read inputs from disk, write outputs to disk)
- No hidden runtime dependencies between skills

## Skill Folder Structure

```
dvp-<skill-name>/
├── SKILL.md              # Required: skill definition and behavior
├── references/           # Optional: reference documentation
│   └── *.md
└── scripts/              # Optional: supporting scripts
    └── *.py
```

## SKILL.md Format

Every skill must have a `SKILL.md` following this template:

```markdown
---
name: dvp-<skill-name>
description: "Brief description. Triggers: keyword1, keyword2, keyword3."
---

# Skill Title

## Overview
What this skill does in 2-3 sentences.

## Workflow

### Step 1: <Name>
1. Action 1
2. Action 2

### Step 2: <Name>
...

## Stopping Points
- List conditions where the skill should stop and ask for user input

## Output
- What artifacts are produced
- Format and location
```

### SKILL.md Frontmatter

The frontmatter is critical for skill discovery:

```yaml
---
name: dvp-<skill-name>          # Unique identifier
description: "..."               # Description + trigger keywords
parent_skill: dvp-orchestrator   # Optional: parent orchestrator
---
```

**Description field:** Include trigger keywords that help the AI identify when to use this skill. Example:
```yaml
description: "Scan .py files for EWI codes and resolve them. Triggers: fix EWIs, resolve SPRKPY, SMA warnings, migration issues."
```

## Workflow Design

### Step Pattern

Each step should follow this pattern:
1. **Read** -- Gather inputs (files, inventories, user input)
2. **Process** -- Analyze, transform, generate
3. **Output** -- Write artifacts to disk
4. **Report** -- Summarize what was done

### Stopping Points

Define explicit stopping points where the skill should pause and ask the user:
- When a decision is needed (ambiguous case)
- When a reference is missing
- When the skill encounters an error it can't resolve
- Before making destructive changes

### User Interactions

If a skill requires user input, document it clearly:

```markdown
## Required User Interactions

### Before <Phase>
You MUST confirm with the user:
1. **<Decision>** -- Ask: "> Question text here"
```

## Naming Convention

| Component | Pattern | Example |
|-----------|---------|---------|
| Skill name | `dvp-<category-verb>` | `dvp-entrypoint-identifier` |
| Folder | Same as skill name | `dvp-entrypoint-identifier/` |
| Inventory output | `<noun>.json` | `entrypoints.json` |
| Report output | `<noun>_<type>.html` | `testing_status.html` |
| Generated code | `<purpose>_<pipeline>.py` | `setup_pipeline_x.py` |

## Input/Output Contract

Each skill should clearly document:

### Inputs
```markdown
| Input | Required | Description |
|-------|----------|-------------|
| Source code | Yes | Path to .py files |
| Entrypoints inventory | No | From dvp-entrypoint-identifier (`entrypoints.json`) |
```

### Outputs
```markdown
| Output | Format | Location |
|--------|--------|----------|
| Entrypoints inventory | JSON | `dvp/04-results/entrypoints.json` |
```

## Testing Your Skill

Before considering a skill complete:

1. **Manual test** -- Run the skill on a real or sample migrated project
2. **Edge cases** -- Test with empty inputs, missing files, large codebases
3. **Integration** -- Verify outputs are consumable by downstream skills
4. **Documentation** -- Ensure SKILL.md is clear and complete

## Adding a New Skill

1. Create the skill folder: `dvp-<skill-name>/`
2. Write the `SKILL.md` following the template above
3. Add reference documents if needed: `references/*.md`
4. Add supporting scripts if needed: `scripts/*.py`
5. Update `docs/skills-catalog.md` with the new skill
6. Update the category README (`docs/data-validator/README.md` or `docs/tracking-manager/README.md`)
7. Create a doc page: `docs/<category>/dvp-<skill-name>.md`
8. Update the main `README.md` table

## Anti-Patterns

| Anti-Pattern | Why It's Bad | Alternative |
|-------------|-------------|-------------|
| Skill does 3+ things | Violates single responsibility | Split into separate skills |
| Skill has no output | Can't be composed or verified | Define a concrete artifact |
| Hardcoded paths | Not portable | Use relative paths or parameters |
| Hidden dependencies | Skills can't run independently | Document all inputs explicitly |
| No stopping points | AI may make wrong decisions silently | Add decision points |
| Vague SKILL.md | AI can't reliably execute the skill | Be specific and step-by-step |
