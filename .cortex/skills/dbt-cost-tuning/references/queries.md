# dbt Cost Tuning: Query Templates

Every query here is read-only. Replace the placeholders with values found in Phase 0. Never guess them:

| Placeholder | Meaning | Example |
|---|---|---|
| `<DB>` | Database holding the dbt project object | `TASTY_BYTES_DBT_DB` |
| `<SCHEMA>` | Schema holding the dbt project object (UPPERCASE) | `DEV` |
| `<PROJECT>` | dbt project object name (UPPERCASE) | `TASTY_BYTES_DBT` |
| `<N_RUNS>` | Successful runs to analyze | `5` |
| `<DAYS>` | Look-back window for reads | `30` |

Always bound `ACCOUNT_USAGE` queries by time. The views are large, and unbounded scans cost credits.

---

## Q0: Session context and capability probes (Phase 0)

```sql
SELECT CURRENT_ROLE() AS role, CURRENT_WAREHOUSE() AS warehouse,
       CURRENT_DATABASE() AS db, CURRENT_SCHEMA() AS schema, CURRENT_ACCOUNT() AS account;
```

Run each probe separately. A failure means that capability is missing, not that the skill should stop:

```sql
SELECT MAX(start_time) FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE start_time >= DATEADD(day, -1, CURRENT_TIMESTAMP());                -- AU_QUERY_HISTORY

SELECT MAX(start_time) FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY
WHERE start_time >= DATEADD(day, -2, CURRENT_TIMESTAMP());                -- QAH (NULL = not populated yet)

SELECT MAX(query_start_time) FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY
WHERE query_start_time >= DATEADD(day, -1, CURRENT_TIMESTAMP());          -- ACCESS_HISTORY (Enterprise+)

SELECT MIN(start_time) FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE start_time >= DATEADD(day, -31, CURRENT_TIMESTAMP());               -- history depth (for R4 guard)
```
The history-depth probe only needs to answer "is there at least 30 days of history?", so it looks back 31 days
and no further. A result 30 or more days ago means the R4 guard is satisfied. Report the depth as "≥ 30 days", not as the true earliest date.

To find dbt projects:
```sql
SHOW DBT PROJECTS IN ACCOUNT;
```

## Q1: Recent successful runs (Phase 1)

```sql
SELECT query_id AS run_id, query_start_time, query_end_time, state, command, args, dbt_version
FROM TABLE(<DB>.INFORMATION_SCHEMA.DBT_PROJECT_EXECUTION_HISTORY())
WHERE object_name = '<PROJECT>' AND schema_name = '<SCHEMA>'          -- schema filter is mandatory
ORDER BY query_start_time DESC
LIMIT 20;
```
Keep `state = 'SUCCESS'` runs whose command is `build` or `run`. Failed runs end early, so their cost per model can't be compared.

## Q2: Artifacts (Phase 1)

```sql
SELECT SYSTEM$LOCATE_DBT_ARTIFACTS('<RUN_ID>');
-- returns snow://dbt/<DB>.<SCHEMA>.<PROJECT>/results/query_id_<RUN_ID>/
```
Download to a local temporary directory. This writes nothing in Snowflake:
```sql
GET 'snow://dbt/<DB>.<SCHEMA>.<PROJECT>/results/query_id_<RUN_ID>/target/manifest.json' 'file://<TMP_DIR>/';
GET 'snow://dbt/<DB>.<SCHEMA>.<PROJECT>/results/query_id_<RUN_ID>/target/run_results.json' 'file://<TMP_DIR>/';
```
From `manifest.json`, use these fields of `nodes[*]`: `resource_type`, `config.materialized`, `config.unique_key`,
`config.cluster_by`, `relation_name`, `raw_code`/`compiled_code`, and `depends_on.nodes`. Also use `child_map` and `exposures`.

## Q3: Per-model metrics (Phase 2): primary path via ACCOUNT_USAGE

Applies the credit-sourcing and attribution rules in `scoring-rubric.md` §1–2.

```sql
WITH runs AS (
  SELECT query_id AS run_id, query_start_time AS run_start, query_end_time AS run_end
  FROM TABLE(<DB>.INFORMATION_SCHEMA.DBT_PROJECT_EXECUTION_HISTORY())
  WHERE object_name = '<PROJECT>' AND schema_name = '<SCHEMA>' AND state = 'SUCCESS'
  QUALIFY ROW_NUMBER() OVER (ORDER BY query_start_time DESC) <= <N_RUNS>
),
run_ctx AS (                                   -- who ran it, so we only pick up this run's queries
  SELECT r.*, h.user_name AS run_user
  FROM runs r
  JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY h ON h.query_id = r.run_id
  WHERE h.start_time >= (SELECT MIN(run_start) FROM runs) - INTERVAL '1 minute'
),
q AS (
  SELECT c.run_id, h.query_id, h.query_type, h.warehouse_size, h.execution_time, h.total_elapsed_time,
         h.bytes_scanned, h.rows_inserted, h.partitions_scanned, h.partitions_total,
         h.bytes_spilled_to_local_storage AS spill_local, h.bytes_spilled_to_remote_storage AS spill_remote,
         REGEXP_SUBSTR(h.query_text, '"node_id": "([^"]+)"', 1, 1, 'e') AS node_id,
         IFF(node_id IS NULL AND h.query_type IN ('CREATE_TABLE_AS_SELECT','INSERT','MERGE'),
             LOWER(REGEXP_SUBSTR(h.query_text, 'TABLE\\s+(IF\\s+NOT\\s+EXISTS\\s+)?([A-Za-z0-9_."]+)', 1, 1, 'ie', 2)),
             NULL) AS target_relation           -- Python models: link uncommented writes by table
  FROM run_ctx c
  JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY h
    ON h.user_name = c.run_user AND h.start_time BETWEEN c.run_start AND c.run_end
  WHERE h.start_time >= (SELECT MIN(run_start) FROM runs)
    AND h.query_id <> c.run_id                  -- exclude the parent EXECUTE DBT PROJECT
    AND h.query_type NOT IN ('USE','SHOW','DESCRIBE')
    AND h.query_text NOT ILIKE 'SELECT CURRENT\\_%'
)
SELECT COALESCE(q.node_id, 'unattributed:' || COALESCE(q.target_relation, q.query_type)) AS node,
       COUNT(DISTINCT q.run_id) AS runs,
       COUNT(*) AS queries,
       ROUND(SUM(COALESCE(
               a.credits_attributed_compute + COALESCE(a.credits_used_query_acceleration, 0),
               q.execution_time / 3600000 *
               DECODE(q.warehouse_size,'X-Small',1,'Small',2,'Medium',4,'Large',8,'X-Large',16,
                      '2X-Large',32,'3X-Large',64,'4X-Large',128,'5X-Large',256,'6X-Large',512)))
             / COUNT(DISTINCT q.run_id), 5)                      AS credits_per_run,
       ROUND(100 * COUNT(a.query_id) / COUNT(*))                AS pct_attributed,   -- source label
       ROUND(AVG(q.total_elapsed_time) / 1000, 1)               AS avg_secs,
       SUM(IFF(q.query_type = 'SELECT' AND q.bytes_scanned = 0, 1, 0)) AS zero_scan_execs,
       ROUND(MAX(q.partitions_scanned / NULLIF(q.partitions_total, 0)), 2) AS max_scan_ratio,
       ROUND(MAX(q.spill_local)  / 1e9, 2)                      AS max_spill_local_gb,
       ROUND(MAX(q.spill_remote) / 1e9, 2)                      AS max_spill_remote_gb,
       COUNT(DISTINCT IFF(q.spill_local > 0 OR q.spill_remote > 0, q.run_id, NULL)) AS runs_with_spill,
       MAX(q.rows_inserted)                                     AS rows_written
FROM q
LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY a
  ON a.query_id = q.query_id AND a.start_time >= (SELECT MIN(run_start) FROM runs)
GROUP BY 1
ORDER BY credits_per_run DESC NULLS LAST;
```

`pct_attributed` = 100 → `attributed`; 0 → `estimated`; anything in between → `mixed (N% attributed)`.

## Q3b: Fallback when ACCOUNT_USAGE hasn't caught up with recent runs

`INFORMATION_SCHEMA.QUERY_HISTORY()` updates immediately but keeps only 7 days, and has **no partition or spill columns**.
Use it for credits and timing only, label everything `estimated`, and mark R2 and R3 "not yet evaluable".

```sql
SELECT REGEXP_SUBSTR(query_text, '"node_id": "([^"]+)"', 1, 1, 'e') AS node_id,
       query_id, query_type, warehouse_size, execution_time, total_elapsed_time, bytes_scanned, rows_inserted
FROM TABLE(<DB>.INFORMATION_SCHEMA.QUERY_HISTORY(
       END_TIME_RANGE_START => '<RUN_START>'::TIMESTAMP_LTZ,
       END_TIME_RANGE_END   => '<RUN_END>'::TIMESTAMP_LTZ,
       RESULT_LIMIT => 10000))
WHERE user_name = '<RUN_USER>' AND query_id <> '<RUN_ID>'
  AND query_type NOT IN ('USE','SHOW','DESCRIBE');
```

## Q4: Who reads each model (R2, R4)

Use **`direct_objects_accessed`**. `base_objects_accessed` resolves views down to their base tables, so dbt models never show up in it.
Treat every query belonging to an analyzed dbt run (the `q` set from Q3) as dbt's own, including uncommented Python-model queries.
A comment filter isn't enough.

```sql
WITH reads AS (
  SELECT ah.query_id, ah.user_name, ah.query_start_time,
         UPPER(f.value:"objectName"::STRING) AS obj
  FROM SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY ah,
       LATERAL FLATTEN(ah.direct_objects_accessed) f
  WHERE ah.query_start_time >= DATEADD(day, -<DAYS>, CURRENT_TIMESTAMP())
    AND UPPER(f.value:"objectName"::STRING) IN (<UPPERCASE_RELATION_NAMES>)
)
SELECT r.obj,
       COUNT(DISTINCT r.query_id)                                   AS reads,
       COUNT(DISTINCT r.user_name)                                  AS readers,
       ROUND(AVG(h.partitions_scanned / NULLIF(h.partitions_total, 0)), 2) AS avg_scan_ratio
FROM reads r
JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY h
  ON h.query_id = r.query_id AND h.start_time >= DATEADD(day, -<DAYS>, CURRENT_TIMESTAMP())
WHERE h.query_text NOT ILIKE '%"app": "dbt"%'        -- first pass; then also drop query_ids from Q3's run set
GROUP BY 1;
```
If a model has no row here, nothing outside dbt read it in the window.

## Q5: Table size and clustering (R1, R2)

```sql
SELECT table_name, row_count, bytes, clustering_key, is_transient
FROM <DB>.INFORMATION_SCHEMA.TABLES
WHERE table_schema = '<MODEL_SCHEMA>' AND table_type = 'BASE TABLE';
```
For a clustering candidate column:
```sql
SELECT SYSTEM$CLUSTERING_INFORMATION('<DB>.<MODEL_SCHEMA>.<TABLE>', '(<COLUMN>)');
```
