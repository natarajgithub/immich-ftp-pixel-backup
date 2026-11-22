# immich-ftp-pixel-backup

<img src="assets/icon.svg" width="64" height="64" alt="icon" />

Multi-user **Immich → Pixel FTP** sync so each user’s phone can rely on Immich while a dedicated Pixel + Google Photos handles cloud backup.

Repo: https://github.com/natarajgithub/immich-ftp-pixel-backup

## Features

- Per-user Immich API key + webhook + FTP target
- AssetCreate webhook + catch-up cron
- **Web portal** at `/` — dashboard, YAML config editor, FTP test, live status (2s poll)
- SQLite state + Docker image

LAN-only by design: the portal shows secrets and has **no login**. Do not expose port 8088 to the internet.

## Config

YAML on disk (edited by the portal or by hand). Docker must mount `/config` **read-write**.

**Blank volume bootstrap:** if `/config/app.yaml` or `/config/users.yaml` are missing on startup, the process writes a seed (`immich_url: http://127.0.0.1:2283`, empty `users: []`) and continues. Existing files are never overwritten. Open the portal → Settings to set Immich URL and add users.

Optional manual seed from examples:

```bash
cp config/app.example.yaml runtime-config/app.yaml
cp config/users.example.yaml runtime-config/users.yaml
```

### Webhook

`POST /hooks/{user_id}/asset-create` with header `X-Webhook-Token`  
Immich: **Utilities → Workflows** → trigger `AssetCreate` → webhook action.  
URL must be bare (`http://host:8088/hooks/{user}/asset-create`) — do not put `POST` in the URL field.

## Develop

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

```bash
immich-ftp-pixel-backup --app-config config/app.yaml --users-config config/users.yaml
```

## Docker

```bash
docker compose up -d --build
# portal: http://localhost:8088/
```

NAS overlay example: `docker-compose.nas.yml` (maps appdata config/data, host 8088).

## Layout

```
assets/icon.svg
config/          examples
src/immich_ftp_pixel_backup/
  static/        portal UI
  sql/
tests/
```
