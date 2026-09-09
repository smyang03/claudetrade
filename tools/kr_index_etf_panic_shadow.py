# -*- coding: utf-8 -*-
"""KOSDAQ 급락일 지수 ETF 종가 매수 → 다음날 시가 매도 쉐도우 원장 (2026-09-09, 수익성 검토 §2-2 후보).

구조: 코스닥이 크게 빠지는 날, 마감 동시호가에 레버리지 리밸런싱·손절·강제청산이 시각에 묶여 판다. 그 반대편(종가 매수)에 서고 다음날 시가에 판다.

⚠️ 2026-09-10 규칙 확장(v1 → v2, 정본 docs/reports/system_reexamination_and_strategy_20260910.md §0-3):
 v1 = 종가 등락 ≤−2.5%만. v2 = **시가 갭 ≤−1% 또는 종가 ≤−2.5%**. 근거는 개별주 강제매도 가설 검정에서 나왔다 —
 "혼자 갭다운"은 악재(−1.96%)고 "다같이 갭다운"은 강제 매도(+2.10%)인데, 개별주로 수확하면 용량 벽에 막힌다(K=1 t 0.36).
 같은 사건을 지수 ETF로 옮기니 통했다. 11년 실측(233740): v1 140건 +0.950 t 2.90 연도 9/11 → v2 250건 +0.715 t 3.40 **연도 11/11**, 누적 +133 → +179%p.
 2026(코스닥 −14%/+14% 폭락·반등 해) 제외해도 v2 206건 +0.545 t 3.03 연도 10/10. 기간 격리 2016~19 +0.283 / 2020~22 +0.584 / 2023~25 +0.766 전부 양수.
 강건성: 상위 10건 제외 t 1.97, 비용 0.3%까지 t 2.21, 하루 늦게 진입하면 +0.221(t 1.13)로 반감(시점 특이성). US 이식 실패(SPY/QQQ/IWM 초과 t<1) — 코스닥 전용.
 갭은 09:00에 확정되므로 15:19 관측 시점에 이미 알 수 있다. 판정 아님 — forward 20건 규약은 그대로.
07-10 메모리가 "깊은 스트레스 반등 = 공격 평균회귀"로 파킹했던 가설의 재개. 09-05 KR 종목 오버나이트(상한가 산물, 기각)와 대상이 다르다(지수 ETF).

모드
- backfill : yfinance 일봉(^KQ11 종가 등락 ≤ THR)으로 2015-12~ 신호일마다 ETF 2종 OPEN(진입 = 종가) 기록 → settle. mode=backfill.
- live     : schtask 15:16 → 15:19:00까지 대기 → 네이버 지수 API(m.stock.naver.com, localTradedAt 포함)로 코스닥 등락률·ETF 현재가 기록.
             소스 나이 >60s면 stale(신호로 세지 않음). ≤THR면 ETF 2종 OPEN(entry_1519 = 15:19 현재가). 15:31:30까지 대기 → 종가 등락률·ETF 종가 기록,
             15:19 신호와 종가 신호의 어긋남(missed/false_positive)을 세션 행에 남긴다. 진입 체결 가정 = 종가(마감 동시호가). 휴장·조건 미충족도 세션 행 기록.
- settle   : OPEN 행을 yfinance 일봉으로 정산(다음 거래일 시가, 비용 0.05%). entry_close 없으면 캐시 종가로 채운다. d1(다음 종가)은 정보 열.
- report   : data/analysis/kr_index_etf_panic_report.json (ETF별 백필/forward 초과·t·승률, 세션 어긋남 통계) — 대시보드 카드 입력.
원장: data/shadow/kr_index_etf_panic.jsonl — 행 종류 session / trade, (session_date, ticker) 멱등. 실주문 경로 없음.
"""
from __future__ import annotations

import csv
import json
import statistics as st
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "shadow" / "kr_index_etf_panic.jsonl"
CACHE = ROOT / "data" / "analysis" / "kr_index_etf_prices.csv"
REPORT = ROOT / "data" / "analysis" / "kr_index_etf_panic_report.json"
KST = timezone(timedelta(hours=9))
ETFS = {"233740": "KODEX 코스닥150레버리지", "229200": "KODEX 코스닥150"}
INDEX = "KOSDAQ"
THR = -2.5           # 코스닥 종가 등락률 문턱(%) — 백필 단조 구간(≤−2.5 +0.9 / −2.5~−2 +0.58 / −2~−1.5 +0.13)
GAP_THR = -1.0       # 코스닥 시가 갭 문턱(%) — 09-10 확장. 갭 사다리 −0.5 +0.49 / −1 +0.71 / −1.5 +1.34 / −2 +2.09(t 3.93)
COST = 0.05          # 왕복 비용(%) — ETF 거래세 없음, 수수료 0.015%×2 + 스프레드 근사
STALE_SEC = 150.0    # 네이버 localTradedAt은 분 단위(15:18:00을 15:19:00에 읽으면 60s) + 폴링 70s → 150s 넘으면 stale (09-09 첫 실행에서 60s 문턱이 정상 시세를 stale로 오판)
BACKFILL_START = "2015-12-17"
CONTRACT = "kq_panic_etf_v2:gap1_or_close25/close_buy/next_open_sell"
CONTRACT_V1 = "kq_panic_etf_v1:close_buy/next_open_sell"
UA = {"User-Agent": "Mozilla/5.0"}


# ── 순수 함수 (테스트 대상) ─────────────────────────────────────────────────
def decide(ratio_pct: float | None, stale: bool, market_status: str | None, thr: float = THR,
           gap_pct: float | None = None, gap_thr: float = GAP_THR) -> tuple[bool, str]:
    """15:19 시점 신호(v2). 시가 갭 ≤ gap_thr **또는** 종가 등락 ≤ thr.

    갭은 09:00에 확정된 값이라 stale 대상이 아니지만, 등락률이 결측이면 그날 관측 자체가 실패한 것으로 보고 신호를 내지 않는다."""
    if ratio_pct is None:
        return False, "ratio_missing"
    if market_status and str(market_status).upper() not in ("OPEN", "TRADING", ""):
        return False, f"market_{str(market_status).lower()}"
    if stale:
        return False, "stale_quote"
    gap_hit = gap_pct is not None and gap_pct <= gap_thr
    close_hit = ratio_pct <= thr
    if gap_hit and close_hit:
        return True, "signal_both"
    if gap_hit:
        return True, "signal_gap"
    if close_hit:
        return True, "signal_close"
    return False, "above_thr"


def classify_divergence(sig_1519: bool, ratio_close: float | None, thr: float = THR,
                        gap_pct: float | None = None, gap_thr: float = GAP_THR) -> str:
    """15:19 신호와 종가 확정 신호의 일치 여부. v2는 갭 조건도 확정 신호에 포함(갭은 09:00에 이미 확정)."""
    if ratio_close is None:
        return "close_unknown"
    close_sig = ratio_close <= thr or (gap_pct is not None and gap_pct <= gap_thr)
    if sig_1519 and close_sig:
        return "agree_signal"
    if sig_1519 and not close_sig:
        return "false_positive_1519"
    if (not sig_1519) and close_sig:
        return "missed_by_1519"
    return "agree_none"


def settle_math(entry_close: float, next_open: float, next_close: float | None,
                entry_1519: float | None = None, cost: float = COST) -> dict:
    out = {"exit_price": round(next_open, 4),
           "net_pct": round((next_open / entry_close - 1.0) * 100.0 - cost, 4),
           "d1_pct": round((next_close / entry_close - 1.0) * 100.0 - cost, 4) if next_close else None}
    if entry_1519:
        out["net_1519_pct"] = round((next_open / entry_1519 - 1.0) * 100.0 - cost, 4)
    return out


def quote_age_sec(traded_at: str | None, now: datetime) -> float:
    if not traded_at:
        return float("inf")
    try:
        t = datetime.fromisoformat(traded_at)
    except ValueError:
        return float("inf")
    if t.tzinfo is None:
        t = t.replace(tzinfo=KST)
    return max(0.0, (now - t).total_seconds())


# ── 원장 ────────────────────────────────────────────────────────────────────
def _load() -> list[dict]:
    if not OUT.exists():
        return []
    out = []
    for line in OUT.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _rewrite(rows: list[dict]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(OUT)


def _append(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── 가격 캐시 (yfinance 일봉) ───────────────────────────────────────────────
def _read_cache() -> dict[str, dict]:
    """date → {kq_close, kq_chg, <etf>_open, <etf>_close}."""
    if not CACHE.exists():
        return {}
    out = {}
    with CACHE.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            out[r["date"]] = {k: (float(v) if v not in ("", None) else None) for k, v in r.items() if k != "date"}
    return out


def refresh_cache(force: bool = False) -> dict[str, dict]:
    cache = _read_cache()
    today = datetime.now(KST).date().isoformat()
    last = max(cache) if cache else None
    if cache and not force and last and last >= (datetime.now(KST).date() - timedelta(days=1)).isoformat():
        return cache
    try:
        import yfinance as yf
    except ImportError:
        print("[KQPANIC] yfinance 없음 — 캐시만 사용"); return cache
    start = "2015-10-01" if force or not cache else (datetime.fromisoformat(last) - timedelta(days=7)).date().isoformat()
    cols: dict[str, dict[str, float]] = {}
    def _dl(sym):
        d = yf.download(sym, start=start, auto_adjust=False, progress=False, threads=False)
        if hasattr(d.columns, "levels"):
            d.columns = d.columns.get_level_values(0)
        return d.dropna()
    try:
        kq = _dl("^KQ11")
        for ts, row in kq.iterrows():
            col = cols.setdefault(ts.date().isoformat(), {})
            col["kq_close"] = float(row["Close"]); col["kq_open"] = float(row["Open"])
        for tk in ETFS:
            d = _dl(f"{tk}.KS")
            for ts, row in d.iterrows():
                c = cols.setdefault(ts.date().isoformat(), {})
                c[f"{tk}_open"] = float(row["Open"]); c[f"{tk}_close"] = float(row["Close"])
    except Exception as exc:  # noqa: BLE001
        print(f"[KQPANIC] yfinance 실패 {exc} — 캐시만 사용"); return cache
    for d, c in cols.items():
        if d >= today:
            continue   # 당일 봉은 미완성일 수 있어 다음 실행에 채운다
        cache.setdefault(d, {}).update(c)
    dates = sorted(cache)
    for i, d in enumerate(dates):
        c = cache[d]
        prev = cache[dates[i - 1]].get("kq_close") if i else None
        if prev and c.get("kq_close"):
            c["kq_chg"] = round((c["kq_close"] / prev - 1.0) * 100.0, 4)
        if prev and c.get("kq_open"):
            c["kq_gap"] = round((c["kq_open"] / prev - 1.0) * 100.0, 4)
    keys = ["kq_close", "kq_open", "kq_chg", "kq_gap"] + [f"{tk}_{k}" for tk in ETFS for k in ("open", "close")]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh); w.writerow(["date"] + keys)
        for d in dates:
            w.writerow([d] + [cache[d].get(k, "") for k in keys])
    return cache


# ── backfill ────────────────────────────────────────────────────────────────
def backfill() -> int:
    need_open = "kq_gap" not in next(iter(_read_cache().values()), {})
    cache = refresh_cache(force=need_open or not CACHE.exists())
    rows = _load()
    have = {(r["session_date"], r["ticker"]) for r in rows if r.get("kind") == "trade"}
    # v1 → v2 일회성 재라벨: v1 신호(종가≤−2.5)는 v2 규칙의 진부분집합이라 같은 행이 v2에서도 그대로 생성된다.
    relabeled = 0
    for r in rows:
        if r.get("kind") == "trade" and r.get("mode") == "backfill" and r.get("contract") == CONTRACT_V1:
            r["contract"] = CONTRACT
            r.setdefault("signal_kind", "signal_close")
            relabeled += 1
    if relabeled:
        _rewrite(rows)
        print(f"[KQPANIC] v1 백필 {relabeled}행 v2 재라벨(규칙 확장, 기존 신호는 부분집합)")
    n = 0
    for d in sorted(cache):
        c = cache[d]
        if d < BACKFILL_START or c.get("kq_chg") is None:
            continue
        gap_hit = c.get("kq_gap") is not None and c["kq_gap"] <= GAP_THR
        close_hit = c["kq_chg"] <= THR
        if not (gap_hit or close_hit):
            continue
        kind = "signal_both" if (gap_hit and close_hit) else ("signal_gap" if gap_hit else "signal_close")
        for tk in ETFS:
            if (d, tk) in have or not c.get(f"{tk}_close"):
                continue
            _append({"kind": "trade", "mode": "backfill", "session_date": d, "ticker": tk, "index": INDEX, "kq_chg_close": c["kq_chg"],
                     "kq_gap": c.get("kq_gap"), "signal_kind": kind,
                     "entry_close": c[f"{tk}_close"], "entry_1519": None, "contract": CONTRACT, "status": "OPEN",
                     "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            n += 1
    print(f"[KQPANIC] backfill 신규 {n}행"); return n


# ── live ────────────────────────────────────────────────────────────────────
def _get_json(url: str, timeout: float = 10.0) -> dict | None:
    try:
        req = urllib.request.Request(url, headers=UA)
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception as exc:  # noqa: BLE001
        print(f"[KQPANIC] 조회 실패 {url[-40:]} {exc}"); return None


def _f(x) -> float | None:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch_index_gap(today: str) -> dict:
    """당일 시가 갭(%) — 네이버 지수 일별 price 엔드포인트. 당일 행이 없으면 gap=None(갭 조건만 빠지고 종가 조건은 그대로)."""
    rows = _get_json(f"https://m.stock.naver.com/api/index/{INDEX}/price?pageSize=5&page=1") or []
    if not isinstance(rows, list):
        return {"gap": None, "open": None, "prev_close": None, "gap_src": "bad_payload"}
    i = next((k for k, r in enumerate(rows) if str(r.get("localTradedAt", ""))[:10] == today), None)
    if i is None:
        return {"gap": None, "open": None, "prev_close": None, "gap_src": "no_today_row"}
    o = _f(rows[i].get("openPrice"))
    prev = _f(rows[i + 1].get("closePrice")) if i + 1 < len(rows) else None
    if prev is None:
        c, dl = _f(rows[i].get("closePrice")), _f(rows[i].get("compareToPreviousClosePrice"))
        prev = (c - dl) if (c is not None and dl is not None) else None
    if o is None or not prev:
        return {"gap": None, "open": o, "prev_close": prev, "gap_src": "missing_price"}
    return {"gap": round((o / prev - 1.0) * 100.0, 4), "open": o, "prev_close": prev, "gap_src": "naver_price"}


def fetch_index() -> dict:
    d = _get_json(f"https://m.stock.naver.com/api/index/{INDEX}/basic") or {}
    return {"ratio": _f(d.get("fluctuationsRatio")), "close": _f(d.get("closePrice")), "traded_at": d.get("localTradedAt"),
            "market_status": d.get("marketStatus")}


def fetch_etf_prices() -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for tk in ETFS:
        d = _get_json(f"https://polling.finance.naver.com/api/realtime/domestic/stock/{tk}") or {}
        row = ((d.get("datas") or [{}])[0]) or {}
        try:
            out[tk] = float(str(row.get("closePrice")).replace(",", ""))
        except (TypeError, ValueError):
            out[tk] = None
    return out


def _wait_until(hh: int, mm: int, ss: int = 0) -> None:
    now = datetime.now(KST)
    target = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
    if target > now:
        time.sleep((target - now).total_seconds())


def live() -> None:
    today = datetime.now(KST).date().isoformat()
    rows = _load()
    sess = next((r for r in rows if r.get("kind") == "session" and r.get("session_date") == today), None)
    if sess and sess.get("ratio_close") is not None:
        print(f"[KQPANIC] {today} 세션 행 완료됨 — 스킵"); return
    _wait_until(15, 19, 0)
    now = datetime.now(KST)
    idx = fetch_index(); px = fetch_etf_prices(); gp = fetch_index_gap(today)
    age = quote_age_sec(idx.get("traded_at"), now)
    traded_day = (idx.get("traded_at") or "")[:10]
    holiday = bool(traded_day) and traded_day != today
    stale = age > STALE_SEC or holiday
    status = "CLOSE" if holiday else (idx.get("market_status") or "")
    sig, reason = decide(idx.get("ratio"), stale, status, gap_pct=gp.get("gap"))
    if sess is None:
        sess = {"kind": "session", "mode": "live", "session_date": today, "index": INDEX, "contract": CONTRACT}
        rows.append(sess)
    sess.update({"ratio_1519": idx.get("ratio"), "index_1519": idx.get("close"), "traded_at_1519": idx.get("traded_at"), "age_sec_1519": round(age, 1),
                 "stale_1519": stale, "market_status_1519": idx.get("market_status"), "holiday": holiday, "signal_1519": sig, "reason_1519": reason,
                 "kq_gap": gp.get("gap"), "index_open": gp.get("open"), "gap_src": gp.get("gap_src"),
                 "etf_1519": px, "observed_at": now.isoformat(timespec="seconds")})
    have = {(r["session_date"], r["ticker"]) for r in rows if r.get("kind") == "trade"}
    if sig:
        for tk in ETFS:
            if (today, tk) in have:
                continue
            rows.append({"kind": "trade", "mode": "live", "session_date": today, "ticker": tk, "index": INDEX, "kq_chg_1519": idx.get("ratio"),
                         "kq_gap": gp.get("gap"), "signal_kind": reason,
                         "kq_chg_close": None, "entry_1519": px.get(tk), "entry_close": None, "contract": CONTRACT, "status": "OPEN",
                         "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    _rewrite(rows)
    print(f"[KQPANIC] {today} 15:19 코스닥 등락 {idx.get('ratio')}% 갭 {gp.get('gap')}%({gp.get('gap_src')}) age {age:.0f}s stale={stale} → signal={sig} ({reason})")
    if holiday:
        return
    _wait_until(15, 31, 30)
    idx2 = fetch_index(); px2 = fetch_etf_prices()
    rows = _load()
    for r in rows:
        if r.get("kind") == "session" and r.get("session_date") == today:
            r.update({"ratio_close": idx2.get("ratio"), "index_close": idx2.get("close"), "traded_at_close": idx2.get("traded_at"), "etf_close": px2,
                      "divergence": classify_divergence(bool(r.get("signal_1519")), idx2.get("ratio"), gap_pct=r.get("kq_gap")),
                      "ratio_gap_pp": round(idx2["ratio"] - r["ratio_1519"], 3) if idx2.get("ratio") is not None and r.get("ratio_1519") is not None else None})
        if r.get("kind") == "trade" and r.get("session_date") == today and r.get("mode") == "live":
            r["kq_chg_close"] = idx2.get("ratio"); r["entry_close"] = px2.get(r["ticker"]) or r.get("entry_close")
    _rewrite(rows)
    print(f"[KQPANIC] {today} 종가 코스닥 {idx2.get('ratio')}% ETF {px2}")


# ── settle ──────────────────────────────────────────────────────────────────
def settle() -> int:
    cache = refresh_cache()
    dates = sorted(cache)
    rows = _load(); n = 0
    today = datetime.now(KST).date().isoformat()
    for r in rows:
        if r.get("kind") != "trade" or r.get("status") != "OPEN" or r["session_date"] >= today:
            continue
        sd = r["session_date"]; tk = r["ticker"]
        if sd not in cache:
            continue
        i = dates.index(sd)
        if i + 1 >= len(dates):
            continue
        nxt = dates[i + 1]
        c0, c1 = cache[sd], cache[nxt]
        if not c1.get(f"{tk}_open"):
            continue
        if not r.get("entry_close"):
            if not c0.get(f"{tk}_close"):
                continue
            r["entry_close"] = c0[f"{tk}_close"]; r["entry_close_source"] = "cache_close"
        if r.get("kq_chg_close") is None:
            r["kq_chg_close"] = c0.get("kq_chg")
        r.update(settle_math(r["entry_close"], c1[f"{tk}_open"], c1.get(f"{tk}_close"), r.get("entry_1519")))
        r.update({"exit_date": nxt, "exit_reason": "NEXT_OPEN", "status": "CLOSED", "settled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        n += 1
    if n:
        _rewrite(rows)
    print(f"[KQPANIC] 정산 {n}행"); return n


# ── report ──────────────────────────────────────────────────────────────────
def _uncond_overnight(cache: dict[str, dict], tk: str) -> float | None:
    dates = sorted(cache); xs = []
    for i in range(1, len(dates)):
        a, b = cache[dates[i - 1]], cache[dates[i]]
        if a.get(f"{tk}_close") and b.get(f"{tk}_open"):
            xs.append((b[f"{tk}_open"] / a[f"{tk}_close"] - 1.0) * 100.0)
    return round(st.mean(xs), 4) if xs else None


def report() -> dict:
    cache = _read_cache()
    rows = _load()
    closed = [r for r in rows if r.get("kind") == "trade" and r.get("status") == "CLOSED"]
    out: dict = {"generated_at": datetime.now(KST).isoformat(timespec="seconds"), "thr": THR, "cost": COST, "contract": CONTRACT, "by": {}}
    for tk, name in ETFS.items():
        base = _uncond_overnight(cache, tk)
        sub = [r for r in closed if r["ticker"] == tk]
        d = {"name": name, "uncond_overnight": base, "n": len(sub)}
        for mode in ("backfill", "live"):
            xs = [r["net_pct"] + COST for r in sub if r.get("mode") == mode]   # 초과는 비용 전 오버나이트 − 무조건 평균
            if not xs:
                d[mode] = {"n": 0}; continue
            ex = [x - (base or 0.0) for x in xs]
            t = st.mean(ex) / (st.pstdev(ex) / len(ex) ** 0.5) if len(ex) > 1 and st.pstdev(ex) else None
            d[mode] = {"n": len(xs), "overnight_mean": round(st.mean(xs), 3), "excess_mean": round(st.mean(ex), 3), "t": round(t, 2) if t else None,
                       "win": round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 1), "min": round(min(xs), 2),
                       "net_mean": round(st.mean(r["net_pct"] for r in sub if r.get("mode") == mode), 3),
                       "net_1519_mean": (round(st.mean(r["net_1519_pct"] for r in sub if r.get("mode") == mode and r.get("net_1519_pct") is not None), 3)
                                         if any(r.get("net_1519_pct") is not None for r in sub if r.get("mode") == mode) else None)}
        out["by"][tk] = d
    sess = [r for r in rows if r.get("kind") == "session"]
    div: dict[str, int] = {}
    for r in sess:
        k = r.get("divergence") or ("pending" if r.get("ratio_close") is None else "?"); div[k] = div.get(k, 0) + 1
    gaps = [abs(r["ratio_gap_pp"]) for r in sess if r.get("ratio_gap_pp") is not None]
    out["sessions"] = {"n": len(sess), "stale": sum(1 for r in sess if r.get("stale_1519")), "holiday": sum(1 for r in sess if r.get("holiday")),
                       "signals_1519": sum(1 for r in sess if r.get("signal_1519")), "divergence": div,
                       "abs_gap_pp_mean": round(st.mean(gaps), 3) if gaps else None, "latest": sess[-1] if sess else None}
    out["open_trades"] = sum(1 for r in rows if r.get("kind") == "trade" and r.get("status") == "OPEN")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for tk, d in out["by"].items():
        b = d.get("backfill", {}); l = d.get("live", {})
        print(f"[KQPANIC] {tk} {d['name']}: uncond {d['uncond_overnight']} | backfill n {b.get('n')} excess {b.get('excess_mean')} t {b.get('t')} win {b.get('win')} min {b.get('min')} "
              f"| forward n {l.get('n')} excess {l.get('excess_mean')}")
    print(f"[KQPANIC] sessions {out['sessions']['n']} stale {out['sessions']['stale']} divergence {div}")
    return out


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else "settle"
    if cmd == "backfill":
        backfill(); settle(); report()
    elif cmd == "live":
        live()
    elif cmd == "settle":
        settle(); report()
    elif cmd == "report":
        report()
    else:
        print("사용: kr_index_etf_panic_shadow.py [backfill|live|settle|report]"); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
