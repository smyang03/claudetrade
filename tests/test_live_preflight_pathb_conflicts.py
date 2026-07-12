from __future__ import annotations

import unittest

from tools import live_preflight


class LivePreflightPathBConflictTests(unittest.TestCase):
    def test_still_held_order_unknown_is_recoverable_not_start_blocking(self) -> None:
        run = {
            "market": "US",
            "ticker": "SOFI",
            "path_run_id": "path_sofi",
            "status": "ORDER_UNKNOWN",
            "session_date": "2026-05-15",
            "plan": {"session_end_unresolved": True},
        }
        exposure = {
            "market": "US",
            "ticker": "SOFI",
            "path_run_id": "path_sofi",
            "qty": 12,
            "local_position_qty": 12,
            "local_sell_order_id": "0032123235",
            "sources": ["local_position"],
        }
        broker_snapshot = {
            "markets": {
                "US": {
                    "positions": [{"ticker": "SOFI", "qty": 12}],
                    "open_orders": [],
                    "today_fills": [],
                    "last_success_at": "2026-05-16T01:00:00+00:00",
                }
            }
        }

        item = dict(run)
        live_preflight._attach_exposure_evidence(item, {"path_sofi": exposure}, broker_snapshot)
        conflicts = live_preflight._pathb_broker_truth_conflicts(
            "live",
            {"path_sofi": run},
            broker_snapshot=broker_snapshot,
            exposure_by_path={"path_sofi": exposure},
        )

        self.assertTrue(item["pathb_recoverable_still_held"])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["suggested_action"], "recover_still_held")
        self.assertFalse(conflicts[0]["do_not_start"])
        self.assertFalse(conflicts[0]["manual_reconciliation_required"])

    def test_qty_mismatch_conflict_remains_start_blocking(self) -> None:
        run = {
            "market": "US",
            "ticker": "SOFI",
            "path_run_id": "path_sofi",
            "status": "ORDER_UNKNOWN",
            "session_date": "2026-05-15",
            "plan": {},
        }
        exposure = {
            "market": "US",
            "ticker": "SOFI",
            "path_run_id": "path_sofi",
            "qty": 12,
            "local_position_qty": 12,
            "local_sell_order_id": "0032123235",
            "sources": ["local_position"],
        }
        broker_snapshot = {
            "markets": {
                "US": {
                    "positions": [{"ticker": "SOFI", "qty": 7}],
                    "open_orders": [],
                    "today_fills": [],
                }
            }
        }

        conflicts = live_preflight._pathb_broker_truth_conflicts(
            "live",
            {"path_sofi": run},
            broker_snapshot=broker_snapshot,
            exposure_by_path={"path_sofi": exposure},
        )

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["suggested_action"], "manual_review")
        self.assertTrue(conflicts[0]["do_not_start"])
        self.assertTrue(conflicts[0]["manual_reconciliation_required"])

    def test_broker_conflict_remediation_tool_uses_conflict_identity(self) -> None:
        tool = live_preflight._pathb_broker_conflict_remediation_tool(
            [
                {
                    "market": "KR",
                    "ticker": "005930",
                    "path_run_id": "path_kr_005930",
                    "do_not_start": True,
                }
            ],
            [
                {
                    "market": "US",
                    "ticker": "SOFI",
                    "path_run_id": "path_sofi",
                    "do_not_start": False,
                }
            ],
        )

        self.assertEqual(
            tool,
            "python -m tools.reconcile_live_truth --market KR --ticker 005930 --path-run-id path_kr_005930 --dry-run",
        )

    def test_acked_entry_with_matching_local_and_broker_position_is_recoverable(self) -> None:
        item = {
            "market": "US",
            "ticker": "MSFT",
            "path_run_id": "path_msft",
            "status": "ORDER_ACKED",
        }
        exposure = {
            "market": "US",
            "ticker": "MSFT",
            "path_run_id": "path_msft",
            "qty": 1,
            "local_position_qty": 1,
            "sources": ["local_position"],
        }
        broker_snapshot = {
            "markets": {
                "US": {
                    "positions": [{"ticker": "MSFT", "qty": 1}],
                    "open_orders": [],
                    "today_fills": [],
                }
            }
        }

        live_preflight._attach_exposure_evidence(item, {"path_msft": exposure}, broker_snapshot)

        self.assertTrue(item["pathb_recoverable_entry_holding"])
        self.assertFalse(item["pathb_recoverable_still_held"])

    def test_missing_path_run_id_rows_accept_trimmed_dict_payload(self) -> None:
        # _db_checks는 payload_json(단건 최대 1.7MB)을 전량 적재하면 MemoryError가 나므로
        # 커서를 스트리밍하며 판정에 쓰이는 키만 dict로 추려 넘긴다. 그 축약 payload로도
        # 동일 판정이 나와야 한다.
        trimmed_rows = [
            {
                "event_type": "CLAUDE_PRICE_PLAN",
                "market": "US",
                "ticker": "NVDA",
                "decision_id": "dec_linked",
                "payload_json": {},
            },
            {
                "event_type": "CLAUDE_TRADE_READY",
                "market": "KR",
                "ticker": "005930",
                "decision_id": "dec_orphan",
                "payload_json": {"path_type": "claude_price"},
            },
            {
                "event_type": "CLAUDE_PRICE_PLAN",
                "market": "US",
                "ticker": "AMD",
                "decision_id": "dec_ok",
                "payload_json": {"path_run_id": "path_amd"},
            },
        ]

        result = live_preflight._pathb_like_missing_path_run_id_rows(trimmed_rows, {"dec_linked"})

        self.assertEqual([row["ticker"] for row in result["rows"]], ["NVDA", "005930"])
        self.assertEqual(result["decision_id_linkable_count"], 1)
        self.assertEqual(result["decision_id_unlinkable_count"], 1)


if __name__ == "__main__":
    unittest.main()
