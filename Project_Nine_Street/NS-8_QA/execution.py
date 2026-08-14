"""execution.py — NS-8 Execution Layer.

Handles order generation for both semi-automated (TWS Basket CSV)
and fully automated (IBKR ib_async MOC) execution paths.
"""
import csv
import json
from datetime import datetime
from io import StringIO
from typing import Dict, List, Optional

import config
import store


def fetch_latest_prices(tickers: List[str]) -> Dict[str, float]:
    """Fetch latest prices for order sizing.

    In production, this would use the same data source as the pipeline.
    For now, returns mock prices for testing.
    """
    # TODO: Replace with real price fetch from pipeline's data source
    # For now, return reasonable mock prices
    mock_prices = {
        "SPY": 550.0,
        "EFA": 75.0,
        "IEF": 95.0,
        "VNQ": 90.0,
        "DBC": 25.0,
        "SHV": 98.5,
    }
    return {t: mock_prices.get(t, 100.0) for t in tickers}


def compute_target_shares(
    aum: float,
    weights: Dict[str, float],
    prices: Dict[str, float]
) -> Dict[str, int]:
    """Compute target shares for each symbol.

    Args:
        aum: Total assets under management for this strategy.
        weights: Target weights from signal document.
        prices: Current prices per symbol.

    Returns:
        {symbol: target_shares} (rounded to whole shares).
    """
    targets = {}
    for symbol, weight in weights.items():
        if weight <= 0:
            targets[symbol] = 0
            continue
        price = prices.get(symbol, 100.0)
        if price <= 0:
            targets[symbol] = 0
            continue
        targets[symbol] = int((aum * weight) / price)
    return targets


def generate_basket_csv(
    doc: Dict,
    aum: float,
    current_shares: Dict[str, int],
    prices: Optional[Dict[str, float]] = None
) -> str:
    """Generate TWS BasketTrader CSV.

    Args:
        doc: Signal document from pipeline (contains weights).
        aum: Total AUM allocated to this strategy.
        current_shares: {symbol: current_shares_held}.
        prices: Optional price override (for testing).

    Returns:
        CSV content string for TWS BasketTrader import.
    """
    prices = prices or fetch_latest_prices(list(doc["weights"].keys()))
    targets = compute_target_shares(aum, doc["weights"], prices)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Symbol", "Action", "Quantity", "OrderType", "TimeInForce"])

    for symbol in doc["weights"]:
        target = targets.get(symbol, 0)
        current = current_shares.get(symbol, 0)
        delta = target - current

        if delta == 0:
            continue

        side = "BUY" if delta > 0 else "SELL"
        writer.writerow([symbol, side, abs(delta), "MOC", "DAY"])

    return output.getvalue()


async def submit_moc_orders(
    doc: Dict,
    aum: float,
    current_shares: Dict[str, int],
    prices: Optional[Dict[str, float]] = None,
    host: str = "127.0.0.1",
    port: int = 7497,
    client_id: int = 8
) -> List[Dict]:
    """Submit Market-On-Close orders via ib_async.

    Args:
        doc: Signal document with weights.
        aum: Total AUM.
        current_shares: Current holdings.
        prices: Price override.
        host: IB Gateway/TWS host.
        port: Port (7497=paper, 7496=live).
        client_id: Unique client ID.

    Returns:
        List of order confirmations: {symbol, side, qty, order_id, status}.
    """
    from ib_async import IB, MarketOrder, Stock, util

    prices = prices or fetch_latest_prices(list(doc["weights"].keys()))
    targets = compute_target_shares(aum, doc["weights"], prices)

    ib = IB()
    await ib.connect(host, port, clientId=client_id)

    confirmations = []

    try:
        for symbol in doc["weights"]:
            target = targets.get(symbol, 0)
            current = current_shares.get(symbol, 0)
            delta = target - current

            if delta == 0:
                continue

            contract = Stock(symbol, "SMART", "USD")
            # Qualify contract
            await ib.qualifyContracts(contract)

            order = MarketOrder("BUY" if delta > 0 else "SELL", abs(delta))
            order.tif = "MOC"  # Market-On-Close

            trade = ib.placeOrder(contract, order)
            # Wait briefly for order acknowledgment
            await ib.sleep(0.5)

            confirmations.append({
                "symbol": symbol,
                "side": "BUY" if delta > 0 else "SELL",
                "qty": abs(delta),
                "order_id": trade.order.orderId,
                "status": trade.orderStatus.status,
                "filled": trade.orderStatus.filled,
                "remaining": trade.orderStatus.remaining,
            })

            # Log to audit
            store.log_audit(
                tranche_idx=doc.get("tranche", {}).get("current", 0),
                symbol=symbol,
                side="BUY" if delta > 0 else "SELL",
                qty=abs(delta),
                order_id=str(trade.order.orderId)
            )

    finally:
        await ib.disconnect()

    return confirmations


def save_basket_csv(csv_content: str, path: str = None) -> str:
    """Save basket CSV to file."""
    path = path or str(config.DATA_DIR / f"basket_{datetime.now().strftime('%Y%m%d')}.csv")
    with open(path, "w") as f:
        f.write(csv_content)
    return path


if __name__ == "__main__":
    # Test basket CSV generation
    test_doc = {
        "weights": {"SPY": 0.20, "EFA": 0.20, "IEF": 0.20, "VNQ": 0.20, "DBC": 0.0, "SHV": 0.20}
    }
    current = {"SPY": 100, "EFA": 200, "IEF": 150, "VNQ": 80, "DBC": 0, "SHV": 500}
    csv_out = generate_basket_csv(test_doc, 1_000_000, current)
    print(csv_out)