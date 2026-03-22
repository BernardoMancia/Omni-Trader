CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

CREATE TABLE IF NOT EXISTS market_data (
    time        TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    symbol      TEXT              NOT NULL,
    bid         DOUBLE PRECISION,
    ask         DOUBLE PRECISION,
    region      TEXT              NOT NULL
);

SELECT create_hypertable('market_data', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS system_states (
    time        TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    region      TEXT              NOT NULL,
    state       TEXT              NOT NULL,
    drawdown    DOUBLE PRECISION  NOT NULL,
    capital_ref DOUBLE PRECISION  NOT NULL DEFAULT 10000
);

SELECT create_hypertable('system_states', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS trade_logs (
    time        TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    symbol      TEXT              NOT NULL,
    side        TEXT              NOT NULL,
    quantity    DOUBLE PRECISION  NOT NULL,
    price       DOUBLE PRECISION,
    mode        TEXT              NOT NULL,
    region      TEXT              NOT NULL
);

SELECT create_hypertable('trade_logs', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_market_data_symbol_time ON market_data (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_trade_logs_symbol_time ON trade_logs (symbol, time DESC);
