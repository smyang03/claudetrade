from __future__ import annotations

"""교훈 forward-validation 축적 배치 (컴포넌트①②의 채점/누적).

selection_log를 읽어 (교훈×시장×국면) 셀을 반사실 채점하고 validated_lesson store에 upsert한다.
세션마감 hook(trading_bot.session_close)과 tool(tools/run_lesson_validation.py)이 공용으로 호출.

설계: docs/important/LESSON_QUALITY_CONFIG_PIPELINE_DESIGN_20260617.md
- config 토글(LESSON_VALIDATION_ENABLED/APPLY_MODE)과 **무관하게 항상 축적**(반영만 게이트).
- read-only(selection_log) + 격리 store만 씀 → 라이브 매매 무영향.
- 매 실행마다 *현재 성숙된 forward_3d 전체*로 재채점(idempotent upsert). 새 세션 데이터는 forward
  성숙(약 3거래일) 후 자동 반영 → pending이 점점 valid로 승격되며 쌓인다.
"""

import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEL_DB = ROOT / "data" / "ticker_selection_log.db"

from minority_report import lesson_validation as lv


def rescore_lessons(
    lesson_key: str = "watch_only_missed_runup_ratio",
    selection_db: str | None = None,
    store_db: str | None = None,
) -> list[dict]:
    """selection_log 전체를 (시장×consensus regime) 셀로 반사실 채점 → store upsert. 채점 셀 반환."""
    sel = str(selection_db or SEL_DB)
    con = sqlite3.connect(f"file:{sel}?mode=ro", uri=True, timeout=5.0)
    try:
        con.execute("PRAGMA busy_timeout=5000")
        rows = con.execute(
            "SELECT market,date,trade_ready,forward_3d,consensus_mode FROM ticker_selection_log "
            "WHERE forward_3d IS NOT NULL"
        ).fetchall()
    finally:
        con.close()

    # (market, regime) → month → {tr:[fwd], wo:[fwd]} (부호 일관 계산용 월 분할)
    g: dict[tuple, dict] = defaultdict(lambda: defaultdict(lambda: {"tr": [], "wo": []}))
    for market, dt, tr, fwd, cmode in rows:
        regime = lv.regime_from_consensus_mode(cmode)
        if not regime:
            continue
        bucket = g[(market, regime)][str(dt)[:7]]
        (bucket["tr"] if tr == 1 else bucket["wo"]).append(fwd)

    cells = []
    for (market, regime), months in g.items():
        all_tr = [f for m in months.values() for f in m["tr"]]
        all_wo = [f for m in months.values() for f in m["wo"]]
        overall_gain = lv.counterfactual_gain(all_wo, all_tr)
        sub_gains = []
        for m in months.values():
            if len(m["tr"]) >= 3 and len(m["wo"]) >= 5:
                sub_gains.append(lv.counterfactual_gain(m["wo"], m["tr"]))
        sessions = lv.sign_consistency_sessions(sub_gains, overall_gain)
        cells.append(lv.score_cell(lesson_key, market, regime, all_wo, all_tr,
                                   sessions_confirmed=sessions))

    if cells:
        lv.upsert_cells(cells, db_path=store_db)
    return cells


def rescore_safe(store_db: str | None = None) -> int:
    """세션마감 hook용 — 예외를 절대 위로 던지지 않는다(봇 루프 보호). 채점 셀 수 반환(실패 시 0)."""
    try:
        return len(rescore_lessons(store_db=store_db))
    except Exception:
        return 0
