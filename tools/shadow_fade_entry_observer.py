#!/usr/bin/env python3
"""fade(눌림) 진입 신호 shadow 관측기 — 라이브 미개입, forward 누적용.

배경 (실측 2026-07-26, 구간 2026-07-13~24 / 10세션)
  장중 스냅샷 재현(tools/sim_entry_pipeline_offline.py)에서 진입창·target/stop 기하·
  진입필터를 전수 스윕한 결과 **거의 모든 셀이 음수**였고, 유일한 양수 셀이
  momentum_state == "fade" 진입이었다. 3중 검증을 통과했다:
    세션별       KR 8세션 중 6개 양수 / US 10세션 중 7개 양수
    최대기여 제외 KR +0.332% / US +0.503% (여전히 양수)
    플라시보 대조 동일 세션·±10분 동일 시간대 비-fade 대비
                 KR +0.687%p (p=0.0146) / US +1.374%p (p=0.0004)
  양쪽 arm이 동일한 손절 샘플링 편향을 공유하므로 편향으로 설명되지 않는다.

⚠️ 왜 shadow인가
  이 신호는 "눌림에서 사라"는 뜻이고, 현재 라이브 정체성(BUY_READY 즉시매수,
  눌림 폐기 — 2026-07-21~25 enforce)과 정면 충돌한다. 표본은 10세션뿐이다.
  짧은 구간 결과로 전략 방향을 뒤집지 않는다 — forward로 누적 검증한 뒤
  **운영자가** enforce 여부를 판단한다. 이 스크립트는 주문·플랜·게이트에 일절
  개입하지 않으며 shadow 원장만 append 한다.

무엇을 기록하는가
  신호만 기록하면 나중에 다시 편향 논쟁이 된다. 그래서 매 관측마다
  **fade arm과 대조군 arm을 쌍으로** 기록한다(동일 세션·±window 동일 시간대 비-fade).
  이렇게 해야 forward 표본이 쌓였을 때 격차를 그대로 재검정할 수 있다.

출력
  data/shadow/fade_entry_shadow_<MARKET>.jsonl  (append, key 기준 멱등)

사용
  python tools/shadow_fade_entry_observer.py --since 2026-07-13          # 소급 백필
  python tools/shadow_fade_entry_observer.py --session 2026-07-24        # 특정 세션만
  python tools/shadow_fade_entry_observer.py --since 2026-07-13 --dry-run
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import sim_entry_pipeline_offline as sim  # noqa: E402

SHADOW_DIR = ROOT / "data" / "shadow"
SCHEMA_VERSION = 1
CONTROL_WINDOW_MIN = 10.0   # 대조군 진입 시각 허용 오차
SEED = 20260726             # 대조군 추출 재현성


def build_cfg(args) -> dict:
    return {
        "entry_from_min": args.entry_from,
        "entry_to_min": args.entry_to,
        "target_pct": args.target,
        "stop_pct": args.stop,
        "momentum": set(),
        "require_or_break": False,
        "min_vol_ratio_open": None,
        "max_pullback_pct": None,
        "min_snapshots": args.min_snapshots,
        "use_intrabar_high": True,
    }


def first_in_window(seq: list[dict], cfg: dict, state: str | None) -> dict | None:
    lo, hi = cfg["entry_from_min"], cfg["entry_to_min"]
    for snap in seq:
        if snap["elapsed"] < lo or snap["elapsed"] > hi:
            continue
        if state is not None and str(snap.get("momentum") or "") != state:
            continue
        return snap
    return None


def observe(series: dict[tuple, list[dict]], cfg: dict) -> list[dict]:
    """fade 진입과 짝지은 대조군을 함께 산출."""
    rng = random.Random(SEED)
    # 세션·시장별 대조군 후보(비-fade 진입 가능 스냅샷)
    pool: dict[tuple, list[tuple]] = defaultdict(list)
    fade_keys: set[tuple] = set()
    for key, seq in series.items():
        if len(seq) < cfg["min_snapshots"]:
            continue
        if first_in_window(seq, cfg, "fade") is not None:
            fade_keys.add(key)
    for key, seq in series.items():
        if len(seq) < cfg["min_snapshots"] or key in fade_keys:
            continue
        date, market, _ticker = key
        for snap in seq:
            if cfg["entry_from_min"] <= snap["elapsed"] <= cfg["entry_to_min"]:
                pool[(date, market)].append((key, snap))

    out: list[dict] = []
    for key in sorted(fade_keys):
        date, market, ticker = key
        seq = series[key]
        entry = first_in_window(seq, cfg, "fade")
        if entry is None:
            continue
        res = sim.simulate_trade(seq, entry, cfg, market)
        if res is None:
            continue

        control = None
        cands = [
            x for x in pool.get((date, market), [])
            if abs(x[1]["elapsed"] - entry["elapsed"]) <= CONTROL_WINDOW_MIN
        ]
        if cands:
            ckey, csnap = rng.choice(cands)
            cres = sim.simulate_trade(series[ckey], csnap, cfg, market)
            if cres is not None:
                control = {
                    "ticker": ckey[2],
                    "entry_price": csnap["price"],
                    "entry_elapsed": round(csnap["elapsed"], 2),
                    "momentum": csnap.get("momentum"),
                    "net": round(cres["net"], 4),
                    "gross": round(cres["gross"], 4),
                    "reason": cres["reason"],
                    "hold_min": round(cres["hold_min"], 1),
                }

        out.append({
            "schema_version": SCHEMA_VERSION,
            "key": f"{date}|{market}|{ticker}",
            "session_date": date,
            "market": market,
            "ticker": ticker,
            "mode": "shadow",
            "signal": "fade_entry",
            "config": {
                "entry_from_min": cfg["entry_from_min"],
                "entry_to_min": cfg["entry_to_min"],
                "target_pct": cfg["target_pct"],
                "stop_pct": cfg["stop_pct"],
                "control_window_min": CONTROL_WINDOW_MIN,
            },
            "entry": {
                "price": entry["price"],
                "elapsed_min": round(entry["elapsed"], 2),
                "known_at": entry.get("known_at"),
                "pullback_from_high_pct": entry.get("pullback"),
                "vol_ratio_open": entry.get("vol_ratio_open"),
                "vwap_distance_pct": entry.get("vwap_dist"),
            },
            "outcome": {
                "net": round(res["net"], 4),
                "gross": round(res["gross"], 4),
                "reason": res["reason"],
                "hold_min": round(res["hold_min"], 1),
                "mfe_pct": round(res["mfe"], 4),
            },
            "control": control,
            "bias_note": (
                "손절은 스냅샷 표본가(중앙 5.1분 간격)로만 판정 → 손절 빈도 과소. "
                "대조군이 동일 편향을 공유하므로 격차만 읽을 것."
            ),
        })
    return out


def existing_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                keys.add(str(json.loads(line).get("key")))
            except Exception:
                continue
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(description="fade 진입 shadow 관측기 (라이브 미개입)")
    ap.add_argument("--since", default="2026-07-13",
                    help="market_open_elapsed_min 도입 이후만 유효")
    ap.add_argument("--until", default="2026-12-31")
    ap.add_argument("--session", default="", help="특정 세션만 (YYYY-MM-DD)")
    ap.add_argument("--market", default="", help="KR|US (빈값=둘 다)")
    ap.add_argument("--entry-from", type=float, default=0.0)
    ap.add_argument("--entry-to", type=float, default=75.0)
    ap.add_argument("--target", type=float, default=3.0)
    ap.add_argument("--stop", type=float, default=2.0)
    ap.add_argument("--min-snapshots", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    since = args.session or args.since
    until = args.session or args.until
    market = args.market.upper().strip() or None

    series = sim.load_series(market, since, until)
    if not series:
        print(f"스냅샷 없음 ({since}~{until})")
        return 1
    cfg = build_cfg(args)
    records = observe(series, cfg)
    sessions = sorted({r["session_date"] for r in records})
    print(f"fade shadow 관측 — {since}~{until} (라이브 미개입)")
    print(f"  스냅샷 조합 {len(series):,} → fade 신호 {len(records)}건 / "
          f"{len(sessions)}세션")
    paired = [r for r in records if r.get("control")]
    print(f"  대조군 짝지어진 건 {len(paired)}건")

    if records:
        for mkt in ("KR", "US"):
            sub = [r for r in records if r["market"] == mkt and r.get("control")]
            if not sub:
                continue
            fa = [r["outcome"]["net"] for r in sub]
            ca = [r["control"]["net"] for r in sub]
            gap = sum(fa) / len(fa) - sum(ca) / len(ca)
            print(f"  {mkt}: fade {sum(fa)/len(fa):+.3f}% vs 대조 {sum(ca)/len(ca):+.3f}% "
                  f"→ 격차 {gap:+.3f}%p (n={len(sub)})")

    if args.dry_run:
        print("\n[dry-run] 기록하지 않음. 샘플 1건:")
        if records:
            print(json.dumps(records[0], ensure_ascii=False, indent=2)[:900])
        return 0

    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for mkt in sorted({r["market"] for r in records}):
        path = SHADOW_DIR / f"fade_entry_shadow_{mkt}.jsonl"
        have = existing_keys(path)
        fresh = [r for r in records if r["market"] == mkt and r["key"] not in have]
        if not fresh:
            print(f"  {path.name}: 신규 0건 (이미 기록됨)")
            continue
        with open(path, "a", encoding="utf-8") as fh:
            for rec in fresh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        written += len(fresh)
        print(f"  {path.name}: +{len(fresh)}건 (누적 {len(have) + len(fresh)}건)")
    print(f"\n총 {written}건 기록. 라이브 주문·플랜·게이트에는 개입하지 않았다.")
    print("※ enforce 전환은 forward 표본 누적 후 운영자 판단 사항이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
