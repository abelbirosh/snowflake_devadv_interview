-- =============================================================================
-- dbt-cost-tuning eval: teardown. Run as ACCOUNTADMIN when testing is finished.
-- =============================================================================
USE ROLE ACCOUNTADMIN;
SET me = '"' || CURRENT_USER() || '"';   -- quoted: user names can be case-sensitive (IDENTIFIER() upper-cases a bare name)

-- Restore the user settings changed in 01_restricted_roles.sql / 03_optional_cases.sh tc03.
-- Put back the values you recorded from DESC USER (('ALL') is the Snowflake default for new users).
ALTER USER IDENTIFIER($me) SET DEFAULT_SECONDARY_ROLES = ('ALL');
-- ALTER USER IDENTIFIER($me) SET DEFAULT_WAREHOUSE = <your original default warehouse>;

DROP ROLE IF EXISTS DBT_TUNING_EVAL_RESTRICTED;
DROP ROLE IF EXISTS DBT_TUNING_EVAL_NOPROJECT;

-- TC08 / TC07 projects and the tables they built.
DROP SCHEMA IF EXISTS TASTY_BYTES_DBT_DB.EVAL_NOCOMMENT;
DROP SCHEMA IF EXISTS TASTY_BYTES_DBT_DB.EVAL_FAIL;
