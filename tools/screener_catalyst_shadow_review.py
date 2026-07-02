from __future__ import annotations

"""스크리너 catalyst(뉴스/실적) 우선 shadow — 라이브 후보를 catalyst 우선 재정렬 시
품질(forward) 개선을 다국면 누적 측정 (read-only, 라이브 코드 무수정).

배경(2026-07-01 12인토론+시뮬): catalyst 있는 후보가 KR 멀티데이에서 robust하게 나음
(무catalyst 종가 -2.5~-5.7% vs 있으면 방어, placebo 초과). US는 착시(3일 placebo가 나음).
catalyst=저변동(무catalyst=급등추격). 단 6월 단일기간이라 OOS(다국면) 미검증 →
이 도구를 매 세션 실행해 누적, 다국면서 catalyst 우선이 현재 quality-top-N보다
robust하게 나은지 판정. 실제 selection은 안 바꿈(순수 계측).

방법: audit_candidate_rows(candidate_quality_score·news_or_earnings) + audit_candidate_outcomes
(return_pct, horizon)를 세션별로 조인. A=현재 quality top-N, B=catalyst 우선 top-N,
C=placebo(무작위 우선, 10회). Δ(B-A) vs Δ(C-A). 월별 층화.

판정: catalyst Δ > placebo Δ (+0.1%p 이상) 다국면 지속 = 진짜 → enforce 검토.
placebo와 비슷 = 착시. US는 기본 제외(착시 확인됨, --market US로 강제 가능).

mode=ro + busy_timeout. 외부 API·brain·DB 쓰기 없음. return_pct는 종목수익(실현net 아님).
"""

import argparse
import json
import random
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
CATALYST_TAGS = {"direct_catalyst", "earnings_or_guidance"}


def _avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.mean(xs), 3) if xs else None


def _has_catalyst_from_json(nj) -> bool:
    if not nj:
        return False
    try:
        v = json.loads(nj)
        return bool(v)
    except (json.JSONDecodeError, TypeError):
        return bool(nj and nj not in ("[]", "{}", "null"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", default="KR", choices=["KR", "US"])
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--target", type=int, default=0, help="0이면 KR28/US24 기본")
    ap.add_argument("--horizons", default="1440,4320", help="쉼표구분 (분)")
    ap.add_argument("--placebo-iters", type=int, default=10)
    args = ap.parse_args()

    N = args.target or (28 if args.market == "KR" else 24)
    horizons = [int(h) for h in str(args.horizons).split(",") if h.strip()]
    random.seed(42)

    conn = sqlite3.connect(f"file:{AUDIT_DB}?mode=ro", uri=True, timeout=15)
    conn.execute("PRAGMA busy_timeout=15000")

    # 후보(종목당 최신 quality/news) per session
    sess_cand = defaultdict(dict)  # sd -> {ticker: (quality, has_news)}
    for sd, tk, q, nj in conn.execute(
        "SELECT session_date,ticker,candidate_quality_score,news_or_earnings_sources_json "
        "FROM audit_candidate_rows WHERE market=? AND session_date>=? ORDER BY updated_at",
        (args.market, args.since),
    ):
        sess_cand[sd][tk] = (q if isinstance(q, (int, float)) else 0.0, _has_catalyst_from_json(nj))

    print(f"=== catalyst shadow | {args.market} | since {args.since} | top-{N} ===")
    print(f"세션 {len(sess_cand)}개\n")

    for horizon in horizons:
        # return map
        retmap = {}
        for k, ret in conn.execute(
            "SELECT candidate_key,return_pct FROM audit_candidate_outcomes WHERE horizon_min=?", (horizon,)
        ):
            if isinstance(ret, (int, float)):
                retmap[k] = ret
        # candidate_key -> (session, ticker) 매핑 위해 rows 재조회로 ret 연결
        # (key는 audit_candidate_rows.candidate_key = outcomes.candidate_key)
        keyret = defaultdict(dict)  # sd -> {ticker: ret}
        for sd, tk, key in conn.execute(
            "SELECT session_date,ticker,candidate_key FROM audit_candidate_rows WHERE market=? AND session_date>=?",
            (args.market, args.since),
        ):
            if key in retmap:
                keyret[sd][tk] = retmap[key]

        by_month = defaultdict(lambda: {"b": [], "plac": []})
        all_b, all_plac = [], []
        for sd, cands in sess_cand.items():
            rets = keyret.get(sd, {})
            items = [(tk, q, nw, rets[tk]) for tk, (q, nw) in cands.items() if tk in rets]
            if len(items) < N:
                continue
            a = sorted(items, key=lambda x: -x[1])[:N]
            ar = _avg([x[3] for x in a])
            b = sorted(items, key=lambda x: (not x[2], -x[1]))[:N]
            br = _avg([x[3] for x in b])
            if ar is None or br is None:
                continue
            month = str(sd)[:7]
            by_month[month]["b"].append(br - ar)
            all_b.append(br - ar)
            k = sum(1 for x in items if x[2])
            pls = []
            for _ in range(args.placebo_iters):
                idx = set(random.sample(range(len(items)), min(k, len(items))))
                tagged = [(i in idx, items[i][1], items[i][3]) for i in range(len(items))]
                p = sorted(tagged, key=lambda x: (not x[0], -x[1]))[:N]
                pr = _avg([x[2] for x in p])
                if pr is not None:
                    pls.append(pr - ar)
            if pls:
                pv = statistics.mean(pls)
                by_month[month]["plac"].append(pv)
                all_plac.append(pv)

        lbl = "1일" if horizon == 1440 else "3일" if horizon == 4320 else f"{horizon}m"
        real, plac = _avg(all_b), _avg(all_plac)
        diff = round((real or 0) - (plac or 0), 3)
        verdict = "진짜(catalyst>>placebo)" if diff > 0.1 else "착시(placebo와 비슷/미달)"
        print(f"[horizon {horizon}m={lbl}] catalyst Δ={real}%p | placebo Δ={plac}%p | 차이 {diff}%p → {verdict}")
        for m in sorted(by_month):
            d = by_month[m]
            print(f"    {m}: catalyst Δ={_avg(d['b'])} placebo Δ={_avg(d['plac'])} ({len(d['b'])}세션)")
        print()

    conn.close()
    print("[판정] catalyst-placebo > +0.1%p 다국면 지속 = 진짜(enforce 검토). "
          "US는 착시 확인(3일 placebo 우세). return_pct=종목수익(실현net 아님)·5m추정 한계.")


if __name__ == "__main__":
    main()
