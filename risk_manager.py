from __future__ import annotations

from datetime import date
from logger import get_trading_logger

log = get_trading_logger()

HARD_RULES = {
    "max_daily_loss_pct": -3.0,
    "max_single_loss_pct": -3.0,
    "take_profit_pct": 6.0,
    "max_positions": 3,
    "max_position_pct": 0.20,
    "max_order_krw": 500_000,
    "no_new_entry_min": 10,
    "close_before_min": 10,
    "max_sector_positions": 2,
}


class RiskManager:
    def __init__(self, init_cash: float = 10_000_000):
        self.init_cash = init_cash
        self.cash = init_cash
        self.positions = []
        self.session_start_equity = init_cash
        self.daily_pnl = 0.0
        self.halted = False
        self.all_trade_log = []
        self.trade_log = []

    def equity(self) -> float:
        pos_val = sum(p["qty"] * p["current_price"] for p in self.positions)
        return self.cash + pos_val

    def daily_return(self) -> float:
        base = self.session_start_equity if self.session_start_equity > 0 else self.init_cash
        return (self.equity() - base) / base * 100

    def reset_daily_state(self, clear_trade_log: bool = True):
        self.session_start_equity = self.equity()
        self.daily_pnl = 0.0
        self.halted = False
        if clear_trade_log:
            self.trade_log = []

    def check_halt(self) -> bool:
        if self.daily_return() < HARD_RULES["max_daily_loss_pct"]:
            if not self.halted:
                log.warning(
                    f"daily loss limit reached ({self.daily_return():.2f}%) -> halt"
                )
            self.halted = True
        return self.halted

    def can_open(self, ticker: str, price: float, mode_size_pct: int = 70):
        if self.halted:
            return False, "daily loss limit"
        if len(self.positions) >= HARD_RULES["max_positions"]:
            return False, f"max positions {HARD_RULES['max_positions']}"
        if any(p["ticker"] == ticker for p in self.positions):
            return False, "already holding"
        if price <= 0:
            return False, "invalid price"
        if self.cash < price:
            return False, "insufficient cash"
        return True, "OK"

    def calc_order_budget(self, mode_size_pct: int = 70) -> float:
        budget = self.cash * HARD_RULES["max_position_pct"] * (mode_size_pct / 100)
        budget = min(budget, HARD_RULES["max_order_krw"])
        budget = min(budget, self.cash * 0.5)
        return max(0.0, budget)

    def calc_order_size(
        self,
        price: float,
        mode_size_pct: int = 70,
        sl_pct: float = 0.03,
        atr_pct: float | None = None,
        atr_target_pct: float = 0.015,
    ) -> int:
        if price <= 0:
            return 0
        budget = self.calc_order_budget(mode_size_pct)
        if atr_pct is not None and atr_pct > 0 and atr_target_pct > 0:
            # Volatility targeting (optional): reduce size in high ATR regimes.
            vol_scale = atr_target_pct / atr_pct
            vol_scale = max(0.2, min(1.0, vol_scale))
            budget *= vol_scale
        return max(1, int(budget / price)) if budget >= price else 0

    def open_position(
        self,
        ticker: str,
        price: float,
        qty: int,
        strategy: str,
        tp_pct: float,
        sl_pct: float,
        max_hold: int = 1,
    ):
        cost = price * qty
        if cost > self.cash:
            log.warning(f"insufficient cash need={cost:,} cash={self.cash:,}")
            return False

        self.cash -= cost
        pos = {
            "ticker": ticker,
            "entry": price,
            "qty": qty,
            "current_price": price,
            "strategy": strategy,
            "tp": price * (1 + tp_pct),
            "sl": price * (1 - sl_pct),
            "max_hold": max_hold,
            "held_days": 0,
            "entry_date": date.today().isoformat(),
        }
        self.positions.append(pos)
        evt = {
            "side": "buy",
            "ticker": ticker,
            "price": price,
            "qty": qty,
            "strategy": strategy,
            "date": date.today().isoformat(),
        }
        self.trade_log.append(evt)
        self.all_trade_log.append(evt)
        log.info(f"[BUY] {ticker} {qty}@{price:,} TP={pos['tp']:.0f} SL={pos['sl']:.0f}")
        return True

    def update_prices(self, prices: dict):
        for pos in self.positions:
            if pos["ticker"] in prices:
                pos["current_price"] = prices[pos["ticker"]]

    def increment_holding_days(self, today_iso: str | None = None):
        if today_iso is None:
            today_iso = date.today().isoformat()
        for pos in self.positions:
            if pos.get("last_hold_update") == today_iso:
                continue
            pos["held_days"] = int(pos.get("held_days", 0)) + 1
            pos["last_hold_update"] = today_iso

    def get_exit_candidates(self):
        candidates = []
        for pos in self.positions:
            cp = pos["current_price"]
            reason = None
            if cp >= pos["tp"]:
                reason = "take_profit"
            elif cp <= pos["sl"]:
                reason = "stop_loss"
            elif pos["held_days"] >= pos["max_hold"]:
                reason = "max_hold"
            if reason:
                candidates.append({**pos, "exit_price": cp, "reason": reason})
        return candidates

    def close_position(self, ticker: str, exit_price: float, reason: str):
        for pos in list(self.positions):
            if pos["ticker"] != ticker:
                continue

            pnl = (exit_price - pos["entry"]) * pos["qty"]
            pnl_pct = (exit_price - pos["entry"]) / pos["entry"] * 100
            self.cash += exit_price * pos["qty"]
            self.daily_pnl += pnl
            self.positions.remove(pos)

            closed = {
                **pos,
                "exit_price": exit_price,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "reason": reason,
            }
            evt = {
                "side": "sell",
                "ticker": pos["ticker"],
                "price": exit_price,
                "qty": pos["qty"],
                "strategy": pos["strategy"],
                "date": date.today().isoformat(),
                "reason": reason,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            }
            self.trade_log.append(evt)
            self.all_trade_log.append(evt)
            log.info(f"[{reason}] {pos['ticker']} {pnl:+,.0f} ({pnl_pct:+.2f}%)")
            return closed
        return None

    def check_exits(self):
        exits = []
        for cand in self.get_exit_candidates():
            closed = self.close_position(cand["ticker"], cand["exit_price"], cand["reason"])
            if closed:
                exits.append(closed)
        return exits

    def force_close_all(self, prices: dict, reason: str = "forced_close"):
        self.update_prices(prices)
        for pos in list(self.positions):
            self.close_position(pos["ticker"], pos["current_price"], reason)

    def get_status(self):
        return {
            "cash": self.cash,
            "equity": self.equity(),
            "positions": len(self.positions),
            "daily_pnl": self.daily_pnl,
            "daily_return": self.daily_return(),
            "halted": self.halted,
        }
