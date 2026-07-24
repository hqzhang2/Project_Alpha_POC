#!/usr/bin/env python3
"""
Daily 52-Week Highs Database Update
Runs at 5pm ET on weekdays via launchd.
"""
import sys
import os

# Add project path
sys.path.insert(0, '/Users/chuck/Project_Alpha_POC/Project_Sequoia/QA_terminal')

from year_highs import store_today_snapshot
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
    logger.info("Starting daily 52-week highs update")
    logger.info("=" * 50)

    try:
        date_str, count, existed = store_today_snapshot(force=True)
        if existed:
            logger.info(f"Update skipped: {date_str} already exists ({count} rows)")
        else:
            logger.info(f"Update successful: {date_str} - {count} rows stored")
        return 0
    except Exception as e:
        logger.exception(f"Update failed: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())