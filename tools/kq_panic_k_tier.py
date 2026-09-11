# -*- coding: utf-8 -*-
"""패닉 ETF 신호일에 급락 후보 수(k) 등급을 붙이는 주석 원장 (2026-09-12, 관측 전용).

09-12 사전등록(`docs/reports/preregistration_core_etf_and_kq_panic_20260909.md` §09-12 개정 D)이
**k 3등급(0 / 1~19 / 20+)을 forward 30건에서 판정**하기로 했다. 그런데 k가 어디에도
기록되지 않아 **판정 자체가 불가능**했다. 이 도구가 그 구멍을 메운다.

설계 — 라이브 경로를 건드리지 않는다:
- `tools/kr_index_etf_panic_shadow.py`(15:16 schtask)는 **읽기만** 한다. 그 모듈을 고치면
  매일 도는 신호 수집이 위험해진다.
- **판정용 k는 장 마감 후 산출로 충분하다.** 15:19에 k가 필요한 것은 k로 *주문 크기를 바꿀 때*
  뿐이고, 그건 채택된 뒤의 배선 과제다. 지금 필요한 건 기록이다.
- 별도 원장 `data/shadow/kq_panic_k_tier.jsonl`에 (session_date, k, tier)를 남긴다. 멱등.

⚠️ **가장 중요한 함정 — 캐시 결측을 k=0으로 세면 안 된다.**
`k=0`(그날 급락 후보가 진짜 없음)은 의미 있는 등급인데, 가격 캐시에 그 날짜가 없어도
`build()`는 후보를 못 만들어 똑같이 0이 된다. 그래서 **그 날짜에 바를 가진 종목 수**를
먼저 세고 `MIN_COVERAGE` 미만이면 **기록하지 않는다**(다음 실행에서 다시 시도).

사용:
  python tools/kq_panic_k_tier.py            # 미주석 신호일 채우기
  python tools/kq_panic_k_tier.py report     # 등급별 현황(백필/forward 분리)
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "analysis" / "kr_fallen_price_cache.json"
OUT = ROOT / "data" / "shadow" / "kq_panic_k_tier.jsonl"
MIN_COVERAGE = 300          # 그 날짜에 바를 가진 종목 수 하한(라이브 유니버스 811 기준)
TIERS = ((0, 0, "t0"), (1, 19, "t1_19"), (20, 10 ** 9, "t20p"))


def tier_of(k):
    for lo, hi, name in TIERS:
        if lo <= k <= hi:
            return name
    return "unknown"


def _signal_dates():
    """패닉 원장의 신호일(거래 행 기준). 라이브 모듈은 읽기만 한다."""
    import tools.kr_index_etf_panic_shadow as P
    out = {}
    for r in P._load():
        if r.get("kind") != "trade":
            continue
        d = r.get("session_date")
        if d:
            out.setdefault(d, {"mode": r.get("mode"), "strength": r.get("strength")})
    return out


def _existing():
    if not OUT.exists():
        return {}
    got = {}
    for line in OUT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("session_date"):
            got[row["session_date"]] = row
    return got


def annotate():
    from tools.research_kr_rank_pool_reconcile import build, by_session

    if not CACHE.exists():
        print(f"[KTIER] 라이브 캐시 없음: {CACHE}", flush=True)
        return 1
    cache = json.loads(CACHE.read_text(encoding="utf-8"))

    coverage = defaultdict(int)
    for bars in cache.values():
        for b in bars:
            coverage[b["d"]] += 1

    kcount = {d: len(v) for d, v in by_session(build(cache)).items()}
    sigs, got = _signal_dates(), _existing()
    todo = sorted(d for d in sigs if d not in got)
    if not todo:
        print(f"[KTIER] 새로 주석할 신호일 없음 (기록 {len(got)} / 신호 {len(sigs)})", flush=True)
        return 0

    written = skipped = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with OUT.open("a", encoding="utf-8") as fh:
        for d in todo:
            cov = coverage.get(d, 0)
            if cov < MIN_COVERAGE:
                skipped += 1          # 캐시 결측 — k=0으로 세지 않는다. 다음 실행에서 재시도.
                continue
            k = kcount.get(d, 0)
            fh.write(json.dumps({"session_date": d, "k": k, "tier": tier_of(k),
                                 "coverage": cov, "mode": sigs[d]["mode"],
                                 "strength": sigs[d]["strength"], "annotated_at": now},
                                ensure_ascii=False) + "\n")
            written += 1
    print(f"[KTIER] 주석 {written}건 · 캐시 결측으로 보류 {skipped}건 "
          f"(누적 {len(got) + written} / 신호 {len(sigs)})", flush=True)
    return 0


def report():
    import tools.kr_index_etf_panic_shadow as P

    got = _existing()
    if not got:
        print("[KTIER] 원장 비어 있음. 먼저 주석을 돌린다.", flush=True)
        return 0
    nets = defaultdict(lambda: defaultdict(list))      # mode -> tier -> [net]
    for r in P._load():
        if (r.get("kind") != "trade" or r["ticker"] != "233740"
                or r.get("status") != "CLOSED" or r.get("net_pct") is None):
            continue
        row = got.get(r["session_date"])
        if row:
            nets[r.get("mode") or "?"][row["tier"]].append(r["net_pct"])

    print(f"[KTIER] 주석된 신호일 {len(got)}건")
    print("  ※ 아래는 **원 net 관측용**이다. 사전등록 판정은 **연도매칭 초과** 기준이고"
          " forward 30건에서만 한다.")
    for mode in ("backfill", "live"):
        by = nets.get(mode) or {}
        total = sum(len(v) for v in by.values())
        print(f"  [{mode}] 정산 {total}건" + ("" if total else " — 아직 없음"))
        for _, _, name in TIERS:
            v = by.get(name) or []
            if v:
                print(f"    {name:<6} n {len(v):>4}  평균 net {st.mean(v):+.3f}%  "
                      f"승 {100 * sum(1 for x in v if x > 0) / len(v):.1f}%")
            else:
                print(f"    {name:<6} n    0")
    live_total = sum(len(v) for v in (nets.get("live") or {}).values())
    print(f"  → 사전등록 판정까지 forward {max(0, 30 - live_total)}건 남음 "
          f"(등록: 30건에서 t20p > t0 이면 2단 사이징 근거, 현재 적용 없음)")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    return report() if argv and argv[0] == "report" else annotate()


if __name__ == "__main__":
    raise SystemExit(main())
