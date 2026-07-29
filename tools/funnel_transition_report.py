"""진입 퍼널 전이 리포트 — "어느 단계에서 후보가 전멸했는가"를 세션 단위로 드러낸다.

배경 (2026-07-29):
  2026-07-28 US 세션은 모든 점검을 통과했다(preflight fail=0, guardian hard=0, API 11/11,
  에러 0, 국면 판단 적중). 그런데 실주문은 0건이었다.
  각 단계는 정상인데 단계 '사이'가 끊겼고, 그 탈락이 log.debug + DB 미기록이라 무흔적이었다.

  단계별 상태 점검으로는 이런 결함이 원리적으로 안 잡힌다. 봐야 하는 것은 '전이율'이다.
  실제로 그날 수동으로 아래 숫자를 나란히 놓자마자 원인이 보였다:
      judge 12 → 승격 3 → 발동 6 → 주문루프 0 → 실주문 0
  이 도구는 그 작업을 자동화한다. 라이브 코드를 건드리지 않고 로그만 읽는다.

판정 규칙
  - 앞 단계에 공급이 있었는데 다음 단계가 0이면 100% 유실 → ALERT.
  - 매수 0이 '국면 차단(설계대로)'인지 '경로 결함'인지 구분하는 것이 목적이다.
    국면 차단이면 애초에 judge 공급부터 0이거나 mode_block이 찍힌다.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOG_GLOB = str(ROOT / "logs" / "system" / "live_trading_*.log")

# 단계 정의: (키, 표시명, 정규식). 순서가 곧 퍼널 순서다.
STAGES: "OrderedDict[str, tuple[str, re.Pattern]]" = OrderedDict([
    ("judge_ready", ("judge BUY_READY 판정",
                     re.compile(r"\[early judge\]\s+(\w+).*actions=\[[^\]]*BUY_READY"))),
    ("promoted", ("trade_ready 승격",
                  re.compile(r"\[selection normalize\]\s+(\w+).*applied_trade=\[(?!\])"))),
    ("fired", ("즉시매수 발동",
               re.compile(r"\[BUY_READY 즉시 (\w+)\]"))),
    ("order_loop", ("주문루프 도달(신호정렬)",
                    re.compile(r"\[(\w+)\]\s+신호 정렬:"))),
    ("ordered", ("실주문 제출",
                 re.compile(r"\[(?:LIVE|PAPER) (?:MICRO_PROBE )?BUY\]"))),
])

# 참고 지표 — 매수 0의 사유를 국면/결함으로 가르는 데 쓴다.
CONTEXT = OrderedDict([
    ("mode_block", re.compile(r"모드 진입 억제|mode_block")),
    ("from_high_block", re.compile(r"고점근접 차단(?! 면제)")),
    ("from_high_exempt", re.compile(r"고점근접 차단 면제")),
    ("late_cutoff", re.compile(r"마감 직전 차단")),
    ("capacity", re.compile(r"capacity exhausted")),
    ("halt", re.compile(r"consensus: HALT")),
])

TS = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")


def scan(paths: list[str], market: str | None, since: str, until: str) -> dict:
    counts = {k: 0 for k in STAGES}
    ctx = {k: 0 for k in CONTEXT}
    for path in paths:
        try:
            f = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for line in f:
                m = TS.match(line)
                if not m:
                    continue
                stamp = f"{m.group(1)} {m.group(2)}"
                if stamp < since or stamp > until:
                    continue
                for key, (_label, rx) in STAGES.items():
                    mm = rx.search(line)
                    if not mm:
                        continue
                    if market and mm.groups() and mm.group(1) not in (market,):
                        # 시장 태그가 있는 단계만 필터한다(실주문 로그엔 시장 표기가 없다).
                        continue
                    counts[key] += 1
                for key, rx in CONTEXT.items():
                    if rx.search(line):
                        ctx[key] += 1
    return {"counts": counts, "context": ctx}


def report(res: dict) -> int:
    counts, ctx = res["counts"], res["context"]
    print(f"{'단계':26s} {'건수':>6s}  {'전이율':>7s}")
    print("-" * 46)
    prev_key = None
    alerts = []
    for key, (label, _rx) in STAGES.items():
        n = counts[key]
        if prev_key is None:
            rate = "-"
        else:
            p = counts[prev_key]
            rate = "-" if p == 0 else f"{n / p * 100:5.1f}%"
            if p > 0 and n == 0:
                alerts.append((STAGES[prev_key][0], label, p))
        print(f"{label:26s} {n:6d}  {rate:>7s}")
        prev_key = key

    print()
    print("참고 지표:", ", ".join(f"{k}={v}" for k, v in ctx.items() if v))

    print()
    if alerts:
        for src, dst, p in alerts:
            print(f"★ ALERT 100% 유실 — '{src}' {p}건이 '{dst}' 0건으로 전멸")
        if ctx.get("halt") or ctx.get("mode_block"):
            print("  (국면 차단 지표가 함께 잡혔다 — 설계대로인지 결함인지 해당 구간 로그를 확인할 것)")
        else:
            print("  (국면 차단 지표가 없다 — 경로 결함일 가능성이 높다)")
        return 1
    if counts["judge_ready"] == 0:
        print("판정: judge 공급 자체가 0 — 상류(후보/국면/capacity)를 먼저 본다. 경로 결함 아님.")
        return 0
    print("판정: 100% 유실 단계 없음.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help='"YYYY-MM-DD HH:MM:SS"')
    ap.add_argument("--until", default="9999-12-31 23:59:59")
    ap.add_argument("--market", default="", help="US 또는 KR (비우면 전체)")
    ap.add_argument("--glob", default=LOG_GLOB)
    args = ap.parse_args()

    paths = sorted(glob.glob(args.glob))
    if not paths:
        print(f"로그 없음: {args.glob}")
        return 2
    print(f"구간 {args.since} ~ {args.until} | 시장 {args.market or 'ALL'} | 파일 {len(paths)}개")
    print()
    res = scan(paths, args.market or None, args.since, args.until)
    return report(res)


if __name__ == "__main__":
    raise SystemExit(main())
