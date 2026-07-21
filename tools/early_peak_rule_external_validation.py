from __future__ import annotations

"""조기고점 정리 룰을 우리 거래와 무관한 외부 표본으로 독립 검증한다.

우리 내부 근거는 US 159건·세션 27개로 표본이 작다(순열 p=0.0006이지만 기간이 4~7월 한 분기).
여기서는 yfinance 5분봉으로 "진입 후 초기 경로"를 대량 재구성해, 우리 계좌·전략과 무관하게
  "진입 직후 고점이 먼저 오고 되밀리면 이후가 나쁘다"
가 성립하는지 본다. 우리 체결·선정 로직이 개입하지 않으므로 완전한 독립 표본이다.

no-lookahead 규율:
- 판정에는 진입 시점 t부터 t+window 까지의 정보만 쓴다.
- 성과는 t+window '이후' 구간에서만 측정한다(판정 구간과 겹치지 않음).
- 세션 경계를 넘지 않는다(오버나이트 갭을 성과에 섞지 않는다).

한계(결론에 반드시 병기):
- yfinance 5분봉은 최근 60일만 제공 → 기간 편중.
- 수수료·슬리피지·FX 미반영 gross. 우리 net 판정 기준이 아니다.
- 진입 시점을 규칙적으로 샘플링하므로 실제 진입 분포와 다르다.

  python tools/early_peak_rule_external_validation.py --limit 40
"""

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTION_DB = ROOT / "data" / "ticker_selection_log.db"


def universe(limit: int) -> list[str]:
    """우리가 실제로 후보에 자주 올린 US 종목 — 성격이 같은 표본을 쓴다."""
    if not SELECTION_DB.exists():
        return ["AAPL", "NVDA", "AMD", "TSLA", "COIN"][:limit]
    con = sqlite3.connect(f"file:{SELECTION_DB}?mode=ro", uri=True, timeout=15)
    try:
        con.execute("PRAGMA busy_timeout=15000")
        rows = con.execute(
            """SELECT ticker, COUNT(*) c FROM ticker_selection_log
               WHERE market='US' AND date >= '2026-05-20'
               GROUP BY ticker ORDER BY c DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        con.close()
    return [str(r[0]).strip() for r in rows if str(r[0] or "").strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="조기고점 룰 외부 독립 검증")
    ap.add_argument("--limit", type=int, default=40, help="종목 수")
    ap.add_argument("--window-min", type=int, default=30, help="고점 판정 창(분)")
    ap.add_argument("--mfe-min-pct", type=float, default=0.2, help="MFE 문턱(%%)")
    ap.add_argument("--giveback-pct", type=float, default=0.2, help="고점 대비 되밀림(%%p)")
    ap.add_argument("--horizon-min", type=int, default=120, help="판정 후 성과 측정 구간(분)")
    ap.add_argument("--entry-every-min", type=int, default=30, help="진입 시점 샘플 간격(분)")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    import pandas as pd
    import yfinance as yf

    tickers = universe(args.limit)
    print(f"대상 {len(tickers)}종목: {', '.join(tickers[:12])}{' …' if len(tickers) > 12 else ''}")

    bars = int(args.window_min // 5)
    hor = int(args.horizon_min // 5)
    step = max(1, int(args.entry_every_min // 5))

    hit_rets: list[float] = []
    miss_rets: list[float] = []
    per_ticker: dict[str, list[float]] = defaultdict(list)
    sessions_seen = 0

    for i, ticker in enumerate(tickers, 1):
        try:
            df = yf.download(ticker, period="60d", interval="5m", progress=False, auto_adjust=False)
        except Exception:
            df = None
        if df is None or len(df) == 0:
            continue
        try:
            high = df["High"].iloc[:, 0] if hasattr(df["High"], "columns") else df["High"]
            low = df["Low"].iloc[:, 0] if hasattr(df["Low"], "columns") else df["Low"]
            close = df["Close"].iloc[:, 0] if hasattr(df["Close"], "columns") else df["Close"]
        except Exception:
            continue
        idx = df.index
        try:
            days = pd.Series(idx.date, index=idx)
        except Exception:
            continue

        for _day, day_idx in days.groupby(days):
            positions = [idx.get_loc(t) for t in day_idx.index]
            if len(positions) < bars + hor + 2:
                continue
            sessions_seen += 1
            start, end = positions[0], positions[-1]
            t = start
            while t + bars + hor <= end:
                entry = float(close.iloc[t])
                if entry <= 0:
                    t += step
                    continue
                # ── 판정 구간: t+1 … t+bars (진입 이후 정보만) ──
                seg_h = high.iloc[t + 1 : t + 1 + bars]
                seg_l = low.iloc[t + 1 : t + 1 + bars]
                if len(seg_h) < bars:
                    break
                mfe = (float(seg_h.max()) / entry - 1.0) * 100.0
                peak_pos = int(seg_h.values.argmax())
                trough_pos = int(seg_l.values.argmin())
                last = float(close.iloc[t + bars])
                gave_back = (mfe - ((last / entry - 1.0) * 100.0)) >= args.giveback_pct
                is_hit = (
                    mfe >= args.mfe_min_pct
                    and peak_pos < trough_pos          # 고점이 저점보다 먼저
                    and gave_back                      # 창 끝에서 되밀림 확인
                )
                # ── 성과 구간: 판정 이후만(겹치지 않음) ──
                fut = float(close.iloc[t + bars + hor])
                ret = (fut / last - 1.0) * 100.0       # 판정 시점 가격 대비
                (hit_rets if is_hit else miss_rets).append(ret)
                if is_hit:
                    per_ticker[ticker].append(ret)
                t += step
        if i % 10 == 0:
            print(f"  {i}/{len(tickers)} … 대상 {len(hit_rets)} / 비대상 {len(miss_rets)}")

    def stat(v: list[float], label: str) -> dict:
        if not v:
            print(f"  {label}: 표본 없음")
            return {}
        n = len(v)
        avg = sum(v) / n
        win = sum(1 for x in v if x > 0) / n * 100
        s = sorted(v)
        med = s[n // 2]
        print(f"  {label}: n={n:6d}  평균 {avg:+.4f}%  중앙 {med:+.4f}%  승률 {win:5.1f}%")
        return {"n": n, "avg": avg, "median": med, "win_rate": win}

    print(f"\n=== 외부 독립 검증 (세션 {sessions_seen}개, 창 {args.window_min}분 → 이후 {args.horizon_min}분) ===")
    h = stat(hit_rets, "룰 대상(조기고점+되밀림)")
    m = stat(miss_rets, "비대상                ")
    if h and m:
        diff = h["avg"] - m["avg"]
        print(f"\n  차이(대상 - 비대상): {diff:+.4f}%p")
        print("  → 음수이면 '조기고점 건은 이후가 더 나쁘다' = 룰이 지지된다.")
        pos_t = sum(1 for v in per_ticker.values() if sum(v) / len(v) > 0)
        print(f"  종목별 일관성: 대상 평균이 양수인 종목 {pos_t}/{len(per_ticker)}")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"hit": h, "miss": m, "sessions": sessions_seen,
                        "params": vars(args)}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    print("\n한계: yfinance 60일 창(기간 편중) · gross(수수료·슬리피지 미반영) · 진입시점 규칙 샘플링.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
