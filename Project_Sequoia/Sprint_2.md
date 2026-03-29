# Sprint 2 - Option Monitor (OMON)

**Target:** April 7, 2026  
**Focus:** Options Chain

---

## JIRAs

### KAN-23: Yahoo Finance Options Data
- [ ] Fetch options chain for any ticker
- [ ] Parse calls/puts, strikes, expiry dates
- [ ] Handle multiple expirations
- [ ] Output: Options chain JSON

### KAN-24: OMON Screen
- [ ] Ticker search → expiration selector
- [ ] Call chain (left) | Strikes | Put chain (right)
- [ ] Columns: Bid, Ask, Last, Vol, OI
- [ ] Color-coding: green (up), red (down)

### KAN-25: Options Screener (Basic)
- [ ] Filter by IV > threshold
- [ ] Filter by volume > OI
- [ ] Filter by wide bid/ask spread
- [ ] Sort by strike, expiry, IV

### KAN-26: Greeks Calculator
- [ ] Implement Black-Scholes model
- [ ] Calculate Delta, Gamma, Theta, Vega
- [ ] Display Greeks in chain view
- [ ] IV rank/percentile

---

## Done ✅
_(None yet)_
- Sprint 1 (KAN-19 to KAN-22)