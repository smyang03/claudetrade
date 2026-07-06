# -*- coding: utf-8 -*-
"""KR 포켓(저변동 + zone상단 + 저confidence) forward 누적 판독 도구 (read-only).

배경(2026-07-07 재설계 토론 D5): 7/6 생DB 스캔에서 KR 정밀 포켓
(vol<3 + zone상단 + conf낮음)이 6월 +1.7로 나왔으나 전부 6월 단일이라 OOS 불가
→ forward 표본으로만 판정 가능. 이 도구는 세션마다 실행해 포켓/비포켓 net을
월별로 누적 출력한다. 매매·상태 무접촉(라이브 경로 아님).

라벨 정의(고정 — 판정 전 변경 금지):
  - zone_pos = (actual_entry_price - buy_zone_low) / (buy_zone_high - buy_zone_low)
    zone상단 = zone_pos >= 0.5 (zone 폭 0이면 1.0 취급)
  - conf_low = confidence <= 0.55
  - low_vol  = 진입 전일 기준 20거래일 일수익률 표준편차(%)가 누적 표본 내
    하위 1/3 (yfinance 일봉, 진입일 미포함 = no-lookahead).
    주: 7/6 스캔의 "vol<3" 절대컷은 다른 피처 스케일(20d stdev는 KR 표본
    3.6~13.8 분포로 3 미만 0건)이라 상대컷으로 고정 재정의(2026-07-07).

판정 기준(설계 문서 docs/reports/debate_profitability_redesign_20260707.md):
  포켓 forward n>=15 에서 비포켓 대비 mean/median 우위 + 양월 양수면 처방 논의 개시.
"""
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict

DB_URI = "file:data/v2_event_store.db?mode=ro"
VOL_WINDOW = 20
VOL_TERCILE = 1.0 / 3.0  # 누적 표본 내 vol20 하위 1/3 = 저변동
ZONE_TOP_CUT = 0.5
CONF_CUT = 0.55


def load_kr_closed():
    con = sqlite3.connect(DB_URI, uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    rows = con.execute(
        "SELECT decision_id, plan_json FROM v2_path_runs "
        "WHERE market='KR' AND status='CLOSED' ORDER BY updated_at"
    ).fetchall()
    con.close()
    byid = {}
    for did, pj in rows:  # decision_id dedup — 마지막 레코드 채택
        byid[did] = pj
    out = []
    for did, pj in byid.items():
        p = json.loads(pj)
        net = p.get("pnl_pct_net_after_fx_est")
        if net is None:
            net = p.get("pnl_pct_net_est")
        entry_at = p.get("entry_filled_at") or ""
        zone_lo, zone_hi = p.get("buy_zone_low"), p.get("buy_zone_high")
        entry_px = p.get("actual_entry_price")
        if net is None or not entry_at or entry_px is None:
            continue
        if zone_lo is None or zone_hi is None:
            zone_pos = None
        elif zone_hi <= zone_lo:
            zone_pos = 1.0
        else:
            zone_pos = (entry_px - zone_lo) / (zone_hi - zone_lo)
        out.append({
            "id": did,
            "ticker": p.get("ticker"),
            "edate": entry_at[:10],
            "ym": entry_at[:7],
            "net": float(net),
            "conf": p.get("confidence"),
            "zone_pos": zone_pos,
        })
    return out


def daily_vol_pct(ticker: str, edate: str, cache: dict):
    """진입 전일까지 20거래일 일수익률 표준편차(%). 실패 시 None."""
    try:
        import yfinance as yf
    except ImportError:
        return None
    if ticker not in cache:
        sym = f"{ticker}.KS"
        try:
            df = yf.download(sym, period="8mo", progress=False, auto_adjust=True)
            if df is None or len(df) < VOL_WINDOW + 2:
                sym = f"{ticker}.KQ"
                df = yf.download(sym, period="8mo", progress=False, auto_adjust=True)
            closes = df["Close"]
            if hasattr(closes, "columns"):
                closes = closes[sym]
            cache[ticker] = [(d.strftime("%Y-%m-%d"), float(v)) for d, v in closes.items()]
        except Exception:
            cache[ticker] = []
    series = cache[ticker]
    prior = [v for d, v in series if d < edate]
    if len(prior) < VOL_WINDOW + 1:
        return None
    tail = prior[-(VOL_WINDOW + 1):]
    rets = [100.0 * (tail[i] / tail[i - 1] - 1.0) for i in range(1, len(tail))]
    return st.pstdev(rets)


def agg(label, nets):
    if not nets:
        print(f"  {label}: n=0")
        return
    pos = sum(1 for x in nets if x > 0)
    print(f"  {label}: n={len(nets)} 합={sum(nets):+.2f} mean={st.mean(nets):+.3f} "
          f"med={st.median(nets):+.3f} win%={100 * pos / len(nets):.0f}")


def main():
    trades = load_kr_closed()
    print(f"=== KR 포켓 forward 판독 (KR CLOSED n={len(trades)}, dedup) ===")
    vol_cache = {}
    n_vol_missing = 0
    for t in trades:
        t["vol20"] = daily_vol_pct(t["ticker"], t["edate"], vol_cache)
        if t["vol20"] is None:
            n_vol_missing += 1
    vols = sorted(t["vol20"] for t in trades if t["vol20"] is not None)
    vol_cut = vols[max(0, int(len(vols) * VOL_TERCILE) - 1)] if vols else None
    print(f"vol 결측 {n_vol_missing}건 (포켓 판정 제외 처리) / "
          f"저변동 컷(하위 1/3) vol20<={vol_cut:.2f}" if vol_cut is not None else "vol 전량 결측")
    for t in trades:
        t["pocket"] = (
            vol_cut is not None
            and t["vol20"] is not None and t["vol20"] <= vol_cut
            and t["zone_pos"] is not None and t["zone_pos"] >= ZONE_TOP_CUT
            and t["conf"] is not None and t["conf"] <= CONF_CUT
        )

    pocket = [t for t in trades if t["pocket"]]
    rest = [t for t in trades if not t["pocket"]]
    print("\n[통산]")
    agg("포켓", [t["net"] for t in pocket])
    agg("비포켓", [t["net"] for t in rest])

    print("\n[월별]")
    bym = defaultdict(lambda: {"p": [], "r": []})
    for t in trades:
        bym[t["ym"]]["p" if t["pocket"] else "r"].append(t["net"])
    for ym in sorted(bym):
        agg(f"{ym} 포켓", bym[ym]["p"])
        agg(f"{ym} 비포켓", bym[ym]["r"])

    print("\n[포켓 구성 거래]")
    for t in sorted(pocket, key=lambda x: x["edate"]):
        print(f"  {t['edate']} {t['ticker']} net={t['net']:+.2f} "
              f"vol20={t['vol20']:.2f} zone_pos={t['zone_pos']:.2f} conf={t['conf']}")

    n_fwd = sum(1 for t in pocket if t["edate"] >= "2026-07-07")
    print(f"\nforward(2026-07-07 이후 진입) 포켓 표본: {n_fwd}건 / 판정 최소 15건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
