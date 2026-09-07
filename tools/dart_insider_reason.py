# -*- coding: utf-8 -*-
"""KR 내부자 소유상황보고서 본문 → 보고사유(장내매수/장내매도/증여/스톡옵션행사/신규선임…) 원장 (2026-09-08).

elestock 원장(kr_insider_ledger.jsonl)은 증감수량 부호만 있어 장내매수와 증여·옵션행사를 구분 못 한다. 본문 표 "보고사유"(또는 "취득/처분방법")를
정규식으로 읽어 kr_insider_reason.jsonl에 rcept_no 단위로 저장. discovery_pools._cluster는 reason 원장이 덮는 rcept_no는 장내매수만 센다.
예산: DART 일일 한도 공유 → 기본 --max 1500, 최근 보고서부터(forward에 먼저 쓰이므로). 래퍼가 매일 이어받는다(약 10일).
사용: python tools/dart_insider_reason.py [--max 1500]
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SRC = ROOT / "data" / "shadow" / "kr_insider_ledger.jsonl"
OUT = ROOT / "data" / "shadow" / "kr_insider_reason.jsonl"
SLEEP = 0.12
BUY_PAT = re.compile(r"장내\s*매수|시간외\s*매수|장외\s*매수|공개매수")
SELL_PAT = re.compile(r"장내\s*매도|시간외\s*매도|장외\s*매도")
OTHER = {"gift": r"증여|수증", "option": r"스톡옵션|주식매수선택권|옵션\s*행사", "appoint": r"신규\s*선임|임원\s*선임|신규\s*보고",
         "bonus": r"무상\s*증자|주식\s*배당", "rights": r"유상\s*증자|신주\s*인수", "conversion": r"전환|교환\s*청구", "inherit": r"상속"}


def _env_key() -> None:
    if os.getenv("DART_API_KEY", "").strip():
        return
    for line in (ROOT / ".env.live").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("DART_API_KEY="):
            os.environ["DART_API_KEY"] = line.split("=", 1)[1].strip()


def classify(text: str) -> dict:
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(r"보고\s*사유(.{0,200})", t)
    seg = m.group(1) if m else t[:4000]
    kinds = []
    if BUY_PAT.search(seg):
        kinds.append("market_buy")
    if SELL_PAT.search(seg):
        kinds.append("market_sell")
    for k, pat in OTHER.items():
        if re.search(pat, seg):
            kinds.append(k)
    if not kinds:
        # 표 본문 전체에서 거래 행의 '취득/처분 방법' 검색(보고사유 항목이 없는 서식)
        if BUY_PAT.search(t):
            kinds.append("market_buy")
        elif SELL_PAT.search(t):
            kinds.append("market_sell")
    return {"reasons": kinds or ["unknown"], "reason_text": seg[:120] if m else None}


def main() -> int:
    _env_key()
    from runtime.kr_event_lane import dart_document_text
    args = sys.argv[1:]
    mx = int(args[args.index("--max") + 1]) if "--max" in args else 1500
    done = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["rcept_no"])
            except (ValueError, KeyError):
                continue
    rows = []
    for line in SRC.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if (r.get("irds_cnt") or 0) > 0 and str(r.get("rcept_dt", "")) >= "2025-06-01" and r.get("rcept_no") not in done:
            rows.append(r)
    rows.sort(key=lambda r: r["rcept_dt"], reverse=True)   # 최근 보고서부터
    n = 0; ok = 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        for r in rows[:mx]:
            text = dart_document_text(r["rcept_no"], max_chars=60000); n += 1; time.sleep(SLEEP)
            c = classify(text) if text else {"reasons": ["doc_unavailable"], "reason_text": None}
            if text:
                ok += 1
            fh.write(json.dumps({"rcept_no": r["rcept_no"], "stock": r["stock"], "rcept_dt": r["rcept_dt"], "repror": r.get("repror"),
                                 "irds_cnt": r.get("irds_cnt"), **c, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                                ensure_ascii=False) + "\n")
            if n % 100 == 0:
                fh.flush(); print(f"[REASON] {n}/{min(mx, len(rows))} 본문 {ok}")
    print(f"[REASON] 완료 — 본문 {n}건(성공 {ok}) · 잔여 {max(0, len(rows) - n)} · 원장 {len(done) + n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
