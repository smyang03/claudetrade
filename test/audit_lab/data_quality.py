"""OHLCV data quality checks for collected market data."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .db import utc_now


@dataclass(frozen=True)
class QualityIssue:
    symbol: str
    market: str
    timeframe: str
    issue_type: str
    issue_date: str
    detail: str
    severity: str
    detected_at: str


@dataclass(frozen=True)
class QualityReport:
    symbol: str
    market: str
    timeframe: str
    row_count: int
    start_date: str
    end_date: str
    missing_rate: float
    quality_grade: str
    issues: list[dict]


def _date_str(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _is_intraday_timeframe(timeframe: str) -> bool:
    return str(timeframe or "").lower() not in {"", "daily", "1d", "day"}


def _issue(
    *,
    symbol: str,
    market: str,
    timeframe: str,
    issue_type: str,
    detail: str,
    severity: str = "warn",
    issue_date: str = "",
) -> QualityIssue:
    return QualityIssue(
        symbol=symbol,
        market=market.upper(),
        timeframe=timeframe,
        issue_type=issue_type,
        issue_date=issue_date,
        detail=detail,
        severity=severity,
        detected_at=utc_now(),
    )


def normalize_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    frame = df.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(col[0]).lower().replace(" ", "_") for col in frame.columns]
    else:
        frame.columns = [str(col).lower().replace(" ", "_") for col in frame.columns]
    if "datetime" in frame.columns and "date" not in frame.columns:
        frame = frame.rename(columns={"datetime": "date"})
    if "index" in frame.columns and "date" not in frame.columns:
        frame = frame.rename(columns={"index": "date"})
    if "date" not in frame.columns:
        frame = frame.reset_index()
        frame.columns = [str(col).lower().replace(" ", "_") for col in frame.columns]
        if "datetime" in frame.columns:
            frame = frame.rename(columns={"datetime": "date"})
        elif "index" in frame.columns:
            frame = frame.rename(columns={"index": "date"})
    if "adj_close" in frame.columns and "close" not in frame.columns:
        frame = frame.rename(columns={"adj_close": "close"})
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in frame.columns]
    if missing:
        return pd.DataFrame(columns=required)
    frame = frame[required].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.sort_values("date").reset_index(drop=True)


def validate_ohlcv_frame(
    df: pd.DataFrame,
    *,
    symbol: str,
    market: str,
    timeframe: str = "daily",
) -> QualityReport:
    frame = normalize_ohlcv_frame(df)
    market_u = market.upper()
    issues: list[QualityIssue] = []
    if frame.empty:
        issues.append(
            _issue(
                symbol=symbol,
                market=market_u,
                timeframe=timeframe,
                issue_type="empty_data",
                detail="수집된 OHLCV 데이터가 없음",
                severity="critical",
            )
        )
        return QualityReport(symbol, market_u, timeframe, 0, "", "", 100.0, "FAIL", [asdict(i) for i in issues])

    required = ["open", "high", "low", "close", "volume"]
    row_count = len(frame)
    start_date = _date_str(frame["date"].min())
    end_date = _date_str(frame["date"].max())
    missing_cells = int(frame[["date", *required]].isna().sum().sum())
    missing_rate = round(missing_cells / max(row_count * 6, 1), 6)

    duplicate_mask = frame["date"].duplicated(keep=False)
    duplicate_count = int(duplicate_mask.sum())
    if duplicate_count:
        issues.append(
            _issue(
                symbol=symbol,
                market=market_u,
                timeframe=timeframe,
                issue_type="duplicate_date",
                detail=f"중복 날짜 {duplicate_count}건",
                severity="error",
                issue_date=_date_str(frame.loc[duplicate_mask, "date"].iloc[0]),
            )
        )

    price_scale = frame[["open", "high", "low", "close"]].abs().max(axis=1).fillna(0)
    tolerance = (price_scale * 1e-6).clip(lower=1e-9)
    invalid_mask = (
        (frame["high"] + tolerance < frame[["open", "close", "low"]].max(axis=1))
        | (frame["low"] - tolerance > frame[["open", "close", "high"]].min(axis=1))
        | (frame["low"] - tolerance > frame["high"] + tolerance)
    )
    invalid_count = int(invalid_mask.sum())
    invalid_rate = invalid_count / max(row_count, 1)
    if bool(invalid_mask.any()):
        if invalid_count <= 2:
            invalid_severity = "warn"
        elif invalid_rate <= 0.002:
            invalid_severity = "error"
        else:
            invalid_severity = "critical"
        issues.append(
            _issue(
                symbol=symbol,
                market=market_u,
                timeframe=timeframe,
                issue_type="ohlc_invalid",
                detail=f"OHLC 논리 오류 {invalid_count}건",
                severity=invalid_severity,
                issue_date=_date_str(frame.loc[invalid_mask, "date"].iloc[0]),
            )
        )

    negative_volume = frame["volume"] < 0
    if bool(negative_volume.any()):
        issues.append(
            _issue(
                symbol=symbol,
                market=market_u,
                timeframe=timeframe,
                issue_type="volume_negative",
                detail=f"음수 거래량 {int(negative_volume.sum())}건",
                severity="critical",
                issue_date=_date_str(frame.loc[negative_volume, "date"].iloc[0]),
            )
        )

    zero_volume = frame["volume"] == 0
    if bool(zero_volume.mean() > 0.10):
        issues.append(
            _issue(
                symbol=symbol,
                market=market_u,
                timeframe=timeframe,
                issue_type="zero_volume",
                detail=f"거래량 0 비율 {zero_volume.mean() * 100.0:.2f}%",
                severity="warn",
            )
        )

    close_jump = frame["close"].pct_change().abs()
    jump_mask = close_jump > 0.70
    if bool(jump_mask.any()):
        issues.append(
            _issue(
                symbol=symbol,
                market=market_u,
                timeframe=timeframe,
                issue_type="price_jump",
                detail=f"종가 급변 {int(jump_mask.sum())}건. 분할/수정주가 여부 확인 필요",
                severity="warn",
                issue_date=_date_str(frame.loc[jump_mask, "date"].iloc[0]),
            )
        )

    date_diffs = frame["date"].diff().dt.days
    gap_threshold = 14 if timeframe == "daily" else 3
    gap_mask = date_diffs > gap_threshold
    if bool(gap_mask.any()):
        issues.append(
            _issue(
                symbol=symbol,
                market=market_u,
                timeframe=timeframe,
                issue_type="long_gap",
                detail=f"장기 공백 {int(gap_mask.sum())}건",
                severity="warn",
                issue_date=_date_str(frame.loc[gap_mask, "date"].iloc[0]),
            )
        )

    duration_days = 0
    if start_date and end_date:
        duration_days = int((pd.to_datetime(end_date) - pd.to_datetime(start_date)).days)
    severities = {issue.severity for issue in issues}
    if _is_intraday_timeframe(timeframe):
        # Intraday free sources often expose only 30-60 calendar days. Grade
        # them by row count and structural validity, not multi-year duration.
        if (
            missing_rate < 0.01
            and invalid_count == 0
            and "critical" not in severities
            and "error" not in severities
            and duration_days >= 45
            and row_count >= 1000
        ):
            grade = "A"
        elif (
            missing_rate < 0.05
            and "critical" not in severities
            and invalid_rate <= 0.002
            and duration_days >= 20
            and row_count >= 300
        ):
            grade = "B"
        elif (
            missing_rate < 0.10
            and "critical" not in severities
            and invalid_rate <= 0.005
            and row_count >= 100
        ):
            grade = "C"
        else:
            grade = "FAIL"
    elif (
        missing_rate < 0.01
        and invalid_count == 0
        and "critical" not in severities
        and "error" not in severities
        and duration_days >= 365 * 3
    ):
        grade = "A"
    elif missing_rate < 0.05 and "critical" not in severities and invalid_count <= 2 and duration_days >= 365:
        grade = "B"
    elif missing_rate < 0.10 and "critical" not in severities and invalid_rate <= 0.005 and duration_days >= 180:
        grade = "C"
    else:
        grade = "FAIL"

    return QualityReport(
        symbol=symbol,
        market=market_u,
        timeframe=timeframe,
        row_count=row_count,
        start_date=start_date,
        end_date=end_date,
        missing_rate=missing_rate,
        quality_grade=grade,
        issues=[asdict(issue) for issue in issues],
    )
