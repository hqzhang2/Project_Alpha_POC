"""_r1_frontier.py — R2 frontier sizing subprocess helper (R1 alternative sizing).

Runs the NS-5 frontier sizer under the house 3.9 runtime (the one with
sklearn/scipy/pandas) on a joint-universe closes CSV and writes the resulting
weights JSON. Executed as a subprocess by the R1 combined harness to avoid:
  (a) the NS-7/NS-8/NS-5 `config` module-name collision, and
  (b) the sklearn requirement (NS-5's Ledoit-Wolf) not being in the hermes venv.

Arg: <closes_csv> <mode> <risk_free> <out_json>
Writes {"weights": {...}, "source": "...", "n": N} or {"error": ...}.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import frontier_sizing as fs  # noqa: E402


def main() -> int:
    closes_csv, mode, risk_free, out_json = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    import pandas as pd
    closes = pd.read_csv(closes_csv, index_col=0, parse_dates=True)
    tickers = list(closes.columns)
    res = fs.size_frontier(closes, tickers, mode=mode, rf=float(risk_free))
    Path(out_json).write_text(json.dumps(res, indent=2, default=str))
    print(f"frontier sizing: mode={res.get('mode')} source={res.get('source')} "
          f"n={res.get('n')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
