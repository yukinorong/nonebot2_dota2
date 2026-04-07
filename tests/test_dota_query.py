from __future__ import annotations

import importlib
import time
import unittest
from unittest.mock import AsyncMock, patch

from tests.fake_group_chat_redis import FakeGroupChatRedis

from plugins.group_chat_store import (
    configure_group_chat_store_for_tests,
    record_user_group_event,
    reset_group_chat_store,
)


dota_query = importlib.import_module('plugins.dota_query')


class DotaQueryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        configure_group_chat_store_for_tests(redis_client=FakeGroupChatRedis())
        reset_group_chat_store()

    def tearDown(self) -> None:
        reset_group_chat_store()

    def test_resolve_dota_entities_uses_safe_alias_mapping(self) -> None:
        with (
            patch.object(dota_query, 'load_hero_aliases', return_value={'蓝猫': 17, '火猫': 106}),
            patch.object(dota_query, 'load_item_aliases', return_value={}),
        ):
            entities = dota_query.resolve_dota_entities('蓝猫怎么出装')
        self.assertEqual(entities['hero_ids'], [17])


if __name__ == '__main__':
    unittest.main()
