#!/usr/bin/env python3
"""Claude 직접 선별 시험 — 눈가리고 뽑고, 나중에 채점 (read-only, 2026-09-22).

운영자 지시: *"쉐도우 전략으로 매수난 애들을 API 말고 니가 직접 데이터를 받아서
클로드가 얼마나 좋은 애들을 선별할 수 있는지 봐봐."*

## 왜 이렇게 짜는가 — 내가 못 속이게

이 시험의 유일한 위험은 **내가 결과를 보고 고르는 것**이다. 그래서 두 단계로 자른다.

    dump  — 진입 시점 피처만 출력한다. **net_pct·exit_reason·결과는 절대 안 나간다.**
    score — 내가 저장한 픽 파일을 읽어 원장에서 채점한다. 픽 파일은 dump 뒤에 만들어야 한다.

픽 파일에는 `(날짜, 종목)`만 적는다. 채점은 스크립트가 한다.

## 사전 등록 — 무엇을 이기면 성공인가

대조군은 **그날 후보 전량**(조건 전량 매수 = 기저선)이다. "고르기가 값을 더하는가"가 질문이므로
고른 것과 안 고른 것이 아니라 **고른 것 vs 그날 전부**를 비교한다.

  · 지표: 같은 날 **지수 초과**(KR은 KOSPI200·KOSDAQ150 둘 다, 벤치 창은 실제 청산 시점까지)
  · 판정 단위: **날짜 클러스터**. 하루가 관측 1개다.
  · 통과선: **날짜 t ≥ 2.0** 그리고 **픽이 전량을 이긴 날이 절반 초과**.
  · 하루에 고르는 수: **3종목 고정**(사후에 안 바꾼다).

**이건 09-01에 기각된 '모델 선정'과 다른 시험이다.** 그때는 ML 모델이 밴드 위에서 rank1을
고른 것이었고, 여기서는 사람이 읽는 피처를 내가 직접 읽는다. 다만 **같은 결론이 나올 수 있고,
그러면 그게 답이다.** 분모(09-18 실측 96~301)에 1이 더해지는 시험이라는 것도 명시해 둔다.

## 피처 (전부 진입 시점에 확정된 값)

chg 전일등락 · gap 갭 · dvol 거래대금(M) · vol_spike 거래량배수 · ibs 종가위치
rv20 20일변동성 · mom20 20일모멘텀 · from_high20 20일고점대비 · ma20_disc MA20이격
cum5 5일누적 · min1_in5 5일최악하루 · ret60 60일수익 · max21 21일최대상승
hi_break_n 신고가돌파일 · down_streak 연속하락 · price 주가
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_benchmark_excess import bench_bars  # noqa: E402
from tools.virtual_books import (  # noqa: E402
    BE, HOLD_SESSIONS, SL, STRATEGIES, TP, bars_kr,
)

HOLDS = {s["id"]: int(s.get("hold", HOLD_SESSIONS)) for s in STRATEGIES}
VB = ROOT / "data" / "shadow" / "virtual_books.db"
KQ_CACHE = ROOT / "data" / "analysis" / "kr_index_etf_prices.csv"
POOL = "xkr_fallen3"          # 조건 전량 매수 모집단(= 대조군)
PICKS_PER_DAY = 3             # 사전 고정
CAND_PER_DAY = 20             # 하루에 보여줄 후보 수(원 풀은 수백이라 읽고 고를 수 없다)
SEED = 20260922               # 고정 시드 — 무작위 추출이라 **피처 편향이 안 들어간다**.
                              # 거래대금 상위 같은 걸로 자르면 그 피처가 결과에 섞인다.
SHOW = ["chg", "gap", "dvol", "vol_spike", "ibs", "rv20", "mom20", "from_high20",
        "ma20_disc", "cum5", "min1_in5", "ret60", "max21", "hi_break_n", "down_streak", "price"]


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def exit_bar(entry, win, hold):
    if not win or entry <= 0:
        return None
    for i, (_d, _o, hi, _lo, c, _v) in enumerate(win[:hold]):
        hip = (hi - entry) / entry * 100 if i > 0 else (c - entry) / entry * 100
        if hip >= TP or (c - entry) / entry * 100 <= SL:
            return i
    return hold - 1 if len(win) >= hold else None


def subsample(day, cands):
    """하루 후보를 CAND_PER_DAY로 줄인다. 날짜 시드 고정 무작위 — 결과와 무관하다."""
    if len(cands) <= CAND_PER_DAY:
        return cands
    import random
    rnd = random.Random(f"{SEED}:{day}")
    return sorted(rnd.sample(cands, CAND_PER_DAY), key=lambda c: c["ticker"])


def load(with_outcome: bool):
    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    rows = con.execute(
        "SELECT session_date, ticker, entry_price, net_pct, status, meta FROM trades "
        "WHERE strategy_id=? ORDER BY session_date, ticker", (POOL,)).fetchall()
    con.close()
    days = defaultdict(list)
    for d, tk, ep, net, status, meta in rows:
        ft = (json.loads(meta or "{}").get("feat") or {})
        if "rv20" not in ft:
            continue
        rec = {"ticker": tk, "entry": ep, "f": ft}
        if with_outcome:
            rec["net"] = net
            rec["closed"] = status == "CLOSED"
        days[d].append(rec)
    return days


def cmd_dump(argv):
    """진입 시점 피처만 출력. 결과는 절대 포함하지 않는다."""
    lo = argv[0] if argv else "2026-06-01"
    hi = argv[1] if len(argv) > 1 else "2026-09-22"
    maxday = int(argv[2]) if len(argv) > 2 else 40
    days = load(with_outcome=False)
    sel = [d for d in sorted(days) if lo <= d <= hi and len(days[d]) >= PICKS_PER_DAY + 2]
    # 균등 간격으로 뽑아 특정 국면 쏠림을 줄인다
    if len(sel) > maxday:
        step = len(sel) / maxday
        sel = [sel[int(i * step)] for i in range(maxday)]
    print(f"# 선별 시험 — 후보 {len(sel)}일 (결과 미포함) · 하루 최대 {CAND_PER_DAY}종목(시드 {SEED} 무작위)")
    print(f"# 풀 `{POOL}` · 하루 {PICKS_PER_DAY}종목 선택 · 피처는 전부 진입 시점 확정값")
    print("# " + " ".join(SHOW))
    for d in sel:
        cands = subsample(d, days[d])
        print(f"\n## {d}  (표시 {len(cands)} / 원 풀 {len(days[d])})")
        print("ticker  " + "".join(f"{k[:9]:>10}" for k in SHOW))
        for c in cands:
            line = f"{c['ticker']:8}"
            for k in SHOW:
                v = c["f"].get(k)
                line += f"{v:>10.2f}" if isinstance(v, (int, float)) else f"{'-':>10}"
            print(line)


def cmd_score(argv):
    """픽 파일을 읽어 채점. 픽 파일: 각 줄 `YYYY-MM-DD ticker ticker ticker`."""
    pf = Path(argv[0])
    picks = {}
    for line in pf.read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) >= 2 and p[0][:2] == "20":
            picks[p[0]] = p[1:]
    days = load(with_outcome=True)

    ks = bench_bars("kr", "069500")
    ksi = {b[0]: i for i, b in enumerate(ks)}
    rows_kq = sorted(csv.DictReader(KQ_CACHE.open(encoding="utf-8-sig")), key=lambda r: r["date"])
    kq = [(r["date"], f(r["229200_open"]), f(r["229200_close"])) for r in rows_kq]
    kq = [x for x in kq if x[1] and x[2]]
    kqi = {d: i for i, (d, _, _) in enumerate(kq)}
    hold = HOLDS.get(POOL, HOLD_SESSIONS)

    def excess(d, c):
        if not c["closed"] or c["net"] is None or not c["entry"]:
            return None
        bb = bars_kr(c["ticker"])
        j = next((i for i, b in enumerate(bb) if b[0] == d), None)
        ei = exit_bar(float(c["entry"]), bb[j:j + hold], hold) if j is not None else None
        if ei is None:
            return None
        out = {}
        i = ksi.get(d)
        if i is not None and i + ei < len(ks):
            out["KOSPI200"] = c["net"] - (ks[i + ei][4] / ks[i][1] - 1) * 100
        q = kqi.get(d)
        if q is not None and q + ei < len(kq):
            out["KOSDAQ150"] = c["net"] - (kq[q + ei][2] / kq[q][1] - 1) * 100
        return out

    per_day = []
    for d, tks in sorted(picks.items()):
        pool = subsample(d, days.get(d) or [])
        if not pool:
            continue
        pe, ae = defaultdict(list), defaultdict(list)
        for c in pool:
            ex = excess(d, c)
            if not ex:
                continue
            for k, v in ex.items():
                ae[k].append(v)
                if c["ticker"] in tks:
                    pe[k].append(v)
        if not ae:
            continue
        per_day.append({"d": d, "npool": len(pool), "npick": len(pe.get("KOSPI200", [])),
                        "pick": {k: st.mean(v) for k, v in pe.items() if v},
                        "all": {k: st.mean(v) for k, v in ae.items() if v}})

    print(f"# Claude 선별 시험 채점 — {len(per_day)}일\n")
    print("| 날짜 | 후보 | 픽 | 픽 초과(KS200) | 전량 초과(KS200) | 차 | 픽(KQ150) | 전량(KQ150) | 차 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in per_day:
        g1 = r["pick"].get("KOSPI200"), r["all"].get("KOSPI200")
        g2 = r["pick"].get("KOSDAQ150"), r["all"].get("KOSDAQ150")
        fm = lambda x: f"{x:+.2f}%" if x is not None else "-"     # noqa: E731
        d1 = (g1[0] - g1[1]) if None not in g1 else None
        d2 = (g2[0] - g2[1]) if None not in g2 else None
        print(f"| {r['d']} | {r['npool']} | {r['npick']} | {fm(g1[0])} | {fm(g1[1])} | "
              f"**{fm(d1)}** | {fm(g2[0])} | {fm(g2[1])} | **{fm(d2)}** |")

    print()
    for key in ("KOSPI200", "KOSDAQ150"):
        diffs = [r["pick"][key] - r["all"][key] for r in per_day
                 if key in r["pick"] and key in r["all"]]
        if len(diffs) < 3:
            continue
        m = st.mean(diffs)
        t = m / (st.stdev(diffs) / math.sqrt(len(diffs))) if st.stdev(diffs) else None
        win = sum(1 for x in diffs if x > 0)
        ok = (t is not None and t >= 2.0) and win > len(diffs) / 2
        print(f"**{key}** — {len(diffs)}일 · 픽−전량 평균 **{m:+.3f}%p** · "
              f"날짜 t **{t:+.2f}** · 이긴 날 **{win}/{len(diffs)}** → "
              f"{'**통과**' if ok else '**미달**'} (사전 문턱 t≥2.0 그리고 이긴 날 과반)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("dump", "score"):
        print(__doc__)
        print("사용: research_claude_selection_trial.py dump [lo] [hi] [일수]")
        print("      research_claude_selection_trial.py score <픽파일>")
        return 1
    return cmd_dump(sys.argv[2:]) or 0 if sys.argv[1] == "dump" else cmd_score(sys.argv[2:]) or 0


if __name__ == "__main__":
    raise SystemExit(main())
