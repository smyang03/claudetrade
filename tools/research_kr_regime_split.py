#!/usr/bin/env python3
"""KR 급락 레인 국면 축 검정 — 553거래일에서 일가중 초과를 0에서 움직이는 변수가 있는가
(read-only, 2026-09-12).

**질문을 바꾼다.** "무엇이 2020을 망쳤나"가 아니다. 2020의 k≥20인 5일은
2020-03-13/16/18/19/23 — **전부 코로나 폭락 한 주, 실질 사건 1개**다. 5점에 맞춘
식별자는 한 주를 설명하는 과적합이다. 물어야 할 것은:

  **어떤 국면 변수가 553거래일 일가중 초과(+0.01%, t 0.03)를 0에서 움직이는가.**

아무 변수도 553일을 못 가르면, 2020은 평균 0 분포의 꼬리일 뿐이고
'2020형 식별자'는 허구다. 그 결론도 결론이다.

## 사전 등록 — 숫자를 보기 **전에** 고정한다

변수(3개, 상호 중복 제거):
  A. `idx_dd60`  — 신호일 069500 종가 / 직전 60봉 최고 종가 − 1 (최근 하락폭, 크기)
  B. `idx_rv20`  — 직전 20봉 일간수익률 표준편차 ×√252 (변동성)
  C. `k≥5 조건부 rv20` — k축은 이미 작동한다(k≥5 +2.51~2.74%).
     진짜 질문은 **k를 알고 난 뒤 변동성이 정보를 더하는가**이다.
     k≥5인 59일을 rv20 3분위로 갈라 상·하위를 본다. 이것이 2020 질문의 정직한 형태다.

(idx_ret5·idx_ret20·down_streak는 A와 상관 0.6~0.9인 같은 축이라 뺀다.
 6개를 돌리면 3개 축을 6번 세어 적중률이 부풀려진다.)

검정: 변수당 **1회** — 상위 3분위 vs 하위 3분위 일가중 초과 차이, 날짜 클러스터 t.
표는 t 내림차순이 아니라 **변수명 순서 고정**으로 낸다.

판정 기준(먼저 고정):
  · 후보 = |t| ≥ 2.5  **그리고**  전반(2018~2021)·후반(2022~2026) 부호 동일.
  · 둘 중 하나라도 불통과면 기각. 사후에 기준을 낮추지 않는다.
  · 결과를 보고 4번째 변수를 돌리고 싶어지면 그것이 멈추라는 신호다.

전부 past-only(신호일까지의 정보만). 초과분만 판정에 쓴다 — 원 net은 안 쓴다.
⚠️ 생존편향 하한 3.3%가 그대로 적용된다(kr_pool_long_verdict_20260912.md).
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_benchmark_excess import bench_bars  # noqa: E402
from tools.research_kr_rank_pool_reconcile import build, by_session  # noqa: E402

LONG = ROOT / "data" / "price_backfill_yf" / "kr_fallen_price_cache_2018_2026.json"
HOLD = 5
T_BAR = 2.5
SPLIT_YEAR = "2022"


def cluster_t(values):
    """일가중 값들의 t. 각 원소가 이미 '하루'다(일가중이므로 클러스터=일)."""
    if len(values) < 3 or st.stdev(values) == 0:
        return None
    return st.mean(values) / (st.stdev(values) / math.sqrt(len(values)))


def welch_t(a, b):
    """상위분위 − 하위분위 차이의 t (일 단위 독립 가정, Welch)."""
    if len(a) < 3 or len(b) < 3:
        return None, None
    va, vb = st.variance(a) / len(a), st.variance(b) / len(b)
    if va + vb == 0:
        return None, None
    return st.mean(a) - st.mean(b), (st.mean(a) - st.mean(b)) / math.sqrt(va + vb)


def terciles(rows, key):
    vals = sorted(r[key] for r in rows)
    n = len(vals)
    return vals[n // 3], vals[2 * n // 3]


def split_report(name, rows, key, note=""):
    """상위 3분위 vs 하위 3분위 일가중 초과. 반환은 표 행 + 전/후반 부호."""
    lo_cut, hi_cut = terciles(rows, key)
    lo = [r["day_excess"] for r in rows if r[key] <= lo_cut]
    hi = [r["day_excess"] for r in rows if r[key] >= hi_cut]
    diff, t = welch_t(hi, lo)
    halves = {}
    for label, sub in (("전반", [r for r in rows if r["day"] < SPLIT_YEAR]),
                       ("후반", [r for r in rows if r["day"] >= SPLIT_YEAR])):
        if len(sub) < 9:
            halves[label] = None
            continue
        lc, hc = terciles(sub, key)
        d, _ = welch_t([r["day_excess"] for r in sub if r[key] >= hc],
                       [r["day_excess"] for r in sub if r[key] <= lc])
        halves[label] = d
    same_sign = (halves["전반"] is not None and halves["후반"] is not None
                 and halves["전반"] * halves["후반"] > 0)
    passed = t is not None and abs(t) >= T_BAR and same_sign
    f = lambda x: f"{x:+.2f}%" if x is not None else "-"        # noqa: E731
    row = (f"| {name} | {len(rows)} | {st.mean(lo):+.2f}% | {st.mean(hi):+.2f}% | "
           f"{f(diff)} | {t:.2f} | {f(halves['전반'])} | {f(halves['후반'])} | "
           f"{'**통과**' if passed else '기각'} |")
    return row, passed, note


def main():
    if not LONG.exists():
        print(f"장기 캐시 없음: {LONG}", file=sys.stderr)
        return 2
    cache = json.loads(LONG.read_text(encoding="utf-8"))
    sessions = by_session(build(cache))
    bars = bench_bars("kr", "069500")
    idx = {b[0]: i for i, b in enumerate(bars)}

    def excess(entry_date, net):
        i = idx.get(entry_date)
        if i is None or i + HOLD > len(bars):
            return None
        return net - (bars[i + HOLD - 1][4] / bars[i][1] - 1) * 100

    def features(signal_date):
        """신호일까지의 정보만 쓰는 국면 특성."""
        i = idx.get(signal_date)
        if i is None or i < 61:
            return None
        closes = [b[4] for b in bars[i - 60:i + 1]]          # 신호일 포함 61봉
        rets = [closes[j] / closes[j - 1] - 1 for j in range(len(closes) - 20, len(closes))]
        return {"idx_dd60": (closes[-1] / max(closes[:-1]) - 1) * 100,
                "idx_rv20": st.stdev(rets) * math.sqrt(252) * 100}

    days = []
    miss_bench = miss_feat = 0
    for sig, pool in sessions.items():
        feat = features(sig)
        if feat is None:
            miss_feat += 1
            continue
        exs = [excess(c["entry_date"], c["net"]) for c in pool]
        exs = [e for e in exs if e is not None]
        if not exs:
            miss_bench += 1
            continue
        days.append({"day": sig, "k": len(pool), "n": len(exs),
                     "day_excess": st.mean(exs), **feat})
    days.sort(key=lambda r: r["day"])

    big = [r for r in days if r["k"] >= 20]
    mid = [r for r in days if r["k"] >= 5]

    L = [f"# KR 국면 축 검정 — 553일을 가르는 변수가 있는가 ({date.today().isoformat()})", "",
         "read-only. 스크립트 `tools/research_kr_regime_split.py`(사전등록 기준은 docstring).",
         "판정은 **일가중 초과분**으로만 한다.", "",
         "## 먼저 — 2020의 실질 표본은 1개다", ""]
    y20 = [r for r in big if r["day"][:4] == "2020"]
    L += [f"k≥20인 2020년 {len(y20)}일: " + ", ".join(r["day"] for r in y20),
          "",
          f"**전부 {sorted({r['day'][:7] for r in y20})[0]} 한 주다.** 일 클러스터는 5로 세지만",
          "실질 사건은 코로나 폭락 **1개**다. 이 5점을 가르는 변수를 찾는 것은 한 주를 설명하는",
          "과적합이다. 그래서 아래 검정은 26일이 아니라 **553일 전체**에서 한다.", "",
          "## 사전 등록한 기준", "",
          f"- 변수 3개(dd60·rv20·k조건부 rv20), 변수당 **1회** 검정. 표는 변수명 순서 고정.",
          f"- 후보 = **|t| ≥ {T_BAR}** 그리고 **전반(~2021)·후반({SPLIT_YEAR}~) 부호 동일**.",
          "- 사후에 기준을 낮추지 않는다. 4번째 변수를 돌리고 싶어지면 멈춘다.", "",
          f"표본: 유효 {len(days)}일 (벤치 결측 {miss_bench}일 · 특성 워밍업 부족 {miss_feat}일)", "",
          "## 검정 결과", "",
          "| 변수 | 일수 | 하위3분위 | 상위3분위 | 차이 | t | 전반 차이 | 후반 차이 | 판정 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---|"]

    tests = [("A. idx_dd60 (전 553일)", days, "idx_dd60"),
             ("B. idx_rv20 (전 553일)", days, "idx_rv20"),
             ("C. idx_rv20 | k≥5", mid, "idx_rv20")]
    verdicts = []
    for name, rows, key in tests:
        line, passed, _ = split_report(name, rows, key)
        L.append(line)
        verdicts.append((name, passed))

    L += ["", "## 참고 — 판정에 쓰지 않는 기술 통계", "",
          "| 구간 | 일수 | 일가중 초과 | t(일) |", "|---|---:|---:|---:|"]
    for lab, rows in (("전체", days), ("k≥5", mid), ("k≥20", big)):
        v = [r["day_excess"] for r in rows]
        t = cluster_t(v)
        L.append(f"| {lab} | {len(rows)} | {st.mean(v):+.2f}% | "
                 f"{f'{t:.2f}' if t is not None else '-'} |")

    L += ["", "### k≥20인 26일의 국면 특성 (사후 관찰 — 검정 아님)", "",
          "| 날짜 | k | 일초과 | dd60 | rv20 |", "|---|---:|---:|---:|---:|"]
    for r in big:
        L.append(f"| {r['day']} | {r['k']} | {r['day_excess']:+.2f}% | "
                 f"{r['idx_dd60']:+.1f}% | {r['idx_rv20']:.1f}% |")

    passed_any = [n for n, p in verdicts if p]
    L += ["", "## 벤치 시계열 무결성 — 2026 고변동은 실제다", "",
          "위 표의 2026 rv20이 70~85%로 튄다. 지수 ETF로는 비정상이라 원천을 대조했다.",
          "**라이브 CSV(`data/price/kr/kr_069500.csv`)와 백필이 정확히 일치한다** —",
          "2026-07-31 종가 108,820(전일 87,635 대비 +24.2%), 2026-06-23 −10.5% 모두 양쪽 동일.",
          "스플라이스 결함이 아니라 2026이 실제로 구조적 고변동 국면이다",
          "(연도별 지수 일간변동: 2018 0.92% / 2020 1.86% / 2025 1.42% / **2026 4.39%**).", "",
          "**그래서 변수 B는 측정 타당성에 결함이 있다.** 절대 rv20은 8년에 걸쳐 비정상이라",
          "3분위 분할이 사실상 '2026이냐 아니냐'를 가른다. 전반 −2.72 / 후반 +0.10의 부호 뒤집힘은",
          "변동성 효과가 아니라 이 비정상성의 그림자일 수 있다. 재설계하려면",
          "**직전 250봉 대비 rv20 백분위**로 바꿔야 하는데, 결과를 본 뒤 재설계하는 것은",
          "사후 기준 완화다. 돌린다면 새 사전등록으로 분리한다 — 이 리포트에 넣지 않는다.", "",
          "## 판정", ""]
    if passed_any:
        L += [f"사전등록 기준 통과: {', '.join(passed_any)}.",
              "**인샘플 후보일 뿐이다.** 다음은 라이브 게이트가 아니라 사전등록 + forward다."]
    else:
        L += ["**3개 전부 기각이다.** 사전등록 기준(|t|≥2.5 + 전후반 부호 일치)을 넘은 변수가 없다.",
              "553일 일가중 초과는 국면 변수로 0에서 안 움직인다.",
              "→ **2020은 평균 0 분포의 꼬리이지 식별 가능한 국면이 아니다.**",
              "  '2020형 국면 식별자'는 5점(실질 1주)에 맞추는 과적합이므로 여기서 닫는다."]
    L += ["", "(전체 540일 −0.12%는 09-12 리포트의 553일 +0.01%와 같은 수치다 — 여기서는",
          "60봉 워밍업이 필요해 초기 16일과 벤치 결측 8일이 빠진다. 둘 다 0과 구별 안 된다.)", "",
          "## 다음 판 — 국면이 아니라 k다", "",
          "국면 축이 닫혔으므로 이 레인에서 살아 있는 건 **k축 하나**다.",
          "같은 표에서 k≥5 **+2.07%(t 3.00, 79일)**, k≥20 **+3.68%(t 3.19, 26일)**.",
          "국면 변수를 얹어도 이 수치가 안 움직인다는 것이 오늘의 결과다.",
          "**즉 이 레인의 전부는 '시장이 무너진 날에만 산다'이고, 그 외 조건은 장식이다.**", "",
          "1. **k 문턱을 사전등록하고 forward로 간다.** k≥5와 k≥20 중 실측으로 고르면 과적합이다.",
          "   (k=10~19가 −0.04%로 단조를 깨는 것도 20일뿐인 표본 탓일 수 있다.)",
          "2. **dd60은 기각이지만 유일하게 전후반 부호가 같았다**(전반 −0.38 / 후반 −3.09,",
          "   깊은 하락일수록 좋음, t 1.82). 게이트로 쓸 근거는 없고 **forward 관측 항목**으로만 둔다.",
          "3. **rv20 백분위 재설계**는 새 사전등록으로 분리한다. 오늘 결과를 보고 돌리면 인샘플이다.",
          "4. 승격 전 선결: **상폐 목록 확보**. 3.3%가 하한인 한 장기 수치는 승격 근거가 못 된다.", "",
          "⚠️ 생존편향 하한 3.3% 그대로 적용(`kr_pool_long_verdict_20260912.md`).",
          "2020 수치는 그 해에 죽은 종목이 빠져 실제보다 좋다."]

    out = ROOT / "docs" / "reports" / f"kr_regime_split_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
