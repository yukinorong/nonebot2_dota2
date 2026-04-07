from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .common import env, load_env_file
from .group_chat_store import get_recent_group_context
from .group_memory_store import retrieve_group_memories
from .llm_gateway import ask_main
from .openclaw_group_memory import ensure_group_openclaw_setup, group_chat_agent_id

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
TAVILY_API_KEY = env(ENV_VALUES, "TAVILY_API_KEY", "")
TAVILY_API_URL = env(ENV_VALUES, "TAVILY_API_URL", "https://api.tavily.com/search")
TAVILY_MAX_RESULTS = max(1, int(env(ENV_VALUES, "TAVILY_MAX_RESULTS", "5") or "5"))
GROUP_CHAT_CONTEXT_LIMIT = max(
    1, int(env(ENV_VALUES, "QQ_GROUP_CHAT_AGENT_CONTEXT_LIMIT", "20") or "20")
)


def message_channel(group_id: int, message_id: int, stage: str) -> str:
    safe_stage = (
        "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in stage.lower()).strip(
            "-"
        )
        or "msg"
    )
    return f"qq-g{group_id}-m{message_id}-{safe_stage}"


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
        if any(
            token in query
            for token in ("装备", "版本", "补丁", "更新", "item", "patch", "meta")
        ):
            payload["query"] = f"Dota 2 latest patch official patch notes {query}"
    return payload


def _tavily_search_sync(query: str) -> dict[str, Any]:
    if not TAVILY_API_KEY:
        raise RuntimeError("Tavily API key is not configured.")
    body = json.dumps(_build_tavily_payload(query), ensure_ascii=False).encode("utf-8")
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


def _format_news_text(text: str) -> str:
    compact = re.sub(r"[ \t]+", " ", text).strip()
    compact = re.sub(r"\s*(来源：)", r"\n\1", compact)
    compact = re.sub(r"\s*([1-9]\d*\.)\s*", r"\n\1 ", compact)
    compact = compact.lstrip("\n")
    lines = [line.strip() for line in compact.splitlines() if line.strip()]
    return "\n".join(lines).strip()


def _build_news_text_from_search(
    search_data: dict[str, Any], *, keyword: str = ""
) -> str:
    raw_results = search_data.get("results", [])
    if not isinstance(raw_results, list):
        raw_results = []
    blocks: list[str] = []
    links: list[str] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = re.sub(r"\\s+", " ", str(item.get("title", "")).strip())
        snippet = re.sub(
            r"\\s+", " ", str(item.get("content") or item.get("snippet") or "").strip()
        )
        url = str(item.get("url", "")).strip()
        if not title and not snippet:
            continue
        summary = snippet or title
        blocks.append(f"{title}：{summary}" if title and snippet else (title or summary))
        if url and url not in links:
            links.append(url)
        if len(blocks) >= 5:
            break
    if not blocks:
        answer = re.sub(r"\\s+", " ", str(search_data.get("answer", "")).strip())
        if answer:
            blocks.append(answer)
    if not blocks:
        return ""
    headline = f"今日{keyword.strip()}新闻" if keyword.strip() else "今日新闻"
    lines = [headline]
    for index, block in enumerate(blocks[:5], start=1):
        lines.append(f"{index}. {block}")
    if links:
        lines.append(f"来源：{' '.join(links[:3])}")
    return _format_news_text("\n".join(lines))


def _build_group_context_text(group_id: int) -> str:
    records = get_recent_group_context(group_id, max_items=GROUP_CHAT_CONTEXT_LIMIT)
    if not records:
        return "最近两小时群里还没有可用上下文。"
    lines: list[str] = []
    for item in records:
        timestamp = float(item["timestamp"])
        sender_name = str(item["sender_name"])
        text = str(item["text"])
        time_text = time.strftime("%H:%M", time.localtime(timestamp))
        lines.append(f"{time_text} {sender_name}: {text}")
    return "\n".join(lines)


def _build_structured_memory_text(group_id: int, content: str) -> str:
    matches = retrieve_group_memories(group_id, content, limit=3)
    if not matches:
        return "（无命中的长期记忆）"
    lines = [f"- {item['content']}" for item in matches if str(item.get("content", "")).strip()]
    return "\n".join(lines).strip() or "（无命中的长期记忆）"


def _build_group_chat_prompt(content: str, *, group_id: int) -> str:
    return (
        "你是一个真实 QQ 群里的机器人，这是该群唯一的自然语言 agent。\n"
        "你的任务是直接理解用户意图，并输出最终回复；不要先做显式分类，也不要输出你分到了什么路由。\n"
        "优先原则：\n"
        "1. 简单闲聊、打招呼、接梗、吐槽，直接回复，不要调用任何工具。\n"
        "2. 如果用户在问机器人支持什么功能、命令怎么用，优先用 read 查看 BOT_CAPABILITIES.md，再回答。\n"
        "3. 如果用户在问 Dota2，优先用 read 查看 DOTA_AGENT_GUIDE.md，再按需读取 DOTA_META_BRIEFS.json、DOTA_HERO_ALIASES.json、DOTA_HERO_BRIEFS.json。\n"
        "4. 只有当用户明确在问最新、今日、当前、最近、官网、补丁、职业/高分最新趋势、天气、新闻等实时信息时，才使用 web_search。\n"
        "5. 下面已经给出系统筛好的长期记忆命中项；如果为空，就不要凭空假设长期规则。\n"
        "6. 当前消息和最近聊天上下文优先于长期记忆；长期记忆只用于稳定背景，不要扩写成新事实。\n"
        "回答要求：\n"
        "- 只输出适合 QQ 聊天展示的纯文本，不要使用 Markdown，不要代码块。\n"
        "- 简短、自然、有群聊感，不要长篇说教。\n"
        "- 不要输出思考过程、不要解释你调用了什么工具。\n\n"
        f"最近群聊上下文：\n{_build_group_context_text(group_id)}\n\n"
        f"系统筛好的长期记忆命中项：\n{_build_structured_memory_text(group_id, content)}\n\n"
        f"当前用户消息：{content}"
    )


async def fetch_daily_news(*, channel: str, keyword: str = "") -> str:
    del channel
    date_text = time.strftime("%Y-%m-%d")
    query = f"{date_text} 今日新闻 热点"
    if keyword.strip():
        query = f"{date_text} {keyword.strip()} 新闻 热点"
    search_data = await _tavily_search(query)
    news_text = _build_news_text_from_search(search_data, keyword=keyword)
    if news_text:
        return news_text
    return "我刚刚没有查到足够可靠的今日新闻结果。"


async def dispatch_group_prompt(
    prompt: str, *, group_id: int, message_id: int
) -> str:
    ensure_group_openclaw_setup([group_id])
    response = await ask_main(
        _build_group_chat_prompt(prompt, group_id=group_id),
        channel=message_channel(group_id, message_id, "group-chat"),
        agent_id=group_chat_agent_id(group_id),
    )
    answer = response.strip()
    if not answer:
        answer = "我收到消息了，但这次没组织出有效回复。"
    return answer


async def route_group_prompt(
    prompt: str, *, group_id: int, message_id: int
) -> tuple[str, str]:
    return "group_chat", await dispatch_group_prompt(
        prompt, group_id=group_id, message_id=message_id
    )
