#!/usr/bin/env python3
"""KR 지수 정기변경 편입(KOSPI200·KOSDAQ150) — 사전등록 §1 검정 (read-only 연구, 2026-10-30).

사전등록 정본: docs/reports/preregistration_q4_2026_20261022.md §1 (기준 동결, 이 스크립트는 그 기준을 그대로 구현).
운영 파일·봇·state·config 는 건드리지 않는다. KIS 호출 없음. KRX 자동수집 없음(약관) — 편출입 명단은 공개 기사를
사람(에이전트)이 읽고 옮겨 적은 data/reference/kr_index_changes.csv 만 쓴다.

서브커맨드
  names   : 종목명 → 6자리 코드 사전. ① DART list(2019~, 접수 당시 회사명·종목코드, kr_event_20260930/list)
            ② OpenDART corpCode.xml(현재명, 1회 호출, 캐시). 키는 .env.live DART_API_KEY 를 dotenv 로 읽고 출력하지 않는다.
  build   : 수작업 원본 CSV(스크래치) → data/reference/kr_index_changes.csv. 기사에 코드가 있으면 그 코드, 없으면 사전 매핑.
            미매핑은 행을 버리지 않고 ticker 공란 + unmapped 목록으로 보고.
  prices  : 네이버 siseJson(수정주가 일봉, 분석 전용 소스) 2013-01-01~ 을 유니버스 전체에 받는다(캐시).
            유니버스 = yfinance 백필 1,746종목 ∪ 이벤트 종목 ∪ 069500·229200. 호출 간 0.25s.
  test    : 사전등록 검정.

사전등록 §1 그대로
- 주 행: 신규 편입(action=add) 종목. 발표 다음 거래일 시가 매수 → 효력일 전 거래일 종가 매도. 비용 0.25%.
  신호일 s = 발표일 이하 마지막 거래일(장 마감 후 발표 가정), 진입 e = s+1, 청산 x = 효력일 미만 마지막 거래일.
- 벤치: 규모매칭(dvol20 십분위, kr_event_20260930 Book 규칙 재사용 — 적격 = s 종가 ≥ 1,000, dvol20 ≥ 20억,
  이력 ≥ 25봉, e 시가 > 0; 같은 창 십분위 등가중)이 주, 해당 지수 ETF(069500 / 229200)가 보조.
- t: 발표일 클러스터(같은 발표일의 KOSPI200·KOSDAQ150 편입 종목을 한 관측으로 등가중) 시계열의 t.
  회차 창이 서로 겹치지 않으므로 주 t 는 lag 0(단순 t), NW lag 1 병기.
- 합격: 클러스터 t ≥ 2.64 AND IS(발표 2010~2019)·OOS(2020~) 평균 같은 부호 AND 클러스터 부호 양(+) 과반.
- 검정 불가: 2014년 이후 클러스터 < 24.
- 보조(판정 제외): 효력일 시가 → H20 종가 반전(편입·편출 각각, 규모매칭 초과).
- 가격 가드: 창 안 시가·종가가 전일 종가 대비 ±30.5% 밖이면 결측(미수정 분할 등).
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "kr_index_inclusion_20261030"
PX = OUT / "px"
REF = ROOT / "data" / "reference" / "kr_index_changes.csv"
NAMEMAP = OUT / "name_map.json"
CORPCODE = OUT / "corpcode_current.json"
DART_LIST = ROOT / "data" / "analysis" / "kr_event_20260930" / "list"
YF_CACHE = ROOT / "data" / "price_backfill_yf" / "kr_fallen_price_cache_2018_2026.json"
REPORT_JSON = OUT / "results.json"
COST = 0.25
MIN_HIST, DVOL_MIN, PX_MIN, GUARD = 25, 2e9, 1000.0, 0.305
T_PASS = 2.64
MIN_CLUSTERS = 24
FIELDS = ["index", "announce_date", "effective_date", "ticker", "name", "action", "source_url"]


def _get(url: str, timeout: float = 25.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "")
    s = re.sub(r"\(주\)|㈜|주식회사|\s+", "", s)
    return s.upper()


# ── names ──────────────────────────────────────────────────────────────────
def cmd_names() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    m: dict[str, dict[str, str]] = defaultdict(dict)   # norm name -> {code: last_seen}
    for fn in sorted(DART_LIST.glob("*.jsonl")):
        with fn.open(encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                sc = (r.get("stock_code") or "").strip()
                if len(sc) == 6 and r.get("corp_cls") in ("Y", "K"):
                    k = norm(r.get("corp_name", ""))
                    d = r.get("rcept_dt", "")
                    if d > m[k].get(sc, ""):
                        m[k][sc] = d
    if not CORPCODE.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env.live")
        except Exception:
            pass
        key = (os.getenv("DART_API_KEY") or "").strip()
        raw = _get(f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}", 60)
        z = zipfile.ZipFile(io.BytesIO(raw))
        xml = z.read(z.namelist()[0]).decode("utf-8", "ignore")
        rows = []
        for blk in re.findall(r"<list>(.*?)</list>", xml, re.S):
            g = lambda t: (re.search(rf"<{t}>(.*?)</{t}>", blk, re.S) or [None, ""])[1].strip()  # noqa: E731
            sc = g("stock_code")
            if len(sc) == 6:
                rows.append({"name": g("corp_name"), "code": sc, "modify": g("modify_date")})
        CORPCODE.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    for r in json.loads(CORPCODE.read_text(encoding="utf-8")):
        k = norm(r["name"])
        m[k].setdefault(r["code"], "current")
    NAMEMAP.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    print(f"[names] {len(m)} names")


# ── build ──────────────────────────────────────────────────────────────────
MANUAL = OUT / "manual_name_codes.json"   # 기사 외 수동 해소(사유 기록) — 비어 있으면 사용 안 함


def cmd_build(raw_paths: list[str]) -> None:
    nm = json.loads(NAMEMAP.read_text(encoding="utf-8"))
    manual = json.loads(MANUAL.read_text(encoding="utf-8")) if MANUAL.exists() else {}
    rows, unmapped, conflicts = [], [], []
    seen = set()
    # 회차(index, announce_date)가 뒤 파일에 있으면 뒤 파일이 그 회차 전체를 대체한다(보충 수집본이 완전 명단).
    per_file = []
    for p in raw_paths:
        with open(p, encoding="utf-8-sig", newline="") as fh:
            per_file.append(list(csv.DictReader(fh)))
    owner = {}
    for fi, lst in enumerate(per_file):
        for r in lst:
            owner[(r["index"].strip().upper().replace(" ", ""), r["announce_date"].strip())] = fi
    for fi, lst in enumerate(per_file):
        if True:
            for r in lst:
                if owner[(r["index"].strip().upper().replace(" ", ""), r["announce_date"].strip())] != fi:
                    continue
                idx = r["index"].strip().upper().replace(" ", "")
                name = r["name"].strip()
                tk = re.sub(r"\D", "", r.get("ticker") or "")
                cand = nm.get(norm(name), {})
                if tk and len(tk) == 6:
                    if cand and tk not in cand:
                        conflicts.append(f"{r['announce_date']} {idx} {name}: article {tk} vs map {sorted(cand)}")
                elif f"{norm(name)}@{r['announce_date'].strip()}" in manual:      # 날짜 한정 해소(분할 전 구 코드)
                    tk = manual[f"{norm(name)}@{r['announce_date'].strip()}"]
                elif norm(name) in manual:
                    tk = manual[norm(name)]
                elif len(cand) == 1:
                    tk = next(iter(cand))
                elif len(cand) > 1:
                    # 같은 이름 여러 코드: 발표일 이전 가장 최근에 그 이름으로 보인 코드
                    ad = r["announce_date"].replace("-", "")
                    best = sorted(((v if v != "current" else "99999999"), c) for c, v in cand.items())
                    tk = best[-1][1]
                    conflicts.append(f"{r['announce_date']} {idx} {name}: multi {sorted(cand)} -> {tk} (ad {ad})")
                else:
                    tk = ""
                    unmapped.append(f"{r['announce_date']} {idx} {r['action']} {name}")
                src = r.get("source_url", "").strip()
                if (r.get("second_source_url") or "").strip():
                    src += " | " + r["second_source_url"].strip()
                key = (idx, r["announce_date"], name, r["action"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"index": idx, "announce_date": r["announce_date"].strip(),
                             "effective_date": r["effective_date"].strip(), "ticker": tk, "name": name,
                             "action": r["action"].strip().lower(), "source_url": src})
    rows.sort(key=lambda x: (x["announce_date"], x["index"], x["action"], x["name"]))
    REF.parent.mkdir(parents=True, exist_ok=True)
    with REF.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    (OUT / "build_log.json").write_text(json.dumps({"rows": len(rows), "unmapped": unmapped,
                                                     "conflicts": conflicts}, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    print(f"[build] rows={len(rows)} unmapped={len(unmapped)} conflicts={len(conflicts)}")
    for u in unmapped:
        print("  UNMAPPED", u)
    for c in conflicts:
        print("  CONFLICT", c)


# ── prices ─────────────────────────────────────────────────────────────────
def naver_hist(code: str) -> list:
    fp = PX / f"{code}.json"
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))
    u = (f"https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1"
         f"&startTime=20130101&endTime=20261029&timeframe=day")
    out = []
    for attempt in range(3):
        try:
            s = _get(u, 30).decode("utf-8", "ignore")
            for m in re.finditer(r'\["(\d{8})",\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)', s):
                d = m.group(1)
                out.append([f"{d[:4]}-{d[4:6]}-{d[6:]}", float(m.group(2)), float(m.group(5)), float(m.group(6))])
            break
        except Exception:
            time.sleep(2 * (attempt + 1))
    time.sleep(0.25)
    fp.write_text(json.dumps(out), encoding="utf-8")
    return out


def universe() -> list[str]:
    raw = json.loads(YF_CACHE.read_text(encoding="utf-8"))
    tks = set(raw)
    del raw
    if REF.exists():
        with REF.open(encoding="utf-8") as fh:
            tks |= {r["ticker"] for r in csv.DictReader(fh) if len(r["ticker"]) == 6}
    tks |= {"069500", "229200"}
    return sorted(tks)


def cmd_prices() -> None:
    PX.mkdir(parents=True, exist_ok=True)
    tks = universe()
    t0 = time.time()
    for i, t in enumerate(tks):
        naver_hist(t)
        if i % 100 == 0:
            print(f"[prices] {i}/{len(tks)} {time.time() - t0:.0f}s", flush=True)
    print(f"[prices] done {len(tks)}")


# ── Book (kr_event_20260930 규칙 재사용) ───────────────────────────────────
class Book:
    def __init__(self, bars: dict[str, list], calendar: list[str]):
        self.cal = calendar
        self.ix = {d: i for i, d in enumerate(calendar)}
        self.tk = sorted(bars)
        self.col = {t: j for j, t in enumerate(self.tk)}
        T, N = len(calendar), len(self.tk)
        self.o = np.full((T, N), np.nan)
        self.c = np.full((T, N), np.nan)
        self.a = np.full((T, N), np.nan)
        for t, rows in bars.items():
            j = self.col[t]
            for d, o, c, v in rows:
                i = self.ix.get(d)
                if i is not None and c > 0:
                    self.o[i, j] = o if o > 0 else np.nan
                    self.c[i, j] = c
                    self.a[i, j] = c * v
        present = ~np.isnan(self.c)
        self.hist = np.cumsum(present, axis=0)
        a0 = np.nan_to_num(self.a)
        cs = np.cumsum(a0, axis=0)
        cn = np.cumsum(present, axis=0)
        s20 = cs - np.vstack([np.zeros((20, N)), cs[:-20]])
        n20 = cn - np.vstack([np.zeros((20, N)), cn[:-20]])
        with np.errstate(invalid="ignore", divide="ignore"):
            self.dv20 = np.where(n20 >= 15, s20 / np.maximum(n20, 1), np.nan)
        prev = np.full((T, N), np.nan)
        last = np.full(N, np.nan)
        for i in range(T):
            prev[i] = last
            last = np.where(present[i], self.c[i], last)
        with np.errstate(invalid="ignore", divide="ignore"):
            bad = (np.abs(self.o / prev - 1) > GUARD) | (np.abs(self.c / prev - 1) > GUARD)
        self.badcum = np.cumsum(np.nan_to_num(bad.astype(float)), axis=0)
        self._bench: dict = {}

    def fwd(self, j, e: int, h: int):
        x = e + h - 1
        if x >= len(self.cal) or e < 1 or h < 1:
            return np.nan if np.isscalar(j) else np.full(len(j), np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            r = (self.c[x, j] / self.o[e, j] - 1.0) * 100.0
        badw = (self.badcum[x, j] - self.badcum[e - 1, j]) > 0
        return np.where(badw, np.nan, r)

    def eligible(self, s: int, e: int):
        with np.errstate(invalid="ignore"):
            return (self.c[s] >= PX_MIN) & (self.dv20[s] >= DVOL_MIN) & (self.hist[s] >= MIN_HIST) & (self.o[e] > 0)

    def bench(self, s: int, e: int, h: int):
        key = (s, e, h)
        if key in self._bench:
            return self._bench[key]
        idx = np.where(self.eligible(s, e))[0]
        res = (None, None, 0)
        if len(idx) >= 30 and e + h - 1 < len(self.cal):
            dv = self.dv20[s, idx]
            r = self.fwd(idx, e, h)
            order = np.argsort(dv)
            edges = [dv[order[int(len(idx) * k / 10)]] for k in range(1, 10)]
            dec = np.searchsorted(edges, dv, side="right")
            means = []
            for k in range(10):
                rr = r[dec == k]
                rr = rr[~np.isnan(rr)]
                means.append(float(rr.mean()) if len(rr) >= 3 else np.nan)
            res = (edges, means, len(idx))
        self._bench[key] = res
        return res

    def size_bench(self, s: int, e: int, h: int, dv: float):
        edges, means, n = self.bench(s, e, h)
        if edges is None:
            return float("nan"), None
        k = int(np.searchsorted(edges, dv, side="right")) if dv == dv else 0
        return means[k], k


def load_book() -> Book:
    bars = {}
    for fp in PX.glob("*.json"):
        b = json.loads(fp.read_text(encoding="utf-8"))
        if len(b) > 30:
            bars[fp.stem] = [tuple(x) for x in b]
    cal = [r[0] for r in bars["069500"]]
    return Book(bars, cal)


def tstat(y: list[float], lag: int = 0) -> tuple[float, float, int]:
    a = np.array([v for v in y if v == v], float)
    n = len(a)
    if n < 3:
        return (float(a.mean()) if n else float("nan"), float("nan"), n)
    m = a.mean()
    u = a - m
    v = (u @ u) / (n - 1) if lag == 0 else (u @ u) / n
    if lag:
        for l in range(1, min(lag, n - 1) + 1):
            v += 2 * (1 - l / (lag + 1)) * (u[l:] @ u[:-l]) / n
    return float(m), float(m / math.sqrt(max(v, 1e-12) / n)), n


# ── test ───────────────────────────────────────────────────────────────────
def cmd_test() -> None:
    import bisect
    with REF.open(encoding="utf-8") as fh:
        ev = list(csv.DictReader(fh))
    bk = load_book()
    cal = bk.cal
    etf = {"KOSPI200": "069500", "KOSDAQ150": "229200"}
    rows = []
    for r in ev:
        tk, ad, ed = r["ticker"], r["announce_date"], r["effective_date"]
        s = bisect.bisect_right(cal, ad) - 1
        e = s + 1
        x = bisect.bisect_left(cal, ed) - 1
        out = {**r, "s": cal[s] if s >= 0 else None, "entry": cal[e] if 0 <= e < len(cal) else None,
               "exit": cal[x] if 0 <= x < len(cal) else None, "status": "ok"}
        h = x - e + 1
        out["h"] = h
        if s < 0 or e >= len(cal) or h < 1:
            out["status"] = "window"
        elif tk not in bk.col:
            out["status"] = "no_price" if tk else "unmapped"
        elif bk.hist[s, bk.col[tk]] == 0:
            out["status"] = "listed_after_signal"     # 매핑 오류 의심(분할 신설 코드 등) 또는 신규상장 특례
        if out["status"] == "ok":
            j = bk.col[tk]
            raw = float(bk.fwd(j, e, h))
            dv = float(bk.dv20[s, j])
            sb, dec = bk.size_bench(s, e, h, dv)
            ib = float(bk.fwd(bk.col[etf[r["index"]]], e, h)) if etf[r["index"]] in bk.col else float("nan")
            out.update(raw=raw, dv20=dv, decile=dec, size_bench=sb, idx_bench=ib,
                       ex_size=raw - COST - sb, ex_idx=raw - COST - ib)
            if raw != raw:
                out["status"] = "guard_or_missing_bar"
            # 보조: 효력일 시가 → H20
            ee = x + 1
            if ee + 19 < len(cal):
                r20 = float(bk.fwd(j, ee, 20))
                sb20, _ = bk.size_bench(ee - 1, ee, 20, float(bk.dv20[ee - 1, j]))
                out["post20_ex"] = r20 - COST - sb20
        rows.append(out)
    # 편출 보조 H20
    for out in rows:
        if out["action"] == "delete" and out["ticker"] in bk.col and out.get("exit"):
            x = cal.index(out["exit"])
            ee = x + 1
            if ee + 19 < len(cal):
                j = bk.col[out["ticker"]]
                r20 = float(bk.fwd(j, ee, 20))
                sb20, _ = bk.size_bench(ee - 1, ee, 20, float(bk.dv20[ee - 1, j]))
                out["post20_ex"] = r20 - COST - sb20

    adds = [r for r in rows if r["action"] == "add"]
    ok = [r for r in adds if r["status"] == "ok" and r.get("ex_size") == r.get("ex_size")]
    # 클러스터 = 발표일
    def make_clusters(okrows):
        by = defaultdict(list)
        for r in okrows:
            by[r["announce_date"]].append(r)
        cl = []
        for ad in sorted(by):
            g = by[ad]
            es = [r["ex_size"] for r in g if r["ex_size"] == r["ex_size"]]
            ei = [r["ex_idx"] for r in g if r["ex_idx"] == r["ex_idx"]]
            cl.append({"announce_date": ad, "n": len(g), "indices": sorted({r["index"] for r in g}),
                       "h": g[0]["h"], "ex_size": float(np.mean(es)) if es else float("nan"),
                       "ex_idx": float(np.mean(ei)) if ei else float("nan"),
                       "raw_net": float(np.mean([r["raw"] - COST for r in g]))})
        return cl
    clusters = make_clusters(ok)
    # 민감도(판정 밖): 정기변경 발표에 포함된 신규상장 특례 편입 제외
    SPECIAL = {"352820", "293490", "302440"}   # 빅히트·카카오게임즈(2020-11), SK바이오사이언스(2021-05)
    clusters_nospecial = make_clusters([r for r in ok if r["ticker"] not in SPECIAL])
    all_clusters = sorted({r["announce_date"] for r in ev})
    n14 = sum(1 for d in all_clusters if d >= "2014-01-01")
    n14_tested = sum(1 for c in clusters if c["announce_date"] >= "2014-01-01")

    def summ(cl, key):
        ys = [c[key] for c in cl]
        m, t0, n = tstat(ys, 0)
        _, t1, _ = tstat(ys, 1)
        is_ = [c[key] for c in cl if c["announce_date"] < "2020-01-01"]
        oos = [c[key] for c in cl if c["announce_date"] >= "2020-01-01"]
        pos = sum(1 for v in ys if v > 0)
        mi, ti, ni = tstat(is_, 0)
        mo, to, no = tstat(oos, 0)
        return {"mean": m, "t": t0, "t_nw1": t1, "n": n, "pos": pos,
                "IS": {"mean": mi, "t": ti, "n": ni}, "OOS": {"mean": mo, "t": to, "n": no},
                "median": float(np.median(ys)) if ys else float("nan"),
                "ex_best_dropped_mean": float(np.mean(sorted(ys)[:-1])) if len(ys) > 2 else float("nan")}

    res = {"clusters_total": len(all_clusters), "clusters_2014plus": n14, "clusters_2014plus_tested": n14_tested,
           "events_add": len(adds), "events_add_ok": len(ok),
           "status_counts": {k: sum(1 for r in adds if r["status"] == k) for k in {r["status"] for r in adds}},
           "size": summ(clusters, "ex_size"), "idx": summ(clusters, "ex_idx"), "raw": summ(clusters, "raw_net"),
           "size_no_special": summ(clusters_nospecial, "ex_size"),
           "size_2014plus": summ([c for c in clusters if c["announce_date"] >= "2014-01-01"], "ex_size"),
           "not_ok_adds": [f"{r['announce_date']} {r['index']} {r['name']} {r['ticker']} {r['status']}"
                           for r in adds if r["status"] != "ok"]}
    for idxname in ("KOSPI200", "KOSDAQ150"):
        sub = defaultdict(list)
        for r in ok:
            if r["index"] == idxname:
                sub[r["announce_date"]].append(r["ex_size"])
        res[f"size_{idxname}"] = summ([{"announce_date": d, "ex_size": float(np.mean(v))} for d, v in sorted(sub.items())],
                                     "ex_size")
    # 연도별
    yr = defaultdict(list)
    for c in clusters:
        yr[c["announce_date"][:4]].append(c["ex_size"])
    res["by_year"] = {y: float(np.mean(v)) for y, v in sorted(yr.items())}
    # 보조 H20
    for act in ("add", "delete"):
        v = [r["post20_ex"] for r in rows if r["action"] == act and r.get("post20_ex") == r.get("post20_ex")
             and r.get("post20_ex") is not None]
        res[f"post20_{act}"] = {"mean": float(np.mean(v)) if v else None, "n": len(v)}
    s = res["size"]
    if n14 < MIN_CLUSTERS:
        verdict = "검정 불가"
    elif s["t"] >= T_PASS and (s["IS"]["mean"] > 0) == (s["OOS"]["mean"] > 0) and s["pos"] * 2 > s["n"]:
        verdict = "통과"
    else:
        verdict = "기각"
    res["verdict"] = verdict
    res["clusters"] = clusters
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT / "events.csv").open("w", encoding="utf-8", newline="") as fh:
        keys = ["index", "announce_date", "effective_date", "ticker", "name", "action", "s", "entry", "exit", "h",
                "status", "raw", "dv20", "decile", "size_bench", "idx_bench", "ex_size", "ex_idx", "post20_ex"]
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(json.dumps({k: v for k, v in res.items() if k != "clusters"}, ensure_ascii=False, indent=1))
    for c in clusters:
        print(f"  {c['announce_date']} {'+'.join(c['indices']):20s} n={c['n']:2d} h={c['h']:2d} "
              f"ex_size={c['ex_size']:+6.2f} ex_idx={c['ex_idx']:+6.2f} raw_net={c['raw_net']:+6.2f}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "names":
        cmd_names()
    elif cmd == "build":
        cmd_build(sys.argv[2:])
    elif cmd == "prices":
        cmd_prices()
    elif cmd == "test":
        cmd_test()
    else:
        print(__doc__)
