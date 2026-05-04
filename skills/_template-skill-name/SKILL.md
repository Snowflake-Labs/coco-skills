---
id: my-skill-name
name: my-skill-name
skill-name: $my-skill
description: One sentence explaining what this skill does and when to use it.  # max 140 chars
prompt: "$my-skill do something"
language: en
status: Published
author: First Last
type: community     # community | bundled | partner
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

# Stopping Points
- ✋ After Step 1 — wait for user confirmation before making changes
- ✋ After Step 2 — if validation fails, do not proceed

**Resume rule:** Upon user approval, proceed directly to the next step without re-asking.

# Output
Describe what the completed skill produces.

# Examples

## Example 1: Basic usage
User: $my-skill Do something
Assistant: [Expected behavior]

## Example 2: Advanced usage
User: $my-skill Complex task with @file.sql
Assistant: [Expected behavior]

---

## Skill Author Checklist

- [ ] `summary` includes trigger keywords and when to use/not use this skill
- [ ] Steps use imperative voice (`Ask`, `Execute`, `Validate`)
- [ ] Stopping points marked with ⚠️ before any destructive or irreversible action
- [ ] Error handling included for likely failure modes
- [ ] File is under 500 lines
