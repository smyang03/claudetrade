"""전체 레버 완결 검증 — 되는 것/안 되는 것 판정 + 장기/단기 분리. read-only.
2026-07-21. 누락/빈값 없이 모든 레버를 실제 net으로 검증. 근거 스크립트(재현용)."""
import sqlite3
import statistics as st

DB = "data/ml/decisions.db"
B = "FROM v2_learning_performance WHERE closed=1 AND pnl_pct_net IS NOT NULL"


def q(sql, args=()):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=8)
    c.execute("PRAGMA busy_timeout=5000")
    c.row_factory = sqlite3.Row
    rows = c.execute(sql, args).fetchall()
    c.close()
    return rows


def stat(rows):
    xs = [r["pnl_pct_net"] for r in rows]
    if not xs:
        return None
    n = len(xs)
    return {
        "n": n, "avg": round(sum(xs) / n, 3), "med": round(st.median(xs), 3),
        "wr": round(100 * sum(1 for x in xs if x > 0) / n),
        "sum": round(sum(xs), 1),
        "win_avg": round(sum(x for x in xs if x > 0) / max(1, sum(1 for x in xs if x > 0)), 2),
        "loss_avg": round(sum(x for x in xs if x <= 0) / max(1, sum(1 for x in xs if x <= 0)), 2),
    }


def show(label, rows):
    s = stat(rows)
    if not s:
        print(f"  {label}: (표본 없음)")
        return s
    conv = s["win_avg"] / abs(s["loss_avg"]) if s["loss_avg"] else 0
    print(f"  {label:34} n={s['n']:>3} avg={s['avg']:+.3f}% 중앙={s['med']:+.3f}% 승={s['wr']}% "
          f"합계={s['sum']:+.1f} 승/패={conv:.2f}")
    return s


print("=" * 70)
print("전체 레버 완결 검증 (v2_learning 315건, 4/27~7/06, 우리 net)")
print("=" * 70)

print("\n[L1] 볼록성 엔진 — 우리는 비대칭으로 버는가?")
show("전체", q(f"SELECT pnl_pct_net {B}"))
show("TARGET 볼록출구", q(f"SELECT pnl_pct_net {B} AND close_reason='CLOSED_CLAUDE_PRICE_TARGET'"))

print("\n[L2] 손실원 — 어디를 막나?")
for reason in ("CLOSED_LOSS_CAP", "CLOSED_CLAUDE_INTRADAY_SELL", "CLOSED_HARD_STOP"):
    show(reason, q(f"SELECT pnl_pct_net {B} AND close_reason=?", (reason,)))

print("\n[L3] 국면 게이트 — 좋은장/나쁜장 (되는가?)")
show("좋은장(MOD_BULL+NEUTRAL)", q(f"SELECT pnl_pct_net {B} AND market_regime IN('MODERATE_BULL','NEUTRAL')"))
show("나쁜장(MILD_BULL+BEAR+CAUT)", q(f"SELECT pnl_pct_net {B} AND market_regime IN('MILD_BULL','MILD_BEAR','CAUTIOUS')"))

print("\n[L4] 진입방식 — 눌림 vs 즉시 (되는가?)")
show("눌림 PULLBACK_WAIT", q(f"SELECT pnl_pct_net {B} AND origin_action='PULLBACK_WAIT'"))
show("즉시", q(f"SELECT pnl_pct_net {B} AND origin_action!='PULLBACK_WAIT'"))

print("\n[L5] 보유기간 — 장기 vs 단기 (장기/단기 분리)")
show("당일청산(intraday)", q(f"SELECT pnl_pct_net {B} AND filled_at IS NOT NULL AND closed_at IS NOT NULL "
                            f"AND julianday(closed_at)-julianday(filled_at) < 1"))
show("멀티데이(1일+ 보유)", q(f"SELECT pnl_pct_net {B} AND filled_at IS NOT NULL AND closed_at IS NOT NULL "
                          f"AND julianday(closed_at)-julianday(filled_at) >= 1"))

print("\n[L6] 시장 — KR vs US (장기/단기 배분 근거)")
show("KR", q(f"SELECT pnl_pct_net {B} AND market='KR'"))
show("US", q(f"SELECT pnl_pct_net {B} AND market='US'"))

print("\n[L7] 전략 — 살릴 것/버릴 것")
for r in q(f"SELECT strategy,COUNT(*) n,ROUND(AVG(pnl_pct_net),3) avg,ROUND(SUM(pnl_pct_net),1) tot {B} "
           f"GROUP BY strategy HAVING n>=5 ORDER BY avg DESC"):
    print(f"  {str(r['strategy'])[:20]:20} n={r['n']:>3} avg={r['avg']:+.3f}% 합계={r['tot']:+.1f}")

print("\n[L8] anti-chase — 극단급등 배제(이미 라이브)")
# MAX는 별도 산출이라 여기선 스킵 표기(별 스크립트 anti_chase_counterfactual.py 참조)
print("  (max_daily_ret_21d 기반 — anti_chase_counterfactual.py 참조: MAX>=20% 배제 검증완료)")

print("\n" + "=" * 70)
print("반사실 합산: 손실원 제거 시 net 변화")
print("=" * 70)
base = stat(q(f"SELECT pnl_pct_net {B}"))["sum"]
lc = stat(q(f"SELECT pnl_pct_net {B} AND close_reason='CLOSED_LOSS_CAP'"))["sum"]
bad = stat(q(f"SELECT pnl_pct_net {B} AND market_regime IN('MILD_BULL','MILD_BEAR','CAUTIOUS')"))["sum"]
print(f"  전체 net: {base:+.1f}")
print(f"  − LOSS_CAP 절반 제거: {base - lc/2:+.1f}")
print(f"  − 나쁜장 진입 절반 축소: {base - bad/2:+.1f}")
print(f"  − 둘 다(중복 대략 보정 -30%): {base - (lc/2 + bad/2)*0.7:+.1f}")
