#!/usr/bin/env python3
"""K1 백필 — 국민연금 5% 보고서(약식) 원문 파싱으로 2021-01 ~ 2024-06 지분 증감 복원 (read-only, 2026-09-29).

배경: DART majorstock API는 최근 ~2년만 돌려준다 → instiwatch signals는 2024-07부터. 원문(document.xml)의
ACODE 필드(SUM_BMT_RT 직전 비율, SUM_TMT_RT 이번 비율, SUM_CHN_RWN 보고사유, CRP_CD 종목코드)를 읽는다.

단계
  list   : 2021-01-01 ~ 2021-06-10 D001 목록(DART list API, flr_nm '국민연금' 필터). 2021-06-11 이후는
           instiwatch disclosures(읽기 전용)에 이미 전량 있다.
  docs   : 대상 rcept_no 원문 zip → backfill/docs/{rcept_no}.xml 캐시(0.6초 간격, 재실행 시 스킵).
           대상 = 2021-01 ~ 2024-06 국민연금 공시 전량 + 검증용 2024-07 이후 400건(균등 추출).
  parse  : backfill/nps_events.csv (rcept_no, signal_date, stock_code, before, after, change, reason, kind).
           kind 규칙은 instiwatch classify_major와 동일: 보고사유/유형에 '신규' → new, after<5 & change<0 → exit,
           change ≥ 1.0 → increase, ≤ −1.0 → decrease, 그 외 noise.
  validate: 2024-07 이후 겹침에서 instiwatch signals(국민연금, superseded=0)와 비교해 일치율 보고.
DART 키는 .env.live DART_API_KEY를 읽기만 한다(출력 금지).
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import re
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
BF = ROOT / "data" / "analysis" / "k1_smartmoney_20260929" / "backfill"
DOCS = BF / "docs"
DOCS.mkdir(parents=True, exist_ok=True)
DB = "file:E:/code/instiwatch/data/instiwatch.db?mode=ro"
API = "https://opendart.fss.or.kr/api"


def key():
    for line in (ROOT / ".env.live").read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("DART_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("no key")


def cmd_list():
    out = BF / "list_2021h1.json"
    if out.exists():
        return
    K, rows = key(), []
    for bg, ed in (("20210101", "20210331"), ("20210401", "20210610")):
        page = 1
        while True:
            r = requests.get(f"{API}/list.json", params=dict(crtfc_key=K, bgn_de=bg, end_de=ed, pblntf_detail_ty="D001",
                                                          page_no=page, page_count=100), timeout=30).json()
            if r.get("status") != "000":
                print("list status", r.get("status"), r.get("message"), file=sys.stderr)
                break
            rows += [x for x in r["list"] if "국민연금" in x.get("flr_nm", "")]
            if page >= int(r["total_page"]):
                break
            page += 1
            time.sleep(0.3)
        print(bg, "pages", page, "nps", len(rows), file=sys.stderr)
    json.dump(rows, out.open("w", encoding="utf-8"), ensure_ascii=False)


def targets():
    t = {}
    for x in json.load((BF / "list_2021h1.json").open(encoding="utf-8")):
        if x.get("stock_code") and "정정" not in x["report_nm"]:
            t[x["rcept_no"]] = (x["rcept_dt"], x["stock_code"], "bf")
    with contextlib.closing(sqlite3.connect(DB, uri=True, timeout=10)) as c:
        rows = c.execute("SELECT rcept_no, rcept_dt, stock_code FROM disclosures WHERE detail_ty='D001' "
                         "AND flr_nm LIKE '%국민연금%' AND report_nm NOT LIKE '%정정%' AND stock_code!='' "
                         "ORDER BY rcept_dt").fetchall()
    late = [r for r in rows if r[1] >= "20240701"]
    for r in rows:
        if r[1] < "20240701":
            t[r[0]] = (r[1], r[2], "bf")
    step = max(1, len(late) // 400)
    for r in late[::step]:
        t[r[0]] = (r[1], r[2], "val")
    return t


def cmd_docs():
    K, t = key(), targets()
    todo = [rn for rn in sorted(t) if not (DOCS / f"{rn}.xml").exists()]
    print("targets", len(t), "todo", len(todo), file=sys.stderr)
    fails = 0
    for i, rn in enumerate(todo, 1):
        r = None
        try:
            r = requests.get(f"{API}/document.xml", params=dict(crtfc_key=K, rcept_no=rn), timeout=30)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            (DOCS / f"{rn}.xml").write_bytes(z.read(sorted(z.namelist())[0]))
        except Exception as e:  # noqa: BLE001
            fails += 1
            if r is not None and r.content[:1] == b"{":
                print("api", r.text[:200], file=sys.stderr)
                if '"020"' in r.text:  # 한도 초과 → 중단
                    break
        if i % 200 == 0:
            print(f"docs {i}/{len(todo)} fails {fails}", file=sys.stderr)
        time.sleep(0.6)
    print("done fails", fails, file=sys.stderr)


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def parse_doc(raw: bytes):
    try:
        t = raw.decode("utf-8")
    except UnicodeDecodeError:
        t = raw.decode("cp949", "ignore")
    g = {}
    for code in ("SUM_BMT_RT", "SUM_TMT_RT", "SUM_CHN_RWN", "CRP_CD", "BFR_STK_RT", "HLD_STK_RT", "CHN_RSN", "FLT_STK_CNT"):
        m = re.search(rf'ACODE="{code}"[^>]*>([^<]*)<', t)
        g[code] = m.group(1).strip() if m else None
    name = re.search(r"<DOCUMENT-NAME[^>]*>([^<]*)<", t)
    return g, (name.group(1) if name else "")


def classify(before, after, reason, docname):
    change = (after - before) if (after is not None and before is not None) else None
    if "신규" in (reason or ""):
        return "new", change
    if change is None:
        if after is not None and after < 5.0:  # 직전 비율 칸이 빈 약식 보고서 — 5% 미만이면 이탈뿐 (겹침 검증에서 확인)
            return "exit", None
        return "unparsed", None
    if after < 5.0 and change < 0:
        return "exit", change
    if change >= 1.0:
        return "increase", change
    if change <= -1.0:
        return "decrease", change
    return "noise", change


def cmd_parse():
    t = targets()
    out = []
    for rn, (d, sc, grp) in sorted(t.items()):
        p = DOCS / f"{rn}.xml"
        if not p.exists():
            continue
        g, dn = parse_doc(p.read_bytes())
        # 서식 2종: 요약표(SUM_BMT_RT/SUM_TMT_RT)가 비면 본문 표(BFR_STK_RT/HLD_STK_RT)를 쓴다 (겹침 검증에서 발견)
        b = _f(g["SUM_BMT_RT"] or "")
        b = b if b is not None else _f(g["BFR_STK_RT"] or "")
        a = _f(g["SUM_TMT_RT"] or "")
        a = a if a is not None else _f(g["HLD_STK_RT"] or "")
        kind, ch = classify(b, a, (g["SUM_CHN_RWN"] or "") + " " + (g["CHN_RSN"] or ""), dn)
        out.append(dict(rcept_no=rn, signal_date=d, stock_code=sc, crp_cd=g["CRP_CD"], before=b, after=a,
                        change=None if ch is None else round(ch, 2), reason=g["SUM_CHN_RWN"], kind=kind, grp=grp))
    with (BF / "nps_events.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    from collections import Counter
    print(Counter((o["grp"], o["signal_date"][:4], o["kind"]) for o in out))


def cmd_validate():
    ev = {r["rcept_no"]: r for r in csv.DictReader((BF / "nps_events.csv").open(encoding="utf-8")) if r["grp"] == "val"}
    with contextlib.closing(sqlite3.connect(DB, uri=True, timeout=10)) as c:
        sig = {r[0]: r[1:] for r in c.execute(
            "SELECT rcept_no, kind, ratio, ratio_change FROM signals WHERE superseded=0 AND institution LIKE '%국민연금%'")}
    n = kind_ok = ratio_ok = noise_ok = noise_n = 0
    mism = []
    for rn, e in ev.items():
        if rn in sig:
            n += 1
            k, ratio, rch = sig[rn]
            kind_ok += e["kind"] == k
            ratio_ok += e["after"] not in ("", None) and abs(float(e["after"]) - (ratio or -9)) < 0.015
            if e["kind"] != k:
                mism.append((rn, e["kind"], k, e["before"], e["after"], rch, e["reason"]))
        else:
            noise_n += 1
            noise_ok += e["kind"] in ("noise",)
    res = dict(val_docs=len(ev), in_signals=n, kind_match=kind_ok, after_ratio_match=ratio_ok,
               not_in_signals=noise_n, of_which_parsed_noise=noise_ok, mismatches=mism[:30])
    json.dump(res, (BF / "validation.json").open("w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "mismatches"}, ensure_ascii=False), mism[:15])


# =====================================================================================================
RETEST_PREREG = """
## 재검정 사전등록 (백필 파싱 결과를 보기 전, 수익률 계산 전에 고정 — 2026-09-29)

규칙 R0/R1/R2는 원 사전등록(research_k1_smartmoney_20260929.py docstring) 그대로. 바뀌는 것은 표본과 데이터 소스뿐이다.
- NPS 이벤트: 2024-06-30 이전 = 원문 파싱 백필(kind new/increase/decrease/exit, noise·unparsed 제외),
  2024-07-01 이후 = instiwatch signals(국민연금 이름, superseded=0). 겹침 검증 일치율을 먼저 보고한다.
- 시험기간: T = 2022-01(백필 시작 2021-01 + 12개월) ~ T+60 ≤ 벤치 최신 봉.
- 구간: OOS(백필) T ≤ 2024-06 / 교량 2024-07 ~ 2025-06 / 원 관측(IS) 2025-07 ~ 2026-06.
  OOS 판정 = 원 관측과 같은 부호 + 규모매칭 대비 NW t ≥ 2.0.
- 가격: yfinance auto_adjust 캐시(data/price_backfill_yf/kr_fallen_price_cache_2018_2026.json 1,746종목 →
  T1-KR prices/ → K1 yf_prices 순). 현재 상장 종목만 → 상폐 누락(생존편향, 절대수익 상한).
  규모매칭 유니버스 = 이 캐시 합집합(현재 상장 기준).
- 벤치: KOSPI200(069500)·KOSDAQ150(229200) voltarget 캐시, 규모매칭(dvol20 십분위 EW), NPS 보유 유니버스(진단).
- 변형 EV(원 사전등록 다음 판 ②에서 등록): 공시 다음 거래일 종가 진입, 60세션 보유, 같은 적격·비용.
  단위 = 진입일 클러스터 평균, NW lag 3. EV0 = R0 조건, EV1 = R1 조건(진입일 전일 위치 ≤ 35).
- 견고성: R0/EV0 상위 기여 3종목(픽 원수익 합 기준) 제외 재계산.
- 게이트: 원 게이트(규모매칭 AND KOSPI200 NW t ≥ 2.5, 양 반기 +) + OOS 부호 유지. 등급: 확정/후보/기각.
"""


def cmd_retest():
    import datetime as dt
    import math
    import statistics as st
    from bisect import bisect_right
    from collections import defaultdict
    sys.path.insert(0, str(ROOT / "tools" / "research"))
    from research_k1_smartmoney_20260929 import nw_t

    OUT = BF.parent
    H, COST = 60, 0.25
    ev = []
    for r in csv.DictReader((BF / "nps_events.csv").open(encoding="utf-8")):
        if r["grp"] == "bf" and r["kind"] in ("new", "increase", "decrease", "exit") and r["signal_date"] < "20240701":
            ev.append((r["signal_date"], r["stock_code"], r["kind"]))
    with contextlib.closing(sqlite3.connect(DB, uri=True, timeout=10)) as c:
        for d, sc, k in c.execute("SELECT signal_date, stock_code, kind FROM signals WHERE superseded=0 "
                                  "AND institution LIKE '%국민연금%' AND signal_date >= '20240701' AND stock_code!=''"):
            ev.append((d, sc, k))
    ev.sort()
    nps_by = defaultdict(list)
    for d, sc, k in ev:
        nps_by[sc].append((d, k))

    px = {}
    cache = json.load((ROOT / "data/price_backfill_yf/kr_fallen_price_cache_2018_2026.json").open())
    for code, rows in cache.items():
        if rows:
            px[code] = ([x["d"] for x in rows], [x["c"] for x in rows], [x["v"] for x in rows])
    del cache
    for p in (ROOT / "data/analysis/model_T1kr_earnings_20260928/prices").glob("*.csv"):
        if p.stem in px:
            continue
        ds, cs, vs = [], [], []
        for r in csv.DictReader(p.open(encoding="utf-8")):
            try:
                cl, vo = float(r["close"]), float(r["volume"])
            except (ValueError, TypeError):
                continue
            if cl > 0:
                ds.append(r["date"][:10]); cs.append(cl); vs.append(vo)
        if ds:
            px[p.stem] = (ds, cs, vs)
    extra = defaultdict(lambda: ([], [], []))
    for r in csv.DictReader((OUT / "yf_prices.csv").open(encoding="utf-8")):
        if r["code"] not in px:
            e = extra[r["code"]]
            e[0].append(r["date"]); e[1].append(float(r["close"])); e[2].append(float(r["volume"]))
    px.update(extra)
    idx = {c: {d: i for i, d in enumerate(v[0])} for c, v in px.items()}

    def bench(f):
        rows = list(csv.DictReader((ROOT / "data/analysis/voltarget_20260924" / f).open(encoding="utf-8")))
        return {r["Date"][:10]: float(r["Close"]) for r in rows if r["Close"]}
    ks, kq = bench("069500.KS.csv"), bench("229200.KS.csv")
    cal = sorted(ks)
    ci = {d: i for i, d in enumerate(cal)}

    def elig_dv(code, dprev):
        ds, cs, vs = px[code]
        i = bisect_right(ds, dprev)
        if i < 20 or cs[i - 1] < 1000:
            return None
        dv = sum(cs[j] * vs[j] for j in range(i - 20, i)) / 20
        return dv if dv >= 1e8 else None

    def pos1y(code, dprev):
        ds, cs, _ = px[code]
        i = bisect_right(ds, dprev)
        lo = (dt.date.fromisoformat(dprev) - dt.timedelta(days=365)).isoformat()
        j = bisect_right(ds, lo)
        w = cs[j:i]
        if len(w) < 200:
            return None
        mn, mx = min(w), max(w)
        return 100 * (w[-1] - mn) / (mx - mn) if mx > mn else None

    def fwd(code, ti):
        T, E = cal[ti], cal[ti + H]
        k = idx[code].get(T)
        if k is None:
            return None
        ds, cs, _ = px[code]
        e = bisect_right(ds, E) - 1
        if e <= k or ds[e] < cal[ti + H - 5]:
            return None
        for j in range(k + 1, e + 1):
            if abs(cs[j] / cs[j - 1] - 1) > 0.35:
                return None
        return 100 * (cs[e] / cs[k] - 1)

    ucache = {}

    def universe(ti):
        if ti in ucache:
            return ucache[ti]
        T, Tp = cal[ti], cal[ti - 1]
        el = {}
        for code in px:
            if code in ("069500", "229200") or T not in idx[code]:
                continue
            dv = elig_dv(code, Tp)
            if dv:
                el[code] = dv
        order = sorted(el, key=el.get)
        dec = {c: min(9, i * 10 // len(order)) for i, c in enumerate(order)}
        ret = {c: fwd(c, ti) for c in order}
        dm = defaultdict(list)
        for c, r in ret.items():
            if r is not None:
                dm[dec[c]].append(r)
        ucache[ti] = (dec, ret, {k: st.mean(v) for k, v in dm.items()})
        return ucache[ti]

    def held(T8):
        out = []
        for code, lst in nps_by.items():
            prior = [x for x in lst if x[0] < T8]
            if prior and prior[-1][1] != "exit":
                out.append(code)
        return out

    def score(picks, ti):
        dec, ret, dm = universe(ti)
        rs, sb = [], []
        for c in picks:
            r = ret.get(c)
            if r is None:
                continue
            rs.append(r - COST)
            sb.append(dm[dec[c]])
        if not rs:
            return None
        T, E = cal[ti], cal[ti + H]
        hr = [ret[c] for c in held(T.replace("-", "")) if ret.get(c) is not None]
        p = st.mean(rs)
        return dict(T=T, n=len(rs), ret=p, x_size=p - st.mean(sb), x_k200=p - 100 * (ks[E] / ks[T] - 1),
                    x_kq150=p - 100 * (kq[E] / kq[T] - 1), x_held=p - (st.mean(hr) if hr else float("nan")),
                    picks=[(c, ret[c]) for c in picks if ret.get(c) is not None])

    months = sorted({d[:7] for d in cal})
    rebal = [ci[next(d for d in cal if d[:7] == m)] for m in months]
    rebal = [ti for ti in rebal if cal[ti] >= "2022-01-01" and ti + H < len(cal)]
    res = defaultdict(list)
    for ti in rebal:
        T, Tp = cal[ti], cal[ti - 1]
        T8 = T.replace("-", "")
        lo90 = (dt.date.fromisoformat(T) - dt.timedelta(days=90)).strftime("%Y%m%d")
        dec, ret, dm = universe(ti)
        picks = {"R0": [], "R1": [], "R2": []}
        for code, lst in nps_by.items():
            if code not in dec:
                continue
            ups = [x for x in lst if x[1] in ("new", "increase") and lo90 <= x[0] < T8]
            if not ups:
                continue
            picks["R0"].append(code)
            p = pos1y(code, Tp)
            if p is None or p > 35:
                continue
            picks["R1"].append(code)
            prior = [x for x in lst if x[0] < ups[-1][0]]
            if prior and prior[-1][1] in ("decrease", "exit"):
                picks["R2"].append(code)
        for rule, pk in picks.items():
            s = score(pk, ti)
            if s:
                res[rule].append(s)
        print(T, {k: len(v) for k, v in picks.items()}, "univ", len(dec), file=sys.stderr)

    by_entry = defaultdict(lambda: {"EV0": [], "EV1": []})
    for d, code, k in ev:
        if k not in ("new", "increase") or code not in px:
            continue
        dd = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        ti = bisect_right(cal, dd)
        if ti + H >= len(cal) or cal[ti] < "2022-01-01":
            continue
        dec, ret, dm = universe(ti)
        if code not in dec:
            continue
        by_entry[ti]["EV0"].append(code)
        p = pos1y(code, cal[ti - 1])
        if p is not None and p <= 35:
            by_entry[ti]["EV1"].append(code)
    for ti in sorted(by_entry):
        for rule in ("EV0", "EV1"):
            s = score(sorted(set(by_entry[ti][rule])), ti)
            if s:
                res[rule].append(s)

    L = []

    def stats(cs, key, lag):
        x = [c[key] for c in cs if not math.isnan(c[key])]
        if not x:
            return "-"
        h = len(x) // 2
        return (f"{st.mean(x):+.2f} (t {nw_t(x, lag):+.2f}, {sum(v > 0 for v in x)}/{len(x)}, "
                f"반기 {st.mean(x[:h]) if h else float('nan'):+.2f}/{st.mean(x[h:]):+.2f})")
    segs = [("전체", "0000", "9999"), ("OOS 백필 ≤2024-06", "0000", "2024-07"),
            ("교량 2024-07~2025-06", "2024-07", "2025-07"), ("IS 2025-07~2026-06", "2025-07", "9999")]
    for rule in ("R0", "R1", "R2", "EV0", "EV1"):
        lag = 3 if rule.startswith("EV") else 2
        L.append(f"\n#### {rule}")
        L.append("| 구간 | 코호트 | 평균 종목수 | 포트 원수익 | vs 규모매칭 | vs KOSPI200 | vs KOSDAQ150 | vs NPS 보유 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for lab, a, b in segs:
            cs = [c for c in res[rule] if a <= c["T"] < b]
            if not cs:
                L.append(f"| {lab} | 0 | | | | | | |")
                continue
            L.append(f"| {lab} | {len(cs)} | {st.mean(c['n'] for c in cs):.1f} | {st.mean(c['ret'] for c in cs):+.2f} | "
                     f"{stats(cs, 'x_size', lag)} | {stats(cs, 'x_k200', lag)} | {stats(cs, 'x_kq150', lag)} | "
                     f"{stats(cs, 'x_held', lag)} |")
        by = defaultdict(list)
        for c in res[rule]:
            by[c["T"][:4]].append(c["x_size"])
        L.append("- 연도별 규모매칭 초과: " + ", ".join(f"{y} {st.mean(v):+.2f}({len(v)})" for y, v in sorted(by.items())))
    for rule in ("R0", "EV0"):
        contrib = defaultdict(float)
        for c in res[rule]:
            for code, r in c["picks"]:
                contrib[code] += r
        top3 = sorted(contrib, key=contrib.get, reverse=True)[:3]
        rows = []
        for c in res[rule]:
            s = score([code for code, _ in c["picks"] if code not in top3], ci[c["T"]])
            if s:
                rows.append(s)
        lag = 3 if rule.startswith("EV") else 2
        for lab, a, b in segs:
            cs = [c for c in rows if a <= c["T"] < b]
            if cs:
                L.append(f"- {rule} 상위3({','.join(top3)}) 제외 {lab}: 규모매칭 {stats(cs, 'x_size', lag)} / "
                         f"NPS보유 {stats(cs, 'x_held', lag)} / KOSPI200 {stats(cs, 'x_k200', lag)}")
    miss = {sc for _, sc, _ in ev if sc not in px}
    L.append(f"\n- NPS 이벤트 {len(ev)}건(백필 {sum(1 for e in ev if e[0] < '20240701')}), 종목 {len({e[1] for e in ev})}, "
             f"가격 없음(상폐·미수집) {len(miss)}종목 → 제외. 유니버스 가격 종목 {len(px)}.")
    txt = "\n".join(L)
    (OUT / "retest_summary.md").write_text(txt, encoding="utf-8")
    json.dump({k: [{kk: vv for kk, vv in c.items() if kk != "picks"} for c in v] for k, v in res.items()},
              (OUT / "retest_results.json").open("w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(txt)


# =====================================================================================================
# 단계 3: tier1(비NPS)·tier2·tier3 백필 — instiwatch의 티어 매칭(config 화이트리스트 + fallback 패턴)을
# 읽기 전용으로 복제한다(연구 venv가 py3.9라 instiwatch 모듈을 import하지 않고 소스에서 패턴을 읽는다).
IW = Path("E:/code/instiwatch")


def iw_tiers():
    src = (IW / "instiwatch" / "classify.py").read_text(encoding="utf-8")
    fb = eval(re.search(r"FALLBACK_INSTITUTION_PATTERNS = (\{.*?\n\})", src, re.S).group(1))  # noqa: S307 (자체 소스 상수)
    txt = (IW / "config.yaml").read_text(encoding="utf-8").split("\ninstitutions:")[1]
    inst, cur = {}, None
    for line in txt.splitlines():
        m = re.match(r"\s+(tier\d):", line)
        if m:
            cur = m.group(1); inst[cur] = []; continue
        m = re.match(r'\s+-\s+"([^"]+)"', line)
        if m and cur:
            inst[cur].append(m.group(1))

    def match(name):
        for t in ("tier1", "tier2", "tier3"):
            if any(p in name for p in inst.get(t, [])):
                return t
        f = name.casefold()
        for t in ("tier1", "tier2", "tier3"):
            if any(p.casefold() in f for p in fb[t]):
                return t
        return None
    return match


def targets2():
    match = iw_tiers()
    with contextlib.closing(sqlite3.connect(DB, uri=True, timeout=10)) as c:
        rows = c.execute("SELECT rcept_no, rcept_dt, stock_code, flr_nm FROM disclosures WHERE detail_ty='D001' "
                         "AND report_nm NOT LIKE '%정정%' AND stock_code!='' AND flr_nm NOT LIKE '%국민연금%' "
                         "ORDER BY rcept_dt").fetchall()
    t, late = {}, []
    for rn, d, sc, f in rows:
        tier = match(f or "")
        if not tier:
            continue
        if d < "20240701":
            t[rn] = (d, sc, f, tier, "bf")
        else:
            late.append((rn, d, sc, f, tier))
    step = max(1, len(late) // 400)
    for rn, d, sc, f, tier in late[::step]:
        t[rn] = (d, sc, f, tier, "val")
    return t


def cmd_docs2():
    K, t = key(), targets2()
    todo = [rn for rn in sorted(t) if not (DOCS / f"{rn}.xml").exists()]
    print("targets2", len(t), "todo", len(todo), file=sys.stderr, flush=True)
    fails = 0
    for i, rn in enumerate(todo, 1):
        r = None
        try:
            r = requests.get(f"{API}/document.xml", params=dict(crtfc_key=K, rcept_no=rn), timeout=30)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            (DOCS / f"{rn}.xml").write_bytes(z.read(sorted(z.namelist())[0]))
        except Exception:  # noqa: BLE001
            fails += 1
            if r is not None and r.content[:1] == b"{":
                print("api", r.text[:200], file=sys.stderr, flush=True)
                if '"020"' in r.text:
                    break
        if i % 500 == 0:
            print(f"docs2 {i}/{len(todo)} fails {fails}", file=sys.stderr, flush=True)
        time.sleep(0.6)
    print("done2 fails", fails, file=sys.stderr, flush=True)


# =====================================================================================================
FALLEN_PREREG = """
## KR 급락 레인 × 국민연금 5% 보유 필터 (사전등록, 필터 적용 결과를 보기 전 고정 — 2026-09-29)

1) 재현 게이트: 저장소 엔진 `tools/research_kr_rank_pool_reconcile.build()` + `by_session()`을 장기 캐시
   (kr_fallen_price_cache_2018_2026.json)에 그대로 돌려 09-12 리포트(kr_pool_long_excess_20260912.md)의
   후보 2,957건 · 초과 평균 +1.79% · 날짜 t 0.03을 재현한다(허용: n 정확, 평균 ±0.01, t ±0.02 — 첫 실행에서 t 0.02로 나와 벤치 봉 가변을 사유로 허용오차를 명시)(069500 진입일 시가 → D5 종가, 엔진 규약 그대로).
   불일치면 중단한다. ⚠️ 엔진 계약은 E5 규약 TP12/SL25/D5다(라이브는 D7) — 재현 대상이 D5라 그대로 둔다.
2) 필터(PIT): 신호 세션 d 기준, d보다 앞선 날짜에 공시된 국민연금 5% 보고서가 있고 그 최신 보고서가 이탈(exit,
   보고 후 <5%)이 아니면 '보유'. 공시 목록 = 2021-01~06-10 DART list + instiwatch disclosures(2021-06-11~, 전량).
   kind는 파싱값(백필·검증분) 또는 instiwatch signals, 둘 다 없으면 noise(보유 유지 — 겹침 검증 123/124).
   좌측 절단(2021 이전부터 보유하고 공시가 없던 종목 누락)을 피하려 평가 세션은 2022-01-01 이후만.
3) 지표: 후보별 초과 = net − 벤치. 벤치 ① 069500(엔진 규약) ② 규모매칭 계약: 신호일 d의 적격 유니버스
   (장기 캐시, d 종가 ≥1,000원, d 거래대금 ≥20억, 이력 ≥25봉)를 dvol20 십분위로 나눠 같은 십분위 종목에
   같은 계약(d+1 시가 진입, TP12/SL25/D5, 비용 0.25)을 씌운 평균. t = 날짜 클러스터(일평균).
   비교: 전체 레인 / 보유 부분집합 / 비보유 부분집합, 그리고 보유 − 비보유 차이(날짜 쌍 일치일 기준).
4) 구간: OOS 2022-01~2024-06 / 이후 2024-07~. 판정: 보유 부분집합의 규모매칭 초과가 전체 레인보다 크고
   (차이 날짜 t ≥ 2.0) 두 구간 부호 유지 → 후보. 아니면 기각.
"""


def _contract(bars, i):
    """build()와 같은 정산 규칙(d+1 시가 진입, TP12/SL25/D5, 비용 0.25). bars = 캐시 dict 리스트."""
    after = bars[i + 1:i + 6]
    if len(after) < 5:
        return None
    e = after[0]["o"]
    if not e or e <= 0:
        return None
    tp, sl, cost = e * 1.12, e * 0.75, 0.25
    for k, bar in enumerate(after):
        if k > 0 and bar["o"] <= sl:
            return 100 * (bar["o"] / e - 1) - cost
        if k > 0 and bar["o"] >= tp:
            return 100 * (bar["o"] / e - 1) - cost
        if bar["l"] <= sl:
            return 100 * (sl / e - 1) - cost
        if bar["h"] >= tp:
            return 100 * (tp / e - 1) - cost
    return 100 * (after[-1]["c"] / e - 1) - cost


def cmd_fallen():
    import math
    import statistics as st
    from collections import defaultdict
    sys.path.insert(0, str(ROOT))
    from tools.research_benchmark_excess import bench_bars
    from tools.research_kr_rank_pool_reconcile import build, by_session
    from tools.kr_fallen_ranking_experiment import COST

    cache = json.loads((ROOT / "data/price_backfill_yf/kr_fallen_price_cache_2018_2026.json").read_text(encoding="utf-8"))
    sessions = by_session(build(cache))
    bars, bidx = bench_bars("kr", "069500"), None
    bidx = {b[0]: i for i, b in enumerate(bars)}

    def ex_k200(entry_date, net):
        i = bidx.get(entry_date)
        if i is None or i + 5 > len(bars):
            return None
        return net - (bars[i + 4][4] / bars[i][1] - 1) * 100

    recs = []
    for day, pool in sessions.items():
        for c in pool:
            x = ex_k200(c["entry_date"], c["net"])
            if x is not None:
                recs.append(dict(day=day, code=c["ticker"], net=c["net"], xk=x))

    def day_t(pairs):
        g = defaultdict(list)
        for d, v in pairs:
            g[d].append(v)
        m = [st.mean(v) for v in g.values()]
        if len(m) < 3 or st.stdev(m) == 0:
            return float("nan"), float("nan"), len(m)
        return st.mean(m), st.mean(m) / (st.stdev(m) / math.sqrt(len(m))), len(m)
    rep_n, rep_mean = len(recs), st.mean(r["xk"] for r in recs)
    from tools.research_kr_pool_long_excess import cluster_t   # 리포트와 같은 함수·같은 서식으로 대조
    rep_t = cluster_t([(r["day"], r["xk"]) for r in recs], lambda d: d)[0]
    # 허용오차: n 정확 일치, 평균 ±0.01%p, t ±0.02 (069500 벤치 봉은 가변 캐시 — 09-12 이후 소폭 개정 가능)
    ok = rep_n == 2957 and abs(rep_mean - 1.79) <= 0.01 and abs(rep_t - 0.03) <= 0.02
    print(f"재현 정밀값: 평균 {rep_mean:.4f} t {rep_t:.4f}")
    print(f"재현: n {rep_n} 초과 {rep_mean:+.2f} 날짜t {rep_t:+.2f} → {'통과' if ok else '불일치'} (COST {COST})")
    if not ok:
        raise SystemExit("재현 게이트 실패 — 중단")

    # 규모매칭 계약 벤치 (신호일만 계산)
    days = sorted({r["day"] for r in recs if r["day"] >= "2022-01-01"})
    pos = {code: {b["d"]: i for i, b in enumerate(bs)} for code, bs in cache.items() if bs}
    dec_of, dec_mean = {}, {}
    for d in days:
        el = []
        for code, bs in cache.items():
            i = pos.get(code, {}).get(d)
            if i is None or i < 25 or bs[i]["c"] < 1000 or bs[i]["amt"] < 20e8:
                continue
            dv = sum(x["amt"] for x in bs[i - 19:i + 1]) / 20
            r = _contract(bs, i)
            el.append((code, dv, r))
        el.sort(key=lambda x: x[1])
        acc = defaultdict(list)
        for j, (code, dv, r) in enumerate(el):
            dec = min(9, j * 10 // len(el))
            dec_of[(d, code)] = dec
            if r is not None:
                acc[dec].append(r)
        for k, v in acc.items():
            dec_mean[(d, k)] = st.mean(v) if len(v) >= 20 else None

    # NPS 보유 (PIT)
    filings = defaultdict(list)       # code -> [(date, rcept_no)]
    for x in json.load((BF / "list_2021h1.json").open(encoding="utf-8")):
        if x.get("stock_code"):
            filings[x["stock_code"]].append((x["rcept_dt"], x["rcept_no"]))
    kinds = {r["rcept_no"]: r["kind"] for r in csv.DictReader((BF / "nps_events.csv").open(encoding="utf-8"))}
    with contextlib.closing(sqlite3.connect(DB, uri=True, timeout=10)) as c:
        for rn, d, sc in c.execute("SELECT rcept_no, rcept_dt, stock_code FROM disclosures WHERE detail_ty='D001' "
                                   "AND flr_nm LIKE '%국민연금%' AND report_nm NOT LIKE '%정정%' AND stock_code!=''"):
            filings[sc].append((d, rn))
        for rn, k in c.execute("SELECT rcept_no, kind FROM signals WHERE superseded=0 AND institution LIKE '%국민연금%'"):
            kinds.setdefault(rn, k)
    for v in filings.values():
        v.sort()

    def held(code, day):
        d8 = day.replace("-", "")
        prior = [x for x in filings.get(code, []) if x[0] < d8]
        return bool(prior) and kinds.get(prior[-1][1], "noise") != "exit"

    rows = []
    for r in recs:
        if r["day"] < "2022-01-01":
            continue
        dec = dec_of.get((r["day"], r["code"]))
        sm = dec_mean.get((r["day"], dec)) if dec is not None else None
        if sm is None:
            continue
        rows.append(dict(r, xs=r["net"] - sm, held=held(r["code"], r["day"])))

    L = [f"- 재현 게이트 통과: 후보 {rep_n} · 069500 초과 {rep_mean:+.2f}% · 날짜 t {rep_t:+.2f} (09-12 리포트와 일치)",
         f"- 평가 표본(2022-01 이후, 규모매칭 벤치 가능) {len(rows)}건 · 보유 {sum(r['held'] for r in rows)}건 · "
         f"세션 {len({r['day'] for r in rows})}일", "",
         "| 구간 | 부분집합 | n | 세션 | 원 net | 069500 초과 (날짜 t) | 규모매칭 초과 (날짜 t) |", "|---|---|---|---|---|---|---|"]
    segs = [("전체 2022-01~", "0000", "9999"), ("OOS 2022-01~2024-06", "0000", "2024-07"), ("이후 2024-07~", "2024-07", "9999")]
    for lab, a, b in segs:
        seg = [r for r in rows if a <= r["day"] < b]
        for name, sub in (("레인 전체", seg), ("NPS 보유", [r for r in seg if r["held"]]),
                          ("비보유", [r for r in seg if not r["held"]])):
            if not sub:
                continue
            mk, tk, nd = day_t([(r["day"], r["xk"]) for r in sub])
            ms, ts, _ = day_t([(r["day"], r["xs"]) for r in sub])
            L.append(f"| {lab} | {name} | {len(sub)} | {nd} | {st.mean(r['net'] for r in sub):+.2f} | "
                     f"{mk:+.2f} ({tk:+.2f}) | {ms:+.2f} ({ts:+.2f}) |")
        # 보유 − 비보유 (둘 다 있는 날)
        g = defaultdict(lambda: ([], []))
        for r in seg:
            g[r["day"]][0 if r["held"] else 1].append(r["xs"])
        diffs = [(d, st.mean(h) - st.mean(n)) for d, (h, n) in g.items() if h and n]
        md, td, nd = day_t(diffs)
        L.append(f"| {lab} | 보유−비보유(같은 날) | | {nd} | | | {md:+.2f} ({td:+.2f}) |")
    txt = "\n".join(L)
    (BF.parent / "fallen_nps_filter.md").write_text(txt, encoding="utf-8")
    print(txt)


def _parse_one(p):
    g, dn = parse_doc(p.read_bytes())
    b = _f(g["SUM_BMT_RT"] or "")
    b = b if b is not None else _f(g["BFR_STK_RT"] or "")
    a = _f(g["SUM_TMT_RT"] or "")
    a = a if a is not None else _f(g["HLD_STK_RT"] or "")
    rsn = (g["SUM_CHN_RWN"] or "") + " " + (g["CHN_RSN"] or "")
    kind, ch = classify(b, a, rsn, dn)
    if kind == "unparsed" and b is None and a is not None and a >= 5.0:
        # 최초 5% 보고(직전 비율 없음): majorstock은 변동폭 = 보고 후 비율로 준다 → instiwatch는 '신규' 문구가
        # 없으면 increase로 분류한다(NPS 겹침 불일치 사례 20241007000138에서 확인). tier2/3 경로에만 적용(NPS 결과 동결).
        kind, ch = "increase", a
    return b, a, ch, kind, _f(g["FLT_STK_CNT"] or ""), g["SUM_CHN_RWN"] or g["CHN_RSN"]


def cmd_parse2():
    out = []
    for rn, (d, sc, flr, tier, grp) in sorted(targets2().items()):
        p = DOCS / f"{rn}.xml"
        if not p.exists():
            continue
        b, a, ch, kind, flt, rsn = _parse_one(p)
        out.append(dict(rcept_no=rn, signal_date=d, stock_code=sc, institution=flr, tier=tier, before=b, after=a,
                        change=None if ch is None else round(ch, 2), flt=flt, kind=kind, reason=rsn, grp=grp))
    with (BF / "inst_events.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    from collections import Counter
    print(Counter((o["grp"], o["tier"], o["kind"]) for o in out))


def cmd_validate2():
    ev = {r["rcept_no"]: r for r in csv.DictReader((BF / "inst_events.csv").open(encoding="utf-8")) if r["grp"] == "val"}
    with contextlib.closing(sqlite3.connect(DB, uri=True, timeout=10)) as c:
        sig = defaultdict_list()
        for rn, k, ratio, tier in c.execute("SELECT rcept_no, kind, ratio, tier FROM signals WHERE superseded=0 "
                                            "AND tier!='insider' AND institution NOT LIKE '%국민연금%'"):
            sig[rn].append((k, ratio, tier))
    from collections import Counter
    n = kind_ok = ratio_ok = noise_ok = noise_n = 0
    by_tier = Counter()
    mism = []
    for rn, e in ev.items():
        if rn in sig:
            n += 1
            k, ratio, tier = sig[rn][0]
            hit = e["kind"] == k
            kind_ok += hit
            by_tier[(e["tier"], hit)] += 1
            ratio_ok += e["after"] not in ("", None) and abs(float(e["after"]) - (ratio or -9)) < 0.015
            if not hit:
                mism.append((rn, e["institution"], e["kind"], k, e["before"], e["after"], e["reason"]))
        else:
            noise_n += 1
            noise_ok += e["kind"] == "noise"
    res = dict(val_docs=len(ev), in_signals=n, kind_match=kind_ok, after_ratio_match=ratio_ok,
               not_in_signals=noise_n, of_which_parsed_noise=noise_ok,
               by_tier={f"{t}_{h}": v for (t, h), v in by_tier.items()}, mismatches=mism[:40])
    json.dump(res, (BF / "validation2.json").open("w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "mismatches"}, ensure_ascii=False))
    for m in mism[:20]:
        print(m)


RETEST2_PREREG = """
## 재검정 2 사전등록 — R3 교집합 + instiwatch 후보 순위 재현(IW top8) (tier2/3 백필 결과 보기 전 고정, 2026-09-29)

이벤트: 2024-06-30 이전 = 원문 파싱(국민연금 nps_events 동결본 + 비NPS tier1·tier2·tier3 inst_events, 티어는
instiwatch config 화이트리스트 + fallback 패턴을 소스에서 읽어 그대로 적용, 기관명 = 공시 제출인), 2024-07 이후 =
instiwatch signals(비내부자, superseded=0) 그대로. 점수 = instiwatch config: tier1 new/inc 3, tier2 2/2,
tier3 new 2 / inc 1, decrease −2, exit −3. noise·unparsed 제외. 겹침 검증 일치율 먼저 보고.
월초 T(2022-01 ~ T+60 ≤ 최신 봉), 판단 정보 signal_date < T, 종가 ≤ T−1, T 종가 매수 → T+60 종가, 비용 0.25%,
적격 가격 ≥1,000원·dvol20 ≥1억. 창 = [T−180일, T).
- R3: 원 사전등록 그대로(점수 합 ≥4, 양 시그널 기관 ≥2, 그 기관들의 이후 decrease/exit 없음). 전부 보유.
- IW: instiwatch `review.candidates()` 순서 재현. watchlist = 점수 상위 100(동점은 최근 공시 먼저 등장 순) →
  교집합(점수 ≥4, 양 기관 ≥2) → build_indicators rank(① 매수기관 ≥2 +4 ② 기관별 누적 ≥3%p +3 / 매수 ≥3회 +2
  ③ tier3 신규 +2 ④ 교집합 성립 14일 이내 +2 ⑤ 내부자: 워치리스트에 내부자 시그널이 없어 0(원 코드와 동일)
  ⑥ 매수 추정액(시총×변동%p 합) ≥500억 +1, 시총 = T−1 종가 × 발행주식수(공시 FLT_STK_CNT 또는 보유주식/비율)
  경고: 매도만 한 기관 −2, 시그널 ≤2건 −1) → composite(수급 min(40, max(0, rank×4)), 실적 = T1-KR 공시 op_yoy
  (ed < T, 400일 이내 최신) 20/16/12/6/0, 가격 = 1년 위치 20/15/10/3/0, 밸류·안전 = PIT 데이터 없음 → 분모 제외
  (원 코드의 결측 처리와 동일)) → (−pct, −rank) 정렬, 적격 종목 중 상위 8(원 코드 limit=8).
벤치: 규모매칭 / KOSPI200 / KOSDAQ150 / NPS 보유 유니버스. 구간: OOS 2022-01~2024-06 / 교량 2024-07~2025-06 /
IS 2025-07~. 상위 기여 3종목 제외. 게이트 = 원 게이트(규모매칭 AND KOSPI200 NW t ≥ 2.5, 양 반기 +) + OOS 부호 유지.
"""

SCORE = {("tier1", "new"): 3, ("tier1", "increase"): 3, ("tier2", "new"): 2, ("tier2", "increase"): 2,
         ("tier3", "new"): 2, ("tier3", "increase"): 1}


def cmd_retest2():
    import datetime as dt
    import math
    import statistics as st
    from bisect import bisect_right
    from collections import defaultdict
    sys.path.insert(0, str(ROOT / "tools" / "research"))
    from research_k1_smartmoney_20260929 import nw_t

    OUT = BF.parent
    H, COST = 60, 0.25
    # ---- 이벤트: (date8, code, inst, tier, kind, score, rchg, flt)
    ev = []
    for r in csv.DictReader((BF / "nps_events.csv").open(encoding="utf-8")):
        if r["grp"] == "bf" and r["kind"] in ("new", "increase", "decrease", "exit") and r["signal_date"] < "20240701":
            ev.append((r["signal_date"], r["stock_code"], "국민연금공단", "tier1", r["kind"], None,
                       _f(r["change"] or "") or 0.0, None))
    for r in csv.DictReader((BF / "inst_events.csv").open(encoding="utf-8")):
        if r["grp"] == "bf" and r["kind"] in ("new", "increase", "decrease", "exit") and r["signal_date"] < "20240701":
            ev.append((r["signal_date"], r["stock_code"], r["institution"], r["tier"], r["kind"], None,
                       _f(r["change"] or "") or 0.0, _f(r["flt"] or "")))
    with contextlib.closing(sqlite3.connect(DB, uri=True, timeout=10)) as c:
        for d, sc, inst, tier, k, s, rch, sh, ratio in c.execute(
                "SELECT signal_date, stock_code, institution, tier, kind, score, ratio_change, shares, ratio FROM signals "
                "WHERE superseded=0 AND tier!='insider' AND signal_date >= '20240701' AND stock_code!=''"):
            flt = sh / (ratio / 100) if sh and ratio else None
            ev.append((d, sc, inst, tier, k, s, rch or 0.0, flt))
    ev = [(d, sc, inst, tier, k, (s if s is not None else SCORE.get((tier, k), -2 if k == "decrease" else -3)),
           rch, flt) for d, sc, inst, tier, k, s, rch, flt in ev]
    ev.sort(key=lambda x: x[0])
    by_code = defaultdict(list)
    for e in ev:
        by_code[e[1]].append(e)
    nps_by = defaultdict(list)
    for e in ev:
        if "국민연금" in e[2]:
            nps_by[e[1]].append((e[0], e[4]))
    flt_by = defaultdict(list)
    for e in ev:
        if e[7]:
            flt_by[e[1]].append((e[0], e[7]))
    fin = defaultdict(list)
    import gzip
    with gzip.open(ROOT / "data/analysis/model_T1kr_earnings_20260928/events_features.csv.gz", "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["op_yoy"] not in ("", None):
                fin[r["t"]].append((r["ed"], float(r["op_yoy"])))
    for v in fin.values():
        v.sort()

    # ---- 가격 (cmd_retest와 같은 소스·우선순위)
    px = {}
    cache = json.load((ROOT / "data/price_backfill_yf/kr_fallen_price_cache_2018_2026.json").open())
    for code, rows in cache.items():
        if rows:
            px[code] = ([x["d"] for x in rows], [x["c"] for x in rows], [x["v"] for x in rows])
    del cache
    for p in (ROOT / "data/analysis/model_T1kr_earnings_20260928/prices").glob("*.csv"):
        if p.stem in px:
            continue
        ds, cs, vs = [], [], []
        for r in csv.DictReader(p.open(encoding="utf-8")):
            try:
                cl, vo = float(r["close"]), float(r["volume"])
            except (ValueError, TypeError):
                continue
            if cl > 0:
                ds.append(r["date"][:10]); cs.append(cl); vs.append(vo)
        if ds:
            px[p.stem] = (ds, cs, vs)
    extra = defaultdict(lambda: ([], [], []))
    for r in csv.DictReader((OUT / "yf_prices.csv").open(encoding="utf-8")):
        if r["code"] not in px:
            e = extra[r["code"]]
            e[0].append(r["date"]); e[1].append(float(r["close"])); e[2].append(float(r["volume"]))
    px.update(extra)
    idx = {c: {d: i for i, d in enumerate(v[0])} for c, v in px.items()}

    def bench(f):
        rows = list(csv.DictReader((ROOT / "data/analysis/voltarget_20260924" / f).open(encoding="utf-8")))
        return {r["Date"][:10]: float(r["Close"]) for r in rows if r["Close"]}
    ks, kq = bench("069500.KS.csv"), bench("229200.KS.csv")
    cal = sorted(ks)
    ci = {d: i for i, d in enumerate(cal)}

    def close_le(code, d):
        ds, cs, _ = px[code]
        i = bisect_right(ds, d)
        return cs[i - 1] if i else None

    def elig_dv(code, dprev):
        ds, cs, vs = px[code]
        i = bisect_right(ds, dprev)
        if i < 20 or cs[i - 1] < 1000:
            return None
        dv = sum(cs[j] * vs[j] for j in range(i - 20, i)) / 20
        return dv if dv >= 1e8 else None

    def pos1y(code, dprev):
        ds, cs, _ = px[code]
        i = bisect_right(ds, dprev)
        lo = (dt.date.fromisoformat(dprev) - dt.timedelta(days=365)).isoformat()
        j = bisect_right(ds, lo)
        w = cs[j:i]
        if len(w) < 200:
            return None
        mn, mx = min(w), max(w)
        return 100 * (w[-1] - mn) / (mx - mn) if mx > mn else None

    def fwd(code, ti):
        T, E = cal[ti], cal[ti + H]
        k = idx[code].get(T)
        if k is None:
            return None
        ds, cs, _ = px[code]
        e = bisect_right(ds, E) - 1
        if e <= k or ds[e] < cal[ti + H - 5]:
            return None
        for j in range(k + 1, e + 1):
            if abs(cs[j] / cs[j - 1] - 1) > 0.35:
                return None
        return 100 * (cs[e] / cs[k] - 1)

    def universe(ti):
        T, Tp = cal[ti], cal[ti - 1]
        el = {}
        for code in px:
            if code in ("069500", "229200") or T not in idx[code]:
                continue
            dv = elig_dv(code, Tp)
            if dv:
                el[code] = dv
        order = sorted(el, key=el.get)
        dec = {c: min(9, i * 10 // len(order)) for i, c in enumerate(order)}
        ret = {c: fwd(c, ti) for c in order}
        dm = defaultdict(list)
        for c, r in ret.items():
            if r is not None:
                dm[dec[c]].append(r)
        return dec, ret, {k: st.mean(v) for k, v in dm.items()}

    def held(T8):
        out = []
        for code, lst in nps_by.items():
            prior = [x for x in lst if x[0] < T8]
            if prior and prior[-1][1] != "exit":
                out.append(code)
        return out

    def score(u, picks, ti):
        dec, ret, dm = u
        rs, sb = [], []
        for c in picks:
            r = ret.get(c)
            if r is None:
                continue
            rs.append(r - COST)
            sb.append(dm[dec[c]])
        if not rs:
            return None
        T, E = cal[ti], cal[ti + H]
        hr = [ret[c] for c in held(T.replace("-", "")) if ret.get(c) is not None]
        p = st.mean(rs)
        return dict(T=T, n=len(rs), ret=p, x_size=p - st.mean(sb), x_k200=p - 100 * (ks[E] / ks[T] - 1),
                    x_kq150=p - 100 * (kq[E] / kq[T] - 1), x_held=p - (st.mean(hr) if hr else float("nan")),
                    picks=[(c, ret[c]) for c in picks if ret.get(c) is not None])

    def formed_date(code, T8):
        rows = [e for e in by_code[code] if e[0] < T8]
        formed, active = None, False
        for d in sorted({e[0] for e in rows}):
            since = (dt.datetime.strptime(d, "%Y%m%d") - dt.timedelta(days=180)).strftime("%Y%m%d")
            sub = [e for e in rows if since <= e[0] <= d]
            sc = sum(e[5] for e in sub)
            insts = {e[2] for e in sub if e[5] > 0}
            ok = sc >= 4 and len(insts) >= 2
            if ok and not active:
                formed = d
            active = ok
        return formed if active else None

    months = sorted({d[:7] for d in cal})
    rebal = [ci[next(d for d in cal if d[:7] == m)] for m in months]
    rebal = [ti for ti in rebal if cal[ti] >= "2022-01-01" and ti + H < len(cal)]
    res = defaultdict(list)
    for ti in rebal:
        T, Tp = cal[ti], cal[ti - 1]
        T8 = T.replace("-", "")
        lo180 = (dt.date.fromisoformat(T) - dt.timedelta(days=180)).strftime("%Y%m%d")
        u = universe(ti)
        dec = u[0]
        # stock_scores: 최근 공시부터 훑어 삽입 순서를 만든다(원 코드 ORDER BY signal_date DESC)
        win = sorted([e for e in ev if lo180 <= e[0] < T8], key=lambda e: e[0], reverse=True)
        ent = {}
        for e in win:
            x = ent.setdefault(e[1], {"score": 0.0, "insts": set(), "sigs": []})
            x["score"] += e[5]
            if e[5] > 0:
                x["insts"].add(e[2])
            x["sigs"].append(e)
        wl = sorted(ent.items(), key=lambda kv: kv[1]["score"], reverse=True)[:100]
        r3, iw = [], []
        for code, x in ent.items():
            if code not in dec or not (x["score"] >= 4 and len(x["insts"]) >= 2):
                continue
            last_pos = {}
            for e in x["sigs"]:
                if e[5] > 0:
                    last_pos[e[2]] = max(last_pos.get(e[2], ""), e[0])
            if not any(e[2] in last_pos and e[4] in ("decrease", "exit") and e[0] > last_pos[e[2]] for e in x["sigs"]):
                r3.append(code)
        for code, x in wl:
            if code not in dec or not (x["score"] >= 4 and len(x["insts"]) >= 2):
                continue
            stats = {}
            for e in x["sigs"]:
                s_ = stats.setdefault(e[2], {"tier": e[3], "buys": 0, "sells": 0, "cum": 0.0, "new": False})
                if e[4] in ("new", "increase"):
                    s_["buys"] += 1
                    s_["new"] |= e[4] == "new"
                else:
                    s_["sells"] += 1
                s_["cum"] += e[6] or 0
            buyers = {n: s_ for n, s_ in stats.items() if s_["buys"] > 0}
            rank = 4.0 if len(buyers) >= 2 else 0.0
            for s_ in buyers.values():
                rank += 3 if s_["cum"] >= 3.0 else (2 if s_["buys"] >= 3 else 0)
                rank += 2 if (s_["tier"] == "tier3" and s_["new"]) else 0
            fd = formed_date(code, T8)
            if fd and (dt.date.fromisoformat(T) - dt.datetime.strptime(fd, "%Y%m%d").date()).days <= 14:
                rank += 2
            fl = [v for d, v in flt_by.get(code, []) if d < T8]
            cl = close_le(code, Tp)
            if fl and cl:
                mc = cl * fl[-1]
                if sum(mc * e[6] / 100 for e in x["sigs"] if (e[6] or 0) > 0) >= 50e9:
                    rank += 1
            rank -= 2 * sum(1 for s_ in stats.values() if s_["sells"] > 0 and s_["buys"] == 0)
            if len(x["sigs"]) <= 2:
                rank -= 1
            parts = [(max(0.0, min(40.0, rank * 4)), 40)]
            lo400 = (dt.date.fromisoformat(T) - dt.timedelta(days=400)).isoformat()
            fr = [v for d, v in fin.get(code, []) if lo400 <= d < T]
            if fr:
                op = fr[-1]
                parts.append((20 if op >= 30 else 16 if op >= 15 else 12 if op >= 5 else 6 if op >= -5 else 0, 20))
            p52 = pos1y(code, Tp)
            if p52 is not None:
                p = round(p52)
                parts.append((20 if p < 25 else 15 if p < 45 else 10 if p < 66 else 3 if p < 85 else 0, 20))
            pct = round(sum(a for a, _ in parts) / sum(m for _, m in parts) * 100)
            iw.append((-pct, -rank, code))
        iw = [c for _, _, c in sorted(iw, key=lambda z: (z[0], z[1]))[:8]]
        for rule, pk in (("R3", r3), ("IW", iw)):
            s = score(u, pk, ti)
            if s:
                res[rule].append(s)
        print(T, "R3", len(r3), "IW", len(iw), "wl", len(wl), file=sys.stderr, flush=True)

    L = []

    def stats_(cs, key, lag=2):
        x = [c[key] for c in cs if not math.isnan(c[key])]
        if not x:
            return "-"
        h = len(x) // 2
        return (f"{st.mean(x):+.2f} (t {nw_t(x, lag):+.2f}, {sum(v > 0 for v in x)}/{len(x)}, "
                f"반기 {st.mean(x[:h]) if h else float('nan'):+.2f}/{st.mean(x[h:]):+.2f})")
    segs = [("전체", "0000", "9999"), ("OOS 백필 ≤2024-06", "0000", "2024-07"),
            ("교량 2024-07~2025-06", "2024-07", "2025-07"), ("IS 2025-07~2026-06", "2025-07", "9999")]
    for rule in ("R3", "IW"):
        L.append(f"\n#### {rule}")
        L.append("| 구간 | 코호트 | 평균 종목수 | 포트 원수익 | vs 규모매칭 | vs KOSPI200 | vs KOSDAQ150 | vs NPS 보유 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for lab, a, b in segs:
            cs = [c for c in res[rule] if a <= c["T"] < b]
            if not cs:
                continue
            L.append(f"| {lab} | {len(cs)} | {st.mean(c['n'] for c in cs):.1f} | {st.mean(c['ret'] for c in cs):+.2f} | "
                     f"{stats_(cs, 'x_size')} | {stats_(cs, 'x_k200')} | {stats_(cs, 'x_kq150')} | {stats_(cs, 'x_held')} |")
        by = defaultdict(list)
        for c in res[rule]:
            by[c["T"][:4]].append(c["x_size"])
        L.append("- 연도별 규모매칭 초과: " + ", ".join(f"{y} {st.mean(v):+.2f}({len(v)})" for y, v in sorted(by.items())))
        contrib = defaultdict(float)
        for c in res[rule]:
            for code, r in c["picks"]:
                contrib[code] += r
        top3 = sorted(contrib, key=contrib.get, reverse=True)[:3]
        rows = []
        for c in res[rule]:
            ti = ci[c["T"]]
            s = score(universe(ti), [code for code, _ in c["picks"] if code not in top3], ti)
            if s:
                rows.append(s)
        for lab, a, b in segs[:2]:
            cs = [c for c in rows if a <= c["T"] < b]
            if cs:
                L.append(f"- 상위3({','.join(top3)}) 제외 {lab}: 규모매칭 {stats_(cs, 'x_size')} / "
                         f"NPS보유 {stats_(cs, 'x_held')} / KOSPI200 {stats_(cs, 'x_k200')}")
    L.append(f"\n- 이벤트 {len(ev)}건(백필 {sum(1 for e in ev if e[0] < '20240701')}), 종목 {len(by_code)}, "
             f"가격 없음 {len([c for c in by_code if c not in px])}종목")
    txt = "\n".join(L)
    (OUT / "retest2_summary.md").write_text(txt, encoding="utf-8")
    json.dump({k: [{kk: vv for kk, vv in c.items()} for c in v] for k, v in res.items()},
              (OUT / "retest2_results.json").open("w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(txt)


def defaultdict_list():
    from collections import defaultdict
    return defaultdict(list)


if __name__ == "__main__":
    {"list": cmd_list, "docs": cmd_docs, "parse": cmd_parse, "validate": cmd_validate,
     "retest": cmd_retest, "docs2": cmd_docs2, "fallen": cmd_fallen,
     "parse2": cmd_parse2, "validate2": cmd_validate2, "retest2": cmd_retest2}[sys.argv[1]]()
