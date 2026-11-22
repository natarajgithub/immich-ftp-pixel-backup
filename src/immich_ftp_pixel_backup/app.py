from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from immich_ftp_pixel_backup.catchup import (
    next_catchup_run,
    run_catchup_for_user,
    schedule_catchup_jobs,
)
from immich_ftp_pixel_backup.config import (
    AppConfig,
    FtpConfig,
    UserConfig,
    app_public_dict,
    load_config,
    save_app,
    save_users,
    user_to_dict,
)
from immich_ftp_pixel_backup.db import apply_migrations, connect
from immich_ftp_pixel_backup.ftp_client import FtpError, FtpTarget, FtpUploader
from immich_ftp_pixel_backup.immich import ImmichClient
from immich_ftp_pixel_backup.pipeline import AssetPipeline
from immich_ftp_pixel_backup.repository import SyncRepository

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass
class AppState:
    config: AppConfig
    repo: SyncRepository
    pipeline: AssetPipeline
    conn: Any
    app_config_path: Path | None = None
    users_config_path: Path | None = None
    scheduler: BackgroundScheduler | None = None
    _lock: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        import threading

        if self._lock is None:
            self._lock = threading.RLock()


class AppSettingsUpdate(BaseModel):
    immich_url: str | None = None
    catchup_cron: str | None = None
    ready_require_ftp: bool | None = None
    events_retain_per_user: int | None = None
    http_host: str | None = None
    http_port: int | None = None
    database_path: str | None = None


class UserUpsert(BaseModel):
    id: str = Field(min_length=1)
    enabled: bool = True
    immich_api_key: str
    webhook_token: str
    catchup_cron: str | None = None
    ftp: FtpConfig


def create_pipeline(config: AppConfig, repo: SyncRepository) -> AssetPipeline:
    def immich_factory(api_key: str) -> ImmichClient:
        return ImmichClient(config.immich_url, api_key)

    def ftp_factory(user_id: str) -> FtpUploader:
        # Resolve user from the pipeline's closed-over config via fresh lookup
        # is not possible here; recreate pipeline on reload so this closes over
        # the latest users map.
        users = {u.id: u for u in config.users}
        user = users[user_id]
        return FtpUploader(
            FtpTarget(
                host=user.ftp.host,
                port=user.ftp.port,
                username=user.ftp.username,
                password=user.ftp.password,
                remote_dir=user.ftp.remote_dir,
            )
        )

    return AssetPipeline(
        repo,
        immich_factory=immich_factory,
        ftp_factory=ftp_factory,
        events_retain_per_user=config.events_retain_per_user,
    )


def _reload_runtime(state: AppState, config: AppConfig) -> None:
    state.config = config
    state.pipeline = create_pipeline(config, state.repo)
    if state.scheduler is not None:
        schedule_catchup_jobs(
            state.scheduler,
            config=config,
            repo=state.repo,
            pipeline=state.pipeline,
            state=state,
        )


def _require_paths(state: AppState) -> tuple[Path, Path]:
    if state.app_config_path is None or state.users_config_path is None:
        raise HTTPException(
            status_code=503,
            detail="config paths not configured; cannot persist from UI",
        )
    return state.app_config_path, state.users_config_path


def build_app(
    config: AppConfig,
    *,
    repo: SyncRepository | None = None,
    pipeline: AssetPipeline | None = None,
    conn=None,
    app_config_path: Path | None = None,
    users_config_path: Path | None = None,
    scheduler: BackgroundScheduler | None = None,
) -> FastAPI:
    if repo is None or pipeline is None:
        conn = conn or connect(config.database_path)
        apply_migrations(conn)
        repo = repo or SyncRepository(conn)
        pipeline = pipeline or create_pipeline(config, repo)

    users_path = users_config_path or Path(config.users_file)
    state = AppState(
        config=config,
        repo=repo,
        pipeline=pipeline,
        conn=conn,
        app_config_path=app_config_path,
        users_config_path=users_path,
        scheduler=scheduler,
    )
    app = FastAPI(title="immich-ftp-pixel-backup", version="0.1.0")
    app.state.sync = state

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> JSONResponse:
        details: dict[str, Any] = {"database": "ok", "users": {}}
        ok = True
        try:
            state.repo.count_by_status("__ping__")
        except Exception as exc:  # noqa: BLE001
            details["database"] = f"error: {exc}"
            ok = False

        details["immich_url"] = state.config.immich_url

        for user in state.config.enabled_users():
            user_info: dict[str, Any] = {"enabled": True}
            if state.config.ready_require_ftp:
                try:
                    FtpUploader(
                        FtpTarget(
                            host=user.ftp.host,
                            port=user.ftp.port,
                            username=user.ftp.username,
                            password=user.ftp.password,
                            remote_dir=user.ftp.remote_dir,
                        )
                    ).ping()
                    user_info["ftp"] = "ok"
                except Exception as exc:  # noqa: BLE001
                    user_info["ftp"] = f"down: {exc}"
                    ok = False
            else:
                user_info["ftp"] = "not_checked"
            details["users"][user.id] = user_info

        status = 200 if ok else 503
        details["status"] = "ready" if ok else "not_ready"
        return JSONResponse(details, status_code=status)

    @app.get("/status")
    @app.get("/api/status")
    def status() -> dict[str, Any]:
        users_out = {}
        for user in state.config.users:
            users_out[user.id] = _user_status(state, user, rich=True)
        return {"users": users_out}

    @app.get("/status/{user_id}")
    @app.get("/api/status/{user_id}")
    def status_user(user_id: str) -> dict[str, Any]:
        user = state.config.user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="unknown user")
        return _user_status(state, user, rich=True)

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return app_public_dict(state.config)

    @app.put("/api/config/app")
    def put_app_config(body: AppSettingsUpdate) -> dict[str, Any]:
        app_path, users_path = _require_paths(state)
        with state._lock:
            data = state.config.model_dump(mode="python")
            updates = body.model_dump(exclude_unset=True)
            data.update(updates)
            data["users"] = [user_to_dict(u) for u in state.config.users]
            try:
                new_config = AppConfig.model_validate(data)
            except ValidationError as exc:
                raise HTTPException(status_code=400, detail=exc.errors()) from exc
            save_app(app_path, new_config)
            # Keep users_file path consistent with on-disk users.yaml
            new_config.users_file = str(users_path)
            _reload_runtime(state, new_config)
        return app_public_dict(state.config)

    @app.put("/api/config/users")
    def put_users(body: list[UserUpsert]) -> dict[str, Any]:
        app_path, users_path = _require_paths(state)
        with state._lock:
            try:
                users = [UserConfig.model_validate(u.model_dump()) for u in body]
            except ValidationError as exc:
                raise HTTPException(status_code=400, detail=exc.errors()) from exc
            save_users(users_path, users)
            new_config = load_config(app_path, users_path)
            _reload_runtime(state, new_config)
        return app_public_dict(state.config)

    @app.post("/api/users")
    def create_user(body: UserUpsert) -> dict[str, Any]:
        app_path, users_path = _require_paths(state)
        with state._lock:
            if state.config.user(body.id) is not None:
                raise HTTPException(status_code=409, detail="user already exists")
            try:
                user = UserConfig.model_validate(body.model_dump())
            except ValidationError as exc:
                raise HTTPException(status_code=400, detail=exc.errors()) from exc
            users = list(state.config.users) + [user]
            save_users(users_path, users)
            new_config = load_config(app_path, users_path)
            _reload_runtime(state, new_config)
        return user_to_dict(user)

    @app.put("/api/users/{user_id}")
    def update_user(user_id: str, body: UserUpsert) -> dict[str, Any]:
        app_path, users_path = _require_paths(state)
        with state._lock:
            if state.config.user(user_id) is None:
                raise HTTPException(status_code=404, detail="unknown user")
            try:
                incoming = UserConfig.model_validate(body.model_dump())
            except ValidationError as exc:
                raise HTTPException(status_code=400, detail=exc.errors()) from exc
            if incoming.id != user_id.strip().lower():
                raise HTTPException(status_code=400, detail="user id in path/body mismatch")
            users = [incoming if u.id == incoming.id else u for u in state.config.users]
            save_users(users_path, users)
            new_config = load_config(app_path, users_path)
            _reload_runtime(state, new_config)
        return user_to_dict(incoming)

    @app.delete("/api/users/{user_id}")
    def delete_user(user_id: str) -> dict[str, Any]:
        app_path, users_path = _require_paths(state)
        with state._lock:
            if state.config.user(user_id) is None:
                raise HTTPException(status_code=404, detail="unknown user")
            users = [u for u in state.config.users if u.id != user_id.strip().lower()]
            save_users(users_path, users)
            new_config = load_config(app_path, users_path)
            _reload_runtime(state, new_config)
        return {"deleted": user_id.strip().lower()}

    @app.post("/api/users/{user_id}/test-ftp")
    def test_ftp(user_id: str) -> dict[str, Any]:
        user = state.config.user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="unknown user")
        uploader = FtpUploader(
            FtpTarget(
                host=user.ftp.host,
                port=user.ftp.port,
                username=user.ftp.username,
                password=user.ftp.password,
                remote_dir=user.ftp.remote_dir,
            )
        )
        try:
            result = uploader.test_connection()
            return {"ok": True, **{k: v for k, v in result.items() if k != "ok"}}
        except FtpError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)

    @app.post("/api/users/{user_id}/catchup-now")
    def catchup_now(user_id: str) -> dict[str, Any]:
        user = state.config.user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="unknown user")
        if not user.enabled:
            raise HTTPException(status_code=400, detail="user disabled")
        return run_catchup_for_user(
            user,
            config=state.config,
            repo=state.repo,
            pipeline=state.pipeline,
            immich_factory=state.pipeline._immich_factory,
        )

    @app.post("/hooks/{user_id}/asset-create")
    async def asset_create_hook(
        user_id: str,
        request: Request,
        x_webhook_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        user = state.config.user(user_id)
        if user is None or not user.enabled:
            raise HTTPException(status_code=404, detail="unknown user")
        if not x_webhook_token or x_webhook_token != user.webhook_token:
            raise HTTPException(status_code=401, detail="invalid webhook token")

        payload = await request.json()
        asset_id = _extract_asset_id(payload)
        if not asset_id:
            raise HTTPException(status_code=400, detail="asset id missing in payload")

        result = state.pipeline.process_asset(
            user.id,
            asset_id,
            source="webhook",
            api_key=user.immich_api_key,
        )
        return {
            "user_id": result.user_id,
            "asset_id": result.asset_id,
            "status": result.status,
            "remote_path": result.remote_path,
            "error": result.error,
        }

    @app.get("/")
    def index() -> FileResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=500, detail="UI assets missing")
        return FileResponse(index_path)

    return app


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _user_status(state: AppState, user: UserConfig, *, rich: bool = False) -> dict[str, Any]:
    counts = state.repo.count_by_status(user.id)
    events = state.repo.recent_events(user.id, limit=20 if rich else 1)
    last_error = None
    if events and events[0]["level"] == "error":
        last_error = events[0]["message"]
    failed_rows = state.repo.recent_failed_assets(user.id, limit=10 if rich else 1)
    if failed_rows and failed_rows[0]["last_error"]:
        last_error = failed_rows[0]["last_error"]
    out: dict[str, Any] = {
        "id": user.id,
        "enabled": user.enabled,
        "catchup_cron": state.config.catchup_cron_for(user),
        "ftp": f"{user.ftp.host}:{user.ftp.port}",
        "counts": counts,
        "watermark": state.repo.get_watermark(user.id),
        "last_error": last_error,
        "next_catchup": next_catchup_run(state.scheduler, user.id),
    }
    if rich:
        out["events"] = [_row_to_dict(r) for r in events]
        out["failed_assets"] = [_row_to_dict(r) for r in failed_rows]
        out["ftp_detail"] = {
            "host": user.ftp.host,
            "port": user.ftp.port,
            "username": user.ftp.username,
            "remote_dir": user.ftp.remote_dir,
        }
    return out


def _extract_asset_id(payload: dict[str, Any]) -> str | None:
    # Immich workflow webhook payloads vary; accept common shapes.
    # Current Immich AssetCreate webhook:
    #   {"type":"AssetV1","trigger":"AssetCreate","data":{"asset":{"id":"..."}}}
    for key in ("id", "assetId", "asset_id"):
        if payload.get(key):
            return str(payload[key])
    asset = payload.get("asset")
    if isinstance(asset, dict):
        for key in ("id", "assetId", "asset_id"):
            if asset.get(key):
                return str(asset[key])
    data = payload.get("data")
    if isinstance(data, dict):
        nested = _extract_asset_id(data)
        if nested:
            return nested
    return None


def create_app_from_paths(app_config: Path, users_config: Path | None = None) -> FastAPI:
    config = load_config(app_config, users_config)
    users_path = users_config or Path(config.users_file)
    return build_app(config, app_config_path=app_config, users_config_path=users_path)
