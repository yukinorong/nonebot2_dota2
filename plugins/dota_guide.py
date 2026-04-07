from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nonebot import logger

from .common import env, load_env_file
from .dota_guide_store import (
    get_guide_sources,
    latest_fetched_at,
    parse_dota_version,
    prune_expired_guide_sources,
    save_guide_source,
)
from .dota_knowledge_store import (
    load_hero_aliases,
    load_hero_briefs,
    load_meta_briefs,
    normalize_lookup_text,
)
from .group_chat_store import get_recent_group_context
from .llm_gateway import ask_main

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
TAVILY_API_KEY = env(ENV_VALUES, "TAVILY_API_KEY", "")
TAVILY_API_URL = env(ENV_VALUES, "TAVILY_API_URL", "https://api.tavily.com/search")
GUIDE_REFRESH_HOURS = max(1, int(env(ENV_VALUES, "DOTA2_GUIDE_REFRESH_HOURS", "6")))
GUIDE_TAVILY_RESULTS = max(3, int(env(ENV_VALUES, "DOTA2_GUIDE_TAVILY_RESULTS", "4")))


def message_channel(group_id: int, stage: str) -> str:
    safe_stage = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in stage.lower()).strip("-") or "guide"
    return f"qq-g{group_id}-{safe_stage}-{int(time.time())}"


def _ascii_terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9']+", text.lower()) if term}


def _alias_matches(alias: str, *, query: str, normalized_query: str, ascii_terms: set[str]) -> bool:
    if not alias:
        return False
    if re.fullmatch(r"[a-z0-9']+", alias):
        return alias in ascii_terms
    return alias in normalized_query or alias in query.lower()


def resolve_hero_for_guide(query: str) -> dict[str, Any]:
    normalized_query = normalize_lookup_text(query)
    ascii_terms = _ascii_terms(query)
    hero_aliases = load_hero_aliases()
    hero_briefs = load_hero_briefs()
    hero_ids: list[int] = []
    matched_aliases: list[str] = []
    for alias, hero_id in hero_aliases.items():
        if _alias_matches(alias, query=query, normalized_query=normalized_query, ascii_terms=ascii_terms):
            if hero_id not in hero_ids:
                hero_ids.append(hero_id)
                matched_aliases.append(alias)
    if len(hero_ids) == 1:
        hero_id = hero_ids[0]
        brief = hero_briefs.get(str(hero_id), {})
        return {
            "resolved": True,
            "hero_id": hero_id,
            "hero_name": str(brief.get("display_name") or brief.get("localized_name") or hero_id),
            "matched_aliases": matched_aliases,
        }
    if len(hero_ids) > 1:
        names = [str(hero_briefs.get(str(hero_id), {}).get("display_name") or hero_id) for hero_id in hero_ids[:5]]
        return {"resolved": False, "reason": "ambiguous", "candidates": names}
    return {"resolved": False, "reason": "not_found", "candidates": []}


def _extract_version(text: str) -> str:
    parsed = parse_dota_version(text)
    return str(parsed.get("raw") or "")


def _tavily_search_sync(query: str, *, include_domains: list[str] | None = None) -> dict[str, Any]:
    if not TAVILY_API_KEY:
        raise RuntimeError("Tavily API key is not configured.")
    payload: dict[str, Any] = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": GUIDE_TAVILY_RESULTS,
        "include_answer": True,
        "search_depth": "advanced",
        "include_raw_content": False,
        "topic": "general",
    }
    if include_domains:
        payload["include_domains"] = include_domains
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        TAVILY_API_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}


async def _tavily_search(query: str, *, include_domains: list[str] | None = None) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_tavily_search_sync, query, include_domains=include_domains)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Tavily HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Tavily request failed: {exc}") from exc


def _search_items_to_sources(
    *,
    hero_id: int,
    topic_type: str,
    source_type: str,
    search_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    answer = str(search_data.get("answer", "")).strip()
    current_version = _extract_version(answer)
    raw_results = search_data.get("results", [])
    if isinstance(raw_results, list):
        for item in raw_results[:GUIDE_TAVILY_RESULTS]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            content = str(item.get("content") or item.get("snippet") or "").strip()
            text = "\n".join(part for part in [title, content] if part).strip()
            if not text:
                continue
            item_version = _extract_version(f"{title}\n{content}\n{answer}") or current_version
            rows.append(
                {
                    "hero_id": hero_id,
                    "topic_type": topic_type,
                    "source_type": source_type,
                    "source_url": url,
                    "source_title": title,
                    "content_text": text,
                    "game_version": item_version,
                    "metadata": {"answer": answer} if answer else {},
                }
            )
    if answer:
        rows.append(
            {
                "hero_id": hero_id,
                "topic_type": topic_type,
                "source_type": source_type,
                "source_url": "",
                "source_title": f"{topic_type} summary",
                "content_text": answer,
                "game_version": current_version,
                "metadata": {"summary_only": True},
            }
        )
    return rows, current_version


async def _refresh_guide_sources(hero_id: int, hero_name: str) -> str:
    official_data = await _tavily_search(
        f"Dota 2 latest official patch notes {hero_name}",
        include_domains=["dota2.com", "www.dota2.com"],
    )
    liquipedia_data = await _tavily_search(
        f"Liquipedia Dota 2 {hero_name} current patch pro matches meta",
        include_domains=["liquipedia.net"],
    )
    high_mmr_data = await _tavily_search(
        f"Dota 2 {hero_name} high mmr build current patch guide",
        include_domains=["dota2protracker.com", "www.dota2protracker.com", "liquipedia.net"],
    )

    official_rows, current_version = _search_items_to_sources(
        hero_id=hero_id,
        topic_type="patch",
        source_type="official",
        search_data=official_data,
    )
    liquipedia_rows, liquipedia_version = _search_items_to_sources(
        hero_id=hero_id,
        topic_type="pro_meta",
        source_type="liquipedia",
        search_data=liquipedia_data,
    )
    high_mmr_rows, high_mmr_version = _search_items_to_sources(
        hero_id=hero_id,
        topic_type="high_mmr",
        source_type="high_mmr",
        search_data=high_mmr_data,
    )

    chosen_version = current_version or liquipedia_version or high_mmr_version
    for row in [*official_rows, *liquipedia_rows, *high_mmr_rows]:
        if not row.get("game_version"):
            row["game_version"] = chosen_version
        save_guide_source(**row)
    return chosen_version


def _group_context_text(group_id: int) -> str:
    records = get_recent_group_context(group_id, max_items=20)
    if not records:
        return "最近群里没有额外上下文。"
    lines: list[str] = []
    for item in records[-10:]:
        text = str(item.get("text") or "").strip()
        sender = str(item.get("sender_name") or "群友")
        if text:
            lines.append(f"{sender}: {text}")
    return "\n".join(lines) if lines else "最近群里没有额外上下文。"


def _needs_refresh(hero_id: int) -> bool:
    latest = latest_fetched_at(hero_id)
    if not latest:
        return True
    try:
        latest_dt = datetime.fromisoformat(latest)
    except ValueError:
        return True
    return latest_dt <= datetime.now(UTC) - timedelta(hours=GUIDE_REFRESH_HOURS)


def _build_knowledge_lines(hero_id: int) -> list[str]:
    hero_briefs = load_hero_briefs()
    meta_briefs = load_meta_briefs()
    brief = hero_briefs.get(str(hero_id), {})
    if not isinstance(brief, dict):
        return []
    lines = [
        f"英雄：{brief.get('display_name', brief.get('localized_name', hero_id))}",
        f"定位：{brief.get('roles_text', '未知')}",
        f"主属性：{brief.get('primary_attr', '未知')}，攻击类型：{brief.get('attack_type', '未知')}",
    ]
    if brief.get("pub_win_rate") is not None:
        lines.append(f"综合胜率：{brief['pub_win_rate']:.2f}%")
    if brief.get("power_spike"):
        lines.append(f"强势期：{brief['power_spike']}")
    if brief.get("popular_items_text"):
        lines.append(f"主流出装：{brief['popular_items_text']}")
    if brief.get("matchup_text"):
        lines.append(f"对位特点：{brief['matchup_text']}")
    top_pub = meta_briefs.get("top_pub_winrate") or []
    if isinstance(top_pub, list):
        for item in top_pub[:15]:
            if isinstance(item, dict) and int(item.get("hero_id", 0) or 0) == hero_id:
                lines.append("这个英雄当前处在公开局高胜率梯队。")
                break
    return lines


def build_hero_guide_context(hero_id: int, *, refresh: bool = False) -> dict[str, Any]:
    prune_expired_guide_sources()
    hero_briefs = load_hero_briefs()
    brief = hero_briefs.get(str(hero_id), {})
    if not isinstance(brief, dict):
        return {}
    hero_name = str(brief.get("display_name") or brief.get("localized_name") or hero_id)
    current_version = ""
    if refresh or _needs_refresh(hero_id):
        try:
            current_version = asyncio.run(_refresh_guide_sources(hero_id, hero_name))
        except RuntimeError:
            raise
        except Exception:
            logger.exception("Dota guide refresh failed for hero_id={}", hero_id)
    rows = get_guide_sources(hero_id, current_version=current_version)
    if not current_version:
        for row in rows:
            version = str(row.get("game_version") or "").strip()
            if version:
                current_version = version
                break
    return {
        "hero_id": hero_id,
        "hero_name": hero_name,
        "current_version": current_version,
        "knowledge_lines": _build_knowledge_lines(hero_id),
        "sources": rows,
    }


async def build_hero_guide_text(hero_query: str, *, group_id: int) -> str:
    resolved = resolve_hero_for_guide(hero_query)
    if not resolved.get("resolved"):
        if resolved.get("reason") == "ambiguous":
            candidates = "、".join(resolved.get("candidates") or [])
            return f"我识别到多个可能的英雄，你再说明确一点。候选：{candidates}"
        return "我还没识别出你具体想问哪个英雄，你把英雄名说明确一点。"
    hero_id = int(resolved["hero_id"])
    context = await asyncio.to_thread(build_hero_guide_context, hero_id, refresh=False)
    if not context.get("sources"):
        context = await asyncio.to_thread(build_hero_guide_context, hero_id, refresh=True)
    sources = context.get("sources") or []
    knowledge_lines = context.get("knowledge_lines") or []
    source_blocks: list[str] = []
    for row in sources[:8]:
        block = [
            f"来源类型：{row.get('source_type')}",
            f"主题：{row.get('topic_type')}",
            f"版本：{row.get('game_version') or '未知'}",
            f"权重：{row.get('effective_weight')}",
        ]
        if row.get("source_title"):
            block.append(f"标题：{row['source_title']}")
        if row.get("source_url"):
            block.append(f"链接：{row['source_url']}")
        block.append(f"内容：{row.get('content_text', '')}")
        source_blocks.append("\n".join(block))
    if not knowledge_lines and not source_blocks:
        return "这个英雄当前可用的本地资料还不够，稍后再试。"
    prompt = (
        "你是一个非常专业的 Dota2 教练，正在写当前版本的英雄攻略。\n"
        "请优先依据给出的结构化知识和外部来源素材下结论，不要编造，不要偷换逻辑。\n"
        "如果不同来源冲突，优先相信高权重、更新、更官方的内容。\n"
        "请解释为什么这样玩，而不是只罗列结论。\n"
        "输出结构要清晰，至少覆盖：版本定位、分路职责、技能节奏、出装分支、对线重点、团战职责、常见误区。\n"
        "群聊上下文只用于理解用户语气，不影响专业结论。\n"
        "只输出适合 QQ 聊天展示的纯文本，不要使用 Markdown，不要代码块。\n"
        "严格按下面格式输出，并保留这些换行和空行：\n"
        f"{context.get('hero_name')} 当前版本攻略\n\n"
        "版本定位：用 2 到 3 句话。\n\n"
        "分路职责：\n"
        "1. ...\n"
        "2. ...\n\n"
        "技能节奏：\n"
        "1. ...\n"
        "2. ...\n\n"
        "出装分支：\n"
        "1. 核心出装：...\n"
        "2. 顺风变化：...\n"
        "3. 逆风变化：...\n\n"
        "对线重点：\n"
        "1. ...\n"
        "2. ...\n\n"
        "团战职责：\n"
        "1. ...\n"
        "2. ...\n\n"
        "常见误区：\n"
        "1. ...\n"
        "2. ...\n"
        "不要额外输出来源列表、免责声明或总结尾巴。\n"
        "不要输出思考过程，只保留回答正文。\n\n"
        f"用户问题：{hero_query}\n"
        f"目标英雄：{context.get('hero_name')}\n"
        f"当前版本：{context.get('current_version') or '未知'}\n\n"
        f"本地结构化知识：\n{chr(10).join(knowledge_lines) or '无'}\n\n"
        f"外部攻略素材：\n{chr(10).join(source_blocks) or '无'}\n\n"
        f"最近群聊上下文：\n{_group_context_text(group_id)}"
    )
    return await ask_main(prompt, channel=message_channel(group_id, "dota-guide"))
