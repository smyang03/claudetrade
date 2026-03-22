"""
historical_sim.py - Phase 1 역사 데이터 학습 엔진

날짜별 순서대로:
  1. digest 생성
  2. Claude 3명 판단
  3. 실제 결과 확인
  4. postmortem 생성
  5. brain.json 업데이트
  6. 판단 기록 저장 (daily_judgment)

실행:
  python historical_sim.py --market KR --start 2024-10-01 --end 2026-03-19
  python historical_sim.py --market US --start 2025-01-01 --end 2026-03-19
  python historical_sim.py --market ALL  (국내+미국 전체)
"""

import os, sys, json, time, argparse
import pandas as pd
import numpy as np
import anthropic
from pathlib import Path
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_trainer_logger, log_call, ProgressLogger
from minority_report.consensus import build_consensus as runtime_build_consensus
from phase1_trainer.digest_builder import (
    build_kr_digest, build_us_digest,
    load_digest, digest_to_prompt
)
from runtime_paths import get_runtime_path

sys.path.insert(0, str(Path(__file__).parent.parent / "claude_memory"))
import brain as BrainDB

log         = get_trainer_logger()
client      = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY",""))
BASE_DIR    = Path(__file__).parent.parent
PRICE_DIR   = BASE_DIR / "data" / "price"
JUDGMENT_DIR= get_runtime_path("logs", "daily_judgment", make_parents=False)
JUDGMENT_DIR.mkdir(parents=True, exist_ok=True)

CLAUDE_MODEL = "claude-sonnet-4-6"

# ── 합의 룰 ───────────────────────────────────────────────────────────────────

def get_consensus(bull_stance: str, bear_stance: str, neut_stance: str,
                  market: str = "KR") -> dict:
    return runtime_build_consensus(
        {
            "bull":    {"stance": bull_stance,  "confidence": 0.5, "key_reason": ""},
            "bear":    {"stance": bear_stance,  "confidence": 0.5, "key_reason": ""},
            "neutral": {"stance": neut_stance,  "confidence": 0.5, "key_reason": ""},
        },
        check_minority=False,
        market=market,
    )


# ── Claude API 호출 ───────────────────────────────────────────────────────────

def call_claude_analyst(
    analyst_type: str,
    digest_prompt: str,
    brain_summary: str,
    correction_guide: str,
) -> dict:
    """
    Claude에게 단일 분석가 역할로 판단 요청
    analyst_type: 'bull' | 'bear' | 'neutral'
    """
    persona = {
        "bull":    "당신은 낙관적 관점의 주식 분석가입니다. 긍정적 신호를 우선적으로 포착하고 상승 기회를 찾습니다.",
        "bear":    "당신은 비관적/리스크 중심 분석가입니다. 위험 요소와 하락 가능성을 우선적으로 포착합니다.",
        "neutral": "당신은 객관적/중립적 분석가입니다. 긍정과 부정을 균형 있게 판단하고 불확실성을 인정합니다.",
    }[analyst_type]

    stances = "AGGRESSIVE | MODERATE_BULL | MILD_BULL | NEUTRAL | MILD_BEAR | CAUTIOUS_BEAR | DEFENSIVE | HALT"

    prompt = f"""{persona}

{brain_summary}

{correction_guide}

오늘 시장 데이터:
{digest_prompt}

위 데이터를 분석하여 오늘 매매 전략을 판단하세요.
반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이:
{{
  "stance": "{stances} 중 하나",
  "confidence": 0.0~1.0,
  "key_reason": "핵심 판단 근거 한 문장",
  "full_reasoning": "상세 분석 2~3문장",
  "top_risks": ["위험요소1", "위험요소2"],
  "suggested_strategy": "모멘텀|평균회귀|갭+눌림|변동성돌파|관망"
}}"""

    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            messages=[{"role":"user","content":prompt}]
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json","").strip()
        result = json.loads(raw)
        log.debug(f"  [{analyst_type}] {result.get('stance','-')} "
                  f"conf={result.get('confidence',0):.2f} | {result.get('key_reason','')[:50]}")
        return result
    except Exception as e:
        log.error(f"Claude [{analyst_type}] 오류: {e}")
        return {
            "stance": "NEUTRAL",
            "confidence": 0.3,
            "key_reason": f"API 오류: {str(e)[:50]}",
            "full_reasoning": "",
            "top_risks": [],
            "suggested_strategy": "관망"
        }


def call_claude_postmortem(
    judgment: dict,
    actual_result: dict,
    digest_prompt: str,
    brain_summary: str,
) -> dict:
    """
    장 마감 후 사후 분석 (postmortem)
    판단 vs 실제 결과 비교 → brain 업데이트 지침 생성
    """
    prompt = f"""당신은 주식 트레이딩 AI 시스템의 성과 분석가입니다.

오늘 판단:
  Bull:    {judgment['bull']['stance']} / {judgment['bull']['key_reason']}
  Bear:    {judgment['bear']['stance']} / {judgment['bear']['key_reason']}
  Neutral: {judgment['neutral']['stance']} / {judgment['neutral']['key_reason']}
  합의:    {judgment['consensus']['mode']}

실제 결과:
  시장 등락: {actual_result.get('market_change',0):+.2f}%
  매매 손익: {actual_result.get('pnl_pct',0):+.2f}%
  승패: {'승' if actual_result.get('win') else '패'}

오늘 시장 데이터 요약:
{digest_prompt[:500]}

{brain_summary[:300]}

아래 JSON으로만 응답하세요:
{{
  "bull_result": "HIT|MISS|PARTIAL",
  "bear_result": "HIT|MISS|PARTIAL",
  "neutral_result": "HIT|MISS|PARTIAL",
  "bull_why": "Bull 판단이 맞/틀린 이유 한 문장",
  "bear_why": "Bear 판단이 맞/틀린 이유 한 문장",
  "neutral_why": "Neutral 판단 평가 한 문장",
  "key_lesson": "오늘 학습한 가장 중요한 교훈 한 문장",
  "issue_type": "이슈 유형 (예: 개별기업_확정호재, 정책불확실성, 수급이상, 거시이벤트)",
  "issue_description": "이슈 상세 설명 한 문장",
  "pattern_id": "기존 패턴 ID 또는 null",
  "brain_updates": {{
    "bull_reliability_change": "up|down|stable",
    "bear_reliability_change": "up|down|stable",
    "new_lesson": "brain에 추가할 교훈 또는 null",
    "market_regime": "강세장|약세장|횡보|변동성장 중 하나"
  }}
}}"""

    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            messages=[{"role":"user","content":prompt}]
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json","").strip()
        return json.loads(raw)
    except Exception as e:
        log.error(f"postmortem 오류: {e}")
        win = actual_result.get("win", False)
        return {
            "bull_result":    "HIT" if win else "MISS",
            "bear_result":    "MISS" if win else "HIT",
            "neutral_result": "PARTIAL",
            "bull_why":       "자동 판정",
            "bear_why":       "자동 판정",
            "neutral_why":    "자동 판정",
            "key_lesson":     "API 오류로 자동 판정",
            "issue_type":     "미분류",
            "issue_description": "",
            "pattern_id":     None,
            "brain_updates":  {
                "bull_reliability_change": "stable",
                "bear_reliability_change": "stable",
                "new_lesson": None,
                "market_regime": "unknown",
            }
        }


# ── 실제 결과 계산 ────────────────────────────────────────────────────────────

def calc_actual_result(market: str, target_date: str, mode: str) -> dict:
    """
    해당 날짜의 실제 시장 결과 계산
    주가 데이터에서 당일 등락률 추출
    """
    from phase1_trainer.digest_builder import KR_TICKERS, US_TICKERS, load_price_with_cache

    tickers = KR_TICKERS if market == "KR" else US_TICKERS
    changes = []

    for ticker in (tickers.keys() if isinstance(tickers, dict) else tickers):
        df = load_price_with_cache(market, ticker)
        if df.empty:
            continue
        # date 컬럼이 Timestamp이면 Timestamp, string이면 string으로 비교
        target_ts = pd.Timestamp(target_date)
        if pd.api.types.is_datetime64_any_dtype(df["date"]):
            row = df[df["date"] == target_ts]
        else:
            row = df[df["date"] == target_date]
        if not row.empty:
            r = row.iloc[0]
            # change_pct 컬럼 우선, 없으면 change(절대값)로 계산
            if "change_pct" in r.index and pd.notna(r["change_pct"]):
                chg = float(r["change_pct"])
            elif "change" in r.index and pd.notna(r["change"]) and r["close"] != r["change"]:
                prev_close = r["close"] - r["change"]
                chg = float(r["change"] / prev_close * 100) if prev_close != 0 else 0.0
            else:
                continue
            changes.append(chg)

    if not changes:
        log.warning(f"실제 결과 데이터 없음: {market} {target_date}")
        return {"market_change": 0, "pnl_pct": 0, "win": False}

    avg_change = np.mean(changes)

    # 모의 손익 계산
    # HALT면 수익 0, AGGRESSIVE면 레버리지 효과
    mode_mult = {
        "AGGRESSIVE": 1.0, "MODERATE_BULL": 0.7,
        "MILD_BULL": 0.5, "NEUTRAL": 0.3,
        "MILD_BEAR": -0.3, "CAUTIOUS_BEAR": -0.5,
        "DEFENSIVE": -0.7, "HALT": 0.0, "CAUTIOUS": 0.4,
    }
    mult    = mode_mult.get(mode, 0.5)
    pnl_pct = avg_change * mult
    # 거래 비용 차감 (수수료 0.015% × 2)
    pnl_pct -= 0.03

    return {
        "market_change": round(avg_change, 3),
        "pnl_pct":       round(pnl_pct, 3),
        "win":           pnl_pct > 0,
        "trades":        1 if mode != "HALT" else 0,
        "cumulative":    0,  # 누적은 외부에서 계산
    }


# ── 단일 날짜 학습 ────────────────────────────────────────────────────────────

@log_call(logger=log, level="DEBUG")
def simulate_day(market: str, target_date: str, cumulative: float) -> dict:
    """
    하루치 학습 사이클
    1. digest 로드/생성
    2. brain 요약 로드
    3. Claude 3명 판단
    4. 합의
    5. 실제 결과 확인
    6. postmortem
    7. brain 업데이트
    8. 판단 기록 저장
    """
    # 1. Digest
    digest = load_digest(market, target_date)
    if not digest:
        if market == "KR":
            digest = build_kr_digest(target_date)
        else:
            digest = build_us_digest(target_date)

    digest_prompt = digest_to_prompt(digest)

    # 2. Brain 요약
    brain_summary    = BrainDB.generate_prompt_summary(market)
    brain_data       = BrainDB.load()
    correction_guide = json.dumps(
        brain_data.get("correction_guide", {}).get(market, {}),
        ensure_ascii=False
    )

    # 3. Claude 3명 판단 (순차 호출, API 레이트 제한 고려)
    log.debug(f"  [{target_date}] Claude 판단 요청 중...")
    bull = call_claude_analyst("bull", digest_prompt, brain_summary, correction_guide)
    time.sleep(1.5)
    bear = call_claude_analyst("bear", digest_prompt, brain_summary, correction_guide)
    time.sleep(1.5)
    neut = call_claude_analyst("neutral", digest_prompt, brain_summary, correction_guide)
    time.sleep(1.0)

    # 4. 합의
    consensus = get_consensus(
        bull.get("stance","NEUTRAL"),
        bear.get("stance","NEUTRAL"),
        neut.get("stance","NEUTRAL"),
    )

    # 5. 실제 결과
    actual = calc_actual_result(market, target_date, consensus["mode"])
    cumulative *= (1 + actual["pnl_pct"] / 100)
    actual["cumulative"] = round(cumulative)

    # 6. Postmortem
    log.debug(f"  [{target_date}] Postmortem 생성 중...")
    judgment = {"bull": bull, "bear": bear, "neutral": neut, "consensus": consensus}
    postmortem = call_claude_postmortem(judgment, actual, digest_prompt, brain_summary)
    time.sleep(1.0)

    # 7. Brain 업데이트
    recent = brain_data["markets"][market].get("recent_days", [])

    BrainDB.update_analyst(market, "bull",
        postmortem.get("bull_result","MISS") == "HIT", recent)
    BrainDB.update_analyst(market, "bear",
        postmortem.get("bear_result","MISS") == "HIT", recent)
    BrainDB.update_analyst(market, "neutral",
        postmortem.get("neutral_result","MISS") == "HIT", recent)
    BrainDB.update_mode_performance(
        market, consensus["mode"], actual["pnl_pct"], actual["win"])

    bu = postmortem.get("brain_updates", {})
    if bu.get("new_lesson"):
        BrainDB.update_beliefs(market, {"new_lesson": bu["new_lesson"]})
    if bu.get("market_regime"):
        BrainDB.update_beliefs(market, {"market_regime": bu["market_regime"]})

    issue = {
        "matched_id":   postmortem.get("pattern_id"),
        "type":         postmortem.get("issue_type","미분류"),
        "description":  postmortem.get("issue_description",""),
        "bull_hit":     postmortem.get("bull_result") == "HIT",
        "pnl_pct":      actual["pnl_pct"],
        "example":      f"{target_date}: {digest.get('top_news',[{}])[0].get('title','') if digest.get('top_news') else ''}",
        "insight":      postmortem.get("key_lesson",""),
    }
    BrainDB.update_issue_pattern(market, issue)

    daily_record = {
        "date":     target_date,
        "mode":     consensus["mode"],
        "pnl_pct":  actual["pnl_pct"],
        "win":      actual["win"],
        f"bull_result":   postmortem.get("bull_result","MISS"),
        f"bear_result":   postmortem.get("bear_result","MISS"),
        f"neutral_result":postmortem.get("neutral_result","MISS"),
        "bull_reason":   bull.get("key_reason",""),
        "bear_reason":   bear.get("key_reason",""),
        "market_change": actual.get("market_change",0),
    }
    BrainDB.add_daily_record(market, daily_record)

    # 8. 판단 기록 저장
    record = {
        "date":        target_date,
        "market":      market,
        "mode":        "historical_sim",
        "judgments":   {"bull": bull, "bear": bear, "neutral": neut},
        "consensus":   consensus,
        "actual_result": actual,
        "postmortem":  postmortem,
        "trades":      [],
    }
    dt_str = target_date.replace("-","")
    jpath  = JUDGMENT_DIR / f"{dt_str}_{market}.json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    log.info(
        f"  ✅ {target_date} {market} | "
        f"모드:{consensus['mode']} | "
        f"손익:{actual['pnl_pct']:+.2f}% | "
        f"Bull:{postmortem.get('bull_result','?')} "
        f"Bear:{postmortem.get('bear_result','?')} "
        f"Neut:{postmortem.get('neutral_result','?')}"
    )
    return record, cumulative


# ── 기간 전체 학습 ────────────────────────────────────────────────────────────

def run_simulation(
    market: str,
    start:  str,
    end:    str,
    init_cash: float = 10_000_000,
    resume: bool = True,
):
    """
    기간 전체 역사 학습 실행
    market: 'KR' | 'US'
    resume: True면 이미 학습된 날짜 건너뜀
    """
    log.info("=" * 60)
    log.info(f"  Phase 1 역사 학습 시작")
    log.info(f"  시장: {market} | 기간: {start} ~ {end}")
    log.info(f"  모델: {CLAUDE_MODEL}")
    log.info("=" * 60)

    start_dt = datetime.strptime(start, "%Y-%m-%d").date()
    end_dt   = datetime.strptime(end,   "%Y-%m-%d").date()
    current  = start_dt

    # 거래소 캘린더 기반 영업일 필터 (주말 + 공휴일 제외)
    try:
        import exchange_calendars as ec
        cal = ec.get_calendar("XKRX" if market == "KR" else "XNYS")
        biz_days = [
            d.strftime("%Y-%m-%d")
            for d in pd.date_range(start_dt, end_dt, freq="B")
            if cal.is_session(d.strftime("%Y-%m-%d"))
        ]
    except Exception:
        log.warning("exchange_calendars 없음 — weekday 기반 필터 사용")
        biz_days = []
        while current <= end_dt:
            if current.weekday() < 5:
                biz_days.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

    # API 비용 예상
    cost_per_day = 5 * 0.003  # 5회 호출 × $0.003
    est_cost     = len(biz_days) * cost_per_day
    est_min      = len(biz_days) * 0.5  # 일당 30초
    log.info(f"  예상: {len(biz_days)}일 | 비용 ${est_cost:.2f} | "
             f"소요 {est_min:.0f}분")

    prog       = ProgressLogger(len(biz_days), f"{market} 학습", log, interval=10)
    cumulative = init_cash
    success    = 0

    for day_str in biz_days:
        # 이미 학습된 날짜 건너뜀
        if resume:
            dt_str = day_str.replace("-","")
            jpath  = JUDGMENT_DIR / f"{dt_str}_{market}.json"
            if jpath.exists():
                log.debug(f"[SKIP] {day_str} (이미 학습됨)")
                prog.step(day_str, success=True)
                # 누적 자산 복원
                with open(jpath, encoding="utf-8") as f:
                    rec = json.load(f)
                cumulative = rec.get("actual_result",{}).get("cumulative", cumulative)
                success += 1
                continue

        try:
            _, cumulative = simulate_day(market, day_str, cumulative)
            success += 1
            prog.step(day_str, success=True)
        except Exception as e:
            log.error(f"[FAIL] {day_str}: {e}")
            prog.step(day_str, success=False)
            time.sleep(5)  # 오류 시 대기

    prog.done()

    # 최종 brain 상태 출력
    log.info("\n--- 최종 Brain 상태 ---")
    BrainDB.print_status()
    log.info(BrainDB.generate_prompt_summary(market))
    log.info(f"\n최종 누적 자산: {cumulative:,.0f}원 "
             f"({(cumulative/init_cash-1)*100:+.1f}%)")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase1 역사 학습")
    parser.add_argument("--market", default="KR", choices=["KR","US","ALL"])
    parser.add_argument("--start",  default="2025-06-01")
    parser.add_argument("--end",    default=date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--no-resume", action="store_true",
                        help="이미 학습된 날짜도 다시 학습")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY 없음. .env 파일을 확인하세요.")
        sys.exit(1)

    markets = ["KR","US"] if args.market == "ALL" else [args.market]
    for mkt in markets:
        run_simulation(
            market=mkt,
            start=args.start,
            end=args.end,
            resume=not args.no_resume,
        )
