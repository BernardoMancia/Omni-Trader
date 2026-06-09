-- Migration v5: Performance indexes and data retention policies
-- Run after schema.sql has been applied

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_system_states_region_time
    ON system_states (region, time DESC);

CREATE INDEX IF NOT EXISTS idx_market_data_region_time
    ON market_data (region, time DESC);

CREATE INDEX IF NOT EXISTS idx_trade_logs_region_mode
    ON trade_logs (region, mode, is_deleted, time DESC);

CREATE INDEX IF NOT EXISTS idx_ai_thoughts_region_time
    ON ai_thoughts (region, created_at DESC);

-- TimescaleDB data retention policies
-- market_data: ~260k rows/day from ticks, retain 30 days
SELECT add_retention_policy('market_data', INTERVAL '30 days', if_not_exists => TRUE);

-- ai_thoughts: retain 90 days
SELECT add_retention_policy('ai_thoughts', INTERVAL '90 days', if_not_exists => TRUE);

-- sentiment_scores: retain 90 days
SELECT add_retention_policy('sentiment_scores', INTERVAL '90 days', if_not_exists => TRUE);

-- predictions: retain 90 days
SELECT add_retention_policy('predictions', INTERVAL '90 days', if_not_exists => TRUE);

-- system_states: retain 180 days
SELECT add_retention_policy('system_states', INTERVAL '180 days', if_not_exists => TRUE);

-- Note: price_history and trade_logs are NOT subject to retention
-- as they contain critical historical data needed for training and auditing.
