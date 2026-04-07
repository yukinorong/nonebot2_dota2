from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path

from nonebot import get_driver, logger
from nonebot.plugin import PluginMetadata

from .common import env, extract_json_object, load_env_file
from .group_chat_store import get_recent_group_context, list_tracked_group_ids
from .llm_gateway import ask_main
from .openclaw_group_memory import (
    bootstrap_group_memory,
    ensure_group_openclaw_setup,
    group_memory_agent_id,
    normalize_memory_items,
    read_memory_items,
    read_memory_markdown,
    write_memory_items,
    write_memory_markdown,
)

__plugin_meta__ = PluginMetadata(
    name="group_memory",
    description="Maintain per-group long-term memory items and OpenClaw MEMORY.md on a timer.",
    usage="后台每 2 小时整理每个群最近 2 小时聊天，更新 OpenClaw 群长期记忆。",
)
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
GROUP_MEMORY_INTERVAL_SECONDS = max(300, int(env(ENV_VALUES, "QQ_GROUP_MEMORY_INTERVAL_SECONDS", str(2 * 60 * 60)) or str(2 * 60 * 60)))
GROUP_MEMORY_MAX_CONTEXT_ITEMS = max(1, int(env(ENV_VALUES, "QQ_GROUP_MEMORY_MAX_CONTEXT_ITEMS", "100") or "100"))
GROUP_MEMORY_STARTUP_DELAY_SECONDS = max(
    0,
    int(env(ENV_VALUES, "QQ_GROUP_MEMORY_STARTUP_DELAY_SECONDS", "15") or "15"),
)
ALLOWED_GROUP_IDS = {
    int(group_id.strip())
    for group_id in env(ENV_VALUES, "QQ_ALLOWED_GROUP_IDS", "").split(",")
    if group_id.strip().isdigit()
}

driver = get_driver()
_group_memory_task: asyncio.Task[None] | None = None


def read_group_memory(group_id: int) -> str:
    bootstrap_group_memory(group_id)
    return read_memory_markdown(group_id)


def _build_recent_context_text(group_id: int) -> str:
    records = get_recent_group_context(group_id, max_items=GROUP_MEMORY_MAX_CONTEXT_ITEMS)
    if not records:
        return ""
    lines: list[str] = []
    for item in records:
        timestamp = float(item.get("timestamp", 0.0))
        sender_name = str(item.get("sender_name", "")).strip() or "群友"
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        time_text = time.strftime("%H:%M", time.localtime(timestamp))
        lines.append(f"{time_text} {sender_name}: {text}")
    return "\n".join(lines).strip()


def _parse_items_from_llm(raw: str, *, key: str) -> list[dict[str, Any]]:
    payload = extract_json_object(raw)
    return normalize_memory_items(payload.get(key, []))


async def _extract_candidate_items(group_id: int, recent_context_text: str) -> list[dict[str, Any]]:
    prompt = (
        "你在为一个 QQ 群提取结构化长期记忆候选项。\n"
        "目标不是总结群聊，而是筛出未来机器人回复真正需要长期记住的信息。\n"
        "只允许输出三类候选项：\n"
        "1. bot_preference: 群友对机器人的长期要求、偏好、限制、格式要求。\n"
        "2. group_lexicon: 群内稳定的称呼、外号、别名、固定指代。\n"
        "3. durable_context: 会影响机器人长期回复的稳定背景，但必须很少而且明确。\n"
        "不要记录群友在群里做了什么，不要记录一次性事件、临时计划、新闻、短期情绪、模糊猜测。\n"
        "尽可能简洁，明确、不明确的记忆宁可扔掉也不要保存。\n"
        "每条候选项只表达一个长期稳定事实。\n"
        "只输出 JSON 对象，不要解释，不要 Markdown，不要代码块。\n"
        'JSON 格式：{"candidates":[{"type":"bot_preference|group_lexicon|durable_context","subject":"robot|group|user:<name>|topic:<name>","canonical":"标准表达","aliases":["别名1","别名2"],"content":"一句短事实","priority":"high|medium|low"}]}\n\n'
        f"最近两小时群聊：\n{recent_context_text or '（暂无）'}"
    )
    raw = await ask_main(
        prompt,
        channel=f"qq-g{group_id}-memory-{int(time.time())}",
        agent_id=group_memory_agent_id(group_id),
    )
    return _parse_items_from_llm(raw, key="candidates")


async def _merge_memory_items(
    group_id: int,
    existing_items: list[dict[str, Any]],
    candidate_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prompt = (
        "你在维护一个 QQ 群机器人的结构化长期记忆项列表。\n"
        "请基于现有长期记忆项和新候选项，输出新的完整长期记忆项列表。\n"
        "原则：\n"
        "1. 只保留未来机器人回复真正需要的稳定信息。\n"
        "2. 去重，同一事实只保留一条，优先保留更明确、更短、更稳定的一条。\n"
        "3. 可以合并 aliases，但不要把不确定的别名写进去。\n"
        "4. 冲突时保留更新、更明确、更像长期规则的一条。\n"
        "5. 不明确的、短期的、一次性的内容直接丢掉。\n"
        "6. 最终列表要短。\n"
        "只输出 JSON 对象，不要解释。\n"
        'JSON 格式：{"items":[{"type":"bot_preference|group_lexicon|durable_context","subject":"robot|group|user:<name>|topic:<name>","canonical":"标准表达","aliases":["别名1","别名2"],"content":"一句短事实","priority":"high|medium|low"}]}\n\n'
        f"当前长期记忆项：\n{json.dumps(existing_items, ensure_ascii=False)}\n\n"
        f"新候选项：\n{json.dumps(candidate_items, ensure_ascii=False)}"
    )
    raw = await ask_main(
        prompt,
        channel=f"qq-g{group_id}-memory-merge-{int(time.time())}",
        agent_id=group_memory_agent_id(group_id),
    )
    merged_items = _parse_items_from_llm(raw, key="items")
    return merged_items or existing_items


async def build_group_memory(group_id: int) -> tuple[list[dict[str, Any]], str]:
    bootstrap_group_memory(group_id)
    recent_context_text = _build_recent_context_text(group_id)
    if not recent_context_text:
        existing_items = read_memory_items(group_id)
        return existing_items, read_memory_markdown(group_id)
    existing_items = read_memory_items(group_id)
    candidate_items = await _extract_candidate_items(group_id, recent_context_text)
    if not candidate_items:
        return existing_items, read_memory_markdown(group_id)
    merged_items = await _merge_memory_items(group_id, existing_items, candidate_items)
    rendered_memory = write_memory_markdown(group_id, merged_items)
    write_memory_items(group_id, merged_items)
    return merged_items, rendered_memory


async def _run_group_memory_once() -> None:
    tracked_group_ids = sorted(set(ALLOWED_GROUP_IDS) | set(list_tracked_group_ids()))
    if tracked_group_ids:
        ensure_group_openclaw_setup(tracked_group_ids)
    for group_id in tracked_group_ids:
        try:
            items, memory = await build_group_memory(group_id)
        except Exception:
            logger.exception("Group memory summarize failed for group {}", group_id)
            continue
        if not items or not memory:
            continue
        logger.info("Group memory refreshed via OpenClaw workspace for group {}", group_id)


async def _group_memory_loop() -> None:
    if GROUP_MEMORY_STARTUP_DELAY_SECONDS:
        await asyncio.sleep(GROUP_MEMORY_STARTUP_DELAY_SECONDS)
    while True:
        try:
            await _run_group_memory_once()
        except Exception:
            logger.exception("Group memory loop failed")
        await asyncio.sleep(GROUP_MEMORY_INTERVAL_SECONDS)


@driver.on_startup
async def _startup_group_memory() -> None:
    global _group_memory_task
    if _group_memory_task and not _group_memory_task.done():
        return
    ensure_group_openclaw_setup(sorted(ALLOWED_GROUP_IDS))
    _group_memory_task = asyncio.create_task(_group_memory_loop(), name="qq-group-memory-loop")
    logger.info("Group memory monitor started with interval={}s", GROUP_MEMORY_INTERVAL_SECONDS)


@driver.on_shutdown
async def _shutdown_group_memory() -> None:
    global _group_memory_task
    if _group_memory_task is None:
        return
    _group_memory_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _group_memory_task
    _group_memory_task = None
