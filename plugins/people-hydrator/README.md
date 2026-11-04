# People Hydrator (`people-hydrator`)

> [!WARNING]
> **For internal use only. These skills process personally identifiable information (PII).**
>
> - Once set up on your data, these skills work with employee and candidate PII, such as names, compensation, performance, interview feedback, time off and exits. They are intended only for internal use by authorized HR, People Analytics and Talent Acquisition staff within your organization.
> - **These skills do NOT anonymize PII.** Some skills limit their outputs (see [Built-in output limits](#built-in-output-limits)), but others return named individuals and their sensitive data. The limits are not a substitute for access control.
> - **Do NOT expose the results to the public internet**, publish them, or share them outside the people authorized to see that data.
> - **It is the responsibility of whoever deploys these skills** to make sure their use complies with all applicable local laws and regulations on processing personal data (for example GDPR, CCPA/CPRA and local employment and data-protection laws), and with your organization's own privacy, access-control and data-retention policies.
> - **These skills are decision support, not decision makers, and not legal advice.** A qualified person must review every output before it informs a decision about a candidate or employee. See [Legal and compliance](#legal-and-compliance).

People Hydrator sets up the dehydrated People skills on **your own** HR data in Snowflake. You describe what you want to achieve. It recommends skills, finds the right tables across the databases you can see, shows you each skill's real output, asks only the few business questions it can't settle, turns your own feedback into checked changes, verifies each skill, and hydrates it so it runs on your data. What it learns is saved as your **customer profile**, so adding a skill later only processes what's new.

## Who it's for
The person setting up the People skills for their company, usually a **People Analytics analyst** or HR data lead. You don't need to write code or SQL: you answer questions in chat and review real numbers. You need read access to the HR data in Snowflake. The people who later *use* the skills (CHRO, HR Business Partners, recruiters) don't run People Hydrator.

## What to download
| You want | Use |
|---|---|
| Guided setup of the People skills on your data | This plugin: `plugins/people-hydrator/` |
| The dehydrated skill templates on their own | `plugins/people-hydrator/skills/` and their tests in `plugins/people-hydrator/evals/` (the templates here are generated from them) |

## The flow
| Stage | What happens | Your input |
|---|---|---|
| Welcome | What hydration is, the skills and the data they need; set up one or many, come back to add more. Connection checks run meanwhile. | none |
| 1 Discover | Describe what you hope to achieve → recommended skills with what each does and example questions (all skills stay visible); we look in every database your role can see (leave some out if you like) and rank tables with a short sample of recent usage; connect context sources (connected tools or exported docs) | goals, skills, sources |
| 2 Bind | Automatic: tables per data need (found by what they hold), profiling of those tables only, field and value mapping, draft definitions; saves your customer profile. Then a mapping review per skill (which table serves what; say "use X for Y" to change it) and a data-connection check against the skill's eval plan: everything connected, connected with gaps, or key data not confirmed | corrections, if any |
| 3 Tune | Up to 5 questions per skill, generated for your skills and data: definitions with several valid answers, thresholds/policies, and "which table holds X?" for anything the data-connection check couldn't confirm; each option with its number; what the data or your documents settle is stated, not asked. Then all of each skill's metrics with their definitions and automatic checks, "does this match what you report?", and "anything look off?" in your own words → a proposed change with before → after, saved as a correction. Last, the skill's golden cases (test questions) run on your data | a few answers, your feedback |
| 4 Hydrate | Hydrates each skill with progress; summary per skill (hydrated / partial / blocked + reason, preview reviewed or not, test results, what it now helps with) and how to use it. Each hydrated skill checks its data connection before answering and asks you to re-hydrate if a table or column is gone | none |

Adding skills later reuses your customer profile; only new tables are profiled and only new questions are asked. If a table you rely on changes (columns, code values), it's flagged for a quick re-check instead of re-scanning everything.

## If a skill comes out partial or blocked
The summary at the end says, per skill, **hydrated**, **partial** or **blocked**, with the reason.
- **Hydrated:** the data connection, checks and test questions passed and you reviewed the preview. Ready to use.
- **Partial:** the skill works, but something wasn't confirmed: key data not confirmed, a failed or unfinished test question, a failing check, or a preview you haven't reviewed. Fix what the reason names (answer the question, point it to the right table, or review the preview), then say "re-hydrate [skill]".
- **Blocked:** no table was found for data the skill requires. Connect or grant access to that data, or check the database names, and run People Hydrator again.
- **Missing data you don't have:** answer "We don't have this"; the skill then says that section isn't available instead of guessing.

You can always stop and come back: your answers and corrections are saved in your workspace, so the next session continues from where you left off.

## Two ways to run
| You have | Experience | Start |
|---|---|---|
| Snowsight (Cortex Code in the browser) | **Chat**, guided through the stages | Upload `skills/people_hydrator/` to `.snowflake/cortex/skills/people_hydrator/` in your workspace, then say "Open People Hydrator" |
| Cortex Code on your computer | **Chat**, guided through the stages | `cortex plugin add <path>/people-hydrator`, then say "Open People Hydrator" |

The engine detects where it runs: in Snowsight it uses your session; locally it uses your Snowflake CLI connection (`snow`).

https://github.com/user-attachments/assets/9c2f98c2-456e-4bab-9478-cdbf87bf5977

**Requirements:** Python 3.10+ (the engine uses the standard library only). Local runs need the Snowflake CLI with a connection to the account that holds the HR data.

## Layout
```
skills/people_hydrator/
  SKILL.md                    Chat flow + workspace contracts
  catalog/people.json         Dehydrated skill catalog (data needs, tunables, context terms)
  catalog/goals.json          Roles and goals for the skill recommendation
  catalog/definitions.json    Business definitions the skills rely on; asked only when your data offers a real choice
  catalog/connectors.json     Context-source registry (detection, scopes, risk, forbidden scopes)
  subskills/discover_context.md  Playbook for extracting context from connected tools
  engine/scripts/             Engine (standard library only)
  engine/tests/               Unit tests, synthetic fixtures only
  templates/people/skills/    Dehydrated skill templates (generated; do not edit)
  templates/people/evals/     Per skill: eval_plan.md (data-connection rules) and golden_cases.md (tests)
```

## Tests
`python3 -m pytest skills/people_hydrator/engine/tests` (synthetic data only; no account access).

## Legal and compliance
These skills are provided as tools. They are not legal advice, and using them does not make a process compliant. Before you deploy them, have your Legal, Privacy and HR teams review how they will be used in each country, state and city where you hire or employ people.

- **Human review is required.** No skill makes, or should be used to make, a hiring, pay, promotion, performance or termination decision on its own. Outputs are inputs for a qualified person, who stays accountable for the decision.
- **Hiring and automated decision laws.** Some jurisdictions regulate tools that help evaluate candidates or employees, for example New York City Local Law 144 (bias audits and candidate notice), the EU AI Act (employment uses are high-risk), the Colorado AI Act, Illinois laws on AI in employment, and GDPR Article 22. Whoever deploys these skills decides whether those laws apply to their use and is responsible for any required bias audit, impact assessment, notice to candidates or employees, consent, or opt-out.
- **Job postings.** Many jurisdictions require a pay range or other disclosures in job postings (for example Colorado, California, New York, Washington and Illinois, and the EU Pay Transparency Directive), and equal employment opportunity statements are often required or expected. `job_description_builder` drafts content; the recruiter is responsible for making sure each posting meets the laws where the role is located.
- **Pay data.** Many jurisdictions ban asking for or using a candidate's pay history. `offer_builder` never extracts or uses it. Pay equity analysis may need to be run under legal privilege; ask your Legal team before using these skills for it.
- **Employees and works councils.** In some countries (for example Germany and France) introducing tools that analyse employee data may require consultation with a works council or employee representatives, and employees may need to be told how their data is used.
- **Retention.** Your workspace (`~/people_hydrator_ws`) holds your customer profile, corrections and previews. Apply your organization's data-retention policy to it, and delete it when you no longer need it.

## Built-in output limits
Some hydrated skills limit what they return. These limits reduce risk; they do not replace the review above.
- `interview_debrief`: decision support only. No hire / no-hire calls, no candidate ranking or scores; job-related evidence only.
- `offer_builder`: never uses candidate pay history; team pay is shown as aggregates over at least 5 people.
- `org_health_report`: PTO, AI adoption and performance results are aggregates only; groups under 5 people are hidden.
- `job_description_builder`: excludes age, visa / citizenship and protected-characteristic content, and flags biased wording.

Other skills (for example `good_morning_chro` and `good_morning_recruiting`) can return named individuals. Restrict who can run them.

## Privacy and safety during setup
These safeguards cover the setup process. For what hydrated skills return, see [Built-in output limits](#built-in-output-limits).
- **Search and profiling** read metadata, aggregate counts and low-cardinality code values only. They never read names, emails, IDs or free text. Breakdowns with fewer than 5 people are hidden.
- **Recent usage** comes from object lists for candidate tables (last 7 days, 30 if sparse); if query text has to be used, it's reduced to structure such as tables, joins and filters. Query text is never stored.
- **Business context** needs your consent per source and scope. Direct messages, private channels and whole inboxes are refused. Context is redacted when extracted and again when stored, and it never overrides your own answers.
- **Your feedback** can be interpreted with Snowflake Cortex in your own account; it sees setting names, column names and code values only, and every proposed change is checked before you can apply it.
- **Writes:** none to your account. The hydrator only reads; it writes the customer plugin to your workspace.

## License

Copyright (c) Snowflake Inc. All rights reserved.
Licensed under the Snowflake Skills License [LICENSE.txt](LICENSE.txt).
