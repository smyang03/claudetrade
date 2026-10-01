"""profit_path 모델(2026-07-10 학습) 부활 시험 — OOS 예측력 검증 (read-only).

사전등록 (결과 조회 전에 작성, 2026-09-27)
==========================================
대상 모델: state/models/profit_path_{KR,US}.json (portable_linear_v1, trained 2026-07-10).
  KR train 05-26~07-01 / calib 07-02~07-06 / val 07-07~07-09 / purge 07-10.
  US train 05-26~06-29 / calib 06-30~07-02 / val 07-06~07-08 / purge 07-09.

OOS 규칙: session_date > '2026-07-10' 행만 쓴다(학습·보정·검증 데이터 제외). KR/US 분리.

예측 소스 (우선순위 순):
  S1. 결정 시점 로깅 예측 — v2_event_store.db profit_evidence_shadow_events +
      lifecycle_events(PROFIT_EVIDENCE_SHADOW). forward monitor와 같은 규칙(같은 날·티커·path_name,
      |known_at − decision_ts| ≤ 10분)으로 결과에 붙인다. 표본이 작으면 커버리지로만 보고한다.
  S2. logs/shadow/profit_evidence_snapshot_*.jsonl — OOD 비중·고유 예측값 수만 보고(커버리지).
  S3. 재계산 — candidate_counterfactual_paths(metadata_quality='runtime_authoritative')의
      decision-time 피처(metadata_json.context, known_at)와 audit_candidate_rows backward ≤2분
      merge를 학습 파이프라인(tools/profit_evidence_path_walkforward.build_path_dataset)과
      동일하게 재구성하되 session_date>'2026-07-10' 필터만 추가. 점수는 런타임 portable 경로
      (runtime/profit_path_predictor._portable_probability + _isotonic_transform)로 계산.
      joblib은 200행 교차검증용.

라벨/결과 (학습 코드 tools/train_profit_path_shadow._labels 정의 그대로):
  주 결과: net60 = outcome_60m_pct − cost_pct (cost = 아티팩트 metadata: KR 0.21 / US 0.50).
  보조 이진 라벨: net60 >= 0.25 AND max_drawdown_60m_pct > −2.5.

순위 점수: p_raw(보정 전 로지스틱 확률). 보정기(isotonic)는 계단함수라 동률이 많아서
  expected_net_pct로 분위를 자르면 임의적이다. p_raw는 두 보정 출력과 단조 동순.

유니버스:
  주: path_name='immediate', (session_date, ticker)별 첫 관측 1행.
  보조: immediate 전 행 / 진입 path 전 행(no_entry 제외).
  사전등록 절단: non-OOD(런타임 _ood 규칙) 부분집합 — 런타임이 OOD면 abstain하므로 실제 작동 영역.

검정 (일 단위 클러스터, 하루 ≥10행인 날만):
  T1 일별 Spearman(p_raw, net60) → 날짜 간 평균·t.
  T2 일별 상위 20%(ceil) 평균 net60 − 당일 전체 평균 → 날짜 간 평균·t.
  T3 캘리브레이션: p_raw 풀드 10분위별 평균 expected_gross vs 평균 outcome_60m,
     평균 p_cal vs 라벨 적중률.
  T4 모델 게이트: expected_net_pct >= 0.25 통과 vs 탈락 net60 (양쪽 존재 날의 일별 차이 t).
     전체 런타임 게이트(p_cal>=0.55)는 보정 확률 상한(KR 0.49/US 0.30)<0.55라 구조적으로 발화 불가.
통과 기준: T2 날짜 클러스터 t >= 2 AND OOS 기간 전반/후반(중앙 날짜 기준 분할) 모두 부호 양수.
판정 등급: 통과=확정 후보(운영 적용 전 추가 forward 필요) / 부호만 양·t<2=후보 / 그 외=기각.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import glob
import json
import math
from pathlib import Path
import pickle
import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.profit_path_predictor import _isotonic_transform, _ood, _portable_probability  # noqa: E402
from tools.profit_evidence_path_walkforward import CATEGORICAL_FEATURES, NUMERIC_FEATURES  # noqa: E402

OOS_AFTER = "2026-07-10"
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
EVENT_DB = ROOT / "data" / "v2_event_store.db"
MIN_DAY_N = 10
GATE_NET = 0.25
MAX_MAE = 2.5


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=10000")
    return con


def _candidate_frame(con, market):
    frame = pd.read_sql_query(
        """
        SELECT candidate_key AS audit_candidate_key, market, session_date, ticker, known_at,
               actual_prompt_included, final_prompt_included, in_prompt,
               price AS candidate_price, change_pct, volume_ratio, from_high_pct,
               raw_score_current, primary_bucket, recommended_strategy, candidate_source,
               liquidity_bucket, market_type
        FROM audit_candidate_rows
        WHERE market=? AND known_at IS NOT NULL AND session_date > ?
        """,
        con,
        params=(market, OOS_AFTER),
    )
    frame["prompt_included"] = frame[["actual_prompt_included", "final_prompt_included", "in_prompt"]].fillna(0).max(axis=1)
    return frame


def _path_frame(con, market):
    return pd.read_sql_query(
        """
        SELECT id AS path_id, market, session_date, ticker, known_at, created_at, signal_time, trigger_time,
               path_name, trigger_reason, entry_price, entry_delay_min,
               outcome_60m_pct, outcome_close_pct, max_runup_60m_pct, max_drawdown_60m_pct,
               metadata_quality, label_source, runtime_mode,
               json_extract(metadata_json, '$.consensus_mode') AS consensus_mode,
               json_extract(metadata_json, '$.mode_family') AS mode_family,
               json_extract(metadata_json, '$.context.route_source') AS route_source,
               json_extract(metadata_json, '$.context.data_quality') AS data_quality,
               json_extract(metadata_json, '$.context.market_open_elapsed_min') AS market_open_elapsed_min,
               COALESCE(json_extract(metadata_json, '$.context.ret_3m_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.ret_3m_pct')) AS ret_3m_pct,
               COALESCE(json_extract(metadata_json, '$.context.ret_5m_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.ret_5m_pct')) AS ret_5m_pct,
               COALESCE(json_extract(metadata_json, '$.context.ret_10m_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.ret_10m_pct')) AS ret_10m_pct,
               COALESCE(json_extract(metadata_json, '$.context.ret_30m_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.ret_30m_pct')) AS ret_30m_pct,
               COALESCE(json_extract(metadata_json, '$.context.volume_ratio_open'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.volume_ratio_open')) AS volume_ratio_open,
               COALESCE(json_extract(metadata_json, '$.context.vwap_distance_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.vwap_distance_pct')) AS vwap_distance_pct,
               COALESCE(json_extract(metadata_json, '$.context.pullback_from_high_pct'),
                 json_extract(metadata_json, '$.context.evidence_pack.post_open_confirmation.pullback_from_high_pct')) AS pullback_from_high_pct
        FROM candidate_counterfactual_paths
        WHERE metadata_quality='runtime_authoritative' AND market=? AND session_date > ?
          AND entry_price IS NOT NULL AND outcome_60m_pct IS NOT NULL
          AND max_runup_60m_pct IS NOT NULL AND max_drawdown_60m_pct IS NOT NULL
        """,
        con,
        params=(market, OOS_AFTER),
    )


def build_oos_dataset(market: str) -> tuple[pd.DataFrame, dict]:
    """build_path_dataset와 동일 로직 + OOS 날짜 필터."""
    with closing(_ro(AUDIT_DB)) as con:
        candidates = _candidate_frame(con, market)
        paths = _path_frame(con, market)
    for frame in (candidates, paths):
        frame["market"] = frame["market"].astype(str).str.upper()
        frame["ticker_key"] = frame["ticker"].astype(str)
        if market == "US":
            frame["ticker_key"] = frame["ticker_key"].str.upper()
        frame["known_ts"] = pd.to_datetime(frame["known_at"], utc=True, errors="coerce")
        frame["join_key"] = frame["market"] + "|" + frame["session_date"].astype(str) + "|" + frame["ticker_key"]
    candidates = candidates.dropna(subset=["known_ts"]).sort_values("known_ts")
    paths = paths.dropna(subset=["known_ts"]).sort_values("known_ts")
    cols = ["join_key", "known_ts", "audit_candidate_key", "prompt_included", "candidate_price", "change_pct",
            "volume_ratio", "from_high_pct", "raw_score_current", "primary_bucket", "recommended_strategy",
            "candidate_source", "liquidity_bucket", "market_type"]
    side = candidates[cols].rename(columns={"known_ts": "candidate_known_ts"})
    merged = pd.merge_asof(paths, side.sort_values("candidate_known_ts"), left_on="known_ts",
                           right_on="candidate_known_ts", by="join_key", direction="backward",
                           tolerance=pd.Timedelta(minutes=2))
    matched = merged["audit_candidate_key"].notna()
    prompt_matched = matched & merged["prompt_included"].fillna(0).gt(0)
    coverage = {"path_rows": int(len(paths)), "matched": int(matched.sum()), "prompt_matched": int(prompt_matched.sum())}
    merged = merged[prompt_matched].copy()
    merged["created_minus_known_sec"] = (
        pd.to_datetime(merged["created_at"], utc=True, errors="coerce") - merged["known_ts"]
    ).dt.total_seconds()
    merged["entry_vs_candidate_pct"] = np.where(
        pd.to_numeric(merged["candidate_price"], errors="coerce") > 0,
        (pd.to_numeric(merged["entry_price"], errors="coerce") / pd.to_numeric(merged["candidate_price"], errors="coerce") - 1.0) * 100.0,
        np.nan,
    )
    for c in NUMERIC_FEATURES + ["outcome_60m_pct", "max_drawdown_60m_pct"]:
        merged[c] = pd.to_numeric(merged[c], errors="coerce")
    for c in CATEGORICAL_FEATURES:
        merged[c] = merged[c].fillna("__MISSING__").astype(str)
    return merged, coverage


def score(frame: pd.DataFrame, artifact: dict) -> pd.DataFrame:
    pc, rc = artifact["probability_calibrator"], artifact["return_calibrator"]
    cost = float(artifact["metadata"]["cost_pct"])
    feats = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    p_raw, p_cal, gross, ood = [], [], [], []
    for rec in frame[feats].to_dict("records"):
        f = {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in rec.items()}
        r = _portable_probability(artifact, f)
        p_raw.append(r)
        p_cal.append(_isotonic_transform(pc, r))
        gross.append(_isotonic_transform(rc, r))
        ood.append(_ood(artifact, f))
    out = frame.copy()
    out["p_raw"], out["p_cal"], out["exp_gross"] = p_raw, p_cal, gross
    out["exp_net"] = out["exp_gross"] - cost
    out["ood"] = ood
    out["net60"] = out["outcome_60m_pct"] - cost
    out["label"] = ((out["net60"] >= GATE_NET) & (out["max_drawdown_60m_pct"] > -MAX_MAE)).astype(int)
    return out


def joblib_crosscheck(frame: pd.DataFrame, market: str) -> dict:
    try:
        import joblib
        art = joblib.load(ROOT / "state" / "models" / f"profit_path_{market}.joblib")
        s = frame.sample(min(200, len(frame)), random_state=1)
        jr = art["classifier"].predict_proba(s[NUMERIC_FEATURES + CATEGORICAL_FEATURES])[:, 1]
        return {"n": int(len(s)), "max_abs_diff": float(np.max(np.abs(jr - s["p_raw"].to_numpy())))}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


def _t(x) -> tuple[float, float, int]:
    x = np.asarray([v for v in x if np.isfinite(v)], dtype=float)
    if len(x) < 2 or x.std(ddof=1) == 0:
        return (float(x.mean()) if len(x) else float("nan"), float("nan"), len(x))
    return float(x.mean()), float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))), len(x)


def day_tests(df: pd.DataFrame) -> dict:
    rows = []
    for day, g in df.groupby("session_date"):
        if len(g) < MIN_DAY_N:
            continue
        rho = stats.spearmanr(g["p_raw"], g["net60"]).correlation if g["p_raw"].nunique() > 1 else np.nan
        k = math.ceil(0.2 * len(g))
        top = g.nlargest(k, "p_raw")
        gp, gf = g[g["exp_net"] >= GATE_NET], g[g["exp_net"] < GATE_NET]
        rows.append({
            "day": day, "n": len(g), "rho": rho,
            "top_minus_all": top["net60"].mean() - g["net60"].mean(),
            "top_label_minus_all": top["label"].mean() - g["label"].mean(),
            "gate_diff": (gp["net60"].mean() - gf["net60"].mean()) if len(gp) and len(gf) else np.nan,
        })
    d = pd.DataFrame(rows)
    if d.empty:
        return {"days": 0}
    days = sorted(d["day"])
    mid = days[len(days) // 2]
    h1, h2 = d[d["day"] < mid], d[d["day"] >= mid]
    res = {"days": int(len(d)), "rows": int(d["n"].sum()), "first": days[0], "last": days[-1], "split": mid}
    for key in ("rho", "top_minus_all", "top_label_minus_all", "gate_diff"):
        m, t, n = _t(d[key])
        res[key] = {"mean": m, "t": t, "n_days": n, "h1": _t(h1[key])[0], "h2": _t(h2[key])[0]}
    res["pass"] = bool(res["top_minus_all"]["t"] >= 2 and res["top_minus_all"]["h1"] > 0 and res["top_minus_all"]["h2"] > 0)
    return res


def calibration(df: pd.DataFrame) -> list[dict]:
    q = pd.qcut(df["p_raw"].rank(method="first"), 10, labels=False)
    out = []
    for dec, g in df.groupby(q):
        out.append({"decile": int(dec) + 1, "n": int(len(g)), "exp_gross": g["exp_gross"].mean(),
                    "real_gross": g["outcome_60m_pct"].mean(), "p_cal": g["p_cal"].mean(), "hit": g["label"].mean()})
    return out


def posthoc(primary: pd.DataFrame) -> dict:
    """사후 진단(사전등록 밖): 개장 전/후 층 분리, 상위분위 절대 net, 단일 피처 기준선.

    개장 전 행(market_open_elapsed_min<=0)의 immediate 진입가는 장전 context 현재가이고 60분 결과는
    trigger+60분 첫 분봉이다 → 장전 호가→시가 갭이 라벨에 들어가 체결 불가능한 가격이다.
    """
    def top_stats(sub: pd.DataFrame, col: str = "p_raw", largest: bool = True) -> dict:
        spread, absolute = [], []
        for _, g in sub.dropna(subset=[col]).groupby("session_date"):
            if len(g) < MIN_DAY_N:
                continue
            k = math.ceil(0.2 * len(g))
            top = g.nlargest(k, col) if largest else g.nsmallest(k, col)
            spread.append(top["net60"].mean() - g["net60"].mean())
            absolute.append(top["net60"].mean())
        m1, t1, n1 = _t(spread)
        m2, t2, _ = _t(absolute)
        return {"spread": m1, "spread_t": t1, "abs_net": m2, "abs_t": t2, "days": n1,
                "abs_pos_day_share": float(np.mean(np.asarray(absolute) > 0)) if absolute else None}
    pre = primary["market_open_elapsed_min"] <= 0
    q = primary.groupby("session_date")["p_raw"].rank(pct=True)
    out = {"preopen_share": float(pre.mean()), "preopen_share_in_top_quintile": float(pre[q > 0.8].mean()),
           "all": top_stats(primary), "preopen": top_stats(primary[pre]), "postopen": top_stats(primary[~pre])}
    for col in ("market_open_elapsed_min", "from_high_pct", "change_pct", "raw_score_current"):
        out[f"baseline_low_{col}"] = top_stats(primary, col, largest=False)
    return out


def gate_pooled(df: pd.DataFrame) -> dict:
    gp, gf = df[df["exp_net"] >= GATE_NET], df[df["exp_net"] < GATE_NET]
    return {"pass_n": int(len(gp)), "pass_net60": gp["net60"].mean() if len(gp) else None,
            "fail_n": int(len(gf)), "fail_net60": gf["net60"].mean() if len(gf) else None,
            "pass_days": int(gp["session_date"].nunique())}


def logged_events(market: str, outcomes: pd.DataFrame) -> dict:
    with closing(_ro(EVENT_DB)) as con:
        rows = con.execute(
            "SELECT session_date, ticker, occurred_at, payload_json FROM profit_evidence_shadow_events WHERE market=? "
            "UNION ALL SELECT session_date, ticker, occurred_at, payload_json FROM lifecycle_events "
            "WHERE event_type='PROFIT_EVIDENCE_SHADOW' AND market=?", (market, market)).fetchall()
    recs = []
    for sd, tk, occ, raw in rows:
        try:
            ev = (json.loads(raw) or {}).get("evidence") or {}
        except ValueError:
            continue
        if not ev.get("model_version"):
            continue
        recs.append({"session_date": sd, "ticker_key": str(tk).upper() if market == "US" else str(tk),
                     "pred_ts": pd.to_datetime(ev.get("decision_ts") or occ, utc=True, errors="coerce"),
                     "path_name": ev.get("path_name") or "immediate", "ood": ev.get("ood"),
                     "exp_net_logged": ev.get("expected_net_pct"), "strategy": (json.loads(raw) or {}).get("strategy")})
    p = pd.DataFrame(recs)
    info = {"logged_n": int(len(p))}
    if p.empty:
        return info
    p = p[p["session_date"] > OOS_AFTER]
    info.update({"oos_n": int(len(p)), "days": int(p["session_date"].nunique()),
                 "first": str(p["session_date"].min()), "last": str(p["session_date"].max()),
                 "ood_true": int((p["ood"] == True).sum()),  # noqa: E712
                 "strategies": p["strategy"].value_counts().to_dict()})
    o = outcomes[["session_date", "ticker_key", "path_name", "known_ts", "net60"]]
    m = p.merge(o, on=["session_date", "ticker_key", "path_name"], how="inner")
    m = m[(m["known_ts"] - m["pred_ts"]).abs().dt.total_seconds() <= 600]
    m = m.sort_values("known_ts").drop_duplicates(["session_date", "ticker_key", "pred_ts"])
    info["matched_n"] = int(len(m))
    if len(m):
        info["matched_mean_net60"] = float(m["net60"].mean())
    return info


def snapshot_ledger(market: str) -> dict:
    n = ood = 0
    vals, days = set(), set()
    for f in sorted(glob.glob(str(ROOT / "logs" / "shadow" / f"profit_evidence_snapshot_*_{market}.jsonl"))):
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            n += 1
            ood += bool(d.get("ood"))
            vals.add(round(float(d.get("expected_net_pct") or 0), 6))
            days.add(str(d.get("written_at"))[:10])
    return {"rows": n, "ood": ood, "distinct_exp_net": len(vals), "days": len(days),
            "first": min(days) if days else None, "last": max(days) if days else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["pull", "analyze"], required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--markets", default="KR,US")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    for market in args.markets.split(","):
        pk = cache / f"oos_{market}.pkl"
        if args.stage == "pull":
            frame, cov = build_oos_dataset(market)
            artifact = json.loads((ROOT / "state" / "models" / f"profit_path_{market}.json").read_text(encoding="utf-8"))
            scored = score(frame, artifact)
            with open(pk, "wb") as fh:
                pickle.dump({"df": scored, "coverage": cov}, fh)
            print(market, cov, len(scored))
            continue
        with open(pk, "rb") as fh:
            obj = pickle.load(fh)
        df, cov = obj["df"], obj["coverage"]
        out = {"market": market, "coverage": cov,
               "created_minus_known_sec": df["created_minus_known_sec"].describe(percentiles=[.5, .95, .99]).to_dict(),
               "joblib_crosscheck": joblib_crosscheck(df, market),
               "snapshot_ledger": snapshot_ledger(market),
               "logged_events": logged_events(market, df),
               "p_raw_distinct": int(df["p_raw"].round(8).nunique()),
               "exp_net_distinct": int(df["exp_net"].round(8).nunique())}
        df["month"] = df["session_date"].str[:7]
        out["ood_by_month"] = df.groupby("month")["ood"].agg(["mean", "size"]).round(4).to_dict("index")
        imm = df[df["path_name"] == "immediate"]
        primary = imm.sort_values("known_ts").drop_duplicates(["session_date", "ticker_key"], keep="first")
        universes = {
            "primary_immediate_first": primary,
            "primary_nonOOD": primary[~primary["ood"]],
            "all_immediate": imm,
            "all_entry_paths": df[df["path_name"] != "no_entry"],
        }
        for name, u in universes.items():
            out[name] = {"rows": int(len(u)), "days_total": int(u["session_date"].nunique()),
                         "mean_net60": float(u["net60"].mean()) if len(u) else None,
                         "tests": day_tests(u), "gate_pooled": gate_pooled(u)}
        out["posthoc_primary"] = posthoc(primary)
        out["calibration_primary"] = calibration(primary)
        out["calibration_primary_nonOOD"] = calibration(primary[~primary["ood"]]) if (~primary["ood"]).sum() >= 100 else []
        print(json.dumps(out, ensure_ascii=False, indent=1, default=lambda v: None if v != v else str(v)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
