# immich-ftp-pixel-backup

Multi-user **Immich → Pixel FTP** sync so each user’s phone can rely on Immich while a dedicated Pixel + Google Photos handles cloud backup.

Repo: https://github.com/natarajgithub/immich-ftp-pixel-backup

## Status

Phases 0–8 implemented with tests on `feat/phased-implementation` (PR #1).
NAS test container listening on port **8088** (`/health`, `/status`, `/`).

Still needed for end-to-end photo sync: real Immich API key in `users.yaml` + Immich AssetCreate workflow.

## Config

1. Copy `config/app.example.yaml` → `config/app.yaml`
2. Copy `config/users.example.yaml` → `config/users.yaml` and fill secrets

### Catch-up cron

- **Global default:** `catchup_cron` in `app.yaml` (e.g. `"*/5 * * * *"`)
- **Per user:** optional `catchup_cron` in `users.yaml` overrides the global value

### Webhook (planned)

`POST /hooks/{user_id}/asset-create` with `X-Webhook-Token`  
Immich: **Utilities → Workflows** → trigger `AssetCreate` → webhook action.

## Develop

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Init DB only:

```bash
cp config/app.example.yaml config/app.yaml
cp config/users.example.yaml config/users.yaml
# edit database_path / secrets
immich-ftp-pixel-backup --app-config config/app.yaml --init-db
```

## Layout

```
config/          app + users examples
sql/             migrations (also packaged under src/.../sql)
src/immich_ftp_pixel_backup/
docs/
tests/
```
