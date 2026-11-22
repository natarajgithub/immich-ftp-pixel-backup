#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p runtime-config runtime-data
cp -f config/app.example.yaml runtime-config/app.yaml
cp -f config/users.example.yaml runtime-config/users.yaml

# Point local smoke config at container paths and a dummy Immich URL.
python3 - <<'PY'
from pathlib import Path
import yaml
app = yaml.safe_load(Path("runtime-config/app.yaml").read_text())
app["immich_url"] = "http://127.0.0.1:9"
app["database_path"] = "/data/sync.db"
app["users_file"] = "/config/users.yaml"
app["http_host"] = "0.0.0.0"
app["http_port"] = 8080
Path("runtime-config/app.yaml").write_text(yaml.safe_dump(app), encoding="utf-8")
users = yaml.safe_load(Path("runtime-config/users.yaml").read_text())
for u in users.get("users") or []:
    u["immich_api_key"] = "smoke"
    u["webhook_token"] = "smoke-token"
    u["ftp"]["password"] = "smoke"
Path("runtime-config/users.yaml").write_text(yaml.safe_dump(users), encoding="utf-8")
PY

docker compose build
docker compose up -d
trap 'docker compose down' EXIT

for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${HTTP_PORT:-8088}/health" >/dev/null; then
    echo "health ok"
    curl -sf "http://127.0.0.1:${HTTP_PORT:-8088}/status" | head -c 400
    echo
    exit 0
  fi
  sleep 1
done
echo "health check timed out" >&2
docker compose logs
exit 1
