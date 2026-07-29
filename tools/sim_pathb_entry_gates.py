"""PathB 진입 경로 게이트 시뮬 — register → scan_waiting_entries를 실제로 태운다.

배경 (2026-07-29):
  진입 경로 시뮬(tools/sim_entry_path_gates.py)은 Path A만 봤고 `bot.pathb = None`으로
  PathB를 통째로 껐다. PathB는 KR·US 둘 다 live인 주력 경로인데 0% 검증 상태였다.
  정적 스캔으로는 PathB-등록 14개 중 8개, 진입스캔 27개 중 16개가 '무흔적'으로 잡혔으나
  정적 판정은 오탐이 많다(플래그 세팅과 continue가 떨어져 있음). 그래서 실제로 태운다.

방법
  - PathBRuntime(bot, is_paper=False, store=EventStore(tmp))로 실인스턴스를 만든다.
  - 시나리오별로 selection_meta(가격플랜)를 넣어 register_from_selection_meta를 호출하고,
    이어서 scan_waiting_entries를 돌려 진입 판정까지 태운다.
  - 로그를 레벨 무관하게 캡처해 어느 게이트에서 멈췄는지 본다.

한계
  - 브로커/주문은 mock이다. "등록됨/진입판정 도달"이 라이브 체결을 보장하지 않는다.
  - 목적은 어느 상황에서 PathB 후보가 조용히 사라지는지의 지도다.
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.event_store import EventStore  # noqa: E402
import runtime.pathb_runtime as pathb_mod  # noqa: E402
from runtime.pathb_runtime import PathBControlState, PathBRuntime  # noqa: E402


class LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append((record.levelname, record.getMessage()))
        except Exception:
            pass

    def find(self, needle: str) -> str:
        for _l, m in self.records:
            if needle in m:
                return m
        return ""


class _SimBot:
    """PathBRuntime이 참조하는 최소 봇 표면."""

    def __init__(self, market: str, mode: str, price: float) -> None:
        self.current_market = market
        self.is_paper = False
        self.token = "token"
        self.price_cache = {}
        self.pending_orders: list[dict] = []
        self.blocked_entries: list[tuple] = []
        self.decision_events: list[dict] = []
        self.today_judgment = {"market": market, "consensus": {"mode": mode, "size": 50}}
        self.runtime_config = {}
        self.risk = SimpleNamespace(positions=[], cash=10_000_000.0, max_order_krw=500_000.0)
        self._price = price
        self._mode = mode
        self.with_decision_id = True

    # --- 봇 표면 ---
    def _token_for_market(self, market: str, *, force_refresh: bool = False) -> str:
        return "token"

    def _market_mode(self, market: str) -> str:
        return self._mode

    def _price_to_krw(self, price: float, market: str) -> float:
        return float(price)

    def _add_pending_order(self, order: dict) -> None:
        self.pending_orders.append(dict(order))

    def _block_entry(self, ticker: str, minutes: int, reason: str) -> None:
        self.blocked_entries.append((ticker, minutes, reason))

    def _record_decision_event(self, market: str, action: str, ticker: str, **kw) -> None:
        self.decision_events.append({"market": market, "action": action, "ticker": ticker})

    def _market_realized_daily_return_pct(self, market: str) -> float:
        return 0.0

    def _market_daily_return_pct(self, market: str) -> float:
        return 0.0

    def _daily_pnl_pct(self, market: str) -> float:
        return 0.0

    def _has_open_position(self, ticker: str, market: str) -> bool:
        return False

    def _has_pending_order(self, ticker: str, market: str) -> bool:
        return False

    def _v2_decision_id_for_ticker(self, market: str, ticker: str) -> str:
        # decision_id가 없으면 PathB 등록이 로그 없이 continue로 탈락한다
        # (runtime/pathb_runtime.py `_decision_id_for` → `if not decision_id: continue`).
        # with_decision_id=False 시나리오로 그 경로를 재현한다.
        return f'sim-decision-{market}-{ticker}' if self.with_decision_id else ''

    def _v2_record_lifecycle_event(self, *a, **kw) -> None:
        return None


class _Control:
    def load(self):
        return PathBControlState(enabled=True, emergency_disabled=False)


GATES = [
    ("plan registered", "등록됨"),
    ("요일게이트", "weekday_gate"),
    ("zone", "zone"),
    ("cancel_if_open_above", "cancel_open_above"),
    ("risk_off", "risk_off_cap"),
    ("shadow", "shadow_mode"),
    ("not enabled", "disabled"),
    ("blocked", "blocked"),
]


def run_case(*, market: str, mode: str, price: float, zone_low: float, zone_high: float,
             live_enabled: bool = True, with_decision_id: bool = True):
    cap = LogCapture()
    root = logging.getLogger()
    prev_level = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(cap)
    pathb_mod.log.setLevel(logging.DEBUG)
    pathb_mod.log.addHandler(cap)
    registered, err = [], ""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _SimBot(market, mode, price)
            bot.with_decision_id = bool(with_decision_id)
            store = EventStore(Path(tmp) / "events.db")
            rt = PathBRuntime(bot, is_paper=False, store=store)
            rt.control_store = _Control()
            # 브로커 truth mock — 없으면 진입 스캔이 BLOCKED_BROKER_TRUTH로 시장 전체 차단된다
            # (그 자체는 WARNING으로 잘 기록되는 안전 설계다. 더 안쪽 게이트를 보려면 통과시킨다.)
            rt.broker_truth = SimpleNamespace(
                market_snapshot=lambda market, **kw: {
                    'ok': True, 'stale': False, 'missing': False,
                    'positions': [], 'open_orders': [], 'today_fills': [],
                    'account_summary': {'cash': 10_000_000.0, 'orderable_cash': 10_000_000.0},
                    'last_success_at': '2026-07-29T06:30:00+00:00',
                },
                refresh_market=lambda market, **kw: True,
            )
            meta = {
                "trade_ready": ["SIMTK"],
                "price_targets": {
                    "SIMTK": {
                        "buy_zone_low": zone_low,
                        "buy_zone_high": zone_high,
                        "sell_target": price * 1.05,
                        "stop_loss": price * 0.98,
                        "hold_days": 2,
                        "confidence": 0.7,
                    }
                },
                "actions": {"SIMTK": {"action": "PULLBACK_WAIT", "route": "PathB.wait"}},
            }
            try:
                registered = rt.register_from_selection_meta(market, meta) or []
            except Exception as exc:
                err = f"register: {type(exc).__name__}: {exc}"
            if not err:
                try:
                    rt.scan_waiting_entries(market, force=True)
                except Exception as exc:
                    err = f"scan: {type(exc).__name__}: {exc}"
    finally:
        root.removeHandler(cap)
        pathb_mod.log.removeHandler(cap)
        root.setLevel(prev_level)
    return {"registered": list(registered), "err": err, "records": cap.records, "cap": cap}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--limit-logs", type=int, default=10)
    args = ap.parse_args()

    price = 100.0
    scenarios = []
    for market in ("US", "KR"):
        for mode in ("MILD_BULL", "CAUTIOUS", "DEFENSIVE", "HALT"):
            for zname, lo, hi in (
                ("현재가가 존 안", price * 0.99, price * 1.01),
                ("현재가가 존 위", price * 0.90, price * 0.95),
                ("현재가가 존 아래", price * 1.05, price * 1.10),
            ):
                scenarios.append(dict(market=market, mode=mode, price=price,
                                      zone_low=lo, zone_high=hi, _z=zname))

    print(f"PathB 시나리오 {len(scenarios)}건 — 시장 × 국면 × 존 위치")
    print(f"{'시장':4s} {'국면':11s} {'존위치':14s} | {'등록':4s} | 비고")
    print("-" * 92)
    reg_n = 0
    errs = 0
    for sc in scenarios:
        z = sc.pop("_z")
        r = run_case(**sc)
        ok = bool(r["registered"])
        reg_n += int(ok)
        note = ""
        if r["err"]:
            note = f"[오류] {r['err'][:56]}"
            errs += 1
        else:
            for needle, tag in GATES:
                if r["cap"].find(needle):
                    note = tag
                    break
        print(f"{sc['market']:4s} {sc['mode']:11s} {z:14s} | "
              f"{'OK' if ok else '..':4s} | {note}")
        if args.verbose:
            for lvl, msg in r["records"][: args.limit_logs]:
                print(f"      {lvl:7s} {msg[:110]}")
        sc["_z"] = z
    print()
    print(f"등록 성공 {reg_n}/{len(scenarios)} | 오류 {errs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
