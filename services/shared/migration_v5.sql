-- v5: Add performance index for system_states queries
CREATE INDEX IF NOT EXISTS idx_system_states_region_time ON system_states (region, time DESC);

-- v5: Add region index on market_data for filtered queries  
CREATE INDEX IF NOT EXISTS idx_market_data_region_time ON market_data (region, time DESC);
