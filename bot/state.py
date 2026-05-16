"""
bot/state.py
영속성 저장/복구 — StateMixin
trading_bot.py에서 이동 (로직 변경 없음)
"""

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, time as dt_time
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - python<3.9 fallback
    from datetime import timezone

    class ZoneInfo:  # type: ignore
        def __new__(cls, _name: str):
            return timezone(timedelta(hours=9))

from runtime_paths import get_runtime_path
from logger import get_trading_logger

log = get_trading_logger()
KST = ZoneInfo("Asia/Seoul")

POSITIONS_FILE = get_runtime_path("state", "open_positions.json")
POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
PENDING_ORDERS_FILE = get_runtime_path("state", "pending_orders.json")
PENDING_ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
CLAUDE_CONTROL_FILE = get_runtime_path("state", "claude_control.json")
CLAUDE_CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
DAILY_BASELINE_FILE = get_runtime_path("state", "daily_baseline.json")


def _atomic_json_dump(path, payload, *, indent=2, default=str) -> None:
    """Write JSON through a temp file so readers never see a partial document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=indent, default=default)
    json.loads(text)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


class StateMixin:

    @property
    def _mode(self) -> str:
        """실행 모드 — 상태 파일 prefix로 사용 ('paper' | 'live')"""
        return "paper" if self.is_paper else "live"

    def _save_positions(self):
        """이월 포지션을 파일에 저장 (봇 재시작 복구용)
        max_hold>1 장기 보유 뿐 아니라 당일 미청산 포지션(max_hold==1)도 저장:
        VTS 잔고 API 미반영 시 재시작 후 복구 가능하도록.
        """
        carry = []
        for pos in list(self.risk.positions):  # 보유 중인 모든 포지션 저장
            if not isinstance(pos, dict):
                carry.append(pos)
                continue
            item = dict(pos)
            item["market"] = self._saved_position_market(item)
            carry.append(item)
        _path = get_runtime_path("state", f"{self._mode}_open_positions.json")
        _atomic_json_dump(_path, carry, indent=2, default=str)
        if carry:
            tickers = [p.get("ticker", "") if isinstance(p, dict) else str(p) for p in carry]
            log.info(f"[포지션 저장] {tickers} ({len(carry)}개) → {_path}")

    def _saved_position_market(self, pos: dict) -> str:
        raw_market = str((pos or {}).get("market") or "").strip().upper()
        if raw_market in {"KR", "US"}:
            return raw_market
        ticker = str((pos or {}).get("ticker") or "").strip()
        if ticker:
            resolver = getattr(self, "_ticker_market", None)
            if callable(resolver):
                try:
                    resolved = str(resolver(ticker) or "").strip().upper()
                    if resolved in {"KR", "US"}:
                        return resolved
                except Exception:
                    pass
            return "US" if ticker.upper().isalpha() else "KR"
        currency = str((pos or {}).get("display_currency") or "").strip().upper()
        if currency == "USD":
            return "US"
        return "KR"

    def _save_pending_orders(self):
        self._normalize_pending_orders()
        _path = get_runtime_path("state", f"{self._mode}_pending_orders.json")
        _atomic_json_dump(_path, self.pending_orders, indent=2, default=str)

    def _parse_pending_created_at(self, order: dict):
        raw = order.get("created_at", "")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None

    def _normalize_pending_orders(self):
        if not self.pending_orders:
            return
        today_kst = datetime.now(KST).date()
        latest_by_ticker = {}
        for order in self.pending_orders:
            ticker = (order.get("ticker", "") or "").strip()
            market = order.get("market", "")
            if not ticker or market not in ("KR", "US"):
                continue
            created_at = self._parse_pending_created_at(order)
            if created_at and created_at.astimezone(KST).date() < today_kst:
                continue
            key = (market, ticker.upper())
            prev = latest_by_ticker.get(key)
            if prev is None:
                latest_by_ticker[key] = order
                continue
            prev_dt = self._parse_pending_created_at(prev)
            cur_dt = created_at
            if prev_dt is None or (cur_dt is not None and cur_dt > prev_dt):
                latest_by_ticker[key] = order
        self.pending_orders = sorted(
            latest_by_ticker.values(),
            key=lambda o: self._parse_pending_created_at(o) or datetime.min.replace(tzinfo=KST)
        )

    def _restore_pending_orders(self):
        _path = get_runtime_path("state", f"{self._mode}_pending_orders.json")
        if not _path.exists():
            return
        try:
            with open(_path, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, list):
                self.pending_orders = saved
                self._normalize_pending_orders()
                self._save_pending_orders()
        except Exception as e:
            log.error(f"미체결 주문 복구 실패: {e}")

    def _load_daily_baselines(self):
        _path = get_runtime_path("state", f"{self._mode}_daily_baseline.json")
        if not _path.exists():
            return
        try:
            with open(_path, encoding="utf-8") as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                return
            normalized = {"KR": {}, "US": {}}
            for market in ("KR", "US"):
                meta = saved.get(market) or {}
                if not isinstance(meta, dict):
                    continue
                session_date = str(meta.get("session_date", "") or "").strip()
                base = float(meta.get("base", 0) or 0)
                source = str(meta.get("source", "") or "").strip()
                if session_date and base > 0:
                    normalized[market] = {
                        "session_date": session_date,
                        "base": base,
                        "source": source,
                    }
            self._daily_baseline_by_market = normalized
        except Exception as e:
            log.warning(f"daily baseline 복구 실패: {e}")

    def _save_daily_baselines(self):
        payload = {}
        for market in ("KR", "US"):
            meta = dict(self._daily_baseline_by_market.get(market, {}) or {})
            session_date = str(meta.get("session_date", "") or "").strip()
            base = float(meta.get("base", 0) or 0)
            source = str(meta.get("source", "") or "").strip()
            if session_date and base > 0:
                payload[market] = {
                    "session_date": session_date,
                    "base": base,
                    "source": source,
                }
            else:
                payload[market] = {}
        try:
            _path = get_runtime_path("state", f"{self._mode}_daily_baseline.json")
            with open(_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"daily baseline 저장 실패: {e}")

    def _default_claude_control(self) -> dict:
        return {
            "enabled": True,
            "updated_at": "",
            "updated_by": "default",
            "last_trigger_at": "",
            "last_trigger_market": "",
            "last_trigger_source": "",
            "last_result_at": "",
            "last_result_market": "",
            "last_result_status": "idle",
            "last_error": "",
            "pending_trigger": None,
            "pending_position_review": None,
            "pending_sell": None,
            "pending_stop_cluster_reset": None,
            "last_stop_cluster_reset_at": "",
            "last_stop_cluster_reset_market": "",
            "last_stop_cluster_reset_count_before": 0,
            "last_stop_cluster_reset_by": "",
            "last_stop_cluster_reset_reason": "",
        }

    def _save_claude_control(self):
        _path = get_runtime_path("state", f"{self._mode}_claude_control.json")
        with open(_path, "w", encoding="utf-8") as f:
            json.dump(self.claude_control, f, ensure_ascii=False, indent=2, default=str)

    def _normalize_claude_control_state(self, data: Optional[dict] = None) -> dict:
        control = self._default_claude_control()
        control.update(data or {})
        status = str(control.get("last_result_status", "idle") or "idle").lower()
        if status != "running":
            return control
        ts = control.get("last_result_at") or control.get("last_trigger_at") or ""
        if not ts:
            control["last_result_status"] = "error"
            control["last_error"] = control.get("last_error") or "이전 Claude 재판단이 비정상 종료되어 상태를 정리했습니다."
            return control
        try:
            last_dt = datetime.fromisoformat(ts)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=KST)
        except Exception:
            control["last_result_status"] = "error"
            control["last_error"] = control.get("last_error") or "이전 Claude 재판단 시각이 손상되어 상태를 정리했습니다."
            return control
        if datetime.now(KST) - last_dt > timedelta(minutes=10):
            control["last_result_status"] = "error"
            control["last_error"] = control.get("last_error") or "이전 Claude 재판단이 10분 이상 완료되지 않아 stale 상태로 정리했습니다."
        return control

    def _restore_claude_control(self):
        _path = get_runtime_path("state", f"{self._mode}_claude_control.json")
        data = self._default_claude_control()
        if _path.exists():
            try:
                with open(_path, encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    data.update(saved)
            except Exception as e:
                log.warning(f"Claude 제어 상태 복구 실패: {e}")
        data["enabled"] = True
        self.claude_control = self._normalize_claude_control_state(data)
        self._save_claude_control()

    def _refresh_claude_control(self):
        _path = get_runtime_path("state", f"{self._mode}_claude_control.json")
        if not _path.exists():
            self._restore_claude_control()
            return
        try:
            with open(_path, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                data = self._default_claude_control()
                data.update(saved)
                self.claude_control = self._normalize_claude_control_state(data)
                if self.claude_control != data:
                    self._save_claude_control()
        except Exception as e:
            log.warning(f"Claude 제어 상태 새로고침 실패: {e}")

    def _sanitize_live_status_file(self, market: str):
        path = get_runtime_path("state", f"{self._mode}_live_status_{market}.json")
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        changed = False
        positions = []
        for pos in data.get("positions", []) or []:
            ticker = str(pos.get("ticker", "") or "")
            if ticker and self._ticker_market(ticker) == market:
                positions.append(pos)
            else:
                changed = True
        pending_orders = []
        for order in data.get("pending_orders", []) or []:
            ticker = str(order.get("ticker", "") or "")
            order_market = str(order.get("market", "") or "")
            inferred_market = order_market or self._ticker_market(ticker)
            if ticker and inferred_market == market:
                order["market"] = market
                pending_orders.append(order)
            else:
                changed = True
        if data.get("market") != market:
            data["market"] = market
            changed = True
        if positions != (data.get("positions", []) or []):
            data["positions"] = positions
            data["position_count"] = len(positions)
            changed = True
        if pending_orders != (data.get("pending_orders", []) or []):
            data["pending_orders"] = pending_orders
            data["pending_count"] = len(pending_orders)
            changed = True
        if changed:
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
