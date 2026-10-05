# Fix list from the 2026-10-03 eval runs

Bugs found in the 6 runs on skill version `f43d60e0f1f8` (TC01 ×3, TC02, TC05, TC10). Evidence is in each run's
`findings.md` under [`runs/`](runs/); pass rates are in [`results.md`](results.md).

**Status: all fixed.** Skill bugs in `fdb40ea` (new `skill_sha` `91646be17463`), harness and test-plan bugs in
`3d9663d` and earlier. The fixed skill hasn't been re-run yet, so `results.md` shows no runs on `91646be17463`; the
2026-10-03 results describe `f43d60e0f1f8`. Re-run TC01 ×3, TC02, TC05 and TC10 to measure the fixes; the new auto
checks (`no_r5_recommendation`, `no_frequency_question`, `frequency_echoed`, `no_failed_skill_queries`) cover S1, S2 and S5.

## Skill bugs

Ordered by how often they showed up, then by impact on the plan.

| # | Bug | Seen in | Fix (file → section) | Status |
|---|---|---|---|---|
| S1 | **Source tables queried under a guessed schema.** Q6 on a source table (FRANCHISE, ORDER_HEADER) used `TASTY_BYTES_DBT_DB.RAW_POS` or the dbt source name `TB_101` as a schema, failed, and was retried | 5 of 6 runs (9 failed queries) | SKILL.md Phase 3, Q6 on a joined dimension: take the table from the manifest source's `relation_name`, never a guessed schema | Fixed (`fdb40ea`) |
| S2 | **R5 on a passthrough view lands in the wrong section.** `raw_pos_order_header` scores 40 + 0 + 30 = 70 with ~0 Impact. Placed as recommendation #3 ("not recommended" in its own text) in TC01 run 1; listed under both "Observed, below threshold" and "Evaluated, didn't fire" in TC05. Correct in the other 4 runs only because the agent overrode the rubric | 2 of 6 wrong | scoring-rubric.md R5: make "the view does real work (joins or aggregations)" a **gate**, not a 30-point row. §3 placement: a rule with Impact < 1% goes on the "Evaluated, didn't fire" line even at Signal ≥ 60 | Fixed (`fdb40ea`) |
| S3 | **Python model credits flip between runs.** With `attributed` credits, `sales_metrics_by_location`'s write query was dropped in 4 runs and added in 1. The rubric says drop it, which is right for `estimated` credits (TC05 did this correctly) but undercounts with `attributed` credits: QUERY_ATTRIBUTION_HISTORY credits each query on its own (Snowflake docs: sum by `root_query_id`) | 5 of 5 attributed runs (inconsistent) | scoring-rubric.md §2 Python models + SKILL.md Phase 2 step 3: drop the write query's credits only when they're `estimated`; add them when `attributed` | Fixed (`fdb40ea`) |
| S4 | **The skill's own queries count as "outside reads".** Q4 doesn't exclude earlier analysis runs, so `ORDERS` went from 1 to 3 outside reads across runs, then the staging views started showing reads too. No outcome changed yet, but R2/R4 evidence gets noisier every run | Grows with every run | Tag every skill query (e.g. a `/* dbt-cost-tuning */` comment) and exclude that tag in queries.md Q4 | Fixed (`fdb40ea`) |
| S5 | **Asks for the run frequency the user already gave.** TC02's prompt says "runs daily"; the checkpoint asked "How often does this dbt build run?" anyway, then assumed daily | 1 of 1 (TC02) | SKILL.md Phase 0 step 4: if the message states a frequency, show `Run frequency: <value> (from your message)` in the checkpoint and don't ask | Fixed (`fdb40ea`) |
| S6 | **"Not evaluable" heading missing** when nothing is unavailable | 1 of 6 (TC01 run 1) | SKILL.md Phase 4 template: always print every section heading; an empty section says "None" | Fixed (`fdb40ea`) |
| S7 | **Handover code isn't deployable as shown.** After declining to apply (TC10), the "do it yourself" SQL dropped `{{ ref() }}`, and the suggested `snow dbt deploy --project …` / `snow dbt execute … --args='…'` don't match the syntax in the repo's own scripts | TC10 | SKILL.md Failure handling ("User asks to apply a fix") + closing offer: keep `ref()`/`source()` in any code shown, and don't invent deploy commands (quote a checked reference or point to the user's process) | Fixed (`fdb40ea`) |
| S8 | **R6 `where` filter hard-codes a relation** (`TASTY_BYTES_DBT_DB.RAW.ORDER_HEADER`) instead of `{{ source(...) }}` | 1 of 6 (TC02) | SKILL.md Phase 3 (R6): the proposed `where` uses `source()` / `ref()` | Fixed (`fdb40ea`) |
| S9 | **R1 "monotonic event column" counted on a full-history aggregate.** `customer_loyalty_metrics` (one row per customer, all history) scored 50 in TC05, 0–20 elsewhere | 1 of 6 (TC05) | scoring-rubric.md R1: the event-column row counts only if the column survives to the model's grain (in the output or a GROUP BY key) | Fixed (`fdb40ea`) |
| S10 | **Header credit label varies** for the same data: `attributed` (models 100%, tests 50%) in 3 runs, `mixed` in 2 | 5 of 5 attributed runs (inconsistent) | scoring-rubric.md §2: define the header label: `attributed` only if every node is; otherwise `mixed (N% attributed)` | Fixed (`fdb40ea`) |

## Harness and test-plan bugs

| # | Bug | Status |
|---|---|---|
| H1 | `eval.py finish` took the **newest** Cortex session after the run started. When the main session spawns sub-agents (`task` tool), that's a sub-agent, so TC10 was first graded on a 33-second sub-agent transcript | **Fixed** (`eval.py`: earliest session; `finish --rerun --keep-snowflake` re-grades without Snowflake) |
| H2 | `01_restricted_roles.sql` failed on `GRANT ROLE … TO USER IDENTIFIER($me)`: `IDENTIFIER()` upper-cases a bare name, and this user's name is stored in lowercase | **Fixed** (`01e6499`: quote the name; same in teardown and `03_optional_cases.sh`) |
| H3 | Adding the TC05 connection as a `python3 -c` one-liner broke in zsh (`!` history expansion) | **Fixed** (`setup/00_add_eval_connection.py`) |
| H4 | TC07 and TC08 had one prompt, but the Phase 0 checkpoint is a hard stop, so they'd never reach Q1/Q3 | **Fixed** (second prompt added in `cases.json`) |
| H5 | `baseline_runs.md` expectations are on `estimated` credits (`orders` ≈ 81%, R6 5.2%). Once QUERY_ATTRIBUTION_HISTORY caught up, runs report `attributed` figures (`orders` 87.9%, R6 6.3%, total 0.14625/run). TC05's fallback reproduces the estimated figures exactly | **Fixed** (`3d9663d`): `baseline_runs.md` has the attributed figures and the fixed R2/R5 expectations; TC01 manual checks reworded |
| H6 | Eval activity contaminates `ACCESS_HISTORY` (manual baseline checks and earlier skill runs read `ORDERS`) | **Fixed** by S4 (`fdb40ea`): the skill tags its queries and Q4 excludes them. Reads from untagged older runs stay in the 30-day window until they age out |
| H7 | `telemetry.user_turns` misses answers given through Cortex's `ask_user_question` picker (TC01 run 2 shows 1 turn for 2 answers) | **Fixed** (`3d9663d`): `user_turns` adds `ask_user_question` answers (`picker_answers`) |
| H8 | `failed_queries` includes 2 failures per run from Cortex Code itself (`CORTEX_CODE` database), which aren't the skill's | **Fixed** (`3d9663d`): reported as `cortex_internal_failed`; `failed_queries` is the skill's own (older telemetry recomputed) |
| H9 | `finish` needs an interactive browser login (`AG26866` uses OAuth), so it can't run from automation | **Fixed** (`3d9663d`): RUNBOOK: run `finish` in your own terminal, or point `EVAL_ADMIN_CONN` at a key-pair connection |
| H10 | `cortex exec --allowed sql_execute` still blocks SQL, so the smoke test can't run a query | **Fixed** (`3d9663d`): RUNBOOK: a model reply is the smoke test; check the role with `snow sql` |
| H11 | RUNBOOK.md screenshot 02 expects the 2-run window at the checkpoint, but Q1 runs after it | **Fixed** (`3d9663d`): RUNBOOK: window checked in screenshot 04 (plan header) |

## Query-template bugs from review (2026-10-04)

Found by reading the SQL in `references/queries.md`, not by an eval run: the 2 baseline builds are full builds with
nothing else running, so none of these changed a result yet. Each fix was checked on the baseline builds: Q3
reproduces the attributed figures exactly (`orders` 0.12848, total 0.14709 per run), and Q3b reproduces build 1's
estimated figures (`orders` 0.09878, relationships test 0.00609).

| # | Bug | Evidence | Fix (file → section) | Status |
|---|---|---|---|---|
| S11 | **Q1/Q3a/Q3 can miss the project's runs.** `DBT_PROJECT_EXECUTION_HISTORY()` returns at most `RESULT_LIMIT` rows (default 100) across all projects, and `WHERE` filters after that cut. Q3/Q3a also didn't filter `command`, so a successful `test`/`compile` could take a window slot. The function only covers 7 days: the baseline builds drop out on 2026-10-09 | Snowflake docs (usage notes: filter arguments are applied before `RESULT_LIMIT`, a `WHERE` clause after) | queries.md Q1: filter with `OBJECT_NAME`/`DATABASE`/`SCHEMA` arguments, `command IN ('build','run')`; Q1b reads older runs from `ACCOUNT_USAGE.DBT_PROJECT_EXECUTION_HISTORY` (365 days); Q1c for the last failure. Q3a/Q3 take the window's run IDs from Q1 instead of re-deriving it | Fixed |
| S12 | **Q3/Q3b count unrelated queries.** A build's queries were matched by same user + time window only. In build 1's window that's 387 queries, of which 61 carry a dbt `node_id`; an ad-hoc query or a second dbt build by the same user would be added in | Probe on build 1: 54 queries have `root_query_id` = the `EXECUTE DBT PROJECT` query (53 commented + the Python write query) | queries.md Q3: attributed queries count only if `root_query_id` is the run; unattributed ones only if they carry a `node_id` or write a Python model's table (`<PYTHON_MODEL_RELATIONS>`). Q3a reports `overlapping_dbt_runs`. Q3b: same rule. SKILL.md Phase 2, rubric §2 | Fixed |
| S13 | **Q3 overstates models that skip runs.** `credits_per_run` averaged a node over the runs it appeared in, so a model built in 1 of 5 runs showed its full build cost per run: share, rank and monthly saving 5× too high | Reading the SQL (`node_cost` averaged over `node_run` rows) | queries.md Q3: node total ÷ `<WINDOW_RUNS>`, plus `runs_built`; Q3b: same divisor. SKILL.md Phase 2 step 4, rubric §2 | Fixed |
| S14 | **Q3b could truncate on a busy account and silently return nothing for lowercase users.** `INFORMATION_SCHEMA.QUERY_HISTORY()` applies `RESULT_LIMIT` before `WHERE user_name = …`, like S11. `QUERY_HISTORY_BY_USER` filters first, but matches `USER_NAME` as an identifier: `'jdoe'` returned 0 rows, `'"jdoe"'` returned all 387 | Tested on build 1 | queries.md Q3b: step 1 reads the run's user from a 2-second window; step 2 uses `QUERY_HISTORY_BY_USER(USER_NAME => '"<RUN_USER>"')` | Fixed |

## Found by the before/after builds (2026-10-04)

From [`cost_example/`](cost_example/README.md): `orders` deployed as an incremental model in a separate project
(COST_DEMO), one full refresh and three incremental builds.

| # | Bug | Evidence | Proposed fix | Status |
|---|---|---|---|---|
| S15 | **Re-measuring after a change mixes in the transition runs.** The window keeps `--full-refresh` runs and the first incremental run after one. On COST_DEMO, Q3 reports `orders` at 0.0359 credits/run against a steady state of ~0.0073 (5×) | Fixed Q3 on the 4 COST_DEMO builds; `cost_example/README.md` §4 | Q1: flag `--full-refresh` in `args`; when re-measuring a change, start the window at the second incremental run after the last full refresh, and say so | Open |
| S16 | **R1's "verify" step doesn't warn about the first run or the DELETE scan.** The first incremental run after a full refresh cost 35% of a full build (its DELETE read every partition), and every DELETE scans `order_id` across the whole table, which grows with it | Operator stats for both DELETEs; `cost_example/README.md` §6 | SKILL.md Phase 3 (R1): say the first incremental run after `--full-refresh` is a one-time cost; for `delete+insert` on a large unclustered target, suggest `incremental_predicates` on the event column (to test) | Open |

## Found by the 2026-10-05 eval runs (skill `4258d18f71f4`)

| # | Bug | Seen in | Proposed fix | Status |
|---|---|---|---|---|
| S17 | **Reference numbers reported as account evidence.** With no `ACCOUNT_USAGE`, TC05 still scored R3 at Signal 50 "from the rubric's verified Tasty Bytes data: 12.45 GB local spill", and listed R3 under both "Observed, below threshold" and "Not evaluable". The rubric has five "Verified on Tasty Bytes" notes with real figures (§2, R1, R2, R3, R5), so the same leak can happen elsewhere | TC05 `run-20261005T011128Z` | scoring-rubric.md: move the "Verified on Tasty Bytes" notes out of the skill (to `evals/baseline_runs.md`) or mark them "illustration only, never report". SKILL.md Phase 3: a rule whose data source is unavailable goes only under "Not evaluable", with no Signal and no figures | Open |
| S18 | **Tries to edit the model file after declining to apply.** After "Apply the change and run dbt build", the skill declined the build but called `edit` on `models/marts/orders.sql` to "write the local diff", without the user accepting a diff. The harness denied it, so nothing changed | TC10 `run-20261005T004249Z` | SKILL.md Phase 4 closing offer and Failure handling: never edit project files; the local diff is a separate file (e.g. a temp dir), written only after the user accepts the offer | Open |
