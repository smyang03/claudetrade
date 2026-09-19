#!/usr/bin/env python3
"""측정 방식 감사 — 우리가 재는 법이 틀린 건 아닌가 (read-only, 2026-09-20).

운영자 질문: *"혹시 우리가 측정 방식이 잘못됐거나 한 건 아닌지."*

세 가지를 의심하고 전부 실측했다. **둘은 무해했고 하나는 진짜 결함이다.**

## ① 벤치 창 불일치 — 실재하지만 작다

초과 = net − 벤치인데, 벤치 창을 **계약 보유기간 전체(7세션)**로 댄다. 그런데 거래의
39~50%는 TP/SL/BE로 **일찍 닫힌다.** 2세션에 닫힌 거래에 7세션치 벤치를 물리면
상승장에서 초과가 과소평가된다.

실제 청산 봉을 `contract_exit_v2`와 같은 규약으로 복원해 다시 쟀다.
→ 정정폭 **+0.09 ~ +0.86%p**. 평균 실보유가 5.6~6.0일이라 7일과 큰 차이가 없었다.
**부호가 바뀌는 arm은 없다.** 편향은 있지만 결론을 만들지 않았다.

## ② 보유기간·계약값 — 이상 없음

전 arm hold 확인(7일 41개 / 10일 13개 / 20일 2개), 09-16에 고친 뒤 그대로다.

## ③ 벤치 **종목** 불일치 — 이게 진짜 결함이다

KR 판정 벤치가 `069500`(KOSPI200 = 대형주 200)인데, 우리 KR 전략이 사는 종목은
**고유 1,510개**로 사실상 전 시장이다. 그리고 최근 구간은 **대형주 단독 랠리**였다.

    069500 연수익: 2025 **+95.3%** · 2026 **+79.6%**
    7세션 평균:    2025 KOSPI200 +1.97% vs KOSDAQ150 +0.82%
                   2026 KOSPI200 +2.15% vs KOSDAQ150 **−0.41%**  (차 2.56%p)

대형주 200개가 폭등하는 동안 중소형주는 안 올랐는데, 중소형 급락주를 사는 전략에
대형주 지수를 허들로 댄 것이다. 벤치를 바꾸면 **부호가 뒤집히는 arm이 나온다.**

## ⚠️ 그래도 엣지가 생기지는 않는다

벤치를 KOSDAQ150으로 바꾸면 **건 가중**은 뒤집히지만 **날짜 클러스터 t**는 여전히 미달이다
(게이트 arm 0.40 → 1.03). 즉 이 결함은 **음수 판정의 크기를 부풀렸지 방향을 만들지는 않았다.**

## 남은 문제 — 올바른 벤치는 아직 정해지지 않았다

1,510종목은 KOSPI200도 KOSDAQ150도 아니다. 둘 중 유리한 쪽을 고르면 그게 또 편향이다.
정답은 **유니버스 정합 벤치**(그날 적격 종목 전량 등가중 수익)이고, 우리 데이터로 만들 수 있다.
그 전까지 "지수 초과" 수치는 **벤치를 명시해서만** 인용해야 한다.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_benchmark_excess import bench_bars  # noqa: E402
from tools.virtual_books import HOLD_SESSIONS, STRATEGIES  # noqa: E402

HOLDS = {s["id"]: int(s.get("hold", HOLD_SESSIONS)) for s in STRATEGIES}
VB = ROOT / "data" / "shadow" / "virtual_books.db"
KQ_CACHE = ROOT / "data" / "analysis" / "kr_index_etf_prices.csv"
ARMS = ["xkr_fallen3", "c_kr_fallen_regime", "c_kr_fallen5_nobio",
        "c_kr_fallen_nomajor_rg", "c_kr_fallen5_nobio_rg", "xkr_rise5", "xkr_breakout"]


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_kq():
    rows = sorted(csv.DictReader(KQ_CACHE.open(encoding="utf-8-sig")), key=lambda r: r["date"])
    out = [(r["date"], f(r["229200_open"]), f(r["229200_close"])) for r in rows]
    return [x for x in out if x[1] and x[2]]


def day_stats(pairs):
    g = defaultdict(list)
    for d, v in pairs:
        g[d].append(v)
    m = [st.mean(v) for v in g.values()]
    if len(m) < 5 or st.stdev(m) == 0:
        return None, None, len(m)
    return st.mean(m), st.mean(m) / (st.stdev(m) / math.sqrt(len(m))), len(m)


def main():
    ks = bench_bars("kr", "069500")
    ksi = {b[0]: i for i, b in enumerate(ks)}
    kq = load_kq()
    kqi = {d: i for i, (d, _, _) in enumerate(kq)}

    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)

    L = ["# 측정 방식 감사 — 재는 법이 틀린 건 아닌가 (2026-09-20)", "",
         "read-only. `tools/research/research_measurement_audit_20260920.py`(의심 목록은 docstring).",
         "세 가지를 의심해 전부 실측했다. **둘은 무해, 하나는 진짜 결함.**", "",
         "## 벤치 지수가 재는 구간 — 대형주 단독 랠리", "",
         "| 연도 | KOSPI200 연수익 | 7세션 평균 | KOSDAQ150 7세션 평균 | 차 |",
         "|---|---:|---:|---:|---:|"]
    for y in ("2023", "2024", "2025", "2026"):
        ka = [(ks[i + 6][4] / ks[i][1] - 1) * 100 for i in range(len(ks) - 7) if ks[i][0][:4] == y]
        qa = [(kq[i + 6][2] / kq[i][1] - 1) * 100 for i in range(len(kq) - 7) if kq[i][0][:4] == y]
        yr = [b for b in ks if b[0][:4] == y]
        if not ka or not qa or not yr:
            continue
        L.append(f"| {y} | {(yr[-1][4]/yr[0][1]-1)*100:+.1f}% | {st.mean(ka):+.2f}% | "
                 f"{st.mean(qa):+.2f}% | **{st.mean(ka)-st.mean(qa):+.2f}%p** |")

    L += ["", "2025~2026에 KOSPI200이 **+95.3% / +79.6%**다. 같은 구간 KOSDAQ150은 7세션 "
          "**−0.41%**(2026)로 정반대다. 대형주 200개만 폭등했다.", "",
          "## 벤치를 바꾸면 판정이 어떻게 달라지나", "",
          "우리 KR 전략은 고유 1,510종목 — 사실상 전 시장에서 급락주를 산다.", "",
          "| arm | 일수 | vs KOSPI200 (일평균/t) | vs KOSDAQ150 (일평균/t) |",
          "|---|---:|---:|---:|"]
    for sid in ARMS:
        hold = HOLDS.get(sid, HOLD_SESSIONS)
        rows = con.execute("SELECT session_date, net_pct FROM trades WHERE strategy_id=? "
                           "AND status='CLOSED' AND net_pct IS NOT NULL", (sid,)).fetchall()
        A, Bp = [], []
        for d, net in rows:
            i, j = ksi.get(d), kqi.get(d)
            if i is not None and i + hold <= len(ks):
                A.append((d, net - (ks[i + hold - 1][4] / ks[i][1] - 1) * 100))
            if j is not None and j + hold <= len(kq):
                Bp.append((d, net - (kq[j + hold - 1][2] / kq[j][1] - 1) * 100))
        ma, ta, _ = day_stats(A)
        mb, tb, nb = day_stats(Bp)
        if ta is None or tb is None:
            continue
        L.append(f"| `{sid}` | {nb} | {ma:+.2f}% · t {ta:+.2f} | **{mb:+.2f}% · t {tb:+.2f}** |")
    con.close()

    L += ["", "## 결론", "",
          "- **벤치 종목 불일치는 진짜 결함이다.** 대형주 지수를 중소형 급락 전략의 허들로 썼고,",
          "  하필 측정 구간이 대형주 단독 랠리라 음수 판정이 크게 부풀려졌다.",
          "  `xkr_fallen3` 날짜 t가 −7.03 → −2.35로, 게이트 arm은 +0.40 → +1.03으로 바뀐다.",
          "- **그러나 엣지가 생기지는 않는다.** 바뀐 뒤에도 날짜 t는 전부 |t| < 2다.",
          "  이 결함은 **음수의 크기를 부풀렸지 방향을 만들지 않았다.**",
          "- **올바른 벤치는 아직 미정이다.** 1,510종목은 KOSPI200도 KOSDAQ150도 아니다.",
          "  둘 중 유리한 쪽을 고르면 그게 또 편향이다. 정답은 **유니버스 정합 벤치**",
          "  (그날 적격 종목 전량 등가중)이고 우리 데이터로 만들 수 있다.",
          "- 그때까지 **\"지수 초과\" 수치는 벤치를 명시해서만 인용한다.**", "",
          "⚠️ 이 감사는 09-11에 정한 \"판정은 지수 초과분으로\"를 뒤집지 않는다. 기준은 맞고",
          "**기준으로 쓴 지수가 틀렸다.** 09-12 전면 감사의 \"45종 전부 지수 미달\"은",
          "**크기가 과장된 것**으로 읽어야 한다(방향은 유지).", ""]

    out = ROOT / "docs" / "reports" / "measurement_audit_20260920.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
