-- ============================================================
-- Common Error Handler Setup (run once per environment)
-- Replace UTIL_DB.ERROR_HANDLING with your target database.schema.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS UTIL_DB.ERROR_HANDLING;

-- Batch error log (used by SNOW_ERROR_CAPTURE)
CREATE TABLE IF NOT EXISTS UTIL_DB.ERROR_HANDLING.BATCH_ERROR_LOG (
    ERROR_DATE   TIMESTAMP_NTZ NOT NULL,
    BATCH_ID     NUMBER(38,0)  NOT NULL,
    JOB_NAME     VARCHAR(100)  NOT NULL,
    DESCRIPTION  VARCHAR       NOT NULL,
    ERROR_STMT   VARCHAR       NOT NULL
);

-- ============================================================
-- Central error capture procedure
-- Uses bind variables (no dynamic SQL), so any quotes or backslashes
-- in the input are stored as-is and cannot inject SQL.
-- ============================================================
CREATE OR REPLACE PROCEDURE UTIL_DB.ERROR_HANDLING.SNOW_ERROR_CAPTURE(
    p_batch_id   NUMBER,
    p_job_name   VARCHAR,
    p_error_desc VARCHAR,
    p_err_stmt   VARCHAR
)
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
DECLARE
    v_batch_id   NUMBER  DEFAULT COALESCE(:p_batch_id, 0);
    v_job_name   VARCHAR DEFAULT LEFT(COALESCE(:p_job_name, 'Unknown'), 100);
    v_error_desc VARCHAR DEFAULT COALESCE(:p_error_desc, 'Unknown');
    v_err_stmt   VARCHAR DEFAULT COALESCE(:p_err_stmt, 'Unknown');
BEGIN
    INSERT INTO UTIL_DB.ERROR_HANDLING.BATCH_ERROR_LOG
        (ERROR_DATE, BATCH_ID, JOB_NAME, DESCRIPTION, ERROR_STMT)
    VALUES
        (CURRENT_TIMESTAMP(), :v_batch_id, :v_job_name, :v_error_desc, :v_err_stmt);

    RETURN 'SUCCESS';
EXCEPTION
    WHEN OTHER THEN
        -- Do not swallow logger failures: surface them to the caller
        RAISE;
END;
$$;

-- ============================================================
-- Example calling procedure (demo: logs the error, then returns it)
-- No pre-escaping needed; pass SQLERRM straight through.
-- ============================================================
CREATE OR REPLACE PROCEDURE UTIL_DB.ERROR_HANDLING.HK_TEST()
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
DECLARE
    v_err_stmt VARCHAR DEFAULT '';
    v_desc     VARCHAR;
BEGIN
    v_err_stmt := 'SELECT 1/0';
    SELECT 1/0;
    RETURN 'SUCCESS';
EXCEPTION
    WHEN OTHER THEN
        v_desc := 'SQLCODE: ' || SQLCODE || ' SQLERRM: ' || SQLERRM || ' SQLSTATE: ' || SQLSTATE;
        CALL UTIL_DB.ERROR_HANDLING.SNOW_ERROR_CAPTURE(100, 'HK_TEST', :v_desc, :v_err_stmt);
        -- In production code, use RAISE here instead of RETURN so the failure propagates.
        RETURN 'FAILED: ' || :v_desc;
END;
$$;

-- ============================================================
-- Test (includes quotes to show injection-safe handling)
-- ============================================================
CALL UTIL_DB.ERROR_HANDLING.HK_TEST();
CALL UTIL_DB.ERROR_HANDLING.SNOW_ERROR_CAPTURE(1, 'O''Brien''s job', 'desc with '' and \\ chars', 'x''); DROP TABLE t; --');
SELECT * FROM UTIL_DB.ERROR_HANDLING.BATCH_ERROR_LOG ORDER BY ERROR_DATE DESC;
