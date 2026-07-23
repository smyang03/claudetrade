from __future__ import annotations

"""분석 신뢰도 감사 — "거짓 레버"를 하나씩 찾지 말고 그 부류 전체를 한 번에 뽑는다.

왜 필요한가 (2026-07-23, 하루에 같은 부류 함정 3회+):
  ret_5m 대박예측 · KR 가드 완화 · KR capture — 전부 데이터 신뢰 실패에서 나온 거짓 레버였다.
  근본은 소수의 실패 모드다:
    T1  gross(수수료·FX 미반영)를 우리 net 대신 판정에 쓴다
    T2  고정 horizon(1일 forward)을 우리 실제 보유기간(예: 0.5h) 대신 쓴다
    T3  backfill MFE(일봉 고/저)를 우리 보유 중 고점처럼 capture 계산한다
    T4  편향 부분표본(NOT NULL만)을 전체 대신 분석한다
    T5  타임존 불일치(KST vs UTC 문자열 비교)
    T6  테이블 간 전파 갭(canonical vs learning 커버리지 상이)
    T7  퇴역 필드를 라이브 축으로 읽는다

  이 도구는 그 실패 모드를 데이터에서 직접 재고, 어디가 위반인지 지도로 낸다.
  분석 판정을 내리기 전에 이 감사를 먼저 통과시킨다 — 그러면 몇 번째 함정인지 셀 필요가 없다.

전부 읽기 전용.
  python tools/analysis_trust_audit.py
"""

import argparse
import datetime as dt
import sqlite3
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"


def _con(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    c.execute("PRAGMA busy_timeout=50000")
    c.row_factory = sqlite3.Row
    return c


def t6_propagation_gaps(m: sqlite3.Connection) -> None:
    print("[T6] 테이블 간 전파 갭 — 같은 지표인데 커버리지가 다르면 어느 쪽을 읽느냐로 결론이 갈린다")
    tot = m.execute("SELECT COUNT(*) FROM v2_canonical_performance WHERE closed=1").fetchone()[0]
    for col in ("mfe_pct", "mae_pct", "pnl_pct_net", "pnl_pct", "mfe_time", "fee_pct_round_trip"):
        try:
            c = m.execute(f"SELECT SUM({col} IS NOT NULL) FROM v2_canonical_performance WHERE closed=1").fetchone()[0] or 0
            l = m.execute(f"SELECT SUM({col} IS NOT NULL) FROM v2_learning_performance WHERE closed=1").fetchone()[0] or 0
        except sqlite3.Error:
            continue
        flag = "  ★갭>10%" if abs(c - l) > tot * 0.1 else ""
        print(f"    {col:20s} canonical {c:4d} · learning {l:4d} / {tot}{flag}")


def t3_backfill_mfe(m: sqlite3.Connection) -> None:
    print("\n[T3] backfill vs live-observed MFE — capture(=net/MFE)는 live-observed 에만 유효")
    r = m.execute(
        "SELECT SUM(mfe_pct IS NOT NULL) has, "
        "SUM(mfe_pct IS NOT NULL AND mfe_time IS NOT NULL AND mfe_time!='') live "
        "FROM v2_learning_performance WHERE closed=1").fetchone()
    has, live = r["has"] or 0, r["live"] or 0
    back = has - live
    print(f"    mfe_pct 보유 {has} 중 live-observed {live} · backfill {back} ({back/max(has,1)*100:.0f}% backfill)")
    if has and live == 0:
        print("    ★★ mfe_time 0건 — 저장소 전 capture 분석이 100% backfill MFE 위에 있다(우리 보유기간 고점 아님)")
    elif back > has * 0.5:
        print("    ★ backfill 과반 — capture 분석은 live-observed 로 필터해야 한다(audit/mfe_trust.py)")


def t1_gross_only(m: sqlite3.Connection) -> None:
    print("\n[T1] gross만 있고 net 없는 행 — gross 로 판정하면 수수료·FX 를 무시한다")
    r = m.execute(
        "SELECT COUNT(*) n, SUM(pnl_pct_net IS NOT NULL) net, "
        "SUM(pnl_pct IS NOT NULL AND pnl_pct_net IS NULL) gross_only "
        "FROM v2_canonical_performance WHERE closed=1").fetchone()
    print(f"    closed {r['n']}: net 보유 {r['net']} · gross만(net없음) {r['gross_only']}")
    if r["gross_only"]:
        print(f"    → 그 {r['gross_only']}건은 net 판정 불가. gross 로 대체하면 안 된다.")


def t2_holding_period(m: sqlite3.Connection) -> None:
    print("\n[T2] 우리 실제 보유기간 — 고정 horizon forward 분석은 이 시장에 맞아야 한다")
    for mk in ("US", "KR"):
        holds = []
        for row in m.execute(
            "SELECT earliest_fill_at fa, last_closed_at ca FROM v2_canonical_performance "
            "WHERE closed=1 AND market=? AND earliest_fill_at IS NOT NULL AND last_closed_at IS NOT NULL", (mk,)):
            try:
                fa = dt.datetime.fromisoformat(str(row["fa"]).replace("Z", "+00:00"))
                ca = dt.datetime.fromisoformat(str(row["ca"]).replace("Z", "+00:00"))
                holds.append((ca - fa).total_seconds() / 3600)
            except (ValueError, TypeError):
                continue
        if not holds:
            continue
        med = st.median(holds)
        sub1 = sum(1 for x in holds if x < 1) / len(holds) * 100
        near = "60분" if med < 1.5 else ("240분" if med < 5 else "1440분")
        print(f"    {mk}: 보유 중앙 {med:.2f}h · 평균 {st.mean(holds):.2f}h · <1h {sub1:.0f}% (n{len(holds)})"
              f"  → 고정 horizon 은 {near}에 맞춰라. 1일(1440) 남용 주의")


def t7_retired_fields(a: sqlite3.Connection) -> None:
    print("\n[T7] 필드 의미 변화 — 같은 컬럼이 시점에 따라 다른 것을 담으면 라이브로 오독한다")
    print("    참고: claude_action 은 7/08 rule_direct 전환 이후 selection→judge 산출로 의미가 바뀌었다.")
    print("    (완전 퇴역이 아니라 '의미 변경'. 원장 필드는 시계열로 의미를 확인하고 읽는다)")


def summary(m: sqlite3.Connection) -> None:
    print("\n=== 판정 게이트 (분석 전 반드시 통과) ===")
    print("  ① net 인가?           pnl_pct_net 을 쓴다. gross(pnl_pct)로 라이브 판정 금지.")
    print("  ② 우리 horizon 인가?   시장별 보유 중앙([T2])에 맞춘 창을 쓴다. 1일 forward 남용 금지.")
    print("  ③ MFE 가 live 인가?    capture 는 mfe_time 있는 행만. backfill MFE 분모 금지(audit/mfe_trust).")
    print("  ④ 전체 표본인가?       NOT NULL 필터가 편향인지 확인. coverage 를 표기한다.")
    print("  ⑤ 테이블 일치?         canonical vs learning 커버리지 갭([T6]) 없는 쪽을 truth 로.")
    print("  ⑥ tz-aware 인가?       datetime 은 파싱 후 비교. 문자열 비교 금지(KST vs UTC).")
    print("\n  이 여섯을 통과 못 하면 그 '레버'는 거짓일 수 있다 — 오늘 3건 다 여기서 걸렸다.")


def main() -> int:
    argparse.ArgumentParser(description="분석 신뢰도 감사").parse_args()
    m = _con(ML_DB)
    a = _con(AUDIT_DB)
    if not m:
        print("ML DB 없음")
        return 1
    print("=== 분석 신뢰도 감사 — 거짓 레버 부류를 한 번에 뽑는다 ===\n")
    t6_propagation_gaps(m)
    t3_backfill_mfe(m)
    t1_gross_only(m)
    t2_holding_period(m)
    if a:
        t7_retired_fields(a)
    summary(m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
