from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

AssetStatus = Literal["pending", "uploading", "uploaded", "failed", "skipped"]
AssetSource = Literal["webhook", "catchup", "manual"]
EventLevel = Literal["info", "warn", "error"]
RunStatus = Literal["running", "ok", "error"]


@dataclass(frozen=True)
class SyncAsset:
    user_id: str
    asset_id: str
    status: AssetStatus
    checksum: str | None = None
    original_filename: str | None = None
    remote_filename: str | None = None
    remote_path: str | None = None
    file_size_bytes: int | None = None
    attempts: int = 0
    last_error: str | None = None
    source: AssetSource | None = None
    immich_created_at: str | None = None
    uploaded_at: str | None = None


def _row_to_asset(row: sqlite3.Row) -> SyncAsset:
    return SyncAsset(
        user_id=row["user_id"],
        asset_id=row["asset_id"],
        status=row["status"],
        checksum=row["checksum"],
        original_filename=row["original_filename"],
        remote_filename=row["remote_filename"],
        remote_path=row["remote_path"],
        file_size_bytes=row["file_size_bytes"],
        attempts=row["attempts"],
        last_error=row["last_error"],
        source=row["source"],
        immich_created_at=row["immich_created_at"],
        uploaded_at=row["uploaded_at"],
    )


class SyncRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_asset(self, user_id: str, asset_id: str) -> SyncAsset | None:
        row = self._conn.execute(
            "SELECT * FROM sync_assets WHERE user_id = ? AND asset_id = ?",
            (user_id, asset_id),
        ).fetchone()
        return _row_to_asset(row) if row else None

    def upsert_pending(
        self,
        user_id: str,
        asset_id: str,
        *,
        source: AssetSource,
        checksum: str | None = None,
        original_filename: str | None = None,
        immich_created_at: str | None = None,
        file_size_bytes: int | None = None,
    ) -> SyncAsset:
        """Insert pending row, or leave existing uploaded rows unchanged."""
        existing = self.get_asset(user_id, asset_id)
        if existing is not None:
            return existing

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO sync_assets (
                  user_id, asset_id, checksum, original_filename, file_size_bytes,
                  status, source, immich_created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    user_id,
                    asset_id,
                    checksum,
                    original_filename,
                    file_size_bytes,
                    source,
                    immich_created_at,
                ),
            )
        asset = self.get_asset(user_id, asset_id)
        assert asset is not None
        return asset

    def mark_uploading(self, user_id: str, asset_id: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE sync_assets
                SET status = 'uploading',
                    attempts = attempts + 1,
                    last_error = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE user_id = ? AND asset_id = ?
                """,
                (user_id, asset_id),
            )

    def mark_uploaded(
        self,
        user_id: str,
        asset_id: str,
        *,
        remote_filename: str,
        remote_path: str,
        checksum: str | None = None,
        file_size_bytes: int | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE sync_assets
                SET status = 'uploaded',
                    remote_filename = ?,
                    remote_path = ?,
                    checksum = COALESCE(?, checksum),
                    file_size_bytes = COALESCE(?, file_size_bytes),
                    last_error = NULL,
                    uploaded_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE user_id = ? AND asset_id = ?
                """,
                (
                    remote_filename,
                    remote_path,
                    checksum,
                    file_size_bytes,
                    user_id,
                    asset_id,
                ),
            )

    def mark_failed(self, user_id: str, asset_id: str, error: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE sync_assets
                SET status = 'failed',
                    last_error = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE user_id = ? AND asset_id = ?
                """,
                (error, user_id, asset_id),
            )

    def mark_skipped(self, user_id: str, asset_id: str, reason: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE sync_assets
                SET status = 'skipped',
                    last_error = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE user_id = ? AND asset_id = ?
                """,
                (reason, user_id, asset_id),
            )

    def count_by_status(self, user_id: str) -> dict[str, int]:
        rows = self._conn.execute(
            """
            SELECT status, COUNT(*) AS n
            FROM sync_assets
            WHERE user_id = ?
            GROUP BY status
            """,
            (user_id,),
        ).fetchall()
        return {row["status"]: int(row["n"]) for row in rows}

    def get_watermark(self, user_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT created_after FROM watermarks WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row["created_after"] if row else None

    def set_watermark(self, user_id: str, created_after: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO watermarks (user_id, created_after, updated_at)
                VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(user_id) DO UPDATE SET
                  created_after = excluded.created_after,
                  updated_at = excluded.updated_at
                """,
                (user_id, created_after),
            )

    def add_event(
        self,
        user_id: str,
        level: EventLevel,
        message: str,
        *,
        asset_id: str | None = None,
        retain_per_user: int = 500,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO sync_events (user_id, asset_id, level, message)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, asset_id, level, message),
            )
            self._conn.execute(
                """
                DELETE FROM sync_events
                WHERE user_id = ?
                  AND id NOT IN (
                    SELECT id FROM sync_events
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                  )
                """,
                (user_id, user_id, retain_per_user),
            )

    def recent_events(self, user_id: str, limit: int = 50) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                """
                SELECT * FROM sync_events
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        )

    def recent_failed_assets(self, user_id: str, limit: int = 10) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                """
                SELECT asset_id, last_error, attempts, updated_at, remote_path, original_filename
                FROM sync_assets
                WHERE user_id = ? AND status = 'failed'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        )

    def start_catchup_run(self, user_id: str) -> int:
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO catchup_runs (user_id, status)
                VALUES (?, 'running')
                """,
                (user_id,),
            )
            return int(cur.lastrowid)

    def finish_catchup_run(
        self,
        run_id: int,
        *,
        status: RunStatus,
        assets_found: int = 0,
        assets_uploaded: int = 0,
        assets_failed: int = 0,
        error: str | None = None,
    ) -> None:
        if status == "running":
            raise ValueError("finish status cannot be running")
        with self._conn:
            self._conn.execute(
                """
                UPDATE catchup_runs
                SET finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    status = ?,
                    assets_found = ?,
                    assets_uploaded = ?,
                    assets_failed = ?,
                    error = ?
                WHERE id = ?
                """,
                (
                    status,
                    assets_found,
                    assets_uploaded,
                    assets_failed,
                    error,
                    run_id,
                ),
            )
