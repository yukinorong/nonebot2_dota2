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
        with (
            patch.object(qq_commands, "_normalize_todo_request", AsyncMock(return_value={
                "is_feature_request": True,
                "normalized_item": "增加查看某个人最近一把战绩的功能",
                "is_duplicate": False,
            })),
            patch.object(qq_commands, "upsert_todo_item", return_value="- [ ] 增加查看某个人最近一把战绩的功能") as upsert,
        ):
            answer = await qq_commands.try_handle_local_group_command("/todo 增加查看某个人最近一把战绩的功能", group_id=1081502166)
        upsert.assert_called_once_with("增加查看某个人最近一把战绩的功能")
        self.assertEqual(answer, "- [ ] 增加查看某个人最近一把战绩的功能")

    async def test_todo_with_duplicate(self) -> None:
        with (
            patch.object(qq_commands, "_normalize_todo_request", AsyncMock(return_value={
                "is_feature_request": True,
                "normalized_item": "增加查看某个人最近一把战绩的功能",
                "is_duplicate": True,
            })),
            patch.object(qq_commands, "read_todo_display", return_value="- [ ] 增加查看某个人最近一把战绩的功能"),
        ):
            answer = await qq_commands.try_handle_local_group_command("/todo 再加一个查看某人最近战绩", group_id=1081502166)
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


if __name__ == "__main__":
    unittest.main()
