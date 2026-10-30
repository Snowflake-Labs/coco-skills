# Agent Playbook — for after the app is already deployed

This file only matters if someone asks for help *after* the app is already deployed — fixing something that isn't working, or changing how it looks or works. It has nothing to do with the initial deploy, which is entirely covered in `SKILL.md`. Don't read this file, or mention it, during a deploy — only open it once there's an actual fix or change request to handle.

## Customer wants a new feature or customization (not a fix)

Check this before opening any code: is the request actually something the app's own embedded agent can already do, live, in the running app — no engineering session needed? That covers: splitting/tagging expenses by a new rule described in plain language, a new derived column, a new dashboard filter value, or pointing the app at an existing Snowflake semantic view. All of that happens at Step 7 (Validate) of the Settings wizard — see `SKILL.md`'s "Custom logic" section and README.md §14. If that's what's being asked for, send the customer there instead of writing code.

If it's genuinely a new UI/chart type, a restructured page, or new component behavior — something the in-app agent explicitly can't do (it's read-only-SQL-only by design, see `ARCHITECTURE.md`) — that *is* a real code change, and the rest of this playbook is exactly the process for it: checkpoint first, make the change, test before and after, redeploy, confirm it actually finished. Nothing about it is special-cased beyond that.

Tell the customer plainly which case it is before doing anything — e.g. "That's actually something you can set up yourself right now, no code change needed — here's where" vs. "That needs an actual update to the app's code, which I'll make and then redeploy."

## How to talk to the user while doing this work

- Say what you're doing and why, in everyday language. If you're about to change something, say what it changes and what the customer should expect — don't just announce an action with no context.
- Skip unexplained jargon. If a technical term is genuinely the clearest way to say something, explain it in the same breath — e.g. "I'm going to redeploy the app, which means uploading the updated code and having Snowflake rebuild and restart it."
- Don't be vague either. "I'm making a few adjustments" tells the customer nothing useful. Say specifically what's changing.

## Before making any change: save a way back

Two separate checkpoints, both worth having before touching anything:

- **The live app itself:** every time this app is deployed, Snowflake keeps the previous version — it's never deleted or overwritten. Before changing anything, check which version is currently running. If a change causes a problem after redeploying, the app can be switched back to the exact version that was working before, in seconds, without losing any data.
- **The code on disk:** save the current state of the code (using git — set it up first if this folder isn't already tracked) before editing it, so a local edit can be undone on its own, separate from the live app.

Explain this to the customer in plain terms too, for example: "Before I change anything, I'm going to note down what's currently running and save the code as it is right now — that way, if this doesn't work the way we want, I can put everything back exactly as it was."

## Check the actual problem before fixing it

Don't guess at a cause from a description alone if there's a way to check directly. For this app, Snowflake keeps a log of what actually happened inside the running service, which is often more reliable than what shows up on screen.

## Test before and after any change

Run the app's existing test suite before starting, so you know what "working" looked like beforehand, and again after, so you know the change didn't break something else. If you fixed a specific problem, add a small test that would have caught it, so it can't quietly come back later.

## Watch for a few common Snowflake SQL mistakes

- Combining a text value and a number in the same fallback/default expression can silently misbehave — Snowflake sometimes treats the whole expression as a number, which breaks on real text.
- Be careful with how missing values (`NULL`s) behave in comparisons and joins.
- Double check row counts after a join — a join that matches more than one row per record can quietly inflate totals.
- Watch for dividing by zero when calculating percentages or ratios.

## If something "isn't working" even after a fix, check every place that setting or data is stored

This app keeps some information in more than one place — its data tables, its saved settings, and a short-lived cache. If a fix doesn't seem to take effect, make sure all of the relevant places were actually updated, not just the most obvious one.

## Confirm a change actually finished before declaring it done

Deploying a change can take a few minutes and can look "stuck" without actually failing. Confirm it by checking the app's real status in Snowflake, not just by watching the deploy command's own output.

## Where to look when someone asks for a change to how the app looks or works

| What they're asking for | Where it lives |
|---|---|
| How the dashboard looks, or how numbers are formatted | `<APP_SOURCE>/components/bva-dashboard.tsx`, `.../lib/format.ts` |
| How the underlying budget-vs-actuals data is calculated | `<APP_SOURCE>/lib/config.ts` |
| The sample/demo data shown before real data is connected | `<APP_SOURCE>/lib/demo-seed-data.ts` |
| Where saved settings are stored | `<APP_SOURCE>/lib/config-server.ts` |
| How the setup wizard finds and maps tables | see "How the setup agent works" in `SKILL.md` |

## Keep changes focused

Only change what was actually asked for. Don't rewrite unrelated code while you're in there, and if you're touching the sample demo data, keep it obviously fake — never anything that resembles a real company's data.
