from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from immich_ftp_pixel_backup.config import AppConfig, UserConfig, load_config
from immich_ftp_pixel_backup.db import apply_migrations, connect
from immich_ftp_pixel_backup.ftp_client import FtpTarget, FtpUploader
from immich_ftp_pixel_backup.immich import ImmichClient
from immich_ftp_pixel_backup.pipeline import AssetPipeline
from immich_ftp_pixel_backup.repository import SyncRepository

log = logging.getLogger(__name__)


@dataclass
class AppState:
    config: AppConfig
    repo: SyncRepository
    pipeline: AssetPipeline
    conn: Any


def create_pipeline(config: AppConfig, repo: SyncRepository) -> AssetPipeline:
    users = {u.id: u for u in config.users}

    def immich_factory(api_key: str) -> ImmichClient:
        return ImmichClient(config.immich_url, api_key)

    def ftp_factory(user_id: str) -> FtpUploader:
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


def build_app(
    config: AppConfig,
    *,
    repo: SyncRepository | None = None,
    pipeline: AssetPipeline | None = None,
    conn=None,
) -> FastAPI:
    if repo is None or pipeline is None:
        conn = conn or connect(config.database_path)
        apply_migrations(conn)
        repo = repo or SyncRepository(conn)
        pipeline = pipeline or create_pipeline(config, repo)

    state = AppState(config=config, repo=repo, pipeline=pipeline, conn=conn)
    app = FastAPI(title="immich-ftp-pixel-backup", version="0.1.0")
    app.state.sync = state

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

        # Immich URL reachability is soft at process level; report configured.
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
    def status() -> dict[str, Any]:
        users_out = {}
        for user in state.config.users:
            users_out[user.id] = _user_status(state, user)
        return {"users": users_out}

    @app.get("/status/{user_id}")
    def status_user(user_id: str) -> dict[str, Any]:
        user = state.config.user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="unknown user")
        return _user_status(state, user)

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

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        cards = []
        for user in state.config.users:
            st = _user_status(state, user)
            counts = st["counts"]
            cards.append(
                "<div class='card'>"
                f"<h2>{user.id} {'(disabled)' if not user.enabled else ''}</h2>"
                f"<p>FTP {user.ftp.host}:{user.ftp.port}</p>"
                f"<p>counts: {counts}</p>"
                f"<p>watermark: {st['watermark']}</p>"
                f"<p>last_error: {st['last_error']}</p>"
                "</div>"
            )
        body = "\n".join(cards) or "<p>No users configured.</p>"
        return (
            "<!doctype html><html><head><title>immich-ftp-pixel-backup</title>"
            "<style>body{font-family:sans-serif;margin:2rem}.card{border:1px solid #ccc;"
            "padding:1rem;margin-bottom:1rem;border-radius:8px}</style></head>"
            f"<body><h1>immich-ftp-pixel-backup</h1>{body}</body></html>"
        )

    return app


def _user_status(state: AppState, user: UserConfig) -> dict[str, Any]:
    counts = state.repo.count_by_status(user.id)
    events = state.repo.recent_events(user.id, limit=1)
    last_error = None
    if events and events[0]["level"] == "error":
        last_error = events[0]["message"]
    failed = state.repo._conn.execute(
        """
        SELECT last_error FROM sync_assets
        WHERE user_id = ? AND status = 'failed'
        ORDER BY updated_at DESC LIMIT 1
        """,
        (user.id,),
    ).fetchone()
    if failed and failed["last_error"]:
        last_error = failed["last_error"]
    return {
        "id": user.id,
        "enabled": user.enabled,
        "catchup_cron": state.config.catchup_cron_for(user),
        "ftp": f"{user.ftp.host}:{user.ftp.port}",
        "counts": counts,
        "watermark": state.repo.get_watermark(user.id),
        "last_error": last_error,
    }


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
    return build_app(config)
