# -*- coding: utf-8 -*-
"""신규 전략 원장 일일 갱신 래퍼 (2026-09-08) — schtask claudetrade_event_ledgers 주중 21:05 KST, PT1H.

**순서 규약(2026-09-16 개정): 정산이 먼저, 외부 수집기가 나중.**

각 단계는 try로 감싸 한 단계 실패가 뒤를 막지 않는다. 그런데 **느린 단계는 뒤를 막는다** —
09-16에 실제로 그랬다. 앞쪽 8단계가 전부 DART/EDGAR 수집기였고,  하나가
09-15에 38분을 썼다. 09-16엔 총량이 schtask 제한(PT1H30M)에 닿아 4단계에서 프로세스가
잘렸고, **정산 단계(당시 9~20번)가 하나도 안 돌아** 가상 북 공급 0 · 패닉 미정산이 됐다.

같은 계열의 세 번째다(08-29 관측 하트비트가 스케줄러를 죽임, 09-03 캐시 경합).
**관측·수집이 핵심 경로를 굶기면 안 된다.** 그래서 두 가지를 고정한다.

  ① **정산·판정을 앞에**, 외부 API 수집을 뒤에 둔다. 잘리더라도 원장은 닫힌다.
  ② **단계별 timeout을 전체 예산에서 나눠 준다**(STEP_TIMEOUT). 전에는 단계마다 2400초라
     한 단계가 schtask 제한(5400초)의 절반을 먹을 수 있었다.

정산 단계는 전날 데이터로 도는 것이라 그날 수집 결과를 기다리지 않는다(순서 바꿔도 안전).
레인이 20:01에 끝나 DART 키 경합 없음, 07:20 US 체인 전에 끝난다.
로그: logs/event_ledgers/<date>.log
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs" / "event_ledgers"

# schtask 제한은 PT1H30M(5400초)다. 그 안에서 끝내려면 두 가지가 필요하다.
#  · 단계 하나가 예산을 독식하지 못하게 한다(STEP_TIMEOUT). 전에는 단계마다 2400초라
#    한 단계가 제한의 절반을 먹을 수 있었고, 09-16에 실제로 그래서 잘렸다.
#  · 남은 예산이 없으면 **시작하지 않는다**. 시작해서 중간에 죽으면 로그도 안 남는다.
TOTAL_BUDGET = 4800      # 5400에서 여유 10분
STEP_TIMEOUT = 900       # 한 단계 최대 15분 — 느린 수집기는 전부 재개 가능하다(아래 확인)
#   · earnings_dates: 종목 단위 재개(state 파일). 주 1회만 대량(09-14 9s / 09-15 2267s).
#   · insider_reason: --max 1500 일한도라 원래 잘라 쓴다(≈320s).
#   · form4_daily: days_done 커서로 재개. insider_*: refresh-days 증분.
#   잘려도 다음날 이어받으므로 손실은 "하루 지연"이지 "구멍"이 아니다.
MIN_SLICE = 60           # 이보다 적게 남으면 새 단계를 시작하지 않는다
STEPS = [
    ("panic_settle", ["tools/us_panic_close_shadow.py", "settle"]),
    ("absorption_settle", ["tools/kr_absorption_shadow.py", "settle"]),
    ("open_impact_settle", ["tools/us_open_impact_collector.py", "settle"]),
    ("kq_panic_settle", ["tools/kr_index_etf_panic_shadow.py", "settle"]),  # 09-09: KOSDAQ 급락일 지수 ETF 종가매수→다음시가 정산+리포트
    ("kq_close_rung", ["tools/kq_close_rung_shadow.py"]),                   # 09-13: 패닉 v2 바로 아래 종가 칸(-2.5~-2.0) 관측 — 별도 원장이라 v2 신호 오염 없음. 갭 칸은 회당 +0.003%로 죽었고 종가 칸만 살아 있어서 forward 30건을 쌓는다
    ("kq_panic_k_tier", ["tools/kq_panic_k_tier.py"]),                    # 09-12: 패닉 신호일에 급락 후보수(k) 등급 주석 — 사전등록 D의 판정 입력. 라이브 모듈 미변경, 캐시 결측일은 보류 후 재시도
    ("core_etf_book", ["tools/core_etf_book_shadow.py", "run"]),           # 09-09: 코어 ETF 북(ew·absmom) 월초 리밸런스·일일 MTM
    ("forward_gate_watch", ["tools/forward_gate_watch.py"]),                    # 09-08: forward 판정 자동화(상태 변화 때만 텔레그램)
    ("event_family_report", ["tools/event_family_report.py", "--json", "data/analysis/event_family_report_20260907.json"]),
    ("panic_report", ["tools/us_panic_close_shadow.py", "report"]),
    ("canary_materialize", ["tools/canary_materializer.py", "both"]),      # 09-09: 캐너리 신호 파일(다음 세션)+리허설 원장(브리지 미배선)

    # ── 이하 외부 API 수집 — 느리고, 잘려도 다음날 증분으로 따라잡는다 ──
    ("insider_holdings", ["tools/dart_insider_ledger.py", "--holdings-only", "--refresh-days", "1"]),
    ("insider_plans", ["tools/dart_insider_ledger.py", "--plans-only"]),
    ("corp_action_terms", ["tools/dart_corp_action_terms.py"]),
    ("form4_daily", ["tools/edgar_form4_daily.py", "--from", "2026-04-01", "--max-days", "20"]),
    ("krx_alert", ["tools/krx_market_alert_collector.py"]),
    ("insider_reason", ["tools/dart_insider_reason.py", "--max", "1500"]),      # 09-08: 소유보고 본문 사유(장내매수 구분), 하루 1,500건
    ("earnings_dates", ["tools/us_earnings_dates_cache.py", "--refresh-days", "7"]),
    ("independent_inputs", ["tools/collect_independent_research_inputs.py"]),
    ("independent_research", ["tools/independent_research_watch.py"]),  # research only; verified inputs required
]


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"{date.today().isoformat()}.log"
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    fails = 0
    started = time.time()
    skipped: list[str] = []
    with log.open("a", encoding="utf-8") as fh:
        for name, args in STEPS:
            if only and name not in only:
                continue
            left = TOTAL_BUDGET - (time.time() - started)
            if left < MIN_SLICE:
                # 예산 소진 — 남은 단계는 기록만 하고 건너뛴다. 전부 증분이라 내일 따라잡는다.
                skipped.append(name)
                continue
            t0 = time.time()
            try:
                r = subprocess.run([sys.executable, *args], cwd=str(ROOT), capture_output=True, text=True,
                                   timeout=min(STEP_TIMEOUT, left), encoding="utf-8", errors="replace")
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
