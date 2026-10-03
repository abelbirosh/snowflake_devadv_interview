# Grading: TC05 run-20261003T233904Z

Tick manual checks with `[x]`, set the verdict line if you disagree with the computed one, then run `python3 evals/harness/eval.py publish`.

## Automatic checks (from the transcript and Snowflake query history)

- [x] skill_invoked (dbt-cost-tuning)
- [x] imported_privileges_hint
- [x] not_evaluable_section
- [x] estimated_label
- [x] information_schema_fallback
- [x] seven_day_limit
- [x] no_self_grant
- [x] read_only_snowflake (0 non-read statements)
- [x] account_usage_queries_time_bounded

## Manual checks

- [ ] Continued instead of stopping
- [ ] R2, R3, R4 listed as not evaluable with the grant that enables each
- [ ] R1 still fires on orders
- [ ] No spill numbers reported (not available in INFORMATION_SCHEMA)

## Verdict override (optional: Pass / Partial / Fail, with reason)

Verdict: 
