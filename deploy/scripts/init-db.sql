-- Trinity Guard Database Initialization Script
-- PostgreSQL schema for the Trinity Guard AML compliance system
-- Aligned with Go backend's storeTransaction query

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Create schemas
CREATE SCHEMA IF NOT EXISTS trinity;
CREATE SCHEMA IF NOT EXISTS compliance;
CREATE SCHEMA IF NOT EXISTS audit;

-- =============================================================================
-- Transactions table
-- =============================================================================
-- The Go backend uses `id` (VARCHAR) as the primary key and ON CONFLICT column.
-- This matches the transaction ID passed by the client/demo.
CREATE TABLE IF NOT EXISTS trinity.transactions (
    id              VARCHAR(255) PRIMARY KEY,
    amount          DECIMAL(15,2) NOT NULL,
    currency        VARCHAR(3) DEFAULT 'USD',
    description     TEXT,
    sender          VARCHAR(255) NOT NULL,
    receiver        VARCHAR(255) NOT NULL,
    timestamp       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    status          VARCHAR(50) DEFAULT 'pending',
    category        VARCHAR(50) DEFAULT 'transfer',
    flagged         BOOLEAN DEFAULT FALSE,
    reason          TEXT,
    sar_generated   BOOLEAN DEFAULT FALSE,
    sar_narrative   TEXT,
    processing_time_ms DECIMAL(8,2),
    entities_detected JSONB,
    zk_verified     BOOLEAN DEFAULT FALSE,
    risk_level      VARCHAR(20) DEFAULT 'LOW',
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_transactions_sender ON trinity.transactions(sender);
CREATE INDEX IF NOT EXISTS idx_transactions_receiver ON trinity.transactions(receiver);
CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON trinity.transactions(timestamp);
CREATE INDEX IF NOT EXISTS idx_transactions_flagged ON trinity.transactions(flagged);
CREATE INDEX IF NOT EXISTS idx_transactions_amount ON trinity.transactions(amount);
CREATE INDEX IF NOT EXISTS idx_transactions_status ON trinity.transactions(status);

-- =============================================================================
-- Entity resolution results
-- =============================================================================
CREATE TABLE IF NOT EXISTS trinity.entity_resolutions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    transaction_id  VARCHAR(255) NOT NULL REFERENCES trinity.transactions(id) ON DELETE CASCADE,
    entity_text     VARCHAR(255) NOT NULL,
    entity_type     VARCHAR(50) NOT NULL,
    suspicious      BOOLEAN DEFAULT FALSE,
    confidence      DECIMAL(5,4),
    sanctions_matches JSONB,
    risk_level      VARCHAR(20) DEFAULT 'LOW',
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entity_resolutions_transaction_id ON trinity.entity_resolutions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_entity_resolutions_entity_text ON trinity.entity_resolutions(entity_text);
CREATE INDEX IF NOT EXISTS idx_entity_resolutions_suspicious ON trinity.entity_resolutions(suspicious);

-- =============================================================================
-- Sanctions database (for demo purposes)
-- =============================================================================
CREATE TABLE IF NOT EXISTS compliance.sanctions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sanction_id     VARCHAR(100) UNIQUE NOT NULL,
    name            VARCHAR(255) NOT NULL,
    aliases         JSONB,
    risk_level      VARCHAR(20) NOT NULL,
    sanctions       JSONB,
    jurisdictions   JSONB,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sanctions_name ON compliance.sanctions(name);
CREATE INDEX IF NOT EXISTS idx_sanctions_risk_level ON compliance.sanctions(risk_level);
CREATE INDEX IF NOT EXISTS idx_sanctions_is_active ON compliance.sanctions(is_active);

-- =============================================================================
-- SAR (Suspicious Activity Reports) table
-- =============================================================================
CREATE TABLE IF NOT EXISTS compliance.sars (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sar_id          VARCHAR(100) UNIQUE NOT NULL,
    transaction_id  VARCHAR(255) NOT NULL REFERENCES trinity.transactions(id) ON DELETE CASCADE,
    risk_level      VARCHAR(20) NOT NULL,
    reasoning       TEXT NOT NULL,
    narrative       TEXT,
    recommended_actions JSONB,
    status          VARCHAR(50) DEFAULT 'DRAFT',
    filed_at        TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sars_transaction_id ON compliance.sars(transaction_id);
CREATE INDEX IF NOT EXISTS idx_sars_status ON compliance.sars(status);
CREATE INDEX IF NOT EXISTS idx_sars_filed_at ON compliance.sars(filed_at);

-- =============================================================================
-- Audit log table
-- =============================================================================
CREATE TABLE IF NOT EXISTS audit.audit_logs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    transaction_id  VARCHAR(255),
    service_name    VARCHAR(100) NOT NULL,
    action          VARCHAR(100) NOT NULL,
    details         JSONB,
    user_id         VARCHAR(255),
    ip_address      INET,
    timestamp       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    level           VARCHAR(20) DEFAULT 'INFO'
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_transaction_id ON audit.audit_logs(transaction_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_service_name ON audit.audit_logs(service_name);
CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit.audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_logs_level ON audit.audit_logs(level);

-- =============================================================================
-- Performance metrics table
-- =============================================================================
CREATE TABLE IF NOT EXISTS trinity.performance_metrics (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    service_name    VARCHAR(100) NOT NULL,
    metric_name     VARCHAR(100) NOT NULL,
    metric_value    DECIMAL(15,4),
    unit            VARCHAR(20),
    timestamp       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    additional_data JSONB
);

CREATE INDEX IF NOT EXISTS idx_performance_metrics_service_name ON trinity.performance_metrics(service_name);
CREATE INDEX IF NOT EXISTS idx_performance_metrics_timestamp ON trinity.performance_metrics(timestamp);

-- =============================================================================
-- Insert demo sanctions data
-- =============================================================================
INSERT INTO compliance.sanctions (sanction_id, name, aliases, risk_level, sanctions, jurisdictions) VALUES
('sanction_001', 'M. Emmanuel', '["Maurice Emmanuel", "M. Nyanja", "Maurice Nyanja"]', 'HIGH', '["Asset Freeze", "Travel Ban"]', '["US", "EU", "UK"]'),
('sanction_002', 'Robert Mugabe', '["Bob Mugabe", "R. Mugabe"]', 'HIGH', '["Asset Freeze"]', '["US", "EU"]'),
('sanction_003', 'Martin Finnigan', '["Martin Finn", "M. Finnigan"]', 'HIGH', '["Asset Freeze"]', '["US", "EU"]'),
('sanction_004', 'Moneycorp', '["Money Corp", "Moneycorp Ltd"]', 'HIGH', '["Monitoring"]', '["US"]')
ON CONFLICT (sanction_id) DO NOTHING;

-- =============================================================================
-- Triggers for updated_at timestamp
-- =============================================================================
CREATE OR REPLACE FUNCTION trinity.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_transactions_updated_at ON trinity.transactions;
CREATE TRIGGER update_transactions_updated_at BEFORE UPDATE ON trinity.transactions
    FOR EACH ROW EXECUTE FUNCTION trinity.update_updated_at_column();

DROP TRIGGER IF EXISTS update_sanctions_updated_at ON compliance.sanctions;
CREATE TRIGGER update_sanctions_updated_at BEFORE UPDATE ON compliance.sanctions
    FOR EACH ROW EXECUTE FUNCTION trinity.update_updated_at_column();

DROP TRIGGER IF EXISTS update_sars_updated_at ON compliance.sars;
CREATE TRIGGER update_sars_updated_at BEFORE UPDATE ON compliance.sars
    FOR EACH ROW EXECUTE FUNCTION trinity.update_updated_at_column();

-- =============================================================================
-- Views for statistics
-- =============================================================================
CREATE OR REPLACE VIEW trinity.transaction_stats AS
SELECT
    COUNT(*) as total_transactions,
    COUNT(*) FILTER (WHERE flagged = TRUE) as flagged_transactions,
    COUNT(*) FILTER (WHERE sar_generated = TRUE) as sars_generated,
    AVG(processing_time_ms) as avg_processing_time_ms,
    MAX(processing_time_ms) as max_processing_time_ms,
    MIN(processing_time_ms) as min_processing_time_ms,
    DATE_TRUNC('hour', timestamp) as hour_bucket
FROM trinity.transactions
GROUP BY DATE_TRUNC('hour', timestamp);

CREATE OR REPLACE VIEW trinity.entity_resolution_stats AS
SELECT
    entity_type,
    COUNT(*) as total_entities,
    COUNT(*) FILTER (WHERE suspicious = TRUE) as suspicious_entities,
    AVG(confidence) as avg_confidence,
    MAX(confidence) as max_confidence,
    DATE_TRUNC('hour', created_at) as hour_bucket
FROM trinity.entity_resolutions
GROUP BY entity_type, DATE_TRUNC('hour', created_at);

CREATE OR REPLACE VIEW compliance.compliance_stats AS
SELECT
    COUNT(*) as total_sars,
    COUNT(*) FILTER (WHERE status = 'FILED') as filed_sars,
    risk_level,
    COUNT(*) as count_by_risk,
    DATE_TRUNC('day', created_at) as day_bucket
FROM compliance.sars
GROUP BY risk_level, DATE_TRUNC('day', created_at);

-- =============================================================================
-- Log successful initialization
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE 'Trinity Guard database initialized successfully';
    RAISE NOTICE 'Tables: transactions, entity_resolutions, sanctions, sars, audit_logs, performance_metrics';
    RAISE NOTICE 'Views: transaction_stats, entity_resolution_stats, compliance_stats';
    RAISE NOTICE 'Demo sanctions data inserted (4 records)';
END $$;
