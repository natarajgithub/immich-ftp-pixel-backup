-- Immich → Pixel FTP sync state (SQLite)
-- Apply in order; tracked via schema_migrations.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- One row per Immich asset per configured user (users.yaml id).
CREATE TABLE IF NOT EXISTS sync_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  checksum TEXT,
  original_filename TEXT,
  remote_filename TEXT,
  remote_path TEXT,
  file_size_bytes INTEGER,
  status TEXT NOT NULL CHECK (
    status IN (
      'pending',
      'uploading',
      'uploaded',
      'failed',
      'skipped'
    )
  ),
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  source TEXT CHECK (source IS NULL OR source IN ('webhook', 'catchup', 'manual')),
  immich_created_at TEXT,
  uploaded_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  UNIQUE (user_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_sync_assets_user_status
  ON sync_assets (user_id, status);

CREATE INDEX IF NOT EXISTS idx_sync_assets_user_created
  ON sync_assets (user_id, immich_created_at);

-- Catch-up cursor: only assets with Immich createdAt after this ISO timestamp.
CREATE TABLE IF NOT EXISTS watermarks (
  user_id TEXT PRIMARY KEY,
  created_after TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Recent events for the status UI (ring buffer trimmed by the app).
CREATE TABLE IF NOT EXISTS sync_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  asset_id TEXT,
  level TEXT NOT NULL CHECK (level IN ('info', 'warn', 'error')),
  message TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sync_events_user_created
  ON sync_events (user_id, created_at DESC);

-- Catch-up / batch run history (per user).
CREATE TABLE IF NOT EXISTS catchup_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  finished_at TEXT,
  assets_found INTEGER NOT NULL DEFAULT 0,
  assets_uploaded INTEGER NOT NULL DEFAULT 0,
  assets_failed INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'error')),
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_catchup_runs_user_started
  ON catchup_runs (user_id, started_at DESC);

INSERT INTO schema_migrations (version) VALUES (1);
