# -*- coding: utf-8 -*-
"""KR 내부자(임원ㆍ주요주주) 소유보고 원장 + 거래계획 사전공시 원장 (2026-09-07 신규 전략 P4/P5).

- 소유보고: OpenDART `elestock.json`(회사별 임원·주요주주 특정증권등 소유상황보고 전체 이력, 1회 호출). 보고서 단위로
  증감수량(`sp_stock_lmp_irds_cnt`)·보고자·직위를 그대로 저장한다. 장내매수/증여/스톡옵션 구분은 이 API에 없다 —
  가상 북 필터는 "증감 > 0 & 보고자 2인 이상 5거래일 군집"으로 쓰고, 구분은 후속(본문 표본)에서 검증한다.
- 거래계획: 지분공시(D) 목록에서 `임원·주요주주 특정증권등 거래계획보고서`(2024-07 제도)를 12개월 수집, 본문에서
  취득/처분·예정기간·수량을 정규식으로 뽑는다.
- 재개 가능(상태 파일). 호출 간격 0.12s. DART 일일 한도(공시 레인이 같은 키를 쓴다)를 고려해 실패 3회면 중단.

원장: data/shadow/kr_insider_ledger.jsonl (소유보고), data/shadow/kr_insider_plan_ledger.jsonl (거래계획)
사용: python tools/dart_insider_ledger.py [--plans-only|--holdings-only] [--max N] [--refresh-days D(기본 1)]
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
KR_DIR = ROOT / "data" / "price" / "kr"
CORP_MAP = ROOT / "data" / "dart_corp_codes.json"
OUT_HOLD = ROOT / "data" / "shadow" / "kr_insider_ledger.jsonl"
OUT_PLAN = ROOT / "data" / "shadow" / "kr_insider_plan_ledger.jsonl"
STATE = ROOT / "data" / "shadow" / "kr_insider_ledger.state.json"
MIN_DT = "2025-01-01"
SLEEP = 0.12


def _key() -> str:
    k = os.getenv("DART_API_KEY", "").strip()
    if k:
        return k
    for line in (ROOT / ".env.live").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("DART_API_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


def _get(url: str, timeout: float = 30.0) -> bytes:
    return urllib.request.urlopen(url, timeout=timeout).read()


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"holdings_done": [], "plans_done": [], "plan_windows_done": []}


def _save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def holdings(key: str, max_n: int | None, refresh_days: int = 1) -> int:
    corp = json.loads(CORP_MAP.read_text(encoding="utf-8"))
    tickers = sorted(p.stem[3:] for p in KR_DIR.glob("kr_*.csv"))
    st = _state(); done = set(st.get("holdings_done", []))
    fetched_at: dict[str, str] = st.get("holdings_fetched_at", {})
    now = datetime.now(timezone.utc)
    seen = set()
    if OUT_HOLD.exists():
        for line in OUT_HOLD.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line).get("rcept_no"))
            except ValueError:
                continue
    n_rows = 0; fails = 0; n_calls = 0
    with OUT_HOLD.open("a", encoding="utf-8") as fh:
        for tk in tickers:
            cc = corp.get(tk)
            if not cc:
                continue
            last = fetched_at.get(tk)
            if tk in done and last and (now - datetime.fromisoformat(last)).days < refresh_days:
                continue   # Codex P1: 완료 종목도 refresh_days마다 재조회(elestock은 전체 이력 → 새 보고서만 append)
            if max_n is not None and n_calls >= max_n:
                break
            q = urllib.parse.urlencode({"crtfc_key": key, "corp_code": cc})
            try:
                d = json.loads(_get(f"https://opendart.fss.or.kr/api/elestock.json?{q}"))
                n_calls += 1
            except Exception as exc:  # noqa: BLE001
                fails += 1; print(f"[INSIDER] {tk} 호출 실패 {exc}")
                if fails >= 3:
                    print("[INSIDER] 실패 3회 — 중단(재개 가능)"); break
                time.sleep(2.0); continue
            if d.get("status") == "020":   # 한도 초과
                print("[INSIDER] DART 일일 한도 초과 — 중단(재개 가능)"); break
            if d.get("status") not in ("000", "013"):
                print(f"[INSIDER] {tk} status {d.get('status')} {d.get('message')}")
            for r in d.get("list", []) or []:
                dt = str(r.get("rcept_dt") or "")
                if dt < MIN_DT or r.get("rcept_no") in seen:
                    continue
                seen.add(r.get("rcept_no"))
                row = {"stock": tk, "corp_code": cc, "rcept_no": r.get("rcept_no"), "rcept_dt": dt,
                       "repror": r.get("repror"), "ofcps": r.get("isu_exctv_ofcps"), "rgist": r.get("isu_exctv_rgist_at"),
                       "main_shr": r.get("isu_main_shrholdr"), "cnt": _num(r.get("sp_stock_lmp_cnt")),
                       "irds_cnt": _num(r.get("sp_stock_lmp_irds_cnt")), "rate": _num(r.get("sp_stock_lmp_rate")),
                       "irds_rate": _num(r.get("sp_stock_lmp_irds_rate")), "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                fh.write(json.dumps(row, ensure_ascii=False) + "\n"); n_rows += 1
            done.add(tk); fetched_at[tk] = now.isoformat()
            if n_calls % 50 == 0:
                st["holdings_done"] = sorted(done); st["holdings_fetched_at"] = fetched_at; _save_state(st); fh.flush()
                print(f"[INSIDER] {n_calls}회 호출 · {n_rows}행")
            time.sleep(SLEEP)
    st["holdings_done"] = sorted(done); st["holdings_fetched_at"] = fetched_at; _save_state(st)
    print(f"[INSIDER] 소유보고 완료 — 호출 {n_calls} · 신규 행 {n_rows} · 종목 {len(done)}")
    return n_rows


_DATE = r"(\d{4})\s*[.\-년/]\s*(\d{1,2})\s*[.\-월/]\s*(\d{1,2})"


def _dates(text: str) -> list[str]:
    out = []
    for y, m, d in re.findall(_DATE, text):
        try:
            out.append(date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            continue
    return out


def parse_plan(text: str) -> dict:
    """거래계획보고서 본문 → 취득/처분, 예정기간, 수량. 실패 필드는 None."""
    # 본문 표 "거래방법" 값: 장내매수(+) / 장내매도(-) / 장외매도(-) / 장외매수(+) … — 부호 괄호가 정본
    buys = len(re.findall(r"(장내|장외|시간외)?\s*매수\s*\(\+\)", text)) + len(re.findall(r"취득\s*\(\+\)", text))
    sells = len(re.findall(r"(장내|장외|시간외)?\s*매도\s*\(-\)", text)) + len(re.findall(r"처분\s*\(-\)", text))
    side = "buy" if buys and not sells else ("sell" if sells and not buys else ("mixed" if buys and sells else None))
    m = re.search(r"거래\s*개시일\s*\(?결제일\)?\s*거래\s*종료일\s*\(?결제일\)?(.{0,200})", text) or re.search(r"(거래\s*예정\s*기간|거래\s*기간)(.{0,120})", text)
    ds = _dates(m.group(m.lastindex) if m else "") if m else []
    q = re.search(r"특정증권등의수\s*\(주식수\)[^\d]{0,120}?\d{4}년[^\d]*\d{1,2}월[^\d]*\d{1,2}일[^\d]*\d{4}년[^\d]*\d{1,2}월[^\d]*\d{1,2}일\s*\d+\s*[가-힣()+\-]+\s*[가-힣]+\s*([\d,]+)", text)
    return {"side": side, "start": ds[0] if ds else None, "end": ds[1] if len(ds) > 1 else None,
            "qty": _num(q.group(1)) if q else None, "n_buy_rows": buys, "n_sell_rows": sells}


def plans(key: str, max_n: int | None) -> int:
    from runtime.kr_event_lane import dart_document_text
    st = _state(); done = set(st.get("plans_done", []))
    end = date.today(); start = end - timedelta(days=365)
    n_rows = 0; n_calls = 0
    with OUT_PLAN.open("a", encoding="utf-8") as fh:
        w0 = start
        while w0 < end:
            w1 = min(w0 + timedelta(days=89), end)
            page = 1
            while True:
                q = urllib.parse.urlencode({"crtfc_key": key, "bgn_de": w0.strftime("%Y%m%d"), "end_de": w1.strftime("%Y%m%d"),
                                            "pblntf_ty": "D", "page_no": page, "page_count": 100})
                try:
                    d = json.loads(_get(f"https://opendart.fss.or.kr/api/list.json?{q}")); n_calls += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"[INSIDER] 목록 실패 {w0}~{w1} p{page} {exc}"); time.sleep(2.0); break
                if d.get("status") == "020":
                    print("[INSIDER] DART 일일 한도 초과 — 중단"); return n_rows
                for r in d.get("list", []) or []:
                    nm = r.get("report_nm") or ""
                    if "거래계획" not in nm or "정정" in nm:
                        continue
                    rno = r.get("rcept_no"); sc = (r.get("stock_code") or "").strip()
                    if not sc or rno in done:
                        continue
                    if max_n is not None and n_calls >= max_n:
                        break
                    text = dart_document_text(rno, max_chars=60000); n_calls += 1; time.sleep(SLEEP)
                    p = parse_plan(text) if text else {"side": None, "start": None, "end": None, "qty": None}
                    row = {"stock": sc, "corp_name": r.get("corp_name"), "rcept_no": rno, "rcept_dt": r.get("rcept_dt"),
                           "flr_nm": r.get("flr_nm"), **p, "doc_ok": bool(text),
                           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n"); n_rows += 1; done.add(rno)
                if page >= int(d.get("total_page") or 1):
                    break
                page += 1; time.sleep(SLEEP)
            st["plans_done"] = sorted(done); _save_state(st); fh.flush()
            w0 = w1 + timedelta(days=1)
    print(f"[INSIDER] 거래계획 완료 — 호출 {n_calls} · 행 {n_rows}")
    return n_rows


def main() -> int:
    key = _key()
    if not key:
        print("[INSIDER] DART_API_KEY 없음"); return 1
    args = sys.argv[1:]
    max_n = None
    if "--max" in args:
        max_n = int(args[args.index("--max") + 1])
    OUT_HOLD.parent.mkdir(parents=True, exist_ok=True)
    refresh_days = int(args[args.index("--refresh-days") + 1]) if "--refresh-days" in args else 1
    if "--plans-only" not in args:
        holdings(key, max_n, refresh_days)
    if "--holdings-only" not in args:
        plans(key, max_n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
