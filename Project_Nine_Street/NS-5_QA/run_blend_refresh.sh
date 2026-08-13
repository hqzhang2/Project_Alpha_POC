#!/bin/bash
# run_blend_refresh.sh — NS-5 sleeve-blend construction (2b, daily cron).
# Runs AFTER the NS-7 refresh (17:30 ET weekdays) so the growth sleeve feed
# is fresh: reads NS-7 selection.json + A_T screener + regime store → writes
# data/sleeve_blend.json (the PM-facing joint-universe target).
# Logs: /Users/chuck/Project_Alpha_POC/Project_Nine_Street/logs/ns5_blend.{out,err}.log
set -euo pipefail
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-5_QA
exec env -i HOME="$HOME" PATH=/usr/bin:/bin:/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin:/usr/sbin:/sbin \
  /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 sleeve_blend.py
