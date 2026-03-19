"""
trading_bot.py
Main loop for KR/US sessions. Paper by default, live with --live.
"""

import os
import sys
import json
import time
import argparse
import schedule
from pathlib import Path
from datetime import date, datetime, timedelta, time as dt_time
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - python<3.9 fallback
    from datetime import timezone

    class ZoneInfo:  # type: ignore
        def __new__(cls, _name: str):
            return timezone(timedelta(hours=9))

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

from logger import get_trading_logger
from kis_api import (
    get_access_token,
    get_price,
    get_balance,
    place_order,
    KISWebSocket,
    get_daily_ohlcv,
)
from indicators import calc_all
from risk_manager import RiskManager, HARD_RULES
from telegram_reporter import (
    morning_briefing,
    tuning_report,
    trade_alert,
    pnl_alert,
    daily_summary,
)
from minority_report.analysts import get_three_judgments
from minority_report.consensus import build_consensus
from minority_report.tuner import tune
from minority_report.postmortem import run as run_postmortem
from phase1_trainer.digest_builder import build_kr_digest, build_us_digest, digest_to_prompt
from strategy.momentum import signal as mom_sig, params as mom_params
from strategy.mean_reversion import signal as mr_sig, params as mr_params
from strategy.gap_pullback import signal as gap_sig, params as gap_params
from strategy.volatility_breakout import signal as vb_sig, params as vb_params

from claude_memory import brain as BrainDB

log = get_trading_logger()
KST = ZoneInfo("Asia/Seoul")

JUDGMENT_DIR = Path("logs/daily_judgment")
JUDGMENT_DIR.mkdir(parents=True, exist_ok=True)

POSITIONS_FILE = Path("logs/open_positions.json")  # 포지션 영속성 파일
POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)

KR_TICKERS = ["005930", "000660", "035420"]
US_TICKERS = ["NVDA", "TSLA", "AAPL"]


class TradingBot:
    def __init__(self, is_paper: bool = True):
        self.is_paper = is_paper
        self.token = get_access_token()

        # ── 투자 금액 설정 ──────────────────────────────────────────────────
        # 모의투자: .env의 PAPER_CASH (기본 10,000,000원)
        # 실계좌:  실제 KIS 잔고 조회 → 총자산(현금+평가액) 사용
        if is_paper:
            init_cash = int(os.getenv("PAPER_CASH", "10000000"))
            max_order = int(os.getenv("MAX_ORDER_KRW", "500000"))
            log.info(f"모의투자 | 가상자금 {init_cash:,}원 | 최대주문 {max_order:,}원")
        else:
            try:
                bal = get_balance(self.token, market="KR")
                init_cash = bal["cash"] + bal["total_eval"]
                if init_cash <= 0:
                    raise ValueError("잔고 0 — 계좌 확인 필요")
                # 최대 주문: env 설정값 vs 총자산 5% 중 작은 값
                env_cap = int(os.getenv("MAX_ORDER_KRW", "2000000"))
                max_order = min(env_cap, int(init_cash * 0.05))
                log.info(f"실계좌 | 총자산 {init_cash:,}원 "
                         f"(현금 {bal['cash']:,} + 평가 {bal['total_eval']:,}) "
                         f"| 최대주문 {max_order:,}원")
            except Exception as e:
                log.error(f"잔고 조회 실패: {e}")
                raise SystemExit("실계좌 잔고 조회에 실패했습니다. 계좌/API 설정을 확인하세요.")

        HARD_RULES["max_order_krw"] = max_order
        self.risk = RiskManager(init_cash=init_cash)

        # ── KR / US 시장 예산 분리 ──────────────────────────────────────────
        # .env: KR_ALLOC_PCT=60  →  KR 60%, US 40% 배분
        kr_pct = min(100, max(0, int(os.getenv("KR_ALLOC_PCT", "60")))) / 100.0
        self.kr_budget = init_cash * kr_pct
        self.us_budget = init_cash * (1.0 - kr_pct)
        log.info(f"예산 배분 | KR {self.kr_budget:,.0f}원 ({kr_pct*100:.0f}%) "
                 f"/ US {self.us_budget:,.0f}원 ({(1-kr_pct)*100:.0f}%)")
        # ───────────────────────────────────────────────────────────────────

        self.today_judgment = {}
        self.tuning_count = 0
        self.ws = None
        self.price_cache = {}
        self.session_active = False
        self.current_market = None

        # 재시작 시 이월 포지션 복구
        self._restore_positions()

        # API 연결 상태 점검
        self._startup_health_check()
        log.info(f"init | {'paper' if is_paper else 'live'}")

    # ── API 헬스체크 ───────────────────────────────────────────────────────────

    def _startup_health_check(self):
        """봇 시작 시 연결된 API 전체 상태 점검 후 텔레그램 리포트"""
        import os as _os
        results: dict[str, str] = {}

        # 1. KIS 토큰 (이미 __init__에서 획득했으면 OK)
        results["KIS 토큰"] = "OK" if self.token else "FAIL - 토큰 없음"

        # 2. KIS 실시간 시세 (삼성전자 호출)
        try:
            price_info = get_price("005930", self.token, market="KR")
            results["KIS 시세 (005930)"] = f"OK ({price_info['price']:,}원)"
        except Exception as e:
            results["KIS 시세 (005930)"] = f"FAIL - {e}"

        # 3. Alpha Vantage (US 시세)
        av_key = _os.getenv("ALPHA_VANTAGE_KEY", "")
        if av_key:
            try:
                candles = get_daily_ohlcv("NVDA", self.token, lookback_days=5, market="US")
                results["Alpha Vantage (NVDA)"] = (
                    f"OK ({len(candles)}일 캔들)" if not candles.empty else "WARN - 빈 데이터"
                )
            except Exception as e:
                results["Alpha Vantage (NVDA)"] = f"FAIL - {e}"
        else:
            results["Alpha Vantage"] = "SKIP - ALPHA_VANTAGE_KEY 없음"

        # 4. Telegram
        try:
            import requests as _req
            tg_token = _os.getenv("TELEGRAM_TOKEN", "")
            if tg_token:
                resp = _req.get(
                    f"https://api.telegram.org/bot{tg_token}/getMe", timeout=5
                )
                resp.raise_for_status()
                bot_name = resp.json()["result"].get("username", "?")
                results["Telegram"] = f"OK (@{bot_name})"
            else:
                results["Telegram"] = "SKIP - TELEGRAM_TOKEN 없음"
        except Exception as e:
            results["Telegram"] = f"FAIL - {e}"

        # 5. DART (공시) - 선택
        dart_key = _os.getenv("DART_API_KEY", "")
        if dart_key:
            try:
                import requests as _req
                resp = _req.get(
                    "https://opendart.fss.or.kr/api/company.json",
                    params={"crtfc_key": dart_key, "corp_code": "00126380"},
                    timeout=5,
                )
                resp.raise_for_status()
                status = resp.json().get("status", "?")
                results["DART API"] = "OK" if status == "000" else f"WARN - status={status}"
            except Exception as e:
                results["DART API"] = f"FAIL - {e}"
        else:
            results["DART API"] = "SKIP - DART_API_KEY 없음"

        # 결과 로그 출력
        ok_count  = sum(1 for v in results.values() if v.startswith("OK"))
        fail_count = sum(1 for v in results.values() if v.startswith("FAIL"))
        log.info("─── API Health Check ───────────────────────────────")
        for name, status in results.items():
            icon = "✓" if status.startswith("OK") else ("✗" if status.startswith("FAIL") else "–")
            log.info(f"  {icon} {name}: {status}")
        log.info(f"────────────────────── OK={ok_count} / FAIL={fail_count} ──")

        # 텔레그램으로 헬스체크 결과 전송
        lines = [f"{'paper' if self.is_paper else 'live'} 봇 시작 — API 점검 결과"]
        for name, status in results.items():
            icon = "✅" if status.startswith("OK") else ("❌" if status.startswith("FAIL") else "➖")
            lines.append(f"{icon} {name}: {status}")
        if fail_count:
            lines.append(f"\n⚠️ {fail_count}개 API 연결 실패 — 확인 필요")
        send("\n".join(lines))

    # ── 시장별 예산 조회 ──────────────────────────────────────────────────────

    def _market_budget_available(self, market: str) -> float:
        """해당 시장에 추가로 배분 가능한 잔여 예산 (원)"""
        budget = self.kr_budget if market == "KR" else self.us_budget
        invested = sum(
            p["entry"] * p["qty"]
            for p in self.risk.positions
            if self._ticker_market(p["ticker"]) == market
        )
        return max(0.0, budget - invested)

    # ── 포지션 영속성 ──────────────────────────────────────────────────────────

    def _save_positions(self):
        """이월 포지션을 파일에 저장 (봇 재시작 복구용)"""
        carry = [p for p in self.risk.positions if p.get("max_hold", 1) > 1]
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(carry, f, ensure_ascii=False, indent=2, default=str)
        if carry:
            log.info(f"[포지션 저장] {[p['ticker'] for p in carry]} → {POSITIONS_FILE}")

    def _restore_positions(self):
        """저장된 이월 포지션 복구"""
        if not POSITIONS_FILE.exists():
            return
        try:
            with open(POSITIONS_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            if not saved:
                return

            if not self.is_paper:
                saved = self._verify_live_positions(saved)

            for pos in saved:
                # date 필드가 문자열로 저장됐을 수 있으므로 타입 보정
                pos.setdefault("held_days", 0)
                pos.setdefault("entry_date", date.today().isoformat())
                cost = pos["entry"] * pos["qty"]
                self.risk.cash -= cost
                self.risk.positions.append(pos)

            log.info(f"[포지션 복구] {[p['ticker'] for p in saved]} "
                     f"(총 {len(saved)}개 / 현금 잔여 {self.risk.cash:,.0f}원)")
        except Exception as e:
            log.error(f"포지션 복구 실패: {e}")

    def _verify_live_positions(self, saved: list) -> list:
        """실계좌: 브로커 잔고와 저장 포지션 비교 → 실제 보유 중인 것만 유지"""
        try:
            bal = get_balance(self.token, market="KR")
            broker = {s["ticker"]: s for s in bal["stocks"]}
            verified = []
            for pos in saved:
                tk = pos["ticker"]
                if tk in broker:
                    # 수량 불일치 시 브로커 수량으로 보정
                    if broker[tk]["qty"] != pos["qty"]:
                        log.warning(f"[브로커 동기화] {tk} 수량 불일치 "
                                    f"저장={pos['qty']} 브로커={broker[tk]['qty']} → 보정")
                        pos["qty"] = broker[tk]["qty"]
                    verified.append(pos)
                else:
                    log.warning(f"[브로커 동기화] {tk} 브로커에 없음 → 포지션 제거")
            return verified
        except Exception as e:
            log.error(f"브로커 동기화 실패: {e} → 저장 포지션 그대로 사용")
            return saved

    def _refresh_token(self):
        self.token = get_access_token()

    def _ticker_market(self, ticker: str) -> str:
        return "US" if ticker.isalpha() else "KR"

    def _in_entry_blackout(self, market: str) -> bool:
        now = datetime.now(KST).time()
        no_new = HARD_RULES["no_new_entry_min"]
        no_late = HARD_RULES["close_before_min"]

        if market == "KR":
            open_t = dt_time(9, 0)
            close_t = dt_time(16, 0)
        else:
            # US session in KST: 22:30 ~ 05:00(next day)
            open_t = dt_time(22, 30)
            close_t = dt_time(5, 0)

        if market == "KR":
            start_block_end = (datetime.combine(date.today(), open_t).timestamp() + no_new * 60)
            end_block_start = (datetime.combine(date.today(), close_t).timestamp() - no_late * 60)
            now_ts = datetime.combine(date.today(), now).timestamp()
            return now_ts < start_block_end or now_ts > end_block_start

        # US crossing midnight
        now_dt = datetime.now(KST)
        open_dt = datetime.combine(now_dt.date(), open_t, tzinfo=KST)
        close_dt = datetime.combine(now_dt.date(), close_t, tzinfo=KST)
        if now < close_t:
            open_dt = open_dt - timedelta(days=1)
        else:
            close_dt = close_dt + timedelta(days=1)

        start_block_end = open_dt.timestamp() + no_new * 60
        end_block_start = close_dt.timestamp() - no_late * 60
        now_ts = now_dt.timestamp()
        return now_ts < start_block_end or now_ts > end_block_start

    def _process_exit_candidates(self):
        candidates = self.risk.get_exit_candidates()
        for cand in candidates:
            market = self._ticker_market(cand["ticker"])
            if not self.is_paper:
                result = place_order(cand["ticker"], cand["qty"], 0, "sell", self.token, market=market)
                if not result["success"]:
                    log.error(f"sell order failed [{cand['ticker']}]: {result['msg']}")
                    continue

            ex = self.risk.close_position(cand["ticker"], cand["exit_price"], cand["reason"])
            if not ex:
                continue
            pnl_alert(ex["ticker"], ex["pnl_pct"], int(ex["pnl"]), ex["reason"])
            trade_alert("sell", ex["ticker"], ex["qty"], int(ex["exit_price"]), ex["strategy"], 0, 0, reason=ex["reason"])
            # 전략별 성과 brain 업데이트
            try:
                BrainDB.update_strategy_performance(
                    market, ex.get("strategy", "unknown"),
                    ex.get("pnl_pct", 0), ex.get("pnl", 0) > 0
                )
            except Exception as e:
                log.warning(f"strategy brain update failed: {e}")

    def session_open(self, market: str):
        log.info("=" * 50)
        log.info(f"[{market}] session_open")

        if market == "US" and not self.is_paper:
            log.warning("[US] live mode is not supported yet. session skipped.")
            self.session_active = False
            self.current_market = None
            return

        self._refresh_token()
        self.session_active = True
        self.current_market = market
        self.tuning_count = 0
        self.risk.reset_daily_state(clear_trade_log=True)
        self.risk.increment_holding_days()

        # 이월 포지션 현재가 갱신 (어제 종가 → 오늘 시가 방향으로 업데이트)
        tickers_in_market = KR_TICKERS if market == "KR" else US_TICKERS
        for pos in self.risk.positions:
            if pos["ticker"] in tickers_in_market:
                try:
                    price_info = get_price(pos["ticker"], self.token, market=market)
                    self.price_cache[pos["ticker"]] = price_info["price"]
                except Exception as e:
                    log.warning(f"이월 포지션 시가 조회 실패 [{pos['ticker']}]: {e}")
        self.risk.update_prices(self.price_cache)
        if self.risk.positions:
            log.info(f"[이월 포지션 현재가 갱신] "
                     f"{[(p['ticker'], p['current_price']) for p in self.risk.positions]}")

        today = date.today().strftime("%Y-%m-%d")
        digest = build_kr_digest(today) if market == "KR" else build_us_digest(today)
        digest_prompt = digest_to_prompt(digest)

        brain_summary = BrainDB.generate_prompt_summary(market)
        brain_data = BrainDB.load()
        correction = json.dumps(brain_data.get("correction_guide", {}).get(market, {}), ensure_ascii=False)

        judgments = get_three_judgments(digest_prompt, brain_summary, correction)
        consensus = build_consensus(judgments)
        self.today_judgment = {
            "date": today,
            "market": market,
            "judgments": judgments,
            "consensus": consensus,
            "digest_prompt": digest_prompt,
        }

        try:
            balance = get_balance(self.token, market=market)
        except Exception as e:
            log.warning(f"balance lookup failed [{market}]: {e}")
            balance = {"stocks": [], "total_eval": 0, "cash": 0, "total_profit": 0, "profit_rate": 0.0}
        morning_briefing(market, judgments, consensus, balance, digest)
        log.info(f"consensus: {consensus['mode']} size={consensus['size']}%")

        tickers = KR_TICKERS if market == "KR" else US_TICKERS
        self.ws = KISWebSocket(self.token, tickers, on_tick=self._on_tick, market=market)
        self.ws.start()

    def _on_tick(self, data: dict):
        ticker = data["ticker"]
        price = data["price"]
        self.price_cache[ticker] = price
        self.risk.update_prices(self.price_cache)
        self._process_exit_candidates()

    def run_cycle(self, market: str):
        if not self.session_active:
            return
        if self.current_market != market:
            return
        if self.risk.check_halt():
            return

        mode = self.today_judgment.get("consensus", {}).get("mode", "CAUTIOUS")
        size_pct = self.today_judgment.get("consensus", {}).get("size", 50)
        tickers = KR_TICKERS if market == "KR" else US_TICKERS

        for ticker in tickers:
            try:
                price_info = get_price(ticker, self.token, market=market)
                price = price_info["price"]
                self.price_cache[ticker] = price
                self.risk.update_prices(self.price_cache)
                self._process_exit_candidates()

                if mode == "HALT":
                    continue
                if self._in_entry_blackout(market):
                    continue

                ok, _ = self.risk.can_open(ticker, price, size_pct)
                if not ok:
                    continue

                # 시장별 예산 초과 확인
                avail = self._market_budget_available(market)
                if avail < price:  # 최소 1주 살 여유도 없으면 건너뜀
                    log.debug(f"[{market}] 예산 소진 (잔여 {avail:,.0f}원 < {price:,.0f}원) → 신규진입 스킵")
                    continue

                candles = get_daily_ohlcv(ticker, self.token, lookback_days=180, market=market)
                if candles.empty:
                    continue
                sig_df = calc_all(candles)
                if sig_df.empty:
                    continue
                i = len(sig_df) - 1

                signal_fired = False
                strategy_name = ""
                params = {}

                if market == "KR":
                    gap_p = gap_params(mode)
                    if gap_sig(sig_df, i, gap_p):
                        signal_fired = True
                        strategy_name = "gap_pullback"
                        params = gap_p
                    else:
                        mom_p = mom_params(mode)
                        if mom_sig(sig_df, i, mom_p):
                            signal_fired = True
                            strategy_name = "momentum"
                            params = mom_p
                        else:
                            mr_p = mr_params(mode)
                            if mr_sig(sig_df, i, mr_p):
                                signal_fired = True
                                strategy_name = "mean_reversion"
                                params = mr_p
                else:
                    vb_p = vb_params(mode)
                    if vb_sig(sig_df, i, vb_p):
                        signal_fired = True
                        strategy_name = "volatility_breakout"
                        params = vb_p

                if signal_fired and mode not in ("HALT", "DEFENSIVE"):
                    sl_cap = abs(HARD_RULES["max_single_loss_pct"]) / 100.0
                    sl_pct = min(params.get("sl_pct", 0.03), sl_cap)
                    # mean_reversion: BB 중선(ma20)을 TP로 사용
                    if strategy_name == "mean_reversion" and params.get("tp_bb_mid"):
                        bb_mid = float(sig_df.iloc[i].get("ma20", price))
                        tp_pct = max((bb_mid - price) / price, 0.005) if bb_mid > price else 0.03
                    else:
                        tp_pct = params.get("tp_pct", HARD_RULES["take_profit_pct"] / 100.0)
                    qty = self.risk.calc_order_size(price, size_pct, sl_pct)

                    if self.is_paper:
                        log.info(f"[PAPER BUY] {ticker} {qty}@{price:,}")
                    else:
                        result = place_order(ticker, qty, 0, "buy", self.token, market=market)
                        if not result["success"]:
                            log.error(f"order failed [{ticker}]: {result['msg']}")
                            continue

                    tp = int(price * (1 + tp_pct))
                    sl = int(price * (1 - sl_pct))
                    self.risk.open_position(ticker, price, qty, strategy_name, tp_pct, sl_pct, params.get("max_hold", 1))
                    trade_alert("buy", ticker, qty, price, strategy_name, tp, sl)

            except Exception as e:
                log.error(f"cycle error [{ticker}]: {e}")

    def run_tuning(self, market: str):
        if not self.session_active:
            return
        if self.current_market != market:
            return

        self.tuning_count += 1
        elapsed = self.tuning_count * 30

        current_state = {
            "index_change": 0,
            "volume_trend": "normal",
            "positions": [
                {
                    "ticker": p["ticker"],
                    "qty": p["qty"],
                    "current_price": p["current_price"],
                }
                for p in self.risk.positions
            ],
            "alerts": [],
        }
        brain_summary = BrainDB.generate_prompt_summary(market)
        result = tune(market, elapsed, current_state, self.today_judgment, brain_summary)

        if result.get("action") != "MAINTAIN":
            old_mode = self.today_judgment["consensus"]["mode"]
            self.today_judgment["consensus"]["mode"] = result.get("mode", old_mode)
            sl_adj = result.get("sl_adj", 0)
            if sl_adj != 0:
                for pos in self.risk.positions:
                    pos["sl"] = pos["sl"] * (1 + sl_adj)
                log.info(f"SL adjusted: {sl_adj:+.3f}")

        tuning_report(elapsed, result, self.today_judgment["consensus"]["mode"], self.risk.positions)

    def session_close(self, market: str):
        log.info(f"[{market}] session_close")
        if self.current_market != market:
            return

        self.session_active = False
        self.current_market = None
        if self.ws:
            self.ws.stop()
            self.ws = None

        # ── 포지션 정리: 당일 청산 전략만 강제 청산, 멀티데이는 이월 ─────────
        day_trades  = [p for p in list(self.risk.positions) if p.get("max_hold", 1) <= 1]
        multi_days  = [p for p in list(self.risk.positions) if p.get("max_hold", 1) > 1]

        for pos in day_trades:
            cp = self.price_cache.get(pos["ticker"], pos["current_price"])
            if not self.is_paper:
                result = place_order(pos["ticker"], pos["qty"], 0, "sell",
                                     self.token, market=market)
                if not result["success"]:
                    log.error(f"force sell failed [{pos['ticker']}]: {result['msg']}")
            ex = self.risk.close_position(pos["ticker"], cp, "session_close")
            if ex:
                pnl_alert(ex["ticker"], ex["pnl_pct"], int(ex["pnl"]), "session_close")
                trade_alert("sell", ex["ticker"], ex["qty"], int(cp),
                            ex["strategy"], 0, 0, reason="session_close")
            log.warning(f"[당일청산] {pos['ticker']} {cp:,.0f}")

        if multi_days:
            log.info(f"[이월] {[p['ticker'] for p in multi_days]} "
                     f"→ 다음 세션 계속 보유 (held_days: "
                     f"{[p['held_days'] for p in multi_days]})")

        # 이월 포지션 파일 저장 (재시작 대비)
        self._save_positions()

        today = date.today().strftime("%Y-%m-%d")
        actual = {
            "market_change": 0,
            "pnl_pct": self.risk.daily_pnl / self.risk.init_cash * 100,
            "pnl_krw": int(self.risk.daily_pnl),
            "win": self.risk.daily_pnl > 0,
            "trades": len(self.risk.trade_log),
            "cumulative": int(self.risk.equity()),
        }

        pm = run_postmortem(
            market,
            today,
            self.today_judgment,
            actual,
            self.today_judgment.get("digest_prompt", ""),
        )

        record = {
            **self.today_judgment,
            "actual_result": actual,
            "postmortem": pm,
            "trades": self.risk.trade_log,
            "mode": "paper" if self.is_paper else "live",
        }
        path = JUDGMENT_DIR / f"{today.replace('-', '')}_{market}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        try:
            balance = get_balance(self.token, market=market)
        except Exception as e:
            log.warning(f"balance lookup failed [{market}]: {e}")
            balance = {"stocks": [], "total_eval": 0, "cash": 0, "total_profit": 0, "profit_rate": 0.0}
        sell_trades = [t for t in self.risk.trade_log if t.get("side") == "sell"]
        wins = sum(1 for t in sell_trades if t.get("pnl", 0) > 0)
        win_rate = wins / len(sell_trades) if sell_trades else 0.0
        daily_summary(
            today,
            market,
            actual["pnl_pct"],
            actual["pnl_krw"],
            actual["trades"],
            win_rate,
            actual["cumulative"],
            self.today_judgment.get("judgments", {}),
            pm,
        )
        log.info(f"[{market}] summary | {actual['pnl_pct']:+.2f}% | {actual['pnl_krw']:+,}")



def main(is_paper: bool = True):
    bot = TradingBot(is_paper=is_paper)
    log.info("=== Trading Bot Start ===")

    schedule.every().day.at("08:50").do(bot.session_open, "KR")
    schedule.every().day.at("16:00").do(bot.session_close, "KR")
    schedule.every().day.at("22:20").do(bot.session_open, "US")
    schedule.every().day.at("05:00").do(bot.session_close, "US")

    def kr_cycle():
        bot.run_cycle("KR")

    def us_cycle():
        bot.run_cycle("US")

    schedule.every(5).minutes.do(kr_cycle)
    schedule.every(5).minutes.do(us_cycle)

    def kr_tune():
        bot.run_tuning("KR")

    def us_tune():
        bot.run_tuning("US")

    schedule.every(30).minutes.do(kr_tune)
    schedule.every(30).minutes.do(us_tune)

    log.info("schedules registered")
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="live mode")
    parser.add_argument("--paper", action="store_true", help="paper mode (default)")
    args = parser.parse_args()

    is_paper = not args.live
    if not is_paper:
        confirm = input("Live mode. continue? (yes/no): ")
        if confirm.lower() != "yes":
            print("cancelled")
            sys.exit(0)

    main(is_paper=is_paper)
