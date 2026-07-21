from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.kr_investor_flow_cache import (
    effective_flow_source_date,
    update_candidate_flow_series,
)


def _series_stub(rows_by_date: dict[str, dict]):
    def fetch(ticker: str, start: str, end: str, token: str) -> list:
        out = []
        for d, v in sorted(rows_by_date.items()):
            if start <= d <= end:
                out.append({"date": d, "flow_date": d, "flow_date_matched": True, **v})
        return out

    return fetch


class KrFlowSeriesCaptureTests(unittest.TestCase):
    # 2026-07-21(화·거래일) 기준 완료 거래일: 7/20, 7/17(제헌절 아님? known-holiday면 스킵) 등
    # 캘린더 의존을 피하려 effective_flow_source_date로 기대 날짜를 동적으로 계산한다.
    SESSION = "2026-07-21"

    def _dates(self, days: int) -> list[str]:
        out, seen = [], set()
        for lag in range(1, days + 1):
            d = effective_flow_source_date(self.SESSION, lag_trading_days=lag)
            if d not in seen:
                seen.add(d)
                out.append(d)
        return out

    def test_series_distributes_rows_into_per_date_caches(self) -> None:
        dates = self._dates(3)
        rows = {d: {"foreign": 100 + i, "institution": -50, "individual": 10} for i, d in enumerate(dates)}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path_for = lambda d: root / f"flow_{d.replace('-', '')}.json"  # noqa: E731
            primary = update_candidate_flow_series(
                ["005930"],
                session_date=self.SESSION,
                days=3,
                token="t",
                fetch_series_fn=_series_stub(rows),
                sleep_sec=0,
                path_for=path_for,
            )
            # lag-1 캐시가 primary로 반환되고 시리즈 메타 포함
            self.assertEqual(primary.get("series_days"), 3)
            self.assertEqual(primary.get("series_dates"), dates)
            self.assertEqual(primary.get("series_fetch_errors"), 0)
            # 각 date 파일에 레코드 저장 → window>1 로드가 실데이터로 동작
            # (path 오버라이드 로드는 date별 파일이라 직접 파일 존재·내용 확인)
            for d in dates:
                p = path_for(d)
                self.assertTrue(p.exists(), f"cache file missing for {d}")
                import json

                payload = json.loads(p.read_text(encoding="utf-8"))
                rec = payload["records"]["005930"]
                self.assertEqual(rec["status"], "ok")
                self.assertEqual(rec["foreign"], rows[d]["foreign"])
                self.assertTrue(rec["flow_values_trusted"])
                self.assertTrue(rec["flow_date_matched"])

    def test_series_incremental_skips_cached_dates(self) -> None:
        dates = self._dates(3)
        rows = {d: {"foreign": 1, "institution": 2, "individual": 3} for d in dates}
        calls = {"n": 0}

        def counting_fetch(ticker, start, end, token):
            calls["n"] += 1
            return _series_stub(rows)(ticker, start, end, token)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path_for = lambda d: root / f"flow_{d.replace('-', '')}.json"  # noqa: E731
            kwargs = dict(
                session_date=self.SESSION,
                days=3,
                token="t",
                fetch_series_fn=counting_fetch,
                sleep_sec=0,
                path_for=path_for,
            )
            update_candidate_flow_series(["005930"], **kwargs)
            self.assertEqual(calls["n"], 1)
            # 두 번째 호출: 전부 캐시됨 → fetch 없음
            update_candidate_flow_series(["005930"], **kwargs)
            self.assertEqual(calls["n"], 1)

    def test_series_missing_date_marked_untrusted(self) -> None:
        dates = self._dates(2)
        # 최신 date만 응답에 존재
        rows = {dates[0]: {"foreign": 5, "institution": 0, "individual": 0}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path_for = lambda d: root / f"flow_{d.replace('-', '')}.json"  # noqa: E731
            update_candidate_flow_series(
                ["000660"],
                session_date=self.SESSION,
                days=2,
                token="t",
                fetch_series_fn=_series_stub(rows),
                sleep_sec=0,
                path_for=path_for,
            )
            import json

            missing_payload = json.loads(path_for(dates[1]).read_text(encoding="utf-8"))
            rec = missing_payload["records"]["000660"]
            self.assertEqual(rec["status"], "missing")
            self.assertIs(rec["flow_values_trusted"], False)

    def test_fetch_error_records_error_and_continues(self) -> None:
        def boom(ticker, start, end, token):
            raise RuntimeError("rate limited")

        dates = self._dates(2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path_for = lambda d: root / f"flow_{d.replace('-', '')}.json"  # noqa: E731
            primary = update_candidate_flow_series(
                ["005930"],
                session_date=self.SESSION,
                days=2,
                token="t",
                fetch_series_fn=boom,
                sleep_sec=0,
                path_for=path_for,
            )
            self.assertEqual(primary.get("series_fetch_errors"), 1)


if __name__ == "__main__":
    unittest.main()
