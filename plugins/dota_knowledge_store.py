from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
DOTA_MONITOR_DIR = BASE_DIR / "data" / "dota2_monitor"
DEFAULT_DATA_DIR = BASE_DIR / "data" / "dota_knowledge"

HERO_CACHE_FILE = DOTA_MONITOR_DIR / "heroes.json"
ITEM_CACHE_FILE = DOTA_MONITOR_DIR / "items.json"

META_FILE = "meta.json"
SYNC_STATE_FILE = "sync_state.json"
HERO_STATS_FILE = "hero_stats.json"
HERO_MATCHUPS_FILE = "hero_matchups.json"
HERO_ITEM_POPULARITY_FILE = "hero_item_popularity.json"
HERO_DURATIONS_FILE = "hero_durations.json"
HERO_ALIASES_FILE = "hero_aliases.json"
ITEM_ALIASES_FILE = "item_aliases.json"
DERIVED_HERO_BRIEFS_FILE = "derived/hero_briefs.json"
DERIVED_META_BRIEFS_FILE = "derived/meta_briefs.json"
PATCH_NOTES_CACHE_FILE = "patch_notes_cache.json"

CURATED_HERO_ALIASES: dict[str, list[str]] = {
    "1": ["敌法", "敌法师", "am", "antimage", "anti mage"],
    "8": ["主宰", "剑圣", "jugg", "juggernaut"],
    "11": ["影魔", "sf", "shadow fiend"],
    "13": ["帕克", "puck"],
    "17": ["蓝猫", "storm", "storm spirit"],
    "19": ["小小", "tiny"],
    "35": ["火枪", "sniper"],
    "39": ["qop", "queen of pain"],
    "41": ["虚空", "void", "faceless void"],
    "42": ["骷髅王", "wk", "wraith king"],
    "46": ["圣堂", "ta", "templar assassin"],
    "47": ["毒龙", "viper"],
    "51": ["发条", "clock", "clockwerk"],
    "53": ["先知", "furion", "nature's prophet", "natures prophet"],
    "54": ["小狗", "lifestealer"],
    "64": ["双头龙", "jakiro"],
    "68": ["冰魂", "aa", "ancient apparition"],
    "71": ["白牛", "bara", "spirit breaker"],
    "76": ["黑鸟", "od", "outworld destroyer"],
    "78": ["熊猫", "brewmaster"],
    "79": ["毒狗", "sd", "shadow demon"],
    "81": ["ck", "chaos knight"],
    "87": ["萨尔", "disruptor"],
    "89": ["小娜迦", "naga", "naga siren"],
    "90": ["光法", "kotl", "keeper of the light"],
    "93": ["小鱼", "小鱼人", "slark"],
    "98": ["timber", "timbersaw"],
    "99": ["刚背", "bristleback"],
    "100": ["海民", "tusk"],
    "101": ["天怒", "sky", "skywrath mage"],
    "102": ["abba", "abaddon"],
    "103": ["大牛", "elder titan"],
    "106": ["火猫", "ember", "ember spirit"],
    "107": ["土猫", "earth spirit"],
    "112": ["wyvern", "winter wyvern"],
    "114": ["大圣", "monkey king"],
    "120": ["滚滚", "pangolier"],
    "126": ["紫猫", "void spirit"],
    "128": ["奶奶龙", "snapfire"],
}

CURATED_ITEM_ALIASES: dict[str, list[str]] = {
    "1": ["跳刀", "blink", "blink dagger"],
    "41": ["魔瓶", "bottle"],
    "50": ["相位", "相位鞋", "phase boots"],
    "63": ["假腿", "power treads"],
    "65": ["点金", "hand of midas"],
    "108": ["a杖", "阿哈利姆神杖", "aghanims scepter", "aghanim's scepter"],
    "116": ["bkb", "黑皇杖", "black king bar"],
    "123": ["林肯", "linken", "linken's sphere", "linkens sphere"],
    "125": ["先锋盾", "vanguard"],
    "127": ["刃甲", "blade mail"],
    "135": ["mkb", "金箍棒", "monkey king bar"],
    "137": ["辉耀", "radiance"],
    "139": ["蝴蝶", "butterfly"],
    "141": ["大炮", "daedalus"],
    "145": ["狂战", "battle fury"],
    "154": ["散慧对剑", "sny", "sange and yasha"],
    "156": ["撒旦", "satanic"],
    "168": ["黯灭", "deso", "desolator"],
    "174": ["散失", "diffusal", "diffusal blade"],
    "176": ["虚灵刀", "ethereal", "ethereal blade"],
    "190": ["纷争", "veil", "veil of discord"],
    "223": ["陨星锤", "meteor hammer"],
    "226": ["莲花", "lotus", "lotus orb"],
    "231": ["卫士胫甲", "guardian greaves"],
    "235": ["玲珑心", "octarine", "octarine core"],
    "247": ["银月", "moon shard"],
    "256": ["永恒之盘", "aeon disk"],
    "263": ["大推推", "hurricane pike"],
    "96": ["羊刀", "scythe", "scythe of vyse"],
    "98": ["紫苑", "orchid", "orchid malevolence"],
    "100": ["吹风", "eul", "eul's", "euls", "eul's scepter", "euls scepter"],
    "102": ["推推", "force", "force staff"],
}


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


def knowledge_data_dir() -> Path:
    return Path(_env("DOTA2_KNOWLEDGE_DATA_DIR", str(DEFAULT_DATA_DIR))).resolve()


def ensure_knowledge_dirs() -> None:
    directory = knowledge_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "derived").mkdir(parents=True, exist_ok=True)


def knowledge_path(relative: str) -> Path:
    return knowledge_data_dir() / relative


def load_json(relative: str, default: Any) -> Any:
    path = knowledge_path(relative)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(relative: str, payload: Any) -> None:
    ensure_knowledge_dirs()
    path = knowledge_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_lookup_text(text: str) -> str:
    normalized = text.lower().strip()
    normalized = normalized.replace("·", "")
    normalized = normalized.replace("'", "")
    normalized = normalized.replace("_", "")
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)
    return normalized


def _load_id_name_map(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[int, str] = {}
    for key, value in raw.items():
        if str(key).isdigit() and isinstance(value, str) and value.strip():
            result[int(key)] = value.strip()
    return result


def load_existing_hero_names() -> dict[int, str]:
    return _load_id_name_map(HERO_CACHE_FILE)


def load_existing_item_names() -> dict[int, str]:
    return _load_id_name_map(ITEM_CACHE_FILE)


def _normalized_variants(text: str) -> set[str]:
    raw = text.strip()
    if not raw:
        return set()
    variants = {raw, raw.lower(), normalize_lookup_text(raw)}
    if raw.startswith("npc_dota_hero_"):
        short = raw[len("npc_dota_hero_") :]
        variants.add(short)
        variants.add(short.replace("_", " "))
        variants.add(normalize_lookup_text(short))
    return {variant for variant in variants if variant}


def build_hero_aliases(hero_stats: list[dict[str, Any]]) -> dict[str, int]:
    existing_names = load_existing_hero_names()
    aliases: dict[str, int] = {}
    for hero in hero_stats:
        hero_id = int(hero.get("id", 0) or 0)
        if hero_id <= 0:
            continue
        sources = [
            existing_names.get(hero_id, ""),
            str(hero.get("localized_name", "") or ""),
            str(hero.get("name", "") or ""),
        ]
        for source in sources:
            for variant in _normalized_variants(source):
                aliases[variant] = hero_id
        for alias in CURATED_HERO_ALIASES.get(str(hero_id), []):
            aliases[normalize_lookup_text(alias)] = hero_id
    return dict(sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True))


def build_item_aliases(item_names: dict[int, str]) -> dict[str, int]:
    aliases: dict[str, int] = {}
    for item_id, item_name in item_names.items():
        for variant in _normalized_variants(item_name):
            aliases[variant] = item_id
    for item_id, alias_list in CURATED_ITEM_ALIASES.items():
        if not item_id.isdigit():
            continue
        item_int = int(item_id)
        for alias in alias_list:
            aliases[normalize_lookup_text(alias)] = item_int
    return dict(sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True))


def load_hero_aliases() -> dict[str, int]:
    return {str(key): int(value) for key, value in load_json(HERO_ALIASES_FILE, {}).items() if str(value).isdigit()}


def load_item_aliases() -> dict[str, int]:
    return {str(key): int(value) for key, value in load_json(ITEM_ALIASES_FILE, {}).items() if str(value).isdigit()}


def load_hero_briefs() -> dict[str, Any]:
    raw = load_json(DERIVED_HERO_BRIEFS_FILE, {})
    return raw if isinstance(raw, dict) else {}


def load_meta_briefs() -> dict[str, Any]:
    raw = load_json(DERIVED_META_BRIEFS_FILE, {})
    return raw if isinstance(raw, dict) else {}
