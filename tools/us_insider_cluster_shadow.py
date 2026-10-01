# -*- coding: utf-8 -*-
"""US 내부자 군집(재키잉) 쉐도우 레인 (2026-09-29) — 주문 없음, 원장 기록·정산·판정만.

동결 규칙 (docs/reports/k1_us_smartmoney_20260929.md "추가 1"·"다음 단계 1", 재현 코드
tools/research/research_k1_us_followup_20260929.py::ev_rekey / research_k1_us_smartmoney_20260929.py::attach·dedupe60·stats)
- 매수 accession: Form 4(4/A 제외), 비파생 거래 코드 P · 취득 A · 수량 > 0 · 가격 > 0, 심볼은 T3 정리 규칙.
- 소유자 그룹: 매수 accession에 함께 신고한 보고자 CIK를 union-find로 묶는다(전 이력 공동신고 = 신원 정의).
- 군집: 같은 티커, filing일 F 기준 [F−29, F] 달력일 안의 서로 다른 그룹 ≥ 3. 신호일 = 처음 도달한 F.
  같은 티커는 마지막 "도달일"(방출 안 된 날 포함)로부터 90일 안에는 재신호 없음.
- 변형(독립 억제): U5r_all(1차 — 운영자 지정) / U5r_do_all(보고자를 이사·임원으로 거른 뒤 그룹 집합 계산,
  그런 보고자가 없는 accession 제외).
- 필터(신호일 s = F 이하 마지막 세션의 종가 기준): 원가격 ≥ $3, dv20(20일 평균 종가×거래량, min 15) ≥ $1M,
  가격 일치 vwap(창 안 accession Σ금액/Σ주식) / close ∈ [0.67, 1.5]. 플래그: 바닥권 rpos(252일, min 200) ≤ 0.35,
  dv20 ≥ $5M. 같은 규칙·티커 tradable 신호는 진입 60세션 내 첫 건만(dedupe60).
- 진입 두 가지를 모두 기록·정산한다(판정 1차 = t0_filing):
  · t0_filing = s 다음 세션 시가 — 동결 문구 "filing 다음 시가"(사전등록 합격선이 정의된 기준).
  · t0_accept = 운영자 매핑: 접수(ET)가 세션일 16:00 전이면 그 세션이 신호일, 아니면 다음 세션 → 그 다음 세션 시가.
    둘은 16:00~17:30 접수분에서만 다르다.
- 보유 H ∈ {20, 60} 세션, 청산 = 진입 포함 H번째 세션 종가(수정 시가 = open × adj/close). 비용 왕복 0.44%.
- 벤치: IWM 같은 창, 규모매칭 = 신호일 s 트레이더블(≥$3·≥$1M) 연구 유니버스의 dv20 10분위 같은 분위 평균(분위당 ≥5,
  트레이더블 ≥100).
- 판정(사전등록): H60 · IWM 대비 비용 후 초과 > 0 이고 날짜(진입일) 클러스터 t ≥ 2.

원장: data/shadow/us_insider_cluster_shadow.jsonl (append, key = rule|ticker|F 멱등)
정산: data/shadow/us_insider_cluster_shadow_settle.jsonl (key|H|entry 단위, 재작성)
사용:
  python tools/us_insider_cluster_shadow.py signals [--dry] [--since YYYY-MM-DD]
  python tools/us_insider_cluster_shadow.py settle [--dry]
  python tools/us_insider_cluster_shadow.py report [--json PATH]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORM4_DB = ROOT / "data" / "insider" / "form4.db"
HIST_DB = ROOT / "data" / "insider" / "t3_history.db"
UNIVERSE_JSON = ROOT / "data" / "insider" / "universe_tickers.json"
PX_DIR = ROOT / "data" / "insider" / "prices"
LEDGER = ROOT / "data" / "shadow" / "us_insider_cluster_shadow.jsonl"
SETTLE = ROOT / "data" / "shadow" / "us_insider_cluster_shadow_settle.jsonl"
T3_TRANS = ROOT / "data" / "analysis" / "model_T3_form4_20260927" / "trans"
T3_OWNERS = ROOT / "data" / "analysis" / "k1_us_20260929" / "t3_owners.csv.gz"
T3_TICKERS = ROOT / "data" / "analysis" / "k1_us_20260929" / "t3_ticker_list.json"
K1_PX = ROOT / "data" / "analysis" / "k1_us_20260929" / "prices"

COST = 0.0044
HS = (20, 60)
WINDOW_DAYS = 29
SUPPRESS_DAYS = 90
MIN_GROUPS = 3
DEDUPE_SESS = 60
RULES = {"U5r_all": False, "U5r_do_all": True}
PRIMARY = ("U5r_all", 60, "filing", "iwm")

# NYSE 휴장일(생성 시점 세션 달력용 — 정산은 실제 IWM 거래일로 스냅)
NYSE_HOLIDAYS = {
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
}


def log(msg: str) -> None:
    print(f"[INSIDER-SHADOW] {msg}", flush=True)


# ------------------------------------------------------------------ 세션 달력
def is_session(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def next_session(d: date) -> date:
    d = d + timedelta(days=1)
    while not is_session(d):
        d += timedelta(days=1)
    return d


def prev_or_same_session(d: date) -> date:
    while not is_session(d):
        d -= timedelta(days=1)
    return d


def entry_from_filing(fdate: date) -> Tuple[date, date]:
    """(s, t0) — 연구: s = F 이하 마지막 세션, t0 = s 다음 세션."""
    s = prev_or_same_session(fdate)
    return s, next_session(s)


def entry_from_acceptance(acc_dt: Optional[str]) -> Tuple[Optional[date], Optional[date]]:
    """(신호 세션, t0). 접수(ET)가 세션일 16:00 전 → 그 세션, 아니면 다음 세션. 진입 = 신호 세션 다음 세션 시가."""
    if not acc_dt:
        return None, None
    dt = datetime.strptime(acc_dt, "%Y-%m-%d %H:%M:%S")
    d = dt.date()
    if is_session(d) and dt.hour < 16:
        s = d
    else:
        s = next_session(d)
    return s, next_session(s)


def now_et() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return datetime.utcnow() - timedelta(hours=4)


# ------------------------------------------------------------------ 데이터 적재
def import_t3_history(hist_db: Path = HIST_DB) -> bool:
    """연구 캐시(T3 분기셋 매수 accession·보고자)를 쉐도우 전용 DB로 한 번 복사 — 이후 연구 캐시 경로에 의존하지 않는다."""
    if not T3_TRANS.exists() or not T3_OWNERS.exists():
        return False
    fr = [pd.read_csv(f, dtype={"ticker": str}, usecols=["ACCESSION_NUMBER", "ticker", "fdate", "bought", "value"])
          for f in sorted(T3_TRANS.glob("*.csv.gz"))]
    a = pd.concat(fr, ignore_index=True).drop_duplicates("ACCESSION_NUMBER")
    o = pd.read_csv(T3_OWNERS)
    hist_db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(hist_db))) as conn, conn:
        a.rename(columns={"ACCESSION_NUMBER": "accession"}).to_sql("hist_acc", conn, if_exists="replace", index=False)
        o.rename(columns={"ACCESSION_NUMBER": "accession"}).to_sql("hist_owner", conn, if_exists="replace", index=False)
    return True


def load_history(hist_db: Path = HIST_DB) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if not hist_db.exists():
        import_t3_history(hist_db)
    if not hist_db.exists():
        return (pd.DataFrame(columns=["accession", "ticker", "fdate", "bought", "value", "accept"]),
                pd.DataFrame(columns=["accession", "cik", "do"]))
    with closing(sqlite3.connect(f"file:{hist_db}?mode=ro", uri=True)) as conn:
        a = pd.read_sql("SELECT accession, ticker, fdate, bought, value FROM hist_acc", conn)
        o = pd.read_sql("SELECT accession, cik, do FROM hist_owner", conn)
    a["accept"] = None
    return a, o


def load_collected(db: Path = FORM4_DB) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    """일별 수집 DB → (매수 accession, 보고자, 수집 완료일)."""
    if not db.exists():
        return (pd.DataFrame(columns=["accession", "ticker", "fdate", "bought", "value", "accept"]),
                pd.DataFrame(columns=["accession", "cik", "do"]), [])
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
        a = pd.read_sql(
            "SELECT f.accession AS accession, f.ticker AS ticker, f.filing_date AS fdate, f.acceptance_dt AS accept, "
            "SUM(t.shares) AS bought, SUM(t.shares * t.price) AS value "
            "FROM filings f JOIN transactions t ON t.accession = f.accession "
            "WHERE f.form_type = '4' AND t.code = 'P' AND t.acq_disp = 'A' AND t.shares > 0 AND t.price > 0 "
            "AND COALESCE(f.ticker, '') <> '' GROUP BY f.accession", conn)
        o = pd.read_sql("SELECT accession, owner_cik AS cik, (is_dir = 1 OR is_off = 1) AS do FROM owners", conn)
        days = [r[0] for r in conn.execute("SELECT day FROM days WHERE status IN ('done','no_index')")]
    o = o[o.accession.isin(set(a.accession))]
    return a, o, days


def combine(parts: Iterable[Tuple[pd.DataFrame, pd.DataFrame]]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    A = pd.concat([p[0] for p in parts], ignore_index=True).drop_duplicates("accession", keep="last")
    O = pd.concat([p[1] for p in parts], ignore_index=True)
    O = O.assign(cik=pd.to_numeric(O.cik, errors="coerce")).dropna(subset=["cik"])
    O["cik"] = O.cik.astype("int64")
    O["do"] = O["do"].astype(int)
    O = O.drop_duplicates(["accession", "cik"])
    A["fdate"] = pd.to_datetime(A.fdate)
    return A, O


# ------------------------------------------------------------------ 규칙 (연구 코드 재현)
def build_groups(o: pd.DataFrame) -> Dict[int, int]:
    """research_k1_us_followup_20260929.groups 와 같은 union-find."""
    par: Dict[int, int] = {}

    def f(x):
        while par.get(x, x) != x:
            par[x] = par.get(par[x], par[x])
            x = par[x]
        return x
    for _, g in o.groupby("accession"):
        c = list(g.cik.astype(int))
        for x in c[1:]:
            a, b = f(c[0]), f(x)
            if a != b:
                par[b] = a
    return {int(c): f(int(c)) for c in o.cik.unique()}


def find_events(acc: pd.DataFrame, own: pd.DataFrame, gm: Dict[int, int], do_only: bool,
                since: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """ev_rekey 재현. 반환: ticker, date(F), vwap, n_groups, n_acc, trigger_accession, trigger_accept."""
    o = own.copy()
    o["grp"] = o.cik.astype(int).map(gm)
    if do_only:
        o = o[o["do"] == 1]
    og = o.groupby("accession").grp.apply(lambda s: frozenset(s))
    a = acc[acc.accession.isin(og.index)].copy()
    if since is not None:  # 억제 체인·30일 창에 필요한 만큼만(since−120일) — 결과는 동일
        a = a[a.fdate >= since - pd.Timedelta(days=WINDOW_DAYS + SUPPRESS_DAYS + 1)]
    a["grps"] = a.accession.map(og)
    a = a.sort_values(["ticker", "fdate", "accession"])
    ev = []
    for t, g in a.groupby("ticker"):
        fd = g.fdate.values
        gs = g.grps.values
        last = None
        for i in range(len(g)):
            F = fd[i]
            m = (fd >= F - np.timedelta64(WINDOW_DAYS, "D")) & (fd <= F)
            u = set()
            for s in gs[m]:
                u |= s
            if len(u) >= MIN_GROUPS:
                if last is None or (F - last) / np.timedelta64(1, "D") > SUPPRESS_DAYS:
                    vw = g.value.values[m].sum() / max(g.bought.values[m].sum(), 1e-9)
                    trig = _trigger(g[m], F)
                    ev.append((t, pd.Timestamp(F), float(vw), len(u), int(m.sum()), trig[0], trig[1],
                               sorted(g.accession.values[m].tolist())))
                last = F
    df = pd.DataFrame(ev, columns=["ticker", "date", "vwap", "n_groups", "n_acc", "trigger_accession",
                                   "trigger_accept", "window_accessions"])
    df = df.drop_duplicates(["ticker", "date"])
    if since is not None:
        df = df[df.date >= since]
    return df.reset_index(drop=True)


def _trigger(win: pd.DataFrame, F) -> Tuple[Optional[str], Optional[str]]:
    """F 당일 accession을 접수 순으로 더해 그룹 수가 처음 MIN_GROUPS에 닿은 accession·접수시각."""
    prev = win[win.fdate.values < F]
    u = set()
    for s in prev.grps:
        u |= s
    today = win[win.fdate.values == F].copy()
    today["_k"] = today["accept"].fillna("9999")
    for r in today.sort_values(["_k", "accession"]).itertuples():
        u |= r.grps
        if len(u) >= MIN_GROUPS:
            return r.accession, (r.accept if isinstance(r.accept, str) else None)
    return None, None


# ------------------------------------------------------------------ 가격
def fetch_prices(tickers: List[str], start: str, end: str, batch: int = 100, pause: float = 8.0) -> pd.DataFrame:
    """yfinance 일봉(auto_adjust=False) → long(date,ticker,open,close,adj,volume). 연구 stage_prices 형식."""
    import yfinance as yf
    rows = []
    tickers = sorted(set(tickers))
    for bi in range(0, len(tickers), batch):
        b = tickers[bi:bi + batch]
        df = None
        for attempt in range(4):
            try:
                df = yf.download(b, start=start, end=end, auto_adjust=False, actions=True, group_by="ticker",
                                 threads=2, progress=False)
            except Exception as exc:  # noqa: BLE001
                log(f"yfinance 재시도 {exc}")
                time.sleep(60)
                continue
            errs = " ".join(str(v) for v in getattr(yf.shared, "_ERRORS", {}).values())
            if "Rate" in errs or "Too Many" in errs:
                log("yfinance rate limited — 600s")
                df = None
                time.sleep(600)
                continue
            break
        if df is None or len(df) == 0:
            continue
        for t in b:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                continue
            sub = sub.dropna(subset=["Close"])
            if len(sub) == 0:
                continue
            rows.append(pd.DataFrame({"date": sub.index.strftime("%Y-%m-%d"), "ticker": t,
                                      "open": sub["Open"].astype(float).values, "close": sub["Close"].astype(float).values,
                                      "adj": sub["Adj Close"].astype(float).values,
                                      "volume": sub["Volume"].astype(float).values}))
        if bi + batch < len(tickers):
            time.sleep(pause)
    if not rows:
        return pd.DataFrame(columns=["date", "ticker", "open", "close", "adj", "volume"])
    return pd.concat(rows, ignore_index=True)


def matrices(px: pd.DataFrame) -> Dict:
    """IWM 거래일 달력에 정렬한 행렬(연구 build_matrices와 같은 정의)."""
    d = px[(px.close > 0) & (px.adj > 0) & (px.open > 0)].drop_duplicates(["ticker", "date"], keep="last")
    cal = np.array(sorted(d[d.ticker == "IWM"].date.unique()))
    tick = np.array(sorted(d.ticker.astype(str).unique()))
    di = pd.Index(cal).get_indexer(d.date)
    ti = pd.Index(tick).get_indexer(d.ticker.astype(str))
    ok = di >= 0
    di, ti, d = di[ok], ti[ok], d[ok]
    shp = (len(cal), len(tick))
    M: Dict = {"cal": cal, "tick": tick, "tix": {t: i for i, t in enumerate(tick)}}
    f_ = d.adj.values / d.close.values
    for k, v in (("aopen", d.open.values * f_), ("aclose", d.adj.values), ("close", d.close.values),
                 ("dv", d.close.values * d.volume.values)):
        m = np.full(shp, np.nan)
        m[di, ti] = v
        M[k] = m
    M["dv20"] = pd.DataFrame(M["dv"]).rolling(20, min_periods=15).mean().values
    ac = pd.DataFrame(M["aclose"])
    hi = ac.rolling(252, min_periods=200).max().values
    lo = ac.rolling(252, min_periods=200).min().values
    with np.errstate(invalid="ignore", divide="ignore"):
        M["rpos"] = (M["aclose"] - lo) / (hi - lo)
    return M


# ------------------------------------------------------------------ 원장
def read_jsonl(p: Path) -> List[Dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def coverage(days_done: List[str], hist_max: Optional[pd.Timestamp]):
    s = set(days_done)

    def covered(d0: date, d1: date) -> bool:
        d = d0
        while d <= d1:
            if d.weekday() < 5:
                if not ((hist_max is not None and d <= hist_max.date()) or d.isoformat() in s):
                    return False
            d += timedelta(days=1)
        return True
    return covered


def build_rows(ev: pd.DataFrame, rule: str, M: Optional[Dict], covered, ledger_prev: List[Dict],
               detected: datetime) -> Tuple[List[Dict], List[str]]:
    """이벤트 → 원장 행. 가격 미게시(신호일 IWM 없음)는 보류(다음 실행 재시도)."""
    rows, pending = [], []
    kept_t0: Dict[str, date] = {}
    for r in ledger_prev:
        if r.get("rule") == rule and r.get("tradable") and not r.get("dedupe60_drop"):
            t0 = date.fromisoformat(r["t0_filing"])
            if r["ticker"] not in kept_t0 or t0 > kept_t0[r["ticker"]]:
                kept_t0[r["ticker"]] = t0
    for e in ev.sort_values("date").itertuples(index=False):
        F = e.date.date()
        s, t0 = entry_from_filing(F)
        s_acc, t0_acc = entry_from_acceptance(e.trigger_accept)
        row = {"key": f"{rule}|{e.ticker}|{F.isoformat()}", "rule": rule, "ticker": e.ticker, "filing_date": F.isoformat(),
               "trigger_accession": e.trigger_accession, "acceptance_dt_et": e.trigger_accept,
               "n_groups": int(e.n_groups), "n_acc_window": int(e.n_acc), "window_accessions": list(e.window_accessions),
               "vwap": round(float(e.vwap), 4), "s_date": s.isoformat(), "t0_filing": t0.isoformat(),
               "signal_session_accept": s_acc.isoformat() if s_acc else None,
               "t0_accept": t0_acc.isoformat() if t0_acc else None,
               "window_complete": bool(covered(F - timedelta(days=WINDOW_DAYS), F)),
               "suppress_history_complete": bool(covered(F - timedelta(days=WINDOW_DAYS + SUPPRESS_DAYS), F)),
               "detected_at_et": detected.strftime("%Y-%m-%d %H:%M"),
               "actionable": bool(detected.date() < t0 or (detected.date() == t0 and detected.hour < 9)),
               "hold_sessions": 60, "cost_roundtrip": COST}
        close = dv = rp = None
        if M is not None:
            cal = M["cal"]
            si = int(np.searchsorted(cal, s.isoformat(), side="right")) - 1
            if si < 0 or cal[si] != s.isoformat():
                pending.append(row["key"])
                continue
            j = M["tix"].get(e.ticker)
            if j is not None:
                c, d_ = M["close"][si, j], M["dv20"][si, j]
                close = float(c) if np.isfinite(c) else None
                dv = float(d_) if np.isfinite(d_) else None
                rp_ = M["rpos"][si, j]
                rp = float(rp_) if np.isfinite(rp_) else None
        ratio = (float(e.vwap) / close) if close else None
        row.update({"close_s": close, "dv20_s": dv, "rpos_s": rp, "vwap_ratio": round(ratio, 4) if ratio else None,
                    "flag_bottom": bool(rp is not None and rp <= 0.35), "flag_dv5m": bool(dv is not None and dv >= 5e6)})
        reasons = []
        if close is None or dv is None:
            reasons.append("no_price")
        else:
            if close < 3:
                reasons.append("price<3")
            if dv < 1e6:
                reasons.append("dv20<1M")
            if not (ratio is not None and 0.67 <= ratio <= 1.5):
                reasons.append("vwap_mismatch")
        row["tradable"] = not reasons
        row["reject_reasons"] = reasons
        row["dedupe60_drop"] = False
        if row["tradable"]:
            prev = kept_t0.get(e.ticker)
            if prev is not None and _sessions_between(prev, t0) < DEDUPE_SESS:
                row["dedupe60_drop"] = True
            else:
                kept_t0[e.ticker] = t0
        rows.append(row)
    return rows, pending


def _sessions_between(a: date, b: date) -> int:
    n, d = 0, a
    while d < b:
        d = next_session(d)
        n += 1
    return n


def cmd_signals(since: Optional[str], dry: bool, px_fetch=None) -> int:
    px_fetch = px_fetch or fetch_prices
    ha, ho = load_history()
    ca, co, days = load_collected()
    if len(ca) == 0:
        log("수집 DB 비어 있음 — us_form4_daily.py 먼저")
        return 1
    A, O = combine([(ha, ho), (ca, co)])
    gm = build_groups(O)
    hist_max = pd.to_datetime(ha.fdate).max() if len(ha) else None
    covered = coverage(days, hist_max)
    since_ts = pd.Timestamp(since) if since else pd.Timestamp(min(days) if days else ca.fdate.min())
    ledger = read_jsonl(LEDGER)
    have = {r["key"] for r in ledger}
    detected = now_et()
    all_rows, all_pending = [], []
    evs = {}
    for rule, do_only in RULES.items():
        ev = find_events(A, O, gm, do_only, since=since_ts)
        if len(ev):
            ev = ev[[f"{rule}|{t}|{d.date().isoformat()}" not in have for t, d in zip(ev.ticker, ev.date)]]
        evs[rule] = ev
    tick = sorted({t for ev in evs.values() for t in ev.ticker})
    M = None
    if tick:
        start = (since_ts - pd.Timedelta(days=420)).strftime("%Y-%m-%d")
        end = (pd.Timestamp(date.today()) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        px = px_fetch(tick + ["IWM"], start, end)
        M = matrices(px) if len(px) else None
    for rule, ev in evs.items():
        rows, pending = build_rows(ev, rule, M, covered, ledger + all_rows, detected)
        all_rows += rows
        all_pending += pending
    log(f"기간 {since_ts.date()}~ · 이력 accession {len(A)} · 그룹 {len(set(gm.values()))} · 신규 신호 {len(all_rows)} "
        f"(tradable {sum(r['tradable'] and not r['dedupe60_drop'] for r in all_rows)}) · 가격 보류 {len(all_pending)}")
    for r in all_rows:
        log(f"{'DRY ' if dry else ''}{r['rule']:10s} {r['ticker']:6s} F={r['filing_date']} grp={r['n_groups']} "
            f"t0={r['t0_filing']}/{r['t0_accept']} close={_f(r['close_s'])} dv20={_f(r['dv20_s'], 1e6)}M "
            f"rpos={_f(r['rpos_s'])} vwap/c={r['vwap_ratio']} tradable={r['tradable']}{' DEDUPE' if r['dedupe60_drop'] else ''} "
            f"{','.join(r['reject_reasons'])} bottom={r['flag_bottom']} $5M={r['flag_dv5m']} "
            f"win_ok={r['window_complete']} supp_ok={r['suppress_history_complete']} actionable={r['actionable']}")
    if not dry and all_rows:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as fh:
            for r in all_rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return 0


def _f(x, div: float = 1.0) -> str:
    return "NA" if x is None else f"{x / div:.2f}"


# ------------------------------------------------------------------ 정산
def universe_tickers() -> List[str]:
    if UNIVERSE_JSON.exists():
        return json.loads(UNIVERSE_JSON.read_text())
    s = set()
    if T3_TICKERS.exists():
        s |= set(json.loads(T3_TICKERS.read_text()))
    for f in sorted(K1_PX.glob("*.csv.gz")) if K1_PX.exists() else []:
        s |= set(pd.read_csv(f, usecols=["ticker"]).ticker.astype(str))
    out = sorted(t for t in s if isinstance(t, str) and t)
    if out:
        UNIVERSE_JSON.parent.mkdir(parents=True, exist_ok=True)
        UNIVERSE_JSON.write_text(json.dumps(out))
    return out


def settle_rows(rows: List[Dict], M: Dict, settled_keys: set) -> List[Dict]:
    cal = list(M["cal"])
    tix = M["tix"]
    iwm = tix.get("IWM")
    out = []
    for r in rows:
        j = tix.get(r["ticker"])
        for entry in ("filing", "accept"):
            t0s = r.get(f"t0_{entry}")
            if not t0s:
                continue
            for H in HS:
                k = f"{r['key']}|H{H}|{entry}"
                if k in settled_keys:
                    continue
                if t0s not in cal:
                    # 세션 달력 불일치(임시 휴장 등) → 다음 실제 세션으로 스냅
                    nxt = [c for c in cal if c > t0s]
                    if not nxt:
                        continue
                    t0s = nxt[0]
                t0 = cal.index(t0s)
                ex = t0 + H - 1
                if ex >= len(cal) or j is None or iwm is None:
                    continue
                s = t0 - 1
                ao, ac = M["aopen"][t0, j], M["aclose"][ex, j]
                ret = ac / ao - 1 if np.isfinite(ao) and np.isfinite(ac) else np.nan
                ri = M["aclose"][ex, iwm] / M["aopen"][t0, iwm] - 1
                sz, nb = _size_bench(M, s, t0, ex, M["dv20"][s, j])
                rec = {"skey": k, "key": r["key"], "rule": r["rule"], "ticker": r["ticker"], "H": H, "entry": entry,
                       "t0": str(cal[t0]), "exit": str(cal[ex]), "ret": _nz(ret), "iwm": _nz(ri), "sz": _nz(sz), "sz_n": nb,
                       "x_iwm": _nz(ret - COST - ri), "x_sz": _nz(ret - COST - sz),
                       "flag_bottom": r.get("flag_bottom"), "flag_dv5m": r.get("flag_dv5m"),
                       "actionable": r.get("actionable"), "window_complete": r.get("window_complete"),
                       "suppress_history_complete": r.get("suppress_history_complete"),
                       "settled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                out.append(rec)
    return out


def _nz(x):
    return float(x) if x is not None and np.isfinite(x) else None


def _size_bench(M: Dict, s: int, t0: int, ex: int, dv_evt) -> Tuple[float, int]:
    """research size_bench: s 트레이더블(≥$3·≥$1M, ≥100) dv20 10분위, 같은 분위 평균(≥5)."""
    if s < 0 or not np.isfinite(dv_evt):
        return np.nan, 0
    dv = M["dv20"][s]
    ok = (M["close"][s] >= 3) & (dv >= 1e6) & np.isfinite(dv)
    if ok.sum() < 100:
        return np.nan, int(ok.sum())
    e = np.nanpercentile(dv[ok], np.arange(10, 100, 10))
    R = M["aclose"][ex] / M["aopen"][t0] - 1
    dec = np.searchsorted(e, dv[ok])
    rr = R[ok]
    k = int(np.searchsorted(e, dv_evt))
    mk = np.isfinite(rr) & (dec == k)
    if mk.sum() < 5:
        return np.nan, int(mk.sum())
    return float(rr[mk].mean()), int(mk.sum())


def cmd_settle(dry: bool, px_fetch=None) -> int:
    px_fetch = px_fetch or fetch_prices
    rows = [r for r in read_jsonl(LEDGER) if r.get("tradable") and not r.get("dedupe60_drop")]
    old = {x["skey"]: x for x in read_jsonl(SETTLE)}
    today = date.today()
    due = []
    for r in rows:
        for entry in ("filing", "accept"):
            t0s = r.get(f"t0_{entry}")
            if not t0s:
                continue
            for H in HS:
                if f"{r['key']}|H{H}|{entry}" in old:
                    continue
                # 대략 H세션 경과(달력 ≈ H×1.45)면 후보 — 실제 가능 여부는 가격 달력이 정한다
                if (today - date.fromisoformat(t0s)).days >= int(H * 1.4):
                    due.append(r)
    if not due:
        log(f"정산 대상 없음 (원장 tradable {len(rows)} · 정산 {len(old)})")
        return 0
    s_min = min(date.fromisoformat(r["s_date"]) for r in due) - timedelta(days=45)
    tick = sorted(set(universe_tickers()) | {r["ticker"] for r in due} | {"IWM"})
    PX_DIR.mkdir(parents=True, exist_ok=True)
    cache = PX_DIR / f"settle_{s_min.isoformat()}_{today.isoformat()}.csv.gz"
    if cache.exists():
        px = pd.read_csv(cache)
    else:
        log(f"가격 수집 {len(tick)}종목 {s_min}~{today}")
        px = px_fetch(tick, s_min.isoformat(), (today + timedelta(days=1)).isoformat())
        if not dry:
            px.to_csv(cache, index=False, compression="gzip")
    M = matrices(px)
    new = settle_rows({r["key"]: r for r in due}.values(), M, set(old))
    log(f"신규 정산 {len(new)}건")
    for x in new:
        log(f"{x['rule']:10s} {x['ticker']:6s} H{x['H']} {x['entry']:6s} {x['t0']}→{x['exit']} ret={_pct(x['ret'])} "
            f"xIWM={_pct(x['x_iwm'])} xSZ={_pct(x['x_sz'])}")
    if not dry and new:
        old.update({x["skey"]: x for x in new})
        tmp = SETTLE.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in old.values()), encoding="utf-8")
        tmp.replace(SETTLE)
    return 0


def _pct(x) -> str:
    return "NA" if x is None else f"{x * 100:+.2f}%"


# ------------------------------------------------------------------ 판정
def cohort_stats(recs: List[Dict], col: str) -> Dict:
    d = pd.DataFrame([r for r in recs if r.get(col) is not None])
    if len(d) == 0:
        return {"n": 0, "dates": 0}
    coh = d.groupby("t0")[col].mean()
    n = len(coh)
    t = float(coh.mean() / (coh.std(ddof=1) / np.sqrt(n))) if n > 2 and coh.std(ddof=1) > 0 else None
    return {"n": int(len(d)), "dates": int(n), "mean_coh": float(coh.mean()), "cl_t": t,
            "median_evt": float(d[col].median()), "win": float((d[col] > 0).mean())}


def verdict(st: Dict) -> str:
    if not st.get("n"):
        return "표본 없음"
    if st["mean_coh"] > 0 and (st.get("cl_t") or 0) >= 2:
        return "합격(사전등록선 충족)"
    return "미달"


def cmd_report(json_path: Optional[str]) -> int:
    S = read_jsonl(SETTLE)
    L = read_jsonl(LEDGER)
    out = {"ledger_rows": len(L), "tradable": sum(1 for r in L if r.get("tradable") and not r.get("dedupe60_drop")),
           "settled": len(S), "cells": {}}
    subsets = {"all": lambda x: True, "bottom": lambda x: x.get("flag_bottom"), "dv5m": lambda x: x.get("flag_dv5m"),
               "actionable": lambda x: x.get("actionable"),
               # 30일 창·90일 억제 이력이 모두 수집 범위 안인 신호만(초기 수집 공백 인공물 제외)
               "history_ok": lambda x: bool(x.get("window_complete") and x.get("suppress_history_complete"))}
    for rule in RULES:
        for H in HS:
            for entry in ("filing", "accept"):
                for sub, fn in subsets.items():
                    recs = [x for x in S if x["rule"] == rule and x["H"] == H and x["entry"] == entry and fn(x)]
                    for b in ("iwm", "sz"):
                        st = cohort_stats(recs, f"x_{b}")
                        out["cells"][f"{rule}|H{H}|{entry}|{sub}|{b}"] = st
    prim = out["cells"].get(f"{PRIMARY[0]}|H{PRIMARY[1]}|{PRIMARY[2]}|all|{PRIMARY[3]}", {"n": 0})
    out["primary"] = {"cell": "U5r_all · H60 · t0_filing · vs IWM", "stats": prim, "verdict": verdict(prim)}
    do = out["cells"].get("U5r_do_all|H60|filing|all|iwm", {"n": 0})
    out["do_variant"] = {"stats": do, "verdict": verdict(do)}
    ph = out["cells"].get("U5r_all|H60|filing|history_ok|iwm", {"n": 0})
    out["primary_history_ok"] = {"stats": ph, "verdict": verdict(ph)}
    log(f"원장 {out['ledger_rows']}행 · tradable {out['tradable']} · 정산 {out['settled']}")
    for k, st in out["cells"].items():
        if st.get("n") and ("|all|" in k or "|history_ok|" in k):
            log(f"{k:36s} n={st['n']} d={st['dates']} mean={st['mean_coh'] * 100:+.2f}% "
                f"t={st['cl_t'] if st['cl_t'] is None else round(st['cl_t'], 2)} med={st['median_evt'] * 100:+.2f}% win={st['win']:.2f}")
    log(f"1차 판정 {out['primary']['cell']}: {out['primary']['verdict']} · 이력완전분만: "
        f"{out['primary_history_ok']['verdict']} · 이사·임원 변형: {out['do_variant']['verdict']}")
    if json_path:
        Path(json_path).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["signals", "settle", "report", "import-history"])
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--since")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    if a.cmd == "import-history":
        ok = import_t3_history()
        log(f"T3 이력 복사 {'완료' if ok else '실패(캐시 없음)'}")
        return 0 if ok else 1
    if a.cmd == "signals":
        return cmd_signals(a.since, a.dry)
    if a.cmd == "settle":
        return cmd_settle(a.dry)
    return cmd_report(a.json)


if __name__ == "__main__":
    sys.exit(main())
