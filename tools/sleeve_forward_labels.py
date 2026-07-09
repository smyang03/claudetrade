#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""후보군 패밀리 forward label 축적기 (시니어 검토 4순위-b, 2026-07-09).

"당장 매매하지 말고 forward label만 축적" — 매일 1회 실행(멱등):
  1) 스냅샷: low_vol(20d 수익률 표준편차 하위, 시장별 30종목) + pead(실적발표 D0/D-1 종목)
  2) 라벨링: 과거 스냅샷에 fwd 1/5/20 거래일 수익률이 계산 가능해지면 label 이벤트 append
quality 패밀리 = 정직 스킵(저장소에 재무제표 데이터 소스 부재 — 외부 통합은 별도 승인 사안).
격리: 로컬 CSV 읽기 + data/shadow/sleeve_forward_labels_{MKT}.jsonl 쓰기만.
"""
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tsmom_sleeve_tracker import PRICE, PREFIX, _closes  # noqa: E402
from tsmom_sleeve_backtest import kr_universe, us_universe  # noqa: E402

SHADOW = ROOT / "data" / "shadow"
CAL = ROOT / "data" / "earnings_calendar.json"
LOWVOL_N = 30
HORIZONS = (1, 5, 20)


def _file(m):
    return SHADOW / f"sleeve_forward_labels_{m}.jsonl"


def _load_events(m):
    f = _file(m)
    snaps, labels = [], set()
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event") == "snap":
                snaps.append(e)
            elif e.get("event") == "label":
                labels.add((e["snap_date"], e["symbol"], e["family"], e["horizon"]))
    return snaps, labels


def _append(m, obj):
    SHADOW.mkdir(parents=True, exist_ok=True)
    with open(_file(m), "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _snapshot(market, sig_universe):
    """오늘자 low_vol + pead 스냅샷. 반환 ref_date."""
    rows = {}
    for sym in sig_universe:
        d = _closes(market, sym)
        if not d:
            continue
        dates, px = d
        rets = [(px[i] / px[i - 1] - 1) * 100 for i in range(len(px) - 20, len(px)) if px[i - 1] > 0]
        if len(rets) < 15:
            continue
        rows[sym] = {"date": dates[-1], "px": px[-1], "vol20": st.pstdev(rets)}
    if not rows:
        return None
    ref_date = max(set(v["date"] for v in rows.values()),
                   key=lambda dd: sum(1 for v in rows.values() if v["date"] == dd))
    rows = {s: v for s, v in rows.items() if v["date"] == ref_date}

    snaps, _ = _load_events(market)
    done = {(e["snap_date"], e["family"]) for e in snaps}

    if (ref_date, "low_vol") not in done:
        for sym in sorted(rows, key=lambda s: rows[s]["vol20"])[:LOWVOL_N]:
            _append(market, {"event": "snap", "family": "low_vol", "snap_date": ref_date,
                             "symbol": sym, "px": rows[sym]["px"],
                             "vol20": round(rows[sym]["vol20"], 3)})

    if (ref_date, "pead") not in done and CAL.exists():
        cal = json.loads(CAL.read_text(encoding="utf-8"))
        book = cal.get("by_symbol" if market == "US" else "kr_by_code") or {}
        for sym, info in book.items():
            ed = str((info or {}).get("date") or "")
            if ed and ed <= ref_date and (ref_date <= ed or _days_between(ed, ref_date) <= 1):
                if sym in rows:
                    _append(market, {"event": "snap", "family": "pead", "snap_date": ref_date,
                                     "symbol": sym, "px": rows[sym]["px"],
                                     "earnings_date": ed,
                                     "eps_est": (info or {}).get("eps_estimate"),
                                     "eps_act": (info or {}).get("eps_actual")})
    return ref_date


def _days_between(a, b):
    from datetime import date
    try:
        da = date.fromisoformat(a)
        db = date.fromisoformat(b)
        return abs((db - da).days)
    except ValueError:
        return 999


def _label(market):
    snaps, labels = _load_events(market)
    n_new = 0
    cache: dict[str, tuple] = {}
    for e in snaps:
        sym = e["symbol"]
        if sym not in cache:
            cache[sym] = _closes(market, sym) or ([], [])
        dates, px = cache[sym]
        if e["snap_date"] not in dates:
            continue
        i0 = dates.index(e["snap_date"])
        for h in HORIZONS:
            key = (e["snap_date"], sym, e["family"], h)
            if key in labels or i0 + h >= len(px) or e["px"] <= 0:
                continue
            fwd = (px[i0 + h] / e["px"] - 1.0) * 100.0
            _append(market, {"event": "label", "snap_date": e["snap_date"], "symbol": sym,
                             "family": e["family"], "horizon": h, "fwd_pct": round(fwd, 3)})
            labels.add(key)
            n_new += 1
    return n_new


def main() -> None:
    print("quality 패밀리: 스킵(재무 데이터 소스 부재 — 정직 표기)")
    for m in ("KR", "US"):
        uni = us_universe() if m == "US" else kr_universe()
        ref = _snapshot(m, uni)
        n = _label(m)
        print(json.dumps({"market": m, "ref_date": ref, "new_labels": n}, ensure_ascii=False))


if __name__ == "__main__":
    main()
