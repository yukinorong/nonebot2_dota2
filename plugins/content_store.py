from __future__ import annotations

import re
from pathlib import Path

from .common import env, format_qq_chat_text, load_env_file

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_VALUES = load_env_file(ENV_FILE)
WORKSPACE_ROOT = Path(env(ENV_VALUES, "QQ_BOT_WORKSPACE", "/home/futunan/data/study_code/game_demo")).resolve()
TODOLIST_PATH = Path(env(ENV_VALUES, "QQ_BOT_TODOLIST_PATH", str(WORKSPACE_ROOT / "todolist.md"))).resolve()
DESCRIPTION_PATH = Path(
    env(
        ENV_VALUES,
        "QQ_BOT_DESCRIPTION_PATH",
        env(ENV_VALUES, "QQ_BOT_VERSION_PATH", str(WORKSPACE_ROOT / "description.md")),
    )
).resolve()


def _assert_allowed_file(path: Path, *, writable: bool) -> None:
    resolved = path.resolve()
    if writable:
        if resolved != TODOLIST_PATH:
            raise RuntimeError("Write access is only allowed for todolist.md")
        return
    if resolved not in {TODOLIST_PATH, DESCRIPTION_PATH}:
        raise RuntimeError("Read access is only allowed for todolist.md and description.md")


def _read_controlled_file(path: Path) -> str:
    _assert_allowed_file(path, writable=False)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _write_todolist(content: str) -> None:
    _assert_allowed_file(TODOLIST_PATH, writable=True)
    TODOLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    TODOLIST_PATH.write_text(content, encoding="utf-8")


def _parse_todolist_items(content: str) -> list[str]:
    items: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("- [ ] "):
            item = line[6:].strip()
            if item:
                items.append(item)
    return items


def _normalize_todolist_item_for_compare(item: str) -> str:
    return re.sub(r"\s+", "", item).strip().lower()


def _build_todolist_content(items: list[str]) -> str:
    normalized_items = [item.strip() for item in items if item.strip()]
    if not normalized_items:
        return "# Todo List\n"
    lines = ["# Todo List", ""]
    lines.extend(f"- [ ] {item}" for item in normalized_items)
    return "\n".join(lines).rstrip() + "\n"


def markdown_to_chat_text(content: str) -> str:
    return format_qq_chat_text(content)


def todo_display_text(content: str) -> str:
    display = markdown_to_chat_text(content)
    if display.startswith("Todo List\n"):
        display = display[len("Todo List\n") :].strip()
    elif display == "Todo List":
        display = ""
    return display or "当前还没有待办功能。"


def read_todo_raw() -> str:
    return _read_controlled_file(TODOLIST_PATH)


def read_todo_display() -> str:
    return todo_display_text(read_todo_raw())


def read_description() -> str:
    description = markdown_to_chat_text(_read_controlled_file(DESCRIPTION_PATH)).strip()
    return description or "当前还没有维护版本说明文件。"


def upsert_todo_item(normalized_item: str) -> str:
    current = read_todo_raw()
    current_items = _parse_todolist_items(current)
    normalized_key = _normalize_todolist_item_for_compare(normalized_item)
    existing_keys = {_normalize_todolist_item_for_compare(item) for item in current_items}
    if not normalized_item or normalized_key in existing_keys:
        return todo_display_text((current or "# Todo List\n").rstrip())
    updated = _build_todolist_content(current_items + [normalized_item])
    if updated != current:
        _write_todolist(updated)
    return todo_display_text(updated.rstrip())
