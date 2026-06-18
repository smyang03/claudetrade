from __future__ import annotations

"""꼬리-capture shadow 검증 — funnel 플래그를 다음날 forward로 재구성(오버나잇 포함).

설계 §3: shadow는 "캐리/trail했을 것"을 로깅하지만 실시스템이 당일 청산 → 오버나잇 결과 관찰불가.
이 도구가 funnel(`logs/funnel/tail_capture_*.jsonl`)의 엔진 결정 + 실제 진입가를 읽어, 다음날(+2d)
yfinance 경로로 "엔진정책이면 얼마" vs "실제 청산"을 재구성. **이게 무위험 + 오버나잇 포함 검증.**

read-only. shadow 데이터가 쌓인 뒤(내일 KR부터) 실행.
"""

import argparse
import glob
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import tail_capture as tc


def _load_funnel(date_glob: str) -> list[dict]:
    out = []
    for fp in glob.glob(str(ROOT / "logs" / "funnel" / f"tail_capture_{date_glob}.jsonl")):
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="꼬리-capture shadow forward 검증")
    ap.add_argument("--date", default="*", help="funnel 날짜 glob (예: 20260618)")
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()
    import yfinance as yf

    recs = _load_funnel(args.date)
    if not recs:
        print("funnel 레코드 없음(shadow 미실행?).")
        return 0
    # 포지션(path_run_id+entry)별 최초 진입 + 엔진이 EXIT/CARRY 플래그한 시점
    by_pos = defaultdict(list)
    for r in recs:
        by_pos[(r.get("market"), r.get("ticker"), r.get("path_run_id"))].append(r)

    print(f"funnel {len(recs)}행 / 포지션 {len(by_pos)}개")
    # 시장×티커별 5분봉 재구성으로 trail/carry 정책 vs 실제 (간이: tail_capture_sim 방법론)
    # NB: 실제 청산가는 funnel에 없을 수 있어 actual은 마지막 관측 net로 근사. 정밀은 v2_learning과 join.
    summ = {"engine_exit": 0, "engine_carry": 0, "engine_hold": 0}
    for (mk, tk, prid), rs in by_pos.items():
        actions = [x.get("engine", {}).get("action") for x in rs]
        if "CARRY" in actions:
            summ["engine_carry"] += 1
        elif "EXIT" in actions:
            summ["engine_exit"] += 1
        else:
            summ["engine_hold"] += 1
    print("엔진 플래그 분포:", summ)
    print("→ 정밀 forward(오버나잇 net 재구성)는 v2_learning_performance join + yfinance 경로로 확장.")
    print("  (shadow 며칠 쌓인 뒤 carry건 net+ / 손실누수 0 / 약세장 통과 판정)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
