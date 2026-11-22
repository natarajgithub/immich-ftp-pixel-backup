from __future__ import annotations

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


def load_config(app_path: Path, users_path: Path | None = None) -> AppConfig:
    raw = _load_yaml(app_path)
    users_file = Path(users_path or raw.get("users_file") or "config/users.yaml")
    if not users_file.is_absolute():
        # Resolve relative to CWD; Docker mounts usually use absolute paths.
        users_file = Path.cwd() / users_file

    users_raw = _load_yaml(users_file)
    users = users_raw.get("users") or []
    raw["users"] = users
    raw["users_file"] = str(users_file)

    config = AppConfig.model_validate(raw)
    ids = [u.id for u in config.users]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate user id in users.yaml")
    return config
