-- ============================================================
-- Common Error Handler Setup  (run once per environment)
-- ============================================================

-- Sequence (retained for ERROR_LOG; not used by BATCH_ERROR_LOG)
CREATE OR REPLACE SEQUENCE SEQ_ERR;

-- Legacy simple error log
CREATE OR REPLACE TABLE ERROR_LOG (
    ERR_SEQ      INTEGER,
    APP_NAME     VARCHAR(250),
    ERR_MSG      VARCHAR(2500),
    MISC_STRING  VARCHAR(2500),
    ERR_DATE     TIMESTAMP
);

-- Batch error log (primary table used by SNOW_ERROR_CAPTURE)
CREATE OR REPLACE TABLE BATCH_ERROR_LOG (
    ERROR_DATE  TIMESTAMP_NTZ  NOT NULL,
    BATCH_ID    NUMBER(38,0)   NOT NULL,
    JOB_NAME    VARCHAR(100)   NOT NULL,
    DESCRIPTION VARCHAR        NOT NULL,
    ERROR_STMT  VARCHAR        NOT NULL
);


-- ============================================================
-- Central error capture procedure
-- BUG FIXES applied vs original:
--   1. "- v_err_stmt := 'test statement';" → proper comment "--"
--   2. "- return 'hi returns from here';"  → return 'SUCCESS'
--   3. varchar2 → varchar  (Snowflake idiomatic)
-- ============================================================
CREATE OR REPLACE PROCEDURE SNOW_ERROR_CAPTURE(
    p_batch_id   NUMBER,
    p_job_name   VARCHAR,
    p_error_desc VARCHAR,
    p_err_stmt   VARCHAR
)
RETURNS VARCHAR(20000)
LANGUAGE SQL
AS
$$
DECLARE
    v_err_stmt   VARCHAR;
    v_error_desc VARCHAR;
    v_job_name   VARCHAR;
    v_batch_id   NUMBER;
    _sql         VARCHAR;
BEGIN
    -- Default NULLs to safe values
    v_batch_id   := IFF(:p_batch_id   IS NULL, 0,         :p_batch_id);
    v_job_name   := IFF(:p_job_name   IS NULL, 'Unknown', :p_job_name);
    v_error_desc := IFF(:p_error_desc IS NULL, 'Unknown', :p_error_desc);

    IF (:p_err_stmt IS NULL) THEN
        v_err_stmt := 'Unknown';
    ELSE
        -- Escape backslashes then single-quotes to prevent dynamic SQL breakage
        SELECT REPLACE(REPLACE(:p_err_stmt, '\\', '\\\\'), '''', '''''')
        INTO   :v_err_stmt;
        -- Note: original used \' escaping; ''  (doubling) is the SQL-standard approach
    END IF;

    _sql := 'INSERT INTO BATCH_ERROR_LOG (ERROR_DATE, BATCH_ID, JOB_NAME, DESCRIPTION, ERROR_STMT) '
         || 'VALUES (CURRENT_TIMESTAMP, '
         || :v_batch_id   || ', '''
         || :v_job_name   || ''', '''
         || :v_error_desc || ''', '''
         || :v_err_stmt   || ''')';

    EXECUTE IMMEDIATE :_sql;

    RETURN 'SUCCESS';   -- FIX: was "- return 'hi returns from here';" (syntax error)

EXCEPTION
    WHEN STATEMENT_ERROR THEN
        RETURN OBJECT_CONSTRUCT(
            'Error type', 'STATEMENT_ERROR',
            'SQLCODE',    SQLCODE,
            'SQLERRM',    SQLERRM,
            'SQLSTATE',   SQLSTATE
        )::VARCHAR;
    WHEN OTHER THEN
        RETURN OBJECT_CONSTRUCT(
            'Error type', 'Other error',
            'SQLCODE',    SQLCODE,
            'SQLERRM',    SQLERRM,
            'SQLSTATE',   SQLSTATE
        )::VARCHAR;
END;
$$;


-- ============================================================
-- Example calling procedure
-- BUG FIXES applied vs original:
--   1. "select 1/0 from dual" → "select 1/0"  (no DUAL table in Snowflake)
--   2. varchar2 → varchar
-- ============================================================
CREATE OR REPLACE PROCEDURE HK_TEST()
RETURNS VARCHAR(200)
LANGUAGE SQL
AS
$$
DECLARE
    v_err_stmt VARCHAR;
    v_sqlerrm  VARCHAR;
BEGIN
    v_err_stmt := 'Before the select';

    SELECT 1/0;   -- FIX: removed "from dual" — Snowflake has no DUAL table

    RETURN 'SUCCESS';

EXCEPTION
    WHEN OTHER THEN
        -- Escape single-quotes in the error message before passing to dynamic SQL
        SELECT REPLACE(REPLACE(:sqlerrm, '\\', '\\\\'), '''', '''''')
        INTO   :v_sqlerrm;

        CALL SNOW_ERROR_CAPTURE(
            100,
            'HK_TEST',
            'SQLCODE: ' || :sqlcode || ' SQLERRM: ' || :v_sqlerrm || ' SQLSTATE: ' || :sqlstate,
            :v_err_stmt
        );
END;
$$;


-- ============================================================
-- Test
-- ============================================================
CALL HK_TEST();
SELECT * FROM BATCH_ERROR_LOG ORDER BY ERROR_DATE DESC;
