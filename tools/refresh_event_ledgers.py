# -*- coding: utf-8 -*-
"""신규 전략 원장 일일 갱신 래퍼 (2026-09-08) — schtask claudetrade_event_ledgers 주중 21:05 KST, PT1H.

순서(각각 try — 한 단계 실패가 뒤를 막지 않는다):
 1 DART 내부자 소유보고(elestock, 종목당 1회, refresh-days 1)  2 거래계획 증분(list D + 본문)
 3 자사주 기간·무상증자 기준일 신규 본문  4 US Form 4 당일(일일 인덱스)  5 KRX 시장경보(네이버 스냅)
 6 US 어닝 발표일(refresh-days 7 — 주 1회 실효)  7 패닉 원장 정산(settle)
레인이 20:01에 끝나 DART 키 경합 없음, 07:20 US 체인 전에 끝난다. 로그: logs/event_ledgers/<date>.log
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs" / "event_ledgers"
STEPS = [
    ("insider_holdings", ["tools/dart_insider_ledger.py", "--holdings-only", "--refresh-days", "1"]),
    ("insider_plans", ["tools/dart_insider_ledger.py", "--plans-only"]),
    ("corp_action_terms", ["tools/dart_corp_action_terms.py"]),
    ("form4_daily", ["tools/edgar_form4_daily.py", "--from", "2026-04-01", "--max-days", "8"]),   # 2026-04~ 공백을 하루 8거래일씩 이어받아 채운다(재개 가능)
    ("krx_alert", ["tools/krx_market_alert_collector.py"]),
    ("earnings_dates", ["tools/us_earnings_dates_cache.py", "--refresh-days", "7"]),
    ("panic_settle", ["tools/us_panic_close_shadow.py", "settle"]),
    ("absorption_settle", ["tools/kr_absorption_shadow.py", "settle"]),
    ("open_impact_settle", ["tools/us_open_impact_collector.py", "settle"]),
    ("insider_reason", ["tools/dart_insider_reason.py", "--max", "1500"]),      # 09-08: 소유보고 본문 사유(장내매수 구분), 하루 1,500건
    ("event_family_report", ["tools/event_family_report.py", "--json", "data/analysis/event_family_report_20260907.json"]),
    ("forward_gate_watch", ["tools/forward_gate_watch.py"]),                    # 09-08: forward 판정 자동화(상태 변화 때만 텔레그램)
    ("panic_report", ["tools/us_panic_close_shadow.py", "report"]),
    ("canary_materialize", ["tools/canary_materializer.py", "both"]),      # 09-09: 캐너리 신호 파일(다음 세션)+리허설 원장(브리지 미배선)
]


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"{date.today().isoformat()}.log"
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    fails = 0
    with log.open("a", encoding="utf-8") as fh:
        for name, args in STEPS:
            if only and name not in only:
                continue
            t0 = time.time()
            try:
                r = subprocess.run([sys.executable, *args], cwd=str(ROOT), capture_output=True, text=True, timeout=2400, encoding="utf-8", errors="replace")
                tail = (r.stdout or "").strip().splitlines()[-3:]
                fh.write(f"[{name}] rc={r.returncode} {time.time() - t0:.0f}s | " + " / ".join(tail) + "\n")
                if r.returncode != 0:
                    fails += 1; fh.write((r.stderr or "")[-1500:] + "\n")
            except Exception as exc:  # noqa: BLE001
                fails += 1; fh.write(f"[{name}] EXC {exc}\n")
            fh.flush()
    print(f"[LEDGERS] 완료 — 실패 {fails} (log {log})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
