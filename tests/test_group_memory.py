from __future__ import annotations

import importlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot
from tests.fake_group_chat_redis import FakeGroupChatRedis

from plugins.group_memory_store import (
    configure_group_memory_store_for_tests,
    reset_group_memory_store,
)
from plugins.group_chat_store import (
    configure_group_chat_store_for_tests,
    record_user_group_event,
    reset_group_chat_store as reset_group_chat_context_store,
)

nonebot.init()
group_memory = importlib.import_module("plugins.group_memory")
openclaw_group_memory = importlib.import_module("plugins.openclaw_group_memory")


class GroupMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        configure_group_chat_store_for_tests(redis_client=FakeGroupChatRedis())
        reset_group_chat_context_store()
        configure_group_memory_store_for_tests(db_path=self.temp_path / "group-memory.sqlite3")
        reset_group_memory_store()
        self._old_workspace_root = openclaw_group_memory.GROUP_OPENCLAW_WORKSPACE_ROOT
        self._old_legacy_dir = openclaw_group_memory.LEGACY_GROUP_MEMORY_DIR
        self._old_config_path = openclaw_group_memory.OPENCLAW_CONFIG_PATH
        openclaw_group_memory.GROUP_OPENCLAW_WORKSPACE_ROOT = self.temp_path / "workspaces"
        openclaw_group_memory.LEGACY_GROUP_MEMORY_DIR = self.temp_path / "legacy"
        openclaw_group_memory.OPENCLAW_CONFIG_PATH = self.temp_path / "openclaw.json"
        openclaw_group_memory.OPENCLAW_CONFIG_PATH.write_text(
            json.dumps(
                {
                    "agents": {
                        "defaults": {"model": {"primary": "moonshot/kimi-k2.5"}},
                        "list": [{"id": "qq_bot", "model": {"primary": "moonshot/kimi-k2.5"}}],
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        openclaw_group_memory.GROUP_OPENCLAW_WORKSPACE_ROOT = self._old_workspace_root
        openclaw_group_memory.LEGACY_GROUP_MEMORY_DIR = self._old_legacy_dir
        openclaw_group_memory.OPENCLAW_CONFIG_PATH = self._old_config_path
        self.temp_dir.cleanup()
        reset_group_chat_context_store()
        reset_group_memory_store()
        configure_group_memory_store_for_tests(db_path=None)

    def test_read_group_memory_reads_rendered_memory_from_workspace(self) -> None:
        items = [
            {
                "type": "bot_preference",
                "subject": "robot",
                "canonical": "回复简短",
                "aliases": [],
                "content": "机器人回复尽量简短",
                "priority": "high",
            },
            {
                "type": "group_lexicon",
                "subject": "user:四万",
                "canonical": "四万",
                "aliases": ["四万"],
                "content": "四万是固定外号",
                "priority": "medium",
            },
        ]
        openclaw_group_memory.write_memory_items(1081502166, items)
        openclaw_group_memory.write_memory_markdown(1081502166, items)
        content = group_memory.read_group_memory(1081502166)
        self.assertIn("# 机器人偏好", content)
        self.assertIn("- 四万是固定外号", content)

    async def test_build_group_memory_extracts_candidates_and_merges_items(self) -> None:
        now = time.time()
        openclaw_group_memory.write_memory_items(
            1081502166,
            [
                {
                    "type": "durable_context",
                    "subject": "topic:dota2",
                    "canonical": "群聊主题",
                    "aliases": ["Dota2", "刀塔"],
                    "content": "群里主要聊 Dota2",
                    "priority": "medium",
                }
            ],
        )
        openclaw_group_memory.write_memory_markdown(
            1081502166,
            [
                {
                    "type": "durable_context",
                    "subject": "topic:dota2",
                    "canonical": "群聊主题",
                    "aliases": ["Dota2", "刀塔"],
                    "content": "群里主要聊 Dota2",
                    "priority": "medium",
                }
            ],
        )
        record_user_group_event(1081502166, "老王", "以后机器人回复短一点，大家都叫他四万", timestamp=now)
        responses = [
            json.dumps(
                {
                    "candidates": [
                        {
                            "type": "bot_preference",
                            "subject": "robot",
                            "canonical": "回复简短",
                            "aliases": ["短一点"],
                            "content": "机器人回复尽量简短",
                            "priority": "high",
                        },
                        {
                            "type": "group_lexicon",
                            "subject": "user:四万",
                            "canonical": "四万",
                            "aliases": ["四万"],
                            "content": "四万是固定外号",
                            "priority": "medium",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "items": [
                        {
                            "type": "bot_preference",
                            "subject": "robot",
                            "canonical": "回复简短",
                            "aliases": ["短一点"],
                            "content": "机器人回复尽量简短",
                            "priority": "high",
                        },
                        {
                            "type": "group_lexicon",
                            "subject": "user:四万",
                            "canonical": "四万",
                            "aliases": ["四万"],
                            "content": "四万是固定外号",
                            "priority": "medium",
                        },
                        {
                            "type": "durable_context",
                            "subject": "topic:dota2",
                            "canonical": "群聊主题",
                            "aliases": ["Dota2", "刀塔"],
                            "content": "群里主要聊 Dota2",
                            "priority": "medium",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
        ]
        with patch.object(group_memory, "ask_main", AsyncMock(side_effect=responses)) as mocked:
            items, rendered = await group_memory.build_group_memory(1081502166)
        self.assertEqual([item["type"] for item in items], ["bot_preference", "group_lexicon", "durable_context"])
        self.assertEqual(items[0]["canonical"], "回复简短")
        self.assertEqual(items[1]["aliases"], ["四万"])
        self.assertIn("# 机器人偏好", rendered)
        self.assertIn("- 四万是固定外号", rendered)
        first_call = mocked.await_args_list[0]
        second_call = mocked.await_args_list[1]
        first_prompt = first_call.kwargs.get("prompt") or first_call.args[0]
        second_prompt = second_call.kwargs.get("prompt") or second_call.args[0]
        self.assertIn("目标不是总结群聊", first_prompt)
        self.assertIn("只允许输出三类候选项", first_prompt)
        self.assertIn('"subject":"robot|group|user:<name>|topic:<name>"', first_prompt)
        self.assertIn("大家都叫他四万", first_prompt)
        self.assertIn("结构化长期记忆项列表", second_prompt)

    async def test_build_group_memory_skips_merge_when_context_has_no_candidates(self) -> None:
        now = time.time()
        record_user_group_event(1081502166, "老王", "今天下雨了", timestamp=now)
        with patch.object(
            group_memory,
            "ask_main",
            AsyncMock(return_value=json.dumps({"candidates": []}, ensure_ascii=False)),
        ) as mocked:
            items, rendered = await group_memory.build_group_memory(1081502166)
        self.assertEqual(items, [])
        self.assertEqual(rendered, "")
        self.assertEqual(mocked.await_count, 1)


if __name__ == "__main__":
    unittest.main()
