#!/usr/bin/env python3
"""judge 예산 사용 관측기 — 공급/호출/플랜 3숫자 원장 (read-only, 라이브 미개입).

왜 필요한가 (실측 2026-07-27)
  지금은 "매수 0건"이라는 결과만 보이고, 그 원인이 셋 중 어느 것인지 구분되지 않는다:
    ① 데이터 파이프라인 장애 (피처가 안 만들어짐)
    ② 큐/게이트 문제 (자격 있는 종목을 안 부름)
    ③ 후보 질 문제 (불렀는데 judge가 기권)
  이 셋을 가르려면 세션마다 공급·호출·플랜 세 숫자가 필요하다.

  7/27 KR 실측: judge 30회 중 16회가 history 필터로 제외된 종목(피처 스냅샷 0건),
  8회가 피처 결손 종목이었다. 판정 가능한 종목 30개 중 7개만 불렀다.

무엇을 재는가
  공급  해당 세션에 vwap+OR을 갖춘(=judge가 판단 가능한) 종목 수, tier별
  호출  실제 judge 호출 수와 그 구성(자격O/X · 첫판정/재호출 · tier)
  플랜  PULLBACK_WAIT/BUY_READY 산출 수

  라이브 동작은 바꾸지 않는다. 게이트를 넣기 전 **기준선**을 만드는 것이 목적이며,
  기준선 없이 게이트를 넣으면 효과를 측정할 수 없다.

tier 값이 시장별로 다른 이유 (실측)
  KR  자격O 첫판정  ≤90분 37.1% / 90~180분 7.1% / 180분+ 0.0%(n=19)
  US  자격O 첫판정  0~30분 5.3% / 30~180분 44.5% / 180분+ 23.8%
  KR은 90분에 급락하고 US는 180분까지 유지된다. 하나의 상수로 묶으면 한쪽이 손해다.

출력
  data/shadow/judge_budget_<MARKET>.jsonl   (append, 세션당 1행, 멱등)
  state/judge_budget_observer_heartbeat.json

사용
  python tools/judge_budget_observer.py --market KR --session 2026-07-27
  python tools/judge_budget_observer.py --since-days 10
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SHADOW_DIR = ROOT / "data" / "shadow"
STATE_DIR = ROOT / "state"
HEARTBEAT_PATH = STATE_DIR / "judge_budget_observer_heartbeat.json"
SCHEMA_VERSION = 1

# market_open_elapsed_min 도입일. 그 이전은 시간축 분류가 불가능하다.
ELAPSED_FIELD_MIN_DATE = "2026-07-13"

# 시장별 tier 경계 (분). 위 docstring의 실측 플랜율에 따른다.
TIER_RULES = {
    # (첫판정 여부, elapsed 구간) -> tier 이름
    "KR": {"t1": (0, 90), "t3": (90, 180), "drop_first_after": 180},
    "US": {"t1": (30, 180), "t3": (180, 400), "drop_first_after": None},
}
US_OPEN_KST_MIN = 22 * 60 + 30   # US 개장 22:30 KST
KR_OPEN_KST_MIN = 9 * 60         # KR 개장 09:00 KST


def _parse(ts: str):
    try:
        return datetime.fromisoformat(str(ts)[:19])
    except Exception:
        return None


def session_date_of(ts: str, market: str) -> str:
    """호출 시각 → 세션 날짜. US는 KST 자정을 넘기므로 역산이 필요하다.

    ⚠️ 이 매핑을 틀리면 자격 판정이 통째로 깨진다. 처음에 파일 날짜를 그대로 쓰다
    US 자격 분류율이 14%로 나왔고(KR 56%), 세션 역산을 넣고서야 32%로 정상화됐다.
    """
    t = _parse(ts)
    if t is None:
        return ""
    if market == "US":
        d = t.date() if t.hour >= 22 else (t.date() - timedelta(days=1))
        return d.isoformat()
    return t.date().isoformat()


def elapsed_of(ts: str, market: str) -> float | None:
    t = _parse(ts)
    if t is None:
        return None
    minutes = t.hour * 60 + t.minute + t.second / 60.0
    base = US_OPEN_KST_MIN if market == "US" else KR_OPEN_KST_MIN
    diff = minutes - base
    if market == "US" and diff < -200:
        diff += 1440
    return diff


def load_feature_timing(market: str) -> dict[str, dict]:
    """session_date -> 피처 도착 타이밍·품질 분포.

    왜 필요한가: 자격 게이트(어느 호출을 하느냐)와 피처 지연 개선(자격이 언제
    생기나)은 서로 다른 변화인데, 공급 '개수'만 기록하면 후자의 효과가 원장에
    남지 않아 두 변화를 분리 측정할 수 없다.

    vwap과 OR을 분리해 기록한다 — 실측상 지연 양상이 시장별로 반대다:
      KR  OR 최초 중앙 27.2분 (구조적 하한 10분) / VWAP 중앙 6.2분
      US  OR 최초 중앙 10.0분 (하한 15분) / VWAP 중앙 29.7분, p75 105.9분
    """
    first_vwap: dict[str, dict[str, float]] = defaultdict(dict)
    first_or: dict[str, dict[str, float]] = defaultdict(dict)
    quality: dict[str, Counter] = defaultdict(Counter)
    for path in glob.glob(str(ROOT / f"logs/funnel/post_open_features_*_{market}.jsonl")):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                sess = row.get("market_session_date") or row.get("session_date")
                elapsed = row.get("market_open_elapsed_min")
                ticker = str(row.get("ticker") or "")
                if not sess or elapsed is None or not ticker:
                    continue
                try:
                    value = float(elapsed)
                except (TypeError, ValueError):
                    continue
                sess = str(sess)
                quality[sess][str(row.get("data_quality") or "unknown")] += 1
                if row.get("vwap") is not None:
                    cur = first_vwap[sess].get(ticker)
                    if cur is None or value < cur:
                        first_vwap[sess][ticker] = value
                if row.get("opening_range_high") is not None:
                    cur = first_or[sess].get(ticker)
                    if cur is None or value < cur:
                        first_or[sess][ticker] = value

    def _q(values: list[float], pct: float) -> float | None:
        if not values:
            return None
        values = sorted(values)
        idx = min(len(values) - 1, int(len(values) * pct))
        return round(values[idx], 1)

    out: dict[str, dict] = {}
    for sess in set(first_vwap) | set(first_or) | set(quality):
        vw = list(first_vwap.get(sess, {}).values())
        orv = list(first_or.get(sess, {}).values())
        out[sess] = {
            "first_vwap_min": min(vw) if vw else None,
            "median_vwap_min": _q(vw, 0.5),
            "p75_vwap_min": _q(vw, 0.75),
            "first_or_min": min(orv) if orv else None,
            "median_or_min": _q(orv, 0.5),
            "p75_or_min": _q(orv, 0.75),
            "data_quality": dict(quality.get(sess, Counter())),
        }
    return out


def load_eligibility(market: str) -> dict[str, dict[str, float]]:
    """session_date -> {ticker: 최초 자격(vwap+OR 동시 보유) elapsed}"""
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for path in glob.glob(str(ROOT / f"logs/funnel/post_open_features_*_{market}.jsonl")):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                elapsed = row.get("market_open_elapsed_min")
                sess = row.get("market_session_date") or row.get("session_date")
                if elapsed is None or not sess:
                    continue
                if row.get("vwap") is None or row.get("opening_range_high") is None:
                    continue
                ticker = str(row.get("ticker") or "")
                if not ticker:
                    continue
                try:
                    value = float(elapsed)
                except (TypeError, ValueError):
                    continue
                bucket = out[str(sess)]
                if ticker not in bucket or value < bucket[ticker]:
                    bucket[ticker] = value
    return out


def load_calls(market: str) -> dict[str, list[dict]]:
    """session_date -> judge 호출 목록 (시각 오름차순)"""
    out: dict[str, list[dict]] = defaultdict(list)
    for path in glob.glob(str(ROOT / "logs/raw_calls/*.json")):
        name = Path(path).name
        if not name.startswith("2026"):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except Exception:
            continue
        if obj.get("label") != "single_symbol_judge" or obj.get("market") != market:
            continue
        ts = obj.get("timestamp") or ""
        sess = session_date_of(ts, market)
        elapsed = elapsed_of(ts, market)
        if not sess or elapsed is None or not (0 <= elapsed <= 400):
            continue
        parsed = obj.get("parsed") or {}
        action = str(parsed.get("action") or parsed.get("final_action") or "")
        out[sess].append({
            "ticker": str(parsed.get("ticker") or ""),
            "elapsed": elapsed,
            "action": action,
            "plan": action in ("PULLBACK_WAIT", "BUY_READY"),
            "model": obj.get("model"),
        })
    for sess in out:
        out[sess].sort(key=lambda c: c["elapsed"])
    return out


def tier_of(market: str, first_time: bool, elapsed: float) -> str:
    rule = TIER_RULES[market]
    lo, hi = rule["t1"]
    if first_time and lo <= elapsed < hi:
        return "T1"
    if not first_time and lo <= elapsed < hi:
        return "T2" if market == "KR" else "T3"
    t3lo, t3hi = rule["t3"]
    if first_time and t3lo <= elapsed < t3hi:
        return "T3" if market == "KR" else "T2"
    return "DROP"


def observe_session(market: str, sess: str, elig: dict, calls: list[dict],
                    timing: dict | None = None) -> dict:
    supply = elig.get(sess) or {}
    rule = TIER_RULES[market]
    lo, hi = rule["t1"]
    t3lo, t3hi = rule["t3"]
    # 공급 = "그 구간 동안 판정 가능한 종목 수"다. 최초 자격 시각이 구간 안인 것만 세면
    # 안 된다 — +10분에 자격을 얻은 종목은 +50분에도 판정 가능하기 때문이다.
    # (처음 이렇게 셌다가 US T1 공급이 3개로 나왔다. 실제로는 34개가 30분 이전에
    #  자격을 얻어 T1 창 내내 판정 가능한 상태였다.)
    supply_t1 = sum(1 for v in supply.values() if v < hi)
    supply_t3 = sum(1 for v in supply.values() if v < t3hi)

    seen: set[str] = set()
    comp = defaultdict(int)
    plans_by = defaultdict(int)
    for c in calls:
        first = c["ticker"] not in seen
        seen.add(c["ticker"])
        ready = c["ticker"] in supply and supply[c["ticker"]] <= c["elapsed"]
        tier = tier_of(market, first, c["elapsed"]) if ready else "NO_FEATURE"
        comp[tier] += 1
        if c["plan"]:
            plans_by[tier] += 1
    elig_vals = sorted(supply.values())
    def _q(pct: float):
        if not elig_vals:
            return None
        return round(elig_vals[min(len(elig_vals) - 1, int(len(elig_vals) * pct))], 1)

    return {
        "schema_version": SCHEMA_VERSION,
        "key": f"{sess}|{market}",
        "session_date": sess,
        "market": market,
        "mode": "observe",
        "live_impact": "none",
        "supply": {
            "eligible_total": len(supply), "T1": supply_t1, "T3": supply_t3,
            "first_eligible_min": round(elig_vals[0], 1) if elig_vals else None,
            "median_eligible_min": _q(0.5),
            "p75_eligible_min": _q(0.75),
            **(timing or {}),
        },
        "calls": {"total": len(calls), "by_tier": dict(comp)},
        "plans": {"total": sum(1 for c in calls if c["plan"]), "by_tier": dict(plans_by)},
        "tier_rule": {"t1_min": lo, "t1_max": hi, "t3_min": t3lo, "t3_max": t3hi},
    }


def fmt_line(rec: dict) -> str:
    s, c, p = rec["supply"], rec["calls"], rec["plans"]
    comp = " ".join(f"{k}={v}" for k, v in sorted(c["by_tier"].items()))
    return (f"[judge 예산] {rec['market']} {rec['session_date']}  "
            f"공급 자격={s['eligible_total']} T1={s['T1']} T3={s['T3']} / "
            f"호출 {c['total']} ({comp or '없음'}) / 플랜 {p['total']}")


def existing_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("{"):
                try:
                    keys.add(str(json.loads(line).get("key")))
                except Exception:
                    continue
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(description="judge 예산 관측기 (read-only)")
    ap.add_argument("--market", default="", help="KR|US (빈값=둘 다)")
    ap.add_argument("--session", default="", help="특정 세션 (YYYY-MM-DD)")
    ap.add_argument("--since-days", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    markets = [args.market.upper()] if args.market else ["KR", "US"]
    floor = max(
        ELAPSED_FIELD_MIN_DATE,
        (date.today() - timedelta(days=args.since_days)).isoformat(),
    )
    all_recs: list[dict] = []
    for market in markets:
        if market not in TIER_RULES:
            print(f"  지원하지 않는 시장: {market}")
            continue
        elig = load_eligibility(market)
        calls = load_calls(market)
        timing = load_feature_timing(market)
        sessions = sorted(set(elig) | set(calls))
        for sess in sessions:
            if args.session and sess != args.session:
                continue
            if not args.session and sess < floor:
                continue
            rec = observe_session(market, sess, elig, calls.get(sess) or [], timing.get(sess))
            all_recs.append(rec)
            print("  " + fmt_line(rec))

    if not all_recs:
        print("관측 대상 세션 없음")
        return 1
    if args.dry_run:
        print("\n[dry-run] 기록하지 않음")
        return 0

    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for market in sorted({r["market"] for r in all_recs}):
        path = SHADOW_DIR / f"judge_budget_{market}.jsonl"
        have = existing_keys(path)
        fresh = [r for r in all_recs if r["market"] == market and r["key"] not in have]
        if fresh:
            with open(path, "a", encoding="utf-8") as fh:
                for rec in fresh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += len(fresh)
        print(f"  {path.name}: +{len(fresh)}건 (누적 {len(have) + len(fresh)}건)")

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(json.dumps({
            "process": "judge_budget_observer",
            "ran_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sessions_observed": len(all_recs),
            "records_written": written,
            "mode": "observe",
            "live_impact": "none",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"  [경고] 하트비트 기록 실패: {exc}")
    print(f"\n총 {written}건 기록. 라이브 동작에는 개입하지 않았다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
