from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugins.idle_joke_store import (
    configure_idle_joke_store_for_tests,
    has_idle_joke_hash,
    joke_md5,
    reset_idle_joke_store,
    save_idle_joke_hash,
)


class IdleJokeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_idle_joke_store_for_tests(db_path=Path(self._tmpdir.name) / "idle_joke.sqlite3")
        reset_idle_joke_store()

    def tearDown(self) -> None:
        reset_idle_joke_store()
        configure_idle_joke_store_for_tests(db_path=None)
        self._tmpdir.cleanup()

    def test_hash_is_group_scoped(self) -> None:
        self.assertTrue(save_idle_joke_hash(1081502166, "同一个笑话"))
        self.assertTrue(has_idle_joke_hash(1081502166, "同一个笑话"))
        self.assertFalse(has_idle_joke_hash(608990365, "同一个笑话"))

    def test_same_joke_normalizes_before_md5(self) -> None:
        self.assertEqual(joke_md5("第一行<br>第二行"), joke_md5("第一行\n第二行"))


if __name__ == "__main__":
    unittest.main()
