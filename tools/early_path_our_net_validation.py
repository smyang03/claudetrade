from __future__ import annotations

"""진입 후 초기 반응(30분 마크)이 '우리 실제 net'을 가르는지 검증한다.

배경: [[six-visions-verified-20260719]]에서 초기 경로가 이후를 가른다는 것이
나왔고(d1 녹색 승률 76% vs 적색 30%), 2026-07-22 외부 독립 표본(judge 예산으로
버려진 후보 109건)에서도 재현됐다(30분 녹색 +0.675%/69.8% vs 적색 -0.549%/32.1%).

그러나 그 둘은 모두 gross다. CLAUDE.md의 판정 기준은 "우리 시스템의 실제 net
(수수료·FX 반영, 우리 보유기간)"이므로, 실제 체결·청산된 우리 거래에 같은 축을
붙여 net으로 재판정한다. 여기서도 갈리면 조기익절/러너 분기의 근거가 되고,
갈리지 않으면 gross에서만 보이는 현상으로 기각한다.

no-lookahead: 30분 마크는 진입 시점 이후 30분까지의 정보만 쓰고, 판정 대상인
pnl_pct_net은 그 이후에 확정된 값이다. 마크 계산에 청산 정보를 쓰지 않는다.

한계:
- yfinance 5분봉 60일 창 → 최근 약 2개월 거래만 검증된다.
- 30분 마크는 yfinance 종가 기준이라 우리 실제 체결가 경로와 미세하게 다르다.
- KR은 종목코드→야후 접미사(.KS/.KQ) 매핑이 필요해 기본은 US만 본다.

  python tools/early_path_our_net_validation.py --probe-min 30
"""

import argparse
import random
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "ml" / "decisions.db"


def load(market: str, days: int) -> list[tuple]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=25000")
        return con.execute(
            """SELECT ticker, session_date, earliest_fill_at, entry_price, pnl_pct_net
               FROM v2_canonical_performance
               WHERE market=? AND closed=1 AND pnl_pct_net IS NOT NULL
                 AND earliest_fill_at IS NOT NULL AND entry_price > 0
                 AND session_date >= date('now', ?)
               ORDER BY session_date""",
            (market, f"-{int(days)} day"),
        ).fetchall()
    finally:
        con.close()


def spearman(xs: list[float], ys: list[float]) -> float:
    def rank(a: list[float]) -> list[int]:
        order = sorted(range(len(a)), key=lambda i: a[i])
        r = [0] * len(a)
        for pos, i in enumerate(order):
            r[i] = pos
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(len(xs)))
    den = (sum((r - mx) ** 2 for r in rx) * sum((r - my) ** 2 for r in ry)) ** 0.5
    return num / den if den else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="초기 경로의 우리 net 검증")
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--days", type=int, default=58)
    ap.add_argument("--probe-min", type=int, default=30, help="초기 반응 판정 시점(분)")
    ap.add_argument("--perm", type=int, default=10000)
    args = ap.parse_args()

    import yfinance as yf

    random.seed(20260722)
    rows = load(args.market, args.days)
    print(f"[{args.market}] 청산·net 보유 거래 {len(rows)}건 (최근 {args.days}일)")
    if not rows:
        print("표본이 없다.")
        return 0

    probe = max(1, args.probe_min // 5)
    cache: dict = {}

    def series(tk: str):
        if tk in cache:
            return cache[tk]
        try:
            df = yf.download(tk, period="60d", interval="5m",
                             progress=False, auto_adjust=False)
            close = df["Close"].iloc[:, 0] if hasattr(df["Close"], "columns") else df["Close"]
            idx = df.index
            idx = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
            cache[tk] = (idx, close)
        except Exception:
            cache[tk] = None
        return cache[tk]

    data: list[tuple[str, str, float, float]] = []   # (session, ticker, mark%, net%)
    for ticker, session_date, fill_at, entry_price, net in rows:
        s = series(str(ticker).strip())
        if not s:
            continue
        idx, close = s
        try:
            fill = datetime.fromisoformat(str(fill_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        naive = fill.replace(tzinfo=None)
        pos = [i for i, t in enumerate(idx)
               if t.to_pydatetime().replace(tzinfo=None) <= naive]
        if not pos:
            continue
        t0 = pos[-1]
        if t0 + probe >= len(close):
            continue
        entry = float(entry_price)
        mark = (float(close.iloc[t0 + probe]) / entry - 1.0) * 100.0
        data.append((str(session_date), str(ticker), mark, float(net)))

    print(f"초기 마크 결합 성공 {len(data)}건")
    if len(data) < 20:
        print("표본이 부족해 판정하지 않는다.")
        return 0

    green = [d[3] for d in data if d[2] > 0]
    red = [d[3] for d in data if d[2] <= 0]
    print(f"\n=== {args.probe_min}분 초기 반응별 '우리 실제 net' ===")
    for lab, v in (("녹색(마크>0)", green), ("적색(마크<=0)", red)):
        if not v:
            print(f"  {lab}: 표본없음")
            continue
        w = sum(1 for x in v if x > 0) / len(v) * 100
        print(f"  {lab}: n={len(v):4d}  net {mean(v):+7.3f}%  승률 {w:5.1f}%")
    if green and red:
        print(f"  격차: {mean(green)-mean(red):+.3f}%p")

    print("\n=== 마크 구간별 net ===")
    for lo, hi, lab in [(-99, -2, "<-2%"), (-2, -1, "-2~-1%"), (-1, 0, "-1~0%"),
                        (0, 1, "0~+1%"), (1, 2, "+1~2%"), (2, 99, ">+2%")]:
        v = [d[3] for d in data if lo <= d[2] < hi]
        if not v:
            continue
        w = sum(1 for x in v if x > 0) / len(v) * 100
        print(f"  {lab:>8} n={len(v):4d}  net {mean(v):+7.3f}%  승률 {w:5.1f}%")

    xs = [d[2] for d in data]
    ys = [d[3] for d in data]
    rho = spearman(xs, ys)
    cnt = 0
    for _ in range(args.perm):
        sh = ys[:]
        random.shuffle(sh)
        if spearman(xs, sh) >= rho:
            cnt += 1
    print(f"\n거래단위 스피어만 rho={rho:+.3f}  순열 p={cnt/args.perm:.4f}")

    # 세션 단위 — 같은 날 거래는 독립이 아니다
    sess: dict = defaultdict(list)
    for sd, _tk, mk, nt in data:
        sess[sd].append((mk, nt))
    S = [(mean([m for m, _ in v]), mean([n for _, n in v])) for v in sess.values()]
    if len(S) >= 8:
        sx = [a for a, _ in S]
        sy = [b for _, b in S]
        srho = spearman(sx, sy)
        cnt2 = 0
        for _ in range(args.perm):
            sh = sy[:]
            random.shuffle(sh)
            if spearman(sx, sh) >= srho:
                cnt2 += 1
        print(f"세션단위({len(S)}세션) 스피어만 rho={srho:+.3f}  순열 p={cnt2/args.perm:.4f}")
        gs = [b for a, b in S if a > 0]
        rs = [b for a, b in S if a <= 0]
        f = lambda v: f"세션 {len(v):3d} net {mean(v):+7.3f}%" if v else "표본없음"  # noqa: E731
        print(f"  녹색 세션: {f(gs)}")
        print(f"  적색 세션: {f(rs)}")

    # ── 조기 컷 시뮬 ────────────────────────────────────────────────────
    # 적색을 30분에 끊으면 그 시점 gross 마크에서 왕복비용을 뺀 값으로 확정된다.
    # 실제 우리 fee를 원장에서 읽어 쓴다(가정 금지).
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    try:
        fee = con.execute(
            """SELECT AVG(fee_pct_round_trip) FROM v2_canonical_performance
               WHERE market=? AND fee_pct_round_trip IS NOT NULL
                 AND session_date >= date('now', ?)""",
            (args.market, f"-{int(args.days)} day"),
        ).fetchone()[0]
    finally:
        con.close()
    if fee is None:
        print("\n[조기 컷 시뮬] 원장에 fee_pct_round_trip이 없어 생략한다.")
    else:
        fee = float(fee)
        print(f"\n=== 적색 {args.probe_min}분 조기 컷 시뮬 (실제 왕복비용 {fee:.3f}% 적용) ===")
        actual = [d[3] for d in data]
        cut = [d[3] if d[2] > 0 else (d[2] - fee) for d in data]
        print(f"  현행(그대로 보유): n={len(actual):4d}  net {mean(actual):+7.3f}%")
        print(f"  적색 30분 컷    : n={len(cut):4d}  net {mean(cut):+7.3f}%  "
              f"개선 {mean(cut)-mean(actual):+.3f}%p")
        # 적색 중 실제로 이긴 건(조기 컷의 기회비용)
        won = [d for d in data if d[2] <= 0 and d[3] > 0]
        if won:
            print(f"  ※ 기회비용: 적색인데 결국 이긴 건 {len(won)}건, "
                  f"평균 net {mean([d[3] for d in won]):+.3f}% — 이걸 포기하게 된다")
        # 세션 단위
        sess_cut: dict = defaultdict(list)
        sess_act: dict = defaultdict(list)
        for sd, _tk, mk, nt in data:
            sess_act[sd].append(nt)
            sess_cut[sd].append(nt if mk > 0 else (mk - fee))
        sa = [mean(v) for v in sess_act.values()]
        sc = [mean(v) for v in sess_cut.values()]
        better = sum(1 for k in sess_act if mean(sess_cut[k]) > mean(sess_act[k]))
        print(f"  세션단위: 현행 {mean(sa):+.3f}% → 컷 {mean(sc):+.3f}%  "
              f"({better}/{len(sa)} 세션에서 개선)")

    print("\n  판정: 거래·세션 단위가 모두 같은 방향이고 순열 p가 낮아야 채택한다.")
    print("  한계: yfinance 60일 창 · 마크는 야후 종가 기준이라 실체결 경로와 미세 차이.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
