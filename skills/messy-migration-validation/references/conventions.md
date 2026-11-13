# Conventions and Standards

## Personas

- **Use Cases 1–2 (Profile, Baseline Model):** Typically run by an architect or data engineer in CLI (Cortex Code). Produces foundational artifacts.
- **Use Cases 3–4 (Compare, Deep Dive):** Typically run by a business analyst in Snowsight. Consumes foundational artifacts.

Sub-skills inherit this context. Do not enforce persona as a hard gate.

## Naming Conventions

### Table References
- Always use fully qualified names: `DATABASE.SCHEMA.TABLE`
- Case insensitive in SQL, but store as UPPER in artifacts

### Artifact Naming (Shared Workspace)

Files produced in the shared workspace (primarily by Persona 2 — BA/Snowsight) must use the following convention to prevent overwrites in multi-user environments:

```
<artifact_type>_<mart_short_name>_<user>_YYMMDDHH.<ext>
```

**Components:**
- `<artifact_type>`: One of `profile`, `comparison`, `rca`, `test_log`, `checkpoint`, `handoff`
- Handoff files also carry the use case so two use cases finished in the same hour do not collide: `handoff_<mart_short_name>_<user>_<uc>_YYMMDDHH.md` (e.g. `handoff_customer_mart_jdoe_uc2_2604101000.md`)
- `<mart_short_name>`: Derived from mart name (see below)
- `<user>`: First initial + full last name, lowercased, derived from `CURRENT_USER()`. Extract using:
  ```sql
  SELECT CASE WHEN CURRENT_USER() RLIKE '.*[._].*'
              THEN LOWER(LEFT(CURRENT_USER(), 1) || REGEXP_SUBSTR(CURRENT_USER(), '[._]([^@._]+)', 1, 1, 'e'))
              ELSE LOWER(CURRENT_USER()) END AS user_key;
  ```
  Examples: `john.smith@company.com` → `jsmith`, `jane_doe@company.com` → `jdoe`, `ANALYST1` → `analyst1` (no separator: whole name, lowercased).
- `YYMMDDHH`: 2-digit year, month, day, hour (24h) — e.g. `2604091400`

**Examples:**
- `comparison_revenue_mart_jsmith_2604091400.md`
- `profile_customer_mart_jdoe_2604101000.md`
- `rca_revenue_mart_east_2604091400.md`

**Exceptions — architect artifacts (Persona 1):**
Mapping files and semantic views are created by the architect in CLI and do not require multi-user collision protection. These use:
- `mappings/{mart-name}-mapping.yaml`
- `semantic-views/{mart-name}.yaml` — optional mart-only semantic view (UC1)
- `semantic-views/{mart-name}-sv.yaml` — validation semantic view joining mart and baseline (UC2; consumed by UC3 and UC4)

### mart-name Derivation
- Lowercase the table name, replace underscores with hyphens
- Example: `CUSTOMER_MART` → `customer-mart`
- For `<mart_short_name>` in the naming convention, use underscores instead: `customer_mart`

### MART_NAME and GOLDEN_SOURCE in Results Tables
- In `PROFILE_RESULTS`, `COMPARISON_RESULTS`, and `VALIDATION_TEST_LOG`, store `MART_NAME` and `GOLDEN_SOURCE` as the **fully qualified, uppercase** name (e.g. `ANALYTICS_DB.MARTS.CUSTOMER_MART`).
- Rows written by earlier versions may use the short table name. When reading, match both: `WHERE MART_NAME IN ('{{MART_FQN}}', '{{MART_TABLE}}')`.

## Schema Standards

### PROFILE_RESULTS Table
```sql
CREATE TABLE IF NOT EXISTS {{DB}}.{{SCHEMA}}.PROFILE_RESULTS (
    RUN_ID          VARCHAR,
    MART_NAME       VARCHAR,
    COLUMN_NAME     VARCHAR,
    ORDINAL_POSITION NUMBER,
    DATA_TYPE       VARCHAR,
    IS_NULLABLE     VARCHAR(3),
    TOTAL_ROW_COUNT NUMBER,
    DISTINCT_COUNT  NUMBER,
    DISTINCT_PCT    NUMBER(38,4),
    NULL_COUNT      NUMBER,
    NULL_PCT        NUMBER(38,4),
    MIN_VALUE       VARCHAR,
    MAX_VALUE       VARCHAR,
    AVG_VALUE       NUMBER(38,6),
    MEDIAN_VALUE    NUMBER(38,6),
    STDDEV_VALUE    NUMBER(38,6),
    HASH_AGG        VARCHAR,
    PROFILED_AT     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    JOB_NAME        VARCHAR
);
```

### COMPARISON_RESULTS Table
```sql
CREATE TABLE IF NOT EXISTS {{DB}}.{{SCHEMA}}.COMPARISON_RESULTS (
    RUN_ID              VARCHAR,
    MART_NAME           VARCHAR,
    GOLDEN_SOURCE       VARCHAR,
    COMPARISON_TYPE     VARCHAR,
    DIMENSION_KEY       VARCHAR,
    COLUMN_NAME         VARCHAR,
    MART_VALUE          VARCHAR,
    GOLDEN_VALUE        VARCHAR,
    DIFFERENCE          VARCHAR,
    STATUS              VARCHAR,
    COMPARED_AT         TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);
```

### VALIDATION_TEST_LOG Table
```sql
CREATE TABLE IF NOT EXISTS {{DB}}.{{SCHEMA}}.VALIDATION_TEST_LOG (
    TEST_ID          VARCHAR DEFAULT UUID_STRING(),
    MART_NAME        VARCHAR,
    GOLDEN_SOURCE    VARCHAR,
    TEST_TYPE        VARCHAR,
    TEST_DESCRIPTION VARCHAR,
    SQL_EXECUTED     VARCHAR,
    RESULT_SUMMARY   VARCHAR,
    STATUS           VARCHAR,
    ANALYST_NOTES    VARCHAR,
    CREATED_AT       TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CREATED_BY       VARCHAR DEFAULT CURRENT_USER()
);
```

### COMPARISON_RESULTS Values

| Column | Allowed values |
|--------|----------------|
| `COMPARISON_TYPE` | `row_count`, `row_count_by_dimension`, `aggregate_sum`, `hash_agg_matched_keys`, `key_match`, `row_diff_matched_keys`, `match_score` |
| `DIMENSION_KEY` | `ALL`, or `COL=value` pairs joined with `\|` (e.g. `BILLING_PERIOD=FY2025-Q2\|REGION_CODE=SE`) |
| `STATUS` | `MATCH`, `MISMATCH`, `WITHIN_TOLERANCE`, `EXPECTED` (divergence classified expected / by design) |

## Logging to VALIDATION_TEST_LOG

Every sub-skill's "Log to Testing Record" step uses this procedure.

1. Insert the entry. Snowflake does not support `INSERT ... RETURNING`; insert, then read back the TEST_ID:

```sql
INSERT INTO {{DB}}.{{SCHEMA}}.VALIDATION_TEST_LOG
    (MART_NAME, GOLDEN_SOURCE, TEST_TYPE, TEST_DESCRIPTION, SQL_EXECUTED, RESULT_SUMMARY, STATUS, ANALYST_NOTES)
SELECT '{{MART_FQN}}', {{GOLDEN_FQN_OR_NULL}}, '{{TEST_TYPE}}', '{{TEST_DESCRIPTION}}',
       '{{SQL_SUMMARY}}', '{{RESULT_SUMMARY}}', '{{STATUS}}', '{{ANALYST_NOTES}}';

SELECT TEST_ID, CREATED_AT
FROM {{DB}}.{{SCHEMA}}.VALIDATION_TEST_LOG
WHERE MART_NAME = '{{MART_FQN}}' AND TEST_TYPE = '{{TEST_TYPE}}' AND CREATED_BY = CURRENT_USER()
ORDER BY CREATED_AT DESC
LIMIT 1;
```

`STATUS` is one of `pass`, `fail`, `warning`, `info`. Escape single quotes in free text (`''`).

2. Append the same entry to the local log `validation-workspace/testing-records/{mart-name}-test-log.md` (dual persistence is mandatory) using this block:

```markdown
## Test: {TEST_TYPE} — {TIMESTAMP}

**TEST_ID**: {TEST_ID}
**Status**: {STATUS}
**Description**: {TEST_DESCRIPTION}
**Result**: {RESULT_SUMMARY}

<details>
<summary>SQL Executed</summary>

{SQL_EXECUTED}

</details>

**Analyst Notes**: {NOTES}

---
```

Create the file with the Artifact Metadata Header if it does not exist.

## Presenting Large Lists

When presenting more than ~10 items for analyst confirmation (column inventories, column mappings, classification lists), render them as a table (one row per item) rather than inline prose, so every item is visible and can be referenced by number.

## Ad-Hoc SQL Convention

Before writing any ad-hoc SQL that involves a JOIN, confirm that column data types are known for both sides. Run `DESCRIBE TABLE` if the schema has not already been captured (e.g. from a prior profiling run, mapping YAML, or earlier DESCRIBE in the same session). This prevents implicit casting issues, silent type mismatches, and incorrect join results. Do not re-describe tables whose schema is already in context.

## Artifact Metadata Header

Every generated file must include a metadata header block at the top:

```
---
user: <CURRENT_USER()>
role: <CURRENT_ROLE()>
mart: <fully qualified mart name>
timestamp: <YYYY-MM-DD HH:MI:SS>
session_id: <if available>
---
```

## Workspace Layout
```
validation-workspace/
├── profiles/
├── mappings/
├── semantic-views/
├── comparisons/
├── deep-dives/
└── testing-records/
```

## Session Hygiene

At every **[STOP]** point and major step completion, emit a one-line session hygiene reminder. Format:

```
🔖 Milestone: <step name> complete. Checkpoint updated. [n] steps remaining in this use case.
```

Purpose: keeps the analyst oriented in long workflows, signals that progress is being saved (per the Session Continuation strategy above), and nudges proactive session resets if context feels stale.

Rules:
- One line only — do not interrupt flow with multi-line summaries
- Include the count of remaining steps in the current use case
- For CLI (UC1-2): reference the handoff summary that will be generated at use case completion
- For Snowsight (UC3-4): the checkpoint file is updated automatically at each [STOP]
- If the session feels tangled or the analyst reports unexpected behavior, suggest saving and restarting: "Consider saving your checkpoint and starting a fresh session — context resets help."

## Session Continuation

Long validation workflows span multiple use cases and sessions. To enable reliable resume, every sub-skill produces continuation artifacts at key milestones.

### CLI (Architect — UC1, UC2): Handoff Summary

At the end of each use case, auto-generate a handoff summary at `validation-workspace/handoff_<mart_short_name>_<user>_YYMMDDHH.md`:

```markdown
---
user: <CURRENT_USER()>
role: <CURRENT_ROLE()>
mart: <fully qualified mart name>
timestamp: <YYYY-MM-DD HH:MI:SS>
use_case_completed: <UC1 | UC2>
---

## Completed Steps
- <list of steps completed with key outcomes>

## Artifacts Produced
- <path or fully qualified name for each artifact>

## Pending Items
- <anything deferred or flagged for next session>

## Key Decisions
- <analyst decisions that affect downstream work>
```

Generate this automatically after the final [STOP] of each use case. Do not ask — just produce it.

### Snowsight (BA — UC3, UC4, UC5): Session Checkpoint

#### Checkpoint Initialization (Mandatory)

Every Snowsight sub-skill (UC3, UC4, UC5) **must create the checkpoint file immediately after Step 1 context gathering completes** — before the first [STOP] point. This ensures a resumable record exists from the very start of the session, even if the session is interrupted before reaching the first [STOP].

The initial checkpoint should contain:
- User, role, mart, semantic view (if known)
- `current_step`: "Step 1 — Context gathered"
- `findings_so_far`: empty list
- `next_action`: the next step in the workflow

#### Checkpoint Updates (Mandatory at Every [STOP])

At each [STOP] boundary, update the existing checkpoint file. This is not optional — every [STOP] must be accompanied by a checkpoint write. The sub-skill instructions reinforce this at each [STOP] with a directive: **"Update checkpoint."**

Checkpoint file location: `validation-workspace/checkpoint_<mart_short_name>_<user>_YYMMDDHH.yaml`

```yaml
checkpoint:
  user: <CURRENT_USER()>
  role: <CURRENT_ROLE()>
  mart: <fully qualified mart name>
  semantic_view: <fully qualified semantic view name>
  timestamp: <YYYY-MM-DD HH:MI:SS>
  use_case: <UC3 | UC4 | UC5>
  current_step: <step number and name>
  group_by_columns: <list or "none">
  column_scope: <list or "all">
  findings_so_far:
    - <summary of each completed step's outcome>
  divergence_classifications:
    - scope: <column | row_segment>
      column: <name, when scope = column>
      segment: <description, when scope = row_segment, e.g. "mart-only keys, REGION_CODE=SE, FY2025-Q2">
      rows: <affected row count>
      classification: <unexpected | expected | unsure>
  next_action: <what to do next>
```

Update the checkpoint after each [STOP] — overwrite the same file (the timestamp in the filename scopes it to the session). If the session resets, a new file is created with the new timestamp.

### Resume Protocol

When a user selects "Resume session" (router option 6):
1. Scan `validation-workspace/` for `checkpoint_*` and `handoff_*` files matching the current user
2. Sort by `YYMMDDHH` descending — present the most recent
3. Summarize: "Last session: [date], completed [X], next step is [Y]. Continue?"
4. If the analyst confirms, load the appropriate sub-skill and resume from `next_action`
5. If no checkpoint found, inform the user and offer to start fresh

## File Generation Prompts

After every file generation (report, checkpoint, handoff, YAML), emit a clear confirmation:

```
📄 File created: <filename>
   Location: <full path or stage path>
   Type: <artifact_type>
```

For Snowsight users generating to a named stage:
- Offer `@VALIDATION_ARTIFACTS/` as a default stage location
- Provide the stage path and a `GET` or `LIST @stage` command for verification

### List Generated Artifacts

At any point, the analyst can ask to list artifacts from the current session. Respond with:

```
Artifacts generated this session:
1. <artifact_type> — <filename> — <path>
2. ...
```

Filter by user (default: current user) or mart if requested.

## Error Handling

When SQL execution fails or an unexpected condition arises, follow these guidelines:

| Error | Likely Cause | Recovery |
|-------|-------------|----------|
| Table/view not found | Typo, wrong schema, or object not yet created | Confirm fully qualified name with analyst. Run `SHOW TABLES` or `SHOW VIEWS` in the expected schema. |
| Permission denied | Current role lacks SELECT/INSERT/CREATE | Report the error to the analyst. Do not attempt to GRANT or change roles. |
| CREATE TABLE/VIEW fails | Schema doesn't exist or insufficient privileges | Ask analyst to confirm the target schema exists and the role has CREATE privileges. |
| Semantic view deployment fails | Malformed YAML or function not available | Present the error message. Verify YAML syntax. Ask analyst to check account feature enablement. |
| Query timeout | Table too large or warehouse too small | Add LIMIT, narrow filters, or ask analyst to use a larger warehouse. |
| HASH_AGG returns NULL | Column is entirely NULL | Note in results as "all NULL — no hash possible." Not an error. |
| INSERT into results table returns 0 rows | RUN_ID mismatch or empty source query | Re-check the source query independently. Verify RUN_ID was set correctly. |
| Mid-workflow interruption | Context loss, session reset | Check for the most recent checkpoint or handoff file in `validation-workspace/`. Resume from the last completed step. |

General rules:
- Never silently skip a failed query — always surface the error to the analyst
- Never retry a failing DDL with modified logic without analyst approval
- If a query fails and the cause is unclear, present the full error message and ask for guidance
