from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init()

from tests.fake_group_chat_redis import FakeGroupChatRedis
from plugins.group_chat_store import configure_group_chat_store_for_tests, record_idle_joke, reset_group_chat_store
from plugins.idle_joke_store import configure_idle_joke_store_for_tests, reset_idle_joke_store, save_idle_joke_hash
from plugins import idle_joke


class IdleJokeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_group_chat_store_for_tests(redis_client=FakeGroupChatRedis())
        configure_idle_joke_store_for_tests(db_path=Path(self._tmpdir.name) / "idle_joke.sqlite3")
        reset_group_chat_store()
        reset_idle_joke_store()

    def tearDown(self) -> None:
        reset_group_chat_store()
        reset_idle_joke_store()
        configure_idle_joke_store_for_tests(db_path=None)
        self._tmpdir.cleanup()

    async def test_generate_idle_joke_v1_uses_unique_channel_and_recent_jokes(self) -> None:
        record_idle_joke(1081502166, '旧笑话1', timestamp=1234567000.0)
        record_idle_joke(1081502166, '旧笑话2', timestamp=1234567600.0)
        mocked = AsyncMock(return_value='新笑话')
        with patch('plugins.idle_joke.ask_main', mocked), patch('plugins.idle_joke.time.time', return_value=1234567890.0):
            result = await idle_joke._generate_idle_joke_v1(group_id=1081502166)

        self.assertEqual(result, '新笑话')
        prompt = mocked.call_args.kwargs['prompt'] if 'prompt' in mocked.call_args.kwargs else mocked.call_args.args[0]
        channel = mocked.call_args.kwargs['channel']
        self.assertIn('旧笑话1', prompt)
        self.assertIn('旧笑话2', prompt)
        self.assertIn('不要复读', prompt)
        self.assertEqual(channel, 'qq-g1081502166-idle-joke-1234567890')

    async def test_generate_idle_joke_v2_replaces_br_and_returns_new_joke(self) -> None:
        with patch('plugins.idle_joke._fetch_idle_joke_v2', AsyncMock(return_value={'status': 'success', 'data': '第一行<br>第二行'})):
            result = await idle_joke._generate_idle_joke_v2(group_id=1081502166)
        self.assertEqual(result, '第一行\n第二行')

    async def test_generate_idle_joke_v2_retries_when_duplicate(self) -> None:
        save_idle_joke_hash(1081502166, '重复笑话')
        mocked = AsyncMock(
            side_effect=[
                {'status': 'success', 'data': '重复笑话'},
                {'status': 'success', 'data': '新笑话'},
            ]
        )
        with patch('plugins.idle_joke._fetch_idle_joke_v2', mocked):
            result = await idle_joke._generate_idle_joke_v2(group_id=1081502166)
        self.assertEqual(result, '新笑话')
        self.assertEqual(mocked.await_count, 2)

    async def test_generate_idle_joke_v2_returns_empty_for_invalid_payload(self) -> None:
        with patch(
            'plugins.idle_joke._fetch_idle_joke_v2',
            AsyncMock(return_value={'status': 'error', 'message': 'bad request'}),
        ):
            result = await idle_joke._generate_idle_joke_v2(group_id=1081502166)
        self.assertEqual(result, '')


if __name__ == '__main__':
    unittest.main()
