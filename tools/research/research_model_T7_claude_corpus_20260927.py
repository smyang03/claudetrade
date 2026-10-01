#!/usr/bin/env python3
"""R2 + T7 — Claude를 '고르는 자'로 정직하게 재채점하고, Claude가 틀리는 때를 메타 모델로 배운다 (read-only, 2026-09-27).

## 사전등록 (결과 계산 전에 고정 — 이 docstring 이후 규칙 변경 금지)

### 데이터
- 주 소스: `logs/raw_calls/*select_tickers*.json` (05-15~07-08, Claude 실제 응답 원문).
  - 실행 가능 호출만: 프롬프트 `execution_phase: preopen_watch`(trade_ready 금지) 제외,
    `parse_error=True`·`_fallback_mode`·`_parse_recovered` 제외(Claude 판단이 아님).
  - **Claude의 선택 = 원 응답**(`tr` 또는 `ca.a ∈ {BUY_READY, PROBE_READY, TRADE_READY, ADD_READY}`,
    구 포맷은 `trade_ready`). 시스템 정규화(`_normalized.trade_ready`)는 비교용으로만 기록.
  - 풀 = 프롬프트 후보 줄(정규식) ∪ 응답에 등장한 종목. 응답에만 있는 종목 수는 따로 센다.
  - 거부 = `ca.a == AVOID` 또는 구 포맷 `veto`. 언급 = WATCH/PULLBACK_WAIT/watchlist.
  - 천장(ceiling): 프롬프트 줄 `ceil=WATCH` 또는 응답 `ca.ceil == WATCH` 또는
    `_normalized.selection_evidence_watch_ceiling_tickers` 포함이면 **선택 불가(천장 WATCH)**.
- 보조 소스(감사 DB 시대 04-20~05-14): `data/audit/candidate_audit.db` 라벨 select_tickers /
  select_tickers_retry / selection_meta_live(05-11~05-14). 풀 = in_prompt OR watchlist OR trade_ready OR action≠''.
  선택 = claude_trade_ready=1 또는 action ∈ READY 집합. 거부 = AVOID 또는 veto 사유 존재. 천장 정보 없음 → 허용으로 취급.
  **사용 조건:** 겹치는 구간(05-15~07-08)에서 감사 DB(selection_meta_live) 선택 플래그가 raw 선택과
  (market, session, ticker) 단위로 ≥95% 일치할 때만 본 검정에 합친다. 미달이면 raw만으로 검정하고 불일치를 보고한다.

- **개정(결과 계산 전, 추출 점검에서 발견):** raw_calls에 테스트 픽스처 호출이 섞여 있다(US 05-15~05-21,
  후보 GOOD/BAD/T0~T6 또는 빈 풀, 378건). 풀이 비었거나 픽스처 종목뿐인 호출은 제외한다.

### 세션 날짜·중복 제거
- 타임스탬프는 KST naive. US는 ET = KST − 13h(2026-03-08~11-01 EDT 구간 assert), 세션 날짜 = ET 날짜.
- 키 = (market, ticker, session_date). 라벨 = **그날 실행 가능 호출에서 한 번이라도 선택**(P1, 주 정의).
  타임스탬프 = 선택이면 첫 선택 시각, 아니면 첫 노출 시각. 민감도 P2 = 첫 노출 호출의 라벨.
- 천장 허용(pool_perm) = 그날 노출 중 한 번이라도 천장 WATCH가 아니었음.

### 결과(체결 가능)
- 진입 = 결정 시각 **이후 첫 시가**(KR 09:00, US 09:30 ET; 장중 결정 → 다음 세션 시가, 장전 결정 → 당일 시가).
- 청산 = 진입 세션 + h 세션의 시가(시가→시가), h ∈ {1, 5}.
- 비용 왕복 KR 0.25% / US 0.50%. net = gross − cost.
- 벤치(비용 없음, 같은 창): KR = 229200(KOSDAQ150)·069500(KOSPI200)·**dvol20 십분위 규모매칭 EW(주 벤치)**,
  US = **IWM(주 벤치)** + dvol20 십분위 규모매칭 EW(보조). dvol20 = 결정 세션 **이전** 20봉 거래대금 평균.
  적격: KR 종가 ≥1,000원·dvol20 ≥20억·이력 ≥25봉 / US 종가 ≥$5·dvol20 ≥$2M·이력 ≥25봉.
- 분할/오류 필터: 창(진입 전날~청산) 안 종가비 >2.5 또는 <0.4면 제외(개수 보고).
- 민감도 S1: 결정 세션 종가 진입(MOC, 결정이 장 마감 10분 전 이전일 때만) → h세션 뒤 종가.

### R2 검정 (시장 × h)
- 주 검정(4): 천장 허용 풀에서 **선택 − 같은 날 비선택** 초과(주 벤치) 차, 날짜 클러스터
  (하루 = 선택 평균 − 비선택 평균). h=5는 Newey-West lag 4. 전·후반 = 시장별 날짜 중앙값 분할.
- 부 검정(4): **AVOID − 언급(WATCH/PULLBACK)** 차(veto 가치, A12 재검).
- 기술(비검정): 전체 풀, P2, S1, 선택 초과 수준(벤치별), confidence 구간(<0.55 / 0.55~0.64 / ≥0.65),
  행동 유형(BUY_READY vs PROBE_READY).
- 합격 = t ≥ 3 + 전·후반 같은 부호. 2 ≤ t < 3 + 전·후반 같은 부호 = 후보. 그 외 기각/무효.

### T7 메타 모델 (Claude가 틀리는 때)
- 표본 = 선택(P1) 종목. 라벨 y = 주 벤치 초과(h) < 0. h ∈ {1, 5} (검정 2).
- 피처(결정 시점 정보만): 프롬프트 줄 값(chg, p, vol, turn, liq, trainer tier/q, ceil, news signal/score/count,
  from_high, atr, ma60, rs), Claude 응답(a, s, c, pt.conf, mat, fr, rc, blk 수), 호출 맥락(market, 장 시작 후 분,
  mode, model, prompt_version, 후보 줄 수, 선택 수), 전일 종가까지 지수 맥락(벤치 ETF 1일·5일 수익률).
  사후 갱신되는 감사 컬럼(route_*, classification, pnl, lifecycle, mfe/mae, fill)은 쓰지 않는다.
- 모델: LightGBM(num_threads=2, n_estimators=200, learning_rate=0.05, num_leaves=15, min_child_samples=20,
  subsample=0.8, colsample_bytree=0.8, seed=7) 고정. 시장 통합 학습(market 피처 포함).
- 워크포워드: ISO 주 단위, 학습 데이터 ≥4주. 퍼지 = 학습 표본 중 청산일 ≥ 테스트 주 첫날인 행 제외.
- 규칙: 학습 표본 in-sample 예측 확률 중앙값보다 **낮은** P(오답) 픽만 유지.
- 평가: 하루 단위 (유지 평균 − 제거 평균), (유지 평균 − 전체 픽 평균) 날짜 클러스터 t(h=5 NW4), 전·후반.
- 사소한 기준선: `c ≥ 0.60`이면 유지(c 없는 행은 유지). 같은 지표로 보고.
- 합격 = 유지−제거 t ≥ 3 + 유지−전체 전·후반 양수. 한 국면(약 3개월)이므로 최고 등급은 '후보(shadow)'.

### 검정 수
R2 주 4 + 부 4 + T7 2 = 10. 기준 t ≥ 3.
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import glob
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "model_T7_claude_corpus_20260927"
RAW_GLOB = str(ROOT / "logs" / "raw_calls" / "*select_tickers*.json")
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
PRICE = {"KR": ROOT / "data" / "price" / "kr", "US": ROOT / "data" / "price" / "us"}
COST = {"KR": 0.25, "US": 0.50}
READY = {"BUY_READY", "PROBE_READY", "TRADE_READY", "ADD_READY"}
MENTION = {"WATCH", "PULLBACK_WAIT"}
HS = (1, 5)
FIXTURE = {"GOOD", "BAD"} | {f"T{i}" for i in range(20)}

LINE_RE = re.compile(r"^(\d{6}|[A-Z][A-Z0-9.\-]{0,7}) (?=[a-z_]+=)")
NUM = r"([+-]?\d+(?:\.\d+)?)"
KV = {
    "chg": re.compile(r"\bchg=" + NUM + "%"),
    "vol": re.compile(r"\bvol=" + NUM + "x"),
    "p": re.compile(r"\bp=([\d,]+(?:\.\d+)?)"),
    "from_high": re.compile(r"\bfrom_high=" + NUM + "%"),
    "atr": re.compile(r"\batr=" + NUM + "%"),
    "news_score": re.compile(r"\bscore=(\d+)"),
    "news_count": re.compile(r"\bnews=count=(\d+)"),
    "trainer_q": re.compile(r"\btrainer=\w+,q=(\d+)"),
    "rs": re.compile(r"\brs=\(?[A-Z]*" + NUM + "%"),
}
KVS = {
    "liq": re.compile(r"\bliq=(\w+)"),
    "news_signal": re.compile(r"\bsignal=(\w+)"),
    "trainer_tier": re.compile(r"\btrainer=(\w+)"),
    "ceil": re.compile(r"\bceil=(\w+)"),
    "ma60": re.compile(r"\bma60=(\w+)"),
}
TURN_RE = re.compile(r"\bturn=\$?([\d,]+(?:\.\d+)?)(M|B|K|억)?")


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ 시간

def to_local(ts_kst: datetime, market: str) -> datetime:
    if market == "US":
        assert datetime(2026, 3, 8) <= ts_kst <= datetime(2026, 11, 1), ts_kst
        return ts_kst - timedelta(hours=13)
    return ts_kst


def open_close(market, d):
    if market == "KR":
        return d + timedelta(hours=9), d + timedelta(hours=15, minutes=30)
    return d + timedelta(hours=9, minutes=30), d + timedelta(hours=16)


# ------------------------------------------------------------------ 추출: raw_calls

def parse_line(line):
    f = {}
    for k, rx in KV.items():
        m = rx.search(line)
        if m:
            try:
                f[k] = float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    for k, rx in KVS.items():
        m = rx.search(line)
        if m:
            f[k] = m.group(1)
    m = TURN_RE.search(line)
    if m:
        v = float(m.group(1).replace(",", ""))
        mult = {"M": 1e6, "B": 1e9, "K": 1e3, "억": 1e8}.get(m.group(2) or "", 1.0)
        f["log_turn"] = math.log10(max(v * mult, 1.0))
    return f


def extract_raw():
    files = sorted(glob.glob(RAW_GLOB))
    rows, calls = [], []
    skip = defaultdict(int)
    for fp in files:
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            skip["json_error"] += 1
            continue
        mkt = d.get("market")
        prompt = d.get("prompt") or ""
        m = re.search(r"execution_phase:\s*(\w+)", prompt)
        phase = m.group(1) if m else ""
        parsed = d.get("parsed") or {}
        norm = parsed.get("_normalized") or {}
        if phase == "preopen_watch":
            skip["preopen_watch"] += 1
            continue
        if d.get("parse_error") is True or parsed.get("_fallback_mode") or norm.get("_fallback_mode") \
                or parsed.get("_parse_recovered") or norm.get("_parse_recovered"):
            skip["parse_error_or_fallback"] += 1
            continue
        ts = datetime.fromisoformat(d["timestamp"][:26])
        lt = to_local(ts, mkt)
        sdate = lt.date().isoformat()
        mm = re.search(r"\bmode:\s*(\w+)", prompt) or re.search(r"합의 모드:\s*(\w+)", prompt)
        mode = mm.group(1) if mm else ""
        # 프롬프트 후보 줄
        lines = {}
        for ln in prompt.splitlines():
            mt = LINE_RE.match(ln.strip())
            if mt and mt.group(1) not in lines:
                lines[mt.group(1)] = parse_line(ln)
        # 응답
        sel, avoid, mention, ca_map = set(), set(), set(), {}
        if "ca" in parsed or "tr" in parsed:
            for t in parsed.get("tr") or []:
                sel.add(str(t))
            for t in parsed.get("wl") or []:
                mention.add(str(t))
            for ca in parsed.get("ca") or []:
                if not isinstance(ca, dict) or not ca.get("t"):
                    continue
                t = str(ca["t"])
                ca_map[t] = ca
                a = str(ca.get("a") or "")
                if a in READY:
                    sel.add(t)
                elif a == "AVOID":
                    avoid.add(t)
                elif a in MENTION:
                    mention.add(t)
            fmt = "compact"
        else:
            for t in parsed.get("trade_ready") or []:
                sel.add(str(t))
            for t in parsed.get("watchlist") or []:
                mention.add(str(t))
            for t in (parsed.get("veto") or {}):
                avoid.add(str(t))
            fmt = "legacy"
        norm_tr = set(str(t) for t in (norm.get("trade_ready") or []))
        wceil = set(str(t) for t in (norm.get("selection_evidence_watch_ceiling_tickers") or []))
        resp = sel | avoid | mention | set(ca_map)
        pool = set(lines) | resp
        resp_only = len(resp - set(lines))
        if not pool or pool <= FIXTURE:
            skip["test_fixture_or_empty"] += 1
            continue
        cid = f"{mkt}_{d.get('call_id')}"
        calls.append(dict(call_id=cid, market=mkt, ts_kst=ts.isoformat(), local_ts=lt.isoformat(),
                          session_date=sdate, model=d.get("model"), prompt_version=d.get("prompt_version"),
                          phase=phase, mode=mode, fmt=fmt, n_lines=len(lines), n_pool=len(pool),
                          n_resp_only=resp_only, n_sel=len(sel), n_norm_tr=len(norm_tr),
                          n_avoid=len(avoid)))
        for t in pool:
            ca = ca_map.get(t, {})
            lf = lines.get(t, {})
            ceil_w = (lf.get("ceil") == "WATCH") or (str(ca.get("ceil") or "") == "WATCH") or (t in wceil)
            if t in sel:
                lab = "SEL"
            elif t in avoid:
                lab = "AVOID"
            elif t in mention:
                lab = "MENTION"
            else:
                lab = "UNMENTIONED"
            pt = ca.get("pt") or {}
            r = dict(source="raw", call_id=cid, market=mkt, ticker=t, local_ts=lt.isoformat(), session_date=sdate,
                     label=lab, norm_sel=int(t in norm_tr), ceil_watch=int(ceil_w), in_lines=int(t in lines),
                     action=str(ca.get("a") or ""), strat=str(ca.get("s") or ""),
                     c=ca.get("c"), pt_conf=pt.get("conf") if isinstance(pt, dict) else None,
                     mat=str(ca.get("mat") or ""), fr=str(ca.get("fr") or ""), rc=str(ca.get("rc") or ""),
                     n_blk=len(ca.get("blk") or []) if isinstance(ca.get("blk"), list) else None,
                     model=d.get("model"), prompt_version=d.get("prompt_version"), mode=mode,
                     n_lines=len(lines), n_sel_call=len(sel))
            r.update(lf)
            rows.append(r)
    log("raw files", len(files), "skip", dict(skip), "calls kept", len(calls), "cand rows", len(rows))
    return pd.DataFrame(rows), pd.DataFrame(calls), dict(skip)


# ------------------------------------------------------------------ 추출: 감사 DB

def audit_conn():
    return contextlib.closing(sqlite3.connect(f"file:{AUDIT_DB.as_posix()}?mode=ro", uri=True, timeout=10))


def extract_audit():
    q_era = """
    select r.market, r.ticker, r.session_date, c.called_at, c.label, c.model, r.call_id,
           r.in_prompt, r.claude_watchlist, r.claude_trade_ready, r.claude_action, r.claude_veto_reason,
           r.change_pct, r.volume_ratio, r.turnover, r.price, r.liquidity_bucket, r.trainer_tier, r.from_high_pct,
           r.known_at, r.runtime_mode
    from audit_candidate_rows r join audit_claude_calls c on c.call_id = r.call_id
    where r.session_date < '2026-05-15' and r.runtime_mode='live'
      and (c.label in ('select_tickers','select_tickers_retry')
           or (c.label='selection_meta_live' and r.session_date >= '2026-05-11'))
    """
    q_ovl = """
    select r.market, r.ticker, r.session_date, max(r.claude_trade_ready),
           max(case when r.claude_action in ('BUY_READY','PROBE_READY','TRADE_READY','ADD_READY') then 1 else 0 end),
           max(case when r.in_prompt=1 or r.claude_watchlist=1 or r.claude_trade_ready=1 or r.claude_action!='' then 1 else 0 end)
    from audit_candidate_rows r join audit_claude_calls c on c.call_id = r.call_id
    where r.session_date between '2026-05-15' and '2026-07-08' and c.label='selection_meta_live' and r.runtime_mode='live'
    group by 1,2,3
    """
    with audit_conn() as con:
        era = pd.read_sql_query(q_era, con)
        ovl = pd.read_sql_query(q_ovl, con)
    ovl.columns = ["market", "ticker", "session_date", "a_trade_ready", "a_ready_action", "a_pool"]
    log("audit era rows", len(era), "overlap keys", len(ovl))
    return era, ovl


def audit_era_rows(era: pd.DataFrame) -> pd.DataFrame:
    era = era[(era.in_prompt == 1) | (era.claude_watchlist == 1) | (era.claude_trade_ready == 1)
              | (era.claude_action.fillna("") != "")].copy()
    out = []
    for r in era.itertuples(index=False):
        try:
            ts = datetime.fromisoformat(str(r.called_at)[:19])
        except ValueError:
            ts = datetime.fromisoformat(str(r.known_at)[:19])
        lt = to_local(ts, r.market)
        act = str(r.claude_action or "")
        if r.claude_trade_ready == 1 or act in READY:
            lab = "SEL"
        elif act == "AVOID" or (r.claude_veto_reason not in (None, "")):
            lab = "AVOID"
        elif r.claude_watchlist == 1 or act in MENTION:
            lab = "MENTION"
        else:
            lab = "UNMENTIONED"
        tv = r.turnover
        out.append(dict(source="audit", call_id=f"{r.market}_{r.call_id}", market=r.market, ticker=str(r.ticker),
                        local_ts=lt.isoformat(), session_date=lt.date().isoformat(), audit_session=r.session_date,
                        label=lab, norm_sel=int(r.claude_trade_ready == 1), ceil_watch=0, in_lines=int(r.in_prompt == 1),
                        action=act, model=r.model or "", prompt_version="audit_" + str(r.label), mode="",
                        chg=r.change_pct, vol=r.volume_ratio, p=r.price,
                        log_turn=math.log10(tv) if tv and tv > 0 else None,
                        liq=r.liquidity_bucket, trainer_tier=r.trainer_tier, from_high=r.from_high_pct))
    df = pd.DataFrame(out)
    if len(df):
        # 호출별 선택 수·줄 수
        g = df.groupby("call_id")
        df["n_sel_call"] = g["label"].transform(lambda s: int((s == "SEL").sum()))
        df["n_lines"] = g["in_lines"].transform("sum")
    return df


# ------------------------------------------------------------------ 중복 제거

def dedup(cand: pd.DataFrame) -> pd.DataFrame:
    cand = cand.sort_values("local_ts")
    recs = []
    for (m, t, s), g in cand.groupby(["market", "ticker", "session_date"], sort=False):
        first = g.iloc[0]
        sel = g[g.label == "SEL"]
        ever_sel = len(sel) > 0
        base = sel.iloc[0] if ever_sel else first
        rec = base.to_dict()
        rec.update(
            n_exposures=len(g), n_calls=g.call_id.nunique(), ever_sel=int(ever_sel),
            ever_avoid=int((g.label == "AVOID").any()), ever_mention=int((g.label == "MENTION").any()),
            first_label=first.label, first_ts=first.local_ts, decision_ts=base.local_ts,
            pool_perm=int((g.ceil_watch == 0).any()), ever_norm_sel=int(g.norm_sel.max() == 1),
            sel_under_ceil=int(((g.label == "SEL") & (g.ceil_watch == 1)).any()),
        )
        # 비선택 세부 라벨: AVOID > MENTION > UNMENTIONED
        if ever_sel:
            rec["grp"] = "SEL"
        elif rec["ever_avoid"]:
            rec["grp"] = "AVOID"
        elif rec["ever_mention"]:
            rec["grp"] = "MENTION"
        else:
            rec["grp"] = "UNMENTIONED"
        recs.append(rec)
    return pd.DataFrame(recs)


# ------------------------------------------------------------------ 가격

def load_matrix(market, cal_symbol):
    cal = pd.read_csv(PRICE[market] / f"{market.lower()}_{cal_symbol}.csv", encoding="utf-8-sig")
    cal = cal[(cal.date >= "2026-01-01")]
    dates = list(cal.date)
    idx = {d: i for i, d in enumerate(dates)}
    files = sorted(PRICE[market].glob(f"{market.lower()}_*.csv"))
    tick = [f.stem.split("_", 1)[1] for f in files]
    T, N = len(dates), len(files)
    O = np.full((T, N), np.nan, dtype=np.float32)
    C = np.full((T, N), np.nan, dtype=np.float32)
    V = np.full((T, N), np.nan, dtype=np.float32)
    for j, f in enumerate(files):
        try:
            df = pd.read_csv(f, encoding="utf-8-sig", usecols=["date", "open", "close", "volume"])
        except Exception:
            continue
        df = df[df.date >= "2026-01-01"]
        ii = df.date.map(idx)
        ok = ii.notna()
        ii = ii[ok].astype(int).values
        O[ii, j] = df.open[ok].values
        C[ii, j] = df.close[ok].values
        V[ii, j] = df.volume[ok].values
    O[O <= 0] = np.nan
    log(market, "matrix", T, "dates x", N, "tickers")
    return dates, {t: j for j, t in enumerate(tick)}, O, C, V


class Mkt:
    def __init__(self, market):
        self.m = market
        cal = "229200" if market == "KR" else "IWM"
        self.dates, self.col, self.O, self.C, self.V = load_matrix(market, cal)
        self.didx = {d: i for i, d in enumerate(self.dates)}
        self.opens = [open_close(market, datetime.fromisoformat(d))[0] for d in self.dates]
        dv = self.C * self.V
        # 결정 세션 이전 20봉 평균(행 i = 날짜 i 이전 20봉)
        dvs = pd.DataFrame(dv).rolling(20, min_periods=20).mean().shift(1).values
        hist = pd.DataFrame(~np.isnan(self.C)).cumsum().shift(1).fillna(0).values
        prevc = pd.DataFrame(self.C).shift(1).values
        self.dv20 = dvs
        pmin, dmin = (1000.0, 2e9) if market == "KR" else (5.0, 2e6)
        self.elig = (hist >= 25) & (prevc >= pmin) & (dvs >= dmin)
        self._edges = {}
        self._bench = {}
        self.benches = ["229200", "069500"] if market == "KR" else ["IWM"]

    def session_row(self, sdate):
        """결정 세션 날짜 → 그 날짜 이상 첫 거래일 행(휴장일 결정 대비)."""
        i = bisect.bisect_left(self.dates, sdate)
        return i if i < len(self.dates) else None

    def entry_idx(self, lt: datetime):
        i = bisect.bisect_right(self.opens, lt)
        return i if i < len(self.dates) else None

    def split_ok(self, j, a, b):
        c = self.C[max(a - 1, 0): b + 1, j]
        c = c[~np.isnan(c)]
        if len(c) < 2:
            return True
        r = c[1:] / c[:-1]
        return bool(((r < 2.5) & (r > 0.4)).all())

    def decile_edges(self, row):
        if row not in self._edges:
            v = self.dv20[row][self.elig[row]]
            self._edges[row] = np.quantile(v, np.linspace(0.1, 0.9, 9)) if len(v) >= 50 else None
        return self._edges[row]

    def decile(self, row, j):
        e = self.decile_edges(row)
        v = self.dv20[row, j]
        if e is None or np.isnan(v):
            return None
        return int(np.searchsorted(e, v))

    def ew_decile(self, row, a, b, dec, kind="oo"):
        key = (row, a, b, dec, kind)
        if key in self._bench:
            return self._bench[key]
        e = self.decile_edges(row)
        el = self.elig[row].copy()
        dv = self.dv20[row]
        dd = np.searchsorted(e, np.nan_to_num(dv, nan=-1))
        el &= dd == dec
        P0, P1 = (self.O[a], self.O[b]) if kind == "oo" else (self.C[a], self.C[b])
        r = P1 / P0 - 1
        ok = el & ~np.isnan(r)
        # 분할 필터(벡터)
        c = self.C[max(a - 1, 0): b + 1]
        with np.errstate(invalid="ignore", divide="ignore"):
            rr = c[1:] / c[:-1]
        bad = np.nanmax(np.where(np.isnan(rr), 1, rr), axis=0) >= 2.5
        bad |= np.nanmin(np.where(np.isnan(rr), 1, rr), axis=0) <= 0.4
        ok &= ~bad
        val = float(np.mean(r[ok]) * 100) if ok.sum() >= 5 else np.nan
        self._bench[key] = val
        return val

    def sym_ret(self, sym, a, b, kind="oo"):
        j = self.col.get(sym)
        if j is None:
            return np.nan
        P = self.O if kind == "oo" else self.C
        return float((P[b, j] / P[a, j] - 1) * 100)

    def ctx(self, row, sym):
        """결정 세션 전일 종가까지 벤치 1일·5일 수익률."""
        j = self.col.get(sym)
        if j is None or row is None or row < 6:
            return np.nan, np.nan
        c = self.C[:, j]
        return float((c[row - 1] / c[row - 2] - 1) * 100), float((c[row - 1] / c[row - 6] - 1) * 100)


def compute_outcomes(dec: pd.DataFrame) -> pd.DataFrame:
    mk = {m: Mkt(m) for m in ("KR", "US")}
    out = []
    drops = defaultdict(int)
    for r in dec.itertuples(index=False):
        M = mk[r.market]
        tk = str(r.ticker)
        j = M.col.get(tk)
        rec = dict(market=r.market, ticker=tk, session_date=r.session_date)
        if j is None:
            drops["no_price_file"] += 1
            out.append(rec)
            continue
        lt = datetime.fromisoformat(r.decision_ts)
        srow = M.session_row(r.session_date)
        e = M.entry_idx(lt)
        rec["entry_date"] = M.dates[e] if e is not None else None
        prim = M.benches[0]
        c1, c5 = M.ctx(srow, prim)
        rec["ctx_ret1"], rec["ctx_ret5"] = c1, c5
        if srow is not None:
            rec["size_decile"] = M.decile(srow, j)
        o, cl = open_close(r.market, datetime.fromisoformat(r.session_date))
        rec["min_since_open"] = (lt - o).total_seconds() / 60
        for h in HS:
            for kind in ("oo", "cc"):
                if kind == "oo":
                    a = e
                else:
                    # S1: 결정 세션 종가 진입, 마감 10분 전 이전 결정만
                    a = M.didx.get(r.session_date)
                    if a is None or lt >= cl - timedelta(minutes=10):
                        continue
                if a is None or a + h >= len(M.dates):
                    if kind == "oo":
                        drops[f"no_window_h{h}"] += 1
                    continue
                b = a + h
                P = M.O if kind == "oo" else M.C
                if np.isnan(P[a, j]) or np.isnan(P[b, j]):
                    if kind == "oo":
                        drops[f"missing_bar_h{h}"] += 1
                    continue
                if not M.split_ok(j, a, b):
                    if kind == "oo":
                        drops[f"split_filter_h{h}"] += 1
                    continue
                g = float((P[b, j] / P[a, j] - 1) * 100)
                net = g - COST[r.market]
                sfx = f"{kind}{h}"
                rec[f"gross_{sfx}"] = g
                rec[f"net_{sfx}"] = net
                for s in M.benches:
                    rec[f"ex_{s}_{sfx}"] = net - M.sym_ret(s, a, b, kind)
                dcl = rec.get("size_decile")
                if dcl is not None and srow is not None:
                    bv = M.ew_decile(srow, a, b, dcl, kind)
                    rec[f"ex_size_{sfx}"] = net - bv if not np.isnan(bv) else np.nan
                rec[f"exit_date_{sfx}"] = M.dates[b]
        out.append(rec)
    log("outcome drops", dict(drops))
    return pd.DataFrame(out), dict(drops)


# ------------------------------------------------------------------ 통계

def nw_t(x, lag):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 5:
        return np.nan, np.nan, n
    mu = x.mean()
    e = x - mu
    v = (e @ e) / n
    for l in range(1, lag + 1):
        if l >= n:
            break
        v += 2 * (1 - l / (lag + 1)) * (e[l:] @ e[:-l]) / n
    se = math.sqrt(v / n) if v > 0 else np.nan
    return mu, mu / se if se else np.nan, n


def day_series(df, ycol, gcol, a_val, b_val):
    """하루 = a 평균 − b 평균 (둘 다 있는 날만)."""
    s = []
    for (m, d), g in df.groupby(["market", "session_date"]):
        ya = g.loc[g[gcol].isin(a_val), ycol].dropna()
        yb = g.loc[g[gcol].isin(b_val), ycol].dropna()
        if len(ya) and len(yb):
            s.append((d, ya.mean() - yb.mean(), len(ya), len(yb)))
    return pd.DataFrame(s, columns=["date", "diff", "na", "nb"]).sort_values("date")


def summarize(ds: pd.DataFrame, h, col="diff"):
    lag = 4 if h == 5 else 0
    mu, t, n = nw_t(ds[col].values, lag)
    if n < 5:
        return dict(n_days=n, mean=None, t=None)
    med = ds.date.iloc[len(ds) // 2]
    h1 = ds[ds.date < med][col]
    h2 = ds[ds.date >= med][col]
    return dict(n_days=int(n), mean=round(mu, 3), t=round(t, 2),
                first_half=round(float(h1.mean()), 3), second_half=round(float(h2.mean()), 3),
                t_h1=round(nw_t(h1.values, lag)[1], 2), t_h2=round(nw_t(h2.values, lag)[1], 2),
                split_date=med, n_a=int(ds.na.sum()), n_b=int(ds.nb.sum()))


def level_series(df, ycol, mask):
    s = df[mask].groupby(["market", "session_date"])[ycol].mean().dropna().reset_index()
    s = s.rename(columns={"session_date": "date", ycol: "diff"})
    s["na"], s["nb"] = 1, 0
    return s.sort_values("date")


def verdict(res):
    if res.get("t") is None:
        return "무효(표본)"
    same = (res["first_half"] > 0) == (res["second_half"] > 0) == (res["mean"] > 0)
    if abs(res["t"]) >= 3 and same:
        return "통과" if res["mean"] > 0 else "통과(역방향)"
    if abs(res["t"]) >= 2 and same:
        return "후보" if res["mean"] > 0 else "후보(역방향)"
    return "기각"


def primary_col(market, h, kind="oo"):
    return f"ex_size_{kind}{h}" if market == "KR" else f"ex_IWM_{kind}{h}"


# ------------------------------------------------------------------ R2

def run_r2(D: pd.DataFrame):
    res = {}
    for m in ("KR", "US"):
        Dm = D[D.market == m].copy()
        for h in HS:
            pc = primary_col(m, h)
            Dm["y"] = Dm[pc]
            key = f"{m}_h{h}"
            perm = Dm[Dm.pool_perm == 1]
            r = {}
            r["main_sel_minus_notsel_perm"] = summarize(day_series(perm, "y", "grp", ["SEL"], ["AVOID", "MENTION", "UNMENTIONED"]), h)
            r["avoid_minus_mention"] = summarize(day_series(Dm, "y", "grp", ["AVOID"], ["MENTION"]), h)
            r["sel_minus_notsel_allpool"] = summarize(day_series(Dm, "y", "grp", ["SEL"], ["AVOID", "MENTION", "UNMENTIONED"]), h)
            Dm["p2"] = np.where(Dm.first_label == "SEL", "SEL", "NOT")
            r["P2_firstlabel_sel_minus_not"] = summarize(day_series(Dm[Dm.pool_perm == 1], "y", "p2", ["SEL"], ["NOT"]), h)
            Dm["y_s1"] = Dm[primary_col(m, h, "cc")] if primary_col(m, h, "cc") in Dm else np.nan
            r["S1_close_entry_sel_minus_notsel_perm"] = summarize(day_series(perm.assign(y_s1=Dm.loc[perm.index, "y_s1"]), "y_s1", "grp", ["SEL"], ["AVOID", "MENTION", "UNMENTIONED"]), h)
            r["sel_minus_mention"] = summarize(day_series(Dm, "y", "grp", ["SEL"], ["MENTION"]), h)
            # 선택 수준(벤치별)
            lv = {}
            for bc in [c for c in Dm.columns if c.startswith("ex_") and c.endswith(f"_oo{h}")] + [f"net_oo{h}"]:
                lv[bc] = summarize(level_series(Dm, bc, Dm.grp == "SEL"), h)
            lv_ns = {}
            for bc in [c for c in Dm.columns if c.startswith("ex_") and c.endswith(f"_oo{h}")]:
                lv_ns[bc] = summarize(level_series(Dm, bc, (Dm.grp != "SEL") & (Dm.pool_perm == 1)), h)
            r["sel_level"] = lv
            r["notsel_perm_level"] = lv_ns
            # confidence 구간(선택 내부, 수준)
            sel = Dm[Dm.grp == "SEL"].copy()
            cb = pd.cut(pd.to_numeric(sel.c, errors="coerce"), [-1, 0.549, 0.649, 2], labels=["c<0.55", "0.55-0.64", "c>=0.65"])
            sel["cb"] = cb.astype(str)
            conf = {}
            for b in ["c<0.55", "0.55-0.64", "c>=0.65", "nan"]:
                s = sel[sel.cb == b]
                conf[b] = dict(n=int(s.y.notna().sum()), row_mean=round(float(s.y.mean()), 3) if len(s) else None,
                               day=summarize(level_series(sel, "y", sel.cb == b), h))
            conf["hi_minus_lo_day"] = summarize(day_series(sel, "y", "cb", ["c>=0.65"], ["c<0.55", "0.55-0.64"]), h)
            r["confidence"] = conf
            act = {}
            for a in ["BUY_READY", "PROBE_READY", "TRADE_READY", ""]:
                s = sel[sel.action == a]
                act[a or "(legacy/none)"] = dict(n=int(s.y.notna().sum()), row_mean=round(float(s.y.mean()), 3) if len(s) else None)
            r["action_type"] = act
            r["verdict_main"] = verdict(r["main_sel_minus_notsel_perm"])
            r["verdict_avoid"] = verdict(r["avoid_minus_mention"])
            res[key] = r
    return res


# ------------------------------------------------------------------ T7

CAT = ["market", "liq", "news_signal", "trainer_tier", "ceil", "ma60", "action", "strat", "mat", "fr", "rc",
       "model", "prompt_version", "mode"]
NUMF = ["chg", "p", "vol", "log_turn", "from_high", "atr", "news_score", "news_count", "trainer_q", "rs",
        "c", "pt_conf", "n_blk", "n_lines", "n_sel_call", "min_since_open", "ctx_ret1", "ctx_ret5", "size_decile"]


def run_t7(D: pd.DataFrame):
    import lightgbm as lgb
    S = D[D.grp == "SEL"].copy()
    res = {}
    for h in HS:
        S["y_ex"] = np.where(S.market == "KR", S[f"ex_size_oo{h}"], S[f"ex_IWM_oo{h}"])
        S["exit_date"] = S[f"exit_date_oo{h}"]
        T = S.dropna(subset=["y_ex", "exit_date"]).copy()
        T["y"] = (T.y_ex < 0).astype(int)
        T["p"] = pd.to_numeric(T.get("p"), errors="coerce")
        for c in NUMF:
            T[c] = pd.to_numeric(T.get(c), errors="coerce") if c in T else np.nan
        for c in CAT:
            T[c] = T[c].fillna("").astype(str) if c in T else ""
        cats = {c: sorted(T[c].unique()) for c in CAT}
        X = T[CAT + NUMF].copy()
        for c in CAT:
            X[c] = pd.Categorical(X[c], categories=cats[c])
        wk = pd.to_datetime(T.session_date).dt.isocalendar()
        T["week"] = wk.year.astype(str) + "-" + wk.week.astype(str).str.zfill(2)
        weeks = sorted(T.week.unique())
        preds = []
        folds = []
        for w in weeks:
            test = T.week == w
            first_test = T.loc[test, "session_date"].min()
            train = (T.session_date < first_test) & (T.exit_date < first_test)
            ntw = T.loc[train, "week"].nunique()
            if ntw < 4 or train.sum() < 80:
                continue
            mdl = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=15, min_child_samples=20,
                                     subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=7,
                                     num_threads=2, verbose=-1)
            mdl.fit(X[train], T.loc[train, "y"])
            thr = float(np.median(mdl.predict_proba(X[train])[:, 1]))
            p = mdl.predict_proba(X[test])[:, 1]
            preds.append(pd.DataFrame({"i": T.index[test], "p_wrong": p, "thr": thr}))
            folds.append(dict(week=w, n_train=int(train.sum()), train_weeks=int(ntw), n_test=int(test.sum()),
                              thr=round(thr, 3), base_rate_train=round(float(T.loc[train, "y"].mean()), 3)))
        if not preds:
            res[f"h{h}"] = dict(error="no folds")
            continue
        P = pd.concat(preds).set_index("i")
        E = T.loc[P.index].copy()
        E["p_wrong"], E["thr"] = P.p_wrong, P.thr
        E["keep"] = np.where(E.p_wrong < E.thr, "KEEP", "DROP")
        cnum = pd.to_numeric(E.c, errors="coerce")
        E["base_keep"] = np.where(cnum.isna() | (cnum >= 0.60), "KEEP", "DROP")
        E["all"] = "ALL"
        from sklearn.metrics import roc_auc_score
        auc = roc_auc_score(E.y, E.p_wrong) if E.y.nunique() > 1 else None
        r = dict(folds=folds, n_eval=int(len(E)), n_days=int(E.groupby(["market", "session_date"]).ngroups),
                 eval_weeks=len(folds), auc=round(float(auc), 3) if auc is not None else None,
                 base_rate_wrong=round(float(E.y.mean()), 3),
                 keep_frac=round(float((E.keep == "KEEP").mean()), 3))
        r["keep_minus_drop"] = summarize(day_series(E, "y_ex", "keep", ["KEEP"], ["DROP"]), h)
        # 유지 − 전체: 하루 = 유지 평균 − 전체 평균
        rows = []
        for (m, d), g in E.groupby(["market", "session_date"]):
            k = g[g.keep == "KEEP"].y_ex
            if len(k):
                rows.append((d, k.mean() - g.y_ex.mean(), len(k), len(g)))
        r["keep_minus_all"] = summarize(pd.DataFrame(rows, columns=["date", "diff", "na", "nb"]).sort_values("date"), h)
        r["raw_level"] = summarize(level_series(E, "y_ex", E["all"] == "ALL"), h)
        r["kept_level"] = summarize(level_series(E, "y_ex", E.keep == "KEEP"), h)
        r["baseline_c060_keep_minus_drop"] = summarize(day_series(E, "y_ex", "base_keep", ["KEEP"], ["DROP"]), h)
        r["by_market"] = {m: dict(n=int((E.market == m).sum()),
                                  keep_mean=round(float(E[(E.market == m) & (E.keep == "KEEP")].y_ex.mean()), 3),
                                  drop_mean=round(float(E[(E.market == m) & (E.keep == "DROP")].y_ex.mean()), 3))
                          for m in ("KR", "US")}
        kd, ka = r["keep_minus_drop"], r["keep_minus_all"]
        ok = kd.get("t") is not None and kd["t"] >= 3 and ka.get("first_half", -1) > 0 and ka.get("second_half", -1) > 0
        cand = kd.get("t") is not None and kd["t"] >= 2 and ka.get("first_half", -1) > 0 and ka.get("second_half", -1) > 0
        r["verdict"] = "후보(shadow) — 한 국면 상한" if ok else ("약한 후보(t 2~3)" if cand else "기각")
        # 중요도
        mdl_all = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=15, min_child_samples=20,
                                     subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=7,
                                     num_threads=2, verbose=-1, importance_type="gain")
        mdl_all.fit(X, T.y)
        imp = sorted(zip(X.columns, mdl_all.feature_importances_), key=lambda x: -x[1])[:12]
        r["top_features_gain_fullsample"] = [(k, round(float(v), 1)) for k, v in imp]
        res[f"h{h}"] = r
        E[["market", "ticker", "session_date", "y_ex", "y", "p_wrong", "thr", "keep", "base_keep"]].to_csv(
            OUT / f"t7_eval_h{h}.csv", index=False)
    return res


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all", choices=["extract", "outcomes", "analyze", "all"])
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    cov = {}
    if a.stage in ("extract", "all"):
        cand, calls, skip = extract_raw()
        calls.to_csv(OUT / "calls_raw.csv", index=False)
        era, ovl = extract_audit()
        # 겹침 구간 대조: raw 선택 vs 감사 DB
        rd = dedup(cand)
        mg = rd.merge(ovl, on=["market", "ticker", "session_date"], how="inner")
        recon = {}
        for m in ("KR", "US"):
            g = mg[mg.market == m]
            recon[m] = dict(
                keys_overlap=int(len(g)), raw_keys=int((rd.market == m).sum()),
                agree_raw_vs_audit_tr=round(float((g.ever_sel == g.a_trade_ready).mean()), 4) if len(g) else None,
                agree_raw_vs_audit_readyaction=round(float((g.ever_sel == ((g.a_ready_action == 1) | (g.a_trade_ready == 1)).astype(int)).mean()), 4) if len(g) else None,
                agree_norm_vs_audit_tr=round(float((g.ever_norm_sel == g.a_trade_ready).mean()), 4) if len(g) else None,
                raw_sel=int(g.ever_sel.sum()), audit_tr=int(g.a_trade_ready.sum()), norm_sel=int(g.ever_norm_sel.sum()))
        log("reconcile", recon)
        ae = audit_era_rows(era)
        if len(ae):
            mism = ae[ae.session_date != ae.audit_session]
            recon["audit_session_date_mismatch_rows"] = int(len(mism))
        use_audit = all((recon[m]["agree_raw_vs_audit_readyaction"] or 0) >= 0.95 for m in ("KR", "US"))
        recon["use_audit_era"] = bool(use_audit)
        allc = pd.concat([cand, ae], ignore_index=True) if use_audit else cand
        dec = dedup(allc)
        dec.to_csv(OUT / "decisions.csv", index=False)
        cov = dict(skip=skip, reconcile=recon,
                   raw_calls_kept=int(len(calls)), raw_resp_only_total=int(calls.n_resp_only.sum()),
                   raw_candidate_exposures=int(len(cand)), audit_era_exposures=int(len(ae)),
                   exposures_used=int(len(allc)), dedup_rows=int(len(dec)),
                   dedup_ratio=round(len(allc) / max(len(dec), 1), 2))
        for m in ("KR", "US"):
            d = dec[dec.market == m]
            cov[m] = dict(rows=int(len(d)), days=int(d.session_date.nunique()),
                          first=d.session_date.min(), last=d.session_date.max(),
                          sel=int((d.grp == "SEL").sum()), avoid=int((d.grp == "AVOID").sum()),
                          mention=int((d.grp == "MENTION").sum()), unmentioned=int((d.grp == "UNMENTIONED").sum()),
                          pool_perm=int(d.pool_perm.sum()), sel_under_ceil=int(d.sel_under_ceil.sum()),
                          sel_raw_but_not_norm=int(((d.grp == "SEL") & (d.ever_norm_sel == 0)).sum()),
                          sel_days=int(d[d.grp == "SEL"].session_date.nunique()),
                          by_source=d.source.value_counts().to_dict())
        json.dump(cov, open(OUT / "coverage.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
        log(json.dumps(cov, ensure_ascii=False, default=str)[:3000])
    if a.stage in ("outcomes", "all"):
        dec = pd.read_csv(OUT / "decisions.csv", dtype={"ticker": str})
        dec.loc[dec.market == "KR", "ticker"] = dec.loc[dec.market == "KR", "ticker"].str.zfill(6)
        oc, drops = compute_outcomes(dec)
        oc.to_csv(OUT / "outcomes.csv", index=False)
        json.dump(drops, open(OUT / "outcome_drops.json", "w"), indent=1)
    if a.stage in ("analyze", "all"):
        dec = pd.read_csv(OUT / "decisions.csv", dtype={"ticker": str})
        dec.loc[dec.market == "KR", "ticker"] = dec.loc[dec.market == "KR", "ticker"].str.zfill(6)
        oc = pd.read_csv(OUT / "outcomes.csv", dtype={"ticker": str})
        oc.loc[oc.market == "KR", "ticker"] = oc.loc[oc.market == "KR", "ticker"].str.zfill(6)
        D = dec.merge(oc, on=["market", "ticker", "session_date"], how="left")
        r2 = run_r2(D)
        json.dump(r2, open(OUT / "r2_results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
        t7 = run_t7(D)
        json.dump(t7, open(OUT / "t7_results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
        log("done")


if __name__ == "__main__":
    sys.exit(main())
