#!/usr/bin/env python3
"""가상 북 승격 게이트 검정 엔진 (사전등록 정본 개정 1, 2026-09-02).

`docs/reports/preregistered_promotion_gate_20260901.md`의 §2~§4를 계산한다. 09-02 재분석에서
"게이트가 문서뿐"이라는 Codex 지적을 메우는 배선이다. 선별·정산 함수는 `virtual_books`에서
**import해서 재사용**한다(복제하면 08-20처럼 조용히 갈라진다).

== 계산 규약 ==
- 판정 표본 = forward(backfill=0) CLOSED만. `--include-backfill`은 배관 검증 전용 라벨.
- 널 #2 (같은 세션·같은 K 무작위 픽): 세션별 paired 알파 = 픽 평균 − 통과 후보 풀 평균.
  무작위 K픽 평균의 기대값은 풀 평균이므로 표본 추출 없이 기대값으로 계산한다(분산은
  세션 부트스트랩이 담당). pick="all"(전량) arm은 풀=픽이라 알파가 항등 0 → 널 #1(현금 0%)로 판정.
- 블록 LCB: 세션 단위 stationary bootstrap(평균 블록 10세션, 10,000회, seed 고정),
  단측 95% 하한 = 부트스트랩 평균의 5백분위. 거래 세션 30개(블록 3개) 미만이면 INSUFFICIENT
  (--min-sessions로 낮추는 것은 배관 검증 전용).
- F0 max-stat: White Reality Check — 세션 인덱스를 arm 공통으로 재추출, arm별 (부트 평균 −
  관측 평균)의 최대값 분포 대비 관측 최대 알파의 p값. B 등 단일 가설 family는 단일 arm 부트 p.
- family 간 Holm(0.05). 결과는 JSON(`data/shadow/virtual_gate_eval_<date>.json`)에 code_commit·
  게이트 버전·널 방식을 박제한다.

사용: python tools/virtual_gate_eval.py [--include-backfill] [--reps 10000] [--seed 20260901]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import virtual_books as vb  # noqa: E402

GATE_VERSION = "preregistered_promotion_gate_20260901 개정 1 (2026-09-02)"
PARAMS = {
    "hurdle_net_pct": 0.25, "hurdle_pf": 1.20, "lcb_alpha": 0.05,
    "block_mean_sessions": 10, "n_boot": 10_000, "seed": 20260901,
    "stress_add_us_pct": 0.30, "stress_add_kr_pct": 0.20,  # 0.50→0.80 / KR +0.20p
    "top_sessions_excluded": 3, "single_month_share_max": 0.50, "maxdd_max_pct": 20.0,
    "min_sessions_for_bootstrap": 30,  # 평균 블록 10의 3배 — 그 아래선 부트 분산이 붕괴해 LCB가 과신
    "n_final_us": {"sessions": 80, "settled": 50, "trade_sessions": 40, "months": 3},
    "n_final_kr": {"sessions": 80, "settled": 30, "trade_sessions": 20, "months": 3},
    "early_warning_settled": 30,
}
FAMILY_OF = {"b2_leader_pb": "B_TREND_V1"}   # 나머지 활성 arm은 F0_FALLEN_V1
OUT_DIR = ROOT / "data" / "shadow"


# ── 순수 통계 함수 (테스트 대상) ─────────────────────────────────────────────
def stationary_bootstrap_indices(n: int, reps: int, block_mean: float,
                                 rng: np.random.Generator) -> np.ndarray:
    """Politis–Romano stationary bootstrap 인덱스 (reps × n). 원형 랩."""
    p = 1.0 / max(1.0, float(block_mean))
    out = np.empty((reps, n), dtype=np.int64)
    starts = rng.integers(0, n, size=(reps, n))
    restart = rng.random((reps, n)) < p
    out[:, 0] = starts[:, 0]
    for j in range(1, n):
        cont = (out[:, j - 1] + 1) % n
        out[:, j] = np.where(restart[:, j], starts[:, j], cont)
    return out


def block_lcb(values: np.ndarray, *, reps: int, block_mean: float, alpha: float,
              rng: np.random.Generator) -> tuple[float, float]:
    """세션 벡터의 stationary-bootstrap 평균 분포 → (관측 평균, 단측 (1-alpha) 하한)."""
    v = np.asarray(values, dtype=float)
    idx = stationary_bootstrap_indices(len(v), reps, block_mean, rng)
    means = v[idx].mean(axis=1)
    return float(v.mean()), float(np.quantile(means, alpha))


def bootstrap_p_one_sided(values: np.ndarray, *, reps: int, block_mean: float,
                          rng: np.random.Generator) -> float:
    """H0: 평균 ≤ 0. 중심화 부트(평균 − 관측)가 관측 평균 이상일 비율."""
    v = np.asarray(values, dtype=float)
    obs = float(v.mean())
    idx = stationary_bootstrap_indices(len(v), reps, block_mean, rng)
    centered = v[idx].mean(axis=1) - obs
    return float(np.mean(centered >= obs))


def reality_check_p(matrix: np.ndarray, *, reps: int, block_mean: float,
                    rng: np.random.Generator) -> tuple[float, int]:
    """White RC. matrix = 세션 × arm (NaN = 그 세션 미거래). 반환 (p, 최대 arm 인덱스)."""
    m = np.asarray(matrix, dtype=float)
    n_sess, _n_arm = m.shape
    obs = np.nanmean(m, axis=0)                       # arm별 관측 평균
    best = int(np.nanargmax(obs))
    idx = stationary_bootstrap_indices(n_sess, reps, block_mean, rng)
    sampled = m[idx]                                  # reps × n × arm
    with np.errstate(invalid="ignore"):
        boot_mean = np.nanmean(sampled, axis=1)       # reps × arm
    boot_mean = np.where(np.isnan(boot_mean), obs, boot_mean)
    centered_max = (boot_mean - obs).max(axis=1)
    return float(np.mean(centered_max >= obs[best])), best


def holm(pvals: dict[str, float], alpha: float = 0.05) -> dict[str, bool]:
    """Holm step-down. 반환: family → 기각(유의) 여부."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, bool] = {}
    stop = False
    for i, (k, p) in enumerate(items):
        thr = alpha / (m - i)
        if stop or p > thr:
            stop = True
            out[k] = False
        else:
            out[k] = True
    return out


# ── 데이터 수집 ───────────────────────────────────────────────────────────
class PoolResolver:
    """세션별 통과 후보 풀의 계약 net 목록 (널 #2용). 무거운 로더는 지연 초기화.
    테스트에서는 `fn(strategy, session_date) -> list[float]`로 대체한다."""

    def __init__(self, fn=None):
        self._fn = fn
        self._us = self._kr = self._slow = self._lp = None
        self._cache: dict[tuple, list[float]] = {}

    def nets(self, s: dict, sd: str) -> list[float]:
        if self._fn is not None:
            return list(self._fn(s, sd))
        key = (s["id"], sd)
        if key in self._cache:
            return self._cache[key]
        if self._us is None:
            self._us, self._kr = vb.load_sessions(), vb.load_kr_sessions()
            self._slow, self._lp = vb.load_slow_sessions(), vb.load_lp_sessions()
        market = vb.strategy_market(s)
        out: list[float] = []
        for c in vb.strategy_passers(s, self._us, self._kr, sd, self._slow, sessions_lp=self._lp):
            eo = vb.entry_of(c["ticker"], sd, market=market)
            if eo is None:
                continue
            res = vb.contract_exit_v2(eo[0], eo[1], fee=vb.FEE_KR if market == "KR" else vb.FEE_US,
                                      be_lock=(market != "KR"), tp=float(s.get("tp", vb.TP)),
                                      sl=float(s.get("sl", vb.SL)))
            if res is not None:
                out.append(float(res[0]))
        self._cache[key] = out
        return out


def market_sessions_since(con: sqlite3.Connection, market: str, start: str) -> int:
    """경과 시장 세션 수. US는 후보 풀 원장, 그 외는 가상 북 거래 세션 근사."""
    dates: set[str] = set()
    if market == "US":
        try:
            with closing(sqlite3.connect(f"file:{vb.POOL_DB}?mode=ro", uri=True, timeout=10)) as pcon:
                dates = {r[0] for r in pcon.execute(
                    "SELECT DISTINCT session_date FROM candidate_pool_all WHERE session_date>=?",
                    (start,))}
        except sqlite3.Error:
            pass
    if not dates:
        dates = {r[0] for r in con.execute(
            "SELECT DISTINCT session_date FROM trades WHERE session_date>=?", (start,))}
    return len(dates)


def months_elapsed(start: str, asof: str) -> float:
    a, b = datetime.strptime(start, "%Y-%m-%d"), datetime.strptime(asof, "%Y-%m-%d")
    return round((b - a).days / 30.44, 2)


def _session_alphas(s: dict, pool: PoolResolver, by_sess: dict[str, list[float]]) -> tuple[list[str], np.ndarray]:
    sess = sorted(by_sess)
    alphas = []
    for sd in sess:
        if s.get("pick") == "all":
            alphas.append(float(np.mean(by_sess[sd])))           # 널 #1 현금 0%
        else:
            pool_nets = pool.nets(s, sd)                          # 널 #2 기대값
            alphas.append(float(np.mean(by_sess[sd]) - (np.mean(pool_nets) if pool_nets else 0.0)))
    return sess, np.array(alphas)


def evaluate_strategy(con: sqlite3.Connection, s: dict, pool: PoolResolver, *,
                      include_backfill: bool, reps: int, rng: np.random.Generator,
                      asof: str) -> dict:
    market = vb.strategy_market(s)
    where = "strategy_id=? AND status='CLOSED'" + ("" if include_backfill else " AND backfill=0")
    rows = con.execute(
        f"SELECT session_date, ticker, net_pct, pnl_krw, pick_pos FROM trades WHERE {where} "
        "ORDER BY session_date", (s["id"],)).fetchall()
    res: dict = {"strategy_id": s["id"], "market": market,
                 "family": FAMILY_OF.get(s["id"], "F0_FALLEN_V1"),
                 "null_method": "cash_zero" if s.get("pick") == "all" else "paired_pool_expectation",
                 "n_settled": len(rows), "n_trade_sessions": len({r[0] for r in rows})}
    start = vb.BACKFILL_START if include_backfill else vb.FORWARD_START
    res["n_market_sessions"] = market_sessions_since(
        con, "US" if market in ("US", "SLOW") else "KR", start)
    res["months"] = months_elapsed(start, asof)
    nfin = PARAMS["n_final_kr" if market == "KR" else "n_final_us"]
    res["n_ok"] = {"sessions": res["n_market_sessions"] >= nfin["sessions"],
                   "settled": res["n_settled"] >= nfin["settled"],
                   "trade_sessions": res["n_trade_sessions"] >= nfin["trade_sessions"],
                   "months": res["months"] >= nfin["months"]}
    res["n_stage"] = ("FINAL" if all(res["n_ok"].values()) else
                      "EARLY_WARNING" if res["n_settled"] >= PARAMS["early_warning_settled"]
                      else "INSUFFICIENT")
    if not rows:
        res["verdict"] = "NO_DATA"
        return res
    nets = np.array([float(r[2]) for r in rows])
    gains, losses = nets[nets > 0].sum(), -nets[nets < 0].sum()
    res["mean_net_pct"] = round(float(nets.mean()), 4)
    res["median_net_pct"] = round(float(np.median(nets)), 4)
    res["win_rate"] = round(float((nets > 0).mean()), 4)
    res["pf"] = round(float(gains / losses), 3) if losses > 0 else None
    stress = PARAMS["stress_add_kr_pct" if market == "KR" else "stress_add_us_pct"]
    res["stress_mean_net_pct"] = round(float(nets.mean() - stress), 4)
    by_sess: dict[str, list[float]] = defaultdict(list)
    pnl_sess: dict[str, float] = defaultdict(float)
    for sd, _tk, net, p, _pos in rows:
        by_sess[sd].append(float(net))
        pnl_sess[sd] += float(p or 0.0)
    sess, alphas = _session_alphas(s, pool, by_sess)
    sess_mean = np.array([np.mean(by_sess[sd]) for sd in sess])
    order = np.argsort(-sess_mean)
    k = PARAMS["top_sessions_excluded"]
    res["ex_top_sessions_mean_pct"] = (round(float(sess_mean[order[k:]].mean()), 4)
                                       if len(sess) > k else None)
    month_pnl: dict[str, float] = defaultdict(float)
    for sd, p in pnl_sess.items():
        month_pnl[sd[:7]] += p
    total = sum(month_pnl.values())
    res["single_month_share"] = (round(max(month_pnl.values()) / total, 3)
                                 if total > 0 and month_pnl else None)
    cum = np.cumsum([pnl_sess[sd] for sd in sess])
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    res["maxdd_pct"] = round(float(((cum - peak) / float(s["capital_krw"])).min() * -100.0), 3)
    res["alpha_sessions"] = {sd: round(float(a), 4) for sd, a in zip(sess, alphas)}
    res["alpha_mean_pct"] = round(float(alphas.mean()), 4)
    if len(sess) >= PARAMS["min_sessions_for_bootstrap"]:
        _m, lcb = block_lcb(alphas, reps=reps, block_mean=PARAMS["block_mean_sessions"],
                            alpha=PARAMS["lcb_alpha"], rng=rng)
        res["alpha_lcb95_pct"] = round(lcb, 4)
        res["alpha_boot_p"] = round(bootstrap_p_one_sided(
            alphas, reps=reps, block_mean=PARAMS["block_mean_sessions"], rng=rng), 4)
    else:
        res["alpha_lcb95_pct"] = None
        res["alpha_boot_p"] = None
    if int(s.get("daily_cap", 1)) > 1:
        slots: dict = {}
        for pos in sorted({int(r[4] or 0) for r in rows}):
            srows = [r for r in rows if int(r[4] or 0) == pos]
            snets = np.array([float(r[2]) for r in srows])
            sb: dict[str, list[float]] = defaultdict(list)
            for sd, _t, net, _p, _pos in srows:
                sb[sd].append(float(net))
            _ss, sal = _session_alphas(s, pool, sb)
            g, l = snets[snets > 0].sum(), -snets[snets < 0].sum()
            entry = {"n": len(srows), "mean_net_pct": round(float(snets.mean()), 4),
                     "pf": round(float(g / l), 3) if l > 0 else None,
                     "alpha_mean_pct": round(float(sal.mean()), 4), "alpha_lcb95_pct": None}
            if len(sal) >= PARAMS["min_sessions_for_bootstrap"]:
                entry["alpha_lcb95_pct"] = round(block_lcb(
                    sal, reps=reps, block_mean=PARAMS["block_mean_sessions"],
                    alpha=PARAMS["lcb_alpha"], rng=rng)[1], 4)
            slots[str(pos)] = entry
        res["marginal_slots"] = slots
    checks = {
        "mean_net": res["mean_net_pct"] >= PARAMS["hurdle_net_pct"],
        "pf": (res["pf"] or 0.0) >= PARAMS["hurdle_pf"],
        "lcb": (res["alpha_lcb95_pct"] is not None and res["alpha_lcb95_pct"] > 0),
        "stress": res["stress_mean_net_pct"] > 0,
        "ex_top": (res["ex_top_sessions_mean_pct"] if res["ex_top_sessions_mean_pct"] is not None else -1) > 0,
        "month_conc": (res["single_month_share"] is None
                       or res["single_month_share"] <= PARAMS["single_month_share_max"]),
        "maxdd": res["maxdd_pct"] <= PARAMS["maxdd_max_pct"],
    }
    if "marginal_slots" in res:
        checks["marginal_slots"] = all(
            (v["mean_net_pct"] >= PARAMS["hurdle_net_pct"] and (v["pf"] or 0) >= PARAMS["hurdle_pf"]
             and (v["alpha_lcb95_pct"] or 0) > 0) for v in res["marginal_slots"].values())
    res["checks"] = checks
    res["metrics_pass"] = all(checks.values())
    if res["n_stage"] == "FINAL":
        res["verdict"] = "PASS_METRICS" if res["metrics_pass"] else "FAIL_METRICS"
    else:
        res["verdict"] = f"{res['n_stage']}(descriptive_only)"
    return res


def evaluate_families(strats: list[dict], results: dict[str, dict], *, reps: int,
                      rng: np.random.Generator) -> dict:
    """F0 max-stat(RC) + 단일 가설 family 부트 p + Holm."""
    fam: dict[str, dict] = {}
    groups: dict[str, list[str]] = defaultdict(list)
    for s in strats:
        r = results.get(s["id"])
        if not r or r.get("alpha_sessions") is None:
            continue
        if len(r["alpha_sessions"]) < PARAMS["min_sessions_for_bootstrap"]:
            continue  # 희소 arm은 max-stat에서 제외 (개정 1)
        groups[r["family"]].append(s["id"])
    for family, ids in groups.items():
        union = sorted({sd for sid in ids for sd in results[sid]["alpha_sessions"]})
        mat = np.full((len(union), len(ids)), np.nan)
        pos = {sd: i for i, sd in enumerate(union)}
        for j, sid in enumerate(ids):
            for sd, a in results[sid]["alpha_sessions"].items():
                mat[pos[sd], j] = a
        if len(ids) == 1:
            col = mat[:, 0]
            col = col[~np.isnan(col)]
            p = bootstrap_p_one_sided(col, reps=reps, block_mean=PARAMS["block_mean_sessions"], rng=rng)
            fam[family] = {"arms": ids, "method": "single_arm_bootstrap", "p": round(p, 4),
                           "best_arm": ids[0]}
        else:
            p, best = reality_check_p(mat, reps=reps, block_mean=PARAMS["block_mean_sessions"], rng=rng)
            fam[family] = {"arms": ids, "method": "white_reality_check", "p": round(p, 4),
                           "best_arm": ids[best]}
    if fam:
        sig = holm({k: v["p"] for k, v in fam.items()}, alpha=PARAMS["lcb_alpha"])
        for k in fam:
            fam[k]["holm_significant"] = bool(sig[k])
    return fam


def run(*, include_backfill: bool, reps: int, seed: int, asof: str | None = None,
        pool_fn=None, con: sqlite3.Connection | None = None, write: bool = True,
        strategies: list[dict] | None = None) -> dict:
    asof = asof or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rng = np.random.default_rng(seed)
    own = con is None
    if own:
        con = sqlite3.connect(f"file:{vb.BOOK_DB}?mode=ro", uri=True, timeout=30)
    try:
        try:
            from runtime.virtual_overrides import load_overrides, arm_state
            _ov = load_overrides()
        except Exception:
            _ov, arm_state = {}, (lambda a, o=None: "active")  # type: ignore
        strats = [s for s in (strategies or vb.STRATEGIES)
                  if not s.get("retired") and arm_state(s["id"], _ov) != "retired"]
        pool = PoolResolver(pool_fn)
        results = {s["id"]: evaluate_strategy(con, s, pool, include_backfill=include_backfill,
                                              reps=reps, rng=rng, asof=asof) for s in strats}
        families = evaluate_families(strats, results, reps=reps, rng=rng)
        reconcile_ok = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                reconcile_ok = bool(vb.reconcile(con))
        except Exception:
            pass
    finally:
        if own:
            con.close()
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "asof": asof,
           "gate_version": GATE_VERSION, "code_commit": vb._code_commit(), "params": PARAMS,
           "sample": ("backfill+forward (배관 검증 전용, 판정 아님)" if include_backfill
                      else "forward only"),
           "reconcile_ok": reconcile_ok, "strategies": results, "families": families}
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        tag = "backfill_" if include_backfill else ""
        path = OUT_DIR / f"virtual_gate_eval_{tag}{asof.replace('-', '')}.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        out["path"] = str(path)
    return out


def print_report(out: dict) -> None:
    print(f"=== [VIRTUAL GATE] {out['asof']} · {out['sample']} · 대사 {out['reconcile_ok']} · "
          f"{out['gate_version']} ===")
    print(f"{'전략':16} {'N단계':14} {'정산':>4} {'세션':>4} {'평균':>7} {'PF':>5} "
          f"{'알파':>7} {'LCB95':>7} {'p':>6} 판정")
    for sid, r in out["strategies"].items():
        if r.get("verdict") == "NO_DATA":
            print(f"{sid:16} {r['n_stage']:14} {0:>4}  —  NO_DATA")
            continue
        lcb = r.get("alpha_lcb95_pct")
        p = r.get("alpha_boot_p")
        pf = r["pf"] if r["pf"] is not None else float("nan")
        print(f"{sid:16} {r['n_stage']:14} {r['n_settled']:>4} {r['n_trade_sessions']:>4} "
              f"{r['mean_net_pct']:>+7.2f} {pf:>5.2f} {r['alpha_mean_pct']:>+7.2f} "
              f"{(f'{lcb:+.2f}' if lcb is not None else 'INSUF'):>7} "
              f"{(f'{p:.3f}' if p is not None else '  -  '):>6} {r['verdict']}")
    if out["families"]:
        print("[family]")
        for k, v in out["families"].items():
            print(f"  {k}: {v['method']} p={v['p']} best={v['best_arm']} "
                  f"Holm유의={v.get('holm_significant')} arms={len(v['arms'])}")
    else:
        print("[family] 부트스트랩 가능한 arm 없음(거래 세션 30 미만) — INSUFFICIENT")
    if "path" in out:
        print(f"[저장] {out['path']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-backfill", action="store_true", help="배관 검증 전용 — 판정 아님")
    ap.add_argument("--reps", type=int, default=PARAMS["n_boot"])
    ap.add_argument("--seed", type=int, default=PARAMS["seed"])
    ap.add_argument("--asof", default=None)
    ap.add_argument("--min-sessions", type=int, default=None, help="배관 검증 전용 override")
    args = ap.parse_args()
    if args.min_sessions:
        PARAMS["min_sessions_for_bootstrap"] = int(args.min_sessions)
    out = run(include_backfill=args.include_backfill, reps=args.reps, seed=args.seed, asof=args.asof)
    print_report(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
