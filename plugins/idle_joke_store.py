from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from nonebot import logger

from .common import env, load_env_file

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
DEFAULT_DB_PATH = BASE_DIR / "data" / "idle_joke" / "jokes.sqlite3"
DB_PATH = Path(env(ENV_VALUES, "QQ_IDLE_JOKE_DB_PATH", str(DEFAULT_DB_PATH))).resolve()
_DB_PATH_OVERRIDE: Path | None = None

IDLE_JOKE_HASHES_TABLE = "idle_joke_hashes"


def idle_joke_db_path() -> Path:
    return _DB_PATH_OVERRIDE or DB_PATH


def configure_idle_joke_store_for_tests(*, db_path: str | Path | None = None) -> None:
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = Path(db_path).resolve() if db_path else None


def _ensure_db_dir() -> None:
    idle_joke_db_path().parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_db_dir()
    conn = sqlite3.connect(idle_joke_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("<br>", "\n")).strip()


def joke_md5(text: str) -> str:
    normalized = _normalize_text(text)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def ensure_idle_joke_tables() -> None:
    conn = _connect()
    try:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {IDLE_JOKE_HASHES_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                joke_md5 TEXT NOT NULL,
                joke_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_idle_joke_group_md5
            ON {IDLE_JOKE_HASHES_TABLE}(group_id, joke_md5);

            CREATE INDEX IF NOT EXISTS idx_idle_joke_group_created_at
            ON {IDLE_JOKE_HASHES_TABLE}(group_id, created_at DESC);
            """
        )
        conn.commit()
    finally:
        conn.close()


def has_idle_joke_hash(group_id: int, text: str) -> bool:
    ensure_idle_joke_tables()
    md5 = joke_md5(text)
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT 1 FROM {IDLE_JOKE_HASHES_TABLE} WHERE group_id = ? AND joke_md5 = ? LIMIT 1",
            (int(group_id), md5),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def save_idle_joke_hash(group_id: int, text: str) -> bool:
    ensure_idle_joke_tables()
    normalized = _normalize_text(text)
    if int(group_id) <= 0 or not normalized:
        logger.warning(
            "Idle joke store stage=save_invalid group_id={} reason=missing_required_fields",
            group_id,
        )
        return False
    conn = _connect()
    try:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {IDLE_JOKE_HASHES_TABLE}(group_id, joke_md5, joke_text, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (int(group_id), joke_md5(normalized), normalized, _utc_now_iso()),
        )
        inserted = conn.total_changes > 0
        conn.commit()
        return inserted
    finally:
        conn.close()


def reset_idle_joke_store() -> None:
    path = idle_joke_db_path()
    if path.exists():
        path.unlink()
