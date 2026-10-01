from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import kr_target_price_shadow as S  # noqa: E402

KST = timezone(timedelta(hours=9))
SECRET_TOKEN = "TOKEN_SECRET_abcdef0123456789"
SECRET_KEY = "APPSECRET_zyxw9876"


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    bf = tmp_path / "bf"
    (bf / "raw").mkdir(parents=True)
    monkeypatch.setattr(S, "BF_REPORTS", bf / "reports.csv")
    monkeypatch.setattr(S, "BF_CKPT", bf / "checkpoint.json")
    monkeypatch.setattr(S, "OPDIR", tmp_path / "op")
    monkeypatch.setattr(S, "STATE", tmp_path / "op" / "state.json")
    monkeypatch.setattr(S, "RUNS", tmp_path / "op" / "runs.jsonl")
    monkeypatch.setattr(S, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(S, "SETTLE", tmp_path / "settle.jsonl")
    monkeypatch.setattr(S, "KR_PX", tmp_path / "px")
    (tmp_path / "px").mkdir()
    monkeypatch.setattr(S, "SLEEP", lambda s: None)
    monkeypatch.setattr(S, "GAP_SEC", 0.0)
    monkeypatch.setattr(S, "market_map", lambda: {"000001": "KOSPI", "000002": "KOSDAQ"})
    monkeypatch.setattr(S, "BROKER_MAP_PATH", tmp_path / "broker_map.json")
    monkeypatch.setattr(S, "BROKER_MAP", {})
    yield


def _bf(tmp_rows, done=("000001", "000002")):
    lines = ["ticker,date,broker,target,opinion,ticker_complete"]
    lines += [f"{t},{d},{b},{g},{op},1" for t, d, b, g, op in tmp_rows]
    S.BF_REPORTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    S.BF_CKPT.write_text(json.dumps({"tickers": {t: {"status": "done"} for t in done}}), encoding="utf-8")


def _px(code: str, dvol_eok: float):
    c = 10000.0
    v = dvol_eok * 1e8 / c
    lines = ["date,open,high,low,close,volume"] + [f"2026-09-{d:02d},{c},{c},{c},{c},{v}" for d in range(1, 21)]
    (S.KR_PX / f"kr_{code}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


class FakeKis:
    def __init__(self, token=SECRET_TOKEN):
        self.token = token
        self.headers_calls = 0

    def load_token(self, market="KR"):
        return {"access_token": self.token} if self.token else None

    def _headers(self, token, tr_id="", market="KR"):
        assert token, "빈 토큰으로 _headers 호출 금지(신규 발급 경로)"
        self.headers_calls += 1
        return {"authorization": f"Bearer {token}", "appsecret": SECRET_KEY, "tr_id": tr_id}

    def get_kis_market_profile(self, market):
        return SimpleNamespace(base_url="https://example.invalid")


class FakeResp:
    def __init__(self, rows, status=200, rt="0", msg=""):
        self.status_code = status
        self._b = {"rt_cd": rt, "msg_cd": msg, "output": rows}
        self.encoding = None

    def json(self):
        return self._b


def _row(d8, broker, goal="20000", op="매수", pc="10000"):
    return {"stck_bsop_date": d8, "mbcr_name": broker, "hts_goal_prc": goal, "invt_opnn": op, "stck_prdy_clpr": pc}


def _setup_collect(monkeypatch, now, get, kis=None):
    kis = kis or FakeKis()
    monkeypatch.setattr(S, "_kis", lambda: kis)
    monkeypatch.setattr(S, "NOW", lambda: now)
    monkeypatch.setattr(S, "HTTP_GET", get)
    return kis


def _args(**kw):
    d = {"dry": False, "max_tickers": 0, "max_calls": 0}
    d.update(kw)
    return SimpleNamespace(**d)


# ------------------------------------------------------------------ 창 가드·토큰·상한
@pytest.mark.parametrize("hm,state", [((15, 59), "closed"), ((16, 4), "closed"), ((16, 5), "ok"), ((22, 9), "ok"),
                                      ((22, 10), "skip"), ((22, 34), "skip"), ((22, 35), "ok"), ((23, 58), "ok"),
                                      ((23, 59), "closed"), ((0, 30), "closed"), ((7, 0), "closed")])
def test_window_state(hm, state):
    assert S.window_state(datetime(2026, 9, 30, *hm, tzinfo=KST)) == state


@pytest.mark.parametrize("hm", [(15, 0), (22, 20), (23, 59), (2, 0)])
def test_no_calls_outside_window(monkeypatch, hm):
    _bf([("000001", "2026-01-05", "A증권", 20000, "매수")])
    _px("000001", 50)
    calls = []
    kis = _setup_collect(monkeypatch, datetime(2026, 9, 30, *hm, tzinfo=KST), lambda *a, **k: calls.append(1))
    assert S.cmd_collect(_args()) == 0
    assert calls == [] and kis.headers_calls == 0


def test_token_none_exits_without_call(monkeypatch):
    _bf([("000001", "2026-01-05", "A증권", 20000, "매수")])
    _px("000001", 50)
    calls = []
    kis = _setup_collect(monkeypatch, datetime(2026, 9, 30, 17, 0, tzinfo=KST), lambda *a, **k: calls.append(1),
                         FakeKis(token=None))
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert S.cmd_collect(_args()) == 0
    assert calls == [] and kis.headers_calls == 0
    assert "STOP_TOKEN_NONE" in buf.getvalue()


def test_daily_cap(monkeypatch):
    _bf([], done=tuple(f"00000{i}" for i in range(1, 6)))
    for i in range(1, 6):
        _px(f"00000{i}", 50 + i)
    S.OPDIR.mkdir(parents=True, exist_ok=True)
    S.STATE.write_text(json.dumps({"calls": {"2026-09-30": S.DAILY_CAP - 2}, "tickers": {}}), encoding="utf-8")
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append(params["FID_INPUT_ISCD"])
        return FakeResp([])
    _setup_collect(monkeypatch, datetime(2026, 9, 30, 17, 0, tzinfo=KST), get)
    buf = io.StringIO()
    with redirect_stdout(buf):
        S.cmd_collect(_args())
    assert len(calls) == 2
    assert "STOP_DAILY_CAP" in buf.getvalue()
    assert json.loads(S.STATE.read_text(encoding="utf-8"))["calls"]["2026-09-30"] == S.DAILY_CAP


def test_consecutive_failures_stop_and_no_secret_leak(monkeypatch):
    _bf([], done=tuple(f"00000{i}" for i in range(1, 6)))
    for i in range(1, 6):
        _px(f"00000{i}", 50 + i)
    n = []

    def get(url, headers=None, params=None, timeout=None):
        n.append(1)
        raise RuntimeError(f"boom {headers['authorization']} {headers['appsecret']}")
    _setup_collect(monkeypatch, datetime(2026, 9, 30, 17, 0, tzinfo=KST), get)
    buf = io.StringIO()
    with redirect_stdout(buf):
        S.cmd_collect(_args())
    out = buf.getvalue()
    assert len(n) == S.MAX_CONSEC_FAIL
    assert "STOP_CONSEC_FAIL" in out
    assert SECRET_TOKEN not in out and SECRET_KEY not in out
    for p in S.OPDIR.glob("*"):
        txt = p.read_text(encoding="utf-8")
        assert SECRET_TOKEN not in txt and SECRET_KEY not in txt


# ------------------------------------------------------------------ 수집·중복 제거·멱등
def test_collect_dedupe_and_idempotent(monkeypatch):
    _bf([("000001", "2026-01-05", "A증권", 20000, "매수")])
    _px("000001", 50)
    resp = [_row("20260929", "B증권"), _row("20260929", "B증권"), _row("20260929", "B증권", goal="21000")]
    seen_params = []

    def get(url, headers=None, params=None, timeout=None):
        seen_params.append(params)
        return FakeResp(list(resp))
    _setup_collect(monkeypatch, datetime(2026, 9, 30, 17, 0, tzinfo=KST), get)
    S.cmd_collect(_args())
    rows = S.load_shadow_rows()
    assert len(rows) == 2 and all(r["fetch_ts"].startswith("2026-09-30T17:00") for r in rows)
    assert seen_params[0]["FID_INPUT_DATE_1"] == "20260924" and seen_params[0]["FID_INPUT_DATE_2"] == "20260930"
    # 같은 날 재실행 → 이미 수집한 종목은 건너뜀
    S.cmd_collect(_args())
    assert len(seen_params) == 1
    # 다음 날 같은 행이 다시 와도 추가되지 않는다
    monkeypatch.setattr(S, "NOW", lambda: datetime(2026, 10, 1, 17, 0, tzinfo=KST))
    S.cmd_collect(_args())
    assert len(seen_params) == 2 and len(S.load_shadow_rows()) == 2


def test_collect_splits_at_cap(monkeypatch):
    _bf([("000001", "2026-01-05", "A증권", 20000, "매수")])
    _px("000001", 50)
    wins = []

    def get(url, headers=None, params=None, timeout=None):
        d1, d2 = params["FID_INPUT_DATE_1"], params["FID_INPUT_DATE_2"]
        wins.append((d1, d2))
        if (d1, d2) == ("20260924", "20260930"):
            return FakeResp([_row("20260925", f"X{k}") for k in range(100)])
        return FakeResp([_row(d1, "Y증권")])
    _setup_collect(monkeypatch, datetime(2026, 9, 30, 17, 0, tzinfo=KST), get)
    S.cmd_collect(_args())
    assert wins[1:] == [["20260924", "20260927"], ["20260928", "20260930"]] or wins[1:] == [
        ("20260924", "20260927"), ("20260928", "20260930")]
    assert {r["mbcr_name"] for r in S.load_shadow_rows()} == {"Y증권"}   # 상한 창의 행은 버린다


def test_new_listing_gets_full_history(monkeypatch):
    _bf([("000001", "2026-01-05", "A증권", 20000, "매수")], done=("000001",))
    _px("000009", 80)
    ps = []

    def get(url, headers=None, params=None, timeout=None):
        ps.append((params["FID_INPUT_ISCD"], params["FID_INPUT_DATE_1"]))
        return FakeResp([])
    _setup_collect(monkeypatch, datetime(2026, 9, 30, 17, 0, tzinfo=KST), get)
    S.cmd_collect(_args())
    assert ("000009", S.START) in ps
    st = json.loads(S.STATE.read_text(encoding="utf-8"))
    assert st["tickers"]["000009"]["history_full"] is True


# ------------------------------------------------------------------ R3 판정
def _shadow(rows):
    S.OPDIR.mkdir(parents=True, exist_ok=True)
    with (S.OPDIR / "202609.jsonl").open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _srow(code, d8, broker, op="매수", goal="20000", pc="10000", ts="2026-09-30T16:20:00+09:00"):
    return {"code": code, **_row(d8, broker, goal, op, pc), "fetch_ts": ts, "src": "inc7"}


def test_r3_detection_first_vs_existing_and_opinion():
    _bf([("000001", "2025-03-05", "A증권", 20000, "매수"), ("000002", "2025-03-05", "C증권", 20000, "매수")])
    _shadow([
        _srow("000001", "20260930", "A증권"),               # 기존 커버 → 아님
        _srow("000001", "20260930", "B증권", pc="10000", goal="15000"),   # 첫 등장 매수 → R3
        _srow("000002", "20260930", "D증권", op="중립"),     # 첫 등장이지만 의견 아님
        _srow("000002", "20260930", "E증권", op="Outperform", goal="14000", pc="10000"),
        _srow("000002", "20260930", "F증권", op="BUY", goal="12000", pc="10000"),
    ])
    S.cmd_signals(None)
    led = {r["key"]: r for r in S.read_jsonl(S.LEDGER)}
    assert set(led) == {"R3|000001|2026-09-30", "R3|000002|2026-09-30"}
    a, b = led["R3|000001|2026-09-30"], led["R3|000002|2026-09-30"]
    assert a["brokers"] == ["B증권"] and a["n_brokers"] == 1 and a["upside"] == 0.5 and a["upside_tercile"] == "high"
    assert b["brokers"] == ["E증권", "F증권"] and b["n_brokers"] == 2 and b["upside"] == 0.4 and b["market"] == "KOSDAQ"
    assert a["entry_date"] == "2026-10-01" and a["actionable"] is True


def test_backfill_only_event_is_not_new():
    _bf([("000001", "2026-09-30", "A증권", 20000, "매수")])
    _shadow([_srow("000001", "20260930", "A증권")])
    S.cmd_signals(None)
    assert S.read_jsonl(S.LEDGER) == []


def test_signals_idempotent_and_superseded():
    _bf([("000001", "2025-03-05", "A증권", 20000, "매수")])
    _shadow([_srow("000001", "20260929", "B증권")])
    S.cmd_signals(None)
    S.cmd_signals(None)
    led = S.read_jsonl(S.LEDGER)
    assert len(led) == 1 and led[0]["superseded"] is False
    # 더 이른 B증권 리포트가 뒤늦게 들어오면 첫 등장이 아니다 → 지우지 않고 superseded
    _shadow([_srow("000001", "20260915", "B증권", op="중립")])
    S.cmd_signals(None)
    led = S.read_jsonl(S.LEDGER)
    assert len(led) == 1 and led[0]["superseded"] is True


# ------------------------------------------------------------------ 진입 세션
@pytest.mark.parametrize("D,base,entry", [
    ("2026-09-30", "2026-09-30", "2026-10-01"),
    ("2026-10-02", "2026-10-02", "2026-10-06"),      # 10-05 대체공휴일(추석)
    ("2026-10-03", "2026-10-02", "2026-10-06"),      # 토요일 리포트 → 기준 금요일
    ("2026-10-08", "2026-10-08", "2026-10-12"),      # 10-09 한글날
    ("2026-09-23", "2026-09-23", "2026-09-28"),      # 09-24·25 추석
])
def test_entry_sessions_holidays(D, base, entry):
    b, e, x = S.entry_sessions(D)
    assert (b, e) == (base, entry)
    n, d = 1, date.fromisoformat(e)
    while d.isoformat() != x:
        d += timedelta(days=1)
        n += S.BS.kr_is_session(d)
    assert n == S.H


def test_after_open_detection_not_actionable():
    _bf([("000001", "2025-03-05", "A증권", 20000, "매수")])
    _shadow([_srow("000001", "20260929", "B증권", ts="2026-10-01T16:20:00+09:00"),   # 진입일(09-30) 시가 이후 발견
             _srow("000001", "20260930", "C증권", ts="2026-10-01T08:59:00+09:00")])  # 진입 10-01 09:00 이전
    S.cmd_signals(None)
    led = {r["signal_date"]: r for r in S.read_jsonl(S.LEDGER)}
    assert led["2026-09-29"]["entry_date"] == "2026-09-30" and led["2026-09-29"]["actionable"] is False
    assert led["2026-09-30"]["actionable"] is True


# ------------------------------------------------------------------ 정산(순수 함수)
def test_settle_rows_excess_and_placebo():
    import numpy as np

    cal = []
    d = date(2026, 1, 2)
    while len(cal) < 90:
        if S.BS.kr_is_session(d):
            cal.append(d.isoformat())
        d += timedelta(days=1)
    tick = [f"{k:06d}" for k in range(1, 221)] + ["069500", "229200"]
    bars = {}
    for j, t in enumerate(tick):
        px = 10000.0 + 10 * j
        bars[t] = [(c, px, px * (1 + 0.001 * i if t == "000001" else 1.0), 5e5) for i, c in enumerate(cal)]
    bk = S.BS.Book(bars, cal)
    D = cal[30]
    reps = [{"t": t, "d": cal[25], "b": "Z", "g": 0.0, "op": "중립", "src": "backfill"} for t in tick[:220]]
    reps.append({"t": "000001", "d": D, "b": "N증권", "g": 20000.0, "op": "매수", "src": "shadow",
                 "fetch_ts": D + "T16:20:00+09:00", "pc": 10000.0})
    bench = S.Bench(bk, reps)
    led = [{"key": f"R3|000001|{D}", "ticker": "000001", "signal_date": D, "actionable": True, "history_ok": True}]

    def naver(code):
        j = bk.col[code]
        return {c: (bk.o[i, j], 0, 0, bk.c[i, j], 0) for i, c in enumerate(cal)}
    out = S.settle_rows(led, reps, bk, bench, {}, naver)
    assert len(out) == 1
    r = out[0]
    assert r["taken"] is True and r["entry"] == cal[31] and r["exit"] == cal[50]
    exp = (bk.c[50, 0] / bk.o[31, 0] - 1) * 100 - S.COST
    assert abs(r["net"] - exp) < 1e-9
    assert r["ex_cov"] is not None and r["ex_cov"] > 0
    assert r["placebo_done"] is True and r["placebo_a_n"] > 0 and r["diff_a"] is not None
    assert np.isfinite(r["ex_k200"])


# ------------------------------------------------------------------ 증권사 이름 정규화 (v4)
RENAME = {"현대차증권": "현대차", "현대차투자": "현대차", "하나금융투자": "하나증권"}


def test_canon_broker_maps_known_and_passes_unknown(monkeypatch):
    S.BROKER_MAP_PATH.write_text(json.dumps({"map": RENAME}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(S, "BROKER_MAP", None)          # 파일에서 로드
    assert S.canon_broker(" 현대차증권 ") == "현대차"
    assert S.canon_broker("하나금융투자") == "하나증권"
    assert S.canon_broker("키움") == "키움"               # 맵에 없음 → 통과
    assert S.canon_broker("현대차증권", bmap={}) == "현대차증권"


def test_missing_map_file_passes_through(monkeypatch):
    monkeypatch.setattr(S, "BROKER_MAP", None)          # 파일 없음 → 빈 맵
    assert S.canon_broker("현대차증권") == "현대차증권"


def test_rename_is_not_new_coverage(monkeypatch):
    monkeypatch.setattr(S, "BROKER_MAP", dict(RENAME))
    _bf([("000001", "2018-10-29", "현대차증권", 68000, "BUY")])
    _shadow([_srow("000001", "20260930", "현대차", op="BUY")])
    bf, sh = S.read_backfill_reports(), S.load_shadow_rows()
    assert S.r3_events(S.merge_reports(bf, sh)) == {}                     # 정규 이름: 첫 등장 아님
    raw = S.r3_events(S.merge_reports(bf, sh, bmap={}))                   # 원 이름: 인공 신규 커버
    assert list(raw) == [("000001", "2026-09-30")]
    fr = {(r["t"], r["b"]): r for r in S.first_rows(S.merge_reports(bf, sh))}
    assert fr[("000001", "현대차")]["d"] == "2018-10-29" and fr[("000001", "현대차")]["b_raw"] == "현대차증권"
    S.cmd_signals(None)
    assert S.read_jsonl(S.LEDGER) == []


def test_same_day_two_raw_names_dedup_to_one_row(monkeypatch):
    monkeypatch.setattr(S, "BROKER_MAP", dict(RENAME))
    _bf([("000001", "2021-08-02", "현대차증권", 50000, "BUY"), ("000001", "2021-08-02", "현대차", 51000, "BUY")])
    reps = S.merge_reports(S.read_backfill_reports(), [])
    assert len(reps) == 1 and reps[0]["b"] == "현대차" and reps[0]["b_raw"] == "현대차증권"


def test_existing_ledger_row_superseded_by_rename_not_deleted(monkeypatch):
    _bf([("000001", "2018-10-29", "현대차증권", 68000, "BUY"), ("000002", "2025-03-05", "A증권", 20000, "매수")])
    _shadow([_srow("000001", "20260930", "현대차", op="BUY"), _srow("000002", "20260930", "B증권")])
    S.cmd_signals(None)                                   # 맵 없음(기존 동작) → 둘 다 원장에 들어감
    assert {r["key"] for r in S.read_jsonl(S.LEDGER)} == {"R3|000001|2026-09-30", "R3|000002|2026-09-30"}
    monkeypatch.setattr(S, "BROKER_MAP", dict(RENAME))
    S.cmd_signals(None)
    S.cmd_signals(None)                                   # 멱등
    led = {r["key"]: r for r in S.read_jsonl(S.LEDGER)}
    assert len(led) == 2                                   # 삭제 없음
    a, b = led["R3|000001|2026-09-30"], led["R3|000002|2026-09-30"]
    assert a["superseded"] is True and a["superseded_reason"] == "broker_rename" and a.get("superseded_ts")
    assert b["superseded"] is False and "superseded_reason" not in b
    new = S.build_signal_rows(S.merge_reports(S.read_backfill_reports(), S.load_shadow_rows()), set(), {},
                              datetime(2026, 9, 30, 23, 0, tzinfo=KST))
    assert [r["key"] for r in new] == ["R3|000002|2026-09-30"] and new[0]["brokers_raw"] == ["B증권"]
    assert [r for r in S.read_jsonl(S.LEDGER) if not r.get("superseded")] == [b]


def test_earlier_row_supersede_reason():
    _bf([("000001", "2025-03-05", "A증권", 20000, "매수")])
    _shadow([_srow("000001", "20260929", "B증권")])
    S.cmd_signals(None)
    _shadow([_srow("000001", "20260915", "B증권", op="중립")])
    S.cmd_signals(None)
    r = S.read_jsonl(S.LEDGER)[0]
    assert r["superseded"] is True and r["superseded_reason"] == "earlier_row"
