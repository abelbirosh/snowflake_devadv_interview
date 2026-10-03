# Findings: TC01 run-20261003T225441Z

Third run on `skill_sha` f43d60e0f1f8, same 2-run baseline as [run-20261003T221608Z](../run-20261003T221608Z/findings.md)
and [run-20261003T223318Z](../run-20261003T223318Z/findings.md). Graded by Claude from the transcript, screenshots and
telemetry; reviewed with the user.

**Result: Pass (20/20 auto, 14/14 manual).** TC01 on this skill version: 2 of 3 runs Pass, 1 Partial.

## Expected vs observed

| Area | Expected | Observed | Result |
|---|---|---|---|
| Checkpoint | Waits before Q1 | Ended its turn in text (no picker this time); Q1 ran after the reply | ✅ `02-checkpoint.png` |
| Window | 2 runs | 2, both in `ACCOUNT_USAGE` (Q3a) | ✅ `03-run-in-progress.png` |
| Credit source | Labelled with reason | **`mixed`**: models `attributed` (100%), some tests `mixed` (50%, cache-hit queries not in QAH). The most precise label of the three runs; runs 1–2 headed it `attributed` | ✅ |
| Cost table | `orders` #1 | 0.12848 (87.9%) of 0.14625/run | ✅ `04-plan-top.png` |
| R1 | #1, diff, path, lookback | `models/marts/orders.sql`, full join list, `delete+insert` on `order_id`; Q6: 0 nulls, 248,201,269 = `ORDER_HEADER` rows; 3-day lookback; `--full-refresh`; Signal 30+30+0+20 = 80; Impact and Effort shown as formulas | ✅ `05a-plan-r1-r6.png` |
| Fan-out | Reported | 693,681,576 vs 673,655,465 (20M extra rows); FRANCHISE 335 / 325 | ✅ `05b-plan-details.png` |
| R6 | Fires | #2, 40+30+30 = 100, 6.3%, Impact 4.4%; `where` on orders from the last 30 days | ✅ |
| R3 | Below threshold, 50 | 30 + 20 = 50; "remote spill (+50) would bring it to 100" | ✅ |
| R4 | Needs owner review | `customer_loyalty_metrics`, `sales_metrics_by_location`, and **`raw_pos_country`** (new: no model references it; verified in `models/`) | ✅ |
| R2 / R5 | "Evaluated, didn't fire" | R2 Signal 35; R5 "Signal 70 but Impact ≈ 0" | ✅ |
| "Not evaluable" | Present | "None — all capability probes passed." | ✅ |
| Read-only / cost | 0 violations, bounded | 0 violations; 7 `ACCOUNT_USAGE` queries, all bounded; 51 queries; ~0.009 analysis credits; 508 s wall | ✅ |

## Consistency across the 3 runs on f43d60e0f1f8

| Behaviour | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Verdict | Partial | Pass | Pass |
| R5 placement | ❌ Recommendation #3 | ✅ didn't fire | ✅ didn't fire (Signal 70, Impact ≈ 0) |
| "Not evaluable" heading | ❌ missing | ✅ | ✅ |
| Python model credits | write dropped (0.00060) | write added (0.00144) | write dropped (0.00060) |
| Header credit label | attributed | attributed | mixed |
| Checkpoint style | text, ended turn | `ask_user_question` picker | text, ended turn |
| Failed source-schema guesses | 2 (`RAW_POS`) | 1 (`RAW_POS`) | 2 (`TB_101`, the dbt source name used as a schema) |

## Surprises

- **R5 outcome now matches the expected placement, but against the rubric's own rule.** R5 scores 70 (≥ 60 fires); the agent
  put it on the "didn't fire" line because Impact ≈ 0. That's the right call, but it isn't written down, which is why
  run 1 went the other way.
- Python model credits flip between runs (see run 2 findings: with `attributed` credits, adding the write is correct).
- The Q6 on a source table guessed the schema in all 3 runs. This run used the dbt source name `tb_101` as a schema.

## Skill change needed (file + section)

No new items. The 6 from runs 1–2 stand; runs 2–3 add evidence for #1–2 (R5) and #5 (Python credits), and #4 (source
`relation_name` from the manifest) failed in 3/3 runs.

## Test-plan change needed

None new.
