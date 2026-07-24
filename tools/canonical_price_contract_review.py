"""canonical 가격·시점 계약 관측기 — 시점혼합 규모를 세션별 집계 (①canonical 검증). read-only.

Codex P0-①(2026-07-24): 한 judge 프롬프트에 candidate.price(새로움)와 features.current_price
(feature 시점)가 섞여, 오래된 VWAP/OR과 새 현재가를 결합해 존재않은 시장상태를 판단할 수 있다.
①은 canonical_price 단일블록을 프롬프트에 명시했으나, 라이브에서 reference↔canonical conflict를
기록하는 배선이 아직 없다(낮 할일). 그전까지 여기서 audit로 시점혼합의 '대상 규모'를 offline
집계한다: candidate↔feature 가격 충돌 + feature age(anchor_at→known_at). = ①이 얼마나 많은
판단에 개입할 여지가 있는지의 forward 관측용.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"

try:
    from execution.single_symbol_judge import _parse_ts  # 동일 파서 재사용
except Exception:
    _parse_ts = None


def _age_sec(anchor: str, known: str):
    if _parse_ts is None:
        return None
    a, k = _parse_ts(anchor), _parse_ts(known)
    if a is None or k is None:
        return None
    s = (k - a).total_seconds()
    return s if s >= 0 else None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="2026-07-20")
    p.add_argument("--db", default=str(AUDIT_DB))
    args = p.parse_args()

    c = sqlite3.connect(args.db)
    c.execute("PRAGMA busy_timeout=5000")
    print(f"=== canonical 가격·시점 계약 시점혼합 규모 (since {args.since}) ===\n")
    for market in ("US", "KR"):
        rows = c.execute(
            "SELECT price, post_open_features_json FROM audit_candidate_rows "
            "WHERE market=? AND session_date>=? AND post_open_features_json LIKE '%current_price%' "
            "AND price>0",
            (market, args.since)).fetchall()
        conflicts, ages = [], []
        for price, fj in rows:
            try:
                f = json.loads(fj)
            except Exception:
                continue
            fc = f.get("current_price")
            try:
                fc = float(fc) if fc is not None else 0.0
            except (TypeError, ValueError):
                fc = 0.0
            if fc > 0 and price > 0:
                conflicts.append(abs(price / fc - 1.0) * 100.0)
            a = _age_sec(str(f.get("anchor_at") or ""), str(f.get("known_at") or ""))
            if a is not None:
                ages.append(a)
        n = len(conflicts)
        print(f"[{market}] 비교가능 후보 {n}건")
        if n:
            c05 = sum(1 for x in conflicts if x > 0.5)
            c2 = sum(1 for x in conflicts if x > 2.0)
            c025 = sum(1 for x in conflicts if x > 0.25)
            print(f"  가격충돌(candidate↔feature): >0.25% {c025}({100*c025/n:.0f}%) · "
                  f">0.5% {c05}({100*c05/n:.0f}%) · >2% {c2}({100*c2/n:.0f}%)")
            print(f"    중앙 충돌 {statistics.median(conflicts):.3f}%")
        # feature age: audit의 anchor_at==known_at(동기화된 값)이라 대부분 0 → 진짜 수집시점이
        # audit에 없다. Codex는 raw prompt의 feature_known_at으로 봤고, 우리 라이브 관측 배선
        # (①의 feature_as_of vs decision_as_of 기록, 낮 할일)이 붙어야 실제 age를 본다.
        ages = [a for a in ages if a > 0]  # 0(anchor==known)은 관측불가로 제외
        if ages:
            m = len(ages)
            a5 = sum(1 for x in ages if x > 300)
            a15 = sum(1 for x in ages if x > 900)
            print(f"  feature age(anchor→known) n={m}: 중앙 {statistics.median(ages):.0f}s · "
                  f">5분 {a5}({100*a5/m:.0f}%) · >15분 {a15}({100*a15/m:.0f}%)")
        print()
    c.close()
    print("※ ①canonical이 개입할 여지가 있는 판단 규모. 라이브 reference↔canonical conflict 기록"
          "\n  배선(낮 할일)이 붙으면 실제 개입율·판단변화까지 관측 가능.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
