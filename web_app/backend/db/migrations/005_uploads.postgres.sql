CREATE TABLE web_upload_usage (user_id TEXT PRIMARY KEY, file_count INTEGER NOT NULL DEFAULT 0, byte_count BIGINT NOT NULL DEFAULT 0);
CREATE TABLE web_upload_assets (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL, kind TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE INDEX web_upload_assets_owner ON web_upload_assets(user_id, project_id);
CREATE TABLE web_upload_links (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL, format TEXT NOT NULL, expires_at BIGINT NOT NULL, remaining INTEGER NOT NULL);
