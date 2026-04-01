from __future__ import annotations

from pathlib import Path

from .common import OpenClawClient, env, load_env_file

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)

OPENCLAW_URL = env(ENV_VALUES, "OPENCLAW_URL", "http://127.0.0.1:18789/v1/responses")
OPENCLAW_TOKEN = env(ENV_VALUES, "OPENCLAW_TOKEN", "")
OPENCLAW_MODEL = env(ENV_VALUES, "OPENCLAW_MODEL", "moonshot/kimi-k2.5")
OPENCLAW_AGENT_ID = env(ENV_VALUES, "OPENCLAW_AGENT_ID", "qq_bot")
OPENCLAW_ROUTER_AGENT_ID = env(ENV_VALUES, "OPENCLAW_ROUTER_AGENT_ID", "qq_router")
OPENCLAW_ROUTER_MODEL = env(ENV_VALUES, "OPENCLAW_ROUTER_MODEL", "openai/gpt-5-mini")

OPENCLAW = OpenClawClient(
    url=OPENCLAW_URL,
    token=OPENCLAW_TOKEN,
    model=OPENCLAW_MODEL,
    agent_id=OPENCLAW_AGENT_ID,
)


async def ask_main(prompt: str, *, channel: str, agent_id: str | None = None, model: str | None = None) -> str:
    return await OPENCLAW.ask(prompt, channel=channel, agent_id=agent_id, model=model)


async def ask_router(prompt: str, *, channel: str) -> str:
    return await OPENCLAW.ask(
        prompt,
        channel=channel,
        agent_id=OPENCLAW_ROUTER_AGENT_ID,
        model=OPENCLAW_ROUTER_MODEL,
    )


async def ask_dota(prompt: str) -> str:
    return await OPENCLAW.ask(prompt, channel="qq-dota2-v2", agent_id=OPENCLAW_AGENT_ID)
