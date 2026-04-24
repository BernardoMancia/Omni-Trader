CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

CREATE TABLE IF NOT EXISTS market_data (
    time        TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    symbol      TEXT              NOT NULL,
    bid         DOUBLE PRECISION,
    ask         DOUBLE PRECISION,
    region      TEXT              NOT NULL
);
SELECT create_hypertable('market_data', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS price_history (
    symbol  TEXT            NOT NULL,
    date    DATE            NOT NULL,
    open    DOUBLE PRECISION,
    high    DOUBLE PRECISION,
    low     DOUBLE PRECISION,
    close   DOUBLE PRECISION,
    volume  BIGINT,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS predictions (
    time        TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    symbol      TEXT              NOT NULL,
    direction   TEXT              NOT NULL,
    confidence  DOUBLE PRECISION  NOT NULL,
    horizon_days INTEGER          NOT NULL DEFAULT 1,
    source      TEXT              NOT NULL DEFAULT 'forest',
    region      TEXT              NOT NULL DEFAULT 'US'
);
SELECT create_hypertable('predictions', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS ai_thoughts (
    time        TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    symbol      TEXT              NOT NULL,
    thought     TEXT              NOT NULL,
    rsi         DOUBLE PRECISION,
    macd        DOUBLE PRECISION,
    sentiment   DOUBLE PRECISION,
    rf_signal   TEXT,
    rf_conf     DOUBLE PRECISION,
    ppo_action  TEXT,
    ppo_conf    DOUBLE PRECISION,
    final_action TEXT             NOT NULL DEFAULT 'HOLD',
    region      TEXT              NOT NULL DEFAULT 'US'
);
SELECT create_hypertable('ai_thoughts', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS sentiment_scores (
    time    TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    symbol  TEXT              NOT NULL,
    score   DOUBLE PRECISION  NOT NULL,
    region  TEXT              NOT NULL DEFAULT 'US'
);
SELECT create_hypertable('sentiment_scores', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS system_states (
    time         TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    region       TEXT              NOT NULL,
    state        TEXT              NOT NULL,
    drawdown     DOUBLE PRECISION  NOT NULL,
    max_drawdown DOUBLE PRECISION  NOT NULL DEFAULT 0,
    capital_ref  DOUBLE PRECISION  NOT NULL DEFAULT 10000
);
SELECT create_hypertable('system_states', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS trade_logs (
    time        TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    symbol      TEXT              NOT NULL,
    side        TEXT              NOT NULL,
    quantity    DOUBLE PRECISION  NOT NULL,
    price       DOUBLE PRECISION,
    mode        TEXT              NOT NULL,
    region      TEXT              NOT NULL,
    is_deleted  BOOLEAN           NOT NULL DEFAULT FALSE
);
SELECT create_hypertable('trade_logs', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_market_data_symbol_time   ON market_data (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_trade_logs_symbol_time    ON trade_logs (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_trade_logs_is_deleted     ON trade_logs (is_deleted, time DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_symbol_time     ON sentiment_scores (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol_time   ON predictions (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_ai_thoughts_symbol_time   ON ai_thoughts (symbol, time DESC);
