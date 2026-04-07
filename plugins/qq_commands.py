from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from .common import collapse_text, env, load_env_file, send_group_text, send_private_text
from .content_store import read_description, read_todo_display, upsert_todo_item
from .dota2_service import (
    build_latest_match_push_text,
    build_hero_guide_text,
    build_player_profile_text,
    build_recent_match_analysis_text,
    collect_recent_matches,
    collect_recent_matches_for_all,
    list_watched_accounts,
    rebuild_recent_match_analysis,
    resolve_watched_account,
)
from .dota2_watch_config import add_watch_account, list_group_accounts
from .qq_router import fetch_daily_news, message_channel
from .openclaw_group_memory import build_all_group_memory_report

sendqq = on_command("sendqq", priority=5, block=True, permission=SUPERUSER)
sendgroup = on_command("sendgroup", priority=5, block=True, permission=SUPERUSER)

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
QQ_ALLOWED_GROUP_IDS = {
    int(group_id.strip())
    for group_id in env(ENV_VALUES, "QQ_ALLOWED_GROUP_IDS", "").split(",")
    if group_id.strip().isdigit()
}

ADMIN_GROUP_ID = 1081502166
ADMIN_USER_ID = 863347350


def is_admin_group_command_allowed(*, group_id: int, user_id: int) -> bool:
    return int(group_id) == ADMIN_GROUP_ID and int(user_id) == ADMIN_USER_ID


def ensure_admin_group_command_allowed(*, command: str, group_id: int, user_id: int) -> str | None:
    if is_admin_group_command_allowed(group_id=group_id, user_id=user_id):
        return None
    return f"{command} 仅允许指定管理员在指定群内使用。"


def _watched_accounts() -> list[dict[str, str]]:
    return list_watched_accounts()


def _resolve_account_or_error(query: str) -> tuple[str | None, str | None]:
    account_id = resolve_watched_account(query)
    if account_id is None and query.isdigit():
        account_id = query
    if account_id is not None:
        return account_id, None

    names = "、".join(item["display_name"] for item in _watched_accounts())
    if names:
        return None, f"未找到该昵称或 steamID：{query}。当前可用昵称有：{names}"
    return None, f"未找到该昵称或 steamID：{query}。"


def _display_name_for_account(account_id: str) -> str:
    return next((item["display_name"] for item in _watched_accounts() if item["account_id"] == account_id), account_id)


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
    del group_id
    stripped = content.strip()
    if stripped == "/todo":
        return read_todo_display()
    if not stripped.startswith("/todo "):
        return read_todo_display()
    detail = stripped[5:].strip()
    if not detail:
        return read_todo_display()
    normalized_item = re.sub(r"^\s*-\s*\[\s*\]\s*", "", detail).strip()
    if not normalized_item:
        return read_todo_display()
    return upsert_todo_item(normalized_item)


async def _handle_help_command() -> str:
    return read_description()


async def _handle_push_command(content: str) -> str:
    parts = content.split()
    if len(parts) != 2:
        return "用法: /push <昵称>"
    _, nickname = parts
    watched_accounts = _watched_accounts()
    match = next((item for item in watched_accounts if item["display_name"] == nickname), None)
    if match is None:
        names = "、".join(item["display_name"] for item in watched_accounts)
        return f"未找到该昵称：{nickname}。当前可用昵称有：{names}"
    return collapse_text(await build_latest_match_push_text(match["account_id"]))


async def _handle_dota_collect_command(content: str, *, group_id: int, user_id: int) -> str:
    denied = ensure_admin_group_command_allowed(command="/dota_collect", group_id=group_id, user_id=user_id)
    if denied is not None:
        return denied
    parts = content.split()
    if len(parts) == 1:
        summary = await collect_recent_matches_for_all(50)
        lines = [
            f"Dota2 批量采集完成：共 {summary['accounts']} 个监听账号，每个账号最近 {summary['requested_per_account']} 场。",
            f"总请求 {summary['requested_total']} 场，扫描 {summary['scanned']} 场，新采集 {summary['fetched']} 场，跳过 {summary['skipped']} 场，失败 {summary['failed']} 场。",
        ]
        for item in summary["per_account"][:8]:
            lines.append(
                f"{item['display_name']}：扫描 {item['scanned']}，新增 {item['fetched']}，跳过 {item['skipped']}，失败 {item['failed']}"
            )
        return "\n".join(lines)
    if len(parts) not in {2, 3}:
        return "用法: /dota_collect [昵称或steamID] [数量]"
    _, query, *rest = parts
    if rest:
        if not rest[0].isdigit():
            return "用法: /dota_collect [昵称或steamID] [数量]"
        matches_requested = int(rest[0])
    else:
        matches_requested = 50
    if matches_requested <= 0:
        return "数量必须大于 0。"

    account_id, error = _resolve_account_or_error(query)
    if error is not None:
        return error
    assert account_id is not None

    summary = await collect_recent_matches(account_id, matches_requested)
    return (
        f"Dota2 采集完成：{_display_name_for_account(account_id)}，请求 {summary['requested']} 场，"
        f"扫描 {summary['scanned']} 场，新采集 {summary['fetched']} 场，"
        f"跳过 {summary['skipped']} 场，失败 {summary['failed']} 场。"
    )


async def _handle_dota_profile_command(content: str, *, group_id: int) -> str:
    parts = content.split()
    if len(parts) != 2:
        return "用法: /dota_profile <昵称或steamID>"
    _, query = parts
    account_id, error = _resolve_account_or_error(query)
    if error is not None:
        return error
    assert account_id is not None
    return (await build_player_profile_text(account_id, group_id=group_id)).strip()


async def _handle_dota_guide_command(content: str, *, group_id: int) -> str:
    stripped = content.strip()
    if not stripped.startswith("/dota_guide "):
        return "用法: /dota_guide <英雄名>"
    hero_query = stripped[len("/dota_guide ") :].strip()
    if not hero_query:
        return "用法: /dota_guide <英雄名>"
    return (await build_hero_guide_text(hero_query, group_id=group_id)).strip()


async def _handle_dota_rebuild_analysis_command(content: str, *, group_id: int, user_id: int) -> str:
    denied = ensure_admin_group_command_allowed(command="/dota_rebuild_analysis", group_id=group_id, user_id=user_id)
    if denied is not None:
        return denied
    parts = content.split()
    if len(parts) not in {1, 2}:
        return "用法: /dota_rebuild_analysis [昵称或steamID]"

    account_id: str | None = None
    display_name = "全部监听账号"
    if len(parts) == 2:
        account_id, error = _resolve_account_or_error(parts[1])
        if error is not None:
            return error
        assert account_id is not None
        display_name = _display_name_for_account(account_id)

    summary = rebuild_recent_match_analysis(account_id)
    return (
        f"Dota2 分析表重建完成：{display_name}，扫描 {summary['scanned_matches']} 场，"
        f"新增 {summary['inserted_rows']} 行，跳过已存在 {summary['skipped_existing_rows']} 行，"
        f"失败比赛 {summary['failed_matches']} 场，失败玩家 {summary['failed_players']} 个。"
    )


async def _handle_dota_analyze_command(content: str) -> str:
    parts = content.split()
    if len(parts) != 2:
        return "用法: /dota_analyze <昵称或steamID>"
    _, query = parts
    account_id, error = _resolve_account_or_error(query)
    if error is not None:
        return error
    assert account_id is not None
    return build_recent_match_analysis_text(account_id)


async def _handle_news_command(content: str, *, group_id: int) -> str:
    stripped = content.strip()
    keyword = stripped[5:].strip() if stripped.startswith('/news') else ''
    return await fetch_daily_news(
        channel=message_channel(group_id, 0, 'news'),
        keyword=keyword,
    )


async def _handle_check_memory_command(*, group_id: int, user_id: int) -> str:
    denied = ensure_admin_group_command_allowed(command="/check_memory", group_id=group_id, user_id=user_id)
    if denied is not None:
        return denied
    return build_all_group_memory_report(QQ_ALLOWED_GROUP_IDS)


async def try_handle_local_group_command(content: str, *, group_id: int, user_id: int = 0) -> str | None:
    stripped = content.strip()
    result: str | None = None
    if stripped.startswith("/add"):
        result = await _handle_add_command(stripped, group_id=group_id)
    elif stripped.startswith("/list"):
        result = await _handle_list_command(group_id=group_id)
    elif stripped.startswith("/todo"):
        result = await _handle_todo_command(stripped, group_id=group_id)
    elif stripped.startswith("/help"):
        result = await _handle_help_command()
    elif stripped.startswith("/push"):
        result = await _handle_push_command(stripped)
    elif stripped.startswith("/dota_collect"):
        result = await _handle_dota_collect_command(stripped, group_id=group_id, user_id=user_id)
    elif stripped.startswith("/dota_profile"):
        result = await _handle_dota_profile_command(stripped, group_id=group_id)
    elif stripped.startswith("/dota_guide"):
        result = await _handle_dota_guide_command(stripped, group_id=group_id)
    elif stripped.startswith("/dota_rebuild_analysis"):
        result = await _handle_dota_rebuild_analysis_command(stripped, group_id=group_id, user_id=user_id)
    elif stripped.startswith("/dota_analyze"):
        result = await _handle_dota_analyze_command(stripped)
    elif stripped.startswith("/news"):
        result = await _handle_news_command(stripped, group_id=group_id)
    elif stripped.startswith("/check_memory"):
        result = await _handle_check_memory_command(group_id=group_id, user_id=user_id)
    elif stripped.startswith("/"):
        result = "未识别的指令。"

    return result


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
