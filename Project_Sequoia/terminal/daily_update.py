#!/usr/bin/env python3
"""
Daily 52-Week Highs Database Update
Runs at 5pm ET on weekdays via launchd.
"""
import sys
import os

# Ensure this directory is importable (year_highs/year_lows live beside this
# file) regardless of where launchd invokes it from. Never point at QA_terminal
# — a PROD copy importing QA modules is a silent deployment trap.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from year_highs import store_today_snapshot as store_highs
from year_lows import store_today_snapshot as store_lows
import logging

# Configure logging
log_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'daily_update.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 50)
    logger.info("Starting daily update (highs + lows)")
    logger.info("=" * 50)

    try:
        h_d, h_count, h_existed = store_highs(force=True)
        logger.info(f"52W highs: {h_d} - {h_count} rows{' (skipped, already exists)' if h_existed else ''}")
    except Exception as e:
        logger.exception(f"52W highs update failed: {e}")

    try:
        l_d, l_count, l_existed = store_lows(force=True)
        logger.info(f"52W lows: {l_d} - {l_count} rows{' (skipped, already exists)' if l_existed else ''}")
    except Exception as e:
        logger.exception(f"52W lows update failed: {e}")

    return 0


if __name__ == '__main__':
    sys.exit(main())