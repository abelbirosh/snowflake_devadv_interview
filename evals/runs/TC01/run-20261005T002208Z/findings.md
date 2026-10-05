# Findings: TC01 run-20261005T002208Z

## Expected vs observed
All manual checks pass. Matches `baseline_runs.md` (attributed): `orders` 0.12848 credits/run (87%), ranked #1 with
R1 (Signal 80, `delete+insert` on `order_id`, 3-day `order_ts` lookback, `models/marts/orders.sql`); R6 fires on the
`ORDER_DETAIL` relationships test (0.00924, 6.3%); join fan-out reported (693,681,576 vs 673,655,465 rows; FRANCHISE
335 rows / 325 ids); R3 Signal 50 below threshold; R4 under Needs owner review; R2 (Signal 35) and R5 (gate failed)
on the didn't-fire line; header `mixed (96% attributed)`.

First run on skill `4258d18f71f4`: used the fixed templates (Q1 argument filters, Q3a overlap check, Q3
`root_query_id` linking and window divisor) and reproduced the baseline figures exactly.

## Surprises
None.

## Skill change needed (file + section)
None.

## Test-plan change needed
None.
