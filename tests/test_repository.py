from pathlib import Path

import pytest

from immich_ftp_pixel_backup.db import apply_migrations, connect
from immich_ftp_pixel_backup.repository import SyncRepository


@pytest.fixture
def repo(tmp_path: Path) -> SyncRepository:
    conn = connect(tmp_path / "sync.db")
    apply_migrations(conn)
    return SyncRepository(conn)


def test_upsert_pending_and_idempotent_when_uploaded(repo: SyncRepository) -> None:
    first = repo.upsert_pending(
        "nat",
        "asset-1",
        source="webhook",
        checksum="abc",
        original_filename="a.jpg",
        immich_created_at="2026-01-01T00:00:00.000Z",
    )
    assert first.status == "pending"
    assert first.checksum == "abc"

    repo.mark_uploading("nat", "asset-1")
    repo.mark_uploaded(
        "nat",
        "asset-1",
        remote_filename="a__asset-1.jpg",
        remote_path="/DCIM/ImmichSync/a__asset-1.jpg",
        file_size_bytes=10,
    )
    uploaded = repo.get_asset("nat", "asset-1")
    assert uploaded is not None
    assert uploaded.status == "uploaded"
    assert uploaded.attempts == 1
    assert uploaded.remote_path.endswith("a__asset-1.jpg")

    again = repo.upsert_pending("nat", "asset-1", source="catchup")
    assert again.status == "uploaded"


def test_mark_failed_increments_attempts_via_uploading(repo: SyncRepository) -> None:
    repo.upsert_pending("nat", "asset-2", source="catchup")
    repo.mark_uploading("nat", "asset-2")
    repo.mark_failed("nat", "asset-2", "ftp down")
    row = repo.get_asset("nat", "asset-2")
    assert row is not None
    assert row.status == "failed"
    assert row.attempts == 1
    assert row.last_error == "ftp down"

    repo.mark_uploading("nat", "asset-2")
    row = repo.get_asset("nat", "asset-2")
    assert row is not None
    assert row.attempts == 2


def test_users_are_isolated(repo: SyncRepository) -> None:
    repo.upsert_pending("nat", "shared-id", source="webhook")
    repo.upsert_pending("partner", "shared-id", source="webhook")
    repo.mark_uploading("nat", "shared-id")
    repo.mark_uploaded(
        "nat",
        "shared-id",
        remote_filename="x.jpg",
        remote_path="/x.jpg",
    )
    nat = repo.get_asset("nat", "shared-id")
    partner = repo.get_asset("partner", "shared-id")
    assert nat is not None and nat.status == "uploaded"
    assert partner is not None and partner.status == "pending"
    assert repo.count_by_status("nat") == {"uploaded": 1}
    assert repo.count_by_status("partner") == {"pending": 1}


def test_watermark_get_set(repo: SyncRepository) -> None:
    assert repo.get_watermark("nat") is None
    repo.set_watermark("nat", "2026-01-01T00:00:00.000Z")
    assert repo.get_watermark("nat") == "2026-01-01T00:00:00.000Z"
    repo.set_watermark("nat", "2026-02-01T00:00:00.000Z")
    assert repo.get_watermark("nat") == "2026-02-01T00:00:00.000Z"


def test_events_trimmed_per_user(repo: SyncRepository) -> None:
    for i in range(5):
        repo.add_event("nat", "info", f"msg-{i}", retain_per_user=3)
    events = repo.recent_events("nat")
    assert len(events) == 3
    assert [e["message"] for e in events] == ["msg-4", "msg-3", "msg-2"]


def test_catchup_run_lifecycle(repo: SyncRepository) -> None:
    run_id = repo.start_catchup_run("nat")
    repo.finish_catchup_run(
        run_id,
        status="ok",
        assets_found=2,
        assets_uploaded=1,
        assets_failed=1,
    )
    row = repo._conn.execute(
        "SELECT * FROM catchup_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "ok"
    assert row["assets_found"] == 2
    assert row["finished_at"] is not None
