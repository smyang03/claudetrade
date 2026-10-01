#!/usr/bin/env python3
"""인큐베이터 쉐도우 arm 진단 — 왜 높고 왜 낮나 · 국면 운영이 맞나 · 무엇을 버리나 (read-only, 2026-09-30).

    PYTHONUTF8=1 python tools/research/research_incubator_regime_20260930.py [--rebuild]

입력: `data/shadow/virtual_books.db` trades(읽기 전용 URI). arm 계약(tp/sl/hold)은
`tools/virtual_books.STRATEGIES` 정본에서 읽는다(문서·note 추정 금지 — 09-16 사고).
쓰기: `data/analysis/incubator_regime_20260930/`(캐시·CSV·JSON) + `docs/reports/incubator_regime_20260930.md`.

⚠️ 리포트 §0 결론은 실행 후 수기로 삽입했다 — 재실행하면 덮어쓰므로 결론을 다시 붙일 것.

## 0. 규약 (계산 전에 고정)

- **구간 분리**: forward = `backfill=0` AND session_date ≥ 2026-09-01 AND 만기도달(성적표 ⑥ 규약).
  backfill = `backfill=1`(인샘플). 둘은 절대 합치지 않는다. 판정 문장은 구간별로 따로 쓴다.
- **진입 규약**: KR·SLOW = session_date(신호일) 다음 봉 시가. US = session_date 봉 시가(신호 = 전일 봉).
- **청산 봉 재현**: arm 자기 계약(tp/sl/hold, BE락은 US·SLOW만)으로 `contract_exit_v2`와 같은 규칙을
  다시 돌려 청산 봉을 얻는다(원장 `exit_index`는 비어 있음). 재현 net과 원장 net 불일치율을 같이 싣는다.
- **판정 단위는 날짜**(신호일 클러스터 일평균). 보유 h세션이 겹치므로 백필 회귀 t는 Newey-West(lag h−1).

## 1. 수익 분해 (보유 창 = 진입 시가 → D(h−1) 종가, 가산식, 정확히 합이 맞는다)

    net = I + (S_hold − I) + (S_con + fee − S_hold) − fee + (net − S_con)
          시장    규모 기울기        계약 모양           비용    고르기(선택 알파)

- I      : 지수 보유 수익. US·SLOW = IWM, KR = KOSDAQ150(229200). (KR KOSPI200은 회귀에서 병기)
- S_hold : 그날 적격 유니버스 중 **같은 dvol20 십분위** 등가중 단순 보유(수수료 0).
- S_con  : 같은 십분위에 **같은 계약**(TP/SL/보유·BE락·수수료) — 09-23 규모매칭 계약 벤치의 US 확장.
- 적격: KR 거래대금 ≥20억·종가 ≥1,000원·이력 ≥25봉 / US 거래대금 ≥$50M·이력 ≥25봉(discovery 풀 유동성 문턱).
  트레이드의 십분위는 **자기 dvol20을 그날 컷오프에 대어** 정한다(SLOW 종목은 캐시에 없어도 됨).
- 선택 알파 = net − S_con. **이것이 "규모매칭 초과"이자 판정 지표다.**
- 섹터 기울기는 벤치를 만들지 않는다(`data/sector_map.json` 커버리지 부분) — 구성비만 서술.

## 2. 국면 (전부 신호일 종가까지 알려진 값, ex-ante — 계산 전 고정)

주 지수: US·SLOW = **SPY**(arm의 `idx_above_ma20` 태그와 같은 원천), KR = **069500(KOSPI200)**
(메타 태그는 KOSPI 종합 — 069500과의 MA20 일치율을 같이 싣는다).
  R1 `ma20`  : 종가 ≥ 20일 이동평균 → 위/아래
  R2 `ma60`  : 종가 ≥ 60일 이동평균 → 위/아래
  R3 `rv20`  : 20일 일간 로그수익 표준편차의 **확장창 삼분위**(그 날 이전 전 이력 기준, 최소 120개) → 저/중/고
  R4 `ret20` : 20일 수익 부호 → 상승/하락
  R5 `breadth`: 메타 `regime.breadth_down_pct`(신호일 하락 종목 비율) ≥ 50 → 하락우위/아님
  보조(선택 대상 아님): `ma20_alt` = US IWM / KR KOSDAQ150 MA20.

## 3. 국면 운영 검정 (워크포워드, 고정 달력 분할)

- 1반기 = 백필 신호일 < 2026-01-01, 2반기 = 2026-01-01 ~ 백필 끝. 규칙은 1반기로만 정하고 2반기로 평가.
- 규칙: 1반기 국면 칸별 선택 알파 평균 > 0인 칸만 "켬". (a) **MA20 사전고정** (b) **R1~R5 중 1반기 리프트 최대 1개**
  — (b)는 다중비교라 (a)와 나란히 싣는다.
- 꺼진 날 규약: raw 기준 → 현금(0) / 초과 기준 → 자본이 규모매칭 벤치에 있음(초과 0).
  리프트 시계열 = 꺼진 날 −(그날 값), 켜진 날 0 → 날짜 t.
- 추가 점검: 백필 전체로 고른 규칙 → forward 적용 리프트(표본 작음, 방향만).
- ⚠️ 정직한 성격 규정: 이것은 **09-07 무렵 이 데이터를 보고 설계된 arm 안에서의 국면 규칙 안정성**이지 알파 OOS가 아니다.
- 이미 국면/폭 필터가 걸린 arm(`*_rg`, `*_regime`, `c_us_panic_*`)은 국면 분할이 퇴화 → 부모에서 분할하고,
  부모의 "MA20 위" 칸을 종결 판정("KR MA20 위 급락 매수는 전 패널 음수", 09-10 OOS 6/6)과 대조한다.

## 4. 버릴 것/남길 것 규칙 (계산 전 고정)

- 기저선 `x*` = 모집단, 판정 없음(후보의 공급원이라 기록 중단 시 후보도 끊긴다).
- 데이터 끊김(forward 행 0 & 백필 마지막 < 2026-08) → **판정 보류**.
- 백필 일수 < 20 → **백필 부족 → keep-shadow**(forward만으로는 판정 안 함).
- forward 판독 가능 = 만기도달률 ≥ 50% AND forward 일수 ≥ 5.
- **regime-candidate**: 워크포워드 (a) 또는 (b)의 2반기 초과 리프트 t ≥ 2 AND (forward 판독 가능이면 forward 리프트 부호 ≥ 0).
- **discard**: 백필 선택 알파 일평균 ≤ 0 AND NOT(forward 판독 가능 AND forward 선택 알파 > 0 AND t ≥ 1).
- 그 외 **keep-shadow**.

## 5. 추가 사전등록 — `us_wide_dvol` 규칙 외부 재생 (`--wide-replay`, 2026-09-30, 계산 전 고정)

목적: 9월 wide 선택 알파(+4.8%, 15건 1베팅)가 9월 한정인지, 2023~2025에도 있었는지.
- **규칙 재현**(us_swing_shadow_runner·virtual_books 정본): 신호봉 i에서 등락 ≤ −5% · 종가 ≥ $5 · 신호일 거래대금(종가×거래량)
  ≥ $15M = wide 적격(Yahoo day_losers 대응) → 밴드 $100M ≤ 거래대금 < $500M · MAX21 ≥ 8(결측은 통과) = 통과 →
  **거래대금 1위 1종목**. 진입 = 다음 봉 시가, 계약 = TP12 / SL−25 / 보유 7(진입일 포함) / BE락 4 / 비용 0.50%(KIS 실측 왕복).
  같은 종목 재진입 허용·슬롯 7 무시(일 1건 × 보유 ≤7이라 사실상 비구속) — 정본과 같다.
- **데이터**: A = `data/price_backfill_alpaca/us`(SIP·수정 all, 2023-01-03 ~ 2025-04-30, 1,546종목) — **주 판정 창**.
  B = `data/price/us`(2025-05-19 ~) — 2025 연도 보강(5~12월)과 **재현 검증**(2026-08-12 이후 실제 원장 픽과 일치율)에만 쓴다.
  A·B는 이어붙이지 않는다(각 시리즈 안에서만 창을 만든다).
- ⚠️ **생존편향**: 두 소스 모두 2026년 현재 유니버스(생존 종목)다. 2023~2025 상장폐지 급락주가 빠진다 → arm과 벤치에 같은 방향으로
  걸리지만 급락주 풀은 폐지 비중이 높아 arm 쪽이 더 위로 편향될 수 있다. 결과는 **상한**으로 읽는다.
  Yahoo day_losers 상위 100 컷은 재현 안 함(생존 유니버스라 −5% 이하 적격이 100을 넘는 날이 드묾 — 실측 일수를 싣는다).
- **벤치**: (1) 규모매칭 계약 벤치 — 그날 유니버스(거래대금 ≥ $50M) dvol20 십분위 같은 칸, 같은 계약·비용 = **판정 벤치**.
  (1b) 같은 벤치를 유니버스 거래대금 ≥ $5M로 넓힌 변형(유동성 문턱 민감도). (2) IWM 진입 시가 → 실청산 종가.
  (3) 모집단: P0 = wide 적격 전량, P1 = 밴드+MAX 통과 전량(`us_wide_all_v2` 대응). **선택 = rank1 − P1**, 밴드 효과 = P1 − P0.
- **통계**: 신호일 단위 t(rank1은 하루 1건). 보유 겹침 보정 NW(lag 6) t를 부표로. 연도·반기 부호.
- **판정(고정)**: 창 2023-01 ~ 2025-12(A + B의 2025분)에서 rank1 − 규모매칭(1) 일평균 > 0 AND 날짜 t ≥ 2.0
  AND 연도 3개 중 ≥ 2개 양수 → **후보**. 아니면 **기각**(9월은 반등 달 효과로 읽는다).
  A 단독(2023-01 ~ 2025-04)도 같은 문턱으로 병기 — 밴드 발견(08-20) 이전 데이터라 더 깨끗한 OOS다.
"""
from __future__ import annotations

import csv
import json
import math
import pickle
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from tools.research_benchmark_excess import bench_bars  # noqa: E402
from tools.virtual_books import (BE, FEE_KR, FEE_US, HOLD_SESSIONS, SL, STRATEGIES, TP,  # noqa: E402
                                 bars_kr, bars_slow, strategy_market)
from research_early_exit_no_bump import bars as bars_us  # noqa: E402

VB = ROOT / "data" / "shadow" / "virtual_books.db"
OUTDIR = ROOT / "data" / "analysis" / "incubator_regime_20260930"
REPORT = ROOT / "docs" / "reports" / "incubator_regime_20260930.md"
FORWARD_START = "2026-09-01"
SPLIT = "2026-01-01"
UNI_START = "2025-05-01"
KR_DIR, US_DIR = ROOT / "data" / "price" / "kr", ROOT / "data" / "price" / "us"
SECTOR = ROOT / "data" / "sector_map.json"
REGVARS = ["ma20", "ma60", "rv20", "ret20", "breadth"]

SPEC = {s["id"]: s for s in STRATEGIES}


# ───────────────────────── 공통 ─────────────────────────
def load_csv_bars(path: Path) -> list[tuple]:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.reader(fh):
            if len(r) >= 6 and r[0][:2] == "20":
                try:
                    rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                except ValueError:
                    continue
    return sorted(rows)


def contract(entry, win, tp, sl, hold, be_lock, fee):
    """contract_exit_v2 동일 규칙 → (net, 청산봉 index) 또는 None."""
    if not win or entry <= 0:
        return None
    peak = (win[0][4] - entry) / entry * 100.0
    for i, (_d, _o, hi, _lo, c, _v) in enumerate(win[:hold]):
        hip = (hi - entry) / entry * 100.0 if i > 0 else (c - entry) / entry * 100.0
        cp = (c - entry) / entry * 100.0
        if hip >= tp:
            return tp - fee, i
        if cp <= sl:
            return cp - fee, i
        if be_lock and peak >= BE and cp <= 0:
            return cp - fee, i
        peak = max(peak, hip)
    if len(win) < hold:
        return None
    return (win[hold - 1][4] - entry) / entry * 100.0 - fee, hold - 1


def arm_contract(sid):
    s = SPEC.get(sid, {})
    mk = strategy_market(s) if s else ("KR" if "kr" in sid else "US")
    return (mk, float(s.get("tp", TP)), float(s.get("sl", SL)), int(s.get("hold", HOLD_SESSIONS)))


def day_stat(pairs, min_n=3):
    g = defaultdict(list)
    for d, v in pairs:
        g[d].append(v)
    m = [st.mean(v) for _, v in sorted(g.items())]
    if not m:
        return None, None, 0
    if len(m) < min_n or st.stdev(m) == 0:
        return st.mean(m), None, len(m)
    return st.mean(m), st.mean(m) / (st.stdev(m) / math.sqrt(len(m))), len(m)


def day_series(pairs):
    g = defaultdict(list)
    for d, v in pairs:
        g[d].append(v)
    return {d: st.mean(v) for d, v in g.items()}


def ols_nw(y, x, lag):
    """y = a + b x, Newey-West(lag) 표준오차 → (a, t_a, b, t_b)."""
    n = len(y)
    if n < 10:
        return None
    mx, my = st.mean(x), st.mean(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx == 0:
        return None
    b = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / sxx
    a = my - b * mx
    e = [yi - a - b * xi for xi, yi in zip(x, y)]
    X = [(1.0, xi) for xi in x]
    # (X'X)^-1
    s0, s1, s2 = n, sum(x), sum(xi * xi for xi in x)
    det = s0 * s2 - s1 * s1
    inv = ((s2 / det, -s1 / det), (-s1 / det, s0 / det))
    g = [(X[t][0] * e[t], X[t][1] * e[t]) for t in range(n)]
    S = [[0.0, 0.0], [0.0, 0.0]]
    for L in range(0, lag + 1):
        w = 1.0 if L == 0 else 1 - L / (lag + 1)
        for t in range(L, n):
            for i in range(2):
                for j in range(2):
                    if L == 0:
                        S[i][j] += g[t][i] * g[t][j]
                    else:   # Γ_L + Γ_L'
                        S[i][j] += w * (g[t][i] * g[t - L][j] + g[t - L][i] * g[t][j])
    V = [[sum(inv[i][k] * S[k][l] * inv[l][j] for k in range(2) for l in range(2)) for j in range(2)] for i in range(2)]
    sa, sb = math.sqrt(max(V[0][0], 1e-18)), math.sqrt(max(V[1][1], 1e-18))
    return a, a / sa, b, b / sb


def fmt(m, t=None, pct=True):
    if m is None:
        return "-"
    s = f"{m:+.2f}%" if pct else f"{m:+.2f}"
    return s + (f" · t {t:+.2f}" if t is not None else "")


# ───────────────────────── 규모매칭 유니버스 벤치 ─────────────────────────
def contracts_needed():
    need = {"KR": set(), "US": set()}
    for s in STRATEGIES:
        mk = strategy_market(s)
        key = (float(s.get("tp", TP)), float(s.get("sl", SL)), int(s.get("hold", HOLD_SESSIONS)))
        need["KR" if mk == "KR" else "US"].add(key)
    return need


def build_universe(market, cons):
    """→ cuts[sig_date] = 9개 컷(dvol20), sz[(sig_date, dec, ('hold',h)|(tp,sl,h))] = 평균."""
    d = KR_DIR if market == "KR" else US_DIR
    pre = "kr_" if market == "KR" else "us_"
    holds = sorted({c[2] for c in cons})
    be = market != "KR"
    fee = FEE_KR if market == "KR" else FEE_US
    per_date = defaultdict(list)
    for p in sorted(d.glob(f"{pre}*.csv")):
        b = load_csv_bars(p)
        for i in range(25, len(b) - 1):
            dt, _o, _h, _l, c, v = b[i]
            if dt < UNI_START:
                continue
            dvol = c * v / (1e8 if market == "KR" else 1e6)
            if market == "KR":
                if c < 1000 or dvol < 20:
                    continue
            elif dvol < 50:
                continue
            entry = b[i + 1][1]
            if entry <= 0:
                continue
            dv20 = st.mean(x[4] * x[5] for x in b[i - 19:i + 1]) / (1e8 if market == "KR" else 1e6)
            rec = {}
            for h in holds:
                if i + h < len(b):
                    rec[("hold", h)] = (b[i + h][4] / entry - 1) * 100
            for tp, sl, h in cons:
                r = contract(entry, b[i + 1:i + 1 + h], tp, sl, h, be, fee)
                if r is not None:
                    rec[(tp, sl, h)] = r[0]
            per_date[dt].append((dv20, rec))
    cuts, sz = {}, defaultdict(lambda: [0.0, 0])
    for dt, v in per_date.items():
        if len(v) < 50:
            continue
        v.sort(key=lambda r: r[0])
        n = len(v)
        cuts[dt] = [v[(k * n) // 10][0] for k in range(1, 10)]
        for j, (_dv, rec) in enumerate(v):
            dec = min(9, j * 10 // n)
            for k, val in rec.items():
                a = sz[(dt, dec, k)]
                a[0] += val
                a[1] += 1
    return cuts, {k: (a[0] / a[1], a[1]) for k, a in sz.items()}


def decile_of(cuts, dv20):
    for k, c in enumerate(cuts):
        if dv20 < c:
            return k
    return 9


# ───────────────────────── 국면 ─────────────────────────
def regime_table(bars):
    """date → {ma20, ma60, rv20(tercile), ret20} (그 날 종가까지)."""
    closes = [x[4] for x in bars]
    dates = [x[0] for x in bars]
    rets = [None] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    out, rv_hist = {}, []
    for i in range(len(bars)):
        rv = st.stdev(rets[i - 19:i + 1]) * 100 if i >= 20 else None
        r = {}
        if i >= 19:
            r["ma20"] = "위" if closes[i] >= st.mean(closes[i - 19:i + 1]) else "아래"
        if i >= 59:
            r["ma60"] = "위" if closes[i] >= st.mean(closes[i - 59:i + 1]) else "아래"
        if i >= 20:
            r["ret20"] = "상승" if closes[i] > closes[i - 20] else "하락"
        if rv is not None:
            if len(rv_hist) >= 120:
                sh = sorted(rv_hist)
                q1, q2 = sh[len(sh) // 3], sh[2 * len(sh) // 3]
                r["rv20"] = "저" if rv < q1 else ("중" if rv < q2 else "고")
            rv_hist.append(rv)
        out[dates[i]] = r
    return out, dates


def asof(table, dates, d):
    """d 이하 마지막 날짜의 값."""
    import bisect
    j = bisect.bisect_right(dates, d) - 1
    return table.get(dates[j], {}) if j >= 0 else {}


# ───────────────────────── 메인 ─────────────────────────
def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    rebuild = "--rebuild" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    need = contracts_needed()
    cache = OUTDIR / "universe_cache.pkl"
    if cache.exists() and not rebuild:
        uni = pickle.loads(cache.read_bytes())
    else:
        uni = {}
        for mk in ("KR", "US"):
            print(f"[유니버스 {mk}] 계약 {sorted(need[mk])} 계산 중...", flush=True)
            uni[mk] = build_universe(mk, need[mk])
        cache.write_bytes(pickle.dumps(uni))

    # 지수
    idx = {"IWM": bench_bars("us", "IWM"), "SPY": bench_bars("us", "SPY"),
           "K200": bench_bars("kr", "069500"), "KQ150": bench_bars("kr", "229200")}
    idx_i = {k: {b[0]: i for i, b in enumerate(v)} for k, v in idx.items()}
    reg = {}
    for k in ("SPY", "K200", "IWM", "KQ150"):
        reg[k] = regime_table(idx[k])

    def idx_ret(name, entry_date, n_bars):
        """entry_date 시가 → entry+n_bars-1 종가."""
        b, ii = idx[name], idx_i[name]
        i = ii.get(entry_date)
        if i is None or i + n_bars - 1 >= len(b):
            return None
        return (b[i + n_bars - 1][4] / b[i][1] - 1) * 100

    cal = {"KR": [b[0] for b in idx["K200"]], "US": [b[0] for b in idx["IWM"]]}
    cal_i = {m: {d: i for i, d in enumerate(v)} for m, v in cal.items()}

    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    rows = con.execute("SELECT strategy_id, session_date, ticker, entry_price, net_pct, status, backfill, meta "
                       "FROM trades").fetchall()
    con.close()

    sector = json.loads(SECTOR.read_text(encoding="utf-8")) if SECTOR.exists() else {}

    arm_info = defaultdict(lambda: {"entries_fwd": 0, "mat_fwd": 0, "rows_fwd": 0, "last_bf": ""})
    recs = []          # 거래 단위 레코드
    mism = defaultdict(lambda: [0, 0])
    for sid, sd, tk, ep, net, status, bf, meta in rows:
        if only and sid not in only:
            continue
        if SPEC.get(sid, {}).get("retired"):
            continue
        mk, tp, sl, hold = arm_contract(sid)
        umk = "KR" if mk == "KR" else "US"
        seg = "bf" if bf else ("fwd" if sd >= FORWARD_START else "bf")
        ai = arm_info[sid]
        # 만기도달
        ci = cal_i[umk]
        pos = ci.get(sd)
        if pos is None:
            import bisect
            pos = bisect.bisect_right(cal[umk], sd) - 1
        matured = (len(cal[umk]) - 1 - pos) >= hold
        if seg == "fwd":
            ai["rows_fwd"] += 1
            ai["entries_fwd"] += 1
            ai["mat_fwd"] += int(matured)
        else:
            ai["last_bf"] = max(ai["last_bf"], sd)
        if status != "CLOSED" or net is None or not matured:
            continue
        try:
            m = json.loads(meta) if meta else {}
        except ValueError:
            m = {}
        bars = bars_kr(tk) if mk == "KR" else (bars_slow(tk) if mk == "SLOW" else bars_us(tk))
        if not bars:
            continue
        if mk == "US":
            ei = next((i for i, x in enumerate(bars) if x[0] == sd), None)
        else:
            ei = next((i for i, x in enumerate(bars) if x[0] > sd), None)
        if ei is None or ei < 21:
            continue
        E = bars[ei][0]
        sig = m.get("signal_date") or (bars[ei - 1][0] if mk == "US" else sd)
        entry = bars[ei][1]   # 원장 진입가는 수정주가 재계산 전 척도일 수 있어 봉 척도로 통일(청산 봉만 필요)
        r = contract(entry, bars[ei:ei + hold], tp, sl, hold, mk != "KR", FEE_KR if mk == "KR" else FEE_US)
        if r is None:
            continue
        mism[sid][1] += 1
        if abs(r[0] - net) > 0.05:
            mism[sid][0] += 1
        xi = r[1]
        # 자기 dvol20 (신호봉까지)
        si = ei - 1
        dv20 = st.mean(x[4] * x[5] for x in bars[si - 19:si + 1]) / (1e8 if umk == "KR" else 1e6)
        cuts, sz = uni[umk]
        rec = {"sid": sid, "seg": seg, "d": sig, "E": E, "tk": tk, "net": net, "hold": hold, "mk": mk}
        if sig in cuts:
            dec = decile_of(cuts[sig], dv20)
            sh = sz.get((sig, dec, ("hold", hold)))
            sc = sz.get((sig, dec, (tp, sl, hold)))
            if sh and sc and sh[1] >= 20 and sc[1] >= 20:
                rec.update(dec=dec, S_hold=sh[0], S_con=sc[0])
        fee = FEE_KR if mk == "KR" else FEE_US
        rec["fee"] = fee
        if umk == "KR":
            rec["I"] = idx_ret("KQ150", E, hold)
            rec["I2"] = idx_ret("K200", E, hold)
            rec["Ix"] = idx_ret("KQ150", E, xi + 1)
            rec["Ix2"] = idx_ret("K200", E, xi + 1)
            rg, rd = reg["K200"]
            alt, ad = reg["KQ150"]
        else:
            rec["I"] = idx_ret("IWM", E, hold)
            rec["I2"] = idx_ret("SPY", E, hold)
            rec["Ix"] = idx_ret("IWM", E, xi + 1)
            rec["Ix2"] = None
            rg, rd = reg["SPY"]
            alt, ad = reg["IWM"]
        rr = asof(rg, rd, sig)
        for k in ("ma20", "ma60", "rv20", "ret20"):
            rec[k] = rr.get(k)
        rec["ma20_alt"] = asof(alt, ad, sig).get("ma20")
        bd = (m.get("regime") or {}).get("breadth_down_pct")
        rec["breadth"] = None if bd is None else ("하락우위" if bd >= 50 else "아님")
        tagma = (m.get("regime") or {}).get("idx_above_ma20")
        rec["tag_ma20"] = None if tagma is None else ("위" if tagma else "아래")
        rec["sector"] = ((sector.get(umk) or {}).get(str(tk)) or {}).get("sector")
        recs.append(rec)

    # 거래 CSV
    keys = ["sid", "seg", "d", "E", "tk", "net", "hold", "mk", "dec", "S_hold", "S_con", "fee", "I", "I2", "Ix", "Ix2",
            "ma20", "ma60", "rv20", "ret20", "breadth", "ma20_alt", "tag_ma20", "sector"]
    with (OUTDIR / "trades_decomposed.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in recs:
            w.writerow(r)

    def kind(sid):
        if sid.startswith(("xkr_", "xus_")):
            return "기저선"
        return "후보" if sid.startswith("c_") else "실행변형"

    by = defaultdict(list)
    for r in recs:
        by[(r["sid"], r["seg"])].append(r)

    # ── 1. 분해 ──
    decomp = {}
    for (sid, seg), rs in by.items():
        full = [r for r in rs if r.get("S_con") is not None and r.get("I") is not None]
        if not full:
            continue
        comp = {
            "raw": [(r["d"], r["net"]) for r in full],
            "I": [(r["d"], r["I"]) for r in full],
            "size": [(r["d"], r["S_hold"] - r["I"]) for r in full],
            "shape": [(r["d"], r["S_con"] + r["fee"] - r["S_hold"]) for r in full],
            "fee": [(r["d"], -r["fee"]) for r in full],
            "sel": [(r["d"], r["net"] - r["S_con"]) for r in full],
            "xidx": [(r["d"], r["net"] - r["Ix"]) for r in full if r.get("Ix") is not None],
        }
        out = {k: day_stat(v) for k, v in comp.items()}
        out["n"] = len(full)
        out["n_all"] = len(rs)
        # 회귀 (날짜 코호트)
        ys = day_series(comp["raw"])
        xs = day_series(comp["I"])
        ss = day_series([(r["d"], r["S_hold"]) for r in full])
        ds = sorted(set(ys) & set(xs))
        h = max(r["hold"] for r in full)
        out["reg_idx"] = ols_nw([ys[d] for d in ds], [xs[d] for d in ds], h - 1) if len(ds) >= 10 else None
        out["reg_size"] = ols_nw([ys[d] for d in ds], [ss[d] for d in ds], h - 1) if len(ds) >= 10 else None
        if sid.startswith(("c_kr", "xkr", "kr_")):
            x2 = day_series([(r["d"], r["I2"]) for r in full if r.get("I2") is not None])
            d2 = sorted(set(ys) & set(x2))
            out["reg_idx2"] = ols_nw([ys[d] for d in d2], [x2[d] for d in d2], h - 1) if len(d2) >= 10 else None
        # 섹터 구성
        sec = defaultdict(int)
        for r in full:
            sec[r["sector"] or "미분류"] += 1
        out["sector"] = sorted(sec.items(), key=lambda x: -x[1])[:3]
        out["sector_cov"] = 1 - sec.get("미분류", 0) / len(full)
        # TP 캡: 계약 모양
        decomp[(sid, seg)] = out

    # ── 2. 국면 표 ──
    regrows = []
    for (sid, seg), rs in by.items():
        if seg != "bf":
            continue
        full = [r for r in rs if r.get("S_con") is not None]
        for var in REGVARS + ["ma20_alt", "tag_ma20"]:
            g = defaultdict(list)
            for r in full:
                if r.get(var) is not None:
                    g[r[var]].append(r)
            for state, xs_ in g.items():
                for half, sub in (("전체", xs_), ("1반기", [r for r in xs_ if r["d"] < SPLIT]),
                                  ("2반기", [r for r in xs_ if r["d"] >= SPLIT])):
                    m1, t1, n1 = day_stat([(r["d"], r["net"] - r["S_con"]) for r in sub])
                    m2, t2, _ = day_stat([(r["d"], r["net"]) for r in sub])
                    regrows.append({"sid": sid, "var": var, "state": state, "half": half, "days": n1,
                                    "sel": m1, "sel_t": t1, "raw": m2, "raw_t": t2})
    with (OUTDIR / "regime_table.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(regrows[0].keys()) if regrows else ["sid"])
        w.writeheader()
        for r in regrows:
            w.writerow(r)
    RT = {(r["sid"], r["var"], r["state"], r["half"]): r for r in regrows}

    # MA20 일치율
    agree = [(r["ma20"] == r["tag_ma20"]) for r in recs if r.get("tag_ma20") and r.get("ma20")]
    agree_kr = [(r["ma20"] == r["tag_ma20"]) for r in recs if r["mk"] == "KR" and r.get("tag_ma20") and r.get("ma20")]
    agree_us = [(r["ma20"] == r["tag_ma20"]) for r in recs if r["mk"] != "KR" and r.get("tag_ma20") and r.get("ma20")]

    # ── 3. 워크포워드 ──
    def lift_series(sub, var, on_states, val):
        """꺼진 날 −값, 켜진 날 0. 날짜 단위(그날 평균)."""
        ser = defaultdict(list)
        for r in sub:
            s_ = r.get(var)
            v = val(r)
            ser[r["d"]].append(0.0 if (s_ in on_states) else -v)
        return [(d, st.mean(v)) for d, v in ser.items()]

    sel_f = lambda r: r["net"] - r["S_con"]  # noqa: E731
    raw_f = lambda r: r["net"]  # noqa: E731

    def choose(sub, var):
        g = defaultdict(list)
        for r in sub:
            if r.get(var) is not None:
                g[r[var]].append((r["d"], sel_f(r)))
        on = {s_ for s_, v in g.items() if (day_stat(v)[0] or 0) > 0}
        return on, g

    wf = {}
    for (sid, seg), rs in by.items():
        if seg != "bf":
            continue
        full = [r for r in rs if r.get("S_con") is not None]
        h1 = [r for r in full if r["d"] < SPLIT]
        h2 = [r for r in full if r["d"] >= SPLIT]
        fw = [r for r in by.get((sid, "fwd"), []) if r.get("S_con") is not None]
        n1, n2 = len({r["d"] for r in h1}), len({r["d"] for r in h2})
        res = {"d1": n1, "d2": n2}
        if n1 < 10 or n2 < 10:
            wf[sid] = res
            continue
        # (a) MA20 사전고정
        on_a, _ = choose(h1, "ma20")
        # (b) 최대 1반기 리프트
        best, best_l = None, -1e9
        for var in REGVARS:
            if sum(1 for r in h1 if r.get(var) is not None) < len(h1) * 0.8:
                continue
            on, _ = choose(h1, var)
            l1 = day_stat(lift_series([r for r in h1 if r.get(var) is not None], var, on, sel_f))[0] or 0
            if l1 > best_l:
                best, best_l = (var, on), l1
        for tag, (var, on) in (("a", ("ma20", on_a)), ("b", best if best else ("ma20", on_a))):
            s2 = [r for r in h2 if r.get(var) is not None]
            ls = day_stat(lift_series(s2, var, on, sel_f))
            lr = day_stat(lift_series(s2, var, on, raw_f))
            off_share = (sum(1 for r in s2 if r.get(var) not in on) / len(s2)) if s2 else None
            res[tag] = {"var": var, "on": sorted(on), "lift_sel": ls, "lift_raw": lr, "off": off_share}
        # 백필 전체로 고른 MA20 규칙 → forward
        on_full, _ = choose(full, "ma20")
        fsub = [r for r in fw if r.get("ma20") is not None]
        res["fwd_ma20"] = {"on": sorted(on_full), "lift_sel": day_stat(lift_series(fsub, "ma20", on_full, sel_f)),
                           "days": len({r["d"] for r in fsub})}
        # (b) 선택 변수 자체의 forward 점검 — 백필 전체로 켬 칸을 다시 정해 forward에 적용
        bvar = res["b"]["var"]
        on_fb, _ = choose(full, bvar)
        fsub_b = [r for r in fw if r.get(bvar) is not None]
        res["fwd_b"] = {"var": bvar, "on": sorted(on_fb), "lift_sel": day_stat(lift_series(fsub_b, bvar, on_fb, sel_f)),
                        "days": len({r["d"] for r in fsub_b})}
        wf[sid] = res

    # ── 4. 판정 ──
    verdicts = {}
    for sid in sorted({k[0] for k in by}):
        k = kind(sid)
        bf, fw = decomp.get((sid, "bf")), decomp.get((sid, "fwd"))
        ai = arm_info[sid]
        mat_rate = ai["mat_fwd"] / ai["entries_fwd"] if ai["entries_fwd"] else 0
        fdays = fw["sel"][2] if fw else 0
        f_read = mat_rate >= 0.5 and fdays >= 5
        bdays = bf["sel"][2] if bf else 0
        w_ = wf.get(sid, {})
        reason = ""
        if k == "기저선":
            v, reason = "모집단", "후보 공급원 — 판정 대상 아님"
        elif ai["rows_fwd"] == 0 and ai["last_bf"] < "2026-08-01":
            v, reason = "판정 보류", f"데이터 끊김(백필 마지막 {ai['last_bf']})"
        elif bdays < 20:
            v, reason = "keep-shadow", f"백필 부족({bdays}일) — forward만으론 판정 안 함"
        else:
            rc = False
            for tag in ("a", "b"):
                x = w_.get(tag)
                if x and x["lift_sel"][1] is not None and x["lift_sel"][1] >= 2:
                    fl = (w_.get("fwd_ma20" if tag == "a" else "fwd_b") or {}).get("lift_sel", (None,))[0]
                    if not f_read or fl is None or fl >= 0:
                        rc = True
                        reason = f"WF 2반기 리프트({x['var']}) {x['lift_sel'][0]:+.2f}%p t {x['lift_sel'][1]:+.2f}"
            fpos = f_read and fw and fw["sel"][0] is not None and fw["sel"][0] > 0 and (fw["sel"][1] or 0) >= 1
            if rc:
                v = "regime-candidate"
            elif (bf["sel"][0] or 0) <= 0 and not fpos:
                v = "discard"
                reason = f"백필 선택알파 {bf['sel'][0]:+.2f}% (t {bf['sel'][1] or 0:+.2f}), forward 반증 없음"
            else:
                v = "keep-shadow"
                reason = (f"백필 선택알파 {bf['sel'][0]:+.2f}% (t {bf['sel'][1] or 0:+.2f})"
                          + (" · forward 양수 t≥1" if fpos else ""))
        verdicts[sid] = {"kind": k, "verdict": v, "reason": reason, "mat_rate": mat_rate, "f_read": f_read,
                         "bdays": bdays, "fdays": fdays}

    # ───────────── 리포트 ─────────────
    L = ["# 인큐베이터 쉐도우 진단 — 왜 높고 낮나 · 국면 운영 · 버릴 것 (2026-09-30)", "",
         "read-only. `tools/research/research_incubator_regime_20260930.py`(규약·국면 정의·판정 규칙은 계산 전 docstring 고정).",
         "**forward(09-01~, 만기도달분)와 백필(인샘플)은 절대 합치지 않았다.** 값은 신호일 클러스터 일평균 · 날짜 t.",
         "**선택 알파 = net − 규모매칭 계약 벤치**(같은 날·같은 dvol20 십분위·같은 TP/SL/보유/수수료). 판정 지표.",
         f"청산 봉 재현 불일치(|Δnet|>0.05%p): 전체 {sum(a for a, _ in mism.values()):,}/{sum(b for _, b in mism.values()):,}건.",
         f"MA20 국면(069500/SPY) vs 메타 태그(KOSPI/SPY) 일치율: KR {100*st.mean(agree_kr):.1f}% · US {100*st.mean(agree_us):.1f}%"
         if agree_kr and agree_us else "", ""]

    order = sorted(verdicts, key=lambda s: ({"기저선": 2, "후보": 0, "실행변형": 1}[verdicts[s]["kind"]], s))

    def row_decomp(sid, seg):
        o = decomp.get((sid, seg))
        if not o:
            return None
        g = lambda k: o[k][0]  # noqa: E731
        return (f"| `{sid}` | {o['sel'][2]} | {fmt(g('raw'))} | {fmt(g('I'))} | {fmt(g('size'))} | "
                f"{fmt(g('shape'))} | {fmt(g('fee'))} | **{fmt(o['sel'][0], o['sel'][1])}** | {fmt(o['xidx'][0], o['xidx'][1])} |")

    hdr = ["| arm | 일수 | 원 net | 시장(I) | 규모 기울기 | 계약 모양 | 비용 | **선택 알파** | 참고: 지수초과(실청산) |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    L += ["## 1. 판정표", "",
          "| arm | 구분 | fwd 선택알파/t (일수, 만기율) | 백필 선택알파/t (일수) | 백필 β조정 α/t(NW) · β | 국면 의존(백필 MA20 위/아래 선택알파) | 판정 | 사유 |",
          "|---|---|---:|---:|---:|---|---|---|"]
    for sid in order:
        v = verdicts[sid]
        fw, bf = decomp.get((sid, "fwd")), decomp.get((sid, "bf"))
        fcell = (f"{fmt(fw['sel'][0], fw['sel'][1])} ({fw['sel'][2]}일, {100*v['mat_rate']:.0f}%)" if fw else "-")
        bcell = f"{fmt(bf['sel'][0], bf['sel'][1])} ({bf['sel'][2]})" if bf else "-"
        rg = bf.get("reg_idx") if bf else None
        rcell = f"{rg[0]:+.2f}% · t {rg[1]:+.2f} · β {rg[2]:.2f}" if rg else "-"
        up = RT.get((sid, "ma20", "위", "전체"))
        dn = RT.get((sid, "ma20", "아래", "전체"))
        reg_c = (f"위 {fmt(up['sel'], up['sel_t']) if up else '-'} / 아래 {fmt(dn['sel'], dn['sel_t']) if dn else '-'}")
        L.append(f"| `{sid}` | {v['kind']} | {fcell} | {bcell} | {rcell} | {reg_c} | **{v['verdict']}** | {v['reason']} |")

    L += ["", "## 2. 왜 높고 왜 낮나 — 수익 분해 (보유 창, 가산식)", "",
          "net = 시장 + 규모 기울기 + 계약 모양 + 비용 + 선택 알파. 시장 = IWM(US) / KOSDAQ150(KR).", "",
          "### 2a. forward (09-01~, 만기도달분)", ""] + hdr
    for sid in order:
        x = row_decomp(sid, "fwd")
        if x:
            L.append(x)
    L += ["", "### 2b. 백필 (인샘플)", ""] + hdr
    for sid in order:
        x = row_decomp(sid, "bf")
        if x:
            L.append(x)

    L += ["", "### 2c. 베타 회귀 (백필, 날짜 코호트 · NW lag h−1)", "",
          "| arm | β(지수) | α/t | β(규모매칭 보유) | α/t | KR: β(KOSPI200) · α/t |", "|---|---:|---:|---:|---:|---:|"]
    for sid in order:
        o = decomp.get((sid, "bf"))
        if not o or not o.get("reg_idx"):
            continue
        a = o["reg_idx"]
        b = o.get("reg_size")
        c = o.get("reg_idx2")
        L.append(f"| `{sid}` | {a[2]:.2f} | {fmt(a[0], a[1])} | {b[2]:.2f} | {fmt(b[0], b[1])} | "
                 + (f"{c[2]:.2f} · {fmt(c[0], c[1])}" if c else "-") + " |")
    L += ["", "### 2d. 섹터 구성 — forward (서술용, sector_map 커버리지 부분)", "",
          "| arm | 구간 | 커버리지 | 상위 3 섹터(건수) |", "|---|---|---:|---|"]
    for sid in order:
        for seg in ("fwd",):
            o = decomp.get((sid, seg))
            if o and verdicts[sid]["kind"] != "기저선" and o["sel"][2] >= 3:
                L.append(f"| `{sid}` | {seg} | {100*o['sector_cov']:.0f}% | "
                         + ", ".join(f"{s}({n})" for s, n in o["sector"]) + " |")

    L += ["", "## 3. 국면별 (백필, 선택 알파 · 날짜 t · 일수) — 백필 ≥20일 arm + 급락 모집단 2개. 전체는 `regime_table.csv`", "",
          "⚠️ KR `rv20` 확장창 삼분위(2018~ 이력)는 2025-06 이후 거의 전부 '고'라 분할이 퇴화했다 — 정의는 사전고정이라 바꾸지 않았다.", ""]
    L += ["| arm | 변수 | 칸별: 선택알파 · t (일수) |", "|---|---|---|"]
    for sid in order:
        if not decomp.get((sid, "bf")) or verdicts[sid]["bdays"] < 20:
            continue
        if verdicts[sid]["kind"] == "기저선" and sid not in ("xkr_fallen3", "xus_fallen3"):
            continue
        for var in REGVARS + ["ma20_alt"]:
            cells = []
            for (s_, v_, st_, hf), r in RT.items():
                if s_ == sid and v_ == var and hf == "전체":
                    cells.append(f"{st_} {fmt(r['sel'], r['sel_t'])} ({r['days']})")
            if cells:
                L.append(f"| `{sid}` | {var} | {' / '.join(sorted(cells))} |")

    L += ["", "## 4. 국면 운영 워크포워드 (1반기 < 2026-01-01로 규칙 → 2반기 평가)", "",
          "리프트 = 규칙을 켰을 때 − 항상 켬. 초과 기준(꺼진 날 = 규모매칭 벤치 보유) / raw 기준(꺼진 날 = 현금).", "",
          "| arm | 1반기/2반기 일수 | (a) MA20 사전고정: 켬 칸 · 2반기 리프트 초과/t · raw/t · 꺼짐 비율 | "
          "(b) 1반기 최적 변수: 켬 칸 · 2반기 리프트 초과/t | 백필 전체 MA20 규칙 → forward 리프트(일수) | (b) 변수 → forward 리프트(일수) |",
          "|---|---|---|---|---|---|"]
    for sid in order:
        w_ = wf.get(sid)
        if not w_:
            continue
        if "a" not in w_:
            L.append(f"| `{sid}` | {w_['d1']}/{w_['d2']} | 반기 표본 < 10일 | - | - | - |")
            continue
        a, b, f_ = w_["a"], w_["b"], w_["fwd_ma20"]
        L.append(f"| `{sid}` | {w_['d1']}/{w_['d2']} | {','.join(a['on']) or '없음'} · "
                 f"{fmt(a['lift_sel'][0], a['lift_sel'][1])} · {fmt(a['lift_raw'][0], a['lift_raw'][1])} · "
                 f"{(a['off'] or 0)*100:.0f}% | {b['var']}:{','.join(b['on']) or '없음'} · {fmt(b['lift_sel'][0], b['lift_sel'][1])} | "
                 f"{','.join(f_['on']) or '없음'} → {fmt(f_['lift_sel'][0], f_['lift_sel'][1])} ({f_['days']}) | "
                 f"{w_['fwd_b']['var']}:{','.join(w_['fwd_b']['on']) or '없음'} → "
                 f"{fmt(w_['fwd_b']['lift_sel'][0], w_['fwd_b']['lift_sel'][1])} ({w_['fwd_b']['days']}) |")

    js = {"verdicts": verdicts,
          "decomp": {f"{k[0]}|{k[1]}": {kk: vv for kk, vv in v.items()} for k, v in decomp.items()},
          "walkforward": wf, "mismatch": dict(mism)}
    (OUTDIR / "summary.json").write_text(json.dumps(js, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    out_path = REPORT if not only else OUTDIR / "dryrun_report.md"
    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n저장: {out_path}")
    return 0


# ───────────────────────── §5 wide rank-1 외부 재생 ─────────────────────────
ALPACA_DIR = ROOT / "data" / "price_backfill_alpaca" / "us"
POOL_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
W_TP, W_SL, W_HOLD, W_FEE = TP, SL, HOLD_SESSIONS, FEE_US
BAND_LO_M, BAND_HI_M, MAX_FLOOR_ = 100.0, 500.0, 8.0


def _scan(dirpath, start, end):
    """→ uni[d] = [(dv20, dvol_sig, con)], wide[d] = [dict], losers[d] = n."""
    uni, wide, losers = defaultdict(list), defaultdict(list), defaultdict(int)
    for p in sorted(dirpath.glob("us_*.csv")):
        tk = p.stem[3:]
        b = load_csv_bars(p)
        for i in range(25, len(b) - W_HOLD):
            d, _o, _h, _l, c, v = b[i]
            if d < start or d > end:
                continue
            pc = b[i - 1][4]
            if pc <= 0 or c <= 0 or b[i + 1][1] <= 0:
                continue
            dsig = c * v / 1e6
            if dsig < 5:
                continue
            entry = b[i + 1][1]
            r = contract(entry, b[i + 1:i + 1 + W_HOLD], W_TP, W_SL, W_HOLD, True, W_FEE)
            if r is None:
                continue
            dv20 = st.mean(x[4] * x[5] for x in b[i - 19:i + 1]) / 1e6
            uni[d].append((dv20, dsig, r[0]))
            chg = (c / pc - 1) * 100
            if chg <= -5 and c >= 5:
                losers[d] += 1
                if dsig >= 15:
                    mx = max(100.0 * (b[j][4] / b[j - 1][4] - 1.0) for j in range(i - 20, i + 1)) if i >= 21 else None
                    wide[d].append({"tk": tk, "dsig": dsig, "dv20": dv20, "max21": mx, "net": r[0], "xi": r[1],
                                    "E": b[i + 1][0], "chg": chg})
    return uni, wide, losers


def _bench(uni, min_dsig):
    cuts, dec_mean = {}, {}
    for d, v in uni.items():
        u = sorted((x for x in v if x[1] >= min_dsig), key=lambda x: x[0])
        n = len(u)
        if n < 100:
            continue
        cuts[d] = [u[(k * n) // 10][0] for k in range(1, 10)]
        acc = defaultdict(list)
        for j, x in enumerate(u):
            acc[min(9, j * 10 // n)].append(x[2])
        dec_mean[d] = {k: (st.mean(a), len(a)) for k, a in acc.items()}
    return cuts, dec_mean


def _bench_of(d, c, bb):
    cuts, dm = bb
    if d not in cuts:
        return None
    m = dm[d].get(decile_of(cuts[d], c["dv20"]))
    return m[0] if m and m[1] >= 20 else None


def _nw_mean_t(y, lag=6):
    n = len(y)
    if n < 20:
        return None
    m = st.mean(y)
    e = [v - m for v in y]
    S = sum(x * x for x in e) + 2 * sum((1 - L / (lag + 1)) * sum(e[t] * e[t - L] for t in range(L, n))
                                        for L in range(1, lag + 1))
    return m / math.sqrt(S / n / n) if S > 0 else None


def wide_replay() -> int:
    iwm = bench_bars("us", "IWM")
    iwi = {b[0]: i for i, b in enumerate(iwm)}
    segs = {"A": _scan(ALPACA_DIR, "2023-01-03", "2025-04-30"),
            "B": _scan(US_DIR, "2025-05-19", "2026-12-31")}
    rows, stats = [], {}
    for seg, (uni, wide, losers) in segs.items():
        b50, b5 = _bench(uni, 50.0), _bench(uni, 5.0)
        stats[seg] = {"days": len(wide), "loser_gt100": sum(1 for n in losers.values() if n > 100),
                      "wide_per_day": st.mean(len(v) for v in wide.values()) if wide else 0}
        for d, cands in wide.items():
            passers = [c for c in cands if BAND_LO_M <= c["dsig"] < BAND_HI_M
                       and (c["max21"] is None or c["max21"] >= MAX_FLOOR_)]
            p0 = [c["net"] - x for c in cands for x in [_bench_of(d, c, b50)] if x is not None]
            p1 = [c["net"] - x for c in passers for x in [_bench_of(d, c, b50)] if x is not None]
            rec = {"seg": seg, "d": d, "p0": st.mean(p0) if p0 else None, "p1": st.mean(p1) if p1 else None,
                   "p0_raw": st.mean(c["net"] for c in cands), "n_wide": len(cands), "n_pass": len(passers)}
            if passers:
                top = max(passers, key=lambda c: c["dsig"])
                i = iwi.get(top["E"])
                rec.update(tk=top["tk"], E=top["E"], net=top["net"], sm50=_bench_of(d, top, b50),
                           sm5=_bench_of(d, top, b5),
                           iwm=((iwm[i + top["xi"]][4] / iwm[i][1] - 1) * 100
                                if i is not None and i + top["xi"] < len(iwm) else None))
            rows.append(rec)

    with (OUTDIR / "wide_replay_days.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        keys = ["seg", "d", "E", "tk", "net", "sm50", "sm5", "iwm", "p0", "p1", "p0_raw", "n_wide", "n_pass"]
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: r["d"]):
            w.writerow(r)

    # 재현 검증 — 실제 원장 us_wide_dvol 픽과 일치율(진입일 기준)
    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    real = dict(con.execute("SELECT session_date, ticker FROM trades WHERE strategy_id='us_wide_dvol'").fetchall())
    con.close()
    pc = sqlite3.connect(POOL_DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    pc.execute("PRAGMA busy_timeout=30000")
    pool_n = dict(pc.execute("SELECT session_date, SUM(eligible) FROM candidate_pool_all GROUP BY 1").fetchall())
    pc.close()
    rep = {r["E"]: r for r in rows if r["seg"] == "B" and r.get("E")}
    comp = [(d, tk, rep[d]["tk"]) for d, tk in real.items() if d in rep]
    match = sum(1 for _, a, b in comp if a == b)
    ecmp = [(pool_n[r["E"]], r["n_wide"]) for r in rows if r["seg"] == "B" and r.get("E") in pool_n]

    def stat(sub, key, base):
        pairs = [(r["d"], r[key] - (r[base] if base else 0)) for r in sub
                 if r.get(key) is not None and (base is None or r.get(base) is not None)]
        return day_stat(pairs, min_n=5)

    main_ = [r for r in rows if r.get("net") is not None and r["d"] <= "2025-12-31"]
    a_only = [r for r in main_ if r["seg"] == "A"]
    L = ["", "## 5. 추가 — `us_wide_dvol` 규칙 외부 재생 (2023~2025, 사전등록은 스크립트 docstring §5)", "",
         "규칙 = 등락 ≤−5%·$5 이상·거래대금 ≥$15M → 밴드 $100~500M·MAX21 ≥8 → 거래대금 1위. 계약 TP12/SL−25/D7/BE4, 비용 0.50%.",
         "**값은 신호일 평균 · 날짜 t.** ⚠️ 두 소스 모두 생존 종목 유니버스 → 결과는 **상한**.", "",
         "### 5a. 재현·커버리지 점검", "",
         f"- 원장 `us_wide_dvol` 진입일 {len(real)}개 중 재생 대조 가능 {len(comp)}개 → **픽 일치 {match}/{len(comp)}**"
         f" (대조 불가 {len(real) - len(comp)}일 = 미만기 창)",
         (f"- 적격(wide) 수: 실제 풀 평균 {st.mean(a for a, _ in ecmp):.1f} vs 재생 {st.mean(b for _, b in ecmp):.1f}/일 "
          f"({len(ecmp)}일 대조)") if ecmp else "- 적격 수 대조 불가",
         f"- 재생 신호일 A {stats['A']['days']} · B {stats['B']['days']}, 일평균 wide 적격 A {stats['A']['wide_per_day']:.1f} · "
         f"B {stats['B']['wide_per_day']:.1f}. −5% 이하·$5 이상이 100종목을 넘은 날(day_losers 컷 영향 가능) "
         f"A {stats['A']['loser_gt100']} · B {stats['B']['loser_gt100']}일", ""]

    def lines(name, sub):
        out = []
        for lab, key, base in (("**rank1 − 규모매칭($50M) = 판정**", "net", "sm50"), ("rank1 − 규모매칭($5M 유니버스)", "net", "sm5"),
                               ("rank1 − IWM(실청산)", "net", "iwm"), ("P1(밴드 통과 전량) − 규모매칭", "p1", None),
                               ("P0(wide 적격 전량) − 규모매칭", "p0", None), ("rank1 원 net", "net", None)):
            m, t, n = stat(sub, key, base)
            out.append(f"| {name} | {lab} | {n} | {fmt(m, t)} |")
        return out

    L += ["### 5b. 전체 창", "", "| 창 | 비교 | 일수 | 평균 · 날짜 t |", "|---|---|---:|---:|"]
    L += lines("2023-01~2025-12 (A+B)", main_)
    L += lines("A 단독 2023-01~2025-04", a_only)
    ser = [r["net"] - r["sm50"] for r in sorted(main_, key=lambda r: r["d"]) if r.get("sm50") is not None]
    ntm = _nw_mean_t(ser)
    sel = [(r["d"], (r["net"] - r["sm50"]) - r["p1"]) for r in main_ if r.get("sm50") is not None and r.get("p1") is not None]
    ms, ts, ns = day_stat(sel, 5)
    L += ["", f"- 보유 겹침 보정(NW lag 6) t, rank1 − 규모매칭($50M) 2023~2025: **{ntm:+.2f}**" if ntm is not None else "",
          f"- **선택 기여(rank1 − P1, 같은 날, 규모매칭 초과 기준)**: {fmt(ms, ts)} ({ns}일)", ""]

    L += ["### 5c. 연도·반기", "", "| 구간 | 일수 | rank1 − 규모매칭 | rank1 − IWM | P1 − 규모매칭 | rank1 원 net |",
          "|---|---:|---:|---:|---:|---:|"]
    years = sorted({r["d"][:4] for r in main_})
    yr_pos = 0
    for y in years:
        for lab, f_ in ((y, lambda r, y=y: r["d"][:4] == y),
                        (f"{y} H1", lambda r, y=y: r["d"][:4] == y and r["d"][5:7] <= "06"),
                        (f"{y} H2", lambda r, y=y: r["d"][:4] == y and r["d"][5:7] > "06")):
            sub = [r for r in main_ if f_(r)]
            if not sub:
                continue
            a, b, c, e = stat(sub, "net", "sm50"), stat(sub, "net", "iwm"), stat(sub, "p1", None), stat(sub, "net", None)
            if lab == y and (a[0] or 0) > 0:
                yr_pos += 1
            L.append(f"| {lab} | {a[2]} | {fmt(a[0], a[1])} | {fmt(b[0], b[1])} | {fmt(c[0], c[1])} | {fmt(e[0], e[1])} |")
    ref = [r for r in rows if r.get("net") is not None and r["d"] >= "2026-01-01"]
    if ref:
        a = stat(ref, "net", "sm50")
        L.append(f"| 참고: 2026-01~ (B, 판정 외) | {a[2]} | {fmt(a[0], a[1])} | - | - | - |")

    m, t, n = stat(main_, "net", "sm50")
    ok = (m or 0) > 0 and (t or 0) >= 2.0 and yr_pos >= 2
    ma, ta, na = stat(a_only, "net", "sm50")
    L += ["", "### 5d. 판정 (사전 고정: 평균 > 0 · 날짜 t ≥ 2.0 · 연도 ≥ 2/3 양수)", "",
          f"- 2023~2025: {fmt(m, t)} ({n}일), 양수 연도 {yr_pos}/{len(years)} → **{'후보' if ok else '기각'}**",
          f"- A 단독(밴드 발견 이전 데이터): {fmt(ma, ta)} ({na}일) → "
          f"{'문턱 통과' if (ma or 0) > 0 and (ta or 0) >= 2 else '문턱 미달'}", ""]
    with REPORT.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(wide_replay() if "--wide-replay" in sys.argv else main())
