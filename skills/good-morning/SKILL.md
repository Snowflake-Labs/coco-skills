---
name: good-morning
description: Run the user's personalized morning workflow to check pipelines, metrics, and generate a daily report
---

# When to use
- User says "good morning", "gm", "start my day", or similar greeting
- User wants their daily morning check-in report

# Instructions

## Step 1: Find the user's workflow file
Look for a `goodmorning.md` file in the user's project. This file defines their personalized morning routine.

If no workflow file exists, help them create one (see example template below).

## Step 2: Execute the workflow
Read the workflow file and **follow its instructions exactly**. The workflow defines:
- What queries to run
- What thresholds matter
- What to flag as issues
- What actions to take

Do what the workflow says. Don't add extra steps.

## Step 3: Generate the report
1. Use the HTML template at `morning_report_template.html` if one exists
2. Save to: `goodmorning/YYYY-MM-DD_morning_report.html`
3. Open in browser

## Step 4: Present findings
Summarize key findings to the user and link to the saved report.

---

# Example workflow template

If the user needs to create a `goodmorning.md`, here's an example structure:

```markdown
# My Good Morning Workflow

**Owner**: [username]
**Frequency**: Daily

---

## Overview

What this workflow does and why.

**Output**: HTML file → `goodmorning/YYYY-MM-DD_morning_report.html`

---

## [Section Name]

[Description of what to check and why]

```sql
-- Your query here
SELECT ...
FROM database.schema.table
WHERE ...;
```

**What to look for**: [Thresholds, patterns, or conditions to flag]

---

## [Another Section]

[Add as many sections as you need for your workflow]

---

## Generate Report

Save HTML report and open in browser.
```

---

# Notes
- The workflow file is the source of truth — execute what it says
- Always save a report for historical record
- Open the HTML report in browser when done
