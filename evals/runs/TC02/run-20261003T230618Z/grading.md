# Grading: TC02 run-20261003T230618Z

Tick manual checks with `[x]`, set the verdict line if you disagree with the computed one, then run `python3 evals/harness/eval.py publish`.

## Automatic checks (from the transcript and Snowflake query history)

- [x] skill_invoked (dbt-cost-tuning)
- [x] plan_header
- [x] section_recommendations
- [x] monthly_savings
- [x] r1_incremental_orders
- [x] r6_relationships_test
- [x] estimate_language
- [x] read_only_snowflake (0 non-read statements)
- [x] account_usage_queries_time_bounded

## Manual checks

- [ ] Skill triggered from the paraphrased prompt (not invoked by name)
- [ ] Did not re-ask the run frequency
- [ ] Monthly saving = per-run saving x ~30
- [ ] Ranking matches TC01 (R1 orders #1, R6 fires)
- [ ] Savings presented as estimates, not guarantees

## Verdict override (optional: Pass / Partial / Fail, with reason)

Verdict: 
