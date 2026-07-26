#!/usr/bin/env python3
"""진입 파이프라인 장중 오프라인 재현 (read-only, API/네트워크 미사용).

무엇을 하는가
  logs/funnel/post_open_features_*.jsonl 의 장중 스냅샷 시계열로
  "그 세션에 그 종목을 언제 사서 언제 팔았으면 net이 얼마였는가"를 재현한다.
  종가 기반 forward_*가 아니라 **실제 장중 가격 경로**를 쓴다.

데이터 (실측 2026-07-26 기준)
  스냅샷 156,668건 / (세션·시장·종목) 5,350조합 / 조합당 중앙값 15스냅샷 / 스팬 중앙값 300분.
  기간 2026-06~07. 필드: current_price, market_open_elapsed_min, momentum_state,
  opening_range_break/high/low, pullback_from_high_pct, from_open_high_pct,
  volume_ratio_open, time_normalized_rvol, vwap_distance_pct.

가격 경로 복원 (검증됨)
  running_high = current_price * (1 - pullback_from_high_pct/100)
  → 스냅샷 사이 봉내 고점을 포함한 러닝 고가를 복원한다. IREN 20260723 elapsed=18.3에서
    추정 43.3857 vs 표본최대 43.3150 — 표본최대보다 높게 나오는 것이 정상(봉내 고점 반영).

한계 (정직하게)
  - **러닝 저가 필드는 없다.** 손절 판정은 스냅샷 표본 가격으로만 하므로
    스냅샷 사이에 찍고 회복한 저점을 놓친다 → 손절 히트가 과소, net이 **낙관 편향**.
    익절(러닝 고가 사용)은 봉내 고점을 잡으므로 비대칭이다. 이 비대칭은
    --no-intrabar-high 로 끄고 대조할 수 있다.
  - 체결 슬리피지·부분체결·호가 스프레드 미반영. 비용은 왕복 상수(KR 0.21% / US 0.50%).
  - 스냅샷 간격이 불규칙하다(중앙 간격은 --gap-report로 출력).
  - 후보 스냅샷이 존재한다는 것 자체가 그 종목이 이미 스크리너를 통과했다는 뜻이다.
    따라서 이 시뮬은 "후보 풀 안에서의 진입·청산 규칙" 비교용이며,
    풀 자체의 선별력을 재는 도구가 아니다.

검증 (--validate)
  같은 (세션·시장·종목)의 실제 라이브 체결(v2_canonical_performance)과 대조해
  재현 진입가·net이 실제와 얼마나 맞는지 출력한다. 이 대조를 통과하지 못하면
  아래 반사실 수치는 신뢰하지 않는다.
"""
from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FUNNEL_GLOB = "logs/funnel/post_open_features_*.jsonl"
DECISIONS_DB = ROOT / "data" / "ml" / "decisions.db"

COST_PCT = {"KR": 0.21, "US": 0.50}

# 시장별 장 초반 soft gate 경계 (CLAUDE.md 운영 파라미터와 동일)
SOFT_GATE_MIN = {"KR": 60, "US": 30}


# ── 로딩 ────────────────────────────────────────────────────────────────────
def _file_in_range(path: str, since: str, until: str) -> bool:
    """파일명 날짜로 1차 필터. 매일 도는 스케줄 작업이 전체 로그를 재스캔하지 않게 한다.

    파일명은 post_open_features_YYYYMMDD_MKT.jsonl 이지만, US는 파일 날짜와
    market_session_date가 하루 어긋난다(7/24 세션이 20260725_US 파일에 들어간다).
    그래서 앞뒤로 하루씩 여유를 두고 자른다 — 정확한 판정은 행 단위 필터가 한다.
    """
    name = Path(path).name
    parts = name.split("_")
    if len(parts) < 4:
        return True  # 예상 밖 이름은 버리지 않고 통과시켜 행 필터에 맡긴다
    raw = parts[3]
    if len(raw) != 8 or not raw.isdigit():
        return True
    stamp = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    try:
        day = date.fromisoformat(stamp)
        lo = date.fromisoformat(since) - timedelta(days=1)
        hi = date.fromisoformat(until) + timedelta(days=1)
    except ValueError:
        return True
    return lo <= day <= hi


def load_series(market: str | None, since: str, until: str) -> dict[tuple, list[dict]]:
    """(session_date, market, ticker) -> elapsed 오름차순 스냅샷 리스트."""
    series: dict[tuple, list[dict]] = defaultdict(list)
    paths = [p for p in sorted(glob.glob(str(ROOT / FUNNEL_GLOB)))
             if _file_in_range(p, since, until)]
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                mkt = row.get("market")
                if market and mkt != market:
                    continue
                date = row.get("market_session_date") or row.get("session_date")
                tick = row.get("ticker")
                price = row.get("current_price")
                elapsed = row.get("market_open_elapsed_min")
                if not (date and tick and mkt) or price is None or elapsed is None:
                    continue
                if not (since <= str(date) <= until):
                    continue
                try:
                    price = float(price)
                    elapsed = float(elapsed)
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                series[(str(date), mkt, tick)].append(
                    {
                        "elapsed": elapsed,
                        "price": price,
                        "pullback": row.get("pullback_from_high_pct"),
                        "from_open_high": row.get("from_open_high_pct"),
                        "momentum": row.get("momentum_state"),
                        "or_break": row.get("opening_range_break"),
                        "or_high": row.get("opening_range_high"),
                        "or_low": row.get("opening_range_low"),
                        "vol_ratio_open": row.get("volume_ratio_open"),
                        "rvol": row.get("time_normalized_rvol"),
                        "vwap_dist": row.get("vwap_distance_pct"),
                        "known_at": row.get("known_at"),
                    }
                )
    for key in series:
        series[key].sort(key=lambda r: r["elapsed"])
    return series


def running_high(snap: dict, use_intrabar: bool) -> float:
    """스냅샷 시점의 러닝 고가. use_intrabar=False면 표본가격만 사용(보수)."""
    price = snap["price"]
    if not use_intrabar:
        return price
    pb = snap.get("pullback")
    if pb is None:
        return price
    try:
        high = price * (1.0 - float(pb) / 100.0)
    except (TypeError, ValueError):
        return price
    # pullback 부호 오염 방어: 러닝 고가는 현재가 이상이어야 한다
    return high if high >= price else price


# ── 진입 게이트 ─────────────────────────────────────────────────────────────
def entry_snapshot(seq: list[dict], cfg: dict) -> dict | None:
    """게이트를 처음 만족하는 스냅샷을 진입 시점으로 반환. 없으면 None."""
    lo = cfg["entry_from_min"]
    hi = cfg["entry_to_min"]
    for snap in seq:
        if snap["elapsed"] < lo or snap["elapsed"] > hi:
            continue
        if cfg["require_or_break"] and not snap.get("or_break"):
            continue
        mom = cfg.get("momentum")
        if mom and str(snap.get("momentum") or "") not in mom:
            continue
        minrv = cfg.get("min_vol_ratio_open")
        if minrv is not None:
            raw = snap.get("vol_ratio_open")
            if raw is None:
                continue
            try:
                if float(raw) < minrv:
                    continue
            except (TypeError, ValueError):
                continue
        maxpb = cfg.get("max_pullback_pct")
        if maxpb is not None:
            pb = snap.get("pullback")
            if pb is None:
                continue
            try:
                # pullback은 음수(고점 아래). 고점에서 maxpb%보다 더 빠졌으면 제외
                if float(pb) < -abs(maxpb):
                    continue
            except (TypeError, ValueError):
                continue
        return snap
    return None


# ── 청산 시뮬 ───────────────────────────────────────────────────────────────
def simulate_trade(seq: list[dict], entry: dict, cfg: dict, market: str) -> dict | None:
    """진입 스냅샷 이후 경로를 걸으며 target/stop/마감 청산을 판정.

    ⚠️ 룩어헤드 방지 (2026-07-26 수정):
      running_high()가 복원하는 값은 **세션 누적 고가**다. 그대로 익절 판정에 쓰면
      고점에서 밀린 종목(momentum=fade)을 살 때 '진입 이전에 찍힌 고가'로 즉시
      익절 체결되어, US fade가 승률 94.9%·보유 2분으로 나오는 가짜 알파가 생긴다.
      진입 후 고가만 쓰려면:
        - 진입 이후 스냅샷의 표본가격 최대치는 무조건 진입 후 값이다(안전).
        - 세션 누적 고가가 진입시점 누적 고가보다 **커졌다면** 그 증가분은 진입 후에
          발생한 것이므로 그때만 누적 고가를 쓴다.
      두 값의 max가 진입 후 도달 고가의 타당한 추정이다.
    """
    entry_px = entry["price"]
    target = cfg["target_pct"]
    stop = cfg["stop_pct"]
    use_intrabar = cfg["use_intrabar_high"]
    rest = [s for s in seq if s["elapsed"] > entry["elapsed"]]
    if not rest:
        return None

    entry_session_high = running_high(entry, use_intrabar)
    peak = entry_px
    for snap in rest:
        session_high = running_high(snap, use_intrabar)
        # 진입 후 고가: 표본가는 항상 유효, 누적 고가는 진입시점보다 커졌을 때만 유효
        high = snap["price"]
        if use_intrabar and session_high > entry_session_high:
            high = max(high, session_high)
        peak = max(peak, high)
        gain_high = (high / entry_px - 1.0) * 100.0
        gain_now = (snap["price"] / entry_px - 1.0) * 100.0

        hit_t = target is not None and gain_high >= target
        hit_s = stop is not None and gain_now <= -stop
        if hit_t and hit_s:
            # 같은 스냅샷 구간에서 둘 다 도달 — 순서 미상이므로 비관(손절 먼저)
            gross = -stop
            reason = "stop_ambiguous"
        elif hit_t:
            gross = target
            reason = "target"
        elif hit_s:
            gross = gain_now
            reason = "stop"
        else:
            continue
        return {
            "gross": gross,
            "net": gross - COST_PCT[market],
            "reason": reason,
            "hold_min": snap["elapsed"] - entry["elapsed"],
            "mfe": (peak / entry_px - 1.0) * 100.0,
        }

    last = rest[-1]
    gross = (last["price"] / entry_px - 1.0) * 100.0
    return {
        "gross": gross,
        "net": gross - COST_PCT[market],
        "reason": "session_close",
        "hold_min": last["elapsed"] - entry["elapsed"],
        "mfe": (peak / entry_px - 1.0) * 100.0,
    }


def run_sim(series: dict[tuple, list[dict]], cfg: dict) -> list[dict]:
    out: list[dict] = []
    for (date, market, ticker), seq in series.items():
        if len(seq) < cfg["min_snapshots"]:
            continue
        entry = entry_snapshot(seq, cfg)
        if entry is None:
            continue
        res = simulate_trade(seq, entry, cfg, market)
        if res is None:
            continue
        res.update(
            {
                "date": date,
                "market": market,
                "ticker": ticker,
                "entry_price": entry["price"],
                "entry_elapsed": entry["elapsed"],
            }
        )
        out.append(res)
    return out


# ── 집계 ────────────────────────────────────────────────────────────────────
def summarize(trades: list[dict]) -> dict | None:
    if not trades:
        return None
    nets = [t["net"] for t in trades]
    n = len(nets)
    return {
        "n": n,
        "avg": sum(nets) / n,
        "median": statistics.median(nets),
        "sum": sum(nets),
        "win": sum(1 for v in nets if v > 0) / n * 100,
        "hold": statistics.median([t["hold_min"] for t in trades]),
    }


def fmt(s: dict | None) -> str:
    if not s:
        return "n=0"
    return (f"n={s['n']:5d} avg={s['avg']:+7.3f}% med={s['median']:+7.3f}% "
            f"sum={s['sum']:+9.1f} win={s['win']:5.1f}% hold={s['hold']:5.0f}m")


def reason_mix(trades: list[dict]) -> str:
    mix: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        mix[t["reason"]].append(t["net"])
    parts = []
    for reason in sorted(mix, key=lambda r: -len(mix[r])):
        vals = mix[reason]
        parts.append(f"{reason} {len(vals)}건 avg{sum(vals)/len(vals):+.2f}%")
    return " | ".join(parts)


# ── 검증: 실제 라이브 체결과 대조 ────────────────────────────────────────────
def validate_against_live(trades: list[dict], db: Path) -> None:
    print("\n" + "=" * 78)
    print("[검증] 재현 결과 vs 실제 라이브 체결 (v2_canonical_performance)")
    print("=" * 78)
    if not db.exists():
        print("  DB 없음 — 검증 불가")
        return
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=10000")
    con.row_factory = sqlite3.Row
    live = {}
    for row in con.execute(
        """SELECT session_date, market, ticker, entry_price, pnl_pct_net, closed
           FROM v2_canonical_performance
           WHERE runtime_mode='live' AND filled=1 AND session_date>='2026-06-01'"""
    ):
        live[(str(row["session_date"]), row["market"], row["ticker"])] = dict(row)
    con.close()
    print(f"  라이브 체결 원장 {len(live)}건 (2026-06-01~)")

    sim_by_key = {(t["date"], t["market"], t["ticker"]): t for t in trades}
    overlap = sorted(set(sim_by_key) & set(live))
    print(f"  같은 (세션·시장·종목) 교집합: {len(overlap)}건")
    if not overlap:
        print("  → 교집합 0. 이 게이트 설정은 실제 체결 종목을 재현하지 못한다.")
        return

    print(f"\n  {'세션':11s}{'시장':4s}{'종목':9s}{'실체결가':>11s}{'재현진입가':>11s}"
          f"{'가격오차%':>10s}{'실net%':>9s}{'재현net%':>9s}")
    px_err, net_err = [], []
    for key in overlap:
        sim = sim_by_key[key]
        act = live[key]
        ap = act["entry_price"]
        if ap:
            err = (sim["entry_price"] / float(ap) - 1.0) * 100.0
            px_err.append(abs(err))
        else:
            err = float("nan")
        an = act["pnl_pct_net"]
        if an is not None and act["closed"]:
            net_err.append(sim["net"] - float(an))
        print(f"  {key[0]:11s}{key[1]:4s}{str(key[2])[:9]:9s}"
              f"{(float(ap) if ap else 0):11.4f}{sim['entry_price']:11.4f}{err:10.3f}"
              f"{(float(an) if an is not None else 0):9.3f}{sim['net']:9.3f}")
    if px_err:
        print(f"\n  진입가 절대오차: 중앙 {statistics.median(px_err):.3f}% / "
              f"최대 {max(px_err):.3f}%  (n={len(px_err)})")
    if net_err:
        print(f"  net 차이(재현−실제): 중앙 {statistics.median(net_err):+.3f}%p / "
              f"평균 {sum(net_err)/len(net_err):+.3f}%p  (n={len(net_err)})")
        print("  ※ 진입 시점 규칙이 실제 봇과 다르므로 net 차이는 당연히 생긴다.")
        print("    여기서 볼 것은 '가격 경로 복원이 맞는가'(진입가 오차)이지 net 일치가 아니다.")


def report_gaps(series: dict[tuple, list[dict]]) -> None:
    print("\n" + "=" * 78)
    print("[0] 스냅샷 밀도 — 손절 판정 해상도")
    print("=" * 78)
    gaps: list[float] = []
    for seq in series.values():
        for a, b in zip(seq, seq[1:]):
            gaps.append(b["elapsed"] - a["elapsed"])
    if not gaps:
        print("  스냅샷 없음")
        return
    gaps.sort()
    n = len(gaps)
    print(f"  구간 {n:,}개 · 간격(분) 중앙 {gaps[n//2]:.1f} / p75 {gaps[3*n//4]:.1f} / "
          f"p90 {gaps[int(n*0.9)]:.1f} / 최대 {gaps[-1]:.0f}")
    print("  ★간격이 클수록 손절 미검출(낙관 편향)이 커진다. 이 값이 결과 해석의 상한이다.")


def main() -> int:
    ap = argparse.ArgumentParser(description="진입 파이프라인 장중 오프라인 재현 (read-only)")
    ap.add_argument("--market", default="", help="KR|US (빈값=둘 다)")
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--until", default="2026-12-31")
    ap.add_argument("--entry-from", type=float, default=0.0)
    ap.add_argument("--entry-to", type=float, default=75.0)
    ap.add_argument("--target", type=float, default=3.0)
    ap.add_argument("--stop", type=float, default=2.0)
    ap.add_argument("--momentum", default="", help="쉼표구분 momentum_state 화이트리스트")
    ap.add_argument("--require-or-break", action="store_true")
    ap.add_argument("--min-vol-ratio-open", type=float, default=None)
    ap.add_argument("--max-pullback", type=float, default=None)
    ap.add_argument("--min-snapshots", type=int, default=3)
    ap.add_argument("--no-intrabar-high", action="store_true",
                    help="봉내 고점 복원을 끄고 표본가격만 사용(보수 대조군)")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--gap-report", action="store_true")
    args = ap.parse_args()

    cfg = {
        "entry_from_min": args.entry_from,
        "entry_to_min": args.entry_to,
        "target_pct": args.target,
        "stop_pct": args.stop,
        "momentum": {m.strip() for m in args.momentum.split(",") if m.strip()},
        "require_or_break": bool(args.require_or_break),
        "min_vol_ratio_open": args.min_vol_ratio_open,
        "max_pullback_pct": args.max_pullback,
        "min_snapshots": args.min_snapshots,
        "use_intrabar_high": not args.no_intrabar_high,
    }

    market = args.market.upper().strip() or None
    series = load_series(market, args.since, args.until)
    if not series:
        print("스냅샷 없음")
        return 1
    sessions = len({k[0] for k in series})
    print(f"재현 대상: {market or 'KR+US'} {args.since}~{args.until} "
          f"(read-only, API 미사용)")
    print(f"  (세션·시장·종목) {len(series):,}조합 / {sessions}세션 / "
          f"스냅샷 {sum(len(v) for v in series.values()):,}건")

    if args.gap_report:
        report_gaps(series)

    trades = run_sim(series, cfg)
    print("\n" + "=" * 78)
    print(f"[1] 진입 규칙: {args.entry_from:.0f}~{args.entry_to:.0f}분 "
          f"target {args.target}% / stop {args.stop}%"
          + (f" / momentum={sorted(cfg['momentum'])}" if cfg["momentum"] else "")
          + (" / OR돌파필수" if cfg["require_or_break"] else "")
          + (f" / vol_ratio_open>={args.min_vol_ratio_open}"
             if args.min_vol_ratio_open is not None else "")
          + (f" / 고점대비낙폭<={args.max_pullback}%"
             if args.max_pullback is not None else ""))
    print("=" * 78)
    print(f"  진입 {len(trades)}건 / 후보 {len(series)}조합 "
          f"(진입률 {len(trades)/len(series)*100:.1f}%)")
    print(f"  전체   {fmt(summarize(trades))}")
    for mkt in ("KR", "US"):
        sub = [t for t in trades if t["market"] == mkt]
        if sub:
            print(f"  {mkt}     {fmt(summarize(sub))}")
            print(f"         청산사유: {reason_mix(sub)}")

    if args.validate:
        validate_against_live(trades, DECISIONS_DB)

    print("\n※ 손절은 스냅샷 표본가로만 판정 → net은 낙관 편향. "
          "익절은 봉내 고점 복원을 쓰므로 비대칭(--no-intrabar-high로 대조).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
