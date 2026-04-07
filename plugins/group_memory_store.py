from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Any

from .common import env, load_env_file

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "group_memory"
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
_DEFAULT_DB_PATH = Path(
    env(ENV_VALUES, "QQ_GROUP_MEMORY_DB_PATH", str(DATA_DIR / "group_memory.sqlite3"))
).resolve()
_DB_PATH_OVERRIDE: Path | None = None

MEMORY_ITEMS_TABLE = "group_memory_items"
MEMORY_ITEMS_FTS_TABLE = "group_memory_items_fts"
MEMORY_TYPES: tuple[str, ...] = ("bot_preference", "group_lexicon", "durable_context")
PRIORITY_SCORES = {"high": 30, "medium": 15, "low": 0}
TYPE_SCORES = {"bot_preference": 300, "group_lexicon": 250, "durable_context": 150}


def configure_group_memory_store_for_tests(*, db_path: str | Path | None = None) -> None:
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = Path(db_path).resolve() if db_path else None


def group_memory_db_path() -> Path:
    return _DB_PATH_OVERRIDE or _DEFAULT_DB_PATH


def _ensure_db_dir() -> None:
    group_memory_db_path().parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_db_dir()
    conn = sqlite3.connect(group_memory_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _schema_is_current(conn: sqlite3.Connection) -> bool:
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if MEMORY_ITEMS_TABLE not in tables or MEMORY_ITEMS_FTS_TABLE not in tables:
        return False
    columns = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({MEMORY_ITEMS_TABLE})").fetchall()
    }
    return {
        "id",
        "group_id",
        "type",
        "subject",
        "canonical",
        "aliases_json",
        "aliases_text",
        "content",
        "priority",
        "enabled",
        "updated_at",
    }.issubset(columns)


def ensure_tables() -> None:
    conn = _connect()
    try:
        if not _schema_is_current(conn):
            conn.executescript(
                f"""
                DROP TABLE IF EXISTS {MEMORY_ITEMS_TABLE};
                DROP TABLE IF EXISTS {MEMORY_ITEMS_FTS_TABLE};
                """
            )
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {MEMORY_ITEMS_TABLE} (
                id TEXT PRIMARY KEY,
                group_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                subject TEXT NOT NULL,
                canonical TEXT NOT NULL,
                aliases_json TEXT NOT NULL,
                aliases_text TEXT NOT NULL,
                content TEXT NOT NULL,
                priority TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_group_memory_items_group
            ON {MEMORY_ITEMS_TABLE}(group_id, enabled, updated_at DESC);

            CREATE VIRTUAL TABLE IF NOT EXISTS {MEMORY_ITEMS_FTS_TABLE}
            USING fts5(
                item_id UNINDEXED,
                group_id UNINDEXED,
                subject,
                canonical,
                aliases_text,
                content,
                tokenize = 'unicode61'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def reset_group_memory_store() -> None:
    path = group_memory_db_path()
    if path.exists():
        path.unlink()


def _normalize_query_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text)).strip().lower()
    return " ".join(normalized.split())


def _load_aliases(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = " ".join(str(item).strip().split())
        if not text:
            continue
        key = _normalize_query_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def sync_group_memory_items(group_id: int, items: list[dict[str, Any]]) -> None:
    ensure_tables()
    conn = _connect()
    try:
        conn.execute(f"DELETE FROM {MEMORY_ITEMS_TABLE} WHERE group_id = ?", (int(group_id),))
        conn.execute(f"DELETE FROM {MEMORY_ITEMS_FTS_TABLE} WHERE group_id = ?", (int(group_id),))
        rows: list[tuple[Any, ...]] = []
        fts_rows: list[tuple[Any, ...]] = []
        for item in items:
            aliases = _load_aliases(item.get("aliases", []))
            aliases_text = " ".join(aliases)
            row = (
                str(item.get("id", "")).strip(),
                int(group_id),
                str(item.get("type", "")).strip(),
                str(item.get("subject", "")).strip(),
                str(item.get("canonical", "")).strip(),
                json.dumps(aliases, ensure_ascii=False),
                aliases_text,
                str(item.get("content", "")).strip(),
                str(item.get("priority", "medium")).strip(),
                1 if bool(item.get("enabled", True)) else 0,
                int(item.get("updated_at", int(time.time()))),
            )
            rows.append(row)
            fts_rows.append((row[0], row[1], row[3], row[4], row[6], row[7]))
        if rows:
            conn.executemany(
                f"""
                INSERT INTO {MEMORY_ITEMS_TABLE}
                (id, group_id, type, subject, canonical, aliases_json, aliases_text, content, priority, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.executemany(
                f"""
                INSERT INTO {MEMORY_ITEMS_FTS_TABLE}
                (item_id, group_id, subject, canonical, aliases_text, content)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                fts_rows,
            )
        conn.commit()
    finally:
        conn.close()


def _fetch_group_items(conn: sqlite3.Connection, group_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT *
        FROM {MEMORY_ITEMS_TABLE}
        WHERE group_id = ? AND enabled = 1
        ORDER BY updated_at DESC, id ASC
        """,
        (int(group_id),),
    ).fetchall()


def _score_row(row: sqlite3.Row, query: str) -> tuple[int, list[str]]:
    query_norm = _normalize_query_text(query)
    if not query_norm:
        return 0, []
    score = 0
    reasons: list[str] = []
    subject = _normalize_query_text(row["subject"])
    canonical = _normalize_query_text(row["canonical"])
    content = _normalize_query_text(row["content"])
    aliases = [_normalize_query_text(alias) for alias in _load_aliases(json.loads(row["aliases_json"]))]
    terms = [term for term in [subject, canonical, *aliases] if term]
    for term in terms:
        if term == query_norm:
            score += 1000
            reasons.append("exact")
        elif len(term) > 1 and term in query_norm:
            score += 700
            reasons.append("substring")
    if query_norm and query_norm in content:
        score += 250
        reasons.append("content")
    return score, reasons


def _fts_hits(conn: sqlite3.Connection, group_id: int, query: str) -> dict[str, float]:
    query = query.strip()
    if not query:
        return {}
    try:
        rows = conn.execute(
            f"""
            SELECT item_id, bm25({MEMORY_ITEMS_FTS_TABLE}) AS rank
            FROM {MEMORY_ITEMS_FTS_TABLE}
            WHERE {MEMORY_ITEMS_FTS_TABLE} MATCH ? AND group_id = ?
            """,
            (query, int(group_id)),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    hits: dict[str, float] = {}
    for row in rows:
        item_id = str(row["item_id"])
        rank = float(row["rank"])
        hits[item_id] = max(hits.get(item_id, float("-inf")), -rank)
    return hits


def retrieve_group_memories(group_id: int, query: str, *, limit: int = 3) -> list[dict[str, Any]]:
    ensure_tables()
    conn = _connect()
    try:
        rows = _fetch_group_items(conn, group_id)
        if not rows:
            return []
        fts_scores = _fts_hits(conn, group_id, query)
        scored: list[tuple[float, int, sqlite3.Row, list[str]]] = []
        for row in rows:
            base_score, reasons = _score_row(row, query)
            if row["id"] in fts_scores:
                base_score += 100 + min(100, int(fts_scores[row["id"]] * 20))
                reasons.append("fts")
            if not reasons or base_score <= 0:
                continue
            base_score += TYPE_SCORES.get(str(row["type"]), 0) + PRIORITY_SCORES.get(str(row["priority"]), 0)
            scored.append((float(base_score), int(row["updated_at"]), row, reasons))
        scored.sort(key=lambda item: (-item[0], -item[1], str(item[2]["id"])))
        results: list[dict[str, Any]] = []
        for score, _, row, reasons in scored[: max(1, int(limit))]:
            results.append(
                {
                    "id": str(row["id"]),
                    "type": str(row["type"]),
                    "subject": str(row["subject"]),
                    "canonical": str(row["canonical"]),
                    "aliases": _load_aliases(json.loads(row["aliases_json"])),
                    "content": str(row["content"]),
                    "priority": str(row["priority"]),
                    "updated_at": int(row["updated_at"]),
                    "score": score,
                    "reasons": sorted(set(reasons)),
                }
            )
        return results
    finally:
        conn.close()
