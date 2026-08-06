#!/bin/bash
# refresh_fundamentals.sh — quarterly SEC fundamentals store refresh (QA + PROD).
# Incremental: existing rows skipped; picks up newly filed 10-K/20-F (S&P 500
# universe + curated ADRs), backfills missing prices, refreshes the S&P 500
# point-in-time cache. Logs: /tmp/fund_refresh_<dir>.log
CLT=/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3
ADRS="BABA,TSM,BHP,JD,NTES,PBR"
for DIR in /Users/chuck/Project_Alpha_POC/Project_Sequoia/QA_terminal \
           /Users/chuck/Project_Alpha_POC/Project_Sequoia/terminal; do
  TAG=$(basename "$DIR")
  LOG="/tmp/fund_refresh_${TAG}.log"
  cd "$DIR" || { echo "[$TAG] FAIL: no such dir" >> "$LOG"; exit 1; }
  echo "[$(date '+%F %T')] refresh start" > "$LOG"
  env -i HOME="$HOME" "$CLT" fundamentals_history.py --limit 506 >> "$LOG" 2>&1
  env -i HOME="$HOME" "$CLT" fundamentals_history.py --tickers "$ADRS" >> "$LOG" 2>&1
  env -i HOME="$HOME" "$CLT" validate_frameworks.py --build-prices >> "$LOG" 2>&1
  env -i HOME="$HOME" "$CLT" sp500_history.py >> "$LOG" 2>&1
  echo "[$(date '+%F %T')] refresh done" >> "$LOG"
done
