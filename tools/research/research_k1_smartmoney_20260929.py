#!/usr/bin/env python3
"""K1 스마트머니 + 바닥 + 턴어라운드 — 월간 리밸런스 사전등록 검증 (read-only, 2026-09-29).

## 사전등록 (수익률 계산 전에 고정 — 파라미터 탐색 없음)

데이터: instiwatch DB(읽기 전용 URI). signals는 2024-06/07부터만 존재한다(DART majorstock API가
최근 ~2년만 돌려준다 — 경동나비엔 호출로 확인). 따라서 본 검증은 **약 12개 월 코호트**뿐이다.
12코호트의 NW t는 어느 방향이든 판정력이 없다 → 게이트는 기계적으로 적용하되 결론 등급은
"판정 보류(방향 관측)"를 넘지 않는다. 운 좋은 t≥2.5도 '후보'로 승격하지 않는다.

시점(PIT):
- 리밸런스일 T = 매월 첫 거래일(069500 달력). 판단 정보 = signal_date < T 인 공시, 종가 ≤ T−1.
- 매수 = T 종가, 매도 = T+60세션 종가(069500 달력). 등가중. 비용 0.25% 왕복(포트 쪽만 차감).
- 적격: T−1 종가 ≥ 1,000원, dvol20(T−1까지 20봉 종가×거래량 평균) ≥ 1억, T에 봉 존재.
- 1년 가격위치 pos = (T−1 종가 − 1년 최저)/(최고−최저)×100, 1년 창 봉 ≥ 200 필요.
  소스: instiwatch hist_prices(YYYYMMDD) 우선, 없으면 수익률 소스.
- 수익률 소스: data/price/kr(OHLCV) 우선, 없으면 yfinance(.KS/.KQ = disclosures.corp_cls Y/K) 캐시.
- 분할 가드: 보유창 안에 |일간수익| > 35% → 그 종목-코호트 제외(계수 보고).
- 데이터 결손: 청산일 −5세션 이전에 시계열이 끝나면 제외(상폐/결손 → 절대수익은 상한).

규칙(각각 별도 포트, 다중검정에 전부 계산):
- R0 NPS_up: 국민연금(institution에 '국민연금' — tier1엔 한국증권금융·산업은행이 섞여 있어 이름으로 거른다)
  new/increase, T−90일 ≤ signal_date < T.
- R1 NPS_up_bottom: R0 AND pos ≤ 35.
- R2 NPS_reversal_bottom: R1 AND 창 안 가장 최근 NPS up 직전의 NPS 시그널이 decrease/exit.
- R3 smart_intersection: instiwatch 교집합 로직을 저장된 tier 그대로(fallback 매칭 포함) 따른다.
  비내부자 시그널 [T−180일, T): 저장 score 합 ≥ 4 AND 양(+) 시그널 기관 ≥ 2 AND 그 기관들이
  자기 마지막 양 시그널 이후 창 안에서 decrease/exit 없음.
- R4 insider_buy_bottom: insider_buy & method='장내매수' & 기관명 '국민연금' 제외, [T−90일, T),
  금액 = shares_change × 공시일 이하 최근 종가, 합 ≥ 5천만원 AND pos ≤ 35.
  ⚠️ method 정제는 2025-12-15부터만 존재 → 90일 창이 꽉 차는 건 2026-03 코호트부터. 사전에 '커버리지 부족' 예상.
- R5 composite: R1∪R3∪R4 후보, 180일 점수 = 비내부자 저장 score 합(NPS 3, tier2 2, tier3 new 2/inc 1,
  decrease −2, exit −3) + 내부자 장내매수 +2(내부자 이름 1명당 1회) + 바닥 보너스 +2(pos ≤ 35).
  상위 10, 동점은 pos 낮은 순 → 코드 순. (저장 insider score는 config 키 부재로 0이라 쓰지 않는다.)
- R6 R5 + 실적 턴어라운드: T1-KR events_features(kind prelim/pl30, op_yoy 비결측)에서 ed < T,
  ed ≥ T−200일 최신 행이 op_yoy > 0 또는 turn_up=1. 커버리지(R5 픽-월 중 해당 공시 보유 비율) < 70% →
  '검정 불가'로 보고.

평가: 코호트별 3개월 초과(vs KOSPI200 069500, vs KOSDAQ150 229200, vs 규모매칭 = T의 적격
유니버스를 dvol20 십분위로 나눠 픽과 같은 십분위 EW 평균 60세션 수익, 비용 없음).
평균·중앙·적중률·NW t(lag 2)·전후반(코호트 반분)·연도별·월 종목수·턴오버(전월 대비 신규 비율).
시험기간: T ≥ 2025-07(tier1 첫 달 2024-07 + 12개월) ~ T+60 ≤ 최신 봉. 민감도(판정 경로 아님): T ≥ 2025-01.
통과: 규모매칭 AND KOSPI200 NW t ≥ 2.5 + 양 반기 양수 → 후보(shadow 권고), ≥3 → 확정 후보.
(단 위 12코호트 단서로 등급 상한은 '판정 보류'.)
[데이터 발견 후 수정 — 수익률 계산 전 결정이 아니라 첫 실행 후 발견, 규칙 정의는 불변] instiwatch insider 행은 30,287건 전량
superseded=1(내부자 tier 일괄 비활성)이라 insider에 한해 superseded를 무시한다. 민감도(T ≥ 2025-01)는 유니버스
거래량(data/price/kr)이 2025-05-16부터라 규모매칭 벤치를 만들 수 없어 취소한다.
[진단 — 판정 경로 아님, 첫 결과 확인 후 추가] NPS 보유 유니버스 벤치: T 이전 NPS 공시가 있고 최신 NPS 공시가 exit가 아닌
적격 종목 EW. "늘렸다"는 정보가 "NPS가 들고 있는 종목군" 효과와 다른지 분리한다.
운영자 거래(NHN 181710, 코스맥스 192820, 07-01 공시, 07-02 매수)는 규칙 틀 안 위치 + 규칙 밖 사후 관측으로 따로 보고.
"""
from __future__ import annotations

import contextlib
import csv
import datetime as dt
import gzip
import json
import math
import sqlite3
import statistics as st
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "k1_smartmoney_20260929"
OUT.mkdir(parents=True, exist_ok=True)
KR_DIR = ROOT / "data" / "price" / "kr"
DB = "file:E:/code/instiwatch/data/instiwatch.db?mode=ro"
FEAT = ROOT / "data" / "analysis" / "model_T1kr_earnings_20260928" / "events_features.csv.gz"
YF_CACHE = OUT / "yf_prices.csv"
H, COST, POS_MAX = 60, 0.25, 35.0
START, SENS_START = "2025-07-01", "2025-01-01"


def d8(s): return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def load_kr(code):
    p = KR_DIR / f"kr_{code}.csv"
    if not p.exists():
        return None
    rows = {}
    with p.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                rows[r["date"][:10]] = (float(r["close"]), float(r["volume"]))
            except (ValueError, KeyError):
                pass
    return rows


def main():
    with contextlib.closing(sqlite3.connect(DB, uri=True, timeout=10)) as c:
        c.row_factory = sqlite3.Row
        sigs = [dict(r) for r in c.execute(
            "SELECT signal_date,stock_code,corp_name,institution,tier,kind,score,method,shares_change "
            "FROM signals WHERE (superseded=0 OR tier='insider') AND stock_code!='' ORDER BY signal_date")]
        cls = {r[0]: r[1] for r in c.execute(
            "SELECT stock_code, corp_cls FROM disclosures WHERE stock_code!='' GROUP BY stock_code")}
        hp = defaultdict(dict)
        for code, d, cl in c.execute("SELECT stock_code,date,close FROM hist_prices WHERE date>='20230101'"):
            hp[code][d8(d)] = float(cl)
    for s in sigs:
        s["nps"] = "국민연금" in (s["institution"] or "")
    ins_buys = [s for s in sigs if s["tier"] == "insider" and s["kind"] == "insider_buy"
                and s["method"] == "장내매수" and not s["nps"]]
    need = {s["stock_code"] for s in sigs if s["tier"] != "insider"} | {s["stock_code"] for s in ins_buys} | {"181710", "192820"}

    # ---- prices ----
    px = {}
    for p in KR_DIR.glob("kr_*.csv"):
        px[p.stem[3:]] = load_kr(p.stem[3:])
    miss = sorted(x for x in need if x not in px)
    yf = defaultdict(dict)
    if YF_CACHE.exists():
        for r in csv.DictReader(YF_CACHE.open(encoding="utf-8")):
            yf[r["code"]][r["date"]] = (float(r["close"]), float(r["volume"]))
    todo = [x for x in miss if x not in yf]
    if todo:
        import yfinance as yfm
        rows = []
        for i in range(0, len(todo), 100):
            batch = todo[i:i + 100]
            tick = {f"{x}.{'KQ' if cls.get(x) == 'K' else 'KS'}": x for x in batch}
            df = yfm.download(list(tick), start="2024-06-01", end="2026-09-30", auto_adjust=False,
                              progress=False, group_by="ticker", threads=True)
            for t, code in tick.items():
                try:
                    sub = df[t][["Close", "Volume"]].dropna()
                except Exception:
                    continue
                for idx, (cl, vo) in sub.iterrows():
                    if cl > 0:
                        rows.append((code, idx.strftime("%Y-%m-%d"), float(cl), float(vo)))
                        yf[code][idx.strftime("%Y-%m-%d")] = (float(cl), float(vo))
            print(f"yf {i + len(batch)}/{len(todo)}", file=sys.stderr)
        new = not YF_CACHE.exists()
        with YF_CACHE.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["code", "date", "close", "volume"])
            w.writerows(rows)
    for code, d in yf.items():
        if code not in px:
            px[code] = d
    src_yf = {c for c in yf if c in miss}
    sd = {code: sorted(d) for code, d in px.items() if d}

    cal = sd["069500"]
    kq = px["229200"]
    ks = px["069500"]
    months = sorted({d[:7] for d in cal})
    rebal = [next(d for d in cal if d[:7] == m) for m in months]
    rebal = [T for T in rebal if cal.index(T) + H < len(cal) and T >= START]

    def close_on(code, d):
        v = px.get(code, {}).get(d)
        return v[0] if v else None

    def last_close_le(code, d):
        ds = sd.get(code)
        if not ds:
            return None, None
        i = bisect_right(ds, d) - 1
        return (ds[i], px[code][ds[i]][0]) if i >= 0 else (None, None)

    def pos1y(code, dprev):
        src = hp.get(code) if code in hp and max(hp[code]) >= dprev else None
        if src is None:
            src = {d: v[0] for d, v in px.get(code, {}).items()}
        lo = (dt.date.fromisoformat(dprev) - dt.timedelta(days=365)).isoformat()
        vals = [v for d, v in src.items() if lo < d <= dprev]
        if len(vals) < 200:
            return None
        last = src[max(d for d in src if d <= dprev)]
        mn, mx = min(vals), max(vals)
        return 100 * (last - mn) / (mx - mn) if mx > mn else None

    def dvol20(code, dprev):
        ds = sd.get(code)
        if not ds:
            return None
        i = bisect_right(ds, dprev)
        w = ds[max(0, i - 20):i]
        if len(w) < 20:
            return None
        return st.mean(px[code][d][0] * px[code][d][1] for d in w)

    def fwd(code, T, E, cal_win):
        """T 종가 → E 종가 수익%. None + 사유."""
        cT = close_on(code, T)
        if cT is None:
            return None, "no_T_bar"
        de, ce = last_close_le(code, E)
        if de is None or de < cal_win[-6]:
            return None, "series_end"
        prev = cT
        for d in cal_win[1:]:
            v = close_on(code, d)
            if v is None:
                continue
            if abs(v / prev - 1) > 0.35:
                return None, "split_guard"
            prev = v
            if d >= de:
                break
        return 100 * (ce / cT - 1), None

    # earnings features
    fin = defaultdict(list)
    with gzip.open(FEAT, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["op_yoy"] not in ("", None) or r["turn_up"] == "1":
                fin[r["t"]].append((r["ed"], r["op_yoy"], r["turn_up"]))
    for v in fin.values():
        v.sort()

    by_code = defaultdict(list)
    for s in sigs:
        if s["tier"] != "insider":
            by_code[s["stock_code"]].append(s)
    nps_by = defaultdict(list)
    for s in sigs:
        if s["nps"] and s["tier"] != "insider":
            nps_by[s["stock_code"]].append(s)
    ins_by = defaultdict(list)
    for s in ins_buys:
        ins_by[s["stock_code"]].append(s)

    RULES = ["R0", "R1", "R2", "R3", "R4", "R5", "R6"]
    cohorts = []
    ledger = []
    drop = defaultdict(lambda: defaultdict(int))
    r6_cov = [0, 0]
    for T in rebal:
        ti = cal.index(T)
        Tp, E = cal[ti - 1], cal[ti + H]
        win = cal[ti:ti + H + 1]
        T8 = T.replace("-", "")
        lo90 = (dt.date.fromisoformat(T) - dt.timedelta(days=90)).strftime("%Y%m%d")
        lo180 = (dt.date.fromisoformat(T) - dt.timedelta(days=180)).strftime("%Y%m%d")
        # eligible universe + size deciles
        elig = {}
        for code in sd:
            if code in ("069500", "229200"):
                continue
            cp = last_close_le(code, Tp)[1]
            if cp is None or cp < 1000 or close_on(code, T) is None:
                continue
            dv = dvol20(code, Tp)
            if dv is None or dv < 1e8:
                continue
            elig[code] = dv
        order = sorted(elig, key=elig.get)
        dec = {code: min(9, i * 10 // len(order)) for i, code in enumerate(order)}
        ucache = {}
        dec_ret = defaultdict(list)
        for code in order:
            r, why = fwd(code, T, E, win)
            ucache[code] = (r, why)
            if r is not None:
                dec_ret[dec[code]].append(r)
        dec_mean = {k: st.mean(v) for k, v in dec_ret.items()}
        posc = {}

        def P(code):
            if code not in posc:
                posc[code] = pos1y(code, Tp)
            return posc[code]

        picks = {r: [] for r in RULES}
        # R0/R1/R2
        for code, lst in nps_by.items():
            if code not in elig:
                continue
            ups = [s for s in lst if s["kind"] in ("new", "increase") and lo90 <= s["signal_date"] < T8]
            if not ups:
                continue
            picks["R0"].append(code)
            p = P(code)
            if p is None or p > POS_MAX:
                continue
            picks["R1"].append(code)
            last_up = ups[-1]
            prior = [s for s in lst if s["signal_date"] < last_up["signal_date"]]
            if prior and prior[-1]["kind"] in ("decrease", "exit"):
                picks["R2"].append(code)
        # R3 + scores
        score = {}
        for code, lst in by_code.items():
            w = [s for s in lst if lo180 <= s["signal_date"] < T8]
            if not w:
                continue
            sc = sum(s["score"] or 0 for s in w)
            score[code] = sc
            if code not in elig:
                continue
            pos_inst = {}
            for s in w:
                if (s["score"] or 0) > 0:
                    pos_inst[s["institution"]] = s["signal_date"]
            if sc >= 4 and len(pos_inst) >= 2:
                bad = any(s["institution"] in pos_inst and s["kind"] in ("decrease", "exit")
                          and s["signal_date"] > pos_inst[s["institution"]] for s in w)
                if not bad:
                    picks["R3"].append(code)
        # R4
        ins_bonus = {}
        for code, lst in ins_by.items():
            w180 = [s for s in lst if lo180 <= s["signal_date"] < T8]
            if w180:
                ins_bonus[code] = 2 * len({s["institution"] for s in w180})
            if code not in elig:
                continue
            amt = 0.0
            for s in lst:
                if lo90 <= s["signal_date"] < T8 and (s["shares_change"] or 0) > 0:
                    _, cl = last_close_le(code, d8(s["signal_date"]))
                    if cl:
                        amt += s["shares_change"] * cl
            if amt >= 5e7:
                p = P(code)
                if p is not None and p <= POS_MAX:
                    picks["R4"].append(code)
        cand = set(picks["R1"]) | set(picks["R3"]) | set(picks["R4"])
        ranked = sorted(cand, key=lambda x: (-(score.get(x, 0) + ins_bonus.get(x, 0)
                                               + (2 if (P(x) is not None and P(x) <= POS_MAX) else 0)),
                                             P(x) if P(x) is not None else 999, x))
        picks["R5"] = ranked[:10]
        lo200 = (dt.date.fromisoformat(T) - dt.timedelta(days=200)).isoformat()
        for code in picks["R5"]:
            rows = [r for r in fin.get(code, []) if lo200 <= r[0] < T]
            r6_cov[1] += 1
            if not rows:
                continue
            r6_cov[0] += 1
            ed, oy, tu = rows[-1]
            if tu == "1" or (oy not in ("", None) and float(oy) > 0):
                picks["R6"].append(code)
        held = [code for code, lst in nps_by.items()
                if code in elig and [x for x in lst if x["signal_date"] < T8]
                and [x for x in lst if x["signal_date"] < T8][-1]["kind"] != "exit"]
        hr = [ucache[c][0] for c in held if ucache.get(c, (None,))[0] is not None]
        npsb = st.mean(hr) if hr else float("nan")
        kret = 100 * (ks[E][0] / ks[T][0] - 1)
        qret = 100 * (kq[E][0] / kq[T][0] - 1)
        for rule in RULES:
            rets, sm = [], []
            for code in picks[rule]:
                r, why = ucache.get(code, (None, "not_elig"))
                if r is None:
                    drop[rule][why] += 1
                    continue
                rets.append(r - COST)
                sm.append(dec_mean.get(dec[code], float("nan")))
                ledger.append({"T": T, "rule": rule, "code": code, "pos": P(code), "ret": round(r, 2)})
            if rets:
                pr = st.mean(rets)
                cohorts.append({"T": T, "rule": rule, "n": len(rets), "names": sorted(picks[rule]),
                                "ret": pr, "x_k200": pr - kret, "x_kq150": pr - qret,
                                "x_size": pr - st.mean(sm), "k200": kret, "kq150": qret, "sizeb": st.mean(sm), "npsb": npsb, "n_held": len(hr)})
        print(T, {r: len(picks[r]) for r in RULES}, "elig", len(elig), file=sys.stderr)

    # operator trades
    op = {}
    for code in ("181710", "192820"):
        d0 = "2026-07-02"
        c0 = close_on(code, d0)
        de, ce = last_close_le(code, cal[-1])
        k0, ke = ks[d0][0], ks[cal[-1]][0]
        op[code] = {"entry": c0, "exit_date": de, "exit": ce,
                    "ret": 100 * (ce / c0 - 1) if c0 else None, "k200": 100 * (ke / k0 - 1),
                    "pos_at_0701": pos1y(code, "2026-07-01"),
                    "pos_at_0731": pos1y(code, "2026-07-31"),
                    "src": "yfinance" if code in src_yf else "data/price/kr"}

    json.dump({"cohorts": cohorts, "drop": {k: dict(v) for k, v in drop.items()}, "r6_cov": r6_cov,
               "op": op, "rebal": rebal, "yf_n": len(src_yf), "miss_n": len(miss),
               "yf_missing": sorted(set(miss) - set(yf))},
              (OUT / "results.json").open("w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    with (OUT / "picks_ledger.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["T", "rule", "code", "pos", "ret"])
        w.writeheader()
        w.writerows(ledger)
    summarize(cohorts)


def nw_t(x, lag=2):
    n = len(x)
    if n < 3:
        return float("nan")
    m = sum(x) / n
    e = [v - m for v in x]
    g0 = sum(v * v for v in e) / n
    s = g0
    for k in range(1, lag + 1):
        gk = sum(e[i] * e[i - k] for i in range(k, n)) / n
        s += 2 * (1 - k / (lag + 1)) * gk
    return m / math.sqrt(s / n) if s > 0 else float("nan")


def summarize(cohorts):
    lines = []
    for start in (START,):
        lines.append(f"\n### 시작 {start} (사전등록 본판)")
        lines.append("| 규칙 | 코호트 | 평균 종목수 | 턴오버 | 원수익 | 벤치 | 평균 | 중앙 | 적중 | NW t | 전반 | 후반 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for rule in ["R0", "R1", "R2", "R3", "R4", "R5", "R6"]:
            cs = [c for c in cohorts if c["rule"] == rule and c["T"] >= start]
            if not cs:
                lines.append(f"| {rule} | 0 | - | - | - | - | - | - | - | - | - | - |")
                continue
            to = []
            for a, b in zip(cs, cs[1:]):
                if b["names"]:
                    to.append(len(set(b["names"]) - set(a["names"])) / len(b["names"]))
            raw = st.mean(c["ret"] for c in cs)
            for key, lab in (("x_size", "규모매칭"), ("x_k200", "KOSPI200"), ("x_kq150", "KOSDAQ150")):
                x = [c[key] for c in cs]
                h = len(x) // 2
                h1 = st.mean(x[:h]) if h else float("nan")
                h2 = st.mean(x[h:])
                lines.append(f"| {rule} | {len(cs)} | {st.mean(c['n'] for c in cs):.1f} | "
                             f"{(st.mean(to) * 100 if to else float('nan')):.0f}% | {raw:+.2f} | {lab} | "
                             f"{st.mean(x):+.2f} | {st.median(x):+.2f} | {sum(v > 0 for v in x)}/{len(x)} | "
                             f"{nw_t(x):+.2f} | {h1:+.2f} | {h2:+.2f} |")
    lines.append("\n### 연도별 (본판, 규모매칭 초과 평균 / 코호트수)")
    for rule in ["R0", "R1", "R2", "R3", "R4", "R5", "R6"]:
        by = defaultdict(list)
        for c in cohorts:
            if c["rule"] == rule and c["T"] >= START:
                by[c["T"][:4]].append(c["x_size"])
        lines.append(f"- {rule}: " + ", ".join(f"{y} {st.mean(v):+.2f} ({len(v)})" for y, v in sorted(by.items())))
    lines.append("\n### 코호트 상세 (본판, 규모매칭 초과 / KOSPI200 초과 / n)")
    for rule in ["R0", "R1", "R2", "R3", "R4", "R5", "R6"]:
        cs = [c for c in cohorts if c["rule"] == rule and c["T"] >= START]
        lines.append(f"- {rule}: " + "; ".join(f"{c['T'][:7]} {c['x_size']:+.1f}/{c['x_k200']:+.1f}/n{c['n']}" for c in cs))
    txt = "\n".join(lines)
    (OUT / "summary.md").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
