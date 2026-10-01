# -*- coding: utf-8 -*-
"""T1 US 실적 서프라이즈 쉐도우 — 수동 실행 도구 (2026-09-28, 운영자 지시 "진행해").

근거: docs/reports/model_T1_robust_20260927.md(확정 후보) · model_T1_impl_20260927.md(I1 규칙) ·
us_slippage_by_adv_20260928.md(시가 동시호가 체결 전제) · T1_improve4_20260928.md(현행 유지).
**실주문 없음** — 이 파일에는 브로커 호출이 없다. 가상 원장만 쓴다. 예약 등록도 하지 않는다(수동 실행).

명령 (연구 전용 가상환경 C:/Users/Unknown/research_venv/Scripts/python 으로 실행 — lightgbm 필요)
  train   2014~2025 진입 이벤트로 M2(LightGBM 회귀, 연구와 같은 피처·파라미터)를 학습해 저장.
          연구 모듈(tools/research/research_model_T1_earnings_20260927.py)의 load/build를 그대로 쓴다.
  score   미국 개장 전 실행. 오늘 BMO + 직전 세션 AMC 발표 종목의 실적을 새로 받아 채점하고,
          점수 ≥ 연도 임계(I1)면 가상 진입을 기록한다. 같은 날 재실행은 멱등. --dry 는 기록 없이 출력만.
  settle  진입 후 20세션·30세션이 지난 가상 포지션을 시가→시가로 각각 정산(수수료 0.44%, IWM 대비).

규칙(연구 사전등록과 동일): 진입 = 발표 후 첫 세션 시가(LOO 가정), 청산 = 진입 +20세션 시가(MOO 가정),
+30세션 청산을 비교용으로 병행 기록. 필터 close[a] ≥ $3 · 20일 평균 거래대금 ≥ $1M, 장중 발표(DMH) 제외.
임계 2026 = 0.02000(T1-impl), 거래대금 ≥ $5M 태그 기록(T1-plus: 유동 종목만으로도 동일 성과).
missed_feed: 개장 전에 실적치를 못 받은 예정 종목 — 운영 전제(개장 전 수집) 검증용으로 기록한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EVT = ROOT / "data" / "analysis" / "us_earnings_dates.jsonl"
LEDGER = ROOT / "data" / "shadow" / "us_earnings_t1_shadow.jsonl"
MODEL_DIR = ROOT / "state" / "models"
MODEL = MODEL_DIR / "t1_earnings_m2.txt"
META = MODEL_DIR / "t1_earnings_m2.json"
FEATS = ["sue1", "sue2", "sgn", "beats4", "nprev4", "dsue", "days_since", "ret20", "ret60", "vol20",
         "ldvol20", "lsize", "month_sin", "month_cos"]
THRESH = {2026: 0.02000, 2027: 0.01952}   # 전년까지 OOS 점수 90분위(2027은 1월 재계산)
HOLDS = (20, 30)
COST = 0.0044


def _today_et() -> pd.Timestamp:
    import zoneinfo
    return pd.Timestamp(datetime.now(zoneinfo.ZoneInfo("America/New_York")).date())


def _append(rows: list[dict]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]


# ------------------------------------------------------------------ train
def cmd_train() -> int:
    sys.path.insert(0, str(ROOT / "tools" / "research"))
    import lightgbm as lgb
    import research_model_T1_earnings_20260927 as rm
    ev, _ = rm.load_events()
    cal, ti, W = rm.load_prices()
    tab, _ = rm.build(ev, cal, ti, W)
    first2026 = int(np.searchsorted(cal.values, np.datetime64("2026-01-01")))
    tr = tab[(tab.year >= 2014) & (tab.year <= 2025) & (tab["x20"] < first2026)]
    y = tr["gA20"].values
    lo, hi = np.nanpercentile(y, [1, 99])
    m = lgb.LGBMRegressor(n_jobs=2, random_state=0, verbose=-1)
    m.fit(tr[FEATS].values.astype(np.float32), np.clip(y, lo, hi))
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    m.booster_.save_model(str(MODEL))
    META.write_text(json.dumps({"trained_at": datetime.now(timezone.utc).isoformat(), "n_train": int(len(tr)),
                                "train_years": [2014, 2025], "feats": FEATS,
                                "source": "research_model_T1_earnings_20260927 M2 A_open H20"},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[T1] 학습 완료 n={len(tr)} → {MODEL}")
    return 0


# ------------------------------------------------------------------ helpers
def _history(tk: str) -> pd.DataFrame:
    rows = []
    with EVT.open(encoding="utf-8") as fh:
        for line in fh:
            if f'"{tk}"' not in line:
                continue
            r = json.loads(line)
            if r.get("ticker") == tk and r.get("confirmed") and r.get("eps_act") is not None \
                    and r.get("eps_est") is not None:
                rows.append(r)
    if not rows:
        return pd.DataFrame(columns=["date", "eps_act", "eps_est"])
    d = pd.DataFrame(rows).sort_values("fetched_at").drop_duplicates("date", keep="last")
    d["date"] = pd.to_datetime(d.date)
    return d.sort_values("date")


def _fresh_eps(tk: str, day: pd.Timestamp):
    import yfinance as yf
    try:
        ed = yf.Ticker(tk.replace(".", "-")).get_earnings_dates(limit=8)
    except Exception as exc:  # noqa: BLE001
        return None, f"fetch_error:{str(exc)[:60]}"
    if ed is None or len(ed) == 0:
        return None, "empty"
    for ts, row in ed.iterrows():
        t = ts.tz_convert("America/New_York") if ts.tzinfo else ts
        if t.date() != day.date():
            continue
        hour = "BMO" if (t.hour, t.minute) < (9, 30) else ("AMC" if (t.hour, t.minute) >= (16, 0) else "DMH")
        act, est = row.get("Reported EPS"), row.get("EPS Estimate")
        if pd.isna(act) or pd.isna(est):
            return {"hour": hour}, "no_actual"
        return {"eps_act": float(act), "eps_est": float(est), "hour": hour, "ts": t.isoformat()}, "ok"
    return None, "date_not_found"


def _bars(tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    out: dict[str, pd.DataFrame] = {}
    if not tickers:
        return out
    df = yf.download([t.replace(".", "-") for t in tickers], start=start, auto_adjust=True, group_by="ticker",
                     threads=4, progress=False)
    for t in tickers:
        try:
            sub = df[t.replace(".", "-")] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        sub = sub.dropna(subset=["Close"])
        if len(sub):
            sub.index = pd.DatetimeIndex(sub.index.date)
            out[t] = sub
    return out


def _features(ev: dict, hist: pd.DataFrame, px: pd.DataFrame, entry: pd.Timestamp) -> dict | None:
    """연구 build()와 같은 정의. a = 진입 직전 세션(close[a]까지만 사용)."""
    p = px[px.index < entry]
    if len(p) < 61:
        return None
    C, V = p.Close.values.astype(float), p.Volume.values.astype(float)
    ca = C[-1]
    dv = float(np.mean(C[-20:] * V[-20:]))
    surp = ev["eps_act"] - ev["eps_est"]
    sue1 = float(np.clip(surp / max(abs(ev["eps_est"]), 0.05), -5, 5))
    evd = pd.Timestamp(ev["date"])
    past = hist[hist.date < evd].tail(4)
    psue = np.clip((past.eps_act - past.eps_est) / np.maximum(past.eps_est.abs(), 0.05), -5, 5)
    return {"sue1": sue1, "sue2": float(np.clip(surp / ca, -0.2, 0.2)), "sgn": float(np.sign(surp)),
            "beats4": float(((past.eps_act - past.eps_est) > 0).sum()) if len(past) else np.nan,
            "nprev4": float(len(past)),
            "dsue": float(sue1 - psue.iloc[-1]) if len(past) else np.nan,
            "days_since": float((evd - past.date.iloc[-1]).days) if len(past) else np.nan,
            "ret20": float(ca / C[-21] - 1), "ret60": float(ca / C[-61] - 1),
            "vol20": float(np.std(np.diff(np.log(C[-21:])))),
            "ldvol20": float(np.log(max(dv, 1))),
            "lsize": float(np.log(max(ca * float(np.mean(V[-60:])), 1))),
            "month_sin": float(np.sin(2 * np.pi * entry.month / 12)),
            "month_cos": float(np.cos(2 * np.pi * entry.month / 12)),
            "_close_a": float(ca), "_dvol20": dv}


def _candidates(today: pd.Timestamp, prev: pd.Timestamp, path: Path | None = None) -> dict[tuple, dict]:
    """오늘/전일 예정 행 — 티커별 **최신 수집분(fetched_at 최대)** 에 들어 있는 행만 쓴다(09-30 수리 B).
    예정일이 바뀌면 옛 날짜 행이 원장에 남아 후보를 오염시켰다(9월 이후 67행, date_not_found의 주원인)."""
    latest: dict[str, str] = {}
    rows: list[dict] = []
    with (path or EVT).open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            tk, fa = r.get("ticker"), str(r.get("fetched_at") or "")
            if tk and fa > latest.get(tk, ""):
                latest[tk] = fa
            d = pd.Timestamp(r["date"])
            if d == today or d == prev:
                rows.append(r)
    return {(r["ticker"], r["date"]): r for r in rows if str(r.get("fetched_at") or "") == latest.get(r["ticker"], "")}


def _finnhub_calendar(prev: pd.Timestamp, today: pd.Timestamp) -> dict[tuple, dict]:
    """Finnhub 실적 캘린더 {(ticker, date): row}. 키 없음·오류면 빈 dict(기존 yfinance 경로 그대로)."""
    import urllib.request
    try:
        from dotenv import dotenv_values
        key = (dotenv_values(str(ROOT / ".env.live")).get("FINNHUB_API_KEY") or "").strip()
    except Exception:  # noqa: BLE001
        key = ""
    if not key:
        return {}
    url = (f"https://finnhub.io/api/v1/calendar/earnings?from={prev.date()}&to={today.date()}&token={key}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
            cal = json.loads(r.read().decode()).get("earningsCalendar", [])
    except Exception as exc:  # noqa: BLE001
        print(f"[T1] Finnhub 캘린더 실패: {type(exc).__name__}")   # 메시지에 URL(키)이 섞일 수 있어 형식만 출력
        return {}
    return {(str(x.get("symbol") or "").upper(), str(x.get("date") or "")): x for x in cal}


def _finnhub_actual(tk: str, ds: str, evt_row: dict, fh_cal: dict, prior_status: str) -> tuple[dict | None, str]:
    """BMO 실적치 대체(09-30 수리 A): yfinance는 BMO 실적을 개장 뒤(09:37~47 ET)에야 붙인다.
    실적치는 Finnhub, 추정치는 기존과 같은 yfinance 예정 행(eps_est) — 모델 학습 기준 유지(선택 91% 일치 실측).
    추정치 부호가 두 소스에서 엇갈리면 기준 불일치로 보고 채점하지 않는다."""
    x = fh_cal.get((tk.upper(), ds))
    if not x or x.get("epsActual") is None:
        return None, f"{prior_status}|fh_no_actual"
    est = evt_row.get("eps_est")
    if est is None:
        return None, f"{prior_status}|no_yf_est"
    fe = x.get("epsEstimate")
    if fe is not None and est != 0 and fe != 0 and (est > 0) != (fe > 0):
        return None, f"{prior_status}|feed_conflict"
    return {"eps_act": float(x["epsActual"]), "eps_est": float(est), "hour": "BMO", "act_source": "finnhub_cal",
            "feed_seen_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, "ok"


# ------------------------------------------------------------------ score
def cmd_score(dry: bool = False) -> int:
    import lightgbm as lgb
    if not MODEL.exists():
        print("[T1] 모델 없음 — 먼저 train"); return 2
    today = _today_et()
    if today.weekday() >= 5:
        print("[T1] 주말 — 건너뜀"); return 0
    if not dry and any(r.get("kind") == "run" and r.get("session") == str(today.date()) for r in _ledger()):
        print("[T1] 오늘 이미 채점 — 멱등 종료"); return 0
    iwm = _bars(["IWM"], (today - timedelta(days=15)).strftime("%Y-%m-%d")).get("IWM")
    prev = iwm.index[iwm.index < today][-1] if iwm is not None and len(iwm) else today - timedelta(days=1)
    prev = pd.Timestamp(prev)
    cands = _candidates(today, prev)
    fh_cal = _finnhub_calendar(prev, today)
    booster = lgb.Booster(model_file=str(MODEL))
    thr = THRESH.get(today.year)
    fresh, rows, missed = [], [], []
    for (tk, ds), r in sorted(cands.items()):
        day = pd.Timestamp(ds)
        info, status = _fresh_eps(tk, day)
        hour = (info or {}).get("hour") or r.get("hour")
        if hour == "DMH" or (day == today and hour != "BMO") or (day == prev and hour == "BMO"):
            continue                           # 오늘 시가 진입 대상이 아님
        if status != "ok" and hour == "BMO":
            info, status = _finnhub_actual(tk, ds, r, fh_cal, status)
        if status != "ok":
            missed.append({"ticker": tk, "date": ds, "hour": hour, "status": status}); continue
        fresh.append({"ticker": tk, "date": ds, "act_source": "yfinance", **info})
    bars = _bars([e["ticker"] for e in fresh], (today - timedelta(days=130)).strftime("%Y-%m-%d"))
    for e in fresh:
        px = bars.get(e["ticker"])
        f = _features(e, _history(e["ticker"]), px, today) if px is not None else None
        if f is None:
            missed.append({"ticker": e["ticker"], "date": e["date"], "status": "no_price_history"}); continue
        tradable = f["_close_a"] >= 3 and f["_dvol20"] >= 1e6
        score = float(booster.predict(np.array([[f[k] for k in FEATS]], dtype=np.float32))[0])
        sel = bool(tradable and thr is not None and score >= thr)
        rows.append({"kind": "signal", "session": str(today.date()), "ticker": e["ticker"], "event_date": e["date"],
                     "hour": e["hour"], "eps_act": e["eps_act"], "eps_est": e["eps_est"], "score": score,
                     "threshold": thr, "tradable": tradable, "liquid_5m": f["_dvol20"] >= 5e6,
                     "selected_I1": sel, "status": "OPEN" if sel else "NOT_SELECTED", "act_source": e["act_source"],
                     "features": {k: f[k] for k in FEATS}, "close_a": f["_close_a"], "dvol20": f["_dvol20"],
                     "entry_rule": "LOO@open", "exit_rule": "MOO@open+20 (+30 병행)"})
    sel_rows = [r for r in rows if r["selected_I1"]]
    if sel_rows:
        top = max(sel_rows, key=lambda r: r["score"])
        for r in sel_rows:
            r["selected_I2"] = r is top
    run = {"kind": "run", "session": str(today.date()), "prev_session": str(prev.date()),
           "ran_at": datetime.now(timezone.utc).isoformat(), "candidates": len(cands), "scored": len(rows),
           "selected_I1": len(sel_rows), "missed_feed": missed, "threshold": thr}
    print(json.dumps({k: v for k, v in run.items() if k != "missed_feed"}, ensure_ascii=False), f"missed={len(missed)}")
    for r in sel_rows:
        print(f"  선택 {r['ticker']} score={r['score']:.4f} sue1={r['features']['sue1']:+.2f} liquid5m={r['liquid_5m']}")
    if not dry:
        _append(rows + [run])
    return 0


# ------------------------------------------------------------------ settle
def cmd_settle() -> int:
    rows = _ledger()
    done = {(r["ticker"], r["session"], r.get("hold")) for r in rows if r.get("kind") == "settle"}
    todo = [(r, h) for r in rows if r.get("kind") == "signal" and r.get("status") == "OPEN"
            for h in HOLDS if (r["ticker"], r["session"], h) not in done]
    if not todo:
        print("[T1] 정산 대상 없음"); return 0
    start = min(r["session"] for r, _ in todo)
    bars = _bars(sorted({r["ticker"] for r, _ in todo} | {"IWM"}), start)
    iwm = bars.get("IWM")
    out = []
    for r, h in todo:
        px = bars.get(r["ticker"])
        if px is None or iwm is None:
            continue
        d0 = pd.Timestamp(r["session"])
        idx = iwm.index[iwm.index >= d0]
        if len(idx) <= h:
            continue                           # 아직 만기 전
        d1 = idx[h]
        if d0 not in px.index or d1 not in px.index:
            out.append({"kind": "settle", "ticker": r["ticker"], "session": r["session"], "hold": h,
                        "status": "PRICE_MISSING"})
            continue
        g = px.loc[d1, "Open"] / px.loc[d0, "Open"] - 1
        b = iwm.loc[d1, "Open"] / iwm.loc[d0, "Open"] - 1
        out.append({"kind": "settle", "ticker": r["ticker"], "session": r["session"], "hold": h,
                    "exit_session": str(d1.date()), "gross": float(g), "iwm": float(b),
                    "net_excess": float(g - b - COST), "selected_I2": bool(r.get("selected_I2")),
                    "liquid_5m": bool(r.get("liquid_5m")), "settled_at": datetime.now(timezone.utc).isoformat()})
    _append(out)
    print(f"[T1] 정산 {len(out)}건")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["train", "score", "settle"])
    ap.add_argument("--dry", action="store_true", help="score: 원장 기록 없이 출력만")
    a = ap.parse_args()
    if a.cmd == "train":
        return cmd_train()
    if a.cmd == "settle":
        return cmd_settle()
    return cmd_score(a.dry)


if __name__ == "__main__":
    raise SystemExit(main())
