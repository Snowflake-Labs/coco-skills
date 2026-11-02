# People skill evals

One folder per skill, two files each:

- `eval_plan.md` — what correct data looks like for the skill: the data needs it must be connected to and the rules the hydrator grades the connection against during setup.
- `golden_cases.md` — test questions with the expected behavior. People Hydrator runs them on your data before it marks a skill ready; numbers in them are rules or placeholders, checked against your own data.

The skills never read these files when answering. They are tests.

People Hydrator includes the same files, so it can run them on your data during setup.

## License

Copyright (c) Snowflake Inc. All rights reserved.
Licensed under the Snowflake Skills License [LICENSE.txt](../../LICENSE.txt).
