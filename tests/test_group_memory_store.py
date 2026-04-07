from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugins.group_memory_store import (
    configure_group_memory_store_for_tests,
    reset_group_memory_store,
    retrieve_group_memories,
    sync_group_memory_items,
)


class GroupMemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        configure_group_memory_store_for_tests(db_path=self.temp_path / "group-memory.sqlite3")
        reset_group_memory_store()

    def tearDown(self) -> None:
        reset_group_memory_store()
        configure_group_memory_store_for_tests(db_path=None)
        self.temp_dir.cleanup()

    def test_retrieve_group_memories_prefers_exact_alias_hits(self) -> None:
        sync_group_memory_items(
            1081502166,
            [
                {
                    "id": "alias-robot",
                    "type": "group_lexicon",
                    "subject": "robot",
                    "canonical": "机器人",
                    "aliases": ["小猪包仔"],
                    "content": "群友称机器人为“小猪包仔”。",
                    "priority": "high",
                    "enabled": True,
                    "updated_at": 100,
                },
                {
                    "id": "ctx-dota",
                    "type": "durable_context",
                    "subject": "topic:dota2",
                    "canonical": "群聊主题",
                    "aliases": ["Dota2"],
                    "content": "群里主要聊 Dota2。",
                    "priority": "low",
                    "enabled": True,
                    "updated_at": 90,
                },
            ],
        )
        matches = retrieve_group_memories(1081502166, "小猪包仔", limit=3)
        self.assertEqual(matches[0]["id"], "alias-robot")
        self.assertIn("exact", matches[0]["reasons"])

    def test_retrieve_group_memories_hits_bot_preference_by_content(self) -> None:
        sync_group_memory_items(
            1081502166,
            [
                {
                    "id": "pref-short",
                    "type": "bot_preference",
                    "subject": "robot",
                    "canonical": "回复简短",
                    "aliases": ["短一点"],
                    "content": "机器人回复尽量简短。",
                    "priority": "high",
                    "enabled": True,
                    "updated_at": 100,
                }
            ],
        )
        matches = retrieve_group_memories(1081502166, "回复短一点", limit=3)
        self.assertEqual(matches[0]["id"], "pref-short")
        self.assertTrue({"substring", "content"} & set(matches[0]["reasons"]))

    def test_retrieve_group_memories_fts_fallback_returns_match(self) -> None:
        sync_group_memory_items(
            1081502166,
            [
                {
                    "id": "ctx-format",
                    "type": "durable_context",
                    "subject": "group",
                    "canonical": "输出格式",
                    "aliases": [],
                    "content": "这个群更喜欢纯文本聊天格式，不要 Markdown。",
                    "priority": "medium",
                    "enabled": True,
                    "updated_at": 100,
                }
            ],
        )
        matches = retrieve_group_memories(1081502166, "Markdown", limit=3)
        self.assertEqual(matches[0]["id"], "ctx-format")
        self.assertIn("fts", matches[0]["reasons"])

    def test_retrieve_group_memories_limits_to_top_three(self) -> None:
        items = []
        for index in range(5):
            items.append(
                {
                    "id": f"alias-{index}",
                    "type": "group_lexicon",
                    "subject": f"user:{index}",
                    "canonical": f"外号{index}",
                    "aliases": ["四万"],
                    "content": f"四万也可能指向外号{index}。",
                    "priority": "medium",
                    "enabled": True,
                    "updated_at": 100 - index,
                }
            )
        sync_group_memory_items(1081502166, items)
        matches = retrieve_group_memories(1081502166, "四万", limit=3)
        self.assertEqual(len(matches), 3)

    def test_retrieve_group_memories_does_not_return_unmatched_high_priority_items(self) -> None:
        sync_group_memory_items(
            1081502166,
            [
                {
                    "id": "name-robot",
                    "type": "bot_preference",
                    "subject": "robot",
                    "canonical": "机器人名字",
                    "aliases": ["小爪"],
                    "content": "群友称呼机器人为小爪",
                    "priority": "high",
                    "enabled": True,
                    "updated_at": 100,
                }
            ],
        )
        self.assertEqual(retrieve_group_memories(1081502166, "回复短一点", limit=3), [])


if __name__ == "__main__":
    unittest.main()
