"""KR 프롬프트 재정렬 가치 shadow — 급등률 랭킹 vs 중간모멘텀 우선 재정렬 반사실 비교.

근거(2026-07-21 스크리너→선정 분석): KR 프롬프트 포함(WATCH) forward −0.691% vs
cap에 잘린 후보 +0.303%; change_rate 3~7% 밴드만 양수(+0.19), 7%+는 음수(−0.77/−1.05).
현재 랭킹(급등률 기반)이 나쁜 코호트를 상위로 올린다.

이 도구는 **오프라인 반사실**: screener_quality 원장(실제 프롬프트 포함 여부 기록)과
candidate_audit outcome(30/60분 forward)을 조인해, "같은 인원수로 중간모멘텀 우선
재정렬했다면 프롬프트 코호트 forward가 얼마나 달라졌나"를 세션별로 계산한다.

- 런타임 무접촉 (runtime/candidate_prompt_pool.py의 별도 reorder shadow 작업과 독립).
- ⚠️enforce 금지: 6/27 "스크리너 리랭킹 backfire 기각" 이력 — 반사실이 세션 누적으로
  일관 양수 + 운영자 승인 후에만 순서 변경을 논의한다.

사용: python tools/kr_prompt_reorder_value_shadow.py [--start 2026-07-07] [--market KR]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUALITY_DIR = ROOT / "logs" / "screener_quality"
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"


def value_score(row: dict) -> float:
    """중간모멘텀 우선 점수(높을수록 우선). 실증 밴드: 3~7% 최상, 0~3 차선, 7~15 감점, 15+ 큰 감점.

    극단 급등은 anti-chase(MAX>=25 enforce)가 이미 배제 — 여기서는 잔여 풀 내 순서만 본다.
    """
    try:
        chg = float(row.get("change_rate") or 0.0)
    except (TypeError, ValueError):
        chg = 0.0
    if 3.0 <= chg <= 7.0:
        base = 100.0 - abs(chg - 5.0)          # 밴드 중심 5%에 가까울수록 우선
    elif 0.0 <= chg < 3.0:
        base = 60.0 + chg * 5.0                # 0~3%: 차선(상승 초입)
    elif 7.0 < chg <= 15.0:
        base = 40.0 - (chg - 7.0) * 3.0        # 과열 감점
    elif chg < 0.0:
        base = 30.0 + max(-10.0, chg)          # 하락은 후순위(낙폭베팅 회피와 정합)
    else:
        base = 5.0                             # 15%+ 최후순위
    return base


def load_sessions(start: str, market: str) -> dict[str, list[dict]]:
    sessions: dict[str, list[dict]] = defaultdict(list)
    seen: dict[str, set] = defaultdict(set)
    for f in sorted(glob.glob(str(QUALITY_DIR / f"202*_{market}_candidates.jsonl"))):
        day = os.path.basename(f)[:8]
        sd = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        if sd < start:
            continue
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = str(d.get("ticker") or "")
            if not t or t in seen[sd]:
                continue
            seen[sd].add(t)
            sessions[sd].append(d)
    return sessions


def load_outcomes(start: str, market: str, horizon: int) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    con = sqlite3.connect(f"file:{AUDIT_DB}?mode=ro", uri=True)
    con.execute("pragma busy_timeout=5000")
    q = """SELECT r.session_date, r.ticker, o.return_pct
    FROM audit_candidate_rows r JOIN audit_candidate_outcomes o ON o.candidate_key=r.candidate_key
    WHERE r.market=? AND r.session_date>=? AND o.horizon_min=? AND o.return_pct IS NOT NULL"""
    for sd, t, p in con.execute(q, (market, start, horizon)):
        key = (str(sd), str(t).upper() if market == "US" else str(t))
        out[key] = float(p)
    con.close()
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-07-07")
    ap.add_argument("--market", default="KR", choices=["KR", "US"])
    ap.add_argument("--horizon", type=int, default=60)
    # net-proxy: forward에서 왕복비용 차감(2026-07-21 검증). forward≠net이므로 비용
    # 차감으로 net 근사. KR 왕복 ~0.35%(수수료·세금·슬리피지 보수적), US ~0.20%.
    ap.add_argument("--net-cost", type=float, default=None,
                    help="왕복비용%%를 forward에서 차감해 net-proxy로 비교(기본: KR 0.35·US 0.20)")
    args = ap.parse_args()

    cost = args.net_cost
    if cost is None:
        cost = 0.35 if args.market == "KR" else 0.20

    sessions = load_sessions(args.start, args.market)
    outcomes = load_outcomes(args.start, args.market, args.horizon)
    # net-proxy 적용(모든 forward에서 왕복비용 차감 — 상대비교라 both에 동일 적용)
    outcomes = {k: v - cost for k, v in outcomes.items()}
    if not sessions:
        print("세션 데이터 없음")
        return 0

    agg_actual, agg_value = [], []
    print(f"{args.market} 세션별 프롬프트 코호트 net-proxy(=forward{args.horizon}-{cost}%) — 실제 vs 중간모멘텀 재정렬")
    for sd, rows in sorted(sessions.items()):
        actual = [r for r in rows if r.get("input_to_claude")]
        n = len(actual)
        if n == 0:
            continue
        # 반사실: 같은 풀에서 같은 인원수를 value_score 순으로
        ranked = sorted(rows, key=value_score, reverse=True)[:n]
        def _rets(cands):
            vals = []
            for r in cands:
                t = str(r.get("ticker") or "")
                key = (sd, t.upper() if args.market == "US" else t)
                if key in outcomes:
                    vals.append(outcomes[key])
            return vals
        a, v = _rets(actual), _rets(ranked)
        ma, mv = _mean(a), _mean(v)
        if ma is None or mv is None:
            continue
        agg_actual.extend(a)
        agg_value.extend(v)
        print(f"  {sd}: 실제 n={len(a):>3} {ma:+.3f}% | 재정렬 n={len(v):>3} {mv:+.3f}% | Δ={mv-ma:+.3f}%p")

    if agg_actual and agg_value:
        ma, mv = _mean(agg_actual), _mean(agg_value)
        print(f"\n누적: 실제 {ma:+.3f}% (n={len(agg_actual)}) vs 재정렬 {mv:+.3f}% (n={len(agg_value)}) | Δ={mv-ma:+.3f}%p")
        print("판정 규율: Δ가 세션 누적 일관 양수여도 enforce는 운영자 승인 필수(6/27 리랭킹 기각 이력).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
