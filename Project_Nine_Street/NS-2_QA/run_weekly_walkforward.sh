#!/bin/bash
# NS-2 weekly walk-forward refresh — launchd-driven, no LLM.
# Writes ns2_walkforward_results.json in the service dir (acceptance gates +
# /api/backtest consume it automatically). Report + errors go to logs/.
set -euo pipefail
cd "$(dirname "$0")"
exec env -i HOME="$HOME" /usr/bin/python3 ns2_backtest.py --years 3
