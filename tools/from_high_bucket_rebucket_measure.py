"""from_high_bucket 라벨 복구 + 재측정 (A3) — READ-ONLY 측정 도구.

목적: data/ticker_selection_log.db 의 from_high_bucket 라벨이 6월 0% 커버리지로
끊겼으나 raw from_high_pct 는 살아있다. 동일 경계로 raw 에서 버킷을 재생성해
종목특성(pullback/at_high/near_high/deep)의 forward 부호가 월·레짐에 걸쳐
안정적인지 측정한다.

쓰기 절대 금지: DB는 mode=ro 로만 연다. 어떤 UPDATE/INSERT 도 하지 않는다.

버킷 경계(코드와 동일 — minority_report/analysts._candidate_pullback_bucket,
universe_manager._candidate_pullback_bucket, runtime/candidate_quality_trainer.from_high_bin):
    value <= -5.0 -> deep
    value <= -2.0 -> pullback
    value <= -0.5 -> near_high
    else          -> at_high
    None/""       -> unknown
"""
from __future__ import annotations

import os
import sqlite3
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ticker_selection_log.db")


def rebucket(from_high_pct):
    if from_high_pct is None:
        return "unknown"
    try:
        value = float(from_high_pct)
    except (TypeError, ValueError):
        return "unknown"
    if value <= -5.0:
        return "deep"
    if value <= -2.0:
        return "pullback"
    if value <= -0.5:
        return "near_high"
    return "at_high"


def _ro_conn():
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=10000").fetchone()
    except Exception:
        pass
    conn.row_factory = sqlite3.Row
    return conn


def _mean(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _sign_label(mean):
    if mean is None:
        return "n/a"
    if mean > 0:
        return "+"
    if mean < 0:
        return "-"
    return "0"


def main():
    conn = _ro_conn()

    # 0) 라벨 전멸 검증: 월별 from_high_bucket 채워진 비율 vs from_high_pct 채워진 비율
    print("=" * 78)
    print("[0] 라벨 커버리지 — 월별 from_high_bucket NOT NULL/'' vs from_high_pct NOT NULL")
    print("=" * 78)
    rows = conn.execute(
        """
        SELECT market,
               substr(date,1,7) AS ym,
               COUNT(*) AS n,
               SUM(CASE WHEN from_high_bucket IS NOT NULL AND TRIM(from_high_bucket)!='' THEN 1 ELSE 0 END) AS bucket_filled,
               SUM(CASE WHEN from_high_pct IS NOT NULL THEN 1 ELSE 0 END) AS pct_filled
        FROM ticker_selection_log
        GROUP BY market, ym
        ORDER BY market, ym
        """
    ).fetchall()
    print(f"{'market':>6} {'ym':>8} {'rows':>7} {'bucket_filled':>14} {'pct_filled':>11}")
    for r in rows:
        print(f"{r['market']:>6} {r['ym']:>8} {r['n']:>7} {r['bucket_filled']:>14} {r['pct_filled']:>11}")

    # consensus_mode 분포 (레짐 층화용)
    print()
    print("=" * 78)
    print("[0b] consensus_mode 분포 (레짐 층화 후보)")
    print("=" * 78)
    rows = conn.execute(
        """
        SELECT market, substr(date,1,7) AS ym, COALESCE(consensus_mode,'(null)') AS cm, COUNT(*) AS n
        FROM ticker_selection_log
        GROUP BY market, ym, cm
        ORDER BY market, ym, n DESC
        """
    ).fetchall()
    for r in rows:
        print(f"{r['market']:>6} {r['ym']:>8} {r['cm']:>20} n={r['n']}")

    # 1) 재버킷 × 월 × 시장: trade_ready=1, forward_3d NOT NULL
    #    raw from_high_pct -> rebucket. distinct (ticker,date) dedup 은 하지 않고
    #    selection 행 단위로 집계하되, trade_ready 행만(라벨 있던 구간 비교 기준과 동일).
    for ready_filter, ready_name in ((1, "trade_ready=1"), (0, "watch_only(trade_ready=0)")):
        for fwd_col in ("forward_3d",):
            print()
            print("=" * 78)
            print(f"[1] 재버킷 x 월 x 시장 — {ready_name}, {fwd_col} (NULL 제외)")
            print("=" * 78)
            rows = conn.execute(
                f"""
                SELECT market, substr(date,1,7) AS ym, from_high_pct, {fwd_col} AS fwd
                FROM ticker_selection_log
                WHERE trade_ready={ready_filter}
                  AND {fwd_col} IS NOT NULL
                  AND from_high_pct IS NOT NULL
                """
            ).fetchall()
            agg = defaultdict(list)  # (market, ym, bucket) -> [fwd]
            for r in rows:
                b = rebucket(r["from_high_pct"])
                agg[(r["market"], r["ym"], b)].append(r["fwd"])
            bucket_order = ["deep", "pullback", "near_high", "at_high", "unknown"]
            markets = sorted({k[0] for k in agg})
            for mk in markets:
                yms = sorted({k[1] for k in agg if k[0] == mk})
                print(f"\n--- {mk} ({ready_name}, {fwd_col}) ---")
                header = f"{'bucket':>10}" + "".join(f"{ym:>16}" for ym in yms)
                print(header)
                for b in bucket_order:
                    cells = []
                    any_data = False
                    for ym in yms:
                        vals = agg.get((mk, ym, b), [])
                        if vals:
                            any_data = True
                            m = _mean(vals)
                            cells.append(f"{m:+.2f}%/n{len(vals)}")
                        else:
                            cells.append("-")
                    if any_data:
                        print(f"{b:>10}" + "".join(f"{c:>16}" for c in cells))

    # 2) 부호 안정성: 버킷별 월간 부호 시퀀스 (trade_ready=1, forward_3d)
    print()
    print("=" * 78)
    print("[2] 부호 안정성 — 버킷별 월간 forward_3d 부호 시퀀스 (trade_ready=1)")
    print("=" * 78)
    rows = conn.execute(
        """
        SELECT market, substr(date,1,7) AS ym, from_high_pct, forward_3d AS fwd
        FROM ticker_selection_log
        WHERE trade_ready=1 AND forward_3d IS NOT NULL AND from_high_pct IS NOT NULL
        """
    ).fetchall()
    agg = defaultdict(list)
    for r in rows:
        b = rebucket(r["from_high_pct"])
        agg[(r["market"], b)].append((r["ym"], r["fwd"]))
    for (mk, b) in sorted(agg):
        by_month = defaultdict(list)
        for ym, fwd in agg[(mk, b)]:
            by_month[ym].append(fwd)
        seq = []
        for ym in sorted(by_month):
            m = _mean(by_month[ym])
            seq.append(f"{ym}:{_sign_label(m)}{m:+.2f}(n{len(by_month[ym])})")
        overall = _mean([fwd for _, fwd in agg[(mk, b)]])
        print(f"{mk:>4} {b:>10} overall={_sign_label(overall)}{overall:+.2f}% n={len(agg[(mk,b)])} | " + " ".join(seq))

    # 3) 레짐(consensus_mode) 층화: 버킷 x 레짐 x 시장 (trade_ready=1, forward_3d)
    print()
    print("=" * 78)
    print("[3] 레짐 층화 — 버킷 x consensus_mode x 시장 (trade_ready=1, forward_3d)")
    print("=" * 78)
    rows = conn.execute(
        """
        SELECT market, COALESCE(consensus_mode,'(null)') AS cm, from_high_pct, forward_3d AS fwd
        FROM ticker_selection_log
        WHERE trade_ready=1 AND forward_3d IS NOT NULL AND from_high_pct IS NOT NULL
        """
    ).fetchall()
    agg = defaultdict(list)
    for r in rows:
        b = rebucket(r["from_high_pct"])
        agg[(r["market"], b, r["cm"])].append(r["fwd"])
    for (mk, b, cm) in sorted(agg):
        vals = agg[(mk, b, cm)]
        m = _mean(vals)
        print(f"{mk:>4} {b:>10} {cm:>18} {_sign_label(m)}{m:+.2f}% n={len(vals)}")

    conn.close()


if __name__ == "__main__":
    main()
