from __future__ import annotations

import os
from datetime import date, datetime, timedelta, time as dt_time
from typing import Optional
from dotenv import load_dotenv
from logger import get_trading_logger

load_dotenv()
log = get_trading_logger()

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - python<3.9 fallback
    from datetime import timezone

    class ZoneInfo:  # type: ignore
        def __new__(cls, _name: str):
            return timezone(timedelta(hours=9))

KST = ZoneInfo("Asia/Seoul")


def _market_session_date_local(market: str):
    now_dt = datetime.now(KST)
    d = now_dt.date()
    if market == "US" and now_dt.time() < dt_time(5, 0):
        return d - timedelta(days=1)
    return d

# 수수료율 (근사값)
# KR 매수: 0.015%, KR 매도: 0.015% + 증권거래세 0.18% = 0.195%
# US 매수/매도: 0.015%
FEE_RATES = {
    "KR": {"buy": 0.00015, "sell": 0.00195},
    "US": {"buy": 0.00015, "sell": 0.00015},
}

AUTO_PROFIT_TRAILING_ENABLED = os.getenv("AUTO_PROFIT_TRAILING_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
AUTO_TRAIL_TRIGGER_PCT = float(os.getenv("AUTO_TRAIL_TRIGGER_PCT", "3.0"))
AUTO_TRAIL_PCT = float(os.getenv("AUTO_TRAIL_PCT", "0.04"))
AUTO_BREAKEVEN_BUFFER_PCT = float(os.getenv("AUTO_BREAKEVEN_BUFFER_PCT", "0.002"))
POSITION_SESSION_LOSS_CAP_PCT = float(os.getenv("POSITION_SESSION_LOSS_CAP_PCT", "0.5"))
PROFIT_FLOOR_ENABLED = os.getenv("PROFIT_FLOOR_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
PROFIT_FLOOR_TRIGGER_PCT = float(os.getenv("PROFIT_FLOOR_TRIGGER_PCT", "2.0"))
PROFIT_FLOOR_EXIT_PCT = float(os.getenv("PROFIT_FLOOR_EXIT_PCT", "0.5"))

HARD_RULES = {
    "max_daily_loss_pct":   float(os.getenv("MAX_DAILY_LOSS_PCT",   "-3.0")),   # 일일 최대 손실 (%)
    "max_single_loss_pct":  float(os.getenv("MAX_SINGLE_LOSS_PCT",  "-2.0")),   # 단일 종목 최대 손실 (%)
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
        self.halt_reason = ""
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

    def realized_daily_return(self) -> float:
        base = self.session_start_equity if self.session_start_equity > 0 else self.init_cash
        return self.daily_pnl / base * 100 if base > 0 else 0.0

    def reset_daily_state(self, clear_trade_log: bool = True, override_base: float | None = None):
        base = float(override_base) if override_base is not None else self.equity()
        self.session_start_equity = base if base > 0 else self.equity()
        self.daily_pnl = 0.0
        self.total_fee = 0.0
        self.halted = False
        self.halt_reason = ""
        if clear_trade_log:
            self.trade_log = []

    def check_halt(self, allow_auto_release: bool = False, auto_release_note: str = "") -> bool:
        ret = self.daily_return()
        realized_ret = self.realized_daily_return()
        threshold = HARD_RULES["max_daily_loss_pct"]
        equity_breach = ret < threshold
        pnl_breach = realized_ret < threshold
        if equity_breach and pnl_breach:
            if not self.halted:
                log.warning(
                    f"daily loss limit reached (equity={ret:.2f}% realized={realized_ret:.2f}%) -> halt"
                )
            self.halted = True
            if not self.halt_reason:
                self.halt_reason = "daily_loss"
        elif equity_breach and not pnl_breach:
            log.warning(
                f"[HALT 보류] equity breach only (equity={ret:.2f}% realized={realized_ret:.2f}% threshold={threshold:.2f}%)"
            )
        elif self.halted and allow_auto_release and ret > threshold * 0.5:
            # 손실이 한도의 절반 이상 회복되면 HALT 자동 해제
            # (False HALT 후 equity 정상화 시 세션 재개 가능하도록)
            log.warning(
                f"[HALT 자동 해제] daily_return={ret:.2f}% 회복 "
                f"(threshold={threshold:.2f}%) → halted=False"
            )
            self.halted = False
            self.halt_reason = ""
        return self.halted

    def can_open(self, ticker: str, price: float, mode_size_pct: int = 70, market: str = ""):
        if self.halted:
            return False, self.halt_reason or "daily loss limit"
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

    def calc_order_budget(
        self,
        mode_size_pct: int = 70,
        atr_pct: Optional[float] = None,
        atr_target_pct: float = 0.015,
    ) -> float:
        # MAX_ORDER_KRW 기준, 모드별 비율 조절
        budget = self.max_order_krw * (mode_size_pct / 100)
        # 현금 부족 시 잔액 전부 사용
        budget = min(budget, self.cash)
        if atr_pct is not None and atr_pct > 0 and atr_target_pct > 0:
            # Volatility targeting (optional): reduce size in high ATR regimes.
            vol_scale = atr_target_pct / atr_pct
            vol_scale = max(0.1, min(1.0, vol_scale))
            budget *= vol_scale
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
        budget = self.calc_order_budget(
            mode_size_pct,
            atr_pct=atr_pct,
            atr_target_pct=atr_target_pct,
        )
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
        session_date: Optional[str] = None,
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
        from datetime import datetime as _dt
        session_date = session_date or _market_session_date_local(self.market).isoformat()
        pos = {
            "ticker": ticker,
            "entry": price,
            "qty": qty,
            "current_price": price,
            "strategy": strategy,
            "tp": price * (1 + tp_pct),
            "sl": price * (1 - sl_pct),
            "tp_pct": tp_pct,        # 비율 보존 — US 환율 드리프트 방지
            "sl_pct": sl_pct,
            "max_hold": max_hold,
            "held_days": 0,
            "entry_date": date.today().isoformat(),
            "session_date": session_date,
            "entry_session_date": session_date,
            "entry_time": _dt.now().isoformat(timespec="seconds"),  # 장중 보유시간 계산용
            "peak_pnl_pct": 0.0,     # 보유 중 최고 수익률 (hold_advisor 컨텍스트용)
            "trough_pnl_pct": 0.0,   # 보유 중 최저 수익률 (exit audit용)
            # 트레일링 스탑
            "trailing": False,       # 트레일링 모드 여부
            "trail_sl": 0.0,         # 트레일링 SL 가격 (KRW/KRW)
            "trail_sl_usd": 0.0,     # 트레일링 SL (USD, US 전용)
            "trail_pct": 0.03,       # 트레일링 폭 (기본 3%)
            "tp_triggered": False,   # TP 도달 여부 (중복 방지)
            # hold_advisor 기록
            "hold_advice": None,     # {"action", "trail_pct", "votes"} or None
            "tp_price": 0.0,         # TP 도달 당시 가격
            "position_origin": "open_position",
            "position_integrity": "trusted",
            "management_protected": False,
        }
        self.positions.append(pos)
        evt = {
            "side": "buy",
            "ticker": ticker,
            "price": price,
            "qty": qty,
            "strategy": strategy,
            "date": date.today().isoformat(),
            "session_date": session_date,
        }
        self.trade_log.append(evt)
        self.all_trade_log.append(evt)
        log.info(f"[BUY] {ticker} {qty}@{price:,} TP={pos['tp']:.0f} SL={pos['sl']:.0f}")
        return True

    def update_prices(self, prices: dict, raw_prices: dict | None = None):
        """prices: KRW 환산 가격 dict / raw_prices: USD(US) 또는 KRW(KR) 원시가격 dict (선택)"""
        for pos in self.positions:
            if pos["ticker"] not in prices:
                continue
            pos["current_price"] = prices[pos["ticker"]]
            _entry = float(pos.get("entry") or 0)
            is_us = pos.get("display_currency") == "USD"
            protected = bool(pos.get("management_protected"))

            if raw_prices and pos["ticker"] in raw_prices and is_us:
                pos["display_current_price"] = float(raw_prices[pos["ticker"]] or 0)

            # peak_pnl_pct / auto-trailing trigger는 native currency 기준으로 계산한다.
            _cur_pnl = None
            if is_us:
                avg_usd = float(pos.get("display_avg_price") or 0)
                cp_usd = float(pos.get("display_current_price") or 0)
                if avg_usd > 0 and cp_usd > 0:
                    _cur_pnl = (cp_usd / avg_usd - 1) * 100
            elif _entry > 0:
                _cur_pnl = (pos["current_price"] / _entry - 1) * 100
            if _cur_pnl is not None and _cur_pnl > float(pos.get("peak_pnl_pct") or 0):
                pos["peak_pnl_pct"] = round(_cur_pnl, 3)
            if _cur_pnl is not None and _cur_pnl < float(pos.get("trough_pnl_pct") or 0):
                pos["trough_pnl_pct"] = round(_cur_pnl, 3)

            # 수익 보호: +3% 이상이면 TP 도달 이벤트를 기다리지 않고 본전 위 트레일링 전환.
            if (
                AUTO_PROFIT_TRAILING_ENABLED
                and _cur_pnl is not None
                and _cur_pnl >= AUTO_TRAIL_TRIGGER_PCT
                and not pos.get("trailing")
                and not protected
                and pos["current_price"] > 0
            ):
                trail_pct = max(0.005, min(0.08, AUTO_TRAIL_PCT))
                breakeven_sl = _entry * (1 + AUTO_BREAKEVEN_BUFFER_PCT)
                trail_sl = pos["current_price"] * (1 - trail_pct)
                pos["trailing"] = True
                pos["trail_pct"] = trail_pct
                pos["trail_sl"] = max(float(pos.get("trail_sl") or 0), breakeven_sl, trail_sl)
                pos["tp_triggered"] = True
                pos["tp_price"] = pos["current_price"]
                if pos.get("display_currency") == "USD":
                    avg_usd = float(pos.get("display_avg_price") or 0)
                    cp_usd = float(pos.get("display_current_price") or 0)
                    if avg_usd > 0 and cp_usd > 0:
                        pos["trail_sl_usd"] = max(
                            float(pos.get("trail_sl_usd") or 0),
                            avg_usd * (1 + AUTO_BREAKEVEN_BUFFER_PCT),
                            cp_usd * (1 - trail_pct),
                        )
                log.info(
                    f"[AUTO TRAILING] {pos['ticker']} pnl={_cur_pnl:+.2f}% "
                    f"trail={trail_pct*100:.1f}% sl={pos['trail_sl']:,.0f}"
                )

            # 트레일링 모드: SL을 현재가 기준으로 끌어올림 (내려가지 않음)
            if pos.get("trailing") and pos["current_price"] > 0:
                new_trail = pos["current_price"] * (1 - pos["trail_pct"])
                if new_trail > pos["trail_sl"]:
                    pos["trail_sl"] = new_trail
                # US 트레일링: USD 기준으로도 trail_sl_usd 갱신
                if pos.get("display_currency") == "USD":
                    cp_usd = float(pos.get("display_current_price") or 0)
                    if cp_usd > 0:
                        new_trail_usd = cp_usd * (1 - pos["trail_pct"])
                        if new_trail_usd > float(pos.get("trail_sl_usd", 0) or 0):
                            pos["trail_sl_usd"] = new_trail_usd

    def current_pnl_pct(self, pos: dict) -> float | None:
        entry = float(pos.get("entry") or 0)
        if pos.get("display_currency") == "USD":
            avg_usd = float(pos.get("display_avg_price") or 0)
            cp_usd = float(pos.get("display_current_price") or 0)
            if avg_usd > 0 and cp_usd > 0:
                return (cp_usd / avg_usd - 1.0) * 100.0
            return None
        cp = float(pos.get("current_price") or 0)
        if entry > 0 and cp > 0:
            return (cp / entry - 1.0) * 100.0
        return None

    def position_loss_budget_krw(self, pos: dict) -> float:
        entry = float(pos.get("entry") or 0)
        qty = int(pos.get("qty", 0) or 0)
        entry_value = entry * qty
        if entry_value <= 0:
            return 0.0
        budgets: list[float] = []
        single_loss_pct = abs(float(HARD_RULES.get("max_single_loss_pct", 0) or 0)) / 100.0
        if single_loss_pct > 0:
            budgets.append(entry_value * single_loss_pct)
        session_loss_pct = max(0.0, float(POSITION_SESSION_LOSS_CAP_PCT or 0)) / 100.0
        base = float(self.session_start_equity or 0)
        if session_loss_pct > 0 and base > 0:
            budgets.append(base * session_loss_pct)
        return max(0.0, min(budgets)) if budgets else 0.0

    def loss_cap_pct(self, pos: dict) -> float:
        entry = float(pos.get("entry") or 0)
        qty = int(pos.get("qty", 0) or 0)
        entry_value = entry * qty
        if entry_value <= 0:
            return 0.0
        budget = self.position_loss_budget_krw(pos)
        if budget <= 0:
            return 0.0
        return max(0.0, min(0.99, budget / entry_value))

    def loss_cap_price(self, pos: dict, *, native: bool = False) -> float:
        cap_pct = self.loss_cap_pct(pos)
        if cap_pct <= 0:
            return 0.0
        if native and pos.get("display_currency") == "USD":
            avg_usd = float(pos.get("display_avg_price") or 0)
            return avg_usd * (1.0 - cap_pct) if avg_usd > 0 else 0.0
        entry = float(pos.get("entry") or 0)
        return entry * (1.0 - cap_pct) if entry > 0 else 0.0

    def profit_floor_price(self, pos: dict, *, native: bool = False) -> float:
        floor_pct = float(PROFIT_FLOOR_EXIT_PCT or 0) / 100.0
        if native and pos.get("display_currency") == "USD":
            avg_usd = float(pos.get("display_avg_price") or 0)
            return avg_usd * (1.0 + floor_pct) if avg_usd > 0 else 0.0
        entry = float(pos.get("entry") or 0)
        return entry * (1.0 + floor_pct) if entry > 0 else 0.0

    def soft_exit_floor_price(self, pos: dict, *, native: bool = False) -> float:
        floor = float(pos.get("soft_exit_floor_price") or 0)
        if floor <= 0:
            return 0.0
        if native and pos.get("display_currency") == "USD":
            return floor
        if not native and pos.get("display_currency") == "USD":
            rate = float(pos.get("entry") or 0) / float(pos.get("display_avg_price") or 0) if float(pos.get("display_avg_price") or 0) > 0 else 0.0
            return floor * rate if rate > 0 else 0.0
        return floor

    def profit_floor_triggered(self, pos: dict) -> bool:
        if not PROFIT_FLOOR_ENABLED:
            return False
        peak = float(pos.get("peak_pnl_pct") or 0)
        current = self.current_pnl_pct(pos)
        if current is None:
            return False
        return peak >= float(PROFIT_FLOOR_TRIGGER_PCT or 0) and current <= float(PROFIT_FLOOR_EXIT_PCT or 0)

    @staticmethod
    def _exit_meta(
        *,
        strategy_stop_price: float = 0.0,
        loss_cap_price: float = 0.0,
        effective_stop_price: float = 0.0,
        loss_budget_krw: float = 0.0,
        profit_floor_price: float = 0.0,
        profit_floor_triggered: bool = False,
        peak_pnl_pct: float = 0.0,
        position_mfe_pct: float = 0.0,
        position_mae_pct: float = 0.0,
    ) -> dict:
        return {
            "strategy_stop_price": float(strategy_stop_price or 0),
            "loss_cap_price": float(loss_cap_price or 0),
            "effective_stop_price": float(effective_stop_price or 0),
            "loss_budget_krw": float(loss_budget_krw or 0),
            "profit_floor_price": float(profit_floor_price or 0),
            "profit_floor_triggered": bool(profit_floor_triggered),
            "peak_pnl_pct": float(peak_pnl_pct or 0),
            "position_mfe_pct": float(position_mfe_pct or 0),
            "position_mae_pct": float(position_mae_pct or 0),
        }

    def _stop_reason(
        self,
        current: float,
        strategy_stop: float,
        loss_cap_stop: float,
        fallback_reason: str,
    ) -> tuple[str | None, float]:
        effective_stop = max(float(strategy_stop or 0), float(loss_cap_stop or 0))
        if effective_stop <= 0 or float(current or 0) > effective_stop:
            return None, effective_stop
        if loss_cap_stop > 0 and loss_cap_stop >= float(strategy_stop or 0):
            return "loss_cap", effective_stop
        return fallback_reason, effective_stop

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
            if pos.get("pathb_closing"):
                continue
            if pos.get("pathb_path_run_id"):
                continue
            cp = float(pos.get("current_price") or 0)
            reason = None

            is_us = pos.get("display_currency") == "USD"
            protected = bool(pos.get("management_protected"))
            avg_usd = float(pos.get("display_avg_price") or 0)
            cp_usd = float(pos.get("display_current_price") or 0)
            entry_krw = float(pos.get("entry") or 0)
            loss_budget_krw = self.position_loss_budget_krw(pos)
            peak_pnl_pct = float(pos.get("peak_pnl_pct") or 0)
            trough_pnl_pct = float(pos.get("trough_pnl_pct") or 0)
            floor_triggered = self.profit_floor_triggered(pos)

            # US 종목: 환율 드리프트 방지를 위해 USD 기준으로 TP/SL 비교
            if is_us and avg_usd > 0 and cp_usd > 0 and entry_krw > 0:
                # tp_pct/sl_pct가 있으면 우선 사용, 없으면 KRW 비율에서 역산
                tp_pct = float(pos.get("tp_pct") or 0) or (pos["tp"] / entry_krw - 1 if pos.get("tp") else 0)
                sl_pct = float(pos.get("sl_pct") or 0) or (1 - pos["sl"] / entry_krw if pos.get("sl") else 0)
                tp_usd = avg_usd * (1 + tp_pct)
                sl_usd = avg_usd * (1 - sl_pct)
                loss_cap_usd = self.loss_cap_price(pos, native=True)
                floor_usd = self.profit_floor_price(pos, native=True)
                soft_floor_usd = self.soft_exit_floor_price(pos, native=True)
                exit_meta = self._exit_meta(
                    strategy_stop_price=sl_usd,
                    loss_cap_price=loss_cap_usd,
                    loss_budget_krw=loss_budget_krw,
                    profit_floor_price=floor_usd,
                    profit_floor_triggered=floor_triggered,
                    peak_pnl_pct=peak_pnl_pct,
                    position_mfe_pct=peak_pnl_pct,
                    position_mae_pct=trough_pnl_pct,
                )
                exit_meta["soft_exit_floor_price"] = soft_floor_usd
                exit_meta["soft_exit_floor_triggered"] = bool(soft_floor_usd > 0 and cp_usd <= soft_floor_usd)

                if protected:
                    reason, effective_stop = self._stop_reason(cp_usd, sl_usd, loss_cap_usd, "stop_loss")
                    exit_meta["effective_stop_price"] = effective_stop
                    if not reason and soft_floor_usd > 0 and cp_usd <= soft_floor_usd:
                        reason = "soft_exit_floor_price"
                elif pos.get("trailing"):
                    trail_sl_usd = float(pos.get("trail_sl_usd") or 0)
                    reason, effective_stop = self._stop_reason(cp_usd, trail_sl_usd, loss_cap_usd, "trail_stop")
                    exit_meta["strategy_stop_price"] = trail_sl_usd
                    exit_meta["effective_stop_price"] = effective_stop
                    if soft_floor_usd > 0 and cp_usd <= soft_floor_usd and reason in (None, "trail_stop"):
                        reason = "soft_exit_floor_price"
                    if not reason and floor_triggered and floor_usd > 0 and cp_usd <= floor_usd:
                        reason = "profit_floor"
                    if not reason and pos["held_days"] >= pos["max_hold"]:
                        reason = "max_hold"
                else:
                    reason, effective_stop = self._stop_reason(cp_usd, sl_usd, loss_cap_usd, "stop_loss")
                    exit_meta["effective_stop_price"] = effective_stop
                    if reason:
                        pass
                    elif soft_floor_usd > 0 and cp_usd <= soft_floor_usd:
                        reason = "soft_exit_floor_price"
                    elif floor_triggered and floor_usd > 0 and cp_usd <= floor_usd:
                        reason = "profit_floor"
                    elif cp_usd >= tp_usd and not pos.get("tp_triggered"):
                        reason = "tp_check"
                    elif pos["held_days"] >= pos["max_hold"]:
                        reason = "max_hold"

                if reason:
                    candidates.append({**pos, "exit_price": cp, "reason": reason, **exit_meta})
                continue

            # KR 종목 (기존 로직)
            loss_cap_krw = self.loss_cap_price(pos)
            floor_krw = self.profit_floor_price(pos)
            soft_floor_krw = self.soft_exit_floor_price(pos)
            base_stop = float(pos.get("sl") or 0)
            exit_meta = self._exit_meta(
                strategy_stop_price=base_stop,
                loss_cap_price=loss_cap_krw,
                loss_budget_krw=loss_budget_krw,
                profit_floor_price=floor_krw,
                profit_floor_triggered=floor_triggered,
                peak_pnl_pct=peak_pnl_pct,
                position_mfe_pct=peak_pnl_pct,
                position_mae_pct=trough_pnl_pct,
            )
            exit_meta["soft_exit_floor_price"] = soft_floor_krw
            exit_meta["soft_exit_floor_triggered"] = bool(soft_floor_krw > 0 and cp <= soft_floor_krw)
            if protected:
                reason, effective_stop = self._stop_reason(cp, base_stop, loss_cap_krw, "stop_loss")
                exit_meta["effective_stop_price"] = effective_stop
                if not reason and soft_floor_krw > 0 and cp <= soft_floor_krw:
                    reason = "soft_exit_floor_price"
            elif pos.get("trailing"):
                trail_stop = float(pos.get("trail_sl") or 0)
                reason, effective_stop = self._stop_reason(cp, trail_stop, loss_cap_krw, "trail_stop")
                exit_meta["strategy_stop_price"] = trail_stop
                exit_meta["effective_stop_price"] = effective_stop
                if soft_floor_krw > 0 and cp <= soft_floor_krw and reason in (None, "trail_stop"):
                    reason = "soft_exit_floor_price"
                if not reason and floor_triggered and floor_krw > 0 and cp <= floor_krw:
                    reason = "profit_floor"
                if not reason and pos["held_days"] >= pos["max_hold"]:
                    reason = "max_hold"
            else:
                reason, effective_stop = self._stop_reason(cp, base_stop, loss_cap_krw, "stop_loss")
                exit_meta["effective_stop_price"] = effective_stop
                if reason:
                    pass
                elif soft_floor_krw > 0 and cp <= soft_floor_krw:
                    reason = "soft_exit_floor_price"
                elif floor_triggered and floor_krw > 0 and cp <= floor_krw:
                    reason = "profit_floor"
                elif cp >= pos["tp"] and not pos.get("tp_triggered"):
                    reason = "tp_check"      # TP 도달 → trading_bot에서 처리
                elif pos["held_days"] >= pos["max_hold"]:
                    reason = "max_hold"
            if reason:
                candidates.append({**pos, "exit_price": cp, "reason": reason, **exit_meta})
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
                # US 종목: USD 기준 trail_sl도 함께 설정
                if pos.get("display_currency") == "USD":
                    cp_usd = float(pos.get("display_current_price") or 0)
                    pos["trail_sl_usd"] = cp_usd * (1 - trail_pct) if cp_usd > 0 else 0.0
                if hold_advice is not None:
                    pos["hold_advice"] = hold_advice
                log.info(f"[TRAILING] {ticker} trail_sl={pos['trail_sl']:,.0f} ({trail_pct*100:.1f}%)")
                return True
        return False

    def close_position(
        self,
        ticker: str,
        exit_price: float,
        reason: str,
        session_date: Optional[str] = None,
        exit_meta: Optional[dict] = None,
    ):
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
            session_date = session_date or str(pos.get("session_date") or _market_session_date_local(self.market).isoformat())

            closed = {
                **pos,
                "exit_price": exit_price,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "reason": reason,
                **(exit_meta or {}),
            }
            evt = {
                "side": "sell",
                "ticker": pos["ticker"],
                "price": exit_price,
                "qty": pos["qty"],
                "strategy": pos["strategy"],
                "source_strategy": pos.get("source_strategy", ""),
                "micro_probe": bool(pos.get("micro_probe")),
                "micro_probe_reason": pos.get("micro_probe_reason", ""),
                "original_order_cost_krw": float(pos.get("original_order_cost_krw", 0) or 0),
                "adjusted_order_cost_krw": float(pos.get("adjusted_order_cost_krw", 0) or 0),
                "oversize_ratio": float(pos.get("oversize_ratio", 0) or 0),
                "date": date.today().isoformat(),
                "session_date": session_date,
                "reason": reason,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                **(exit_meta or {}),
            }
            self.trade_log.append(evt)
            self.all_trade_log.append(evt)
            log.info(f"[{reason}] {pos['ticker']} {pnl:+,.0f} ({pnl_pct:+.2f}%)")
            return closed
        return None

    def check_exits(self):
        exits = []
        for cand in self.get_exit_candidates():
            exit_meta = {
                key: cand[key]
                for key in (
                    "strategy_stop_price",
                    "loss_cap_price",
                    "effective_stop_price",
                    "loss_budget_krw",
                    "profit_floor_price",
                    "profit_floor_triggered",
                    "peak_pnl_pct",
                    "position_mfe_pct",
                    "position_mae_pct",
                    "exit_owner",
                )
                if key in cand
            }
            closed = self.close_position(
                cand["ticker"],
                cand["exit_price"],
                cand["reason"],
                exit_meta=exit_meta,
            )
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
            "realized_daily_return": self.realized_daily_return(),
            "total_fee": self.total_fee,
            "halted": self.halted,
        }
