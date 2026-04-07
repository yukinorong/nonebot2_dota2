from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nonebot import logger

from .common import pick_bot, send_group_text
from .llm_gateway import ask_dota, ask_main
from .runtime_state_store import get_bool_flag, set_bool_flag
from .dota2_match_store import (
    get_recent_account_analysis,
    get_recent_account_matches,
    has_raw_match,
    rebuild_player_match_analysis_from_raw_matches,
    save_raw_match_and_analysis,
)
from .dota_player_profile import build_player_profile_features
from .dota_guide import build_hero_guide_text as _build_hero_guide_text
from .dota2_watch_config import (
    display_name_for_account as _config_display_name_for_account,
    group_ids_for_account,
    list_watch_account_ids,
    list_watched_accounts as _config_list_watched_accounts,
    resolve_watched_account as _config_resolve_watched_account,
)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "dota2_monitor"
STATE_FILE = DATA_DIR / "state.json"
ENV_FILE = BASE_DIR / ".env"
DOTA_CONSTANTS_BASE = "https://raw.githubusercontent.com/odota/dotaconstants/master/build"


def _load_env_file() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


_ENV_VALUES = _load_env_file()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, _ENV_VALUES.get(name, default))


STEAM_API_BASE = _env("DOTA2_STEAM_API_BASE", "http://api.steampowered.com").strip().rstrip("/")


DOTA2_ENABLED = _env("DOTA2_ENABLED", "true").lower() == "true"
DOTA2_STEAM_API_KEY = _env("DOTA2_STEAM_API_KEY", "").strip()
DOTA2_NOTIFY_GROUP_ID = int(_env("DOTA2_NOTIFY_GROUP_ID", "1081502166"))
DOTA2_POLL_INTERVAL_SECONDS = max(60, int(_env("DOTA2_POLL_INTERVAL_SECONDS", "300")))
DOTA2_HISTORY_WINDOW = max(1, min(100, int(_env("DOTA2_HISTORY_WINDOW", "1"))))
DOTA2_SEQUENCE_BATCH_SIZE = max(1, min(100, int(_env("DOTA2_SEQUENCE_BATCH_SIZE", "1"))))


def _watched_account_ids() -> list[str]:
    return list_watch_account_ids()


DOTA2_OUTPUT_VERSION = _env("DOTA2_OUTPUT_VERSION", "v1").strip().lower() or "v1"
DOTA2_V2_MAX_MATCHES_PER_RUN = max(1, int(_env("DOTA2_V2_MAX_MATCHES_PER_RUN", "1")))
DOTA2_V2_STARTUP_BACKFILL_MATCHES = max(0, int(_env("DOTA2_V2_STARTUP_BACKFILL_MATCHES", "0")))
DOTA2_V2_DEBUG_DEFAULT = _env("DOTA2_V2_DEBUG", "false").lower() == "true"

HERO_CACHE_FILE = Path(_env("DOTA2_HERO_CACHE_FILE", str(DATA_DIR / "heroes.json")))
ITEM_CACHE_FILE = Path(_env("DOTA2_ITEM_CACHE_FILE", str(DATA_DIR / "items.json")))


_hero_map: dict[int, str] = {}
_item_map: dict[int, str] = {}
_poll_lock = asyncio.Lock()

GAME_MODE_NAMES = {
    0: "未知模式",
    1: "全阵营选择",
    2: "队长模式",
    3: "随机征召",
    4: "单一征召",
    5: "全英雄随机",
    18: "技能征召",
    22: "全阵营选择",
    23: "加速模式",
}

LOBBY_TYPE_NAMES = {
    0: "普通匹配",
    1: "练习模式",
    7: "天梯",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_v2_debug_enabled() -> bool:
    return get_bool_flag("dota2_v2_debug", default=DOTA2_V2_DEBUG_DEFAULT)


def set_v2_debug_enabled(enabled: bool) -> bool:
    return set_bool_flag("dota2_v2_debug", bool(enabled))


def _log_v2_debug(stage: str, *, match_id: Any, group_id: int | None, payload: Any) -> None:
    if not is_v2_debug_enabled():
        return
    try:
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        body = str(payload)
    logger.info(
        "Dota2 v2 debug stage={} match_id={} group_id={}\n{}",
        stage,
        match_id,
        group_id if group_id is not None else "none",
        body,
    )


def _notify_group_ids_for_account(account_id: str) -> list[int]:
    return group_ids_for_account(account_id, default_group_id=DOTA2_NOTIFY_GROUP_ID)


async def _persist_match_detail(match: dict[str, Any], *, target_steam_ids: set[str] | None = None) -> bool:
    target_ids = target_steam_ids or set(_watched_account_ids())
    try:
        await asyncio.to_thread(
            save_raw_match_and_analysis,
            match,
            target_steam_ids=target_ids,
        )
        return True
    except Exception:
        logger.exception(
            "Dota2 persist failed: stage=persist_match_detail match_id={} match_seq_num={} target_steam_ids={}",
            match.get("match_id"),
            match.get("match_seq_num"),
            sorted(target_ids),
        )
        return False


async def _send_group_texts(group_ids: list[int], text: str) -> None:
    for group_id in group_ids:
        await send_group_text(group_id, text)


def _build_steam_url(interface: str, method: str, *, version: int = 1, **params: Any) -> str:
    query = {"key": DOTA2_STEAM_API_KEY}
    for key, value in params.items():
        if value is None:
            continue
        query[key] = value
    encoded = urllib.parse.urlencode(query)
    return f"{STEAM_API_BASE}/{interface}/{method}/v{version}/?{encoded}"


def _http_get_json(url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            output = subprocess.check_output(
                [
                    "curl",
                    "-fsSL",
                    "--http1.1",
                    "--retry",
                    "3",
                    "--retry-all-errors",
                    "--connect-timeout",
                    "15",
                    "--max-time",
                    "45",
                    "-A",
                    "nonebot2-dota2-monitor/2.0",
                    url,
                ],
                text=True,
            )
            return json.loads(output)
        except (
            subprocess.CalledProcessError,
            json.JSONDecodeError,
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            ConnectionError,
        ) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(1)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unexpected HTTP state")


def _http_post_json(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _fetch_json(url: str) -> dict[str, Any]:
    return await asyncio.to_thread(_http_get_json, url)


async def _post_json(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    return await asyncio.to_thread(_http_post_json, url, body, headers)


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"accounts": {}, "meta": {}, "updated_at": None}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("State file must contain an object")
        data.setdefault("accounts", {})
        data.setdefault("meta", {})
        return data
    except Exception:
        logger.exception("Failed to load Dota2 state file: %s", STATE_FILE)
        return {"accounts": {}, "meta": {}, "updated_at": None}


def _save_state(state: dict[str, Any]) -> None:
    _ensure_data_dir()
    state["updated_at"] = _utc_now_iso()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_id_name_map(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load cache file: %s", path)
        return {}
    if not isinstance(raw, dict):
        logger.warning("Cache file %s is not a JSON object", path)
        return {}
    source = raw
    if all(not str(key).isdigit() for key in raw.keys()):
        nested = raw.get("heroes") or raw.get("items")
        if isinstance(nested, dict):
            source = nested
    result: dict[int, str] = {}
    for key, value in source.items():
        if str(key).isdigit() and isinstance(value, str) and value.strip():
            result[int(key)] = value.strip()
    return result


def _save_id_name_map(path: Path, mapping: dict[int, str]) -> None:
    _ensure_data_dir()
    serializable = {str(key): value for key, value in sorted(mapping.items())}
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _fetch_recent_matches(account_id: str, matches_requested: int) -> list[dict[str, Any]]:
    url = _build_steam_url(
        "IDOTA2Match_570",
        "GetMatchHistory",
        account_id=account_id,
        matches_requested=matches_requested,
    )
    try:
        data = await _fetch_json(url)
    except Exception:
        logger.exception(
            "Dota2 collect failed: stage=fetch_recent_matches_failed account_id={} matches_requested={}",
            account_id,
            matches_requested,
        )
        raise
    result = data.get("result", {})
    matches = result.get("matches")
    if not isinstance(matches, list):
        logger.warning(
            "Dota2 collect failed: stage=fetch_recent_matches_invalid_payload account_id={} matches_requested={} reason=matches_not_list",
            account_id,
            matches_requested,
        )
        return []
    return matches


async def _fetch_sequence_match(
    match_seq_num: int,
    *,
    target_steam_ids: set[str] | None = None,
    persist_required: bool = False,
) -> dict[str, Any] | None:
    url = _build_steam_url(
        "IDOTA2Match_570",
        "GetMatchHistoryBySequenceNum",
        start_at_match_seq_num=match_seq_num,
        matches_requested=DOTA2_SEQUENCE_BATCH_SIZE,
    )
    try:
        data = await _fetch_json(url)
    except Exception:
        logger.exception(
            "Dota2 collect failed: stage=fetch_sequence_failed match_seq_num={} target_steam_ids={}",
            match_seq_num,
            sorted(target_steam_ids or set()),
        )
        raise
    result = data.get("result", {})
    matches = result.get("matches")
    if not isinstance(matches, list):
        logger.warning(
            "Dota2 collect failed: stage=fetch_sequence_invalid_payload match_seq_num={} reason=matches_not_list",
            match_seq_num,
        )
        return None
    for match in matches:
        if match.get("match_seq_num") == match_seq_num:
            persisted = await _persist_match_detail(match, target_steam_ids=target_steam_ids)
            if persist_required and not persisted:
                logger.warning(
                    "Dota2 collect failed: stage=fetch_sequence_persist_failed match_id={} match_seq_num={} reason=persist_required_failed",
                    match.get("match_id"),
                    match_seq_num,
                )
                return None
            return match
    logger.warning(
        "Dota2 collect failed: stage=fetch_sequence_not_found match_seq_num={} reason=target_sequence_not_found",
        match_seq_num,
    )
    return None


async def _fetch_hero_map_from_steam() -> dict[int, str]:
    url = _build_steam_url("IEconDOTA2_570", "GetHeroes", language="zh_cn")
    data = await _fetch_json(url)
    heroes = data.get("result", {}).get("heroes")
    if not isinstance(heroes, list):
        raise RuntimeError("Steam GetHeroes returned invalid payload")
    mapping: dict[int, str] = {}
    for hero in heroes:
        hero_id = hero.get("id")
        hero_name = hero.get("localized_name")
        if isinstance(hero_id, int) and isinstance(hero_name, str) and hero_name.strip():
            mapping[hero_id] = hero_name.strip()
    if not mapping:
        raise RuntimeError("Steam GetHeroes returned empty hero map")
    return mapping


async def _fetch_item_map_from_dotaconstants() -> dict[int, str]:
    url = f"{DOTA_CONSTANTS_BASE}/items.json"
    data = await _fetch_json(url)
    if not isinstance(data, dict):
        raise RuntimeError("dotaconstants items.json returned invalid payload")
    mapping: dict[int, str] = {}
    for item in data.values():
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        item_name = item.get("dname") or item.get("name")
        if isinstance(item_id, int) and isinstance(item_name, str) and item_name.strip():
            mapping[item_id] = item_name.strip()
    if not mapping:
        raise RuntimeError("dotaconstants items.json returned empty item map")
    return mapping


async def _ensure_name_maps(force_refresh: bool = False) -> None:
    global _hero_map, _item_map

    if force_refresh or not HERO_CACHE_FILE.exists():
        hero_map = await _fetch_hero_map_from_steam()
        _save_id_name_map(HERO_CACHE_FILE, hero_map)
    _hero_map = _load_id_name_map(HERO_CACHE_FILE)

    if force_refresh or not ITEM_CACHE_FILE.exists():
        item_map = await _fetch_item_map_from_dotaconstants()
        _save_id_name_map(ITEM_CACHE_FILE, item_map)
    _item_map = _load_id_name_map(ITEM_CACHE_FILE)


def _player_team_won(player_slot: int, radiant_win: bool) -> bool:
    return (player_slot < 128) == radiant_win


def _format_duration(seconds: int | None) -> str:
    if not isinstance(seconds, int) or seconds < 0:
        return "未知"
    minutes, remain = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{minutes}分{remain}秒"
    return f"{minutes}分{remain}秒"


def _format_timestamp(ts: int | None) -> str:
    if not isinstance(ts, int) or ts <= 0:
        return "未知时间"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _mode_name(game_mode: Any, lobby_type: Any) -> str:
    game_name = GAME_MODE_NAMES.get(game_mode, f"模式{game_mode}")
    lobby_name = LOBBY_TYPE_NAMES.get(lobby_type)
    return game_name if not lobby_name else f"{lobby_name}/{game_name}"


async def _ask_openclaw(prompt: str) -> str:
    return await ask_dota(prompt)


def _extract_player_from_match(match: dict[str, Any], account_id: int) -> dict[str, Any] | None:
    players = match.get("players")
    if not isinstance(players, list):
        return None
    for player in players:
        if isinstance(player, dict) and player.get("account_id") == account_id:
            return player
    return None


def _display_name_for_account(account_id: str) -> str:
    return _config_display_name_for_account(account_id)


def list_watched_accounts() -> list[dict[str, str]]:
    return _config_list_watched_accounts()


def resolve_watched_account(query: str) -> str | None:
    return _config_resolve_watched_account(query)


async def collect_recent_matches(account_id: str, matches_requested: int) -> dict[str, int]:
    requested = max(1, min(100, int(matches_requested)))
    summaries = {
        "requested": requested,
        "scanned": 0,
        "fetched": 0,
        "skipped": 0,
        "failed": 0,
    }
    logger.info(
        "Dota2 collect start: account_id={} requested={}",
        account_id,
        requested,
    )
    try:
        recent_matches = await _fetch_recent_matches(account_id, requested)
    except Exception:
        summaries["failed"] = requested
        logger.exception(
            "Dota2 collect failed: stage=fetch_recent_matches account_id={} requested={}",
            account_id,
            requested,
        )
        return summaries
    target_ids = {str(account_id)}
    for history_match in recent_matches[:requested]:
        summaries["scanned"] += 1
        match_id = history_match.get("match_id")
        match_seq_num = history_match.get("match_seq_num")
        if not isinstance(match_id, int) or not isinstance(match_seq_num, int):
            summaries["failed"] += 1
            logger.warning(
                "Dota2 collect failed: stage=invalid_history_match account_id={} match_id={} match_seq_num={} reason=missing_match_id_or_seq",
                account_id,
                match_id,
                match_seq_num,
            )
            continue
        if has_raw_match(match_id):
            summaries["skipped"] += 1
            logger.info(
                "Dota2 collect skip: stage=skip_existing_raw_match account_id={} match_id={} match_seq_num={} reason=raw_match_exists",
                account_id,
                match_id,
                match_seq_num,
            )
            continue
        try:
            detailed_match = await _fetch_sequence_match(
                match_seq_num,
                target_steam_ids=target_ids,
                persist_required=True,
            )
        except Exception:
            summaries["failed"] += 1
            logger.exception(
                "Dota2 collect failed: stage=fetch_sequence_match account_id={} match_id={} match_seq_num={}",
                account_id,
                match_id,
                match_seq_num,
            )
            continue
        if detailed_match is None:
            summaries["failed"] += 1
            logger.warning(
                "Dota2 collect failed: stage=fetch_sequence_match_empty account_id={} match_id={} match_seq_num={} reason=no_detailed_match",
                account_id,
                match_id,
                match_seq_num,
            )
            continue
        summaries["fetched"] += 1
    logger.info(
        "Dota2 collect done: account_id={} requested={} scanned={} fetched={} skipped={} failed={}",
        account_id,
        summaries["requested"],
        summaries["scanned"],
        summaries["fetched"],
        summaries["skipped"],
        summaries["failed"],
    )
    return summaries


async def collect_recent_matches_for_all(matches_requested: int = 50) -> dict[str, Any]:
    requested = max(1, min(100, int(matches_requested)))
    watched_accounts = list_watched_accounts()
    overall = {
        "accounts": len(watched_accounts),
        "requested_per_account": requested,
        "requested_total": requested * len(watched_accounts),
        "scanned": 0,
        "fetched": 0,
        "skipped": 0,
        "failed": 0,
        "per_account": [],
    }
    for item in watched_accounts:
        account_id = str(item["account_id"])
        summary = await collect_recent_matches(account_id, requested)
        overall["scanned"] += int(summary["scanned"])
        overall["fetched"] += int(summary["fetched"])
        overall["skipped"] += int(summary["skipped"])
        overall["failed"] += int(summary["failed"])
        overall["per_account"].append(
            {
                "account_id": account_id,
                "display_name": str(item["display_name"]),
                **summary,
            }
        )
    return overall


def rebuild_recent_match_analysis(account_id: str | None = None) -> dict[str, int]:
    target_ids = {account_id} if account_id else set(_watched_account_ids())
    logger.info(
        "Dota2 rebuild analysis start: account_id={} target_steam_ids={}",
        account_id or "all",
        sorted(target_ids),
    )
    summaries = rebuild_player_match_analysis_from_raw_matches(target_steam_ids=target_ids)
    logger.info(
        "Dota2 rebuild analysis done: account_id={} scanned_matches={} inserted_rows={} skipped_existing_rows={} failed_matches={} failed_players={}",
        account_id or "all",
        summaries["scanned_matches"],
        summaries["inserted_rows"],
        summaries["skipped_existing_rows"],
        summaries["failed_matches"],
        summaries["failed_players"],
    )
    return summaries


def build_recent_match_analysis_text(account_id: str, limit: int = 50) -> str:
    summary = get_recent_account_analysis(account_id, limit=limit)
    sample_size = int(summary.get("sample_size") or 0)
    if sample_size <= 0:
        return f"本地还没有 {account_id} 的比赛分析数据，请先使用 /dota_collect 采集最近比赛。"

    display_name = _display_name_for_account(account_id)
    most_played_hero_id = summary.get("most_played_hero_id")
    highest_kills_hero_id = summary.get("highest_kills_hero_id")
    highest_deaths_hero_id = summary.get("highest_deaths_hero_id")
    most_played_hero_name = _resolve_hero_name(most_played_hero_id) if most_played_hero_id else "未知英雄"
    highest_kills_hero_name = _resolve_hero_name(highest_kills_hero_id) if highest_kills_hero_id else "未知英雄"
    highest_deaths_hero_name = _resolve_hero_name(highest_deaths_hero_id) if highest_deaths_hero_id else "未知英雄"
    win_count = int(summary.get("win_count") or 0)
    loss_count = int(summary.get("loss_count") or 0)
    win_rate = float(summary.get("win_rate") or 0.0) * 100
    return "\n".join(
        [
            f"Dota2 最近50场分析：{display_name}",
            f"样本数：{sample_size} 场，胜率：{win_rate:.2f}%（{win_count}胜{loss_count}负）",
            f"使用最多英雄：{most_played_hero_name}（{int(summary.get('most_played_hero_count') or 0)}场）",
            f"最高击杀：{int(summary.get('highest_kills') or 0)}杀，英雄：{highest_kills_hero_name}，Match ID：{summary.get('highest_kills_match_id') or '未知'}",
            f"最高死亡：{int(summary.get('highest_deaths') or 0)}死，英雄：{highest_deaths_hero_name}，Match ID：{summary.get('highest_deaths_match_id') or '未知'}",
        ]
    )


async def build_player_profile_text(account_id: str, *, group_id: int, limit: int = 50) -> str:
    features = build_player_profile_features(account_id, limit=limit)
    if int(features.get("sample_size") or 0) <= 0:
        return f"本地还没有 {account_id} 的比赛分析数据，请先使用 /dota_collect 采集最近比赛。"
    display_name = _display_name_for_account(account_id)
    prompt = (
        "你是一个专业的 Dota2 复盘教练。\n"
        "你会根据玩家最近比赛的结构化特征，提炼打法风格，并给出有逻辑的改进建议。\n"
        "请只基于给出的数据下结论，不要编造不存在的比赛细节。\n"
        "输出必须包含：打法概括、优势点、主要问题、最优先的3条改进措施。\n"
        "如果有必要，可以补一段更适合他当前英雄池的训练思路。\n"
        "结论要具体，避免空泛鼓励。\n"
        "只输出适合 QQ 聊天展示的纯文本，不要使用 Markdown，不要代码块。\n"
        "严格按下面格式输出，并保留这些换行和空行：\n"
        f"Dota2 最近50场打法分析：{display_name}\n\n"
        "打法概括：用 2 到 3 句话。\n\n"
        "优势点：\n"
        "1. ...\n"
        "2. ...\n\n"
        "主要问题：\n"
        "1. ...\n"
        "2. ...\n"
        "3. ...\n\n"
        "改进建议：\n"
        "1. ...\n"
        "2. ...\n"
        "3. ...\n\n"
        "如果没有必要，不要额外增加别的段落。\n"
        "不要输出思考过程，只保留回答正文。\n\n"
        f"玩家：{display_name}\n"
        f"最近比赛结构化特征：\n{json.dumps(features, ensure_ascii=False, indent=2)}"
    )
    return await ask_main(
        prompt,
        channel=f"qq-g{group_id}-dota-profile-{account_id}-{int(time.time())}",
    )


async def build_hero_guide_text(hero_query: str, *, group_id: int) -> str:
    return await _build_hero_guide_text(hero_query, group_id=group_id)


def _resolve_hero_name(hero_id: Any) -> str:
    hero_int = int(hero_id or 0)
    return _hero_map.get(hero_int, f"英雄{hero_int}")


def _resolve_item_name(item_id: Any) -> str | None:
    if not isinstance(item_id, int) or item_id <= 0:
        return None
    return _item_map.get(item_id, f"物品{item_id}")


def _extract_item_names(player: dict[str, Any]) -> dict[str, list[str]]:
    main_items = [_resolve_item_name(player.get(f"item_{index}")) for index in range(6)]
    backpack_items = [_resolve_item_name(player.get(f"backpack_{index}")) for index in range(3)]
    neutral_items = [
        _resolve_item_name(player.get("item_neutral")),
        _resolve_item_name(player.get("item_neutral2")),
    ]
    return {
        "main": [item for item in main_items if item],
        "backpack": [item for item in backpack_items if item],
        "neutral": [item for item in neutral_items if item],
    }


async def _build_match_message(
    account_id: str,
    history_match: dict[str, Any],
    detailed_match: dict[str, Any] | None,
) -> str | None:
    match = detailed_match or history_match
    player = _extract_player_from_match(match, int(account_id))
    if not player:
        return None

    hero_name = _resolve_hero_name(player.get("hero_id", 0))
    radiant_win = bool(match.get("radiant_win"))
    player_slot = int(player.get("player_slot", 0))
    result_text = "胜利" if _player_team_won(player_slot, radiant_win) else "失利"
    kills = int(player.get("kills", 0))
    deaths = int(player.get("deaths", 0))
    assists = int(player.get("assists", 0))
    mode_text = _mode_name(match.get("game_mode"), match.get("lobby_type"))
    duration_text = _format_duration(match.get("duration"))
    start_time_text = _format_timestamp(match.get("start_time"))
    hero_damage = player.get("hero_damage")
    radiant_score = match.get("radiant_score")
    dire_score = match.get("dire_score")
    items = [player.get(f"item_{index}", 0) for index in range(6)]

    display_name = _display_name_for_account(account_id)

    extra_lines = []
    if isinstance(hero_damage, int):
        extra_lines.append(f"伤害：{hero_damage}")
    if isinstance(player.get("gold_per_min"), int) and isinstance(player.get("xp_per_min"), int):
        extra_lines.append(f"GPM/XPM：{player['gold_per_min']}/{player['xp_per_min']}")
    if any(isinstance(item_id, int) and item_id > 0 for item_id in items):
        extra_lines.append("装备ID：" + ", ".join(str(item_id) for item_id in items if int(item_id) > 0))

    lines = [
        f"[Dota2] {display_name} 有新战绩",
        f"英雄：{hero_name}",
        f"结果：{result_text}",
        f"K/D/A：{kills}/{deaths}/{assists}",
        f"模式：{mode_text}",
        f"比分：{radiant_score}-{dire_score}",
        f"时长：{duration_text}",
        f"开始时间：{start_time_text}",
        f"Match ID：{history_match.get('match_id')}",
    ]
    lines.extend(extra_lines)
    return "\n".join(lines)


def _normalize_player_for_v2(player: dict[str, Any], radiant_win: bool) -> dict[str, Any]:
    account_id = str(player.get("account_id", 0))
    player_slot = int(player.get("player_slot", 0))
    item_names = _extract_item_names(player)
    return {
        "account_id": account_id,
        "display_name": _display_name_for_account(account_id),
        "is_tracked_player": account_id in _watched_account_ids(),
        "hero_id": int(player.get("hero_id", 0)),
        "hero_name": _resolve_hero_name(player.get("hero_id", 0)),
        "player_slot": player_slot,
        "side": "Radiant" if player_slot < 128 else "Dire",
        "won": _player_team_won(player_slot, radiant_win),
        "kills": int(player.get("kills", 0)),
        "deaths": int(player.get("deaths", 0)),
        "assists": int(player.get("assists", 0)),
        "last_hits": int(player.get("last_hits", 0)),
        "denies": int(player.get("denies", 0)),
        "level": int(player.get("level", 0)),
        "gold_per_min": int(player.get("gold_per_min", 0)),
        "xp_per_min": int(player.get("xp_per_min", 0)),
        "net_worth": int(player.get("net_worth", 0)),
        "hero_damage": int(player.get("hero_damage", 0)),
        "tower_damage": int(player.get("tower_damage", 0)),
        "hero_healing": int(player.get("hero_healing", 0)),
        "main_items": item_names["main"],
        "backpack_items": item_names["backpack"],
        "neutral_items": item_names["neutral"],
    }


def _build_v2_payload(match: dict[str, Any]) -> dict[str, Any]:
    radiant_win = bool(match.get("radiant_win"))
    players = match.get("players")
    normalized_players = []
    if isinstance(players, list):
        for player in players:
            if isinstance(player, dict):
                normalized_players.append(_normalize_player_for_v2(player, radiant_win))
    tracked_players = [player for player in normalized_players if player["is_tracked_player"]]
    return {
        "match_id": match.get("match_id"),
        "match_seq_num": match.get("match_seq_num"),
        "start_time": _format_timestamp(match.get("start_time")),
        "duration": _format_duration(match.get("duration")),
        "game_mode": _mode_name(match.get("game_mode"), match.get("lobby_type")),
        "lobby_type": LOBBY_TYPE_NAMES.get(match.get("lobby_type"), f"大厅{match.get('lobby_type')}"),
        "radiant_win": radiant_win,
        "radiant_score": match.get("radiant_score"),
        "dire_score": match.get("dire_score"),
        "tracked_players": tracked_players,
        "players": normalized_players,
    }


def _build_v2_prompt(payload: dict[str, Any]) -> str:
    return (
        "你是 Dota2 群聊里的毒舌战绩解说员。\n"
        "你会根据下面这场比赛的数据，只评价 tracked_players 里的监听玩家。\n"
        "要求：\n"
        "1. 只点评监听玩家，不评价别人。\n"
        "2. 每个监听玩家都要点名 display_name 和英雄名，别混淆。\n"
        "3. 输出简短、夸张、幽默，适合直接发 QQ 群，尽量控制在 3 句内。\n"
        "4. 如果监听玩家赢了，就吹捧、抬轿、赞美，多用地道 Dota2 黑话。\n"
        "5. 如果监听玩家输了，就辛辣吐槽、阴阳怪气、幽默挖苦，但不要做人身攻击，不要涉及现实侮辱。\n"
        "6. 不要复述整份数据，不要写成列表，不要加免责声明。\n\n"
        "比赛数据(JSON)：\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _build_v2_fallback_text(payload: dict[str, Any]) -> str:
    tracked_players = payload.get("tracked_players", [])
    if not isinstance(tracked_players, list) or not tracked_players:
        return f"Match {payload.get('match_id')} 有新比赛，但没找到监听玩家。"
    parts = []
    for player in tracked_players:
        if not isinstance(player, dict):
            continue
        result_text = "赢麻了" if player.get("won") else "寄了"
        parts.append(
            f"{player.get('display_name')} 的 {player.get('hero_name')} {result_text}，"
            f"KDA {player.get('kills')}/{player.get('deaths')}/{player.get('assists')}"
        )
    return f"Match {payload.get('match_id')} " + "；".join(parts)


async def _build_v2_group_message(
    match: dict[str, Any],
    *,
    target_account_ids: set[str] | None = None,
    group_id: int | None = None,
) -> str:
    payload = _build_v2_payload(match)
    if target_account_ids is not None:
        target_ids = {str(account_id) for account_id in target_account_ids}
        payload["tracked_players"] = [
            player
            for player in payload.get("tracked_players", [])
            if isinstance(player, dict) and str(player.get("account_id")) in target_ids
        ]
        payload["players"] = [
            {
                **player,
                "is_tracked_player": str(player.get("account_id")) in target_ids,
            }
            for player in payload.get("players", [])
            if isinstance(player, dict)
        ]
        if not payload["tracked_players"]:
            return f"Match {payload.get('match_id')} 有新比赛，但没找到指定监听玩家。"
    _log_v2_debug(
        "normalized_payload",
        match_id=payload.get("match_id"),
        group_id=group_id,
        payload=payload,
    )
    try:
        answer = await _ask_openclaw(_build_v2_prompt(payload))
        _log_v2_debug(
            "llm_response",
            match_id=payload.get("match_id"),
            group_id=group_id,
            payload={"text": answer},
        )
        return answer
    except Exception:
        logger.exception("OpenClaw v2 commentary failed for match %s", payload.get("match_id"))
        return _build_v2_fallback_text(payload)


async def build_latest_match_push_text(account_id: str) -> str:
    await _ensure_name_maps()
    recent_matches = await _fetch_recent_matches(account_id, 1)
    if not recent_matches:
        raise RuntimeError(f"账号 {account_id} 当前没有可读取的最近比赛。")
    history_match = recent_matches[0]
    match_seq_num = history_match.get("match_seq_num")
    if not isinstance(match_seq_num, int):
        raise RuntimeError(f"账号 {account_id} 的最近比赛缺少 match_seq_num。")
    detailed_match = await _fetch_sequence_match(match_seq_num)
    if not detailed_match:
        raise RuntimeError(f"账号 {account_id} 的最近比赛详情暂时拉取失败。")
    _log_v2_debug(
        "raw_match",
        match_id=detailed_match.get("match_id"),
        group_id=None,
        payload=detailed_match,
    )
    if DOTA2_OUTPUT_VERSION == "v2":
        return await _build_v2_group_message(detailed_match, target_account_ids={account_id}, group_id=None)
    message = await _build_match_message(account_id, history_match, detailed_match)
    if not message:
        raise RuntimeError(f"账号 {account_id} 的最近比赛里没找到对应玩家数据。")
    return message


def _normalize_known_ids(account_state: dict[str, Any]) -> list[int]:
    known_ids: list[int] = []
    for match_id in account_state.get("known_match_ids", []):
        if isinstance(match_id, int):
            known_ids.append(match_id)
        elif isinstance(match_id, str) and match_id.isdigit():
            known_ids.append(int(match_id))
    return known_ids


def _normalize_pending_ids(account_state: dict[str, Any]) -> list[int]:
    pending_ids: list[int] = []
    for match_id in account_state.get("pending_match_ids", []):
        if isinstance(match_id, int):
            pending_ids.append(match_id)
        elif isinstance(match_id, str) and match_id.isdigit():
            pending_ids.append(int(match_id))
    return pending_ids


def _merge_recent_queue(
    account_state: dict[str, Any],
    recent_matches: list[dict[str, Any]],
) -> tuple[list[int], list[int], dict[int, dict[str, Any]]]:
    known_ids = _normalize_known_ids(account_state)
    pending_ids = _normalize_pending_ids(account_state)
    recent_map: dict[int, dict[str, Any]] = {}
    latest_matches = recent_matches[:1]
    for match in latest_matches:
        match_id = match.get("match_id")
        if isinstance(match_id, int):
            recent_map[match_id] = match
    merged_pending_ids = [match_id for match_id in pending_ids if match_id in recent_map]
    seen = set(known_ids) | set(merged_pending_ids)
    for match in latest_matches:
        match_id = match.get("match_id")
        if isinstance(match_id, int) and match_id not in seen:
            merged_pending_ids.append(match_id)
            seen.add(match_id)
    return known_ids, merged_pending_ids, recent_map


async def _bootstrap_account_state(state: dict[str, Any], account_id: str) -> None:
    recent_matches = await _fetch_recent_matches(account_id, 1)
    if not recent_matches:
        state["accounts"][account_id] = {
            "last_pushed_match_id": None,
            "known_match_ids": [],
            "pending_match_ids": [],
            "bootstrapped_at": _utc_now_iso(),
        }
        return
    latest = recent_matches[0]
    latest_match_id = latest.get("match_id")
    state["accounts"][account_id] = {
        "last_pushed_match_id": latest_match_id,
        "known_match_ids": [latest_match_id] if isinstance(latest_match_id, int) else [],
        "pending_match_ids": [],
        "bootstrapped_at": _utc_now_iso(),
    }


async def _check_account_matches_v1(state: dict[str, Any], account_id: str) -> list[str]:
    account_state = state["accounts"].get(account_id)
    if not isinstance(account_state, dict):
        await _bootstrap_account_state(state, account_id)
        return []

    recent_matches = await _fetch_recent_matches(account_id, DOTA2_HISTORY_WINDOW)
    if not recent_matches:
        return []

    known_ids, pending_ids, recent_map = _merge_recent_queue(account_state, recent_matches)
    if not pending_ids:
        if recent_matches and isinstance(recent_matches[0].get("match_id"), int):
            account_state["last_pushed_match_id"] = recent_matches[0]["match_id"]
        account_state["known_match_ids"] = known_ids[-(DOTA2_HISTORY_WINDOW * 3) :]
        account_state["pending_match_ids"] = []
        return []

    pushed_match_ids: list[int] = []
    summaries: list[str] = []
    for match_id in list(pending_ids):
        history_match = recent_map.get(match_id)
        if history_match is None:
            continue
        match_seq_num = history_match.get("match_seq_num")
        detailed_match = None
        if isinstance(match_seq_num, int):
            try:
                detailed_match = await _fetch_sequence_match(match_seq_num)
            except Exception:
                logger.exception(
                    "Steam sequence lookup failed for account %s match_seq_num %s",
                    account_id,
                    match_seq_num,
                )
        message = await _build_match_message(account_id, history_match, detailed_match)
        if not message:
            pending_ids.remove(match_id)
            known_ids.append(match_id)
            continue
        await _send_group_texts(_notify_group_ids_for_account(account_id), message)
        pending_ids.remove(match_id)
        known_ids.append(match_id)
        pushed_match_ids.append(match_id)
        summaries.append(f"v1 account={account_id} match_id={match_id}")

    if pushed_match_ids:
        account_state["last_pushed_match_id"] = pushed_match_ids[-1]
    account_state["known_match_ids"] = known_ids[-(DOTA2_HISTORY_WINDOW * 3) :]
    account_state["pending_match_ids"] = pending_ids
    return summaries


def _mark_v2_match_processed(account_state: dict[str, Any], known_ids: list[int], pending_ids: list[int], match_id: int) -> None:
    if match_id in pending_ids:
        pending_ids.remove(match_id)
    if match_id not in known_ids:
        known_ids.append(match_id)
    account_state["last_pushed_match_id"] = match_id
    account_state["known_match_ids"] = known_ids[-(DOTA2_HISTORY_WINDOW * 3) :]
    account_state["pending_match_ids"] = pending_ids


async def _collect_account_matches_v2(
    state: dict[str, Any],
    account_id: str,
) -> list[dict[str, Any]]:
    account_state = state["accounts"].get(account_id)
    if not isinstance(account_state, dict):
        await _bootstrap_account_state(state, account_id)
        return []

    recent_matches = await _fetch_recent_matches(account_id, DOTA2_HISTORY_WINDOW)
    if not recent_matches:
        return []

    known_ids, pending_ids, recent_map = _merge_recent_queue(account_state, recent_matches)
    if not pending_ids:
        if recent_matches and isinstance(recent_matches[0].get("match_id"), int):
            account_state["last_pushed_match_id"] = recent_matches[0]["match_id"]
        account_state["known_match_ids"] = known_ids[-(DOTA2_HISTORY_WINDOW * 3) :]
        account_state["pending_match_ids"] = []
        return []

    collected: list[dict[str, Any]] = []
    for match_id in pending_ids[:DOTA2_V2_MAX_MATCHES_PER_RUN]:
        history_match = recent_map.get(match_id)
        if history_match is None:
            continue
        match_seq_num = history_match.get("match_seq_num")
        if not isinstance(match_seq_num, int):
            _mark_v2_match_processed(account_state, known_ids, pending_ids, match_id)
            continue
        collected.append(
            {
                "account_id": account_id,
                "match_id": match_id,
                "match_seq_num": match_seq_num,
                "group_ids": _notify_group_ids_for_account(account_id),
            }
        )

    account_state["known_match_ids"] = known_ids[-(DOTA2_HISTORY_WINDOW * 3) :]
    account_state["pending_match_ids"] = pending_ids
    return collected


async def _push_v2_match_events_once(state: dict[str, Any], watched_accounts: list[str]) -> list[str]:
    events_by_match: dict[int, dict[str, Any]] = {}
    for account_id in watched_accounts:
        try:
            for event in await _collect_account_matches_v2(state, account_id):
                match_id = int(event["match_id"])
                bucket = events_by_match.setdefault(
                    match_id,
                    {
                        "match_seq_num": int(event["match_seq_num"]),
                        "accounts_by_group": {},
                        "account_ids": set(),
                    },
                )
                bucket["account_ids"].add(str(event["account_id"]))
                accounts_by_group = bucket["accounts_by_group"]
                for group_id in event["group_ids"]:
                    accounts_by_group.setdefault(int(group_id), set()).add(str(event["account_id"]))
        except Exception:
            logger.exception("Dota2 check failed for account %s", account_id)

    summaries: list[str] = []
    for match_id in sorted(events_by_match):
        event = events_by_match[match_id]
        match_seq_num = event["match_seq_num"]
        detailed_match = await _fetch_sequence_match(match_seq_num)
        if not detailed_match:
            logger.warning("No sequence match found for aggregated match_id=%s", match_id)
            continue

        for group_id, target_account_ids in sorted(event["accounts_by_group"].items()):
            _log_v2_debug(
                "raw_match",
                match_id=match_id,
                group_id=group_id,
                payload=detailed_match,
            )
            message = await _build_v2_group_message(
                detailed_match,
                target_account_ids=target_account_ids,
                group_id=group_id,
            )
            await send_group_text(group_id, message)
            account_names = ",".join(_display_name_for_account(account_id) for account_id in sorted(target_account_ids))
            summaries.append(f"v2 group={group_id} match_id={match_id} accounts={account_names}")

        for account_id in sorted(event["account_ids"]):
            account_state = state["accounts"].get(account_id)
            if not isinstance(account_state, dict):
                continue
            known_ids = _normalize_known_ids(account_state)
            pending_ids = _normalize_pending_ids(account_state)
            _mark_v2_match_processed(account_state, known_ids, pending_ids, match_id)

    return summaries


async def _check_account_matches_v2(state: dict[str, Any], account_id: str) -> list[str]:
    account_state = state["accounts"].get(account_id)
    if not isinstance(account_state, dict):
        await _bootstrap_account_state(state, account_id)
        return []

    recent_matches = await _fetch_recent_matches(account_id, DOTA2_HISTORY_WINDOW)
    if not recent_matches:
        return []

    known_ids, pending_ids, recent_map = _merge_recent_queue(account_state, recent_matches)
    if not pending_ids:
        if recent_matches and isinstance(recent_matches[0].get("match_id"), int):
            account_state["last_pushed_match_id"] = recent_matches[0]["match_id"]
        account_state["known_match_ids"] = known_ids[-(DOTA2_HISTORY_WINDOW * 3) :]
        account_state["pending_match_ids"] = []
        return []

    processed = 0
    summaries: list[str] = []
    for match_id in list(pending_ids):
        if processed >= DOTA2_V2_MAX_MATCHES_PER_RUN:
            break
        history_match = recent_map.get(match_id)
        if history_match is None:
            continue
        match_seq_num = history_match.get("match_seq_num")
        if not isinstance(match_seq_num, int):
            pending_ids.remove(match_id)
            known_ids.append(match_id)
            continue
        detailed_match = await _fetch_sequence_match(match_seq_num)
        if not detailed_match:
            logger.warning("No sequence match found for account=%s match_id=%s", account_id, match_id)
            continue
        _log_v2_debug(
            "raw_match",
            match_id=match_id,
            group_id=None,
            payload=detailed_match,
        )
        message = await _build_v2_group_message(detailed_match, group_id=None)
        await _send_group_texts(_notify_group_ids_for_account(account_id), message)
        pending_ids.remove(match_id)
        known_ids.append(match_id)
        account_state["last_pushed_match_id"] = match_id
        summaries.append(f"v2 account={account_id} match_id={match_id}")
        processed += 1

    account_state["known_match_ids"] = known_ids[-(DOTA2_HISTORY_WINDOW * 3) :]
    account_state["pending_match_ids"] = pending_ids
    return summaries


async def run_dota2_check_once(force_refresh: bool = False) -> list[str]:
    if not DOTA2_ENABLED:
        return []
    if not DOTA2_STEAM_API_KEY:
        raise RuntimeError("DOTA2_STEAM_API_KEY is not configured.")
    watched_accounts = _watched_account_ids()
    if not watched_accounts:
        raise RuntimeError("No watched Dota2 accounts are configured.")

    async with _poll_lock:
        await _ensure_name_maps(force_refresh=force_refresh)
        state = _load_state()
        state.setdefault("accounts", {})
        state.setdefault("meta", {})

        if DOTA2_OUTPUT_VERSION == "v2":
            summaries = await _push_v2_match_events_once(state, watched_accounts)
        else:
            summaries: list[str] = []
            for account_id in watched_accounts:
                try:
                    summaries.extend(await _check_account_matches_v1(state, account_id))
                except Exception:
                    logger.exception("Dota2 check failed for account %s", account_id)

        _save_state(state)
        return summaries


async def _wait_for_bot(timeout_seconds: int = 120) -> bool:
    elapsed = 0
    while elapsed < timeout_seconds:
        if pick_bot() is not None:
            return True
        await asyncio.sleep(1)
        elapsed += 1
    return False


async def push_recent_matches_with_openclaw(count: int) -> list[str]:
    if count <= 0:
        return []
    await _ensure_name_maps()
    if not await _wait_for_bot():
        raise RuntimeError("Bot did not connect before v2 backfill.")

    events_by_match: dict[int, dict[str, Any]] = {}
    for account_id in _watched_account_ids():
        recent_matches = await _fetch_recent_matches(account_id, count)
        for history_match in reversed(recent_matches[:count]):
            match_id = history_match.get("match_id")
            match_seq_num = history_match.get("match_seq_num")
            if not isinstance(match_id, int) or not isinstance(match_seq_num, int):
                continue
            bucket = events_by_match.setdefault(
                match_id,
                {
                    "match_seq_num": match_seq_num,
                    "accounts_by_group": {},
                },
            )
            for group_id in _notify_group_ids_for_account(account_id):
                bucket["accounts_by_group"].setdefault(int(group_id), set()).add(str(account_id))

    summaries: list[str] = []
    for match_id in sorted(events_by_match):
        event = events_by_match[match_id]
        detailed_match = await _fetch_sequence_match(event["match_seq_num"])
        if not detailed_match:
            continue
        for group_id, target_account_ids in sorted(event["accounts_by_group"].items()):
            _log_v2_debug(
                "raw_match",
                match_id=match_id,
                group_id=group_id,
                payload=detailed_match,
            )
            message = await _build_v2_group_message(
                detailed_match,
                target_account_ids=target_account_ids,
                group_id=group_id,
            )
            await send_group_text(group_id, message)
            account_names = ",".join(_display_name_for_account(account_id) for account_id in sorted(target_account_ids))
            summaries.append(f"v2-backfill group={group_id} match_id={match_id} accounts={account_names}")
    return summaries
