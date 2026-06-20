"""Codex KR shadow excess 리포트 — KOSPI/KOSDAQ 등락률 대비 초과수익.

- codex KR 스냅샷(event_type=codex_shadow_snapshot)을 read-only로 읽는다.
- KR yfinance는 절대가격 split 오염이 있으므로, 벤치는 지수 '등락률(%)'만 사용한다
  (등락률은 split factor가 분자분모 상쇄돼 robust). 픽 return_pct는 codex가 KIS 기반
  current_price로 계산한 값이라 깨끗하다.
- 종목별 KOSPI/KOSDAQ 상장 구분이 불확실하므로 두 지수 대비 excess를 모두 출력한다.

사용: python data/shadow/codex_shadow_kr_excess_report.py
"""
import json
from pathlib import Path

import pandas as pd
import yfinance as yf

SNAPS = Path("data/shadow/codex_shadow_snapshots_20260619_KR.jsonl")
OUT = Path("data/shadow/codex_shadow_kr_excess_report.json")
LOCKED_AT = "2026-06-19T09:05:29+09:00"  # book locked = entry 기준 시각


def load_snaps() -> list[dict]:
    rows = []
    if not SNAPS.exists():
        return rows
    for line in SNAPS.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("event_type") == "codex_shadow_snapshot":
            rows.append(o)
    return rows


def idx_series(sym: str) -> pd.Series:
    df = yf.download(sym, period="1d", interval="5m", progress=False, auto_adjust=False)
    if df.empty:
        return pd.Series(dtype="float64")
    c = df["Close"]
    c = c.iloc[:, 0] if hasattr(c, "columns") else c
    i = c.index
    i = i.tz_convert("UTC") if i.tz is not None else i.tz_localize("UTC")
    return pd.Series(c.values, index=i, dtype="float64")


def at(s: pd.Series, ts: str):
    if len(s) == 0:
        return None
    t = pd.Timestamp(ts).tz_convert("UTC")
    sub = s[s.index <= t]
    return float(sub.iloc[-1]) if len(sub) else None


def idx_ret(s: pd.Series, t0: str, t1: str):
    e, c = at(s, t0), at(s, t1)
    return (c - e) / e * 100.0 if (e and c) else None


def main() -> None:
    snaps = load_snaps()
    if not snaps:
        raise SystemExit("no codex KR snapshots")
    last = snaps[-1]
    wat = last["written_at"]
    kospi, kosdaq = idx_series("^KS11"), idx_series("^KQ11")
    kospi_ret = idx_ret(kospi, LOCKED_AT, wat)
    kosdaq_ret = idx_ret(kosdaq, LOCKED_AT, wat)

    # MFE/MAE: 전체 스냅샷 시계열에서 종목별 max/min return_pct
    mfe, mae = {}, {}
    for sn in snaps:
        for side in ("buy_shadow", "avoid_shadow"):
            for r in sn.get(side, []):
                t, rp = r.get("ticker"), r.get("return_pct")
                if t and rp is not None:
                    mfe[t] = max(mfe.get(t, rp), rp)
                    mae[t] = min(mae.get(t, rp), rp)

    def rows_for(side):
        out = []
        for r in last.get(side, []):
            t, rp = r.get("ticker"), r.get("return_pct")
            out.append({
                "ticker": t, "return_pct": rp,
                "excess_vs_kospi": round(rp - kospi_ret, 3) if (rp is not None and kospi_ret is not None) else None,
                "excess_vs_kosdaq": round(rp - kosdaq_ret, 3) if (rp is not None and kosdaq_ret is not None) else None,
                "mfe": round(mfe.get(t), 2) if t in mfe else None,
                "mae": round(mae.get(t), 2) if t in mae else None,
            })
        return out

    buy, avoid = rows_for("buy_shadow"), rows_for("avoid_shadow")

    def avg(rows, key):
        v = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(v) / len(v), 3) if v else None

    report = {
        "as_of": wat, "locked_at": LOCKED_AT, "n_snaps": len(snaps),
        "kospi_ret_pct": round(kospi_ret, 3) if kospi_ret is not None else None,
        "kosdaq_ret_pct": round(kosdaq_ret, 3) if kosdaq_ret is not None else None,
        "buy": {"n": len(buy), "avg_excess_vs_kospi": avg(buy, "excess_vs_kospi"),
                "avg_excess_vs_kosdaq": avg(buy, "excess_vs_kosdaq"), "rows": buy},
        "avoid": {"n": len(avoid), "avg_excess_vs_kospi": avg(avoid, "excess_vs_kospi"),
                  "avg_excess_vs_kosdaq": avg(avoid, "excess_vs_kosdaq"), "rows": avoid},
        "bot_kr_positions": last.get("bot_kr_positions"),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== Codex KR excess (as_of {wat}, snaps={len(snaps)}) ===")
    print(f"지수 등락(09:05~현재): KOSPI {kospi_ret:+.2f}% / KOSDAQ {kosdaq_ret:+.2f}%  (벤치=등락률, split무관)")
    for name, grp, rows in (("BUY", report["buy"], buy), ("AVOID", report["avoid"], avoid)):
        print(f"[{name}] avg excess vs KOSPI {grp['avg_excess_vs_kospi']:+.2f}pp / vs KOSDAQ {grp['avg_excess_vs_kosdaq']:+.2f}pp")
        for r in sorted(rows, key=lambda x: -(x["return_pct"] or -99)):
            print(f"   {r['ticker']:<7} ret {r['return_pct']:+.2f}% | exKOSPI {r['excess_vs_kospi']:+.2f} exKOSDAQ {r['excess_vs_kosdaq']:+.2f} | MFE {r['mfe']:+.2f} MAE {r['mae']:+.2f}")
    print(f"봇 KR 보유: {last.get('bot_kr_positions')}")


if __name__ == "__main__":
    main()
