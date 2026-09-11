#!/usr/bin/env python3
"""E5 풀(R2∪R4 + 라이브 바닥) 위에서 KR 랭킹 재측정 — read-only, 사후 분석 전용.

배경: 계좌 도구(selection_account_compare)는 xkr_fallen3 거래 기록 풀에서
할인깊은순(라이브 랭킹)이 거래대금큰순·무작위보다 크게 나쁘다고 측정했다.
E5(kr_fallen_ranking_experiment)는 다른 풀·다른 지표에서 할인깊은순이
무작위 동급이라고 기록했다. 두 결과를 같은 풀에서 대조한다.

설계:
  1. E5의 후보 생성·정산(_candidates_and_nets)을 **그대로 호출**해 기준을 만든다.
  2. 같은 로직의 파라미터화 사본을 만들고, 바닥 기본값에서 (세션,티커,net)이
     E5 원본과 완전히 같은지 **게이트로 검사한다**. 다르면 즉시 중단한다.
  3. 그 위에 V6(거래대금 큰순)·무작위 200회를 얹고, 바닥을 끄고 재측정한다.
  4. 지표 2종 — 픽당 평균 net(E5 비교용)과 라이브 계약 순차 손익(원).

주의: 가격 캐시는 가변이다. 08-07 E5 리포트 수치는 그 시점 캐시의 것이고
현재 캐시로는 재현되지 않는다(2026분 후보 573 -> 785건).
"""
from __future__ import annotations

import json
import random
import statistics as st
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kr_fallen_gate_report import rule_flags  # noqa: E402
from tools.kr_fallen_ranking_experiment import (  # noqa: E402
    CACHES, COST, DROP_GE, DROP_FLOOR, PRICE_GE, AMT20_GE, _candidates_and_nets,
)

ORDER_KRW = 220_000      # KR_FALLEN_ORDER_MAX_KRW (2026-08-21 개정)
SLOTS = 3                # KR_FALLEN_MAX_OPEN_SLOTS_PHASE3
SEEDS = 200


def build(cache, *, price_ge=PRICE_GE, amt20_ge=AMT20_GE):
    """E5 _candidates_and_nets의 파라미터화 사본. 바닥 기본값이면 원본과 같아야 한다.

    원본이 버리는 값(진입/청산 날짜, 신호일 거래대금, 가격)을 추가로 돌려준다.
    """
    out = []
    for code, bars in cache.items():
        if not bars or len(bars) < 27:
            continue
        for idx in range(22, len(bars)):
            b = bars[idx]
            prev = bars[idx - 1]["c"]
            if prev <= 0:
                continue
            chg = 100 * (b["c"] / prev - 1)
            if not (DROP_FLOOR <= chg <= -DROP_GE) or b["c"] < price_ge:
                continue
            w20 = bars[idx - 20:idx]
            if sum(x["amt"] for x in w20) / 20 < amt20_ge:
                continue
            ma20 = sum(x["c"] for x in w20) / 20
            rets = [100 * (w20[m]["c"] / w20[m - 1]["c"] - 1) for m in range(1, 20)
                    if w20[m - 1]["c"] > 0]
            feats = {
                "chg": chg,
                "gap": 100 * (b["o"] / prev - 1),
                "rv20": st.pstdev(rets) if len(rets) > 3 else 99.0,
                "ma20_disc": 100 * (b["c"] / ma20 - 1) if ma20 > 0 else 0.0,
            }
            flags = rule_flags({"feats": feats, "pass_all": False})
            if not (flags["R2_할인저변동"] or flags["R4_갭할인"]):
                continue
            after = bars[idx + 1:idx + 6]
            if len(after) < 5:
                continue
            e = after[0]["o"]
            if not e or e <= 0:
                continue
            tp, sl = e * 1.12, e * 0.75
            net, held = None, None
            for k, bar in enumerate(after):
                if k > 0 and bar["o"] <= sl:
                    net, held = 100 * (bar["o"] / e - 1) - COST, k
                    break
                if k > 0 and bar["o"] >= tp:
                    net, held = 100 * (bar["o"] / e - 1) - COST, k
                    break
                if bar["l"] <= sl:
                    net, held = 100 * (sl / e - 1) - COST, k
                    break
                if bar["h"] >= tp:
                    net, held = 100 * (tp / e - 1) - COST, k
                    break
            if net is None:
                net, held = 100 * (after[-1]["c"] / e - 1) - COST, 4
            out.append({"session": b["d"], "ticker": code, "net": net,
                        "both": flags["R2_할인저변동"] and flags["R4_갭할인"],
                        "entry_date": after[0]["d"], "exit_date": after[held]["d"],
                        "amt": b["amt"], "price": b["c"], "entry_price": e, **feats})
    return out


def gate(cache, label):
    """사본이 E5 원본과 같은 후보·같은 net을 내는지 검사. 다르면 중단한다."""
    original = sorted((c["session"], c["ticker"], round(c["net"], 9))
                      for c in _candidates_and_nets(cache))
    copied = sorted((c["session"], c["ticker"], round(c["net"], 9))
                    for c in build(cache))
    if original != copied:
        raise SystemExit(f"게이트 실패({label}): 사본이 E5 원본과 다르다 "
                         f"(원본 {len(original)} / 사본 {len(copied)})")
    print(f"게이트 통과({label}): E5 원본과 후보·net 완전 일치 n={len(original)}")


def by_session(cands):
    out = {}
    for c in cands:
        out.setdefault(c["session"], []).append(c)
    for v in out.values():
        v.sort(key=lambda r: r["ticker"])          # 결정론
    return dict(sorted(out.items()))


RANKS = {
    "V0_할인깊은순(라이브)": lambda v: min(v, key=lambda r: (r["ma20_disc"], r["ticker"])),
    "V6_거래대금큰순": lambda v: min(v, key=lambda r: (-r["amt"], r["ticker"])),
}


def per_pick(sessions, chooser):
    return [chooser(v)["net"] for v in sessions.values()]


def sequence(sessions, chooser, *, order_krw=ORDER_KRW, slots=SLOTS, per_day=1,
             per_day_fn=None):
    """라이브 KR 계약 순차 집행: 건당 22만, 보유 중 동일종목 재진입 금지.

    기본값은 현행 계약(슬롯 3 · 일 1건). per_day/slots를 올리면 용량 사다리가 된다.
    per_day_fn(k)를 주면 그날 후보 수 k에 따라 진입 건수를 정한다(k-조건부 사이징).
    """
    held, pnl, trades = {}, 0.0, []
    for day, pool in sessions.items():
        held = {t: d for t, d in held.items() if d >= day}
        avail = [c for c in pool if c["ticker"] not in held]
        room = per_day_fn(len(pool)) if per_day_fn is not None else per_day
        for _ in range(max(0, min(room, slots - len(held)))):
            if not avail:
                break
            pick = chooser(avail)
            avail = [c for c in avail if c["ticker"] != pick["ticker"]]
            pnl += order_krw * pick["net"] / 100
            held[pick["ticker"]] = pick["exit_date"]
            trades.append(pick)
    return pnl, trades


def stats(nets):
    if not nets:
        return "n=0"
    pos = sum(x for x in nets if x > 0)
    neg = -sum(x for x in nets if x <= 0)
    pf = round(pos / neg, 2) if neg > 0 else float("inf")
    return (f"n={len(nets)} 평균 {st.mean(nets):+.2f}% 승률 "
            f"{100 * sum(1 for x in nets if x > 0) / len(nets):.0f}% PF {pf}")


def random_chooser(seed):
    rng = random.Random(seed)
    return lambda v: v[rng.randrange(len(v))]


def report(sessions, label, lines):
    def emit(t=""):
        print(t)
        lines.append(t)
    multi = {s: v for s, v in sessions.items() if len(v) >= 2}
    emit(f"\n### {label} — 후보 {sum(len(v) for v in sessions.values())}건 / "
         f"세션 {len(sessions)}일 / 다발일 {len(multi)}일")
    for scope, subset in (("전체 세션", sessions), ("다발일만", multi)):
        if not subset:
            continue
        emit(f"\n**{scope}**\n")
        emit("| 랭킹 | 픽당 평균 net | 순차 손익(22만·슬롯3·일1건) | 진입 |")
        emit("|---|---|---:|---:|")
        rows = {}
        for name, chooser in RANKS.items():
            nets = per_pick(subset, chooser)
            pnl, trades = sequence(subset, chooser)
            rows[name] = (st.mean(nets), pnl)
            emit(f"| {name} | {stats(nets)} | {pnl:+,.0f}원 | {len(trades)}건 |")
        rnd_means = sorted(st.mean(per_pick(subset, random_chooser(s))) for s in range(SEEDS))
        rnd_pnls = sorted(sequence(subset, random_chooser(s))[0] for s in range(SEEDS))
        emit(f"| 무작위 {SEEDS}회 (중앙값) | 평균 {st.median(rnd_means):+.2f}% "
             f"(p05 {rnd_means[9]:+.2f} / p95 {rnd_means[-10]:+.2f}) | "
             f"{st.median(rnd_pnls):+,.0f}원 | — |")
        emit("")
        for name, (mean_net, pnl) in rows.items():
            emit(f"- {name}: 픽당 평균이 무작위 {sum(m <= mean_net for m in rnd_means)}/{SEEDS}보다 좋고, "
                 f"순차 손익이 무작위 {sum(p <= pnl for p in rnd_pnls)}/{SEEDS}보다 좋다")
    return lines


LADDER = ((3, 1), (3, 2), (6, 2), (10, 2), (10, 3), (20, 3), (40, 5))


def capacity(sessions, label, lines):
    """용량 사다리 — 랭킹이 아니라 슬롯·일한도를 올렸을 때의 손익."""
    def emit(t=""):
        print(t)
        lines.append(t)
    months = len({d[:7] for d in sessions})
    emit(f"\n### 용량 사다리 — {label} (세션 {len(sessions)}일 / {months}개월)")
    emit("\n건당 22만원 고정. 랭킹이 아니라 **몇 개를 사느냐**를 올린다.\n")
    emit("| 슬롯 x 일한도 | 최대노출 | V0 할인깊은순 | V6 거래대금큰순 | 무작위 중앙값 |")
    emit("|---|---:|---:|---:|---:|")
    for slots, per_day in LADDER:
        cells = []
        for name in RANKS:
            pnl, trades = sequence(sessions, RANKS[name], slots=slots, per_day=per_day)
            cells.append(f"{pnl:+,.0f}원 ({len(trades)}건)")
        runs = [sequence(sessions, random_chooser(s), slots=slots, per_day=per_day)
                for s in range(SEEDS)]
        med = st.median([p for p, _ in runs])
        medn = st.median([len(t) for _, t in runs])
        emit(f"| {slots} x {per_day} | {slots * ORDER_KRW:,}원 | {cells[0]} | {cells[1]} "
             f"| {med:+,.0f}원 ({medn:.0f}건) |")
    return lines


def main():
    lines = [f"# KR 랭킹 풀 대조 — E5 풀에서 할인깊은순 vs 거래대금큰순 ({date.today().isoformat()})", "",
             "read-only 사후 분석. 라이브 변경 아님. 후보·정산은 E5(`kr_fallen_ranking_experiment`) 규약을",
             "그대로 쓴다(R2∪R4, TP12/SL25/D5, cost 0.25, 갭 시가 체결).",
             "가격 캐시는 가변이므로 08-07 E5 리포트 수치는 현재 캐시로 재현되지 않는다."]
    for year, path in CACHES.items():
        cache = json.loads(path.read_text(encoding="utf-8"))
        gate(cache, year)
        lines.append(f"\n## {year} 캐시")
        report(by_session(build(cache)), "라이브 바닥 적용 (price>=7,110 · amt20>=10억)", lines)
        report(by_session(build(cache, price_ge=0.0)), "가격 바닥 해제 (price>=0)", lines)
        report(by_session(build(cache, price_ge=0.0, amt20_ge=0.0)), "바닥 전부 해제", lines)
        capacity(by_session(build(cache)), "라이브 바닥 적용", lines)
    out = ROOT / "docs" / "reports" / f"kr_rank_pool_reconcile_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
