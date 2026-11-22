from pathlib import Path

import pytest
import yaml

from immich_ftp_pixel_backup.config import load_config


def _write_configs(tmp_path: Path, *, users: list[dict], catchup_cron: str = "*/5 * * * *") -> Path:
    users_path = tmp_path / "users.yaml"
    users_path.write_text(yaml.safe_dump({"users": users}), encoding="utf-8")
    app_path = tmp_path / "app.yaml"
    app_path.write_text(
        yaml.safe_dump(
            {
                "immich_url": "http://immich.test:2283",
                "database_path": str(tmp_path / "sync.db"),
                "catchup_cron": catchup_cron,
                "users_file": str(users_path),
            }
        ),
        encoding="utf-8",
    )
    return app_path


def _user(user_id: str, catchup_cron: str | None = None) -> dict:
    u = {
        "id": user_id,
        "enabled": True,
        "immich_api_key": "key",
        "webhook_token": "tok",
        "ftp": {
            "host": "192.168.2.199",
            "port": 12345,
            "username": "photo",
            "password": "secret",
            "remote_dir": "/storage/emulated/0/DCIM/ImmichSync",
        },
    }
    if catchup_cron is not None:
        u["catchup_cron"] = catchup_cron
    return u


def test_load_users_and_global_catchup_cron(tmp_path: Path) -> None:
    app = _write_configs(tmp_path, users=[_user("nat")])
    cfg = load_config(app)
    assert [u.id for u in cfg.enabled_users()] == ["nat"]
    assert cfg.catchup_cron_for(cfg.users[0]) == "*/5 * * * *"


def test_per_user_catchup_cron_override(tmp_path: Path) -> None:
    app = _write_configs(
        tmp_path,
        users=[_user("nat", catchup_cron="*/10 * * * *")],
        catchup_cron="*/5 * * * *",
    )
    cfg = load_config(app)
    assert cfg.catchup_cron_for(cfg.users[0]) == "*/10 * * * *"


def test_duplicate_user_ids_rejected(tmp_path: Path) -> None:
    app = _write_configs(tmp_path, users=[_user("nat"), _user("NAT")])
    with pytest.raises(ValueError, match="Duplicate user id"):
        load_config(app)
