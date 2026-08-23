#!/bin/bash
# run_refresh.sh — NS-7 daily pipeline refresh (launchd cron runner).
# Runs AFTER the A_T daily update (17:00 ET weekdays) so the A_T point-in-time
# store is fresh: universe → facts → league → momentum → selection.json.
# Also refreshes per-ticker volume (U3) via yfinance.
# v4.6: then constructs the DeltaOne basket (d1_basket.json — the only NS-7→NS-5
# handoff) and marks it to market into NS-DB strategy_returns[ns7].
# Logs: /Users/chuck/Project_Alpha_POC/Project_Nine_Street/logs/ns7_refresh.{out,err}.log
set -euo pipefail
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-7_QA
PY39=/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3
exec env -i HOME="$HOME" PATH=/usr/bin:/bin:/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin:/usr/sbin:/sbin \
  /bin/bash -c '
    set -euo pipefail
    cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-7_QA
    '"$PY39"' pipeline.py
    # v4.6 D1 basket + mark-to-market (fail-open individually: a basket failure
    # must not block MtM of the prior book, and vice versa)
    '"$PY39"' d1_basket.py || echo "WARN: d1_basket failed — keeping prior basket"
    '"$PY39"' d1_grading.py || echo "WARN: d1_grading failed — strategy_returns not updated"
  '
