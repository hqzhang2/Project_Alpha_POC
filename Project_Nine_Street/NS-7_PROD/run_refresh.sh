#!/bin/bash
# run_refresh.sh — NS-7 daily pipeline refresh (launchd cron runner).
# Runs AFTER the A_T daily update (17:00 ET weekdays) so the A_T point-in-time
# store is fresh: universe → facts → league → momentum → selection.json.
# Also refreshes per-ticker volume (U3) via yfinance.
# Logs: /Users/chuck/Project_Alpha_POC/Project_Nine_Street/logs/ns7_refresh.{out,err}.log
set -euo pipefail
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-7_QA
exec env -i HOME="$HOME" PATH=/usr/bin:/bin:/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin:/usr/sbin:/sbin \
  /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 pipeline.py
