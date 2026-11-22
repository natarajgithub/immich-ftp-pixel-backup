from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from immich_ftp_pixel_backup.app import build_app, create_pipeline
from immich_ftp_pixel_backup.catchup import start_scheduler
from immich_ftp_pixel_backup.config import load_config
from immich_ftp_pixel_backup.db import apply_migrations, connect
from immich_ftp_pixel_backup.repository import SyncRepository

log = logging.getLogger("immich_ftp_pixel_backup")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Immich → Pixel FTP sync")
    parser.add_argument("--app-config", type=Path, default=Path("config/app.yaml"))
    parser.add_argument("--users-config", type=Path, default=None)
    parser.add_argument("--init-db", action="store_true", help="Apply migrations and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    users_arg = Path(args.users_config) if args.users_config else None
    # load_config seeds missing YAML (empty users) so a blank /config volume can boot.
    config = load_config(args.app_config, users_arg)
    users_path = users_arg if users_arg is not None else Path(config.users_file)
    conn = connect(config.database_path)
    version = apply_migrations(conn)
    log.info(
        "database ready path=%s schema=%s users=%s",
        config.database_path,
        version,
        [u.id for u in config.enabled_users()],
    )
    if not config.users:
        log.info(
            "no users configured yet — open the portal and add users under Settings "
            "(config paths: app=%s users=%s)",
            args.app_config,
            users_path,
        )

    if args.init_db:
        conn.close()
        return

    repo = SyncRepository(conn)
    pipeline = create_pipeline(config, repo)
    app = build_app(
        config,
        repo=repo,
        pipeline=pipeline,
        conn=conn,
        app_config_path=args.app_config,
        users_config_path=users_path,
    )
    state = app.state.sync
    scheduler = start_scheduler(config, repo=repo, pipeline=pipeline, state=state)
    state.scheduler = scheduler

    try:
        uvicorn.run(app, host=config.http_host, port=config.http_port, log_level="info")
    finally:
        scheduler.shutdown(wait=False)
        conn.close()


if __name__ == "__main__":
    main()
