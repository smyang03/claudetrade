"""
minority_report/hold_advisor.py — TP 도달 시 분석가 3명 HOLD/SELL 합의

TRAILING_ANALYST_ENABLED=true 일 때만 호출됨.
기본값 false → 트레일링 스탑 즉시 활성화.
"""
import os
import json
import time
import anthropic
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_trading_logger
from credit_tracker import record as credit_record
from runtime_paths import get_runtime_path
from minority_report.raw_call_logger import save as save_raw_call

log    = get_trading_logger()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL  = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

PERSONAS = {
    "bull": "당신은 15년 경력의 성장주 모멘텀 트레이더입니다. 추세가 살아있으면 보유를 선호합니다.",
    "bear": "당신은 헤지펀드 리스크 매니저입니다. 이익 실현 타이밍을 중시하고 욕심을 경계합니다.",
    "neutral": "당신은 퀀트 통계 분석가입니다. 데이터 기반으로 냉정하게 판단합니다.",
}


def _ask_one(analyst_type: str, pos: dict, market: str, digest_prompt: str) -> dict:
    # entry: open_positions(KRW) 우선, 없으면 display_avg_price(USD) 폴백
    entry = float(pos.get("entry", 0) or 0)
    if entry <= 0:
        entry = float(pos.get("avg_price", 0) or pos.get("display_avg_price", 0) or 0)
    if entry <= 0:
        raise ValueError(f"[hold_advisor] entry=0 — 진입가 미확정, 호출 불가 ({pos.get('ticker','-')})")

    # US: display_avg_price(USD) 기준으로 표시, KRW 환산값을 괄호에 병기
    # KR: KRW 단위 그대로 표시
    disp_entry = float(pos.get("display_avg_price", 0) or 0)
    disp_cp    = float(pos.get("display_current_price", 0) or 0)
    # USD/KRW 환율: entry(KRW) / disp_entry(USD)로 역산
    fx_rate = (entry / disp_entry) if (market == "US" and disp_entry > 0) else 1.0

    if market == "US" and disp_entry > 0:
        show_entry = disp_entry
        show_cp    = disp_cp if disp_cp > 0 else float(pos.get("current_price", entry) or entry) / fx_rate
        show_tp    = round(float(pos.get("tp", 0) or 0) / fx_rate, 2)
        show_sl    = round(float(pos.get("sl", 0) or 0) / fx_rate, 2)
        show_trail = round(float(pos.get("trail_sl", 0) or 0) / fx_rate, 2)
        ccy = "USD"
        # KRW 환산값 (괄호 병기용)
        krw_entry  = int(entry)
        krw_cp     = int(show_cp * fx_rate)
        krw_tp     = int(float(pos.get("tp", 0) or 0))
        krw_sl     = int(float(pos.get("sl", 0) or 0))
        krw_trail  = int(float(pos.get("trail_sl", 0) or 0))
        def _p(usd, krw): return f"${usd:,.2f} (≈{krw:,}원)" if krw > 0 else f"${usd:,.2f}"
    else:
        show_entry = entry
        show_cp    = float(pos.get("current_price", entry) or entry)
        show_tp    = float(pos.get("tp", 0) or 0)
        show_sl    = float(pos.get("sl", 0) or 0)
        show_trail = float(pos.get("trail_sl", 0) or 0)
        ccy = "KRW"
        krw_entry = krw_cp = krw_tp = krw_sl = krw_trail = 0
        def _p(val, _krw=0): return f"{val:,.0f}원"

    cp      = show_cp
    pnl_pct = (show_cp / show_entry - 1) * 100 if show_entry else 0
    ticker  = pos.get("ticker", "-")
    strat   = pos.get("strategy", "-")
    held    = pos.get("held_days", 0)
    tp      = show_tp
    sl      = show_sl
    trailing = bool(pos.get("trailing", False))
    trail_sl = show_trail
    tp_triggered = bool(pos.get("tp_triggered", False))
    status_bits = []
    if tp > 0:
        status_bits.append(f"TP={_p(tp, krw_tp)}")
    if sl > 0:
        status_bits.append(f"SL={_p(sl, krw_sl)}")
    if tp_triggered:
        status_bits.append("TP 도달 상태")
    if trailing:
        _tr_str = f"트레일링 활성(trail_sl={_p(trail_sl, krw_trail)})" if trail_sl > 0 else "트레일링 활성"
        status_bits.append(_tr_str)
    status_line = " / ".join(status_bits) if status_bits else "별도 TP/SL 상태 정보 없음"

    # 진입가/현재가 표시 (US: USD + KRW 병기)
    if market == "US":
        entry_str = _p(show_entry, krw_entry)
        cp_str    = _p(show_cp, krw_cp)
    else:
        entry_str = f"{show_entry:,.0f}원"
        cp_str    = f"{show_cp:,.0f}원"

    prompt = f"""{PERSONAS[analyst_type]}

목표가에 도달한 포지션을 계속 보유할지 판단하세요.

━━━ 포지션 ━━━
  종목: {ticker} ({market}, {ccy})  전략: {strat}
  진입가: {entry_str}  현재가: {cp_str}  수익률: {pnl_pct:+.2f}%
  보유일: {held}일
  포지션 상태: {status_line}

━━━ 시장 컨텍스트 ━━━
{digest_prompt[:250] if digest_prompt else "  (정보 없음)"}

HOLD(보유) 또는 SELL(청산) 중 하나를 선택하고,
HOLD 시 트레일링 폭(trail_pct: 0.02~0.05)을 제안하세요.

JSON으로만 응답:
{{
  "action": "HOLD" or "SELL",
  "confidence": 0.0~1.0,
  "trail_pct": 0.03,
  "reason": "한 문장"
}}"""

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=320,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        result = json.loads(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens, "hold_advisor")
        save_raw_call(
            label=f"hold_advisor_{analyst_type}",
            prompt=prompt, raw_response=raw, parsed=result,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market,
        )
        return {
            "action":     result.get("action", "SELL").upper(),
            "confidence": float(result.get("confidence", 0.5)),
            "trail_pct":  max(0.02, min(0.05, float(result.get("trail_pct", 0.03)))),
            "reason":     result.get("reason", ""),
        }
    except Exception as e:
        log.warning(f"[hold_advisor:{analyst_type}] 오류 → SELL 기본값: {e}")
        return {"action": "SELL", "confidence": 0.5, "trail_pct": 0.03, "reason": "오류"}


def ask(pos: dict, market: str, digest_prompt: str = "", delay: float = 0.5) -> dict:
    """
    분석가 3명 합의 → HOLD/SELL 결정.

    Returns
    -------
    {
        "action": "HOLD" | "SELL",
        "trail_pct": 0.03,
        "votes": {"bull": ..., "bear": ..., "neutral": ...},
    }
    """
    ticker  = pos.get("ticker", "-")

    # entry=0이면 Claude가 "데이터 오류"로 일관되게 SELL 판단 → 의미없는 호출 차단
    _entry = float(pos.get("entry", 0) or 0)
    if _entry <= 0:
        _entry = float(pos.get("avg_price", 0) or pos.get("display_avg_price", 0) or 0)
    if _entry <= 0:
        log.warning(f"[hold_advisor] {ticker} entry=0 → 호출 차단 (진입가 미확정), HOLD 반환")
        return {"action": "HOLD", "trail_pct": 0.03, "votes": {}}

    votes   = {}
    for atype in ("bull", "bear", "neutral"):
        votes[atype] = _ask_one(atype, pos, market, digest_prompt)
        time.sleep(delay)

    hold_score = sum(
        v["confidence"] for v in votes.values() if v["action"] == "HOLD"
    )
    sell_score = sum(
        v["confidence"] for v in votes.values() if v["action"] == "SELL"
    )
    action = "HOLD" if hold_score > sell_score else "SELL"

    # trail_pct: HOLD 투표한 분석가들의 평균
    hold_voters = [v for v in votes.values() if v["action"] == "HOLD"]
    trail_pct   = (
        sum(v["trail_pct"] for v in hold_voters) / len(hold_voters)
        if hold_voters else 0.03
    )

    log.info(
        f"[hold_advisor] {ticker} → {action} "
        f"(HOLD {hold_score:.2f} vs SELL {sell_score:.2f}) trail={trail_pct:.2f}"
    )

    # ── 결정 시점 JSONL 기록 ──────────────────────────────────────────────────
    _log_decision(ticker, market, pos, action, trail_pct, votes)

    return {"action": action, "trail_pct": round(trail_pct, 3), "votes": votes}


def _log_decision(ticker: str, market: str, pos: dict,
                  action: str, trail_pct: float, votes: dict):
    """hold_advisor 결정을 JSONL 파일에 기록"""
    try:
        log_dir = get_runtime_path("logs", "hold_advisor", make_parents=False)
        log_dir.mkdir(parents=True, exist_ok=True)
        today   = datetime.now().strftime("%Y-%m-%d")
        log_file = log_dir / f"decisions_{today}.jsonl"

        entry = {
            "ts":         datetime.now().isoformat(timespec="seconds"),
            "ticker":     ticker,
            "market":     market,
            "entry":      pos.get("entry", 0),
            "tp_price":   pos.get("tp", 0),
            "current":    pos.get("current_price", 0),
            "pnl_pct":    round((pos.get("current_price", 0) / pos.get("entry", 1) - 1) * 100, 3),
            "held_days":  pos.get("held_days", 0),
            "decision":   action,
            "trail_pct":  trail_pct,
            "votes": {k: {"action": v["action"], "confidence": v["confidence"],
                          "reason": v["reason"]} for k, v in votes.items()},
            "outcome":    None,   # 청산 후 채워짐
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[hold_advisor] 결정 로그 기록 실패: {e}")
