from __future__ import annotations

import unittest

from tests.fake_group_chat_redis import FakeGroupChatRedis

from plugins.runtime_state_store import (
    configure_runtime_state_store_for_tests,
    get_bool_flag,
    reset_runtime_state_store,
    set_bool_flag,
)


class RuntimeStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        configure_runtime_state_store_for_tests(redis_client=FakeGroupChatRedis())
        reset_runtime_state_store()

    def tearDown(self) -> None:
        reset_runtime_state_store()

    def test_get_bool_flag_initializes_default_once(self) -> None:
        self.assertTrue(get_bool_flag("dota2_v2_debug", default=True))
        self.assertTrue(get_bool_flag("dota2_v2_debug", default=False))

    def test_set_bool_flag_persists_in_shared_redis(self) -> None:
        fake_redis = FakeGroupChatRedis()
        configure_runtime_state_store_for_tests(redis_client=fake_redis)
        set_bool_flag("dota2_v2_debug", True)
        configure_runtime_state_store_for_tests(redis_client=fake_redis)
        self.assertTrue(get_bool_flag("dota2_v2_debug", default=False))

    def test_set_bool_flag_can_turn_off(self) -> None:
        set_bool_flag("dota2_v2_debug", True)
        self.assertTrue(get_bool_flag("dota2_v2_debug", default=False))
        set_bool_flag("dota2_v2_debug", False)
        self.assertFalse(get_bool_flag("dota2_v2_debug", default=True))


if __name__ == "__main__":
    unittest.main()
