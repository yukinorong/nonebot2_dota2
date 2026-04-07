from __future__ import annotations

import asyncio
import contextlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .common import env, load_env_file
from .dota_knowledge_store import (
    DERIVED_HERO_BRIEFS_FILE,
    DERIVED_META_BRIEFS_FILE,
    HERO_ALIASES_FILE,
    HERO_DURATIONS_FILE,
    HERO_ITEM_POPULARITY_FILE,
    HERO_MATCHUPS_FILE,
    HERO_STATS_FILE,
    ITEM_ALIASES_FILE,
    META_FILE,
    SYNC_STATE_FILE,
    build_hero_aliases,
    build_item_aliases,
    ensure_knowledge_dirs,
    load_existing_hero_names,
    load_existing_item_names,
    load_json,
    save_json,
)

__plugin_meta__ = PluginMetadata(
    name="dota_knowledge_sync",
    description="Sync OpenDota hero knowledge into local JSON files.",
    usage="/dota_knowledge_sync 手动同步一次本地 Dota2 知识库。",
)

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
DOTA2_KNOWLEDGE_ENABLED = env(ENV_VALUES, "DOTA2_KNOWLEDGE_ENABLED", "true").lower() == "true"
DOTA2_KNOWLEDGE_SYNC_INTERVAL_SECONDS = max(3600, int(env(ENV_VALUES, "DOTA2_KNOWLEDGE_SYNC_INTERVAL_SECONDS", "21600")))
DOTA2_KNOWLEDGE_MATCHUP_MIN_GAMES = max(50, int(env(ENV_VALUES, "DOTA2_KNOWLEDGE_MATCHUP_MIN_GAMES", "200")))
DOTA2_KNOWLEDGE_FETCH_CONCURRENCY = max(2, int(env(ENV_VALUES, "DOTA2_KNOWLEDGE_FETCH_CONCURRENCY", "8")))
OPENDOTA_BASE = "https://api.opendota.com/api"


driver = get_driver()
dota_knowledge_sync = on_command("dota_knowledge_sync", priority=5, block=True, permission=SUPERUSER)
_sync_task: asyncio.Task[None] | None = None
_sync_lock = asyncio.Lock()


def _http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 nonebot2-dota-knowledge"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _fetch_json(url: str) -> Any:
    return await asyncio.to_thread(_http_get_json, url)


async def _safe_fetch_json(url: str, default: Any, *, error_label: str, errors: list[str], semaphore: asyncio.Semaphore) -> Any:
    async with semaphore:
        try:
            return await _fetch_json(url)
        except Exception as exc:
            logger.warning("{} failed: {}", error_label, exc)
            errors.append(f"{error_label}: {exc}")
            return default


def _hero_display_name(hero: dict[str, Any], existing_names: dict[int, str]) -> str:
    hero_id = int(hero.get("id", 0) or 0)
    return existing_names.get(hero_id) or str(hero.get("localized_name") or hero.get("name") or hero_id)


def _win_rate(wins: int | float, picks: int | float) -> float | None:
    if not picks:
        return None
    return round(float(wins) * 100.0 / float(picks), 2)


def _duration_text(duration_rows: list[dict[str, Any]]) -> str:
    if not duration_rows:
        return "数据不足"
    best_row = max(duration_rows, key=lambda row: (row.get("wins", 0) / max(1, row.get("games_played", 0)), row.get("games_played", 0)))
    minute_mark = int(best_row.get("x", 0) or 0)
    if minute_mark <= 20:
        return "前中期发力"
    if minute_mark <= 35:
        return "中期强势"
    return "偏后期成长"


def _popular_items_text(item_popularity: dict[str, Any], item_names: dict[int, str]) -> str:
    labels: list[str] = []
    for stage in ("start_game_items", "early_game_items", "mid_game_items", "late_game_items"):
        stage_items = item_popularity.get(stage) or {}
        if not isinstance(stage_items, dict):
            continue
        top_items = sorted(stage_items.items(), key=lambda item: item[1], reverse=True)[:2]
        for item_id, _ in top_items:
            if str(item_id).isdigit():
                name = item_names.get(int(item_id))
                if name and name not in labels:
                    labels.append(name)
    return "、".join(labels[:6]) or "暂无明显热门装"


def _matchup_text(hero_id: int, matchup_rows: list[dict[str, Any]], existing_names: dict[int, str]) -> str:
    normalized_rows = []
    for row in matchup_rows:
        games_played = int(row.get("games_played", 0) or 0)
        if games_played < DOTA2_KNOWLEDGE_MATCHUP_MIN_GAMES:
            continue
        wins = int(row.get("wins", 0) or 0)
        opponent_win_rate = wins * 100.0 / games_played
        normalized_rows.append((opponent_win_rate, int(row.get("hero_id", 0) or 0), games_played))
    if not normalized_rows:
        return "样本不足"
    hard_rows = sorted(normalized_rows, reverse=True)[:3]
    easy_rows = sorted(normalized_rows)[:3]
    hard_text = "、".join(existing_names.get(opponent_id, str(opponent_id)) for _, opponent_id, _ in hard_rows)
    easy_text = "、".join(existing_names.get(opponent_id, str(opponent_id)) for _, opponent_id, _ in easy_rows)
    return f"较怕 {hard_text}；较好打 {easy_text}"


def _derive_hero_briefs(
    hero_stats: list[dict[str, Any]],
    hero_matchups: dict[str, list[dict[str, Any]]],
    hero_item_popularity: dict[str, dict[str, Any]],
    hero_durations: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    existing_hero_names = load_existing_hero_names()
    item_names = load_existing_item_names()
    result: dict[str, Any] = {}
    for hero in hero_stats:
        hero_id = int(hero.get("id", 0) or 0)
        if hero_id <= 0:
            continue
        total_picks = sum(int(hero.get(f"{rank}_pick", 0) or 0) for rank in range(1, 9))
        total_wins = sum(int(hero.get(f"{rank}_win", 0) or 0) for rank in range(1, 9))
        result[str(hero_id)] = {
            "hero_id": hero_id,
            "display_name": _hero_display_name(hero, existing_hero_names),
            "localized_name": hero.get("localized_name"),
            "name": hero.get("name"),
            "primary_attr": hero.get("primary_attr"),
            "attack_type": hero.get("attack_type"),
            "roles": hero.get("roles") or [],
            "roles_text": "、".join(hero.get("roles") or []) or "未知",
            "pub_win_rate": _win_rate(total_wins, total_picks),
            "turbo_win_rate": _win_rate(hero.get("turbo_wins", 0), hero.get("turbo_picks", 0)),
            "power_spike": _duration_text(hero_durations.get(str(hero_id), [])),
            "popular_items_text": _popular_items_text(hero_item_popularity.get(str(hero_id), {}), item_names),
            "matchup_text": _matchup_text(hero_id, hero_matchups.get(str(hero_id), []), existing_hero_names),
        }
    return result


def _derive_meta_briefs(hero_briefs: dict[str, Any]) -> dict[str, Any]:
    valid_heroes = [brief for brief in hero_briefs.values() if isinstance(brief, dict) and brief.get("pub_win_rate") is not None]
    top_pub = sorted(valid_heroes, key=lambda brief: brief.get("pub_win_rate", 0.0), reverse=True)[:15]
    return {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "top_pub_winrate": top_pub,
    }


async def _fetch_hero_detail_bundle(
    hero_id: int,
    *,
    semaphore: asyncio.Semaphore,
    errors: list[str],
) -> tuple[int, list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    matchups_task = _safe_fetch_json(
        f"{OPENDOTA_BASE}/heroes/{hero_id}/matchups",
        [],
        error_label=f"hero {hero_id} matchups",
        errors=errors,
        semaphore=semaphore,
    )
    item_popularity_task = _safe_fetch_json(
        f"{OPENDOTA_BASE}/heroes/{hero_id}/itemPopularity",
        {},
        error_label=f"hero {hero_id} itemPopularity",
        errors=errors,
        semaphore=semaphore,
    )
    durations_task = _safe_fetch_json(
        f"{OPENDOTA_BASE}/heroes/{hero_id}/durations",
        [],
        error_label=f"hero {hero_id} durations",
        errors=errors,
        semaphore=semaphore,
    )
    matchups, item_popularity, durations = await asyncio.gather(matchups_task, item_popularity_task, durations_task)
    return hero_id, matchups if isinstance(matchups, list) else [], item_popularity if isinstance(item_popularity, dict) else {}, durations if isinstance(durations, list) else []


async def sync_dota_knowledge(force: bool = False) -> list[str]:
    del force
    ensure_knowledge_dirs()
    async with _sync_lock:
        logger.info("Starting Dota knowledge sync from OpenDota")
        fetch_errors: list[str] = []
        source = "opendota"
        try:
            hero_stats = await _fetch_json(f"{OPENDOTA_BASE}/heroStats")
            if not isinstance(hero_stats, list):
                raise RuntimeError("OpenDota heroStats returned invalid payload")
        except Exception as exc:
            fetch_errors.append(f"heroStats: {exc}")
            cached_hero_stats = load_json(HERO_STATS_FILE, [])
            if isinstance(cached_hero_stats, list) and cached_hero_stats:
                hero_stats = cached_hero_stats
                source = "cached"
            else:
                source = "bootstrap"
                hero_stats = [
                    {
                        "id": hero_id,
                        "localized_name": hero_name,
                        "name": hero_name,
                        "primary_attr": None,
                        "attack_type": None,
                        "roles": [],
                    }
                    for hero_id, hero_name in sorted(load_existing_hero_names().items())
                ]
            logger.warning("Falling back to {} Dota knowledge source because heroStats fetch failed: {}", source, exc)

        hero_matchups: dict[str, list[dict[str, Any]]] = {}
        hero_item_popularity: dict[str, dict[str, Any]] = {}
        hero_durations: dict[str, list[dict[str, Any]]] = {}

        if source == "opendota":
            hero_ids = [int(hero.get("id", 0) or 0) for hero in hero_stats if int(hero.get("id", 0) or 0) > 0]
            semaphore = asyncio.Semaphore(DOTA2_KNOWLEDGE_FETCH_CONCURRENCY)
            bundles = await asyncio.gather(
                *[
                    _fetch_hero_detail_bundle(hero_id, semaphore=semaphore, errors=fetch_errors)
                    for hero_id in hero_ids
                ]
            )
            for hero_id, matchups, item_popularity, durations in bundles:
                hero_matchups[str(hero_id)] = matchups
                hero_item_popularity[str(hero_id)] = item_popularity
                hero_durations[str(hero_id)] = durations
        else:
            cached_matchups = load_json(HERO_MATCHUPS_FILE, {})
            cached_item_popularity = load_json(HERO_ITEM_POPULARITY_FILE, {})
            cached_durations = load_json(HERO_DURATIONS_FILE, {})
            hero_matchups = cached_matchups if isinstance(cached_matchups, dict) else {}
            hero_item_popularity = cached_item_popularity if isinstance(cached_item_popularity, dict) else {}
            hero_durations = cached_durations if isinstance(cached_durations, dict) else {}

        item_names = load_existing_item_names()
        hero_aliases = build_hero_aliases(hero_stats)
        item_aliases = build_item_aliases(item_names)
        hero_briefs = _derive_hero_briefs(hero_stats, hero_matchups, hero_item_popularity, hero_durations)
        meta_briefs = _derive_meta_briefs(hero_briefs)

        save_json(HERO_STATS_FILE, hero_stats)
        save_json(HERO_MATCHUPS_FILE, hero_matchups)
        save_json(HERO_ITEM_POPULARITY_FILE, hero_item_popularity)
        save_json(HERO_DURATIONS_FILE, hero_durations)
        save_json(HERO_ALIASES_FILE, hero_aliases)
        save_json(ITEM_ALIASES_FILE, item_aliases)
        save_json(DERIVED_HERO_BRIEFS_FILE, hero_briefs)
        save_json(DERIVED_META_BRIEFS_FILE, meta_briefs)
        save_json(META_FILE, {"updated_at": meta_briefs["updated_at"], "hero_count": len(hero_stats), "fetch_errors": len(fetch_errors), "source": source})
        save_json(
            SYNC_STATE_FILE,
            {
                "last_success_at": meta_briefs["updated_at"],
                "error": None if source == "opendota" and not fetch_errors else "; ".join(fetch_errors[:10]) or None,
                "fetch_errors": fetch_errors[:50],
                "source": source,
            },
        )
        summaries = [
            f"hero_stats={len(hero_stats)}",
            f"hero_aliases={len(hero_aliases)}",
            f"item_aliases={len(item_aliases)}",
            f"fetch_errors={len(fetch_errors)}",
            f"source={source}",
        ]
        logger.info("Dota knowledge sync completed: {}", ", ".join(summaries))
        return summaries


async def _sync_loop() -> None:
    while True:
        try:
            await sync_dota_knowledge()
        except Exception as exc:
            logger.exception("Dota knowledge sync failed")
            save_json(SYNC_STATE_FILE, {"last_success_at": None, "error": str(exc)})
        await asyncio.sleep(DOTA2_KNOWLEDGE_SYNC_INTERVAL_SECONDS)


@driver.on_startup
async def _startup_dota_knowledge_sync() -> None:
    global _sync_task
    if not DOTA2_KNOWLEDGE_ENABLED:
        logger.info("Dota knowledge sync is disabled.")
        return
    if _sync_task and not _sync_task.done():
        return
    _sync_task = asyncio.create_task(_sync_loop(), name="dota-knowledge-sync")
    logger.info("Dota knowledge sync started interval={}s", DOTA2_KNOWLEDGE_SYNC_INTERVAL_SECONDS)


@driver.on_shutdown
async def _shutdown_dota_knowledge_sync() -> None:
    global _sync_task
    if _sync_task is None:
        return
    _sync_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _sync_task
    _sync_task = None


@dota_knowledge_sync.handle()
async def _handle_dota_knowledge_sync(args: Message = CommandArg()) -> None:
    force = str(args).strip().lower() == "force"
    summaries = await sync_dota_knowledge(force=force)
    await dota_knowledge_sync.finish("Dota2 知识库同步完成：\n" + "\n".join(summaries))
