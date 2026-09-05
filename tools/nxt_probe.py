#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NXT(넥스트레이드) 시세 지원 프로브 — KIS inquire-price를 시장구분 J(KRX) / NX(NXT) / UN(통합)으로 호출해 응답을 비교한다.

목적: 공시 이벤트 레인을 15:30~20:00 시간외(NXT)로 확장할 수 있는지의 첫 게이트. 주문 없음, 읽기 전용.
출력: state/nxt_probe.json (대시보드 연구 패널이 읽음) + 콘솔 표.
사용: python tools/nxt_probe.py [--tickers 005930 000660 035420]
판정: NX 응답에 가격(stck_prpr)·거래량이 오고 KRX와 다른 값이면 '지원'. 장외 시간엔 마지막 체결가만 와도 '지원(장외)'.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "state" / "nxt_probe.json"
KST = timezone(timedelta(hours=9))


def probe(tickers: list[str]) -> dict:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.live", override=False)
    import kis_api as k
    token = k.get_access_token(market="KR")
    rows = []
    for t in tickers:
        row = {"ticker": t}
        for div in ("J", "NX", "UN"):
            try:
                resp = k._kis_get(f"{k.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
                                  headers=k._headers(token, "FHKST01010100"),
                                  params={"FID_COND_MRKT_DIV_CODE": div, "FID_INPUT_ISCD": t}, timeout=10)
                j = resp.json()
                o = j.get("output") or {}
                row[div] = {"rt_cd": j.get("rt_cd"), "msg": str(j.get("msg1", ""))[:40], "price": o.get("stck_prpr"),
                            "volume": o.get("acml_vol"), "open": o.get("stck_oprc"), "high": o.get("stck_hgpr"), "low": o.get("stck_lwpr"),
                            "name": o.get("hts_kor_isnm"), "keys": len(o)}
            except Exception as exc:
                row[div] = {"error": str(exc)[:120]}
        rows.append(row)
    ok = [r for r in rows if (r.get("NX") or {}).get("rt_cd") == "0" and (r.get("NX") or {}).get("price") not in (None, "", "0")]
    now = datetime.now(KST)
    verdict = ("supported" if len(ok) == len(rows) and rows else ("partial" if ok else "unsupported"))
    return {"probed_at": now.isoformat(timespec="seconds"), "session_hint": "장외" if now.weekday() >= 5 or not ("09:00" <= now.strftime("%H:%M") <= "20:00") else "장중/시간외",
            "verdict": verdict, "rows": rows,
            "note": "NX=넥스트레이드 단독, UN=KRX+NXT 통합. 시간외(15:30~20:00) 실거래 판단은 평일 그 시간대에 재실행해 NX 거래량 증가를 확인해야 한다."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", default=["005930", "000660", "035420", "196170"])
    a = ap.parse_args()
    res = probe(a.tickers)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[NXT probe] {res['probed_at']} {res['session_hint']} → {res['verdict']}")
    for r in res["rows"]:
        print(f"  {r['ticker']}: " + " | ".join(f"{d}: {r.get(d, {}).get('price')} vol {r.get(d, {}).get('volume')} rt {r.get(d, {}).get('rt_cd')} {r.get(d, {}).get('msg') or r.get(d, {}).get('error', '')}" for d in ("J", "NX", "UN")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
