Snowflake Error Handler Skill for Cortex Code (CoCo)

A custom skill that teaches Cortex Code to automatically wrap every new stored procedure in a standardized error-handling block, logging failures to a centralized telemetry table.



Overview

Scattered, unpredictable error logs are a nightmare to manage. This skill ensures that every time CoCo scaffolds a new Snowflake Stored Procedure, it includes a robust, uniform error-handling block that captures failures and logs them into a centralized BATCH\_ERROR\_LOG table.



By pairing this skill with Snowflake's alerting mechanisms, you can automatically trigger email notifications on failure — and if you route those into an incident management tool like PagerDuty, your team gets real-time alerts the second a production data load fails.



What's Included

File	Purpose

.cortex/skills/error-handler-setup.sql	One-time environment setup: creates the error log table, sequence, and the central SNOW\_ERROR\_CAPTURE procedure

.cortex/skills/SKILL.md	Skill definition that tells CoCo when and how to apply the error-handling pattern

How It Works

BATCH\_ERROR\_LOG table — Central telemetry table storing error date, batch ID, job name, description, and the failing SQL statement.

SNOW\_ERROR\_CAPTURE procedure — A reusable procedure called from any other procedure's EXCEPTION block. It safely escapes dynamic SQL values and inserts a row into BATCH\_ERROR\_LOG.

Automatic triggering — Once installed, CoCo automatically detects when you're building a stored procedure and wraps your logic in the standard error-handling pattern.

Installation

Option 1: Local (Personal Use)

Place the .cortex/skills/ folder in your workspace directory. CoCo will detect and activate the skill automatically.



Option 2: Git (Team Sharing)

Clone or fork this repository and install it via the Agent Settings panel in Cortex Code Desktop under the Git skills section.



Option 3: Snowflake Stage (Organization-wide)

Upload the skill folder to a Snowflake Stage to share it across your entire data engineering team.



One-Time Setup

Run the setup SQL once per database where you want error logging:



CREATE OR REPLACE SEQUENCE SEQ\_ERR;



CREATE OR REPLACE TABLE BATCH\_ERROR\_LOG (

&#x20;   ERROR\_DATE  TIMESTAMP\_NTZ  NOT NULL,

&#x20;   BATCH\_ID    NUMBER(38,0)   NOT NULL,

&#x20;   JOB\_NAME    VARCHAR(100)   NOT NULL,

&#x20;   DESCRIPTION VARCHAR        NOT NULL,

&#x20;   ERROR\_STMT  VARCHAR        NOT NULL

);

Then create the central error capture procedure (see error-handler-setup.sql for the full definition).



Usage Example

Once the skill is active, simply ask CoCo to create a procedure:



"CoCo, please create a new procedure to process daily sales transactions."



CoCo will automatically wrap your logic in the error-handling pattern:



CREATE OR REPLACE PROCEDURE PROCESS\_DAILY\_SALES()

RETURNS VARCHAR(200)

LANGUAGE SQL

AS

$$

DECLARE

&#x20;   v\_err\_stmt VARCHAR;

BEGIN

&#x20;   v\_err\_stmt := 'Processing daily sales';



&#x20;   -- Your business logic here

&#x20;   INSERT INTO SALES\_SUMMARY

&#x20;   SELECT region, SUM(amount)

&#x20;   FROM RAW\_SALES

&#x20;   WHERE sale\_date = CURRENT\_DATE

&#x20;   GROUP BY region;



&#x20;   RETURN 'SUCCESS';



EXCEPTION

&#x20;   WHEN OTHER THEN

&#x20;       CALL SNOW\_ERROR\_CAPTURE(

&#x20;           100,

&#x20;           'PROCESS\_DAILY\_SALES',

&#x20;           'SQLCODE: ' || :sqlcode || ' SQLERRM: ' || :sqlerrm || ' SQLSTATE: ' || :sqlstate,

&#x20;           :v\_err\_stmt

&#x20;       );

&#x20;       RETURN 'FAILED';

END;

$$;



Prerequisites

A Snowflake account with Cortex Code enabled

Permissions to create tables, sequences, and procedures in your target schema

Cortex Code Desktop, Snowsight, or CLI access

Further Reading

Mastering Snowflake's CoCo: How to Build Custom Skills for Error Handling — Original article by the author

Snowflake Cortex Code Documentation

Author

Himanshu Kandpal (@hkandpal732)



License

Apache-2.0





