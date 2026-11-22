from __future__ import annotations

from ftplib import error_perm
from typing import Self

import pytest

from immich_ftp_pixel_backup.ftp_client import (
    FtpError,
    FtpTarget,
    FtpUploader,
    build_remote_filename,
)


class FakeFTP:
    def __init__(self) -> None:
        self.connected: tuple[str, int] | None = None
        self.logged_in: tuple[str, str] | None = None
        self.pasv: bool | None = None
        self.cwd_paths: list[str] = []
        self.mkd_paths: list[str] = []
        self.stored: list[tuple[str, bytes]] = []
        self.dirs = {"/"}
        self.fail_login = False
        self.fail_store = False

    def connect(self, host: str, port: int = 21, timeout: float | None = None) -> None:
        self.connected = (host, port)

    def login(self, user: str = "", passwd: str = "", acct: str = "") -> None:
        if self.fail_login:
            raise error_perm("530 auth")
        self.logged_in = (user, passwd)

    def set_pasv(self, pasv: bool) -> None:
        self.pasv = pasv

    def pwd(self) -> str:
        return "/"

    def cwd(self, path: str) -> None:
        if path not in self.dirs:
            raise error_perm(f"550 no such dir {path}")
        self.cwd_paths.append(path)

    def mkd(self, path: str) -> None:
        self.mkd_paths.append(path)
        self.dirs.add(path)

    def storbinary(self, cmd: str, fp) -> None:
        if self.fail_store:
            raise error_perm("553 store failed")
        assert cmd.startswith("STOR ")
        name = cmd[5:]
        self.stored.append((name, fp.read()))

    def close(self) -> None:
        return None

    # Context manager support used by FtpUploader._connect as `with`
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


@pytest.fixture
def fake_ftp() -> FakeFTP:
    return FakeFTP()


@pytest.fixture
def uploader(fake_ftp: FakeFTP) -> FtpUploader:
    def factory() -> FakeFTP:
        return fake_ftp

    target = FtpTarget(
        host="pixel.example.local",
        port=12345,
        username="photo",
        password="ftp-secret",
        remote_dir="/storage/emulated/0/DCIM/ImmichSync",
    )
    return FtpUploader(target, ftp_factory=factory)  # type: ignore[arg-type]


def test_build_remote_filename() -> None:
    assert build_remote_filename("IMG_001.jpg", "uuid-1") == "IMG_001__uuid-1.jpg"
    assert build_remote_filename("weird name!.png", "abc") == "weird_name___abc.png"
    assert build_remote_filename(None, "abc") == "asset__abc"


def test_upload_creates_dirs_and_stores(uploader: FtpUploader, fake_ftp: FakeFTP) -> None:
    path = uploader.upload_bytes(b"hello", "pic__uuid.jpg")
    assert path == "/storage/emulated/0/DCIM/ImmichSync/pic__uuid.jpg"
    assert fake_ftp.connected == ("pixel.example.local", 12345)
    assert fake_ftp.logged_in == ("photo", "ftp-secret")
    assert fake_ftp.pasv is True
    assert "/storage/emulated/0/DCIM/ImmichSync" in fake_ftp.dirs
    assert fake_ftp.stored == [("pic__uuid.jpg", b"hello")]


def test_login_failure(uploader: FtpUploader, fake_ftp: FakeFTP) -> None:
    fake_ftp.fail_login = True
    with pytest.raises(FtpError, match="connect/login"):
        uploader.upload_bytes(b"x", "a.jpg")


def test_store_failure(uploader: FtpUploader, fake_ftp: FakeFTP) -> None:
    fake_ftp.fail_store = True
    with pytest.raises(FtpError, match="STOR failed"):
        uploader.upload_bytes(b"x", "a.jpg")


def test_ping(uploader: FtpUploader, fake_ftp: FakeFTP) -> None:
    uploader.ping()
    assert fake_ftp.connected is not None
