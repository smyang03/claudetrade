from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase1_trainer.external_data_collectors import collect_ready_sources_dry_run, _truncate_error
from phase1_trainer.external_data_store import ExternalDataStore


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status={self.status_code}")


class FakeSession:
    def get(self, url: str, params: dict | None = None, timeout: int = 20) -> FakeResponse:
        params = params or {}
        if "opendart" in url:
            return FakeResponse(
                {
                    "status": "000",
                    "message": "OK",
                    "list": [
                        {
                            "corp_name": "Samsung Electronics",
                            "rcept_no": "202605100001",
                            "rcept_dt": "20260510",
                            "report_nm": "\uc720\uc0c1\uc99d\uc790 \uacb0\uc815",
                        }
                    ],
                }
            )
        if "GetKrxListedInfoService" in url:
            return FakeResponse(
                {
                    "response": {
                        "header": {"resultCode": "00", "resultMsg": "NORMAL"},
                        "body": {
                            "items": {
                                "item": {
                                    "basDt": "20260508",
                                    "srtnCd": "005930",
                                    "isinCd": "KR7005930003",
                                    "mrktCtg": "KOSPI",
                                    "itmsNm": "Samsung Electronics",
                                    "corpNm": "Samsung Electronics",
                                    "crno": "1301110006246",
                                }
                            }
                        },
                    }
                }
            )
        if "GetStockSecuritiesInfoService" in url:
            return FakeResponse(
                {
                    "response": {
                        "header": {"resultCode": "00", "resultMsg": "NORMAL"},
                        "body": {
                            "items": {
                                "item": [
                                    {
                                        "basDt": "20260508",
                                        "srtnCd": "005930",
                                        "isinCd": "KR7005930003",
                                        "mrktCtg": "KOSPI",
                                        "itmsNm": "Samsung Electronics",
                                        "mkp": "70000",
                                        "hipr": "71000",
                                        "lopr": "69000",
                                        "clpr": "70500",
                                        "fltRt": "1.23",
                                        "trqu": "1234567",
                                        "trPrc": "90000000000",
                                    }
                                ]
                            }
                        },
                    }
                }
            )
        if "GetSecuritiesProductInfoService" in url:
            return FakeResponse(
                {
                    "response": {
                        "header": {"resultCode": "00", "resultMsg": "NORMAL"},
                        "body": {
                            "items": {
                                "item": [
                                    {
                                        "basDt": "20260508",
                                        "srtnCd": "091160",
                                        "isinCd": "KR7091160002",
                                        "mrktCtg": "ETF",
                                        "itmsNm": "TIGER Semiconductor",
                                        "mkp": "10000",
                                        "hipr": "10100",
                                        "lopr": "9900",
                                        "clpr": "10050",
                                        "fltRt": "0.50",
                                        "trqu": "1000",
                                        "trPrc": "10050000",
                                    }
                                ]
                            }
                        },
                    }
                }
            )
        if "fred" in url:
            return FakeResponse(
                {
                    "observations": [
                        {
                            "realtime_start": "2026-05-10",
                            "realtime_end": "2026-05-10",
                            "date": "2026-04-01",
                            "value": "3.4",
                        }
                    ]
                }
            )
        raise AssertionError(f"unexpected url: {url}")


class ErrorDataGoKrSession(FakeSession):
    def get(self, url: str, params: dict | None = None, timeout: int = 20) -> FakeResponse:
        if "apis.data.go.kr" in url:
            return FakeResponse(
                {
                    "response": {
                        "header": {"resultCode": "30", "resultMsg": "SERVICE KEY IS NOT REGISTERED ERROR"}
                    }
                }
            )
        return super().get(url, params=params, timeout=timeout)


class ExternalDataCollectorsTest(unittest.TestCase):
    def test_external_data_readiness_empty_db_is_not_production_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ExternalDataStore(Path(tmp) / "external.sqlite")
            summary = store.readiness_summary(initialize=True)

            self.assertEqual(summary["status"], "empty")
            self.assertFalse(summary["production_ready"])
            self.assertEqual(summary["total_data_rows"], 0)
            self.assertEqual(summary["table_counts"]["public_stock_quotes"], 0)

    def test_external_data_readiness_populated_db_reports_latest_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ExternalDataStore(Path(tmp) / "external.sqlite")
            store.init_schema()
            store.upsert_public_stock_quotes(
                [
                    {
                        "base_date": "20260508",
                        "stock_code": "005930",
                        "market": "KOSPI",
                        "item_name": "Samsung Electronics",
                        "close": 70500,
                        "fetched_at": "2026-05-10T13:00:00+09:00",
                    }
                ]
            )
            store.record_run(
                source="data_go_kr",
                endpoint="stock_quotes",
                status="ok",
                row_count=1,
                fetched_at="2026-05-10T13:00:05+09:00",
            )

            summary = store.readiness_summary()

            self.assertEqual(summary["status"], "ready")
            self.assertTrue(summary["production_ready"])
            self.assertEqual(summary["total_data_rows"], 1)
            self.assertEqual(summary["latest_fetched_at_by_table"]["public_stock_quotes"], "2026-05-10T13:00:00+09:00")
            self.assertEqual(summary["latest_api_run_at"], "2026-05-10T13:00:05+09:00")

    def test_dry_run_normalizes_and_stores_ready_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env.live"
            env_path.write_text(
                "\n".join(
                    [
                        "DART_API_KEY=fake_dart",
                        "DATA_GO_KR_KEY=fake_data",
                        "FRED_API_KEY=fake_fred",
                    ]
                ),
                encoding="utf-8",
            )
            db_path = root / "external.sqlite"

            with patch.dict("os.environ", {}, clear=True):
                summary = collect_ready_sources_dry_run(
                    db_path=db_path,
                    env_path=env_path,
                    target_date="2026-05-10",
                    session=FakeSession(),
                )

            self.assertTrue(db_path.exists())
            self.assertFalse([c for c in summary["checks"] if c["status"] == "failed"])
            self.assertTrue(all(c["columns_ok"] for c in summary["checks"]))
            self.assertEqual(summary["table_counts"]["dart_disclosures"], 1)
            self.assertEqual(summary["table_counts"]["public_krx_listed"], 1)
            self.assertEqual(summary["table_counts"]["public_stock_quotes"], 1)
            self.assertEqual(summary["table_counts"]["public_securities_products"], 1)
            self.assertEqual(summary["table_counts"]["fred_observations"], 3)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                dart = conn.execute("SELECT risk_level, risk_tags_json FROM dart_disclosures").fetchone()
                self.assertEqual(dart["risk_level"], "high")
                self.assertIn("capital_increase", dart["risk_tags_json"])

                quote = conn.execute("SELECT close, volume FROM public_stock_quotes").fetchone()
                self.assertEqual(quote["close"], 70500.0)
                self.assertEqual(quote["volume"], 1234567.0)

                fred = conn.execute(
                    "SELECT series_id, observation_date, value FROM fred_observations WHERE series_id = 'CPIAUCSL'"
                ).fetchone()
                self.assertEqual(fred["observation_date"], "2026-04-01")
                self.assertEqual(fred["value"], 3.4)
            finally:
                conn.close()

    def test_no_write_does_not_create_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env.live"
            env_path.write_text(
                "\n".join(
                    [
                        "DART_API_KEY=fake_dart",
                        "DATA_GO_KR_KEY=fake_data",
                        "FRED_API_KEY=fake_fred",
                    ]
                ),
                encoding="utf-8",
            )
            db_path = root / "external.sqlite"

            with patch.dict("os.environ", {}, clear=True):
                summary = collect_ready_sources_dry_run(
                    db_path=db_path,
                    env_path=env_path,
                    target_date="2026-05-10",
                    session=FakeSession(),
                    write_db=False,
                )

            self.assertFalse(db_path.exists())
            self.assertFalse(summary["write_db"])
            self.assertEqual(summary["table_counts"], {})
            self.assertFalse([c for c in summary["checks"] if c["status"] == "failed"])

    def test_explicit_env_file_overrides_existing_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env.live"
            env_path.write_text(
                "\n".join(
                    [
                        "DART_API_KEY=file_dart",
                        "DATA_GO_KR_KEY=file_data",
                        "FRED_API_KEY=file_fred",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {
                    "DART_API_KEY": "stale_dart",
                    "DATA_GO_KR_KEY": "stale_data",
                    "FRED_API_KEY": "stale_fred",
                },
                clear=True,
            ):
                collect_ready_sources_dry_run(
                    db_path=root / "external.sqlite",
                    env_path=env_path,
                    target_date="2026-05-10",
                    session=FakeSession(),
                    write_db=False,
                )
                import os

                self.assertEqual(os.environ["DART_API_KEY"], "file_dart")
                self.assertEqual(os.environ["DATA_GO_KR_KEY"], "file_data")
                self.assertEqual(os.environ["FRED_API_KEY"], "file_fred")

    def test_data_go_kr_non_normal_result_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env.live"
            env_path.write_text(
                "\n".join(
                    [
                        "DART_API_KEY=fake_dart",
                        "DATA_GO_KR_KEY=fake_data",
                        "FRED_API_KEY=fake_fred",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {}, clear=True):
                summary = collect_ready_sources_dry_run(
                    db_path=root / "external.sqlite",
                    env_path=env_path,
                    target_date="2026-05-10",
                    session=ErrorDataGoKrSession(),
                    write_db=False,
                )

            failed_public = [
                check
                for check in summary["checks"]
                if check["source"] == "DATA_GO_KR" and check["status"] == "failed"
            ]
            self.assertEqual(len(failed_public), 3)
            self.assertTrue(all("SERVICE KEY" in check["error"] for check in failed_public))

    def test_error_redaction_masks_full_query_values(self) -> None:
        error = (
            "url: /x?serviceKey=abc%2Bdef%3D%3D&pageNo=1 "
            "/y?crtfc_key=dartsecret&corp_code=001 "
            "/z?api_key=fredsecret&file_type=json"
        )

        redacted = _truncate_error(error)

        self.assertIn("serviceKey=***&pageNo=1", redacted)
        self.assertIn("crtfc_key=***&corp_code=001", redacted)
        self.assertIn("api_key=***&file_type=json", redacted)
        self.assertNotIn("abc", redacted)
        self.assertNotIn("dartsecret", redacted)
        self.assertNotIn("fredsecret", redacted)


if __name__ == "__main__":
    unittest.main()
