from __future__ import annotations

import argparse
import logging
from pathlib import Path

from immich_ftp_pixel_backup.config import load_config
from immich_ftp_pixel_backup.db import apply_migrations, connect

log = logging.getLogger("immich_ftp_pixel_backup")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Immich → Pixel FTP sync")
    parser.add_argument(
        "--app-config",
        type=Path,
        default=Path("config/app.yaml"),
        help="Path to app.yaml",
    )
    parser.add_argument(
        "--users-config",
        type=Path,
        default=None,
        help="Path to users.yaml (overrides app.yaml users_file)",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Apply SQLite migrations and exit",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = load_config(args.app_config, args.users_config)
    conn = connect(config.database_path)
    version = apply_migrations(conn)
    log.info(
        "database ready path=%s schema=%s users=%s",
        config.database_path,
        version,
        [u.id for u in config.enabled_users()],
    )
    for user in config.enabled_users():
        log.info(
            "user=%s catchup_cron=%s ftp=%s:%s",
            user.id,
            config.catchup_cron_for(user),
            user.ftp.host,
            user.ftp.port,
        )

    if args.init_db:
        conn.close()
        return

    # Worker / FastAPI / scheduler land in follow-up commits.
    log.warning("sync server not implemented yet; use --init-db to apply schema only")
    conn.close()


if __name__ == "__main__":
    main()
