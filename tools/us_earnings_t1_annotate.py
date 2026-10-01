# -*- coding: utf-8 -*-
"""T1 픽 forward 기록 전용 주석 (2026-09-29, 운영자 지시 "수정진행" · "T1 원장 기록 필드").

**기록 전용 — 매매 로직을 바꾸지 않는다.** 이 파일은 브로커를 부르지 않고, T1 쉐도우/라이브 도구가 읽는 어떤
파일도 쓰지 않는다. 입력은 data/shadow/us_earnings_t1_shadow.jsonl(signal, selected_I1=true · settle hold=20),
출력은 data/shadow/us_earnings_t1_annotations.jsonl(append-only) 하나다.

필드 (정의는 연구 스크립트 그대로 — 이탈 금지)
  issuer_type        research_jev_8k_t1_20260929.filer_stage: 이벤트일 이전 400일(filingDate < event_date) 안에
                     10-Q·10-K(/A)·10-KT 있으면 domestic, 없고 20-F·40-F·6-K(/A) 있으면 foreign, 둘 다 없으면 unknown.
  rare_neg_8k_30d    진입 세션 09:30 ET 이전 30일(달력일) 안에 accept된 8-K(8-K/A 제외) 중 item 2.06/3.01/4.01/4.02.
                     acceptanceDateTime은 UTC → America/New_York 변환(연구 1라운드 이탈 기록과 동일).
  item_502_during_hold  settle 명령: 쉐도우 settle(hold=20)의 exit_session이 생긴 뒤, [진입 09:30 ET, 청산 09:30 ET)
                     구간 8-K item 5.02 여부·건수.
  jev                research_jev_round2_20260929 H1718 동결 호출(V2): press_release + previous_quarter_release,
                     extract()·mask()·tail_questions()+DIFF_Q·모델 jev-1.13.0을 연구 모듈에서 직접 import한다.
                     현재 8-K = items∋2.02, |accept일(ET) − 이벤트일| ≤ 3, accept < 진입 09:30 ET, 가장 가까운 것.
                     직전 분기 = 2.02 8-K 중 이벤트일 − accept일 ∈ [50,130]일, 가장 최근. 문서 = 인덱스 첫 EX-99*(없으면 8-K 본문).
                     질문 dict의 sha256을 동결값과 대조해 어긋나면 호출하지 않는다(frozen_mismatch).

명령 (연구 가상환경 C:/Users/Unknown/research_venv/Scripts/python)
  annotate [--dry] [--synthetic TICKER EVENT_DATE SESSION HOUR]
           미주석 selected_I1 신호를 주석. (session,ticker) 멱등 — 단 api_error·sec_error·frozen_mismatch는 다음 실행에서 재시도.
           Jev 호출은 실행당 ≤ 50회(재시도 포함). 예산이 떨어지면 남은 신호는 기록하지 않고 다음 실행으로 넘긴다.
           --synthetic 은 원장 신호 대신 가상 신호 1건을 주석하고 기록하지 않는다(--dry 강제).
  settle   item_502_during_hold 채움.
  report   건수 + (20세션 성과 ≥ 100건이면) 사전등록 검정:
           ① d_outlook_improved 확률 상위 절반(≥ 중앙값) − 하위 절반 net_excess 차, 세션(날짜) 클러스터 t.
              기준: 차 > 0 이고 t ≥ 2.0.  ② foreign − domestic net_excess 차(같은 t, 보고용).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
SHADOW = ROOT / "data" / "shadow" / "us_earnings_t1_shadow.jsonl"
ANN = ROOT / "data" / "shadow" / "us_earnings_t1_annotations.jsonl"
CACHE = ROOT / "data" / "cache" / "sec_t1_annotate"
TICKERS = ROOT / "data" / "cache" / "sec_company_tickers.json"   # us_news_collector와 같은 원본·같은 7일 규칙
ENV_LIVE = ROOT / ".env.live"
UA = {"User-Agent": "claudetrade research smyang03@gmail.com", "Accept-Encoding": "gzip, deflate"}
JURL = "https://api.typesafe.ai/v1/systemone"
VERSION = "t1_annotate_v1"
TICKER_MAX_AGE = 7 * 24 * 3600
SEC_MIN_INTERVAL = 0.25          # ≤ 4 req/s
MAX_JEV_CALLS = 50
HOLD = 20
MIN_REPORT_N = 100
FROZEN_MODEL = "jev-1.13.0"
FROZEN_Q_SHA = "9b5572d80f3b5eacbf2c4462e6fccbd5d71df3a0db6a0b916527ff570a119508"
DOM_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT"}
FRN_FORMS = {"20-F", "40-F", "6-K", "20-F/A", "40-F/A"}
RARE_ITEMS = ("2.06", "3.01", "4.01", "4.02")
RETRY_STATUSES = {"api_error", "sec_error", "frozen_mismatch"}
ET = "America/New_York"

_sleep = time.sleep
_slock = threading.Lock()
_slast = [0.0]


# ------------------------------------------------------------------ io
def _jl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(p: Path, rows: list[dict]) -> None:
    if not rows:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _research():
    """연구 모듈(동결 정의) — 1라운드 mask, 2라운드 extract/strip/tail_questions/DIFF_Q/MODEL/cl_ols."""
    p = str(ROOT / "tools" / "research")
    if p not in sys.path:
        sys.path.insert(0, p)
    import research_jev_8k_t1_20260929 as r1
    import research_jev_round2_20260929 as r2
    return r1, r2


def frozen_questions() -> tuple[dict, str]:
    _, r2 = _research()
    q = r2.tail_questions()
    q.update(r2.DIFF_Q)
    sha = hashlib.sha256(json.dumps(q, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return q, sha


# ------------------------------------------------------------------ SEC
def sec_get(url: str):
    for att in range(5):
        with _slock:
            w = SEC_MIN_INTERVAL - (time.time() - _slast[0])
            if w > 0:
                _sleep(w)
            _slast[0] = time.time()
        try:
            r = requests.get(url, headers=UA, timeout=30)
        except Exception:  # noqa: BLE001
            r = None
        if r is not None:
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                _sleep(min(60, 5 * 2 ** att))
                continue
        _sleep(1 + 2 * att)
    return None


def ticker_map() -> dict[str, tuple[int, str]]:
    raw = None
    try:
        if TICKERS.exists() and time.time() - TICKERS.stat().st_mtime < TICKER_MAX_AGE:
            raw = json.loads(TICKERS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    if raw is None:
        r = sec_get("https://www.sec.gov/files/company_tickers.json")
        if r is not None:
            raw = r.json()
            TICKERS.parent.mkdir(parents=True, exist_ok=True)
            TICKERS.write_text(json.dumps(raw), encoding="utf-8")
        elif TICKERS.exists():                       # 낡은 캐시라도 사용
            raw = json.loads(TICKERS.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    vals = raw.values() if isinstance(raw, dict) else raw
    return {str(v["ticker"]).upper(): (int(v["cik_str"]), v.get("title") or "") for v in vals if v.get("ticker")}


def _tkey(tk: str) -> str:
    return str(tk or "").strip().upper().replace(".", "-")


_SUBS: dict[int, pd.DataFrame] = {}


def submissions(cik: int, since: str) -> pd.DataFrame | None:
    """SEC submissions(recent + filingTo ≥ since 인 추가 페이지). 실행 내 메모만 — 디스크 캐시 안 함(새 공시 반영)."""
    if cik in _SUBS:
        return _SUBS[cik]
    r = sec_get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    if r is None:
        return None
    j = r.json()
    fr = [pd.DataFrame(j["filings"]["recent"])]
    for f in j["filings"].get("files", []):
        if f.get("filingTo", "9999") >= since:
            rr = sec_get("https://data.sec.gov/submissions/" + f["name"])
            if rr is None:
                return None
            fr.append(pd.DataFrame(rr.json()))
    df = pd.concat(fr, ignore_index=True)
    for c in ("form", "accessionNumber", "filingDate", "acceptanceDateTime", "items"):
        if c not in df:
            df[c] = ""
    df = df[["form", "accessionNumber", "filingDate", "acceptanceDateTime", "items"]].drop_duplicates("accessionNumber")
    df = df.reset_index(drop=True)
    _SUBS[cik] = df
    return df


def acc_et(s: pd.Series) -> pd.Series:
    """acceptanceDateTime(UTC 'Z') → America/New_York, tz-naive (연구와 동일)."""
    return pd.to_datetime(s, utc=True).dt.tz_convert(ET).dt.tz_localize(None)


def _open_et(session) -> pd.Timestamp:
    return pd.Timestamp(session).normalize() + pd.Timedelta(hours=9, minutes=30)


def item_tokens(x) -> list[str]:
    return [t.strip() for t in str(x or "").split(",") if t.strip()]


# ------------------------------------------------------------------ definitions (pure)
def issuer_type(f: pd.DataFrame, event_date) -> str:
    """research_jev_8k_t1_20260929.filer_stage 그대로."""
    if f is None or not len(f):
        return "unknown"
    ed = pd.Timestamp(event_date)
    fd = pd.to_datetime(f.filingDate)
    prior = f[(fd < ed) & (fd >= ed - pd.Timedelta(days=400))].form
    if prior.isin(list(DOM_FORMS)).any():
        return "domestic"
    if prior.isin(list(FRN_FORMS)).any():
        return "foreign"
    return "unknown"


def _k202(f: pd.DataFrame) -> pd.DataFrame:
    if f is None or not len(f):
        return pd.DataFrame(columns=["form", "accessionNumber", "filingDate", "acceptanceDateTime", "items"])
    return f[(f.form == "8-K") & f["items"].astype(str).str.contains("2.02", regex=False)].reset_index(drop=True)


def match_current(f: pd.DataFrame, event_date, session) -> dict:
    """현재 분기 8-K: |accept일(ET) − 이벤트일| ≤ 3 · accept < 진입 09:30 ET · 가장 가까운 것."""
    x = _k202(f)
    if not len(x):
        return {"status": "no_8k"}
    at = acc_et(x.acceptanceDateTime)
    dd = (at.dt.normalize() - pd.Timestamp(event_date)).dt.days
    near = dd.abs() <= 3
    ok = near & (at < _open_et(session))
    if ok.any():
        j = dd[ok].abs().sort_values(kind="stable").index[0]
        return {"status": "matched", "acc": x.accessionNumber[j], "accept_et": str(at[j]), "dd": int(dd[j])}
    if near.any():
        j = dd[near].abs().sort_values(kind="stable").index[0]
        return {"status": "after_cutoff", "acc": x.accessionNumber[j], "accept_et": str(at[j]), "dd": int(dd[j])}
    return {"status": "no_8k"}


def find_prior(f: pd.DataFrame, event_date, cur_acc: str) -> dict | None:
    """직전 분기: 2.02 8-K 중 이벤트일 − accept일(ET) ∈ [50,130], 현재 제외, 가장 최근."""
    x = _k202(f)
    if not len(x):
        return None
    at = acc_et(x.acceptanceDateTime)
    gap = (pd.Timestamp(event_date) - at.dt.normalize()).dt.days
    ok = (gap >= 50) & (gap <= 130) & (x.accessionNumber != cur_acc)
    if not ok.any():
        return None
    j = gap[ok].sort_values(kind="stable").index[0]
    return {"acc": x.accessionNumber[j], "gap": int(gap[j])}


def _k8_window(f: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, want: tuple) -> list[dict]:
    if f is None or not len(f):
        return []
    x = f[f.form == "8-K"].reset_index(drop=True)
    if not len(x):
        return []
    at = acc_et(x.acceptanceDateTime)
    out = []
    for i in x.index[(at >= start) & (at < end)]:
        hit = [t for t in item_tokens(x.at[i, "items"]) if t in want]
        if hit:
            out.append({"acc": x.at[i, "accessionNumber"], "accept_et": str(at[i]), "items": hit})
    return out


def rare_neg_8k(f: pd.DataFrame, session, days: int = 30) -> dict:
    op = _open_et(session)
    hits = _k8_window(f, op - pd.Timedelta(days=days), op, RARE_ITEMS)
    return {"rare_neg_8k_30d": bool(hits), "rare_neg_8k_items": sorted({t for h in hits for t in h["items"]}),
            "rare_neg_8k_filings": hits}


def item502_during_hold(f: pd.DataFrame, session, exit_session) -> dict:
    hits = _k8_window(f, _open_et(session), _open_et(exit_session), ("5.02",))
    return {"item_502_during_hold": bool(hits), "item_502_count": len(hits), "item_502_acc": [h["acc"] for h in hits]}


# ------------------------------------------------------------------ documents
def fetch_doc(cik: int, acc: str) -> str | None:
    """research_jev_round2_20260929.fetch_doc 선택 규칙(인덱스 첫 EX-99*, 없으면 8-K 본문). 접수번호 단위 캐시."""
    fp = CACHE / "docs" / f"{acc}.txt"
    if fp.exists():
        return fp.read_text(encoding="utf-8")
    _, r2 = _research()
    nd = acc.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nd}/"
    r = sec_get(base + f"{acc}-index.htm")
    if r is None:
        return None
    name = prim = None
    for row in re.findall(r'(?is)<tr[^>]*>(.*?)</tr>', r.text):
        m = re.search(r'href="([^"]+)"', row)
        tds = [r2.strip(x) for x in re.findall(r'(?is)<td[^>]*>(.*?)</td>', row)]
        if m and len(tds) >= 4:
            fn = m.group(1).split("/")[-1]; typ = tds[3].upper()
            if fn.lower().endswith((".pdf", ".jpg", ".gif", ".png")):
                continue
            if name is None and typ.startswith("EX-99"):
                name = fn
            if prim is None and typ.startswith("8-K"):
                prim = fn
    name = name or prim
    if not name:
        return None
    d = sec_get(base + name)
    if d is None:
        return None
    txt = r2.strip(d.text)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(txt, encoding="utf-8")
    return txt


# ------------------------------------------------------------------ Jev
def jev_key() -> str:
    try:
        from dotenv import dotenv_values
        return (dotenv_values(ENV_LIVE).get("JEV_API_KEY") or "").strip()
    except ImportError:
        pass
    if ENV_LIVE.exists():
        for line in ENV_LIVE.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("JEV_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _scrub(s: str, key: str) -> str:
    s = str(s or "")
    return s.replace(key, "***") if key else s


class Budget:
    def __init__(self, n: int = MAX_JEV_CALLS):
        self.left = n
        self.calls = 0


def jev_post(body: dict, key: str, budget: Budget) -> tuple[dict | None, str]:
    err = ""
    for att in range(3):
        if budget.left <= 0:
            return None, err or "budget_exhausted"
        budget.left -= 1
        budget.calls += 1
        try:
            r = requests.post(JURL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=120)
            if r.status_code == 200:
                return r.json(), ""
            err = f"HTTP {r.status_code} {str(r.text)[:150]}"
            if r.status_code in (400, 401, 403, 422):
                break
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
        _sleep(2 * (att + 1))
    return None, _scrub(err, key)


def _choice(ans: dict, k: str) -> dict:
    v = (ans or {}).get(k) or {}
    return {"probs": v.get("probabilities"), "argmax": v.get("choice"), "confidence": v.get("confidence")}


# ------------------------------------------------------------------ annotate
def _signals() -> list[dict]:
    return [r for r in _jl(SHADOW) if r.get("kind") == "signal" and r.get("selected_I1") is True]


def _latest_ann() -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for r in _jl(ANN):
        if r.get("kind") == "annotation":
            out[(r["session"], r["ticker"])] = r
    return out


def annotate_one(sig: dict, tmap: dict, budget: Budget, key_fn=None) -> dict | None:
    """신호 1건 주석. Jev가 필요한데 예산이 0이면 None(기록 안 함 → 다음 실행)."""
    tk, session, ev = sig["ticker"], str(sig["session"]), str(sig["event_date"])
    row = {"kind": "annotation", "version": VERSION, "session": session, "ticker": tk, "event_date": ev,
           "hour": sig.get("hour"), "score": sig.get("score"), "selected_I2": sig.get("selected_I2"),
           "liquid_5m": sig.get("liquid_5m"), "annotated_at": datetime.now(timezone.utc).isoformat(),
           "cik": None, "issuer_type": "unknown", "rare_neg_8k_30d": None, "rare_neg_8k_items": [],
           "rare_neg_8k_filings": [], "item_502_during_hold": None, "jev": {"status": None}}
    hit = tmap.get(_tkey(tk))
    if hit is None:
        row["jev"]["status"] = "no_cik"
        return row
    cik, title = hit
    row["cik"] = cik
    since = str((pd.Timestamp(ev) - pd.Timedelta(days=400)).date())
    f = submissions(cik, since)
    if f is None:
        row["jev"]["status"] = "sec_error"
        return row
    row["issuer_type"] = issuer_type(f, ev)
    row.update(rare_neg_8k(f, session))
    cur = match_current(f, ev, session)
    j = row["jev"]
    j.update({"cur_acc": cur.get("acc"), "cur_accept_et": cur.get("accept_et"), "cur_dd": cur.get("dd")})
    if cur["status"] != "matched":
        j["status"] = cur["status"]
        return row
    pr = find_prior(f, ev, cur["acc"])
    if pr is None:
        j["status"] = "no_prior"
        return row
    j.update({"prior_acc": pr["acc"], "prior_gap": pr["gap"]})
    cur_txt = fetch_doc(cik, cur["acc"])
    if not cur_txt or len(cur_txt) <= 200:
        j["status"] = "no_doc" if cur_txt is not None else "sec_error"
        return row
    pri_txt = fetch_doc(cik, pr["acc"])
    if not pri_txt or len(pri_txt) <= 200:
        j["status"] = "no_prior"
        j["prior_doc_missing"] = True
        return row
    q, sha = frozen_questions()
    j["q_sha"] = sha
    r1, r2 = _research()
    if sha != FROZEN_Q_SHA or r2.MODEL != FROZEN_MODEL:
        j["status"] = "frozen_mismatch"
        return row
    if budget.left <= 0:
        return None
    names = [title, tk]
    body = {"state": {"press_release": r2.extract(r1.mask(cur_txt, names)),
                      "previous_quarter_release": r2.extract(r1.mask(pri_txt, names))},
            "model": r2.MODEL, "questions": q}
    key = (key_fn or jev_key)()
    if not key:
        j.update(status="api_error", error="no_key")
        return row
    res, err = jev_post(body, key, budget)
    j["model_req"] = r2.MODEL
    if res is None:
        j.update(status="api_error", error=_scrub(err, key))
        return row
    ans = res.get("answers") or {}
    do = _choice(ans, "d_outlook")
    probs = do["probs"] or {}
    j.update(status="ok", model_resp=res.get("model"), usage=res.get("usage"),
             d_outlook_probs=probs, d_outlook_argmax=do["argmax"], d_outlook_confidence=do["confidence"],
             d_outlook_improved=probs.get("improved"),
             d_momentum_probs=_choice(ans, "d_momentum")["probs"],
             d_new=(ans.get("d_new") or {}).get("noul"))
    return row


def cmd_annotate(dry: bool = False, synthetic: dict | None = None, out=print) -> int:
    done = {k for k, r in _latest_ann().items() if (r.get("jev") or {}).get("status") not in RETRY_STATUSES}
    sigs = [synthetic] if synthetic else [s for s in _signals() if (str(s["session"]), s["ticker"]) not in done]
    seen, todo = set(), []
    for s in sigs:
        k = (str(s["session"]), s["ticker"])
        if k not in seen:
            seen.add(k); todo.append(s)
    if not todo:
        out("[T1-annot] 주석 대상 없음"); return 0
    tmap = ticker_map()
    budget = Budget(MAX_JEV_CALLS)
    rows, deferred = [], 0
    for s in todo:
        r = annotate_one(s, tmap, budget)
        if r is None:
            deferred += 1; continue
        rows.append(r)
        j = r["jev"]
        out(f"  {r['session']} {r['ticker']} issuer={r['issuer_type']} rare8k={r['rare_neg_8k_30d']}"
            f"{r['rare_neg_8k_items'] or ''} jev={j['status']} cur={j.get('cur_acc')}@{j.get('cur_accept_et')}"
            f" prior={j.get('prior_acc')} d_outlook_improved={j.get('d_outlook_improved')} argmax={j.get('d_outlook_argmax')}")
    out(f"[T1-annot] 대상 {len(todo)} · 기록 {len(rows)} · 예산 이월 {deferred} · Jev 호출 {budget.calls}/{MAX_JEV_CALLS}"
        + (" · DRY(기록 안 함)" if dry or synthetic else ""))
    if dry or synthetic:
        for r in rows:
            out(json.dumps(r, ensure_ascii=False, default=str))
    else:
        _append(ANN, rows)
    return 0


# ------------------------------------------------------------------ settle (item 5.02 during hold)
def _settle20() -> dict[tuple, dict]:
    return {(str(r["session"]), r["ticker"]): r for r in _jl(SHADOW)
            if r.get("kind") == "settle" and r.get("hold") == HOLD and r.get("exit_session")}


def cmd_settle(dry: bool = False, out=print) -> int:
    ann = _latest_ann()
    have = {(r["session"], r["ticker"]) for r in _jl(ANN) if r.get("kind") == "settle_502"}
    st = _settle20()
    todo = [(k, a, st[k]) for k, a in ann.items() if k not in have and k in st and a.get("cik")]
    if not todo:
        out("[T1-annot] 5.02 정산 대상 없음"); return 0
    rows = []
    for (session, tk), a, s in todo:
        f = submissions(int(a["cik"]), session)
        if f is None:
            out(f"  {session} {tk} SEC 실패 — 다음 실행"); continue
        rows.append({"kind": "settle_502", "session": session, "ticker": tk, "exit_session": s["exit_session"],
                     **item502_during_hold(f, session, s["exit_session"]),
                     "settled_at": datetime.now(timezone.utc).isoformat()})
    out(f"[T1-annot] 5.02 정산 {len(rows)}건" + (" · DRY" if dry else ""))
    if not dry:
        _append(ANN, rows)
    return 0


# ------------------------------------------------------------------ report
def cluster_diff(y: np.ndarray, flag: np.ndarray, groups) -> tuple[float, float]:
    """y ~ 1 + flag, 그룹(세션) 클러스터 t — 연구 2라운드 cl_ols 그대로."""
    _, r2 = _research()
    b, t = r2.cl_ols(np.asarray(y, float), np.asarray(flag, float)[:, None], np.asarray(groups))
    return float(b[1]), float(t[1])


def build_table() -> pd.DataFrame:
    ann = _latest_ann()
    s502 = {(r["session"], r["ticker"]): r for r in _jl(ANN) if r.get("kind") == "settle_502"}
    st = {(str(r["session"]), r["ticker"]): r for r in _jl(SHADOW) if r.get("kind") == "settle" and r.get("hold") == HOLD}
    rows = []
    for k, a in ann.items():
        j = a.get("jev") or {}
        s = st.get(k) or {}
        rows.append({"session": k[0], "ticker": k[1], "issuer_type": a.get("issuer_type"),
                     "rare_neg_8k_30d": a.get("rare_neg_8k_30d"), "jev_status": j.get("status"),
                     "d_outlook_improved": j.get("d_outlook_improved"),
                     "item_502_during_hold": (s502.get(k) or {}).get("item_502_during_hold"),
                     "net_excess": s.get("net_excess")})
    return pd.DataFrame(rows)


def cmd_report(out=print) -> int:
    sig = _signals()
    d = build_table()
    out(f"[T1-annot] selected_I1 신호 {len({(str(s['session']), s['ticker']) for s in sig})} · 주석 {len(d)}")
    if not len(d):
        return 0
    out("  jev status " + json.dumps(d.jev_status.value_counts().to_dict(), ensure_ascii=False))
    out("  issuer_type " + json.dumps(d.issuer_type.value_counts().to_dict(), ensure_ascii=False))
    out(f"  rare_neg_8k_30d True {int((d.rare_neg_8k_30d == True).sum())} · 5.02 정산 {int(d.item_502_during_hold.notna().sum())}"  # noqa: E712
        f" (True {int((d.item_502_during_hold == True).sum())})")  # noqa: E712
    o = d[d.net_excess.notna()]
    oj = o[(o.jev_status == "ok") & o.d_outlook_improved.notna()]
    out(f"  20세션 성과 {len(o)} · 그중 Jev ok {len(oj)} (검정 문턱 {MIN_REPORT_N})")
    if len(o) < MIN_REPORT_N:
        out("  사전등록 검정: 표본 부족 — 미실행"); return 0
    if len(oj) >= 10 and oj.d_outlook_improved.nunique() > 1:
        hi = (oj.d_outlook_improved >= oj.d_outlook_improved.median()).values
        b, t = cluster_diff(oj.net_excess.values, hi, oj.session.values)
        ok = b > 0 and t >= 2.0
        out(f"  ① d_outlook_improved 상위−하위 net 차 {b*100:+.2f}%p · 세션 클러스터 t {t:.2f} (n {len(oj)}: "
            f"상위 {int(hi.sum())} / 하위 {int((~hi).sum())}) → {'통과' if ok else '미통과'} (기준 차>0 · t≥2.0)")
    else:
        out(f"  ① d_outlook_improved: Jev ok {len(oj)}건 — 산출 불가")
    fd = o[o.issuer_type.isin(["foreign", "domestic"])]
    fl = (fd.issuer_type == "foreign").values
    if fl.sum() >= 3 and (~fl).sum() >= 3:
        b2, t2 = cluster_diff(fd.net_excess.values, fl, fd.session.values)
        out(f"  ② foreign−domestic net 차 {b2*100:+.2f}%p · 세션 클러스터 t {t2:.2f} (foreign {int(fl.sum())} / domestic {int((~fl).sum())})")
    else:
        out(f"  ② foreign−domestic: foreign {int(fl.sum())}건 — 3건 미만, 산출 불가")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["annotate", "settle", "report"])
    ap.add_argument("--dry", action="store_true", help="기록 없이 출력만")
    ap.add_argument("--synthetic", nargs=4, metavar=("TICKER", "EVENT_DATE", "SESSION", "HOUR"),
                    help="annotate: 가상 신호 1건(기록 안 함)")
    a = ap.parse_args(argv)
    if a.cmd == "annotate":
        syn = None
        if a.synthetic:
            tk, ev, se, hr = a.synthetic
            syn = {"kind": "signal", "ticker": tk, "event_date": ev, "session": se, "hour": hr, "selected_I1": True,
                   "synthetic": True}
        return cmd_annotate(a.dry, syn)
    if a.cmd == "settle":
        return cmd_settle(a.dry)
    return cmd_report()


if __name__ == "__main__":
    raise SystemExit(main())
