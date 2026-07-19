"""LOSS_CAP/TARGET 손익 구조 전수 분해 — 수익 레버 추출. read-only."""
import sqlite3

conn = sqlite3.connect('file:data/ml/decisions.db?mode=ro', uri=True, timeout=10)
conn.execute('PRAGMA busy_timeout=5000')
conn.row_factory = sqlite3.Row
BASE = "FROM v2_learning_performance WHERE closed=1 AND pnl_krw_net IS NOT NULL"


def q(sql, args=()):
    return conn.execute(sql, args).fetchall()


def line(r, *keys):
    return "  " + "  ".join(str(r[k]) for k in keys)


tot = q(f"SELECT COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s {BASE}")[0]
print(f"=== 전체 closed+net: n={tot['n']} net={tot['s']}원 (learning_performance) ===\n")

print("=== [A] 시장 × 승/패 엔진 (net KRW) ===")
for r in q(f"SELECT market, "
           f"ROUND(SUM(CASE WHEN pnl_krw_net>0 THEN pnl_krw_net ELSE 0 END),0) win_krw, "
           f"SUM(CASE WHEN pnl_krw_net>0 THEN 1 ELSE 0 END) win_n, "
           f"ROUND(SUM(CASE WHEN pnl_krw_net<=0 THEN pnl_krw_net ELSE 0 END),0) loss_krw, "
           f"SUM(CASE WHEN pnl_krw_net<=0 THEN 1 ELSE 0 END) loss_n, "
           f"ROUND(SUM(pnl_krw_net),0) net {BASE} GROUP BY market"):
    print(f"  {r['market']}: 이익 {r['win_krw']}원({r['win_n']}건) | 손실 {r['loss_krw']}원({r['loss_n']}건) | net {r['net']}원")

print("\n=== [B] close_reason × market (net KRW) ===")
for r in q(f"SELECT close_reason, market, COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s, ROUND(AVG(pnl_pct_net),2) avg "
           f"{BASE} GROUP BY close_reason, market ORDER BY s"):
    print(f"  {str(r['close_reason'])[:22]:22} {r['market']} n={r['n']:>2} net={r['s']:>9}원 avg={r['avg']}%")

print("\n=== [C] LOSS_CAP 분해 (손실 지배원) ===")
LC = f"{BASE} AND close_reason='CLOSED_LOSS_CAP'"
lc = q(f"SELECT COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s, ROUND(AVG(pnl_pct_net),2) avg, ROUND(AVG(mfe_pct),2) mfe, ROUND(AVG(mae_pct),2) mae {LC}")[0]
print(f"  총 n={lc['n']} net={lc['s']}원 avg={lc['avg']}% | 평균 MFE={lc['mfe']}% MAE={lc['mae']}%")
print("  -- by market --")
for r in q(f"SELECT market, COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s, ROUND(AVG(mfe_pct),2) mfe {LC} GROUP BY market ORDER BY s"):
    print(f"     {r['market']}: n={r['n']} net={r['s']}원 평균MFE={r['mfe']}%")
print("  -- by strategy --")
for r in q(f"SELECT strategy, COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s {LC} GROUP BY strategy ORDER BY s"):
    print(f"     {str(r['strategy'])[:20]:20} n={r['n']} net={r['s']}원")
print("  -- by regime --")
for r in q(f"SELECT market_regime, COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s {LC} GROUP BY market_regime ORDER BY s"):
    print(f"     {str(r['market_regime'])[:22]:22} n={r['n']} net={r['s']}원")
print("  -- by timing_style --")
for r in q(f"SELECT timing_style, COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s {LC} GROUP BY timing_style ORDER BY s"):
    print(f"     {r['timing_style']}: n={r['n']} net={r['s']}원")

print("\n=== [D] LOSS_CAP: 포착 실패 vs 선별 실패 (MFE 버킷) ===")
print("  (MFE=진입후 최대 도달 이익. 높으면 '녹색 갔다가 반납'=포착실패, 낮으면 '애초에 안 됨'=선별실패)")
for lo, hi, lbl in [(-999, 0.5, "MFE<0.5% 애초에 안됨(선별)"), (0.5, 2.0, "0.5~2% 소폭 갔다 반납"), (2.0, 999, "MFE>2% 크게 갔다 반납(포착)")]:
    r = q(f"SELECT COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s, ROUND(AVG(mae_pct),2) mae {LC} AND mfe_pct>=? AND mfe_pct<?", (lo, hi))[0]
    print(f"     {lbl:26} n={r['n'] or 0} net={r['s'] or 0}원 평균MAE={r['mae']}%")

print("\n=== [E] TARGET 승리 엔진 분해 (+256k 원천) ===")
TG = f"{BASE} AND close_reason='CLOSED_CLAUDE_PRICE_TARGET'"
tg = q(f"SELECT COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s, ROUND(AVG(pnl_pct_net),2) avg, ROUND(AVG(mfe_pct),2) mfe, ROUND(AVG(mae_pct),2) mae {TG}")[0]
print(f"  총 n={tg['n']} net={tg['s']}원 avg={tg['avg']}% | 평균 MFE={tg['mfe']}% MAE={tg['mae']}%")
for dim in ('market', 'strategy', 'market_regime', 'timing_style'):
    parts = []
    for r in q(f"SELECT {dim} d, COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s {TG} GROUP BY {dim} ORDER BY s DESC"):
        parts.append(f"{str(r['d'])[:16]}:{r['n']}건/{r['s']}원")
    print(f"  -- {dim}: " + " | ".join(parts))

print("\n=== [F] 전략 × net (전체) — 어느 전략이 버나/잃나 ===")
for r in q(f"SELECT strategy, COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s, ROUND(AVG(pnl_pct_net),2) avg, "
           f"ROUND(100.0*SUM(CASE WHEN pnl_pct_net>0 THEN 1 ELSE 0 END)/COUNT(*),0) wr {BASE} GROUP BY strategy ORDER BY s"):
    print(f"  {str(r['strategy'])[:20]:20} n={r['n']:>3} net={r['s']:>9}원 avg={r['avg']}% 승률={r['wr']}%")

print("\n=== [G] 국면 × net (전체) — 어느 국면이 바닥 ===")
for r in q(f"SELECT market_regime, COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s, ROUND(AVG(pnl_pct_net),2) avg {BASE} GROUP BY market_regime ORDER BY s"):
    print(f"  {str(r['market_regime'])[:24]:24} n={r['n']:>3} net={r['s']:>9}원 avg={r['avg']}%")

print("\n=== [H] 티커 집중 — 반복 손실 종목 (LOSS_CAP+전체) ===")
for r in q(f"SELECT ticker, market, COUNT(*) n, ROUND(SUM(pnl_krw_net),0) s {BASE} GROUP BY ticker, market HAVING s<-20000 ORDER BY s LIMIT 10"):
    print(f"  {r['market']} {r['ticker']}: {r['n']}건 net={r['s']}원")

print("\n=== [I] 반사실 레버 — 특정 세그먼트 제거 시 net 개선 ===")
base_net = tot['s']
for label, cond in [
    ("LOSS_CAP 전량 제거", "close_reason='CLOSED_LOSS_CAP'"),
    ("MFE<0.5% 선별실패 제거", "close_reason='CLOSED_LOSS_CAP' AND mfe_pct<0.5"),
    ("최악 국면 제거(동적)", None),
    ("US만(KR 제외)", "market='KR'"),
]:
    if cond is None:
        continue
    removed = q(f"SELECT ROUND(SUM(pnl_krw_net),0) s {BASE} AND {cond}")[0]['s'] or 0
    print(f"  {label:28}: 제거분 {removed}원 → net {base_net} → {round(base_net-removed)}원")
worst = q(f"SELECT market_regime, ROUND(SUM(pnl_krw_net),0) s {BASE} GROUP BY market_regime ORDER BY s LIMIT 1")[0]
print(f"  최악국면 '{worst['market_regime']}' 제거: 제거분 {worst['s']}원 → net {round(base_net-worst['s'])}원")

conn.close()
