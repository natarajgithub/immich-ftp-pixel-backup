from __future__ import annotations

from pathlib import Path

import pytest

from immich_ftp_pixel_backup.db import apply_migrations, connect
from immich_ftp_pixel_backup.ftp_client import FtpError
from immich_ftp_pixel_backup.immich import ImmichAsset, ImmichError
from immich_ftp_pixel_backup.pipeline import AssetPipeline
from immich_ftp_pixel_backup.repository import SyncRepository


class FakeImmich:
    def __init__(self) -> None:
        self.assets: dict[str, ImmichAsset] = {}
        self.blobs: dict[str, bytes] = {}
        self.fail_get = False
        self.fail_download = False
        self.closed = False

    def get_asset(self, asset_id: str) -> ImmichAsset:
        if self.fail_get:
            raise ImmichError("get failed", status_code=500)
        if asset_id not in self.assets:
            raise ImmichError("missing", status_code=404)
        return self.assets[asset_id]

    def download_original(self, asset_id: str) -> bytes:
        if self.fail_download:
            raise ImmichError("dl failed", status_code=500)
        return self.blobs[asset_id]

    def close(self) -> None:
        self.closed = True


class FakeFtp:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes]] = []
        self.fail = False

    def upload_bytes(self, data: bytes, remote_filename: str) -> str:
        if self.fail:
            raise FtpError("ftp down")
        self.uploads.append((remote_filename, data))
        return f"/remote/{remote_filename}"


@pytest.fixture
def setup(tmp_path: Path):
    conn = connect(tmp_path / "sync.db")
    apply_migrations(conn)
    repo = SyncRepository(conn)
    immich = FakeImmich()
    ftp = FakeFtp()
    asset = ImmichAsset(
        id="a1",
        checksum="chk",
        original_file_name="shot.jpg",
        file_created_at="2026-01-01T00:00:00.000Z",
        created_at="2026-01-02T00:00:00.000Z",
        file_size=4,
        owner_id="u1",
        raw={},
    )
    immich.assets["a1"] = asset
    immich.blobs["a1"] = b"JPEG"
    pipeline = AssetPipeline(
        repo,
        immich_factory=lambda _key: immich,
        ftp_factory=lambda _user: ftp,
    )
    return repo, immich, ftp, pipeline


def test_process_uploads(setup) -> None:
    repo, immich, ftp, pipeline = setup
    result = pipeline.process_asset("nat", "a1", source="webhook", api_key="k")
    assert result.status == "uploaded"
    assert result.remote_path == "/remote/shot__a1.jpg"
    assert ftp.uploads == [("shot__a1.jpg", b"JPEG")]
    row = repo.get_asset("nat", "a1")
    assert row is not None and row.status == "uploaded"
    assert immich.closed is True


def test_process_idempotent(setup) -> None:
    repo, _immich, ftp, pipeline = setup
    pipeline.process_asset("nat", "a1", source="webhook", api_key="k")
    result = pipeline.process_asset("nat", "a1", source="catchup", api_key="k")
    assert result.status == "uploaded"
    assert len(ftp.uploads) == 1


def test_immich_get_failure(setup) -> None:
    repo, immich, _ftp, pipeline = setup
    immich.fail_get = True
    result = pipeline.process_asset("nat", "a1", source="webhook", api_key="k")
    assert result.status == "failed"
    row = repo.get_asset("nat", "a1")
    assert row is not None and row.status == "failed"


def test_ftp_failure_does_not_mark_uploaded(setup) -> None:
    repo, _immich, ftp, pipeline = setup
    ftp.fail = True
    result = pipeline.process_asset("nat", "a1", source="webhook", api_key="k")
    assert result.status == "failed"
    row = repo.get_asset("nat", "a1")
    assert row is not None and row.status == "failed"
    assert row.last_error == "ftp down"


def test_user_isolation(setup) -> None:
    repo, _immich, ftp, pipeline = setup
    pipeline.process_asset("nat", "a1", source="webhook", api_key="k")
    assert repo.get_asset("partner", "a1") is None
    assert len(ftp.uploads) == 1
