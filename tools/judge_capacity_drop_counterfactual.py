from __future__ import annotations

"""early judge 예산 소진으로 버려진 후보를 '그때 샀다면'으로 반사실 검증한다.

배경: 2026-07-22 진입 0의 상류를 추적하니 두 갈래였다 — 분석가 confidence 게이트(세션
단위 순열 p=0.87로 반증되어 현행 유지)와 judge 예산 소진이다. 후자는 로그에
  [green_tape_shadow] US capacity_dropped n=5 tickers=['HUT','LITE','CRDO','WDC','SPCX']
로 남는데, 이 중 HUT은 다음 날 반사실에서 TARGET +7.50%를 낸 종목이었다. 즉 예산이
좋은 후보를 버리고 있을 가능성이 있어, 버려진 건의 이후 성과를 실측한다.

판정 방향:
- 평균이 양수이고 세션 단위에서도 유지되면 예산(특히 시간당 15건)이 기회를 깎고 있다.
- 음수이거나 세션 단위에서 무너지면 예산은 죄가 없다. 거래 단위 평균은 특정 세션
  쏠림에 오염되므로 세션 단위를 함께 본다(2026-07-21 요일 게이트가 이 함정이었다).

한계(결론에 반드시 병기):
- gross다. 수수료·슬리피지·FX 미반영이라 우리 net 판정 기준이 아니다.
- yfinance 5분봉은 최근 60일만 제공한다.
- 'judge를 돌렸다면 샀다'가 아니다. judge가 거부했을 수도 있으므로 상한 추정치다.
- KR 종목은 yfinance 커버가 불완전해 기본은 US만 본다.

  python tools/judge_capacity_drop_counterfactual.py --horizons 12,24
"""

import argparse
import glob
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]

LINE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*capacity_dropped\s+n=\d+\s+tickers=\[(?P<tk>[^\]]*)\]"
)
MARKET = re.compile(r"\]\s+(US|KR)\s+capacity_dropped")


def collect(market: str) -> list[tuple[datetime, str]]:
    """로그에서 (버려진 시각, 종목)을 뽑는다. 같은 세션 내 중복은 첫 발생만 남긴다."""
    out: list[tuple[datetime, str]] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(glob.glob(str(ROOT / "logs" / "system" / "live_trading_*.log"))):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "capacity_dropped" not in line:
                continue
            mk = MARKET.search(line)
            if not mk or mk.group(1) != market:
                continue
            m = LINE.match(line)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            for raw in m.group("tk").split(","):
                ticker = raw.strip().strip("'\"")
                if not ticker:
                    continue
                key = (ts.strftime("%Y-%m-%d"), ticker)
                if key in seen:
                    continue
                seen.add(key)
                out.append((ts, ticker))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="judge 예산 소진 후보 반사실 검증")
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--horizons", default="12,24,48", help="5분봉 개수(12=1시간)")
    args = ap.parse_args()

    import yfinance as yf

    rows = collect(args.market)
    print(f"[{args.market}] capacity_dropped 기록 {len(rows)}건 "
          f"(세션-종목 중복 제거 후)")
    if not rows:
        print("표본이 없다.")
        return 0
    tickers = sorted({t for _, t in rows})
    print(f"대상 종목 {len(tickers)}개: {', '.join(tickers[:14])}"
          f"{' …' if len(tickers) > 14 else ''}")

    cache: dict = {}

    def series(tk: str):
        if tk in cache:
            return cache[tk]
        try:
            df = yf.download(tk, period="60d", interval="5m",
                             progress=False, auto_adjust=False)
            close = df["Close"].iloc[:, 0] if hasattr(df["Close"], "columns") else df["Close"]
            idx = df.index
            idx = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
            cache[tk] = (idx, close)
        except Exception:
            cache[tk] = None
        return cache[tk]

    horizons = [int(x) for x in str(args.horizons).split(",") if x.strip()]
    per_h: dict = defaultdict(list)
    per_session: dict = defaultdict(lambda: defaultdict(list))
    for ts, ticker in rows:
        s = series(ticker)
        if not s:
            continue
        idx, close = s
        # 로그는 KST다. UTC로 되돌려 봉을 맞춘다.
        dtu = ts - timedelta(hours=9)
        pos = [i for i, t in enumerate(idx)
               if t.to_pydatetime().replace(tzinfo=None) <= dtu]
        if not pos:
            continue
        t0 = pos[-1]
        entry = float(close.iloc[t0])
        if entry <= 0:
            continue
        day = ts.strftime("%Y-%m-%d")
        for h in horizons:
            if t0 + h < len(close):
                ret = (float(close.iloc[t0 + h]) / entry - 1.0) * 100.0
                per_h[h].append(ret)
                per_session[h][day].append(ret)

    print("\n=== 예산으로 버려진 후보를 '그때 샀다면' (gross) ===")
    for h in horizons:
        v = per_h.get(h)
        if not v:
            continue
        win = sum(1 for x in v if x > 0) / len(v) * 100
        print(f"  {h*5:3d}분 후: n={len(v):4d}  거래평균 {mean(v):+.3f}%  승률 {win:5.1f}%")

    print("\n=== 세션 단위 (같은 날 건은 독립이 아니다) ===")
    for h in horizons:
        sess = per_session.get(h) or {}
        if not sess:
            continue
        per = [mean(v) for v in sess.values()]
        pos = sum(1 for x in per if x > 0)
        print(f"  {h*5:3d}분 후: 세션 {len(per):3d}개  세션평균 {mean(per):+.3f}%  "
              f"양수 세션 {pos}/{len(per)}")

    print("\n  판정: 거래·세션 단위가 모두 양수여야 '예산이 기회를 깎는다'가 성립한다.")
    print("  한계: gross · yfinance 60일 창 · judge가 거부했을 수 있어 상한 추정치.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
