#!/usr/bin/env python3
"""우선순위 3전략 주간 성적표 (read-only, 2026-09-30 운영자 승인).

    PYTHONUTF8=1 python tools/research/research_focus_scoreboard.py [YYYY-MM-DD] [--rebuild]

as-of D(기본 = 오늘 KST): **D 이하 봉만** 쓴다(지수 달력·종목 봉·정산 행 전부 D에서 자른다).
쓰기: `docs/reports/focus_scoreboard_<D>.md`, `data/analysis/focus_scoreboard/<D>.json`,
      유니버스 캐시 `data/analysis/focus_scoreboard/universe_<KR끝>_<US끝>.pkl`(가격 끝날이 바뀌면 재빌드).
라이브 코드·config·virtual_books 원장·자사주 원장에는 쓰지 않는다(DB는 `mode=ro` URI, jsonl은 읽기만).

## 규약 (계산 전 고정 — 수치는 전부 소스에서 읽는다)

- 인큐베이터 arm: `data/shadow/virtual_books.db` trades. 계약(tp/sl/hold)·주문금액은 `tools/virtual_books.STRATEGIES` 정본.
  레코드 생성·규모매칭 벤치는 `research_incubator_regime_20260930.py`의 구현을 그대로 재사용한다
  (청산 봉 재현 `contract`, dvol20 십분위 `build_universe`/`decile_of`, 날짜 클러스터 `day_stat`).
  - forward = `backfill=0` AND session_date ≥ 2026-09-01 (09-30 진단 리포트와 같은 규약 — `_rg`의 forward_start 09-10도 이 창에 포함).
  - 만기 = 신호 세션 이후 지수 달력(≤D) 세션 수 ≥ hold. 만기분만 집계. 만기율 = 만기 / forward 진입(≤D).
  - 판정 지표 = net − 규모매칭 계약 벤치(같은 신호일·같은 dvol20 십분위·같은 TP/SL/보유/BE락/비용). 날짜 = 신호일 클러스터.
  - 참고 지수: KR = KOSPI200(069500)·KOSDAQ150(229200), US = IWM — 진입 시가 → 실청산 봉 종가.
- KR 자사주 원장: `data/shadow/kr_buyback_shadow_settle.jsonl`(규칙 Y1·L1_lowliq, h5 = 1차). 규모매칭 = 원장 정산의
  `ex`(Y1: dvol20 십분위) / `lx`(L1: 저유동 대역 삼분위·AR 스프레드 비용). forward = `actionable=True`(D 다음 시가 전 탐지)
  AND `elig_final` AND 청산일 ≤ D. 날짜 = 진입일 클러스터(원장 report와 같은 키). 참고 지수 `xk`/`xq`(Y1) · `lxk`/`lxq`(L1).
  백필 부호 = 연구 OOS h5(`data/analysis/kr_event_20260930/ext_results.json`: Y1 = c.yf_h5, L1 = b.rules 'L1 저유동 직접 ≥1%').
- t = 날짜 클러스터 일평균의 단순 t(`day_stat`). ⚠️ buyback_shadow 자체 report는 NW t·게이트 30건을 쓴다 — 이 성적표는 운영자 게이트(50건)를 쓴다.
- 누적 KRW = Σ(만기 트레이드 net% × 라이브 계약 주문금액). arm = STRATEGIES `order_krw`(KR 22만·US 54만 — CLAUDE.md의
  KR_FALLEN_ORDER_MAX_KRW / US_SWING_ORDER_MAX_KRW와 같은 값), 자사주 = KR 22만(원장에 계약 금액 없음 → KR 레인 값 차용).
  ⚠️ 이 arm들은 pick=all·슬롯 무제한 → 누적 KRW는 "전량 체결 가정" 값이지 실현 가능한 북이 아니다.

## 승격 게이트 (사전등록, 운영자 2026-09-30)

만기 forward ≥ 50건 AND 날짜 클러스터 t(규모매칭 초과) ≥ 2.0 AND forward 평균 부호 = 백필 부호 → **'승격 검토'**.
아니면 진행 막대(n/50). 부모 `c_kr_fallen_nomajor`는 맥락 표시(게이트 대상 아님).
나머지 arm은 '기록 전용' — 개수만 한 줄.
"""
from __future__ import annotations

import bisect
import json
import pickle
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for p in (ROOT, ROOT / "tools", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import research_incubator_regime_20260930 as rr  # noqa: E402

VB = ROOT / "data" / "shadow" / "virtual_books.db"
BB_SETTLE = ROOT / "data" / "shadow" / "kr_buyback_shadow_settle.jsonl"
BB_LEDGER = ROOT / "data" / "shadow" / "kr_buyback_shadow.jsonl"
BB_RESEARCH = ROOT / "data" / "analysis" / "kr_event_20260930" / "ext_results.json"
OUTDIR = ROOT / "data" / "analysis" / "focus_scoreboard"
REPORT_DIR = ROOT / "docs" / "reports"
FORWARD_START = "2026-09-01"
GATE_N, GATE_T = 50, 2.0
BB_KRW = 220_000
BB_COST = 0.25
KST = timezone(timedelta(hours=9))

# (그룹, 종류, id, 맥락 여부)
FOCUS = [
    ("1. KR 자사주", "buyback", "Y1", False),
    ("1. KR 자사주", "buyback", "L1_lowliq", False),
    ("1. KR 자사주", "arm", "c_kr_fallen_buyback30", False),
    ("1. KR 자사주", "arm", "c_kr_fallen_buyback30_rg", False),
    ("2. US 급락·국면", "arm", "c_us_fallen_regime", False),
    ("3. KR 급락(MA20·최대주주 배제)", "arm", "c_kr_fallen_nomajor_rg", False),
    ("3. KR 급락(MA20·최대주주 배제)", "arm", "c_kr_fallen_nomajor", True),
]
FOCUS_ARMS = [x[2] for x in FOCUS if x[1] == "arm"]
BB_COLS = {"Y1": {"sel": "ex", "k200": "xk", "kq150": "xq"},
           "L1_lowliq": {"sel": "lx", "k200": "lxk", "kq150": "lxq"}}


# ───────────────────────── 순수 계산 (테스트 대상) ─────────────────────────
def truncate(bars, asof):
    """D 이하 봉만."""
    j = bisect.bisect_right([b[0] for b in bars], asof)
    return bars[:j]


def arm_records(rows, asof, cal, bars_fn, uni, idx_ret, spec):
    """virtual_books trades 행 → (거래 레코드, arm별 forward 진입/만기 수).

    rows: (sid, session_date, ticker, entry_price, net_pct, status, backfill, meta)
    cal: {"KR": [날짜 ≤ D], "US": [날짜 ≤ D]} · bars_fn(mk, tk) → D 이하 봉 · uni: {"KR"|"US": (cuts, sz)}
    idx_ret(name, entry_date, n_bars) → % 또는 None · spec: sid → STRATEGIES 항목.
    research_incubator_regime_20260930.main()의 레코드 루프와 같은 필터 체인이다.
    """
    info = defaultdict(lambda: {"entries_fwd": 0, "mat_fwd": 0})
    recs = []
    for sid, sd, tk, _ep, net, status, bf, meta in rows:
        if sd > asof:
            continue
        s = spec.get(sid, {})
        mk = rr.strategy_market(s) if s else ("KR" if "kr" in sid else "US")
        tp, sl, hold = float(s.get("tp", rr.TP)), float(s.get("sl", rr.SL)), int(s.get("hold", rr.HOLD_SESSIONS))
        umk = "KR" if mk == "KR" else "US"
        seg = "bf" if bf else ("fwd" if sd >= FORWARD_START else "bf")
        c = cal[umk]
        pos = bisect.bisect_right(c, sd) - 1
        matured = (len(c) - 1 - pos) >= hold
        if seg == "fwd":
            info[sid]["entries_fwd"] += 1
            info[sid]["mat_fwd"] += int(matured)
        if status != "CLOSED" or net is None or not matured:
            continue
        try:
            m = json.loads(meta) if meta else {}
        except ValueError:
            m = {}
        bars = bars_fn(mk, tk)
        if not bars:
            continue
        if mk == "US":
            ei = next((i for i, x in enumerate(bars) if x[0] == sd), None)
        else:
            ei = next((i for i, x in enumerate(bars) if x[0] > sd), None)
        if ei is None or ei < 21:
            continue
        E = bars[ei][0]
        sig = m.get("signal_date") or (bars[ei - 1][0] if mk == "US" else sd)
        fee = rr.FEE_KR if mk == "KR" else rr.FEE_US
        r = rr.contract(bars[ei][1], bars[ei:ei + hold], tp, sl, hold, mk != "KR", fee)
        if r is None:
            continue
        xi = r[1]
        si = ei - 1
        dv20 = st.mean(x[4] * x[5] for x in bars[si - 19:si + 1]) / (1e8 if umk == "KR" else 1e6)
        rec = {"sid": sid, "seg": seg, "d": sig, "E": E, "tk": tk, "net": net, "hold": hold, "mk": mk,
               "krw": float(s.get("order_krw") or 0)}
        cuts, sz = uni[umk]
        if sig in cuts:
            dec = rr.decile_of(cuts[sig], dv20)
            sc = sz.get((sig, dec, (tp, sl, hold)))
            sh = sz.get((sig, dec, ("hold", hold)))
            if sh and sc and sh[1] >= 20 and sc[1] >= 20:
                rec.update(dec=dec, S_con=sc[0])
        if umk == "KR":
            rec["Ix_kq150"] = idx_ret("KQ150", E, xi + 1)
            rec["Ix_k200"] = idx_ret("K200", E, xi + 1)
        else:
            rec["Ix_iwm"] = idx_ret("IWM", E, xi + 1)
        recs.append(rec)
    return recs, dict(info)


def summarize_arm(recs, sid, seg):
    rs = [r for r in recs if r["sid"] == sid and r["seg"] == seg and r.get("S_con") is not None]
    out = {"n": len(rs), "raw": rr.day_stat([(r["d"], r["net"]) for r in rs]),
           "sel": rr.day_stat([(r["d"], r["net"] - r["S_con"]) for r in rs])}
    for k in ("Ix_k200", "Ix_kq150", "Ix_iwm"):
        pairs = [(r["d"], r["net"] - r[k]) for r in rs if r.get(k) is not None]
        out[k] = rr.day_stat(pairs) if pairs else (None, None, 0)
    out["krw_raw"] = sum(r["net"] / 100 * r["krw"] for r in rs)
    out["krw_sel"] = sum((r["net"] - r["S_con"]) / 100 * r["krw"] for r in rs)
    out["n_nobench"] = sum(1 for r in recs if r["sid"] == sid and r["seg"] == seg and r.get("S_con") is None)
    return out


def buyback_summary(settle, ledger, rule, asof, h=5):
    """자사주 원장 forward 요약 (actionable·elig_final·청산 ≤ D, h5)."""
    cols = BB_COLS[rule]
    fw = [x for x in settle if x.get("rule") == rule and x.get("h") == h and x.get("actionable") is True
          and x.get("elig_final") and x.get("exit", "9999") <= asof and x.get(cols["sel"]) is not None]
    bf = [x for x in settle if x.get("rule") == rule and x.get("h") == h and x.get("actionable") is not True
          and x.get("elig_final") and x.get("exit", "9999") <= asof]
    q_fwd = [r for r in ledger if r.get("qualifies") and r.get("rule") == rule and r.get("actionable") is True
             and r.get("entry_date", "9999") <= asof]

    def raw(x):
        if rule == "Y1":
            return None if x.get("r_px") is None else x["r_px"] - BB_COST
        spr = x.get("spread_ar_pct")
        return None if (x.get("r_naver") is None or spr is None) else x["r_naver"] - BB_COST - spr

    out = {"n": len(fw), "entries_fwd": len(q_fwd), "mat_fwd": len(fw),
           "sel": rr.day_stat([(x["entry"], x[cols["sel"]]) for x in fw]),
           "raw": rr.day_stat([(x["entry"], raw(x)) for x in fw if raw(x) is not None])}
    for k, c in (("Ix_k200", cols["k200"]), ("Ix_kq150", cols["kq150"])):
        pairs = [(x["entry"], x[c]) for x in fw if x.get(c) is not None]
        out[k] = rr.day_stat(pairs) if pairs else (None, None, 0)
    out["Ix_iwm"] = (None, None, 0)
    out["krw_raw"] = sum((raw(x) or 0) / 100 * BB_KRW for x in fw)
    out["krw_sel"] = sum(x[cols["sel"]] / 100 * BB_KRW for x in fw)
    out["ledger_backfill_settled"] = len(bf)
    return out


def gate(n, sel_mean, sel_t, bf_mean):
    """→ (승격 검토 여부, 문구)."""
    bar = "#" * min(10, n * 10 // GATE_N) + "-" * (10 - min(10, n * 10 // GATE_N))
    same = (bf_mean is not None and sel_mean is not None and (sel_mean > 0) == (bf_mean > 0))
    if n >= GATE_N and sel_t is not None and sel_t >= GATE_T and same:
        return True, f"**승격 검토** (n {n}, t {sel_t:+.2f}, 백필 부호 일치)"
    miss = []
    if n < GATE_N:
        miss.append(f"n {n}/{GATE_N}")
    if sel_t is None or sel_t < GATE_T:
        miss.append("t " + ("산출 불가" if sel_t is None else f"{sel_t:+.2f}") + f" < {GATE_T}")
    if bf_mean is None:
        miss.append("백필 부호 미상")
    elif sel_mean is not None and not same:
        miss.append("백필 부호 불일치")
    return False, f"`[{bar}]` {n}/{GATE_N} — 미달: " + ", ".join(miss)


def fmt(v, t=None, days=None):
    if v is None:
        return "-"
    s = f"{v:+.2f}%"
    if t is not None:
        s += f" · t {t:+.2f}"
    if days is not None:
        s += f" ({days}일)"
    return s


def krw(v):
    return f"{v:+,.0f}원"


def render(asof, rows, record_only, meta):
    L = [f"# 우선순위 3전략 주간 성적표 — as-of {asof}", "",
         "read-only. `tools/research/research_focus_scoreboard.py` (규약·게이트는 docstring에 계산 전 고정).",
         f"**forward 만기분만**(arm: backfill=0·신호 ≥ {FORWARD_START} / 자사주: actionable=True). 값 = 신호일(자사주는 진입일) "
         "클러스터 일평균 · 날짜 t. **판정 지표 = 규모매칭 계약 벤치 초과.**",
         f"데이터 끝: KR 지수 {meta['kr_last']} · US 지수 {meta['us_last']} (as-of 이하로 자름). 생성 {meta['generated']}.", "",
         "| 전략 | 대상 | 만기 n | 일수 | 만기율 | 원 net | **규모매칭 초과 · t** | KOSPI200 초과 | KOSDAQ150 초과 | IWM 초과 | 백필 부호(선택알파) | 누적 KRW 원/초과 | 게이트 |",
         "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|"]
    for r in rows:
        s = r["stats"]
        sm, stt, sd = s["sel"]
        mr = (f"{100 * s['mat_fwd'] / s['entries_fwd']:.0f}% ({s['mat_fwd']}/{s['entries_fwd']})"
              if s.get("entries_fwd") else "진입 0")
        bfm = r["bf_mean"]
        bfc = "미상" if bfm is None else f"{'+' if bfm > 0 else '−'} ({bfm:+.2f}%, {r['bf_src']})"
        name = f"`{r['id']}`" + (" (맥락)" if r["context"] else "")
        krwc = (f"{krw(s['krw_raw'])} / {krw(s['krw_sel'])}" if s["n"] else "0원")
        L.append(f"| {r['group']} | {name} | {s['n']} | {sd} | {mr} | {fmt(*s['raw'][:2])} | **{fmt(sm, stt)}** | "
                 f"{fmt(*s['Ix_k200'][:2])} | {fmt(*s['Ix_kq150'][:2])} | {fmt(*s['Ix_iwm'][:2])} | {bfc} | {krwc} | "
                 f"{'맥락 — 게이트 대상 아님' if r['context'] else r['gate'][1]} |")
    L += ["", f"기록 전용 arm {record_only['n']}개(forward 행 보유 {record_only['with_fwd']}개) — 표 생략.", "",
          "주석:",
          "- 누적 KRW = Σ(net% × 계약 주문금액: arm KR 22만·US 54만 = STRATEGIES order_krw, 자사주 22만 차용). "
          "arm은 pick=all·슬롯 무제한이라 **전량 체결 가정** 값이다(실현 가능한 북 아님).",
          "- 자사주 원장: t는 이 표에선 단순 날짜 t(운영자 게이트 50건). `tools/buyback_shadow.py report`는 NW t·30건 게이트를 쓴다.",
          "- 자사주 백필 부호 = 연구 OOS h5(ext_results.json). arm 백필 부호 = 같은 파이프라인의 backfill 구간 선택알파.",
          f"- 규모매칭 벤치 결측(그날 십분위 칸 < 20종목 등)으로 빠진 만기 arm 행: {meta['nobench']}."]
    for x in meta.get("notes", []):
        L.append(f"- {x}")
    return "\n".join(L) + "\n"


# ───────────────────────── 적재 (I/O) ─────────────────────────
def load_universe(need, kr_last, us_last, rebuild):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    path = OUTDIR / f"universe_{kr_last}_{us_last}.pkl"
    if path.exists() and not rebuild:
        return pickle.loads(path.read_bytes())
    uni = {}
    for mk in ("KR", "US"):
        print(f"[유니버스 {mk}] 계약 {sorted(need[mk])} 빌드 중...", flush=True)
        uni[mk] = rr.build_universe(mk, need[mk])
    path.write_bytes(pickle.dumps(uni))
    for old in OUTDIR.glob("universe_*.pkl"):
        if old != path:
            old.unlink()
    return uni


def read_jsonl(p):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def buyback_backfill_sign():
    """연구 OOS h5 평균 (Y1, L1)."""
    try:
        d = json.loads(BB_RESEARCH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"Y1": None, "L1_lowliq": None}
    y1 = (((d.get("c") or {}).get("yf_h5") or {}).get("OOS") or [None])[0]
    l1 = None
    for k, v in ((d.get("b") or {}).get("rules") or {}).items():
        if k.startswith("L1 "):
            l1 = ((v.get("h5") or {}).get("OOS") or [None])[0]
    return {"Y1": y1, "L1_lowliq": l1}


def run(asof, rebuild=False):
    spec = rr.SPEC
    idx_full = {"IWM": rr.bench_bars("us", "IWM"), "K200": rr.bench_bars("kr", "069500"),
                "KQ150": rr.bench_bars("kr", "229200")}
    idx = {k: truncate(v, asof) for k, v in idx_full.items()}
    idx_i = {k: {b[0]: i for i, b in enumerate(v)} for k, v in idx.items()}

    def idx_ret(name, entry_date, n_bars):
        b, ii = idx[name], idx_i[name]
        i = ii.get(entry_date)
        if i is None or i + n_bars - 1 >= len(b):
            return None
        return (b[i + n_bars - 1][4] / b[i][1] - 1) * 100

    cal = {"KR": [b[0] for b in idx["K200"]], "US": [b[0] for b in idx["IWM"]]}
    need = {"KR": set(), "US": set()}
    for sid in FOCUS_ARMS:
        mk, tp, sl, h = rr.arm_contract(sid)
        need["KR" if mk == "KR" else "US"].add((tp, sl, h))
    uni = load_universe(need, idx_full["K200"][-1][0], idx_full["IWM"][-1][0], rebuild)

    cache = {}

    def bars_fn(mk, tk):
        k = (mk, tk)
        if k not in cache:
            b = rr.bars_kr(tk) if mk == "KR" else (rr.bars_slow(tk) if mk == "SLOW" else rr.bars_us(tk))
            cache[k] = truncate(b or [], asof)
        return cache[k]

    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        q = ",".join("?" * len(FOCUS_ARMS))
        rows = con.execute("SELECT strategy_id, session_date, ticker, entry_price, net_pct, status, backfill, meta "
                           f"FROM trades WHERE strategy_id IN ({q})", FOCUS_ARMS).fetchall()
        fwd_ids = {r[0] for r in con.execute(
            "SELECT DISTINCT strategy_id FROM trades WHERE backfill=0 AND session_date>=? AND session_date<=?",
            (FORWARD_START, asof)).fetchall()}
    finally:
        con.close()

    recs, info = arm_records(rows, asof, cal, bars_fn, uni, idx_ret, spec)
    bb_sign = buyback_backfill_sign()
    settle, ledger = read_jsonl(BB_SETTLE), read_jsonl(BB_LEDGER)

    out_rows, nobench = [], []
    for group, kind, sid, ctx in FOCUS:
        if kind == "arm":
            s = summarize_arm(recs, sid, "fwd")
            s.update(info.get(sid, {"entries_fwd": 0, "mat_fwd": 0}))
            b = summarize_arm(recs, sid, "bf")
            bf_mean, bf_src = b["sel"][0], f"백필 {b['sel'][2]}일"
            if s["n_nobench"]:
                nobench.append(f"`{sid}` {s['n_nobench']}건")
        else:
            s = buyback_summary(settle, ledger, sid, asof)
            bf_mean, bf_src = bb_sign.get(sid), "연구 OOS h5"
        g = gate(s["n"], s["sel"][0], s["sel"][1], bf_mean)
        out_rows.append({"group": group, "kind": kind, "id": sid, "context": ctx, "stats": s,
                         "bf_mean": bf_mean, "bf_src": bf_src, "gate": g})

    rec_only = [x["id"] for x in rr.STRATEGIES if not x.get("retired") and x["id"] not in FOCUS_ARMS]
    record_only = {"n": len(rec_only), "with_fwd": len(set(rec_only) & fwd_ids)}
    notes = []
    for r in out_rows:
        if r["kind"] == "buyback":
            notes.append(f"자사주 {r['id']}: forward 적격 진입 {r['stats']['entries_fwd']}건 · 만기 {r['stats']['n']}건 "
                         f"(원장 백필 정산 {r['stats']['ledger_backfill_settled']}건은 제외).")
    meta = {"kr_last": cal["KR"][-1] if cal["KR"] else "-", "us_last": cal["US"][-1] if cal["US"] else "-",
            "generated": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
            "nobench": ", ".join(nobench) if nobench else "0건", "notes": notes}
    md = render(asof, out_rows, record_only, meta)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"focus_scoreboard_{asof}.md").write_text(md, encoding="utf-8")
    js = {"asof": asof, "meta": meta, "record_only": record_only,
          "rows": [{k: v for k, v in r.items()} for r in out_rows]}
    (OUTDIR / f"{asof}.json").write_text(json.dumps(js, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(md)
    return js


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    pos = [a for a in argv if not a.startswith("--")]
    asof = pos[0] if pos else datetime.now(KST).strftime("%Y-%m-%d")
    datetime.strptime(asof, "%Y-%m-%d")
    run(asof, rebuild="--rebuild" in argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
