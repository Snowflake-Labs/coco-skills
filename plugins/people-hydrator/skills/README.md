# People skills (dehydrated)

Nine People Analytics and Talent Acquisition skills: `attrition_insights`, `headcount_insights`, `good_morning_chro`, `good_morning_recruiting`, `hiring_brief`, `org_health_report`, `interview_debrief`, `offer_builder` and `job_description_builder`. See the [People README](../README.md) for what each one does, who it's for and how they fit together.

They are **dehydrated**: no tables, columns, definitions or thresholds are filled in. To run them on your data, set them up with **People Hydrator** (`plugins/people-hydrator/`), which connects each skill to your tables and the definitions you confirm. These are the same templates it uses.

Each skill folder has:
- `SKILL.md`: an "About this skill" summary at the top, then the workflow, guard rails and data requirements.
- `queries.md`: the queries the skill runs, written against generic field names that People Hydrator maps to your columns.

Each skill's tests (`eval_plan.md` and `golden_cases.md`) are in `plugins/people-hydrator/evals/<skill>/`. People Hydrator checks your data connection against the eval plan, runs the golden cases on your data before it marks a skill ready, and copies both into your set-up plugin under `evals/<skill>/` so you can run them again later.

## Readiness check

`org_health_precheck` is not one of the dehydrated skills: it runs as-is, before setup, in CoWork or Cortex Code, and is not set up by People Hydrator. It answers aggregate questions (counts, dates, yes / no) about the data `org_health_report` needs, through a people-data agent or read-only queries, checks how fresh it is and how much history there is, and returns a PASS / WARN / BLOCKED report to send to your Snowflake contact. It has no `queries.md` and no tests in `plugins/people-hydrator/evals/`.

## License

Copyright (c) Snowflake Inc. All rights reserved.
Licensed under the Snowflake Skills License [LICENSE.txt](../../LICENSE.txt).
