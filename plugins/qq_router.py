from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Awaitable, Callable

from .common import collapse_text, env, load_env_file
from .content_store import read_description
from .llm_gateway import ask_main, ask_router

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
TAVILY_API_KEY = env(ENV_VALUES, "TAVILY_API_KEY", "")
TAVILY_API_URL = env(ENV_VALUES, "TAVILY_API_URL", "https://api.tavily.com/search")
TAVILY_MAX_RESULTS = max(1, int(env(ENV_VALUES, "TAVILY_MAX_RESULTS", "5") or "5"))

CLASS_LABELS = {
    "version_query",
    "bot_abuse",
    "web_answerable",
    "smalltalk",
}


def message_channel(group_id: int, message_id: int, stage: str) -> str:
    safe_stage = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in stage.lower()).strip("-") or "msg"
    return f"qq-g{group_id}-m{message_id}-{safe_stage}"


async def _classify_message(content: str, *, channel: str) -> str:
    prompt = (
        "你是一个严格的消息分类器。\n"
        "请把下面这条发给 QQ 机器人的群消息，严格分类为且仅输出以下四个标签之一：\n"
        "version_query\nbot_abuse\nweb_answerable\nsmalltalk\n\n"
        "分类规则：\n"
        "1. 只要用户是在问机器人当前已经有什么功能、支持什么能力、版本特性、现在会做什么，一律归类为 version_query。\n"
        "2. 骂机器人、嘲讽机器人、攻击机器人本身，归类为 bot_abuse。\n"
        "3. 明显需要联网查实时信息、资料、天气、新闻、官网信息的问题，归类为 web_answerable。\n"
        "4. 其他普通闲聊归类为 smalltalk。\n\n"
        "例子：\n"
        "你有什么功能 -> version_query\n"
        "你现在支持哪些功能 -> version_query\n"
        "北京今天天气怎么样 -> web_answerable\n"
        "你可真蠢 -> bot_abuse\n"
        "你好啊 -> smalltalk\n\n"
        "只输出标签本身，不要解释。\n\n"
        f"消息：{content}"
    )
    raw = collapse_text(await ask_router(prompt, channel=channel))
    for label in CLASS_LABELS:
        if raw == label:
            return label
    lowered = raw.lower()
    for label in CLASS_LABELS:
        if label in lowered:
            return label
    return "smalltalk"


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


async def _handle_version_query(content: str, *, channel: str) -> str:
    return read_description()


async def _handle_bot_abuse(content: str, *, channel: str) -> str:
    prompt = (
        "你是一个 QQ 机器人。用户正在骂你或批评你。\n"
        "请直接输出一句最终回怼原文。\n"
        "要求：简短、阴阳、允许一点刻薄和嘲讽，但不要现实侮辱、不要仇恨、不要长篇输出。\n"
        "可以带 1 个恰到好处的 emoji。\n"
        "不要解释，不要复述用户消息，不要分析。\n\n"
        f"用户消息：{content}"
    )
    return collapse_text(await ask_router(prompt, channel=channel))


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
        "要求：中文简短回答，1 到 3 句，优先给结论。\n\n"
        f"用户问题：{content}\n\n"
        f"Tavily 搜索结果：\n{search_context}"
    )
    return collapse_text(await ask_main(prompt, channel=channel))


async def _handle_smalltalk(content: str, *, channel: str) -> str:
    prompt = (
        "你是一个群聊机器人。请对下面这句闲聊做一个简短中文回复。\n"
        "要求：轻松自然，1 句即可，可以带 1 到 2 个恰到好处的 emoji。\n"
        "不要复述用户消息。\n\n"
        f"用户消息：{content}"
    )
    return collapse_text(await ask_router(prompt, channel=channel))


RouteHandler = Callable[..., Awaitable[str]]
ROUTE_HANDLERS: dict[str, RouteHandler] = {
    "version_query": _handle_version_query,
    "bot_abuse": _handle_bot_abuse,
    "web_answerable": _handle_web_answerable,
    "smalltalk": _handle_smalltalk,
}


async def route_group_prompt(prompt: str, *, group_id: int, message_id: int) -> tuple[str, str]:
    route = await _classify_message(prompt, channel=message_channel(group_id, message_id, "classifier"))
    handler = ROUTE_HANDLERS.get(route, _handle_smalltalk)
    answer = collapse_text(await handler(prompt, channel=message_channel(group_id, message_id, route)))
    if not answer:
        answer = "我收到消息了，但这次没组织出有效回复。"
    return route, answer
