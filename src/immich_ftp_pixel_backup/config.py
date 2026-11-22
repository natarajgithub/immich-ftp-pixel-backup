from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class FtpConfig(BaseModel):
    host: str
    port: int = 12345
    username: str
    password: str
    remote_dir: str


class UserConfig(BaseModel):
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    enabled: bool = True
    immich_api_key: str
    webhook_token: str
    ftp: FtpConfig
    # If set, overrides AppConfig.catchup_cron for this user.
    catchup_cron: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class AppConfig(BaseModel):
    immich_url: str
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    database_path: str = "/data/sync.db"
    catchup_cron: str = "*/5 * * * *"
    ready_require_ftp: bool = False
    events_retain_per_user: int = 500
    users_file: str = "/config/users.yaml"
    users: list[UserConfig] = Field(default_factory=list)

    def enabled_users(self) -> list[UserConfig]:
        return [u for u in self.users if u.enabled]

    def user(self, user_id: str) -> UserConfig | None:
        key = user_id.strip().lower()
        for user in self.users:
            if user.id == key:
                return user
        return None

    def catchup_cron_for(self, user: UserConfig) -> str:
        return user.catchup_cron or self.catchup_cron


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {path}")
    return data


def _atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def default_seed_app(*, users_file: str = "/config/users.yaml") -> AppConfig:
    """Minimal app config used when no app.yaml exists yet."""
    return AppConfig(
        immich_url="http://127.0.0.1:2283",
        http_host="0.0.0.0",
        http_port=8080,
        database_path="/data/sync.db",
        catchup_cron="*/5 * * * *",
        ready_require_ftp=False,
        events_retain_per_user=500,
        users_file=users_file,
        users=[],
    )


def ensure_seed_configs(app_path: Path, users_path: Path) -> list[str]:
    """Create missing app.yaml / users.yaml so a blank volume can boot.

    Never overwrites existing files. Returns list of paths that were seeded.
    """
    seeded: list[str] = []
    users_file = str(users_path)

    if not app_path.is_file():
        save_app(app_path, default_seed_app(users_file=users_file))
        seeded.append(str(app_path))

    if not users_path.is_file():
        # Empty user list — configure via portal after start.
        save_users(users_path, [])
        seeded.append(str(users_path))

    return seeded


def load_config(app_path: Path, users_path: Path | None = None) -> AppConfig:
    users_file = Path(users_path) if users_path is not None else None
    if users_file is None and app_path.is_file():
        preview = _load_yaml(app_path)
        users_file = Path(preview.get("users_file") or "config/users.yaml")
    if users_file is None:
        users_file = Path("config/users.yaml")
    if not users_file.is_absolute():
        users_file = Path.cwd() / users_file

    ensure_seed_configs(app_path, users_file)

    raw = _load_yaml(app_path)
    users_raw = _load_yaml(users_file)
    users = users_raw.get("users") or []
    raw["users"] = users
    raw["users_file"] = str(users_file)

    config = AppConfig.model_validate(raw)
    ids = [u.id for u in config.users]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate user id in users.yaml")
    return config


def save_users(path: Path, users: list[UserConfig]) -> None:
    ids = [u.id for u in users]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate user id")
    payload = {
        "users": [u.model_dump(mode="python") for u in users],
    }
    _atomic_write_yaml(path, payload)


def save_app(path: Path, config: AppConfig) -> None:
    """Persist global app settings (excludes in-memory users list)."""
    payload = {
        "immich_url": config.immich_url,
        "http_host": config.http_host,
        "http_port": config.http_port,
        "database_path": config.database_path,
        "catchup_cron": config.catchup_cron,
        "ready_require_ftp": config.ready_require_ftp,
        "events_retain_per_user": config.events_retain_per_user,
        "users_file": config.users_file,
    }
    _atomic_write_yaml(path, payload)


def user_to_dict(user: UserConfig) -> dict[str, Any]:
    return user.model_dump(mode="python")


def app_public_dict(config: AppConfig) -> dict[str, Any]:
    return {
        "immich_url": config.immich_url,
        "http_host": config.http_host,
        "http_port": config.http_port,
        "database_path": config.database_path,
        "catchup_cron": config.catchup_cron,
        "ready_require_ftp": config.ready_require_ftp,
        "events_retain_per_user": config.events_retain_per_user,
        "users_file": config.users_file,
        "users": [user_to_dict(u) for u in config.users],
    }
