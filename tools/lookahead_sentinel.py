from __future__ import annotations

"""Lookahead 누수 sentinel (read-only).

ticker_selection_log의 forward_* / max_runup_* / max_drawdown_* 는 "선택일 +N거래일"의
사후 감사 라벨이다. trading_bot 의 KR intraday recheck 게이트(_rolling_kr_forward_3d_recent
→ get_recent_selection_feedback)는 코드 레벨 known_at guard 없이 이 라벨을 라이브 게이팅에
쓴다. 현재는 forward_updater 가 "date +N일 경과분만 채우는" 데이터 타이밍 덕분에 암묵적으로
안전(미경과 종목은 NULL → AVG 자동 제외)하지만, 잘못된 backfill·채움정책 변경으로 이 암묵적
안전이 깨지면 즉시 미래정보 누수가 된다.

이 sentinel 은 그 회귀를 잡는다: as_of 시점 기준 물리적으로 N거래일이 경과할 수 없는데
(date 가 너무 최근인데) 라벨이 채워진 행 = 미래값을 탐지한다. 누수 행이 하나라도 있으면
비정상 종료(exit 1)해 CI/정기 점검에서 차단한다. 입력은 로컬 sqlite 뿐, 외부 호출 없음.

near-days 는 캘린더일 보수 근사다(N거래일 ≥ N캘린더일이므로, 잡힌 행은 100% 미래값 —
false positive 없음. 주말이 끼어 더 길어지는 미경과분 일부는 보수적으로 놓칠 수 있음).

검사는 현재 DB 스냅샷(today) 기준으로만 유효하다. 라벨 채움 시점(forward_3d 가 언제
UPDATE 됐는지)이 기록되지 않으므로 과거 as_of 재현은 불가능하다 — 과거로 돌리면 그 사이
정상적으로 채워진 라벨이 전부 "미경과인데 채워짐"으로 잡혀 거짓 누수가 된다. 따라서 정기
실행으로 "지금 이 순간 미경과 종목에 라벨이 잘못 채워졌는지"를 감시하는 회귀 가드다.
"""

import argparse
import sqlite3
import pandas as pd
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEL_DB = ROOT / "data" / "ticker_selection_log.db"

# 라이브 게이팅에서 실제 소비되는 사후 라벨과 신뢰까지 필요한 최소 경과(캘린더일 보수 근사)
LABELS: dict[str, int] = {
    "forward_1d": 1,
    "forward_3d": 3,
    "forward_5d": 5,
    "max_runup_3d": 3,
    "max_drawdown_3d": 3,
    "max_runup_5d": 5,
}
# get_recent_selection_feedback 의 기본 윈도우(KR_INTRADAY_RECHECK_FORWARD_DAYS=10)
GATE_WINDOW_DAYS = 10


@dataclass
class LeakRow:
    market: str
    label: str
    near_days: int
    leak_rows: int          # date 가 as_of-near_days 이후인데 라벨 NOT NULL
    in_gate_window: int     # 그중 라이브 게이트 윈도우[as_of-9, as_of] 안에 든 행(=실제 게이팅 오염)
    sample_dates: list[str]


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def scan(conn: sqlite3.Connection, market: str, as_of: str) -> list[LeakRow]:
    as_of_d = datetime.strptime(as_of, "%Y-%m-%d").date()
    gate_lo = (as_of_d - timedelta(days=GATE_WINDOW_DAYS - 1)).strftime("%Y-%m-%d")
    out: list[LeakRow] = []
    for label, near in LABELS.items():
        # 거래일(BDay) 기준 cutoff — 캘린더일이면 주말 낀 구간에서 미경과 행을 놓쳐(false-negative)
        # 누수를 통과시킨다. BDay는 주말을 건너뛴다(공휴일은 미반영 — 보수적으로 약간 좁음).
        cutoff = (pd.Timestamp(as_of_d) - pd.offsets.BDay(near)).strftime("%Y-%m-%d")
        # 물리적 미경과인데 채워진 행: date > as_of-near(거래일) AND date <= as_of
        rows = conn.execute(
            f"SELECT date, COUNT(*) FROM ticker_selection_log "
            f"WHERE market=? AND {label} IS NOT NULL AND date > ? AND date <= ? "
            f"GROUP BY date ORDER BY date DESC",
            (market, cutoff, as_of),
        ).fetchall()
        leak = sum(int(r[1]) for r in rows)
        in_gate = sum(int(r[1]) for r in rows if str(r[0]) >= gate_lo)
        out.append(
            LeakRow(
                market=market,
                label=label,
                near_days=near,
                leak_rows=leak,
                in_gate_window=in_gate,
                sample_dates=[str(r[0]) for r in rows[:5]],
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lookahead 누수 sentinel (read-only)")
    ap.add_argument("--market", choices=["KR", "US", "both"], default="both")
    ap.add_argument("--sel-db", default=str(DEFAULT_SEL_DB))
    ap.add_argument("--strict", action="store_true",
                    help="게이트 윈도우 밖 누수도 실패로 간주(기본은 게이트 윈도우 내 누수만 실패)")
    args = ap.parse_args(argv)

    sel_db = Path(args.sel_db)
    if not sel_db.exists():
        print(f"[ERR] DB 없음: {sel_db}")
        return 2

    markets = ["KR", "US"] if args.market == "both" else [args.market]
    as_of = date.today().strftime("%Y-%m-%d")  # 현재 스냅샷 기준 전용(과거 재현은 거짓누수)
    conn = _connect_ro(sel_db)
    try:
        results: list[LeakRow] = []
        for mkt in markets:
            results.extend(scan(conn, mkt, as_of))
    finally:
        conn.close()

    print(f"=== Lookahead sentinel (snapshot={as_of}) ===")
    total_gate_leak = 0
    total_any_leak = 0
    for r in results:
        total_gate_leak += r.in_gate_window
        total_any_leak += r.leak_rows
        flag = ""
        if r.in_gate_window > 0:
            flag = "  <<< 게이트 윈도우 누수!"
        elif r.leak_rows > 0:
            flag = "  (윈도우 밖 미래값)"
        print(
            f"  [{r.market}] {r.label} (≥{r.near_days}d): "
            f"누수행={r.leak_rows} 게이트내={r.in_gate_window}{flag}"
            + (f"  최근={r.sample_dates}" if r.sample_dates else "")
        )

    if total_gate_leak > 0:
        print(f"\n[FAIL] 라이브 게이트 윈도우에 미래값 {total_gate_leak}행 — known_at guard 필요/채움정책 점검")
        return 1
    if args.strict and total_any_leak > 0:
        print(f"\n[FAIL strict] 윈도우 밖이지만 미래값 {total_any_leak}행 존재")
        return 1
    print("\n[OK] 라이브 게이트 윈도우 내 lookahead 누수 없음 (사후라벨이 미경과 종목에 채워지지 않음)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
