# SQLite schema

Database file: path from `app.yaml` → `database_path` (default `/data/sync.db`).

Migrations: `sql/001_initial.sql` … applied in order; versions stored in `schema_migrations`.

## Tables

### `sync_assets`

Idempotent record of each Immich asset handled for a user.

| Column | Notes |
|--------|--------|
| `user_id` | Slug from `users.yaml` (`alice`, …) |
| `asset_id` | Immich asset UUID |
| `checksum` | Immich base64 SHA-1 when known |
| `status` | `pending` → `uploading` → `uploaded` \| `failed` \| `skipped` |
| `source` | `webhook` \| `catchup` \| `manual` |
| Unique | `(user_id, asset_id)` |

### `watermarks`

Catch-up cursor per user: Immich `createdAfter` lower bound (ISO-8601 UTC).

### `sync_events`

Short log for the status UI (`info` / `warn` / `error`), trimmed by `events_retain_per_user`.

### `catchup_runs`

History of catch-up jobs per user (counts + status).

## Status flow

```
webhook/catchup discovers asset
  → insert pending (or skip if uploaded)
  → uploading
  → uploaded | failed (retry later increments attempts)
```
