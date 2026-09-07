#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""탐색 풀 생성기 — 가격 CSV만으로 US·KR 후보 풀을 넓게 만든다 (2026-09-07, 쉐도우 탐색 체계 개편 D1).

운영자 방향(09-07): 지금은 실매수가 아니므로 하루 1건 한도를 두지 말고 조건에 맞는 상황을 최대한 많이·다양하게 DB에 쌓는다.
이 모듈은 virtual_books의 탐색 arm(x_*)에 세션별 후보를 공급한다. 스크리너 DB(us_swing_shadow.db)나 kr_fallen 원장에 의존하지 않고
data/price/{us,kr} 일봉 전체(US 1,673·KR 1,744 종목)에서 특성값·풀 통과·순위·국면 태그를 계산한다.

규약
- US: 신호봉 i(전일) → 진입 세션 = 다음 봉 날짜(세션 키). virtual_books.entry_of(market="US")가 그 날짜 시가로 진입한다.
- KR: 신호일 = 세션 키, 진입은 다음 거래 세션 시가(entry_of(market="KR") 규약과 동일).
- 특성값은 전부 신호봉까지의 데이터(no-lookahead). 국면 태그(지수 20일·MA20·breadth)도 신호일 기준.
- 풀 문턱은 넓게 잡고 문턱 자체는 특성값으로 남긴다 → "어느 문턱부터 엣지가 살아나는가"는 사후 분해로 본다.

출력: discovery_sessions(market) → {pool_id: {session_key: [cand, ...]}}, pool_stats(market) → {session_key: {pool_id: n, "_universe": n}}
cand = {ticker, chg, gap, dvol, dvol20, vol_spike, ibs, rv20, mom20, from_high20, ma20_disc, cum5, ret60, max21, hi_break_n,
        down_streak, price, ranks{rule: rank}, regime{...}, pool}
사용: python tools/discovery_pools.py [--market US|KR] [--date YYYY-MM-DD]   # 풀 규모·샘플 출력(read-only)
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
US_DIR = ROOT / "data" / "price" / "us"
KR_DIR = ROOT / "data" / "price" / "kr"
KR_INDEX = ROOT / "state" / "kr_index_history_kospi.json"
DISCOVERY_START = "2025-06-02"   # KR 캐시 2025-04-22 시작 + 특성 창(20~60봉)
MIN_HISTORY = 25                 # 특성 계산 최소 봉 수

# 풀 정의 — (id, 시장, 설명). 문턱은 아래 predicate. 넓게 잡는다(실운영 문턱은 특성값 필터로 복원).
US_DVOL_MIN_M = 50.0     # 백만 달러
KR_DVOL_MIN_EOK = 20.0   # 억 원
POOLS_US = {
    "xus_fallen3":  "전일 ≤−3% (거래대금 ≥50M) — 급락 풀 확장(실운영 −5%·밴드·MAX는 특성 필터로 복원)",
    "xus_slow8":    "5일 누적 ≤−8% & 단일 −5% 없음 — 느린 급락 확장",
    "xus_rise5":    "전일 ≥+5% — 급등 다음날(지속/되돌림) 관측",
    "xus_breakout": "종가가 직전 120~250봉 최고 종가 돌파 — 신고가 추세",
    "xus_volspike": "거래량 20일 평균의 3배↑ & |전일| <3% — 조용한 거래량 급증",
    # 2026-09-07 신규 전략(이벤트 풀 — 원장 결합, 기존 풀과 메커니즘이 다름)
    "xus_earn_gap": "어닝 반응일(yfinance 발표일·확정분) 갭 ≥+8% & 종가 ≥ 시가 — P7 가격반응 PEAD(추정치 불필요)",
    "xus_insider":  "Form 4 공개시장 매수 7일 내 2인↑ 군집(SEC 분기 데이터셋) — P8 내부자",
    "xus_volfirst": "volspike & 60봉 최대 거래량 & 직전 20봉 3배 사건 없음 — Codex N1 최초 거래량 충격",
}
POOLS_KR = {
    "xkr_fallen3":  "전일 ≤−3% (거래대금 ≥20억) — 급락 풀 확장(8조건은 플래그, R2/R4는 특성 필터로 복원)",
    "xkr_rise5":    "전일 ≥+5% — 급등 다음날 관측(상한가 복권·회피 판단 데이터)",
    "xkr_breakout": "종가가 직전 120봉 최고 종가 돌파 — 신고가 추세",
    "xkr_volspike": "거래량 20일 평균의 3배↑ & |전일| <3%",
    # 2026-09-07 신규 전략(DART 원장 결합)
    "xkr_insider":       "임원·주요주주 소유보고 증가(+) 7일 내 2인↑ 군집(elestock) — P4 내부자 매수",
    "xkr_plan_buy":      "거래계획 사전공시(매수) 0~3일 후 — P5 예고된 수요",
    "xkr_exright":       "무상증자 신주배정기준일 −2거래일(다음 시가 = 권리락일 시가) — P6 권리락 착시",
    "xkr_buyback_start": "자사주 장내취득 예정 시작일 −1거래일(다음 시가 = 시작일 시가) — Codex N4 취득 개시 수요",
}
RANK_RULES = ("dvol_desc", "dvol_asc", "chg_hi", "chg_lo", "ibs_hi", "max_lo", "disc_deep", "cum5_deep", "ret60_desc", "volspike_desc")

_CACHE: dict[str, tuple[dict, dict]] = {}
_NEARMISS: dict[str, dict[str, list]] = {}   # market → {signal_date: [[ticker, chg, dvol, vol_spike, cum5, hi_break_n], ...]}
NEARMISS_BAND = {"chg_abs": 2.0, "vol_spike": 2.0, "cum5": -5.0}   # 풀 문턱 바로 밖(탈락) 후보만 남긴다 — "문턱을 더 낮췄다면"의 복원용


def bar_complete(bar_date: str, market: str, now=None) -> bool:
    """virtual_books.bar_complete와 같은 규약: KR은 당일 16:00 KST 이후, US는 다음날 06:00 KST 이후."""
    from datetime import datetime, timedelta, timezone
    now = now or (datetime.now(timezone.utc) + timedelta(hours=9)).replace(tzinfo=None)
    today = now.strftime("%Y-%m-%d")
    if market == "KR":
        return bar_date < today or (bar_date == today and now.hour >= 16)
    nxt = (datetime.strptime(bar_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return nxt < today or (nxt == today and now.hour >= 6)


def _load_bars(path: Path) -> list[tuple]:
    rows = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.reader(fh):
                if len(r) >= 6 and r[0][:2] == "20":
                    try:
                        rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                    except ValueError:
                        continue
    except OSError:
        return []
    return sorted(rows)


def featurize(b: list[tuple], i: int, market: str, vol_ctx: tuple | None = None) -> dict | None:
    """신호봉 i의 특성값. 데이터 부족이면 None. vol_ctx=(spike_flags, vols) — 종목당 1회 계산한 롤링 3배 플래그(N1용, 선택)."""
    if i < MIN_HISTORY or i >= len(b):
        return None
    d, o, h, l, c, v = b[i]
    pc = b[i - 1][4]
    if pc <= 0 or c <= 0 or o <= 0:
        return None
    closes = [x[4] for x in b[max(0, i - 60): i + 1]]
    vols = [x[5] for x in b[i - 20: i]]
    rets = [100.0 * (b[j][4] / b[j - 1][4] - 1.0) for j in range(i - 19, i + 1) if b[j - 1][4] > 0]
    ma20 = st.mean(closes[-20:])
    dv = c * v / (1e6 if market == "US" else 1e8)
    dv20 = st.mean(x[4] * x[5] for x in b[i - 19: i + 1]) / (1e6 if market == "US" else 1e8)
    hi20 = max(x[2] for x in b[i - 19: i + 1])
    n_break = min(120 if market == "KR" else 250, i)   # KR은 120봉(캐시 16개월), US는 최대 250봉
    prior_max = max(x[4] for x in b[i - n_break: i]) if n_break >= 120 else None
    streak = 0
    for j in range(i, 0, -1):
        if b[j][4] < b[j - 1][4]:
            streak += 1
        else:
            break
    single5 = min(100.0 * (b[j][4] / b[j - 1][4] - 1.0) for j in range(max(1, i - 4), i + 1))
    f = {
        "ticker": None, "date": d, "price": c,
        "chg": 100.0 * (c / pc - 1.0), "gap": 100.0 * (o / pc - 1.0),
        "dvol": dv, "dvol20": dv20, "vol_spike": (v / st.mean(vols)) if vols and st.mean(vols) > 0 else None,
        "ibs": (c - l) / (h - l) * 100.0 if h > l else None,
        "rv20": st.pstdev(rets) if len(rets) >= 10 else None,
        "mom20": 100.0 * (c / closes[-21] - 1.0) if len(closes) >= 21 else None,
        "from_high20": 100.0 * (c / hi20 - 1.0),
        "ma20_disc": 100.0 * (c / ma20 - 1.0),
        "cum5": 100.0 * (c / b[i - 5][4] - 1.0) if i >= 5 and b[i - 5][4] > 0 else None,
        "min1_in5": single5,
        "ret60": 100.0 * (c / closes[0] - 1.0) if len(closes) >= 61 else None,
        "max21": max(100.0 * (b[j][4] / b[j - 1][4] - 1.0) for j in range(i - 20, i + 1)) if i >= 21 else None,
        "hi_break_n": n_break if (prior_max is not None and c >= prior_max) else 0,
        "down_streak": streak,
        "close_pos_up": c >= o,
    }
    if vol_ctx is not None:
        flags, vols_all = vol_ctx
        f["vol_max60"] = bool(i >= 60 and v > max(vols_all[i - 60: i]))
        f["spike_prior20"] = sum(flags[i - 20: i]) if i >= 20 else None
    return f


def volume_context(b: list[tuple]) -> tuple[list[int], list[float]]:
    """종목당 1회: 각 봉의 '직전 20봉 평균 대비 3배' 플래그(O(n))와 거래량 배열 — N1 최초 거래량 사건용."""
    vols = [x[5] for x in b]
    flags = [0] * len(b)
    if len(b) <= 20:
        return flags, vols
    run = sum(vols[:20])
    for j in range(20, len(b)):
        mean = run / 20.0
        flags[j] = 1 if (mean > 0 and vols[j] >= 3.0 * mean) else 0
        run += vols[j] - vols[j - 20]
    return flags, vols


def pool_pass(f: dict, market: str) -> list[str]:
    out = []
    if market == "US":
        if f["dvol"] is None or f["dvol"] < US_DVOL_MIN_M:
            return out
        if f["chg"] <= -3.0:
            out.append("xus_fallen3")
        if f["cum5"] is not None and f["cum5"] <= -8.0 and f["min1_in5"] > -5.0:
            out.append("xus_slow8")
        if f["chg"] >= 5.0:
            out.append("xus_rise5")
        if f["hi_break_n"] >= 120:
            out.append("xus_breakout")
        if f["vol_spike"] is not None and f["vol_spike"] >= 3.0 and abs(f["chg"]) < 3.0:
            out.append("xus_volspike")
    else:
        if f["dvol"] is None or f["dvol"] < KR_DVOL_MIN_EOK or f["price"] < 1000:
            return out
        if f["chg"] <= -3.0:
            out.append("xkr_fallen3")
        if f["chg"] >= 5.0:
            out.append("xkr_rise5")
        if f["hi_break_n"] >= 120:
            out.append("xkr_breakout")
        if f["vol_spike"] is not None and f["vol_spike"] >= 3.0 and abs(f["chg"]) < 3.0:
            out.append("xkr_volspike")
    return out


# ── 이벤트 원장(2026-09-07 신규 전략) — 신호일 기준 no-lookahead: 공시일/보고일/발표일 ≤ 신호일만 본다 ──────────────
SHADOW_DIR = ROOT / "data" / "shadow"
DART_EVENTS_12M = ROOT / "data" / "analysis" / "dart_events_12m.jsonl"      # 09-03 재생(2025-09-01~)
KR_EVENT_SIGNALS = SHADOW_DIR / "kr_event_signals.jsonl"                   # 실시간 레인 forward
KR_TERMS = SHADOW_DIR / "kr_dart_terms.jsonl"                               # 자사주 기간·무상증자 기준일
KR_INSIDER = SHADOW_DIR / "kr_insider_ledger.jsonl"                         # elestock 소유보고
KR_PLANS = SHADOW_DIR / "kr_insider_plan_ledger.jsonl"                      # 거래계획 사전공시
US_EARN = ROOT / "data" / "analysis" / "us_earnings_dates.jsonl"           # yfinance 발표일
US_INSIDER = SHADOW_DIR / "us_insider_ledger.jsonl"                         # SEC Form 4 'P'
DART_BACKFILL_START = "2025-09-08"   # dart_events_12m 시작(2025-09-01)+창 → 그 전 세션은 "공시 없음"이 아니라 "모름"(dart_ok=False)
_EVT: dict[str, object] = {}
_KIND_MAP = {"자기주식취득결정": "buyback", "최대주주변경": "major_holder_change", "유상증자결정(대조)": "rights_offering",
             "공급계약체결": "supply_contract", "무상증자결정": "bonus_issue",
             "buyback": "buyback", "major_holder_change": "major_holder_change", "rights_offering": "rights_offering",
             "supply_contract": "supply_contract", "bonus_issue": "bonus_issue"}


def _jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _dt(s) -> _date | None:
    """'YYYY-MM-DD' 또는 'YYYYMMDD'(DART rcept_dt) → date. Python 3.9는 fromisoformat이 기본형(YYYYMMDD)을 못 읽는다."""
    t = str(s or "").strip()
    try:
        if len(t) == 8 and t.isdigit():
            return _date(int(t[:4]), int(t[4:6]), int(t[6:8]))
        return _date.fromisoformat(t[:10])
    except (TypeError, ValueError):
        return None


def _load_events() -> dict:
    """한 번만 로드. 원장이 없으면 빈 dict + ok=False(호출자는 결측을 '불통과'로 닫는다)."""
    if _EVT:
        return _EVT
    kr: dict[str, list[tuple]] = defaultdict(list)
    for r in _jsonl(DART_EVENTS_12M):
        k = _KIND_MAP.get(str(r.get("kind")))
        d = str(r.get("date", ""))
        if k and len(d) == 8 and "정정" not in str(r.get("report", "")):
            kr[str(r["stock"])].append((_date(int(d[:4]), int(d[4:6]), int(d[6:])), k))
    for r in _jsonl(KR_EVENT_SIGNALS):
        k = _KIND_MAP.get(str(r.get("kind")))
        d = _dt(r.get("session_date"))
        if k and d and not r.get("is_correction") and r.get("stock_code"):
            kr[str(r["stock_code"])].append((d, k))
    terms: dict[str, list[dict]] = defaultdict(list)
    for r in _jsonl(KR_TERMS):
        terms[str(r.get("stock"))].append(r)
    ins: dict[str, list[tuple]] = defaultdict(list)
    for r in _jsonl(KR_INSIDER):
        d = _dt(r.get("rcept_dt"))
        if d and r.get("irds_cnt") is not None:
            ins[str(r["stock"])].append((d, str(r.get("repror")), float(r["irds_cnt"])))
    plans: dict[str, list[dict]] = defaultdict(list)
    for r in _jsonl(KR_PLANS):
        plans[str(r.get("stock"))].append(r)
    earn: dict[str, list[tuple]] = defaultdict(list)
    for r in _jsonl(US_EARN):
        d = _dt(r.get("date"))
        if d and r.get("confirmed"):
            earn[str(r["ticker"]).upper()].append((d, str(r.get("hour"))))
    usi: dict[str, list[tuple]] = defaultdict(list)
    for r in _jsonl(US_INSIDER):
        d = _dt(r.get("filing_date"))
        if d:
            usi[str(r["ticker"]).upper()].append((d, str(r.get("owner")), float(r.get("value") or 0)))
    _EVT.update({"kr": kr, "terms": terms, "ins": ins, "plans": plans, "earn": earn, "usi": usi,
                 "ok": {"dart": DART_EVENTS_12M.exists(), "terms": KR_TERMS.exists(), "ins": KR_INSIDER.exists(),
                        "plans": KR_PLANS.exists(), "earn": US_EARN.exists(), "usi": US_INSIDER.exists()}})
    return _EVT


def _days_since(events: list[tuple], sig: _date, kind: str | None = None) -> int | None:
    """가장 최근(신호일 이전) 공시까지의 일수. 없으면 None."""
    best = None
    for e in events:
        if kind is not None and e[1] != kind:
            continue
        if e[0] <= sig:
            n = (sig - e[0]).days
            best = n if best is None or n < best else best
    return best


def _cluster(events: list[tuple], sig: _date, days: int = 7, positive: bool = True) -> tuple[int, float]:
    """(신호일−days ~ 신호일) 보고 중 (positive면 증가분만) 서로 다른 보고자 수·합계."""
    who = set(); tot = 0.0
    for d, name, val in events:
        gap = (sig - d).days
        if gap < 0 or gap > days:
            continue
        if positive and val <= 0:
            continue
        who.add(name); tot += val
    return len(who), tot


def event_features(t: str, sig_date: str, market: str, dates: list[str], i: int) -> dict:
    """원장 결합 특성(신호일 기준). dates[i] = 신호봉. 이벤트 풀 통과 여부는 event_pool_pass가 판단."""
    ev = _load_events(); ok = ev["ok"]; sig = _date.fromisoformat(sig_date); f: dict = {}   # type: ignore[index]
    nxt = _date.fromisoformat(dates[i + 1]) if i + 1 < len(dates) else None
    if market == "KR":
        kr = ev["kr"].get(t, [])   # type: ignore[index]
        f["dart_ok"] = bool(ok["dart"]) and sig_date >= DART_BACKFILL_START
        for k in ("buyback", "major_holder_change", "rights_offering", "supply_contract", "bonus_issue"):
            f[f"{k}_days"] = _days_since(kr, sig, k) if f["dart_ok"] else None
        f["buyback_active"] = (False if (ok["terms"] and f["dart_ok"]) else None); f["buyback_start_next"] = False
        f["exright_next"] = False
        for r in ev["terms"].get(t, []):   # type: ignore[index]
            dd = _dt(r.get("date"))
            if not dd or dd > sig:
                continue   # 공시 전 — 모른다
            if r.get("kind") == "buyback" and r.get("method") == "market":
                s0, s1 = _dt(r.get("start")), _dt(r.get("end"))
                if s0 and s1 and s0 <= sig <= s1:
                    f["buyback_active"] = True
                if s0 and ((nxt is not None and nxt == s0) or (nxt is None and 1 <= (s0 - sig).days <= 4)):
                    f["buyback_start_next"] = True   # 다음 봉 = 시작일(오늘 신호면 달력 근사)
            if r.get("kind") == "bonus_issue":
                R = _dt(r.get("record_date"))
                if not R or R <= sig:
                    continue
                if nxt is not None:
                    nn = _date.fromisoformat(dates[i + 2]) if i + 2 < len(dates) else None
                    if nxt < R and (nn is None or nn >= R):
                        f["exright_next"] = True   # 다음 봉이 기준일 직전 거래일 = 권리락일
                elif 1 <= (R - sig).days <= 4:
                    f["exright_next"] = True
        n, tot = _cluster(ev["ins"].get(t, []), sig, 7, True)   # type: ignore[index]
        f["insider_buy_n7"] = n if ok["ins"] else None; f["insider_buy_qty7"] = tot
        f["plan_buy_days"] = None
        if ok["plans"]:
            best = None
            for r in ev["plans"].get(t, []):   # type: ignore[index]
                d = _dt(r.get("rcept_dt"))
                if r.get("side") == "buy" and d and d <= sig:
                    g = (sig - d).days; best = g if best is None or g < best else best
            f["plan_buy_days"] = best
    else:
        f["earn_ok"] = bool(ok["earn"]); f["earn_react"] = False; f["earn_hour"] = None
        prev = _date.fromisoformat(dates[i - 1]) if i >= 1 else None
        for d, hour in ev["earn"].get(t.upper(), []):   # type: ignore[index]
            # BMO: 발표일 당일이 반응봉 / AMC·DMH: 다음 거래일이 반응봉(발표일이 직전 봉 이상 ~ 신호봉 미만)
            if (hour == "BMO" and d == sig) or (hour != "BMO" and prev is not None and prev <= d < sig):
                f["earn_react"] = True; f["earn_hour"] = hour
        n, tot = _cluster(ev["usi"].get(t.upper(), []), sig, 7, False)   # type: ignore[index]
        f["insider_buy_n7"] = n if ok["usi"] else None; f["insider_buy_usd7"] = tot
    return f


def event_pool_pass(f: dict, market: str) -> list[str]:
    out = []
    if market == "US":
        if f.get("earn_react") and f.get("gap") is not None and f["gap"] >= 8.0 and f.get("close_pos_up"):
            out.append("xus_earn_gap")
        if (f.get("insider_buy_n7") or 0) >= 2:
            out.append("xus_insider")
        if (f.get("vol_spike") is not None and f["vol_spike"] >= 3.0 and abs(f["chg"]) < 3.0
                and f.get("vol_max60") and f.get("spike_prior20") == 0):
            out.append("xus_volfirst")
    else:
        if (f.get("insider_buy_n7") or 0) >= 2:
            out.append("xkr_insider")
        if f.get("plan_buy_days") is not None and f["plan_buy_days"] <= 3:
            out.append("xkr_plan_buy")
        if f.get("exright_next"):
            out.append("xkr_exright")
        if f.get("buyback_start_next"):
            out.append("xkr_buyback_start")
    return out

def _rank_key(rule: str, c: dict) -> float:
    g = lambda k, dflt: c[k] if c.get(k) is not None else dflt
    return {
        "dvol_desc": -g("dvol", 0.0), "dvol_asc": g("dvol", 1e18), "chg_hi": -g("chg", -1e9), "chg_lo": g("chg", 1e9),
        "ibs_hi": -g("ibs", -1.0), "max_lo": g("max21", 1e18), "disc_deep": g("ma20_disc", 1e9), "cum5_deep": g("cum5", 1e9),
        "ret60_desc": -g("ret60", -1e9), "volspike_desc": -g("vol_spike", -1.0),
    }[rule]


def _index_regime(market: str) -> dict[str, dict]:
    """날짜 → {idx_ret20, idx_above_ma20}. US=SPY CSV, KR=kr_index_history_kospi.json(종가)."""
    closes: list[tuple[str, float]] = []
    if market == "US":
        closes = [(x[0], x[4]) for x in _load_bars(US_DIR / "us_SPY.csv")]
    else:
        try:
            k = json.loads(KR_INDEX.read_text(encoding="utf-8"))
            rows = k if isinstance(k, list) else (k.get("rows") or k.get("history") or list(k.values())[0])
            closes = sorted((str(r["date"]), float(r["close"])) for r in rows if r.get("date") and r.get("close"))
        except Exception:
            closes = []
    out = {}
    for i in range(20, len(closes)):
        d, c = closes[i]
        ma = st.mean(x[1] for x in closes[i - 19: i + 1])
        out[d] = {"idx_ret20": round(100.0 * (c / closes[i - 20][1] - 1.0), 2), "idx_above_ma20": bool(c >= ma)}
    return out


def build(market: str, *, start: str | None = None) -> tuple[dict, dict]:
    """전 종목 스캔 → (sessions, stats). sessions[pool][session_key] = [cand...]."""
    market = market.upper()
    start = start or DISCOVERY_START
    d = US_DIR if market == "US" else KR_DIR
    prefix = "us_" if market == "US" else "kr_"
    pools = POOLS_US if market == "US" else POOLS_KR
    per_day: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    breadth: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # date → [n_total, n_down]
    for p in sorted(d.glob(f"{prefix}*.csv")):
        t = p.stem[len(prefix):]
        b = _load_bars(p)
        if len(b) < MIN_HISTORY + 1:
            continue
        vctx = volume_context(b)
        bdates = [x[0] for x in b]
        for i in range(MIN_HISTORY, len(b)):
            sig_date = b[i][0]
            if sig_date < start or not bar_complete(sig_date, market):
                continue   # 미완성 봉(당일 장중 KR·미마감 US)은 신호로 쓰지 않는다 — 풀 통계·탈락 후보에도 섞이지 않게
            f = featurize(b, i, market, vctx)
            if f is None:
                continue
            f["ticker"] = t
            breadth[sig_date][0] += 1
            if f["chg"] < 0:
                breadth[sig_date][1] += 1
            hits = pool_pass(f, market)
            if f["dvol"] is not None and f["dvol"] >= (US_DVOL_MIN_M if market == "US" else KR_DVOL_MIN_EOK) and (market == "US" or f["price"] >= 1000):
                f.update(event_features(t, sig_date, market, bdates, i))   # 원장 결합(유동성 문턱 통과 종목만)
                hits = hits + event_pool_pass(f, market)
            if not hits:
                if (abs(f["chg"]) >= NEARMISS_BAND["chg_abs"] or (f["vol_spike"] or 0) >= NEARMISS_BAND["vol_spike"]
                        or (f["cum5"] is not None and f["cum5"] <= NEARMISS_BAND["cum5"])):
                    _NEARMISS.setdefault(market, {}).setdefault(sig_date, []).append(
                        [t, round(f["chg"], 2), round(f["dvol"], 1), round(f["vol_spike"], 2) if f["vol_spike"] is not None else None,
                         round(f["cum5"], 2) if f["cum5"] is not None else None, f["hi_break_n"]])
                continue
            key = b[i + 1][0] if market == "US" and i + 1 < len(b) else (sig_date if market == "KR" else None)
            if key is None:
                continue  # US: 다음 봉이 아직 없음(오늘 신호) — 다음 실행에서 잡힌다
            f["signal_date"] = sig_date
            for pid in hits:
                per_day[pid][key].append(dict(f))
    regime = _index_regime(market)
    for pid, days in per_day.items():
        for key, cands in days.items():
            for rule in RANK_RULES:
                order = sorted(range(len(cands)), key=lambda j: _rank_key(rule, cands[j]))
                for r, j in enumerate(order, start=1):
                    cands[j].setdefault("ranks", {})[rule] = r
            for c in cands:
                sd = c["signal_date"]   # 후보별 자기 신호일 국면(US 세션 키는 다음 봉이라 결측 종목은 신호일이 다를 수 있음)
                tot, dn = breadth.get(sd, [0, 0])
                c["regime"] = {**regime.get(sd, {}), "breadth_down_pct": round(100.0 * dn / tot, 1) if tot else None, "universe_n": tot}
                c["pool"] = pid
                c["pool_n"] = len(cands)
    stats = {}
    all_keys = set(k for days in per_day.values() for k in days)
    for key in all_keys:
        stats[key] = {pid: len(per_day[pid].get(key, [])) for pid in pools}
        sd = next((cands[0]["signal_date"] for pid in pools for k2, cands in per_day[pid].items() if k2 == key and cands), None)
        stats[key]["_universe"] = breadth.get(sd, [0, 0])[0] if sd else 0
    return {pid: dict(days) for pid, days in per_day.items()}, stats


def discovery_sessions(market: str) -> dict:
    market = market.upper()
    if market not in _CACHE:
        _CACHE[market] = build(market)
    return _CACHE[market][0]


def nearmiss(market: str) -> dict[str, list]:
    """신호일 → 탈락 후보(풀 문턱 바로 밖) 압축 행. build 이후에만 채워진다."""
    market = market.upper()
    if market not in _CACHE:
        _CACHE[market] = build(market)
    return _NEARMISS.get(market, {})


def pool_stats(market: str) -> dict:
    market = market.upper()
    if market not in _CACHE:
        _CACHE[market] = build(market)
    return _CACHE[market][1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    sessions, stats = build(a.market)
    pools = POOLS_US if a.market.upper() == "US" else POOLS_KR
    keys = sorted(stats)
    print(f"[{a.market}] 세션 {len(keys)} ({keys[0] if keys else '-'} ~ {keys[-1] if keys else '-'})")
    for pid, desc in pools.items():
        ns = [stats[k].get(pid, 0) for k in keys]
        tot = sum(ns)
        print(f"  {pid:<14} 총 {tot:>6} · 일평균 {tot / max(1, len(keys)):5.1f} · 최대 {max(ns) if ns else 0:>4} — {desc}")
    k = a.date or (keys[-1] if keys else None)
    if k:
        print(f"\n[{k}] 유니버스 {stats[k].get('_universe')} · 풀별 {{{', '.join(f'{p}: {stats[k].get(p, 0)}' for p in pools)}}}")
        for pid in pools:
            for c in sessions.get(pid, {}).get(k, [])[:3]:
                print(f"   {pid} {c['ticker']} chg {c['chg']:+.1f} dvol {c['dvol']:.0f} max21 {c['max21']} ranks dvol#{c['ranks']['dvol_desc']} regime {c['regime']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
