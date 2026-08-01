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
