from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from nonebot import get_bots
from nonebot.adapters.onebot.v11 import Bot


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def env(values: dict[str, str], name: str, default: str = "") -> str:
    return os.getenv(name, values.get(name, default))


def collapse_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def format_qq_chat_text(text: str) -> str:
    if not text.strip():
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"```[a-zA-Z0-9_-]*\n?", "", normalized)
    normalized = normalized.replace("```", "")

    lines: list[str] = []
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue

        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1：\2", line)
        line = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"[图片] \1 \2", line)
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"^\s*>\s*", "", line)
        line = re.sub(r"^\s*[-*+]\s+\[\s\]\s*", "[未完成] ", line)
        line = re.sub(r"^\s*[-*+]\s+\[[xX]\]\s*", "[已完成] ", line)
        line = re.sub(r"^\s*[-*+]\s+", "• ", line)
        line = re.sub(r"^(\d+)\)\s+", r"\1. ", line)
        line = line.replace("`", "")
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", line)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def strip_reasoning_text(text: str) -> str:
    cleaned = text
    cleaned = re.sub(r"<thinking>.*?</thinking>", " ", cleaned, flags=re.S | re.I)
    cleaned = re.sub(r"</?think(?:ing)?>", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"<reasoning>.*?</reasoning>", " ", cleaned, flags=re.S | re.I)
    cleaned = re.sub(r"</?reasoning(?:\.[^>]*)?>", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"^Reasoning:\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(
        r"^\s*(?:Reasoning|Thinking|Thought process|Chain of thought|思考过程|推理过程|分析过程|思路|内心OS)\s*[:：].*$",
        " ",
        cleaned,
        flags=re.I | re.M,
    )
    cleaned = re.sub(
        r"^\s*(?:我来想想|让我想想|先分析一下|下面是我的思考|以下是我的思考).*$",
        " ",
        cleaned,
        flags=re.M,
    )
    cleaned = re.sub(r"```(?:thinking|reasoning)[\s\S]*?```", " ", cleaned, flags=re.I)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        lines.append(line)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


def extract_openclaw_text(data: dict[str, Any]) -> str:
    output = data.get("output")
    if isinstance(output, list):
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            contents = item.get("content", [])
            if not isinstance(contents, list):
                continue
            for content in contents:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str) and text.strip():
                        texts.append(strip_reasoning_text(text))
        if texts:
            return "\n\n".join(text for text in texts if text)
    return "OpenClaw 没有返回可显示的文本。"


def extract_json_object(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}


def pick_bot() -> Bot | None:
    for bot in get_bots().values():
        if isinstance(bot, Bot):
            return bot
    return None


async def send_private_text(user_id: int, text: str) -> dict[str, Any]:
    bot = pick_bot()
    if bot is None:
        raise RuntimeError("No connected OneBot V11 bot is available.")
    return await bot.send_private_msg(user_id=user_id, message=format_qq_chat_text(text))


async def send_group_text(group_id: int, text: str) -> dict[str, Any]:
    bot = pick_bot()
    if bot is None:
        raise RuntimeError("No connected OneBot V11 bot is available.")
    return await bot.send_group_msg(group_id=group_id, message=format_qq_chat_text(text))


class OpenClawClient:
    def __init__(self, *, url: str, token: str, model: str, agent_id: str) -> None:
        self.url = url
        self.token = token
        self.model = model
        self.agent_id = agent_id

    def _request_text(self, req: urllib.request.Request, *, timeout: int) -> str:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return extract_openclaw_text(data)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenClaw HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenClaw request failed: {exc}") from exc

    async def ask(
        self,
        prompt: str,
        *,
        channel: str,
        agent_id: str | None = None,
        model: str | None = None,
        timeout: int = 90,
    ) -> str:
        effective_prompt = f"/reasoning off\n/think off\n{prompt}"
        body = json.dumps(
            {
                "model": model or self.model,
                "input": effective_prompt,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "x-openclaw-agent-id": agent_id or self.agent_id,
                "x-openclaw-message-channel": channel,
            },
        )
        return await asyncio.to_thread(self._request_text, req, timeout=timeout)
