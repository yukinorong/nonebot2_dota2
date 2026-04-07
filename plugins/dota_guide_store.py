from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nonebot import logger

from .common import env, load_env_file

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
DEFAULT_DB_PATH = BASE_DIR / "data" / "dota_knowledge" / "guide_sources.sqlite3"
DB_PATH = Path(env(ENV_VALUES, "DOTA2_GUIDE_DB_PATH", str(DEFAULT_DB_PATH))).resolve()
_DB_PATH_OVERRIDE: Path | None = None

GUIDE_SOURCES_TABLE = "dota_guide_sources"
GUIDE_SOURCE_TTL_DAYS = max(30, int(env(ENV_VALUES, "DOTA2_GUIDE_SOURCE_TTL_DAYS", "90")))

SOURCE_BASE_WEIGHTS = {
    "official": 1.0,
    "liquipedia": 0.9,
    "high_mmr": 0.8,
    "tavily": 0.65,
    "opendota": 0.75,
}


def _ensure_db_dir() -> None:
    guide_source_db_path().parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_db_dir()
    conn = sqlite3.connect(guide_source_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def configure_dota_guide_store_for_tests(*, db_path: str | Path | None = None) -> None:
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = Path(db_path).resolve() if db_path else None


def guide_source_db_path() -> Path:
    return _DB_PATH_OVERRIDE or DB_PATH


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _content_hash(text: str) -> str:
    normalized = _normalize_text(text).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_dota_version(version: str | None) -> dict[str, Any]:
    raw = str(version or "").strip().lower()
    if not raw:
        return {"raw": "", "major_version": "", "patch_suffix": "", "sortable": None}
    match = re.search(r"(\d+)\.(\d+)([a-z]?)", raw)
    if not match:
        return {"raw": raw, "major_version": "", "patch_suffix": "", "sortable": None}
    major_left = int(match.group(1))
    major_right = int(match.group(2))
    patch_suffix = match.group(3) or ""
    suffix_rank = ord(patch_suffix) - 96 if patch_suffix else 0
    return {
        "raw": f"{major_left}.{major_right}{patch_suffix}",
        "major_version": f"{major_left}.{major_right}",
        "patch_suffix": patch_suffix,
        "sortable": (major_left, major_right, suffix_rank),
    }


def _previous_major_version(major_version: str) -> str:
    parsed = parse_dota_version(major_version)
    sortable = parsed.get("sortable")
    if not sortable:
        return ""
    _, right, _ = sortable
    left = int(major_version.split(".")[0])
    if right <= 0:
        return ""
    return f"{left}.{right - 1}"


def version_weight(*, current_version: str | None, candidate_version: str | None) -> float:
    current = parse_dota_version(current_version)
    candidate = parse_dota_version(candidate_version)
    if not current.get("sortable") or not candidate.get("sortable"):
        return 0.0
    if current["raw"] == candidate["raw"]:
        return 1.0
    if current["major_version"] == candidate["major_version"]:
        return 0.85
    if _previous_major_version(current["major_version"]) == candidate["major_version"]:
        return 0.55
    return 0.0


def ensure_guide_source_tables() -> None:
    conn = _connect()
    try:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {GUIDE_SOURCES_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hero_id INTEGER NOT NULL,
                topic_type TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_url TEXT,
                source_title TEXT,
                content_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                game_version TEXT,
                major_version TEXT,
                published_at TEXT,
                fetched_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                metadata_json TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_guide_sources_unique
            ON {GUIDE_SOURCES_TABLE}(hero_id, topic_type, source_type, content_hash);

            CREATE INDEX IF NOT EXISTS idx_guide_sources_hero_topic
            ON {GUIDE_SOURCES_TABLE}(hero_id, topic_type);

            CREATE INDEX IF NOT EXISTS idx_guide_sources_version
            ON {GUIDE_SOURCES_TABLE}(major_version, game_version);

            CREATE INDEX IF NOT EXISTS idx_guide_sources_fetched_at
            ON {GUIDE_SOURCES_TABLE}(fetched_at);

            CREATE INDEX IF NOT EXISTS idx_guide_sources_expires_at
            ON {GUIDE_SOURCES_TABLE}(expires_at);
            """
        )
        conn.commit()
    finally:
        conn.close()


def reset_dota_guide_store() -> None:
    path = guide_source_db_path()
    if path.exists():
        path.unlink()


def prune_expired_guide_sources(*, now: datetime | None = None) -> int:
    ensure_guide_source_tables()
    current = now or _utc_now()
    conn = _connect()
    try:
        conn.execute(
            f"DELETE FROM {GUIDE_SOURCES_TABLE} WHERE expires_at <= ?",
            (current.isoformat(),),
        )
        deleted = conn.total_changes
        conn.commit()
        return deleted
    finally:
        conn.close()


def save_guide_source(
    *,
    hero_id: int,
    topic_type: str,
    source_type: str,
    content_text: str,
    source_url: str = "",
    source_title: str = "",
    game_version: str = "",
    published_at: str = "",
    fetched_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    ensure_guide_source_tables()
    normalized_content = _normalize_text(content_text)
    if hero_id <= 0 or not topic_type or not source_type or not normalized_content:
        logger.warning(
            "Dota guide store stage=save_invalid hero_id={} topic_type={} source_type={} reason=missing_required_fields",
            hero_id,
            topic_type,
            source_type,
        )
        return False
    fetched_text = fetched_at or _utc_now_iso()
    try:
        fetched_dt = datetime.fromisoformat(fetched_text)
    except ValueError:
        fetched_dt = _utc_now()
        fetched_text = fetched_dt.isoformat()
    expires_at = (fetched_dt + timedelta(days=GUIDE_SOURCE_TTL_DAYS)).isoformat()
    parsed = parse_dota_version(game_version)
    conn = _connect()
    try:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {GUIDE_SOURCES_TABLE} (
                hero_id, topic_type, source_type, source_url, source_title,
                content_text, content_hash, game_version, major_version,
                published_at, fetched_at, expires_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(hero_id),
                str(topic_type),
                str(source_type),
                str(source_url or "").strip(),
                str(source_title or "").strip(),
                normalized_content,
                _content_hash(normalized_content),
                parsed["raw"],
                parsed["major_version"],
                str(published_at or "").strip(),
                fetched_text,
                expires_at,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        inserted = conn.total_changes > 0
        conn.commit()
        return inserted
    finally:
        conn.close()


def get_guide_sources(
    hero_id: int,
    *,
    current_version: str | None = None,
    include_background: bool = True,
    limit: int = 12,
) -> list[dict[str, Any]]:
    ensure_guide_source_tables()
    prune_expired_guide_sources()
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM {GUIDE_SOURCES_TABLE}
            WHERE hero_id = ?
            ORDER BY fetched_at DESC, id DESC
            """,
            (int(hero_id),),
        ).fetchall()
    finally:
        conn.close()
    primary_rows: list[dict[str, Any]] = []
    background_rows: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        base_weight = SOURCE_BASE_WEIGHTS.get(str(item.get("source_type", "")), 0.5)
        patch_weight = version_weight(current_version=current_version, candidate_version=item.get("game_version"))
        if patch_weight <= 0:
            if include_background:
                item["effective_weight"] = round(base_weight * 0.2, 4)
                background_rows.append(item)
            continue
        item["effective_weight"] = round(base_weight * patch_weight, 4)
        primary_rows.append(item)
    primary_rows.sort(key=lambda row: (float(row.get("effective_weight") or 0.0), row.get("fetched_at", "")), reverse=True)
    background_rows.sort(key=lambda row: (float(row.get("effective_weight") or 0.0), row.get("fetched_at", "")), reverse=True)
    combined = primary_rows[:limit]
    if include_background and len(combined) < limit:
        combined.extend(background_rows[: max(0, limit - len(combined))])
    return combined


def latest_fetched_at(hero_id: int, *, topic_type: str | None = None) -> str | None:
    ensure_guide_source_tables()
    conn = _connect()
    try:
        if topic_type:
            row = conn.execute(
                f"SELECT fetched_at FROM {GUIDE_SOURCES_TABLE} WHERE hero_id = ? AND topic_type = ? ORDER BY fetched_at DESC, id DESC LIMIT 1",
                (int(hero_id), topic_type),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT fetched_at FROM {GUIDE_SOURCES_TABLE} WHERE hero_id = ? ORDER BY fetched_at DESC, id DESC LIMIT 1",
                (int(hero_id),),
            ).fetchone()
        return str(row["fetched_at"]) if row else None
    finally:
        conn.close()
