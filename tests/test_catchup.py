from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from immich_ftp_pixel_backup.catchup import parse_cron, run_catchup_for_user, start_scheduler
from immich_ftp_pixel_backup.config import load_config
from immich_ftp_pixel_backup.db import apply_migrations, connect
from immich_ftp_pixel_backup.immich import ImmichAsset
from immich_ftp_pixel_backup.pipeline import AssetPipeline
from immich_ftp_pixel_backup.repository import SyncRepository


def test_parse_cron() -> None:
    trigger = parse_cron("*/5 * * * *")
    assert trigger is not None
    with pytest.raises(ValueError):
        parse_cron("*/5 * * *")


class FakeImmich:
    def __init__(self, assets: list[ImmichAsset]) -> None:
        self._assets = assets
        self.closed = False

    def search_assets_created_after(self, created_after: str, *, page_size: int = 100):
        return iter(self._assets)

    def get_asset(self, asset_id: str) -> ImmichAsset:
        return next(a for a in self._assets if a.id == asset_id)

    def download_original(self, asset_id: str) -> bytes:
        return b"data"

    def close(self) -> None:
        self.closed = True


class FakeFtp:
    def __init__(self) -> None:
        self.n = 0

    def upload_bytes(self, data: bytes, remote_filename: str) -> str:
        self.n += 1
        return f"/r/{remote_filename}"


@pytest.fixture
def env(tmp_path: Path):
    users = {
        "users": [
            {
                "id": "nat",
                "enabled": True,
                "immich_api_key": "k",
                "webhook_token": "t",
                "catchup_cron": "*/10 * * * *",
                "ftp": {
                    "host": "h",
                    "port": 1,
                    "username": "u",
                    "password": "p",
                    "remote_dir": "/d",
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
                "immich_url": "http://immich",
                "database_path": str(tmp_path / "s.db"),
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
    return config, repo


def test_run_catchup_and_watermark(env) -> None:
    config, repo = env
    assets = [
        ImmichAsset(
            id="a1",
            checksum="c1",
            original_file_name="1.jpg",
            file_created_at=None,
            created_at="2026-01-02T00:00:00.000Z",
            file_size=1,
            owner_id="u",
            raw={},
        ),
        ImmichAsset(
            id="a2",
            checksum="c2",
            original_file_name="2.jpg",
            file_created_at=None,
            created_at="2026-01-03T00:00:00.000Z",
            file_size=1,
            owner_id="u",
            raw={},
        ),
    ]
    immich = FakeImmich(assets)
    ftp = FakeFtp()
    pipeline = AssetPipeline(
        repo,
        immich_factory=lambda _k: immich,
        ftp_factory=lambda _u: ftp,
    )
    user = config.users[0]
    result = run_catchup_for_user(
        user,
        config=config,
        repo=repo,
        pipeline=pipeline,
        immich_factory=lambda _k: immich,
    )
    assert result["status"] == "ok"
    assert result["found"] == 2
    assert result["uploaded"] == 2
    assert ftp.n == 2
    assert repo.get_watermark("nat") == "2026-01-03T00:00:00.000Z"


def test_scheduler_registers_per_user_cron(env) -> None:
    config, repo = env
    pipeline = AssetPipeline(
        repo,
        immich_factory=lambda _k: FakeImmich([]),
        ftp_factory=lambda _u: FakeFtp(),
    )
    sched = start_scheduler(config, repo=repo, pipeline=pipeline)
    try:
        jobs = sched.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "catchup:nat"
        assert config.catchup_cron_for(config.users[0]) == "*/10 * * * *"
    finally:
        sched.shutdown(wait=False)
