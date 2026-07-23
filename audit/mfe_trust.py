from __future__ import annotations

"""MFE 신뢰도 계약 — capture·우리-보유기간 분석이 backfill MFE에 오염되지 않게 한다.

왜 필요한가 (2026-07-23, 하루에 세 번 같은 함정):
  - ret_5m 대박예측 AUC 0.703  → canonical.mfe_pct 갭이 만든 편향 부분표본. 정정 후 0.557.
  - KR 가드 gross forward +8%/1일 → 우리 보유기간(0.5h) 아님. 우리net −0.55%.
  - KR capture −8%             → MFE가 일봉 backfill. 보유 3분인데 MFE +19.9%.

  공통 오류: **backfill MFE(일봉 고/저)를 우리 보유 중 고점처럼 다뤘다.** 그러면
  "우리가 봉우리를 반납했다(capture 낮다)"는 잘못된 결론이 나온다 — 실제로는 우리가
  나간 뒤 그날 종목이 뛴 것이다.

계약:
  MFE가 **우리 보유 중 고점**인지(=capture 계산 유효)는 `mfe_time` 유무로 판별한다.
    mfe_time 있음  → 라이브 observed. 우리 보유 중 실제 신고점 시각이 있다. capture 유효.
    mfe_time 없음  → backfill(일봉/외부). 우리 보유 밖 고점일 수 있다. capture 금지.
  capture(= net / MFE)를 계산하는 모든 분석은 이 게이트를 먼저 통과시킨다.
"""

from typing import Any


def is_holding_period_mfe(row: dict[str, Any]) -> bool:
    """이 행의 mfe_pct가 우리 보유기간 고점인가(=capture·우리net 분석에 쓸 수 있나).

    row는 mfe_time 또는 mfe_source 중 하나라도 있으면 그걸로 판별한다.
      - mfe_source가 있고 'backfill'을 포함하면 False.
      - mfe_time이 비어 있으면 backfill로 간주(현 원장은 observed에만 시각을 남긴다).
    """
    src = str(row.get("mfe_source") or "").strip().lower()
    if src:
        return "backfill" not in src and "daily" not in src and "yf" not in src
    mfe_time = row.get("mfe_time")
    return bool(mfe_time and str(mfe_time).strip())


def filter_capture_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """capture 분석에 쓸 수 있는 행만 남기고, 버린 backfill 건수를 함께 반환한다.

    반환: (신뢰 가능한 행, 제외된 backfill 행 수). 제외 수를 반드시 로깅·표기한다 —
    조용히 버리면 "capture 표본이 이만큼이다"가 다시 과대표기된다.
    """
    keep = [r for r in rows if is_holding_period_mfe(r)]
    return keep, len(rows) - len(keep)


def capture_pct(net: float | None, mfe_pct: float | None, row: dict[str, Any]) -> float | None:
    """net/MFE capture(%). backfill MFE면 None(계산 금지). MFE<=0이면 None."""
    if net is None or mfe_pct is None or mfe_pct <= 0:
        return None
    if not is_holding_period_mfe(row):
        return None
    return net / mfe_pct * 100.0
