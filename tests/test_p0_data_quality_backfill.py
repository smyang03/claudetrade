from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import p0_data_quality_backfill as backfill


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def _patched_root(root: Path):
    (root / "data" / "supplement" / "us").mkdir(parents=True)
    (root / "data" / "supplement" / "kr").mkdir(parents=True)
    (root / "data" / "daily_digest").mkdir(parents=True)
    with (
        patch.object(backfill, "ROOT", root),
        patch.object(backfill, "SUPPLEMENT_DIR", root / "data" / "supplement"),
        patch.object(backfill, "DAILY_DIGEST_DIR", root / "data" / "daily_digest"),
        patch.object(backfill, "BACKUP_ROOT", root / "data" / "backups"),
        patch.object(backfill, "LOG_ROOT", root / "logs" / "p0_backfill"),
    ):
        yield


class P0DataQualityBackfillTests(unittest.TestCase):
    def test_dry_run_reports_needed_changes_without_mutation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _patched_root(root):
                path = root / "data" / "supplement" / "us" / "2026-05-01.json"
                _write_json(path, {"date": "2026-05-01", "vix": 0.0, "dxy": 0, "oil_wti": 0})

                code = backfill.main(["--from", "2026-05-01", "--to", "2026-05-01", "--market", "US"])

                self.assertEqual(code, 0)
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["vix"], 0.0)
                summaries = list((root / "logs" / "p0_backfill").glob("*.json"))
                self.assertEqual(len(summaries), 1)
                summary = json.loads(summaries[0].read_text(encoding="utf-8"))
                self.assertEqual(summary["counts"]["needs_change"], 1)
                self.assertEqual(summary["reports"][0]["status"], "needs_change")

    def test_write_normalizes_us_supplement_and_creates_backup(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _patched_root(root):
                path = root / "data" / "supplement" / "us" / "2026-05-01.json"
                _write_json(path, {"date": "2026-05-01", "vix": 0.0, "dxy": 0, "oil_wti": 0})

                code = backfill.main(["--write", "--from", "2026-05-01", "--to", "2026-05-01", "--market", "US"])

                self.assertEqual(code, 0)
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsNone(payload["vix"])
                self.assertIsNone(payload["dxy"])
                self.assertIsNone(payload["oil_wti"])
                self.assertIn("vix_missing", payload["data_quality_flags"])
                self.assertIn("collected_at", payload)
                self.assertEqual(payload["sources"]["dxy"], "backfill_offline")

                manifests = list((root / "data" / "backups").glob("p0_backfill_*/manifest.json"))
                self.assertEqual(len(manifests), 1)
                manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
                self.assertEqual(manifest["entries"][0]["original_path"], "data/supplement/us/2026-05-01.json")

                code = backfill.main(["--from", "2026-05-01", "--to", "2026-05-01", "--market", "US"])
                self.assertEqual(code, 0)
                summaries = sorted((root / "logs" / "p0_backfill").glob("*_dry_run.json"))
                summary = json.loads(summaries[-1].read_text(encoding="utf-8"))
                self.assertEqual(summary["counts"]["needs_change"], 0)

    def test_write_adds_digest_breadth_summary_without_rebuilding_digest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _patched_root(root):
                path = root / "data" / "daily_digest" / "2026-05-01_US.json"
                _write_json(
                    path,
                    {
                        "date": "2026-05-01",
                        "market": "US",
                        "context": {"vix": 0, "dxy": 0, "sectors": {"XLK": 1.0}},
                        "technicals": {
                            "AAPL": {
                                "name": "Apple",
                                "change_pct": 1.25,
                                "rsi": 55,
                                "macd": "golden",
                                "vol_ratio": 1.7,
                                "pos_52w": 96,
                            }
                        },
                        "top_news": [{"title": "preserve"}],
                    },
                )

                code = backfill.main(["--write", "--from", "2026-05-01", "--to", "2026-05-01", "--market", "US"])

                self.assertEqual(code, 0)
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["top_news"], [{"title": "preserve"}])
                self.assertEqual(payload["breadth_summary"]["universe_count"], 1)
                self.assertIn("vix_missing", payload["breadth_summary"]["data_quality_flags"])

    def test_write_normalizes_kr_risk_fields_without_touching_flows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _patched_root(root):
                path = root / "data" / "supplement" / "kr" / "2026-05-01.json"
                flows = {"005930": {"foreign": 0, "institution": -10, "individual": 0}}
                _write_json(path, {"date": "2026-05-01", "flows": flows, "usd_krw": 50, "vkospi": 0})

                code = backfill.main(["--write", "--from", "2026-05-01", "--to", "2026-05-01", "--market", "KR"])

                self.assertEqual(code, 0)
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["flows"], flows)
                self.assertIsNone(payload["usd_krw"])
                self.assertIsNone(payload["vkospi"])
                self.assertIn("usd_krw_missing", payload["data_quality_flags"])

    def test_verify_fails_on_invalid_kr_usd_krw(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _patched_root(root), patch.object(backfill, "_git_status_for_outputs", return_value=""):
                path = root / "data" / "supplement" / "kr" / "2026-05-01.json"
                _write_json(path, {"date": "2026-05-01", "usd_krw": 10, "vkospi": None})

                code = backfill.main(["--verify", "--from", "2026-05-01", "--to", "2026-05-01", "--market", "KR"])

                self.assertEqual(code, 1)
                summaries = list((root / "logs" / "p0_backfill").glob("*.json"))
                summary = json.loads(summaries[0].read_text(encoding="utf-8"))
                self.assertEqual(summary["counts"]["verify_error"], 1)
                self.assertIn("usd_krw_invalid", summary["reports"][0]["issues"])


if __name__ == "__main__":
    unittest.main()
