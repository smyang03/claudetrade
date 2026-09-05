# -*- coding: utf-8 -*-
"""KR 공시 이벤트 레인 v1 — 분류·본문 파싱·결정 규칙·유령 출구·원장 (SHADOW, 2026-09-04)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_MID = chr(0x318D)
sys.path.insert(0, str(ROOT))

from runtime import kr_event_lane as k  # noqa: E402

DOC = ("유한양행/단일판매" + _MID + "공급계약체결/(2026.09.01) 1. 판매" + _MID + "공급계약 구분 상품공급 - 체결계약명 원료의약품(API) 공급계약 "
       "2. 계약내역 계약금액(원) 131,074,406,400 최근매출액(원) 2,186,637,586,358 매출액대비(%) 6.0 대규모법인여부 해당 "
       "3. 계약상대 글로벌 제약사 - 회사와의 관계 - 4. 판매" + _MID + "공급지역 미정 5. 계약기간 시작일 2026-09-01 종료일 2028-05-31 "
       "6. 주요 계약조건 계약금" + _MID + "선급금 유무 무 대금지급 조건 등 - 7. 계약(수주)일자 2026-09-01")
DOC_REL = DOC.replace("3. 계약상대 글로벌 제약사 - 회사와의 관계 - 4.", "3. 계약상대 ABC홀딩스 회사와의 관계 계열회사 4.").replace("매출액대비(%) 6.0", "매출액대비(%) 45.2")
BONUS = "무상증자결정 1. 신주의 종류와 수 보통주식 (주) 5,000,000 3. 1주당 신주배정 주식수 (주) 1.0 신주배정기준일 2026-09-20"


class ClassifyParseTest(unittest.TestCase):
    def test_titles(self):
        self.assertEqual(k.classify_title("단일판매" + _MID + "공급계약체결"), ("supply_contract", False))
        self.assertEqual(k.classify_title("[기재정정]단일판매" + _MID + "공급계약체결"), ("supply_contract", True))
        self.assertEqual(k.classify_title("무상증자결정"), ("bonus_issue", False))
        self.assertEqual(k.classify_title("주요사항보고서(자기주식취득결정)"), ("buyback", False))
        self.assertEqual(k.classify_title("소송등의제기"), ("other", False))

    def test_supply_fields(self):
        f = k.parse_supply_contract(DOC)
        self.assertEqual(f["amount_krw"], 131074406400.0)
        self.assertEqual(f["ratio_pct"], 6.0)
        self.assertEqual(f["counterparty"], "글로벌 제약사")
        self.assertFalse(f["related_party"])
        self.assertEqual(f["period"], ("2026-09-01", "2028-05-31"))
        self.assertEqual(f["advance_payment"], "무")
        f2 = k.parse_supply_contract(DOC_REL)
        self.assertEqual(f2["ratio_pct"], 45.2)
        self.assertTrue(f2["related_party"])

    def test_bonus_fields(self):
        f = k.parse_bonus_issue(BONUS)
        self.assertEqual(f["ratio_per_share"], 1.0)
        self.assertEqual(f["record_date"], "2026-09-20")


class DecideTest(unittest.TestCase):
    liq = {"prev_close": 10000.0, "dvol20_krw": 5e9}
    q = {"price": 10300.0, "source": "t"}

    def test_supply_rules(self):
        f = k.parse_supply_contract(DOC)
        self.assertEqual(k.decide("supply_contract", False, f, {"available": False}, self.q, self.liq)[0], "SKIP")  # 6%
        f["ratio_pct"] = 45.0
        self.assertEqual(k.decide("supply_contract", False, f, {"available": False}, self.q, self.liq), ("ENTER", "rules_pass"))
        self.assertEqual(k.decide("supply_contract", True, f, {"available": False}, self.q, self.liq), ("SKIP", "correction"))
        fr = k.parse_supply_contract(DOC_REL)
        self.assertEqual(k.decide("supply_contract", False, fr, {"available": False}, self.q, self.liq), ("SKIP", "related_party"))
        self.assertEqual(k.decide("supply_contract", False, f, {"available": True, "quality": "skip", "reason": "갱신"}, self.q, self.liq)[0], "SKIP")
        self.assertEqual(k.decide("supply_contract", False, f, {"available": False}, {"price": 11000.0}, self.liq)[0], "SKIP")  # +10% runup
        self.assertEqual(k.decide("supply_contract", False, f, {"available": False}, self.q, {"prev_close": 10000.0, "dvol20_krw": 1e9})[0], "SKIP")
        self.assertEqual(k.decide("supply_contract", False, f, {"available": False}, self.q, self.liq, open_n=3), ("SKIP", "slots_full"))
        self.assertEqual(k.decide("supply_contract", False, f, {"available": False}, self.q, self.liq, new_today=6), ("SKIP", "daily_cap"))

    def test_observe_only_kinds(self):
        self.assertEqual(k.decide("buyback", False, {}, {}, self.q, self.liq)[0], "OBSERVE")
        self.assertEqual(k.decide("bonus_issue", False, {"ratio_per_share": 0.2}, {}, self.q, self.liq)[0], "SKIP")
        self.assertEqual(k.decide("bonus_issue", False, {"ratio_per_share": 1.0}, {}, self.q, self.liq)[0], "ENTER")


class PhantomTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = (k.PHANTOM_LEDGER, k.SIGNAL_LEDGER)
        k.PHANTOM_LEDGER = Path(self.tmp.name) / "ph.jsonl"
        k.SIGNAL_LEDGER = Path(self.tmp.name) / "sig.jsonl"

    def tearDown(self):
        k.PHANTOM_LEDGER, k.SIGNAL_LEDGER = self.orig
        self.tmp.cleanup()

    def _open(self):
        sig = {"rcept_no": "1", "stock_code": "000100", "corp_name": "X", "kind": "supply_contract",
               "session_date": "2026-09-04", "basis": "b"}
        return k.open_phantom(sig, {"price": 10000.0, "source": "t"})

    def test_open_qty_and_slippage(self):
        pos = self._open()
        self.assertEqual(pos["entry"], 10030.0)
        self.assertEqual(pos["qty"], 24)  # 250000 // 10030
        rows = k.read_jsonl(k.PHANTOM_LEDGER)
        self.assertEqual(rows[0]["event"], "OPEN")

    def test_exit_tp_sl_time_eod(self):
        pos = self._open()
        now = k.now_kst().replace(hour=10, minute=0, second=0)
        pos["opened_at"] = now.isoformat(timespec="seconds")
        keep, closed = k.evaluate_phantoms([dict(pos)], lambda t: {"price": 10030.0 * 1.09}, now=now + timedelta(minutes=5))
        self.assertEqual(closed[0]["exit_reason"], "TP")
        keep, closed = k.evaluate_phantoms([dict(pos)], lambda t: {"price": 10030.0 * 0.95}, now=now + timedelta(minutes=5))
        self.assertEqual(closed[0]["exit_reason"], "SL")
        # 30분 시점 +1% → TIME_STOP
        keep, closed = k.evaluate_phantoms([dict(pos)], lambda t: {"price": 10030.0 * 1.01}, now=now + timedelta(minutes=31))
        self.assertEqual(closed[0]["exit_reason"], "TIME_STOP")
        # 30분 시점 +3% → 유지, 이후 EOD
        p2 = dict(pos)
        keep, closed = k.evaluate_phantoms([p2], lambda t: {"price": 10030.0 * 1.03}, now=now + timedelta(minutes=31))
        self.assertEqual(len(keep), 1); self.assertTrue(keep[0]["time_checked"])
        keep, closed = k.evaluate_phantoms(keep, lambda t: {"price": 10030.0 * 1.03}, now=now.replace(hour=15, minute=21))
        self.assertEqual(closed[0]["exit_reason"], "EOD")
        self.assertAlmostEqual(closed[0]["net_pct"], 3.0 - 0.21, places=2)

    def test_bonus_issue_holds_to_eod_without_time_stop(self):
        sig = {"rcept_no": "2", "stock_code": "000100", "corp_name": "X", "kind": "bonus_issue",
               "session_date": "2026-09-04", "basis": "b"}
        pos = k.open_phantom(sig, {"price": 10000.0, "source": "t"})
        now = k.now_kst().replace(hour=10, minute=0, second=0)
        pos["opened_at"] = now.isoformat(timespec="seconds")
        # 30분 시점 +1%라도 무상증자는 TIME_STOP 없음
        keep, closed = k.evaluate_phantoms([dict(pos)], lambda t: {"price": 10030.0 * 1.01}, now=now + timedelta(minutes=31))
        self.assertEqual(len(keep), 1); self.assertEqual(closed, [])
        # 공급계약 기본 SL(−4%)보다 넓은 −7% 안전 손절
        keep, closed = k.evaluate_phantoms([dict(pos)], lambda t: {"price": 10030.0 * 0.95}, now=now + timedelta(minutes=5))
        self.assertEqual(closed, [])
        keep, closed = k.evaluate_phantoms([dict(pos)], lambda t: {"price": 10030.0 * 0.92}, now=now + timedelta(minutes=5))
        self.assertEqual(closed[0]["exit_reason"], "SL")

    def test_no_quote_keeps_position(self):
        pos = self._open()
        keep, closed = k.evaluate_phantoms([pos], lambda t: None)
        self.assertEqual(len(keep), 1); self.assertEqual(closed, [])


class ProcessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = (k.PHANTOM_LEDGER, k.SIGNAL_LEDGER)
        k.PHANTOM_LEDGER = Path(self.tmp.name) / "ph.jsonl"
        k.SIGNAL_LEDGER = Path(self.tmp.name) / "sig.jsonl"

    def tearDown(self):
        k.PHANTOM_LEDGER, k.SIGNAL_LEDGER = self.orig
        self.tmp.cleanup()

    def test_process_records_all_and_enters_on_strong(self):
        item = {"rcept_no": "9", "stock_code": "000100", "corp_name": "유한양행", "report_nm": "단일판매" + _MID + "공급계약체결"}
        row = k.process_disclosure(item, session_date="2026-09-04", quote_fn=lambda t: {"price": 81500.0, "source": "t"},
                                   open_n=0, new_today=0, doc_fn=lambda r: DOC.replace("매출액대비(%) 6.0", "매출액대비(%) 35.0"),
                                   llm_fn=lambda kind, text, f: {"available": True, "quality": "strong", "reason": "신규 외부"})
        self.assertEqual(row["decision"], "ENTER")
        self.assertIn("매출대비 35%", row["basis"])
        self.assertIn("LLM strong", row["basis"])
        other = k.process_disclosure({"rcept_no": "10", "stock_code": "000100", "report_nm": "소송등의제기"},
                                     session_date="2026-09-04", quote_fn=lambda t: None, open_n=0, new_today=0)
        self.assertEqual(other["decision"], "IGNORE")
        self.assertEqual(len(k.read_jsonl(k.SIGNAL_LEDGER)), 2)


class DocRetryTest(unittest.TestCase):
    """본문 지연 재시도 (09-06): 감지 직후 document.xml이 비면 원장에 쓰지 않고 PENDING, 러너가 재시도 후 확정."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.orig = (k.PHANTOM_LEDGER, k.SIGNAL_LEDGER, k.STATE_PATH)
        k.PHANTOM_LEDGER, k.SIGNAL_LEDGER, k.STATE_PATH = d / "ph.jsonl", d / "sig.jsonl", d / "state.json"
        self.item = {"rcept_no": "20260904900109", "stock_code": "083640", "corp_name": "인콘",
                     "report_nm": "단일판매" + _MID + "공급계약체결"}

    def tearDown(self):
        k.PHANTOM_LEDGER, k.SIGNAL_LEDGER, k.STATE_PATH = self.orig
        self.tmp.cleanup()

    def test_empty_doc_is_pending_not_ledger(self):
        row = k.process_disclosure(self.item, session_date="2026-09-04", quote_fn=lambda t: {"price": 1087.0},
                                   open_n=0, new_today=0, doc_fn=lambda r: "")
        self.assertEqual(row["decision"], "PENDING")
        self.assertEqual(row["reason"], "doc_unavailable")
        self.assertEqual(row["doc_attempts"], 1)
        self.assertEqual(k.read_jsonl(k.SIGNAL_LEDGER), [])

    def test_retry_keeps_first_seen_and_parses(self):
        row = k.process_disclosure(self.item, session_date="2026-09-04", quote_fn=lambda t: {"price": 1087.0},
                                   open_n=0, new_today=0, doc_fn=lambda r: DOC, first_seen="2026-09-04T11:09:11+09:00",
                                   doc_attempts=1)
        self.assertEqual(row["ts_detected"], "2026-09-04T11:09:11+09:00")
        self.assertEqual(row["doc_attempts"], 2)
        self.assertEqual(row["fields"]["ratio_pct"], 6.0)
        self.assertTrue(row["reason"].startswith("ratio_6.0_lt_30"))
        self.assertEqual(len(k.read_jsonl(k.SIGNAL_LEDGER)), 1)

    def test_final_records_skip(self):
        row = k.process_disclosure(self.item, session_date="2026-09-04", quote_fn=lambda t: None,
                                   open_n=0, new_today=0, doc_fn=lambda r: "", final=True, doc_attempts=9)
        self.assertEqual(row["decision"], "SKIP")
        self.assertEqual(row["reason"], "doc_unavailable_after_retry")
        self.assertEqual(row["doc_attempts"], 10)
        self.assertEqual(len(k.read_jsonl(k.SIGNAL_LEDGER)), 1)

    def _runner(self, doc_fn, quote):
        sys.path.insert(0, str(ROOT / "tools"))
        import kr_event_lane_runner as rn
        orig = (k.dart_list_today, k.dart_document_text, rn._quote, rn.HEARTBEAT, rn._ensure_cache_async)
        k.dart_list_today = lambda sd: [self.item]
        k.dart_document_text = doc_fn
        rn._quote = lambda t: quote
        rn.HEARTBEAT = Path(self.tmp.name) / "hb.json"
        rn._ensure_cache_async = lambda t: None

        def restore():
            k.dart_list_today, k.dart_document_text, rn._quote, rn.HEARTBEAT, rn._ensure_cache_async = orig
        return rn, restore

    def test_runner_cycle_retries_until_doc_arrives(self):
        from datetime import datetime, timezone
        docs = {"text": ""}
        rn, restore = self._runner(lambda r, **kw: docs["text"], {"price": 1087.0, "source": "t"})
        try:
            t0 = datetime(2026, 9, 4, 11, 9, 11, tzinfo=timezone(timedelta(hours=9)))
            st = {"session_date": "2026-09-04", "seen": []}
            r = rn.cycle("2026-09-04", st, now=t0)
            self.assertEqual((r["pending"], r["retried"]), (1, 0))
            self.assertEqual(k.read_jsonl(k.SIGNAL_LEDGER), [])
            r = rn.cycle("2026-09-04", st, now=t0 + timedelta(seconds=30))   # 간격 미달 → 재시도 안 함
            self.assertEqual((r["fresh"], r["pending"], r["retried"]), (0, 1, 0))
            docs["text"] = DOC
            r = rn.cycle("2026-09-04", st, now=t0 + timedelta(seconds=70))
            self.assertEqual((r["pending"], r["retried"]), (0, 1))
            rows = k.read_jsonl(k.SIGNAL_LEDGER)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["ts_detected"], k._iso(t0))
            self.assertEqual(rows[0]["doc_attempts"], 2)
            self.assertTrue(rows[0]["reason"].startswith("ratio_6.0_lt_30"))
            self.assertEqual(st["pending"], {})
        finally:
            restore()

    def test_runner_cycle_finalizes_after_max_wait(self):
        from datetime import datetime, timezone
        rn, restore = self._runner(lambda r, **kw: "", None)
        try:
            t0 = datetime(2026, 9, 4, 11, 9, 11, tzinfo=timezone(timedelta(hours=9)))
            st = {"session_date": "2026-09-04", "seen": []}
            rn.cycle("2026-09-04", st, now=t0)
            r = rn.cycle("2026-09-04", st, now=t0 + timedelta(seconds=k.DOC_RETRY_MAX_SEC + 1))
            self.assertEqual(r["pending"], 0)
            rows = k.read_jsonl(k.SIGNAL_LEDGER)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["reason"], "doc_unavailable_after_retry")
        finally:
            restore()


if __name__ == "__main__":
    unittest.main()
