#!/usr/bin/env python3
"""장기 종목 선택 — 골라서 오래 들고 있으면 지수/전체보다 나은가 (read-only, 2026-09-24).

운영자 질문: "1은 장기 선택 종목 방식" — 지수 대신 종목을 골라 장기 보유.

## 사전 등록(실행 전 고정)
- 데이터: `state/tsmom_cache/px_{US,KR}.pkl`(07-07 수집, 종가, 2015-06 ~ 2026-07).
  US = 현재 S&P500 501종목 · KR = 현재 거래대금 상위 247종목. **생존편향 있음**(상폐·탈락 종목 없음).
- 리밸런스: 반기(1월·7월 첫 거래일), 20종목 등가중, 사이는 방치.
- 적격: 리밸런스일 기준 이력 ≥ 252거래일(그 시점까지 정보만).
- 규칙 3종(가격만 — 재무 데이터 없음):
  ① 모멘텀 12-1(최근 1개월 제외 12개월 수익) 상위 20
  ② 저변동(252일 일간 변동성) 하위 20
  ③ 결합(두 순위 평균) 상위 20
- 대조: **같은 목록 전 종목 등가중(EW, 반기 리밸런스)** ← 판정 기준. 생존편향이 양쪽에 같이 걸린다.
  무작위 20종목(시드 50개 평균) — "20개로 줄인 효과"만 분리. 지수(069500 / SPY)는 참고.
- 비용: 거래 비중 1당 편도 KR 0.15% · US 0.25%.
- 판정: 반기 단위 EW 대비 초과의 평균·t(반기 = 독립 표본 단위), 전반(2016~2020)·후반(2021~2026) 부호.
  **t ≥ 2 이고 전·후반 부호 모두 양수**면 "후보", 아니면 "기각".
"""
from __future__ import annotations

import csv
import math
import pickle
import random
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "state" / "tsmom_cache"
IDX = {"KR": ROOT / "data" / "analysis" / "voltarget_20260924" / "069500.KS.csv",
       "US": ROOT / "data" / "analysis" / "voltarget_20260924" / "SPY.csv"}
COST = {"KR": 0.15, "US": 0.25}
N, HIST = 20, 252
OUT = ROOT / "docs" / "reports" / "longterm_selection_20260924.md"


def load_idx(p):
    out = {}
    with p.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out[r[next(iter(r))][:10]] = float(r["Close"])
            except (TypeError, ValueError):
                pass
    return out


def main():
    L = ["# 장기 종목 선택 — 골라서 오래 들고 있기 (2016 ~ 2026-07)", "",
         "read-only. `tools/research/research_longterm_selection_20260924.py`(규칙·판정은 docstring에 실행 전 고정).",
         "**판정 기준은 지수가 아니라 같은 종목 목록 전체 등가중(EW)** — 생존편향 상쇄용.", ""]
    for mk in ("KR", "US"):
        px = pickle.load(open(CACHE / f"px_{mk}.pkl", "rb"))
        px = {t: dict(v) for t, v in px.items() if isinstance(v, list) and not t.startswith("^")}
        dates = sorted({d for v in px.values() for d in v})
        pos = {t: sorted(v) for t, v in px.items()}
        # 반기 리밸런스일
        rebs, seen = [], set()
        for d in dates:
            key = (d[:4], "H1" if d[5:7] <= "06" else "H2")
            if key not in seen and d >= "2016-01-01" and d[5:7] in ("01", "07"):
                seen.add(key)
                rebs.append(d)
        rebs.append(dates[-1])

        def price(t, d):
            return px[t].get(d)

        def feats(d):
            out = {}
            for t, ds in pos.items():
                hist = [x for x in ds if x <= d]
                if len(hist) < HIST + 1 or hist[-1] != d:
                    continue
                c = [px[t][x] for x in hist[-(HIST + 1):]]
                rets = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
                mom = c[-22] / c[0] - 1
                out[t] = (mom, st.pstdev(rets))
            return out

        periods = []   # (시작, 끝, {방식: 기간수익})
        prev_hold = {k: set() for k in ("mom", "lowvol", "combo", "ew")}
        for a, b in zip(rebs[:-1], rebs[1:]):
            f = feats(a)
            if len(f) < 60:
                continue
            ts = list(f)
            mom_rank = {t: r for r, t in enumerate(sorted(ts, key=lambda t: -f[t][0]))}
            vol_rank = {t: r for r, t in enumerate(sorted(ts, key=lambda t: f[t][1]))}
            picks = {"mom": sorted(ts, key=lambda t: mom_rank[t])[:N],
                     "lowvol": sorted(ts, key=lambda t: vol_rank[t])[:N],
                     "combo": sorted(ts, key=lambda t: mom_rank[t] + vol_rank[t])[:N],
                     "ew": ts}

            def ret_of(names):
                rs = []
                for t in names:
                    p0 = price(t, a)
                    later = [x for x in pos[t] if x <= b]
                    p1 = px[t][later[-1]] if later else None
                    if p0 and p1:
                        rs.append(p1 / p0 - 1)
                return st.mean(rs) if rs else 0.0

            res = {}
            for k, names in picks.items():
                gross = ret_of(names)
                turnover = 1.0 if not prev_hold[k] else len(set(names) - prev_hold[k]) / max(1, len(names))
                res[k] = gross - 2 * turnover * COST[mk] / 100
                prev_hold[k] = set(names)
            rnd = []
            for seed in range(50):
                random.seed(seed * 7919 + len(periods))
                rnd.append(ret_of(random.sample(ts, N)) - 2 * COST[mk] / 100)
            res["random"] = st.mean(rnd)
            idx = load_idx(IDX[mk])
            ik = sorted(idx)
            i0 = max(x for x in ik if x <= a)
            i1 = max(x for x in ik if x <= b)
            res["index"] = idx[i1] / idx[i0] - 1
            periods.append((a, b, res))

        def cagr(key):
            nav = math.prod(1 + r[key] for _, _, r in periods)
            yrs = (len(periods)) / 2
            return (nav ** (1 / yrs) - 1) * 100, nav

        L += [f"## {mk} — 반기 {len(periods)}개 ({periods[0][0]} ~ {periods[-1][1]})", "",
              "| 방식 | CAGR | 누적 배수 | **EW 대비 반기 초과 평균** | t | 이긴 반기 | 전반 초과 | 후반 초과 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        half = len(periods) // 2
        for key, lab in (("mom", "① 모멘텀 20"), ("lowvol", "② 저변동 20"), ("combo", "③ 결합 20"),
                         ("random", "무작위 20(대조)"), ("ew", "전체 등가중(EW, 기준)"), ("index", "지수(참고)")):
            c, nav = cagr(key)
            ex = [r[key] - r["ew"] for _, _, r in periods]
            m = st.mean(ex) * 100
            t = (st.mean(ex) / (st.stdev(ex) / math.sqrt(len(ex)))) if st.stdev(ex) > 0 else 0
            h1, h2 = st.mean(ex[:half]) * 100, st.mean(ex[half:]) * 100
            win = sum(1 for x in ex if x > 0)
            mark = ""
            if key in ("mom", "lowvol", "combo"):
                mark = " → **후보**" if (t >= 2 and h1 > 0 and h2 > 0) else " → 기각"
            L.append(f"| {lab}{mark} | {c:+.1f}% | {nav:.2f}배 | {m:+.2f}%p | {t:+.2f} | {win}/{len(ex)} | "
                     f"{h1:+.2f}%p | {h2:+.2f}%p |")
        L.append("")
    L += ["## 읽는 법", "",
          "- CAGR 절대치는 생존편향으로 부풀려져 있다(살아남은 종목만). **EW 대비 초과만 판정에 쓴다.**",
          "- 무작위 20이 EW와 비슷하면 '20개로 줄이는 것' 자체엔 값이 없다는 뜻이다.",
          "- 반기 표본 ~21개라 검정력이 낮다. 기각은 '효과 없음'이 아니라 '이 표본으로 구분 안 됨'이다."]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
