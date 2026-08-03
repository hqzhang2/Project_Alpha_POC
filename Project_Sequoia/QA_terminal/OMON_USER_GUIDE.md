# OMON (Option Monitor) — User Guide

**Applies to:** Alpha Terminal → OMON tab (QA `:9099` / PROD `:9098`)
**Version:** v2.3.0 (2026-08-03) — vollib pricing engine, dividend-aware, parity checks

---

## 1. What OMON Does

OMON displays the **options chain** for a ticker at a chosen expiration: bid/ask/last, volume, open interest, **implied volatility (IV)**, **probability of finishing ITM**, and the five **Greeks** (delta, gamma, theta, vega, rho). It is a **research/display tool** — it does not place orders.

---

## 2. Screen Layout

| Element | What it shows |
|---|---|
| **Ticker + Spot** | Current underlying price |
| **Last** | Last trade with day change |
| **Exp. move** | Market-implied expected move to expiry = ATM straddle (call + put mid at the strike nearest spot), in $ and % |
| **Expiry dropdown** | Defaults to the nearest **standard monthly** (3rd-Friday) — the most liquid chain. Weeklies are available but wider/illiquid |
| **View dropdown** | `20 OTM (10c/10p)` · `⚠ Parity Violations` · `All Strikes` |
| **Chain table** | Calls left, strike center, puts right. IV cells are **color-tinted** (cool = low IV, hot = high IV relative to the chain). Columns: Vega, Theta, Gamma, Delta, IV, **P(ITM)**, OI, Vol, Last, Bid, Ask |
| **IV Smile chart** | Below the table: IV vs strike for calls (green) and puts (red), parity-clean quotes only |

### Reading the rows
- **Dimmed row (40% opacity)** = no two-sided quote — bid/ask are zero, only a stale last (or nothing). Not tradeable as quoted.
- **⚠ next to strike** = put-call parity violation. Click it for the breakdown (call/put mid, implied forward vs chain median, residual). Hover bid/ask for spread %.
- **P(ITM)** = risk-neutral probability the option finishes in-the-money at expiry (from N(d2)).

---

## 3. Worked Examples

### Example A — "How much could MSFT move by Aug 21?"
1. Type `MSFT`, click **Load** (defaults to nearest monthly).
2. Read the info bar: `Exp. move: ~$27 (±5.5%)` (varies with spot; Aug 3 check: $26.55–27.15).
3. **Interpretation:** the options market prices ~$27 (5.5%) as the one-standard-deviation move to expiry. Roughly 68% of the time MSFT ends within ±$27 of spot. Useful for setting stop/limit bands and for sizing.

### Example B — "Which strike gives me ~30% odds of a breakout?"
1. Load `MSFT` (nearest monthly).
2. Scan the **P(ITM)** column on the call side for ~0.30: e.g. the **500 call P(ITM) ≈ 31%**.
3. **Interpretation:** the market implies about 1-in-3 odds MSFT closes above 500 at expiry. Compare with your own view — if you think it's more likely, the option is "cheap" relative to your forecast; if less likely, it's rich.

### Example C — "Is this quote trustworthy?"
1. Load any ticker, switch View → **⚠ Parity Violations**.
2. **MSFT** typically shows ~20 flagged strikes: e.g. `K=240` with residual **−$104** — the call mid is $142 but the put mid is $0.02 for a $240 strike on a ~$485 stock. Impossible pair; the data is stale/broken. **Do not trade off these quotes.**
3. A clean chain (**IWM**, for instance) shows the green "✅ No parity violations" message — the surface is internally consistent.
4. **Rule of thumb:** prefer rows with a live two-sided quote (not dimmed) and no ⚠. For execution, tighten by checking the spread % on hover (e.g. 1% = tight; 10%+ = wide, expect slippage).

### Example D — "Skew check before buying puts"
1. Load `SPY`, switch to **All Strikes** to see the full smile.
2. Read the **IV Smile chart**: puts (red) rising steeply on the left (low strikes) means downside puts are expensive (fear priced in); calls (green) flat means upside calls are cheap.
3. **Interpretation:** if put IV >> call IV at equivalent moneyness, the market is paying up for downside protection — a covered-call or put-spread seller's environment, not a naked put buyer's.

---

## 4. Methodology & Caveats

1. **Data source: Yahoo Finance (unofficial API).** ~15-min delayed quotes; fundamentals can be stale; rate-limited (occasional 429s). Not execution-grade market data. **Independent-verification advice applies to every number here.**
2. **IV/Greeks are Black-Scholes-Merton (European) with dividend yield** (`q` from Yahoo, unit-guarded). For deep-ITM options on dividend payers, American early-exercise premium is not modeled — negligible for typical display strikes, meaningful for deep-ITM longer-dated calls.
3. **Risk-free rate is a fixed 4.5% proxy**, not the live curve. Small IV-level shifts vs. a full OIS curve; acceptable for relative comparison, not for absolute fair-value claims.
4. **IV is solved from mid price** (bid/ask mid, else last trade). Where no quote exists, IV is left blank — Yahoo's raw IV field is a known quantized placeholder (e.g. 6.25%) and is deliberately **not trusted**.
5. **Expected move = ATM straddle** (call+put mid). It is a market-implied approximation, not a forecast; it excludes tail risk and assumes no early exercise.
6. **P(ITM) is risk-neutral** — it embeds the market's (possibly distorted) IV, not your view. It is not a "real" probability of your scenario.
7. **Parity flags** compare each strike's implied forward against the **chain median**; noise floor = half combined spread + 5¢ and 0.25% of spot. Residuals inside the floor are normal market noise. Flags indicate *inconsistent* quotes, not necessarily tradable arbitrage (transaction costs, borrow, and timing usually eat the edge).
8. **Illiquid rows** (dimmed) have no reliable quote. Volume/OI are shown — prefer strikes with real open interest.
9. **The chart and P(ITM) only use parity-clean, valid-IV quotes** — flagged strikes are excluded so garbage doesn't distort the surface.

---

## 5. Quick Reference

| Task | Where |
|---|---|
| Expected move | Info bar (green) |
| Strike odds | P(ITM) column |
| Quote sanity | ⚠ badge / Violations view / row dimming |
| Skew | IV Smile chart |
| Parity breakdown | Click ⚠ |
| Spread cost | Hover bid/ask |
| Liquid expiry | Default monthly; avoid weeklies |

> ⚠️ **Disclaimer:** OMON is an internal research aid, not financial advice. Always validate against a licensed data source before trading.
