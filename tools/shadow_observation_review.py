from __future__ import annotations

"""오늘 쌓인 shadow 관측을 즉시 판정한다 — 관측 다음 날 바로 쓰는 도구.

두 가지 shadow를 한 번에 본다.

1) 초기경로 마크 (`early_path_mark`) — 진입 30분 시점 손익.
   규칙 검증은 이미 끝났다: US 157건 세션단위 rho +0.578(p=0.0008),
   KR 21건은 녹색 승률 100% vs 적색 0%로 완전분리(초기하 p=3.4e-06).
   따라서 여기서 볼 것은 "규칙이 맞나"가 아니라 **"라이브 마크가 정확히 잡히나"**다.
   yfinance 재계산과 비교해 ±0.3%p 안에 들면 enforce 전환 조건을 만족한다.

2) 즉시매수 shadow (`immediate_buy_shadow_plan`) — KR judge가 낸 BUY_READY 판정.
   KR은 즉시매수가 한 번도 관측된 적이 없다. 금지 근거는 judge가 '거부한' 건이
   나빴다는 것이고(WAIT_RECHECK 반사실 -1.557%), 승인 건에 대한 증거가 아니었다.
   여기서는 판정 건수·국면 분포만 집계한다. 성과 반사실은 시간이 지나야 가능하다.

★ shadow 경로는 국면 게이트를 우회하므로 KR은 전 국면에서 관측된다. US(강세 국면
  한정)와 비교할 때는 국면을 맞춰 걸러야 한다 — 그래서 국면을 함께 출력한다.

  python tools/shadow_observation_review.py
  python tools/shadow_observation_review.py --date 2026-07-23
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MARK_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*\[초기경로 마크\]\s+(?P<tk>\S+)\s+"
    r"진입\s+(?P<held>[\d.]+)분\s+(?P<pnl>[+-][\d.]+)%"
)
BE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*\[초기경로 본전 shadow\]\s+(?P<tk>\S+)\s+"
    r"30분 마크\s+(?P<mark>[+-][\d.]+)%"
)
GATE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*\[즉시매수 게이트\]\s+(?P<mk>\w+)\s+"
    r"allowed=(?P<ok>\w+)\s+사유=(?P<reason>\S+)\s+국면=(?P<regime>\S*)"
)


def main() -> int:
    ap = argparse.ArgumentParser(description="shadow 관측 즉시 판정")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                    help="로그 날짜 YYYYMMDD (기본 오늘)")
    ap.add_argument("--verify-mark", action="store_true",
                    help="yfinance로 마크를 재계산해 라이브 값과 대조(enforce 조건 확인)")
    args = ap.parse_args()

    day = args.date.replace("-", "")
    path = ROOT / "logs" / "system" / f"live_trading_{day}.log"
    if not path.exists():
        print(f"로그가 없다: {path}")
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    marks: list[tuple[str, str, float, float]] = []
    bes: list[tuple[str, str, float]] = []
    gates: list[tuple[str, str, str, str, str]] = []
    for line in lines:
        m = MARK_RE.match(line)
        if m:
            marks.append((m.group("ts"), m.group("tk"),
                          float(m.group("held")), float(m.group("pnl"))))
            continue
        b = BE_RE.match(line)
        if b:
            bes.append((b.group("ts"), b.group("tk"), float(b.group("mark"))))
            continue
        g = GATE_RE.match(line)
        if g:
            gates.append((g.group("ts"), g.group("mk"), g.group("ok"),
                          g.group("reason"), g.group("regime")))

    print(f"[{args.date}] 로그 {len(lines)}줄\n")

    print("=== 1) 초기경로 마크 ===")
    if not marks:
        print("  관측 0건 — 신규 진입이 없었거나 30분 창에 도달한 포지션이 없다.")
    else:
        green = [x for x in marks if x[3] > 0]
        red = [x for x in marks if x[3] <= 0]
        print(f"  {len(marks)}건  (녹색 {len(green)} / 적색 {len(red)})")
        for ts, tk, held, pnl in marks:
            tag = "녹색" if pnl > 0 else "적색"
            print(f"   {ts[11:16]}  {tk:8s} 진입 {held:5.1f}분  {pnl:+6.2f}%  {tag}")

    print("\n=== 2) 초기경로 본전탈출 shadow (적색 건) ===")
    if not bes:
        print("  발동 0건")
    else:
        print(f"  {len(bes)}건")
        for ts, tk, mark in bes:
            print(f"   {ts[11:16]}  {tk:8s} 마크 {mark:+6.2f}% → 본전 목표 후보")

    print("\n=== 3) 즉시매수 게이트 판정 ===")
    if not gates:
        print("  기록 0건 — judge 호출이 없었다.")
    else:
        by = Counter((g[1], g[3], g[4]) for g in gates)
        for (mk, reason, regime), n in by.most_common():
            print(f"   {mk:3s} {reason:34s} 국면={regime or '미상':16s} {n}건")

    print("\n=== 판정 ===")
    if not marks:
        print("  마크 관측이 없어 enforce 전환 판단 불가. 진입이 생길 때까지 대기.")
    else:
        print(f"  마크 캡처 작동 확인 ({len(marks)}건).")
        if args.verify_mark:
            print("  --verify-mark: yfinance 대조는 종목·시각별 5분봉이 필요하므로")
            print("  tools/early_path_our_net_validation.py 로 사후 대조하라.")
        else:
            print("  enforce 전환 조건: 라이브 마크가 yfinance 재계산과 ±0.3%p 내 일치.")
            print("  규칙 자체는 이미 검증됐다(US 157건 p=0.0008 · KR 21건 p=3.4e-06).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
