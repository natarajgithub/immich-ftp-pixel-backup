from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from immich_ftp_pixel_backup.app import build_app
from immich_ftp_pixel_backup.config import load_config
from immich_ftp_pixel_backup.db import apply_migrations, connect
from immich_ftp_pixel_backup.immich import ImmichAsset, ImmichError
from immich_ftp_pixel_backup.pipeline import AssetPipeline
from immich_ftp_pixel_backup.repository import SyncRepository


class FakeImmich:
    def __init__(self) -> None:
        self.assets = {
            "a1": ImmichAsset(
                id="a1",
                checksum="c",
                original_file_name="x.jpg",
                file_created_at=None,
                created_at="2026-01-01T00:00:00.000Z",
                file_size=1,
                owner_id="u",
                raw={},
            )
        }
        self.blobs = {"a1": b"X"}

    def get_asset(self, asset_id: str) -> ImmichAsset:
        if asset_id not in self.assets:
            raise ImmichError("missing", status_code=404)
        return self.assets[asset_id]

    def download_original(self, asset_id: str) -> bytes:
        return self.blobs[asset_id]

    def close(self) -> None:
        return None


class FakeFtp:
    def upload_bytes(self, data: bytes, remote_filename: str) -> str:
        return f"/r/{remote_filename}"

    def ping(self) -> None:
        return None


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    users = {
        "users": [
            {
                "id": "nat",
                "enabled": True,
                "immich_api_key": "ikey",
                "webhook_token": "secret",
                "ftp": {
                    "host": "pixel.example.local",
                    "port": 12345,
                    "username": "photo",
                    "password": "pw",
                    "remote_dir": "/DCIM/ImmichSync",
                },
            }
        ]
    }
    users_path = tmp_path / "users.yaml"
    users_path.write_text(yaml.safe_dump(users), encoding="utf-8")
    app_path = tmp_path / "app.yaml"
    app_path.write_text(
        yaml.safe_dump(
            {
                "immich_url": "http://immich.test",
                "database_path": str(tmp_path / "sync.db"),
                "users_file": str(users_path),
                "catchup_cron": "*/5 * * * *",
            }
        ),
        encoding="utf-8",
    )
    config = load_config(app_path)
    conn = connect(config.database_path)
    apply_migrations(conn)
    repo = SyncRepository(conn)
    immich = FakeImmich()
    ftp = FakeFtp()
    pipeline = AssetPipeline(
        repo,
        immich_factory=lambda _k: immich,
        ftp_factory=lambda _u: ftp,
    )
    app = build_app(
        config,
        repo=repo,
        pipeline=pipeline,
        conn=conn,
        app_config_path=app_path,
        users_config_path=users_path,
    )
    return TestClient(app)


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_and_status(client: TestClient) -> None:
    assert client.get("/ready").status_code == 200
    st = client.get("/status").json()
    assert "nat" in st["users"]
    assert client.get("/status/nat").status_code == 200
    assert client.get("/status/missing").status_code == 404


def test_webhook_auth_and_process(client: TestClient) -> None:
    assert client.post("/hooks/nat/asset-create", json={"id": "a1"}).status_code == 401
    assert (
        client.post(
            "/hooks/unknown/asset-create",
            json={"id": "a1"},
            headers={"X-Webhook-Token": "secret"},
        ).status_code
        == 404
    )
    r = client.post(
        "/hooks/nat/asset-create",
        json={"asset": {"id": "a1"}},
        headers={"X-Webhook-Token": "secret"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "uploaded"


def test_webhook_immich_asset_v1_payload(client: TestClient) -> None:
    """Immich AssetCreate webhook nests the id under data.asset.id."""
    r = client.post(
        "/hooks/nat/asset-create",
        json={
            "type": "AssetV1",
            "trigger": "AssetCreate",
            "data": {"asset": {"id": "a1", "type": "IMAGE"}},
        },
        headers={"X-Webhook-Token": "secret"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["asset_id"] == "a1"
    assert body["status"] == "uploaded"


def test_index_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "immich" in r.text.lower()
    assert client.get("/static/icon.svg").status_code == 200
    assert client.get("/static/app.js").status_code == 200
