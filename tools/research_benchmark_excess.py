#!/usr/bin/env python3
"""벤치마크 초과분 검증 — 전략 수익이 알파인가 시장 노출인가 (read-only).

배경(2026-09-11): `c_us_panic_all`이 날짜 클러스터 t 3.45로 유일하게 살아남은
전략이었다. 그런데 출구 격자를 보니 **보유를 늘릴수록 단조 개선**이었다
(D5 +0.34% -> D15 +0.83%, TP20 기준). 내부 최적점이 없는 단조는 알파가 아니라
시장 노출을 의심해야 한다. 같은 창의 지수 ETF 대비 초과분으로 가른다.

규약:
  · 벤치마크는 전략과 **같은 창**이다 — 진입일 시가 매수 → h세션 뒤 종가.
  · 전략 net에는 비용 0.50%(US)/0.25%(KR)가 이미 빠져 있고 벤치마크에는 없다.
    따라서 초과분은 비용만큼 과소평가다(gross 비교는 +비용 해서 읽는다).
  · 전략은 TP/SL/BE로 조기 청산할 수 있어 실제 보유가 h보다 짧을 수 있다.
    그래도 "그 창에 그냥 지수를 샀으면?"이 실무 질문이므로 이대로 비교한다.
  · 클러스터는 진입일과 월 둘 다 낸다. D10 이상은 보유가 겹쳐 날짜 t가 부풀려진다.

벤치마크 CSV는 2025-04-29부터라 그 이전 진입은 평가 불가(결측으로 보고).
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.selection_account_compare import read_bars  # noqa: E402
from tools.virtual_books import contract_exit_v2  # noqa: E402

_CACHE: dict[str, list] = {}


def bars(market, ticker):
    key = f"{market}:{str(ticker).upper()}"
    if key not in _CACHE:
        path = ROOT / "data" / "price" / market / f"{market}_{str(ticker).upper()}.csv"
        try:
            _CACHE[key] = read_bars(path, "2099-12-31", invalid_dates=set())
        except (OSError, ValueError):
            _CACHE[key] = []
    return _CACHE[key]


def benchmark_window(market, symbol, day, hold):
    """같은 창의 벤치마크 수익 — 진입일 시가 → hold세션 뒤 종가."""
    b = bars(market, symbol)
    idx = {x[0]: i for i, x in enumerate(b)}.get(day)
    if idx is None or idx + hold > len(b):
        return None
    return (b[idx + hold - 1][4] / b[idx][1] - 1) * 100


def cluster_t(pairs, keyfn):
    g = defaultdict(list)
    for day, value in pairs:
        g[keyfn(day)].append(value)
    means = [st.mean(v) for v in g.values()]
    if len(means) < 3 or st.stdev(means) == 0:
        return None, len(means)
    return st.mean(means) / (st.stdev(means) / math.sqrt(len(means))), len(means)


def line(label, pairs):
    if not pairs:
        return f"| {label} | 0 | - | - | - |"
    td, kd = cluster_t(pairs, lambda d: d)
    tm, km = cluster_t(pairs, lambda d: d[:7])
    f = lambda x: f"{x:.2f}" if x is not None else "-"  # noqa: E731
    return (f"| {label} | {len(pairs)} | {st.mean(v for _, v in pairs):+.2f}% | "
            f"{f(td)} (k{kd}) | {f(tm)} (k{km}) |")


def us_panic(lines):
    """US 패닉 레인 — 출구 격자 × 패닉 필터를 IWM 초과분으로."""
    p = (ROOT / "data/shadow/virtual_books.db").resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(p, uri=True)
    panic = {(r[0], r[1]) for r in con.execute(
        "SELECT session_date,ticker FROM trades WHERE strategy_id='c_us_panic_all'")}
    rows = list(con.execute(
        "SELECT session_date,ticker,entry_price FROM trades WHERE strategy_id='xus_fallen3' "
        "AND status='CLOSED' AND net_pct IS NOT NULL AND entry_price>0"))
    con.close()
    grid = [(tp, h) for tp in (12.0, 20.0) for h in (5, 7, 10, 15)]
    acc = {g: {"all": [], "panic": [], "rest": []} for g in grid}
    raw = {g: {"panic": [], "rest": []} for g in grid}
    for day, ticker, entry in rows:
        b = bars("us", ticker)
        if not b:
            continue
        i = next((j for j, x in enumerate(b) if x[0] == day), None)
        if i is None:
            continue
        tag = "panic" if (day, ticker) in panic else "rest"
        for tp, hold in grid:
            win = b[i:i + hold]
            if len(win) < hold:
                continue
            res = contract_exit_v2(entry, win, fee=0.50, be_lock=True, tp=tp, sl=-25.0, hold=hold)
            bm = benchmark_window("us", "IWM", day, hold)
            if res is None:
                continue
            raw[(tp, hold)][tag].append((day, res[0]))
            if bm is None:
                continue
            acc[(tp, hold)]["all"].append((day, res[0] - bm))
            acc[(tp, hold)][tag].append((day, res[0] - bm))
    lines.append("\n## US — `c_us_panic_all` (C6) 출구 격자 × IWM 초과분\n")
    lines.append("| TP / D | 전체풀 원net | 패닉 원net | 전체풀 초과 | 패닉 초과 | 패닉초과 t일 | 패닉초과 t월 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for tp, hold in grid:
        a, pn = acc[(tp, hold)]["all"], acc[(tp, hold)]["panic"]
        rp = raw[(tp, hold)]["panic"]
        allraw = raw[(tp, hold)]["panic"] + raw[(tp, hold)]["rest"]
        td, _ = cluster_t(pn, lambda d: d)
        tm, _ = cluster_t(pn, lambda d: d[:7])
        f = lambda x: f"{x:.2f}" if x is not None else "-"  # noqa: E731
        lines.append(
            f"| {tp:.0f} / D{hold} | {st.mean(v for _, v in allraw):+.2f}% | "
            f"{st.mean(v for _, v in rp):+.2f}% | {st.mean(v for _, v in a):+.2f}% | "
            f"**{st.mean(v for _, v in pn):+.2f}%** | {f(td)} | {f(tm)} |")
    return lines


def kr_pool(lines):
    """KR E5 풀 — 같은 창 KODEX200(069500) 초과분."""
    from tools.kr_fallen_ranking_experiment import CACHES
    from tools.research_kr_rank_pool_reconcile import build, by_session
    lines.append("\n## KR — E5 풀(R2∪R4) KODEX200 초과분 (계약 D5)\n")
    lines.append("| 구간 | n | 평균 | t(진입일) | t(월) |")
    lines.append("|---|---:|---:|---:|---:|")
    for year, path in CACHES.items():
        ss = by_session(build(json.loads(path.read_text(encoding="utf-8"))))
        raw, exc, miss = [], [], 0
        big_raw, big_exc = [], []
        for day, pool in ss.items():
            for c in pool:
                bm = benchmark_window("kr", "069500", c["entry_date"], 5)
                raw.append((day, c["net"]))
                if len(pool) >= 5:
                    big_raw.append((day, c["net"]))
                if bm is None:
                    miss += 1
                    continue
                exc.append((day, c["net"] - bm))
                if len(pool) >= 5:
                    big_exc.append((day, c["net"] - bm))
        lines.append(line(f"{year} 전체 · 원 net", raw))
        lines.append(line(f"{year} 전체 · 초과 (벤치결측 {miss})", exc))
        lines.append(line(f"{year} k≥5인 날 · 원 net", big_raw))
        lines.append(line(f"{year} k≥5인 날 · 초과", big_exc))
    return lines


def main():
    from datetime import date
    lines = [f"# 벤치마크 초과분 검증 ({date.today().isoformat()})", "",
             "read-only. 라이브 변경 없음. 벤치마크는 전략과 같은 창",
             "(진입일 시가 → h세션 뒤 종가). 전략 net에서만 비용이 빠져 있으므로",
             "초과분은 비용(US 0.50 / KR 0.25%p)만큼 과소평가다.",
             "벤치마크 CSV가 2025-04-29부터라 그 이전 진입은 평가 불가."]
    us_panic(lines)
    kr_pool(lines)
    out = ROOT / "docs" / "reports" / f"benchmark_excess_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
