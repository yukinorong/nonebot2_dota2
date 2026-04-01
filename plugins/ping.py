from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message

ping = on_command("ping", priority=5, block=True)


@ping.handle()
async def handle_ping() -> None:
    await ping.finish(Message("pong"))
