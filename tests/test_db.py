from pathlib import Path

from immich_ftp_pixel_backup.db import apply_migrations, connect, current_version


def test_apply_initial_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "sync.db"
    conn = connect(db_path)
    version = apply_migrations(conn)
    assert version == 1
    assert current_version(conn) == 1

    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    }
    assert "sync_assets" in tables
    assert "watermarks" in tables
    assert "sync_events" in tables
    assert "catchup_runs" in tables

    # Idempotent second run
    assert apply_migrations(conn) == 1
    conn.close()
