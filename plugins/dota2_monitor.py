from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .common import env, load_env_file
from .dota2_service import is_v2_debug_enabled, push_recent_matches_with_openclaw, run_dota2_check_once, set_v2_debug_enabled
from .dota2_watch_config import list_watch_account_ids

__plugin_meta__ = PluginMetadata(
    name="dota2_monitor",
    description="Poll Steam Dota2 APIs and push new match summaries or OpenClaw commentary to QQ groups.",
    usage=(
        "/dota_check 手动触发一次 Dota2 战绩检查。\n"
        "/dota_check refresh 重新刷新英雄/物品缓存。\n"
        "/dota_backfill_v2 [数量] 顺序补推最近 N 把比赛到 QQ 群。"
    ),
)

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
DOTA2_ENABLED = env(ENV_VALUES, "DOTA2_ENABLED", "true").lower() == "true"
DOTA2_NOTIFY_GROUP_ID = int(env(ENV_VALUES, "DOTA2_NOTIFY_GROUP_ID", "1081502166"))
DOTA2_POLL_INTERVAL_SECONDS = max(60, int(env(ENV_VALUES, "DOTA2_POLL_INTERVAL_SECONDS", "300")))
DOTA2_OUTPUT_VERSION = env(ENV_VALUES, "DOTA2_OUTPUT_VERSION", "v1").strip().lower() or "v1"
DOTA2_V2_STARTUP_BACKFILL_MATCHES = max(0, int(env(ENV_VALUES, "DOTA2_V2_STARTUP_BACKFILL_MATCHES", "0")))


driver = get_driver()
dota_check = on_command("dota_check", priority=5, block=True, permission=SUPERUSER)
dota_backfill_v2 = on_command("dota_backfill_v2", priority=5, block=True, permission=SUPERUSER)
dota_debug = on_command("dota_debug", priority=5, block=True, permission=SUPERUSER)
_poller_task: asyncio.Task[None] | None = None


async def _poll_loop() -> None:
    while True:
        try:
            await run_dota2_check_once()
        except Exception:
            logger.exception("Dota2 poll loop failed")
        await asyncio.sleep(DOTA2_POLL_INTERVAL_SECONDS)


@driver.on_startup
async def _startup_dota2_monitor() -> None:
    global _poller_task
    if not DOTA2_ENABLED:
        logger.info("Dota2 monitor is disabled.")
        return
    if _poller_task and not _poller_task.done():
        return
    _poller_task = asyncio.create_task(_poll_loop(), name="dota2-monitor-poller")
    logger.info(
        f"Dota2 monitor started: version={DOTA2_OUTPUT_VERSION} default_group={DOTA2_NOTIFY_GROUP_ID} "
        f"accounts={','.join(list_watch_account_ids())} interval={DOTA2_POLL_INTERVAL_SECONDS}s"
    )


@driver.on_shutdown
async def _shutdown_dota2_monitor() -> None:
    global _poller_task
    if _poller_task:
        _poller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _poller_task
        _poller_task = None


@dota_check.handle()
async def _handle_dota_check(args: Message = CommandArg()) -> None:
    force_refresh = str(args).strip().lower() == "refresh"
    summaries = await run_dota2_check_once(force_refresh=force_refresh)
    if not summaries:
        await dota_check.finish("Dota2 检查完成，没有发现需要推送的新比赛。")
    await dota_check.finish("Dota2 检查完成：\n" + "\n".join(summaries))


@dota_backfill_v2.handle()
async def _handle_dota_backfill_v2(args: Message = CommandArg()) -> None:
    raw = str(args).strip()
    count = DOTA2_V2_STARTUP_BACKFILL_MATCHES or 5
    if raw:
        try:
            count = max(1, min(10, int(raw)))
        except ValueError:
            await dota_backfill_v2.finish("用法: /dota_backfill_v2 [数量]")
    summaries = await push_recent_matches_with_openclaw(count)
    if not summaries:
        await dota_backfill_v2.finish("v2 补推完成，但没有发送任何比赛。")
    await dota_backfill_v2.finish("v2 补推完成：\n" + "\n".join(summaries))


@dota_debug.handle()
async def _handle_dota_debug(args: Message = CommandArg()) -> None:
    raw = str(args).strip().lower()
    if raw in {"", "status"}:
        status = "on" if is_v2_debug_enabled() else "off"
        await dota_debug.finish(f"Dota2 v2 debug 当前状态：{status}")
    if raw in {"on", "true", "1", "enable", "enabled"}:
        set_v2_debug_enabled(True)
        await dota_debug.finish("Dota2 v2 debug 已开启。")
    if raw in {"off", "false", "0", "disable", "disabled"}:
        set_v2_debug_enabled(False)
        await dota_debug.finish("Dota2 v2 debug 已关闭。")
    await dota_debug.finish("用法: /dota_debug on|off|status")
