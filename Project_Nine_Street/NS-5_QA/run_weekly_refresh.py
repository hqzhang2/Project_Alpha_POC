#!/usr/bin/env python3
"""
NS-5 weekly factor data refresh (launchd cron, no LLM in the loop).

Runs data_fetcher.main() with a clean environment. Idempotent — refreshes
cached closes to the latest bar. Logs to logs/ns5_refresh.{out,err}.log.

launchd template (Saturday 08:00, mirrors ns2 walkforward):
  ProgramArguments: /bin/bash /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-5_QA/run_weekly_refresh.sh
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data_fetcher  # noqa: E402


if __name__ == "__main__":
    data_fetcher.main()
