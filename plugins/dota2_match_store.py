from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nonebot import logger

from .common import env, load_env_file

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "dota2_monitor"
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
_DEFAULT_DB_PATH = Path(env(ENV_VALUES, "DOTA2_MATCH_DB_PATH", str(DATA_DIR / "matches.sqlite3"))).resolve()
_DB_PATH_OVERRIDE: Path | None = None

RAW_MATCHES_TABLE = "raw_matches"
PLAYER_ANALYSIS_TABLE = "player_match_analysis"

ANALYSIS_COLUMNS: tuple[str, ...] = (
    "steam_id",
    "match_id",
    "match_seq_num",
    "start_time",
    "duration",
    "player_slot",
    "hero_id",
    "radiant_win",
    "won",
    "kills",
    "deaths",
    "assists",
    "last_hits",
    "denies",
    "gold_per_min",
    "xp_per_min",
    "level",
    "net_worth",
    "hero_damage",
    "tower_damage",
    "hero_healing",
    "gold",
    "gold_spent",
    "leaver_status",
    "item_0",
    "item_1",
    "item_2",
    "item_3",
    "item_4",
    "item_5",
    "backpack_0",
    "backpack_1",
    "backpack_2",
    "item_neutral",
    "item_neutral2",
    "items_json",
    "ability_upgrades_json",
    "player_raw_json",
    "created_at",
)


def configure_dota_match_store_for_tests(*, db_path: str | Path | None = None) -> None:
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = Path(db_path).resolve() if db_path else None


def dota_match_db_path() -> Path:
    return _DB_PATH_OVERRIDE or _DEFAULT_DB_PATH


def _ensure_db_dir() -> None:
    dota_match_db_path().parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_db_dir()
    conn = sqlite3.connect(dota_match_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bool_to_int(value: Any) -> int:
    return 1 if bool(value) else 0


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _drop_legacy_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS matches;
        DROP TABLE IF EXISTS match_players;
        DROP TABLE IF EXISTS raw_matches;
        DROP TABLE IF EXISTS player_match_analysis;
        """
    )


def _schema_is_current(conn: sqlite3.Connection) -> bool:
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if RAW_MATCHES_TABLE not in tables or PLAYER_ANALYSIS_TABLE not in tables:
        return False
    raw_columns = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({RAW_MATCHES_TABLE})").fetchall()
    }
    analysis_columns = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({PLAYER_ANALYSIS_TABLE})").fetchall()
    }
    return {
        "match_id",
        "match_seq_num",
        "start_time",
        "duration",
        "raw_match_json",
        "updated_at",
    }.issubset(raw_columns) and {
        "id",
        "steam_id",
        "match_id",
        "hero_id",
        "items_json",
        "ability_upgrades_json",
        "player_raw_json",
    }.issubset(analysis_columns)


def ensure_tables() -> None:
    conn = _connect()
    try:
        if not _schema_is_current(conn):
            _drop_legacy_tables(conn)
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {RAW_MATCHES_TABLE} (
                match_id INTEGER PRIMARY KEY,
                match_seq_num INTEGER,
                start_time INTEGER,
                duration INTEGER,
                raw_match_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS {PLAYER_ANALYSIS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                steam_id TEXT NOT NULL,
                match_id INTEGER NOT NULL,
                match_seq_num INTEGER,
                start_time INTEGER,
                duration INTEGER,
                player_slot INTEGER,
                hero_id INTEGER,
                radiant_win INTEGER,
                won INTEGER,
                kills INTEGER,
                deaths INTEGER,
                assists INTEGER,
                last_hits INTEGER,
                denies INTEGER,
                gold_per_min INTEGER,
                xp_per_min INTEGER,
                level INTEGER,
                net_worth INTEGER,
                hero_damage INTEGER,
                tower_damage INTEGER,
                hero_healing INTEGER,
                gold INTEGER,
                gold_spent INTEGER,
                leaver_status INTEGER,
                item_0 INTEGER,
                item_1 INTEGER,
                item_2 INTEGER,
                item_3 INTEGER,
                item_4 INTEGER,
                item_5 INTEGER,
                backpack_0 INTEGER,
                backpack_1 INTEGER,
                backpack_2 INTEGER,
                item_neutral INTEGER,
                item_neutral2 INTEGER,
                items_json TEXT,
                ability_upgrades_json TEXT,
                player_raw_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_player_match_analysis_match_steam
            ON {PLAYER_ANALYSIS_TABLE}(match_id, steam_id);

            CREATE INDEX IF NOT EXISTS idx_player_match_analysis_steam_start_time
            ON {PLAYER_ANALYSIS_TABLE}(steam_id, start_time DESC);

            CREATE INDEX IF NOT EXISTS idx_player_match_analysis_match_id
            ON {PLAYER_ANALYSIS_TABLE}(match_id);

            CREATE INDEX IF NOT EXISTS idx_player_match_analysis_hero_id
            ON {PLAYER_ANALYSIS_TABLE}(hero_id);
            """
        )
        conn.commit()
    finally:
        conn.close()


def reset_dota_match_store() -> None:
    path = dota_match_db_path()
    if path.exists():
        path.unlink()


def has_raw_match(match_id: int) -> bool:
    ensure_tables()
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT 1 FROM {RAW_MATCHES_TABLE} WHERE match_id = ? LIMIT 1",
            (int(match_id),),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def has_player_analysis(match_id: int, steam_id: str) -> bool:
    ensure_tables()
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT 1 FROM {PLAYER_ANALYSIS_TABLE} WHERE match_id = ? AND steam_id = ? LIMIT 1",
            (int(match_id), str(steam_id)),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def save_raw_match(match: dict[str, Any]) -> bool:
    match_id = _int_or_none(match.get("match_id"))
    if match_id is None:
        logger.warning("Dota2 match store stage=save_raw_match_invalid reason=missing_match_id")
        return False
    ensure_tables()
    conn = _connect()
    try:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {RAW_MATCHES_TABLE}
            (match_id, match_seq_num, start_time, duration, raw_match_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                match_id,
                _int_or_none(match.get("match_seq_num")),
                _int_or_none(match.get("start_time")),
                _int_or_none(match.get("duration")),
                json.dumps(match, ensure_ascii=False),
                _utc_now_iso(),
            ),
        )
        inserted = conn.total_changes > 0
        conn.commit()
        return inserted
    except Exception:
        logger.exception(
            "Dota2 match store stage=save_raw_match_failed match_id={}",
            match_id,
        )
        raise
    finally:
        conn.close()


def _player_team_won(player_slot: int, radiant_win: bool) -> bool:
    return (player_slot < 128) == radiant_win


def _player_items_payload(player: dict[str, Any]) -> str:
    payload = {
        "main": {f"item_{index}": _int_or_none(player.get(f"item_{index}")) for index in range(6)},
        "backpack": {f"backpack_{index}": _int_or_none(player.get(f"backpack_{index}")) for index in range(3)},
        "neutral": {
            "item_neutral": _int_or_none(player.get("item_neutral")),
            "item_neutral2": _int_or_none(player.get("item_neutral2")),
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _ability_upgrades_payload(player: dict[str, Any]) -> str:
    abilities = player.get("ability_upgrades")
    if not isinstance(abilities, list):
        abilities = []
    normalized = [item for item in abilities if isinstance(item, dict)]
    return json.dumps(normalized, ensure_ascii=False)


def _existing_analysis_steam_ids(conn: sqlite3.Connection, match_id: int, target_steam_ids: set[str]) -> set[str]:
    if not target_steam_ids:
        return set()
    placeholders = ", ".join("?" for _ in target_steam_ids)
    rows = conn.execute(
        f"""
        SELECT steam_id
        FROM {PLAYER_ANALYSIS_TABLE}
        WHERE match_id = ? AND steam_id IN ({placeholders})
        """,
        [match_id, *sorted(target_steam_ids)],
    ).fetchall()
    return {str(row["steam_id"]) for row in rows}


def _collect_player_analysis_rows(
    match: dict[str, Any],
    *,
    target_steam_ids: set[str],
    existing_steam_ids: set[str],
    log_prefix: str,
) -> tuple[list[dict[str, Any]], int, int, int]:
    match_id = _int_or_none(match.get("match_id"))
    if match_id is None or not target_steam_ids:
        logger.warning(
            "Dota2 match store stage={} match_id={} target_steam_ids={} reason={}",
            log_prefix,
            match_id,
            sorted(target_steam_ids),
            "missing_match_id" if match_id is None else "empty_target_steam_ids",
        )
        return [], 0, 0, 0

    players = match.get("players")
    if not isinstance(players, list):
        logger.warning(
            "Dota2 match store stage={} match_id={} reason=players_not_list",
            log_prefix,
            match_id,
        )
        return [], 0, 0, 0

    radiant_win = bool(match.get("radiant_win"))
    now_text = _utc_now_iso()
    rows: list[dict[str, Any]] = []
    skipped_existing = 0
    failed_players = 0
    target_players = 0

    for player in players:
        if not isinstance(player, dict):
            failed_players += 1
            logger.warning(
                "Dota2 match store stage={} match_id={} reason=player_not_object",
                log_prefix,
                match_id,
            )
            continue

        steam_id = str(player.get("account_id", "")).strip()
        if not steam_id or steam_id not in target_steam_ids:
            continue
        target_players += 1

        if steam_id in existing_steam_ids:
            skipped_existing += 1
            logger.info(
                "Dota2 match store stage={} match_id={} steam_id={} reason=player_analysis_exists",
                f"{log_prefix}_skip_existing",
                match_id,
                steam_id,
            )
            continue

        player_slot = _int_or_none(player.get("player_slot"))
        if player_slot is None:
            failed_players += 1
            logger.warning(
                "Dota2 match store stage={} match_id={} steam_id={} reason=missing_player_slot",
                log_prefix,
                match_id,
                steam_id,
            )
            continue

        rows.append(
            {
                "steam_id": steam_id,
                "match_id": match_id,
                "match_seq_num": _int_or_none(match.get("match_seq_num")),
                "start_time": _int_or_none(match.get("start_time")),
                "duration": _int_or_none(match.get("duration")),
                "player_slot": player_slot,
                "hero_id": _int_or_none(player.get("hero_id")),
                "radiant_win": _bool_to_int(match.get("radiant_win")),
                "won": _bool_to_int(_player_team_won(player_slot, radiant_win)),
                "kills": _int_or_none(player.get("kills")),
                "deaths": _int_or_none(player.get("deaths")),
                "assists": _int_or_none(player.get("assists")),
                "last_hits": _int_or_none(player.get("last_hits")),
                "denies": _int_or_none(player.get("denies")),
                "gold_per_min": _int_or_none(player.get("gold_per_min")),
                "xp_per_min": _int_or_none(player.get("xp_per_min")),
                "level": _int_or_none(player.get("level")),
                "net_worth": _int_or_none(player.get("net_worth")),
                "hero_damage": _int_or_none(player.get("hero_damage")),
                "tower_damage": _int_or_none(player.get("tower_damage")),
                "hero_healing": _int_or_none(player.get("hero_healing")),
                "gold": _int_or_none(player.get("gold")),
                "gold_spent": _int_or_none(player.get("gold_spent")),
                "leaver_status": _int_or_none(player.get("leaver_status")),
                "item_0": _int_or_none(player.get("item_0")),
                "item_1": _int_or_none(player.get("item_1")),
                "item_2": _int_or_none(player.get("item_2")),
                "item_3": _int_or_none(player.get("item_3")),
                "item_4": _int_or_none(player.get("item_4")),
                "item_5": _int_or_none(player.get("item_5")),
                "backpack_0": _int_or_none(player.get("backpack_0")),
                "backpack_1": _int_or_none(player.get("backpack_1")),
                "backpack_2": _int_or_none(player.get("backpack_2")),
                "item_neutral": _int_or_none(player.get("item_neutral")),
                "item_neutral2": _int_or_none(player.get("item_neutral2")),
                "items_json": _player_items_payload(player),
                "ability_upgrades_json": _ability_upgrades_payload(player),
                "player_raw_json": json.dumps(player, ensure_ascii=False),
                "created_at": now_text,
            }
        )

    return rows, skipped_existing, failed_players, target_players


def save_player_analysis(match: dict[str, Any], target_steam_ids: set[str]) -> int:
    match_id = _int_or_none(match.get("match_id"))
    ensure_tables()
    conn = _connect()
    try:
        existing_steam_ids = _existing_analysis_steam_ids(conn, match_id, target_steam_ids) if match_id is not None else set()
        rows, _, _, _ = _collect_player_analysis_rows(
            match,
            target_steam_ids=target_steam_ids,
            existing_steam_ids=existing_steam_ids,
            log_prefix="save_player_analysis",
        )
        for row in rows:
            conn.execute(
                f"""
                INSERT OR IGNORE INTO {PLAYER_ANALYSIS_TABLE}
                ({", ".join(ANALYSIS_COLUMNS)})
                VALUES ({", ".join("?" for _ in ANALYSIS_COLUMNS)})
                """,
                [row[column] for column in ANALYSIS_COLUMNS],
            )
        conn.commit()
        return len(rows)
    except Exception:
        logger.exception(
            "Dota2 match store stage=save_player_analysis_failed match_id={} target_steam_ids={}",
            match_id,
            sorted(target_steam_ids),
        )
        raise
    finally:
        conn.close()


def save_raw_match_and_analysis(match: dict[str, Any], *, target_steam_ids: set[str]) -> tuple[bool, int]:
    raw_inserted = save_raw_match(match)
    analysis_inserted = save_player_analysis(match, target_steam_ids)
    return raw_inserted, analysis_inserted


def rebuild_player_match_analysis_from_raw_matches(*, target_steam_ids: set[str]) -> dict[str, int]:
    ensure_tables()
    summaries = {
        "scanned_matches": 0,
        "inserted_rows": 0,
        "skipped_existing_rows": 0,
        "failed_matches": 0,
        "failed_players": 0,
    }
    if not target_steam_ids:
        logger.warning("Dota2 match store stage=rebuild_analysis_skipped reason=empty_target_steam_ids")
        return summaries

    conn = _connect()
    try:
        raw_rows = conn.execute(
            f"""
            SELECT match_id, raw_match_json
            FROM {RAW_MATCHES_TABLE}
            ORDER BY start_time DESC, match_id DESC
            """
        ).fetchall()

        logger.info(
            "Dota2 match store stage=rebuild_analysis_start raw_matches={} target_steam_ids={}",
            len(raw_rows),
            sorted(target_steam_ids),
        )

        for raw_row in raw_rows:
            match_id = int(raw_row["match_id"])
            summaries["scanned_matches"] += 1
            try:
                match = json.loads(raw_row["raw_match_json"])
            except Exception:
                summaries["failed_matches"] += 1
                logger.exception(
                    "Dota2 match store stage=rebuild_analysis_invalid_raw match_id={} reason=invalid_raw_match_json",
                    match_id,
                )
                continue
            if not isinstance(match, dict):
                summaries["failed_matches"] += 1
                logger.warning(
                    "Dota2 match store stage=rebuild_analysis_invalid_raw match_id={} reason=raw_match_not_object",
                    match_id,
                )
                continue

            existing_steam_ids = _existing_analysis_steam_ids(conn, match_id, target_steam_ids)
            rows, skipped_existing, failed_players, target_players = _collect_player_analysis_rows(
                match,
                target_steam_ids=target_steam_ids,
                existing_steam_ids=existing_steam_ids,
                log_prefix="rebuild_analysis",
            )
            summaries["skipped_existing_rows"] += skipped_existing
            summaries["failed_players"] += failed_players

            if target_players == 0:
                logger.info(
                    "Dota2 match store stage=rebuild_analysis_skip_match match_id={} reason=no_target_players",
                    match_id,
                )
                continue

            try:
                for row in rows:
                    conn.execute(
                        f"""
                        INSERT OR IGNORE INTO {PLAYER_ANALYSIS_TABLE}
                        ({", ".join(ANALYSIS_COLUMNS)})
                        VALUES ({", ".join("?" for _ in ANALYSIS_COLUMNS)})
                        """,
                        [row[column] for column in ANALYSIS_COLUMNS],
                    )
                summaries["inserted_rows"] += len(rows)
            except Exception:
                summaries["failed_matches"] += 1
                logger.exception(
                    "Dota2 match store stage=rebuild_analysis_save_failed match_id={}",
                    match_id,
                )

        conn.commit()
        logger.info(
            "Dota2 match store stage=rebuild_analysis_done scanned_matches={} inserted_rows={} skipped_existing_rows={} failed_matches={} failed_players={}",
            summaries["scanned_matches"],
            summaries["inserted_rows"],
            summaries["skipped_existing_rows"],
            summaries["failed_matches"],
            summaries["failed_players"],
        )
        return summaries
    finally:
        conn.close()


def get_recent_account_matches(account_id: str, limit: int = 20) -> list[dict[str, Any]]:
    ensure_tables()
    capped_limit = max(1, min(100, int(limit)))
    conn = _connect()
    try:
        rows = conn.execute(
            f"""
            SELECT
                steam_id,
                match_id,
                match_seq_num,
                start_time,
                duration,
                player_slot,
                hero_id,
                radiant_win,
                won,
                kills,
                deaths,
                assists,
                last_hits,
                denies,
                gold_per_min,
                xp_per_min,
                level,
                net_worth,
                hero_damage,
                tower_damage,
                hero_healing,
                gold,
                gold_spent,
                leaver_status,
                item_0,
                item_1,
                item_2,
                item_3,
                item_4,
                item_5,
                backpack_0,
                backpack_1,
                backpack_2,
                item_neutral,
                item_neutral2,
                items_json,
                ability_upgrades_json,
                player_raw_json,
                created_at
            FROM {PLAYER_ANALYSIS_TABLE}
            WHERE steam_id = ?
            ORDER BY start_time DESC, id DESC
            LIMIT ?
            """,
            (str(account_id), capped_limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_recent_account_analysis(account_id: str, limit: int = 50) -> dict[str, Any]:
    rows = get_recent_account_matches(account_id, limit=limit)
    if not rows:
        return {
            "steam_id": str(account_id),
            "sample_size": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "most_played_hero_id": None,
            "most_played_hero_count": 0,
            "highest_kills_match_id": None,
            "highest_kills": None,
            "highest_kills_hero_id": None,
            "highest_deaths_match_id": None,
            "highest_deaths": None,
            "highest_deaths_hero_id": None,
        }

    win_count = sum(1 for row in rows if int(row.get("won") or 0) == 1)
    sample_size = len(rows)
    loss_count = sample_size - win_count
    hero_counts: dict[int, int] = {}
    for row in rows:
        hero_id = int(row.get("hero_id") or 0)
        if hero_id <= 0:
            continue
        hero_counts[hero_id] = hero_counts.get(hero_id, 0) + 1

    most_played_hero_id = None
    most_played_hero_count = 0
    for row in rows:
        hero_id = int(row.get("hero_id") or 0)
        if hero_id <= 0:
            continue
        count = hero_counts.get(hero_id, 0)
        if count > most_played_hero_count:
            most_played_hero_id = hero_id
            most_played_hero_count = count

    highest_kills_row = max(rows, key=lambda row: (int(row.get("kills") or 0), int(row.get("start_time") or 0)))
    highest_deaths_row = max(rows, key=lambda row: (int(row.get("deaths") or 0), int(row.get("start_time") or 0)))

    return {
        "steam_id": str(account_id),
        "sample_size": sample_size,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": (win_count / sample_size) if sample_size else 0.0,
        "most_played_hero_id": most_played_hero_id,
        "most_played_hero_count": most_played_hero_count,
        "highest_kills_match_id": int(highest_kills_row.get("match_id") or 0) or None,
        "highest_kills": int(highest_kills_row.get("kills") or 0),
        "highest_kills_hero_id": int(highest_kills_row.get("hero_id") or 0) or None,
        "highest_deaths_match_id": int(highest_deaths_row.get("match_id") or 0) or None,
        "highest_deaths": int(highest_deaths_row.get("deaths") or 0),
        "highest_deaths_hero_id": int(highest_deaths_row.get("hero_id") or 0) or None,
    }
