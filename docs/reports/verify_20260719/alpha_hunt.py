"""숨은 알파 탐색 — gross vs net, FX, 비용, 보유기간, 손절캡, 녹색 도달률. read-only."""
import sqlite3

conn = sqlite3.connect('file:data/ml/decisions.db?mode=ro', uri=True, timeout=10)
conn.execute('PRAGMA busy_timeout=5000')
conn.row_factory = sqlite3.Row
B = "FROM v2_learning_performance WHERE closed=1 AND pnl_pct_net IS NOT NULL"


def q(sql, a=()):
    return conn.execute(sql, a).fetchall()


print("=== [J] GROSS vs NET — 신호가 비용 전에 (+)인가? (핵심 알파 테스트) ===")
for r in q(f"SELECT market, COUNT(*) n, ROUND(AVG(pnl_pct),3) gross, ROUND(AVG(pnl_pct_net),3) net, "
           f"ROUND(AVG(pnl_pct)-AVG(pnl_pct_net),3) cost_gap, ROUND(AVG(fee_pct_round_trip),3) fee {B} GROUP BY market"):
    print(f"  {r['market']}: n={r['n']} gross={r['gross']}% net={r['net']}% (비용갭 {r['cost_gap']}%p, 왕복수수료 {r['fee']}%)")
r = q(f"SELECT ROUND(AVG(pnl_pct),3) g, ROUND(AVG(pnl_pct_net),3) net, ROUND(SUM(pnl_krw_net),0) s {B}")[0]
print(f"  전체: gross avg={r['g']}% net avg={r['net']}% netKRW={r['s']}")

print("\n=== [K] US FX 드래그 (fx_change_pct, US net의 얼마가 환율인가) ===")
for r in q(f"SELECT market, COUNT(*) n, ROUND(AVG(fx_change_pct),3) avg_fx, ROUND(SUM(fx_change_pct),1) sum_fx {B} AND fx_change_pct IS NOT NULL GROUP BY market"):
    print(f"  {r['market']}: n={r['n']} 평균FX={r['avg_fx']}%/건 합산FX기여={r['sum_fx']}%p")

print("\n=== [L] 비용 드래그 by close_reason (소폭 익절이 비용에 먹히나) ===")
for r in q(f"SELECT close_reason, COUNT(*) n, ROUND(AVG(pnl_pct),3) gross, ROUND(AVG(pnl_pct_net),3) net, "
           f"ROUND(AVG(pnl_pct)-AVG(pnl_pct_net),3) gap {B} GROUP BY close_reason HAVING n>=3 ORDER BY gap DESC"):
    print(f"  {str(r['close_reason'])[:22]:22} n={r['n']:>2} gross={r['gross']:>6}% net={r['net']:>6}% 비용갭={r['gap']}%p")

print("\n=== [M] 보유기간 net (당일청산 vs 멀티데이) ===")
rows = q(f"SELECT CASE WHEN julianday(closed_at)-julianday(filled_at) < 1 THEN '0_intraday' ELSE '1_multiday' END hold, "
         f"market, COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s, ROUND(AVG(pnl_pct_net),3) avg "
         f"{B} AND filled_at IS NOT NULL AND closed_at IS NOT NULL GROUP BY hold, market ORDER BY hold, market")
for r in rows:
    print(f"  {r['hold']:12} {r['market']}: n={r['n']:>3} netKRW={r['s']:>9} avg={r['avg']}%")

print("\n=== [N] MAE 분포: winner(TARGET) vs LOSS_CAP (손절캡이 winner 예비군을 죽이나) ===")
for lbl, cr in [("TARGET(winner)", "CLOSED_CLAUDE_PRICE_TARGET"), ("LOSS_CAP", "CLOSED_LOSS_CAP")]:
    r = q(f"SELECT COUNT(*) n, ROUND(AVG(mae_pct),2) mae, ROUND(MIN(mae_pct),2) worst, "
          f"SUM(CASE WHEN mae_pct<=-2.0 THEN 1 ELSE 0 END) breach2, "
          f"SUM(CASE WHEN mae_pct<=-3.0 THEN 1 ELSE 0 END) breach3 {B} AND close_reason=?", (cr,))[0]
    print(f"  {lbl:16} n={r['n']} 평균MAE={r['mae']}% 최악={r['worst']}% | MAE≤-2% {r['breach2']}건 ≤-3% {r['breach3']}건")
print("  → winner가 -2% 밑으로 자주 내려갔다면, -2% 손절캡은 winner 예비군을 죽이는 것")

print("\n=== [O] 녹색 도달률 — 전체 트레이드 중 MFE가 특정 이익 터치 비율 (포착 알파 크기) ===")
tot = q(f"SELECT COUNT(*) n {B} AND mfe_pct IS NOT NULL")[0]['n']
for thr in [0.5, 1.0, 2.0, 3.0, 5.0]:
    r = q(f"SELECT COUNT(*) n, ROUND(AVG(pnl_pct_net),2) net {B} AND mfe_pct>=?", (thr,))[0]
    print(f"  MFE≥{thr}% 터치: {r['n']}/{tot}건 ({round(100*r['n']/tot)}%), 이들 실현net avg={r['net']}%")
print("  → 대부분이 +2% 터치하는데 실현 net이 낮으면 = 포착 실패가 시스템 전반의 알파 누수")

print("\n=== [P] 국면 게이트 반사실 — MODERATE_BULL+CAUTIOUS_BEAR만 거래 시 ===")
base = q(f"SELECT ROUND(SUM(pnl_krw_net),0) s, COUNT(*) n {B}")[0]
good = q(f"SELECT ROUND(SUM(pnl_krw_net),0) s, COUNT(*) n {B} AND market_regime IN ('MODERATE_BULL','CAUTIOUS_BEAR')")[0]
print(f"  전체: {base['s']}원({base['n']}건) | MODERATE_BULL+CAUTIOUS_BEAR만: {good['s']}원({good['n']}건)")

print("\n=== [Q] TARGET까지 간 winner의 진입국면 vs 전체 진입국면 (국면이 winner 확률 바꾸나) ===")
print("  국면별: TARGET승 / 전체 / 승률")
for r in q(f"SELECT market_regime, "
           f"SUM(CASE WHEN close_reason='CLOSED_CLAUDE_PRICE_TARGET' THEN 1 ELSE 0 END) tgt, "
           f"COUNT(*) n {B} GROUP BY market_regime ORDER BY n DESC"):
    n = r['n']; tgt = r['tgt']
    print(f"     {str(r['market_regime'])[:20]:20} TARGET {tgt}/{n} ({round(100*tgt/n) if n else 0}%)")

conn.close()
