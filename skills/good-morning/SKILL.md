---
name: good-morning
description: Daily workflow that checks the things you care about and generates a morning report. Triggers include good morning, gm, start my day.
---

# Good Morning

An AI assistant that knows what you care about and checks it for you every morning.

**Credits**: Tyler Richards, Zachary Blackwood, Mark Huberty, Tyler Simons

---

## How It Works

1. **Read your manifesto** — A file that describes what you care about, where your data lives, and what you check daily
2. **Run your checks** — Query dashboards, look for week-over-week changes, check for errors
3. **Generate a report** — HTML file using your template, opened in browser

---

## Setup

Create a `goodmorning.md` file in your project that describes:

```markdown
# My Good Morning Manifesto

## What I Care About
- [Product/feature you own]
- [Metrics that matter to you]
- [Customers or accounts you track]

## Where My Data Lives
- [database.schema.table_name] — [what it contains]
- [another_table] — [what it contains]

## What I Check Every Morning

### Dashboard Checks
[Describe the charts you look at. What are you looking for? Week-over-week changes? Spikes? Drops?]

### Error Checks  
[What logs or tables do you check for errors? What does "bad" look like?]

### Follow-ups
[What actions do you take when you find something? Draft a message? Create an issue? Dig deeper?]
```

---

## Running the Workflow

When triggered, the assistant will:

1. Look for `goodmorning.md` in your project (or ask you to create one)
2. Read the manifesto to understand your context
3. Run SQL queries against the tables you specified
4. Compare this week vs last week for each metric
5. Flag anything that looks unusual
6. Generate an HTML report using `morning_report_template.html`
7. Open the report in your browser

---

## HTML Template

The skill uses `morning_report_template.html` for consistent formatting. If none exists, create one or use this starter:

```html
<!DOCTYPE html>
<html>
<head>
    <title>Morning Report - {{DATE}}</title>
    <style>
        body { font-family: system-ui, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; }
        h1 { border-bottom: 2px solid #333; padding-bottom: 10px; }
        .section { margin: 20px 0; }
        .good { color: #059669; }
        .bad { color: #DC2626; }
        .warning { color: #D97706; }
        table { border-collapse: collapse; width: 100%; margin: 10px 0; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background: #f5f5f5; }
    </style>
</head>
<body>
    <h1>Good Morning — {{DATE}}</h1>
    
    <div class="section">
        <h2>Alerts</h2>
        {{ALERTS}}
    </div>
    
    <div class="section">
        <h2>Dashboard Checks</h2>
        {{DASHBOARD_CHECKS}}
    </div>
    
    <div class="section">
        <h2>Week over Week</h2>
        {{WOW_CHANGES}}
    </div>
    
    <div class="section">
        <h2>Follow-ups</h2>
        {{FOLLOWUPS}}
    </div>
</body>
</html>
```

Save this as `morning_report_template.html` in your project, then customize it.

---

## Example Manifesto

```markdown
# My Good Morning Manifesto

## What I Care About
- User adoption of our new feature
- Error rates in production
- Key customer accounts (Acme Corp, BigCo)

## Where My Data Lives
- analytics.prod.daily_metrics — DAU, feature usage, errors
- analytics.prod.account_health — per-account metrics

## What I Check Every Morning

### Dashboard Checks
I look at daily_metrics for the past 7 days. I want to see:
- DAU trending up or stable
- Feature adoption > 10%
- Error rate < 1%

Flag anything that changed more than 20% week-over-week.

### Error Checks
Check for rows in daily_metrics where error_count > 100.
Check for any account in account_health where health_score dropped below 50.

### Follow-ups
If error rate spikes: draft a message to #engineering with the details.
If a key account's health dropped: prepare talking points for the account team.
```

---

## Output

Reports are saved to `goodmorning/YYYY-MM-DD_morning_report.html` and opened automatically.
