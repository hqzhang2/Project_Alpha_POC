#!/bin/bash
# NS-6 daily EOD price feed (R2a) — QA launchd wrapper.
# Fetches closes → drawdown/budget → drawdown_log. No LLM in the compute path.
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-6_QA
exec env -u PYTHONPATH \
  /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 \
  run_daily_price_feed.py
