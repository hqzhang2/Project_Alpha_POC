#!/bin/bash
# NS-5 regime data refresh (launchd cron runner) — mirrors run_weekly_refresh.sh.
set -euo pipefail
# FRED_API_KEY must be set by the launchd plist EnvironmentVariables
# (launchd does not inherit shell env). Fail loudly if missing.
if [ -z "${FRED_API_KEY:-}" ]; then
    echo "run_regime_refresh: FRED_API_KEY not set in plist EnvironmentVariables" >&2
    exit 1
fi
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-5_QA
exec env -i HOME="$HOME" PATH=/usr/bin:/bin:/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin:/usr/sbin:/sbin \
  FRED_API_KEY="${FRED_API_KEY}" \
  /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 run_regime_refresh.py
