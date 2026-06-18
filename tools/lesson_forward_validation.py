from __future__ import annotations

"""교훈 forward-validation 채점기 (PoC) — 운영자 비전 1단계.

현재 lesson 파이프라인은 교훈을 빈도/severity/recency로만 점수화하고 "그 교훈대로 했으면
실제 forward 성과가 개선됐나"를 검증하지 않는다(minority_report/lesson_quality._score_item 확인).
이 도구는 재발 메트릭-교훈을 forward 결과로 채점해 "맞았는지"를 별도 격리 DB에 누적한다.

검증 대상(현재 재발 3개 중 측정 가능 2개):
- watch_only_missed_runup → 처방="watch_only 더 승격". 검증=승격대상(watch_only) forward_3d가
  trade_ready보다 좋았나. 좋으면 valid(+), 나쁘면 invalid(-).
- trade_ready_signal_conversion → 처방="trade_ready 임계 완화". 동일 축으로 근사.

국면 오염 차단 위해 시장×월별로 채점. read-only(selection_log), 결과는 격리 DB.
"""

import sqlite3
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEL_DB = ROOT / "data" / "ticker_selection_log.db"
OUT_DB = ROOT / "data" / "analysis" / "lesson_validation.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lesson_validation (
    lesson_key TEXT, market TEXT, period TEXT,
    n_trade_ready INTEGER, n_watch_only INTEGER,
    tr_fwd3d_med REAL, wo_fwd3d_med REAL,
    promote_gain REAL,   -- wo_fwd3d - tr_fwd3d: 승격이 도움됐으면 +
    verdict TEXT,        -- valid / invalid / neutral / insufficient
    synced_at TEXT,
    PRIMARY KEY (lesson_key, market, period)
)
"""


def _med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def main() -> int:
    con = sqlite3.connect(f"file:{SEL_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT market, substr(date,1,7) AS period, trade_ready, forward_3d "
        "FROM ticker_selection_log WHERE forward_3d IS NOT NULL"
    ).fetchall()
    con.close()

    # 시장×월 그룹
    groups: dict[tuple, dict] = {}
    for market, period, tr, fwd in rows:
        g = groups.setdefault((market, period), {"tr": [], "wo": []})
        (g["tr"] if tr == 1 else g["wo"]).append(fwd)

    out = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lesson_key = "watch_only_missed_runup_ratio"  # 처방=승격확대, 동일 검증축
    for (market, period), g in sorted(groups.items()):
        n_tr, n_wo = len(g["tr"]), len(g["wo"])
        tr_med, wo_med = _med(g["tr"]), _med(g["wo"])
        if n_tr < 5 or n_wo < 5 or tr_med is None or wo_med is None:
            verdict, gain = "insufficient", None
        else:
            gain = wo_med - tr_med  # 승격(watch_only 끌어올림)이 forward로 도움됐나
            if gain > 0.5:
                verdict = "valid"        # 승격이 실제로 forward+ → 교훈 맞음
            elif gain < -0.5:
                verdict = "invalid"      # 승격대상이 더 나빴음 → 교훈 틀림(함정)
            else:
                verdict = "neutral"
        out.append((lesson_key, market, period, n_tr, n_wo, tr_med, wo_med, gain, verdict, now))

    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    w = sqlite3.connect(str(OUT_DB))
    try:
        w.executescript(SCHEMA)
        w.executemany(
            "INSERT INTO lesson_validation VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(lesson_key,market,period) DO UPDATE SET "
            "n_trade_ready=excluded.n_trade_ready,n_watch_only=excluded.n_watch_only,"
            "tr_fwd3d_med=excluded.tr_fwd3d_med,wo_fwd3d_med=excluded.wo_fwd3d_med,"
            "promote_gain=excluded.promote_gain,verdict=excluded.verdict,synced_at=excluded.synced_at",
            out,
        )
        w.commit()
    finally:
        w.close()

    # 요약 출력
    print(f"lesson_validation: {len(out)} rows -> {OUT_DB}")
    for market in ("KR", "US"):
        sub = [o for o in out if o[1] == market and o[8] != "insufficient"]
        if not sub:
            continue
        v = sum(1 for o in sub if o[8] == "valid")
        iv = sum(1 for o in sub if o[8] == "invalid")
        nu = sum(1 for o in sub if o[8] == "neutral")
        gains = [o[7] for o in sub if o[7] is not None]
        print(f"  {market}: {len(sub)}개월 | valid {v} / invalid {iv} / neutral {nu} | "
              f"promote_gain 중앙 {st.median(gains):+.2f}%p (음수=승격교훈 틀림)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
