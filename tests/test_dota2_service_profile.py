from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()

from plugins.dota2_service import build_player_profile_text


class Dota2ServiceProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_player_profile_text_prompt_uses_chat_sections(self) -> None:
        features = {
            "steam_id": "163926078",
            "sample_size": 50,
            "win_count": 28,
            "loss_count": 22,
            "win_rate": 0.56,
            "style_tags": ["偏核心刷钱"],
            "problem_tags": ["阵亡控制偏差"],
        }
        with (
            patch("plugins.dota2_service.build_player_profile_features", return_value=features),
            patch("plugins.dota2_service._display_name_for_account", return_value="洗洗"),
            patch("plugins.dota2_service.ask_main", AsyncMock(return_value="分析结果")) as mocked,
        ):
            answer = await build_player_profile_text("163926078", group_id=1081502166)

        self.assertEqual(answer, "分析结果")
        prompt = mocked.await_args.kwargs["prompt"] if "prompt" in mocked.await_args.kwargs else mocked.await_args.args[0]
        self.assertIn("Dota2 最近50场打法分析：洗洗", prompt)
        self.assertIn("打法概括：", prompt)
        self.assertIn("改进建议：", prompt)
        self.assertIn("不要使用 Markdown", prompt)


if __name__ == "__main__":
    unittest.main()
