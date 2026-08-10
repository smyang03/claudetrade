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
        for q in quotes:
            chg = q.get("regularMarketChangePercent")
            px = q.get("regularMarketPrice")
            vol = q.get("regularMarketVolume") or 0
            if chg is None or px is None or chg > -5:
                continue
            n5 += 1
            if px >= 5 and px * vol >= 15e6:
                n5_eligible += 1
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "session_date": str(session_date), "screener_rows": len(quotes),
                "losers_le_minus5": n5, "eligible_le_minus5": n5_eligible,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
