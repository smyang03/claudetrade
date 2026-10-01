"""T1 픽 기록 전용 주석(tools/us_earnings_t1_annotate.py) — 오프라인(HTTP 모의).

계약: 발행인 유형·컷오프(UTC→ET)·직전 분기 창은 연구 정의 그대로, (session,ticker) 멱등,
api_error 재시도, Jev 호출 실행당 ≤ 50, 키는 출력·원장 어디에도 남지 않는다, 동결 질문 그대로 보낸다.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "research"))
import us_earnings_t1_annotate as A  # noqa: E402
import research_jev_8k_t1_20260929 as r1  # noqa: E402
import research_jev_round2_20260929 as r2  # noqa: E402

KEY = "SECRETKEY-abc123-DO-NOT-LEAK"


def F(rows):
    return pd.DataFrame(rows, columns=["form", "accessionNumber", "filingDate", "acceptanceDateTime", "items"])


def k8(acc, accept_utc, items="2.02,9.01", form="8-K"):
    return [form, acc, accept_utc[:10], accept_utc, items]


class DefinitionTests(unittest.TestCase):
    def test_issuer_type(self):
        dom = F([["10-Q", "a1", "2026-05-01", "", ""], ["6-K", "a2", "2026-06-01", "", ""]])
        self.assertEqual(A.issuer_type(dom, "2026-07-29"), "domestic")
        frn = F([["6-K", "a2", "2026-06-01", "", ""], ["20-F", "a3", "2026-03-01", "", ""]])
        self.assertEqual(A.issuer_type(frn, "2026-07-29"), "foreign")
        self.assertEqual(A.issuer_type(F([["8-K", "a4", "2026-06-01", "", "2.02"]]), "2026-07-29"), "unknown")
        # 400일 밖·이벤트일 당일은 제외
        old = F([["10-K", "a5", "2025-06-01", "", ""], ["10-Q", "a6", "2026-07-29", "", ""]])
        self.assertEqual(A.issuer_type(old, "2026-07-29"), "unknown")
        self.assertEqual(A.issuer_type(None, "2026-07-29"), "unknown")

    def test_cutoff_utc_to_et_summer(self):
        # AMC: 이벤트일 20:30Z = 16:30 EDT → 다음 세션 개장 전
        m = A.match_current(F([k8("c1", "2026-07-29T20:30:00.000Z")]), "2026-07-29", "2026-07-30")
        self.assertEqual((m["status"], m["acc"], m["dd"]), ("matched", "c1", 0))
        # BMO 세션 당일: 13:29Z = 09:29 EDT 통과, 13:31Z = 09:31 EDT 컷오프 이후
        self.assertEqual(A.match_current(F([k8("c2", "2026-07-30T13:29:00.000Z")]), "2026-07-30", "2026-07-30")["status"], "matched")
        self.assertEqual(A.match_current(F([k8("c3", "2026-07-30T13:31:00.000Z")]), "2026-07-30", "2026-07-30")["status"], "after_cutoff")

    def test_cutoff_winter_est(self):
        # 1월(EST): 14:29Z = 09:29 통과, 14:31Z = 09:31 이후
        self.assertEqual(A.match_current(F([k8("w1", "2026-01-15T14:29:00.000Z")]), "2026-01-15", "2026-01-15")["status"], "matched")
        self.assertEqual(A.match_current(F([k8("w2", "2026-01-15T14:31:00.000Z")]), "2026-01-15", "2026-01-15")["status"], "after_cutoff")

    def test_current_needs_202_and_plain_8k_and_near(self):
        f = F([k8("x1", "2026-07-29T20:30:00Z", items="5.02"), k8("x2", "2026-07-29T20:30:00Z", form="8-K/A"),
               k8("x3", "2026-07-20T20:30:00Z")])
        self.assertEqual(A.match_current(f, "2026-07-29", "2026-07-30")["status"], "no_8k")
        # 가장 가까운 것
        f2 = F([k8("n2", "2026-07-27T20:30:00Z"), k8("n1", "2026-07-29T12:00:00Z")])
        self.assertEqual(A.match_current(f2, "2026-07-29", "2026-07-29")["acc"], "n1")

    def test_prior_window_bounds(self):
        ev = pd.Timestamp("2026-07-29")

        def at(days):
            return (ev - pd.Timedelta(days=days)).strftime("%Y-%m-%d") + "T20:30:00.000Z"
        self.assertIsNone(A.find_prior(F([k8("p49", at(49))]), ev, "cur"))
        self.assertEqual(A.find_prior(F([k8("p50", at(50))]), ev, "cur"), {"acc": "p50", "gap": 50})
        self.assertEqual(A.find_prior(F([k8("p130", at(130))]), ev, "cur")["acc"], "p130")
        self.assertIsNone(A.find_prior(F([k8("p131", at(131))]), ev, "cur"))
        # 가장 최근 + 현재 접수번호 제외
        self.assertEqual(A.find_prior(F([k8("a", at(120)), k8("b", at(90))]), ev, "cur")["acc"], "b")
        self.assertIsNone(A.find_prior(F([k8("cur", at(90))]), ev, "cur"))

    def test_rare_neg_8k(self):
        f = F([k8("r1", "2026-07-10T20:00:00Z", items="4.01,9.01"),
               k8("r2", "2026-07-12T20:00:00Z", items="12.06"),           # 토큰 정확 매칭(부분문자열 아님)
               k8("r3", "2026-07-13T20:00:00Z", items="4.02", form="8-K/A"),
               k8("r4", "2026-06-29T13:29:00Z", items="3.01"),            # 30일 전 09:29 ET → 창 밖
               k8("r5", "2026-07-30T13:31:00Z", items="2.06")])           # 진입 개장 이후 → 창 밖
        r = A.rare_neg_8k(f, "2026-07-30")
        self.assertTrue(r["rare_neg_8k_30d"])
        self.assertEqual(r["rare_neg_8k_items"], ["4.01"])
        self.assertEqual([h["acc"] for h in r["rare_neg_8k_filings"]], ["r1"])
        self.assertFalse(A.rare_neg_8k(F([k8("z", "2026-07-10T20:00:00Z", items="2.02")]), "2026-07-30")["rare_neg_8k_30d"])

    def test_item502(self):
        f = F([k8("e1", "2026-08-05T20:00:00Z", items="5.02"), k8("e2", "2026-07-30T13:00:00Z", items="5.02"),
               k8("e3", "2026-08-27T13:31:00Z", items="5.02")])
        r = A.item502_during_hold(f, "2026-07-30", "2026-08-27")
        self.assertEqual((r["item_502_during_hold"], r["item_502_count"], r["item_502_acc"]), (True, 1, ["e1"]))

    def test_frozen_sha_matches_research(self):
        q, sha = A.frozen_questions()
        self.assertEqual(sha, A.FROZEN_Q_SHA)
        self.assertEqual(r2.MODEL, A.FROZEN_MODEL)
        self.assertIn("d_outlook", q)


# ------------------------------------------------------------------ fake HTTP
class Resp:
    def __init__(self, code=200, js=None, text=""):
        self.status_code, self._js, self.text = code, js, text

    def json(self):
        return self._js


def _subs(cik):
    rows = [["10-Q", f"{cik}-q", "2026-05-01", "2026-05-01T20:00:00.000Z", ""],
            ["8-K", f"{cik}-cur", "2026-07-29", "2026-07-29T20:30:00.000Z", "2.02,9.01"],
            ["8-K", f"{cik}-pri", "2026-04-29", "2026-04-29T20:30:00.000Z", "2.02,9.01"]]
    cols = ["form", "accessionNumber", "filingDate", "acceptanceDateTime", "items"]
    return {"filings": {"recent": {c: [r[i] for r in rows] for i, c in enumerate(cols)}, "files": []}}


INDEX = ('<table><tr><th>Seq</th></tr>'
         '<tr><td>1</td><td>8-K</td><td><a href="/Archives/x/k.htm">k.htm</a></td><td>8-K</td><td>1</td></tr>'
         '<tr><td>2</td><td>PR</td><td><a href="/Archives/x/ex991.htm">ex991.htm</a></td><td>EX-99.1</td><td>2</td></tr></table>')


def fake_get_factory(n_tickers):
    tick = {str(i): {"cik_str": 1000 + i, "ticker": f"TK{i}", "title": f"Tick{i} Widgets Inc."} for i in range(n_tickers)}

    def get(url, headers=None, timeout=None):
        assert "User-Agent" in headers
        if url.endswith("company_tickers.json"):
            return Resp(js=tick)
        if "/submissions/CIK" in url:
            cik = int(url.split("CIK")[1][:10])
            return Resp(js=_subs(cik))
        if url.endswith("-index.htm"):
            return Resp(text=INDEX)
        if url.endswith("ex991.htm"):
            which = "current" if "cur" in url else "previous"
            return Resp(text="<html><body><p>" + (f"Tick Widgets reports {which} quarter results. Outlook raised. " * 20)
                        + "</p></body></html>")
        return Resp(404)
    return get


JEV_OK = {"model": "jev-1.13.0", "usage": {"input_tokens": 1000},
          "answers": {"d_outlook": {"type": "choice", "choice": "improved", "confidence": 0.4,
                                    "probabilities": {"improved": 0.6, "same": 0.2, "worsened": 0.1, "not_comparable": 0.1}},
                      "d_momentum": {"type": "choice", "choice": "stronger",
                                     "probabilities": {"stronger": 0.5, "same": 0.3, "weaker": 0.2}},
                      "d_new": {"type": "noul", "noul": 0.3}}}


class AnnotateFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [patch.object(A, "SHADOW", self.tmp / "shadow.jsonl"), patch.object(A, "ANN", self.tmp / "ann.jsonl"),
                        patch.object(A, "CACHE", self.tmp / "cache"), patch.object(A, "TICKERS", self.tmp / "tickers.json"),
                        patch.object(A, "_sleep", lambda s: None), patch.object(A, "jev_key", lambda: KEY)]
        for p in self.patches:
            p.start()
        A._SUBS.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        A._SUBS.clear()

    def _signals(self, n, extra=()):
        rows = [{"kind": "signal", "session": "2026-07-30", "ticker": f"TK{i}", "event_date": "2026-07-29", "hour": "AMC",
                 "score": 0.03, "selected_I1": True} for i in range(n)]
        rows.append({"kind": "signal", "session": "2026-07-30", "ticker": "NOPE", "event_date": "2026-07-29",
                     "selected_I1": False})
        rows += list(extra)
        A.SHADOW.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def _run(self, post, n_tickers=3):
        buf = io.StringIO()
        with patch.object(A.requests, "get", side_effect=fake_get_factory(n_tickers)), \
                patch.object(A.requests, "post", side_effect=post) as mp, redirect_stdout(buf):
            A.cmd_annotate()
        A._SUBS.clear()
        return mp, buf.getvalue()

    def test_body_is_frozen_research_call_and_idempotent(self):
        self._signals(1)
        mp, _ = self._run(lambda *a, **k: Resp(js=JEV_OK))
        self.assertEqual(mp.call_count, 1)
        body = mp.call_args.kwargs["json"]
        q = r2.tail_questions(); q.update(r2.DIFF_Q)
        self.assertEqual(body["questions"], q)
        self.assertEqual(body["model"], "jev-1.13.0")
        self.assertEqual(set(body["state"]), {"press_release", "previous_quarter_release"})
        self.assertIn("current", body["state"]["press_release"])
        self.assertIn("previous", body["state"]["previous_quarter_release"])
        self.assertEqual(body["state"]["press_release"],
                         r2.extract(r1.mask(A.fetch_doc(1000, "1000-cur"), ["Tick0 Widgets Inc.", "TK0"])))
        self.assertNotIn("Tick0", body["state"]["press_release"])   # 가림 적용
        rows = A._jl(A.ANN)
        self.assertEqual(len(rows), 1)
        j = rows[0]["jev"]
        self.assertEqual((j["status"], j["cur_acc"], j["prior_acc"], j["prior_gap"]), ("ok", "1000-cur", "1000-pri", 91))
        self.assertEqual(j["d_outlook_improved"], 0.6)
        self.assertEqual(j["d_outlook_argmax"], "improved")
        self.assertEqual(rows[0]["issuer_type"], "domestic")
        self.assertFalse(rows[0]["rare_neg_8k_30d"])
        # 재실행 → 추가 기록·호출 없음
        mp2, out = self._run(lambda *a, **k: Resp(js=JEV_OK))
        self.assertEqual(mp2.call_count, 0)
        self.assertEqual(len(A._jl(A.ANN)), 1)
        self.assertIn("대상 없음", out)

    def test_api_error_retried_next_run_and_key_never_leaks(self):
        self._signals(1)
        mp, out = self._run(lambda *a, **k: Resp(500, text=f"upstream echoed Bearer {KEY}"))
        self.assertEqual(mp.call_count, 3)
        rows = A._jl(A.ANN)
        self.assertEqual(rows[-1]["jev"]["status"], "api_error")
        self.assertNotIn(KEY, A.ANN.read_text(encoding="utf-8"))
        self.assertNotIn(KEY, out)
        mp2, out2 = self._run(lambda *a, **k: Resp(js=JEV_OK))
        self.assertEqual(mp2.call_count, 1)
        latest = A._latest_ann()[("2026-07-30", "TK0")]
        self.assertEqual(latest["jev"]["status"], "ok")
        self.assertNotIn(KEY, A.ANN.read_text(encoding="utf-8") + out2)

    def test_budget_guard_50_calls(self):
        self._signals(60)
        mp, out = self._run(lambda *a, **k: Resp(js=JEV_OK), n_tickers=60)
        self.assertEqual(mp.call_count, 50)
        self.assertEqual(len(A._jl(A.ANN)), 50)
        self.assertIn("예산 이월 10", out)
        mp2, _ = self._run(lambda *a, **k: Resp(js=JEV_OK), n_tickers=60)
        self.assertEqual(mp2.call_count, 10)
        self.assertEqual(len(A._latest_ann()), 60)

    def test_budget_counts_retries(self):
        b = A.Budget(2)
        with patch.object(A.requests, "post", return_value=Resp(503, text="busy")) as mp:
            res, err = A.jev_post({"x": 1}, KEY, b)
        self.assertIsNone(res)
        self.assertEqual(mp.call_count, 2)
        self.assertEqual(b.left, 0)

    def test_no_cik_and_dry_writes_nothing(self):
        self._signals(0, extra=[{"kind": "signal", "session": "2026-07-30", "ticker": "ZZZZ", "event_date": "2026-07-29",
                                 "selected_I1": True}])
        buf = io.StringIO()
        with patch.object(A.requests, "get", side_effect=fake_get_factory(1)), \
                patch.object(A.requests, "post") as mp, redirect_stdout(buf):
            A.cmd_annotate(dry=True)
        self.assertEqual(mp.call_count, 0)
        self.assertFalse(A.ANN.exists())
        self.assertIn("no_cik", buf.getvalue())

    def test_settle_502_after_exit(self):
        self._signals(1, extra=[{"kind": "settle", "session": "2026-07-30", "ticker": "TK0", "hold": 20,
                                 "exit_session": "2026-08-27", "net_excess": 0.01}])
        self._run(lambda *a, **k: Resp(js=JEV_OK))
        with patch.object(A.requests, "get", side_effect=fake_get_factory(1)), redirect_stdout(io.StringIO()):
            A.cmd_settle()
            A._SUBS.clear()
            A.cmd_settle()                       # 멱등
        s = [r for r in A._jl(A.ANN) if r["kind"] == "settle_502"]
        self.assertEqual(len(s), 1)
        self.assertEqual((s[0]["item_502_during_hold"], s[0]["item_502_count"]), (False, 0))


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = [patch.object(A, "SHADOW", self.tmp / "s.jsonl"), patch.object(A, "ANN", self.tmp / "a.jsonl")]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def _write(self, n):
        rng = np.random.RandomState(0)
        sh, an = [], []
        for i in range(n):
            ses = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
            prob = rng.rand()
            sh.append({"kind": "signal", "session": ses, "ticker": f"T{i}", "selected_I1": True})
            sh.append({"kind": "settle", "session": ses, "ticker": f"T{i}", "hold": 20, "exit_session": "x",
                       "net_excess": 0.05 * (prob > 0.5) - 0.02 + rng.randn() * 0.01})
            an.append({"kind": "annotation", "session": ses, "ticker": f"T{i}",
                       "issuer_type": "foreign" if i % 10 == 0 else "domestic", "rare_neg_8k_30d": False,
                       "jev": {"status": "ok", "d_outlook_improved": prob} if i % 7 else {"status": "no_8k"}})
        A.SHADOW.write_text("\n".join(json.dumps(r) for r in sh), encoding="utf-8")
        A.ANN.write_text("\n".join(json.dumps(r) for r in an), encoding="utf-8")

    def _out(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            A.cmd_report()
        return buf.getvalue()

    def test_insufficient_below_100(self):
        self._write(99)
        self.assertIn("표본 부족", self._out())

    def test_runs_at_100(self):
        self._write(120)                         # 성과 120건 중 Jev ok ~102 — 게이트는 성과 건수 기준
        out = self._out()
        self.assertIn("20세션 성과 120", out)
        self.assertIn("① d_outlook_improved", out)
        self.assertIn("→ 통과", out)
        self.assertIn("② foreign−domestic", out)


if __name__ == "__main__":
    unittest.main()
