from __future__ import annotations

import importlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.fake_group_chat_redis import FakeGroupChatRedis

from plugins.group_memory_store import (
    configure_group_memory_store_for_tests,
    reset_group_memory_store,
)
from plugins.group_chat_store import (
    configure_group_chat_store_for_tests,
    record_user_group_event,
    reset_group_chat_store,
)

qq_router = importlib.import_module('plugins.qq_router')


class QQRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        configure_group_chat_store_for_tests(redis_client=FakeGroupChatRedis())
        reset_group_chat_store()
        configure_group_memory_store_for_tests(db_path=Path(self.temp_dir.name) / "group-memory.sqlite3")
        reset_group_memory_store()

    def tearDown(self) -> None:
        reset_group_chat_store()
        reset_group_memory_store()
        configure_group_memory_store_for_tests(db_path=None)
        self.temp_dir.cleanup()

    async def test_dispatch_group_prompt_uses_single_group_agent_call(self) -> None:
        now = time.time()
        record_user_group_event(1081502166, '老王', '今晚打不打', timestamp=now)
        with (
            patch.object(qq_router, 'ensure_group_openclaw_setup', return_value=False),
            patch.object(qq_router, 'ask_main', AsyncMock(return_value='## 接上文回复\n- 第一条')) as mocked,
        ):
            answer = await qq_router.dispatch_group_prompt('你来吗', group_id=1081502166, message_id=1)
        self.assertEqual(answer, '## 接上文回复\n- 第一条')
        prompt = mocked.await_args.kwargs['prompt'] if 'prompt' in mocked.await_args.kwargs else mocked.await_args.args[0]
        self.assertIn('系统筛好的长期记忆命中项', prompt)
        self.assertIn('（无命中的长期记忆）', prompt)
        self.assertIn('最近群聊上下文', prompt)
        self.assertIn('老王: 今晚打不打', prompt)
        self.assertIn('不要使用 Markdown', prompt)
        self.assertIn('简单闲聊、打招呼、接梗、吐槽，直接回复，不要调用任何工具', prompt)
        self.assertIn('BOT_CAPABILITIES.md', prompt)
        self.assertIn('DOTA_AGENT_GUIDE.md', prompt)
        self.assertIn('web_search', prompt)
        self.assertEqual(mocked.await_args.kwargs['agent_id'], 'qq_group_1081502166')

    async def test_route_group_prompt_returns_group_chat_route(self) -> None:
        with (
            patch.object(qq_router, 'dispatch_group_prompt', AsyncMock(return_value='你好啊')) as mocked,
        ):
            route, answer = await qq_router.route_group_prompt('北京天气', group_id=1081502166, message_id=2)
        self.assertEqual(route, 'group_chat')
        self.assertEqual(answer, '你好啊')
        mocked.assert_awaited_once()

    async def test_fetch_daily_news_uses_tavily_results_directly(self) -> None:
        search_data = {
            'answer': '今天有几条热点',
            'results': [
                {
                    'title': '新闻A',
                    'content': '摘要A',
                    'url': 'https://example.com/a',
                },
                {
                    'title': '新闻B',
                    'content': '摘要B',
                    'url': 'https://example.com/b',
                },
            ],
        }
        with (
            patch.object(qq_router, '_tavily_search', AsyncMock(return_value=search_data)),
            patch.object(qq_router, 'ask_main', AsyncMock(return_value='不该被调用')) as mocked,
        ):
            answer = await qq_router.fetch_daily_news(channel='qq-g1081502166-m0-news', keyword='科技')
        self.assertIn('今日科技新闻', answer)
        self.assertIn('1. 新闻A：摘要A', answer)
        self.assertIn('来源：https://example.com/a https://example.com/b', answer)
        mocked.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
