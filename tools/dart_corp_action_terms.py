# -*- coding: utf-8 -*-
"""DART 코퍼레이트 액션 본문 조건 원장 — 무상증자 신주배정기준일(권리락일), 자사주 취득 예정기간·방법 (2026-09-07 P6/N4).

입력: data/analysis/dart_events_12m.jsonl(09-03 12개월 재생, kind 한글) + forward data/shadow/kr_event_signals.jsonl(kind 영문).
본문: OpenDART document.xml(zip) → 태그 제거 텍스트(최대 80k자). 정규식으로
  - 무상증자결정: 신주배정기준일 → 권리락일 = 기준일 직전 거래일(가상 북에서 KR 봉 달력으로 계산), 1주당 신주 배정 수
  - 자기주식취득결정: 취득예정기간 시작·종료, 취득방법(장내/신탁/장외), 취득예정주식수
재개 가능(rcept_no 단위). 원장: data/shadow/kr_dart_terms.jsonl
사용: python tools/dart_corp_action_terms.py [--max N]
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SRC_BACKFILL = ROOT / "data" / "analysis" / "dart_events_12m.jsonl"
SRC_FORWARD = ROOT / "data" / "shadow" / "kr_event_signals.jsonl"
OUT = ROOT / "data" / "shadow" / "kr_dart_terms.jsonl"
KIND_MAP = {"무상증자결정": "bonus_issue", "자기주식취득결정": "buyback", "bonus_issue": "bonus_issue", "buyback": "buyback"}
_DATE = r"(\d{4})\s*[.\-년/]\s*(\d{1,2})\s*[.\-월/]\s*(\d{1,2})"
SLEEP = 0.12


def _env_key() -> None:
    if os.getenv("DART_API_KEY", "").strip():
        return
    for line in (ROOT / ".env.live").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("DART_API_KEY="):
            os.environ["DART_API_KEY"] = line.split("=", 1)[1].strip()


def _dates(text: str) -> list[str]:
    out = []
    for y, m, d in re.findall(_DATE, text):
        try:
            out.append(date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            continue
    return out


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_bonus(text: str) -> dict:
    m = re.search(r"신주\s*배정\s*기준일(.{0,60})", text)
    ds = _dates(m.group(1)) if m else []
    r = re.search(r"1주당\s*신주\s*배정\s*주식수[^\d]{0,30}([\d.,]+)", text)
    return {"record_date": ds[0] if ds else None, "ratio": _num(r.group(1)) if r else None}


def parse_buyback(text: str) -> dict:
    m = re.search(r"취득\s*예[정상]\s*기간(.{0,120})", text)
    ds = _dates(m.group(1)) if m else []
    method = None
    mm = re.search(r"취득\s*방법(.{0,80})", text)
    if mm:
        seg = mm.group(1)
        method = "market" if ("장내" in seg or "코스닥시장" in seg or "유가증권시장" in seg) else ("trust" if "신탁" in seg else ("otc" if "장외" in seg else None))
    q = re.search(r"취득\s*예정\s*주식\s*\(주\)\s*보통주식\s*([\d,]+)", text) or re.search(r"취득\s*예정\s*주식수[^\d]{0,40}([\d,]+)", text)
    return {"start": ds[0] if ds else None, "end": ds[1] if len(ds) > 1 else None, "method": method,
            "qty": _num(q.group(1)) if q else None}


def _targets() -> list[dict]:
    out = []
    if SRC_BACKFILL.exists():
        for line in SRC_BACKFILL.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k = KIND_MAP.get(str(r.get("kind")))
            if not k or "정정" in str(r.get("report", "")):
                continue
            d = str(r.get("date", ""))
            out.append({"rcept_no": r["rcept_no"], "stock": str(r["stock"]), "kind": k, "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "src": "backfill"})
    if SRC_FORWARD.exists():
        for line in SRC_FORWARD.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k = KIND_MAP.get(str(r.get("kind")))
            if not k or r.get("is_correction") or not r.get("stock_code"):
                continue
            out.append({"rcept_no": r["rcept_no"], "stock": str(r["stock_code"]), "kind": k, "date": str(r.get("session_date")), "src": "forward"})
    return out


def main() -> int:
    _env_key()
    from runtime.kr_event_lane import dart_document_text
    args = sys.argv[1:]
    max_n = int(args[args.index("--max") + 1]) if "--max" in args else None
    done = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["rcept_no"])
            except (ValueError, KeyError):
                continue
    n = 0; n_ok = 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        for t in _targets():
            if t["rcept_no"] in done:
                continue
            if max_n is not None and n >= max_n:
                break
            text = dart_document_text(t["rcept_no"], max_chars=80000); n += 1; time.sleep(SLEEP)
            p = (parse_bonus if t["kind"] == "bonus_issue" else parse_buyback)(text) if text else {}
            row = {**t, **p, "doc_ok": bool(text), "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n"); done.add(t["rcept_no"])
            if p and any(v for k, v in p.items()):
                n_ok += 1
            if n % 50 == 0:
                fh.flush(); print(f"[TERMS] {n}건 · 파싱 성공 {n_ok}")
    print(f"[TERMS] 완료 — 본문 {n}건 · 파싱 성공 {n_ok} · 원장 {len(done)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
