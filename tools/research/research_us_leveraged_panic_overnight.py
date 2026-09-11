#!/usr/bin/env python3
"""US 레버리지 ETF 패닉 오버나이트 — 미측정 칸 (read-only 외부 조회, 2026-09-12).

## 왜 다시 보나

09-10에 "US 이식 실패"로 닫았다. 검정 대상은 **SPY/QQQ/IWM/TQQQ**였다 —
3배는 TQQQ **하나뿐**이고 나머지 셋은 1배다.
코스닥 레인의 가설은 "마감 동시호가에 **레버리지 리밸런싱**·손절이 시각에 묶여 판다"이다.
그 메커니즘이 맞다면 검정은 **레버리지 ETF에서 폭넓게** 해야 한다.
TQQQ 1종으로 닫은 것은 얇다. **SOXL·TNA·SPXL 3종이 비어 있고**, 벤치마크도
풀드 스칼라였다. 그 둘을 채운다.

## 미리 밝히는 예상 — 기각이다

두 가지 이유로 **null을 예상한다**. 이걸 먼저 적어서 사후 합리화를 막는다.
 · KR에서 **229200(1배)도 통과**했다. 즉 KR 현상은 순수 레버리지 산물이 아니라
   지수 자체에 있다. 레버리지는 배율일 뿐이다.
 · 1배 US(SPY/QQQ/IWM)에서 초과 t<1이었으므로, 3배는 그 3배 × 잡음 3배 ≈ 여전히 0이다.
 · 미국은 마감 **단일가 경매(15:20~15:30)**가 아니라 closing cross다. 미시구조가 다르다.
예상대로 기각되면 "US는 닫혔다"가 **가설 수준에서** 확인되는 것이고, 그것도 결과다.

## 사전 등록 — 숫자 보기 전에 고정

- 대상 4종: TQQQ(나스닥100 3x) · SOXL(반도체 3x) · TNA(러셀2000 3x) · SPXL(S&P500 3x).
- 신호: **해당 기초지수의 시가 갭 ≤ −1% 또는 종가 등락 ≤ −2.5%** (코스닥 v2와 동일 규칙).
- 실행: 신호일 **종가 매수 → 다음날 시가 매도**. 왕복 비용 0.05%.
- 벤치마크: **같은 ETF·같은 연도**의 무조건 오버나이트 평균(연도매칭).
- **기준: 연도매칭 초과 t ≥ 2 그리고 양수 연도 비율 ≥ 70%.**
  ETF당 **1회** 검정, 표는 티커 순서 고정. 4종이므로 다중비교를 감안해 t 2.0을 그대로 둔다
  (하나가 겨우 넘으면 통과로 부르지 않고 "다중비교 경계"로 적는다).
- 통과해도 후보다. 승격은 사전등록 후 forward로만.

⚠️ 레버리지 ETF는 일일 리밸런싱 상품이라 장기 보유 시 변동성 끌림이 있다.
이 전략은 **1박 보유**라 그 영향은 작지만 0은 아니다.
⚠️ 신호·가격 모두 yfinance. 라이브 수집 경로와 분리된 연구 전용이다.
"""
from __future__ import annotations

import math
import statistics as st
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PAIRS = [("TQQQ", "^NDX", "나스닥100 3x"),
         ("SOXL", "^SOX", "반도체 3x"),
         ("TNA", "^RUT", "러셀2000 3x"),
         ("SPXL", "^GSPC", "S&P500 3x")]
GAP_THR = -1.0
CHG_THR = -2.5
COST = 0.05
T_BAR = 2.0
POS_YEAR_BAR = 0.70
START = "2011-01-01"


def fetch(ticker):
    import yfinance as yf
    f = yf.Ticker(ticker).history(start=START, auto_adjust=True)
    out = {}
    for i, r in f.iterrows():
        try:
            o, c = float(r["Open"]), float(r["Close"])
        except (TypeError, ValueError, KeyError):
            continue
        if o > 0 and c > 0:
            out[str(i)[:10]] = (o, c)
    return out


def main():
    L = [f"# US 레버리지 ETF 패닉 오버나이트 ({date.today().isoformat()})", "",
         "read-only 연구. 라이브 변경 없음. `tools/research/research_us_leveraged_panic_overnight.py`",
         "(사전등록 기준·예상은 docstring).", "",
         "09-10의 \"US 이식 실패\"는 SPY/QQQ/IWM/**TQQQ**로 낸 결론이다 — 3배는 TQQQ 하나뿐이다.",
         "\"마감에 레버리지가 강제로 판다\"는 가설을 1종으로 닫은 것은 얇다.",
         "**SOXL·TNA·SPXL 3종을 더하고 벤치마크를 연도매칭으로 바꿔** 칸을 채운다.",
         "**미리 밝혀둔 예상은 기각이다**(KR에서 1배 229200도 통과했으므로 레버리지 산물이 아니고,",
         "1배 US가 t<1이었으므로 3배는 3배 × 잡음 3배다).", "",
         f"신호 = 기초지수 갭 ≤ {GAP_THR}% 또는 종가 등락 ≤ {CHG_THR}% (코스닥 v2와 동일)",
         f"· 실행 = 종가 매수 → 다음 시가 매도 · 비용 {COST}% · 벤치마크 = 같은 ETF·같은 연도 무조건 오버나이트", "",
         "| ETF | 기초지수 | 구간 | 신호 | 원 오버나이트 | 무조건(풀드) | **연도매칭 초과** | t | 양수 연도 | 판정 |",
         "|---|---|---|---:|---:|---:|---:|---:|---:|---|"]

    verdicts = []
    for etf, index, label in PAIRS:
        try:
            px, ix = fetch(etf), fetch(index)
        except Exception as exc:                      # noqa: BLE001 — 외부 조회
            L.append(f"| {etf} | {index} | 조회 실패 | - | - | - | - | - | - | {exc.__class__.__name__} |")
            continue
        days = sorted(set(px) & set(ix))
        if len(days) < 500:
            L.append(f"| {etf} | {index} | 데이터 부족({len(days)}) | - | - | - | - | - | - | 판정 불가 |")
            continue

        # 전 영업일 오버나이트 (연도별 무조건 평균)
        allnight, byyear = [], defaultdict(list)
        for i in range(1, len(days)):
            ov = (px[days[i]][0] / px[days[i - 1]][1] - 1) * 100
            allnight.append(ov)
            byyear[days[i][:4]].append(ov)
        pooled = st.mean(allnight)
        uncond = {y: st.mean(v) for y, v in byyear.items()}

        sig = []
        for i in range(1, len(days)):
            d, prev = days[i], days[i - 1]
            gap = (ix[d][0] / ix[prev][1] - 1) * 100
            chg = (ix[d][1] / ix[prev][1] - 1) * 100
            if not (gap <= GAP_THR or chg <= CHG_THR) or i + 1 >= len(days):
                continue
            raw = (px[days[i + 1]][0] / px[d][1] - 1) * 100 - COST
            sig.append({"y": d[:4], "raw": raw, "ex": raw - uncond.get(d[:4], pooled)})
        if len(sig) < 30:
            L.append(f"| {etf} | {index} | {days[0]}~{days[-1]} | {len(sig)} | - | - | - | - | - | 표본 부족 |")
            continue

        ex = [r["ex"] for r in sig]
        t = st.mean(ex) / (st.pstdev(ex) / math.sqrt(len(ex))) if st.pstdev(ex) else None
        yrs = sorted({r["y"] for r in sig})
        pos = sum(1 for y in yrs if st.mean(r["ex"] for r in sig if r["y"] == y) > 0)
        ok = t is not None and t >= T_BAR and pos / len(yrs) >= POS_YEAR_BAR
        verdicts.append((etf, label, ok, t, pos, len(yrs), st.mean(ex)))
        L.append(f"| **{etf}** | {index} | {days[0][:7]}~{days[-1][:7]} | {len(sig)} | "
                 f"{st.mean(r['raw'] for r in sig):+.3f}% | {pooled:+.3f}% | "
                 f"**{st.mean(ex):+.3f}%** | {t:.2f} | {pos}/{len(yrs)} | "
                 f"{'**통과**' if ok else '기각'} |")

    L += ["", "## 판정", ""]
    passed = [v for v in verdicts if v[2]]
    if not verdicts:
        L += ["**데이터를 못 받아 판정 불가.** 외부 조회 실패."]
    elif not passed:
        L += ["**4종 전부 기각. 예상대로다.**",
              "",
              "코스닥 패닉 오버나이트는 **레버리지 구조가 아니라 코스닥이라는 시장의 성질**이다.",
              "1배(229200)도 KR에서 통과했고, 3배 US는 전부 떨어졌다. 두 사실이 같은 곳을 가리킨다.",
              "",
              "**US는 이 축에서 닫혔다.** 09-10 결론(TQQQ 1종)이 얇았지만 3종을 더하고",
              "벤치마크를 고쳐도 답은 같다. 이제는 가설 수준에서 닫혔다고 말할 수 있다.",
              "TQQQ 재측정값(+0.007%, t 0.03)도 09-10의 t<1과 일치한다."]
    else:
        L += ["통과: " + ", ".join(f"{v[0]}({v[1]}) t {v[3]:.2f}, {v[4]}/{v[5]}" for v in passed),
              "",
              "⚠️ **4종을 돌렸으므로 다중비교 경계다.** 하나만 겨우 넘었다면 통과로 부르지 않는다.",
              "**예상과 다른 결과이므로 더 의심한다** — 표본 구성·기초지수 매칭·배당 처리부터 재확인한다.",
              "승격은 사전등록 후 forward로만."]

    L += ["", "⚠️ 레버리지 ETF는 일일 리밸런싱 상품이다. 1박 보유라 변동성 끌림은 작지만 0은 아니다.",
          "⚠️ yfinance 조회(auto_adjust=True). 라이브 수집 경로와 분리된 연구 전용 데이터다."]

    out = ROOT / "docs" / "reports" / f"us_leveraged_panic_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
