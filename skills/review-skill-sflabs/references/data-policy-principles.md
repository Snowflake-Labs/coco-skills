# Data Policy Principles for Skills

> Source of truth for the `data-policy-scan` workflow. When this file changes, the workflow's behavior changes — no other file needs to update.

These principles apply to any skill that surfaces data, recommendations, or assessments to a user. They are domain-agnostic — every check below is engineering hygiene that holds regardless of which platform, tool, or audience the skill targets.

## 1. Never expose PII in skill output

A skill must not instruct an agent to surface personally identifiable information in its output, even if the user asks for it.

Field types to flag if a skill's body or example output references them:

| Field type | Examples |
|---|---|
| User identity | `USER_NAME`, `LOGIN_NAME`, `EMAIL`, `DISPLAY_NAME`, given name, surname |
| Contact information | phone numbers, postal addresses |
| Network identity | `CLIENT_IP`, `SOURCE_IP`, MAC addresses |
| Activity tied to identity | login history rows, access history with user joins, query history with user joins, raw audit log entries |
| Authentication artifacts | SSO identifiers, MFA enrollment details, session tokens |

If a user genuinely needs PII, the skill should help them write the query to run in their own environment — never deliver PII as output.

## 2. Aggregate over individual

When telemetry is needed, prefer aggregate metrics over per-user breakdowns.

| Prefer | Avoid |
|---|---|
| `427 active users, 38 dormant > 90 days` | A list of those 38 users by name or email |
| `Top warehouse by spend: ANALYTICS_WH, 1,247 credits` | `jane.doe@example.com consumed 847 credits` |
| `3 admin roles not reviewed in 180 days` | Naming the users holding those roles |
| Per-warehouse / per-role / per-service breakdowns | Per-user breakdowns |

If the natural answer requires per-user data, the skill should produce SQL the user can run themselves — not surface the result.

## 3. No customer-specific hardcoding

A skill must accept the things that vary between users as parameters, not bake them into the body. Hardcoded specifics produce two problems: the skill leaks identifiers it should not, and users fork the skill to override the values.

Patterns to flag:

- Account locators or specific account names embedded in SQL or examples
- Specific schema names, table names, or role names assumed without parameterization
- Specific integration names, warehouse names, or database names baked in
- Credentials, API tokens, secrets, or anything that looks like a key (always treat as a hard error, not just an advisory)

If the skill targets a specific use case, the use case should be selectable — not the only mode.

## 4. Advisory tone

A skill is helping someone improve. Its tone should make the user want to take action, not feel defensive.

Avoid these words. Each row gives the recommended replacement:

| Avoid | Why | Use instead |
|---|---|---|
| Critical | Implies imminent failure | Needs attention, priority recommendation |
| High-risk | Triggers fear or blame | Opportunity for improvement, needs attention |
| Danger | Signals alarm | Not recommended, below best practice |
| Failure | Implies something is broken | Underperforming, below threshold |
| Violation | Sounds like compliance enforcement | Deviation from best practice |
| Urgent | Creates unwarranted pressure | Recommended action |

Do not paraphrase findings into harsher language than the rubric calls for. A finding is a coaching opportunity.

## 5. Color discipline

Skills that use status indicators (in tables, summaries, or any rendered output) must use only these three colors:

| Color | Label | Meaning |
|---|---|---|
| 🟢 Green | On Track | Meets or exceeds expectations. No action needed. |
| 🟡 Yellow | Needs Improvement | Below optimal. Worth addressing, but not urgent. |
| 🟠 Amber | Needs Attention | Requires attention. Should be prioritized. |

**Do not use 🔴 red.** Red implies an emergency that almost no skill output is actually communicating. Tone is advisory, not reactive.

## 6. Disclaimer on assessments

Any skill output that surfaces telemetry, recommendations, or an assessment must include a disclaimer near the top. The disclaimer should establish that the output is a guide, not a definitive audit.

Suggested templates by category:

- **General:** *This report is intended to help you evaluate against a set of recommended best practices; it is not a comprehensive audit.*
- **Security:** *This report is intended to help you evaluate against a set of recommended security best practices; it is not a comprehensive audit. You are responsible for securing your environment and determining your compliance requirements.*
- **Reliability:** *This report is intended to help you evaluate against a set of recommended reliability best practices; it is not a comprehensive audit. You are responsible for assessing reliability gaps within your environment.*
- **Performance:** *This report is intended to help you evaluate against a set of recommended performance best practices; it is not a comprehensive audit. You are responsible for assessing the performance of your environment.*
- **Cost optimization:** *This report is intended to help you evaluate against a set of recommended cost optimization best practices; it is not a comprehensive audit. You are responsible for assessing optimization opportunities within your environment.*
- **Operational excellence:** *This report is intended to help you evaluate against a set of recommended operational excellence best practices; it is not a comprehensive audit. You are responsible for identifying gaps in observability within your environment.*

A skill should pick the single most relevant template and include it once near the top of its output. Do not stack multiple disclaimers.

## 7. Flexibility is a feature

When a skill does not accommodate common variations in how people use it, users copy the skill and edit it to fit their case. Forks bypass review and drift over time.

Ask, when reviewing a skill:

| Question | When the answer is "yes," design for it |
|---|---|
| Will users need to filter by subset (env, region, line of business)? | Build filtering as a parameter, not a hardcoded assumption |
| Will users need different scopes (single target vs. many)? | Accept a scope parameter |
| Will users need different output detail (summary vs. deep-dive)? | Offer output modes |

A skill that hardcodes the answer to any of these is a fork waiting to happen. Flag it as `🟡 Opportunity for Improvement`.
