-- Before/after cost of `orders` (and the whole build), one row per dbt run.
-- Runs: the 2 DEV baseline builds (table) and the COST_DEMO builds (incremental, delete+insert).
-- Linking follows the fixed Q3 (S12): an attributed query belongs to a run only if its root_query_id is the
-- run's EXECUTE DBT PROJECT query; a not-yet-attributed one only if it carries dbt's node_id comment.
-- Credits: attributed when QUERY_ATTRIBUTION_HISTORY has the query, else execution_time x 2 credits/h (Small).
WITH runs AS (
  SELECT query_id AS run_id, start_time AS run_start, end_time AS run_end, user_name AS run_user,
         REGEXP_SUBSTR(query_text, 'args=''([^'']*)''', 1, 1, 'e') AS args
  FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
  WHERE query_id IN ('01c77787-0208-c3ea-0011-5b520001f266',   -- DEV baseline 1 (table)
                     '01c77799-0208-bb91-0011-5b5200022262',   -- DEV baseline 2 (table)
                     '01c783db-0108-c882-0011-5b52000571ae',   -- COST_DEMO full refresh
                     '01c783de-0108-cbd0-0011-5b520005420a',   -- COST_DEMO incremental 1 (right after full refresh)
                     '01c783e0-0108-cd64-0011-5b520005c162',   -- COST_DEMO incremental 2 (warm warehouse)
                     '01c783e3-0108-cbd0-0011-5b520005489e')   -- COST_DEMO incremental 3 (after auto-suspend)
    AND start_time >= '2026-10-02 12:00:00 -0700'::TIMESTAMP_LTZ
),
q AS (
  SELECT r.run_id, r.run_start, r.args, h.query_type, h.execution_time, h.bytes_scanned,
         h.percentage_scanned_from_cache, h.bytes_written, h.rows_inserted, h.rows_deleted,
         h.partitions_scanned, h.partitions_total, h.bytes_spilled_to_local_storage,
         REGEXP_SUBSTR(h.query_text, '"node_id": "([^"]+)"', 1, 1, 'e') AS node_id,
         a.query_id AS attr_query_id,
         COALESCE(a.credits_attributed_compute + COALESCE(a.credits_used_query_acceleration, 0),
                  h.execution_time / 3600000 * 2) AS credits
  FROM runs r
  JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY h
    ON h.user_name = r.run_user AND h.start_time BETWEEN r.run_start AND r.run_end AND h.query_id <> r.run_id
  LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY a
    ON a.query_id = h.query_id AND a.start_time >= '2026-10-02 12:00:00 -0700'::TIMESTAMP_LTZ
  WHERE h.start_time >= '2026-10-02 12:00:00 -0700'::TIMESTAMP_LTZ
    AND (a.root_query_id = r.run_id OR (a.query_id IS NULL AND node_id IS NOT NULL))
)
SELECT run_start, args,
       ROUND(SUM(IFF(node_id = 'model.tasty_bytes.orders', credits, 0)), 5)                 AS orders_credits,
       ROUND(SUM(IFF(node_id = 'model.tasty_bytes.orders', execution_time, 0)) / 1000, 1)   AS orders_exec_s,
       ROUND(SUM(IFF(node_id = 'model.tasty_bytes.orders', bytes_scanned, 0)) / 1e9, 2)     AS orders_gb_scanned,
       ROUND(SUM(IFF(node_id = 'model.tasty_bytes.orders', partitions_scanned, 0)))         AS orders_partitions_scanned,
       ROUND(MAX(IFF(node_id = 'model.tasty_bytes.orders' AND query_type = 'DELETE',
                     percentage_scanned_from_cache, NULL)) * 100)                           AS delete_pct_from_cache,
       ROUND(SUM(IFF(node_id = 'model.tasty_bytes.orders' AND query_type = 'DELETE', bytes_written, 0)) / 1e9, 2) AS delete_gb_written,
       SUM(IFF(node_id = 'model.tasty_bytes.orders', rows_inserted, 0))                     AS orders_rows_inserted,
       SUM(IFF(node_id = 'model.tasty_bytes.orders', rows_deleted, 0))                      AS orders_rows_deleted,
       ROUND(SUM(IFF(node_id = 'model.tasty_bytes.orders', bytes_spilled_to_local_storage, 0)) / 1e9, 2) AS orders_spill_gb,
       ROUND(SUM(credits), 5)                                                               AS build_credits,
       ROUND(100 * COUNT(attr_query_id) / COUNT(*))                                         AS pct_queries_attributed
FROM q
GROUP BY run_id, run_start, args
ORDER BY run_start;
