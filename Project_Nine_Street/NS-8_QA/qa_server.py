"""qa_server.py — NS-8 QA FastAPI Server.

Endpoints:
- GET /api/signals — Current signal document
- GET /api/tranche — Tranche schedule
- POST /api/rebalance — Manual trigger
- GET /api/walkforward — Run walkforward (dev)
"""
import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

import config
import pipeline
import store
import walkforward

app = FastAPI(title="NS-8 QA", version="0.1.0")


class RebalanceRequest(BaseModel):
    source: str = "yfinance"
    as_of: Optional[str] = None


@app.get("/api/signals")
def get_signals():
    """Get latest signal document."""
    signal = store.get_latest_signal()
    if not signal:
        raise HTTPException(404, "No signals generated yet. Run /api/rebalance first.")
    return signal


@app.get("/api/tranche")
def get_tranche():
    """Get tranche schedule and current state."""
    state = store.get_tranche_state()
    current = store.get_current_tranche()
    return {
        "current_tranche": current,
        "total_tranches": config.TRANCHES,
        "schedule": state
    }


@app.post("/api/rebalance")
def trigger_rebalance(req: RebalanceRequest):
    """Manually trigger a full pipeline refresh."""
    try:
        doc = pipeline.run_refresh(as_of=req.as_of, source=req.source)
        return {"status": "ok", "document": doc}
    except Exception as e:
        raise HTTPException(500, f"Rebalance failed: {str(e)}")


@app.get("/api/walkforward")
def run_wf(
    start: str = Query(default=config.WF_START),
    end: str = Query(default=config.WF_END),
    tranched: bool = True,
    cost_bps: int = Query(default=config.TXN_COST_BPS)
):
    """Run walkforward backtest (dev endpoint)."""
    try:
        result = walkforward.run_walkforward(
            start=start,
            end=end,
            tranched=tranched,
            transaction_cost_bps=cost_bps
        )
        return result
    except Exception as e:
        raise HTTPException(500, f"Walkforward failed: {str(e)}")


@app.get("/health")
def health():
    """Health check."""
    return {
        "status": "ok",
        "service": "NS-8 QA",
        "version": "0.1.0",
        "config": {
            "sma_window": config.SMA_WINDOW,
            "tranches": config.TRANCHES,
            "risky_assets": config.RISKY_ASSETS,
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)