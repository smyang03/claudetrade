# -*- coding: utf-8 -*-
"""사전등록 docs/reports/preregistration_kr_tender_offer_20261120.md 판정 — KR 공개매수 차익.

prices : 대상 종목 네이버 일봉(수정주가) → data/analysis/kr_tender_offers/px (L7 캐시에 없는 것만)
test   : L7 엔진 Book(L7 캐시 + 보강 px). 진입 = 접수일 다음 거래일 시가(시가 ≥ 공개매수가면 미거래),
         청산 = 기간 종료일(이하 마지막 거래일) 종가. 비용 0.25%. 규모매칭 초과, 접수월 클러스터 t.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import research_kr_index_inclusion_20261030 as R  # noqa: E402

D = ROOT / "data" / "analysis" / "kr_tender_offers"
SRC = D / "kr_tender_offers.csv"
EXTRA = D / "px"
OUT = D / "test_result.json"


def _d(s: str) -> str:
    s = re.sub(r"[^0-9]", "", str(s or ""))
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else ""


def _f(s) -> float | None:
    try:
        v = float(str(s).replace(",", "").strip())
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def events() -> list[dict]:
    out = []
    for r in csv.DictReader(SRC.open(encoding="utf-8-sig")):
        mk = (r.get("market") or "").upper()
        if not any(k in mk for k in ("KOSPI", "KOSDAQ", "유가", "코스닥")) or "KONEX" in mk:
            continue
        # 개정 1(11-20, 결과 열람 전): 현금 공개매수만(교환공개매수 56건은 P*가 현금이 아님), KDR·신주인수권 단독 제외,
        # 결과 미확정(pending) 제외 — 분모에서도 뺀다.
        if (r.get("consideration") or "").strip() != "cash" or (r.get("outcome") or "") == "pending":
            continue
        sc = r.get("share_class") or ""
        if "KDR" in sc or ("신주인수권" in sc and "보통" not in sc):
            continue
        tk = re.sub(r"[^0-9A-Z]", "", str(r.get("target_ticker") or "").upper())
        out.append({"ticker": tk.zfill(6) if tk else "", "name": r.get("target_name"),
                    "announce": _d(r.get("announce_rcept_dt")), "end": _d(r.get("period_end")),
                    "price": _f(r.get("tender_price_krw")), "self": str(r.get("self_tender") or "").lower() in ("yes", "y", "true", "1"),
                    "cap": str(r.get("max_shares") or ""), "purpose": r.get("purpose") or "",
                    "revised": str(r.get("price_revised") or "").lower().startswith("y"),
                    "competing": str(r.get("competing_offer") or "").lower().startswith("y"),
                    "success": r.get("success") or r.get("result") or ""})
    return out


def cmd_prices() -> None:
    EXTRA.mkdir(parents=True, exist_ok=True)
    have = {p.stem for p in R.PX.glob("*.json")} | {p.stem for p in EXTRA.glob("*.json")}
    todo = sorted({e["ticker"] for e in events() if e["ticker"] and e["ticker"] not in have})
    for code in todo:                                   # 수익률 계산·출력 없음 — 봉만 저장
        u = (f"https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1"
             f"&startTime=20130101&endTime=20261120&timeframe=day")
        try:
            s = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read().decode("utf-8", "ignore")
        except Exception as exc:  # noqa: BLE001
            print(f"  {code} 실패 {type(exc).__name__}"); continue
        bars = [[f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}", float(m.group(2)), float(m.group(5)), float(m.group(6))]
                for m in re.finditer(r'\["(\d{8})",\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)', s)]
        (EXTRA / f"{code}.json").write_text(json.dumps(bars), encoding="utf-8")
        time.sleep(0.4)
    print(f"[tender] 봉 보강 {len(todo)}종목")


def load_book():
    bars = {}
    for d in (R.PX, EXTRA):
        for fp in d.glob("*.json"):
            b = json.loads(fp.read_text(encoding="utf-8"))
            if len(b) > 30:
                bars[fp.stem] = [tuple(x) for x in b]
    return R.Book(bars, [r[0] for r in bars["069500"]])


def summ(vals_by_month: dict) -> dict:
    ys = [float(np.mean(v)) for _, v in sorted(vals_by_month.items())]
    m, t, n = R.tstat(ys, 0)
    is_ = [float(np.mean(v)) for k, v in vals_by_month.items() if k < "2020"]
    oos = [float(np.mean(v)) for k, v in vals_by_month.items() if k >= "2020"]
    return {"mean": m, "t": t, "n": n, "pos": int(sum(y > 0 for y in ys)),
            "IS": list(R.tstat(is_, 0)), "OOS": list(R.tstat(oos, 0))}


def cmd_test() -> None:
    ev = events()
    bk = load_book(); cal = bk.cal
    st = defaultdict(int); recs = []
    for e in ev:
        if not (e["ticker"] and e["announce"] and e["end"] and e["price"]):
            st["unparsed"] += 1; continue
        if e["ticker"] not in bk.col:
            st["no_price"] += 1; continue
        s = bisect.bisect_right(cal, e["announce"]) - 1; en = s + 1
        x = bisect.bisect_right(cal, e["end"]) - 1
        if s < 0 or en >= len(cal) or x < en:
            st["window"] += 1; continue
        j = bk.col[e["ticker"]]
        o = bk.o[en, j]
        if not (o > 0) or bk.hist[s, j] == 0:
            st["no_bar"] += 1; continue
        if o >= e["price"]:
            st["no_spread"] += 1; continue
        h = x - en + 1
        raw = float(bk.fwd(j, en, h))
        if raw != raw:
            st["guard"] += 1; continue
        sb, _ = bk.size_bench(s, en, h, float(bk.dv20[s, j]))
        st["traded"] += 1
        recs.append({**e, "h": h, "raw_net": raw - R.COST, "ex_size": raw - R.COST - (sb if sb == sb else 0.0),
                     "spread": (e["price"] / o - 1) * 100, "gap": float((o / bk.c[s, j] - 1) * 100) if bk.c[s, j] > 0 else None})
    priced = st["traded"] + st["no_spread"]
    cover = priced / max(len(ev), 1)
    by = defaultdict(list)
    for r in recs:
        by[r["announce"][:7]].append(r["ex_size"])
    main = summ(by)
    if st["traded"] < 40 or cover < 0.80:
        verdict = "검정 불가"
    elif main["t"] >= 2.64 and main["IS"][0] > 0 and main["OOS"][0] > 0 and main["pos"] > main["n"] / 2:
        verdict = "합격"
    else:
        verdict = "기각"

    def grp(f):
        g = defaultdict(list)
        for r in recs:
            if f(r):
                g[r["announce"][:7]].append(r["ex_size"])
        return summ(g) if g else None
    full = lambda r: r["cap"].strip() == "전량" or "상장폐지" in r["purpose"]  # noqa: E731
    aux = {"raw_net": summ({k: [r["raw_net"] for r in recs if r["announce"][:7] == k] for k in by}),
           "full_or_delist": grp(full), "partial": grp(lambda r: not full(r)),
           "self": grp(lambda r: r["self"]), "third_party": grp(lambda r: not r["self"]),
           "ex_revised_competing": grp(lambda r: not (r["revised"] or r["competing"])),
           "gap_mean": float(np.nanmean([r["gap"] for r in recs if r["gap"] is not None])) if recs else None,
           "worst5": sorted(((round(r["raw_net"], 2), r["name"], r["announce"]) for r in recs))[:5],
           "spread_bins": {b: float(np.mean([r["ex_size"] for r in recs if lo <= r["spread"] < hi]))
                           for b, (lo, hi) in {"<2%": (0, 2), "2-5%": (2, 5), "5-10%": (5, 10), "10%+": (10, 1e9)}.items()
                           if any(lo <= r["spread"] < hi for r in recs)},
           "hold_median": float(np.median([r["h"] for r in recs])) if recs else None}
    res = {"events": len(ev), "status": dict(st), "coverage": round(cover, 3), "main": main, "verdict": verdict, "aux": aux}
    OUT.write_text(json.dumps({**res, "rows": recs}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["prices", "test"])
    a = ap.parse_args()
    cmd_prices() if a.cmd == "prices" else cmd_test()
