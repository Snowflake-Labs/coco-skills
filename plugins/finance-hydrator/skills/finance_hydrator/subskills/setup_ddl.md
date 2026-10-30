# Setup DDL Templates — Recon Profile Store

DDL templates for the recon_analysis-compatible profile store. Used by `subskills/setup_profile_store.md`.
All `<PLACEHOLDER>` tokens are substituted by the setup sub-phase before display and execution.

Source of truth for the backing table schema: `<TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>`
(derived from the recon_analysis skill behavioral specs and the live procedure signatures returned
by `SHOW PROCEDURES`).

---

## Placeholder reference

| Placeholder | Description | Example |
|---|---|---|
| `<TABLE_DB>` | Database for the backing table | `FINANCE` |
| `<TABLE_SCHEMA>` | Schema for the backing table | `STREAMLIT_DATA` |
| `<TABLE_NAME>` | Table name (uppercased) | `RECON_USER_PROFILES` |
| `<PROC_DB>` | Database for the procedures | `FINANCE` |
| `<PROC_SCHEMA>` | Schema for the procedures | `ACCOUNTING` |
| `<GET_PROC_NAME>` | GET procedure name (uppercased) | `RECON_GET_PROFILES` |
| `<SAVE_PROC_NAME>` | SAVE procedure name (uppercased) | `RECON_SAVE_PROFILE` |
| `<GRANT_ROLE>` | Role to receive USAGE grants | `<ROLE_WITH_FINANCE_READ>` |
| `<EXECUTE_MODE>` | `OWNER` or `CALLER` | `OWNER` |

---

## Section A — Backing Table

Exact schema derived from `GET_DDL('TABLE', '<TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>')`.
Append-only: new rows are INSERTed for every save/update; the latest non-deleted row per
`PROFILE_NAME` is treated as current. No UPDATE statements are ever issued against this table.

```sql
-- ============================================================
-- Section A: Recon Profile Store — Backing Table
-- ============================================================
CREATE TABLE IF NOT EXISTS <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME> (
    PROFILE_NAME     VARCHAR(255),
    PROFILE_OWNER    VARCHAR(255),
    PROFILE_SETTINGS VARIANT,
    IS_PUBLIC        BOOLEAN,
    UPDATED_AT       TIMESTAMP_NTZ(9),
    IS_DELETED       BOOLEAN DEFAULT FALSE
)
COMMENT = 'Reconciliation profile store — created by Finance Hydrator';
```

**Column mapping to `accounting.recon.profile_store` catalog fields:**

| Physical column | Canonical field | Aliases in catalog |
|---|---|---|
| `PROFILE_NAME` | `profile_name` | profile_name, name, recon_name, config_name |
| `PROFILE_OWNER` | `owner` | owner, created_by, profile_owner |
| `PROFILE_SETTINGS` | `settings` | settings, config, settings_json, payload |
| `IS_PUBLIC` | `visibility` | visibility, is_public |
| `UPDATED_AT` | `updated_at` | updated_at, last_updated, modified_at |

---

## Section B — GET Procedure

Behavior:
- No args → returns JSON array of accessible profile summaries
- `PROFILE_NAME` arg → returns full `PROFILE_SETTINGS` VARIANT for that profile
- `PROFILE_NAME` + `AS_OF` → returns settings as of that timestamp (for revert)

```sql
-- ============================================================
-- Section B: Recon Profile Store — GET Procedure
-- ============================================================
CREATE OR REPLACE PROCEDURE <PROC_DB>.<PROC_SCHEMA>.<GET_PROC_NAME>(
    PROFILE_NAME  VARCHAR        DEFAULT NULL,
    AS_OF         TIMESTAMP_NTZ  DEFAULT NULL
)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS <EXECUTE_MODE>
AS
$$
DECLARE
    v_result VARCHAR;
BEGIN
    IF (:PROFILE_NAME IS NULL) THEN
        -- List mode: return summary array of all accessible profiles
        SELECT ARRAY_AGG(
            OBJECT_CONSTRUCT(
                'profile_name',  PROFILE_NAME,
                'profile_owner', PROFILE_OWNER,
                'is_public',     IS_PUBLIC,
                'updated_at',    TO_CHAR(UPDATED_AT, 'YYYY-MM-DD HH24:MI:SS'),
                'source1',       PROFILE_SETTINGS:source1:value::STRING,
                'source2',       PROFILE_SETTINGS:source2:value::STRING
            ) ORDER BY UPDATED_AT DESC
        )::VARCHAR INTO :v_result
        FROM <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>
        WHERE IS_DELETED = FALSE
          AND (IS_PUBLIC = TRUE OR UPPER(PROFILE_OWNER) = UPPER(CURRENT_USER()));
        RETURN NVL(:v_result, '[]');

    ELSEIF (:AS_OF IS NULL) THEN
        -- Load mode: return full settings for the latest version of a named profile
        SELECT PROFILE_SETTINGS::VARCHAR INTO :v_result
        FROM <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>
        WHERE PROFILE_NAME = :PROFILE_NAME
          AND IS_DELETED   = FALSE
          AND (IS_PUBLIC   = TRUE OR UPPER(PROFILE_OWNER) = UPPER(CURRENT_USER()))
        ORDER BY UPDATED_AT DESC
        LIMIT 1;
        RETURN NVL(:v_result, 'null');

    ELSE
        -- Revert mode: return settings as they existed at a specific timestamp
        SELECT PROFILE_SETTINGS::VARCHAR INTO :v_result
        FROM <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>
        WHERE PROFILE_NAME = :PROFILE_NAME
          AND UPDATED_AT  <= :AS_OF
          AND (IS_PUBLIC   = TRUE OR UPPER(PROFILE_OWNER) = UPPER(CURRENT_USER()))
        ORDER BY UPDATED_AT DESC
        LIMIT 1;
        RETURN NVL(:v_result, 'null');
    END IF;
END;
$$;
```

---

## Section C — SAVE Procedure

Actions:
- `INSERT` — create new profile (fails if name already exists for an active profile)
- `UPDATE` — append new version row; only the profile owner may update
- `DELETE` — soft-delete by appending a tombstone row; only the profile owner may delete

Returns `{"success": true, "updated_at": "YYYY-MM-DD HH24:MI:SS"}` or
`{"success": false, "error": "..."}`.

```sql
-- ============================================================
-- Section C: Recon Profile Store — SAVE Procedure
-- ============================================================
CREATE OR REPLACE PROCEDURE <PROC_DB>.<PROC_SCHEMA>.<SAVE_PROC_NAME>(
    ACTION                VARCHAR,
    PROFILE_NAME          VARCHAR,
    PROFILE_SETTINGS_JSON VARCHAR  DEFAULT NULL,
    IS_PUBLIC             BOOLEAN  DEFAULT FALSE
)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS <EXECUTE_MODE>
AS
$$
DECLARE
    v_now    TIMESTAMP_NTZ := CURRENT_TIMESTAMP()::TIMESTAMP_NTZ;
    v_ts_str VARCHAR       := TO_CHAR(:v_now, 'YYYY-MM-DD HH24:MI:SS');
BEGIN
    IF (:ACTION = 'INSERT') THEN
        -- Reject if an active profile with this name already exists
        IF (EXISTS (
            SELECT 1 FROM <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>
            WHERE PROFILE_NAME = :PROFILE_NAME
              AND IS_DELETED   = FALSE
        )) THEN
            RETURN '{"success": false, "error": "Profile name already exists. Use UPDATE to modify."}';
        END IF;
        INSERT INTO <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>
            (PROFILE_NAME, PROFILE_OWNER, PROFILE_SETTINGS, IS_PUBLIC, UPDATED_AT, IS_DELETED)
        VALUES
            (:PROFILE_NAME, CURRENT_USER(),
             PARSE_JSON(:PROFILE_SETTINGS_JSON), :IS_PUBLIC, :v_now, FALSE);
        RETURN '{"success": true, "updated_at": "' || :v_ts_str || '"}';

    ELSEIF (:ACTION = 'UPDATE') THEN
        -- Only the profile owner may update
        IF (NOT EXISTS (
            SELECT 1 FROM <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>
            WHERE PROFILE_NAME                  = :PROFILE_NAME
              AND UPPER(PROFILE_OWNER)          = UPPER(CURRENT_USER())
              AND IS_DELETED                    = FALSE
        )) THEN
            RETURN '{"success": false, "error": "Profile not found or you do not have permission to update."}';
        END IF;
        -- Append a new version row (append-only; no UPDATE on table rows)
        INSERT INTO <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>
            (PROFILE_NAME, PROFILE_OWNER, PROFILE_SETTINGS, IS_PUBLIC, UPDATED_AT, IS_DELETED)
        VALUES
            (:PROFILE_NAME, CURRENT_USER(),
             PARSE_JSON(:PROFILE_SETTINGS_JSON), :IS_PUBLIC, :v_now, FALSE);
        RETURN '{"success": true, "updated_at": "' || :v_ts_str || '"}';

    ELSEIF (:ACTION = 'DELETE') THEN
        -- Only the profile owner may delete; tombstone row preserves history
        IF (NOT EXISTS (
            SELECT 1 FROM <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>
            WHERE PROFILE_NAME         = :PROFILE_NAME
              AND UPPER(PROFILE_OWNER) = UPPER(CURRENT_USER())
              AND IS_DELETED           = FALSE
        )) THEN
            RETURN '{"success": false, "error": "Profile not found or you do not have permission to delete."}';
        END IF;
        INSERT INTO <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>
            (PROFILE_NAME, PROFILE_OWNER, PROFILE_SETTINGS, IS_PUBLIC, UPDATED_AT, IS_DELETED)
        VALUES
            (:PROFILE_NAME, CURRENT_USER(), NULL, FALSE, :v_now, TRUE);
        RETURN '{"success": true, "updated_at": "' || :v_ts_str || '"}';

    ELSE
        RETURN '{"success": false, "error": "Unknown action. Use INSERT, UPDATE, or DELETE."}';
    END IF;
END;
$$;
```

---

## Section D — GRANT statements

```sql
-- ============================================================
-- Section D: Grant USAGE on procedures to the working role
-- ============================================================
GRANT USAGE ON PROCEDURE <PROC_DB>.<PROC_SCHEMA>.<GET_PROC_NAME>(VARCHAR, TIMESTAMP_NTZ)
  TO ROLE <GRANT_ROLE>;

GRANT USAGE ON PROCEDURE <PROC_DB>.<PROC_SCHEMA>.<SAVE_PROC_NAME>(VARCHAR, VARCHAR, VARCHAR, BOOLEAN)
  TO ROLE <GRANT_ROLE>;
```

**Additional grant for EXECUTE AS CALLER only:**
```sql
-- Required only when EXECUTE AS CALLER is chosen in Step 2 of setup_profile_store.md.
-- Not needed for EXECUTE AS OWNER (the procedure owner's privileges cover the table).
GRANT SELECT, INSERT ON TABLE <TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>
  TO ROLE <GRANT_ROLE>;
```

---

## Notes

1. **EXECUTE AS OWNER vs CALLER**
   - `OWNER` (recommended): procedures run with the owner's privileges. The backing table
     (`<TABLE_DB>.<TABLE_SCHEMA>.<TABLE_NAME>`) does not need to be directly accessible to
     `<GRANT_ROLE>`. Matches the production recon_analysis pattern.
   - `CALLER`: procedures run with the calling user's role. Section D must include the table
     GRANT above. Easier to audit but requires broader table access.

2. **Append-only writes**
   The SAVE procedure never issues `UPDATE` or `DELETE` against the backing table — every
   change is a new `INSERT`. This provides a full audit trail and enables the point-in-time
   revert feature (the `AS_OF` parameter on GET).

3. **Inventory entry after setup**
   After successful execution, `setup_profile_store.md` writes an inventory object with:
   - `fqn`: the GET procedure FQN
   - `object_type`: `"procedure"`
   - `extra.save_procedure`: the SAVE procedure FQN
   - `extra.backing_table`: the backing table FQN
   `score_readiness.py` treats this as a fully-resolved binding (`req_columns_ok = true`,
   `confidence = HIGH`).
