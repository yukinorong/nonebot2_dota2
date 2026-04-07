from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable

from nonebot import logger

from .common import env, load_env_file
from .content_store import read_description
from .group_memory_store import sync_group_memory_items

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)

OPENCLAW_CONFIG_PATH = Path(env(ENV_VALUES, "OPENCLAW_CONFIG_PATH", "/root/.openclaw/openclaw.json"))
GROUP_OPENCLAW_WORKSPACE_ROOT = Path(
    env(
        ENV_VALUES,
        "QQ_GROUP_OPENCLAW_WORKSPACE_ROOT",
        str(BASE_DIR / "data" / "openclaw_group_memory"),
    )
)
LEGACY_GROUP_MEMORY_DIR = Path(env(ENV_VALUES, "QQ_GROUP_MEMORY_DIR", str(BASE_DIR / "data" / "group_memory")))

MEMORY_ITEMS_FILENAME = ".memory-items.json"
MEMORY_MARKDOWN_FILENAME = "MEMORY.md"
BOT_CAPABILITIES_FILENAME = "BOT_CAPABILITIES.md"
DOTA_META_BRIEFS_FILENAME = "DOTA_META_BRIEFS.json"
DOTA_HERO_BRIEFS_FILENAME = "DOTA_HERO_BRIEFS.json"
DOTA_HERO_ALIASES_FILENAME = "DOTA_HERO_ALIASES.json"
DOTA_AGENT_GUIDE_FILENAME = "DOTA_AGENT_GUIDE.md"
ITEM_CATEGORIES = ("bot_preference", "group_lexicon", "durable_context")
_CATEGORY_TITLES = {
    "bot_preference": "机器人偏好",
    "group_lexicon": "群内词典",
    "durable_context": "长期背景",
}
_TYPE_DEFAULT_SUBJECT = {
    "bot_preference": "robot",
    "group_lexicon": "group",
    "durable_context": "group",
}
_PRIORITY_ORDER = ("high", "medium", "low")
_DOTA_KNOWLEDGE_DIR = BASE_DIR / "data" / "dota_knowledge"
_DOTA_DERIVED_DIR = _DOTA_KNOWLEDGE_DIR / "derived"
_BOT_CAPABILITIES_HEADER = "# 机器人能力说明\n\n"
_IGNORABLE_FACTS = {
    "无值得长期保留的信息",
    "暂无值得长期保留的信息",
    "没有值得长期保留的信息",
    "暂无长期记忆",
    "当前没有整理出的长期记忆",
}


def group_chat_agent_id(group_id: int) -> str:
    return f"qq_group_{group_id}"


def group_memory_agent_id(group_id: int) -> str:
    return f"qq_group_{group_id}_memory"


def group_workspace_dir(group_id: int) -> Path:
    return GROUP_OPENCLAW_WORKSPACE_ROOT / str(group_id)


def memory_items_path(group_id: int) -> Path:
    return group_workspace_dir(group_id) / MEMORY_ITEMS_FILENAME


def memory_markdown_path(group_id: int) -> Path:
    return group_workspace_dir(group_id) / MEMORY_MARKDOWN_FILENAME


def legacy_group_memory_path(group_id: int) -> Path:
    return LEGACY_GROUP_MEMORY_DIR / f"{group_id}.memory.md"


def ensure_group_workspace(group_id: int) -> Path:
    workspace = group_workspace_dir(group_id)
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _write_workspace_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _copy_workspace_file_if_exists(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    _write_workspace_file(destination, source.read_text(encoding="utf-8"))


def sync_group_workspace_support_files(group_id: int) -> None:
    workspace = ensure_group_workspace(group_id)
    capabilities_text = read_description().strip()
    if capabilities_text:
        _write_workspace_file(
            workspace / BOT_CAPABILITIES_FILENAME,
            f"{_BOT_CAPABILITIES_HEADER}{capabilities_text}\n",
        )

    _copy_workspace_file_if_exists(
        _DOTA_DERIVED_DIR / "meta_briefs.json",
        workspace / DOTA_META_BRIEFS_FILENAME,
    )
    _copy_workspace_file_if_exists(
        _DOTA_DERIVED_DIR / "hero_briefs.json",
        workspace / DOTA_HERO_BRIEFS_FILENAME,
    )
    _copy_workspace_file_if_exists(
        _DOTA_KNOWLEDGE_DIR / "hero_aliases.json",
        workspace / DOTA_HERO_ALIASES_FILENAME,
    )

    dota_guide = (
        "# Dota2 资料索引\n\n"
        "- 如果用户在问 Dota2，用 `read` 按需查看这些文件，不要一次性全读。\n"
        f"- `{DOTA_META_BRIEFS_FILENAME}`：当前版本整体趋势、热门与高胜率英雄摘要。\n"
        f"- `{DOTA_HERO_ALIASES_FILENAME}`：英雄别名和常见叫法映射。\n"
        f"- `{DOTA_HERO_BRIEFS_FILENAME}`：按英雄整理的本地知识摘要，包含定位、强势期、热门出装、对位特点。\n"
        "- 如果用户明确在问最新版本、刚更新了什么、职业/高分局最新趋势，再使用 web_search 补充最新资料。\n"
    )
    _write_workspace_file(workspace / DOTA_AGENT_GUIDE_FILENAME, dota_guide)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.strip())


def _normalize_aliases(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    aliases: list[str] = []
    seen: set[str] = set()
    for alias in value:
        normalized = _normalize_whitespace(str(alias))
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(normalized)
    return aliases


def _make_item_id(item_type: str, subject: str, canonical: str) -> str:
    seed = f"{item_type}|{subject}|{canonical}".encode("utf-8")
    return hashlib.md5(seed).hexdigest()


def _normalize_item(item: dict[str, Any]) -> dict[str, Any] | None:
    item_type = _normalize_whitespace(str(item.get("type") or item.get("category") or "")).lower()
    content = _normalize_whitespace(str(item.get("content") or item.get("fact") or ""))
    if item_type not in ITEM_CATEGORIES or not content:
        return None
    if content in _IGNORABLE_FACTS:
        return None
    subject = _normalize_whitespace(str(item.get("subject") or _TYPE_DEFAULT_SUBJECT[item_type]))
    canonical = _normalize_whitespace(str(item.get("canonical") or content))
    aliases = _normalize_aliases(item.get("aliases", []))
    priority = _normalize_whitespace(str(item.get("priority") or "medium")).lower()
    if priority not in _PRIORITY_ORDER:
        priority = "medium"
    if len(content) > 160:
        content = content[:157].rstrip() + "..."
    if len(canonical) > 80:
        canonical = canonical[:77].rstrip() + "..."
    if len(subject) > 80:
        subject = subject[:77].rstrip() + "..."
    if not subject or not canonical:
        return None
    updated_at_raw = item.get("updated_at")
    try:
        updated_at = int(updated_at_raw) if updated_at_raw is not None else int(time.time())
    except (TypeError, ValueError):
        updated_at = int(time.time())
    return {
        "id": _normalize_whitespace(str(item.get("id") or _make_item_id(item_type, subject, canonical))),
        "type": item_type,
        "subject": subject,
        "canonical": canonical,
        "aliases": aliases,
        "content": content,
        "priority": priority,
        "enabled": bool(item.get("enabled", True)),
        "updated_at": updated_at,
    }


def normalize_memory_items(items: Iterable[dict[str, Any]] | Any) -> list[dict[str, Any]]:
    if not isinstance(items, Iterable) or isinstance(items, (str, bytes, dict)):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized_item = _normalize_item(item)
        if normalized_item is None:
            continue
        key = str(normalized_item["id"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_item)
    normalized.sort(
        key=lambda item: (
            ITEM_CATEGORIES.index(item["type"]),
            _PRIORITY_ORDER.index(item["priority"]),
            item["canonical"],
        )
    )
    return normalized


def read_memory_items(group_id: int) -> list[dict[str, Any]]:
    path = memory_items_path(group_id)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Invalid memory items JSON for group {}", group_id)
        return []
    return normalize_memory_items(payload)


def write_memory_items(group_id: int, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = normalize_memory_items(items)
    ensure_group_workspace(group_id)
    memory_items_path(group_id).write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    sync_group_memory_items(group_id, normalized)
    return normalized


def render_memory_markdown(items: list[dict[str, Any]]) -> str:
    normalized = normalize_memory_items(items)
    if not normalized:
        return ""
    grouped: dict[str, list[str]] = {category: [] for category in ITEM_CATEGORIES}
    for item in normalized:
        grouped[item["type"]].append(item["content"])

    blocks: list[str] = []
    for category in ITEM_CATEGORIES:
        facts = grouped[category]
        if not facts:
            continue
        blocks.append(f"# {_CATEGORY_TITLES[category]}")
        blocks.extend(f"- {fact}" for fact in facts)
        blocks.append("")
    return "\n".join(blocks).strip() + "\n"


def write_memory_markdown(group_id: int, items: list[dict[str, Any]]) -> str:
    rendered = render_memory_markdown(items)
    if not rendered:
        return ""
    ensure_group_workspace(group_id)
    memory_markdown_path(group_id).write_text(rendered, encoding="utf-8")
    return rendered


def read_memory_markdown(group_id: int) -> str:
    path = memory_markdown_path(group_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _classify_legacy_fact(line: str) -> str:
    lowered = line.lower()
    if any(token in line for token in ("机器人", "回复", "不要", "需要", "记住", "命令", "格式")):
        return "bot_preference"
    if any(token in line for token in ("叫", "外号", "昵称", "指的是", "就是")):
        return "group_lexicon"
    if any(token in lowered for token in ("dota", "刀塔", "群里", "主要聊")):
        return "durable_context"
    return "durable_context"


def bootstrap_group_memory(group_id: int) -> None:
    ensure_group_workspace(group_id)
    initial_items = read_memory_items(group_id)
    if not initial_items:
        legacy_path = legacy_group_memory_path(group_id)
        if legacy_path.exists():
            legacy_lines = [
                re.sub(r"^\s*[-*+]\s*", "", raw_line).strip()
                for raw_line in legacy_path.read_text(encoding="utf-8").splitlines()
            ]
            legacy_items = [{"type": _classify_legacy_fact(line), "content": line} for line in legacy_lines if line]
            initial_items = normalize_memory_items(legacy_items)
        else:
            current_markdown = read_memory_markdown(group_id)
            if current_markdown:
                legacy_lines = [
                    re.sub(r"^\s*[-*+]\s*", "", raw_line).strip()
                    for raw_line in current_markdown.splitlines()
                    if raw_line.strip().startswith("-")
                ]
                initial_items = normalize_memory_items(
                    [{"type": _classify_legacy_fact(line), "content": line} for line in legacy_lines]
                )

    if initial_items:
        write_memory_items(group_id, initial_items)
        write_memory_markdown(group_id, initial_items)
    sync_group_workspace_support_files(group_id)


def _build_chat_agent_entry(group_id: int, template_agent: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": group_chat_agent_id(group_id),
        "workspace": str(group_workspace_dir(group_id)),
        "model": template_agent.get("model", {}),
        "skills": [],
        "tools": {
            "allow": ["read", "web_search", "session_status"],
            "deny": ["write", "edit", "apply_patch", "exec", "process"],
            "fs": {"workspaceOnly": True},
        },
    }


def _build_memory_agent_entry(group_id: int, template_agent: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": group_memory_agent_id(group_id),
        "workspace": str(group_workspace_dir(group_id)),
        "model": template_agent.get("model", {}),
        "skills": [],
        "tools": {
            "allow": ["group:fs", "group:memory", "session_status"],
            "deny": ["exec", "process"],
            "fs": {"workspaceOnly": True},
        },
    }


def sync_openclaw_group_agents(group_ids: list[int] | set[int]) -> bool:
    if not OPENCLAW_CONFIG_PATH.exists():
        logger.warning("OpenClaw config does not exist: {}", OPENCLAW_CONFIG_PATH)
        return False

    config = json.loads(OPENCLAW_CONFIG_PATH.read_text(encoding="utf-8"))
    agents = config.setdefault("agents", {})
    tools = config.setdefault("tools", {})
    web = tools.setdefault("web", {})
    search = web.setdefault("search", {})
    search["enabled"] = True
    search.setdefault("provider", "kimi")

    agent_list = agents.setdefault("list", [])
    by_id: dict[str, dict[str, Any]] = {
        str(agent.get("id")): agent for agent in agent_list if isinstance(agent, dict) and agent.get("id")
    }
    template_agent = by_id.get("qq_bot") or next(iter(by_id.values()), {"model": {}})

    changed = False
    for group_id in sorted(int(group_id) for group_id in group_ids):
        ensure_group_workspace(group_id)
        sync_group_workspace_support_files(group_id)
        desired_entries = (
            _build_chat_agent_entry(group_id, template_agent),
            _build_memory_agent_entry(group_id, template_agent),
        )
        for desired in desired_entries:
            current = by_id.get(desired["id"])
            if current == desired:
                continue
            if current is None:
                agent_list.append(desired)
            else:
                current.clear()
                current.update(desired)
            by_id[desired["id"]] = desired
            changed = True

    if changed:
        OPENCLAW_CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("OpenClaw group agent config updated for groups {}", ",".join(str(gid) for gid in sorted(group_ids)))
    return changed


def ensure_group_openclaw_setup(group_ids: list[int] | set[int]) -> bool:
    normalized_group_ids = sorted({int(group_id) for group_id in group_ids})
    for group_id in normalized_group_ids:
        bootstrap_group_memory(group_id)
        sync_group_workspace_support_files(group_id)
    return sync_openclaw_group_agents(normalized_group_ids)


def build_all_group_memory_report(group_ids: list[int] | set[int]) -> str:
    normalized_group_ids = sorted({int(group_id) for group_id in group_ids})
    ensure_group_openclaw_setup(normalized_group_ids)
    blocks: list[str] = []
    for group_id in normalized_group_ids:
        memory_text = read_memory_markdown(group_id).strip() or "当前为空。"
        blocks.append(f"群 {group_id}\n{memory_text}")
    return "\n\n".join(blocks).strip()
