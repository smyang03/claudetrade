#!/usr/bin/env python3
"""KR 실적 회피 규칙(AVOID) — 급락 레인 원장 사후 재채점 (read-only, 2026-09-28).

## 사전등록 (결과 계산 전에 고정)

출처: T1-KR 사후 진단 — 같은 주 코호트 op_yoy 하위 절반이 상위 절반보다 H20 규모매칭 초과 −1.51%p
(185주, NW t −3.36). 사전등록 밖에서 나온 값이라 여기서 규칙으로 검증한다.

**규칙 AVOID**: KR 트레이드(신호일 D)를 제외한다 ⇔ 그 종목의 **가장 최근** 실적 공시
(잠정실적 `prelim` 또는 손익구조 30% 변경 `pl30`)가 다음을 모두 만족할 때:
- 공시일(`rcept_no[:8]`) < D (엄격), D − 공시일 ≤ 120일(달력).
- 같은 날 두 종류가 있으면 prelim을 쓴다. "가장 최근" 1건만 본다(그 1건이 하위가 아니면 유지).
- 그 공시의 op_yoy가 **자기 주 코호트**에서 하위 절반.
  코호트 = T1과 같은 주(`ed`의 W-SUN 주) 이면서 **공시일 < D**인 공시만(무 lookahead — 주 중간 신호면
  부분 코호트). 순위 = op_yoy + 1e-3·clip(dop_eq,±2) (T1 M0와 같은 정렬), pct-rank ≤ 0.5면 하위.
  코호트 크기 < 10이면 판정 불가 → **유지**(별도 집계). 코호트 구성에 y20/exit20 같은 사후값은 쓰지 않는다.
  op_yoy 결측 0건(확인됨) — 보정 규칙 불필요.
- 자격 공시가 없는 트레이드는 유지(비중 보고).

**검정 arm**: 1차 `xkr_fallen3`(넓은 풀), 2차 `c_kr_fallen_regime`, `c_kr_fallen5_nobio_rg`.
계약은 `virtual_books.STRATEGIES`에서 읽는다(세 arm 모두 hold 7 / TP12 / SL−25 실측).

**측정**: 트레이드 초과 = 계약 net − 규모매칭 계약 벤치(`research_universe_bench_20260923.py`와 같은 구성:
적격 = 거래대금 ≥20억·종가 ≥1,000원·이력 ≥25봉, 그날 dvol20 십분위, 같은 십분위 종목 ≥20개에 같은 계약).
- (a) 제외 − 유지: 날짜별 (제외 평균 − 유지 평균), 두 그룹이 모두 있는 날만. 날짜 클러스터 t(평범) +
  NW lag 6 t(보유 7세션 겹침 보정, 참고).
- (b) arm 일별 초과 전 vs 후: 전 = 그날 전 트레이드 평균, 후 = 유지 트레이드 평균. 유지가 0건인 날은
  쌍 검정에서 **제외**(일수 보고). ⚠️ 후−전 ≈ 제외비중 × (유지−제외)로 (a)와 기계적으로 묶인다 —
  독립 증거 두 개가 아니다.
- 기간 분할: 2025H2(≤2025-12-31) vs 2026.

**합격**: 1차 arm에서 (제외−유지) < 0 이고 |평범 t| ≥ 2.5, AND 후−전 쌍 t ≥ 2, AND 두 기간 모두 부호 유지
(제외−유지 < 0, 후−전 > 0). 2차 arm은 보조(판정 불참).

**OOS 아님**: 규칙은 2019–2026 T1 이벤트에서 발견됐고 원장(2025-06~2026-09)은 그 구간 안이다.
기간 분할은 안정성 확인일 뿐이다. forward(≥09-01)는 약 3주라 읽지 않는다.
**지평 불일치**: 발견은 H20, 여기 계약은 7세션 + TP/SL 절단. H5에선 두 절반 차가 작았다. 불합격이면
"7세션 계약에선 안 붙는다"이지 T1 발견이 틀렸다는 뜻이 아니다.
**데이터 한계**: events_features는 공시 2026-08-19까지(08-20 이후 공시 누락 → 9월 트레이드 일부가
'공시 없음'으로 분류될 수 있음, 월별 비중 보고). events_features는 가격 있는 종목·(종목,진입일) 중복 병합
후 표본이라 코호트가 원 DART 전량보다 작다(상폐 종목 빠짐).

산출: `data/analysis/kr_earnings_avoid_20260928/`(bench_szc.csv 캐시, trades_flagged.csv.gz, results.json),
리포트 `docs/reports/kr_earnings_avoid_20260928.md`.
"""
from __future__ import annotations

import bisect
import csv
import json
import math
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from tools.virtual_books import FEE_KR, HOLD_SESSIONS, SL, STRATEGIES, TP, contract_exit_v2  # noqa: E402

VB = ROOT / "data" / "shadow" / "virtual_books.db"
KR_DIR = ROOT / "data" / "price" / "kr"
EV = ROOT / "data" / "analysis" / "model_T1kr_earnings_20260928" / "events_features.csv.gz"
OUT = ROOT / "data" / "analysis" / "kr_earnings_avoid_20260928"
REP = ROOT / "docs" / "reports" / "kr_earnings_avoid_20260928.md"
ARMS = ["xkr_fallen3", "c_kr_fallen_regime", "c_kr_fallen5_nobio_rg"]
WINDOW, MIN_COHORT, START = 120, 10, "2025-05-01"
MIN_HISTORY, DVOL_MIN_EOK, PRICE_MIN, N_BUCKET = 25, 20.0, 1000.0, 10


def load_bars(path):
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.reader(fh):
            if len(r) >= 6 and r[0][:2] == "20":
                try:
                    rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                except ValueError:
                    continue
    return sorted(rows)


def build_bench(contracts):
    """(date, ticker, h, tp, sl) -> 규모매칭 계약 벤치. universe_bench와 같은 구성, START 이후 신호일만."""
    cache = OUT / "bench_szc.csv"
    if cache.exists():
        b = pd.read_csv(cache, dtype={"t": str})
        return {(r.d, r.t, r.h, r.tp, r.sl): r.v for r in b.itertuples()}
    recs = defaultdict(list)
    for p in sorted(KR_DIR.glob("kr_*.csv")):
        t = p.stem[3:]
        b = load_bars(p)
        for i in range(MIN_HISTORY, len(b) - 1):
            d, _o, _h, _l, c, v = b[i]
            if d < START or c < PRICE_MIN or c * v / 1e8 < DVOL_MIN_EOK or b[i + 1][1] <= 0:
                continue
            entry = b[i + 1][1]
            dv20 = st.mean(x[4] * x[5] for x in b[i - 19: i + 1]) / 1e8
            cr = {}
            for h, tp, sl in contracts:
                r_ = contract_exit_v2(entry, b[i + 1: i + 1 + h], fee=FEE_KR, be_lock=False, tp=tp, sl=sl, hold=h)
                if r_ is not None:
                    cr[(h, tp, sl)] = r_[0]
            recs[d].append((t, dv20, cr))
    out = {}
    rows = []
    for d, v in recs.items():
        order = sorted(v, key=lambda r: r[1])
        acc = defaultdict(lambda: [0.0, 0])
        dec_of = {}
        for j, r in enumerate(order):
            dec = min(N_BUCKET - 1, j * N_BUCKET // len(order))
            dec_of[r[0]] = dec
            for k, val in r[2].items():
                acc[(dec, *k)][0] += val
                acc[(dec, *k)][1] += 1
        for t, dec in dec_of.items():
            for k in contracts:
                a = acc.get((dec, *k))
                if a and a[1] >= 20:
                    out[(d, t, *k)] = a[0] / a[1]
                    rows.append((d, t, *k, a[0] / a[1]))
    pd.DataFrame(rows, columns=["d", "t", "h", "tp", "sl", "v"]).to_csv(cache, index=False)
    return out


def flag_trades(tr, ev):
    """tr: DataFrame(ticker, D). 반환: status ∈ {none, small, bottom, top}."""
    ev = ev.sort_values(["t", "dd", "kind_ord"])
    by_t = {t: g for t, g in ev.groupby("t")}
    wk_groups = {w: g for w, g in ev.groupby("wk")}
    status, ev_id = [], []
    for tk, D in zip(tr["ticker"], tr["D"]):
        g = by_t.get(tk)
        if g is None:
            status.append("none"); ev_id.append(None); continue
        prior = g[(g["dd"] < D) & ((D - g["dd"]).dt.days <= WINDOW)]
        if prior.empty:
            status.append("none"); ev_id.append(None); continue
        last_dd = prior["dd"].max()
        e = prior[prior["dd"] == last_dd].sort_values("kind_ord").iloc[0]  # 같은 날이면 prelim(0) 우선
        coh = wk_groups[e["wk"]]
        coh = coh[coh["dd"] < D]
        ev_id.append(e["rcept_no"])
        if len(coh) < MIN_COHORT:
            status.append("small"); continue
        pr = (coh["score"] < e["score"]).sum() + 0.5 * ((coh["score"] == e["score"]).sum() + 1)
        pct = pr / len(coh)
        status.append("bottom" if pct <= 0.5 else "top")
    return status, ev_id


def tstat(x):
    x = [v for v in x if v is not None and not math.isnan(v)]
    if len(x) < 5 or st.stdev(x) == 0:
        return (st.mean(x) if x else None), None, len(x)
    return st.mean(x), st.mean(x) / (st.stdev(x) / math.sqrt(len(x))), len(x)


def nw_t(x, lag=6):
    x = np.asarray([v for v in x if v is not None and not math.isnan(v)], float)
    n = len(x)
    if n < 10:
        return None
    e = x - x.mean()
    s = (e @ e) / n
    for L in range(1, lag + 1):
        s += 2 * (1 - L / (lag + 1)) * (e[L:] @ e[:-L]) / n
    return x.mean() / math.sqrt(s / n) if s > 0 else None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ev = pd.read_csv(EV, dtype={"t": str, "rcept_no": str}, usecols=["rcept_no", "t", "kind", "ed", "op_yoy", "dop_eq"])
    ev["ed"] = pd.to_datetime(ev["ed"])
    ev["dd"] = pd.to_datetime(ev["rcept_no"].str[:8])
    ev["wk"] = ev["ed"].dt.to_period("W-SUN").dt.start_time
    ev["kind_ord"] = (ev["kind"] != "prelim").astype(int)
    ev["score"] = ev["op_yoy"] + 1e-3 * ev["dop_eq"].fillna(0).clip(-2, 2)
    assert ev["op_yoy"].isna().sum() == 0

    sm = {s["id"]: s for s in STRATEGIES}
    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    q = ("SELECT strategy_id, session_date, ticker, net_pct FROM trades WHERE status='CLOSED' AND net_pct IS NOT NULL "
         f"AND strategy_id IN ({','.join('?' * len(ARMS))})")
    tr = pd.DataFrame(con.execute(q, ARMS).fetchall(), columns=["arm", "d", "ticker", "net"])
    con.close()
    tr["ticker"] = tr["ticker"].astype(str).str.zfill(6)
    tr["D"] = pd.to_datetime(tr["d"])
    tr["h"] = tr["arm"].map(lambda a: int(sm[a].get("hold", HOLD_SESSIONS)))
    tr["tp"] = tr["arm"].map(lambda a: float(sm[a].get("tp", TP)))
    tr["sl"] = tr["arm"].map(lambda a: float(sm[a].get("sl", SL)))

    # 1) 플래그 먼저 고정 (결과 열 미사용)
    u = tr[["ticker", "D"]].drop_duplicates().reset_index(drop=True)
    u["status"], u["ev"] = flag_trades(u, ev)
    tr = tr.merge(u, on=["ticker", "D"], how="left")

    # 2) 벤치
    contracts = sorted({(h, tp, sl) for h, tp, sl in zip(tr["h"], tr["tp"], tr["sl"])})
    bench = build_bench(contracts)
    tr["bench"] = [bench.get((d, t, h, tp, sl)) for d, t, h, tp, sl in zip(tr["d"], tr["ticker"], tr["h"], tr["tp"], tr["sl"])]
    tr["x"] = tr["net"] - tr["bench"]
    tr["excl"] = tr["status"] == "bottom"
    tr["per"] = np.where(tr["d"] <= "2025-12-31", "2025H2", "2026")
    tr.drop(columns=["D"]).to_csv(OUT / "trades_flagged.csv.gz", index=False)

    res = {}
    L = ["# KR 실적 회피 규칙(AVOID) — 급락 레인 사후 재채점 (2026-09-28)", "",
         "스크립트 `tools/research/research_kr_earnings_avoid_20260928.py`(사전등록은 docstring). "
         "데이터 `data/analysis/kr_earnings_avoid_20260928/`. 기존 파일 수정 없음.", ""]
    for arm in ARMS:
        a = tr[(tr["arm"] == arm) & tr["bench"].notna()]
        res[arm] = {"n_trades": int(len(tr[tr["arm"] == arm])), "n_with_bench": int(len(a)),
                    "status_share": {k: round(v, 4) for k, v in a["status"].value_counts(normalize=True).items()}}
        for per in ("all", "2025H2", "2026"):
            b = a if per == "all" else a[a["per"] == per]
            g = b.groupby(["d", "excl"])["x"].mean().unstack()
            diff = (g.get(True) - g.get(False)).dropna() if True in g and False in g else pd.Series(dtype=float)
            before = b.groupby("d")["x"].mean()
            after = b[~b["excl"]].groupby("d")["x"].mean()
            pair = (after - before.reindex(after.index)).dropna()
            m_d, t_d, n_d = tstat(diff.tolist())
            m_p, t_p, n_p = tstat(pair.tolist())
            mb, tb, _ = tstat(before.tolist())
            ma, ta, _ = tstat(after.tolist())
            res[arm][per] = {
                "trades": int(len(b)), "excl_share": round(float(b["excl"].mean()), 4) if len(b) else None,
                "kept_mean": round(float(b[~b["excl"]]["x"].mean()), 3), "excl_mean": round(float(b[b["excl"]]["x"].mean()), 3),
                "diff_day_mean": m_d, "diff_t": t_d, "diff_nw6": nw_t(diff.tolist()), "diff_days": n_d,
                "before_mean": mb, "before_t": tb, "after_mean": ma, "after_t": ta,
                "pair_mean": m_p, "pair_t": t_p, "pair_nw6": nw_t(pair.tolist()), "pair_days": n_p,
                "days_all_excluded": int(len(before) - len(after)),
            }
    mon = tr[tr["arm"] == "xkr_fallen3"].assign(m=lambda z: z["d"].str[:7]).groupby("m")["status"].value_counts(normalize=True).unstack().fillna(0).round(3)
    res["xkr_fallen3_status_by_month"] = mon.to_dict(orient="index")

    p = res["xkr_fallen3"]
    ok = (p["all"]["diff_day_mean"] is not None and p["all"]["diff_day_mean"] < 0 and abs(p["all"]["diff_t"] or 0) >= 2.5
          and (p["all"]["pair_t"] or 0) >= 2
          and all(p[x]["diff_day_mean"] is not None and p[x]["diff_day_mean"] < 0 and (p[x]["pair_mean"] or 0) > 0
                  for x in ("2025H2", "2026")))
    res["pass"] = bool(ok)
    json.dump(res, open(OUT / "results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)

    f = lambda v, d=2: "-" if v is None else f"{v:+.{d}f}"  # noqa: E731
    L += [f"## 판정: {'합격' if ok else '불합격'}", "",
          "| arm | 기간 | 트레이드 | 제외 비중 | 유지 평균 초과 | 제외 평균 초과 | 제외−유지 (일평균) | t | NW6 t | 일수 | "
          "arm 초과 전 | 후 | 후−전 | 쌍 t | NW6 t | 전부 제외된 날 |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for arm in ARMS:
        for per in ("all", "2025H2", "2026"):
            r = res[arm][per]
            L.append(f"| `{arm}` | {per} | {r['trades']:,} | {r['excl_share']:.1%} | {r['kept_mean']:+.2f}% | {r['excl_mean']:+.2f}% | "
                     f"{f(r['diff_day_mean'])}%p | {f(r['diff_t'])} | {f(r['diff_nw6'])} | {r['diff_days']} | "
                     f"{f(r['before_mean'])}% | {f(r['after_mean'])}% | {f(r['pair_mean'], 3)}%p | {f(r['pair_t'])} | "
                     f"{f(r['pair_nw6'])} | {r['days_all_excluded']} |")
    L += ["", "## 분류 비중 (벤치 있는 트레이드)", "",
          "none = 120일 내 공시 없음(유지) · small = 코호트 <10(유지) · top = 상위 절반(유지) · bottom = 하위 절반(제외)", ""]
    for arm in ARMS:
        L.append(f"- `{arm}`: " + ", ".join(f"{k} {v:.1%}" for k, v in res[arm]["status_share"].items())
                 + f" (벤치 결합 {res[arm]['n_with_bench']:,}/{res[arm]['n_trades']:,})")
    L += ["", "## xkr_fallen3 월별 분류 비중", "", "| 월 | " + " | ".join(mon.columns) + " |",
          "|---|" + "---:|" * len(mon.columns)]
    for m, r in mon.iterrows():
        L.append(f"| {m} | " + " | ".join(f"{v:.1%}" for v in r) + " |")
    (OUT / "report_tables.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print("pass", ok)


if __name__ == "__main__":
    raise SystemExit(main())
