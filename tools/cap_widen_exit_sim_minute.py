"""
cap_widen_exit_sim_minute — cap 완화 진입 케이스의 청산을 '분봉 경로'로 재현해
앞 단계의 opt/pes 밴드를 실제 net 하나로 해소한다 (read-only, 로컬 분봉, API 없음).

데이터: data/price/minute/kr/kr_<ticker>.csv  (1분봉 OHLCV, KST)
- cap_exceeded 케이스 진입일(D0) 분봉 100% 존재 → TP/SL 도달 '순서'를 실측.

모델:
- 진입 지정가 P = cap*(1+m)  (cap 을 m 만큼 올림 = 그 가격 이하로 떨어지면 매수)
- 체결: D0 분봉 중 low<=P 인 첫 봉. 그 '다음' 봉부터 추적(체결봉 자기체결 제외).
- 추적: 체결 이후 D0 잔여 + 이후 가용 거래일(최대 3일) 분봉을 시간순으로
    high>=P*(1+TP) → TP 청산(+TP%)
    low <=P*(1-SL) → SL 청산(-SL%)
    한 봉에서 둘 다 닿으면 순서 불명 → 보수적으로 SL(표시: both_bar)
- 미발동: 추적 마지막 분봉 close 로 청산.

한계:
- 1분봉 내부의 TP/SL 동시 도달(both_bar)만 잔여 불확실(보수적 처리, 건수 출력).
- 진입 지정가 P=cap*(1+m). 실제 체결은 더 유리할 수도(슬리피지 무시).
- 표본 소수 — 통계보다 '밴드가 어디로 수렴하나' 확인용.

CLI:
  python tools/cap_widen_exit_sim_minute.py --market KR --start 20260506 --end 20260629 \
      --multipliers 0.005,0.01 --tp 3,5 --sl 2,3
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from tools.cap_widen_shadow_review import _collect_cap_cases  # noqa: E402

_MIN_DIR = _ROOT / "data" / "price" / "minute"

_bar_cache: dict[str, list[tuple]] = {}


def _load_bars(market: str, ticker: str) -> list[tuple]:
    """[(date 'YYYY-MM-DD', ts, high, low, close)] 시간순. 캐시."""
    key = f"{market}:{ticker}"
    if key in _bar_cache:
        return _bar_cache[key]
    path = _MIN_DIR / market.lower() / f"{market.lower()}_{ticker}.csv"
    bars: list[tuple] = []
    if path.exists():
        with open(path, encoding="utf-8-sig") as f:  # BOM 제거(헤더 키 정합)
            r = csv.DictReader(f)
            for row in r:
                ts = (row.get("ts") or "").strip()
                if not ts:
                    continue
                try:
                    hi = float(row["high"]); lo = float(row["low"]); cl = float(row["close"])
                except (KeyError, ValueError, TypeError):
                    continue
                bars.append((ts[:10], ts, hi, lo, cl))
    bars.sort(key=lambda b: b[1])
    _bar_cache[key] = bars
    return bars


def _simulate(market: str, ticker: str, d0: str, P: float, tp: float, sl: float) -> Optional[tuple]:
    """분봉 경로 청산. 반환 (net_pct, how) 또는 None(체결불가/데이터없음)."""
    bars = _load_bars(market, ticker)
    if not bars or P <= 0:
        return None
    day0 = [b for b in bars if b[0] == d0]
    if not day0:
        return None
    # 체결: D0 분봉 중 low<=P 첫 봉
    entry_i = None
    for i, b in enumerate(day0):
        if b[3] <= P:
            entry_i = i
            break
    if entry_i is None:
        return None  # 그날 P 에 안 닿음(마스크상 드묾)
    later_dates = sorted({b[0] for b in bars if b[0] > d0})[:3]
    track = day0[entry_i + 1:] + [b for b in bars if b[0] in later_dates]
    tp_px = P * (1 + tp / 100.0)
    sl_px = P * (1 - sl / 100.0)
    for (_d, _ts, hi, lo, _cl) in track:
        hit_tp = hi >= tp_px
        hit_sl = lo <= sl_px
        if hit_tp and hit_sl:
            return (-sl, "both_bar")
        if hit_tp:
            return (tp, "tp")
        if hit_sl:
            return (-sl, "sl")
    last = track[-1] if track else day0[entry_i]
    return ((last[4] - P) / P * 100.0, "close")


def _stat(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "win": None}
    return {"n": len(vals), "mean": round(statistics.mean(vals), 3),
            "win": round(100.0 * sum(1 for v in vals if v > 0) / len(vals), 1)}


def run(market, start, end, multipliers, tps, sls):
    cases, scanned = _collect_cap_cases(market, start, end)
    print("=" * 82)
    print(f" cap_widen_exit_sim_minute  market={market}  기간={start}~{end}")
    print(f" cap_exceeded 케이스={len(cases)}  (분봉 경로 청산 실측)")
    print("=" * 82)
    if not cases:
        print(" 케이스 0.")
        return

    for m in sorted(multipliers):
        # 진입가능: min_current <= cap*(1+m)
        sub = [(d, t, cases[(d, t)]) for (d, t) in cases
               if cases[(d, t)]["min_current"] <= cases[(d, t)]["cap"] * (1 + m)]
        if not sub:
            print(f"\n[m=+{m*100:.1f}%] 진입가능 0")
            continue
        print(f"\n[m=+{m*100:.1f}%] 진입가능 {len(sub)}/{len(cases)}  (진입가 P=cap*(1+m))")
        for tp in tps:
            for sl in sls:
                nets = []
                hows = {"tp": 0, "sl": 0, "close": 0, "both_bar": 0}
                measured = 0
                for (d, t, rec) in sub:
                    P = rec["cap"] * (1 + m)
                    d_iso = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"  # 20260629 -> 2026-06-29 (분봉 ts 형식)
                    res = _simulate(market, t, d_iso, P, tp, sl)  # t = ticker (key 의 둘째)
                    if res is None:
                        continue
                    measured += 1
                    nets.append(res[0])
                    hows[res[1]] = hows.get(res[1], 0) + 1
                s = _stat(nets)
                if s["n"] == 0:
                    print(f"    TP{tp:g}/SL{sl:g}  측정0")
                    continue
                print(f"    TP{tp:g}/SL{sl:g}  net={s['mean']:+6.2f}%  win={s['win']:5.1f}%  "
                      f"(n={s['n']})  [tp:{hows['tp']} sl:{hows['sl']} "
                      f"close:{hows['close']} both:{hows['both_bar']}]")

    print("\n" + "-" * 82)
    print(" net = 분봉 경로로 실측한 단일값(밴드 해소). how: tp/sl=먼저닿은쪽, close=미발동종가,")
    print("       both=1분봉내 동시도달(보수적 SL처리). both 가 많으면 잔여 불확실.")
    print(" 한계: 진입가=cap*(1+m), 슬리피지 무시, 표본 소수.")


def main():
    p = argparse.ArgumentParser(description="cap 완화 × 청산 분봉경로 실측 (read-only)")
    p.add_argument("--market", choices=["KR", "US"], default="KR")
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--multipliers", default="0.005,0.01")
    p.add_argument("--tp", default="3,5")
    p.add_argument("--sl", default="2,3")
    a = p.parse_args()
    run(a.market, a.start, a.end,
        [float(x) for x in a.multipliers.split(",") if x.strip()],
        [float(x) for x in a.tp.split(",") if x.strip()],
        [float(x) for x in a.sl.split(",") if x.strip()])


if __name__ == "__main__":
    main()
