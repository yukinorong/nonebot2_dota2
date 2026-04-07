from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from plugins.dota_guide_store import (
    configure_dota_guide_store_for_tests,
    ensure_guide_source_tables,
    get_guide_sources,
    parse_dota_version,
    prune_expired_guide_sources,
    reset_dota_guide_store,
    save_guide_source,
    version_weight,
)


class DotaGuideStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_dota_guide_store_for_tests(db_path=Path(self._tmpdir.name) / "guide.sqlite3")
        reset_dota_guide_store()
        ensure_guide_source_tables()

    def tearDown(self) -> None:
        reset_dota_guide_store()
        self._tmpdir.cleanup()
        configure_dota_guide_store_for_tests(db_path=None)

    def test_version_weight(self) -> None:
        self.assertEqual(version_weight(current_version="7.39d", candidate_version="7.39d"), 1.0)
        self.assertEqual(version_weight(current_version="7.39d", candidate_version="7.39c"), 0.85)
        self.assertEqual(version_weight(current_version="7.39d", candidate_version="7.38c"), 0.55)
        self.assertEqual(version_weight(current_version="7.39d", candidate_version="7.37e"), 0.0)

    def test_save_guide_source_deduplicates(self) -> None:
        first = save_guide_source(
            hero_id=17,
            topic_type="patch",
            source_type="official",
            source_url="https://www.dota2.com/patches/739d",
            source_title="7.39d patch",
            content_text="Storm Spirit mana cost increased.",
            game_version="7.39d",
        )
        second = save_guide_source(
            hero_id=17,
            topic_type="patch",
            source_type="official",
            source_url="https://www.dota2.com/patches/739d",
            source_title="7.39d patch",
            content_text="Storm Spirit mana cost increased.",
            game_version="7.39d",
        )
        self.assertTrue(first)
        self.assertFalse(second)

    def test_get_guide_sources_filters_expired_and_sorts(self) -> None:
        old_dt = datetime.now(UTC) - timedelta(days=120)
        recent_dt = datetime.now(UTC) - timedelta(days=1)
        save_guide_source(
            hero_id=17,
            topic_type="patch",
            source_type="official",
            source_title="old",
            content_text="old patch",
            game_version="7.38c",
            fetched_at=old_dt.isoformat(),
        )
        save_guide_source(
            hero_id=17,
            topic_type="patch",
            source_type="official",
            source_title="current",
            content_text="current patch",
            game_version="7.39d",
            fetched_at=recent_dt.isoformat(),
        )
        prune_expired_guide_sources()
        rows = get_guide_sources(17, current_version="7.39d")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_title"], "current")
        self.assertEqual(rows[0]["effective_weight"], 1.0)

    def test_parse_dota_version(self) -> None:
        parsed = parse_dota_version("Latest is 7.39d now")
        self.assertEqual(parsed["raw"], "7.39d")
        self.assertEqual(parsed["major_version"], "7.39")


if __name__ == "__main__":
    unittest.main()
