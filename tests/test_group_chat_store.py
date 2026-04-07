from __future__ import annotations

import unittest

import nonebot

nonebot.init()

from tests.fake_group_chat_redis import FakeGroupChatRedis

from plugins.group_chat_store import (
    configure_group_chat_store_for_tests,
    get_last_activity_at,
    get_last_idle_joke_at,
    get_recent_group_context,
    record_bot_group_reply,
    record_idle_joke,
    record_user_group_event,
    reset_group_chat_store,
)


class GroupChatStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        configure_group_chat_store_for_tests(redis_client=FakeGroupChatRedis())
        reset_group_chat_store()

    def tearDown(self) -> None:
        reset_group_chat_store()

    def test_keeps_data_after_store_reinitialization(self) -> None:
        fake_redis = FakeGroupChatRedis()
        configure_group_chat_store_for_tests(redis_client=fake_redis)
        record_user_group_event(1, '甲', 'hello', timestamp=100.0)
        configure_group_chat_store_for_tests(redis_client=fake_redis)
        self.assertEqual(get_recent_group_context(1, now=100.0)[0]['text'], 'hello')
        self.assertEqual(get_last_activity_at(1), 100.0)

    def test_keeps_groups_isolated(self) -> None:
        record_user_group_event(1, '甲', 'hello', timestamp=100.0)
        record_user_group_event(2, '乙', 'world', timestamp=101.0)
        self.assertEqual(len(get_recent_group_context(1, now=101.0)), 1)
        self.assertEqual(len(get_recent_group_context(2, now=101.0)), 1)
        self.assertEqual(get_recent_group_context(1, now=101.0)[0]['text'], 'hello')
        self.assertEqual(get_recent_group_context(2, now=101.0)[0]['text'], 'world')

    def test_prunes_old_messages_but_keeps_last_activity(self) -> None:
        record_user_group_event(1, '甲', 'old', timestamp=0.0)
        record_user_group_event(1, '乙', 'new', timestamp=7201.0)
        context = get_recent_group_context(1, now=7201.0)
        self.assertEqual([item['text'] for item in context], ['new'])
        self.assertEqual(get_last_activity_at(1), 7201.0)

    def test_ignores_command_like_bot_reply_routes(self) -> None:
        record_bot_group_reply(1, 'version_query', '功能简介', timestamp=10.0)
        self.assertEqual(get_recent_group_context(1, now=10.0), [])

    def test_records_group_chat_and_web_answerable_bot_replies(self) -> None:
        record_bot_group_reply(1, 'group_chat', '接话', timestamp=10.0)
        record_bot_group_reply(1, 'web_answerable', '联网结果', timestamp=11.0)
        context = get_recent_group_context(1, now=11.0)
        self.assertEqual([item['kind'] for item in context], ['group_chat', 'web_answerable'])
        self.assertEqual(get_last_activity_at(1), 11.0)

    def test_idle_joke_counts_as_activity(self) -> None:
        record_idle_joke(1, '冷笑话', timestamp=50.0)
        context = get_recent_group_context(1, now=50.0)
        self.assertEqual(context[0]['kind'], 'idle_joke')
        self.assertEqual(get_last_activity_at(1), 50.0)
        self.assertEqual(get_last_idle_joke_at(1), 50.0)

    def test_recent_idle_jokes_remain_in_context_order(self) -> None:
        record_idle_joke(1, '冷笑话A', timestamp=10.0)
        record_idle_joke(1, '冷笑话B', timestamp=20.0)
        record_idle_joke(1, '冷笑话C', timestamp=30.0)
        context = get_recent_group_context(1, now=30.0)
        jokes = [item['text'] for item in context if item['kind'] == 'idle_joke']
        self.assertEqual(jokes, ['冷笑话A', '冷笑话B', '冷笑话C'])


    def test_command_like_user_message_can_update_activity_without_entering_context(self) -> None:
        record_user_group_event(1, '甲', '', timestamp=600.0, kind='user_command')
        self.assertEqual(get_recent_group_context(1, now=600.0), [])
        self.assertEqual(get_last_activity_at(1), 600.0)

    def test_limits_recent_context_items(self) -> None:
        for idx in range(140):
            record_user_group_event(1, '甲', f'msg-{idx}', timestamp=float(idx))
        context = get_recent_group_context(1, now=139.0, max_items=100)
        self.assertEqual(len(context), 100)
        self.assertEqual(context[0]['text'], 'msg-40')
        self.assertEqual(context[-1]['text'], 'msg-139')


class QQEntryCommandDetectionTests(unittest.TestCase):
    def test_is_command_message(self) -> None:
        from plugins.qq_entry import _is_command_message

        self.assertTrue(_is_command_message('/news'))
        self.assertTrue(_is_command_message('   /todo abc'))
        self.assertFalse(_is_command_message('今天/news 不算命令'))
        self.assertFalse(_is_command_message(''))


if __name__ == '__main__':
    unittest.main()
