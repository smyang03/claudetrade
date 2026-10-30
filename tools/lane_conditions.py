# -*- coding: utf-8 -*-
"""레인 계약서 중단·확대 조건 진행도 (2026-10-30 운영자 "진행해" — 중단 조건 자동 감지·알림).

docs/operating_model.md §2의 조건을 실전 원장으로 매일 계산해 일일 보고에 싣는다. **조회 전용 — 잠그지 않는다.**
중단·폐기·확대 결정은 운영자만 내린다(CLAUDE.md). 판정 시점에 닿으면 `reached=True`로 일일 보고 맨 위 경고에 올린다.
자동 잠금은 이미 계약된 손실 한도(canary·패닉·L7 각 도구 안)만 그대로 둔다.
  python tools/lane_conditions.py      (출력만)
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
SH = ROOT / "data" / "shadow"

T1_FILL_N, T1_SLIP_MAX_BP = 20, 20.0          # 중단 ①: 체결 20건 시점 체결가−공식시가 중앙값 > +20bp
T1_EXIT_N, T1_COST = 50, 0.005                # 중단 ②/확대: 만기 50건 시점 IWM 대비 net 평균(왕복 약 0.50%)
CANARY_N, PANIC_N, IDX_ROUNDS = 30, 20, 4


def _read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _last_positions(rows: list[dict]) -> list[dict]:
    """같은 (ticker, entry_session) 포지션의 마지막 행만."""
    last: dict = {}
    for r in rows:
        if r.get("kind") == "position":
            last[(r.get("ticker"), r.get("entry_session"))] = r
    return list(last.values())


def iwm_open_ret(d0: str, d1: str) -> float | None:
    """IWM 시가(d0) → 시가(d1) 수익률. 실패하면 None."""
    try:
        import yfinance as yf
        df = yf.download("IWM", start=d0, progress=False, auto_adjust=False)
        opens = {x.date().isoformat(): float(v) for x, v in zip(df.index, df["Open"].to_numpy().ravel())}
        return opens[d1] / opens[d0] - 1 if d0 in opens and d1 in opens else None
    except Exception:  # noqa: BLE001
        return None


def t1_conditions(rows: list[dict], bench: Callable[[str, str], float | None] = iwm_open_ret) -> list[dict]:
    slips = [float(r["slip_bp_vs_open"]) for r in rows if r.get("kind") == "fill" and r.get("side") == "buy"
             and isinstance(r.get("slip_bp_vs_open"), (int, float))]
    med = statistics.median(slips) if slips else None
    c1 = {"lane": "T1", "cond": "①슬리피지", "n": len(slips), "need": T1_FILL_N,
          "value": None if med is None else round(med, 1), "reached": len(slips) >= T1_FILL_N}
    c1["verdict"] = None if not c1["reached"] else ("중단 조건 도달" if med > T1_SLIP_MAX_BP else "통과")
    c1["line"] = (f"T1 ① 체결 {len(slips)}/{T1_FILL_N} · 체결가−시가 중앙값 "
                  f"{'—' if med is None else f'{med:+.0f}bp'} (중단 > +{T1_SLIP_MAX_BP:.0f}bp)")

    sells = [r for r in rows if r.get("kind") == "order" and r.get("side") == "sell" and r.get("success")]
    ex, missing = [], 0
    for p in _last_positions(rows):
        if p.get("close_reason") != "moo_exit" or not p.get("entry_price") or not p.get("exit_price"):
            continue
        d0 = str(p.get("entry_session"))
        d1 = max((str(s["session"]) for s in sells if s.get("ticker") == p.get("ticker") and str(s.get("session")) > d0),
                 default=None)
        b = bench(d0, d1) if d1 else None
        if b is None:
            missing += 1
            continue
        ex.append(float(p["exit_price"]) / float(p["entry_price"]) - 1 - T1_COST - b)
    mean = statistics.fmean(ex) if ex else None
    n = len(ex) + missing
    c2 = {"lane": "T1", "cond": "②만기 net", "n": n, "need": T1_EXIT_N,
          "value": None if mean is None else round(mean * 100, 2), "reached": n >= T1_EXIT_N}
    c2["verdict"] = None if not c2["reached"] else (
        "벤치 산출 실패" if missing else ("중단 조건 도달" if mean < 0 else "확대 조건 도달" if mean > 0 else "경계"))
    c2["line"] = (f"T1 ② 만기 {n}/{T1_EXIT_N} · IWM 대비 net 평균 {'—' if mean is None else f'{mean * 100:+.2f}%'}"
                  + (f" (벤치 산출 실패 {missing})" if missing else ""))
    return [c1, c2]


def canary_conditions(rows: list[dict], loss_limit: float = 50_000) -> list[dict]:
    done = [p for p in _last_positions(rows) if p.get("status") == "CLOSED" and p.get("close_reason") == "h5_exit"]
    realized = sum(float(p.get("pnl_krw") or 0) for p in _last_positions(rows) if p.get("status") == "CLOSED") + \
        sum(float(r.get("pnl_krw") or 0) for r in rows if r.get("kind") == "close")
    c = {"lane": "L2", "cond": "실시간 30건", "n": len(done), "need": CANARY_N, "value": round(realized),
         "reached": len(done) >= CANARY_N}
    c["verdict"] = "판정 시점(쉐도우 원장 규모매칭 초과 리포트 필요)" if c["reached"] else None
    c["line"] = f"L2 canary 만기 {len(done)}/{CANARY_N} · 누적 실현 {realized:,.0f}원 (자동 중단 −{loss_limit:,.0f})"
    return [c]


def panic_conditions(rows: list[dict], loss_limit: float = 50_000) -> list[dict]:
    closes = [r for r in rows if r.get("kind") == "close" and r.get("entry_price") and r.get("qty")]
    rets = [float(r["pnl_krw"]) / (float(r["entry_price"]) * float(r["qty"])) for r in closes]
    mean = statistics.fmean(rets) if rets else None
    realized = sum(float(r.get("pnl_krw") or 0) for r in rows if r.get("kind") == "close")
    c = {"lane": "L3", "cond": "forward 20건", "n": len(rets), "need": PANIC_N,
         "value": None if mean is None else round(mean * 100, 2), "reached": len(rets) >= PANIC_N}
    c["verdict"] = None if not c["reached"] else ("폐기 조건 도달" if mean < 0 else "소액 단계 조건 충족")
    c["line"] = (f"L3 패닉 {len(rets)}/{PANIC_N} · net 평균 {'—' if mean is None else f'{mean * 100:+.2f}%'}"
                 f" · 누적 실현 {realized:,.0f}원 (자동 중단 −{loss_limit:,.0f})")
    return [c]


def index_incl_conditions(rows: list[dict], loss_limit: float = 100_000) -> list[dict]:
    by_round: dict = {}
    for r in rows:
        if r.get("kind") == "close":
            by_round.setdefault(r.get("round"), []).append(r)
    open_rounds = {p.get("round") for p in _last_positions(rows) if p.get("status") == "OPEN"}
    done = {k: v for k, v in by_round.items() if k not in open_rounds}
    realized = sum(float(r.get("pnl_krw") or 0) for v in by_round.values() for r in v)
    c = {"lane": "L7", "cond": "forward 4회차", "n": len(done), "need": IDX_ROUNDS, "value": round(realized),
         "reached": len(done) >= IDX_ROUNDS}
    c["verdict"] = "판정 시점(회차별 규모매칭 초과 리포트 필요)" if c["reached"] else None
    c["line"] = f"L7 지수편입 회차 {len(done)}/{IDX_ROUNDS} · 누적 실현 {realized:,.0f}원 (신규 중단 −{loss_limit:,.0f})"
    return [c]


def all_conditions(sh: Path = SH, bench: Callable[[str, str], float | None] = iwm_open_ret) -> list[dict]:
    out = []
    for fn, f in (("us_earnings_t1_live.jsonl", lambda r: t1_conditions(r, bench)),
                  ("kr_buyback_canary_live.jsonl", canary_conditions),
                  ("kr_panic_etf_micro_live.jsonl", panic_conditions),
                  ("kr_index_inclusion_live.jsonl", index_incl_conditions)):
        try:
            out += f(_read(sh / fn))
        except Exception as exc:  # noqa: BLE001 — 보고 도구라 한 레인 실패가 보고 전체를 막지 않게
            out.append({"lane": fn, "cond": "계산 실패", "reached": True, "verdict": f"계산 실패 {type(exc).__name__}",
                        "line": f"{fn} 조건 계산 실패: {type(exc).__name__}"})
    return out


def main() -> int:
    for c in all_conditions():
        print(c["line"] + (f"  ← {c['verdict']}" if c.get("verdict") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
