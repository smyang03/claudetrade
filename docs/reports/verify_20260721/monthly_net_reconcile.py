"""월별 손익표 기준 원장 고정 — ultimate 리포트 첫 표의 재현 도구.

코덱스 검토(2026-07-21) 지적: 월별 KRW net이 테이블·NULL 처리에 따라 달라진다.
이 스크립트는 기준을 명시적으로 고정하고 net coverage를 함께 출력해, "이 수치가
net인지 gross-fallback인지"를 숨기지 않는다.

기준 원장(고정):
- 테이블: v2_canonical_performance (중복 제거된 canonical view — 한 포지션이
  learning에서 다중 행으로 잡히는 것을 방지. CLAUDE.md 'Path B가 truth' 계약).
- 금액: pnl_krw_net(수수료·FX 반영 우리 실제 net) 우선.
- net 결측 시: pnl_krw(gross)로 폴백하되, 그 건수를 net_missing으로 명시.
  → US 5월처럼 net 백필이 안 된 구간은 표의 값이 gross임을 드러낸다.

사용: python docs/reports/verify_20260721/monthly_net_reconcile.py
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "data" / "ml" / "decisions.db"


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("pragma busy_timeout=5000")
    q = """
    select substr(session_date,1,7) m, market,
        count(*) n,
        sum(case when pnl_krw_net is null then 1 else 0 end) net_missing,
        round(sum(coalesce(pnl_krw_net, pnl_krw, 0)),0) best_krw,
        round(sum(pnl_krw),0) gross_krw,
        round(avg(coalesce(pnl_pct_net, pnl_pct, 0)),4) avg_pct
    from v2_canonical_performance
    where runtime_mode='live' and closed=1 and session_date>='2026-05-01'
    group by 1,2 order by 1,2
    """
    print("기준: v2_canonical_performance · live · closed · pnl_krw_net 우선(결측=gross 폴백)")
    print(f"{'월':8} {'시장':4} {'n':>4} {'net결측':>7} {'best_krw':>10} {'gross_krw':>10} {'avg%':>8} 비고")
    monthly = {}
    for r in con.execute(q):
        d = dict(r)
        note = ""
        if d["net_missing"] == d["n"]:
            note = "★전건 net결측 = 값은 GROSS"
        elif d["net_missing"] > 0:
            note = f"부분 net결측 {d['net_missing']}/{d['n']}"
        best = d["best_krw"] or 0.0
        gross = d["gross_krw"] if d["gross_krw"] is not None else 0.0
        avgp = d["avg_pct"] if d["avg_pct"] is not None else 0.0
        print(f"{d['m']:8} {d['market']:4} {d['n']:>4} {d['net_missing']:>7} "
              f"{best:>10,.0f} {gross:>10,.0f} {avgp:>+8.3f} {note}")
        monthly.setdefault(d["m"], 0.0)
        monthly[d["m"]] += best
    print("\n월 합계(best_krw 기준):")
    for m, v in sorted(monthly.items()):
        print(f"  {m}: {v:+,.0f}")
    con.close()
    print("\n주의: best_krw는 net 우선·결측 시 gross. net결측 구간(특히 US 5월)은 "
          "gross라 fee/FX 미반영 — 이 구간을 net으로 인용하지 말 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
