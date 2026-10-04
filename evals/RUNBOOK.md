# Runbook: running the dbt-cost-tuning skill evals in Cortex Code

**Scope:** all 10 cases in [`test_matrix.md`](test_matrix.md), **3 runs each** per skill version. Cortex Code is
non-deterministic, so a case's result is its pass rate across runs, not one sample. Testing isn't cost-minimized. The
skill itself still keeps *its own* analysis queries bounded (SKILL.md guardrail 3), because that's product behaviour and
part of what's graded.

**Every run goes through the harness.** It saves and pushes telemetry, grading and findings. There are two ways to
use it.

### Automated (default): say which evals, the harness runs them

```bash
python3 evals/harness/eval.py run --suite core          # TC01 x3, TC02, TC10, TC05
```
```bash
python3 evals/harness/eval.py run TC01x3 TC10           # any cases; xN = repeats
```
Add `--dry-run` to see the plan and preflight checks without running anything.

What it does:
1. **Plans** the selection: suites and cases come from [`harness/cases.json`](harness/cases.json), sorted into the
   block order below (so runs that add dbt builds go last), and **preflights** it: skill visible, connections
   present, no manual run in progress, `run_before` dates. Then it asks `Start? [y/N]` (`--yes` skips that).
2. **Runs each case's setup** (`setup.once` before its first run, `setup.each` before every run; `--skip-setup` skips them).
3. **Drives Cortex Code headlessly**, one run at a time (`cortex --input-format stream-json`, with a fixed
   `--session-id`, from the repo root so the project skill loads). It types the case's prompts one turn at a time and
   answers tool approvals the way this runbook tells a person to ([`harness/driver.py`](harness/driver.py)): read-only
   SQL and read-only shell are approved; SQL that writes, file edits outside a temp dir, and `snow`/`dbt`/`git` writes
   are denied. If the skill asks with Cortex's question picker, the harness answers with the case's `answers` entry or
   else the next prompt. Every decision goes into the run's `driver.json`.
4. After the last run it makes **one** Snowflake telemetry query for the whole batch (so only one OAuth login), splits it
   by each run's time window, auto-grades every run, rebuilds `results.md`, and does **one commit + push**. If the
   login fails, the sessions are kept: finish with `eval.py finish-batch evals/runs/batches/<batch>.json`.
5. You tick the manual checks in each `grading.md`, write `findings.md`, then
   `python3 evals/harness/eval.py publish --batch evals/runs/batches/<batch>.json`.

Automated runs get two extra auto checks: `session_completed` (no turn timed out) and `no_repo_edits` (`git status`
unchanged outside `evals/runs/`). There are no screenshots, so use the manual mode when you need them.

### Manual (for screenshots)

| Step | Command | Saves + pushes |
|---|---|---|
| 1 | `python3 evals/harness/eval.py start TC01` | `meta.json` (Cortex version, git sha, skill sha, prompts) |
| 2 | run the session in `cortex`, take screenshots into the run's `screenshots/` folder, `/exit` | |
| 3 | `python3 evals/harness/eval.py finish` | `transcript.md/.json`, `telemetry.json`, `queries.json`, `grading.md/.json` (auto checks), `findings.md` template, `evals/results.md` → **commit + push** |
| 4 | tick manual checks in `grading.md`, write `findings.md` | |
| 5 | `python3 evals/harness/eval.py publish` | verdict, manual grading, findings, `evals/results.md` → **commit + push** |

**Telemetry** (`telemetry.json`):
- Cortex side: wall time, turns, tool calls, token counts if the transcript has them.
- Snowflake side: every query the session ran, from `INFORMATION_SCHEMA.QUERY_HISTORY` (no lag). Counts by type, failed queries, estimated analysis credits and bytes scanned.
  Failed queries are the skill's own; Cortex Code's internal lookups of its `CORTEX_CODE` database (2 per run) are counted separately as `cortex_internal_failed`.
- Read-only violations: any statement that isn't `SELECT`/`SHOW`/`DESCRIBE`/`GET`/`LIST`/`USE`/`ALTER SESSION`.
- Unbounded `ACCOUNT_USAGE` queries.

**Grading:** automatic regex checks from [`harness/cases.json`](harness/cases.json), the read-only check and the time-bound check, plus a manual checklist.
Verdict: any read-only violation → **Fail**; all checks pass → **Pass**; ≥ 70% → **Partial**; otherwise **Fail**. Override with `Verdict: …` in `grading.md`.
Everything committed is redacted: account locator, host, user and connection names.

> **Don't run other Snowflake queries as your user while a run is in progress.** Telemetry is attributed by user + time window.

---

## 0. One-time prep

From the repo root (this worktree):

```bash
cortex skill list | grep dbt-cost-tuning
```
It must show `[PROJECT] dbt-cost-tuning`.

**Smoke test.** An earlier Cortex conversation on this machine ended with "Cortex Code is not enabled or the usage limit has been reached". Make sure that's resolved:
```bash
cortex exec --no-history -c AG26866 "Run SELECT CURRENT_ROLE() and tell me the result."
```
Any reply from the model means Cortex Code works for that connection's role. It will say the SQL tool is blocked:
`cortex exec` auto-rejects tool approvals, and `--allowed sql_execute` doesn't unblock it. That's expected. Check the
role itself with `snow sql -c <connection> -q "SELECT CURRENT_ROLE()"`; the real run asks you to approve each query.

**Telemetry login.** `eval.py finish` queries Snowflake as `EVAL_ADMIN_CONN` (default `AG26866`). That connection uses
browser OAuth, so run `finish` in your own terminal, where the login tab can open; from a non-interactive shell it times
out. To automate it, point `EVAL_ADMIN_CONN` at a key-pair connection with the same user.
If `finish` already ran but graded the wrong session, re-grade without querying Snowflake again:
`eval.py finish --rerun evals/runs/<CASE>/<run> --keep-snowflake [--session <id>]`.

**Connections.** Add these to `~/.snowflake/connections.toml`. Copy your `[AG26866]` block and change only what's shown:
```toml
[AG26866_RESTRICTED]      # TC05: role = "DBT_TUNING_EVAL_RESTRICTED"
[AG26866_NOPROJECT]       # TC04: role = "DBT_TUNING_EVAL_NOPROJECT", no database/schema lines
[AG26866_NOWH]            # TC03: delete the warehouse line
```

## Order of work

The baseline window must stay at the 2 baseline runs for TC01/TC02/TC05/TC10. Run everything that reads that window first, then the cases that add runs or projects:

| Block | Cases (× 3 runs each) | Setup before the block |
|---|---|---|
| A | TC01, TC02, TC10 | none |
| B | TC05, TC04, TC03 | `snow sql -c AG26866 -f evals/setup/01_restricted_roles.sql`, then a new login. TC03 also needs `bash evals/setup/03_optional_cases.sh tc03` |
| C | TC08, TC07, TC09 | `bash evals/setup/02_no_comments_project.sh`, `bash evals/setup/03_optional_cases.sh tc07` (TC09 then happens naturally: 3 projects visible) |
| D | TC06 | `bash evals/setup/03_optional_cases.sh tc06` before **each** run. Each run adds a build to `TASTY_BYTES_DBT`, so it goes last |

> ⏰ The baseline runs are from 2026-10-02. TC05's `INFORMATION_SCHEMA` fallback sees 7 days, so finish block B **before 2026-10-09**.

When a run fails: fix `SKILL.md` / references, commit, and re-run that case 3×. `skill_sha` in `results.md` keeps versions apart.

Teardown when everything is done (first edit the restore lines to your recorded `DESC USER` values):
```bash
snow sql -c AG26866 -f evals/setup/99_teardown.sql
```

---

## Next two sessions

### Session 1: TC01 run 1 (also Part 1: the screenshots of the run and the tuning plan)

```bash
python3 evals/harness/eval.py start TC01
```

```bash
cortex -c AG26866
```

1. Type: **Which dbt models cost the most in my Tasty Bytes project? Give me a tuning plan.**
2. 📸 `screenshots/01-skill-loaded.png`: the prompt, the skill loading (`dbt-cost-tuning` / `SKILL.md`), and the Q0 context query.
3. Approve read-only SQL (`SELECT`/`SHOW`/`DESCRIBE`/`GET`). **Deny anything else**, and note it in `findings.md`.
4. 📸 `02-checkpoint.png`: project, capability matrix, rules to evaluate, and the frequency question. (The 2-run window
   isn't known yet: Q1 runs after the checkpoint. Check it in the plan header, screenshot 04.)
5. Type: **Yes, that project. Skip frequency, show per run.**
6. 📸 `03-run-in-progress.png`: Q1 (2 SUCCESS runs), the artifact `GET`, the Q3 cost output.
7. 📸 `04-plan-top.png`: plan header ("Window: 2 successful runs"), "Where the cost is", Recommendations table.
8. 📸 `05-plan-details.png`: R1 diff for `orders` + verify step, R6, "Observed, below threshold", "Not evaluable", "Assumptions", closing offer.
9. `/exit`

```bash
python3 evals/harness/eval.py finish
```
This prints the auto-check result, commits and pushes.

Then open the run's `grading.md` and tick the manual checks. Compare against [`baseline_runs.md`](baseline_runs.md) "Expected skill outcomes": orders ≈ 80%, R1 #1, R6 fires, R3 at Signal 50 below threshold, R4 "needs owner review", R2/R5 silent.
Write `findings.md`, covering expected vs observed and any skill change needed. Then:

```bash
python3 evals/harness/eval.py publish
```

Before you publish, check the screenshots for account identifiers (URL bar, prompt, `CURRENT_ACCOUNT()` output) and blur them. The harness redacts text but not images.

### Session 2: TC02 run 1 (paraphrased trigger + daily frequency)

```bash
python3 evals/harness/eval.py start TC02
```

```bash
cortex -c AG26866
```

1. Type: **Our dbt build runs daily and credits are creeping up. Find the expensive dbt models and give me an optimization plan.**
   Don't name the skill: this checks that the description triggers it.
2. 📸 `01-skill-loaded.png`, then 📸 `02-checkpoint.png`. It should **not** ask for the frequency again.
3. Type: **Yes, that project.**
4. 📸 `03-plan-top.png` with the monthly savings column, then 📸 `04-plan-details.png`.
5. `/exit`

```bash
python3 evals/harness/eval.py finish
```
Grade: monthly = per-run × ~30, same ranking as TC01, savings worded as estimates. Fill `findings.md`, then:

```bash
python3 evals/harness/eval.py publish
```

If auto-detect picks the wrong Cortex session, find the id with `cortex conversations list` and run `eval.py finish --session <id>`.
