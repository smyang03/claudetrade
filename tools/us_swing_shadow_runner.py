from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.us_swing_authority import evaluate_swing_authority, load_swing_policy
from runtime.us_swing_execution_contract import (
    OPERATOR_MICRO_OVERRIDE_ACK,
    operator_override_active,
    resolve_execution_contract,
)
from runtime.us_swing_order_handoff import ensure_handoff_schema, summarize_forward_evidence
from tools.build_us_yahoo_point_in_time import BENCHMARKS, build_ticker_frame, _read_price
from tools.us_daily_alpha_walkforward import YAHOO_FEATURES, load_yahoo_dataset
from tools.us_swing_exit_counterfactual import simulate_exit


SCHEMA_VERSION = "us_swing_shadow_v1"
_BREADTH_COLUMNS = {
    "reference_close": "REAL",
    "breadth_context_date": "TEXT",
    "prior_spy_return_pct": "REAL",
    "prior_narrow_excess_pct": "REAL",
    "prior_rsp_spy_ratio_5d_pct": "REAL",
    "prior_adv_pct": "REAL",
    "breadth_context_state": "TEXT",
}


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS signals (
            signal_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            feature_date TEXT NOT NULL,
            model_version TEXT NOT NULL,
            rank INTEGER NOT NULL,
            alpha_score REAL,
            predicted_net_pct REAL,
            probability REAL,
            candidate_source TEXT,
            created_at TEXT NOT NULL,
            entry_date TEXT,
            entry_price REAL,
            exit_date TEXT,
            exit_price REAL,
            entry_fx REAL,
            exit_fx REAL,
            gross_usd_pct REAL,
            gross_krw_pct REAL,
            net_krw_pct REAL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            data_quality TEXT NOT NULL DEFAULT 'point_in_time',
            error TEXT,
            PRIMARY KEY(signal_date, ticker)
        );
        CREATE INDEX IF NOT EXISTS idx_us_swing_signals_status ON signals(status, signal_date);
        CREATE TABLE IF NOT EXISTS runs (
            signal_date TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            report_json TEXT NOT NULL
        );
        """
    )
    existing = {str(row[1]) for row in con.execute("PRAGMA table_info(signals)")}
    for column, sql_type in _BREADTH_COLUMNS.items():
        if column not in existing:
            con.execute(f"ALTER TABLE signals ADD COLUMN {column} {sql_type}")
    con.commit()


def classify_breadth_context(narrow_excess_pct: float | None) -> str:
    if narrow_excess_pct is None or not np.isfinite(narrow_excess_pct):
        return "MISSING"
    if narrow_excess_pct <= -0.30:
        return "NARROW"
    if narrow_excess_pct >= 0.30:
        return "BROAD"
    return "BALANCED"


def load_breadth_context(
    *,
    feature_date: str,
    breadth_path: Path,
    adv_path: Path,
) -> dict[str, Any]:
    try:
        breadth = pd.read_csv(breadth_path)
        adv = pd.read_csv(adv_path)
    except (OSError, ValueError):
        return {"breadth_context_date": feature_date, "breadth_context_state": "MISSING"}
    for frame in (breadth, adv):
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    breadth = breadth.sort_values("date")
    breadth["spy_return_pct"] = pd.to_numeric(breadth["SPY"], errors="coerce").pct_change() * 100.0
    breadth["rsp_return_pct"] = pd.to_numeric(breadth["RSP"], errors="coerce").pct_change() * 100.0
    breadth["narrow_excess_pct"] = breadth["rsp_return_pct"] - breadth["spy_return_pct"]
    breadth["ratio_full"] = pd.to_numeric(breadth["RSP"], errors="coerce") / pd.to_numeric(
        breadth["SPY"], errors="coerce"
    )
    breadth["ratio_5d_pct"] = breadth["ratio_full"].pct_change(5) * 100.0
    merged = breadth.merge(adv[["date", "adv_pct"]], on="date", how="left")
    row = merged[merged["date"].eq(str(feature_date))]
    if row.empty:
        return {"breadth_context_date": feature_date, "breadth_context_state": "MISSING"}
    record = row.iloc[-1]

    def finite(name: str) -> float | None:
        value = pd.to_numeric(pd.Series([record.get(name)]), errors="coerce").iloc[0]
        return float(value) if pd.notna(value) and np.isfinite(value) else None

    narrow = finite("narrow_excess_pct")
    return {
        "breadth_context_date": str(feature_date),
        "prior_spy_return_pct": finite("spy_return_pct"),
        "prior_narrow_excess_pct": narrow,
        "prior_rsp_spy_ratio_5d_pct": finite("ratio_5d_pct"),
        "prior_adv_pct": finite("adv_pct"),
        "breadth_context_state": classify_breadth_context(narrow),
    }


def _benchmark_frame(price_dir: Path, *, before_date: str) -> pd.DataFrame:
    output: pd.DataFrame | None = None
    for ticker in BENCHMARKS:
        path = price_dir / f"us_{ticker}.csv"
        if not path.exists():
            return pd.DataFrame()
        data = build_ticker_frame(_read_price(path))
        data = data[data["date"].astype(str) < str(before_date)]
        selected = data[["date", "momentum_5d_pct", "momentum_20d_pct", "momentum_60d_pct"]].rename(
            columns={
                "momentum_5d_pct": f"{ticker.lower()}_momentum_5d_pct",
                "momentum_20d_pct": f"{ticker.lower()}_momentum_20d_pct",
                "momentum_60d_pct": f"{ticker.lower()}_momentum_60d_pct",
            }
        )
        output = selected if output is None else output.merge(selected, on="date", how="outer")
    return output if output is not None else pd.DataFrame()


def _news_flag_state(candidate: dict[str, Any]) -> float:
    """정보성 이벤트 플래그를 3-state로 읽는다. 1.0=있음 / 0.0=없음 / NaN=unknown.

    후보 snapshot에 키 자체가 없거나 None이면 unknown이다. 이걸 False로 접으면
    "뉴스 없음"으로 취급되어 fail-open이 된다.
    """

    if "news_or_earnings_flag" not in candidate:
        return float("nan")
    raw = candidate.get("news_or_earnings_flag")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return float("nan")
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in {"true", "1", "y", "yes"}:
            return 1.0
        if text in {"false", "0", "n", "no"}:
            return 0.0
        return float("nan")
    return 1.0 if bool(raw) else 0.0


def news_arm_counterfactual(scored: pd.DataFrame) -> dict[str, Any]:
    """정보성 이벤트 배제 3-arm의 rank1을 같은 scorer로 비교한다(기록만, enforce 아님).

    arm 정의
      current         : 현행 — 플래그를 보지 않는다
      exclude_flagged : flag=True 제외
      unknown_abstain : flag=True 제외 + flag unknown 제외(fail-closed)

    30세션 paired 표본이 쌓이기 전에는 어떤 arm도 승격하지 않는다.
    """

    if scored.empty:
        return {"arms": {}, "rank1_changed": False}
    frame = scored.sort_values("rank")
    flag = frame["news_or_earnings_flag"] if "news_or_earnings_flag" in frame.columns else None
    arms: dict[str, Any] = {}

    def _pick(subset: pd.DataFrame) -> dict[str, Any] | None:
        if subset.empty:
            return None
        row = subset.iloc[0]
        return {
            "ticker": str(row["ticker"]),
            "original_rank": int(row["rank"]),
            "predicted_net_pct": float(row["predicted_net_pct"]),
            "candidate_source": str(row.get("candidate_source") or ""),
        }

    arms["current"] = _pick(frame)
    if flag is None:
        arms["exclude_flagged"] = arms["current"]
        arms["unknown_abstain"] = None
    else:
        arms["exclude_flagged"] = _pick(frame[~(flag == 1.0)])
        arms["unknown_abstain"] = _pick(frame[flag == 0.0])
    tickers = {name: (value or {}).get("ticker") for name, value in arms.items()}
    return {
        "arms": arms,
        "rank1_changed": len({value for value in tickers.values() if value}) > 1,
        "flag_counts": {
            "flagged": int((flag == 1.0).sum()) if flag is not None else 0,
            "clean": int((flag == 0.0).sum()) if flag is not None else 0,
            "unknown": int(flag.isna().sum()) if flag is not None else int(len(frame)),
        },
    }


def load_candidate_features(
    *, snapshot_path: Path, price_dir: Path, session_date: str, vetoes: dict[str, str] | None = None
) -> tuple[pd.DataFrame, list[str]]:
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if str(payload.get("market", "")).upper() != "US":
        raise ValueError("snapshot market is not US")
    if str(payload.get("session_date", "")) != str(session_date):
        raise ValueError("snapshot session date mismatch")
    benchmark = _benchmark_frame(price_dir, before_date=session_date)
    if benchmark.empty:
        raise ValueError("benchmark price history missing")
    benchmark_feature_date = str(benchmark["date"].dropna().astype(str).max())
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    veto_map = {str(key).upper(): str(value) for key, value in (vetoes or {}).items()}
    for candidate in payload.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        ticker = str(candidate.get("ticker") or "").strip().upper()
        path = price_dir / f"us_{ticker}.csv"
        if not ticker or not path.exists():
            errors.append(f"{ticker or 'UNKNOWN'}:price_missing")
            continue
        features = build_ticker_frame(_read_price(path))
        features = features[features["date"].astype(str) < str(session_date)].merge(benchmark, on="date", how="left")
        if features.empty:
            errors.append(f"{ticker}:no_point_in_time_bar")
            continue
        row = features.iloc[-1].to_dict()
        if str(row.get("date") or "") != benchmark_feature_date:
            errors.append(f"{ticker}:stale_feature_date:{row.get('date')}!=benchmark:{benchmark_feature_date}")
            continue
        for window in (5, 20, 60):
            row[f"relative_strength_qqq_{window}d_pct"] = (
                row.get(f"momentum_{window}d_pct") - row.get(f"qqq_momentum_{window}d_pct")
                if pd.notna(row.get(f"momentum_{window}d_pct")) and pd.notna(row.get(f"qqq_momentum_{window}d_pct"))
                else np.nan
            )
        row.update(
            {
                "ticker": ticker,
                "candidate_source": str(candidate.get("source") or ""),
                # 2026-08-04: thesis "정보성 하락은 안 산다"의 검증 입력.
                # bool() 캐스팅은 unknown(키 없음/None)을 False로 만들어 fail-open이 된다.
                # unknown을 3번째 상태로 살려야 `unknown abstain` arm을 검증할 수 있다.
                "news_or_earnings_flag": _news_flag_state(candidate),
                "news_signal_type": str(candidate.get("news_signal_type") or ""),
                "snapshot_data_quality": str(candidate.get("data_quality") or ""),
                "veto_reason": veto_map.get(ticker, ""),
            }
        )
        rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame, errors
    frame = frame.replace([np.inf, -np.inf], np.nan)
    eligible = (
        frame["close"].ge(5.0)
        & frame["dollar_volume_20d"].ge(15_000_000.0)
        & frame["change_pct"].abs().le(25.0)
    )
    # 후보 소스 화이트리스트(기본 빈값 = 현행 = 전체 허용).
    # 근거(2026-08-01 실측): 같은 신호에 실거래 규칙(TP/SL/갭)을 적용하면 소스별로 부호가 갈린다.
    #   day_losers   n=17 합 +117.9 평균 +6.94 승률 64.7% (갭TP 5건 전부 여기)
    #   day_gainers  n=28 합  -87.4 평균 -3.12
    #   most_actives n=20 합 -117.4 평균 -5.87
    # day_losers 단독 최적(TP+10/SL-20/5일)은 +123.5, 부트5% +20.3·양수 97.1%이고
    # 최상위 1건(AXTI +50.3) 제외 +67.7, 상위 3건 제외 +32.9로 소수 승자 의존이 아니다.
    # signals가 걸러지면 handoff(실주문)도 같은 집합을 읽으므로 한 곳에서 양쪽에 적용된다.
    allowed_raw = str(os.getenv("US_SWING_ALLOWED_SOURCES", "") or "").strip()
    if allowed_raw:
        allowed = {part.strip().lower() for part in allowed_raw.split(",") if part.strip()}
        if allowed:
            eligible &= frame["candidate_source"].astype(str).str.lower().isin(allowed)
    return frame[eligible].copy(), errors


def score_candidates(
    train: pd.DataFrame,
    candidates: pd.DataFrame,
    *,
    seeds: list[int],
    top_k: int,
) -> tuple[pd.DataFrame, str]:
    if candidates.empty:
        return candidates.copy(), ""
    if train.empty or train["target"].nunique() < 2:
        raise ValueError("training data insufficient")
    predicted: list[np.ndarray] = []
    probabilities: list[np.ndarray] = []
    for seed in seeds:
        regressor = HistGradientBoostingRegressor(
            learning_rate=0.05, max_iter=160, max_leaf_nodes=15,
            min_samples_leaf=35, l2_regularization=1.0, random_state=seed,
        )
        classifier = HistGradientBoostingClassifier(
            learning_rate=0.05, max_iter=160, max_leaf_nodes=15,
            min_samples_leaf=35, l2_regularization=1.0, random_state=seed,
        )
        regressor.fit(train[YAHOO_FEATURES], train["net_return_pct"])
        classifier.fit(train[YAHOO_FEATURES], train["target"])
        predicted.append(regressor.predict(candidates[YAHOO_FEATURES]))
        probabilities.append(classifier.predict_proba(candidates[YAHOO_FEATURES])[:, 1])
    scored = candidates.copy()
    scored["predicted_net_pct"] = np.mean(predicted, axis=0)
    scored["probability"] = np.mean(probabilities, axis=0)
    scored["net_rank"] = scored["predicted_net_pct"].rank(pct=True)
    scored["prob_rank"] = scored["probability"].rank(pct=True)
    scored["alpha_score"] = 0.5 * scored["net_rank"] + 0.5 * scored["prob_rank"]
    scored = scored.sort_values(["alpha_score", "predicted_net_pct"], ascending=False).copy()
    scored["rank"] = np.arange(1, len(scored) + 1)
    # top_k로 자르기 전 전체 랭킹을 보관한다. 정보성 이벤트 arm에서 상위가 제외되면
    # top_k 밖 후보가 rank1로 올라오므로, 잘린 프레임만으로는 arm 비교가 틀린다.
    full_ranking = scored.copy()
    scored = scored.head(max(1, top_k)).copy()
    scored.attrs["full_ranking"] = full_ranking
    identity = f"{train['session_date'].max()}|{len(train)}|{','.join(map(str, seeds))}"
    model_version = "us_swing_5d_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return scored, model_version


def write_signals(
    con: sqlite3.Connection,
    *,
    signal_date: str,
    scored: pd.DataFrame,
    model_version: str,
) -> int:
    created_at = datetime.now(timezone.utc).isoformat()
    con.execute("DELETE FROM signals WHERE signal_date=? AND status='PENDING'", (signal_date,))
    written = 0
    for row in scored.to_dict("records"):
        con.execute(
            """
            INSERT OR IGNORE INTO signals (
                signal_date,ticker,feature_date,model_version,rank,alpha_score,
                predicted_net_pct,probability,candidate_source,created_at,status,data_quality,
                reference_close,
                breadth_context_date,prior_spy_return_pct,prior_narrow_excess_pct,
                prior_rsp_spy_ratio_5d_pct,prior_adv_pct,breadth_context_state
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signal_date, str(row["ticker"]), str(row["date"]), model_version, int(row["rank"]),
                float(row["alpha_score"]), float(row["predicted_net_pct"]), float(row["probability"]),
                str(row.get("candidate_source") or ""), created_at, "PENDING", "point_in_time",
                float(row.get("close")) if pd.notna(row.get("close")) else None,
                row.get("breadth_context_date"), row.get("prior_spy_return_pct"),
                row.get("prior_narrow_excess_pct"), row.get("prior_rsp_spy_ratio_5d_pct"),
                row.get("prior_adv_pct"), row.get("breadth_context_state"),
            ),
        )
        written += int(con.execute("SELECT changes()").fetchone()[0])
    con.commit()
    return written


def verify_reference_closes(
    con: sqlite3.Connection, *, signal_date: str, tolerance_pct: float = 1.0
) -> dict[str, Any]:
    """기록 직후 전 랭크의 reference_close를 독립 소스(yfinance)와 대조해 오염 마킹.

    사고(2026-08-13): 가격 CSV의 미완성 봉 고착으로 FRVO 기준종가가 +11.7% 어긋난
    채 rank1이 됐다(주문은 독립 전일종가 가드가 차단, 당일 2/10 오염). 수집기 수리와
    별개의 이중 방어 — 불일치 종목은 data_quality='reference_contaminated'로 남긴다.
    랭킹·주문 개입 없음(코호트 통계 분리용). CSV와 같은 기준(auto_adjust=True).
    실패는 조용히 스킵(관측 결측이 파이프라인을 막으면 안 된다).
    """
    out: dict[str, Any] = {"checked": 0, "contaminated": [], "error": ""}
    try:
        rows = con.execute(
            "SELECT ticker, feature_date, reference_close FROM signals "
            "WHERE signal_date=? AND reference_close IS NOT NULL",
            (str(signal_date),),
        ).fetchall()
        if not rows:
            return out
        import yfinance as yf

        feature_date = str(rows[0][1])
        start = pd.Timestamp(feature_date)
        data = yf.download(
            [str(r[0]) for r in rows], start=start.strftime("%Y-%m-%d"),
            end=(start + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=True, progress=False, threads=True,
        )
        closes = data.get("Close") if data is not None else None
        if closes is None or closes.empty:
            out["error"] = "independent_source_empty"
            return out
        for ticker, fdate, ref in rows:
            ticker = str(ticker)
            try:
                series = closes[ticker] if ticker in getattr(closes, "columns", []) else closes
                actual = float(series.dropna().iloc[-1])
            except Exception:
                continue
            if not (actual > 0 and ref):
                continue
            out["checked"] += 1
            deviation_pct = (float(ref) / actual - 1.0) * 100.0
            if abs(deviation_pct) > float(tolerance_pct):
                con.execute(
                    "UPDATE signals SET data_quality='reference_contaminated' "
                    "WHERE signal_date=? AND ticker=?",
                    (str(signal_date), ticker),
                )
                out["contaminated"].append(
                    {"ticker": ticker, "reference_close": float(ref),
                     "independent_close": actual, "deviation_pct": round(deviation_pct, 2)}
                )
        if out["contaminated"]:
            con.commit()
            print(f"[reference verify] 오염 {len(out['contaminated'])}건 마킹: "
                  + ", ".join(f"{c['ticker']}({c['deviation_pct']:+.1f}%)" for c in out["contaminated"]))
    except Exception as exc:
        out["error"] = str(exc)[:120]
    return out


def _fx_map_from_research_db(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT date, usdkrw FROM usdkrw_daily WHERE usdkrw IS NOT NULL").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        con.close()
    return {str(date): float(value) for date, value in rows}


def refresh_fx_map(base: dict[str, float], *, start: str, end: str) -> dict[str, float]:
    output = dict(base)
    try:
        import yfinance as yf

        raw = yf.download(
            "KRW=X", start=start, end=end, interval="1d", auto_adjust=True,
            repair=True, progress=False, threads=False, multi_level_index=False,
        )
        if raw is not None and not raw.empty:
            frame = raw.reset_index().rename(columns={"Date": "date", "Close": "usdkrw"})
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
            frame["usdkrw"] = pd.to_numeric(frame["usdkrw"], errors="coerce")
            output.update({str(row.date): float(row.usdkrw) for row in frame.dropna(subset=["date", "usdkrw"]).itertuples()})
    except Exception:
        pass
    return output


def _latest_fx(fx_map: dict[str, float], date: str) -> float | None:
    eligible = [key for key in fx_map if str(key) <= str(date)]
    if not eligible:
        return None
    value = fx_map[max(eligible)]
    return float(value) if np.isfinite(value) and float(value) > 100 else None


def _record_pool_size(session_date: str, *, pool_n: int, scored_n: int) -> None:
    """세션 후보 풀 크기 기록 — "후보 수=신호"(2026-08-07 F2 발견) forward 검증 축.

    signals 테이블은 top_k(10)로 잘려 풀 크기를 복원할 수 없어 별도 원장에 남긴다.
    pool_n=eligibility 통과(소스 필터 포함, veto 전), scored_n=veto 후 채점 대상.
    세션당 1행 멱등. 슬롯·일한도 변경 판단은 게이트+운영자 — 여기는 기록만.
    """
    path = ROOT / "data" / "shadow" / "us_swing_pool_size.jsonl"
    try:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    if json.loads(line).get("session_date") == str(session_date):
                        return
                except ValueError:
                    continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"session_date": str(session_date),
                                     "pool_n": int(pool_n), "scored_n": int(scored_n)},
                                    ensure_ascii=False) + "\n")
    except OSError:
        pass


def _record_tp_capture(session_date: str, candidates: "pd.DataFrame") -> None:
    """TP 포획 조건(ATR 하한) 관측 원장 — 2026-08-11 design_tp_capture_lane.

    실측(T1): ATR 상위 25% 코호트가 TP 적중 55%·계약 net +2.71%(양 기간 재현)로
    모델 rank1(+0.12%)을 크게 앞선다. 임계는 **past-only 확장창**(이 원장에 쌓인
    과거 atr_pct만)으로 산출해 lookahead를 만들지 않는다.

    주문 경로 무접촉 — 조건 통과 여부만 기록하고, 성과는 판정 시 signals의
    net_krw_pct와 (session_date, ticker)로 조인해 검증한다.
    """
    path = ROOT / "data" / "shadow" / "us_tp_capture_shadow.jsonl"
    try:
        history: list[float] = []
        seen_session = False
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if str(row.get("session_date")) == str(session_date):
                    seen_session = True
                value = row.get("atr_pct")
                if isinstance(value, (int, float)) and value == value:
                    history.append(float(value))
        if seen_session:
            return
        threshold = float(np.quantile(history, 0.75)) if len(history) >= 150 else None
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for row in candidates.to_dict("records"):
                atr = row.get("atr_pct")
                atr = float(atr) if atr is not None and atr == atr else None
                handle.write(json.dumps({
                    "session_date": str(session_date),
                    "ticker": str(row.get("ticker") or ""),
                    "candidate_source": str(row.get("candidate_source") or row.get("source") or ""),
                    "atr_pct": atr,
                    "threshold_p75_past_only": threshold,
                    "passed": bool(threshold is not None and atr is not None and atr >= threshold),
                    "history_n": len(history),
                }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _record_candidate_age(session_date: str, candidates: "pd.DataFrame") -> None:
    """후보 관측 연령 원장 — 2026-08-11 B2 최대 발견의 forward 배선.

    실측(candidate_audit 33만 행, US 급락 −5%↓ 3일 수익):
      D0(처음 보는 종목) +0.16% / D1-3 +3.77% / D4-10 +6.24% / D10+ **+9.97%**
    유동성 통제 후에도 모든 계층에서 단조 증가 — 공선성으로 설명되지 않는다.
    우리 후보의 88%가 D0인데 이 정보를 지금 전혀 쓰지 않는다.

    `candidate_registry_first`(5,371행, 인덱스 있음 — 조회 ~0.05초)에서 최초 관측일을
    읽어 연령(일)을 기록한다. 주문·채점 경로 무접촉, 실패는 조용히 스킵.
    """
    path = ROOT / "data" / "shadow" / "us_candidate_age_shadow.jsonl"
    audit_db = ROOT / "data" / "audit" / "candidate_audit.db"
    try:
        if not audit_db.exists():
            return
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    if json.loads(line).get("session_date") == str(session_date):
                        return
                except ValueError:
                    continue
        tickers = [str(t).upper() for t in candidates.get("ticker", []) if str(t).strip()]
        if not tickers:
            return
        con = sqlite3.connect(f"file:{audit_db}?mode=ro", uri=True, timeout=20)
        try:
            con.execute("PRAGMA busy_timeout=15000")
            marks = ",".join("?" * len(tickers))
            rows = con.execute(
                f"""SELECT ticker, MIN(first_seen_at) FROM candidate_registry_first
                    WHERE runtime_mode='live' AND market='US' AND ticker IN ({marks})
                    GROUP BY ticker""", tickers).fetchall()
        finally:
            con.close()
        first = {str(t).upper(): str(ts or "")[:10] for t, ts in rows}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for ticker in tickers:
                seen = first.get(ticker, "")
                age = None
                if seen:
                    try:
                        age = (pd.Timestamp(str(session_date)) - pd.Timestamp(seen)).days
                    except (ValueError, TypeError):
                        age = None
                handle.write(json.dumps({
                    "session_date": str(session_date), "ticker": ticker,
                    "first_seen_date": seen or None, "age_days": age,
                    "bucket": ("unknown" if age is None else
                               "D0" if age <= 0 else "D1-3" if age <= 3 else
                               "D4-10" if age <= 10 else "D10+"),
                }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _record_premarket(session_date: str, candidates: "pd.DataFrame") -> None:
    """프리마켓 스냅샷 관측 원장 — 2026-08-11 신설(주문·채점 경로 무접촉).

    runner 실행 시각(22:20 KST = 09:20 ET)은 개장 10분 전으로 프리마켓 유동성이
    가장 붙는 구간이다. 후보 풀의 프리마켓 마지막가·누적 거래량을 남겨, 정산 후
    (session_date, ticker)로 signals와 조인해 세 가지를 검증한다:
      ① 갭 가드 3%(US_SWING_ORDER_MAX_ABS_GAP_PCT)의 실측 근거 — 지금은 없다
      ② 급락 다음날 되돌림이 개장 전에 얼마나 끝나는가(= 우리 진입이 늦은가)
      ③ U7(진입 시점)이 분봉 부족으로 판정 불가였던 문제의 대체 데이터원

    KIS를 쓰지 않는다(라이브 시세 경로와 분리 — 08-04 레이트리밋 사고 원칙).
    yfinance 벌크 1회, 실패는 조용히 스킵(관측 결측이 파이프라인을 막으면 안 된다).
    """
    path = ROOT / "data" / "shadow" / "us_premarket_shadow.jsonl"
    try:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    if json.loads(line).get("session_date") == str(session_date):
                        return
                except ValueError:
                    continue
        tickers = [str(t).upper() for t in candidates.get("ticker", []) if str(t).strip()]
        if not tickers:
            return
        import datetime as _dt

        import yfinance as yf

        data = yf.download(tickers, period="1d", interval="5m", prepost=True,
                           progress=False, threads=False, group_by="ticker")
        rows = []
        for ticker in tickers:
            try:
                frame = data[ticker] if len(tickers) > 1 else data
                frame = frame.dropna(subset=["Close"])
                if frame.empty:
                    continue
                frame.index = frame.index.tz_convert("America/New_York")
                pre = frame[frame.index.time < _dt.time(9, 30)]
                if pre.empty:
                    continue
                rows.append({
                    "session_date": str(session_date), "ticker": ticker,
                    "premarket_last": round(float(pre["Close"].iloc[-1]), 4),
                    "premarket_volume": int(pre["Volume"].sum()),
                    "premarket_bars": int(len(pre)),
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                continue
        if rows:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _record_market_width(session_date: str) -> None:
    """시장 전체 낙폭 폭 기록 (관측 전용, 주문·채점 경로 무접촉).

    2026-08-08: 수집기 컷(상위 ~10)이 풀 크기 신호(F2)의 천장이라, 야후 스크리너
    count=100으로 "오늘 시장 전체의 적격 낙폭주 수"를 별도 원장에 남긴다.
    이 값은 신호·주문 어디에도 입력되지 않는다 — 사냥철 강도 관측 전용.
    실패는 조용히 스킵(관측 결측이 파이프라인을 막으면 안 된다).
    """
    path = ROOT / "data" / "shadow" / "us_market_width.jsonl"
    try:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    if json.loads(line).get("session_date") == str(session_date):
                        return
                except ValueError:
                    continue
        import requests

        resp = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved",
            params={"scrIds": "day_losers", "count": 100},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
        )
        quotes = (resp.json().get("finance", {}).get("result") or [{}])[0].get("quotes", [])
        n5 = n5_eligible = 0
        # 2026-08-12: 집계만으론 "컷 10 밖 후보의 사후 성과"를 영영 물을 수 없어
        # 종목별 행을 함께 남긴다(관측 전용 유지 — 신호·주문 어디에도 입력되지 않는다).
        ticker_rows: list[dict] = []
        for q in quotes:
            chg = q.get("regularMarketChangePercent")
            px = q.get("regularMarketPrice")
            vol = q.get("regularMarketVolume") or 0
            if chg is None or px is None or chg > -5:
                continue
            n5 += 1
            eligible = bool(px >= 5 and px * vol >= 15e6)
            if eligible:
                n5_eligible += 1
            ticker_rows.append({
                "ticker": str(q.get("symbol") or ""),
                "chg_pct": round(float(chg), 2),
                "price": round(float(px), 4),
                "dollar_vol": round(float(px) * float(vol), 0),
                "eligible": eligible,
            })
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "session_date": str(session_date), "screener_rows": len(quotes),
                "losers_le_minus5": n5, "eligible_le_minus5": n5_eligible,
                "tickers": ticker_rows,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


SECTOR_ETFS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC")


def _record_sector_context(session_date: str, candidates: "pd.DataFrame") -> None:
    """섹터 컨텍스트 관측 (2026-08-16 운영자 승인) — 주문·선정 무접촉, 기록만.

    발견(08-16 실측, day_losers 정산 57건): 신호일 XLK 방향이 성과와 갈린다 —
    상승일 +3.17%/건(n=26) vs 하락일 −0.61%(n=31), **양월 재현**(7월 +8.73 vs
    +0.83, 8월 +2.71 vs −1.79), VIX와 교락 없음(중앙 15.9 동일). brain 이슈패턴
    P047/P051("섹터 미스매치·역풍 진입")이 반복 지목한 축과 독립적으로 일치.

    기록만 한다 — 신호·주문 어디에도 입력되지 않는다. 판정은 세션당 1행을
    (session_date, ticker)로 signals의 net_krw_pct와 조인해 사후에 한다.
    섹터 전체를 남기는 이유: 지금 XLK만 보이지만 종목별 소속 섹터로 나중에
    "자기 섹터 역풍" 축을 복원하려면 11개가 다 필요하다. 실패는 조용히 스킵.
    """
    path = ROOT / "data" / "shadow" / "us_sector_context.jsonl"
    try:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        if json.loads(line).get("session_date") == str(session_date):
                            return
                    except ValueError:
                        continue
        import yfinance as yf

        start = (pd.Timestamp(session_date) - pd.Timedelta(days=12)).strftime("%Y-%m-%d")
        raw = yf.download(list(SECTOR_ETFS), start=start,
                          end=(pd.Timestamp(session_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                          interval="1d", auto_adjust=True, progress=False, threads=True)
        closes = raw["Close"]
        sectors: dict[str, Any] = {}
        for etf in SECTOR_ETFS:
            try:
                series = closes[etf].dropna()
                # signal_date 이전 마지막 마감 세션 = 신호 피처의 기준일
                series = series[series.index.strftime("%Y-%m-%d") < str(session_date)]
                if len(series) < 6:
                    continue
                sectors[etf] = {
                    "ret_1d_pct": round(float(series.iloc[-1] / series.iloc[-2] - 1) * 100, 3),
                    "ret_5d_pct": round(float(series.iloc[-1] / series.iloc[-6] - 1) * 100, 3),
                }
            except Exception:
                continue
        if not sectors:
            return
        up = sum(1 for v in sectors.values() if v["ret_1d_pct"] > 0)
        payload = {
            "session_date": str(session_date),
            "feature_basis": "last_close_before_session",
            "sectors": sectors,
            "sectors_up_1d": up,
            "sectors_n": len(sectors),
            "breadth_ratio": round(up / len(sectors), 3),
            "candidate_n": int(len(candidates)),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        xlk = sectors.get("XLK", {}).get("ret_1d_pct")
        print(f"[sector ctx] {session_date} XLK {xlk:+.2f}% | 상승섹터 {up}/{len(sectors)}"
              if xlk is not None else f"[sector ctx] {session_date} 상승섹터 {up}/{len(sectors)}")
    except Exception as exc:
        print(f"[sector ctx] 관측 스킵: {str(exc)[:120]}", file=sys.stderr)


def _record_wide_net_shadow(
    session_date: str,
    *,
    pool: pd.DataFrame,
    pool_rank1: dict[str, Any] | None,
    train: pd.DataFrame,
    seeds: list[int],
    price_dir: Path,
    cost_pct: float,
    tp_pct: float,
    sl_pct: float,
) -> None:
    """넓은 그물 병렬 채점 관측 (2026-08-15 운영자 승인) — 주문·선정 무접촉, 기록만.

    질문: 수집기 컷(~10) 밖의 적격 급락주까지 풀에 넣으면 모델이 더 좋은 rank1을
    뽑는가(그물 폭 F2 축의 직접 A/B). 시장폭 원장(us_market_width.jsonl)의 적격
    종목 전수를 본 채점과 **같은 train/seeds**로 채점해 '넓은 그물 rank1'을 별도
    원장에 기록하고, 이전 세션 행은 두 rank1을 같은 계약(다음 시가 진입,
    TP/SL/D5, 동일 비용, 동시터치 SL 우선)으로 짝지어 가상 정산한다.
    실패는 조용히 스킵 — 관측 결측이 파이프라인을 막으면 안 된다.
    """
    path = ROOT / "data" / "shadow" / "us_wide_net_shadow.jsonl"
    try:
        import yfinance as yf

        rows: list[dict[str, Any]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass

        # 1) 이전 PENDING 행 가상 정산 (두 rank1 동일 방법 — 공정 짝비교)
        changed = False
        pending = [r for r in rows if r.get("status") == "PENDING" and r.get("session_date") < session_date]
        if pending:
            tickers = sorted({t for r in pending for t in (
                (r.get("wide_rank1") or {}).get("ticker"), (r.get("pool_rank1") or {}).get("ticker"))
                if t})
            bars_raw = yf.download(tickers, period="1mo", interval="1d",
                                   auto_adjust=True, progress=False, threads=True)

            def _settle_one(ticker: str, sess: str):
                try:
                    if len(tickers) > 1:
                        o = bars_raw["Open"][ticker].dropna()
                        h = bars_raw["High"][ticker].dropna()
                        lo = bars_raw["Low"][ticker].dropna()
                        c = bars_raw["Close"][ticker].dropna()
                    else:
                        o = bars_raw["Open"].squeeze().dropna()
                        h = bars_raw["High"].squeeze().dropna()
                        lo = bars_raw["Low"].squeeze().dropna()
                        c = bars_raw["Close"].squeeze().dropna()
                    dates = [d.strftime("%Y-%m-%d") for d in o.index]
                    after = [i for i, d in enumerate(dates) if d > sess]
                    if not after:
                        return None
                    e = float(o.iloc[after[0]])
                    if e <= 0:
                        return None
                    tp_price, sl_price = e * (1 + tp_pct), e * (1 - sl_pct)
                    win = after[:5]
                    for idx in win:
                        if float(lo.iloc[idx]) <= sl_price:
                            return round(-sl_pct * 100 - cost_pct, 3)
                        if float(h.iloc[idx]) >= tp_price:
                            return round(tp_pct * 100 - cost_pct, 3)
                    if len(win) < 5:
                        return None  # 만기 전
                    return round((float(c.iloc[win[-1]]) / e - 1) * 100 - cost_pct, 3)
                except Exception:
                    return None

            for r in pending:
                wt = (r.get("wide_rank1") or {}).get("ticker")
                pt = (r.get("pool_rank1") or {}).get("ticker")
                wn = _settle_one(wt, r["session_date"]) if wt else None
                pn = _settle_one(pt, r["session_date"]) if pt else None
                if wn is not None and (pn is not None or not pt):
                    r["wide_net_pct"], r["pool_net_pct"] = wn, pn
                    r["status"] = "SETTLED"
                    changed = True

        # 2) 오늘 세션 기록 (멱등)
        if not any(r.get("session_date") == session_date for r in rows):
            width_row = None
            width_path = ROOT / "data" / "shadow" / "us_market_width.jsonl"
            if width_path.exists():
                for line in width_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            w = json.loads(line)
                            if w.get("session_date") == session_date and w.get("tickers"):
                                width_row = w
                                break
                        except ValueError:
                            continue
            if width_row is not None and pool_rank1:
                pool_tickers = {str(t).upper() for t in pool["ticker"].astype(str)}
                eligible = [str(t["ticker"]).upper() for t in width_row["tickers"]
                            if t.get("eligible") and str(t.get("ticker") or "").strip()]
                wide_adds = [t for t in eligible if t not in pool_tickers]
                wide_frame = _build_wide_features(wide_adds, price_dir=price_dir, session_date=session_date)
                need = [*YAHOO_FEATURES, "ticker", "candidate_source", "close", "date"]
                pool_part = pool[[c for c in need if c in pool.columns]].copy()
                combined = pd.concat([pool_part, wide_frame], ignore_index=True) if not wide_frame.empty else pool_part
                scored_wide, _ = score_candidates(train, combined, seeds=seeds, top_k=3)
                top = scored_wide.iloc[0].to_dict() if not scored_wide.empty else {}
                wide_r1 = {
                    "ticker": str(top.get("ticker") or ""),
                    "source": str(top.get("candidate_source") or ""),
                    "probability": float(top.get("probability") or 0.0),
                    "predicted_net_pct": float(top.get("predicted_net_pct") or 0.0),
                    "ref_close": float(top.get("close") or 0.0),
                }
                rows.append({
                    "session_date": session_date,
                    "pool_n": int(len(pool)),
                    "eligible_n": int(len(eligible)),
                    "wide_added_n": int(len(wide_frame)),
                    "wide_rank1": wide_r1,
                    "pool_rank1": pool_rank1,
                    "rank1_changed": bool(wide_r1["ticker"] and wide_r1["ticker"] != pool_rank1.get("ticker")),
                    "contract": f"next_open_TP{int(tp_pct*100)}_SL{int(sl_pct*100)}_D5_cost{cost_pct}",
                    "status": "PENDING",
                })
                changed = True
                print(f"[wide net] eligible {len(eligible)} (풀 밖 +{len(wide_frame)}) → "
                      f"wide rank1 {wide_r1['ticker']}({wide_r1['source']}) vs pool rank1 {pool_rank1.get('ticker')}"
                      f"{' [교체]' if rows[-1]['rank1_changed'] else ' [동일]'}")

        if changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                for r in rows:
                    handle.write(json.dumps(r, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[wide net] 관측 스킵: {str(exc)[:120]}", file=sys.stderr)


def _build_wide_features(tickers: list[str], *, price_dir: Path, session_date: str) -> pd.DataFrame:
    """그물 밖 종목의 피처 프레임 — 본 채점과 동일 규약(build_ticker_frame + 벤치마크).

    yfinance auto_adjust=True(수집기와 동일 기준), date < session_date 필터로
    진행 중 봉 배제(무-lookahead). 벤치마크 feature_date와 다른 stale 행은 제외.
    """
    if not tickers:
        return pd.DataFrame()
    import yfinance as yf

    benchmark = _benchmark_frame(price_dir, before_date=session_date)
    if benchmark.empty:
        return pd.DataFrame()
    bench_date = str(benchmark["date"].dropna().astype(str).max())
    raw = yf.download(tickers, period="9mo", interval="1d", auto_adjust=True,
                      progress=False, threads=True)
    out: list[dict[str, Any]] = []
    for ticker in tickers:
        try:
            if len(tickers) > 1:
                df = pd.DataFrame({
                    "date": raw["Close"][ticker].dropna().index.strftime("%Y-%m-%d"),
                    "open": raw["Open"][ticker].dropna().values,
                    "high": raw["High"][ticker].dropna().values,
                    "low": raw["Low"][ticker].dropna().values,
                    "close": raw["Close"][ticker].dropna().values,
                    "volume": raw["Volume"][ticker].dropna().values,
                })
            else:
                sq = raw.dropna()
                df = pd.DataFrame({
                    "date": sq.index.strftime("%Y-%m-%d"),
                    "open": sq["Open"].squeeze().values,
                    "high": sq["High"].squeeze().values,
                    "low": sq["Low"].squeeze().values,
                    "close": sq["Close"].squeeze().values,
                    "volume": sq["Volume"].squeeze().values,
                })
            if len(df) < 80:
                continue
            features = build_ticker_frame(df)
            features = features[features["date"].astype(str) < str(session_date)].merge(
                benchmark, on="date", how="left")
            if features.empty:
                continue
            row = features.iloc[-1].to_dict()
            if str(row.get("date") or "") != bench_date:
                continue
            for window in (5, 20, 60):
                mom = row.get(f"momentum_{window}d_pct")
                qqq = row.get(f"qqq_momentum_{window}d_pct")
                row[f"relative_strength_qqq_{window}d_pct"] = (
                    mom - qqq if pd.notna(mom) and pd.notna(qqq) else np.nan)
            row["ticker"] = ticker
            row["candidate_source"] = "wide_net"
            out.append(row)
        except Exception:
            continue
    return pd.DataFrame(out)


def resolve_shadow_contract(
    policy: dict[str, Any],
    *,
    base_order_budget_krw: float = 500_000.0,
    authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """shadow가 쓸 계약을 실주문과 같은 규칙으로 계산한다.

    shadow runner는 봇과 별도 프로세스로도 돌기 때문에 `os.getenv`로 읽는다.
    같은 `.env.live` / start-config를 보므로 실주문 경로와 값이 일치한다.
    """

    configured_max = float(os.getenv("US_SWING_ORDER_MAX_KRW", "250000") or 250000.0)
    ack = os.getenv("US_SWING_OPERATOR_MICRO_OVERRIDE_ACK", "")
    if authority is not None:
        override = operator_override_active(ack=ack, blockers=list(authority.get("blockers") or []))
    else:
        override = str(ack or "") == OPERATOR_MICRO_OVERRIDE_ACK
    # 실행 shadow는 authority가 shadow로 강등돼 있어도 **micro 계약으로 관찰**하는 원장이다
    # (그게 이 원장의 목적: 승격 판정에 쓸 forward를 미리 쌓는다).
    # 오버라이드가 붙으면 예산이 micro multiplier 대신 운영자 절대캡으로 바뀐다.
    effective_mode = "micro"
    absolute_cap = configured_max if override else 0.0
    return resolve_execution_contract(
        policy=policy,
        effective_mode=effective_mode,
        configured_max_order_krw=configured_max,
        base_order_budget_krw=base_order_budget_krw,
        absolute_order_cap_krw=absolute_cap,
        allowed_sources_raw=os.getenv("US_SWING_ALLOWED_SOURCES", ""),
        override_active=override,
        # 실주문 핸드오프(order bridge)와 같은 env 키·기본값 — 값이 다르면 계약이 갈라진다.
        min_probability=float(os.getenv("US_SWING_ORDER_MIN_PROB", "0.55") or 0.55),
        min_predicted_net_pct=float(os.getenv("US_SWING_ORDER_MIN_PREDICTED_NET_PCT", "0.25") or 0.25),
        hurdles_enforced=str(os.getenv("US_SWING_ORDER_ABSOLUTE_HURDLES_ENFORCED", "false")).strip().lower()
        in ("1", "true", "yes", "y", "on"),
    )


def annotate_execution_shadow(
    con: sqlite3.Connection,
    *,
    signal_date: str,
    fx_map: dict[str, float],
    policy: dict[str, Any],
    base_order_budget_krw: float = 500_000.0,
) -> dict[str, Any]:
    """실주문과 **같은 계약**으로 rank1을 잡는다(예산·슬롯·소스 화이트리스트 공유).

    2026-08-04 이전에는 여기가 5만원/슬롯1 고정이라 실주문(30만원/슬롯3)과 갈라졌다.
    상세와 실측 사고 기록은 `runtime/us_swing_execution_contract` 참고.
    """
    ensure_handoff_schema(con)
    contract = resolve_shadow_contract(policy, base_order_budget_krw=base_order_budget_krw)
    budget = float(contract["budget_krw"])
    max_open_slots = max(1, int(contract["max_open_slots"]))
    max_hold = int(contract["max_hold_sessions"])
    allowed_sources = {str(item).lower() for item in (contract.get("allowed_sources") or [])}
    fx = _latest_fx(fx_map, signal_date)
    evaluated_at = datetime.now(timezone.utc).isoformat()

    # 미청산 건을 모두 세어 다중 슬롯을 판정한다(기존에는 직전 1건만 봤다).
    # 2026-08-07: 현재 계약(contract_id) 행만 센다 — 구계약 shadow 행이 슬롯을
    # 점유해 실주문(FRMI 08-03 체결)이 표본에서 빠지는 사고의 재발 방지.
    # 브로커에 실재하는 포지션의 슬롯은 실주문 경로(order bridge)가 따로 센다.
    open_rows = con.execute(
        """SELECT signal_date,execution_shadow_exit_date FROM signals
           WHERE execution_shadow_eligible=1 AND signal_date<?
             AND COALESCE(execution_shadow_contract_id,'')=?
           ORDER BY signal_date DESC""",
        (str(signal_date), str(contract["contract_id"])),
    ).fetchall()
    occupied: list[str] = []
    for prior_date, actual_exit in open_rows:
        prior_date = str(prior_date)
        exit_date = str(actual_exit or "")
        if exit_date:
            if str(signal_date) <= exit_date:
                occupied.append(prior_date)
            continue
        later_sessions = con.execute(
            "SELECT COUNT(DISTINCT signal_date) FROM signals WHERE signal_date>? AND signal_date<=?",
            (prior_date, str(signal_date)),
        ).fetchone()[0]
        if int(later_sessions or 0) < max_hold:
            occupied.append(prior_date)
    slot_free = len(occupied) < max_open_slots
    slot_reason = "" if slot_free else f"slots_full_{len(occupied)}/{max_open_slots}:{','.join(occupied[:3])}"

    hurdles_enforced = bool(contract.get("hurdles_enforced"))
    min_probability = float(contract.get("min_probability") or 0.0)
    min_predicted_net = float(contract.get("min_predicted_net_pct") or 0.0)
    rows = con.execute(
        """SELECT ticker,rank,reference_close,candidate_source,probability,predicted_net_pct
           FROM signals WHERE signal_date=? ORDER BY rank""",
        (str(signal_date),),
    ).fetchall()
    selected: dict[str, Any] = {}
    for ticker, rank, reference_close, candidate_source, probability, predicted_net in rows:
        eligible = 0
        qty = 0
        price_krw = None
        reason = "rank_outside_micro_contract"
        if int(rank) == 1:
            reference = float(reference_close) if reference_close is not None else None
            price_krw = reference * fx if reference and fx else None
            source = str(candidate_source or "").lower()
            # 절대 허들 — 실주문 핸드오프(evaluate_swing_handoff)와 같은 판정·같은 사유 문자열.
            # 차단 건은 execution 코호트에서 빠지고 counterfactual([A2] 뷰)로만 관측된다.
            hurdle_reason = ""
            if hurdles_enforced:
                if probability is None or float(probability) < min_probability:
                    hurdle_reason = "probability_below_hurdle"
                elif predicted_net is None or float(predicted_net) < min_predicted_net:
                    hurdle_reason = "predicted_net_below_hurdle"
            if allowed_sources and source not in allowed_sources:
                # 실주문 경로가 거르는 소스는 shadow 표본에도 넣지 않는다.
                reason = f"source_outside_contract:{source or 'unknown'}"
            elif hurdle_reason:
                reason = hurdle_reason
            elif not slot_free:
                reason = slot_reason
            elif not fx:
                reason = "fx_missing"
            elif not reference or reference <= 0:
                reason = "reference_price_missing"
            else:
                qty = int(budget // price_krw) if budget > 0 else 0
                if qty <= 0:
                    reason = "micro_budget_cannot_buy_one_share"
                else:
                    eligible = 1
                    reason = "selected_rank1_whole_share"
                    selected = {"ticker": str(ticker), "rank": 1, "qty": qty}
        con.execute(
            """UPDATE signals SET execution_shadow_eligible=?,execution_shadow_reason=?,
                execution_shadow_qty=?,execution_shadow_budget_krw=?,
                execution_shadow_entry_proxy_usd=?,execution_shadow_entry_price_krw=?,
                execution_shadow_fx=?,execution_shadow_policy=?,execution_shadow_evaluated_at=?,
                execution_shadow_contract_id=?,execution_shadow_max_open_slots=?,
                execution_shadow_allowed_sources=?
                WHERE signal_date=? AND ticker=?""",
            (
                eligible,
                reason,
                qty,
                budget,
                reference_close,
                price_krw,
                fx,
                "rank1_skip_v1",
                evaluated_at,
                str(contract["contract_id"]),
                int(max_open_slots),
                ",".join(sorted(allowed_sources)),
                str(signal_date),
                str(ticker),
            ),
        )
    con.commit()
    return {
        "policy": "rank1_skip_v1",
        "contract_id": str(contract["contract_id"]),
        "contract": contract,
        "budget_krw": budget,
        "fx": fx,
        "slot_free": slot_free,
        "slot_reason": slot_reason,
        "open_slots_used": len(occupied),
        "max_open_slots": max_open_slots,
        "selected": selected,
    }


def expected_maturity_session(signal_date: str, max_hold_sessions: int) -> str:
    """Resolve the last session in an inclusive fixed-horizon US hold."""

    count = max(1, int(max_hold_sessions or 1))
    try:
        import exchange_calendars as ec

        calendar = ec.get_calendar("XNYS")
        # Positive windows include the anchor session. A five-session
        # inclusive hold starting Friday therefore matures Thursday.
        sessions = calendar.sessions_window(str(signal_date), count)
        return str(sessions[-1].date())
    except Exception:
        current = pd.Timestamp(str(signal_date))
        observed = 1
        while observed < count:
            current += pd.Timedelta(days=1)
            if current.weekday() < 5:
                observed += 1
        return current.strftime("%Y-%m-%d")


def summarize_active_execution_shadow(
    con: sqlite3.Connection,
    *,
    price_dir: Path,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Expose legitimate one-slot maturity instead of labelling it stale."""

    max_hold = int((policy.get("execution_contract") or {}).get("max_hold_sessions", 5) or 5)
    rows = con.execute(
        """SELECT signal_date,ticker,entry_date,execution_shadow_entry_fill_usd,
                  execution_shadow_qty,execution_shadow_reason
           FROM signals
           WHERE execution_shadow_eligible=1
             AND execution_shadow_net_krw_pct IS NULL
           ORDER BY signal_date,ticker"""
    ).fetchall()
    active: list[dict[str, Any]] = []
    for signal_date, ticker, entry_date, entry_fill, qty, reason in rows:
        observed_sessions = 0
        latest_bar_date = ""
        path = price_dir / f"us_{str(ticker).upper()}.csv"
        if path.exists():
            try:
                bars = _read_price(path)
                future = bars[bars["date"].astype(str) >= str(signal_date)].sort_values("date")
                observed_sessions = int(len(future))
                if not future.empty:
                    latest_bar_date = str(future.iloc[-1]["date"])
            except Exception:
                pass
        expected_session = expected_maturity_session(str(signal_date), max_hold)
        maturity_due = observed_sessions >= max_hold
        active.append({
            "state": "MATURITY_DUE" if maturity_due else "ACTIVE_UNMATURED",
            "signal_date": str(signal_date),
            "ticker": str(ticker),
            "entry_date": str(entry_date or signal_date),
            "entry_fill_usd": float(entry_fill) if entry_fill is not None else None,
            "qty": int(qty or 0),
            "execution_shadow_reason": str(reason or ""),
            "observed_sessions": observed_sessions,
            "max_hold_sessions": max_hold,
            "expected_maturity_session": expected_session,
            "latest_bar_date": latest_bar_date,
            "maturity_due": maturity_due,
        })
    return {
        "state": "ACTIVE_UNMATURED" if active and not any(row["maturity_due"] for row in active) else (
            "MATURITY_DUE" if active else "IDLE"
        ),
        "active_count": len(active),
        "rows": active,
    }


def mature_pending(
    con: sqlite3.Connection,
    *,
    price_dir: Path,
    fx_map: dict[str, float],
    cost_pct: float,
    entry_slippage_pct: float = 0.5,
    tp_pct: float = 0.12,
    sl_pct: float = 0.25,
) -> dict[str, int]:
    ensure_handoff_schema(con)
    pending = con.execute(
        """SELECT signal_date,ticker,execution_shadow_eligible,execution_shadow_budget_krw
           FROM signals WHERE status='PENDING' ORDER BY signal_date,ticker"""
    ).fetchall()
    matured = 0
    waiting = 0
    execution_matured = 0
    execution_unaffordable = 0
    for signal_date, ticker, execution_eligible, execution_budget in pending:
        path = price_dir / f"us_{str(ticker).upper()}.csv"
        if not path.exists():
            waiting += 1
            continue
        bars = _read_price(path)
        # signal_date is the intended entry session produced before that session opens.
        # The feature bar is the prior session, so entry is signal_date open and the
        # outcome is the fifth session close including the entry session.
        future = bars[bars["date"].astype(str) >= str(signal_date)].sort_values("date")
        if int(execution_eligible or 0) == 1 and not future.empty:
            first = future.iloc[0]
            first_fx = fx_map.get(str(first["date"])) or _latest_fx(fx_map, str(first["date"]))
            shadow_entry = float(first["open"]) * (1.0 + float(entry_slippage_pct) / 100.0)
            budget = float(execution_budget or 0.0)
            shadow_qty = int(budget // (shadow_entry * first_fx)) if budget > 0 and first_fx else 0
            if shadow_qty <= 0:
                con.execute(
                    """UPDATE signals SET execution_shadow_eligible=0,
                        execution_shadow_reason='entry_open_unaffordable',execution_shadow_qty=0,
                        execution_shadow_entry_fill_usd=?,execution_shadow_entry_price_krw=?,
                        execution_shadow_fx=? WHERE signal_date=? AND ticker=?""",
                    (
                        shadow_entry,
                        shadow_entry * first_fx if first_fx else None,
                        first_fx,
                        signal_date,
                        ticker,
                    ),
                )
                execution_eligible = 0
                execution_unaffordable += 1
            else:
                con.execute(
                    """UPDATE signals SET entry_date=COALESCE(entry_date,?),
                        entry_price=COALESCE(entry_price,?),entry_fx=COALESCE(entry_fx,?),
                        execution_shadow_reason='entry_open_whole_share_confirmed',
                        execution_shadow_qty=?,execution_shadow_entry_fill_usd=?,
                        execution_shadow_entry_price_krw=?,execution_shadow_fx=?
                       WHERE signal_date=? AND ticker=?""",
                    (
                        str(first["date"]),
                        float(first["open"]),
                        first_fx,
                        shadow_qty,
                        shadow_entry,
                        shadow_entry * first_fx,
                        first_fx,
                        signal_date,
                        ticker,
                    ),
                )
        if len(future) < 5:
            waiting += 1
            continue
        entry = future.iloc[0]
        exit_row = future.iloc[4]
        entry_fx = fx_map.get(str(entry["date"]))
        exit_fx = fx_map.get(str(exit_row["date"]))
        if not entry_fx or not exit_fx:
            waiting += 1
            continue
        entry_price = float(entry["open"])
        exit_price = float(exit_row["close"])
        gross_usd = (exit_price / entry_price - 1.0) * 100.0
        gross_krw = ((exit_price / entry_price) * (exit_fx / entry_fx) - 1.0) * 100.0
        net_krw = gross_krw - float(cost_pct)
        execution_values: tuple[Any, ...] = (None, None, None, None, None)
        if int(execution_eligible or 0) == 1:
            budget = float(execution_budget or 0.0)
            execution_entry_price = entry_price * (1.0 + float(entry_slippage_pct) / 100.0)
            qty = int(budget // (execution_entry_price * entry_fx)) if budget > 0 else 0
            if qty <= 0:
                con.execute(
                    """UPDATE signals SET execution_shadow_eligible=0,
                        execution_shadow_reason='entry_open_unaffordable',execution_shadow_qty=0
                       WHERE signal_date=? AND ticker=?""",
                    (signal_date, ticker),
                )
                execution_unaffordable += 1
            else:
                contract_exit_date, contract_exit_price, contract_reason = simulate_exit(
                    future.head(5),
                    entry_price=execution_entry_price,
                    tp_pct=tp_pct,
                    sl_pct=sl_pct,
                    tie_break="sl_first",
                )
                contract_exit_fx = fx_map.get(str(contract_exit_date)) or _latest_fx(
                    fx_map, str(contract_exit_date)
                )
                if contract_exit_fx:
                    contract_net = (
                        (contract_exit_price / execution_entry_price) * (contract_exit_fx / entry_fx) - 1.0
                    ) * 100.0 - float(cost_pct)
                    contract_pnl = qty * execution_entry_price * entry_fx * contract_net / 100.0
                    execution_values = (
                        str(contract_exit_date),
                        float(contract_exit_price),
                        str(contract_reason),
                        float(contract_net),
                        float(contract_pnl),
                    )
                    con.execute(
                        """UPDATE signals SET execution_shadow_qty=?,
                            execution_shadow_entry_fill_usd=?,execution_shadow_entry_price_krw=?
                           WHERE signal_date=? AND ticker=?""",
                        (qty, execution_entry_price, execution_entry_price * entry_fx, signal_date, ticker),
                    )
                    execution_matured += 1
        con.execute(
            """
            UPDATE signals SET entry_date=?,entry_price=?,exit_date=?,exit_price=?,entry_fx=?,exit_fx=?,
                gross_usd_pct=?,gross_krw_pct=?,net_krw_pct=?,status='MATURED',error=NULL,
                execution_shadow_exit_date=?,execution_shadow_exit_price=?,
                execution_shadow_exit_reason=?,execution_shadow_net_krw_pct=?,execution_shadow_pnl_krw=?
            WHERE signal_date=? AND ticker=? AND status='PENDING'
            """,
            (
                str(entry["date"]), entry_price, str(exit_row["date"]), exit_price, entry_fx, exit_fx,
                gross_usd, gross_krw, net_krw,
                *execution_values,
                signal_date, ticker,
            ),
        )
        matured += 1
    con.commit()
    return {
        "matured_now": matured,
        "waiting": waiting,
        "execution_matured_now": execution_matured,
        "execution_unaffordable_now": execution_unaffordable,
    }


def _block_lcb(values: np.ndarray, *, seed: int = 20260710) -> float | None:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 5:
        return None
    rng = np.random.default_rng(seed)
    block = min(5, len(values))
    starts = np.arange(max(1, len(values) - block + 1))
    output = []
    for _ in range(2000):
        sample: list[float] = []
        while len(sample) < len(values):
            start = int(rng.choice(starts))
            sample.extend(values[start:start + block].tolist())
        output.append(float(np.mean(sample[:len(values)])))
    return float(np.quantile(output, 0.05))


def summarize_forward(con: sqlite3.Connection) -> dict[str, Any]:
    frame = pd.read_sql_query(
        """SELECT signal_date,ticker,rank,candidate_source,net_krw_pct,breadth_context_state
           FROM signals WHERE status='MATURED' AND net_krw_pct IS NOT NULL""",
        con,
    )
    if frame.empty:
        return {"sessions": 0, "matured": 0, "critical_data_errors": []}
    daily = frame.groupby("signal_date", sort=True)["net_krw_pct"].mean()
    values = daily.to_numpy(dtype=float)
    positive = float(values[values > 0].sum())
    negative = float(-values[values < 0].sum())
    ordered = np.sort(values)[::-1]
    breadth_diagnostic: dict[str, Any] = {}
    for state, group in frame.groupby(frame["breadth_context_state"].fillna("MISSING")):
        state_daily = group.groupby("signal_date")["net_krw_pct"].mean().to_numpy(dtype=float)
        state_positive = float(state_daily[state_daily > 0].sum())
        state_negative = float(-state_daily[state_daily < 0].sum())
        breadth_diagnostic[str(state)] = {
            "sessions": int(len(state_daily)),
            "signals": int(len(group)),
            "mean_net_pct": float(state_daily.mean()),
            "profit_factor": float(state_positive / state_negative) if state_negative > 0 else None,
        }
    # 2026-08-04: 이 집계는 rank1~5 전체 + 소스 화이트리스트 도입 이전 행을 모두 담는다.
    # 현재 계약(day_losers·rank1)의 forward가 아니므로 소스·rank별로 쪼개 함께 남긴다.
    # 그대로 두면 -6.33%가 현재 레인의 성적처럼 읽힌다.
    source_diagnostic: dict[str, Any] = {}
    for source, group in frame.groupby(frame["candidate_source"].fillna("unknown").replace("", "unknown")):
        source_values = group["net_krw_pct"].to_numpy(dtype=float)
        source_positive = float(source_values[source_values > 0].sum())
        source_negative = float(-source_values[source_values < 0].sum())
        rank1 = group[group["rank"] == 1]["net_krw_pct"].to_numpy(dtype=float)
        source_diagnostic[str(source)] = {
            "signals": int(len(group)),
            "mean_net_pct": float(source_values.mean()),
            "win_rate": float((source_values > 0).mean()),
            "profit_factor": float(source_positive / source_negative) if source_negative > 0 else None,
            "rank1_signals": int(len(rank1)),
            "rank1_mean_net_pct": float(rank1.mean()) if len(rank1) else None,
        }
    return {
        "sessions": int(len(daily)),
        "matured": int(len(frame)),
        "mean_net_pct": float(values.mean()),
        "win_rate": float((values > 0).mean()),
        "profit_factor": float(positive / negative) if negative > 0 else None,
        "block_lcb_pct": _block_lcb(values),
        "ex_top3_days_pct": float(ordered[3:].mean()) if len(ordered) > 3 else None,
        "scope_note": "rank1~5 + 소스필터 도입 이전 행 혼합. 현재 계약 forward 아님.",
        "critical_data_errors": [],
        "breadth_context_diagnostic_only": breadth_diagnostic,
        "candidate_source_diagnostic_only": source_diagnostic,
    }


def summarize_executable_forward(con: sqlite3.Connection) -> dict[str, Any]:
    return summarize_forward_evidence(con)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and mature US 5-session swing shadow signals")
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--price-dir", default=str(ROOT / "data" / "price" / "us"))
    parser.add_argument("--research-db", default=str(ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"))
    parser.add_argument("--shadow-db", default=str(ROOT / "data" / "analysis" / "us_swing_shadow.db"))
    parser.add_argument("--policy", default=str(ROOT / "config" / "us_swing_accelerated.json"))
    parser.add_argument("--historical-evidence", default=str(ROOT / "state" / "us_swing_historical_evidence.json"))
    parser.add_argument("--execution-evidence", default=str(ROOT / "state" / "us_swing_execution_evidence.json"))
    parser.add_argument("--status-output", default=str(ROOT / "state" / "us_swing_status.json"))
    parser.add_argument("--veto-file", default="")
    parser.add_argument("--breadth", default=str(ROOT / "data" / "analysis" / "us_breadth_proxy_daily.csv"))
    parser.add_argument("--adv-breadth", default=str(ROOT / "data" / "analysis" / "us_adv_dec_breadth_daily.csv"))
    parser.add_argument("--authority-mode", default=os.getenv("US_SWING_AUTHORITY_MODE", "shadow"))
    parser.add_argument("--mature-only", action="store_true")
    args = parser.parse_args()
    policy = load_swing_policy(args.policy)
    shadow_path = Path(args.shadow_db)
    shadow_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(shadow_path)
    ensure_schema(con)
    ensure_handoff_schema(con)
    price_dir = Path(args.price_dir)
    earliest_pending = con.execute("SELECT MIN(signal_date) FROM signals WHERE status='PENDING'").fetchone()[0]
    fx_start = str(earliest_pending or (datetime.now(timezone.utc) - timedelta(days=45)).date())
    fx_end = str((datetime.now(timezone.utc) + timedelta(days=2)).date())
    fx_map = refresh_fx_map(
        _fx_map_from_research_db(Path(args.research_db)), start=fx_start, end=fx_end
    )
    maturity = mature_pending(
        con,
        price_dir=price_dir,
        fx_map=fx_map,
        cost_pct=float(policy.get("cost_pct", 0.50)),
        entry_slippage_pct=float(
            (policy.get("execution_contract") or {}).get("max_entry_slippage_pct", 0.5)
        ),
        tp_pct=float((policy.get("execution_contract") or {}).get("take_profit_pct", 0.12)),
        sl_pct=float((policy.get("execution_contract") or {}).get("catastrophe_stop_pct", 0.25)),
    )
    generated = 0
    candidates_n = 0
    model_version = ""
    feature_errors: list[str] = []
    selected: list[dict[str, Any]] = []
    applied_vetoes: list[dict[str, str]] = []
    breadth_context: dict[str, Any] = {}
    execution_shadow: dict[str, Any] = {}
    active_execution_shadow: dict[str, Any] = {}
    news_arms: dict[str, Any] = {"arms": {}, "rank1_changed": False}
    if not args.mature_only:
        snapshot = Path(args.snapshot) if args.snapshot else ROOT / "state" / f"preopen_US_{args.session_date.replace('-', '')}.json"
        veto_path = Path(args.veto_file) if args.veto_file else ROOT / "state" / f"us_swing_veto_{args.session_date.replace('-', '')}.json"
        veto_payload = _load_json(veto_path)
        raw_vetoes = veto_payload.get("vetoes") if isinstance(veto_payload.get("vetoes"), dict) else {}
        candidates, feature_errors = load_candidate_features(
            snapshot_path=snapshot, price_dir=price_dir, session_date=args.session_date, vetoes=raw_vetoes
        )
        candidates_n = len(candidates)
        vetoed = candidates[candidates["veto_reason"].astype(str).ne("")].copy()
        applied_vetoes = [
            {"ticker": str(row["ticker"]), "reason": str(row["veto_reason"])}
            for row in vetoed.to_dict("records")
        ]
        candidates = candidates[candidates["veto_reason"].astype(str).eq("")].copy()
        _record_pool_size(args.session_date, pool_n=candidates_n, scored_n=len(candidates))
        _record_market_width(args.session_date)
        _record_tp_capture(args.session_date, candidates)
        _record_sector_context(args.session_date, candidates)
        _record_premarket(args.session_date, candidates)
        _record_candidate_age(args.session_date, candidates)
        research_con = sqlite3.connect(args.research_db)
        try:
            train = load_yahoo_dataset(
                research_con, horizon=5, cost_pct=float(policy.get("cost_pct", 0.50)),
                # as-of 계약(2026-08-07): 봉인 교재에선 no-op, 교재 최신화 시 lookahead 차단.
                as_of=args.session_date,
            )
        finally:
            research_con.close()
        # 2026-08-04: 랭킹 관측 확장은 정책 파일이 아니라 env로 한다 — 정책 top_k를 바꾸면
        # historical evidence의 policy hash 계약이 깨져 authority가 차단된다(실측: 08-04
        # top_k 10 변경이 historical_policy_hash_mismatch 블로커를 만들어 e2e가 잡아냄).
        # 저장 개수만 늘리는 것은 주문(rank cap=일1건)·authority(rank1 전용 evidence)와 무관.
        store_top_k = int(os.getenv("US_SWING_STORE_TOP_K", "0") or 0)
        scored, model_version = score_candidates(
            train,
            candidates,
            seeds=[int(value) for value in policy.get("seeds", [20260710])],
            top_k=max(int(policy.get("top_k", 5)), store_top_k),
        )
        # 2026-08-01 하드필터 5조건 shadow 관측(주문·선정 무영향, 기록만).
        # 근거: 검증기간 5조건 통과군 +1.152/건(n=980) vs 잔여 +0.16 (discriminator_hunt).
        # cum3d는 runner 피처에 없어 momentum_5d_pct로 근사(proxy 표기). 실패해도 본 흐름 무영향.
        try:
            _hf_rows = []
            for _r in candidates.to_dict("records"):
                _chg = float(_r.get("change_pct") or 0.0)
                _gap = float(_r.get("gap_pct") or 0.0)
                _flags = {
                    "drop_ge_4_66": bool(-_chg >= 4.66),
                    "intraday_ge_3_69": bool(-(_chg - _gap) >= 3.69),
                    "mom20_le_m5_72": bool(float(_r.get("momentum_20d_pct") or 0.0) <= -5.72),
                    "from_high20_le_m15_8": bool(float(_r.get("from_high_20d_pct") or 0.0) <= -15.80),
                    "cum3d_proxy_mom5_le_m5_18": bool(float(_r.get("momentum_5d_pct") or 0.0) <= -5.18),
                }
                _hf_rows.append({
                    "session_date": str(args.session_date),
                    "ticker": str(_r.get("ticker") or ""),
                    "candidate_source": str(_r.get("candidate_source") or ""),
                    "pass_count": int(sum(_flags.values())),
                    "pass_all": bool(all(_flags.values())),
                    **_flags,
                })
            _hf_path = ROOT / "data" / "shadow" / "us_hard_filter_shadow.jsonl"
            with open(_hf_path, "a", encoding="utf-8") as _hf:
                for _row in _hf_rows:
                    _hf.write(json.dumps(_row, ensure_ascii=False) + "\n")
        except Exception:
            pass
        feature_date = str(scored["date"].iloc[0]) if not scored.empty else ""
        breadth_context = load_breadth_context(
            feature_date=feature_date,
            breadth_path=Path(args.breadth),
            adv_path=Path(args.adv_breadth),
        )
        for key, value in breadth_context.items():
            scored[key] = value
        generated = write_signals(
            con, signal_date=args.session_date, scored=scored, model_version=model_version
        )
        # 2026-08-13 이중 방어: 기록 직후 전 랭크 기준종가 독립 대조(오염 마킹만, 개입 없음)
        verify_reference_closes(con, signal_date=args.session_date)
        # 2026-08-15 넓은 그물 A/B 관측 (운영자 승인): 컷 밖 적격 급락주까지 같은
        # 모델로 채점해 wide rank1 vs 현 rank1을 짝지어 가상 정산 — 주문·선정 무접촉.
        _r1 = scored[scored["rank"] == 1]
        _pool_rank1 = None
        if not _r1.empty:
            _row = _r1.iloc[0].to_dict()
            _pool_rank1 = {
                "ticker": str(_row.get("ticker") or ""),
                "source": str(_row.get("candidate_source") or ""),
                "probability": float(_row.get("probability") or 0.0),
                "predicted_net_pct": float(_row.get("predicted_net_pct") or 0.0),
                "ref_close": float(_row.get("close") or 0.0),
            }
        _record_wide_net_shadow(
            args.session_date,
            pool=candidates,
            pool_rank1=_pool_rank1,
            train=train,
            seeds=[int(value) for value in policy.get("seeds", [20260710])],
            price_dir=price_dir,
            cost_pct=float(policy.get("cost_pct", 0.50)),
            tp_pct=float((policy.get("execution_contract") or {}).get("take_profit_pct", 0.12)),
            sl_pct=float((policy.get("execution_contract") or {}).get("catastrophe_stop_pct", 0.25)),
        )
        # 정보성 하락 배제 3-arm 기록(관찰 전용, 선정에 개입하지 않는다).
        news_arms = news_arm_counterfactual(scored.attrs.get("full_ranking", scored))
        try:
            _arm_path = ROOT / "data" / "shadow" / "us_swing_news_arm_shadow.jsonl"
            _arm_path.parent.mkdir(parents=True, exist_ok=True)
            with open(_arm_path, "a", encoding="utf-8") as _arm_file:
                _arm_file.write(
                    json.dumps(
                        {
                            "session_date": str(args.session_date),
                            "model_version": model_version,
                            **news_arms,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError as exc:
            log_note = f"news arm shadow write failed: {exc}"
            print(log_note, file=sys.stderr)
        execution_shadow = annotate_execution_shadow(
            con,
            signal_date=args.session_date,
            fx_map=fx_map,
            policy=policy,
            base_order_budget_krw=500_000.0,
        )
        selected = [
            {
                "ticker": str(row["ticker"]), "rank": int(row["rank"]),
                "predicted_net_pct": float(row["predicted_net_pct"]),
                "probability": float(row["probability"]), "feature_date": str(row["date"]),
                "breadth_context_state": str(row.get("breadth_context_state") or "MISSING"),
            }
            for row in scored.to_dict("records")
        ]
    active_execution_shadow = summarize_active_execution_shadow(
        con,
        price_dir=price_dir,
        policy=policy,
    )
    model_forward = summarize_forward(con)
    forward = summarize_executable_forward(con)
    historical = _load_json(Path(args.historical_evidence))
    execution_evidence = _load_json(Path(args.execution_evidence))
    authority = evaluate_swing_authority(
        configured_mode=args.authority_mode,
        historical_evidence=historical,
        forward_evidence=forward,
        policy=policy,
        execution_evidence=execution_evidence,
    )
    # 2026-08-04: 여기 저장되는 authority는 운영자 오버라이드 **적용 전** 값이라
    # status 파일만 읽으면 실매수 중인 시스템이 allowed_to_emit_orders=false로 보인다.
    # (실제 08-03 FRMI 38@5.5225가 micro_probe 경로로 체결됐다.)
    # 검토자가 파일 하나로 실행 권한을 판정할 수 있게 오버라이드 후 상태를 함께 남긴다.
    _base_authority = authority.to_dict()
    _override_active = operator_override_active(
        ack=os.getenv("US_SWING_OPERATOR_MICRO_OVERRIDE_ACK", ""),
        blockers=_base_authority.get("blockers"),
    )
    _contract = resolve_shadow_contract(
        policy, base_order_budget_krw=500_000.0, authority=_base_authority
    )
    effective_authority = {
        **_base_authority,
        "operator_override_active": bool(_override_active),
        "operator_override_source": "US_SWING_OPERATOR_MICRO_OVERRIDE_ACK",
    }
    if _override_active:
        effective_authority.update(
            {
                "eligible_mode": "micro_operator_trial",
                "effective_mode": "micro",
                "allowed_to_emit_orders": True,
                "size_multiplier": 0.10,
                "max_new_per_day": int(_contract["max_new_per_day"]),
                "max_open_slots": int(_contract["max_open_slots"]),
                "absolute_order_cap_krw": float(_contract["budget_krw"]),
                "order_cap_source": "operator_config_absolute",
                "operator_forward_override": True,
                "operator_forward_override_blockers": list(_base_authority.get("blockers") or []),
            }
        )
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signal_date": args.session_date,
        "model_version": model_version,
        "eligible_candidates": candidates_n,
        "new_signals": generated,
        "selected": selected,
        "applied_vetoes": applied_vetoes,
        "breadth_context": breadth_context,
        "feature_errors": feature_errors,
        "maturity": maturity,
        "execution_shadow": execution_shadow,
        "active_execution_shadow": active_execution_shadow,
        "forward_evidence": forward,
        "model_forward_diagnostic_only": model_forward,
        "historical_evidence_present": bool(historical),
        "execution_evidence_present": bool(execution_evidence),
        "authority": _base_authority,
        "effective_authority": effective_authority,
        "execution_contract": _contract,
        "news_arm_shadow_only": news_arms,
        "order_integration": "WIRED_FAIL_CLOSED_DISABLED; separate handoff, submit, and live-ack locks",
    }
    con.execute(
        "INSERT OR REPLACE INTO runs(signal_date,created_at,report_json) VALUES (?,?,?)",
        (args.session_date, report["generated_at"], json.dumps(report, ensure_ascii=False, sort_keys=True)),
    )
    con.commit()
    con.close()
    output = Path(args.status_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(output)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
