# Findings: TC05 run-20261003T233904Z

TC05 (role with no `SNOWFLAKE.ACCOUNT_USAGE` access → `INFORMATION_SCHEMA` fallback) on `skill_sha` f43d60e0f1f8, role
`DBT_TUNING_EVAL_RESTRICTED` (setup: `01_restricted_roles.sql`, secondary roles off). Graded by Claude from the
transcript, screenshots and telemetry; reviewed with the user.

**Result: Pass (9/9 auto, 4/4 manual).** The skill degraded instead of stopping, and its estimated numbers match the
independently verified baseline.

## Expected vs observed

| Check | Expected | Observed | Result |
|---|---|---|---|
| Probes | Each `ACCOUNT_USAGE` probe fails on its own | 4 probes (history depth, QAH, QUERY_HISTORY, ACCESS_HISTORY) each failed with "not authorized"; matrix shows all 3 views "Not authorized", history depth "Unknown" | ✅ `01-probes-fail.png`, `02-checkpoint-capability-matrix.png` |
| Continue | Falls back, doesn't stop | "I'll fall back to `INFORMATION_SCHEMA` (7-day window, no spill/pruning data)"; checkpoint lists R1/R5/R6 evaluable, R2/R3/R4 not | ✅ |
| Credit label | All `estimated` | Header `estimated`, with the reason (no `ACCOUNT_USAGE`; execution_time × warehouse rate; overstates under concurrency); every row `estimated` | ✅ `03-plan-top-estimated.png` |
| Window | Q3b, 2 runs | "2 successful runs … (both via Q3b — `INFORMATION_SCHEMA`)" | ✅ |
| R1 | Still fires on `orders` | #1, Signal 30+30+0+20 = 80, Impact 64.5%; `delete+insert` on `order_id` with Q6 (0 nulls, 248,201,269 = `ORDER_HEADER`); `ref()`s kept; `--full-refresh` | ✅ `04-plan-r1-r6.png` |
| R2/R3/R4 | "Not evaluable" with the grant | Table: R2, R3 → "Grant `IMPORTED PRIVILEGES` on `SNOWFLAKE` database to the role, or use a role that has it"; R4 → same, plus "Enterprise edition required" | ✅ `05-plan-not-evaluable.png` |
| Spill numbers | None (not in `INFORMATION_SCHEMA`) | None reported; only "check `max_spill_local_gb` drops" as a verify step | ✅ |
| 7-day limit | Mentioned | In the fallback message ("7-day window") | ✅ |
| Self-grant | None | No `GRANT` attempted; 0 read-only violations (50 queries: SELECT 23, ALTER_SESSION 21, SHOW 3, GET_FILES 2, DESCRIBE 1) | ✅ |

**Numbers match the baseline's estimated basis exactly:** total 0.11709/run (baseline_runs.md: 0.1171 after zero-scan
costing), `orders` 0.09439 (80.6%), R6 relationships test 0.00609 = 5.2% (baseline: 0.00609, 5.2%). The fallback path
reproduces the hand-verified figures, so the estimation model is consistent between `ACCOUNT_USAGE` and `INFORMATION_SCHEMA`.

## Surprises

- **Estimated vs attributed differ most for the Python model.** `sales_metrics_by_location` is 0.00767/run (6.6%) here
  vs 0.0006–0.00144 (0.4–1.0%) with attributed credits in TC01. The outer Python node query mostly waits on Snowpark, and
  execution time × warehouse rate charges that wait. The ranking is the same, but it's a concrete example for the
  "estimated overstates" caveat.
- **R5 shows up in two places with a Signal outside its band.** `raw_pos_order_header` (Signal 70, near-zero Impact) is
  under "Observed, below threshold" (which is for Signal 40–59) **and** on the "Evaluated, didn't fire" line. Same root
  cause as TC01 run 1: the rubric has no rule for "fires on Signal, nothing to save".
- **R1 on `customer_loyalty_metrics` scored 50** ("Observed, below threshold": event column 30 + unique key via
  GROUP BY 20) where TC01 runs scored it 0–20. The model aggregates all history per customer, so "reads a monotonic
  event column" shouldn't count for a model whose output has no event grain.
- 3 more failed source-schema guesses (`TASTY_BYTES_DBT_DB.RAW_POS`), as in TC01. The 4 `ACCOUNT_USAGE` failures are the
  expected probes; 2 `CORTEX_CODE` failures came from Cortex Code itself.
- The checkpoint asked for run frequency; correct here, since the prompt didn't give one.

## Skill change needed (file + section)

Adds evidence to #1–2 (R5 gate / near-zero Impact placement) and #4 (source names from the manifest). New:

10. scoring-rubric.md R1: "monotonic event column" counts only if the column survives to the model's grain (it's in
    the output or a GROUP BY key). A full-history aggregate per entity scores 0 for it.

## Test-plan change needed

- Setup `01_restricted_roles.sql` needed the user name quoted for `IDENTIFIER()` (fixed in `01e6499`); the connection
  is now added with `evals/setup/00_add_eval_connection.py` (zsh history expansion broke the one-liner).
