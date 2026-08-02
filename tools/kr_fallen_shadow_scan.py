"""KR 급락 반등 후보 shadow 스캔 (관측 전용 — 주문 없음).

2026-08-01 설계(docs/reports/design_candidate_selection_and_exit_20260801.md) K2 준비물.
8조건 (2026-08-01 실측, 검증기간 n=141 +3.35/건 — 단 피처 선정에 검증기간 참조된
경미한 누출이 있어 이 shadow forward 가 최종 판정이다):
  낙폭 5.27%+ / 갭하락 0.88%p+ / 종가위치 0.14+ / vol_spike 0.64+ /
  mom20 <= +4.72 / 고점20 대비 -21.22% 이하 / rv20 <= 6.24 / 종가 7,110원+
유동성: 20일 평균 거래대금 10억+, 하한가 잠김(-29.7% 초과) 제외.

사용:
  python tools/kr_fallen_shadow_scan.py --update-cache   # 가격 캐시 갱신(장 마감 후, ~10분)
  python tools/kr_fallen_shadow_scan.py --date 20260803  # 캐시 기반 당일 스캔(즉시)
  python tools/kr_fallen_shadow_scan.py --settle         # 만기(D5) 결과 채움(캐시 기반)

2026-08-01 재설계: 스캔 루프 안의 pykrx 호출이 타임아웃 없이 행 걸리는 것을 실측
(첫 실행 5시간 미완, 전종목 스냅샷 API는 이 환경에서 빈 값). 그래서 스캔·정산은
캐시(data/analysis/kr_fallen_price_cache.json)만 읽고, 네트워크는 --update-cache
한 단계로 격리한다(검증된 수집 패턴: 개별 실패 스킵, 25건마다 저장, flush 출력).
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "shadow" / "kr_fallen_shadow.jsonl"
CACHE = ROOT / "data" / "analysis" / "kr_fallen_price_cache.json"
UNIVERSE = ROOT / "data" / "analysis" / "kr_fallen_universe.json"
COST = 0.25

CONDS_DOC = {
    "drop_ge": 5.27, "gap_ge": 0.88, "close_pos_ge": 0.14, "vol_spike_ge": 0.64,
    "mom20_le": 4.72, "from_high20_le": -21.22, "rv20_le": 6.24, "price_ge": 7110.0,
}


def scan(date_str: str) -> int:
    day = datetime.strptime(date_str, "%Y%m%d")
    d_iso = day.strftime("%Y-%m-%d")
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    rows = []
    n_hasday = 0
    for code, bars in cache.items():
        if not bars or len(bars) < 25:
            continue
        idx = None
        for k in range(len(bars) - 1, max(-1, len(bars) - 4), -1):
            if bars[k]["d"] == d_iso:
                idx = k
                break
        if idx is None or idx < 22:
            continue
        n_hasday += 1
        b = bars[idx]
        prev = bars[idx - 1]["c"]
        if prev <= 0:
            continue
        chg = 100 * (b["c"] / prev - 1)
        if not (-29.7 <= chg <= -CONDS_DOC["drop_ge"]) or b["c"] < CONDS_DOC["price_ge"]:
            continue
        w20 = bars[idx - 20:idx]
        amt20 = sum(x["amt"] for x in w20) / 20
        if amt20 < 1e9:
            continue
        gap = 100 * (b["o"] / prev - 1)
        rng = b["h"] - b["l"]
        v20 = sum(x["v"] for x in w20) / 20
        hi20 = max(x["h"] for x in w20)
        rets = [100 * (w20[m]["c"] / w20[m - 1]["c"] - 1) for m in range(1, 20)
                if w20[m - 1]["c"] > 0]
        mom20 = 100 * (b["c"] / bars[idx - 21]["c"] - 1) if bars[idx - 21]["c"] > 0 else 0.0
        feats = {
            "chg": chg,
            "gap": gap,
            "close_pos": (b["c"] - b["l"]) / rng if rng > 0 else 0.5,
            "vol_spike": b["v"] / v20 if v20 > 0 else 1.0,
            "mom20": mom20,
            "from_high20": 100 * (b["c"] / hi20 - 1) if hi20 > 0 else 0.0,
            "rv20": st.pstdev(rets) if len(rets) > 3 else 99.0,
            "price": b["c"],
        }
        flags = {
            "drop": -chg >= CONDS_DOC["drop_ge"],
            "gap_down": -gap >= CONDS_DOC["gap_ge"],
            "close_pos": feats["close_pos"] >= CONDS_DOC["close_pos_ge"],
            "vol_spike": feats["vol_spike"] >= CONDS_DOC["vol_spike_ge"],
            "mom20": feats["mom20"] <= CONDS_DOC["mom20_le"],
            "from_high20": feats["from_high20"] <= CONDS_DOC["from_high20_le"],
            "rv20": feats["rv20"] <= CONDS_DOC["rv20_le"],
            "price": feats["price"] >= CONDS_DOC["price_ge"],
        }
        rows.append({
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "session_date": d_iso,
            "ticker": code,
            "pass_all": all(flags.values()),
            "pass_count": int(sum(flags.values())),
            "flags": flags,
            "feats": {k: round(v, 4) for k, v in feats.items()},
            "status": "PENDING",
            "entry_rule": "next_open",
            "exit_rule": "TP12_SL25_D5_cost0.25",
            "entry_price": None, "exit_price": None, "net_pct": None,
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # 재실행 중복 방지: 같은 (session_date, ticker)가 원장에 있으면 다시 쓰지 않는다
    # (append 모드라 --date 재실행 시 PENDING 중복행이 쌓여 정산·통계를 오염시키던 결함).
    existing: set[tuple[str, str]] = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                old = json.loads(line)
            except Exception:
                continue
            existing.add((str(old.get("session_date")), str(old.get("ticker"))))
    new_rows = [r for r in rows if (r["session_date"], r["ticker"]) not in existing]
    with open(OUT, "a", encoding="utf-8") as f:
        for r in new_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_pass = sum(1 for r in rows if r["pass_all"])
    n_dup = len(rows) - len(new_rows)
    print("캐시 내 %s 보유 %d종목 / 낙폭후보 %d건 기록 (8조건 전부 통과 %d건, 중복 스킵 %d건) -> %s" % (
        d_iso, n_hasday, len(new_rows), n_pass, n_dup, OUT))
    return 0


def update_cache() -> int:
    """가격 캐시 갱신 — 검증된 수집 패턴(개별 실패 스킵, 25건마다 저장, flush)."""
    import time

    from pykrx import stock
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=100)).strftime("%Y%m%d")
    ok = 0
    for i, code in enumerate(universe):
        try:
            df = stock.get_market_ohlcv(start, end, code)
        except Exception:
            time.sleep(0.3)
            continue
        bars = []
        if df is not None and len(df):
            has_amt = "거래대금" in df.columns
            for idx, r in df.iterrows():
                try:
                    o, h, l, c, v = (float(r["시가"]), float(r["고가"]), float(r["저가"]),
                                     float(r["종가"]), float(r["거래량"]))
                except Exception:
                    continue
                if c <= 0:
                    continue
                amt = float(r["거래대금"]) if has_amt else c * v
                bars.append({"d": str(idx)[:10], "o": o, "h": h, "l": l, "c": c,
                             "v": v, "amt": amt})
        if bars:
            old = {x["d"]: x for x in cache.get(code, [])}
            for x in bars:
                old[x["d"]] = x
            cache[code] = sorted(old.values(), key=lambda x: x["d"])
            ok += 1
        if (i + 1) % 25 == 0:
            CACHE.write_text(json.dumps(cache), encoding="utf-8")
            print("  %d/%d" % (i + 1, len(universe)), flush=True)
        time.sleep(0.15)
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    print("캐시 갱신 완료: %d/%d 종목" % (ok, len(universe)))
    return 0


def settle() -> int:
    if not OUT.exists():
        print("shadow 원장 없음")
        return 0
    lines = [json.loads(x) for x in OUT.read_text(encoding="utf-8").splitlines() if x.strip()]
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    changed = 0
    for r in lines:
        if r.get("status") != "PENDING":
            continue
        bars = cache.get(r["ticker"], [])
        after = [b for b in bars if b["d"] > r["session_date"]]
        if not after:
            continue
        e = after[0]["o"]
        if not e or e <= 0:
            r["status"] = "NO_ENTRY"
            changed += 1
            continue
        tp, sl = e * 1.12, e * 0.75
        win = after[:5]
        net = None
        kind = None
        for k, b in enumerate(win):
            if k > 0:
                if b["o"] <= sl:
                    net, kind = 100 * (b["o"] / e - 1) - COST, "gap_sl"
                    break
                if b["o"] >= tp:
                    net, kind = 100 * (b["o"] / e - 1) - COST, "gap_tp"
                    break
            if b["l"] <= sl:
                net, kind = 100 * (sl / e - 1) - COST, "sl"
                break
            if b["h"] >= tp:
                net, kind = 100 * (tp / e - 1) - COST, "tp"
                break
        if net is None:
            if len(win) < 5:
                continue  # 아직 만기 전
            net, kind = 100 * (win[-1]["c"] / e - 1) - COST, "time"
        r["entry_price"] = e
        r["net_pct"] = round(net, 4)
        r["exit_kind"] = kind
        r["status"] = "SETTLED"
        changed += 1
    if changed:
        with open(OUT, "w", encoding="utf-8") as f:
            for r in lines:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    settled = [r for r in lines if r.get("status") == "SETTLED" and r.get("pass_all")]
    if settled:
        v = [r["net_pct"] for r in settled]
        print("8조건 통과 만기 %d건: 평균 %.3f%% 승률 %.1f%%" % (
            len(v), sum(v) / len(v), 100 * sum(1 for x in v if x > 0) / len(v)))
    print("갱신 %d건" % changed)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--update-cache", action="store_true")
    ap.add_argument("--auto", action="store_true",
                    help="캐시 갱신 -> 당일 스캔 -> 만기 정산 일괄 실행 (스케줄러용)")
    args = ap.parse_args()
    date_str = str(args.date or "").replace("-", "")  # 스케줄러는 ISO(YYYY-MM-DD)로 넘긴다
    if args.auto:
        update_cache()
        scan(date_str)
        return settle()
    if args.update_cache:
        return update_cache()
    if args.settle:
        return settle()
    return scan(date_str)


if __name__ == "__main__":
    sys.exit(main())
