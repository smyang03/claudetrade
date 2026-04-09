from __future__ import annotations

import os
from datetime import date
from dotenv import load_dotenv
from logger import get_trading_logger

load_dotenv()
log = get_trading_logger()

# 수수료율 (근사값)
# KR 매수: 0.015%, KR 매도: 0.015% + 증권거래세 0.18% = 0.195%
# US 매수/매도: 0.015%
FEE_RATES = {
    "KR": {"buy": 0.00015, "sell": 0.00195},
    "US": {"buy": 0.00015, "sell": 0.00015},
}

HARD_RULES = {
    "max_daily_loss_pct":   float(os.getenv("MAX_DAILY_LOSS_PCT",   "-3.0")),   # 일일 최대 손실 (%)
    "max_single_loss_pct":  float(os.getenv("MAX_SINGLE_LOSS_PCT",  "-3.0")),   # 단일 종목 최대 손실 (%)
    "take_profit_pct":      float(os.getenv("TAKE_PROFIT_PCT",       "6.0")),   # 기본 TP (%)
    "max_positions":          int(os.getenv("MAX_POSITIONS",            "3")),   # 동시 보유 최대 종목 수
    "max_pyramid":            int(os.getenv("MAX_PYRAMID",              "2")),   # 동일 종목 최대 추가매수 횟수
    "max_position_pct":     float(os.getenv("MAX_POSITION_PCT",      "0.20")),  # 종목당 최대 비중 (소수)
    "max_order_krw":        float(os.getenv("MAX_ORDER_KRW",      "500000")),   # 1회 최대 주문금액
    "no_new_entry_min":       int(os.getenv("NO_NEW_ENTRY_MIN",       "10")),   # 장 시작 후 N분 진입 금지
    "close_before_min":       int(os.getenv("CLOSE_BEFORE_MIN",       "10")),   # 장 마감 전 N분 신규 금지
    "max_sector_positions":   int(os.getenv("MAX_SECTOR_POSITIONS",    "2")),   # 동일 섹터 최대 보유
}


class RiskManager:
    def __init__(self, init_cash: float = 10_000_000, max_order_krw: Optional[float] = None,
                 market: str = "KR"):
        self.init_cash = init_cash
        self.cash = init_cash
        self.max_order_krw = max_order_krw if max_order_krw is not None else HARD_RULES["max_order_krw"]
        self.market = market
        self.positions = []
        self.session_start_equity = init_cash
        self.daily_pnl = 0.0
        self.total_fee = 0.0          # 누적 수수료
        self.halted = False
        self.all_trade_log = []
        self.trade_log = []

    def _fee(self, side: str, amount: float) -> float:
        rate = FEE_RATES.get(self.market, FEE_RATES["KR"])[side]
        return amount * rate

    def equity(self) -> float:
        pos_val = sum(p["qty"] * p["current_price"] for p in self.positions)
        return self.cash + pos_val

    def daily_return(self) -> float:
        base = self.session_start_equity if self.session_start_equity > 0 else self.init_cash
        return (self.equity() - base) / base * 100

    def reset_daily_state(self, clear_trade_log: bool = True):
        self.session_start_equity = self.equity()
        self.daily_pnl = 0.0
        self.total_fee = 0.0
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

    def can_open(self, ticker: str, price: float, mode_size_pct: int = 70, market: str = ""):
        if self.halted:
            return False, "daily loss limit"
        # 마켓별 포지션 수 제한 (market 지정 시 해당 마켓만, 미지정 시 전체)
        if market:
            def _is_same_market(p: dict) -> bool:
                tk = p.get("ticker", "")
                pos_mkt = "US" if tk.replace(".", "").isalpha() else "KR"
                return pos_mkt == market
            mkt_count = sum(1 for p in self.positions if _is_same_market(p))
        else:
            mkt_count = len(self.positions)
        if mkt_count >= HARD_RULES["max_positions"]:
            return False, f"max positions {HARD_RULES['max_positions']} ({market or 'total'})"
        # 동일 티커 피라미딩 제한 — market 구분 (KR/US 간 티커명 충돌 방지)
        def _ticker_mkt(tk: str) -> str:
            return "US" if tk.replace(".", "").isalpha() else "KR"
        same = sum(
            1 for p in self.positions
            if p["ticker"] == ticker
            and (not market or _ticker_mkt(p["ticker"]) == market)
        )
        if same >= HARD_RULES["max_pyramid"]:
            return False, "already holding"
        if price <= 0:
            return False, "invalid price"
        # 수수료 포함 최소 필요 현금 체크 (1주 기준)
        if self.cash < price + self._fee("buy", price):
            return False, "insufficient cash"
        return True, "OK"

    def calc_order_budget(self, mode_size_pct: int = 70) -> float:
        # MAX_ORDER_KRW 기준, 모드별 비율 조절
        budget = self.max_order_krw * (mode_size_pct / 100)
        # 현금 부족 시 잔액 전부 사용
        budget = min(budget, self.cash)
        return max(0.0, budget)

    def calc_order_size(
        self,
        price: float,
        mode_size_pct: int = 70,
        sl_pct: float = 0.03,
        atr_pct: Optional[float] = None,
        atr_target_pct: float = 0.015,
    ) -> int:
        if price <= 0:
            return 0
        budget = self.calc_order_budget(mode_size_pct)
        if atr_pct is not None and atr_pct > 0 and atr_target_pct > 0:
            # Volatility targeting (optional): reduce size in high ATR regimes.
            vol_scale = atr_target_pct / atr_pct
            vol_scale = max(0.1, min(1.0, vol_scale))
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
        fee  = self._fee("buy", cost)
        total_cost = cost + fee
        if total_cost > self.cash:
            log.warning(f"insufficient cash need={total_cost:,} cash={self.cash:,}")
            return False

        self.cash -= total_cost
        self.total_fee += fee
        self.daily_pnl -= fee          # 매수 수수료 즉시 반영
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
            # 트레일링 스탑
            "trailing": False,       # 트레일링 모드 여부
            "trail_sl": 0.0,         # 트레일링 SL 가격
            "trail_pct": 0.03,       # 트레일링 폭 (기본 3%)
            "tp_triggered": False,   # TP 도달 여부 (중복 방지)
            # hold_advisor 기록
            "hold_advice": None,     # {"action", "trail_pct", "votes"} or None
            "tp_price": 0.0,         # TP 도달 당시 가격
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
                # 트레일링 모드: SL을 현재가 기준으로 끌어올림 (내려가지 않음)
                if pos.get("trailing") and pos["current_price"] > 0:
                    new_trail = pos["current_price"] * (1 - pos["trail_pct"])
                    if new_trail > pos["trail_sl"]:
                        pos["trail_sl"] = new_trail

    def increment_holding_days(self, today_iso: Optional[str] = None):
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
            if pos.get("trailing"):
                # 트레일링 모드: trail_sl 발동 여부만 체크
                if cp <= pos["trail_sl"]:
                    reason = "trail_stop"
                elif pos["held_days"] >= pos["max_hold"]:
                    reason = "max_hold"
            else:
                if cp >= pos["tp"] and not pos.get("tp_triggered"):
                    reason = "tp_check"      # TP 도달 → trading_bot에서 처리
                elif cp <= pos["sl"]:
                    reason = "stop_loss"
                elif pos["held_days"] >= pos["max_hold"]:
                    reason = "max_hold"
            if reason:
                candidates.append({**pos, "exit_price": cp, "reason": reason})
        return candidates

    def activate_trailing(self, ticker: str, trail_pct: float, hold_advice: dict = None):
        """포지션을 트레일링 스탑 모드로 전환"""
        for pos in self.positions:
            if pos["ticker"] == ticker:
                pos["trailing"]     = True
                pos["trail_pct"]    = trail_pct
                pos["trail_sl"]     = pos["current_price"] * (1 - trail_pct)
                pos["tp_triggered"] = True
                pos["tp_price"]     = pos["current_price"]   # TP 도달 시점 가격
                if hold_advice is not None:
                    pos["hold_advice"] = hold_advice
                log.info(f"[TRAILING] {ticker} trail_sl={pos['trail_sl']:,.0f} ({trail_pct*100:.1f}%)")
                return True
        return False

    def close_position(self, ticker: str, exit_price: float, reason: str):
        for pos in list(self.positions):
            if pos["ticker"] != ticker:
                continue

            gross_pnl  = (exit_price - pos["entry"]) * pos["qty"]
            sell_fee   = self._fee("sell", exit_price * pos["qty"])
            pnl        = gross_pnl - sell_fee
            cost_basis = pos["entry"] * pos["qty"]
            pnl_pct    = (pnl / cost_basis * 100) if cost_basis else 0.0
            self.cash += exit_price * pos["qty"] - sell_fee
            self.total_fee += sell_fee
            self.daily_pnl += gross_pnl - sell_fee
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
            "total_fee": self.total_fee,
            "halted": self.halted,
        }
