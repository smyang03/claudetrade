#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KR 공모주(IPO) 캘린더·균등배정 shadow 원장 (read-only, 2026-09-06).

근거: 한국 개인에게 남은 구조적 우위 후보 — 균등배정은 소액 청약증거금으로 참여 가능하고 첫날 수익 분포가 양의 꼬리를 가진다는 가설.
자동 청약은 API가 없어 반자동이므로 여기서는 **원장과 캘린더**만 만든다. 판정은 표본이 쌓인 뒤 분포로(평균이 아니라 양의 꼬리·손실 비율).

소스(09-06 실측): 38커뮤니케이션 신규상장 표(기업명·신규상장일·공모가·시초가·첫날종가, 페이지네이션) — DART 발행공시 본문 파싱은
공모가 오인식·상장일 결측·종목코드 미해결이라 1차 소스로 부적합(같은 날 실측 19건 중 0건 정산). 외부 표는 생존편향 없음(상장 전부),
단 '현재가'는 지연·수정주가 미반영이므로 쓰지 않는다. 출처·수집 시각을 행마다 남긴다.
계산: 1주 균등배정 가정 · 공모가 대비 시초가(ret_open) · 첫날종가(ret_close). 시초가 매도 = 청약 참여자가 잡을 수 있는 몫.
출력: data/shadow/kr_ipo_ledger.jsonl ((기업명,상장일) 멱등) · data/shadow/kr_ipo_calendar.json (대시보드용 upcoming/recent/stats)
사용: python tools/kr_ipo_calendar.py --pages 8      # 이력(약 20행/페이지, 8페이지 ≈ 12개월)
      python tools/kr_ipo_calendar.py --pages 2      # 관측 체인용(신규·예정 갱신)
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "shadow" / "kr_ipo_ledger.jsonl"
CAL = ROOT / "data" / "shadow" / "kr_ipo_calendar.json"
KST = timezone(timedelta(hours=9))
SRC = "38.co.kr/fund/index.htm?o=nw"
UA = {"User-Agent": "Mozilla/5.0"}


def _fetch(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("euc-kr", "ignore")


def _table_rows(html: str, key: str) -> list[list[str]]:
    for tb in re.findall(r"<table.*?</table>", html, flags=re.S):
        if key in tb:
            out = []
            for tr in re.findall(r"<tr.*?</tr>", tb, flags=re.S):
                cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).replace("&nbsp;", "").strip()
                         for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S)]
                if any(cells):
                    out.append(cells)
            return out
    return []


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def is_spac(name: str) -> bool:
    return ("스팩" in name) or ("기업인수목적" in name)


def parse_listing_rows(rows: list[list[str]]) -> list[dict]:
    """헤더: 기업명 | 신규상장일 | 현재가 | 전일비 | 공모가 | 공모가대비 | 시초가 | 시초/공모 | 첫날종가"""
    out = []
    for r in rows:
        if len(r) < 9 or not re.match(r"\d{4}/\d{2}/\d{2}", r[1] or ""):
            continue
        name = r[0].strip()
        d = r[1].replace("/", "-")
        offer, opn, close = _num(r[4]), _num(r[6]), _num(r[8])
        pending = ("예정" in (r[8] or "")) or opn is None
        row = {"corp_name": name, "listing_date": d, "offer_price": offer, "day1_open": opn, "day1_close": close,
               "spac": is_spac(name), "status": "upcoming" if pending else "listed", "source": SRC}
        if offer and opn:
            row["ret_open_pct"] = round((opn / offer - 1) * 100, 2)
        if offer and close:
            row["ret_close_pct"] = round((close / offer - 1) * 100, 2)
        if opn and close:
            row["ret_open_to_close_pct"] = round((close / opn - 1) * 100, 2)
        out.append(row)
    return out


def fetch_listings(pages: int, *, sleep: float = 0.4) -> list[dict]:
    out = []
    for p in range(1, pages + 1):
        try:
            html = _fetch(f"http://www.38.co.kr/html/fund/index.htm?o=nw&page={p}")
        except Exception as exc:
            print(f"  page {p} 실패: {str(exc)[:80]}")
            break
        rows = parse_listing_rows(_table_rows(html, "신규상장일"))
        if not rows:
            break
        out += rows
        time.sleep(sleep)
    return out


def fetch_schedule() -> list[dict]:
    """청약 일정 표(있으면). 헤더에 '희망공모가'·'주간사'가 있는 표를 찾는다. 없으면 빈 리스트(상장예정은 신규상장 표의 '예정' 행으로 대체)."""
    try:
        html = _fetch("http://www.38.co.kr/html/fund/index.htm?o=k")
    except Exception:
        return []
    for key in ("희망공모가", "주간사", "공모주일정"):
        rows = _table_rows(html, key)
        rows = [r for r in rows if len(r) >= 4 and not r[0].startswith("[")]
        if len(rows) >= 2:
            hdr = rows[0]
            out = []
            for r in rows[1:]:
                rec = {"raw": r, "corp_name": r[0]}
                for i, h in enumerate(hdr[:len(r)]):
                    if "일정" in h or "청약" in h:
                        rec["subscription"] = r[i]
                    elif "확정" in h:
                        rec["offer_price"] = _num(r[i])
                    elif "희망" in h:
                        rec["band"] = r[i]
                    elif "경쟁률" in h:
                        rec["competition"] = r[i]
                    elif "주간사" in h or "주관" in h:
                        rec["underwriter"] = r[i]
                rec["spac"] = is_spac(r[0])
                out.append(rec)
            return out
    return []


def build(pages: int) -> dict:
    now = datetime.now(KST)
    listings = fetch_listings(pages)
    sched = fetch_schedule()
    old: dict[tuple, dict] = {}
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line); old[(r["corp_name"], r["listing_date"])] = r
            except (ValueError, KeyError):
                pass
    for r in listings:
        r["collected_at"] = now.isoformat(timespec="seconds")
        k = (r["corp_name"], r["listing_date"])
        prev = old.get(k)
        if prev and prev.get("status") == "listed" and r.get("status") == "listed":
            r["first_seen_at"] = prev.get("first_seen_at", r["collected_at"])
        else:
            r["first_seen_at"] = (prev or {}).get("first_seen_at", r["collected_at"])
        old[k] = r
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("w", encoding="utf-8") as fh:
        for r in sorted(old.values(), key=lambda x: x["listing_date"]):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return summarize(list(old.values()), sched, now)


def summarize(rows: list[dict], sched: list[dict], now: datetime) -> dict:
    today = now.date().isoformat()
    since = (now.date() - timedelta(days=365)).isoformat()
    listed = [r for r in rows if r.get("status") == "listed" and not r.get("spac") and r.get("ret_open_pct") is not None and r["listing_date"] >= since]
    spacs = [r for r in rows if r.get("status") == "listed" and r.get("spac") and r.get("ret_open_pct") is not None and r["listing_date"] >= since]
    upcoming = sorted([r for r in rows if r.get("status") == "upcoming" or r["listing_date"] > today], key=lambda r: r["listing_date"])
    recent = sorted(listed, key=lambda r: r["listing_date"], reverse=True)[:25]

    def _stats(sel):
        if not sel:
            return {}
        op = [r["ret_open_pct"] for r in sel]; cl = [r.get("ret_close_pct") for r in sel if r.get("ret_close_pct") is not None]
        return {"n": len(sel), "open_mean": round(st.mean(op), 2), "open_median": round(st.median(op), 2),
                "open_pos_pct": round(sum(1 for v in op if v > 0) / len(op) * 100, 1),
                "open_ge50_pct": round(sum(1 for v in op if v >= 50) / len(op) * 100, 1),
                "open_le_minus10_pct": round(sum(1 for v in op if v <= -10) / len(op) * 100, 1),
                "close_mean": round(st.mean(cl), 2) if cl else None, "close_median": round(st.median(cl), 2) if cl else None,
                "close_pos_pct": round(sum(1 for v in cl if v > 0) / len(cl) * 100, 1) if cl else None,
                "close_le_minus10_pct": round(sum(1 for v in cl if v <= -10) / len(cl) * 100, 1) if cl else None,
                "quarters": _by_quarter(sel)}

    def _by_quarter(sel):
        q: dict[str, list[float]] = {}
        for r in sel:
            d = r["listing_date"]; key = f"{d[:4]}Q{(int(d[5:7]) - 1) // 3 + 1}"
            q.setdefault(key, []).append(r["ret_open_pct"])
        return {k: {"n": len(v), "open_mean": round(st.mean(v), 1), "pos_pct": round(sum(1 for x in v if x > 0) / len(v) * 100, 0)} for k, v in sorted(q.items())}

    cal = {"generated_at": now.isoformat(timespec="seconds"), "source": SRC, "n_total": len(rows), "n_listed": len(listed), "n_spac": len(spacs),
           "stats": _stats(listed), "stats_spac": _stats(spacs),
           "upcoming": [{k: r.get(k) for k in ("corp_name", "listing_date", "offer_price", "spac", "status")} for r in upcoming[:20]],
           "schedule": sched[:20],
           "recent": [{k: r.get(k) for k in ("corp_name", "listing_date", "offer_price", "day1_open", "day1_close", "ret_open_pct", "ret_close_pct", "ret_open_to_close_pct")} | {"day1_date": r["listing_date"]} for r in recent],
           "note": "1주 균등배정 가정·공모가 대비 시초가/첫날종가(38커뮤니케이션 신규상장 표, 지연 표). 청약 자동화 없음(반자동). SPAC 분리. 판정은 분포(양의 꼬리·손실 비율)로. 청약증거금·배정 미달·수수료 미반영."}
    CAL.write_text(json.dumps(cal, ensure_ascii=False, indent=1), encoding="utf-8")
    return cal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2)
    a = ap.parse_args()
    cal = build(a.pages)
    s = cal.get("stats") or {}
    print(f"[IPO] 법인 {cal['n_total']} · 12개월 상장(비SPAC) {cal['n_listed']} · SPAC {cal['n_spac']} · 예정 {len(cal['upcoming'])}")
    if s:
        print(f"  시초가/공모가: 평균 {s['open_mean']:+.1f}% 중앙 {s['open_median']:+.1f}% 양수 {s['open_pos_pct']}% ≥+50% {s['open_ge50_pct']}% ≤−10% {s['open_le_minus10_pct']}%")
        print(f"  첫날종가/공모가: 평균 {s['close_mean']:+.1f}% 중앙 {s['close_median']:+.1f}% 양수 {s['close_pos_pct']}% ≤−10% {s['close_le_minus10_pct']}%")
        print("  분기:", s["quarters"])
    for r in cal["upcoming"][:8]:
        print(f"  예정 {r['listing_date']} {r['corp_name']}{' (SPAC)' if r['spac'] else ''} 공모가 {r['offer_price']}")
    print(f"saved {LEDGER} / {CAL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
