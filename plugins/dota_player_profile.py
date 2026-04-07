from __future__ import annotations

import json
import statistics
from typing import Any

from .dota2_match_store import get_recent_account_matches
from .dota_knowledge_store import load_existing_hero_names, load_existing_item_names


def _mean(values: list[int]) -> float:
    if not values:
        return 0.0
    return round(statistics.fmean(values), 2)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _hero_name(hero_id: int, hero_names: dict[int, str]) -> str:
    return hero_names.get(hero_id, f"英雄{hero_id}")


def _item_name(item_id: int, item_names: dict[int, str]) -> str | None:
    if item_id <= 0:
        return None
    return item_names.get(item_id, f"物品{item_id}")


def _load_items_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw or "{}")
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _row_snapshot(row: dict[str, Any], hero_names: dict[int, str]) -> dict[str, Any]:
    return {
        "match_id": _safe_int(row.get("match_id")),
        "hero_id": _safe_int(row.get("hero_id")),
        "hero_name": _hero_name(_safe_int(row.get("hero_id")), hero_names),
        "won": _safe_int(row.get("won")),
        "kills": _safe_int(row.get("kills")),
        "deaths": _safe_int(row.get("deaths")),
        "assists": _safe_int(row.get("assists")),
        "gold_per_min": _safe_int(row.get("gold_per_min")),
        "xp_per_min": _safe_int(row.get("xp_per_min")),
        "last_hits": _safe_int(row.get("last_hits")),
        "hero_damage": _safe_int(row.get("hero_damage")),
        "tower_damage": _safe_int(row.get("tower_damage")),
        "start_time": _safe_int(row.get("start_time")),
    }


def build_player_profile_features(account_id: str, limit: int = 50) -> dict[str, Any]:
    rows = get_recent_account_matches(account_id, limit=limit)
    if not rows:
        return {"steam_id": str(account_id), "sample_size": 0}

    hero_names = load_existing_hero_names()
    item_names = load_existing_item_names()
    sample_size = len(rows)
    recent_rows = rows[:10]
    win_count = sum(1 for row in rows if _safe_int(row.get("won")) == 1)
    hero_counts: dict[int, int] = {}
    item_counts: dict[str, int] = {}
    core_like_matches = 0
    support_like_matches = 0
    kill_values: list[int] = []
    death_values: list[int] = []
    assist_values: list[int] = []
    gpm_values: list[int] = []
    xpm_values: list[int] = []
    lh_values: list[int] = []
    hero_damage_values: list[int] = []
    tower_damage_values: list[int] = []

    for row in rows:
        hero_id = _safe_int(row.get("hero_id"))
        if hero_id > 0:
            hero_counts[hero_id] = hero_counts.get(hero_id, 0) + 1
        kill_values.append(_safe_int(row.get("kills")))
        death_values.append(_safe_int(row.get("deaths")))
        assist_values.append(_safe_int(row.get("assists")))
        gpm_values.append(_safe_int(row.get("gold_per_min")))
        xpm_values.append(_safe_int(row.get("xp_per_min")))
        lh_values.append(_safe_int(row.get("last_hits")))
        hero_damage_values.append(_safe_int(row.get("hero_damage")))
        tower_damage_values.append(_safe_int(row.get("tower_damage")))

        if _safe_int(row.get("gold_per_min")) >= 520 and _safe_int(row.get("last_hits")) >= 180:
            core_like_matches += 1
        if _safe_int(row.get("assists")) >= 16 and _safe_int(row.get("gold_per_min")) <= 430:
            support_like_matches += 1

        items_payload = _load_items_payload(str(row.get("items_json") or ""))
        main_items = items_payload.get("main") or {}
        if isinstance(main_items, dict):
            for item_id in main_items.values():
                item_name = _item_name(_safe_int(item_id), item_names)
                if item_name:
                    item_counts[item_name] = item_counts.get(item_name, 0) + 1

    top_heroes = sorted(hero_counts.items(), key=lambda item: (item[1], item[0]), reverse=True)[:5]
    top_items = sorted(item_counts.items(), key=lambda item: (item[1], item[0]), reverse=True)[:8]

    style_tags: list[str] = []
    if _mean(gpm_values) >= 520:
        style_tags.append("偏核心刷钱")
    if _mean(assist_values) >= 15:
        style_tags.append("团战参与高")
    if _mean(death_values) >= 7:
        style_tags.append("死亡偏多")
    if _mean(hero_damage_values) >= 25000:
        style_tags.append("输出占比高")
    if core_like_matches > support_like_matches and core_like_matches >= sample_size * 0.35:
        style_tags.append("更像核心位打法")
    elif support_like_matches >= sample_size * 0.35:
        style_tags.append("更像功能/辅助位打法")
    if not style_tags:
        style_tags.append("打法比较平均")

    problem_tags: list[str] = []
    if _mean(death_values) >= 7:
        problem_tags.append("阵亡控制偏差")
    if _mean(lh_values) < 140:
        problem_tags.append("补刀和资源转化不足")
    if _mean(hero_damage_values) < 18000 and _mean(gpm_values) >= 480:
        problem_tags.append("经济转输出效率偏低")
    if _mean(tower_damage_values) < 2500:
        problem_tags.append("推进贡献偏低")
    if recent_rows:
        recent_win_rate = sum(1 for row in recent_rows if _safe_int(row.get("won")) == 1) / len(recent_rows)
        total_win_rate = win_count / sample_size
    else:
        recent_win_rate = 0.0
        total_win_rate = 0.0
    if recent_win_rate + 0.12 < total_win_rate:
        problem_tags.append("近期状态明显下滑")
    if not problem_tags:
        problem_tags.append("暂未发现特别突出的结构性短板")

    best_row = max(rows, key=lambda row: (_safe_int(row.get("won")), _safe_int(row.get("kills")) + _safe_int(row.get("assists")), _safe_int(row.get("start_time"))))
    worst_row = max(rows, key=lambda row: (_safe_int(row.get("deaths")), -_safe_int(row.get("won")), _safe_int(row.get("start_time"))))
    low_farm_row = min(rows, key=lambda row: (_safe_int(row.get("gold_per_min")), _safe_int(row.get("start_time"))))
    high_damage_row = max(rows, key=lambda row: (_safe_int(row.get("hero_damage")), _safe_int(row.get("start_time"))))

    return {
        "steam_id": str(account_id),
        "sample_size": sample_size,
        "win_count": win_count,
        "loss_count": sample_size - win_count,
        "win_rate": round(total_win_rate, 4),
        "recent_10_win_rate": round(recent_win_rate, 4),
        "style_tags": style_tags,
        "problem_tags": problem_tags,
        "top_heroes": [
            {
                "hero_id": hero_id,
                "hero_name": _hero_name(hero_id, hero_names),
                "matches": count,
                "share": round(count / sample_size, 4),
            }
            for hero_id, count in top_heroes
        ],
        "top_items": [
            {"item_name": item_name, "matches": count}
            for item_name, count in top_items
        ],
        "role_tendency": {
            "core_like_matches": core_like_matches,
            "support_like_matches": support_like_matches,
        },
        "averages": {
            "kills": _mean(kill_values),
            "deaths": _mean(death_values),
            "assists": _mean(assist_values),
            "gold_per_min": _mean(gpm_values),
            "xp_per_min": _mean(xpm_values),
            "last_hits": _mean(lh_values),
            "hero_damage": _mean(hero_damage_values),
            "tower_damage": _mean(tower_damage_values),
        },
        "recent_trend": {
            "recent_10_average_kills": _mean([_safe_int(row.get("kills")) for row in recent_rows]),
            "recent_10_average_deaths": _mean([_safe_int(row.get("deaths")) for row in recent_rows]),
            "recent_10_average_gpm": _mean([_safe_int(row.get("gold_per_min")) for row in recent_rows]),
            "recent_10_average_damage": _mean([_safe_int(row.get("hero_damage")) for row in recent_rows]),
        },
        "representative_matches": {
            "best": _row_snapshot(best_row, hero_names),
            "worst": _row_snapshot(worst_row, hero_names),
            "low_farm": _row_snapshot(low_farm_row, hero_names),
            "high_damage": _row_snapshot(high_damage_row, hero_names),
        },
    }
