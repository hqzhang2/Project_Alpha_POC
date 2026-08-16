"""_r1_tactical.py — NS-8 tactical monthly returns (R1 subprocess helper).

Runs NS-8 walkforward in NS-8_QA and dumps {ym: mean monthly return} to a
JSON file. Executed as a subprocess by combined_walkforward.py to avoid the
NS-7/NS-8 config.py name collision. Arg: <output_json> <start> <end>.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import walkforward as w

out_path, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
res = w.run_walkforward(tranched=True)
with open(w.HIST_PATH) as fh:
    dates = [d for d in json.load(fh)["dates"] if start <= d <= end]
rets = res["returns"]
out = {}
for i, d in enumerate(dates):
    if i >= len(rets):
        break
    ym = d[:7]
    out.setdefault(ym, []).append(rets[i])
monthly = {ym: float(sum(v) / len(v)) for ym, v in out.items()}
Path(out_path).write_text(json.dumps(monthly))
print(f"NS-8 tactical: {len(monthly)} months -> {out_path}")
