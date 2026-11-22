from __future__ import annotations

from pathlib import Path

import yaml

from immich_ftp_pixel_backup.config import (
    AppConfig,
    FtpConfig,
    UserConfig,
    ensure_seed_configs,
    load_config,
    save_app,
    save_users,
)


def test_save_users_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "users.yaml"
    users = [
        UserConfig(
            id="nat",
            immich_api_key="k",
            webhook_token="t",
            ftp=FtpConfig(host="h", port=1, username="u", password="p", remote_dir="/d"),
        )
    ]
    save_users(path, users)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["users"][0]["id"] == "nat"
    assert raw["users"][0]["ftp"]["password"] == "p"


def test_ensure_seed_creates_missing_only(tmp_path: Path) -> None:
    app_path = tmp_path / "app.yaml"
    users_path = tmp_path / "users.yaml"
    seeded = ensure_seed_configs(app_path, users_path)
    assert str(app_path) in seeded
    assert str(users_path) in seeded
    assert app_path.is_file()
    assert users_path.is_file()
    cfg = load_config(app_path, users_path)
    assert cfg.users == []
    assert cfg.immich_url.startswith("http")

    # Second call must not overwrite edits
    save_users(
        users_path,
        [
            UserConfig(
                id="keep",
                immich_api_key="k",
                webhook_token="t",
                ftp=FtpConfig(host="h", port=1, username="u", password="p", remote_dir="/d"),
            )
        ],
    )
    again = ensure_seed_configs(app_path, users_path)
    assert again == []
    cfg2 = load_config(app_path, users_path)
    assert [u.id for u in cfg2.users] == ["keep"]


def test_load_config_boots_from_empty_dir(tmp_path: Path) -> None:
    app_path = tmp_path / "app.yaml"
    users_path = tmp_path / "users.yaml"
    cfg = load_config(app_path, users_path)
    assert cfg.users == []
    assert app_path.is_file()
    assert users_path.is_file()


def test_save_app_and_load(tmp_path: Path) -> None:
    users_path = tmp_path / "users.yaml"
    save_users(
        users_path,
        [
            UserConfig(
                id="a",
                immich_api_key="k",
                webhook_token="t",
                ftp=FtpConfig(host="h", port=1, username="u", password="p", remote_dir="/d"),
            )
        ],
    )
    app_path = tmp_path / "app.yaml"
    cfg = AppConfig(
        immich_url="http://immich",
        database_path=str(tmp_path / "s.db"),
        users_file=str(users_path),
        catchup_cron="*/7 * * * *",
    )
    save_app(app_path, cfg)
    loaded = load_config(app_path)
    assert loaded.catchup_cron == "*/7 * * * *"
    assert loaded.users[0].id == "a"
