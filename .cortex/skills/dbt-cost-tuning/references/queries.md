# dbt Cost Tuning: Query Templates

Every query here is read-only. Replace the placeholders with values found in Phase 0. Never guess them:

| Placeholder | Meaning | Example |
|---|---|---|
| `<DB>` | Database holding the dbt project object | `TASTY_BYTES_DBT_DB` |
| `<SCHEMA>` | Schema holding the dbt project object (UPPERCASE) | `DEV` |
| `<PROJECT>` | dbt project object name (UPPERCASE) | `TASTY_BYTES_DBT` |
| `<N_RUNS>` | Successful runs to analyze | `5` |
| `<DAYS>` | Look-back window for reads | `30` |
| `<MODEL_RELATION>` / `<KEY>` | A model's `relation_name` and the key to check (Q6) | `TASTY_BYTES_DBT_DB.DEV.ORDERS` / `ORDER_DETAIL_ID` |
| `<DIM_RELATION>` | A joined model's or **source's** `relation_name` from the manifest (`nodes` or `sources`) | `tasty_bytes_dbt_db.RAW.FRANCHISE` |

Always bound `ACCOUNT_USAGE` queries by time. The views are large, and unbounded scans cost credits.

**End every query with `/* dbt-cost-tuning */`**, after the last clause (Snowflake strips leading comments, keeps
trailing ones). Q4 filters on it, so this skill's own earlier analyses (e.g. Q6 counting a model's keys) never show
up as "outside reads" of a model. The templates below leave it out for readability.

Table names come from the manifest (`nodes[*].relation_name`, `sources[*].relation_name`). Never build one from a
guessed schema; a dbt source's name (e.g. `tb_101`) is not a schema.

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
`config.cluster_by`, `relation_name`, `raw_code`/`compiled_code`, and `depends_on.nodes`. Also use `child_map`, `exposures`,
and `sources[*].relation_name` (the real location of every source table, for Q6 on a source).

## Q3a: Run coverage (Phase 2): which runs ACCOUNT_USAGE has

Q3 inner-joins each run to its parent query in `ACCOUNT_USAGE.QUERY_HISTORY`, so a run that hasn't arrived yet
(~45 min latency) drops out with no error. Run this first to see which runs Q3 will use:

```sql
WITH runs AS (
  SELECT query_id AS run_id, query_start_time AS run_start, query_end_time AS run_end
  FROM TABLE(<DB>.INFORMATION_SCHEMA.DBT_PROJECT_EXECUTION_HISTORY())
  WHERE object_name = '<PROJECT>' AND schema_name = '<SCHEMA>' AND state = 'SUCCESS'
  QUALIFY ROW_NUMBER() OVER (ORDER BY query_start_time DESC) <= <N_RUNS>
)
SELECT r.run_id, r.run_start, r.run_end, h.query_id IS NOT NULL AS in_account_usage
FROM runs r
LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY h
  ON h.query_id = r.run_id AND h.start_time >= (SELECT MIN(run_start) FROM runs) - INTERVAL '1 minute'
ORDER BY r.run_start DESC;
```
Runs with `in_account_usage = FALSE` go through Q3b. Cross-check with Q3's `runs_used`: it must equal the number of
`TRUE` rows here, and the window size from Q1 minus Q3b's runs.

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
cov AS (SELECT COUNT(*) AS runs_used FROM run_ctx),  -- runs Q3 actually saw; compare with Q1's window size
q AS (
  SELECT c.run_id, c.run_start, h.query_id, h.query_type, h.warehouse_size, h.execution_time, h.total_elapsed_time,
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
),
qc AS (                                       -- per-query credits (rubric §1)
  SELECT q.*,
         COALESCE(q.node_id, 'unattributed:' || COALESCE(q.target_relation, q.query_type)) AS node,
         COALESCE(a.credits_attributed_compute + COALESCE(a.credits_used_query_acceleration, 0),
                  q.execution_time / 3600000 *
                  DECODE(q.warehouse_size,'X-Small',1,'Small',2,'Medium',4,'Large',8,'X-Large',16,
                         '2X-Large',32,'3X-Large',64,'4X-Large',128,'5X-Large',256,'6X-Large',512),
                  0) AS credits,                -- result-cache hits use no warehouse (size NULL): 0, not NULL
         IFF(a.query_id IS NULL, 0, 1) AS is_attributed
  FROM q
  LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY a
    ON a.query_id = q.query_id AND a.start_time >= (SELECT MIN(run_start) FROM runs)
),
node_run AS (                                 -- one row per node per run
  SELECT node, run_id, MAX(run_start) AS run_start, SUM(credits) AS credits, SUM(bytes_scanned) AS bytes_scanned
  FROM qc GROUP BY 1, 2
),
last_scan AS (                                -- rubric §2 zero-scan rule: the most recent run that scanned data
  SELECT node, MAX_BY(credits, run_start) AS credits
  FROM node_run WHERE bytes_scanned > 0 GROUP BY 1
),
node_cost AS (
  SELECT nr.node,
         ROUND(AVG(IFF(nr.bytes_scanned = 0, COALESCE(ls.credits, nr.credits), nr.credits)), 5) AS credits_per_run,
         ROUND(AVG(nr.credits), 5)                                                     AS raw_credits_per_run,
         COUNT_IF(nr.bytes_scanned = 0 AND ls.credits IS NOT NULL)                     AS zero_scan_runs
  FROM node_run nr LEFT JOIN last_scan ls ON ls.node = nr.node
  GROUP BY 1
)
SELECT qc.node,
       COUNT(DISTINCT qc.run_id)                                 AS runs,
       COUNT(*)                                                  AS queries,
       nc.credits_per_run,                                       -- use this for shares and thresholds
       nc.raw_credits_per_run,                                   -- as observed, cache hits included
       nc.zero_scan_runs,
       ROUND(100 * SUM(qc.is_attributed) / COUNT(*))             AS pct_attributed,   -- source label
       ROUND(AVG(qc.total_elapsed_time) / 1000, 1)               AS avg_secs,
       ROUND(MAX(qc.partitions_scanned / NULLIF(qc.partitions_total, 0)), 2) AS max_scan_ratio,
       ROUND(MAX(qc.spill_local)  / 1e9, 2)                      AS max_spill_local_gb,
       ROUND(MAX(qc.spill_remote) / 1e9, 2)                      AS max_spill_remote_gb,
       COUNT(DISTINCT IFF(qc.spill_local > 0 OR qc.spill_remote > 0, qc.run_id, NULL)) AS runs_with_spill,
       MAX(qc.rows_inserted)                                     AS rows_written,
       cov.runs_used                                             -- same on every row
FROM qc JOIN node_cost nc ON nc.node = qc.node CROSS JOIN cov
GROUP BY qc.node, nc.credits_per_run, nc.raw_credits_per_run, nc.zero_scan_runs, cov.runs_used
ORDER BY nc.credits_per_run DESC NULLS LAST;
```
Python-model write queries come back as `unattributed:<table>`. Match `<table>` to a Python model's `relation_name`
and move the row's metrics to that model. Credits (rubric §2): **drop** them if they're `estimated`, **add** them
if both the write and the outer node query are `attributed` (SKILL.md Phase 2).

`pct_attributed` = 100 → `attributed`; 0 → `estimated`; anything in between → `mixed (N% attributed)`. That's per
node; the plan header is `attributed` only if every node is (rubric §1).

`runs_used` < the window size from Q1 (or no rows at all) means some runs aren't in `ACCOUNT_USAGE` yet. Run Q3b for
the runs Q3a marks `FALSE`. Never report the plan on fewer runs than Q1 found without saying so.

If **every** `node` starts with `unattributed:`, no query carries dbt's comment: per-model attribution isn't possible
(SKILL.md Phase 2 step 2). Rows with a table name are `table-linked` writes; the rest is `unattributed`.

## Q3b: Fallback when ACCOUNT_USAGE hasn't caught up with recent runs

`INFORMATION_SCHEMA.QUERY_HISTORY()` updates immediately but keeps only 7 days, and has **no partition or spill columns**.
Use it for credits and timing only, label everything `estimated`, and mark R2 and R3 "not yet evaluable".
Run it once per run that Q3a marks `in_account_usage = FALSE`. The run's user is read from the parent query itself,
since it isn't in `ACCOUNT_USAGE` yet.

```sql
WITH h AS (
  SELECT * FROM TABLE(<DB>.INFORMATION_SCHEMA.QUERY_HISTORY(
         END_TIME_RANGE_START => '<RUN_START>'::TIMESTAMP_LTZ,
         END_TIME_RANGE_END   => DATEADD(second, 1, '<RUN_END>'::TIMESTAMP_LTZ),
         RESULT_LIMIT => 10000))
)
SELECT REGEXP_SUBSTR(query_text, '"node_id": "([^"]+)"', 1, 1, 'e') AS node_id,
       IFF(node_id IS NULL AND query_type IN ('CREATE_TABLE_AS_SELECT','INSERT','MERGE'),
           LOWER(REGEXP_SUBSTR(query_text, 'TABLE\\s+(IF\\s+NOT\\s+EXISTS\\s+)?([A-Za-z0-9_."]+)', 1, 1, 'ie', 2)),
           NULL) AS target_relation,
       query_id, query_type, warehouse_size, execution_time, total_elapsed_time, bytes_scanned, rows_inserted
FROM h
WHERE user_name = (SELECT user_name FROM h WHERE query_id = '<RUN_ID>')
  AND query_id <> '<RUN_ID>'
  AND start_time >= '<RUN_START>'::TIMESTAMP_LTZ
  AND query_type NOT IN ('USE','SHOW','DESCRIBE')
  AND query_text NOT ILIKE 'SELECT CURRENT\\_%';
```
Aggregate per `node_id` (or `unattributed:<target_relation>`) with the same credit formula, Python-model and
zero-scan rules as Q3. Rows from Q3b are always `estimated`.

## Q4: Who reads each model (R2, R4)

Use **`direct_objects_accessed`**. `base_objects_accessed` resolves views down to their base tables, so dbt models never show up in it.
Treat every query belonging to an analyzed dbt run (the `q` set from Q3) as dbt's own, including uncommented Python-model queries.
A comment filter isn't enough. Also exclude this skill's own queries (tagged `/* dbt-cost-tuning */`), or every
earlier analysis adds "outside reads" to the models it checked.

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
  AND h.query_text NOT ILIKE '%/* dbt-cost-tuning */%'  -- this skill's own earlier analyses
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

## Q6: Key uniqueness and join fan-out (R1)

Run this before proposing any `unique_key`, including the key of a `delete+insert` strategy. It's exact, so it
scans one column of the model. Say so if the model is large.
```sql
SELECT COUNT(*) AS row_count, COUNT(*) - COUNT(<KEY>) AS null_keys, COUNT(DISTINCT <KEY>) AS distinct_keys
FROM <MODEL_RELATION>;
```
- `merge` key: needs `null_keys = 0` and `distinct_keys = row_count`.
- `delete+insert` key (e.g. `order_id` on an order-line model): needs `null_keys = 0` in the model, and the key must be
  unique in the table it comes from (run the same query on e.g. `ORDER_HEADER`), with the model's `distinct_keys` ≤ that
  table's `row_count`. It doesn't need to be unique per model row.
Free first check: compare Q3 `rows_written` with the grain table's `row_count` from Q5 or `INFORMATION_SCHEMA.TABLES`.
More rows than the grain table means fan-out. To find the source, run the same query on each joined dimension's
join key; dimensions are small, so this is cheap. `<DIM_RELATION>` is the dimension's `relation_name` from the
manifest: for a source table, `sources[*].relation_name` (e.g. `tasty_bytes_dbt_db.RAW.FRANCHISE`), not the dbt source name:
```sql
SELECT COUNT(*) AS row_count, COUNT(DISTINCT <JOIN_KEY>) AS distinct_keys FROM <DIM_RELATION>;
```
