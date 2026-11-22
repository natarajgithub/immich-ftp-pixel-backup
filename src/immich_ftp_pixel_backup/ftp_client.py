from __future__ import annotations

import posixpath
from dataclasses import dataclass
from ftplib import FTP, error_perm
from io import BytesIO
from pathlib import PurePosixPath


class FtpError(Exception):
    pass


@dataclass(frozen=True)
class FtpTarget:
    host: str
    port: int
    username: str
    password: str
    remote_dir: str


def build_remote_filename(original_filename: str | None, asset_id: str) -> str:
    """Stable FTP name including Immich UUID for correlation."""
    name = (original_filename or "asset").strip() or "asset"
    stem = PurePosixPath(name).stem or "asset"
    suffix = PurePosixPath(name).suffix
    # Keep UUID readable and filesystem-safe.
    safe_stem = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in stem)
    return f"{safe_stem}__{asset_id}{suffix}"


class FtpUploader:
    def __init__(self, target: FtpTarget, *, ftp_factory: type[FTP] = FTP) -> None:
        self._target = target
        self._ftp_factory = ftp_factory

    def ping(self) -> None:
        with self._connect() as ftp:
            ftp.pwd()

    def test_connection(self) -> dict[str, str]:
        """Login and ensure remote_dir is reachable (create if missing)."""
        with self._connect() as ftp:
            self._ensure_dir(ftp, self._target.remote_dir)
            pwd = ftp.pwd()
        return {
            "ok": "true",
            "host": f"{self._target.host}:{self._target.port}",
            "remote_dir": self._target.remote_dir,
            "pwd": pwd,
        }

    def upload_bytes(self, data: bytes, remote_filename: str) -> str:
        remote_path = posixpath.join(self._target.remote_dir.rstrip("/"), remote_filename)
        with self._connect() as ftp:
            self._ensure_dir(ftp, self._target.remote_dir)
            try:
                ftp.storbinary(f"STOR {remote_filename}", BytesIO(data))
            except error_perm as exc:
                raise FtpError(f"FTP STOR failed for {remote_path}: {exc}") from exc
        return remote_path

    def _connect(self) -> FTP:
        ftp = self._ftp_factory()
        try:
            ftp.connect(self._target.host, self._target.port, timeout=30)
            ftp.login(self._target.username, self._target.password)
            # Prefer passive mode for phone FTP servers.
            ftp.set_pasv(True)
        except Exception as exc:  # noqa: BLE001 - map all connect/login failures
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass
            raise FtpError(f"FTP connect/login failed: {exc}") from exc
        return ftp

    def _ensure_dir(self, ftp: FTP, remote_dir: str) -> None:
        parts = [p for p in remote_dir.split("/") if p]
        path = ""
        # Absolute paths on Android often start with /storage/...
        if remote_dir.startswith("/"):
            path = ""
        for part in parts:
            path = f"{path}/{part}" if path else f"/{part}" if remote_dir.startswith("/") else part
            try:
                ftp.cwd(path)
            except error_perm:
                try:
                    ftp.mkd(path)
                    ftp.cwd(path)
                except error_perm as exc:
                    raise FtpError(f"FTP cannot create/enter directory {path}: {exc}") from exc
        # Leave CWD at remote_dir for STOR relative name.
        try:
            ftp.cwd(remote_dir)
        except error_perm as exc:
            raise FtpError(f"FTP cannot cwd to {remote_dir}: {exc}") from exc
