#!/usr/bin/env python3
"""픽 규칙 shadow 원장 — 세션당 5규칙의 픽을 결정 시점 값으로 박제 (2026-09-01).

배경: 모델 제거(09-01)로 라이브 픽 순서는 dvol_desc(잠정)가 됐다. 픽 시뮬
(research_pick_simulation)에서 5규칙 전부 "두 표본 널 95% 동시 초과" 미달 —
통계적으로 구별 불가였다. 그래서 어느 순서가 라이브로 가든 **나머지 규칙들의
forward 증거**를 병행 축적해야 한다. 이것이 "매수한 것만으로 판정"(검증 코호트 =
매수 코호트)을 forward에서 성립시키는 배선이다.

candidate_pool_all은 ON CONFLICT 덮어쓰기라 사후 재산출이 그날의 결정 시점 값과
다를 수 있다 — 여기서 픽 시점 값을 JSONL로 얼린다.

**규칙은 사전등록분 5종 고정**(research_pick_simulation.RULES). 선별 상수(밴드
100~500M, MAX>=8)도 사전등록 고정 — env 드리프트가 연구 원장을 오염시키지 않게
한다. 라이브 계약 검증은 integrity_check의 몫이고 여기는 연구 관측 전용이다.

**모집단 2종 분리 기록** (2026-09-01 Codex 리뷰 P1-5 반영): `picks`(wide =
eligible 전체, 공급 확대 가정)와 `picks_live`(in_pool=1 = 스크리너 quota 통과,
실제 주문 도달 가능)를 나란히 남긴다. 실측(09-01): 스크리너 quota가 day_losers를
세션당 10개로 자르므로 wide와 live는 다른 모집단이다 — 이걸 섞으면 "30건 모아도
주문 가능 후보의 forward가 아니다".

정산은 기록하지 않는다 — 픽이 고정되면 정산은 가격 CSV에서 결정론으로 재산출
가능하다(research_pick_simulation 참조). 판정: 새 계약 정산 30건 시점.

멱등: session_date당 1행. 라이브 미개입(read-only + JSONL append).
사용: python tools/observe_pick_rules.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from research_early_exit_no_bump import bars  # noqa: E402
from research_pick_simulation import (  # noqa: E402
    BAND_HI, BAND_LO, MAX_FLOOR, RULES, _key, max21_at,
)

POOL_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
LEDGER = ROOT / "data" / "shadow" / "us_swing_pick_rules_shadow.jsonl"


def signal_day_features(ticker: str, session_date: str) -> dict | None:
    """신호일(= session_date 직전 거래일) 봉 기준 특징. 진입 세션 봉을 요구하지
    않는다 — 러너(22:20) 직후, 그날 장이 끝나기 전에도 기록할 수 있어야 한다."""
    b = bars(ticker)
    si = None
    for i in range(len(b) - 1, -1, -1):
        if b[i][0] < str(session_date):
            si = i
            break
    if si is None or si < 1:
        return None
    _d, _o, hi, lo, c, v = b[si]
    return {
        "ticker": ticker,
        "signal_date": b[si][0],
        "ibs": (c - lo) / (hi - lo) * 100.0 if hi > lo else None,
        "chg": 100.0 * (c / b[si - 1][4] - 1.0) if b[si - 1][4] else None,
        "dvol": c * v / 1e6 if v else None,
        "max21": max21_at(b, si),
    }


def recorded_sessions() -> set[str]:
    done: set[str] = set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                done.add(str(json.loads(line).get("session_date")))
            except ValueError:
                continue
    return done


def main() -> int:
    con = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True, timeout=10)
    try:
        rows = con.execute(
            """SELECT session_date, ticker, chg_pct, dollar_vol, in_pool
               FROM candidate_pool_all WHERE eligible=1 ORDER BY session_date"""
        ).fetchall()
    finally:
        con.close()

    by_session: dict[str, list] = defaultdict(list)
    for sd, tk, chg, dvol, in_pool in rows:
        by_session[str(sd)].append((str(tk).upper(), chg, dvol, int(in_pool or 0)))

    done = recorded_sessions()
    todo = [sd for sd in sorted(by_session) if sd not in done]
    if not todo:
        print("[pick_rules] 신규 세션 없음")
        return 0

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with LEDGER.open("a", encoding="utf-8") as fh:
        for sd in todo:
            cands = []
            for tk, chg, dvol, in_pool in by_session[sd]:
                f = signal_day_features(tk, sd)
                if f is None:
                    continue
                if chg is not None:
                    f["chg"] = float(chg)
                if dvol is not None:
                    f["dvol"] = float(dvol) / 1e6
                f["in_pool"] = in_pool
                cands.append(f)

            def band_max_pass(cs):
                return [c for c in cs
                        if c["dvol"] is not None and BAND_LO <= c["dvol"] < BAND_HI
                        and (c["max21"] is None or c["max21"] >= MAX_FLOOR)]

            # 두 모집단을 분리 기록한다 (2026-09-01 Codex 리뷰 P1-5):
            #   live  = in_pool=1 (스크리너 quota 컷 통과 = 실제 주문 도달 가능)
            #   wide  = eligible=1 전체 (공급 확대 가정의 연구 모집단)
            # 이전에는 wide만 기록해 "30건 모아도 주문 가능 후보의 forward가 아니다"였다.
            passers_wide = band_max_pass(cands)
            passers_live = band_max_pass([c for c in cands if c["in_pool"]])

            def rule_picks(ps):
                out = {}
                for rule in RULES:
                    if ps:
                        p = sorted(ps, key=lambda c: _key(rule, c))[0]
                        out[rule] = {k: (round(v, 4) if isinstance(v, float) else v)
                                     for k, v in p.items()}
                return out

            fh.write(json.dumps({
                "session_date": sd,
                "n_eligible": len(by_session[sd]),
                "n_featured": len(cands),
                "n_passers": len(passers_wide),
                "n_passers_live": len(passers_live),
                "picks": rule_picks(passers_wide),
                "picks_live": rule_picks(passers_live),
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, ensure_ascii=False) + "\n")
            written += 1
            print(f"[pick_rules] {sd} 통과 wide {len(passers_wide)} / live {len(passers_live)} 기록")
    print(f"[pick_rules] {written}세션 기록 완료 → {LEDGER.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
