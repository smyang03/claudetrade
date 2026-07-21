"""regime_entry_gate shadow 리뷰 — 시장별(KR/US) 분리 집계. 매매·config 무접촉.

전략 성과가 KR/US 정반대(momentum KR−2.17/US+0.22 등)이므로 게이트 판정을
전역 합산으로 보지 않는다(평균의 오류). 시장별로 decision·국면 분포와
would_skip 대상의 사후 net(가능하면 v2_learning join)을 분리해 보여준다.

사용: python tools/regime_entry_gate_review.py [--days N]
근거: regime-is-the-edge-not-selection-20260721 (나쁜장 진입 스킵 net −331k→−48k).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
from collections import Counter, defaultdict

FUNNEL = os.path.join("logs", "funnel")
DECISIONS_DB = os.path.join("data", "ml", "decisions.db")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _load_rows() -> list[dict]:
    rows: list[dict] = []
    for f in sorted(glob.glob(os.path.join(FUNNEL, "regime_entry_gate_*.jsonl"))):
        try:
            for line in open(f, encoding="utf-8"):
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        except OSError:
            continue
    return rows


def _net_lookup() -> dict[tuple[str, str, str], float]:
    """(market, session_date, ticker) → pnl_pct_net (closed live만)."""
    out: dict[tuple[str, str, str], float] = {}
    try:
        con = sqlite3.connect(f"file:{DECISIONS_DB}?mode=ro", uri=True)
        con.execute("pragma busy_timeout=5000")
        q = (
            "select market, session_date, ticker, coalesce(pnl_pct_net, pnl_pct) "
            "from v2_canonical_performance where runtime_mode='live' and closed=1"
        )
        for m, d, t, p in con.execute(q):
            if p is not None:
                out[(str(m), str(d), str(t))] = float(p)
        con.close()
    except Exception:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()

    rows = _load_rows()
    if not rows:
        print("regime_entry_gate 기록 없음 — PathB 진입 시도가 쌓이면 채워짐(prospective).")
        print("확인 경로: pathb 진입부 shadow 훅 → logs/funnel/regime_entry_gate_*.jsonl")
        return 0

    net = _net_lookup()
    markets = sorted({str(r.get("market") or "?") for r in rows})
    print(f"regime_entry_gate 기록 {len(rows)}건 — 시장 {markets}\n")

    for mkt in markets:
        sub = [r for r in rows if str(r.get("market") or "?") == mkt]
        print(f"===== {mkt} (n={len(sub)}) =====")

        dec_c = Counter(r.get("decision") for r in sub)
        print("  decision 분포:", dict(dec_c.most_common()))

        by_reg = defaultdict(list)
        for r in sub:
            by_reg[str(r.get("regime") or "?")].append(r)
        print("  국면별:")
        for reg, rs in sorted(by_reg.items(), key=lambda x: -len(x[1])):
            skips = sum(1 for r in rs if r.get("decision") in ("would_skip", "skip"))
            print(f"    {reg:14} n={len(rs):>4} would_skip/skip={skips}")

        # would_skip 대상 사후 net (같은 세션 실체결이 있으면 — '막았으면 잃었을/벌었을' 근사)
        ws = [r for r in sub if r.get("decision") in ("would_skip", "skip")]
        joined = []
        for r in ws:
            key = (mkt, str(r.get("session_date") or ""), str(r.get("ticker") or ""))
            if key in net:
                joined.append(net[key])
        if joined:
            print(
                f"  ★would_skip 종목의 실현 net(진입이 실제 있었던 경우): "
                f"n={len(joined)} avg={_mean(joined):+.3f}% sum={sum(joined):+.2f}%p"
            )
            print("  → avg가 음수면 게이트가 손실을 막았을 것(enforce 지지), 양수면 이익 희생(반증)")
        else:
            print("  would_skip 종목의 실현 net 매칭 0건 (미체결이면 정상 — 반사실은 candidate_audit 기반)")
        print()

    print("판정 규율: 시장별로만 판정. enforce 전환은 운영자 확인 필수(매수 차단 게이트).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
