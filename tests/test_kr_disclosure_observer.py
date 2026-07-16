from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit.candidate_audit_store import CandidateAuditStore
from runtime.kr_disclosure_observer import (
    AUTHORITY,
    classify_report_name,
    disclosure_observer_tags,
)


class KrDisclosureObserverTests(unittest.TestCase):
    def test_classifies_rights_offering_and_supply_contract(self) -> None:
        self.assertEqual(
            classify_report_name("주요사항보고서(유상증자결정)"),
            ["KR_RIGHTS_OFFERING_OBSERVER"],
        )
        self.assertEqual(
            classify_report_name("단일판매ㆍ공급계약체결"),
            ["KR_SUPPLY_CONTRACT_OBSERVER"],
        )

    def test_tags_use_only_disclosures_known_by_session_date(self) -> None:
        cache = {
            "authority": AUTHORITY,
            "by_code": {
                "005930": [
                    {
                        "date": "2026-07-15",
                        "report_name": "단일판매ㆍ공급계약체결",
                        "tags": ["KR_SUPPLY_CONTRACT_OBSERVER"],
                    },
                    {
                        "date": "2026-07-17",
                        "report_name": "주요사항보고서(유상증자결정)",
                        "tags": ["KR_RIGHTS_OFFERING_OBSERVER"],
                    },
                ]
            },
        }
        tags = disclosure_observer_tags(
            "005930",
            session_date="2026-07-16",
            cache=cache,
        )
        self.assertEqual([row["tag"] for row in tags], ["KR_SUPPLY_CONTRACT_NEXT_SESSION"])

    def test_candidate_registry_records_tag_without_changing_live_risk_tags(self) -> None:
        observed = {
            "tag": "KR_RIGHTS_OFFERING_D0_D5",
            "date": "2026-07-16",
            "authority": AUTHORITY,
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "runtime.kr_disclosure_observer.disclosure_observer_tags",
            return_value=[observed],
        ):
            db = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db)
            store.upsert_candidate(
                {
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": "2026-07-16",
                    "ticker": "005930",
                    "call_id": "call1",
                    "known_at": "2026-07-16T09:05:00+09:00",
                    "price": 70000,
                    "risk_tags": [],
                }
            )
            conn = sqlite3.connect(db)
            registry_tags = conn.execute(
                "SELECT observer_tags_json FROM candidate_registry_first"
            ).fetchone()[0]
            live_risk_tags = conn.execute(
                "SELECT risk_tags_json FROM audit_candidate_rows"
            ).fetchone()[0]
            conn.close()

        self.assertIn("KR_RIGHTS_OFFERING_D0_D5", registry_tags)
        self.assertEqual(live_risk_tags, "[]")


if __name__ == "__main__":
    unittest.main()
