# dbt Cost Tuning: Metrics & Scoring Rubric

Single source of truth for how the skill measures models and ranks recommendations.
All weights and thresholds live in the tables below so they can be tuned without
touching the workflow in `SKILL.md`.

---

## 1. Credit sourcing (per query)

| Priority | Source | Credits used | Label |
|---|---|---|---|
| 1 | `SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY` | `credits_attributed_compute + COALESCE(credits_used_query_acceleration, 0)` | `attributed` |
| 2 | `QUERY_HISTORY` (`INFORMATION_SCHEMA` table function if < 7 days old, else `ACCOUNT_USAGE`) | `execution_time / 3,600,000 × warehouse_credits_per_hour` | `estimated` |

Rules:
- Join on `query_id`. Use source 1 whenever a row exists; fall back to source 2 per query, not per model.
- Every model-level number carries a source label: `attributed` (100% of its queries), `estimated` (0%), or `mixed (N% attributed)`.
- The report states which source was used and why (e.g. "QUERY_ATTRIBUTION_HISTORY has not caught up for runs in the last ~8h").
- Only compare models on the same basis. If one model is `attributed` and another `estimated`, rank them but flag the comparison.

Why source 2 is only an estimate:
- It assumes the query had the warehouse to itself. dbt runs models concurrently (`threads`), so this **overstates** credits.
- It ignores idle time and per-second minimums, so the totals won't match `WAREHOUSE_METERING_HISTORY`.
- `QUERY_ATTRIBUTION_HISTORY` leaves out very short queries, so those always fall back to the estimate. They're cheap and don't change the ranking.

Warehouse credits per hour (standard warehouses):

| XS | S | M | L | XL | 2XL | 3XL | 4XL | 5XL | 6XL |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2 | 4 | 8 | 16 | 32 | 64 | 128 | 256 | 512 |

Snowpark-optimized or Gen2 warehouses bill at a higher rate. If one is detected, use the
table above and lower confidence on the estimate instead of guessing a multiplier.

---

## 2. Model attribution

- Each dbt query ends with dbt's JSON comment, because `query-comment: append: true` is set. Extract the model with
  `REGEXP_SUBSTR(query_text, '"node_id": "([^"]+)"', 1, 1, 'e')`.
- **No comments at all** (`query-comment: null`, or a prepended comment, which Snowflake strips): no query has a
  `node_id`, so credits can't be attributed to models. Report total credits, `table-linked` writes (an uncommented
  `CREATE_TABLE_AS_SELECT`/`INSERT`/`MERGE` matched to a `relation_name` by its target; that one query only) and
  `unattributed`. Never split totals across models. The fix is `query-comment: {append: true}` in `dbt_project.yml`.
- Group per `node_id` over the analysis window (default: the last 5 successful runs, or 7 days).
- **Exclude the parent `EXECUTE DBT PROJECT` query** (`query_type = 'CALL'`). It runs for the whole build, so counting it would double-count every model's queries. With `estimated` credits, leave it out of all totals. With `attributed` credits, report it separately as dbt orchestration overhead.
- **Python models:** the comment lands on the outer node query (a stored-procedure call), but the Snowpark query that actually writes the table (`CREATE ... TABLE <db>.<schema>.<model>`) has no comment. The outer query's time already includes that write query.
  - Credits: for `estimated`, use the outer node query only. For `attributed`, add the outer query's credits to its children's (`QUERY_ATTRIBUTION_HISTORY.parent_query_id`). Never add the uncommented write query's estimate on top, because that double-counts.
  - Metrics (rows, bytes, spill): link the uncommented `CREATE_TABLE_AS_SELECT`/`INSERT`/`MERGE` to the model by matching its target table against `relation_name` in `manifest.json`.
- **Zero-scan executions:** a `SELECT` with `bytes_scanned = 0` was answered either from the result cache (same query, unchanged data) or from table metadata (e.g. a `not_null` check on a column whose statistics prove it). Label it `zero-scan`. For cost, use the most recent execution that did scan data, and say so: in production, data changes between runs, so the result cache usually won't apply.
  Apply this **per node and per run, before computing any share or threshold** (Q3 does it). A node that never scanned in the window keeps its actual (near-zero) cost.
  Verified failure mode: averaging the Tasty Bytes cache hits in as zero put the `ORDER_DETAIL` relationships test at 2.7% instead of ~5%, so R6 wrongly missed.
- **Ignore session setup** (`USE ...`, `SELECT CURRENT_*()`, `SHOW ...`). These are hundreds of near-zero queries per run, and including them only adds noise.

Verified on the Tasty Bytes builds: the parent CALL made up about 47% of the raw estimated credits; the Python model's write query had no comment; and the second build's tests all came from cache. The first `relationships` test on `order_detail` took 11.3s and scanned 5.2 GB; in the second build it took 0.2s and scanned 0 bytes.

### Per-model metrics

`INFORMATION_SCHEMA.QUERY_HISTORY()` has no partition or spill columns. If `ACCOUNT_USAGE` hasn't caught up yet
(about 45 min), mark R2 and R3 as "not yet evaluable" instead of scoring them as zero.

| Metric | Source column(s) | Used for |
|---|---|---|
| Credits per run | §1 | Impact |
| Share of dbt credits (%) | model credits ÷ all dbt query credits in window | Impact |
| Elapsed time per run | `total_elapsed_time` | Critical path, report |
| Pruning ratio | `partitions_scanned / NULLIF(partitions_total, 0)`, **`ACCOUNT_USAGE.QUERY_HISTORY` only** | R2 |
| Local / remote spill | `bytes_spilled_to_local_storage`, `bytes_spilled_to_remote_storage`, **`ACCOUNT_USAGE.QUERY_HISTORY` only** | R3 |
| Rows produced, bytes scanned | `rows_produced`, `bytes_scanned` | R1, R6 |
| External reads | `ACCOUNT_USAGE.ACCESS_HISTORY` (excluding dbt's own queries) | R4 |
| Downstream dependents | `manifest.json` `child_map` | R4, R5, effort |

---

## 3. Scoring model

Each candidate recommendation (model × rule) gets three scores:

```
Signal   (0–100)  how strongly the evidence supports the rule      → acts as confidence
Impact   (0–100)  estimated % of total dbt credits saved per run
Effort   (1–5)    weighted cost of making and validating the change

Priority = Impact × (Signal / 100) ÷ Effort
```

A rule **fires** only if every gate passes and Signal ≥ **60**.

Signal is the sum of the rule's evidence weights that counted (§4), so every Signal must be reachable from those
weights. Show the sum in the report, row by row. Placement:

| Signal | Section |
|---|---|
| ≥ 60, gates pass | Recommendations |
| 40–59 | Observed, below threshold, naming each missing row and its weight (e.g. "remote spill +50 → 100") |
| < 40, or a gate fails | "Evaluated, didn't fire" line |

### Impact

```
est_credits_saved_per_run = model_credits_per_run × savings_factor(rule)
Impact                    = est_credits_saved_per_run ÷ total_dbt_credits_per_run × 100
```

If the user gives a run frequency, also show monthly savings (`× runs per month`). Otherwise ask, or show per run only.
Savings factors are deliberately conservative defaults. Always present them as estimates.

### Effort (weighted, 1–5)

| Factor | Weight | 1 | 3 | 5 |
|---|---|---|---|---|
| Change scope | 50% | Config only (`cluster_by`, `materialized`, test `where`) | SQL rewrite of one model | Restructure across several models |
| Blast radius | 30% | 0 downstream dependents | 3–5 dependents | > 10 dependents, or exposures |
| Validation risk | 20% | No change to results | Needs a full-refresh comparison or backfill | Removes an object; needs owner sign-off |

Blast radius scale: 0→1, 1–2→2, 3–5→3, 6–10→4, >10→5.
`Effort = 0.5 × scope + 0.3 × blast + 0.2 × risk`, rounded to one decimal.

### Plan quadrants

| | Effort ≤ 2 | Effort > 2 |
|---|---|---|
| **Impact ≥ 5** | Quick win: do first | Big bet: plan it |
| **Impact < 5** | Fill-in: batch with other changes | Deprioritize: don't recommend |

---

## 4. Rules

For every rule, the Signal weights add up to 100. Gates must pass before scoring.

### R1: Make it incremental
Gate: `materialized = table` (full rebuild every run).

| Evidence | Weight |
|---|---|
| Model is large: ≥ 10M rows or ≥ 1 GB | 30 |
| Model reads a monotonic event timestamp or ID column (e.g. `*_ts`, `*_at`, increasing ID) | 30 |
| A unique key is **verified** on the built model (Q6: `COUNT(*) = COUNT(DISTINCT key)`, 0 nulls, and rows written ≤ grain-table rows) | 20 |
| SQL can be processed incrementally (no full-history window functions or aggregations without a partition key) | 20 |

Savings factor: **0.8**, assuming most data is unchanged between runs. Effort defaults: scope 3, risk 3.

Fan-out check: if the model writes more rows than its grain table, or Q6 finds duplicate keys, report it under
"Data-quality findings" with the duplicated join key. Verified on Tasty Bytes: `orders` wrote 693,681,576 rows from
673,655,465 `order_detail` rows because `raw_pos_franchise` has 335 rows for 325 `franchise_id`s.

`delete+insert` on a coarser key (e.g. `order_id`) is allowed under fan-out, but the key still goes through Q6: 0 nulls
in the model, unique in its source table, model distinct count ≤ source rows. It doesn't earn the 20 points above,
because the model's rows still aren't unique.

### R2: Add a cluster key
Gate: table is ≥ 1 GB.

| Evidence | Weight |
|---|---|
| Queries reading this model scan ≥ 80% of its partitions | 35 |
| Those reads filter or join on the same column(s) | 25 |
| Table has ≥ 1,000 micro-partitions | 25 |
| Candidate column's cardinality is moderate (not unique, not a handful of values) | 15 |

Savings factor: **0.3** of the downstream read credits, not the model's own build. Note that automatic clustering adds serverless cost. Effort defaults: scope 1, risk 1.

Verified on Tasty Bytes: `ORDERS` has 464 micro-partitions (< 1,000) and one outside read, so R2 scores at most
35 (scan ratio only), which puts it on the "Evaluated, didn't fire" line, not under "Observed, below threshold".

### R3: Fix spilling
Gate: some spill observed.

| Evidence | Weight |
|---|---|
| Remote spill > 0 | 50 |
| Spilling happens in ≥ 2 runs | 30 |
| Local spill > 1 GB | 20 |

Savings factor: **0.3**. Recommend refactoring first (filter earlier, pre-aggregate, simplify joins). A larger warehouse just for this model is the fallback. Effort defaults: scope 3, risk 2.

A near miss names the weight that's missing, not a fixed threshold. Verified on Tasty Bytes: `orders` spills
12.45 GB locally in both runs, so Signal = 30 + 20 = 50; "remote spill (+50) would bring it to 100".

**Design note: why there's no warehouse-sizing rule.** A rule like "large local spill on a full rebuild → test one
size up" was considered and left out, because R1 is the better fix for the same evidence:
- The spill comes from rebuilding the whole table. R1 makes each run process a few days of rows instead of all
  history, so the working set (and the spill) shrinks with it. Upsizing keeps the full rebuild and pays more for it.
- One size up doubles the credit rate. It only breaks even if runtime halves, and local spill alone doesn't
  predict that: local spill slows a query far less than remote spill, which R3 already weights at 50.
- A dbt project usually shares one warehouse, so sizing up hits every model, not just the one that spills. A
  per-model `snowflake_warehouse` config avoids that, but adds a warehouse to run and monitor.
- The skill is read-only and never resizes, so it can't measure the trade-off. It would be guessing.
So when R1 fires on a model that also spills, the plan says the spill should drop after the incremental change,
and the verify step re-checks `max_spill_local_gb` on the next run. A bigger warehouse stays R3's last-resort
fallback for a model that can't be made incremental.

### R4: Drop or disable an unused model
Gate: `ACCESS_HISTORY` is available. If it isn't, report "cannot assess" instead of guessing.

| Evidence | Weight |
|---|---|
| Nothing outside dbt reads it within the window | 50 |
| No downstream dbt models depend on it | 30 |
| Not referenced by an exposure, and not tagged or documented as public | 20 |

Savings factor: **1.0** of its build credits, plus storage. Effort defaults: scope 1, risk 5.

False-positive guard: in a dev schema or a new account, nothing reads the final models yet, so every one of them looks unused.
- With less than **30 days** of history, or when the analyzed target is a dev target, cap Signal at **50**. That's below the fire threshold, so the rule never recommends dropping a model in that case.
- List those models under "Needs owner review: no readers observed in N days" instead.
- Never let R4 outrank R1–R3 on the same model.

### R5: Materialize a heavy view
Gate: `materialized = view`.

| Evidence | Weight |
|---|---|
| Dependent models together use ≥ 10% of dbt credits | 40 |
| The view does real work (joins or aggregations, not just selecting and renaming columns) | 30 |
| ≥ 2 downstream dependents | 30 |

Savings factor: **(N − 1) / N** of the view's work, where N is the number of dependents. Effort defaults: scope 1, risk 2.

### R6: Reduce test cost
Gate: the node is a test.

| Evidence | Weight |
|---|---|
| This test uses ≥ 5% of the run's dbt credits, after zero-scan costing (§2) | 40 |
| The test scans a table with ≥ 100M rows | 30 |
| Test type needs a full scan (`unique`, `relationships`) | 30 |

Savings factor: **0.7**, from a `where` filter on recent data or running the test less often. Effort defaults: scope 1, risk 2.

---

## 5. Required in every report

- Credit source label per model, plus a one-line explanation of the source mix.
- Signal, Impact and Effort for every fired rule, and which pieces of evidence counted.
- Rules that could not be evaluated (missing privilege, view, or history) and what's needed to enable them. Rules that were evaluated and didn't fire go on one "Evaluated, didn't fire" line instead.
- Any `unique_key` in a proposed diff has been verified with Q6, including a `delete+insert` key (nulls and distinct count). Any join fan-out found is reported under "Data-quality findings".
- The runs actually used: if Q3 saw fewer runs than Q1 found, which ones came from Q3b (`estimated`, R2/R3 not yet evaluable).
- If no query carries a `node_id`: say per-model attribution isn't possible, and give the `query-comment` fix.
