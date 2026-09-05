#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KR 공모주(IPO) 캘린더·균등배정 shadow 원장 (read-only, 2026-09-06).

근거: 한국 개인에게 남은 구조적 우위 후보 — 균등배정은 소액 청약증거금으로 참여 가능하고 첫날 수익 분포가 양의 꼬리를 가진다는 가설.
자동 청약은 API가 없어 반자동이므로, 여기서는 **원장과 캘린더**를 만든다: 어떤 IPO가 언제 청약·상장하는지, 공모가 대비 첫날 시가·종가·고가가
어땠는지(1주 균등배정 가정). 판정은 표본이 쌓인 뒤 분포로 한다(평균이 아니라 양의 꼬리·손실 비율).

소스: DART list.json pblntf_ty=C(발행공시). IPO = 정정 아닌 '증권신고서(지분증권)' 중 접수 시점 stock_code가 비어 있는 법인(미상장).
      같은 corp_code의 후속 '[발행조건확정]증권신고서' / '증권발행실적보고서' / '투자설명서'로 공모가·청약일·상장예정일을 보강.
      본문(document.xml)에서 정규식으로 공모가·희망밴드·청약기일·상장(예정)일 추출. 상장 후 종목코드는 dart_corp_codes.json 역매핑.
가격: data/price/kr(없으면 ensure_kr_price_cache로 KIS 일봉 생성) 첫 봉 = 상장일.
출력: data/shadow/kr_ipo_ledger.jsonl (corp_code 멱등) · data/shadow/kr_ipo_calendar.json (대시보드용 upcoming/recent/stats)
사용: python tools/kr_ipo_calendar.py --months 12          # 이력 구축(본문 fetch ~ 수 분)
      python tools/kr_ipo_calendar.py --recent-days 45      # 관측 체인용(신규·갱신만)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics as st
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.live", override=False)

LEDGER = ROOT / "data" / "shadow" / "kr_ipo_ledger.jsonl"
CAL = ROOT / "data" / "shadow" / "kr_ipo_calendar.json"
CORP = ROOT / "data" / "dart_corp_codes.json"
KR_DIR = ROOT / "data" / "price" / "kr"
KST = timezone(timedelta(hours=9))
_NUM = r"([\d,]{3,})"


def _key() -> str:
    return str(os.getenv("DART_API_KEY", "") or "").strip()


def _get(url: str, timeout: float = 20.0, tries: int = 3) -> bytes:
    for i in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=timeout).read()
        except Exception:
            time.sleep(1.5 * (i + 1))
    return b""


def dart_list(ty: str, bgn: str, end: str, *, max_pages: int = 400) -> list[dict]:
    out = []
    page, total = 1, 1
    while page <= total and page <= max_pages:
        q = urllib.parse.urlencode({"crtfc_key": _key(), "bgn_de": bgn, "end_de": end, "pblntf_ty": ty, "page_no": page, "page_count": 100})
        raw = _get(f"https://opendart.fss.or.kr/api/list.json?{q}")
        try:
            d = json.loads(raw)
        except ValueError:
            break
        if d.get("status") != "000":
            break
        total = int(d.get("total_page", 1))
        out.extend(d.get("list", []) or [])
        page += 1
        time.sleep(0.12)
    return out


def doc_text(rcept_no: str, max_chars: int = 40000) -> str:
    from runtime.kr_event_lane import dart_document_text
    return dart_document_text(rcept_no, max_chars=max_chars)


def _num(s: str | None) -> float | None:
    try:
        return float(str(s).replace(",", "")) if s else None
    except ValueError:
        return None


def _kdate(s: str) -> str | None:
    m = re.search(r"(\d{4})\s*[.년\-/]\s*(\d{1,2})\s*[.월\-/]\s*(\d{1,2})", s)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def parse_ipo_doc(text: str) -> dict:
    """공모가(확정/희망밴드)·청약기일·상장예정일. 서식이 제각각이라 관대한 정규식 + 원문 조각을 남긴다."""
    f: dict = {}
    m = re.search(r"확정\s*공모가(?:액)?[^\d]{0,40}" + _NUM, text) or re.search(r"공모가(?:액)?\s*\(?확정\)?[^\d]{0,40}" + _NUM, text)
    f["offer_price"] = _num(m.group(1)) if m else None
    m = re.search(r"희망\s*공모가(?:액)?[^\d]{0,40}" + _NUM + r"\s*원?\s*[~∼\-]\s*" + _NUM, text)
    f["band"] = [_num(m.group(1)), _num(m.group(2))] if m else None
    if f["offer_price"] is None:
        m = re.search(r"(?:모집|매출)\s*가액[^\d]{0,30}" + _NUM, text)
        f["offer_price_guess"] = _num(m.group(1)) if m else None
    m = re.search(r"청약\s*기일[^\d]{0,20}(\d{4}\s*[.년\-/]\s*\d{1,2}\s*[.월\-/]\s*\d{1,2})[^\d]{0,12}(\d{4}\s*[.년\-/]\s*\d{1,2}\s*[.월\-/]\s*\d{1,2})?", text)
    f["subscription"] = [_kdate(m.group(1)), _kdate(m.group(2)) if m.group(2) else None] if m else None
    m = re.search(r"(?:상장\s*예정일|신규\s*상장일|상장일)[^\d]{0,20}(\d{4}\s*[.년\-/]\s*\d{1,2}\s*[.월\-/]\s*\d{1,2})", text)
    f["listing_date"] = _kdate(m.group(1)) if m else None
    m = re.search(r"(?:일반\s*청약자|일반투자자)[^\d%]{0,60}(\d{1,3}(?:\.\d+)?)\s*%", text)
    f["retail_pct"] = _num(m.group(1)) if m else None
    f["spac"] = ("기업인수목적" in text[:3000]) or ("스팩" in text[:3000])
    return f


def load_bars(ticker: str) -> list[tuple[str, float, float, float, float]]:
    p = KR_DIR / f"kr_{ticker}.csv"
    rows = []
    if p.exists():
        with p.open(encoding="utf-8-sig") as fh:
            for r in csv.reader(fh):
                if r and r[0][:2] == "20" and len(r) >= 6:
                    try:
                        rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])))
                    except ValueError:
                        pass
    return sorted(rows)


def outcome(ticker: str, listing_date: str | None, offer: float | None, *, ensure: bool = True) -> dict:
    if not ticker or not offer:
        return {}
    bars = load_bars(ticker)
    if not bars and ensure:
        try:
            from tools.ensure_kr_price_cache import ensure as _ensure
            _ensure([ticker], verbose=False)
            bars = load_bars(ticker)
        except Exception:
            bars = []
    if not bars:
        return {"no_bars": True}
    # 상장일 봉: listing_date가 있으면 그 날(또는 직후), 없으면 첫 봉
    idx = 0
    if listing_date:
        idx = next((i for i, b in enumerate(bars) if b[0] >= listing_date), None)
        if idx is None:
            return {"no_bars": True}
    d0 = bars[idx]
    out = {"day1_date": d0[0], "day1_open": d0[1], "day1_high": d0[2], "day1_close": d0[4],
           "ret_open_pct": round((d0[1] / offer - 1) * 100, 2), "ret_high_pct": round((d0[2] / offer - 1) * 100, 2),
           "ret_close_pct": round((d0[4] / offer - 1) * 100, 2)}
    if idx + 4 < len(bars):
        out["ret_d5_close_pct"] = round((bars[idx + 4][4] / offer - 1) * 100, 2)
    if idx + 19 < len(bars):
        out["ret_d20_close_pct"] = round((bars[idx + 19][4] / offer - 1) * 100, 2)
    return out


def build(months: int | None, recent_days: int | None, *, ensure_prices: bool = True, verbose: bool = True) -> dict:
    today = date.today()
    if months:
        bgn = (today - timedelta(days=30 * months)).strftime("%Y%m%d")
    else:
        bgn = (today - timedelta(days=recent_days or 45)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    # 3개월 창으로 나눠 수집(DART 창 제한)
    rows: list[dict] = []
    b = datetime.strptime(bgn, "%Y%m%d").date()
    while b <= today:
        e = min(b + timedelta(days=89), today)
        rows += dart_list("C", b.strftime("%Y%m%d"), e.strftime("%Y%m%d"))
        b = e + timedelta(days=1)
    corp = json.load(open(CORP, encoding="utf-8")) if CORP.exists() else {}
    code2stock = {v: k for k, v in corp.items()} if isinstance(corp, dict) else {}
    by_corp: dict[str, list[dict]] = {}
    for r in rows:
        by_corp.setdefault(r.get("corp_code", ""), []).append(r)
    ipo_corps = {}
    for cc, rs in by_corp.items():
        first = [r for r in rs if "증권신고서(지분증권)" in r.get("report_nm", "") and "정정" not in r.get("report_nm", "")]
        if not first:
            continue
        first.sort(key=lambda r: r["rcept_dt"])
        f0 = first[0]
        if (f0.get("stock_code") or "").strip():
            continue  # 상장사 유상증자
        ipo_corps[cc] = {"corp_code": cc, "corp_name": f0.get("corp_name"), "filed_at": f0["rcept_dt"], "rcept_no": f0["rcept_no"],
                         "filings": sorted([{"rcept_no": r["rcept_no"], "report_nm": r["report_nm"], "rcept_dt": r["rcept_dt"],
                                             "stock_code": (r.get("stock_code") or "").strip()} for r in rs], key=lambda x: x["rcept_dt"])}
    # 기존 원장(멱등 갱신)
    old = {}
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line); old[r["corp_code"]] = r
            except (ValueError, KeyError):
                pass
    out_rows = []
    for cc, info in sorted(ipo_corps.items(), key=lambda kv: kv[1]["filed_at"]):
        prev = old.get(cc, {})
        # 본문: 발행조건확정 > 최신 증권신고서(지분증권)/투자설명서 순으로 공모가 확정본 우선
        confirm = [f for f in info["filings"] if "발행조건확정" in f["report_nm"]]
        base = [f for f in info["filings"] if "증권신고서(지분증권)" in f["report_nm"] or "투자설명서" in f["report_nm"]]
        pick = (confirm[-1] if confirm else (base[-1] if base else None))
        fields = prev.get("fields") or {}
        if pick and (prev.get("parsed_rcept_no") != pick["rcept_no"]):
            text = doc_text(pick["rcept_no"])
            fields = parse_ipo_doc(text) if text else fields
            time.sleep(0.15)
        stock = next((f["stock_code"] for f in reversed(info["filings"]) if f["stock_code"]), "") or code2stock.get(cc, "") or prev.get("stock_code", "")
        offer = fields.get("offer_price") or fields.get("offer_price_guess")
        listing = fields.get("listing_date")
        status = "listed" if stock else ("subscribed" if confirm else "filed")
        res = prev.get("outcome") or {}
        if stock and offer and (not res or res.get("no_bars")) and (not listing or listing <= today.isoformat()):
            res = outcome(stock, listing, offer, ensure=ensure_prices)
        row = {"corp_code": cc, "corp_name": info["corp_name"], "stock_code": stock, "filed_at": info["filed_at"], "status": status,
               "parsed_rcept_no": pick["rcept_no"] if pick else prev.get("parsed_rcept_no"), "fields": fields, "offer_price": offer,
               "listing_date": listing, "spac": bool(fields.get("spac")), "outcome": res, "n_filings": len(info["filings"]),
               "updated_at": datetime.now(KST).isoformat(timespec="seconds")}
        out_rows.append(row)
        if verbose:
            o = res or {}
            print(f"  {info['filed_at']} {info['corp_name']:<18} {stock or '------'} {status:<10} 공모가 {offer} 상장 {listing} "
                  f"{'SPAC ' if row['spac'] else ''}첫날 시가 {o.get('ret_open_pct')} 종가 {o.get('ret_close_pct')}")
    # 원장: 기존 + 갱신(멱등)
    merged = {**old, **{r["corp_code"]: r for r in out_rows}}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("w", encoding="utf-8") as fh:
        for r in sorted(merged.values(), key=lambda x: x["filed_at"]):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return summarize(list(merged.values()))


def summarize(rows: list[dict]) -> dict:
    today = date.today().isoformat()
    listed = [r for r in rows if r.get("outcome") and not r["outcome"].get("no_bars") and not r.get("spac")]
    upcoming = [r for r in rows if r.get("status") != "listed" or (r.get("listing_date") and r["listing_date"] > today)]
    upcoming.sort(key=lambda r: (r.get("listing_date") or "9999", r["filed_at"]))
    recent = sorted(listed, key=lambda r: r["outcome"].get("day1_date", ""), reverse=True)[:25]
    stats = {}
    if listed:
        op = [r["outcome"]["ret_open_pct"] for r in listed]; cl = [r["outcome"]["ret_close_pct"] for r in listed]
        stats = {"n": len(listed), "open_mean": round(st.mean(op), 2), "open_median": round(st.median(op), 2),
                 "open_pos_pct": round(sum(1 for v in op if v > 0) / len(op) * 100, 1),
                 "open_ge50_pct": round(sum(1 for v in op if v >= 50) / len(op) * 100, 1),
                 "close_mean": round(st.mean(cl), 2), "close_median": round(st.median(cl), 2),
                 "close_pos_pct": round(sum(1 for v in cl if v > 0) / len(cl) * 100, 1),
                 "close_le_minus10_pct": round(sum(1 for v in cl if v <= -10) / len(cl) * 100, 1)}
    cal = {"generated_at": datetime.now(KST).isoformat(timespec="seconds"), "n_total": len(rows), "n_listed": len(listed),
           "n_spac": sum(1 for r in rows if r.get("spac")), "stats": stats,
           "upcoming": [{k: r.get(k) for k in ("corp_name", "stock_code", "status", "offer_price", "listing_date", "filed_at", "spac")} | {"band": (r.get("fields") or {}).get("band"), "subscription": (r.get("fields") or {}).get("subscription")} for r in upcoming[:30]],
           "recent": [{k: r.get(k) for k in ("corp_name", "stock_code", "offer_price", "listing_date")} | {k: r["outcome"].get(k) for k in ("day1_date", "ret_open_pct", "ret_high_pct", "ret_close_pct", "ret_d5_close_pct")} for r in recent],
           "note": "1주 균등배정 가정·공모가 대비. 청약 자동화 없음(반자동). SPAC 제외. 판정은 분포(양의 꼬리·손실 비율)로."}
    CAL.write_text(json.dumps(cal, ensure_ascii=False, indent=1), encoding="utf-8")
    return cal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=None)
    ap.add_argument("--recent-days", type=int, default=45)
    ap.add_argument("--no-ensure", action="store_true", help="가격 CSV 생성(KIS) 생략")
    a = ap.parse_args()
    if not _key():
        print("DART_API_KEY 없음"); return 1
    cal = build(a.months, a.recent_days, ensure_prices=not a.no_ensure)
    print(json.dumps({k: cal[k] for k in ("n_total", "n_listed", "n_spac", "stats")}, ensure_ascii=False))
    print(f"saved {LEDGER} / {CAL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
