-- NS-DB — centralized strategy data store (PostgreSQL, project_alpha)
-- Idempotent DDL. Run via: psql -d project_alpha -f common/schema.sql
-- or programmatically via common.db.ensure_schema().

-- ── Portfolio (replaces paper_portfolio.json + portfolios.json) ─────────
CREATE TABLE IF NOT EXISTS portfolios (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,        -- 'paper' (live), 'hyperscaler', ...
    kind            TEXT NOT NULL,               -- 'live' | 'policy' | 'backtest'
    initial_balance NUMERIC(14,2) NOT NULL DEFAULT 100000.00,
    cash            NUMERIC(14,2) NOT NULL DEFAULT 0,
    total_nav       NUMERIC(14,2) NOT NULL DEFAULT 0,
    commissions     NUMERIC(14,2) NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS positions (
    id             SERIAL PRIMARY KEY,
    portfolio_id   INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    ticker         VARCHAR(16) NOT NULL,
    shares         NUMERIC(14,4) NOT NULL,
    entry_price    NUMERIC(14,4) NOT NULL,
    current_price  NUMERIC(14,4),
    allocation_pct NUMERIC(6,3),
    strategy       VARCHAR(64),                  -- 'NS-X-fund', ...
    pnl            NUMERIC(14,2) DEFAULT 0,
    pnl_pct        NUMERIC(10,6) DEFAULT 0,
    as_of          DATE NOT NULL,
    UNIQUE (portfolio_id, ticker, as_of)
);

CREATE TABLE IF NOT EXISTS portfolio_guardrails (
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
    as_of        DATE NOT NULL,
    n            INTEGER,
    eff_n        NUMERIC(8,3),
    max_weight   NUMERIC(8,4),
    weights_sum  NUMERIC(8,6),
    min_eff_n    NUMERIC(8,3),
    max_name_w   NUMERIC(8,4),
    PRIMARY KEY (portfolio_id, as_of)
);

CREATE TABLE IF NOT EXISTS portfolio_nav (
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
    date         DATE NOT NULL,
    nav          NUMERIC(14,2) NOT NULL,
    note         TEXT,
    PRIMARY KEY (portfolio_id, date)
);

-- ── Strategy output (replaces selection/signals/blend/alloc JSONs) ──────
CREATE TABLE IF NOT EXISTS strategy_output (
    service      VARCHAR(16) NOT NULL,           -- 'ns5','ns7','ns8','nsx','nspc'
    kind         VARCHAR(16) NOT NULL,           -- 'blend','selection','signals','alloc'
    as_of        DATE NOT NULL,
    payload      JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (service, kind, as_of)
);

-- ── Per-strategy return stream (replaces strategy_streams.json) ─────────
CREATE TABLE IF NOT EXISTS strategy_returns (
    strategy_id VARCHAR(16) NOT NULL,            -- 'ns7','at_val','ns8','cash'
    date        DATE NOT NULL,
    return      DOUBLE PRECISION NOT NULL,       -- computed float (round-trip exact)
    source      VARCHAR(32),                     -- 'live','walkforward','reference'
    PRIMARY KEY (strategy_id, date)
);

-- ── Regime (from common/data/regime_history.db) ─────────────────────────
CREATE TABLE IF NOT EXISTS regime_history (
    date         DATE PRIMARY KEY,
    regime       TEXT NOT NULL,                  -- R1 | R2 | R3 | R4
    confidence   REAL,
    flags        TEXT,
    cpi_yoy      REAL,
    gdp_qoq      REAL,
    unrate       REAL,
    curve_bp     REAL,
    baa_aaa_bp   REAL,
    nfci         REAL,
    vix          REAL,
    corr         REAL,
    wti          REAL,
    recorded_at  TIMESTAMPTZ
);

-- ── NS-6 enforcement logs (from ns6.db) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS drawdown_log (
    id         SERIAL PRIMARY KEY,
    date       DATE,
    current_dd NUMERIC(8,6),
    note       TEXT
);
CREATE TABLE IF NOT EXISTS circuit_breaker_log (
    id      SERIAL PRIMARY KEY,
    date    DATE,
    tripped BOOLEAN,
    reason  TEXT
);
CREATE TABLE IF NOT EXISTS performance_log (
    id     SERIAL PRIMARY KEY,
    date   DATE,
    nav    NUMERIC(14,2),
    return DOUBLE PRECISION
);
