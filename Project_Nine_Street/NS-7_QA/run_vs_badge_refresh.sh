#!/bin/bash
# run_vs_badge_refresh.sh — daily value-screen + HMM badge snapshot (v4.4).
# Population: scored Major names outperforming BOTH SPY & QQQ in selection.json.
# HMM via NS-2 function reuse (incl. walk-forward acceptance gate); value screen
# via read-only A_T screener API. Writes NS-7_QA/data/vs_pass_badges.json.
# Runs AFTER the NS-7 pipeline refresh so selection.json is same-day.
# Logs: /Users/chuck/Project_Alpha_POC/Project_Nine_Street/logs/ns7_vsbadge.{out,err}.log
set -euo pipefail
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-7_QA
exec env -i HOME="$HOME" PATH=/usr/bin:/bin:/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin:/usr/sbin:/sbin \
  /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 vs_badge_refresh.py
