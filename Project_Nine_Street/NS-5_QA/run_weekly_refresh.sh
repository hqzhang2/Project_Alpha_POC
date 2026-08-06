#!/bin/bash
# NS-5 weekly factor data refresh (launchd cron runner)
# Mirrors NS-2 run_weekly_walkforward.sh pattern: cd to service dir, clean env.
set -euo pipefail
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-5_QA
exec env -i HOME="$HOME" /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 run_weekly_refresh.py
