# -*- coding: utf-8 -*-
"""KR 애널리스트 신규 커버(매수 의견) R3 — forward 쉐도우 수집기 + 가상 원장 (2026-09-30 운영자 지시 "수집기 만들어서 진행해").

주문 없음. 기록·정산·판정만. kis_api 는 토큰 캐시 읽기(load_token)·헤더 조립(_headers)만 쓴다 — 신규 토큰 발급 경로 금지.

동결 규칙(연구 코드를 그대로 복사 — 블록마다 출처 줄 번호. 연구 파일은 import 시 os.chdir·env 로딩을 하므로 import 하지 않는다)
출처: docs/reports/kr_target_price_20260930.md(v1~v3) · tools/research/research_kr_target_price_20260930.py
  - 엔드포인트 invest-opinion(FHKST663300C0), 100행 상한·창 분할(split_window, 연구 179~210행).
  - 정리: (종목, 증권사, 날짜)당 응답 첫 행(연구 cmd_build 57~88행).
  - 증권사 이름 정규화(연구 사전등록 v4, 2026-09-30 운영자 승인 옵션 B): `broker_map.json`(raw → canonical, 사명 변경·
    표시명 축약 11개)을 합본 단계에서 적용한 뒤 (종목, 정규 이름, 날짜)로 중복 제거. 맵에 없는 이름은 그대로 통과.
    이름이 바뀌어 '첫 등장'이 아니게 된 기존 원장 행은 지우지 않고 superseded(reason=broker_rename)로 표시.
  - R3: 증권사의 해당 종목 **데이터 내 첫 등장**(2018-04-02~, 백필+쉐도우 합본), 날짜 ≥ 2019-04-02,
    의견에 BUY_KEYS 포함(연구 54행·700~735행). 같은 종목·날짜는 1거래(set), 같은 날 신규 매수 커버 증권사 수 = n_brokers.
  - 진입 = D 이하 마지막 세션 i 의 다음 세션 시가, 청산 = i+20 세션 종가(H20), 비용 0.25% 왕복.
  - 적격(i) = 봉 있음·이력 ≥25봉·종가 ≥1,000·그날 거래대금 ≥20억·dvol20(20봉 전부 존재) 있음(연구 619~622행).
  - 벤치: dvol20 십분위(적격 집합 안정 정렬), 규모매칭 셀 ≥20, 커버리지(최근 90일 리포트 ≥1) 셀 ≥10(연구 635~668행).
    KOSPI200(069500)·KOSDAQ150(229200) 같은 창, 비용 없음.
  - 겹침 제거: 같은 종목 직전 채택 거래와 i 차이 < 20세션이면 제외(연구 776~787행).
  - 플라시보 A(v3 주): 같은 종목·같은 달력월·k > i, R1/R2/R3 신호 기준일 제외, 적격·수익·커버리지 벤치 있는 k 전부 평균
    (net_k − 커버리지 벤치_k). 후보 없으면 차이 계산 제외(연구 789~797행).
  - 층(보고용): 괴리 = max(goal/prdy_clpr) − 1, IS 삼분위 경계 +26.6% / +37.1%(eval3 출력, 소수 1자리 반올림값),
    시장(instiwatch corp_cls), 신규 매수 커버 증권사 수.
  가격: 신호 종목·플라시보 = 네이버 fchart 수정주가(buyback_shadow.naver_daily), 벤치 = data/price/kr(buyback_shadow.Book,
  ±30.5% 창 가드로 무수정 기업행위 제외 — 연구는 yf 수정주가라 해당 점프가 없었다).

판정(사전등록, 운영자 지시): 플라시보 차이 만기 ≥ 30건 AND 날짜 클러스터 t ≥ 2.0 → '소액 canary 검토' 메모만.

KIS 규칙: 16:05~23:59 KST, 22:10~22:35 대기(중단), 간격 ≥1.1초, 하루 2,500회, 연속 실패 5회 중단,
load_token None → 조용히 종료(신규 발급 금지), 비밀값 출력 금지.

저장: data/shadow/kr_invest_opinion/YYYYMM.jsonl(수집월, fetch_ts 포함) · state.json(호출수·종목별 최종 수집)
원장: data/shadow/kr_target_price_r3_shadow.jsonl(키 R3|종목|D) · 정산 kr_target_price_r3_shadow_settle.jsonl

사용:
  python tools/kr_target_price_shadow.py collect [--dry] [--max-tickers N] [--max-calls N]
  python tools/kr_target_price_shadow.py signals
  python tools/kr_target_price_shadow.py settle [--dry]
  python tools/kr_target_price_shadow.py report
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import csv
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import buyback_shadow as BS  # noqa: E402  (kr_is_session·kr_next_session·Book·naver_daily 재사용)

KST = timezone(timedelta(hours=9))
BACKFILL = ROOT / "data" / "analysis" / "kr_target_price_20260930"
BF_REPORTS = BACKFILL / "reports.csv"
BF_CKPT = BACKFILL / "checkpoint.json"
BF_RAW = BACKFILL / "raw"
SHADOW = ROOT / "data" / "shadow"
OPDIR = SHADOW / "kr_invest_opinion"
STATE = OPDIR / "state.json"
RUNS = OPDIR / "runs.jsonl"
LEDGER = SHADOW / "kr_target_price_r3_shadow.jsonl"
SETTLE = SHADOW / "kr_target_price_r3_shadow_settle.jsonl"
BROKER_MAP_PATH = BACKFILL / "broker_map.json"
KR_PX = ROOT / "data" / "price" / "kr"
INSTIWATCH = "file:E:/code/instiwatch/data/instiwatch.db?mode=ro"

# 연구 88~93행
PATH = "/uapi/domestic-stock/v1/quotations/invest-opinion"
TR_ID = "FHKST663300C0"
START = "20180401"
CAP = 100
GAP_SEC = 1.1
DAILY_CAP = 2500
MAX_CONSEC_FAIL = 5
WINDOW_DAYS = 7
DVOL_CATCH = 20.0          # 억원 — 첫 커버 포착용 유니버스 하한
H = 20
COST = 0.25
R3_MIN_DATE = "2019-04-02"
UP_EDGES = (0.266, 0.371)  # eval3 IS 삼분위 경계(+26.6% / +37.1%)
GATE_N, GATE_T = 30, 2.0
# 연구 54행
BUY_KEYS = ("매수", "Buy", "BUY", "buy", "Outperform", "OUTPERFORM", "비중확대")

# 테스트에서 교체
HTTP_GET = None            # None → requests.get
SLEEP = time.sleep
NOW = None                 # None → datetime.now(KST)
BROKER_MAP = None          # None → BROKER_MAP_PATH 로드(없으면 빈 맵 = 통과)


def now_kst() -> datetime:
    return NOW() if NOW else datetime.now(KST)


def log(msg: str) -> None:
    print(f"[TP-R3] {msg}", flush=True)


read_jsonl = BS.read_jsonl
write_jsonl = BS.write_jsonl


def atomic_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    for k in range(20):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.25 * (k + 1))
    os.replace(tmp, p)


# ─────────────────────────── 창 가드 ───────────────────────────
def window_state(t: datetime) -> str:
    """'ok' | 'skip'(22:10~22:35) | 'closed'(16:05 전 · 23:59 이후). 자정 넘으면 load_token 이 어차피 None."""
    hm = t.hour * 60 + t.minute
    # 2026-10-07: 아침 창 08:21~08:38 추가 — KIS가 리포트를 다음날 올려 16:15 수집만으로는 R3가 진입 시가 뒤에
    # 잡혀 전부 actionable=False였다(13건 중 11건). 08:20 아침 토큰 발급 뒤, canary·패닉 장전 주문(08:40~) 전에 끝낸다.
    if 8 * 60 + 21 <= hm < 8 * 60 + 38:
        return "ok"
    if hm < 16 * 60 + 5 or hm >= 23 * 60 + 59:
        return "closed"
    if 22 * 60 + 10 <= hm < 22 * 60 + 35:
        return "skip"
    return "ok"


# ─────────────────────────── 창 분할 (연구 179~210행 그대로) ───────────────────────────
def _d(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def _s(d: date) -> str:
    return d.strftime("%Y%m%d")


def split_window(d1: str, d2: str) -> list[list[str]]:
    a, b = _d(d1), _d(d2)
    if a >= b:
        return []
    if a.year != b.year:
        out = []
        for y in range(a.year, b.year + 1):
            s, e = max(a, date(y, 1, 1)), min(b, date(y, 12, 31))
            out.append([_s(s), _s(e)])
        return out
    qa, qb = (a.month - 1) // 3, (b.month - 1) // 3
    if qa != qb:
        out = []
        for q in range(qa, qb + 1):
            s = max(a, date(a.year, 3 * q + 1, 1))
            e = min(b, (date(a.year + 1, 1, 1) if q == 3 else date(a.year, 3 * q + 4, 1)) - timedelta(days=1))
            out.append([_s(s), _s(e)])
        return out
    if a.month != b.month:
        out = []
        for mth in range(a.month, b.month + 1):
            s = max(a, date(a.year, mth, 1))
            e = min(b, (date(a.year + 1, 1, 1) if mth == 12 else date(a.year, mth + 1, 1)) - timedelta(days=1))
            out.append([_s(s), _s(e)])
        return out
    mid = a + (b - a) // 2
    return [[_s(a), _s(mid)], [_s(mid + timedelta(days=1)), _s(b)]]


# ─────────────────────────── env / KIS (연구 213~224행) ───────────────────────────
def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=str(ROOT / ".env.live"), override=True)
    try:
        cfg = json.loads((ROOT / "config" / "v2_start_config.json").read_text(encoding="utf-8"))
        for k, v in (cfg.get("env_overrides") or {}).items():
            if str(k).startswith("KIS_"):
                os.environ[str(k)] = str(v)
    except Exception:
        pass


def _kis():
    _load_env()
    import kis_api  # noqa: E402

    return kis_api


class Stop(Exception):
    pass


# ─────────────────────────── 원자료 ───────────────────────────
def raw_key(code: str, r: dict) -> tuple:
    return (code, str(r.get("stck_bsop_date", "")), (r.get("mbcr_name") or "").strip(),
            str(r.get("hts_goal_prc", "")).strip(), (r.get("invt_opnn") or "").strip())


def load_shadow_rows() -> List[dict]:
    out = []
    for p in sorted(OPDIR.glob("[0-9]" * 6 + ".jsonl")):
        out.extend(read_jsonl(p))
    return out


def append_rows(code: str, rows: List[dict], seen: set, fetch_ts: str, src: str) -> int:
    """(code, date, broker, goal_prc, opinion) 중복 제거 후 수집월 파일에 추가. 추가 건수 반환."""
    new = []
    for r in rows:
        k = raw_key(code, r)
        if not k[1] or not k[2] or k in seen:
            continue
        seen.add(k)
        x = {kk: v for kk, v in r.items() if kk != "_w"}
        x.update({"code": code, "fetch_ts": fetch_ts, "src": src})
        new.append(x)
    if new:
        OPDIR.mkdir(parents=True, exist_ok=True)
        p = OPDIR / f"{fetch_ts[:4]}{fetch_ts[5:7]}.jsonl"
        with p.open("a", encoding="utf-8") as fh:
            for x in new:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
    return len(new)


def load_state() -> dict:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    st.setdefault("calls", {})
    st.setdefault("tickers", {})
    return st


def backfill_done() -> set:
    try:
        ck = json.loads(BF_CKPT.read_text(encoding="utf-8"))
        return {t for t, v in ck["tickers"].items() if v.get("status") == "done"}
    except Exception:
        return set()


def read_backfill_reports() -> List[tuple]:
    """reports.csv → (ticker, date, broker, target, opinion)."""
    out = []
    if not BF_REPORTS.exists():
        return out
    with BF_REPORTS.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                g = float(r["target"] or 0)
            except ValueError:
                g = 0.0
            out.append((r["ticker"], r["date"], r["broker"], g, r["opinion"] or ""))
    return out


def dvol20_price_kr() -> Dict[str, float]:
    """data/price/kr 최근 20봉 평균 거래대금(억) — 연구 universe(113~126행)와 같은 계산."""
    out = {}
    for p in KR_PX.glob("kr_*.csv"):
        bars = []
        with p.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.reader(fh):
                if len(r) >= 6 and r[0][:2] == "20":
                    try:
                        bars.append((r[0], float(r[4]), float(r[5])))
                    except ValueError:
                        pass
        bars.sort()
        last = bars[-20:]
        out[p.stem[3:]] = sum(c * v for _, c, v in last) / len(last) / 1e8 if last else 0.0
    return out


def build_universe(today: date, bf_reports: List[tuple], shadow_rows: List[dict], dv: Dict[str, float]) -> List[str]:
    """최근 365일 리포트가 있는 종목 ∪ dvol20 ≥ 20억 종목, dvol 내림차순."""
    lo = (today - timedelta(days=365)).isoformat()
    cov = {t for t, d, *_ in bf_reports if d >= lo}
    lo8 = lo.replace("-", "")
    cov |= {r["code"] for r in shadow_rows if str(r.get("stck_bsop_date", "")) >= lo8}
    uni = cov | {t for t, v in dv.items() if v >= DVOL_CATCH}
    return sorted(uni, key=lambda t: (-dv.get(t, 0.0), t))


# ─────────────────────────── collect ───────────────────────────
def cmd_collect(a) -> int:
    t0 = now_kst()
    today = t0.date()
    tstr = today.isoformat()
    ws = window_state(t0)
    st = load_state()
    bf = read_backfill_reports()
    shadow = load_shadow_rows()
    uni = build_universe(today, bf, shadow, dvol20_price_kr())
    if a.max_tickers:
        uni = uni[: a.max_tickers]
    done_bf = backfill_done()
    todo = [t for t in uni if st["tickers"].get(t, {}).get("last_fetch_date") != tstr]
    if a.dry:
        n_full = sum(1 for t in todo if t not in done_bf and not st["tickers"].get(t, {}).get("history_full"))
        tok = None
        try:
            tok = bool((_kis().load_token("KR") or {}).get("access_token"))
        except Exception as e:  # noqa: BLE001
            tok = f"load_error {type(e).__name__}"
        log(f"DRY now={t0.isoformat(timespec='seconds')} window={ws} universe={len(uni)} todo={len(todo)} "
            f"full_history_needed={n_full} calls_today={st['calls'].get(tstr, 0)}/{DAILY_CAP} token_cached={tok}")
        log(f"DRY 창={_s(today - timedelta(days=WINDOW_DAYS - 1))}~{_s(today)} top20={uni[:20]}")
        return 0
    if ws != "ok":
        log(f"STOP_WINDOW_{ws.upper()} {t0.strftime('%H:%M')} — 호출 0")
        return 0
    kis = _kis()
    import requests

    get = HTTP_GET or requests.get
    base = kis.get_kis_market_profile("KR").base_url
    seen = {raw_key(r["code"], r) for r in shadow}
    state = {"last": 0.0, "consec": 0}
    max_calls = min(DAILY_CAP, a.max_calls or DAILY_CAP)
    stats = {"calls": 0, "rows_new": 0, "tickers_done": 0, "tickers_err": 0, "full_hist": 0}

    def save():
        atomic_json(STATE, st)

    def call(code: str, d1: str, d2: str):
        ws_ = window_state(now_kst())
        if ws_ != "ok":
            raise Stop(f"STOP_WINDOW_{ws_.upper()}")
        used = st["calls"].get(tstr, 0)
        if used >= max_calls:
            raise Stop(f"STOP_DAILY_CAP used={used}")
        tok = kis.load_token("KR")
        token = (tok or {}).get("access_token") or ""
        if not str(token).strip():
            raise Stop("STOP_TOKEN_NONE")      # 신규 발급 금지 — _headers 에 빈 토큰을 넘기지 않는다
        wait = GAP_SEC - (time.monotonic() - state["last"])
        if wait > 0:
            SLEEP(wait)
        headers = kis._headers(token, TR_ID, "KR")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16633", "FID_INPUT_ISCD": code,
                  "FID_INPUT_DATE_1": d1, "FID_INPUT_DATE_2": d2}
        st["calls"][tstr] = used + 1
        stats["calls"] += 1
        try:
            resp = get(base + PATH, headers=headers, params=params, timeout=15)
            state["last"] = time.monotonic()
            resp.encoding = "utf-8"
            body = resp.json()
        except Exception as e:  # noqa: BLE001  (메시지에 헤더가 섞일 수 있어 타입명만)
            state["last"] = time.monotonic()
            return None, f"EXC {type(e).__name__}"
        msg = str(body.get("msg_cd", ""))
        if msg.startswith("EGW0012"):
            raise Stop(f"STOP_TOKEN_REJECTED {msg}")
        if resp.status_code != 200 or str(body.get("rt_cd")) != "0":
            if msg == "EGW00201":
                SLEEP(3)
            return None, f"http={resp.status_code} rt={body.get('rt_cd')} msg={msg}"
        out = body.get("output") or []
        if isinstance(out, dict):
            out = [out]
        return out, "ok"

    reason = "DONE_ALL"
    try:
        for code in todo:
            ts = st["tickers"].setdefault(code, {})
            full = code not in done_bf and not ts.get("history_full")
            pending = [[START, _s(today)]] if full else [[_s(today - timedelta(days=WINDOW_DAYS - 1)), _s(today)]]
            got, complete, fail = [], True, 0
            while pending:
                d1, d2 = pending[0]
                rows, info = call(code, d1, d2)
                if rows is None:
                    state["consec"] += 1
                    fail += 1
                    log(f"FAIL {code} {d1}-{d2} {info} consec={state['consec']}")
                    if state["consec"] >= MAX_CONSEC_FAIL:
                        raise Stop("STOP_CONSEC_FAIL")
                    if fail >= 3:
                        break
                    SLEEP(5)
                    continue
                state["consec"] = 0
                pending.pop(0)
                kids = split_window(d1, d2) if len(rows) >= CAP else []
                if len(rows) >= CAP and not kids:
                    complete = False
                if kids:
                    pending[0:0] = kids
                else:
                    got.extend(rows)
            if pending:                    # 실패 종료 — 이 종목 결과 버림(다음 실행에서 재시도)
                ts["last_error"] = now_kst().isoformat(timespec="seconds")
                stats["tickers_err"] += 1
                save()
                continue
            fts = now_kst().isoformat(timespec="seconds")
            stats["rows_new"] += append_rows(code, got, seen, fts, "full" if full else "inc7")
            ts["last_fetch_date"] = tstr
            ts["last_fetch_ts"] = fts
            if not complete:
                ts["incomplete_day_window"] = True
            if full:
                ts["history_full"] = True
                stats["full_hist"] += 1
            stats["tickers_done"] += 1
            save()
    except Stop as e:
        reason = str(e)
    save()
    rec = {"ts": now_kst().isoformat(timespec="seconds"), "reason": reason, "universe": len(uni), "todo": len(todo),
           **stats, "calls_today": st["calls"].get(tstr, 0)}
    OPDIR.mkdir(parents=True, exist_ok=True)
    with RUNS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log(f"{reason} 종목 {stats['tickers_done']}/{len(todo)} 오류 {stats['tickers_err']} 신규행 {stats['rows_new']} "
        f"호출 {stats['calls']} (오늘 누적 {rec['calls_today']}/{DAILY_CAP}) 전체이력 {stats['full_hist']}")
    return 0


# ─────────────────────────── 리포트 합본 · 신호 ───────────────────────────
def broker_map() -> Dict[str, str]:
    """raw mbcr_name → canonical(연구 사전등록 v4 동결 맵). 파일이 없거나 깨지면 빈 맵(원 이름 그대로)."""
    global BROKER_MAP
    if BROKER_MAP is None:
        try:
            BROKER_MAP = dict(json.loads(BROKER_MAP_PATH.read_text(encoding="utf-8"))["map"])
        except Exception as e:  # noqa: BLE001
            log(f"broker_map 로드 실패({type(e).__name__}) — 원 이름으로 진행")
            BROKER_MAP = {}
    return BROKER_MAP


def canon_broker(b: str, bmap: Optional[Dict[str, str]] = None) -> str:
    b = (b or "").strip()
    m = broker_map() if bmap is None else bmap
    return m.get(b, b)


def merge_reports(bf_reports: List[tuple], shadow_rows: List[dict], bmap: Optional[Dict[str, str]] = None) -> List[dict]:
    """연구 cmd_build(57~88행): (종목, 증권사, 날짜)당 첫 행. 백필 먼저, 쉐도우는 파일 순서.
    증권사는 정규 이름(v4)으로 바꾼 뒤 중복 제거한다. bmap={} 이면 원 이름 그대로(비교용)."""
    seen, out = set(), []
    for t, d, b0, g, op in bf_reports:
        b = canon_broker(b0, bmap)
        if (t, b, d) in seen:
            continue
        seen.add((t, b, d))
        out.append({"t": t, "d": d, "b": b, "b_raw": b0, "g": g, "op": op, "src": "backfill"})
    for r in shadow_rows:
        t = r["code"]
        d8 = str(r.get("stck_bsop_date", ""))
        b0 = (r.get("mbcr_name") or "").strip()
        b = canon_broker(b0, bmap)
        if not d8 or not b:
            continue
        d = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
        if (t, b, d) in seen:
            continue
        seen.add((t, b, d))
        try:
            g = float(r.get("hts_goal_prc") or 0)
        except ValueError:
            g = 0.0
        try:
            pc = float(r.get("stck_prdy_clpr") or 0)
        except ValueError:
            pc = 0.0
        out.append({"t": t, "d": d, "b": b, "b_raw": b0, "g": g, "op": (r.get("invt_opnn") or "").strip(), "src": "shadow",
                    "fetch_ts": r.get("fetch_ts"), "pc": pc})
    return out


def first_rows(reps: List[dict]) -> List[dict]:
    """연구 700~705행: (종목, 증권사)를 날짜순 정렬해 첫 행."""
    best: Dict[tuple, dict] = {}
    for r in reps:
        k = (r["t"], r["b"])
        if k not in best or r["d"] < best[k]["d"]:
            best[k] = r
    return list(best.values())


def is_buy(op: str) -> bool:
    return any(k in (op or "") for k in BUY_KEYS)


def r3_events(reps: List[dict]) -> Dict[tuple, List[dict]]:
    """연구 733~735행 + v3 init_n: (종목, D) → 그날 매수 의견 첫 등장 행들."""
    ev = defaultdict(list)
    for r in first_rows(reps):
        if r["d"] >= R3_MIN_DATE and is_buy(r["op"]):
            ev[(r["t"], r["d"])].append(r)
    return ev


def entry_sessions(D: str) -> tuple[str, str, str]:
    """기준 세션 i(D 이하 마지막 세션), 진입(i 다음 세션 시가), 청산 예정(i+H 세션 종가)."""
    base = BS.kr_prev_or_same(date.fromisoformat(D))
    ent = BS.kr_next_session(base)
    ex = ent
    for _ in range(H - 1):
        ex = BS.kr_next_session(ex)
    return base.isoformat(), ent.isoformat(), ex.isoformat()


def upside_tercile(u: Optional[float]) -> Optional[str]:
    if u is None:
        return None
    return "low" if u < UP_EDGES[0] else ("mid" if u < UP_EDGES[1] else "high")


def market_map() -> Dict[str, str]:
    try:
        with contextlib.closing(sqlite3.connect(INSTIWATCH, uri=True, timeout=10)) as c:
            return {r[0]: {"Y": "KOSPI", "K": "KOSDAQ"}.get(r[1], "기타") for r in c.execute(
                "SELECT stock_code, corp_cls FROM disclosures WHERE stock_code!='' GROUP BY stock_code")}
    except Exception:
        return {}


def build_signal_rows(reps: List[dict], history_ok: set, mkt: Dict[str, str], now: datetime) -> List[dict]:
    """쉐도우 원자료로 새로 드러난 R3 이벤트만 원장 행으로 만든다(순수 함수)."""
    out = []
    for (t, D), rows in sorted(r3_events(reps).items(), key=lambda kv: (kv[0][1], kv[0][0])):
        new = [r for r in rows if r["src"] == "shadow"]
        if not new:
            continue
        base, ent, ex = entry_sessions(D)
        det = min(r["fetch_ts"] for r in new if r.get("fetch_ts"))
        det_dt = datetime.fromisoformat(det)
        if det_dt.tzinfo is None:
            det_dt = det_dt.replace(tzinfo=KST)
        open_dt = datetime.fromisoformat(ent + "T09:00:00+09:00")
        ups = [r["g"] / r["pc"] - 1 for r in rows if r.get("pc") and r["g"] > 0 and r["pc"] > 0]
        up = max(ups) if ups else None
        out.append({
            "key": f"R3|{t}|{D}", "rule": "R3", "ticker": t, "signal_date": D,
            "brokers": sorted(r["b"] for r in rows), "brokers_raw": sorted(r.get("b_raw", r["b"]) for r in rows),
            "opinions": [r["op"] for r in rows],
            "targets": [r["g"] for r in rows], "n_brokers": len(rows),
            "upside": round(up, 4) if up is not None else None, "upside_tercile": upside_tercile(up),
            "market": mkt.get(t, "기타"), "base_session": base, "entry_date": ent, "exit_date_planned": ex,
            "detected_ts": det, "actionable": det_dt < open_dt, "history_ok": t in history_ok,
            "created_ts": now.isoformat(timespec="seconds"), "superseded": False,
        })
    return out


def history_ok_set(st: dict) -> set:
    return backfill_done() | {t for t, v in st.get("tickers", {}).items() if v.get("history_full")}


def cmd_signals(_a) -> int:
    now = now_kst()
    bf, sh = read_backfill_reports(), load_shadow_rows()
    reps = merge_reports(bf, sh)
    hok, mkt = history_ok_set(load_state()), market_map()
    new = build_signal_rows(reps, hok, mkt, now)
    cur = {r["key"] for r in new}
    # 원 이름으로는 이벤트인데 정규 이름으로는 아닌 키 = 사명 변경 인공물
    raw_keys = {r["key"] for r in build_signal_rows(merge_reports(bf, sh, bmap={}), hok, mkt, now)}
    old = {r["key"]: r for r in read_jsonl(LEDGER)}
    added = []
    for r in new:
        if r["key"] not in old:
            old[r["key"]] = r
            added.append(r)
        elif old[r["key"]].get("superseded") and r["key"] in cur:
            old[r["key"]]["superseded"] = False
            old[r["key"]].pop("superseded_reason", None)
    # 더 이른 날짜의 같은 증권사 행이 뒤늦게 들어와 '첫 등장'이 아니게 된 이벤트는 지우지 않고 표시만 한다
    for k, r in old.items():
        if k not in cur and not r.get("superseded"):
            r["superseded"] = True
            r["superseded_ts"] = now.isoformat(timespec="seconds")
            r["superseded_reason"] = "broker_rename" if k in raw_keys else "earlier_row"
    write_jsonl(LEDGER, sorted(old.values(), key=lambda r: (r["signal_date"], r["ticker"])))
    for r in added:
        log(f"NEW {r['key']} 증권사 {r['brokers']} 의견 {r['opinions']} 괴리 {r['upside']} ({r['upside_tercile']}) "
            f"{r['market']} 진입 {r['entry_date']} 청산예정 {r['exit_date_planned']} actionable={r['actionable']} "
            f"history_ok={r['history_ok']}")
    log(f"신규 {len(added)}건 · 원장 {len(old)}건 (superseded {sum(1 for r in old.values() if r.get('superseded'))})")
    return 0


# ─────────────────────────── settle ───────────────────────────
def signal_base_idx(reps: List[dict], cal: List[str]) -> Dict[str, set]:
    """R1/R2/R3 신호 기준일(플라시보 제외용). 연구 697~735행 그대로(pandas 없이)."""
    def base_idx(dstr):
        k = bisect.bisect_right(cal, dstr) - 1
        return k if k >= 0 else None

    by_tb = defaultdict(list)
    for r in reps:
        by_tb[(r["t"], r["b"])].append(r)
    rev_rows, frs = [], []
    for (t, b), g in by_tb.items():
        g.sort(key=lambda r: r["d"])
        prev_d, prev_g, first = None, None, True
        for r in g:
            d, tg, op = r["d"], r["g"], r["op"]
            if first:
                frs.append((t, d, b, op))
                first = False
            if tg and tg > 0:
                if prev_g and (datetime.fromisoformat(d) - datetime.fromisoformat(prev_d)).days <= 365:
                    rv_ = tg / prev_g - 1
                    if abs(rv_) <= 0.60:
                        rev_rows.append((t, d, rv_))
                prev_d, prev_g = d, tg
    daily = defaultdict(list)
    for t, d, rv_ in rev_rows:
        daily[(t, d)].append(rv_)
    out = defaultdict(set)
    by_t = defaultdict(dict)
    for (t, d), rs in daily.items():
        by_t[t][d] = (sum(1 for x in rs if x > 0), sum(1 for x in rs if x < 0), sum(rs) / len(rs))
    for t, dd in by_t.items():
        net_by_i = defaultdict(int)
        for d, (u, dn, mr) in dd.items():
            k = base_idx(d)
            if k is not None:
                net_by_i[k] += u - dn
            if mr >= 0.10 and k is not None:          # R2
                out[t].add(k)
        ks = sorted(net_by_i)
        prev_true = False
        for k in range(ks[0], ks[-1] + 1) if ks else []:   # R1
            cond = sum(net_by_i.get(x, 0) for x in range(k - 4, k + 1)) >= 2
            if cond and not prev_true and k in net_by_i:
                out[t].add(k)
            prev_true = cond
    for t, d, b, op in frs:                                  # R3
        if d >= R3_MIN_DATE and is_buy(op):
            k = base_idx(d)
            if k is not None:
                out[t].add(k)
    return out


class Bench:
    """연구 619~668행 정의를 Book 행렬(data/price/kr)에 적용: 적격·dvol20 십분위·규모매칭(≥20)·커버리지(≥10)."""

    def __init__(self, bk, reps: List[dict]):
        import numpy as np
        import pandas as pd

        self.bk, self.np = bk, np
        C, O, A = bk.c, bk.o, bk.a
        cal = bk.cal
        nD, nT = C.shape
        have = ~np.isnan(C)
        nbars = np.cumsum(have, axis=0)
        dv20 = pd.DataFrame(A).rolling(20, min_periods=20).mean().to_numpy() / 1e8
        with np.errstate(invalid="ignore"):
            self.elig = have & (nbars >= 25) & (C >= 1000) & (A / 1e8 >= 20) & ~np.isnan(dv20)
        self.dv20 = dv20
        R = np.full_like(C, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            R[: nD - H] = (C[H:] / O[1: nD - H + 1] - 1) * 100 - COST
        # 창 가드: 무수정 기업행위 점프(±30.5%) 포함 창 제외
        bad = np.zeros_like(R, dtype=bool)
        if nD > H:
            bad[: nD - H] = (bk.badcum[H:] - bk.badcum[: nD - H]) > 0
        R[bad] = np.nan
        self.RET = R
        DEC = np.full((nD, nT), -1, dtype=int)
        for i in range(nD):
            js = np.where(self.elig[i])[0]
            if len(js):
                order = js[np.argsort(dv20[i, js], kind="stable")]
                DEC[i, order] = np.minimum(9, np.arange(len(order)) * 10 // len(order))
        self.DEC = DEC
        COV = np.zeros((nD, nT), dtype=bool)
        dates_by_t = defaultdict(set)
        for r in reps:
            dates_by_t[r["t"]].add(r["d"])
        for t, ds in dates_by_t.items():
            j = bk.col.get(t)
            if j is None:
                continue
            for d in ds:
                k0 = bisect.bisect_left(cal, d)
                end = (datetime.fromisoformat(d) + timedelta(days=89)).strftime("%Y-%m-%d")
                k1 = bisect.bisect_right(cal, end)
                COV[k0:k1, j] = True
        self.COV = COV
        self.SZ = self._bench(np.ones_like(COV), 20)
        self.CB = self._bench(COV, 10)

    def _bench(self, mask, minn):
        np = self.np
        nD = self.RET.shape[0]
        B = np.full((nD, 10), np.nan)
        for i in range(nD - H):
            for dcl in range(10):
                sel = (self.DEC[i] == dcl) & mask[i]
                v = self.RET[i, sel]
                v = v[~np.isnan(v)]
                if len(v) >= minn:
                    B[i, dcl] = v.mean()
        return B


def naver_val(nb: dict, cal: List[str], i: int) -> Optional[float]:
    """기준 i: i+1 시가 → i+H 종가 − 비용 (네이버 수정주가)."""
    if i + H >= len(cal):
        return None
    a, b = nb.get(cal[i + 1]), nb.get(cal[i + H])
    if not a or not b or a[0] <= 0 or b[3] <= 0:
        return None
    return (b[3] / a[0] - 1) * 100 - COST


def settle_rows(ledger: List[dict], reps: List[dict], bk, bench: Bench, old: Dict[str, dict], naver) -> List[dict]:
    """만기 도달분 정산(주), 월 완료·플라시보 후보 전부 만기 후 플라시보 채움. 순수 함수(naver 주입)."""
    np = bench.np
    cal = bk.cal
    nD = len(cal)
    sig_i = signal_base_idx(reps, cal)
    k2, q2 = bk.col.get("069500"), bk.col.get("229200")

    def base_idx(dstr):
        k = bisect.bisect_right(cal, dstr) - 1
        return k if k >= 0 else None

    # 겹침 제거(연구 776~787행): 백필·쉐도우 R3 합본을 날짜순으로 돌려 채택 거래의 last_i 를 만든다
    evs = sorted(r3_events(reps), key=lambda x: (x[1], x[0]))
    first_d = min((r["signal_date"] for r in ledger), default=None)
    lo = (date.fromisoformat(first_d) - timedelta(days=60)).isoformat() if first_d else "9999"
    last_i, overlap = {}, {}
    for t, d in evs:
        if d < lo:
            continue
        j, i = bk.col.get(t), base_idx(d)
        if j is None or i is None or i + H >= nD:
            continue
        if t in last_i and i - last_i[t] < H:
            overlap[(t, d)] = True
            continue
        if not bench.elig[i, j] or np.isnan(bench.RET[i, j]):
            continue
        dcl = bench.DEC[i, j]
        if np.isnan(bench.SZ[i, dcl]) or np.isnan(bench.CB[i, dcl]):
            continue
        last_i[t] = i

    month_sessions = defaultdict(list)
    for i, d in enumerate(cal):
        month_sessions[d[:7]].append(i)
    out = []
    for r in ledger:
        if r.get("superseded"):
            continue
        prev = old.get(r["key"])
        if prev and prev.get("placebo_done"):
            continue
        t, D = r["ticker"], r["signal_date"]
        i, j = base_idx(D), bk.col.get(t)
        if i is None or i + H >= nD:
            continue                                        # 미만기
        rec = dict(prev) if prev else {"key": r["key"], "ticker": t, "signal_date": D}
        nb = naver(t)
        if not prev:
            val = naver_val(nb, cal, i)
            el = bool(j is not None and bench.elig[i, j])
            dcl = int(bench.DEC[i, j]) if j is not None else -1
            sz = float(bench.SZ[i, dcl]) if dcl >= 0 else float("nan")
            cb = float(bench.CB[i, dcl]) if dcl >= 0 else float("nan")

            def ix(col):
                if col is None:
                    return None
                o_, c_ = bk.o[i + 1, col], bk.c[i + H, col]
                return float((c_ / o_ - 1) * 100) if (o_ == o_ and c_ == c_ and o_ > 0) else None
            ki, kq = ix(k2), ix(q2)
            rp = float(bench.RET[i, j]) if (j is not None and bench.RET[i, j] == bench.RET[i, j]) else None
            taken = bool(el and val is not None and sz == sz and cb == cb and not overlap.get((t, D))
                         and r.get("history_ok", True))
            rec.update({
                "entry": cal[i + 1], "exit": cal[i + H], "actionable": r.get("actionable"),
                "upside_tercile": r.get("upside_tercile"), "market": r.get("market"), "n_brokers": r.get("n_brokers"),
                "history_ok": r.get("history_ok"), "elig": el, "decile": dcl, "overlap_skip": bool(overlap.get((t, D))),
                "taken": taken, "net": val, "net_pricekr": rp,
                "ex_cov": (val - cb) if (val is not None and cb == cb) else None,
                "ex_sz": (val - sz) if (val is not None and sz == sz) else None,
                "ex_k200": (val - ki) if (val is not None and ki is not None) else None,
                "ex_kq": (val - kq) if (val is not None and kq is not None) else None,
                "cov_bench": cb if cb == cb else None, "size_bench": sz if sz == sz else None,
                "placebo_done": False, "settled_ts": now_kst().isoformat(timespec="seconds"),
            })
        # 플라시보 A: 달이 끝났고(다음 달 세션이 달력에 있음) 그 달 k 전부 만기
        ms = month_sessions[cal[i][:7]]
        month_done = ms[-1] + 1 < nD and ms[-1] + H < nD
        if month_done and j is not None:
            vals = []
            for k in ms:
                if k <= i or k in sig_i.get(t, set()) or not bench.elig[k, j]:
                    continue
                dk = bench.DEC[k, j]
                if dk < 0 or np.isnan(bench.CB[k, dk]):
                    continue
                vk = naver_val(nb, cal, k)
                if vk is None:
                    continue
                vals.append(vk - float(bench.CB[k, dk]))
            pa = float(np.mean(vals)) if vals else None
            rec.update({"placebo_a": pa, "placebo_a_n": len(vals),
                        "diff_a": (rec["ex_cov"] - pa) if (pa is not None and rec.get("ex_cov") is not None) else None,
                        "placebo_done": True})
        out.append(rec)
    return out


def cmd_settle(a) -> int:
    ledger = [r for r in read_jsonl(LEDGER) if not r.get("superseded")]
    old = {x["key"]: x for x in read_jsonl(SETTLE)}
    due = [r for r in ledger if not old.get(r["key"], {}).get("placebo_done")]
    if not due:
        log(f"정산 대상 없음 (원장 {len(ledger)} · 정산 {len(old)})")
        return 0
    reps = merge_reports(read_backfill_reports(), load_shadow_rows())
    start = (min(date.fromisoformat(r["signal_date"]) for r in due) - timedelta(days=500)).isoformat()
    bk = BS.kr_load_book(start, {r["ticker"] for r in due} | {"069500", "229200"})
    cal = bk.cal
    if all(bisect.bisect_right(cal, r["signal_date"]) - 1 + H >= len(cal) for r in due):
        log(f"만기 도달분 없음 (원장 {len(ledger)} · 달력 끝 {cal[-1]})")
        return 0
    bench = Bench(bk, reps)
    new = settle_rows(due, reps, bk, bench, old, BS.naver_daily)
    for x in new:
        log(f"{x['key']} {x.get('entry')}→{x.get('exit')} taken={x.get('taken')} net={_f(x.get('net'))} "
            f"커버리지초과={_f(x.get('ex_cov'))} 플라시보차={_f(x.get('diff_a'))} placebo_done={x.get('placebo_done')}")
    if not a.dry and new:
        old.update({x["key"]: x for x in new})
        write_jsonl(SETTLE, list(old.values()))
    log(f"정산 {len(new)}건 (달력 끝 {cal[-1]})")
    return 0


def _f(x) -> str:
    return "-" if x is None else f"{x:+.2f}%"


# ─────────────────────────── report ───────────────────────────
def date_t(rows: List[dict], col: str) -> Optional[dict]:
    """연구 st3(802~812행): 신호일 평균 → 평이한 t (날짜 클러스터). 신호일 < 5 이면 t 없음."""
    by = defaultdict(list)
    for x in rows:
        v = x.get(col)
        if v is not None and v == v:
            by[x["signal_date"]].append(v)
    xs = [sum(v) / len(v) for _, v in sorted(by.items())]
    if not xs:
        return None
    m = sum(xs) / len(xs)
    t = None
    if len(xs) >= 5:
        sd = math.sqrt(sum((v - m) ** 2 for v in xs) / (len(xs) - 1))
        t = m / (sd / math.sqrt(len(xs))) if sd > 0 else None
    return {"n": sum(len(v) for v in by.values()), "nd": len(xs), "m": m, "t": t}


def _fs(s) -> str:
    if not s:
        return "-"
    tt = "-" if s["t"] is None else f"{s['t']:+.2f}"
    return f"{s['m']:+.2f}% (t {tt}, n {s['n']}/{s['nd']}일)"


def cmd_report(_a) -> int:
    led = read_jsonl(LEDGER)
    st = [x for x in read_jsonl(SETTLE) if x.get("taken")]
    print(f"R3 forward 쉐도우 — 원장 {len(led)}건 (superseded {sum(1 for r in led if r.get('superseded'))}, "
          f"actionable {sum(1 for r in led if r.get('actionable') and not r.get('superseded'))}) · 만기 채택 {len(st)}건")
    for lab, rows in (("전체", st), ("actionable", [x for x in st if x.get("actionable")])):
        print(f"[{lab}] vs 커버리지 {_fs(date_t(rows, 'ex_cov'))} · vs 규모매칭 {_fs(date_t(rows, 'ex_sz'))} · "
              f"vs KOSPI200 {_fs(date_t(rows, 'ex_k200'))} · vs KOSDAQ150 {_fs(date_t(rows, 'ex_kq'))} · "
              f"플라시보 차 {_fs(date_t(rows, 'diff_a'))}")
    for lab, f in (("괴리 상위(≥+37.1%)", lambda x: x.get("upside_tercile") == "high"),
                   ("KOSDAQ", lambda x: x.get("market") == "KOSDAQ"), ("KOSPI", lambda x: x.get("market") == "KOSPI")):
        rows = [x for x in st if f(x)]
        print(f"  층 {lab}: 커버리지 {_fs(date_t(rows, 'ex_cov'))} · 플라시보 차 {_fs(date_t(rows, 'diff_a'))}")
    d = date_t(st, "diff_a")
    ok = bool(d and d["n"] >= GATE_N and d["t"] is not None and d["t"] >= GATE_T)
    print(f"사전등록 게이트: 플라시보 차 만기 ≥{GATE_N}건 AND 날짜 클러스터 t ≥ {GATE_T} → "
          f"{'소액 canary 검토' if ok else '미충족'} (현재 n={d['n'] if d else 0}, t={'-' if not d or d['t'] is None else round(d['t'], 2)})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--dry", action="store_true")
    c.add_argument("--max-tickers", type=int, default=0)
    c.add_argument("--max-calls", type=int, default=0)
    sub.add_parser("signals")
    s = sub.add_parser("settle")
    s.add_argument("--dry", action="store_true")
    sub.add_parser("report")
    a = ap.parse_args(argv)
    return {"collect": cmd_collect, "signals": cmd_signals, "settle": cmd_settle, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
