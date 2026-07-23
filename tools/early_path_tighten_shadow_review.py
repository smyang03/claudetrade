from __future__ import annotations

"""초기경로 적색 손절조임 shadow 판정 — 조인 손절이 얼마나 회수했을지 세션 단위로 본다.

배경 (2026-07-23 수익성 분석):
  손실 엔진 = LOSS_CAP 블록(전부 MFE<1%). 배포한 본전+0.8% 청산은 적색 후 반등건(17%)만
  잡고, 단조 하락 83%는 못 잡는다. 가설 = 적색 + 반등없음이면 손절을 조여 이르게 정리.

  이 도구는 `_mark_early_path_tighten_shadow`가 남긴 로그(조인 발동 지점)와 canonical의
  실제 청산 net을 대조해, "조인 손절이 발동했다면 vs 실제로 어떻게 됐나"를 계산한다.

판정 규율 (2026-07-23에 배운 것):
  - 세션 단위로 본다. 거래 단위 개선이 세션 단위에서 소멸한 사례가 많다.
  - 조인 손절 net은 근사다(fire 지점 pnl - 왕복 수수료). 실제 체결가는 슬리피지가 있다.
  - 표본이 없으면 "0건"이라 나온다 — 그건 결함이 아니라 아직 발동이 없어서다.
  - 반등건(recovered_to_buffer)은 조임 대상이 아니다 — 그 꼬리는 계산에서 뺀다.

  python tools/early_path_tighten_shadow_review.py
  python tools/early_path_tighten_shadow_review.py --since 2026-07-23 --fee-pct 0.44
"""

import argparse
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
LOG_DIR = ROOT / "logs" / "system"

# [초기경로 손절조임 shadow] TICKER 적색마크 -0.10% · 반등없이 -1.30% 도달 → 조인손절(-1.0%) 발동지점
FIRE_RE = re.compile(
    r"\[초기경로 손절조임 shadow\]\s+(?P<tk>\S+)\s+적색마크\s+(?P<mark>[-+][\d.]+)%"
    r".*?반등없이\s+(?P<fire>[-+][\d.]+)%\s+도달\s+.*?조인손절\((?P<stop>[-+][\d.]+)%\)"
)
TS_RE = re.compile(r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


def parse_fires(since: str) -> list[dict]:
    fires: list[dict] = []
    for path in sorted(LOG_DIR.glob("live_trading_*.log")):
        day = path.stem.replace("live_trading_", "")
        if len(day) == 8:
            iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            if iso < since:
                continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = FIRE_RE.search(line)
                if not m:
                    continue
                tsm = TS_RE.match(line)
                fires.append({
                    "ticker": m.group("tk"),
                    "mark": float(m.group("mark")),
                    "fire_pnl": float(m.group("fire")),
                    "stop_pct": float(m.group("stop")),
                    "at": tsm.group("ts") if tsm else "",
                    "session": (tsm.group("ts")[:10] if tsm else ""),
                })
    return fires


def load_actual(since: str) -> dict:
    """ticker+session_date → 실제 청산 net (가장 최근 청산건)."""
    if not ML_DB.exists():
        return {}
    con = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA busy_timeout=50000")
    con.row_factory = sqlite3.Row
    out: dict = {}
    for r in con.execute(
        "SELECT ticker, session_date, pnl_pct_net, pnl_pct FROM v2_canonical_performance "
        "WHERE closed=1 AND session_date>=?", (since,)):
        net = r["pnl_pct_net"] if r["pnl_pct_net"] is not None else r["pnl_pct"]
        if net is None:
            continue
        out[(str(r["ticker"]), str(r["session_date"]))] = float(net)
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="초기경로 손절조임 shadow 판정")
    ap.add_argument("--since", default="2026-07-23")
    ap.add_argument("--fee-pct", type=float, default=0.44,
                    help="왕복 수수료 근사(US 실측 0.44, KR 0.21)")
    args = ap.parse_args()

    fires = parse_fires(args.since)
    actual = load_actual(args.since)
    print(f"=== 초기경로 손절조임 shadow (since {args.since}) ===")
    print(f"조인 손절 발동 관측 {len(fires)}건\n")
    if not fires:
        print("아직 발동 없음 — 적색 마크 후 반등없이 조인선 도달한 건이 나오면 쌓인다.")
        print("(결함 아님: 발동 조건이 아직 발생하지 않았다.)")
        return 0

    by_session: dict = defaultdict(lambda: {"actual": 0.0, "tighten": 0.0, "n": 0, "unmatched": 0})
    rows = []
    for f in fires:
        key = (f["ticker"], f["session"])
        act = actual.get(key)
        # 조인 손절 net 근사: 발동 pnl에서 왕복 수수료 차감
        tighten_net = f["fire_pnl"] - args.fee_pct
        s = by_session[f["session"]]
        s["n"] += 1
        if act is None:
            s["unmatched"] += 1
            rows.append((f, None, tighten_net))
            continue
        s["actual"] += act
        s["tighten"] += tighten_net
        rows.append((f, act, tighten_net))

    print(f"  {'세션':12s} {'발동':>4s} {'실제net합':>10s} {'조임net합':>10s} {'차이(회수)':>12s}")
    tot_a = tot_t = 0.0
    matched_sessions = 0
    for sess in sorted(by_session):
        s = by_session[sess]
        matched = s["n"] - s["unmatched"]
        if not matched:
            print(f"  {sess:12s} {s['n']:4d}  (청산 미매칭 {s['unmatched']})")
            continue
        diff = s["tighten"] - s["actual"]
        tot_a += s["actual"]; tot_t += s["tighten"]; matched_sessions += 1
        print(f"  {sess:12s} {s['n']:4d} {s['actual']:+10.2f} {s['tighten']:+10.2f} {diff:+12.2f}")
    if matched_sessions:
        print(f"\n  통산: 실제 {tot_a:+.2f}% → 조임 {tot_t:+.2f}% (회수 {tot_t-tot_a:+.2f}%p, {matched_sessions}세션)")
        print("  ※ 회수 양수 = 조인 손절이 나았다. 단 근사(수수료·무슬리피지)이며 세션 부호가 안정적일 때만 신뢰.")

    print("\n  개별 건:")
    for f, act, tnet in rows[:20]:
        astr = f"{act:+.2f}%" if act is not None else "미매칭"
        print(f"    {f['session']} {f['ticker']:8s} 마크{f['mark']:+.2f} 발동{f['fire_pnl']:+.2f} "
              f"→ 조임net {tnet:+.2f}% vs 실제 {astr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
