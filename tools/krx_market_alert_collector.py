# -*- coding: utf-8 -*-
"""KRX 시장경보(투자주의·투자경고·투자위험) 일일 스냅 → 지정/해제 원장 (2026-09-08, P13 — B등급 forward 전용).

출처: 네이버 금융 투자경보 페이지(https://finance.naver.com/sise/investment_alert.naver?type=caution|warning|risk).
KRX data 포털은 400(차단)이라 네이버 페이지 스냅으로 대체. 백필 불가 → 첫 스냅부터 diff.
원장: data/shadow/krx_market_alert.jsonl — 행 종류 snapshot(일자·종류·종목 목록) / event(designated|released, 종목, 종류, 일자)
사용: python tools/krx_market_alert_collector.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "shadow" / "krx_market_alert.jsonl"
TYPES = {"caution": "투자주의", "warning": "투자경고", "risk": "투자위험"}
HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}


def fetch(kind: str) -> list[dict]:
    url = f"https://finance.naver.com/sise/investment_alert.naver?type={kind}"
    html = urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=30).read().decode("euc-kr", "ignore")
    rows = []
    for m in re.finditer(r'<a href="/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>(.*?)</tr>', html, re.S):
        code, name, rest = m.group(1), m.group(2).strip(), m.group(3)
        ds = re.findall(r"(\d{4}\.\d{2}\.\d{2})", rest)
        rows.append({"code": code, "name": name, "designated": ds[0].replace(".", "-") if ds else None})
    return rows


def main() -> int:
    today = date.today().isoformat()
    prev: dict[str, set] = {}
    if OUT.exists():
        last: dict[str, dict] = {}
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind_row") == "snapshot":
                last[r["alert"]] = r
        prev = {k: set(v.get("codes") or []) for k, v in last.items()}
        if any(v.get("date") == today for v in last.values()):
            print(f"[ALERT] {today} 이미 기록"); return 0
    n_ev = 0
    with OUT.open("a", encoding="utf-8") as fh:
        for kind, label in TYPES.items():
            try:
                rows = fetch(kind)
            except Exception as exc:  # noqa: BLE001
                print(f"[ALERT] {label} 실패 {exc}"); continue
            codes = sorted({r["code"] for r in rows})
            fh.write(json.dumps({"kind_row": "snapshot", "date": today, "alert": label, "n": len(codes), "codes": codes,
                                 "rows": rows, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
            if label in prev:
                for c in sorted(set(codes) - prev[label]):
                    fh.write(json.dumps({"kind_row": "event", "date": today, "alert": label, "event": "designated", "code": c}, ensure_ascii=False) + "\n"); n_ev += 1
                for c in sorted(prev[label] - set(codes)):
                    fh.write(json.dumps({"kind_row": "event", "date": today, "alert": label, "event": "released", "code": c}, ensure_ascii=False) + "\n"); n_ev += 1
            print(f"[ALERT] {label} {len(codes)}종목")
    print(f"[ALERT] 완료 — 이벤트 {n_ev}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
