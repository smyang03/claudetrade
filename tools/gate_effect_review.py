from __future__ import annotations

"""2026-07-22 적용 레버(국면 진입게이트 enforce·일일 진입 상한)의 라이브 효과 점검.

적용 근거는 과거 표본 시뮬이었다. 라이브에서 같은 방향인지, 그리고 게이트가 과하게 막아
진입이 죽지 않는지를 한 번에 본다. 진입이 0이면 즉시 롤백해야 하므로 그 판정을 먼저 낸다.

  python tools/gate_effect_review.py                # 오늘 세션
  python tools/gate_effect_review.py --since 2026-07-22
"""

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
LOG_DIR = ROOT / "logs" / "system"

# 적용값(2026-07-22). 실제 판정은 effective_config로 확인한다.
APPLIED = {
    "US": {"block": {"CAUTIOUS", "MILD_BEAR"}, "daily_cap": 5},
    "KR": {"block": {"CAUTIOUS", "MILD_BULL"}, "daily_cap": 40},
}


def _effective_config() -> dict:
    files = sorted(
        (ROOT / "logs" / "config").glob("effective_config_*live*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return {}
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return {}
    eff = data.get("effective") if isinstance(data, dict) else None
    return {"_file": files[0].name, **(eff if isinstance(eff, dict) else {})}


def _gate_log_counts(since: str) -> dict:
    """봇 로그에서 게이트/상한 차단 발동을 센다(원장이 없어도 즉시 확인 가능)."""
    counts: Counter = Counter()
    for path in sorted(LOG_DIR.glob("live_trading_*.log")):
        stamp = re.search(r"(\d{8})", path.name)
        if stamp and stamp.group(1) < since.replace("-", ""):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        counts["regime_gate_block"] += len(re.findall(r"REGIME_ENTRY_GATE|regime_entry_gate", text))
        counts["daily_cap_block"] += len(re.findall(r"PATHB_MAX_DAILY_ENTRIES", text))
        counts["midday_block"] += len(re.findall(r"US_MIDDAY_ENTRY_BLOCK", text))
        counts["early_peak_shadow"] += len(re.findall(r"조기고점 shadow", text))
        counts["buy_ready_immediate"] += len(re.findall(r"BUY_READY 즉시", text))
        counts["entry_scan"] += len(re.findall(r"엔트리 스캔", text))
    return dict(counts)


def _live_entries(since: str) -> dict:
    if not ML_DB.exists():
        return {}
    con = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True, timeout=20)
    try:
        con.execute("PRAGMA busy_timeout=20000")
        rows = con.execute(
            """SELECT p.market, p.session_date, p.pnl_pct_net, p.closed, l.market_regime
               FROM v2_canonical_performance p
               LEFT JOIN v2_learning_performance l ON l.v2_decision_id = p.v2_decision_id
               WHERE p.session_date >= ?""",
            (since,),
        ).fetchall()
    finally:
        con.close()
    by_market: dict = defaultdict(lambda: {"entries": 0, "closed": 0, "net": 0.0, "regimes": Counter()})
    for market, _sd, net, closed, regime in rows:
        slot = by_market[str(market or "?")]
        slot["entries"] += 1
        if closed:
            slot["closed"] += 1
        if net is not None:
            slot["net"] += float(net)
        if regime:
            slot["regimes"][str(regime)] += 1
    return by_market


def main() -> int:
    ap = argparse.ArgumentParser(description="적용 레버의 라이브 효과 점검")
    ap.add_argument("--since", default=date.today().isoformat())
    args = ap.parse_args()

    eff = _effective_config()
    print(f"=== effective config: {eff.get('_file', '(없음)')} ===")
    for key in (
        "REGIME_ENTRY_GATE_MODE",
        "US_REGIME_ENTRY_GATE_BLOCK_MODES",
        "KR_REGIME_ENTRY_GATE_BLOCK_MODES",
        "US_DAILY_ENTRY_CAP",
        "KR_DAILY_ENTRY_CAP",
        "US_MIDDAY_ENTRY_BLOCK_UTC_HOUR",
        "EARLY_PEAK_EXIT_SHADOW_MODE",
        "SINGLE_SYMBOL_JUDGE_ALLOW_BUY_READY",
    ):
        print(f"  {key:36s} = {eff.get(key, '(미설정)')}")

    print(f"\n=== 로그 발동 집계 (since {args.since}) ===")
    counts = _gate_log_counts(args.since)
    for key in ("entry_scan", "regime_gate_block", "daily_cap_block", "midday_block",
                "buy_ready_immediate", "early_peak_shadow"):
        print(f"  {key:22s} {counts.get(key, 0)}")

    print(f"\n=== 진입/성과 (since {args.since}) ===")
    live = _live_entries(args.since)
    if not live:
        print("  기록 없음")
    for market, slot in sorted(live.items()):
        cap = APPLIED.get(market, {}).get("daily_cap", "-")
        print(f"  [{market}] 진입 {slot['entries']}건  청산 {slot['closed']}건  "
              f"net {slot['net']:+.2f}%  (일일상한 {cap})")
        if slot["regimes"]:
            print(f"        국면: {dict(slot['regimes'])}")

    print("\n=== 판정 ===")
    total_entries = sum(s["entries"] for s in live.values()) if live else 0
    scans = counts.get("entry_scan", 0)
    gate_blocks = counts.get("regime_gate_block", 0) + counts.get("daily_cap_block", 0)
    if scans == 0:
        print("  ⚠ 엔트리 스캔이 0 — 세션이 아직 열리지 않았거나 봇이 멈춰 있다.")
    elif total_entries > 0:
        print(f"  ✅ 진입 {total_entries}건 발생 — 게이트가 전량 차단하지는 않았다.")
        print("     다음 확인: 청산이 쌓이면 net 방향이 시뮬(US +5.09%/+28.73%)과 같은지 본다.")
    elif gate_blocks > 0:
        # 게이트가 실제로 막은 흔적이 있을 때만 과차단을 의심한다.
        print(f"  🚨 진입 0 + 게이트 차단 {gate_blocks}회 — 과차단일 수 있다. 확인 후 롤백:")
        print("     REGIME_ENTRY_GATE_MODE=shadow  또는  US_DAILY_ENTRY_CAP=40")
    else:
        # 차단 흔적이 없으면 원인은 게이트가 아니다. 여기서 오진해 롤백하면 레버만 잃는다.
        print("  ℹ 진입 0이지만 게이트 차단 기록도 0 — 원인은 게이트가 아니다.")
        print("     상류를 본다: judge 예산 소진(early judge capacity exhausted) / 신호 미발생 /")
        print("     후보 부재 / 세션 시간대. 이 경우 롤백은 불필요하다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
