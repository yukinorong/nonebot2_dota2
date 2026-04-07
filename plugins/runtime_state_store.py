from __future__ import annotations

from pathlib import Path
from typing import Any

from nonebot import get_driver, logger

from .common import env, load_env_file

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
REDIS_URL = env(ENV_VALUES, "QQ_GROUP_CHAT_REDIS_URL", "redis://127.0.0.1:6379/0")
REDIS_KEY_PREFIX = env(ENV_VALUES, "QQ_GROUP_CHAT_REDIS_KEY_PREFIX", "nonebot2:group_chat")

_RUNTIME_FLAGS_KEY = f"{REDIS_KEY_PREFIX}:runtime:flags"
_REDIS_CLIENT: Any | None = None


def _build_redis_client() -> Any:
    try:
        from redis import Redis
    except ImportError as exc:
        raise RuntimeError("Python package 'redis' is required for runtime state persistence.") from exc
    client = Redis.from_url(REDIS_URL, decode_responses=True)
    client.ping()
    return client


def _get_redis_client() -> Any:
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        _REDIS_CLIENT = _build_redis_client()
    return _REDIS_CLIENT


def configure_runtime_state_store_for_tests(*, redis_client: Any | None = None) -> None:
    global _REDIS_CLIENT
    _REDIS_CLIENT = redis_client


def _bool_to_text(value: bool) -> str:
    return "true" if value else "false"


def _text_to_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "on", "yes", "enabled"}:
        return True
    if normalized in {"0", "false", "off", "no", "disabled"}:
        return False
    return default


def get_bool_flag(name: str, *, default: bool = False) -> bool:
    client = _get_redis_client()
    client.hsetnx(_RUNTIME_FLAGS_KEY, name, _bool_to_text(default))
    return _text_to_bool(client.hget(_RUNTIME_FLAGS_KEY, name), default=default)


def set_bool_flag(name: str, value: bool) -> bool:
    _get_redis_client().hset(_RUNTIME_FLAGS_KEY, name, _bool_to_text(bool(value)))
    return bool(value)


def reset_runtime_state_store() -> None:
    _get_redis_client().delete(_RUNTIME_FLAGS_KEY)


async def _startup_runtime_state_store() -> None:
    _get_redis_client()
    logger.info("Runtime state store connected to Redis: {}", REDIS_URL)


def _register_startup_hook() -> None:
    try:
        driver = get_driver()
    except ValueError:
        return
    driver.on_startup(_startup_runtime_state_store)


_register_startup_hook()
