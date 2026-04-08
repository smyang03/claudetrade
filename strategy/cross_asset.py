"""strategy/cross_asset.py - Cross-asset 지표 기반 전략 파라미터 자동 보정

VIX, USD/KRW 환율, 섹터 ETF 흐름을 반영하여
mode+confidence 기반 파라미터를 추가 보정한다.

보정 방향:
  - VIX < 15   : 저공포 구간 → vol_mult 완화 (-0.15) — 진입 기회 확대
  - VIX 15~20  : 정상 구간 → 보정 없음
  - VIX 20~25  : 주의 구간 → vol_mult 강화 (+0.15)
  - VIX 25~30  : 고변동 구간 → vol_mult 강화 (+0.30)
  - VIX >= 30  : 위기 구간 → vol_mult 강화 (+0.50)

  - USD/KRW >= 1500: KR vol_mult 추가 강화 (+0.20)
  - USD/KRW >= 1450: KR vol_mult 추가 강화 (+0.10)

  - 섹터 ETF 강한 상승(+1% 이상): 해당 섹터 종목 vol_mult 완화 (-0.10)
  - 섹터 ETF 강한 하락(-1% 이하): 해당 섹터 종목 vol_mult 강화 (+0.10)
"""

# 섹터 ETF → 관련 종목 티커 매핑
_SECTOR_TICKER_MAP: dict[str, list[str]] = {
    "XLK":  ["AAPL", "MSFT", "NVDA", "META", "GOOGL", "AVGO", "AMD"],   # 기술
    "XLF":  ["JPM", "BAC", "GS", "MS", "BRK-B", "WFC"],                  # 금융
    "XLE":  ["XOM", "CVX", "COP", "SLB", "EOG"],                         # 에너지
    "XLY":  ["AMZN", "TSLA", "HD", "MCD", "NKE"],                        # 경기소비재
    "XLV":  ["UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE"],                # 헬스케어
    "XLI":  ["GE", "CAT", "BA", "HON", "UPS"],                           # 산업재
    "XLC":  ["META", "GOOGL", "NFLX", "DIS", "T", "VZ"],                 # 통신서비스
}

# KR 섹터 ETF → 관련 종목 (향후 확장용)
_KR_SECTOR_TICKER_MAP: dict[str, list[str]] = {
    "반도체": ["005930", "000660", "068270"],
    "바이오":  ["207940", "068270", "326030"],
    "2차전지": ["006400", "373220", "247540"],
}


_BEAR_MODES = {"MILD_BEAR", "CAUTIOUS_BEAR", "DEFENSIVE", "HALT"}


def apply_cross_asset_adjust(
    params: dict,
    context: dict,
    market: str,
    ticker: str = "",
    mode: str = "",
) -> dict:
    """
    Cross-asset 지표를 반영해 전략 파라미터를 보정한다.

    Args:
        params:  전략 params() 결과 dict (원본 수정 없이 복사본 반환)
        context: digest_raw["context"] — vix, usd_krw, sectors 등 포함
        market:  "KR" | "US"
        ticker:  개별 종목 티커 (섹터별 완화/강화 적용 시 사용)

    Returns:
        보정된 params dict (새 dict, 원본 불변)
    """
    p = params.copy()
    adj = 0.0

    # KR은 vkospi, US는 vix 사용
    vix     = float(context.get("vix", 0) or 0)
    vkospi  = float(context.get("vkospi", 0) or 0)
    fear_idx = vkospi if market == "KR" else vix   # 공포지수 통합
    usd_krw = float(context.get("usd_krw", 0) or 0)
    sectors = context.get("sectors", {}) or {}

    # ── 공포지수 기반 보정 (VIX / VKOSPI 공통 스케일) ────────────────────────
    # VKOSPI 기준치: 정상 15~20, 주의 20~25, 위기 25+
    # VIX    기준치: 정상 15~20, 주의 20~25, 위기 25+  (거의 동일 스케일)
    if fear_idx > 0:
        if fear_idx < 15:
            adj -= 0.15    # 저공포: 완화
        elif fear_idx < 20:
            adj += 0.00    # 정상
        elif fear_idx < 25:
            adj += 0.15    # 주의
        elif fear_idx < 30:
            adj += 0.30    # 고변동
        else:
            adj += 0.50    # 위기

    # ── USD/KRW 환율 기반 보정 (KR만) ───────────────────────────────────────
    if market == "KR" and usd_krw > 0:
        if usd_krw >= 1500:
            adj += 0.20
        elif usd_krw >= 1450:
            adj += 0.10

    # ── 섹터 ETF 흐름 (US만, 개별 종목 지정 시) ────────────────────────────
    sector_adj = 0.0
    if market == "US" and ticker and sectors:
        for etf, tickers in _SECTOR_TICKER_MAP.items():
            if ticker.upper() in tickers:
                chg = float(sectors.get(etf, 0) or 0)
                if chg >= 1.0:
                    sector_adj -= 0.10   # 섹터 강세: 완화
                elif chg <= -1.0:
                    sector_adj += 0.10   # 섹터 약세: 강화
                break  # 첫 번째 매칭 섹터만 적용

    # ── bear mode 이중 강화 방지 ──────────────────────────────────────────────
    # mode(MILD_BEAR~DEFENSIVE)가 이미 공포를 params에 반영했으므로
    # cross-asset이 추가로 tightening 하는 것은 이중 계산.
    # → bear mode에서 양수 adj(강화)는 0으로 클램프, 완화(음수)는 그대로 허용
    is_bear = mode.upper() in _BEAR_MODES if mode else False
    if is_bear:
        adj       = min(adj, 0.0)        # tightening 차단
        sector_adj = min(sector_adj, 0.0)

    total_adj = round(adj + sector_adj, 2)

    # vol_mult 보정
    # - MILD_BEAR / CAUTIOUS_BEAR: 캡 1.65 적용 (이중 강화 방지, 신호 허용)
    # - DEFENSIVE / HALT: 캡 없음 → base vol_mult(2.0+) 그대로 유지 (진입 억제)
    # - NEUTRAL 이상: 캡 없음 → cross-asset이 자유롭게 보정
    _CAPPED_MODES = {"MILD_BEAR", "CAUTIOUS_BEAR"}
    _VOL_MULT_CAP = 1.65
    if "vol_mult" in p:
        raw = max(1.0, float(p["vol_mult"]) + total_adj)
        if mode.upper() in _CAPPED_MODES:
            raw = min(_VOL_MULT_CAP, raw)
        p["vol_mult"] = round(raw, 2)

    # mean_reversion: rsi_thr / bb_thr 보정
    # 공포지수 높을수록 더 깊은 과매도 필요 → 임계값 낮춤
    if "rsi_thr" in p and fear_idx > 0:
        fi_rsi_adj = 0
        if fear_idx >= 30:
            fi_rsi_adj = -4
        elif fear_idx >= 25:
            fi_rsi_adj = -2
        elif fear_idx < 15:
            fi_rsi_adj = +2   # 저공포: 완화
        p["rsi_thr"] = max(15, int(p["rsi_thr"]) + fi_rsi_adj)

    if "bb_thr" in p and fear_idx > 0:
        fi_bb_adj = 0
        if fear_idx >= 30:
            fi_bb_adj = -4
        elif fear_idx >= 25:
            fi_bb_adj = -2
        elif fear_idx < 15:
            fi_bb_adj = +3
        p["bb_thr"] = max(5, int(p["bb_thr"]) + fi_bb_adj)

    return p


def get_vix_regime(context: dict, market: str) -> str:
    """VIX / VKOSPI 수준 문자열 반환 (로그용)"""
    if market == "KR":
        idx = float(context.get("vkospi", 0) or 0)
        label = "VKOSPI"
    else:
        idx = float(context.get("vix", 0) or 0)
        label = "VIX"
    if idx <= 0:
        return f"{label}=unknown"
    if idx < 15:
        return f"{label}={idx:.1f}(저공포)"
    if idx < 20:
        return f"{label}={idx:.1f}(정상)"
    if idx < 25:
        return f"{label}={idx:.1f}(주의)"
    if idx < 30:
        return f"{label}={idx:.1f}(고변동)"
    return f"{label}={idx:.1f}(위기)"
