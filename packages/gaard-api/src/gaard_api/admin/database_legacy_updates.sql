-- One-time repair for metadata databases created before tagged updates existed.
-- It runs only when the first database_updates.sql tag has not been recorded.
-- Column conditions apply to the next SQL command; table, dialect, and phase persist.

-- phase: before-initial
-- dialect: all
-- table: admin_users
-- column-missing: display_name
ALTER TABLE admin_users ADD COLUMN display_name VARCHAR(255) NOT NULL DEFAULT '';
-- column-missing: auth_provider
ALTER TABLE admin_users ADD COLUMN auth_provider VARCHAR(255) NOT NULL DEFAULT 'local';
-- column-missing: role
ALTER TABLE admin_users ADD COLUMN role VARCHAR(50) NOT NULL DEFAULT 'admin';
-- dialect: sqlite
-- column-missing: is_system_admin
ALTER TABLE admin_users ADD COLUMN is_system_admin BOOLEAN NOT NULL DEFAULT 0;
-- column-missing: enterprise_access
ALTER TABLE admin_users ADD COLUMN enterprise_access BOOLEAN NOT NULL DEFAULT 0;
-- column-missing: is_provisioned
ALTER TABLE admin_users ADD COLUMN is_provisioned BOOLEAN NOT NULL DEFAULT 0;
-- dialect: postgresql
-- column-missing: is_system_admin
ALTER TABLE admin_users ADD COLUMN is_system_admin BOOLEAN NOT NULL DEFAULT FALSE;
-- column-missing: enterprise_access
ALTER TABLE admin_users ADD COLUMN enterprise_access BOOLEAN NOT NULL DEFAULT FALSE;
-- column-missing: is_provisioned
ALTER TABLE admin_users ADD COLUMN is_provisioned BOOLEAN NOT NULL DEFAULT FALSE;
-- dialect: sqlite
UPDATE admin_users SET is_system_admin = 1 WHERE id = (SELECT id FROM admin_users WHERE auth_provider = 'local' AND role = 'admin' ORDER BY id LIMIT 1) AND NOT EXISTS (SELECT 1 FROM admin_users WHERE is_system_admin = 1);
UPDATE admin_users SET enterprise_access = 1 WHERE is_system_admin = 1;
-- dialect: postgresql
UPDATE admin_users SET is_system_admin = TRUE WHERE id = (SELECT id FROM admin_users WHERE auth_provider = 'local' AND role = 'admin' ORDER BY id LIMIT 1) AND NOT EXISTS (SELECT 1 FROM admin_users WHERE is_system_admin = TRUE);
UPDATE admin_users SET enterprise_access = TRUE WHERE is_system_admin = TRUE;

-- dialect: sqlite
DROP TABLE IF EXISTS admin_users__migrated;
CREATE TABLE admin_users__migrated (id INTEGER NOT NULL, username VARCHAR(255) NOT NULL, display_name VARCHAR(255) NOT NULL DEFAULT '', auth_provider VARCHAR(255) NOT NULL DEFAULT 'local', role VARCHAR(50) NOT NULL DEFAULT 'admin', is_system_admin BOOLEAN NOT NULL DEFAULT 0, enterprise_access BOOLEAN NOT NULL DEFAULT 0, password_hash TEXT NOT NULL, must_change_password BOOLEAN NOT NULL, is_provisioned BOOLEAN NOT NULL DEFAULT 0, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_admin_users_auth_provider_username UNIQUE (auth_provider, username));
INSERT INTO admin_users__migrated (id, username, display_name, auth_provider, role, is_system_admin, enterprise_access, password_hash, must_change_password, is_provisioned, created_at, updated_at) SELECT id, CASE WHEN password_hash = 'external$disabled' AND instr(username, ':') > 0 AND (auth_provider = 'local' OR substr(username, 1, instr(username, ':') - 1) = auth_provider) THEN substr(username, instr(username, ':') + 1) ELSE username END, display_name, CASE WHEN auth_provider = 'local' AND password_hash = 'external$disabled' AND instr(username, ':') > 0 THEN substr(username, 1, instr(username, ':') - 1) ELSE auth_provider END, role, is_system_admin, enterprise_access, password_hash, must_change_password, is_provisioned, created_at, updated_at FROM admin_users;
DROP TABLE admin_users;
-- table: admin_users__migrated
ALTER TABLE admin_users__migrated RENAME TO admin_users;

-- dialect: postgresql
-- table: admin_users
DO $$ DECLARE constraint_name TEXT; BEGIN FOR constraint_name IN SELECT constraint_row.conname FROM pg_constraint AS constraint_row JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid WHERE table_row.relname = 'admin_users' AND constraint_row.contype = 'u' AND (SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality) FROM unnest(constraint_row.conkey) WITH ORDINALITY AS key_row(attnum, ordinality) JOIN pg_attribute AS attribute_row ON attribute_row.attrelid = constraint_row.conrelid AND attribute_row.attnum = key_row.attnum) = ARRAY['username']::TEXT[] LOOP EXECUTE format('ALTER TABLE admin_users DROP CONSTRAINT %I', constraint_name); END LOOP; END $$;
UPDATE admin_users SET username = substring(username from position(':' in username) + 1), auth_provider = CASE WHEN auth_provider = 'local' THEN substring(username from 1 for position(':' in username) - 1) ELSE auth_provider END WHERE password_hash = 'external$disabled' AND position(':' in username) > 0 AND (auth_provider = 'local' OR substring(username from 1 for position(':' in username) - 1) = auth_provider);
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint AS constraint_row JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid WHERE table_row.relname = 'admin_users' AND constraint_row.contype = 'u' AND (SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality) FROM unnest(constraint_row.conkey) WITH ORDINALITY AS key_row(attnum, ordinality) JOIN pg_attribute AS attribute_row ON attribute_row.attrelid = constraint_row.conrelid AND attribute_row.attnum = key_row.attnum) = ARRAY['auth_provider', 'username']::TEXT[]) THEN ALTER TABLE admin_users ADD CONSTRAINT uq_admin_users_auth_provider_username UNIQUE (auth_provider, username); END IF; END $$;

-- dialect: all
-- table: admin_sessions
-- column-missing: username
ALTER TABLE admin_sessions ADD COLUMN username VARCHAR(255) DEFAULT '';
-- column-missing: role
ALTER TABLE admin_sessions ADD COLUMN role VARCHAR(50) DEFAULT 'admin';
-- column-missing: auth_provider
ALTER TABLE admin_sessions ADD COLUMN auth_provider VARCHAR(255) DEFAULT 'local';
-- dialect: sqlite
-- column-missing: last_seen
ALTER TABLE admin_sessions ADD COLUMN last_seen DATETIME;
-- dialect: postgresql
-- column-missing: last_seen
ALTER TABLE admin_sessions ADD COLUMN last_seen TIMESTAMP WITH TIME ZONE;
-- dialect: all
UPDATE admin_sessions SET last_seen = created_at WHERE last_seen IS NULL;

-- table: data_query_audit_logs
-- column-missing: type
ALTER TABLE data_query_audit_logs ADD COLUMN type VARCHAR(50) NOT NULL DEFAULT 'info';
-- column-missing: output_classification
ALTER TABLE data_query_audit_logs ADD COLUMN output_classification VARCHAR(50) NOT NULL DEFAULT 'unknown';
-- column-missing: llm_sql_language
ALTER TABLE data_query_audit_logs ADD COLUMN llm_sql_language VARCHAR(50) DEFAULT '';

-- table: overview_widgets
-- column-missing: sql
ALTER TABLE overview_widgets ADD COLUMN sql TEXT DEFAULT '';
-- column-missing: grid_width
ALTER TABLE overview_widgets ADD COLUMN grid_width INTEGER DEFAULT 1;
-- column-missing: grid_height
ALTER TABLE overview_widgets ADD COLUMN grid_height INTEGER DEFAULT 2;
UPDATE overview_widgets SET grid_height = 4 WHERE widget_type <> 'scalar' AND grid_height = 2;
-- column-missing: result_mode
ALTER TABLE overview_widgets ADD COLUMN result_mode VARCHAR(50) DEFAULT 'data';

-- table: external_api_configs
-- column-missing: resources_json
ALTER TABLE external_api_configs ADD COLUMN resources_json TEXT NOT NULL DEFAULT '[]';

-- table: extract_blueprints
-- column-missing: json_schema_json
ALTER TABLE extract_blueprints ADD COLUMN json_schema_json TEXT;
-- column-missing: updated_by
ALTER TABLE extract_blueprints ADD COLUMN updated_by VARCHAR(255) NOT NULL DEFAULT 'system';

-- table: extract_unstructured_source_models
-- column-missing: table_roles_json
ALTER TABLE extract_unstructured_source_models ADD COLUMN table_roles_json TEXT;

-- dialect: sqlite
-- column-exists: case_id_column
-- column-exists: content_column
UPDATE extract_unstructured_source_models SET table_roles_json = json_object(main_table, json_object('case_id_column', COALESCE(case_id_column, ''), 'content_column', COALESCE(content_column, ''))) WHERE table_roles_json IS NULL OR table_roles_json = '';
-- column-exists: case_id_column
-- column-missing: content_column
UPDATE extract_unstructured_source_models SET table_roles_json = json_object(main_table, json_object('case_id_column', COALESCE(case_id_column, ''), 'content_column', '')) WHERE table_roles_json IS NULL OR table_roles_json = '';
-- column-missing: case_id_column
-- column-exists: content_column
UPDATE extract_unstructured_source_models SET table_roles_json = json_object(main_table, json_object('case_id_column', '', 'content_column', COALESCE(content_column, ''))) WHERE table_roles_json IS NULL OR table_roles_json = '';
-- column-missing: case_id_column
-- column-missing: content_column
UPDATE extract_unstructured_source_models SET table_roles_json = json_object(main_table, json_object('case_id_column', '', 'content_column', '')) WHERE table_roles_json IS NULL OR table_roles_json = '';
UPDATE extract_unstructured_source_models SET table_roles_json = '{}' WHERE table_roles_json IS NULL OR table_roles_json = '';
DROP TABLE IF EXISTS extract_unstructured_source_models__migrated;
CREATE TABLE extract_unstructured_source_models__migrated (id INTEGER NOT NULL, datasource_connector_id INTEGER NOT NULL, datasource_connector_key VARCHAR(255) NOT NULL, main_table VARCHAR(255) NOT NULL, table_roles_json TEXT NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_by VARCHAR(255) NOT NULL DEFAULT 'system', PRIMARY KEY (id), CONSTRAINT uq_extract_unstructured_source_models_datasource UNIQUE (datasource_connector_id));
INSERT INTO extract_unstructured_source_models__migrated (id, datasource_connector_id, datasource_connector_key, main_table, table_roles_json, created_at, updated_at, updated_by) SELECT id, datasource_connector_id, datasource_connector_key, main_table, table_roles_json, created_at, updated_at, COALESCE(updated_by, 'system') FROM extract_unstructured_source_models;
DROP TABLE extract_unstructured_source_models;
-- table: extract_unstructured_source_models__migrated
ALTER TABLE extract_unstructured_source_models__migrated RENAME TO extract_unstructured_source_models;

-- dialect: postgresql
-- table: extract_unstructured_source_models
-- column-exists: case_id_column
-- column-exists: content_column
UPDATE extract_unstructured_source_models SET table_roles_json = json_build_object(main_table, json_build_object('case_id_column', COALESCE(case_id_column, ''), 'content_column', COALESCE(content_column, '')))::TEXT WHERE table_roles_json IS NULL OR table_roles_json = '';
-- column-exists: case_id_column
-- column-missing: content_column
UPDATE extract_unstructured_source_models SET table_roles_json = json_build_object(main_table, json_build_object('case_id_column', COALESCE(case_id_column, ''), 'content_column', ''))::TEXT WHERE table_roles_json IS NULL OR table_roles_json = '';
-- column-missing: case_id_column
-- column-exists: content_column
UPDATE extract_unstructured_source_models SET table_roles_json = json_build_object(main_table, json_build_object('case_id_column', '', 'content_column', COALESCE(content_column, '')))::TEXT WHERE table_roles_json IS NULL OR table_roles_json = '';
-- column-missing: case_id_column
-- column-missing: content_column
UPDATE extract_unstructured_source_models SET table_roles_json = json_build_object(main_table, json_build_object('case_id_column', '', 'content_column', ''))::TEXT WHERE table_roles_json IS NULL OR table_roles_json = '';
UPDATE extract_unstructured_source_models SET table_roles_json = '{}' WHERE table_roles_json IS NULL OR table_roles_json = '';
ALTER TABLE extract_unstructured_source_models ALTER COLUMN table_roles_json SET NOT NULL;
-- column-exists: case_id_column
ALTER TABLE extract_unstructured_source_models DROP COLUMN case_id_column;
-- column-exists: content_column
ALTER TABLE extract_unstructured_source_models DROP COLUMN content_column;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'extract_unstructured_source_models'::regclass AND conname = 'uq_extract_unstructured_source_models_datasource') THEN ALTER TABLE extract_unstructured_source_models ADD CONSTRAINT uq_extract_unstructured_source_models_datasource UNIQUE (datasource_connector_id); END IF; END $$;

-- phase: after-initial
-- dialect: all
-- table: datasource_connectors
UPDATE datasource_connectors SET sql_dialect = 'postgres' WHERE sql_dialect = 'postgresql';

-- table: extract_blueprints
-- column-exists: json_schema
-- column-exists: json_schema_json
UPDATE extract_blueprints SET json_schema_json = json_schema WHERE json_schema_json IS NULL;

-- table: widget_tags
INSERT INTO widget_tags (name, created_at) SELECT 'public', CURRENT_TIMESTAMP WHERE NOT EXISTS (SELECT 1 FROM widget_tags WHERE name = 'public');

-- table: overview_widget_tags
INSERT INTO overview_widget_tags (widget_id, tag_name) SELECT widget.id, 'public' FROM overview_widgets AS widget WHERE NOT EXISTS (SELECT 1 FROM overview_widget_tags AS assignment WHERE assignment.widget_id = widget.id AND assignment.tag_name = 'public');

-- table: widget_tags
INSERT INTO widget_tags (name, created_at) SELECT DISTINCT metric.owner_username, CURRENT_TIMESTAMP FROM user_saved_metrics AS metric WHERE metric.owner_username <> '' AND NOT EXISTS (SELECT 1 FROM widget_tags AS existing_tag WHERE existing_tag.name = metric.owner_username);

-- table: overview_widget_tags
INSERT INTO overview_widget_tags (widget_id, tag_name) SELECT widget.id, metric.owner_username FROM overview_widgets AS widget JOIN user_saved_metrics AS metric ON metric.widget_key = widget.widget_key WHERE metric.owner_username <> '' AND NOT EXISTS (SELECT 1 FROM overview_widget_tags AS assignment WHERE assignment.widget_id = widget.id AND assignment.tag_name = metric.owner_username);

-- dialect: sqlite
-- table: data_query_audit_logs
UPDATE data_query_audit_logs SET type = lower(replace(replace(json_extract(metadata_json, '$.audit_type'), ' ', '_'), '-', '_')), metadata_json = json_remove(metadata_json, '$.audit_type') WHERE json_valid(metadata_json) AND json_type(metadata_json, '$.audit_type') = 'text' AND lower(replace(replace(json_extract(metadata_json, '$.audit_type'), ' ', '_'), '-', '_')) IN ('info', 'sql_error', 'access_error');

-- dialect: postgresql
DO $$ DECLARE audit_row RECORD; parsed JSONB; normalized TEXT; BEGIN FOR audit_row IN SELECT id, metadata_json FROM data_query_audit_logs WHERE metadata_json LIKE '%"audit_type"%' LOOP BEGIN parsed := audit_row.metadata_json::JSONB; normalized := lower(replace(replace(parsed ->> 'audit_type', ' ', '_'), '-', '_')); IF normalized IN ('info', 'sql_error', 'access_error') THEN UPDATE data_query_audit_logs SET type = normalized, metadata_json = (parsed - 'audit_type')::TEXT WHERE id = audit_row.id; END IF; EXCEPTION WHEN others THEN NULL; END; END LOOP; END $$;
