#!/usr/bin/env python3
"""KR 지수 ETF 일봉 과거 백필 — yfinance (2026-09-11).

격차: `data/price/kr/kr_069500.csv`가 2025-04-29부터다. 그래서 전략 판정을
**지수 초과분**으로 낼 때 그 이전 진입은 평가 자체가 불가하다
(09-11 실측: 2025 캐시 81건 중 50건 결측 → KR 초과분 OOS 확인 불가).

원칙(CLAUDE.md 외부 데이터 · backfill_us_daily_from_alpaca와 같은 규약):
- **라이브 수집 CSV와 분리 저장** — `data/price_backfill_yf/kr/`.
  라이브 지표·주문 경로는 이 디렉터리를 모르는 채로 둔다. 병합 금지.
- 출처·시점·수정방식을 manifest에 박는다.
- **경계 검증**: 라이브 CSV와 겹치는 구간의 종가를 대조해 수정주가 규약이
  같은지 실측한다. 일정비율 차이면 배당 소급 재계산 차이(무해, 수익률 불변),
  무작위 차이면 사용 금지.

사용:
  python tools/backfill_kr_index_from_yfinance.py            # 기본 2018-01-01~
  python tools/backfill_kr_index_from_yfinance.py --start 2010-01-01

read-only 외부 조회 전용 — live DB·주문 경로 접근 없음.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LIVE_DIR = ROOT / "data" / "price" / "kr"
OUT_DIR = ROOT / "data" / "price_backfill_yf" / "kr"
MANIFEST = ROOT / "data" / "price_backfill_yf" / "manifest_kr.json"

# 069500 KODEX 200(KOSPI200) / 229200 KODEX 코스닥150 — 둘 다 .KS로 조회된다(실측).
TICKERS = {"069500": "069500.KS", "229200": "229200.KS"}


def fetch(symbol, start, end):
    import yfinance as yf
    frame = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=True)
    rows = []
    for idx, r in frame.iterrows():
        d = str(idx)[:10]
        o, h, low, c, v = (float(r["Open"]), float(r["High"]), float(r["Low"]),
                           float(r["Close"]), float(r["Volume"]))
        if min(o, h, low, c) <= 0:
            continue
        rows.append((d, o, h, low, c, v))
    rows.sort()
    return rows


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    # 라이브 CSV와 같은 형식 — BOM + CRLF + 같은 헤더.
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for r in rows:
            w.writerow([r[0], r[1], r[2], r[3], r[4], int(r[5])])


def live_bars(ticker):
    from tools.selection_account_compare import read_bars
    path = LIVE_DIR / f"kr_{ticker}.csv"
    if not path.exists():
        return []
    try:
        return read_bars(path, "2099-12-31", invalid_dates=set())
    except (OSError, ValueError):
        return []


def boundary_check(ticker, fetched):
    """겹침 구간 종가 대조.

    yfinance auto_adjust는 배당 소급 재계산이라 라이브/재수집 비율이 **계단형**으로
    나온다(실측 069500: 1.006504 -> 1.002046 -> 1.000000). 계단은 무해하다 —
    계단 내부에서 수익률이 불변이기 때문이다. 진짜 문제는 계단으로 설명 안 되는
    개별 이상일이므로, 지배적 계단(각 1% 이상 차지)과 잔여 이상일을 나눠 센다.
    """
    live = {x[0]: x for x in live_bars(ticker)}
    got = {r[0]: r for r in fetched}
    overlap = sorted(set(live) & set(got))
    if not overlap:
        return {"overlap_days": 0, "verdict": "NO_OVERLAP"}
    ratios = [(d, live[d][4] / got[d][4]) for d in overlap if got[d][4] > 0]
    # 6자리 반올림 후 0.05% 이내를 한 계단으로 묶는다.
    levels: list[tuple[float, list[str]]] = []
    for d, r in sorted(ratios, key=lambda x: -x[1]):
        for lv in levels:
            if abs(r / lv[0] - 1) * 100 <= 0.05:
                lv[1].append(d)
                break
        else:
            levels.append((r, [d]))
    big = [(round(r, 6), len(ds)) for r, ds in levels if len(ds) >= max(3, len(ratios) * 0.01)]
    outliers = sorted(d for r, ds in levels if len(ds) < max(3, len(ratios) * 0.01) for d in ds)
    verdict = ("IDENTICAL" if len(big) == 1 and abs(big[0][0] - 1) * 100 < 0.01 and not outliers
               else "DIVIDEND_STEPS" if big and len(outliers) <= len(ratios) * 0.05
               else "RANDOM_MISMATCH")
    return {"overlap_days": len(overlap), "first": overlap[0], "last": overlap[-1],
            "steps": big, "outlier_days": len(outliers),
            "outlier_dates": outliers[:20], "verdict": verdict,
            "note": ("계단은 배당 소급 재계산 차이로 수익률에 무해하다. "
                     "잔여 이상일은 두 출처가 실제로 다른 날이므로 별도 확인 대상."
                     if verdict == "DIVIDEND_STEPS" else "")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default=None, help="미지정이면 오늘+1일")
    args = ap.parse_args(argv)
    end = args.end or (date.today().toordinal() + 1)
    end = args.end or date.fromordinal(end).isoformat()
    manifest = {"source": "yfinance", "adjustment": "auto_adjust=True",
                "window": [args.start, end],
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "note": "라이브 price CSV와 분리 보관. 병합 금지. 초과분 계산은 "
                        "이어붙이지 말고 이 파일 하나를 전 구간 단일 출처로 쓴다 "
                        "(배당 계단을 넘나드는 창의 왜곡 방지).",
                "tickers": {}}
    for ticker, symbol in TICKERS.items():
        try:
            rows = fetch(symbol, args.start, end)
        except Exception as exc:                       # noqa: BLE001 — 외부 조회
            print(f"  {ticker}({symbol}): 조회 실패 {type(exc).__name__}: {exc}", file=sys.stderr)
            manifest["tickers"][ticker] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if not rows:
            print(f"  {ticker}({symbol}): 0행", file=sys.stderr)
            manifest["tickers"][ticker] = {"rows": 0}
            continue
        write_csv(OUT_DIR / f"kr_{ticker}.csv", rows)
        chk = boundary_check(ticker, rows)
        manifest["tickers"][ticker] = {"symbol": symbol, "rows": len(rows),
                                       "first": rows[0][0], "last": rows[-1][0],
                                       "boundary": chk}
        print(f"  {ticker}({symbol}): {len(rows)}행 {rows[0][0]}~{rows[-1][0]}  "
              f"경계 {chk['verdict']} (겹침 {chk['overlap_days']}일, "
              f"계단 {chk.get('steps')}, 잔여 이상일 {chk.get('outlier_days')})")
        if chk.get("outlier_dates"):
            print(f"    이상일: {', '.join(chk['outlier_dates'])}")
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nmanifest: {MANIFEST}")
    bad = [t for t, v in manifest["tickers"].items()
           if (v.get("boundary") or {}).get("verdict") == "RANDOM_MISMATCH"]
    if bad:
        print(f"⚠ 무작위 불일치 — 사용 금지: {bad}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
