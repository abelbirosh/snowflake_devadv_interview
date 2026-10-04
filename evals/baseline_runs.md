# Baseline dbt runs: Tasty Bytes (2026-10-02)

These two `dbt build` runs are the input data for the `dbt-cost-tuning` skill's happy-path evaluation.

**Setup:** dbt project object `TASTY_BYTES_DBT_DB.DEV.TASTY_BYTES_DBT`, dbt 1.10.15, `--target dev`, warehouse `TASTY_BYTES_DBT_WH` (SMALL, 2 credits/h).
Command: `snow dbt execute ... tasty_bytes_dbt build --target dev`.

**Source data:** `RAW.ORDER_DETAIL` 673,655,465 rows · `RAW.ORDER_HEADER` 248,201,269 rows · `CUSTOMER_LOYALTY` 222,540 rows.

## Runs

| Run | Run query ID | Start (PDT) | End | Duration | State |
|---|---|---|---|---|---|
| 1 | `01c77787-0208-c3ea-0011-5b520001f266` | 12:19:05 | 12:22:42 | 217 s | SUCCESS |
| 2 | `01c77799-0208-bb91-0011-5b5200022262` | 12:37:10 | 12:40:23 | 193 s | SUCCESS |

## Per-model results

Credits are **estimated**: `execution_time × 2 credits/h`. `QUERY_ATTRIBUTION_HISTORY` had no rows yet.
The parent `EXECUTE DBT PROJECT` query and session setup queries (`USE`, `SHOW`, `SELECT CURRENT_*`) are excluded.

| Node | Run 1 time | Run 1 est. credits | Run 2 time | Run 2 est. credits | Rows written | Local spill (GB) |
|---|---|---|---|---|---|---|
| `orders` (table) | 178.7 s | 0.0988 | 163.1 s | 0.0900 | 693,681,576 | 12.45 / 12.43 |
| `sales_metrics_by_location` (Python) | 14.2 s | 0.0076 | 14.5 s | 0.0077 | 13,093 ¹ | 0 |
| `customer_loyalty_metrics` (table) | 10.0 s | 0.0053 | 10.5 s | 0.0055 | 222,540 | 0 |
| 50 tests | 24.9 s | 0.0096 | 9.9 s | ~0 ² | n/a | 0 |
| 8 staging views | ~0.4 s each | ~0 | ~0.4 s each | ~0 | n/a | 0 |
| Other (incl. Python write query) | 11.1 s | 0.0010 | 12.5 s | 0.0010 | 13,093 | 0 |
| **Total** | | **≈ 0.122** | | **≈ 0.104** | | |

¹ The Python model's outer query writes no rows. Its uncommented Snowpark `CREATE TABLE` query (counted under "Other") writes the 13,093 rows.
² Run 1: 41 of 50 tests scanned 0 bytes (answered from table metadata). Run 2: all 50 scanned 0 bytes (result cache plus metadata).
The `relationships` test on `ORDER_DETAIL.ORDER_ID` took 11.3 s and scanned 5.2 GB in run 1, then 0.2 s and 0 bytes in run 2.

No queries spilled to remote storage. `orders` scanned 100% of its source partitions in both runs, as expected for a full rebuild.

## Expected skill outcomes on this baseline

| Rule | Expected | Basis |
|---|---|---|
| R1 Incremental | **Fires on `orders`** (top priority), Signal 80, without claiming `order_detail_id` is unique | Full-rebuild table, 694M rows, 15.7 GB, ~80% of credits, `order_ts` event column. Unique-key evidence scores 0: see fan-out below |
| Data quality | **Join fan-out in `orders` reported** | `orders` writes 693,681,576 rows from 673,655,465 `ORDER_DETAIL` rows (20.0M duplicate `order_detail_id`), because `RAW_POS_FRANCHISE` has 335 rows for 325 `franchise_id`s. Verified 2026-10-02 |
| R6 Test cost | **Fires** on the `ORDER_DETAIL` relationships test | 0.00609 credits/run after zero-scan costing = 5.2% of 0.1171 (2.7% if cache hits are averaged in as 0, which is the TC01 run-1 bug), 674M-row table, full-scan test type |
| R3 Spill | Observed, below threshold (Signal 50) | > 1 GB local spill in 2 of 2 runs, no remote spill |
| R4 Unused | Under "Needs owner review" (not "Not evaluable") | ~1 day of account history and a dev target, so the 30-day guard caps it |
| R2 Cluster key | Doesn't fire; on the "Evaluated, didn't fire" line, Signal ≤ 35 | `ORDERS` has 464 micro-partitions (< 1,000) and reads don't share a filter column, so only the scan-ratio row (35) can count. Outside reads did appear after 2026-10-02 (the manual baseline check, then untagged Q6 runs of earlier skill versions), which doesn't change the score |
| R5 Heavy view | Doesn't fire; on the "Evaluated, didn't fire" line (gate failed) | Staging views only select and rename, so they fail R5's "does real work" gate (rubric R5) |

Cost basis (new Q3, verified on these runs): 63 nodes incl. 50 tests; raw 0.1133 credits/run, 0.1171 after zero-scan costing,
0.1161 after also dropping the Python write query's credits. `orders` ≈ 81%. TC05 (no `ACCOUNT_USAGE`, Q3b only)
reproduced these figures: 0.11709/run, `orders` 0.09439 (80.6%), relationships test 0.00609 (5.2%).

## Attributed credits (from 2026-10-03)

`QUERY_ATTRIBUTION_HISTORY` caught up on 2026-10-03 (latest row 12:36 PDT), so runs with `ACCOUNT_USAGE` access
report **attributed** credits from then on. Same ranking, different numbers:

| Node | Credits/run (attributed) | Share | Estimated basis, for comparison |
|---|---|---|---|
| `orders` | 0.12848 | 87.4% | 0.09439 (80.6%) |
| `relationships` test on `ORDER_DETAIL.ORDER_ID` | 0.00924 (cache hit in run 2, costed at run 1) | 6.3% | 0.00609 (5.2%) |
| `customer_loyalty_metrics` | 0.00671 | 4.6% | 0.00540 |
| `sales_metrics_by_location` (Python) | 0.00144 = outer 0.00060 + write query 0.00084 | 1.0% | 0.00767 (outer query only) |
| **Total** | **0.14709** | | 0.11709 |

- Header label: `mixed`. The models are 100% attributed, but run 2's cache-hit tests have no attribution row.
- Python model: with attributed credits the write query is **added** (rubric §2). Runs before the S3 fix dropped it
  (total 0.14625, `orders` 87.9%).
- Estimated credits overstate the Python model most (6.6% vs 1.0%): its outer query mostly waits on Snowpark, and
  execution time × warehouse rate charges that wait.

## Findings that changed the skill

1. The parent `EXECUTE DBT PROJECT` query spans the whole build. Including it roughly doubles the estimated credits, so it's excluded.
2. The Python model's write query has no dbt `node_id` comment. It's linked to the model by target table, for metrics only.
3. `INFORMATION_SCHEMA.QUERY_HISTORY` has no partition or spill columns, so R2 and R3 need `ACCOUNT_USAGE`.
4. `ACCESS_HISTORY.base_objects_accessed` resolves views to `RAW` tables, so model reads must use `direct_objects_accessed`.
5. Zero-scan executions (cache or metadata) would make tests look free. The skill costs them by their most recent execution that scanned data.
