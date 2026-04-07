from __future__ import annotations

import time
from pathlib import Path

from nonebot import get_bots, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from .common import env, format_qq_chat_text, load_env_file
from .group_chat_store import record_bot_group_reply, record_user_group_event
from .qq_commands import try_handle_local_group_command
from .qq_router import route_group_prompt

__plugin_meta__ = PluginMetadata(
    name='qq_entry',
    description='QQ group message entrypoint and dispatcher.',
    usage='群里 @机器人 后，优先处理本地命令，再处理自然语言消息。',
)

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / '.env'
ENV_VALUES = load_env_file(ENV_FILE)
ALLOWED_GROUP_IDS = {
    int(group_id.strip())
    for group_id in env(ENV_VALUES, 'QQ_ALLOWED_GROUP_IDS', '1081502166,608990365').split(',')
    if group_id.strip().isdigit()
}
ACK_EMOJI_ID = env(ENV_VALUES, 'QQ_BOT_ACK_EMOJI_ID', '128064')
TEXT_BOT_MENTION_PREFIXES = ('@机器人', '＠机器人')

group_message_recorder = on_message(priority=5, block=False)


def _extract_plaintext_bot_prefix_prompt(text: str) -> str | None:
    stripped = text.strip()
    for prefix in TEXT_BOT_MENTION_PREFIXES:
        if not stripped.startswith(prefix):
            continue
        return stripped[len(prefix) :].strip()
    return None


async def _has_plaintext_bot_prefix(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    return _extract_plaintext_bot_prefix_prompt(event.get_plaintext()) is not None


async def _is_group_chat_entry(event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    return bool(getattr(event, 'to_me', False)) or await _has_plaintext_bot_prefix(event)


group_chat = on_message(priority=10, block=True, rule=Rule(_is_group_chat_entry))


def _sender_name(event: GroupMessageEvent) -> str:
    sender = getattr(event, 'sender', None)
    for attr in ('card', 'nickname'):
        value = getattr(sender, attr, '') if sender is not None else ''
        if value:
            return str(value)
    return str(event.user_id)


def _is_command_message(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and stripped.startswith('/')


def _message_text_for_context(event: GroupMessageEvent) -> str:
    parts: list[str] = []
    plaintext = event.get_plaintext().strip()
    if plaintext:
        return plaintext
    for segment in event.message:
        if segment.type == 'text':
            text = str(segment.data.get('text', '')).strip()
            if text:
                parts.append(text)
            continue
        if segment.type == 'image':
            parts.append('[图片]')
        elif segment.type == 'face':
            parts.append('[表情]')
        elif segment.type == 'reply':
            parts.append('[回复]')
        elif segment.type == 'forward':
            parts.append('[转发]')
        elif segment.type == 'record':
            parts.append('[语音]')
        elif segment.type == 'video':
            parts.append('[视频]')
        elif segment.type == 'json':
            parts.append('[卡片]')
        elif segment.type == 'xml':
            parts.append('[XML]')
        elif segment.type == 'at':
            qq = str(segment.data.get('qq', '')).strip()
            if qq and qq != str(event.self_id):
                parts.append(f'@{qq}')
        else:
            parts.append(f'[{segment.type}]')
    return ' '.join(part for part in parts if part).strip()


def _extract_bot_prompt(event: GroupMessageEvent) -> str | None:
    plaintext = event.get_plaintext().strip()
    prefixed_prompt = _extract_plaintext_bot_prefix_prompt(plaintext)
    if prefixed_prompt is not None:
        return prefixed_prompt
    if getattr(event, 'to_me', False):
        return plaintext
    return None


async def _send_ack_emoji(bot: Bot, message_id: int) -> None:
    started_at = time.monotonic()
    await bot.call_api(
        'set_msg_emoji_like',
        message_id=str(message_id),
        emoji_id=str(ACK_EMOJI_ID),
        set=True,
    )
    finished_at = time.monotonic()
    logger.info('Ack emoji sent for message {} in {:.3f}s', message_id, finished_at - started_at)


@group_message_recorder.handle()
async def handle_group_message_record(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        return
    if event.group_id not in ALLOWED_GROUP_IDS:
        return
    if str(event.user_id) == str(event.self_id):
        return
    message_text = _message_text_for_context(event)
    bot_prompt = _extract_plaintext_bot_prefix_prompt(message_text)
    if _is_command_message(message_text) or (bot_prompt is not None and _is_command_message(bot_prompt)):
        record_user_group_event(
            event.group_id,
            _sender_name(event),
            '',
            kind='user_command',
        )
        return
    record_user_group_event(
        event.group_id,
        _sender_name(event),
        message_text,
    )


@group_chat.handle()
async def handle_group_chat(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        return
    if event.group_id not in ALLOWED_GROUP_IDS:
        return
    bot = get_bots().get(str(event.self_id))
    if not isinstance(bot, Bot):
        logger.error('No active OneBot bot found for self_id=%s', event.self_id)
        return

    prompt = _extract_bot_prompt(event)
    if prompt is None:
        return
    if not prompt:
        await group_chat.finish(MessageSegment.reply(event.message_id) + Message(format_qq_chat_text('请在 @我 或输入“@机器人”时附上内容。')))

    logger.info('Ack emoji requested for message {} in group {}', event.message_id, event.group_id)
    try:
        await _send_ack_emoji(bot, int(event.message_id))
    except Exception:
        logger.exception('Failed to add ack emoji to message {}', event.message_id)

    route: str | None = None
    try:
        local_answer = await try_handle_local_group_command(prompt, group_id=event.group_id, user_id=int(event.user_id))
        if local_answer is not None:
            answer = local_answer.strip()
        else:
            route, answer = await route_group_prompt(prompt, group_id=event.group_id, message_id=int(event.message_id))
            if route in {'group_chat', 'web_answerable'}:
                record_bot_group_reply(event.group_id, route, answer)
    except Exception:
        logger.exception('Failed to handle @mention message in group {}', event.group_id)
        answer = "这条消息处理失败，请稍后再试。"

    reply = MessageSegment.reply(event.message_id) + Message(format_qq_chat_text(answer))
    await group_chat.finish(reply)
