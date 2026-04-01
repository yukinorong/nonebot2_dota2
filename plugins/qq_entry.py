from __future__ import annotations

import time
from pathlib import Path

from nonebot import get_bots, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me

from .common import collapse_text, env, load_env_file
from .qq_commands import try_handle_local_group_command
from .qq_router import route_group_prompt

__plugin_meta__ = PluginMetadata(
    name="qq_entry",
    description="QQ group message entrypoint and dispatcher.",
    usage="群里 @机器人 后，优先处理本地命令，再处理自然语言消息。",
)

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
ALLOWED_GROUP_IDS = {
    int(group_id.strip())
    for group_id in env(ENV_VALUES, "QQ_ALLOWED_GROUP_IDS", "1081502166,608990365").split(",")
    if group_id.strip().isdigit()
}
ACK_EMOJI_ID = env(ENV_VALUES, "QQ_BOT_ACK_EMOJI_ID", "128064")

group_chat = on_message(priority=10, block=True, rule=to_me())


async def _send_ack_emoji(bot: Bot, message_id: int) -> None:
    started_at = time.monotonic()
    await bot.call_api(
        "set_msg_emoji_like",
        message_id=str(message_id),
        emoji_id=str(ACK_EMOJI_ID),
        set=True,
    )
    finished_at = time.monotonic()
    logger.info("Ack emoji sent for message {} in {:.3f}s", message_id, finished_at - started_at)


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
        await group_chat.finish(MessageSegment.reply(event.message_id) + Message("请在 @我 的同时附上内容。"))

    logger.info("Ack emoji requested for message {} in group {}", event.message_id, event.group_id)
    try:
        await _send_ack_emoji(bot, int(event.message_id))
    except Exception:
        logger.exception("Failed to add ack emoji to message {}", event.message_id)

    try:
        local_answer = await try_handle_local_group_command(prompt, group_id=event.group_id)
        if local_answer is not None:
            answer = collapse_text(local_answer)
        else:
            _, answer = await route_group_prompt(prompt, group_id=event.group_id, message_id=int(event.message_id))
    except Exception:
        logger.exception("Failed to handle @mention message in group %s", event.group_id)
        answer = "我收到消息了，但刚刚处理这条消息时出了点问题。"

    reply = MessageSegment.reply(event.message_id) + Message(answer)
    await group_chat.finish(reply)
