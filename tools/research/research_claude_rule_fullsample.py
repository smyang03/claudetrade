#!/usr/bin/env python3
"""전략이 후보군, Claude가 매수 선택 — 전 표본 (read-only, 2026-09-22).

운영자 프레임: *"전략으로 후보군을 고르고 매수는 클로드가 선택한다."*
그리고 *"표본 늘려서 다시 쳐봐."*

## 먼저 정직하게 — 내가 한 건 '판단'이 아니라 '규칙'이었다

09-22 20일 시험에서 내가 고른 방식은 결정론적이었다.

    ma20_disc < 0 으로 거르고 → rv20 오름차순 상위 3 → dvol ≥ 20 · chg > −15% 유지

즉 **자동화 가능한 규칙**이다. 그러면 20일이 아니라 **319일 전부**에 돌려 진짜 표본을 만들 수 있다.
"Claude가 고른다"의 실체는 "Claude가 이 규칙을 세웠다"이고, 규칙의 성능은 기계가 재는 게 맞다.

## 규칙을 바꾸지 않는다

표본을 늘리면서 규칙을 손대면 그건 맞추기다. **20일 시험에 쓴 규칙 그대로** 돌린다.
파라미터(3종목·ma20<0·rv20 오름차순·dvol 20·chg −15%)는 전부 그때 값이다.

## 구간 분리

  · **본 20일** — 내가 결과를 본 뒤라 인샘플 취급한다.
  · **나머지 299일** — 이번에 처음 도는 구간. 판정은 여기서 한다.

## 대조군 셋

  · **전량** — 그날 후보 전부(조건 전량 매수 = 기저선). 주 대조군.
  · **무작위 3** — 시드 20개 평균. "3종목으로 줄인 것만으로 생기는 효과"를 걷어낸다.
  · **규칙 분해** — ma20 필터만 / rv20 정렬만. 어느 쪽이 값을 내는지 본다(탐색용, 판정 아님).

## ⚠️ 이 규칙의 출신

`ma20_disc < 0`은 09-10 "MA20 위 급락 매수 금지"가 OOS 6/6 부호 유지한 데서,
`rv20` 오름차순은 09-17 "rv20 상위가 나쁘다"에서 가져왔다. **둘 다 같은 저장소 데이터에서
나온 것**이라 299일도 규칙 출신 기준으로는 완전한 OOS가 아니다. 이건 깎아서 읽어야 한다.
"""
from __future__ import annotations

import csv
import json
import math
import random
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_benchmark_excess import bench_bars  # noqa: E402
from tools.virtual_books import HOLD_SESSIONS, SL, STRATEGIES, TP, bars_kr  # noqa: E402

HOLDS = {s["id"]: int(s.get("hold", HOLD_SESSIONS)) for s in STRATEGIES}
VB = ROOT / "data" / "shadow" / "virtual_books.db"
KQ_CACHE = ROOT / "data" / "analysis" / "kr_index_etf_prices.csv"
POOL = "xkr_fallen3"
PICKS = 3
CAND = 20
SEED = 20260922
SEEN = {"2026-06-01", "2026-06-05", "2026-06-11", "2026-06-17", "2026-06-23", "2026-06-29",
        "2026-07-03", "2026-07-09", "2026-07-15", "2026-07-22", "2026-07-28", "2026-07-31",
        "2026-08-06", "2026-08-12", "2026-08-19", "2026-08-25", "2026-08-31", "2026-09-04",
        "2026-09-10", "2026-09-16"}


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def exit_bar(entry, win, hold):
    if not win or entry <= 0:
        return None
    for i, (_d, _o, hi, _lo, c, _v) in enumerate(win[:hold]):
        # D0은 종가만 쓴다(체결 전 고가 오염 방지) — contract_exit_v2와 같은 규약
        hip = (hi - entry) / entry * 100 if i > 0 else (c - entry) / entry * 100
        if hip >= TP or (c - entry) / entry * 100 <= SL:
            return i
    return hold - 1 if len(win) >= hold else None


def claude_rule(cands):
    """20일 시험에 쓴 규칙 그대로. 바꾸지 않는다."""
    ok = [c for c in cands
          if isinstance(c["f"].get("ma20_disc"), (int, float)) and c["f"]["ma20_disc"] < 0
          and isinstance(c["f"].get("rv20"), (int, float))
          and (c["f"].get("dvol") or 0) >= 20
          and (c["f"].get("chg") or 0) > -15]
    return [c["ticker"] for c in sorted(ok, key=lambda c: c["f"]["rv20"])[:PICKS]]


def rule_ma_only(cands):
    ok = [c for c in cands if isinstance(c["f"].get("ma20_disc"), (int, float))
          and c["f"]["ma20_disc"] < 0 and (c["f"].get("dvol") or 0) >= 20]
    rnd = random.Random(f"ma:{len(ok)}")
    return [c["ticker"] for c in rnd.sample(ok, min(PICKS, len(ok)))] if ok else []


def rule_rv_only(cands):
    ok = [c for c in cands if isinstance(c["f"].get("rv20"), (int, float))
          and (c["f"].get("dvol") or 0) >= 20]
    return [c["ticker"] for c in sorted(ok, key=lambda c: c["f"]["rv20"])[:PICKS]]


def main():
    ks = bench_bars("kr", "069500")
    ksi = {b[0]: i for i, b in enumerate(ks)}
    rows_kq = sorted(csv.DictReader(KQ_CACHE.open(encoding="utf-8-sig")), key=lambda r: r["date"])
    kq = [(r["date"], f(r["229200_open"]), f(r["229200_close"])) for r in rows_kq]
    kq = [x for x in kq if x[1] and x[2]]
    kqi = {d: i for i, (d, _, _) in enumerate(kq)}
    hold = HOLDS.get(POOL, HOLD_SESSIONS)

    con = sqlite3.connect(VB.resolve().as_uri() + "?mode=ro", uri=True)
    raw = con.execute("SELECT session_date, ticker, entry_price, net_pct, status, meta "
                      "FROM trades WHERE strategy_id=?", (POOL,)).fetchall()
    con.close()

    days = defaultdict(list)
    for d, tk, ep, net, stt, meta in raw:
        ft = (json.loads(meta or "{}").get("feat") or {})
        if "rv20" not in ft:
            continue
        days[d].append({"ticker": tk, "entry": ep, "net": net,
                        "closed": stt == "CLOSED" and net is not None, "f": ft})

    def excess(d, c):
        if not c["closed"] or not c["entry"]:
            return None
        bb = bars_kr(c["ticker"])
        j = next((i for i, b in enumerate(bb) if b[0] == d), None)
        ei = exit_bar(float(c["entry"]), bb[j:j + hold], hold) if j is not None else None
        if ei is None:
            return None
        out = {}
        i = ksi.get(d)
        if i is not None and i + ei < len(ks):
            out["KOSPI200"] = c["net"] - (ks[i + ei][4] / ks[i][1] - 1) * 100
        q = kqi.get(d)
        if q is not None and q + ei < len(kq):
            out["KOSDAQ150"] = c["net"] - (kq[q + ei][2] / kq[q][1] - 1) * 100
        return out

    diffs = defaultdict(lambda: defaultdict(list))     # arm → bench → [(날짜, 차)]
    for d in sorted(days):
        cands = days[d]
        if len(cands) > CAND:
            rnd = random.Random(f"{SEED}:{d}")
            cands = sorted(rnd.sample(cands, CAND), key=lambda c: c["ticker"])
        ex = {c["ticker"]: excess(d, c) for c in cands}
        pools = defaultdict(list)
        for tk, e in ex.items():
            if e:
                for k, v in e.items():
                    pools[k].append(v)
        if not pools:
            continue
        strategies = {"Claude 규칙": claude_rule(cands),
                      "MA20 필터만(무작위 3)": rule_ma_only(cands),
                      "rv20 정렬만": rule_rv_only(cands)}
        # 무작위 3 — 시드 20개 평균
        rnd2 = random.Random(f"rand:{d}")
        rand_pick = []
        for _ in range(20):
            rand_pick.append([c["ticker"] for c in rnd2.sample(cands, min(PICKS, len(cands)))])
        for name, tks in strategies.items():
            if not tks:
                continue
            for k in pools:
                v = [ex[t][k] for t in tks if ex.get(t) and k in ex[t]]
                if v:
                    diffs[name][k].append((d, st.mean(v) - st.mean(pools[k])))
        for k in pools:
            vals = []
            for tks in rand_pick:
                v = [ex[t][k] for t in tks if ex.get(t) and k in ex[t]]
                if v:
                    vals.append(st.mean(v))
            if vals:
                diffs["무작위 3(시드20 평균)"][k].append((d, st.mean(vals) - st.mean(pools[k])))

    def stat(pairs):
        v = [x for _, x in pairs]
        if len(v) < 5:
            return None
        m, sd = st.mean(v), st.stdev(v)
        t = m / (sd / math.sqrt(len(v))) if sd else None
        win = sum(1 for x in v if x > 0)
        p = sum(math.comb(len(v), i) * 0.5 ** len(v) for i in range(win, len(v) + 1))
        return {"n": len(v), "m": m, "med": st.median(v), "t": t, "win": win, "p": p}

    L = ["# 전략이 후보군, Claude가 매수 선택 — 전 표본 (2026-09-22)", "",
         "read-only. `tools/research/research_claude_rule_fullsample.py`(규약은 docstring).",
         "20일 시험에 쓴 규칙 **그대로** 319일에 돌린다. 규칙은 손대지 않았다.", "",
         "규칙: `ma20_disc < 0` 필터 → `rv20` 오름차순 상위 3 (dvol ≥ 20, chg > −15%)", ""]

    for k in ("KOSPI200", "KOSDAQ150"):
        L += [f"## 벤치 {k}", "",
              "| 방식 | 일수 | 평균 차 | 중앙값 | 날짜 t | 이긴 날 | 부호검정 p |",
              "|---|---:|---:|---:|---:|---:|---:|"]
        for name in ("Claude 규칙", "무작위 3(시드20 평균)", "MA20 필터만(무작위 3)", "rv20 정렬만"):
            s = stat(diffs[name][k])
            if not s:
                continue
            L.append(f"| {'**' if name.startswith('Claude') else ''}{name}"
                     f"{'**' if name.startswith('Claude') else ''} | {s['n']} | "
                     f"{s['m']:+.3f}%p | {s['med']:+.3f}%p | "
                     f"{s['t']:+.2f} | {s['win']}/{s['n']} | {s['p']:.4f} |")
        # 본 20일 vs 나머지
        seen = [x for x in diffs["Claude 규칙"][k] if x[0] in SEEN]
        rest = [x for x in diffs["Claude 규칙"][k] if x[0] not in SEEN]
        L += ["", "Claude 규칙을 **본 20일**과 **나머지**로 쪼개면:", "",
              "| 구간 | 일수 | 평균 차 | 중앙값 | 날짜 t | 이긴 날 | 부호검정 p |",
              "|---|---:|---:|---:|---:|---:|---:|"]
        for nm, arr in (("본 20일(인샘플 취급)", seen), ("**나머지(첫 실행)**", rest)):
            s = stat(arr)
            if s:
                L.append(f"| {nm} | {s['n']} | {s['m']:+.3f}%p | {s['med']:+.3f}%p | "
                         f"{s['t']:+.2f} | {s['win']}/{s['n']} | {s['p']:.4f} |")
        L.append("")

    L += ["## 읽는 법", "",
          "- **무작위 3과 비교가 핵심이다.** 3종목으로 줄이기만 해도 생기는 효과를 걷어내야",
          "  '고르기'의 값이 보인다.",
          "- 규칙 분해(MA20만 / rv20만)는 **탐색용**이다. 여기서 최고를 골라 쓰면 그건 맞추기다.",
          "- ⚠️ 규칙 출신이 같은 저장소 데이터다(`ma20_disc`는 09-10 OOS 게이트, `rv20`은 09-17",
          "  측정). 나머지 299일도 **규칙 출신 기준으로는 완전한 OOS가 아니다.**",
          "- ⚠️ 09-18 실측 분모 96~301. 요구 p는 0.0002~0.0005다.", ""]

    out = ROOT / "docs" / "reports" / "claude_rule_fullsample_20260922.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
