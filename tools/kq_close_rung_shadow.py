# -*- coding: utf-8 -*-
"""종가 rung 관측 원장 — 패닉 v2 바로 아래 한 칸 (2026-09-13, 관측 전용).

09-13 사다리 측정(`tools/research/research_panic_etf_rung_margin.py`)에서 비대칭이 나왔다.
현행 v2(갭 ≤ −1 **또는** 종가 ≤ −2.5) 바로 아래 칸을 둘로 쪼개 보니

  갭  ∈ (−1.0, −0.5]  → 회당 **+0.003%** (연 10.9회를 더 해도 버는 게 없다)
  종가 ∈ (−2.5, −2.0] → 회당 **+0.500%** (기준 +0.523%와 거의 같다, 연 5.6회)

즉 **얕은 구간에서 신호를 지니는 축은 갭이 아니라 종가(장중에 팔린 날)다.**
종가 칸은 사전등록 규칙(전반기 t ≥ 2)을 못 넘어 기각됐지만 후반기가 더 강해서
(+0.682%) forward로 확인할 값이 있다. 연 +2.80%p 규모다.

## 설계 — 라이브 신호 경로를 건드리지 않는다

⚠️ **패닉 원장에 행을 넣지 않는다.** `forward_gate_watch` · `kq_panic_k_tier` ·
`event_family_report` 등 다수가 `kind == "trade"`로 그 원장을 읽는다. 거기에 rung 행을
넣으면 **v2 신호 수·성과가 조용히 오염된다.** 그래서 별도 원장으로 분리한다
(k 등급 원장 `kq_panic_k_tier.jsonl`과 같은 방식).

`tools/kr_index_etf_panic_shadow.py`는 **읽기만** 한다(가격 캐시 + 상수).
`GAP_THR`·`THR`·`UNITS` 어느 것도 바꾸지 않는다.

## 계약

  조건   : 종가 ∈ (−2.5, −2.0] **이면서** v2 신호가 아님(갭 > −1)
  진입   : 그날 종가
  청산   : 다음 영업일 시가
  비용   : 왕복 0.05%(패닉 레인과 동일), 세금은 환산 때만
  단위   : 1단위 고정 — **2단 사이징을 적용하지 않는다**(강신호 등급은 갭 기준이고
           이 칸은 정의상 갭이 얕다. 사이징은 §D 판정 뒤 문제다)

멱등이다. 다음날 시가가 아직 없는 날은 기록하지 않고 다음 실행에서 다시 본다.

사용:
  python tools/kq_close_rung_shadow.py          # 미기록 신호일 채우기
  python tools/kq_close_rung_shadow.py report   # 백필/forward 분리 현황
"""
from __future__ import annotations

import json
import statistics as st
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.kr_index_etf_panic_shadow as P  # noqa: E402  (read-only)

OUT = ROOT / "data" / "shadow" / "kq_close_rung.jsonl"
TK = "233740"
CLOSE_LO, CLOSE_HI = -2.5, -2.0      # 종가 ∈ (CLOSE_LO, CLOSE_HI]
FORWARD_START = "2026-09-13"         # 이 날짜부터가 forward 표본이다
TARGET_FORWARD = 30                  # 사전등록 판정 표본
CONTRACT = "kq_close_rung_obs:close_-2.5~-2.0/close_buy/next_open_sell"


def is_rung(c: dict) -> bool:
    """v2 신호가 아니면서 종가가 얕은 하락 구간에 든 날."""
    chg, gap = c.get("kq_chg"), c.get("kq_gap")
    if chg is None or not (CLOSE_LO < chg <= CLOSE_HI):
        return False
    if gap is not None and gap <= P.GAP_THR:      # 갭으로 이미 v2 신호다
        return False
    return chg > P.THR                            # 종가로도 v2 신호면 제외


def _existing() -> dict:
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


def annotate() -> int:
    cache = P._read_cache()
    if not cache:
        print("[KQRUNG] 가격 캐시 없음", flush=True)
        return 1
    dates = sorted(cache)
    got = _existing()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    written = held = 0
    with OUT.open("a", encoding="utf-8") as fh:
        for i, d in enumerate(dates[:-1] if dates else []):
            if d < P.BACKFILL_START or d in got or not is_rung(cache[d]):
                continue
            entry = cache[d].get(f"{TK}_close")
            exit_ = cache[dates[i + 1]].get(f"{TK}_open")
            if not entry or not exit_:
                held += 1          # 다음날 시가가 아직 없다. 다음 실행에서 재시도.
                continue
            raw = (exit_ / entry - 1.0) * 100.0
            fh.write(json.dumps({
                "session_date": d, "ticker": TK, "contract": CONTRACT,
                "mode": "live" if d >= FORWARD_START else "backfill",
                "kq_chg_close": cache[d]["kq_chg"], "kq_gap": cache[d].get("kq_gap"),
                "entry_close": entry, "exit_open": exit_, "exit_date": dates[i + 1],
                "overnight_pct": round(raw, 4), "net_pct": round(raw - P.COST, 4),
                "units": 1, "recorded_at": now}, ensure_ascii=False) + "\n")
            written += 1

    # 마지막 날은 다음날 시가가 원리상 없다 — 보류에 세지 않는다.
    last = dates[-1] if dates else None
    if last and is_rung(cache[last]) and last not in got:
        held += 1
    print(f"[KQRUNG] 신규 {written}행 · 다음 시가 대기 {held}건 "
          f"(누적 {len(got) + written})", flush=True)
    return 0


def report() -> int:
    got = _existing()
    if not got:
        print("[KQRUNG] 원장 비어 있음. 먼저 주석을 돌린다.", flush=True)
        return 0
    by = {"backfill": [], "live": []}
    for r in got.values():
        by.setdefault(r.get("mode") or "?", []).append(r["net_pct"])
    print(f"[KQRUNG] 기록 {len(got)}건 · 계약 {CONTRACT}")
    print("  ※ 아래는 **원 net 관측용**이다. 판정은 연도매칭 초과 기준으로"
          f" forward {TARGET_FORWARD}건에서 한다.")
    for mode in ("backfill", "live"):
        v = by.get(mode) or []
        if v:
            print(f"  [{mode}] n {len(v):>4}  평균 net {st.mean(v):+.3f}%  "
                  f"승 {100 * sum(1 for x in v if x > 0) / len(v):.1f}%")
        else:
            print(f"  [{mode}] n    0")
    live_n = len(by.get("live") or [])
    print(f"  → 판정까지 forward {max(0, TARGET_FORWARD - live_n)}건 남음 "
          f"(등록: 비용 후 연도매칭 초과 t ≥ 2. 현재 적용 없음 — 관측만)")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    return report() if argv and argv[0] == "report" else annotate()


if __name__ == "__main__":
    raise SystemExit(main())
