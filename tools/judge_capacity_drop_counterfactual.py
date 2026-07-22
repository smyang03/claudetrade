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

    # ── 평균만 보면 틀린다 ───────────────────────────────────────────────
    # 우리 시스템은 예측이 아니라 볼록 수확(승/패 1.53, TARGET +5.07)으로 번다.
    # 평균이 0이어도 상단 꼬리가 두꺼우면 '사서 관리'가 이긴다. 실제 운용 방식대로
    # 손절 -2%를 먼저 적용하고 남은 것을 러너로 들고 가는 경로 시뮬을 함께 본다.
    print("\n=== 분포 (평균 뒤에 숨은 꼬리) ===")
    for h in horizons:
        v = sorted(per_h.get(h) or [])
        if not v:
            continue
        n = len(v)
        p = lambda q: v[min(n - 1, int(n * q))]  # noqa: E731
        big = sum(1 for x in v if x >= 3.0) / n * 100
        print(f"  {h*5:3d}분: p10 {p(0.10):+6.2f}%  중앙 {p(0.50):+6.2f}%  "
              f"p90 {p(0.90):+6.2f}%  최대 {v[-1]:+6.2f}%  |  +3%이상 {big:4.1f}%")

    print("\n=== 비대칭 관리 시뮬 (손절 -2% 선적용 → 러너 보유, gross) ===")
    print("  경로를 봉 단위로 훑어 손절이 먼저 닿으면 -2%로 확정한다.")
    for stop in (-2.0, -3.0, -4.0, -99.0):
        for h in horizons:
            realized: list[float] = []
            for ts, ticker in rows:
                s = series(ticker)
                if not s:
                    continue
                idx, close = s
                dtu = ts - timedelta(hours=9)
                pos = [i for i, t in enumerate(idx)
                       if t.to_pydatetime().replace(tzinfo=None) <= dtu]
                if not pos:
                    continue
                t0 = pos[-1]
                entry = float(close.iloc[t0])
                if entry <= 0 or t0 + h >= len(close):
                    continue
                out = None
                for k in range(t0 + 1, t0 + h + 1):
                    r = (float(close.iloc[k]) / entry - 1.0) * 100.0
                    if r <= stop:
                        out = stop
                        break
                realized.append(out if out is not None
                                else (float(close.iloc[t0 + h]) / entry - 1.0) * 100.0)
            if not realized:
                continue
            wins = [x for x in realized if x > 0]
            losses = [x for x in realized if x <= 0]
            payoff = (mean(wins) / abs(mean(losses))) if wins and losses else float("nan")
            print(f"  손절{stop:+.0f}% / {h*5:3d}분 보유: n={len(realized):4d}  "
                  f"평균 {mean(realized):+.3f}%  승률 "
                  f"{len(wins)/len(realized)*100:5.1f}%  승/패 {payoff:.2f}")

    # ── 초기 경로 분기 ──────────────────────────────────────────────────
    # [[six-visions-verified-20260719]]에서 초기 경로가 이후를 가른다는 것이
    # 실증됐다(d1 녹색 승률 76% vs 적색 30%, d1->d5 r=0.49). 사전 선별이 아니라
    # '사고 나서 초기 반응으로 분기'하는 축이므로, 버려진 풀에도 적용해본다.
    print("\n=== 초기 경로 분기 (진입 후 30분 반응으로 러너/컷, gross) ===")
    probe = 6  # 5분봉 6개 = 30분
    for h in horizons:
        if h <= probe:
            continue
        green: list[float] = []
        red: list[float] = []
        red_at_cut: list[float] = []   # 적색을 30분 시점에 실제로 끊었을 때의 손익
        for ts, ticker in rows:
            s = series(ticker)
            if not s:
                continue
            idx, close = s
            dtu = ts - timedelta(hours=9)
            pos = [i for i, t in enumerate(idx)
                   if t.to_pydatetime().replace(tzinfo=None) <= dtu]
            if not pos:
                continue
            t0 = pos[-1]
            entry = float(close.iloc[t0])
            if entry <= 0 or t0 + h >= len(close):
                continue
            mark = (float(close.iloc[t0 + probe]) / entry - 1.0) * 100.0
            final = (float(close.iloc[t0 + h]) / entry - 1.0) * 100.0
            if mark > 0:
                green.append(final)
            else:
                red.append(final)
                red_at_cut.append(mark)
        if not green or not red:
            continue
        gw = sum(1 for x in green if x > 0) / len(green) * 100
        rw = sum(1 for x in red if x > 0) / len(red) * 100
        print(f"  {h*5:3d}분 최종  |  30분 녹색 n={len(green):3d} {mean(green):+6.3f}% 승률 {gw:5.1f}%"
              f"   적색 n={len(red):3d} {mean(red):+6.3f}% 승률 {rw:5.1f}%"
              f"   격차 {mean(green)-mean(red):+.3f}%p")
        # 적색을 30분에 실제로 끊었다면: 녹색은 끝까지, 적색은 그 시점 손익으로 확정
        combined = green + red_at_cut
        hold_all = green + red
        print(f"           → 적색 30분 컷: {mean(combined):+.3f}%  "
              f"(전량 보유 {mean(hold_all):+.3f}%, 개선 {mean(combined)-mean(hold_all):+.3f}%p)")

    print("\n  판정: 평균이 0이어도 상단 꼬리가 두껍고 손절 적용 후 평균이 양수로")
    print("        돌아서면 '사서 관리'가 성립한다 — 그때는 예산이 기회를 깎는 것이다.")
    print("  한계: gross · yfinance 60일 창 · judge가 거부했을 수 있어 상한 추정치.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
