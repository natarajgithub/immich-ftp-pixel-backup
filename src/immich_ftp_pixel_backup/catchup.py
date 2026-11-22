from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from immich_ftp_pixel_backup.config import AppConfig, UserConfig
from immich_ftp_pixel_backup.immich import ImmichClient
from immich_ftp_pixel_backup.pipeline import AssetPipeline
from immich_ftp_pixel_backup.repository import SyncRepository

if TYPE_CHECKING:
    from immich_ftp_pixel_backup.app import AppState

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
    except Exception as exc:
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


def schedule_catchup_jobs(
    scheduler: BackgroundScheduler,
    *,
    config: AppConfig,
    repo: SyncRepository,
    pipeline: AssetPipeline,
    state: AppState | None = None,
) -> None:
    """Replace all catchup:* jobs from current enabled users."""
    for job in list(scheduler.get_jobs()):
        if str(job.id).startswith("catchup:"):
            job.remove()

    users = (state.config if state is not None else config).enabled_users()
    for user in users:
        uid = user.id
        cron = (state.config if state is not None else config).catchup_cron_for(user)
        trigger = parse_cron(cron)

        def _job(user_id: str = uid) -> None:
            if state is not None:
                current = state.config.user(user_id)
                if current is None or not current.enabled:
                    return
                run_catchup_for_user(
                    current,
                    config=state.config,
                    repo=state.repo,
                    pipeline=state.pipeline,
                )
            else:
                # Legacy path used by unit tests (static config/pipeline).
                match = next((u for u in config.enabled_users() if u.id == user_id), None)
                if match is None:
                    return
                run_catchup_for_user(match, config=config, repo=repo, pipeline=pipeline)

        scheduler.add_job(
            _job,
            trigger=trigger,
            id=f"catchup:{uid}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log.info("scheduled catchup user=%s cron=%s", uid, cron)


def start_scheduler(
    config: AppConfig,
    *,
    repo: SyncRepository,
    pipeline: AssetPipeline,
    state: AppState | None = None,
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    schedule_catchup_jobs(scheduler, config=config, repo=repo, pipeline=pipeline, state=state)
    scheduler.start()
    return scheduler


def next_catchup_run(scheduler: BackgroundScheduler | None, user_id: str) -> str | None:
    if scheduler is None:
        return None
    job = scheduler.get_job(f"catchup:{user_id}")
    if job is None or job.next_run_time is None:
        return None
    return job.next_run_time.isoformat()


def job_ids(scheduler: BackgroundScheduler | None) -> list[str]:
    if scheduler is None:
        return []
    return [str(j.id) for j in scheduler.get_jobs()]
