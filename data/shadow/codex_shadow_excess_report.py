"""Codex shadow 픽의 QQQ 대비 초과수익(excess) 사후 백필 리포트.

- 돌고 있는 러너/봇을 건드리지 않는다. snapshots/picks JSONL을 read-only로 읽고,
  yfinance QQQ 5분봉을 받아 snapshot 시각에 매칭해 excess를 계산한다.
- 절대 pnl이 아니라 QQQ 대비 초과수익으로 채점.
  BUY_SHADOW: excess>0 이면 시장(QQQ)을 이긴 것.
  AVOID_SHADOW: excess<0 이면 시장 대비 손실을 회피한 것(회피 알파 = -excess).
  BOT_ORDER/observation: 참고용 관찰.

사용: python data/shadow/codex_shadow_excess_report.py
"""
import json
from pathlib import Path

import pandas as pd
import yfinance as yf

SESSION_ID = "codex_shadow_US_20260618_0109KST"
PICKS = Path("data/shadow/codex_shadow_portfolio_20260618_US.jsonl")
SNAPS = Path("data/shadow/codex_shadow_snapshots_20260618_US.jsonl")
OUT = Path("data/shadow/codex_shadow_excess_report_20260618_US.json")
BENCH = "QQQ"


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("session_id") == SESSION_ID:
            rows.append(obj)
    return rows


def bench_series() -> pd.Series:
    df = yf.download(BENCH, period="1d", interval="5m", progress=False, auto_adjust=False)
    if df.empty:
        raise SystemExit("benchmark download empty")
    closes = df["Close"]
    if hasattr(closes, "columns"):
        closes = closes.iloc[:, 0]
    idx = closes.index
    idx = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
    return pd.Series(closes.values, index=idx, dtype="float64")


def at(series: pd.Series, ts_iso: str):
    """ts_iso 시각 이하의 마지막 5분봉 종가 (entry/snapshot 시각 매칭)."""
    t = pd.Timestamp(ts_iso).tz_convert("UTC")
    sub = series[series.index <= t]
    if len(sub) == 0:
        return None
    return float(sub.iloc[-1])


def main() -> None:
    picks = load_jsonl(PICKS)
    snaps = load_jsonl(SNAPS)
    qqq = bench_series()

    latest: dict[str, dict] = {}
    mfe: dict[str, float] = {}
    mae: dict[str, float] = {}
    for s in snaps:
        tk = s.get("ticker")
        if not tk:
            continue
        if tk not in latest or s.get("snapshot_at", "") > latest[tk].get("snapshot_at", ""):
            latest[tk] = s
        p = s.get("pnl_pct")
        if p is not None:
            mfe[tk] = max(mfe.get(tk, p), p)
            mae[tk] = min(mae.get(tk, p), p)

    rows = []
    for p in picks:
        tk = p.get("ticker")
        eq = at(qqq, p.get("created_at"))
        s = latest.get(tk)
        if not s or eq is None:
            continue
        cq = at(qqq, s.get("snapshot_at"))
        qqq_ret = (cq - eq) / eq * 100.0 if (cq and eq) else None
        pnl = s.get("pnl_pct")
        excess = (pnl - qqq_ret) if (pnl is not None and qqq_ret is not None) else None
        rows.append({
            "ticker": tk,
            "side": p.get("side"),
            "record_type": p.get("record_type"),
            "bucket": p.get("bucket"),
            "pnl_pct": pnl,
            "qqq_ret_pct": round(qqq_ret, 4) if qqq_ret is not None else None,
            "excess_pct": round(excess, 4) if excess is not None else None,
            "mfe_pct": round(mfe.get(tk), 4) if tk in mfe else None,
            "mae_pct": round(mae.get(tk), 4) if tk in mae else None,
            "snapshot_at": s.get("snapshot_at"),
        })

    # 버킷 분리: 봇 구조차단(예산/가드 검증) vs 내 독립판단
    BOT_GUARD = ("high_price_budget_block", "same_day_reentry_guard")

    def is_bot_guard(r):
        b = (r.get("bucket") or "")
        return any(k in b for k in BOT_GUARD)

    def agg(rows_in):
        vals = [r["excess_pct"] for r in rows_in if r["excess_pct"] is not None]
        if not vals:
            return None
        return {"n": len(vals), "avg_excess_pct": round(sum(vals) / len(vals), 4),
                "min": round(min(vals), 4), "max": round(max(vals), 4)}

    buy = [r for r in rows if r["side"] == "BUY_SHADOW"]
    avoid = [r for r in rows if r["side"] == "AVOID_SHADOW"]
    avoid_indep = [r for r in avoid if not is_bot_guard(r)]
    avoid_botguard = [r for r in avoid if is_bot_guard(r)]
    bot = [r for r in rows if r["side"] == "BOT_ORDER_ACKED_OBSERVE"]

    report = {
        "session_id": SESSION_ID, "benchmark": BENCH, "n_picks": len(rows),
        "buy_shadow": agg(buy),
        "avoid_independent": agg(avoid_indep),
        "avoid_bot_guard_verify": agg(avoid_botguard),
        "bot_observe": agg(bot),
        "rows": sorted(rows, key=lambda r: (r["side"], -(r["excess_pct"] or -999))),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== Codex Shadow 최종 결산 vs {BENCH} (n={len(rows)}) ===")
    labels = [("내 BUY", buy), ("내 독립 AVOID", avoid_indep),
              ("봇가드 검증 AVOID", avoid_botguard), ("봇 실주문 관찰", bot)]
    for name, grp in labels:
        a = agg(grp)
        if a:
            print(f"[{name:>16}] n={a['n']} avg_excess={a['avg_excess_pct']:+.3f}pp "
                  f"(min {a['min']:+.3f}, max {a['max']:+.3f})")
    print("-" * 78)
    for r in report["rows"]:
        print(f"{r['side']:>26} {r['ticker']:<6} excess {r['excess_pct']:+.3f}pp "
              f"(pnl {r['pnl_pct']:+.2f} qqq {r['qqq_ret_pct']:+.2f}) "
              f"MFE {r['mfe_pct']:+.2f} MAE {r['mae_pct']:+.2f} [{r['bucket']}]")


if __name__ == "__main__":
    main()
