#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""green-tape dropped 후보 forward 판독 (2026-07-10) — read-only.

judge 캡(10)이 버린 후보(early_judge_capacity_dropped / [green_tape_shadow] 로그)의
사후 경로를 yfinance 분봉으로 판독. "캡이 버린 게 돈이었나 = 캡 확장 실익 있나."
판정 기준(pre-reg): dropped n>=30 + 창구성 날 2회에서 우리규칙 근사 net 양수+월별일치 → 확장 근거.

로그에서 dropped 티커·시각 파싱 → drop 시각 이후 분봉 MFE/MAE/우리규칙 net.
표본 적으면 방향감각만(판정 아님). US 5m 60일 한계.
"""
import argparse
import glob
import re
import statistics as st
import warnings
from datetime import timezone, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
FEE_RT = {"US": 0.5, "KR": 0.3}
LOSS_CAP = -2.0

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\d+.*green_tape_shadow\] (US|KR) capacity_dropped.*tickers=\[([^\]]*)\]")


def _collect(since_day: str):
    """logs/system/live_trading_*.log에서 dropped 티커·시각 수집."""
    out = []
    for f in sorted(glob.glob(str(ROOT / "logs" / "system" / "live_trading_*.log"))):
        day = re.search(r"(\d{8})", Path(f).name)
        if not day or day.group(1) < since_day:
            continue
        try:
            for line in Path(f).read_text(encoding="utf-8", errors="ignore").splitlines():
                m = _LINE.match(line)
                if m:
                    ts, mk, tks = m.groups()
                    for t in re.findall(r"'([^']+)'", tks):
                        out.append((mk, t, ts))
        except OSError:
            continue
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="20260709")
    args = ap.parse_args()
    import yfinance as yf
    import pandas as pd

    drops = _collect(args.since)
    print(f"dropped 수집: {len(drops)}건 ({args.since}+)")
    if not drops:
        print("아직 dropped 표본 없음 — 캡 소진 세션 후 재실행")
        return

    rows = []
    for mk, tk, ts_kst in drops:
        sym = tk if mk == "US" else None
        if sym is None:
            continue  # KR은 .KS/.KQ 판별 생략(현재 dropped 대부분 US)
        try:
            du = pd.Timestamp(ts_kst, tz="Asia/Seoul").tz_convert("UTC")
            df = yf.download(sym, start=(du - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                             end=(du + pd.Timedelta(days=3)).strftime("%Y-%m-%d"),
                             interval="5m", progress=False, auto_adjust=False)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        fwd = df[df.index >= du]
        if len(fwd) < 2:
            continue
        e = float(fwd.iloc[0]["Open"])
        if e <= 0:
            continue
        cur = float(fwd.iloc[-1]["Close"])
        mfe = (float(fwd["High"].max()) - e) / e * 100
        mae = (float(fwd["Low"].min()) - e) / e * 100
        close_pct = (cur - e) / e * 100
        our = (LOSS_CAP if mae <= LOSS_CAP else close_pct) - FEE_RT[mk]
        rows.append({"tk": tk, "mfe": mfe, "mae": mae, "close": close_pct, "our": our})

    print(f"\n{'종목':7}{'MFE%':>8}{'MAE%':>8}{'마감%':>8}{'우리net':>9}")
    print("-" * 42)
    for r in rows:
        print(f"{r['tk']:7}{r['mfe']:+8.2f}{r['mae']:+8.2f}{r['close']:+8.2f}{r['our']:+9.2f}")
    print("-" * 42)
    if rows:
        ours = [r["our"] for r in rows]
        mfes = [r["mfe"] for r in rows]
        print(f"n={len(rows)} | 우리규칙 net 합 {sum(ours):+.2f} 중앙 {st.median(ours):+.2f} | MFE중앙 {st.median(mfes):+.2f}")
        print(f"판정(pre-reg): n>=30+창구성 2회에서 net 양수+월별일치 → judge 캡 확장 근거. 현재 n={len(rows)}=방향감각.")


if __name__ == "__main__":
    main()
