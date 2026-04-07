from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path

group_memory_store = importlib.import_module("plugins.group_memory_store")
openclaw_group_memory = importlib.import_module("plugins.openclaw_group_memory")


class OpenClawGroupMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self._old_workspace_root = openclaw_group_memory.GROUP_OPENCLAW_WORKSPACE_ROOT
        self._old_legacy_dir = openclaw_group_memory.LEGACY_GROUP_MEMORY_DIR
        self._old_config_path = openclaw_group_memory.OPENCLAW_CONFIG_PATH
        self._old_dota_knowledge_dir = openclaw_group_memory._DOTA_KNOWLEDGE_DIR
        self._old_dota_derived_dir = openclaw_group_memory._DOTA_DERIVED_DIR
        self._old_memory_db_override = group_memory_store.group_memory_db_path()
        openclaw_group_memory.GROUP_OPENCLAW_WORKSPACE_ROOT = self.temp_path / "workspaces"
        openclaw_group_memory.LEGACY_GROUP_MEMORY_DIR = self.temp_path / "legacy"
        openclaw_group_memory.OPENCLAW_CONFIG_PATH = self.temp_path / "openclaw.json"
        group_memory_store.configure_group_memory_store_for_tests(
            db_path=self.temp_path / "group-memory.sqlite3"
        )
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
        self._old_read_description = openclaw_group_memory.read_description
        openclaw_group_memory.read_description = lambda: "支持群聊、联网问答、Dota2 查询"
        dota_dir = self.temp_path / "data" / "dota_knowledge"
        derived_dir = dota_dir / "derived"
        derived_dir.mkdir(parents=True, exist_ok=True)
        openclaw_group_memory._DOTA_KNOWLEDGE_DIR = dota_dir
        openclaw_group_memory._DOTA_DERIVED_DIR = derived_dir
        (derived_dir / "meta_briefs.json").write_text('{"updated_at":"now"}\n', encoding="utf-8")
        (derived_dir / "hero_briefs.json").write_text('{"17":{"display_name":"风暴之灵"}}\n', encoding="utf-8")
        (dota_dir / "hero_aliases.json").write_text('{"蓝猫":17}\n', encoding="utf-8")

    def tearDown(self) -> None:
        openclaw_group_memory.GROUP_OPENCLAW_WORKSPACE_ROOT = self._old_workspace_root
        openclaw_group_memory.LEGACY_GROUP_MEMORY_DIR = self._old_legacy_dir
        openclaw_group_memory.OPENCLAW_CONFIG_PATH = self._old_config_path
        openclaw_group_memory._DOTA_KNOWLEDGE_DIR = self._old_dota_knowledge_dir
        openclaw_group_memory._DOTA_DERIVED_DIR = self._old_dota_derived_dir
        openclaw_group_memory.read_description = self._old_read_description
        group_memory_store.configure_group_memory_store_for_tests(db_path=None)
        group_memory_store.reset_group_memory_store()
        self.temp_dir.cleanup()

    def test_render_memory_markdown_groups_items(self) -> None:
        markdown = openclaw_group_memory.render_memory_markdown(
            [
                {
                    "type": "group_lexicon",
                    "subject": "user:四万",
                    "canonical": "四万",
                    "aliases": ["四万"],
                    "content": "四万是固定外号",
                    "priority": "medium",
                },
                {
                    "type": "bot_preference",
                    "subject": "robot",
                    "canonical": "回复简短",
                    "aliases": [],
                    "content": "机器人回复尽量简短",
                    "priority": "high",
                },
            ]
        )
        self.assertIn("# 机器人偏好", markdown)
        self.assertIn("# 群内词典", markdown)
        self.assertIn("- 机器人回复尽量简短", markdown)

    def test_sync_openclaw_group_agents_adds_chat_and_memory_agents(self) -> None:
        changed = openclaw_group_memory.sync_openclaw_group_agents([1081502166])
        self.assertTrue(changed)
        config = json.loads(openclaw_group_memory.OPENCLAW_CONFIG_PATH.read_text(encoding="utf-8"))
        agent_ids = {agent["id"] for agent in config["agents"]["list"]}
        self.assertIn("qq_group_1081502166", agent_ids)
        self.assertIn("qq_group_1081502166_memory", agent_ids)
        self.assertTrue(config["tools"]["web"]["search"]["enabled"])
        chat_agent = next(agent for agent in config["agents"]["list"] if agent["id"] == "qq_group_1081502166")
        self.assertNotIn("memory_search", chat_agent["tools"]["allow"])
        self.assertNotIn("memory_get", chat_agent["tools"]["allow"])
        workspace = openclaw_group_memory.group_workspace_dir(1081502166)
        self.assertTrue((workspace / openclaw_group_memory.BOT_CAPABILITIES_FILENAME).exists())
        self.assertTrue((workspace / openclaw_group_memory.DOTA_META_BRIEFS_FILENAME).exists())
        self.assertTrue((workspace / openclaw_group_memory.DOTA_HERO_BRIEFS_FILENAME).exists())
        self.assertTrue((workspace / openclaw_group_memory.DOTA_HERO_ALIASES_FILENAME).exists())
        self.assertTrue((workspace / openclaw_group_memory.DOTA_AGENT_GUIDE_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
