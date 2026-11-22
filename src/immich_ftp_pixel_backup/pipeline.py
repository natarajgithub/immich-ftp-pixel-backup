from __future__ import annotations

import logging
from dataclasses import dataclass

from immich_ftp_pixel_backup.ftp_client import (
    FtpError,
    build_remote_filename,
)
from immich_ftp_pixel_backup.immich import ImmichAsset, ImmichClient, ImmichError
from immich_ftp_pixel_backup.repository import AssetSource, SyncRepository

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessResult:
    user_id: str
    asset_id: str
    status: str
    remote_path: str | None = None
    error: str | None = None


class AssetPipeline:
    def __init__(
        self,
        repo: SyncRepository,
        *,
        immich_factory,  # Callable[[str], ImmichClient] api_key -> client
        ftp_factory,  # Callable[[str], FtpUploader] user_id -> uploader
        events_retain_per_user: int = 500,
    ) -> None:
        self._repo = repo
        self._immich_factory = immich_factory
        self._ftp_factory = ftp_factory
        self._events_retain = events_retain_per_user

    def process_asset(
        self,
        user_id: str,
        asset_id: str,
        *,
        source: AssetSource,
        api_key: str,
    ) -> ProcessResult:
        existing = self._repo.get_asset(user_id, asset_id)
        if existing is not None and existing.status == "uploaded":
            return ProcessResult(user_id, asset_id, status="uploaded", remote_path=existing.remote_path)

        immich: ImmichClient = self._immich_factory(api_key)
        try:
            try:
                asset = immich.get_asset(asset_id)
            except ImmichError as exc:
                self._repo.upsert_pending(user_id, asset_id, source=source)
                self._repo.mark_uploading(user_id, asset_id)
                self._repo.mark_failed(user_id, asset_id, str(exc))
                self._event(user_id, asset_id, "error", f"immich get failed: {exc}")
                return ProcessResult(user_id, asset_id, status="failed", error=str(exc))

            self._repo.upsert_pending(
                user_id,
                asset_id,
                source=source,
                checksum=asset.checksum,
                original_filename=asset.original_file_name,
                immich_created_at=asset.created_at,
                file_size_bytes=asset.file_size,
            )
            # Re-check after upsert in case of race with uploaded.
            existing = self._repo.get_asset(user_id, asset_id)
            if existing is not None and existing.status == "uploaded":
                return ProcessResult(user_id, asset_id, status="uploaded", remote_path=existing.remote_path)

            return self._upload(user_id, asset, source=source, immich=immich)
        finally:
            immich.close()

    def _upload(
        self,
        user_id: str,
        asset: ImmichAsset,
        *,
        source: AssetSource,
        immich: ImmichClient,
    ) -> ProcessResult:
        remote_name = build_remote_filename(asset.original_file_name, asset.id)
        self._repo.mark_uploading(user_id, asset.id)
        try:
            data = immich.download_original(asset.id)
        except ImmichError as exc:
            self._repo.mark_failed(user_id, asset.id, str(exc))
            self._event(user_id, asset.id, "error", f"immich download failed: {exc}")
            return ProcessResult(user_id, asset.id, status="failed", error=str(exc))

        ftp = self._ftp_factory(user_id)
        try:
            remote_path = ftp.upload_bytes(data, remote_name)
        except FtpError as exc:
            self._repo.mark_failed(user_id, asset.id, str(exc))
            self._event(user_id, asset.id, "error", f"ftp upload failed: {exc}")
            return ProcessResult(user_id, asset.id, status="failed", error=str(exc))

        self._repo.mark_uploaded(
            user_id,
            asset.id,
            remote_filename=remote_name,
            remote_path=remote_path,
            checksum=asset.checksum,
            file_size_bytes=len(data),
        )
        self._event(user_id, asset.id, "info", f"uploaded via {source} to {remote_path}")
        return ProcessResult(user_id, asset.id, status="uploaded", remote_path=remote_path)

    def _event(self, user_id: str, asset_id: str, level: str, message: str) -> None:
        self._repo.add_event(
            user_id,
            level,  # type: ignore[arg-type]
            message,
            asset_id=asset_id,
            retain_per_user=self._events_retain,
        )
        log.log(logging.ERROR if level == "error" else logging.INFO, "%s %s %s", user_id, asset_id, message)
