"""tools/prompt_eval_improve.py

DB 실제 결과를 기반으로 analysts.py R1 프롬프트를 평가·개선하고,
Sonnet vs Haiku 응답을 비교한다.

사용법:
  python tools/prompt_eval_improve.py
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
for _env in (".env.live", ".env.paper", ".env"):
    if Path(_env).exists():
        load_dotenv(_env)
        break
import anthropic

EVAL_MODEL   = "claude-sonnet-4-6"
SONNET_MODEL = "claude-sonnet-4-6"
HAIKU_MODEL  = "claude-haiku-4-5-20251001"

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

STANCES = "AGGRESSIVE|MODERATE_BULL|MILD_BULL|CAUTIOUS|NEUTRAL|MILD_BEAR|CAUTIOUS_BEAR|DEFENSIVE|HALT"

# ── 현재 프롬프트 핵심부 ─────────────────────────────────────────────────────
CURRENT_PERSONAS = {
    "bull": """당신은 15년 경력의 성장주 모멘텀 트레이더입니다.

[전문 영역 — 이 지표들을 우선 확인]
• RSI 과매도(30 이하) 반등 신호
• MACD 골든크로스 or 히스토그램 상향 전환
• 거래량 평균 대비 1.5배 이상 급증
• 볼린저밴드 하단 터치 후 반등
• 52주 신고가 근접 (5% 이내)

[판단 기준]
• 개별 종목의 위 신호 2개 이상은 해당 종목 모멘텀 근거일 뿐, 시장 MODERATE_BULL의 충분조건이 아님
• 시장 MODERATE_BULL 이상은 breadth 요약(상승 비율, GC/DC, 섹터 확산) 또는 지수/섹터 확인이 동반될 때만
• 신호 1개 + 시장 분위기 양호 → MILD_BULL
• 기술적 신호 없음 → NEUTRAL 이하

[절대 하지 말 것]
• 환율·VIX만을 이유로 하락 판단 금지 (매크로는 참고만)
• HALT 판단은 시장 전체 서킷브레이커 상황에서만
• 근거 없이 confidence 0.5 이하 부여 금지""",

    "bear_us": """당신은 미국 주식 헤지펀드 리스크 매니저입니다.

[전문 영역 — US 리스크 축을 우선 확인]
• VIX 수준/변화: 결측이면 calm으로 해석하지 말고 data_quality 불확실성으로 처리
• HYG 하락, TNX 급등, DXY 급등 같은 credit/rate/USD 스트레스
• SPY/QQQ/IWM 및 섹터 ETF(XLK/XLF/XLE 등) 약세 확산
• breadth 악화: 상승 비율 하락, GC/DC 악화, RSI 과매수 과포화 후 둔화

[판단 기준]
• VIX/HYG/TNX/DXY 중 2개 이상 위험 신호 + breadth 악화 → CAUTIOUS_BEAR 이하
• VIX 25 이상 또는 HYG 급락 + 지수 약세 → DEFENSIVE 검토
• 대형주 일부 과매수만 있고 breadth가 양호하면 CAUTIOUS 이상으로 과도 하향 금지

[절대 하지 말 것]
• KR 지표(VKOSPI, 외국인 선물)로 US Bear 판단 금지
• 개별 기술주 1~3개의 과매수만으로 시장 전체 HALT 판단 금지""",

    "neutral": """당신은 퀀트 통계 분석가입니다.

[전문 영역 — 이 관점에서 분석]
• 제공된 breadth 요약의 상승/하락 신호 개수 대비 비교
• 지표 간 상충 여부 (기술적 긍정 + 매크로 부정 → 불확실)
• 데이터 신뢰도 검증 (데이터 누락시 불확실성 증가)

[판단 기준]
• 상승/하락 신호 균등 → 반드시 NEUTRAL
• 한쪽으로 2:1 이상 기울 때만 MILD_BULL or MILD_BEAR
• 극단 판단(AGGRESSIVE, HALT) 원칙적 금지""",
}

CURRENT_SYSTEM_CONTRACTS = """Decision contract:
- You are a decision-support model for an automated trading system.
- Use only the supplied market, candidate, and position data.
- Return strict JSON only when a JSON schema is requested.

Hard/soft rule boundary:
- Hard rules owned by the system: daily loss limit, broker-truth distrust, max position limits.
- Soft areas where Claude may advise: market stance, target trailing, candidate risk cap.

[시장 breadth 우선 계약]
• 시장 mode는 먼저 breadth 요약과 지수/매크로/섹터 흐름으로 판단하세요.
• 개별 종목 1~3개만으로 시장 mode를 결정하지 마세요."""

CURRENT_DATA_GUIDE = """[데이터 해석 가이드]
• 코스피/SPY 등: "1d X% / 5d Y%" 형태 — 1d는 전일 대비, 5d는 주간 추세
• USD/KRW: 1d 음수 = KRW 강세(위험 완화), 양수 = KRW 약세(위험)
• VIX 결측: 안정 신호가 아닌 data_quality 불확실성
• 외국인/기관 N/A: 데이터 없음. 0과 다름. 판단 유보"""

def build_current_prompt(analyst_type: str, digest: str, brain_summary: str = "") -> str:
    persona = CURRENT_PERSONAS.get(analyst_type, CURRENT_PERSONAS["neutral"])
    return f"""{persona}

{CURRENT_SYSTEM_CONTRACTS}
{CURRENT_DATA_GUIDE}

[시장 전체 메모리]
{brain_summary or "(없음)"}

[오늘 시장 데이터]
{digest}

위 데이터를 당신의 전문 영역 관점에서 분석하세요. 반드시 트렌드 수치(1d/5d)를 근거로 언급하세요.
JSON으로만 응답 (다른 텍스트 없이):
{{"stance":"{STANCES} 중 하나","confidence":0.0~1.0,
  "key_reason":"핵심 근거 한 문장 (구체적 지표 수치 포함)"}}"""


def load_samples(path: str = "state/prompt_eval_samples.json") -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _direction_label(avg_pnl: float | None) -> str:
    if avg_pnl is None:
        return "N/A"
    return "BULLISH" if avg_pnl > 0.2 else "BEARISH" if avg_pnl < -0.2 else "FLAT"


def _is_bull_stance(stance: str) -> bool:
    return stance in {"AGGRESSIVE", "MODERATE_BULL", "MILD_BULL", "CAUTIOUS"}


def _hit(consensus: str, avg_pnl: float | None) -> str | None:
    if avg_pnl is None:
        return None
    return "HIT" if _is_bull_stance(consensus) == (avg_pnl > 0) else "MISS"


# ── Step 1: 현재 프롬프트를 Claude(평가자)가 평가 ──────────────────────────
def evaluate_current_prompt(samples: list[dict]) -> dict:
    print("\n[1단계] 현재 프롬프트 품질 평가 중...")

    sample_summary = []
    for s in samples[:15]:
        if s.get("avg_pnl") is None:
            continue
        hit = _hit(s["consensus_mode"], s["avg_pnl"])
        sample_summary.append({
            "date": s["date"],
            "consensus": s["consensus_mode"],
            "bull": s.get("bull_stance"),
            "bear": s.get("bear_stance"),
            "neut": s.get("neut_stance"),
            "avg_pnl_pct": round(s["avg_pnl"], 3) if s["avg_pnl"] else None,
            "outcome_direction": _direction_label(s.get("avg_pnl")),
            "directional_hit": hit,
            "bull_reason": (s.get("bull_reason") or "")[:80],
            "bear_reason": (s.get("bear_reason") or "")[:80],
        })

    hits = [x for x in sample_summary if x["directional_hit"] == "HIT"]
    misses = [x for x in sample_summary if x["directional_hit"] == "MISS"]

    eval_prompt = f"""당신은 자동매매 AI 시스템의 프롬프트 품질 평가 전문가입니다.

아래는 Claude R1(시장 방향성 판단) 프롬프트의 구조입니다:

=== 현재 bull 분석가 프롬프트 핵심 ===
{CURRENT_PERSONAS['bull'][:600]}

=== 현재 시스템 계약 ===
{CURRENT_SYSTEM_CONTRACTS[:400]}

=== 실제 판단 기록 ({len(sample_summary)}개 세션, US 시장) ===
적중 {len(hits)}건 / 실패 {len(misses)}건 / 무역 세션 제외

판단 기록:
{json.dumps(sample_summary, ensure_ascii=False, indent=2)}

위 데이터를 분석하여 다음을 JSON으로 응답하세요:
{{
  "hit_rate": "XX/YY",
  "main_failure_patterns": ["패턴1", "패턴2", "패턴3"],
  "bull_prompt_weaknesses": ["약점1", "약점2"],
  "bear_prompt_weaknesses": ["약점1", "약점2"],
  "neutral_prompt_weaknesses": ["약점1"],
  "key_improvement_areas": ["개선점1", "개선점2", "개선점3"],
  "evaluation_summary": "전체 평가 2-3문장"
}}"""

    resp = client.messages.create(
        model=EVAL_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": eval_prompt}],
    )
    raw = resp.content[0].text.strip()
    print(f"  토큰: in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
    result = _parse_json_response(raw)
    result["_sample_summary"] = sample_summary
    return result


def _parse_json_response(raw: str) -> dict:
    import re
    # ```json ... ``` 블록 우선 추출
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        # 중괄호 범위 추출
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


# ── Step 2: 개선된 프롬프트 생성 ─────────────────────────────────────────────
def generate_improved_prompt(eval_result: dict) -> dict:
    print("\n[2단계] 개선된 프롬프트 생성 중...")

    weaknesses = json.dumps({
        "main_failure_patterns": eval_result.get("main_failure_patterns", []),
        "bull_weaknesses": eval_result.get("bull_prompt_weaknesses", []),
        "bear_weaknesses": eval_result.get("bear_prompt_weaknesses", []),
        "neutral_weaknesses": eval_result.get("neutral_prompt_weaknesses", []),
        "key_improvement_areas": eval_result.get("key_improvement_areas", []),
    }, ensure_ascii=False)

    improve_prompt = f"""당신은 자동매매 AI 프롬프트 개선 전문가입니다.

=== 현재 프롬프트 ===

[Bull 분석가]
{CURRENT_PERSONAS['bull']}

[Bear 분석가 (US)]
{CURRENT_PERSONAS['bear_us']}

[Neutral 분석가]
{CURRENT_PERSONAS['neutral']}

[시스템 계약]
{CURRENT_SYSTEM_CONTRACTS}

[데이터 가이드]
{CURRENT_DATA_GUIDE}

=== 평가 결과 (DB 실제 성과 기반) ===
{weaknesses}

=== 제약사항 ===
- 프롬프트는 한국어 유지
- stance는 반드시 AGGRESSIVE|MODERATE_BULL|MILD_BULL|CAUTIOUS|NEUTRAL|MILD_BEAR|CAUTIOUS_BEAR|DEFENSIVE|HALT 중 하나
- 출력 JSON 형식 유지: {{"stance": "...", "confidence": 0.0~1.0, "key_reason": "..."}}
- 하드 리스크 룰(daily loss limit, broker-truth 등)은 Claude가 override 불가 — 변경하지 말 것
- 각 분석가 persona를 완전히 교체하지 말고 개선에 집중
- Haiku(소형 모델)도 올바르게 작동해야 하므로 지시가 명확하고 간결해야 함

다음 JSON 형식으로 개선된 프롬프트를 생성하세요:
{{
  "bull_persona_improved": "개선된 bull 분석가 프롬프트 전문",
  "bear_us_persona_improved": "개선된 bear(US) 분석가 프롬프트 전문",
  "neutral_persona_improved": "개선된 neutral 분석가 프롬프트 전문",
  "system_contracts_improved": "개선된 시스템 계약",
  "data_guide_improved": "개선된 데이터 해석 가이드",
  "key_changes": ["변경사항1", "변경사항2", "변경사항3"],
  "rationale": "개선 근거 설명"
}}"""

    resp = client.messages.create(
        model=EVAL_MODEL,
        max_tokens=3000,
        messages=[{"role": "user", "content": improve_prompt}],
    )
    raw = resp.content[0].text.strip()
    print(f"  토큰: in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
    try:
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        result = json.loads(m.group(0)) if m else {"raw": raw}
    except Exception:
        result = {"raw": raw}
    return result


def build_improved_prompt(improved: dict, analyst_type: str, digest: str, brain_summary: str = "") -> str:
    if analyst_type == "bull":
        persona = improved.get("bull_persona_improved", CURRENT_PERSONAS["bull"])
    elif analyst_type == "bear_us":
        persona = improved.get("bear_us_persona_improved", CURRENT_PERSONAS["bear_us"])
    else:
        persona = improved.get("neutral_persona_improved", CURRENT_PERSONAS["neutral"])

    contracts = improved.get("system_contracts_improved", CURRENT_SYSTEM_CONTRACTS)
    data_guide = improved.get("data_guide_improved", CURRENT_DATA_GUIDE)

    return f"""{persona}

{contracts}
{data_guide}

[시장 전체 메모리]
{brain_summary or "(없음)"}

[오늘 시장 데이터]
{digest}

위 데이터를 당신의 전문 영역 관점에서 분석하세요. 반드시 트렌드 수치(1d/5d)를 근거로 언급하세요.
JSON으로만 응답 (다른 텍스트 없이):
{{"stance":"{STANCES} 중 하나","confidence":0.0~1.0,
  "key_reason":"핵심 근거 한 문장 (구체적 지표 수치 포함)"}}"""


# ── Step 3: Sonnet vs Haiku 비교 ─────────────────────────────────────────────
def compare_sonnet_haiku(improved: dict, samples: list[dict], n_test: int = 6) -> list[dict]:
    print(f"\n[3단계] Sonnet vs Haiku 비교 ({n_test}개 세션)...")

    test_samples = [s for s in samples if s.get("digest") and len(s["digest"]) > 400][:n_test]
    results = []

    for i, s in enumerate(test_samples):
        digest = s["digest"]
        analyst_types = ["bull", "bear_us", "neutral"]
        row: dict[str, Any] = {
            "date": s["date"],
            "actual_consensus": s["consensus_mode"],
            "actual_pnl": s.get("avg_pnl"),
            "sonnet": {},
            "haiku": {},
        }

        for atype in analyst_types:
            old_prompt = build_current_prompt(atype, digest)
            new_prompt = build_improved_prompt(improved, atype, digest)

            for label, model, prompt in [
                ("sonnet_old", SONNET_MODEL, old_prompt),
                ("sonnet_new", SONNET_MODEL, new_prompt),
                ("haiku_old",  HAIKU_MODEL,  old_prompt),
                ("haiku_new",  HAIKU_MODEL,  new_prompt),
            ]:
                try:
                    resp = client.messages.create(
                        model=model,
                        max_tokens=300,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    raw = resp.content[0].text.strip()
                    import re
                    m = re.search(r"\{.*\}", raw, re.DOTALL)
                    parsed = json.loads(m.group(0)) if m else {}
                    row.setdefault(label, {})[atype] = {
                        "stance": parsed.get("stance", "?"),
                        "confidence": parsed.get("confidence"),
                        "key_reason": (parsed.get("key_reason") or "")[:80],
                        "tokens": resp.usage.output_tokens,
                    }
                except Exception as e:
                    row.setdefault(label, {})[atype] = {"error": str(e)[:60]}

        results.append(row)
        pnl_str = f"{s['avg_pnl']:+.2f}%" if s.get("avg_pnl") is not None else "N/A"
        print(f"  [{i+1}/{n_test}] {s['date']} pnl={pnl_str} 완료")

    return results


def print_comparison_report(eval_result: dict, improved: dict, comparisons: list[dict]) -> None:
    print("\n" + "="*70)
    print("📊 프롬프트 평가 결과")
    print("="*70)

    print(f"\n적중률: {eval_result.get('hit_rate', 'N/A')}")
    print(f"평가 요약: {eval_result.get('evaluation_summary', '')}")

    print("\n[주요 실패 패턴]")
    for p in eval_result.get("main_failure_patterns", []):
        print(f"  • {p}")

    print("\n[개선 영역]")
    for k in eval_result.get("key_improvement_areas", []):
        print(f"  • {k}")

    print("\n" + "="*70)
    print("🔧 개선된 프롬프트 주요 변경사항")
    print("="*70)
    for c in improved.get("key_changes", []):
        print(f"  • {c}")
    print(f"\n근거: {improved.get('rationale', '')}")

    print("\n" + "="*70)
    print("⚖️  Sonnet vs Haiku 비교")
    print("="*70)

    for comp in comparisons:
        pnl_str = f"{comp['actual_pnl']:+.2f}%" if comp.get("actual_pnl") is not None else "N/A"
        print(f"\n[{comp['date']}] 실제합의={comp['actual_consensus']} pnl={pnl_str}")

        for atype in ["bull", "bear_us", "neutral"]:
            s_old = comp.get("sonnet_old", {}).get(atype, {})
            s_new = comp.get("sonnet_new", {}).get(atype, {})
            h_old = comp.get("haiku_old", {}).get(atype, {})
            h_new = comp.get("haiku_new", {}).get(atype, {})
            print(f"  {atype:8s}:")
            print(f"    Sonnet  현재→개선: {s_old.get('stance','?')}({s_old.get('confidence','-')}) → {s_new.get('stance','?')}({s_new.get('confidence','-')})")
            print(f"    Haiku   현재→개선: {h_old.get('stance','?')}({h_old.get('confidence','-')}) → {h_new.get('stance','?')}({h_new.get('confidence','-')})")


def main() -> None:
    samples = load_samples("state/prompt_eval_samples.json")
    print(f"샘플 {len(samples)}개 로드")

    # 1. 현재 프롬프트 평가
    eval_result = evaluate_current_prompt(samples)
    print(f"\n평가 결과: {eval_result.get('hit_rate')} | {eval_result.get('evaluation_summary','')[:100]}")

    # 2. 개선된 프롬프트 생성
    improved = generate_improved_prompt(eval_result)
    print(f"변경사항: {improved.get('key_changes', [])}")

    # 3. Sonnet vs Haiku 비교
    comparisons = compare_sonnet_haiku(improved, samples, n_test=5)

    # 4. 보고서 출력
    print_comparison_report(eval_result, improved, comparisons)

    # 결과 저장
    out = {
        "eval_result": eval_result,
        "improved_prompt": improved,
        "comparisons": comparisons,
    }
    out_path = Path("state/prompt_improvement_result.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()
