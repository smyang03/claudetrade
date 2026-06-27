from __future__ import annotations

"""좌측꼬리(큰 손실) 원인 귀인 — 상관 군집 vs 개별 종목 (read-only).

"손실은 loss_cap 좌측꼬리가 전부"라는 진단을 한 단계 더 분해한다: 큰 손실이
① 같은 날 시장 동반하락(군집/레짐) 때문인지 ② 그 종목만의 문제(개별 손절 실패)인지를
판별해, correlation/레짐 노출제한이 맞는 처방인지 아니면 개별 손절·비용이 레버인지 가린다.

판별 방식: 각 좌측꼬리 거래에 대해 같은 (market, session_date)의 다른 청산 종목 net
평균을 본다. 동료도 동반 하락이면 군집(레짐), 동료는 양호한데 그 종목만 크게 깨졌으면
고립(개별). 입력은 decisions.db(v2_canonical_performance) 뿐, 외부 호출 없음. net은
실현 라벨이라 측정 전용.
"""

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"


@dataclass
class TailVerdict:
    market: str
    n_closed: int
    net_p10: float | None
    net_median: float | None
    tail_n: int
    tail_threshold: float
    tail_share_of_loss_pct: float | None   # 좌측꼬리가 전체 음수 net 합에서 차지하는 %
    cluster_n: int                         # 같은 날 동료도 동반 하락(레짐/군집)
    isolated_n: int                        # 동료는 양호한데 그 종목만(개별)
    solo_n: int                            # 그날 그 종목만 청산
    multi_loss_dates: int                  # 한 날 2건+ 동시 좌측꼬리 날짜 수
    verdict: str


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def _pct(vals: list[float], q: float) -> float | None:
    s = sorted(vals)
    if not s:
        return None
    i = q * (len(s) - 1)
    lo = int(i)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] * (1 - (i - lo)) + s[hi] * (i - lo)


def analyze(rows: list[dict[str, Any]], threshold: float, peer_drop: float) -> TailVerdict:
    market = str(rows[0].get("market") or "?")
    nets = [float(r["net"]) for r in rows]
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_date[str(r["date"])].append(r)

    losers = sorted((r for r in rows if float(r["net"]) <= threshold), key=lambda r: float(r["net"]))
    cluster = isolated = solo = 0
    detail: list[dict[str, Any]] = []
    for r in losers:
        peers = [float(p["net"]) for p in by_date[str(r["date"])] if p["ticker"] != r["ticker"]]
        if not peers:
            solo += 1
            tag = "solo"
            pm = None
        else:
            pm = mean(peers)
            if pm <= peer_drop:
                cluster += 1
                tag = "cluster"
            else:
                isolated += 1
                tag = "isolated"
        detail.append({"date": r["date"], "ticker": r["ticker"],
                       "net": round(float(r["net"]), 2),
                       "peer_mean": round(pm, 2) if pm is not None else None, "tag": tag})

    neg_sum = sum(x for x in nets if x < 0)
    tail_sum = sum(x for x in nets if x <= threshold)
    share = round(tail_sum / neg_sum * 100, 0) if neg_sum < 0 else None

    ld: dict[str, int] = defaultdict(int)
    for r in losers:
        ld[str(r["date"])] += 1
    multi = sum(1 for c in ld.values() if c >= 2)

    tot = cluster + isolated + solo
    if tot == 0:
        verdict = f"좌측꼬리(net<={threshold}%) 없음"
    else:
        cl_pct = cluster / tot * 100
        if cl_pct >= 60:
            verdict = (f"군집 {cluster}/{tot}({cl_pct:.0f}%) 우세 — 시장 동반하락(레짐) 성격. "
                       "종목 correlation보다 레짐 기반 노출축소가 처방")
        elif cl_pct <= 30:
            verdict = (f"개별 {isolated+solo}/{tot} 우세 — correlation 무용. "
                       "개별 종목 손절 타이밍·비용이 레버")
        else:
            verdict = f"혼재(군집 {cluster}/{tot}) — 레짐·개별 둘 다 기여"

    return TailVerdict(
        market=market, n_closed=len(rows),
        net_p10=round(_pct(nets, .1), 2) if nets else None,
        net_median=round(median(nets), 2) if nets else None,
        tail_n=len(losers), tail_threshold=threshold,
        tail_share_of_loss_pct=share,
        cluster_n=cluster, isolated_n=isolated, solo_n=solo,
        multi_loss_dates=multi, verdict=verdict,
    ), detail


def load(ml_db: Path) -> list[dict[str, Any]]:
    conn = _connect_ro(ml_db)
    try:
        rows = conn.execute(
            "SELECT market, session_date AS date, ticker, pnl_pct_net AS net, mae_pct "
            "FROM v2_canonical_performance "
            "WHERE closed=1 AND pnl_pct_net IS NOT NULL AND runtime_mode='live'"
        ).fetchall()
        cols = ["market", "date", "ticker", "net", "mae_pct"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="좌측꼬리 원인 귀인 (read-only)")
    ap.add_argument("--market", choices=["KR", "US", "both"], default="both")
    ap.add_argument("--threshold", type=float, default=-3.0, help="좌측꼬리 net%% 기준(기본 -3)")
    ap.add_argument("--peer-drop", type=float, default=-1.0, help="동료 평균이 이 값 이하면 군집(기본 -1)")
    ap.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    ap.add_argument("--detail", action="store_true", help="좌측꼬리 거래 개별 출력")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ml_db = Path(args.ml_db)
    if not ml_db.exists():
        print(f"[ERR] DB 없음: {ml_db}")
        return 2

    all_rows = load(ml_db)
    markets = ["KR", "US"] if args.market == "both" else [args.market]
    out = []
    for mkt in markets:
        recs = [r for r in all_rows if str(r["market"]).upper() == mkt]
        if not recs:
            continue
        verdict, detail = analyze(recs, args.threshold, args.peer_drop)
        out.append((verdict, detail))

    if args.json:
        print(json.dumps([{**asdict(v), "detail": d} for v, d in out], ensure_ascii=False, indent=2))
    else:
        print(f"=== 좌측꼬리 원인 귀인 (threshold={args.threshold}%, peer_drop={args.peer_drop}%) ===")
        for v, d in out:
            print(f"\n[{v.market}] closed={v.n_closed} net p10={v.net_p10} median={v.net_median}")
            print(f"  좌측꼬리 {v.tail_n}건, 전체손실합의 {v.tail_share_of_loss_pct}% 차지, "
                  f"동시손실 날짜 {v.multi_loss_dates}개")
            print(f"  군집 {v.cluster_n} / 고립 {v.isolated_n} / 단독 {v.solo_n}")
            print(f"  판정: {v.verdict}")
            if args.detail:
                for r in d:
                    pm = f"{r['peer_mean']:+.2f}" if r["peer_mean"] is not None else "n/a"
                    print(f"    {r['date']} {r['ticker']:>8} net={r['net']:+6.2f}% peer={pm} [{r['tag']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
