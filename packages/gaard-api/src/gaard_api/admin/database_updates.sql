-- Append new tagged groups only at the end of this file. Never edit, reorder, or reuse a tag.
-- Every non-comment line is one SQL command and must end with a semicolon.

-- tag: 2026-07-31.identity-ids-use-admin-user-id.v1
-- table: identity_privilege_datasource_permissions
DELETE FROM identity_privilege_datasource_permissions AS legacy WHERE EXISTS (SELECT 1 FROM admin_users AS mapped_user WHERE legacy.identity_id IN ('local:' || CAST(mapped_user.id AS VARCHAR(255)), mapped_user.auth_provider || ':' || mapped_user.username) AND EXISTS (SELECT 1 FROM identity_privilege_datasource_permissions AS duplicate WHERE duplicate.connector_id = legacy.connector_id AND duplicate.id <> legacy.id AND (duplicate.identity_id = CAST(mapped_user.id AS VARCHAR(255)) OR (duplicate.id < legacy.id AND duplicate.identity_id IN ('local:' || CAST(mapped_user.id AS VARCHAR(255)), mapped_user.auth_provider || ':' || mapped_user.username)))));
UPDATE identity_privilege_datasource_permissions AS permission SET identity_id = (SELECT CAST(mapped_user.id AS VARCHAR(255)) FROM admin_users AS mapped_user WHERE permission.identity_id IN ('local:' || CAST(mapped_user.id AS VARCHAR(255)), mapped_user.auth_provider || ':' || mapped_user.username) ORDER BY mapped_user.id LIMIT 1) WHERE EXISTS (SELECT 1 FROM admin_users AS mapped_user WHERE permission.identity_id IN ('local:' || CAST(mapped_user.id AS VARCHAR(255)), mapped_user.auth_provider || ':' || mapped_user.username));
-- table: identity_privilege_table_permissions
DELETE FROM identity_privilege_table_permissions AS legacy WHERE EXISTS (SELECT 1 FROM admin_users AS mapped_user WHERE legacy.identity_id IN ('local:' || CAST(mapped_user.id AS VARCHAR(255)), mapped_user.auth_provider || ':' || mapped_user.username) AND EXISTS (SELECT 1 FROM identity_privilege_table_permissions AS duplicate WHERE duplicate.connector_id = legacy.connector_id AND duplicate.table_name = legacy.table_name AND duplicate.id <> legacy.id AND (duplicate.identity_id = CAST(mapped_user.id AS VARCHAR(255)) OR (duplicate.id < legacy.id AND duplicate.identity_id IN ('local:' || CAST(mapped_user.id AS VARCHAR(255)), mapped_user.auth_provider || ':' || mapped_user.username)))));
UPDATE identity_privilege_table_permissions AS permission SET identity_id = (SELECT CAST(mapped_user.id AS VARCHAR(255)) FROM admin_users AS mapped_user WHERE permission.identity_id IN ('local:' || CAST(mapped_user.id AS VARCHAR(255)), mapped_user.auth_provider || ':' || mapped_user.username) ORDER BY mapped_user.id LIMIT 1) WHERE EXISTS (SELECT 1 FROM admin_users AS mapped_user WHERE permission.identity_id IN ('local:' || CAST(mapped_user.id AS VARCHAR(255)), mapped_user.auth_provider || ':' || mapped_user.username));

-- tag: 2026-08-01.dashboard-sharing.v1
-- dialect: sqlite
CREATE TABLE IF NOT EXISTS dashboard_shares (id INTEGER NOT NULL, dashboard_id VARCHAR(64) NOT NULL, target_user_id VARCHAR(255) NOT NULL, target_username VARCHAR(255) NOT NULL, access_level VARCHAR(20) NOT NULL, created_by_user_id VARCHAR(255) NOT NULL, created_by_username VARCHAR(255) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_dashboard_shares_dashboard_target UNIQUE (dashboard_id, target_user_id));
-- dialect: postgresql
CREATE TABLE IF NOT EXISTS dashboard_shares (id SERIAL NOT NULL, dashboard_id VARCHAR(64) NOT NULL, target_user_id VARCHAR(255) NOT NULL, target_username VARCHAR(255) NOT NULL, access_level VARCHAR(20) NOT NULL, created_by_user_id VARCHAR(255) NOT NULL, created_by_username VARCHAR(255) NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL, updated_at TIMESTAMP WITH TIME ZONE NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_dashboard_shares_dashboard_target UNIQUE (dashboard_id, target_user_id));
-- dialect: all
CREATE INDEX IF NOT EXISTS ix_dashboard_shares_access_level ON dashboard_shares (access_level);
CREATE INDEX IF NOT EXISTS ix_dashboard_shares_created_by_user_id ON dashboard_shares (created_by_user_id);
CREATE INDEX IF NOT EXISTS ix_dashboard_shares_created_by_username ON dashboard_shares (created_by_username);
CREATE INDEX IF NOT EXISTS ix_dashboard_shares_dashboard_id ON dashboard_shares (dashboard_id);
CREATE INDEX IF NOT EXISTS ix_dashboard_shares_target_user_id ON dashboard_shares (target_user_id);
CREATE INDEX IF NOT EXISTS ix_dashboard_shares_target_username ON dashboard_shares (target_username);

-- tag: 2026-08-12.duckdb-file-connector.initial.v1
-- dialect: sqlite
CREATE TABLE IF NOT EXISTS duckdb_file_imports (id VARCHAR(36) NOT NULL, mode VARCHAR(16) NOT NULL, original_filename VARCHAR(1024) NOT NULL, status VARCHAR(16) NOT NULL, database_url VARCHAR(2048), storage_key VARCHAR(255) NOT NULL, options_json TEXT NOT NULL, created_at DATETIME NOT NULL, started_at DATETIME, completed_at DATETIME, error_message TEXT, PRIMARY KEY (id), UNIQUE (storage_key));
CREATE INDEX IF NOT EXISTS ix_duckdb_file_imports_status ON duckdb_file_imports(status);
CREATE TABLE IF NOT EXISTS duckdb_file_relations (id INTEGER NOT NULL, import_id VARCHAR(36) NOT NULL, source_file VARCHAR(1024) NOT NULL, source_format VARCHAR(32) NOT NULL, source_member VARCHAR(1024), adapter_key VARCHAR(64) NOT NULL, relation_name VARCHAR(255) NOT NULL, row_count INTEGER NOT NULL, column_count INTEGER NOT NULL, source_size_bytes INTEGER NOT NULL, imported_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(import_id) REFERENCES duckdb_file_imports(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS ix_duckdb_file_relations_import_id ON duckdb_file_relations(import_id);
-- dialect: postgresql
CREATE TABLE IF NOT EXISTS duckdb_file_imports (id VARCHAR(36) NOT NULL, mode VARCHAR(16) NOT NULL, original_filename VARCHAR(1024) NOT NULL, status VARCHAR(16) NOT NULL, database_url VARCHAR(2048), storage_key VARCHAR(255) NOT NULL, options_json TEXT NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL, started_at TIMESTAMP WITH TIME ZONE, completed_at TIMESTAMP WITH TIME ZONE, error_message TEXT, PRIMARY KEY (id), UNIQUE (storage_key));
CREATE INDEX IF NOT EXISTS ix_duckdb_file_imports_status ON duckdb_file_imports(status);
CREATE TABLE IF NOT EXISTS duckdb_file_relations (id SERIAL PRIMARY KEY, import_id VARCHAR(36) NOT NULL, source_file VARCHAR(1024) NOT NULL, source_format VARCHAR(32) NOT NULL, source_member VARCHAR(1024), adapter_key VARCHAR(64) NOT NULL, relation_name VARCHAR(255) NOT NULL, row_count INTEGER NOT NULL, column_count INTEGER NOT NULL, source_size_bytes INTEGER NOT NULL, imported_at TIMESTAMP WITH TIME ZONE NOT NULL, FOREIGN KEY(import_id) REFERENCES duckdb_file_imports(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS ix_duckdb_file_relations_import_id ON duckdb_file_relations(import_id);

-- tag: 2026-08-14.duckdb-file-connector.migrate-duckdb-excel.v1
-- dialect: all
-- table: datasource_connectors
UPDATE datasource_connectors SET database_type = 'duckdb-file' WHERE database_type = 'duckdb-excel';
-- table: datasource_connectors
UPDATE datasource_connectors SET database_url = REPLACE(database_url, 'duckdb-excel://', 'duckdb-file://') WHERE database_url LIKE 'duckdb-excel://%';

-- tag: 2026-09-06.analysis-findings.v1
-- dialect: sqlite
CREATE TABLE IF NOT EXISTS analysis_findings (id INTEGER NOT NULL, finding_id VARCHAR(64) NOT NULL, investigation_id VARCHAR(64) NOT NULL, owner_user_id VARCHAR(255) NOT NULL, connector_id INTEGER NOT NULL, business_logic_suggestion_id INTEGER, statement TEXT NOT NULL, finding_type VARCHAR(100) NOT NULL, confidence FLOAT NOT NULL, critique TEXT NOT NULL, scope_json TEXT NOT NULL, evidence_refs_json TEXT NOT NULL, status VARCHAR(50) NOT NULL, evidence_state VARCHAR(50) NOT NULL, decision VARCHAR(50) NOT NULL, decision_confidence FLOAT, verdict TEXT NOT NULL, decision_scope_json TEXT NOT NULL, decision_evidence_refs_json TEXT NOT NULL, decided_by VARCHAR(255) NOT NULL, contract_version VARCHAR(20) NOT NULL, decisions_json TEXT NOT NULL, evidence_updates_json TEXT NOT NULL, used_in_steps_json TEXT NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id));
-- dialect: postgresql
CREATE TABLE IF NOT EXISTS analysis_findings (id SERIAL NOT NULL, finding_id VARCHAR(64) NOT NULL, investigation_id VARCHAR(64) NOT NULL, owner_user_id VARCHAR(255) NOT NULL, connector_id INTEGER NOT NULL, business_logic_suggestion_id INTEGER, statement TEXT NOT NULL, finding_type VARCHAR(100) NOT NULL, confidence FLOAT NOT NULL, critique TEXT NOT NULL, scope_json TEXT NOT NULL, evidence_refs_json TEXT NOT NULL, status VARCHAR(50) NOT NULL, evidence_state VARCHAR(50) NOT NULL, decision VARCHAR(50) NOT NULL, decision_confidence FLOAT, verdict TEXT NOT NULL, decision_scope_json TEXT NOT NULL, decision_evidence_refs_json TEXT NOT NULL, decided_by VARCHAR(255) NOT NULL, contract_version VARCHAR(20) NOT NULL, decisions_json TEXT NOT NULL, evidence_updates_json TEXT NOT NULL, used_in_steps_json TEXT NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL, updated_at TIMESTAMP WITH TIME ZONE NOT NULL, PRIMARY KEY (id));
-- dialect: all
CREATE UNIQUE INDEX IF NOT EXISTS ix_analysis_findings_finding_id ON analysis_findings (finding_id);
CREATE INDEX IF NOT EXISTS ix_analysis_findings_connector_id ON analysis_findings (connector_id);
CREATE INDEX IF NOT EXISTS ix_analysis_findings_decision ON analysis_findings (decision);
CREATE INDEX IF NOT EXISTS ix_analysis_findings_evidence_state ON analysis_findings (evidence_state);
CREATE INDEX IF NOT EXISTS ix_analysis_findings_finding_type ON analysis_findings (finding_type);
CREATE INDEX IF NOT EXISTS ix_analysis_findings_investigation_id ON analysis_findings (investigation_id);
CREATE INDEX IF NOT EXISTS ix_analysis_findings_owner_user_id ON analysis_findings (owner_user_id);
CREATE INDEX IF NOT EXISTS ix_analysis_findings_status ON analysis_findings (status);

-- tag: 2026-09-06.analysis-finding-decisions.v1
-- dialect: sqlite
CREATE TABLE IF NOT EXISTS analysis_finding_decisions (id INTEGER NOT NULL, decision_id VARCHAR(64) NOT NULL, idempotency_key VARCHAR(64) NOT NULL, investigation_id VARCHAR(64) NOT NULL, finding_id VARCHAR(64) NOT NULL, radar_run_id VARCHAR(255) NOT NULL, decision VARCHAR(50) NOT NULL, confidence FLOAT NOT NULL, verdict TEXT NOT NULL, scope_json TEXT NOT NULL, evidence_refs_json TEXT NOT NULL, is_current BOOLEAN NOT NULL, active BOOLEAN NOT NULL, contract_version VARCHAR(20) NOT NULL, actor_id VARCHAR(255) NOT NULL, actor_type VARCHAR(50) NOT NULL, actor_username VARCHAR(255) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_analysis_finding_decisions_idempotency_key UNIQUE (idempotency_key));
-- dialect: postgresql
CREATE TABLE IF NOT EXISTS analysis_finding_decisions (id SERIAL NOT NULL, decision_id VARCHAR(64) NOT NULL, idempotency_key VARCHAR(64) NOT NULL, investigation_id VARCHAR(64) NOT NULL, finding_id VARCHAR(64) NOT NULL, radar_run_id VARCHAR(255) NOT NULL, decision VARCHAR(50) NOT NULL, confidence FLOAT NOT NULL, verdict TEXT NOT NULL, scope_json TEXT NOT NULL, evidence_refs_json TEXT NOT NULL, is_current BOOLEAN NOT NULL, active BOOLEAN NOT NULL, contract_version VARCHAR(20) NOT NULL, actor_id VARCHAR(255) NOT NULL, actor_type VARCHAR(50) NOT NULL, actor_username VARCHAR(255) NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL, updated_at TIMESTAMP WITH TIME ZONE NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_analysis_finding_decisions_idempotency_key UNIQUE (idempotency_key));
-- dialect: all
CREATE UNIQUE INDEX IF NOT EXISTS ix_analysis_finding_decisions_decision_id ON analysis_finding_decisions (decision_id);
CREATE INDEX IF NOT EXISTS ix_analysis_finding_decisions_active ON analysis_finding_decisions (active);
CREATE INDEX IF NOT EXISTS ix_analysis_finding_decisions_actor_id ON analysis_finding_decisions (actor_id);
CREATE INDEX IF NOT EXISTS ix_analysis_finding_decisions_actor_type ON analysis_finding_decisions (actor_type);
CREATE INDEX IF NOT EXISTS ix_analysis_finding_decisions_decision ON analysis_finding_decisions (decision);
CREATE INDEX IF NOT EXISTS ix_analysis_finding_decisions_finding_id ON analysis_finding_decisions (finding_id);
CREATE INDEX IF NOT EXISTS ix_analysis_finding_decisions_investigation_id ON analysis_finding_decisions (investigation_id);
CREATE INDEX IF NOT EXISTS ix_analysis_finding_decisions_is_current ON analysis_finding_decisions (is_current);
CREATE INDEX IF NOT EXISTS ix_analysis_finding_decisions_radar_run_id ON analysis_finding_decisions (radar_run_id);
