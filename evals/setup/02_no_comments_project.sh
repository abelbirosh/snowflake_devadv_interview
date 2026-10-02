#!/usr/bin/env bash
# =============================================================================
# dbt-cost-tuning eval TC08: a dbt project whose queries carry NO dbt query comment.
#
# Copies tasty_bytes_dbt_demo, sets `query-comment: null` (so no "node_id" lands in
# QUERY_HISTORY.query_text), points it at its own schema so the DEV tables are untouched,
# deploys it as TASTY_BYTES_DBT_DB.EVAL_NOCOMMENT.TASTY_BYTES_DBT_NOCOMMENT and runs one build.
#
# Runs one full build of the copy (~3–4 min on SMALL).
# Usage (from the repo root):  bash evals/setup/02_no_comments_project.sh [connection]
# Teardown: evals/setup/99_teardown.sql
# =============================================================================
set -euo pipefail

CONN="${1:-AG26866}"
DB=TASTY_BYTES_DBT_DB
SCHEMA=EVAL_NOCOMMENT
PROJECT=TASTY_BYTES_DBT_NOCOMMENT
SRC="tasty_bites/tasty_bytes_dbt_demo"
WORK="$(mktemp -d)/tasty_bytes_nocomment"

cp -R "$SRC" "$WORK"
rm -rf "$WORK/setup" "$WORK/target" "$WORK/logs"

# 1. Disable dbt's query comment entirely (replaces the `query-comment: append: true` block).
python3 - "$WORK/dbt_project.yml" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
s2 = re.sub(r"query-comment:\n(\s+.*\n?)+", "query-comment: null\n", s)
assert s2 != s, "query-comment block not found"
open(p, "w").write(s2)
PY
grep -n "query-comment" "$WORK/dbt_project.yml"

# 2. Build into EVAL_NOCOMMENT instead of DEV.
sed -i '' 's/schema: dev/schema: eval_nocomment/' "$WORK/profiles.yml"
grep -n "schema:" "$WORK/profiles.yml"

# 3. Deploy and run once.
snow sql -c "$CONN" -q "CREATE SCHEMA IF NOT EXISTS $DB.$SCHEMA"
snow dbt deploy "$PROJECT" --source "$WORK" -c "$CONN" --database "$DB" --schema "$SCHEMA"
snow dbt execute "$PROJECT" build --target dev -c "$CONN" --database "$DB" --schema "$SCHEMA"

# 4. Verify: queries against EVAL_NOCOMMENT in the last 30 min should carry no node_id.
snow sql -c "$CONN" -q "
SELECT COUNT(*) AS queries,
       COUNT_IF(query_text ILIKE '%\"node_id\"%') AS with_node_id   -- expect 0
FROM TABLE($DB.INFORMATION_SCHEMA.QUERY_HISTORY(
       END_TIME_RANGE_START => DATEADD(minute, -30, CURRENT_TIMESTAMP()), RESULT_LIMIT => 10000))
WHERE query_text ILIKE '%eval_nocomment%' AND query_type <> 'CALL';"

echo "Done. Project: $DB.$SCHEMA.$PROJECT (working copy: $WORK)"
