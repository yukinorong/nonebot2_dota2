from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()
qq_commands = importlib.import_module("plugins.qq_commands")


class QQCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_success(self) -> None:
        with patch.object(qq_commands, "add_watch_account", return_value=(True, "添加成功：洗洗 -> 163926078，已加入本群监听。")) as mocked:
            answer = await qq_commands.try_handle_local_group_command("/add 洗洗 163926078", group_id=1081502166)
        mocked.assert_called_once_with("洗洗", "163926078", 1081502166)
        self.assertEqual(answer, "添加成功：洗洗 -> 163926078，已加入本群监听。")

    async def test_add_wrong_arg_count_missing(self) -> None:
        answer = await qq_commands.try_handle_local_group_command("/add 洗洗", group_id=1081502166)
        self.assertEqual(answer, "用法: /add <昵称> <steamID>")

    async def test_add_wrong_arg_count_extra(self) -> None:
        answer = await qq_commands.try_handle_local_group_command("/add 洗洗 163926078 多余参数", group_id=1081502166)
        self.assertEqual(answer, "用法: /add <昵称> <steamID>")

    async def test_add_nickname_conflict(self) -> None:
        with patch.object(qq_commands, "add_watch_account", return_value=(False, "添加失败：昵称“洗洗”已绑定到 163926078。")):
            answer = await qq_commands.try_handle_local_group_command("/add 洗洗 999999999", group_id=1081502166)
        self.assertEqual(answer, "添加失败：昵称“洗洗”已绑定到 163926078。")

    async def test_list_without_args(self) -> None:
        with patch.object(qq_commands, "list_group_accounts", return_value=[
            {"account_id": "163926078", "display_name": "洗洗"},
            {"account_id": "162255543", "display_name": "四万"},
        ]):
            answer = await qq_commands.try_handle_local_group_command("/list", group_id=1081502166)
        self.assertEqual(answer, "当前群监听账号：洗洗、四万")

    async def test_list_with_args_ignored(self) -> None:
        with patch.object(qq_commands, "list_group_accounts", return_value=[{"account_id": "143291985", "display_name": "蚕蛹"}]):
            answer = await qq_commands.try_handle_local_group_command("/list abc", group_id=834689068)
        self.assertEqual(answer, "当前群监听账号：蚕蛹")

    async def test_todo_without_args(self) -> None:
        with patch.object(qq_commands, "read_todo_display", return_value="- [ ] 增加推送某人战绩"):
            answer = await qq_commands.try_handle_local_group_command("/todo", group_id=1081502166)
        self.assertEqual(answer, "- [ ] 增加推送某人战绩")

    async def test_todo_with_new_item(self) -> None:
        with patch.object(qq_commands, "upsert_todo_item", return_value="- [ ] 增加查看某个人最近一把战绩的功能") as upsert:
            answer = await qq_commands.try_handle_local_group_command("/todo 增加查看某个人最近一把战绩的功能", group_id=1081502166)
        upsert.assert_called_once_with("增加查看某个人最近一把战绩的功能")
        self.assertEqual(answer, "- [ ] 增加查看某个人最近一把战绩的功能")

    async def test_todo_with_duplicate(self) -> None:
        with patch.object(qq_commands, "upsert_todo_item", return_value="- [ ] 增加查看某个人最近一把战绩的功能") as upsert:
            answer = await qq_commands.try_handle_local_group_command("/todo 再加一个查看某人最近战绩", group_id=1081502166)
        upsert.assert_called_once_with("再加一个查看某人最近战绩")
        self.assertEqual(answer, "- [ ] 增加查看某个人最近一把战绩的功能")

    async def test_help_without_args(self) -> None:
        with patch.object(qq_commands, "read_description", return_value="机器人说明"):
            answer = await qq_commands.try_handle_local_group_command("/help", group_id=1081502166)
        self.assertEqual(answer, "机器人说明")

    async def test_help_with_args_ignored(self) -> None:
        with patch.object(qq_commands, "read_description", return_value="机器人说明"):
            answer = await qq_commands.try_handle_local_group_command("/help xxx", group_id=1081502166)
        self.assertEqual(answer, "机器人说明")

    async def test_unknown_slash_command(self) -> None:
        answer = await qq_commands.try_handle_local_group_command("/abc", group_id=1081502166)
        self.assertEqual(answer, "未识别的指令。")

    async def test_push_success(self) -> None:
        with (
            patch.object(qq_commands, "list_watched_accounts", return_value=[
                {"account_id": "163926078", "display_name": "洗洗"},
                {"account_id": "162255543", "display_name": "四万"},
            ]),
            patch.object(qq_commands, "build_latest_match_push_text", AsyncMock(return_value="洗洗这把赢了，直接把对面中路打穿。")),
        ):
            answer = await qq_commands.try_handle_local_group_command("/push 洗洗", group_id=1081502166)
        self.assertEqual(answer, "洗洗这把赢了，直接把对面中路打穿。")

    async def test_push_missing_arg(self) -> None:
        answer = await qq_commands.try_handle_local_group_command("/push", group_id=1081502166)
        self.assertEqual(answer, "用法: /push <昵称>")

    async def test_push_extra_args(self) -> None:
        answer = await qq_commands.try_handle_local_group_command("/push 洗洗 四万", group_id=1081502166)
        self.assertEqual(answer, "用法: /push <昵称>")

    async def test_push_unknown_name(self) -> None:
        with patch.object(qq_commands, "list_watched_accounts", return_value=[
            {"account_id": "163926078", "display_name": "洗洗"},
            {"account_id": "162255543", "display_name": "四万"},
        ]):
            answer = await qq_commands.try_handle_local_group_command("/push 老王", group_id=1081502166)
        self.assertEqual(answer, "未找到该昵称：老王。当前可用昵称有：洗洗、四万")


    async def test_news_without_keyword(self) -> None:
        expected = "今日新闻\n1. A：B"
        with patch.object(qq_commands, "fetch_daily_news", AsyncMock(return_value=expected)) as mocked:
            answer = await qq_commands.try_handle_local_group_command("/news", group_id=1081502166)
        mocked.assert_called_once_with(channel="qq-g1081502166-m0-news", keyword="")
        self.assertEqual(answer, expected)

    async def test_news_with_keyword(self) -> None:
        expected = "今日科技新闻\n1. A：B"
        with patch.object(qq_commands, "fetch_daily_news", AsyncMock(return_value=expected)) as mocked:
            answer = await qq_commands.try_handle_local_group_command("/news 科技", group_id=1081502166)
        mocked.assert_called_once_with(channel="qq-g1081502166-m0-news", keyword="科技")
        self.assertEqual(answer, expected)

    async def test_dota_collect_with_nickname(self) -> None:
        with (
            patch.object(qq_commands, 'resolve_watched_account', return_value='163926078'),
            patch.object(qq_commands, 'collect_recent_matches', AsyncMock(return_value={
                'requested': 5,
                'scanned': 5,
                'fetched': 3,
                'skipped': 1,
                'failed': 1,
            })),
            patch.object(qq_commands, 'list_watched_accounts', return_value=[{'account_id': '163926078', 'display_name': '洗洗'}]),
        ):
            answer = await qq_commands.try_handle_local_group_command('/dota_collect 洗洗 5', group_id=1081502166, user_id=863347350)
        self.assertEqual(answer, 'Dota2 采集完成：洗洗，请求 5 场，扫描 5 场，新采集 3 场，跳过 1 场，失败 1 场。')

    async def test_dota_collect_without_args_collects_all(self) -> None:
        with patch.object(
            qq_commands,
            'collect_recent_matches_for_all',
            AsyncMock(
                return_value={
                    'accounts': 2,
                    'requested_per_account': 50,
                    'requested_total': 100,
                    'scanned': 90,
                    'fetched': 18,
                    'skipped': 60,
                    'failed': 12,
                    'per_account': [
                        {'display_name': '洗洗', 'scanned': 45, 'fetched': 10, 'skipped': 30, 'failed': 5},
                        {'display_name': '四万', 'scanned': 45, 'fetched': 8, 'skipped': 30, 'failed': 7},
                    ],
                }
            ),
        ):
            answer = await qq_commands.try_handle_local_group_command('/dota_collect', group_id=1081502166, user_id=863347350)
        self.assertIn('共 2 个监听账号', answer)
        self.assertIn('洗洗：扫描 45，新增 10，跳过 30，失败 5', answer)

    async def test_dota_collect_with_unknown_name(self) -> None:
        with (
            patch.object(qq_commands, 'resolve_watched_account', return_value=None),
            patch.object(qq_commands, 'list_watched_accounts', return_value=[{'account_id': '163926078', 'display_name': '洗洗'}]),
        ):
            answer = await qq_commands.try_handle_local_group_command('/dota_collect 老王 5', group_id=1081502166, user_id=863347350)
        self.assertEqual(answer, '未找到该昵称或 steamID：老王。当前可用昵称有：洗洗')


    async def test_dota_collect_denied_for_non_admin(self) -> None:
        answer = await qq_commands.try_handle_local_group_command('/dota_collect 洗洗 5', group_id=1081502166, user_id=123456)
        self.assertEqual(answer, '/dota_collect 仅允许指定管理员在指定群内使用。')

    async def test_dota_rebuild_analysis_with_nickname(self) -> None:
        with (
            patch.object(qq_commands, 'resolve_watched_account', return_value='163926078'),
            patch.object(qq_commands, 'rebuild_recent_match_analysis', return_value={
                'scanned_matches': 12,
                'inserted_rows': 4,
                'skipped_existing_rows': 6,
                'failed_matches': 1,
                'failed_players': 2,
            }),
            patch.object(qq_commands, 'list_watched_accounts', return_value=[{'account_id': '163926078', 'display_name': '洗洗'}]),
        ):
            answer = await qq_commands.try_handle_local_group_command('/dota_rebuild_analysis 洗洗', group_id=1081502166, user_id=863347350)
        self.assertEqual(answer, 'Dota2 分析表重建完成：洗洗，扫描 12 场，新增 4 行，跳过已存在 6 行，失败比赛 1 场，失败玩家 2 个。')

    async def test_dota_rebuild_analysis_denied_for_wrong_group(self) -> None:
        answer = await qq_commands.try_handle_local_group_command('/dota_rebuild_analysis', group_id=608990365, user_id=863347350)
        self.assertEqual(answer, '/dota_rebuild_analysis 仅允许指定管理员在指定群内使用。')

    async def test_check_memory_returns_all_group_memories(self) -> None:
        with patch.object(
            qq_commands,
            'build_all_group_memory_report',
            return_value='群 1081502166\n# 机器人偏好\n- 回复短一点',
        ) as mocked:
            answer = await qq_commands.try_handle_local_group_command('/check_memory', group_id=1081502166, user_id=863347350)
        mocked.assert_called_once_with(qq_commands.QQ_ALLOWED_GROUP_IDS)
        self.assertIn('群 1081502166', answer)

    async def test_check_memory_denied_for_non_admin(self) -> None:
        answer = await qq_commands.try_handle_local_group_command('/check_memory', group_id=1081502166, user_id=123456)
        self.assertEqual(answer, '/check_memory 仅允许指定管理员在指定群内使用。')

    async def test_dota_analyze_with_nickname(self) -> None:
        expected = 'Dota2 最近50场分析：洗洗\n样本数：3 场，胜率：66.67%（2胜1负）\n使用最多英雄：祈求者（2场）\n最高击杀：12杀，英雄：小小，Match ID：3003\n最高死亡：9死，英雄：祈求者，Match ID：3002'
        with (
            patch.object(qq_commands, 'resolve_watched_account', return_value='163926078'),
            patch.object(qq_commands, 'build_recent_match_analysis_text', return_value=expected),
        ):
            answer = await qq_commands.try_handle_local_group_command('/dota_analyze 洗洗', group_id=1081502166)
        self.assertEqual(answer, expected)

    async def test_dota_analyze_with_unknown_name(self) -> None:
        with (
            patch.object(qq_commands, 'resolve_watched_account', return_value=None),
            patch.object(qq_commands, 'list_watched_accounts', return_value=[{'account_id': '163926078', 'display_name': '洗洗'}]),
        ):
            answer = await qq_commands.try_handle_local_group_command('/dota_analyze 老王', group_id=1081502166)
        self.assertEqual(answer, '未找到该昵称或 steamID：老王。当前可用昵称有：洗洗')

    async def test_dota_profile_with_nickname(self) -> None:
        expected = '打法概括\\n1. ...'
        with (
            patch.object(qq_commands, 'resolve_watched_account', return_value='163926078'),
            patch.object(qq_commands, 'build_player_profile_text', AsyncMock(return_value=expected)),
        ):
            answer = await qq_commands.try_handle_local_group_command('/dota_profile 洗洗', group_id=1081502166)
        self.assertEqual(answer, expected)

    async def test_dota_guide_with_hero_name(self) -> None:
        with patch.object(qq_commands, 'build_hero_guide_text', AsyncMock(return_value='## 风暴之灵当前版本攻略\n- 中路节奏英雄\n- 注意蓝量管理')):
            answer = await qq_commands.try_handle_local_group_command('/dota_guide 蓝猫', group_id=1081502166)
        self.assertEqual(answer, '## 风暴之灵当前版本攻略\n- 中路节奏英雄\n- 注意蓝量管理')


if __name__ == "__main__":
    unittest.main()
