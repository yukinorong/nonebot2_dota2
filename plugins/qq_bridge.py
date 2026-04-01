from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Awaitable, Callable

from nonebot import get_bots, on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me

from .common import (
    OpenClawClient,
    collapse_text,
    env,
    extract_json_object,
    load_env_file,
    send_group_text,
    send_private_text,
)
from .dota2_monitor import build_latest_match_push_text, list_watched_accounts, resolve_watched_account
from .dota2_watch_config import add_watch_account, list_group_accounts

__plugin_meta__ = PluginMetadata(
    name="qq_bridge",
    description="Send QQ private/group messages and route @mentions to categorized OpenClaw handlers.",
    usage=(
        "/sendqq <qq号> <消息内容>\n"
        "/sendgroup <群号> <消息内容>\n"
        "群里 @机器人 会先分类，再按对应策略回复。\n"
        "@机器人 /add <昵称> <steamID>\n"
        "@机器人 /list\n"
        "@机器人 /todo\n"
        "@机器人 /help"
    ),
)

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)

OPENCLAW_URL = env(ENV_VALUES, "OPENCLAW_URL", "http://127.0.0.1:18789/v1/responses")
OPENCLAW_TOKEN = env(ENV_VALUES, "OPENCLAW_TOKEN", "80b386bc7275d3c003aa3bfb5bb27115d68d6caf82f5a7a0")
OPENCLAW_MODEL = env(ENV_VALUES, "OPENCLAW_MODEL", "moonshot/kimi-k2.5")
OPENCLAW_AGENT_ID = env(ENV_VALUES, "OPENCLAW_AGENT_ID", "qq_bot")
OPENCLAW_ROUTER_AGENT_ID = env(ENV_VALUES, "OPENCLAW_ROUTER_AGENT_ID", "qq_router")
OPENCLAW_ROUTER_MODEL = env(ENV_VALUES, "OPENCLAW_ROUTER_MODEL", "openai/gpt-5-mini")
TAVILY_API_KEY = env(ENV_VALUES, "TAVILY_API_KEY", "")
TAVILY_API_URL = env(ENV_VALUES, "TAVILY_API_URL", "https://api.tavily.com/search")
TAVILY_MAX_RESULTS = max(1, int(env(ENV_VALUES, "TAVILY_MAX_RESULTS", "5") or "5"))
ALLOWED_GROUP_IDS = {
    int(group_id.strip())
    for group_id in env(ENV_VALUES, "QQ_ALLOWED_GROUP_IDS", "1081502166,608990365").split(",")
    if group_id.strip().isdigit()
}
ACK_EMOJI_ID = env(ENV_VALUES, "QQ_BOT_ACK_EMOJI_ID", "128064")

WORKSPACE_ROOT = Path(env(ENV_VALUES, "QQ_BOT_WORKSPACE", "/home/futunan/data/study_code/game_demo")).resolve()
TODOLIST_PATH = Path(env(ENV_VALUES, "QQ_BOT_TODOLIST_PATH", str(WORKSPACE_ROOT / "todolist.md"))).resolve()
DESCRIPTION_PATH = Path(env(ENV_VALUES, "QQ_BOT_DESCRIPTION_PATH", env(ENV_VALUES, "QQ_BOT_VERSION_PATH", str(WORKSPACE_ROOT / "description.md")))).resolve()

CLASS_LABELS = {
    "feature_request",
    "version_query",
    "bot_abuse",
    "web_answerable",
    "smalltalk",
    "match_push",
}

OPENCLAW = OpenClawClient(
    url=OPENCLAW_URL,
    token=OPENCLAW_TOKEN,
    model=OPENCLAW_MODEL,
    agent_id=OPENCLAW_AGENT_ID,
)


def _assert_allowed_file(path: Path, *, writable: bool) -> None:
    resolved = path.resolve()
    if writable:
        if resolved != TODOLIST_PATH:
            raise RuntimeError("Write access is only allowed for todolist.md")
        return
    if resolved not in {TODOLIST_PATH, DESCRIPTION_PATH}:
        raise RuntimeError("Read access is only allowed for todolist.md and description.md")


def _read_controlled_file(path: Path) -> str:
    _assert_allowed_file(path, writable=False)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _write_todolist(content: str) -> None:
    _assert_allowed_file(TODOLIST_PATH, writable=True)
    TODOLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    TODOLIST_PATH.write_text(content, encoding="utf-8")


def _todolist_display_text(content: str) -> str:
    lines = content.splitlines()
    if lines and lines[0].strip() == "# Todo List":
        lines = lines[1:]
    display = "\n".join(lines).strip()
    return display or "当前还没有待办功能。"


def _parse_todolist_items(content: str) -> list[str]:
    items: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("- [ ] "):
            item = line[6:].strip()
            if item:
                items.append(item)
    return items


def _normalize_todolist_item_for_compare(item: str) -> str:
    return re.sub(r"\s+", "", item).strip().lower()


def _build_todolist_content(items: list[str]) -> str:
    normalized_items = [item.strip() for item in items if item.strip()]
    if not normalized_items:
        return "# Todo List\n"
    lines = ["# Todo List", ""]
    lines.extend(f"- [ ] {item}" for item in normalized_items)
    return "\n".join(lines).rstrip() + "\n"


def _message_channel(group_id: int, message_id: int, stage: str) -> str:
    safe_stage = re.sub(r"[^a-z0-9-]+", "-", stage.lower()).strip("-") or "msg"
    return f"qq-g{group_id}-m{message_id}-{safe_stage}"


async def ask_openclaw(prompt: str, *, channel: str = "qq-group", agent_id: str | None = None) -> str:
    return await OPENCLAW.ask(prompt, channel=channel, agent_id=agent_id)


async def ask_router_openclaw(prompt: str, *, channel: str) -> str:
    return await OPENCLAW.ask(
        prompt,
        channel=channel,
        agent_id=OPENCLAW_ROUTER_AGENT_ID,
        model=OPENCLAW_ROUTER_MODEL,
    )


def _tavily_search_sync(query: str) -> dict[str, Any]:
    if not TAVILY_API_KEY:
        raise RuntimeError("Tavily API key is not configured.")
    body = json.dumps(
        _build_tavily_payload(query),
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        TAVILY_API_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Tavily HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Tavily request failed: {exc}") from exc


async def _tavily_search(query: str) -> dict[str, Any]:
    return await asyncio.to_thread(_tavily_search_sync, query)


def _build_tavily_payload(query: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": TAVILY_MAX_RESULTS,
        "include_answer": True,
        "search_depth": "advanced",
        "include_raw_content": False,
        "topic": "general",
    }
    lowered = query.lower()
    if "dota" in lowered or "刀塔" in query:
        payload["include_domains"] = ["dota2.com", "www.dota2.com"]
        if any(token in query for token in ("装备", "版本", "补丁", "更新", "item", "patch", "meta")):
            payload["query"] = f"Dota 2 latest patch official patch notes {query}"
    return payload


def _build_tavily_context(search_data: dict[str, Any]) -> str:
    answer = str(search_data.get("answer", "")).strip()
    raw_results = search_data.get("results", [])
    lines: list[str] = []
    if answer:
        lines.append(f"搜索摘要：{answer}")
    if isinstance(raw_results, list):
        for index, item in enumerate(raw_results[:TAVILY_MAX_RESULTS], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            snippet = str(item.get("content") or item.get("snippet") or "").strip()
            block = [f"[结果{index}]"]
            if title:
                block.append(f"标题：{title}")
            if url:
                block.append(f"链接：{url}")
            if snippet:
                block.append(f"摘要：{snippet}")
            lines.append("\n".join(block))
    return "\n\n".join(lines).strip()


async def _send_ack_emoji(bot: Bot, message_id: int) -> None:
    started_at = time.monotonic()
    await bot.call_api(
        "set_msg_emoji_like",
        message_id=str(message_id),
        emoji_id=str(ACK_EMOJI_ID),
        set=True,
    )
    finished_at = time.monotonic()
    logger.info(
        "Ack emoji sent for message {} in {:.3f}s",
        message_id,
        finished_at - started_at,
    )


async def _classify_message(content: str, *, channel: str) -> str:
    prompt = (
        "你是一个严格的消息分类器。\n"
        "请把下面这条发给 QQ 机器人的群消息，严格分类为且仅输出以下六个标签之一：\n"
        "feature_request\nversion_query\nbot_abuse\nweb_answerable\nsmalltalk\nmatch_push\n\n"
        "分类规则：\n"
        "1. 只有在用户明确提出想新增、扩展、后续增加某个功能时，才是 feature_request。\n"
        "2. 只要用户是在问机器人当前已经有什么功能、支持什么能力、版本特性、现在会做什么，一律归类为 version_query。\n"
        "3. 骂机器人、嘲讽机器人、攻击机器人本身，归类为 bot_abuse。\n"
        "4. 如果用户是在要求补发、推送、发一下某个 Dota2 监听玩家的最新战绩，归类为 match_push。\n"
        "5. 明显需要联网查实时信息、资料、天气、新闻、官网信息的问题，归类为 web_answerable。\n"
        "6. 其他普通闲聊归类为 smalltalk。\n\n"
        "几个例子：\n"
        "你有什么功能 -> version_query\n"
        "你现在支持哪些功能 -> version_query\n"
        "你能做什么 -> version_query\n"
        "给机器人增加天气提醒功能 -> feature_request\n"
        "以后加个群签到功能 -> feature_request\n"
        "发一下洗洗的最新战绩 -> match_push\n"
        "把四万最近一把战绩补推一下 -> match_push\n\n"
        "只输出标签本身，不要解释，不要标点，不要代码块。\n\n"
        f"消息：{content}"
    )
    raw = collapse_text(await ask_router_openclaw(prompt, channel=channel))
    for label in CLASS_LABELS:
        if raw == label:
            return label
    lowered = raw.lower()
    for label in CLASS_LABELS:
        if label in lowered:
            return label
    return "smalltalk"


async def _handle_feature_request(content: str, *, channel: str) -> str:
    if any(
        phrase in content
        for phrase in ("你有什么功能", "你现在支持哪些功能", "你支持哪些功能", "你能做什么", "现在支持什么", "版本特性")
    ):
        return await _handle_version_query(content, channel=channel)

    current = _read_controlled_file(TODOLIST_PATH)
    current_items = _parse_todolist_items(current)
    prompt = (
        "你在维护一个机器人功能待办清单。\n"
        "请根据当前用户消息和已有待办，只做判断与归一化，不要重写整个文档。\n"
        "请只返回 JSON，对象格式必须是："
        '{"is_feature_request":true,"normalized_item":"归一化后的单条待办","is_duplicate":false}'
        "。\n"
        "要求：\n"
        "1. is_feature_request 表示这条消息是否真的是在提新增机器人功能。\n"
        "2. normalized_item 只保留一条简短、清晰的待办描述，不要带 `- [ ]`。\n"
        "3. is_duplicate 表示 normalized_item 是否与现有待办语义重复。\n"
        "4. 如果不是功能需求，is_feature_request=false，normalized_item 置空，is_duplicate=false。\n"
        "5. 只输出 JSON，不要解释，不要额外文字。\n\n"
        "当前待办条目(JSON数组)：\n"
        f"{json.dumps(current_items, ensure_ascii=False)}\n\n"
        f"用户消息：{content}"
    )
    parsed = extract_json_object(await ask_router_openclaw(prompt, channel=channel))
    is_feature_request = bool(parsed.get("is_feature_request"))
    normalized_item = str(parsed.get("normalized_item", "")).strip()
    normalized_item = re.sub(r"^\s*-\s*\[\s*\]\s*", "", normalized_item).strip()
    is_duplicate = bool(parsed.get("is_duplicate"))
    normalized_key = _normalize_todolist_item_for_compare(normalized_item)
    existing_keys = {_normalize_todolist_item_for_compare(item) for item in current_items}

    if not is_feature_request or not normalized_item or is_duplicate or normalized_key in existing_keys:
        return _todolist_display_text((current or "# Todo List\n").rstrip())

    updated_items = current_items + [normalized_item]
    updated = _build_todolist_content(updated_items)
    if updated != current:
        _write_todolist(updated)
    return _todolist_display_text(updated.rstrip())


async def _handle_version_query(content: str, *, channel: str) -> str:
    version_text = _read_controlled_file(DESCRIPTION_PATH)
    if not version_text.strip():
        return "当前还没有维护版本说明文件。"
    return version_text.strip()


async def _handle_bot_abuse(content: str, *, channel: str) -> str:
    prompt = (
        "你是一个 QQ 机器人。用户正在骂你或批评你。\n"
        "请直接输出一句最终回怼原文。\n"
        "要求：简短、阴阳、允许一点刻薄和嘲讽，但不要现实侮辱、不要仇恨、不要长篇输出。\n"
        "可以带 1 个恰到好处的 emoji。\n"
        "不要解释，不要复述要求，不要复述用户消息，不要分析，不要加引号，不要输出多段。\n"
        "只输出最终这句回怼本身。\n\n"
        f"用户消息：{content}"
    )
    return collapse_text(await ask_router_openclaw(prompt, channel=channel))


async def _handle_web_answerable(content: str, *, channel: str) -> str:
    search_data = await _tavily_search(content)
    search_context = _build_tavily_context(search_data)
    if not search_context:
        return "我刚刚没有查到足够可靠的联网结果。"
    prompt = (
        f"当前日期：{time.strftime('%Y-%m-%d')}。\n"
        "你是一个联网问答整理器。下面已经给你 Tavily 搜索结果。\n"
        "请只基于这些搜索结果回答，不要使用你自己的旧知识补充，不要编造。\n"
        "如果搜索结果明显不足以支撑结论，就直接简短说没查到足够可靠的信息。\n"
        "要求：中文简短回答，1 到 3 句，优先给结论；不要复述题目，不要解释步骤，不要输出任务说明。\n\n"
        f"用户问题：{content}\n\n"
        f"Tavily 搜索结果：\n{search_context}"
    )
    return collapse_text(await ask_openclaw(prompt, channel=channel))


async def _handle_smalltalk(content: str, *, channel: str) -> str:
    prompt = (
        "你是一个群聊机器人。请对下面这句闲聊做一个简短中文回复。\n"
        "要求：轻松自然，1 句即可，可以带 1 到 2 个恰到好处的 emoji，不要长篇输出。\n"
        "不要复述要求，不要分析，不要复述用户消息，只输出最终回复。\n\n"
        f"用户消息：{content}"
    )
    return collapse_text(await ask_router_openclaw(prompt, channel=channel))


async def _extract_match_push_account(content: str, *, channel: str) -> str | None:
    watched_accounts = list_watched_accounts()
    prompt = (
        "你要从一句 QQ 群消息里识别用户想补推哪个 Dota2 监听账号的最新战绩。\n"
        "候选账号如下(JSON)：\n"
        f"{json.dumps(watched_accounts, ensure_ascii=False)}\n\n"
        "请只返回 JSON，对象格式必须是："
        '{"account_id":"候选中的账号ID，无法确定则填空字符串"}'
        "。\n"
        "不要解释，不要额外文字。\n\n"
        f"用户消息：{content}"
    )
    parsed = extract_json_object(await ask_router_openclaw(prompt, channel=channel))
    account_id = str(parsed.get("account_id", "")).strip()
    valid_ids = {item["account_id"] for item in watched_accounts}
    return account_id if account_id in valid_ids else None


def _handle_add_command(content: str, *, group_id: int) -> str:
    parts = content.split()
    if len(parts) != 3:
        return "用法: /add <昵称> <steamID>"
    _, nickname, account_id = parts
    _, message = add_watch_account(nickname, account_id, group_id)
    return message


def _handle_list_command(*, group_id: int) -> str:
    accounts = list_group_accounts(group_id)
    if not accounts:
        return "当前群还没有配置监听账号。"
    names = "、".join(item["display_name"] for item in accounts)
    return f"当前群监听账号：{names}"


def _handle_todo_command() -> str:
    return _todolist_display_text(_read_controlled_file(TODOLIST_PATH))


def _handle_help_command() -> str:
    version_text = _read_controlled_file(DESCRIPTION_PATH).strip()
    return version_text or "当前还没有维护版本说明文件。"


def _try_handle_local_group_command(content: str, *, group_id: int) -> str | None:
    stripped = content.strip()
    if stripped.startswith("/add"):
        return _handle_add_command(stripped, group_id=group_id)
    if stripped == "/list":
        return _handle_list_command(group_id=group_id)
    if stripped == "/todo":
        return _handle_todo_command()
    if stripped == "/help":
        return _handle_help_command()
    return None


async def _handle_match_push(content: str, *, channel: str) -> str:
    account_id = resolve_watched_account(content)
    if account_id is None:
        account_id = await _extract_match_push_account(content, channel=channel)
    if account_id is None:
        names = "、".join(item["display_name"] for item in list_watched_accounts())
        return f"我没识别出你要补推谁的战绩。当前可用账号有：{names}"
    return collapse_text(await build_latest_match_push_text(account_id))


RouteHandler = Callable[..., Awaitable[str]]

sendqq = on_command("sendqq", priority=5, block=True, permission=SUPERUSER)
sendgroup = on_command("sendgroup", priority=5, block=True, permission=SUPERUSER)
group_chat = on_message(priority=10, block=True, rule=to_me())

ROUTE_HANDLERS: dict[str, RouteHandler] = {
    "feature_request": _handle_feature_request,
    "version_query": _handle_version_query,
    "bot_abuse": _handle_bot_abuse,
    "web_answerable": _handle_web_answerable,
    "smalltalk": _handle_smalltalk,
    "match_push": _handle_match_push,
}


async def route_group_prompt(prompt: str, *, group_id: int, message_id: int) -> tuple[str, str]:
    route = await _classify_message(
        prompt,
        channel=_message_channel(group_id, message_id, "classifier"),
    )
    handler = ROUTE_HANDLERS.get(route, _handle_smalltalk)
    answer = collapse_text(
        await handler(
            prompt,
            channel=_message_channel(group_id, message_id, route),
        )
    )
    if not answer:
        answer = "我收到消息了，但这次没组织出有效回复。"
    return route, answer


@sendqq.handle()
async def handle_sendqq(args: Message = CommandArg()) -> None:
    raw = str(args).strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await sendqq.finish("用法: /sendqq <qq号> <消息内容>")
    result = await send_private_text(int(parts[0]), parts[1].strip())
    await sendqq.finish(f"已发送私聊，message_id={result.get('message_id', 'unknown')}")


@sendgroup.handle()
async def handle_sendgroup(args: Message = CommandArg()) -> None:
    raw = str(args).strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await sendgroup.finish("用法: /sendgroup <群号> <消息内容>")
    result = await send_group_text(int(parts[0]), parts[1].strip())
    await sendgroup.finish(f"已发送群消息，message_id={result.get('message_id', 'unknown')}")


@group_chat.handle()
async def handle_group_chat(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        return
    if event.group_id not in ALLOWED_GROUP_IDS:
        return
    bot = get_bots().get(str(event.self_id))
    if not isinstance(bot, Bot):
        logger.error("No active OneBot bot found for self_id=%s", event.self_id)
        return

    prompt = event.get_plaintext().strip()
    if not prompt:
        await group_chat.finish(
            MessageSegment.reply(event.message_id) + Message("请在 @我 的同时附上内容。")
        )

    logger.info("Ack emoji requested for message {} in group {}", event.message_id, event.group_id)
    try:
        await _send_ack_emoji(bot, int(event.message_id))
    except Exception:
        logger.exception("Failed to add ack emoji to message {}", event.message_id)

    try:
        local_answer = _try_handle_local_group_command(prompt, group_id=event.group_id)
        if local_answer is not None:
            answer = collapse_text(local_answer)
        else:
            _, answer = await route_group_prompt(
                prompt,
                group_id=event.group_id,
                message_id=int(event.message_id),
            )
    except Exception:
        logger.exception("Failed to handle @mention message in group %s", event.group_id)
        answer = "我收到消息了，但刚刚处理这条消息时出了点问题。"

    reply = MessageSegment.reply(event.message_id) + Message(answer)
    await group_chat.finish(reply)
