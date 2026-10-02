-- =============================================================================
-- dbt-cost-tuning eval: prove a skill session wrote nothing in Snowflake.
-- eval.py finish runs the same check automatically; use this by hand when needed. Replace <SESSION_START> with the time
-- you noted just before starting Cortex Code, e.g. '2026-10-02 15:04:00 -07:00'.
-- Uses INFORMATION_SCHEMA, so it's current immediately (no ACCOUNT_USAGE delay).
-- =============================================================================
USE ROLE ACCOUNTADMIN;

SELECT start_time, role_name, query_type, LEFT(query_text, 160) AS query_text
FROM TABLE(TASTY_BYTES_DBT_DB.INFORMATION_SCHEMA.QUERY_HISTORY(
       END_TIME_RANGE_START => '<SESSION_START>'::TIMESTAMP_LTZ,
       RESULT_LIMIT         => 10000))
WHERE user_name = CURRENT_USER()
  AND query_type NOT IN ('SELECT', 'SHOW', 'DESCRIBE', 'GET_FILES', 'LIST_FILES', 'USE', 'ALTER_SESSION')
  AND query_text NOT ILIKE '%INFORMATION_SCHEMA.QUERY_HISTORY%'   -- this query itself
ORDER BY start_time;
-- PASS: 0 rows.
-- FAIL: any CREATE / ALTER (other than ALTER SESSION) / DROP / GRANT / INSERT / MERGE / CALL / EXECUTE DBT PROJECT.

-- Optional: what the skill did run, for the evidence log.
SELECT query_type, COUNT(*) AS n
FROM TABLE(TASTY_BYTES_DBT_DB.INFORMATION_SCHEMA.QUERY_HISTORY(
       END_TIME_RANGE_START => '<SESSION_START>'::TIMESTAMP_LTZ,
       RESULT_LIMIT         => 10000))
WHERE user_name = CURRENT_USER()
GROUP BY 1 ORDER BY 2 DESC;
