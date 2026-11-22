from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from immich_ftp_pixel_backup.app import build_app
from immich_ftp_pixel_backup.config import load_config
from immich_ftp_pixel_backup.db import apply_migrations, connect
from immich_ftp_pixel_backup.ftp_client import FtpError
from immich_ftp_pixel_backup.immich import ImmichAsset
from immich_ftp_pixel_backup.pipeline import AssetPipeline
from immich_ftp_pixel_backup.repository import SyncRepository


class FakeImmich:
    def get_asset(self, asset_id: str) -> ImmichAsset:
        return ImmichAsset(
            id=asset_id,
            checksum="c",
            original_file_name="x.jpg",
            file_created_at=None,
            created_at="2026-01-01T00:00:00.000Z",
            file_size=1,
            owner_id="u",
            raw={},
        )

    def download_original(self, asset_id: str) -> bytes:
        return b"X"

    def search_assets_created_after(self, created_after: str, *, page_size: int = 100):
        return iter([])

    def close(self) -> None:
        return None


class FakeFtp:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def upload_bytes(self, data: bytes, remote_filename: str) -> str:
        return f"/r/{remote_filename}"

    def ping(self) -> None:
        if self.fail:
            raise FtpError("down")

    def test_connection(self) -> dict[str, str]:
        if self.fail:
            raise FtpError("login failed")
        return {"ok": "true", "host": "h:1", "remote_dir": "/d", "pwd": "/d"}


@pytest.fixture
def portal(tmp_path: Path):
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
    # Patch test-ftp to use FakeFtp via state users — monkey via FtpUploader
    client = TestClient(app)
    return client, app_path, users_path, repo, ftp, app


def test_get_config_and_status_rich(portal) -> None:
    client, *_ = portal
    cfg = client.get("/api/config").json()
    assert cfg["users"][0]["id"] == "nat"
    assert cfg["users"][0]["immich_api_key"] == "ikey"
    st = client.get("/api/status").json()
    assert "events" in st["users"]["nat"]
    assert "failed_assets" in st["users"]["nat"]


def test_user_crud_persists_yaml(portal) -> None:
    client, _app_path, users_path, *_ = portal
    body = {
        "id": "partner",
        "enabled": True,
        "immich_api_key": "k2",
        "webhook_token": "tok2",
        "catchup_cron": "*/10 * * * *",
        "ftp": {
            "host": "h",
            "port": 21,
            "username": "u",
            "password": "p",
            "remote_dir": "/x",
        },
    }
    r = client.post("/api/users", json=body)
    assert r.status_code == 200
    disk = yaml.safe_load(users_path.read_text(encoding="utf-8"))
    ids = [u["id"] for u in disk["users"]]
    assert "partner" in ids
    assert client.get("/api/config").json()["users"][-1]["id"] == "partner"

    body["immich_api_key"] = "k3"
    assert client.put("/api/users/partner", json=body).status_code == 200
    disk = yaml.safe_load(users_path.read_text(encoding="utf-8"))
    partner = next(u for u in disk["users"] if u["id"] == "partner")
    assert partner["immich_api_key"] == "k3"

    assert client.delete("/api/users/partner").status_code == 200
    disk = yaml.safe_load(users_path.read_text(encoding="utf-8"))
    assert all(u["id"] != "partner" for u in disk["users"])


def test_put_app_config(portal) -> None:
    client, app_path, *_ = portal
    r = client.put(
        "/api/config/app",
        json={"immich_url": "http://immich2", "catchup_cron": "*/9 * * * *"},
    )
    assert r.status_code == 200
    assert r.json()["immich_url"] == "http://immich2"
    disk = yaml.safe_load(app_path.read_text(encoding="utf-8"))
    assert disk["catchup_cron"] == "*/9 * * * *"


def test_status_includes_failed_asset(portal) -> None:
    client, _, _, repo, *_ = portal
    repo.upsert_pending("nat", "bad-1", source="manual")
    repo.mark_failed("nat", "bad-1", "boom")
    st = client.get("/api/status/nat").json()
    assert st["last_error"] == "boom"
    assert st["failed_assets"][0]["asset_id"] == "bad-1"


def test_test_ftp_ok_and_fail(portal, monkeypatch) -> None:
    client, _, _, _, _, _app = portal
    from immich_ftp_pixel_backup import app as app_mod

    class OkUploader:
        def __init__(self, *a, **k):
            pass

        def test_connection(self):
            return {"ok": "true", "host": "h:1", "remote_dir": "/d", "pwd": "/d"}

    monkeypatch.setattr(app_mod, "FtpUploader", OkUploader)
    r = client.post("/api/users/nat/test-ftp")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    class BadUploader:
        def __init__(self, *a, **k):
            pass

        def test_connection(self):
            raise FtpError("nope")

    monkeypatch.setattr(app_mod, "FtpUploader", BadUploader)
    r = client.post("/api/users/nat/test-ftp")
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "nope" in r.json()["error"]


def test_catchup_now(portal) -> None:
    client, *_ = portal
    r = client.post("/api/users/nat/catchup-now")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
