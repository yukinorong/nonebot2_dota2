from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()

from plugins.dota_guide import build_hero_guide_text, resolve_hero_for_guide


class DotaGuideTests(unittest.IsolatedAsyncioTestCase):
    def test_resolve_hero_for_guide(self) -> None:
        with (
            patch("plugins.dota_guide.load_hero_aliases", return_value={"蓝猫": 17}),
            patch("plugins.dota_guide.load_hero_briefs", return_value={"17": {"display_name": "风暴之灵"}}),
        ):
            resolved = resolve_hero_for_guide("蓝猫怎么玩")
        self.assertTrue(resolved["resolved"])
        self.assertEqual(resolved["hero_id"], 17)

    async def test_build_hero_guide_text(self) -> None:
        with (
            patch("plugins.dota_guide.resolve_hero_for_guide", return_value={"resolved": True, "hero_id": 17, "hero_name": "风暴之灵"}),
            patch(
                "plugins.dota_guide.build_hero_guide_context",
                return_value={
                    "hero_id": 17,
                    "hero_name": "风暴之灵",
                    "current_version": "7.39d",
                    "knowledge_lines": ["英雄：风暴之灵", "定位：爆发中单"],
                    "sources": [
                        {
                            "source_type": "official",
                            "topic_type": "patch",
                            "game_version": "7.39d",
                            "effective_weight": 1.0,
                            "source_title": "7.39d",
                            "source_url": "https://www.dota2.com/patches/739d",
                            "content_text": "风暴之灵前期更怕蓝量压力。",
                        }
                    ],
                },
            ),
            patch("plugins.dota_guide.ask_main", AsyncMock(return_value="专业攻略")) as mocked,
        ):
            answer = await build_hero_guide_text("蓝猫", group_id=1081502166)
        self.assertEqual(answer, "专业攻略")
        prompt = mocked.await_args.args[0]
        self.assertIn("风暴之灵", prompt)
        self.assertIn("7.39d", prompt)
        self.assertIn("风暴之灵前期更怕蓝量压力", prompt)
        self.assertIn("风暴之灵 当前版本攻略", prompt)
        self.assertIn("出装分支：", prompt)
        self.assertIn("不要使用 Markdown", prompt)


if __name__ == "__main__":
    unittest.main()
