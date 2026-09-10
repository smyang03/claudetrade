# -*- coding: utf-8 -*-
"""FINRA RegSHO 일별 장외물량 수집 (2026-09-11, US 장외 비중 축).

`https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt`
하루 한 파일에 전 종목(약 12,300행). 무료. 2023-01-03부터 보존 확인.
컬럼: Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market

⚠️ TotalVolume은 **통합 거래량이 아니라 FINRA 보고분(장외)**이다(09-11 실측: FNSQ+FNYX 합과 96~99% 일치).
장외 비중을 쓰려면 분모(통합 거래량)를 Alpaca 일봉에서 가져와야 한다.

거래일 목록은 Alpaca SPY 파일에서 뽑는다(우리가 실제로 분모를 가진 날만 수집하면 된다).
결과는 종목별 dict가 아니라 **날짜별 {symbol: [short, total]}** 로 JSONL에 쌓는다. 재실행 시 이미 받은 날짜는 건너뛴다.

사용: python tools/research/research_finra_offex_collect.py <out_jsonl> [--from=2023-01-01] [--to=2025-04-30] [--throttle=0.5]
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALPACA = ROOT / "data" / "price_backfill_alpaca" / "us"
URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"}
ABORT_STREAK = 6


def trading_days(lo: str, hi: str) -> list[str]:
    p = ALPACA / "us_SPY.csv"
    if not p.exists():
        return []
    out = []
    for r in csv.DictReader(p.open(encoding="utf-8-sig")):
        d = str(r.get("date") or "")[:10]
        if lo <= d <= hi:
            out.append(d)
    return sorted(set(out))


def fetch_day(d: str, timeout: float = 25.0) -> dict[str, list[float]]:
    url = URL.format(ymd=d.replace("-", ""))
    body = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read().decode("utf-8", "ignore")
    out: dict[str, list[float]] = {}
    for line in body.splitlines()[1:]:
        p = line.split("|")
        if len(p) < 5 or not p[1]:
            continue
        try:
            out[p[1]] = [float(p[2] or 0), float(p[4] or 0)]
        except ValueError:
            continue
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=")[0]: (a.split("=", 1)[1] if "=" in a else "") for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        return 1
    out = Path(args[0])
    lo = flags.get("--from", "2023-01-01")
    hi = flags.get("--to", "2025-04-30")
    throttle = float(flags.get("--throttle", "0.5") or 0.5)

    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("rows"):
                done.add(r["date"])
    days = [d for d in trading_days(lo, hi) if d not in done]
    print(f"[FINRA] 대상 {len(days)}일 ({lo}~{hi}, 이미 {len(done)}일)", flush=True)
    if not days:
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    ok = miss = streak = 0
    t0 = time.time()
    with out.open("a", encoding="utf-8") as fh:
        for i, d in enumerate(days, 1):
            rows: dict = {}
            try:
                rows = fetch_day(d)
            except Exception as exc:  # noqa: BLE001
                rows = {}
                err = type(exc).__name__
            else:
                err = ""
            fh.write(json.dumps({"date": d, "rows": rows, "err": err}, ensure_ascii=False) + "\n")
            fh.flush()
            if rows:
                ok += 1
                streak = 0
            else:
                miss += 1
                streak += 1
                if streak >= ABORT_STREAK:
                    print(f"[FINRA] 연속 실패 {streak}회 — 중단", flush=True)
                    break
            if i % 50 == 0 or i == len(days):
                el = time.time() - t0
                print(f"[FINRA] {i}/{len(days)} 성공 {ok} 실패 {miss} · {el:.0f}s "
                      f"(잔여 {el / i * (len(days) - i) / 60:.0f}분)", flush=True)
            time.sleep(throttle)
    print(f"[FINRA] 완료 — 성공 {ok} 실패 {miss} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
