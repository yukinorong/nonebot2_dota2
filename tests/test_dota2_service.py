from __future__ import annotations

import importlib
import unittest
from unittest.mock import AsyncMock, patch


dota2_service = importlib.import_module('plugins.dota2_service')


class Dota2ServicePushAggregationTests(unittest.IsolatedAsyncioTestCase):
    async def test_push_v2_match_events_once_aggregates_same_match_same_group(self) -> None:
        state = {
            'accounts': {
                '111': {
                    'last_pushed_match_id': 900,
                    'known_match_ids': [900],
                    'pending_match_ids': [1001],
                },
                '222': {
                    'last_pushed_match_id': 900,
                    'known_match_ids': [900],
                    'pending_match_ids': [1001],
                },
            }
        }

        async def fake_collect(state_obj, account_id):
            del state_obj
            return [
                {
                    'account_id': account_id,
                    'match_id': 1001,
                    'match_seq_num': 5001,
                    'group_ids': [1081502166],
                }
            ]

        with (
            patch.object(dota2_service, '_collect_account_matches_v2', side_effect=fake_collect),
            patch.object(dota2_service, '_fetch_sequence_match', AsyncMock(return_value={'match_id': 1001})),
            patch.object(dota2_service, '_build_v2_group_message', AsyncMock(return_value='聚合推送')) as build_mock,
            patch.object(dota2_service, 'send_group_text', AsyncMock()) as send_mock,
            patch.object(dota2_service, '_display_name_for_account', side_effect=lambda account_id: {'111': '甲', '222': '乙'}[account_id]),
        ):
            summaries = await dota2_service._push_v2_match_events_once(state, ['111', '222'])

        send_mock.assert_awaited_once_with(1081502166, '聚合推送')
        build_mock.assert_awaited_once()
        self.assertEqual(build_mock.await_args.kwargs['target_account_ids'], {'111', '222'})
        self.assertEqual(summaries, ['v2 group=1081502166 match_id=1001 accounts=甲,乙'])
        self.assertEqual(state['accounts']['111']['last_pushed_match_id'], 1001)
        self.assertEqual(state['accounts']['222']['last_pushed_match_id'], 1001)
        self.assertEqual(state['accounts']['111']['pending_match_ids'], [])
        self.assertEqual(state['accounts']['222']['pending_match_ids'], [])
        self.assertIn(1001, state['accounts']['111']['known_match_ids'])
        self.assertIn(1001, state['accounts']['222']['known_match_ids'])


class Dota2ServicePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_sequence_match_persists_match_detail(self) -> None:
        payload = {
            'result': {
                'matches': [
                    {'match_id': 3003, 'match_seq_num': 7003, 'players': []},
                ]
            }
        }
        with (
            patch.object(dota2_service, '_fetch_json', AsyncMock(return_value=payload)),
            patch.object(dota2_service, '_persist_match_detail', AsyncMock()) as persist_mock,
        ):
            match = await dota2_service._fetch_sequence_match(7003, target_steam_ids={'123'})
        self.assertEqual(match['match_id'], 3003)
        persist_mock.assert_awaited_once_with(match, target_steam_ids={'123'})

    async def test_collect_recent_matches_skips_existing_raw_match(self) -> None:
        history_matches = [
            {'match_id': 4001, 'match_seq_num': 8001},
            {'match_id': 4002, 'match_seq_num': 8002},
        ]
        with (
            patch.object(dota2_service, '_fetch_recent_matches', AsyncMock(return_value=history_matches)),
            patch.object(dota2_service, 'has_raw_match', side_effect=lambda match_id: match_id == 4001),
            patch.object(dota2_service, '_fetch_sequence_match', AsyncMock(return_value={'match_id': 4002})) as fetch_mock,
        ):
            summary = await dota2_service.collect_recent_matches('123456', 2)
        self.assertEqual(summary, {'requested': 2, 'scanned': 2, 'fetched': 1, 'skipped': 1, 'failed': 0})
        fetch_mock.assert_awaited_once_with(8002, target_steam_ids={'123456'}, persist_required=True)



    async def test_collect_recent_matches_handles_fetch_recent_exception(self) -> None:
        with patch.object(dota2_service, '_fetch_recent_matches', AsyncMock(side_effect=RuntimeError('boom'))):
            summary = await dota2_service.collect_recent_matches('123456', 2)
        self.assertEqual(summary, {'requested': 2, 'scanned': 0, 'fetched': 0, 'skipped': 0, 'failed': 2})

    async def test_collect_recent_matches_for_all(self) -> None:
        with (
            patch.object(
                dota2_service,
                "list_watched_accounts",
                return_value=[
                    {"account_id": "111", "display_name": "甲"},
                    {"account_id": "222", "display_name": "乙"},
                ],
            ),
            patch.object(
                dota2_service,
                "collect_recent_matches",
                AsyncMock(side_effect=[
                    {"requested": 50, "scanned": 40, "fetched": 10, "skipped": 25, "failed": 5},
                    {"requested": 50, "scanned": 50, "fetched": 8, "skipped": 40, "failed": 2},
                ]),
            ),
        ):
            summary = await dota2_service.collect_recent_matches_for_all()
        self.assertEqual(summary["accounts"], 2)
        self.assertEqual(summary["requested_total"], 100)
        self.assertEqual(summary["fetched"], 18)
        self.assertEqual(summary["per_account"][0]["display_name"], "甲")


class Dota2ServiceQueueTests(unittest.TestCase):
    def test_merge_recent_queue_drops_stale_pending_ids_before_limit(self) -> None:
        account_state = {
            "known_match_ids": [900],
            "pending_match_ids": [1001, 1002],
        }
        recent_matches = [
            {"match_id": 1003, "match_seq_num": 5003},
        ]

        known_ids, pending_ids, recent_map = dota2_service._merge_recent_queue(account_state, recent_matches)

        self.assertEqual(known_ids, [900])
        self.assertEqual(pending_ids, [1003])
        self.assertEqual(list(recent_map.keys()), [1003])

if __name__ == '__main__':
    unittest.main()
