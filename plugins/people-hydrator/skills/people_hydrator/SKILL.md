---
name: people_hydrator
description: "Sets up the dehydrated People skills on a customer's own HR data. The analyst describes their goals and picks skills; the engine searches every database their role can see for each data need of the chosen skills (headcount, exits, requisitions, surveys…) by what the tables hold, binds tables, fields, values and definitions using data profiles, a bounded sample of recent usage and consented business context (Confluence, SharePoint, Google Drive, Jira, Slack, Teams, email or uploaded docs), shows each skill's real output, asks only the business questions it can't settle, turns the analyst's own feedback into validated changes, checks each skill, runs its golden cases on the customer's data, saves the analyst's corrections and re-checks them later, and hydrates each skill. What it learns is saved as a versioned customer profile reused by every skill. Use when: people hydrator, hydrate people skills, set up people plugin, onboard HR data, people hydrator v3, add more people skills, configure attrition/headcount/CHRO/recruiting skills for my data."
license: Proprietary. See LICENSE.txt for complete terms
---

# People Hydrator

Internal ID: `people_hydrator`. Call it **People Hydrator** when talking to the analyst.

Turns the dehydrated People skills into skills that run correctly on THIS customer's data.

**Persona:** People Analytics analyst. The analyst picks skills, looks at the real output, answers a few business questions, says what's wrong in their own words, and approves changes. All searching, profiling, mapping and checking runs in the engine.

## Step 0: detect the environment, then pick the experience (do this first)

The flow is the same everywhere: **Welcome → 1 Discover → 2 Bind → 3 Tune → 4 Hydrate**. It runs in chat, in Snowsight or in Cortex Code on the analyst's computer.

1. Run `command -v snow python3; uname -s` (or check whether a shell tool exists at all).
   - Local shell with `snow` and a configured connection → **Cortex Code on the analyst's computer**.
   - No local shell or no `snow` → **Snowsight** (Cortex Code in the browser).
2. **Snowsight:** run the chat flow below. SQL automatically uses the Snowsight session (`ENGINE_SQL=snowpark`); `--connection` can be any value there because it is ignored.
3. **Local:** run the same chat flow; SQL uses the analyst's Snowflake CLI connection.
4. **Workspace folder `W` (always the same one):** locally `~/people_hydrator_ws`; in Snowsight `people_hydrator_ws/` at the root of the analyst's workspace (next to `.snowflake/`), so it is saved with the workspace. Never use a temporary folder: `W` holds the customer profile, every answer and fix, and is what lets a later session (adding more skills) skip questions already answered. If `W/profile.json` exists, say so in one line ("Picking up your saved profile: N settings you confirmed") and reuse it.

Start every stage in chat with a one-line marker, e.g. "**Stage 3 of 4 · Tune**: here's what your skills would show; 2 quick questions".

**How to talk in every stage:** show results first, ask only what the engine can't settle, and post a one-line progress note before and after every step that takes more than a few seconds ("Reading 6 databases… done: HR data in HR_DB"). Never show raw JSON, internal keys or check codes to the analyst; use the plain labels the scripts return.

## Contracts (workspace folder `W`)

| File | Written by | Purpose |
|---|---|---|
| `prewarm.json`, `sources.json` | `prewarm.py`, `detect_sources.py` | Connection/role check, visible databases, context tools found |
| `recommendation.json` | `recommend_skills.py` | The analyst's goals, and fit per skill (recommended / consider / not relevant) with reasons |
| `topics.json`, `scan_metadata.json` | `discover_topics.py` | Per data need of the chosen skills: ranked candidate tables with reasons and the choice; per database: what was found; how usage was measured |
| `mapping_review.json` | `mapping_review.py` (run by `bind.py`) | Per skill: which table serves each data need, key columns, confidence and why; needs with nothing found plus the closest suggestion |
| `binding_gate.json` | `eval_gate.py` (run by `bind.py`) | Per skill: the data-connection check from its eval plan (each rule passed or not, in plain words) and the grade: EQUIVALENT (everything needed is connected), DEGRADED (works with gaps), INCOMPATIBLE (key data not confirmed) |
| `data_index.json` | `profile_data.py`, `mine_usage.py` | Profiles of the chosen tables only (reused when skills are added later) |
| `context.json` | `context_store.py` | Consented sources + cited, redacted business-context items |
| `bindings.json` | `match_fields.py` | Per data need: object, row filter, field → column or expression, evidence |
| `learned.json` | chat / fix list | Analyst corrections. Never overwritten by the engine. |
| `tuning/<skill>.json` | `build_tuning.py`, `questions.py` | Definitions and thresholds with source and evidence |
| **`profile.json`**, `profile_history.jsonl` | `profile_store.py` | **Customer Context Profile**: one versioned view of connections, roles (data need → table), table facts and fingerprints, glossary (including the analyst's own rules), conventions, corrections. Every entry has value, status (inferred / confirmed / deferred / stale / conflict), evidence, sources, scope |
| `bind_delta.json` | `bind.py` | Per chosen skill: data reused from earlier runs, found now, still missing |
| `questions.json`, `questions_state.json` | `questions.py` | Business questions to ask (preselected answer + evidence), decided ones, hidden ones, what can't be computed yet |
| `ai_definitions/<skill>.json` | `definitions.py suggest` | Assistant-suggested questions for the skill, checked against the data; reused until the skill or its data changes |
| `previews/<skill>.json` | `preview.py` | The skill's real output (aggregates), its definitions, and a signature that tells when it's out of date |
| `fix_ledger.json` | `fix_ledger.py` | Feedback, failed checks and number differences, each with a validated proposed change |
| `review_state.json`, `corrections.json`, `customer_rules.json` | `review.py`, `corrections.py`, `fix_ledger.py` | Per skill: preview reviewed or not; every accepted fix as a re-runnable check; the analyst's own rules the hydrated skills follow |
| `templates/people/evals/<skill>/` | shipped with the plugin (generated from `plugins/external/people/evals/`) | Per skill: `eval_plan.md` (what correct data looks like, with the `binding-gate` rules Bind grades against) and `golden_cases.md` (test prompts, expected behavior, guard checks, run at the end of Tune). Linked from `eval_dir` in `catalog/people.json` |
| `draft/`, `evaluation.json` | `build_customer_plugin.py`, `evaluate.py` | End of Tune: a draft build per skill, and per golden case its result (pass / fail / not tested), the failed check in plain words, and a fingerprint of the build it ran on (a later change makes it stale) |
| `validation_report.json`, `validation_cache/` | `check_bindings.py` | Per skill: compile, run, sanity, double counting, definitions; verdict + plain reason; passes are cached |
| `hydration_summary.json`, `customer-people/` | `build_customer_plugin.py` | Hydrated skills, status per skill, numbers confirmed or not, profile version |

Scripts live in `engine/scripts/` (stdlib only). `C` is the connection name.

## Welcome (and pre-warm)

1. Start the pre-warm immediately, before talking: `python3 engine/scripts/prewarm.py --workspace W --connection C`.
2. While it runs, welcome the analyst to **People Hydrator** and introduce the process in a few lines:
   - The plugin ships dehydrated skills; hydration connects each one to the customer's tables and definitions, shows the real output, checks it with them, and produces skills that run on their data.
   - The four stages, one line each.
   - They can describe what they want to achieve and you'll recommend skills.
   - **What we learn is saved as their customer profile and shared by every skill**, so adding a skill later only processes what's new.
   - **What we read:** table and column names, usage patterns and aggregate counts in the databases their role can access. Never employee records. Business documents only with consent.
3. Report the pre-warm result in one line: role, number of databases visible, context tools found.

## Stage 1 · Discover

1. **Prerequisites:** `prewarm.json` has `connection_ok: true`. If not, show `error` and stop.
2. **Goals → recommended skills:** ask "What are you hoping to achieve?". Run
   `python3 engine/scripts/recommend_skills.py --workspace W --text "<their words>" [--role <id>] [--goal <id>]`
   and show every skill: fit, why, **what it does, example questions it answers (`triggers` in `catalog/people.json`) and the data it needs**. Recommended skills are pre-selected; suggest starting with one or two.
3. **No database question.** Say "I'll look in all N databases your role can see", and offer to leave some out or add others.
4. **Context sources:** show `sources.json`. For each source the analyst wants, ask for the scope with an example and record consent (`context_store.py consent --context W/context.json --connector confluence --scope PEOPLE`). Local tools use the analyst's own login; say so in one line. Always offer "upload doc exports" (`--connector local_files --scope <folder>`).
5. **Scan:** `python3 engine/scripts/discover_topics.py scan --workspace W --connection C --skill S1 [--skill S2] [--exclude DB] [--database EXTRA_DB]`.
   Without `--database` it covers every visible database. Usage is a bounded sample: ACCESS_HISTORY object lists for candidate tables over 7 days (30 if sparse), then the user's own recent queries as structure only, otherwise names and columns only. Every data need of the chosen skills is scored against every scanned table on what it holds (required fields), name hints, comments and usage, so a table is found even when its name is unexpected, and one table may serve several needs. Summarize from `topics.json`:
   - per database (`database_summary`): HR tables found and in which schemas, or "nothing relevant";
   - how the ranking was done (`usage`), in one line;
   - per skill: how many of its data needs have a table. If nothing is visible at all, say "no HR data visible to role X; try another role".

## Stage 2 · Bind (automatic, no questions)

1. `python3 engine/scripts/bind.py --workspace W --connection C --skill S1 [--skill S2] [--context-docs <folder> --context-connector local_files]`
   On a repeat run it first checks saved tables for changes (columns/types); changed tables become `stale` instead of everything being re-scanned. Then it binds, writes the mapping review and rebuilds `profile.json`. Report progress from `W/progress.jsonl`.
2. For each consented MCP source, follow `subskills/discover_context.md`, then run `discover.py --workspace W --rematch` and `profile_store.py build --workspace W`.
3. **Mapping review, per skill** (from `W/mapping_review.json`): list the tables the skill uses and, for each data need, the table, its key columns and why it was picked (`why`). Put `needs_input` first ("we couldn't find hiring manager survey data; closest is X, but it has no score column"), then `check` items (a field we're unsure of). Ask: "Does this look right? Tell me if a table should be different." A correction in their words ("use HM_FEEDBACK_SURVEY for hiring manager sentiment") goes to `fix_ledger.py feedback --workspace W --skill S --text "<their words>"`. It becomes a `bind_table` proposal, checked first: the table must exist, must not be a dev/test copy, and must have the columns the need requires. Otherwise say which are missing. Apply with `fix_ledger.py apply --workspace W --id I --connection C`; the table is profiled if needed and that need is re-matched.
4. Then report from `profile_store.py summary --workspace W --skill S…`: **settings we assumed** and **still open**. From `W/definition_inputs.json` (`summary`), give one line per business definition: found (and how), found but looks off, or "needs your input" (asked in Tune). Never say "cannot compute" before Tune has asked. Per skill, from `bind_delta.json`: reused, new, missing.
5. **Data-connection check, per skill** (from `W/binding_gate.json`, written by `bind.py`; re-run alone with `python3 engine/scripts/eval_gate.py --workspace W --skill S…` after any binding fix). Say it in plain words, never the grade codes:
   - EQUIVALENT → "Everything this skill needs is connected."
   - DEGRADED → "Connected; a few things are missing: …" (the titles of the failed rules).
   - INCOMPATIBLE → "Key data isn't confirmed yet: …" (the failed G rules, with `reason`). The analyst can still continue; the skill is hydrated as partial with this warning unless it's fixed.
   Every failed rule is asked in Tune as one question ("which table holds exits?"); don't ask here.

## Stage 3 · Tune (a few questions, one review per skill, then its golden cases)

1. **Prepare while you talk:** `python3 engine/scripts/preview.py --workspace W --connection C --skill S…`, and in the background the assistant's suggestions (cached; skip for a fast or fully predictable run): `python3 engine/scripts/definitions.py suggest --workspace W --connection C --skill S…`.
2. **Questions:** `python3 engine/scripts/questions.py plan --workspace W --connection C --skill S…`. The questions are generated for these skills and this data. They are not a fixed list:
   - **Settled, not asked** (`decided`, say them in one line): labels the data states clearly (e.g. TERM_TYPE Voluntary/Involuntary), definitions with only one way to compute them, answers from documents or earlier skills, and conventions where every option gives about the same number.
   - **Asked** (`ask`, already ranked by how much each answer moves the numbers; never more than 5 per skill, the rest are listed as assumed): conventions with several valid answers (fiscal year, who counts in headcount, attrition method, time to fill/hire) and policies no data can decide (healthy span of control, when a req is aging, whether a req with an accepted offer is still open). Ask them one at a time in business words. Give the preselected answer, why it's asked (`why`) and each option with its number (`effect`, e.g. "120 vs 112 exits", "30 vs 41 days"). Say "suggested for this skill" for `suggested: true`. Numbers (`kind: number`, thresholds) accept any value in range.
   - `kind: "gate"` (a data-connection rule from Bind failed): ask the `prompt` ("Which column holds exit flag?"), show the options (our current guess marked "not certain", close alternatives, "We don't have this") and accept a typed `COLUMN`, `TABLE.COLUMN` or `TABLE`. Record it with `questions.py answer --workspace W --id Q --value '"<their answer>"' --connection C`. The answer becomes an exact, validated change: `confirmed` (they confirmed our guess; nothing else changes), `proposed` (show `changes` and apply only after they agree with `fix_ledger.py apply --workspace W --id <fix> --connection C`), or `unresolved` (say `why` and ask again). Then run `eval_gate.py --workspace W --skill S` and say whether the check now passes. "We don't have this" is recorded; the skill says so instead of estimating.
   - `kind: "locate"` (Bind couldn't find a plausible source): ask where it lives. Show the options (anything found but flagged, with why it looks off; other dates from linked tables) and accept a typed `TABLE.COLUMN` as `--value '{"text": "APPLICATIONS.HIRED_DATE"}'`, which is checked. "We don't track this" is an option. Only after that does it become a `cannot_compute` note, which the hydrated skill states instead of estimating.
   - Record each answer with `questions.py answer --workspace W --id Q --value '<json>'`, or `--skip` ("not sure" is fine). If the answer returns `"rebind": true`, run `bind.py` again before continuing.
3. **Follow-up:** ask `follow_up.prompt` once ("Is there any other rule or context we should know?"). Run each answer through `fix_ledger.py rule --workspace W --skill S --text "<their words>" --ai --connection C`. A `proposed` result is a change (handle it as in step 5). A `noted` result is saved as a customer rule that the hydrated skill follows; say so.
4. **Checks, then review, one per skill:** first run the checks silently: `python3 engine/scripts/check_bindings.py --workspace W --connection C --skill S…`. They verify that queries compile and run, numbers are in range, nothing is double counted, definitions exist in the data, and saved corrections still hold (`A6_corrections`). Unchanged skills are reused (`cached`). Re-run `preview.py` if anything changed. Then show **all** of the skill's metrics together. For each: the number, its one-line definition (`definitions`), the source table, any warnings, and document numbers next to ours where they exist. Put any failed check from `validation_report.json` on the metric it affects, in plain words (e.g. "⚠ the exit flag is empty in your data, so regrettable share can't be trusted"). For a failed `A6_corrections`, quote the correction and today's number and offer to re-apply it. Judge the preview against the skill's behavior standard (`templates/people/evals/<skill>/golden_cases.md`): if the output breaks an expected behavior or a guard check there (e.g. counts transfers as exits, ignores the analyst's fiscal year, shows a group under 5), flag it on the metric it affects and propose the fix as in step 5. Then list "Assumed: …" for the defaults nobody was asked about. For the skill's headline number (the first metric), ask **"Does this match what you report?"** A different number in their words ("we had 321") goes to `fix_ledger.py reconcile --workspace W --skill S --metric M --target N --connection C`, handled as in step 5. Then ask once: **"Anything else look off? Tell me in your own words, or say 'looks good'."**
5. **Feedback** (one message may hold several points): `python3 engine/scripts/fix_ledger.py feedback --workspace W --skill S --text "<their words>" --ai --connection C`.
   - `proposed`: show the `changes`, which skills they affect, the before → after numbers (`effects`) and any `note`. Apply only after the analyst agrees: `fix_ledger.py apply --workspace W --id I --connection C`. The fix is saved as a correction and re-checked later. Then re-run `preview.py`, show what changed, and ask the review question again.
   - `resolved`: say what's already true, or the explanation (e.g. "your 120 includes 6 exits after the as-of date").
   - `unresolved`: keep their words (they are saved) and ask one clarifying question.
   - "looks good": `python3 engine/scripts/review.py done --workspace W --skill S`.
   - Open fix-list items (`fix_ledger.py collect --workspace W`, e.g. a failed check with a proposal) are offered in the same way. After applying, re-run the checks for the affected skills only.
   - **Moving on:** the analyst can always continue. If some skills still have failing checks or an unreviewed preview, say so in one line. It's recorded in the summary, and hard failures affect only that skill.
6. Never change bindings or settings from free text without a validated proposal and the analyst's yes.
7. **Golden cases (last step of Tune, one skill at a time).** The golden cases are the skill's tests. The skill itself never reads them when answering; it follows its `SKILL.md` and customer configuration.
   1. **Draft build:** `python3 engine/scripts/build_customer_plugin.py --workspace W --output W/draft --skill S1 [--skill S2]`. Nothing is activated.
   2. **Plan:** `python3 engine/scripts/evaluate.py plan --workspace W --skill S…` picks up to 3 cases per skill (`--all` for every case) and lists each case's input, expected behavior and guard checks with their definitions from `eval_plan.md`. Say "Testing the skill on your data: N questions".
   3. **Run each case** by acting as the draft skill: follow `W/draft/skills/<skill>/SKILL.md` (with its customer configuration) and `hydration_hints.yaml`, answer the case's **Input** on the analyst's data, aggregates only. Multi-part cases (`parts`) run part by part.
   4. **Score** the answer against **Expected behavior** and **Guard checks**. Numbers in the cases are placeholders or rules (e.g. "rate = exits ÷ average headcount over the same months"): check the rule on the data, never a fixed value. A case whose data isn't available (or that needs more history than the data has) is `not_tested`, not a failure. Record: `evaluate.py record --workspace W --skill S --case N --result pass|fail|not_tested [--failed "<plain words>"] [--note "..."]`.
   5. **Report** one line per case, then `evaluate.py summary --workspace W` per skill (e.g. "2 of 3 tests passed; one failed: tenure breakdown reads Location").
   6. **Fix:** send each failed check to `fix_ledger.py feedback --workspace W --skill S --text "<failed check>" --ai --connection C` and handle the proposal as in step 5 (apply only after the analyst agrees). After a fix, rebuild the draft and run `evaluate.py plan` + the cases again: results from an older build show as `stale`.
   7. **Moving on:** the analyst can always continue. A skill with failed, stale or unfinished cases is hydrated as **partial** with that reason.

## Stage 4 · Hydrate

1. `python3 engine/scripts/build_customer_plugin.py --workspace W --output W/customer-people --skill S1 [--skill S2]`. Report each skill as it finishes (`W/hydrate_progress.jsonl`). The build copies each skill's golden cases and eval plan to `W/customer-plugins/people-hydrator/evals/<skill>/`. A skill is **hydrated** when its data connection passed (or works with listed gaps), its checks pass, its golden cases passed (or it has none) and its preview was reviewed. Otherwise it is **partial**, with the reason: key data not confirmed (a warning), failed / stale / unfinished golden cases, failing checks, or a preview nobody reviewed. **Blocked** only when no table at all was found for required data.
2. Show the summary from `hydration_summary.json`. Per skill: **hydrated / partial / blocked** with the reason, whether its preview was reviewed (`reviewed`), its golden-case result (`evaluation`, e.g. "3 of 3 tests passed"), what it can now help with (`can_answer`) and how to use it (`invoke`). Mention the profile version it was built from.
3. Activation: `cortex plugin add W/customer-people`, then `/plugin reload`.
4. Offer to **add more skills**: go back to Stage 1 and pass **only the new skills** (`--skill NEW1 --skill NEW2`) to every stage. Skills hydrated before are kept automatically: their tables, decisions, cached checks and golden-case results are reused, and only new tables are profiled and only new questions are asked. Don't re-preview, re-ask or re-run cases for an earlier skill unless the summary marks it `updated (its data or definitions changed)` (its `change` field); then say which skill changed and re-run only its stale cases.

Hydrated skills query the bound tables directly. Each hydrated `SKILL.md` starts its run with a **data-connection check** (one metadata query per database): if a bound table or column is gone, the skill stops and asks the user to re-hydrate it here. Re-hydrating starts from the saved profile, re-connects only that skill, and re-runs its checks and golden cases.

## Safety rules
- Read-only: metadata, aggregates, and code values for low-cardinality columns that are not IDs, names, emails or free text.
- Usage comes from object lists (ACCESS_HISTORY) for candidate tables; if query text is needed, it's reduced to structure (tables, joins, filters, group-bys), literals are stripped, and raw text is never stored.
- Business context needs consent per source and scope. Items are redacted (names, emails, IDs, mentions, phones) and cited, and they never override an analyst's decision.
- Free-text feedback can be interpreted with Snowflake Cortex (`AI_COMPLETE`) in the customer's own account. The prompt holds only setting names, current values, code values and column names, never rows. Every proposal is validated before it can be applied.
- Breakdowns with fewer than 5 people are suppressed. The validator and previews run aggregate queries only; nothing person-level is returned or written.
- Never create or alter objects in the customer's account. The hydrator only reads; its only output is the plugin in the workspace.
