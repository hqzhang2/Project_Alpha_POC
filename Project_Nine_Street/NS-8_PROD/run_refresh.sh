#!/bin/bash
# run_refresh.sh — NS-8 daily pipeline refresh (launchd cron runner).
# v4.7: creates the previously-MISSING NS-8 refresh trigger (the ns8 plists
# are server daemons only — nothing refreshed signals.json, so it went stale
# past FEED_STALE_DAYS and NS-5 dropped the sleeve; see research_ns8_feed_v47.md §6).
# Weekdays 18:00 (after NS-7 17:30 / NS-ETF 17:45), via
# com.ninestreet.ns8.refresh{,.prod}.plist.
#
# Steps (each fail-open individually):
#   1. pipeline.py        — fetch → signal → inverse-vol sizing → enriched
#                           signals.json (v4.7 feed contract)
#   2. ns8_grading.py     — mark the weighted book (incl. SHV) to market into
#                           NS-DB strategy_returns["ns8"]
# Logs: /Users/chuck/Project_Alpha_POC/Project_Nine_Street/logs/ns8_refresh.{out,err}.log
set -euo pipefail
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-8_QA
PY39=/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3
exec env -i HOME="$HOME" PATH=/usr/bin:/bin:/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin:/usr/sbin:/sbin \
  /bin/bash -c '
    set -euo pipefail
    cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-8_QA
    '"$PY39"' pipeline.py
    # MtM of the weighted book (fail-open: a grading failure must not kill the feed)
    '"$PY39"' ns8_grading.py || echo "WARN: ns8_grading failed — strategy_returns not updated"
  '
