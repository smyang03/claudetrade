"""
sector_play.py
Tier 2 섹터 플레이 — ETF 강세 신호 시 on-demand 종목 판단

흐름:
  1. digest context의 sectors dict에서 ETF 등락률 확인
  2. 임계값(ETF_THRESHOLD) 초과 시 → 해당 섹터 후보 종목 활성화
  3. yfinance로 실시간 가격 + 단기 데이터 수집
  4. Claude(Haiku) 판단 → 간단한 매수/스킵 결정
  5. trading_bot이 50% 사이즈로 주문

섹터 맵:
  XLF → JPM, GS         (금융)
  XLE → XOM, CVX        (에너지)
  XLV → LLY, ABBV       (헬스케어)
  XLI → CAT, GE         (산업재)
  XLC → GOOGL, NFLX     (통신서비스 — Core와 겹치므로 낮은 우선순위)
"""

import os
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_trainer_logger

log = get_trainer_logger()

try:
    import yfinance as yf
    _YF_OK = True
except ImportError:
    _YF_OK = False

# ── 설정 ──────────────────────────────────────────────────────────────────────

# ETF 등락률 임계값 (%): 이 이상 움직인 섹터만 Tier 2 활성화
ETF_THRESHOLD = float(os.getenv("SECTOR_ETF_THRESHOLD", "1.5"))

# Tier 2 포지션 사이즈 (Core 대비 절반)
TIER2_SIZE_RATIO = float(os.getenv("TIER2_SIZE_RATIO", "0.5"))

# 섹터 ETF → 후보 종목 매핑
SECTOR_MAP: dict[str, list[str]] = {
    "XLF": ["JPM", "GS"],    # 금융
    "XLE": ["XOM", "CVX"],   # 에너지
    "XLV": ["LLY", "ABBV"],  # 헬스케어
    "XLI": ["CAT", "GE"],    # 산업재
    # XLC(통신)는 Core 5(GOOGL/NFLX)와 겹치므로 비활성화
}

# 섹터 ETF 한글명
SECTOR_NAMES: dict[str, str] = {
    "XLF": "금융",
    "XLE": "에너지",
    "XLV": "헬스케어",
    "XLI": "산업재",
}


def _etf_prev_day_chg(etf: str) -> float:
    """yfinance로 ETF 전일 등락률 조회 (모멘텀 연속성 확인용)"""
    if not _YF_OK:
        return 0.0
    try:
        hist = yf.Ticker(etf).history(period="3d", interval="1d", auto_adjust=True)
        if len(hist) < 2:
            return 0.0
        # 가장 최근 2행: [-2]가 전일, [-1]이 당일
        prev_close = float(hist["Close"].iloc[-2])
        prev_prev  = float(hist["Close"].iloc[-3]) if len(hist) >= 3 else prev_close
        return (prev_close - prev_prev) / prev_prev * 100 if prev_prev else 0.0
    except Exception:
        return 0.0


def get_active_sectors(sectors: dict) -> list[tuple[str, list[str], float]]:
    """
    섹터 ETF dict에서 임계값 초과 + 모멘텀 연속성 확인 후 활성 섹터 반환.

    필터 조건:
      1. 당일 등락 ≥ ETF_THRESHOLD (1.5%)
      2. 단일 스파이크 제거: 당일 등락 > 8%면 이미 추격 위험 → 제외
      3. 모멘텀 연속성: 전일도 같은 방향 ≥ 0.3% 이면 우선 (아니면 낮은 가중치)

    Returns:
        [(etf, [ticker1, ticker2], change_pct), ...]
        연속성 점수 기준 정렬
    """
    MAX_SPIKE = float(os.getenv("SECTOR_MAX_SPIKE_PCT", "8.0"))

    active = []
    for etf, candidates in SECTOR_MAP.items():
        chg = sectors.get(etf, 0) or 0
        if abs(chg) < ETF_THRESHOLD:
            continue
        # 단일 스파이크 제거 — 하루에 8%+ 움직임은 추격 위험
        if abs(chg) > MAX_SPIKE:
            log.info(f"[sector_play] {etf} {chg:+.2f}% — 단일 스파이크 제외 (>{MAX_SPIKE}%)")
            continue
        # 전일 방향 확인 (연속성)
        prev_chg = _etf_prev_day_chg(etf)
        continuous = (chg > 0 and prev_chg > 0.3) or (chg < 0 and prev_chg < -0.3)
        # 연속성 없어도 진입은 하되 점수 낮춰 우선순위 뒤로
        score = abs(chg) * (1.3 if continuous else 0.8)
        if continuous:
            log.info(f"[sector_play] {etf} {chg:+.2f}% (전일 {prev_chg:+.2f}%) ✓ 연속 모멘텀")
        else:
            log.info(f"[sector_play] {etf} {chg:+.2f}% (전일 {prev_chg:+.2f}%) — 단일 이벤트 가능성")
        active.append((etf, candidates, chg, score))

    active.sort(key=lambda x: -x[3])
    # score 제거 후 반환 (기존 인터페이스 유지)
    return [(etf, cands, chg) for etf, cands, chg, _ in active]


def fetch_ticker_snapshot(ticker: str) -> Optional[dict]:
    """
    yfinance로 티커 스냅샷 수집 (5일 히스토리 + 프리마켓).
    실패 시 None 반환.
    """
    if not _YF_OK:
        return None
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="5d", interval="1d", auto_adjust=True)
        if hist.empty or len(hist) < 2:
            return None

        closes = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()
        change_pct = (closes[-1] - closes[-2]) / closes[-2] * 100 if closes[-2] else 0
        vol_ratio  = volumes[-1] / (sum(volumes[:-1]) / max(len(volumes) - 1, 1)) if volumes else 1.0

        info = tk.fast_info
        premarket = getattr(info, "pre_market_price", None)
        premarket_pct = None
        if premarket and closes[-1]:
            premarket_pct = (premarket - closes[-1]) / closes[-1] * 100

        return {
            "ticker":        ticker,
            "close":         round(closes[-1], 2),
            "change_pct":    round(change_pct, 2),
            "vol_ratio":     round(vol_ratio, 2),
            "closes_5d":     [round(c, 2) for c in closes],
            "premarket_pct": round(premarket_pct, 2) if premarket_pct is not None else None,
        }
    except Exception as e:
        log.warning(f"[sector_play] {ticker} 스냅샷 실패: {e}")
        return None


def build_sector_prompt(
    ticker: str,
    snap: dict,
    etf: str,
    etf_chg: float,
    market_mode: str,
    digest_summary: str = "",
) -> str:
    """
    Tier 2 섹터 플레이용 Claude 프롬프트 생성.
    digest_summary: 오늘의 시장 요약 (선택)
    """
    sector_name = SECTOR_NAMES.get(etf, etf)
    lines = [
        f"## Tier 2 섹터 플레이 판단 요청",
        f"",
        f"섹터ETF: {etf}({sector_name}) {etf_chg:+.2f}% → 임계값({ETF_THRESHOLD}%) 초과",
        f"시장모드: {market_mode}",
        f"",
        f"### {ticker} 현재 상태",
        f"- 종가: ${snap['close']}",
        f"- 금일 등락: {snap['change_pct']:+.2f}%",
        f"- 거래량 비율(5일평균 대비): {snap['vol_ratio']:.2f}x",
        f"- 5일 종가 추이: {snap['closes_5d']}",
    ]
    if snap.get("premarket_pct") is not None:
        lines.append(f"- 프리마켓: {snap['premarket_pct']:+.2f}%")
    if digest_summary:
        lines += ["", "### 오늘 시장 요약", digest_summary]

    lines += [
        "",
        "### 판단 지침",
        f"- 섹터 ETF({etf})가 {etf_chg:+.2f}%로 강하게 움직였습니다.",
        f"- {ticker}이 섹터 모멘텀에 올라탈 가능성이 있는지 판단하세요.",
        "- 포지션 사이즈는 Core 종목의 50%만 사용합니다.",
        "- BUY 또는 SKIP 중 하나로만 답하세요.",
        "",
        "### 응답 형식 (JSON)",
        '{"action": "BUY" | "SKIP", "confidence": 0.0~1.0, "reason": "한 줄 이유"}',
    ]
    return "\n".join(lines)


def evaluate_sector_play(
    ticker: str,
    snap: dict,
    etf: str,
    etf_chg: float,
    market_mode: str,
    digest_summary: str = "",
) -> Optional[dict]:
    """
    Claude Haiku로 Tier 2 섹터 플레이 판단.
    Returns {"action": "BUY"|"SKIP", "confidence": float, "reason": str} or None
    """
    try:
        import anthropic
        from minority_report.analysts import R1_MODEL  # Haiku 사용

        prompt = build_sector_prompt(
            ticker, snap, etf, etf_chg, market_mode, digest_summary
        )
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=R1_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # JSON 파싱
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(raw[start:end])
            result.setdefault("action", "SKIP")
            result.setdefault("confidence", 0.5)
            result.setdefault("reason", "")
            return result
    except Exception as e:
        log.warning(f"[sector_play] {ticker} Claude 판단 실패: {e}")
    return None


def run_sector_plays(
    sectors: dict,
    market_mode: str,
    digest_summary: str = "",
    max_plays: int = 2,
) -> list[dict]:
    """
    활성 섹터에서 BUY 신호 종목 목록 반환.

    Args:
        sectors:        digest context의 sectors dict
        market_mode:    오늘 시장 모드 (AGGRESSIVE, MODERATE_BULL 등)
        digest_summary: 간략한 시장 요약 텍스트
        max_plays:      최대 Tier 2 종목 수 (기본 2)

    Returns:
        [{"ticker": ..., "etf": ..., "etf_chg": ..., "confidence": ...,
          "reason": ..., "size_ratio": TIER2_SIZE_RATIO}, ...]
    """
    # 약세 모드에서는 Tier 2 비활성화
    _BEAR_MODES = {"CAUTIOUS_BEAR", "MILD_BEAR", "DEFENSIVE", "HALT"}
    if market_mode in _BEAR_MODES:
        log.info(f"[sector_play] {market_mode} — Tier 2 비활성화")
        return []

    active = get_active_sectors(sectors)
    if not active:
        return []

    results = []
    for etf, candidates, etf_chg in active:
        if len(results) >= max_plays:
            break
        sector_name = SECTOR_NAMES.get(etf, etf)
        log.info(
            f"[sector_play] {etf}({sector_name}) {etf_chg:+.2f}% "
            f"→ 후보: {candidates}"
        )
        for ticker in candidates:
            if len(results) >= max_plays:
                break
            snap = fetch_ticker_snapshot(ticker)
            if snap is None:
                log.warning(f"[sector_play] {ticker} 스냅샷 없음 — 스킵")
                continue
            judgment = evaluate_sector_play(
                ticker, snap, etf, etf_chg, market_mode, digest_summary
            )
            if judgment is None:
                continue
            log.info(
                f"[sector_play] {ticker} → {judgment['action']} "
                f"(conf={judgment['confidence']:.2f}) {judgment['reason']}"
            )
            if judgment["action"] == "BUY" and judgment["confidence"] >= 0.55:
                results.append({
                    "ticker":     ticker,
                    "etf":        etf,
                    "etf_chg":    etf_chg,
                    "confidence": judgment["confidence"],
                    "reason":     judgment["reason"],
                    "size_ratio": TIER2_SIZE_RATIO,
                    "snap":       snap,
                })

    return results
