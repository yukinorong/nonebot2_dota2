from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot


nonebot.init()

qq_bridge = importlib.import_module("plugins.qq_bridge")


class QQBridgeRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.todolist_path = self.root / "todolist.md"
        self.description_path = self.root / "description.md"
        self.todolist_path.write_text("# Todo List\n\n", encoding="utf-8")
        self.description_path.write_text(
            "我目前主要提供 Dota2 战绩播报功能，会自动监控指定账号的比赛并在新比赛结束后把战绩推送到群里，包括英雄、胜负、KDA、比赛时长等信息，还会生成一段偏吹捧或吐槽的群聊风格点评。",
            encoding="utf-8",
        )

        self.path_patches = [
            patch.object(qq_bridge, "WORKSPACE_ROOT", self.root),
            patch.object(qq_bridge, "TODOLIST_PATH", self.todolist_path),
            patch.object(qq_bridge, "DESCRIPTION_PATH", self.description_path),
        ]
        for item in self.path_patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_feature_request_adds_new_item(self) -> None:
        responses = iter(
            [
                "feature_request",
                json.dumps(
                    {
                        "todolist": "# Todo List\n\n- [ ] 增加查看某个人最近一把战绩的功能"
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        with patch.object(qq_bridge, "ask_openclaw", AsyncMock(side_effect=lambda *args, **kwargs: next(responses))):
            route, answer = await qq_bridge.route_group_prompt(
                "增加一个功能，查看某个人的最近一把战绩",
                group_id=1081502166,
                message_id=1,
            )

        self.assertEqual(route, "feature_request")
        self.assertEqual(answer, "- [ ] 增加查看某个人最近一把战绩的功能")
        self.assertIn("最近一把战绩", self.todolist_path.read_text(encoding="utf-8"))

    async def test_feature_request_merges_duplicate_item(self) -> None:
        self.todolist_path.write_text(
            "# Todo List\n\n- [ ] 增加查看某个人最近一把战绩的功能\n",
            encoding="utf-8",
        )
        responses = iter(
            [
                "feature_request",
                json.dumps(
                    {
                        "todolist": "# Todo List\n\n- [ ] 增加查看某个人最近一把战绩的功能"
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        with patch.object(qq_bridge, "ask_openclaw", AsyncMock(side_effect=lambda *args, **kwargs: next(responses))):
            route, answer = await qq_bridge.route_group_prompt(
                "再加一个功能，想看某个人最新一场战绩",
                group_id=1081502166,
                message_id=2,
            )

        self.assertEqual(route, "feature_request")
        self.assertEqual(answer, "- [ ] 增加查看某个人最近一把战绩的功能")
        self.assertEqual(self.todolist_path.read_text(encoding="utf-8").count("- [ ]"), 1)

    async def test_version_query_returns_file_content(self) -> None:
        with patch.object(qq_bridge, "ask_openclaw", AsyncMock(return_value="version_query")):
            route, answer = await qq_bridge.route_group_prompt(
                "你有什么功能",
                group_id=1081502166,
                message_id=3,
            )

        self.assertEqual(route, "version_query")
        self.assertEqual(answer, self.version_path.read_text(encoding="utf-8"))

    async def test_version_query_falls_back_when_file_missing(self) -> None:
        self.description_path.write_text("", encoding="utf-8")
        with patch.object(qq_bridge, "ask_openclaw", AsyncMock(return_value="version_query")):
            route, answer = await qq_bridge.route_group_prompt(
                "你现在支持哪些功能",
                group_id=1081502166,
                message_id=4,
            )

        self.assertEqual(route, "version_query")
        self.assertEqual(answer, "当前还没有维护版本说明文件。")

    async def test_bot_abuse_returns_short_rebuttal(self) -> None:
        responses = iter(["bot_abuse", "你这点火力也就够给键盘热身 😏"])
        with patch.object(qq_bridge, "ask_openclaw", AsyncMock(side_effect=lambda *args, **kwargs: next(responses))):
            route, answer = await qq_bridge.route_group_prompt(
                "你可真蠢",
                group_id=1081502166,
                message_id=5,
            )

        self.assertEqual(route, "bot_abuse")
        self.assertEqual(answer, "你这点火力也就够给键盘热身 😏")

    async def test_bot_abuse_collapses_multiline_output(self) -> None:
        responses = iter(["bot_abuse", "嘴挺硬。\n但水平还是那样 🙃"])
        with patch.object(qq_bridge, "ask_openclaw", AsyncMock(side_effect=lambda *args, **kwargs: next(responses))):
            route, answer = await qq_bridge.route_group_prompt(
                "你好菜",
                group_id=1081502166,
                message_id=6,
            )

        self.assertEqual(route, "bot_abuse")
        self.assertEqual(answer, "嘴挺硬。 但水平还是那样 🙃")

    async def test_web_answerable_returns_live_answer(self) -> None:
        responses = iter(["web_answerable", "北京今天多云，气温大约 18 到 28 度。"])
        with patch.object(qq_bridge, "ask_openclaw", AsyncMock(side_effect=lambda *args, **kwargs: next(responses))):
            route, answer = await qq_bridge.route_group_prompt(
                "今天北京天气怎么样",
                group_id=1081502166,
                message_id=7,
            )

        self.assertEqual(route, "web_answerable")
        self.assertEqual(answer, "北京今天多云，气温大约 18 到 28 度。")

    async def test_web_answerable_defaults_when_model_returns_empty(self) -> None:
        responses = iter(["web_answerable", ""])
        with patch.object(qq_bridge, "ask_openclaw", AsyncMock(side_effect=lambda *args, **kwargs: next(responses))):
            route, answer = await qq_bridge.route_group_prompt(
                "诸葛亮怎么死的",
                group_id=1081502166,
                message_id=8,
            )

        self.assertEqual(route, "web_answerable")
        self.assertEqual(answer, "我收到消息了，但这次没组织出有效回复。")

    async def test_smalltalk_returns_brief_reply(self) -> None:
        responses = iter(["smalltalk", "你好呀，今天也挺适合开黑的 👋"])
        with patch.object(qq_bridge, "ask_openclaw", AsyncMock(side_effect=lambda *args, **kwargs: next(responses))):
            route, answer = await qq_bridge.route_group_prompt(
                "你好啊",
                group_id=1081502166,
                message_id=9,
            )

        self.assertEqual(route, "smalltalk")
        self.assertEqual(answer, "你好呀，今天也挺适合开黑的 👋")

    async def test_smalltalk_classifier_falls_back_from_noisy_label(self) -> None:
        responses = iter(["分类结果: smalltalk", "来都来了，先喝口水再说 😄"])
        with patch.object(qq_bridge, "ask_openclaw", AsyncMock(side_effect=lambda *args, **kwargs: next(responses))):
            route, answer = await qq_bridge.route_group_prompt(
                "哈哈哈哈",
                group_id=1081502166,
                message_id=10,
            )

        self.assertEqual(route, "smalltalk")
        self.assertEqual(answer, "来都来了，先喝口水再说 😄")

    async def test_match_push_returns_latest_match_text(self) -> None:
        with (
            patch.object(qq_bridge, "ask_openclaw", AsyncMock(return_value="match_push")),
            patch.object(qq_bridge, "resolve_watched_account", return_value="163926078"),
            patch.object(
                qq_bridge,
                "build_latest_match_push_text",
                AsyncMock(return_value="洗洗这把赢了，帕克 12/2/18，直接把对面中路打成教学局。"),
            ),
        ):
            route, answer = await qq_bridge.route_group_prompt(
                "发一下洗洗的最新战绩",
                group_id=1081502166,
                message_id=11,
            )

        self.assertEqual(route, "match_push")
        self.assertIn("洗洗这把赢了", answer)

    async def test_match_push_reports_when_account_not_found(self) -> None:
        with (
            patch.object(qq_bridge, "ask_openclaw", AsyncMock(return_value="match_push")),
            patch.object(qq_bridge, "resolve_watched_account", return_value=None),
            patch.object(qq_bridge, "_extract_match_push_account", AsyncMock(return_value=None)),
            patch.object(
                qq_bridge,
                "list_watched_accounts",
                return_value=[
                    {"account_id": "163926078", "display_name": "洗洗"},
                    {"account_id": "162255543", "display_name": "四万"},
                ],
            ),
        ):
            route, answer = await qq_bridge.route_group_prompt(
                "发一下老王的最新战绩",
                group_id=1081502166,
                message_id=12,
            )

        self.assertEqual(route, "match_push")
        self.assertEqual(answer, "我没识别出你要补推谁的战绩。当前可用账号有：洗洗、四万")


if __name__ == "__main__":
    unittest.main()
