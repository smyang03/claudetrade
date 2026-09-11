#!/usr/bin/env python3
"""KR 급락 풀 가격 캐시 장기 백필 — yfinance 2018~ (read-only 외부 조회, 2026-09-12).

격차: E5 풀 재구성에 쓰는 `data/analysis/kr_fallen_price_cache*.json`이
2024-11부터다. 벤치마크(069500)는 09-11 백필로 2018년까지 열렸는데 **풀이 없어서**
초과분 OOS를 못 낸다. 이 도구가 그 병목을 푼다.

설계:
- 출력은 기존 캐시와 **완전히 같은 형식** — `{ticker: [{d,o,h,l,c,v,amt}, ...]}`.
  그래야 `research_kr_rank_pool_reconcile.build()`(E5 원본 일치 게이트 통과)를
  한 줄도 안 고치고 그대로 쓴다.
- `amt`는 yfinance가 안 주므로 **종가 × 거래량**으로 근사한다.
  실측(09-11, 5종목 171일 겹침): pykrx 거래대금 ÷ (종가×거래량) 중앙 1.0000, p90 1.04.
- 대상은 `data/price/kr/` CSV 보유 종목 전체(1,770) — 유니버스 811보다 넓다.
  유니버스 규칙(amt20·price_ge)은 `build()`가 **시점별로** 다시 걸므로,
  여기서 미리 좁히면 그게 곧 lookahead다.
- **라이브 캐시와 분리 저장** — `data/price_backfill_yf/kr_fallen_price_cache_2018_2026.json`.
  병합 금지. 라이브 스캔·정산 경로는 이 파일을 모른다.

⚠️ **생존편향**: 대상은 2026-09 현재 우리가 CSV를 들고 있는 종목이다. 급락 매수 전략은
"사서 죽은 종목"을 정확히 사는 전략이므로 좌측 꼬리가 통째로 빠진다.
09-12 실측 경계: DART 종목코드 표본 180개 중 유니버스 기준 충족 이력 92개에서
사라진 것 3개(**3.3%**). 단 표본의 35%는 yfinance가 보존하지 않아 **이 값은 하한**이다.
장기 결과는 위쪽으로 치우쳐 있고 그 크기는 미확정이다.

사용:
  python tools/backfill_kr_price_cache_yfinance.py --limit 50    # 파일럿
  python tools/backfill_kr_price_cache_yfinance.py               # 전체
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRICE_DIR = ROOT / "data" / "price" / "kr"
LIVE_CACHE = ROOT / "data" / "analysis" / "kr_fallen_price_cache.json"
OUT = ROOT / "data" / "price_backfill_yf" / "kr_fallen_price_cache_2018_2026.json"
MANIFEST = ROOT / "data" / "price_backfill_yf" / "manifest_kr_pool.json"


def targets():
    return sorted(p.stem[3:] for p in PRICE_DIR.glob("kr_*.csv") if len(p.stem) > 3)


def fetch_one(ticker, start, end):
    import yfinance as yf
    for suffix in (".KS", ".KQ"):
        try:
            frame = yf.Ticker(ticker + suffix).history(start=start, end=end, auto_adjust=True)
        except Exception:                                  # noqa: BLE001 — 외부 조회
            continue
        if frame is None or len(frame) < 30:
            continue
        bars = []
        for idx, r in frame.iterrows():
            try:
                o, h, low, c, v = (float(r["Open"]), float(r["High"]), float(r["Low"]),
                                   float(r["Close"]), float(r["Volume"]))
            except (TypeError, ValueError, KeyError):
                continue
            if min(o, h, low, c) <= 0:
                continue
            # amt = 종가 × 거래량 근사. 기존 캐시의 pykrx 거래대금과 중앙 1.0000 일치(실측).
            bars.append({"d": str(idx)[:10], "o": o, "h": h, "l": low, "c": c,
                         "v": v, "amt": c * v})
        if bars:
            bars.sort(key=lambda x: x["d"])
            return bars, suffix
    return [], None


def overlap_check(cache):
    """라이브 캐시(pykrx)와 겹치는 구간 대조 — 종가비·거래대금비."""
    if not LIVE_CACHE.exists():
        return {"error": "라이브 캐시 없음"}
    live = json.loads(LIVE_CACHE.read_text(encoding="utf-8"))
    close_ok = close_bad = 0
    amt_ratios, close_ratios = [], []
    for ticker, lbars in live.items():
        got = {b["d"]: b for b in cache.get(ticker, [])}
        pairs = [(b, got[b["d"]]) for b in lbars if b["d"] in got and got[b["d"]]["c"] > 0]
        if len(pairs) < 20:
            continue
        cr = st.median(l["c"] / g["c"] for l, g in pairs)
        if abs(cr - 1) <= 0.005:
            close_ok += 1
        else:
            close_bad += 1
        close_ratios.append(cr)
        ar = [l["amt"] / g["amt"] for l, g in pairs if g["amt"] > 0]
        if ar:
            amt_ratios.append(st.median(ar))
    total = close_ok + close_bad
    return {"compared_tickers": total,
            "close_match_pct": round(close_ok / total * 100, 2) if total else None,
            "close_ratio_median": round(st.median(close_ratios), 6) if close_ratios else None,
            "amt_ratio_median": round(st.median(amt_ratios), 4) if amt_ratios else None,
            "amt_ratio_p10_p90": ([round(sorted(amt_ratios)[len(amt_ratios) // 10], 3),
                                   round(sorted(amt_ratios)[-max(1, len(amt_ratios) // 10)], 3)]
                                  if len(amt_ratios) >= 10 else None)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--limit", type=int, default=0, help="파일럿용 종목 수 제한")
    ap.add_argument("--sleep", type=float, default=0.12)
    args = ap.parse_args(argv)
    end = args.end or date.today().isoformat()
    codes = targets()
    if args.limit:
        codes = codes[:args.limit]
    print(f"대상 {len(codes)}종목 · {args.start}~{end}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cache, empty, market = {}, [], {}
    for i, ticker in enumerate(codes):
        bars, suffix = fetch_one(ticker, args.start, end)
        if bars:
            cache[ticker] = bars
            market[ticker] = suffix
        else:
            empty.append(ticker)
        if (i + 1) % 100 == 0:
            OUT.write_text(json.dumps(cache), encoding="utf-8")
            print(f"  {i+1}/{len(codes)} 수집 {len(cache)} 결측 {len(empty)}", flush=True)
        time.sleep(args.sleep)
    OUT.write_text(json.dumps(cache), encoding="utf-8")
    spans = [(v[0]["d"], v[-1]["d"]) for v in cache.values() if v]
    manifest = {
        "source": "yfinance", "adjustment": "auto_adjust=True",
        "amt_definition": "close * volume (yfinance는 거래대금 미제공)",
        "window": [args.start, end], "targets": len(codes),
        "tickers_written": len(cache), "tickers_empty": len(empty),
        "earliest": min(s for s, _ in spans) if spans else None,
        "latest": max(e for _, e in spans) if spans else None,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "survivorship": {
            "note": "대상은 2026-09 현재 CSV 보유 종목 — 상폐분 누락(좌측 꼬리 결손).",
            "measured_lower_bound_pct": 3.3,
            "measurement": "DART 종목코드 표본 180 중 유니버스 기준 충족 이력 92개에서 사라진 것 3개",
            "caveat": "표본의 35%는 yfinance 미보존이라 3.3%는 하한이다."},
        "overlap_vs_live_pykrx_cache": overlap_check(cache),
        "note": "라이브 캐시와 분리 보관. 병합 금지. 연구 전용.",
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n수집 {len(cache)} / 결측 {len(empty)}")
    print(f"구간 {manifest['earliest']} ~ {manifest['latest']}")
    print(f"경계 대조: {manifest['overlap_vs_live_pykrx_cache']}")
    print(f"manifest: {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
