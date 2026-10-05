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
| `<RUN_IDS>` | The window's run IDs from Q1, quoted, comma-separated | `'01c77787-…f266', '01c77799-…2262'` |
| `<WINDOW_START>` | `query_start_time` of the earliest run in the window | `2026-10-02 12:19:05.084 -0700` |
| `<WINDOW_RUNS>` | Number of runs in the window (Q1, including runs Q3b covers) | `2` |
| `<PYTHON_MODEL_RELATIONS>` | Lowercase `relation_name` of each Python model, quoted, comma-separated; `NULL` if none | `'tasty_bytes_dbt_db.dev.sales_metrics_by_location'` |
| `<RUN_ID>` / `<RUN_START>` / `<RUN_END>` / `<RUN_USER>` | One run, for Q2 and Q3b | |

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
SELECT query_id AS run_id, query_start_time, query_end_time, command, args, dbt_version
FROM TABLE(<DB>.INFORMATION_SCHEMA.DBT_PROJECT_EXECUTION_HISTORY(
       OBJECT_NAME => '<PROJECT>', DATABASE => '<DB>', SCHEMA => '<SCHEMA>', RESULT_LIMIT => 10000))
WHERE state = 'SUCCESS' AND command IN ('build', 'run')
ORDER BY query_start_time DESC
LIMIT <N_RUNS>;
```
- **Filter with the function's arguments, never only in `WHERE`.** The function returns at most `RESULT_LIMIT` rows
  (default 100) across every dbt project in the account, and `WHERE` runs after that cut. Without the arguments, a
  busy account can push this project's runs out of the result.
- Only `SUCCESS` runs of `build`/`run`: failed runs end early, and `test`/`compile` runs build no models, so neither
  can be compared. Runs whose `args` has `--select`/`--exclude` are partial builds: keep them and say so in the plan
  (Q3 divides by the window's runs, so a model built in fewer runs isn't overstated).
- The error `Inputs may not be null.` means no execution matches: treat it as 0 rows.
- The function only covers the last **7 days**. If it returns fewer than `<N_RUNS>` runs and `ACCOUNT_USAGE` is
  available, run **Q1b** for older runs.
- **No rows at all** (from Q1 and Q1b): run **Q1c** and show the latest failure's `ERROR_MESSAGE`.

The window found here sets four placeholders used from Q3a on: `<RUN_IDS>`, `<WINDOW_START>`, `<WINDOW_RUNS>`,
and (after Q2) `<PYTHON_MODEL_RELATIONS>`.

**Q1b: runs older than 7 days** (`ACCOUNT_USAGE`, kept 365 days). Merge with Q1 by `run_id` and keep the
`<N_RUNS>` most recent:
```sql
SELECT query_id AS run_id, query_start_time, query_end_time, command, args, dbt_version
FROM SNOWFLAKE.ACCOUNT_USAGE.DBT_PROJECT_EXECUTION_HISTORY
WHERE query_start_time >= DATEADD(day, -30, CURRENT_TIMESTAMP())
  AND database_name = '<DB>' AND schema_name = '<SCHEMA>' AND object_name = '<PROJECT>'
  AND state = 'SUCCESS' AND command IN ('build', 'run')
ORDER BY query_start_time DESC
LIMIT <N_RUNS>;
```

**Q1c: latest executions in any state** (only when there's no successful run):
```sql
SELECT query_id, query_start_time, state, command, args, error_message
FROM TABLE(<DB>.INFORMATION_SCHEMA.DBT_PROJECT_EXECUTION_HISTORY(
       OBJECT_NAME => '<PROJECT>', DATABASE => '<DB>', SCHEMA => '<SCHEMA>', RESULT_LIMIT => 10000))
ORDER BY query_start_time DESC
LIMIT 5;
```

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

Q3 looks up each run's parent query in `ACCOUNT_USAGE.QUERY_HISTORY`, so a run that hasn't arrived yet (~45 min
latency) drops out with no error. Run this first to see which runs Q3 will use. It also counts other dbt builds by
the same user that overlap each run (see Q3's "which queries belong to a run"):

```sql
WITH runs AS (
  SELECT f.value::STRING AS run_id FROM TABLE(FLATTEN(INPUT => ARRAY_CONSTRUCT(<RUN_IDS>))) f
),
p AS (
  SELECT r.run_id, h.start_time, h.end_time, h.user_name
  FROM runs r
  LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY h
    ON h.query_id = r.run_id AND h.start_time >= '<WINDOW_START>'::TIMESTAMP_LTZ - INTERVAL '1 minute'
)
SELECT p.run_id, p.start_time, p.start_time IS NOT NULL AS in_account_usage,
       COUNT(o.query_id) AS overlapping_dbt_runs        -- other EXECUTE DBT PROJECT by the same user, same time
FROM p
LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY o
  ON o.user_name = p.user_name AND o.query_id <> p.run_id
 AND o.start_time < p.end_time AND o.end_time > p.start_time
 AND o.query_type = 'CALL' AND o.query_text ILIKE 'EXECUTE DBT PROJECT%'
 AND o.start_time >= '<WINDOW_START>'::TIMESTAMP_LTZ - INTERVAL '1 day'
GROUP BY 1, 2, 3
ORDER BY p.start_time DESC NULLS FIRST;
```
Runs with `in_account_usage = FALSE` go through Q3b. Cross-check with Q3's `runs_used`: it must equal the number of
`TRUE` rows here, and `<WINDOW_RUNS>` minus Q3b's runs.

`overlapping_dbt_runs > 0`: another dbt build ran as the same user during that run. Q3 still separates the two
exactly for `attributed` queries, but a not-yet-attributed query of the other build that carries the same `node_id`
can't be told apart. Name the run in Assumptions.

## Q3: Per-model metrics (Phase 2): primary path via ACCOUNT_USAGE

Applies the credit-sourcing and attribution rules in `scoring-rubric.md` §1–2.

**Which queries belong to a run.** Candidates are the run user's queries inside the run's time window. Of those:
- A query with a `QUERY_ATTRIBUTION_HISTORY` row belongs to the run **only if its `root_query_id` is the run's query
  ID** (the `EXECUTE DBT PROJECT` call). This is exact, and it catches the Python model's uncommented write query too.
- A query with no attribution row yet (attribution lag, result-cache hit, or too short to attribute) belongs to the
  run only if it carries dbt's `node_id` comment, or it's an uncommented write to a Python model's table
  (`<PYTHON_MODEL_RELATIONS>`).
- Everything else (an ad-hoc query by the same user during the build, another tool's writes) is left out.

**Credits per run** are per run **of the window**: a node's total over the window ÷ `<WINDOW_RUNS>`, not an average
over the runs it appeared in. A model built in 1 of 5 runs costs a fifth of its build per run. `runs_built` says how
many runs it was in.

```sql
WITH run_ctx AS (                              -- the window's runs (Q1), with the user that ran each one
  SELECT query_id AS run_id, start_time AS run_start, end_time AS run_end, user_name AS run_user
  FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
  WHERE query_id IN (<RUN_IDS>)
    AND start_time >= '<WINDOW_START>'::TIMESTAMP_LTZ - INTERVAL '1 minute'
),
cov AS (SELECT COUNT(*) AS runs_used FROM run_ctx),  -- runs Q3 actually saw; compare with Q3a
cand AS (                                      -- candidates: the run user's queries during the run
  SELECT c.run_id, c.run_start, h.query_id, h.query_type, h.warehouse_size, h.execution_time, h.total_elapsed_time,
         h.bytes_scanned, h.rows_inserted, h.partitions_scanned, h.partitions_total,
         h.bytes_spilled_to_local_storage AS spill_local, h.bytes_spilled_to_remote_storage AS spill_remote,
         REGEXP_SUBSTR(h.query_text, '"node_id": "([^"]+)"', 1, 1, 'e') AS node_id,
         IFF(node_id IS NULL AND h.query_type IN ('CREATE_TABLE_AS_SELECT','INSERT','MERGE'),
             LOWER(REGEXP_SUBSTR(h.query_text, 'TABLE\\s+(IF\\s+NOT\\s+EXISTS\\s+)?([A-Za-z0-9_."]+)', 1, 1, 'ie', 2)),
             NULL) AS target_relation,          -- Python models: link uncommented writes by table
         a.query_id AS attr_query_id, a.root_query_id,
         a.credits_attributed_compute + COALESCE(a.credits_used_query_acceleration, 0) AS attr_credits
  FROM run_ctx c
  JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY h
    ON h.user_name = c.run_user AND h.start_time BETWEEN c.run_start AND c.run_end
  LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY a
    ON a.query_id = h.query_id AND a.start_time >= '<WINDOW_START>'::TIMESTAMP_LTZ
  WHERE h.start_time >= '<WINDOW_START>'::TIMESTAMP_LTZ
    AND h.query_id <> c.run_id                  -- exclude the parent EXECUTE DBT PROJECT
    AND h.query_type NOT IN ('USE','SHOW','DESCRIBE')
    AND h.query_text NOT ILIKE 'SELECT CURRENT\\_%'
),
q AS (                                         -- keep only this run's queries
  SELECT * FROM cand
  WHERE root_query_id = run_id                  -- attributed: exact link to the run
     OR (attr_query_id IS NULL                  -- not attributed: dbt's own queries only
         AND (node_id IS NOT NULL OR target_relation IN (<PYTHON_MODEL_RELATIONS>)))
),
qc AS (                                       -- per-query credits (rubric §1)
  SELECT q.*,
         COALESCE(q.node_id, 'unattributed:' || COALESCE(q.target_relation, q.query_type)) AS node,
         COALESCE(q.attr_credits,
                  q.execution_time / 3600000 *
                  DECODE(q.warehouse_size,'X-Small',1,'Small',2,'Medium',4,'Large',8,'X-Large',16,
                         '2X-Large',32,'3X-Large',64,'4X-Large',128,'5X-Large',256,'6X-Large',512),
                  0) AS credits,                -- result-cache hits use no warehouse (size NULL): 0, not NULL
         IFF(q.attr_query_id IS NULL, 0, 1) AS is_attributed
  FROM q
),
node_run AS (                                 -- one row per node per run it appeared in
  SELECT node, run_id, MAX(run_start) AS run_start, SUM(credits) AS credits, SUM(bytes_scanned) AS bytes_scanned
  FROM qc GROUP BY 1, 2
),
last_scan AS (                                -- rubric §2 zero-scan rule: the most recent run that scanned data
  SELECT node, MAX_BY(credits, run_start) AS credits
  FROM node_run WHERE bytes_scanned > 0 GROUP BY 1
),
node_cost AS (                                -- per run of the WINDOW, not per run the node appeared in
  SELECT nr.node,
         ROUND(SUM(IFF(nr.bytes_scanned = 0, COALESCE(ls.credits, nr.credits), nr.credits)) / <WINDOW_RUNS>, 5)
                                                                                       AS credits_per_run,
         ROUND(SUM(nr.credits) / <WINDOW_RUNS>, 5)                                     AS raw_credits_per_run,
         COUNT(*)                                                                      AS runs_built,
         COUNT_IF(nr.bytes_scanned = 0 AND ls.credits IS NOT NULL)                     AS zero_scan_runs
  FROM node_run nr LEFT JOIN last_scan ls ON ls.node = nr.node
  GROUP BY 1
)
SELECT qc.node,
       nc.runs_built,                                            -- < <WINDOW_RUNS>: say "built in N of M runs"
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
GROUP BY qc.node, nc.runs_built, nc.credits_per_run, nc.raw_credits_per_run, nc.zero_scan_runs, cov.runs_used
ORDER BY nc.credits_per_run DESC NULLS LAST;
```
`<RUN_IDS>` is the window's run IDs from Q1, quoted and comma-separated; `<WINDOW_START>` is the earliest run's
`query_start_time`; `<WINDOW_RUNS>` is the window size from Q1 (including runs Q3b will cover).
`<PYTHON_MODEL_RELATIONS>` is the lowercase `relation_name` of each Python model in the manifest, quoted and
comma-separated, or `NULL` if there are none.

Python-model write queries come back as `unattributed:<table>`. Match `<table>` to a Python model's `relation_name`
and move the row's metrics to that model. Credits (rubric §2): **drop** them if they're `estimated`, **add** them
if both the write and the outer node query are `attributed` (SKILL.md Phase 2).

`pct_attributed` = 100 → `attributed`; 0 → `estimated`; anything in between → `mixed (N% attributed)`. That's per
node; the plan header is `attributed` only if every node is (rubric §1).

`runs_used` < `<WINDOW_RUNS>` (or no rows at all) means some runs aren't in `ACCOUNT_USAGE` yet. Run Q3b for the
runs Q3a marks `FALSE`, and add their node totals ÷ `<WINDOW_RUNS>` to Q3's `credits_per_run` (Q3 already divides
by the full window). Never report the plan on fewer runs than Q1 found without saying so.

If **every** `node` starts with `unattributed:`, no query carries dbt's comment: per-model attribution isn't possible
(SKILL.md Phase 2 step 2). Only attributed queries (linked by `root_query_id`) come back in that case; rows with a
table name are `table-linked` writes, the rest is `unattributed`. If attribution hasn't caught up either, Q3 returns
no rows for those runs: report the run's total from Q3b's step 1 instead.

## Q3b: Fallback when ACCOUNT_USAGE hasn't caught up with recent runs

`INFORMATION_SCHEMA.QUERY_HISTORY*()` updates immediately but keeps only 7 days, and has **no partition or spill
columns**. Use it for credits and timing only, label everything `estimated`, and mark R2 and R3 "not yet evaluable".
Run it once per run that Q3a marks `in_account_usage = FALSE`.

Like Q1, these functions apply their arguments and `RESULT_LIMIT` before `WHERE`, so filter by user in the
arguments. Step 1 reads the run's user and times from a 2-second window around the run's end, which is small enough
not to hit the limit:
```sql
SELECT user_name, start_time, end_time, execution_time, warehouse_size
FROM TABLE(<DB>.INFORMATION_SCHEMA.QUERY_HISTORY(
       END_TIME_RANGE_START => DATEADD(second, -1, '<RUN_END>'::TIMESTAMP_LTZ),
       END_TIME_RANGE_END   => DATEADD(second, 1, '<RUN_END>'::TIMESTAMP_LTZ),
       RESULT_LIMIT => 10000))
WHERE query_id = '<RUN_ID>';
```
Step 2 takes that user's queries during the run, and keeps dbt's own (same rule as Q3 for unattributed queries).
`USER_NAME` is matched as an identifier: pass the name exactly as step 1 returns it, inside double quotes. Unquoted,
a lowercase name like `jdoe` matches nothing and the step silently returns 0 rows:
```sql
WITH h AS (
  SELECT * FROM TABLE(<DB>.INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER(
         USER_NAME            => '"<RUN_USER>"',          -- exact case from step 1, in double quotes
         END_TIME_RANGE_START => '<RUN_START>'::TIMESTAMP_LTZ,
         END_TIME_RANGE_END   => DATEADD(second, 1, '<RUN_END>'::TIMESTAMP_LTZ),
         RESULT_LIMIT => 10000))
),
c AS (
  SELECT REGEXP_SUBSTR(query_text, '"node_id": "([^"]+)"', 1, 1, 'e') AS node_id,
         IFF(node_id IS NULL AND query_type IN ('CREATE_TABLE_AS_SELECT','INSERT','MERGE'),
             LOWER(REGEXP_SUBSTR(query_text, 'TABLE\\s+(IF\\s+NOT\\s+EXISTS\\s+)?([A-Za-z0-9_."]+)', 1, 1, 'ie', 2)),
             NULL) AS target_relation,
         query_id, query_type, warehouse_size, execution_time, total_elapsed_time, bytes_scanned, rows_inserted
  FROM h
  WHERE query_id <> '<RUN_ID>'
    AND start_time >= '<RUN_START>'::TIMESTAMP_LTZ
    AND query_type NOT IN ('USE','SHOW','DESCRIBE')
    AND query_text NOT ILIKE 'SELECT CURRENT\\_%'
)
SELECT * FROM c
WHERE node_id IS NOT NULL OR target_relation IN (<PYTHON_MODEL_RELATIONS>);   -- dbt's own queries only
```
Aggregate per `node_id` (or `unattributed:<target_relation>`) with the same credit formula, Python-model and
zero-scan rules as Q3, and divide node totals by `<WINDOW_RUNS>`. Rows from Q3b are always `estimated`.

If no query has a `node_id` (dbt's comment is off), step 2 can't tell dbt's queries from others: report the run's
total as `execution_time` of the parent query from step 1 × the warehouse rate, labelled `estimated` and
"includes orchestration", and nothing per model.

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
