from __future__ import annotations

import json
import re
from typing import Any

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from .common import collapse_text, extract_json_object, send_group_text, send_private_text
from .content_store import (
    read_description,
    read_todo_display,
    read_todo_raw,
    upsert_todo_item,
)
from .dota2_service import build_latest_match_push_text, list_watched_accounts
from .dota2_watch_config import add_watch_account, list_group_accounts
from .llm_gateway import ask_main
from .qq_router import message_channel

sendqq = on_command("sendqq", priority=5, block=True, permission=SUPERUSER)
sendgroup = on_command("sendgroup", priority=5, block=True, permission=SUPERUSER)


async def _normalize_todo_request(content: str, *, group_id: int) -> dict[str, Any]:
    current = read_todo_raw()
    current_items = []
    for raw_line in current.splitlines():
        line = raw_line.strip()
        if line.startswith("- [ ] "):
            item = line[6:].strip()
            if item:
                current_items.append(item)
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
    raw = await ask_main(prompt, channel=message_channel(group_id, 0, "todo"))
    parsed = extract_json_object(raw)
    normalized_item = str(parsed.get("normalized_item", "")).strip()
    normalized_item = re.sub(r"^\s*-\s*\[\s*\]\s*", "", normalized_item).strip()
    return {
        "is_feature_request": bool(parsed.get("is_feature_request")),
        "normalized_item": normalized_item,
        "is_duplicate": bool(parsed.get("is_duplicate")),
    }


async def _handle_add_command(content: str, *, group_id: int) -> str:
    parts = content.split()
    if len(parts) != 3:
        return "用法: /add <昵称> <steamID>"
    _, nickname, account_id = parts
    _, message = add_watch_account(nickname, account_id, group_id)
    return message


async def _handle_list_command(*, group_id: int) -> str:
    accounts = list_group_accounts(group_id)
    if not accounts:
        return "当前群还没有配置监听账号。"
    names = "、".join(item["display_name"] for item in accounts)
    return f"当前群监听账号：{names}"


async def _handle_todo_command(content: str, *, group_id: int) -> str:
    stripped = content.strip()
    if stripped == "/todo":
        return read_todo_display()
    if not stripped.startswith("/todo "):
        return read_todo_display()
    detail = stripped[5:].strip()
    if not detail:
        return read_todo_display()
    parsed = await _normalize_todo_request(detail, group_id=group_id)
    if not parsed["is_feature_request"] or not parsed["normalized_item"] or parsed["is_duplicate"]:
        return read_todo_display()
    return upsert_todo_item(parsed["normalized_item"])


async def _handle_help_command() -> str:
    return read_description()


async def _handle_push_command(content: str) -> str:
    parts = content.split()
    if len(parts) != 2:
        return "用法: /push <昵称>"
    _, nickname = parts
    watched_accounts = list_watched_accounts()
    match = next((item for item in watched_accounts if item["display_name"] == nickname), None)
    if match is None:
        names = "、".join(item["display_name"] for item in watched_accounts)
        return f"未找到该昵称：{nickname}。当前可用昵称有：{names}"
    return collapse_text(await build_latest_match_push_text(match["account_id"]))


async def try_handle_local_group_command(content: str, *, group_id: int) -> str | None:
    stripped = content.strip()
    if stripped.startswith("/add"):
        return await _handle_add_command(stripped, group_id=group_id)
    if stripped.startswith("/list"):
        return await _handle_list_command(group_id=group_id)
    if stripped.startswith("/todo"):
        return await _handle_todo_command(stripped, group_id=group_id)
    if stripped.startswith("/help"):
        return await _handle_help_command()
    if stripped.startswith("/push"):
        return await _handle_push_command(stripped)
    if stripped.startswith("/"):
        return "未识别的指令。"
    return None


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
