from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nonebot import get_driver, logger

from .common import env, load_env_file

CONTEXT_WINDOW_SECONDS = 2 * 60 * 60
MAX_CONTEXT_ITEMS = 100
BOT_REPLY_KINDS = {"group_chat", "web_answerable"}

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
REDIS_URL = env(ENV_VALUES, "QQ_GROUP_CHAT_REDIS_URL", "redis://127.0.0.1:6379/0")
REDIS_KEY_PREFIX = env(ENV_VALUES, "QQ_GROUP_CHAT_REDIS_KEY_PREFIX", "nonebot2:group_chat")


@dataclass(slots=True)
class ChatRecord:
    timestamp: float
    sender_type: str
    sender_name: str
    text: str
    kind: str


@dataclass(slots=True)
class GroupChatState:
    messages: list[ChatRecord] = field(default_factory=list)
    last_activity_at: float | None = None
    last_idle_joke_at: float | None = None


_REDIS_CLIENT: Any | None = None


def _messages_key(group_id: int) -> str:
    return f"{REDIS_KEY_PREFIX}:group:{group_id}:messages"


def _status_key(group_id: int) -> str:
    return f"{REDIS_KEY_PREFIX}:group:{group_id}:status"


def _tracked_groups_key() -> str:
    return f"{REDIS_KEY_PREFIX}:groups"


def _build_redis_client() -> Any:
    try:
        from redis import Redis
    except ImportError as exc:
        raise RuntimeError("Python package 'redis' is required for group chat persistence.") from exc
    client = Redis.from_url(REDIS_URL, decode_responses=True)
    client.ping()
    return client


def _get_redis_client() -> Any:
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = _build_redis_client()
    return _REDIS_CLIENT


def configure_group_chat_store_for_tests(*, redis_client: Any | None = None) -> None:
    global _REDIS_CLIENT
    _REDIS_CLIENT = redis_client


def _prune_messages(group_id: int, *, now: float | None = None, window_seconds: int = CONTEXT_WINDOW_SECONDS) -> None:
    current_time = now if now is not None else time.time()
    cutoff = current_time - window_seconds
    _get_redis_client().zremrangebyscore(_messages_key(group_id), "-inf", f"({cutoff}")


def _decode_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _append_message(
    group_id: int,
    *,
    sender_type: str,
    sender_name: str,
    text: str,
    kind: str,
    timestamp: float | None = None,
    update_activity: bool = True,
) -> None:
    current_time = timestamp if timestamp is not None else time.time()
    _prune_messages(group_id, now=current_time)
    client = _get_redis_client()
    client.sadd(_tracked_groups_key(), str(group_id))
    normalized_text = str(text).strip()
    if normalized_text:
        record = ChatRecord(
            timestamp=current_time,
            sender_type=sender_type,
            sender_name=sender_name.strip() or ("机器人" if sender_type == "bot" else "群友"),
            text=normalized_text,
            kind=kind,
        )
        member = json.dumps(
            {
                "id": f"{int(current_time * 1_000_000)}-{uuid.uuid4().hex}",
                "timestamp": record.timestamp,
                "sender_type": record.sender_type,
                "sender_name": record.sender_name,
                "text": record.text,
                "kind": record.kind,
            },
            ensure_ascii=False,
        )
        client.zadd(_messages_key(group_id), {member: current_time})
    if update_activity:
        client.hset(_status_key(group_id), "last_activity_at", str(current_time))


def record_user_group_event(
    group_id: int,
    sender_name: str,
    text: str,
    *,
    timestamp: float | None = None,
    kind: str = "user_message",
) -> None:
    _append_message(
        group_id,
        sender_type="user",
        sender_name=sender_name,
        text=text,
        kind=kind,
        timestamp=timestamp,
        update_activity=True,
    )


def record_bot_group_reply(group_id: int, route: str, text: str, *, timestamp: float | None = None) -> None:
    if route not in BOT_REPLY_KINDS:
        return
    _append_message(
        group_id,
        sender_type="bot",
        sender_name="机器人",
        text=text,
        kind=route,
        timestamp=timestamp,
        update_activity=True,
    )


def record_idle_joke(group_id: int, text: str, *, timestamp: float | None = None) -> None:
    current_time = timestamp if timestamp is not None else time.time()
    _append_message(
        group_id,
        sender_type="bot",
        sender_name="机器人",
        text=text,
        kind="idle_joke",
        timestamp=current_time,
        update_activity=True,
    )
    _get_redis_client().hset(_status_key(group_id), "last_idle_joke_at", str(current_time))


def get_recent_group_context(
    group_id: int,
    *,
    now: float | None = None,
    max_items: int = MAX_CONTEXT_ITEMS,
    window_seconds: int = CONTEXT_WINDOW_SECONDS,
) -> list[dict[str, str | float]]:
    _prune_messages(group_id, now=now, window_seconds=window_seconds)
    raw_items = _get_redis_client().zrevrange(_messages_key(group_id), 0, max(1, max_items) - 1)
    context: list[dict[str, str | float]] = []
    for raw_item in reversed(raw_items):
        try:
            parsed = json.loads(raw_item)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        context.append(
            {
                "timestamp": float(parsed.get("timestamp", 0.0)),
                "sender_type": str(parsed.get("sender_type", "")),
                "sender_name": str(parsed.get("sender_name", "")),
                "text": str(parsed.get("text", "")),
                "kind": str(parsed.get("kind", "")),
            }
        )
    return context


def get_last_activity_at(group_id: int) -> float | None:
    return _decode_float(_get_redis_client().hget(_status_key(group_id), "last_activity_at"))


def get_last_idle_joke_at(group_id: int) -> float | None:
    return _decode_float(_get_redis_client().hget(_status_key(group_id), "last_idle_joke_at"))


def list_tracked_group_ids() -> list[int]:
    group_ids: list[int] = []
    for raw_group_id in _get_redis_client().smembers(_tracked_groups_key()):
        try:
            group_ids.append(int(raw_group_id))
        except (TypeError, ValueError):
            continue
    return sorted(group_ids)


def reset_group_chat_store() -> None:
    client = _get_redis_client()
    keys = list(client.scan_iter(match=f"{REDIS_KEY_PREFIX}:*"))
    if keys:
        client.delete(*keys)


async def _startup_group_chat_store() -> None:
    _get_redis_client()
    logger.info("Group chat store connected to Redis: {}", REDIS_URL)


def _register_startup_hook() -> None:
    try:
        driver = get_driver()
    except ValueError:
        return
    driver.on_startup(_startup_group_chat_store)


_register_startup_hook()
