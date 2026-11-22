# Contributing

Thanks for helping improve **immich-ftp-pixel-backup**.

## Develop

```bash
git clone https://github.com/natarajgithub/immich-ftp-pixel-backup.git
cd immich-ftp-pixel-backup
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests
```

## Pull requests

- Keep changes focused; include or update tests for behavior changes.
- Run `pytest` (and `ruff check`) before opening a PR.
- Do not commit real `users.yaml`, API keys, FTP passwords, or `.env` files.
- Prefer clear commit messages that explain *why*.

## Code style

- Python 3.12+
- Package layout under `src/immich_ftp_pixel_backup/`
- Portal static assets live in `src/immich_ftp_pixel_backup/static/`
