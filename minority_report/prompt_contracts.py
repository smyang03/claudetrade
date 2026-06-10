"""Shared Claude prompt contracts for trading decisions.

These strings intentionally stay plain ASCII so they can be safely embedded in
existing prompts that already mix Korean and English text.
"""

COMMON_DECISION_CONTRACT = """Decision contract:
- You are a decision-support model for an automated trading system.
- You do not execute orders and you do not decide final order quantity.
- Use only the supplied market, candidate, and position data.
- Do not invent tickers, prices, volume, support/resistance, or unavailable facts.
- If a required data point is missing, use null or "unknown" instead of guessing.
- Catastrophic system rules are deterministic and cannot be relaxed by you.
- When the system asks for AUTO_SELL_REVIEW, reviewable risk exits may be re-judged from fresh evidence.
- Price, allocation, hold, and carry outputs are advisory inputs only.
- The system decides final order budget, quantity, broker checks, and forced exits.
- Return strict JSON only when a JSON schema is requested."""

SIZING_DECISION_CONTRACT = """Sizing contract:
- suggested_size_pct is market-level analyst intent used by the consensus engine.
- max_position_pct is a legacy per-candidate maximum order cap, not final position size.
- max_order_cap_pct is the preferred per-candidate cap against the system order budget.
- risk_budget_pct is the suggested capital-at-risk budget, not order notional.
- allocation_intent must be one of probe, small, normal, aggressive.
- The system applies cash, fixed_order_krw, mode_size_pct, ATR scaling, minimum order, position limits, broker state, and hard risk gates before submitting any quantity."""

SELECTION_EXECUTION_PHASE_CONTRACT = """Selection/execution-plan phases:
1. selection_rank_v3: rank candidates into WATCH and TRADE_READY using setup quality and execution feasibility.
2. execution_plan_v1: create price_targets only for TRADE_READY names.
Do not add price_targets for watch-only names."""

# buy zone 깊이 규칙 — selection price_targets와 single_symbol_judge 양대 PathB 플랜 경로가 공유.
# 값(-0.5%) 변경 시 이 상수 한 곳만 수정한다 (2026-06-10 운영자 승인값).
PULLBACK_ZONE_RULE = (
    "Pullback entry rule: buy_zone_high must sit at least 0.5% BELOW the current price. "
    "A zone that fills immediately at the current price is a chase entry, not a pullback plan."
)

PRICE_PLAN_CONTRACT = f"""Price-plan contract:
- Use native market prices: KR=KRW, US=USD.
- Do not fabricate support/resistance, VWAP, opening range, or ATR-derived levels when the input does not contain enough evidence.
- Required long setup order: stop_loss < buy_zone_low <= buy_zone_high < sell_target.
- Hard minimum reward/risk is 1.5; the system rejects plans below 1.5.
- {PULLBACK_ZONE_RULE}
  Anchor the zone to real support evidence (VWAP, open anchor, opening-range retest), not to the live price.
- cancel_if_open_above is a chase-prevention price.
- target_basis must identify the evidence used; invalid_if must state the setup failure condition."""

HARD_SOFT_RULE_CONTRACT = """Hard/soft rule boundary:
- Hard rules owned by the system: daily loss limit, broker-truth distrust, unconfirmed orders, market-close forced liquidation, max position limits, cash shortage, minimum order, and bad data quality.
- Reviewable risk exits during AUTO_SELL_REVIEW: loss_cap, stop_loss, hard_stop, trail_stop, profit_floor, and profit_ladder.
- Soft areas where Claude may advise: target trailing, pre-close carry exception, soft-exit recheck, candidate risk cap, and price-plan proposal.
- A Claude HOLD never overrides catastrophic, broker-truth, emergency, or operator-kill conditions.
- For reviewable risk exits, HOLD is valid only as bounded advice with protective_stop, invalid_if, and next_review_min."""
