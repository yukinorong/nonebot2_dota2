from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "dota2_monitor"
CONFIG_FILE = DATA_DIR / "watch_config.json"
ENV_FILE = BASE_DIR / ".env"


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


def _normalize_group_map(raw: Any) -> dict[str, list[int]]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, list[int]] = {}
    for key, value in raw.items():
        account_id = str(key).strip()
        if not account_id:
            continue
        raw_group_ids = value if isinstance(value, list) else [value]
        group_ids: list[int] = []
        seen: set[int] = set()
        for item in raw_group_ids:
            try:
                group_id = int(item)
            except (TypeError, ValueError):
                continue
            if group_id not in seen:
                group_ids.append(group_id)
                seen.add(group_id)
        if group_ids:
            normalized[account_id] = group_ids
    return normalized


def _normalize_nicknames(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        nickname = str(key).strip()
        account_id = str(value).strip()
        if nickname and account_id:
            normalized[nickname] = account_id
    return normalized


def _normalize_config(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "nicknames": _normalize_nicknames(raw.get("nicknames", {})),
        "group_map": _normalize_group_map(raw.get("group_map", {})),
    }


def save_watch_config(config: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_config(config)
    CONFIG_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _migrate_legacy_config() -> dict[str, dict[str, Any]]:
    env_values = _load_env_file()
    try:
        legacy_account_names = json.loads(env_values.get("DOTA2_ACCOUNT_NAMES_JSON", "{}"))
    except json.JSONDecodeError:
        legacy_account_names = {}
    try:
        legacy_group_map = json.loads(env_values.get("DOTA2_NOTIFY_GROUP_MAP_JSON", "{}"))
    except json.JSONDecodeError:
        legacy_group_map = {}

    nicknames: dict[str, str] = {}
    if isinstance(legacy_account_names, dict):
        for account_id, nickname in legacy_account_names.items():
            nickname_str = str(nickname).strip()
            account_id_str = str(account_id).strip()
            if nickname_str and account_id_str:
                nicknames[nickname_str] = account_id_str

    config = {
        "nicknames": nicknames,
        "group_map": _normalize_group_map(legacy_group_map),
    }
    save_watch_config(config)
    return config


def load_watch_config() -> dict[str, dict[str, Any]]:
    if not CONFIG_FILE.exists():
        return _migrate_legacy_config()
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    config = _normalize_config(raw)
    if raw != config:
        save_watch_config(config)
    return config


def list_watch_account_ids() -> list[str]:
    return list(load_watch_config()["group_map"].keys())


def display_name_for_account(account_id: str) -> str:
    config = load_watch_config()
    for nickname, mapped_account_id in config["nicknames"].items():
        if mapped_account_id == account_id:
            return nickname
    return account_id


def list_watched_accounts() -> list[dict[str, str]]:
    return [
        {
            "account_id": account_id,
            "display_name": display_name_for_account(account_id),
        }
        for account_id in load_watch_config()["group_map"].keys()
    ]


def resolve_watched_account(query: str) -> str | None:
    config = load_watch_config()
    account_ids = list(config["group_map"].keys())
    raw = query.strip()
    if not raw:
        return None
    if raw in account_ids:
        return raw
    normalized = "".join(raw.split()).lower()
    exact_name_matches = [
        account_id
        for nickname, account_id in config["nicknames"].items()
        if "".join(nickname.split()).lower() == normalized
    ]
    if len(exact_name_matches) == 1:
        return exact_name_matches[0]
    fuzzy_matches = [
        account_id
        for nickname, account_id in config["nicknames"].items()
        if normalized in "".join(nickname.split()).lower() or normalized in account_id
    ]
    return fuzzy_matches[0] if len(set(fuzzy_matches)) == 1 else None


def group_ids_for_account(account_id: str, *, default_group_id: int | None = None) -> list[int]:
    group_ids = load_watch_config()["group_map"].get(account_id, [])
    if group_ids:
        return group_ids
    return [default_group_id] if default_group_id is not None else []


def list_group_accounts(group_id: int) -> list[dict[str, str]]:
    config = load_watch_config()
    accounts: list[dict[str, str]] = []
    for account_id, group_ids in config["group_map"].items():
        if group_id in group_ids:
            accounts.append(
                {
                    "account_id": account_id,
                    "display_name": display_name_for_account(account_id),
                }
            )
    accounts.sort(key=lambda item: item["display_name"])
    return accounts


def add_watch_account(nickname: str, account_id: str, group_id: int) -> tuple[bool, str]:
    clean_nickname = nickname.strip()
    clean_account_id = account_id.strip()
    if not clean_nickname or not clean_account_id.isdigit():
        return False, "用法: /add <昵称> <steamID>"

    config = load_watch_config()
    nicknames = dict(config["nicknames"])
    group_map = _normalize_group_map(config["group_map"])

    existing_account_id = nicknames.get(clean_nickname)
    if existing_account_id and existing_account_id != clean_account_id:
        return False, f"添加失败：昵称“{clean_nickname}”已绑定到 {existing_account_id}。"

    nicknames[clean_nickname] = clean_account_id
    group_ids = group_map.get(clean_account_id, [])
    if group_id in group_ids:
        save_watch_config({"nicknames": nicknames, "group_map": group_map})
        return False, f"已存在：{clean_nickname} -> {clean_account_id}，本群已在监听。"

    group_ids.append(group_id)
    group_map[clean_account_id] = group_ids
    save_watch_config({"nicknames": nicknames, "group_map": group_map})
    return True, f"添加成功：{clean_nickname} -> {clean_account_id}，已加入本群监听。"
