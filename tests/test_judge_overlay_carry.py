"""judge overlay 이월 회귀 테스트.

2026-07-29 실측: judge가 BUY_READY를 내도 rescreen이 다음 사이클에 덮어써서
trade_ready 승격이 되지 않았다. selection meta 저장소가 둘로 갈려 있었기 때문이다.

    _LAST_SELECTION_META (모듈 전역, analysts.py) ← selection 함수만 기록
    self.selection_meta[market]                  ← single_symbol_judge overlay가 쌓임

rescreen은 meta_override 없이 _apply_selection_meta를 부르므로 모듈 전역만 읽었고,
judge overlay는 통째로 사라졌다.

실측 근거(2026-07-28 US): judge BUY_READY 12건 중 승격 3건뿐.
살아남은 ACN·PAY·RGEN은 selection이 원래 알던 종목이라 모듈 전역에도 있었다.
decision_id 유무와의 상관(9/9)은 인과가 아니라 그 사실의 부산물이었다.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import trading_bot  # noqa: E402
from trading_bot import KST  # noqa: E402

JUDGE_SRC = "single_symbol_judge_v1"


def _bot(prev_actions):
    bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
    bot.selection_meta = {"US": {"candidate_actions": list(prev_actions)}}
    bot._runtime_float = lambda key, default: float(default)
    return bot


def _action(ticker, *, minutes_ago=3, source=JUDGE_SRC, action="BUY_READY"):
    created = datetime.now(KST).replace(tzinfo=None) - timedelta(minutes=minutes_ago)
    return {
        "ticker": ticker,
        "market": "US",
        "action": action,
        "source_prompt_id": source,
        "created_at": created.isoformat(timespec="seconds"),
        "price_targets": {"sell_target": 1.0, "stop_loss": 0.9},
    }


def _carry(bot, raw_meta):
    return trading_bot.TradingBot._carry_judge_overlay_actions(bot, "US", raw_meta)


def _tickers(meta):
    return [str(a.get("ticker") or "") for a in (meta.get("candidate_actions") or [])]


class JudgeOverlayCarryTests(unittest.TestCase):
    def test_fresh_judge_overlay_is_carried(self) -> None:
        """★ 회귀 방지 — rescreen이 judge BUY_READY를 지우면 안 된다."""
        bot = _bot([_action("FRESHTK", minutes_ago=3)])
        out = _carry(bot, {"watchlist": ["OTHER"],
                           "candidate_actions": [{"ticker": "OTHER", "action": "WATCH"}]})
        self.assertIn("FRESHTK", _tickers(out), "judge overlay가 이월되지 않았다")
        self.assertIn("FRESHTK", out.get("watchlist") or [],
                      "watchlist에 없으면 라우팅 대상에서 빠진다")

    def test_stale_overlay_is_dropped(self) -> None:
        """TTL(기본 20분)을 넘긴 판단은 이월하지 않는다 — stale 진입 방지."""
        bot = _bot([_action("STALETK", minutes_ago=60)])
        out = _carry(bot, {"watchlist": [], "candidate_actions": []})
        self.assertNotIn("STALETK", _tickers(out))

    def test_selection_origin_actions_not_carried(self) -> None:
        """selection이 만든 action은 이월 대상이 아니다(judge overlay만)."""
        bot = _bot([_action("SELTK", source="selection_v1")])
        out = _carry(bot, {"watchlist": [], "candidate_actions": []})
        self.assertNotIn("SELTK", _tickers(out))

    def test_existing_actions_preserved(self) -> None:
        """rescreen이 새로 만든 action은 그대로 유지된다."""
        bot = _bot([_action("FRESHTK")])
        out = _carry(bot, {"watchlist": ["OTHER"],
                           "candidate_actions": [{"ticker": "OTHER", "action": "WATCH"}]})
        self.assertIn("OTHER", _tickers(out))

    def test_judge_wins_on_same_ticker(self) -> None:
        """같은 종목이 양쪽에 있으면 더 최신인 judge 판단을 쓴다."""
        bot = _bot([_action("DUP", action="BUY_READY")])
        out = _carry(bot, {"watchlist": ["DUP"],
                           "candidate_actions": [{"ticker": "DUP", "action": "WATCH"}]})
        actions = {str(a.get("ticker")): str(a.get("action"))
                   for a in (out.get("candidate_actions") or [])}
        self.assertEqual(actions.get("DUP"), "BUY_READY")

    def test_no_overlay_is_noop(self) -> None:
        """이월할 judge overlay가 없으면 입력을 그대로 돌려준다."""
        bot = _bot([])
        raw = {"watchlist": ["X"], "candidate_actions": [{"ticker": "X", "action": "WATCH"}]}
        self.assertEqual(_carry(bot, raw), raw)


if __name__ == "__main__":
    unittest.main()
