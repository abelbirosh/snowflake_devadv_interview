# Findings: TC01 run-20261003T223318Z

Second run on `skill_sha` f43d60e0f1f8, same 2-run baseline as [run-20261003T221608Z](../run-20261003T221608Z/findings.md).
Graded by Claude from the transcript, screenshots and telemetry; reviewed with the user.

**Result: Pass (20/20 auto, 14/14 manual).** Both misses from the previous run on this version were not repeated.

## Previous run's misses: before → after

| Miss in run-20261003T221608Z | That run | This run | Evidence |
|---|---|---|---|
| R5 placement | #3 under Recommendations (Signal 70, Impact ~0, "not recommended") | ✅ "Evaluated, didn't fire: … R5 on staging views (pass-through `SELECT *`, zero view credits)" | transcript l.267 |
| "Not evaluable" heading | Missing | ✅ "None — all views and capabilities were available." | `05b-plan-details.png` |

Neither was fixed in the skill (no skill change between the runs), so both are **non-deterministic**, not solved. The R5 and
section-heading fixes proposed in the previous run's findings still apply.

## Expected vs observed

| Area | Expected | Observed | Result |
|---|---|---|---|
| Checkpoint | Waits before Q1 | Asked via Cortex's `ask_user_question` picker (run frequency options) instead of ending the turn in text; Q1 ran only after the answer. Telemetry counts 1 user turn because the reply went through the picker | ✅ `02-checkpoint.png`, `03-checkpoint-answered-q1.png` |
| Window | 2 runs | 2, both via `ACCOUNT_USAGE` | ✅ |
| Credit source | Labelled with reason | `attributed`: QAH covers the top models 100%; tests mixed (50%, cache hit in one run) | ✅ |
| Cost table | `orders` #1 | 0.12848 (87.4%) of 0.14709/run | ✅ |
| R1 | #1, diff, path, lookback | `models/marts/orders.sql`, `delete+insert` on `order_id`; Q6: 0 nulls, 248,201,269 = `raw_pos_order_header` rows; 3-day lookback; `--full-refresh`; Signal 30+30+0+20 = 80 | ✅ `05a-plan-r1.png` |
| Fan-out | Reported | 693,681,576 vs 673,655,465; `raw_pos_franchise` 335 rows / 325 ids | ✅ |
| R6 | Fires | #2, Signal 40+30+30 = 100, 6.3%; `where` limited to orders from the last 30 days | ✅ |
| R3 | Below threshold, 50 | 30 + 20 = 50; "remote spill (+50) would bring it to 100" | ✅ |
| R4 | Needs owner review | `customer_loyalty_metrics`, `sales_metrics_by_location` | ✅ |
| R2 / R5 | "Evaluated, didn't fire" | R2 Signal 35 (464 partitions < 1,000); R5 pass-through views | ✅ |
| Read-only / cost | 0 violations, bounded | 0 violations; 7 `ACCOUNT_USAGE` queries, all bounded; 49 queries; ~0.009 analysis credits | ✅ |

Spot-checked: 693,681,576 rows, 248,201,269 `ORDER_HEADER` rows, `ORDERS` 15,674,380,288 bytes. All match baseline_runs.md.

## Surprises

- **Python model credits: this run summed the write query; the previous run dropped it.** `sales_metrics_by_location`
  = outer node query 0.0006 + write query 0.00084 = 0.00144. The rubric says to drop the write query's credits because
  they're "already inside the outer node query". That's true for `estimated` credits (the outer query's elapsed time
  covers the write). It isn't true for `attributed` credits: QUERY_ATTRIBUTION_HISTORY attributes each query on its own,
  and Snowflake's docs say to sum by `root_query_id` to get a procedure's full cost. So with attributed credits this run
  is correct and the previous run undercounted by 0.00084/run (0.6%). Graded as passing; the rubric needs the
  distinction.
- Q4 now shows **3** outside reads of `ORDERS` (previous run: 1). The 2 new ones are the previous run's own Q6 checks:
  Q4 doesn't exclude the skill's earlier analysis queries. R2 stayed at 35, because full-scan `COUNT` queries add no
  filter-column evidence.
- The source Q6 guessed `TASTY_BYTES_DBT_DB.RAW_POS` again (1 failed query) before finding the real location. 2 more
  failures against `CORTEX_CODE` came from Cortex Code itself.

## Skill change needed (file + section)

Hold until run 3. Adds to the previous run's list:

5. scoring-rubric.md §2 Python models (and SKILL.md Phase 2 step 3): drop the write query's credits only when the outer
   node's credits are `estimated`. When both are `attributed`, add them.
6. queries.md Q4: also exclude queries whose text matches the skill's own analysis queries (e.g. a `-- dbt-cost-tuning`
   tag the skill adds to every query it runs), so repeated analyses don't count as outside reads.

## Test-plan change needed

- cases.json TC01 manual check "Python write query's credits not double-counted" → "Python write query handled per
  credit source (dropped if estimated, added if attributed)".
