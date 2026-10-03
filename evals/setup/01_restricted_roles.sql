-- =============================================================================
-- dbt-cost-tuning eval: restricted roles (TC04, TC05)
-- Run as ACCOUNTADMIN in Snowsight or with:  snow sql -c AG26866 -f evals/setup/01_restricted_roles.sql
-- Teardown: evals/setup/99_teardown.sql
-- =============================================================================

USE ROLE ACCOUNTADMIN;
SET me = '"' || CURRENT_USER() || '"';   -- quoted: user names can be case-sensitive (IDENTIFIER() upper-cases a bare name)

-- -----------------------------------------------------------------------------
-- TC05: DBT_TUNING_EVAL_RESTRICTED
-- Can see and monitor the dbt project, read DEV models and use the warehouse.
-- It has NO access to SNOWFLAKE.ACCOUNT_USAGE: no IMPORTED PRIVILEGES and no SNOWFLAKE.* database roles.
-- Expected effect: QUERY_HISTORY, QUERY_ATTRIBUTION_HISTORY and ACCESS_HISTORY are all unavailable,
-- so the skill falls back to INFORMATION_SCHEMA and reports R2, R3 and R4 as not evaluable.
-- -----------------------------------------------------------------------------
CREATE ROLE IF NOT EXISTS DBT_TUNING_EVAL_RESTRICTED
  COMMENT = 'dbt-cost-tuning eval TC05: no ACCOUNT_USAGE access';

GRANT USAGE ON WAREHOUSE TASTY_BYTES_DBT_WH            TO ROLE DBT_TUNING_EVAL_RESTRICTED;
GRANT USAGE ON DATABASE  TASTY_BYTES_DBT_DB            TO ROLE DBT_TUNING_EVAL_RESTRICTED;
GRANT USAGE ON SCHEMA    TASTY_BYTES_DBT_DB.DEV        TO ROLE DBT_TUNING_EVAL_RESTRICTED;
GRANT SELECT ON ALL TABLES IN SCHEMA TASTY_BYTES_DBT_DB.DEV TO ROLE DBT_TUNING_EVAL_RESTRICTED;
GRANT SELECT ON ALL VIEWS  IN SCHEMA TASTY_BYTES_DBT_DB.DEV TO ROLE DBT_TUNING_EVAL_RESTRICTED;

-- USAGE makes the project visible to SHOW DBT PROJECTS. MONITOR is needed for
-- DBT_PROJECT_EXECUTION_HISTORY and the run artifacts. If either GRANT errors on your
-- account, note the error in the results: it changes what TC05 can observe.
GRANT USAGE   ON DBT PROJECT TASTY_BYTES_DBT_DB.DEV.TASTY_BYTES_DBT TO ROLE DBT_TUNING_EVAL_RESTRICTED;
GRANT MONITOR ON DBT PROJECT TASTY_BYTES_DBT_DB.DEV.TASTY_BYTES_DBT TO ROLE DBT_TUNING_EVAL_RESTRICTED;

-- Lets INFORMATION_SCHEMA.QUERY_HISTORY() show queries on this warehouse (the Q3b fallback).
GRANT MONITOR ON WAREHOUSE TASTY_BYTES_DBT_WH TO ROLE DBT_TUNING_EVAL_RESTRICTED;

GRANT ROLE DBT_TUNING_EVAL_RESTRICTED TO USER IDENTIFIER($me);

-- -----------------------------------------------------------------------------
-- TC04: DBT_TUNING_EVAL_NOPROJECT
-- Can use the warehouse but can't see any database, so SHOW DBT PROJECTS returns nothing.
-- -----------------------------------------------------------------------------
CREATE ROLE IF NOT EXISTS DBT_TUNING_EVAL_NOPROJECT
  COMMENT = 'dbt-cost-tuning eval TC04: no visible dbt projects';
GRANT USAGE ON WAREHOUSE TASTY_BYTES_DBT_WH TO ROLE DBT_TUNING_EVAL_NOPROJECT;
GRANT ROLE DBT_TUNING_EVAL_NOPROJECT TO USER IDENTIFIER($me);

-- -----------------------------------------------------------------------------
-- Secondary roles: if your user has DEFAULT_SECONDARY_ROLES = ('ALL'), every session
-- also gets ACCOUNTADMIN's privileges, and the restricted roles restrict nothing.
-- Record the current value, then switch it off for the duration of the eval.
-- -----------------------------------------------------------------------------
DESC USER IDENTIFIER($me);   -- note DEFAULT_SECONDARY_ROLES and DEFAULT_WAREHOUSE for teardown
ALTER USER IDENTIFIER($me) SET DEFAULT_SECONDARY_ROLES = ();

-- =============================================================================
-- Verification. Open a NEW session (secondary-role defaults apply at login), then:
-- =============================================================================
-- USE ROLE DBT_TUNING_EVAL_RESTRICTED;
-- SELECT CURRENT_SECONDARY_ROLES();          -- expect {"roles":"","value":""}
-- SHOW DBT PROJECTS IN ACCOUNT;              -- expect TASTY_BYTES_DBT only
-- SELECT query_id, state, command
-- FROM TABLE(TASTY_BYTES_DBT_DB.INFORMATION_SCHEMA.DBT_PROJECT_EXECUTION_HISTORY())
-- WHERE object_name = 'TASTY_BYTES_DBT' AND schema_name = 'DEV';   -- expect the 2 baseline runs
-- SELECT MAX(start_time) FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
-- WHERE start_time >= DATEADD(hour, -1, CURRENT_TIMESTAMP());      -- expect: does not exist or not authorized
--
-- USE ROLE DBT_TUNING_EVAL_NOPROJECT;
-- SHOW DBT PROJECTS IN ACCOUNT;              -- expect 0 rows
