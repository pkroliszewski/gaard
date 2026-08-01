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
