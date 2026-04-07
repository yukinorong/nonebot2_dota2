from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from plugins.dota2_match_store import (
    configure_dota_match_store_for_tests,
    ensure_tables,
    get_recent_account_analysis,
    get_recent_account_matches,
    has_player_analysis,
    has_raw_match,
    rebuild_player_match_analysis_from_raw_matches,
    reset_dota_match_store,
    save_raw_match_and_analysis,
)


class Dota2MatchStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_dota_match_store_for_tests(db_path=Path(self._tmpdir.name) / 'matches.sqlite3')
        reset_dota_match_store()
        ensure_tables()

    def tearDown(self) -> None:
        reset_dota_match_store()
        self._tmpdir.cleanup()
        configure_dota_match_store_for_tests(db_path=None)

    def _sample_match(self, match_id: int = 1001, kills: int = 5) -> dict:
        return {
            'match_id': match_id,
            'match_seq_num': 9001,
            'start_time': 1710000000,
            'duration': 2400,
            'game_mode': 22,
            'lobby_type': 0,
            'radiant_win': True,
            'players': [
                {
                    'account_id': 123456,
                    'player_slot': 0,
                    'hero_id': 74,
                    'item_0': 48,
                    'item_1': 36,
                    'item_2': 108,
                    'item_3': 0,
                    'item_4': 0,
                    'item_5': 0,
                    'backpack_0': 0,
                    'backpack_1': 0,
                    'backpack_2': 0,
                    'item_neutral': 585,
                    'item_neutral2': 0,
                    'kills': kills,
                    'deaths': 2,
                    'assists': 11,
                    'leaver_status': 0,
                    'last_hits': 180,
                    'denies': 7,
                    'gold_per_min': 520,
                    'xp_per_min': 690,
                    'level': 24,
                    'net_worth': 18000,
                    'hero_damage': 23000,
                    'tower_damage': 1200,
                    'hero_healing': 0,
                    'gold': 1500,
                    'gold_spent': 16000,
                    'ability_upgrades': [
                        {'ability': 5059, 'time': 100, 'level': 1},
                        {'ability': 5060, 'time': 200, 'level': 2},
                    ],
                },
                {
                    'account_id': 654321,
                    'player_slot': 128,
                    'hero_id': 19,
                    'kills': 1,
                    'deaths': 8,
                    'assists': 5,
                },
            ],
        }

    def test_save_raw_match_and_analysis_writes_only_target_player(self) -> None:
        raw_inserted, analysis_inserted = save_raw_match_and_analysis(
            self._sample_match(),
            target_steam_ids={'123456'},
        )
        self.assertTrue(raw_inserted)
        self.assertEqual(analysis_inserted, 1)
        self.assertTrue(has_raw_match(1001))
        self.assertTrue(has_player_analysis(1001, '123456'))
        self.assertFalse(has_player_analysis(1001, '654321'))
        rows = get_recent_account_matches('123456', limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['hero_id'], 74)
        self.assertEqual(rows[0]['kills'], 5)
        self.assertIn('5059', rows[0]['ability_upgrades_json'])

    def test_duplicate_match_and_player_is_skipped(self) -> None:
        first = save_raw_match_and_analysis(self._sample_match(match_id=2002, kills=5), target_steam_ids={'123456'})
        second = save_raw_match_and_analysis(self._sample_match(match_id=2002, kills=9), target_steam_ids={'123456'})
        self.assertEqual(first, (True, 1))
        self.assertEqual(second, (False, 0))
        rows = get_recent_account_matches('123456', limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kills'], 5)

    def test_ensure_tables_rebuilds_legacy_schema(self) -> None:
        db_path = Path(self._tmpdir.name) / 'matches.sqlite3'
        conn = sqlite3.connect(db_path)
        conn.execute('CREATE TABLE matches (match_id INTEGER PRIMARY KEY, raw_json TEXT NOT NULL)')
        conn.commit()
        conn.close()
        ensure_tables()
        conn = sqlite3.connect(db_path)
        table_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertIn('raw_matches', table_names)
        self.assertIn('player_match_analysis', table_names)
        self.assertNotIn('match_players', table_names)


    def test_rebuild_player_match_analysis_from_raw_matches(self) -> None:
        from plugins.dota2_match_store import save_raw_match

        save_raw_match(self._sample_match(match_id=4001, kills=8))
        summary = rebuild_player_match_analysis_from_raw_matches(target_steam_ids={'123456'})
        self.assertEqual(summary['scanned_matches'], 1)
        self.assertEqual(summary['inserted_rows'], 1)
        self.assertEqual(summary['skipped_existing_rows'], 0)
        self.assertEqual(summary['failed_matches'], 0)
        self.assertTrue(has_player_analysis(4001, '123456'))

    def test_get_recent_account_analysis(self) -> None:
        save_raw_match_and_analysis(self._sample_match(match_id=3001, kills=12), target_steam_ids={'123456'})
        second = self._sample_match(match_id=3002, kills=7)
        second['radiant_win'] = False
        second['players'][0]['hero_id'] = 74
        second['players'][0]['deaths'] = 9
        save_raw_match_and_analysis(second, target_steam_ids={'123456'})
        third = self._sample_match(match_id=3003, kills=12)
        third['start_time'] = 1710000100
        third['players'][0]['hero_id'] = 19
        third['players'][0]['deaths'] = 4
        save_raw_match_and_analysis(third, target_steam_ids={'123456'})

        summary = get_recent_account_analysis('123456', limit=50)
        self.assertEqual(summary['sample_size'], 3)
        self.assertEqual(summary['win_count'], 2)
        self.assertEqual(summary['loss_count'], 1)
        self.assertAlmostEqual(summary['win_rate'], 2 / 3)
        self.assertEqual(summary['most_played_hero_id'], 74)
        self.assertEqual(summary['most_played_hero_count'], 2)
        self.assertEqual(summary['highest_kills'], 12)
        self.assertEqual(summary['highest_kills_match_id'], 3003)
        self.assertEqual(summary['highest_kills_hero_id'], 19)
        self.assertEqual(summary['highest_deaths'], 9)
        self.assertEqual(summary['highest_deaths_match_id'], 3002)
        self.assertEqual(summary['highest_deaths_hero_id'], 74)


if __name__ == '__main__':
    unittest.main()
