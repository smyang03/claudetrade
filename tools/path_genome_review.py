"""경로 유전자 shadow 리뷰 — 청산 기록(logs/funnel/path_genome_*.jsonl)을 읽어
ride-규칙("확인된 승자 연장")을 우리 net으로 forward 검증. 매매·config 무접촉.

사용: python tools/path_genome_review.py [--days N]
근거: six-visions-verified-20260719 (② 경로 r=0.49, 컷 반증·연장 실익 +0.330%).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter, defaultdict

FUNNEL = os.path.join("logs", "funnel")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()

    rows = []
    for f in sorted(glob.glob(os.path.join(FUNNEL, "path_genome_*.jsonl"))):
        try:
            for line in open(f, encoding="utf-8"):
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        except OSError:
            continue

    if not rows:
        print("경로 유전자 기록 없음 — 라이브 청산이 쌓이면 채워짐(prospective).")
        print("확인 경로: pathb 청산부 shadow 훅 → logs/funnel/path_genome_*.jsonl")
        return 0

    n = len(rows)
    print(f"경로 유전자 기록 {n}건\n")

    # shape 분포
    shape_c = Counter(r.get("shape") for r in rows)
    print("=== shape 분포 ===")
    for s, c in shape_c.most_common():
        print(f"  {s}: {c}")

    # outcome_tag별 net
    print("\n=== outcome_tag별 (pnl_pct) ===")
    by_tag = defaultdict(list)
    for r in rows:
        by_tag[r.get("outcome_tag")].append(r.get("pnl_pct"))
    for tag, pnls in sorted(by_tag.items(), key=lambda x: -len(x[1])):
        m = _mean(pnls)
        print(f"  {str(tag):22} n={len(pnls):>3} avg pnl={m:+.3f}%" if m is not None else f"  {tag}: n={len(pnls)}")

    # ★ride-규칙 검증: 확인된 승자(ride_candidate) vs 확인 후 반납(confirmed_but_lost)
    print("\n=== ★ride-규칙 검증 (확인된 러너를 연장할 가치가 있나) ===")
    ride = [r for r in rows if r.get("ride_candidate")]
    conf_lost = [r for r in rows if r.get("outcome_tag") == "confirmed_but_lost"]
    print(f"  ride_candidate(확인+회복형): n={len(ride)} avg pnl={_mean([r.get('pnl_pct') for r in ride])}")
    print(f"  confirmed_but_lost(확인 후 반납): n={len(conf_lost)} avg pnl={_mean([r.get('pnl_pct') for r in conf_lost])}")
    print("  → ride_candidate가 크게 (+)면 연장 가치, confirmed_but_lost가 많으면 포착 누수")

    # 시간축(있으면): 고점까지 시간 vs outcome
    tp = [r.get("time_to_peak_min") for r in rows if r.get("time_to_peak_min") is not None]
    if tp:
        print(f"\n=== 고점 도달 시간(min) — n={len(tp)} 평균 {_mean(tp):.0f} ===")
        early = [r for r in rows if (r.get("time_to_peak_min") or 1e9) <= 60]
        late = [r for r in rows if (r.get("time_to_peak_min") or -1) > 60]
        print(f"  조기고점(<=60min) n={len(early)} avg pnl={_mean([r.get('pnl_pct') for r in early])}")
        print(f"  후기고점(>60min)  n={len(late)} avg pnl={_mean([r.get('pnl_pct') for r in late])}")
    print("\n판정 규율: shadow 관측만. 우리 net으로 ride-규칙 확인 후 기존 러너/carry 파라미터로 enforce 검토.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
