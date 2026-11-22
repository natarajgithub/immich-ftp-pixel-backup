from __future__ import annotations

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from immich_ftp_pixel_backup.config import AppConfig, UserConfig
from immich_ftp_pixel_backup.immich import ImmichClient
from immich_ftp_pixel_backup.pipeline import AssetPipeline
from immich_ftp_pixel_backup.repository import SyncRepository

log = logging.getLogger(__name__)


def parse_cron(expr: str) -> CronTrigger:
    """Parse 5-field cron: min hour day month day_of_week."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"catchup_cron must have 5 fields, got: {expr!r}")
    minute, hour, day, month, day_of_week = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
    )


def run_catchup_for_user(
    user: UserConfig,
    *,
    config: AppConfig,
    repo: SyncRepository,
    pipeline: AssetPipeline,
    immich_factory: Callable[[str], ImmichClient] | None = None,
) -> dict[str, int | str]:
    factory = immich_factory or (lambda key: ImmichClient(config.immich_url, key))
    run_id = repo.start_catchup_run(user.id)
    found = uploaded = failed = 0
    watermark = repo.get_watermark(user.id) or "1970-01-01T00:00:00.000Z"
    newest_created: str | None = None

    try:
        client = factory(user.immich_api_key)
        try:
            for asset in client.search_assets_created_after(watermark):
                found += 1
                result = pipeline.process_asset(
                    user.id,
                    asset.id,
                    source="catchup",
                    api_key=user.immich_api_key,
                )
                if result.status == "uploaded":
                    uploaded += 1
                elif result.status == "failed":
                    failed += 1
                if asset.created_at and (newest_created is None or asset.created_at > newest_created):
                    newest_created = asset.created_at
        finally:
            client.close()

        if newest_created:
            repo.set_watermark(user.id, newest_created)

        status = "ok" if failed == 0 else "error"
        repo.finish_catchup_run(
            run_id,
            status=status,  # type: ignore[arg-type]
            assets_found=found,
            assets_uploaded=uploaded,
            assets_failed=failed,
            error=None if failed == 0 else f"{failed} asset(s) failed",
        )
        return {
            "user_id": user.id,
            "status": status,
            "found": found,
            "uploaded": uploaded,
            "failed": failed,
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("catchup failed user=%s", user.id)
        repo.finish_catchup_run(
            run_id,
            status="error",
            assets_found=found,
            assets_uploaded=uploaded,
            assets_failed=failed,
            error=str(exc),
        )
        return {
            "user_id": user.id,
            "status": "error",
            "found": found,
            "uploaded": uploaded,
            "failed": failed,
            "error": str(exc),
        }


def start_scheduler(
    config: AppConfig,
    *,
    repo: SyncRepository,
    pipeline: AssetPipeline,
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    for user in config.enabled_users():
        cron = config.catchup_cron_for(user)
        trigger = parse_cron(cron)

        def _job(u: UserConfig = user) -> None:
            run_catchup_for_user(u, config=config, repo=repo, pipeline=pipeline)

        scheduler.add_job(
            _job,
            trigger=trigger,
            id=f"catchup:{user.id}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log.info("scheduled catchup user=%s cron=%s", user.id, cron)
    scheduler.start()
    return scheduler
