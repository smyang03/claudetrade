#!/usr/bin/env python3
"""REQUIRE_TRADE_READY_BLOCK 블록→종가 β보정 누적 리뷰 (read-only, D1/2026-07-02 설계).

질문: RTRB로 취소된 진입(buy_zone_hit 시점)이 블록 시점→종가로 어떻게 가나 —
시장(β) 상승분을 걷어낸 뒤에도 초과수익이 남는가? regime별·세션 클러스터 단위로.

설계 문턱(docs/reports/design_four_axes_verdict_20260702.md §D1, 변경 금지):
- shadow 승격: 비강세 국면 포함 US ≥20세션 AND 세션평균 β-adj 알파 > +0.70%(왕복비용) AND t≥2
- 킬: 다국면 누적 β-adj med < +0.3 / 비강세 구간 med < 0 → 축 폐기·게이트 유지 확정
함정 방지: 세션 클러스터 집계 강제(같은 세션 종목 동조 — 이벤트 풀링 금지),
메가캡 농도 리포트(완화의 실체가 베타 추가매수인지), 블록→고점은 추격가라 미사용.

매매 무변경. lifecycle_events(이미 기록)+분봉(이미 수집)+yfinance(β·지수, 분석용)만 읽는다.
β·지수 캐시: data/analysis/rtrb_review_cache.json (분석 산출물, 커밋 금지 대상).
"""
from __future__ import annotations

import csv
import json
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENT_DB = ROOT / "data" / "v2_event_store.db"
DECISIONS_DB = ROOT / "data" / "ml" / "decisions.db"
MIN_DIR = {"US": ROOT / "data" / "price" / "minute" / "us", "KR": ROOT / "data" / "price" / "minute" / "kr"}
CACHE_PATH = ROOT / "data" / "analysis" / "rtrb_review_cache.json"
BENCH = {"US": "QQQ", "KR": "^KS11"}
KST = timezone(timedelta(hours=9))

STRONG = {"MODERATE_BULL", "MILD_BULL", "AGGRESSIVE"}
WEAK = {"MILD_BEAR", "CAUTIOUS", "CAUTIOUS_BEAR", "DEFENSIVE"}


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"beta": {}, "bench_win": {}}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def load_blocks() -> list[tuple]:
    """(market, session_date, ticker, block_ts_kst) — 세션·종목 유니크(첫 발생)."""
    c = sqlite3.connect(f"file:{EVENT_DB}?mode=ro", uri=True, timeout=10)
    c.execute("PRAGMA busy_timeout=5000")
    q = """SELECT market, session_date, ticker, MIN(occurred_at)
           FROM lifecycle_events
           WHERE event_type='CLAUDE_PRICE_CANCELLED'
             AND payload_json LIKE '%REQUIRE_TRADE_READY_BLOCK%'
           GROUP BY market, session_date, ticker ORDER BY session_date"""
    rows = []
    for m, sd, tk, occ in c.execute(q):
        try:
            dt = datetime.fromisoformat(str(occ).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            rows.append((str(m).upper(), str(sd), str(tk), dt.astimezone(KST).isoformat()))
        except ValueError:
            continue
    c.close()
    return rows


def session_mode(cache: dict, market: str, sd: str) -> str:
    key = f"{market}|{sd}"
    memo = cache.setdefault("mode", {})
    if key in memo:
        return memo[key]
    mode = "NA"
    try:
        c = sqlite3.connect(f"file:{DECISIONS_DB}?mode=ro", uri=True, timeout=10)
        c.execute("PRAGMA busy_timeout=5000")
        r = c.execute(
            "SELECT mode, COUNT(*) FROM decisions WHERE market=? AND session_date=? AND mode IS NOT NULL "
            "GROUP BY mode ORDER BY COUNT(*) DESC LIMIT 1",
            (market, sd),
        ).fetchone()
        c.close()
        if r and r[0]:
            mode = str(r[0])
    except sqlite3.Error:
        pass
    memo[key] = mode
    return mode


def load_bars(market: str, ticker: str) -> list[tuple[str, float, float]]:
    prefix = "us" if market == "US" else "kr"
    p = MIN_DIR[market] / f"{prefix}_{ticker}.csv"
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out.append((r["ts"], float(r["high"]), float(r["close"])))
            except (KeyError, ValueError, TypeError):
                continue
    return out


def session_window(market: str, sd: str, bars: list) -> list:
    """세션 분봉: US=당일 저녁~익일 06시 전(KST), KR=당일 09~16시."""
    if market == "US":
        nxt = (datetime.fromisoformat(sd) + timedelta(days=1)).strftime("%Y-%m-%d")
        return [b for b in bars if b[0][:10] == sd and b[0][11:13] >= "17"
                or (b[0][:10] == nxt and b[0][11:13] < "06")]
    return [b for b in bars if b[0][:10] == sd and "09" <= b[0][11:13] <= "16"]


def block_to_close(market: str, sd: str, block_ts: str, bars: list):
    sess = session_window(market, sd, bars)
    if not sess:
        return None
    ref = None
    for ts, _h, cl in sess:
        if ts <= block_ts:
            ref = cl
    after = [b for b in sess if b[0] > block_ts]
    if ref is None or ref <= 0 or not after:
        return None
    return (after[-1][2] / ref - 1) * 100  # 블록→종가 %


def yf_beta(cache: dict, market: str, ticker: str) -> float | None:
    """6개월 일봉 β (vs 시장 벤치마크). 월 단위 캐시."""
    key = f"{market}|{ticker}|{datetime.now().strftime('%Y-%m')}"
    if key in cache["beta"]:
        return cache["beta"][key]
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        sym = ticker if market == "US" else None
        if market == "KR":
            for suf in (".KS", ".KQ"):
                probe = yf.download(ticker + suf, period="6mo", interval="1d", progress=False, auto_adjust=False)
                if not probe.empty:
                    sym, tdf = ticker + suf, probe
                    break
            else:
                cache["beta"][key] = None
                return None
        else:
            tdf = yf.download(sym, period="6mo", interval="1d", progress=False, auto_adjust=False)
        bdf = yf.download(BENCH[market], period="6mo", interval="1d", progress=False, auto_adjust=False)
        if tdf.empty or bdf.empty:
            cache["beta"][key] = None
            return None
        tc = tdf["Close"].squeeze().pct_change().dropna()
        bc = bdf["Close"].squeeze().pct_change().dropna()
        joined = tc.to_frame("t").join(bc.to_frame("b"), how="inner").dropna()
        if len(joined) < 40 or float(joined["b"].var()) == 0:
            cache["beta"][key] = None
            return None
        beta = float(joined["t"].cov(joined["b"]) / joined["b"].var())
        cache["beta"][key] = round(beta, 4)
        return cache["beta"][key]
    except Exception:
        cache["beta"][key] = None
        return None


def bench_window_ret(cache: dict, market: str, sd: str, block_ts: str) -> float | None:
    """벤치마크의 블록시점→세션종가 수익률(%). 세션·블록분 단위 캐시."""
    key = f"{market}|{sd}|{block_ts[:16]}"
    if key in cache["bench_win"]:
        return cache["bench_win"][key]
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        start = sd
        end = (datetime.fromisoformat(sd) + timedelta(days=2)).strftime("%Y-%m-%d")
        df = yf.download(BENCH[market], start=start, end=end, interval="5m", progress=False, auto_adjust=False)
        if df.empty:
            cache["bench_win"][key] = None
            return None
        df.index = df.index.tz_convert("Asia/Seoul")
        bars = [(ts.isoformat(), float(row["High"].iloc[0] if hasattr(row["High"], "iloc") else row["High"]),
                 float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"]))
                for ts, row in df.iterrows()]
        ret = block_to_close(market, sd, block_ts, bars)
        cache["bench_win"][key] = None if ret is None else round(ret, 4)
        return cache["bench_win"][key]
    except Exception:
        cache["bench_win"][key] = None
        return None


def main() -> int:
    cache = _load_cache()
    blocks = load_blocks()
    print(f"RTRB 블록 (세션-종목 유니크) {len(blocks)}건  [소스=lifecycle_events CLAUDE_PRICE_CANCELLED]")

    events = []  # (market, sd, ticker, mode, raw_ret, excess_ret|None)
    missing = 0
    for market, sd, tk, bts in blocks:
        bars = load_bars(market, tk)
        raw = block_to_close(market, sd, bts, bars) if bars else None
        if raw is None:
            missing += 1
            continue
        mode = session_mode(cache, market, sd)
        beta = yf_beta(cache, market, tk)
        bret = bench_window_ret(cache, market, sd, bts)
        excess = raw - beta * bret if (beta is not None and bret is not None) else None
        events.append((market, sd, tk, mode, raw, excess))
    _save_cache(cache)

    print(f"경로 복원 {len(events)}건 / 결측 {missing} (분봉 누적되며 채워짐)\n")
    if not events:
        return 0

    for market in ("US", "KR"):
        ev = [e for e in events if e[0] == market]
        if not ev:
            continue
        print(f"===== {market} (이벤트 n={len(ev)}) =====")
        raws = [e[4] for e in ev]
        excs = [e[5] for e in ev if e[5] is not None]
        print(f"  이벤트 단위: raw 블록→종가 mean {st.mean(raws):+.2f} med {st.median(raws):+.2f}"
              + (f" | β-adj excess mean {st.mean(excs):+.2f} med {st.median(excs):+.2f} (n={len(excs)})" if excs else " | β-adj 산출불가"))
        tail = 100 * sum(1 for x in raws if x <= -5) / len(raws)
        print(f"  좌꼬리(raw ≤ -5%): {tail:.0f}%")

        # 세션 클러스터 (판정 단위)
        by_sess = defaultdict(list)
        for e in ev:
            if e[5] is not None:
                by_sess[e[1]].append(e[5])
        sess_means = {sd: st.mean(v) for sd, v in by_sess.items()}
        if sess_means:
            vals = list(sess_means.values())
            mean = st.mean(vals)
            tstat = None
            if len(vals) >= 2 and st.pstdev(vals) > 0:
                tstat = mean / (st.stdev(vals) / (len(vals) ** 0.5))
            print(f"  세션 클러스터(β-adj, 판정 단위): n_sess={len(vals)} mean {mean:+.2f}"
                  + (f" t={tstat:.2f}" if tstat is not None else " t=산출불가"))
            for sd in sorted(sess_means):
                print(f"    {sd}: n={len(by_sess[sd]):2} sess-mean {sess_means[sd]:+.2f}")

        # regime 분리 (핵심: 비강세 표본 존재 여부)
        by_regime = defaultdict(list)
        for e in ev:
            by_regime[e[3]].append(e[5] if e[5] is not None else e[4])
        strong_n = sum(len(v) for m, v in by_regime.items() if m in STRONG)
        weak_n = sum(len(v) for m, v in by_regime.items() if m in WEAK)
        print(f"  regime: 강세 n={strong_n} / 약세·방어 n={weak_n} / 기타 n={len(ev) - strong_n - weak_n}")
        for mode in sorted(by_regime, key=lambda m: -len(by_regime[m])):
            v = by_regime[mode]
            print(f"    {mode:16} n={len(v):3} mean {st.mean(v):+.2f} med {st.median(v):+.2f}")

        # 메가캡/종목 농도 (완화 실체 경고 지표)
        cnt = defaultdict(int)
        for e in ev:
            cnt[e[2]] += 1
        top = sorted(cnt.items(), key=lambda x: -x[1])[:6]
        conc = 100 * sum(n for _, n in top) / len(ev)
        print(f"  종목 농도 top6 = {conc:.0f}%: " + ", ".join(f"{t}×{n}" for t, n in top))
        print()

    print("판정 문턱(설계 고정): shadow 승격 = 비강세 포함 US ≥20세션 AND 세션평균 β-adj > +0.70 AND t≥2")
    print("킬 = 다국면 β-adj med < +0.3 또는 비강세 구간 med < 0. 문턱 도달 전 어떤 완화도 금지.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
