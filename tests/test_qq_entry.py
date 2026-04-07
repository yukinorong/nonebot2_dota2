from __future__ import annotations

import importlib
import unittest
from types import SimpleNamespace

import nonebot

nonebot.init()
qq_entry = importlib.import_module("plugins.qq_entry")


class QQEntryTests(unittest.IsolatedAsyncioTestCase):
    def test_extract_plaintext_bot_prefix_prompt(self) -> None:
        self.assertEqual(
            qq_entry._extract_plaintext_bot_prefix_prompt("@机器人   /dota_profile 洗洗"),
            "/dota_profile 洗洗",
        )
        self.assertEqual(
            qq_entry._extract_plaintext_bot_prefix_prompt("  ＠机器人  北京天气"),
            "北京天气",
        )
        self.assertIsNone(qq_entry._extract_plaintext_bot_prefix_prompt("机器人 你好"))

    def test_extract_bot_prompt_for_plaintext_prefix(self) -> None:
        event = SimpleNamespace(
            to_me=False,
            get_plaintext=lambda: "@机器人   /news 科技",
        )
        self.assertEqual(qq_entry._extract_bot_prompt(event), "/news 科技")

    def test_extract_bot_prompt_for_real_at(self) -> None:
        event = SimpleNamespace(
            to_me=True,
            get_plaintext=lambda: "/dota_profile 戴套",
        )
        self.assertEqual(qq_entry._extract_bot_prompt(event), "/dota_profile 戴套")

    async def test_has_plaintext_bot_prefix_true(self) -> None:
        class FakeGroupEvent:
            def get_plaintext(self) -> str:
                return "@机器人  /dota_profile 戴套"

        with unittest.mock.patch.object(qq_entry, "GroupMessageEvent", FakeGroupEvent):
            self.assertTrue(await qq_entry._has_plaintext_bot_prefix(FakeGroupEvent()))

    async def test_has_plaintext_bot_prefix_false(self) -> None:
        class FakeGroupEvent:
            def get_plaintext(self) -> str:
                return "戴套今天又菜了"

        with unittest.mock.patch.object(qq_entry, "GroupMessageEvent", FakeGroupEvent):
            self.assertFalse(await qq_entry._has_plaintext_bot_prefix(FakeGroupEvent()))

    def test_text_bot_prefixed_command_is_still_command(self) -> None:
        prompt = qq_entry._extract_plaintext_bot_prefix_prompt("@机器人   /todo 新增功能")
        self.assertTrue(qq_entry._is_command_message(prompt or ""))


if __name__ == "__main__":
    unittest.main()
