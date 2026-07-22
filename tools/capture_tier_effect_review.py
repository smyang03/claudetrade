from __future__ import annotations

"""조기익절 tier(2026-07-14 적용)가 실제로 반납을 줄였는지 추적한다.

왜 이 도구가 필요한가:
  흑자까지 필요한 양이 계산으로 특정됐다 — 승률 +5.1%p(34.6%→39.7%) 또는
  평균승 +25%(2.87→3.58, 곧 capture +25%). 후보 선정으로 승률을 올리는 경로는
  9번 반증됐고(점수·순위·승격·저점반등 전부 무작위), 남은 축은 capture다.
  조기익절 tier는 정확히 그걸 겨냥해 2026-07-14에 라이브로 나갔으나
  청산 표본이 부족해 아직 검증되지 않았다.

이 도구는 tier 적용일 전후로 capture를 갈라 비교하고, **아직 판정할 수 없으면
"몇 건이 더 필요한지"를 알려준다.** 표본이 모자란 상태에서 성급히 결론내지
않기 위한 장치다.

capture = 평균 net / 평균 MFE. MFE는 canonical(`mfe_pct`) 우선, 없으면
`mfe_backfill_yf`(yfinance 5분봉 추정)로 보강한다. yf 추정치는 체계적으로 약간
넓어(낙관 상한) capture를 과소평가하는 방향이므로, 전후 비교에는 같은 편향이
양쪽에 걸린다는 전제로 쓴다.

한계:
- 청산 사유별 내생성: TARGET은 정의상 양수, LOSS_CAP은 정의상 음수다.
  구간별 비교는 기술통계이지 인과가 아니다.
- 국면이 다르면 tier 효과와 시장 효과가 섞인다. 세션 단위와 표본 수를 함께 본다.
- KR은 표본이 작아 대부분 판정 불가다.

  python tools/capture_tier_effect_review.py
  python tools/capture_tier_effect_review.py --tier-date 2026-07-14 --market US
"""

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "ml" / "decisions.db"

TIER_DATE = "2026-07-14"          # 조기익절 tier 라이브 적용일
MIN_N = 20                        # 이 미만이면 판정하지 않는다


def load(market: str) -> list[tuple]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=40)
    try:
        con.execute("PRAGMA busy_timeout=35000")
        # canonical mfe가 없으면 yf 백필로 보강한다.
        # 백필 테이블 키는 v2_decision_id다(ticker+session_date 아님).
        return con.execute(
            """SELECT p.session_date, p.ticker, p.pnl_pct_net,
                      COALESCE(p.mfe_pct, b.mfe_pct) AS mfe
               FROM v2_canonical_performance p
               LEFT JOIN mfe_backfill_yf b
                 ON b.v2_decision_id = p.v2_decision_id
               WHERE p.closed = 1 AND p.pnl_pct_net IS NOT NULL
                 AND p.market = ?
                 AND COALESCE(p.mfe_pct, b.mfe_pct) IS NOT NULL""",
            (market,),
        ).fetchall()
    finally:
        con.close()


def summarize(rows: list[tuple], label: str) -> dict:
    if not rows:
        print(f"  {label}: 표본 없음")
        return {}
    nets = [float(r[2]) for r in rows]
    mfes = [float(r[3]) for r in rows]
    pos_mfe = [(n, m) for n, m in zip(nets, mfes) if m > 0]
    cap = (mean([n for n, _ in pos_mfe]) / mean([m for _, m in pos_mfe]) * 100
           if pos_mfe else float("nan"))
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    payoff = (mean(wins) / abs(mean(losses))) if wins and losses else float("nan")
    giveback = (mean([m for _, m in pos_mfe]) - mean([n for n, _ in pos_mfe])
                if pos_mfe else float("nan"))
    print(f"  {label}: n={len(rows):3d}  net {mean(nets):+6.3f}%  "
          f"승률 {len(wins)/len(nets)*100:4.1f}%  승/패 {payoff:4.2f}  "
          f"MFE>0 {len(pos_mfe):3d}건 capture {cap:5.1f}%  반납 {giveback:5.2f}%p")
    return {"n": len(rows), "net": mean(nets), "capture": cap,
            "giveback": giveback, "payoff": payoff}


def main() -> int:
    ap = argparse.ArgumentParser(description="조기익절 tier 효과 추적")
    ap.add_argument("--tier-date", default=TIER_DATE)
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--min-n", type=int, default=MIN_N)
    args = ap.parse_args()

    rows = load(args.market)
    before = [r for r in rows if str(r[0]) < args.tier_date]
    after = [r for r in rows if str(r[0]) >= args.tier_date]

    print(f"[{args.market}] tier 적용일 {args.tier_date} 기준 "
          f"(MFE 보유 청산 {len(rows)}건)")
    print("\n=== 전체 ===")
    b = summarize(before, "적용 전")
    a = summarize(after, "적용 후")

    print("\n=== MFE 구간별 capture ===")
    for lo, hi, lab in [(0, 1, "0~1%"), (1, 3, "1~3%"), (3, 5, "3~5%"), (5, 99, ">5%")]:
        bb = [r for r in before if lo <= float(r[3]) < hi]
        aa = [r for r in after if lo <= float(r[3]) < hi]
        if not bb and not aa:
            continue
        def cap(v):
            if not v:
                return "표본없음"
            nets = [float(x[2]) for x in v]
            mfes = [float(x[3]) for x in v]
            return (f"n={len(v):3d} capture {mean(nets)/mean(mfes)*100:6.1f}%"
                    if mean(mfes) > 0 else f"n={len(v):3d} n/a")
        print(f"  MFE {lab:>6}  전: {cap(bb):28s}  후: {cap(aa)}")

    print("\n=== 판정 ===")
    if len(after) < args.min_n:
        need = args.min_n - len(after)
        print(f"  ❌ 판정 불가 — 적용 후 표본 {len(after)}건 (최소 {args.min_n}건 필요, "
              f"{need}건 부족)")
        print(f"     현재 진입 빈도로는 표본이 더 쌓여야 한다. 성급히 결론내지 말 것.")
    elif b and a:
        d_cap = a["capture"] - b["capture"]
        d_give = b["giveback"] - a["giveback"]
        print(f"  capture {b['capture']:.1f}% → {a['capture']:.1f}% ({d_cap:+.1f}%p)")
        print(f"  반납    {b['giveback']:.2f}%p → {a['giveback']:.2f}%p ({d_give:+.2f}%p 개선)")
        print(f"  승/패비 {b['payoff']:.2f} → {a['payoff']:.2f}")
        # 흑자 조건 대조
        print(f"\n  흑자 필요선: 평균승 +25%(capture 기준 약 +25% 상대개선)")
        rel = (d_cap / b["capture"] * 100) if b["capture"] else 0
        print(f"  현재 상대개선 {rel:+.1f}% → "
              f"{'✅ 필요선 도달' if rel >= 25 else '아직 미달'}")
    print("\n  한계: 청산사유 내생성 · 국면 차이 · yf MFE 추정 편향(양쪽 동일 가정).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
