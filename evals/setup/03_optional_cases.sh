#!/usr/bin/env bash
# =============================================================================
# dbt-cost-tuning eval: setup for TC03, TC06 and TC07.
# Run only the block for the case you are about to run.
# Usage (from the repo root): bash evals/setup/03_optional_cases.sh <tc03|tc06|tc07> [connection]
# =============================================================================
set -euo pipefail
CASE="${1:?tc03|tc06|tc07}"
CONN="${2:-AG26866}"
DB=TASTY_BYTES_DBT_DB

case "$CASE" in
  tc03)
    # No warehouse in session. Unset your user's default warehouse (restore it in 99_teardown.sql),
    # then start Cortex Code with a connection entry that has no `warehouse = ...` line
    # (see RUNBOOK.md, "Connections").
    snow sql -c "$CONN" -q "SET me = '\"' || CURRENT_USER() || '\"'; DESC USER IDENTIFIER(\$me);" | grep -i default_warehouse || true
    snow sql -c "$CONN" -q "SET me = '\"' || CURRENT_USER() || '\"'; ALTER USER IDENTIFIER(\$me) UNSET DEFAULT_WAREHOUSE;"
    ;;
  tc06)
    # ACCOUNT_USAGE / QUERY_ATTRIBUTION_HISTORY not caught up. Run a fresh build, then start the
    # skill within ~10 minutes. NOTE: this adds a 3rd run to the baseline window. Do it only after
    # the Part 1 screenshots and the happy-path cases.
    snow dbt execute TASTY_BYTES_DBT build --target dev -c "$CONN" --database "$DB" --schema DEV
    date
    ;;
  tc07)
    # No successful runs: a project whose only run fails (unknown target).
    snow sql -c "$CONN" -q "CREATE SCHEMA IF NOT EXISTS $DB.EVAL_FAIL"
    snow dbt deploy TASTY_BYTES_DBT_FAIL --source tasty_bites/tasty_bytes_dbt_demo \
      -c "$CONN" --database "$DB" --schema EVAL_FAIL
    snow dbt execute TASTY_BYTES_DBT_FAIL build --target does_not_exist \
      -c "$CONN" --database "$DB" --schema EVAL_FAIL || echo "Expected failure: OK"
    ;;
  *) echo "unknown case: $CASE" >&2; exit 1 ;;
esac
