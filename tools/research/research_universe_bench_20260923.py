#!/usr/bin/env python3
"""유니버스 정합 벤치 — KR arm을 '그날 살 수 있었던 전 종목'과 비교한다 (read-only, 2026-09-23).

09-20 측정 감사가 남긴 숙제: KOSPI200도 KOSDAQ150도 우리 1,510종목 유니버스가 아니다.
둘 중 유리한 쪽을 고르면 편향이다. 정답은 **그날 적격 종목 전량 등가중**이다.

## 벤치 4종 (전부 신호일 D 정보만으로 적격 판정 — lookahead 없음)

적격 = D에 봉이 있고, 이력 ≥25봉, 거래대금(종가×거래량) ≥20억, 종가 ≥1,000원.
이것은 `discovery_pools.pool_pass`의 KR 유동성 문턱과 같다 — 우리 arm이 뽑히는 모집단이다.

1. **EW 보유** — 적격 전 종목을 D+1 시가에 사서 hold세션째 종가에 판다(수수료 없음, 지수와 같은 취급).
2. **EW 계약** — 적격 전 종목에 **같은 계약**(TP/SL/보유, 수수료 0.25%)을 씌운다.
   arm − 이 값 = **"고르기"가 더한 값**만 남는다(계약 출구 모양 효과가 상쇄된다).
3. **KOSPI200/KOSDAQ150 정렬** — D+1 시가 → D+hold 종가. 트레이드 창과 같은 날짜.
4. **KOSPI200 기존식** — D 시가 → D+hold−1 종가. 09-20 감사·성적표가 쓴 창. 비교용으로만.

## ⚠️ 발견: 기존 KR 벤치 창이 하루 앞당겨져 있다

KR `trades.session_date`는 **신호일**이고 진입은 **다음 세션 시가**다(`entry_of` 규약, 원장 실측 확인).
그런데 `research_measurement_audit_20260920.py`와 `research_shadow_scoreboard.py`는 벤치를
`ks[i]`(= 신호일 시가)에서 시작한다. 트레이드가 보유하지 않은 **신호일 하루**가 벤치에 들어가고
마지막 날이 빠진다. 급락 arm의 신호일은 시장도 빠진 날이 많아 벤치가 낮게 잡힌다 → 초과가 부풀 수 있다.
이 스크립트는 두 창을 나란히 내서 크기를 잰다.
→ **실측 결과 무해: 정렬 − 기존 = +0.01%p (t +0.05, 303일).** 창 어긋남은 판정에 영향 없음.

5. **규모매칭 계약** — 그날 적격 유니버스를 dvol20 십분위로 나눠 **같은 십분위** 종목에 같은 계약.
   대형주 쏠림 arm(`c_kr_insider_k1` = 삼성전자 132/315건)이 EW 벤치에서 부풀려지는 걸 막는다.
   ⚠️ 최상위 십분위 안에서도 삼성전자·하이닉스 단독 랠리는 못 걷어낸다 — k1은 k1_ex와 같이 읽는다.

## 한계

- 가격 캐시 `data/price/kr`는 1,800종목이다. 상장폐지 종목이 빠졌을 수 있다(생존편향 → 벤치가 위로).
  arm도 같은 캐시에서 뽑히므로 **양쪽에 같은 방향**으로 걸린다.
- 판정은 날짜 클러스터 t. 백필(인샘플)과 forward를 분리한다.
"""
from __future__ import annotations

import csv
import math
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
from tools.virtual_books import (FEE_KR, HOLD_SESSIONS, SL, STRATEGIES, TP,  # noqa: E402
                                 contract_exit_v2, strategy_market)

VB = ROOT / "data" / "shadow" / "virtual_books.db"
KR_DIR = ROOT / "data" / "price" / "kr"
KQ_CACHE = ROOT / "data" / "analysis" / "kr_index_etf_prices.csv"
FORWARD_START = "2026-09-01"
MIN_HISTORY, DVOL_MIN_EOK, PRICE_MIN = 25, 20.0, 1000.0
N_BUCKET = 10
OUT = ROOT / "docs" / "reports" / "universe_bench_20260923.md"


def load_bars(path: Path) -> list[tuple]:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.reader(fh):
            if len(r) >= 6 and r[0][:2] == "20":
                try:
                    rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                except ValueError:
                    continue
    return sorted(rows)


def load_kq() -> list[tuple]:
    out = []
    for r in sorted(csv.DictReader(KQ_CACHE.open(encoding="utf-8-sig")), key=lambda r: r["date"]):
        try:
            o, c = float(r["229200_open"]), float(r["229200_close"])
        except (TypeError, ValueError, KeyError):
            continue
        if o > 0 and c > 0:
            out.append((r["date"], o, c))
    return out


def day_t(pairs):
    g = defaultdict(list)
    for d, v in pairs:
        g[d].append(v)
    m = [st.mean(v) for v in g.values()]
    if len(m) < 5 or st.stdev(m) == 0:
        return None, None, len(m)
    return st.mean(m), st.mean(m) / (st.stdev(m) / math.sqrt(len(m))), len(m)


def main() -> int:
    arms = {s["id"]: s for s in STRATEGIES if strategy_market(s) == "KR"}
    contracts = {(int(s.get("hold", HOLD_SESSIONS)), float(s.get("tp", TP)), float(s.get("sl", SL)))
                 for s in arms.values()}
    holds = sorted({h for h, _, _ in contracts})

    # ── 유니버스 벤치: 날짜별 적격 종목 레코드 (ticker, dvol20, {hold: 보유수익}, {계약: 계약수익})
    recs_by_date = defaultdict(list)
    files = sorted(KR_DIR.glob("kr_*.csv"))
    for p in files:
        t = p.stem[3:]
        b = load_bars(p)
        for i in range(MIN_HISTORY, len(b) - 1):
            d, _o, _h, _l, c, v = b[i]
            if c < PRICE_MIN or c * v / 1e8 < DVOL_MIN_EOK or b[i + 1][1] <= 0:
                continue
            entry = b[i + 1][1]
            dv20 = st.mean(x[4] * x[5] for x in b[i - 19: i + 1]) / 1e8
            hr = {h: (b[i + h][4] / entry - 1) * 100 for h in holds if i + h < len(b)}
            cr = {}
            for h, tp, sl in contracts:
                r_ = contract_exit_v2(entry, b[i + 1: i + 1 + h], fee=FEE_KR, be_lock=False, tp=tp, sl=sl, hold=h)
                if r_ is not None:
                    cr[(h, tp, sl)] = r_[0]
            recs_by_date[d].append((t, dv20, hr, cr))

    uni_n = {d: len(v) for d, v in recs_by_date.items()}
    ew_hold, ew_con = {}, {}
    size_dec = {}                  # (d, ticker) -> 규모 십분위(dvol20, 그날 적격 유니버스 안에서)
    sz_con = defaultdict(lambda: [0.0, 0])   # (d, 십분위, 계약)
    for d, v in recs_by_date.items():
        for h in holds:
            xs = [r[2][h] for r in v if h in r[2]]
            if xs:
                ew_hold[(d, h)] = [sum(xs), len(xs)]
        for k in contracts:
            xs = [r[3][k] for r in v if k in r[3]]
            if xs:
                ew_con[(d, *k)] = [sum(xs), len(xs)]
        order = sorted(v, key=lambda r: r[1])
        for j, r in enumerate(order):
            dec = min(N_BUCKET - 1, j * N_BUCKET // len(order))
            size_dec[(d, r[0])] = dec
            for k, val in r[3].items():
                a = sz_con[(d, dec, *k)]
                a[0] += val
                a[1] += 1

    ks = bench_bars("kr", "069500")
    ksi = {x[0]: i for i, x in enumerate(ks)}
    kq = load_kq()
    kqi = {x[0]: i for i, x in enumerate(kq)}

    def idx_old(d, h):
        i = ksi.get(d)
        return (ks[i + h - 1][4] / ks[i][1] - 1) * 100 if i is not None and i + h - 1 < len(ks) else None

    def idx_k200(d, h):
        i = ksi.get(d)
        return (ks[i + h][4] / ks[i + 1][1] - 1) * 100 if i is not None and i + h < len(ks) else None

    def idx_kq(d, h):
        i = kqi.get(d)
        return (kq[i + h][2] / kq[i + 1][1] - 1) * 100 if i is not None and i + h < len(kq) else None

    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    rows = con.execute("SELECT strategy_id, session_date, net_pct, backfill, ticker FROM trades "
                       "WHERE status='CLOSED' AND net_pct IS NOT NULL").fetchall()
    con.close()

    cols = ["old", "k200", "kq", "ewh", "ewc", "szc"]
    res = defaultdict(lambda: {c: [] for c in cols + ["raw"]})
    for sid, d, net, bf, tk in rows:
        s = arms.get(sid)
        if s is None:
            continue
        h, tp, sl = int(s.get("hold", HOLD_SESSIONS)), float(s.get("tp", TP)), float(s.get("sl", SL))
        eh, ec = ew_hold.get((d, h)), ew_con.get((d, h, tp, sl))
        dec = size_dec.get((d, str(tk)))
        sc = sz_con.get((d, dec, h, tp, sl)) if dec is not None else None
        # 공통 표본: 다섯 벤치가 전부 있는 행만 — 벤치마다 표본이 다르면 비교가 아니다
        vals = {"old": idx_old(d, h), "k200": idx_k200(d, h), "kq": idx_kq(d, h),
                "ewh": eh[0] / eh[1] if eh and eh[1] >= 50 else None,
                "ewc": ec[0] / ec[1] if ec and ec[1] >= 50 else None,
                "szc": sc[0] / sc[1] if sc and sc[1] >= 20 else None}
        if any(v is None for v in vals.values()):
            continue
        seg = "fwd" if d >= FORWARD_START else "bf"
        for key in (seg, "all"):
            r = res[(sid, key)]
            r["raw"].append((d, net))
            for c in cols:
                r[c].append((d, net - vals[c]))

    def kind(sid):
        return "기저선" if sid.startswith("xkr_") else ("후보" if sid.startswith("c_") else "실행변형")

    L = ["# 유니버스 정합 벤치 — KR arm을 '그날 살 수 있던 전 종목'과 비교 (2026-09-23)", "",
         "read-only. `tools/research/research_universe_bench_20260923.py`(규약·한계는 docstring).",
         f"가격 캐시 {len(files):,}종목 · 적격 = 거래대금 ≥20억·종가 ≥1,000원·이력 ≥25봉(풀 유동성 문턱과 동일).",
         "**값은 전부 날짜 클러스터 일평균 초과 · t.** 다섯 벤치가 모두 있는 공통 행만 쓴다.", "",
         "## 0. 벤치 자체 — 적격 유니버스 크기와 보유 수익", ""]
    ds = sorted(d for d in uni_n if (d, HOLD_SESSIONS) in ew_hold)
    by_year = defaultdict(list)
    for d in ds:
        a = ew_hold[(d, HOLD_SESSIONS)]
        if a[1] >= 50:
            by_year[d[:4]].append((uni_n[d], a[0] / a[1], idx_k200(d, HOLD_SESSIONS), idx_kq(d, HOLD_SESSIONS)))
    L += ["| 연도 | 신호일 | 적격 종목(평균) | EW 7세션 | KOSPI200 7세션 | KOSDAQ150 7세션 |",
          "|---|---:|---:|---:|---:|---:|"]
    for y, v in sorted(by_year.items()):
        k2 = [x[2] for x in v if x[2] is not None]
        kq_ = [x[3] for x in v if x[3] is not None]
        L.append(f"| {y} | {len(v)} | {st.mean(x[0] for x in v):,.0f} | {st.mean(x[1] for x in v):+.2f}% | "
                 f"{st.mean(k2):+.2f}% | {st.mean(kq_):+.2f}% |" if k2 and kq_ else
                 f"| {y} | {len(v)} | {st.mean(x[0] for x in v):,.0f} | {st.mean(x[1] for x in v):+.2f}% | - | - |")

    for seg, title in (("bf", "백필(인샘플, 2026-09-01 이전)"), ("fwd", "forward(2026-09-01 이후)")):
        L += ["", f"## {'1' if seg == 'bf' else '2'}. {title}", "",
              "| arm | 구분 | 일수 | 원 net | vs KOSPI200 기존창 | vs KOSPI200 정렬 | vs KOSDAQ150 정렬 | "
              "**vs EW 보유** | **vs EW 계약** | **vs 규모매칭 계약** |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        recs = []
        for (sid, key), r in res.items():
            if key != seg:
                continue
            m_raw, _, n = day_t(r["raw"])
            if m_raw is None:
                continue
            cells = {c: day_t(r[c]) for c in cols}
            recs.append((cells["szc"][1] or -99, sid, n, m_raw, cells))
        for _, sid, n, m_raw, cells in sorted(recs, reverse=True):
            fmt = lambda c: f"{cells[c][0]:+.2f}% · t {cells[c][1]:+.2f}"  # noqa: E731
            L.append(f"| `{sid}` | {kind(sid)} | {n} | {m_raw:+.2f}% | {fmt('old')} | {fmt('k200')} | "
                     f"{fmt('kq')} | **{fmt('ewh')}** | **{fmt('ewc')}** | **{fmt('szc')}** |")

    # 창 어긋남 크기 — 전 arm 공통 행의 일평균 차
    diffs = [(d, a - b) for (sid, key), r in res.items() if key == "all"
             for (d, a), (_, b) in zip(r["k200"], r["old"])]
    md, td, nd = day_t(diffs)
    L += ["", "## 3. 기존 KR 벤치 창의 1일 어긋남 — 크기", "",
          f"정렬 창 초과 − 기존 창 초과(전 KR arm 공통 행, 날짜 클러스터): **{md:+.2f}%p · t {td:+.2f}** ({nd}일)" if md is not None else "산출 불가",
          "", "음수면 기존 창이 초과를 **부풀렸다**는 뜻이다(신호일 시장 하락이 벤치에 들어가 벤치가 낮았다)."]

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
