from __future__ import annotations

import asyncio
import contextlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from nonebot import get_driver, logger
from nonebot.plugin import PluginMetadata

from .common import env, format_qq_chat_text, load_env_file, send_group_text
from .group_chat_store import get_last_activity_at, get_last_idle_joke_at, get_recent_group_context, record_idle_joke
from .idle_joke_store import has_idle_joke_hash, save_idle_joke_hash
from .llm_gateway import ask_main

__plugin_meta__ = PluginMetadata(
    name="idle_joke",
    description="Send a short cold joke to configured groups after long inactivity.",
    usage="后台每 1 分钟检查一次指定群，空闲超 1 小时后主动发一条冷笑话。",
)

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / '.env'
ENV_VALUES = load_env_file(ENV_FILE)
IDLE_JOKE_GROUP_IDS = [
    int(group_id.strip())
    for group_id in env(ENV_VALUES, 'QQ_IDLE_JOKE_GROUP_IDS', '1081502166').split(',')
    if group_id.strip().isdigit()
]
IDLE_JOKE_INTERVAL_SECONDS = 60
IDLE_JOKE_THRESHOLD_SECONDS = 60 * 60
IDLE_JOKE_START_HOUR = 13
IDLE_JOKE_END_HOUR = 22
IDLE_JOKE_VERSION = env(ENV_VALUES, "QQ_IDLE_JOKE_VERSION", "v2").strip().lower() or "v2"
IDLE_JOKE_V2_API_URL = env(
    ENV_VALUES,
    "QQ_IDLE_JOKE_V2_API_URL",
    "https://tools.mgtv100.com/external/v1/pear/duanZi",
).strip()
IDLE_JOKE_V2_TIMEOUT_SECONDS = max(3, int(env(ENV_VALUES, "QQ_IDLE_JOKE_V2_TIMEOUT_SECONDS", "10") or "10"))
IDLE_JOKE_V2_MAX_RETRIES = max(1, int(env(ENV_VALUES, "QQ_IDLE_JOKE_V2_MAX_RETRIES", "10") or "10"))

driver = get_driver()
_idle_joke_task: asyncio.Task[None] | None = None


def _is_joke_window(now: float) -> bool:
    local = time.localtime(now)
    return IDLE_JOKE_START_HOUR <= local.tm_hour < IDLE_JOKE_END_HOUR


def _recent_idle_jokes(group_id: int, *, limit: int = 5) -> list[str]:
    context = get_recent_group_context(group_id, max_items=100)
    jokes = [
        str(item.get("text", "")).strip()
        for item in context
        if str(item.get("kind", "")) == "idle_joke" and str(item.get("text", "")).strip()
    ]
    if not jokes:
        return []
    return jokes[-limit:]


async def _generate_idle_joke(*, group_id: int) -> str:
    if IDLE_JOKE_VERSION != "v1":
        return await _generate_idle_joke_v2(group_id=group_id)
    return await _generate_idle_joke_v1(group_id=group_id)


async def _generate_idle_joke_v1(*, group_id: int) -> str:
    recent_jokes = _recent_idle_jokes(group_id)
    prompt = (
        '讲一个冷笑话，要非常冷，听了之后尴尬得想笑。'
        '不要黄暴内容，不要重复老梗，控制在50字以内，只回复笑话正文，不加任何解释或表情。'
    )
    if recent_jokes:
        prompt += "\n最近已经发过这些冷笑话，请避开，不要复读：\n" + "\n".join(f"- {joke}" for joke in recent_jokes)
    return (
        await ask_main(
            prompt,
            channel=f'qq-g{group_id}-idle-joke-{int(time.time())}',
        )
    ).strip()


def _fetch_idle_joke_v2_sync() -> dict[str, Any]:
    req = urllib.request.Request(IDLE_JOKE_V2_API_URL, method="GET")
    with urllib.request.urlopen(req, timeout=IDLE_JOKE_V2_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}


async def _fetch_idle_joke_v2() -> dict[str, Any]:
    return await asyncio.to_thread(_fetch_idle_joke_v2_sync)


def _normalize_v2_joke_text(raw: str) -> str:
    return format_qq_chat_text(str(raw or "").replace("<br>", "\n")).strip()


async def _generate_idle_joke_v2(*, group_id: int) -> str:
    for attempt in range(1, IDLE_JOKE_V2_MAX_RETRIES + 1):
        logger.info(
            "Idle joke stage=idle_joke_v2_request group_id={} attempt={} url={}",
            group_id,
            attempt,
            IDLE_JOKE_V2_API_URL,
        )
        try:
            payload = await _fetch_idle_joke_v2()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            logger.warning(
                "Idle joke stage=idle_joke_v2_failed group_id={} attempt={} reason=http_{} detail={}",
                group_id,
                attempt,
                exc.code,
                detail,
            )
            continue
        except urllib.error.URLError as exc:
            logger.warning(
                "Idle joke stage=idle_joke_v2_failed group_id={} attempt={} reason=url_error detail={}",
                group_id,
                attempt,
                exc,
            )
            continue
        except Exception as exc:
            logger.warning(
                "Idle joke stage=idle_joke_v2_failed group_id={} attempt={} reason=request_exception detail={}",
                group_id,
                attempt,
                exc,
            )
            continue

        status = str(payload.get("status", "")).strip().lower()
        raw_joke = str(payload.get("data", "")).strip()
        if status != "success" or not raw_joke:
            logger.warning(
                "Idle joke stage=idle_joke_v2_invalid_payload group_id={} attempt={} reason=status_or_data_invalid payload={}",
                group_id,
                attempt,
                payload,
            )
            continue

        joke = _normalize_v2_joke_text(raw_joke)
        if not joke:
            logger.warning(
                "Idle joke stage=idle_joke_v2_invalid_payload group_id={} attempt={} reason=normalized_empty",
                group_id,
                attempt,
            )
            continue
        if has_idle_joke_hash(group_id, joke):
            logger.info(
                "Idle joke stage=idle_joke_v2_duplicate group_id={} attempt={} reason=hash_exists",
                group_id,
                attempt,
            )
            continue
        return joke
    return ""


async def _run_idle_joke_check_once() -> None:
    now = time.time()
    if not _is_joke_window(now):
        return
    for group_id in IDLE_JOKE_GROUP_IDS:
        last_activity_at = get_last_activity_at(group_id)
        if last_activity_at is None or now - last_activity_at <= IDLE_JOKE_THRESHOLD_SECONDS:
            continue
        last_idle_joke_at = get_last_idle_joke_at(group_id)
        if last_idle_joke_at is not None and last_idle_joke_at >= last_activity_at:
            continue
        joke = (await _generate_idle_joke(group_id=group_id)).strip()
        if not joke:
            continue
        await send_group_text(group_id, joke)
        record_idle_joke(group_id, joke, timestamp=now)
        save_idle_joke_hash(group_id, joke)
        logger.info('Idle joke stage=idle_joke_sent group_id={}', group_id)


async def _idle_joke_loop() -> None:
    while True:
        try:
            await _run_idle_joke_check_once()
        except Exception:
            logger.exception('Idle joke loop failed')
        await asyncio.sleep(IDLE_JOKE_INTERVAL_SECONDS)


@driver.on_startup
async def _startup_idle_joke() -> None:
    global _idle_joke_task
    if _idle_joke_task and not _idle_joke_task.done():
        return
    _idle_joke_task = asyncio.create_task(_idle_joke_loop(), name='qq-idle-joke-loop')
    logger.info(
        'Idle joke monitor started for version={} groups={}',
        IDLE_JOKE_VERSION,
        ','.join(str(group_id) for group_id in IDLE_JOKE_GROUP_IDS),
    )


@driver.on_shutdown
async def _shutdown_idle_joke() -> None:
    global _idle_joke_task
    if _idle_joke_task is None:
        return
    _idle_joke_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _idle_joke_task
    _idle_joke_task = None
