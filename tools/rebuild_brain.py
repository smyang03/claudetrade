#!/usr/bin/env python3
"""
tools/rebuild_brain.py — brain.json HIT/MISS 재채점 도구

HIT/MISS 판정 규칙이 변경될 때마다 이 스크립트를 돌려
과거 로그를 새 기준으로 다시 채점하고 brain 통계를 재생성합니다.

수정 대상:
  markets.KR.analyst_performance  (total/hit/miss/rate/recent_7d/recent_30d)
  markets.KR.recent_days          (각 레코드의 *_result 필드)
  markets.KR.trained_days
  _rebuilt_at, _rebuild_rule_version  (타임스탬프 + 규칙 버전)

보존 (손대지 않음):
  strategy_performance, mode_performance, issue_patterns
  tuning_patterns, current_beliefs, debate_history
  execution_*, correction_guide

사용법:
  python tools/rebuild_brain.py           # dry-run (변경사항만 출력)
  python tools/rebuild_brain.py --apply   # 실제 적용
  python tools/rebuild_brain.py --market US --apply
"""

import argparse
import glob
import json
import math
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent

# ── 현재 규칙 버전 (postmortem.py와 항상 동일하게 유지) ───────────────────────
CURRENT_RULE_VERSION = 2

# ── HIT/MISS 판정 기준 (minority_report/postmortem.py:_code_judge_hit_miss 동일) ─
_BULL_STANCES    = {"AGGRESSIVE", "MODERATE_BULL", "MILD_BULL", "CAUTIOUS"}
_NEUTRAL_STANCES = {"NEUTRAL"}
_BEAR_STANCES    = {"MILD_BEAR", "CAUTIOUS_BEAR"}
_AVOID_STANCES   = {"DEFENSIVE", "HALT"}
_HIT_THRESHOLD   = 0.5
_FLAT_THRESHOLD  = 0.5
_FLAT_PARTIAL    = 1.5
_AVOID_MISS      = 1.0


def _judge(stance: str, chg: float) -> str:
    """새 규칙 기준 HIT/MISS/PARTIAL 판정."""
    abs_chg = abs(chg)
    if stance in _BULL_STANCES:
        if chg >= _HIT_THRESHOLD:  return "HIT"
        if chg > 0:                 return "PARTIAL"
        return "MISS"
    elif stance in _BEAR_STANCES:
        if chg <= -_HIT_THRESHOLD: return "HIT"
        if chg < 0:                 return "PARTIAL"
        return "MISS"
    elif stance in _AVOID_STANCES:
        if chg < -_HIT_THRESHOLD:  return "HIT"
        if chg < _AVOID_MISS:       return "PARTIAL"
        return "MISS"
    else:  # NEUTRAL + 기타
        if abs_chg <= _FLAT_THRESHOLD:  return "HIT"
        if abs_chg <= _FLAT_PARTIAL:    return "PARTIAL"
        return "MISS"


def _safe_float(v, default=0.0) -> float:
    try:
        f = float(v)
        return default if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return default


def load_records(market: str) -> list[dict]:
    """daily_judgment 파일에서 유효 레코드 로드.

    - market_change=0 또는 None인 레코드는 제외 (재채점 불가)
    - 같은 날짜에 live_*/paper_* 중복이 있으면 가장 최근 파일 우선
    """
    pattern = str(ROOT / "logs" / "daily_judgment" / f"*_{market}.json")
    records: dict[str, dict] = {}

    for fpath in sorted(glob.glob(pattern)):
        try:
            with open(fpath, encoding="utf-8") as fp:
                d = json.load(fp)

            j  = d.get("judgments", {})
            ar = d.get("actual_result", {})

            bull_stance    = j.get("bull",    {}).get("stance", "")
            bear_stance    = j.get("bear",    {}).get("stance", "")
            neutral_stance = j.get("neutral", {}).get("stance", "")
            market_change  = _safe_float(ar.get("market_change"), default=None)

            if not bull_stance or market_change is None or market_change == 0.0:
                continue

            date = d.get("date") or Path(fpath).stem.split("_")[0]
            date = str(date)[:10]

            records[date] = {
                "date":              date,
                "market_change":     market_change,
                "bull_stance":       bull_stance,
                "bear_stance":       bear_stance,
                "neutral_stance":    neutral_stance,
                "mode":              d.get("consensus", {}).get("mode", ""),
                "pnl_pct":           _safe_float(ar.get("pnl_pct")),
                "win":               bool(ar.get("win", False)),
                "trades":            int(ar.get("trades", 0) or 0),
                "bull_reason":       j.get("bull",    {}).get("key_reason", ""),
                "bear_reason":       j.get("bear",    {}).get("key_reason", ""),
                "neutral_reason":    j.get("neutral", {}).get("key_reason", ""),
                "key_lesson":        (d.get("postmortem") or {}).get("key_lesson",        ""),
                "issue_type":        (d.get("postmortem") or {}).get("issue_type",         ""),
                "best_trade":        (d.get("postmortem") or {}).get("best_trade",         None),
                "worst_trade":       (d.get("postmortem") or {}).get("worst_trade",        None),
                "worst_trade_reason":(d.get("postmortem") or {}).get("worst_trade_reason", ""),
            }
        except Exception as e:
            print(f"  [skip] {fpath}: {e}")

    return sorted(records.values(), key=lambda x: x["date"])


def rebuild_analyst_performance(records: list[dict]) -> dict:
    """전체 레코드로 analyst_performance 재계산."""
    result = {}
    for atype in ("bull", "bear", "neutral"):
        total = hit = 0
        for r in records:
            stance = r.get(f"{atype}_stance", "")
            if not stance:
                continue
            if _judge(stance, r["market_change"]) == "HIT":
                hit += 1
            total += 1
        miss = total - hit
        rate = round(hit / total, 3) if total else 0.0

        def _window(n: int) -> dict:
            w = records[-n:]
            wt = wh = 0
            for r in w:
                stance = r.get(f"{atype}_stance", "")
                if not stance:
                    continue
                if _judge(stance, r["market_change"]) == "HIT":
                    wh += 1
                wt += 1
            return {"total": wt, "hit": wh, "rate": round(wh / wt, 3) if wt else 0.0}

        result[atype] = {
            "total":      total,
            "hit":        hit,
            "miss":       miss,
            "rate":       rate,
            "recent_7d":  _window(7),
            "recent_30d": _window(30),
            "trend":      "stable",
        }
    return result


def rebuild_recent_days(records: list[dict], n: int = 30) -> list[dict]:
    """최근 N 레코드로 recent_days 재생성 (HIT/MISS 수정)."""
    result = []
    for r in records[-n:]:
        result.append({
            "date":              r["date"],
            "mode":              r["mode"],
            "pnl_pct":           r["pnl_pct"],
            "market_change":     r["market_change"],
            "win":               r["win"],
            "bull_result":       _judge(r["bull_stance"],    r["market_change"]),
            "bear_result":       _judge(r["bear_stance"],    r["market_change"]),
            "neutral_result":    _judge(r["neutral_stance"], r["market_change"]),
            "bull_stance":       r["bull_stance"],
            "bear_stance":       r["bear_stance"],
            "neutral_stance":    r["neutral_stance"],
            "bull_reason":       r["bull_reason"],
            "bear_reason":       r["bear_reason"],
            "neutral_reason":    r["neutral_reason"],
            "key_lesson":        r["key_lesson"],
            "issue_type":        r["issue_type"],
            "best_trade":        r["best_trade"],
            "worst_trade":       r["worst_trade"],
            "worst_trade_reason":r["worst_trade_reason"],
            "trades":            r["trades"],
        })
    return result


def print_diff(market: str, old_brain: dict,
               new_perf: dict, new_days: list[dict]) -> int:
    """변경 내용 출력. 변경 건수 반환."""
    total_changes = 0
    kr = old_brain.get("markets", {}).get(market, {})
    old_perf = kr.get("analyst_performance", {})
    old_days = kr.get("recent_days", [])

    print(f"\n{'='*60}")
    print(f"[{market}] analyst_performance 변경")
    print(f"{'='*60}")
    for atype in ("bull", "bear", "neutral"):
        o = old_perf.get(atype, {})
        n = new_perf[atype]
        hit_diff  = n["hit"]  - o.get("hit",  0)
        miss_diff = n["miss"] - o.get("miss", 0)
        rate_diff = n["rate"] - o.get("rate", 0.0)
        changed   = hit_diff != 0 or miss_diff != 0
        mark = " ← 변경" if changed else ""
        print(
            f"  {atype:8s}: "
            f"HIT {o.get('hit','?'):>3}/{o.get('total','?'):>3} ({o.get('rate',0):.1%})"
            f" → {n['hit']:>3}/{n['total']:>3} ({n['rate']:.1%})"
            f"  (Δhit={hit_diff:+d}){mark}"
        )
        if changed:
            total_changes += 1

    print(f"\n[{market}] recent_days HIT/MISS 변경")
    print(f"{'='*60}")
    old_map = {d["date"]: d for d in old_days}
    day_changes = 0
    for nd in new_days:
        od = old_map.get(nd["date"])
        for key in ("bull_result", "bear_result", "neutral_result"):
            old_v = od.get(key, "?") if od else "NEW"
            new_v = nd[key]
            if old_v != new_v:
                atype = key.replace("_result", "")
                print(
                    f"  {nd['date']} {atype:8s}: "
                    f"{nd[f'{atype}_stance']:15s} "
                    f"chg={nd['market_change']:+.2f}%  "
                    f"{old_v} → {new_v}"
                )
                day_changes += 1
    if day_changes == 0:
        print("  (변경 없음)")
    total_changes += day_changes
    return total_changes


def main():
    parser = argparse.ArgumentParser(description="brain.json HIT/MISS 재채점")
    parser.add_argument("--market",  default="KR", choices=["KR", "US"],
                        help="재채점할 시장 (기본: KR)")
    parser.add_argument("--apply",   action="store_true",
                        help="실제 brain.json 업데이트 (없으면 dry-run)")
    parser.add_argument("--recent",  type=int, default=30,
                        help="recent_days 보존 개수 (기본: 30)")
    args = parser.parse_args()

    brain_path = ROOT / "state" / "brain.json"
    if not brain_path.exists():
        print(f"[ERROR] brain.json 없음: {brain_path}")
        return

    print(f"[mode] {'APPLY' if args.apply else 'DRY-RUN'} | market={args.market}")

    # 1. 로그 로드
    records = load_records(args.market)
    print(f"[records] {args.market} 유효 레코드: {len(records)}개")
    if not records:
        print("[ERROR] 재계산할 레코드 없음 — 중단")
        return

    # 2. 재계산
    new_perf = rebuild_analyst_performance(records)
    new_days = rebuild_recent_days(records, n=args.recent)

    # 3. 현재 brain 로드
    with open(brain_path, encoding="utf-8") as f:
        brain = json.load(f)

    # 4. diff 출력
    total_changes = print_diff(args.market, brain, new_perf, new_days)
    print(f"\n총 변경: {total_changes}건")

    if not args.apply:
        print("\n[dry-run] 실제 적용하려면 --apply 옵션을 추가하세요.")
        return

    if total_changes == 0:
        print("[skip] 변경 없음 — brain.json 유지")
        return

    # 5. 백업
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = ROOT / "state" / f"brain_backup_{ts}.json"
    shutil.copy2(brain_path, backup_path)
    print(f"\n[backup] {backup_path.name}")

    # 6. 적용 (보존 대상 외 필드만 수정)
    mkt = brain.setdefault("markets", {}).setdefault(args.market, {})
    mkt["analyst_performance"] = new_perf
    mkt["recent_days"]         = new_days
    mkt["trained_days"]        = len(records)
    brain["_rebuilt_at"]            = datetime.now().isoformat(timespec="seconds")
    brain["_rebuild_rule_version"]  = CURRENT_RULE_VERSION

    with open(brain_path, "w", encoding="utf-8") as f:
        json.dump(brain, f, ensure_ascii=False, indent=2)

    print(f"[done] {brain_path.name} 업데이트 완료")
    print(f"  trained_days={len(records)}, rule_version={CURRENT_RULE_VERSION}")
    print(f"  _rebuilt_at={brain['_rebuilt_at']}")


if __name__ == "__main__":
    main()
