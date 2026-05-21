# DCR V1 → V2 Migration Mapping Reference

Quick-reference for translating V1 (SAMOOHA Provider/Consumer API) constructs
to V2 (Collaboration API).

---

## Architecture Overview

| | V1 | V2 |
|---|---|---|
| **API schema** | `samooha_by_snowflake_local_db.provider.*` / `consumer.*` | `SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.*` / `REGISTRY.*` |
| **Clean room unit** | Clean room (app package per clean room) | Collaboration (shared compute layer) |
| **Data registration** | `link_datasets` (array of FQNs) | `REGISTRY.REGISTER_DATA_OFFERING` (YAML spec) |
| **Policy enforcement** | `set_join_policy` (TABLE:COLUMN pairs) | `schema_and_template_policies` in offering YAML |
| **Template registration** | `add_custom_sql_template` (raw JinjaSQL string) | `REGISTRY.REGISTER_TEMPLATE` (YAML spec with `template:` body) |
| **Consumer onboarding** | `add_consumers` + `create_or_update_cleanroom_listing` | Declared in `COLLABORATION.INITIALIZE` YAML |
| **Consumer setup** | `consumer.install_cleanroom` | `COLLABORATION.JOIN` |
| **Session requirement** | None | `USE SECONDARY ROLES NONE` before all Collaboration API calls |
| **DB grants** | `library.register_schema` (automatic) | Explicit SAMOOHA_APP_ROLE grants (manual) |

---

## API Call Mapping

### Provider

| V1 Call | V2 Equivalent |
|---|---|
| `samooha_by_snowflake_local_db.library.register_schema([...])` | `GRANT USAGE, SELECT, REFERENCES, REFERENCE_USAGE WITH GRANT OPTION` to `SAMOOHA_APP_ROLE` |
| `samooha_by_snowflake_local_db.provider.cleanroom_init($name, 'EXTERNAL')` | `COLLABORATION.INITIALIZE($$...YAML...$$, '<warehouse>')` |
| `samooha_by_snowflake_local_db.provider.link_datasets($name, [...])` | `REGISTRY.REGISTER_DATA_OFFERING($$...YAML...$$)` |
| `samooha_by_snowflake_local_db.provider.set_join_policy($name, [...])` | `schema_and_template_policies` embedded in `REGISTER_DATA_OFFERING` YAML |
| `samooha_by_snowflake_local_db.provider.add_custom_sql_template($name, 'tmpl', $$...$$)` | `REGISTRY.REGISTER_TEMPLATE($$...YAML...$$)` |
| `samooha_by_snowflake_local_db.provider.add_consumers($name, $locator, $account)` | `collaborator_identifier_aliases` in `COLLABORATION.INITIALIZE` YAML |
| `samooha_by_snowflake_local_db.provider.set_default_release_directive($name, 'V1_0', '0')` | Not needed in V2 |
| `samooha_by_snowflake_local_db.provider.create_or_update_cleanroom_listing($name)` | Implicit in `COLLABORATION.INITIALIZE` |
| `samooha_by_snowflake_local_db.provider.view_cleanrooms()` | `COLLABORATION.VIEW_COLLABORATIONS()` |
| `samooha_by_snowflake_local_db.provider.view_provider_datasets($name)` | `REGISTRY.VIEW_REGISTERED_DATA_OFFERINGS()` |
| `samooha_by_snowflake_local_db.provider.view_join_policy($name)` | Per-column policies in offering YAML |
| `samooha_by_snowflake_local_db.provider.view_added_templates($name)` | `REGISTRY.VIEW_REGISTERED_TEMPLATES()` |
| `samooha_by_snowflake_local_db.provider.view_cleanroom_scan_status($name)` | Not needed — no security scan in V2 |
| `samooha_by_snowflake_local_db.provider.drop_cleanroom($name)` | `COLLABORATION.TEARDOWN($name)` — two-call async |

### Consumer

| V1 Call | V2 Equivalent |
|---|---|
| `samooha_by_snowflake_local_db.library.register_schema([...])` | Explicit SAMOOHA_APP_ROLE grants |
| `samooha_by_snowflake_local_db.consumer.install_cleanroom($name, $provider_locator)` | `COLLABORATION.JOIN($name)` |
| `samooha_by_snowflake_local_db.consumer.link_datasets($name, [...])` | `REGISTRY.REGISTER_DATA_OFFERING` + `COLLABORATION.LINK_LOCAL_DATA_OFFERING($name, $offering_id)` |
| `samooha_by_snowflake_local_db.consumer.set_join_policy($name, [...])` | `schema_and_template_policies` in consumer offering YAML |
| `samooha_by_snowflake_local_db.consumer.run_analysis($name, tmpl, [consumer], [provider], args)` | `COLLABORATION.RUN($name, tmpl_id, [provider], [consumer], args)` — **args 3 and 4 reversed** |
| `samooha_by_snowflake_local_db.consumer.view_cleanrooms()` | `COLLABORATION.VIEW_COLLABORATIONS()` |
| `samooha_by_snowflake_local_db.consumer.is_enabled($name)` | `COLLABORATION.GET_STATUS($name)` |
| `samooha_by_snowflake_local_db.consumer.uninstall_cleanroom($name)` | `COLLABORATION.LEAVE($name)` — two-call async |

---

## Template Conversion Rules

### Pattern Mapping

| V1 JinjaSQL Pattern | V2 Replacement | Reason |
|---|---|---|
| `IDENTIFIER({{ source_table[0] }}) p` | `IDENTIFIER({{ source_table[0] }}) p` | Unchanged |
| `IDENTIFIER({{ my_table[0] }}) c` | `IDENTIFIER({{ my_table[0] }}) c` | Unchanged |
| `IDENTIFIER({{ source_table[N] }})` | `IDENTIFIER({{ source_table[N] }})` | Unchanged for N ≥ 1 |
| `ON IDENTIFIER({{ provider_id \| join_policy }}) = IDENTIFIER({{ consumer_id \| join_policy }})` | `ON p.<join_col> = c.<join_col>` | `join_policy` filter not supported in V2; hardcode column |
| `{{ param }}` | `{{ param }}` | Engine auto-quotes strings; no change needed |
| `'{{ param }}'` | `{{ param }}` | Remove outer quotes — causes double-quoting `''value''` in V2 |
| `{{ param \| sqlsafe }}` | `{{ param }}` | `sqlsafe` is incompatible with V2 Snowpark parameterized binding |

### How the Join Policy Changes

**V1:** The `join_policy` filter validates at runtime that the caller-supplied column reference
(`provider_id`, `consumer_id`) matches the registered join policy. The template body is generic —
it does not name the actual join column.

**V2:** There is no `join_policy` filter. Security is enforced by `allowed_analyses: template_only`
in the data offering (prevents direct data access) and by `schema_and_template_policies` (declares
which columns are available). The template body hardcodes the join column name.

**Migration:** Replace the `join_policy` expression using the column discovered in `view_join_policy`:

```sql
-- V1 template body
ON IDENTIFIER({{ provider_id | join_policy }}) = IDENTIFIER({{ consumer_id | join_policy }})

-- V2 template body (EMAIL_HASH discovered from view_join_policy)
ON p.EMAIL_HASH = c.EMAIL_HASH
```

Also remove `'provider_id'` and `'consumer_id'` from the `COLLABORATION.RUN` `OBJECT_CONSTRUCT()` args.

---

## run_analysis → COLLABORATION.RUN Arg Order

**This is the most error-prone migration step.** Args 3 and 4 are swapped.

**V1 `consumer.run_analysis`:**
```sql
CALL samooha_by_snowflake_local_db.consumer.run_analysis(
    $cleanroom_name,           -- arg 1: clean room name
    'template_name',           -- arg 2: plain template name
    ['consumer_table_fqn'],    -- arg 3: CONSUMER tables → populates my_table[]
    ['provider_table_fqn'],    -- arg 4: PROVIDER tables → populates source_table[]
    object_construct(...)      -- arg 5: includes provider_id, consumer_id, other params
);
```

**V2 `COLLABORATION.RUN`:**
```sql
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.RUN(
    $collab_name,              -- arg 1: collaboration name
    'template_id',             -- arg 2: name_version (e.g., overlap_analysis_YYYYMMDD_V1)
    ['provider_3part_id'],     -- arg 3: PROVIDER offerings → source_tables (REVERSED from V1)
    ['consumer_3part_id'],     -- arg 4: CONSUMER offerings → my_tables   (REVERSED from V1)
    OBJECT_CONSTRUCT(...)      -- arg 5: other params only; no provider_id/consumer_id
);
```

**Migration checklist:**
- Swap arg 3 (V1 consumer) → arg 4 in V2
- Swap arg 4 (V1 provider) → arg 3 in V2
- Change table FQNs to 3-part IDs: `ALIAS.DATA_OFFERING_ID.DATASET_ALIAS`
- Remove `provider_id` and `consumer_id` from `OBJECT_CONSTRUCT`
- Change template name to composite ID: `name_version`

---

## V2 Data Offering YAML Structure

```yaml
api_version: 2.0.0
spec_type: data_offering
name: <offering_name>               # snake_case, e.g., provider_customer_spend_v
version: <YYYYMMDD_V1>             # must match ^[A-Za-z0-9_]{1,20}$ — no dots
description: <text>
datasets:
  - alias: <short_name>             # used as DATASET_ALIAS in 3-part RUN ID
    data_object_fqn: <db.schema.object>
    allowed_analyses: template_only
    schema_and_template_policies:
      <COLUMN_NAME>:
        category: passthrough       # for hashed/synthetic columns
      <DATE_COL>:
        category: timestamp         # for date/time columns
```

Data offering ID (returned by `REGISTER_DATA_OFFERING`) = `name_version`
(e.g., `provider_customer_spend_v_YYYYMMDD_V1`)

---

## V2 Template YAML Structure

```yaml
api_version: 2.0.0
spec_type: template
name: <template_name>               # e.g., overlap_analysis
version: <YYYYMMDD_V1>             # must match ^[A-Za-z0-9_]{1,20}$
type: sql_analysis
description: <text>
template: |
    SELECT ...
    FROM IDENTIFIER({{ source_table[0] }}) p
    JOIN IDENTIFIER({{ my_table[0] }})     c  ON p.<join_col> = c.<join_col>
    WHERE p.COL = {{ param }}              -- bare param; engine auto-quotes strings
    GROUP BY ...
```

Template ID (returned by `REGISTER_TEMPLATE`) = `name_version`
(e.g., `overlap_analysis_YYYYMMDD_V1`)

**Note:** Template versions are immutable. To update a template on a running collaboration:
1. Register a new version with a bumped version string
2. `COLLABORATION.ADD_TEMPLATE_REQUEST(collab, new_template_id, [runner_alias])`
3. Both owner and runner call `COLLABORATION.APPROVE_UPDATE_REQUEST(collab, request_id)`
4. Wait for request to reach COMPLETED status before using the new template ID

---

## V2 COLLABORATION.INITIALIZE YAML Structure

```yaml
api_version: 2.0.0
spec_type: collaboration
name: <COLLABORATION_NAME>
owner: owner_account                # must match an alias below
collaborator_identifier_aliases:
    owner_account: <provider_org.account>    # e.g., SFPSCOGS.WLIN_DCR_AWS_W2
    collab_account: <consumer_org.account>   # e.g., SFPSCOGS.WLIN_AWS_W2
analysis_runners:
    collab_account:                 # consumer/runner alias
        data_providers:
            owner_account:          # provider alias supplies data
                data_offerings:
                    - id: <offering_id_1>   # name_version
                    - id: <offering_id_N>
        templates:
            - id: <template_id_1>           # name_version
            - id: <template_id_N>
```

**Note:** Collaborator list is fixed after `INITIALIZE` — you cannot add or remove
participants later. All parties must be declared upfront.

---

## SAMOOHA_APP_ROLE Grants (V2 Requirement)

V1 used `library.register_schema` to grant privileges automatically. V2 requires explicit grants
on both provider and consumer DBs before `COLLABORATION.INITIALIZE` / `COLLABORATION.JOIN`.

```sql
-- Provider DB (run before COLLABORATION.INITIALIZE)
GRANT USAGE           ON DATABASE <provider_db>                           TO ROLE SAMOOHA_APP_ROLE;
GRANT USAGE           ON SCHEMA   <provider_db>.<schema>                  TO ROLE SAMOOHA_APP_ROLE;
GRANT SELECT          ON ALL TABLES IN SCHEMA <provider_db>.<schema>      TO ROLE SAMOOHA_APP_ROLE;
GRANT SELECT          ON ALL VIEWS  IN SCHEMA <provider_db>.<schema>      TO ROLE SAMOOHA_APP_ROLE;
GRANT REFERENCES      ON ALL VIEWS  IN SCHEMA <provider_db>.<schema>      TO ROLE SAMOOHA_APP_ROLE;
GRANT REFERENCE_USAGE ON DATABASE <provider_db>  TO ROLE SAMOOHA_APP_ROLE WITH GRANT OPTION;

-- Consumer DB (run before COLLABORATION.JOIN)
GRANT USAGE           ON DATABASE <consumer_db>                           TO ROLE SAMOOHA_APP_ROLE;
GRANT USAGE           ON SCHEMA   <consumer_db>.<schema>                  TO ROLE SAMOOHA_APP_ROLE;
GRANT SELECT          ON ALL TABLES IN SCHEMA <consumer_db>.<schema>      TO ROLE SAMOOHA_APP_ROLE;
GRANT REFERENCE_USAGE ON DATABASE <consumer_db>  TO ROLE SAMOOHA_APP_ROLE WITH GRANT OPTION;
```

`REFERENCE_USAGE WITH GRANT OPTION` is required so the SAMOOHA backend can share data object
references cross-account for the collaboration data access layer.

---

## LEAVE / TEARDOWN Two-Call Pattern

V2 collaboration cleanup is asynchronous. Each side requires two calls with a wait:

```
Consumer (first):
  COLLABORATION.LEAVE($name)    → status: LEAVING
  [wait ~60s]
  GET_STATUS($name)             → status: LOCAL_DROP_PENDING
  COLLABORATION.LEAVE($name)    → status: LEFT

Provider (after consumer is LEFT):
  COLLABORATION.TEARDOWN($name) → status: DROPPING
  [wait ~60s]
  GET_STATUS($name)             → status: LOCAL_DROP_PENDING
  COLLABORATION.TEARDOWN($name) → status: DROPPED
```

---

## Key Gotchas

1. **`USE SECONDARY ROLES NONE` is mandatory.** All Collaboration API calls require this in
   the session. Forgetting it causes permission errors even when the correct role is active.

2. **Arg 3/4 reversed between V1 and V2.** V1 `run_analysis`: consumer first (arg 3), provider
   second (arg 4). V2 `COLLABORATION.RUN`: provider first (arg 3), consumer second (arg 4).

3. **Template versions are immutable.** Cannot re-register an existing version. Use unique
   version strings (e.g., `YYYYMMDD_V1`, `YYYYMMDD_V2`) and the update-request workflow for live collaborations.

4. **Version naming regex `^[A-Za-z0-9_]{1,20}$`.** No dots, hyphens, or spaces. Max 20 chars.
   Use `YYYYMMDD_V1` format (13 chars, safe).

5. **`LINK_LOCAL_DATA_OFFERING` must precede first `RUN`.** The consumer must call this after
   joining and registering their data offering. Without it, `RUN` fails with "Object does not exist."

6. **Grants must be in place before INITIALIZE/JOIN.** SAMOOHA_APP_ROLE must have
   REFERENCE_USAGE WITH GRANT OPTION on the data DB before these calls. If grants are
   added after INITIALIZE, call `COLLABORATION.JOIN` explicitly — the auto-join task may
   have already fired and failed.

7. **Collaborator list is fixed after INITIALIZE.** All participants must be declared in
   the INITIALIZE YAML upfront.

8. **No `DELETE_DATA_OFFERING` or `DELETE_TEMPLATE` procedures.** Registry entries cannot be
   individually deleted. They are cleaned up automatically when the SAMOOHA app is uninstalled.

9. **3-part RUN ID format.** Table IDs in `COLLABORATION.RUN` must use
   `ALIAS.DATA_OFFERING_ID.DATASET_ALIAS`. Using a plain FQN or offering ID alone fails
   with `InvalidDataOfferingIdFormat`.

10. **No security scan wait.** V1 `EXTERNAL` clean rooms required waiting for
    `view_cleanroom_scan_status` to return `SUCCEEDED` before publishing. V2 has no security
    scan — `COLLABORATION.INITIALIZE` completes without a scan wait.
