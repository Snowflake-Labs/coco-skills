---
name: quickstart-guide
title: Learn Snowflake Quickstarts
summary: Paste a Snowflake Quickstart URL and get a guided, interactive learning experience.
description: >-
  Use when a user pastes a Snowflake Quickstart URL or asks to learn/walk through a Quickstart.
  Fetches the source content from GitHub, parses it into stages, checks the learner's environment,
  and delivers a guided build experience with two modes (learner or builder).
  Triggers: quickstarts.snowflake.com, snowflake.com/en/developers/guides/, walk me through this
  quickstart, teach me this quickstart, learn this guide, I want to do this quickstart.
  Do NOT use for general SQL help or non-Quickstart tutorials.
tools:
  - snowflake_sql_execute
  - web_fetch
  - Bash
  - Read
  - Write
  - ask_user_question
prompt: "$quickstart-guide https://www.snowflake.com/en/developers/guides/getting-started-with-cortex-agents/"
language: en
status: Published
author: Gilberto Hernandez
type: snowflake
---

# Quickstart Guide

Take a Snowflake Quickstart URL and deliver a guided, interactive learning experience. The learner pastes a link; you fetch the content, plan the build, check their environment, and teach it live.

## When to Use

Trigger this skill when the user's input contains a URL matching either pattern:

- `https://www.snowflake.com/en/developers/guides/<slug>` (trailing slash optional)
- `https://quickstarts.snowflake.com/guide/<slug>` (trailing slash optional, legacy pattern)

Also trigger when the user says something like "walk me through [quickstart name]" or "teach me [quickstart topic]" alongside a URL.

## Workflow

### Step 1: Detect and Extract Slug

Parse the URL to extract the Quickstart slug.

**New pattern:** `https://www.snowflake.com/en/developers/guides/<slug>`
- Extract everything after `/guides/` up to the next `/` or end of string

**Old pattern:** `https://quickstarts.snowflake.com/guide/<slug>`
- Extract everything after `/guide/` up to the next `/` or end of string
- Note: old slugs may use underscores where new ones use hyphens

Store the extracted slug for Step 2.

### Step 2: Fetch Quickstart Content

Retrieve the source markdown from GitHub. The Quickstart source lives at:
`https://github.com/Snowflake-Labs/sfquickstarts` under `site/sfguides/src/<folder>/`

**Fetch sequence:**

1. **Get the folder listing.** Use `web_fetch` on:
   `https://api.github.com/repos/Snowflake-Labs/sfquickstarts/contents/site/sfguides/src/<slug>`
   This returns a JSON array of file objects.

2. **If 404, try slug variations:**
   - Replace hyphens with underscores (or vice versa)
   - Try the original slug with/without trailing characters
   - Repeat the folder listing request with the alternate slug

3. **Fetch the raw markdown.** From the JSON array response, access and read the raw markdown content by fetching the value for the `download_url` key of the `.md` file.

4. **Verify the content.** Read the first ~20 lines of the fetched markdown. Check that the `id:` field in the frontmatter matches the URL slug. If it doesn't match, you may have the wrong Quickstart — try another variation.

5. **Fallback.** If GitHub fetch fails entirely (repo restructured, file moved), fall back to `web_fetch` on the original URL the user pasted. Parse whatever structured content is available from the rendered page.

**If fetch fails completely:** Tell the learner the content couldn't be retrieved. Ask if they have the content locally or can provide an alternative URL.

### Step 3: Parse and Plan Stages

Load `references/quickstart-parsing.md` for detailed structure rules.

From the fetched markdown, extract:

1. **Metadata** from frontmatter: `id`, `summary` (title), `authors`, `categories`, `duration`
2. **Prerequisites** from the Prerequisites/Setup steps: roles, privileges, warehouses, external tools
3. **Logical stages** by mapping Quickstart steps:
   - Overview/Prerequisites → inputs for diagnostics (Step 5)
   - Setup/Environment → Stage 1 (schema, grants, warehouse)
   - Core tutorial steps → Stages 2-N (one per major deliverable)
   - Steps with external dependencies (SPCS, third-party tools) → optional stages
   - Conclusion → use as source material for the recap in Step 8
4. **Code blocks** (SQL, Python, YAML) — these are what gets executed or written to files
5. **Final deliverable** — what the learner will have when done

Target 4-8 stages total. Combine small steps; split large ones.

### Step 4: Check for Companion Repo

Many Quickstarts have a companion GitHub repo containing SQL scripts, Python code, YAML configs, or data files. Check for one.

**Detection:**
- Look for a `fork repo link:` field in the Quickstart frontmatter
- Scan the markdown body for GitHub URLs pointing to a `Snowflake-Labs/sfguide-*` repo (typically in a Prerequisites or Setup step)

**If a companion repo is found:**

1. Use the GitHub API to list its top-level contents:
   `https://api.github.com/repos/<owner>/<repo>/contents/`
   Note the key files (`.sql`, `.py`, `.yaml`, `.json`, notebooks).

2. Present the finding to the learner: "This Quickstart has a companion repo at [url] containing [list key files — e.g., setup.sql, app.py, model.yaml]."

3. Ask the learner via `ask_user_question`:
   - **Clone it** — "Download the full repo to my machine. I'll use the files directly." Best if you want to keep the code after the session.
   - **Reference it (lightweight)** — "Don't download anything. Access individual files from GitHub as needed." No git clone — files are fetched on demand during the build, same way the Quickstart markdown was fetched.

4. **If Clone:** Ask where to clone (suggest current directory as default). Run `git clone <url> <path>`. Reference files from the cloned path during execution.

5. **If Reference (lightweight):** During Step 7 execution, fetch individual files on demand via the GitHub API (`download_url` from the contents listing) — the same pattern used for the Quickstart markdown itself. No local clone needed.

**If no companion repo is found:** Skip this step silently.

**⚠️ STOPPING POINT**: Wait for the learner's choice before proceeding.

### Step 5: Lightweight Diagnostics

Check that the learner's environment can handle this Quickstart.

**Run session context:**
```sql
SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME(), CURRENT_ROLE(),
       CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_USER();
```

**Check grants on current role:**
```sql
SHOW GRANTS TO ROLE <current_role>;
```

**Compare against Quickstart requirements:**
- Does the role have CREATE SCHEMA/TABLE/VIEW privileges?
- Is a warehouse set and active?
- Does the Quickstart need specific features (Cortex, SPCS, Streamlit)?
- Does the Quickstart need access to SNOWFLAKE_SAMPLE_DATA or ACCOUNT_USAGE?

**Present findings:**
- If ready: "Your environment looks good for this Quickstart."
- If gaps: List what's missing with specific GRANT statements to fix each one.

**⚠️ STOPPING POINT**: If issues found, present them. Ask the learner: "Want to fix these first, or proceed anyway?" Do not block — let them choose.

### Step 6: Introduce and Choose Mode

**Attribution:**
"This is the *[title from summary field]* Quickstart."

**Explain the deliverable:**
Summarize what the learner will have at the end — the tables, services, apps, or models they'll build.

**Nudge (CLI/Desktop only):** Suggest opening Snowsight alongside the session, rendering their account URL from the diagnostics results: "You may want to open Snowsight to explore objects as we build them: https://app.snowflake.com/<org_name>/<account_name>"

**Set up learning database:**
```sql
CREATE DATABASE IF NOT EXISTS LEARN_SNOWFLAKE_QUICKSTARTS;
```
Ask permission before creating. Then create a schema named after the slug:
```sql
CREATE SCHEMA IF NOT EXISTS LEARN_SNOWFLAKE_QUICKSTARTS.<slug_as_identifier>;
```

**Choose mode** using `ask_user_question`:
- **Learner mode** — Step by step. I'll explain what we're building at each stage, execute it, recap, and check in before moving on.
- **Builder mode** — Build everything at once. I'll execute all stages and give you a full summary at the end.

Load `references/teaching-guide.md` for mode-specific behavior.

**⚠️ STOPPING POINT**: Wait for mode selection before proceeding.

### Step 7: Execute Learning Journey

Execute each stage from the plan created in Step 3.

**Detect the execution surface:**
- If the working directory is under `/workspace/` → **Snowsight workspace**
- Otherwise → **CLI**

---

**Learner mode (CLI):**

For each stage:
1. Explain what will be built and why
2. Render the SQL/Python/code directly in the terminal with explanatory comments so the learner can read and understand it
3. Use `ask_user_question` to give the learner agency before executing — e.g., "Run it", "Explain more", "I want to make changes"
4. Execute via `sql_execute`
5. Brief recap: what was created, key results (row counts, object status)
6. **⚠️ STOPPING POINT** — pause between stages

**Learner mode (Snowsight workspace):**

For each stage:
1. Explain what will be built and why
2. Write code to `.sql` or `.py` files with explanatory comments
3. Prompt the learner to run the file
4. Brief recap after they've run it
5. **⚠️ STOPPING POINT** — pause between stages

**Builder mode (any surface):**
- CLI: execute all stages directly via `sql_execute` without pausing
- Snowsight: write all files and tell the learner to run them in sequence
- Only stop for optional stages (ask include/skip)
- Full summary at the end

---

**Handling outdated content:**
If the Quickstart uses deprecated syntax or old patterns, flag it:
"Note: This Quickstart uses [old pattern]. The current recommended approach is [new pattern]. I'll use the modern approach."
Use the correct current approach — don't blindly replicate deprecated code.

**Handling errors:**
If a stage fails during execution:
1. Read the error
2. Diagnose the likely cause (permissions, missing prerequisites, syntax)
3. Suggest a fix or workaround
4. Ask the learner how to proceed

### Step 8: Recap and Cleanup

After all stages complete:

**Recap:** Briefly congratulate the learner and summarize what they built — the key objects created, the concepts covered, and what they now have running in their account. Keep it concise (3-5 sentences). Draw from the Quickstart's Conclusion step if available.

**Cleanup:** Then ask the learner: "Want to keep everything, or clean up?"
- If cleaning up: drop the schema (which drops all objects within it)
  ```sql
  DROP SCHEMA IF EXISTS LEARN_SNOWFLAKE_QUICKSTARTS.<slug_as_identifier> CASCADE;
  ```
- Never drop the `LEARN_SNOWFLAKE_QUICKSTARTS` database itself
- Never drop or alter any Snowflake-provided databases

## Stopping Points

- ⚠️ After companion repo detection (Step 4) — wait for clone/reference choice
- ⚠️ After diagnostics (Step 5) if environment issues found
- ⚠️ After mode selection (Step 6) — wait for learner's choice
- ⚠️ Between stages (Step 7, learner mode only)
- ⚠️ Before optional stages (both modes) — ask include/skip
- ⚠️ Before cleanup (Step 8) — ask keep/remove

## Troubleshooting

**Quickstart not found on GitHub:**
- Try alternate slug formats (hyphens ↔ underscores)
- Check if the Quickstart was recently renamed or archived
- Fall back to web_fetch on the original URL

**Learner's role lacks privileges:**
- Present specific GRANT statements
- Suggest switching to a role with more access
- Skip stages that require unavailable privileges and note what was skipped

**Quickstart references external tools (Kafka, S3, third-party APIs):**
- Mark those stages as optional
- Explain what would happen if the tool were available
- Offer to simulate or skip

**Quickstart is very long (15+ steps):**
- Chunk into logical phases (setup, core build, optional extensions)
- In learner mode: add phase-level checkpoints
- In builder mode: execute in phases with brief summaries between

**Code execution fails:**
- Read error message carefully
- Common issues: wrong schema context, missing warehouse, object already exists
- Fix and retry — don't ask the learner to debug unless the fix is unclear

## Examples

### Example 1: Basic usage
User: $quickstart-guide https://www.snowflake.com/en/developers/guides/getting-started-with-cortex-agents/
Assistant: Fetches the Cortex Agents Quickstart, checks environment, asks mode, and guides the learner through building a sales intelligence agent.

### Example 2: Legacy URL
User: $quickstart-guide https://quickstarts.snowflake.com/guide/getting_started_with_dataengineering_ml_using_snowpark_python
Assistant: Detects legacy URL pattern, converts underscores to hyphens, fetches content, and delivers the Data Engineering with Snowpark experience.
