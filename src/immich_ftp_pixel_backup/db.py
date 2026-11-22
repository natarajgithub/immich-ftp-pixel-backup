from __future__ import annotations

import sqlite3
from pathlib import Path

# Packaged with the module; repo also keeps sql/ at project root for readability.
SCHEMA_DIR = Path(__file__).resolve().parent / "sql"


def connect(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if row is None:
        return 0
    ver = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations").fetchone()
    return int(ver["v"])


def apply_migrations(conn: sqlite3.Connection, schema_dir: Path | None = None) -> int:
    """Apply ordered sql/NNN_*.sql files. Returns resulting schema version."""
    directory = schema_dir or SCHEMA_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"Schema directory missing: {directory}")

    applied = current_version(conn)
    files = sorted(directory.glob("*.sql"))
    for sql_path in files:
        # Expect names like 001_initial.sql
        try:
            version = int(sql_path.name.split("_", 1)[0])
        except ValueError as exc:
            raise ValueError(f"Invalid migration name: {sql_path.name}") from exc
        if version <= applied:
            continue
        script = sql_path.read_text(encoding="utf-8")
        with conn:
            conn.executescript(script)
            # 001_initial.sql already inserts version 1; later migrations should too.
            # Ensure version row exists even if a file forgets the insert.
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
        applied = version
    return applied
