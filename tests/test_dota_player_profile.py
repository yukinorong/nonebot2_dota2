from __future__ import annotations

import unittest
from unittest.mock import patch

from plugins.dota_player_profile import build_player_profile_features


class DotaPlayerProfileTests(unittest.TestCase):
    def test_build_player_profile_features(self) -> None:
        rows = [
            {
                "match_id": 1,
                "hero_id": 17,
                "won": 1,
                "kills": 10,
                "deaths": 4,
                "assists": 12,
                "gold_per_min": 560,
                "xp_per_min": 720,
                "last_hits": 220,
                "hero_damage": 28000,
                "tower_damage": 3400,
                "items_json": '{"main":{"item_0":116,"item_1":108}}',
                "start_time": 100,
            },
            {
                "match_id": 2,
                "hero_id": 17,
                "won": 0,
                "kills": 4,
                "deaths": 9,
                "assists": 8,
                "gold_per_min": 430,
                "xp_per_min": 590,
                "last_hits": 130,
                "hero_damage": 17000,
                "tower_damage": 1500,
                "items_json": '{"main":{"item_0":116}}',
                "start_time": 200,
            },
            {
                "match_id": 3,
                "hero_id": 106,
                "won": 1,
                "kills": 15,
                "deaths": 5,
                "assists": 10,
                "gold_per_min": 610,
                "xp_per_min": 810,
                "last_hits": 260,
                "hero_damage": 32000,
                "tower_damage": 4200,
                "items_json": '{"main":{"item_0":141}}',
                "start_time": 300,
            },
        ]
        with (
            patch("plugins.dota_player_profile.get_recent_account_matches", return_value=rows),
            patch("plugins.dota_player_profile.load_existing_hero_names", return_value={17: "风暴之灵", 106: "灰烬之灵"}),
            patch("plugins.dota_player_profile.load_existing_item_names", return_value={116: "Black King Bar", 108: "Aghanim's Scepter", 141: "Daedalus"}),
        ):
            features = build_player_profile_features("123456", limit=50)

        self.assertEqual(features["sample_size"], 3)
        self.assertEqual(features["win_count"], 2)
        self.assertEqual(features["top_heroes"][0]["hero_name"], "风暴之灵")
        self.assertIn("Black King Bar", [item["item_name"] for item in features["top_items"]])
        self.assertTrue(features["representative_matches"]["worst"]["deaths"] >= 9)
        self.assertTrue(features["style_tags"])
        self.assertTrue(features["problem_tags"])


if __name__ == "__main__":
    unittest.main()
