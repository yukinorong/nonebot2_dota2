from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .common import env, load_env_file
from .dota_knowledge_store import (
    load_hero_aliases,
    load_hero_briefs,
    load_item_aliases,
    load_meta_briefs,
    normalize_lookup_text,
)
from .group_chat_store import get_recent_group_context
from .llm_gateway import ask_main

DOTA_QUERY_KEYWORDS = (
    "dota",
    "刀塔",
    "英雄",
    "出装",
    "对线",
    "克制",
    "胜率",
    "版本",
    "补丁",
    "阵容",
    "中单",
    "一号位",
    "二号位",
    "三号位",
    "四号位",
    "五号位",
)

HERO_OVERVIEW_KEYWORDS = ("怎么玩", "强不强", "定位", "适合", "介绍", "思路")
HERO_BUILD_KEYWORDS = ("出装", "怎么出", "带什么装备", "build")
HERO_MATCHUP_KEYWORDS = ("怕谁", "克制", "好打", "难打", "打不过")
PATCH_KEYWORDS = ("补丁", "版本", "改动", "更新", "patch")
META_KEYWORDS = ("meta", "强势", "热门", "胜率", "上分")
TERM_KEYWORDS = ("什么意思", "是啥", "啥意思", "解释一下")
LINEUP_KEYWORDS = ("阵容", "bp", "选人", "搭配")
MATCH_COMMENTARY_KEYWORDS = ("评价", "怎么看", "这把", "这局")

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / '.env'
ENV_VALUES = load_env_file(ENV_FILE)
DOTA2_KNOWLEDGE_USE_TAVILY_FOR_PATCH = env(ENV_VALUES, 'DOTA2_KNOWLEDGE_USE_TAVILY_FOR_PATCH', 'true').lower() == 'true'


def message_channel(group_id: int, message_id: int, stage: str) -> str:
    safe_stage = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in stage.lower()).strip("-") or "msg"
    return f"qq-g{group_id}-m{message_id}-{safe_stage}"


def _ascii_terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9']+", text.lower()) if term}


def _alias_matches(alias: str, *, query: str, normalized_query: str, ascii_terms: set[str]) -> bool:
    if not alias:
        return False
    if re.fullmatch(r"[a-z0-9']+", alias):
        return alias in ascii_terms
    return alias in normalized_query or alias in query.lower()


def _build_group_context_text(group_id: int) -> str:
    records = get_recent_group_context(group_id)
    if not records:
        return "最近两小时群里没有可用上下文。"
    lines: list[str] = []
    for item in records:
        timestamp = float(item["timestamp"])
        sender_name = str(item["sender_name"])
        text = str(item["text"])
        time_text = time.strftime("%H:%M", time.localtime(timestamp))
        lines.append(f"{time_text} {sender_name}: {text}")
    return "\n".join(lines)


def resolve_dota_entities(query: str) -> dict[str, Any]:
    normalized = normalize_lookup_text(query)
    ascii_terms = _ascii_terms(query)
    hero_aliases = load_hero_aliases()
    item_aliases = load_item_aliases()

    matched_hero_ids: list[int] = []
    matched_item_ids: list[int] = []
    matched_aliases: list[str] = []

    for alias, hero_id in hero_aliases.items():
        if _alias_matches(alias, query=query, normalized_query=normalized, ascii_terms=ascii_terms) and hero_id not in matched_hero_ids:
            matched_hero_ids.append(hero_id)
            matched_aliases.append(alias)
    for alias, item_id in item_aliases.items():
        if _alias_matches(alias, query=query, normalized_query=normalized, ascii_terms=ascii_terms) and item_id not in matched_item_ids:
            matched_item_ids.append(item_id)
            matched_aliases.append(alias)

    confidence = 0.0
    if matched_hero_ids or matched_item_ids:
        confidence = 0.85
        if len(matched_hero_ids) + len(matched_item_ids) > 1:
            confidence = 0.7

    return {
        "normalized_query": normalized,
        "hero_ids": matched_hero_ids,
        "item_ids": matched_item_ids,
        "matched_aliases": matched_aliases,
        "confidence": confidence,
    }


def classify_dota_query_intent(query: str, entities: dict[str, Any]) -> str:
    lowered = query.lower()
    if any(keyword in query or keyword in lowered for keyword in PATCH_KEYWORDS):
        return "patch_query"
    if any(keyword in query or keyword in lowered for keyword in HERO_BUILD_KEYWORDS):
        return "hero_build"
    if any(keyword in query or keyword in lowered for keyword in HERO_MATCHUP_KEYWORDS):
        return "hero_matchup"
    if any(keyword in query or keyword in lowered for keyword in META_KEYWORDS):
        return "meta_query"
    if any(keyword in query or keyword in lowered for keyword in TERM_KEYWORDS):
        return "term_explain"
    if any(keyword in query or keyword in lowered for keyword in LINEUP_KEYWORDS):
        return "draft_or_lineup"
    if any(keyword in query or keyword in lowered for keyword in MATCH_COMMENTARY_KEYWORDS):
        return "match_commentary"
    if entities.get("hero_ids") or entities.get("item_ids"):
        return "hero_overview"
    return "general_dota_chat"


def _hero_brief_lines(hero_id: int, hero_briefs: dict[str, Any]) -> list[str]:
    brief = hero_briefs.get(str(hero_id))
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
    return lines


def _meta_lines(meta_briefs: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    top_pub = meta_briefs.get("top_pub_winrate") or []
    if isinstance(top_pub, list) and top_pub:
        summary = "、".join(str(item.get("display_name")) for item in top_pub[:6] if isinstance(item, dict))
        if summary:
            lines.append(f"当前高胜率英雄：{summary}")
    return lines


def _build_dota_knowledge_context(entities: dict[str, Any], intent: str) -> str:
    hero_briefs = load_hero_briefs()
    meta_briefs = load_meta_briefs()
    lines = [f"意图：{intent}"]
    for hero_id in entities.get("hero_ids", [])[:3]:
        lines.extend(_hero_brief_lines(hero_id, hero_briefs))
    if intent == "meta_query":
        lines.extend(_meta_lines(meta_briefs))
    if entities.get("item_ids"):
        lines.append("涉及装备ID：" + ", ".join(str(item_id) for item_id in entities["item_ids"][:5]))
    return "\n".join(line for line in lines if line).strip()


def _build_patch_context(search_data: dict[str, Any]) -> str:
    lines: list[str] = []
    answer = str(search_data.get("answer", "")).strip()
    if answer:
        lines.append(f"补丁摘要：{answer}")
    raw_results = search_data.get("results", [])
    if isinstance(raw_results, list):
        for item in raw_results[:3]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            snippet = str(item.get("content") or item.get("snippet") or "").strip()
            block: list[str] = []
            if title:
                block.append(f"标题：{title}")
            if url:
                block.append(f"链接：{url}")
            if snippet:
                block.append(f"摘要：{snippet}")
            if block:
                lines.append("\n".join(block))
    return "\n\n".join(lines).strip()


async def answer_dota_query(
    query: str,
    *,
    group_id: int,
    message_id: int,
    tavily_search: Any | None = None,
) -> str:
    hero_briefs = load_hero_briefs()
    meta_briefs = load_meta_briefs()
    if not hero_briefs and not meta_briefs:
        return "本地 Dota2 知识库还没准备好，先同步一次知识库再来问我。"

    entities = resolve_dota_entities(query)
    intent = classify_dota_query_intent(query, entities)

    if entities["confidence"] < 0.5 and intent in {"hero_overview", "hero_build", "hero_matchup"}:
        return "我还没识别出你具体在问哪个英雄或装备，你可以把名字说得更明确一点。"

    knowledge_context = _build_dota_knowledge_context(entities, intent)
    if not knowledge_context:
        return "我现在本地 Dota2 知识还不够完整，这个问题你可以换个更具体的问法。"

    patch_context = ""
    if intent == "patch_query" and DOTA2_KNOWLEDGE_USE_TAVILY_FOR_PATCH and callable(tavily_search):
        search_data = await tavily_search(f"Dota 2 官方 最新 补丁 更新 {query}")
        patch_context = _build_patch_context(search_data)

    prompt = (
        "你是一个懂 Dota2 的群聊机器人。\n"
        "请优先基于下面提供的 Dota2 本地知识回答问题。\n"
        "群聊上下文只作为辅助，用于理解用户语气、承接上文和补充群内话题背景，不能影响专业结论。\n"
        "如果群聊上下文和知识冲突，以知识为准。\n"
        "回答要专业、清晰，但保留一点群聊口吻。\n"
        "不要输出思考过程，只保留回答正文。\n"
        "尽量控制在 3 到 6 句。\n\n"
        f"用户问题：{query}\n\n"
        f"Dota2 本地知识：\n{knowledge_context}\n\n"
        f"补充联网信息（仅在涉及最新补丁时参考）：\n{patch_context or '无'}\n\n"
        f"最近群聊上下文（仅辅助）：\n{_build_group_context_text(group_id)}"
    )
    return await ask_main(prompt, channel=message_channel(group_id, message_id, "dota-query"))
