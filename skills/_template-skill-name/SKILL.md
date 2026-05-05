---
name: my-skill-name    # unique identifier, matches folder name. Max 3 words, lowercase, hyphens only (e.g. cost-intelligence). Invoked in prompts as $my-skill-name 
title: What Your Skill Does  # human-readable display name, . Max 30 chars (e.g. "Analyze Snowflake Costs"). Displayed in skill catalog and search results
summary: One sentence explaining what this skill does.  # max 140 chars, displayed in skill catalog and search results
description: >-
  Longer explanation including when to use, trigger keywords, and anti-patterns.
  Cortex Code matches user prompts against this field to automatically activate your skill.
  Example: "Use for ALL requests that mention: [action1], [action2].
  Triggers: [keyword1], [keyword2]. Do NOT use for [anti-pattern]."
tools:              # optional: tools to enable when this skill is active
  - snowflake_sql_execute
prompt: "$my-skill-name do something"
language: en
status: Published   # Published | Archived | Hidden
author: First Last
type: community     # community (default) | snowflake | partner
demo-url: https://www.youtube.com    # update with your video URL
---

# My Skill Name

# Demo

> **Optional:** Record a short demo video (under 5 minutes) showing your skill in action. Then:
> 1. Replace `VIDEO_ID` below with your YouTube video ID
> 2. Update `demo-url` in the frontmatter with the full video URL

[![Watch the demo](https://img.youtube.com/vi/VIDEO_ID/0.jpg)](https://www.youtube.com/watch?v=VIDEO_ID)

# When to Use
- List specific trigger phrases or user intents that should invoke this skill
- Be concrete: "User asks to create/build/debug X" not "User needs help with X"
- Include anti-patterns: "Do NOT use for [related-but-different task]"

# What This Skill Provides
Describe the capabilities and domain knowledge this skill adds to Cortex Code.

# Instructions

Write steps as directives to Cortex Code (imperative voice: "Ask", "Load", "Execute").
Keep this file under 500 lines.

## Step 1: [Goal]

**Actions:**
1. **Ask** the user for [specific input needed]
2. **Execute** [specific action]

**Output:** [What this step produces]

**⚠️ STOPPING POINT:** Present findings to user and wait for confirmation before proceeding.

## Step 2: [Goal]

**Actions:**
1. **Execute** the next action
2. **Validate** the result

**If error occurs:**
- Error X: [How to handle]
- Unknown error: Ask user for guidance

**Output:** [What this step produces]

## Best Practices
- Write one best practice per bullet, as a concrete rule not a vague suggestion

## Common Patterns

### Pattern 1: [Name]
Description and example.

### Pattern 2: [Name]
Description and example.

# Tools (Optional)

> Include this section only if your skill uses scripts, CLI commands, or external tools. Delete it otherwise.

**Consider using scripts when:**
- The operation involves API calls or external services
- Logic is complex and benefits from a real programming language
- You need proper error handling, retries, or validation

**Keep it in markdown when:**
- Simple SQL queries (Cortex Code can run these directly)
- File operations or straightforward logic

### tool_name

**Description:** What it does.

**Parameters:**
- `--param1`: [type] - what it controls

**Example:**
```bash
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/my_script.py --param1 value
```

**When to use:** [Specific scenario]

**Script tips:**
- Use argparse for CLI arguments
- Never hardcode credentials; use environment variables
- One script = one job
- Always use absolute paths with `uv run`

# Stopping Points
- ✋ After Step 1 — wait for user confirmation before making changes
- ✋ After Step 2 — if validation fails, do not proceed

**Resume rule:** Upon user approval, proceed directly to the next step without re-asking.

# Output
Describe what the completed skill produces.

# Examples

## Example 1: Basic usage
User: $my-skill-name Do something
Assistant: [Expected behavior]

## Example 2: Advanced usage
User: $my-skill-name Complex task with @file.sql
Assistant: [Expected behavior]

---

## Skill Author Checklist

- [ ] `id` uses verb-noun format (e.g., `deploy-agent`, `build-dashboard`)
- [ ] `description` includes trigger keywords and when to use/not use this skill
- [ ] Steps use imperative voice (`Ask`, `Execute`, `Validate`)
- [ ] Stopping points marked with ⚠️ before any destructive or irreversible action
- [ ] Error handling included for likely failure modes
- [ ] File is under 500 lines
