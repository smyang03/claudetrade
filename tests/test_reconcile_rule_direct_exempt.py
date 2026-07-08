# -*- coding: utf-8 -*-
"""P2 패치 테스트: rule_direct 메타에서 reconcile 'reviewed_and_removed' 취소 면제.

배경(완결성 토론 2026-07-08): US 프롬프트 풀(40) > watchlist(30)라서 rule_direct
랭크 31~40 이탈 티커의 WAITING 플랜이 INVALID_CANCEL 되는 kill-zone.
rule_direct는 Claude 검토가 아니므로 취소 의미론 미적용 = KEEP.
"""
import unittest

from runtime.pathb_runtime import PathBRuntime


class _Bot:
    """indexes/verdict가 쓰는 최소 표면."""
    pass


def _mk_runtime():
    # __init__ 우회: 판정 함수는 인스턴스 상태를 안 씀 (indexes 인자만 사용)
    rt = object.__new__(PathBRuntime)
    return rt


def _meta(rule_direct: bool):
    # 풀 40 > watchlist 30 시나리오: T31은 풀에만 있고 watch에 없음
    pool = [{"ticker": f"T{i}"} for i in range(1, 41)]
    watch = [f"T{i}" for i in range(1, 31)]
    meta = {
        "_final_prompt_pool": pool,
        "watchlist": watch,
        "trade_ready": [],
    }
    if rule_direct:
        meta["_selection_rule_direct"] = True
    return meta


class ReconcileRuleDirectExemptTest(unittest.TestCase):
    def test_rule_direct_pool_dropout_keeps(self):
        rt = _mk_runtime()
        idx = rt._selection_reconcile_indexes("US", _meta(rule_direct=True))
        self.assertTrue(idx.get("rule_direct"))
        verdict, reason, _ = rt._selection_reconcile_verdict("US", "T31", idx)
        self.assertEqual(verdict, "KEEP")
        self.assertEqual(reason, "rule_direct_watch_rotation")

    def test_claude_meta_pool_dropout_still_cancels(self):
        """기존(비 rule_direct) 의미론 회귀 방지: reviewed_and_removed 유지."""
        rt = _mk_runtime()
        idx = rt._selection_reconcile_indexes("US", _meta(rule_direct=False))
        self.assertFalse(idx.get("rule_direct"))
        verdict, reason, _ = rt._selection_reconcile_verdict("US", "T31", idx)
        self.assertEqual(verdict, "INVALID_CANCEL")
        self.assertEqual(reason, "reviewed_and_removed")

    def test_rule_direct_watchlist_member_keeps(self):
        rt = _mk_runtime()
        idx = rt._selection_reconcile_indexes("US", _meta(rule_direct=True))
        verdict, _, _ = rt._selection_reconcile_verdict("US", "T5", idx)
        self.assertIn(verdict, ("KEEP", "VALID_KEEP"))

    def test_rule_direct_outside_pool_unknown_keep(self):
        rt = _mk_runtime()
        idx = rt._selection_reconcile_indexes("US", _meta(rule_direct=True))
        verdict, reason, _ = rt._selection_reconcile_verdict("US", "ZZZZ", idx)
        self.assertEqual(verdict, "UNKNOWN_KEEP")
        self.assertEqual(reason, "not_reviewed")


if __name__ == "__main__":
    unittest.main()
