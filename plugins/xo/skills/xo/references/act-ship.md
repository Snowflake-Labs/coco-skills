---
name: act-ship
description: "Ship stage — deliver the completed work to its audience. Pure delivery mechanics only — validation and approval are prior stages' responsibility. Multi-mode: PR, route-to-requestor, publish, deploy, share."
---

# Ship

Deliver the completed work to whoever needs it. Ship handles purely the mechanics of getting the artifact to its audience. It does not validate, review, or gate — those are Prove's and Review's jobs.

## When to use this stage

- After the operator has approved the work (via Review or direct confirmation)
- The artifact is ready to deliver — all gates passed

---

## Gate guard (reminder, not a re-run)

Before delivering, confirm prior stages were completed:

> **Check 1 — Reviewed?** Has this artifact been through Review (operator approval)? If not — pause and route to Review first. Do not ship unreviewed work. (If the operator explicitly said "ship it" or the pathway already passed through Review, this check is satisfied — proceed.)
>
> **Check 2 — Still current?** Before an irreversible post to a remote or shared target (PR, publish, share to a live thread), re-fetch **both** its code and its discussion surface and reconcile — it may have moved while the work was in Review. This check **always runs**, including after Review passed — time in Review is exactly when the target drifts. See the freshness reconcile in `references/core-guidelines.md` ("Freshness and concurrency") and `references/decide-review.md` (Step 3, item 4).

Check 1 is a reminder to the orchestrator, not a validation step. Check 2 is a required gate before any irreversible post — not waived by "ship it" or a prior Review pass.

---

## Delivery mode decision

| Work type | Delivery mode | Target |
|---|---|---|
| Code changes in a repository | **Pull request** | Repo reviewers via `gh pr create` |
| Standalone deliverable (doc, analysis, spec) | **Route to requestor** | The person/channel who raised the demand |
| Documentation for a docs site | **Publish** | Docs platform (Confluence, docs repo, wiki) |
| App or service | **Deploy** | Platform (SPCS, Streamlit in Snowflake, hosting service) |
| Demo, presentation, or shared artifact | **Share** | Stakeholders via appropriate channel |

Ask the operator if the delivery target isn't obvious from the spec.

---

## Mode: Pull request (repository work)

### Input contract

- Worktree path and branch name (from Provision)
- Build/Prove summary
- Target repository + organisation

### Steps

1. **Auth** — confirm push access to the target repo. For Snowflake repos, confirm correct org. If issues, load `$snowflake-github`.

2. **Push + create PR:**
   ```bash
   git -C <worktree> push -u origin <branch-name>
   ```
   Fetch PR template (`<repo>/.github/PULL_REQUEST_TEMPLATE.md`) or use standard format. Create with `gh pr create`.

3. **Record** — update task file with PR URL, branch name, status `In Review`. Inform operator.

### Handoff

When reviewers leave comments → route to **Review** (`references/decide-review.md`, respond to feedback).
When merged → route to **Cleanup**.

---

## Mode: Route to requestor (task deliverables)

For work where someone asked for a specific output.

### Steps

1. **Deliver** — present the artifact to the operator with:
   - Summary of what was produced
   - Where it lives (path or location)
   - Any caveats or follow-up needed

2. **Record** — update task file. If requestor is external, ask operator whether to send directly or hand back.

---

## Mode: Publish (documentation)

For guides, docs, or reference material reaching a documentation platform.

### Steps

1. **Submit** — depending on platform:
   - **Docs repo:** Commit + PR (reuse PR mode)
   - **Confluence/wiki:** Use available API or ask operator to paste/upload
   - **Static site:** Commit to content directory + trigger build

2. **Record** — note published location in task file and WI notes.

---

## Mode: Deploy (apps and services)

For Streamlit apps, SPCS services, or other deployed artifacts.

### Steps

1. **Deploy** — depending on target:
   - **Streamlit in Snowflake:** Load `$developing-with-streamlit-in-snowflake` skill
   - **SPCS:** Load `$deploy-to-spcs` skill
   - **Snowflake App Runtime:** Load `$snowflake-apps` skill
   - **Other platforms:** Follow platform-specific steps or check for relevant available skills

2. **Verify accessibility** — confirm the deployment is reachable. Provide URL/endpoint to operator.

3. **Record** — note deployment URL, version/commit, and rollback instructions in WI notes.

---

## Mode: Share (demos, presentations, ad-hoc artifacts)

For work reaching stakeholders via informal channels.

### Steps

1. **Deliver** — share via the appropriate channel. Ask operator for routing if unclear.

2. **Record** — note what was shared, with whom, and where it lives.

---

## On completion

After delivering, before treating the work as finished: record (update the task file and advance the `Provisional Pathway` `(here)` marker), then **proactively offer the operator the next stage** — Capture (keep durable learnings) then Cleanup (tear down the workspace) — once, dismissable. This is the moment close-out is most often skipped; the offer is the countermeasure. Do not auto-run Cleanup/commit while the operator may still be reviewing.

## Chaining (soft — reminders, not gates)

- **Usually follows:** Review (operator approved the work) — or operator's direct "ship it" instruction.
- **Leads to — primary:** Capture (keep durable learnings) → then Cleanup (tear down workspace once accepted). *Capture before teardown.*
- **Or:** Review (`references/decide-review.md`) if feedback arrives post-delivery.
