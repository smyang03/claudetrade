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

k.DOC_DIR = Path(tempfile.mkdtemp()) / "kr_event_docs"  # 본문 보관은 임시 디렉터리로 (저장소 오염 방지)

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
        self.assertGreater(row["latency_sec"], 600)          # 총 지연 = 최초 감지 기준(본문 대기 포함)
        self.assertLess(row["proc_sec"], 5)                   # 이번 호출 처리 시간은 별도
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
            self.assertEqual(rows[0]["latency_sec"], 70.0)   # 감지 11:09:11 → 판단 11:10:21
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


class PhantomStateAndEodTest(unittest.TestCase):
    """09-06 수리 1: 유령 상태 영속화·진입 마감 15:10·시세 없는 EOD·이월 금지."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.orig = (k.PHANTOM_LEDGER, k.SIGNAL_LEDGER, k.STATE_PATH)
        k.PHANTOM_LEDGER, k.SIGNAL_LEDGER, k.STATE_PATH = d / "ph.jsonl", d / "sig.jsonl", d / "state.json"
        from datetime import datetime, timezone
        self.tz = timezone(timedelta(hours=9))
        self.t0 = datetime(2026, 9, 7, 10, 0, 0, tzinfo=self.tz)
        self.sig = {"rcept_no": "1", "stock_code": "000100", "corp_name": "A", "kind": "supply_contract",
                    "session_date": "2026-09-07", "basis": "t"}

    def tearDown(self):
        k.PHANTOM_LEDGER, k.SIGNAL_LEDGER, k.STATE_PATH = self.orig
        self.tmp.cleanup()

    def _open(self):
        pos = k.open_phantom(self.sig, {"price": 10000.0, "source": "t"})
        pos["opened_at"] = k._iso(self.t0)
        return pos

    def test_entry_cutoff(self):
        fields = {"ratio_pct": 40.0, "related_party": False}
        liq = {"prev_close": 10000.0, "dvol20_krw": 5e9}
        q = {"price": 10100.0}
        self.assertEqual(k.decide("supply_contract", False, fields, {"available": False}, q, liq,
                                  now=self.t0.replace(hour=15, minute=9))[0], "ENTER")
        self.assertEqual(k.decide("supply_contract", False, fields, {"available": False}, q, liq,
                                  now=self.t0.replace(hour=15, minute=10)), ("SKIP", "after_entry_cutoff"))

    def test_time_check_once_when_state_carried(self):
        pos = self._open()
        # 31분 시점 +3% → 시점 점검 통과, time_checked 기록
        keep, closed = k.evaluate_phantoms([pos], lambda t: {"price": 10330.0}, now=self.t0 + timedelta(minutes=31))
        self.assertEqual(len(keep), 1); self.assertTrue(keep[0]["time_checked"]); self.assertAlmostEqual(keep[0]["peak_pct"], 2.99, places=1)
        # 같은 dict를 이어받은 40분 시점 +1% → 다시 시점 점검하지 않는다
        keep, closed = k.evaluate_phantoms(keep, lambda t: {"price": 10130.0}, now=self.t0 + timedelta(minutes=40))
        self.assertEqual((len(keep), len(closed)), (1, 0))
        self.assertAlmostEqual(keep[0]["peak_pct"], (10330 / 10030 - 1) * 100, places=3)  # MFE 유지
        # 원장에서 재생성한 옛 방식이면 청산됐다 (재발 방지 대조)
        fresh = k.open_positions_from_ledger("2026-09-07")
        self.assertFalse(fresh[0].get("time_checked"))
        keep2, closed2 = k.evaluate_phantoms(fresh, lambda t: {"price": 10130.0}, now=self.t0 + timedelta(minutes=40))
        self.assertEqual(closed2[0]["exit_reason"], "TIME_STOP")

    def test_eod_without_quote_uses_last_observed(self):
        pos = self._open()
        keep, _ = k.evaluate_phantoms([pos], lambda t: {"price": 10200.0}, now=self.t0 + timedelta(minutes=5))
        self.assertEqual(keep[0]["last_px"], 10200.0)
        keep, closed = k.evaluate_phantoms(keep, lambda t: None, now=self.t0.replace(hour=15, minute=25))
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["exit_reason"], "EOD_LASTQUOTE"); self.assertTrue(closed[0]["unpriced_exit"])
        self.assertEqual(closed[0]["exit"], 10200.0)

    def test_force_close_without_any_quote(self):
        pos = self._open()
        keep, closed = k.evaluate_phantoms([pos], lambda t: None, now=self.t0.replace(hour=15, minute=41), force_close=True)
        self.assertEqual((len(keep), closed[0]["exit_reason"], closed[0]["gross_pct"]), (0, "EOD_FORCED", 0.0))
        self.assertTrue(closed[0]["unpriced_exit"])

    def test_before_eod_no_quote_keeps(self):
        pos = self._open()
        keep, closed = k.evaluate_phantoms([pos], lambda t: None, now=self.t0 + timedelta(minutes=10))
        self.assertEqual((len(keep), len(closed)), (1, 0))

    def test_finalize_orphans(self):
        self._open()
        out = k.finalize_orphans("2026-09-08", now=self.t0 + timedelta(days=1))
        self.assertEqual(len(out), 1); self.assertEqual(out[0]["exit_reason"], "ORPHAN_UNPRICED")
        self.assertEqual(k.open_positions_from_ledger("2026-09-07"), [])
        self.assertEqual(k.finalize_orphans("2026-09-08"), [])  # 멱등

    def test_runner_persists_state_between_cycles(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import kr_event_lane_runner as rn
        pos = self._open()
        st = {"session_date": "2026-09-07", "seen": []}
        orig = (k.dart_list_today, rn._quote, rn.HEARTBEAT)
        k.dart_list_today = lambda sd: []
        rn.HEARTBEAT = Path(self.tmp.name) / "hb.json"
        try:
            rn._quote = lambda t: {"price": 10330.0}
            rn.cycle("2026-09-07", st, now=self.t0 + timedelta(minutes=31))
            self.assertTrue(st["open_positions"][0]["time_checked"])
            st2 = k.load_state()
            self.assertTrue(st2["open_positions"][0]["time_checked"])  # 파일에도 남는다
            rn._quote = lambda t: {"price": 10130.0}
            r = rn.cycle("2026-09-07", st2, now=self.t0 + timedelta(minutes=40))
            self.assertEqual((r["closed"], r["open_n"]), (0, 1))
        finally:
            k.dart_list_today, rn._quote, rn.HEARTBEAT = orig


class RelationThreeStateTest(unittest.TestCase):
    """09-06 수리 2: 관계사 True / 비관계사 False / 확인불가 None, 확인불가는 LLM 명시 없으면 SKIP. 상대방 경계."""
    REAL = ("3. 계약상대방 인천국제공항공사 - 최근 매출액(원) 3,067,177,661,865 - 주요사업 인천국제공항의 건설 및 관리·운영 등 "
            "- 회사와의 관계 - - 회사와 최근 3년간 동종계약 이행여부 미해당 4. 판매" + _MID + "공급지역 인천 5. 계약기간 시작일 2026-09-03 종료일 2027-12-03")

    def test_classify_relation(self):
        self.assertIsNone(k.classify_relation(None, found=False))
        self.assertFalse(k.classify_relation("", found=True))
        self.assertFalse(k.classify_relation("-", found=True))
        self.assertFalse(k.classify_relation("관계없음", found=True))
        self.assertFalse(k.classify_relation("해당사항 없음", found=True))
        self.assertFalse(k.classify_relation("특수관계 없음", found=True))
        self.assertFalse(k.classify_relation("계열회사 아님", found=True))
        self.assertTrue(k.classify_relation("계열회사", found=True))
        self.assertTrue(k.classify_relation("최대주주의 특수관계인", found=True))
        self.assertTrue(k.classify_relation("종속회사", found=True))

    def test_counterparty_boundary_on_real_form(self):
        f = k.parse_supply_contract(self.REAL)
        self.assertEqual(f["counterparty"], "인천국제공항공사")
        self.assertEqual(f["relation"], "")
        self.assertTrue(f["relation_found"]); self.assertFalse(f["related_party"])
        self.assertEqual(f["period"], ("2026-09-03", "2027-12-03"))

    def test_missing_relation_section_is_unknown(self):
        f = k.parse_supply_contract("계약금액(원) 50,000,000,000 최근매출액(원) 100,000,000,000 매출액대비(%) 50.0 3. 계약상대 ABC 4. 판매지역")
        self.assertIsNone(f["related_party"]); self.assertFalse(f["relation_found"])
        liq = {"prev_close": 10000.0, "dvol20_krw": 5e9}; q = {"price": 10100.0}
        self.assertEqual(k.decide("supply_contract", False, f, {"available": False}, q, liq), ("SKIP", "relation_unknown"))
        self.assertEqual(k.decide("supply_contract", False, f, {"available": True, "related_party": False, "quality": "strong"}, q, liq)[0], "ENTER")
        self.assertEqual(k.decide("supply_contract", False, f, {"available": True, "related_party": True, "quality": "strong"}, q, liq), ("SKIP", "related_party"))
        self.assertIn("관계불명", k.basis_text("supply_contract", f, {"available": False}))

    def test_negated_relation_passes(self):
        doc = DOC.replace("회사와의 관계 - 4.", "회사와의 관계 관계없음 4.").replace("매출액대비(%) 6.0", "매출액대비(%) 40.0")
        f = k.parse_supply_contract(doc)
        self.assertFalse(f["related_party"])
        self.assertEqual(k.decide("supply_contract", False, f, {"available": False}, {"price": 10100.0},
                                  {"prev_close": 10000.0, "dvol20_krw": 5e9}), ("ENTER", "rules_pass"))


class ObservationLedgerTest(unittest.TestCase):
    """09-06 수리 4: 3시점 관측 원장 — 감지·본문·판단 시점 가격 + 5분/30분/15:20/종가 결과, 탈락 공시 포함, 본문 보관."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.orig = (k.PHANTOM_LEDGER, k.SIGNAL_LEDGER, k.STATE_PATH, k.OBS_LEDGER, k.DOC_DIR)
        k.PHANTOM_LEDGER, k.SIGNAL_LEDGER, k.STATE_PATH = d / "ph.jsonl", d / "sig.jsonl", d / "state.json"
        k.OBS_LEDGER, k.DOC_DIR = d / "obs.jsonl", d / "docs"
        self.item = {"rcept_no": "77", "stock_code": "000100", "corp_name": "유한양행", "report_nm": "단일판매" + _MID + "공급계약체결"}

    def tearDown(self):
        k.PHANTOM_LEDGER, k.SIGNAL_LEDGER, k.STATE_PATH, k.OBS_LEDGER, k.DOC_DIR = self.orig
        self.tmp.cleanup()

    def test_rejected_disclosure_is_observed_with_three_timestamps_and_outcomes(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import kr_event_lane_runner as rn
        from datetime import datetime, timezone
        tz = timezone(timedelta(hours=9)); t0 = datetime(2026, 9, 7, 10, 0, 0, tzinfo=tz)
        docs = {"text": ""}; px = {"v": 10000.0}
        orig = (k.dart_list_today, k.dart_document_text, rn._quote, rn.HEARTBEAT, rn._ensure_cache_async)
        k.dart_list_today = lambda sd: [self.item]
        k.dart_document_text = lambda r, **kw: docs["text"]
        rn._quote = lambda t: {"price": px["v"], "source": "t"}
        rn.HEARTBEAT = Path(self.tmp.name) / "hb.json"; rn._ensure_cache_async = lambda t: None
        try:
            st = {"session_date": "2026-09-07", "seen": []}
            rn.cycle("2026-09-07", st, now=t0)                       # 본문 없음 → PENDING, 감지가 10000 기록
            self.assertEqual(st["obs"]["77"]["px_detect"], 10000.0)
            self.assertIsNone(st["obs"]["77"]["decision"])
            docs["text"] = DOC; px["v"] = 10100.0
            rn.cycle("2026-09-07", st, now=t0 + timedelta(seconds=70))  # 본문 확보·판단(6% → SKIP), 판단가 10100
            o = st["obs"]["77"]
            self.assertEqual(o["decision"], "SKIP"); self.assertEqual(o["px_decide"], 10100.0)
            self.assertEqual(o["t_doc"], k._iso(t0 + timedelta(seconds=70)))
            self.assertEqual(o["fields"]["ratio_pct"], 6.0)
            self.assertTrue((k.DOC_DIR / "77.txt").exists())        # 본문 보관 → 나중에 LLM 재현
            px["v"] = 10300.0
            rn.cycle("2026-09-07", st, now=t0 + timedelta(minutes=6))   # 5분 결과
            self.assertEqual(st["obs"]["77"]["out"]["px_5m"], 10300.0)
            self.assertNotIn("px_30m", st["obs"]["77"]["out"])
            px["v"] = 10050.0
            rn.cycle("2026-09-07", st, now=t0 + timedelta(minutes=31))  # 30분 결과
            self.assertEqual(st["obs"]["77"]["out"]["px_30m"], 10050.0)
            px["v"] = 9900.0
            rn.cycle("2026-09-07", st, now=t0.replace(hour=15, minute=21))  # 15:20 결과
            self.assertEqual(st["obs"]["77"]["out"]["px_1520"], 9900.0)
            self.assertFalse(k.OBS_LEDGER.exists())                  # 아직 원장 기록 전
            px["v"] = 9950.0
            written = rn._obs_fill(st["obs"], rn._quote, t0.replace(hour=15, minute=41), session_end=True)
            self.assertEqual(len(written), 1); self.assertEqual(st["obs"], {})
            rows = k.read_jsonl(k.OBS_LEDGER)
            out = rows[0]["out"]
            self.assertEqual(out["px_close"], 9950.0)
            self.assertAlmostEqual(out["ret_5m_pct"], 3.0, places=3)         # 감지가 대비
            self.assertAlmostEqual(out["ret_close_pct"], -0.5, places=3)
            self.assertAlmostEqual(out["ret_decide_to_close_pct"], -1.485, places=3)  # 판단가 대비 = 우리가 잡을 수 있는 몫
        finally:
            k.dart_list_today, k.dart_document_text, rn._quote, rn.HEARTBEAT, rn._ensure_cache_async = orig

    def test_non_target_kinds_not_observed(self):
        obs = {}
        sys.path.insert(0, str(ROOT / "tools"))
        import kr_event_lane_runner as rn
        rn._obs_start(obs, {"rcept_no": "1", "stock_code": "000100"}, {"kind": "rights_offering"}, "2026-09-07", k.now_kst())
        rn._obs_start(obs, {"rcept_no": "2", "stock_code": "000100"}, {"kind": "supply_contract", "is_correction": True}, "2026-09-07", k.now_kst())
        rn._obs_start(obs, {"rcept_no": "3", "stock_code": "000100"}, {"kind": "buyback", "quote": {"price": 5.0}}, "2026-09-07", k.now_kst())
        self.assertEqual(list(obs), ["3"])


if __name__ == "__main__":
    unittest.main()
