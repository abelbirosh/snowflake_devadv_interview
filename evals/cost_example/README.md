# End-to-end example: `orders`, from recommendation to measured saving

One recommendation from the `dbt-cost-tuning` skill, followed all the way through: the costly model, the evidence the
skill gave, the change it proposed, and the cost measured before and after the change was deployed and run.

| Part | Where |
|---|---|
| The costly model and the evidence | TC01 plans, e.g. [`runs/TC01/run-20261004T225003Z`](../runs/TC01/run-20261004T225003Z/transcript.md) |
| The proposed change | [`orders_incremental.diff`](orders_incremental.diff) |
| The measurement query | [`measure.sql`](measure.sql) |

## 1. The costly model

On the two baseline builds (2026-10-02, dbt 1.10.15, `SMALL` warehouse), `orders` is 87% of the build's credits:

| Model | Credits/run | Share | Avg time | Rows written | Local spill |
|---|---|---|---|---|---|
| `orders` (table) | 0.12848 attributed (0.09439 estimated) | 87.3% | 170.9 s | 693,681,576 | 12.45 GB |
| Whole build | 0.14709 | | ~205 s | | |

## 2. The evidence

R1 (make it incremental) fires with Signal 80 of 100:

| Evidence | Weight | Counted | Detail |
|---|---|---|---|
| Large model (≥ 10M rows or ≥ 1 GB) | 30 | ✓ | 694M rows, 15.7 GB |
| Monotonic event timestamp survives to the output | 30 | ✓ | `order_ts` from `ORDER_HEADER` |
| Unique key verified (Q6) | 20 | ✗ | Join fan-out: 694M rows for 674M order lines (a duplicated `franchise_id` adds 20M rows), so no column is unique per row |
| SQL can be processed incrementally | 20 | ✓ | Joins only, no full-history aggregation |

Impact: 0.12848 × 0.80 savings factor = **0.103 credits/run predicted (80%)**. Effort 2.4.

Because of the fan-out, the skill proposes `delete+insert` on `order_id` rather than `merge` on a row key. It checked
that `order_id` has 0 nulls in the model and is unique in `ORDER_HEADER` (248,201,269 rows and distinct values).

## 3. The proposed change

[`orders_incremental.diff`](orders_incremental.diff): `materialized='incremental'`, `incremental_strategy='delete+insert'`,
`unique_key='order_id'`, and an `is_incremental()` filter that reprocesses the last 3 days of `order_ts`:

```sql
{% if is_incremental() %}
WHERE oh.order_ts > (SELECT DATEADD(day, -3, MAX(order_ts)) FROM {{ this }})
{% endif %}
```

The skill only proposes this. The change was applied by hand to a copy of the project, deployed as a separate dbt
project object (`TASTY_BYTES_DBT_DB.COST_DEMO.TASTY_BYTES_DBT`, target schema `COST_DEMO`) so the DEV baseline the
evals grade against stays untouched.

## 4. Measured before and after

Same warehouse (`SMALL`, 60 s auto-suspend), same source data. Four builds on 2026-10-04:

1. `build --full-refresh`: rebuilds `orders` in full, the same work as the table model. This is the same-day "before".
2. `build`: the first incremental run, straight after the full refresh.
3. `build`: the second incremental run, warehouse still warm.
4. `build`: the third incremental run, after the warehouse auto-suspended (a cold start, as a daily schedule would see).

All credits below are `estimated` (execution time × 2 credits/h), so before and after are on the same basis.
`QUERY_ATTRIBUTION_HISTORY` hadn't reached the 2026-10-04 builds yet; re-run [`measure.sql`](measure.sql) after it
does (up to ~8 h) for `attributed` figures. Source: `ACCOUNT_USAGE.QUERY_HISTORY` via [`measure.sql`](measure.sql).

| `orders` per run | Baseline (DEV, table, avg of 2) | Full refresh | Incremental 1 (first after refresh) | Incremental 2 (warm) | Incremental 3 (cold) |
|---|---|---|---|---|---|
| Credits | 0.09439 | **0.09615** | 0.03370 (−65%) | **0.00678 (−93%)** | **0.00773 (−92%)** |
| Execution time | 169.9 s | 173.1 s | 60.6 s | 12.2 s | 13.9 s |
| Rows written | 693.7M | 693.7M | 3.63M (3.63M deleted) | 3.63M (3.63M deleted) | 3.63M (3.63M deleted) |
| Local spill | 12.44 GB | 12.57 GB | 0 | 0 | 0 |
| GB scanned (all `orders` queries) | 4.88 | 4.88 | 19.53 | 3.83 | 3.88 |
| **Whole build credits** | 0.11494 | 0.11718 | 0.04327 | 0.01831 | 0.02105 |
| Whole build wall time | 206 s | 201 s | 79 s | 33 s | 34 s |

**Predicted vs measured** (same `estimated` basis, against the same-day full refresh):

| | Per run | Per month (daily) |
|---|---|---|
| Predicted saving (80% savings factor) | 0.0769 credits | 2.31 credits |
| Measured saving, steady state (incremental 2–3) | **0.0889 credits (92.5%)** | **2.67 credits** |
| One-time extra cost of the first run after each `--full-refresh` | 0.0264 credits | |

The whole build drops 83% (0.117 → 0.020 credits) and its wall time from 201 s to 33 s. The four demo builds cost
0.20 credits in total.

## 5. Correctness

Tasty Bytes' raw data doesn't change, so after the incremental runs the table must equal the full rebuild. It does:

| Table | Rows | Distinct `order_id` | Distinct `order_detail_id` | `SUM(order_total)` | `SUM(price)` |
|---|---|---|---|---|---|
| `DEV.ORDERS` (full rebuild) | 693,681,576 | 248,201,269 | 673,655,465 | 36,277,851,849 | 10,380,528,275.25 |
| `COST_DEMO.ORDERS` (after the 3 incremental runs) | 693,681,576 | 248,201,269 | 673,655,465 | 36,277,851,849 | 10,380,528,275.25 |

`delete+insert` on `order_id` handles the fan-out: every line of a reprocessed order, duplicates included, is deleted
and re-inserted together.

## 6. What the measurement showed

1. **The recommendation holds, and the steady-state saving beats the prediction**: 92.5% measured vs 80% predicted,
   with the 12.6 GB spill gone. Cold (incremental 3, after auto-suspend) and warm (incremental 2) runs cost the
   same, so the saving doesn't depend on the warehouse cache.
2. **The first incremental run after a full refresh is a transition cost** (−65% only). Its DELETE read 15.75 GB,
   all 480 partitions in full (operator stats), and took 52 of its 61 s. The full refresh writes `ORDERS` unordered
   (clustering depth 453 of 476 partitions for `order_ts`), so the 3-day lookback's orders sit in every partition.
   The first INSERT puts them into ~18 new partitions; from then on the Delete operator reads 65 MB. This repeats
   after every `--full-refresh`.
3. **One recurring cost grows with the table.** Every DELETE finds its rows by scanning `order_id` in every
   partition (~1.05 GB, 476 of 476, no pruning): 94% of the cold DELETE's time. Not tested: an
   `incremental_predicates` filter on `DBT_INTERNAL_DEST.order_ts`, with the full refresh written in `order_ts`
   order so old partitions can be pruned.
4. **The incremental filter doesn't prune the source.** The temp-table build still scans all 261 source
   partitions. The saving comes from joining and writing 3.6M rows instead of 694M, not from reading less.
5. **Re-running the skill right after the change would misreport it.** The fixed Q3 on the four COST_DEMO builds
   reports `orders` at 0.0359 credits/run, 5× the steady state, because the window includes the full refresh and
   the transition run. Measure from the second incremental run on (open item S15 in
   [`fix_list.md`](../fix_list.md)).

## 7. Caveats

- **Static data.** Tasty Bytes' raw tables never change, so each incremental run reprocesses the last 3 days
  (1.30M of 248M orders, 3.63M lines) and writes the same rows back. A real daily run also appends the new day.
- **dbt version.** `snow dbt deploy` without `--dbt-version` gave COST_DEMO dbt 1.9.4; the DEV baseline ran
  1.10.15. Before and after are compared within COST_DEMO (same version), and its full refresh matches the
  baseline (173.1 s vs 169.9 s).
- **`estimated` credits** assume each query had the warehouse to itself, so they overstate cost under dbt's
  8 threads. On the baseline, attributed credits were 1.36× the estimate for `orders`; the demo's attributed
  figures aren't in yet.

