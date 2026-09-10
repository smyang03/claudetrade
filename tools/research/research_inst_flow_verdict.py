# -*- coding: utf-8 -*-
"""기관 수급 축 — 사전등록 규칙 판정 (2026-09-10).

사전등록: `docs/reports/preregistration_kr_institutional_flow_20260910.md` (커밋 5490e68, 표본 보기 전 고정)
  신호   : inst = 기관 순매매 / 그날 거래량 × 100  ≤ **−8%**
  선별   : 세션당 inst가 가장 낮은 **3종목**(K=3). K=1은 부수 관측.
  진입/출구: 다음 세션 시가, TP12 / SL25 / D7, KR 비용 0.25 (저장소 `contract_exit_v2`)
  국면 게이트 없음.

판정선
  창 A(인샘플 잔여 213세션): K=3 세션평균 > 0 **및** 세션 t ≥ 1.5 **및** 전량 대조 대비 증분 > 0
  창 B(OOS)              : K=3 세션평균 > 0 **및** 부호가 A와 같음
  **둘 다** 통과해야 후보. 하나만이면 기각.

수급 단위 주의: KRX는 순매수'거래대금'/거래대금, 네이버는 순매매'수량'/거래량이다.
2025-10-16 상위 20종목 대조에서 부호 20/20 일치·상관 +0.971로 동등함을 확인했다(사전등록 §5).

사용: python tools/research/research_inst_flow_verdict.py <naver_jsonl> <krx_flow_jsonl> <scratchpad_dir>
"""
from __future__ import annotations

import csv
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from virtual_books import contract_exit_v2, FEE_KR, HOLD_SESSIONS  # noqa: E402

DB = f"file:{ROOT / 'data' / 'shadow' / 'virtual_books.db'}?mode=ro"
CACHE_B = ROOT / "data" / "analysis" / "kr_fallen_price_cache_2025.json"
INST_THR = -8.0
K = 3


def stats(rows):
    """rows = [(session_date, ticker, net)] → (n, sessions, mean, t, win)"""
    if not rows:
        return None
    byd = defaultdict(list)
    for d, _, v in rows:
        byd[d].append(v)
    sm = [st.mean(v) for v in byd.values()]
    t = st.mean(sm) / (st.pstdev(sm) / len(sm) ** 0.5) if len(sm) > 2 and st.pstdev(sm) else float("nan")
    return dict(n=len(rows), sessions=len(sm), mean=st.mean(sm), t=t,
                win=100 * sum(v > 0 for _, _, v in rows) / len(rows),
                trade_mean=st.mean(v for _, _, v in rows))


def show(rows, label, indent="  "):
    s = stats(rows)
    if not s:
        print(f"{indent}{label:32s} n 0")
        return None
    print(f"{indent}{label:32s} n {s['n']:5d} 세션 {s['sessions']:3d} 세션평균 {s['mean']:+6.2f} t {s['t']:+5.2f} "
          f"| 거래평균 {s['trade_mean']:+6.2f} | 승 {s['win']:4.1f}%")
    return s


def load_naver(p: Path) -> dict[str, dict[str, list[int]]]:
    """ticker → {date: [vol, inst, foreign]} (같은 종목 여러 행이면 병합)."""
    out: dict[str, dict[str, list[int]]] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("rows"):
            out.setdefault(r["ticker"], {}).update(r["rows"])
    return out


def inst_ratio(nv: dict, tk: str, d: str) -> float | None:
    row = (nv.get(tk) or {}).get(d)
    if not row or not row[0]:
        return None
    return row[1] / row[0] * 100.0


def pick_and_score(cands: list[dict], nv: dict, label: str) -> None:
    """cands = [{d, tk, v}] — 신호 적용·K 선별·판정 출력."""
    for c in cands:
        c["inst"] = inst_ratio(nv, c["tk"], c["d"])
    have = [c for c in cands if c["inst"] is not None]
    cov = 100 * len(have) / len(cands) if cands else 0
    print(f"\n=== {label} — 급락 {len(cands)}건 중 수급 결합 {len(have)}건 ({cov:.0f}%) / {len({c['d'] for c in have})}세션")
    if not have:
        return
    R = lambda L: [(c["d"], c["tk"], c["v"]) for c in L]
    base_all = show(R(have), "전량(대조)")
    sig = [c for c in have if c["inst"] <= INST_THR]
    show(R(sig), f"기관 ≤{INST_THR}% 전량")
    show(R([c for c in have if c["inst"] > INST_THR]), f"기관 >{INST_THR}% (버리는 쪽)")

    byd = defaultdict(list)
    for c in sig:
        byd[c["d"]].append(c)
    picks = [x for v in byd.values() for x in sorted(v, key=lambda y: y["inst"])[:K]]
    s_k = show(R(picks), f"★ 사전등록 규칙 K={K}")
    byd_all = defaultdict(list)
    for c in have:
        byd_all[c["d"]].append(c)
    ctrl = [x for v in byd_all.values() for x in v[:K]]
    s_c = show(R(ctrl), f"K={K} 전량 대조")
    byd1 = defaultdict(list)
    for c in sig:
        byd1[c["d"]].append(c)
    show(R([sorted(v, key=lambda y: y["inst"])[0] for v in byd1.values()]), "K=1 (부수)")
    if s_k and s_c:
        print(f"   → K={K} 증분 {s_k['mean'] - s_c['mean']:+.2f}pp (규칙 {s_k['mean']:+.2f} vs 대조 {s_c['mean']:+.2f})")
    return s_k, s_c


def window_a(nv, krx_flow: Path):
    have_krx = set()
    if krx_flow.exists():
        for line in krx_flow.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not r.get("errors"):
                have_krx.add(r["date"])
    with closing(sqlite3.connect(DB, uri=True, timeout=5)) as con:
        rows = con.execute("select session_date, ticker, net_pct from trades where strategy_id='xkr_fallen3' "
                           "and backfill=1 and status='CLOSED' and net_pct is not null").fetchall()
    return [dict(d=d, tk=tk, v=net) for d, tk, net in rows if d not in have_krx]


def _from_bars(bars_by_tk: dict[str, list[tuple]], lo: str, hi: str) -> list[dict]:
    out = []
    for tk, b in bars_by_tk.items():
        for i in range(1, len(b) - HOLD_SESSIONS - 1):
            d, o, h, l, c, v = b[i]
            pc = b[i - 1][4]
            if not (lo <= d <= hi) or pc <= 0 or c <= 0:
                continue
            chg = (c / pc - 1) * 100
            if chg > -3 or chg <= -30 or c * v < 2e9:
                continue
            res = contract_exit_v2(b[i + 1][1], b[i + 1:i + 1 + HOLD_SESSIONS], fee=FEE_KR, be_lock=False)
            if res:
                out.append(dict(d=d, tk=tk, v=res[0]))
    return out


def window_b():
    raw = json.loads(CACHE_B.read_text(encoding="utf-8"))
    bars = {tk: sorted((x["d"], x.get("o") or 0, x.get("h") or 0, x.get("l") or 0, x.get("c") or 0, x.get("v") or 0)
                       for x in arr if x.get("c")) for tk, arr in raw.items()}
    return _from_bars(bars, "2024-12-01", "2025-05-31")


def window_b2(sp: Path):
    d = sp / "kr_oos_px"
    if not d.exists():
        return []
    bars = {}
    for p in d.glob("*.csv"):
        rows = []
        for r in csv.DictReader(p.open(encoding="utf-8")):
            try:
                rows.append((r["Date"][:10], float(r["Open"]), float(r["High"]), float(r["Low"]),
                             float(r["Close"]), float(r["Volume"] or 0)))
            except (ValueError, KeyError):
                continue
        if len(rows) > 60:
            bars[p.stem] = sorted(rows)
    return _from_bars(bars, "2023-01-01", "2025-05-31")


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    nv = load_naver(Path(sys.argv[1]))
    print(f"네이버 수급 종목 {len(nv)} · 총 {sum(len(v) for v in nv.values()):,}일")
    a = pick_and_score(window_a(nv, Path(sys.argv[2])), nv, "창 A — 인샘플 잔여(2025-10-20~2026-09)")
    b = pick_and_score(window_b(), nv, "창 B — OOS 634종목(2024-12~2025-05)")
    b2 = pick_and_score(window_b2(Path(sys.argv[3])), nv, "창 B2 — OOS 자사주 250종목(2023-01~2025-05)")

    print("\n" + "=" * 78)
    print("판정 (사전등록 기준)")
    ok_a = bool(a and a[0] and a[1] and a[0]["mean"] > 0 and a[0]["t"] >= 1.5 and a[0]["mean"] > a[1]["mean"])
    print(f"  창 A: {'통과' if ok_a else '불통과'}" + (f" (평균 {a[0]['mean']:+.2f}, t {a[0]['t']:+.2f}, 증분 {a[0]['mean']-a[1]['mean']:+.2f}pp)" if a and a[0] and a[1] else ""))
    for nm, w in (("창 B", b), ("창 B2", b2)):
        if w and w[0]:
            same = (w[0]["mean"] > 0) and ok_a
            print(f"  {nm}: 평균 {w[0]['mean']:+.2f} t {w[0]['t']:+.2f} → {'양수·A와 부호 일치' if same else '조건 미충족'}")
    both = ok_a and any(w and w[0] and w[0]["mean"] > 0 for w in (b, b2))
    print(f"\n  최종: {'후보 — 쉐도우 arm 등록 검토' if both else '기각 — 사전등록 판정선 미달'}")
    print("  (통과해도 실매수는 forward 30 정산 세션 이후 운영자 결정)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
